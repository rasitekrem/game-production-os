"""Project authority: lifecycle, GPOS upgrade, review-policy overrides and effective policy,
project triggers, presentation parity and human-evidence sources
(GOVERNANCE §12.9, 10, 12, 14, 26, 27, 30, 31, 36)."""

import json

from .. import diagnostics as dg

TPD = "TARGET_PRESENTATION_DIFFERS"


def _version_tuple(v):
    core, _, pre = v.partition("-")
    return tuple(int(x) for x in core.split(".")), pre


def gpos_boundary_is_governed(from_version, to_version):
    """MAJOR changes and any change involving a pre-release version need a GPOS_UPGRADE decision."""
    (f_core, f_pre), (t_core, t_pre) = _version_tuple(from_version), _version_tuple(to_version)
    return f_core[0] != t_core[0] or bool(f_pre) or bool(t_pre)


def human_may_review(config, human_id, gate):
    """A listed reviewer whose `gates` (if any) include the gate, or a decision authority holding ALL."""
    for r in config["human_review"]["reviewers"]:
        if r["id"] == human_id and ("gates" not in r or gate in r["gates"]):
            return True
    return any(a["id"] == human_id and "ALL" in a["may_decide"] for a in config["decision_authorities"])


def effective_policy_floor(fw, config, routing, item):
    """Minimum review policy for a routed gate (registry effective_review_policy_precedence):
    mandatory core/project trigger -> single applicable project override -> routing's own choice.
    Returns (policy | "AMBIGUOUS" | None, source text, related ids)."""
    if routing["review_triggers"] and fw.gates[item["gate"]]["subjective"] and item["gate"] != "HUMAN_REVIEW":
        return "HUMAN_REVIEW_REQUIRED", f"mandatory trigger {routing['review_triggers']}", list(routing["review_triggers"])
    applicable = [o for o in config["human_review"].get("review_policy_overrides", [])
                  if o["gate"] == item["gate"] and (o.get("scope") is None or o["scope"] == routing["subject"])]
    if len(applicable) > 1:
        refs = [o["decision_ref"] for o in applicable]
        return "AMBIGUOUS", ", ".join(refs), refs
    if applicable:
        return applicable[0]["review_policy"], f"project override {applicable[0]['decision_ref']}", [applicable[0]["decision_ref"]]
    return None, "routing", []


def project_authority(fw, config, decisions_by_id):
    """Lifecycle, GPOS upgrade and override-duplicate rules on project config."""
    out = []
    reg = fw.registry

    def d(code, message, path, related=()):
        out.append(dg.make(code, message, record_type="project-config", record_id=config["project"]["id"], path=path,
                           related=related))

    stage = config.get("lifecycle_stage", "CONCEPT")
    if stage != "CONCEPT":
        ref = config.get("lifecycle_decision_ref")
        dec = decisions_by_id.get(ref)
        if dec and dec["kind"] == "LIFECYCLE_TRANSITION":
            frm, to = dec["transition"]["from"], dec["transition"]["to"]
            if to != stage:
                d("LIFECYCLE_TRANSITION_MISMATCH", f"lifecycle: decision {ref} enters {to}, but project claims {stage}",
                  "/lifecycle_stage", [ref])
            stages = reg["lifecycle_stages"]
            lt = reg["lifecycle_transitions"]
            forward = [frm, to] in lt["forward"]
            backward = lt["backward_allowed"] and stages.index(to) < stages.index(frm)
            if not (forward or backward):
                d("LIFECYCLE_TRANSITION_ILLEGAL", f"lifecycle: {frm} -> {to} is not an allowed transition", "/lifecycle_decision_ref", [ref])
            if [frm, to] in lt["requires_golden_cell_waiver"] and config["golden_gameplay_cell"].get("status") != "WAIVED":
                d("LIFECYCLE_WAIVER_REQUIRED", f"lifecycle: {frm} -> {to} requires a Golden Cell waiver", "/golden_gameplay_cell", [ref])

    up = config.get("gpos_upgrade")
    if up:
        frm, to = up["from_version"], config["gpos_version"]
        if frm == to:
            d("GPOS_UPGRADE_SAME_VERSION", f"gpos_upgrade: from_version equals gpos_version {to}", "/gpos_upgrade/from_version")
        elif gpos_boundary_is_governed(frm, to) and "decision_ref" not in up:
            d("GPOS_UPGRADE_DECISION_REQUIRED", f"gpos_upgrade: {frm} -> {to} crosses a governed boundary and requires a "
              f"GPOS_UPGRADE decision", "/gpos_upgrade")

    overrides = config["human_review"].get("review_policy_overrides", [])
    keys = [(o["gate"], json.dumps(o.get("scope"), sort_keys=True)) for o in overrides]
    for k in sorted({k for k in keys if keys.count(k) > 1}):
        refs = [o["decision_ref"] for o, kk in zip(overrides, keys) if kk == k]
        d("REVIEW_OVERRIDE_DUPLICATE", f"duplicate review-policy override for {k[0]} scope {k[1]}",
          "/human_review/review_policy_overrides", refs)
    return out


def routing_authority(fw, config, routing):
    """Presentation parity, effective review policy and project triggers for one routing (every routing)."""
    out = []
    task = routing["task_id"]

    def d(code, message, path, related=()):
        out.append(dg.make(code, message, record_type="routing", record_id=task, path=path, related=related))

    parity = config.get("presentation", {}).get("target_presentation_differs_from_editor", "UNDECIDED")
    for i, g in enumerate(routing["required_gates"]):
        base = f"/required_gates/{i}"
        for j, u in enumerate(g.get("unapplied_conditions", [])):
            if u["condition"] == TPD and parity != "NO":
                d("CONDITION_DECLINE_NOT_AUTHORIZED", f"routing {task}: {g['gate']} declines {TPD} but presentation parity is "
                  f"{parity}, not a decided NO", f"{base}/unapplied_conditions/{j}")
        defines_tpd = any(c["condition"] == TPD for c in fw.gates[g["gate"]]["conditional_evidence"])
        if defines_tpd and parity == "YES" and TPD not in g.get("applied_conditions", []):
            d("CONDITION_REQUIRED_BY_PARITY", f"routing {task}: {g['gate']} must apply {TPD} because project presentation "
              f"parity is a decided YES", f"{base}/applied_conditions")
        floor, source, related = effective_policy_floor(fw, config, routing, g)
        if floor == "AMBIGUOUS":
            d("REVIEW_OVERRIDE_AMBIGUOUS", f"routing {task}: {g['gate']} has more than one applicable review-policy override "
              f"({source})", f"{base}/review_policy", related)
        elif floor and fw.policy_strength[g["review_policy"]] < fw.policy_strength[floor]:
            d("REVIEW_POLICY_WEAKER_THAN_EFFECTIVE", f"routing {task}: {g['gate']} uses {g['review_policy']}, weaker than the "
              f"effective policy {floor} required by {source}", f"{base}/review_policy", related)
    prefix = fw.registry["project_trigger_prefix"]
    project_triggers = {t["id"] for t in config["human_review"].get("additional_mandatory_triggers", [])}
    for i, trig in enumerate(routing["review_triggers"]):
        if trig.startswith(prefix) and trig[len(prefix):] not in project_triggers:
            d("PROJECT_TRIGGER_UNKNOWN", f"routing {task}: {trig} is not a project trigger declared in project config",
              f"/review_triggers/{i}", [trig])
    return out


def human_evidence_sources(config, evidence):
    out = []
    listed = {r["id"] for r in config["human_review"]["reviewers"]} | {a["id"] for a in config["decision_authorities"]}
    for ev in evidence:
        if ev["type"] == "HUMAN_EVIDENCE" and ev["source"]["id"] not in listed:
            out.append(dg.make("HUMAN_EVIDENCE_SOURCE_UNLISTED", f"{ev['evidence_id']}: {ev['source']['id']} is not a listed "
                               f"reviewer or decision authority", record_type="evidence", record_id=ev["evidence_id"],
                               path="/source/id", related=[ev["source"]["id"]]))
    return out
