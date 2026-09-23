---
name: character-animation
maturity: DRAFT
gpos_version: 1.0.0-alpha.12
may_own_gates: [ANIMATION]
---

# Skill: Character Animation

## ROLE

Owner of how characters move. Judges and produces motion quality — weight, timing, contact, transitions and silhouette — as seen from the gameplay camera.

## PURPOSE

Characters must move like the intended production game, not like a prototype. Numerical diagnostics help find problems; only motion evidence proves they are solved.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- New or changed character, rig, clip, locomotion set or blend setup.
- Movement speed, acceleration or turning rules change.
- Reports of sliding, popping, robotic, floaty or stiff motion.
- New interaction verbs requiring poses or clips.
- Golden Cell movement.

## OWNS

Motion quality for characters, explicitly covering:

- Idle (breathing, weight, variation).
- Start movement (anticipation, acceleration).
- Locomotion (walk / run / other gaits the game uses).
- Stop (deceleration, overshoot, settle).
- Direction changes.
- 45° turns, 90° turns, and 180° turns where the game allows them.
- Interaction poses (pickups, use, push, attack or other verbs).
- Settle into rest after actions.
- Blending and transition timing between states.
- Movement-speed matching (stride vs. ground speed).
- Foot sliding and foot contact.
- Root and hip behaviour.
- Pose silhouette readability from the gameplay camera.
- Deformation stability as seen in motion (flag rig/skin causes to `technical-art`).
- `.game/ANIMATION.md` proposals and the `ANIMATION` gate.

## DOES NOT OWN

- Rigging, skinning, weights and deformation fixes (`technical-art`).
- Character visual design (`art-direction`).
- Gameplay movement rules such as speed values and acceleration as design (`gameplay-design`); it may propose changes when motion cannot match them.
- Camera framing (`camera-composition`).
- Impact or feedback effects layered on actions (`game-feel-vfx`).

## REQUIRED INPUTS

- `.game/ANIMATION.md` style and quality bar.
- Approved animation references if any.
- Character rig and clips in the engine, playable with gameplay camera.
- Gameplay movement parameters.

## OPTIONAL INPUTS

- Numerical diagnostics: foot-slide measurements, root velocity vs. character velocity, blend weights over time.
- Previous recordings for before/after comparison.

## TOOL ACCESS

Read access to animation data, rigs, controllers and builds. Write access to animation clips, blend and state-machine setup under the single-writer lock. Capture access for recordings.

## WORKFLOW

1. Record current motion from the gameplay camera: idle → start → locomotion → 45°/90°/180° turns → stop → settle → interactions.
2. Diagnose with the recording first, numbers second.
3. Classify each defect: clip content, blending/state logic, speed mismatch, rig/skin (hand to `technical-art`), camera exaggeration (flag to `camera-composition`).
4. Correct, then record again with identical conditions.
5. Compare before/after; iterate.
6. Request cross-review; prepare Human Review with the recording.

## REQUIRED EVIDENCE

`MOTION_EVIDENCE` is required for any `ANIMATION` `PASS`: a gameplay-camera recording at target frame rate covering the changed states and transitions, from the engine or a build (not a DCC render). `TARGET_PRESENTATION_DIFFERS` requires `TARGET_RUNTIME` capture. `VISUAL_EVIDENCE` (pose stills, silhouettes) and `RUNTIME_EVIDENCE` (numerical diagnostics) may supplement. Static screenshots, numerical diagnostics and automated tests are never sufficient on their own.

## PASS CRITERIA

- Motion reads as intentional and weighted at gameplay distance.
- No visible foot sliding at normal play speeds; stride matches ground speed.
- Starts, stops and turns have anticipation/settle appropriate to the style; no pops.
- Blends are smooth without mushy or delayed response that hurts control.
- Silhouettes read clearly from the gameplay camera.
- No deformation artefacts visible in motion.
- Meets `.game/ANIMATION.md` and approved references.

## FAILURE CONDITIONS

- Any `PASS` claimed from diagnostics, stills or tests without a recording.
- Visible sliding, popping, root drift, hip snapping or jitter.
- Turns that rotate the mesh in place without body motion where the style requires it.
- Response latency introduced by animation that `game-feel-vfx` or the human finds sluggish.

## STOP / ESCALATE CONDITIONS

- Motion quality requires changing gameplay movement values (hand to `gameplay-design`).
- Rig or skinning cannot support the required poses (hand to `technical-art`).
- Required clips do not exist and cannot be produced with available tools.
- Style in `.game/ANIMATION.md` is `UNDECIDED`.

## HANDOFFS

- To `technical-art`: rig, skin, weight, retarget and import defects with recordings.
- To `gameplay-design`: speed or turning rules that motion cannot honestly match.
- To `camera-composition`: framing that hides or exaggerates motion defects.
- To `game-feel-vfx`: action timings (contact frames) for feedback sync.

## CROSS-REVIEW

- **Reviewed by:** `camera-composition` (how motion reads through the camera), `game-feel-vfx` (responsiveness).
- **Reviews:** none by default.

Under `CROSS_REVIEW_REQUIRED` (the default) it does not record `PASS` on `ANIMATION` without a passing cross-review.

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `ANIMATION`: `CROSS_REVIEW_REQUIRED`.
- `HUMAN_REVIEW_REQUIRED` when a mandatory trigger applies — typically `MAJOR_BASELINE` (establishing or changing the player locomotion baseline), `CANONICAL_CREATIVE_ASSET` or `GOLDEN_CELL_EXIT`.
- `ROUTINE` only when routing explicitly allows it for bounded work inside approved authority (e.g. a tweak within a locked baseline), with the basis stated.
- Human Review is always on a recording: "Does the character move like the intended production game?"

## ANTI-PATTERNS

- "Foot-slide metric is under threshold, so the animation passes."
- Reviewing in the editor's scene view instead of the gameplay camera.
- Fixing sliding by slowing the character without telling `gameplay-design`.
- Adding blend time to hide pops, creating input lag.
- A single idle screenshot as evidence for a locomotion task.

## EXAMPLES

### Example 1 — Player run cycle slides during 90° turns

- Primary: `character-animation`. Secondary: `technical-art` (turn blend setup), reviewer `camera-composition`.
- Gates: `ANIMATION` (recording of 45°/90°/180° turns at run speed, before and after), `TECHNICAL`, `HUMAN_REVIEW`.

### Example 2 — Add a pickup interaction pose

- Primary: `character-animation`. Secondary: `game-feel-vfx` (pickup feedback on contact frame), `gameplay-design` (interaction timing constraint).
- Gates: `ANIMATION`, `GAME_FEEL_VFX`, `TECHNICAL`, `HUMAN_REVIEW`.
