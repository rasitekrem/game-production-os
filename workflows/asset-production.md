# Workflow: Asset Production

Lifecycle: `PRE_PRODUCTION` (representative assets), `GOLDEN_CELL`, `PRODUCTION`.

For characters use `character-production`; this workflow covers props, modular pieces, VFX textures, UI art and other non-character assets.

## ENTRY CONDITIONS

- An asset is required, with its purpose and where it will be seen.
- `ART-BIBLE.md` covers the asset category, or the asset's look is itself the decision being made.
- Budget for the asset category exists or is recorded as a limitation.

## PURPOSE

Produce assets that look right in the game, not just in the DCC tool — coherent with the art bible, correct in the pipeline and within budget.

## STEPS

1. `game-director` routes: primary `technical-art` for pipeline-heavy assets or `art-direction` when the look is undecided; `environment-art` for environment assets.
2. Visual target confirmed (art bible, approved references; options + Human Review if undecided).
3. Build in DCC to project conventions (scale, pivots, naming, topology, UVs).
4. Export and import; resolve all warnings or record why they are acceptable.
5. Visual feedback loop in engine: render → capture → inspect → correct → capture again, from the gameplay camera under production lighting, alongside existing assets.
6. Measure cost against budget.
7. Visual owner records `VISUAL_ART`; `qa-performance` or `technical-art` records `TECHNICAL`/`PERFORMANCE`.
8. Close gates per review policy: cross-review, or Human Review when a trigger applies or routing requires it. Direction-setting assets always go to a human.
9. Postmortem for meaningful asset sets.

## SPECIALISTS

Primary: `technical-art` (or `art-direction` / `environment-art` as routed). Secondary: `art-direction`, `environment-art`, `qa-performance`. Router: `game-director`.

## REQUIRED GATES

Always required: `VISUAL_ART`, `TECHNICAL`, `PERFORMANCE`.
When affected: `DEVICE`, `HUMAN_REVIEW`.

`VISUAL_ART` (default `CROSS_REVIEW_REQUIRED`), `TECHNICAL` and `PERFORMANCE` (default `ROUTINE`). `DEVICE` when the target differs materially.

`VISUAL_ART` may be `ROUTINE` for an asset that matches an approved set and art-bible rules, when routing states that basis. A direction-setting asset is a `CANONICAL_CREATIVE_ASSET` and requires `HUMAN_REVIEW`.

## REQUIRED EVIDENCE

- `VISUAL_EVIDENCE` from the engine or a build, in context with existing assets (`DCC_RENDER` supports asset inspection only).
- `RUNTIME_EVIDENCE` / `TEST_EVIDENCE` for import validity.
- `PERFORMANCE_EVIDENCE` in stated context.
- `ANIMATED_PRESENTATION` adds `MOTION_EVIDENCE` for animated assets or materials.
- `HUMAN_EVIDENCE` where Human Review applies.

## HUMAN REVIEW POINTS

1. Before building, if the look is `UNDECIDED` (a Human Decision).
2. After in-engine verification, for direction-setting assets (`CANONICAL_CREATIVE_ASSET`). Other assets close on cross-review or as routine.

## EXIT CONDITIONS

- Blocking gates `PASS`; asset registered in project conventions.
- Any visual trade-off for performance assessed through `VISUAL_ART` at its review policy; a change to approved look is an `ART_DIRECTION_CHANGE` and needs Human Review.

## FORBIDDEN SHORTCUTS

- "Export succeeded / import succeeded" as completion.
- Evaluating in isolation or in an unlit scene only.
- Lowering quality to meet budget without visual review.
- Batch-producing a set before one representative asset has passed.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
