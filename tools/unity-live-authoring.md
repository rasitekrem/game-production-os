# Unity live Scene authoring (Phase 2C-6B1, extended in 2C-6B2A)

Code: [`gpos/tools/unity/authoring.py`](../gpos/tools/unity/authoring.py) and the bridge's authoring code in [`Editor/Authoring.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Authoring.cs), [`Scene.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Scene.cs), [`Properties.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Properties.cs), [`Catalog.cs`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Catalog.cs) and the Unity-free core in [`Editor/Core/`](../gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/PropertyRules.cs) · adapter id `unity` · bridge `com.gpos.live-bridge` 1.2.0, protocol `gpos.unity.live/3`. Built on the [live Editor plane](unity-live-bridge.md): every capability here needs its Human-approved session. Assets themselves are read, created and edited by the [asset capabilities](unity-live-assets.md); since alpha.18 a Scene property may *reference* a reviewed asset, and one Renderer material slot can be set.

Thirteen fixed capabilities change or read the open Scene of the attached Editor. Each one maps to one bridge command, and each command reads or changes only what its name says.

| Capability | Class | What it does |
|---|---|---|
| `unity.live-object-inspect` | `INSPECT`, `READ_ONLY` | one GameObject (state, parent, children, components, Transform, prefab role) or one Scene's root objects, with every token below |
| `unity.live-component-types` | `INSPECT`, `READ_ONLY` | the closed component catalog and its digest (pages of 200) |
| `unity.live-properties` | `INSPECT`, `READ_ONLY` | one Component's listed properties: kind, value, whether writable and why not (pages of 256) |
| `unity.live-create-gameobject` | `TRANSFORM`, `MUTATING` | one empty GameObject, at the Scene root or under a parent, with optional local pose and sibling index |
| `unity.live-delete-gameobject` | `TRANSFORM`, `MUTATING` | one GameObject and everything below it |
| `unity.live-set-parent` | `TRANSFORM`, `MUTATING` | move one GameObject under another parent of the same Scene, or to its root, keeping its local or its world pose |
| `unity.live-set-gameobject` | `TRANSFORM`, `MUTATING` | name, active state, tag, layer, static flags |
| `unity.live-set-transform` | `TRANSFORM`, `MUTATING` | local position, rotation (a unit quaternion), scale |
| `unity.live-add-component` | `TRANSFORM`, `MUTATING` | one catalogued component type, plus the components it requires |
| `unity.live-remove-component` | `TRANSFORM`, `MUTATING` | one component nothing on its GameObject requires |
| `unity.live-set-property` | `TRANSFORM`, `MUTATING` | one allowlisted serialized property, validated first and read back after |
| `unity.live-save-scene` | `TRANSFORM`, `MUTATING` | one open Scene that already has a path, saved to that path |
| `unity.live-set-renderer-material` | `TRANSFORM`, `MUTATING` | one shared-material slot of a Renderer (alpha.18; [below](#renderer-material-slots)) |

All thirteen are `STATEFUL`, `EDITOR`, lease mode `SESSION_REQUIRED` (session id and canonical owner verified; nothing taken or released), need no tool probe, declare no evidence and support no dry run. Timeouts: 30 s (at most 120 s), and 60 s (at most 300 s) for saving. The mutating ones need explicit mutation consent. Inputs are strings, as the command line gives them: identifiers have their own grammars, each matched against the whole string (a value that ends in a newline or carriage return is refused), and vectors, rotations and property values are strict JSON text (no duplicate keys, no `NaN`, depth at most 4). GPOS validates every input before the Editor sees it, and the bridge validates it again.

**Never:** prefab asset editing, Prefab Mode, applying, reverting or unpacking a prefab instance, texture, audio or model authoring, cross-Scene references, creating, renaming, deleting or Save-As of Scenes, array or list changes (other than the one reviewed Renderer material slot), managed-reference changes, curve or gradient editing, source generation, package operations other than the reviewed bridge upgrade, reflection, C#, `-executeMethod`, menu execution, input, live evidence. Material and ScriptableObject creation and editing are the [asset capabilities](unity-live-assets.md), with their own rules.

## Preconditions

- The session is LIVE and owned by the caller, and the running bridge is exactly the audited 1.2.0 bridge. An earlier released bridge (1.0.0 or 1.1.0) is `LIVE_BRIDGE_INCOMPATIBLE` until it is [upgraded](unity-live-bridge.md#upgrading-the-bridge).
- The Editor is in Edit Mode with nothing pending: not playing or paused, not entering or leaving Play Mode, not compiling, updating, reloading or quitting. Otherwise `EDITOR_BUSY`, for the read-only capabilities too. Nothing is queued.
- The alpha.16 at-most-once rules apply unchanged: start deadlines, withdrawal (`LIVE_REQUEST_WITHDRAWN`, never executed), and `LIVE_OUTCOME_UNKNOWN` for a request the Editor claimed but did not answer. That outcome has status `OUTCOME_UNKNOWN`, `mutation_performed` true for a mutating capability, and is never retried; inspect the Scene again. A request interrupted by a Domain Reload or restart is answered NOT_REPLAYED and never runs again. An authoring command runs within one Editor update, so a Domain Reload cannot split it.

## Identity

An object is named only by its `GlobalObjectId` string, `GlobalObjectId_V1-2-<scene guid>-<file id>-<prefab id>`. Identifier type 2 is a Scene object; an asset id as the object to author, and every other type, is `LIVE_OBJECT_REFUSED` (an asset id may only be the *value* of an object reference, below). There is no InstanceID, EntityId, hierarchy path or temporary id. The bridge accepts an id only when:

- the GUID is that of a loaded, saved Scene under `Assets/`; a Scene that is not loaded is `LIVE_OBJECT_NOT_FOUND`;
- it names a GameObject or Component of exactly that Scene, not an asset;
- the object is not hidden in the Hierarchy, excluded from saving or not editable (and a Component not hidden in the Inspector);
- the object's own canonical id is exactly the string given.

A Scene that was never saved has no path, and its objects have the null id `GlobalObjectId_V1-0-000…-0-0`. Both are `LIVE_SCENE_NOT_SAVED`: GPOS never chooses a path. An id that no longer resolves is `LIVE_OBJECT_NOT_FOUND`. A request resolves at most 256 ids.

The same id survives save, rename, reparent, Scene reload, Domain Reload, delete then Undo, remove then Undo, and create or add then Undo then Redo. For a created GameObject this holds because its id is allocated before its creation is recorded for Undo. Unity's own id lookup misses a component that Redo re-created until the Scene is next saved, although the component reports the same id. When the lookup misses, the bridge compares the ids of the Scene's objects (at most 100,000 objects and components). This is still identity by `GlobalObjectId` only.

## Tokens

Every mutation carries the tokens of an earlier inspection. The bridge recomputes them **before anything changes**; any difference is `LIVE_AUTHORING_CONFLICT` and nothing is changed. The Human's own editing is never blocked: a request decided on stale state is.

A token is SHA-256 over a canonical, length-prefixed sequence of fields, truncated to 32 hex digits. Each kind starts with its own domain string. A float is its binary32 bit pattern with negative zero written as zero, and nothing is formatted with a culture. Each token claims only what it hashes:

| Token | Covers |
|---|---|
| `object` | id, name, active flag, tag, layer, static flags, parent id, sibling index, the ordered child ids, and the ordered component list (type key and id, or a missing-script marker) |
| `transform` | the Transform's id and kind, local position, rotation and scale; for a RectTransform also anchors, anchored position, size delta and pivot |
| `transform_chain` | the Scene path, then for every object from the Scene root down to the object: its id and `transform` token |
| `component` | id, type key, enabled (a Behaviour), and **every** top-level serialized property, visible or hidden, as path, property type, type name and an opaque hash of its whole value, nested values included. Plain values use Unity's content hash. An object reference contributes the referenced object's `GlobalObjectId`, because Unity's own hash of a reference follows the session-local instance id, which Undo and Scene reloads change. More than 2,048 top-level properties or 200,000 values is `LIVE_AUTHORING_LIMIT`. |
| `subtree` | pre-order, the object and every descendant: id, `object` token, `transform` token and the `component` token of each component (at most 2,000 objects and 20,000 components, otherwise `LIVE_AUTHORING_LIMIT`) |
| `scene_roots` | the Scene path and the ordered root object ids (at most 10,000) |

Hashing hidden state is concurrency detection only. A hidden property, `m_Script`, `m_GameObject`, prefab internals and hide flags are hashed, but that never lists, values or writes them. A token stays the same whenever the state it covers is unchanged: across a Domain Reload, a save, reopening the saved Scene, and an Undo that restores an object exactly. Unity's session-local instance ids, which a reload, a reopen or an Undo re-creation reassign, are never hashed. If project-authored code actually changes covered state during a reload or reopen (for example an `OnValidate` that runs again when scripts reload or a Scene loads), the token changes. That is expected and correct: a request decided on the old token is a conflict.

Required tokens per capability:

| Capability | Tokens compared before any change |
|---|---|
| `unity.live-create-gameobject` | the parent's `object`, or `scene_roots` at the root |
| `unity.live-delete-gameobject` | `subtree` |
| `unity.live-set-parent` | the object's `object` and `transform`; the current parent's `object` (or `scene_roots` when the object is at the root); the new parent's `object` (or `scene_roots` when the new parent is the root). With `keep_world=true` also the object's `transform_chain` and, under a new parent, that parent's `transform_chain` |
| `unity.live-set-gameobject` | `object` |
| `unity.live-set-transform` | `transform` |
| `unity.live-add-component` | `object` and the catalog digest |
| `unity.live-remove-component` | the component's `component` token and its GameObject's `object` |
| `unity.live-set-property` | `component` |
| `unity.live-set-renderer-material` | `component` (the Renderer's) |

Keeping the world pose depends on every Transform from the Scene root down to the object and down to the new parent. A Human changing the object, any ancestor of it, the new parent or any ancestor of the new parent between inspection and a stale `keep_world=true` request is therefore a conflict. A `keep_world=false` move depends on none of them and takes no chain token; GPOS refuses one if it is given. `unity.live-object-inspect` returns the object's tokens together with its parent's `object` and `transform_chain` and the Scene's `scene_roots`. A mutation returns the new tokens of what it changed.

## Component catalog

A type can be added only when it is in the closed catalog, found by its exact id `<assembly>::<full name>` (nested types use `+`). The catalog is rebuilt from the Editor's own type information on every use, so it follows every recompile and Domain Reload. A type is catalogued when it is:

- a concrete, non-generic, public, non-obsolete Component that the Add Component menu does not hide;
- from a Unity runtime module or a Player assembly of the project (not an Editor or test assembly);
- neither a Transform nor a RectTransform;
- for a MonoBehaviour, the class of exactly one runtime MonoScript.

Two classes with one full name in two assemblies are two entries. Each entry reports its kind (NATIVE or SCRIPT), whether it disallows multiple instances, the sorted type keys its `RequireComponent` attributes name, and whether it runs in Edit Mode (`ExecuteAlways` or `ExecuteInEditMode`).

The catalog digest is SHA-256 over every entry in type-id order, with every one of those fields. A recompile that changes a type's `RequireComponent`, `DisallowMultipleComponent` or edit-mode attribute changes the digest even when the type id does not. `unity.live-add-component` takes the digest it was decided on: a changed catalog is `LIVE_CATALOG_CHANGED`, and a type outside it is `LIVE_TYPE_NOT_IN_CATALOG`.

Adding a single-instance type that is already present is `LIVE_AUTHORING_REFUSED`. Unity adds required components in the same Undo group, and the result lists them all. If Unity itself declines to add the component (for example a 2D and a 3D physics body), the group is reverted and the result is `LIVE_AUTHORING_REFUSED` with `mutation_performed` true. A Transform is never removed, and a component another component on the same GameObject requires is not removed (`LIVE_AUTHORING_REFUSED`).

## Properties

**Enumeration.** `unity.live-properties` lists visible properties only, plus a Behaviour's `m_Enabled`. It enters only plain serializable structs and classes: never arrays or lists, managed references, or the parts of a vector, colour or rect. Hidden serialized state is covered by the component token but never listed or valued. Each entry has its path, display name, property type, type name, depth, kind, `writable` and `refusal`. For a supported kind it also has the value: strings are cut to 256 characters with `value_truncated`, an enum gives its names, and an object reference gives SCENE or ASSET (with the id), OTHER_SCENE or NONE, never an asset path. An AudioSource's `m_audioClip` is refused (AUDIO_USES_RESOURCE): Unity 6 takes the clip through `m_Resource`.

**Default-deny write.** A property is writable only when every one of these holds; otherwise the refusal says which failed:

- its path is plain identifiers, at most 6 segments and 256 characters (PATH_UNSUPPORTED);
- no segment is denied: `m_Script`, `m_GameObject`, `m_Name`, `m_ObjectHideFlags`, `m_EditorHideFlags`, `m_PrefabInstance`, `m_PrefabAsset`, `m_CorrespondingSourceObject`, `m_EditorClassIdentifier`, `m_PrefabParentObject`, `m_PrefabInternal`, `m_Father`, `m_Children` and `m_Component` (DENIED);
- it is listed (HIDDEN);
- every ancestor is a listed plain struct or class (INSIDE_UNSUPPORTED);
- it is editable (NOT_EDITABLE);
- its property type and type name are exactly one kind of the table below (TYPE_UNSUPPORTED);
- the component is not a Transform, which `unity.live-set-transform` covers (TRANSFORM_USES_SET_TRANSFORM);
- the component is not prefab-instance content (PREFAB_BOUNDARY).

| Kind | Unity property (type name) | Value | Checked before writing | Read back |
|---|---|---|---|---|
| `bool` | Boolean | `true` / `false` | type | exact |
| `int8` `int16` `int32` | Integer (`sbyte`, `short`, `int`) | JSON integer | range of the type (Unity would clamp) | exact |
| `uint8` `uint16` `uint32` | Integer (`byte`, `ushort`, `uint`) | JSON integer | range of the type (Unity would wrap or clamp) | exact |
| `int64` `uint64` | Integer (`long`, `ulong`) | decimal string | full range; a JSON number would lose precision | exact |
| `float32` | Float (`float`) | number | finite and within binary32 (Unity would store infinity) | the value as binary32 |
| `float64` | Float (`double`) | number | finite | exact |
| `string` | String | string | at most 4,096 characters, no unpaired surrogate | exact |
| `enum` | Enum | one name | exactly one declared name; an index or a combination is refused | the same index |
| `vector2` `vector3` `vector4` `rect` `color` | Vector2/3/4, Rect, Color | array of numbers | each component within binary32 | binary32 per component |
| `quaternion` | Quaternion | `[x, y, z, w]` | length within 1e-4 of 1, then normalized (Unity would accept any quaternion) | within 1e-6 |
| `vector2int` `vector3int` `rectint` | Vector2Int/3Int, RectInt | array of integers | 32-bit | exact |
| `bounds` | Bounds | `[[center], [extents]]` | binary32, extents not negative | binary32 |
| `boundsint` | BoundsInt | `[[position], [size]]` | 32-bit | exact |
| `layermask` | LayerMask | integer | 0 to 2^32−1 | exact |
| `object` | ObjectReference `PPtr<T>` | `null`, a Scene object id or an asset id | see below | the same object |

Refused kinds include characters, arrays and lists and their sizes, managed references, `ExposedReference`, `AnimationCurve`, `Gradient`, `Hash128` and fixed buffers.

A `[Flags]` enum is not detected. Only one declared name can be written, which sets exactly that one value; a combined or unmapped state is shown as `null` and cannot be written. Two names for one value are ambiguous: the write reads back as the other name, so it is reverted and `LIVE_VALUE_INVALID`.

**The write pipeline**, in this order:

1. Resolve the component.
2. Compare its token.
3. Check the property is on the allowlist.
4. Check the kind matches.
5. Validate the value.
6. Open one Undo group.
7. Write.
8. Apply.
9. Read back through a fresh `SerializedObject`.
10. Verify the value.
11. On any difference, revert.

A value Unity or project code did not keep (for example an `OnValidate` that clamps it) is reverted and is `LIVE_VALUE_INVALID` with `mutation_performed` true. Values are never clamped or coerced by GPOS.

**Object references** accept `null`, a validated GameObject or Component of the same Scene, or (since alpha.18) a reviewed asset the [asset resolver](unity-live-assets.md#identity-and-the-reference-boundary) accepts: a Material, Texture, Sprite, AudioClip or AudioResource, Mesh, prefab or model root, prefab-root Component or catalogued ScriptableObject, from `Assets/`, a registered package or the fixed built-in table. There are no cross-Scene references. The field's declared type `T` (from `PPtr<T>`) must accept the object: `T` is the object's type or one of its base types (`Object` accepts any). This is checked **before** Unity assigns anything, because Unity silently stores `null` for a wrong type and reports success: a Sprite is not a Texture, a Texture is not a Sprite, a prefab's MeshRenderer is not a BoxCollider. An AudioSource's `m_Resource` takes AUDIO assets only. The read-back must be exactly the object written, so two same-named types from different assemblies cannot slip through. Referencing an asset changes the Scene only; the asset is not touched.

## Renderer material slots

`unity.live-set-renderer-material` sets one slot of a Renderer's **shared** materials: inputs `renderer` (the Renderer component's id), `material` (a MATERIAL asset id, or the empty string to clear the slot), `slot` (0 to 7) and `expected_component_token`. It writes the serialized `m_Materials` array exactly as the Inspector does, through the same apply, Undo and read-back pipeline and the same component token as `unity.live-set-property`. It never calls `Renderer.material` or `Renderer.materials`, whose getters instantiate a copy of the material (the lab counted one new instance per call); the real tests count non-persistent Materials before and after and find none created.

- A slot below the Renderer's current slot count is replaced (REPLACE).
- A Renderer with no slots gets its first slot only for slot 0 (CREATE_FIRST).
- Everything else is `LIVE_PROPERTY_UNSUPPORTED`: slots are never appended, inserted, removed or resized, and `unity.live-set-property` still refuses every array path.

It runs as one `GPOS: set material <slot> of <type> on <object>` Undo group, reads back `sharedMaterials[slot]` and the slot count, and reverts on any difference. The prefab boundary applies (role NONE only), and a stale component token is `LIVE_AUTHORING_CONFLICT`.

## Undo, rollback and dirty state

Every mutating command runs as exactly one Undo group named `GPOS: <what>`, collapsed, so one Cmd-Z in the Editor undoes one GPOS command and Redo re-applies it. The primitives are:

- create: construct fully, allocate its ids, then `RegisterCreatedObjectUndo`;
- delete and remove: `Undo.DestroyObjectImmediate`;
- reparent: `Undo.SetTransformParent` and `Undo.SetSiblingIndex`;
- GameObject fields and Transform: `Undo.RecordObject` (static flags through `GameObjectUtility`);
- add a component: `Undo.AddComponent`;
- set a property: `ApplyModifiedProperties`.

A cycle, a cross-Scene move or a sibling index out of range is refused before the group opens.

If anything fails after the group opened (an exception, a read-back mismatch, Unity declining an operation), the bridge calls `Undo.RevertAllDownToGroup` and recomputes the operation's pre-state tokens from ids:

- **Equal:** GPOS verified that the authoring state covered by the operation's pre-state tokens was restored. The result names the specific code, with `reverted` and `restored` true.
- **Different:** `LIVE_ROLLBACK_INCOMPLETE` (FAILED). Inspect again before doing anything else.

This proves restoration only for what those tokens cover; it is **not** a claim that the whole Scene or project was restored. Project code that ran because of the operation (`OnValidate`, component callbacks, `ExecuteAlways` scripts) may have changed other state, and may change state again when Undo restores it. That falls under the adapter's `TOOL_INHERENT` limitation, and no sandbox is claimed.

`mutation_performed`:

| Case | `mutation_performed` |
|---|---|
| refused before the Undo group opened (input, token, prefab boundary, catalog, busy, limits) | false |
| success | true |
| began, then reverted (restored or not) | true |
| unknown outcome (claimed, no answer) | true |
| a read-only capability | always false |

A reverted operation may leave the Scene marked dirty; this is disclosed, and the bridge never marks a Scene clean. Nothing is saved except by `unity.live-save-scene`.

## Prefab boundary

The bridge classifies every object relative to prefab instances as one of:

- NONE;
- INSTANCE_ROOT: an outermost instance root;
- INSTANCE_CONTENT: inside an instance;
- ADDED_OVERRIDE: an object or component added to an instance;
- MISSING: an instance whose asset is gone.

| Target | Allowed |
|---|---|
| NONE | everything above |
| INSTANCE_ROOT | rename (name only); set its Transform; move it (keeping local or world pose) where neither parent is prefab content; delete it with everything below it |
| INSTANCE_CONTENT, ADDED_OVERRIDE, MISSING | inspection only |

Everything else is `LIVE_PREFAB_BOUNDARY`, including:

- creating under an instance;
- moving something into one;
- the root's tag, layer, active state or static flags;
- adding or removing components on the root;
- setting properties on instance content.

No prefab override is ever created, applied or reverted, and no prefab asset is touched.

## Saving

`unity.live-save-scene` takes the Scene's own path. The Scene must be loaded and already saved, and it is saved to that path with `EditorSceneManager.SaveScene`: never Save As, never a chosen path, never a dialog, never a new Scene asset. An unsaved Scene (empty path) is `LIVE_SCENE_NOT_SAVED` before any Unity call; a path that is not a loaded Scene is `LIVE_OBJECT_NOT_FOUND`. Success reports `LIVE_SCENE_SAVED` and the Scene's dirty state. Saving includes the Human's own unsaved edits, and project save callbacks may run. Saving is never combined with another operation and never happens automatically.

## Evidence and limitations

- SUCCESS means only that the Editor completed the operation on the open Scene: no gameplay, rendering, build or target-runtime claim, and no evidence.
- Applying serialized changes, adding components, activating objects and saving can run project Editor code. This is `TOOL_INHERENT`, and no sandbox is claimed.
- A GameObject is created in the active Scene and then moved to the requested Scene. **Corrected in alpha.18:** alpha.17 stated that with several Scenes open the active Scene may be marked dirty. The 2C-6B2A research and the permanent real test (a second saved Scene open, the first one active) show that creating in the non-active Scene dirties only that Scene; the active Scene stays clean. A reference or move across the two Scenes is refused (`LIVE_VALUE_INVALID`, `LIVE_OBJECT_REFUSED`).
- The bridge admits at most 64 requests in 10 s; beyond that a request is refused `EDITOR_BUSY`, never queued.

## Tests

```bash
python3 tests/test_unity_authoring.py
python3 tests/test_unity_live_bridge_core.py
python3 tests/mutate_unity_authoring.py
python3 tests/mutate_unity_authoring.py --real
```

[`tests/test_unity_authoring.py`](../tests/test_unity_authoring.py) has fast groups against the protocol stand-in, the bridge-upgrade recovery matrix and a frozen-tag check of the release history (1.0.0 from `v1.0.0-alpha.16`, 1.1.0 from `v1.0.0-alpha.17`). Its real groups, including a second saved Scene and real upgrades from both earlier releases, use disposable synthetic projects ([`unity_fixture_builder.make_authoring_project`](../tests/unity_fixture_builder.py)) in lab-owned batch-mode Editors. There, the test-only [testkit](../tests/unity_live_testkit/com.gpos.live-bridge-testkit/Editor/Testkit.cs) also stands in for the Human's Inspector edits, Hierarchy drags and Cmd-Z. The C# core tests cover the grammars, tokens, catalog digest, property rules, busy rule and protocol. The mutation harness breaks each guarantee once: the fast set against the GPOS side and the core, `--real` against the Editor-side code in real lab Editors.
