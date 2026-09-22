# Game Production OS

Version `1.0.0-alpha.7` · Phase 1 (core architecture) · all skills `DRAFT`

A model-independent production framework for AI-assisted professional game development. It makes AI agents work like a disciplined, multidisciplinary game studio — with clear creative authority, specialist ownership, independent quality gates and typed evidence — instead of like a generic software agent that calls a feature done once the tests pass.

---

## What it is

- A set of **contracts**: who owns what, who decides what, what must be true before something counts as done.
- A **quality-gate model** where each discipline (animation, camera, visual art, game feel, UI/UX, audio, performance…) passes or fails on its own evidence.
- An **evidence taxonomy** that says which kinds of proof can and cannot support each kind of claim.
- A **production lifecycle** that proves quality in one Golden Gameplay Cell before scaling content.
- A **project-local authority model** (`.game/`) that lets each game override generic guidance through recorded Human Decisions.

## What it is not

- Not a prompt collection, and not a single giant prompt.
- Not an engine integration, plugin or tool. Phase 1 contains no Unity, Blender, Claude or Codex integration.
- Not tied to any one game, engine, genre or model.

Game Production OS does **not** replace Unity, Blender, Claude, Codex, game designers, artists, or human creative direction. It coordinates production responsibility and evidence between them.

## Why it exists

In game production, correct code is mandatory but not sufficient. A feature can compile, pass tests, save correctly and hit its frame rate — and still fail because the animation looks mechanical, the camera feels wrong, the visuals lack production quality, the UI is hard to read, the feedback is weak, or the result simply does not feel like the intended game.

General-purpose agent workflows are tuned for software correctness, and tend to treat "tests are green" as "done". GPOS makes the missing distinctions explicit and enforceable.

## Architecture

```
core/          Constitution and normative rules; registry.json is the canonical vocabulary
skills/        13 specialist contracts (ROLE … EXAMPLES), all DRAFT
workflows/     13 production workflows (ENTRY CONDITIONS … POSTMORTEM)
templates/     Authority skeletons for a project's .game/ directory
schemas/       JSON Schemas for project config, task routing, gate, evidence and Human Decision records
examples/      One minimal valid instance per schema (generic)
adapters/      Phase-2+ boundary (placeholder only)
tools/         Phase-2+ boundary (placeholder only)
tests/         Standard-library framework validator and fixtures
```

| Core document | Defines |
|---|---|
| [PRINCIPLES.md](core/PRINCIPLES.md) | The eleven principles; everything else elaborates these |
| [HUMAN-AUTHORITY.md](core/HUMAN-AUTHORITY.md) | What humans own, what agents may/may not do, the Human Review protocol |
| [AUTHORITY-HIERARCHY.md](core/AUTHORITY-HIERARCHY.md) | The seven authority levels, conflicts, reopening, `.game/` |
| [QUALITY-GATES.md](core/QUALITY-GATES.md) | 12 gates, five statuses, base and conditional evidence, proportional review policy, mandatory Human Review triggers |
| [EVIDENCE-RULES.md](core/EVIDENCE-RULES.md) | Ten evidence types, capture contexts, provenance, staleness and supersession, instrumentation |
| [ROLE-ROUTING.md](core/ROLE-ROUTING.md) | Specialists, routing procedure and table, cross-review, default visual responsibility, handoffs, concurrency |
| [PRODUCTION-LIFECYCLE.md](core/PRODUCTION-LIFECYCLE.md) | Stages, Human-decided transitions, the scale rule |
| [GOLDEN-GAMEPLAY-CELL.md](core/GOLDEN-GAMEPLAY-CELL.md) | The representative production-quality slice that precedes scale |
| [SKILL-MATURITY.md](core/SKILL-MATURITY.md) | `DRAFT` / `PILOTED` / `PROVEN` and promotion rules |
| [DEFINITION-OF-DONE.md](core/DEFINITION-OF-DONE.md) | Task, feature, cell and release done |
| [GOVERNANCE.md](core/GOVERNANCE.md) | How GPOS itself changes: proposals, registry changes, lesson intake, SemVer, deprecation, promotion, releases, migration, Phase-2 acceptance requirement |

## Authority hierarchy

1. Human Decision
2. Project locked authority
3. Approved visual / audio / animation references
4. Game Production OS
5. Engine / tool defaults
6. Implementation
7. Agent recommendation

Lower authority never silently overrides higher authority. GPOS itself sits **below** a project's Human Decisions.

## Human creative authority

Agents own implementation; humans own final subjective judgement. Agents may analyze, implement, compare, generate options, prepare evidence and recommend. They may not create canonical creative authority, synthesize Human Review, waive the Golden Cell, advance lifecycle stages or promote skills. Agents may transcribe a human verdict but never invent one; GPOS cannot yet authenticate human evidence technically, and that limitation is documented ([HUMAN-AUTHORITY.md §7](core/HUMAN-AUTHORITY.md#7-authenticity-of-human-evidence-trust-boundary)). A human is not the fallback implementer for technical engine or DCC work an agent workflow failed to complete. See [HUMAN-AUTHORITY.md](core/HUMAN-AUTHORITY.md).

## Skills

| # | Skill | Owns (summary) | Gate(s) owned by default |
|---|---|---|---|
| 01 | [game-director](skills/game-director/SKILL.md) | Decomposition, routing, gate and evidence selection, Human Review timing | — |
| 02 | [gameplay-design](skills/gameplay-design/SKILL.md) | Loops, mechanics, choices, rewards, failure, progression | `GAMEPLAY_DESIGN` |
| 03 | [level-design](skills/level-design/SKILL.md) | Metrics, routes, traversal, encounter layout, sightlines, pacing | `LEVEL_DESIGN` |
| 04 | [character-animation](skills/character-animation/SKILL.md) | Idle, starts, locomotion, turns, stops, blends, contact, silhouette | `ANIMATION` |
| 05 | [camera-composition](skills/camera-composition/SKILL.md) | FOV, distance, follow, dead zone, look-ahead, readability, comfort | `CAMERA_COMPOSITION` |
| 06 | [art-direction](skills/art-direction/SKILL.md) | Visual identity, shape/silhouette/palette/material language | `VISUAL_ART` |
| 07 | [environment-art](skills/environment-art/SKILL.md) | Visual massing, surfaces, dressing, environment storytelling | `VISUAL_ART` (environment scope, when routed) |
| 08 | [technical-art](skills/technical-art/SKILL.md) | Pipeline, topology, UVs, rigs, skinning, shaders, LOD, import/export | `TECHNICAL`, `PERFORMANCE` (when routed) |
| 09 | [game-feel-vfx](skills/game-feel-vfx/SKILL.md) | Anticipation, impact, response, VFX, feedback timing | `GAME_FEEL_VFX` |
| 10 | [ui-ux](skills/ui-ux/SKILL.md) | Hierarchy, layout, touch usability, safe areas, comprehension | `UI_UX` |
| 11 | [audio-design](skills/audio-design/SKILL.md) | SFX, ambience, interaction audio, mix priority | `AUDIO` |
| 12 | [qa-performance](skills/qa-performance/SKILL.md) | Tests, regressions, determinism, saves, builds, devices, profiling | `TECHNICAL`, `PERFORMANCE`, `DEVICE` |
| 13 | [game-engineering](skills/game-engineering/SKILL.md) | Gameplay and system code, runtime architecture, persistence, engine integration | — (implements; `qa-performance` verifies) |

Each contract has the same nineteen sections, including explicit **DOES NOT OWN**, **STOP / ESCALATE CONDITIONS**, **CROSS-REVIEW** and **HUMAN REVIEW REQUIREMENTS**.

Possible future specialist domains (not part of Phase 1): narrative / writing, localization, accessibility, networking, economy / live operations, music composition, marketing capture.

## Workflows

`new-game` · `golden-gameplay-cell` · `gameplay-feature` · `runtime-system` · `character-production` · `animation-production` · `asset-production` · `level-production` · `environment-production` · `ui-production` · `visual-review` · `device-validation` · `release`

Each workflow defines entry conditions, steps, specialists, required gates, required evidence, Human Review points, exit conditions, forbidden shortcuts and a postmortem. Workflows contain no engine-specific commands.

## Quality gates

`TECHNICAL` · `GAMEPLAY_DESIGN` · `LEVEL_DESIGN` · `ANIMATION` · `CAMERA_COMPOSITION` · `VISUAL_ART` · `GAME_FEEL_VFX` · `UI_UX` · `AUDIO` · `PERFORMANCE` · `DEVICE` · `HUMAN_REVIEW`

Statuses: `PASS` · `FAIL` · `CHANGES_REQUIRED` · `NOT_APPLICABLE` (reason required) · `NOT_RUN` (never implies `PASS`).

One gate passing never passes another. A scope is ready only when every blocking relevant gate is `PASS`. See [QUALITY-GATES.md](core/QUALITY-GATES.md).

**Review is proportional.** Each gate carries a review policy — `ROUTINE` (bounded work inside approved authority; the owner may close its gate when routing allows it), `CROSS_REVIEW_REQUIRED` (default for subjective gates) or `HUMAN_REVIEW_REQUIRED`. Human Review is always mandatory for seven triggers: `GOLDEN_CELL_EXIT`, `CANONICAL_CREATIVE_ASSET`, `MAJOR_BASELINE`, `ART_DIRECTION_CHANGE`, `MILESTONE_ACCEPTANCE`, `RELEASE`, `AUTHORITY_CHANGE`. Projects may require it more often; nothing may relax the triggers.

## Evidence model

`CODE_EVIDENCE` · `TEST_EVIDENCE` · `RUNTIME_EVIDENCE` · `VISUAL_EVIDENCE` · `MOTION_EVIDENCE` · `AUDIO_EVIDENCE` · `DEVICE_EVIDENCE` · `PERFORMANCE_EVIDENCE` · `PERSISTENCE_EVIDENCE` · `HUMAN_EVIDENCE`

Every gate declares **base** evidence plus **conditional** evidence tied to named conditions (e.g. `REAL_TIME_BEHAVIOUR`, `CAMERA_MOTION_CLAIM`, `TOUCH_OR_MOBILE_TARGET`, `TARGET_PRESENTATION_DIFFERS`) that routing applies explicitly — so turn-based, card or narrative games are not forced into real-time evidence, and mobile evidence is not universal. Every evidence record carries provenance (capture context, subject revision, build, platform, tool version, instrumentation) and cannot silently prove a newer revision. Capture contexts: `DCC_RENDER`, `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `PERFORMANCE_RUNTIME`, `OFFLINE_ANALYSIS`, `AUTOMATED_TEST`, `HUMAN_RECORD`. Examples: animation cannot pass from stills or tests; camera follow cannot pass from numbers; game feel cannot pass from code inspection; visual art cannot pass because an import succeeded; target performance cannot pass from Editor profiling or from runs whose instrumentation distorts timing; persistence cannot pass from in-memory state; Human Review cannot be synthesized. See [EVIDENCE-RULES.md](core/EVIDENCE-RULES.md).

## Golden Gameplay Cell

Before mass content production, each project builds one short, representative slice of play at production quality — player, movement, camera, one core interaction, a representative environment, visual language and lighting, UI, feedback, audio where applicable, target-device performance, and one encounter if the game has combat. Required by default; waivable only by Human Decision. The primary Human Review question:

> "Without explanation, does a short representative gameplay recording look and feel like the intended production game?"

A technically correct but visually or game-feel-inadequate cell fails. See [GOLDEN-GAMEPLAY-CELL.md](core/GOLDEN-GAMEPLAY-CELL.md).

## Skill maturity

`DRAFT` (contract reviewed, not yet validated in production) → `PILOTED` (used successfully in a real production task with Human Review and postmortem) → `PROVEN` (validated repeatedly). Skills never promote themselves; promotion is a Human Decision. **Every skill in this release is `DRAFT`.** See [SKILL-MATURITY.md](core/SKILL-MATURITY.md).

## Project-local authority (`.game/`)

Each game keeps its own authority in a `.game/` directory built from `templates/`: `PROJECT.md`, `PILLARS.md`, `GAME-DESIGN.md`, `ART-BIBLE.md`, `GAME-FEEL.md`, `CAMERA.md`, `ANIMATION.md`, `LEVEL-DESIGN.md`, `UI-UX.md`, `AUDIO.md`, `PERFORMANCE.md`, `DECISIONS.md`, `CURRENT.md`, plus per-review `HUMAN-REVIEW.md` records. These override generic GPOS guidance according to the authority hierarchy. Templates contain placeholders only (`UNDECIDED`, `HUMAN_DECISION_REQUIRED`, `NOT_APPLICABLE`, `PROJECT_SPECIFIC`); missing decisions stay visibly missing. Bootstrapping is not implemented in Phase 1.

## Future phases

| Phase | Scope |
|---|---|
| 1 (this release) | Core architecture, `DRAFT` contracts, schemas, validation |
| 2 | Pilot specialist pack and adapter foundations. Likely first deeply piloted skills: `game-director`, `character-animation`, `camera-composition`, `technical-art`, `game-feel-vfx`, `qa-performance` |
| 3 | Tool adapters: Claude/Codex, Unity, Blender, FFmpeg, device, Git/GitHub |
| 4 | Real-project pilot |
| 5 | Expand remaining specialist maturity |
| 6 | Bootstrap, distribution, version migration |

Adapter and tool boundaries: [adapters/README.md](adapters/README.md), [tools/README.md](tools/README.md). Future Claude and Codex instructions will be generated from this shared source, not maintained as duplicated hand-written authority.

## Versioning

Semantic Versioning, currently `1.0.0-alpha.7` ([VERSION](VERSION), [CHANGELOG.md](CHANGELOG.md)).

| Bump | When |
|---|---|
| PATCH | Typo, clarification, non-semantic validation improvement |
| MINOR | Compatible new workflow, skill, evidence type or adapter capability |
| MAJOR | Authority model, lifecycle semantics, gate or review-policy semantics, or any incompatible contract change |

The repository remains alpha until piloted on a real game. Change process: [GOVERNANCE.md](core/GOVERNANCE.md).

## Known limitations

- **Project record validation is not implemented.** Phase 1 validates the framework, not a project's actual routing, gate and evidence records against each other (revision match, linked Human Review, superseded evidence, evidence type and context per gate). This is a Phase-2 acceptance requirement ([GOVERNANCE.md §12](core/GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation)).
- **Human evidence is not authenticated.** See [HUMAN-AUTHORITY.md §7](core/HUMAN-AUTHORITY.md#7-authenticity-of-human-evidence-trust-boundary).
- **Applying evidence conditions is a routing judgement.** The conditions are fixed vocabulary; whether one applies to a task is decided and recorded by routing.
- **Decision references are not yet resolved.** Schemas check that a reference looks like a decision id and that decisions carry structured values; resolving each reference to an authorized Human Decision whose value matches the configuration, rejecting duplicate ids and enforcing reviewer gate permissions are Phase-2 tooling (reference model in the test suite).
- **Device coverage is minimal by design.** Target-runtime evidence must be on a declared target platform and a declared reference device; Release needs every PRIMARY platform. Richer device-coverage planning (selected-device matrices, hardware catalogues) is left to Phase 2 if piloting needs it.
- **Timestamps are validated as RFC 3339** by the framework validator and, when installed, by `jsonschema` with `rfc3339-validator`.

## Future considerations (not Phase 1)

- Games with materially different core modes may need a Golden Gameplay Cell set rather than one cell.
- Asset source and licensing provenance.
- Further specialist domains: narrative, localization, accessibility, networking, economy / live operations.

## Validation

```bash
python3 tests/validate_framework.py
```

Standard library only. Checks structure, contract sections, maturity, vocabulary consistency across every document and schema, internal links, schema behaviour against valid and invalid fixtures, and key ownership separations. It verifies consistency — it cannot and does not judge subjective framework quality. See [tests/README.md](tests/README.md).
