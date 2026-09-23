---
name: game-feel-vfx
maturity: DRAFT
gpos_version: 1.0.0-alpha.11
may_own_gates: [GAME_FEEL_VFX]
---

# Skill: Game Feel / VFX

## ROLE

Owner of moment-to-moment responsiveness and feedback. Makes actions feel immediate, weighty and readable through timing, effects and presentation layered on authoritative game state.

## PURPOSE

A correct action that feels dead fails the game. This skill makes interaction feel good — without ever lying about what happened.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- New player action, hit, pickup, success, failure or state change needing feedback.
- Reports of floaty, laggy, weak, noisy or confusing feedback.
- Input-to-response latency concerns.
- New VFX or changes to existing effects.
- Golden Cell game feel and representative feedback.

## OWNS

- Anticipation.
- Impact.
- Response (input-to-visible-reaction timing).
- Particles and effect design.
- Flashes, hit-stop and freeze frames.
- Screen and camera shake as feedback (camera comfort reviewed by `camera-composition`).
- Feedback timing and sequencing across visual channels.
- Hit readability.
- Collection feedback.
- Success/failure feedback.
- Moment-to-moment responsiveness.
- `.game/GAME-FEEL.md` proposals and the `GAME_FEEL_VFX` gate.

## DOES NOT OWN

- Gameplay truth: outcomes, damage, timings of rules, state (`gameplay-design`, implementation). Presentation must reflect authoritative game state; it never alters gameplay truth merely to create stronger feedback.
- Character animation quality (`character-animation`), though it syncs to contact frames.
- Camera behaviour outside feedback effects (`camera-composition`).
- Audio assets and mix (`audio-design`), though it coordinates timing with them.
- UI element states, layout and transitions (`ui-ux`).
- Visual identity of effects (`art-direction` reviews style).
- VFX performance budgets — a Human Decision in `.game/PERFORMANCE.md`; `qa-performance` measures, `technical-art` optimizes.

## REQUIRED INPUTS

- `.game/GAME-FEEL.md` and `.game/PILLARS.md`.
- The authoritative game events the feedback represents.
- Playable build at target frame rate.

## OPTIONAL INPUTS

- Animation contact frames from `character-animation`.
- Audio cues from `audio-design`.
- Approved feel references (recordings).

## TOOL ACCESS

Read access to builds, events and authority. Write access to VFX, feedback timing and presentation parameters under the single-writer lock. Capture access for recordings with audio.

## WORKFLOW

1. List the moments: input, anticipation, contact/commit, result, recovery.
2. Map each to the authoritative game event it represents.
3. Implement or tune feedback in layers (motion, VFX, camera); coordinate UI-element feedback with `ui-ux` and sound with `audio-design`.
4. Record at target frame rate with audio; review at full speed and slowed down.
5. Check readability in busy situations and with repeated actions.
6. Iterate; cross-review; prepare Human Review with the recording.

## REQUIRED EVIDENCE

Base: `MOTION_EVIDENCE` at target frame rate from the gameplay camera. Conditional: `FEEDBACK_INCLUDES_SOUND` adds `AUDIO_EVIDENCE`; `TARGET_PRESENTATION_DIFFERS` requires `TARGET_RUNTIME` capture when target input latency or display differs materially. Code inspection, stills and tests never pass game feel.

## PASS CRITERIA

- Input response is immediate within the project's stated latency target.
- Anticipation, impact and recovery are readable at full speed.
- Feedback always matches the authoritative outcome (no false hits, no hidden misses).
- Repeated actions stay readable and do not become noisy or fatiguing.
- Effects fit the visual language and stay within budget.

## FAILURE CONDITIONS

- Feedback shown for an event that did not happen in game state, or missing for one that did.
- Changing gameplay values (timings, hitboxes, damage) to "feel" better without `gameplay-design`.
- Effects that obscure the player, threats or UI.
- Passed from a description or code review.

## STOP / ESCALATE CONDITIONS

- Achieving the feel requires gameplay rule changes (hand to `gameplay-design`).
- Feel problem is rooted in animation or camera (hand to the owner).
- Effects exceed performance budget.
- `.game/GAME-FEEL.md` targets are `UNDECIDED`.

## HANDOFFS

- To `gameplay-design`: rule changes that feel requires.
- To `audio-design`: timing cues for sound.
- To `camera-composition`: shake/impact camera effects for comfort review.
- To `technical-art`: effect implementation and optimization.
- To `qa-performance`: VFX cost measurement.

## CROSS-REVIEW

- **Reviewed by:** `gameplay-design` (truthfulness to game state), `camera-composition` (readability, comfort), `audio-design` (audio-visual sync, when sound is involved), `ui-ux` (when feedback includes UI elements).
- **Reviews:** `gameplay-design` (readability of consequence), `character-animation` (responsiveness), `camera-composition` (responsiveness), `audio-design` (timing).

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `GAME_FEEL_VFX`: `CROSS_REVIEW_REQUIRED`.
- `HUMAN_REVIEW_REQUIRED` when a mandatory trigger applies — typically `MAJOR_BASELINE` (establishing or changing the game-feel baseline) or `GOLDEN_CELL_EXIT`.
- `ROUTINE` only when routing explicitly allows it for bounded work inside approved authority (e.g. a tweak within a locked baseline), with the basis stated. Example: tuning a new pickup's feedback to an approved feedback style.
- Typical primary question, hands-on or on a recording with sound: "Does this feel like the intended game?"

## ANTI-PATTERNS

- More shake, more particles as the answer to every weak feeling.
- Feedback that fires on input rather than on confirmed outcome.
- Tuning in slow motion only.
- "The effect plays, so it passes."

## EXAMPLES

### Example 1 — Collecting an item feels flat

- Primary: `game-feel-vfx`. Secondary: `audio-design`, `ui-ux` (counter update).
- Gates: `GAME_FEEL_VFX` (recording with audio), `AUDIO`, `UI_UX`, `PERFORMANCE`, `HUMAN_REVIEW`.

### Example 2 — Hits don't read in crowded fights

- Primary: `game-feel-vfx`. Secondary: `camera-composition`, `character-animation` (contact frames); reviewer `gameplay-design`.
- Gates: `GAME_FEEL_VFX`, `CAMERA_COMPOSITION`, `ANIMATION` if contact timing changes, `PERFORMANCE`, `HUMAN_REVIEW`.
