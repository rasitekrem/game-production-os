# Unity engine adapter: batch plane (Phase 2C-5)

This page describes the batch plane. The same `unity` adapter also has a live Editor plane (Phase 2C-6A), described in [unity-live-bridge.md](unity-live-bridge.md), with Scene authoring (Phase 2C-6B1), described in [unity-live-authoring.md](unity-live-authoring.md).

Code: [`gpos/tools/unity/`](../gpos/tools/unity/__init__.py) · adapter id `unity` · status: the first production engine adapter. It is built on the frozen [tool adapter foundation](adapter-foundation.md), extended only by the `TOOL_INHERENT` network semantic (see [Network](#network)).

The adapter answers two questions about one Unity project inside a GPOS project:

1. is it a supported Unity project, and which exact Editor does it require (static inspection);
2. what do its EditMode or PlayMode tests report when the Unity Test Framework runs them in a fresh batch-mode Editor.

**The batch plane.** It has no live Editor session; that is the [live plane](unity-live-bridge.md). The batch plane has no authoring at all; the live plane authors the open Scene through [fixed commands](unity-live-authoring.md) only. Neither plane has capture, build, deployment, profiling, video or audio; prefab or asset authoring; `-executeMethod`, arbitrary C# or menu invocation; general package installation or API migration. Each needs its own Human Review.

**This is not a Unity automation interface.** The caller never supplies an executable, an Editor version, a method, C#, a test filter or category, a graphics mode, a network destination, a registry, a proxy, a credential or any Unity argument. The only input is `unity_project`.

## Identity

| Field | Value |
|---|---|
| `adapter_id` | `unity` |
| `target_tool` | Unity Editor |
| `tool_family` | `ENGINE` |
| `adapter_kind` | `CLI` |
| `state_model` | `STATEFUL` (the adapter manages the live plane's long-lived session; each batch capability is `STATELESS`) |
| platforms | `MACOS` only in this release |
| network | `TOOL_INHERENT`, with a disclosure |
| TEST_ONLY | no |

`default_registry()` now contains exactly `adb`, `blender`, `ffmpeg`, `ffprobe`, `git` and `unity`. The adapter is not an agent adapter and is not in the registry's `adapter_ids`.

## Capabilities

| Capability | Category / class | Context | Lease | Artifacts | Evidence |
|---|---|---|---|---|---|
| `unity.inspect-project` | `INSPECT` / `READ_ONLY` | `OFFLINE_ANALYSIS` | none | none | none |
| `unity.run-editmode-tests` | `RUN` / `MUTATING` | `AUTOMATED_TEST` | single writer, the resolved GPOS project root | `results` (`REPORT`), editor-log (`LOG`) | `TEST_EVIDENCE` in `AUTOMATED_TEST` |
| `unity.run-playmode-tests` | `RUN` / `MUTATING` | `AUTOMATED_TEST` | as above | as above | as above |

Input `unity_project` (all three): a path relative to the GPOS project root, default `.`. It is resolved (symbolic links followed) and must stay inside the root: no absolute path, no `..`, no escaping link. The directory must contain `Assets/`, `Packages/`, `ProjectSettings/` and a regular file `ProjectSettings/ProjectVersion.txt`.

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

Unity's own project lock is a second, independent layer. A `Temp/UnityLockfile` that already exists is `ENGINE_PROJECT_LOCKED` (`CONFLICT`) and nothing is launched; the adapter never deletes or bypasses it. A run killed by its timeout can leave that file behind, and a human must remove it. A second instance that Unity itself refuses is also classified `ENGINE_PROJECT_LOCKED`.

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

A dry run validates the project path and layout, the version file, the exact Editor, the package sources, the Unity lock and output collisions, and returns a three-line plan. It starts no Unity process and creates no workspace, package cache, results or evidence; it cannot know whether packages resolve, scripts compile or tests execute.

## Security review

| Concern | Finding |
|---|---|
| caller executable, argument, method, C#, filter, graphics or network setting | none: one input, `unity_project`; tested at the descriptor, source and runtime-argv level |
| PATH or a non-Editor `unity` tool | never used: Hub root discovery with exact names |
| silent Editor substitution or project upgrade | refused: exact version or `ENGINE_EDITOR_VERSION_UNAVAILABLE`; never `-accept-apiupdate` |
| project-controlled network sources | refused before launch: scoped registries, registry overrides, Git and URL dependencies, Git lock sources |
| inherited user Package Manager configuration or proxies | none: GPOS-owned empty configuration files and cache; proxies not in the environment allowlist |
| concurrent writers | GPOS lease on the resolved root before launch; Unity's lock refused, never deleted |
| exit code 0 read as a pass | never: valid results with at least one test are required |
| results-file attacks | bounded size, no DTD or entities, strict counts |
| subprocess or network code in GPOS | none: the Unity modules import neither; `process.py` gained nothing |
| sandboxing | none claimed |

## Limitations

- macOS only, validated with Unity 6000.5.8f1; exactly one Hub-installed Editor.
- The log signatures used for classification are version-specific; an unknown failure is reported as unclassified.
- A run killed by its timeout may leave `Temp/UnityLockfile`, which blocks later runs until a human removes it.
- The results XML embeds local absolute paths; the Editor log contains machine and session identifiers.
- Package Manager user-level settings are isolated, but the Unity process tree is not network-confined.

## Tests

```bash
python3 tests/test_unity_adapter.py
python3 tests/mutate_unity_adapter.py
```

The suite needs exactly one Hub-installed Unity Editor; otherwise it stops with UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C5 and never falls back to mocks. Every Unity project is generated by [`tests/unity_fixture_builder.py`](../tests/unity_fixture_builder.py) inside a temporary GPOS project. Stand-in programs cover only deterministic error cases: several or unconfirmed Editors, licence refusal, a refused second instance, missing, malformed, empty or contradictory results, and a timeout. A module guard fails the suite if any Unity Editor preference other than the accepted keys changes or if the user's Package Manager configuration files change (compared by existence, size and time only). The mutation harness runs the suite in its fast mode (GPOS_UNITY_TEST_FAST=1), which starts no real Unity process.
