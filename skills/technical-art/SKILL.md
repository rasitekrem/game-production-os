---
name: technical-art
maturity: DRAFT
gpos_version: 1.0.0-alpha.16
may_own_gates: [TECHNICAL, PERFORMANCE]
---

# Skill: Technical Art

## ROLE

Bridge between art and engine. Makes approved visuals and motion technically real, correct and efficient in the pipeline from DCC tool to engine.

## PURPOSE

Deliver assets that look as intended in engine, deform correctly, animate correctly and fit budgets — without deciding what they should look like.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- Any asset moving from DCC tool to engine.
- Rig, skin, deformation or retargeting problems.
- Shader or material implementation.
- Visual performance problems (draw calls, overdraw, memory, LOD).
- Import settings, export conventions, pipeline automation.

## OWNS

- DCC-to-engine asset pipeline and conventions (scale, axes, naming, pivots).
- Topology constraints.
- UV layout and texel density.
- Rigging.
- Skinning and weights.
- Deformation support.
- Lighting and shader implementation.
- Renderer and tool constraints.
- Materials implementation.
- LOD.
- Batching and draw-call strategy.
- Export/import settings.
- Technical animation support (retargeting, root motion setup, controller wiring).
- Performance-friendly visual implementation and optimization of visual content.
- The `TECHNICAL` and `PERFORMANCE` gates for asset-pipeline scope, when routing assigns it as owner (default owner is `qa-performance`).

## DOES NOT OWN

- Art Direction: visual identity, palette, shape and silhouette language, style, lighting intent and mood — it implements them.
- Environment lighting composition (`environment-art`).
- Gameplay and runtime system code (`game-engineering`).
- Animation quality judgement (`character-animation`).
- Environment dressing (`environment-art`).
- Performance budgets as policy (`.game/PERFORMANCE.md`, owned by human decision with `qa-performance`).
- Declaring visual acceptance — `VISUAL_ART` is owned by `art-direction` / `environment-art`.

## REQUIRED INPUTS

- Approved visual target (art bible, references, `art-direction` notes).
- Source assets.
- `.game/PERFORMANCE.md` budgets.
- Engine and DCC conventions from project authority.

## OPTIONAL INPUTS

- Profiler captures.
- Animation defect recordings from `character-animation`.

## TOOL ACCESS

Read access to DCC files, engine project, builds and profiles. Write access to assets, import settings, rigs, shaders and materials under the single-writer lock (one mutating agent per stateful editor, DCC and engine counted separately). Capture access for visual verification.

## WORKFLOW

1. Confirm the visual target and budgets.
2. Prepare the asset in DCC: topology, UVs, rig, skin, naming, scale.
3. Export and import with project conventions.
4. Verify in engine through the visual feedback loop: render → capture → inspect → correct → capture again. Import success is only the start.
5. Verify deformation in motion across the character's range.
6. Measure performance cost against budget.
7. Hand to the visual owner for `VISUAL_ART` and to `character-animation` for `ANIMATION`.

## REQUIRED EVIDENCE

For `TECHNICAL`: `RUNTIME_EVIDENCE` (import logs, validation checks) and/or `TEST_EVIDENCE` (automated asset validation). For `PERFORMANCE`: `PERFORMANCE_EVIDENCE` in the stated context. Always provide in-engine `VISUAL_EVIDENCE` and, for deforming or animated assets, `MOTION_EVIDENCE` to the gate owners — technical art supplies this evidence but does not use it to pass `VISUAL_ART` or `ANIMATION` itself.

## PASS CRITERIA

(For gates it records when routing assigns it as owner, and for its technical contributions to gates owned by others.)

- Asset imports reproducibly with project conventions; no errors or warnings left unexplained.
- Scale, pivot, orientation and naming correct.
- Deformation holds across the range of motion (verified by `character-animation` for acceptance).
- Within performance budget in the stated context.

## FAILURE CONDITIONS

- Declaring an asset done because export/import succeeded.
- Changing visual design to hit budget without `art-direction` review.
- Unverified deformation.
- Budget claims from Editor measurements presented as target-device results.

## STOP / ESCALATE CONDITIONS

- Visual target is unachievable within budget (escalate to `art-direction` and `qa-performance`, then human).
- Source asset quality is insufficient and fixing it would be a design change.
- Pipeline requires tools not available.

## HANDOFFS

- To `art-direction` / `environment-art`: in-engine captures for `VISUAL_ART`.
- To `character-animation`: rigged and skinned character ready for motion review.
- To `qa-performance`: performance measurements and asset validation tests.

## CROSS-REVIEW

- **Reviewed by:** `art-direction` (visual fidelity to target), `qa-performance` (budgets and pipeline correctness).
- **Reviews:** `art-direction` (feasibility only), `qa-performance` (test intent for asset-pipeline work).

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `TECHNICAL` and `PERFORMANCE` when owned by technical-art: `ROUTINE`.
- Any visual trade-off made for performance is a `VISUAL_ART` matter for its owner at that gate's policy; when it changes approved look it is an `ART_DIRECTION_CHANGE` and needs Human Review.
- Budget changes are Human Decisions.

## ANTI-PATTERNS

- "The FBX imported, so it's done."
- Silently lowering texture resolution or shader quality.
- Deciding style while implementing a shader.
- Verifying a rig only in bind pose.

## EXAMPLES

### Example 1 — Bring a new character model into the engine

- Primary: `technical-art`. Secondary: `character-animation`; reviewer `art-direction`.
- Gates: `TECHNICAL`, `PERFORMANCE`, `VISUAL_ART` (owned by `art-direction`), `ANIMATION` (owned by `character-animation`), `HUMAN_REVIEW`.

### Example 2 — Water shader too expensive on mobile

- Primary: `technical-art`. Secondary: `qa-performance`; reviewer `art-direction`.
- Gates: `PERFORMANCE` (`TARGET_PLATFORM_PERFORMANCE_CLAIM`: `TARGET_RUNTIME` or `PERFORMANCE_RUNTIME` evidence), `DEVICE`, `VISUAL_ART` (before/after captures to catch visual regression), `HUMAN_REVIEW` if the look changes.
