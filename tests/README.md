# Framework validation

```bash
python3 tests/validate_framework.py
```

Requires Python 3.8+ and **no third-party packages**. That floor is not a qualification claim: only tested interpreter and operating-system combinations are qualified (alpha.23: Windows 11 Enterprise 10.0.26200 with CPython 3.14.8 x64; macOS as recorded for each release).

## Windows (alpha.23)

On Windows every suite runs in Python's UTF-8 Mode, given explicitly on the command line (the Windows locale is not UTF-8, and `python3` is usually the Microsoft Store alias, not an interpreter):

```bash
python -X utf8 tests/validate_framework.py
```

The same applies to every suite and harness below. A suite started without `-X utf8` on Windows stops at once with `WINDOWS_UTF8_MODE_REQUIRED`; the mutation harnesses pass the flag to every suite they run themselves.

| Suite | What it covers |
|---|---|
| `tests/test_windows_foundation.py` | Windows only (skipped elsewhere): W01 the Job Object process mechanism, W02 executable pinning and refusals, W03 handle-based liveness, W04 the environment, W05 NTFS containment and races (junctions, links, hard links, SUBST, 8.3, writers, pins), W06 leases and sessions, W07 platform refusals, W08 UTF-8 and bytes. Side effects: temporary directories, one SUBST drive letter that is removed again |
| `tests/test_posix_parity.py` | The alpha.22 POSIX source parity: frozen files unchanged but for the version, POSIX code unchanged but for `if sys.platform == "win32":` guards, every deliberate shared change listed. Source evidence only — never a macOS PASS. `tests/generate_posix_parity.py` wrote its fixture from the `v1.0.0-alpha.22` tag |
| `tests/test_mutation_gate.py` | The baseline gate every mutation harness runs behind (`tests/mutation_gate.py`): no mutant runs, and none is counted, unless the unmutated suite passes in an identically prepared copy; mutants for another host are `NOT_RUN`, never counted |
| `tests/test_windows_standin.py` | alpha.24: the test-only tool stand-in (`tests/windows_standin.py`, source `tests/fixtures/windows-standin/StandIn.cs`): exact argv, byte relay, exit code, timeout, refusal without its fixed files, and a fail-closed stop when the compiler is missing |

### Tool stand-ins on Windows (alpha.24)

The adapter suites replace a real tool with a stand-in only where the real tool cannot be made to misbehave on demand. On POSIX a stand-in is a script with a shebang. Windows starts only `.exe` images (alpha.23), so there each stand-in is a small C# program, compiled once per suite run from the reviewed source `tests/fixtures/windows-standin/StandIn.cs` by the .NET Framework compiler that ships with Windows (`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`), into a temporary directory that is removed afterwards. Copied as `<tool>.exe`, it runs `<python> -X utf8 -B <tool>.standin.py <arguments>` from two fixed files beside it, passes the arguments through exactly, relays stdout and stderr as bytes and returns the script's exit code; it runs inside the same Job Object as any tool. No executable is committed, nothing is downloaded, no package is installed, and no production code uses it. Without the compiler the suite stops with WINDOWS_STANDIN_COMPILER_UNAVAILABLE. The compiler's identity and the source and image digests are recorded in `windows_standin.IDENTITY`; the image itself is not byte-reproducible (the compiler stamps it), so the source digest is what is pinned.

### Windows qualification of the production tools (alpha.24)

| Suite | On Windows |
|---|---|
| `test_git_adapter.py` | real Git; the production descriptor does not declare `WINDOWS` (D-W1, pending the repository-filter decision D-G1), so the suite measures the adapter through a test-side declaration and separately proves the production refusal |
| `test_media_adapters.py` | real FFmpeg and ffprobe; the two Git handoff tests are `NOT_RUN` |
| `test_adb_adapter.py` | always offline on Windows (see [ADB adapter tests](#adb-adapter-tests)); production does not declare `WINDOWS` |
| `test_unity_windows_live.py` | alpha.26: the Windows live bridge 1.6.0. Without Unity (GPOS_UNITY_TEST_FAST=1): the Windows capability gating (12 / 35) and the bridge's allowlist; the bridge core compiled and tested in both platform views and the Editor assembly compiled in both views with the Mono bundled with the Editor (native imports: Windows kernel32.dll!MoveFileExW only); the live IPC across real processes on NTFS with production code on both sides (rename held in flight, sharing interference, replaced or moved requests, timed races); the exact Editor identity; the call outcomes. With Unity (one lab Editor at a time, GPOS_UNITY_WINDOWS_LIVE_LAB, default `D:\gpos-unity-lab-alpha26`): install and the 1.5.0 upgrade, the R2 exact creation-time gate, the Scene-authoring demonstration with persistence proven by the batch plane and the file, SESSION integrity at each boundary, approval, the allowlist and Human-approved recovery. Approval is SYNTHETIC (the testkit). `mutate_unity_windows_live.py` runs fast mutations, and `--real` a bounded set one Editor at a time |
| `test_unity_windows.py` | alpha.25: the Windows Unity batch plane. Without Unity (GPOS_UNITY_TEST_FAST=1): gating, discovery and version against stand-ins, the lock proof against a fake host and against the real host (an exclusive holder process, an orphan, a stand-in Editor command line), environment and classification. With Unity: exactly one Hub Editor and the existing lab root GPOS_UNITY_WINDOWS_LAB (default `D:\gpos-unity-lab-alpha26` since alpha.26), under which every Unity project is created and removed; at most one Editor at a time. Fails if any Editor preference other than the accepted keys changes, if the user's Package Manager configuration changes, or if the shared licensing client changes. `mutate_unity_windows.py` runs fast mutations, and `--real` a bounded set one Editor at a time; a timeout is inconclusive, never caught |
| `test_blender_adapter.py` | real Blender 5.2.2 on an interactive desktop; group WA (render path templates, every host) and WB (private directories, cleanup, the 5.2.x rule; Windows only). Side effect: one test makes its project in a temporary directory on Blender's own drive (a relative path cannot cross drives) and removes it |

Optional cross-check: with `jsonschema` and `rfc3339-validator` installed, every result is compared with the reference implementation, including date-time format checking. If `jsonschema` is installed without date-time support the run fails rather than silently skipping format checks.

## Why no dependency

JSON Schema validation normally uses the `jsonschema` package. GPOS schemas are kept to a small keyword subset so a ~150-line standard-library validator (`schema_lite.py`) can check them. The validator **refuses unknown keywords** rather than ignoring them, so a schema change cannot silently weaken validation. If `jsonschema` happens to be installed, every validation result is cross-checked against it and any disagreement fails the run.

## What is checked

| Group | Checks |
|---|---|
| T01–T05 | Skills, workflows, core documents, templates exist; every skill and workflow has every contract section in order; every skill is `DRAFT` |
| T06–T08 | Gate statuses, gates and evidence types identical in registry, schemas and documents; `NOT_RUN` never counts as `PASS`; registry gate rules coherent |
| T09–T14 | Schema behaviour: invalid evidence, primary specialist, mandatory Human Review, project config, Golden Cell required by default |
| T15–T20 | Contract content: two examples per skill, Level Design ≠ Environment Art, Art Direction ≠ Technical Art, QA cannot override subjective gates, animation and camera motion claims require motion evidence |
| H01–H09 | Final hardening: review policies and their defaults (registry, schemas, QUALITY-GATES table, every skill contract); mandatory triggers incl. Golden Cell exit; conditional evidence (gameplay design not motion-only, device evidence never universal); capture contexts and provenance; record fixtures; visual responsibility split; GOVERNANCE sections; Phase-2 boundary; human-evidence trust boundary |
| A01–A11 | Alpha.3 corrections: `game-engineering` contract and implementation/verification separation, `runtime-system` routing, evidence type/context compatibility (incl. DCC render masquerading as runtime evidence), Human Decision schema and decision-ref fields, authority fixtures, lifecycle transition authority, corrected review semantics, accountable assessor, authorization model, scope kinds, presentation parity, future considerations |
| B01–B09 | Alpha.4 hardening: decision value binding (opposite values rejected), GPOS upgrade authority, accountable carryover, Human-Review-only routing, record-id uniqueness, gate-aware reviewer authorization, RFC 3339 timestamps and unambiguous ids, expanded Phase-2 contract, maturity and boundary |
| C01–C14 | Alpha.5 binding and readiness: evidence subject binding and explicit applicability, decision references resolved in gate and routing records, blocking-downgrade binding, routing↔gate linkage, routing-aware readiness (missing gates), routing uniqueness/disjointness, revision-bound cross-reviews, multi-routing authority checks, project triggers, character-production trigger semantics, `ROUTINE` owner assessment, config-decision subject rules, Human Review scope kind |
| D01–D10 | Alpha.6 routing authority closure: effective review policy (overrides, scoped overrides, ambiguity, trigger precedence), workflow always-required gates, routing evidence contract (validity and fulfilment), routed cross-reviewers, complete condition accounting and presentation parity `YES`, Golden Cell 12-gate accounting, Phase-2 contract, maturity and boundary |
| E01–E06 | Alpha.7 freeze closures: machine-readable cross-review eligibility (game-director never counts), Release 12-gate accounting, target-platform and reference-device binding, Release PRIMARY-platform coverage, Phase-2 contract, maturity and boundary |
| P (helper) | Phase-2A boundary (`phase_boundary_problems`): code only in `tests/` and `gpos/`, `adapters/` only README, `tools/` documentation only, production code imports neither tests nor network modules, and only `gpos/tools/process.py` may start a process (Phase 2C-0). The Phase-1 boundary tests (B09, C14, D10, E06, X08) asserted the Phase-1 file layout; they now assert this boundary, so their protection is preserved |
| X00–X10 | Consistency: gate-evidence table vs registry, schema enums and per-gate owner/condition rules vs registry, backticked vocabulary in every Markdown file, internal links and anchors, versions, examples, schema fixtures, Phase-1 boundary, templates contain no decisions, cross-review sections vs the routing table |

## Production validator tests

```bash
python3 tests/test_production_validator.py
```

Tests for the Phase-2A production validator (`gpos/`): schema parity with `schema_lite.py` and `jsonschema`, RFC 3339 boundaries, record-set and readiness parity with the frozen reference model on every authority and record fixture, named adversarial regressions on the synthetic bundles, bundle loading, read-only behaviour, determinism, CLI exit codes, performance on thousands of records, and the GOVERNANCE §12 coverage matrix. See [tools/validator/README.md](../tools/validator/README.md).

`fixtures/bundles/` holds synthetic project bundles (generic data) generated by `generate_bundles.py`. `mutate_production.py` is a bounded mutation harness for `gpos/`.

## Adapter tests

```bash
python3 tests/test_adapters.py
```

Tests for the Phase-2B agent adapter layer (`gpos/adapters/`): the IR (all 13 skills, authority order, project authority rows, placeholders preserved, maturity never promoted), Claude Code and Codex rendering (layout, budgets, front matter, determinism, no global paths), semantic parity of the two manifests, drift detection, sync ownership and idempotency, path security (traversal, manifest injection, symlinks), the validator precondition, context budgets and monolith rejection, the CLI and layout snapshots (`fixtures/adapter-snapshots/`, updated deliberately with `GPOS_UPDATE_SNAPSHOTS=1`). `fixtures/adapter-project/` is the synthetic project; `mutate_adapters.py` is a bounded mutation harness for `gpos/adapters/`.

## Tool adapter foundation tests

```bash
python3 tests/test_tool_foundation.py
```

Tests for the Phase-2C-0 tool adapter foundation (`gpos/tools/`): adapter identity, registration validation and deterministic listing; the capability model (read-only vs mutating, stateless vs stateful, contradictory declarations); probe states (`AVAILABLE`, `UNAVAILABLE`, `VERSION_UNSUPPORTED`) and lifecycle; execution (success, failure, dry run, mutation consent, unavailable and incompatible tools, adapter defects); process safety (no shell, executable and argv separated, unsafe working directories, path traversal, symlink escape, timeout and process-tree termination, output truncation, secret redaction, environment policy); single-writer leases; artifacts, streamed hashing and derivation; provenance (observed values recorded, unknown values absent and named); evidence candidates (registry compatibility, `HUMAN_EVIDENCE` refused, dry-run limits, derivation never upgrading a source, no gate vocabulary at all); proportional project preconditions; determinism; and the phase boundary. Group N covers the integrity hardening from code review: materialization provenance, project-less scopes, provenance and timing, request vocabulary, adapter-metadata redaction against a deliberately hostile adapter, runtime artifact claims, lease release and resource identity, the portability refinements, and the dry-run filesystem contract (a dry run creates no workspace and no parent of one; creation failures are structured results). `mutate_tools.py` is a bounded mutation harness for `gpos/tools/`.

No real production tool is required or invoked: everything runs against the `TEST_ONLY` synthetic reference adapter.

## Git adapter tests

```bash
python3 tests/test_git_adapter.py
```

Real integration tests for the Phase-2C-1 Git provenance adapter (`gpos/tools/git/`): every repository is created with the installed Git in a temporary directory and inspected through the audited process boundary. The suite fails if Git is absent; it never falls back to mocks. It covers registration and the real probe; missing or unusable tools; clean, dirty, conflicted, detached and unborn repositories; non-repository and nested projects; spaces, Unicode, newlines and renames in paths; truncated output and credential-shaped names parsed from the raw capture; submodules (dirty, hidden by `ignore = all`, untracked-only, moved HEAD, clean); fsmonitor hooks and the daemon proven not to start; the environment and real optional-lock behaviour; read-only behaviour checked by hashing every file under `.git`; resolve-provenance and the explicit `build_revision` handoff; the network and argument surface; the CLI; determinism; and linked worktrees. `mutate_git_adapter.py` is its bounded mutation harness.

## Media adapter tests

```bash
python3 tests/test_media_adapters.py
```

Real integration tests for the Phase-2C-2 media adapters (`gpos/tools/ffprobe/`, `gpos/tools/ffmpeg/`). Fixture media (a test pattern and a sine tone with credential-shaped metadata) are generated with the installed FFmpeg, and the adapters drive the same ffprobe and FFmpeg through the audited process boundary. The suite fails with the marker FFMPEG_RUNTIME_UNAVAILABLE_FOR_PHASE2C2 if either executable is absent; it never falls back to mocks. Groups A–Z cover:

- registration, real probes, and missing or unusable tools;
- inspection and the strict raw-JSON parser;
- non-media input;
- a real network block: a local HTTP server receives zero requests, with a sensitivity control that proves the fixture would reach it;
- frame, clip and audio extraction, checked with ffprobe from test code;
- numeric validation;
- missing, incompatible, inherited and DCC capture contexts;
- mutation consent and dry run;
- source immutability, output boundaries and no-overwrite;
- partial output and timeout;
- secrecy of credential-shaped names and metadata;
- the explicit Git handoff and evidence materialization;
- the exact command surface and the CLI.

`mutate_media_adapters.py` is its bounded mutation harness.

## ADB adapter tests

```bash
GPOS_TEST_ANDROID_SERIALS=<serial>[,<serial>...] python3 tests/test_adb_adapter.py
```

Real integration tests for the Phase-2C-3 ADB adapter (`gpos/tools/adb/`), run against real Android targets a human authorized. GPOS_TEST_ANDROID_SERIALS is test-only and names them. Without it, the suite uses the single eligible target, or stops with ADB_TARGET_UNAVAILABLE_FOR_PHASE2C3 or ADB_TARGET_SELECTION_REQUIRED_FOR_PHASE2C3. It never falls back to mocks. Stand-in programs cover only deterministic error cases.

Every real-target test runs on each authorized target in turn as `adb -s <serial>`. Physical devices capture and materialize evidence, whose identity is checked against the real Phase-2A validator's reference devices; emulators must be refused. Physical serials and build fingerprints are never printed (targets are labelled, and the runner filters its output), and screenshots are checked for structure only. Groups A–Z cover:

- registration and the real probe;
- target selection and readiness;
- the device report and its privacy;
- screenshot and truncation;
- meminfo, package validation and a package that is not running;
- contexts and materialization;
- the explicit Git handoff through the CLI;
- mutation consent, dry run and output collision;
- target immutability, the command surface, network and wireless;
- public output, the parsers and performance limitations;
- the CLI, repository privacy and the other adapters.

`mutate_adb_adapter.py` is its bounded mutation harness.

**Offline runs (alpha.24).** With GPOS_TEST_ADB_OFFLINE=1, and always on Windows, the suite contacts no ADB server and no Android target: it never runs `adb devices`, and every test that needs a target is `NOT_RUN` (reported as a skip that says why). Only `adb version`, which the client answers alone, runs with the real adb; every other real adb command is refused at the process boundary before it starts, by a guard installed when the suite is imported (so no way of loading the suite can bypass it), and the run fails if any such command was attempted outside a test reported `NOT_RUN`. The stand-in, parser, refusal and classification tests run. The mutation harness lists the mutations only a real target can catch as `NOT_RUN` in an offline run.

## Blender adapter tests

```bash
python3 tests/test_blender_adapter.py
```

Real integration tests for the Phase-2C-4 Blender adapter (`gpos/tools/blender/`), run against the real Blender found on PATH under its exact platform name. Without one, the suite stops with BLENDER_RUNTIME_UNAVAILABLE_FOR_PHASE2C4. It never falls back to mocks. Every `.blend` fixture is generated for the run by `blender_fixture_builder.py`, and no binary fixture is committed. Every Blender process the suite starts uses an isolated BLENDER_USER_RESOURCES, and the suite fails if the real Blender user profile's fingerprint changes. Stand-in programs cover only deterministic error cases. Groups A–Z cover:

- registration, the real probe and a missing or incompatible Blender;
- inspection and source immutability;
- embedded scripts, blocked source Python (text blocks, Python drivers), drivers Blender evaluates natively, Freestyle and OSL, and user startup and add-on isolation;
- the input contract, bounds, the helper protocol, the bounded load log and external dependencies (including a path that imitates Blender's datafiles, and a missing file inside the real one);
- asset scope, scene, camera, frame, engine, resolution and extra outputs;
- the real render, materialization and the DCC authority boundary against the real validator;
- mutation consent, dry run, partial output and timeout;
- privacy, the command surface and the CLI.

`mutate_blender_adapter.py` is its bounded mutation harness.

## Unity adapter tests

```bash
python3 tests/test_unity_adapter.py
```

Real integration tests for the Phase-2C-5 Unity batch adapter (`gpos/tools/unity/`), run against the one Unity Editor installed under the Unity Hub root. Without exactly one, the suite stops with UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C5; it never falls back to mocks. Every Unity project is generated for the run by `unity_fixture_builder.py` inside a temporary GPOS project, and none contains a hanging test (the adapter always runs a platform's whole test set). A module guard fails the suite if any Unity Editor preference other than the six accepted Unity-owned keys changes, or if the user's Package Manager configuration files change (existence, size and time only; never read). Stand-in programs cover only deterministic error cases. Groups A–Z cover:

- registration, the `TOOL_INHERENT` network semantic, discovery and the probe;
- static inspection with zero, one or several Editors, the project path and the exact version rule;
- manifest and lock-file package sources;
- Package Manager isolation, the command template, the lease and Unity's project lock;
- real EditMode runs (pass, failures, zero tests, compile failure) and real PlayMode runs;
- the results reader, classification, dry run, mutation consent and project side effects;
- privacy, materialization, the command surface and the CLI.

`mutate_unity_adapter.py` is its bounded mutation harness; it runs the suite with GPOS_UNITY_TEST_FAST=1, which starts no real Unity process.

## Unity live plane tests

```bash
python3 tests/test_unity_live.py
python3 tests/test_unity_live_bridge_core.py
```

Tests for the Phase-2C-6A Unity live Editor plane. Groups A–N of `test_unity_live.py` drive the GPOS side against `unity_live_fake_bridge.py`, a Python stand-in that speaks the same file protocol:
- registration, identity and the project key, the bridge manifest and the installer;
- the IPC client, status classification, attach with Human approval, inspection and Play Mode;
- deadlines, withdrawal and unknown outcomes;
- detach, clean close and recovery;
- batch and live on one resource, boundaries and the CLI.

They prove GPOS-side behaviour only. The real groups (R1, R2) open disposable synthetic projects in lab-owned batch-mode Editors. The test-only testkit package (`unity_live_testkit/`, never installed by GPOS) starts the production bridge and presses its production approval method for test-named owners only. They cover:
- install, bridge identity, approval, inspection, Play Mode, Domain Reload and compile errors;
- deadlines, withdrawal and unknown outcomes, and the batch plane blocked by a session;
- owner detach, clean close, crash, stale session and Human-approved recovery;
- the frozen batch plane with the bridge installed and dormant.

The same EditorPrefs and Package Manager guards as the batch suite apply. `test_unity_live_bridge_core.py` compiles the bridge's Unity-free C# core with the tests in `unity_live_bridge_core/`, using the Mono bundled with the installed Editor, and runs it without Unity. Both stop with UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A unless exactly one Hub Editor is installed. `generate_live_bridge_manifest.py` regenerates the bridge's fixed `.meta` files and release manifest; the suite runs it with `--check`. `mutate_unity_live.py` is the bounded mutation harness: fast-mode mutations of the GPOS side, and C# mutations of the bridge core against the core tests. For bridge mutations it regenerates the manifest in its copy, so only a behavioural test can catch them. Windowed behaviour and the real approval button are checked by a Human-attended release-candidate checklist, not by this suite.

## Unity live Scene authoring tests

```bash
python3 tests/test_unity_authoring.py
python3 tests/mutate_unity_authoring.py
python3 tests/mutate_unity_authoring.py --real
```

Tests for Phase 2C-6B1: Scene authoring (bridge 1.2.0 since alpha.18, with `unity.live-set-renderer-material` and asset references), and the bridge upgrade. The fast groups need no Unity:

- A: the thirteen capability declarations.
- B: the input grammar.
- C: the exact arguments GPOS sends and how every bridge answer maps to a result, against the protocol stand-in.
- D: the crash-recoverable bridge upgrade with an interruption at every step, the recovery matrix and fail-closed cases. It also checks that `gpos/tools/unity/live_bridge/history/1.0.0.json` and `1.1.0.json` and their pinned digests are exactly the manifests frozen in tags `v1.0.0-alpha.16` and `v1.0.0-alpha.17`, read with git; GPOS_SOURCE_GIT_DIR names the repository's .git when the suite runs in a copy.
- E: source boundaries.

The real groups use disposable projects from `unity_fixture_builder.make_authoring_project` in lab-owned batch-mode Editors. The testkit also stands in for the Human's Inspector edits, Hierarchy drags and Cmd-Z through `op-*.json` trigger files.

- R1 is one authoring session end to end: catalog filters and digest, every property kind with read-back, unsafe-property refusals, scene-only references, verified rollback (restored and incomplete), component rules, Human-concurrent-edit conflicts including hidden serialized state and transform chains, the prefab boundary, saving, Play Mode refusal, Domain Reload, a recompile that changes the catalog digest, withdrawal, a second saved Scene (cross-Scene refusals; creating in the non-active Scene dirties only that Scene) and detach.
- R2 upgrades real 1.0.0 and 1.1.0 bridges, extracted from the frozen tags, in a closed project and attaches to the upgraded bridge.

The same EditorPrefs and Package Manager guards apply. `test_unity_live_bridge_core.py` also runs `unity_live_bridge_core/AuthoringCoreTests.cs`. `mutate_unity_authoring.py` mutates the GPOS side and the bridge core against the fast suite and the core tests. With `--real`, it mutates the bridge's Editor-side authoring code against R1 in real lab Editors.

## Unity live asset tests

```bash
python3 tests/test_unity_assets.py
python3 tests/mutate_unity_assets.py
python3 tests/mutate_unity_assets.py --real
```

Tests for Phase 2C-6B2A: asset references and asset authoring through bridge 1.2.0. The fast groups need no Unity:

- A: the seven capability declarations and the closed vocabularies.
- B: the input grammar: asset ids (types 1, 3 and 4, prefab id 0, the built-in GUIDs), write paths, Material and ScriptableObject values, lookup and catalog inputs, each matched against the whole string.
- C: the exact arguments GPOS sends and how every bridge answer maps to a result against the protocol stand-in, including `mutation_performed` once a targeted import happened, `OUTCOME_UNKNOWN` from the commit point on, and the creation-recovery diagnostics.
- E: source boundaries of the bridge's asset code: forbidden mechanisms, where each write primitive appears, and the order of the persistence and creation steps.

The real groups use disposable projects from `unity_fixture_builder.make_asset_project` (generated PNG, WAV and OBJ files, a test shader with every shader property kind, ScriptableObject types, a logging AssetPostprocessor and an embedded fixture package) in lab-owned batch-mode Editors. The testkit also prepares the rest of the fixture as a Human would (a sprite import, a cubemap, a 3D texture, a prefab, materials, ScriptableObject assets), saves, imports, edits and undoes as a Human, and arms the bridge's `AssetAuthoring.AfterStep` test seam to write a file, or stop the Editor process, at one exact step.

- R3 is one asset session end to end: the catalogs, typed lookup in every source, the reference boundary, creation and its collisions, the temporary namespace (an existing temp file, an orphan temp `.meta`, a scratch-name collision, a competing final file before the move, unknown scratch content before cleanup), every Material property kind (a Sprite refused, the same PNG's Texture2D accepted), ScriptableObject properties and callbacks, Scene references to every asset kind and `m_Resource`, Renderer material slots with no Material instance created, Undo and dirty-after-Undo, Human edits, external disk and `.meta` changes, Domain Reload and a recompile that changes the ScriptableObject digest, Play Mode refusal and detach.
- R4 stops the lab Editor at exact creation steps (before `CreateAsset`, after an unproven and a proven temporary asset, after the move), restarts it, recovers the session and proves recovery: nothing created, exact temporary asset removed, exact final asset kept, modified temporary or final asset and untrusted records never touched.

The same EditorPrefs and Package Manager guards apply. `test_unity_live_bridge_core.py` also runs `unity_live_bridge_core/AssetCoreTests.cs`. `mutate_unity_assets.py` mutates the GPOS side, the bridge's asset core and the asset source boundaries against the fast suites and the core tests; with `--real`, it mutates the bridge's Editor-side asset code against R3 and R4 in real lab Editors.

## Unity live prefab tests

```bash
python3 tests/test_unity_prefabs.py
python3 tests/mutate_unity_prefabs.py
python3 tests/mutate_unity_prefabs.py --real
```

Tests for Phase 2C-6B2B: prefab authoring through bridge 1.3.0. The fast groups need no Unity:

- A: the nine capability declarations, the diagnostic codes and the absence of every deferred prefab operation.
- B: the input grammar: persistent prefab ids (type 1, prefab id 0), Scene ids, `.prefab` paths, prefab property values that never name a Scene object, and exactly one place token for an instantiation.
- C: the exact arguments GPOS sends and how every bridge answer maps to a result against the protocol stand-in, including `mutation_performed` once a targeted import happened (an instantiation refused after its import too), `OUTCOME_UNKNOWN` from the commit point on, recovery diagnostics by record kind and the side-effect disclosure.
- D: the 1.2.0 history manifest is the one frozen in `v1.0.0-alpha.18`, and an exact 1.2.0 package is upgraded.
- E: source boundaries of the bridge's prefab code: no deferred prefab operation, where each write primitive appears, the order of the edit lifecycle, the instantiation and the creation transaction, the alpha.18 asset surface authoring only Materials and ScriptableObjects, and the unchanged Scene prefab boundary.

The real groups use disposable projects from `unity_fixture_builder.make_prefab_project` (the asset project plus a component with every reviewed reference kind, and project scripts whose `OnValidate` and AssetPostprocessor change state on demand) in lab-owned batch-mode Editors. The testkit prepares regular, nested, Variant, package and embedded-asset prefabs, a plain Scene subtree and prefab instances with every override kind, and stands in for the Human in Prefab Mode (open, open a nested prefab in context, edit, save, go back through the breadcrumb, return to the Scenes).

- R5 is one prefab session end to end: inspection and its scope reasons, effective write authority, the alpha.18 asset surface refusing prefabs, instance inspection of every override kind, creation from a plain subtree (every reference, a real Sprite, the root's name and Transform) and every refused source and path, instantiation with stable Undo/Redo ids and its refusals (out of scope, dirty, stale token after the import), every edit through the isolated copy, properties and references including a real Sprite and both wrong Texture/Sprite types, and dependency propagation into a clean Scene.
- R6 exercises every guard: Prefab Mode in isolation and a nested prefab in context through the breadcrumb, a dirty prefab, the version-control seam and read-only file, `.meta` and folder, a file changed before the save, an unimported external change found by the targeted import, `OUTCOME_UNKNOWN` after the save, a dirty dependent Scene against an unrelated one, the Scene scan bound, project callbacks, and ids and tokens across Domain Reload, reimport and a move.
- R7 stops the lab Editor at exact steps (creation steps, during an edit before and after the save), restarts it and proves recovery, including records of every kind recovered by every creating command and a destination taken right before the move.
- R8 upgrades a running 1.2.0 bridge only while the project is closed.

The same EditorPrefs and Package Manager guards apply. `test_unity_live_bridge_core.py` also runs `unity_live_bridge_core/PrefabCoreTests.cs`. `mutate_unity_prefabs.py` mutates the GPOS side, the bridge's prefab core and the prefab source boundaries against the fast suite and the core tests; with `--real`, it mutates the bridge's Editor-side prefab code against R5, R6 and R7 in real lab Editors.

## Unity live source and compilation tests

```bash
python3 tests/test_unity_sources.py
python3 tests/mutate_unity_sources.py
python3 tests/mutate_unity_sources.py --real
```

Tests for Phase 2C-6C: source synchronization, compilation facts and wait-ready through bridge 1.4.0, and the batch plane's read-only project-lock proof. The fast groups need no Unity:

- A: the four capability declarations, and no compile, refresh, write, move or code capability anywhere.
- B: the source path grammar, strict JSON path lists named once, and the closed diagnostics and wait inputs.
- C: the exact paths GPOS sends and how every bridge answer maps to a result against the protocol stand-in (which models Unity's compilation counters): `mutation_performed` from the first import on, no mutation when nothing was imported, the side-effect disclosure, `OUTCOME_UNKNOWN`, the facts GPOS derives, and sanitized, paged diagnostics.
- W: wait-ready as observation only (nothing but compilation-status is sent): success after a proven reload, a completed failure, no success without a reload, a stale failure flag before the reload, a timeout with facts (`NONE_OBSERVED`), the causal baseline read before the first import, an older compilation never satisfying a sync, a changed Editor boot, and the 300 s bound.
- D: the 1.3.0 history manifest is the one frozen in `v1.0.0-alpha.19`, and an exact 1.3.0 package is upgraded.
- E: source boundaries: no forbidden mechanism in the bridge's source code, the two `ImportAsset` calls exactly where reviewed, every check before the first import and the baseline before it, the read-only lock proof (four libSystem calls, a read-only no-follow open, no lock taken, no write) and an adapter that never touches the lockfile.
- L: the lock proof with a scripted OS (every row of the decision table, unknown never meaning "no process", exact project matching, the candidate bound, lock-query failures, links, directories and FIFOs, a lockfile replaced while examined), with the real macOS calls on this host (a flock held by a child process, the argument vector of this process, the listing and argv bounds), and through the adapter with a stand-in Editor (both proofs, the orphan diagnostic, the dry run, Unity's own refusal of the race, and the installer's unchanged closed-project rule).

The real groups use disposable projects from `unity_fixture_builder.make_prefab_project` in lab-owned batch-mode Editors; the test itself stands in for the external tooling that writes source files.

- R9 is one source/compile session: a new script and its `.meta`, an exact reimport that never imports an unrelated file, a syntax error (existing types stay authorable, Unity refuses Play Mode), a type error and its fix (a cached assembly and a superseded error), a warning-only compilation, asmdef and asmref, coalesced imports and a sync refused while compiling, monotonic generations, a wait timeout with facts, paging and filters over two warning assemblies, CS2001 without an absolute path, and the journal lost at an Editor restart.
- R10 covers the session and ids across a reload and the catalog digest, requests during a compilation (`EDITOR_BUSY`) and during a reload (run once), a namespace change and a class rename, a move with its `.meta`, deletion synchronization with an unrelated file reported, D1, D2 (one widening only), the entry, depth and link bounds, no global Refresh, and a missing script that is never repaired.
- R11 is the permanent end-to-end qualification of the whole loop, with no Human Unity step.
- R12 is the real stale-lock contract: no lock, an orphan left by a compile error and replaced by Unity, an active matching Editor, a held flock without a matching process, an Editor with an unprovable (relative) project argument, Unity arbitrating the final race, and link and FIFO lockfiles.

The same EditorPrefs and Package Manager guards apply. `test_unity_live_bridge_core.py` also runs `unity_live_bridge_core/SourceCoreTests.cs`. `mutate_unity_sources.py` mutates the GPOS side, the lock proof, the adapter, the bridge's source core and its source boundaries against the fast suites and the core tests; with `--real`, it mutates the bridge's Editor-side source and compilation code against R9 and R10 in real lab Editors.

## Unity Build Core tests

```bash
python3 tests/test_unity_build.py
python3 tests/mutate_unity_build.py
python3 tests/mutate_unity_build.py --real
```

Tests for Phase 2C-7: `unity.inspect-build-configuration` and `unity.build-player` through the batch-only build entry of bridge 1.5.0. The fast groups need no Unity (a stand-in Editor under a temporary Hub root plays the build entry):

- A: the two declarations (47 Unity capabilities) and no caller authority input.
- B: no `build_id`, a canonical `build_revision` (SHA-1 or SHA-256 form), `MACOS` only for both capabilities, the token, the build request-id grammar, consent, a dry run that creates nothing, and a caller `output_dir` refused before any directory, lock proof or Unity process exists.
- C: the exact argv, the one executeMethod constant, and no request value in the command.
- D: 1.5.0 on protocol `/5`, `history/1.4.0.json` equal to the manifest frozen in `v1.0.0-alpha.20`, the live lifecycle unchanged apart from its header, an exact 1.4.0 package upgraded, and the build entry refused on anything but exactly this release.
- E: the build entry's allowlist scan (two `BuildPlayer` calls, one `Exit`, one public type and member, no target switch, profile change, settings assignment, reflection, process, network, environment request or Android/iOS code), a Unity-free rules core, no Git and no directory artifact in the Unity adapter, output only in the workspace.
- F: every outcome against the stand-in: inspection, a published build and its manifest, refusal mapping, pre-entry log classification only without a response, started builds without a trustworthy answer (never retried), timeouts, failed builds, post-build checks, a forged token, observed development and profile mode, payload failures and repeated builds.
- G: the payload tree digest's golden vector, change sensitivity, link containment, special files and names, and every bound.
- H: publication order (validate, rename, manifest last), publication failures, no overwrite and revalidation.
- M: public text — project paths relativized, every other absolute path `<path>`, credentials redacted, clipping after cleaning, no raw BuildReport text and no absolute `outputPath` in a result, diagnostic or manifest.
- L: a live session, a held project lock (and an unheld orphan), both lock proofs, the exact Editor version and a reused request id.

The real groups use disposable projects in lab-owned batch-mode Editors; a test-only build testkit (`unity_build_testkit/`) plays the Human's configuration changes, and the test performs the authorized external repository workflow (commits with an isolated Git configuration):

- RB1 classic: the qualified build with three Git reads, Development, a stale token, repeated builds, debug-state refusal, no and missing scenes, a non-active target (WebGL, switched by the testkit; never Android) and a killed build with no manifest.
- RB2 profile: an active macOS Build Profile, its Development, its scripting defines, and the Player Settings override (D-A) and profiler (D-B) refusals.
- RB3: a project whose tests fail builds successfully, and its tests still fail.
- RB4: compile errors before the entry.
- RQ: the alpha.20 composition — a source error, diagnostics, the fix, wait-ready, detach, close, commit, the qualified build — with a live-session conflict and a Human-open Editor's project lock.

No real test builds for Android or touches the user's Gradle or Android state. `test_unity_live_bridge_core.py` also runs `unity_live_bridge_core/BuildCoreTests.cs` (with the canonical-token golden vector shared with Python). `mutate_unity_build.py` mutates the GPOS side, the foundation's request-id guard, the build entry's boundaries and the build rules against the fast suite, the foundation tests and the core tests; with `--real`, it mutates the entry's Editor-side behaviour against RB1 and RB2.

## Player runtime tests

```bash
python3 tests/test_player.py
python3 tests/test_player_helper.py
python3 tests/test_player_real.py
GPOS_PLAYER_CAPTURE_DENIED=1 python3 tests/test_player_real.py RC8_PermissionDenied   # once, before the Human grant
GPOS_REAL_CAPTURE=1 python3 tests/test_player_real.py                               # after the Human grant
python3 tests/build_player_helper.py --check
python3 tests/mutate_player.py
python3 tests/mutate_player_helper.py
```

Tests for Phase 2C-8, the `player` adapter. `test_player.py` needs no Unity and no privacy permission: `player_fakes.py` stands in for libproc, AppKit, the helper's supervise mode (speaking the exact runtime-file protocol) and its capture modes, through the real foundation:

- A: the descriptor, the six declarations, the production registry, the registry allowlists and the permanent external-invocation table; the single-invocation guard.
- B: structural pins — only `invocation.py` starts a process, no other process mechanism, the ctypes library allowlist, exactly three helper modes, no permission request or other privilege anywhere, the preflight before ScreenCaptureKit, a portable contract.
- C: the release manifest, its source digest and the EXACT / ABSENT / UNTRUSTED classification.
- D: the install — the account-database home, EXACT no-op, UNTRUSTED never touched (an empty directory included), raced-in destinations, `renamex_np(RENAME_EXCL)` against every existing kind, a staged copy that does not verify, staging leftovers, dry run, no caller destination.
- E: write-once runtime files, links, bounds and strict shapes; the runtime binding bound to its session.
- F: build resolution, tampering, a re-serialised manifest, a linked workspace.
- G: the launch sequence and every refusal, abandoned and unresolved launches, a foreign instance before and during the launch, drift during the launch.
- H: status with zero processes and no writes; helper facts, gone, reused, moved, drift, unresolved; owners.
- I: screenshot and video through exactly one `/usr/bin/open` with the exact arguments; duration rules; no caller target; the restated build; the permission refusal inside the one invocation; the helper's identity before and after; every helper failure; media rules; trust preconditions.
- J: stop through the supervisor, the fallback (with the helper install gone), the proven kill, unproven and moved executables, drift, log evidence rules, owners and recovery, unresolved launches, the classification table.
- K: the player log sanitizer, including the measured `Application Support` fragment and its control.
- L: the PNG and MP4 validators.
- N: the network semantic.

`test_player_helper.py` runs the real native helper (a fresh copy of the release; the granted install is never touched) without Unity or privacy permission: RH0 the reproducible release, RH1 supervise against a compiled stub Player (argv, environment, working directory, process group, commit, stop intents, abort, the commit deadline, exit codes, the kill of its own child), RH2 every request refusal, RH3 the three-mode table and capture modes that refuse outside LaunchServices, RH4 a 16-way concurrent install on the real filesystem, RH5 the in-process macOS backend against real processes.

`test_player_real.py` builds one QualGame project (`player_testkit/`) with the alpha.21 qualified workflow and runs RP1–RP9 (lifecycle, hang, exits and crash, foreign instances, drift, crash points, the fallback, the helper moved or deleted after launch, persistence) against a scratch helper install; with `GPOS_REAL_CAPTURE=1` and the Human-granted helper, RC1–RC7 (screenshots and downscaling, videos, interrupted videos, a hidden window, privacy controls, the visual persistence proof, the FFprobe/FFmpeg composition); RC8 is the one permission-denied qualification before the grant. The game's own side-effect files are listed at the end of a run, never deleted.

`mutate_player.py` mutates the adapter, the backends, the install, the sanitizer, the validators and (statically) the helper source against the fast suite and RH4/RH5; `mutate_player_helper.py` mutates the helper's supervise and request-validation code, rebuilds the helper in the copy and runs RH1–RH3. The foundation's detached-process and host-location guarantees are mutated by `mutate_tools.py`.

## Fixtures

**Schema fixtures** — `fixtures/valid/*.json` and `fixtures/invalid/*.json` are patches applied to a valid example:

```json
{ "description": "...", "schema": "gate", "base": "examples/example-gate-record.json",
  "set": { "/status": "PASS" }, "remove": ["/required_changes"],
  "expect": "invalid", "expect_keyword": "contains" }
```

The base must itself be valid, so an invalid fixture fails for the reason it describes.

**Authority fixtures** — `fixtures/authority/*.json` pair a project config with Human Decision records (and optionally routing or evidence) and state the expected referential-integrity, lifecycle, authorization or presentation-parity problem. They show that a well-formed but unresolved decision id is not authority.

**Record fixtures** — `fixtures/records/*.json` pair a gate record with evidence records (each schema-valid) and state the cross-record problem expected, or `null` for none: stale revisions, justified and mismatched carryover, superseded evidence, self cross-review, non-counting capture contexts, target-runtime conditions, instrumentation impact, conditional evidence (e.g. turn-based gameplay passing on runtime evidence). They exercise the reference implementation of rules Phase-2 tooling must enforce on real projects ([core/GOVERNANCE.md §12](../core/GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation)).

## What is not checked

Nothing here judges whether the framework is *good*. Tests confirm structure and internal consistency only. Subjective framework quality is a Human Review matter.
