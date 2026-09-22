#!/usr/bin/env python3
"""Regenerate the synthetic project bundles in tests/fixtures/bundles/ (generic data, no real project).

    python3 tests/generate_bundles.py

Each bundle is a complete `.game/gpos/` record bundle. The Release bundles are built from
the frozen Phase-1 authority fixtures (release-two-primary-*.json) so they stay equivalent
to what the reference model already accepts or rejects. Output is deterministic.
"""

import copy
import json
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import validate_framework as vf  # noqa: E402  (test-side helper `build`; production code never imports tests)

OUT = ROOT / "tests" / "fixtures" / "bundles"
T0 = "2026-03-01T10:00:00Z"


def load(rel):
    return json.loads((ROOT / rel).read_text())


def decision(did, kind, subject_ref="synthetic-project", by="creative-lead", **payload):
    d = load("examples/example-decision-record.json")
    d.pop("transition")
    d.update({"decision_id": did, "kind": kind, "subject": {"kind": "PROJECT", "ref": subject_ref},
              "decided_by": {"kind": "HUMAN", "id": by}, "decided_at": T0, "affects": ["project-config"]})
    d.update(payload)
    return d


def config(**changes):
    c = load("examples/minimal-project-config.json")
    c["project"] = {"id": "synthetic-project", "name": "Synthetic Project"}
    c["target_platforms"] = [{"platform": "ANDROID", "tier": "PRIMARY", "reference_devices": ["Pixel-X"]}]
    c["human_review"] = {"reviewers": [{"id": "creative-lead", "role": "Holds creative authority"}]}
    c.update(changes)
    return c


def evidence(eid, etype, subject, revision, ctx, source=("AGENT", "qa-performance"), **prov):
    e = load("examples/example-evidence-record.json")
    p = {"capture_context": ctx, "subject_revision": revision}
    if ctx in ("TARGET_RUNTIME", "DIAGNOSTIC_RUNTIME", "PERFORMANCE_RUNTIME"):
        p["build_id"] = f"build-{revision}"
    if ctx in ("DCC_RENDER", "EDITOR"):
        p["tool_version"] = "synthetic-engine 1.0"
    p.update(prov)
    e.update({"evidence_id": eid, "type": etype, "summary": f"Synthetic {etype} for {subject['ref']}.", "subject": dict(subject),
              "source": {"kind": source[0], "id": source[1]}, "created_at": T0, "provenance": p,
              "artifacts": [{"uri": f"evidence/{eid}.bin", "media_type": "application/octet-stream", "description": "synthetic"}],
              "limitations": ["Synthetic record for validator tests"], "superseded": False})
    e.pop("supersedes", None)
    return e


def gate(gid, gname, owner, subject, revision, routing_ref, refs, policy="ROUTINE", status="PASS", **extra):
    g = {"schema_version": "1.0", "gate_id": gid, "gate": gname, "scope": {"kind": subject["kind"], "ref": subject["ref"], "revision": revision},
         "routing_ref": routing_ref, "status": status, "owner": owner, "blocking": True, "review_policy": policy,
         "applied_conditions": [], "evidence_refs": refs, "recorded_at": T0}
    if owner == "HUMAN":
        g["assessed_by"] = {"kind": "HUMAN", "id": "creative-lead"}
        g["recorded_by"] = {"kind": "AGENT", "id": "game-director"}
    else:
        g["assessed_by"] = {"kind": "AGENT", "id": owner}
        g["specialist_assessment"] = "PASS" if status == "PASS" else "CHANGES_REQUIRED"
    if policy == "ROUTINE":
        g["routine_basis"] = "Bounded work inside the approved routing"
    if status == "CHANGES_REQUIRED":
        g["required_changes"] = ["Synthetic change request"]
    g.update(extra)
    return g


def write_bundle(name, cfg, decisions=(), routings=(), gates=(), evidence_=()):
    base = OUT / name / ".game" / "gpos"
    base.mkdir(parents=True)
    (base / "project-config.json").write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
    for folder, records, key in (("decisions", decisions, "decision_id"), ("routings", routings, "task_id"),
                                 ("gates", gates, "gate_id"), ("evidence", evidence_, "evidence_id")):
        (base / folder).mkdir()
        for r in records:
            (base / folder / f"{r[key]}.json").write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------- gameplay feature

FEAT = {"kind": "FEATURE", "ref": "dash"}
FREV = "rev-dash-3"
LIFECYCLE = decision("D-LC-1", "LIFECYCLE_TRANSITION", transition={"from": "CONCEPT", "to": "PRE_PRODUCTION"})


def feature_routing(extra_gd_evidence=()):
    return {
        "schema_version": "1.0", "task_id": "FEAT-DASH", "subject": FEAT, "workflow": "gameplay-feature",
        "description": "Add a short-range dash with a cooldown to the player character.",
        "lifecycle_stage": "PRE_PRODUCTION", "primary_specialist": "gameplay-design", "secondary_specialists": ["qa-performance"],
        "reviewers": ["level-design"],
        "required_gates": [
            {"gate": "GAMEPLAY_DESIGN", "owner": "gameplay-design", "blocking": True, "review_policy": "CROSS_REVIEW_REQUIRED",
             "applied_conditions": ["REAL_TIME_BEHAVIOUR"], "required_evidence": ["MOTION_EVIDENCE", *extra_gd_evidence]},
            {"gate": "TECHNICAL", "owner": "qa-performance", "blocking": True, "review_policy": "ROUTINE",
             "routine_basis": "Bounded change inside the movement system; no persisted data",
             "unapplied_conditions": [{"condition": "PERSISTENCE_AFFECTED", "reason": "The dash stores no saved data."}],
             "required_evidence": ["TEST_EVIDENCE"]},
        ],
        "omitted_gates": [{"gate": "GAME_FEEL_VFX", "reason": "Dash feedback polish is a later task."}],
        "review_triggers": [],
        "human_review": {"required": False, "not_required_reason": "Bounded feature inside the approved design pillars."},
        "missing_decisions": [], "editor_write_lock": "gameplay-design", "out_of_scope": ["Dash VFX"],
        "rationale": "Gameplay feel of a movement verb is owned by gameplay-design; level-design checks traversal impact.",
    }


FEATURE_EVIDENCE = [
    evidence("EV-DASH-MOTION", "MOTION_EVIDENCE", FEAT, FREV, "DIAGNOSTIC_RUNTIME", source=("AGENT", "gameplay-design")),
    evidence("EV-DASH-TEST", "TEST_EVIDENCE", FEAT, FREV, "AUTOMATED_TEST", source=("CI", "ci-runner")),
]
GD_PASS = gate("G-DASH-GD", "GAMEPLAY_DESIGN", "gameplay-design", FEAT, FREV, "FEAT-DASH", ["EV-DASH-MOTION"],
               policy="CROSS_REVIEW_REQUIRED", applied_conditions=["REAL_TIME_BEHAVIOUR"],
               cross_reviews=[{"reviewer": "level-design", "assessment": "PASS", "reviewed_revision": FREV}])
TECH_PASS = gate("G-DASH-TECH", "TECHNICAL", "qa-performance", FEAT, FREV, "FEAT-DASH", ["EV-DASH-TEST"])
FEATURE_CONFIG = config(lifecycle_stage="PRE_PRODUCTION", lifecycle_decision_ref="D-LC-1")

# ---------------------------------------------------------------- Golden Gameplay Cell

CELL = {"kind": "GOLDEN_CELL", "ref": "cell-1"}
CREV = "build-7"
OMIT_REASON = "Not part of the first cell: the cell has no characters, authored camera, level layout, UI or audio yet."


def golden_routing():
    tpd_no = {"condition": "TARGET_PRESENTATION_DIFFERS", "reason": "Project parity decision: editor matches target presentation."}
    return {
        "schema_version": "1.0", "task_id": "GC-1", "subject": CELL, "workflow": "golden-gameplay-cell",
        "description": "First Golden Gameplay Cell: movement and dash loop at target quality on the reference device.",
        "lifecycle_stage": "GOLDEN_CELL", "primary_specialist": "gameplay-design",
        "secondary_specialists": ["art-direction", "game-feel-vfx", "qa-performance"], "reviewers": ["HUMAN"],
        "required_gates": [
            {"gate": "TECHNICAL", "owner": "qa-performance", "blocking": True, "review_policy": "ROUTINE",
             "routine_basis": "Regression suite on the cell build", "required_evidence": ["TEST_EVIDENCE"],
             "unapplied_conditions": [{"condition": "PERSISTENCE_AFFECTED", "reason": "The cell has no save data."}]},
            {"gate": "GAMEPLAY_DESIGN", "owner": "gameplay-design", "blocking": True, "review_policy": "HUMAN_REVIEW_REQUIRED",
             "applied_conditions": ["REAL_TIME_BEHAVIOUR"], "required_evidence": ["MOTION_EVIDENCE"]},
            {"gate": "VISUAL_ART", "owner": "art-direction", "blocking": True, "review_policy": "HUMAN_REVIEW_REQUIRED",
             "required_evidence": ["VISUAL_EVIDENCE"],
             "unapplied_conditions": [{"condition": "ANIMATED_PRESENTATION", "reason": "Static environment dressing only."}, tpd_no]},
            {"gate": "GAME_FEEL_VFX", "owner": "game-feel-vfx", "blocking": True, "review_policy": "HUMAN_REVIEW_REQUIRED",
             "required_evidence": ["MOTION_EVIDENCE"],
             "unapplied_conditions": [{"condition": "FEEDBACK_INCLUDES_SOUND", "reason": "Audio is out of the first cell."}, tpd_no]},
            {"gate": "PERFORMANCE", "owner": "qa-performance", "blocking": True, "review_policy": "ROUTINE",
             "routine_basis": "Measure against the locked budget", "applied_conditions": ["TARGET_PLATFORM_PERFORMANCE_CLAIM"],
             "required_evidence": ["PERFORMANCE_EVIDENCE", "DEVICE_EVIDENCE"]},
            {"gate": "DEVICE", "owner": "qa-performance", "blocking": True, "review_policy": "ROUTINE",
             "routine_basis": "Run on the reference device", "required_evidence": ["DEVICE_EVIDENCE"]},
            {"gate": "HUMAN_REVIEW", "owner": "HUMAN", "blocking": True, "review_policy": "HUMAN_REVIEW_REQUIRED",
             "required_evidence": ["HUMAN_EVIDENCE"]},
        ],
        "omitted_gates": [{"gate": g, "reason": OMIT_REASON} for g in ("LEVEL_DESIGN", "ANIMATION", "CAMERA_COMPOSITION", "UI_UX", "AUDIO")],
        "review_triggers": ["GOLDEN_CELL_EXIT"],
        "human_review": {"required": True, "timing": "After device validation of the cell build",
                         "primary_question": "Does this cell meet the target quality bar to scale from?"},
        "missing_decisions": [], "editor_write_lock": "NONE", "out_of_scope": ["Content beyond the first cell"],
        "rationale": "The Golden Cell proves the core loop at target quality before production scales.",
    }


INST = {"present": True, "description": "Sampling profiler", "timing_impact": "NEGLIGIBLE"}
GOLDEN_EVIDENCE = [
    evidence("EV-GC-TEST", "TEST_EVIDENCE", CELL, CREV, "AUTOMATED_TEST", source=("CI", "ci-runner")),
    evidence("EV-GC-MOTION", "MOTION_EVIDENCE", CELL, CREV, "DIAGNOSTIC_RUNTIME", source=("AGENT", "gameplay-design")),
    evidence("EV-GC-VISUAL", "VISUAL_EVIDENCE", CELL, CREV, "DIAGNOSTIC_RUNTIME", source=("AGENT", "art-direction")),
    evidence("EV-GC-FEEL", "MOTION_EVIDENCE", CELL, CREV, "DIAGNOSTIC_RUNTIME", source=("AGENT", "game-feel-vfx")),
    evidence("EV-GC-PERF", "PERFORMANCE_EVIDENCE", CELL, CREV, "PERFORMANCE_RUNTIME", target_platform="ANDROID",
             device="Pixel-X", instrumentation=INST),
    evidence("EV-GC-DEV", "DEVICE_EVIDENCE", CELL, CREV, "TARGET_RUNTIME", target_platform="ANDROID", device="Pixel-X"),
    evidence("EV-GC-HUMAN", "HUMAN_EVIDENCE", CELL, CREV, "HUMAN_RECORD", source=("HUMAN", "creative-lead")),
]


def golden_gates(complete=True):
    hr = dict(human_review_ref="G-GC-HR", disagreements_disclosed=True)
    gates = [
        gate("G-GC-TECH", "TECHNICAL", "qa-performance", CELL, CREV, "GC-1", ["EV-GC-TEST"]),
        gate("G-GC-GD", "GAMEPLAY_DESIGN", "gameplay-design", CELL, CREV, "GC-1", ["EV-GC-MOTION"], policy="HUMAN_REVIEW_REQUIRED",
             applied_conditions=["REAL_TIME_BEHAVIOUR"], **hr),
        gate("G-GC-VA", "VISUAL_ART", "art-direction", CELL, CREV, "GC-1", ["EV-GC-VISUAL"], policy="HUMAN_REVIEW_REQUIRED", **hr),
        gate("G-GC-FEEL", "GAME_FEEL_VFX", "game-feel-vfx", CELL, CREV, "GC-1", ["EV-GC-FEEL"], policy="HUMAN_REVIEW_REQUIRED", **hr),
        gate("G-GC-PERF", "PERFORMANCE", "qa-performance", CELL, CREV, "GC-1", ["EV-GC-PERF", "EV-GC-DEV"],
             applied_conditions=["TARGET_PLATFORM_PERFORMANCE_CLAIM"]),
        gate("G-GC-DEV", "DEVICE", "qa-performance", CELL, CREV, "GC-1", ["EV-GC-DEV"]),
        gate("G-GC-HR", "HUMAN_REVIEW", "HUMAN", CELL, CREV, "GC-1", ["EV-GC-HUMAN"], policy="HUMAN_REVIEW_REQUIRED"),
    ]
    if not complete:  # the device run has not happened and game feel still needs work
        gates = [g for g in gates if g["gate_id"] != "G-GC-DEV"]
        feel = next(g for g in gates if g["gate_id"] == "G-GC-FEEL")
        feel.update(status="CHANGES_REQUIRED", specialist_assessment="CHANGES_REQUIRED",
                    required_changes=["Dash start needs more anticipation"])
        for k in ("human_review_ref", "disagreements_disclosed"):
            feel.pop(k)
    return gates


GOLDEN_CONFIG = config(
    lifecycle_stage="GOLDEN_CELL", lifecycle_decision_ref="D-LC-2",
    golden_gameplay_cell={"required": True, "status": "IN_PROGRESS"},
    presentation={"target_presentation_differs_from_editor": "NO", "decision_ref": "D-PARITY"})
GOLDEN_DECISIONS = [
    decision("D-LC-2", "LIFECYCLE_TRANSITION", transition={"from": "PRE_PRODUCTION", "to": "GOLDEN_CELL"}),
    decision("D-PARITY", "PRESENTATION_PARITY", value="NO"),
]


# ---------------------------------------------------------------- Release (from frozen authority fixtures)

def from_authority_fixture(rel):
    fx = vf._fixture(rel)
    routing = vf.build(vf._fixture(fx["routing"]))
    return (vf.build(fx["config"]), [vf.build(d) for d in fx["decisions"]], [routing],
            [vf.build(g) for g in fx["gates"]], [vf.build(e) for e in fx["evidence"]])


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    write_bundle("minimal-valid", config())
    write_bundle("gameplay-feature-ready", FEATURE_CONFIG, [LIFECYCLE], [feature_routing()], [GD_PASS, TECH_PASS], FEATURE_EVIDENCE)
    write_bundle("gameplay-feature-not-ready", FEATURE_CONFIG, [LIFECYCLE], [feature_routing(["RUNTIME_EVIDENCE"])],
                 [GD_PASS], FEATURE_EVIDENCE)
    write_bundle("golden-cell-ready", GOLDEN_CONFIG, GOLDEN_DECISIONS, [golden_routing()], golden_gates(), GOLDEN_EVIDENCE)
    write_bundle("golden-cell-incomplete", GOLDEN_CONFIG, GOLDEN_DECISIONS, [golden_routing()], golden_gates(False), GOLDEN_EVIDENCE)
    write_bundle("release-multi-platform-ready", *from_authority_fixture("tests/fixtures/authority/release-two-primary-covered.json"))
    write_bundle("release-missing-primary-coverage",
                 *from_authority_fixture("tests/fixtures/authority/release-two-primary-only-android.json"))
    # a second, broken routing next to the ready one: FEAT-SLIDE drops the always-required TECHNICAL gate and its
    # gameplay gate cites stale evidence. FEAT-DASH does not depend on any of it (scoped readiness).
    slide = copy.deepcopy(feature_routing())
    slide.update(task_id="FEAT-SLIDE", subject={"kind": "FEATURE", "ref": "slide"}, description="Add a slide move.")
    slide["required_gates"] = slide["required_gates"][:1]
    slide_subject = {"kind": "FEATURE", "ref": "slide"}
    slide_ev = evidence("EV-SLIDE-MOTION", "MOTION_EVIDENCE", slide_subject, "rev-slide-1", "DIAGNOSTIC_RUNTIME",
                        source=("AGENT", "gameplay-design"))
    slide_gate = gate("G-SLIDE-GD", "GAMEPLAY_DESIGN", "gameplay-design", slide_subject, "rev-slide-2", "FEAT-SLIDE",
                      ["EV-SLIDE-MOTION"], policy="CROSS_REVIEW_REQUIRED", applied_conditions=["REAL_TIME_BEHAVIOUR"],
                      cross_reviews=[{"reviewer": "level-design", "assessment": "PASS", "reviewed_revision": "rev-slide-2"}])
    write_bundle("multi-routing-scoped", FEATURE_CONFIG, [LIFECYCLE], [feature_routing(), slide], [GD_PASS, TECH_PASS, slide_gate],
                 FEATURE_EVIDENCE + [slide_ev])
    bad = copy.deepcopy(LIFECYCLE)
    bad["decided_by"] = {"kind": "HUMAN", "id": "visiting-producer"}  # not a listed decision authority
    write_bundle("invalid-authority", FEATURE_CONFIG, [bad], [feature_routing()], [GD_PASS, TECH_PASS], FEATURE_EVIDENCE)
    print(f"wrote {len(list(OUT.iterdir()))} bundles to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
