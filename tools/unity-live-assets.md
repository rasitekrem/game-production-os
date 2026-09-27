# Unity live asset references and asset authoring (Phase 2C-6B2A)

Code: [`gpos/tools/unity/assets.py`](../gpos/tools/unity/assets.py) and the bridge's asset code in [`Editor/AssetAuthoring.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/AssetAuthoring.cs), [`AssetResolver.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/AssetResolver.cs), [`AssetCatalogs.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/AssetCatalogs.cs) and the Unity-free core [`Editor/Core/AssetRules.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/AssetRules.cs) · adapter id `unity` · bridge `com.gpos.live-bridge` 1.2.0, protocol `gpos.unity.live/3`. Built on the [live Editor plane](unity-live-bridge.md) and [Scene authoring](unity-live-authoring.md): every capability here needs the Human-approved session, Edit Mode and nothing pending, exactly as Scene authoring does.

Seven fixed capabilities read assets, create Materials and ScriptableObjects, and edit them. Each maps to one bridge command, and each command reads or changes only what its name says.

| Capability | Class | What it does |
|---|---|---|
| `unity.live-asset-types` | `INSPECT`, `READ_ONLY` | one closed catalog: `KINDS`, `SCRIPTABLE_OBJECTS` (pages of 200) or `SHADERS` (pages of 10, with every declared property), each with its digest |
| `unity.live-asset-find` | `INSPECT`, `READ_ONLY` | typed lookup of one kind in `ASSETS`, `PACKAGE` or `BUILTIN`, optionally by a name substring (pages of 200) |
| `unity.live-asset-inspect` | `INSPECT`, `READ_ONLY` | one asset by id: reference facts, values, whether GPOS may write it, and its composite token; never imports |
| `unity.live-create-material` | `TRANSFORM`, `MUTATING` | one Material of a catalogued shader at an exact new `.mat` path |
| `unity.live-set-material-property` | `TRANSFORM`, `MUTATING` | one property the Material's catalogued shader declares, then save that Material |
| `unity.live-create-scriptable-object` | `TRANSFORM`, `MUTATING` | one ScriptableObject of a creatable catalogued type at an exact new `.asset` path |
| `unity.live-set-asset-property` | `TRANSFORM`, `MUTATING` | one allowlisted serialized property of a ScriptableObject, then save that asset |

All seven are `STATEFUL`, `EDITOR`, lease mode `SESSION_REQUIRED`, need no tool probe, declare no evidence and support no dry run. Timeouts: 30 s (at most 120 s) for reads, 60 s (at most 300 s) for writes. Inputs are strings; identifiers have their own grammars, each matched against the whole string (a trailing LF or CRLF is refused), and values are strict JSON text. GPOS validates every input before the Editor sees it, and the bridge validates it again. Scene properties may also *reference* assets and one Renderer material slot can be set; those are [Scene-authoring commands](unity-live-authoring.md#renderer-material-slots) and change only the Scene.

**Never (deferred):** prefab asset authoring, Prefab Mode, applying, reverting or unpacking prefabs; a user-facing delete, move or rename of assets; import settings; Scene creation or Save As; array, list or managed-reference writes; curves and gradients; ShaderGUI, keyword, blend-preset, emission or render-state emulation; package or built-in asset mutation; an arbitrary AssetDatabase call, `SaveAssets`, a global `Refresh`, folder creation; reflection, C#, input and evidence.

## Identity and the reference boundary

An asset is named only by its `GlobalObjectId` string with prefab id 0:

| Identifier type | What it names |
|---|---|
| 1 | an imported asset (texture, audio, model, prefab) or one of its sub-assets |
| 3 | a source asset (`.mat`, `.asset`) |
| 4 | a built-in resource, only with the GUID `0000000000000000e000000000000000` (default resources) or `0000000000000000f000000000000000` (built-in extra) |

Anything else is `LIVE_OBJECT_REFUSED`, and there is no InstanceID, EntityId, path or file name as identity (`Library/PackageCache` paths are never accepted anywhere). The bridge resolves the id with Unity, requires a persistent object whose canonical id is exactly the string given (an unknown id is `LIVE_OBJECT_NOT_FOUND`), and accepts only one of the reviewed kinds:

| Kind | Accepted as |
|---|---|
| `MATERIAL` | a main `.mat` asset, a model's embedded material, or a built-in material of the table |
| `TEXTURE` | a Texture2D, Cubemap, Texture3D, Texture2DArray or CubemapArray (main or sub-asset) |
| `SPRITE` | a Sprite (usually a sub-asset of an imported texture) |
| `AUDIO` | an AudioClip or another `UnityEngine.Audio.AudioResource` (main asset) |
| `MESH` | a Mesh (usually a sub-asset of a model), or a built-in mesh of the table |
| `PREFAB` | the root GameObject of a prefab (or prefab variant) main asset |
| `MODEL` | the root GameObject of an imported model |
| `PREFAB_COMPONENT` | a Component on the root GameObject of a prefab or model main asset |
| `SCRIPTABLE_OBJECT` | a main ScriptableObject asset whose type is in the ScriptableObject catalog |

Refused (`LIVE_ASSET_REFUSED`): Scene assets, MonoScripts, folders and unknown files, internal prefab or model objects (children and their components), sub-assets of ScriptableObjects, shaders and every other type, Editor-assembly types, and anything in an `Editor` or `Editor Default Resources` folder.

**Sources.** `ASSETS` is below `Assets/`. `PACKAGE` is below the canonical `Packages/<name>/` path of a registered package (the AssetDatabase path; never the package cache). `BUILTIN` is only the fixed, reviewed table: the default Cube, Sphere, Capsule, Cylinder, Plane and Quad meshes, `Default-Material` and `Sprites-Default`, and the UI sprites `UISprite`, `Background`, `Knob` and `Checkmark`. A built-in id outside that table is refused even when Unity resolves it; built-ins have no project path (`path` is null), and `HideAndDontSave` on them is not a refusal. **Package and built-in assets are references only**: they are never written.

**Authorable.** GPOS writes only a main Material (`.mat`) or catalogued ScriptableObject (`.asset`) below `Assets/`, outside special folders, that is alone in its file. `unity.live-asset-inspect` reports `authorable` and, only for an authorable asset, its token, dirty state, file and `.meta` hashes and whether version control has it open for edit.

## Lookup

`unity.live-asset-find` takes a findable kind (all but `PREFAB_COMPONENT`, which `unity.live-asset-inspect` of a prefab or model root lists), a source, an optional `query` and a page. For `ASSETS` and `PACKAGE` the bridge builds a fixed type filter from the kind table itself (`t:Material`, `t:Texture`, …) and searches `Assets` or each registered package root; the caller's `query` is never an AssetDatabase filter, only a case-insensitive substring of the object name applied afterwards. More than 5,000 files for one kind and source is `LIVE_ASSET_LIMIT` (nothing is listed partially); a file with more than 256 objects is listed partially and named in `files_listed_partially`. For `BUILTIN` only the fixed table is searched. Results are sorted by path and id, 200 per page, and every entry carries id, kind, type, name, path, source, main, sub-asset and authorable.

## Catalogs and digests

`unity.live-asset-types` returns one catalog, rebuilt from the Editor's own information on every request:

- **KINDS**: the nine kinds, which are findable, what each names and the authorable extension, with a digest of the table.
- **SCRIPTABLE_OBJECTS**: every concrete, non-generic, public, non-obsolete ScriptableObject type of a Player (non-Editor, non-test) assembly of the project that is the class of exactly one runtime MonoScript and not a StateMachineBehaviour. Each is referenced and edited; only those with `[CreateAssetMenu]` are **creatable**. Entries report type id, name, namespace, assembly, full name, script mapping, creatable, menu name and file name. The digest covers every one of those fields in type-id order, so a recompile that adds or removes `[CreateAssetMenu]` changes it.
- **SHADERS**: every supported shader without errors that is not `Hidden/`, has an asset identity (type 1, 3 or 4) and declares at most 128 properties (more than 1,024 such shaders is `LIVE_ASSET_LIMIT`). Each lists every declared property: name, type, kind, the three write-relevant flags as a refusal (HIDDEN for `HideInInspector`, PER_RENDERER_DATA, NOT_EDITABLE for non-modifiable texture data), range limits and texture dimension. The digest covers all of that.

`unity.live-create-material` takes `expected_shader_catalog_digest` and `unity.live-create-scriptable-object` takes `expected_so_catalog_digest`: a changed catalog is `LIVE_CATALOG_CHANGED`. A shader outside the catalog is `LIVE_SHADER_NOT_IN_CATALOG`; a type outside the catalog, or not creatable, is `LIVE_TYPE_NOT_IN_CATALOG`.

## The composite token

Every edit carries `expected_asset_token` from an earlier `unity.live-asset-inspect`. The token (`gpos.asset/1`, SHA-256, 32 hex digits) covers:

- the asset id, its canonical path and its runtime type key;
- an opaque hash of the whole in-memory serialized state, hidden values included, with references hashed by `GlobalObjectId` (the Scene-authoring component-token scheme);
- the dirty flag;
- the SHA-256 of the source file (at most 4 MiB) and of its `.meta` (at most 256 KiB).

Beyond those bounds, or beyond 2,048 top-level properties or 200,000 values, the asset is `LIVE_ASSET_LIMIT`: nothing is hashed past the bound and it is never written. The token detects an Inspector edit (memory and dirty flag), an external edit (file), a label or importer change (`.meta`) and a save. Hashing never authorizes writing.

## Editing an existing asset

`unity.live-set-material-property` and `unity.live-set-asset-property` follow one sequence (`Persist`):

1. Resolve and verify identity; the asset must be authorable.
2. Validate the property and value (below) — before anything changes.
3. Refuse a dirty asset: `LIVE_ASSET_DIRTY`. GPOS never saves the Human's (or another tool's) unsaved changes.
4. Refuse a file version control does not have open for edit (`AssetDatabase.IsOpenForEdit`): `LIVE_ASSET_NOT_EDITABLE`. Nothing is ever checked out.
5. **Targeted import** of that one asset (`AssetDatabase.ImportAsset(path)`). Inspection never imports; this is the only import, and project AssetPostprocessors may run. From here on the request reports `mutation_performed` true.
6. Refuse if the import left the asset dirty.
7. Recompute the composite token and compare: a difference is `LIVE_ASSET_CONFLICT`. The request is never adapted to the new state.
8. Capture the source and `.meta` hashes the import left.
9. Open one Undo group (`GPOS: set <property> of <asset>`), make the typed change, and read it back; on a mismatch revert the group and verify the in-memory state is the one before the edit.
10. **Immediately before saving**, re-hash the source and `.meta`. Any change reverts the group, saves nothing and is `LIVE_ASSET_CONFLICT`: an external change on disk is never overwritten.
11. **Commit point**: `AssetDatabase.SaveAssetIfDirty(asset)` — only this asset. Never `SaveAssets`, never a global `Refresh`.
12. Post-commit verification: the asset is not dirty and holds the value; the new token, file and `.meta` hashes are reported.

From the commit point on, anything uncertain (Unity raises during the save, or the post-commit check fails) is `PERSISTENCE_UNKNOWN`, reported as `LIVE_OUTCOME_UNKNOWN` (status `OUTCOME_UNKNOWN`) with `mutation_performed` true: no rollback claim, no retry — inspect again.

**Material properties.** Only a property the Material's actual shader declares (checked through the shader catalog and `Shader.FindPropertyIndex`; Unity itself would accept any name) and that is not HIDDEN, PER_RENDERER_DATA or NOT_EDITABLE. The request's kind must be the declared one:

| Kind | Shader property | Value | Checked | Read back |
|---|---|---|---|---|
| `color` | Color | `[r, g, b, a]` | finite binary32 (HDR allowed) | binary32 exact |
| `vector` | Vector | `[x, y, z, w]` | finite binary32 | binary32 exact |
| `float` | Float | number | finite binary32 | binary32 exact |
| `range` | Range | number | within the declared limits | binary32 exact |
| `int` | Int (`Integer`) | integer | 32-bit | exact |
| `texture` | Texture | `null` or a Texture asset id | the id resolves **directly** to a `TEXTURE` whose dimension fits the declared one (Tex2D, Cube, Tex3D, Tex2DArray, CubeArray; `Any` takes any) | the same object |

A Sprite id is not a Texture: it is refused, never converted to `sprite.texture`, so the id requested, the object assigned and the object read back are one. `unity.live-asset-inspect` of a Sprite names its Texture's own id for callers that want it. There is no ShaderGUI, keyword, blend-preset, emission or render-state emulation: writing a raw value (for example `_Mode` or `_SrcBlend`, which the Standard shader hides anyway) does not do what the Inspector would do around it. The Material API writes are recorded with `Undo.RecordObject`.

**ScriptableObject properties.** The Scene-authoring [default-deny rules](unity-live-authoring.md#properties) apply unchanged to the asset (listed properties only, no arrays, lists, managed references, curves, gradients or hidden fields, `m_Script` and `m_Name` denied), written through `SerializedObject` and read back. An object reference names an asset only — an asset never references a Scene object — and must satisfy the field's declared type before Unity assigns it. `OnValidate` may run and keep another value: the edit is then reverted, nothing is saved, and the result is `LIVE_VALUE_INVALID` with `mutation_performed` true (the import happened).

## Creating an asset

Creation writes one new asset at exactly the requested path and never overwrites, renames, creates folders or writes under `Packages/`. It is **not undoable** (Unity records no Undo for it).

**Write paths.** `Assets/<folders>/<stem><ext>`: at most 16 folders; each folder a plain name (letters, digits, space, `_ ( ) . , + -`, not starting with a dot or space, not ending with a dot or space); not an `Editor`, `Editor Default Resources` or `StreamingAssets` folder, not a GPOS scratch folder; a file stem of 1 to 64 letters, digits, spaces, `_ ( ) -` not starting or ending with a space; the extension exactly `.mat` for a Material and `.asset` for a ScriptableObject. The folder must already exist in the AssetDatabase and on disk, every path component must be free of symbolic links (`LIVE_ASSET_PATH_INVALID`), and it must be writable. Anything at the path — a file, a folder, an orphan `.meta`, the same name in another letter case, or an asset the AssetDatabase still knows there — is `LIVE_ASSET_EXISTS`.

**Version control.** A creation is allowed only when no version-control provider is active (`Provider.isActive` false); otherwise `LIVE_ASSET_NOT_EDITABLE`, because adding and checking out files are never done by GPOS. Real Perforce and Plastic providers are untested.

**The transaction.** Research proved that `AssetDatabase.CreateAsset` silently overwrites an existing file and that `MoveAsset` refuses an existing or case-variant destination but silently replaces an orphan `.meta`. Creation therefore never calls `CreateAsset` where anything of anyone else could be:

1. Recover every earlier interrupted creation of the project (below), then check the destination again.
2. Write the transaction record (below), phase `PREPARED`.
3. Create the GPOS-owned scratch folder `Assets/GposAssetTxn-<txn id>` **exclusively** (`mkdir`, which fails if anything has that name), import it, check it is empty, and record its `.meta` hash (`SCRATCH_READY`).
4. Check the scratch folder is still empty, then `CreateAsset` the new object inside it under the final file name (`OnEnable` of a ScriptableObject runs here).
5. Prove the temporary asset: the folder holds exactly it and its `.meta`; its GUID, `GlobalObjectId` (type 3, main object), runtime type and name are the expected ones; it is not dirty; record its file and `.meta` hashes (`TEMP_PROVEN`).
6. Immediately before the move, re-check the final destination (file, `.meta`, letter case, AssetDatabase, folder identity and link freedom); `ValidateMoveAsset` must approve.
7. `MoveAsset` into place. Because the temporary asset already has the final name, the move changes no byte: the verified post-move state is the same GUID, id, type, name, file and `.meta` hashes at the exact requested path.
8. Record the final hashes (`FINAL_PROVEN`), remove the scratch folder only when it is exactly the empty folder GPOS created (its `.meta` hash proven), and clear the record.

A failure inside the request before the move removes only this transaction's own exact, proven temporary asset and exact scratch folder (`compensated`: `TEMP_REMOVED` or `NOTHING_CREATED`), then reports the cause with `mutation_performed` true — for example `LIVE_ASSET_EXISTS` when a competing final file appeared before the move. Nothing at the final path is ever removed in-process. Anything that cannot be proven keeps the record and is `LIVE_ROLLBACK_INCOMPLETE`. Unknown content that appears in the scratch folder is never overwritten or removed: before `CreateAsset` it stops the creation; before the cleanup, after the asset was created, the result is `LIVE_ASSET_CREATE_INCOMPLETE` naming the created asset, and the scratch folder stays until the unknown content is removed.

**The record.** `.game/gpos-runtime/unity/asset-create-txn/<project key>/<txn id>.json`: at most 4 KiB, strict JSON with exactly these keys (duplicates refused), written atomically, every directory on the way a real directory (a link is `LIVE_ASSET_CREATE_INCOMPLETE`):

`schema`, `txn_id`, `project_key`, `session_id`, `request_id`, `owner`, `kind`, `final_path`, `phase`, `scratch_meta_sha256`, `guid`, `global_id`, `type`, `temp_sha256`, `temp_meta_sha256`, `final_sha256`, `final_meta_sha256`, `started_utc`.

It is non-authoritative and never trusted for a path: the file name must be its transaction id, the project key this project's, the final path is validated again, and each identity or hash field must be present exactly from the phase that proved it. The scratch folder and temporary path are derived from the transaction id and the validated final path only. A record that is not exactly this shape is never acted on.

**Recovery.** Before every creation the bridge reads each open record and classifies what is actually on disk: the scratch folder (absent, empty, holding exactly the temporary asset, or unknown), its `.meta`, the temporary asset and the final path (absent, exact, this transaction's GUID but changed, or someone else's). It then acts only on proof:

| On disk | Recovery | Outcome |
|---|---|---|
| nothing was created (only the record, or GPOS's own empty scratch folder) | remove the exact empty scratch folder, clear the record | `NOTHING_CREATED` |
| the exact temporary asset, and the final path absent | remove the temporary asset (GPOS-owned scratch, not a retry) and the scratch folder, clear | `TEMP_REMOVED` |
| the exact final asset, no temporary asset | keep the final asset, remove the empty scratch folder, clear | `FINAL_KEPT` |
| neither the temporary nor the final asset of this transaction (the final path absent or someone else's) | remove the empty scratch folder, clear | `ABANDONED` |
| anything else: a changed temporary or final asset, an unproven temporary asset, unknown scratch content, an untrusted record | nothing is deleted, moved, overwritten or repaired | `LIVE_ASSET_CREATE_INCOMPLETE` (CONFLICT), with bounded state facts |

No creation is replayed: a request whose result was lost after the move is closed as `FINAL_KEPT`, and the caller, who saw `OUTCOME_UNKNOWN`, inspects again. Recovered transactions are reported as `LIVE_ASSET_CREATE_RECOVERED`. A Human resolves an incomplete transaction outside GPOS — by removing the unknown content, or the scratch folder and its record — after which the next creation proceeds.

## Undo, persistence and dirty state

An asset edit is recorded in Unity's Undo history as one `GPOS: …` group and saved at once. Cmd-Z afterwards restores the value **in memory** and Unity marks the asset dirty, while the file keeps GPOS's saved value until someone saves again. GPOS never saves that dirty state: the next GPOS edit of the asset is `LIVE_ASSET_DIRTY` until the Human saves or reverts it. A creation is not undoable. A reverted edit (a conflict or a read-back mismatch) may leave the asset marked dirty; that is disclosed in `dirty`, and GPOS never marks an asset clean.

`mutation_performed`:

| Case | `mutation_performed` |
|---|---|
| refused before the targeted import, the scratch folder or any Undo change (input, identity, dirty, version control, catalog, collision, busy, limits) | false |
| the targeted import happened, then a refusal (token conflict, dirty after import, disk conflict before the save) | true — `import_performed` true, `value_persisted` false |
| an edit reverted after a read-back mismatch | true |
| any creation step from the scratch folder on, including a successful compensation, and a recovery that removed something | true |
| persistence uncertain from the commit point on | true (`OUTCOME_UNKNOWN`) |
| a read-only capability | always false |

## Diagnostics

| Code | Class | Meaning |
|---|---|---|
| `LIVE_ASSET_REFUSED` | INVALID_REQUEST | not a reviewed kind or source, or a reference-only asset a write was asked for |
| `LIVE_ASSET_PATH_INVALID` | INVALID_REQUEST | not a plain path below `Assets/` into an existing, link-free folder with the kind's extension |
| `LIVE_ASSET_LIMIT` | INVALID_REQUEST | a file, token, lookup or catalog bound |
| `LIVE_SHADER_NOT_IN_CATALOG` | INVALID_REQUEST | the shader is not catalogued |
| `LIVE_ASSET_CONFLICT` | CONFLICT | the token changed, or the file or `.meta` changed before the save |
| `LIVE_ASSET_DIRTY` | CONFLICT | unsaved Editor changes; never saved by GPOS |
| `LIVE_ASSET_EXISTS` | CONFLICT | something is at the path; nothing is overwritten or renamed |
| `LIVE_ASSET_NOT_EDITABLE` | CONFLICT | not open for edit, a provider is active for a creation, or the folder is not writable |
| `LIVE_ASSET_CREATE_INCOMPLETE` | CONFLICT | a creation cannot be finished or undone from proof; nothing unknown was touched |
| `LIVE_ASSET_CREATED` | INFO | a new asset exists at exactly the requested path |
| `LIVE_ASSET_SAVED` | INFO | the edit was saved to that one asset's file |
| `LIVE_ASSET_CREATE_RECOVERED` | INFO | an earlier interrupted creation was closed from proof |

Reused: `LIVE_OBJECT_REFUSED`, `LIVE_OBJECT_NOT_FOUND`, `LIVE_CATALOG_CHANGED`, `LIVE_TYPE_NOT_IN_CATALOG`, `LIVE_PROPERTY_UNSUPPORTED`, `LIVE_VALUE_INVALID`, `LIVE_ROLLBACK_INCOMPLETE`, `LIVE_AUTHORING_FAILED`, `LIVE_OUTCOME_UNKNOWN`, `EDITOR_BUSY`.

## Evidence and limitations

- SUCCESS means only that the Editor completed the operation and wrote that one asset file: no gameplay, rendering, build or target-runtime claim, and no evidence.
- Project code runs as a consequence and is `TOOL_INHERENT`: AssetPostprocessors on the targeted import, the scratch folder import, `CreateAsset` and the move (they see the temporary `Assets/GposAssetTxn-<id>/…` path briefly), ScriptableObject `OnEnable` on creation and `OnValidate` on edits. No sandbox is claimed.
- Version-control providers other than none are untested; creation refuses whenever a provider is active.
- The bridge admits at most 64 requests in 10 s; beyond that a request is refused `EDITOR_BUSY`, never queued.

## Tests

```bash
python3 tests/test_unity_assets.py
python3 tests/test_unity_live_bridge_core.py
python3 tests/mutate_unity_assets.py
python3 tests/mutate_unity_assets.py --real
```

See [tests/README.md](../tests/README.md#unity-live-asset-tests). The real groups run in disposable synthetic projects only, with lab-owned batch-mode Editors; R4 stops the lab Editor process at exact creation steps to prove recovery from a real crash.
