"""Routing record structure and workflow invariants (GOVERNANCE §12.22, 29, 32–35, 37, 40; registry routing_rules).

Workflow invariants come from registry `workflow_gate_requirements`; Markdown is never parsed.
"""

from .. import diagnostics as dg


def _dups(names):
    return sorted({n for n in names if names.count(n) > 1})


def routing_structure(fw, routing):
    out = []
    task = routing["task_id"]

    def d(code, message, path, related=(), details=None):
        out.append(dg.make(code, message, record_type="routing", record_id=task, path=path, related=related, details=details))

    req = [g["gate"] for g in routing["required_gates"]]
    omitted = [g["gate"] for g in routing.get("omitted_gates", [])]
    for label, names in (("required_gates", req), ("omitted_gates", omitted)):
        for gname in _dups(names):
            d("ROUTING_GATE_DUPLICATE", f"{gname} appears more than once in {label}", f"/{label}", [gname])
    for gname in sorted(set(req) & set(omitted)):
        d("ROUTING_GATE_REQUIRED_AND_OMITTED", f"{gname} is both required and omitted", "/omitted_gates", [gname])

    wf = routing["workflow"]
    wr = fw.registry["workflow_gate_requirements"][wf]
    for gname in wr["always_required"]:
        if gname in omitted:
            d("WORKFLOW_GATE_OMITTED", f"{gname} is always required by {wf} and cannot be omitted", "/omitted_gates", [gname])
        elif gname not in req:
            d("WORKFLOW_GATE_MISSING", f"{gname} is always required by {wf} but is not in required_gates", "/required_gates", [gname])
    if wr.get("account_for_all_gates"):
        for gname in fw.gate_names:
            if gname not in req and gname not in omitted:
                d("WORKFLOW_GATE_UNACCOUNTED", f"{wf} must account for {gname}: required, or omitted with a reason",
                  "/omitted_gates", [gname])

    routed = {routing["primary_specialist"], *routing["secondary_specialists"], *routing["reviewers"]}
    if routing["primary_specialist"] in routing["secondary_specialists"]:
        d("ROUTING_PRIMARY_REPEATED", "primary specialist repeated as secondary", "/secondary_specialists",
          [routing["primary_specialist"]])

    for i, g in enumerate(routing["required_gates"]):
        base = f"/required_gates/{i}"
        rules = fw.gates[g["gate"]]
        owner = g.get("owner", rules["default_owner"])
        if owner not in routed:
            d("ROUTING_OWNER_NOT_ROUTED", f"{g['gate']} owner {owner} is not routed", f"{base}/owner", [owner])
        applied = g.get("applied_conditions", [])
        needed = set(rules["base_evidence"]["all_of"])
        for c in rules["conditional_evidence"]:
            if c["condition"] in applied:
                needed |= set(c["requires"])
        missing = needed - set(g["required_evidence"])
        if missing:
            d("ROUTING_EVIDENCE_BELOW_MINIMUM", f"{g['gate']} routing omits required evidence {sorted(missing)}",
              f"{base}/required_evidence", details={"missing": sorted(missing)})
        any_of = rules["base_evidence"]["any_of"]
        if any_of and not set(any_of) & set(g["required_evidence"]):
            d("ROUTING_EVIDENCE_BELOW_MINIMUM", f"{g['gate']} routing requires none of {any_of}", f"{base}/required_evidence",
              details={"one_of": any_of})
        invalid = set(g["required_evidence"]) - set(rules["acceptable_evidence"]) - set(rules["insufficient_alone"])
        if invalid:
            d("ROUTING_EVIDENCE_INVALID_FOR_GATE", f"{g['gate']} routing requires {sorted(invalid)}, which is not valid evidence "
              f"for {g['gate']}", f"{base}/required_evidence", details={"invalid": sorted(invalid)})

        defined = [c["condition"] for c in rules["conditional_evidence"]]
        declined = [u["condition"] for u in g.get("unapplied_conditions", [])]
        for label, names in (("applied_conditions", applied), ("unapplied_conditions", declined)):
            for cname in _dups(names):
                d("CONDITION_DUPLICATE", f"{g['gate']}: {cname} appears more than once in {label}", f"{base}/{label}", [cname])
        for cname in sorted(set(applied) & set(declined)):
            d("CONDITION_APPLIED_AND_DECLINED", f"{g['gate']}: {cname} is both applied and unapplied", f"{base}/unapplied_conditions",
              [cname])
        for cname in sorted(set(declined) - set(defined)):
            d("CONDITION_NOT_DEFINED", f"{g['gate']}: {cname} is not a condition of {g['gate']}", f"{base}/unapplied_conditions",
              [cname])
        for cname in defined:
            if cname not in applied and cname not in declined:
                d("CONDITION_UNACCOUNTED", f"{g['gate']}: condition {cname} is not accounted for (apply it, or decline it "
                  f"with a reason)", base, [cname])

        if fw.needs_cross_review(g["review_policy"], g.get("cross_review_required", False)) and g["gate"] != "HUMAN_REVIEW":
            if not [r for r in routing["reviewers"] if r not in ("HUMAN", owner)]:
                d("ROUTING_CROSS_REVIEWER_MISSING", f"{g['gate']} needs a cross-reviewer other than {owner}", "/reviewers")
        if g["review_policy"] == "HUMAN_REVIEW_REQUIRED" and "HUMAN" not in routing["reviewers"]:
            d("ROUTING_HUMAN_REVIEWER_MISSING", f"{g['gate']} is HUMAN_REVIEW_REQUIRED but HUMAN is not a reviewer", "/reviewers")
    return out
