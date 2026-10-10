# Windows Build Core — alpha.27 review candidate

Phase 2C-9.4 implements one Windows build contract: Unity 6000.6.4f1, already active StandaloneWindows64, x64 Mono2x, Standalone Player, CLASSIC, nondevelopment. Active profiles, other targets/backends/subtargets, debug/profiler flags, solution/PDB/install outputs and other Editor versions fail closed. Production never switches targets, creates or activates profiles, changes settings or accepts arbitrary arguments.

The Windows production build and build-inspection capabilities remain unavailable. D-G1 is open: the Windows Git adapter is disabled and repository filter-driver execution is unresolved. `build_revision` retains its exact 40/64 lowercase hexadecimal contract and is explicitly caller-supplied (manifest value CALLER_SUPPLIED). Neither GitHub nor test fixtures establish clean production provenance. The qualification adapter lives only in tests and is not registered by the production registry. The existing twelve Windows Unity capabilities remain the production allowlist.

## Fixed execution

Windows dispatch reuses the fixed `Gpos.LiveBridge.Build.BuildEntry.Run` method, request/started records, canonical configuration token, pre/post checks, EDITOR_PROJECT single-writer lease, Windows project-lock proof, private UPM files/cache and the reviewed process Job boundary. Every invocation needs a fresh workspace. An ambiguous started build is OUTCOME_UNKNOWN, preserved in quarantine and never retried or adopted.

The Windows-only configuration/response schemas are `/2`. The fixed read-only classic-profile getter reads architecture, solution and PDB fields through Unity's existing SerializedObject view. These private fields are qualified only for the pinned Editor; missing/changed fields refuse. No setter, profile creation or activation occurs in production. Test fixture preparation changes settings only inside the disposable qualification lab and is explicitly test-only.

## Payload and publication

The complete BuildReport file paths/sizes must match the actual Player tree; its result, output path, platform, development state, total size and GUID must agree. The payload verifies Player.exe, UnityPlayer.dll, Player_Data, managed assemblies and the Mono runtime/configuration. Native PE images require x64 PE32+; managed CLI headers are checked separately and may be PE32. DLLs require the DLL characteristic. boot.config must contain exactly the BuildReport GUID.

The root runtime categories retain the observed crash handler, D3D12 and DirectStorage files. The inventory is not fixed at 151 files. Unqualified root GPU plug-ins and debug/project sidecars refuse rather than being discarded.

Bounds are 4,096 entries, 8 GiB, depth 32, 1,024 UTF-8 bytes per relative path, 8,192 retained directory-pin handles, a 1 MiB PE-header read and a 1 MiB strict JSON record. The stricter entry/handle limits bound resource use and can refuse a larger legitimate project. No automatic widening occurs.

Public lexical/scope checks precede trusted handles. Only canonical local drive-letter NTFS paths are accepted. Reparse points, aliases, unsafe names, case aliases and multiply linked files refuse. Hashes, sizes and volume/file identities come from checked handles. Files are opened denying writers/deletion; directory ancestry is pinned. The whole inventory includes directory and root identities, so identical-byte replacement is also drift.

The only new Win32 bindings are FindFirstStreamW, FindNextStreamW and FindClose. Files accept exactly one correctly sized unnamed stream; any named stream on a file, directory, Player root or staging/payload container refuses. Unexpected errors, unsupported results, ambiguity and incomplete enumeration fail closed. Explicit ADS paths never reach the raw reader. Isolated ADS qualification precedes implementation.

Publication order is: validate full report/output/tree and process containment/capture; rename staging to payload without overwrite; create/flush a private manifest temporary; rename it without overwrite **last**; revalidate the committed manifest and tree before reporting BUILD_PUBLISHED. Rename retries cover only sharing/access-denied errors for at most 100 ms and never repeat the build. Destination collisions, partial publication and revalidation failures remain incomplete/quarantined. There is no power-loss durability claim.

## Manifest and preserved boundaries

Windows `gpos.unity.build-manifest/2` binds GPOS/Bridge/adapter versions, fixed entry/package digest, build/request/subject, caller-supplied revision attribution and explicit limitations, Unity version, configuration/token/scenes, BuildReport result/GUID and duration, relative executable/data paths and complete inventory/digest/count/bytes/NTFS identities. Strict revalidation rejects schema/field/version/attribution changes and added, missing, altered or replaced payload content. A consumer must revalidate before use; this is a local NTFS workspace contract, not a portable archive or an authenticated repository snapshot.

Bridge 1.7.0 retains `gpos.unity.live/5`. The exact 1.6.0 historical manifest is pinned at `883d1e31751ad158f0c3d82f90a1b18a173a54ad46799a39834929315e4a0400`; prior histories are retained. POSIX parity checks preserve the macOS compiled Build Core and existing manifest/1 semantics. The macOS view and Windows view are compiled and core-tested with the installed Mono toolchain; macOS Editor/runtime execution remains NOT_RUN.

## Qualification commands

Run Windows suites with `python -B -X utf8`. The fresh authorized lab is `D:\gpos-unity-lab-alpha27-qualification`; earlier alpha.25/26 labs and unrelated projects are not used. The measurement lab is not reused. At most one Editor runs; no Player is launched.

- `tests/test_unity_windows_build.py`: bounded NTFS/ADS/report/publication/manifest fault tests and Build Core source checks.
- Set `GPOS_WINDOWS_BUILD_REAL=1` for the real Build Core fixture: inspect, positive build, target/backend/profile refusals, compile/BuildReport failure, pre/post drift, timeout cleanup and real consumer drift.
- `tests/mutate_unity_windows_build.py`: baseline-gated Windows payload/publication/configuration mutations; `--real` adds the sequential real Editor token-precheck mutation.
- Existing affected framework, production validator, agent adapter, Tool Foundation, Windows Foundation, Windows Batch and Windows Live suites remain required. Point both Windows Unity lab environment variables to the qualification root.

Mutation timeouts are INCONCLUSIVE, never CAUGHT. Sharing/scanner-handle interference exercises the failure contract; it does not qualify any named antivirus product. Crash-point injections do not establish power-loss recovery. Live approval tests use explicitly synthetic testkit owners and produce no Human approval evidence. Final run counts, actual manifest digest, logs, remaining limitations and implementation commit belong in the Human Review report.
