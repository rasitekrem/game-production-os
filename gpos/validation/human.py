"""Human authority on gate records: gate-aware reviewer authorization, evidence-reuse approval
and linked Human Review verdicts (GOVERNANCE §12.3, 4, 12, 16, 17, 24)."""

from .. import diagnostics as dg
from .authority import human_may_review


def gate_human_authority(ctx, gate):
    out = []
    config = ctx.config
    gid = gate["gate_id"]

    def d(code, message, path, related=()):
        out.append(dg.make(code, message, record_type="gate", record_id=gid, path=path, related=related))

    a = gate.get("assessed_by")
    if a and a["kind"] == "HUMAN" and not human_may_review(config, a["id"], gate["gate"]):
        d("GATE_ASSESSOR_UNAUTHORIZED", f"{gid}: {a['id']} is not authorized to assess {gate['gate']}", "/assessed_by/id", [a["id"]])
    for label in ("evidence_carryover", "evidence_applicability"):
        for i, c in enumerate(gate.get(label, [])):
            ca = c["assessed_by"]
            if ca["kind"] == "HUMAN" and not human_may_review(config, ca["id"], gate["gate"]):
                d("EVIDENCE_REUSE_APPROVER_UNAUTHORIZED", f"{gid}: {ca['id']} is not authorized to approve evidence reuse for "
                  f"{gate['gate']}", f"/{label}/{i}/assessed_by/id", [ca["id"]])

    if gate["gate"] != "HUMAN_REVIEW":  # a HUMAN_REVIEW record is judged against the gates that cite it (below)
        for i, eref in enumerate(gate["evidence_refs"]):
            ev = ctx.evidence_by_id.get(eref)
            if ev and ev["type"] == "HUMAN_EVIDENCE" and not human_may_review(config, ev["source"]["id"], gate["gate"]):
                d("HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED", f"{gid}: {eref} is HUMAN_EVIDENCE from {ev['source']['id']}, who is not a "
                  f"Human Review participant for {gate['gate']}", f"/evidence_refs/{i}", [eref, ev["source"]["id"]])

    ref = gate.get("human_review_ref")
    if ref and gate["gate"] != "HUMAN_REVIEW":
        hr = ctx.gates_by_id.get(ref)
        if hr is None or hr["gate"] != "HUMAN_REVIEW":
            d("HUMAN_REVIEW_REF_NOT_FOUND", f"{gid}: human_review_ref {ref} does not resolve to a HUMAN_REVIEW record",
              "/human_review_ref", [ref])
            return out
        if hr["status"] != "PASS" or hr["scope"] != gate["scope"]:
            d("HUMAN_REVIEW_SCOPE_MISMATCH", f"{gid}: {ref} is not a PASS for the same scope kind, ref and revision",
              "/human_review_ref", [ref])
        humans = [(hr["assessed_by"]["id"], f"assessor of {ref}")] if hr.get("assessed_by", {}).get("kind") == "HUMAN" else []
        humans += [(ctx.evidence_by_id[e]["source"]["id"], f"source of {e}") for e in hr["evidence_refs"]
                   if e in ctx.evidence_by_id and ctx.evidence_by_id[e]["type"] == "HUMAN_EVIDENCE"]
        for h, role in humans:
            if not human_may_review(config, h, gate["gate"]):
                d("HUMAN_REVIEWER_UNAUTHORIZED", f"{gid}: {h} ({role}) is not authorized to review {gate['gate']}",
                  "/human_review_ref", [ref, h])
    return out
