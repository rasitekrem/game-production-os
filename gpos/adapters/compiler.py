"""Compile canonical GPOS sources plus project authority into the adapter IR.

The compiler is the only place that reads sources. It never invents authority: project values
are copied as written, placeholders stay placeholders, LOCKED rows are marked verified only
when a matching ACTIVE decision record exists, and skill maturity is copied, never changed.
"""

import json
import re
from pathlib import Path

from ..cli import EXIT_FOR
from ..framework import load_framework
from . import sources as src
from .errors import AdapterError
from .model import IR, IR_VERSION, AuthorityDocIR, AuthorityRowIR, SkillIR, WorkflowIR

EXTENSION_KEY = "gpos-adapters"   # project-config `extensions` key for adapter-layer settings (registry adapter_extension_key)
SKILL_PREFIX = "gpos-"            # generated skills are namespaced: gpos-<skill>
DESCRIPTION_MAX = 600

AUTHORITY_LABELS = {  # display labels for registry authority_levels (checked against the registry)
    "HUMAN_DECISION": "Human Decision",
    "PROJECT_LOCKED_AUTHORITY": "Project Locked Authority",
    "APPROVED_REFERENCES": "Approved references",
    "GAME_PRODUCTION_OS": "Game Production OS (GPOS)",
    "ENGINE_TOOL_DEFAULTS": "Engine and tool defaults",
    "IMPLEMENTATION": "Implementation",
    "AGENT_RECOMMENDATION": "Agent recommendation",
}


def project_root_of(path):
    """The project directory (the one holding .game/gpos/project-config.json)."""
    p = Path(path).resolve()
    if not (p / ".game" / "gpos" / "project-config.json").is_file():
        raise AdapterError("PROJECT_LAYOUT", f"{path}: not a GPOS project root (no .game/gpos/project-config.json); "
                                             f"adapters write agent entry files next to .game/ and need the project root")
    return p


def adapter_settings(config, registry):
    ext = (config.get("extensions") or {}).get(EXTENSION_KEY, {})
    if not isinstance(ext, dict):
        raise AdapterError("ADAPTER_CONFIG_INVALID", f"extensions.{EXTENSION_KEY} must be an object")
    unknown_keys = sorted(set(ext) - {"skills"})
    if unknown_keys:
        raise AdapterError("ADAPTER_CONFIG_INVALID", f"extensions.{EXTENSION_KEY}: unknown settings {unknown_keys}")
    skills = ext.get("skills", list(registry["skills"]))
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills) or len(set(skills)) != len(skills):
        raise AdapterError("ADAPTER_CONFIG_INVALID", f"extensions.{EXTENSION_KEY}.skills must be a list of distinct skill ids")
    unknown = sorted(set(skills) - set(registry["skills"]))
    if unknown:
        raise AdapterError("ADAPTER_CONFIG_INVALID", f"extensions.{EXTENSION_KEY}.skills names unknown skills {unknown}")
    if "game-director" not in skills:
        raise AdapterError("ADAPTER_CONFIG_INVALID", "game-director must be enabled: it routes work to every other specialist")
    return [s for s in registry["skills"] if s in skills]  # registry order, deterministic


def _first_paragraph(text):
    return re.split(r"\n\s*\n", text.strip(), 1)[0].replace("\n", " ").strip()


def _description(title, role, gates, maturity):
    owns = f" Owns the {', '.join(gates)} gate{'s' if len(gates) > 1 else ''}." if gates else " Owns no quality gate."
    text = (f"GPOS {title} specialist ({maturity}). {role}{owns} Use when a task needs this discipline's "
            f"ownership, judgement or evidence under Game Production OS; not for work another GPOS specialist owns.")
    if len(text) > DESCRIPTION_MAX:
        raise AdapterError("CONTEXT_BUDGET_EXCEEDED", f"description of {title} is {len(text)} characters (max {DESCRIPTION_MAX})")
    return text


def _skill(fw, name, source, authority_files):
    reg = fw.registry
    fm = src.front_matter(source.text)
    sections = src.h2_sections(source.text)
    order = reg["skill_contract_sections"]
    missing = [s for s in order if s not in sections]
    if missing or fm.get("name") != name:
        raise AdapterError("SOURCE_INVALID", f"{source.id}: contract sections {missing} missing or name mismatch")
    gates = tuple(g.strip() for g in fm.get("may_own_gates", "").strip("[]").split(",") if g.strip())
    title = src.title(source.text).replace("Skill: ", "")
    role = _first_paragraph(sections["ROLE"])
    return SkillIR(
        name=name, title=title, description=_description(title, role, gates, fm["maturity"]), maturity=fm["maturity"],
        may_own_gates=gates,
        sections=tuple((h, _demote(src.delink(sections[h], f"skills/{name}/SKILL.md"))) for h in order),
        authority_files=tuple(f for f in authority_files if f in _named_inputs(sections)),
        cross_reviewers=tuple(reg["cross_review_eligibility"].get(name, [])),
        reviews_for=tuple(o for o, rs in reg["cross_review_eligibility"].items() if name in rs),
        never_cross_reviewer=name in reg["never_cross_reviewer"], source_id=source.id)


def _demote(text):
    """Contract sections render under `###`; their own sub-headings move one level down."""
    return re.sub(r"^(#{3,5}) ", r"#\1 ", text, flags=re.M)


def _named_inputs(sections):
    text = sections.get("REQUIRED INPUTS", "") + "\n" + sections.get("OPTIONAL INPUTS", "")
    return set(re.findall(r"\.game/([A-Z-]+\.md)", text))


def _workflow(fw, name, source):
    reg = fw.registry
    sections = src.h2_sections(source.text)
    wr = reg["workflow_gate_requirements"][name]
    return WorkflowIR(
        name=name, title=src.title(source.text).replace("Workflow: ", ""), purpose=_first_paragraph(sections.get("PURPOSE", "")),
        always_required=tuple(wr["always_required"]), when_affected=tuple(wr.get("when_affected", [])),
        account_for_all_gates=bool(wr.get("account_for_all_gates")),
        mandatory_triggers=tuple(reg["workflow_mandatory_triggers"].get(name, [])),
        body=src.delink(source.text.split("\n", 1)[1] if source.text.startswith("#") else source.text, f"workflows/{name}.md"),
        source_id=source.id)


def _active_decisions(root, used):
    out = {}
    ddir = root / ".game" / "gpos" / "decisions"
    for p in sorted(ddir.glob("*.json")) if ddir.is_dir() else []:
        s = src.read_source(src.PROJECT_AUTHORITY, f"project:.game/gpos/decisions/{p.name}", p)
        used.append(s)
        d = json.loads(s.text)
        if d.get("status") == "ACTIVE":
            out[d.get("decision_id")] = d
    return out


def compile_ir(project, framework=None):
    """Compile the IR for a project root. The project must already be VALID (see pipeline.prepare)."""
    fw = framework or load_framework()
    reg = fw.registry
    if reg.get("adapter_extension_key") != EXTENSION_KEY:
        raise AdapterError("SOURCE_INVALID", "registry adapter_extension_key does not match the adapter layer")
    if set(AUTHORITY_LABELS) != set(reg["authority_levels"]):
        raise AdapterError("SOURCE_INVALID", "registry authority_levels no longer match the adapter labels")
    root = project_root_of(project)
    used = [src.read_source(src.NORMATIVE, "gpos:core/registry.json", fw.root / "core" / "registry.json")]
    cfg_source = src.read_source(src.PROJECT_AUTHORITY, "project:.game/gpos/project-config.json",
                                 root / ".game" / "gpos" / "project-config.json")
    used.append(cfg_source)
    config = json.loads(cfg_source.text)
    enabled = adapter_settings(config, reg)
    decisions = _active_decisions(root, used)

    docs = []
    for f in reg["project_authority_files"]:
        path = root / ".game" / f
        if not path.is_file():
            docs.append(AuthorityDocIR(f, f".game/{f}", False, None, None, (), None))
            continue
        s = src.read_source(src.PROJECT_AUTHORITY, f"project:.game/{f}", path)
        used.append(s)
        parsed = src.parse_authority_document(s.text, reg["placeholders"])
        rows = tuple(AuthorityRowIR(r["section"], r["item"], r["value"], r["status"], r["decision_ref"],
                                    r["status"] == src.LOCKED and r["decision_ref"] in decisions, tuple(r["placeholders"]))
                     for r in parsed["rows"])
        docs.append(AuthorityDocIR(f, f".game/{f}", True, parsed["status"], parsed["locked_by"], rows, s.id))

    skills = []
    for name in enabled:
        s = src.read_source(src.SPECIALIST_SKILL, f"gpos:skills/{name}/SKILL.md", fw.root / "skills" / name / "SKILL.md")
        used.append(s)
        skills.append(_skill(fw, name, s, reg["project_authority_files"]))
    workflows = []
    for name in reg["workflows"]:
        s = src.read_source(src.WORKFLOW, f"gpos:workflows/{name}.md", fw.root / "workflows" / f"{name}.md")
        used.append(s)
        workflows.append(_workflow(fw, name, s))

    hr = config.get("human_review", {})
    project = {
        "id": config["project"]["id"], "name": config["project"]["name"],
        "lifecycle_stage": config.get("lifecycle_stage", "CONCEPT"), "gpos_version": config["gpos_version"],
        "enabled_adapters": list(config.get("enabled_adapters", [])),
        "target_platforms": [dict(t) for t in config["target_platforms"]],
    }
    return IR(
        ir_version=IR_VERSION, gpos_version=fw.version, project=project,
        authority_order=tuple((lvl, AUTHORITY_LABELS[lvl]) for lvl in reg["authority_levels"]),
        human_review={
            "mandatory_triggers": list(reg["mandatory_human_review_triggers"]),
            "project_triggers": sorted(t["id"] for t in hr.get("additional_mandatory_triggers", [])),
            "project_trigger_prefix": reg["project_trigger_prefix"],
            "never_cross_reviewer": list(reg["never_cross_reviewer"]),
            "placeholders": list(reg["placeholders"]),
            "reviewers": sorted(r["id"] for r in hr.get("reviewers", [])),
        },
        gates=tuple((g, tuple(d["permitted_owners"]), bool(d["subjective"])) for g, d in reg["gates"].items()),
        skills=tuple(skills), disabled_skills=tuple(s for s in reg["skills"] if s not in enabled),
        workflows=tuple(workflows), project_authority=tuple(docs),
        validator={
            "validate": "python3 -m gpos.validator validate --project .",
            "readiness": "python3 -m gpos.validator readiness --project . --routing <task_id>",
            "exit_codes": dict(sorted(EXIT_FOR.items())),
            "records": ".game/gpos/",
        },
        sources=tuple(sorted((s.ref() for s in used), key=lambda r: r["id"])),
    )
