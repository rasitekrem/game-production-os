# Workflow: Golden Gameplay Cell

Lifecycle: `GOLDEN_CELL`. Policy: [core/GOLDEN-GAMEPLAY-CELL.md](../core/GOLDEN-GAMEPLAY-CELL.md).

## ENTRY CONDITIONS

- Lifecycle stage is `GOLDEN_CELL` by Human Decision.
- Core loop is playable at prototype quality; `GAME-DESIGN.md` core sections `LOCKED`.
- `ART-BIBLE.md` direction decided enough to produce one representative space and character (or the open items are explicitly part of this workflow's decisions).
- Reference target device identified in `PERFORMANCE.md`. If none is available, work may start, but `DEVICE` and `PERFORMANCE` stay `NOT_RUN` and block exit until a device is obtained or a Human Decision changes the requirement.

## PURPOSE

Produce one short, representative slice of play at production quality that proves the game's production direction, and turn what it teaches into locked authority and approved references before content scales.

## STEPS

1. `game-director` proposes the cell scope: player, space, one core interaction, one encounter if the game has combat, required UI, audio scope. Human approves scope.
2. `game-director` routes sub-tasks using the other workflows (`character-production`, `animation-production`, `level-production`, `environment-production`, `ui-production`, `gameplay-feature`) at cell scope.
3. `level-design` builds and validates the cell layout; `environment-art` dresses it.
4. `technical-art` brings the player character in; `character-animation` delivers the representative movement set (idle, start, locomotion, turns, stop, settle, interaction).
5. `camera-composition` tunes the production camera on final art, reopening any greybox-era camera decision if evidence demands it.
6. `game-feel-vfx` and `audio-design` deliver representative feedback.
7. `ui-ux` delivers only the UI the cell needs.
8. `qa-performance` validates correctness, persistence (if applicable), and performance on the reference device.
9. `visual-review` workflow runs across the whole cell.
10. `game-director` assembles the Golden Cell review packet: a short gameplay recording from the target (or representative) device, without narration, plus per-area evidence.
11. Human Review on the primary question; per-area verdicts recorded.
12. Iterate on `CHANGES_REQUIRED` / `FAIL`; repeat 9–11.
13. On `PASS`: register approved captures as approved references; lock revealed decisions in `.game/` documents.

## SPECIALISTS

Primary: `game-director`. All other eleven specialists participate as their areas are in scope.

## REQUIRED GATES

Always required: `TECHNICAL`, `GAMEPLAY_DESIGN`, `VISUAL_ART`, `GAME_FEEL_VFX`, `PERFORMANCE`, `DEVICE`, `HUMAN_REVIEW`.
When affected: `ANIMATION`, `CAMERA_COMPOSITION`, `LEVEL_DESIGN`, `UI_UX`, `AUDIO`.
Accounting: every one of the 12 quality gates is either in `required_gates` or in `omitted_gates` with an explicit reason.

Every gate is on cell scope. Relevant gates are required and blocking; a genuinely irrelevant gate (for example `AUDIO` for a deliberately silent game) is omitted with its reason — never recorded as a fake `PASS` and never silently absent. Trigger `GOLDEN_CELL_EXIT` is mandatory, so every required subjective gate is `HUMAN_REVIEW_REQUIRED`.

Sub-tasks built during the cell (step 2) may use lower policies (`CROSS_REVIEW_REQUIRED`, or `ROUTINE` inside approved authority); the cell exit review is always human.

Routing must explicitly apply or decline `TARGET_PRESENTATION_DIFFERS` for `CAMERA_COMPOSITION`, `GAME_FEEL_VFX` and `UI_UX` when they are required (schema-enforced), and every other condition is accounted for as for any routing.

## REQUIRED EVIDENCE

- `MOTION_EVIDENCE` — the unnarrated gameplay recording; movement, camera and feedback recordings. Captured in `TARGET_RUNTIME` when `TARGET_PRESENTATION_DIFFERS` applies.
- `VISUAL_EVIDENCE` — gameplay-camera captures under production lighting.
- `AUDIO_EVIDENCE` — where audio is in scope.
- `DEVICE_EVIDENCE` and `PERFORMANCE_EVIDENCE` — on the reference target, in `TARGET_RUNTIME` or `PERFORMANCE_RUNTIME` with negligible instrumentation impact.
- `TEST_EVIDENCE` / `RUNTIME_EVIDENCE` — correctness; `PERSISTENCE_EVIDENCE` if the cell saves.
- `HUMAN_EVIDENCE` — the Human Review verdict.

All evidence must match the cell revision under review.

## HUMAN REVIEW POINTS

Mandatory trigger: `GOLDEN_CELL_EXIT`.

1. Step 1: cell scope.
2. Intermediate reviews as triggered (e.g. `CANONICAL_CREATIVE_ASSET` for the player character design, `MAJOR_BASELINE` for camera or locomotion).
3. Step 11: primary question — "Without explanation, does a short representative gameplay recording look and feel like the intended production game?"
4. Step 13: approval of references and locked decisions (`AUTHORITY_CHANGE`).

## EXIT CONDITIONS

Exactly the exit conditions in [GOLDEN-GAMEPLAY-CELL.md §5](../core/GOLDEN-GAMEPLAY-CELL.md#5-exit-conditions). Then `game-director` may recommend the transition to `PRODUCTION`; the transition itself is a Human Decision.

## FORBIDDEN SHORTCUTS

- Passing the cell on technical health (tests, frame rate, saves) while visual or feel gates are not `PASS`.
- Showing the human stills in place of the gameplay recording.
- Narrating or annotating the primary recording before the human answers the primary question.
- Using Editor captures as target-device evidence.
- Starting production-scale content in parallel "to save time".
- Carrying greybox camera or metric decisions forward unexamined.

## POSTMORTEM / LESSON EXTRACTION

Mandatory for this workflow. Answer:

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Also record which decisions the cell revealed that pre-production had missed. Project-specific lessons go to `.game/`; framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
