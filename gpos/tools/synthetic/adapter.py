"""TEST_ONLY synthetic reference adapter.

It drives no production tool. It exists so that every behaviour of the tool adapter foundation —
probe states, read-only and mutating execution, stateful single-writer leases, dry run, artifacts
and hashing, derivation, provenance, evidence candidates, timeouts, output truncation, secret
redaction and process safety — can be exercised without any real production tool being installed,
and without any network.

It is marked `TEST_ONLY` in both its `adapter_kind` and its `tool_family`, so the production
registry refuses to register it: `default_registry()` will not accept it, and only a registry
explicitly constructed with `allow_test_only=True` can. It is never an enabled production adapter.

Its "tool" is a small Python helper in this package, executed through the ordinary process
boundary as an executable plus an argument vector.
"""

from pathlib import Path

from .. import model
from .. import process as proc
from ..artifacts import ArtifactSpec
from ..capabilities import Capability, TimeoutPolicy
from ..evidence import EvidenceCandidate
from ..execution import AdapterOutcome

HELPER = Path(__file__).resolve().parent / "helper.py"
ADAPTER_ID = "synthetic"
ADAPTER_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"

INSPECT = f"{ADAPTER_ID}.inspect"
TRANSFORM = f"{ADAPTER_ID}.transform"
DERIVE = f"{ADAPTER_ID}.derive"
STATEFUL_WRITE = f"{ADAPTER_ID}.stateful-write"
FAIL = f"{ADAPTER_ID}.fail"
TIMEOUT = f"{ADAPTER_ID}.timeout"
NOISY = f"{ADAPTER_ID}.noisy"
LEAK = f"{ADAPTER_ID}.leak"
CLAIM_HUMAN = f"{ADAPTER_ID}.claim-human-evidence"
CLAIM_RUNTIME = f"{ADAPTER_ID}.claim-runtime"
ESCAPE = f"{ADAPTER_ID}.escape"
PARTIAL = f"{ADAPTER_ID}.partial"
RESOURCE_WRITE = f"{ADAPTER_ID}.resource-write"

CAPABILITIES = (
    Capability(
        id=INSPECT, category="INSPECT",
        description="TEST_ONLY: read something about the project and report it. Changes nothing.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        dry_run_supported=True, input_kinds=("target",),
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=TRANSFORM, category="TRANSFORM",
        description="TEST_ONLY: write a small text artifact into the execution workspace.",
        operation_class="MUTATING", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        dry_run_supported=True, input_kinds=("text", "name"), artifact_kinds=("TEXT",),
        potential_evidence=(("CODE_EVIDENCE", "OFFLINE_ANALYSIS"),),
        side_effect_scope="one file inside the execution workspace",
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=DERIVE, category="EXPORT",
        description="TEST_ONLY: derive a still artifact from an artifact captured elsewhere, keeping the "
                    "origin's capture context.",
        operation_class="MUTATING", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        dry_run_supported=True, input_kinds=("claim_context",), artifact_kinds=("TEXT", "IMAGE"),
        potential_evidence=(("VISUAL_EVIDENCE", "TARGET_RUNTIME"), ("VISUAL_EVIDENCE", "DCC_RENDER"),
                            ("CODE_EVIDENCE", "OFFLINE_ANALYSIS")),
        side_effect_scope="derived files inside the execution workspace",
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=STATEFUL_WRITE, category="BUILD",
        description="TEST_ONLY: stand in for a long-lived editor session that only one writer may drive.",
        operation_class="MUTATING", state_model="STATEFUL", execution_context="EDITOR",
        single_writer_required=True, resource_kind="SYNTHETIC_SESSION", dry_run_supported=True,
        input_kinds=("text",), artifact_kinds=("TEXT",),
        side_effect_scope="the synthetic session state and one file in the workspace",
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=RESOURCE_WRITE, category="DEPLOY",
        description="TEST_ONLY: stand in for a stateful target the request names, such as a device.",
        operation_class="MUTATING", state_model="STATEFUL", execution_context="TARGET_RUNTIME",
        single_writer_required=True, resource_kind="SYNTHETIC_TARGET", resource_from_request=True,
        input_kinds=("text",), artifact_kinds=("TEXT",),
        side_effect_scope="the named synthetic target and one file in the workspace",
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=FAIL, category="VALIDATE", description="TEST_ONLY: fail deterministically.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_project=False,
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=TIMEOUT, category="RUN", description="TEST_ONLY: outlive its deadline so termination can be observed.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_project=False, input_kinds=("seconds",), timeout=TimeoutPolicy(default=0.2, maximum=30.0)),
    Capability(
        id=NOISY, category="RUN", description="TEST_ONLY: print more than the capture bound.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_project=False, input_kinds=("kilobytes",), timeout=TimeoutPolicy(default=30.0, maximum=60.0)),
    Capability(
        id=LEAK, category="RUN", description="TEST_ONLY: print credential-shaped values so redaction can be observed.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_project=False,
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=CLAIM_HUMAN, category="CAPTURE",
        description="TEST_ONLY: attempt to emit HUMAN_EVIDENCE, which the foundation must refuse.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        artifact_kinds=("TEXT",), potential_evidence=(("CODE_EVIDENCE", "OFFLINE_ANALYSIS"),),
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=CLAIM_RUNTIME, category="CAPTURE",
        description="TEST_ONLY: attempt to claim a capture context this execution did not observe.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        dry_run_supported=True, artifact_kinds=("TEXT",),
        input_kinds=("evidence_type", "capture_context"),
        potential_evidence=(("CODE_EVIDENCE", "OFFLINE_ANALYSIS"), ("RUNTIME_EVIDENCE", "TARGET_RUNTIME"),
                            ("PERFORMANCE_EVIDENCE", "OFFLINE_ANALYSIS")),
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=PARTIAL, category="CAPTURE",
        description="TEST_ONLY: fail after producing output, while offering a candidate about an input artifact.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        artifact_kinds=("TEXT",),
        potential_evidence=(("VISUAL_EVIDENCE", "TARGET_RUNTIME"), ("CODE_EVIDENCE", "OFFLINE_ANALYSIS")),
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
    Capability(
        id=ESCAPE, category="TRANSFORM",
        description="TEST_ONLY: attempt to write and declare an artifact outside the permitted scope.",
        operation_class="MUTATING", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        input_kinds=("path", "cwd"), artifact_kinds=("TEXT",),
        side_effect_scope="attempts a write outside the workspace; the foundation must refuse it",
        timeout=TimeoutPolicy(default=10.0, maximum=30.0)),
)

DESCRIPTOR = model.AdapterDescriptor(
    adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION, tool_family="TEST_ONLY",
    target_tool="gpos-synthetic-helper", adapter_kind="TEST_ONLY", state_model="STATELESS",
    supported_platforms=("MACOS", "LINUX", "WINDOWS"), capabilities=CAPABILITIES,
    availability="a Python interpreter and the helper program shipped with this package",
    minimum_tool_version="1.0.0",
    compatibility_notes=("TEST_ONLY: this adapter exists to exercise the tool adapter foundation and is never "
                         "registered as a production adapter.",))


class SyntheticAdapter(model.ToolAdapter):
    """TEST_ONLY reference adapter. `mode` selects the probe state it reports."""

    descriptor = DESCRIPTOR

    def __init__(self, mode=model.AVAILABLE, helper=None, tool_version=TOOL_VERSION):
        self.mode = mode
        self.helper = Path(helper) if helper else HELPER
        self.tool_version = tool_version
        self.probe_calls = 0

    # ------------------------------------------------------------ probe

    def probe(self):
        self.probe_calls += 1
        platform = model.current_platform()
        available = [(c.id, True, "") for c in CAPABILITIES]
        if self.mode == model.UNAVAILABLE or not self.helper.is_file():
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail=f"the synthetic helper is not present at {self.helper}",
                                     capability_availability=tuple((c.id, False, "tool unavailable")
                                                                   for c in CAPABILITIES))
        if self.mode == model.VERSION_UNSUPPORTED:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=str(self.helper),
                                     tool_version="0.1.0", platform=platform,
                                     detail=f"helper 0.1.0 is older than the required "
                                            f"{DESCRIPTOR.minimum_tool_version}",
                                     capability_availability=tuple((c.id, False, "tool version unsupported")
                                                                   for c in CAPABILITIES))
        return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=str(self.helper),
                                 tool_version=self.tool_version, platform=platform,
                                 detail="synthetic helper available",
                                 capability_availability=tuple(available))

    # ------------------------------------------------------------ execution

    def _spec(self, context, argv, cwd=None, timeout=None):
        return proc.ToolProcessSpec(executable=proc.interpreter_path(),
                                    argv=(str(self.helper),) + tuple(argv),
                                    cwd=cwd or context.workspace,
                                    timeout=timeout if timeout is not None else context.timeout)

    def execute(self, request, context):
        cap = request.capability_id
        inputs = request.inputs or {}
        if cap == INSPECT:
            return self._inspect(context, inputs)
        if cap in (TRANSFORM, STATEFUL_WRITE, RESOURCE_WRITE):
            return self._write(context, inputs, cap)
        if cap == DERIVE:
            return self._derive(context, inputs)
        if cap == FAIL:
            return self._simple(context, ("fail",), ok=False, detail="the synthetic tool exited 3")
        if cap == TIMEOUT:
            return self._simple(context, ("sleep", str(inputs.get("seconds", 30))))
        if cap == NOISY:
            return self._simple(context, ("noisy", str(inputs.get("kilobytes", 1024))))
        if cap == LEAK:
            return self._simple(context, ("leak",))
        if cap == CLAIM_HUMAN:
            return self._claim(context, "HUMAN_EVIDENCE", "HUMAN_RECORD")
        if cap == CLAIM_RUNTIME:
            return self._claim(context, inputs.get("evidence_type", "RUNTIME_EVIDENCE"),
                               inputs.get("capture_context", "TARGET_RUNTIME"))
        if cap == PARTIAL:
            return self._partial(context)
        if cap == ESCAPE:
            return self._escape(context, inputs)
        raise AssertionError(f"{cap} is declared but not implemented")  # a defect, reported as INTERNAL_ERROR

    # ------------------------------------------------------------ capability bodies

    def _simple(self, context, argv, ok=True, detail=""):
        outcome = context.run(self._spec(context, argv))
        spec = self._spec(context, argv)
        return AdapterOutcome(ok=ok and outcome.exit_code == 0 and not outcome.timed_out,
                              exit_code=outcome.exit_code, process=outcome, detail=detail,
                              command=spec.command_for_provenance(), environment=spec.env.metadata())

    def _inspect(self, context, inputs):
        target = str(inputs.get("target", context.project_root or "project"))
        if context.dry_run:
            return AdapterOutcome(plan=(f"would inspect {target}",), detail="dry run: nothing was read")
        spec = self._spec(context, ("inspect", target))
        outcome = context.run(spec)
        return AdapterOutcome(ok=outcome.exit_code == 0, exit_code=outcome.exit_code, process=outcome,
                              command=spec.command_for_provenance(), environment=spec.env.metadata(),
                              data={"target": target, "reported": outcome.stdout.strip()})

    def _write(self, context, inputs, cap):
        name = str(inputs.get("name", "synthetic.txt"))
        text = str(inputs.get("text", "synthetic artifact"))
        path = context.artifact_path(name)
        if context.dry_run:
            return AdapterOutcome(plan=(f"would write {path}", f"would not touch anything outside {context.workspace}"),
                                  detail="dry run: no file was written")
        spec = self._spec(context, ("write", str(path), text))
        outcome = context.run(spec)
        ok = outcome.exit_code == 0 and not outcome.timed_out
        artifacts = (ArtifactSpec(artifact_id="output", kind="TEXT", path=str(path), media_type="text/plain",
                                  description="TEST_ONLY synthetic output"),) if ok else ()
        evidence = ()
        if ok and cap == TRANSFORM:
            evidence = (EvidenceCandidate(
                evidence_type="CODE_EVIDENCE", capture_context="OFFLINE_ANALYSIS",
                summary="TEST_ONLY synthetic transform output",
                subject_kind="TASK", subject_ref="", source_adapter=ADAPTER_ID, source_capability=cap,
                generated_at=context.clock.now(), artifact_ids=("output",),
                limitations=("produced by the TEST_ONLY synthetic adapter; not production evidence",)),)
        return AdapterOutcome(ok=ok, exit_code=outcome.exit_code, process=outcome, artifacts=artifacts,
                              evidence=evidence, mutation_performed=ok,
                              command=spec.command_for_provenance(), environment=spec.env.metadata())

    def _derive(self, context, inputs):
        """Derive a still from an artifact the caller supplied, whose capture context the caller
        vouched for. The claim the candidate makes comes from the request, so the tests can show
        that a transform cannot upgrade its source."""
        source = next(iter(context.input_artifacts), None)
        derived_path = context.artifact_path("derived.txt")
        if source is None:
            return AdapterOutcome(ok=False, detail="no input artifact was supplied to derive from")
        if context.dry_run:
            return AdapterOutcome(plan=(f"would derive {derived_path} from {source.path}",),
                                  detail="dry run: nothing was derived")
        spec = self._spec(context, ("write", str(derived_path), f"derived from {source.artifact_id}"))
        outcome = context.run(spec)
        ok = outcome.exit_code == 0 and not outcome.timed_out
        claim = str(inputs.get("claim_context") or source.origin_capture_context)
        return AdapterOutcome(
            ok=ok, exit_code=outcome.exit_code, process=outcome, mutation_performed=ok,
            artifacts=(ArtifactSpec("derived", "IMAGE", str(derived_path), media_type="text/plain",
                                    description="TEST_ONLY still derived from a capture made elsewhere",
                                    derived_from=(source.artifact_id,)),) if ok else (),
            evidence=(EvidenceCandidate(
                evidence_type="VISUAL_EVIDENCE", capture_context=claim,
                summary="TEST_ONLY still derived from a capture made elsewhere",
                subject_kind="TASK", subject_ref="", source_adapter=ADAPTER_ID, source_capability=DERIVE,
                generated_at=context.clock.now(), artifact_ids=("derived",),
                derived_from=("derived",),
                limitations=("derived by the TEST_ONLY synthetic adapter",)),) if ok else (),
            command=spec.command_for_provenance(), environment=spec.env.metadata(),
            data={"source_capture_context": source.origin_capture_context, "claimed_capture_context": claim})

    def _claim(self, context, evidence_type, capture_context):
        path = context.artifact_path("claim.txt")
        if context.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
        spec = self._spec(context, ("write", str(path), "claim"))
        outcome = context.run(spec)
        return AdapterOutcome(
            ok=outcome.exit_code == 0, exit_code=outcome.exit_code, process=outcome,
            artifacts=(ArtifactSpec("claim", "TEXT", str(path), media_type="text/plain"),),
            evidence=(EvidenceCandidate(
                evidence_type=evidence_type, capture_context=capture_context,
                summary="TEST_ONLY claim the foundation must judge",
                subject_kind="TASK", subject_ref="", source_adapter=ADAPTER_ID,
                source_capability=context.capability.id, generated_at=context.clock.now(),
                artifact_ids=("claim",)),),
            command=spec.command_for_provenance(), environment=spec.env.metadata())

    def _partial(self, context):
        """Fail, but still declare an artifact and offer a candidate about a caller-supplied input.

        The input artifact is complete on its own, so only the foundation's rule that an unfinished
        execution offers no evidence can withhold the candidate.
        """
        source = next(iter(context.input_artifacts), None)
        path = context.artifact_path("partial.txt")
        spec = self._spec(context, ("write", str(path), "partial"))
        context.run(spec)
        return AdapterOutcome(
            ok=False, exit_code=1, detail="the synthetic tool stopped half way",
            artifacts=(ArtifactSpec("partial", "TEXT", str(path), media_type="text/plain"),),
            evidence=(EvidenceCandidate(
                evidence_type="VISUAL_EVIDENCE", capture_context=source.origin_capture_context,
                summary="TEST_ONLY candidate about an input artifact from a failed run",
                subject_kind="TASK", subject_ref="", source_adapter=ADAPTER_ID, source_capability=PARTIAL,
                generated_at=context.clock.now(), artifact_ids=(source.artifact_id,),
                derived_from=(source.artifact_id,)),) if source is not None else (),
            command=spec.command_for_provenance(), environment=spec.env.metadata())

    def _escape(self, context, inputs):
        """Declare an artifact (and optionally a working directory) outside the permitted scope."""
        base = Path(context.project_root or context.workspace)
        target = inputs.get("path") or str(base.parent / "escaped-outside-the-project.txt")
        cwd = inputs.get("cwd")
        spec = self._spec(context, ("write", str(target), "escaped"), cwd=cwd)
        outcome = context.run(spec)  # a bad cwd raises ProcessSpecError before anything runs
        return AdapterOutcome(ok=outcome.exit_code == 0, exit_code=outcome.exit_code, process=outcome,
                              mutation_performed=outcome.exit_code == 0,
                              artifacts=(ArtifactSpec("escaped", "TEXT", str(target)),),
                              command=spec.command_for_provenance(), environment=spec.env.metadata())


def register_into(registry, **kwargs):
    """Register the synthetic adapter into a registry that allows test-only adapters."""
    return registry.register(SyntheticAdapter(**kwargs))
