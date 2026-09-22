"""Routing <-> gate linkage, routed reviewers and evidence, Release PRIMARY coverage and routed
readiness (GOVERNANCE §12.3, 19–22, 32, 34, 38, 43, 44).

Integrity problems (a gate record contradicting its routing) are ERRORs. What the routing
demands beyond valid records (a missing or unfinished gate, routed evidence or reviewer,
PRIMARY coverage) is a readiness BLOCKER of that routing.
"""

from .. import diagnostics as dg
from .platforms import covers_platform, primary_platforms


def routing_linkage(ctx, routing):
    """(errors, blockers) for one routing and the gates linked to it by routing_ref."""
    fw = ctx.fw
    errors, blockers = [], []
    task = routing["task_id"]
    linked = ctx.gates_by_routing.get(task, [])
    subject = (routing["subject"]["kind"], routing["subject"]["ref"])
    required = {}
    for i, item in enumerate(routing["required_gates"]):
        required.setdefault(item["gate"], (i, item))  # duplicates are a routing-structure ERROR

    for g in linked:
        if g["gate"] not in required:
            blockers.append(dg.make("GATE_NOT_IN_ROUTING", f"{g['gate_id']}: {g['gate']} was raised after routing {task}; "
                                    f"revise routing before readiness", record_type="routing", record_id=task,
                                    path="/required_gates", related=[g["gate_id"], g["gate"]]))

    routed_reviewers = {r for r in routing["reviewers"] if r != "HUMAN"}
    for gname, (i, item) in required.items():
        recs = [g for g in linked if g["gate"] == gname]
        if len(recs) != 1:
            if len(recs) > 1:
                errors.append(dg.make("ROUTED_GATE_AMBIGUOUS", f"routing {task}: {gname} has {len(recs)} gate records; ambiguous",
                                      record_type="routing", record_id=task, path=f"/required_gates/{i}",
                                      related=[g["gate_id"] for g in recs]))
            continue
        g = recs[0]
        gid = g["gate_id"]
        expected_owner = item.get("owner", fw.gates[gname]["default_owner"])
        checks = [
            ("owner", g["owner"], expected_owner),
            ("blocking", g["blocking"], item["blocking"]),
            ("review_policy", g["review_policy"], item["review_policy"]),
            ("applied_conditions", sorted(g.get("applied_conditions", [])), sorted(item.get("applied_conditions", []))),
            ("cross_review_required", g.get("cross_review_required", g["review_policy"] == "CROSS_REVIEW_REQUIRED"),
             item.get("cross_review_required", item["review_policy"] == "CROSS_REVIEW_REQUIRED")),
            ("subject", [g["scope"]["kind"], g["scope"]["ref"]], list(subject)),
        ]
        for label, got, want in checks:
            if got != want:
                errors.append(dg.make("ROUTING_GATE_MISMATCH", f"{gid}: {label} {got!r} does not match routing {task} ({want!r})",
                                      record_type="gate", record_id=gid, path="/scope" if label == "subject" else f"/{label}",
                                      related=[task], details={"field": label, "gate": got, "routing": want}))

        revision = g["scope"]["revision"]
        contributing = [(j, r) for j, r in enumerate(g.get("cross_reviews", []))
                        if not r.get("superseded") and r["reviewed_revision"] == revision and r["assessment"] == "PASS"]
        for j, r in contributing:
            if r["reviewer"] not in routed_reviewers:
                blockers.append(dg.make("CROSS_REVIEWER_NOT_ROUTED", f"{gid}: cross-review by {r['reviewer']} cannot contribute; "
                                        f"not a routed reviewer of {task}", record_type="gate", record_id=gid,
                                        path=f"/cross_reviews/{j}/reviewer", related=[task, r["reviewer"]]))
            elif not fw.eligible_cross_reviewer(g["owner"], r["reviewer"]):
                blockers.append(dg.make("CROSS_REVIEWER_NOT_ELIGIBLE", f"{gid}: cross-review by {r['reviewer']} cannot contribute; "
                                        f"routed but not eligible to review {g['owner']} (escalate to HUMAN_REVIEW_REQUIRED for "
                                        f"a non-standard reviewer)", record_type="gate", record_id=gid,
                                        path=f"/cross_reviews/{j}/reviewer", related=[task, r["reviewer"]]))
        if g["status"] == "PASS" and fw.needs_cross_review(g["review_policy"], g.get("cross_review_required", False)):
            if not any(r["reviewer"] in routed_reviewers for _, r in contributing):
                blockers.append(dg.make("ROUTED_CROSS_REVIEW_MISSING", f"{gid}: PASS needs a current passing cross-review by a "
                                        f"routed reviewer of {task}", record_type="gate", record_id=gid, path="/cross_reviews",
                                        related=[task]))
        if g["status"] == "PASS":
            counted = {e["type"] for e in ctx.gate_assessment(g)[1]}
            absent = sorted(set(item["required_evidence"]) - counted)
            if absent:
                blockers.append(dg.make("ROUTED_EVIDENCE_MISSING", f"{gid}: PASS lacks routing-required evidence {absent} for {task}",
                                        record_type="gate", record_id=gid, path="/evidence_refs", related=[task],
                                        details={"missing": absent}))
    return errors, blockers


def release_coverage(ctx, routing):
    """Release: every PRIMARY target platform has counting DEVICE and PERFORMANCE evidence on that platform.
    One platform's evidence never covers another; SECONDARY platforms never block."""
    blockers = []
    if routing["workflow"] != "release":
        return blockers
    task = routing["task_id"]
    linked = {}
    for g in ctx.gates_by_routing.get(task, []):
        linked.setdefault(g["gate"], g)
    primaries = primary_platforms(ctx.config)
    for gname, etype in ctx.fw.registry["release_primary_platform_coverage"].items():
        g = linked.get(gname)
        if g is None or g["status"] != "PASS":
            continue  # a missing or unfinished gate is already a readiness blocker
        counted = ctx.gate_assessment(g)[1]
        for platform in primaries:
            if platform == "UNDECIDED":
                blockers.append(dg.make("PRIMARY_PLATFORM_UNDECIDED", f"release {task}: a PRIMARY target platform is UNDECIDED; "
                                        f"release coverage cannot be shown", record_type="routing", record_id=task,
                                        related=[g["gate_id"]], details={"gate": gname}))
                continue
            if not any(covers_platform(e, etype, platform) for e in counted):
                blockers.append(dg.make("PRIMARY_PLATFORM_COVERAGE_MISSING", f"release {task}: {gname} has no counting {etype} "
                                        f"for PRIMARY platform {platform}", record_type="routing", record_id=task,
                                        related=[g["gate_id"], platform],
                                        details={"gate": gname, "evidence_type": etype, "platform": platform}))
    return blockers


def gate_completion(ctx, routing):
    """Readiness blockers for required gates that are missing (NOT_RUN) or not PASS.
    NOT_APPLICABLE never satisfies a gate the routing still requires."""
    out = []
    task = routing["task_id"]
    linked = {}
    for g in ctx.gates_by_routing.get(task, []):
        linked.setdefault(g["gate"], []).append(g)
    seen = set()
    for i, item in enumerate(routing["required_gates"]):
        gname = item["gate"]
        if gname in seen:
            continue
        seen.add(gname)
        recs = linked.get(gname, [])
        if len(recs) > 1:
            continue  # ROUTED_GATE_AMBIGUOUS already invalidates the record set
        path = f"/required_gates/{i}"
        if not item["blocking"]:
            if not recs or recs[0]["status"] != "PASS":
                state = recs[0]["status"] if recs else "NOT_RUN (no record)"
                out.append(dg.make("NON_BLOCKING_GATE_OPEN", f"routing {task}: non-blocking {gname} is {state}",
                                   record_type="routing", record_id=task, path=path, related=[gname]))
            continue
        if not recs:
            code = "HUMAN_REVIEW_MISSING" if gname == "HUMAN_REVIEW" else "MISSING_REQUIRED_GATE"
            out.append(dg.make(code, f"routing {task}: required {gname} has no gate record (NOT_RUN)", record_type="routing",
                               record_id=task, path=path, related=[gname],
                               details={"gate": gname, "required_evidence": sorted(item["required_evidence"])}))
            continue
        g = recs[0]
        if g["status"] != "PASS":
            counted = {e["type"] for e in ctx.gate_assessment(g)[1]}
            details = {"gate": gname, "status": g["status"],
                       "routed_evidence_not_counting": sorted(set(item["required_evidence"]) - counted)}
            out.append(dg.make("GATE_NOT_PASSED", f"routing {task}: blocking {gname} is {g['status']} ({g['gate_id']})",
                               record_type="routing", record_id=task, path=path, related=[g["gate_id"], gname], details=details))
    return out
