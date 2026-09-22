# Workflow: Animation Production

Lifecycle: all stages from `PRE_PRODUCTION`.

## ENTRY CONDITIONS

- A rigged, skinned character exists in engine (from `character-production`), or the task corrects existing motion.
- `ANIMATION.md` style and quality bar decided, or explicitly `UNDECIDED` with the task limited to exploration.
- Gameplay movement parameters known.

## PURPOSE

Produce character motion that looks and feels like the intended production game from the gameplay camera — judged in motion, not from numbers or stills.

## STEPS

1. `game-director` routes: primary `character-animation`; secondaries `technical-art`, `camera-composition` (reviewer), `game-feel-vfx` (reviewer).
2. `character-animation` records the baseline from the gameplay camera: idle, start, locomotion, 45°/90°/180° turns (as relevant), stop, settle, interactions.
3. Diagnose from the recording; use numerical diagnostics (foot slide, root velocity, blend weights) to locate causes.
4. Classify defects: clip content, blend/state logic, speed mismatch, rig/skin (→ `technical-art`), framing (→ `camera-composition`), gameplay values (→ `gameplay-design`).
5. Correct; record again under identical conditions; compare before/after.
6. Repeat until the owner's pass criteria are met.
7. Cross-review by `camera-composition` and `game-feel-vfx`.
8. Close gates per review policy: cross-review, or Human Review when a trigger applies or routing requires it. Human Review is always on the recording.
9. Postmortem.

## SPECIALISTS

Primary: `character-animation`. Secondary: `technical-art`. Reviewers: `camera-composition`, `game-feel-vfx`. Consulted: `gameplay-design` if movement values must change. Router: `game-director`.

## REQUIRED GATES

Always required: `ANIMATION`, `TECHNICAL`.
When affected: `CAMERA_COMPOSITION`, `GAME_FEEL_VFX`, `GAMEPLAY_DESIGN`, `PERFORMANCE`, `HUMAN_REVIEW`.

`ANIMATION` (default `CROSS_REVIEW_REQUIRED`), `TECHNICAL` (default `ROUTINE`). When affected: `CAMERA_COMPOSITION`, `GAME_FEEL_VFX`, `GAMEPLAY_DESIGN`, `PERFORMANCE`.

`HUMAN_REVIEW` when establishing or changing the locomotion baseline (`MAJOR_BASELINE`) or when routing requires it. Corrective work inside an approved baseline closes on cross-review.

## REQUIRED EVIDENCE

- `MOTION_EVIDENCE` — mandatory; gameplay-camera recordings at target frame rate covering every changed state and transition, before and after, from the engine or a build. `TARGET_PRESENTATION_DIFFERS` requires `TARGET_RUNTIME` capture.
- `RUNTIME_EVIDENCE` — numerical diagnostics, supplementary only.
- `VISUAL_EVIDENCE` — pose and silhouette stills, supplementary only.
- `HUMAN_EVIDENCE` where Human Review applies.

## HUMAN REVIEW POINTS

1. When a trigger applies (typically `MAJOR_BASELINE`): "Does the character move like the intended production game?" — on the recording, not stills.

## EXIT CONDITIONS

- `ANIMATION` `PASS` backed by current `MOTION_EVIDENCE` for the subject revision, with its review policy satisfied.
- Other affected gates `PASS` or `NOT_APPLICABLE` with reason.
- `ANIMATION.md` updated with any new standards (locked by Human Decision).

## FORBIDDEN SHORTCUTS

- `ANIMATION` `PASS` from numerical diagnostics, screenshots or automated tests.
- Reviewing in the editor scene view instead of the gameplay camera.
- Hiding pops with long blends that add input latency.
- Changing movement speed to fix sliding without `gameplay-design`.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
