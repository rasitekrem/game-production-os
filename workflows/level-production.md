# Workflow: Level Production

Lifecycle: `PRE_PRODUCTION` (greybox exploration), `GOLDEN_CELL` (cell space), `PRODUCTION` (at scale only after the Golden Cell exits or is waived).

## ENTRY CONDITIONS

- A space is needed: level, area, room, arena.
- `LEVEL-DESIGN.md` metrics exist or are part of this task's decisions.
- Mechanics the space must support are specified.
- For production-scale level work: Golden Cell exited or waived by Human Decision.

## PURPOSE

Produce a space that plays well — routes, traversal, encounters, sightlines, pacing — validated in greybox before visual dressing, and re-validated after.

## STEPS

1. `game-director` routes: primary `level-design`; secondaries `gameplay-design`, `camera-composition`; `environment-art` downstream.
2. `level-design` derives metrics and plans the critical path, pacing and encounters.
3. Greybox build with gameplay collision and interaction placement.
4. Playthrough from the gameplay camera; overview capture and traversal recording.
5. Iterate on routes, sightlines, pacing, safe/risk structure.
6. Cross-review: `gameplay-design` (mechanic support), `camera-composition` (frameability).
7. Close gates per review policy: cross-review, or Human Review when a trigger applies or routing requires it.
8. Hand to `environment-production` with explicit constraints.
9. After dressing: re-run `LEVEL_DESIGN` on the dressed space.
10. Postmortem.

## SPECIALISTS

Primary: `level-design`. Secondary: `gameplay-design`, `camera-composition`, `qa-performance` (navigation/progression checks). Downstream: `environment-art`. Router: `game-director`.

## REQUIRED GATES

Always required: `LEVEL_DESIGN`, `CAMERA_COMPOSITION`, `TECHNICAL`.
When affected: `GAMEPLAY_DESIGN`, `HUMAN_REVIEW`.

`LEVEL_DESIGN` (default `CROSS_REVIEW_REQUIRED`), `GAMEPLAY_DESIGN` (when mechanics are exercised), `CAMERA_COMPOSITION`, `TECHNICAL` (default `ROUTINE`). `LEVEL_DESIGN` is re-run after dressing.

`HUMAN_REVIEW` when a trigger applies — the first space of a new type establishing spatial grammar (`MAJOR_BASELINE`) or the Golden Cell space — or when routing requires it.

## REQUIRED EVIDENCE

- `VISUAL_EVIDENCE` — overview and key sightlines from the gameplay camera.
- Playable evidence — `RUNTIME_EVIDENCE` or `MOTION_EVIDENCE`; `REAL_TIME_BEHAVIOUR` adds `MOTION_EVIDENCE` (traversal of critical path and encounters).
- `RUNTIME_EVIDENCE` — navigation and metric checks (supplementary).
- `HUMAN_EVIDENCE` where Human Review applies.

## HUMAN REVIEW POINTS

1. Greybox playthrough, when a trigger applies: "Does this space play the way it should?"
2. After dressing, if traversal or readability changed materially.

## EXIT CONDITIONS

- `LEVEL_DESIGN` `PASS` on greybox, and again on the dressed space.
- Constraints handed to `environment-art` in writing.
- Blocking gates `PASS`.

## FORBIDDEN SHORTCUTS

- Dressing before layout passes.
- Judging layout from the editor free camera.
- Accepting a dressed space without re-running `LEVEL_DESIGN`.
- Scaling to many levels before the Golden Cell exits.

## POSTMORTEM / LESSON EXTRACTION

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
