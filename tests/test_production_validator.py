#!/usr/bin/env python3
"""Phase 2A — production validator tests.

    python3 tests/test_production_validator.py

Standard library only (the `jsonschema` / `rfc3339-validator` cross-checks run when installed).

Groups:
  A  schema parity           production gpos.schema vs tests/schema_lite.py vs jsonschema; RFC 3339
  B  record-set parity       every Phase-1 authority and record fixture vs the frozen reference model
  C  readiness parity        routed readiness vs reference routed_scope_ready; bundles
  D  adversarial regressions named attacks on valid bundles, each with its diagnostic code
  E  reference independence  production code never imports the reference or the tests
  L  loading                 bundle layout, malformed files, hidden files, ids from contents
  R  read-only, determinism, CLI, performance, contract coverage

The frozen reference functions in tests/validate_framework.py are the regression oracle here;
production code (gpos/) never imports them.
"""

import copy
import hashlib
import io
import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # keep bytecode out of the tree (X08 scans files)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import gpos  # noqa: E402
import schema_lite  # noqa: E402  (oracle)
import validate_framework as vf  # noqa: E402  (oracle)
from gpos import cli, diagnostics as dg  # noqa: E402
from gpos.errors import FrameworkLoadError, UnsupportedSchemaKeyword  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.records import Record, from_records, load_project  # noqa: E402
from gpos.schema import SchemaValidator, is_rfc3339_datetime  # noqa: E402
from gpos.validation import authority, project  # noqa: E402
from gpos.validation.gates import assess_gate  # noqa: E402
from gpos.validation.routing import routing_structure  # noqa: E402

try:
    import jsonschema  # type: ignore
except ImportError:  # pragma: no cover
    jsonschema = None

FW = load_framework()
BUNDLES = ROOT / "tests" / "fixtures" / "bundles"
AUTHORITY = sorted((ROOT / "tests" / "fixtures" / "authority").glob("*.json"))
RECORDS = sorted((ROOT / "tests" / "fixtures" / "records").glob("*.json"))

# Production rules the frozen reference does not model (reported for Human Review):
#   UNSUPPORTED_GPOS_VERSION                    §12.7  records are validated only against the pinned GPOS version
#   CROSS_REVIEW_SUPERSEDED_WITHOUT_REPLACEMENT §12.6  "a superseded negative cross-review has a later review by the same reviewer"
#   HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED        §12.12 HUMAN_EVIDENCE source is a Human Review participant "for the gate"
PRODUCTION_ONLY = {"UNSUPPORTED_GPOS_VERSION", "CROSS_REVIEW_SUPERSEDED_WITHOUT_REPLACEMENT", "HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED"}
EXPECTED_DIVERGENCE = {"gpos-patch-upgrade-ungoverned.json": {"UNSUPPORTED_GPOS_VERSION"}}


# ---------------------------------------------------------------- helpers

def fixture_records(path):
    fx = json.loads(path.read_text())
    routings = [vf.build(vf._fixture(r)) for r in ([fx["routing"]] if "routing" in fx else fx.get("routings", []))]
    return (vf.build(fx["config"]), [vf.build(d) for d in fx["decisions"]], [vf.build(e) for e in fx.get("evidence", [])],
            [vf.build(g) for g in fx.get("gates", [])], routings)


def reference_verdict(config, decisions, evidence, gates, routings):
    """(reference record-set problems, reference routed readiness per routing id)."""
    problems = vf.record_set_problems(config, decisions, evidence, gates, routings)
    ready = {r["task_id"]: vf.routed_scope_ready(config, r, gates, evidence, decisions) for r in routings} if len(routings) == 1 else {}
    return problems, ready


def production_flags(an):
    """What the reference would call a record-set problem: any ERROR, or a reference-class blocker."""
    blockers = {d.code for diags in an.routing_diagnostics.values() for d in diags}
    return (not an.valid) or bool(blockers & dg.REFERENCE_CLASS_BLOCKERS)


def error_codes(an):
    return {d.code for d in an.errors if d.severity == dg.ERROR}


def all_codes(an):
    return {d.code for d in an.errors} | {d.code for diags in an.routing_diagnostics.values() for d in diags}


def bundle(name):
    return load_project(BUNDLES / name)


def data(rs, record_type, record_id):
    return next(r.data for r in rs.all_records() if r.type == record_type and r.id == record_id)


def raw(rs):
    return (rs.config.data, [r.data for r in rs.decisions], [r.data for r in rs.evidence], [r.data for r in rs.gates],
            [r.data for r in rs.routings])


def decision(did, kind, **payload):
    d = json.loads((ROOT / "examples/example-decision-record.json").read_text())
    d.pop("transition")
    d.update({"decision_id": did, "kind": kind, "subject": {"kind": "PROJECT", "ref": "synthetic-project"}, "affects": ["project-config"]})
    d.update(payload)
    return d


def run_cli(*argv):
    out = io.StringIO()
    code = cli.main(list(argv), stdout=out)
    return code, out.getvalue()


def snapshot(path):
    out = {}
    for p in sorted(Path(path).rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(path))] = (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir",
                                         st.st_mtime_ns, stat.S_IMODE(st.st_mode))
    return out


# ---------------------------------------------------------------- A  schema parity

def schema_instances():
    """(schema name, instance, label) for every example, schema fixture, fixture record and bundle record."""
    out = [(n, json.loads(p.read_text()), p.name) for n, p in vf.EXAMPLES.items()]
    for f in sorted((ROOT / "tests/fixtures/valid").glob("*.json")) + sorted((ROOT / "tests/fixtures/invalid").glob("*.json")):
        fx = json.loads(f.read_text())
        out.append((fx["schema"], vf.build(fx), f.name))
    for f in AUTHORITY:
        config, decisions, evidence, gates, routings = fixture_records(f)
        out += [("project-config", config, f.name)] + [("decision", d, f.name) for d in decisions] + \
               [("evidence", e, f.name) for e in evidence] + [("gate", g, f.name) for g in gates] + \
               [("task-routing", r, f.name) for r in routings]
    for f in RECORDS:
        fx = json.loads(f.read_text())
        out += [("gate", vf.build(fx["gate"]), f.name)] + [("evidence", vf.build(e), f.name) for e in fx["evidence"]]
    for b in sorted(BUNDLES.iterdir()):
        rs = load_project(b)
        for r in rs.all_records():
            out.append(({"routing": "task-routing"}.get(r.type, r.type), r.data, f"{b.name}/{r.file}"))
    return out


class A01_SchemaParity(unittest.TestCase):
    def test_production_matches_oracle_and_jsonschema(self):
        instances = schema_instances()
        self.assertGreater(len(instances), 400)
        oracle = {n: schema_lite.Validator(s) for n, s in vf.SCHEMAS.items()}
        for name, doc, label in instances:
            prod = FW.validators[name].errors(doc)
            ref = oracle[name].errors(doc)
            self.assertEqual(bool(prod), bool(ref), f"{label}: production {prod} vs oracle {ref}")
            self.assertEqual({e.keyword for e in prod}, {e[1] for e in ref}, label)
            if jsonschema is not None:
                self.assertEqual(not prod, vf._reference_validator(name).is_valid(doc), label)

    def test_invalid_fixtures_fail_for_their_keyword(self):
        for f in sorted((ROOT / "tests/fixtures/invalid").glob("*.json")):
            fx = json.loads(f.read_text())
            self.assertIn(fx["expect_keyword"], {e.keyword for e in FW.validators[fx["schema"]].errors(vf.build(fx))}, f.name)

    def test_errors_are_sorted_and_pointed(self):
        errs = FW.validators["gate"].errors({"gate": 1, "scope": {"kind": "NOPE"}})
        self.assertEqual([e.as_tuple() for e in errs], sorted(e.as_tuple() for e in errs))
        self.assertIn("/scope/kind", {e.path for e in errs})

    def test_schemas_are_the_framework_schemas(self):
        self.assertEqual(FW.schemas, vf.SCHEMAS)
        self.assertEqual(FW.registry, vf.REGISTRY)


class A02_Rfc3339(unittest.TestCase):
    VALID = ["2026-01-01T00:00:00Z", "2026-12-31T23:59:59.999+14:00", "2024-02-29T12:00:00-05:30"]
    # RFC 3339 §5.6 allows lower-case t/z; rfc3339-validator (the jsonschema checker) rejects them and so does
    # production (fail-closed). The frozen Phase-1 oracle accepts them: the only known oracle divergence.
    ORACLE_DIVERGENCE = ["2026-01-01t00:00:00z", "2026-01-01T00:00:00z", "2026-01-01t00:00:00Z"]
    INVALID = ["garbage", "2026-02-30T00:00:00Z", "2025-02-29T00:00:00Z", "2026-01-01T24:00:00Z", "2026-01-01T12:60:00Z",
               "2026-01-01T12:00:60Z", "2026-01-01T12:00:00+24:00", "2026-01-01T12:00:00", "2026-01-01 12:00:00Z",
               "2026-13-01T00:00:00Z", "2026-01-01T00:00:00+05:60", "", "2026-1-1T00:00:00Z"]

    def test_boundaries_match_oracle_and_reference_checker(self):
        try:
            from rfc3339_validator import validate_rfc3339  # type: ignore
        except ImportError:  # pragma: no cover
            validate_rfc3339 = None
        for v in self.VALID + self.INVALID + self.ORACLE_DIVERGENCE:
            self.assertEqual(is_rfc3339_datetime(v), v in self.VALID, v)
            self.assertEqual(is_rfc3339_datetime(v), schema_lite.is_rfc3339_datetime(v) and v not in self.ORACLE_DIVERGENCE, v)
            if validate_rfc3339 is not None:
                self.assertEqual(is_rfc3339_datetime(v), bool(validate_rfc3339(v)), v)

    def test_timestamps_are_asserted_in_records(self):
        rs = bundle("gameplay-feature-ready")
        data(rs, "gate", "G-DASH-TECH")["recorded_at"] = "2026-02-30T10:00:00Z"
        an = project.analyze(rs)
        self.assertIn("SCHEMA_INVALID", error_codes(an))
        self.assertIn(("keyword", '"format"'), next(d for d in an.errors if d.code == "SCHEMA_INVALID").details)


class A03_UnsupportedKeywords(unittest.TestCase):
    def test_fail_loudly(self):
        for schema in ({"type": "object", "minProperties": 1}, {"format": "email"}, {"$ref": "other.json#/x"},
                       {"type": "tuple"}, {"properties": {"a": {"maximum": 3}}}, {"$ref": "#/$defs/missing"}):
            with self.assertRaises(UnsupportedSchemaKeyword, msg=schema):
                SchemaValidator(schema)

    def test_framework_refuses_bad_schema_or_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            for part in ("core", "schemas"):
                shutil.copytree(ROOT / part, Path(tmp) / part)
            shutil.copy(ROOT / "VERSION", Path(tmp) / "VERSION")
            s = json.loads((Path(tmp) / "schemas/gate.schema.json").read_text())
            s["properties"]["gate_id"]["maxLength"] = 64
            (Path(tmp) / "schemas/gate.schema.json").write_text(json.dumps(s))
            with self.assertRaises(UnsupportedSchemaKeyword):
                load_framework(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            for part in ("core", "schemas"):
                shutil.copytree(ROOT / part, Path(tmp) / part)
            shutil.copy(ROOT / "VERSION", Path(tmp) / "VERSION")
            r = json.loads((Path(tmp) / "core/registry.json").read_text())
            r["capture_contexts"].remove("PERFORMANCE_RUNTIME")
            (Path(tmp) / "core/registry.json").write_text(json.dumps(r))
            with self.assertRaises(FrameworkLoadError):
                load_framework(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FrameworkLoadError):
                load_framework(tmp)


# ---------------------------------------------------------------- B  record-set parity

AUTHORITY_CODES = {
    "config-decision-unrelated-asset-subject": "DECISION_SUBJECT_MISMATCH",
    "config-decision-unrelated-task-subject": "DECISION_SUBJECT_MISMATCH",
    "cross-review-by-unrouted-specialist": "CROSS_REVIEWER_NOT_ROUTED",
    "decision-authority-limited-kinds": "UNAUTHORIZED_DECIDER",
    "decision-by-unauthorized-human": "UNAUTHORIZED_DECIDER",
    "decision-superseded": "DECISION_NOT_ACTIVE",
    "decision-transition-targets-other-stage": "LIFECYCLE_TRANSITION_MISMATCH",
    "decision-wrong-kind": "DECISION_KIND_MISMATCH",
    "device-evidence-windows-for-android-project": "TARGET_PLATFORM_NOT_DECLARED",
    "downgrade-animation-decision-on-ui-ux": "DECISION_VALUE_MISMATCH",
    "downgrade-other-revision": "DECISION_VALUE_MISMATCH",
    "downgrade-other-subject": "DECISION_VALUE_MISMATCH",
    "duplicate-decision-authority-ids": "DUPLICATE_CONFIG_ID",
    "duplicate-decision-ids": "DUPLICATE_RECORD_ID",
    "duplicate-evidence-ids": "DUPLICATE_RECORD_ID",
    "duplicate-gate-ids": "DUPLICATE_RECORD_ID",
    "duplicate-project-trigger-ids": "DUPLICATE_CONFIG_ID",
    "duplicate-reviewer-ids": "DUPLICATE_CONFIG_ID",
    "duplicate-routing-ids": "DUPLICATE_RECORD_ID",
    "editor-concurrency-opposite": "DECISION_VALUE_MISMATCH",
    "fake-decision-ref": "DECISION_REF_NOT_FOUND",
    "gate-downgrade-fake-ref": "DECISION_REF_NOT_FOUND",
    "gate-routing-ref-unknown": "ROUTING_REF_NOT_FOUND",
    "golden-exit-ref-fake": "DECISION_REF_NOT_FOUND",
    "gpos-upgrade-from-mismatch": "DECISION_VALUE_MISMATCH",
    "gpos-upgrade-to-mismatch": "DECISION_VALUE_MISMATCH",
    "gpos-upgrade-without-decision": "GPOS_UPGRADE_DECISION_REQUIRED",
    "human-evidence-from-unlisted-human": "HUMAN_EVIDENCE_SOURCE_UNLISTED",
    "human-review-wrong-revision": "HUMAN_REVIEW_SCOPE_MISMATCH",
    "human-review-wrong-scope-kind": "HUMAN_REVIEW_SCOPE_MISMATCH",
    "illegal-transition-skip": "LIFECYCLE_TRANSITION_ILLEGAL",
    "multi-routing-one-declines-target-runtime": "CONDITION_DECLINE_NOT_AUTHORIZED",
    "override-ambiguous": "REVIEW_OVERRIDE_AMBIGUOUS",
    "override-duplicate": "REVIEW_OVERRIDE_DUPLICATE",
    "override-hrr-routed-as-crr": "REVIEW_POLICY_WEAKER_THAN_EFFECTIVE",
    "override-scoped-matching-subject": "REVIEW_POLICY_WEAKER_THAN_EFFECTIVE",
    "parity-yes-condition-declined": "CONDITION_REQUIRED_BY_PARITY",
    "preproduction-to-production-without-waiver": "LIFECYCLE_WAIVER_REQUIRED",
    "presentation-value-mismatch": "DECISION_VALUE_MISMATCH",
    "project-trigger-unknown": "PROJECT_TRIGGER_UNKNOWN",
    "quality-target-opposite": "DECISION_VALUE_MISMATCH",
    "release-device-not-reference-device": "REFERENCE_DEVICE_MISMATCH",
    "release-device-on-non-target-platform": "TARGET_PLATFORM_NOT_DECLARED",
    "release-thin-routing": "WORKFLOW_GATE_UNACCOUNTED",
    "release-two-primary-only-android": "PRIMARY_PLATFORM_COVERAGE_MISSING",
    "review-policy-override-opposite": "DECISION_VALUE_MISMATCH",
    "review-policy-override-other-gate": "DECISION_VALUE_MISMATCH",
    "review-policy-override-scope-mismatch": "DECISION_VALUE_MISMATCH",
    "reviewer-animation-only-approves-ui-ux": "HUMAN_REVIEWER_UNAUTHORIZED",
    "routed-extra-evidence-missing": "ROUTED_EVIDENCE_MISSING",
    "routing-declines-target-runtime-while-parity-undecided": "CONDITION_DECLINE_NOT_AUTHORIZED",
    "routing-downgrade-fake-ref": "DECISION_REF_NOT_FOUND",
    "routing-gate-condition-mismatch": "ROUTING_GATE_MISMATCH",
    "routing-gate-owner-policy-mismatch": "ROUTING_GATE_MISMATCH",
    "routing-gate-raised-after-routing": "GATE_NOT_IN_ROUTING",
    "waiver-wrong-subject": "DECISION_SUBJECT_MISMATCH",
}

RECORD_CODES = {
    "animation-from-stills-only": "PASS_INSUFFICIENT_EVIDENCE",
    "animation-target-differs-without-target-runtime": "PASS_EVIDENCE_MISSING",
    "camera-motion-claim-from-stills": "PASS_EVIDENCE_MISSING",
    "carryover-approved-by-tool": "CARRYOVER_NOT_ACCOUNTABLE",
    "carryover-revision-mismatch": "CARRYOVER_REVISION_MISMATCH",
    "cross-review-stale-revision": "CROSS_REVIEW_STALE",
    "evidence-applicability-by-tool": "APPLICABILITY_NOT_ACCOUNTABLE",
    "evidence-applicability-wrong-subject": "EVIDENCE_SUBJECT_MISMATCH",
    "evidence-build-capture-without-mapping": "EVIDENCE_SUBJECT_MISMATCH",
    "evidence-other-task": "EVIDENCE_SUBJECT_MISMATCH",
    "gameplay-real-time-without-motion": "PASS_EVIDENCE_MISSING",
    "performance-editor-profile-target-claim": "PASS_EVIDENCE_MISSING",
    "performance-material-instrumentation": "INSTRUMENTATION_TIMING_UNUSABLE",
    "stale-evidence-newer-revision": "STALE_EVIDENCE",
    "superseded-evidence": "EVIDENCE_SUPERSEDED",
    "technical-runtime-evidence-from-dcc-render": "INVALID_EVIDENCE_CONTEXT",
    "ui-touch-target-without-device": "PASS_EVIDENCE_MISSING",
    "visual-art-dcc-render-task-scope": "EVIDENCE_CONTEXT_NOT_COUNTING",
}


class B01_AuthorityFixtureParity(unittest.TestCase):
    def test_every_authority_fixture(self):
        self.assertGreaterEqual(len(AUTHORITY), 75)
        for f in AUTHORITY:
            fx = json.loads(f.read_text())
            recs = fixture_records(f)
            ref, _ = reference_verdict(*recs)
            an = project.analyze(from_records(*recs))
            if f.name in EXPECTED_DIVERGENCE:
                self.assertEqual(ref, [], f.name)
                self.assertEqual(error_codes(an), EXPECTED_DIVERGENCE[f.name], f.name)
                continue
            self.assertEqual(bool(ref), production_flags(an), f"{f.name}: reference {ref} vs production {sorted(all_codes(an))}")
            self.assertEqual(bool(fx["expect_problem"]), production_flags(an), f.name)
            if fx["expect_problem"]:
                self.assertIn(AUTHORITY_CODES[f.stem], all_codes(an), f"{f.name}: {sorted(all_codes(an))}")
            self.assertFalse(all_codes(an) & PRODUCTION_ONLY, f.name)

    def test_problem_counts_match_reference(self):
        """One production diagnostic per reference problem (errors plus reference-class blockers)."""
        for f in AUTHORITY:
            if f.name in EXPECTED_DIVERGENCE:
                continue
            recs = fixture_records(f)
            ref = vf.record_set_problems(*recs)
            an = project.analyze(from_records(*recs))
            n = len([d for d in an.errors if d.severity == dg.ERROR]) + \
                len([d for v in an.routing_diagnostics.values() for d in v if d.code in dg.REFERENCE_CLASS_BLOCKERS])
            self.assertEqual(n, len(ref), f"{f.name}: {ref}")

    def test_every_expected_problem_has_a_code(self):
        expected = {f.stem for f in AUTHORITY if json.loads(f.read_text())["expect_problem"]}
        self.assertEqual(expected, set(AUTHORITY_CODES))


class B02_RecordFixtureParity(unittest.TestCase):
    def test_gate_evidence_parity(self):
        self.assertGreaterEqual(len(RECORDS), 28)
        for f in RECORDS:
            fx = json.loads(f.read_text())
            gate = vf.build(fx["gate"])
            by_id = {e["evidence_id"]: e for e in (vf.build(e) for e in fx["evidence"])}
            ref_problems, ref_counting = vf.gate_evidence(gate, by_id)
            diags, counting = assess_gate(FW, gate, by_id)
            diags = [d for d in diags if d.code not in PRODUCTION_ONLY]
            self.assertEqual(len(diags), len(ref_problems), f"{f.name}: {ref_problems} vs {[d.code for d in diags]}")
            self.assertEqual([e["evidence_id"] for e in counting], [e["evidence_id"] for e in ref_counting], f.name)
            if fx["expect_problem"]:
                self.assertIn(RECORD_CODES[f.stem], {d.code for d in diags}, f.name)
            else:
                self.assertEqual(diags, [], f.name)
        self.assertEqual({f.stem for f in RECORDS if json.loads(f.read_text())["expect_problem"]}, set(RECORD_CODES))

    def test_platform_binding_parity_with_config(self):
        for f in AUTHORITY:
            config, _, evidence, gates, _ = fixture_records(f)
            by_id = {e["evidence_id"]: e for e in evidence}
            if len(by_id) != len(evidence):
                continue
            for g in gates:
                self.assertEqual({e["evidence_id"] for e in assess_gate(FW, g, by_id, config)[1]},
                                 {e["evidence_id"] for e in vf.gate_evidence(g, by_id, config)[1]}, f.name)


# ---------------------------------------------------------------- C  readiness parity

EXPECTED_BUNDLES = {
    # name: (valid, {routing: ready})
    "minimal-valid": (True, {}),
    "gameplay-feature-ready": (True, {"FEAT-DASH": True}),
    "gameplay-feature-not-ready": (True, {"FEAT-DASH": False}),
    "golden-cell-ready": (True, {"GC-1": True}),
    "golden-cell-incomplete": (True, {"GC-1": False}),
    "release-multi-platform-ready": (True, {"REL-1.0": True}),
    "release-missing-primary-coverage": (True, {"REL-1.0": False}),
    "invalid-authority": (False, {"FEAT-DASH": False}),
}


class C01_ReadinessParity(unittest.TestCase):
    def test_single_routing_fixtures(self):
        n = 0
        for f in AUTHORITY:
            if f.name in EXPECTED_DIVERGENCE:
                continue
            recs = fixture_records(f)
            if len(recs[4]) != 1:
                continue
            _, ref_ready = reference_verdict(*recs)
            (task, expected), = ref_ready.items()
            self.assertEqual(gpos.evaluate_readiness(from_records(*recs), task).ready, expected, f.name)
            n += 1
        self.assertGreaterEqual(n, 25)

    def test_bundles(self):
        self.assertEqual(sorted(p.name for p in BUNDLES.iterdir()), sorted(EXPECTED_BUNDLES))
        for name, (valid, ready) in EXPECTED_BUNDLES.items():
            rs = bundle(name)
            self.assertEqual(gpos.validate_project(rs).valid, valid, name)
            ref_problems, ref_ready = reference_verdict(*raw(rs))
            self.assertEqual(bool(ref_problems), not valid or any(
                d.code in dg.REFERENCE_CLASS_BLOCKERS for v in project.analyze(rs).routing_diagnostics.values() for d in v), name)
            for task, expected in ready.items():
                result = gpos.evaluate_readiness(rs, task)
                self.assertEqual(result.ready, expected, name)
                self.assertEqual(result.valid_records, valid, name)
                self.assertEqual(ref_ready[task], expected, name)
                self.assertEqual(result.ready, not result.blocking_reasons, name)
                if not valid:
                    self.assertIn("RECORD_SET_INVALID", {d.code for d in result.blocking_reasons}, name)

    def test_gate_only_scope_ready_is_not_used_as_proof(self):
        """The reference's gate-only scope_ready says True with a required gate missing; routed readiness does not."""
        rs = bundle("gameplay-feature-not-ready")
        g = data(rs, "gate", "G-DASH-GD")
        self.assertTrue(vf.scope_ready([g]))
        self.assertFalse(gpos.evaluate_readiness(rs, "FEAT-DASH").ready)


# ---------------------------------------------------------------- D  adversarial regressions

def _add_decision(rs, d):
    rs.decisions.append(Record("decision", d))


def _feature(fn):
    return ("gameplay-feature-ready", "FEAT-DASH", fn)


def _golden(fn):
    return ("golden-cell-ready", "GC-1", fn)


def _release(fn):
    return ("release-multi-platform-ready", "REL-1.0", fn)


def m_fake_decision(rs):
    rs.config.data["lifecycle_decision_ref"] = "D-DOES-NOT-EXIST"


def m_parity_value(rs):
    data(rs, "decision", "D-PARITY")["value"] = "YES"


def m_parity_subject(rs):
    data(rs, "decision", "D-PARITY")["subject"]["ref"] = "another-project"


def m_parity_subject_kind(rs):
    data(rs, "decision", "D-PARITY")["subject"]["kind"] = "TASK"  # right project id, wrong subject kind


def m_trigger_over_ambiguous_overrides(rs):
    """Two overrides apply to GAMEPLAY_DESIGN, but the mandatory GOLDEN_CELL_EXIT trigger takes precedence."""
    cell = {"kind": "GOLDEN_CELL", "ref": "cell-1"}
    rs.config.data["human_review"]["review_policy_overrides"] = [
        {"gate": "GAMEPLAY_DESIGN", "review_policy": "CROSS_REVIEW_REQUIRED", "decision_ref": "D-OVR-1"},
        {"gate": "GAMEPLAY_DESIGN", "review_policy": "HUMAN_REVIEW_REQUIRED", "scope": cell, "decision_ref": "D-OVR-2"}]
    _add_decision(rs, decision("D-OVR-1", "REVIEW_POLICY_OVERRIDE", value={"gate": "GAMEPLAY_DESIGN", "review_policy": "CROSS_REVIEW_REQUIRED"}))
    _add_decision(rs, decision("D-OVR-2", "REVIEW_POLICY_OVERRIDE",
                               value={"gate": "GAMEPLAY_DESIGN", "review_policy": "HUMAN_REVIEW_REQUIRED", "scope": cell}))


def m_ambiguous_overrides_without_trigger(rs):
    scope = {"kind": "FEATURE", "ref": "dash"}
    rs.config.data["human_review"]["review_policy_overrides"] = [
        {"gate": "GAMEPLAY_DESIGN", "review_policy": "ROUTINE", "decision_ref": "D-OVR-1"},
        {"gate": "GAMEPLAY_DESIGN", "review_policy": "ROUTINE", "scope": scope, "decision_ref": "D-OVR-2"}]
    _add_decision(rs, decision("D-OVR-1", "REVIEW_POLICY_OVERRIDE", value={"gate": "GAMEPLAY_DESIGN", "review_policy": "ROUTINE"}))
    _add_decision(rs, decision("D-OVR-2", "REVIEW_POLICY_OVERRIDE", value={"gate": "GAMEPLAY_DESIGN", "review_policy": "ROUTINE", "scope": scope}))


def m_stale_evidence(rs):
    data(rs, "evidence", "EV-DASH-MOTION")["provenance"]["subject_revision"] = "rev-dash-2"


def m_wrong_subject_evidence(rs):
    data(rs, "evidence", "EV-DASH-MOTION")["subject"]["ref"] = "slide"


def m_dcc_as_runtime(rs):
    p = data(rs, "evidence", "EV-DASH-MOTION")["provenance"]
    p["capture_context"] = "DCC_RENDER"
    p["tool_version"] = "dcc 4.2"


def m_runtime_evidence_in_dcc(rs):
    e = data(rs, "evidence", "EV-DASH-MOTION")
    e["type"] = "RUNTIME_EVIDENCE"
    e["provenance"].update(capture_context="DCC_RENDER", tool_version="dcc 4.2")


def m_stale_cross_review(rs):
    data(rs, "gate", "G-DASH-GD")["cross_reviews"][0]["reviewed_revision"] = "rev-dash-2"


def m_missing_routed_gate(rs):
    rs.gates = [r for r in rs.gates if r.id != "G-DASH-TECH"]


def m_routing_gate_disagreement(rs):
    g = data(rs, "gate", "G-DASH-TECH")
    g["owner"] = "technical-art"
    g["assessed_by"]["id"] = "technical-art"


def m_override_weakening(rs):
    rs.config.data["human_review"]["review_policy_overrides"] = [
        {"gate": "GAMEPLAY_DESIGN", "review_policy": "HUMAN_REVIEW_REQUIRED", "decision_ref": "D-OVR"}]
    _add_decision(rs, decision("D-OVR", "REVIEW_POLICY_OVERRIDE", value={"gate": "GAMEPLAY_DESIGN", "review_policy": "HUMAN_REVIEW_REQUIRED"}))


def m_override_stronger_routing_ok(rs):
    rs.config.data["human_review"]["review_policy_overrides"] = [
        {"gate": "GAMEPLAY_DESIGN", "review_policy": "ROUTINE", "decision_ref": "D-OVR"}]
    _add_decision(rs, decision("D-OVR", "REVIEW_POLICY_OVERRIDE", value={"gate": "GAMEPLAY_DESIGN", "review_policy": "ROUTINE"}))


def m_override_scoped_elsewhere_ok(rs):
    scope = {"kind": "FEATURE", "ref": "slide"}
    rs.config.data["human_review"]["review_policy_overrides"] = [
        {"gate": "GAMEPLAY_DESIGN", "review_policy": "HUMAN_REVIEW_REQUIRED", "scope": scope, "decision_ref": "D-OVR"}]
    _add_decision(rs, decision("D-OVR", "REVIEW_POLICY_OVERRIDE",
                               value={"gate": "GAMEPLAY_DESIGN", "review_policy": "HUMAN_REVIEW_REQUIRED", "scope": scope}))


def m_missing_workflow_invariant(rs):
    r = data(rs, "routing", "FEAT-DASH")
    r["required_gates"] = [g for g in r["required_gates"] if g["gate"] != "TECHNICAL"]


def m_workflow_gate_omitted(rs):
    r = data(rs, "routing", "FEAT-DASH")
    r["required_gates"] = [g for g in r["required_gates"] if g["gate"] != "TECHNICAL"]
    r["omitted_gates"].append({"gate": "TECHNICAL", "reason": "trying to skip it"})


def m_missing_routed_evidence(rs):
    data(rs, "routing", "FEAT-DASH")["required_gates"][0]["required_evidence"].append("RUNTIME_EVIDENCE")


def m_ineligible_only_reviewer(rs):
    data(rs, "routing", "FEAT-DASH")["reviewers"] = ["audio-design"]
    data(rs, "gate", "G-DASH-GD")["cross_reviews"][0]["reviewer"] = "audio-design"


def m_routed_but_ineligible_extra(rs):
    data(rs, "routing", "FEAT-DASH")["reviewers"] = ["level-design", "audio-design"]
    data(rs, "gate", "G-DASH-GD")["cross_reviews"].append({"reviewer": "audio-design", "assessment": "PASS", "reviewed_revision": "rev-dash-3"})


def m_unrouted_reviewer(rs):
    data(rs, "gate", "G-DASH-GD")["cross_reviews"][0]["reviewer"] = "game-feel-vfx"


def m_game_director_reviewer(rs):
    data(rs, "gate", "G-DASH-GD")["cross_reviews"][0]["reviewer"] = "game-director"


def m_self_review(rs):
    data(rs, "gate", "G-DASH-GD")["cross_reviews"][0]["reviewer"] = "gameplay-design"


def m_superseded_negative_without_replacement(rs):
    data(rs, "gate", "G-DASH-GD")["cross_reviews"].append(
        {"reviewer": "level-design", "assessment": "CHANGES_REQUIRED", "reviewed_revision": "rev-dash-2", "superseded": True})


def m_superseded_negative_with_replacement_ok(rs):
    data(rs, "gate", "G-DASH-GD")["cross_reviews"].insert(
        0, {"reviewer": "level-design", "assessment": "CHANGES_REQUIRED", "reviewed_revision": "rev-dash-2", "superseded": True})


def m_not_applicable_does_not_satisfy(rs):
    g = data(rs, "gate", "G-DASH-TECH")
    g.update(status="NOT_APPLICABLE", not_applicable_reason="claimed irrelevant")


def m_ambiguous_routed_gate(rs):
    g = copy.deepcopy(data(rs, "gate", "G-DASH-TECH"))
    g["gate_id"] = "G-DASH-TECH-2"
    rs.gates.append(Record("gate", g))


def m_unknown_evidence(rs):
    data(rs, "gate", "G-DASH-TECH")["evidence_refs"].append("EV-NOPE")


def m_superseded_evidence(rs):
    data(rs, "evidence", "EV-DASH-TEST").update(superseded=True, superseded_reason="re-run")


def m_wrong_evidence_type(rs):
    data(rs, "gate", "G-DASH-TECH")["evidence_refs"].append("EV-DASH-MOTION")


def m_routine_pass_by_human(rs):
    data(rs, "gate", "G-DASH-TECH")["assessed_by"] = {"kind": "HUMAN", "id": "creative-lead"}


def _sound_lead(rs):
    rs.config.data["human_review"]["reviewers"].append({"id": "sound-lead", "gates": ["AUDIO"]})


def m_human_assessor_wrong_gate(rs):
    _sound_lead(rs)
    data(rs, "gate", "G-GC-GD")["assessed_by"] = {"kind": "HUMAN", "id": "sound-lead"}


def m_reuse_approver_wrong_gate(rs):
    _sound_lead(rs)
    data(rs, "gate", "G-DASH-TECH")["evidence_carryover"] = [
        {"evidence_ref": "EV-DASH-TEST", "evidence_revision": "rev-dash-3", "justification": "same build",
         "assessed_by": {"kind": "HUMAN", "id": "sound-lead"}}]


def m_human_evidence_wrong_gate(rs):
    _sound_lead(rs)
    ev = copy.deepcopy(data(rs, "evidence", "EV-GC-HUMAN"))
    ev.update(evidence_id="EV-GC-SOUND", source={"kind": "HUMAN", "id": "sound-lead"})
    rs.evidence.append(Record("evidence", ev))
    data(rs, "gate", "G-GC-GD")["evidence_refs"].append("EV-GC-SOUND")


def m_human_review_ref_missing(rs):
    data(rs, "gate", "G-GC-GD")["human_review_ref"] = "G-NOPE"


def m_human_review_other_revision(rs):
    data(rs, "gate", "G-GC-HR")["scope"]["revision"] = "build-6"
    data(rs, "evidence", "EV-GC-HUMAN")["provenance"]["subject_revision"] = "build-6"


def m_golden_accounting(rs):
    r = data(rs, "routing", "GC-1")
    r["omitted_gates"] = [g for g in r["omitted_gates"] if g["gate"] != "AUDIO"]


def m_golden_parity_undecided(rs):
    del rs.config.data["presentation"]
    rs.decisions = [d for d in rs.decisions if d.id != "D-PARITY"]


def m_golden_parity_yes(rs):
    rs.config.data["presentation"]["target_presentation_differs_from_editor"] = "YES"
    data(rs, "decision", "D-PARITY")["value"] = "YES"


def m_condition_unaccounted(rs):
    item = data(rs, "routing", "GC-1")["required_gates"][2]
    item["unapplied_conditions"] = [u for u in item["unapplied_conditions"] if u["condition"] != "ANIMATED_PRESENTATION"]


def m_condition_twice(rs):
    item = data(rs, "routing", "GC-1")["required_gates"][2]
    item["unapplied_conditions"].append(dict(item["unapplied_conditions"][0]))


def m_condition_applied_and_declined(rs):
    item = data(rs, "routing", "GC-1")["required_gates"][2]
    item["applied_conditions"] = ["ANIMATED_PRESENTATION"]


def m_condition_not_defined(rs):
    data(rs, "routing", "GC-1")["required_gates"][2]["unapplied_conditions"].append(
        {"condition": "PERSISTENCE_AFFECTED", "reason": "not a VISUAL_ART condition"})


def m_instrumented_timing(rs):
    data(rs, "evidence", "EV-GC-PERF")["provenance"]["instrumentation"]["timing_impact"] = "MATERIAL"


def m_lifecycle_wrong_stage(rs):
    data(rs, "decision", "D-LC-2")["transition"]["to"] = "PRODUCTION"


def m_release_accounting(rs):
    r = data(rs, "routing", "REL-1.0")
    r["omitted_gates"] = [g for g in r["omitted_gates"] if g["gate"] != "VISUAL_ART"]


def m_wrong_platform(rs):
    data(rs, "evidence", "DEV-W")["provenance"]["target_platform"] = "MACOS"


def m_wrong_device(rs):
    data(rs, "evidence", "DEV-A")["provenance"]["device"] = "Random-Phone"


def m_one_platform_covers_another(rs):
    for eid in ("DEV-W", "PERF-W"):
        data(rs, "evidence", eid)["provenance"].update(target_platform="ANDROID", device="Pixel-X")


def m_secondary_promoted(rs):
    for t in rs.config.data["target_platforms"]:
        if t["platform"] == "IOS":
            t["tier"] = "PRIMARY"


def m_primary_undecided(rs):
    rs.config.data["target_platforms"].append({"platform": "UNDECIDED", "tier": "PRIMARY"})


def m_human_review_gate_missing(rs):
    rs.gates = [r for r in rs.gates if r.id != "G-HR"]


def m_gpos_same_version(rs):
    rs.config.data["gpos_upgrade"] = {"from_version": rs.config.data["gpos_version"]}


def m_unsupported_version(rs):
    rs.config.data["gpos_version"] = "1.0.0-alpha.7"


def m_duplicate_routing_entry(rs):
    r = data(rs, "routing", "FEAT-DASH")
    r["required_gates"].append(copy.deepcopy(r["required_gates"][1]))


def m_required_and_omitted(rs):
    data(rs, "routing", "FEAT-DASH")["omitted_gates"].append({"gate": "TECHNICAL", "reason": "both"})


def m_primary_repeated(rs):
    data(rs, "routing", "FEAT-DASH")["secondary_specialists"].append("gameplay-design")


def m_owner_not_routed(rs):
    data(rs, "routing", "FEAT-DASH")["secondary_specialists"] = []


def m_routing_evidence_below_minimum(rs):
    data(rs, "routing", "FEAT-DASH")["required_gates"][0]["required_evidence"] = ["TEST_EVIDENCE"]


def m_routing_evidence_invalid(rs):
    data(rs, "routing", "FEAT-DASH")["required_gates"][1]["required_evidence"].append("AUDIO_EVIDENCE")


def m_no_cross_reviewer_routed(rs):
    data(rs, "routing", "FEAT-DASH")["reviewers"] = []


def m_non_blocking_open(rs):
    item = data(rs, "routing", "FEAT-DASH")["required_gates"][1]
    item.update(blocking=False, blocking_downgrade_ref="D-DG")
    g = data(rs, "gate", "G-DASH-TECH")
    g.update(blocking=False, blocking_downgrade_ref="D-DG", status="CHANGES_REQUIRED", specialist_assessment="CHANGES_REQUIRED",
             required_changes=["flaky test"])
    _add_decision(rs, decision("D-DG", "BLOCKING_DOWNGRADE", subject={"kind": "FEATURE", "ref": "dash"}, value={"gate": "TECHNICAL"}))


# (name, (bundle, routing, mutation), expected code or None, expect valid records, expect ready, compare with reference)
ADVERSARIAL = [
    ("fake decision reference", _feature(m_fake_decision), "DECISION_REF_NOT_FOUND", False, False, True),
    ("decision value mismatch", _golden(m_parity_value), "DECISION_VALUE_MISMATCH", False, False, True),
    ("decision wrong subject", _golden(m_parity_subject), "DECISION_SUBJECT_MISMATCH", False, False, True),
    ("decision wrong subject kind", _golden(m_parity_subject_kind), "DECISION_SUBJECT_MISMATCH", False, False, True),
    ("trigger precedes ambiguous overrides", _golden(m_trigger_over_ambiguous_overrides), None, True, True, True),
    ("ambiguous overrides without trigger", _feature(m_ambiguous_overrides_without_trigger), "REVIEW_OVERRIDE_AMBIGUOUS", False, False, True),
    ("lifecycle decision enters another stage", _golden(m_lifecycle_wrong_stage), "LIFECYCLE_TRANSITION_MISMATCH", False, False, True),
    ("stale evidence", _feature(m_stale_evidence), "STALE_EVIDENCE", False, False, True),
    ("wrong-subject evidence", _feature(m_wrong_subject_evidence), "EVIDENCE_SUBJECT_MISMATCH", False, False, True),
    ("DCC render as runtime proof", _feature(m_dcc_as_runtime), "EVIDENCE_CONTEXT_NOT_COUNTING", False, False, True),
    ("RUNTIME_EVIDENCE labelled DCC_RENDER", _feature(m_runtime_evidence_in_dcc), "SCHEMA_INVALID", False, False, False),
    ("stale cross-review", _feature(m_stale_cross_review), "CROSS_REVIEW_STALE", False, False, True),
    ("missing routed gate", _feature(m_missing_routed_gate), "MISSING_REQUIRED_GATE", True, False, True),
    ("NOT_APPLICABLE does not satisfy a required gate", _feature(m_not_applicable_does_not_satisfy), "GATE_NOT_PASSED", True, False, True),
    ("routing/gate disagreement", _feature(m_routing_gate_disagreement), "ROUTING_GATE_MISMATCH", False, False, True),
    ("two records for one routed gate", _feature(m_ambiguous_routed_gate), "ROUTED_GATE_AMBIGUOUS", False, False, True),
    ("override weakening", _feature(m_override_weakening), "REVIEW_POLICY_WEAKER_THAN_EFFECTIVE", False, False, True),
    ("routing stronger than override", _feature(m_override_stronger_routing_ok), None, True, True, True),
    ("override scoped to another subject", _feature(m_override_scoped_elsewhere_ok), None, True, True, True),
    ("missing workflow invariant", _feature(m_missing_workflow_invariant), "WORKFLOW_GATE_MISSING", False, False, True),
    ("workflow invariant omitted", _feature(m_workflow_gate_omitted), "WORKFLOW_GATE_OMITTED", False, False, True),
    ("missing routed evidence", _feature(m_missing_routed_evidence), "ROUTED_EVIDENCE_MISSING", True, False, True),
    ("ineligible reviewer only", _feature(m_ineligible_only_reviewer), "CROSS_REVIEW_ELIGIBLE_MISSING", False, False, True),
    ("routed but ineligible reviewer", _feature(m_routed_but_ineligible_extra), "CROSS_REVIEWER_NOT_ELIGIBLE", True, False, True),
    ("unrouted reviewer", _feature(m_unrouted_reviewer), "CROSS_REVIEWER_NOT_ROUTED", True, False, True),
    ("unrouted reviewer cannot close", _feature(m_unrouted_reviewer), "ROUTED_CROSS_REVIEW_MISSING", True, False, True),
    ("game-director as reviewer", _feature(m_game_director_reviewer), "SCHEMA_INVALID", False, False, False),
    ("owner reviews own gate", _feature(m_self_review), "SCHEMA_INVALID", False, False, False),
    ("superseded negative review without replacement", _feature(m_superseded_negative_without_replacement),
     "CROSS_REVIEW_SUPERSEDED_WITHOUT_REPLACEMENT", False, False, False),
    ("superseded negative review with replacement", _feature(m_superseded_negative_with_replacement_ok), None, True, True, True),
    ("unknown evidence", _feature(m_unknown_evidence), "EVIDENCE_NOT_FOUND", False, False, True),
    ("superseded evidence", _feature(m_superseded_evidence), "EVIDENCE_SUPERSEDED", False, False, True),
    ("evidence type not accepted", _feature(m_wrong_evidence_type), "EVIDENCE_TYPE_NOT_ACCEPTED", False, False, True),
    ("ROUTINE PASS by a human", _feature(m_routine_pass_by_human), "SCHEMA_INVALID", False, False, False),
    ("human assessor limited to other gates", _golden(m_human_assessor_wrong_gate), "GATE_ASSESSOR_UNAUTHORIZED", False, False, True),
    ("carryover approved by human for other gates", _feature(m_reuse_approver_wrong_gate), "EVIDENCE_REUSE_APPROVER_UNAUTHORIZED",
     False, False, True),
    ("HUMAN_EVIDENCE from a reviewer of other gates", _golden(m_human_evidence_wrong_gate), "HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED",
     False, False, False),
    ("Human Review reference missing", _golden(m_human_review_ref_missing), "HUMAN_REVIEW_REF_NOT_FOUND", False, False, True),
    ("Human Review of another revision", _golden(m_human_review_other_revision), "HUMAN_REVIEW_SCOPE_MISMATCH", False, False, True),
    ("Golden Cell accounting", _golden(m_golden_accounting), "WORKFLOW_GATE_UNACCOUNTED", False, False, True),
    ("parity UNDECIDED, condition declined", _golden(m_golden_parity_undecided), "CONDITION_DECLINE_NOT_AUTHORIZED", False, False, True),
    ("parity YES, condition declined", _golden(m_golden_parity_yes), "CONDITION_REQUIRED_BY_PARITY", False, False, True),
    ("condition unaccounted", _golden(m_condition_unaccounted), "CONDITION_UNACCOUNTED", False, False, True),
    ("condition twice", _golden(m_condition_twice), "CONDITION_DUPLICATE", False, False, True),
    ("condition applied and declined", _golden(m_condition_applied_and_declined), "CONDITION_APPLIED_AND_DECLINED", False, False, True),
    ("declined condition not defined", _golden(m_condition_not_defined), "CONDITION_NOT_DEFINED", False, False, True),
    ("instrumented timing", _golden(m_instrumented_timing), "INSTRUMENTATION_TIMING_UNUSABLE", False, False, True),
    ("Release accounting", _release(m_release_accounting), "WORKFLOW_GATE_UNACCOUNTED", False, False, True),
    ("wrong platform", _release(m_wrong_platform), "TARGET_PLATFORM_NOT_DECLARED", False, False, True),
    ("wrong device", _release(m_wrong_device), "REFERENCE_DEVICE_MISMATCH", False, False, True),
    ("one platform cannot cover another", _release(m_one_platform_covers_another), "PRIMARY_PLATFORM_COVERAGE_MISSING", True, False, True),
    ("SECONDARY promoted to PRIMARY now blocks", _release(m_secondary_promoted), "PRIMARY_PLATFORM_COVERAGE_MISSING", True, False, True),
    ("PRIMARY platform UNDECIDED", _release(m_primary_undecided), "PRIMARY_PLATFORM_UNDECIDED", True, False, True),
    ("Human Review gate missing", _release(m_human_review_gate_missing), "HUMAN_REVIEW_MISSING", True, False, True),
    ("GPOS upgrade to the same version", _feature(m_gpos_same_version), "GPOS_UPGRADE_SAME_VERSION", False, False, True),
    ("project pins another GPOS version", _feature(m_unsupported_version), "UNSUPPORTED_GPOS_VERSION", False, False, False),
    ("gate routed twice", _feature(m_duplicate_routing_entry), "ROUTING_GATE_DUPLICATE", False, False, True),
    ("gate required and omitted", _feature(m_required_and_omitted), "ROUTING_GATE_REQUIRED_AND_OMITTED", False, False, True),
    ("primary repeated as secondary", _feature(m_primary_repeated), "ROUTING_PRIMARY_REPEATED", False, False, True),
    ("gate owner not routed", _feature(m_owner_not_routed), "ROUTING_OWNER_NOT_ROUTED", False, False, True),
    ("routing evidence below registry minimum", _feature(m_routing_evidence_below_minimum), "ROUTING_EVIDENCE_BELOW_MINIMUM",
     False, False, True),
    ("routing evidence invalid for gate", _feature(m_routing_evidence_invalid), "ROUTING_EVIDENCE_INVALID_FOR_GATE", False, False, True),
    ("no routed cross-reviewer", _feature(m_no_cross_reviewer_routed), "ROUTING_CROSS_REVIEWER_MISSING", False, False, True),
    ("downgraded gate still open", _feature(m_non_blocking_open), "NON_BLOCKING_GATE_OPEN", True, True, True),
]


class D01_AdversarialRegressions(unittest.TestCase):
    def test_named_attacks(self):
        for name, (bundle_name, task, fn), code, valid, ready, compare in ADVERSARIAL:
            rs = bundle(bundle_name)
            fn(rs)
            an = project.analyze(rs)
            result = gpos.evaluate_readiness(rs, task)
            self.assertEqual(an.valid, valid, f"{name}: {sorted(all_codes(an))}")
            self.assertEqual(result.valid_records, valid, name)
            self.assertEqual(result.ready, ready, f"{name}: {[d.code for d in result.blocking_reasons]}")
            if code is None:
                self.assertFalse({d.code for d in an.errors if d.severity == dg.ERROR} |
                                 {d.code for d in result.blocking_reasons}, name)
            else:
                self.assertIn(code, all_codes(an), f"{name}: {sorted(all_codes(an))}")
            if compare:  # the frozen reference agrees on validity and readiness
                ref_problems, ref_ready = reference_verdict(*raw(rs))
                self.assertEqual(bool(ref_problems), production_flags(an), f"{name}: {ref_problems}")
                self.assertEqual(ref_ready[task], result.ready, name)

    def test_direct_gate_rules_behind_the_schema(self):
        """Rules the schema already enforces are still enforced by the domain model (defence in depth)."""
        rs = bundle("gameplay-feature-ready")
        by_id = {r.id: r.data for r in rs.evidence}
        g = copy.deepcopy(data(rs, "gate", "G-DASH-GD"))
        g["cross_reviews"][0]["reviewer"] = "game-director"
        self.assertIn("CROSS_REVIEW_BY_NEVER_REVIEWER", {d.code for d in assess_gate(FW, g, by_id)[0]})
        g["cross_reviews"][0]["reviewer"] = "gameplay-design"
        self.assertIn("CROSS_REVIEW_SELF", {d.code for d in assess_gate(FW, g, by_id)[0]})
        g = copy.deepcopy(data(rs, "gate", "G-DASH-TECH"))
        g["owner"] = "ui-ux"
        codes = {d.code for d in assess_gate(FW, g, by_id)[0]}
        self.assertTrue({"GATE_OWNER_NOT_PERMITTED", "ASSESSOR_NOT_OWNER"} <= codes, codes)
        g = copy.deepcopy(data(rs, "gate", "G-DASH-TECH"))
        g["assessed_by"] = {"kind": "HUMAN", "id": "creative-lead"}
        self.assertEqual({d.code for d in assess_gate(FW, g, by_id)[0]}, {"ROUTINE_PASS_NOT_OWNER"})
        r = copy.deepcopy(data(rs, "routing", "FEAT-DASH"))
        r["review_triggers"] = ["MAJOR_BASELINE"]  # the schema would also force HUMAN_REVIEW_REQUIRED here
        self.assertEqual(authority.effective_policy_floor(FW, rs.config.data, r, r["required_gates"][0])[0], "HUMAN_REVIEW_REQUIRED")
        self.assertIn("REVIEW_POLICY_WEAKER_THAN_EFFECTIVE", {d.code for d in authority.routing_authority(FW, rs.config.data, r)})
        self.assertIsNone(authority.effective_policy_floor(FW, rs.config.data, r, r["required_gates"][1])[0])  # TECHNICAL not subjective
        r = copy.deepcopy(data(rs, "routing", "FEAT-DASH"))
        r["required_gates"][0]["review_policy"] = "HUMAN_REVIEW_REQUIRED"
        self.assertIn("ROUTING_HUMAN_REVIEWER_MISSING", {d.code for d in routing_structure(FW, r)})

    def test_every_diagnostic_code_is_exercised(self):
        source = Path(__file__).read_text()
        missing = [c for c in dg.CODES if f'"{c}"' not in source]
        self.assertEqual(missing, [])


# ---------------------------------------------------------------- E  reference independence

class E01_NoReferenceImport(unittest.TestCase):
    def test_production_never_loads_the_oracle(self):
        code = ("import sys; sys.dont_write_bytecode = True; sys.path.insert(0, %r); import gpos, gpos.cli; "
                "from gpos.validation import project; bad = sorted(m for m in sys.modules if m.split('.')[0] in "
                "('validate_framework', 'schema_lite', 'tests', 'jsonschema', 'unittest', 'socket', 'urllib', 'http', 'ssl')); "
                "print(bad)") % str(ROOT)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "[]")

    def test_boundary(self):
        self.assertEqual(vf.phase_boundary_problems(), [])
        for p in (ROOT / "gpos").rglob("*.py"):
            text = p.read_text()
            for word in ("unity", "blender", "ffmpeg", "adb ", "github", "anthropic", "openai", "codex"):
                self.assertNotIn(word, text.lower(), f"{p.relative_to(ROOT)} mentions {word}")


# ---------------------------------------------------------------- L  loading

def _tmp_bundle(name="gameplay-feature-ready"):
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(BUNDLES / name, tmp / name)
    return tmp, tmp / name, tmp / name / ".game" / "gpos"


class L01_Loading(unittest.TestCase):
    def tearDown(self):
        for t in getattr(self, "_tmps", []):
            shutil.rmtree(t, ignore_errors=True)

    def tmp(self, name="gameplay-feature-ready"):
        tmp, project_dir, bundle_dir = _tmp_bundle(name)
        self._tmps = getattr(self, "_tmps", []) + [tmp]
        return project_dir, bundle_dir

    def test_project_dir_or_bundle_dir(self):
        project_dir, bundle_dir = self.tmp()
        self.assertTrue(gpos.validate_project(load_project(project_dir)).valid)
        self.assertTrue(gpos.validate_project(load_project(bundle_dir)).valid)
        with self.assertRaises(gpos.BundleNotFound):
            load_project(project_dir / "nope")
        with self.assertRaises(gpos.BundleNotFound):
            load_project(ROOT / "core")

    def test_malformed_files_fail_clearly(self):
        project_dir, b = self.tmp()
        (b / "gates" / "broken.json").write_text("{ not json")
        (b / "evidence" / "list.json").write_text("[1, 2]")
        (b / "gates" / "notes.txt").write_text("x")
        (b / "stray").mkdir()
        (b / "decisions" / "nested").mkdir()
        r = gpos.validate_project(load_project(project_dir))
        self.assertFalse(r.valid)
        found = {(d.code, d.file) for d in r.diagnostics}
        for expected in [("RECORD_INVALID_JSON", "gates/broken.json"), ("RECORD_NOT_OBJECT", "evidence/list.json"),
                         ("UNKNOWN_RECORD_FILE", "gates/notes.txt"), ("UNKNOWN_RECORD_FILE", "stray"),
                         ("UNKNOWN_RECORD_FILE", "decisions/nested")]:
            self.assertIn(expected, found)
        self.assertEqual(r.summary["stages_completed"], [])

    def test_unloadable_routing_is_not_ready_rather_than_not_found(self):
        project_dir, b = self.tmp()
        (b / "routings" / "FEAT-DASH.json").write_text("{ truncated")
        r = gpos.evaluate_readiness(load_project(project_dir), "FEAT-DASH")
        self.assertEqual((r.ready, r.valid_records), (False, False))
        self.assertEqual({d.code for d in r.blocking_reasons}, {"RECORD_SET_INVALID"})
        self.assertEqual(run_cli("readiness", "--project", str(project_dir), "--routing", "FEAT-DASH")[0], 1)
        (b / "routings" / "FEAT-DASH.json").unlink()
        with self.assertRaises(gpos.RoutingNotFound):
            gpos.evaluate_readiness(load_project(project_dir), "FEAT-DASH")

    def test_missing_config(self):
        project_dir, b = self.tmp()
        (b / "project-config.json").unlink()
        r = gpos.validate_project(load_project(project_dir))
        self.assertEqual([d.code for d in r.diagnostics], ["PROJECT_CONFIG_MISSING"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read unreadable files")
    def test_unreadable_file(self):
        project_dir, b = self.tmp()
        p = b / "gates" / "G-DASH-TECH.json"
        p.chmod(0)
        try:
            r = gpos.validate_project(load_project(project_dir))
        finally:
            p.chmod(0o644)
        self.assertIn(("RECORD_UNREADABLE", "gates/G-DASH-TECH.json"), {(d.code, d.file) for d in r.diagnostics})

    def test_hidden_files_ignored_and_ids_from_contents(self):
        project_dir, b = self.tmp()
        (b / ".DS_Store").write_text("junk")
        (b / "gates" / ".swp").write_text("junk")
        (b / ".cache").mkdir()
        (b / "gates" / "G-DASH-TECH.json").rename(b / "gates" / "zzz-anything.json")
        rs = load_project(project_dir)
        self.assertTrue(gpos.evaluate_readiness(rs, "FEAT-DASH").ready)
        self.assertEqual([r.file for r in rs.gates], ["gates/G-DASH-GD.json", "gates/zzz-anything.json"])

    def test_duplicate_ids_across_files(self):
        project_dir, b = self.tmp()
        shutil.copy(b / "gates" / "G-DASH-TECH.json", b / "gates" / "copy-of-tech.json")
        r = gpos.validate_project(load_project(project_dir))
        dup = [d for d in r.diagnostics if d.code == "DUPLICATE_RECORD_ID"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0].related, ("gates/G-DASH-TECH.json", "gates/copy-of-tech.json"))
        self.assertNotIn("identifiers", r.summary["stages_completed"])

    def test_schema_invalid_record_stops_cross_record_rules(self):
        project_dir, b = self.tmp()
        g = json.loads((b / "gates" / "G-DASH-TECH.json").read_text())
        g["status"] = "DONE"
        (b / "gates" / "G-DASH-TECH.json").write_text(json.dumps(g))
        r = gpos.validate_project(load_project(project_dir))
        self.assertEqual({d.code for d in r.diagnostics}, {"SCHEMA_INVALID"})
        self.assertEqual(r.summary["stages_completed"], ["load"])
        self.assertIn("gates/G-DASH-TECH.json", {d.file for d in r.diagnostics})


# ---------------------------------------------------------------- R  read-only, determinism, CLI, performance, coverage

class R01_ReadOnly(unittest.TestCase):
    def test_validator_never_modifies_records(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(BUNDLES, tmp / "bundles")
            before = snapshot(tmp)
            for name, (_, ready) in EXPECTED_BUNDLES.items():
                path = tmp / "bundles" / name
                rs = load_project(path)
                frozen = copy.deepcopy([r.data for r in rs.all_records()])
                gpos.validate_project(rs)
                for task in ready:
                    gpos.evaluate_readiness(rs, task)
                    gpos.validate_routing(rs, task)
                    for fmt in ("text", "json"):
                        run_cli("readiness", "--project", str(path), "--routing", task, "--format", fmt)
                self.assertEqual([r.data for r in rs.all_records()], frozen, name)
                for fmt in ("text", "json"):
                    run_cli("validate", "--project", str(path), "--format", fmt)
            self.assertEqual(snapshot(tmp), before)
        finally:
            shutil.rmtree(tmp)


class R02_Determinism(unittest.TestCase):
    def test_same_output_twice_and_independent_of_record_order(self):
        for name, (_, ready) in EXPECTED_BUNDLES.items():
            path = str(BUNDLES / name)
            a = run_cli("validate", "--project", path, "--format", "json")
            self.assertEqual(a, run_cli("validate", "--project", path, "--format", "json"), name)
            for task in ready:
                r1 = run_cli("readiness", "--project", path, "--routing", task, "--format", "json")
                self.assertEqual(r1, run_cli("readiness", "--project", path, "--routing", task, "--format", "json"))
        rng = random.Random(7)
        for name in ("release-missing-primary-coverage", "golden-cell-incomplete", "invalid-authority"):
            config, decisions, evidence, gates, routings = raw(bundle(name))
            base = [d.to_dict() for d in gpos.validate_project(from_records(config, decisions, evidence, gates, routings)).diagnostics]
            task = routings[0]["task_id"]
            base_r = gpos.evaluate_readiness(from_records(config, decisions, evidence, gates, routings), task).to_dict()
            for _ in range(5):
                for lst in (decisions, evidence, gates):
                    rng.shuffle(lst)
                rs = from_records(config, decisions, evidence, gates, routings)
                self.assertEqual([d.to_dict() for d in gpos.validate_project(rs).diagnostics], base, name)
                self.assertEqual(gpos.evaluate_readiness(rs, task).to_dict(), base_r, name)

    def test_json_has_versions_and_no_timestamps(self):
        code, out = run_cli("readiness", "--project", str(BUNDLES / "golden-cell-ready"), "--routing", "GC-1", "--format", "json")
        doc = json.loads(out)
        self.assertEqual((code, doc["verdict"]), (0, "READY"))
        self.assertEqual(doc["validator_version"], gpos.__version__)
        self.assertEqual(doc["gpos_version"], FW.version)
        self.assertEqual(doc["project_id"], "synthetic-project")
        self.assertEqual(gpos.__version__, (ROOT / "VERSION").read_text().strip())

        def keys(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield k
                    yield from keys(v)
            elif isinstance(node, list):
                for v in node:
                    yield from keys(v)
        self.assertFalse([k for k in keys(doc) if re.search(r"time|date|generated|_at$", k)])
        self.assertNotIn(str(ROOT), out)


class R03_Cli(unittest.TestCase):
    def test_exit_codes(self):
        b = lambda n: str(BUNDLES / n)
        cases = [
            (("validate", "--project", b("minimal-valid")), 0, "VALID"),
            (("validate", "--project", b("invalid-authority")), 1, "INVALID"),
            (("validate", "--project", b("gameplay-feature-not-ready")), 0, "VALID"),
            (("readiness", "--project", b("gameplay-feature-ready"), "--routing", "FEAT-DASH"), 0, "READY"),
            (("readiness", "--project", b("gameplay-feature-not-ready"), "--routing", "FEAT-DASH"), 2, "NOT_READY"),
            (("readiness", "--project", b("invalid-authority"), "--routing", "FEAT-DASH"), 1, "NOT_READY"),
            (("readiness", "--project", b("release-missing-primary-coverage"), "--routing", "REL-1.0"), 2, "NOT_READY"),
            (("readiness", "--project", b("release-multi-platform-ready"), "--routing", "REL-1.0"), 0, "READY"),
            (("validate", "--project", b("gameplay-feature-ready"), "--routing", "FEAT-DASH"), 0, "VALID"),
        ]
        for argv, code, verdict in cases:
            got, out = run_cli(*argv, "--format", "json")
            self.assertEqual((got, json.loads(out)["verdict"]), (code, verdict), argv)
            self.assertEqual(run_cli(*argv)[0], code, argv)

    def test_errors_exit_3_without_traces(self):
        b = str(BUNDLES / "gameplay-feature-ready")
        for argv, err in [(("readiness", "--project", b, "--routing", "NOPE"), "ROUTING_NOT_FOUND"),
                          (("validate", "--project", "/definitely/not/here"), "BUNDLE_NOT_FOUND"),
                          (("validate", "--project", b, "--format", "yaml"), None),
                          (("readiness", "--project", b), None), (("fix", "--project", b), None), ((), None)]:
            code, out = run_cli(*argv, "--format", "json") if "--format" not in argv else run_cli(*argv)
            self.assertEqual(code, 3, argv)
            if err:
                self.assertEqual(json.loads(out)["error"]["code"], err)
            self.assertNotIn("Traceback", out)

    def test_tool_and_internal_failures_are_distinguishable(self):
        b = str(BUNDLES / "gameplay-feature-ready")
        original_fw, original_validate = cli.load_framework, cli.validate_project
        try:
            cli.load_framework = lambda: (_ for _ in ()).throw(FrameworkLoadError("registry is missing keys ['gates']"))
            code, out = run_cli("validate", "--project", b, "--format", "json")
            self.assertEqual((code, json.loads(out)["error"]["code"]), (3, "FRAMEWORK_LOAD_ERROR"))
            cli.load_framework = original_fw
            cli.validate_project = lambda *a: 1 / 0
            code, out = run_cli("validate", "--project", b, "--format", "json")
            self.assertEqual((code, json.loads(out)["error"]["code"]), (3, "INTERNAL_ERROR"))
            self.assertNotIn("Traceback", out)
        finally:
            cli.load_framework, cli.validate_project = original_fw, original_validate

    def test_no_mutating_commands(self):
        choices = cli.build_parser()._subparsers._group_actions[0].choices
        self.assertEqual(sorted(choices), ["readiness", "validate"])

    def test_module_entry_point(self):
        out = subprocess.run([sys.executable, "-B", "-m", "gpos.validator", "--version"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn(gpos.__version__, out.stdout)


def synthetic_project(n_features):
    """A valid project with n gameplay-feature routings (2 gates and 2 evidence records each) plus decisions."""
    rs = bundle("gameplay-feature-ready")
    config, decisions, evidence, gates, routings = raw(rs)
    out = {"decisions": decisions, "evidence": [], "gates": [], "routings": []}
    for i in range(n_features):
        sfx = f"-{i:05d}"
        subj = {"kind": "FEATURE", "ref": f"dash{sfx}"}
        r = copy.deepcopy(routings[0])
        r.update(task_id=f"FEAT{sfx}", subject=subj)
        out["routings"].append(r)
        for e in evidence:
            e2 = copy.deepcopy(e)
            e2.update(evidence_id=e["evidence_id"] + sfx, subject=subj)
            out["evidence"].append(e2)
        for g in gates:
            g2 = copy.deepcopy(g)
            g2.update(gate_id=g["gate_id"] + sfx, routing_ref=f"FEAT{sfx}", evidence_refs=[x + sfx for x in g["evidence_refs"]])
            g2["scope"]["ref"] = subj["ref"]
            out["gates"].append(g2)
    return config, out


class R04_Performance(unittest.TestCase):
    def test_thousands_of_records_scale_linearly(self):
        timings = {}
        for n in (250, 1000):
            config, recs = synthetic_project(n)
            rs = from_records(config, recs["decisions"], recs["evidence"], recs["gates"], recs["routings"])
            t0 = time.perf_counter()
            result = gpos.validate_project(rs)
            ready = gpos.evaluate_readiness(rs, f"FEAT-{n - 1:05d}")
            timings[n] = time.perf_counter() - t0
            self.assertTrue(result.valid and ready.ready, n)
            self.assertEqual(result.summary["records"]["gate"], 2 * n)
        self.assertLess(timings[1000], 60)
        self.assertLess(timings[1000] / max(timings[250], 1e-3), 10, timings)  # 4x records; quadratic would be ~16x

    def test_bundle_on_disk(self):
        config, recs = synthetic_project(300)
        tmp = Path(tempfile.mkdtemp())
        try:
            b = tmp / ".game" / "gpos"
            for folder, key in (("decisions", "decision_id"), ("routings", "task_id"), ("gates", "gate_id"), ("evidence", "evidence_id")):
                (b / folder).mkdir(parents=True)
                for r in recs[folder]:
                    (b / folder / f"{r[key]}.json").write_text(json.dumps(r))
            (b / "project-config.json").write_text(json.dumps(config))
            rs = load_project(tmp)
            self.assertEqual(len(rs.all_records()), 1 + len(recs["decisions"]) + 300 * 5)
            self.assertTrue(gpos.validate_project(rs).valid)
        finally:
            shutil.rmtree(tmp)


class R05_ContractCoverage(unittest.TestCase):
    """tools/validator/README.md maps every GOVERNANCE §12 requirement (1–44) to codes and tests."""

    def test_matrix(self):
        text = (ROOT / "tools/validator/README.md").read_text()
        rows = re.findall(r"^\| (\d+) \| (.*?) \| (.*?) \| (.*?) \|$", text, re.M)
        self.assertEqual(sorted(int(r[0]) for r in rows), list(range(1, 45)))
        source = Path(__file__).read_text()
        for number, _, codes, tests in rows:
            named = re.findall(r"`([A-Z][A-Z0-9_]+)`", codes)
            self.assertTrue(named or "schema" in codes.lower(), number)
            for c in named:
                self.assertIn(c, dg.CODES, f"§12.{number}: {c}")
            for t in re.findall(r"`([A-Za-z0-9_]+)`", tests):
                self.assertTrue(re.search(rf"(class|def) {t}\b", source + (ROOT / "tests/validate_framework.py").read_text()),
                                f"§12.{number}: test {t} not found")

    def test_every_code_documented(self):
        text = (ROOT / "tools/validator/README.md").read_text()
        self.assertEqual([c for c in dg.CODES if f"`{c}`" not in text], [])


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    print(f"GPOS production validator {gpos.__version__} tests (jsonschema cross-check: {'on' if jsonschema else 'off — not installed'})")
    sys.exit(0 if result.wasSuccessful() else 1)
