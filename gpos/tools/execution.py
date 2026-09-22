"""Execution: the request, the result, and the foundation that owns the semantics between them.

An adapter maps a capability to a real invocation and reports what it did. Everything that decides
whether an execution may happen at all, and what it is allowed to claim afterwards, lives here and
runs whether the adapter cooperates or not:

    request validation -> project precondition -> adapter readiness -> mutation consent
    -> single-writer lease -> adapter.execute() -> artifact hashing and path checks
    -> provenance -> evidence-candidate validation -> lease release -> fail-closed status

Fail closed is the rule: the status is the most severe class among the diagnostics, so no path
returns SUCCESS while something blocking was recorded, and `mutation_performed` is only ever true
when a MUTATING capability actually ran outside a dry run. Success is never inferred from an exit
code, because not every adapter drives a command-line process.
"""

import datetime
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import artifacts as art
from . import diagnostics as dg
from . import evidence as ev
from . import leases as lease_mod
from . import model
from . import paths as tp
from . import process as proc
from .capabilities import Capability
from .redaction import redact, sanitize_all, sanitize_or_none


# The cross-record identifier shape the frozen evidence and gate schemas use for an actor id.
ACTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")


def vocabulary_problems(framework, request, capability):
    """[ToolDiagnostic] for canonical request fields that are not GPOS vocabulary.

    Checked at the boundary, before any adapter runs: an invalid subject must never reach an accepted
    evidence candidate and surface much later as a schema failure during materialization.
    """
    reg = framework.registry
    adapter, cap = request.adapter_id, request.capability_id
    bad = lambda message: dg.make("INVALID_TOOL_REQUEST", message, adapter, cap)
    out = []
    subject = request.subject
    if subject is not None:
        if subject.kind not in reg["gate_scope_kinds"]:
            out.append(bad(f"subject kind {subject.kind!r} is not a GPOS scope kind {reg['gate_scope_kinds']}"))
        if not isinstance(subject.ref, str) or not subject.ref.strip():
            out.append(bad("the subject reference must be a non-empty string"))
        if subject.revision is not None and (not isinstance(subject.revision, str) or not subject.revision.strip()):
            out.append(bad("a supplied subject revision must be a non-empty string; omit it when it is unknown"))
    actor = request.actor
    if actor is not None:
        if actor.kind not in reg["actor_kinds"]:
            out.append(bad(f"actor kind {actor.kind!r} is not a GPOS actor kind {reg['actor_kinds']}"))
        if not isinstance(actor.id, str) or not ACTOR_ID.match(actor.id or ""):
            out.append(bad(f"actor id {actor.id!r} is not a usable cross-record identifier"))
    if request.target_platform is not None and request.target_platform not in reg["platforms"]:
        out.append(bad(f"target platform {request.target_platform!r} is not in registry platforms {reg['platforms']}"))
    if request.resource_id is not None and not capability.single_writer_required:
        out.append(bad(f"{cap} takes no single-writer lease, so a resource_id has no meaning for it"))
    for field in ("build_revision", "build_id", "device", "routing_ref", "resource_id"):
        value = getattr(request, field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            out.append(bad(f"{field} must be a non-empty string when supplied"))
    for pair in request.expected_evidence or ():
        if not (isinstance(pair, (tuple, list)) and len(pair) == 2):
            out.append(bad(f"expected evidence {pair!r} must be (evidence type, capture context)"))
            continue
        etype, context = pair
        if etype not in reg["evidence_types"] or context not in reg["capture_contexts"]:
            out.append(bad(f"expected evidence {etype!r} in {context!r} is not GPOS vocabulary"))
        elif context not in reg["evidence_context_compatibility"][etype]:
            out.append(bad(f"expected evidence {etype} cannot be captured in {context} "
                           f"(registry evidence_context_compatibility)"))
        elif (etype, context) not in capability.evidence_pairs():
            out.append(bad(f"{cap} never produces {etype} captured in {context}; it declares "
                           f"{[list(p) for p in capability.evidence_pairs()]}"))
    return out


def rfc3339(moment=None):
    moment = moment or datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Clock:
    """Wall clock for timestamps, monotonic clock for durations. Injectable so tests need no sleeps."""

    def now(self):
        return rfc3339()

    def monotonic(self):
        return time.monotonic()


@dataclass(frozen=True)
class ExecutionRequest:
    adapter_id: str
    capability_id: str
    subject: model.Subject
    project_root: str = None
    inputs: dict = field(default_factory=dict)
    input_artifacts: tuple = ()       # model.InputArtifact: existing artifacts this execution consumes
    resource_id: str = None           # the single-writer target, for a capability that declares one
    output_dir: str = None            # where artifacts may be written; defaults to the runtime area
    dry_run: bool = False
    allow_mutation: bool = False      # a MUTATING capability runs only with explicit consent
    timeout: float = None
    actor: model.Actor = None
    routing_ref: str = None
    build_revision: str = None
    build_id: str = None
    target_platform: str = None
    device: str = None
    expected_evidence: tuple = ()     # ((evidence type, capture context), ...) the caller hopes for; never a promise
    request_id: str = None

    def with_id(self, request_id):
        return dataclasses_replace(self, request_id=request_id)

    def to_dict(self):
        out = {"request_id": self.request_id, "adapter_id": self.adapter_id, "capability_id": self.capability_id,
               "subject": self.subject.to_dict() if self.subject else None,
               "project_root": self.project_root, "inputs": dict(self.inputs),
               "input_artifacts": [a.to_dict() for a in self.input_artifacts], "dry_run": self.dry_run,
               "allow_mutation": self.allow_mutation, "timeout": self.timeout,
               "expected_evidence": [{"evidence_type": t, "capture_context": c} for t, c in self.expected_evidence]}
        for name in ("output_dir", "resource_id", "routing_ref", "build_revision", "build_id",
                     "target_platform", "device"):
            if getattr(self, name) is not None:
                out[name] = getattr(self, name)
        if self.actor is not None:
            out["actor"] = self.actor.to_dict()
        return out


def dataclasses_replace(obj, **changes):
    import dataclasses
    return dataclasses.replace(obj, **changes)


@dataclass(frozen=True)
class ExecutionContext:
    """What the foundation hands an adapter. An adapter never opens a subprocess itself: it builds a
    ToolProcessSpec and calls `run`, which applies the boundary rules."""
    request: ExecutionRequest
    capability: Capability
    project_root: str
    workspace: str                    # the only directory the adapter may write artifacts into
    scopes: tuple                     # permitted filesystem scopes
    clock: Clock
    probe: model.ProbeResult
    dry_run: bool
    timeout: float
    input_artifacts: tuple = ()       # already hashed and path-checked by the foundation
    lease: lease_mod.Lease = None

    def run(self, spec):
        """Execute an authorized process spec under the foundation's boundary rules."""
        return proc.run_process(spec, self.scopes, self.clock.monotonic)

    def artifact_path(self, *parts):
        """A path inside this execution's workspace. Nothing else is a legal artifact location."""
        return Path(self.workspace).joinpath(*parts)


@dataclass(frozen=True)
class AdapterOutcome:
    """What an adapter reports. The foundation decides what it means."""
    ok: bool = True
    exit_code: int = None
    process: proc.ProcessOutcome = None
    artifacts: tuple = ()             # ArtifactSpec
    evidence: tuple = ()              # EvidenceCandidate (unvalidated: the foundation validates them)
    diagnostics: tuple = ()
    mutation_performed: bool = False
    detail: str = ""
    data: dict = None                 # tool-specific parsed values; never bytes, never secrets
    plan: tuple = ()                  # dry run: what would have happened
    command: dict = None              # the redacted spec that ran, for provenance
    environment: dict = None          # environment *names* only, from the policy; never values


@dataclass(frozen=True)
class ToolResult:
    request_id: str
    adapter_id: str
    capability_id: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    dry_run: bool
    mutation_performed: bool
    exit_code: int = None
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    output_truncated: bool = False
    artifacts: tuple = ()
    evidence_candidates: tuple = ()
    provenance: object = None
    diagnostics: tuple = ()
    data: dict = None
    plan: tuple = ()

    @property
    def ok(self):
        return self.status == dg.SUCCESS

    @property
    def exit_code_for_cli(self):
        return dg.EXIT_FOR[self.status]

    def to_dict(self):
        return {"request_id": self.request_id, "adapter_id": self.adapter_id, "capability_id": self.capability_id,
                "status": self.status, "started_at": self.started_at, "finished_at": self.finished_at,
                "duration_seconds": self.duration_seconds, "dry_run": self.dry_run,
                "mutation_performed": self.mutation_performed, "exit_code": self.exit_code,
                "stdout": self.stdout, "stderr": self.stderr,
                "stdout_bytes": self.stdout_bytes, "stderr_bytes": self.stderr_bytes,
                "output_truncated": self.output_truncated,
                "artifacts": [a.to_dict() for a in self.artifacts],
                "evidence_candidates": [c.to_dict() for c in self.evidence_candidates],
                "provenance": self.provenance.to_dict() if self.provenance else None,
                "diagnostics": [d.to_dict() for d in self.diagnostics],
                "data": self.data or {}, "plan": list(self.plan)}


# ---------------------------------------------------------------- preconditions

def project_problems(framework, request, capability):
    """Proportional project preconditions.

    A capability that touches project records needs a project that the frozen Phase-2A validator
    reports VALID and a supported pinned GPOS version. A capability that does not (a probe, a tool
    inspection) is never blocked on project state, and no capability is blocked because some
    *unrelated* routing is NOT_READY — readiness is only consulted when the capability says it acts
    on a routed task.
    """
    from ..errors import BundleNotFound, UnsupportedGposVersion
    from ..records import load_project
    from ..validation.project import evaluate_readiness, validate_project
    adapter, cap = request.adapter_id, request.capability_id
    if not capability.requires_project:
        if request.project_root:
            return None, [dg.make("INVALID_TOOL_REQUEST",
                                  f"{cap} is not project-bound, so it never receives filesystem authority over a "
                                  f"project tree: remove project_root and name an output_dir (or declare an adapter "
                                  f"filesystem scope) instead", adapter, cap)]
        return None, []
    if not request.project_root:
        return None, [dg.make("INVALID_TOOL_REQUEST", f"{cap} is project-bound and needs project_root", adapter, cap)]
    root = Path(request.project_root).resolve()
    if not (root / ".game" / "gpos" / "project-config.json").is_file():
        return None, [dg.make("PROJECT_LAYOUT", f"{root}: not a GPOS project root (no .game/gpos/project-config.json)",
                              adapter, cap)]
    try:
        record_set = load_project(root)
        result = validate_project(record_set, framework)
    except UnsupportedGposVersion as exc:
        return None, [dg.make("GPOS_VERSION_INCOMPATIBLE", str(exc), adapter, cap)]
    except BundleNotFound as exc:
        return None, [dg.make("PROJECT_LAYOUT", str(exc), adapter, cap)]
    if not result.valid:
        codes = sorted({d.code for d in result.diagnostics if d.severity == "ERROR"})
        return None, [dg.make("PROJECT_INVALID",
                              f"the production validator reports the project INVALID ({', '.join(codes)}); tool "
                              f"execution that depends on project records is refused until the records are fixed",
                              adapter, cap)]
    if capability.requires_ready_routing:
        routing = request.routing_ref
        if not routing:
            return record_set, [dg.make("INVALID_TOOL_REQUEST", f"{cap} acts on a routed task and needs routing_ref",
                                        adapter, cap)]
        readiness = evaluate_readiness(record_set, routing, framework)
        if not readiness.ready:
            return record_set, [dg.make("ROUTING_NOT_READY",
                                        f"{routing} is not READY, and {cap} declares it acts on a READY routing",
                                        adapter, cap)]
    return record_set, []


def request_problems(registry, request):
    """(adapter, capability, [ToolDiagnostic]) — structural validation before anything runs."""
    adapter_id, cap_id = request.adapter_id, request.capability_id
    adapter = registry.get(adapter_id)
    if adapter is None:
        return None, None, [dg.make("ADAPTER_NOT_FOUND",
                                    f"no tool adapter {adapter_id!r} is registered; registered: "
                                    f"{registry.adapter_ids()}", adapter_id, cap_id)]
    capability = adapter.descriptor.capability(cap_id)
    if capability is None:
        return adapter, None, [dg.make("CAPABILITY_NOT_FOUND",
                                       f"{adapter_id} declares no capability {cap_id!r}; declared: "
                                       f"{sorted(c.id for c in adapter.descriptor.capabilities)}", adapter_id, cap_id)]
    problems = []
    if request.subject is None:
        problems.append(dg.make("INVALID_TOOL_REQUEST", "a request must name the subject it is about",
                                adapter_id, cap_id))
    if request.dry_run and not capability.dry_run_supported:
        problems.append(dg.make("DRY_RUN_UNSUPPORTED", f"{cap_id} does not support dry run", adapter_id, cap_id))
    if capability.mutating and not request.dry_run and not request.allow_mutation:
        problems.append(dg.make("MUTATION_NOT_ALLOWED",
                                f"{cap_id} is MUTATING ({capability.side_effect_scope}); execution requires explicit "
                                f"allow_mutation consent in the request", adapter_id, cap_id))
    if not capability.mutating and request.allow_mutation:
        problems.append(dg.make("INVALID_TOOL_REQUEST",
                                f"{cap_id} is READ_ONLY; mutation consent does not apply to it", adapter_id, cap_id))
    platform = model.current_platform()
    if platform is not None and platform not in adapter.descriptor.supported_platforms:
        problems.append(dg.make("PLATFORM_UNSUPPORTED",
                                f"{adapter_id} supports {list(adapter.descriptor.supported_platforms)}, not {platform}",
                                adapter_id, cap_id))
    unknown = sorted(set(request.inputs or {}) - set(capability.input_kinds))
    if unknown:
        problems.append(dg.make("INVALID_TOOL_REQUEST",
                                f"{cap_id} accepts inputs {list(capability.input_kinds)}; unknown inputs {unknown}",
                                adapter_id, cap_id))
    _, error = capability.timeout.resolve(request.timeout)
    if error:
        problems.append(dg.make("TIMEOUT_NOT_PERMITTED", f"{cap_id}: {error}", adapter_id, cap_id))
    return adapter, capability, problems


# ---------------------------------------------------------------- execution

def execute(registry, request, clock=None, now=None):
    """Run one capability under the full foundation contract and return a ToolResult.

    Never raises for a tool problem: a missing tool, a failing tool, a hanging tool, a lease conflict
    and an adapter defect are all structured results.
    """
    clock = clock or Clock()
    request = request if request.request_id else dataclasses_replace(request, request_id=f"req-{uuid.uuid4().hex[:16]}")
    started_at, started = now or clock.now(), clock.monotonic()
    framework = registry.framework

    def finish(status, diagnostics, **kw):
        diagnostics = dg.sort(diagnostics)
        resolved = dg.result_status(diagnostics, default=status)
        return ToolResult(request_id=request.request_id, adapter_id=request.adapter_id,
                          capability_id=request.capability_id, status=resolved, started_at=started_at,
                          finished_at=clock.now(), duration_seconds=round(clock.monotonic() - started, 6),
                          dry_run=bool(request.dry_run), diagnostics=tuple(diagnostics),
                          **{"mutation_performed": False, **kw})

    adapter, capability, problems = request_problems(registry, request)
    if problems:
        return finish(dg.INVALID_REQUEST, problems)

    problems = vocabulary_problems(framework, request, capability)
    if problems:
        return finish(dg.INVALID_REQUEST, problems)

    record_set, problems = project_problems(framework, request, capability)
    if problems:
        return finish(dg.INVALID_REQUEST, problems)
    project_id = record_set.project_id if record_set is not None else None

    probe = None
    if capability.requires_tool:
        probe, problems = registry.ready(request.adapter_id, started_at)
        if problems:
            return finish(dg.UNAVAILABLE, problems)
    elif registry.state(request.adapter_id) is not None:
        probe = registry.state(request.adapter_id).probe

    root = Path(request.project_root).resolve() if request.project_root else None
    scopes, workspace, problems = _scopes_and_workspace(adapter, capability, request, root)
    if problems:
        return finish(dg.INVALID_REQUEST, problems)

    inputs, problems = art.collect_inputs(request.input_artifacts, scopes, root or workspace,
                                          framework.registry["capture_contexts"])
    if problems:
        return finish(dg.INVALID_REQUEST, [dg.make(code, message, request.adapter_id, request.capability_id, aid)
                                           for code, aid, message in problems])

    diagnostics, lease = [], None
    if capability.single_writer_required and not request.dry_run:
        lease, problems = _acquire(root, adapter, capability, request, started_at)
        if problems:
            return finish(dg.CONFLICT, problems)
        diagnostics.append(dg.make("LEASE_ACQUIRED", f"single-writer lease held on {lease.resource_id}",
                                   request.adapter_id, request.capability_id))

    timeout, _ = capability.timeout.resolve(request.timeout)
    context = ExecutionContext(request=request, capability=capability, project_root=str(root) if root else None,
                               workspace=str(workspace), scopes=tuple(str(s) for s in scopes), clock=clock,
                               probe=probe, dry_run=bool(request.dry_run), timeout=timeout,
                               input_artifacts=tuple(inputs), lease=lease)
    try:
        outcome = adapter.execute(request, context)
        if not isinstance(outcome, AdapterOutcome):
            raise TypeError(f"{request.adapter_id}.execute() must return an AdapterOutcome, not {type(outcome).__name__}")
    except proc.ProcessSpecError as exc:
        code = exc.code if exc.code in dg.CODES else "UNSAFE_PROCESS_SPEC"
        return _release_and_finish(root, lease, finish, dg.CODES[code][0], diagnostics + [
            dg.make(code, redact(str(exc))[0], request.adapter_id, request.capability_id)],
            request.adapter_id, request.capability_id)
    except Exception as exc:
        return _release_and_finish(root, lease, finish, dg.INTERNAL_ERROR, diagnostics + [
            dg.make("ADAPTER_INTERNAL_ERROR",
                    f"{request.adapter_id}.execute() raised {type(exc).__name__}: {redact(str(exc))[0]}",
                    request.adapter_id, request.capability_id)], request.adapter_id, request.capability_id)

    try:
        result = _assemble(framework, registry, request, adapter, capability, context, outcome,
                           diagnostics, probe, root, started_at, started, clock, inputs, project_id)
    except Exception as exc:  # assembling the result must never strand a lease
        return _release_and_finish(root, lease, finish, dg.INTERNAL_ERROR, diagnostics + [
            dg.make("ADAPTER_INTERNAL_ERROR",
                    f"the foundation could not assemble a result for {request.capability_id}: "
                    f"{type(exc).__name__}: {redact(str(exc))[0]}", request.adapter_id, request.capability_id)],
            request.adapter_id, request.capability_id)
    problems = _release(root, lease, request.adapter_id, request.capability_id)
    if problems:  # the status is recomputed: a stranded lease is never a clean SUCCESS
        diagnostics = dg.sort(list(result.diagnostics) + problems)
        return dataclasses_replace(result, diagnostics=tuple(diagnostics),
                                   status=dg.result_status(diagnostics, default=result.status))
    return result


def _resource(request, capability, root):
    """(resource string, problem or None) — the deterministic identity a single-writer lease is taken on.

    A capability that leases the project leases the *resolved* project root, so two spellings of the
    same directory always map to the same lease. A capability that leases something else declares
    `resource_from_request`, and the request names it explicitly: there is no magic input name acting
    as an undocumented lease protocol.
    """
    if capability.resource_from_request:
        if not isinstance(request.resource_id, str) or not request.resource_id.strip():
            return None, (f"{capability.id} takes a single-writer lease on a resource the request names; "
                          f"supply resource_id")
        return f"{capability.resource_kind}:{request.resource_id.strip()}", None
    if request.resource_id is not None:
        return None, (f"{capability.id} leases the project itself, so it does not take a resource_id; "
                      f"remove it")
    if root is None:
        return None, f"{capability.id} leases the project, so it needs project_root"
    return f"{capability.resource_kind}:{Path(root).resolve()}", None


def _acquire(root, adapter, capability, request, now):
    if root is None:
        return None, [dg.make("INVALID_TOOL_REQUEST",
                              f"{capability.id} needs a single-writer lease, which lives in the project runtime area, "
                              f"so it needs project_root", request.adapter_id, request.capability_id)]
    resource, problem = _resource(request, capability, root)
    if problem:
        return None, [dg.make("INVALID_TOOL_REQUEST", problem, request.adapter_id, request.capability_id)]
    owner = request.actor.id if request.actor else f"pid-{os.getpid()}"
    try:
        lease = lease_mod.acquire(root, request.adapter_id, resource, owner, now, request.request_id)
    except lease_mod.LeaseHeld as exc:
        diags = [dg.make(exc.code, str(exc), request.adapter_id, request.capability_id, exc.path,
                         {"holder": exc.holder} if exc.holder else None)]
        if exc.holder and lease_mod.looks_stale(exc.holder, lease_mod.pid_alive):
            diags.append(dg.make("LEASE_STALE",
                                 f"the holding process (pid {exc.holder.get('pid')}) is gone. GPOS never breaks a lease "
                                 f"automatically; recovery is an explicit operation",
                                 request.adapter_id, request.capability_id, exc.path))
        return None, diags
    return lease, []


def _release(root, lease, adapter_id=None, capability_id=None):
    """Release a held lease. [ToolDiagnostic] — non-empty when the foundation could not release it.

    A failed release is blocking: an execution must never report a clean SUCCESS while the writer
    lease it took is still lying in the project. The lease itself is left untouched, because the
    foundation never deletes a lease it could not verify.
    """
    if lease is None or root is None:
        return []
    if lease_mod.release(root, lease):
        return []
    return [dg.make("LEASE_RELEASE_FAILED",
                    f"the single-writer lease on {lease.resource_id} could not be released; it is still held and is "
                    f"not removed unverified. Recovering it is an explicit operation",
                    adapter_id, capability_id, lease.path)]


def _release_and_finish(root, lease, finish, status, diagnostics, adapter_id=None, capability_id=None):
    return finish(status, list(diagnostics) + _release(root, lease, adapter_id, capability_id))


def _scopes_and_workspace(adapter, capability, request, root):
    """The permitted filesystem scopes and the directory artifacts may be written into.

    The default is project-root bounded: a project-bound capability may only touch the project tree,
    plus any extra absolute scopes its adapter contract declares. A capability that declares
    `requires_project = False` has no project tree, so it is scoped to the output directory the
    caller named and nothing else. Nothing is ever granted implicitly, the canonical record area is
    never a write target, and no scope comes from anything the tool itself produced.
    """
    adapter_id, cap_id = request.adapter_id, request.capability_id
    scopes = [Path(s).resolve() for s in adapter.descriptor.filesystem_scopes]
    if root is not None:
        scopes.insert(0, root)
    if request.output_dir is not None:
        workspace = Path(request.output_dir)
        if root is None and not capability.requires_project:
            workspace.mkdir(parents=True, exist_ok=True)
            scopes.insert(0, workspace.resolve())
        reason = tp.unsafe_reason([str(s) for s in scopes], str(workspace)) if scopes else \
            f"{workspace}: no permitted filesystem scope is declared"
        if reason:
            return (), None, [dg.make("UNSAFE_ARTIFACT_PATH", reason, adapter_id, cap_id)]
        if root is not None and tp.in_records(root, workspace):
            return (), None, [dg.make("UNSAFE_ARTIFACT_PATH",
                                      f"{workspace}: tool output never goes into the canonical record area "
                                      f"{tp.RECORDS_DIR}/", adapter_id, cap_id)]
    elif root is not None and not capability.artifact_kinds:
        # A capability that declares no artifacts gets no workspace: a READ_ONLY inspection must not
        # create directories in the project just by running. It works in the project root.
        return tuple(scopes), root, []
    elif root is not None:
        workspace = tp.runtime_dir(root, "tool-output", adapter_id, request.request_id)
    else:
        return (), None, [dg.make("INVALID_TOOL_REQUEST",
                                  f"{cap_id} is not project-bound, so the request must name an output directory for it "
                                  f"to work in", adapter_id, cap_id)]
    if not scopes:
        return (), None, [dg.make("UNSAFE_EXECUTION_PATH",
                                  f"{cap_id} has no permitted filesystem scope: it needs a project root or an adapter "
                                  f"contract that declares one", adapter_id, cap_id)]
    if not request.dry_run:
        workspace.mkdir(parents=True, exist_ok=True)
    return tuple(scopes), workspace, []


def _assemble(framework, registry, request, adapter, capability, context, outcome, diagnostics,
              probe, root, started_at, started, clock, inputs=(), project_id=None):
    """Turn what the adapter reported into a checked, fail-closed ToolResult."""
    adapter_id, cap_id = request.adapter_id, request.capability_id
    outcome, redacted, unsupported = _sanitize_outcome(outcome, adapter_id, cap_id)
    diagnostics = list(diagnostics) + list(outcome.diagnostics) + unsupported
    process = outcome.process
    dry_run = bool(request.dry_run)
    if redacted:
        diagnostics.append(dg.make("SECRETS_REDACTED",
                                   f"{redacted} credential-shaped value(s) were redacted from metadata this adapter "
                                   f"supplied", adapter_id, cap_id))

    if process is not None:
        if process.timed_out:
            diagnostics.append(dg.make("EXECUTION_TIMEOUT",
                                       f"{cap_id} exceeded its {context.timeout}s deadline and its process tree was "
                                       f"terminated; no partial output is treated as success", adapter_id, cap_id))
        if process.truncated:
            diagnostics.append(dg.make("PROCESS_OUTPUT_TRUNCATED",
                                       f"captured output reached the capture bound (stdout {process.stdout_bytes} B, "
                                       f"stderr {process.stderr_bytes} B); the rest was discarded, not failed",
                                       adapter_id, cap_id))
        if process.redactions:
            diagnostics.append(dg.make("SECRETS_REDACTED",
                                       f"{process.redactions} credential-shaped value(s) were redacted from captured "
                                       f"output", adapter_id, cap_id))
    timed_out = bool(process is not None and process.timed_out)
    failed = not outcome.ok and not timed_out
    if failed:
        diagnostics.append(dg.make("EXECUTION_FAILED",
                                   f"{cap_id} failed: {redact(outcome.detail)[0] or 'the tool reported failure'}",
                                   adapter_id, cap_id))
    complete = not timed_out and not failed

    claim_problems = art.declaration_problems(outcome.artifacts, capability,
                                              framework.registry["tool_artifact_kinds"], inputs)
    refused = {aid for _, aid, _ in claim_problems}
    artifacts, problems = art.collect([a for a in outcome.artifacts if a.artifact_id not in refused],
                                      context.scopes, root or context.workspace,
                                      request.request_id, capability.execution_context, complete=complete,
                                      known=inputs)
    for code, artifact_id, message in claim_problems + problems:
        diagnostics.append(dg.make(code, message, adapter_id, cap_id, artifact_id))
    for a in artifacts:
        if not a.complete:
            diagnostics.append(dg.make("ARTIFACT_INCOMPLETE",
                                       f"{a.artifact_id} was produced by an execution that did not finish; it is "
                                       f"reported, never offered as evidence", adapter_id, cap_id, a.path))

    # One foundation-observed finish time and one monotonic duration for the whole execution, used by
    # the result, the provenance and every accepted candidate. A tool-independent adapter may run with
    # no process at all, so the interval is never taken from a ProcessOutcome and never from a
    # subtraction of wall-clock timestamps.
    finished_at, duration = clock.now(), round(clock.monotonic() - started, 6)

    mutation = bool(outcome.mutation_performed) and capability.mutating and not dry_run
    if dry_run and capability.mutating:
        diagnostics.append(dg.make("MUTATION_SKIPPED_DRY_RUN",
                                   f"dry run: {cap_id} validated the plan and performed no mutation", adapter_id, cap_id))
    if outcome.mutation_performed and dry_run:
        diagnostics.append(dg.make("INVALID_TOOL_REQUEST",
                                   f"{cap_id} reported a mutation during a dry run; a dry run mutates nothing",
                                   adapter_id, cap_id))
    if outcome.mutation_performed and not capability.mutating:
        diagnostics.append(dg.make("INVALID_TOOL_REQUEST",
                                   f"{cap_id} is declared READ_ONLY but reported a mutation; the declaration, not the "
                                   f"execution, decides what a capability may do", adapter_id, cap_id))

    provenance = _provenance(framework, request, adapter, capability, outcome, probe, artifacts,
                             started_at, finished_at, duration, mutation, dry_run, process, inputs,
                             project_id)

    candidates = []
    for candidate in outcome.evidence:
        bound = _bind_candidate(candidate, request, provenance, finished_at)
        accepted, problems = ev.validate(framework, bound, capability, list(artifacts) + list(inputs), dry_run,
                                         capability.execution_context)
        diagnostics += problems
        if accepted is not None and complete:
            candidates.append(accepted)
        elif accepted is not None:
            diagnostics.append(dg.make("ARTIFACT_INCOMPLETE",
                                       f"{cap_id} did not finish, so its evidence candidate is withheld",
                                       adapter_id, cap_id))

    # The status is decided by the recorded diagnostics alone: a timeout records EXECUTION_TIMEOUT and a
    # failure records EXECUTION_FAILED above, so no parallel flag can contradict them (fail closed).
    diagnostics = dg.sort(diagnostics)
    return ToolResult(
        request_id=request.request_id, adapter_id=adapter_id, capability_id=cap_id,
        status=dg.result_status(diagnostics, default=dg.SUCCESS), started_at=started_at, finished_at=finished_at,
        duration_seconds=duration, dry_run=dry_run, mutation_performed=mutation,
        exit_code=outcome.exit_code if outcome.exit_code is not None else (process.exit_code if process else None),
        stdout=process.stdout if process else "", stderr=process.stderr if process else "",
        stdout_bytes=process.stdout_bytes if process else 0, stderr_bytes=process.stderr_bytes if process else 0,
        output_truncated=bool(process.truncated) if process else False,
        artifacts=tuple(artifacts), evidence_candidates=tuple(candidates), provenance=provenance,
        diagnostics=tuple(diagnostics), data=outcome.data, plan=tuple(outcome.plan))


def _sanitize_outcome(outcome, adapter_id, cap_id):
    """Redact every adapter-supplied surface before it can reach a caller. (outcome, redactions, problems).

    The process boundary already redacts captured output. This covers everything else an adapter
    controls — diagnostics, parsed data, the recorded command and environment, artifact descriptions,
    evidence summaries, limitations and notes — so the promise holds even for an adapter that never
    used the boundary helpers. A value a result cannot carry is dropped with a diagnostic rather than
    stringified.
    """
    total, problems = 0, []

    def clean(value, what):
        nonlocal total
        sanitized, n, problem = sanitize_or_none(value)
        total += n
        if problem is not None:
            problems.append(dg.make("UNSUPPORTED_RESULT_VALUE", f"{what}: {problem}; it was dropped",
                                    adapter_id, cap_id))
            return None
        return sanitized

    diagnostics, n = sanitize_all(outcome.diagnostics)
    total += n
    artifacts = tuple(dataclasses_replace(a, description=redact(a.description)[0] if a.description else a.description)
                      for a in outcome.artifacts)
    evidence = []
    for candidate in outcome.evidence:
        summary, a = redact(candidate.summary) if isinstance(candidate.summary, str) else (candidate.summary, 0)
        limitations, b = _redact_strings(candidate.limitations)
        notes, c = _redact_strings(candidate.notes)
        total += a + b + c
        evidence.append(dataclasses_replace(candidate, summary=summary, limitations=limitations, notes=notes))
    detail, n = redact(outcome.detail) if isinstance(outcome.detail, str) else ("", 0)
    total += n
    plan, n = _redact_strings(outcome.plan)
    total += n
    return dataclasses_replace(
        outcome, diagnostics=tuple(diagnostics), artifacts=artifacts, evidence=tuple(evidence),
        detail=detail, plan=plan,
        data=clean(outcome.data, "adapter result data"),
        command=clean(outcome.command, "recorded command"),
        environment=clean(outcome.environment, "recorded environment")), total, problems


def _redact_strings(values):
    out, total = [], 0
    for value in values or ():
        if isinstance(value, str):
            text, n = redact(value)
            out.append(text)
            total += n
        else:
            out.append(value)
    return tuple(out), total


def _bind_candidate(candidate, request, provenance, generated_at):
    """Bind a candidate to the request's subject, this execution's provenance and the foundation's own
    clock. An adapter cannot re-point a candidate at another subject, supply its own provenance, or
    establish evidence freshness: `generated_at` is the foundation-observed execution time, because
    freshness is exactly the kind of claim a tool must not be trusted to make about itself."""
    return dataclasses_replace(
        candidate,
        subject_kind=request.subject.kind, subject_ref=request.subject.ref,
        subject_revision=request.subject.revision,
        source_adapter=request.adapter_id, source_capability=request.capability_id,
        generated_at=generated_at, provenance=provenance.to_dict(), materializable=False)


def _provenance(framework, request, adapter, capability, outcome, probe, artifacts,
                started_at, finished_at, duration, mutation, dry_run, process, inputs=(), project_id=None):
    from . import provenance as prov
    return prov.ToolProvenance(
        gpos_version=framework.version, adapter_id=adapter.descriptor.adapter_id,
        adapter_version=adapter.descriptor.adapter_version, capability_id=capability.id,
        request_id=request.request_id, execution_context=capability.execution_context,
        started_at=started_at, finished_at=finished_at, duration_seconds=duration,
        dry_run=dry_run, mutation_performed=mutation,
        subject_kind=request.subject.kind, subject_ref=request.subject.ref,
        tool_name=adapter.descriptor.target_tool,
        tool_version=probe.tool_version if probe else None,
        tool_path=probe.tool_path if probe else None,
        platform=model.current_platform(), project_id=project_id, routing_ref=request.routing_ref,
        actor=request.actor.to_dict() if request.actor else None,
        command=outcome.command, environment=outcome.environment,
        input_artifacts=tuple(sorted((a.artifact_id, a.sha256) for a in inputs)),
        output_artifacts=tuple((a.artifact_id, a.sha256) for a in artifacts),
        output_truncated=bool(process.truncated) if process else False,
        subject_revision=request.subject.revision, build_revision=request.build_revision,
        build_id=request.build_id, target_platform=request.target_platform, device=request.device)
