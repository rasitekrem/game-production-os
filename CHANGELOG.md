# Changelog

All notable changes to Game Production OS. Format based on Keep a Changelog; versions follow Semantic Versioning as defined in [README.md § Versioning](README.md#versioning).

Maturity promotions of skills are recorded here, each with the Human Decision and evidence references that authorized it.

## [1.0.0-alpha.15] — Phase 2C-5: Unity engine adapter (batch plane)

Builds on the frozen Phase-2C-4 tree (`v1.0.0-alpha.14`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. The only foundation change is the network semantic below. Projects must pin `gpos_version` `1.0.0-alpha.15`.

The first production engine adapter, batch plane only. The live Editor plane (session, bridge, IPC, capture) is deferred; it is architectural intent, not part of this release.

### Added

- **Foundation: network semantics.** `tool_adapter_policy` gains `network_semantics` (`FORBIDDEN`, `TOOL_INHERENT`) and `network_semantic_adapters` (`TOOL_INHERENT` → `unity` only); `AdapterDescriptor` gains `network_disclosure`. Validation fails closed: `FORBIDDEN` stays the default and takes no disclosure; `TOOL_INHERENT` requires an allowlisted adapter id and a non-empty disclosure; any other value is refused. `TOOL_INHERENT` means GPOS still provides no networking capability, takes no network destination and originates no network operation, while the external tool's own process tree may use the network; no operating-system confinement is claimed. The process boundary gained nothing, and git, ffmpeg, ffprobe, adb and blender stay `FORBIDDEN`.
- `gpos/tools/unity/`: the `unity` adapter (`ENGINE`, `CLI`, `STATELESS`, macOS only, network `TOOL_INHERENT` with a disclosure) with three capabilities:
  - `unity.inspect-project` (`INSPECT`, `READ_ONLY`, `OFFLINE_ANALYSIS`): static project layout, the exact Editor version the project requires, and package-source safety. No Unity process and no installed Editor needed.
  - `unity.run-editmode-tests` and `unity.run-playmode-tests` (`RUN`, `MUTATING`, `AUTOMATED_TEST`, single-writer lease on the resolved project root, dry run): the Unity Test Framework in a fresh batch-mode Editor; `TEST_EVIDENCE` in `AUTOMATED_TEST` only when valid results record at least one executed test.
- Hub-only Editor discovery with exact names (never PATH); exactly one usable Hub Editor in this release (none: unavailable; several: version unsupported). A project must require exactly the probed version: no fallback, upgrade or downgrade.
- A static package-source preflight before any launch: no scoped registries, registry overrides, Git, URL or out-of-scope local dependencies, and no unsupported lock-file sources; the project is never rewritten.
- Package Manager isolation per execution: empty GPOS-owned user and global configuration files and a GPOS runtime package cache.
- A fixed command: never `-quit`, `-accept-apiupdate`, `-noUpm`, `-nographics` or `-executeMethod`; Accelerator upload and download disabled.
- A strict NUnit results reader and a classifier that never reads an exit code alone. Diagnostics `ENGINE_PROJECT_UNSUPPORTED`, `ENGINE_EDITOR_VERSION_UNAVAILABLE`, `ENGINE_PROJECT_LOCKED`, `ENGINE_LICENSE_UNAVAILABLE`, `ENGINE_TESTS_NOT_EXECUTED` and `TESTS_FAILED`, generic to engine adapters.
- [tools/unity-adapter.md](tools/unity-adapter.md), including the side-effect inventory (Unity-managed project files and user-level Unity state) and the security review.
- A real-Unity integration suite (`tests/test_unity_adapter.py`, groups A–Z, Unity 6000.5.8f1, generated projects only, guarded against any unaccepted change to Unity Editor preferences or to the user's Package Manager configuration) and a bounded mutation harness (`tests/mutate_unity_adapter.py`).

### Changed

- `default_registry()` now contains exactly `adb`, `blender`, `ffmpeg`, `ffprobe`, `git` and `unity`.
- Phase-boundary tests evolved to the Phase-2C-5 boundary without weakening any other assertion (the word "unity" only in the Unity package and the registry; the module list; the six-adapter registry and CLI counts; the harness registry anchors). The foundation suite and harness gained the network-semantic tests and mutations.

### Notes

- Research record: the Phase 2C-5 synthetic experiments A–K (A–G, I–K complete; H not run, not required) established the batch contract, including that `-noUpm` prevents the package-provided Test Framework from compiling and that Unity's own project lock is only defence in depth.

## [1.0.0-alpha.14] — Phase 2C-4: Blender DCC inspection and visual-evidence adapter

Builds on the frozen Phase-2C-3 tree (`v1.0.0-alpha.13`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics, to the registry, or to any frozen foundation module. Projects must pin `gpos_version` `1.0.0-alpha.14`.

The first production DCC adapter. It inspects one `.blend` file and renders one authored still of it as DCC evidence about an ASSET. **It is not a Blender automation or Python interface:** the caller never supplies Python, a script, an expression, an operator, a Blender option, an engine, a camera, an executable or an output path, and the `.blend` is never saved.

### Added

- `gpos/tools/blender/`: the `blender` adapter (`DCC`, `CLI`, `STATELESS`, network `FORBIDDEN`) with two capabilities:
  - `blender.inspect-blend` (`INSPECT`, `READ_ONLY`, `OFFLINE_ANALYSIS`): bounded counts and names (scenes, cameras, frame ranges, engines, resolutions, object and geometry counts, datablock counts, external-dependency counts, render-safety observations), never a path, no artifact and no evidence;
  - `blender.render-scene` (`CAPTURE`, `MUTATING`, dry run): one still of the authored scene, camera and frame through a built-in engine to `render.png` → `VISUAL_EVIDENCE` / `DCC_RENDER`, for an `ASSET` subject only. The only inputs are an exact scene_name and a canonical frame.
- A fixed, audited helper (`gpos/tools/blender/helper.py`) that runs inside Blender. It imports only json, os, re, sys and bpy, uses only the open-file and render operators, and emits one nonce-bound machine record. A strict parser (`parser.py`) reads that record from the private raw capture.
- A six-layer safety baseline for every Blender process:
  1. an isolated, per-execution BLENDER_USER_RESOURCES;
  2. `--factory-startup`;
  3. `--disable-autoexec`;
  4. an explicit `open_mainfile(load_ui=False, use_scripts=False)`;
  5. `--offline-mode`;
  6. the fixed helper.

  A probe self-test checks the capabilities these rely on.
- Render refusals, never silent repairs: unknown scene, no authored camera, frame out of range, a load log over 256 KiB (never read in part), an unavailable or non-built-in engine (detected from Blender's complete load report), an oversized resolution, any external dependency, source Python that Blender reports it blocked (`bpy.app.autoexec_fail`: registered text blocks, Python drivers), Freestyle (which runs scripts even with auto-execution off), OSL script nodes, compositor File Output nodes, multi-view, sequencer strips and stamp burn-in. Drivers that Blender evaluates without auto-execution still render.
- A path counts as Blender's own bundled asset only when the file it resolves to lies inside the running installation's resolved datafiles directory and actually exists there; stored path text, and location alone, are never trusted. PNG metadata stamps are switched off in memory, so renders are reproducible and name no file.
- Exact-name executable discovery over a fixed per-platform candidate list. A lookup that succeeds only on a case-insensitive filesystem is rejected, not repaired, and an ambiguous PATH chooses nothing.
- `DCC_SOURCE_NOT_ACCEPTED`, generic to DCC adapters.
- [tools/blender-adapter.md](tools/blender-adapter.md), with the first-party Blender documentation consulted, the security review and the pre-flight user-configuration incident.
- A real-Blender integration suite (`tests/test_blender_adapter.py`, groups A–Z, run on Blender 5.2.0 LTS). Every fixture is generated by `tests/blender_fixture_builder.py`; the suite fails if the real Blender user profile changes. There is also a bounded mutation harness (`tests/mutate_blender_adapter.py`).

### Changed

- `default_registry()` now contains exactly `adb`, `blender`, `ffmpeg`, `ffprobe` and `git`.
- Phase-boundary tests evolved to the Phase-2C-4 boundary, without weakening any other assertion:
  - the word "blender" is allowed only in the Blender package and the registry;
  - the exact module list and the five-adapter registry;
  - each tool is named only in its own package;
  - the Git, media, ADB and foundation suites' registry and CLI-list counts;
  - the registry anchors in the Git, media and ADB mutation harnesses.

### Notes

- The frozen registry already limits `DCC_RENDER` to the `VISUAL_ART` gate at `ASSET` scope. The suite proves this with the real validator: the adapter adds no gate rule.
- Pre-flight incident: an isolation experiment wrongly assumed that HOME redirects Blender's user resources on macOS. It created one startup script in the real 5.2 user profile and switched Auto Run Python Scripts on. The script was removed and the preference was explicitly restored to off; the exact earlier preference file cannot be proven. Production then moved to the official BLENDER_USER_RESOURCES isolation, and no further real-profile change occurred.

## [1.0.0-alpha.13] — Phase 2C-3: Android ADB target-device evidence adapter

Builds on the frozen Phase-2C-2 tree (`v1.0.0-alpha.12`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics, to the registry, or to any frozen foundation module. Projects must pin `gpos_version` `1.0.0-alpha.13`.

The first production target-device adapter. It reads evidence from one explicitly named **physical** Android target through ADB, and **no production capability changes Android target state**.

### Added

- `gpos/tools/adb/`: the `adb` adapter (`DEVICE`, `CLI`, `STATELESS`, network `FORBIDDEN`) with three `MUTATING` capabilities that support dry run. They are mutating only because each writes one evidence file into the host workspace:
  - `adb.capture-device-report` (`CAPTURE`): nine allowlisted system properties as canonical `device.json` → `DEVICE_EVIDENCE` / `TARGET_RUNTIME`;
  - `adb.capture-screenshot` (`CAPTURE`): a PNG streamed with `exec-out screencap -p`, with no file on the device → `VISUAL_EVIDENCE` / `TARGET_RUNTIME`;
  - `adb.capture-meminfo` (`PROFILE`): `dumpsys meminfo -s <package>` for one named, running package → `PERFORMANCE_EVIDENCE` / `PERFORMANCE_RUNTIME`.
- Two identities, never confused:
  - the `adb_serial` input is only the operational selector: the exact local serial, bound with `-s` on every target command. There is no `-d` or `-e`, ANDROID_SERIAL is never inherited, and there is no first-device fallback. Network serials are refused, and wireless auto-connect is disabled for any server the adapter's client starts. The serial is replaced by `<adb-target>` in recorded commands and never enters evidence.
  - `request.device` is the canonical GPOS reference-device identity, `<manufacturer> <model> / Android <release> (API <level>)`. Before any capture the adapter derives it from the selected target and requires an exact match (`TARGET_DEVICE_IDENTITY_MISMATCH` otherwise). A project lists that identity in `reference_devices`, and the unchanged Phase-2A validator counts the evidence; a test proves both the match and the mismatch.
- Physical targets only: `DEVICE_EVIDENCE` is observation on physical hardware, and emulators are not (core/EVIDENCE-RULES.md). A target identified as an emulator by its qemu and hardware properties, or one that cannot be established as physical, is refused with `TARGET_DEVICE_NOT_PHYSICAL` before any capture.
- A closed, read-only command surface: `version`, `get-state`, `getprop sys.boot_completed`, `getprop`, `exec-out screencap -p`, `dumpsys meminfo -s <package>`. The only variable slots are the validated serial and application id.
- Readiness before every capture: `get-state` must be `device`, and `sys.boot_completed` must be `1`. A dry run contacts no target.
- Strict parsers (`gpos/tools/adb/parsers.py`) over the private raw capture:
  - multi-line `getprop` values;
  - a complete-PNG structure check (the image is never decoded);
  - a meminfo check that the output describes exactly the requested process, with totals.
  - A package with no running process is a conflict; zeros are never invented.
- `TARGET_DEVICE_UNAVAILABLE`, `TARGET_DEVICE_NOT_READY`, `TARGET_PROCESS_NOT_RUNNING`, `TARGET_DEVICE_NOT_PHYSICAL` and `TARGET_DEVICE_IDENTITY_MISMATCH`, generic to device adapters.
- [tools/adb-adapter.md](tools/adb-adapter.md), with the first-party Android documentation consulted.
- A real-target integration suite (`tests/test_adb_adapter.py`, groups A–Z, run on 2 physical devices, which capture, and 2 emulators, which are refused, across API 31 and 35) and a bounded mutation harness (`tests/mutate_adb_adapter.py`).

### Changed

- `default_registry()` now contains exactly `adb`, `ffmpeg`, `ffprobe` and `git`.
- Phase-boundary tests evolved to the Phase-2C-3 boundary, without weakening any other assertion:
  - the word "adb" is allowed only in the ADB package and the registry;
  - the exact module list and the four-adapter registry;
  - each tool is named only in its own package;
  - the Git, media and foundation suites' registry and CLI-list counts;
  - the registry anchors in the Git and media mutation harnesses.

### Notes

- Materializing a meminfo record requires the caller to declare `instrumentation`, a supplemental field the frozen foundation leaves to the caller. The result offers a suggested declaration with timing impact `UNKNOWN`.
- ADB is client/server software: an adb client command may start the host ADB server. The adapter never starts, stops or configures it explicitly, and claims no sandboxing.

## [1.0.0-alpha.12] — Phase 2C-2: media evidence adapters (ffprobe, FFmpeg)

Builds on the frozen Phase-2C-1 tree (`v1.0.0-alpha.11`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics, and no change to any frozen foundation module. Projects must pin `gpos_version` `1.0.0-alpha.12`.

Two production media adapters, one per executable, because the foundation records one tool path and version per execution. Both work on local files a caller already has: they capture nothing and never reach a network.

**Media processing does not upgrade capture authority.** A frame, clip or audio segment derived from a `TARGET_RUNTIME` recording is `TARGET_RUNTIME` evidence, never `OFFLINE_ANALYSIS`.

### Added

- `gpos/tools/ffprobe/`: the `ffprobe` adapter (`MEDIA`, `CLI`, `STATELESS`, network `FORBIDDEN`) with one capability, `ffprobe.inspect` (`READ_ONLY`). It returns a bounded, tag-free summary: format names, duration, stream counts, and the first video and audio stream's codec, size, pixel format, frame rate, sample rate and channels. It uses `-show_entries` only, because `-show_format` / `-show_streams` would print the source's metadata tags. The summary is parsed from the private raw capture by a strict parser that refuses truncated, malformed or unexpected output.
- `gpos/tools/ffmpeg/`: the `ffmpeg` adapter (`MEDIA`, `CLI`, `STATELESS`, network `FORBIDDEN`) with three `MUTATING` `TRANSFORM` capabilities that support dry run:
  - `ffmpeg.extract-frame`: a PNG, `IMAGE`, offering `VISUAL_EVIDENCE`;
  - `ffmpeg.extract-clip`: at most 30 s of FFV1 video plus PCM audio in Matroska, `VIDEO`, offering `MOTION_EVIDENCE`;
  - `ffmpeg.extract-audio`: at most 120 s of 16-bit PCM WAV, `AUDIO`, offering `AUDIO_EVIDENCE`.
- Each capability consumes exactly one input artifact. Each transform refuses, before FFmpeg starts, a source with no capture context, or one whose context the frozen registry does not allow for its evidence type. Outputs are `DERIVED` from the input and inherit its capture context, and every candidate states that it is derived.
- `gpos/tools/media_common.py`: the shared local-only input contract, `-protocol_whitelist file` and a closed `-format_whitelist` of self-contained demuxers (`mov`, `matroska`, `avi`, `mpegts`, `wav`, `mp3`, `flac`, `ogg`). A local playlist pointing at a local HTTP server is refused with zero requests, and each whitelist alone blocks it.
- Fixed command templates only. The variable slots are the validated input path, the adapter's workspace output path and canonicalized numbers. Every template uses `-n`, never `-y`, and strips source metadata and chapters.
- The FFmpeg probe confirms the build provides the `png`, `ffv1` and `pcm_s16le` encoders and the `image2pipe`, `matroska` and `wav` muxers, and otherwise reports `VERSION_UNSUPPORTED`.
- Handling of verified FFmpeg behaviour:
  - an existing output is refused first, because `-n` exits 0;
  - frames go through `image2pipe`, because `image2` ignores `-n`;
  - an output at its `-fs` limit, an empty output (a frame past the end) or a wrong format signature is a failure, and the file is kept as an incomplete artifact, never evidence;
  - outputs are byte-reproducible (`-fflags +bitexact`).
- [tools/media-adapters.md](tools/media-adapters.md), with the first-party FFmpeg documentation consulted; real FFmpeg/ffprobe integration tests (`tests/test_media_adapters.py`, groups A–Z) and a bounded mutation harness (`tests/mutate_media_adapters.py`).

### Changed

- `default_registry()` now contains exactly `ffmpeg`, `ffprobe` and `git`.
- `python3 -m gpos.tools execute` exposes the request's existing provenance fields, one option each: `--build-revision`, `--build-id`, `--target-platform`, `--device`. The values are passed unchanged and validated by the foundation as before, and an omitted option stays unknown. The explicit Git → media provenance handoff now works end to end from the CLI. No new foundation semantics.
- A transform's dry run now refuses an output (or symlink) already at the output path, exactly as the real run does, instead of planning success for a run that would be refused.
- The CLI's JSON output echoes the request through the same redaction as the result, so a credential-shaped input file name is not printed back (`gpos/tools/cli.py`, not a frozen module).
- Phase-boundary tests evolved to the Phase-2C-2 boundary, without weakening any other assertion:
  - the Phase-2A test that forbade the word "ffmpeg" anywhere in `gpos/` now allows it only in the two media adapter packages, their shared constants module and the production registry;
  - the Phase-2C-0 tests assert the exact production registry and module list, and that each real tool executable is named only inside its own adapter package;
  - the Phase-2C-1 registry and CLI-list assertions count three adapters;
  - the Git mutation harness's two registry mutations use the new registration anchor, with the same semantics.

### Notes

- No capture of any kind (screen, game, device, microphone, live input), no network input, no arbitrary transcode, filter, codec, map or FFmpeg argument, and no generic command capability.
- No OS sandboxing is claimed: FFmpeg remains an external native parser and decoder under the foundation's existing trust boundary.

## [1.0.0-alpha.11] — Phase 2C-1: Git provenance adapter

Builds on the frozen Phase-2C-0 tool adapter foundation (`v1.0.0-alpha.10`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. The only foundation change is one bounded amendment authorized at Human Review: a private raw capture channel (see Changed). Projects must pin `gpos_version` `1.0.0-alpha.11`.

This is the first production tool adapter. It is local and read-only: it inspects a Git repository and reports an exact revision, and it can neither change a repository nor reach a network.

### Added

- `gpos/tools/git/`: the production Git adapter (`git`, `VERSION_CONTROL`, `CLI`, `STATELESS`, network `FORBIDDEN`), registered by `default_registry()`, which now contains exactly `git`. The TEST_ONLY synthetic adapter still never enters the production registry, and the tool and agent adapter registries stay separate.
- `git.inspect`: the repository state — HEAD, branch or detached HEAD, unborn branch, staged, unstaged, untracked and conflicted counts, and `exact_revision`, which is HEAD only when the tree is clean. A dirty tree is never represented as its HEAD commit.
- `git.resolve-provenance`: the exact commit a caller may hand to later requests as `build_revision`; refused with `REPOSITORY_STATE_CONFLICT` when the tree is dirty or the branch has no commit. The foundation still never infers a revision: the handoff is explicit, and a request that does not pass it records the revision as unknown.
- A real probe through the audited process boundary: Git found on an absolute PATH entry and its version parsed conservatively. It requires Git 2.36.0, the first release that treats `core.fsmonitor=false` as "off" rather than as the name of a program to run.
- A fixed Git surface of three argument vectors, with no caller-supplied argument, no `-c`, no mutation and no network command. Git runs with no terminal prompt, no optional locks (so `status` cannot rewrite the index), no pager, a deterministic message locale and fsmonitor disabled at command scope.
- Repository-root rule: the GPOS project root must be Git's work-tree top level. A nested project fails closed with `REPOSITORY_ROOT_MISMATCH` (`MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED`) without inspecting the enclosing repository; a project outside any repository is `REPOSITORY_NOT_FOUND`. Linked worktrees are supported because Git itself reports their top level.
- State is reported only from complete machine output, parsed from the exact captured bytes by a bytes-native porcelain v2 parser. Truncated output, and anything the parser does not fully understand, is refused.
- `REPOSITORY_NOT_FOUND`, `REPOSITORY_ROOT_MISMATCH` and `REPOSITORY_STATE_CONFLICT`, generic to version control.
- [tools/git-adapter.md](tools/git-adapter.md), with the first-party Git documentation consulted; real-Git integration tests (`tests/test_git_adapter.py`) and a bounded mutation harness (`tests/mutate_git_adapter.py`).

### Hardened (Human Review of Phase 2C-1)

- Submodule ignore settings can no longer produce false clean provenance. `submodule.<name>.ignore = all`, in configuration or `.gitmodules`, hid a dirty submodule completely, so a tree that was not exactly its HEAD commit was reported clean with an exact revision. Status now runs with `--ignore-submodules=none`.
- `core.fsmonitor` is neutralized. A repository could make the adapter's READ_ONLY `git status` run a configured hook program or start Git's fsmonitor daemon — a process no capability authorized. The adapter now disables it at command scope (`GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0`, `GIT_CONFIG_VALUE_0`, fixed and adapter-owned), with no configuration write and no `-c`. The override also reaches the `git status` Git itself runs inside each submodule, closing the same path through a submodule's own hook. Because Git before 2.36 treats `false` as a hook pathname, the minimum Git is now 2.36.0.
- Git output is parsed from the process boundary's private raw capture with a bytes-native parser, so a credential-shaped path or branch name no longer makes a repository uninspectable: counts are exact, and nothing secret-shaped reaches any public surface. The repository-root comparison also uses Git's exact bytes.

### Changed

- **Foundation amendment (bounded, authorized at Human Review):** the process boundary also exposes `raw_stdout` / `raw_stderr`, the exact bytes of the same bounded capture, for adapters that parse a machine protocol. The redacted `stdout` / `stderr` are unchanged. The raw bytes are excluded from `repr` and never reach a result, provenance, diagnostic, CLI output or evidence; raw bytes offered as adapter data are refused, and copied text is still redacted. Every other foundation module is unchanged.
- The Phase-2C-0 tests that asserted "no production tool adapter exists" now assert the Phase-2C-1 boundary exactly: the production registry is `git` and nothing else, `gpos/tools/` holds exactly one production adapter package, and no other real tool executable is named anywhere in `gpos/`.
- `python3 -m gpos.tools` lists the production adapters by default.

### Notes

- No version-control mutation capability exists; any mutation needs a separate Human Review. No hosting-service, media, device, DCC, engine or MCP adapter exists.
- A latent flaky test in the frozen Phase-2C-0 suite is fixed: the determinism test's volatile-timestamp set omitted `generated_at`, so two executions straddling a wall-clock second failed it (reproduced on the untouched `v1.0.0-alpha.10` tree).

## [1.0.0-alpha.10] — Phase 2C-0: tool adapter foundation

Builds on the frozen Phase-1 core (`v1.0.0-alpha.7`), the frozen Phase-2A validator (`v1.0.0-alpha.8`) and the frozen Phase-2B agent adapter layer (`v1.0.0-alpha.9`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. Projects must pin `gpos_version` `1.0.0-alpha.10`.

This phase integrates **no** real production tool. It creates the shared layer that future tool adapters — Git, FFmpeg, target device, Blender, Unity — must use, so that none of them invents its own execution model, capability vocabulary, result structure, provenance model, mutation semantics, single-writer behaviour, timeout behaviour, evidence semantics or failure model.

### Added

- `gpos/tools/`: the tool adapter foundation. Adapter identity and lifecycle (`REGISTERED` to `PROBED` to `READY`, or `UNAVAILABLE` / `INCOMPATIBLE`), a structured capability model, a typed execution request and result, artifacts with streamed hashing and derivation, provenance, evidence candidates, single-writer leases, and one audited process boundary.
- Nine distinct result statuses (`SUCCESS`, `FAILED`, `TIMED_OUT`, `CANCELLED`, `UNAVAILABLE`, `CONFLICT`, `INVALID_REQUEST`, `INCOMPATIBLE`, `INTERNAL_ERROR`), each with its own exit code; a library caller distinguishes them without reading a message, and success is never inferred from an exit code alone.
- The authority boundary in code: a tool adapter offers an evidence candidate, never a gate result. `HUMAN_EVIDENCE` can never be produced by a tool, a dry run may only produce the registry's `dry_run_evidence_types`, and the layer has no field anywhere for a gate, a status, a reviewer or an approval.
- Evidence type and capture context are checked against the frozen registry `evidence_context_compatibility`; the foundation keeps no duplicate matrix and no adapter can override it. A derived artifact inherits its origin's capture context, so processing a capture never upgrades its authority.
- A safe process boundary: resolved executable plus argument vector, no shell, explicit working directory inside the permitted scope, bounded output capture with truncation reporting, monotonic timing, process-tree termination on timeout, an environment allowlist and credential redaction.
- Single-writer leases for `MUTATING` and `STATEFUL` capabilities, atomic and project-local under `.game/gpos-runtime/`, never in the canonical record area. A held lease is never broken automatically; recovery is an explicit, attributable operation.
- `python3 -m gpos.tools list|describe|capabilities|probe|execute` with text and JSON output. There is no command, argv or shell option: only a declared adapter and capability.
- Registry tool vocabulary: `tool_adapter_kinds`, `tool_families`, `tool_operation_classes`, `tool_state_models`, `tool_capability_categories`, `tool_artifact_kinds`, `tool_artifact_classifications`, `tool_probe_statuses`, `tool_adapter_states`, `tool_result_statuses` and `tool_adapter_policy`.
- A `TEST_ONLY` synthetic reference adapter (`gpos/tools/synthetic/`) that exercises the whole foundation without Git, FFmpeg, Android, Blender, Unity or a network. The production registry refuses it, and it is never an enabled adapter.
- [tools/adapter-foundation.md](tools/adapter-foundation.md); tests (`tests/test_tool_foundation.py`) and a bounded mutation harness (`tests/mutate_tools.py`).

### Changed

- The Phase-1 production boundary now names one audited location for process execution (`gpos/tools/process.py`) instead of forbidding it everywhere in `gpos/`. The ban on depending on tests or on network modules is unchanged and still applies to every module.

### Hardened (code review of Phase 2C-0)

- Materialized evidence provenance comes from the execution, never from the caller. Everything the foundation observed flows automatically out of the candidate into the record; a caller may add only the schema-supported fields the foundation cannot observe, and setting a foundation-owned field to a different value — or to one this execution never observed — fails closed.
- A capability that does not require a project never gains filesystem authority over a project tree: supplying `project_root` to it is refused instead of quietly becoming an unvalidated scope.
- The project id in provenance comes from the record set the validator already produced. Execution timing is one foundation-observed interval shared by the result, the provenance and every accepted candidate, measured monotonically and never taken from the inner process. The foundation, not the adapter, establishes an accepted candidate's `generated_at`.
- Canonical request fields — subject kind and reference, supplied revision, actor kind and id, target platform, expected evidence pairs — are validated against the registry before any adapter runs, so an invalid subject can never reach an accepted candidate.
- Every string an adapter controls is redacted, not only captured process output: diagnostics and details, result data, recorded command and environment, artifact descriptions, evidence summaries, limitations and notes, and probe text including an exception message. A credential-named key or command-line flag redacts its value; a value a result cannot carry is refused rather than stringified.
- Output artifact claims are checked against the registered capability at execution time: declared kinds only, structural and unique ids, and no shadowing of an input artifact. Invalid claims are dropped, never renamed or reclassified.
- A single-writer lease that cannot be released is blocking (`LEASE_RELEASE_FAILED`), so an execution never reports a clean success while its lease is still held; the unverified lease is still not deleted. A project lease is taken on the resolved project root, and a capability that leases something else declares `resource_from_request` and the request names it explicitly.
- The CLI takes an input artifact's path and capture context as separate options, so a Windows drive path is unambiguous. A process that cannot be started after its spec validated is reported as a tool availability problem, not an opaque defect.
- A dry run creates no execution workspace and no parent of one. A caller-named output directory is resolved without being created and checked before anything is made, the workspace for a real execution is created only after validation succeeds, and a creation failure is a structured `WORKSPACE_NOT_USABLE` result instead of an exception escaping the call.

### Notes

- No Git, GitHub, FFmpeg, Android/ADB, Blender, Unity or MCP adapter exists. No agent is invoked and no network is used at runtime.
- `RUNTIME_NOT_YET_SMOKE_TESTED` continues to apply to the Phase-2B agent adapters.

## [1.0.0-alpha.9] — Phase 2B: agent adapter layer

Builds on the frozen Phase-1 core (`v1.0.0-alpha.7`) and the frozen Phase-2A validator (`v1.0.0-alpha.8`). No change to gate, evidence, authority, routing, lifecycle or validator semantics. Projects must pin `gpos_version` `1.0.0-alpha.9`.

### Added

- `gpos/adapters/`: model-independent agent adapter layer. Explicit source selection and hashing, one agent-independent IR, all GPOS meaning composed in one module, and format-only backends for Claude Code (`CLAUDE.md`, `.claude/skills/gpos-*/`) and Codex (`AGENTS.md`, `.agents/skills/gpos-*/`).
- `python3 -m gpos.adapters render|sync|check` with exit codes 0 OK, 1 INVALID, 2 DRIFT, 3 ERROR, 4 CONFLICT.
- Manifests with provenance (source ids and sha256, per-file sources and semantic blocks) and a `semantics` parity block; deterministic, without timestamps.
- Strict ownership: human-written entry files are never overwritten or adopted, the managed area is limited, paths are safe, sync is atomic and idempotent, and drift is detected without regenerating.
- Context budgets and progressive disclosure: a concise root, one skill per specialist, workflow references loaded on demand. Monolithic renders are rejected.
- Registry `adapter_ids`, `adapter_extension_key`, `agent_operating_contract` and `project_lock_binding`; `adapters/README.md`, `adapters/claude-code.md`, `adapters/codex.md`.
- Tests (`tests/test_adapters.py`), a synthetic adapter project, layout and root snapshots, and an adapter mutation harness.

### Hardened (Human Review of Phase 2B)

- Project Locked Authority is bound to its Human Decision. A `LOCK` decision's structured `value.locks` must target the exact row and current value, or the document's canonical hash (registry `project_lock_binding`). An authorized decider, `ACTIVE` status and this project as subject are also required; otherwise generation fails closed (`AUTHORITY_LOCK_UNVERIFIED`). A read-only `authority` command prints the exact bindings.
- Strict authority-document parsing: malformed, duplicate or near-miss authority rows and metadata fail (`AUTHORITY_DOCUMENT_INVALID`) instead of being skipped.
- Unmanaged project instruction layers are detected and block sync and check (`INSTRUCTION_LAYER_CONFLICT`): Codex nested or override `AGENTS` files; Claude Code `.claude/CLAUDE.md`, `CLAUDE.local.md`, nested `CLAUDE.md`, `.claude/rules/` and unowned `AGENTS.md`. Generated text no longer claims such layers cannot relax GPOS.
- Project runtime configuration that changes instruction discovery blocks sync and check (`INSTRUCTION_CONFIG_CONFLICT` / `INSTRUCTION_CONFIG_UNREADABLE`):
  - Claude Code project or local `claudeMdExcludes`;
  - Codex project `.codex/config.toml` `model_instructions_file`, any `project_root_markers`, a `project_doc_max_bytes` below the generated root, `[[skills.config]]` disabling a generated skill;
  - `project_doc_fallback_filenames` matching unowned files.
- Project-local skills occupying a generated skill id are rejected (`SKILL_ID_CONFLICT`).
- Authority tables must belong to a `##` section.
- The user-level, admin and global agent configuration trust boundary is documented.
- Project-scoped generated skill ids, `gpos-<namespace>-<skill>`: deterministic, 64 characters at most. Manifests map them to the logical ids.
- The rules generated instructions state come from registry `agent_operating_contract`: statements with verbatim-quoted frozen sources, whose documents are hashed as sources. `content.py` is a renderer and authors no rule.

### Changed

- The Phase-2A/Phase-1 boundary allows `adapters/*.md` documentation; code stays in `gpos/` and `tests/`. The vocabulary check accepts registered adapter ids.

### Maturity

- No skill promotions. All 13 skills `DRAFT`; adapters copy maturity and never change it.

## [1.0.0-alpha.8] — Phase 2A: production validator

Builds on the frozen Phase-1 core (`v1.0.0-alpha.7`). No change to gate, evidence, authority, routing or lifecycle semantics; records valid under alpha.7 remain valid, but must pin `gpos_version` `1.0.0-alpha.8` to be validated.

### Added

- `gpos/`: deterministic, read-only, fail-closed production validator (standard library only). Library API `load_project_record_set`, `validate_project`, `validate_routing`, `evaluate_readiness`; CLI `python3 -m gpos.validator validate|readiness` with exit codes 0/1/2/3 and text or JSON output.
- Project record bundle convention `.game/gpos/` (project config, decisions, routings, gates, evidence).
- Structured diagnostics with stable codes, severities (`ERROR` for record validity, `BLOCKER` for routed readiness), JSON pointers, files, related ids and rule references.
- Every GOVERNANCE §12 requirement (1–44) enforced and mapped to codes and tests ([tools/validator/README.md](tools/validator/README.md)).
- Production tests: parity with the frozen reference model on every Phase-1 authority and record fixture, readiness parity, named adversarial regressions, read-only, determinism, CLI and performance tests; synthetic bundles; production mutation harness.

### Changed

- Phase-1 boundary tests assert the Phase-2A boundary (code only in `tests/` and `gpos/`; `tools/` documentation only; `adapters/` unchanged).
- The vocabulary check accepts validator diagnostic codes and verdicts.

### Semantic alignment (Human Review of Phase 2A)

- Routed readiness is scoped: project-global authority plus the routing's own dependency records. Errors in unrelated routings no longer block it; `validate` still reports the whole project, and readiness reports them only as `project_has_other_diagnostics`.
- A project pinned to another GPOS version raises `UnsupportedGposVersion` (`UNSUPPORTED_GPOS_VERSION`, severity `INCOMPATIBLE`, category `COMPATIBILITY`, CLI exit 3); it is no longer reported as invalid records.
- Timestamps follow the frozen RFC 3339 contract: lower-case `t` / `z` are accepted, as by the Phase-1 oracle and the `jsonschema` cross-check. The stricter behaviour of `rfc3339-validator` called directly is recorded as a known external-checker divergence.
- Four machine-readable result classes with distinct exit codes: `VALID`/`READY` (0), `INVALID` (1), `NOT_READY` (2), `INCOMPATIBLE` (3). `ReadinessResult.status` and the CLI `verdict` report `INVALID`, never `NOT_READY`, when the routing's scope has errors (fixes `NOT_READY` with exit 1).
- The `ERROR` / `BLOCKER` split is documented as the normative Phase-2A diagnostic convention. Parity is required on verdicts and frozen rules, not on diagnostic counts.

### Normative contract enforced beyond the reference oracle (approved at Human Review)

- §12.6: a superseded negative cross-review needs a later review by the same reviewer.
- §12.12: `HUMAN_EVIDENCE` cited by a gate must come from a human authorized to review that gate.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.7] — Phase 1 freeze candidate

Independent review of alpha.6: three production-level contract gaps closed. Incompatible with alpha.6 records.

### Added

- Machine-readable cross-review eligibility (registry `cross_review_eligibility`, identical to the ROLE-ROUTING table) and `never_cross_reviewer` (`game-director`); non-standard reviewers escalate to `HUMAN_REVIEW_REQUIRED`.
- Release all-gate accounting (`account_for_all_gates` for `release`).
- Shared target-platform vocabulary (registry `platforms`) for project config and evidence; target-runtime evidence bound to declared project platforms; `DEVICE_EVIDENCE` bound to declared reference devices.
- Release PRIMARY-platform coverage for `DEVICE` and `PERFORMANCE` (registry `release_primary_platform_coverage`).
- GOVERNANCE §12 requirements 38–44.

### Breaking

- `provenance.target_platform` is an enum of the project platform vocabulary (free text rejected).
- A cross-review by `game-director` is invalid (schema).
- Release routing must account for all 12 quality gates.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.6] — Phase 1 routing authority closure

Independent review of alpha.5: five routing/authority gaps closed. Incompatible with alpha.5 records.

### Added

- Effective review policy (registry `effective_review_policy_precedence`, `review_policy_strength`): mandatory triggers, then applicable project override, then routing; routing may be stronger, never weaker.
- Workflow gate requirements (registry `workflow_gate_requirements`): `always_required` / `when_affected` per workflow, mirrored in each workflow's REQUIRED GATES section; Golden Cell `account_for_all_gates`.
- Routing evidence contract: required evidence must be valid for the gate and is enforced against the linked gate's counting evidence.
- Routed cross-reviewer authorization for linked gates.
- Complete conditional-evidence accounting (applied or declined, exactly once); presentation parity `YES` forces `TARGET_PRESENTATION_DIFFERS`.
- GOVERNANCE §12 requirements 29–37.

### Breaking

- Review-policy override `scope` (config and `REVIEW_POLICY_OVERRIDE` decision value) is now an object `{kind, ref}` matched against the routing subject.
- Routing records must account for every condition of every required gate and include every workflow always-required gate.
- `routed_scope_ready` (reference model) takes the evidence record set.

### Fixed

- Golden Cell: irrelevant gates are omitted from routing with a reason instead of being recorded `NOT_APPLICABLE`, consistent with routing-aware readiness.
- `character-production`: `HUMAN_REVIEW` listed as a when-affected gate (conditional trigger), not always required.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.5] — Phase 1 final local hardening (binding and readiness)

Independent adversarial review of alpha.4: cross-record binding and readiness integrity hardened. Incompatible with alpha.4 records.

### Added

- Evidence subject binding (`evidence.subject.kind`) and explicit, accountable `evidence_applicability` for cross-subject reuse.
- Routing `subject`; gate `routing_ref`; routing↔gate linkage checks and routing-aware readiness (`routed_scope_ready`).
- Generic decision-reference resolution for every record type (`resolve_decision_refs`).
- `BLOCKING_DOWNGRADE` / `KNOWN_ISSUE_ACCEPTANCE` payloads bound to the gate and its scope; registry `decision_subject_rules` for config-level decisions.
- Project-specific mandatory triggers as structured ids, referenced in routing as `PROJECT:<id>`.
- GOVERNANCE §12 requirements 19–28.

### Breaking

- `evidence.subject` requires `kind`; routing requires `subject`.
- `cross_reviews[].reviewed_revision` is required.
- `ROUTINE` `PASS` requires the owning specialist as assessor with a `PASS` assessment.
- `human_review.additional_mandatory_triggers` entries are objects with `id` and `description`.
- Blocking-downgrade decisions must carry `value.gate` and the downgraded scope as subject.

### Fixed

- Evidence about another subject no longer proves a gate because revisions match.
- Fake decision references in gate and routing records are no longer ignored.
- Readiness no longer ignores missing required gates.
- Presentation-parity checks apply to every routing, not only when one routing exists.
- `character-production`: `CANONICAL_CREATIVE_ASSET` is now conditional (only when canon is created or changed), matching the registry, which lists no unconditional trigger for that workflow.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.4] — Phase 1 final hardening (authority and referential semantics)

Independent review of alpha.3: architecture accepted in principle; authority and referential semantics hardened. Incompatible with alpha.3 records.

### Added

- Kind-specific decision payloads (registry `decision_payloads`) and value bindings (registry `decision_value_bindings`): `QUALITY_TARGET`, `REVIEW_POLICY_OVERRIDE` (gate, policy, scope), `EDITOR_CONCURRENCY`, `GPOS_UPGRADE` (from/to versions), alongside existing `PRESENTATION_PARITY` and `LIFECYCLE_TRANSITION`.
- GPOS upgrade authority: `gpos_upgrade` in project config (`from_version`, `decision_ref`).
- Record-id uniqueness model (registry `record_id_uniqueness`) and record-set reference validation (`record_set_problems`, `index_unique`).
- Gate-aware Human Review authorization (`human_may_review`).
- Assertive RFC 3339 `date-time` checking in the framework validator; the `jsonschema` cross-check runs with its format checker.
- Carryover `proposed_by`.
- GOVERNANCE §12 requirements 13–18.

### Breaking

- Decisions of payload-carrying kinds must include their structured value.
- Evidence carryover must be approved by the gate owner or a human; CI, tools and devices are rejected.
- Identifiers used as cross-record keys (actor, human, authority and reviewer ids) must be non-empty and contain no whitespace.
- `quality_target` values come from registry `quality_targets`.

### Fixed

- The reference routing model no longer requires a specialist reviewer for `HUMAN_REVIEW_REQUIRED` gates unless `cross_review_required` is true.
- Reference lookups no longer silently use last-write-wins on duplicate ids.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.3] — Phase 1 corrections (independent review)

Independent Human architecture review of alpha.2: Phase 1 not accepted; corrections below. Incompatible with alpha.2 records.

### Added

- `game-engineering` specialist (13th skill): owns runtime software implementation; owns no gate — `qa-performance` stays the default `TECHNICAL` owner so implementation and verification are separate.
- `runtime-system` workflow and `templates/ENGINEERING.md`.
- Evidence type / capture-context compatibility model (registry `evidence_context_compatibility`, schema-enforced).
- Human Decision record: `schemas/decision.schema.json`, `examples/example-decision-record.json`; registry `decision_kinds` and `decision_ref_fields`.
- Lifecycle transition authority: `lifecycle_decision_ref`, registry `lifecycle_transitions`.
- Authorization model: `decision_authorities` and reviewer gate lists in project config.
- Gate scope kinds `MILESTONE`, `PROJECT`, `DECISION`; registry `trigger_scope_kinds`.
- Accountable assessor model (`assessed_by` = owner or human; `recorded_by`; CI/tools never assess).
- Authority fixtures and expanded Phase-2 referential-integrity and authorization requirements (GOVERNANCE §12).
- Documented future considerations (Golden Cell set, asset licensing provenance, further specialist domains).

### Breaking

- Gate records: `assessed_by` limited to `AGENT` (the owner) or `HUMAN`; required unless `NOT_RUN`; `CROSS_REVIEW_REQUIRED` `PASS` requires owner assessment `PASS` and no unresolved negative cross-review; `HUMAN_REVIEW_REQUIRED` requires cross-review only when `cross_review_required`, and disclosure of disagreements; `blocking_downgrade_ref` must be a decision id.
- Evidence records: capture context must be compatible with the evidence type.
- Project config: `decision_authorities` required; `lifecycle_decision_ref` required after `CONCEPT`; decided presentation parity requires a decision; all authority references use decision ids; `editor_concurrency.validated_workflow_ref` replaced by `decision_ref`.
- Blender adapters no longer listed as producing game `RUNTIME_EVIDENCE`.

### Fixed

- `game-director` no longer plans Human Review for every subjective gate — only `HUMAN_REVIEW_REQUIRED` gates and mandatory triggers.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.2] — Phase 1 final hardening

Human Review of alpha.1: architecture accepted with final hardening. The changes below are incompatible with alpha.1 records, so the pre-release number was incremented ([core/GOVERNANCE.md §6](core/GOVERNANCE.md#6-semantic-versioning)).

### Added

- Proportional review policy: `ROUTINE`, `CROSS_REVIEW_REQUIRED`, `HUMAN_REVIEW_REQUIRED`, with per-gate defaults in the registry.
- Seven mandatory Human Review triggers that nothing can relax; workflow-level triggers for `new-game`, `golden-gameplay-cell`, `release`.
- Base plus conditional evidence model with nine named evidence conditions.
- Capture-context vocabulary (`DCC_RENDER`, `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `PERFORMANCE_RUNTIME`, `OFFLINE_ANALYSIS`, `AUTOMATED_TEST`, `HUMAN_RECORD`) and evidence provenance (subject revision, build revision/id, artifact hash, target platform, device, tool version, instrumentation, supersedes).
- Revision-bound gate records and explicit, justified evidence carryover.
- Default visual responsibility split (lighting intent / environment lighting composition / lighting implementation / readability cross-review); primary visual owner for mixed scopes.
- `core/GOVERNANCE.md`, including the Phase-2 record-validation acceptance requirement.
- Human-evidence trust-boundary section in `core/HUMAN-AUTHORITY.md`.
- Record-set fixtures for staleness, supersession, context and instrumentation rules.

### Breaking

- Gate records: `human_review_required` and `human_review_waiver_ref` replaced by `review_policy` (+ `routine_basis`); `scope.version` replaced by required `scope.revision`; subjective `PASS` rules now follow the review policy.
- Routing records: per-gate `review_policy` required; `review_triggers` required; `applied_conditions` / `unapplied_conditions` added; `VISUAL_ART` requires an explicit owner.
- Evidence records: `environment` replaced by `provenance`; `subject.version` replaced by `provenance.subject_revision`; context vocabulary replaced.
- Project config: `subjective_gates_require_review` and `gate_overrides` replaced by `review_policy_overrides` and `additional_mandatory_triggers`; `presentation` added.
- Registry gate rules: `required_all` / `required_any` / `conditional` replaced by `base_evidence` / `conditional_evidence`; `GAMEPLAY_DESIGN` no longer requires motion evidence universally.

### Maturity

- No skill promotions. All skills `DRAFT`.

## [1.0.0-alpha.1] — Phase 1: Core architecture

### Added

- Core documents: principles, human authority, authority hierarchy, quality gates, evidence rules, role routing, production lifecycle, Golden Gameplay Cell, skill maturity, definition of done.
- `core/registry.json`: machine-readable canonical vocabulary (gates, statuses, evidence types, skills, workflows, lifecycle stages, per-gate evidence rules).
- Twelve specialist skill contracts, all `DRAFT`.
- Twelve workflow specifications.
- Fourteen project authority templates plus a templates guide.
- JSON Schemas: project config, task routing, gate record, evidence record.
- Generic examples for every schema.
- Standard-library framework validator with fixtures.
- Placeholder boundaries for adapters and tools.

### Maturity

- No skill promotions. All skills `DRAFT`.
