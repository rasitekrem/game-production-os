"""Strict reading of the GPOS Blender helper's machine record and of the rendered PNG (Phase 2C-4).

Pure functions over exact bytes (the process boundary's private raw capture, or the output file). They
start no process. Everything fails closed with `HelperProtocolError`: a missing record, several records,
a record with the wrong nonce, malformed or oversized JSON, or any field outside its schema.
"""

import json
import re
import struct

PREFIX = b"GPOS_BLENDER_RESULT_V1:"
MAX_RECORD = 256 * 1024
MAX_NAME = 256
MAX_SCENES = 64
MAX_COUNT = 10 ** 9
MAX_FRAME = 1_048_574
NONCE = re.compile(r"[0-9a-f]{32}")
ENGINE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
VERSION = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4}")
REFUSAL_CODES = {"OPEN_FAILED", "NAME_LIMIT", "SCENE_LIMIT", "SCENE_NOT_FOUND", "NO_CAMERA", "FRAME_OUT_OF_RANGE",
                 "ENGINE_UNSUPPORTED", "RESOLUTION_OUT_OF_BOUNDS", "EXTERNAL_DEPENDENCIES", "RENDER_SCRIPTING",
                 "COMPOSITOR_FILE_OUTPUT", "MULTIVIEW", "SEQUENCER_STRIPS", "STAMP_BURN_IN", "RENDER_FAILED",
                 "ENGINE_LOG_UNBOUNDED", "AUTOEXEC_REQUIRED"}
OBJECT_GROUPS = {"MESH", "ARMATURE", "CAMERA", "LIGHT", "EMPTY", "OTHER"}


class HelperProtocolError(ValueError):
    """The helper's output is not exactly one complete, well-formed record; nothing may be reported."""


def record(raw, nonce):
    """The helper's one record for this execution's nonce, as a dict. Raises HelperProtocolError."""
    if not isinstance(raw, (bytes, bytearray)):
        raise HelperProtocolError("the helper output must be the exact captured bytes")
    lines = [line.rstrip(b"\r") for line in bytes(raw).split(b"\n") if line.startswith(PREFIX)]
    if len(lines) != 1:
        raise HelperProtocolError(f"expected exactly one helper record, found {len(lines)}")
    line = lines[0]
    head = PREFIX + nonce.encode("ascii") + b":"
    if not line.startswith(head):
        raise HelperProtocolError("the helper record does not carry this execution's nonce")
    body = line[len(head):]
    if len(body) > MAX_RECORD:
        raise HelperProtocolError("the helper record is larger than its bound")
    try:
        value = json.loads(body.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        raise HelperProtocolError("the helper record is not ASCII JSON") from None
    if not isinstance(value, dict):
        raise HelperProtocolError("the helper record is not an object")
    return value


def _keys(value, expected, what):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise HelperProtocolError(f"{what} does not have exactly the expected fields")


def _int(value, low, high, what):
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise HelperProtocolError(f"{what} is not an integer in range")
    return value


def _bool(value, what):
    if not isinstance(value, bool):
        raise HelperProtocolError(f"{what} is not a boolean")
    return value


def _name(value, what, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HelperProtocolError(f"{what} is not a bounded name")
    return value


def _engine(value):
    if not isinstance(value, str) or not ENGINE.fullmatch(value):
        raise HelperProtocolError("a render engine identifier is malformed")
    return value


def refusal(value):
    """(code, message) of a refusal record."""
    _keys(value, ("status", "mode", "reason_code", "message"), "a refusal record")
    if value["reason_code"] not in REFUSAL_CODES or not isinstance(value["message"], str) or len(value["message"]) > 400:
        raise HelperProtocolError("a refusal record is malformed")
    return value["reason_code"], value["message"]


def selftest(value):
    _keys(value, ("status", "mode", "blender_version", "engines", "open_mainfile", "render", "blend_paths",
                  "autoexec_fail", "factory_startup", "autoexec_enabled", "online_access"), "the selftest record")
    if not isinstance(value["blender_version"], str) or not VERSION.fullmatch(value["blender_version"]):
        raise HelperProtocolError("the selftest version is malformed")
    engines = value["engines"]
    if not isinstance(engines, list) or not engines or not all(isinstance(e, str) for e in engines):
        raise HelperProtocolError("the selftest found no supported built-in render engine")
    _keys(value["open_mainfile"], ("use_scripts", "load_ui"), "the open_mainfile capabilities")
    _keys(value["render"], ("write_still", "scene"), "the render capabilities")
    for flag in ("blend_paths", "autoexec_fail", "factory_startup", "autoexec_enabled", "online_access"):
        _bool(value[flag], flag)
    required = (value["open_mainfile"]["use_scripts"], value["open_mainfile"]["load_ui"], value["render"]["write_still"],
                value["render"]["scene"], value["blend_paths"], value["autoexec_fail"], value["factory_startup"])
    if not all(v is True for v in required) or value["autoexec_enabled"] is not False or value["online_access"] is not False:
        raise HelperProtocolError("this Blender build does not provide the fixed safety and render contract")
    return value


def inspection(value):
    """The public inspection summary (the record without its status fields). Raises HelperProtocolError."""
    fields = ("status", "mode", "blend_file_version", "active_scene", "scene_count", "scenes", "object_counts",
              "mesh_datablocks", "total_vertices", "total_edges", "total_polygons", "material_count", "image_count",
              "armature_count", "action_count", "external_dependencies", "has_compositor_file_outputs",
              "has_script_nodes")
    _keys(value, fields, "the inspection record")
    if not isinstance(value["blend_file_version"], str) or not VERSION.fullmatch(value["blend_file_version"]):
        raise HelperProtocolError("the file version is malformed")
    scenes = value["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= MAX_SCENES or value["scene_count"] != len(scenes):
        raise HelperProtocolError("the scene list is missing, oversized or inconsistent")
    for scene in scenes:
        _keys(scene, ("name", "camera", "frame_start", "frame_end", "current_frame", "render_engine",
                      "render_engine_available", "resolution", "freestyle"), "a scene")
        _bool(scene["render_engine_available"], "render_engine_available")
        _name(scene["name"], "a scene name")
        _name(scene["camera"], "a camera name", optional=True)
        _int(scene["frame_start"], 0, MAX_FRAME, "frame_start")
        _int(scene["frame_end"], 0, MAX_FRAME, "frame_end")
        _int(scene["current_frame"], -MAX_FRAME, MAX_FRAME, "current_frame")
        _engine(scene["render_engine"])
        _keys(scene["resolution"], ("x", "y", "percentage"), "a resolution")
        _int(scene["resolution"]["x"], 1, 65536, "resolution x")
        _int(scene["resolution"]["y"], 1, 65536, "resolution y")
        _int(scene["resolution"]["percentage"], 1, 32767, "resolution percentage")
        _bool(scene["freestyle"], "freestyle")
    _name(value["active_scene"], "the active scene")
    if value["active_scene"] not in {s["name"] for s in scenes}:
        raise HelperProtocolError("the active scene is not among the scenes")
    _keys(value["object_counts"], OBJECT_GROUPS, "the object counts")
    for group, count in value["object_counts"].items():
        _int(count, 0, MAX_COUNT, f"{group} count")
    for field in ("mesh_datablocks", "total_vertices", "total_edges", "total_polygons", "material_count",
                  "image_count", "armature_count", "action_count"):
        _int(value[field], 0, MAX_COUNT, field)
    _keys(value["external_dependencies"], ("count", "missing"), "the external dependencies")
    _int(value["external_dependencies"]["count"], 0, MAX_COUNT, "dependency count")
    _int(value["external_dependencies"]["missing"], 0, value["external_dependencies"]["count"], "missing dependencies")
    _bool(value["has_compositor_file_outputs"], "has_compositor_file_outputs")
    _bool(value["has_script_nodes"], "has_script_nodes")
    return {k: v for k, v in value.items() if k not in ("status", "mode")}


def rendering(value):
    _keys(value, ("status", "mode", "scene", "frame", "camera", "engine", "width", "height"), "the render record")
    _name(value["scene"], "the scene")
    _name(value["camera"], "the camera")
    _int(value["frame"], 0, MAX_FRAME, "the frame")
    _engine(value["engine"])
    _int(value["width"], 1, 4096, "the width")
    _int(value["height"], 1, 4096, "the height")
    return {k: v for k, v in value.items() if k not in ("status", "mode")}


# ---------------------------------------------------------------- the rendered PNG

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def png_dimensions(data):
    """(width, height) of a complete PNG: signature, IHDR first, IEND last. Raises HelperProtocolError."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 8 + 25 + 12 or not data.startswith(PNG_SIGNATURE):
        raise HelperProtocolError("the render is not a PNG")
    length, kind = struct.unpack(">I4s", data[8:16])
    if length != 13 or kind != b"IHDR":
        raise HelperProtocolError("the PNG does not begin with a valid IHDR chunk")
    if not bytes(data).endswith(PNG_IEND):
        raise HelperProtocolError("the PNG does not end with IEND; it is incomplete")
    return struct.unpack(">II", data[16:24])
