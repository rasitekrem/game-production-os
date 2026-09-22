# Workflow: Gameplay Feature

Lifecycle: `PRE_PRODUCTION` (prototype scale), `GOLDEN_CELL` (cell scope), `PRODUCTION`.

## ENTRY CONDITIONS

- A feature request that adds or changes a mechanic, rule, verb, reward or progression element.
- Relevant `GAME-DESIGN.md` sections exist (decided or explicitly `UNDECIDED`).

## PURPOSE

Deliver a gameplay feature that works correctly **and** plays, reads and feels as intended — with every affected discipline judged independently.

## STEPS

1. `game-director` routes: primary `gameplay-design`; secondaries by impact (feedback, UI, animation, audio, level, QA); selects gates.
2. `gameplay-design` specifies the player decision, rules, failure semantics and information needs in `GAME-DESIGN.md` (`PROPOSED`).
3. Missing decisions surfaced as `HUMAN_DECISION_REQUIRED`; blocking ones resolved before irreversible work.
4. `gameplay-design` hands the decided specification to `game-engineering`, which implements it under the editor write lock (via `runtime-system` for architecture or persistence changes). Design-only tuning inside existing systems may be done by `gameplay-design` when routing says so.
5. `qa-performance` adds tests for rules and edge cases; persistence checks if state is saved.
6. Presentation specialists deliver their layers (`game-feel-vfx`, `ui-ux`, `character-animation`, `audio-design`) and record their gates.
7. `gameplay-design` plays the integrated build; records `GAMEPLAY_DESIGN` with motion evidence.
8. Cross-review per [ROLE-ROUTING.md §4](../core/ROLE-ROUTING.md#4-cross-review).
9. Close gates per review policy: cross-review, or Human Review when a trigger applies or routing requires it.
10. Definition of Done check; postmortem.

## SPECIALISTS

Primary: `gameplay-design`. Implementation: `game-engineering`. Typical secondaries: `qa-performance`, `game-feel-vfx`, `ui-ux`, `character-animation`, `audio-design`, `level-design`. Router: `game-director`.

## REQUIRED GATES

Always required: `GAMEPLAY_DESIGN`, `TECHNICAL`.
When affected: `GAME_FEEL_VFX`, `UI_UX`, `ANIMATION`, `AUDIO`, `LEVEL_DESIGN`, `CAMERA_COMPOSITION`, `PERFORMANCE`, `DEVICE`, `HUMAN_REVIEW`.

Always: `GAMEPLAY_DESIGN` (default `CROSS_REVIEW_REQUIRED`), `TECHNICAL` (default `ROUTINE`).
`HUMAN_REVIEW` when a trigger applies (e.g. `MAJOR_BASELINE` for a new core verb) or routing/project authority requires it.

## REQUIRED EVIDENCE

- Playable evidence of the feature: `RUNTIME_EVIDENCE` or `MOTION_EVIDENCE`; `REAL_TIME_BEHAVIOUR` adds `MOTION_EVIDENCE`.
- `TEST_EVIDENCE` for rules; `PERSISTENCE_AFFECTED` adds `PERSISTENCE_EVIDENCE`.
- Per affected presentation gate: its base and applied conditional types ([QUALITY-GATES.md §5](../core/QUALITY-GATES.md#5-evidence-per-gate)).
- `HUMAN_EVIDENCE` where Human Review applies.

## HUMAN REVIEW POINTS

1. Before implementation, if the design depends on a `HUMAN_DECISION_REQUIRED` item.
2. After integration, when a trigger applies or routing requires it: "Playing this, is the choice meaningful and does it feel like the intended game?" Otherwise the feature closes on cross-review.

## EXIT CONDITIONS

- All blocking relevant gates `PASS`; `NOT_APPLICABLE` gates carry reasons.
- `GAME-DESIGN.md` updated; decisions logged; `CURRENT.md` updated.
- Postmortem completed if meaningful.

## FORBIDDEN SHORTCUTS

- "Tests pass, feature done."
- Shipping placeholder feedback or debug UI as final without a recorded limitation and follow-up.
- Tuning gameplay values inside feedback work to make it "feel" better.
- Skipping the play session because the spec was implemented exactly.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
