# Unity live source synchronization and compilation facts (Phase 2C-6C)

Code: [`gpos/tools/unity/sources.py`](../gpos/tools/unity/sources.py) and the bridge's code in [`Editor/SourceSync.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/SourceSync.cs), [`Editor/Compilation.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Compilation.cs) and the Unity-free core [`Editor/Core/SourceRules.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/SourceRules.cs) · adapter id `unity` · bridge `com.gpos.live-bridge` 1.4.0, protocol `gpos.unity.live/5` · status: source synchronization by exact paths, the compilation journal, and observation of a settled Editor. It uses the Human-approved session of the [live plane](unity-live-bridge.md). The batch plane's read-only project-lock proof of the same release is described with the [batch plane](unity-adapter.md#concurrency).

| Capability | Class | What it does |
|---|---|---|
| `unity.live-sync-sources` | `TRANSFORM`, `MUTATING` | tells the attached Editor about exact source paths other programs changed or deleted: a targeted import of each existing `.cs`, `.asmdef` or `.asmref` path, and a bounded recursive import of the folder of each deleted one |
| `unity.live-compilation-status` | `INSPECT`, `READ_ONLY` | phase, compiling, updating, `compilation_failed`, reload, compile and sync generations, the last finished compilations, per-assembly counts and the journal's retention limits; triggers nothing |
| `unity.live-compilation-diagnostics` | `INSPECT`, `READ_ONLY` | the journal's compiler messages, paged, filtered only by compile generation, severity or an exact journaled assembly |
| `unity.live-wait-ready` | `INSPECT`, `READ_ONLY` | observes the Editor until it has settled in Edit Mode — after a named sync, until a compilation that started after it has succeeded or failed — or until the timeout, then reports the facts |

All four are `STATEFUL`, `EDITOR`, lease mode `SESSION_REQUIRED`; none produces evidence. Timeouts: 120 s (at most 300 s) for the sync and the wait, 30 s (at most 120 s) for the two readers. The sync runs only in Edit Mode with nothing pending and is never queued (`EDITOR_BUSY` while Unity compiles, imports, reloads or plays); the two readers answer in any phase.

**Never:** C# text or file content, a method name, `executeMethod`, a menu item, an arbitrary `AssetDatabase` operation, a caller-named folder or extension, a compile or recompile command, a source-write command, `AssetDatabase.Refresh`, `RequestScriptCompilation` or `Editor.log` parsing. Other programs write source files; GPOS names paths.

## Why a sync is needed

Measured with Unity 6000.5.8f1: a file another program writes is not detected by a batch-mode Editor, nor by an unfocused windowed one. A targeted `AssetDatabase.ImportAsset(path)` of the file compiles it. A new file's `.meta` is created by that import. Importing an `.asmdef` does not import the `.cs` files in its folder, so every changed file is named.

## Paths

A source path is `Assets/<folders>/<name>.cs`, `.asmdef` or `.asmref`, spelled exactly as on disk:

- plain folder names (no leading or trailing dot or space, no `~`, not `StreamingAssets`, `Editor Default Resources`, `cvs` or a GPOS transaction scratch folder), at most 16 deep;
- a file name of letters, digits, `_`, `+`, `-` and inner dots;
- at most 64 paths per sync in total, each named once in any spelling;
- an existing source is a regular file of at most 2 MiB below real folders; a link anywhere on the way is refused.

GPOS checks the grammar first and the bridge checks everything again, including the file system.

## Existing paths

Each named existing path is imported exactly (`ImportAsset(path)`), in the order given. A source file moved together with its `.meta` is synchronized by naming its new path: Unity keeps its GUID and every component stays bound. GPOS has no move command.

## Deleted paths

A deleted path must be absent (no file, folder or link in any spelling) and must be a source path. Unity keeps compiling a vanished file (CS2001) until the deletion is synchronized, and only a recursive import of a folder that contained it does that. The folder is derived, never named:

- **direct parent:** the folder that contained the file, when it still exists;
- **one widening (D2):** when that folder is gone too, exactly the folder above it — never a second level and never a nearest-existing-ancestor search; the result records `parent_widened: true`;
- **never `Assets` (D1):** a source directly in `Assets/`, or a widening that would reach `Assets`, is `LIVE_SOURCE_SYNC_REFUSED` and nothing is imported. Source meant for autonomous deletion lives in a folder below `Assets/`.

A deleted path Unity no longer knows is reported `ALREADY_SYNCHRONIZED` and imports nothing. Folders are distinct: a folder inside another one is covered by it, and at most 4 separate folder imports are made per sync (`LIVE_SOURCE_SYNC_LIMIT` otherwise).

Before the first import, each folder is snapshotted: every entry (at most 1000, at most 6 folders deep, no link anywhere), the SHA-256 of every `.meta` file, and the assets Unity knows below it. A bound exceeded is `LIVE_SOURCE_SYNC_LIMIT`; a link is `LIVE_SOURCE_SYNC_REFUSED`; nothing is imported. After the imports each folder is snapshotted again and reported:

| Field | Meaning |
|---|---|
| `imported_new` | assets Unity knows now and did not before (for example an unrelated new file) |
| `removed` | assets Unity knew before and no longer does (the deleted source, and any other stale deletion) |
| `meta_created`, `meta_removed`, `meta_changed` | `.meta` files Unity created, removed or rewrote in the folder |

Each list holds at most 100 paths plus a count. Which unchanged files Unity re-read is not exposed. `LIVE_SOURCE_SYNC_SIDE_EFFECTS` summarizes every `.meta` created (including those of named new sources) and every other asset imported or removed.

## Mutation semantics

Every path, every folder and every snapshot bound is checked before the first import: a sync is either refused with nothing imported (`mutation_performed` false) or it imports. From the first import on, `mutation_performed` is true, also when the request then fails (`LIVE_SOURCE_SYNC_INCOMPLETE`: an import raised, or Unity still knows a deleted source) and when the answer never arrives (`LIVE_OUTCOME_UNKNOWN`). Nothing is retried or undone. A sync in which every named deleted source was already synchronized imports nothing and is no mutation.

## The compile-generation model

The bridge keeps Editor-session counters from `CompilationPipeline` callbacks:

| Counter | Meaning |
|---|---|
| compile generation | the number of `compilationStarted` callbacks |
| completed compiles | the number of `compilationFinished` callbacks; each finish records the compile generation at that moment, the reload generation, and its own error and warning counts |
| sync generation | +1 for every sync that imports; its record holds `compile_started_before_sync`, read **before its first import**, and, as extra facts, `compile_started_after_sync` and `compiling_after_sync` |
| reload generation | +1 for every Domain Reload of the bridge |

The causal baseline of sync S is `compile_started_before_sync(S)`. A compilation Unity starts during an import is therefore newer than the sync. A **settled outcome after S** is the last finished compilation whose compile generation is greater than that baseline, with the Editor back in Edit Mode, idle and stable:

- `FAILED`: it reported errors (Unity keeps the old domain; no reload follows), or it reported none and Unity's settled `compilation_failed` flag is set;
- `SUCCEEDED`: it reported no errors and a Domain Reload after it is proven.

Unity coalesces and abandons compilations (two starts, one result), so one edit is never claimed to be one compilation. A failed compilation is Edit Mode with `compilation_failed` true, never a separate phase.

## The journal

`assemblyCompilationFinished` gives each compiled assembly's messages: severity (`ERROR` or `WARNING`), file, line, column and text. The journal keeps, per assembly, its latest result:

- the file made project-relative, or none (`outside_project`) when it is not inside the project;
- the text with the project's absolute path made relative and clipped to 512 characters; GPOS then passes it through its redaction boundary and replaces any remaining absolute path with `<path>`;
- at most 32 messages per assembly (errors first), 64 assemblies and 256 messages in total; dropped messages are counted;
- one deterministic order: assembly, then severity, file, line, column, text.

An assembly Unity takes from its cache reports nothing and keeps its earlier entry. Error entries are the exception: a Domain Reload after a compilation that reported no errors proves every assembly compiled, so any error entry left from an earlier failed compilation is dropped and counted as `superseded` (measured: restoring a source to an earlier successfully compiled version compiles from the cache, reports no assembly and reloads).

The journal lives in the Editor's session state: it survives a Domain Reload and is lost when the Editor quits (`unity.live-compilation-status` reports when it started). It is never read from `Editor.log` and is never evidence.

## compilation-status and compilation-diagnostics

`unity.live-compilation-status` reads the Editor's flags and the journal. GPOS adds `settled`, `last_compile_outcome` (`SUCCEEDED`, `FAILED` or none), `last_completed_compile_generation` and `outcome_newer_than_latest_sync`. It triggers nothing.

`unity.live-compilation-diagnostics` pages the journal's messages, 50 per page. The only filters are `generation` (a retained compile generation), `severity` and `assembly` (an exact name the journal holds); a filter the journal does not know is `LIVE_DIAGNOSTICS_FILTER_UNKNOWN`. There is no text search, no regular expression and no file access.

## wait-ready

`unity.live-wait-ready` observes only. It reads the bridge's heartbeat (every 0.25 s; the heartbeat carries the counters, the last finished compilation and the latest sync) and sends nothing but compilation-status requests: one to resolve a named sync generation, and one to confirm a ready observation. It never imports, compiles, refreshes, reloads, restarts, kills or replays anything.

Ready means: Edit Mode, not compiling, not updating, no pending transition, the observation unchanged for 2 s, and — with `sync_generation` — the settled outcome after that sync. `ready` with `compilation_failed` true is a settled result: the Editor keeps running the scripts of its last successful compilation, and Unity refuses Play Mode and test runs until the errors are fixed (`LIVE_COMPILATION_FAILED`). A generation this Editor session did not record, or no longer retains, is `LIVE_SYNC_GENERATION_UNKNOWN`. The observation stops 10 s before the capability timeout (at most 300 s) to read the final facts; when it ends unready the result is `LIVE_NOT_READY` with those facts, and `NONE_OBSERVED` when no compilation started after the sync.

## Domain Reload, identity and failures

Measured: the live session survives a Domain Reload without a new attach. Requests sent while Unity compiles are refused `EDITOR_BUSY` and never run; a request sent while the domain reloads is claimed after the reload and runs once. Tokens and `GlobalObjectId`s stay stable across reloads. A namespace change or a class rename inside the same file keeps a Scene component bound. A reload that adds a type changes the component catalog, so an older catalog digest is `LIVE_CATALOG_CHANGED` and the caller inspects again.

A failed compilation blocks nothing globally: reads keep working, authoring on existing types stays governed by its own checks, and a missing or changed type fails through the catalog. Unity refuses Play Mode with any compile error, and GPOS does not override that. A deleted script leaves a missing script on its components; GPOS never repairs it (`unity.live-create-prefab` refuses such a source), and restoring the file with its `.meta` and syncing it binds the components again.

## Security boundary

A structural test proves the bridge's source code has no `AssetDatabase.Refresh`, `RequestScriptCompilation`, `SaveAssets`, reflection, `executeMethod`, menu, process, network, file-write, delete or move call. `ImportAsset` appears exactly twice: the exact import of a named path, and the one recursive import of a derived folder. The compilation code only subscribes to the three callbacks. GPOS's module writes no file and its wait sends only compilation-status. Source synchronization results, compilation status and diagnostics are operational facts, never evidence.

## Diagnostics

| Code | Class | Meaning |
|---|---|---|
| `LIVE_SOURCE_PATH_INVALID` | `INVALID_REQUEST` | not an exact source path below `Assets/`, spelled as on disk, named once |
| `LIVE_SOURCE_SYNC_LIMIT` | `INVALID_REQUEST` | too many paths, a source over 2 MiB, or a folder over its entry, depth or count bound; nothing imported |
| `LIVE_SYNC_GENERATION_UNKNOWN` | `INVALID_REQUEST` | a sync generation this Editor session did not record or no longer retains |
| `LIVE_DIAGNOSTICS_FILTER_UNKNOWN` | `INVALID_REQUEST` | a compile generation or assembly the journal does not hold |
| `LIVE_SOURCE_SYNC_REFUSED` | `CONFLICT` | a named source is missing, a deleted one still exists, or its folder cannot be synchronized (D1, D2, a link); nothing imported |
| `LIVE_NOT_READY` | `CONFLICT` | the Editor did not settle before the wait ended; the facts are returned; nothing was triggered |
| `LIVE_SOURCE_SYNC_INCOMPLETE` | `FAILED` | imports began and did not complete as asked; not retried or undone |
| `LIVE_SOURCES_SYNCED`, `LIVE_SOURCE_SYNC_SIDE_EFFECTS`, `LIVE_COMPILATION_FAILED` | `INFO` | what happened |

`EDITOR_BUSY`, `LIVE_OUTCOME_UNKNOWN`, `LIVE_REQUEST_WITHDRAWN` and the session codes keep their meanings.

## Tests

```bash
python3 tests/test_unity_sources.py
python3 tests/test_unity_live_bridge_core.py
python3 tests/mutate_unity_sources.py [--real]
```

The fast groups drive the GPOS side against the fake bridge, which models Unity's compilation counters (A–C, W), pin the frozen 1.3.0 release and upgrade it (D), scan the sources (E) and cover the batch lock proof (L). The C# core tests cover the path grammar, D1 and D2, the folder bound, the causal baseline (a host that starts a compilation during an import), the journal and the protocol. The real groups use disposable synthetic projects in lab-owned batch Editors, with the test itself as the external tooling: R9 the source/compile loop, R10 reloads, identity and deletion synchronization, R11 the permanent end-to-end qualification (source, compile error, diagnostics, fix, reload, catalogs, Scene, asset and prefab authoring, Play Mode, batch EditMode and PlayMode tests, a failing test, its fix through the live loop, a passing rerun and a reopened project), and R12 the batch stale-lock contract.
