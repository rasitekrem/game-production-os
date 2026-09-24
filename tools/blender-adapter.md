# Blender DCC adapter (Phase 2C-4)

Code: [`gpos/tools/blender/`](../gpos/tools/blender/__init__.py) · adapter id `blender` · status: the first production DCC adapter. It is built on the frozen [tool adapter foundation](adapter-foundation.md) without changing it.

The adapter answers two questions about one production `.blend` file:

1. what the file contains, as bounded metadata (an inspection);
2. what its authored scene, camera and frame look like as one still image (a render).

A render is **DCC evidence about an ASSET**. It is not proof of how the game looks at runtime: it proves nothing about engine presentation, the target platform, motion, interaction or game feel.

**This is not a Blender automation or Python interface.** The caller never supplies Python, a script, an expression, an operator, a Blender option, a render engine, a camera, an executable or an output path. Every Blender process runs one fixed, audited GPOS helper and exits.

The adapter never saves, exports, imports, packs, bakes, generates or edits anything. There is no interactive session, no camera or light placement, no automatic framing, no animation or turntable render, no add-on or package installation and no network access. Each of those needs its own Human Review.

## Identity

| Field | Value |
|---|---|
| `adapter_id` | `blender` |
| `target_tool` | Blender |
| `tool_family` | `DCC` |
| `adapter_kind` | `CLI` |
| `state_model` | `STATELESS` |
| platforms | `WINDOWS`, `MACOS`, `LINUX` |
| network | `FORBIDDEN` |
| TEST_ONLY | no |

`default_registry()` now contains exactly `adb`, `blender`, `ffmpeg`, `ffprobe` and `git`. The adapter is not an agent adapter, and it is not in the registry's `adapter_ids`.

**Why `STATELESS`.** Each capability is one fresh background process that owns no session. The render capability is `MUTATING` only because it writes one image into its execution's host workspace. The `.blend` itself is never written. No single-writer lease is needed.

## Capabilities

| Capability | Category / class | Context | Inputs | Output | Evidence |
|---|---|---|---|---|---|
| `blender.inspect-blend` | `INSPECT` / `READ_ONLY` | `OFFLINE_ANALYSIS` | none | none (data only) | none |
| `blender.render-scene` | `CAPTURE` / `MUTATING` | `DCC_RENDER` | optional scene_name, frame | `render.png` (`IMAGE`) | `VISUAL_EVIDENCE` in `DCC_RENDER`, subject kind `ASSET` |

Both capabilities consume exactly one input artifact, with id `blend`. It must be a `.blend` file with no capture context: a `.blend` is production source, not captured evidence, so no earlier capture's authority can pass to a render. Anything else is refused before Blender starts, including no artifact, two artifacts, a wrong id, another suffix or a capture context.

The render capability requires mutation consent, supports a dry run, and accepts only an `ASSET` subject. A task, feature, project or release subject needs engine or runtime evidence, which a Blender render is not.

## Verified Blender behaviour

The first-party documentation was consulted on 2026-09-23 and 2026-09-24:

| Document | What it establishes |
|---|---|
| [Command Line Arguments](https://docs.blender.org/manual/en/latest/advanced/command_line/arguments.html) | `--background`. `--factory-startup` skips the user's startup file. `--disable-autoexec` covers Python drivers and startup scripts and is the default. `--offline-mode` disallows internet access, overriding the preference. `--python-exit-code`. `--python-use-system-env` is off by default. `-noaudio`. Argument order matters: loading a file overwrites options given before it. The environment variable BLENDER_USER_RESOURCES replaces the default directory of all user files. |
| [Scripting Security](https://docs.blender.org/manual/en/latest/advanced/scripting/security.html) | Auto-execution is disabled by default. Registered text blocks and Python drivers run automatically when it is enabled. **Freestyle rendering can run scripts even with auto-execution off.** Preferences are still used on the command line. |
| [bpy.ops.wm](https://docs.blender.org/api/current/bpy.ops.wm.html) | `open_mainfile(filepath, load_ui, use_scripts, …)`. The `use_scripts` option ("Trusted Source") defaults from the preferences, so the helper always passes it explicitly. |

The render operator's `write_still` and `scene` options, the render engines, and the user-resource layout were checked against the installed Blender's own RNA and `bpy.utils`. The helper self-test re-checks the ones the adapter relies on every time the adapter is probed.

**Real runtime.** Blender **5.2.0 LTS** on macOS, at the canonical app-bundle path. The built-in render engines in 5.2 are BLENDER_EEVEE, BLENDER_WORKBENCH and CYCLES. BLENDER_EEVEE_NEXT does not exist in 5.2.

Observed on the real runtime, and handled in code:

| Observation | Consequence |
|---|---|
| HOME does **not** redirect Blender's user resources on macOS; the official BLENDER_USER_RESOURCES does, verified by resolving every user resource kind under a temporary root | every process gets an isolated, per-execution BLENDER_USER_RESOURCES (see [Pre-flight user-configuration incident](#pre-flight-user-configuration-incident)) |
| a registered text block and a Python driver run when a file is opened with auto-execution enabled and `use_scripts=True` | production uses `--disable-autoexec` and `use_scripts=False`; tests prove the markers run under the control and never under production |
| a Freestyle style module in SCRIPT mode runs embedded Python during rendering **even with** `--disable-autoexec` | every Freestyle render is refused; it is never silently disabled |
| a user startup script in the isolated root runs without `--factory-startup`, and not with it | `--factory-startup` is part of the baseline |
| a scene authored with an engine that is not available (an add-on engine) is shown by the Python API as the fallback engine BLENDER_EEVEE | the helper reads Blender's own load report from the execution's log file; such a scene is refused and inspected with its authored engine |
| `--log-file` truncates the file, and the load report is written line by line as it happens; a normal load log is a few hundred bytes, and 800 scenes with unavailable engines produce about 300 KiB | the helper trusts the report only when the whole log is at most 256 KiB (see [Render](#render)) |
| the factory cube material carries a *weak library reference* to Blender's bundled brush library, stored as a path relative to where the file was saved (`//../../…/datafiles/assets/brushes/…`); in a copy at another directory depth, the same text resolves somewhere else, where no file exists | a path is Blender's own only when the file it actually resolves to lies inside the installation's resolved datafiles directory; the generated fixtures replace that material, so they are truly self-contained |
| with scripts disabled, Blender sets its own read-only flag `bpy.app.autoexec_fail` when it blocks a registered text block, or a driver that its restricted driver evaluator refuses (for example one naming `__import__`, even in a branch not taken at that frame); the flag has a message naming only the last failure | a render is refused whenever the flag is set; inspection stays available |
| a simple driver expression (`frame * 0.5`, or one of driver variables) is evaluated natively; a non-simple one using only names Blender's restricted driver namespace allows (`max(frame, 2) * 0.25`) is evaluated there; neither sets the flag | such drivers render, and the image shows their effect |
| a PNG render carries metadata stamps by default, including the file name, the date and the render time | with burn-in off, the metadata stamps are switched off in memory: renders are reproducible and name no file. Burn-in (stamps drawn into the pixels) is refused |
| `frame_start` and `frame_end` have a hard minimum of 0; the resolution has a hard minimum of 4 | the frame input is a non-negative integer, no larger than 1,048,574 |

## Executable discovery

Blender is found by an exact-name lookup on PATH over a fixed per-platform candidate list: `blender` and `Blender` on macOS, `blender` on Linux, and `blender.exe`, `Blender.exe`, `blender` and `Blender` on Windows. The rules:

- the path that `shutil.which` returns must be absolute; a relative PATH entry is rejected;
- its basename must be a real directory entry spelled **exactly** that way. On a case-insensitive filesystem, `which("blender")` "finds" `…/MacOS/blender` although the file is `Blender`. That hit is rejected, never repaired;
- if more than one distinct executable (by real path) qualifies, none is chosen.

No caller, request, input or environment variable chooses the executable. There is no alias, symlink or installation-path special case.

During Phase 2C-4 the executable was reached by a temporary PATH prepend of the canonical macOS app-bundle executable directory, authorized by Human Review. It was per process only: no shell profile, system PATH, IDE or repository configuration was changed.

## The safety baseline

Every Blender process, including the probe's self-test, starts as:

```text
Blender --background --factory-startup --disable-autoexec --offline-mode -noaudio
        --log-file <isolated-user-root>/blender.log --python-exit-code 71 --python <gpos-blender-helper>
        -- <mode> <nonce> [<arguments>]
```

Its environment is the foundation's allowlist plus BLENDER_USER_RESOURCES, pointed at a fresh directory created for this execution under the project's runtime directory and removed afterwards. The probe's root is in the system temporary directory.

The layers:

1. **Isolated user resources.** No user preference, startup script, add-on or extension participates. A parent-process BLENDER_USER_RESOURCES is not inherited: the adapter sets its own.
2. `--factory-startup`: no startup file, and no user startup scripts or add-ons.
3. `--disable-autoexec`: auto-execution is off whatever any preference says.
4. `open_mainfile(load_ui=False, use_scripts=False)`: the helper opens the `.blend` explicitly after start-up, so embedded scripts do not run for that file, and its UI layout is not loaded.
5. `--offline-mode`: no internet access, whatever any preference says.
6. **The fixed helper.** Its only variable arguments come after `--`: the mode, a per-execution nonce, the validated `.blend` path, the adapter's workspace output path, an exact scene name and a canonical frame number.

The self-test refuses a Blender where `open_mainfile` lacks `use_scripts` or `load_ui`, where the render operator lacks `write_still` or `scene`, where `bpy.utils.blend_paths` or `bpy.app.autoexec_fail` is missing, where factory start-up is not in effect, where auto-execution is enabled or online access is allowed, or where no built-in engine can be set. That Blender is `VERSION_UNSUPPORTED`. The helper must also report the same version as `--version` did.

This is process-level configuration, not operating-system sandboxing. A Blender process runs with the invoking user's file-system permissions.

## Helper protocol

The helper is [`helper.py`](../gpos/tools/blender/helper.py). It runs inside Blender and imports only `json`, `os`, `re`, `sys` and `bpy`. It uses two operators, `wm.open_mainfile` and `render.render`. It has no eval, exec, compile, subprocess, network, save, export, import or add-on call.

It writes exactly one line to stdout:

```text
GPOS_BLENDER_RESULT_V1:<nonce>:<ASCII JSON object>
```

The parser in [`parser.py`](../gpos/tools/blender/parser.py) reads the process boundary's **private raw capture**, never the redacted public text. A scene name that looks like a credential would be rewritten by the redaction, so it has to be read from the raw bytes. The parser requires:

- exactly one line with the prefix;
- this execution's 32-hex-digit nonce, so file content printed by Blender cannot forge a result;
- a record of at most 256 KiB;
- ASCII JSON that is an object and matches its schema exactly: no extra or missing field, bounded names without control characters, bounded integers, and booleans that are real booleans.

A missing record, several records, a wrong nonce, malformed JSON, a schema violation, a non-zero exit (the helper's own failure exit is 71), a timeout or output that reached the capture bound all fail closed. Blender's logs are never passed on: the public result carries no stdout or stderr.

## Inspection

Inspection reports:

- the file version and the active scene;
- the scene count and, for each scene (at most 64):
  - its name and camera name;
  - its frame range and current frame;
  - its authored render engine, and whether that engine is available;
  - its resolution and percentage;
  - whether Freestyle is on;
- object counts by group (mesh, armature, camera, light, empty, other);
- mesh datablock, vertex, edge and polygon totals;
- material, image, armature and action counts;
- the external-dependency count and how many of those files are missing;
- whether the file has compositor File Output nodes, and whether it has script nodes.

It reports **counts and names, never paths**. More than 64 scenes, or a name longer than 256 characters, fails closed. Inspection never renders, saves or creates evidence.

**External dependencies** are the union of every path Blender itself reports (`bpy.utils.blend_paths`) with an explicit list of render-relevant sources: linked libraries, file-backed unpacked images, fonts that are not built in, unpacked sounds and volumes, movie clips and cache files. Packed data is part of the `.blend`.

Each stored path is first **resolved** to the real file it refers to from the loaded file (`//` expanded, every `..` and symbolic link followed). It is excluded only when **both** hold: that resolved path lies inside the resolved datafiles directory of the running installation (`bpy.utils.system_resource("DATAFILES")`), compared by whole path components with the platform's case and drive rules, **and** it is an existing regular file (the bundled resources observed on Blender 5.2 are asset-library `.blend` files). Those are Blender's bundled assets, part of the installation that the tool version identifies. The stored text never decides, and neither does location alone: a reference to a file that does not exist inside the real datafiles directory is an ordinary, missing dependency, and a test proves it. A path written to *look like* the installation (`//../Applications/Blender.app/…/datafiles/…`) but resolving next to the project is an external dependency, and a test proves it. A factory-derived file whose weak brush reference no longer resolves into the installation, because the file was moved to another directory depth, is counted as a missing dependency and is not rendered; this fails closed.

## Render

The render uses the authored scene. It is the active scene, or the scene_name input, which must match a scene name exactly: no case folding, trimming or prefix match. The authored camera, frame, engine and settings are used. Before anything renders, the helper refuses, in this order:

| Refusal | Why |
|---|---|
| unknown scene | names match exactly |
| no authored camera | a camera is never generated or chosen |
| frame outside the scene's range | a frame is never clamped |
| load log over 256 KiB | the unavailable-engine report could lie beyond any part read; the log is never read in part |
| unavailable or unsupported engine | only BLENDER_EEVEE, BLENDER_WORKBENCH and CYCLES; an engine is never replaced |
| effective resolution over 4096 on either edge or over 16,777,216 pixels | never downscaled; the asset owner changes the scene |
| any external dependency | only a self-contained `.blend` is rendered as evidence |
| Blender reports blocked source Python (`bpy.app.autoexec_fail`) | the scene may not be what its author sees; GPOS never runs source Python, so it cannot claim visual evidence from such a scene |
| Freestyle | can run embedded scripts during rendering even with auto-execution off |
| OSL script nodes, or the Cycles OSL shading system | Open Shading Language scripts |
| a compositor File Output node | would write files besides the one image |
| multi-view | writes more than one image |
| sequencer strips | the image would not be the 3D scene |
| stamp burn-in | would draw metadata such as the file name or date into the pixels; it is refused, not changed |

A refusal is `DCC_SOURCE_NOT_ACCEPTED` (`INVALID_REQUEST`), with the helper's short reason and no path. Nothing is written.

The helper changes only the following, in memory and never saved:

- the output format is PNG, RGBA, 8-bit, compression 15;
- the output path is the adapter's `<workspace>/render.png`, with the file extension added by nobody;
- every metadata-stamp flag is switched off (burn-in is already known to be off, so the pixels are unaffected);
- the scene is set to the frame.

It then renders one still (`write_still`) for that scene. Blender's blocked-Python flag is checked again after the frame is set and after rendering. If it appears only while rendering, the image exists but is refused: it becomes an incomplete artifact, never evidence. The adapter then checks the file:

- it is a regular file and not a symlink;
- it is not empty and not over 128 MiB;
- it is a complete PNG, with the signature, a first IHDR chunk and a final IEND chunk;
- its dimensions are exactly the scene's effective resolution.

The result data holds the source artifact id, the scene, frame, camera, engine, width, height and byte count.

**Reproducibility.** The same file renders to identical bytes, with no text chunks and no file name in the image.

## Evidence

A successful render offers one candidate:

| Field | Value |
|---|---|
| evidence type | `VISUAL_EVIDENCE` |
| capture context | `DCC_RENDER` |
| subject | the request's `ASSET` and revision |
| artifact | `render` (`IMAGE`, image/png, canonical) |
| limitations | an ASSET render supports inspecting the asset, not the game; no engine, runtime, platform or performance proof; no motion, interaction or game feel; authored settings, self-contained sources only |

It materializes through the foundation with no runtime field: no build revision or id, target platform, device or instrumentation. Nothing is fabricated.

**The gate boundary is the frozen registry's.** `DCC_RENDER` is a non-counting context for every gate except `VISUAL_ART`, which accepts it for the `ASSET` scope only. The test suite runs the real production validator: a materialized render is valid, with no diagnostics, for an ASSET-scope `VISUAL_ART` gate. For `CAMERA_COMPOSITION`, `GAME_FEEL_VFX`, `ANIMATION` and `UI_UX` the validator reports `EVIDENCE_CONTEXT_NOT_COUNTING`. The adapter adds no rule of its own.

## Dry run and output collisions

A dry run validates the request and plans. It prints three lines: what would render; the checks that only a real execution can make (scene, camera, engine, frame, resolution, dependencies, scripting, extra outputs); and that nothing is created. No project Blender process runs, and no workspace, image or evidence is created.

An existing `render.png` in the workspace, including a dangling symlink, is refused in both the dry run and a real run. An existing file is never reported as a new render and never overwritten.

## Partial output and timeout

If Blender leaves a file but the execution fails (timeout, a non-zero exit, a refusal record or a defective PNG), the file is reported as an incomplete artifact, `mutation_performed` is true, and there is no evidence. If a success record arrives without a file, the result is a failure.

## Privacy

The recorded command replaces the helper path, the `.blend` path, the output path, the log path and the nonce with placeholders:

- `<gpos-blender-helper>`
- `<blend-input>`
- `<workspace>/render.png`
- `<isolated-user-root>/blender.log`
- `<nonce>`

Refusal messages and inspection data carry no path. The tests use a credential-shaped file name and check that it appears in no result, provenance record, candidate, or CLI JSON or text output.

## Results and diagnostics

| Situation | Status | Code |
|---|---|---|
| request contract violated (inputs, subject, scene name, frame, unknown input) | `INVALID_REQUEST` | `INVALID_TOOL_REQUEST` |
| the source or scene fails the render or inspection contract | `INVALID_REQUEST` | `DCC_SOURCE_NOT_ACCEPTED` |
| render without consent | `INVALID_REQUEST` | `MUTATION_NOT_ALLOWED` |
| protocol failure, truncated output, non-zero exit, defective PNG | `FAILED` | `EXECUTION_FAILED` |
| timeout | `TIMED_OUT` | `EXECUTION_TIMEOUT` |
| Blender missing, only on a relative PATH entry, or ambiguous | `UNAVAILABLE` | `TOOL_NOT_FOUND` |
| unrecognized version, or a failed helper self-test | `INCOMPATIBLE` | `TOOL_VERSION_UNSUPPORTED` |

`DCC_SOURCE_NOT_ACCEPTED` is new in Phase 2C-4 and generic to DCC adapters.

## Security review

| Concern | Finding |
|---|---|
| caller Python, script, expression, operator, argv, engine, camera, executable or output path | none: two capabilities with inputs scene_name and frame only; tested at the descriptor, source and runtime-argv level |
| subprocess outside the boundary | none: the adapter, helper and parser import no subprocess or network module; process.py remains the only subprocess importer |
| embedded scripts (text blocks, drivers) | not run: `--disable-autoexec` and `use_scripts=False`, proven with marker-writing fixtures against a sensitive control |
| a render whose scene depends on blocked source Python | refused when Blender's own flag reports blocked Python, before rendering; drivers Blender evaluates without auto-execution still render |
| render-time scripts (Freestyle, OSL) | refused before rendering, never silently disabled; the Freestyle risk is proven real on this Blender |
| user preferences, startup scripts, add-ons, extensions | not loaded: isolated BLENDER_USER_RESOURCES plus `--factory-startup`, proven with a prepared user root |
| real user profile | never written: every real test process uses an isolated root, and the suite fails if the real profile's fingerprint changes |
| source modification | none: no save, export or pack call; tests hash the source and list its directory (no backup or autosave file appears) |
| undeclared file reads | refused: any external dependency blocks a render; Blender's own assets are recognized only as existing files at a resolved real location inside the installation, never by stored path text or location alone |
| silent engine fallback | refused: the complete, bounded load report is required; an oversized log is refused |
| extra writes | refused: File Output nodes, multi-view and sequencer strips; the workspace holds exactly render.png |
| forged helper result | refused: per-execution nonce, exactly one record, strict schema, parsed from the private raw capture |
| network | `--offline-mode`, online access checked off by the self-test, no network import |
| path leak | none: placeholders in the recorded command, no path in data or messages |
| sandboxing | none claimed: the process has the invoking user's permissions |

## Pre-flight user-configuration incident

During Phase 2C-4 pre-flight, before the production design existed, an isolation experiment wrongly assumed that setting HOME would redirect Blender's user resources on macOS. It does not. Blender used its normal 5.2 macOS user-resource directory. The experiment created one startup script there and switched the Auto Run Python Scripts preference to ON.

Recovery, authorized by Human Review:

- the startup script was removed from the active profile;
- the preference was explicitly restored to OFF, after a backup, with one bounded change, and verified;
- the exact earlier binary preference file cannot be proven, so no byte-for-byte restoration is claimed.

The production design was then changed to use Blender's official BLENDER_USER_RESOURCES isolation for every process. No further change to the real profile occurred. The test suite now fingerprints the real profile before and after every run.

## Limitations

- One still frame of an authored scene. No animation, turntable, video, framing or camera choice.
- Only self-contained `.blend` sources render. A file with linked libraries or external textures can be inspected but not rendered as evidence.
- An unavailable engine is detected through Blender's own load report in its log file, whose wording is version-specific. The helper fails closed if a report mentions an unavailable engine it cannot read, or if the log is larger than 256 KiB, and the self-test pins the helper to the Blender it runs in. This was verified on Blender 5.2.0 LTS only.
- A non-simple driver expression is evaluated by Blender's own restricted driver namespace with auto-execution off; GPOS relies on Blender's report (`bpy.app.autoexec_fail`) of what that namespace blocked, and refuses those renders.
- A file created from Blender's factory scene keeps a weak reference to Blender's bundled brush library, stored relative to where it was saved. After the file is moved to another directory depth it counts as a missing dependency, and the render is refused.
- Rendering uses the host's CPU and GPU as Blender chooses; identical bytes are verified on one host, not across hardware.
- Real-runtime tests ran on macOS only; Windows and Linux hosts were not exercised.
- Process-level isolation only; no operating-system sandbox.

## Tests

```bash
python3 tests/test_blender_adapter.py
python3 tests/mutate_blender_adapter.py
```

Both need Blender on PATH under its exact platform name. Without it, the suite stops with BLENDER_RUNTIME_UNAVAILABLE_FOR_PHASE2C4. It never falls back to mocks.

Every `.blend` fixture is generated fresh by [`tests/blender_fixture_builder.py`](../tests/blender_fixture_builder.py) inside an isolated user root; no binary fixture is committed. Stand-in programs cover only deterministic error cases: a missing or unrecognizable Blender, a failed self-test, protocol breaks, truncation, a partial or malformed PNG and a timeout.

Groups A–Z cover:

- registration and the real probe;
- a missing or incompatible Blender;
- inspection and source immutability;
- embedded and render-time code, and user-state isolation;
- the input contract, bounds and the protocol;
- external dependencies and asset scope;
- scene, camera, frame, engine and resolution;
- extra outputs and the real render;
- materialization and the DCC authority boundary against the real validator;
- mutation consent, dry run, partial output and timeout;
- privacy, the command surface and the CLI;
- the final hardening: resolved-path containment against a path that imitates Blender's datafiles, the bounded load log (a real oversized log, and the helper's own reader against crafted complete, at-the-bound, beyond-the-bound and oversized logs), and blocked source Python (Blender's own flag per fixture; text blocks and Python drivers refused; simple and restricted drivers rendered with their effect visible).
