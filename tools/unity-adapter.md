# Unity engine adapter: batch plane (Phase 2C-5)

This page describes the batch plane. The same `unity` adapter also has a live Editor plane (Phase 2C-6A), described in [unity-live-bridge.md](unity-live-bridge.md), with Scene authoring (Phase 2C-6B1), described in [unity-live-authoring.md](unity-live-authoring.md), asset references and asset authoring (Phase 2C-6B2A), described in [unity-live-assets.md](unity-live-assets.md), and prefab authoring (Phase 2C-6B2B), described in [unity-live-prefabs.md](unity-live-prefabs.md).

Code: [`gpos/tools/unity/`](../gpos/tools/unity/__init__.py) · adapter id `unity` · status: the first production engine adapter. It is built on the frozen [tool adapter foundation](adapter-foundation.md), extended only by the `TOOL_INHERENT` network semantic (see [Network](#network)).

The batch plane answers three questions about one Unity project inside a GPOS project:

1. is it a supported Unity project, and which exact Editor does it require (static inspection);
2. what do its EditMode or PlayMode tests report when the Unity Test Framework runs them in a fresh batch-mode Editor;
3. is its existing build configuration supported, and what does the fixed Build Core produce from that inspected configuration.

**The batch plane.** It has no live Editor session; that is the [live plane](unity-live-bridge.md). The batch plane has no authoring at all; the live plane authors the open Scene, creates or edits Materials and ScriptableObjects, and creates, instantiates and edits regular prefabs through [fixed](unity-live-authoring.md) [commands](unity-live-assets.md) [only](unity-live-prefabs.md). The batch plane includes the [Build Core](unity-build.md), which uses the fixed `Gpos.LiveBridge.Build.BuildEntry.Run` method. Neither plane provides capture, deployment, profiling, video or audio; prefab apply, revert, unpack, Variant, nested-prefab or Prefab Mode authoring, import settings or other asset authoring; caller-selected `-executeMethod`, arbitrary C# or menu invocation; general package installation or API migration. Each extension needs its own Human Review.

**This is not a Unity automation interface.** The caller never supplies an executable, an Editor version, a method, C#, a test filter or category, a graphics mode, a network destination, a registry, a proxy, a credential or arbitrary Unity arguments. Project inspection and test execution take `unity_project`; a Build Core build also requires the inspection's `expected_configuration_token` and the contract-bound `request.build_revision`. The build contract defines their exact formats and provenance limitations.

## Identity

| Field | Value |
|---|---|
| `adapter_id` | `unity` |
| `target_tool` | Unity Editor |
| `tool_family` | `ENGINE` |
| `adapter_kind` | `CLI` |
| `state_model` | `STATEFUL` (the adapter manages the live plane's long-lived session; each batch capability is `STATELESS`) |
| platforms | `MACOS`; `WINDOWS` for the batch plane (alpha.25), bounded Live/Scene-authoring support (alpha.26) and the alpha.29 bounded Build Core (14 capabilities; [Windows contract](../adapters/windows-build-core.md)) |
| network | `TOOL_INHERENT`, with a disclosure |
| TEST_ONLY | no |

`default_registry()` now contains exactly `adb`, `blender`, `ffmpeg`, `ffprobe`, `git`, `player` and `unity`. The adapter is not an agent adapter and is not in the registry's `adapter_ids`.

## Capabilities

| Capability | Category / class | Context | Lease | Artifacts | Evidence |
|---|---|---|---|---|---|
| `unity.inspect-project` | `INSPECT` / `READ_ONLY` | `OFFLINE_ANALYSIS` | none | none | none |
| `unity.run-editmode-tests` | `RUN` / `MUTATING` | `AUTOMATED_TEST` | single writer, the resolved GPOS project root | `results` (`REPORT`), editor-log (`LOG`) | `TEST_EVIDENCE` in `AUTOMATED_TEST` |
| `unity.run-playmode-tests` | `RUN` / `MUTATING` | `AUTOMATED_TEST` | as above | as above | as above |
| `unity.inspect-build-configuration` | `INSPECT` / `MUTATING` | `EDITOR` | as above | editor-log (`LOG`) | none |
| `unity.build-player` | `BUILD` / `MUTATING` | `EDITOR` | as above | build-manifest (`REPORT`), editor-log (`LOG`) | none |

The two build capabilities (Phase 2C-7) are described in [unity-build.md](unity-build.md): the same batch plane, Editor discovery, version rule, package-source preflight, Package Manager isolation and project-lock proof, with one fixed build command instead of the test command.

**Alpha.27 clarification:** Windows CLASSIC x64 Mono Build Core mechanics are implemented and test-only qualified with Bridge 1.7.0. Production `unity.build-player` and `unity.inspect-build-configuration` remain `PLATFORM_UNSUPPORTED` pending the separately authorized D-G1 security remediation; Windows Git provenance remains unavailable. The Windows contract is documented in [adapters/windows-build-core.md](../adapters/windows-build-core.md). The alpha.25 and alpha.26 qualification facts below retain their original scope.

**Historical alpha.28 Windows Git gate:** Human authorization enables Windows Git provenance for its bounded qualified subset. Unity production availability stays unchanged: exactly 12 Windows capabilities; both Build Core capabilities still return `PLATFORM_UNSUPPORTED` pending their own subsequent qualification and approval. Caller-supplied revisions remain caller-supplied. Windows Git-unavailable statements in the alpha.25–alpha.27 descriptions below are historical release facts, not the current Git gate.

**Alpha.29 production:** Subsequent Human approval enables only `unity.inspect-build-configuration` and `unity.build-player` on Windows, bringing the allowlist to 14. The frozen alpha.25–alpha.28 availability descriptions remain historical release facts. The existing Unity 6000.6.4f1 CLASSIC x64 Mono restrictions and Bridge 1.7.0 are unchanged. Unity does not invoke Git or verify `build_revision`; separate pre/post Git observations and full consumer revalidation establish the qualified workflow. Manifest attribution remains CALLER_SUPPLIED, not authenticated Git attestation. Release metadata does not change the accepted gate implementation; freeze completion requires its separate identity checks.

Input `unity_project` (all five): a path relative to the GPOS project root, default `.`. It is resolved (symbolic links followed) and must stay inside the root: no absolute path, no `..`, no escaping link. The directory must contain `Assets/`, `Packages/`, `ProjectSettings/` and a regular file `ProjectSettings/ProjectVersion.txt`.

**`unity.inspect-project`** starts no process and needs no installed Editor (`requires_tool` is false); it works with zero, one or several Editors installed and never compares the project with an installed Editor. It reports the project path, the exact Editor version the project requires (and its revision when recorded), and a summary of the package sources of `Packages/manifest.json` and `Packages/packages-lock.json`, after running the package-source preflight below.

**The test capabilities** need explicit mutation consent, support a dry run, and default to a 1800 s timeout (at most 3600 s). They run a platform's **whole** test set; there is no filter.

## Editor discovery and the version rule

The Editor is found only under the Unity Hub Editor root, `/Applications/Unity/Hub/Editor/<version>/Unity.app/Contents/MacOS/Unity`: every path component must be a real directory entry with exactly that name, the version directory must match the Unity version grammar (for example `6000.5.8f1`), and the executable must be a regular, executable, non-symlinked file. **PATH is never used**: a Unity command-line tool that is not the Editor may be installed under the name `unity`. A custom Hub install location is unsupported in this release.

The probe runs only `Unity -version` and requires it to print exactly the installation's version.

| Hub Editors found | Probe |
|---|---|
| none | `UNAVAILABLE` |
| exactly one | `AVAILABLE`, with that version |
| more than one | `VERSION_UNSUPPORTED`: per-project Editor selection needs a reviewed foundation extension, because the foundation records one probed tool per adapter |

A test run requires the project's `m_EditorVersion` to equal the probed Editor's version exactly (major, minor, patch and stream suffix). Otherwise the run is `INCOMPATIBLE` with `ENGINE_EDITOR_VERSION_UNAVAILABLE`, before any launch. There is no fallback, upgrade or downgrade.

## Package-source preflight

Before any launch, and also for inspection and dry runs, the project's package configuration is checked statically. A violation is `ENGINE_PROJECT_UNSUPPORTED`; the project is never rewritten, no dependency is removed or reinterpreted, and the message names the rule and the package, never a URL or a path.

`Packages/manifest.json` (strict UTF-8 JSON object, at most 1 MiB, no repeated key):

- top-level keys limited to `dependencies`, `enableLockFile`, `resolutionStrategy`, `testables` and `pinnedPackages`; `scopedRegistries` only absent or empty; any other key (for example a legacy `registry` override) is refused;
- each dependency is either a registry version (`1.2.3`, optionally with a pre-release or build suffix) or `file:<path>` naming an existing local package folder (with `package.json`, not a Git working copy) or `.tgz` that resolves inside the GPOS project root;
- refused: every Git form (`https://….git`, `git+https://`, `ssh://`, `git+ssh://`, `user@host:…`, `git+file://`, `git://`, `file:///….git`, `?path=`, `#revision`), every `http://` or `https://` value, `file://` URL forms, and anything else.

`Packages/packages-lock.json`, when present (at most 8 MiB): every entry's `source` must be `builtin`, `registry` (with Unity's default registry URL), `embedded`, `local` or local-tarball; `git` and anything else are refused, and local entries must resolve inside the root.

## Package Manager isolation

For every Unity launch the adapter creates two empty GPOS-owned files in the execution workspace and sets, as absolute paths:

| Variable | Value |
|---|---|
| UPM_USER_CONFIG_FILE | `<workspace>/upm-user.toml` |
| UPM_GLOBAL_CONFIG_FILE | `<workspace>/upm-global.toml` |
| UPM_CACHE_ROOT | `.game/gpos-runtime/unity/upm-cache/` (persistent, non-authoritative) |

The user's own Package Manager configuration (`~/.upmconfig.toml`) and the machine's global configuration are therefore not part of an execution: no token, registry, proxy or credential is inherited, and neither file is read or modified. The rest of the environment is the foundation's allowlist, which excludes proxy variables. A real run confirms in Unity's own Package Manager log that it uses these three values.

## Command

```text
Unity -batchmode -projectPath <unity-project> -logFile <workspace>/editor.log -upmLogFile <workspace>/upm.log
      -cacheServerEnableDownload false -cacheServerEnableUpload false
      -runTests -testPlatform EditMode|PlayMode -testResults <workspace>/results.xml
```

The working directory is the workspace. Never passed: `-quit` (the Unity Test Framework exits the Editor when a run completes; `-quit` could end it before the tests finish), `-accept-apiupdate` (without it the API Updater does not run in batch mode, so authored source is never migrated; a project needing migration fails to compile, which is an ordinary failure), `-noUpm` (it prevents the package-provided Test Framework from compiling), `-nographics`, `-executeMethod`, credentials, build targets or Accelerator endpoints. The recorded command replaces the project and workspace paths with `<unity-project>` and `<workspace>/…`.

## Concurrency

The foundation takes the single-writer lease `EDITOR_PROJECT:<resolved GPOS project root>` **before** Unity is launched and releases it afterwards; symbolic-link spellings of the same project map to the same lease. A held lease is a `CONFLICT` and nothing is launched; a stale lease is reported, never broken. The live plane's SESSION lease is the same resource: while a live session holds the project, a batch run is `LIVE_SESSION_HELD` (`CONFLICT`) before Unity is launched.

Unity's own project lock is a second, independent layer (alpha.20, Phase 2C-6C). A running Editor holds an exclusive whole-file lock on `Temp/UnityLockfile`. A batch run that stops on a compile error exits without Unity's `Temp/` clean-up and leaves the file behind, unheld (measured on Unity 6000.5.8f1: more than 600 s in 3 of 3 runs, never clearing by itself; the next Unity launch accepts the project and replaces the file). The file's presence alone is therefore no longer a conflict. Before the workspace is prepared, and again immediately before the launch, the adapter proves the project's lock read-only ([`project_lock.py`](../gpos/tools/unity/project_lock.py), macOS):

- **process proof:** the kernel lists this user's processes; for each one whose kernel executable path is the discovered Hub Editor, the exact argument vector (KERN_PROCARGS2, never joined `ps` text) must carry exactly one absolute `-projectPath` that is this project (same file identity or real path — never a text or prefix match). Import workers count. A failed or truncated listing, an unreadable candidate, a missing, repeated, relative or malformed project argument, or a bound exceeded is `PROCESS_STATE_UNKNOWN`, never "no process";
- **lock proof:** the lockfile is `lstat`ed (a link or a non-regular file is unknown), opened read-only with O_NOFOLLOW and O_NONBLOCK, `fstat`ed (the same regular file) and queried with F_GETLK. No lock is taken, and the file is never written, truncated, renamed or removed.

| Effective state | When | Result |
|---|---|---|
| `NO_LOCK` | no lockfile, and `NO_MATCH_PROVEN` before and after | the run proceeds with no delay |
| `ACTIVE_EDITOR` | `MATCHING_EDITOR` at either proof, or the OS reports the lock held | `ENGINE_PROJECT_LOCKED`; no second Editor is launched |
| `ORPHAN_UNHELD` | a regular lockfile nobody holds, and `NO_MATCH_PROVEN` before and after the lock query | the run proceeds; `ENGINE_PROJECT_ORPHAN_LOCK` (`INFO`) records that GPOS left the file alone and Unity applied its own project-lock semantics |
| `LOCK_STATE_UNKNOWN` | anything that cannot be proven, or another platform | `ENGINE_PROJECT_LOCKED`; nothing is launched |

`ENGINE_PROJECT_LOCKED` carries `lock_state` in its details, and the result's data carries `project_lock`. The race between the final proof and the launch remains: Unity arbitrates it with its own project lock, and the second instance it refuses is classified `ENGINE_PROJECT_LOCKED` as before, never retried. Processes of other users are not listed; an Editor of another user that holds the project is still seen through the lock query. The rule is measured on macOS; Windows has its own, separately measured proof with the same outcomes ([Windows](#windows-alpha25)). `unity.live-install-bridge` keeps its closed-project rule: any existing lockfile is `ENGINE_PROJECT_LOCKED`.

## Results

The NUnit results are read strictly: at most 64 MiB, no document type or entity declaration, root test-run element, integer counts that add up to `total` and match the number of test-case elements, and a known result value. Classification always uses the results, the exit code and Unity's log together; an exit code is never read alone (exit 1 has been observed for a compile failure, a refused second instance and a disabled Package Manager alike).

| What happened | Status | Diagnostic | Evidence |
|---|---|---|---|
| results, at least one test, exit 0, no failures | `SUCCESS` | | `TEST_EVIDENCE` |
| results, at least one test, exit 2, failures | `SUCCESS` | `TESTS_FAILED` | `TEST_EVIDENCE` recording the failures |
| results with zero tests (Unity exits 0) | `FAILED` | `ENGINE_TESTS_NOT_EXECUTED` | none |
| results that contradict the exit code, or unreadable results | `FAILED` | `EXECUTION_FAILED` | none |
| no results, the log shows a script compilation failure | `FAILED` | `EXECUTION_FAILED` (cause recorded) | none |
| no results, the log shows no usable licence | `UNAVAILABLE` | `ENGINE_LICENSE_UNAVAILABLE` | none |
| no results, Unity refused a second instance | `CONFLICT` | `ENGINE_PROJECT_LOCKED` | none |
| no results, the Package Manager server could not start (measured on Windows) | `FAILED` | `EXECUTION_FAILED` (cause recorded) | none |
| no results, anything else | `FAILED` | `EXECUTION_FAILED` (unclassified) | none |
| timeout | `TIMED_OUT` | `EXECUTION_TIMEOUT` | none; artifacts incomplete |

Failed tests are evidence of failure, a successful tool execution, and never a passing quality gate.

## Evidence

A candidate is `TEST_EVIDENCE` captured in `AUTOMATED_TEST` for the request's subject, referencing only the `results` artifact (the raw Unity NUnit XML, which embeds local absolute paths). Its limitations state that the results come from a batch-mode Editor on the development host (not target-runtime, device or performance evidence; PlayMode runs inside the Editor), that the whole platform test set ran, that Unity-managed project and user-level Unity state changed, and that failed tests are never a passing result. Provenance records the subject revision and the Editor version; no build, platform or device field is invented. The Editor log is a `LOG` artifact only: it contains machine and session identifiers and local paths, and it is never evidence. Captured process output follows the foundation normally: bounded, redacted, with truncation reported.

## Side effects

Opening a Unity project changes state, which is why the test capabilities are `MUTATING` and require consent. Nothing is rolled back.

- **In the project** (observed on Unity 6000.5.8f1): `Library/`, `Temp/`, `Logs/`, `UserSettings/`; `.meta` files for every asset; `Packages/packages-lock.json`; `ProjectSettings/*.asset` (including repeated `ProjectAuditorSettings.asset` rewrites and settings written by PlayMode runs); the revision line of `ProjectSettings/ProjectVersion.txt` when it is missing. Scripts, assembly definitions and the manifest are not changed.
- **In the GPOS runtime area**: the execution workspace and the Package Manager cache.
- **Outside the project** (Unity-owned; GPOS claims no isolation from the Unity user profile): Unity Editor preferences (the recent-project keys `LastUsedProjectPath`, `kProjectBasePath`, `kWorkspacePath`; `UnityConnectUrlConfiguration`; the session keys `unity.editor_session_count` and `unity.editor_sessionid`), a per-product player-preferences file created by PlayMode runs, `~/Library/Application Support/Unity/CoreBusinessMetrics.db`, Unity Editor global values and configuration, the Bee and GI caches under `~/Library/Caches/com.unity3d.UnityEditor/`, the licensing and entitlement-audit logs, and a per-run Unity Licensing Client started by the Editor. No other Editor preference is accepted: the test suite fails if any other key changes.

## Network

The descriptor declares `TOOL_INHERENT`, allowlisted for `unity` only. GPOS provides no networking capability, takes no URL, host, endpoint, registry, proxy or credential, and originates no network operation. The external Unity process tree may nevertheless use the network as a consequence of running: the Editor and its Licensing Client (licensing and entitlement), Unity services configuration, the Package Manager resolving supported dependencies from Unity's default package service, and project or test code that Unity loads and runs. The Editor also opens local PlayerConnection listening and discovery sockets. Remote package sources are refused before launch, the Package Manager configuration is GPOS-owned, and Accelerator upload and download are disabled; these are defence in depth, not confinement. **No operating-system network confinement is claimed.**

## Dry run

A dry run validates the project path and layout, the version file, the exact Editor, the package sources, the Unity lock proof (reported as `project_lock`) and output collisions, and returns a three-line plan. It starts no Unity process and creates no workspace, package cache, results or evidence; it cannot know whether packages resolve, scripts compile or tests execute.

## Security review

| Concern | Finding |
|---|---|
| caller executable, argument, method, C#, filter, graphics or network setting | none: contract-bound inputs only, with a fixed executable, argv and Build Core method; tested at the descriptor, source and runtime-argv level |
| PATH or a non-Editor `unity` tool | never used: Hub root discovery with exact names |
| silent Editor substitution or project upgrade | refused: exact version or `ENGINE_EDITOR_VERSION_UNAVAILABLE`; never `-accept-apiupdate` |
| project-controlled network sources | refused before launch: scoped registries, registry overrides, Git and URL dependencies, Git lock sources |
| inherited user Package Manager configuration or proxies | none: GPOS-owned empty configuration files and cache; proxies not in the environment allowlist |
| concurrent writers | GPOS lease on the resolved root before launch; Unity's lock proven read-only, an active or unprovable one refused, never deleted or modified |
| exit code 0 read as a pass | never: valid results with at least one test are required |
| results-file attacks | bounded size, no DTD or entities, strict counts |
| subprocess or network code in GPOS | none: the Unity modules import neither; `process.py` gained nothing |
| sandboxing | none claimed |

## Limitations

- macOS, validated with Unity 6000.5.8f1; Windows batch plane and bounded Live/Scene slice, qualified with Unity 6000.6.4f1; exactly one Hub-installed Editor. Alpha.27 qualification retains its original test-only attribution and alpha.28 retained closed Build Core gates. Alpha.29 enables the separately qualified production Windows Build Core for Unity 6000.6.4f1 CLASSIC StandaloneWindows64 x64 Mono nondevelopment; other Windows configurations refuse.
- The log signatures used for classification are version-specific; an unknown failure is reported as unclassified.
- A run that stops on a compile error, or is killed by its timeout, leaves `Temp/UnityLockfile` behind; the next run proceeds only when the read-only proof shows it unheld with no Unity process for the project (macOS).
- The results XML embeds local absolute paths; the Editor log contains machine and session identifiers.
- Package Manager user-level settings are isolated, but the Unity process tree is not network-confined.

## Windows (alpha.25)

Qualified on Windows 11 Enterprise 10.0.26200 with CPython 3.14.8 x64, local NTFS, and **Unity 6000.6.4f1** for the **batch plane only**: `unity.inspect-project`, `unity.run-editmode-tests` and `unity.run-playmode-tests`. Every other capability — the live plane, Scene, asset, prefab and source authoring, and Build Core — is refused on Windows with `PLATFORM_UNSUPPORTED` (`INCOMPATIBLE`) inside the adapter before anything runs, and the probe reports it unavailable. Alpha.26 adds the live session and the Scene-authoring slice on Windows with bridge 1.6.0 ([§ Windows live bridge](#windows-live-bridge-alpha26)); Build Core builds macOS players only. The macOS declarations and behaviour are unchanged.

**Discovery and version.** The Hub root is `<Program Files>\Unity\Hub\Editor`, with Program Files taken from the Windows known folder (never an environment variable); the Editor is exactly `<version>\Editor\Unity.exe` beneath it, every component an exact directory entry and none of them a reparse point. PATH is never used: neither the Unity CLI (`unity.exe` under the user's local application data) nor Unity Hub's own `unity.exe` is ever taken for the Editor. `Unity.exe -version` (measured: `6000.6.4f1` and CRLF on its redirected standard output) must print exactly the version folder's name, as on macOS.

**Environment.** The Unity process gets the foundation's Windows allowlist plus exactly ProgramData and LOCALAPPDATA: measured, the Package Manager server does not start without both, and nothing else was needed. The GPOS-owned Package Manager configuration and cache are unchanged. Nothing else of the parent environment is inherited. This is not user-profile isolation: Unity finds the user's folders through Windows itself.

**Project lock (D-U4).** The outcomes are those of macOS — `NO_LOCK`, `ACTIVE_EDITOR`, `ORPHAN_UNHELD`, `LOCK_STATE_UNKNOWN` — and the proof runs, as there, before the workspace is prepared and again immediately before the launch ([`project_lock.py`](../gpos/tools/unity/project_lock.py), [`host_win32.py`](../gpos/tools/unity/host_win32.py)).

What was measured first: Unity opens `Temp/UnityLockfile` exclusively, and **a data handle on it opened by any other process makes a starting Editor abort** ("another Unity instance is running with this project open"). An attributes-only handle does not, but it cannot see the lock. The proof therefore never opens the file at all:

- **process proof:** every `Unity.exe` of one bounded process snapshot is opened once for a limited query, and every fact is read through that one handle, so a reused process id can never mix two processes: still running; the image, which must be the discovered Editor (the CLI and Hub's `unity.exe` are not); the token's user, which must be this user (another user's Editor is still seen by the lock query); and the command line, which must carry exactly one absolute `-projectPath` naming this project. Paths are compared as Windows does — `/` and `\`, letter case — through the file system's own resolution. Import workers carry their own `-projectPath` (with `/`) and count as the project's Editor. A `Unity.exe` that cannot be opened, or whose image, user or command line cannot be read, is `PROCESS_STATE_UNKNOWN`;
- **lock query:** the lockfile is `lstat`ed (a reparse point or anything but a regular file is unknown) and Restart Manager — its documented, read-only session workflow (`RmStartSession`, `RmRegisterResources` for the one file, `RmGetList`, `RmEndSession`; never `RmShutdown` or `RmRestart`) — lists the processes using it. Each listed owner is opened and must still be running with exactly the start time Restart Manager reported; the file must keep its identity across the query. A verified owner is `HELD`; an owner that cannot be verified, a failed query or a changed file is unknown;
- **two answers:** after the second process proof the lock is queried again, and `ORPHAN_UNHELD` needs both queries empty for the same file. One empty answer never proves an unheld lockfile (Restart Manager also answers "nobody" for a file that does not exist).

Why Restart Manager, measured: it named the Editor with its exact process start time (equal to the creation time the process handle reports), took 50–60 ms per query while the file was held and about 6 ms otherwise, and 222 queries made while an Editor started over an orphan lockfile did not disturb it. Its one side effect is the session's own transient key under the user's `Software\Microsoft\RestartManager` registry key, removed when the session ends. The command line is read with `NtQueryInformationProcess` (process command-line information, Windows 8.1 and later), which Microsoft documents as subject to change: its failure is unknown, never "no Editor". The whole assessment is time-bounded (20 s). Between the final proof and the launch the race remains, as on macOS: Unity arbitrates it, and the instance it refuses is `ENGINE_PROJECT_LOCKED`.

**Processes.** The batch Editor runs inside the alpha.23 Job Object, unchanged. Measured: a cold run starts several hundred processes (the build backend's compiler steps, the Package Manager server, the shader compiler, import workers — themselves `Unity.exe` — and Unity's own watchdogs), all inside the job; none needed to break away. On a normal exit Unity ends its own children; on a failed or aborted run the foundation terminates what was left (`PROCESS_DESCENDANTS_TERMINATED`, `INFO`); a timeout ends the whole tree. Process identity is never taken from parent process ids, which Windows reuses.

**Licensing.** The Editor connects to the licensing client Unity Hub already runs, over its named channel; that client is not part of the job, and GPOS never starts, stops, configures or inspects it. Licence files are never read, copied or changed by GPOS (the licensing client itself rewrites its entitlement file and logs during ordinary licensing). A run with no licensing client running was not measured.

**Side effects on Windows** (measured; nothing is rolled back): the project's `Library/`, `Temp/`, `Logs/`, `UserSettings/` and generated files; Editor preferences in the registry under the user's `Software\Unity Technologies\Unity Editor 5.x` (a first run writes several dozen values; later runs rewrite the recent-project, connection and session values and `RecompileTracker_Times`, which the test suite accepts — any other change fails it); the compiler cache under the user's local application data `Unity\Caches\bee`; analytics under `AppData\LocalLow\Unity`; compiler warm-up files and `DefaultCompany` and `UnityCBMCrashes` folders in the temporary directory; and the licensing logs. The Editor joins player-connection multicast on the local network in batch mode.

**Provenance.** Windows Git provenance is unavailable in this release: the request's subject revision is recorded as given, and no source or build revision is ever inferred.

## Windows live bridge (alpha.26)

Qualified on the same host (Windows 11 Enterprise 10.0.26200, CPython 3.14.8 x64, local NTFS, Unity 6000.6.4f1) with **bridge 1.6.0** (`com.gpos.live-bridge`, protocol `gpos.unity.live/5` unchanged). Windows offers **12** Unity capabilities: the batch plane (alpha.25) and, live, `unity.live-install-bridge`, `unity.live-status`, `unity.live-attach`, `unity.live-inspect`, `unity.live-detach` (with Human-approved recovery), `unity.live-object-inspect`, `unity.live-create-gameobject` (an empty GameObject), `unity.live-set-transform` and `unity.live-save-scene` (an already saved Scene, to its own path). The other **35** — Play Mode, the other Scene edits, components and properties, materials, assets, prefabs, sources and Build Core — are refused with `PLATFORM_UNSUPPORTED` before anything runs, and the bridge separately serves only the 14 commands this slice needs: any other command is answered `REFUSED UNKNOWN_COMMAND` before it is admitted, journaled or dispatched (`Protocol.WindowsCommands`). No Scene creation and no primitive exist on any platform.

**One package, two platform views.** Bridge 1.6.0 is one audited package for Windows and macOS. Its Windows code sits in `#if UNITY_EDITOR_WIN` branches; the macOS view of every changed file — the source with those branches dropped — is byte for byte the frozen 1.5.0 source but for the version constant (tests/test_posix_parity.py P10). The macOS runtime of 1.6.0 was not executed (NOT_RUN). The frozen 1.5.0 manifest is kept byte for byte as `live_bridge/history/1.5.0.json`, pinned at `b7f4775d…`; an exact installed 1.5.0 is upgraded with the existing crash-recoverable transaction.

**Native surface.** The Windows view imports exactly one native function, `kernel32.dll!MoveFileExW`, with exactly two flag sets: `0` (a same-volume move that never replaces) and `MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH` (the atomic replacement of a state file). It has no libc import; the asset and prefab helpers that use libc on macOS refuse on Windows and are unreachable there. Everything else is managed: deletion, directories, attributes and paths.

**What NTFS does (N1, measured without Unity, both sides in separate processes).** A no-replace move onto an existing name fails (`ERROR_ALREADY_EXISTS`) and changes nothing; across volumes it fails (not the same device) without copying. An atomic replace is never seen missing or partial by a reader, but fails with `ERROR_ACCESS_DENIED` while any reader holds the target, even one that shares delete: it is retried, bounded (20 attempts 1 ms apart replaced 3000 of 3000 against a reader looping on the target). A publish-once race between two writers has exactly one winner. And **two renames of the same file at the same moment can both succeed**: a rename opens its source by name and renames through that handle, so when both sides open it before either renames, the one file is renamed twice and the last rename decides where it rests (999 of 1000 at one instant, within about ±100–500 µs).

**Claim and withdrawal: RENAME → PIN → VERIFY → DECIDE.** On Windows a rename therefore decides nothing. Each side, after its own rename, opens the file at its destination with a share mode that excludes delete — the bridge with `FileShare.Read` (`WindowsFiles.TakeRequest`), GPOS with the foundation's `paths_win32.open_file_for_read(deny_writers=True)` (pinned ancestry, no reparse point, one link). NTFS cannot grant that while any rename handle on the file is open, and once it is granted the file can no longer be reached through `requests/`, so the decision holds after the pin is closed:

- the bridge executes a request only after its pin, on a plain file whose content names its id; a request whose file moved away is GPOS's and is left untouched; a pin that cannot be acquired within 20 ms is answered `INTERRUPTED NOT_REPLAYED` and never executed;
- GPOS reports `WITHDRAWN` only when its pin holds exactly the file it published — the volume and file id recorded at publication, and the same bytes. A withdrawal that is blocked, cannot be pinned or finds another file is undecided: the call then reports the bridge's answer or `UNKNOWN`, never `WITHDRAWN`, and never publishes again.

N1-B qualified this before it was built: 28 deterministic fault-injection scenarios (each side's rename held in flight while the other renames, both renames landing in either order, pins blocked past their budget, files moved or replaced, readers with and without delete sharing, a writer handle, either process ending after its rename or after its pin, recovery after a restart, interrupted publications) and 3900 timed cross-process races: no outcome had both sides decide, a withdrawn request never executed, no request executed twice. tests/test_unity_windows_live.py WLC repeats this against the production code on both sides.

**Publication and reading.** GPOS publishes a request by a no-replace rename (no hard link: a file with two names is never trusted by the reader), records its file id, and removes only its own interrupted publication temporaries (`.tmp-<32 hex>`, older than 10 minutes). It reads every bridge file through the pinned, alias-refusing reader with delete sharing, so a read never blocks the bridge. A response that cannot be read after publication is waited for like a missing one and is finally `UNKNOWN`: the request may have run. The bridge reads the SESSION lease with every share mode, so it never blocks GPOS replacing or releasing it.

**Paths.** The bridge spells its GPOS root and Unity project exactly as GPOS's `Path.resolve()` does — a drive-letter path, every component in its on-disk case, `\` separators — because the SESSION lease is found by that text, and the project's relative path with `/` (the project key). A UNC or device path, a reparse point on the way, an 8.3 short name or an ambiguous spelling leaves the bridge dormant. On the GPOS side every link check (`live_ipc`, `identity`, `bridge_install`) also refuses Windows reparse points (junctions), which a symbolic-link test does not see.

**Editor identity (R2).** `editor_started_utc` is `Process.GetCurrentProcess().StartTime` — the operating system's creation time of the Editor process, not the bridge's start — written with seven fractional digits. Measured on every Editor start of the qualification: parsed to a FILETIME it equals `GetProcessTimes` read through a handle on the same process to the 100 ns tick. The Windows proof (`project_lock.windows_editor_identity`, through the alpha.25 `host_win32` handle; no second process scanner) is therefore exact: `ALIVE` needs the exact creation time, the discovered Hub Editor image, this user, a running process and an exact single absolute `-projectPath` naming the project; a process created later holds a reused id (reused); anything else is `UNKNOWN`. There is no tolerance and no proof from a process id or name alone.

**SESSION integrity.** The frozen session contract is unchanged; Windows adds two reconciliations. A bind whose `session.json` cannot be published is undone in the Editor before the `FAILED` answer, so a failed bind is always a known unbound Editor (the grant stays consumed: one Human approval can never bind twice). Answering never throws, so a committed bind is never followed by a second, `FAILED` answer; a bind whose answer is lost is `LIVE_OUTCOME_UNKNOWN` and keeps its lease, and the status shows the Editor bound (`LIVE`). A bind the Editor never claimed is withdrawn and its lease released. Status still distinguishes a known bound session (`LIVE`), an unknown one (`UNRESPONSIVE`, "being bound") and a known unbound one (`STALE` once the binding grace has passed), and a stale session ends only through a Human-approved recovery in a running Editor.

**Approval.** A Human approves an attach or a recovery in the Editor. In the qualification that approval was **synthetic**: the test-only testkit presses the bridge's approval method for test-named owners. It is never installed by GPOS and is not evidence that a Human approved anything; an owner it does not name was never approved, and its attach expired.

**What it is not.** No live capability produces evidence; Scene authoring is Editor state only. Git provenance stays unavailable on Windows. Directory fsync is a no-op on Windows (NTFS journals its metadata); the live folder inherits the GPOS runtime area's Windows permissions (there is no `chmod 700` counterpart).

## Tests

```bash
python3 tests/test_unity_adapter.py
python3 tests/mutate_unity_adapter.py
python -X utf8 tests/test_unity_windows.py
python -X utf8 tests/mutate_unity_windows.py [--real]
python -X utf8 tests/test_unity_windows_live.py
python -X utf8 tests/mutate_unity_windows_live.py [--real]
```

The suite needs exactly one Hub-installed Unity Editor; otherwise it stops with UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C5 and never falls back to mocks. Every Unity project is generated by [`tests/unity_fixture_builder.py`](../tests/unity_fixture_builder.py) inside a temporary GPOS project. Stand-in programs cover only deterministic error cases: several or unconfirmed Editors, licence refusal, a refused second instance, missing, malformed, empty or contradictory results, and a timeout. A module guard fails the suite if any Unity Editor preference other than the accepted keys changes or if the user's Package Manager configuration files change (compared by existence, size and time only). The mutation harness runs the suite in its fast mode (GPOS_UNITY_TEST_FAST=1), which starts no real Unity process.
