---
name: audio-design
maturity: DRAFT
gpos_version: 1.0.0-alpha.13
may_own_gates: [AUDIO]
---

# Skill: Audio Design

## ROLE

Owner of how the game sounds in play: feedback, space, mood and mix.

## PURPOSE

Sound confirms actions, communicates state and builds place. Audio is judged in context, in the mix, on the target output — never from files in isolation.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- New interaction, event or state needing sound.
- New space needing ambience.
- Mix problems: important sounds masked, fatigue, clipping, imbalance.
- Music integration.
- Golden Cell audio.

## OWNS

- Sound effects (SFX) selection and design.
- Ambience.
- Interaction audio.
- Feedback sounds.
- Spatial audio where relevant.
- Mix priority and ducking rules.
- Music integration where relevant.
- `.game/AUDIO.md` proposals and the `AUDIO` gate.

## DOES NOT OWN

- Gameplay events and their timing (`gameplay-design`).
- Visual feedback (`game-feel-vfx`), though it coordinates timing.
- Audio performance budgets — a Human Decision in `.game/PERFORMANCE.md`; `qa-performance` measures against them.
- Music composition direction (a Human Decision; not a Phase-1 specialist domain).

## REQUIRED INPUTS

- `.game/AUDIO.md` direction and mix priorities.
- List of events needing audio and their authoritative triggers.
- Playable build with audio output.

## OPTIONAL INPUTS

- Approved audio references.
- Feedback timings from `game-feel-vfx`.

## TOOL ACCESS

Read access to builds and authority. Write access to audio assets, events and mix settings under the single-writer lock. Capture access for audio and synced audio/video.

## WORKFLOW

1. List events and spaces; set mix priority per sound.
2. Select or design sounds; hook them to authoritative events.
3. Capture gameplay with audio in representative busy and quiet situations.
4. Listen for timing, masking, repetition fatigue, spatial correctness.
5. Iterate; cross-review; prepare Human Review with captures.

## REQUIRED EVIDENCE

Base: `AUDIO_EVIDENCE` captured in gameplay context; synced `MOTION_EVIDENCE` preferred for timing. Conditional: `TARGET_OUTPUT_MATTERS` adds `DEVICE_EVIDENCE` when the target output device (e.g. a phone speaker) materially affects the result. A sound playing without error is never sufficient.

## PASS CRITERIA

- Every important event has audible, correctly timed feedback.
- Priority sounds are never masked in busy situations.
- Repeated sounds do not fatigue.
- Ambience supports place and mood per `.game/AUDIO.md`.
- No clipping; balance holds on target output.

## FAILURE CONDITIONS

- Audio judged from isolated files.
- Sounds triggered on input rather than on confirmed outcome where that misleads.
- Missing evidence treated as `PASS`.

## STOP / ESCALATE CONDITIONS

- Audio direction is `UNDECIDED`.
- Required assets unavailable or licensing unclear.
- Target output device cannot be tested.

## HANDOFFS

- To `game-feel-vfx`: timing alignment.
- To `qa-performance`: audio performance and regression checks.

## CROSS-REVIEW

- **Reviewed by:** `game-feel-vfx` (timing and feedback fit).
- **Reviews:** `game-feel-vfx` (audio-visual sync, when sound is involved).

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `AUDIO`: `CROSS_REVIEW_REQUIRED`.
- `HUMAN_REVIEW_REQUIRED` when a mandatory trigger applies — typically `MAJOR_BASELINE` (audio direction or mix baseline) or `GOLDEN_CELL_EXIT`.
- `ROUTINE` only when routing explicitly allows it for bounded work inside approved authority (e.g. a tweak within a locked baseline), with the basis stated.

Audio may be `NOT_APPLICABLE` for a specific task (with a reason). Missing evidence never automatically becomes `PASS`.

## ANTI-PATTERNS

- Mixing everything loud.
- Reviewing in silence because "audio comes later" while claiming game feel passed.
- One sound for every event of a type with no variation.

## EXAMPLES

### Example 1 — Footsteps on different surfaces

- Primary: `audio-design`. Secondary: `character-animation` (contact timing), `technical-art` (surface tagging).
- Gates: `AUDIO` (captured traversal across surfaces with synced video), `TECHNICAL`, `HUMAN_REVIEW`.

### Example 2 — Success sound for completing a puzzle

- Primary: `audio-design`. Secondary: `game-feel-vfx`.
- Gates: `AUDIO`, `GAME_FEEL_VFX`, `HUMAN_REVIEW`.
