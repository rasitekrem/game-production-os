# Tools

**Phase 2A implements one tool: the production validator.** Its code is the Python package [`gpos/`](../gpos/__init__.py); its documentation is [validator/README.md](validator/README.md). This directory holds documentation only.

```bash
python3 -m gpos.validator validate  --project PATH [--format text|json]
python3 -m gpos.validator readiness --project PATH --routing ID [--format text|json]
```

The validator checks a project's records (project config, Human Decisions, routing, gates, evidence) against `schemas/`, `core/registry.json` and every record-validation requirement of [core/GOVERNANCE.md §12](../core/GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation). It is read-only: it reports and never changes gate statuses, records or decisions on its own. It does not judge creative quality; that stays with Human Review.

Agent adapters (render, sync and check generated Claude Code and Codex instructions) are implemented in Phase 2B as `python3 -m gpos.adapters`; see [adapters/README.md](../adapters/README.md).

The **tool adapter foundation** is implemented in Phase 2C-0 as `python3 -m gpos.tools`; see [adapter-foundation.md](adapter-foundation.md). It is the shared execution, capability, provenance, safety and evidence layer that future tool adapters must use. Phase 2C-0 integrates no real production tool: the only executable adapter in the tree is a `TEST_ONLY` synthetic reference adapter, and the production registry is empty.

```bash
python3 -m gpos.tools list|describe|capabilities|probe|execute
```

The first production tool adapter is the **Git provenance adapter** (Phase 2C-1), built on that foundation without changing it: local, read-only repository inspection and exact-revision resolution, with no version-control mutation and no network. See [git-adapter.md](git-adapter.md).

The **media evidence adapters** (Phase 2C-2) are two production adapters, one per executable: `ffprobe` summarizes a local media file, and `ffmpeg` derives a still frame, a bounded review clip or a bounded audio segment from one, offering visual, motion or audio evidence in the source's own capture context. Media processing never upgrades capture authority. Both are local-only (closed protocol and demuxer whitelists), use fixed command templates, and capture nothing. See [media-adapters.md](media-adapters.md).

The **Android ADB evidence adapter** (Phase 2C-3) is the first target-device adapter. From one explicitly named physical Android target (`--input adb_serial=<serial>` selects it; `--device` names its verified canonical identity; `--target-platform ANDROID`) it captures a device report, a screenshot or one named package's memory snapshot, offering device, visual or performance evidence. Every target command is a fixed, read-only template: no install, launch, input, file transfer, shell runner or wireless ADB. Emulators are refused, because they are not device evidence. See [adb-adapter.md](adb-adapter.md).

The **Blender DCC adapter** (Phase 2C-4) is the first DCC adapter. It inspects one `.blend` file (bounded counts and names, never paths) and renders one still of its authored scene, camera and frame, offering `VISUAL_EVIDENCE` in `DCC_RENDER` for an `ASSET` only: asset inspection evidence, never runtime, motion or presentation proof. Every Blender process runs one fixed, audited helper from factory settings, with auto-execution and scripts disabled, offline, and with an isolated per-execution user-resource directory. The caller supplies no Python, script, option, engine, camera or output path, and the `.blend` is never saved. See [blender-adapter.md](blender-adapter.md).

The **Unity engine adapter** (Phase 2C-5) is the first engine adapter, batch plane only. `unity.inspect-project` statically reports a Unity project's exact Editor version and checks its package sources; `unity.run-editmode-tests` and `unity.run-playmode-tests` run the Unity Test Framework in a fresh batch-mode Editor and offer `TEST_EVIDENCE` in `AUTOMATED_TEST` when at least one test executed. The Editor is found only under the Unity Hub root and must match the project's version exactly; remote package sources are refused before launch; Package Manager configuration is isolated per execution; the command is fixed. It declares the `TOOL_INHERENT` network semantic: GPOS originates no network operation, but Unity's own process tree may use the network. See [unity-adapter.md](unity-adapter.md).

The same adapter's **live Editor plane** (Phase 2C-6A) works with a Unity Editor a Human already has open, through a fixed, audited GPOS bridge package. GPOS installs the bridge into a closed project. After the Human approves inside the Editor, GPOS holds one SESSION lease per GPOS project (the batch plane's resource). It exchanges bounded, strict, at-most-once requests with the bridge over local files: status, bounded inspection, and entering, pausing, resuming and exiting Play Mode as Editor state only. A stale session ends only through a separate Human-approved recovery. It never launches, quits, focuses or restarts an Editor, injects no input, runs no caller code and produces no evidence. See [unity-live-bridge.md](unity-live-bridge.md).

**Live Scene authoring** (Phase 2C-6B1) adds twelve fixed capabilities on that session through bridge 1.1.0. It inspects, creates, deletes, moves and sets GameObjects, Transforms, catalogued components and allowlisted serialized properties of the open, saved Scene, and saves that Scene to its own path. Objects are named only by `GlobalObjectId`. Every mutation carries tokens of an earlier inspection and is refused as a conflict when they changed. Every mutation runs as one named Undo group, is read back, and is reverted, with verification, on any mismatch. Prefab-instance content, assets, cross-Scene references, arrays, managed references and Save As are refused. `unity.live-install-bridge` upgrades an exact earlier released bridge in a closed project with a crash-recoverable transaction. See [unity-live-authoring.md](unity-live-authoring.md).

## Planned concepts (not implemented)

| Tool | Purpose | Constraints |
|---|---|---|
| Project bootstrap | Create a project's `.game/` directory from `templates/` and write a project config | Must leave every decision as a placeholder; must not invent game decisions |
| Evidence collection | Capture and register evidence records with correct type, capture context, provenance (revision, build, hash, platform, tool version, instrumentation) and limitations | Cannot create `HUMAN_EVIDENCE`; provenance filled by the tool, not typed by the agent. Phase 2C-0 builds the candidate and record-materialization half of this; registering records into a project stays a separate, human-authorized step |
| Framework upgrade / migration | Move a project from one GPOS version to another, with a report of changed semantics | MAJOR upgrades require Human Decision per project |

The Phase-1 reference implementation of the cross-record rules remains in `tests/validate_framework.py` as the regression oracle for the production validator; production code does not import it.
