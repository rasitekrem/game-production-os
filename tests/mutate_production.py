#!/usr/bin/env python3
"""Bounded mutation harness for the production validator (gpos/).

    python3 tests/mutate_production.py [--jobs N] [--only TEXT]

Each mutation disables or weakens exactly one rule in a temporary copy of the repository
and runs tests/test_production_validator.py there. A mutation must make the suite fail
("CAUGHT"); one that leaves it green is "MISSED" and fails this harness. An anchor that no
longer exists is reported "NOT APPLIED" and also fails the harness, so the list cannot rot.
The repository itself is never modified.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (name, file, anchor, replacement)
MUTATIONS = [
    ("superseded evidence counts", "gpos/validation/gates.py", 'if ev.get("superseded"):', "if False:"),
    ("stale evidence counts", "gpos/validation/gates.py", 'if prov["subject_revision"] != revision:', "if False:"),
    ("evidence subject ignored", "gpos/validation/gates.py", "if ev_subject != scope_subject and mapped.get(ref) != ev_subject:", "if False:"),
    ("non-counting context counts", "gpos/validation/gates.py", 'if ctx in rules["non_counting_contexts"] and', "if False and"),
    ("type/context compatibility ignored", "gpos/validation/gates.py", 'if ctx not in compat[ev["type"]]:', "if False:"),
    ("platform binding not applied", "gpos/validation/gates.py", "        if config is not None:\n            why", "        if False:\n            why"),
    ("undeclared platform accepted", "gpos/validation/platforms.py", "if platform not in targets:", "if False:"),
    ("reference device ignored", "gpos/validation/platforms.py", 'if ev["type"] == "DEVICE_EVIDENCE" and refs and', "if False and"),
    ("coverage ignores platform", "gpos/validation/platforms.py",
     'return ev["type"] == etype and prov.get("target_platform") == platform and', 'return ev["type"] == etype and'),
    ("SECONDARY treated as PRIMARY", "gpos/validation/platforms.py", 'if t["tier"] == "PRIMARY"]', "]"),
    ("any reviewer eligible", "gpos/framework.py",
     'return reviewer not in reg["never_cross_reviewer"] and reviewer in reg["cross_review_eligibility"].get(owner, [])', "return True"),
    ("gate-level eligibility dropped", "gpos/validation/gates.py",
     'r["assessment"] == "PASS" and fw.eligible_cross_reviewer(owner, r["reviewer"])', 'r["assessment"] == "PASS"'),
    ("anyone may approve reuse", "gpos/validation/gates.py",
     'return assessor["kind"] == "HUMAN" or (assessor["kind"] == "AGENT" and assessor["id"] == owner)', "return True"),
    ("ROUTINE PASS unchecked", "gpos/validation/gates.py", 'if status == "PASS" and gate["review_policy"] == "ROUTINE":', "if False:"),
    ("registry base evidence ignored", "gpos/validation/gates.py", '        for t in base["all_of"]:\n            if t not in types:',
     '        for t in base["all_of"]:\n            if False:'),
    ("instrumented timing accepted", "gpos/validation/gates.py", 'inst["timing_impact"] in UNUSABLE_TIMING', "False"),
    ("superseded negative review needs no replacement", "gpos/validation/gates.py",
     'if review.get("superseded") and review["assessment"] in NEGATIVE:', "if False:"),
    ("stale cross-review accepted", "gpos/validation/gates.py",
     'if not review.get("superseded") and review.get("reviewed_revision") != revision:', "if False:"),
    ("unlisted linked gate ignored", "gpos/validation/linkage.py", 'if g["gate"] not in required:', "if False:"),
    ("routed evidence ignored", "gpos/validation/linkage.py", 'absent = sorted(set(item["required_evidence"]) - counted)', "absent = []"),
    ("unrouted reviewer accepted", "gpos/validation/linkage.py", 'if r["reviewer"] not in routed_reviewers:', "if False:"),
    ("routing metadata not compared", "gpos/validation/linkage.py", "            if got != want:", "            if False:"),
    ("release coverage skipped", "gpos/validation/linkage.py", 'if routing["workflow"] != "release":', "if True:"),
    ("missing gate is ready", "gpos/validation/linkage.py", "        if not recs:\n            code =", "        if False:\n            code ="),
    ("unfinished gate is ready", "gpos/validation/linkage.py", '        if g["status"] != "PASS":\n            counted', '        if False:\n            counted'),
    ("readiness not fail-closed", "gpos/validation/project.py",
     'ready = rec is not None and an.valid and "cross-record" in an.stages_completed and not blockers',
     'ready = rec is not None and not [b for b in blockers if b.code != "RECORD_SET_INVALID"]'),
    ("version pin ignored", "gpos/validation/project.py", "if isinstance(pinned, str) and pinned != fw.version:", "if False:"),
    # semantic alignment (Human Review of Phase 2A)
    ("readiness back to whole-project blocking", "gpos/validation/project.py",
     "return fw, rec, analyze(routing_scope(record_set, routing_id, fw), fw)", "return fw, rec, analyze(record_set, fw)"),
    ("unsupported version reported as INVALID", "gpos/validation/project.py",
     "raise UnsupportedGposVersion(diag.message, diag)", "record_set.load_diagnostics.append(diag)"),
    ("lower-case RFC 3339 rejected", "gpos/schema.py", "(\\d{2})[Tt](\\d{2})", "(\\d{2})T(\\d{2})"),
    ("routed-evidence BLOCKER classified as ERROR", "gpos/diagnostics.py",
     '"ROUTED_EVIDENCE_MISSING": (BLOCKER, READINESS,', '"ROUTED_EVIDENCE_MISSING": (ERROR, RECORD,'),
    ("missing-gate BLOCKER classified as ERROR", "gpos/diagnostics.py",
     '"MISSING_REQUIRED_GATE": (BLOCKER, READINESS,', '"MISSING_REQUIRED_GATE": (ERROR, RECORD,'),
    ("scope ignores project-global decisions", "gpos/validation/scope.py",
     'decision_ids |= _decision_refs(fw, "project-config", rs.config.data)', "pass"),
    ("scope misses duplicate ids elsewhere", "gpos/validation/scope.py",
     'evidence = [e for e in rs.evidence if _str(e.data.get("evidence_id")) in evidence_ids]',
     'evidence = [e for e in {_str(e.data.get("evidence_id")): e for e in rs.evidence}.values() '
     'if _str(e.data.get("evidence_id")) in evidence_ids]'),
    ("scope drops load problems", "gpos/validation/scope.py", "root=rs.root, load_diagnostics=rs.load_diagnostics,", "root=rs.root,"),
    ("duplicate record ids ignored", "gpos/validation/ids.py", "for dup in duplicates([r.data[field] for r in records]):", "for dup in []:"),
    ("decision value binding ignored", "gpos/validation/decisions.py", "                if cv != dv:", "                if False:"),
    ("unauthorized decider accepted", "gpos/validation/decisions.py", 'if allowed is None or not ({"ALL", dec["kind"]} & allowed):', "if False:"),
    ("inactive decision accepted", "gpos/validation/decisions.py", 'if dec["status"] != "ACTIVE":', "if False:"),
    ("decision subject ignored", "gpos/validation/decisions.py", 'if dec["subject"]["kind"] not in rule["subject_kinds"]:', "if False:"),
    ("weaker routing accepted", "gpos/validation/authority.py",
     'elif floor and fw.policy_strength[g["review_policy"]] < fw.policy_strength[floor]:', "elif False:"),
    ("trigger precedence ignored", "gpos/validation/authority.py",
     'if routing["review_triggers"] and fw.gates[item["gate"]]["subjective"]', 'if False and fw.gates[item["gate"]]["subjective"]'),
    ("parity decline unchecked", "gpos/validation/authority.py", 'if u["condition"] == TPD and parity != "NO":', "if False:"),
    ("parity YES not enforced", "gpos/validation/authority.py", 'if defines_tpd and parity == "YES"', 'if False and parity == "YES"'),
    ("reviewer gate limits ignored", "gpos/validation/authority.py", 'if r["id"] == human_id and ("gates" not in r or gate in r["gates"]):',
     'if r["id"] == human_id:'),
    ("human evidence per gate unchecked", "gpos/validation/human.py",
     'if ev and ev["type"] == "HUMAN_EVIDENCE" and not human_may_review', 'if False and not human_may_review'),
    ("Human Review scope unchecked", "gpos/validation/human.py", 'if hr["status"] != "PASS" or hr["scope"] != gate["scope"]:', "if False:"),
    ("all-gate accounting skipped", "gpos/validation/routing.py", 'if wr.get("account_for_all_gates"):', "if False:"),
    ("always-required gate may be missing", "gpos/validation/routing.py", "        elif gname not in req:", "        elif False:"),
    ("condition accounting skipped", "gpos/validation/routing.py", "if cname not in applied and cname not in declined:", "if False:"),
    ("date-time not asserted", "gpos/schema.py", 'if schema.get("format") == "date-time" and not is_rfc3339_datetime(inst):', "if False:"),
    ("unknown schema keyword accepted", "gpos/schema.py", "if key not in SUPPORTED_KEYWORDS:", "if False:"),
    ("unknown bundle files skipped", "gpos/records.py", '                else:\n                    diags.append(dg.make("UNKNOWN_RECORD_FILE"',
     '                elif False:\n                    diags.append(dg.make("UNKNOWN_RECORD_FILE"'),
]


def run(mutation):
    name, rel, anchor, replacement = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-mut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        path = copy / rel
        text = path.read_text()
        if text.count(anchor) != 1:
            return name, f"NOT APPLIED (anchor found {text.count(anchor)} times)"
        path.write_text(text.replace(anchor, replacement))
        out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_production_validator.py")], capture_output=True,
                             text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=600)
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    args = parser.parse_args()
    selected = [m for m in MUTATIONS if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
