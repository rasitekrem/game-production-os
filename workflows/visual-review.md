# Workflow: Visual Review

Lifecycle: any stage; mandatory within `golden-gameplay-cell`; recurring in `PRODUCTION` and `POLISH`.

## ENTRY CONDITIONS

- A scope with visual, motion or presentation output is ready for assessment: an asset, a scene, a feature's presentation, a cell, a build.
- The subject version is identified (build, revision).

## PURPOSE

Assess presentation quality as the player sees it — look, motion, camera, feedback, UI — against approved authority and the Golden Cell benchmark, using correct evidence and independent gates. This is the standard procedure behind every subjective gate assessment.

## STEPS

1. `game-director` defines the review scope, the subject version and which presentation gates apply.
2. Capture fresh evidence of the current version:
   - stills from the gameplay camera under production lighting at target aspect ratios,
   - motion recordings at target frame rate for anything that moves or is timed,
   - audio-synced captures where sound matters,
   - device captures where the target display differs materially.
3. Each gate owner inspects its own discipline independently: `art-direction` / `environment-art` (`VISUAL_ART`), `character-animation` (`ANIMATION`), `camera-composition` (`CAMERA_COMPOSITION`), `game-feel-vfx` (`GAME_FEEL_VFX`), `ui-ux` (`UI_UX`), `audio-design` (`AUDIO`).
4. Compare against approved references and, from `PRODUCTION` onward, the Golden Cell captures.
5. Each owner records status with specific findings; `CHANGES_REQUIRED` lists concrete changes.
6. Cross-review per [ROLE-ROUTING.md §4](../core/ROLE-ROUTING.md#4-cross-review).
7. Corrections follow the visual feedback loop: modify → render/play → capture → inspect → correct → capture again.
8. Human Review packet assembled for gates at `HUMAN_REVIEW_REQUIRED`; other gates close on cross-review or as routine.
9. Postmortem if the review exposed systematic issues.

## SPECIALISTS

Router: `game-director`. Gate owners as in step 3. `technical-art` supports capture and diagnoses implementation causes.

## REQUIRED GATES

Always required: none.
When affected: `VISUAL_ART`, `ANIMATION`, `CAMERA_COMPOSITION`, `GAME_FEEL_VFX`, `UI_UX`, `AUDIO`, `HUMAN_REVIEW`.

The gates are scope-dependent: routing selects the presentation gates the reviewed scope affects, each at the policy routing sets (default `CROSS_REVIEW_REQUIRED`); `HUMAN_REVIEW` when a trigger applies or routing requires it.

## REQUIRED EVIDENCE

- `VISUAL_EVIDENCE` — always, from the engine or a build.
- `MOTION_EVIDENCE` — for animation, camera motion claims, feedback, game feel and animated presentation.
- `AUDIO_EVIDENCE` — where sound is in scope.
- `DEVICE_EVIDENCE` — where touch/mobile or target output conditions apply.
- `TARGET_RUNTIME` capture — where `TARGET_PRESENTATION_DIFFERS` applies.
- `HUMAN_EVIDENCE` where Human Review applies.

All evidence must match the revision under review; earlier captures are superseded unless explicitly carried over with justification.

## HUMAN REVIEW POINTS

1. When a trigger applies or routing requires it: primary evidence shown first without explanation, then findings.

## EXIT CONDITIONS

- Every applicable presentation gate has a recorded status backed by current evidence.
- Blocking gates `PASS`, or `CHANGES_REQUIRED` / `FAIL` with concrete follow-up routed.

## FORBIDDEN SHORTCUTS

- Reviewing export or import logs instead of looking at the result.
- Reusing captures from before the latest change.
- One hero screenshot for a motion subject.
- A single reviewer passing all presentation gates at once.
- Debug camera, debug lighting or paused gameplay presented without limitation notes.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
