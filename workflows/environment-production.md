# Workflow: Environment Production

Lifecycle: `GOLDEN_CELL` (cell environment), `PRODUCTION`. Exploratory mood pieces are allowed in `PRE_PRODUCTION` as proposals.

## ENTRY CONDITIONS

- A layout has `LEVEL_DESIGN` `PASS` on greybox, with written constraints from `level-design`.
- `ART-BIBLE.md` covers the environment type, or its direction is a decision made in this task.
- Environment budgets exist or are recorded as limitations.

## PURPOSE

Turn a validated layout into a production-quality environment that matches the art bible and tells its story visually — without degrading how the space plays.

## STEPS

1. `game-director` routes: primary `environment-art`; secondary `technical-art`; reviewers `art-direction`, `level-design`.
2. `environment-art` reads the constraints: routes, collision, sightlines, landmarks to preserve.
3. Visual massing pass from the gameplay camera.
4. Surfaces, materials, set dressing, storytelling; lighting composition if assigned by project authority.
5. Visual feedback loop: render → capture → inspect → correct → capture again, from the gameplay camera and key viewpoints.
6. `technical-art` handles batching, LOD, material implementation; `qa-performance` measures cost.
7. `level-design` re-runs `LEVEL_DESIGN` on the dressed space.
8. `art-direction` cross-reviews `VISUAL_ART`.
9. Close gates per review policy: cross-review, or Human Review when a trigger applies or routing requires it.
10. Postmortem.

## SPECIALISTS

Primary: `environment-art`. Secondary: `technical-art`, `qa-performance`. Reviewers: `art-direction`, `level-design`. Router: `game-director`.

## REQUIRED GATES

Always required: `VISUAL_ART`, `LEVEL_DESIGN`, `PERFORMANCE`, `TECHNICAL`.
When affected: `DEVICE`, `CAMERA_COMPOSITION`, `HUMAN_REVIEW`.

`VISUAL_ART` (owner `environment-art`, default `CROSS_REVIEW_REQUIRED` with `art-direction`), `LEVEL_DESIGN` (re-run), `PERFORMANCE`, `TECHNICAL`. `CAMERA_COMPOSITION` if dressing or lighting affects framing, occlusion or actor readability.

`HUMAN_REVIEW` when a trigger applies — first environment of a new type (`MAJOR_BASELINE`), `ART_DIRECTION_CHANGE`, `GOLDEN_CELL_EXIT` — or routing requires it. Further spaces built with an approved kit may be `ROUTINE` when routing states that basis.

## REQUIRED EVIDENCE

- `VISUAL_EVIDENCE` — gameplay camera and key viewpoints, production lighting, from the engine or a build; before/after against greybox. `TARGET_PRESENTATION_DIFFERS` requires `TARGET_RUNTIME` capture.
- `MOTION_EVIDENCE` — traversal through the dressed space (for the `LEVEL_DESIGN` re-run).
- `PERFORMANCE_EVIDENCE` — in stated context.
- `HUMAN_EVIDENCE` where Human Review applies.

## HUMAN REVIEW POINTS

1. When a trigger applies, after dressing and re-verification: "Does this space look like the intended game while still playing as validated?"

## EXIT CONDITIONS

- `VISUAL_ART` `PASS` and `LEVEL_DESIGN` `PASS` on the dressed space.
- Performance within budget or trade-off accepted by Human Decision.
- No unrecorded changes to layout or collision.

## FORBIDDEN SHORTCUTS

- Changing validated traversal, gameplay collision or sightlines without handing back to `level-design`.
- Beauty-shot-only review.
- Accepting meshes because they imported.
- Uniform maximum density to "look finished".

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
