# Workflow: New Game

Lifecycle: `CONCEPT` → proposes transition to `PRE_PRODUCTION`.

## ENTRY CONDITIONS

- A human has stated the intent to start a game project.
- No `.game/` authority exists yet, or existing authority is being restarted by Human Decision.

## PURPOSE

Establish the minimum project authority that lets every later workflow route, gate and judge work: identity, pillars, platforms, quality target, and the list of decisions still open. This workflow produces authority skeletons and decisions — not content.

## STEPS

1. `game-director` creates `.game/` from `templates/` (manually in Phase 1; no bootstrap tool exists yet). Every field starts as `UNDECIDED` or `HUMAN_DECISION_REQUIRED`.
2. The human states the concept, player fantasy and reference games in their own words; `game-director` records them verbatim in `PROJECT.md` / `PILLARS.md` as `PROPOSED`.
3. `gameplay-design` proposes pillars and core loop options derived from the human's statement.
4. `art-direction` proposes visual direction options with references (labelled proposals, authority level 7).
5. `qa-performance` lists the information needed for `PERFORMANCE.md` (target platforms, reference devices, budgets) and marks it `HUMAN_DECISION_REQUIRED`.
6. `game-director` assembles a decision packet: what must be decided now, what can remain `UNDECIDED` until later, and the consequences of each.
7. Human decides; decisions are logged in `DECISIONS.md` and the decided sections become `LOCKED`.
8. `game-director` records the Golden Gameplay Cell requirement (default: required) and the quality target (default: polished professional indie) unless the human decided otherwise.
9. `game-director` writes `CURRENT.md` and recommends the transition to `PRE_PRODUCTION`.

## SPECIALISTS

Primary: `game-director`. Secondary: `gameplay-design`, `art-direction`, `qa-performance`. Others consulted only if the concept depends on them (e.g. `audio-design` for a music-driven game).

## REQUIRED GATES

Always required: `HUMAN_REVIEW`.
When affected: none.

`HUMAN_REVIEW` is `HUMAN_REVIEW_REQUIRED` with trigger `AUTHORITY_CHANGE` (mandatory): the decision packet locks project identity, pillars, platforms, quality target and the Golden Cell requirement. Discipline gates are listed in `omitted_gates` with the reason that no game content is produced.

## REQUIRED EVIDENCE

- `HUMAN_EVIDENCE` — the logged decisions (`HUMAN_RECORD`).
- Reference material cited in proposals (not gate evidence; it becomes approved reference only by Human Decision).

## HUMAN REVIEW POINTS

Mandatory trigger: `AUTHORITY_CHANGE`.

1. After step 2: confirm the recorded concept matches what the human meant.
2. Step 7: decision packet — every lock is a Human Decision.

## EXIT CONDITIONS

- `PROJECT.md` and `PILLARS.md` exist with decided sections `LOCKED`.
- Target platforms and quality target decided, or explicitly `UNDECIDED` with a stated point by which they must be decided.
- Golden Cell requirement recorded (required, or waived by Human Decision).
- `DECISIONS.md` and `CURRENT.md` exist.
- Human Decision to move to `PRE_PRODUCTION` recorded.

## FORBIDDEN SHORTCUTS

- Filling templates with plausible invented decisions.
- Treating an agent's pillar proposal as locked because the human did not object.
- Starting asset or level production during this workflow.
- Choosing engine, platforms or quality target on the human's behalf.

## POSTMORTEM / LESSON EXTRACTION

Answer briefly and store in the project (e.g. `.game/DECISIONS.md` notes or a postmortem log):

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in project authority. Framework-general lessons are filed as proposed GPOS issues. GPOS is not modified during project work.
