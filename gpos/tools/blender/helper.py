"""The fixed, audited GPOS Blender helper (Phase 2C-4). It runs INSIDE Blender, started only as

    Blender --background --factory-startup --disable-autoexec --offline-mode -noaudio
            --log-file <isolated root>/blender.log --python-exit-code 71 --python <this file>
            -- <mode> <nonce> [<arguments>]

by `gpos/tools/blender/adapter.py`. It is part of the GPOS implementation, never caller input: no
caller supplies Python, an operator, an expression or a path to another script.

Modes:
    selftest <nonce>                                   the probe's compatibility check; opens no file
    inspect  <nonce> <blend>                           bounded metadata of one .blend; never renders or saves
    render   <nonce> <blend> <output> <scene> <frame>  one still of the authored scene, camera and frame

The .blend is opened explicitly, after Blender has started from factory settings, with
`bpy.ops.wm.open_mainfile(load_ui=False, use_scripts=False)`: embedded scripts (registered text blocks,
Python drivers) do not run, in addition to `--disable-autoexec`. Before a render the helper refuses every
source whose render could run embedded code (Freestyle, which the Blender manual says runs scripts even
with auto-execution off; OSL script nodes), read files that were not supplied (external dependencies), or
write more than the one output (compositor File Output nodes, multi-view, sequencer strips). It also
refuses to render when Blender reports that it blocked source Python for this file (`bpy.app.autoexec_fail`:
a registered text block, or a driver that Blender's restricted driver evaluator would not run): the loaded
scene may then differ from what its author sees, and GPOS never runs that Python. It never
saves, exports, imports, packs, creates or moves anything, and never generates a camera or changes the
authored render settings; only the output format and path are set, in memory, for the one PNG.

A scene whose authored render engine is not available (an add-on engine) is shown by Blender's Python API
as its fallback engine, so the helper reads Blender's own load report from the log file Blender writes for
this execution (`Engine '<id>' not available for scene '<name>'`): such a scene is reported with its authored
engine and is never rendered with a substitute. That report is trusted only when the whole log is known:
a log larger than LOG_LIMIT is refused rather than read in part.

A path counts as Blender's own only when the file it actually resolves to lies inside the installation's
resolved datafiles directory; the stored path text is never trusted.

It emits exactly one machine record on stdout, `GPOS_BLENDER_RESULT_V1:<nonce>:<json>`, with ASCII-only
JSON on one line. It reports counts and names, never file paths.
"""

import json
import os
import re
import sys

import bpy

PREFIX = "GPOS_BLENDER_RESULT_V1:"
ENGINE_CANDIDATES = ("BLENDER_EEVEE", "BLENDER_WORKBENCH", "CYCLES")  # built-in, verified on Blender 5.2
MAX_SCENES = 64
MAX_NAME = 256
MAX_EDGE = 4096
MAX_PIXELS = 16_777_216
OBJECT_GROUPS = ("MESH", "ARMATURE", "CAMERA", "LIGHT", "EMPTY")
LOG_LIMIT = 256 * 1024                # Blender's load log: a normal one is well under 1 KiB
UNAVAILABLE_ENGINE = re.compile(r"Engine '([^'\r\n]{1,64})' not available for scene '([^\r\n]*)' \(an add-on")


class Refusal(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def emit(nonce, record):
    sys.stdout.write(PREFIX + nonce + ":" + json.dumps(record, sort_keys=True, ensure_ascii=True,
                                                      separators=(",", ":")) + "\n")
    sys.stdout.flush()


def name(value):
    """A datablock name as reported: bounded, or the whole inspection fails closed."""
    if value is None:
        return None
    if len(value) > MAX_NAME:
        raise Refusal("NAME_LIMIT", f"a name is longer than {MAX_NAME} characters")
    return value


def open_blend(path):
    try:
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
    except RuntimeError:
        raise Refusal("OPEN_FAILED", "the input could not be opened as a .blend file") from None


def unavailable_engines():
    """{scene name: authored engine id} for scenes whose engine Blender reported as unavailable at load, read
    from the log file Blender writes for this execution (`--log-file`, before `--`). An unreadable log is
    refused: the engine could not be established."""
    args = sys.argv[:sys.argv.index("--")]
    if "--log-file" not in args:
        raise Refusal("ENGINE_UNSUPPORTED", "Blender's load log is not available, so the render engine cannot be "
                                           "established")
    try:
        with open(args[args.index("--log-file") + 1], "rb") as log:
            data = log.read(LOG_LIMIT + 1)
    except OSError:
        raise Refusal("ENGINE_UNSUPPORTED", "Blender's load log could not be read") from None
    if len(data) > LOG_LIMIT:
        raise Refusal("ENGINE_LOG_UNBOUNDED", f"Blender's load log is larger than {LOG_LIMIT} bytes, so the complete "
                                              f"render-engine state cannot be established")
    text = data.decode("utf-8", errors="replace")
    found = {m.group(2): m.group(1) for m in UNAVAILABLE_ENGINE.finditer(text)}
    if "not available for scene" in text and not found:
        raise Refusal("ENGINE_UNSUPPORTED", "Blender reported an unavailable render engine that could not be read")
    return found


def settable_engines():
    scene = bpy.context.scene
    authored, found = scene.render.engine, []
    for engine in ENGINE_CANDIDATES:
        try:
            scene.render.engine = engine
            found.append(engine)
        except TypeError:
            continue
    scene.render.engine = authored
    return found


def resolved(raw):
    """The real file-system path a stored path refers to from the loaded .blend: `//` expanded, then every
    `..` and symbolic link resolved."""
    return os.path.realpath(bpy.path.abspath(raw))


def inside(path, root):
    """True when the resolved `path` is `root` or lies inside it, with the platform's path semantics (case on
    Windows, drives). Both must already be resolved; only whole path components are compared."""
    path, root = os.path.normcase(path), os.path.normcase(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:                  # different drives, or mixed absolute and relative
        return False


def blender_owned(real, system):
    """True only for a bundled resource actually present in this installation: the resolved path lies inside
    Blender's resolved datafiles directory AND is an existing regular file (the bundled resources observed on
    Blender 5.2 are asset-library .blend files). A missing path there is an ordinary, missing dependency."""
    return inside(real, system) and os.path.isfile(real)


def external_dependencies():
    """(count, missing) of distinct external files the loaded .blend references.

    The union of every path Blender itself reports (`bpy.utils.blend_paths`, which covers every path-bearing
    data type of this version) and an explicit list of render-relevant ones: linked libraries and file-backed,
    unpacked images, fonts, sounds, movie clips, cache files and volumes. Only paths into Blender's own
    installation are excluded (`blender_owned`). The stored text never decides."""
    system = os.path.realpath(bpy.utils.system_resource("DATAFILES"))
    raw = set(bpy.utils.blend_paths(absolute=False, packed=False, local=False))
    raw.update(lib.filepath for lib in bpy.data.libraries)
    raw.update(img.filepath for img in bpy.data.images
               if img.source in {"FILE", "SEQUENCE", "MOVIE", "TILED"} and not img.packed_file)
    raw.update(f.filepath for f in bpy.data.fonts if f.filepath != "<builtin>" and not f.packed_file)
    for collection in (bpy.data.sounds, bpy.data.volumes):
        raw.update(item.filepath for item in collection if not item.packed_file)
    raw.update(item.filepath for item in bpy.data.movieclips)
    raw.update(item.filepath for item in bpy.data.cache_files)
    external = {real for real in (resolved(path) for path in raw if path) if not blender_owned(real, system)}
    return len(external), sum(1 for path in external if not os.path.exists(path))


def node_trees():
    trees = list(bpy.data.node_groups)
    for collection in (bpy.data.materials, bpy.data.worlds, bpy.data.lights):
        trees += [owner.node_tree for owner in collection if getattr(owner, "node_tree", None) is not None]
    return trees


def compositor_file_outputs():
    return any(node.bl_idname == "CompositorNodeOutputFile"
               for tree in bpy.data.node_groups if tree.bl_idname == "CompositorNodeTree" for node in tree.nodes)


def script_nodes():
    return any(node.bl_idname == "ShaderNodeScript" for tree in node_trees() for node in tree.nodes)


def require_no_blocked_python(when):
    """Refuse when Blender reports that it blocked source Python (a registered text block, or a driver its
    restricted evaluator refused). Blender sets the flag itself; it is read-only, and GPOS never clears it."""
    if bpy.app.autoexec_fail:
        raise Refusal("AUTOEXEC_REQUIRED", f"Blender blocked Python embedded in the file ({when}); the scene may not "
                                            f"be what its author sees, and GPOS never runs source Python")


def effective_resolution(scene):
    r = scene.render
    return int(r.resolution_x * r.resolution_percentage / 100), int(r.resolution_y * r.resolution_percentage / 100)


def inspect():
    scenes = list(bpy.data.scenes)
    if len(scenes) > MAX_SCENES:
        raise Refusal("SCENE_LIMIT", f"the file has more than {MAX_SCENES} scenes")
    counts = {group: 0 for group in OBJECT_GROUPS}
    counts["OTHER"] = 0
    for ob in bpy.data.objects:
        counts[ob.type if ob.type in OBJECT_GROUPS else "OTHER"] += 1
    meshes = list(bpy.data.meshes)
    count, missing = external_dependencies()
    unavailable = unavailable_engines()
    version = bpy.data.version
    return {
        "blend_file_version": f"{version[0]}.{version[1]}.{version[2]}",
        "active_scene": name(bpy.context.scene.name),
        "scene_count": len(scenes),
        "scenes": [{
            "name": name(sc.name),
            "camera": name(sc.camera.name) if sc.camera else None,
            "frame_start": sc.frame_start, "frame_end": sc.frame_end, "current_frame": sc.frame_current,
            "render_engine": unavailable.get(sc.name, sc.render.engine),
            "render_engine_available": sc.name not in unavailable,
            "resolution": {"x": sc.render.resolution_x, "y": sc.render.resolution_y,
                           "percentage": sc.render.resolution_percentage},
            "freestyle": bool(sc.render.use_freestyle),
        } for sc in scenes],
        "object_counts": counts,
        "mesh_datablocks": len(meshes),
        "total_vertices": sum(len(me.vertices) for me in meshes),
        "total_edges": sum(len(me.edges) for me in meshes),
        "total_polygons": sum(len(me.polygons) for me in meshes),
        "material_count": len(bpy.data.materials),
        "image_count": len(bpy.data.images),
        "armature_count": len(bpy.data.armatures),
        "action_count": len(bpy.data.actions),
        "external_dependencies": {"count": count, "missing": missing},
        "has_compositor_file_outputs": compositor_file_outputs(),
        "has_script_nodes": script_nodes(),
    }


def render(output, scene_name, frame_text):
    if scene_name:
        scene = bpy.data.scenes.get(scene_name)
        if scene is None or scene.name != scene_name:
            raise Refusal("SCENE_NOT_FOUND", "the requested scene does not exist (names match exactly)")
    else:
        scene = bpy.context.scene
    name(scene.name)
    if scene.camera is None:
        raise Refusal("NO_CAMERA", "the scene has no authored camera; none is generated")
    frame = int(frame_text) if frame_text else scene.frame_current
    if not scene.frame_start <= frame <= scene.frame_end:
        raise Refusal("FRAME_OUT_OF_RANGE", f"frame {frame} is outside the scene's range "
                                             f"{scene.frame_start}..{scene.frame_end}; it is never clamped")
    unavailable = unavailable_engines()
    if unavailable:
        authored = unavailable.get(scene.name) or sorted(unavailable.values())[0]
        raise Refusal("ENGINE_UNSUPPORTED", f"render engine {authored[:64]!r} is not available in this Blender; it is "
                                              f"never replaced by another engine")
    engine = scene.render.engine
    if engine not in ENGINE_CANDIDATES or engine not in settable_engines():
        raise Refusal("ENGINE_UNSUPPORTED", f"render engine {engine[:64]!r} is not a supported built-in engine; "
                                              f"it is never replaced")
    width, height = effective_resolution(scene)
    if not (1 <= width <= MAX_EDGE and 1 <= height <= MAX_EDGE) or width * height > MAX_PIXELS:
        raise Refusal("RESOLUTION_OUT_OF_BOUNDS", f"the authored render size {width}x{height} exceeds "
                                                    f"{MAX_EDGE}x{MAX_EDGE} or {MAX_PIXELS} pixels; the asset "
                                                    f"owner must change the scene, it is never downscaled")
    count, _ = external_dependencies()
    if count:
        raise Refusal("EXTERNAL_DEPENDENCIES", f"the file references {count} external file(s); only "
                                                 f"self-contained .blend sources are rendered as evidence")
    require_no_blocked_python("at load")
    if scene.render.use_freestyle:
        raise Refusal("RENDER_SCRIPTING", "the scene renders with Freestyle, which can run scripts during "
                                           "rendering even with auto-execution off")
    if script_nodes() or (engine == "CYCLES" and getattr(scene.cycles, "shading_system", False)):
        raise Refusal("RENDER_SCRIPTING", "the file uses Open Shading Language script nodes")
    if compositor_file_outputs():
        raise Refusal("COMPOSITOR_FILE_OUTPUT", "the file has a compositor File Output node, which would "
                                                 "write files besides the one evidence image")
    if scene.render.use_multiview:
        raise Refusal("MULTIVIEW", "the scene renders multiple views, which writes more than one image")
    editor = scene.sequence_editor
    strips = (editor.strips_all if hasattr(editor, "strips_all") else editor.sequences_all) if editor else ()
    if scene.render.use_sequencer and len(strips):
        raise Refusal("SEQUENCER_STRIPS", "the scene renders sequencer strips; only the 3D scene is supported")
    if scene.render.use_stamp:
        raise Refusal("STAMP_BURN_IN", "the scene burns metadata (such as the file name or date) into the image; it is "
                                       "not changed, so the render is refused")
    for flag in scene.render.bl_rna.properties:        # metadata-only while burn-in is off: pixels are unaffected
        if flag.identifier.startswith("use_stamp_"):
            setattr(scene.render, flag.identifier, False)
    settings = scene.render.image_settings                  # in memory only: never saved
    settings.file_format = "PNG"
    settings.color_mode = "RGBA"
    settings.color_depth = "8"
    settings.compression = 15
    scene.render.filepath = output
    scene.render.use_file_extension = False
    scene.frame_set(frame)
    require_no_blocked_python("at the frame")
    result = bpy.ops.render.render(write_still=True, scene=scene.name)
    if "FINISHED" not in result:
        raise Refusal("RENDER_FAILED", "Blender did not finish the render")
    require_no_blocked_python("while rendering")     # the image exists but is refused: never evidence
    return {"scene": scene.name, "frame": frame, "camera": name(scene.camera.name), "engine": engine,
            "width": width, "height": height}


def selftest():
    wm = bpy.ops.wm.open_mainfile.get_rna_type().properties
    rr = bpy.ops.render.render.get_rna_type().properties
    return {
        "blender_version": ".".join(str(part) for part in bpy.app.version),
        "engines": settable_engines(),
        "open_mainfile": {"use_scripts": "use_scripts" in wm, "load_ui": "load_ui" in wm},
        "render": {"write_still": "write_still" in rr, "scene": "scene" in rr},
        "blend_paths": hasattr(bpy.utils, "blend_paths"),
        "autoexec_fail": hasattr(bpy.app, "autoexec_fail"),
        "factory_startup": bool(bpy.app.factory_startup),
        "autoexec_enabled": bool(bpy.context.preferences.filepaths.use_scripts_auto_execute),
        "online_access": bool(bpy.app.online_access),
    }


def main(argv):
    mode, nonce, args = argv[0], argv[1], argv[2:]
    try:
        if mode == "selftest" and not args:
            record = dict(selftest(), status="ok", mode=mode)
        elif mode == "inspect" and len(args) == 1:
            open_blend(args[0])
            record = dict(inspect(), status="ok", mode=mode)
        elif mode == "render" and len(args) == 4:
            open_blend(args[0])
            record = dict(render(args[1], args[2], args[3]), status="ok", mode=mode)
        else:
            record = {"status": "error", "mode": "unknown", "reason_code": "USAGE"}
    except Refusal as refusal:
        record = {"status": "refused", "mode": mode, "reason_code": refusal.code, "message": refusal.message}
    emit(nonce, record)


main(sys.argv[sys.argv.index("--") + 1:])
