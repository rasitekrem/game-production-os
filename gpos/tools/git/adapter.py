"""Production version-control adapter: local Git repository provenance (Phase 2C-1).

The first real production tool adapter on the frozen Phase-2C-0 foundation. It answers two
questions about the local repository a GPOS project lives in, and nothing else:

    git.inspect             what state is the repository in?
    git.resolve-provenance  is there an exact, immutable revision that describes it?

It is deliberately not a Git command runner. `AUTHORIZED_COMMANDS` declares fixed metadata reads
and status. Only check-attr additionally receives bounded index-derived paths after `--`, privately;
no caller-supplied arguments are accepted. It has no capability that changes a
repository (no staging, committing, checkout, reset, branch or tag management, configuration
writes or anything equivalent) and no capability that reaches a network (no fetch, pull, push,
clone or remote listing). Effective configuration values stay private, including any remote settings.

Execution goes through the audited process boundary like every other adapter: this module never
starts a process itself and never imports the subprocess module. Git runs with a fixed
environment policy — no terminal prompts, no optional locks (so `status` cannot refresh and
rewrite the index as a side effect), no pager, a deterministic message locale — and the policy's
variable names, never values, appear in provenance.

Repository-root rule: the GPOS project root must itself be Git's work-tree top level. A project
nested inside a larger repository fails closed rather than acquiring authority over the enclosing
repository (MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED). Git, not this module, is the authority on
repository layout: a linked worktree whose `.git` is a file is supported because Git itself reports
the project root as the top level, and nothing here parses `.git` by hand.

Provenance is handed over explicitly. The foundation never infers a repository revision; a caller
that wants one runs `git.resolve-provenance` and passes the returned revision as `build_revision`
in a later request. There is no cache, no current-revision singleton and no cross-call state: every
execution reads the repository as it is now.

Machine-readable output is parsed from the process boundary's private raw capture — the exact bytes
Git wrote — never from the public redacted text, which a credential-shaped path could rewrite. Output
the capture bound truncated is refused rather than parsed. The public result carries counts, never a
path, and anything the adapter reports still passes the foundation's redaction boundary.

Content inspection uses a bounded disposable copy with frozen effective configuration and no
executable filter definitions. A selected filter attribute is unproven and refused before status.
Includes, worktree configuration, submodules and external attributes are resolved before isolation;
status never runs in the source repository. Source stability is checked before reporting a state.
Two further repository settings are overridden because they would otherwise break this contract:
`core.fsmonitor` (it can make `git status` run a hook program or start a daemon) and
`submodule.<name>.ignore` (it can hide a dirty submodule, and with it the fact that the tree is not
exactly its HEAD commit). Both overrides are fixed, command-scope and write nothing.
"""

import os
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from .. import diagnostics as dg
from .. import executables
from .. import model
from .. import process as proc
from ..capabilities import Capability, TimeoutPolicy
from ..execution import AdapterOutcome
from . import status as git_status
from . import inspection as gi

ADAPTER_ID = "git"
ADAPTER_VERSION = "1.0.0"
EXECUTABLE_NAME = "git"
# The oldest Git that honours everything below. Porcelain v2 with branch headers first appears in the
# 2.11 manual, GIT_OPTIONAL_LOCKS in 2.15 and `status --find-renames` in 2.18; command-scope
# configuration through GIT_CONFIG_COUNT arrives in 2.31. The original constraint was fsmonitor: Git's
# own manual warns that "Git versions 2.35.1 and prior will not understand the boolean values and will
# consider the 'true' or 'false' values as hook pathnames to be invoked", and boolean core.fsmonitor
# arrives with the fsmonitor daemon in 2.36.0. On an older Git, core.fsmonitor=false would itself try to
# run a program named `false`, so the override below is only safe from 2.36.0 on.
MINIMUM_VERSION = (2, 43, 0)  # effective attribute paths, including the NULL-safe global getter in 2.43
VERSION_OUTPUT = re.compile(r"^git version (\d+)\.(\d+)\.(\d+)(?:[.\s(].*)?$")

INSPECT = f"{ADAPTER_ID}.inspect"
RESOLVE_PROVENANCE = f"{ADAPTER_ID}.resolve-provenance"

# Fixed authorized Git surface; only check-attr additionally receives bounded index-derived paths.
# `--find-renames` makes rename detection independent of user configuration;
# `--no-ahead-behind` skips upstream divergence counting, which this adapter never reports;
# `--ignore-submodules=none` overrides any `submodule.<name>.ignore` in configuration or .gitmodules,
# which could otherwise hide a dirty submodule and let a dirty tree look like an exact revision.
VERSION_ARGV = ("--version",)
TOPLEVEL_ARGV = ("rev-parse", "--show-toplevel")
STATUS_ARGV = ("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all",
               "--find-renames", "--no-ahead-behind", "--ignore-submodules=none")
AUTHORIZED_COMMANDS = (VERSION_ARGV, TOPLEVEL_ARGV, STATUS_ARGV) + gi.COMMANDS

# Non-interactive, side-effect-free Git. Names and values are fixed here, owned by the adapter and never
# influenced by a caller; only the names are ever recorded (EnvironmentPolicy.metadata).
#
# core.fsmonitor is switched off at command scope (GIT_CONFIG_COUNT/KEY/VALUE, which "override values in
# configuration files"). Repository configuration may otherwise make `git status` start the fsmonitor
# daemon or run a configured hook program — a second process this READ_ONLY capability never authorized.
# Command-scope configuration also reaches the `git status` Git itself runs inside each submodule, so a
# submodule's own hook is neutralized too. Nothing is written to any configuration file, and `-c` is
# never used. Content-reading commands use the private frozen configuration in inspection.py.
FSMONITOR_OVERRIDE = (
    ("GIT_CONFIG_COUNT", "1"),
    ("GIT_CONFIG_KEY_0", "core.fsmonitor"),
    ("GIT_CONFIG_VALUE_0", "false"),
)
ENVIRONMENT = (
    ("GIT_TERMINAL_PROMPT", "0"),  # never prompt on a terminal
    ("GIT_OPTIONAL_LOCKS", "0"),   # status must not refresh and rewrite the index
    ("GIT_PAGER", ""),             # disable pagers, including forced config pagers
    ("LC_ALL", "C"),               # deterministic wording in the messages this adapter quotes
) + FSMONITOR_OVERRIDE
ENVIRONMENT_POLICY = proc.EnvironmentPolicy(overrides=ENVIRONMENT)

PROBE_TIMEOUT = 10.0
TOPLEVEL_CAPTURE_BYTES = 64 * 1024
STATUS_CAPTURE_BYTES = 8 * 1024 * 1024  # bounded; a larger status fails closed instead of truncating

# Documented Phase-2C-1 limitations, referenced by id from diagnostics and documentation.
MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED = "MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED"
LIMITATIONS = (MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED,)

CAPABILITIES = (
    Capability(
        id=INSPECT, category="INSPECT",
        description="Report the local repository's state: HEAD, branch or detached HEAD, unborn branch, staged, "
                    "unstaged, untracked and conflicted counts, and the exact revision when the tree is clean.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_tool=True, requires_project=True,
        timeout=TimeoutPolicy(default=30.0, maximum=120.0),
        notes=("Counts only; no path list is returned.",)),
    Capability(
        id=RESOLVE_PROVENANCE, category="VERSION_CONTROL",
        description="Return the exact immutable commit that describes the repository, for a caller to hand "
                    "explicitly to later tool requests as build_revision. Refused when the tree is dirty or "
                    "the branch has no commit.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_tool=True, requires_project=True,
        timeout=TimeoutPolicy(default=30.0, maximum=120.0),
        notes=("The foundation never infers a revision; this capability is how a caller obtains one.",)),
)

DESCRIPTOR = model.AdapterDescriptor(
    adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION, tool_family="VERSION_CONTROL",
    target_tool="Git", adapter_kind="CLI", state_model="STATELESS",
    # alpha.24 (D-W1): WINDOWS is withdrawn until the repository-filter finding (D-G1) has a Human-approved fix; the
    # foundation refuses the adapter on Windows (PLATFORM_UNSUPPORTED) instead of running it unqualified.
    supported_platforms=("MACOS", "LINUX"), capabilities=CAPABILITIES,
    availability="a Git executable on PATH (absolute PATH entries only), version "
                 + ".".join(str(n) for n in MINIMUM_VERSION) + " or later",
    minimum_tool_version=".".join(str(n) for n in MINIMUM_VERSION),
    compatibility_notes=(
        "Local and read-only: no version-control mutation and no network operation exists in this adapter.",
        f"{MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED}: the GPOS project root must be Git's work-tree top level; "
        f"a project nested inside a larger repository is refused.",
        "Git's machine output is parsed from the exact captured bytes; truncated output is refused rather "
        "than parsed, and public output stays redacted.",
        "Git's safe.directory trust check precedes a bounded private inspection copy. Effective configuration "
        "is frozen without includes or executable filter definitions; selected filters and unproven trees "
        "are refused. Fsmonitor, hooks and submodule ignore settings cannot authorize side effects or hide dirtiness.",
    ))


class GitAdapter(model.ToolAdapter):
    """The production Git adapter.

    `which` is a code-level seam for tests (for example, a lookup that finds nothing, or one that
    points at a stand-in program). It is never reachable from a request: the executable a request
    runs is always the one this adapter's probe resolved.
    """

    descriptor = DESCRIPTOR

    def __init__(self, which=shutil.which, status_capture_bytes=STATUS_CAPTURE_BYTES):
        if sys.platform == "win32":   # alpha.24: PATH only, never the current directory, only <name>.exe
            which = executables.windows_which if which is shutil.which else which
        self._which = which
        self._status_capture_bytes = status_capture_bytes

    # ------------------------------------------------------------ probe

    def probe(self):
        platform = model.current_platform()
        unusable = lambda reason: tuple((c.id, False, reason) for c in CAPABILITIES)
        found = self._which(EXECUTABLE_NAME)
        if not found:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail="no Git executable was found on PATH",
                                     capability_availability=unusable("Git is not installed"))
        if not os.path.isabs(found):
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail="Git was only found through a relative PATH entry, which would make the "
                                            "executed program depend on the working directory; it is not used",
                                     capability_availability=unusable("Git is not on an absolute PATH entry"))
        executable = str(Path(found).resolve())
        neutral = str(Path(tempfile.gettempdir()).resolve())
        spec = proc.ToolProcessSpec(executable=executable, argv=VERSION_ARGV, cwd=neutral,
                                    timeout=PROBE_TIMEOUT, env=ENVIRONMENT_POLICY)
        try:
            outcome = proc.run_process(spec, [neutral])
        except proc.ProcessSpecError as exc:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"Git could not be started: {exc}",
                                     capability_availability=unusable("Git could not be started"))
        if outcome.timed_out or outcome.exit_code != 0 or outcome.truncated:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"`git --version` did not complete normally (exit {outcome.exit_code})",
                                     capability_availability=unusable("Git did not run normally"))
        version = parse_version(outcome.raw_stdout.decode("utf-8", errors="replace"))
        if version is None:
            # The version output format is not documented as stable. An unrecognized format is never
            # turned into a guessed version: compatibility cannot be established, so it is not claimed.
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable,
                                     platform=platform,
                                     detail=f"unrecognized version output {outcome.stdout.strip()[:80]!r}; "
                                            f"the Git version could not be established",
                                     capability_availability=unusable("Git version unknown"))
        text = ".".join(str(n) for n in version)
        if version < MINIMUM_VERSION:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable,
                                     tool_version=text, platform=platform,
                                     detail=f"Git {text} is older than the required "
                                            f"{DESCRIPTOR.minimum_tool_version}",
                                     capability_availability=unusable("Git version unsupported"))
        return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=executable, tool_version=text,
                                 platform=platform, detail=f"Git {text} at {executable}",
                                 capability_availability=tuple((c.id, True, "") for c in CAPABILITIES))

    # ------------------------------------------------------------ execution

    def execute(self, request, context):
        if request.capability_id not in (INSPECT, RESOLVE_PROVENANCE):
            raise AssertionError(f"{request.capability_id} is declared but not implemented")
        reading, refused = self._repository_state(context)
        if refused is not None:
            return refused
        return self._reply(reading) if request.capability_id == INSPECT else self._resolve(reading)

    def _run(self, context, argv, capture_bytes, root=None, inspection=None):
        if argv not in AUTHORIZED_COMMANDS and not (
                tuple(argv[:4]) == gi.ATTRIBUTES_ARGV and 4 < len(argv) and
                sum(len(a.encode("utf-16-le", "surrogatepass")) + 8 for a in argv[4:]) <= gi.ATTRIBUTE_ARG_BYTES):
            raise AssertionError("unauthorized Git inspection vector")
        policy = ENVIRONMENT_POLICY
        scoped = context
        if inspection is not None:
            policy = proc.EnvironmentPolicy(overrides=ENVIRONMENT + (
                ("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_CONFIG_GLOBAL", os.devnull), ("GIT_ATTR_NOSYSTEM", "1")))
            # Only this adapter-created temporary directory is added, for this invocation's lifetime.
            scoped = replace(context, scopes=context.scopes + (str(inspection.base),))
        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv, cwd=str(root or context.project_root),
                                    timeout=min(context.timeout, inspection.seconds()) if inspection else context.timeout,
                                    env=policy, capture_bytes=capture_bytes)
        return spec, scoped.run(spec)

    def _private_read(self, context, argv, root, inspection=None):
        spec, outcome = self._run(context, argv, gi.CAPTURE_BYTES, root, inspection)
        if (argv in (gi.EXCLUDES_ARGV, gi.SYSTEM_ATTRIBUTES_ARGV, gi.GLOBAL_ATTRIBUTES_ARGV) and
                outcome.exit_code == 1 and not outcome.raw_stdout and not outcome.raw_stderr and
                not outcome.timed_out and not outcome.truncated and outcome.integrity_ok):
            return b""  # documented absence, never a failed config parse
        if outcome.exit_code != 0 or outcome.timed_out or outcome.truncated or not outcome.integrity_ok:
            raise gi.UnsafeInspection("incomplete or failed private Git metadata read")
        return outcome.raw_stdout

    def _inspection_copy(self, context, copy):
        repositories = []

        def read(argv, root):
            return self._private_read(replace(context, timeout=min(context.timeout, copy.seconds())), argv, root)

        def discover(root):
            if len(repositories) >= gi.MAX_REPOSITORIES or any(root == item[0] for item in repositories):
                raise gi.UnsafeInspection("repeated or excessive submodule layout")
            layout_raw = read(gi.LAYOUT_ARGV, root)
            layout = tuple((root / os.fsdecode(line)).resolve() for line in gi.path_lines(layout_raw, 2))
            config = read(gi.CONFIG_ARGV, root)
            entries = gi.config_entries(config)
            paths, children = gi.index_paths(read(gi.INDEX_ARGV, root))
            attrs = []
            for argv in (gi.SYSTEM_ATTRIBUTES_ARGV, gi.GLOBAL_ATTRIBUTES_ARGV):
                raw = read(argv, root)
                attrs.append((root / os.fsdecode(gi.path_lines(raw, 1)[0])).resolve() if raw else None)
            raw_excludes = read(gi.EXCLUDES_ARGV, root)
            if raw_excludes:
                excludes = (root / os.fsdecode(gi.path_lines(raw_excludes, 1)[0])).resolve()
            else:
                environment = ENVIRONMENT_POLICY.build()
                home = environment.get("HOME") or environment.get("USERPROFILE")
                excludes = Path(home) / ".config" / "git" / "ignore" if home else None
            repositories.append((root, layout, config, entries, paths, children, attrs, excludes))
            for name in children:
                child = root / name
                if not (child / ".git").exists():
                    raise gi.UnsafeInspection("uninitialized submodule cannot establish a complete revision")
                if child.is_symlink() or not child.resolve().is_relative_to(Path(context.project_root).resolve()):
                    raise gi.UnsafeInspection("submodule outside the project scope")
                discover(child)

        source = Path(context.project_root).resolve()
        discover(source)
        skip = tuple((item[0].relative_to(source) / ".git").as_posix() for item in repositories)
        copy.tree(source, copy.root, skip=skip)
        for root, layout, raw, entries, paths, children, attrs, excludes in repositories:
            target = copy.root / root.relative_to(source)
            copy.add_repository(root, target, layout, entries, paths, children)
            copy.attributes(target, *attrs)
            copy.excludes(target, excludes)
            for argv in gi.attribute_batches(paths):
                gi.refuse_filters(self._private_read(context, argv, target, copy), len(argv) - len(gi.ATTRIBUTES_ARGV))
        copy.invalidate_stats()
        return repositories

    def _repository_state(self, context):
        """(reading, None), where reading holds the state, the status spec and its outcome, or
        (None, AdapterOutcome) refusing to report a state."""
        spec, top = self._run(context, TOPLEVEL_ARGV, TOPLEVEL_CAPTURE_BYTES)
        refused = _unusable(top, "rev-parse --show-toplevel", spec)
        if refused is not None:
            return None, refused
        if top.exit_code != 0:
            # Any failure to name a work tree at the project root is the same answer for this adapter:
            # there is no repository here it may inspect. Git's own first line explains which.
            reason = (top.stderr.strip().splitlines() or ["no reason given"])[0]
            return None, _refusal(top, spec, "REPOSITORY_NOT_FOUND",
                                  f"Git found no work tree at the GPOS project root ({reason})")
        raw_toplevel = top.raw_stdout[:-1] if top.raw_stdout.endswith(b"\n") else top.raw_stdout
        if not raw_toplevel or b"\n" in raw_toplevel or b"\0" in raw_toplevel:
            return None, _failed(top, spec, "`rev-parse --show-toplevel` did not return exactly one path")
        toplevel = os.fsdecode(raw_toplevel)            # exact, for the comparison
        shown = raw_toplevel.decode("utf-8", errors="backslashreplace")  # printable, for the message
        if not same_directory(toplevel, context.project_root):
            # Only the location of the enclosing repository was read. Its work tree is never inspected
            # and no scope over it is granted.
            return None, _refusal(top, spec, "REPOSITORY_ROOT_MISMATCH",
                                  f"Git's work-tree top level is {shown}, not the GPOS project root "
                                  f"{context.project_root}. A project nested inside a larger repository is not "
                                  f"supported yet ({MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED}); nothing in the "
                                  f"enclosing repository was inspected")
        copy = gi.InspectionCopy(context.timeout)
        try:
            repositories = self._inspection_copy(context, copy)
            spec, status = self._run(context, STATUS_ARGV, self._status_capture_bytes, copy.root, copy)
            copy.verify()
            for root, layout, raw, entries, paths, children, attrs, excludes in repositories:
                bounded = replace(context, timeout=min(context.timeout, copy.seconds()))
                if self._private_read(bounded, gi.CONFIG_ARGV, root) != raw:
                    raise gi.UnsafeInspection("repository configuration changed during inspection")
                current = gi.path_lines(self._private_read(bounded, gi.LAYOUT_ARGV, root), 2)
                if tuple((root / os.fsdecode(line)).resolve() for line in current) != layout:
                    raise gi.UnsafeInspection("repository layout changed during inspection")
        except (gi.UnsafeInspection, OSError, ValueError):
            return None, _failed(top, spec, "safe Git inspection could not be established; no repository state or "
                                           "exact revision is reported (filter, layout, source stability or copy bound)")
        finally:
            copy.close()
        refused = _unusable(status, "status", spec)
        if refused is not None:
            return None, refused
        if status.exit_code != 0:
            reason = (status.stderr.strip().splitlines() or ["no reason given"])[0]
            return None, _failed(status, spec, f"`git status` exited {status.exit_code}: {reason}")
        try:
            state = git_status.parse(status.raw_stdout)
        except git_status.StatusParseError as exc:
            return None, _failed(status, spec, f"`git status` output could not be read as complete porcelain v2 "
                                               f"({exc}); no repository state is reported")
        state = {"repository_root": str(Path(context.project_root).resolve()), **state}
        return {"state": state, "spec": spec, "process": _without_output(status)}, None

    def _reply(self, reading):
        spec = reading["spec"]
        return AdapterOutcome(ok=True, exit_code=0, process=reading["process"], data=dict(reading["state"]),
                              command=spec.command_for_provenance(), environment=spec.env.metadata())

    def _resolve(self, reading):
        state, spec = reading["state"], reading["spec"]
        data = {"repository_revision": state["exact_revision"], "head_sha": state["head_sha"],
                "branch": state["branch"], "detached": state["detached"]}
        problem = None
        if state["unborn"]:
            problem = (f"the branch {state['branch']!r} has no commit yet, so there is no revision to hand over; "
                       f"commit first")
        elif not state["clean"]:
            problem = (f"the working tree differs from HEAD {state['head_sha']} (staged {state['staged_count']}, "
                       f"unstaged {state['unstaged_count']}, untracked {state['untracked_count']}, conflicted "
                       f"{state['conflicted_count']}); a dirty tree is never represented as its HEAD commit")
        elif state["exact_revision"] is None:
            problem = "no exact revision is available"
        diagnostics = ()
        if problem is not None:
            data["repository_revision"] = None
            diagnostics = (dg.make("REPOSITORY_STATE_CONFLICT", problem, ADAPTER_ID, RESOLVE_PROVENANCE),)
        return AdapterOutcome(ok=True, exit_code=0, process=reading["process"], data=data, diagnostics=diagnostics,
                              command=spec.command_for_provenance(), environment=spec.env.metadata())


# ---------------------------------------------------------------- helpers

def parse_version(text):
    """(major, minor, patch) from `git version X.Y.Z…`, or None when the output is not recognized."""
    lines = (text or "").strip().splitlines()
    if len(lines) != 1:
        return None
    match = VERSION_OUTPUT.match(lines[0].strip())
    return tuple(int(n) for n in match.groups()) if match else None


def same_directory(left, right):
    """Whether two paths name the same directory once resolved (case-normalized where the platform is)."""
    try:
        a, b = Path(left).resolve(), Path(right).resolve()
    except (OSError, ValueError):
        return False
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _without_output(outcome):
    """The process outcome without its captured stdout, public or raw. Timing, exit code, truncation,
    byte counts and redaction counts still reach the foundation; the machine output — which names
    paths — goes no further than the parser, because the public contract is counts."""
    return replace(outcome, stdout="", raw_stdout=b"", raw_stderr=b"")


def _unusable(outcome, what, spec):
    """An AdapterOutcome refusing to parse output that is incomplete (timed out or truncated), else None."""
    if outcome.timed_out:
        return AdapterOutcome(ok=False, process=_without_output(outcome), detail=f"`git {what}` timed out",
                              command=spec.command_for_provenance(), environment=spec.env.metadata())
    if outcome.truncated:
        return _failed(outcome, spec, f"`git {what}` output reached the capture bound and was truncated; a partial "
                                      f"reading is never reported as the repository state")
    return None


def _failed(outcome, spec, detail):
    return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=_without_output(outcome), detail=detail,
                          command=spec.command_for_provenance(), environment=spec.env.metadata())


def _refusal(outcome, spec, code, message):
    """A completed inspection whose answer is that there is no repository here to inspect."""
    return AdapterOutcome(ok=True, exit_code=outcome.exit_code, process=_without_output(outcome),
                          diagnostics=(dg.make(code, message, ADAPTER_ID),),
                          command=spec.command_for_provenance(), environment=spec.env.metadata())
