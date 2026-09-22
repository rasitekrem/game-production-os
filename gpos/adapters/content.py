"""Agent-independent composition: a RENDERER from IR facts to neutral wording blocks.

No normative fact originates here. Every rule sentence comes from the IR (`ir.rules`, projected
from registry `agent_operating_contract`, whose statements cite the frozen documents they restate);
authority order, triggers, gates, eligibility, project authority and the validator contract come
from the IR too. This module only decides layout: which block a fact appears in and the neutral
connective wording around it. Backends (backends.py) supply FORMAT facts through `AgentFormat`.

Output is a list of blocks, each tagged with a semantic id. The manifest records which semantic
ids each file carries; validation.py checks each block's required markers (built here from the
same IR facts) really appear in the rendered text, without parsing prose back into rules.
"""

import re

from .compiler import SKILL_PREFIX

# ---------------------------------------------------------------- budgets (characters and lines)
# Tokenizer-independent. Sources: Claude Code recommends CLAUDE.md under 200 lines and SKILL.md
# under 500 lines; Codex reads at most 32 KiB of AGENTS.md; the Agent Skills format recommends a
# skill body under ~5000 tokens (~20000 characters) and descriptions of at most 1024 characters;
# Codex lists skills in at most ~8000 characters. Budgets fail loudly; nothing is truncated.
ROOT_MAX_CHARS, ROOT_MAX_LINES = 8000, 200
SKILL_MAX_CHARS, SKILL_MAX_LINES = 20000, 500
REFERENCE_MAX_CHARS = 12000
DESCRIPTION_MAX_CHARS, DESCRIPTIONS_TOTAL_MAX_CHARS = 600, 8000
MONOLITH_EXCERPT_CHARS = 160   # a root file quoting any contract passage this long is a monolith

# Where each registry rule is rendered in the root. A rule the registry adds later that is not placed
# here still renders (in "Other GPOS rules"): nothing from the registry is ever dropped.
ROOT_PLACEMENT = {
    "GENERATED_FILES_NOT_AUTHORITY": "generated-notice",
    "AUTHORITY_ORDER": "authority-order",
    "PROJECT_LOCKED_AUTHORITY": "project-authority",
    "MISSING_DECISIONS_STAY_MISSING": "project-authority",
    "ROLE_ROUTING": "routing",
    "INDEPENDENT_GATES": "routing",
    "DIRECTOR_ROUTES_ONLY": "routing",
    "HUMAN_AUTHORITY_RESERVED": "human-review",
    "MANDATORY_HUMAN_REVIEW": "human-review",
    "MACHINE_VALIDATION": "validator",
    "UNMANAGED_INSTRUCTION_LAYERS": "generated-files",
}


class Block:
    __slots__ = ("semantic", "markdown")

    def __init__(self, semantic, markdown):
        self.semantic, self.markdown = semantic, markdown.rstrip("\n")


class AgentFormat:
    """Format facts a backend supplies. No GPOS meaning."""

    def __init__(self, agent_name, entrypoint, skill_root, invoke, discovery_note, instruction_layers):
        self.agent_name = agent_name                  # e.g. "Claude Code"
        self.entrypoint = entrypoint                  # e.g. "CLAUDE.md"
        self.skill_root = skill_root                  # e.g. ".claude/skills"
        self.invoke = invoke                          # callable: agent skill id -> invocation text
        self.discovery_note = discovery_note          # how the agent finds skills (documented behaviour)
        self.instruction_layers = instruction_layers  # project instruction files the runtime can also load


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def _rule(ir, rule_id):
    return ir.rule(rule_id).statement


def _placement(rule_id):
    return ROOT_PLACEMENT.get(rule_id, "other-rules")


def _rules_for(ir, placement):
    return " ".join(r.statement for r in ir.rules if _placement(r.id) == placement)


def _open_items(doc):
    return [r for r in doc.rows if "HUMAN_DECISION_REQUIRED" in r.placeholders]


def _undecided(doc):
    return [r for r in doc.rows if "UNDECIDED" in r.placeholders]


def _locked(doc):
    return [r for r in doc.rows if r.status == "LOCKED"]


def _invoke(ir, fmt, skill_name):
    return fmt.invoke(ir.skill(skill_name).agent_id)


# ---------------------------------------------------------------- root operating contract

def root_blocks(ir, fmt, manifest_path):
    p = ir.project
    blocks = [Block("generated-notice",
                    f"> {_rules_for(ir, 'generated-notice')} Source: GPOS `{ir.gpos_version}` and this project's `.game/` "
                    f"authority; regenerate with `python3 -m gpos.adapters sync --project .` (manifest `{manifest_path}`).")]
    blocks.append(Block("title", f"# Game Production OS — {p['name']} (`{p['id']}`)\n\n"
                                 f"Lifecycle stage `{p['lifecycle_stage']}` · pinned GPOS `{p['gpos_version']}`."))
    order = "\n".join(f"{i}. {label}" for i, (_, label) in enumerate(ir.authority_order, 1))
    blocks.append(Block("authority-order", f"## Authority (highest first)\n\n{order}\n\n{_rules_for(ir, 'authority-order')}"))
    present = [d for d in ir.project_authority if d.present]
    rows = "\n".join(f"| `{d.path}` | `{d.status or 'LOG'}` | {len(_locked(d))} | {len(_open_items(d))} | {len(_undecided(d))} |"
                     for d in present) or "| — | — | 0 | 0 | 0 |"
    missing = [d.path for d in ir.project_authority if not d.present]
    blocks.append(Block("project-authority",
                        f"## Project authority (`.game/`)\n\nProject rules, separate from generic GPOS rules. "
                        f"{_rules_for(ir, 'project-authority')} Read the relevant file before working in its domain; specialist "
                        f"skills list its locked rows and open decisions.\n\n| File | Status | Locked rows | Human decisions "
                        f"required | Undecided |\n|---|---|---|---|---|\n{rows}\n\nNot present (no project-specific authority "
                        f"recorded): {', '.join(f'`{m}`' for m in missing) or 'none'}."))
    specialists = "\n".join(f"- {_invoke(ir, fmt, s.name)} — {s.title}; gates: "
                            f"{', '.join(f'`{g}`' for g in s.may_own_gates) or 'none'}; maturity `{s.maturity}`" for s in ir.skills)
    disabled = (f"\n\nNot enabled in this project: {', '.join(f'`{s}`' for s in ir.disabled_skills)}. If a task needs one of these "
                f"disciplines, stop and ask the human.") if ir.disabled_skills else ""
    blocks.append(Block("routing",
                        f"## How work is routed\n\n{_rules_for(ir, 'routing')} Routing starts with the game-director skill, "
                        f"{_invoke(ir, fmt, 'game-director')}.\n\nSpecialists ({fmt.discovery_note}):\n\n{specialists}{disabled}"))
    triggers = ", ".join(f"`{t}`" for t in ir.human_review["mandatory_triggers"])
    project_triggers = ", ".join(f"`{ir.human_review['project_trigger_prefix']}{t}`" for t in ir.human_review["project_triggers"])
    blocks.append(Block("human-review",
                        f"## Human Review boundaries\n\nMandatory Human Review triggers: {triggers}"
                        f"{'; project triggers: ' + project_triggers if project_triggers else ''}. {_rules_for(ir, 'human-review')}"))
    v = ir.validator
    blocks.append(Block("validator",
                        f"## Machine validation\n\n{_rules_for(ir, 'validator')} GPOS records live in `{v['records']}`.\n\n"
                        f"- After changing records, at workflow boundaries: `{v['validate']}` (exit 0 `VALID`, 1 `INVALID`).\n"
                        f"- Before claiming `READY`, merge-ready, release-ready or Golden Cell exited: `{v['readiness']}` (exit 0 "
                        f"`READY`; 1 `INVALID`, 2 `NOT_READY`, 3 `INCOMPATIBLE` or tool error).\n"
                        f"- Trivial edits that touch no records need no validation run. The `gpos` package must be importable (the "
                        f"GPOS repository on the Python path); if it is not, say so instead of guessing a result."))
    layers = ", ".join(f"`{x}`" for x in fmt.instruction_layers)
    blocks.append(Block("generated-files",
                        f"## Generated files\n\n`{fmt.entrypoint}`, `{fmt.skill_root}/{SKILL_PREFIX}{p['skill_namespace']}-*/` "
                        f"and `{manifest_path}` are generated and owned by GPOS; change their sources instead "
                        f"(`python3 -m gpos.adapters check --project .` reports drift). {_rules_for(ir, 'generated-files')} "
                        f"For {fmt.agent_name} these are: {layers}."))
    other = _rules_for(ir, "other-rules")
    if other:
        blocks.append(Block("other-rules", f"## Other GPOS rules\n\n{other}"))
    return blocks


def root_markers(ir, fmt):
    """semantic id -> strings that must appear, in this order, in any backend's root file. Every registry
    rule statement is a marker of the block it is placed in, so no rule can silently disappear."""
    def rules(placement):
        return [r.statement for r in ir.rules if _placement(r.id) == placement]
    markers = {
        "generated-notice": rules("generated-notice"),
        "title": [f"`{ir.project['id']}`", f"pinned GPOS `{ir.project['gpos_version']}`"],
        "authority-order": [label for _, label in ir.authority_order] + rules("authority-order"),
        "project-authority": rules("project-authority") + [d.path for d in ir.project_authority if d.present],
        "routing": rules("routing") + [_invoke(ir, fmt, "game-director")] + [_invoke(ir, fmt, s.name) for s in ir.skills],
        "human-review": [f"`{t}`" for t in ir.human_review["mandatory_triggers"]] + rules("human-review"),
        "validator": rules("validator") + [ir.validator["validate"], ir.validator["readiness"]],
        "generated-files": [fmt.entrypoint] + rules("generated-files") + list(fmt.instruction_layers),
    }
    if rules("other-rules"):
        markers["other-rules"] = rules("other-rules")
    return markers


# ---------------------------------------------------------------- specialist skills

def _row_line(r):
    ref = f" — decision `{r.decision_ref}`" if r.decision_ref else ""
    return f"- {r.section} › {r.item}: {r.value}{ref}"


def project_authority_block(ir, skill):
    docs = [d for d in ir.project_authority if d.file in skill.authority_files]
    parts = [f"## Project authority for this discipline\n\nProject-specific rules from `.game/`, distinct from the generic GPOS "
             f"contract below. {_rule(ir, 'PROJECT_LOCKED_AUTHORITY')} {_rule(ir, 'MISSING_DECISIONS_STAY_MISSING')}"]
    for d in docs:
        if not d.present:
            parts.append(f"### `{d.path}` — not present\n\nNo project-specific authority is recorded here.")
            continue
        if d.status:
            status = f"`{d.status}`" + (f" by decision `{d.locked_by}`" if d.locked_by else "")
        else:
            status = "log (no authority tables)"
        locked = "\n".join(_row_line(r) for r in _locked(d)) or "- none"
        open_items = "\n".join(f"- {r.section} › {r.item}" for r in _open_items(d)) or "- none"
        undecided = "\n".join(f"- {r.section} › {r.item}" for r in _undecided(d)) or "- none"
        parts.append(f"### `{d.path}` — document status {status}\n\nLocked (Project Locked Authority, each bound to its Human "
                     f"Decision):\n{locked}\n\n`HUMAN_DECISION_REQUIRED`:\n{open_items}\n\n`UNDECIDED`:\n{undecided}")
    if not docs:
        parts.append("This contract names no project authority file.")
    return Block("project-authority", "\n\n".join(parts))


def skill_blocks(ir, skill, fmt, manifest_path):
    blocks = [Block("generated-notice",
                    f"> {_rule(ir, 'GENERATED_FILES_NOT_AUTHORITY')} Source: the GPOS `{skill.name}` contract (GPOS "
                    f"`{ir.gpos_version}`) and this project's `.game/` authority (manifest `{manifest_path}`).")]
    blocks.append(Block("identity", f"# GPOS specialist: {skill.title} (`{skill.name}`)\n\nMaturity: `{skill.maturity}` (set by "
                                    f"GPOS). Authority order: {', '.join(label for _, label in ir.authority_order)}."))
    blocks.append(project_authority_block(ir, skill))
    contract = "\n\n".join(f"### {h}\n\n{text}" for h, text in skill.sections)
    blocks.append(Block("contract", f"## GPOS contract (generic)\n\n{contract}"))
    reviewers = ", ".join(f"`{r}`" for r in skill.cross_reviewers) or "none"
    reviews = ", ".join(f"`{o}`" for o in skill.reviews_for) or "none"
    about = " ".join(r.statement for r in ir.rules if r.skill == skill.name)
    blocks.append(Block("gates-and-reviews",
                        f"## Gates and reviews (registry)\n\n- May own: {', '.join(f'`{g}`' for g in skill.may_own_gates) or 'no gate'}.\n"
                        f"- Eligible cross-reviewers of this skill's gates: {reviewers}.\n- This skill may cross-review gates owned by: "
                        f"{reviews}." + (f"\n\n{about}" if about else "")))
    if skill.name == "game-director":
        blocks.append(routing_reference_block(ir, fmt))
    v = ir.validator
    blocks.append(Block("validator", f"## Machine validation\n\n{_rule(ir, 'MACHINE_VALIDATION')} Records: `{v['records']}`. "
                                     f"Validate records with `{v['validate']}`; for readiness run `{v['readiness']}`."))
    return blocks


def routing_reference_block(ir, fmt):
    rows = "\n".join(
        f"| [{w.name}](references/workflows/{w.name}.md) | {', '.join(f'`{g}`' for g in w.always_required) or '—'} | "
        f"{'yes' if w.account_for_all_gates else 'no'} | {', '.join(f'`{t}`' for t in w.mandatory_triggers) or '—'} |"
        for w in ir.workflows)
    index = "\n".join(f"- {_invoke(ir, fmt, s.name)} — gates {', '.join(f'`{g}`' for g in s.may_own_gates) or 'none'}"
                      for s in ir.skills if s.name != "game-director")
    return Block("routing-reference",
                 "## Routing reference (registry)\n\nWorkflow gate requirements. Read a workflow's reference file when routing "
                 "work into it.\n\n| Workflow | Always-required gates | Account for all 12 gates | Mandatory triggers |\n"
                 f"|---|---|---|---|\n{rows}\n\nEnabled specialists:\n\n{index}")


def skill_markers(ir, skill, fmt):
    markers = {
        "generated-notice": [_rule(ir, "GENERATED_FILES_NOT_AUTHORITY")],
        "identity": [f"`{skill.name}`", f"Maturity: `{skill.maturity}`"],
        "project-authority": [_rule(ir, "PROJECT_LOCKED_AUTHORITY"), _rule(ir, "MISSING_DECISIONS_STAY_MISSING")],
        "contract": [text for _, text in skill.sections],
        "gates-and-reviews": [f"`{g}`" for g in skill.may_own_gates] + [r.statement for r in ir.rules if r.skill == skill.name],
        "validator": [_rule(ir, "MACHINE_VALIDATION"), ir.validator["readiness"]],
    }
    for d in (d for d in ir.project_authority if d.file in skill.authority_files):  # rendered order
        markers["project-authority"].append(f"`{d.path}`")
        markers["project-authority"] += [f"{r.item}: {r.value}" for r in _locked(d)]
        markers["project-authority"] += [f"{r.section} › {r.item}" for r in _open_items(d)]
    if skill.name == "game-director":
        markers["routing-reference"] = [f"references/workflows/{w.name}.md" for w in ir.workflows]
    return markers


def workflow_reference(ir, workflow, manifest_path):
    return (f"> {_rule(ir, 'GENERATED_FILES_NOT_AUTHORITY')} Source: GPOS workflow `{workflow.name}` (GPOS `{ir.gpos_version}`, "
            f"manifest `{manifest_path}`).\n\n# Workflow: {workflow.title}\n\n{workflow.body.strip()}\n")


def markdown(blocks):
    return "\n\n".join(b.markdown for b in blocks) + "\n"
