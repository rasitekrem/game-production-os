#!/usr/bin/env python3
"""Phase 2C-0 — tool adapter foundation tests.

    python3 tests/test_tool_foundation.py

Standard library only; no network; no real production tool is required or invoked. Everything runs
against the TEST_ONLY synthetic reference adapter inside temporary directories, with HOME redirected
so that any accidental write to a user or global directory lands there and fails the boundary group.

Groups: A adapter identity and registry · B capabilities · C probe and lifecycle · D execution ·
E process safety · F leases · G artifacts · H provenance · I evidence · J project preconditions ·
K determinism · L regression and boundaries · M CLI.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_HOME = tempfile.mkdtemp(prefix="gpos-tool-home-")
os.environ["HOME"] = _HOME  # any accidental write to a user/global directory lands here and fails L01

from gpos.framework import load_framework  # noqa: E402
from gpos.tools import artifacts as art  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import evidence as ev  # noqa: E402
from gpos.tools import leases as lease_mod  # noqa: E402
from gpos.tools import model as tmodel  # noqa: E402
from gpos.tools import paths as tpaths  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools import validation as tval  # noqa: E402
from gpos.tools.capabilities import Capability, TimeoutPolicy  # noqa: E402
from gpos.tools.errors import AdapterRegistrationError  # noqa: E402
from gpos.tools.execution import AdapterOutcome, Clock, ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Actor, AdapterDescriptor, InputArtifact, ProbeResult, Subject, ToolAdapter  # noqa: E402
from gpos.tools.redaction import redact  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.synthetic import SyntheticAdapter  # noqa: E402
from gpos.tools.synthetic import adapter as syn  # noqa: E402

FW = load_framework()
REG = FW.registry
POLICY = REG["tool_adapter_policy"]
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"


def registry(mode=tmodel.AVAILABLE, **kwargs):
    r = ToolRegistry(FW, allow_test_only=True)
    r.register(SyntheticAdapter(mode=mode, **kwargs))
    return r


def capability(**kwargs):
    base = dict(id="synthetic.example", category="INSPECT", description="a capability",
                operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS")
    base.update(kwargs)
    return Capability(**base)


def descriptor(**kwargs):
    base = dict(adapter_id="synthetic", adapter_version="1.0.0", tool_family="TEST_ONLY",
                target_tool="gpos-synthetic-helper", adapter_kind="TEST_ONLY", state_model="STATELESS",
                supported_platforms=("MACOS", "LINUX", "WINDOWS"), capabilities=(capability(),))
    base.update(kwargs)
    return AdapterDescriptor(**base)


class Fake(ToolAdapter):
    """A minimal adapter used to exercise registration; it never runs anything."""

    def __init__(self, desc):
        self.descriptor = desc

    def probe(self):
        return ProbeResult(self.descriptor.adapter_id, tmodel.AVAILABLE)

    def execute(self, request, context):
        return AdapterOutcome()


class Misreporting(ToolAdapter):
    """An adapter that claims mutations its declarations do not allow. The foundation must refuse
    both claims: the declaration decides what a capability may do, not the execution."""

    descriptor = descriptor(capabilities=(
        capability(requires_project=False, requires_tool=False),
        capability(id="synthetic.writer", operation_class="MUTATING", state_model="STATELESS",
                   dry_run_supported=True, side_effect_scope="claims to write something",
                   requires_project=False, requires_tool=False)))

    def probe(self):
        return ProbeResult("synthetic", tmodel.AVAILABLE)

    def execute(self, request, context):
        return AdapterOutcome(ok=True, exit_code=0, mutation_performed=True)


def misreporting():
    r = ToolRegistry(FW, allow_test_only=True)
    r.register(Misreporting())
    return r


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-tools-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def project(self, name="p"):
        target = self.tmp / name
        shutil.copytree(FIXTURE, target)
        return target

    def out(self, name="out"):
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run_cap(self, cap, reg=None, project=None, **kwargs):
        reg = reg or registry()
        kwargs.setdefault("output_dir", None if project else str(self.out()))
        revision = kwargs.pop("revision", None)
        request = ExecutionRequest(adapter_id="synthetic", capability_id=cap,
                                   subject=Subject(kwargs.pop("subject_kind", "TASK"),
                                                   kwargs.pop("subject_ref", "TASK-1"), revision),
                                   project_root=str(project) if project else None, **kwargs)
        return execute(reg, request)

    def codes(self, result):
        return {d.code for d in result.diagnostics}


# ---------------------------------------------------------------- A  identity and registry

class A01_Registry(TmpCase):
    def test_production_registry_is_empty_and_refuses_test_only(self):
        r = default_registry(FW)
        self.assertEqual(r.adapter_ids(), [])
        self.assertFalse(r.allow_test_only)
        with self.assertRaises(AdapterRegistrationError) as cm:
            r.register(SyntheticAdapter())
        self.assertIn("TEST_ONLY", str(cm.exception))
        self.assertEqual(r.adapter_ids(), [])

    def test_register_valid_adapter(self):
        r = registry()
        self.assertEqual(r.adapter_ids(), ["synthetic"])
        self.assertIn("synthetic", r)
        self.assertEqual(r.state("synthetic").state, tmodel.REGISTERED)
        self.assertEqual(r.get("synthetic").descriptor.adapter_id, "synthetic")

    def test_duplicate_adapter_id_rejected(self):
        r = registry()
        with self.assertRaises(AdapterRegistrationError) as cm:
            r.register(SyntheticAdapter())
        self.assertIn("already registered", str(cm.exception))
        self.assertEqual(r.adapter_ids(), ["synthetic"])

    def test_duplicate_capability_id_rejected(self):
        cap = capability()
        problems = tval.validate_descriptor(FW, descriptor(capabilities=(cap, cap)), allow_test_only=True)
        self.assertIn("duplicate capability id", " ".join(p.message for p in problems))

    def test_malformed_descriptor_rejected(self):
        for kwargs, expected in ((dict(adapter_id="Synthetic"), "adapter_id"),
                                 (dict(adapter_version="one"), "adapter_version"),
                                 (dict(tool_family="MAGIC"), "tool_family"),
                                 (dict(adapter_kind="MAGIC"), "adapter_kind"),
                                 (dict(state_model="MAYBE"), "state_model"),
                                 (dict(supported_platforms=()), "supported_platforms"),
                                 (dict(supported_platforms=("AMIGA",)), "supported platform"),
                                 (dict(target_tool=""), "target_tool"),
                                 (dict(capabilities=()), "at least one capability"),
                                 (dict(network="ALLOWED"), "network")):
            problems = tval.validate_descriptor(FW, descriptor(**kwargs), allow_test_only=True)
            self.assertTrue(problems, kwargs)
            self.assertIn(expected, " ".join(p.message for p in problems), kwargs)
            self.assertEqual({p.code for p in problems}, {"ADAPTER_REGISTRATION_INVALID"})

    def test_missing_probe_or_execute_rejected(self):
        for name in ("probe", "execute"):
            cls = type("Partial", (ToolAdapter,), {"descriptor": descriptor(),
                                                   **{n: (lambda self, *a: None) for n in ("probe", "execute")
                                                      if n != name}})
            with self.assertRaises(AdapterRegistrationError) as cm:
                ToolRegistry(FW, allow_test_only=True).register(cls())
            self.assertIn(f"{name}() is not implemented", str(cm.exception))

    def test_listing_is_deterministic(self):
        r = ToolRegistry(FW, allow_test_only=True)
        r.register(SyntheticAdapter())
        r.register(Fake(descriptor(adapter_id="another", capabilities=(capability(id="another.example"),))))
        self.assertEqual(r.adapter_ids(), ["another", "synthetic"])
        self.assertEqual([d.adapter_id for d in r.list_adapters()], ["another", "synthetic"])
        ids = [c.id for _, c in r.list_capabilities()]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(r.to_dict(), r.to_dict())

    def test_descriptor_is_deterministic_and_serializable(self):
        first, second = SyntheticAdapter().descriptor.to_dict(), SyntheticAdapter().descriptor.to_dict()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertTrue(first["test_only"])


# ---------------------------------------------------------------- B  capabilities

class B01_Capabilities(TmpCase):
    def declared(self, cap_id):
        return SyntheticAdapter().capability(cap_id)

    def test_read_only_and_mutating(self):
        self.assertFalse(self.declared(syn.INSPECT).mutating)
        self.assertTrue(self.declared(syn.TRANSFORM).mutating)
        self.assertEqual(self.declared(syn.INSPECT).side_effect_scope, "NONE")
        self.assertTrue(self.declared(syn.TRANSFORM).side_effect_scope)

    def test_stateless_and_stateful(self):
        self.assertFalse(self.declared(syn.TRANSFORM).stateful)
        stateful = self.declared(syn.STATEFUL_WRITE)
        self.assertTrue(stateful.stateful and stateful.mutating and stateful.single_writer_required)
        self.assertTrue(stateful.resource_kind)

    def test_stateful_mutating_must_require_single_writer(self):
        cap = capability(id="synthetic.editor", operation_class="MUTATING", state_model="STATEFUL",
                         single_writer_required=False, side_effect_scope="an editor session")
        problems = tval.validate_capability(FW, "synthetic", cap)
        self.assertIn("must declare single_writer_required", " ".join(p.message for p in problems))
        self.assertEqual(POLICY["single_writer_operation_class"], "MUTATING")
        self.assertEqual(POLICY["single_writer_state_model"], "STATEFUL")

    def test_contradictory_declarations_rejected(self):
        cases = (
            (dict(operation_class="MUTATING", state_model="STATELESS", side_effect_scope="NONE"), "side_effect_scope"),
            (dict(side_effect_scope="writes files"), "must not declare a side-effect scope"),
            (dict(single_writer_required=True, resource_kind="X"), "READ_ONLY and must not take a writer lease"),
            (dict(operation_class="MUTATING", state_model="STATEFUL", single_writer_required=True,
                  side_effect_scope="x"), "names no resource_kind"),
            (dict(category="MAGIC"), "category"),
            (dict(operation_class="SORT_OF"), "operation_class"),
            (dict(state_model="SORT_OF"), "state_model"),
            (dict(execution_context="SOMEWHERE"), "execution_context"),
            (dict(artifact_kinds=("HOLOGRAM",)), "artifact kind"),
            (dict(description=""), "must describe"),
            (dict(id="Synthetic.Example"), "lower-case identifier"),
            (dict(id="other.example"), "must be scoped to its adapter"),
            (dict(timeout=TimeoutPolicy(default=90.0, maximum=30.0)), "timeout policy"),
        )
        for kwargs, expected in cases:
            problems = tval.validate_capability(FW, "synthetic", capability(**kwargs))
            self.assertTrue(problems, kwargs)
            self.assertIn(expected, " ".join(p.message for p in problems), kwargs)

    def test_declared_evidence_pairs_are_registry_checked(self):
        bad = capability(potential_evidence=(("MOTION_EVIDENCE", "OFFLINE_ANALYSIS"),))
        self.assertIn("which GPOS does not allow",
                      " ".join(p.message for p in tval.validate_capability(FW, "synthetic", bad)))
        human = capability(potential_evidence=(("HUMAN_EVIDENCE", "HUMAN_RECORD"),))
        self.assertIn("only a human record can be",
                      " ".join(p.message for p in tval.validate_capability(FW, "synthetic", human)))
        good = capability(potential_evidence=(("VISUAL_EVIDENCE", "DCC_RENDER"),))
        self.assertEqual(tval.validate_capability(FW, "synthetic", good), [])

    def test_every_synthetic_capability_is_valid(self):
        self.assertEqual(tval.validate_descriptor(FW, SyntheticAdapter().descriptor, allow_test_only=True), [])

    def test_timeout_policy_bounds(self):
        policy = TimeoutPolicy(default=10.0, maximum=30.0)
        self.assertEqual(policy.resolve(None)[0], 10.0)
        self.assertEqual(policy.resolve(5)[0], 5.0)
        self.assertIsNone(policy.resolve(60)[0])
        self.assertIn("exceeds the capability maximum", policy.resolve(60)[1])
        for bad in (0, -1, "10", True):
            self.assertIsNone(policy.resolve(bad)[0], bad)


# ---------------------------------------------------------------- C  probe and lifecycle

class C01_Probe(TmpCase):
    def test_available(self):
        r = registry()
        result = r.probe("synthetic")
        self.assertEqual(result.status, tmodel.AVAILABLE)
        self.assertTrue(result.available)
        self.assertEqual(result.state, tmodel.READY)
        self.assertEqual(r.state("synthetic").state, tmodel.READY)
        self.assertTrue(result.tool_path and result.tool_version)
        self.assertEqual(r.ready("synthetic")[1], [])

    def test_unavailable(self):
        r = registry(mode=tmodel.UNAVAILABLE)
        result = r.probe("synthetic")
        self.assertEqual((result.status, result.state), (tmodel.UNAVAILABLE, tmodel.UNAVAILABLE))
        self.assertEqual([d.code for d in r.ready("synthetic")[1]], ["TOOL_NOT_FOUND"])
        self.assertTrue(all(not a for _, a, _ in result.capability_availability))

    def test_version_unsupported(self):
        r = registry(mode=tmodel.VERSION_UNSUPPORTED)
        result = r.probe("synthetic")
        self.assertEqual((result.status, result.state), (tmodel.VERSION_UNSUPPORTED, tmodel.INCOMPATIBLE))
        self.assertEqual([d.code for d in r.ready("synthetic")[1]], ["TOOL_VERSION_UNSUPPORTED"])

    def test_probe_is_structured_and_never_raises(self):
        class Exploding(ToolAdapter):
            descriptor = descriptor()

            def probe(self):
                raise RuntimeError("the tool blew up")

            def execute(self, request, context):
                return AdapterOutcome()

        r = ToolRegistry(FW, allow_test_only=True)
        r.register(Exploding())
        result = r.probe("synthetic")
        self.assertEqual(result.status, tmodel.UNAVAILABLE)
        self.assertEqual([d.code for d in result.diagnostics], ["ADAPTER_INTERNAL_ERROR"])
        self.assertNotIn("Traceback", json.dumps(result.to_dict()))

    def test_probe_must_return_a_probe_result(self):
        class Wrong(ToolAdapter):
            descriptor = descriptor()

            def probe(self):
                return "fine"

            def execute(self, request, context):
                return AdapterOutcome()

        r = ToolRegistry(FW, allow_test_only=True)
        r.register(Wrong())
        self.assertEqual(r.probe("synthetic").status, tmodel.UNAVAILABLE)

    def test_probe_of_unknown_adapter(self):
        result = registry().probe("nothing")
        self.assertEqual(result.status, tmodel.UNAVAILABLE)
        self.assertEqual([d.code for d in result.diagnostics], ["ADAPTER_NOT_FOUND"])

    def test_lifecycle_order(self):
        r = registry()
        self.assertEqual(r.state("synthetic").state, tmodel.REGISTERED)
        r.probe("synthetic")
        self.assertEqual(r.state("synthetic").history, (tmodel.REGISTERED,))
        self.assertEqual(r.state("synthetic").state, tmodel.READY)

    def test_probe_is_read_only(self):
        p = self.project()
        before = sorted(x.relative_to(p).as_posix() for x in p.rglob("*"))
        registry().probe("synthetic")
        self.assertEqual(sorted(x.relative_to(p).as_posix() for x in p.rglob("*")), before)


# ---------------------------------------------------------------- D  execution

class D01_Execution(TmpCase):
    def test_read_only_success(self):
        result = self.run_cap(syn.INSPECT, project=self.project())
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertEqual(result.artifacts, ())
        self.assertTrue(result.ok)
        self.assertFalse(result.mutation_performed)
        self.assertFalse(result.dry_run)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.exit_code_for_cli, 0)

    def test_a_read_only_capability_creates_nothing_in_the_project(self):
        """Declaring no artifacts means no workspace: running an inspection leaves the tree untouched."""
        p = self.project()
        before = sorted(x.relative_to(p).as_posix() for x in p.rglob("*"))
        self.assertEqual(self.run_cap(syn.INSPECT, project=p).status, tdg.SUCCESS)
        self.assertEqual(sorted(x.relative_to(p).as_posix() for x in p.rglob("*")), before)
        self.assertFalse((p / tpaths.RUNTIME_DIR).exists())

    def test_mutating_success_produces_artifact(self):
        result = self.run_cap(syn.TRANSFORM, project=self.project(), allow_mutation=True,
                              inputs={"text": "hello"}, revision="rev-1")
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertTrue(result.mutation_performed)
        self.assertEqual([a.artifact_id for a in result.artifacts], ["output"])

    def test_mutation_requires_explicit_consent(self):
        result = self.run_cap(syn.TRANSFORM, project=self.project(), inputs={"text": "x"})
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertIn("MUTATION_NOT_ALLOWED", self.codes(result))
        self.assertFalse(result.mutation_performed)

    def test_mutation_consent_on_read_only_capability_rejected(self):
        result = self.run_cap(syn.INSPECT, project=self.project(), allow_mutation=True)
        self.assertEqual(result.status, tdg.INVALID_REQUEST)

    def test_deterministic_failure(self):
        result = self.run_cap(syn.FAIL)
        self.assertEqual(result.status, tdg.FAILED)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.exit_code_for_cli, tdg.EXIT_FOR[tdg.FAILED])
        self.assertIn("EXECUTION_FAILED", self.codes(result))
        self.assertEqual(result.evidence_candidates, ())

    def test_unknown_adapter_and_capability(self):
        r = registry()
        unknown = execute(r, ExecutionRequest(adapter_id="nope", capability_id="nope.x", subject=Subject("TASK", "T")))
        self.assertEqual(unknown.status, tdg.INVALID_REQUEST)
        self.assertIn("ADAPTER_NOT_FOUND", self.codes(unknown))
        missing = self.run_cap("synthetic.nothing", reg=r)
        self.assertIn("CAPABILITY_NOT_FOUND", self.codes(missing))

    def test_unavailable_adapter_fails_closed(self):
        result = self.run_cap(syn.INSPECT, reg=registry(mode=tmodel.UNAVAILABLE), project=self.project())
        self.assertEqual(result.status, tdg.UNAVAILABLE)
        self.assertEqual(result.exit_code_for_cli, tdg.EXIT_FOR[tdg.UNAVAILABLE])
        self.assertEqual(result.artifacts, ())

    def test_incompatible_tool_version_fails_closed(self):
        result = self.run_cap(syn.INSPECT, reg=registry(mode=tmodel.VERSION_UNSUPPORTED), project=self.project())
        self.assertEqual(result.status, tdg.INCOMPATIBLE)

    def test_dry_run_performs_no_mutation(self):
        p = self.project()
        before = sorted(x.relative_to(p).as_posix() for x in p.rglob("*"))
        result = self.run_cap(syn.TRANSFORM, project=p, dry_run=True, inputs={"text": "x"})
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertTrue(result.dry_run)
        self.assertFalse(result.mutation_performed)
        self.assertTrue(result.plan)
        self.assertEqual(result.artifacts, ())
        self.assertIn("MUTATION_SKIPPED_DRY_RUN", self.codes(result))
        self.assertEqual(sorted(x.relative_to(p).as_posix() for x in p.rglob("*")), before)

    def test_dry_run_unsupported_is_refused(self):
        result = self.run_cap(syn.FAIL, dry_run=True)
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertIn("DRY_RUN_UNSUPPORTED", self.codes(result))

    def test_unknown_inputs_refused(self):
        result = self.run_cap(syn.INSPECT, project=self.project(), inputs={"nonsense": "1"})
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertIn("INVALID_TOOL_REQUEST", self.codes(result))

    def test_timeout_outside_policy_refused(self):
        result = self.run_cap(syn.INSPECT, project=self.project(), timeout=10_000)
        self.assertIn("TIMEOUT_NOT_PERMITTED", self.codes(result))
        self.assertEqual(result.status, tdg.INVALID_REQUEST)

    def test_adapter_defect_is_internal_error(self):
        class Broken(ToolAdapter):
            descriptor = descriptor(capabilities=(capability(requires_project=False, requires_tool=False),))

            def probe(self):
                return ProbeResult("synthetic", tmodel.AVAILABLE)

            def execute(self, request, context):
                raise ValueError("token=abcdefghijklmnop leaked in a defect")

        r = ToolRegistry(FW, allow_test_only=True)
        r.register(Broken())
        result = self.run_cap("synthetic.example", reg=r)
        self.assertEqual(result.status, tdg.INTERNAL_ERROR)
        self.assertEqual(result.exit_code_for_cli, tdg.EXIT_FOR[tdg.INTERNAL_ERROR])
        self.assertNotIn("abcdefghijklmnop", json.dumps(result.to_dict()))

    def test_adapter_returning_the_wrong_type_is_internal_error(self):
        class Wrong(ToolAdapter):
            descriptor = descriptor(capabilities=(capability(requires_project=False, requires_tool=False),))

            def probe(self):
                return ProbeResult("synthetic", tmodel.AVAILABLE)

            def execute(self, request, context):
                return {"status": "SUCCESS"}

        r = ToolRegistry(FW, allow_test_only=True)
        r.register(Wrong())
        self.assertEqual(self.run_cap("synthetic.example", reg=r).status, tdg.INTERNAL_ERROR)

    def test_read_only_capability_reporting_a_mutation_is_refused(self):
        result = self.run_cap("synthetic.example", reg=misreporting())
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertFalse(result.mutation_performed)
        self.assertTrue(any("declared READ_ONLY but reported a mutation" in d.message for d in result.diagnostics),
                        [d.message for d in result.diagnostics])

    def test_dry_run_reporting_a_mutation_is_refused_and_never_recorded(self):
        result = self.run_cap("synthetic.writer", reg=misreporting(), dry_run=True)
        self.assertFalse(result.mutation_performed)
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertTrue(any("reported a mutation during a dry run" in d.message for d in result.diagnostics),
                        [d.message for d in result.diagnostics])

    def test_every_status_has_its_own_exit_code(self):
        self.assertEqual(sorted(tdg.EXIT_FOR), sorted(tdg.STATUSES))
        self.assertEqual(len(set(tdg.EXIT_FOR.values())), len(tdg.STATUSES))
        self.assertEqual(tdg.EXIT_FOR[tdg.SUCCESS], 0)
        self.assertNotIn(0, [v for k, v in tdg.EXIT_FOR.items() if k != tdg.SUCCESS])
        self.assertEqual(set(tdg.STATUSES), set(REG["tool_result_statuses"]))

    def test_success_is_not_inferred_from_exit_code(self):
        """An adapter that exits 0 but records a blocking diagnostic never reports SUCCESS."""
        class Optimistic(ToolAdapter):
            descriptor = descriptor(capabilities=(capability(requires_project=False, requires_tool=False),))

            def probe(self):
                return ProbeResult("synthetic", tmodel.AVAILABLE)

            def execute(self, request, context):
                return AdapterOutcome(ok=True, exit_code=0, diagnostics=(
                    tdg.make("ARTIFACT_MISSING", "the output vanished", "synthetic"),))

        r = ToolRegistry(FW, allow_test_only=True)
        r.register(Optimistic())
        result = self.run_cap("synthetic.example", reg=r)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.status, tdg.FAILED)


# ---------------------------------------------------------------- E  process safety

class E01_ProcessSafety(TmpCase):
    def spec(self, **kwargs):
        base = dict(executable=tproc.interpreter_path(), argv=(str(syn.HELPER), "version"), cwd=str(self.tmp))
        base.update(kwargs)
        return tproc.ToolProcessSpec(**base)

    def test_runs_executable_and_argv_without_a_shell(self):
        outcome = tproc.run_process(self.spec(), [str(self.tmp)])
        self.assertEqual(outcome.exit_code, 0)
        self.assertIn("gpos-synthetic-helper", outcome.stdout)

    def test_no_shell_execution(self):
        source = (ROOT / "gpos").rglob("*.py")
        for path in source:
            text = path.read_text()
            self.assertNotIn("shell=True", text, path)
            self.assertNotIn("os.system", text, path)
            self.assertNotIn("popen2", text, path)
        importers = [p.relative_to(ROOT).as_posix() for p in (ROOT / "gpos").rglob("*.py")
                     if "import subprocess" in p.read_text()]
        self.assertEqual(importers, ["gpos/tools/process.py"])

    def test_shell_interpreters_refused(self):
        for name in ("sh", "bash", "zsh", "cmd.exe", "powershell"):
            fake = self.tmp / name
            fake.write_text("#!/bin/true\n")
            fake.chmod(0o755)
            with self.assertRaises(tproc.ProcessSpecError) as cm:
                tproc.validate_spec(self.spec(executable=str(fake)), [str(self.tmp)])
            self.assertEqual(cm.exception.code, "UNSAFE_PROCESS_SPEC")

    def test_program_string_flags_refused(self):
        for flag in ("-c", "/c", "-Command"):
            with self.assertRaises(tproc.ProcessSpecError):
                tproc.validate_spec(self.spec(argv=(flag, "print(1)")), [str(self.tmp)])

    def test_relative_or_missing_executable_refused(self):
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            tproc.validate_spec(self.spec(executable="python3"), [str(self.tmp)])
        self.assertIn("absolute path", str(cm.exception))
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            tproc.validate_spec(self.spec(executable=str(self.tmp / "nothing")), [str(self.tmp)])
        self.assertEqual(cm.exception.code, "TOOL_NOT_FOUND")

    def test_non_string_argument_refused(self):
        with self.assertRaises(tproc.ProcessSpecError):
            tproc.validate_spec(self.spec(argv=(1,)), [str(self.tmp)])

    def test_unsafe_cwd_rejected_before_execution(self):
        for cwd in ("/", str(self.tmp.parent), str(self.tmp / "missing"), "relative"):
            with self.assertRaises(tproc.ProcessSpecError) as cm:
                tproc.validate_spec(self.spec(cwd=cwd), [str(self.tmp / "scope")])
            self.assertIn(cm.exception.code, ("UNSAFE_EXECUTION_PATH",))

    def test_path_traversal_rejected(self):
        scope = self.tmp / "scope"
        scope.mkdir()
        self.assertIsNone(tpaths.unsafe_reason([str(scope)], str(scope / "a" / "b.txt")))
        self.assertIn("outside", tpaths.unsafe_reason([str(scope)], str(scope / ".." / "x.txt")))
        self.assertIn("outside", tpaths.unsafe_reason([str(scope)], "/etc/passwd"))
        self.assertIn("absolute", tpaths.unsafe_reason([str(scope)], "relative/x"))
        self.assertIn("no permitted filesystem scope", tpaths.unsafe_reason([], str(scope / "x")))

    def test_symlink_escape_rejected(self):
        scope = self.tmp / "scope"
        (scope / "real").mkdir(parents=True)
        os.symlink("/etc", scope / "out")
        os.symlink(str(scope / "real"), scope / "inside")
        self.assertIn("symlink", tpaths.unsafe_reason([str(scope)], str(scope / "out" / "passwd")))
        self.assertIn("symlink", tpaths.unsafe_reason([str(scope)], str(scope / "inside" / "x")))

    def test_timeout_terminates_and_never_succeeds(self):
        result = self.run_cap(syn.TIMEOUT, inputs={"seconds": 30})
        self.assertEqual(result.status, tdg.TIMED_OUT)
        self.assertEqual(result.exit_code_for_cli, tdg.EXIT_FOR[tdg.TIMED_OUT])
        self.assertIn("EXECUTION_TIMEOUT", self.codes(result))
        self.assertIsNone(result.exit_code)
        self.assertLess(result.duration_seconds, 25)

    def test_timeout_is_enforced_by_the_runner(self):
        outcome = tproc.run_process(self.spec(argv=(str(syn.HELPER), "sleep", "30"), timeout=0.3), [str(self.tmp)])
        self.assertTrue(outcome.timed_out and outcome.terminated)
        self.assertLess(outcome.duration_seconds, 20)

    def test_output_truncation_is_bounded_and_not_a_failure(self):
        result = self.run_cap(syn.NOISY, inputs={"kilobytes": 600})
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertTrue(result.output_truncated)
        self.assertIn("PROCESS_OUTPUT_TRUNCATED", self.codes(result))
        self.assertLessEqual(len(result.stdout), tproc.DEFAULT_CAPTURE_BYTES + 1024)
        self.assertGreater(result.stdout_bytes, tproc.DEFAULT_CAPTURE_BYTES)

    def test_secret_redaction(self):
        result = self.run_cap(syn.LEAK)
        self.assertEqual(result.status, tdg.SUCCESS)
        payload = json.dumps(result.to_dict())
        self.assertNotIn("super-secret-test-value", payload)
        self.assertNotIn("test-bearer-token-value", payload)
        self.assertIn("[REDACTED]", result.stdout)
        self.assertIn("SECRETS_REDACTED", self.codes(result))
        self.assertIn("fps=59.8", result.stdout)  # ordinary game logs are not redacted

    def test_redaction_shapes(self):
        for text in ("API_KEY=abc123", "password: hunter2", "Authorization: Bearer xyz",
                     "Cookie: session=abc", '{"client_secret": "zzz"}', "--token abc123",
                     "-----BEGIN RSA PRIVATE KEY-----\nbody\n-----END RSA PRIVATE KEY-----"):
            redacted, count = redact(text)
            self.assertGreaterEqual(count, 1, text)
            self.assertIn("[REDACTED]", redacted)
        for benign in ("fps=59.8", "level=forest-02", "tokens 3", "frame_time=16.7"):
            self.assertEqual(redact(benign), (benign, 0))

    def test_environment_values_are_never_recorded(self):
        policy = tproc.EnvironmentPolicy(overrides=(("GPOS_TEST_TOKEN", "super-secret"),))
        meta = policy.metadata()
        self.assertIn("GPOS_TEST_TOKEN", meta["set_names"])
        self.assertNotIn("super-secret", json.dumps(meta))
        self.assertEqual(policy.build({"PATH": "/bin", "AWS_SECRET_ACCESS_KEY": "nope"}),
                         {"PATH": "/bin", "GPOS_TEST_TOKEN": "super-secret"})

    def test_stdin_is_not_interactive(self):
        self.assertIn("stdin", (ROOT / "gpos/tools/process.py").read_text())
        self.assertIn("DEVNULL", (ROOT / "gpos/tools/process.py").read_text())


# ---------------------------------------------------------------- F  leases

class F01_Leases(TmpCase):
    def test_first_writer_acquires_second_conflicts_release_allows_next(self):
        p = self.project()
        first = lease_mod.acquire(p, "synthetic", "SESSION:a", "owner-1", "2026-09-22T10:00:00Z")
        with self.assertRaises(lease_mod.LeaseHeld) as cm:
            lease_mod.acquire(p, "synthetic", "SESSION:a", "owner-2", "2026-09-22T10:00:01Z")
        self.assertEqual(cm.exception.code, "LEASE_CONFLICT")
        self.assertEqual(cm.exception.holder["owner_id"], "owner-1")
        self.assertTrue(lease_mod.release(p, first))
        second = lease_mod.acquire(p, "synthetic", "SESSION:a", "owner-2", "2026-09-22T10:00:02Z")
        self.assertEqual(second.owner_id, "owner-2")

    def test_unrelated_resources_proceed(self):
        p = self.project()
        lease_mod.acquire(p, "synthetic", "SESSION:a", "owner-1", "2026-09-22T10:00:00Z")
        other = lease_mod.acquire(p, "synthetic", "SESSION:b", "owner-2", "2026-09-22T10:00:00Z")
        self.assertTrue(other.token)
        self.assertNotEqual(lease_mod.resource_key("synthetic", "SESSION:a"),
                            lease_mod.resource_key("synthetic", "SESSION:b"))

    def test_resource_identity_is_deterministic(self):
        self.assertEqual(lease_mod.resource_key("a", "b"), lease_mod.resource_key("a", "b"))
        self.assertNotEqual(lease_mod.resource_key("a", "b"), lease_mod.resource_key("b", "a"))

    def test_release_never_removes_another_owner_lease(self):
        p = self.project()
        held = lease_mod.acquire(p, "synthetic", "SESSION:a", "owner-1", "2026-09-22T10:00:00Z")
        impostor = lease_mod.Lease("synthetic", "SESSION:a", "owner-2", "different-token",
                                   "2026-09-22T10:00:00Z", os.getpid(), path=held.path)
        self.assertFalse(lease_mod.release(p, impostor))
        self.assertIsNotNone(lease_mod.holder(p, "synthetic", "SESSION:a"))

    def test_unknown_owner_lease_is_not_broken_silently(self):
        p = self.project()
        path = lease_mod.lease_path(p, "synthetic", f"SYNTHETIC_SESSION:{p}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"adapter_id": "synthetic", "resource_id": "x", "owner_id": "someone-else",
                                    "token": "t", "acquired_at": "2026-09-22T10:00:00Z", "pid": 999999}))
        raw = path.read_bytes()
        result = self.run_cap(syn.STATEFUL_WRITE, project=p, allow_mutation=True, inputs={"text": "x"})
        self.assertEqual(result.status, tdg.CONFLICT)
        self.assertIn("LEASE_CONFLICT", self.codes(result))
        self.assertIn("LEASE_STALE", self.codes(result))  # reported, never acted on
        self.assertEqual(path.read_bytes(), raw)

    def test_unreadable_lease_fails_closed(self):
        p = self.project()
        path = lease_mod.lease_path(p, "synthetic", f"SYNTHETIC_SESSION:{p}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        result = self.run_cap(syn.STATEFUL_WRITE, project=p, allow_mutation=True, inputs={"text": "x"})
        self.assertEqual(result.status, tdg.CONFLICT)
        self.assertIn("LEASE_INVALID", self.codes(result))

    def test_stateful_execution_takes_and_releases_the_lease(self):
        p = self.project()
        result = self.run_cap(syn.STATEFUL_WRITE, project=p, allow_mutation=True, inputs={"text": "x"})
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertIn("LEASE_ACQUIRED", self.codes(result))
        self.assertIsNone(lease_mod.holder(p, "synthetic", f"SYNTHETIC_SESSION:{p}"))
        again = self.run_cap(syn.STATEFUL_WRITE, project=p, allow_mutation=True, inputs={"text": "y"})
        self.assertEqual(again.status, tdg.SUCCESS)

    def test_read_only_and_dry_run_take_no_lease(self):
        p = self.project()
        self.run_cap(syn.INSPECT, project=p)
        self.assertEqual(list(lease_mod.lease_dir(p).glob("*.json")) if lease_mod.lease_dir(p).exists() else [], [])
        result = self.run_cap(syn.STATEFUL_WRITE, project=p, dry_run=True, inputs={"text": "x"})
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertNotIn("LEASE_ACQUIRED", self.codes(result))

    def test_lease_state_stays_in_the_runtime_area(self):
        p = self.project()
        lease = lease_mod.acquire(p, "synthetic", "SESSION:a", "o", "2026-09-22T10:00:00Z")
        rel = Path(lease.path).resolve().relative_to(p.resolve()).as_posix()
        self.assertTrue(rel.startswith(tpaths.RUNTIME_DIR + "/"), rel)
        self.assertFalse(rel.startswith(tpaths.RECORDS_DIR + "/"))
        self.assertIsNone(tpaths.unsafe_reason([str(p)], lease.path))

    def test_breaking_a_lease_is_explicit_and_recorded(self):
        p = self.project()
        lease_mod.acquire(p, "synthetic", "SESSION:a", "owner-1", "2026-09-22T10:00:00Z")
        with self.assertRaises(lease_mod.LeaseHeld):
            lease_mod.break_lease(p, "synthetic", "SESSION:a", "owner-2", "", "2026-09-22T10:05:00Z")
        entry = lease_mod.break_lease(p, "synthetic", "SESSION:a", "owner-2", "the writer crashed",
                                      "2026-09-22T10:05:00Z")
        self.assertEqual(entry["previous_holder"]["owner_id"], "owner-1")
        log = tpaths.runtime_dir(p, "leases", "broken.log").read_text()
        self.assertIn("the writer crashed", log)
        self.assertIsNone(lease_mod.holder(p, "synthetic", "SESSION:a"))

    def test_stale_detection_never_acts_on_its_own(self):
        self.assertTrue(lease_mod.looks_stale({"pid": 999999}, lambda pid: False))
        self.assertFalse(lease_mod.looks_stale({"pid": os.getpid()}, lease_mod.pid_alive))
        self.assertFalse(lease_mod.looks_stale({}, lambda pid: False))


# ---------------------------------------------------------------- G  artifacts

class G01_Artifacts(TmpCase):
    def test_hash_matches_and_is_streamed(self):
        import hashlib
        path = self.tmp / "big.bin"
        chunk = b"a" * (1024 * 1024)
        with open(path, "wb") as fh:
            for _ in range(3):
                fh.write(chunk)
        self.assertEqual(art.hash_file(path), hashlib.sha256(chunk * 3).hexdigest())
        self.assertEqual(art.CHUNK, 1024 * 1024)

    def test_execution_hashes_the_artifact(self):
        import hashlib
        result = self.run_cap(syn.TRANSFORM, project=self.project(), allow_mutation=True,
                              inputs={"text": "hashed"}, revision="r1")
        artifact = result.artifacts[0]
        self.assertEqual(artifact.sha256, hashlib.sha256(b"hashed\n").hexdigest())
        self.assertEqual(artifact.bytes, 7)
        self.assertEqual(artifact.classification, art.CANONICAL)

    def test_missing_artifact_reported(self):
        specs = (art.ArtifactSpec("gone", "TEXT", str(self.tmp / "nothing.txt")),)
        found, problems = art.collect(specs, (str(self.tmp),), self.tmp, "req", "OFFLINE_ANALYSIS")
        self.assertEqual(found, [])
        self.assertEqual([c for c, _, _ in problems], ["ARTIFACT_MISSING"])

    def test_artifact_path_escape_rejected(self):
        outside = self.tmp.parent / "escaped.txt"
        outside.write_text("x")
        self.addCleanup(outside.unlink, True)
        specs = (art.ArtifactSpec("bad", "TEXT", str(outside)),)
        found, problems = art.collect(specs, (str(self.tmp),), self.tmp, "req", "OFFLINE_ANALYSIS")
        self.assertEqual(found, [])
        self.assertEqual([c for c, _, _ in problems], ["UNSAFE_ARTIFACT_PATH"])

    def test_escape_attempt_through_execution_is_refused(self):
        p = self.project()
        result = self.run_cap(syn.ESCAPE, project=p, allow_mutation=True)
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertIn("UNSAFE_ARTIFACT_PATH", self.codes(result))
        self.assertEqual(result.artifacts, ())

    def test_unsafe_cwd_through_execution_is_refused(self):
        p = self.project()
        result = self.run_cap(syn.ESCAPE, project=p, allow_mutation=True,
                              inputs={"cwd": "/etc", "path": str(p / "x.txt")})
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertIn("UNSAFE_EXECUTION_PATH", self.codes(result))

    def test_records_area_is_never_an_artifact_target(self):
        p = self.project()
        target = p / ".game" / "gpos" / "sneaky.txt"
        target.write_text("x")
        specs = (art.ArtifactSpec("sneaky", "TEXT", str(target)),)
        found, problems = art.collect(specs, (str(p),), p, "req", "OFFLINE_ANALYSIS")
        self.assertEqual(found, [])
        self.assertEqual([c for c, _, _ in problems], ["UNSAFE_ARTIFACT_PATH"])

    def test_derived_artifact_points_at_its_source_and_keeps_its_context(self):
        p = self.project()
        capture = p / "capture.mp4"
        capture.write_bytes(b"pretend capture")
        result = self.run_cap(syn.DERIVE, project=p, allow_mutation=True, revision="r1",
                              input_artifacts=(InputArtifact("gameplay", str(capture), "TARGET_RUNTIME"),))
        self.assertEqual(result.status, tdg.SUCCESS)
        derived = next(a for a in result.artifacts if a.artifact_id == "derived")
        self.assertEqual(derived.classification, art.DERIVED)
        self.assertEqual(derived.derived_from, ("gameplay",))
        self.assertEqual(derived.origin_capture_context, "TARGET_RUNTIME")

    def test_incomplete_artifacts_are_reported_not_promoted(self):
        specs = (art.ArtifactSpec("partial", "TEXT", str(self.tmp / "p.txt")),)
        (self.tmp / "p.txt").write_text("half")
        found, _ = art.collect(specs, (str(self.tmp),), self.tmp, "req", "OFFLINE_ANALYSIS", complete=False)
        self.assertFalse(found[0].complete)

    def test_artifact_bytes_are_never_embedded(self):
        """An artifact record references the file by path and hash; it never carries its content."""
        result = self.run_cap(syn.TRANSFORM, project=self.project(), allow_mutation=True,
                              inputs={"text": "unmistakable-artifact-body"}, revision="r1")
        record = result.artifacts[0].to_dict()
        self.assertNotIn("unmistakable-artifact-body", json.dumps(record))
        self.assertNotIn("unmistakable-artifact-body",
                         json.dumps([c.to_dict() for c in result.evidence_candidates])
                         .replace(json.dumps(result.provenance.to_dict())[1:-1], ""))
        self.assertIn("sha256", record)
        self.assertEqual(set(record) & {"content", "data", "body", "text"}, set())


# ---------------------------------------------------------------- H  provenance

class H01_Provenance(TmpCase):
    def prov(self, **kwargs):
        result = self.run_cap(syn.TRANSFORM, project=self.project(), allow_mutation=True,
                              inputs={"text": "x"}, **kwargs)
        self.assertEqual(result.status, tdg.SUCCESS)
        return result, result.provenance.to_dict()

    def test_observed_values_are_recorded(self):
        result, prov = self.prov(revision="rev-9")
        self.assertEqual(prov["adapter_id"], "synthetic")
        self.assertEqual(prov["adapter_version"], SyntheticAdapter().descriptor.adapter_version)
        self.assertEqual(prov["capability_id"], syn.TRANSFORM)
        self.assertEqual(prov["request_id"], result.request_id)
        self.assertEqual(prov["gpos_version"], FW.version)
        self.assertEqual(prov["execution_context"], "OFFLINE_ANALYSIS")
        self.assertEqual(prov["subject"], {"kind": "TASK", "ref": "TASK-1", "revision": "rev-9"})
        self.assertTrue(prov["mutation_performed"])
        self.assertFalse(prov["dry_run"])
        self.assertEqual(prov["tool_version"], syn.TOOL_VERSION)
        self.assertEqual(prov["output_artifacts"], [{"artifact_id": "output", "sha256": result.artifacts[0].sha256}])
        self.assertTrue(prov["started_at"].endswith("Z") and prov["finished_at"].endswith("Z"))

    def test_unknown_values_are_absent_and_named(self):
        _, prov = self.prov()
        self.assertNotIn("revision", prov["subject"])
        for name in ("subject_revision", "build_revision", "build_id", "target_platform", "device"):
            self.assertNotIn(name, prov)
            self.assertIn(name, prov["unknown"])

    def test_supplied_values_are_preserved(self):
        result = self.run_cap(syn.TRANSFORM, project=self.project(), allow_mutation=True, inputs={"text": "x"},
                              revision="rev-1", build_revision="build-abc", target_platform="ANDROID",
                              device="Pixel 7 / Android 14", routing_ref="FEAT-DASH")
        prov = result.provenance.to_dict()
        self.assertEqual(prov["build_revision"], "build-abc")
        self.assertEqual(prov["target_platform"], "ANDROID")
        self.assertEqual(prov["device"], "Pixel 7 / Android 14")
        self.assertEqual(prov["routing_ref"], "FEAT-DASH")
        self.assertEqual(prov["unknown"], ["build_id"])

    def test_no_repository_revision_is_inferred(self):
        _, prov = self.prov()
        self.assertIn("build_revision", prov["unknown"])
        self.assertNotIn("build_revision", prov)

    def test_dry_run_provenance_is_explicit(self):
        result = self.run_cap(syn.TRANSFORM, project=self.project(), dry_run=True, inputs={"text": "x"})
        prov = result.provenance.to_dict()
        self.assertTrue(prov["dry_run"])
        self.assertFalse(prov["mutation_performed"])
        self.assertEqual(prov["output_artifacts"], [])

    def test_input_artifact_hashes_are_recorded(self):
        import hashlib
        p = self.project()
        capture = p / "capture.mp4"
        capture.write_bytes(b"pretend capture")
        result = self.run_cap(syn.DERIVE, project=p, allow_mutation=True, revision="r1",
                              input_artifacts=(InputArtifact("gameplay", str(capture), "TARGET_RUNTIME"),))
        self.assertEqual(result.provenance.to_dict()["input_artifacts"],
                         [{"artifact_id": "gameplay", "sha256": hashlib.sha256(b"pretend capture").hexdigest()}])

    def test_command_is_recorded_without_a_shell_string_and_without_secrets(self):
        _, prov = self.prov()
        self.assertIn("executable", prov["command"])
        self.assertIsInstance(prov["command"]["argv"], list)
        self.assertNotIn("environment_values", prov)
        self.assertEqual(prov["environment"]["set_names"], [])

    def test_no_secrets_in_provenance(self):
        os.environ["GPOS_TEST_FAKE_TOKEN"] = "super-secret-provenance-value"
        self.addCleanup(os.environ.pop, "GPOS_TEST_FAKE_TOKEN", None)
        _, prov = self.prov()
        self.assertNotIn("super-secret-provenance-value", json.dumps(prov))

    def test_missing_required_reports_names(self):
        from gpos.tools.provenance import missing_required
        result, _ = self.prov()
        self.assertEqual(missing_required(result.provenance, ("subject_revision", "device")),
                         ("subject_revision", "device"))
        self.assertEqual(missing_required(result.provenance, ("adapter_id",)), ())


# ---------------------------------------------------------------- I  evidence

class I01_Evidence(TmpCase):
    def test_valid_type_and_context_accepted(self):
        result = self.run_cap(syn.TRANSFORM, project=self.project(), allow_mutation=True,
                              inputs={"text": "x"}, revision="r1")
        candidate = result.evidence_candidates[0]
        self.assertEqual((candidate.evidence_type, candidate.capture_context), ("CODE_EVIDENCE", "OFFLINE_ANALYSIS"))
        self.assertTrue(candidate.materializable)
        self.assertEqual(candidate.subject_ref, "TASK-1")
        self.assertEqual(candidate.source_adapter, "synthetic")

    def test_incompatible_type_and_context_rejected_from_the_registry(self):
        result = self.run_cap(syn.CLAIM_RUNTIME, project=self.project(), revision="r1",
                              inputs={"evidence_type": "CODE_EVIDENCE", "capture_context": "TARGET_RUNTIME"})
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertIn("EVIDENCE_CONTEXT_INCOMPATIBLE", self.codes(result))
        self.assertEqual(result.evidence_candidates, ())
        self.assertNotIn("TARGET_RUNTIME", REG["evidence_context_compatibility"]["CODE_EVIDENCE"])

    def test_no_duplicate_compatibility_matrix_in_the_foundation(self):
        """The foundation must read the registry, not keep its own table."""
        for path in (ROOT / "gpos" / "tools").rglob("*.py"):
            text = path.read_text()
            self.assertNotIn("evidence_context_compatibility = ", text, path)
            if "evidence_context_compatibility" in text:
                self.assertIn("registry", text, path)

    def test_human_evidence_can_never_be_produced(self):
        result = self.run_cap(syn.CLAIM_HUMAN, project=self.project(), revision="r1")
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertIn("EVIDENCE_TYPE_FORBIDDEN", self.codes(result))
        self.assertEqual(result.evidence_candidates, ())
        self.assertEqual(POLICY["forbidden_evidence_types"], ["HUMAN_EVIDENCE"])

    def test_dry_run_cannot_emit_observed_evidence(self):
        p = self.project()
        for etype in ("RUNTIME_EVIDENCE", "PERFORMANCE_EVIDENCE"):
            context = "TARGET_RUNTIME" if etype == "RUNTIME_EVIDENCE" else "OFFLINE_ANALYSIS"
            result = self.run_cap(syn.CLAIM_RUNTIME, project=p, dry_run=True, revision="r1",
                                  inputs={"evidence_type": etype, "capture_context": context})
            self.assertEqual(result.status, tdg.INVALID_REQUEST, etype)
            self.assertIn("EVIDENCE_NOT_AVAILABLE_IN_DRY_RUN", self.codes(result))
        for forbidden in ("RUNTIME_EVIDENCE", "DEVICE_EVIDENCE", "PERFORMANCE_EVIDENCE",
                          "MOTION_EVIDENCE", "AUDIO_EVIDENCE"):
            self.assertNotIn(forbidden, POLICY["dry_run_evidence_types"])

    def test_capture_context_must_be_observed(self):
        result = self.run_cap(syn.CLAIM_RUNTIME, project=self.project(), revision="r1",
                              inputs={"evidence_type": "RUNTIME_EVIDENCE", "capture_context": "TARGET_RUNTIME"})
        self.assertIn("EVIDENCE_CONTEXT_NOT_OBSERVED", self.codes(result))
        self.assertEqual(result.evidence_candidates, ())
        self.assertTrue(any("this execution observed OFFLINE_ANALYSIS" in d.message for d in result.diagnostics),
                        [d.message for d in result.diagnostics])

    def test_undeclared_pair_rejected(self):
        cap = SyntheticAdapter().capability(syn.CLAIM_HUMAN)
        candidate = ev.EvidenceCandidate("TEST_EVIDENCE", "AUTOMATED_TEST", "s", "TASK", "T", "synthetic",
                                         syn.CLAIM_HUMAN, "2026-09-22T10:00:00Z", artifact_ids=("a",))
        self.assertIn("AUTOMATED_TEST", REG["evidence_context_compatibility"]["TEST_EVIDENCE"])  # a legal pair
        accepted, problems = ev.validate(FW, candidate, cap, [], False, "OFFLINE_ANALYSIS")
        self.assertIsNone(accepted)
        self.assertIn("did not declare", problems[0].message)

    def test_derived_candidate_inherits_and_cannot_upgrade(self):
        p = self.project()
        capture = p / "capture.mp4"
        capture.write_bytes(b"pretend capture")
        source = (InputArtifact("gameplay", str(capture), "TARGET_RUNTIME"),)
        good = self.run_cap(syn.DERIVE, project=p, allow_mutation=True, revision="r1", input_artifacts=source)
        self.assertEqual(good.status, tdg.SUCCESS)
        candidate = good.evidence_candidates[0]
        self.assertEqual((candidate.evidence_type, candidate.capture_context), ("VISUAL_EVIDENCE", "TARGET_RUNTIME"))
        self.assertNotEqual(candidate.capture_context,
                            SyntheticAdapter().capability(syn.DERIVE).execution_context)
        upgraded = self.run_cap(syn.DERIVE, project=p, allow_mutation=True, revision="r1",
                                input_artifacts=source, inputs={"claim_context": "DCC_RENDER"})
        self.assertEqual(upgraded.status, tdg.INVALID_REQUEST)
        self.assertIn("EVIDENCE_CONTEXT_NOT_OBSERVED", self.codes(upgraded))
        self.assertTrue(any("inherits the capture context of its origin" in d.message for d in upgraded.diagnostics),
                        [d.message for d in upgraded.diagnostics])

    def test_candidate_cannot_express_a_gate_result(self):
        fields = set(ev.EvidenceCandidate.__dataclass_fields__)
        for forbidden in ("gate", "gate_status", "status", "verdict", "pass", "passed", "approved",
                          "reviewer", "assessor", "decision", "review_policy"):
            self.assertNotIn(forbidden, fields)
        text = "".join(p.read_text() for p in (ROOT / "gpos" / "tools").rglob("*.py"))
        for token in ('"PASS"', "'PASS'", "gate_status", "review_policy"):
            self.assertNotIn(token, text)

    def test_unknown_revision_keeps_the_candidate_but_blocks_materialization(self):
        result = self.run_cap(syn.TRANSFORM, project=self.project(), allow_mutation=True, inputs={"text": "x"})
        candidate = result.evidence_candidates[0]
        self.assertFalse(candidate.materializable)
        self.assertIsNone(candidate.subject_revision)
        self.assertIn("EVIDENCE_NOT_MATERIALIZABLE", self.codes(result))
        self.assertEqual(result.status, tdg.SUCCESS)  # informational, not blocking
        self.assertTrue(any("subject revision unknown" in l for l in candidate.limitations))
        record, problems = ev.materialize(FW, candidate, result.artifacts, "EV-1")
        self.assertIsNone(record)
        self.assertIn("proven subject revision", problems[0])

    def test_candidate_without_artifacts_rejected(self):
        cap = SyntheticAdapter().capability(syn.TRANSFORM)
        candidate = ev.EvidenceCandidate("CODE_EVIDENCE", "OFFLINE_ANALYSIS", "s", "TASK", "T", "synthetic",
                                         syn.TRANSFORM, "2026-09-22T10:00:00Z")
        accepted, problems = ev.validate(FW, candidate, cap, [], False, "OFFLINE_ANALYSIS")
        self.assertIsNone(accepted)
        self.assertIn("at least one artifact", problems[0].message)

    def test_timed_out_execution_offers_no_evidence(self):
        result = self.run_cap(syn.TIMEOUT, inputs={"seconds": 30})
        self.assertEqual(result.evidence_candidates, ())

    def test_validate_refuses_an_incomplete_artifact(self):
        cap = SyntheticAdapter().capability(syn.TRANSFORM)
        artifact = art.Artifact("output", "TEXT", "out.txt", "/tmp/out.txt", "0" * 64, 1,
                                origin_capture_context="OFFLINE_ANALYSIS", complete=False)
        candidate = ev.EvidenceCandidate("CODE_EVIDENCE", "OFFLINE_ANALYSIS", "s", "TASK", "T", "synthetic",
                                         syn.TRANSFORM, "2026-09-22T10:00:00Z", artifact_ids=("output",))
        accepted, problems = ev.validate(FW, candidate, cap, [artifact], False, "OFFLINE_ANALYSIS")
        self.assertIsNone(accepted)
        self.assertEqual([p.code for p in problems], ["ARTIFACT_INCOMPLETE"])

    def test_a_failed_execution_offers_no_evidence_even_about_complete_inputs(self):
        """The input artifact is complete on its own; the execution that referenced it is not."""
        p = self.project()
        capture = p / "capture.mp4"
        capture.write_bytes(b"pretend capture")
        result = self.run_cap(syn.PARTIAL, project=p, revision="r1",
                              input_artifacts=(InputArtifact("gameplay", str(capture), "TARGET_RUNTIME"),))
        self.assertEqual(result.status, tdg.FAILED)
        self.assertEqual(result.evidence_candidates, ())
        self.assertIn("ARTIFACT_INCOMPLETE", self.codes(result))
        self.assertTrue(any("did not finish, so its evidence candidate is withheld" in d.message
                            for d in result.diagnostics), [d.message for d in result.diagnostics])

    def test_materialize_is_schema_valid_deterministic_and_writes_nothing(self):
        p = self.project()
        result = self.run_cap(syn.TRANSFORM, project=p, allow_mutation=True, inputs={"text": "x"}, revision="rev-1")
        before = sorted(x.as_posix() for x in (p / ".game" / "gpos").rglob("*"))
        record, problems = ev.materialize(FW, result.evidence_candidates[0], result.artifacts, "EV-TOOL-1",
                                          tool_version="synthetic 1.0.0")
        self.assertEqual(problems, [])
        self.assertEqual(FW.validators["evidence"].errors(record), [])
        self.assertEqual(record["source"], {"kind": "TOOL", "id": "synthetic"})
        self.assertEqual(record["provenance"]["subject_revision"], "rev-1")
        self.assertNotIn("gate", json.dumps(record))
        again, _ = ev.materialize(FW, result.evidence_candidates[0], result.artifacts, "EV-TOOL-1",
                                  tool_version="synthetic 1.0.0")
        self.assertEqual(json.dumps(record, sort_keys=True), json.dumps(again, sort_keys=True))
        self.assertEqual(sorted(x.as_posix() for x in (p / ".game" / "gpos").rglob("*")), before)

    def test_materialize_refuses_human_evidence(self):
        candidate = ev.EvidenceCandidate("HUMAN_EVIDENCE", "HUMAN_RECORD", "s", "TASK", "T", "synthetic",
                                         syn.TRANSFORM, "2026-09-22T10:00:00Z", artifact_ids=("a",),
                                         subject_revision="r", materializable=True)
        record, problems = ev.materialize(FW, candidate, [], "EV-1")
        self.assertIsNone(record)
        self.assertIn("never be materialized", problems[0])

    def test_execution_never_writes_into_the_record_area(self):
        p = self.project()
        before = {x.as_posix(): x.stat().st_mtime for x in (p / ".game" / "gpos").rglob("*") if x.is_file()}
        for cap, kwargs in ((syn.INSPECT, {}), (syn.TRANSFORM, {"allow_mutation": True, "inputs": {"text": "x"}}),
                            (syn.STATEFUL_WRITE, {"allow_mutation": True, "inputs": {"text": "x"}})):
            self.run_cap(cap, project=p, **kwargs)
        after = {x.as_posix(): x.stat().st_mtime for x in (p / ".game" / "gpos").rglob("*") if x.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((p / ".game" / "gpos" / "evidence").exists()
                         and list((p / ".game" / "gpos" / "evidence").glob("*")))


# ---------------------------------------------------------------- J  project preconditions

class J01_ProjectPreconditions(TmpCase):
    def test_invalid_project_blocks_project_bound_execution(self):
        p = self.project()
        config = p / ".game" / "gpos" / "project-config.json"
        data = json.loads(config.read_text())
        data["lifecycle_stage"] = "NOT_A_STAGE"
        config.write_text(json.dumps(data))
        result = self.run_cap(syn.INSPECT, project=p)
        self.assertEqual(result.status, tdg.INVALID_REQUEST)
        self.assertIn("PROJECT_INVALID", self.codes(result))

    def test_missing_project_layout(self):
        result = self.run_cap(syn.INSPECT, project=self.tmp / "nowhere")
        self.assertIn("PROJECT_LAYOUT", self.codes(result))

    def test_unsupported_gpos_version_is_incompatible(self):
        p = self.project()
        config = p / ".game" / "gpos" / "project-config.json"
        data = json.loads(config.read_text())
        data["gpos_version"] = "9.9.9"
        config.write_text(json.dumps(data))
        result = self.run_cap(syn.INSPECT, project=p)
        self.assertEqual(result.status, tdg.INCOMPATIBLE)
        self.assertIn("GPOS_VERSION_INCOMPATIBLE", self.codes(result))

    def test_project_bound_capability_needs_a_project(self):
        result = self.run_cap(syn.INSPECT, output_dir=str(self.out()))
        self.assertIn("INVALID_TOOL_REQUEST", self.codes(result))

    def test_unrelated_not_ready_routing_does_not_block(self):
        p = self.project()
        routing = next((p / ".game" / "gpos" / "routings").glob("*.json"))
        data = json.loads(routing.read_text())
        self.assertTrue(data)  # the fixture has a routing whose gates are not all PASS
        result = self.run_cap(syn.INSPECT, project=p)
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertFalse(SyntheticAdapter().capability(syn.INSPECT).requires_ready_routing)

    def test_probe_and_listing_need_no_project_or_readiness(self):
        r = registry()
        self.assertEqual(r.probe("synthetic").status, tmodel.AVAILABLE)
        self.assertEqual(r.adapter_ids(), ["synthetic"])
        self.assertEqual([c.id for _, c in r.list_capabilities()],
                         sorted(c.id for c in SyntheticAdapter().capabilities()))

    def test_capability_that_requires_readiness_checks_it(self):
        p = self.project()
        cap = capability(id="synthetic.routed", requires_ready_routing=True, requires_tool=False)
        from gpos.tools.execution import project_problems
        request = ExecutionRequest(adapter_id="synthetic", capability_id="synthetic.routed",
                                   subject=Subject("TASK", "T"), project_root=str(p))
        _, problems = project_problems(FW, request, cap)
        self.assertEqual([d.code for d in problems], ["INVALID_TOOL_REQUEST"])
        routed = ExecutionRequest(adapter_id="synthetic", capability_id="synthetic.routed",
                                  subject=Subject("TASK", "T"), project_root=str(p), routing_ref="FEAT-DASH")
        _, problems = project_problems(FW, routed, cap)
        self.assertEqual([d.code for d in problems], ["ROUTING_NOT_READY"])

    def test_non_project_capability_is_not_blocked_by_an_invalid_project(self):
        result = self.run_cap(syn.FAIL)
        self.assertEqual(result.status, tdg.FAILED)  # ran; not blocked on any project state


# ---------------------------------------------------------------- K  determinism

class K01_Determinism(TmpCase):
    VOLATILE = {"started_at", "finished_at", "duration_seconds", "request_id"}

    def stable(self, payload):
        if isinstance(payload, dict):
            return {k: self.stable(v) for k, v in payload.items() if k not in self.VOLATILE}
        if isinstance(payload, list):
            return [self.stable(v) for v in payload]
        if isinstance(payload, str) and payload.startswith("req-"):
            return "req-*"
        return payload

    def test_same_inputs_produce_the_same_semantic_result(self):
        first = self.run_cap(syn.TRANSFORM, project=self.project("a"), allow_mutation=True,
                             inputs={"text": "same"}, revision="r1")
        second = self.run_cap(syn.TRANSFORM, project=self.project("b"), allow_mutation=True,
                              inputs={"text": "same"}, revision="r1")
        def normalize(result, root):
            text = json.dumps(self.stable(result.to_dict()))
            text = text.replace(str(root.resolve()), "<ROOT>").replace(str(root), "<ROOT>")
            payload = json.loads(text.replace(result.request_id, "<REQ>"))
            payload["provenance"].pop("command", None)
            for candidate in payload["evidence_candidates"]:
                candidate.get("provenance", {}).pop("command", None)
            return payload
        self.assertEqual(normalize(first, self.tmp / "a"), normalize(second, self.tmp / "b"))
        self.assertEqual(first.artifacts[0].sha256, second.artifacts[0].sha256)

    def test_canonical_json_is_stable(self):
        result = self.run_cap(syn.TRANSFORM, project=self.project(), allow_mutation=True,
                              inputs={"text": "x"}, revision="r1")
        self.assertEqual(json.dumps(result.to_dict(), sort_keys=True),
                         json.dumps(result.to_dict(), sort_keys=True))

    def test_diagnostic_order_is_stable(self):
        diags = [tdg.make("ARTIFACT_MISSING", "b"), tdg.make("LEASE_CONFLICT", "a"),
                 tdg.make("SECRETS_REDACTED", "c"), tdg.make("TOOL_NOT_FOUND", "d")]
        self.assertEqual([d.code for d in tdg.sort(diags)], [d.code for d in tdg.sort(list(reversed(diags)))])
        self.assertEqual([d.code for d in tdg.sort(diags)][0], "LEASE_CONFLICT")

    def test_result_status_is_the_most_severe_class(self):
        self.assertEqual(tdg.result_status([tdg.make("EXECUTION_FAILED", "x"), tdg.make("LEASE_CONFLICT", "y")]),
                         tdg.CONFLICT)
        self.assertEqual(tdg.result_status([tdg.make("SECRETS_REDACTED", "x")]), tdg.SUCCESS)
        self.assertEqual(tdg.result_status([], default=tdg.TIMED_OUT), tdg.TIMED_OUT)

    def test_rfc3339_timestamps(self):
        import re as _re
        result = self.run_cap(syn.INSPECT, project=self.project())
        for value in (result.started_at, result.finished_at):
            self.assertRegex(value, _re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"))

    def test_duration_uses_a_monotonic_clock(self):
        class Fixed(Clock):
            def __init__(self):
                self.ticks = iter([100.0, 100.0, 103.5, 103.5, 103.5, 103.5])

            def now(self):
                return "2026-09-22T10:00:00Z"

            def monotonic(self):
                return next(self.ticks)

        result = execute(registry(), ExecutionRequest(adapter_id="synthetic", capability_id=syn.INSPECT,
                                                      subject=Subject("TASK", "T"),
                                                      project_root=str(self.project())), clock=Fixed())
        self.assertEqual(result.started_at, result.finished_at)  # wall clock frozen
        self.assertGreater(result.duration_seconds, 0)           # duration still measured
        self.assertIn("monotonic", (ROOT / "gpos/tools/process.py").read_text())


# ---------------------------------------------------------------- L  regression and boundaries

class L01_Boundaries(TmpCase):
    def test_no_production_tool_adapter_exists(self):
        self.assertEqual(default_registry(FW).adapter_ids(), [])
        modules = sorted(p.relative_to(ROOT / "gpos" / "tools").as_posix()
                         for p in (ROOT / "gpos" / "tools").rglob("*.py"))
        self.assertEqual(modules, ["__init__.py", "__main__.py", "artifacts.py", "capabilities.py", "cli.py",
                                   "diagnostics.py", "errors.py", "evidence.py", "execution.py", "leases.py",
                                   "model.py", "paths.py", "process.py", "provenance.py", "redaction.py",
                                   "registry.py", "synthetic/__init__.py", "synthetic/adapter.py",
                                   "synthetic/helper.py", "validation.py"])

    def test_the_only_adapter_in_the_tree_is_test_only(self):
        self.assertTrue(SyntheticAdapter().descriptor.test_only)
        self.assertEqual(SyntheticAdapter().descriptor.adapter_kind, POLICY["test_only_adapter_kind"])
        self.assertEqual(SyntheticAdapter().descriptor.tool_family, "TEST_ONLY")

    def test_no_real_tool_executable_is_named(self):
        import ast
        forbidden = {"git", "ffmpeg", "ffprobe", "adb", "blender", "unity", "unityhub", "gh"}
        for path in (ROOT / "gpos").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn(node.value.strip().lower(), forbidden, f"{path}: {node.value!r}")

    def test_no_network_at_runtime(self):
        banned = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "ftplib", "smtplib", "asyncio"}
        import ast
        for path in (ROOT / "gpos").rglob("*.py"):
            names = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
            self.assertEqual(names & banned, set(), path)
        self.assertEqual(POLICY["network"], "FORBIDDEN")
        self.assertEqual(SyntheticAdapter().descriptor.network, "FORBIDDEN")

    def test_no_global_or_user_files_are_written(self):
        p = self.project()
        for cap, kwargs in ((syn.INSPECT, {}), (syn.TRANSFORM, {"allow_mutation": True, "inputs": {"text": "x"}}),
                            (syn.STATEFUL_WRITE, {"allow_mutation": True, "inputs": {"text": "x"}})):
            self.run_cap(cap, project=p, **kwargs)
        self.assertEqual(sorted(Path(_HOME).rglob("*")), [])

    def test_generated_state_stays_out_of_the_record_area(self):
        self.assertEqual(POLICY["runtime_state_dir"], tpaths.RUNTIME_DIR)
        self.assertNotEqual(tpaths.RUNTIME_DIR, tpaths.RECORDS_DIR)
        self.assertFalse(tpaths.RUNTIME_DIR.startswith(tpaths.RECORDS_DIR + "/"))

    def test_registry_vocabulary_is_the_single_source(self):
        self.assertEqual(set(tmodel.ADAPTER_STATES), set(REG["tool_adapter_states"]))
        self.assertEqual(set(tmodel.PROBE_STATUSES), set(REG["tool_probe_statuses"]))
        self.assertEqual(set(tdg.STATUSES), set(REG["tool_result_statuses"]))
        self.assertEqual({art.CANONICAL, art.DERIVED}, set(REG["tool_artifact_classifications"]))
        declared = {c.category for c in SyntheticAdapter().capabilities()}
        self.assertTrue(declared <= set(REG["tool_capability_categories"]))

    def test_phase_2b_semantics_untouched(self):
        from gpos.adapters import diagnostics as adg
        self.assertEqual(adg.EXIT_FOR, {"OK": 0, "INVALID": 1, "DRIFT": 2, "ERROR": 3, "CONFLICT": 4})
        self.assertEqual(sorted(REG["adapter_ids"]), ["claude-code", "codex"])
        self.assertEqual(adg.RUNTIME_STATUSES, ("RUNTIME_NOT_YET_SMOKE_TESTED",))

    def test_all_thirteen_skills_remain_draft(self):
        skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(skills), 13)
        for skill in skills:
            self.assertIn("maturity: DRAFT", skill.read_text(), skill)

    def test_diagnostic_codes_have_a_fixed_class(self):
        for code, (cls, meaning) in tdg.CODES.items():
            self.assertIn(cls, tuple(tdg.STATUSES) + (tdg.INFO,), code)
            self.assertTrue(meaning)
            self.assertEqual(tdg.make(code, "x").cls, cls)
        self.assertEqual(len(set(tdg.CODES)), len(tdg.CODES))


# ---------------------------------------------------------------- M  CLI

class M01_Cli(TmpCase):
    def cli(self, *argv):
        import io
        from gpos.tools import cli as tool_cli
        buffer = io.StringIO()
        code = tool_cli.main(list(argv), stdout=buffer)
        return code, buffer.getvalue()

    def test_list_is_empty_without_test_adapters(self):
        code, out = self.cli("list")
        self.assertEqual(code, 0)
        self.assertIn("0 tool adapter", out)
        self.assertIn("none registered", out)

    def test_list_describe_capabilities_probe(self):
        code, out = self.cli("--include-test-adapters", "list")
        self.assertEqual((code, "synthetic" in out), (0, True))
        self.assertIn("TEST_ONLY", out)
        code, out = self.cli("--include-test-adapters", "describe", "synthetic")
        self.assertEqual(code, 0)
        self.assertIn(syn.STATEFUL_WRITE, out)
        self.assertIn("single writer", out)
        code, out = self.cli("--include-test-adapters", "capabilities", "synthetic", "--format", "json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["capabilities"]["capabilities"]), len(SyntheticAdapter().capabilities()))
        code, out = self.cli("--include-test-adapters", "probe", "synthetic", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["probe"]["status"], "AVAILABLE")

    def test_unknown_adapter_exits_invalid_request(self):
        code, _ = self.cli("--include-test-adapters", "describe", "nothing")
        self.assertEqual(code, tdg.EXIT_FOR[tdg.INVALID_REQUEST])

    def test_execute_exit_codes_match_statuses(self):
        p = self.project()
        cases = ((syn.INSPECT, [], 0),
                 (syn.TRANSFORM, ["--input", "text=x"], tdg.EXIT_FOR[tdg.INVALID_REQUEST]),
                 (syn.TRANSFORM, ["--input", "text=x", "--allow-mutation"], 0))
        for cap, extra, expected in cases:
            code, _ = self.cli("--include-test-adapters", "execute", "--adapter", "synthetic",
                               "--capability", cap, "--project", str(p), "--subject-ref", "TASK-1", *extra)
            self.assertEqual(code, expected, cap)

    def test_execute_json_output(self):
        p = self.project()
        code, out = self.cli("--include-test-adapters", "execute", "--adapter", "synthetic",
                             "--capability", syn.TRANSFORM, "--project", str(p), "--subject-ref", "TASK-1",
                             "--subject-revision", "r1", "--input", "text=x", "--allow-mutation",
                             "--format", "json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["result"]["status"], "SUCCESS")
        self.assertTrue(payload["result"]["mutation_performed"])
        self.assertEqual(payload["request"]["subject"]["revision"], "r1")

    def test_no_arbitrary_command_option(self):
        from gpos.tools import cli as tool_cli
        text = (ROOT / "gpos/tools/cli.py").read_text()
        for forbidden in ("--command", "--exec", "--argv", "--shell", "--executable"):
            self.assertNotIn(forbidden, text)
        options = {opt for a in tool_cli.build_parser()._actions for opt in a.option_strings}
        self.assertEqual(options & {"--command", "--exec", "--argv", "--shell", "--executable"}, set())

    def test_usage_error_is_invalid_request_not_a_crash(self):
        code, _ = self.cli("nonsense")
        self.assertEqual(code, tdg.EXIT_FOR[tdg.INVALID_REQUEST])

    def test_module_entrypoint(self):
        out = subprocess.run([sys.executable, "-B", "-m", "gpos.tools", "--include-test-adapters", "list"],
                             cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("synthetic", out.stdout)


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    shutil.rmtree(_HOME, ignore_errors=True)
    print(f"GPOS tool adapter foundation tests ({len(SyntheticAdapter().capabilities())} synthetic capabilities; "
          f"0 production tool adapters)")
    sys.exit(0 if result.wasSuccessful() else 1)
