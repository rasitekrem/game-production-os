"""The single audited place where GPOS starts an operating-system process.

This is a boundary, not a shell. It executes a `ToolProcessSpec` that an adapter has already
authorized: a resolved executable plus an argument vector, an explicit working directory inside a
permitted scope, an explicit environment policy and a deadline. It cannot be used to run a command
line, and no agent-facing surface takes a command string.

Guarantees:

* no shell: the subprocess shell mode is never enabled, and `sh`/`bash`/`cmd`/`powershell` style
  interpreters are refused as the executable, so an argument can never be re-parsed as a command;
* the executable is an existing regular file the adapter resolved (usually through its probe), not
  prose from a request;
* arguments are passed as a vector, never interpolated or concatenated;
* the working directory is explicit and checked against the permitted filesystem scopes;
* the environment is built from a declared policy, never simply inherited wholesale into the result;
* stdin is closed by default: nothing interactive;
* stdout and stderr are captured with a hard byte bound, so a process printing gigabytes cannot
  exhaust memory; the full stream length is still counted and truncation is reported;
* captured output is exposed twice, from the same bounded capture: `stdout`/`stderr` are redacted
  text, safe for any caller, and `raw_stdout`/`raw_stderr` are the exact captured bytes, for the
  adapter that must parse a machine protocol (redaction can rewrite such output — for example, a
  credential-shaped value can swallow the NUL that separates two records). The raw bytes are private:
  no ToolResult, provenance, diagnostic, CLI output or evidence ever carries them;
* the process runs in its own session, so a timeout terminates the whole process tree (SIGTERM,
  then SIGKILL after a short grace) instead of leaving orphans behind;
* duration is measured with a monotonic clock, never by subtracting wall-clock timestamps.

A detached process (alpha.22) is the one exception to "the boundary waits for the process": a
`DetachedProcessSpec` is validated by exactly the same rules (absolute existing executable, no shell or
interpreter program string, an argument vector, an explicit working directory inside the scopes, an explicit
environment policy), started in its own session with stdin, stdout and stderr on /dev/null, and not waited
for. A daemon thread reaps it, so a long-lived host never keeps a zombie. Only an execution whose capability
is allowlisted for it can reach `spawn_detached` (see `ExecutionContext.spawn_detached`); no request field
names an executable, an argument, a directory or an environment for it.

Windows batch execution (alpha.23) uses the same boundary with `process_win32.py`, imported only here and by the
private fixed Player boundary: a `.exe` only (never a batch file, script or shell), created suspended in a Job through
STARTUPINFOEX + PROC_THREAD_ATTRIBUTE_JOB_LIST, proven (job membership, image identity) before it runs, its whole
tree terminated on a timeout and whatever it leaves behind terminated when it exits. A Windows outcome reports
whether that containment and the output capture were actually observed (`tree_contained`, `capture_complete`);
when either was not, its exit code is not reported. Detached processes are unavailable on Windows.

Every outcome this boundary returns, on every host, passes through one observer (`observe`), so the foundation
judges the integrity of every process an execution or a probe started, including one an adapter ran without
`ExecutionContext.run` or left out of its result.
"""

import contextlib
import contextvars
import dataclasses
import os
import signal
import subprocess  # the one permitted use in gpos/: see tests/validate_framework.py PROCESS_BOUNDARY
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths as tp
from .redaction import redact, redact_all

DEFAULT_CAPTURE_BYTES = 256 * 1024
TERMINATE_GRACE_SECONDS = 2.0
# Interpreters that would turn an argument back into a command line. An adapter that genuinely needs
# one (a future engine's own batch-mode runner) declares a concrete script path as an argument, never
# a `-c` style program string.
SHELL_EXECUTABLES = {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "cmd", "cmd.exe",
                     "command.com", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}
SHELL_PROGRAM_FLAGS = {"-c", "/c", "/k", "-command", "-Command", "-EncodedCommand"}

# Environment names that may be copied from the parent process when a policy asks for them. Anything
# credential-shaped is excluded by construction: the allowlist is positive, not a denylist.
SAFE_ENV_DEFAULTS = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE",
                     "SYSTEMROOT", "COMSPEC", "PATHEXT", "TZ")

# alpha.23 (D5): on Windows the *default* policy also inherits these, and nothing else. USERPROFILE, HOMEDRIVE and
# HOMEPATH are the Windows counterparts of HOME (Git for Windows, for one, finds the user's configuration through
# them); SystemDrive and WINDIR are where Windows programs and the C runtime find the system. No credential, proxy
# or application variable is ever added. A policy that names its own `inherit` keeps exactly what it names.
WINDOWS_ENV_DEFAULTS = ("SystemDrive", "WINDIR", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
# Windows interpreters and script hosts that would re-parse an argument as a command, refused by name like the
# shells above. A batch file is refused by its suffix: only a `.exe` image is ever started on Windows.
WINDOWS_SHELL_EXECUTABLES = {"bash.exe", "sh.exe", "zsh.exe", "dash.exe", "ksh.exe", "csh.exe", "tcsh.exe",
                             "fish.exe", "wsl.exe", "wslhost.exe", "wscript.exe", "cscript.exe", "mshta.exe",
                             "powershell_ise.exe", "conhost.exe", "openconsole.exe"}
WINDOWS_EXECUTABLE_SUFFIX = ".exe"


class ProcessSpecError(Exception):
    """The spec is not something this boundary is allowed to execute."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EnvironmentPolicy:
    """How the child process environment is built.

    `inherit` names variables copied from the parent (restricted to `SAFE_ENV_DEFAULTS` unless the
    adapter explicitly widens it); `overrides` are literal values the adapter sets. Neither is ever
    copied into a ToolResult: only `recorded` names are reported, and only as names plus a note that
    the value was set, never the value itself.
    """
    inherit: tuple = SAFE_ENV_DEFAULTS
    overrides: tuple = ()          # ((name, value), ...)
    recorded: tuple = ()           # names whose *presence* is worth recording in provenance

    def build(self, parent=None):
        if sys.platform == "win32":
            return _windows_environment(self, os.environ if parent is None else parent)
        parent = os.environ if parent is None else parent
        env = {name: parent[name] for name in self.inherit if name in parent}
        env.update({name: str(value) for name, value in self.overrides})
        return env

    def metadata(self):
        """Safe environment metadata for provenance: names only, never values."""
        if sys.platform == "win32":
            return {"inherited_names": sorted(_windows_inherit(self.inherit)),
                    "set_names": sorted({name for name, _ in self.overrides} | set(self.recorded))}
        names = {name for name, _ in self.overrides} | set(self.recorded)
        return {"inherited_names": sorted(self.inherit), "set_names": sorted(names)}


def _windows_inherit(inherit):
    """The names a Windows child inherits: the default allowlist gains WINDOWS_ENV_DEFAULTS (D5), any other stays."""
    return tuple(inherit) + WINDOWS_ENV_DEFAULTS if tuple(inherit) == SAFE_ENV_DEFAULTS else tuple(inherit)


def _windows_environment(policy, parent):
    """The child environment on Windows, where names are case-insensitive: each allowlisted name is looked up
    case-insensitively and set once, and an override replaces an inherited value of any letter case."""
    by_upper = {}
    for name, value in parent.items():
        by_upper.setdefault(name.upper(), value)
    env = {}
    for name in _windows_inherit(policy.inherit):
        if name.upper() in by_upper and name.upper() not in {k.upper() for k in env}:
            env[name] = by_upper[name.upper()]
    for name, value in policy.overrides:
        for existing in [k for k in env if k.upper() == name.upper()]:
            del env[existing]
        env[name] = str(value)
    return env


@dataclass(frozen=True)
class ToolProcessSpec:
    """An already-authorized process invocation. The adapter owns what may appear here."""
    executable: str
    argv: tuple = ()
    cwd: str = None
    timeout: float = 60.0
    env: EnvironmentPolicy = field(default_factory=EnvironmentPolicy)
    capture_bytes: int = DEFAULT_CAPTURE_BYTES

    def command_for_provenance(self):
        """The executable and arguments as recorded — redacted, never re-joined into a shell string."""
        argv, _ = redact_all([str(a) for a in self.argv])
        return {"executable": redact(str(self.executable))[0], "argv": argv}


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int = None
    stdout: str = ""               # public: redacted text
    stderr: str = ""
    stdout_bytes: int = 0          # full length of the stream, even when truncated
    stderr_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    terminated: bool = False       # the process tree was signalled
    duration_seconds: float = 0.0
    redactions: int = 0
    # Private: the exact captured bytes, before redaction, from the same bounded capture as stdout and
    # stderr (never more than capture_bytes each). For an adapter's own parsing only. Excluded from
    # repr so they cannot surface through a log line or an exception message by accident.
    raw_stdout: bytes = field(default=b"", repr=False)
    raw_stderr: bytes = field(default=b"", repr=False)
    # alpha.23, observed on Windows only (a POSIX outcome keeps these defaults): whether the whole process tree was
    # observed terminated (the Job Object reported no active process) and whether both output streams were read to
    # their end. When either is False the exit code is untrustworthy and is None.
    tree_contained: bool = True
    capture_complete: bool = True
    descendants_terminated: int = 0   # processes still in the job after the root ended, which GPOS terminated (D9)

    @property
    def truncated(self):
        return self.stdout_truncated or self.stderr_truncated

    @property
    def integrity_ok(self):
        """Whether containment and capture were proven. An outcome without it can never count as a success."""
        return self.tree_contained and self.capture_complete


def validate_spec(spec, scopes):
    """Raise ProcessSpecError unless this spec is safe to execute inside `scopes`."""
    if not isinstance(spec, ToolProcessSpec):
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", "a ToolProcessSpec is required")
    exe = spec.executable
    if not isinstance(exe, str) or not exe or "\x00" in exe:
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", "the executable must be a non-empty path")
    if not Path(exe).is_absolute():
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", f"{exe}: the executable must be an absolute path the adapter "
                                                      f"resolved (usually through its probe)")
    if Path(exe).name.lower() in SHELL_EXECUTABLES:
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", f"{exe}: a shell or command interpreter is never executed by the "
                                                      f"tool foundation; an adapter declares a concrete program")
    if not Path(exe).is_file():
        raise ProcessSpecError("TOOL_NOT_FOUND", f"{exe}: no such executable")
    if not os.access(exe, os.X_OK):
        raise ProcessSpecError("TOOL_NOT_FOUND", f"{exe}: not executable")
    for arg in spec.argv:
        if not isinstance(arg, str):
            raise ProcessSpecError("UNSAFE_PROCESS_SPEC", f"argument {arg!r} is not a string; arguments are passed as a "
                                                          f"vector, never composed")
        if "\x00" in arg:
            raise ProcessSpecError("UNSAFE_PROCESS_SPEC", "an argument contains NUL")
        if arg in SHELL_PROGRAM_FLAGS:
            raise ProcessSpecError("UNSAFE_PROCESS_SPEC", f"argument {arg!r} would pass a program string to an "
                                                          f"interpreter; that is not an authorized capability")
    if spec.cwd is None:
        raise ProcessSpecError("UNSAFE_EXECUTION_PATH", "an explicit working directory is required")
    reason = tp.unsafe_reason(scopes, spec.cwd, must_exist=True)
    if reason:
        raise ProcessSpecError("UNSAFE_EXECUTION_PATH", reason)
    if not Path(spec.cwd).is_dir():
        raise ProcessSpecError("UNSAFE_EXECUTION_PATH", f"{spec.cwd}: the working directory is not a directory")
    if not isinstance(spec.timeout, (int, float)) or spec.timeout <= 0:
        raise ProcessSpecError("INVALID_TOOL_REQUEST", "a positive timeout is required")
    if not isinstance(spec.capture_bytes, int) or spec.capture_bytes <= 0:
        raise ProcessSpecError("INVALID_TOOL_REQUEST", "capture_bytes must be a positive integer")
    if sys.platform == "win32":
        _validate_windows(spec)


def _validate_windows(spec):
    """Windows-only refusals on top of the shared rules: only a `.exe` image is started (a batch file runs through
    cmd.exe, which re-parses its arguments), never a script host or a reparse-point alias, and the environment has
    no name Windows would read differently."""
    exe = Path(spec.executable)
    if exe.name.lower() in WINDOWS_SHELL_EXECUTABLES:
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", f"{exe}: a shell or script host is never executed by the tool "
                                                      f"foundation; an adapter declares a concrete program")
    if exe.suffix.lower() != WINDOWS_EXECUTABLE_SUFFIX:
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", f"{exe}: only a .exe image is started on Windows; a batch file, "
                                                      f"script or extensionless file would run through an "
                                                      f"interpreter")
    if os.lstat(exe).st_file_attributes & 0x400:   # FILE_ATTRIBUTE_REPARSE_POINT: a link or an app-execution alias
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", f"{exe}: is a reparse point (a link or app-execution alias); "
                                                      f"an adapter resolves the real executable")
    names = [n for n, _ in spec.env.overrides] + list(spec.env.inherit)
    for name in names:
        if not isinstance(name, str) or not name or "=" in name or "\x00" in name:
            raise ProcessSpecError("UNSAFE_PROCESS_SPEC", f"environment name {name!r} is not a usable Windows name")
    overrides = [n.upper() for n, _ in spec.env.overrides]
    if len(set(overrides)) != len(overrides):
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", "two environment overrides differ only in letter case; Windows "
                                                      "would keep only one of them")
    for _, value in spec.env.overrides:
        if "\x00" in str(value):
            raise ProcessSpecError("UNSAFE_PROCESS_SPEC", "an environment value contains NUL")


def _drain(stream, limit, sink):
    """Read a stream to the end, keeping at most `limit` bytes but counting all of them."""
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            sink["total"] += len(chunk)
            room = limit - len(sink["data"])
            if room > 0:
                sink["data"] += chunk[:room]
    except (OSError, ValueError):  # the pipe closed under us (killed process)
        return
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _terminate(proc):
    """Terminate the process tree: SIGTERM to the session, SIGKILL after a short grace."""
    for sig, wait in ((signal.SIGTERM, TERMINATE_GRACE_SECONDS), (signal.SIGKILL, 5.0)):
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), sig)
            else:  # pragma: no cover - POSIX is the supported foundation platform
                proc.kill()
        except (OSError, ProcessLookupError):
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def run_process(spec, scopes, clock=time.monotonic):
    """Execute an authorized spec. Returns a ProcessOutcome; raises ProcessSpecError for a bad spec.

    Never raises for a failing, hanging or noisy process: those are outcomes, not exceptions. Every outcome is
    passed to the active observer (`observe`), and one whose containment or capture was not proven carries no exit
    code, so no caller can read it as a success.
    """
    validate_spec(spec, scopes)
    backend = _run_windows if sys.platform == "win32" else _run_posix
    return _observed(backend(spec, scopes, clock))


# ---------------------------------------------------------------- observation (alpha.23)

_OBSERVER = contextvars.ContextVar("gpos_tool_process_outcomes", default=None)


@contextlib.contextmanager
def observe():
    """Collect every ProcessOutcome run_process returns in this context (an execution or a probe)."""
    seen = []
    token = _OBSERVER.set(seen)
    try:
        yield seen
    finally:
        _OBSERVER.reset(token)


def _observed(outcome):
    """The fail-closed integrity rule, applied to every outcome on every host before anyone sees it."""
    if not outcome.integrity_ok and outcome.exit_code is not None:
        outcome = dataclasses.replace(outcome, exit_code=None)
    seen = _OBSERVER.get()
    if seen is not None:
        seen.append(outcome)
    return outcome


def _run_windows(spec, scopes, clock):
    """The Windows backend: process_win32.run, its raw result redacted and bounded exactly like the POSIX one."""
    from . import process_win32
    command_line = subprocess.list2cmdline([spec.executable] + list(spec.argv))
    try:
        raw = process_win32.run(spec, command_line, spec.env.build(), _drain, clock)
    except process_win32.LaunchRefused as exc:
        raise ProcessSpecError(exc.code, str(exc)) from None
    out, out_n = redact(raw.raw_stdout.decode("utf-8", errors="replace"))
    err, err_n = redact(raw.raw_stderr.decode("utf-8", errors="replace"))
    return ProcessOutcome(
        exit_code=None if raw.timed_out else raw.exit_code, stdout=out, stderr=err,
        stdout_bytes=raw.stdout_total, stderr_bytes=raw.stderr_total,
        stdout_truncated=raw.stdout_total > len(raw.raw_stdout), stderr_truncated=raw.stderr_total > len(raw.raw_stderr),
        timed_out=raw.timed_out, terminated=raw.terminated, duration_seconds=round(raw.duration, 6),
        redactions=out_n + err_n, raw_stdout=raw.raw_stdout, raw_stderr=raw.raw_stderr,
        tree_contained=raw.tree_contained, capture_complete=raw.capture_complete,
        descendants_terminated=raw.descendants_terminated)


def host_pid_alive(pid):
    """Windows process liveness from a handle (process_win32.pid_alive); never a signal or a console event."""
    from . import process_win32
    return process_win32.pid_alive(pid)


def _run_posix(spec, scopes, clock):
    """The frozen alpha.22 POSIX body of run_process, unchanged (tests/test_posix_parity.py pins it)."""
    started = clock()
    popen_kwargs = {"cwd": spec.cwd, "env": spec.env.build(), "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "close_fds": True}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True  # own process group: a timeout kills the whole tree
    # An argument vector and no shell mode: the child is exec'd directly, so nothing is ever re-parsed.
    try:
        proc = subprocess.Popen([spec.executable] + list(spec.argv), **popen_kwargs)
    except (FileNotFoundError, PermissionError, NotADirectoryError, IsADirectoryError) as exc:
        # The executable passed validate_spec and then disappeared, lost its permission bit or was
        # replaced. That is a tool availability problem, not a foundation defect, so it is reported as
        # one rather than surfacing as an opaque adapter error.
        raise ProcessSpecError("TOOL_NOT_FOUND", f"{spec.executable}: {type(exc).__name__}: {exc}") from exc
    except OSError as exc:
        raise ProcessSpecError("EXECUTION_FAILED", f"{spec.executable}: the process could not be started "
                                                   f"({type(exc).__name__}: {exc})") from exc
    sinks = ({"data": bytearray(), "total": 0}, {"data": bytearray(), "total": 0})
    readers = [threading.Thread(target=_drain, args=(s, spec.capture_bytes, sink), daemon=True)
               for s, sink in ((proc.stdout, sinks[0]), (proc.stderr, sinks[1]))]
    for r in readers:
        r.start()
    timed_out = terminated = False
    try:
        proc.wait(timeout=spec.timeout)
    except subprocess.TimeoutExpired:
        timed_out = terminated = True
        _terminate(proc)
    for r in readers:
        r.join(timeout=5.0)
    duration = clock() - started
    raw_out, raw_err = bytes(sinks[0]["data"]), bytes(sinks[1]["data"])  # the bounded capture, unredacted
    out, out_n = redact(raw_out.decode("utf-8", errors="replace"))
    err, err_n = redact(raw_err.decode("utf-8", errors="replace"))
    return ProcessOutcome(
        exit_code=None if timed_out else proc.returncode,
        stdout=out, stderr=err,
        stdout_bytes=sinks[0]["total"], stderr_bytes=sinks[1]["total"],
        stdout_truncated=sinks[0]["total"] > len(sinks[0]["data"]),
        stderr_truncated=sinks[1]["total"] > len(sinks[1]["data"]),
        timed_out=timed_out, terminated=terminated,
        duration_seconds=round(duration, 6), redactions=out_n + err_n,
        raw_stdout=raw_out, raw_stderr=raw_err)


@dataclass(frozen=True)
class DetachedProcessSpec:
    """An already-authorized process that outlives the execution which starts it. No timeout and no output
    capture: the boundary does not wait for it, and its standard streams are /dev/null."""
    executable: str
    argv: tuple = ()
    cwd: str = None
    env: EnvironmentPolicy = field(default_factory=EnvironmentPolicy)

    def command_for_provenance(self):
        argv, _ = redact_all([str(a) for a in self.argv])
        return {"executable": redact(str(self.executable))[0], "argv": argv}


@dataclass(frozen=True)
class DetachedHandle:
    pid: int


def spawn_detached(spec, scopes):
    """Start an authorized detached process and return its pid without waiting. Raises ProcessSpecError for a
    spec the boundary refuses, or when the process cannot be started."""
    if sys.platform == "win32":   # D3: a Windows supervisor is designed with the Windows Player Runtime, not mapped
        raise ProcessSpecError("PLATFORM_UNSUPPORTED", "a detached process is not available on Windows in this "
                                                       "release; nothing was started")
    if not isinstance(spec, DetachedProcessSpec):
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", "a DetachedProcessSpec is required")
    if not isinstance(spec.env, EnvironmentPolicy):
        raise ProcessSpecError("UNSAFE_PROCESS_SPEC", "an explicit EnvironmentPolicy is required")
    validate_spec(ToolProcessSpec(executable=spec.executable, argv=tuple(spec.argv), cwd=spec.cwd, env=spec.env),
                  scopes)
    try:
        child = subprocess.Popen([spec.executable] + list(spec.argv), cwd=spec.cwd, env=spec.env.build(),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True, start_new_session=True)
    except (PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
        raise ProcessSpecError("TOOL_NOT_FOUND", f"{spec.executable}: {type(exc).__name__}: {exc}") from exc
    except OSError as exc:
        raise ProcessSpecError("EXECUTION_FAILED", f"{spec.executable}: the process could not be started "
                                                   f"({type(exc).__name__}: {exc})") from exc
    threading.Thread(target=child.wait, daemon=True).start()   # reap it whenever it ends; never wait here
    return DetachedHandle(child.pid)


def interpreter_path():
    """The running Python interpreter, resolved. Adapters that drive a Python helper use this rather
    than searching PATH, so the executed program is never chosen by a request."""
    return str(Path(sys.executable).resolve())


def _player_command_line(argv):
    """Private Windows quoting for the two fixed Player lifecycle vectors; never a public command input."""
    return subprocess.list2cmdline(argv)


def spawn_windows_player_supervisor(root, directory, session, scopes):
    """The fixed Player-only lifecycle amendment. General Windows detached specs remain refused."""
    if sys.platform == 'win32':
        from . import player_process_win32
        return player_process_win32.spawn_supervisor(root, directory, session, scopes)
    raise ProcessSpecError('PLATFORM_UNSUPPORTED', 'Windows Player supervision is Windows-only')
