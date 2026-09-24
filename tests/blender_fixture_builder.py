"""TEST-ONLY: builds the Phase-2C-4 .blend fixtures inside the real Blender.

    Blender --background --factory-startup --python tests/blender_fixture_builder.py -- <out dir> <marker dir>

Run only by tests/test_blender_adapter.py, always with an isolated BLENDER_USER_RESOURCES. It uses Blender
scripting far beyond what the production adapter exposes (creating scenes, text blocks, drivers, links,
compositor nodes), which is fine for test setup and never available to a caller. No binary fixture is
committed: every .blend is generated fresh for each run.
"""

import os
import sys

import bpy

OUT, MARKERS = sys.argv[sys.argv.index("--") + 1:][:2]


def fresh(engine="BLENDER_WORKBENCH", width=64, height=48):
    """A factory scene made self-contained. Blender 5.2's factory cube material carries a weak library
    reference (a record of where it was appended from: Blender's bundled brush library, stored relative to the
    saved file), so it is replaced by a new local material instead of trusting that stored path."""
    bpy.ops.wm.read_factory_settings(use_empty=False)
    scene = bpy.context.scene
    scene.render.engine = engine
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = width, height, 100
    scene.frame_start, scene.frame_end, scene.frame_current = 1, 10, 3
    cube = bpy.data.objects["Cube"].data
    cube.materials.clear()
    for factory in [m for m in bpy.data.materials if m.library_weak_reference is not None]:
        bpy.data.materials.remove(factory)
    cube.materials.append(bpy.data.materials.new("GposMaterial"))
    return scene


def save(name):
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, name), compress=False)


def driven(expression):
    """A scene whose cube x location is driven by `expression`."""
    scene = fresh()
    curve = bpy.data.objects["Cube"].driver_add("location", 0)
    curve.driver.type = "SCRIPTED"
    curve.driver.expression = expression
    return scene


def marker_code(label):
    return "open(%r, 'w').write('ran')\n" % os.path.join(MARKERS, label)


# A. safe, self-contained: one scene, a mesh, a material, the factory camera and light, 64x48, frames 1..10
fresh()
save("safe.blend")

# the same safe fixture rendered with each built-in engine
for engine, file in (("BLENDER_EEVEE", "safe-eevee.blend"), ("CYCLES", "safe-cycles.blend")):
    scene = fresh(engine)
    if engine == "CYCLES":
        scene.cycles.samples = 4
    save(file)

# B. several scenes: "Main" (active) and "Alt" with its own camera, frames 20..30
scene = fresh()
scene.name = "Main"
alt = bpy.data.scenes.new("Alt")
alt.render.engine = "BLENDER_WORKBENCH"
alt.render.resolution_x, alt.render.resolution_y, alt.render.resolution_percentage = 40, 30, 100
alt.frame_start, alt.frame_end, alt.frame_current = 20, 30, 25
cam = bpy.data.objects.new("AltCamera", bpy.data.cameras.new("AltCamera"))
alt.collection.objects.link(cam)
alt.camera = cam
cam.location = (0, -8, 0)
cam.rotation_euler = (1.5708, 0, 0)
alt.collection.objects.link(bpy.data.objects["Cube"])
save("multi.blend")

# too many scenes
fresh()
for index in range(65):
    bpy.data.scenes.new(f"Extra{index:02d}")
save("many-scenes.blend")

# C. no camera
scene = fresh()
bpy.data.objects.remove(bpy.data.objects["Camera"])
scene.camera = None
save("no-camera.blend")

# D. an external image texture (unpacked, file-backed)
fresh()
image = bpy.data.images.new("ExternalTexture", 4, 4)
image.filepath_raw = os.path.join(OUT, "external-texture.png")
image.file_format = "PNG"
image.save()
material = bpy.data.materials["GposMaterial"]
material.use_nodes = True
node = material.node_tree.nodes.new("ShaderNodeTexImage")
node.image = image
save("external-image.blend")

# E. a linked library
fresh()
bpy.data.objects["Cube"].name = "LibraryCube"
save("library.blend")
fresh()
with bpy.data.libraries.load(os.path.join(OUT, "library.blend"), link=True) as (source, target):
    target.objects = ["LibraryCube"]
for ob in target.objects:
    bpy.context.scene.collection.objects.link(ob)
save("linked-library.blend")

# F. a compositor File Output node that would write beside the marker directory
scene = fresh()
tree = bpy.data.node_groups.new("GposCompositor", "CompositorNodeTree")
scene.compositing_node_group = tree
file_output = tree.nodes.new("CompositorNodeOutputFile")
for attribute in ("directory", "base_path"):
    if hasattr(file_output, attribute):
        setattr(file_output, attribute, os.path.join(MARKERS, "compositor-output") + os.sep)
save("compositor-output.blend")

# G. embedded code: a registered text block and a Python driver, each writing a marker if run
fresh()
text = bpy.data.texts.new("gpos_autoexec.py")
text.write(marker_code("autoexec_textblock"))
text.use_module = True
curve = bpy.data.objects["Cube"].driver_add("location", 0)
curve.driver.type = "SCRIPTED"
curve.driver.expression = "__import__('builtins').open(%r, 'w').write('x') or 0" % os.path.join(MARKERS, "autoexec_driver")
save("autoexec.blend")

# G1. each kind of blocked source Python on its own, and drivers Blender evaluates without auto-execution
fresh()
text = bpy.data.texts.new("gpos_autoexec.py")
text.write(marker_code("autoexec_text_only"))
text.use_module = True
save("autoexec-text.blend")
driven("__import__('builtins').open(%r, 'w').write('x') or 0" % os.path.join(MARKERS, "autoexec_driver_only"))
save("autoexec-driver.blend")
driven("frame * 0.5")                   # a simple expression: evaluated natively, no Python
save("simple-driver.blend")
driven("max(frame, 2) * 0.25")          # not simple, but within Blender's restricted driver namespace
save("restricted-driver.blend")

# G2. Freestyle SCRIPT mode with an embedded style module that writes a marker when rendered
scene = fresh()
style = bpy.data.texts.new("gpos_style.py")
style.write(marker_code("freestyle_script"))
scene.render.use_freestyle = True
layer = scene.view_layers[0]
layer.use_freestyle = True
layer.freestyle_settings.mode = "SCRIPT"
layer.freestyle_settings.modules.new().script = style
save("freestyle-script.blend")

# G3. an OSL script node (Cycles)
scene = fresh("CYCLES")
material = bpy.data.materials["GposMaterial"]
material.use_nodes = True
script = bpy.data.texts.new("gpos_shader.osl")
script.write("shader gpos(output color C = 0) { C = color(1, 0, 0); }\n")
osl = material.node_tree.nodes.new("ShaderNodeScript")
osl.mode = "INTERNAL"
osl.script = script
save("osl-script.blend")

# H. a custom render engine registered only while building the fixture
class GposTestEngine(bpy.types.RenderEngine):
    bl_idname = "GPOS_TEST_ENGINE"
    bl_label = "GPOS Test Engine"


bpy.utils.register_class(GposTestEngine)
scene = fresh()
scene.render.engine = "GPOS_TEST_ENGINE"
save("custom-engine.blend")
bpy.utils.unregister_class(GposTestEngine)

# a stored path whose text imitates Blender's own datafiles directory but resolves beside the .blend's parent:
# `//../<datafiles path without its root>/…`, which the test backs with a real file outside the installation
fresh()
system = os.path.realpath(bpy.utils.system_resource("DATAFILES"))
tail = os.path.splitdrive(system)[1].replace("\\", "/").strip("/")
image = bpy.data.images.new("SpoofTexture", 4, 4)
image.source = "FILE"
image.filepath = "//../" + tail + "/gpos-spoof-texture.png"
material = bpy.data.materials["GposMaterial"]
material.use_nodes = True
material.node_tree.nodes.new("ShaderNodeTexImage").image = image
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "spoof-datafiles.blend"), compress=False, relative_remap=False)
print("GPOS_SPOOF_TAIL", tail)

# a load log larger than the helper's bound: every extra scene's unavailable engine is reported (and logged)
bpy.utils.register_class(GposTestEngine)
fresh()
for index in range(800):
    bpy.data.scenes.new(f"GposLogFiller{index:04d}" + "x" * 230).render.engine = "GPOS_TEST_ENGINE"
save("oversized-log.blend")
bpy.utils.unregister_class(GposTestEngine)

# oversized authored resolution (never downscaled)
fresh(width=5000, height=100)
save("oversized.blend")

# metadata burned into the image
scene = fresh()
scene.render.use_stamp = True
save("stamp-burn-in.blend")

# multi-view and sequencer strips (extra or different outputs)
scene = fresh()
scene.render.use_multiview = True
save("multiview.blend")
scene = fresh()
editor = scene.sequence_editor_create()
strips = editor.strips if hasattr(editor, "strips") else editor.sequences
strips.new_effect(name="GposColor", type="COLOR", channel=1, frame_start=1, length=10)
save("sequencer.blend")

print("GPOS_FIXTURES_BUILT", sorted(f for f in os.listdir(OUT) if f.endswith(".blend")))
