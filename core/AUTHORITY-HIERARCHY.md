# Authority Hierarchy

Status: normative · GPOS `1.0.0-alpha.7` · Elaborates [P7](PRINCIPLES.md#p7--authority-hierarchy)

---

## 1. Order

| Rank | Level | Registry id | Examples |
|---|---|---|---|
| 1 | Human Decision | `HUMAN_DECISION` | A logged decision in `.game/DECISIONS.md`; a Human Review verdict |
| 2 | Project locked authority | `PROJECT_LOCKED_AUTHORITY` | A `.game/` document or section with status `LOCKED` |
| 3 | Approved visual / audio / animation references | `APPROVED_REFERENCES` | Reference boards, target clips, approved Golden Cell captures |
| 4 | Game Production OS | `GAME_PRODUCTION_OS` | This repository |
| 5 | Engine / tool defaults | `ENGINE_TOOL_DEFAULTS` | Default camera FOV, default import settings, default blend times |
| 6 | Implementation | `IMPLEMENTATION` | What the code, scene or asset currently does |
| 7 | Agent recommendation | `AGENT_RECOMMENDATION` | Any agent proposal not yet accepted |

**Rule:** a lower level may not silently override a higher level. "Silently" means without an explicit, recorded Human Decision.

Consequences:

- GPOS is below project authority. A project may legitimately decide something GPOS advises against; GPOS rules then yield for that project, and the decision is logged.
- Engine defaults are not design decisions. "It's the default" never justifies a presentation value.
- Existing implementation is not authority. "That's how it's built" does not make a behaviour correct.
- An agent recommendation is the weakest authority, regardless of how confident or detailed it is.

Exceptions that GPOS does **not** let a project waive: the ban on synthesized Human Review, and the rule that `NOT_RUN` never means `PASS`. These protect the meaning of the hierarchy itself.

## 2. Approved references

A reference becomes level 3 authority only when a Human Decision approves it for a stated purpose ("target run cycle weight", "palette reference for biome A"). Unapproved mood images, agent-generated concepts and downloaded examples are level 7 until approved. An approved reference constrains only the purpose it was approved for.

## 3. Resolving conflicts

1. Identify the level of each conflicting source.
2. The higher level wins. Implement to it.
3. If the higher level appears to produce a bad result, do **not** deviate. Record the evidence and propose a reopen (§4).
4. If two sources at the same level conflict, stop and escalate to a human ([HUMAN-AUTHORITY.md §6](HUMAN-AUTHORITY.md#6-escalation-to-a-human)).
5. Record the resolution in `.game/DECISIONS.md` if a human decided it.

## 4. Reopening locked authority

Locked decisions are not sacred forever; they are sacred until a human reopens them.

Any specialist may propose a reopen when production-quality evidence exposes a problem the original decision could not have seen — for example, a camera distance that passed on greybox but makes final-art characters unreadable. A valid proposal contains:

- the decision being challenged and its record,
- the new evidence (correct type — motion evidence for motion problems),
- why the original evidence could not reveal this,
- one or more concrete alternatives.

Only a Human Decision reopens or replaces the locked item. Until then, the locked item stands, and dependent gates may be recorded `CHANGES_REQUIRED` with a reference to the pending proposal.

## 5. Project-local authority (`.game/`)

Each project using GPOS keeps its authority in a `.game/` directory at the project root. Phase 1 defines the concept and templates only; bootstrapping is a later phase.

| File | Holds |
|---|---|
| `PROJECT.md` | Identity, platforms, engine, quality target, GPOS version, Golden Cell requirement |
| `PILLARS.md` | Design pillars and the player fantasy |
| `GAME-DESIGN.md` | Loops, mechanics, progression, failure semantics |
| `ART-BIBLE.md` | Visual identity, palette, shape and material language, references |
| `GAME-FEEL.md` | Feedback, responsiveness and juice targets |
| `CAMERA.md` | Camera model, framing and follow targets |
| `ANIMATION.md` | Animation style, locomotion set, quality bar |
| `LEVEL-DESIGN.md` | Metrics, spatial grammar, pacing |
| `UI-UX.md` | Information architecture, layout, touch and readability rules |
| `AUDIO.md` | Audio direction, mix priorities |
| `PERFORMANCE.md` | Target devices, budgets, measurement rules |
| `ENGINEERING.md` | Runtime architecture, code conventions, persistence and migration rules, technical designs |
| `DECISIONS.md` | The human-readable Human Decision log (mirrors the decision records, §6) |
| `CURRENT.md` | Current stage, active work, open gates, blockers |

Templates live in `templates/`. `templates/HUMAN-REVIEW.md` is a per-review record, stored by the project wherever its review records live (for example `.game/reviews/`).

Rules for `.game/` documents:

- Each document and, where useful, each section carries an authority status: `PROPOSED` (level 7 until a human approves it) or `LOCKED` (level 2).
- Missing decisions stay visibly missing: `UNDECIDED`, `HUMAN_DECISION_REQUIRED`, `NOT_APPLICABLE` (with reason) or `PROJECT_SPECIFIC`.
- Agents may edit `PROPOSED` sections and `CURRENT.md`. Changing `LOCKED` content requires a Human Decision referenced in the change.
- Project-specific lessons from postmortems go here, not into GPOS.

## 6. Human Decision records

Every Human Decision has a machine-readable record ([`schemas/decision.schema.json`](../schemas/decision.schema.json), example [`examples/example-decision-record.json`](../examples/example-decision-record.json)), stored by the project (for example `.game/decisions/D-0001.json`) and mirrored one-to-one, by id, in `.game/DECISIONS.md`.

Which representation is authoritative:

1. **The human's own words or linked source** (`decision.verbatim` / `decision.source_uri`) are the substance of the decision. No record may state more than they do.
2. **The decision record** is the canonical record of that decision for tooling. Every decision reference in project records (`*_decision_ref`, `decision_ref`, `blocking_downgrade_ref`, lifecycle references) must resolve to an `ACTIVE` decision record of an appropriate kind for the right subject (registry `decision_ref_fields`).
3. **`DECISIONS.md`** is the human-readable view. It must match the records; a mismatch is escalated to a human, never resolved by an agent choosing one side. A human editing `DECISIONS.md` directly is making a new decision, which is then recorded.

**Structured payloads bind decisions to values.** Kinds that authorize a configurable value carry it in machine-readable form (registry `decision_payloads`): `QUALITY_TARGET` the target, `REVIEW_POLICY_OVERRIDE` the gate, policy and optional scope, `EDITOR_CONCURRENCY` the single-writer state, `PRESENTATION_PARITY` `YES`/`NO`, `LIFECYCLE_TRANSITION` from/to stages, `GPOS_UPGRADE` from/to versions. Registry `decision_value_bindings` states which configured value must equal which decision field; a decision of the right kind but a different value does not authorize the configuration. The human's words remain the substance; the structured value must faithfully represent them, and tooling enforces the value, never by parsing prose.

**Every record type is resolved.** Decision references in project config, gate records (`blocking_downgrade_ref`) and routing records (`required_gates[].blocking_downgrade_ref`) are all resolved by the same rules (registry `decision_ref_fields`).

**Decisions are bound to their subject.** Registry `decision_subject_rules` states the required subject per kind: project-level configuration decisions (quality target, presentation parity, editor concurrency, GPOS upgrade, lifecycle transition, review-policy override, Golden Cell waiver) must have subject kind `PROJECT` and ref equal to the project id; a Golden Cell exit decision has subject kind `GOLDEN_CELL` (one-cell model — a Golden Cell set is a documented future consideration). A `BLOCKING_DOWNGRADE` or `KNOWN_ISSUE_ACCEPTANCE` decision names the gate in its value and the gate's scope as its subject (kind, ref and optionally revision): a decision accepting an `ANIMATION` issue cannot downgrade a `UI_UX` gate or another subject's gate.

A reference that merely looks like a decision id is not authority. Resolution, kind, subject and authorization checks are Phase-2 tooling requirements ([GOVERNANCE.md §12](GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation)).

