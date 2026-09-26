"""Unity live Scene authoring (Phase 2C-6B1): twelve fixed capabilities through the audited bridge 1.1.0.

    unity.live-object-inspect      one GameObject (or a Scene's roots) with every token            READ_ONLY
    unity.live-component-types     the closed component catalog and its digest                     READ_ONLY
    unity.live-properties          one Component's listed serialized properties                    READ_ONLY
    unity.live-create-gameobject   create one GameObject in a saved, loaded Scene                  MUTATING
    unity.live-delete-gameobject   delete one GameObject and everything below it                   MUTATING
    unity.live-set-parent          move one GameObject under another parent (or the Scene root)    MUTATING
    unity.live-set-gameobject      name, active, tag, layer, static flags                          MUTATING
    unity.live-set-transform       local position, rotation, scale                                 MUTATING
    unity.live-add-component       add one catalogued component type                               MUTATING
    unity.live-remove-component    remove one component                                            MUTATING
    unity.live-set-property        write one allowlisted serialized property                       MUTATING
    unity.live-save-scene          save one saved Scene to its own path                            MUTATING

Every one needs the attached SESSION (SESSION_REQUIRED) and the Editor in Edit Mode with nothing pending; nothing
is queued. Objects are named only by GlobalObjectId strings of Scene objects in saved Scenes. Every mutation
carries the tokens it was decided on; the bridge compares them before changing anything (a difference is
LIVE_AUTHORING_CONFLICT), runs the change as one named Undo group, reads the result back, and on any mismatch
reverts the group and re-verifies the state those tokens cover. Nothing here produces evidence: SUCCESS means
only that the Editor completed the operation. Inputs are strings (as the command line gives them); structured
values are strict JSON text. This module validates them before the Editor sees them, and the bridge validates
them again.
"""

import json
import math
import re

from .. import diagnostics as dg
from ..execution import AdapterOutcome
from . import live as lv
from . import live_ipc as ipc
from . import live_status as ls

INSPECT_OBJECT = "unity.live-object-inspect"
COMPONENT_TYPES = "unity.live-component-types"
PROPERTIES = "unity.live-properties"
CREATE = "unity.live-create-gameobject"
DELETE = "unity.live-delete-gameobject"
SET_PARENT = "unity.live-set-parent"
SET_GAMEOBJECT = "unity.live-set-gameobject"
SET_TRANSFORM = "unity.live-set-transform"
ADD_COMPONENT = "unity.live-add-component"
REMOVE_COMPONENT = "unity.live-remove-component"
SET_PROPERTY = "unity.live-set-property"
SAVE_SCENE = "unity.live-save-scene"
COMMANDS = {INSPECT_OBJECT: "object-inspect", COMPONENT_TYPES: "component-types", PROPERTIES: "properties",
            CREATE: "create-gameobject", DELETE: "delete-gameobject", SET_PARENT: "set-parent",
            SET_GAMEOBJECT: "set-gameobject", SET_TRANSFORM: "set-transform", ADD_COMPONENT: "add-component",
            REMOVE_COMPONENT: "remove-component", SET_PROPERTY: "set-property", SAVE_SCENE: "save-scene"}
CAPABILITY_IDS = tuple(COMMANDS)
READ_ONLY = (INSPECT_OBJECT, COMPONENT_TYPES, PROPERTIES)

LIMITATION = ("Editor Scene state only: SUCCESS means the Unity Editor completed this operation on the open Scene. It "
              "is not evidence of gameplay, rendering, build or target behaviour, and project code (OnValidate, "
              "component and save callbacks, ExecuteAlways scripts) may have run as a consequence.")

GLOBAL_ID = re.compile(r"^GlobalObjectId_V1-(\d+)-([0-9a-f]{32})-(\d{1,20})-(\d{1,20})$")
ZERO_GUID = "0" * 32
TOKEN = re.compile(r"^[0-9a-f]{32}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
SCENE_PATH = re.compile(r'^Assets/[^\x00-\x1f\x7f\\:*?"<>|]+\.unity$')
TYPE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,128}::[A-Za-z_][A-Za-z0-9_.+]{0,255}$")
PROPERTY_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,5}$")
DECIMAL = re.compile(r"^-?(0|[1-9][0-9]{0,19})$")
MAX_INPUT = 8192
MAX_NAME = 128
MAX_STRING = 4096
MAX_SCENE_PATH = 512
MAX_JSON_DEPTH = 4
FLOAT32_MAX = 3.4028234663852886e38
QUATERNION_TOLERANCE = 1e-4
KINDS = ("bool", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64", "float32", "float64",
         "string", "enum", "vector2", "vector3", "vector4", "vector2int", "vector3int", "rect", "rectint", "bounds",
         "boundsint", "color", "quaternion", "layermask", "object")
INT_RANGES = {"int8": (-2 ** 7, 2 ** 7 - 1), "int16": (-2 ** 15, 2 ** 15 - 1), "int32": (-2 ** 31, 2 ** 31 - 1),
              "uint8": (0, 2 ** 8 - 1), "uint16": (0, 2 ** 16 - 1), "uint32": (0, 2 ** 32 - 1),
              "layermask": (0, 2 ** 32 - 1)}


class InputProblem(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- input grammar

def _text(name, value):
    if not isinstance(value, str):
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} must be text")
    if len(value) > MAX_INPUT:
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is longer than {MAX_INPUT} characters")
    return value


def object_id(name, value):
    value = _text(name, value)
    m = GLOBAL_ID.fullmatch(value)
    if not m:
        raise InputProblem("LIVE_OBJECT_REFUSED", f"{name} is a GlobalObjectId_V1-2-<scene guid>-<file id>-<prefab id> "
                                                  f"string")
    if m.group(2) == ZERO_GUID:
        raise InputProblem("LIVE_SCENE_NOT_SAVED", f"{name} has no stable id: its Scene was never saved; GPOS never "
                                                   f"chooses a path for it")
    if m.group(1) != "2":
        raise InputProblem("LIVE_OBJECT_REFUSED", f"{name}: only Scene objects (GlobalObjectId type 2) are authored")
    if int(m.group(3)) >= 2 ** 64 or int(m.group(4)) >= 2 ** 64:
        raise InputProblem("LIVE_OBJECT_REFUSED", f"{name}: the id's numbers are out of range")
    return value


def scene_path(name, value):
    value = _text(name, value)
    if value == "":
        raise InputProblem("LIVE_SCENE_NOT_SAVED", "an unsaved Scene has no path; GPOS never chooses one (no Save As, "
                                                   "no dialog)")
    if (len(value) > MAX_SCENE_PATH or not SCENE_PATH.fullmatch(value) or "/../" in value or "/./" in value
            or "//" in value):
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is a Scene path under Assets/ ending in .unity")
    return value


def token(name, value):
    if not TOKEN.fullmatch(_text(name, value)):
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is a 32-hex token from an inspection")
    return value


def digest(name, value):
    if not DIGEST.fullmatch(_text(name, value)):
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is the 64-hex catalog digest")
    return value


def name_text(name, value):
    value = _text(name, value)
    if not 1 <= len(value) <= MAX_NAME or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        raise InputProblem("LIVE_VALUE_INVALID", f"{name} has 1 to {MAX_NAME} characters and no control characters")
    return value


def tag_text(name, value):
    value = _text(name, value)
    if not 1 <= len(value) <= 64 or any(ord(c) < 0x20 for c in value):
        raise InputProblem("LIVE_VALUE_INVALID", f"{name} is a tag name")
    return value


def boolean(name, value):
    value = _text(name, value)
    if value not in ("true", "false"):
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is true or false")
    return value == "true"


def whole(low, high):
    def parse(name, value):
        value = _text(name, value)
        if not re.fullmatch(r"-?(0|[1-9][0-9]{0,9})", value) or not low <= int(value) <= high:
            raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is a whole number from {low} to {high}")
        return int(value)
    return parse


def strict_json(name, value):
    value = _text(name, value)

    def refuse(token_):
        raise ValueError(f"{token_} is not JSON")

    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError("duplicate key")
            out[k] = v
        return out
    try:
        parsed = json.loads(value, object_pairs_hook=unique, parse_constant=refuse)
    except ValueError as exc:
        raise InputProblem("LIVE_VALUE_INVALID", f"{name} is not strict JSON ({exc})") from None
    _bounded(name, parsed, 0)
    return parsed


def _bounded(name, value, depth):
    if depth > MAX_JSON_DEPTH:
        raise InputProblem("LIVE_VALUE_INVALID", f"{name} nests deeper than {MAX_JSON_DEPTH}")
    if isinstance(value, (list, dict)):
        for v in (value.values() if isinstance(value, dict) else value):
            _bounded(name, v, depth + 1)


def _float32(name, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > FLOAT32_MAX:
        raise InputProblem("LIVE_VALUE_INVALID", f"{name}: every component is a finite 32-bit float")
    return v


def _floats(name, value, count):
    if not isinstance(value, list) or len(value) != count:
        raise InputProblem("LIVE_VALUE_INVALID", f"{name} is an array of {count} numbers")
    return [_float32(name, v) for v in value]


def _ints(name, value, count, low=-2 ** 31, high=2 ** 31 - 1):
    if not isinstance(value, list) or len(value) != count or any(
            isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high for v in value):
        raise InputProblem("LIVE_VALUE_INVALID", f"{name} is an array of {count} 32-bit integers")
    return value


def _unit_quaternion(name, value):
    q = _floats(name, value, 4)
    if abs(math.sqrt(sum(float(x) * float(x) for x in q)) - 1.0) > QUATERNION_TOLERANCE:
        raise InputProblem("LIVE_VALUE_INVALID", f"{name} is a unit quaternion [x, y, z, w]")
    return q


def vector3(name, value):
    return _floats(name, strict_json(name, value), 3)


def rotation(name, value):
    return _unit_quaternion(name, strict_json(name, value))


def static_flags(name, value):
    value = _text(name, value)
    if not re.fullmatch(r"0|[1-9][0-9]{0,9}", value) or int(value) > 2 ** 31 - 1:
        raise InputProblem("LIVE_VALUE_INVALID", f"{name} is a non-negative combination of StaticEditorFlags bits")
    return int(value)


def kind(name, value):
    if _text(name, value) not in KINDS:
        raise InputProblem("LIVE_PROPERTY_UNSUPPORTED", f"{name} is one of {', '.join(KINDS)}")
    return value


def property_path(name, value):
    if not PROPERTY_PATH.fullmatch(_text(name, value)) or len(value) > 256:
        raise InputProblem("LIVE_PROPERTY_UNSUPPORTED", f"{name} is a serialized property path such as `speed` or "
                                                        f"`settings.speed` (no arrays)")
    return value


def path_prefix(name, value):
    value = _text(name, value)
    if len(value) > 256 or any(ord(c) < 0x20 for c in value):
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is a property path prefix")
    return value


def type_id(name, value):
    if not TYPE_ID.fullmatch(_text(name, value)):
        raise InputProblem("LIVE_TYPE_NOT_IN_CATALOG", f"{name} is a catalog type id <assembly>::<full name>")
    return value


def query(name, value):
    value = _text(name, value)
    if len(value) > 64 or any(ord(c) < 0x20 for c in value):
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} has at most 64 printable characters")
    return value


def optional_parent(name, value):
    return None if _text(name, value) == "" else object_id(name, value)


def property_value(kind_, value):
    """The JSON value for `kind_`, validated exactly as the bridge validates it (the bridge checks again)."""
    name = "value"
    if kind_ == "bool":
        if not isinstance(value, bool):
            raise InputProblem("LIVE_VALUE_INVALID", "value is true or false")
    elif kind_ in INT_RANGES:
        low, high = INT_RANGES[kind_]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise InputProblem("LIVE_VALUE_INVALID", f"value is a whole number from {low} to {high}")
    elif kind_ in ("int64", "uint64"):
        low, high = (-2 ** 63, 2 ** 63 - 1) if kind_ == "int64" else (0, 2 ** 64 - 1)
        if not isinstance(value, str) or not DECIMAL.fullmatch(value) or not low <= int(value) <= high:
            raise InputProblem("LIVE_VALUE_INVALID", f"value is a decimal string from {low} to {high}")
    elif kind_ == "float32":
        _float32(name, value)
    elif kind_ == "float64":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise InputProblem("LIVE_VALUE_INVALID", "value is a finite number")
    elif kind_ == "string":
        if not isinstance(value, str) or len(value) > MAX_STRING:
            raise InputProblem("LIVE_VALUE_INVALID", f"value is a string of at most {MAX_STRING} characters")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise InputProblem("LIVE_VALUE_INVALID", "value holds an unpaired surrogate") from None
    elif kind_ == "enum":
        if not isinstance(value, str) or not 1 <= len(value) <= 256:
            raise InputProblem("LIVE_VALUE_INVALID", "value is exactly one of the enum's names")
    elif kind_ in ("vector2", "vector3", "vector4", "rect", "color"):
        _floats(name, value, {"vector2": 2, "vector3": 3}.get(kind_, 4))
    elif kind_ == "quaternion":
        _unit_quaternion(name, value)
    elif kind_ in ("vector2int", "vector3int", "rectint"):
        _ints(name, value, {"vector2int": 2, "vector3int": 3, "rectint": 4}[kind_])
    elif kind_ == "bounds":
        if not isinstance(value, list) or len(value) != 2:
            raise InputProblem("LIVE_VALUE_INVALID", "value is [[center x, y, z], [extents x, y, z]]")
        _floats(name, value[0], 3)
        if any(e < 0 for e in _floats(name, value[1], 3)):
            raise InputProblem("LIVE_VALUE_INVALID", "bounds extents are not negative")
    elif kind_ == "boundsint":
        if not isinstance(value, list) or len(value) != 2:
            raise InputProblem("LIVE_VALUE_INVALID", "value is [[position x, y, z], [size x, y, z]]")
        _ints(name, value[0], 3)
        _ints(name, value[1], 3)
    elif kind_ == "object":
        if value is not None:
            if not isinstance(value, str):
                raise InputProblem("LIVE_VALUE_INVALID", "value is null or a Scene object id")
            object_id(name, value)
    return value


# Each capability's inputs (besides unity_project): name -> parser. The bridge receives every name, None when absent.
INPUTS = {
    INSPECT_OBJECT: {"object": object_id, "scene": scene_path, "children_limit": whole(0, 200)},
    COMPONENT_TYPES: {"query": query, "page": whole(0, 10000)},
    PROPERTIES: {"component": object_id, "path_prefix": path_prefix, "page": whole(0, 10000)},
    CREATE: {"scene": scene_path, "name": name_text, "parent": object_id, "sibling": whole(0, 1_000_000),
             "local_position": vector3, "local_rotation": rotation, "local_scale": vector3,
             "expected_parent_token": token, "expected_scene_roots_token": token},
    DELETE: {"object": object_id, "expected_subtree_token": token},
    SET_PARENT: {"object": object_id, "parent": optional_parent, "keep_world": boolean, "sibling": whole(0, 1_000_000),
                 "expected_object_token": token, "expected_transform_token": token,
                 "expected_old_parent_token": token, "expected_old_scene_roots_token": token,
                 "expected_new_parent_token": token, "expected_new_scene_roots_token": token,
                 "expected_transform_chain_token": token, "expected_new_parent_chain_token": token},
    SET_GAMEOBJECT: {"object": object_id, "name": name_text, "active": boolean, "tag": tag_text,
                     "layer": whole(0, 31), "static_flags": static_flags, "expected_object_token": token},
    SET_TRANSFORM: {"object": object_id, "local_position": vector3, "local_rotation": rotation,
                    "local_scale": vector3, "expected_transform_token": token},
    ADD_COMPONENT: {"object": object_id, "type_id": type_id, "expected_object_token": token,
                    "expected_catalog_digest": digest},
    REMOVE_COMPONENT: {"component": object_id, "expected_component_token": token, "expected_object_token": token},
    SET_PROPERTY: {"component": object_id, "path": property_path, "kind": kind, "value": strict_json,
                   "expected_component_token": token},
    SAVE_SCENE: {"scene": scene_path},
}
REQUIRED = {
    INSPECT_OBJECT: (), COMPONENT_TYPES: (), PROPERTIES: ("component",),
    CREATE: ("scene", "name"), DELETE: ("object", "expected_subtree_token"),
    SET_PARENT: ("object", "parent", "keep_world", "expected_object_token", "expected_transform_token"),
    SET_GAMEOBJECT: ("object", "expected_object_token"), SET_TRANSFORM: ("object", "expected_transform_token"),
    ADD_COMPONENT: ("object", "type_id", "expected_object_token", "expected_catalog_digest"),
    REMOVE_COMPONENT: ("component", "expected_component_token", "expected_object_token"),
    SET_PROPERTY: ("component", "path", "kind", "value", "expected_component_token"),
    SAVE_SCENE: ("scene",),
}


def input_kinds(cap):
    return ("unity_project",) + tuple(INPUTS[cap])


def _one_of(args, a, b, why):
    if (args[a] is None) == (args[b] is None):
        raise InputProblem("INVALID_TOOL_REQUEST", f"exactly one of {a} and {b} is required {why}")


def _forbidden(args, key, why):
    if args[key] is not None:
        raise InputProblem("INVALID_TOOL_REQUEST", f"{key} is not accepted {why}")


def parse_inputs(cap, inputs):
    """The bridge arguments for `cap` (every key, None when absent), or InputProblem."""
    inputs = dict(inputs or {})
    inputs.pop("unity_project", None)
    missing = [k for k in REQUIRED[cap] if k not in inputs]
    if missing:
        raise InputProblem("INVALID_TOOL_REQUEST", f"{cap} requires {', '.join(missing)}")
    args = {k: None for k in INPUTS[cap]}
    for k, v in inputs.items():
        args[k] = INPUTS[cap][k](k, v)
    if cap == INSPECT_OBJECT:
        _one_of(args, "object", "scene", "(a GameObject or a Scene)")
    elif cap == CREATE:
        if args["parent"] is not None:
            if args["expected_parent_token"] is None:
                raise InputProblem("INVALID_TOOL_REQUEST", "expected_parent_token (the parent's object token) is "
                                                           "required with a parent")
            _forbidden(args, "expected_scene_roots_token", "with a parent")
        else:
            if args["expected_scene_roots_token"] is None:
                raise InputProblem("INVALID_TOOL_REQUEST", "expected_scene_roots_token is required at the Scene root")
            _forbidden(args, "expected_parent_token", "at the Scene root")
    elif cap == SET_PARENT:
        _one_of(args, "expected_old_parent_token", "expected_old_scene_roots_token", "(the object's current place)")
        if args["parent"] is not None:
            if args["expected_new_parent_token"] is None:
                raise InputProblem("INVALID_TOOL_REQUEST", "expected_new_parent_token is required with a new parent")
            _forbidden(args, "expected_new_scene_roots_token", "with a new parent")
        else:
            if args["expected_new_scene_roots_token"] is None:
                raise InputProblem("INVALID_TOOL_REQUEST", "expected_new_scene_roots_token is required when the new "
                                                           "parent is the Scene root (parent=\"\")")
            _forbidden(args, "expected_new_parent_token", "when the new parent is the Scene root")
        if args["keep_world"]:
            if args["expected_transform_chain_token"] is None:
                raise InputProblem("INVALID_TOOL_REQUEST", "keep_world=true requires expected_transform_chain_token")
            if args["parent"] is not None and args["expected_new_parent_chain_token"] is None:
                raise InputProblem("INVALID_TOOL_REQUEST", "keep_world=true requires the new parent's "
                                                           "expected_new_parent_chain_token")
            if args["parent"] is None:
                _forbidden(args, "expected_new_parent_chain_token", "when the new parent is the Scene root")
        else:
            _forbidden(args, "expected_transform_chain_token", "with keep_world=false")
            _forbidden(args, "expected_new_parent_chain_token", "with keep_world=false")
    elif cap == SET_GAMEOBJECT:
        if all(args[k] is None for k in ("name", "active", "tag", "layer", "static_flags")):
            raise InputProblem("INVALID_TOOL_REQUEST", "at least one of name, active, tag, layer and static_flags")
    elif cap == SET_TRANSFORM:
        if all(args[k] is None for k in ("local_position", "local_rotation", "local_scale")):
            raise InputProblem("INVALID_TOOL_REQUEST", "at least one of local_position, local_rotation and "
                                                       "local_scale")
    elif cap == SET_PROPERTY:
        args["value"] = property_value(args["kind"], args["value"])
    return args


# ---------------------------------------------------------------- execution

def execute(request, context, facts=None, sleep=None, monotonic=None):
    kw = {k: v for k, v in (("facts", facts), ("sleep", sleep), ("monotonic", monotonic)) if v is not None}
    cap = request.capability_id
    try:
        args = parse_inputs(cap, request.inputs)
    except InputProblem as problem:
        return AdapterOutcome(ok=True, diagnostics=(lv._diag(problem.code, str(problem), cap),))
    try:
        live = lv.Live(request, context, **kw)
        return _run(live, cap, args)
    except lv.Refused as refused:
        return refused.outcome


def _run(live, cap, args):
    record = live.context.session
    sid = (record.get("session") or {}).get("session_id")
    cls, reasons, _, state = live.classify(record)
    if cls != ls.LIVE:
        code = "LIVE_SESSION_UNRESPONSIVE" if cls == ls.UNRESPONSIVE else "LIVE_SESSION_STALE"
        return lv._refuse(cap, code, f"session {sid} is {cls}", {"reasons": reasons})
    manifest = live.require_installed()
    b = live.bridge(manifest, state)
    r = live.call(live.channel(), COMMANDS[cap], args, b["boot_id"], sid, wait=float(live.context.timeout))
    return outcome(cap, r)


def outcome(cap, r):
    """The AdapterOutcome of one authoring call. A mutation that began — even one the bridge reverted — reports
    mutation_performed; one refused before any change does not."""
    mutating = cap not in READ_ONLY
    what = COMMANDS[cap]
    if r.outcome == ipc.WITHDRAWN:
        return lv._refuse(cap, "LIVE_REQUEST_WITHDRAWN", f"{what}: the Editor did not claim the request in time; it "
                                                         f"was withdrawn and never executed")
    if r.outcome == ipc.UNKNOWN or r.status == "INTERRUPTED":
        return lv._refuse(cap, "LIVE_OUTCOME_UNKNOWN", f"{what}: the Editor claimed the request but its final effect "
                                                       f"is unknown; it is not retried — inspect the Scene again",
                          {"request_id": r.request_id}, mutation_performed=mutating)
    resp = r.response
    data = resp.get("data")
    details = {"bridge_code": resp["code"], "request_id": r.request_id}
    if resp["status"] == "OK":
        if not isinstance(data, dict):
            return lv._refuse(cap, "LIVE_PROTOCOL_ERROR", f"{what}: the bridge answered without data",
                              details, mutation_performed=mutating)
        out = dict(data, limitation=LIMITATION) if mutating else data
        diags = (lv._diag("LIVE_SCENE_SAVED", f"saved {data.get('scene', {}).get('path')!r} to its own path", cap),) \
            if cap == SAVE_SCENE else ()
        return AdapterOutcome(ok=True, mutation_performed=mutating, data=out, diagnostics=diags)
    started = isinstance(data, dict) and data.get("mutation_started") is True
    if resp["status"] == "REFUSED":
        code = lv.REFUSALS.get(resp["code"], "LIVE_PROTOCOL_ERROR")
        return lv._refuse(cap, code, f"{what}: {resp.get('message') or resp['code']}", details,
                          mutation_performed=mutating and started, data=data if isinstance(data, dict) else None)
    code = lv.AUTHORING_FAILURES.get(resp["code"], "LIVE_PROTOCOL_ERROR")
    unknown_start = not (isinstance(data, dict) and data.get("mutation_started") is False)
    return lv._refuse(cap, code, f"{what}: {resp.get('message') or resp['code']}", details,
                      mutation_performed=mutating and unknown_start, data=data if isinstance(data, dict) else None)
