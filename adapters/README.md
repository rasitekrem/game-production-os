# Adapters — placeholder (Phase 1)

**Nothing in this directory is implemented in Phase 1.** This file defines the boundary so that later phases add adapters without changing GPOS semantics.

## What an adapter is

An adapter connects GPOS contracts to a specific agent runtime or tool. It may:

- generate agent-specific instruction files from GPOS sources,
- expose tool capabilities (capture, render, profile, deploy) that produce GPOS-typed evidence,
- read and write GPOS records (routing, gate, evidence) in the formats defined in `schemas/`, filling evidence provenance (capture context, revisions, build, hash, platform, tool version, instrumentation) from the tool itself wherever possible.

An adapter may **not**:

- redefine gates, statuses, evidence types, authority levels or lifecycle stages,
- emit evidence in a capture context incompatible with its type (registry `evidence_context_compatibility`),
- weaken any rule in `core/`,
- synthesize Human Review or `HUMAN_EVIDENCE` (a future connector that captures a verdict the human enters themselves is the intended way to strengthen authenticity; see core/HUMAN-AUTHORITY.md §7),
- bypass the single-writer rule for stateful editors unless a project has validated a workflow for it.

## Single source of authority

Future Claude and Codex instructions **must be generated from the shared GPOS source** (`core/`, `skills/`, `workflows/`, `core/registry.json`) — not duplicated as hand-written, per-agent authority. Hand-maintained parallel copies drift; drift creates conflicting authority. Generated files are build artefacts; GPOS source is authority.

## Planned adapter categories (Phase 2–3)

| Category | Intended role | Evidence it may produce |
|---|---|---|
| Claude Code | Generate skills / instructions from GPOS source; routing and gate bookkeeping | — (records only) |
| Codex | Same as above for Codex | — (records only) |
| Unity official agent / plugin integration | Engine operations through the vendor's supported path | `RUNTIME_EVIDENCE`, `VISUAL_EVIDENCE`, `MOTION_EVIDENCE`, `TEST_EVIDENCE`, `PERFORMANCE_EVIDENCE` |
| Unity MCP | Engine inspection and mutation via MCP | as above |
| Blender CLI | Headless DCC operations, asset validation, renders | `VISUAL_EVIDENCE` and `MOTION_EVIDENCE` in `DCC_RENDER`; asset-analysis `CODE_EVIDENCE` / `PERFORMANCE_EVIDENCE` in `OFFLINE_ANALYSIS`. Never game `RUNTIME_EVIDENCE` — Blender executing is not the game running. |
| Blender MCP | Interactive DCC inspection and mutation | as Blender CLI; never game `RUNTIME_EVIDENCE` |
| FFmpeg | Recording, trimming, side-by-side comparisons, frame extraction | `MOTION_EVIDENCE`, `AUDIO_EVIDENCE`, `VISUAL_EVIDENCE` |
| Android / device | Install, run, capture, profile on physical devices | `DEVICE_EVIDENCE`, `PERFORMANCE_EVIDENCE`, `MOTION_EVIDENCE` |
| Git / GitHub | Provenance, review integration, gate status reporting | `CODE_EVIDENCE`, `TEST_EVIDENCE` (CI) |

Adapter ids will be registered for `enabled_adapters` in `schemas/project-config.schema.json`. Adapter-specific settings belong under `extensions`.
