---
name: ui-ux
maturity: DRAFT
gpos_version: 1.0.0-alpha.14
may_own_gates: [UI_UX]
---

# Skill: UI / UX

## ROLE

Owner of how the player reads and operates the game's interface — HUD, menus, prompts, dialogs and on-screen feedback.

## PURPOSE

The player must understand what is happening and what they can do, and must be able to do it comfortably on the target device. A working button is not a passing UI.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- New or changed HUD, menu, screen, prompt, dialog or UI flow.
- New information the player needs (from `gameplay-design`).
- Players misread, miss or mis-tap UI.
- New target platform, aspect ratio, input method or safe-area requirement.
- Golden Cell UI.

## OWNS

- Visual hierarchy.
- Layout.
- Spacing.
- Touch usability (target sizes, reach, gesture conflicts).
- Safe areas and notch/cutout handling.
- Readability (size, contrast, legibility on target display).
- Information architecture and flows.
- Player comprehension.
- Screen density.
- Feedback within UI elements (control states, screen transitions, confirmations).
- `.game/UI-UX.md` proposals and the `UI_UX` gate.

## DOES NOT OWN

- What information the game rules require (`gameplay-design`).
- Visual identity of the game (`art-direction`), though it applies it to UI.
- In-world feedback, VFX and moment-to-moment feel (`game-feel-vfx`); it reviews UI elements used in that feedback.
- UI code implementation (`game-engineering`) and its correctness (`TECHNICAL`, verified by `qa-performance`).
- Localization content.

## REQUIRED INPUTS

- `.game/UI-UX.md`, `.game/ART-BIBLE.md` (visual language).
- Information requirements from `gameplay-design`.
- Target platforms, aspect ratios and input methods from `.game/PROJECT.md`.

## OPTIONAL INPUTS

- Approved UI references.
- Playtest observations and recordings.
- Accessibility requirements declared by the project.

## TOOL ACCESS

Read access to builds and authority. Write access to UI layouts, styles and flows under the single-writer lock. Capture access for target-aspect stills and recordings; device capture via `qa-performance` when needed.

## WORKFLOW

1. List what the player must know and do on this screen, in priority order.
2. Lay out hierarchy; apply visual language.
3. Capture at every target aspect ratio and resolution class.
4. Check readability, density, safe areas, touch targets.
5. Test on target device when touch/mobile is a target.
6. Record flows and transitions if animated or timed.
7. Iterate; cross-review; prepare Human Review.

## REQUIRED EVIDENCE

Base: `VISUAL_EVIDENCE` at each target aspect ratio. Conditional: `TOUCH_OR_MOBILE_TARGET` adds `DEVICE_EVIDENCE` (desktop captures alone never pass touch or mobile UI); `ANIMATED_PRESENTATION` adds `MOTION_EVIDENCE` for animated or timing-sensitive UI; `TARGET_PRESENTATION_DIFFERS` requires `TARGET_RUNTIME` capture. A functioning control (`TEST_EVIDENCE`, `RUNTIME_EVIDENCE`) is never sufficient. Device evidence is not required for targets without touch or mobile.

## PASS CRITERIA

- The most important information is read first.
- Text and icons are legible on the smallest target display.
- Touch targets are comfortably sized and reachable; no safe-area violations.
- A first-time player understands the screen without explanation.
- Density appropriate; no clutter over gameplay-critical areas.
- Consistent with visual language.

## FAILURE CONDITIONS

- "The button works" offered as UI/UX evidence.
- Validated only in the editor at desktop resolution for a mobile game.
- UI covering the player, threats or interaction points during play.
- Information required by gameplay missing or ambiguous.

## STOP / ESCALATE CONDITIONS

- Information requirements are `UNDECIDED`.
- Target devices or aspect ratios are `UNDECIDED`.
- UI cannot fit required information at the smallest target without a design change.
- Device evidence cannot be obtained.

## HANDOFFS

- To `gameplay-design`: information conflicts or overload.
- To `art-direction`: UI visual language questions.
- To `camera-composition`: HUD/actor overlap zones.
- To `qa-performance`: device validation and UI regression tests.

## CROSS-REVIEW

- **Reviewed by:** `art-direction` (visual language), `gameplay-design` (information correctness).
- **Reviews:** `game-feel-vfx` (when feedback includes UI elements).

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `UI_UX`: `CROSS_REVIEW_REQUIRED`.
- `HUMAN_REVIEW_REQUIRED` when a mandatory trigger applies — typically `MAJOR_BASELINE` (HUD or information-architecture baseline), `ART_DIRECTION_CHANGE` (UI visual language) or `GOLDEN_CELL_EXIT`.
- `ROUTINE` only when routing explicitly allows it for bounded work inside approved authority (e.g. a tweak within a locked baseline), with the basis stated.
- Typical primary question, on the target runtime where it differs: "Can a player read and use this without explanation?"

## ANTI-PATTERNS

- Debug-style UI shipped as final.
- Designing at one resolution.
- Solving comprehension with more text.
- Treating a tutorial popup as a fix for unclear UI.

## EXAMPLES

### Example 1 — HUD for health and a resource counter on phones

- Primary: `ui-ux`. Secondary: `art-direction`, `qa-performance` (device).
- Gates: `UI_UX` (`DEVICE_EVIDENCE` on smallest target phone), `VISUAL_ART`, `DEVICE`, `TECHNICAL`, `HUMAN_REVIEW`.

### Example 2 — Pause menu flow is confusing

- Primary: `ui-ux`. Secondary: `qa-performance`.
- Gates: `UI_UX` (flow recording), `TECHNICAL` (+ `PERSISTENCE_EVIDENCE` if settings are saved), `HUMAN_REVIEW`.
