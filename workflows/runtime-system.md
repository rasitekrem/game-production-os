# Workflow: Runtime System

Lifecycle: all stages from `PRE_PRODUCTION`.

## ENTRY CONDITIONS

- Runtime software work is needed: a gameplay system implementation, persistence or save work, runtime architecture, engine-side integration, or a runtime refactor.
- The behaviour to implement is specified by its owner (e.g. `GAME-DESIGN.md` for rules), or the task is a behaviour-preserving refactor.
- `ENGINEERING.md` exists (decided or explicitly `UNDECIDED`).

## PURPOSE

Implement or change runtime software so it does exactly what its owners specify, with implementation (`game-engineering`) and verification (`qa-performance`) kept separate, and with every player-facing effect judged by its own discipline.

## STEPS

1. `game-director` routes: primary `game-engineering`; secondary `qa-performance`; the owners of any affected behaviour or presentation as reviewers or secondaries.
2. `game-engineering` confirms the specification; ambiguous behaviour goes back to its owner or becomes `HUMAN_DECISION_REQUIRED`.
3. For architecture, persistence or interface changes, `game-engineering` writes a technical design; locking it in `ENGINEERING.md` is a Human Decision.
4. `game-engineering` implements under the editor write lock and adds behavioural tests.
5. `game-engineering` captures runtime evidence of the behaviour in a running build; persistence changes include a real save → quit → relaunch → load round trip and migration from supported versions.
6. `qa-performance` verifies and records `TECHNICAL` (and `PERFORMANCE` where affected). `game-engineering` never records these gates.
7. Owners of affected player-facing disciplines assess their gates (for a behaviour-preserving refactor, they confirm nothing changed).
8. Definition of Done check; postmortem.

## SPECIALISTS

Primary: `game-engineering`. Secondary: `qa-performance`. As affected: `gameplay-design`, `ui-ux`, `game-feel-vfx`, `audio-design`, `technical-art`. Router: `game-director`.

## REQUIRED GATES

Always required: `TECHNICAL`.
When affected: `PERFORMANCE`, `GAMEPLAY_DESIGN`, `GAME_FEEL_VFX`, `UI_UX`, `ANIMATION`, `AUDIO`, `HUMAN_REVIEW`.

- `TECHNICAL` (owner `qa-performance`, default `ROUTINE`) — always.
- `PERFORMANCE` (owner `qa-performance`) — when runtime cost can change.
- `GAMEPLAY_DESIGN` (owner `gameplay-design`, default `CROSS_REVIEW_REQUIRED`) — when rules or player-facing behaviour are implemented or could change.
- Presentation gates (`GAME_FEEL_VFX`, `UI_UX`, `ANIMATION`, `AUDIO`) — when their behaviour could change; a refactor that must not change feel routes `GAME_FEEL_VFX` with before/after evidence.
- `HUMAN_REVIEW` — when locking or changing runtime architecture or persistence authority (`AUTHORITY_CHANGE`) or when routing requires it.

## REQUIRED EVIDENCE

- `TEST_EVIDENCE` (`AUTOMATED_TEST`) for specified behaviour.
- `RUNTIME_EVIDENCE` from a running build (`EDITOR`, `DIAGNOSTIC_RUNTIME` or `TARGET_RUNTIME`).
- `PERSISTENCE_AFFECTED` adds `PERSISTENCE_EVIDENCE` across a real persistence boundary — never from a DCC render or static analysis.
- `CODE_EVIDENCE` (diff, technical design) as support only.
- `PERFORMANCE_EVIDENCE` with declared instrumentation when performance is in scope.
- Evidence required by any affected player-facing gate.

## HUMAN REVIEW POINTS

1. Locking or changing runtime architecture or persistence rules in `ENGINEERING.md` (`AUTHORITY_CHANGE`).
2. Any change that risks existing players' save data — Human Decision before release.

## EXIT CONDITIONS

- `TECHNICAL` `PASS` recorded by `qa-performance` on the implemented revision; other blocking gates `PASS`.
- Specified behaviour demonstrated in a running build.
- `ENGINEERING.md` updated where decisions were made; decisions logged.

## FORBIDDEN SHORTCUTS

- The implementer recording `TECHNICAL` on its own work.
- Implementing undecided behaviour as if decided.
- "Tests pass" offered as acceptance of design or presentation gates.
- Persistence evidence from in-memory state, a DCC render or static analysis.
- Behaviour-changing refactors routed as `ROUTINE` without the behaviour owner.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
