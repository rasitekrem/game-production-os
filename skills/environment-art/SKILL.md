---
name: environment-art
maturity: DRAFT
gpos_version: 1.0.0-alpha.17
may_own_gates: [VISUAL_ART]
---

# Skill: Environment Art

## ROLE

Builder of the world's look. Turns validated layouts into believable, coherent, production-quality spaces without changing how they play.

## PURPOSE

Make spaces look like the intended production game and tell their story visually — while preserving the traversal, collision and readability that `level-design` validated.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- A validated greybox is ready for dressing.
- Environment visual quality or coherence problems.
- New modular kit or environment asset set.
- Golden Cell representative environment visuals.

## OWNS

- Visual massing of environment elements.
- Modular visual language (kit rules as visual rules).
- Surface language.
- Environment material coherence.
- Prop density.
- Set dressing.
- Visual storytelling in the environment.
- Visual landmarks' appearance (not their placement).
- Environment lighting composition and scene-light placement where applicable (GPOS default; project authority may reassign).
- Environmental readability.
- `VISUAL_ART` gate on environment scope when routed as owner.

## DOES NOT OWN

- Validated traversal, routes or gameplay collision (`level-design`) — never changed silently.
- Level Design authority: metrics, encounter layout, sightlines.
- Visual identity, art bible rules, lighting intent and mood (`art-direction`).
- Shader/material implementation, LOD and batching (`technical-art`).
- Performance budgets — a Human Decision in `.game/PERFORMANCE.md`; `qa-performance` measures against them and drafts proposals.

## REQUIRED INPUTS

- Validated layout with `LEVEL_DESIGN` status and constraints from `level-design`.
- `.game/ART-BIBLE.md` and approved references.
- Modular kit and asset library state.

## OPTIONAL INPUTS

- Golden Cell captures as benchmark.
- Lighting authority if assigned by project.

## TOOL ACCESS

Read access to builds and authority. Write access to environment visual content (meshes, props, decals, environment lighting if assigned) under the single-writer lock. No write access to gameplay collision or layout except by handback to `level-design`.

## WORKFLOW

1. Read layout constraints (routes, sightlines, collision, landmarks to preserve).
2. Block visual massing; establish silhouettes and value structure from the gameplay camera.
3. Apply surfaces, materials and dressing; follow visual feedback loop: capture → inspect → correct → capture.
4. Check the gameplay camera view, not only beauty shots.
5. Hand back to `level-design` for traversal/readability re-verification.
6. Cross-review with `art-direction`; prepare Human Review.

## REQUIRED EVIDENCE

For `VISUAL_ART`: `VISUAL_EVIDENCE` from the gameplay camera and key viewpoints under production lighting (engine or build; DCC renders do not count for environment presentation); `TARGET_PRESENTATION_DIFFERS` requires `TARGET_RUNTIME` capture. It also supplies a traversal recording (`MOTION_EVIDENCE`) through the dressed space for the `LEVEL_DESIGN` re-run owned by `level-design`.

## PASS CRITERIA

- Space matches art bible and approved references; coherent with the Golden Cell.
- Readable from the gameplay camera: player, routes and interactables stand out from dressing.
- Density and storytelling support the space's purpose without clutter.
- `level-design` confirms no traversal, collision or sightline regression.

## FAILURE CONDITIONS

- Moving walls, adding blocking props or changing collision without handback.
- Dressing that hides interactables or routes.
- Judged only from a free-camera beauty shot.
- Accepted because meshes imported.

## STOP / ESCALATE CONDITIONS

- The visual target requires layout changes (hand back to `level-design`).
- Density exceeds performance budget (escalate to `technical-art`, `qa-performance`).
- Art bible is `UNDECIDED` for this environment type.

## HANDOFFS

- To `level-design`: dressed space for traversal and readability re-verification; proposed layout changes as requests.
- To `technical-art`: batching, LOD, material implementation needs.
- To `art-direction`: environment for visual review.

## CROSS-REVIEW

- **Reviewed by:** `art-direction` (visual), `level-design` (traversal and readability unchanged), `camera-composition` (focal and actor readability under lighting).
- **Reviews:** `art-direction` (environment feasibility).

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `VISUAL_ART` on environment scope: `CROSS_REVIEW_REQUIRED` (cross-reviewer `art-direction`).
- `HUMAN_REVIEW_REQUIRED` when a mandatory trigger applies — typically `GOLDEN_CELL_EXIT`, `MAJOR_BASELINE` (first environment of a new type) or `ART_DIRECTION_CHANGE`.
- `ROUTINE` only when routing explicitly allows it for bounded work inside approved authority (e.g. a tweak within a locked baseline), with the basis stated. Example: dressing a further space with an already-approved kit.

## ANTI-PATTERNS

- "It looks better now" while the critical path is blocked.
- Building for screenshots instead of the gameplay camera.
- Uniform prop density everywhere.
- Treating a beautiful environment as a good level.

## EXAMPLES

### Example 1 — Dress the Golden Cell arena greybox

- Primary: `environment-art`. Secondary: `technical-art`; reviewers `art-direction`, `level-design`.
- Gates: `VISUAL_ART`, `LEVEL_DESIGN` (re-run after dressing), `PERFORMANCE`, `HUMAN_REVIEW`.

### Example 2 — A corridor feels bland and repetitive

- Primary: `environment-art`. Secondary: `art-direction`.
- Gates: `VISUAL_ART` (before/after gameplay-camera captures), `LEVEL_DESIGN` (confirm no readability regression), `HUMAN_REVIEW`.
