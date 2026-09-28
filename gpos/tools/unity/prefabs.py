"""Unity live prefab authoring (Phase 2C-6B2B): nine fixed capabilities through the audited bridge 1.3.0.

    unity.live-prefab-inspect           one prefab: hierarchy, ownership, scope, token; or one component's properties  READ_ONLY
    unity.live-prefab-instance-inspect  one Scene prefab instance: source, object mapping, every override kind        READ_ONLY
    unity.live-create-prefab            save one plain Scene subtree as a new regular prefab at an exact new path    MUTATING
    unity.live-instantiate-prefab       one instance of a clean regular prefab in a saved, loaded Scene              MUTATING
    unity.live-set-prefab-gameobject    name (never the root's), active, tag, layer, static flags of a prefab object MUTATING
    unity.live-set-prefab-transform     local position, rotation, scale of a prefab object                           MUTATING
    unity.live-add-prefab-component     add one catalogued component type to a prefab object                         MUTATING
    unity.live-remove-prefab-component  remove one component that nothing on its prefab object requires              MUTATING
    unity.live-set-prefab-property      write one allowlisted serialized property of a prefab component              MUTATING

Prefab objects are named only by GlobalObjectId strings of identifier type 1 with prefab id 0 (the prefab file's GUID
and the object's own file id); Scene objects by type-2 ids of saved Scenes. Only a regular prefab below Assets/ without
nested prefab instances, missing scripts, hidden content or embedded assets is created, instantiated or edited; Variants,
models, package prefabs and nested prefabs are inspected only. Nothing here applies, reverts or unpacks overrides,
connects a Scene object to a prefab, creates or deletes prefab children, or opens, saves or closes Prefab Mode — an
open Prefab Mode stage refuses every prefab mutation. A new prefab is written through the alpha.18 creation transaction
(exclusive GPOS scratch folder, no overwrite, no other name, no folder creation) from a completely plain Scene subtree
whose every reference was checked. An edit carries the whole-prefab token of an earlier inspection and is made on
Unity's isolated copy of the prefab after the bridge refused a dirty prefab, version control, an unwritable file and a
dirty Scene the save would propagate into, imported that prefab and compared the token; from the targeted import on,
a refusal still reports mutation_performed. Saving is the commit point: uncertain persistence is OUTCOME_UNKNOWN and
never retried. Nothing here produces evidence. Inputs are strings; structured values are strict JSON text.
"""

import dataclasses
import re

from . import assets as A
from . import authoring as au
from . import live as lv

PREFAB_INSPECT = "unity.live-prefab-inspect"
INSTANCE_INSPECT = "unity.live-prefab-instance-inspect"
CREATE_PREFAB = "unity.live-create-prefab"
INSTANTIATE = "unity.live-instantiate-prefab"
SET_GAMEOBJECT = "unity.live-set-prefab-gameobject"
SET_TRANSFORM = "unity.live-set-prefab-transform"
ADD_COMPONENT = "unity.live-add-prefab-component"
REMOVE_COMPONENT = "unity.live-remove-prefab-component"
SET_PROPERTY = "unity.live-set-prefab-property"
COMMANDS = {PREFAB_INSPECT: "prefab-inspect", INSTANCE_INSPECT: "prefab-instance-inspect", CREATE_PREFAB: "create-prefab",
            INSTANTIATE: "instantiate-prefab", SET_GAMEOBJECT: "set-prefab-gameobject",
            SET_TRANSFORM: "set-prefab-transform", ADD_COMPONENT: "add-prefab-component",
            REMOVE_COMPONENT: "remove-prefab-component", SET_PROPERTY: "set-prefab-property"}
CAPABILITY_IDS = tuple(COMMANDS)
READ_ONLY = (PREFAB_INSPECT, INSTANCE_INSPECT)
EDITS = (SET_GAMEOBJECT, SET_TRANSFORM, ADD_COMPONENT, REMOVE_COMPONENT, SET_PROPERTY)

LIMITATION = ("Editor prefab state only: SUCCESS means the Unity Editor completed this operation. It is not evidence of "
              "gameplay, rendering, build or target behaviour. Project code may have run as a consequence "
              "(OnValidate, Awake and OnEnable of ExecuteAlways scripts, AssetPostprocessors including "
              "OnPostprocessPrefab, the reimport of dependent assets); saving a prefab also changes the effective "
              "content of Variants and prefabs that include it and of their instances in loaded Scenes, which Unity "
              "marks dirty (TOOL_INHERENT dependency propagation; nothing is saved). A creation and an edit are not "
              "undoable; an instantiation is one Scene Undo group.")

PREFAB_ID = re.compile(r"^GlobalObjectId_V1-1-([0-9a-f]{32})-(\d{1,20})-0$")
PREFAB_PATH_EXT = ".prefab"

InputProblem = au.InputProblem


def prefab_id(name, value):
    """A persistent prefab object: GlobalObjectId type 1 with prefab id 0. Never a Scene, prefab-contents, source-asset
    or built-in id."""
    value = au._text(name, value)
    m = PREFAB_ID.fullmatch(value)
    if not m:
        raise InputProblem("LIVE_OBJECT_REFUSED", f"{name} is a prefab object id GlobalObjectId_V1-1-<prefab guid>-"
                                                  f"<file id>-0")
    if int(m.group(2)) >= 2 ** 64 or m.group(1) == au.ZERO_GUID or m.group(1) in au.BUILTIN_GUIDS:
        raise InputProblem("LIVE_OBJECT_REFUSED", f"{name}: the prefab id's GUID or file id is invalid")
    return value


def prefab_value(kind, value):
    """A prefab property value: the Scene-authoring kinds, except that an object reference names an object of the same
    prefab or a reviewed asset (a prefab never references a Scene object)."""
    if kind == "object" and value is not None:
        if not isinstance(value, str):
            raise InputProblem("LIVE_VALUE_INVALID", "value is null, an object of this prefab or an asset id")
        if not au.ASSET_ID.fullmatch(value):
            raise InputProblem("LIVE_VALUE_INVALID", "a prefab never references Scene objects")
        au.asset_id("value", value)
        return value
    return au.property_value(kind, value)


INPUTS = {
    PREFAB_INSPECT: {"prefab": prefab_id, "component": prefab_id, "path_prefix": au.path_prefix,
                     "page": au.whole(0, 10000)},
    INSTANCE_INSPECT: {"object": au.object_id, "page": au.whole(0, 10000)},
    CREATE_PREFAB: {"source": au.object_id, "path": A.asset_path(PREFAB_PATH_EXT), "expected_subtree_token": au.token},
    INSTANTIATE: {"prefab": prefab_id, "scene": au.scene_path, "parent": au.object_id, "sibling": au.whole(0, 1_000_000),
                  "local_position": au.vector3, "local_rotation": au.rotation, "local_scale": au.vector3,
                  "expected_prefab_token": au.token, "expected_parent_token": au.token,
                  "expected_scene_roots_token": au.token},
    SET_GAMEOBJECT: {"object": prefab_id, "name": au.name_text, "active": au.boolean, "tag": au.tag_text,
                     "layer": au.whole(0, 31), "static_flags": au.static_flags, "expected_prefab_token": au.token},
    SET_TRANSFORM: {"object": prefab_id, "local_position": au.vector3, "local_rotation": au.rotation,
                    "local_scale": au.vector3, "expected_prefab_token": au.token},
    ADD_COMPONENT: {"object": prefab_id, "type_id": au.type_id, "expected_prefab_token": au.token,
                    "expected_catalog_digest": au.digest},
    REMOVE_COMPONENT: {"component": prefab_id, "expected_prefab_token": au.token},
    SET_PROPERTY: {"component": prefab_id, "path": au.property_path, "kind": au.kind, "value": au.strict_json,
                   "expected_prefab_token": au.token},
}
REQUIRED = {
    PREFAB_INSPECT: ("prefab",), INSTANCE_INSPECT: ("object",),
    CREATE_PREFAB: ("source", "path", "expected_subtree_token"),
    INSTANTIATE: ("prefab", "scene", "expected_prefab_token"),
    SET_GAMEOBJECT: ("object", "expected_prefab_token"), SET_TRANSFORM: ("object", "expected_prefab_token"),
    ADD_COMPONENT: tuple(INPUTS[ADD_COMPONENT]), REMOVE_COMPONENT: tuple(INPUTS[REMOVE_COMPONENT]),
    SET_PROPERTY: tuple(INPUTS[SET_PROPERTY]),
}


def input_kinds(cap):
    return ("unity_project",) + tuple(INPUTS[cap])


def parse_inputs(cap, inputs):
    """The bridge arguments for `cap` (every key, None when absent), or InputProblem."""
    inputs = dict(inputs or {})
    inputs.pop("unity_project", None)
    missing = [k for k in REQUIRED[cap] if k not in inputs]
    if missing:
        raise InputProblem("INVALID_TOOL_REQUEST", f"{cap} requires {', '.join(missing)}")
    args = {k: None for k in INPUTS[cap]}
    for k, v in inputs.items():
        if k not in INPUTS[cap]:
            raise InputProblem("INVALID_TOOL_REQUEST", f"{cap} takes no input {k!r}")
        args[k] = INPUTS[cap][k](k, v)
    if cap == PREFAB_INSPECT and args["path_prefix"] is not None and args["component"] is None:
        raise InputProblem("INVALID_TOOL_REQUEST", "path_prefix lists one component's properties: it needs component")
    elif cap == INSTANTIATE:
        if args["parent"] is not None:
            if args["expected_parent_token"] is None:
                raise InputProblem("INVALID_TOOL_REQUEST", "expected_parent_token (the parent's object token) is "
                                                           "required with a parent")
            au._forbidden(args, "expected_scene_roots_token", "with a parent")
        else:
            if args["expected_scene_roots_token"] is None:
                raise InputProblem("INVALID_TOOL_REQUEST", "expected_scene_roots_token is required at the Scene root")
            au._forbidden(args, "expected_parent_token", "at the Scene root")
    elif cap == SET_GAMEOBJECT:
        if all(args[k] is None for k in ("name", "active", "tag", "layer", "static_flags")):
            raise InputProblem("INVALID_TOOL_REQUEST", "at least one of name, active, tag, layer and static_flags")
    elif cap == SET_TRANSFORM:
        if all(args[k] is None for k in ("local_position", "local_rotation", "local_scale")):
            raise InputProblem("INVALID_TOOL_REQUEST", "at least one of local_position, local_rotation and "
                                                       "local_scale")
    elif cap == SET_PROPERTY:
        args["value"] = prefab_value(args["kind"], args["value"])
    return args


# ---------------------------------------------------------------- execution

def _side_effects(cap, data):
    """LIVE_PREFAB_SIDE_EFFECTS when project code changed other prefab objects or Scenes became dirty."""
    changed, scenes = data.get("unrequested_changes") or [], data.get("scenes_marked_dirty") or []
    if not changed and not scenes:
        return []
    return [lv._diag("LIVE_PREFAB_SIDE_EFFECTS", f"{data.get('unrequested_change_count', len(changed))} other prefab "
                                                 f"object(s) changed and {len(scenes)} loaded Scene(s) were marked "
                                                 f"dirty as a consequence (nothing was saved)", cap,
                     {"unrequested_changes": changed, "scenes_marked_dirty": scenes})]


def _success(cap, data):
    diags = A.recovered_diagnostics(cap, data.get("recovered"))
    if cap == CREATE_PREFAB:
        diags.append(lv._diag("LIVE_PREFAB_CREATED", f"created {(data.get('created') or {}).get('path')!r}", cap))
    elif cap == INSTANTIATE:
        diags.append(lv._diag("LIVE_PREFAB_INSTANTIATED", f"instantiated {(data.get('prefab') or {}).get('path')!r} "
                                                          f"in {(data.get('scene') or {}).get('path')!r}", cap))
    elif cap in EDITS:
        diags.append(lv._diag("LIVE_PREFAB_SAVED", f"saved {(data.get('prefab') or {}).get('path')!r}", cap))
    if cap not in READ_ONLY:
        diags += _side_effects(cap, data)
    return diags


def outcome(cap, r):
    result = au.outcome(cap, r, commands=COMMANDS, read_only=READ_ONLY, limitation=LIMITATION,
                        success=lambda data: _success(cap, data))
    refused = r.response is not None and r.response.get("status") != "OK"
    data = (r.response or {}).get("data")
    if refused and isinstance(data, dict) and data.get("recovered"):
        # earlier interrupted creations were closed before this request was refused: say so
        result = dataclasses.replace(result, diagnostics=tuple(result.diagnostics)
                                     + tuple(A.recovered_diagnostics(cap, data["recovered"])))
    return result


def execute(request, context, facts=None, sleep=None, monotonic=None):
    return au.execute(request, context, facts=facts, sleep=sleep, monotonic=monotonic, parse=parse_inputs,
                      command=COMMANDS[request.capability_id], finish=outcome)
