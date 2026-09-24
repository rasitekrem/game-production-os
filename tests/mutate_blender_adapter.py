#!/usr/bin/env python3
"""Bounded mutation harness for the production Blender DCC adapter (gpos/tools/blender/).

    PATH=<directory holding the Blender executable>:$PATH python3 tests/mutate_blender_adapter.py [--jobs N] [--only TEXT]

Each mutation breaks exactly one semantic guarantee of the adapter or its fixed helper in a temporary copy
of the repository and runs tests/test_blender_adapter.py (real Blender, generated fixtures, an isolated
BLENDER_USER_RESOURCES for every process) there. A mutation must make the suite fail ("CAUGHT"); one that
leaves it green is "MISSED" and fails this harness. An anchor that does not match exactly once is
"NOT APPLIED" and also fails, so the list cannot rot.

A mutation is a list of edits (file, anchor, replacement). Mutations 1-48 are the ones the Phase 2C-4
brief requires; the Freestyle group is the one the user-state isolation decision requires; the final group is
the one the final hardening requires (bundled-path containment, the bounded load log, blocked source Python);
the rest defend further guarantees. No mutation removes the isolated user root from the environment: that defect would
point real Blender processes at the real user profile, which the suite must never touch, so it is covered
by the static environment assertions instead (group G and the parent-environment mutation below).
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTER, HELPER = "gpos/tools/blender/adapter.py", "gpos/tools/blender/helper.py"
PARSER, REGISTRY = "gpos/tools/blender/parser.py", "gpos/tools/registry.py"

PRODUCTION = "(AdbAdapter(), BlenderAdapter(), FfmpegAdapter(), FfprobeAdapter(), GitAdapter())"
BASELINE = 'BASELINE = ("--background", "--factory-startup", "--disable-autoexec", "--offline-mode", "-noaudio")'
INPUT_KINDS = 'input_kinds=("scene_name", "frame"),'
SPEC = "            spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv,\n"
POTENTIAL = 'potential_evidence=(("VISUAL_EVIDENCE", "DCC_RENDER"),),'
CANDIDATE = 'evidence_type="VISUAL_EVIDENCE", capture_context="DCC_RENDER",'
DRY_RUN = "            if context.dry_run:\n                return AdapterOutcome(\n"
DRY_RUN_END = '**({"frame": int(frame)} if frame else {})})\n'
COLLISION = ("            if output.exists() or output.is_symlink():\n"
             "                return _refuse(cap, f\"the workspace already holds {OUTPUT_NAME}; an existing file is never reported \"\n"
             "                                    f\"as a new render\")\n")
INSPECT_RECORD = ("            value = parser.record(outcome.raw_stdout, nonce)\n            if value.get(\"status\") == \"refused\":\n"
                  "                code, message = parser.refusal(value)\n                return AdapterOutcome(")
RENDER_RECORD = ("            value = parser.record(outcome.raw_stdout, nonce)\n            if value.get(\"status\") == \"refused\":\n"
                 "                code, message = parser.refusal(value)\n                if produced:")
FREESTYLE = "    if scene.render.use_freestyle:\n        raise Refusal(\"RENDER_SCRIPTING\""
RENDER_RETURN = '    return {"scene": scene.name, "frame": frame, "camera": name(scene.camera.name), "engine": engine,\n'
CAPABILITIES_END = ")\n\nDESCRIPTOR = model.AdapterDescriptor("
EXTERNAL = "    external = {real for real in (resolved(path) for path in raw if path) if not blender_owned(real, system)}\n"


def declared_input(kind):
    return (ADAPTER, INPUT_KINDS, f'input_kinds=("scene_name", "frame", "{kind}"),')


def before_spec(code):
    return (ADAPTER, SPEC, code + SPEC)


def evidence_claim(evidence_type, context):
    return [(ADAPTER, POTENTIAL, f'potential_evidence=(("{evidence_type}", "{context}"),),'),
            (ADAPTER, CANDIDATE, f'evidence_type="{evidence_type}", capture_context="{context}",')]


MUTATIONS = [
    ("1 blender absent from the production registry", [
        (REGISTRY, PRODUCTION, "(AdbAdapter(), FfmpegAdapter(), FfprobeAdapter(), GitAdapter())")]),
    ("2 TEST_ONLY synthetic enters the production registry", [
        (REGISTRY, "        registry.register(adapter)\n    return registry",
         "        registry.register(adapter)\n    registry.allow_test_only = True\n"
         "    from .synthetic import SyntheticAdapter\n    registry.register(SyntheticAdapter())\n    return registry")]),
    ("3 caller chooses the Blender executable", [
        declared_input("executable"),
        (ADAPTER, "executable=context.probe.tool_path, argv=argv,",
         'executable=(request.inputs or {}).get("executable") or context.probe.tool_path, argv=argv,')]),
    ("4 caller supplies a Python script", [
        declared_input("python"),
        before_spec('            if (request.inputs or {}).get("python"):\n'
                    '                argv = ("--python", request.inputs["python"]) + argv\n')]),
    ("5 caller Python expression introduced", [
        declared_input("expression"),
        before_spec('            if (request.inputs or {}).get("expression"):\n'
                    '                argv = ("--python-expr", request.inputs["expression"]) + argv\n')]),
    ("6 --disable-autoexec removed", [
        (ADAPTER, BASELINE, 'BASELINE = ("--background", "--factory-startup", "--offline-mode", "-noaudio")')]),
    ("7 factory-startup isolation removed", [
        (ADAPTER, BASELINE, 'BASELINE = ("--background", "--disable-autoexec", "--offline-mode", "-noaudio")')]),
    ("8 the fixed helper becomes caller-controlled", [
        declared_input("helper"),
        before_spec('            argv = tuple((request.inputs or {}).get("helper", a) if a == HELPER else a for a in argv)\n')]),
    ("9 helper uses eval", [
        (HELPER, "    frame = int(frame_text) if frame_text else scene.frame_current\n",
         "    frame = eval(frame_text) if frame_text else scene.frame_current\n")]),
    ("10 helper uses exec", [
        (HELPER, "            open_blend(args[0])\n            record = dict(inspect(), status=\"ok\", mode=mode)\n",
         "            open_blend(args[0])\n            for text in bpy.data.texts:\n                exec(text.as_string())\n"
         "            record = dict(inspect(), status=\"ok\", mode=mode)\n")]),
    ("11 helper can save the .blend", [
        (HELPER, RENDER_RETURN, "    bpy.ops.wm.save_mainfile()\n" + RENDER_RETURN)]),
    ("12 helper enables embedded scripts", [
        (HELPER, "filepath=path, load_ui=False, use_scripts=False)", "filepath=path, load_ui=False, use_scripts=True)")]),
    ("13 source capture_context accepted", [
        (ADAPTER, "    if source.origin_capture_context is not None:\n", "    if False:\n")]),
    ("14 wrong or multiple source artifacts accepted", [
        (ADAPTER, "    if len(inputs) != 1:\n", "    if not inputs:\n"),
        (ADAPTER, "    if source.artifact_id != INPUT_ID:\n", "    if False:\n")]),
    ("15 non-.blend input accepted", [
        (ADAPTER, '    if not str(source.absolute_path).endswith(".blend"):\n', "    if False:\n")]),
    ("16 inspection starts returning paths", [
        (HELPER, '        "has_script_nodes": script_nodes(),\n',
         '        "has_script_nodes": script_nodes(),\n        "source_path": bpy.data.filepath,\n'),
        (PARSER, '              "has_script_nodes")\n', '              "has_script_nodes", "source_path")\n')]),
    ("17 helper protocol parsed from the public redacted text", [
        (ADAPTER, INSPECT_RECORD, INSPECT_RECORD.replace("outcome.raw_stdout", "outcome.stdout.encode()")),
        (ADAPTER, RENDER_RECORD, RENDER_RECORD.replace("outcome.raw_stdout", "outcome.stdout.encode()"))]),
    ("18 truncation ignored", [(ADAPTER, "    if outcome.truncated:\n", "    if False:\n")]),
    ("19 multiple sentinels tolerated", [(PARSER, "    if len(lines) != 1:\n", "    if not lines:\n")]),
    ("19b a missing sentinel tolerated", [
        (PARSER, "    if len(lines) != 1:\n", '    if not lines:\n        return {"status": "ok"}\n    if len(lines) != 1:\n')]),
    ("20 render allowed outside ASSET scope", [(ADAPTER, '            if request.subject.kind != "ASSET":\n', "            if False:\n")]),
    ("21 fuzzy scene selection introduced", [
        (HELPER, "        scene = bpy.data.scenes.get(scene_name)\n        if scene is None or scene.name != scene_name:\n",
         "        scene = next((s for s in bpy.data.scenes if s.name.strip().lower() == scene_name.strip().lower()), None)\n"
         "        if scene is None:\n")]),
    ("22 a missing camera is replaced by a generated camera", [
        (HELPER, "    if scene.camera is None:\n        raise Refusal(",
         "    if scene.camera is None:\n        generated = bpy.data.objects.new(\"GposCamera\", bpy.data.cameras.new(\"GposCamera\"))\n"
         "        scene.collection.objects.link(generated)\n        scene.camera = generated\n    if False:\n        raise Refusal(")]),
    ("23 caller can choose the camera", [
        declared_input("camera"),
        before_spec('            camera = (request.inputs or {}).get("camera")\n')]),
    ("24 frame outside the range clamped", [
        (HELPER, "    if not scene.frame_start <= frame <= scene.frame_end:\n",
         "    frame = min(max(frame, scene.frame_start), scene.frame_end)\n    if False:\n")]),
    ("25 caller render-engine override introduced", [
        declared_input("engine"),
        before_spec('            engine = (request.inputs or {}).get("engine")\n')]),
    ("26 custom render engine accepted", [
        (HELPER, "    unavailable = unavailable_engines()\n    if unavailable:\n", "    unavailable = {}\n    if unavailable:\n")]),
    ("27 oversized resolution silently downscaled", [
        (HELPER, "    width, height = effective_resolution(scene)\n    if not",
         "    width, height = effective_resolution(scene)\n    while width > MAX_EDGE or height > MAX_EDGE:\n"
         "        scene.render.resolution_percentage //= 2\n        width, height = effective_resolution(scene)\n    if not")]),
    ("28 external dependency accepted for evidence", [
        (HELPER, "    count, _ = external_dependencies()\n    if count:\n", "    count, _ = external_dependencies()\n    if False:\n")]),
    ("29 compositor file-output node allowed", [
        (HELPER, "    if compositor_file_outputs():\n        raise", "    if False:\n        raise")]),
    ("30 source file modified", [
        (HELPER, RENDER_RETURN, "    bpy.data.libraries.write(bpy.data.filepath, set(bpy.data.objects))\n" + RENDER_RETURN)]),
    ("31 output written beside the source", [
        (ADAPTER, "            output = Path(context.workspace) / OUTPUT_NAME\n",
         "            output = Path(source.absolute_path).parent / OUTPUT_NAME\n")]),
    ("32 caller output path introduced", [
        declared_input("output"),
        (ADAPTER, "            output = Path(context.workspace) / OUTPUT_NAME\n",
         '            output = Path(inputs.get("output") or context.workspace) / OUTPUT_NAME\n')]),
    ("33 malformed (incomplete) PNG accepted", [
        (PARSER, "    if not bytes(data).endswith(PNG_IEND):\n", "    if False:\n")]),
    ("33b a non-PNG accepted", [
        (ADAPTER, "        actual = parser.png_dimensions(output.read_bytes())\n", "        actual = (width, height)\n")]),
    ("34 wrong dimensions accepted", [(ADAPTER, "    if actual != (width, height):\n", "    if False:\n")]),
    ("35 partial artifact offered as evidence", [
        (ADAPTER, "    if outcome.exit_code != 0:\n", "    if outcome.exit_code not in (0, 71):\n"),
        (PARSER, "    if not bytes(data).endswith(PNG_IEND):\n", "    if False:\n")]),
    ("36 DCC render claims TARGET_RUNTIME", evidence_claim("VISUAL_EVIDENCE", "TARGET_RUNTIME")),
    ("37 DCC render claims MOTION_EVIDENCE", evidence_claim("MOTION_EVIDENCE", "DCC_RENDER")),
    ("38 DCC render claims HUMAN_EVIDENCE", evidence_claim("HUMAN_EVIDENCE", "DCC_RENDER")),
    ("39 dry run launches project Blender", [
        (ADAPTER, DRY_RUN, "            if context.dry_run and False:\n                return AdapterOutcome(\n")]),
    ("40 dry run writes render.png", [
        (ADAPTER, DRY_RUN, "            if context.dry_run:\n                output.parent.mkdir(parents=True, exist_ok=True)\n"
                           "                output.write_bytes(b\"\")\n                return AdapterOutcome(\n")]),
    ("41 collision check occurs after the dry run", [
        (ADAPTER, COLLISION + DRY_RUN, DRY_RUN),
        (ADAPTER, DRY_RUN_END, DRY_RUN_END + COLLISION)]),
    ("42 helper imports subprocess", [(HELPER, "import sys\n\nimport bpy\n", "import subprocess\nimport sys\n\nimport bpy\n")]),
    ("43 helper gains network access", [
        (HELPER, "import sys\n\nimport bpy\n", "import sys\nimport urllib.request\n\nimport bpy\n")]),
    ("43b --offline-mode removed", [
        (ADAPTER, BASELINE, 'BASELINE = ("--background", "--factory-startup", "--disable-autoexec", "-noaudio")')]),
    ("44 source path leaks into the recorded command", [
        (ADAPTER, 'str(source.absolute_path): PLACEHOLDERS["blend"], ', "")]),
    ("45 helper path leaks into the recorded command", [(ADAPTER, 'swap = {HELPER: PLACEHOLDERS["helper"], ', "swap = {")]),
    ("46 dependency path leaks publicly", [
        (HELPER, 'f"the file references {count} external file(s); only "',
         'f"the file references {sorted(bpy.utils.blend_paths(absolute=True))[:2]}; only "')]),
    ("47 Blender adapter imports subprocess", [(ADAPTER, "import shutil\nimport sys\n", "import shutil\nimport subprocess\nimport sys\n")]),
    ("48 generic Blender command capability introduced", [
        (ADAPTER, CAPABILITIES_END,
         "    Capability(\n        id=f\"{ADAPTER_ID}.run-command\", category=\"INSPECT\", operation_class=\"READ_ONLY\",\n"
         "        state_model=\"STATELESS\", execution_context=\"OFFLINE_ANALYSIS\", requires_tool=True, requires_project=True,\n"
         "        description=\"Run a Blender command line.\", timeout=TimeoutPolicy(default=120.0, maximum=600.0)),\n"
         + CAPABILITIES_END)]),
    # --- Freestyle and render-time scripting (user-state isolation decision)
    ("F1 Freestyle script check removed", [(HELPER, FREESTYLE, '    if False:\n        raise Refusal("RENDER_SCRIPTING"')]),
    ("F2 Freestyle script configuration accepted", [
        (HELPER, FREESTYLE,
         '    if scene.render.use_freestyle and not any(layer.use_freestyle and layer.freestyle_settings.mode == "SCRIPT"\n'
         '                                              for layer in scene.view_layers):\n        raise Refusal("RENDER_SCRIPTING"')]),
    ("F3 render-time scripting silently disabled instead of refused", [
        (HELPER, FREESTYLE, '    scene.render.use_freestyle = False\n    if False:\n        raise Refusal("RENDER_SCRIPTING"')]),
    ("F4 OSL script-node check removed", [
        (HELPER, '    if script_nodes() or (engine == "CYCLES" and getattr(scene.cycles, "shading_system", False)):\n',
         "    if False:\n")]),
    # --- further guarantees
    ("unavailable-engine load log no longer read", [
        (ADAPTER, 'return BASELINE + ("--log-file", str(Path(user_root) / LOG_NAME), "--python-exit-code"',
         'return BASELINE + ("--python-exit-code"'),
        (HELPER, '    if "--log-file" not in args:\n        raise Refusal(', '    if "--log-file" not in args:\n        return {}\n        raise Refusal(')]),
    ("burned-in metadata stamps rendered", [(HELPER, "    if scene.render.use_stamp:\n", "    if False:\n")]),
    ("PNG metadata stamps kept (file name and date in the image)", [
        (HELPER, '        if flag.identifier.startswith("use_stamp_"):\n', '        if flag.identifier == "use_stamp_gpos":\n')]),
    ("multi-view allowed", [(HELPER, "    if scene.render.use_multiview:\n", "    if False:\n")]),
    ("sequencer strips allowed", [(HELPER, "    if scene.render.use_sequencer and len(strips):\n", "    if False:\n")]),
    ("scene limit removed", [(HELPER, "    if len(scenes) > MAX_SCENES:\n", "    if False:\n")]),
    ("isolated user root taken from the parent environment", [
        (ADAPTER, '(("BLENDER_USER_RESOURCES", str(user_root)),)',
         '(("BLENDER_USER_RESOURCES", os.environ.get("BLENDER_USER_RESOURCES") or str(user_root)),)')]),
    ("isolated user root left behind", [
        (ADAPTER, "            shutil.rmtree(user_root, ignore_errors=True)\n            try:\n                runtime_base.rmdir()",
         "            try:\n                runtime_base.rmdir()")]),
    ("nonce check removed", [
        (PARSER, "    if not line.startswith(head):\n        raise HelperProtocolError(\"the helper record does not carry",
         "    head = line[:len(PREFIX) + 33]\n    if False:\n        raise HelperProtocolError(\"the helper record does not carry")]),
    ("bundled-asset containment replaced by a substring match", [
        (HELPER, "if not blender_owned(real, system)}", "if \"datafiles\" not in real}")]),
    ("Blender's own references counted as project dependencies", [
        (HELPER, "        return os.path.commonpath([path, root]) == root\n", "        return False\n")]),
    ("self-test accepts auto-execution enabled", [
        (PARSER, ' or value["autoexec_enabled"] is not False or ', " or ")]),
    ("probe skips the helper self-test", [
        (ADAPTER, "            nonce = secrets.token_hex(16)\n            test = proc.run_process(",
         "            return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=executable, tool_version=tool_version,\n"
         "                                     platform=platform, detail=\"no self-test\",\n"
         "                                     capability_availability=tuple((c.id, True, \"\") for c in CAPABILITIES))\n"
         "            nonce = secrets.token_hex(16)\n            test = proc.run_process(")]),
    ("case-insensitive executable lookup accepted", [(ADAPTER, "            if base not in entries:", "            if False:")]),
    ("frame text not canonical", [(ADAPTER, 'FRAME_TEXT = re.compile(r"0|[1-9][0-9]{0,6}")', 'FRAME_TEXT = re.compile(r"[-+]?[0-9]+")')]),
    ("a helper refusal of a render is reported as success", [
        (ADAPTER, "        diagnostics=(dg.make(\"DCC_SOURCE_NOT_ACCEPTED\", f\"{code}: {message}\", ADAPTER_ID,\n"
                  "                                                           cap),), **record)\n            rendered",
         "        **record)\n            rendered")]),
    ("an existing output file is overwritten", [(ADAPTER, "            if output.exists() or output.is_symlink():\n", "            if False:\n")]),
    # --- final hardening: bundled-path containment, bounded load log, blocked source Python
    ("old textual-tail shortcut for bundled paths restored", [
        (HELPER, EXTERNAL,
         "    def textual(path):\n"
         "        tail = os.path.splitdrive(system)[1].replace(\"\\\\\", \"/\").strip(\"/\")\n"
         "        rest = path[2:].replace(\"\\\\\", \"/\") if path.startswith(\"//\") else \"\"\n"
         "        while rest.startswith(\"../\"):\n            rest = rest[3:]\n"
         "        return rest == tail or rest.startswith(tail + \"/\")\n"
         "    external = {resolved(path) for path in raw if path and not textual(path)\n"
         "                and not blender_owned(resolved(path), system)}\n")]),
    ("bundled containment trusts the stored text instead of the resolved file", [
        (HELPER, "    return os.path.realpath(bpy.path.abspath(raw))\n", "    return os.path.normpath(raw.replace(\"//\", os.sep, 1))\n")]),
    ("missing path inside DATAFILES is accepted as bundled", [
        (HELPER, "    return inside(real, system) and os.path.isfile(real)\n", "    return inside(real, system)\n")]),
    ("oversized Blender load log accepted", [
        (HELPER, '    if len(data) > LOG_LIMIT:\n        raise Refusal("ENGINE_LOG_UNBOUNDED"',
         '    data = data[:LOG_LIMIT]\n    if False:\n        raise Refusal("ENGINE_LOG_UNBOUNDED"')]),
    ("load log read in part without a completeness check", [
        (HELPER, "            data = log.read(LOG_LIMIT + 1)\n", "            data = log.read(LOG_LIMIT)\n")]),
    ("autoexec_fail check removed", [
        (HELPER, '    require_no_blocked_python("at load")\n', ""),
        (HELPER, '    require_no_blocked_python("at the frame")\n', ""),
        (HELPER, '    require_no_blocked_python("while rendering")', "    pass")]),
    ("blocked autoexec accepted for render (checked only after rendering)", [
        (HELPER, '    require_no_blocked_python("at load")\n', ""),
        (HELPER, '    require_no_blocked_python("at the frame")\n', "")]),
    ("autoexec failure silently ignored", [
        (HELPER, "    if bpy.app.autoexec_fail:\n        raise Refusal(\"AUTOEXEC_REQUIRED\"",
         "    if bpy.app.autoexec_fail:\n        return\n        raise Refusal(\"AUTOEXEC_REQUIRED\"")]),
    ("blocked text blocks ignored (only drivers refused)", [
        (HELPER, "    if bpy.app.autoexec_fail:\n",
         "    if bpy.app.autoexec_fail and bpy.app.autoexec_fail_message.startswith(\"Driver\"):\n")]),
    ("blocked drivers ignored (only text blocks refused)", [
        (HELPER, "    if bpy.app.autoexec_fail:\n",
         "    if bpy.app.autoexec_fail and not bpy.app.autoexec_fail_message.startswith(\"Driver\"):\n")]),
    ("every driver refused, even those Blender evaluates without Python", [
        (HELPER, "    if bpy.app.autoexec_fail:\n",
         "    if bpy.app.autoexec_fail or any(ob.animation_data and ob.animation_data.drivers for ob in bpy.data.objects):\n")]),
    ("self-test no longer requires Blender's blocked-Python flag", [
        (PARSER, 'value["blend_paths"], value["autoexec_fail"], value["factory_startup"])',
         'value["blend_paths"], value["factory_startup"])')]),
]


def run(mutation):
    name, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-blendermut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for rel, anchor, replacement in edits:
            path = copy / rel
            text = path.read_text()
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            path.write_text(text.replace(anchor, replacement))
        env = {k: v for k, v in os.environ.items() if not k.startswith("BLENDER_")}
        out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_blender_adapter.py")],
                             capture_output=True, text=True, timeout=3600, env=dict(env, PYTHONDONTWRITEBYTECODE="1"))
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    args = parser.parse_args()
    selected = [m for m in MUTATIONS if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
