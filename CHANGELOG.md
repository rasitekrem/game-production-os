# Changelog

All notable changes to Game Production OS. Format based on Keep a Changelog; versions follow Semantic Versioning as defined in [README.md § Versioning](README.md#versioning).

Maturity promotions of skills are recorded here, each with the Human Decision and evidence references that authorized it.

## [1.0.0-alpha.24] — Phase 2C-9.2: Windows qualification of the production tools

Builds on the frozen Phase-2C-9.1 tree (`v1.0.0-alpha.23`, `a025d51`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics, no new capability, no change to the Unity Editor bridge (`com.gpos.live-bridge` 1.5.0, `gpos.unity.live/5`, 67 files, digest `b7f4775d…`), to Unity Build Core or to the Player Runtime, and the production registry stays at seven adapters. Projects must pin `gpos_version` `1.0.0-alpha.24`.

Qualified on Windows 11 Enterprise 10.0.26200 with CPython 3.14.8 x64 only. **Qualified for production on Windows:** FFmpeg and ffprobe (9.0.2, gyan.dev full build) and Blender (5.2.2 LTS only). **Not declared on Windows:** Git (pending the repository-filter decision D-G1) and ADB (no physical target or server lifecycle qualified). Unity and the Player stay macOS-only.

### Added

- **Windows tool discovery** (`gpos/tools/executables.py`): on Windows every production adapter finds `<name>.exe` in the absolute PATH directories, in order — never the current directory, never a relative, drive-relative or UNC entry, never a `.cmd` or `.bat` shim (which `shutil.which` would return there). The process boundary still validates and pins what it finds. POSIX keeps `shutil.which`.
- **Blender on Windows (D-B2, D-B3):** each Blender process gets a private temporary directory and private per-user and machine data directories inside its user root (TEMP, TMP, TMPDIR, APPDATA, LOCALAPPDATA, ProgramData), created and proven to be plain directories before launch; without them the NVIDIA driver wrote `NVIDIA Corporation/umdlogs` into the render workspace. The private root's removal is verified, never assumed: a residue is reported as `DCC_CLEANUP_INCOMPLETE` (`INFO`, a new code). Only the 5.2.x family is accepted on Windows, and the version `--version` prints must equal Blender's own report exactly; macOS and Linux keep the alpha.22 rule.
- **Tests:** a test-only tool stand-in for Windows (`tests/windows_standin.py`, compiled from `tests/fixtures/windows-standin/StandIn.cs` by the .NET Framework compiler that ships with Windows; no executable committed) and `tests/test_windows_standin.py`; W09 (tool discovery) in `tests/test_windows_foundation.py`; Blender groups WA and WB; the ADB suite's offline mode; a measured media repeatability test; P08 in `tests/test_posix_parity.py`; discovery, Blender and ADB mutations.

### Changed

- **Blender render path templates (D-B1, every host):** a render whose workspace output path contains `#`, `{` or `}` is refused before Blender starts, because Blender would expand them into another file name. A `.blend` source whose path contains them is still inspected.
- **ADB server failures (every host):** a failed `get-state` whose output names a host ADB server failure is a tool failure (`FAILED`), never `TARGET_DEVICE_UNAVAILABLE` or `TARGET_DEVICE_NOT_READY`.
- **Git and ADB no longer declare `WINDOWS` (D-W1):** the registry refuses them on Windows with `PLATFORM_UNSUPPORTED` before any process starts; their macOS and Linux declarations are unchanged. The media and ADB Git handoffs therefore cannot run on Windows.
- `tests/mutation_gate.py`: `qualify` can name why a mutation is `NOT_RUN` (an offline ADB run).

### Notes

- **Open finding D-G1 (every host):** `git status` runs a repository's configured filter driver (`clean` or `process`) for a file whose attributes select it, so `git.inspect` and `git.resolve-provenance` can start a program the repository chose. Neutralising it needs the driver names, which needs a command outside the fixed Git surface; that choice is left to Human Review ([tools/git-adapter.md](tools/git-adapter.md#repository-filter-drivers-open-finding-every-host)). macOS keeps the alpha.22 behaviour.
- **ADB on Windows:** a server the adb client starts is a process of the client's Job Object and is terminated with it (alpha.23 D9), so there is no server lifecycle to rely on. A GPOS-owned lifecycle is proposed for review, not implemented ([tools/adb-adapter.md](tools/adb-adapter.md#proposed-gpos-owned-server-lifecycle-future-not-implemented)). No physical device was used.
- Media repeatability is measured for one host, FFmpeg build, input and settings; it is not a promise across builds, platforms or hardware.
- macOS was not executed for this release. The shared changes (D-B1, the ADB server classification, the two descriptors) are listed and proven exact by source parity (`tests/test_posix_parity.py` P08), which is not a macOS PASS.

## [1.0.0-alpha.23] — Phase 2C-9.1: Windows Foundation Core

Builds on the frozen Phase-2C-8 tree (`v1.0.0-alpha.22`, `92ca10b`). Windows is now the primary development and production platform; macOS keeps its alpha.22 behaviour. No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics, no change to the Unity Editor bridge (`com.gpos.live-bridge` 1.5.0, `gpos.unity.live/5`, 67 files, digest `b7f4775d…`), to Unity Build Core or to the Player Runtime capabilities, and the production registry stays at seven adapters. Projects must pin `gpos_version` `1.0.0-alpha.23`.

Qualified on Windows 11 Enterprise 10.0.26200 with CPython 3.14.8 x64 on local NTFS only; no other interpreter or operating-system combination is claimed. The tool adapter foundation now runs natively on Windows. The production adapters on Windows (git, FFmpeg, ffprobe, adb, Blender) are qualified in 2C-9.2; Unity and the Player stay macOS-only.

### Added

- **The Windows process backend** (`gpos/tools/process_win32.py`, a private part of the one audited process boundary; only `process.py` imports it): `CreateProcessW` with the validated `.exe` as `lpApplicationName`, a writable Unicode command line, `STARTUPINFOEX` with `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` (exactly the three standard handles) and `PROC_THREAD_ATTRIBUTE_JOB_LIST`, created `CREATE_SUSPENDED` inside a fresh unnamed Job Object (`KILL_ON_JOB_CLOSE | DIE_ON_UNHANDLED_EXCEPTION`, no breakaway), with `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` and an explicit Unicode environment and working directory. Job membership (`IsProcessInJob`) and image identity (`QueryFullProcessImageNameW` against the pinned executable) are proven before `ResumeThread`; anything unproven is terminated before any of its code ran. The readers run before the child resumes. A timeout terminates the owned job (no console signal); whatever the tool leaves running when it exits is terminated with its job (D9), and containment is reported only when the job was observed empty. The executable is pinned (`FILE_SHARE_READ` only) and the working-directory chain is pinned through creation.
- **Process integrity at every boundary:** `ProcessOutcome.tree_contained`, `capture_complete`, `descendants_terminated` and `integrity_ok` (defaults on POSIX); every `run_process` outcome passes through one observer (`process.observe`), and an unproven one never carries an exit code. `execute()` and `ToolRegistry.probe()` judge every process an execution or a probe started (through `ExecutionContext.run` or directly): an unproven one is never a success or a usable tool, withholds evidence and leaves artifacts incomplete, whatever the adapter reported. New codes `PROCESS_TREE_NOT_CONTAINED` (`OUTCOME_UNKNOWN`), `PROCESS_CAPTURE_INCOMPLETE` (`FAILED`) and `PROCESS_DESCENDANTS_TERMINATED` (`INFO`).
- **Windows liveness** (`process.host_pid_alive`): `OpenProcess` + `WaitForSingleObject`, never `os.kill(pid, 0)` (a console control event on Windows); an inaccessible process counts as alive.
- **Windows file-system containment** (`gpos/tools/paths_win32.py`): drive-letter paths only (no UNC, device, mapped network drive or SUBST alias), no alternate data stream, reserved device name, trailing dot or space or `<>"|?*`; every component from the volume root down, scope roots and their ancestors included, opened without following reparse points, refused if it is one and required to be spelled canonically (8.3 names refused); local NTFS only (D10, D11, D8). Artifacts are hashed through one handle that excludes writers (`FILE_SHARE_READ`), never follows a reparse point and refuses hard-linked aliases; workspaces and lease directories are created inside pinned, proven chains, and a letter-case variant of a workspace is refused (D4).
- **Windows leases:** created with `CREATE_NEW` without following a reparse point, read without following one, released, session-released and broken by verifying the content through the delete handle and deleting through it; a sharing violation is a structured failure, never retried (D6). No NTFS ACL privacy is claimed.
- **Windows environment** (D5): the default policy also inherits `SystemDrive`, `WINDIR`, `USERPROFILE`, `HOMEDRIVE` and `HOMEPATH`; names are case-insensitive and set once; ambiguous or malformed names are refused.
- **Refusals on Windows:** a batch file, script, extensionless file, app-execution alias or other reparse point is never started; shells and script hosts are refused by name; detached processes are unavailable (D3); there is no host location; request ids Windows cannot name are refused.
- **`.gitattributes`:** text is LF in every checkout whatever `core.autocrlf` says; the Unity bridge and the Player helper release are never converted.
- **Tests:** `tests/test_windows_foundation.py` (W01–W08, Windows only), `tests/test_posix_parity.py` with `tests/posix_parity.py`, `tests/generate_posix_parity.py` and `tests/fixtures/posix-parity-alpha22.json` (alpha.22 POSIX source parity), `tests/test_mutation_gate.py` and `tests/mutation_gate.py`; foundation groups V01 (process integrity) and V02 (the static Windows boundary); Windows mutations in `tests/mutate_tools.py`.

### Changed

- **The platform gate fails closed** (D12): an unrecognised host is `PLATFORM_UNSUPPORTED`, and `ToolRegistry.probe()` never invokes an adapter on a platform it does not declare.
- **Every mutation harness runs behind a baseline gate:** the unmutated suite must pass in an identically prepared copy before any mutant runs; a red, crashed, timed-out or unrun baseline is `BLOCKED` (exit 2) with nothing counted. Edits are applied byte for byte; on Windows each suite runs in its own hidden console. All 16 harnesses are gated; this release qualifies `mutate_tools`, `mutate_production` and `mutate_adapters`.
- **The CLI** writes UTF-8 with LF on Windows. The Windows test command is `python -X utf8 tests/<suite>.py`; every in-scope suite refuses to run on Windows without UTF-8 Mode (`WINDOWS_UTF8_MODE_REQUIRED`).
- `unity/project_lock.py` imports on Windows (`fcntl` and the `O_*` flags are optional there); macOS behaviour is unchanged.
- An artifact outside the project is recorded with a `/`-separated path on every host.

### Notes

- **Windows/POSIX difference (D9):** on Windows, processes a tool leaves running when it exits are terminated with its job and recorded (`PROCESS_DESCENDANTS_TERMINATED`); POSIX leaves them. The adb server lifecycle is reviewed with the adb qualification (2C-9.2).
- The native `CreateProcessW` call cannot be interrupted: its duration counts against the deadline, and a child created after the deadline is terminated before it runs, but the call itself is not bounded by GPOS.
- A reader blocked by a pipe handle that a process outside the job duplicated cannot be cancelled; it is abandoned after the drain bound and the capture is reported incomplete.
- A process started through WMI, COM, a service or the Task Scheduler is not a member of the job (Microsoft documents this for `Win32_Process.Create`); this is the counterpart of a POSIX double-fork.
- Liveness by process id is advisory (ids are reused); it never breaks a lease.
- macOS was not executed for this release: the POSIX paths are covered by source parity (`tests/test_posix_parity.py`) only, which is not a macOS PASS.

## [1.0.0-alpha.22] — Phase 2C-8: Runtime / Deploy / Capture Core

Builds on the frozen Phase-2C-7 tree (`v1.0.0-alpha.21`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics, no change to the Unity Editor bridge (`com.gpos.live-bridge` 1.5.0, `gpos.unity.live/5`, 67 files, digest `b7f4775d…`) and no change to the alpha.20 or alpha.21 sanitizers. Projects must pin `gpos_version` `1.0.0-alpha.22`.

A new engine-neutral production adapter, `player` (`DEVICE` / `DEVICE`, macOS only), runs exactly one already-built game on this host and observes it. alpha.22 resolves one build kind — an alpha.21 Unity macOS application bundle — and has one backend, macOS. Its one external tool is the native **GPOS Player Helper**, shipped as release content. Excluded (deferred): Windows and every other backend, Android and device deployment, `TARGET_RUNTIME`, device, performance, persistence and human evidence, audio of any kind, input injection, display or area capture, a relaunch capability, a reset-save capability, inspecting or deleting the game's save, preference or log folders, and Developer ID signing of the helper.

### Added

- **Foundation — detached processes** (`process.DetachedProcessSpec`, `process.spawn_detached`, `ExecutionContext.spawn_detached`, `Capability.detached_spawn`): one process that outlives its execution, validated by the same boundary rules as every process (absolute executable, no shell or program string, argument vector, explicit working directory in scope, explicit environment policy), started in its own session with null standard streams and reaped by a daemon thread; reachable only from a capability that declares it and that the registry allowlists (`tool_adapter_policy.detached_spawn_capabilities`: `player.launch`), never in a dry run, at most once per execution. No request field names its executable, arguments, directory or environment.
- **Foundation — host locations** (`tool_host_locations`, `tool_adapter_policy.host_location_capabilities`, `Capability.host_location`, `ExecutionContext.host_location`): `USER_APPLICATIONS_GPOS` resolves to `<home>/Applications/GPOS` from the account database (never `HOME`) and is added to the scopes of `player.install-capture-helper` only.
- **Registry:** `player` joins the `TOOL_INHERENT` allowlist (`["unity", "player"]`) with its disclosure: GPOS accepts no URL, host, endpoint, proxy or credential and originates no network operation; the launched game may use the network; no operating-system confinement is claimed.
- **The GPOS Player Helper** (`gpos/tools/player/helper_src/`, `helper_release/GposPlayerHelper.app`, `helper_release/manifest.json`): version 1.0.0, `com.gpos.player-helper`, ad-hoc signed, universal arm64 + x86_64, macOS 14 or later; exactly three modes — `supervise`, `shot`, `video` — each given exactly one GPOS-written request file. The manifest binds per-file size, mode and SHA-256, the bundle digest, the per-architecture CDHash, the source digest and the toolchain; `tests/build_player_helper.py` (maintainer tooling) rebuilds it reproducibly and `--check` proves the committed bundle is what the committed source builds to.
- **`player.install-capture-helper`** (`DEPLOY`, `MUTATING`, `STATELESS`, `OFFLINE_ANALYSIS`, no tool, no evidence): copies the release to exactly `~/Applications/GPOS/GposPlayerHelper.app` through a staging directory and publishes it with `renamex_np(RENAME_EXCL)`; ABSENT installs, EXACT is a no-op, UNTRUSTED (and a raced-in non-release destination) is a conflict and never overwritten, repaired or removed. Nothing is compiled, signed, downloaded or granted; a Human grants Screen Recording to the helper.
- **`player.launch`** (`SESSION_OPEN` on `PLAYER_RUNTIME:<project>`, one runtime per project): revalidates the build (alpha.21 `revalidate`, the manifest's own SHA-256), refuses any other running instance of the same application id (an own-user libproc bundle-identifier scan united with AppKit; never by name; never adopted or signalled), opens an immutable SESSION lease with the pre-launch facts, starts the verified helper's `supervise` mode detached, proves the handshake (supervisor pid, start time and kernel CDHash; the Player as its child in its own process group running the build's executable), revalidates again, re-scans, writes a write-once `runtime-binding.json`, commits and confirms. A launch that does not commit is abandoned and its session released only when the Player is proven gone; nothing is retried.
- **The supervise mode:** launches exactly `<Player executable> -logFile <runtime>/player.log` with `HOME`, `TMPDIR`, `LANG` and a fixed `PATH`, owns the child, waits for commit, abort, a stop intent, a 60 s commit deadline or the child's exit, asks the Player to quit through AppKit and SIGKILLs only its own unreaped child after a 10 s grace, then writes the observed exit status. It never retries, relaunches or signals another process.
- **`player.status`** (`READ_ONLY`, `SESSION_REQUIRED`, no process, no write): binding phase, kernel identity (PROVEN, GONE, NOT_THIS_PROCESS, UNPROVEN), the supervisor (proven by pid, start time and code hash, not by path), exit observation, build trust, helper state, window presence (in-process CoreGraphics) and a sanitized log tail; Screen Recording is not probed (`NOT_CHECKED` / `UNKNOWN`).
- **`player.capture-screenshot` / `player.capture-video`** (`CAPTURE`, `MUTATING`, `SESSION_REQUIRED`, single writer): one `/usr/bin/open` LaunchServices invocation of `shot` or `video`; the helper runs the read-only `CGPreflightScreenCaptureAccess()` first and refuses without ScreenCaptureKit when it is false (`CAPTURE_PERMISSION_REQUIRED`); exactly one on-screen, layer-0, visible window owned by the proven Player, a 5 s technical settle, the same window and identity again, a desktop-independent window filter without the cursor. Screenshots are PNG with the longest edge at most 1920 px, never upscaled; videos are silent H.264 MP4 of 1–15 s, at most 1920 px and 30 fps, checked by AVFoundation and by a bounded GPOS box walk. The helper must be EXACT before and after, and its result must name the request nonce, the session and the release identity. No FFprobe or FFmpeg inside.
- **`player.stop`** (`SESSION_CLOSE`, no process): a stop intent the running supervisor acts on; when the supervisor is gone, the reviewed in-process AppKit request (only when bundle identifier and executable path match) and then a SIGKILL of exactly the re-proven pid; never SIGTERM, never by name, never an unproven process; build drift never blocks stopping a proven Player. Classes `GRACEFUL_STOP`, `FORCED_STOP`, `EXITED(code)`, `CRASHED(signal)`, `GONE_UNOBSERVED`, `OUTCOME_UNKNOWN`. Another owner may only recover a session whose Player is proven gone.
- **The one-external-process invariant:** install 0, launch 1 (`supervise`), status 0, screenshot 1 (`shot`), video 1 (`video`), stop 0; only `gpos/tools/player/invocation.py` calls `context.run` / `context.spawn_detached`. A capture's recorded command is truthfully `/usr/bin/open` (D7); the helper's identity is bound in the capture record.
- **Evidence:** screenshot `VISUAL_EVIDENCE`, video `MOTION_EVIDENCE`, the stop's sanitized log `RUNTIME_EVIDENCE` (only when the supervisor observed the end, the build still revalidates and `build_id` was restated) — all `DIAGNOSTIC_RUNTIME`, each with a capture or runtime record binding session, build, process, helper and window facts.
- **The player log sanitizer** (player-only): relative runtime and project paths, `~` for the account home, the shared credential redaction, a space-aware path rule that removes `~/Library/Application Support/<Company>/<Product>/…` whole, the alpha.20 absolute-path rule as defence in depth, clipping and bounds.
- **Diagnostics:** `PLAYER_*` and `CAPTURE_*` codes.
- **Tests:** `tests/test_player.py`, `tests/test_player_helper.py`, `tests/test_player_real.py`, `tests/player_fakes.py`, `tests/player_testkit/` (QualGame, a test-only setup package, Swift fixtures), `tests/mutate_player.py`, `tests/mutate_player_helper.py`; foundation tests and mutations for the detached process and the host location.

### Notes

- Side effects of running a game (never read or deleted by GPOS): `~/Library/Application Support/<Company>/<Product>`, `~/Library/Preferences/<bundle id>.plist`, `~/Library/Saved Application State/<bundle id>.savedState`, logs and crash reports. The install writes `~/Applications/GPOS/` (and creates `~/Applications` when absent).
- The probe's `tool_path` is the installed helper executable's real absolute path and names the local home directory in provenance.
- The checks around the LaunchServices invocation are not atomic; a locally swapped helper binary would also lose the ad-hoc Screen Recording grant. A Human must grant Screen Recording again whenever the helper's bytes or signature change.
- The stop fallback signals a process that is not GPOS's child: a pid could be reused in the microseconds between the last proof and the signal.
- The space-aware path rule can leave the last word of a path whose last component contains a space and no further `/`.

## [1.0.0-alpha.21] — Phase 2C-7: Unity Build Core

Builds on the frozen Phase-2C-6C tree (`v1.0.0-alpha.20`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. The foundation gains a request-id path-safety check and new diagnostic codes. Projects must pin `gpos_version` `1.0.0-alpha.21`.

The `unity` adapter gains a build plane: two batch capabilities that inspect a project's existing build configuration and build exactly it — macOS Standalone Player, Mono, the already active target — through one fixed, audited `executeMethod` entry in bridge 1.5.0 (the live protocol stays `gpos.unity.live/5`). The payload stays in the execution workspace, bound by a build manifest written last. Excluded (deferred):

- Android, AAB, custom signing, IL2CPP and every other target; target switching;
- creating, activating, deactivating, choosing or editing a Build Profile; Build Profile Player Settings overrides (D-A);
- debugger, profiler, deep-profiling, managed-debugger and code-coverage builds (D-B); any settings change;
- a caller-named method, class, argument, target, development flag, scene, option, define, output path or build id;
- running, installing, deploying or capturing the Player; a directory artifact; Git inside the Unity adapter.

### Added

- **Build capabilities** (`gpos/tools/unity/build.py`, the adapter's build plane; bridge `Editor/Build/BuildEntry.cs`, `Editor/Build/BuildConfiguration.cs` and the Unity-free core `Editor/Core/BuildRules.cs`):
  - `unity.inspect-build-configuration` (`INSPECT`, `MUTATING`): the existing configuration, its buildability under closed rules and its configuration token;
  - `unity.build-player` (`BUILD`, `MUTATING`): requires `request.build_revision` and the inspected token; `build_id` is `build-<request id>`; a supplied `build_id` is refused;
  - both are macOS-only: a `request.target_platform` other than absent or `MACOS` is refused, and a caller `output_dir` is refused before anything is created;
  - both `STATELESS`, `EDITOR`, the `EXECUTION` single-writer lease on `EDITOR_PROJECT`, the read-only project-lock proof twice, the exact Editor version, Package Manager isolation, dry run; no evidence.
- **The fixed command:** the batch test command's common flags plus `-executeMethod Gpos.LiveBridge.Build.BuildEntry.Run -gposBuildRequest <workspace>/build-request.json`; no `-quit`, `-accept-apiupdate`, `-noUpm` or `-nographics`. The entry is batch-only, has no initializer or callback, trusts only its exact execution workspace and exits the Editor itself.
- **Profile and classic modes:** the active custom Build Profile of a macOS Player is built with `BuildPlayerWithProfileOptions` exactly (never activated, edited or replaced by classic settings); otherwise the enabled Editor build scenes with `BuildPlayerOptions` and `Development` exactly when the Editor's setting is on. In profile mode the Editor's development state must equal the profile's serialized `m_Development`; after the build the BuildReport's Development bit must match.
- **The configuration token** (`gpos.unity.build-config/1`, canonical JSON reproduced byte for byte in C#): Unity version, target, subtarget, backend, application identifier, development, mode, the effective scenes with GUIDs and file hashes, `ProjectSettings.asset`, `EditorBuildSettings.asset`, `manifest.json`, `packages-lock.json`, the debug and output states, and the active profile's identity, file hash and facts; recomputed at inspection, before `BuildPlayer` and after it, and by GPOS from every response.
- **The workspace is the build:** `.game/gpos-runtime/tool-output/unity/<request id>/` holds the request, started marker, response, logs, `staging/Player.app`, `payload/Player.app` and `build-manifest.json`; a non-empty workspace is refused; `staging` becomes `payload` by one rename and the manifest is written last and once.
- **Payload validation and `gpos.unity.payload-tree/1`:** Info.plist, bundle identifier, executable, `boot.config` build GUID; an lstat-only, sorted, bounded tree digest with link containment, special-file refusal and entry, byte, depth and path bounds; `build.revalidate` for later consumers.
- **The build manifest** `gpos.unity.build-manifest/1` (the `REPORT` artifact), with `build_revision_source: CALLER_SUPPLIED`, fixed limitations and no `git_verified`.
- **Public text:** BuildReport step names and messages, problems and refusals are relativized to the Unity project and GPOS root, passed through the redaction boundary, stripped of any other absolute path (`<path>`) and clipped again; the BuildReport's absolute `outputPath` is used only to verify the staging path and never appears in a result, diagnostic or manifest.
- **The qualified-build workflow:** `git.resolve-provenance` before the inspection, again before the build and again after it, all returning the same clean revision; documented as a workflow-level proof, not an atomic repository lock.
- `live_bridge/history/1.4.0.json`: the manifest frozen in `v1.0.0-alpha.20`, byte for byte, with its digest pinned in code.
- Diagnostics:
  - `BUILD_ENTRY_UNAVAILABLE`, `BUILD_WORKSPACE_NOT_FRESH`, `BUILD_TARGET_NOT_ACTIVE`, `BUILD_CONFIGURATION_UNSUPPORTED` and `BUILD_CONFIGURATION_CHANGED` (conflicts); `BUILD_TARGET_MODULE_MISSING` (unavailable);
  - `BUILD_COMPILE_FAILED`, `BUILD_ENTRY_FAILED`, `BUILD_FAILED` and `BUILD_PAYLOAD_INVALID` (failed); `BUILD_OUTCOME_UNKNOWN` (outcome unknown);
  - `BUILD_CONFIGURATION_NOT_BUILDABLE`, `BUILD_QUARANTINED` and `BUILD_PUBLISHED` (informational).
- [tools/unity-build.md](tools/unity-build.md).
- Tests:
  - `tests/test_unity_build.py`: fast groups (declarations, request rules, the fixed command, the release, source scans, the stand-in outcome matrix, tree digest golden vectors and bounds, publication order, conflicts); real RB1 (the three-Git-read qualified classic build, Development, a stale token, repeated builds, debug-state refusal, no and missing scenes, a non-active target, a killed build), RB2 (an active macOS Build Profile, its Development and defines, D-A and D-B refusals), RB3 (failing tests still build), RB4 (compile errors before the entry) and RQ (the alpha.20 source-error → fix → build composition with the live-session and open-Editor conflicts);
  - `tests/unity_live_bridge_core/BuildCoreTests.cs`, with the canonical-token golden vector shared with Python;
  - `tests/unity_build_testkit/` (test only: plays the Human's configuration changes in lab Editors);
  - `tests/mutate_unity_build.py`, with a `--real` mode for the entry's Editor-side behaviour.

### Changed

- **Foundation:** a capability declares `caller_output_dir_allowed` (default `true`, so every earlier capability is unchanged; exposed in the capability descriptor). A capability that owns its execution workspace declares `false`, and a request naming an `output_dir` for it is `INVALID_TOOL_REQUEST` in request validation, before any path is resolved or created; only a project-bound capability may declare it. Both build capabilities declare it.
- **Foundation:** a caller-supplied `request_id` must be one safe path component (`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`) and is checked before any path is derived from it; an invalid one is `INVALID_TOOL_REQUEST` with no workspace or other filesystem change. An empty id is validated (and refused), no longer replaced by a generated one. The generated `req-<16 hex>` form is unchanged.
- Bridge `com.gpos.live-bridge` 1.5.0 on protocol `gpos.unity.live/5` (request and response schemas unchanged): the package gains the batch-only build entry; the live lifecycle is unchanged apart from its header comment and stays dormant in batch mode. An installed 1.4.0 (or earlier) package is PREVIOUS: live capabilities report it `LIVE_BRIDGE_INCOMPATIBLE` and the build plane `BUILD_ENTRY_UNAVAILABLE` until `unity.live-install-bridge` upgrades it, directly to 1.5.0 with the alpha.17 transaction.
- The `unity` descriptor has 47 capabilities (3 batch test, 2 build, 9 live session, 13 Scene authoring, 7 asset, 9 prefab, 4 source).
- The live bridge's forbidden-mechanism scan covers every bridge file except `Editor/Build/`, which has its own allowlist scan.

### Notes

- Measured with Unity 6000.5.8f1 (research): `BuildPlayer` with a non-active target silently switches the active target; Unity merges a build into an existing output directory; a killed build can leave a complete-looking `.app`; the BuildReport GUID identifies a data build, not an invocation, and `boot.config` carries it; macOS builds are not byte-reproducible (the ad-hoc signature and `boot.config`); with a Build Profile active, the Editor's Development setting is the profile's `m_Development`; a normal macOS build leaves Git-tracked project state unchanged.
- Known limitation: alpha.21 publishes a single `Player.app` payload only. A build that writes a sibling beside it in `staging/` (for example a Burst debug-information or other debug-symbol sidecar) is `BUILD_PAYLOAD_INVALID` and is not published; the sidecar is never ignored, deleted, packaged or absorbed. Burst and debug-symbol sidecar output is deferred until it is researched.
- Research side effects disclosed (Android, now deferred): lab Android builds wrote the user's shared Gradle home (`~/.gradle`), downloaded `androidx.games:games-frame-pacing` there from the network, were signed with the user's personal debug keystore and left `.utmp/` and `build/` in the Unity project root. Nothing in alpha.21 builds for Android or touches those locations.

## [1.0.0-alpha.20] — Phase 2C-6C: Unity source, compile and diagnostics core

Builds on the frozen Phase-2C-6B2B tree (`v1.0.0-alpha.19`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. The foundation gains new diagnostic codes. Projects must pin `gpos_version` `1.0.0-alpha.20`.

The `unity` adapter's live plane gains source synchronization and compilation facts through the fixed bridge 1.4.0 (protocol `gpos.unity.live/5`): four capabilities on the Human-approved session. Other programs write source files; GPOS names exact paths. The batch plane proves Unity's project lock read-only instead of treating a leftover lockfile as a conflict. Excluded:

- C# text or file content, a source-write command, a compile or recompile command, `RequestScriptCompilation`;
- `AssetDatabase.Refresh`, an arbitrary `AssetDatabase` operation, a caller-named folder or extension, a move command;
- `Editor.log` parsing; reflection, `executeMethod`, menus, input, processes and network in the bridge;
- deleting, truncating, rewriting or locking `Temp/UnityLockfile`; a time-based stale-lock grace.

### Added

- **Source capabilities** (`gpos/tools/unity/sources.py`; bridge `SourceSync.cs`, `Compilation.cs` and the Unity-free core `SourceRules.cs`):
  - `unity.live-sync-sources` (`TRANSFORM`, `MUTATING`): an exact `ImportAsset` of each named existing `.cs`, `.asmdef` or `.asmref` path below `Assets/`; for each deleted one, a recursive import of its direct parent folder, or — only when that folder is gone too — of exactly one folder above it, never `Assets` itself (D1, D2); at most 64 paths and 4 folders;
  - `unity.live-compilation-status` and `unity.live-compilation-diagnostics` (`INSPECT`, `READ_ONLY`);
  - `unity.live-wait-ready` (`INSPECT`, `READ_ONLY`): observation only, at most 300 s;
  - all `STATEFUL`, `EDITOR`, `SESSION_REQUIRED`, and none produces evidence.
- **Validation before the first import:** every path, the derived folders, and a bounded, link-free before-snapshot of each folder (1000 entries, 6 levels); the AssetDatabase is asked only about the entries that bounded walk visits and about the exact requested paths — never a project-wide enumeration; after the imports, a second bounded snapshot and the report of every requested removal, every walked file or folder Unity started or stopped knowing, and every `.meta` created, removed or changed; stale database entries of files already gone before the sync may be reconciled by Unity unlisted, and the result says so. `mutation_performed` is true from the first import on.
- **The compile-generation model:** compile, completed-compile, sync and reload generations; the causal baseline `compile_started_before_sync` read before a sync's first import; a settled outcome after a sync is a finished compilation newer than that baseline: `FAILED` (errors) or `SUCCEEDED` (no errors and a proven Domain Reload). A failed compilation is Edit Mode with `compilation_failed`, not a phase; coalesced compilations are never counted as one per edit.
- **The compilation journal:** `CompilationPipeline` callbacks only, kept in the Editor session (survives Domain Reloads, lost at Editor quit), bounded (64 assemblies, 32 messages each, 256 in total, 512 characters), deterministically ordered, with project-relative files and paths, sanitized again through the redaction boundary; stale error entries of cached assemblies are dropped after a reload that proves a clean compilation.
- **The read-only project-lock proof** of the batch test plane (`gpos/tools/unity/project_lock.py`, macOS): the exact argument vectors of this user's Hub Editor processes (`KERN_PROCARGS2`) matched by file identity of `-projectPath`, and an `F_GETLK` query on `Temp/UnityLockfile` opened read-only and link-free; effective states `NO_LOCK`, `ACTIVE_EDITOR`, `ORPHAN_UNHELD` and `LOCK_STATE_UNKNOWN`, proven before the workspace is prepared and again immediately before the launch.
- `live_bridge/history/1.3.0.json`: the manifest frozen in `v1.0.0-alpha.19`, byte for byte, with its digest pinned in code.
- Diagnostics:
  - `LIVE_SOURCE_PATH_INVALID`, `LIVE_SOURCE_SYNC_LIMIT`, `LIVE_SYNC_GENERATION_UNKNOWN` and `LIVE_DIAGNOSTICS_FILTER_UNKNOWN` (invalid requests);
  - `LIVE_SOURCE_SYNC_REFUSED` and `LIVE_NOT_READY` (conflicts); `LIVE_SOURCE_SYNC_INCOMPLETE` (failed);
  - `LIVE_SOURCES_SYNCED`, `LIVE_SOURCE_SYNC_SIDE_EFFECTS`, `LIVE_COMPILATION_FAILED` and `ENGINE_PROJECT_ORPHAN_LOCK` (informational).
- [tools/unity-live-sources.md](tools/unity-live-sources.md).
- Tests:
  - `tests/test_unity_sources.py`: fast groups; real R9 (the source/compile loop), R10 (reloads, identity, deletion synchronization and its bounds), R11 (the permanent end-to-end qualification: file tooling, compile error, diagnostics, fix, reload, catalogs, Scene, asset and prefab authoring, Play Mode, batch EditMode and PlayMode tests, a failing test, its fix through the live loop, a passing rerun, a reopened project) and R12 (the real stale-lock contract);
  - `tests/unity_live_bridge_core/SourceCoreTests.cs`;
  - `tests/mutate_unity_sources.py`, with a `--real` mode for the bridge's Editor-side source code.

### Changed

- Bridge `com.gpos.live-bridge` 1.4.0, protocol `gpos.unity.live/5`, request and response schemas `/5`; the heartbeat carries the compilation counters. Installed 1.0.0, 1.1.0, 1.2.0 and 1.3.0 packages are PREVIOUS and `LIVE_BRIDGE_INCOMPATIBLE` until upgraded, all directly to 1.4.0 with the alpha.17 transaction.
- The `unity` descriptor has 45 capabilities (3 batch, 9 live session, 13 Scene authoring, 7 asset, 9 prefab, 4 source).
- `unity.run-editmode-tests` and `unity.run-playmode-tests` no longer refuse a project merely because `Temp/UnityLockfile` exists: an active or unprovable lock stays `ENGINE_PROJECT_LOCKED` (with `lock_state`), an unheld leftover with no Unity process for the project proceeds (`ENGINE_PROJECT_ORPHAN_LOCK`), and GPOS never touches the file. `unity.live-install-bridge` keeps its closed-project rule.
- The structural ban on `ctypes` in the Unity modules admits exactly `project_lock.py`, which binds four read-only libSystem calls.
- The Scene, asset and prefab tests pin 45 capabilities, protocol `/5` and the 1.3.0 history.

### Notes

- Measured with Unity 6000.5.8f1 (D4): a batch run that stops on a compile error leaves an unheld `Temp/UnityLockfile` for more than 600 s (3 of 3 runs); it never cleared by itself; an active Editor holds a whole-file lock that `F_GETLK` reports; a later Unity launch accepts the orphan and replaces the file; a second launch against a held lock is refused by Unity.
- Measured: restoring a source to a version Unity compiled before takes the assembly from its cache without reporting it, and the domain reloads.

## [1.0.0-alpha.19] — Phase 2C-6B2B: Unity live prefab authoring core

Builds on the frozen Phase-2C-6B2A tree (`v1.0.0-alpha.18`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. The foundation gains new diagnostic codes. Projects must pin `gpos_version` `1.0.0-alpha.19`.

The `unity` adapter's live plane gains prefab authoring through the fixed bridge 1.3.0 (protocol `gpos.unity.live/4`): nine prefab capabilities on the Human-approved session, in Edit Mode only. Only a regular prefab below `Assets/` without nested prefab instances is created, instantiated or edited; Variants, models, package and nested prefabs are inspected only. Excluded (deferred):

- `SaveAsPrefabAssetAndConnect`; apply, revert and unpack of overrides;
- Variant creation or editing, nested-prefab authoring, prefab child creation or deletion, model prefab editing;
- opening, saving, closing or navigating Prefab Mode; a generic `PrefabUtility` call;
- package prefab mutation, reflection, C#, input and live evidence.

### Added

- **Prefab capabilities** (`gpos/tools/unity/prefabs.py`; bridge `PrefabAuthoring.cs`, `PrefabResolver.cs` and the Unity-free core `PrefabRules.cs`):
  - `unity.live-prefab-inspect` and `unity.live-prefab-instance-inspect` (`INSPECT`, `READ_ONLY`);
  - `unity.live-create-prefab`, `unity.live-instantiate-prefab`, `unity.live-set-prefab-gameobject`, `unity.live-set-prefab-transform`, `unity.live-add-prefab-component`, `unity.live-remove-prefab-component` and `unity.live-set-prefab-property` (`TRANSFORM`, `MUTATING`);
  - all `STATEFUL`, `EDITOR`, `SESSION_REQUIRED`, and none produces evidence.
- **Prefab identity:** persistent prefab objects are type-1 `GlobalObjectId` strings with prefab id 0; the isolated `LoadPrefabContents` copy is mapped to them one to one by file id and never exposed. Every object has an ownership role (`OWNED`, `NESTED_ROOT`, `NESTED_CONTENT`, `VARIANT_INHERITED`) and every prefab its scope reasons.
- **Whole-prefab token:** GUID, root id, canonical path, type, source, the file and `.meta` hashes, and every object's id, ownership, dirty flag, hide flags and complete serialized state; given only for regular prefabs without nested instances (`NOT_COVERED` otherwise).
- **Inspection** that never claims authority the mutation refuses: `property_writable`, `prefab_mutable` and the effective `writable` per property; instance inspection of the source, the object mapping and every override kind (nested overrides with their inner source).
- **Guards** of every prefab mutation: any open Prefab Mode stage, a dirty prefab, version control, an OS-unwritable file, `.meta` or folder, and a dirty loaded Scene holding a dependent instance (bounded scan, `LIVE_PREFAB_LIMIT` beyond it) — all before any import.
- **Creation** from a completely plain Scene subtree after a full reference scan, through the alpha.18 creation transaction (records of schema `/2`, kind `PREFAB`), with identity and content proofs (every reference saved as it is, nothing nulled, the source unchanged and never connected).
- **Instantiation** of a clean regular prefab: a targeted import and a fresh-token check first, then one Scene Undo group with every id requested before the creation is recorded.
- **Edits** on Unity's isolated copy: one reviewed edit read back, the file and `.meta` re-checked immediately before `SaveAsPrefabAsset` as the commit point, the contents always unloaded, the persistent result verified (exact final ids of added components); uncertainty from the commit point on is `OUTCOME_UNKNOWN`, never retried.
- `live_bridge/history/1.2.0.json`: the manifest frozen in `v1.0.0-alpha.18`, byte for byte, with its digest pinned in code.
- Diagnostics:
  - `LIVE_PREFAB_STAGE_OPEN`, `LIVE_PREFAB_DIRTY`, `LIVE_PREFAB_CONFLICT`, `LIVE_PREFAB_NOT_EDITABLE` and `LIVE_PREFAB_CREATE_INCOMPLETE` (conflicts);
  - `LIVE_PREFAB_REFUSED` and `LIVE_PREFAB_LIMIT` (invalid requests);
  - `LIVE_PREFAB_CREATED`, `LIVE_PREFAB_SAVED`, `LIVE_PREFAB_INSTANTIATED`, `LIVE_PREFAB_CREATE_RECOVERED` and `LIVE_PREFAB_SIDE_EFFECTS` (informational).
- [tools/unity-live-prefabs.md](tools/unity-live-prefabs.md).
- Tests and fixtures:
  - `tests/test_unity_prefabs.py`: fast groups; a real end-to-end prefab session; every guard in a real Editor; real crash recovery; a real upgrade from 1.2.0;
  - `tests/unity_live_bridge_core/PrefabCoreTests.cs`;
  - `tests/mutate_unity_prefabs.py`, with a `--real` mode for the bridge's Editor-side prefab code;
  - the prefab fixture project in `tests/unity_fixture_builder.py`;
  - testkit operations for the prefab fixture, Prefab Mode as a Human drives it, a dirty Scene, many objects, an asset move and the version-control test seam.

### Changed

- Bridge `com.gpos.live-bridge` 1.3.0, protocol `gpos.unity.live/4`, request and response schemas `/4`. GPOS talks only to the audited 1.3.0 bridge; installed 1.0.0, 1.1.0 and 1.2.0 packages are PREVIOUS and `LIVE_BRIDGE_INCOMPATIBLE` until upgraded, all directly to 1.3.0 with the alpha.17 transaction.
- The `unity` descriptor has 41 capabilities (3 batch, 9 live session, 13 Scene authoring, 7 asset, 9 prefab).
- The asset-creation record store also holds `PREFAB` records (schema `/2`); Material and ScriptableObject records stay schema `/1`, each schema is read only with its own kinds, every creating command recovers every kind, and an undecidable `PREFAB` record is `LIVE_PREFAB_CREATE_INCOMPLETE`.
- The alpha.18 asset surface explicitly authors only Materials and ScriptableObjects; a prefab stays a reference (`authorable` false).
- The property rule is split into the Scene prefab boundary and the property rule itself; Scene and asset commands keep both, unchanged.
- The Scene and asset tests pin 41 capabilities, protocol `/4` and the 1.2.0 history.

### Notes

- Measured with Unity 6000.5.8f1 and correcting the research record: saving a prefab updated its instances in loaded Scenes, but Unity did not mark those Scenes dirty (a value change, a rename, and instances whose `OnValidate` runs). `scenes_marked_dirty` reports what actually happens; project code that marks Scenes dirty is disclosed.
- Project code that changes a prefab during the targeted import makes the fresh token differ, so the edit or instantiation is refused (`LIVE_PREFAB_CONFLICT`); project code changing other objects of the edited copy is disclosed in `unrequested_changes`. All of it is `TOOL_INHERENT`.
- A Human's Prefab Mode edit may be saved by Unity's own Prefab Mode auto-save; GPOS never saves, clears or discards it.

## [1.0.0-alpha.18] — Phase 2C-6B2A: Unity live asset references and asset authoring core

Builds on the frozen Phase-2C-6B1 tree (`v1.0.0-alpha.17`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. The foundation gains new diagnostic codes. Projects must pin `gpos_version` `1.0.0-alpha.18`.

The `unity` adapter's live plane gains asset references and asset authoring through the fixed bridge 1.2.0 (protocol `gpos.unity.live/3`): seven asset capabilities and one Scene-authoring capability on the Human-approved session, in Edit Mode only. Excluded (deferred):

- prefab asset authoring, Prefab Mode, apply, revert and unpack;
- a user-facing asset delete, move or rename; import settings; Scene creation and Save As;
- array (other than one reviewed Renderer material slot), list and managed-reference writes, curves and gradients;
- ShaderGUI, keyword, blend-preset, emission and render-state emulation;
- package and built-in asset mutation; an arbitrary AssetDatabase call, `SaveAssets`, a global `Refresh`, folder creation;
- reflection, C#, input and live evidence.

### Added

- **Asset capabilities** (`gpos/tools/unity/assets.py`; bridge `AssetAuthoring.cs`, `AssetResolver.cs`, `AssetCatalogs.cs` and the Unity-free core `AssetRules.cs`):
  - `unity.live-asset-types`, `unity.live-asset-find` and `unity.live-asset-inspect` (`INSPECT`, `READ_ONLY`);
  - `unity.live-create-material`, `unity.live-set-material-property`, `unity.live-create-scriptable-object` and `unity.live-set-asset-property` (`TRANSFORM`, `MUTATING`);
  - all `STATEFUL`, `EDITOR`, `SESSION_REQUIRED`, and none produces evidence.
- **`unity.live-set-renderer-material`** (Scene authoring): one shared-material slot of a Renderer through the serialized `m_Materials`, never `Renderer.material`; an existing slot is replaced, a Renderer without slots gets slot 0 only, nothing is appended, inserted, removed or resized.
- **Asset identity:** `GlobalObjectId` only, identifier types 1, 3 and 4 with prefab id 0, and the two built-in GUIDs only for type 4. The reviewed kinds are MATERIAL, TEXTURE, SPRITE, AUDIO, MESH, PREFAB, MODEL, PREFAB_COMPONENT and SCRIPTABLE_OBJECT, from `Assets/`, canonical `Packages/<name>/` paths of registered packages, or a fixed built-in table. Scene assets, MonoScripts, folders, internal prefab or model objects, Editor-only paths and types are refused. Package and built-in assets are references only.
- **Asset references** from Scene properties and ScriptableObjects, type-checked before Unity assigns them (Unity silently stores `null` for a wrong type); an AudioSource's clip is `m_Resource` (audio only), and `m_audioClip` is refused.
- **Typed, bounded, paged lookup** in `ASSETS`, `PACKAGE` and `BUILTIN` with a fixed type filter per kind; the caller's query is only a name substring.
- **Catalogs:** the kind table; a ScriptableObject catalog (editable types, and a creatable subset with `[CreateAssetMenu]`); a shader catalog with every declared property's type, flags, range and texture dimension. Each has a digest over every listed field.
- **Composite asset token:** identity, canonical path, runtime type, the whole in-memory serialized state, the dirty flag, and the SHA-256 of the file and its `.meta`, with fixed size bounds.
- **Persistence:** refuse a dirty asset, check version control, one targeted import, compare the token, capture the hashes, one Undo-group edit read back, re-check the file and `.meta` immediately before saving, then `SaveAssetIfDirty` of that asset only as the commit point; uncertainty from there on is `OUTCOME_UNKNOWN`, never retried.
- **Material properties** limited to what the Material's catalogued shader declares (color, vector, float, range, int, texture), with declared ranges and texture dimensions enforced; a Material texture takes a Texture asset's own id only (a Sprite is refused, never converted).
- **Collision-safe creation:** a GPOS-owned, exclusively created scratch folder `Assets/GposAssetTxn-<txn id>`, `CreateAsset` there under the final file name, proof of GUID, id, type and hashes, a re-check of the exact destination, `ValidateMoveAsset` and `MoveAsset`, proof again, and removal of the proven empty scratch folder. Nothing is ever overwritten, renamed, or created as a folder.
- **Persistent creation transaction** (`.game/gpos-runtime/unity/asset-create-txn/<project key>/<txn id>.json`): bounded, strict, identifiers and hashes only, never trusted for a path. Before each creation, every earlier interrupted creation is recovered from what is on disk: nothing created, exact temporary asset removed, exact final asset kept — or `LIVE_ASSET_CREATE_INCOMPLETE` with nothing touched. No creation is replayed.
- `live_bridge/history/1.1.0.json`: the manifest frozen in `v1.0.0-alpha.17`, byte for byte, with its digest pinned in code.
- Diagnostics:
  - `LIVE_ASSET_CONFLICT`, `LIVE_ASSET_DIRTY`, `LIVE_ASSET_EXISTS`, `LIVE_ASSET_NOT_EDITABLE` and `LIVE_ASSET_CREATE_INCOMPLETE` (conflicts);
  - `LIVE_ASSET_REFUSED`, `LIVE_ASSET_PATH_INVALID`, `LIVE_ASSET_LIMIT` and `LIVE_SHADER_NOT_IN_CATALOG` (invalid requests);
  - `LIVE_ASSET_CREATED`, `LIVE_ASSET_SAVED` and `LIVE_ASSET_CREATE_RECOVERED` (informational).
- [tools/unity-live-assets.md](tools/unity-live-assets.md).
- Tests and fixtures:
  - `tests/test_unity_assets.py`: fast groups; a real end-to-end asset session; real crash recovery that stops the lab Editor at exact creation steps;
  - `tests/unity_live_bridge_core/AssetCoreTests.cs`;
  - `tests/mutate_unity_assets.py`, with a `--real` mode for the bridge's Editor-side asset code;
  - the asset fixture project in `tests/unity_fixture_builder.py` (generated PNG, WAV and OBJ files, a test shader, ScriptableObject types, an AssetPostprocessor, an embedded package);
  - testkit operations for the asset fixture, a Human's asset save, import and edits, a second saved Scene, and the `AssetAuthoring.AfterStep` test seam.

### Changed

- Bridge `com.gpos.live-bridge` 1.2.0, protocol `gpos.unity.live/3`, request and response schemas `/3`. GPOS talks only to the audited 1.2.0 bridge; installed 1.0.0 and 1.1.0 packages are PREVIOUS and `LIVE_BRIDGE_INCOMPATIBLE` until upgraded, both directly to 1.2.0 with the alpha.17 transaction.
- The `unity` descriptor has 32 capabilities (3 batch, 9 live session, 13 Scene authoring, 7 asset).
- Scene authoring: object references may name reviewed assets; `unity.live-properties` reports an asset reference's id.
- `LIVE_OBJECT_REFUSED`, `LIVE_TYPE_NOT_IN_CATALOG` and `LIVE_CATALOG_CHANGED` meanings cover assets and the new catalogs.
- **Corrected forward:** alpha.17's documentation said that creating a GameObject with several Scenes open may dirty the active Scene. Creating in the non-active Scene dirties only that Scene; a permanent real test with a second saved Scene now covers it, together with the cross-Scene reference and move refusals.
- `tests/test_unity_authoring.py` checks both pinned history manifests against their frozen tags, upgrades real 1.0.0 and 1.1.0 bridges, and has the permanent second-Scene test; `tests/mutate_unity_authoring.py` anchors follow bridge 1.2.0.

### Notes

- A targeted import changes Unity's imported state and may run project AssetPostprocessors, so a refusal after it reports `mutation_performed` true (with `import_performed` true and `value_persisted` false). Project code (`AssetPostprocessor`, ScriptableObject `OnEnable` and `OnValidate`) is `TOOL_INHERENT`.
- An asset edit stays in Unity's Undo history: Cmd-Z restores the value in memory and leaves the asset dirty while its file keeps the saved value; GPOS never saves that state. Creation is not undoable.
- Version-control providers other than none are untested; creation refuses whenever a provider is active.

## [1.0.0-alpha.17] — Phase 2C-6B1: Unity live Scene authoring core

Builds on the frozen Phase-2C-6A tree (`v1.0.0-alpha.16`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. The foundation gains new diagnostic codes, and its identity grammars now match the whole string. Projects must pin `gpos_version` `1.0.0-alpha.17`.

The `unity` adapter's live plane gains Scene authoring through the fixed bridge 1.1.0 (protocol `gpos.unity.live/2`): twelve capabilities on the Human-approved session, in Edit Mode only, with optimistic concurrency, one Undo group per command and verified rollback. Excluded:

- prefab asset or Prefab Mode editing, apply, revert and unpack;
- asset and cross-Scene references, and asset creation;
- array and managed-reference mutation, curves and gradients;
- Scene creation and Save As;
- reflection, C#, `-executeMethod`, menu execution and input;
- live evidence.

### Added

- **Scene authoring** (`gpos/tools/unity/authoring.py`; bridge `Authoring.cs`, `Scene.cs`, `Properties.cs`, `Catalog.cs` and the Unity-free core `ObjectIds.cs`, `Tokens.cs`, `PropertyRules.cs`):
  - `unity.live-object-inspect`, `unity.live-component-types` and `unity.live-properties` (`INSPECT`, `READ_ONLY`);
  - `unity.live-create-gameobject`, `unity.live-delete-gameobject`, `unity.live-set-parent`, `unity.live-set-gameobject`, `unity.live-set-transform`, `unity.live-add-component`, `unity.live-remove-component`, `unity.live-set-property` and `unity.live-save-scene` (`TRANSFORM`, `MUTATING`);
  - all `STATEFUL`, `EDITOR`, `SESSION_REQUIRED`, and none produces evidence.
- **Identity:** `GlobalObjectId` strings of Scene objects in saved, loaded Scenes only; unsaved Scenes are `LIVE_SCENE_NOT_SAVED`. A created object's id is allocated before its creation is recorded, so Redo keeps it, and a bounded by-id scan finds components Redo re-created that Unity's lookup misses.
- **Optimistic concurrency:** `object`, `transform`, `transform_chain`, `component`, `subtree` and `scene_roots` tokens, each required as its capability declares. `transform_chain` covers every Transform from the Scene root down to the object or new parent, for keep-world reparenting. The component token hashes every top-level serialized property, hidden ones included, opaquely; references are hashed by `GlobalObjectId`. Any mismatch is `LIVE_AUTHORING_CONFLICT` before anything changes.
- **Closed component catalog** and a digest over every entry's id, assembly, name, kind, `DisallowMultipleComponent`, sorted `RequireComponent` requirements and edit-mode execution. A changed catalog is `LIVE_CATALOG_CHANGED`.
- **Default-deny property writes:**
  - an exact kind table, with values validated before Unity sees them (no clamping, wrapping, infinity or non-unit quaternions);
  - read back through a fresh `SerializedObject`, and reverted on any difference;
  - denied engine paths, hidden properties, arrays, managed references, curves, gradients and characters;
  - object references only to type-checked objects of the same Scene.
- **Undo and rollback:** one collapsed `GPOS: …` Undo group per command. On failure the group is reverted with `Undo.RevertAllDownToGroup` and the pre-state tokens are re-verified: restored, or `LIVE_ROLLBACK_INCOMPLETE`. A reverted mutation still reports `mutation_performed`, and the Scene may stay dirty.
- **Prefab boundary** (`LIVE_PREFAB_BOUNDARY`): an outermost instance root can be renamed, moved, transformed or deleted; instance content is read-only.
- **Saving:** `unity.live-save-scene` saves an already saved Scene to its own path only.
- **Bridge upgrade:** `unity.live-install-bridge` upgrades an exact pinned earlier bridge in a closed project through a recorded, crash-recoverable transaction. The record holds identifiers only, and staging and backup paths are derived. Recovery finishes or rolls back only from exact known packages; anything unknown is `LIVE_BRIDGE_UPGRADE_INCOMPLETE` and untouched. There is no downgrade and no force repair. The released 1.0.0 manifest is kept byte for byte as `live_bridge/history/1.0.0.json`, with its digest pinned in code.
- Diagnostics:
  - `LIVE_AUTHORING_CONFLICT`, `LIVE_OBJECT_NOT_FOUND`, `LIVE_PREFAB_BOUNDARY`, `LIVE_SCENE_NOT_SAVED`, `LIVE_CATALOG_CHANGED`, `LIVE_AUTHORING_REFUSED` and `LIVE_BRIDGE_UPGRADE_INCOMPLETE` (conflicts);
  - `LIVE_OBJECT_REFUSED`, `LIVE_PROPERTY_UNSUPPORTED`, `LIVE_VALUE_INVALID`, `LIVE_TYPE_NOT_IN_CATALOG` and `LIVE_AUTHORING_LIMIT` (invalid requests);
  - `LIVE_ROLLBACK_INCOMPLETE` and `LIVE_AUTHORING_FAILED` (failures);
  - `LIVE_BRIDGE_UPGRADED`, `LIVE_BRIDGE_UPGRADE_RECOVERED` and `LIVE_SCENE_SAVED` (informational).
- [tools/unity-live-authoring.md](tools/unity-live-authoring.md).
- Tests and fixtures:
  - `tests/test_unity_authoring.py`: fast groups; the upgrade recovery matrix and a frozen-tag history check; real groups for an end-to-end authoring session and a real 1.0.0 → 1.1.0 upgrade;
  - `tests/unity_live_bridge_core/AuthoringCoreTests.cs`;
  - `tests/mutate_unity_authoring.py`, with a `--real` mode for the bridge's Editor-side code;
  - the authoring fixture project in `tests/unity_fixture_builder.py`;
  - testkit trigger files that stand in for the Human's Inspector edits, Hierarchy drags and Undo.

### Changed

- Bridge `com.gpos.live-bridge` 1.1.0, protocol `gpos.unity.live/2`, request and response schemas `/2`. GPOS talks only to the audited 1.1.0 bridge; an installed 1.0.0 is reported as PREVIOUS by `unity.live-status` and is `LIVE_BRIDGE_INCOMPATIBLE` until upgraded. The bridge's identifier grammars are anchored at the end of the string (`\z`), so a trailing newline no longer passes.
- The `unity` descriptor has 24 capabilities (3 batch, 9 live session, 12 authoring).
- Identity grammars match the whole string. A pattern ending in `$` also accepted a value followed by a newline; a value ending in LF or CRLF is now refused. This covers:
  - the SESSION lease id and `KIND:ID` owner, the request `session_id` and actor id;
  - tool adapter, capability and artifact ids, and the adapter version;
  - Git commit object ids and generated agent skill names;
  - every Scene-authoring and bridge-upgrade grammar.

  Regression tests and mutations cover each.
- `tests/test_unity_live.py`, `tests/unity_live_fake_bridge.py`, `tests/mutate_unity_live.py` and the testkit follow the new bridge version and protocol.

### Notes

- Rollback verification proves restoration of the state the operation's pre-state tokens cover, not of the whole Scene or project: project Editor code (`OnValidate`, component callbacks, `ExecuteAlways` scripts, save callbacks) can run and is `TOOL_INHERENT`.
- No windowed Human checklist was needed: the approval UI is unchanged, and Inspector-path edits are simulated through `SerializedObject` exactly as the Inspector applies them.

## [1.0.0-alpha.16] — Phase 2C-6A: Unity live session foundation

Builds on the frozen Phase-2C-5 tree (`v1.0.0-alpha.15`). No change to gate, evidence, authority, routing, lifecycle, validator or agent-adapter semantics. The foundation gains SESSION leases, lease modes, `ExecutionRequest.session_id` and the `OUTCOME_UNKNOWN` result status; the existing statuses, exit codes and execution-scoped lease behaviour are unchanged. Projects must pin `gpos_version` `1.0.0-alpha.16`.

The `unity` adapter gains its live Editor plane: one Human-approved session per GPOS project with an Editor a Human already has open, through a fixed, audited Editor bridge over local files. No authoring, capture, Editor launch or quit, focus control, input injection, MCP transport, arbitrary C#, reflection surface, `-executeMethod` or menu execution.

### Added

- **Foundation: sessions.** Registry `tool_lease_modes` (`NONE`, `EXECUTION`, `SESSION_OPEN`, `SESSION_REQUIRED`, `SESSION_CLOSE`) and `Capability.lease_mode` (derived from `single_writer_required` when unset, so earlier capabilities keep their behaviour). A SESSION lease uses the same file and resource key as an `EXECUTION` lease, binds a session id and the canonical owner `KIND:ID` (a new helper; `EXECUTION` owners unchanged), is never judged stale from the attaching command's process id and is never broken automatically. `SESSION_OPEN` lets the adapter open the lease only once its own conditions hold and releases an unconfirmed one; `SESSION_REQUIRED` verifies session and owner without taking anything, so a `READ_ONLY` capability can require a session; `SESSION_CLOSE` releases after verification or breaks exactly the inspected lease (`break_lease` gains `expected_token`). An `EXECUTION` writer meeting a session gets `LIVE_SESSION_HELD` before its adapter runs. `ExecutionRequest.session_id` (CLI `--session-id`) is accepted only where a session is named. Registration rules keep session requirement and writer-lease acquisition separate.
- **Foundation: `OUTCOME_UNKNOWN`** (exit code 9): an external operation may have started or completed but its final effect cannot be established; nothing is retried and the caller must re-read the state. `LIVE_OUTCOME_UNKNOWN` maps to it. It is never used for slowness.
- **Unity live plane** (`gpos/tools/unity/live.py`, `live_ipc.py`, `live_status.py`, `identity.py`, `bridge_install.py`): `unity.live-install-bridge` (`DEPLOY`, `MUTATING`, `STATELESS`, `OFFLINE_ANALYSIS`, closed projects only, fixed package, idempotent, never overwrites), `unity.live-status`, `unity.live-attach`, `unity.live-detach`, `unity.live-inspect`, `unity.live-enter-playmode`, `unity.live-pause`, `unity.live-resume`, `unity.live-exit-playmode`. None produces evidence; Play Mode success means only the Editor's state.
- **The fixed bridge package** `com.gpos.live-bridge` 1.0.0 (protocol `gpos.unity.live/1`) with deterministic `.meta` files and a release manifest; Editor-only; dormant in import workers and in every batch-mode Editor with no switch; closed command set over atomic, bounded, strict local files; a per-boot request journal for at-most-once; start deadlines; withdrawal; Domain Reload recovery that never replays; Human approval of attach and, separately, of stale-session recovery, inside the Editor only.
- Diagnostics: `LIVE_SESSION_HELD`, `LIVE_SESSION_MISMATCH`, `LIVE_SESSION_STALE`, `LIVE_SESSION_UNRESPONSIVE`, `LIVE_BRIDGE_ABSENT`, `LIVE_BRIDGE_UNAVAILABLE`, `LIVE_BRIDGE_UNTRUSTED`, `LIVE_BRIDGE_INCOMPATIBLE`, `LIVE_BRIDGE_SOURCE_CORRUPT`, `LIVE_PROJECT_IDENTITY_MISMATCH`, `LIVE_GRANT_INVALID`, `LIVE_BIND_REFUSED`, `LIVE_STATE_REFUSED`, `EDITOR_BUSY`, `LIVE_APPROVAL_NOT_GRANTED`, `LIVE_APPROVAL_REJECTED`, `LIVE_REQUEST_EXPIRED`, `LIVE_REQUEST_WITHDRAWN`, `LIVE_TRANSITION_FAILED`, `LIVE_OUTCOME_UNKNOWN`, `LIVE_PROTOCOL_ERROR` and the informational `LIVE_BRIDGE_INSTALLED`, `LIVE_BRIDGE_ALREADY_INSTALLED`, `LIVE_SESSION_ATTACHED`, `LIVE_SESSION_DETACHED`, `LIVE_SESSION_RECOVERED`.
- [tools/unity-live-bridge.md](tools/unity-live-bridge.md).
- Tests: `tests/test_unity_live.py` (fast groups against a Python protocol stand-in; real groups with lab-owned batch-mode Editors and the test-only testkit package), `tests/test_unity_live_bridge_core.py` (the bridge's C# core, compiled with the Mono bundled with Unity), `tests/mutate_unity_live.py`, `tests/generate_live_bridge_manifest.py`.

### Changed

- The `unity` descriptor's `state_model` is `STATEFUL` (it manages the live session); each batch capability stays `STATELESS`; `adapter_kind` stays `CLI`, with a compatibility note for the two planes. The batch plane is otherwise unchanged; a live session makes a batch run `LIVE_SESSION_HELD` before Unity is launched.
- Foundation and Unity suites and harnesses gained the session, lease-mode and outcome tests and mutations; boundary anchors evolved for the five new Unity modules and the twelve Unity capabilities.

### Notes

- Background limitation: a windowed Editor may be throttled while unfocused; the bridge keeps working, Play Mode state can be controlled, but gameplay progress is not guaranteed while unfocused. No Unity preference is changed and nothing is focused to change this.
- Alpha.16 installs the bridge only into a closed project; a changed bridge in a later release will need its own reviewed upgrade path.

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
