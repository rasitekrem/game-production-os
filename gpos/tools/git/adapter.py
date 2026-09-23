"""Production version-control adapter: local Git repository provenance (Phase 2C-1).

The first real production tool adapter on the frozen Phase-2C-0 foundation. It answers two
questions about the local repository a GPOS project lives in, and nothing else:

    git.inspect             what state is the repository in?
    git.resolve-provenance  is there an exact, immutable revision that describes it?

It is deliberately not a Git command runner. The complete set of Git invocations it can make is
the three fixed argument vectors in `AUTHORIZED_COMMANDS`; no request input, no configuration and
no caller-supplied argument is ever appended to them. It has no capability that changes a
repository (no staging, committing, checkout, reset, branch or tag management, configuration
writes or anything equivalent) and no capability that reaches a network (no fetch, pull, push,
clone, remote listing, remote names, URLs or credentials).

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

Machine-readable output is parsed only when it is complete and unaltered. The process boundary
redacts credential-shaped text in captured output before an adapter sees it; if that changed the
output (a path or branch name that looks like a credential assignment), or the capture bound
truncated it, the adapter refuses to report a state rather than parse text that is not what Git
wrote.
"""

import os
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from .. import diagnostics as dg
from .. import model
from .. import process as proc
from ..capabilities import Capability, TimeoutPolicy
from ..execution import AdapterOutcome
from . import status as git_status

ADAPTER_ID = "git"
ADAPTER_VERSION = "1.0.0"
EXECUTABLE_NAME = "git"
# The oldest Git whose documentation describes every option used below: porcelain v2 with branch
# headers first appears in the 2.11 manual, GIT_OPTIONAL_LOCKS in the 2.15 manual, and
# `status --find-renames` / `--no-renames` in the 2.18 manual (absent from 2.17).
MINIMUM_VERSION = (2, 18, 0)
VERSION_OUTPUT = re.compile(r"^git version (\d+)\.(\d+)\.(\d+)(?:[.\s(].*)?$")

INSPECT = f"{ADAPTER_ID}.inspect"
RESOLVE_PROVENANCE = f"{ADAPTER_ID}.resolve-provenance"

# The complete authorized Git surface. Every process this adapter starts uses exactly one of these
# vectors, unmodified. `--find-renames` makes rename detection independent of user configuration;
# `--no-ahead-behind` skips upstream divergence counting, which this adapter never reports.
VERSION_ARGV = ("--version",)
TOPLEVEL_ARGV = ("rev-parse", "--show-toplevel")
STATUS_ARGV = ("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all",
               "--find-renames", "--no-ahead-behind")
AUTHORIZED_COMMANDS = (VERSION_ARGV, TOPLEVEL_ARGV, STATUS_ARGV)

# Non-interactive, side-effect-free Git. Names and values are fixed here; only the names are ever
# recorded (EnvironmentPolicy.metadata).
ENVIRONMENT = (
    ("GIT_TERMINAL_PROMPT", "0"),  # never prompt on a terminal
    ("GIT_OPTIONAL_LOCKS", "0"),   # status must not refresh and rewrite the index
    ("GIT_PAGER", "cat"),          # never launch a pager
    ("LC_ALL", "C"),               # deterministic wording in the messages this adapter quotes
)
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
    supported_platforms=("WINDOWS", "MACOS", "LINUX"), capabilities=CAPABILITIES,
    availability="a Git executable on PATH (absolute PATH entries only), version "
                 + ".".join(str(n) for n in MINIMUM_VERSION) + " or later",
    minimum_tool_version=".".join(str(n) for n in MINIMUM_VERSION),
    compatibility_notes=(
        "Local and read-only: no version-control mutation and no network operation exists in this adapter.",
        f"{MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED}: the GPOS project root must be Git's work-tree top level; "
        f"a project nested inside a larger repository is refused.",
        "Output that the process boundary had to redact or truncate is refused rather than parsed, so a "
        "credential-shaped path or branch name makes the state unavailable instead of wrong.",
        "Git runs with the repository's and the user's own configuration, under Git's own trust model "
        "(safe.directory). Git itself may run a configured filesystem-monitor hook during status.",
    ))


class GitAdapter(model.ToolAdapter):
    """The production Git adapter.

    `which` is a code-level seam for tests (for example, a lookup that finds nothing, or one that
    points at a stand-in program). It is never reachable from a request: the executable a request
    runs is always the one this adapter's probe resolved.
    """

    descriptor = DESCRIPTOR

    def __init__(self, which=shutil.which, status_capture_bytes=STATUS_CAPTURE_BYTES):
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
        if outcome.timed_out or outcome.exit_code != 0 or outcome.truncated or outcome.redactions:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"`git --version` did not complete normally (exit {outcome.exit_code})",
                                     capability_availability=unusable("Git did not run normally"))
        version = parse_version(outcome.stdout)
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

    def _run(self, context, argv, capture_bytes):
        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv, cwd=context.project_root,
                                    timeout=context.timeout, env=ENVIRONMENT_POLICY, capture_bytes=capture_bytes)
        return spec, context.run(spec)

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
        toplevel = top.stdout.rstrip("\n")
        if not toplevel or "\n" in toplevel:
            return None, _failed(top, spec, "`rev-parse --show-toplevel` did not return exactly one path")
        if not same_directory(toplevel, context.project_root):
            # Only the location of the enclosing repository was read. Its work tree is never inspected
            # and no scope over it is granted.
            return None, _refusal(top, spec, "REPOSITORY_ROOT_MISMATCH",
                                  f"Git's work-tree top level is {toplevel}, not the GPOS project root "
                                  f"{context.project_root}. A project nested inside a larger repository is not "
                                  f"supported yet ({MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED}); nothing in the "
                                  f"enclosing repository was inspected")
        spec, status = self._run(context, STATUS_ARGV, self._status_capture_bytes)
        refused = _unusable(status, "status", spec)
        if refused is not None:
            return None, refused
        if status.exit_code != 0:
            reason = (status.stderr.strip().splitlines() or ["no reason given"])[0]
            return None, _failed(status, spec, f"`git status` exited {status.exit_code}: {reason}")
        try:
            state = git_status.parse(status.stdout)
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
    """The process outcome without its captured stdout. Timing, exit code, truncation, byte counts and
    redaction counts still reach the foundation; the raw machine output — which names paths — does not
    reach the result, because the public contract is counts."""
    return replace(outcome, stdout="")


def _unusable(outcome, what, spec):
    """An AdapterOutcome refusing to parse output that is incomplete or altered, else None."""
    if outcome.timed_out:
        return AdapterOutcome(ok=False, process=_without_output(outcome), detail=f"`git {what}` timed out",
                              command=spec.command_for_provenance(), environment=spec.env.metadata())
    if outcome.truncated:
        return _failed(outcome, spec, f"`git {what}` output reached the capture bound and was truncated; a partial "
                                      f"reading is never reported as the repository state")
    if outcome.redactions:
        return _failed(outcome, spec, f"`git {what}` output contained credential-shaped text that the process "
                                      f"boundary redacted, so it is no longer exactly what Git wrote; no repository "
                                      f"state is reported from altered output")
    return None


def _failed(outcome, spec, detail):
    return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=_without_output(outcome), detail=detail,
                          command=spec.command_for_provenance(), environment=spec.env.metadata())


def _refusal(outcome, spec, code, message):
    """A completed inspection whose answer is that there is no repository here to inspect."""
    return AdapterOutcome(ok=True, exit_code=outcome.exit_code, process=_without_output(outcome),
                          diagnostics=(dg.make(code, message, ADAPTER_ID),),
                          command=spec.command_for_provenance(), environment=spec.env.metadata())
