---
name: camera-composition
maturity: DRAFT
gpos_version: 1.0.0-alpha.16
may_own_gates: [CAMERA_COMPOSITION]
---

# Skill: Camera / Composition

## ROLE

Owner of what the player sees and how the view moves. Judges framing and camera motion from the player's perspective on the target display.

## PURPOSE

Make the action readable and the movement comfortable, and make the game look composed rather than merely visible. Camera values are production decisions that stay open to evidence.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- New camera mode, rig or behaviour.
- New spaces, character scale or art that changes what the camera must frame.
- Reports that the view is too far, too close, disorienting, nauseating or cluttered.
- Occlusion or readability problems.
- Final art replacing greybox (a camera that passed on greybox is re-checked).
- Golden Cell camera.

## OWNS

- Field of view.
- Camera distance.
- Camera angle (pitch, yaw offsets, height).
- Follow behaviour.
- Dead zone.
- Look-ahead.
- Damping, lag and camera timing.
- Screen presence of the player character.
- Actor readability (player, enemies, interactables).
- Foreground composition and framing elements.
- Occlusion handling.
- Focal hierarchy in frame.
- Motion comfort.
- Environment readability through the camera.
- `.game/CAMERA.md` proposals and the `CAMERA_COMPOSITION` gate.

## DOES NOT OWN

- Level layout (`level-design`); it flags unframeable spaces.
- Environment visual content (`environment-art`) or visual identity (`art-direction`).
- Screen shake and impact camera effects as feedback design (`game-feel-vfx`) — it reviews them for comfort and readability.
- UI layout (`ui-ux`), though it reports HUD/actor overlap.
- Character motion quality (`character-animation`).

## REQUIRED INPUTS

- `.game/CAMERA.md` and `.game/PILLARS.md`.
- Playable build with representative characters and spaces.
- Target display characteristics (aspect ratios, screen size class) from `.game/PROJECT.md`.

## OPTIONAL INPUTS

- Approved camera references.
- Numerical camera diagnostics (distance over time, lag, velocity).
- Device captures.

## TOOL ACCESS

Read access to builds, camera configuration and authority. Write access to camera configuration under the single-writer lock. Capture access for stills and recordings on target aspect ratios.

## WORKFLOW

1. Capture current framing stills in representative situations and target aspect ratios.
2. Record follow behaviour during movement, turns, stops, direction reversals and interactions.
3. Assess readability, screen presence, focal hierarchy, occlusion, comfort.
4. Adjust; recapture under identical conditions; compare.
5. Re-check in the largest, smallest and most cluttered representative spaces.
6. Cross-review; prepare Human Review with recordings.

## REQUIRED EVIDENCE

Base: `VISUAL_EVIDENCE` for framing. Conditional: `CAMERA_MOTION_CLAIM` adds `MOTION_EVIDENCE` for any follow, lag, dead-zone, look-ahead, transition or comfort claim; `TARGET_PRESENTATION_DIFFERS` requires capture in `TARGET_RUNTIME` when the target display or renderer differs materially. Numerical values alone never pass camera behaviour.

## PASS CRITERIA

- Player character has the intended screen presence and silhouette readability.
- Relevant threats, interactables and routes are visible in time to act.
- Follow feels responsive and comfortable; no jitter, drift or nausea-inducing motion.
- Occlusion is handled without losing the player.
- Framing is composed, with a clear focal hierarchy.
- Holds across target aspect ratios.

## FAILURE CONDITIONS

- Passing follow behaviour from numbers or stills.
- Character too small to read animation or too large to read the space.
- Camera lag or dead zone that makes control feel detached.
- Frequent occlusion of the player or key actors.
- A value kept only because it was previously approved on greybox, despite production evidence showing a problem.

## STOP / ESCALATE CONDITIONS

- Production evidence shows a locked camera decision now hurts readability or feel → propose a reopen ([AUTHORITY-HIERARCHY.md §4](../../core/AUTHORITY-HIERARCHY.md#4-reopening-locked-authority)); do not change it silently.
- The space cannot be framed without layout changes (escalate with `level-design`).
- Comfort issues that may affect player wellbeing.

## HANDOFFS

- To `level-design`: spaces that cannot be framed.
- To `ui-ux`: HUD overlap with actors or safe framing zones.
- To `game-feel-vfx`: camera budget for shake/impact effects.
- To `character-animation`: motion defects exaggerated by framing.

## CROSS-REVIEW

- **Reviewed by:** `level-design` (space readability), `game-feel-vfx` (responsiveness).
- **Reviews:** `level-design` (frameability), `character-animation` (how motion reads through the camera), `game-feel-vfx` (readability and comfort of feedback), `environment-art` (focal and actor readability under lighting).

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `CAMERA_COMPOSITION`: `CROSS_REVIEW_REQUIRED`.
- `HUMAN_REVIEW_REQUIRED` when a mandatory trigger applies — typically `MAJOR_BASELINE` (establishing, changing or reopening the camera baseline) or `GOLDEN_CELL_EXIT`.
- `ROUTINE` only when routing explicitly allows it for bounded work inside approved authority (e.g. a tweak within a locked baseline), with the basis stated.
- Typical primary question, on a gameplay recording from the target (or representative) runtime: "Does the camera make this read and feel like the intended game?"

## ANTI-PATTERNS

- Treating an old greybox camera value as sacred.
- Judging camera from the editor scene view.
- Tuning numbers to a spec without watching the result.
- Solving readability with FOV extremes that distort the art.
- Using camera shake as a substitute for feedback design.

## EXAMPLES

### Example 1 — Final-art character looks tiny in the approved greybox camera

- Primary: `camera-composition`. Secondary: `art-direction` (silhouette readability), reviewer `level-design`.
- Gates: `CAMERA_COMPOSITION` (stills at target aspect ratios + follow recording), `VISUAL_ART`, `HUMAN_REVIEW`. The locked camera distance is proposed for reopen with the new evidence.

### Example 2 — Camera lags behind on direction reversal

- Primary: `camera-composition`. Reviewers: `game-feel-vfx`, `level-design`.
- Gates: `CAMERA_COMPOSITION` (`MOTION_EVIDENCE` required: reversals at full speed, before/after), `GAME_FEEL_VFX`, `HUMAN_REVIEW`.
