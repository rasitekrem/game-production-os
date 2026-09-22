"""One gate record against its evidence and the registry gate rules (GOVERNANCE §12.1–6, 16, 23, 24, 28, 38, 39).

assess_gate never needs other gates or routings, so it also serves gate-only checks
(no project config): target-platform binding is applied only when config is given.
"""

from .. import diagnostics as dg
from .platforms import target_platform_problem

UNUSABLE_TIMING = ("MATERIAL", "UNKNOWN")
NEGATIVE = ("FAIL", "CHANGES_REQUIRED")


def _accountable(assessor, owner):
    return assessor["kind"] == "HUMAN" or (assessor["kind"] == "AGENT" and assessor["id"] == owner)


def assess_gate(fw, gate, evidence_by_id, config=None):
    """(diagnostics, counting evidence records) for one schema-valid gate record."""
    rules = fw.gates[gate["gate"]]
    gid = gate["gate_id"]
    owner = gate.get("owner")
    revision = gate["scope"]["revision"]
    scope_kind = gate["scope"]["kind"]
    scope_subject = (scope_kind, gate["scope"]["ref"])
    conditions = gate.get("applied_conditions", [])
    out, counting = [], []

    def d(code, message, path=None, related=(), details=None):
        out.append(dg.make(code, message, record_type="gate", record_id=gid, path=path, related=related, details=details))

    mapped = {}
    for i, m in enumerate(gate.get("evidence_applicability", [])):
        a = m["assessed_by"]
        if _accountable(a, owner):
            mapped[m["evidence_ref"]] = (m["evidence_subject"]["kind"], m["evidence_subject"]["ref"])
        else:
            d("APPLICABILITY_NOT_ACCOUNTABLE", f"applicability of {m['evidence_ref']} approved by {a['kind']} {a['id']} is not "
              f"accountable (only the gate owner or an authorized human may approve it)",
              f"/evidence_applicability/{i}/assessed_by", [m["evidence_ref"]])

    reviews = gate.get("cross_reviews", [])
    for i, review in enumerate(reviews):
        if not review.get("superseded") and review.get("reviewed_revision") != revision:
            d("CROSS_REVIEW_STALE", f"stale cross-review by {review['reviewer']}: reviewed {review.get('reviewed_revision')}, "
              f"gate is {revision}", f"/cross_reviews/{i}/reviewed_revision", [review["reviewer"]])
        if review.get("superseded") and review["assessment"] in NEGATIVE:
            if not any(r["reviewer"] == review["reviewer"] and not r.get("superseded") for r in reviews[i + 1:]):
                d("CROSS_REVIEW_SUPERSEDED_WITHOUT_REPLACEMENT",
                  f"negative cross-review by {review['reviewer']} is marked superseded, but no later active review by "
                  f"{review['reviewer']} replaces it", f"/cross_reviews/{i}/superseded", [review["reviewer"]])

    carry = {}
    for i, c in enumerate(gate.get("evidence_carryover", [])):
        a = c["assessed_by"]
        if _accountable(a, owner):
            carry[c["evidence_ref"]] = c
        else:
            d("CARRYOVER_NOT_ACCOUNTABLE", f"carryover of {c['evidence_ref']} approved by {a['kind']} {a['id']} is not accountable "
              f"(only the gate owner or an authorized human may approve carryover)",
              f"/evidence_carryover/{i}/assessed_by", [c["evidence_ref"]])

    compat = fw.registry["evidence_context_compatibility"]
    for i, ref in enumerate(gate["evidence_refs"]):
        path = f"/evidence_refs/{i}"
        ev = evidence_by_id.get(ref)
        if ev is None:
            d("EVIDENCE_NOT_FOUND", f"unknown evidence {ref}", path, [ref])
            continue
        if ev.get("superseded"):
            d("EVIDENCE_SUPERSEDED", f"{ref} is superseded and cannot satisfy a current gate", path, [ref])
            continue
        if ev["type"] not in rules["acceptable_evidence"] + rules["insufficient_alone"]:
            d("EVIDENCE_TYPE_NOT_ACCEPTED", f"{ref} type {ev['type']} not acceptable for {gate['gate']}", path, [ref])
            continue
        prov = ev["provenance"]
        ctx = prov["capture_context"]
        if ctx not in compat[ev["type"]]:
            d("INVALID_EVIDENCE_CONTEXT", f"{ref} {ev['type']} in {ctx} is an incompatible type/context combination", path, [ref])
            continue
        if ctx in rules["non_counting_contexts"] and scope_kind not in rules["context_scope_exceptions"].get(ctx, []):
            d("EVIDENCE_CONTEXT_NOT_COUNTING", f"{ref} captured in {ctx}, which does not count for {gate['gate']} on "
              f"{scope_kind} scope", path, [ref])
            continue
        ev_subject = (ev["subject"]["kind"], ev["subject"]["ref"])
        if ev_subject != scope_subject and mapped.get(ref) != ev_subject:
            d("EVIDENCE_SUBJECT_MISMATCH", f"{ref} proves {ev_subject[0]} {ev_subject[1]}, not the gate subject "
              f"{scope_subject[0]} {scope_subject[1]} (no accountable evidence_applicability)", path, [ref])
            continue
        if prov["subject_revision"] != revision:
            c = carry.get(ref)
            if c is None:
                d("STALE_EVIDENCE", f"{ref} is stale: revision {prov['subject_revision']} != {revision}", path, [ref])
                continue
            if c["evidence_revision"] != prov["subject_revision"]:
                d("CARRYOVER_REVISION_MISMATCH", f"{ref} carryover names {c['evidence_revision']} but evidence is "
                  f"{prov['subject_revision']}", path, [ref])
                continue
        if config is not None:
            why = target_platform_problem(ev, config)
            if why:
                d(why[0], why[1], path, [ref])
                continue
        inst = prov.get("instrumentation")
        if ev["type"] == "PERFORMANCE_EVIDENCE" and inst and inst["timing_impact"] in UNUSABLE_TIMING:
            d("INSTRUMENTATION_TIMING_UNUSABLE", f"{ref} instrumentation timing impact {inst['timing_impact']} cannot prove timing",
              path, [ref])
            continue
        counting.append(ev)

    if owner not in rules["permitted_owners"]:
        d("GATE_OWNER_NOT_PERMITTED", f"{owner} may not own {gate['gate']}", "/owner")
    assessor = gate.get("assessed_by")
    if assessor and assessor["kind"] != "HUMAN" and assessor["id"] != owner:
        d("ASSESSOR_NOT_OWNER", f"assessed_by {assessor['id']} is not the accountable owner {owner}", "/assessed_by")
    for i, review in enumerate(reviews):
        if review["reviewer"] == owner:
            d("CROSS_REVIEW_SELF", f"{owner} cross-reviewed its own gate", f"/cross_reviews/{i}/reviewer")
        if review["reviewer"] in fw.registry["never_cross_reviewer"]:
            d("CROSS_REVIEW_BY_NEVER_REVIEWER", f"{review['reviewer']} never reviews discipline quality and cannot be a "
              f"cross-reviewer", f"/cross_reviews/{i}/reviewer")

    status = gate["status"]
    if status == "PASS" and fw.needs_cross_review(gate["review_policy"], gate.get("cross_review_required", False)):
        current = [r for r in reviews if not r.get("superseded") and r.get("reviewed_revision") == revision]
        if not any(r["assessment"] == "PASS" and fw.eligible_cross_reviewer(owner, r["reviewer"]) for r in current):
            eligible = fw.registry["cross_review_eligibility"].get(owner, [])
            d("CROSS_REVIEW_ELIGIBLE_MISSING", f"PASS has no current passing cross-review by a specialist eligible for {owner} "
              f"(registry cross_review_eligibility: {eligible})", "/cross_reviews", details={"eligible": eligible})
    if status == "PASS" and gate["review_policy"] == "ROUTINE":
        a = gate.get("assessed_by", {})
        if gate.get("specialist_assessment") != "PASS" or a.get("kind") != "AGENT" or a.get("id") != owner:
            d("ROUTINE_PASS_NOT_OWNER", "ROUTINE PASS must be the owning specialist's own PASS assessment", "/specialist_assessment")
    if status == "PASS":
        types = {e["type"] for e in counting}
        base = rules["base_evidence"]
        for t in base["all_of"]:
            if t not in types:
                d("PASS_EVIDENCE_MISSING", f"PASS lacks required {t}", "/evidence_refs", details={"missing": [t]})
        if base["any_of"] and not types & set(base["any_of"]):
            d("PASS_EVIDENCE_MISSING", f"PASS lacks one of {base['any_of']}", "/evidence_refs", details={"one_of": base["any_of"]})
        for cond in rules["conditional_evidence"]:
            if cond["condition"] not in conditions:
                continue
            for t in cond["requires"]:
                if t not in types:
                    d("PASS_EVIDENCE_MISSING", f"PASS lacks {t} required by {cond['condition']}", "/evidence_refs",
                      details={"missing": [t], "condition": cond["condition"]})
            ctxs = cond.get("requires_context")
            if ctxs:
                claim_types = set(base["all_of"]) | set(base["any_of"])
                if not any(e["type"] in claim_types and e["provenance"]["capture_context"] in ctxs for e in counting):
                    d("PASS_EVIDENCE_MISSING", f"PASS lacks evidence captured in {ctxs} required by {cond['condition']}",
                      "/evidence_refs", details={"contexts": ctxs, "condition": cond["condition"]})
        credible = types & set(rules["acceptable_evidence"])
        if types and (not credible or credible <= set(rules["insufficient_alone"])):
            d("PASS_INSUFFICIENT_EVIDENCE", f"PASS rests only on insufficient evidence {sorted(types)}", "/evidence_refs")
    return out, counting
