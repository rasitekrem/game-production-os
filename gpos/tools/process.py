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
"""

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
        parent = os.environ if parent is None else parent
        env = {name: parent[name] for name in self.inherit if name in parent}
        env.update({name: str(value) for name, value in self.overrides})
        return env

    def metadata(self):
        """Safe environment metadata for provenance: names only, never values."""
        names = {name for name, _ in self.overrides} | set(self.recorded)
        return {"inherited_names": sorted(self.inherit), "set_names": sorted(names)}


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

    @property
    def truncated(self):
        return self.stdout_truncated or self.stderr_truncated


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

    Never raises for a failing, hanging or noisy process: those are outcomes, not exceptions.
    """
    validate_spec(spec, scopes)
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


def interpreter_path():
    """The running Python interpreter, resolved. Adapters that drive a Python helper use this rather
    than searching PATH, so the executed program is never chosen by a request."""
    return str(Path(sys.executable).resolve())
