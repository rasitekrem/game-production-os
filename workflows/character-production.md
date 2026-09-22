# Workflow: Character Production

Lifecycle: `PRE_PRODUCTION` (single representative character), `GOLDEN_CELL`, `PRODUCTION`.

## ENTRY CONDITIONS

- A character (player, NPC, enemy) is needed in game.
- `ART-BIBLE.md` covers characters, or the character design itself is the decision being made.
- Performance budgets for characters exist or are explicitly `UNDECIDED` (recorded as a limitation).

## PURPOSE

Take a character from design to a production-quality, animated, performant in-game character — with design approved by a human, implementation verified technically, and appearance and motion judged in engine.

## STEPS

1. `game-director` routes: design phase primary `art-direction`; build phase primary `technical-art`; motion via `animation-production`.
2. `art-direction` produces design options (silhouette, shape, palette, material) against the art bible; labelled proposals.
3. Human Review selects or directs the design; the approved design becomes an approved reference.
4. `technical-art` builds or prepares the model: topology, UVs, materials, rig, skin; within budget.
5. Import to engine; visual feedback loop: render → capture → inspect → correct → capture, under production lighting and gameplay camera.
6. `technical-art` verifies deformation across the range of motion (recording).
7. `art-direction` assesses `VISUAL_ART` in engine; cross-review.
8. Hand to `animation-production` for the motion set.
9. `qa-performance` measures cost in context.
10. Human Review of the character in game.
11. Postmortem.

## SPECIALISTS

Design: `art-direction` (primary), `technical-art` (feasibility). Build: `technical-art` (primary), `character-animation`, `qa-performance`. Router: `game-director`.

## REQUIRED GATES

Always required: `VISUAL_ART`, `TECHNICAL`, `PERFORMANCE`.
When affected: `ANIMATION`, `DEVICE`, `HUMAN_REVIEW`.

`ANIMATION` is required when this run delivers motion (usually via its own `animation-production` routing); `DEVICE` when the target differs materially. `HUMAN_REVIEW` is required when a canonical design is created or changed (`CANONICAL_CREATIVE_ASSET`: the `VISUAL_ART` gate is then `HUMAN_REVIEW_REQUIRED`) or another trigger applies; building an already-approved design follows the gates' own policies. Technical gates default to `ROUTINE`.

## REQUIRED EVIDENCE

- `VISUAL_EVIDENCE` from the engine or a build, gameplay camera and close range, production lighting. `DCC_RENDER` captures support design review on `ASSET` scope only.
- `MOTION_EVIDENCE` of deformation and movement.
- `RUNTIME_EVIDENCE` / `TEST_EVIDENCE` for import validity.
- `PERFORMANCE_EVIDENCE` in stated context.
- `HUMAN_EVIDENCE`.

## HUMAN REVIEW POINTS

Conditional trigger: `CANONICAL_CREATIVE_ASSET` applies only when this run creates or changes a canonical character design. Building an already-approved design into the game does not change canon and follows the gates' own review policies.

1. Step 3: design selection — when a canonical design is created or changed (`CANONICAL_CREATIVE_ASSET`).
2. Step 10: in-game character — "Does this character look and move like it belongs in the intended game?"

## EXIT CONDITIONS

- Approved design recorded as reference.
- All blocking gates `PASS` on the character scope.
- Budgets met or trade-offs accepted by Human Decision.

## FORBIDDEN SHORTCUTS

- "The FBX imported, so the character is done."
- Judging appearance from DCC renders only.
- Treating an agent-generated concept as canonical without Human Review.
- Verifying the rig only in bind pose.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
