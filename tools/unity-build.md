# Unity Build Core (Phase 2C-7)

This document describes the macOS Build Core contract introduced with Bridge 1.5.0. Current GPOS alpha.28 ships Bridge 1.7.0; the Human-authorized Windows production candidate enables the separately qualified CLASSIC x64 Mono contract in [adapters/windows-build-core.md](../adapters/windows-build-core.md). It has no new release freeze or version. The macOS behavior and technical rules below remain unchanged.

Code: [`gpos/tools/unity/build.py`](../gpos/tools/unity/build.py) and the adapter's build plane in [`gpos/tools/unity/adapter.py`](../gpos/tools/unity/adapter.py); in the bridge package, the batch-only entry [`Editor/Build/BuildEntry.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Build/BuildEntry.cs), its configuration reader [`Editor/Build/BuildConfiguration.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Build/BuildConfiguration.cs) and the Unity-free rules [`Editor/Core/BuildRules.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/BuildRules.cs) · adapter id `unity` · contract introduced with bridge `com.gpos.live-bridge` 1.5.0, live protocol unchanged at `gpos.unity.live/5` · status: builds a macOS Standalone Player (Mono) from a project's existing build configuration, into the execution workspace, bound by a build manifest.

| Capability | Class | What it does |
|---|---|---|
| `unity.inspect-build-configuration` | `INSPECT`, `MUTATING` | reads the existing build configuration in a fresh batch-mode Editor: active target, profile or classic mode, development, scenes, every rule below, whether it is buildable and its configuration token |
| `unity.build-player` | `BUILD`, `MUTATING` | builds exactly the inspected configuration in a fresh batch-mode Editor, validates the `.app`, commits it inside the execution workspace and writes the build manifest last |

Both are batch-plane capabilities: `STATELESS`, `EDITOR`, the `EXECUTION` single-writer lease on `EDITOR_PROJECT:<GPOS project root>`, the read-only project-lock proof before the workspace is used and again immediately before the launch, the exact Editor version, and the batch plane's Package Manager configuration isolation. They need explicit mutation consent and support a dry run (a plan only). Neither produces evidence. The inspection is `MUTATING` because opening a project in Unity changes `Library/`, user and tool state; it changes no build setting. Timeouts: 600 s (at most 1800 s) for the inspection, 1800 s (at most 3600 s) for a build.

**Scope:** macOS Standalone Player, the Mono scripting backend, the target that is already active. **Never:** Android, AAB, custom signing, IL2CPP, a target switch, creating, activating, deactivating, choosing or editing a Build Profile, a settings change, running, installing or deploying the Player.

## Inputs

- `unity_project` — the Unity project inside the GPOS project root (as for every Unity capability).
- `expected_configuration_token` (build only) — the 64-hex token an inspection returned.
- `request.build_revision` (build only, required) — the exact 40- or 64-hex revision `git.resolve-provenance` returned. HEAD is never inferred.

There is no target, development flag, scene, option, define, architecture, output path, method or build id input. A supplied `request.build_id` is refused (`INVALID_TOOL_REQUEST`): a build's id is always `build-<request id>`, and a build's request id must be lower-case letters, digits and inner hyphens, at most 64 characters (the foundation's own `req-<16 hex>` is one). For both capabilities, `request.target_platform` is provenance only and, when given, must be `MACOS`; any other platform is refused. Both capabilities declare `caller_output_dir_allowed: false`: a request that names an `output_dir` is refused by the foundation (`INVALID_TOOL_REQUEST`) before any directory is resolved or created, before the lock proof and before Unity starts — the workspace is always the foundation's own.

## The fixed command

```
Unity -batchmode -projectPath <unity-project> -logFile <workspace>/editor.log -upmLogFile <workspace>/upm.log
      -cacheServerEnableDownload false -cacheServerEnableUpload false
      -executeMethod Gpos.LiveBridge.Build.BuildEntry.Run -gposBuildRequest <workspace>/build-request.json
```

The method is a constant of the adapter and the only `executeMethod` GPOS ever names; the request travels in `build-request.json` (strict JSON: schema, operation, request id and, for a build, the expected token). No `-quit`, `-accept-apiupdate`, `-noUpm` or `-nographics`; the entry exits the Editor itself. A build runs only when the installed `Packages/com.gpos.live-bridge` is exactly this release (`BUILD_ENTRY_UNAVAILABLE` otherwise: install or upgrade it with `unity.live-install-bridge` on the closed project).

`BuildEntry` is independent of the live bridge: it has no `[InitializeOnLoad]`, static constructor, callback or menu, runs only when Unity calls it, only in batch mode and never in an import worker. The live bridge lifecycle still returns at once in batch mode. The entry trusts only a workspace that is exactly `<GPOS root>/.game/gpos-runtime/tool-output/unity/<request id>/`, link-free, with none of the build-owned names present.

## Configuration: classic or the active Build Profile

- **PROFILE mode** — a custom Build Profile is active. It must be a regular `.asset` below `Assets/` that describes a macOS Standalone Player (`m_BuildTarget` StandaloneOSX, the macOS platform module, subtarget Player; read through `SerializedObject` because the public API does not expose them), without Player Settings overrides (D-A). The build is `BuildPlayerWithProfileOptions` with exactly that profile; the profile's scenes, scripting defines and settings apply as Unity applies them. There is never a fallback to classic settings.
- **CLASSIC mode** — no custom profile is active. The build is `BuildPlayerOptions` with the enabled scenes of the Editor build settings, target StandaloneOSX, subtarget Player, and `BuildOptions.Development` exactly when the Editor's Development setting is on.

Development belongs to the configuration, never to the request: in PROFILE mode the Editor's development state must equal the profile's own `m_Development` (with a profile active, Unity reads and writes that setting in the profile asset), in CLASSIC mode it is the Editor's setting. After the build, the BuildReport's Development bit must equal it.

A configuration is **not buildable** (each a closed rule, reported by the inspection and refused by a build before `BuildPlayer`):

| Rule | When |
|---|---|
| `TARGET_MODULE_MISSING` | the macOS build module is not installed or not supported |
| `TARGET_NOT_ACTIVE` | the active target is not StandaloneOSX (GPOS never switches it) |
| `EDITOR_NOT_SETTLED`, `SCRIPTS_FAILED` | the Editor is compiling, importing or building; the last compilation failed |
| `PROFILE_NOT_MACOS_PLAYER`, `PROFILE_FIELD_UNREADABLE` | the active profile is not a macOS Player profile below `Assets/`, or a bound field cannot be read |
| `PROFILE_PLAYER_SETTINGS_OVERRIDE` | D-A: the active profile overrides Player Settings |
| `SUBTARGET_NOT_PLAYER`, `BACKEND_NOT_MONO` | a Server subtarget; IL2CPP |
| `DEBUG_STATE_UNSUPPORTED` | D-B: connect profiler, allow debugging, deep profiling, wait for managed debugger, code coverage (or, in either mode, the Editor's wait-for-connection flag) |
| `DEVELOPMENT_AMBIGUOUS` | the Editor's and the active profile's development state disagree |
| `OUTPUT_NOT_PLAYER_APP` | the configuration would produce an Xcode project or install into the build folder |
| `NO_SCENES`, `SCENE_INVALID`, `TOO_MANY_SCENES` | no enabled scene; a scene that is not an existing, known `.unity` asset below `Assets/` with its recorded GUID; more than 1024 |
| `FILE_UNREADABLE` | a bound project file is a link, a directory or beyond 256 MiB |

## The configuration token

`sha256` of the canonical JSON (`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`, reproduced byte for byte by the entry's C#) of `gpos.unity.build-config/1`:

- `unity_version`, `active_target`, `standalone_subtarget`, `scripting_backend`, `application_identifier`, `development`, `mode`;
- `scenes`: each effective build scene's path, GUID and file SHA-256, in build order;
- `files`: the SHA-256 of `Packages/manifest.json`, `Packages/packages-lock.json` (or `ABSENT`), `ProjectSettings/EditorBuildSettings.asset` and `ProjectSettings/ProjectSettings.asset`;
- `debug` and `output`: the debug, profiler, Xcode-project, install-in-build-folder and architecture states of the effective mode (unsupported states are bound too, never normalized away);
- `profile`: the active profile's path, GUID, file SHA-256, target, platform, subtarget, scene override, scripting defines, Player Settings override count and development state, or `null`.

Volatile Editor state (compiling, importing) is not configuration. The entry computes the token at inspection, again immediately before `BuildPlayer` (a mismatch: `BUILD_CONFIGURATION_CHANGED`, nothing built) and immediately after it (a mismatch: nothing published, `BUILD_OUTCOME_UNKNOWN`). GPOS recomputes every token from the configuration in the response.

## The workspace is the build

The foundation's execution workspace `.game/gpos-runtime/tool-output/unity/<request id>/` is the whole, persistent build root; there is no other build directory. It must be empty (a reused request id: `BUILD_WORKSPACE_NOT_FRESH`; an earlier build is never adopted, reused or overwritten).

```
build-request.json   build-started.json   build-response.json   editor.log   upm.log   upm-user.toml   upm-global.toml
staging/Player.app   Unity writes here only
payload/Player.app   the validated payload, after one rename
build-manifest.json  written last
```

The commit sequence: the entry writes `build-started.json` immediately before `BuildPlayer`; Unity exits normally; the response says `BUILT`, `Succeeded`, zero errors; the post-build token, target, profile, development, BuildReport GUID and output path checks pass; `staging/` holds exactly `Player.app`; the `.app` validates; its tree digest is computed within its bounds; `staging` is renamed to `payload` (never over an existing one); `build-manifest.json` is written once (a temporary file, fsync, a hard link that never replaces a file) — last. **A payload without its manifest is not a completed build.**

**Single `.app` payload only.** alpha.21 publishes exactly one `Player.app`. A build that writes anything else beside it in `staging/` — for example a Burst debug-information or other debug-symbol sidecar folder — is `BUILD_PAYLOAD_INVALID` and is not published; the sidecar is never ignored, deleted, packaged or absorbed into the payload. Burst and debug-symbol sidecar output is deferred until it is deliberately researched.

The `.app` is never a foundation artifact. The build's artifacts are the build-manifest (`REPORT`, `application/json`) and the editor-log (`LOG`) under the batch contract.

## Payload validation and the tree digest

The `.app` must be a real directory; `Contents/Info.plist` a regular file of at most 1 MiB that parses, whose `CFBundleIdentifier` is the inspected application identifier and whose `CFBundleExecutable` is one safe file name of an executable regular file in `Contents/MacOS/`; `Contents/Resources/Data/boot.config` must carry exactly one `build-guid=` line equal to the BuildReport's GUID.

`gpos.unity.payload-tree/1`: walked with `lstat` only, never following a link; one line per entry sorted by its UTF-8 relative path — `D\t<rel>`, `F\t<rel>\t<size>\t<sha256>`, `L\t<rel>\t<target>` for a relative link whose target stays inside the payload lexically — and `digest = sha256("gpos.unity.payload-tree/1\n" + each line + "\n")`. Absolute or escaping links, special files, names with a tab, newline, NUL or non-UTF-8 bytes, more than 20 000 entries, more than 8 GiB, more than 32 levels or a relative path over 1024 bytes are refused (`BUILD_PAYLOAD_INVALID`). Nothing outside the payload (no `Library/` or `Temp/`) is read.

## The build manifest

`gpos.unity.build-manifest/1`: `build_id`, `request_id`, capability, adapter, GPOS version, subject; `build_revision` with `build_revision_source: CALLER_SUPPLIED`; the configuration token and the whole canonical configuration; Unity version, target `StandaloneOSX`, effective development, configuration mode, the Build Profile's path, GUID and SHA-256 (or `null`), the scene identities; the Unity build GUID, result `Succeeded`, error and warning counts, size, observed development and duration; the build entry's package, version, method and package digest; the payload's relative path `payload/Player.app`, kind, tree algorithm and digest, entries, bytes, bundle identifier, executable and bundle version; start, finish and duration; fixed limitations. There is no `git_verified` field.

A later Runtime, Deploy or Capture phase resolves `build-<request id>` to this workspace, reads the manifest and recomputes the tree digest before using the payload (`build.revalidate` is the pure check). None of that exists yet.

## Outcomes

| Result | Code | Status |
|---|---|---|
| published | `BUILD_PUBLISHED` | `SUCCESS` |
| refused before `BuildPlayer` | `BUILD_TARGET_NOT_ACTIVE`, `BUILD_CONFIGURATION_UNSUPPORTED`, `BUILD_CONFIGURATION_CHANGED` / `BUILD_TARGET_MODULE_MISSING` | `CONFLICT` / `UNAVAILABLE` |
| scripts do not compile (the entry never ran; the only case where the frozen batch log classifier is read) | `BUILD_COMPILE_FAILED` | `FAILED` |
| the entry gave no answer and provably never started a build | `BUILD_ENTRY_FAILED` | `FAILED` |
| Unity reported a failed build | `BUILD_FAILED` + `BUILD_QUARANTINED` | `FAILED` |
| the payload failed validation | `BUILD_PAYLOAD_INVALID` + `BUILD_QUARANTINED` | `FAILED` |
| a build started without a trustworthy final answer (a crash, a kill, a timeout), a post-build check failed, or publication failed | `BUILD_OUTCOME_UNKNOWN` + `BUILD_QUARANTINED` | `OUTCOME_UNKNOWN` |

When the entry ran, the result comes only from its BuildReport-based response (the summary and at most 16 error, exception or assert messages of 400 characters); `Editor.log` is never parsed and never enters a result. Every text the entry or Unity wrote (step names, messages, problems, the refusal) is cleaned before it leaves GPOS: paths inside the Unity project or the GPOS root become relative, the text passes the GPOS redaction boundary, any other absolute path becomes `<path>`, and the original bound is applied again. The BuildReport's absolute `outputPath` is used only to verify the exact staging path; it never appears in a result, a diagnostic or the manifest, whose paths are all workspace- or project-relative. Quarantined state stays in the workspace, is never published, deleted or adopted, and nothing is ever retried. Measured: a killed build can leave a complete-looking `.app` and an import worker that outlives the Editor for a few seconds (the next request's lock proof then refuses until it is gone).

## The qualified build (Git)

The Unity adapter never runs Git. A **qualified build** is a workflow:

1. `git.resolve-provenance` → the clean revision R;
2. `unity.inspect-build-configuration` → the token T;
3. `git.resolve-provenance` → must again be R (the inspection opened a batch Editor and must not have changed the repository);
4. `unity.build-player` with `request.build_revision = R` and `expected_configuration_token = T`;
5. `git.resolve-provenance` → must again be R.

`unity.build-player` SUCCESS alone is not a Git-qualified build: the binding is a workflow-level pre/post proof, not an atomic repository lock held by the Unity adapter. How the repository becomes clean (committing the bridge package and the project's changes) belongs to the authorized repository workflow outside Build Core; GPOS never stages or commits.

## Evidence boundary

A build and its manifest are operational build facts. They are not runtime, visual, motion, audio, device, performance, test or Human evidence, and a successful build does not imply that any test passed (a project whose tests fail builds successfully; tested permanently).

## Network

The adapter's `TOOL_INHERENT` disclosure applies unchanged: GPOS originates no network operation; Unity's process tree may. macOS builds measured no Package Manager downloads beyond the first open of a fresh project. Android builds are deferred: the Phase 2C-7 research measured that Unity's Android build writes the user's shared Gradle home, downloaded a dependency from the network there, signed with the user's personal debug keystore and left files in the Unity project root.

## Tests

`tests/test_unity_build.py` (fast groups A–H and L with a stand-in Editor; real groups RB1–RB4 and the RQ composition with Unity 6000.5.8f1 and a test-only build testkit that plays the Human), `tests/unity_live_bridge_core/BuildCoreTests.cs`, `tests/mutate_unity_build.py`.
