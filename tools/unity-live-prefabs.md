# Unity live prefab authoring (Phase 2C-6B2B)

Code: [`gpos/tools/unity/prefabs.py`](../gpos/tools/unity/prefabs.py) and the bridge's prefab code in [`Editor/PrefabAuthoring.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/PrefabAuthoring.cs), [`Editor/PrefabResolver.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/PrefabResolver.cs) and the Unity-free core [`Editor/Core/PrefabRules.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/PrefabRules.cs) · adapter id `unity` · bridge `com.gpos.live-bridge` 1.3.0, protocol `gpos.unity.live/4` · status: inspection of every prefab, creation, instantiation and bounded edits of regular prefabs only. It uses the Human-approved session of the [live plane](unity-live-bridge.md), the rules of [Scene authoring](unity-live-authoring.md) and the reference boundary and creation transaction of [asset authoring](unity-live-assets.md).

| Capability | Class | What it does |
|---|---|---|
| `unity.live-prefab-inspect` | `INSPECT`, `READ_ONLY` | one prefab by its root id: type, source, hierarchy, ownership of every object, components, Transforms, whether GPOS may change it and why not, the whole-prefab token; or one component's properties with the effective write authority |
| `unity.live-prefab-instance-inspect` | `INSPECT`, `READ_ONLY` | one prefab instance in a saved Scene: its source prefab, connection status, the instance-to-source object mapping and every override kind Unity reports |
| `unity.live-create-prefab` | `TRANSFORM`, `MUTATING` | one completely plain subtree of a saved Scene saved as a new regular prefab at an exact new `.prefab` path below `Assets/`; the source stays plain Scene content |
| `unity.live-instantiate-prefab` | `TRANSFORM`, `MUTATING` | one instance of a clean regular prefab without nested prefabs in a saved, loaded Scene (Scene authoring) |
| `unity.live-set-prefab-gameobject` | `TRANSFORM`, `MUTATING` | name (never the root's), active state, tag, layer or static flags of one prefab object |
| `unity.live-set-prefab-transform` | `TRANSFORM`, `MUTATING` | local position, rotation or scale of one prefab object |
| `unity.live-add-prefab-component` | `TRANSFORM`, `MUTATING` | one component of a catalogued type (and the components it requires) on one prefab object |
| `unity.live-remove-prefab-component` | `TRANSFORM`, `MUTATING` | one component that nothing on its prefab object requires; never a Transform |
| `unity.live-set-prefab-property` | `TRANSFORM`, `MUTATING` | one allowlisted serialized property of a prefab component |

All nine are `STATEFUL`, `EDITOR`, lease mode `SESSION_REQUIRED`, Edit Mode only and never queued; none produces evidence. Timeouts: 30 s (at most 120 s) for the inspections, 60 s (at most 300 s) otherwise. The preconditions, input rules and at-most-once rules of [Scene authoring](unity-live-authoring.md#preconditions) apply unchanged.

**Never (deferred):** `SaveAsPrefabAssetAndConnect`, applying, reverting or unpacking overrides, Variant creation or editing, nested-prefab authoring, creating or deleting prefab children, model prefab editing, opening, saving, closing or navigating Prefab Mode, package prefab mutation, a generic `PrefabUtility` call. The Scene prefab boundary of [Scene authoring](unity-live-authoring.md) is unchanged: an instance's content is never edited through a Scene command, and inspection grants no apply or revert.

## Identity

A persistent prefab object is named only by `GlobalObjectId_V1-1-<prefab guid>-<file id>-0`: identifier type 1 (the prefab file's GUID and the object's own file id) with prefab id 0. A Scene instance's objects keep the Scene ids of [Scene authoring](unity-live-authoring.md#identity) (type 2, whose file id is the prefab object's and whose prefab id is the instance's). `LoadPrefabContents` gives the isolated copy's objects type-2 ids with the prefab's GUID and the same file ids; GPOS uses that only internally, to map each persistent object to its copy one to one, and never accepts or returns such an id. There is no InstanceID and no hierarchy path.

Every object of a prefab file has an ownership role:

| Role | Meaning |
|---|---|
| `OWNED` | stored as this prefab's own object |
| `NESTED_ROOT`, `NESTED_CONTENT` | the root or the content of an instance of another prefab inside this one |
| `VARIANT_INHERITED` | an object a Variant inherits from its base |

## Scope

A prefab is `REGULAR`, `VARIANT` or `MODEL`, from `ASSETS` or `PACKAGE`. Only a **mutable** prefab is created, instantiated or edited: a regular prefab below `Assets/` without a nested prefab instance, a missing script, hidden content or an embedded asset. Anything else is inspected only, and a mutation of it is `LIVE_PREFAB_REFUSED` before anything is imported. `unity.live-prefab-inspect` lists why a prefab is not mutable:

| Scope reason | Meaning |
|---|---|
| `VARIANT`, `MODEL`, `PACKAGE`, `NESTED_PRESENT`, `MISSING_SCRIPT`, `HIDDEN_CONTENT`, `EMBEDDED_ASSETS` | what the prefab is |
| `STAGE_OPEN`, `DIRTY`, `NOT_WRITABLE`, `VERSION_CONTROL` | the state it is in now |

A component's property listing reports `property_writable` (the property rule of [Scene authoring](unity-live-authoring.md#properties)), `prefab_mutable` (the prefab is mutable now and the component is `OWNED`), and `writable`, which is true only when both are. `refusal` then names the property rule's reason or the scope reason (`NOT_OWNED` for an object the prefab does not own). Inspection never claims an authority the mutation would refuse.

The alpha.18 asset surface keeps its own boundary: `unity.live-asset-inspect` reports a prefab and its root components as references with `authorable` false, and no asset command writes a prefab.

## The prefab token

The whole-prefab token covers the GUID, the root id, the canonical path, the prefab type and source, the SHA-256 of the file and of its `.meta`, and, for every object of the file in hierarchy order, its id, ownership role, dirty flag, hide flags and state: a GameObject's name, active state, tag, layer, static flags, parent, sibling index, children and ordered components, its Transform, its whole serialized state; each component's complete serialized state, hidden properties included, with references as `GlobalObjectId` strings. Any change of any object of the prefab changes it (whole-prefab granularity), and a move changes it (the path), while every id stays.

A token is given only for a regular prefab without nested prefab instances. A Variant's or an outer prefab's effective content also depends on other files, which the token does not cover: their `token_scope` is `NOT_COVERED` and their token is null.

## Guards

Every prefab mutation, and the instantiation, refuses while **any** Prefab Mode stage is open (`LIVE_PREFAB_STAGE_OPEN`): the current stage is not the main stage, or a prefab stage is current. Returning to the Scenes closes every prefab stage of the breadcrumb, so the main stage means that no stage remains. A real test opens a prefab, opens a nested prefab in context, goes back through the breadcrumb and returns to the Scenes, and checks the rule at each step. GPOS never saves, clears, closes or navigates a stage. A Human stage edit is kept, either unsaved in the stage or saved by the Human's own Prefab Mode auto-save setting.

Before anything is imported, an edit also refuses:

- a dirty prefab: any object of its file has unsaved Editor changes (`LIVE_PREFAB_DIRTY`);
- an active version-control provider, or a file it does not hold open for edit (`LIVE_PREFAB_NOT_EDITABLE`; GPOS never checks files out);
- a prefab file, `.meta` or folder the OS does not let GPOS write (`LIVE_PREFAB_NOT_EDITABLE`, checked with `access(W_OK)`; Unity itself would overwrite a read-only prefab);
- a linked or missing file or `.meta`;
- a **dirty loaded Scene that depends on the prefab**: a bounded scan finds every loaded Scene holding an instance whose source is the prefab or includes it (a Variant or an outer prefab). If one is dirty, the edit is `LIVE_PREFAB_CONFLICT` with `dependent_scenes_dirty`, because saving the prefab would propagate into a Scene that may hold unsaved Human work. More than 100000 GameObjects in the loaded Scenes, or more than 32 open Scenes, is `LIVE_PREFAB_LIMIT`; nothing is scanned partially. An unrelated dirty Scene does not block the edit.

## Creating a prefab

`unity.live-create-prefab` takes `source` (a Scene GameObject id), `path` and `expected_subtree_token` (the source's subtree token from `unity.live-object-inspect`).

The source must be a **completely plain Scene subtree** of a saved, loaded Scene:

- no prefab instance root or content, no override, no nested prefab (an instance source would silently become a Variant);
- no hidden, DontSave or NotEditable object or component, and no missing script;
- stable Scene ids;
- at most 256 GameObjects, 1024 components and 32 levels (`LIVE_PREFAB_LIMIT`).

Every serialized reference of every object, hidden ones included, is scanned before anything is written. A reference may be null, an object inside the exact subtree, a reviewed asset of the [alpha.18 kinds](unity-live-assets.md#identity-and-the-reference-boundary), a component's own script, or the subtree root Transform's parent (which becomes a prefab root). Anything else is `LIVE_PREFAB_REFUSED` with `references_refused` naming up to 20 object ids, property paths and reasons:

| Reason | What the reference names |
|---|---|
| `SCENE_OUTSIDE` | a Scene object outside the subtree, which Unity would silently set to null |
| `UNSUPPORTED_ASSET` | a persistent object that is not a reviewed asset kind |
| `OTHER_SCRIPT`, `SCRIPT`, `MISSING_SCRIPT` | a script that is not the component's own, or none |
| `PREFAB_LINK` | a prefab link |
| `UNKNOWN_REFERENCE` | anything else |

The subtree token is compared (`LIVE_AUTHORING_CONFLICT`). The path must be new, below `Assets/`, in an existing folder, end in exactly `.prefab`, and use the [asset path rules](unity-live-assets.md#creating-an-asset) (`LIVE_ASSET_PATH_INVALID`, `LIVE_ASSET_EXISTS` for a file, an orphan `.meta`, a letter-case variant or a known asset; an unwritable folder is `LIVE_PREFAB_NOT_EDITABLE`).

The creation is the alpha.18 transaction, with the same record store, phases, steps, scratch folder, recovery and compensation. Its record is schema `/2` with kind `PREFAB`; Material and ScriptableObject records stay schema `/1`, and each schema is read only with its own kinds.

1. Recover every earlier interrupted creation; write the record (`PREPARED`).
2. Create the scratch folder `Assets/GposAssetTxn-<txn id>` exclusively (`SCRATCH_READY`).
3. Check the source's subtree token again, then `SaveAsPrefabAsset(source, <scratch>/<file name>)`. Never `…AndConnect`, never the final path.
4. Prove the new prefab's identity: exactly the file and its `.meta`, the GUID, a `REGULAR` root of that GUID (type 1), not dirty (`TEMP_PROVEN`, with its hashes).
5. Prove its content: the same hierarchy with the root named as the file; the same names, active states, tags, layers, static flags, local Transforms and component types; and every reference saved as it is in the source. A reference inside the subtree must point to the corresponding prefab object, an asset to the same asset; nothing may be nulled. The source must be unchanged and not connected. Otherwise `LIVE_AUTHORING_FAILED`, and the proven temporary prefab is removed.
6. Re-check the destination, then `ValidateMoveAsset` and `MoveAsset`, which never overwrite. Prove the final prefab again (`FINAL_PROVEN`), remove the proven empty scratch folder and clear the record.

The result names the new prefab and maps each source Scene id to its new prefab id (up to 200). It also gives the prefab token and the file hashes, and reports `undoable` false and `connected` false for the source. The source keeps its Scene ids and state; nothing is recorded for Undo and the Scene is not marked dirty.

Measured with Unity 6000.5.8f1: the prefab root takes the file's name, and it keeps the source root's local Transform.

A crash is recovered by the next creation of any kind:

- nothing created: the record is cleared;
- an exact temporary prefab with no final one: the temporary prefab is removed;
- an exact final prefab: it is kept;
- anything else: `LIVE_PREFAB_CREATE_INCOMPLETE` for a `PREFAB` record, with nothing touched.

No creation is replayed. `LIVE_PREFAB_CREATE_RECOVERED` (a prefab record) and `LIVE_ASSET_CREATE_RECOVERED` (a Material or ScriptableObject record) report what was closed.

## Instantiating a prefab

`unity.live-instantiate-prefab` takes:

- `prefab` (a root id) and `expected_prefab_token`;
- `scene`, an optional `parent` and `sibling`, and optional `local_position`, `local_rotation` and `local_scale`;
- exactly one of `expected_parent_token` (the parent's object token) and `expected_scene_roots_token`, as for `unity.live-create-gameobject`.

A parent inside prefab-instance content is `LIVE_PREFAB_BOUNDARY`. The source must be a mutable prefab: `ASSETS`, `REGULAR`, no nested prefab, not a Variant, model or package prefab. A dirty prefab is `LIVE_PREFAB_DIRTY` before anything is imported: GPOS never turns unsaved prefab state into an instance.

The bridge then imports that one prefab. From the import on, every answer reports `mutation_performed`, even a refusal that created no instance (`import_performed` true, `value_persisted` false, `scene_changed` false). It re-resolves the prefab, requires it still clean and in scope, and compares the fresh token: a difference is `LIVE_PREFAB_CONFLICT` and no instance is created. It checks the parent's or the Scene roots' token again. Then it runs one named Undo group:

1. `InstantiatePrefab` into the Scene or under the parent; the sibling index and the local Transform are set.
2. The id of every object of the new instance, components included, is requested **before** the creation is recorded, so Redo brings back the same ids.
3. `RegisterCreatedObjectUndo`.
4. Verify: the place, the Transform, a connected outermost instance of that prefab corresponding object by object, stable Scene ids, and no other loaded Scene marked dirty. Any mismatch reverts the group, with verification.

The result gives:

- the instance, the instance-to-source mapping (up to 200 objects) and the tokens;
- `overrides_after`: Unity's default root overrides are counted apart from the other property overrides, which project code such as an instance's `OnValidate` may create;
- `scenes_marked_dirty` for the other Scenes (the target Scene is dirty by design; nothing is saved).

## Editing a prefab

`unity.live-set-prefab-gameobject`, `-set-prefab-transform` and `-add-prefab-component` take an `object` id (a prefab GameObject); `-remove-prefab-component` and `-set-prefab-property` take a `component` id. Each needs `expected_prefab_token`. The object must be `OWNED` by a mutable prefab. Every edit runs this lifecycle:

1. Resolve, and validate the edit read-only against the persistent prefab. A typed refusal here reports no mutation: a root rename, an uncatalogued type (`LIVE_TYPE_NOT_IN_CATALOG`), a stale catalog digest (`LIVE_CATALOG_CHANGED`), a `DisallowMultipleComponent` or `RequireComponent` conflict, a Transform, a disallowed property or a wrong reference type.
2. Apply the [guards](#guards).
3. Import that one prefab; from here on `mutation_performed` is reported.
4. Re-resolve; the prefab must still be clean and in scope; compare the fresh token (`LIVE_PREFAB_CONFLICT`).
5. `LoadPrefabContents`; map every persistent object to its isolated copy by file id (`LIVE_PREFAB_CONFLICT` unless one to one); make exactly one edit on the copy, without Undo, and read it back.
6. Immediately before saving, check again: no stage open, the same clean prefab, and the same file and `.meta` bytes. Otherwise the copy is unloaded unsaved (`LIVE_PREFAB_CONFLICT`); an external change is never overwritten.
7. Commit point: `SaveAsPrefabAsset(copy, the same path)`.
8. `UnloadPrefabContents`, always.
9. Re-resolve and verify the persistent result. After an add, recover the exact new persistent ids of the added components (the requested one first, then those its `RequireComponent` added).

A persistent prefab object is never edited directly, and no global `Refresh`, `SaveAssets` or Scene save happens. An edit is not undoable (`undoable` false).

The success result gives the prefab, the new token and file hashes, `file_changed` and `value_persisted` true, plus:

- `unrequested_changes` (up to 20 ids and a count): prefab objects whose state changed besides the edited ones, from project code such as `OnValidate` on the edited copy;
- `scenes_marked_dirty`: loaded Scenes that were clean and are dirty now.

`LIVE_PREFAB_SIDE_EFFECTS` reports both when either is non-empty.

`unity.live-set-prefab-property` uses the property rules of Scene authoring without the Scene prefab boundary; the prefab scope takes its place. Arrays, lists, managed references, hidden properties and engine-owned fields stay refused. A reference may be:

- null;
- an object or component the same prefab owns;
- a reviewed asset of the field's declared type, including another prefab's root or root component and a Sprite. A Sprite is not a Texture and a Texture is not a Sprite.

A Scene object, another prefab's internal object or nested content is refused before anything is imported. The persisted reference is read back as its exact `GlobalObjectId`.

## Persistence uncertainty

From the commit point on, GPOS promises no rollback and restores no backup. It answers `LIVE_OUTCOME_UNKNOWN` (`mutation_performed` true, `commit_started` true), never retried, when:

- `SaveAsPrefabAsset` throws or reports no success;
- the Editor dies;
- the post-save verification fails, or persistence cannot be proven.

The caller re-inspects. A crash before the save leaves the prefab unchanged; a crash after it leaves the saved change, and the request is never replayed.

## Project code and dependency propagation

Project code may run: `OnValidate`, `Awake` and `OnEnable` of `ExecuteAlways` scripts, `AssetPostprocessor` callbacks including `OnPostprocessPrefab`, and the reimport of dependent assets. It is `TOOL_INHERENT`.

Code that changes the prefab during the targeted import makes the fresh token differ, so the edit is refused as `LIVE_PREFAB_CONFLICT`. Code that changes other objects of the edited copy is disclosed in `unrequested_changes`.

Saving a prefab changes the effective content of its instances in loaded Scenes, and of Variants and outer prefabs that include it, whose own files stay byte for byte the same. This dependency propagation is `TOOL_INHERENT`: GPOS neither enumerates nor edits dependent prefabs. Measured with Unity 6000.5.8f1, the instances follow the prefab, but Unity itself did not mark their Scenes dirty (a value change, a rename, and instances whose `OnValidate` runs). A Scene that project code marks dirty is reported in `scenes_marked_dirty`. GPOS never saves or cleans a Scene.

## Diagnostics

| Code | Class | Meaning |
|---|---|---|
| `LIVE_PREFAB_REFUSED` | `INVALID_REQUEST` | outside the authorable scope, a non-owned object, prefab-instance source content, a missing script, hidden content or an unsupported reference |
| `LIVE_PREFAB_LIMIT` | `INVALID_REQUEST` | a fixed bound (prefab, source subtree, instance, overrides or the Scene scan); never truncated |
| `LIVE_PREFAB_STAGE_OPEN` | `CONFLICT` | a Prefab Mode stage is open |
| `LIVE_PREFAB_DIRTY` | `CONFLICT` | the prefab has unsaved Editor changes |
| `LIVE_PREFAB_CONFLICT` | `CONFLICT` | a stale token, a change on disk before the save, the contents mapping, a changed source, or a dirty dependent Scene |
| `LIVE_PREFAB_NOT_EDITABLE` | `CONFLICT` | version control, or the OS does not let GPOS write the file, `.meta` or folder |
| `LIVE_PREFAB_CREATE_INCOMPLETE` | `CONFLICT` | an interrupted prefab creation that cannot be finished or undone from proven facts |
| `LIVE_PREFAB_CREATED`, `LIVE_PREFAB_SAVED`, `LIVE_PREFAB_INSTANTIATED`, `LIVE_PREFAB_CREATE_RECOVERED`, `LIVE_PREFAB_SIDE_EFFECTS` | `INFO` | what happened |

The Scene-authoring and asset codes keep their meanings: `LIVE_OBJECT_REFUSED`, `LIVE_OBJECT_NOT_FOUND`, `LIVE_AUTHORING_CONFLICT`, `LIVE_PREFAB_BOUNDARY`, `LIVE_ASSET_PATH_INVALID`, `LIVE_ASSET_EXISTS`, `LIVE_ASSET_REFUSED`, `LIVE_VALUE_INVALID`, `LIVE_PROPERTY_UNSUPPORTED`, `LIVE_AUTHORING_REFUSED`, `LIVE_AUTHORING_FAILED`, `LIVE_ROLLBACK_INCOMPLETE` and `LIVE_OUTCOME_UNKNOWN`.
