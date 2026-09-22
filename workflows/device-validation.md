# Workflow: Device Validation

Lifecycle: `GOLDEN_CELL` onward; earlier for platform-risk prototypes.

## ENTRY CONDITIONS

- A build exists for a target platform.
- Reference devices and budgets are defined in `PERFORMANCE.md`, or their absence is recorded as a blocker.
- Build provenance (source revision, settings) is known.

## PURPOSE

Establish, on real target hardware, that the game runs correctly, performs within budget and presents correctly — and that nothing was concluded about the target from Editor or emulator evidence alone.

## STEPS

1. `game-director` routes: primary `qa-performance`; secondary `technical-art`; presentation owners as reviewers where display matters.
2. Record build provenance.
3. Install on each reference device in scope; record model and OS version. Evidence counts only on a declared project target platform and, where the platform lists reference devices, on one of them. Richer device-coverage planning (selected-device matrices) is a later-phase concern.
4. Run defined scenarios (e.g. the Golden Cell, heaviest scene, longest session, save/load cycle).
5. Capture `PERFORMANCE_EVIDENCE` (frame time, memory, load time, thermal behaviour over a stated duration) against named budgets.
6. Capture `DEVICE_EVIDENCE`: input, display, safe areas, audio output, interruptions (backgrounding, notifications) as relevant.
7. Capture device `MOTION_EVIDENCE` / `VISUAL_EVIDENCE` for presentation owners where target display differs materially.
8. `qa-performance` records `DEVICE` and `PERFORMANCE`; presentation owners re-check their gates if device evidence reveals issues.
9. If optimization is needed, route it; any optimization affecting look or feel re-opens the affected gates.
10. Postmortem.

## SPECIALISTS

Primary: `qa-performance`. Secondary: `technical-art`. Reviewers as relevant: `ui-ux`, `art-direction`, `game-feel-vfx`, `camera-composition`, `audio-design`. Router: `game-director`.

## REQUIRED GATES

Always required: `DEVICE`, `PERFORMANCE`, `TECHNICAL`.
When affected: `UI_UX`, `VISUAL_ART`, `GAME_FEEL_VFX`, `CAMERA_COMPOSITION`, `AUDIO`.

`DEVICE`, `PERFORMANCE`, `TECHNICAL` (default `ROUTINE`). When device evidence affects presentation: `UI_UX`, `VISUAL_ART`, `GAME_FEEL_VFX`, `CAMERA_COMPOSITION`, `AUDIO` at their routed policies.

## REQUIRED EVIDENCE

- `DEVICE_EVIDENCE` — named physical devices, `TARGET_RUNTIME` or `PERFORMANCE_RUNTIME`.
- `PERFORMANCE_EVIDENCE` — with build, platform, duration and declared instrumentation; timing claims only from `NONE` or `NEGLIGIBLE` impact runs.
- `PERSISTENCE_EVIDENCE` — when saves are in scope.
- `VISUAL_EVIDENCE` / `MOTION_EVIDENCE` / `AUDIO_EVIDENCE` from the target runtime, where presentation is re-checked.

## HUMAN REVIEW POINTS

1. When budgets are missed and a trade-off is proposed (budget change or quality reduction) — Human Decision; a visual trade-off that changes approved look is an `ART_DIRECTION_CHANGE`.
2. When target-runtime presentation differs materially from Editor and a subjective gate is re-assessed at a human policy.

## EXIT CONDITIONS

- `DEVICE` and `PERFORMANCE` `PASS` on every reference device in scope, or failures routed with owners.
- Presentation gates re-checked where device evidence changed the picture.
- Build provenance recorded with the evidence.

## FORBIDDEN SHORTCUTS

- Editor profiling reported as device performance.
- Emulator or simulator results reported as `DEVICE_EVIDENCE`.
- Testing only the lightest scene.
- Quietly reducing visual quality to hit budget.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
