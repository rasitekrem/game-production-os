#!/usr/bin/env python3
"""Game Production OS — framework validation.

Standard library only. Run from anywhere:

    python3 tests/validate_framework.py

Validates structure, contracts, vocabulary consistency, schemas, examples and
fixtures. It does not (and cannot) judge the subjective quality of the
framework; that remains Human Review.

The functions under "Reference implementation of cross-record rules" are test
code that demonstrates rules Phase-2 tooling must enforce on real project
records (core/GOVERNANCE.md §12). They are not project tooling.
"""

import copy
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema_lite import Validator  # noqa: E402

try:  # Optional cross-check; never required.
    import jsonschema  # type: ignore
except ImportError:  # pragma: no cover
    jsonschema = None

REGISTRY = json.loads((ROOT / "core" / "registry.json").read_text())
SCHEMA_NAMES = ["gate", "evidence", "task-routing", "project-config", "decision"]
SCHEMAS = {n: json.loads((ROOT / "schemas" / f"{n}.schema.json").read_text()) for n in SCHEMA_NAMES}
VALIDATORS = {n: Validator(s) for n, s in SCHEMAS.items()}
EXAMPLES = {
    "project-config": ROOT / "examples" / "minimal-project-config.json",
    "task-routing": ROOT / "examples" / "example-task-routing.json",
    "gate": ROOT / "examples" / "example-gate-record.json",
    "evidence": ROOT / "examples" / "example-evidence-record.json",
    "decision": ROOT / "examples" / "example-decision-record.json",
}
GATES = REGISTRY["gates"]
SUBJECTIVE_DISCIPLINE_GATES = sorted(g for g, d in GATES.items() if d["subjective"] and g != "HUMAN_REVIEW")
TRIGGERS = list(REGISTRY["mandatory_human_review_triggers"])
CONDITIONS = list(REGISTRY["evidence_conditions"])
VERSION = "1.0.0-alpha.15"
GATE_OWNERS = [s for s in REGISTRY["skills"] if any(s in d["permitted_owners"] for d in GATES.values())] + ["HUMAN"]


# ---------------------------------------------------------------- helpers

def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def load_json(rel):
    return json.loads(read(rel))


def front_matter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def skill_text(skill):
    return read(f"skills/{skill}/SKILL.md")


def may_own(skill):
    raw = front_matter(skill_text(skill))["may_own_gates"].strip("[]")
    return [x.strip() for x in raw.split(",") if x.strip()]


def h2_sections(text):
    """Map '## NAME' headings to their body text (ordered)."""
    sections, order, current = {}, [], None
    for line in text.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            current = m.group(1)
            order.append(current)
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    return order, sections


def skill_section(skill, name):
    return h2_sections(skill_text(skill))[1].get(name, "")


def workflow_text(wf):
    return read(f"workflows/{wf}.md")


def backticked(text):
    return re.findall(r"`([^`\n]+)`", text)


def all_markdown():
    return sorted(p for p in ROOT.rglob("*.md") if ".git" not in p.parts)


def slug(heading):
    h = heading.strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def heading_slugs(path):
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6} (.+?)\s*$", path.read_text(), re.M)}


def table_rows(section_text):
    """Rows of Markdown tables whose first cell is backticked, as lists of cells."""
    return [[c.strip() for c in row.strip().strip("|").split("|")]
            for row in section_text.splitlines() if re.match(r"^\| `[A-Za-z_-]+` \|", row)]


def _walk(doc, pointer):
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.lstrip("/").split("/")]
    node = doc
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    return node, parts[-1]


def json_pointer_set(doc, pointer, value):
    node, last = _walk(doc, pointer)
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def json_pointer_remove(doc, pointer):
    node, last = _walk(doc, pointer)
    if isinstance(node, list):
        del node[int(last)]
    else:
        node.pop(last, None)


def build(spec):
    """Apply a {base, set, remove} patch to a valid example."""
    doc = copy.deepcopy(load_json(spec["base"]))
    for ptr in spec.get("remove", []):
        json_pointer_remove(doc, ptr)
    for ptr, value in spec.get("set", {}).items():
        json_pointer_set(doc, ptr, value)
    return doc


def _reference_validator(schema_name):
    """jsonschema with its format checker; refuses to run if date-time checking is unavailable."""
    checker = jsonschema.Draft202012Validator.FORMAT_CHECKER
    if "date-time" not in checker.checkers:
        raise AssertionError("jsonschema is installed but cannot check date-time "
                             "(install rfc3339-validator), so the cross-check would be dishonest")
    return jsonschema.Draft202012Validator(SCHEMAS[schema_name], format_checker=checker)


def validate(schema_name, doc):
    errors = VALIDATORS[schema_name].errors(doc)
    if jsonschema is not None:
        ref_valid = _reference_validator(schema_name).is_valid(doc)
        if ref_valid != (not errors):
            raise AssertionError(f"schema_lite and jsonschema disagree on {schema_name}: lite errors={errors}")
    return errors


def schema_vocabulary():
    words = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "enum":
                    words.update(x for x in v if isinstance(x, str))
                elif k == "const" and isinstance(v, str):
                    words.add(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for s in SCHEMAS.values():
        walk(s)
    return words


def registry_vocabulary():
    words = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                words.add(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            words.add(node)

    walk(REGISTRY)
    return words


def find_rules(all_of, key):
    """Map gate -> `then.properties.<key>` for per-gate if/then rules in a schema allOf."""
    out = {}
    for r in all_of:
        gate = r.get("if", {}).get("properties", {}).get("gate", {}).get("const")
        prop = r.get("then", {}).get("properties", {}).get(key)
        if gate and prop is not None:
            out[gate] = prop
    return out


# ------------------------------------------ reference implementation of cross-record rules

def gate_evidence_problems(gate, evidence_by_id, config=None):
    """Problems with a gate record's evidence under registry rules (Phase-2 requirement).
    With project config, target-runtime evidence is also bound to declared platforms and reference devices."""
    return gate_evidence(gate, evidence_by_id, config)[0]


def counting_evidence_types(gate, evidence_by_id, config=None):
    """Evidence types that actually count for this gate (subject, revision, context, type and platform rules applied)."""
    return {e["type"] for e in gate_evidence(gate, evidence_by_id, config)[1]}


def eligible_cross_reviewer(owner, reviewer):
    """Registry cross_review_eligibility: may `reviewer`'s passing review close a gate owned by `owner`?"""
    return reviewer not in REGISTRY["never_cross_reviewer"] and reviewer in REGISTRY["cross_review_eligibility"].get(owner, [])


def target_platform_problem(ev, config):
    """Why target-runtime evidence cannot count for this project (None if it can)."""
    prov = ev["provenance"]
    if prov["capture_context"] not in ("TARGET_RUNTIME", "PERFORMANCE_RUNTIME") or "target_platform" not in prov:
        return None
    targets = {t["platform"]: t for t in config["target_platforms"]}
    platform = prov["target_platform"]
    if platform not in targets:
        return (f"{ev['evidence_id']} is labelled {prov['capture_context']} on {platform}, which is not a declared project "
                f"target platform; it cannot count as target-runtime proof (record it as DIAGNOSTIC_RUNTIME)")
    refs = targets[platform].get("reference_devices")
    if ev["type"] == "DEVICE_EVIDENCE" and refs and prov.get("device") not in refs:
        return (f"{ev['evidence_id']} was captured on {prov.get('device')!r}, not a declared {platform} reference device {refs}")
    return None


def gate_evidence(gate, evidence_by_id, config=None):
    """(problems, counting evidence records) for one gate record."""
    rules = GATES[gate["gate"]]
    conditions = gate.get("applied_conditions", [])
    revision = gate["scope"]["revision"]
    scope_kind = gate["scope"]["kind"]
    problems, counting = [], []
    scope_subject = (gate["scope"]["kind"], gate["scope"]["ref"])
    mapped = {}
    for m in gate.get("evidence_applicability", []):
        a = m["assessed_by"]
        if a["kind"] == "HUMAN" or (a["kind"] == "AGENT" and a["id"] == gate.get("owner")):
            mapped[m["evidence_ref"]] = (m["evidence_subject"]["kind"], m["evidence_subject"]["ref"])
        else:
            problems.append(f"applicability of {m['evidence_ref']} approved by {a['kind']} {a['id']} is not accountable")
    for review in gate.get("cross_reviews", []):
        if not review.get("superseded") and review.get("reviewed_revision") != revision:
            problems.append(f"stale cross-review by {review['reviewer']}: reviewed {review.get('reviewed_revision')}, gate is {revision}")
    carry = {}
    for c in gate.get("evidence_carryover", []):
        a = c["assessed_by"]
        if a["kind"] == "HUMAN" or (a["kind"] == "AGENT" and a["id"] == gate.get("owner")):
            carry[c["evidence_ref"]] = c
        else:
            problems.append(f"carryover of {c['evidence_ref']} approved by {a['kind']} {a['id']} is not accountable "
                            f"(only the gate owner or an authorized human may approve carryover)")
    for ref in gate["evidence_refs"]:
        ev = evidence_by_id.get(ref)
        if ev is None:
            problems.append(f"unknown evidence {ref}")
            continue
        if ev.get("superseded"):
            problems.append(f"{ref} is superseded")
            continue
        if ev["type"] not in rules["acceptable_evidence"] + rules["insufficient_alone"]:
            problems.append(f"{ref} type {ev['type']} not acceptable for {gate['gate']}")
            continue
        prov = ev["provenance"]
        ctx = prov["capture_context"]
        if ctx not in REGISTRY["evidence_context_compatibility"][ev["type"]]:
            problems.append(f"{ref} {ev['type']} in {ctx} is an incompatible type/context combination")
            continue
        if ctx in rules["non_counting_contexts"] and scope_kind not in rules["context_scope_exceptions"].get(ctx, []):
            problems.append(f"{ref} captured in {ctx}, which does not count for {gate['gate']} on {scope_kind} scope")
            continue
        ev_subject = (ev["subject"]["kind"], ev["subject"]["ref"])
        if ev_subject != scope_subject:
            if mapped.get(ref) != ev_subject:
                problems.append(f"{ref} proves {ev_subject[0]} {ev_subject[1]}, not the gate subject "
                                f"{scope_subject[0]} {scope_subject[1]} (no accountable evidence_applicability)")
                continue
        if prov["subject_revision"] != revision:
            c = carry.get(ref)
            if c is None:
                problems.append(f"{ref} is stale: revision {prov['subject_revision']} != {revision}")
                continue
            if c["evidence_revision"] != prov["subject_revision"]:
                problems.append(f"{ref} carryover names {c['evidence_revision']} but evidence is {prov['subject_revision']}")
                continue
        if config is not None:
            why = target_platform_problem(ev, config)
            if why:
                problems.append(why)
                continue
        inst = prov.get("instrumentation")
        if ev["type"] == "PERFORMANCE_EVIDENCE" and inst and inst["timing_impact"] in ("MATERIAL", "UNKNOWN"):
            problems.append(f"{ref} instrumentation timing impact {inst['timing_impact']} cannot prove timing")
            continue
        counting.append(ev)
    owner = gate.get("owner")
    if owner not in rules["permitted_owners"]:
        problems.append(f"{owner} may not own {gate['gate']}")
    assessor = gate.get("assessed_by")
    if assessor and assessor["kind"] != "HUMAN" and assessor["id"] != owner:
        problems.append(f"assessed_by {assessor['id']} is not the accountable owner {owner}")
    for review in gate.get("cross_reviews", []):
        if review["reviewer"] == owner:
            problems.append(f"{owner} cross-reviewed its own gate")
        if review["reviewer"] in REGISTRY["never_cross_reviewer"]:
            problems.append(f"{review['reviewer']} never reviews discipline quality and cannot be a cross-reviewer")
    needs_review = gate["review_policy"] == "CROSS_REVIEW_REQUIRED" or (
        gate["review_policy"] == "HUMAN_REVIEW_REQUIRED" and gate.get("cross_review_required", False))
    if gate["status"] == "PASS" and needs_review:
        current = [r for r in gate.get("cross_reviews", []) if not r.get("superseded") and r.get("reviewed_revision") == revision]
        if not any(r["assessment"] == "PASS" and eligible_cross_reviewer(owner, r["reviewer"]) for r in current):
            problems.append(f"PASS has no current passing cross-review by a specialist eligible for {owner} "
                            f"(registry cross_review_eligibility: {REGISTRY['cross_review_eligibility'].get(owner, [])})")
    if gate["status"] == "PASS" and gate["review_policy"] == "ROUTINE":
        a = gate.get("assessed_by", {})
        if gate.get("specialist_assessment") != "PASS" or a.get("kind") != "AGENT" or a.get("id") != owner:
            problems.append("ROUTINE PASS must be the owning specialist's own PASS assessment")
    if gate["status"] == "PASS":
        types = {e["type"] for e in counting}
        base = rules["base_evidence"]
        for t in base["all_of"]:
            if t not in types:
                problems.append(f"PASS lacks required {t}")
        if base["any_of"] and not types & set(base["any_of"]):
            problems.append(f"PASS lacks one of {base['any_of']}")
        for cond in rules["conditional_evidence"]:
            if cond["condition"] not in conditions:
                continue
            for t in cond["requires"]:
                if t not in types:
                    problems.append(f"PASS lacks {t} required by {cond['condition']}")
            ctxs = cond.get("requires_context")
            if ctxs:
                claim_types = set(base["all_of"]) | set(base["any_of"])
                if not any(e["type"] in claim_types and e["provenance"]["capture_context"] in ctxs for e in counting):
                    problems.append(f"PASS lacks evidence captured in {ctxs} required by {cond['condition']}")
        credible = types & set(rules["acceptable_evidence"])
        if types and (not credible or credible <= set(rules["insufficient_alone"])):
            problems.append(f"PASS rests only on insufficient evidence {sorted(types)}")
    return problems, counting


def routing_problems(routing):
    """Cross-field routing rules JSON Schema cannot express (Phase-2 requirement)."""
    problems = []
    req = [g["gate"] for g in routing["required_gates"]]
    omitted = [g["gate"] for g in routing.get("omitted_gates", [])]
    for label, names in (("required_gates", req), ("omitted_gates", omitted)):
        for gname in sorted({n for n in names if names.count(n) > 1}):
            problems.append(f"{gname} appears more than once in {label}")
    for gname in sorted(set(req) & set(omitted)):
        problems.append(f"{gname} is both required and omitted")
    wr = REGISTRY["workflow_gate_requirements"][routing["workflow"]]
    for gname in wr["always_required"]:
        if gname in omitted:
            problems.append(f"{gname} is always required by {routing['workflow']} and cannot be omitted")
        elif gname not in req:
            problems.append(f"{gname} is always required by {routing['workflow']} but is not in required_gates")
    if wr.get("account_for_all_gates"):
        for gname in GATES:
            if gname not in req and gname not in omitted:
                problems.append(f"{routing['workflow']} must account for {gname}: required, or omitted with a reason")
    routed = {routing["primary_specialist"], *routing["secondary_specialists"], *routing["reviewers"]}
    if routing["primary_specialist"] in routing["secondary_specialists"]:
        problems.append("primary specialist repeated as secondary")
    for g in routing["required_gates"]:
        rules = GATES[g["gate"]]
        owner = g.get("owner", rules["default_owner"])
        if owner not in routed:
            problems.append(f"{g['gate']} owner {owner} is not routed")
        needed = set(rules["base_evidence"]["all_of"])
        for c in rules["conditional_evidence"]:
            if c["condition"] in g.get("applied_conditions", []):
                needed |= set(c["requires"])
        missing = needed - set(g["required_evidence"])
        if missing:
            problems.append(f"{g['gate']} routing omits required evidence {sorted(missing)}")
        any_of = rules["base_evidence"]["any_of"]
        if any_of and not set(any_of) & set(g["required_evidence"]):
            problems.append(f"{g['gate']} routing requires none of {any_of}")
        invalid = set(g["required_evidence"]) - set(rules["acceptable_evidence"]) - set(rules["insufficient_alone"])
        if invalid:
            problems.append(f"{g['gate']} routing requires {sorted(invalid)}, which is not valid evidence for {g['gate']}")
        defined = [c["condition"] for c in rules["conditional_evidence"]]
        applied = g.get("applied_conditions", [])
        declined = [u["condition"] for u in g.get("unapplied_conditions", [])]
        for label, names in (("applied_conditions", applied), ("unapplied_conditions", declined)):
            for cname in sorted({n for n in names if names.count(n) > 1}):
                problems.append(f"{g['gate']}: {cname} appears more than once in {label}")
        for cname in sorted(set(applied) & set(declined)):
            problems.append(f"{g['gate']}: {cname} is both applied and unapplied")
        for cname in sorted(set(declined) - set(defined)):
            problems.append(f"{g['gate']}: {cname} is not a condition of {g['gate']}")
        for cname in defined:
            if cname not in applied and cname not in declined:
                problems.append(f"{g['gate']}: condition {cname} is not accounted for (apply it, or decline it with a reason)")
        needs_specialist = g["review_policy"] == "CROSS_REVIEW_REQUIRED" or (
            g["review_policy"] == "HUMAN_REVIEW_REQUIRED" and g.get("cross_review_required", False))
        if needs_specialist and g["gate"] != "HUMAN_REVIEW":
            if not [r for r in routing["reviewers"] if r not in ("HUMAN", owner)]:
                problems.append(f"{g['gate']} needs a cross-reviewer other than {owner}")
        if g["review_policy"] == "HUMAN_REVIEW_REQUIRED" and "HUMAN" not in routing["reviewers"]:
            problems.append(f"{g['gate']} is HUMAN_REVIEW_REQUIRED but HUMAN is not a reviewer")
    return problems


def _resolve_pointer(doc, pointer):
    """Values at a registry field path such as /human_review/review_policy_overrides/*/decision_ref."""
    nodes = [doc]
    for part in pointer.lstrip("/").split("/"):
        nxt = []
        for n in nodes:
            if part == "*" and isinstance(n, list):
                nxt.extend(n)
            elif isinstance(n, dict) and part in n:
                nxt.append(n[part])
        nodes = nxt
    return [n for n in nodes if isinstance(n, str)]


def index_unique(records, key, label):
    """Index records by `key`, refusing duplicates. Never build a lookup before this check."""
    seen, dups = {}, []
    for r in records:
        k = r.get(key)
        if k in seen:
            dups.append(k)
        seen.setdefault(k, r)
    problems = [f"duplicate {label} {k!r}" for k in sorted(set(dups), key=str)]
    return (None if problems else seen), problems


def _get(doc, path, default=None):
    node = doc
    for part in path.strip("/").split("/"):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _ref_sites(doc, pointer):
    """(container, ref) pairs for a registry field path such as /human_review/review_policy_overrides/*/decision_ref."""
    parts = pointer.lstrip("/").split("/")
    containers = [doc]
    for part in parts[:-1]:
        nxt = []
        for n in containers:
            if part == "*" and isinstance(n, list):
                nxt.extend(x for x in n if isinstance(x, dict))
            elif isinstance(n, dict) and isinstance(n.get(part), (dict, list)):
                nxt.append(n[part])
        containers = nxt
    return [(c, c[parts[-1]]) for c in containers if isinstance(c, dict) and isinstance(c.get(parts[-1]), str)]


def _version_tuple(v):
    core, _, pre = v.partition("-")
    return tuple(int(x) for x in core.split(".")), pre


def gpos_boundary_is_governed(from_version, to_version):
    """MAJOR changes and any change involving a pre-release (alpha) version need a GPOS_UPGRADE decision."""
    (f_core, f_pre), (t_core, t_pre) = _version_tuple(from_version), _version_tuple(to_version)
    return f_core[0] != t_core[0] or bool(f_pre) or bool(t_pre)


def human_may_review(config, human_id, gate):
    """Gate-aware Human Review authorization (GOVERNANCE §12): a listed reviewer whose `gates` (if any)
    include the gate, or a decision authority holding ALL."""
    for r in config["human_review"]["reviewers"]:
        if r["id"] == human_id and ("gates" not in r or gate in r["gates"]):
            return True
    return any(a["id"] == human_id and "ALL" in a["may_decide"] for a in config["decision_authorities"])


def resolve_decision_refs(schema_name, doc, by_id, config, problems):
    """Generic resolver for every decision reference registered for `schema_name` (registry decision_ref_fields):
    existence, ACTIVE, kind, authorization, subject rules and value bindings."""
    authorities = {a["id"]: set(a["may_decide"]) for a in config["decision_authorities"]}
    project_id = config["project"]["id"]
    label = doc.get("gate_id") or doc.get("task_id") or "project-config"
    for key, kinds in REGISTRY["decision_ref_fields"].items():
        name, pointer = key.split(":", 1)
        if name != schema_name:
            continue
        for container, ref in _ref_sites(doc, pointer):
            field = f"{label}:{pointer}"
            d = by_id.get(ref)
            if d is None:
                problems.append(f"{field}: {ref} does not resolve to a Human Decision record")
                continue
            if d["status"] != "ACTIVE":
                problems.append(f"{field}: {ref} is not ACTIVE ({d['status']})")
            if d["kind"] not in kinds:
                problems.append(f"{field}: {ref} has kind {d['kind']}, expected {kinds}")
            allowed = authorities.get(d["decided_by"]["id"])
            if allowed is None or not ({"ALL", d["kind"]} & allowed):
                problems.append(f"{field}: {ref} decided by {d['decided_by']['id']}, who is not authorized for {d['kind']}")
            rule = REGISTRY["decision_subject_rules"].get(d["kind"])
            if rule and isinstance(rule["subject_kinds"], list):
                if d["subject"]["kind"] not in rule["subject_kinds"]:
                    problems.append(f"{field}: {ref} subject kind {d['subject']['kind']} is not {rule['subject_kinds']}")
                elif rule.get("subject_ref") == "PROJECT_ID" and d["subject"]["ref"] != project_id:
                    problems.append(f"{field}: {ref} subject {d['subject']['ref']} is not this project")
            for b in REGISTRY["decision_value_bindings"]:
                if b["ref"] != key:
                    continue
                cv = _get(doc, b["config"]) if b["config"].startswith("/") else container.get(b["config"], b.get("config_default"))
                dv = _get(d, b["decision"])
                if b.get("optional") and cv is None and dv is None:
                    continue
                if b.get("decision_may_omit") and dv is None:
                    continue
                if cv != dv:
                    problems.append(f"{field}: {b['config']}={cv!r} does not match decision {ref} "
                                    f"{b['decision']}={dv!r}; a decision of the right kind but a different value or subject does not authorize it")


POLICY_STRENGTH = REGISTRY["review_policy_strength"]


def effective_policy_floor(config, routing, item):
    """Minimum review policy a routed gate must use (registry effective_review_policy_precedence):
    1. a mandatory core or project Human Review trigger -> HUMAN_REVIEW_REQUIRED for subjective gates;
    2. the single applicable project review-policy override (project-wide, or scoped to routing.subject);
    3. otherwise no floor beyond the routing's own choice under the default rules.
    Returns (policy | "AMBIGUOUS" | None, source)."""
    if routing["review_triggers"] and GATES[item["gate"]]["subjective"] and item["gate"] != "HUMAN_REVIEW":
        return "HUMAN_REVIEW_REQUIRED", f"mandatory trigger {routing['review_triggers']}"
    applicable = [o for o in config["human_review"].get("review_policy_overrides", [])
                  if o["gate"] == item["gate"] and (o.get("scope") is None or o["scope"] == routing["subject"])]
    if len(applicable) > 1:
        return "AMBIGUOUS", ", ".join(o["decision_ref"] for o in applicable)
    if applicable:
        return applicable[0]["review_policy"], f"project override {applicable[0]['decision_ref']}"
    return None, "routing"


def authority_problems(config, decisions, evidence=(), routings=()):
    """Project-config decision references, lifecycle, GPOS upgrade, triggers and presentation parity."""
    problems = []
    for label, items in (("decision_authorities id", config["decision_authorities"]),
                         ("human_review.reviewers id", config["human_review"]["reviewers"]),
                         ("project trigger id", config["human_review"].get("additional_mandatory_triggers", []))):
        problems += index_unique(items, "id", label)[1]
    by_id, dup = index_unique(decisions, "decision_id", "decision_id")
    problems += dup
    if problems:
        return problems  # never resolve references against ambiguous identifiers
    resolve_decision_refs("project-config", config, by_id, config, problems)

    stage = config.get("lifecycle_stage", "CONCEPT")
    if stage != "CONCEPT":
        d = by_id.get(config.get("lifecycle_decision_ref"))
        if d and d["kind"] == "LIFECYCLE_TRANSITION":
            frm, to = d["transition"]["from"], d["transition"]["to"]
            if to != stage:
                problems.append(f"lifecycle: decision enters {to}, but project claims {stage}")
            stages = REGISTRY["lifecycle_stages"]
            lt = REGISTRY["lifecycle_transitions"]
            forward = [frm, to] in lt["forward"]
            backward = lt["backward_allowed"] and stages.index(to) < stages.index(frm)
            if not (forward or backward):
                problems.append(f"lifecycle: {frm} -> {to} is not an allowed transition")
            if [frm, to] in lt["requires_golden_cell_waiver"] and config["golden_gameplay_cell"].get("status") != "WAIVED":
                problems.append(f"lifecycle: {frm} -> {to} requires a Golden Cell waiver")

    up = config.get("gpos_upgrade")
    if up:
        frm, to = up["from_version"], config["gpos_version"]
        if frm == to:
            problems.append(f"gpos_upgrade: from_version equals gpos_version {to}")
        elif gpos_boundary_is_governed(frm, to) and "decision_ref" not in up:
            problems.append(f"gpos_upgrade: {frm} -> {to} crosses a governed boundary and requires a GPOS_UPGRADE decision")

    overrides = config["human_review"].get("review_policy_overrides", [])
    keys = [(o["gate"], json.dumps(o.get("scope"), sort_keys=True)) for o in overrides]
    for k in sorted({k for k in keys if keys.count(k) > 1}):
        problems.append(f"duplicate review-policy override for {k[0]} scope {k[1]}")
    parity = config.get("presentation", {}).get("target_presentation_differs_from_editor", "UNDECIDED")
    project_triggers = {t["id"] for t in config["human_review"].get("additional_mandatory_triggers", [])}
    prefix = REGISTRY["project_trigger_prefix"]
    for routing in routings:  # every routing, however many
        for g in routing["required_gates"]:
            declined = [u for u in g.get("unapplied_conditions", []) if u["condition"] == "TARGET_PRESENTATION_DIFFERS"]
            if declined and parity != "NO":
                problems.append(f"routing {routing['task_id']}: {g['gate']} declines TARGET_PRESENTATION_DIFFERS "
                                f"but presentation parity is {parity}, not a decided NO")
        for g in routing["required_gates"]:
            defines_tpd = any(c["condition"] == "TARGET_PRESENTATION_DIFFERS" for c in GATES[g["gate"]]["conditional_evidence"])
            if defines_tpd and parity == "YES" and "TARGET_PRESENTATION_DIFFERS" not in g.get("applied_conditions", []):
                problems.append(f"routing {routing['task_id']}: {g['gate']} must apply TARGET_PRESENTATION_DIFFERS "
                                f"because project presentation parity is a decided YES")
            floor, source = effective_policy_floor(config, routing, g)
            if floor == "AMBIGUOUS":
                problems.append(f"routing {routing['task_id']}: {g['gate']} has more than one applicable review-policy override ({source})")
            elif floor and POLICY_STRENGTH[g["review_policy"]] < POLICY_STRENGTH[floor]:
                problems.append(f"routing {routing['task_id']}: {g['gate']} uses {g['review_policy']}, weaker than the effective "
                                f"policy {floor} required by {source}")
        for trig in routing["review_triggers"]:
            if trig.startswith(prefix) and trig[len(prefix):] not in project_triggers:
                problems.append(f"routing {routing['task_id']}: {trig} is not a project trigger declared in project config")

    reviewers = {r["id"] for r in config["human_review"]["reviewers"]} | {a["id"] for a in config["decision_authorities"]}
    for ev in evidence:
        if ev["type"] == "HUMAN_EVIDENCE" and ev["source"]["id"] not in reviewers:
            problems.append(f"{ev['evidence_id']}: {ev['source']['id']} is not a listed reviewer or decision authority")
    return problems


def routing_gate_problems(routing, gates, evidence_by_id=None, config=None):
    """Routing <-> gate linkage integrity. Returns (problems, missing): problems are disagreements
    (ambiguous, mismatched or unrouted gate records); missing lists required gates with no record yet —
    not an integrity error, but NOT_RUN for readiness."""
    problems, missing = [], []
    task = routing["task_id"]
    linked = [g for g in gates if g.get("routing_ref") == task]
    subject = (routing["subject"]["kind"], routing["subject"]["ref"])
    required = {i["gate"]: i for i in routing["required_gates"]}
    for g in linked:
        if g["gate"] not in required:
            problems.append(f"{g['gate_id']}: {g['gate']} was raised after routing {task}; revise routing before readiness")
    for gname, item in required.items():
        recs = [g for g in linked if g["gate"] == gname]
        if not recs:
            missing.append(gname)
            continue
        if len(recs) > 1:
            problems.append(f"routing {task}: {gname} has {len(recs)} gate records; ambiguous")
            continue
        g = recs[0]
        expected_owner = item.get("owner", GATES[gname]["default_owner"])
        checks = [
            ("owner", g["owner"], expected_owner),
            ("blocking", g["blocking"], item["blocking"]),
            ("review_policy", g["review_policy"], item["review_policy"]),
            ("applied_conditions", sorted(g.get("applied_conditions", [])), sorted(item.get("applied_conditions", []))),
            ("cross_review_required", g.get("cross_review_required", g["review_policy"] == "CROSS_REVIEW_REQUIRED"),
             item.get("cross_review_required", item["review_policy"] == "CROSS_REVIEW_REQUIRED")),
            ("subject", (g["scope"]["kind"], g["scope"]["ref"]), subject),
        ]
        for label, got, want in checks:
            if got != want:
                problems.append(f"{g['gate_id']}: {label} {got!r} does not match routing {task} ({want!r})")
        routed_reviewers = {r for r in routing["reviewers"] if r != "HUMAN"}
        revision = g["scope"]["revision"]
        contributing = [r for r in g.get("cross_reviews", [])
                        if not r.get("superseded") and r["reviewed_revision"] == revision and r["assessment"] == "PASS"]
        for r in contributing:
            if r["reviewer"] not in routed_reviewers:
                problems.append(f"{g['gate_id']}: cross-review by {r['reviewer']} cannot contribute; not a routed reviewer of {task}")
            elif not eligible_cross_reviewer(g["owner"], r["reviewer"]):
                problems.append(f"{g['gate_id']}: cross-review by {r['reviewer']} cannot contribute; routed but not eligible "
                                f"to review {g['owner']} (escalate to HUMAN_REVIEW_REQUIRED for a non-standard reviewer)")
        needs_review = g["review_policy"] == "CROSS_REVIEW_REQUIRED" or (
            g["review_policy"] == "HUMAN_REVIEW_REQUIRED" and g.get("cross_review_required", False))
        if g["status"] == "PASS" and needs_review and not any(r["reviewer"] in routed_reviewers for r in contributing):
            problems.append(f"{g['gate_id']}: PASS needs a current passing cross-review by a routed reviewer of {task}")
        if g["status"] == "PASS" and evidence_by_id is not None:
            absent = set(item["required_evidence"]) - counting_evidence_types(g, evidence_by_id, config)
            if absent:
                problems.append(f"{g['gate_id']}: PASS lacks routing-required evidence {sorted(absent)} for {task}")
    return problems, missing


def release_coverage_problems(config, routing, gates, evidence_by_id):
    """Release: every PRIMARY target platform has counting DEVICE and PERFORMANCE evidence on that platform
    (registry release_primary_platform_coverage). One platform's evidence never covers another."""
    problems = []
    if routing["workflow"] != "release":
        return problems
    linked = {g["gate"]: g for g in gates if g.get("routing_ref") == routing["task_id"]}
    primaries = [t["platform"] for t in config["target_platforms"] if t["tier"] == "PRIMARY"]
    for gname, etype in REGISTRY["release_primary_platform_coverage"].items():
        g = linked.get(gname)
        if g is None or g["status"] != "PASS":
            continue  # missing or unfinished gates already make the release not ready
        counted = gate_evidence(g, evidence_by_id, config)[1]
        for platform in primaries:
            if platform == "UNDECIDED":
                problems.append(f"release {routing['task_id']}: a PRIMARY target platform is UNDECIDED; release coverage cannot be shown")
                continue
            if not any(e["type"] == etype and e["provenance"].get("target_platform") == platform
                       and e["provenance"]["capture_context"] in ("TARGET_RUNTIME", "PERFORMANCE_RUNTIME") for e in counted):
                problems.append(f"release {routing['task_id']}: {gname} has no counting {etype} for PRIMARY platform {platform}")
    return problems


def routed_scope_ready(config, routing, gates, evidence=(), decisions=()):
    """Routing-aware readiness for one routed scope. Ready only when the whole record set is valid under
    project authority (effective review policy, overrides, triggers, presentation parity, decision references,
    routed evidence and reviewers), every blocking required gate has exactly one linked record, and each is PASS.
    A missing record is NOT_RUN; NOT_APPLICABLE never satisfies a gate routing still requires."""
    if record_set_problems(config, decisions, evidence, gates, [routing]):
        return False
    missing = routing_gate_problems(routing, gates)[1]
    if any(i["gate"] in missing for i in routing["required_gates"] if i["blocking"]):
        return False
    linked = {g["gate"]: g for g in gates if g.get("routing_ref") == routing["task_id"]}
    return all(linked[i["gate"]]["status"] == "PASS" for i in routing["required_gates"] if i["blocking"])


def record_set_problems(config, decisions=(), evidence=(), gates=(), routings=()):
    """Whole-record-set validation (Phase-2 requirement). Duplicate ids are rejected before any lookup."""
    problems = []
    ev_by_id, dup_ev = index_unique(evidence, "evidence_id", "evidence_id")
    gate_by_id, dup_gate = index_unique(gates, "gate_id", "gate_id")
    routing_by_id, dup_routing = index_unique(routings, "task_id", "routing task_id")
    problems += dup_ev + dup_gate + dup_routing
    problems += authority_problems(config, decisions, evidence, routings)
    if dup_ev or dup_gate or dup_routing or any(p.startswith("duplicate") for p in problems):
        return problems
    by_id = index_unique(decisions, "decision_id", "decision_id")[0]
    for r in routings:
        problems += routing_problems(r)
        resolve_decision_refs("task-routing", r, by_id, config, problems)
        problems += routing_gate_problems(r, gates, ev_by_id, config)[0]
        problems += release_coverage_problems(config, r, gates, ev_by_id)
    for g in gates:
        resolve_decision_refs("gate", g, by_id, config, problems)
        if g.get("routing_ref") and g["routing_ref"] not in routing_by_id:
            problems.append(f"{g['gate_id']}: routing_ref {g['routing_ref']} does not resolve to a routing record")
        problems += gate_evidence_problems(g, ev_by_id, config)
        a = g.get("assessed_by")
        if a and a["kind"] == "HUMAN" and not human_may_review(config, a["id"], g["gate"]):
            problems.append(f"{g['gate_id']}: {a['id']} is not authorized to assess {g['gate']}")
        for c in g.get("evidence_carryover", []) + g.get("evidence_applicability", []):
            ca = c["assessed_by"]
            if ca["kind"] == "HUMAN" and not human_may_review(config, ca["id"], g["gate"]):
                problems.append(f"{g['gate_id']}: {ca['id']} is not authorized to approve evidence reuse for {g['gate']}")
        ref = g.get("human_review_ref")
        if ref and g["gate"] != "HUMAN_REVIEW":
            hr = gate_by_id.get(ref)
            if hr is None or hr["gate"] != "HUMAN_REVIEW":
                problems.append(f"{g['gate_id']}: human_review_ref {ref} does not resolve to a HUMAN_REVIEW record")
                continue
            if hr["status"] != "PASS" or hr["scope"] != g["scope"]:
                problems.append(f"{g['gate_id']}: {ref} is not a PASS for the same scope kind, ref and revision")
            humans = [hr["assessed_by"]["id"]] if hr.get("assessed_by", {}).get("kind") == "HUMAN" else []
            humans += [ev_by_id[e]["source"]["id"] for e in hr["evidence_refs"]
                       if e in ev_by_id and ev_by_id[e]["type"] == "HUMAN_EVIDENCE"]
            for h in humans:
                if not human_may_review(config, h, g["gate"]):
                    problems.append(f"{g['gate_id']}: {h} is not authorized to review {g['gate']}")
    return problems


def scope_ready(gate_records):
    """Gate-only helper: every blocking gate that EXISTS is PASS. Not proof of routed readiness —
    it cannot see missing gates. Use routed_scope_ready for a routed task."""
    return all(g["status"] == "PASS" for g in gate_records if g["blocking"] and g["status"] != "NOT_APPLICABLE")


# ---------------------------------------------------------------- phase boundary (Phase 2A)
#
# Phase 1 allowed no code outside tests/ and only README.md in adapters/ and tools/.
# Phase 2A (alpha.8) added the production validator package gpos/ and its Markdown
# documentation under tools/validator/. Phase 2B (alpha.9) adds the agent adapter layer as
# code in gpos/adapters/ and its documentation in adapters/*.md. The boundary tests of
# alpha.4–alpha.7 (B09, C14, D10, E06, X08) asserted the Phase-1 file layout; they now assert
# this single boundary instead, so the protection (no code outside gpos/ and tests/, docs-only
# adapters/ and tools/, production code independent of tests and of the network) is preserved.

CODE_SUFFIXES = {".py", ".js", ".ts", ".cs", ".sh", ".ps1"}
CODE_ROOTS = ("tests", "gpos")
PRODUCTION_FORBIDDEN_IMPORTS = {
    "tests", "validate_framework", "schema_lite", "jsonschema", "unittest",
    "socket", "ssl", "http", "urllib", "urllib3", "requests", "ftplib", "smtplib", "asyncio",
}
# Starting an operating-system process is what the Phase-2C tool adapter foundation exists to do, so it
# cannot be banned outright any more. It is confined instead: exactly one audited module in gpos/ may
# import it, and the boundary test names that module. Everywhere else in gpos/ the ban still holds, and
# the ban on tests and network modules above is unchanged and applies to every module including this one.
PROCESS_EXECUTION_IMPORTS = {"subprocess", "multiprocessing", "pty"}
PROCESS_BOUNDARY = "gpos/tools/process.py"


def _imports(path):
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def phase_boundary_problems():
    problems = []
    files = lambda d: sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / d).rglob("*")  # untracked OS/bytecode files ignored
                             if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith("."))
    if "adapters/README.md" not in files("adapters"):
        problems.append("adapters/README.md is missing")
    for f in files("adapters"):
        if not f.endswith(".md"):
            problems.append(f"adapters/ holds documentation only (adapter code lives in gpos/adapters/): {f}")
    for f in files("tools"):
        if not f.endswith(".md"):
            problems.append(f"tools/ holds documentation only (the validator lives in gpos/): {f}")
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if p.suffix in CODE_SUFFIXES and rel.parts[0] not in CODE_ROOTS and ".git" not in p.parts:
            problems.append(f"code outside {CODE_ROOTS}: {rel}")
    for p in sorted((ROOT / "gpos").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        bad = _imports(p) & PRODUCTION_FORBIDDEN_IMPORTS
        if bad:
            problems.append(f"{rel} imports {sorted(bad)} (production code must not depend on tests or the network)")
        spawning = _imports(p) & PROCESS_EXECUTION_IMPORTS
        if spawning and rel != PROCESS_BOUNDARY:
            problems.append(f"{rel} imports {sorted(spawning)}: only {PROCESS_BOUNDARY} may start a process")
    return problems


# ---------------------------------------------------------------- structure (T01–T05)

class T01_SkillsExist(unittest.TestCase):
    def test_all_thirteen_skills_exist(self):
        self.assertEqual(len(REGISTRY["skills"]), 13)
        on_disk = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(on_disk, sorted(REGISTRY["skills"]))

    def test_front_matter_name_matches_directory(self):
        for s in REGISTRY["skills"]:
            self.assertEqual(front_matter(skill_text(s)).get("name"), s)


class T02_SkillContractSections(unittest.TestCase):
    def test_every_skill_has_every_section_in_order(self):
        required = REGISTRY["skill_contract_sections"]
        for s in REGISTRY["skills"]:
            order, sections = h2_sections(skill_text(s))
            self.assertEqual(order, required, f"{s}: sections {order}")
            for name in required:
                self.assertTrue(sections[name].strip(), f"{s}: section {name} is empty")


class T03_SkillMaturity(unittest.TestCase):
    def test_every_skill_is_draft(self):
        for s in REGISTRY["skills"]:
            self.assertEqual(front_matter(skill_text(s)).get("maturity"), "DRAFT", s)
            maturity = skill_section(s, "MATURITY")
            self.assertIn("`DRAFT`", maturity, s)
            self.assertNotRegex(maturity, r"`(PILOTED|PROVEN)`", s)
            self.assertIn("Promotion history: none", maturity, s)

    def test_maturity_levels_documented(self):
        doc = read("core/SKILL-MATURITY.md")
        for level in REGISTRY["maturity_levels"]:
            self.assertIn(f"`{level}`", doc)
        self.assertIn("must not promote itself", doc)


class T04_WorkflowContractSections(unittest.TestCase):
    def test_workflow_files_match_registry(self):
        on_disk = sorted(p.stem for p in (ROOT / "workflows").glob("*.md"))
        self.assertEqual(on_disk, sorted(REGISTRY["workflows"]))

    def test_every_workflow_has_every_section_in_order(self):
        required = REGISTRY["workflow_contract_sections"]
        for wf in REGISTRY["workflows"]:
            order, sections = h2_sections(workflow_text(wf))
            self.assertEqual(order, required, f"{wf}: sections {order}")
            for name in required:
                self.assertTrue(sections[name].strip(), f"{wf}: section {name} is empty")

    def test_workflow_gates_are_real(self):
        for wf in REGISTRY["workflows"]:
            section = h2_sections(workflow_text(wf))[1]["REQUIRED GATES"]
            self.assertTrue([t for t in backticked(section) if t in GATES], f"{wf}: REQUIRED GATES names no gate")

    def test_postmortem_asks_the_three_questions(self):
        for wf in REGISTRY["workflows"]:
            pm = h2_sections(workflow_text(wf))[1]["POSTMORTEM / LESSON EXTRACTION"]
            self.assertIn("Which GPOS rule helped?", pm, wf)
            self.assertIn("missing, wrong or unclear", pm, wf)
            self.assertIn("project-specific or framework-general", pm, wf)
            self.assertIn("not modified during project work", pm, wf)


class T05_RequiredDocumentsExist(unittest.TestCase):
    def test_core_documents(self):
        for doc in REGISTRY["core_documents"]:
            self.assertTrue((ROOT / "core" / doc).is_file(), doc)
        self.assertEqual(sorted(p.name for p in (ROOT / "core").glob("*.md")), sorted(REGISTRY["core_documents"]))

    def test_repository_documents(self):
        for rel in ["README.md", "VERSION", "CHANGELOG.md", "adapters/README.md",
                    "tools/README.md", "tests/README.md", "tests/validate_framework.py"]:
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_templates(self):
        for t in REGISTRY["project_authority_files"] + ["HUMAN-REVIEW.md"]:
            self.assertTrue((ROOT / "templates" / t).is_file(), t)


# ---------------------------------------------------------------- vocabulary (T06–T08)

class T06_GateStatusesExact(unittest.TestCase):
    def test_statuses_identical_everywhere(self):
        expected = ["PASS", "FAIL", "CHANGES_REQUIRED", "NOT_APPLICABLE", "NOT_RUN"]
        self.assertEqual(REGISTRY["gate_statuses"], expected)
        self.assertEqual(SCHEMAS["gate"]["$defs"]["status"]["enum"], expected)
        rows = table_rows(h2_sections(read("core/QUALITY-GATES.md"))[1]["2. Statuses"])
        self.assertEqual([r[0].strip("`") for r in rows], expected)

    def test_assessments_are_subset_of_statuses(self):
        allowed = set(REGISTRY["gate_statuses"])
        gate = SCHEMAS["gate"]["properties"]
        self.assertTrue(set(gate["specialist_assessment"]["enum"]) <= allowed)
        self.assertTrue(set(gate["cross_reviews"]["items"]["properties"]["assessment"]["enum"]) <= allowed)

    def test_gates_identical_everywhere(self):
        gates = list(GATES)
        self.assertEqual(SCHEMAS["gate"]["$defs"]["gate"]["enum"], gates)
        self.assertEqual(SCHEMAS["task-routing"]["$defs"]["gate"]["enum"], gates)
        rows = table_rows(h2_sections(read("core/QUALITY-GATES.md"))[1]["1. Gates"])
        self.assertEqual([r[0].strip("`") for r in rows], gates)


class T07_NotRunIsNotPass(unittest.TestCase):
    def test_not_run_valid_without_evidence_but_pass_is_not(self):
        base = load_json("examples/example-gate-record.json")
        not_run = dict(base, status="NOT_RUN", evidence_refs=[])
        not_run.pop("required_changes")
        self.assertEqual(validate("gate", not_run), [])
        passed = dict(not_run, status="PASS", cross_reviews=[{"reviewer": "camera-composition", "assessment": "PASS", "reviewed_revision": "rev-example-0002"}])
        self.assertNotEqual(validate("gate", passed), [])

    def test_not_run_blocks_readiness(self):
        g = {"gate": "ANIMATION", "blocking": True}
        self.assertFalse(scope_ready([dict(g, status="NOT_RUN")]))
        self.assertFalse(scope_ready([dict(g, status="CHANGES_REQUIRED")]))
        self.assertTrue(scope_ready([dict(g, status="PASS")]))
        self.assertTrue(scope_ready([dict(g, status="NOT_APPLICABLE")]))

    def test_documents_state_the_rule(self):
        self.assertIn("`NOT_RUN` never means `PASS`", read("core/PRINCIPLES.md"))
        self.assertIn("**Never implies `PASS`.**", read("core/QUALITY-GATES.md"))


class T08_EvidenceTypesRepresented(unittest.TestCase):
    def test_evidence_types_identical_everywhere(self):
        expected = ["CODE_EVIDENCE", "TEST_EVIDENCE", "RUNTIME_EVIDENCE", "VISUAL_EVIDENCE", "MOTION_EVIDENCE",
                    "AUDIO_EVIDENCE", "DEVICE_EVIDENCE", "PERFORMANCE_EVIDENCE", "PERSISTENCE_EVIDENCE", "HUMAN_EVIDENCE"]
        self.assertEqual(REGISTRY["evidence_types"], expected)
        self.assertEqual(SCHEMAS["evidence"]["properties"]["type"]["enum"], expected)
        self.assertEqual(SCHEMAS["task-routing"]["$defs"]["evidence_type"]["enum"], expected)
        rows = table_rows(h2_sections(read("core/EVIDENCE-RULES.md"))[1]["1. Evidence types"])
        self.assertEqual([r[0].strip("`") for r in rows], expected)

    def test_every_evidence_type_is_accepted_by_some_gate(self):
        accepted = {t for g in GATES.values() for t in g["acceptable_evidence"]}
        self.assertEqual(accepted, set(REGISTRY["evidence_types"]))

    def test_registry_gate_rules_are_coherent(self):
        for name, g in GATES.items():
            base = g["base_evidence"]
            needed = set(base["all_of"]) | set(base["any_of"])
            self.assertTrue(needed, f"{name} requires nothing")
            for c in g["conditional_evidence"]:
                self.assertIn(c["condition"], CONDITIONS, name)
                needed |= set(c["requires"])
                self.assertTrue(c["requires"] or c.get("requires_context"), f"{name}/{c['condition']} adds nothing")
                for ctx in c.get("requires_context", []):
                    self.assertIn(ctx, REGISTRY["capture_contexts"], name)
            self.assertTrue(needed <= set(g["acceptable_evidence"]), name)
            self.assertFalse(set(g["insufficient_alone"]) & set(base["all_of"]), name)
            self.assertTrue(set(g["non_counting_contexts"]) <= set(REGISTRY["capture_contexts"]), name)
            self.assertIn(g["default_review_policy"], REGISTRY["review_policies"], name)
        self.assertEqual(GATES["HUMAN_REVIEW"]["acceptable_evidence"], ["HUMAN_EVIDENCE"])


# ---------------------------------------------------------------- schema behaviour (T09–T14)

class T09_InvalidEvidenceRejected(unittest.TestCase):
    def test_unknown_evidence_type(self):
        doc = dict(load_json("examples/example-evidence-record.json"), type="SCREENSHOT_EVIDENCE")
        self.assertIn("enum", [e[1] for e in validate("evidence", doc)])

    def test_agent_cannot_author_human_evidence(self):
        doc = load_json("examples/example-evidence-record.json")
        doc["type"] = "HUMAN_EVIDENCE"
        doc["provenance"] = {"capture_context": "HUMAN_RECORD", "subject_revision": "r"}
        self.assertNotEqual(validate("evidence", doc), [])


class T10_RoutingRequiresPrimary(unittest.TestCase):
    def test_primary_required_and_single(self):
        base = load_json("examples/example-task-routing.json")
        self.assertEqual(validate("task-routing", base), [])
        missing = {k: v for k, v in base.items() if k != "primary_specialist"}
        self.assertIn("required", [e[1] for e in validate("task-routing", missing)])
        self.assertNotEqual(validate("task-routing", dict(base, primary_specialist=["character-animation", "technical-art"])), [])
        self.assertNotEqual(validate("task-routing", dict(base, primary_specialist="game-developer")), [])


class T11_HumanReviewCanBeMandatory(unittest.TestCase):
    def test_human_review_required_policy_needs_linked_review(self):
        doc = build({"base": "examples/example-gate-record.json", "remove": ["/required_changes", "/cross_reviews"],
                     "set": {"/status": "PASS", "/review_policy": "HUMAN_REVIEW_REQUIRED", "/specialist_assessment": "PASS"}})
        self.assertNotEqual(validate("gate", doc), [])
        self.assertEqual(validate("gate", dict(doc, human_review_ref="GATE-0002")), [])

    def test_agent_cannot_decide_human_review(self):
        doc = build({"base": "examples/example-gate-record.json",
                     "remove": ["/required_changes", "/applied_conditions", "/cross_reviews", "/specialist_assessment"],
                     "set": {"/gate": "HUMAN_REVIEW", "/owner": "HUMAN", "/status": "PASS",
                             "/review_policy": "HUMAN_REVIEW_REQUIRED"}})
        self.assertNotEqual(validate("gate", doc), [])
        self.assertEqual(validate("gate", dict(doc, assessed_by={"kind": "HUMAN", "id": "creative-lead"})), [])

    def test_triggered_routing_needs_human_review(self):
        r = build({"base": "examples/example-task-routing.json", "set": {"/review_triggers": ["MAJOR_BASELINE"]}})
        self.assertNotEqual(validate("task-routing", r), [])


class T12_MinimalProjectConfigValid(unittest.TestCase):
    def test_minimal_config(self):
        doc = load_json("examples/minimal-project-config.json")
        self.assertEqual(validate("project-config", doc), [])
        self.assertEqual(doc["gpos_version"], REGISTRY["gpos_version"])
        self.assertEqual(doc["enabled_adapters"], [])


class T13_InvalidProjectConfigFails(unittest.TestCase):
    def test_invalid_configs(self):
        base = load_json("examples/minimal-project-config.json")
        for bad in (
            {},
            {k: v for k, v in base.items() if k != "engine"},
            dict(base, gpos_version="latest"),
            dict(base, target_platforms=[]),
            dict(base, golden_gameplay_cell={"required": False}),
            dict(base, engine_specific_settings={}),
        ):
            self.assertNotEqual(validate("project-config", bad), [], bad)


class T14_GoldenCellRequiredByDefault(unittest.TestCase):
    def test_schema_default_and_waiver(self):
        ggc = SCHEMAS["project-config"]["properties"]["golden_gameplay_cell"]
        self.assertIs(ggc["properties"]["required"]["default"], True)
        self.assertIn("golden_gameplay_cell", SCHEMAS["project-config"]["required"])
        self.assertTrue(load_json("examples/minimal-project-config.json")["golden_gameplay_cell"]["required"])

    def test_policy_documented(self):
        doc = read("core/GOLDEN-GAMEPLAY-CELL.md")
        self.assertIn("**Required by default**", doc)
        self.assertIn("**Waivable only by Human Decision.**", doc)
        self.assertIn("a missing config value is read as *required*", doc)
        self.assertIn("Without explanation, does a short representative gameplay recording look and feel like the intended production game?", doc)

    def test_lifecycle_does_not_conflict(self):
        stages = REGISTRY["lifecycle_stages"]
        self.assertLess(stages.index("GOLDEN_CELL"), stages.index("PRODUCTION"))
        lc = read("core/PRODUCTION-LIFECYCLE.md")
        self.assertIn("`GOLDEN_CELL` → `PRODUCTION`", lc)
        self.assertIn("recorded Human waiver", lc)


# ---------------------------------------------------------------- contract content (T15–T20)

class T15_SkillExamples(unittest.TestCase):
    def test_at_least_two_concrete_examples(self):
        for s in REGISTRY["skills"]:
            examples = re.split(r"^### ", skill_section(s, "EXAMPLES"), flags=re.M)[1:]
            self.assertGreaterEqual(len(examples), 2, s)
            for ex in examples:
                primary = re.search(r"Primary: `([a-z-]+)`", ex)
                self.assertTrue(primary and primary.group(1) in REGISTRY["skills"], f"{s}: example lacks a real primary")
                self.assertTrue(any(t in GATES for t in backticked(ex)), f"{s}: example names no gate")


def owns_and_not(skill):
    return skill_section(skill, "OWNS").lower(), skill_section(skill, "DOES NOT OWN").lower()


class T16_LevelDesignVsEnvironmentArt(unittest.TestCase):
    def test_no_cross_claim(self):
        ld_owns, _ = owns_and_not("level-design")
        ea_owns, _ = owns_and_not("environment-art")
        for term in ["set dressing", "prop density", r"\bsurface", r"\bmaterial", "visual massing", "palette", "lighting"]:
            self.assertNotRegex(ld_owns, term, f"level-design claims {term}")
        for term in ["traversal", r"\broutes?\b", "encounter layout", "sightline", "collision", "space metrics", "pacing"]:
            self.assertNotRegex(ea_owns, term, f"environment-art claims {term}")

    def test_explicit_non_ownership(self):
        _, ld_not = owns_and_not("level-design")
        _, ea_not = owns_and_not("environment-art")
        self.assertIn("environment-art", ld_not)
        for term in ["traversal", "collision", "level design"]:
            self.assertIn(term, ea_not)


class T17_ArtDirectionVsTechnicalArt(unittest.TestCase):
    def test_no_cross_claim(self):
        ad_owns, ad_not = owns_and_not("art-direction")
        ta_owns, ta_not = owns_and_not("technical-art")
        for term in ["rigging", "skinning", "topology", r"\buv", "shader", r"\blod\b", "optimization", "batching", "export"]:
            self.assertNotRegex(ad_owns, term, f"art-direction claims {term}")
        for term in ["visual identity", "shape language", "silhouette language", "palette", "style consistency",
                     "lighting intent", r"\bmood\b"]:
            self.assertNotRegex(ta_owns, term, f"technical-art claims {term}")
        self.assertIn("art direction", ta_not)
        for term in ["rigging", "optimization", "final human creative approval"]:
            self.assertIn(term, ad_not)


class T18_QACannotOverrideSubjectiveGates(unittest.TestCase):
    def test_contract_statement(self):
        not_own = skill_section("qa-performance", "DOES NOT OWN")
        self.assertIn("**cannot override**", not_own)
        for g in SUBJECTIVE_DISCIPLINE_GATES:
            self.assertIn(f"`{g}`", not_own, g)

    def test_qa_owns_no_subjective_gate(self):
        for g in SUBJECTIVE_DISCIPLINE_GATES:
            self.assertNotIn("qa-performance", GATES[g]["permitted_owners"])
            self.assertNotIn(g, may_own("qa-performance"))


class T19_AnimationRequiresMotionEvidence(unittest.TestCase):
    def test_registry_and_contract(self):
        rules = GATES["ANIMATION"]
        self.assertIn("MOTION_EVIDENCE", rules["base_evidence"]["all_of"])
        for t in ["VISUAL_EVIDENCE", "TEST_EVIDENCE", "RUNTIME_EVIDENCE"]:
            self.assertIn(t, rules["insufficient_alone"])
        self.assertIn("`MOTION_EVIDENCE` is required", skill_section("character-animation", "REQUIRED EVIDENCE"))

    def test_required_coverage_listed(self):
        owns = skill_section("character-animation", "OWNS").lower()
        for term in ["idle", "start movement", "locomotion", "stop", "direction changes", "45°", "90°",
                     "180°", "interaction poses", "settle", "blending", "movement-speed matching",
                     "foot sliding", "root and hip", "silhouette", "deformation stability"]:
            self.assertIn(term, owns, term)


class T20_CameraAndFeelCanRequireMotion(unittest.TestCase):
    def test_camera_conditional_motion(self):
        conds = {c["condition"]: c for c in GATES["CAMERA_COMPOSITION"]["conditional_evidence"]}
        self.assertIn("MOTION_EVIDENCE", conds["CAMERA_MOTION_CLAIM"]["requires"])
        self.assertIn("CAMERA_MOTION_CLAIM", skill_section("camera-composition", "REQUIRED EVIDENCE"))

    def test_game_feel_requires_motion(self):
        self.assertIn("MOTION_EVIDENCE", GATES["GAME_FEEL_VFX"]["base_evidence"]["all_of"])
        self.assertIn("CODE_EVIDENCE", GATES["GAME_FEEL_VFX"]["insufficient_alone"])

    def test_camera_coverage_listed(self):
        owns = skill_section("camera-composition", "OWNS").lower()
        for term in ["field of view", "distance", "angle", "follow", "dead zone", "look-ahead",
                     "screen presence", "actor readability", "foreground", "occlusion",
                     "focal hierarchy", "motion comfort", "environment readability"]:
            self.assertIn(term, owns, term)


# ---------------------------------------------------------------- hardening (H01–H09)

class H01_ReviewPolicy(unittest.TestCase):
    POLICIES = ["ROUTINE", "CROSS_REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED"]

    def test_policies_identical_everywhere(self):
        self.assertEqual(REGISTRY["review_policies"], self.POLICIES)
        self.assertEqual(SCHEMAS["gate"]["$defs"]["review_policy"]["enum"], self.POLICIES)
        self.assertEqual(SCHEMAS["task-routing"]["$defs"]["review_policy"]["enum"], self.POLICIES)
        overrides = SCHEMAS["project-config"]["properties"]["human_review"]["properties"]["review_policy_overrides"]
        self.assertEqual(overrides["items"]["properties"]["review_policy"]["enum"], self.POLICIES)
        rows = table_rows(h2_sections(read("core/QUALITY-GATES.md"))[1]["7. Review policy"])
        self.assertEqual([r[0].strip("`") for r in rows if r[0].strip("`") in self.POLICIES], self.POLICIES)

    def test_defaults_are_proportional(self):
        for g, d in GATES.items():
            if g == "HUMAN_REVIEW":
                self.assertEqual(d["default_review_policy"], "HUMAN_REVIEW_REQUIRED")
            elif d["subjective"]:
                self.assertEqual(d["default_review_policy"], "CROSS_REVIEW_REQUIRED", g)
            else:
                self.assertEqual(d["default_review_policy"], "ROUTINE", g)

    def test_quality_gates_table_matches_registry_defaults(self):
        for row in table_rows(h2_sections(read("core/QUALITY-GATES.md"))[1]["1. Gates"]):
            self.assertEqual(row[3].strip("`"), GATES[row[0].strip("`")]["default_review_policy"], row[0])

    def test_skill_contracts_match_registry_defaults(self):
        for s in REGISTRY["skills"]:
            section = skill_section(s, "HUMAN REVIEW REQUIREMENTS")
            lines = re.findall(r"Default review policy for ((?:`[A-Z_]+`(?:, | and )?)+)[^:\n]*: `([A-Z_]+)`", section)
            declared = {g: pol for gates, pol in lines for g in backticked(gates)}
            for gate in may_own(s):
                self.assertIn(gate, declared, f"{s}: no default review policy line for {gate}")
                self.assertEqual(declared[gate], GATES[gate]["default_review_policy"], f"{s}/{gate}")

    def test_routine_does_not_universally_require_human(self):
        routing = load_json("examples/example-task-routing.json")
        self.assertFalse(routing["human_review"]["required"])
        self.assertEqual(validate("task-routing", routing), [])
        gate = build({"base": "examples/example-gate-record.json", "remove": ["/required_changes", "/cross_reviews"],
                      "set": {"/status": "PASS", "/review_policy": "ROUTINE", "/routine_basis": "inside approved baseline",
                              "/specialist_assessment": "PASS"}})
        self.assertEqual(validate("gate", gate), [])

    def test_routine_needs_basis_and_cross_review_needs_passing_reviewer(self):
        gate = build({"base": "examples/example-gate-record.json", "set": {"/review_policy": "ROUTINE"}})
        self.assertIn("required", [e[1] for e in validate("gate", gate)])
        gate = build({"base": "examples/example-gate-record.json", "remove": ["/required_changes"],
                      "set": {"/status": "PASS", "/cross_reviews": []}})
        self.assertIn("contains", [e[1] for e in validate("gate", gate)])


class H02_MandatoryTriggers(unittest.TestCase):
    REQUIRED = ["GOLDEN_CELL_EXIT", "CANONICAL_CREATIVE_ASSET", "MAJOR_BASELINE", "ART_DIRECTION_CHANGE",
                "MILESTONE_ACCEPTANCE", "RELEASE", "AUTHORITY_CHANGE"]

    def test_triggers_identical_everywhere(self):
        self.assertEqual(TRIGGERS, self.REQUIRED)
        self.assertEqual(SCHEMAS["task-routing"]["properties"]["review_triggers"]["items"]["anyOf"][0]["enum"], self.REQUIRED)
        rows = table_rows(h2_sections(read("core/QUALITY-GATES.md"))[1]["7. Review policy"])
        self.assertEqual([r[0].strip("`") for r in rows if r[0].strip("`") in TRIGGERS], self.REQUIRED)

    def test_triggers_force_human_review(self):
        r = build({"base": "examples/example-task-routing.json", "set": {"/review_triggers": ["RELEASE"]}})
        self.assertNotEqual(validate("task-routing", r), [])

    def test_workflow_triggers_encoded_in_schema_and_docs(self):
        wf_rules = {}
        for r in SCHEMAS["task-routing"]["allOf"]:
            wf = r.get("if", {}).get("properties", {}).get("workflow", {}).get("const")
            trig = r.get("then", {}).get("properties", {}).get("review_triggers")
            if wf and trig:
                wf_rules[wf] = sorted(c["contains"]["const"] for c in trig["allOf"])
        self.assertEqual(wf_rules, {k: sorted(v) for k, v in REGISTRY["workflow_mandatory_triggers"].items()})
        for wf, trig in REGISTRY["workflow_mandatory_triggers"].items():
            points = h2_sections(workflow_text(wf))[1]["HUMAN REVIEW POINTS"]
            for t in trig:
                self.assertIn(f"Mandatory trigger: `{t}`", points, wf)

    def test_golden_cell_exit_always_human(self):
        fx = json.loads((ROOT / "tests/fixtures/invalid/routing-golden-cell-without-human-review.json").read_text())
        self.assertNotEqual(validate("task-routing", build(fx)), [])
        ggc = read("core/GOLDEN-GAMEPLAY-CELL.md")
        self.assertIn("`GOLDEN_CELL_EXIT`", ggc)
        self.assertIn("No review policy, routing record or project override can replace it", ggc)

    def test_project_cannot_override_human_review_gate(self):
        overrides = SCHEMAS["project-config"]["properties"]["human_review"]["properties"]["review_policy_overrides"]
        self.assertNotIn("HUMAN_REVIEW", overrides["items"]["properties"]["gate"]["enum"])


class H03_ConditionalEvidence(unittest.TestCase):
    def test_conditions_are_used_and_documented(self):
        used = {c["condition"] for g in GATES.values() for c in g["conditional_evidence"]}
        self.assertEqual(used, set(CONDITIONS))
        qg = read("core/QUALITY-GATES.md")
        for c in CONDITIONS:
            self.assertIn(f"`{c}`", qg, c)

    def test_gameplay_design_is_not_motion_only(self):
        rules = GATES["GAMEPLAY_DESIGN"]
        self.assertNotIn("MOTION_EVIDENCE", rules["base_evidence"]["all_of"])
        self.assertIn("RUNTIME_EVIDENCE", rules["base_evidence"]["any_of"])
        conds = {c["condition"]: c["requires"] for c in rules["conditional_evidence"]}
        self.assertEqual(conds, {"REAL_TIME_BEHAVIOUR": ["MOTION_EVIDENCE"]})

    def test_named_examples(self):
        def cond(g, c):
            return next(x for x in GATES[g]["conditional_evidence"] if x["condition"] == c)
        self.assertEqual(GATES["UI_UX"]["base_evidence"]["all_of"], ["VISUAL_EVIDENCE"])
        self.assertIn("DEVICE_EVIDENCE", cond("UI_UX", "TOUCH_OR_MOBILE_TARGET")["requires"])
        self.assertIn("MOTION_EVIDENCE", cond("UI_UX", "ANIMATED_PRESENTATION")["requires"])
        self.assertEqual(GATES["VISUAL_ART"]["base_evidence"]["all_of"], ["VISUAL_EVIDENCE"])
        self.assertIn("TARGET_RUNTIME", cond("VISUAL_ART", "TARGET_PRESENTATION_DIFFERS")["requires_context"])
        self.assertEqual(GATES["CAMERA_COMPOSITION"]["base_evidence"]["all_of"], ["VISUAL_EVIDENCE"])

    def test_device_evidence_is_never_universal(self):
        for g, d in GATES.items():
            if g != "DEVICE":
                self.assertNotIn("DEVICE_EVIDENCE", d["base_evidence"]["all_of"] + d["base_evidence"]["any_of"], g)

    def test_schemas_encode_conditions_per_gate(self):
        for all_of in (SCHEMAS["gate"]["allOf"], SCHEMAS["task-routing"]["properties"]["required_gates"]["items"]["allOf"]):
            per_gate = find_rules(all_of, "applied_conditions")
            for g, d in GATES.items():
                expected = [c["condition"] for c in d["conditional_evidence"]]
                self.assertEqual(per_gate[g], {"items": {"enum": expected}} if expected else {"maxItems": 0}, g)

    def test_golden_cell_target_runtime_decision_enforced(self):
        self.assertEqual(sorted(REGISTRY["golden_cell_target_runtime_gates"]), ["CAMERA_COMPOSITION", "GAME_FEEL_VFX", "UI_UX"])
        fx = json.loads((ROOT / "tests/fixtures/invalid/routing-golden-cell-target-runtime-undecided.json").read_text())
        self.assertNotEqual(validate("task-routing", build(fx)), [])


class H04_CaptureContextsAndProvenance(unittest.TestCase):
    def test_contexts_identical_everywhere(self):
        expected = ["DCC_RENDER", "EDITOR", "TARGET_RUNTIME", "DIAGNOSTIC_RUNTIME", "PERFORMANCE_RUNTIME",
                    "OFFLINE_ANALYSIS", "AUTOMATED_TEST", "HUMAN_RECORD"]
        self.assertEqual(REGISTRY["capture_contexts"], expected)
        self.assertEqual(SCHEMAS["evidence"]["properties"]["provenance"]["properties"]["capture_context"]["enum"], expected)
        rows = table_rows(h2_sections(read("core/EVIDENCE-RULES.md"))[1]["3. Capture contexts"])
        self.assertEqual([r[0].strip("`") for r in rows if r[0].strip("`") in expected], expected)

    def test_provenance_fields(self):
        prov = SCHEMAS["evidence"]["properties"]["provenance"]
        for f in ["capture_context", "subject_revision", "build_revision", "build_id", "artifact_hash",
                  "target_platform", "tool_version", "instrumentation", "device"]:
            self.assertIn(f, prov["properties"], f)
        self.assertEqual(prov["required"], ["capture_context", "subject_revision"])
        self.assertIn("supersedes", SCHEMAS["evidence"]["properties"])
        self.assertEqual(prov["properties"]["instrumentation"]["properties"]["timing_impact"]["enum"],
                         REGISTRY["instrumentation_timing_impacts"])

    def test_gate_records_are_revision_bound(self):
        self.assertIn("revision", SCHEMAS["gate"]["properties"]["scope"]["required"])
        self.assertIn("evidence_carryover", SCHEMAS["gate"]["properties"])

    def test_staleness_rule_documented(self):
        doc = read("core/EVIDENCE-RULES.md")
        self.assertIn("evidence from an older or materially different runtime may not silently prove a newer revision", doc.lower())
        self.assertIn("**shown not to affect the claim**", doc)


class H05_RecordFixtures(unittest.TestCase):
    """Static cross-record fixtures: staleness, carryover, supersession, contexts, instrumentation, conditions."""

    def test_record_fixtures(self):
        files = sorted((ROOT / "tests" / "fixtures" / "records").glob("*.json"))
        self.assertGreaterEqual(len(files), 15)
        for f in files:
            fx = json.loads(f.read_text())
            gate = build(fx["gate"])
            evidence = [build(e) for e in fx["evidence"]]
            if fx.get("schema_invalid_gate"):
                self.assertNotEqual(validate("gate", gate), [], f"{f.name}: schema must reject the gate record")
            else:
                self.assertEqual(validate("gate", gate), [], f"{f.name}: gate record must be schema-valid")
            for e in evidence:
                if fx.get("schema_invalid_evidence"):
                    # Defence in depth: the schema rejects it, and so must the cross-record model.
                    self.assertNotEqual(validate("evidence", e), [], f"{f.name}: schema must reject {e['evidence_id']}")
                else:
                    self.assertEqual(validate("evidence", e), [], f"{f.name}: evidence {e['evidence_id']} must be schema-valid")
            by_id, dup = index_unique(evidence, "evidence_id", "evidence_id")
            self.assertEqual(dup, [], f.name)
            problems = gate_evidence_problems(gate, by_id)
            if fx["expect_problem"] is None:
                self.assertEqual(problems, [], f"{f.name}: {fx['description']}")
            else:
                self.assertTrue(any(fx["expect_problem"] in p for p in problems), f"{f.name}: {problems}")


class H06_VisualResponsibility(unittest.TestCase):
    def test_defaults_in_skill_contracts(self):
        for skill, items in REGISTRY["visual_responsibility_defaults"].items():
            where = skill_section(skill, "CROSS-REVIEW" if skill == "camera-composition" else "OWNS").lower()
            for item in items:
                self.assertIn(item.replace(" (cross-review)", ""), where, f"{skill}: {item}")

    def test_no_cross_claims(self):
        owns = {s: skill_section(s, "OWNS").lower() for s in REGISTRY["skills"]}
        exclusive = {"lighting intent": "art-direction", "environment lighting composition": "environment-art",
                     "scene-light placement": "environment-art", "lighting and shader implementation": "technical-art"}
        for phrase, owner in exclusive.items():
            for s, text in owns.items():
                if s != owner:
                    self.assertNotIn(phrase, text, f"{s} claims {phrase}")

    def test_routing_documents_and_enforces_primary_visual_owner(self):
        section = h2_sections(read("core/ROLE-ROUTING.md"))[1]["5. Default visual responsibility"]
        self.assertEqual({r[0].strip("`") for r in table_rows(section)}, set(REGISTRY["visual_responsibility_defaults"]))
        self.assertIn("primary visual owner", section)
        self.assertIn("primary visual owner", skill_section("game-director", "OWNS").lower())
        rules = SCHEMAS["task-routing"]["properties"]["required_gates"]["items"]["allOf"]
        self.assertTrue(any(r["if"]["properties"].get("gate", {}).get("const") == "VISUAL_ART"
                            and r["then"].get("required") == ["owner"] for r in rules))


class H07_Governance(unittest.TestCase):
    SECTIONS = ["1. Principle", "2. Change proposals", "3. Registry changes", "4. Lesson intake",
                "5. Review requirements", "6. Semantic versioning", "7. Backward compatibility and deprecation",
                "8. Breaking changes", "9. Skill maturity promotion", "10. Releases and tagging",
                "11. Project migration", "12. Phase-2 acceptance requirement: record validation"]

    def test_governance_sections(self):
        order, sections = h2_sections(read("core/GOVERNANCE.md"))
        self.assertEqual(order, self.SECTIONS)
        principle = sections["1. Principle"]
        self.assertIn("**A game project does not modify GPOS automatically during production.**", principle)
        self.assertIn("project postmortem → framework-general lesson → GPOS change proposal → review → validation → versioned GPOS release", principle)


class H08_Phase2Boundary(unittest.TestCase):
    def test_record_validation_requirement_documented(self):
        section = h2_sections(read("core/GOVERNANCE.md"))[1]["12. Phase-2 acceptance requirement: record validation"]
        self.assertIn("not implemented in Phase 1", section)
        self.assertGreaterEqual(len(re.findall(r"^\d+\. ", section, re.M)), 7)
        for phrase in ["claimed gate", "subject revision", "linked Human Review", "verdict is valid",
                       "not the gate owner", "superseded evidence", "registry and schemas"]:
            self.assertIn(phrase, section, phrase)
        self.assertIn("GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation", read("tools/README.md"))
        # Phase 2A: the README now states that the production validator implements §12 (was: "not implemented").
        self.assertIn("Project record validation is implemented by the Phase-2A production validator", read("README.md"))


class H09_HumanEvidenceTrustBoundary(unittest.TestCase):
    def test_trust_boundary_documented(self):
        section = h2_sections(read("core/HUMAN-AUTHORITY.md"))[1]["7. Authenticity of human evidence (trust boundary)"]
        self.assertIn("Agents may transcribe a human verdict. They may never invent one.", section)
        self.assertIn("cannot technically verify", section)
        self.assertIn("does not define identity or cryptographic infrastructure", section)


# ---------------------------------------------------------------- alpha.3 corrections (A01–A11)

class A01_GameEngineering(unittest.TestCase):
    def test_contract(self):
        self.assertIn("game-engineering", REGISTRY["skills"])
        self.assertEqual(may_own("game-engineering"), [])
        owns, not_own = owns_and_not("game-engineering")
        for term in ["gameplay implementation", "runtime architecture", "gameplay and system code", "state machines",
                     "engine-side system integration", "input plumbing", "runtime data flow", "persistence implementation",
                     "technical design", "integration apis", "runtime refactoring"]:
            self.assertIn(term, owns, term)
        for term in ["gameplay rules", "art direction", "animation quality", "level design", "ui/ux design",
                     "subjective acceptance", "qa acceptance"]:
            self.assertIn(term, not_own, term)

    def test_implementation_and_verification_separated(self):
        self.assertEqual(GATES["TECHNICAL"]["default_owner"], "qa-performance")
        for g, d in GATES.items():
            self.assertNotIn("game-engineering", d["permitted_owners"], g)
        fx = json.loads((ROOT / "tests/fixtures/invalid/routing-game-engineering-owns-technical.json").read_text())
        self.assertNotEqual(validate("task-routing", build(fx)), [])

    def test_gameplay_implementation_has_registered_owner(self):
        self.assertIn("(`game-engineering`)", skill_section("gameplay-design", "DOES NOT OWN"))
        steps = h2_sections(workflow_text("gameplay-feature"))[1]["STEPS"]
        self.assertIn("hands the decided specification to `game-engineering`", steps)
        self.assertNotIn("routed implementer", workflow_text("gameplay-feature"))
        table = h2_sections(read("core/ROLE-ROUTING.md"))[1]["3. Routing table"]
        for pattern in ["Gameplay system implementation", "Save / runtime architecture", "Runtime refactor", "Engine or platform integration"]:
            row = next(r for r in table.splitlines() if r.startswith(f"| {pattern}"))
            self.assertIn("| `game-engineering` |", row, pattern)

    def test_runtime_system_workflow_routes_code(self):
        self.assertIn("runtime-system", REGISTRY["workflows"])
        routing = build(json.loads((ROOT / "tests/fixtures/valid/routing-runtime-system.json").read_text()))
        self.assertEqual(validate("task-routing", routing), [])
        self.assertEqual(routing["primary_specialist"], "game-engineering")
        self.assertEqual(routing_problems(routing), [])
        spec = h2_sections(workflow_text("runtime-system"))[1]
        self.assertIn("Primary: `game-engineering`", spec["SPECIALISTS"])
        self.assertIn("The implementer recording `TECHNICAL` on its own work.", spec["FORBIDDEN SHORTCUTS"])

    def test_engineering_authority_template(self):
        self.assertIn("ENGINEERING.md", REGISTRY["project_authority_files"])
        self.assertIn("`ENGINEERING.md`", read("core/AUTHORITY-HIERARCHY.md"))


class A02_EvidenceContextCompatibility(unittest.TestCase):
    def test_registry_covers_every_type(self):
        compat = REGISTRY["evidence_context_compatibility"]
        self.assertEqual(list(compat), REGISTRY["evidence_types"])
        for t, ctxs in compat.items():
            self.assertTrue(ctxs and set(ctxs) <= set(REGISTRY["capture_contexts"]), t)
            if t != "HUMAN_EVIDENCE":
                self.assertNotIn("HUMAN_RECORD", ctxs, t)
        self.assertEqual(compat["HUMAN_EVIDENCE"], ["HUMAN_RECORD"])
        self.assertEqual(sorted(compat["DEVICE_EVIDENCE"]), ["PERFORMANCE_RUNTIME", "TARGET_RUNTIME"])
        for t in ["RUNTIME_EVIDENCE", "PERSISTENCE_EVIDENCE", "TEST_EVIDENCE"]:
            self.assertNotIn("DCC_RENDER", compat[t], t)
        for t in ["RUNTIME_EVIDENCE", "PERSISTENCE_EVIDENCE", "AUDIO_EVIDENCE"]:
            self.assertNotIn("OFFLINE_ANALYSIS", compat[t], t)
        self.assertIn("AUTOMATED_TEST", compat["MOTION_EVIDENCE"])  # automated-harness video stays legitimate

    def test_schema_encodes_compatibility(self):
        got = {}
        for r in SCHEMAS["evidence"]["allOf"]:
            t = r["if"]["properties"].get("type", {}).get("const")
            ctx = r["then"].get("properties", {}).get("provenance", {}).get("properties", {}).get("capture_context")
            if t and ctx:
                got[t] = ctx["enum"]
        self.assertEqual(got, REGISTRY["evidence_context_compatibility"])

    def test_documented(self):
        rows = table_rows(h2_sections(read("core/EVIDENCE-RULES.md"))[1]["3. Capture contexts"])
        doc = {r[0].strip("`"): set(backticked(r[1])) for r in rows if r[0].strip("`") in REGISTRY["evidence_types"]}
        self.assertEqual(doc, {t: set(c) for t, c in REGISTRY["evidence_context_compatibility"].items()})

    def test_dcc_render_cannot_masquerade_as_runtime_for_technical(self):
        ev = build({"base": "examples/example-evidence-record.json",
                    "set": {"/type": "RUNTIME_EVIDENCE", "/provenance": {"capture_context": "DCC_RENDER",
                            "subject_revision": "rev-example-0002", "tool_version": "dcc x"}}})
        self.assertIn("enum", [e[1] for e in validate("evidence", ev)])
        gate = build({"base": "examples/example-gate-record.json", "remove": ["/required_changes", "/cross_reviews"],
                      "set": {"/gate": "TECHNICAL", "/owner": "qa-performance", "/assessed_by": {"kind": "AGENT", "id": "qa-performance"},
                              "/status": "PASS", "/review_policy": "ROUTINE", "/routine_basis": "x", "/specialist_assessment": "PASS"}})
        self.assertEqual(validate("gate", gate), [])
        self.assertTrue(any("incompatible" in p for p in gate_evidence_problems(gate, {ev["evidence_id"]: ev})))

    def test_adapters_do_not_claim_blender_runtime_evidence(self):
        rows = [l for l in read("adapters/README.md").splitlines() if l.startswith("| Blender")]
        self.assertEqual(len(rows), 2)
        for line in rows:
            evidence_cell = line.strip("|").split("|")[2]
            self.assertIn("never game `RUNTIME_EVIDENCE`", evidence_cell.lower().replace("never game `runtime_evidence`", "never game `RUNTIME_EVIDENCE`"), line)
            self.assertEqual(evidence_cell.count("`RUNTIME_EVIDENCE`"), 1, line)


class A03_DecisionModel(unittest.TestCase):
    def test_schema_and_example(self):
        d = SCHEMAS["decision"]["properties"]
        self.assertEqual(d["kind"]["enum"], REGISTRY["decision_kinds"])
        self.assertEqual(d["subject"]["properties"]["kind"]["enum"], REGISTRY["gate_scope_kinds"])
        self.assertEqual(d["decided_by"]["properties"]["kind"]["const"], "HUMAN")
        for f in ["decision_id", "subject", "kind", "decided_by", "decided_at", "decision", "affects", "status"]:
            self.assertIn(f, SCHEMAS["decision"]["required"], f)
        self.assertIn("supersedes", d)
        self.assertEqual(validate("decision", load_json("examples/example-decision-record.json")), [])

    def test_decision_ref_fields_are_patterned_in_schemas(self):
        for key in REGISTRY["decision_ref_fields"]:
            schema_name, pointer = key.split(":", 1)
            node = SCHEMAS[schema_name]
            for part in pointer.lstrip("/").split("/"):
                node = node["items"] if part == "*" else node["properties"][part]
            self.assertEqual(node.get("pattern"), "^D-[A-Za-z0-9._-]+$", key)

    def test_authoritative_representation_documented(self):
        section = h2_sections(read("core/AUTHORITY-HIERARCHY.md"))[1]["6. Human Decision records"]
        self.assertIn("canonical record", section)
        self.assertIn("human-readable view", section)
        self.assertIn("A reference that merely looks like a decision id is not authority.", section)


class A04_AuthorityFixtures(unittest.TestCase):
    """Referential integrity, lifecycle, authorization and presentation parity (planned Phase-2 model)."""

    def test_authority_fixtures(self):
        files = sorted((ROOT / "tests/fixtures/authority").glob("*.json"))
        self.assertGreaterEqual(len(files), 15)
        for f in files:
            fx = json.loads(f.read_text())
            config = build(fx["config"])
            decisions = [build(d) for d in fx["decisions"]]
            evidence = [build(e) for e in fx.get("evidence", [])]
            gates = [build(g) for g in fx.get("gates", [])]
            routings = [build(json.loads((ROOT / r).read_text())) for r in
                        ([fx["routing"]] if "routing" in fx else fx.get("routings", []))]
            self.assertEqual(validate("project-config", config), [], f"{f.name}: config must be schema-valid")
            for kind, docs in (("decision", decisions), ("evidence", evidence), ("gate", gates), ("task-routing", routings)):
                for doc in docs:
                    self.assertEqual(validate(kind, doc), [], f"{f.name}: {kind} record must be schema-valid")
            problems = record_set_problems(config, decisions, evidence, gates, routings)
            if fx["expect_problem"] is None:
                self.assertEqual(problems, [], f"{f.name}: {fx['description']}")
            else:
                self.assertTrue(any(fx["expect_problem"] in p for p in problems), f"{f.name}: {problems}")

    def test_fake_decision_ref_is_schema_valid_but_not_authority(self):
        config = build({"base": "examples/minimal-project-config.json",
                        "set": {"/lifecycle_stage": "PRE_PRODUCTION", "/lifecycle_decision_ref": "D-9999"}})
        self.assertEqual(validate("project-config", config), [])
        self.assertTrue(record_set_problems(config, [load_json("examples/example-decision-record.json")]))


class A05_LifecycleAuthority(unittest.TestCase):
    def test_every_stage_after_concept_needs_decision(self):
        base = load_json("examples/minimal-project-config.json")
        for stage in REGISTRY["lifecycle_stages"][1:]:
            doc = dict(base, lifecycle_stage=stage,
                       golden_gameplay_cell={"required": True, "status": "EXITED", "exit_decision_ref": "D-0012"})
            self.assertIn("required", [e[1] for e in validate("project-config", doc)], stage)
            self.assertEqual(validate("project-config", dict(doc, lifecycle_decision_ref="D-0001")), [], stage)

    def test_transitions_coherent(self):
        stages = REGISTRY["lifecycle_stages"]
        lt = REGISTRY["lifecycle_transitions"]
        for frm, to in lt["forward"]:
            self.assertLess(stages.index(frm), stages.index(to))
        self.assertEqual({to for _, to in lt["forward"]}, set(stages[1:]))
        self.assertEqual(lt["requires_golden_cell_waiver"], [["PRE_PRODUCTION", "PRODUCTION"]])
        self.assertIn("lifecycle_decision_ref", read("core/PRODUCTION-LIFECYCLE.md"))


class A06_ReviewSemantics(unittest.TestCase):
    def test_game_director_not_human_review_for_every_subjective_gate(self):
        crit = skill_section("game-director", "PASS CRITERIA")
        self.assertNotIn("every blocking subjective gate", crit)
        self.assertIn("every `HUMAN_REVIEW_REQUIRED` gate and every mandatory Human Review trigger", crit)
        for p in all_markdown():
            if p.name != "CHANGELOG.md":
                self.assertNotIn("Human Review is planned for every blocking subjective gate", p.read_text(), p.name)

    def test_quality_gates_semantics(self):
        section = h2_sections(read("core/QUALITY-GATES.md"))[1]["7. Review policy"]
        self.assertIn("**no unresolved negative cross-review**", section)
        self.assertIn("Specialist cross-review is required only when routing or the workflow says so", section)
        self.assertIn("may never be silently discarded", section)
        self.assertIn("never assess a gate", section)
        self.assertIn("never silently discarded", read("core/HUMAN-AUTHORITY.md"))

    def test_schema_semantics(self):
        base = "examples/example-gate-record.json"

        def v(set_):
            return validate("gate", build({"base": base, "set": set_, "remove": ["/required_changes"]}))
        ok_cr = [{"reviewer": "camera-composition", "assessment": "PASS", "reviewed_revision": "rev-example-0002"}]
        self.assertEqual(v({"/status": "PASS", "/specialist_assessment": "PASS", "/cross_reviews": ok_cr}), [])
        self.assertNotEqual(v({"/status": "PASS", "/specialist_assessment": "FAIL", "/cross_reviews": ok_cr}), [])
        self.assertNotEqual(v({"/status": "PASS", "/specialist_assessment": "PASS",
                               "/cross_reviews": ok_cr + [{"reviewer": "game-feel-vfx", "assessment": "FAIL", "reviewed_revision": "rev-example-0002"}]}), [])
        hrr = {"/status": "PASS", "/review_policy": "HUMAN_REVIEW_REQUIRED", "/human_review_ref": "GATE-0002",
               "/specialist_assessment": "PASS", "/cross_reviews": []}
        self.assertEqual(v(hrr), [])
        self.assertNotEqual(v(dict(hrr, **{"/cross_review_required": True})), [])
        self.assertNotEqual(v(dict(hrr, **{"/specialist_assessment": "FAIL"})), [])
        self.assertEqual(v(dict(hrr, **{"/specialist_assessment": "FAIL", "/disagreements_disclosed": True})), [])

    def test_routing_needs_reviewer_when_cross_review_required(self):
        r = load_json("examples/example-task-routing.json")
        r["reviewers"] = []
        self.assertTrue(routing_problems(r))


class A07_AccountableAssessor(unittest.TestCase):
    def test_assessor_vocabulary(self):
        self.assertEqual(REGISTRY["assessor_kinds"], ["AGENT", "HUMAN"])
        self.assertEqual(SCHEMAS["gate"]["$defs"]["assessor"]["properties"]["kind"]["enum"], REGISTRY["assessor_kinds"])

    def test_per_owner_rules_cover_every_owner(self):
        owners = set()
        for r in SCHEMAS["gate"]["allOf"]:
            o = r.get("if", {}).get("properties", {}).get("owner", {}).get("const")
            if not o:
                continue
            owners.add(o)
            props = r["then"]["properties"]
            if o != "HUMAN":
                self.assertIn({"properties": {"kind": {"const": "AGENT"}, "id": {"const": o}}}, props["assessed_by"]["anyOf"], o)
                self.assertEqual(props["cross_reviews"]["items"]["properties"]["reviewer"], {"not": {"const": o}}, o)
        self.assertEqual(owners, set(GATE_OWNERS))

    def test_impersonation_rejected(self):
        g = build({"base": "examples/example-gate-record.json", "set": {"/assessed_by": {"kind": "AGENT", "id": "qa-performance"}}})
        self.assertNotEqual(validate("gate", g), [])
        self.assertTrue(gate_evidence_problems(g, {}))


class A08_Authorization(unittest.TestCase):
    def test_modelled(self):
        cfg = SCHEMAS["project-config"]
        self.assertIn("decision_authorities", cfg["required"])
        kinds = cfg["properties"]["decision_authorities"]["items"]["properties"]["may_decide"]["items"]["enum"]
        self.assertEqual(kinds, ["ALL"] + REGISTRY["decision_kinds"])
        self.assertIn("gates", cfg["properties"]["human_review"]["properties"]["reviewers"]["items"]["properties"])
        self.assertIn("### Authorization (modelled now)", read("core/HUMAN-AUTHORITY.md"))
        section = h2_sections(read("core/GOVERNANCE.md"))[1]["12. Phase-2 acceptance requirement: record validation"]
        self.assertIn("listed decision authority", section)
        self.assertIn("listed Human Review participant", section)


class A09_ScopeKinds(unittest.TestCase):
    def test_scopes_coherent(self):
        kinds = REGISTRY["gate_scope_kinds"]
        for k in ["MILESTONE", "PROJECT", "DECISION"]:
            self.assertIn(k, kinds)
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertEqual(set(REGISTRY["trigger_scope_kinds"]), set(TRIGGERS))
        for trig, ks in REGISTRY["trigger_scope_kinds"].items():
            self.assertTrue(ks and set(ks) <= set(kinds), trig)
        self.assertEqual(SCHEMAS["decision"]["properties"]["subject"]["properties"]["kind"]["enum"], kinds)
        self.assertEqual(SCHEMAS["gate"]["properties"]["scope"]["properties"]["kind"]["enum"], kinds)
        rule = h2_sections(read("core/QUALITY-GATES.md"))[1]["3. Independence rules"]
        for k in kinds:
            self.assertIn(f"`{k}`", rule, k)


class A10_PresentationParity(unittest.TestCase):
    def test_decided_value_needs_decision(self):
        base = "examples/minimal-project-config.json"
        for val in ("YES", "NO"):
            doc = build({"base": base, "set": {"/presentation": {"target_presentation_differs_from_editor": val}}})
            self.assertIn("required", [e[1] for e in validate("project-config", doc)], val)
        doc = build({"base": base, "set": {"/presentation": {"target_presentation_differs_from_editor": "UNDECIDED"}}})
        self.assertEqual(validate("project-config", doc), [])
        self.assertIn("presentation parity", h2_sections(read("core/GOVERNANCE.md"))[1]["12. Phase-2 acceptance requirement: record validation"].lower())


class A11_FutureConsiderations(unittest.TestCase):
    def test_documented_not_implemented(self):
        readme = read("README.md")
        self.assertIn("## Future considerations (not Phase 1)", readme)
        for phrase in ["Golden Gameplay Cell set", "licensing provenance", "narrative, localization, accessibility, networking"]:
            self.assertIn(phrase, readme)
        self.assertEqual(len(REGISTRY["skills"]), 13)


# ---------------------------------------------------------------- alpha.4 hardening (B01–B09)

def _fixture(rel):
    return json.loads((ROOT / rel).read_text())


def _record_set(rel):
    fx = _fixture(rel)
    routings = [build(_fixture(r)) for r in ([fx["routing"]] if "routing" in fx else fx.get("routings", []))]
    return record_set_problems(build(fx["config"]), [build(d) for d in fx["decisions"]],
                               [build(e) for e in fx.get("evidence", [])], [build(g) for g in fx.get("gates", [])], routings)


class B01_DecisionValueBinding(unittest.TestCase):
    CASES = {
        "review-policy-override-opposite": "does not match",
        "review-policy-override-other-gate": "does not match",
        "review-policy-override-scope-mismatch": "does not match",
        "quality-target-opposite": "does not match",
        "editor-concurrency-opposite": "does not match",
    }

    def test_right_kind_wrong_value_does_not_authorize(self):
        for name, expected in self.CASES.items():
            problems = _record_set(f"tests/fixtures/authority/{name}.json")
            self.assertTrue(any(expected in p for p in problems), f"{name}: {problems}")

    def test_matching_values_authorize(self):
        for name in ["review-policy-override-ok", "quality-target-ok", "editor-concurrency-ok", "presentation-ok"]:
            self.assertEqual(_record_set(f"tests/fixtures/authority/{name}.json"), [], name)

    def test_payload_schema_and_bindings_cover_kinds(self):
        self.assertEqual(set(REGISTRY["decision_payloads"]),
                         {"LIFECYCLE_TRANSITION", "PRESENTATION_PARITY", "QUALITY_TARGET", "REVIEW_POLICY_OVERRIDE",
                          "EDITOR_CONCURRENCY", "GPOS_UPGRADE", "BLOCKING_DOWNGRADE", "KNOWN_ISSUE_ACCEPTANCE"})
        rules = {}
        for r in SCHEMAS["decision"]["allOf"]:
            k = r["if"]["properties"].get("kind", {})
            for kind in ([k["const"]] if "const" in k else k.get("enum", [])):
                rules[kind] = r["then"]
        for kind in REGISTRY["decision_payloads"]:
            self.assertIn(kind, rules, kind)
        self.assertEqual(rules["QUALITY_TARGET"]["properties"]["value"]["enum"], REGISTRY["quality_targets"])
        self.assertEqual(SCHEMAS["project-config"]["properties"]["quality_target"]["enum"], REGISTRY["quality_targets"])
        bound = {b["ref"] for b in REGISTRY["decision_value_bindings"]}
        for key, kinds in REGISTRY["decision_ref_fields"].items():
            if set(kinds) & set(REGISTRY["decision_payloads"]) and key.startswith("project-config:"):
                self.assertIn(key, bound, key)
        doc = read("core/AUTHORITY-HIERARCHY.md")
        self.assertIn("a decision of the right kind but a different value does not authorize", doc)

    def test_decision_schema_rejects_missing_payload(self):
        d = build({"base": "examples/example-decision-record.json", "remove": ["/transition"],
                   "set": {"/kind": "QUALITY_TARGET"}})
        self.assertIn("required", [e[1] for e in validate("decision", d)])


class B02_GposUpgradeAuthority(unittest.TestCase):
    def test_fresh_project_needs_no_upgrade_decision(self):
        self.assertNotIn("gpos_upgrade", load_json("examples/minimal-project-config.json"))
        self.assertEqual(_record_set("tests/fixtures/authority/ok-stage-backed-by-decision.json"), [])

    def test_governed_upgrade_requires_decision(self):
        problems = _record_set("tests/fixtures/authority/gpos-upgrade-without-decision.json")
        self.assertTrue(any("requires a GPOS_UPGRADE decision" in p for p in problems), problems)

    def test_from_to_mismatch_fails(self):
        for name in ["gpos-upgrade-from-mismatch", "gpos-upgrade-to-mismatch"]:
            problems = _record_set(f"tests/fixtures/authority/{name}.json")
            self.assertTrue(any("does not match" in p for p in problems), f"{name}: {problems}")

    def test_matching_upgrade_and_ungoverned_patch(self):
        self.assertEqual(_record_set("tests/fixtures/authority/gpos-upgrade-ok.json"), [])
        self.assertEqual(_record_set("tests/fixtures/authority/gpos-patch-upgrade-ungoverned.json"), [])
        self.assertTrue(gpos_boundary_is_governed("1.0.0-alpha.3", "1.0.0-alpha.7"))
        self.assertTrue(gpos_boundary_is_governed("1.2.0", "2.0.0"))
        self.assertFalse(gpos_boundary_is_governed("1.0.0", "1.0.1"))
        self.assertIn("gpos_upgrade", read("core/GOVERNANCE.md"))


class B03_CarryoverAuthority(unittest.TestCase):
    def test_tool_ci_device_cannot_approve(self):
        for kind in ("TOOL", "CI", "DEVICE"):
            gate = build({"base": "examples/example-gate-record.json", "set": {
                "/scope/revision": "rev-example-0003",
                "/evidence_carryover": [{"evidence_ref": "EV-0001", "evidence_revision": "rev-example-0002",
                                         "justification": "bot says fine", "assessed_by": {"kind": kind, "id": "carryover-bot"}}]}})
            self.assertIn("enum", [e[1] for e in validate("gate", gate)], kind)
            ev = load_json("examples/example-evidence-record.json")
            problems = gate_evidence_problems(gate, {ev["evidence_id"]: ev})
            self.assertTrue(any("not accountable" in p for p in problems), f"{kind}: {problems}")
            self.assertTrue(any("stale" in p for p in problems), f"{kind}: stale evidence must not count")

    def test_other_agent_cannot_approve_but_proposal_is_allowed(self):
        base = {"/scope/revision": "rev-example-0003", "/evidence_carryover": [{
            "evidence_ref": "EV-0001", "evidence_revision": "rev-example-0002", "justification": "x",
            "proposed_by": {"kind": "TOOL", "id": "diff-analyzer"}, "assessed_by": {"kind": "AGENT", "id": "qa-performance"}}]}
        self.assertIn("anyOf", [e[1] for e in validate("gate", build({"base": "examples/example-gate-record.json", "set": base}))])
        base["/evidence_carryover"][0]["assessed_by"] = {"kind": "AGENT", "id": "character-animation"}
        self.assertEqual(validate("gate", build({"base": "examples/example-gate-record.json", "set": base})), [])


class B04_HumanReviewRouting(unittest.TestCase):
    def test_human_review_required_without_cross_review_needs_no_specialist(self):
        routing = build(_fixture("tests/fixtures/valid/routing-camera-human-review-only.json"))
        self.assertEqual(validate("task-routing", routing), [])
        self.assertEqual(routing["reviewers"], ["HUMAN"])
        self.assertEqual(routing_problems(routing), [])

    def test_cross_review_flag_brings_back_specialist(self):
        routing = build(_fixture("tests/fixtures/valid/routing-camera-human-review-only.json"))
        routing["required_gates"][0]["cross_review_required"] = True
        self.assertTrue(any("cross-reviewer" in p for p in routing_problems(routing)))

    def test_human_review_required_needs_human_reviewer(self):
        routing = build(_fixture("tests/fixtures/valid/routing-camera-human-review-only.json"))
        routing["reviewers"] = ["level-design"]
        self.assertTrue(any("HUMAN is not a reviewer" in p for p in routing_problems(routing)))

    def test_cross_review_required_still_needs_specialist(self):
        routing = build(_fixture("tests/fixtures/valid/routing-camera-human-review-only.json"))
        routing["required_gates"][0]["review_policy"] = "CROSS_REVIEW_REQUIRED"
        self.assertTrue(routing_problems(routing))


class B05_RecordIdUniqueness(unittest.TestCase):
    CASES = ["duplicate-decision-ids", "duplicate-evidence-ids", "duplicate-gate-ids",
             "duplicate-decision-authority-ids", "duplicate-reviewer-ids", "duplicate-routing-ids"]

    def test_duplicates_rejected(self):
        for name in self.CASES:
            problems = _record_set(f"tests/fixtures/authority/{name}.json")
            self.assertTrue(any(p.startswith("duplicate") for p in problems), f"{name}: {problems}")

    def test_index_unique_never_last_write_wins(self):
        idx, problems = index_unique([{"id": "a", "v": 1}, {"id": "a", "v": 2}], "id", "id")
        self.assertIsNone(idx)
        self.assertEqual(problems, ["duplicate id 'a'"])

    def test_registry_and_docs(self):
        self.assertEqual(set(REGISTRY["record_id_uniqueness"]),
                         {"decision records", "evidence records", "gate records", "routing records",
                          "project-config:/decision_authorities", "project-config:/human_review/reviewers",
                          "project-config:/human_review/additional_mandatory_triggers"})
        self.assertIn("never builds a lookup before duplicate", read("core/GOVERNANCE.md"))


class B06_ReviewerGateAuthorization(unittest.TestCase):
    def test_animation_reviewer_cannot_approve_ui_ux(self):
        problems = _record_set("tests/fixtures/authority/reviewer-animation-only-approves-ui-ux.json")
        self.assertTrue(any("not authorized to review UI_UX" in p for p in problems), problems)

    def test_animation_reviewer_can_approve_animation(self):
        self.assertEqual(_record_set("tests/fixtures/authority/reviewer-animation-only-approves-animation.json"), [])

    def test_helper_semantics(self):
        cfg = build({"base": "examples/minimal-project-config.json", "set": {
            "/human_review/reviewers": [{"id": "reviewer-a", "gates": ["ANIMATION"]}],
            "/decision_authorities": [{"id": "creative-lead", "may_decide": ["ALL"]},
                                      {"id": "budget-owner", "may_decide": ["BUDGET"]}]}})
        self.assertTrue(human_may_review(cfg, "reviewer-a", "ANIMATION"))
        self.assertFalse(human_may_review(cfg, "reviewer-a", "UI_UX"))
        self.assertTrue(human_may_review(cfg, "creative-lead", "UI_UX"))  # ALL authority, per GOVERNANCE
        self.assertFalse(human_may_review(cfg, "budget-owner", "UI_UX"))
        self.assertIn("A decision authority holding `ALL`", read("core/HUMAN-AUTHORITY.md"))


class B07_Timestamps(unittest.TestCase):
    def test_invalid_date_times_rejected(self):
        for rel, schema in [("tests/fixtures/invalid/gate-recorded-at-garbage.json", "gate"),
                            ("tests/fixtures/invalid/evidence-created-at-impossible-date.json", "evidence"),
                            ("tests/fixtures/invalid/decision-decided-at-no-offset.json", "decision")]:
            self.assertIn("format", [e[1] for e in validate(schema, build(_fixture(rel)))], rel)

    def test_every_date_time_field_is_asserted(self):
        def walk(node, found):
            if isinstance(node, dict):
                if node.get("format") == "date-time":
                    found.append(node)
                for v in node.values():
                    walk(v, found)
            elif isinstance(node, list):
                for v in node:
                    walk(v, found)
            return found
        total = sum(len(walk(s_, [])) for s_ in SCHEMAS.values())
        self.assertEqual(total, 3)  # gate.recorded_at, evidence.created_at, decision.decided_at
        self.assertIn("date-time", __import__("schema_lite").SUPPORTED_FORMATS)

    def test_rfc3339_boundaries(self):
        from schema_lite import is_rfc3339_datetime as ok
        for v in ["2026-01-01T00:00:00Z", "2026-12-31T23:59:59.999+14:00", "2024-02-29T12:00:00-05:30"]:
            self.assertTrue(ok(v), v)
        for v in ["garbage", "2026-02-30T00:00:00Z", "2025-02-29T00:00:00Z", "2026-01-01T24:00:00Z", "2026-01-01T12:60:00Z",
                  "2026-01-01T12:00:60Z", "2026-01-01T12:00:00+24:00", "2026-01-01T12:00:00", "2026-01-01 12:00:00Z"]:
            self.assertFalse(ok(v), v)

    def test_identifiers_are_unambiguous(self):
        d = build({"base": "examples/example-decision-record.json", "set": {"/decided_by": {"kind": "HUMAN", "id": "creative lead"}}})
        self.assertIn("pattern", [e[1] for e in validate("decision", d)])
        c = build({"base": "examples/minimal-project-config.json", "set": {"/decision_authorities": [{"id": "", "may_decide": ["ALL"]}]}})
        self.assertIn("pattern", [e[1] for e in validate("project-config", c)])


class B08_Phase2ContractExpanded(unittest.TestCase):
    def test_new_requirements_listed(self):
        section = h2_sections(read("core/GOVERNANCE.md"))[1]["12. Phase-2 acceptance requirement: record validation"]
        for phrase in ["payload", "from_version", "duplicate", "last-write-wins", "carryover", "gate permissions",
                       "timestamps"]:
            self.assertIn(phrase, section, phrase)


class B09_BoundaryAndMaturity(unittest.TestCase):
    def test_all_thirteen_draft_and_no_phase2(self):
        self.assertEqual(len(REGISTRY["skills"]), 13)
        for s_ in REGISTRY["skills"]:
            self.assertEqual(front_matter(skill_text(s_))["maturity"], "DRAFT", s_)
        self.assertEqual(phase_boundary_problems(), [])


# ---------------------------------------------------------------- alpha.5 binding and readiness (C01–C14)

def _rec(rel):
    fx = _fixture(rel)
    evidence = [build(e) for e in fx["evidence"]]
    return gate_evidence_problems(build(fx["gate"]), index_unique(evidence, "evidence_id", "evidence_id")[0])


def _has(problems, text):
    return any(text in p for p in problems)


def _linked_gate(gate_name, owner, status="PASS", **extra):
    g = build({"base": "examples/example-gate-record.json", "remove": ["/required_changes"],
               "set": {"/gate_id": f"G-{gate_name}", "/gate": gate_name, "/owner": owner, "/status": status,
                       "/assessed_by": {"kind": "AGENT", "id": owner}, "/specialist_assessment": "PASS"}})
    g.update(extra)
    return g


class C01_EvidenceSubject(unittest.TestCase):
    def test_other_task_evidence_cannot_prove_gate(self):
        self.assertTrue(_has(_rec("tests/fixtures/records/evidence-other-task.json"), "not the gate subject"))
        self.assertTrue(_has(_rec("tests/fixtures/records/evidence-build-capture-without-mapping.json"), "not the gate subject"))

    def test_explicit_applicability_works(self):
        self.assertEqual(_rec("tests/fixtures/records/evidence-applicability-justified.json"), [])
        self.assertTrue(_has(_rec("tests/fixtures/records/evidence-applicability-wrong-subject.json"), "not the gate subject"))
        self.assertTrue(_has(_rec("tests/fixtures/records/evidence-applicability-by-tool.json"), "not accountable"))

    def test_schema_requires_subject_kind(self):
        self.assertEqual(SCHEMAS["evidence"]["properties"]["subject"]["required"], ["kind", "ref"])
        self.assertEqual(SCHEMAS["evidence"]["properties"]["subject"]["properties"]["kind"]["enum"], REGISTRY["gate_scope_kinds"])


class C02_DecisionRefsAllRecordTypes(unittest.TestCase):
    def test_fake_gate_and_routing_refs_fail(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/gate-downgrade-fake-ref.json"), "does not resolve"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/routing-downgrade-fake-ref.json"), "does not resolve"))

    def test_every_registered_record_type_is_resolved(self):
        types = {k.split(":", 1)[0] for k in REGISTRY["decision_ref_fields"]}
        self.assertEqual(types, {"project-config", "gate", "task-routing"})
        src = open(__file__).read()
        for t in types:
            self.assertIn(f'resolve_decision_refs("{t}"', src, t)


class C03_DowngradeBinding(unittest.TestCase):
    def test_animation_decision_cannot_downgrade_ui_ux(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/downgrade-animation-decision-on-ui-ux.json"), "does not match"))

    def test_scope_and_revision_bound(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/downgrade-other-subject.json"), "does not match"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/downgrade-other-revision.json"), "does not match"))
        self.assertEqual(_record_set("tests/fixtures/authority/downgrade-bound-ok.json"), [])


class C04_RoutingGateLinkage(unittest.TestCase):
    def test_condition_owner_policy_and_unrouted_gates(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/routing-gate-condition-mismatch.json"), "applied_conditions"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/routing-gate-owner-policy-mismatch.json"), "review_policy"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/routing-gate-raised-after-routing.json"), "raised after routing"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/gate-routing-ref-unknown.json"), "does not resolve to a routing record"))

    def test_schema_linkage_fields(self):
        self.assertIn("routing_ref", SCHEMAS["gate"]["properties"])
        self.assertIn("subject", SCHEMAS["task-routing"]["required"])


class C05_RoutedReadiness(unittest.TestCase):
    ROUTING = "examples/example-task-routing.json"

    def _parts(self):
        routing = load_json(self.ROUTING)
        config = build({"base": "examples/minimal-project-config.json",
                        "set": {"/presentation": {"target_presentation_differs_from_editor": "NO", "decision_ref": "D-0004"}}})
        decision = build({"base": "examples/example-decision-record.json", "remove": ["/transition"],
                          "set": {"/decision_id": "D-0004", "/kind": "PRESENTATION_PARITY", "/value": "NO"}})
        motion = load_json("examples/example-evidence-record.json")
        runtime = dict(copy.deepcopy(motion), evidence_id="EV-R", type="RUNTIME_EVIDENCE")
        tech = _linked_gate("TECHNICAL", "qa-performance", review_policy="ROUTINE", routine_basis="x", applied_conditions=[],
                            evidence_refs=["EV-R"])
        tech.pop("cross_reviews")
        anim = _linked_gate("ANIMATION", "character-animation",
                            cross_reviews=[{"reviewer": "camera-composition", "assessment": "PASS", "reviewed_revision": "rev-example-0002"}])
        return config, [decision], routing, tech, anim, [motion, runtime]

    def test_missing_required_gate_is_not_ready(self):
        config, decisions, routing, tech, anim, evidence = self._parts()
        self.assertFalse(routed_scope_ready(config, routing, [], evidence, decisions))
        self.assertFalse(routed_scope_ready(config, routing, [tech], evidence, decisions))  # ANIMATION missing
        self.assertTrue(scope_ready([tech]))                                                 # the gate-only helper cannot see it

    def test_all_required_pass_is_ready_and_not_applicable_is_not(self):
        config, decisions, routing, tech, anim, evidence = self._parts()
        self.assertTrue(routed_scope_ready(config, routing, [tech, anim], evidence, decisions))
        na = dict(anim, status="NOT_APPLICABLE", not_applicable_reason="x")
        self.assertFalse(routed_scope_ready(config, routing, [tech, na], evidence, decisions))
        self.assertFalse(routed_scope_ready(config, routing, [tech, dict(anim, status="CHANGES_REQUIRED")], evidence, decisions))
        self.assertFalse(routed_scope_ready(config, routing, [tech, anim], evidence[:1], decisions))  # TECHNICAL evidence missing
        undecided = load_json("examples/minimal-project-config.json")  # parity UNDECIDED: the routing's decline is invalid
        self.assertFalse(routed_scope_ready(undecided, routing, [tech, anim], evidence, []))
        self.assertIn("never proof of a routed task's readiness", read("core/ROLE-ROUTING.md"))


class C06_RoutingUniqueness(unittest.TestCase):
    def test_duplicate_and_conflicting_gates_rejected(self):
        r = load_json("examples/example-task-routing.json")
        dup = copy.deepcopy(r)
        dup["required_gates"].append(copy.deepcopy(dup["required_gates"][0]))
        self.assertTrue(_has(routing_problems(dup), "appears more than once in required_gates"))
        both = copy.deepcopy(r)
        both["omitted_gates"].append({"gate": "ANIMATION", "reason": "x"})
        self.assertTrue(_has(routing_problems(both), "both required and omitted"))
        twice = copy.deepcopy(r)
        twice["omitted_gates"].append(copy.deepcopy(twice["omitted_gates"][0]))
        self.assertTrue(_has(routing_problems(twice), "appears more than once in omitted_gates"))
        self.assertGreaterEqual(len(REGISTRY["routing_rules"]), 3)
        self.assertIn("required_gates and omitted_gates are disjoint", REGISTRY["routing_rules"])


class C07_RevisionBoundCrossReview(unittest.TestCase):
    def test_stale_cross_review_cannot_close(self):
        self.assertTrue(_has(_rec("tests/fixtures/records/cross-review-stale-revision.json"), "stale cross-review"))
        self.assertEqual(_rec("tests/fixtures/records/cross-review-superseded-old-revision-ok.json"), [])
        self.assertIn("reviewed_revision", SCHEMAS["gate"]["properties"]["cross_reviews"]["items"]["required"])


class C08_MultiRoutingAuthority(unittest.TestCase):
    def test_every_routing_checked(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/multi-routing-one-declines-target-runtime.json"),
                             "declines TARGET_PRESENTATION_DIFFERS"))
        self.assertEqual(_record_set("tests/fixtures/authority/multi-routing-parity-no-ok.json"), [])
        self.assertNotIn("len(routings) == 1", open(__file__).read().split("def record_set_problems")[1].split("def ")[0])


class C09_ProjectTriggers(unittest.TestCase):
    def test_unknown_trigger_fails_known_resolves(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/project-trigger-unknown.json"), "not a project trigger"))
        self.assertEqual(_record_set("tests/fixtures/authority/project-trigger-known.json"), [])
        self.assertTrue(_has(_record_set("tests/fixtures/authority/duplicate-project-trigger-ids.json"), "duplicate project trigger id"))

    def test_project_trigger_forces_human_review(self):
        fx = _fixture("tests/fixtures/invalid/routing-project-trigger-without-human-review.json")
        self.assertIn("const", [e[1] for e in validate("task-routing", build(fx))])
        self.assertEqual(validate("task-routing", build(_fixture("tests/fixtures/valid/routing-project-trigger-only.json"))), [])
        items = SCHEMAS["task-routing"]["properties"]["review_triggers"]["items"]["anyOf"]
        self.assertEqual(items[0]["enum"], TRIGGERS)
        self.assertTrue(items[1]["pattern"].startswith("^PROJECT:"))


class C10_WorkflowTriggerSemantics(unittest.TestCase):
    def test_prose_and_registry_agree(self):
        declared = {}
        for wf in REGISTRY["workflows"]:
            points = h2_sections(workflow_text(wf))[1]["HUMAN REVIEW POINTS"]
            found = re.findall(r"^Mandatory trigger: `([A-Z_]+)`", points, re.M)
            if found:
                declared[wf] = found
        self.assertEqual(declared, REGISTRY["workflow_mandatory_triggers"])
        points = h2_sections(workflow_text("character-production"))[1]["HUMAN REVIEW POINTS"]
        self.assertIn("Conditional trigger: `CANONICAL_CREATIVE_ASSET`", points)


class C11_RoutinePass(unittest.TestCase):
    def test_routine_pass_requires_owner_assessment(self):
        for rel in ["tests/fixtures/invalid/gate-routine-pass-assessed-by-human.json",
                    "tests/fixtures/invalid/gate-routine-pass-without-owner-assessment.json"]:
            self.assertNotEqual(validate("gate", build(_fixture(rel))), [], rel)
        g = build(_fixture("tests/fixtures/invalid/gate-routine-pass-assessed-by-human.json"))
        self.assertTrue(_has(gate_evidence_problems(g, {}), "ROUTINE PASS must be the owning specialist"))


class C12_ConfigDecisionSubjects(unittest.TestCase):
    def test_unrelated_subject_fails(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/config-decision-unrelated-asset-subject.json"), "subject kind ASSET"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/config-decision-unrelated-task-subject.json"), "subject kind TASK"))

    def test_rules_cover_config_kinds(self):
        rules = REGISTRY["decision_subject_rules"]
        for key, kinds in REGISTRY["decision_ref_fields"].items():
            if key.startswith("project-config:"):
                for k in kinds:
                    self.assertIn(k, rules, k)
        self.assertEqual(rules["QUALITY_TARGET"], {"subject_kinds": ["PROJECT"], "subject_ref": "PROJECT_ID"})


class C13_HumanReviewScope(unittest.TestCase):
    def test_wrong_scope_kind_or_revision_cannot_close(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/human-review-wrong-scope-kind.json"), "same scope kind"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/human-review-wrong-revision.json"), "same scope kind"))


class C14_Phase2ContractAndBoundary(unittest.TestCase):
    def test_contract_and_boundary(self):
        section = h2_sections(read("core/GOVERNANCE.md"))[1]["12. Phase-2 acceptance requirement: record validation"]
        for phrase in ["matching gate record", "metadata agree", "applied_conditions", "non-conflicting",
                       "evidence subject applies", "reviewed the current", "every record type",
                       "every routing record", "project-specific mandatory triggers"]:
            self.assertIn(phrase, section, phrase)
        self.assertEqual(len(REGISTRY["skills"]), 13)
        for s_ in REGISTRY["skills"]:
            self.assertEqual(front_matter(skill_text(s_))["maturity"], "DRAFT", s_)
        self.assertEqual(phase_boundary_problems(), [])


# ---------------------------------------------------------------- alpha.6 routing authority closure (D01–D10)

def _example_routing(**changes):
    r = load_json("examples/example-task-routing.json")
    r.update(changes)
    return r


def _item(gate, owner, policy, evidence, **extra):
    return dict({"gate": gate, "owner": owner, "blocking": True, "review_policy": policy, "required_evidence": evidence}, **extra)


def _ready(rel, config_patch=None, decisions_extra=()):
    fx = _fixture(rel)
    cfg_spec = copy.deepcopy(fx["config"])
    cfg_spec["set"].update(config_patch or {})
    return routed_scope_ready(build(cfg_spec), build(_fixture(fx["routing"])), [build(g) for g in fx.get("gates", [])],
                              [build(e) for e in fx.get("evidence", [])], [build(d) for d in fx["decisions"]] + list(decisions_extra))


class D01_EffectiveReviewPolicy(unittest.TestCase):
    def test_project_override_cannot_be_weakened(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/override-hrr-routed-as-crr.json"), "weaker than the effective policy"))

    def test_weakened_override_makes_routed_scope_not_ready(self):
        ok = "tests/fixtures/authority/cross-review-by-routed-specialist-ok.json"
        self.assertTrue(_ready(ok))
        override = build({"base": "examples/example-decision-record.json", "remove": ["/transition"], "set": {
            "/decision_id": "D-0021", "/kind": "REVIEW_POLICY_OVERRIDE",
            "/value": {"gate": "ANIMATION", "review_policy": "HUMAN_REVIEW_REQUIRED"}}})
        patch = {"/human_review/review_policy_overrides": [{"gate": "ANIMATION", "review_policy": "HUMAN_REVIEW_REQUIRED", "decision_ref": "D-0021"}]}
        self.assertFalse(_ready(ok, patch, [override]))

    def test_scoped_override_applies_only_to_matching_subject(self):
        self.assertEqual(_record_set("tests/fixtures/authority/override-scoped-other-subject.json"), [])
        self.assertTrue(_has(_record_set("tests/fixtures/authority/override-scoped-matching-subject.json"), "weaker than the effective policy"))

    def test_ambiguous_and_duplicate_overrides_fail(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/override-ambiguous.json"), "more than one applicable"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/override-duplicate.json"), "duplicate review-policy override"))

    def test_scope_is_structured(self):
        scope = SCHEMAS["project-config"]["properties"]["human_review"]["properties"]["review_policy_overrides"]["items"]["properties"]["scope"]
        self.assertEqual(scope["required"], ["kind", "ref"])
        self.assertIn("type", [e[1] for e in validate("project-config", build(_fixture("tests/fixtures/invalid/project-config-override-scope-free-text.json")))])


class D02_PolicyPrecedence(unittest.TestCase):
    def test_trigger_beats_weaker_override(self):
        cfg = build({"base": "examples/minimal-project-config.json", "set": {"/human_review/review_policy_overrides": [
            {"gate": "CAMERA_COMPOSITION", "review_policy": "ROUTINE", "decision_ref": "D-0021"}]}})
        routing = build(_fixture("tests/fixtures/valid/routing-camera-human-review-only.json"))
        item = routing["required_gates"][0]
        self.assertEqual(effective_policy_floor(cfg, routing, item)[0], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(_record_set("tests/fixtures/authority/override-weaker-than-trigger-ok.json"), [])
        weakened = copy.deepcopy(routing)
        weakened["required_gates"][0]["review_policy"] = "ROUTINE"
        weakened["required_gates"][0]["routine_basis"] = "project override says routine"
        self.assertNotEqual(validate("task-routing", weakened), [])       # schema: triggers force HUMAN_REVIEW_REQUIRED
        self.assertTrue(_has(authority_problems(cfg, [], (), [weakened]), "weaker than the effective policy"))

    def test_override_then_routing(self):
        cfg = build({"base": "examples/minimal-project-config.json", "set": {"/human_review/review_policy_overrides": [
            {"gate": "ANIMATION", "review_policy": "CROSS_REVIEW_REQUIRED", "decision_ref": "D-0021"}]}})
        routing = load_json("examples/example-task-routing.json")
        self.assertEqual(effective_policy_floor(cfg, routing, routing["required_gates"][0])[0], "CROSS_REVIEW_REQUIRED")
        self.assertEqual(effective_policy_floor(load_json("examples/minimal-project-config.json"), routing, routing["required_gates"][0]), (None, "routing"))
        self.assertEqual(REGISTRY["effective_review_policy_precedence"], ["MANDATORY_TRIGGER", "PROJECT_OVERRIDE", "ROUTING"])


class D03_WorkflowInvariants(unittest.TestCase):
    CASES = {
        "gameplay-feature": ("GAMEPLAY_DESIGN", [_item("TECHNICAL", "qa-performance", "ROUTINE", ["RUNTIME_EVIDENCE"], routine_basis="x",
                                                       unapplied_conditions=[{"condition": "PERSISTENCE_AFFECTED", "reason": "x"}])]),
        "animation-production": ("ANIMATION", [_item("TECHNICAL", "qa-performance", "ROUTINE", ["RUNTIME_EVIDENCE"], routine_basis="x",
                                                     unapplied_conditions=[{"condition": "PERSISTENCE_AFFECTED", "reason": "x"}])]),
        "ui-production": ("UI_UX", [_item("TECHNICAL", "qa-performance", "ROUTINE", ["RUNTIME_EVIDENCE"], routine_basis="x",
                                          unapplied_conditions=[{"condition": "PERSISTENCE_AFFECTED", "reason": "x"}])]),
        "runtime-system": ("TECHNICAL", [_item("PERFORMANCE", "qa-performance", "ROUTINE", ["PERFORMANCE_EVIDENCE"], routine_basis="x",
                                               unapplied_conditions=[{"condition": "TARGET_PLATFORM_PERFORMANCE_CLAIM", "reason": "x"}])]),
    }

    def test_always_required_gate_cannot_be_omitted(self):
        for wf, (gate, required) in self.CASES.items():
            r = _example_routing(workflow=wf, required_gates=required, omitted_gates=[{"gate": gate, "reason": "not relevant"}])
            self.assertEqual(validate("task-routing", r), [], wf)                     # schema cannot see it
            self.assertTrue(_has(routing_problems(r), f"{gate} is always required by {wf} and cannot be omitted"), wf)
            r2 = _example_routing(workflow=wf, required_gates=required, omitted_gates=[])
            self.assertTrue(_has(routing_problems(r2), f"{gate} is always required by {wf} but is not in required_gates"), wf)

    def test_registry_matches_workflow_prose(self):
        for wf in REGISTRY["workflows"]:
            section = h2_sections(workflow_text(wf))[1]["REQUIRED GATES"]
            always = re.search(r"^Always required: (.*)$", section, re.M).group(1)
            when = re.search(r"^When affected: (.*)$", section, re.M).group(1)
            wr = REGISTRY["workflow_gate_requirements"][wf]
            self.assertEqual(backticked(always), wr["always_required"], wf)
            self.assertEqual(backticked(when), wr["when_affected"], wf)
            mentioned = {t for t in backticked(section) if t in GATES}
            self.assertTrue(mentioned <= set(wr["always_required"]) | set(wr["when_affected"]), f"{wf}: {mentioned}")
            self.assertFalse(set(wr["always_required"]) & set(wr["when_affected"]), wf)
        self.assertEqual(set(REGISTRY["workflow_gate_requirements"]), set(REGISTRY["workflows"]))

    def test_character_production_keeps_human_review_conditional(self):
        wr = REGISTRY["workflow_gate_requirements"]["character-production"]
        self.assertNotIn("HUMAN_REVIEW", wr["always_required"])
        self.assertIn("HUMAN_REVIEW", wr["when_affected"])
        self.assertNotIn("character-production", REGISTRY["workflow_mandatory_triggers"])


class D04_RoutingEvidenceContract(unittest.TestCase):
    def test_invalid_evidence_type_rejected(self):
        r = load_json("examples/example-task-routing.json")
        r["required_gates"][1]["required_evidence"] = ["RUNTIME_EVIDENCE", "DEVICE_EVIDENCE"]
        self.assertTrue(_has(routing_problems(r), "not valid evidence for TECHNICAL"))

    def test_extra_evidence_is_enforced(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/routed-extra-evidence-missing.json"), "lacks routing-required evidence"))
        self.assertEqual(_record_set("tests/fixtures/authority/routed-extra-evidence-present-ok.json"), [])

    def test_readiness_uses_routed_evidence(self):
        self.assertFalse(_ready("tests/fixtures/authority/routed-extra-evidence-missing.json"))
        self.assertTrue(_ready("tests/fixtures/authority/routed-extra-evidence-present-ok.json"))


class D05_RoutedCrossReviewers(unittest.TestCase):
    def test_unrouted_specialist_cannot_close(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/cross-review-by-unrouted-specialist.json"), "not a routed reviewer"))

    def test_routed_specialist_can_close(self):
        self.assertEqual(_record_set("tests/fixtures/authority/cross-review-by-routed-specialist-ok.json"), [])
        self.assertTrue(_ready("tests/fixtures/authority/cross-review-by-routed-specialist-ok.json"))
        self.assertFalse(_ready("tests/fixtures/authority/cross-review-by-unrouted-specialist.json"))


class D05b_RoutedReviewerRequiredWhenFlagged(unittest.TestCase):
    def test_human_review_with_cross_review_flag_needs_routed_reviewer(self):
        routing = build(_fixture("tests/fixtures/valid/routing-camera-human-review-only.json"))
        item = routing["required_gates"][0]
        item["cross_review_required"] = True
        routing["reviewers"] = ["HUMAN", "level-design"]
        gate = _linked_gate("CAMERA_COMPOSITION", "camera-composition", review_policy="HUMAN_REVIEW_REQUIRED",
                            cross_review_required=True, applied_conditions=list(item.get("applied_conditions", [])),
                            human_review_ref="GATE-HR1", cross_reviews=[])
        problems = routing_gate_problems(routing, [gate])[0]
        self.assertTrue(_has(problems, "needs a current passing cross-review by a routed reviewer"), problems)
        gate["cross_reviews"] = [{"reviewer": "level-design", "assessment": "PASS", "reviewed_revision": "rev-example-0002"}]
        self.assertFalse(_has(routing_gate_problems(routing, [gate])[0], "cross-review"))


class D06_ConditionAccounting(unittest.TestCase):
    def test_condition_cannot_silently_disappear(self):
        r = load_json("examples/example-task-routing.json")
        r["required_gates"][0].pop("unapplied_conditions")
        self.assertEqual(validate("task-routing", r), [])
        self.assertTrue(_has(routing_problems(r), "condition TARGET_PRESENTATION_DIFFERS is not accounted for"))

    def test_exactly_once(self):
        r = load_json("examples/example-task-routing.json")
        g = r["required_gates"][0]
        g["applied_conditions"] = ["TARGET_PRESENTATION_DIFFERS"]
        self.assertTrue(_has(routing_problems(r), "both applied and unapplied"))
        r2 = load_json("examples/example-task-routing.json")
        r2["required_gates"][0]["unapplied_conditions"].append(copy.deepcopy(r2["required_gates"][0]["unapplied_conditions"][0]))
        self.assertTrue(_has(routing_problems(r2), "appears more than once in unapplied_conditions"))
        r3 = load_json("examples/example-task-routing.json")
        r3["required_gates"][0]["unapplied_conditions"].append({"condition": "TOUCH_OR_MOBILE_TARGET", "reason": "x"})
        self.assertTrue(_has(routing_problems(r3), "is not a condition of ANIMATION"))
        self.assertEqual(routing_problems(load_json("examples/example-task-routing.json")), [])

    def test_parity_yes_forces_condition(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/parity-yes-condition-declined.json"), "must apply TARGET_PRESENTATION_DIFFERS"))
        self.assertEqual(_record_set("tests/fixtures/authority/parity-yes-condition-applied-ok.json"), [])

    def test_every_valid_routing_fixture_accounts_for_conditions(self):
        for f in sorted((ROOT / "tests/fixtures/valid").glob("routing-*.json")):
            r = build(_fixture(str(f.relative_to(ROOT))))
            self.assertEqual([p for p in routing_problems(r) if "not accounted" in p], [], f.name)


class D07_GoldenCellAccounting(unittest.TestCase):
    def test_all_twelve_gates_accounted(self):
        routing = build(_fixture("tests/fixtures/valid/routing-golden-cell.json"))
        self.assertEqual(routing_problems(routing), [])
        accounted = {g["gate"] for g in routing["required_gates"]} | {g["gate"] for g in routing["omitted_gates"]}
        self.assertEqual(accounted, set(GATES))
        self.assertTrue(REGISTRY["workflow_gate_requirements"]["golden-gameplay-cell"]["account_for_all_gates"])

    def test_irrelevant_gate_only_with_reason(self):
        routing = build(_fixture("tests/fixtures/valid/routing-golden-cell.json"))
        silent = copy.deepcopy(routing)
        silent["omitted_gates"] = []
        self.assertTrue(_has(routing_problems(silent), "must account for AUDIO"))
        self.assertIn("minLength", [e[1] for e in validate("task-routing", build(_fixture("tests/fixtures/invalid/routing-golden-cell-omission-without-reason.json")))])
        dropped = copy.deepcopy(routing)
        dropped["required_gates"] = [g for g in dropped["required_gates"] if g["gate"] != "GAME_FEEL_VFX"]
        dropped["omitted_gates"].append({"gate": "GAME_FEEL_VFX", "reason": "not needed"})
        self.assertTrue(_has(routing_problems(dropped), "GAME_FEEL_VFX is always required by golden-gameplay-cell"))
        self.assertIn("Without explanation, does a short representative gameplay recording look and feel like the intended production game?",
                      routing["human_review"]["primary_question"])


class D08_Phase2Contract(unittest.TestCase):
    def test_contract_lists_closure_requirements(self):
        section = h2_sections(read("core/GOVERNANCE.md"))[1]["12. Phase-2 acceptance requirement: record validation"]
        for phrase in ["always_required", "review-policy overrides", "effective review policy", "counting gate evidence",
                       "not valid for the gate", "routing's specialist reviewers", "applied or declined",
                       "presentation parity `YES`", "every quality gate"]:
            self.assertIn(phrase, section, phrase)


class D09_DocsState(unittest.TestCase):
    def test_docs_describe_the_model(self):
        qg = h2_sections(read("core/QUALITY-GATES.md"))[1]["7. Review policy"]
        self.assertIn("Effective review policy", qg)
        rr = read("core/ROLE-ROUTING.md")
        for phrase in ["always required", "authoritative for the task", "routed reviewer", "accounted for exactly once"]:
            self.assertIn(phrase, rr, phrase)
        self.assertIn("omitted from the cell routing with a reason", read("core/GOLDEN-GAMEPLAY-CELL.md"))


class D10_MaturityAndBoundary(unittest.TestCase):
    def test_all_draft_no_phase2(self):
        self.assertEqual(len(REGISTRY["skills"]), 13)
        for s_ in REGISTRY["skills"]:
            self.assertEqual(front_matter(skill_text(s_))["maturity"], "DRAFT", s_)
        self.assertEqual(phase_boundary_problems(), [])


# ---------------------------------------------------------------- alpha.7 freeze hardening (E01–E06)

def _anim_record_set_with_reviewer(reviewer):
    """The routed-specialist-ok record set, with `reviewer` routed and as the only contributing ANIMATION cross-reviewer."""
    fx = _fixture("tests/fixtures/authority/cross-review-by-routed-specialist-ok.json")
    routing = build(_fixture(fx["routing"]))
    routing["reviewers"] = [reviewer]
    gates = [build(g) for g in fx["gates"]]
    gates[0]["cross_reviews"] = [{"reviewer": reviewer, "assessment": "PASS", "reviewed_revision": "rev-example-0002"}]
    config, decisions, evidence = build(fx["config"]), [build(d) for d in fx["decisions"]], [build(e) for e in fx["evidence"]]
    return config, decisions, evidence, gates, routing


class E01_CrossReviewEligibility(unittest.TestCase):
    def test_registry_matches_routing_table(self):
        table = h2_sections(read("core/ROLE-ROUTING.md"))[1]["4. Cross-review"]
        expected = {row[0].strip("`"): [t for t in backticked(row[1]) if t in REGISTRY["skills"]] for row in table_rows(table)}
        self.assertEqual(REGISTRY["cross_review_eligibility"], expected)
        self.assertEqual(REGISTRY["never_cross_reviewer"], ["game-director"])
        for owner, reviewers in REGISTRY["cross_review_eligibility"].items():
            self.assertNotIn("game-director", reviewers, owner)
            self.assertNotIn(owner, reviewers, owner)
        self.assertEqual(REGISTRY["cross_review_eligibility"]["character-animation"], ["camera-composition", "game-feel-vfx"])

    def test_ineligible_reviewers_cannot_close_animation(self):
        for reviewer in ["game-director", "qa-performance", "game-engineering", "audio-design", "ui-ux"]:
            config, decisions, evidence, gates, routing = _anim_record_set_with_reviewer(reviewer)
            problems = record_set_problems(config, decisions, evidence, gates, [routing])
            self.assertTrue(problems, reviewer)
            self.assertFalse(routed_scope_ready(config, routing, gates, evidence, decisions), reviewer)

    def test_eligible_reviewers_can_close_animation(self):
        for reviewer in ["camera-composition", "game-feel-vfx"]:
            config, decisions, evidence, gates, routing = _anim_record_set_with_reviewer(reviewer)
            self.assertEqual(record_set_problems(config, decisions, evidence, gates, [routing]), [], reviewer)
            self.assertTrue(routed_scope_ready(config, routing, gates, evidence, decisions), reviewer)

    def test_routed_but_ineligible_is_named_and_owner_still_cannot_self_review(self):
        config, decisions, evidence, gates, routing = _anim_record_set_with_reviewer("qa-performance")
        self.assertTrue(_has(record_set_problems(config, decisions, evidence, gates, [routing]), "routed but not eligible"))
        self.assertTrue(_has(gate_evidence_problems(gates[0], {}), "eligible for character-animation"))
        own = copy.deepcopy(gates[0])
        own["cross_reviews"] = [{"reviewer": "character-animation", "assessment": "PASS", "reviewed_revision": "rev-example-0002"}]
        self.assertTrue(_has(gate_evidence_problems(own, {}), "cross-reviewed its own gate"))
        self.assertNotEqual(validate("gate", own), [])

    def test_never_cross_reviewer_guard_holds_even_if_listed(self):
        saved = copy.deepcopy(REGISTRY["cross_review_eligibility"])
        try:
            REGISTRY["cross_review_eligibility"]["character-animation"].append("game-director")
            self.assertFalse(eligible_cross_reviewer("character-animation", "game-director"))
            self.assertTrue(eligible_cross_reviewer("character-animation", "camera-composition"))
        finally:
            REGISTRY["cross_review_eligibility"].clear()
            REGISTRY["cross_review_eligibility"].update(saved)

    def test_game_director_rejected_by_schema(self):
        g = build({"base": "examples/example-gate-record.json",
                   "set": {"/cross_reviews": [{"reviewer": "game-director", "assessment": "CHANGES_REQUIRED", "reviewed_revision": "rev-example-0002"}]}})
        self.assertIn("not", [e[1] for e in validate("gate", g)])
        self.assertIn("never reviews discipline quality", skill_section("game-director", "CROSS-REVIEW") + read("core/ROLE-ROUTING.md"))

    def test_human_review_with_cross_review_flag_uses_same_eligibility(self):
        g = _linked_gate("CAMERA_COMPOSITION", "camera-composition", review_policy="HUMAN_REVIEW_REQUIRED", cross_review_required=True,
                         human_review_ref="GATE-HR1", disagreements_disclosed=True,
                         cross_reviews=[{"reviewer": "audio-design", "assessment": "PASS", "reviewed_revision": "rev-example-0002"}])
        self.assertTrue(_has(gate_evidence_problems(g, {}), "eligible for camera-composition"))
        g["cross_reviews"][0]["reviewer"] = "level-design"
        self.assertFalse(_has(gate_evidence_problems(g, {}), "eligible for"))


class E02_ReleaseAllGateAccounting(unittest.TestCase):
    def test_release_accounts_for_every_gate(self):
        self.assertTrue(REGISTRY["workflow_gate_requirements"]["release"]["account_for_all_gates"])
        full = build(_fixture("tests/fixtures/valid/routing-release-all-gates.json"))
        self.assertEqual(routing_problems(full), [])
        self.assertEqual({g["gate"] for g in full["required_gates"]} | {g["gate"] for g in full["omitted_gates"]}, set(GATES))

    def test_thin_and_forgetful_release_routings_fail(self):
        thin = build(_fixture("tests/fixtures/valid/routing-release-thin.json"))
        self.assertEqual(validate("task-routing", thin), [])
        problems = routing_problems(thin)
        for g in ["GAMEPLAY_DESIGN", "LEVEL_DESIGN", "ANIMATION", "CAMERA_COMPOSITION", "VISUAL_ART", "GAME_FEEL_VFX", "UI_UX", "AUDIO"]:
            self.assertTrue(_has(problems, f"release must account for {g}"), g)
        self.assertTrue(_has(routing_problems(build(_fixture("tests/fixtures/valid/routing-release-missing-visual-art.json"))), "account for VISUAL_ART"))
        self.assertTrue(_has(routing_problems(build(_fixture("tests/fixtures/valid/routing-release-missing-gameplay-design.json"))), "account for GAMEPLAY_DESIGN"))

    def test_audio_omission_with_reason_and_without(self):
        full = build(_fixture("tests/fixtures/valid/routing-release-all-gates.json"))
        audio = [o for o in full["omitted_gates"] if o["gate"] == "AUDIO"][0]
        self.assertTrue(audio["reason"])
        self.assertEqual(routing_problems(full), [])
        self.assertIn("minLength", [e[1] for e in validate("task-routing", build(_fixture("tests/fixtures/invalid/routing-release-omission-without-reason.json")))])

    def test_release_prose_declares_accounting(self):
        for wf, wr in REGISTRY["workflow_gate_requirements"].items():
            section = h2_sections(workflow_text(wf))[1]["REQUIRED GATES"]
            self.assertEqual(bool(re.search(r"^Accounting: every one of the 12 quality gates", section, re.M)),
                             bool(wr.get("account_for_all_gates")), wf)


class E03_TargetPlatformBinding(unittest.TestCase):
    def test_vocabulary_shared(self):
        self.assertEqual(SCHEMAS["evidence"]["properties"]["provenance"]["properties"]["target_platform"]["enum"], REGISTRY["platforms"])
        self.assertEqual(SCHEMAS["project-config"]["properties"]["target_platforms"]["items"]["properties"]["platform"]["enum"],
                         REGISTRY["platforms"] + ["UNDECIDED"])
        bad = build({"base": "examples/example-evidence-record.json", "set": {"/provenance/target_platform": "Random-PC"}})
        self.assertIn("enum", [e[1] for e in validate("evidence", bad)])

    def test_non_target_platform_does_not_count(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/device-evidence-windows-for-android-project.json"), "not a declared project target platform"))
        self.assertTrue(_has(_record_set("tests/fixtures/authority/release-device-on-non-target-platform.json"), "not a declared project target platform"))

    def test_undeclared_reference_device_does_not_count_and_matching_does(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/release-device-not-reference-device.json"), "not a declared ANDROID reference device"))
        self.assertEqual(_record_set("tests/fixtures/authority/release-two-primary-covered.json"), [])

    def test_no_reference_devices_means_any_device_on_that_platform(self):
        fx = _fixture("tests/fixtures/authority/release-two-primary-covered.json")
        windows = [t for t in fx["config"]["set"]["/target_platforms"] if t["platform"] == "WINDOWS"][0]
        self.assertNotIn("reference_devices", windows)
        self.assertEqual(_record_set("tests/fixtures/authority/release-two-primary-covered.json"), [])


class E04_ReleasePrimaryCoverage(unittest.TestCase):
    def test_only_one_primary_covered_is_not_ready(self):
        rel = "tests/fixtures/authority/release-two-primary-only-android.json"
        self.assertTrue(_has(_record_set(rel), "no counting DEVICE_EVIDENCE for PRIMARY platform WINDOWS"))
        self.assertTrue(_has(_record_set(rel), "no counting PERFORMANCE_EVIDENCE for PRIMARY platform WINDOWS"))
        self.assertFalse(_ready(rel))

    def test_both_primaries_covered_is_ready(self):
        self.assertTrue(_ready("tests/fixtures/authority/release-two-primary-covered.json"))

    def test_secondary_absence_does_not_block(self):
        fx = _fixture("tests/fixtures/authority/release-two-primary-covered.json")
        tiers = {t["platform"]: t["tier"] for t in fx["config"]["set"]["/target_platforms"]}
        self.assertEqual(tiers["IOS"], "SECONDARY")
        ev_platforms = {e["set"]["/provenance"].get("target_platform") for e in fx["evidence"]}
        self.assertNotIn("IOS", ev_platforms)
        self.assertTrue(_ready("tests/fixtures/authority/release-two-primary-covered.json"))

    def test_one_platform_cannot_cover_another(self):
        fx = _fixture("tests/fixtures/authority/release-two-primary-covered.json")
        config = build(fx["config"])
        evidence = [build(e) for e in fx["evidence"]]
        for e in evidence:
            if e["evidence_id"] in ("DEV-W", "PERF-W"):
                e["provenance"]["target_platform"] = "ANDROID"
                e["provenance"]["device"] = "Pixel-X"
        problems = record_set_problems(config, [], evidence, [build(g) for g in fx["gates"]], [build(_fixture(fx["routing"]))])
        self.assertTrue(_has(problems, "PRIMARY platform WINDOWS"))

    def test_thin_release_routing_is_not_ready(self):
        self.assertTrue(_has(_record_set("tests/fixtures/authority/release-thin-routing.json"), "release must account for VISUAL_ART"))
        self.assertFalse(_ready("tests/fixtures/authority/release-thin-routing.json"))


class E05_Phase2FreezeContract(unittest.TestCase):
    def test_contract(self):
        section = h2_sections(read("core/GOVERNANCE.md"))[1]["12. Phase-2 acceptance requirement: record validation"]
        for phrase in ["eligible for the owning discipline", "game-director never acts", "Release accounts for every quality gate",
                       "declared project target platform", "reference devices", "every PRIMARY target platform",
                       "one target platform cannot satisfy another"]:
            self.assertIn(phrase, section, phrase)
        self.assertIn("richer device-coverage planning", read("README.md").lower())


class E06_FreezeBoundary(unittest.TestCase):
    def test_all_draft_and_no_phase2(self):
        self.assertEqual(len(REGISTRY["skills"]), 13)
        for s_ in REGISTRY["skills"]:
            self.assertEqual(front_matter(skill_text(s_))["maturity"], "DRAFT", s_)
        self.assertEqual(phase_boundary_problems(), [])


# ---------------------------------------------------------------- consistency (X00–X10)

class X00_GateEvidenceTableMatchesRegistry(unittest.TestCase):
    def test_quality_gates_section_5(self):
        rows = table_rows(h2_sections(read("core/QUALITY-GATES.md"))[1]["5. Evidence per gate"])
        self.assertEqual(len(rows), len(GATES))
        ev_types = set(REGISTRY["evidence_types"])
        for gate, base, conditional, insufficient in rows:
            r = GATES[gate.strip("`")]
            self.assertEqual(set(backticked(base)), set(r["base_evidence"]["all_of"]) | set(r["base_evidence"]["any_of"]), gate)
            self.assertEqual({t for t in backticked(conditional) if t in CONDITIONS},
                             {c["condition"] for c in r["conditional_evidence"]}, gate)
            self.assertEqual({t for t in backticked(conditional) if t in ev_types},
                             {t for c in r["conditional_evidence"] for t in c["requires"]}, gate)
            self.assertEqual({t for t in backticked(conditional) if t in REGISTRY["capture_contexts"]},
                             {x for c in r["conditional_evidence"] for x in c.get("requires_context", [])}, gate)
            self.assertEqual(set(backticked(insufficient)), set(r["insufficient_alone"]), gate)


class X01_SchemasMatchRegistry(unittest.TestCase):
    def test_enums(self):
        skills = REGISTRY["skills"]
        self.assertEqual(SCHEMAS["task-routing"]["$defs"]["skill"]["enum"], skills)
        self.assertEqual(SCHEMAS["gate"]["$defs"]["skill"]["enum"], skills)
        self.assertEqual(SCHEMAS["task-routing"]["properties"]["workflow"]["enum"], REGISTRY["workflows"])
        self.assertEqual(SCHEMAS["task-routing"]["properties"]["lifecycle_stage"]["enum"], REGISTRY["lifecycle_stages"])
        self.assertEqual(SCHEMAS["project-config"]["properties"]["lifecycle_stage"]["enum"], REGISTRY["lifecycle_stages"])
        self.assertEqual(SCHEMAS["evidence"]["properties"]["source"]["properties"]["kind"]["enum"], REGISTRY["actor_kinds"])
        self.assertEqual(SCHEMAS["gate"]["$defs"]["actor"]["properties"]["kind"]["enum"], REGISTRY["actor_kinds"])
        self.assertEqual(SCHEMAS["gate"]["properties"]["scope"]["properties"]["kind"]["enum"], REGISTRY["gate_scope_kinds"])
        self.assertEqual(SCHEMAS["project-config"]["properties"]["golden_gameplay_cell"]["properties"]["status"]["enum"],
                         REGISTRY["golden_cell_statuses"])
        self.assertEqual(SCHEMAS["gate"]["properties"]["applied_conditions"]["items"]["enum"], CONDITIONS)
        self.assertEqual(SCHEMAS["gate"]["properties"]["owner"]["enum"], GATE_OWNERS)
        self.assertEqual(SCHEMAS["task-routing"]["properties"]["required_gates"]["items"]["properties"]["owner"]["enum"], GATE_OWNERS)
        self.assertNotIn("game-director", GATE_OWNERS)
        self.assertNotIn("game-engineering", GATE_OWNERS)

    def test_schemas_use_only_supported_keywords(self):
        self.assertEqual(set(VALIDATORS), set(SCHEMA_NAMES))


class X02_GateOwnershipConsistent(unittest.TestCase):
    def test_permitted_owners_match_may_own_gates(self):
        for gate, rules in GATES.items():
            self.assertIn(rules["default_owner"], rules["permitted_owners"], gate)
            declared = {s for s in REGISTRY["skills"] if gate in may_own(s)}
            self.assertEqual(declared, set(rules["permitted_owners"]) - {"HUMAN"}, gate)
        self.assertEqual(may_own("game-director"), [])

    def test_schemas_encode_permitted_owners(self):
        expected = {g: {"enum": d["permitted_owners"]} for g, d in GATES.items()}
        self.assertEqual(find_rules(SCHEMAS["gate"]["allOf"], "owner"), expected)
        self.assertEqual(find_rules(SCHEMAS["task-routing"]["properties"]["required_gates"]["items"]["allOf"], "owner"), expected)

    def test_quality_gates_table_owners(self):
        for row in table_rows(h2_sections(read("core/QUALITY-GATES.md"))[1]["1. Gates"]):
            expected = GATES[row[0].strip("`")]["default_owner"]
            self.assertEqual(row[1].strip("`"), "human" if expected == "HUMAN" else expected, row[0])


def validator_vocabulary():
    """Terms the production validator and the agent adapters emit: diagnostic codes, severities, categories,
    verdicts, tool error codes, readiness-overview states, adapter codes and source kinds. Taken from gpos/
    itself (Phase 2A added this to X03; Phase 2B extended it)."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    from gpos import cli, diagnostics, errors
    from gpos.validation import project
    words = set(diagnostics.CODES) | set(diagnostics.SEVERITY_ORDER) | set(diagnostics.CATEGORIES)
    words |= set(cli.VERDICTS) | set(cli.CLI_ERROR_CODES) | set(project.OVERVIEW_STATES)
    pending = [errors.GposToolError]
    while pending:
        cls = pending.pop()
        words.add(cls.code)
        pending += cls.__subclasses__()
    # Phase 2B: adapter diagnostic codes, result classes and source kinds
    from gpos.adapters import diagnostics as adapter_diagnostics, sources as adapter_sources
    words |= set(adapter_diagnostics.CODES) | set(adapter_diagnostics.EXIT_FOR) | set(adapter_sources.SOURCE_KINDS)
    words |= set(adapter_diagnostics.RUNTIME_STATUSES)
    # Phase 2C-0: tool foundation diagnostic codes, result statuses, lifecycle and probe states
    from gpos.tools import diagnostics as tool_diagnostics, model as tool_model
    words |= set(tool_diagnostics.CODES) | set(tool_diagnostics.STATUSES) | {tool_diagnostics.INFO}
    words |= set(tool_model.ADAPTER_STATES) | set(tool_model.PROBE_STATUSES)
    words |= {"CANONICAL", "DERIVED"}
    # Phase 2C-1: the Git adapter's declared environment names and documented limitation ids
    from gpos.tools.git import adapter as git_adapter
    words |= {name for name, _ in git_adapter.ENVIRONMENT} | set(git_adapter.LIMITATIONS)
    return words


class X03_VocabularyConsistent(unittest.TestCase):
    """Every backticked canonical-looking token in any Markdown file must be a known term."""

    def test_backticked_terms(self):
        upper_vocab = registry_vocabulary() | schema_vocabulary() | validator_vocabulary()
        # Phase 2B: registered adapter ids and the adapter-settings key are lower-case vocabulary too.
        lower_vocab = set(REGISTRY["skills"]) | set(REGISTRY["workflows"]) | set(REGISTRY.get("adapter_ids", [])) \
            | {REGISTRY.get("adapter_extension_key")}
        problems = []
        for path in all_markdown():
            if path.name == "CHANGELOG.md":
                continue  # the changelog records superseded names by design
            for tok in backticked(path.read_text()):
                if re.fullmatch(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*", tok) and len(tok) > 2:
                    if tok not in upper_vocab:
                        problems.append(f"{path.relative_to(ROOT)}: unknown term `{tok}`")
                elif re.fullmatch(r"[a-z]+(-[a-z]+)+", tok) and tok not in lower_vocab:
                    problems.append(f"{path.relative_to(ROOT)}: unknown skill/workflow `{tok}`")
        self.assertEqual(problems, [])


class X04_LinksResolve(unittest.TestCase):
    def test_relative_links_and_anchors(self):
        problems = []
        for path in all_markdown():
            for target in re.findall(r"\]\(([^)\s]+)\)", path.read_text()):
                if re.match(r"^[a-z]+:", target):
                    continue
                file_part, _, anchor = target.partition("#")
                dest = (path.parent / file_part).resolve() if file_part else path
                if not dest.exists():
                    problems.append(f"{path.relative_to(ROOT)} -> {target}: missing")
                elif anchor and dest.suffix == ".md" and anchor not in heading_slugs(dest):
                    problems.append(f"{path.relative_to(ROOT)} -> {target}: missing anchor")
        self.assertEqual(problems, [])


class X05_VersionConsistent(unittest.TestCase):
    def test_version_everywhere(self):
        version = read("VERSION").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
        self.assertEqual(version, VERSION)
        self.assertEqual(REGISTRY["gpos_version"], version)
        self.assertIn(f"[{version}]", read("CHANGELOG.md"))
        for s in REGISTRY["skills"]:
            self.assertEqual(front_matter(skill_text(s))["gpos_version"], version, s)
        for p in list((ROOT / "templates").glob("*.md")) + list((ROOT / "core").glob("*.md")):
            if p.name != "README.md":
                self.assertIn(f"`{version}`", p.read_text(), p.name)


class X06_ExamplesCoherent(unittest.TestCase):
    def test_examples_validate(self):
        for name, path in EXAMPLES.items():
            self.assertEqual(validate(name, json.loads(path.read_text())), [], name)

    def test_gate_example_references_matching_evidence(self):
        gate = load_json("examples/example-gate-record.json")
        ev = load_json("examples/example-evidence-record.json")
        self.assertEqual(gate_evidence_problems(gate, {ev["evidence_id"]: ev}), [])

    def test_routing_example_has_no_cross_record_problems(self):
        self.assertEqual(routing_problems(load_json("examples/example-task-routing.json")), [])

    def test_routing_must_require_registry_evidence(self):
        r = load_json("examples/example-task-routing.json")
        r["required_gates"][0]["required_evidence"] = ["RUNTIME_EVIDENCE"]
        self.assertTrue(routing_problems(r))


class X07_SchemaFixtures(unittest.TestCase):
    def test_fixtures(self):
        files = sorted(list((ROOT / "tests/fixtures/valid").glob("*.json")) + list((ROOT / "tests/fixtures/invalid").glob("*.json")))
        self.assertGreaterEqual(len(files), 60)
        for f in files:
            fx = json.loads(f.read_text())
            errors = validate(fx["schema"], build(fx))
            rel = f.relative_to(ROOT)
            if fx["expect"] == "valid":
                self.assertEqual(errors, [], f"{rel}: {fx['description']}")
            else:
                self.assertTrue(errors, f"{rel} unexpectedly valid: {fx['description']}")
                self.assertIn(fx["expect_keyword"], [e[1] for e in errors], f"{rel}: {errors}")
                self.assertEqual(validate(fx["schema"], load_json(fx["base"])), [], rel)


class X08_PhaseBoundary(unittest.TestCase):
    def test_no_adapter_or_tool_implementation(self):
        self.assertEqual(phase_boundary_problems(), [])

    def test_no_code_outside_tests(self):
        """Phase 2A: code lives only in tests/ and the production package gpos/ (see phase_boundary_problems)."""
        code = [p for p in ROOT.rglob("*") if p.suffix in CODE_SUFFIXES and p.relative_to(ROOT).parts[0] not in CODE_ROOTS
                and ".git" not in p.parts]
        self.assertEqual(code, [])

    def test_project_independent(self):
        forbidden = re.compile(r"inven" + r"igma", re.I)
        # __pycache__ holds untracked, gitignored bytecode, whose constant folding re-joins the split pattern above.
        hits = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
                and forbidden.search(p.read_text(errors="ignore"))]
        self.assertEqual(hits, [])


class X09_TemplatesAreSkeletons(unittest.TestCase):
    def test_templates_hold_no_decisions(self):
        for t in (ROOT / "templates").glob("*.md"):
            if t.name == "README.md":
                continue
            text = t.read_text()
            self.assertTrue(any(f"`{p}`" in text for p in REGISTRY["placeholders"]), t.name)
            self.assertNotIn("| `LOCKED` |", text, f"{t.name} contains a locked decision")

    def test_placeholders_documented(self):
        readme = read("templates/README.md")
        for p in REGISTRY["placeholders"]:
            self.assertIn(f"`{p}`", readme)


class X10_CrossReviewMatchesRoutingTable(unittest.TestCase):
    def test_skill_cross_review_sections(self):
        table = h2_sections(read("core/ROLE-ROUTING.md"))[1]["4. Cross-review"]
        reviewed_by = {row[0].strip("`"): {t for t in backticked(row[1]) if t in REGISTRY["skills"]}
                       for row in table_rows(table)}
        self.assertEqual(set(reviewed_by), set(REGISTRY["skills"]) - {"game-director"})
        for s in reviewed_by:
            section = skill_section(s, "CROSS-REVIEW")
            by_line = re.search(r"\*\*Reviewed by:\*\*(.*)", section).group(1)
            reviews_line = re.search(r"\*\*Reviews:\*\*(.*)", section).group(1)
            self.assertEqual({t for t in backticked(by_line) if t in reviewed_by}, reviewed_by[s], f"{s} reviewed-by")
            expected = {o for o, rs in reviewed_by.items() if s in rs}
            self.assertEqual({t for t in backticked(reviews_line) if t in reviewed_by}, expected, f"{s} reviews")


if __name__ == "__main__":
    print(f"GPOS {REGISTRY['gpos_version']} framework validation"
          f" (jsonschema cross-check: {'on' if jsonschema else 'off — not installed'})")
    unittest.main(verbosity=2)
