# Workflow: Release

Lifecycle: `RELEASE_CANDIDATE` → `RELEASED`; reused for post-release patches.

## ENTRY CONDITIONS

- Human Decision to prepare a release candidate.
- Content scope frozen except release fixes.
- Open-gate list for the release scope available.

## PURPOSE

Verify that one specific, reproducible build is safe and ready to ship: every blocking gate on the release scope is `PASS`, known issues are accepted by a human, and player data is safe.

## STEPS

1. `game-director` defines release scope and lists all gates on that scope with current status.
2. `qa-performance` produces the candidate build and records provenance (source revision, settings, asset versions, toolchain).
3. Full regression suite; persistence and migration from every supported previous version.
4. `device-validation` on every primary target platform.
5. `visual-review` on the release build for representative scenes, compared with the Golden Cell and approved references.
6. Any fix after this point produces a new candidate; affected gates return to `NOT_RUN` and are re-run on the new build.
7. `game-director` compiles the release packet: gate table, evidence references, known issues with severity, limitations.
8. Human Decision on known issues and on release.
9. Release; record the released build identity.
10. Postmortem.

## SPECIALISTS

Primary: `qa-performance`. Router and packet: `game-director`. All gate owners re-confirm their gates on the release build.

## REQUIRED GATES

Always required: `TECHNICAL`, `PERFORMANCE`, `DEVICE`, `HUMAN_REVIEW`.
When affected: `GAMEPLAY_DESIGN`, `LEVEL_DESIGN`, `ANIMATION`, `CAMERA_COMPOSITION`, `VISUAL_ART`, `GAME_FEEL_VFX`, `UI_UX`, `AUDIO`.
Accounting: every one of the 12 quality gates is either in `required_gates` or in `omitted_gates` with an explicit reason.

Every presentation and design gate relevant to the release scope is required; one that is genuinely irrelevant (for example `AUDIO` for a deliberately silent game, `ANIMATION` for a game without character animation) is omitted with its reason. No discipline disappears silently, and no fake `PASS` is recorded. Trigger `RELEASE` is mandatory: subjective gates on release scope are `HUMAN_REVIEW_REQUIRED`.

## REQUIRED EVIDENCE

- `TEST_EVIDENCE` on the candidate build.
- `PERSISTENCE_EVIDENCE` — save/load and migration.
- `DEVICE_EVIDENCE` and `PERFORMANCE_EVIDENCE` in `TARGET_RUNTIME` or `PERFORMANCE_RUNTIME` for **every PRIMARY target platform** declared in project config, each on that platform (and on a declared reference device where the platform lists any). One platform's evidence never covers another; SECONDARY platforms do not block release unless project authority promotes them.
- `VISUAL_EVIDENCE` / `MOTION_EVIDENCE` / `AUDIO_EVIDENCE` from the candidate build.
- `HUMAN_EVIDENCE` — release decision and known-issue acceptance.

All evidence must reference the candidate build (`build_id`, `build_revision`).

## HUMAN REVIEW POINTS

Mandatory trigger: `RELEASE`.

1. Step 8: release decision and known-issue acceptance.

## EXIT CONDITIONS

- Every blocking relevant gate `PASS` on the candidate build.
- Known issues explicitly accepted by Human Decision.
- Provenance recorded; released build identified.
- Lifecycle transition recorded by Human Decision.

## FORBIDDEN SHORTCUTS

- Carrying gate results from an earlier build to the candidate.
- Shipping with a blocking gate `NOT_RUN`.
- Treating "no new bugs reported" as evidence.
- An agent accepting known issues on the human's behalf.

## POSTMORTEM / LESSON EXTRACTION

Mandatory for this workflow.

1. Which GPOS rule helped?
2. Which rule was missing, wrong or unclear?
3. Is each lesson project-specific or framework-general?

Project-specific lessons stay in `.game/`. Framework-general lessons become proposed GPOS issues. GPOS is not modified during project work.
