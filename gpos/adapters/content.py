"""Agent-independent composition: WHAT generated instructions say.

Every statement of GPOS meaning in generated files comes from here, built from the IR. Backends
(claude_code.py, codex.py) supply only FORMAT facts through `AgentFormat`: file locations,
skill invocation syntax, discovery wording. A backend cannot add, drop or reword a rule.

Output is a list of blocks, each tagged with a semantic id. The manifest records which semantic
ids each file carries; `validation.py` checks the rendered text really contains each one's
required markers, without parsing prose back into rules.
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

# ---------------------------------------------------------------- fixed phrases (also validation markers)
DO_NOT_EDIT = "Generated file — do not edit as authority."
SOURCES_WIN = "If this file disagrees with its sources, the sources win: stop and report the mismatch."
REASONING_IS_NOT_VALIDATION = "Agent reasoning is not GPOS validation."
NO_HUMAN_SYNTHESIS = ("Never make, record or imply a Human Decision, never write HUMAN_EVIDENCE for a human, "
                      "never lock project authority and never promote skill maturity.")
DIRECTOR_NOT_REVIEWER = ("game-director routes and coordinates; it never implements for specialists, never acts "
                         "as creative authority and never counts as a discipline-quality cross-reviewer.")
TESTS_NOT_DONE = "Passing tests is not done: each discipline passes or fails its own gate on typed evidence."
PLACEHOLDER_RULE = ("`HUMAN_DECISION_REQUIRED`: only a human decides — surface it, prepare options, never fill it. "
                    "`UNDECIDED`: you may propose a value, marked `PROPOSED`; a proposal is not a decision.")
LOCKED_RULE = ("Project Locked Authority is a `LOCKED` row backed by a Human Decision; it overrides generic GPOS "
               "defaults. `PROPOSED` rows are proposals, not decisions.")


class Block:
    __slots__ = ("semantic", "markdown")

    def __init__(self, semantic, markdown):
        self.semantic, self.markdown = semantic, markdown.rstrip("\n")


class AgentFormat:
    """Format facts a backend supplies. No GPOS meaning."""

    def __init__(self, agent_name, entrypoint, skill_root, invoke, discovery_note, scope_note):
        self.agent_name = agent_name          # e.g. "Claude Code"
        self.entrypoint = entrypoint          # e.g. "CLAUDE.md"
        self.skill_root = skill_root          # e.g. ".claude/skills"
        self.invoke = invoke                  # callable: skill dir name -> invocation text
        self.discovery_note = discovery_note  # how the agent finds skills (verified behaviour)
        self.scope_note = scope_note          # how other instruction files interact with this one


def skill_dir(skill_name):
    return SKILL_PREFIX + skill_name


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def _open_items(doc):
    return [r for r in doc.rows if "HUMAN_DECISION_REQUIRED" in r.placeholders]


def _undecided(doc):
    return [r for r in doc.rows if "UNDECIDED" in r.placeholders]


def _locked(doc):
    return [r for r in doc.rows if r.status == "LOCKED"]


# ---------------------------------------------------------------- root operating contract

def root_blocks(ir, fmt, manifest_path):
    p = ir.project
    blocks = [Block("generated-notice",
                    f"> {DO_NOT_EDIT} Projection of GPOS `{ir.gpos_version}` and this project's `.game/` authority; "
                    f"regenerate with `python3 -m gpos.adapters sync --project .` (manifest `{manifest_path}`). {SOURCES_WIN}")]
    blocks.append(Block("title", f"# Game Production OS — {p['name']} (`{p['id']}`)\n\n"
                                 f"Lifecycle stage `{p['lifecycle_stage']}` · pinned GPOS `{p['gpos_version']}`. "
                                 f"This is AI-assisted professional game production, not generic software work."))
    order = "\n".join(f"{i}. {label}" for i, (_, label) in enumerate(ir.authority_order, 1))
    blocks.append(Block("authority-order",
                        f"## Authority (highest first)\n\n{order}\n\nA lower level never overrides a higher one. You are the lowest "
                        f"level. This file is a generated projection of levels 2 and 4 and is not authority itself."))
    present = [d for d in ir.project_authority if d.present]
    rows = "\n".join(f"| `{d.path}` | `{d.status or 'UNSTATED'}` | {len(_locked(d))} | {len(_open_items(d))} | {len(_undecided(d))} |"
                     for d in present) or "| — | — | 0 | 0 | 0 |"
    missing = [d.path for d in ir.project_authority if not d.present]
    blocks.append(Block("project-authority",
                        f"## Project authority (`.game/`)\n\nProject rules are separate from generic GPOS rules. {LOCKED_RULE} "
                        f"Read the relevant file before working in its domain; specialist skills list its locked rules and open "
                        f"decisions.\n\n| File | Status | Locked rows | Human decisions required | Undecided |\n|---|---|---|---|---|\n{rows}\n\n"
                        f"Not present (no project-specific authority recorded; do not assume one): "
                        f"{', '.join(f'`{m}`' for m in missing) or 'none'}.\n\n{PLACEHOLDER_RULE}"))
    specialists = "\n".join(f"- {fmt.invoke(skill_dir(s.name))} — {s.title}; gates: "
                            f"{', '.join(f'`{g}`' for g in s.may_own_gates) or 'none'}; maturity `{s.maturity}`" for s in ir.skills)
    disabled = (f"\n\nNot enabled in this project: {', '.join(f'`{s}`' for s in ir.disabled_skills)}. If a task needs one of these "
                f"disciplines, stop and ask the human; do not do that discipline's work without its contract.") if ir.disabled_skills else ""
    blocks.append(Block("routing",
                        f"## How work is routed\n\nStart any non-trivial game-production task with "
                        f"{fmt.invoke(skill_dir('game-director'))}: it reads project authority, decomposes the task, selects "
                        f"specialists, names the gates and evidence, and plans Human Review. Load a specialist skill only when "
                        f"the task needs that discipline — by its domain intent, not by keyword. {TESTS_NOT_DONE} "
                        f"{DIRECTOR_NOT_REVIEWER}\n\nSpecialists ({fmt.discovery_note}):\n\n{specialists}{disabled}"))
    triggers = ", ".join(f"`{t}`" for t in ir.human_review["mandatory_triggers"])
    project_triggers = ", ".join(f"`{ir.human_review['project_trigger_prefix']}{t}`" for t in ir.human_review["project_triggers"])
    blocks.append(Block("human-review",
                        f"## Human Review boundaries\n\nMandatory Human Review triggers (cannot be relaxed): {triggers}"
                        f"{'; project triggers: ' + project_triggers if project_triggers else ''}. Creative judgement stays with "
                        f"humans. {NO_HUMAN_SYNTHESIS}"))
    v = ir.validator
    blocks.append(Block("validator",
                        f"## Machine validation\n\n{REASONING_IS_NOT_VALIDATION} GPOS records live in `{v['records']}`; the "
                        f"production validator is the authority on whether they are valid and whether a routed task is ready.\n\n"
                        f"- After changing records, at workflow boundaries: `{v['validate']}` (exit 0 `VALID`, 1 `INVALID`).\n"
                        f"- Before claiming `READY`, merge-ready, release-ready or Golden Cell exited: `{v['readiness']}`. Only exit "
                        f"0 (`READY`) permits the claim; 1 `INVALID`, 2 `NOT_READY`, 3 `INCOMPATIBLE` or tool error.\n"
                        f"- Do not run it for trivial edits that touch no records. The `gpos` package must be importable (the GPOS "
                        f"repository on the Python path); if it is not, say so instead of guessing a result."))
    blocks.append(Block("generated-files",
                        f"## Generated files\n\n`{fmt.entrypoint}`, `{fmt.skill_root}/{SKILL_PREFIX}*/` and `{manifest_path}` are "
                        f"generated and owned by GPOS. Change their sources instead; edits are reported as drift "
                        f"(`python3 -m gpos.adapters check --project .`) and discarded by regeneration. {fmt.scope_note}"))
    return blocks


def root_markers(ir, fmt):
    """semantic id -> strings that must appear (in this order) in any backend's root file."""
    return {
        "generated-notice": [DO_NOT_EDIT, SOURCES_WIN],
        "title": [f"`{ir.project['id']}`", f"pinned GPOS `{ir.project['gpos_version']}`"],
        "authority-order": [label for _, label in ir.authority_order],
        "project-authority": [LOCKED_RULE] + [d.path for d in ir.project_authority if d.present] + [PLACEHOLDER_RULE],
        "routing": [fmt.invoke(skill_dir("game-director")), TESTS_NOT_DONE, DIRECTOR_NOT_REVIEWER]
                   + [fmt.invoke(skill_dir(s.name)) for s in ir.skills],
        "human-review": [f"`{t}`" for t in ir.human_review["mandatory_triggers"]] + [NO_HUMAN_SYNTHESIS],
        "validator": [REASONING_IS_NOT_VALIDATION, ir.validator["validate"], ir.validator["readiness"]],
        "generated-files": [fmt.entrypoint],
    }


# ---------------------------------------------------------------- specialist skills

def _row_line(r):
    ref = f" — decision `{r.decision_ref}`" if r.decision_ref else ""
    verified = "" if r.lock_verified else " — **unverified**: no ACTIVE decision record; treat as `PROPOSED` and ask the human"
    return f"- {r.section} › {r.item}: {r.value}{ref}{verified}"


def project_authority_block(ir, skill):
    docs = [d for d in ir.project_authority if d.file in skill.authority_files]
    parts = ["## Project authority for this discipline\n\nProject-specific rules from `.game/`, distinct from the generic GPOS "
             f"contract below. {LOCKED_RULE}"]
    for d in docs:
        if not d.present:
            parts.append(f"### `{d.path}` — not present\n\nNo project-specific authority is recorded here. Do not assume one; "
                         f"surface the gap as `HUMAN_DECISION_REQUIRED` when the task depends on it.")
            continue
        locked = "\n".join(_row_line(r) for r in _locked(d)) or "- none"
        open_items = "\n".join(f"- {r.section} › {r.item}" for r in _open_items(d)) or "- none"
        undecided = "\n".join(f"- {r.section} › {r.item}" for r in _undecided(d)) or "- none"
        parts.append(f"### `{d.path}` — document status `{d.status or 'UNSTATED'}`\n\nLocked (Project Locked Authority):\n{locked}\n\n"
                     f"Human decisions required (never fill these):\n{open_items}\n\nUndecided (you may propose, marked `PROPOSED`):\n{undecided}")
    if not docs:
        parts.append("This contract names no project authority file.")
    return Block("project-authority", "\n\n".join(parts))


def skill_blocks(ir, skill, fmt, manifest_path):
    blocks = [Block("generated-notice",
                    f"> {DO_NOT_EDIT} Projection of the GPOS `{skill.name}` contract (GPOS `{ir.gpos_version}`) and this project's "
                    f"`.game/` authority (manifest `{manifest_path}`). {SOURCES_WIN}")]
    blocks.append(Block("identity", f"# GPOS specialist: {skill.title} (`{skill.name}`)\n\nMaturity: `{skill.maturity}` — set "
                                    f"by GPOS; generation never changes it. Authority: Human Decision, then Project Locked "
                                    f"Authority, then this generic contract."))
    blocks.append(project_authority_block(ir, skill))
    contract = "\n\n".join(f"### {h}\n\n{text}" for h, text in skill.sections)
    blocks.append(Block("contract", f"## GPOS contract (generic)\n\n{contract}"))
    reviewers = ", ".join(f"`{r}`" for r in skill.cross_reviewers) or "none (no gate of this skill takes specialist cross-review)"
    reviews = ", ".join(f"`{o}`" for o in skill.reviews_for) or "none"
    never = f"\n\n{DIRECTOR_NOT_REVIEWER}" if skill.never_cross_reviewer else ""
    blocks.append(Block("gates-and-reviews",
                        f"## Gates and reviews (registry)\n\n- May own: {', '.join(f'`{g}`' for g in skill.may_own_gates) or 'no gate'}.\n"
                        f"- Eligible cross-reviewers of this skill's gates: {reviewers}.\n- This skill may cross-review gates owned by: "
                        f"{reviews}.\n- Never cross-review your own gate. Only a routed, eligible reviewer's current passing review "
                        f"counts.{never}"))
    if skill.name == "game-director":
        blocks.append(routing_reference_block(ir, fmt))
    v = ir.validator
    blocks.append(Block("validator", f"## Machine validation\n\n{REASONING_IS_NOT_VALIDATION} Records: `{v['records']}`. "
                                     f"Validate records with `{v['validate']}`; before any `READY` claim run `{v['readiness']}` "
                                     f"(only exit 0 is `READY`)."))
    return blocks


def routing_reference_block(ir, fmt):
    rows = "\n".join(
        f"| [{w.name}](references/workflows/{w.name}.md) | {', '.join(f'`{g}`' for g in w.always_required) or '—'} | "
        f"{'yes' if w.account_for_all_gates else 'no'} | {', '.join(f'`{t}`' for t in w.mandatory_triggers) or '—'} |"
        for w in ir.workflows)
    index = "\n".join(f"- {fmt.invoke(skill_dir(s.name))} — gates {', '.join(f'`{g}`' for g in s.may_own_gates) or 'none'}"
                      for s in ir.skills if s.name != "game-director")
    return Block("routing-reference",
                 "## Routing reference (registry)\n\nWorkflow gate requirements. Read a workflow's reference file only when routing "
                 "work into it.\n\n| Workflow | Always-required gates | Account for all 12 gates | Mandatory triggers |\n"
                 f"|---|---|---|---|\n{rows}\n\nEnabled specialists to route to:\n\n{index}")


def skill_markers(ir, skill, fmt):
    markers = {
        "generated-notice": [DO_NOT_EDIT, SOURCES_WIN],
        "identity": [f"`{skill.name}`", f"Maturity: `{skill.maturity}`"],
        "project-authority": [LOCKED_RULE],
        "contract": [text for _, text in skill.sections],
        "gates-and-reviews": [f"`{g}`" for g in skill.may_own_gates] + ([DIRECTOR_NOT_REVIEWER] if skill.never_cross_reviewer else []),
        "validator": [REASONING_IS_NOT_VALIDATION, ir.validator["readiness"]],
    }
    for d in (d for d in ir.project_authority if d.file in skill.authority_files):  # rendered order
        markers["project-authority"].append(f"`{d.path}`")
        markers["project-authority"] += [f"{r.item}: {r.value}" for r in _locked(d)]
        markers["project-authority"] += [f"{r.section} › {r.item}" for r in _open_items(d)]
    if skill.name == "game-director":
        markers["routing-reference"] = [f"references/workflows/{w.name}.md" for w in ir.workflows]
    return markers


def workflow_reference(ir, workflow, manifest_path):
    return (f"> {DO_NOT_EDIT} Projection of GPOS workflow `{workflow.name}` (GPOS `{ir.gpos_version}`, manifest "
            f"`{manifest_path}`). {SOURCES_WIN}\n\n# Workflow: {workflow.title}\n\n{workflow.body.strip()}\n")


def markdown(blocks):
    return "\n\n".join(b.markdown for b in blocks) + "\n"
