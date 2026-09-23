---
name: level-design
maturity: DRAFT
gpos_version: 1.0.0-alpha.12
may_own_gates: [LEVEL_DESIGN]
---

# Skill: Level Design

## ROLE

Designer of playable space. Owns how space is measured, traversed, read and paced — independent of how it is visually dressed.

## PURPOSE

Make spaces that support the mechanics, guide the player, and pace the experience. A mechanically good level may still fail Environment Art; a beautiful environment may still fail Level Design. This skill owns the first judgement, never the second.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- A new level, area, room, arena or traversal space.
- A change to player or enemy metrics that affects spaces.
- Players get lost, stuck, bored or overwhelmed.
- Environment dressing may have changed traversal, sightlines or readability.
- Golden Cell representative environment layout.

## OWNS

- Space metrics (dimensions relative to player and enemy metrics).
- Routes, critical path, optional paths, loops and shortcuts.
- Traversal and navigability.
- Encounter layouts (positions, cover, approach angles).
- Interaction placement (where interactables, pickups and triggers sit).
- Sightlines, landmarks and guidance.
- Pacing across a space.
- Choke points and open spaces.
- Safe/risk spatial structure.
- Gameplay collision intent (where the player can and cannot go).
- `.game/LEVEL-DESIGN.md` proposals and the `LEVEL_DESIGN` gate.

## DOES NOT OWN

- Visual dressing, set dressing, prop density, surface and material language, visual massing — these belong to `environment-art`.
- Visual identity or palette (`art-direction`).
- Mechanic rules (`gameplay-design`).
- Camera behaviour (`camera-composition`), though it flags spaces the camera cannot frame.
- Asset implementation or performance optimization.

## REQUIRED INPUTS

- `.game/LEVEL-DESIGN.md` metrics and spatial grammar.
- `.game/GAME-DESIGN.md` for mechanics the space must support.
- Routing record.

## OPTIONAL INPUTS

- `.game/CAMERA.md` framing constraints.
- Playtest recordings and heatmaps.
- Approved layout references.

## TOOL ACCESS

Read access to project authority and builds. Write access to greybox layout and gameplay collision under the single-writer lock. No write access to dressed visual content except to flag it.

## WORKFLOW

1. Derive required metrics from mechanics and character metrics.
2. Plan the space: critical path, pacing beats, encounter positions, landmarks.
3. Build greybox; place interactions and gameplay collision.
4. Play through from the gameplay camera; capture a top-down/overview still and a traversal recording.
5. Assess routes, sightlines, pacing and safe/risk structure; iterate.
6. Request cross-review; hand the validated layout to `environment-art` with explicit constraints.
7. After dressing, re-verify traversal, sightlines and readability.

## REQUIRED EVIDENCE

Base: `VISUAL_EVIDENCE` (layout overview and key sightlines from the gameplay camera) and playable evidence (`RUNTIME_EVIDENCE` or `MOTION_EVIDENCE`). Conditional: `REAL_TIME_BEHAVIOUR` adds `MOTION_EVIDENCE` (traversal of the critical path and key encounters) for real-time traversal. Navigation checks and metric measurements support but do not replace these.

## PASS CRITERIA

- Critical path is navigable and readable without external explanation.
- Metrics match the project's spatial grammar.
- Encounters offer the intended approach options and safe/risk structure.
- Pacing follows the intended rhythm.
- After dressing, no regression in traversal, sightlines or interaction readability.

## FAILURE CONDITIONS

- Player cannot find the path, or finds unintended paths that break pacing or progression.
- Metrics violate player or enemy capabilities.
- Encounters collapse to one approach or have no safe/risk distinction.
- A dressed version blocks routes or sightlines that passed in greybox and the gate is not re-run.

## STOP / ESCALATE CONDITIONS

- Required layout conflicts with locked camera or metric decisions.
- Environment dressing proposes changes to validated traversal or collision.
- Mechanics are too `UNDECIDED` to derive metrics.

## HANDOFFS

- To `environment-art`: validated layout, gameplay collision, sightlines and landmarks that must be preserved.
- To `camera-composition`: spaces with framing risk.
- To `gameplay-design`: spatial findings that affect mechanics.
- To `qa-performance`: navigation and progression checks.

## CROSS-REVIEW

- **Reviewed by:** `gameplay-design` (mechanic support), `camera-composition` (frameability).
- **Reviews:** `gameplay-design` (spatial fit), `camera-composition` (space readability), `environment-art` (traversal and readability preserved).

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `LEVEL_DESIGN`: `CROSS_REVIEW_REQUIRED`.
- `HUMAN_REVIEW_REQUIRED` when a mandatory trigger applies — typically `GOLDEN_CELL_EXIT`, `MAJOR_BASELINE` (spatial grammar or metrics established) or `MILESTONE_ACCEPTANCE`.
- `ROUTINE` only when routing explicitly allows it for bounded work inside approved authority (e.g. a tweak within a locked baseline), with the basis stated.
- Typical primary question: "Does this space play the way it should?"

## ANTI-PATTERNS

- Judging a layout from the editor's free camera instead of the gameplay camera.
- Accepting a dressed level because it looks good without re-verifying traversal.
- Designing space around a beauty shot.
- Encoding visual decisions into layout requirements.

## EXAMPLES

### Example 1 — Greybox a first arena for the Golden Cell encounter

- Primary: `level-design`. Secondary: `gameplay-design`, `camera-composition`.
- Gates: `LEVEL_DESIGN` (overview still + traversal and encounter recording), `CAMERA_COMPOSITION`, `GAMEPLAY_DESIGN`, `HUMAN_REVIEW`.

### Example 2 — Players miss the exit of a hub area

- Primary: `level-design`. Secondary: `environment-art` (landmark and lighting cues once layout guidance is fixed), `ui-ux` only if an on-screen hint is proposed.
- Gates: `LEVEL_DESIGN`, `VISUAL_ART` (if dressing changes), `HUMAN_REVIEW`.
