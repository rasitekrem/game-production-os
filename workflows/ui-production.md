# Workflow: UI Production

Lifecycle: all stages from `PRE_PRODUCTION`.

## ENTRY CONDITIONS

- A HUD element, screen, menu, prompt or flow is needed or changed.
- Information requirements are specified by `gameplay-design` (or the UI is non-gameplay, e.g. settings).
- Target platforms, aspect ratios and input methods known from `PROJECT.md`, or recorded as `UNDECIDED` limitations.

## PURPOSE

Deliver UI that players read, understand and operate comfortably on every target — not UI that merely functions.

## STEPS

1. `game-director` routes: primary `ui-ux`; secondaries `art-direction`, `qa-performance`; reviewer `gameplay-design`.
2. `ui-ux` lists what the player must know and do, in priority order.
3. Layout and hierarchy; apply visual language.
4. Implement; verify function (`qa-performance` for `TECHNICAL`).
5. Capture at every target aspect ratio and resolution class.
6. For touch/mobile targets: run on a reference device; check touch targets, reach, safe areas, legibility.
7. Record flows and animated transitions.
8. Cross-review: `art-direction` (visual language), `gameplay-design` (information correctness).
9. Close gates per review policy: cross-review, or Human Review when a trigger applies or routing requires it. Prefer the target runtime.
10. Postmortem.

## SPECIALISTS

Primary: `ui-ux`. Secondary: `art-direction`, `qa-performance`. Reviewer: `gameplay-design`. Consulted: `camera-composition` for HUD/actor overlap. Router: `game-director`.

## REQUIRED GATES

Always required: `UI_UX`, `TECHNICAL`.
When affected: `DEVICE`, `VISUAL_ART`, `PERFORMANCE`, `HUMAN_REVIEW`.

`UI_UX` (default `CROSS_REVIEW_REQUIRED`), `TECHNICAL` (default `ROUTINE`). `DEVICE` when targets include touch or mobile. `VISUAL_ART` when the UI establishes or changes visual language. `PERFORMANCE` for heavy UI.

`HUMAN_REVIEW` for the HUD or information-architecture baseline (`MAJOR_BASELINE`), UI visual-language changes (`ART_DIRECTION_CHANGE`) or when routing requires it.

## REQUIRED EVIDENCE

- `VISUAL_EVIDENCE` at every target aspect ratio.
- `TOUCH_OR_MOBILE_TARGET` adds `DEVICE_EVIDENCE` — mandatory for `UI_UX` `PASS` on those targets, not required otherwise.
- `ANIMATED_PRESENTATION` adds `MOTION_EVIDENCE` for animated or timing-sensitive UI.
- `TARGET_PRESENTATION_DIFFERS` requires `TARGET_RUNTIME` capture.
- `TEST_EVIDENCE` / `RUNTIME_EVIDENCE` for function (never sufficient for `UI_UX`).
- `HUMAN_EVIDENCE` where Human Review applies.

## HUMAN REVIEW POINTS

1. When a trigger applies, preferably on the target runtime: "Can a player read and use this without explanation?"

## EXIT CONDITIONS

- `UI_UX` `PASS` with device evidence where required; other blocking gates `PASS`.
- `UI-UX.md` updated with any new rules (locked by Human Decision).

## FORBIDDEN SHORTCUTS

- "The button works" as UI/UX evidence.
- Desktop-editor captures standing in for a mobile target.
- Debug styling shipped as final.
- Adding explanatory text in place of fixing hierarchy.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
