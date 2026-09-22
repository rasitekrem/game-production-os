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
from .redaction import redact


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
        for name in ("output_dir", "routing_ref", "build_revision", "build_id", "target_platform", "device"):
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

    record_set, problems = project_problems(framework, request, capability)
    if problems:
        return finish(dg.INVALID_REQUEST, problems)

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
        diagnostics.append(dg.make("LEASE_ACQUIRED",
                                   f"single-writer lease held on {capability.resource_kind}:"
                                   f"{_resource_id(request, capability)}",
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
        return _release_and_finish(root, lease, finish, dg.INVALID_REQUEST, diagnostics + [
            dg.make(exc.code if exc.code in dg.CODES else "UNSAFE_PROCESS_SPEC", str(exc),
                    request.adapter_id, request.capability_id)])
    except Exception as exc:
        return _release_and_finish(root, lease, finish, dg.INTERNAL_ERROR, diagnostics + [
            dg.make("ADAPTER_INTERNAL_ERROR",
                    f"{request.adapter_id}.execute() raised {type(exc).__name__}: {redact(str(exc))[0]}",
                    request.adapter_id, request.capability_id)])

    result = _assemble(framework, registry, request, adapter, capability, context, outcome,
                       diagnostics, probe, root, started_at, started, clock, inputs)
    _release(root, lease)
    return result


def _resource_id(request, capability):
    return request.inputs.get("resource") or request.project_root or request.subject.ref


def _acquire(root, adapter, capability, request, now):
    if root is None:
        return None, [dg.make("INVALID_TOOL_REQUEST",
                              f"{capability.id} needs a single-writer lease, which lives in the project runtime area, "
                              f"so it needs project_root", request.adapter_id, request.capability_id)]
    resource = f"{capability.resource_kind}:{_resource_id(request, capability)}"
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


def _release(root, lease):
    if lease is not None and root is not None:
        lease_mod.release(root, lease)


def _release_and_finish(root, lease, finish, status, diagnostics):
    _release(root, lease)
    return finish(status, diagnostics)


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
              probe, root, started_at, started, clock, inputs=()):
    """Turn what the adapter reported into a checked, fail-closed ToolResult."""
    adapter_id, cap_id = request.adapter_id, request.capability_id
    diagnostics = list(diagnostics) + list(outcome.diagnostics)
    process = outcome.process
    dry_run = bool(request.dry_run)

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

    artifacts, problems = art.collect(outcome.artifacts, context.scopes, root or context.workspace,
                                      request.request_id, capability.execution_context, complete=complete,
                                      known=inputs)
    for code, artifact_id, message in problems:
        diagnostics.append(dg.make(code, message, adapter_id, cap_id, artifact_id))
    for a in artifacts:
        if not a.complete:
            diagnostics.append(dg.make("ARTIFACT_INCOMPLETE",
                                       f"{a.artifact_id} was produced by an execution that did not finish; it is "
                                       f"reported, never offered as evidence", adapter_id, cap_id, a.path))

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

    provenance = _provenance(framework, request, adapter, capability, context, outcome, probe,
                             artifacts, started_at, clock, mutation, dry_run, process, inputs)

    candidates = []
    for candidate in outcome.evidence:
        bound = _bind_candidate(candidate, request, provenance)
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
        status=dg.result_status(diagnostics, default=dg.SUCCESS), started_at=started_at, finished_at=clock.now(),
        duration_seconds=round(clock.monotonic() - started, 6), dry_run=dry_run, mutation_performed=mutation,
        exit_code=outcome.exit_code if outcome.exit_code is not None else (process.exit_code if process else None),
        stdout=process.stdout if process else "", stderr=process.stderr if process else "",
        stdout_bytes=process.stdout_bytes if process else 0, stderr_bytes=process.stderr_bytes if process else 0,
        output_truncated=bool(process.truncated) if process else False,
        artifacts=tuple(artifacts), evidence_candidates=tuple(candidates), provenance=provenance,
        diagnostics=tuple(diagnostics), data=outcome.data, plan=tuple(outcome.plan))


def _bind_candidate(candidate, request, provenance):
    """Bind a candidate to the request's subject and this execution's provenance. An adapter cannot
    re-point a candidate at a different subject, and it cannot supply its own provenance."""
    return dataclasses_replace(
        candidate,
        subject_kind=request.subject.kind, subject_ref=request.subject.ref,
        subject_revision=request.subject.revision,
        source_adapter=request.adapter_id, source_capability=request.capability_id,
        provenance=provenance.to_dict(), materializable=False)


def _provenance(framework, request, adapter, capability, context, outcome, probe, artifacts,
                started_at, clock, mutation, dry_run, process, inputs=()):
    from . import provenance as prov
    project_id = None
    if request.project_root:
        try:
            import json
            config = json.loads((Path(request.project_root) / ".game" / "gpos" / "project-config.json")
                                .read_text(encoding="utf-8"))
            project_id = config.get("project_id") or config.get("id")
        except (OSError, ValueError):
            project_id = None
    return prov.ToolProvenance(
        gpos_version=framework.version, adapter_id=adapter.descriptor.adapter_id,
        adapter_version=adapter.descriptor.adapter_version, capability_id=capability.id,
        request_id=request.request_id, execution_context=capability.execution_context,
        started_at=started_at, finished_at=clock.now(),
        duration_seconds=round(process.duration_seconds if process else 0.0, 6),
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
