"""Unity live asset references and asset authoring (Phase 2C-6B2A): seven fixed capabilities through the audited
bridge (1.2.0; unchanged in 1.3.0, which adds the prefab capabilities of prefabs.py).

    unity.live-asset-types              the asset kinds, the ScriptableObject and shader catalogs    READ_ONLY
    unity.live-asset-find               typed, bounded, paged lookup in ASSETS, PACKAGE or BUILTIN    READ_ONLY
    unity.live-asset-inspect            one asset: reference facts, values, authorability, token      READ_ONLY
    unity.live-create-material          create one Material (.mat) at an exact new path               MUTATING
    unity.live-set-material-property    write one declared shader property of a Material and save it  MUTATING
    unity.live-create-scriptable-object create one ScriptableObject (.asset) of a creatable type      MUTATING
    unity.live-set-asset-property       write one allowlisted property of a ScriptableObject, save it MUTATING

Assets are named only by GlobalObjectId strings (identifier type 1, 3 or 4, prefab id 0) — never by an InstanceID,
EntityId, package-cache path or file name. Package and built-in assets are references only. A new asset is written
only at the exact path the caller names, below Assets/, into an existing folder, with the one extension of its kind;
nothing is overwritten, renamed, moved elsewhere or created as a folder, and creation is not undoable. An edit of
an existing asset carries the asset token of an earlier inspection and is saved to that one asset's file only after
the bridge has refused a dirty asset, checked version control, imported that asset, compared the token, made the
typed change in one Undo group, read it back and re-checked the file and its .meta on disk; from the targeted import
on, a refusal still reports mutation_performed. Uncertain persistence is OUTCOME_UNKNOWN and never retried.
Nothing here produces evidence. Inputs are strings; structured values are strict JSON text.
"""

import dataclasses
import re

from . import authoring as au
from . import live as lv

ASSET_TYPES = "unity.live-asset-types"
ASSET_FIND = "unity.live-asset-find"
ASSET_INSPECT = "unity.live-asset-inspect"
CREATE_MATERIAL = "unity.live-create-material"
SET_MATERIAL_PROPERTY = "unity.live-set-material-property"
CREATE_SCRIPTABLE_OBJECT = "unity.live-create-scriptable-object"
SET_ASSET_PROPERTY = "unity.live-set-asset-property"
COMMANDS = {ASSET_TYPES: "asset-types", ASSET_FIND: "asset-find", ASSET_INSPECT: "asset-inspect",
            CREATE_MATERIAL: "create-material", SET_MATERIAL_PROPERTY: "set-material-property",
            CREATE_SCRIPTABLE_OBJECT: "create-scriptable-object", SET_ASSET_PROPERTY: "set-asset-property"}
CAPABILITY_IDS = tuple(COMMANDS)
READ_ONLY = (ASSET_TYPES, ASSET_FIND, ASSET_INSPECT)
CREATES = (CREATE_MATERIAL, CREATE_SCRIPTABLE_OBJECT)

LIMITATION = ("Editor asset state only: SUCCESS means the Unity Editor completed this operation and wrote this one "
              "asset file. It is not evidence of gameplay, rendering, build or target behaviour, and project code "
              "(AssetPostprocessors on import and move, ScriptableObject OnEnable and OnValidate) may have run as a "
              "consequence. A creation is not undoable; an edit stays in Unity's Undo history, where Cmd-Z restores "
              "the value in memory and leaves the asset dirty while its file keeps the saved value.")

KINDS = ("MATERIAL", "TEXTURE", "SPRITE", "AUDIO", "MESH", "PREFAB", "PREFAB_COMPONENT", "MODEL", "SCRIPTABLE_OBJECT")
FINDABLE = tuple(k for k in KINDS if k != "PREFAB_COMPONENT")
SOURCES = ("ASSETS", "PACKAGE", "BUILTIN")
CATALOGS = ("KINDS", "SCRIPTABLE_OBJECTS", "SHADERS")
MATERIAL_KINDS = ("color", "vector", "float", "range", "int", "texture")
EXTENSIONS = {CREATE_MATERIAL: ".mat", CREATE_SCRIPTABLE_OBJECT: ".asset"}
SHADER_PROPERTY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SEGMENT = re.compile(r"^[A-Za-z0-9 _().,+\-]{1,64}$")
STEM = re.compile(r"^[A-Za-z0-9_()\-]([A-Za-z0-9 _()\-]{0,62}[A-Za-z0-9_()\-])?$")
SPECIAL_FOLDERS = ("editor", "editor default resources", "streamingassets")
SCRATCH_PREFIX = "gposassettxn-"
MAX_PATH = 512
MAX_FOLDERS = 16

InputProblem = au.InputProblem


def _closed(options, code="INVALID_TOOL_REQUEST"):
    def parse(name, value):
        if au._text(name, value) not in options:
            raise InputProblem(code, f"{name} is one of {', '.join(options)}")
        return value
    return parse


def asset_path(ext):
    """A new asset's path: Assets/<existing folders>/<stem><ext>, exactly as the bridge checks it again."""
    def parse(name, value):
        value = au._text(name, value)

        def refuse(why):
            raise InputProblem("LIVE_ASSET_PATH_INVALID", f"{name}: {why}")
        if not 1 <= len(value) <= MAX_PATH:
            refuse(f"an asset path has 1 to {MAX_PATH} characters")
        if not value.startswith("Assets/"):
            refuse("assets are written only below Assets/ (never Packages/, Library/ or an absolute path)")
        if not value.endswith(ext):
            refuse(f"this kind of asset is written as a {ext} file")
        parts = value.split("/")
        if not 3 <= len(parts) <= MAX_FOLDERS + 2:
            refuse(f"an asset is written into a folder below Assets/, at most {MAX_FOLDERS} folders deep")
        for s in parts[1:-1]:
            if (not SEGMENT.fullmatch(s) or s in (".", "..") or s.startswith((".", " ")) or s.endswith((".", " "))):
                refuse(f"folder name {s!r} is not a plain folder name")
            if s.lower() in SPECIAL_FOLDERS:
                refuse(f"assets are never written into a special folder ({s})")
            if s.lower().startswith(SCRATCH_PREFIX):
                refuse("GPOS transaction scratch folders are never a destination")
        if not STEM.fullmatch(parts[-1][:-len(ext)]):
            refuse("the file name is 1 to 64 letters, digits, spaces, _ ( ) or -, not starting or ending with a space")
        return value
    return parse


def shader_property(name, value):
    if not SHADER_PROPERTY.fullmatch(au._text(name, value)):
        raise InputProblem("LIVE_PROPERTY_UNSUPPORTED", f"{name} is a shader property name such as _Color")
    return value


def material_value(kind, value):
    """The JSON value of a Material shader property of `kind`, validated as the bridge validates it (the shader's
    declared range is checked there)."""
    if kind in ("color", "vector"):
        au._floats("value", value, 4)
    elif kind in ("float", "range"):
        au._float32("value", value)
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not -2 ** 31 <= value <= 2 ** 31 - 1:
            raise InputProblem("LIVE_VALUE_INVALID", "value is a 32-bit whole number")
    elif kind == "texture":
        if value is not None:
            if not isinstance(value, str):
                raise InputProblem("LIVE_VALUE_INVALID", "value is null or a Texture asset id")
            au.asset_id("value", value)
    return value


def asset_value(kind, value):
    """A ScriptableObject property value: the Scene-authoring kinds, except that an object reference names an asset
    (an asset never references a Scene object)."""
    if kind == "object" and value is not None:
        if not isinstance(value, str):
            raise InputProblem("LIVE_VALUE_INVALID", "value is null or an asset id")
        if not au.ASSET_ID.fullmatch(value):
            raise InputProblem("LIVE_VALUE_INVALID", "an asset references assets only, never Scene objects")
        au.asset_id("value", value)
        return value
    return au.property_value(kind, value)


INPUTS = {
    ASSET_TYPES: {"catalog": _closed(CATALOGS), "query": au.query, "page": au.whole(0, 10000)},
    ASSET_FIND: {"kind": _closed(FINDABLE), "source": _closed(SOURCES), "query": au.query, "page": au.whole(0, 10000)},
    ASSET_INSPECT: {"asset": au.asset_id, "path_prefix": au.path_prefix, "page": au.whole(0, 10000)},
    CREATE_MATERIAL: {"path": asset_path(".mat"), "shader": au.asset_id, "expected_shader_catalog_digest": au.digest},
    SET_MATERIAL_PROPERTY: {"material": au.asset_id, "property": shader_property,
                            "kind": _closed(MATERIAL_KINDS, "LIVE_PROPERTY_UNSUPPORTED"), "value": au.strict_json,
                            "expected_asset_token": au.token},
    CREATE_SCRIPTABLE_OBJECT: {"path": asset_path(".asset"), "type_id": au.type_id,
                               "expected_so_catalog_digest": au.digest},
    SET_ASSET_PROPERTY: {"asset": au.asset_id, "path": au.property_path, "kind": au.kind, "value": au.strict_json,
                         "expected_asset_token": au.token},
}
REQUIRED = {
    ASSET_TYPES: ("catalog",), ASSET_FIND: ("kind", "source"), ASSET_INSPECT: ("asset",),
    CREATE_MATERIAL: tuple(INPUTS[CREATE_MATERIAL]), SET_MATERIAL_PROPERTY: tuple(INPUTS[SET_MATERIAL_PROPERTY]),
    CREATE_SCRIPTABLE_OBJECT: tuple(INPUTS[CREATE_SCRIPTABLE_OBJECT]), SET_ASSET_PROPERTY: tuple(INPUTS[SET_ASSET_PROPERTY]),
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
    if cap == ASSET_TYPES and args["catalog"] == "KINDS" and (args["query"] is not None or args["page"] not in (None, 0)):
        raise InputProblem("INVALID_TOOL_REQUEST", "the KINDS table takes no query and has one page")
    if cap == SET_MATERIAL_PROPERTY:
        args["value"] = material_value(args["kind"], args["value"])
    elif cap == SET_ASSET_PROPERTY:
        args["value"] = asset_value(args["kind"], args["value"])
    return args


# ---------------------------------------------------------------- execution

def recovered_diagnostics(cap, records):
    """One LIVE_ASSET_CREATE_RECOVERED (LIVE_PREFAB_CREATE_RECOVERED for a prefab) per earlier interrupted creation the
    bridge closed before this request. Every creating command recovers every kind of record."""
    return [lv._diag("LIVE_PREFAB_CREATE_RECOVERED" if rec.get("kind") == "PREFAB" else "LIVE_ASSET_CREATE_RECOVERED",
                     f"an interrupted creation of {rec.get('final_path')!r} was closed before this request: "
                     f"{rec.get('outcome')}", cap,
                     {"txn_id": rec.get("txn_id"), "outcome": rec.get("outcome")}) for rec in records or ()]


_recovered = recovered_diagnostics


def _success(cap, data):
    diags = _recovered(cap, data.get("recovered"))
    if cap in CREATES:
        diags.append(lv._diag("LIVE_ASSET_CREATED", f"created {(data.get('created') or {}).get('path')!r}", cap))
    elif cap not in READ_ONLY:
        diags.append(lv._diag("LIVE_ASSET_SAVED", f"saved {(data.get('asset') or {}).get('path')!r}", cap))
    return diags


def outcome(cap, r):
    result = au.outcome(cap, r, commands=COMMANDS, read_only=READ_ONLY, limitation=LIMITATION,
                        success=lambda data: _success(cap, data))
    refused = r.response is not None and r.response.get("status") != "OK"
    data = (r.response or {}).get("data")
    if refused and isinstance(data, dict) and data.get("recovered"):
        # earlier interrupted creations were closed before this request was refused: say so
        result = dataclasses.replace(result, diagnostics=tuple(result.diagnostics) + tuple(_recovered(cap, data["recovered"])))
    return result


def execute(request, context, facts=None, sleep=None, monotonic=None):
    return au.execute(request, context, facts=facts, sleep=sleep, monotonic=monotonic, parse=parse_inputs,
                      command=COMMANDS[request.capability_id], finish=outcome)
