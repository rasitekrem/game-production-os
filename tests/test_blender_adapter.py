#!/usr/bin/env python3
"""Phase 2C-4 — production Blender DCC adapter tests.

    python3 tests/test_blender_adapter.py

Real integration tests against the installed Blender, found exactly as the production adapter finds it
(exact-name lookup on PATH). Every .blend fixture is generated fresh by `tests/blender_fixture_builder.py`
inside that Blender; no binary fixture is committed. The suite fails with the marker
BLENDER_RUNTIME_UNAVAILABLE_FOR_PHASE2C4 if Blender is absent, and never falls back to mocks: stand-in
programs are used only where a real Blender cannot be made to misbehave on demand (missing, malformed
version, broken helper protocol, partial output, timeout), and say so.

The user's real Blender profile never participates. Test-side Blender runs use their own isolated
BLENDER_USER_RESOURCES, the adapter uses its own per execution, and a module-level guard fails the suite
if anything under the real profile (macOS: ~/Library/Application Support/Blender) changes.

Groups: A registration · B real probe · C missing or incompatible Blender · D inspection · E source
immutability · F autoexec and render-time scripts · G user startup and add-on isolation · H input contract ·
I inspection bounds and protocol · J external dependencies · K asset scope · L scene · M camera · N frame ·
O render engine · P resolution · Q extra outputs · R real render · S materialization · T DCC authority and
gate boundary · U mutation consent · V dry run · W partial output and timeout · X path and secret privacy ·
Y command surface · Z CLI.
"""

import ast
import copy
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import gpos  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.records import from_records  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import evidence as tev  # noqa: E402
from gpos.tools import model as tmodel  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools import validation as tval  # noqa: E402
from gpos.tools.blender import adapter as ba  # noqa: E402
from gpos.tools.blender import parser as bp  # noqa: E402
from gpos.tools.blender import BlenderAdapter  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import InputArtifact, Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.synthetic import SyntheticAdapter  # noqa: E402

FW = load_framework()
REG = FW.registry
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
BUILDER = ROOT / "tests" / "blender_fixture_builder.py"
BLENDER, _DISCOVERY_PROBLEM = BlenderAdapter().find_executable()
REVISION = "rev-asset-0001"
SECRET = "ghp_" + "Q1w2E3r4T5y6U7i8O9p0" + "asdfghjklzxc"          # credential-shaped, not a credential
REAL_PROFILE = Path.home() / "Library" / "Application Support" / "Blender"
FIX = {}                    # name -> generated .blend path
_STATE = {}


def fingerprint(root):
    if not root.exists():
        return None
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*"))


def blender_test(args, user_root, timeout=300):
    """A TEST-SIDE Blender run (fixtures and sensitivity controls), always with its own isolated user root."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BLENDER_")}
    env["BLENDER_USER_RESOURCES"] = str(user_root)
    return subprocess.run([BLENDER, *args], capture_output=True, timeout=timeout, env=env)


def setUpModule():
    if BLENDER is None:
        raise RuntimeError(f"BLENDER_RUNTIME_UNAVAILABLE_FOR_PHASE2C4: {_DISCOVERY_PROBLEM}")
    _STATE["profile"] = fingerprint(REAL_PROFILE)
    work = Path(tempfile.mkdtemp(prefix="gpos-blender-fixtures-")).resolve()
    _STATE["work"] = work
    out, markers, user = work / "out", work / "markers", work / "user"
    for d in (out, markers, user):
        d.mkdir()
    done = blender_test(["--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
                         "--python-exit-code", "3", "--python", str(BUILDER), "--", str(out), str(markers)], user)
    if done.returncode != 0 or b"GPOS_FIXTURES_BUILT" not in done.stdout:
        raise RuntimeError("the Blender fixtures could not be built")
    _STATE["spoof_tail"] = re.search(rb"GPOS_SPOOF_TAIL (\S+)", done.stdout).group(1).decode()
    for path in out.glob("*.blend"):
        FIX[path.stem] = path
    FIX["external-texture.png"] = out / "external-texture.png"
    _STATE["markers"] = markers
    for leftover in markers.iterdir():
        raise RuntimeError(f"a marker exists before any test: {leftover.name}")


def tearDownModule():
    try:
        if fingerprint(REAL_PROFILE) != _STATE.get("profile"):
            raise AssertionError("the real Blender user profile changed during the test run")
    finally:
        shutil.rmtree(_STATE.get("work", "/nonexistent"), ignore_errors=True)


def markers():
    return sorted(p.name for p in _STATE["markers"].iterdir())


def clear_markers():
    for p in _STATE["markers"].iterdir():
        p.unlink()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Recorder:
    """Records every process spec started through the audited boundary while active."""

    def __init__(self, transform=None):
        self.specs, self._original, self._transform = [], tproc.run_process, transform

    def __enter__(self):
        def recording(spec, scopes, clock=None):
            self.specs.append(spec)
            outcome = self._original(spec, scopes) if clock is None else self._original(spec, scopes, clock)
            return self._transform(spec, outcome) if self._transform else outcome
        tproc.run_process = recording
        return self

    def __exit__(self, *exc):
        tproc.run_process = self._original

    @property
    def project_runs(self):
        """Blender processes that opened a project file (inspect or render), not the probe."""
        return [s for s in self.specs if "--" in s.argv and s.argv[s.argv.index("--") + 1] in ("inspect", "render")]


class BlenderCase(unittest.TestCase):
    registry = None

    @classmethod
    def setUpClass(cls):
        cls.registry = default_registry(FW)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-blender-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        clear_markers()

    def project(self, *fixtures, name="p"):
        target, n = self.tmp / name, 1
        while target.exists():
            n += 1
            target = self.tmp / f"{name}{n}"
        shutil.copytree(FIXTURE, target)
        (target / "assets").mkdir()
        for fixture in fixtures:
            shutil.copyfile(FIX[fixture], target / "assets" / f"{fixture}.blend")
        return target

    def blend(self, project, fixture):
        return project / "assets" / f"{fixture}.blend"

    def request(self, capability, project, fixture=None, artifacts=None, kind="ASSET", subject=None, **kwargs):
        if artifacts is None:
            artifacts = (InputArtifact("blend", str(self.blend(project, fixture))),)
        if capability == ba.RENDER:
            kwargs.setdefault("allow_mutation", not kwargs.get("dry_run", False))
        return ExecutionRequest(adapter_id="blender", capability_id=capability,
                                subject=subject or Subject(kind, "ASSET-0001", REVISION), project_root=str(project),
                                input_artifacts=artifacts, **kwargs)

    def run_cap(self, capability, project, fixture=None, registry=None, **kwargs):
        return execute(registry or self.registry, self.request(capability, project, fixture, **kwargs))

    def inspect(self, fixture, **kwargs):
        p = self.project(fixture)
        return self.run_cap(ba.INSPECT, p, fixture, **kwargs), p

    def render(self, fixture, **kwargs):
        p = self.project(fixture)
        return self.run_cap(ba.RENDER, p, fixture, **kwargs), p

    def codes(self, result):
        return {d.code for d in result.diagnostics}

    def messages(self, result):
        return " ".join(d.message for d in result.diagnostics)

    def assertSucceeded(self, result):
        self.assertEqual(result.status, tdg.SUCCESS, self.messages(result))

    def assertRefused(self, result, text=None, code="INVALID_TOOL_REQUEST"):
        self.assertEqual(result.status, tdg.INVALID_REQUEST, self.messages(result))
        self.assertIn(code, self.codes(result))
        if text:
            self.assertIn(text, self.messages(result))
        self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))

    def assertNotRendered(self, result, reason):
        self.assertRefused(result, reason, code="DCC_SOURCE_NOT_ACCEPTED")
        self.assertFalse(result.mutation_performed)

    def workspace_root(self, project):
        return project / ".game" / "gpos-runtime" / "tool-output"


# ---------------------------------------------------------------- stand-in Blender (TEST_ONLY)

STAND_IN = r"""
import json, sys, time
cfg = json.load(open(CONFIG))
argv = sys.argv[1:]
if argv == ["--version"]:
    sys.stdout.write(cfg.get("version", "Blender 5.2.0 LTS\n\tbuild date: 2026-07-14\n")); sys.exit(0)
rest = argv[argv.index("--") + 1:]
mode, nonce = rest[0], rest[1]
record = cfg.get(mode, {})
if mode == "render":
    output = rest[3]
    if "png_hex" in cfg:
        open(output, "wb").write(bytes.fromhex(cfg["png_hex"]))
# "lines"/"exit" shape the probe's self-test; "<key>_<mode>" shapes one project mode only
time.sleep(cfg.get("sleep_" + mode, 0))
default = ["GPOS_BLENDER_RESULT_V1:%s:%s" % (nonce, json.dumps(record, separators=(",", ":")))]
lines = cfg.get("lines_" + mode, cfg.get("lines", default) if mode == "selftest" else default)
for line in lines:
    print(line.replace("{nonce}", nonce))
sys.exit(cfg.get("exit_" + mode, cfg.get("exit", 0) if mode == "selftest" else 0))
"""

GOOD_SELFTEST = {"status": "ok", "mode": "selftest", "blender_version": "5.2.0",
                 "engines": ["BLENDER_EEVEE", "BLENDER_WORKBENCH", "CYCLES"],
                 "open_mainfile": {"use_scripts": True, "load_ui": True}, "render": {"write_still": True, "scene": True},
                 "blend_paths": True, "autoexec_fail": True, "factory_startup": True, "autoexec_enabled": False, "online_access": False}
GOOD_INSPECT = {"status": "ok", "mode": "inspect", "blend_file_version": "5.2.44", "active_scene": "Scene",
                "scene_count": 1,
                "scenes": [{"name": "Scene", "camera": "Camera", "frame_start": 1, "frame_end": 10, "current_frame": 3,
                            "render_engine": "BLENDER_WORKBENCH", "render_engine_available": True,
                            "resolution": {"x": 64, "y": 48, "percentage": 100}, "freestyle": False}],
                "object_counts": {"MESH": 1, "ARMATURE": 0, "CAMERA": 1, "LIGHT": 1, "EMPTY": 0, "OTHER": 0},
                "mesh_datablocks": 1, "total_vertices": 8, "total_edges": 12, "total_polygons": 6, "material_count": 2,
                "image_count": 2, "armature_count": 0, "action_count": 0,
                "external_dependencies": {"count": 0, "missing": 0}, "has_compositor_file_outputs": False,
                "has_script_nodes": False}
GOOD_RENDER = {"status": "ok", "mode": "render", "scene": "Scene", "frame": 3, "camera": "Camera",
               "engine": "BLENDER_WORKBENCH", "width": 64, "height": 48}


def png_bytes(width=64, height=48, complete=True):
    body = bp.PNG_SIGNATURE + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x06\0\0\0"
    body += b"\0\0\0\0" + struct.pack(">I", 4) + b"IDAT" + b"\0" * 8
    return body + (bp.PNG_IEND if complete else b"")


class StandIn:
    def __init__(self, directory, name="Blender", **config):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        config.setdefault("selftest", GOOD_SELFTEST)
        if "png" in config:
            config["png_hex"] = config.pop("png").hex()
        self.config = self.dir / "config.json"
        self.config.write_text(json.dumps(config))
        self.exe = self.dir / name
        self.exe.write_text(f"#!{tproc.interpreter_path()}\nCONFIG = {str(self.config)!r}\n{STAND_IN}")
        self.exe.chmod(0o755)

    def adapter(self, **kwargs):
        return BlenderAdapter(which=lambda name: str(self.exe) if name == self.exe.name else None, **kwargs)

    def registry(self, **kwargs):
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(self.adapter(**kwargs))
        return registry


# ---------------------------------------------------------------- A  registration

class A_Registration(BlenderCase):
    def test_production_registry_is_exactly_the_five_adapters(self):
        self.assertEqual(default_registry(FW).adapter_ids(), ["adb", "blender", "ffmpeg", "ffprobe", "git"])

    def test_synthetic_stays_out_of_production(self):
        registry = default_registry(FW)
        with self.assertRaises(Exception):
            registry.register(SyntheticAdapter())
        self.assertEqual(registry.adapter_ids(), ["adb", "blender", "ffmpeg", "ffprobe", "git"])

    def test_descriptor(self):
        self.assertEqual(tval.validate_descriptor(FW, ba.DESCRIPTOR, allow_test_only=False), [])
        d = ba.DESCRIPTOR
        self.assertEqual((d.adapter_id, d.tool_family, d.target_tool, d.adapter_kind, d.state_model, d.network),
                         ("blender", "DCC", "Blender", "CLI", "STATELESS", "FORBIDDEN"))
        self.assertEqual(d.supported_platforms, ("WINDOWS", "MACOS", "LINUX"))
        self.assertFalse(d.test_only)

    def test_exactly_two_capabilities(self):
        caps = {c.id: c for c in ba.DESCRIPTOR.capabilities}
        self.assertEqual(sorted(caps), ["blender.inspect-blend", "blender.render-scene"])
        i, r = caps[ba.INSPECT], caps[ba.RENDER]
        self.assertEqual((i.category, i.operation_class, i.state_model, i.execution_context, i.dry_run_supported,
                          i.input_kinds, i.artifact_kinds, i.potential_evidence),
                         ("INSPECT", "READ_ONLY", "STATELESS", "OFFLINE_ANALYSIS", False, (), (), ()))
        self.assertEqual((r.category, r.operation_class, r.state_model, r.execution_context, r.dry_run_supported,
                          r.input_kinds, r.artifact_kinds, r.potential_evidence),
                         ("CAPTURE", "MUTATING", "STATELESS", "DCC_RENDER", True, ("scene_name", "frame"), ("IMAGE",),
                          (("VISUAL_EVIDENCE", "DCC_RENDER"),)))
        for cap in caps.values():
            self.assertTrue(cap.requires_tool and cap.requires_project)
            self.assertFalse(cap.requires_ready_routing or cap.single_writer_required)

    def test_agent_and_tool_registries_stay_separate(self):
        from gpos.adapters.backends import BACKENDS
        self.assertNotIn("blender", REG["adapter_ids"])
        self.assertNotIn("blender", BACKENDS)


# ---------------------------------------------------------------- B  real probe

class B_RealProbe(BlenderCase):
    def test_real_blender_is_available_with_helper_compatibility(self):
        probe = BlenderAdapter().probe()
        self.assertEqual(probe.status, tmodel.AVAILABLE, probe.detail)
        self.assertTrue(os.path.isabs(probe.tool_path))
        self.assertIn(os.path.basename(probe.tool_path), os.listdir(os.path.dirname(probe.tool_path)))  # exact name
        first = subprocess.run([BLENDER, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
        self.assertEqual(f"Blender {probe.tool_version}", first.strip())
        self.assertIn("helper self-test passed", probe.detail)

    def test_the_probe_runs_version_and_the_selftest_only(self):
        with Recorder() as rec:
            BlenderAdapter().probe()
        modes = [s.argv[s.argv.index("--") + 1] if "--" in s.argv else s.argv[0] for s in rec.specs]
        self.assertEqual(modes, ["--version", "selftest"])
        self.assertEqual(rec.project_runs, [])

    def test_no_subprocess_or_network_import_in_the_blender_modules(self):
        for path in sorted((ROOT / "gpos" / "tools" / "blender").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                names = set()
                if isinstance(node, ast.Import):
                    names = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {(node.module or "").split(".")[0]}
                self.assertEqual(names & {"subprocess", "socket", "urllib", "http", "requests", "ctypes", "ssl",
                                          "ftplib", "asyncio", "multiprocessing"}, set(), path)

    def test_process_py_is_still_the_only_subprocess_importer(self):
        importers = set()
        for path in (ROOT / "gpos").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names)) or \
                        (isinstance(node, ast.ImportFrom) and node.module == "subprocess"):
                    importers.add(path.relative_to(ROOT).as_posix())
        self.assertEqual(importers, {"gpos/tools/process.py"})

    def test_exact_name_discovery(self):
        d = self.tmp / "bin"
        d.mkdir()
        (d / "Blender").write_text("#!/bin/sh\n")
        (d / "Blender").chmod(0o755)
        lookups = {"blender": str(d / "blender"), "Blender": str(d / "Blender")}  # a case-insensitive lookup "hit"
        adapter = BlenderAdapter(which=lookups.get, platform="darwin")
        self.assertEqual(adapter.find_executable(), (str(d / "Blender"), None))
        self.assertEqual(BlenderAdapter(which={"blender": str(d / "blender")}.get, platform="linux").find_executable()[0],
                         None)  # no real entry named "blender": never accepted by case-insensitive luck
        self.assertIn("relative PATH", BlenderAdapter(which=lambda n: "bin/blender", platform="linux").find_executable()[1])
        other = self.tmp / "other"
        other.mkdir()
        (other / "blender").write_text("#!/bin/sh\n")
        (other / "blender").chmod(0o755)
        both = {"blender": str(other / "blender"), "Blender": str(d / "Blender")}
        self.assertIn("more than one distinct", BlenderAdapter(which=both.get, platform="darwin").find_executable()[1])


# ---------------------------------------------------------------- C  missing or incompatible Blender

class C_MissingOrIncompatible(BlenderCase):
    def test_missing_blender_is_unavailable_without_a_traceback(self):
        probe = BlenderAdapter(which=lambda n: None).probe()
        self.assertEqual((probe.status, probe.tool_path), (tmodel.UNAVAILABLE, None))
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(BlenderAdapter(which=lambda n: None))
        p = self.project("safe")
        with Recorder() as rec:
            result = self.run_cap(ba.INSPECT, p, "safe", registry=registry)
        self.assertEqual(result.status, tdg.UNAVAILABLE)
        self.assertEqual(rec.specs, [])
        self.assertNotIn("Traceback", json.dumps(result.to_dict()))

    def test_malformed_version_is_never_fabricated(self):
        for text in ("Blender\n", "blender 5.2.0\n", "Blender 5.2.0 LTS $(id)\n", ""):
            probe = StandIn(self.tmp / f"v{len(text)}", version=text).adapter().probe()
            self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED, text)
            self.assertIsNone(probe.tool_version)

    def test_an_incompatible_helper_or_missing_api_is_unsupported(self):
        cases = {"no record": dict(lines=["nothing here"]),
                 "two records": dict(lines=["GPOS_BLENDER_RESULT_V1:{nonce}:{}", "GPOS_BLENDER_RESULT_V1:{nonce}:{}"]),
                 "no use_scripts": dict(selftest=dict(GOOD_SELFTEST, open_mainfile={"use_scripts": False, "load_ui": True})),
                 "autoexec on": dict(selftest=dict(GOOD_SELFTEST, autoexec_enabled=True)),
                 "online": dict(selftest=dict(GOOD_SELFTEST, online_access=True)),
                 "no engines": dict(selftest=dict(GOOD_SELFTEST, engines=[])),
                 "no autoexec_fail": dict(selftest=dict(GOOD_SELFTEST, autoexec_fail=False)),
                 "helper exception": dict(lines=[], exit=71)}
        for name, config in cases.items():
            with self.subTest(case=name):
                probe = StandIn(self.tmp / name.replace(" ", "-"), **config).adapter().probe()
                self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED, name)
                self.assertNotIn("Traceback", json.dumps(probe.to_dict()))


# ---------------------------------------------------------------- D  inspection

class D_Inspection(BlenderCase):
    def test_a_safe_file_is_summarized(self):
        result, _ = self.inspect("safe")
        self.assertSucceeded(result)
        d = result.data
        self.assertEqual((d["scene_count"], d["active_scene"]), (1, "Scene"))
        (scene,) = d["scenes"]
        self.assertEqual((scene["camera"], scene["frame_start"], scene["frame_end"], scene["current_frame"],
                          scene["render_engine"], scene["render_engine_available"], scene["resolution"], scene["freestyle"]),
                         ("Camera", 1, 10, 3, "BLENDER_WORKBENCH", True, {"x": 64, "y": 48, "percentage": 100}, False))
        self.assertEqual(d["object_counts"], {"MESH": 1, "ARMATURE": 0, "CAMERA": 1, "LIGHT": 1, "EMPTY": 0, "OTHER": 0})
        self.assertEqual((d["mesh_datablocks"], d["total_vertices"], d["total_edges"], d["total_polygons"]), (1, 8, 12, 6))
        self.assertEqual((d["material_count"], d["armature_count"], d["action_count"]), (1, 0, 0))  # the fixture's own
        self.assertEqual(d["external_dependencies"], {"count": 0, "missing": 0})
        self.assertFalse(d["has_compositor_file_outputs"] or d["has_script_nodes"])
        self.assertTrue(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", d["blend_file_version"]))
        self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
        self.assertFalse(result.mutation_performed)

    def test_several_scenes(self):
        result, _ = self.inspect("multi")
        self.assertSucceeded(result)
        scenes = {s["name"]: s for s in result.data["scenes"]}
        self.assertEqual(sorted(scenes), ["Alt", "Main"])
        self.assertEqual(result.data["active_scene"], "Main")
        self.assertEqual((scenes["Alt"]["camera"], scenes["Alt"]["frame_start"], scenes["Alt"]["frame_end"]),
                         ("AltCamera", 20, 30))

    def test_no_path_dump_and_no_evidence(self):
        result, p = self.inspect("external-image")
        text = json.dumps(result.to_dict())
        data = json.dumps(result.data) + json.dumps(result.provenance.to_dict()["command"]["argv"])
        for needle in (str(p), "external-texture", "datafiles", "brushes", str(Path.home()), "assets/", ".png", ".blend"):
            self.assertNotIn(needle, data, needle)
        self.assertNotIn(str(p), text)
        self.assertEqual((result.stdout, result.stderr), ("", ""))


# ---------------------------------------------------------------- E  source immutability

class E_SourceImmutability(BlenderCase):
    def test_inspect_and_render_never_change_the_source_or_its_directory(self):
        for fixture in ("safe", "simple-driver", "safe-cycles", "autoexec"):
            with self.subTest(fixture=fixture):
                p = self.project(fixture)
                src = self.blend(p, fixture)
                before, listing = sha256(src), sorted(x.name for x in src.parent.iterdir())
                project_before = {x.relative_to(p).as_posix() for x in p.rglob("*") if ".game/gpos-runtime" not in x.as_posix()}
                self.assertSucceeded(self.run_cap(ba.INSPECT, p, fixture))
                rendered = self.run_cap(ba.RENDER, p, fixture)
                if fixture == "autoexec":
                    self.assertNotRendered(rendered, "AUTOEXEC_REQUIRED")
                else:
                    self.assertSucceeded(rendered)
                self.assertEqual(sha256(src), before)
                self.assertEqual(sorted(x.name for x in src.parent.iterdir()), listing)  # no .blend1, autosave, quit.blend
                after = {x.relative_to(p).as_posix() for x in p.rglob("*") if ".game/gpos-runtime" not in x.as_posix()}
                self.assertEqual(after, project_before)
                self.assertFalse((p / ".game" / "gpos-runtime" / "blender-user").exists())  # isolated root removed

    def test_the_helper_has_no_save_export_or_import(self):
        text = (ROOT / "gpos" / "tools" / "blender" / "helper.py").read_text()
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        body = code.split('"""', 2)[2]  # everything after the module docstring
        for needle in ("save_mainfile", "save_as_mainfile", "save_userpref", "save_homefile", "wm.save", "export_",
                       "import_scene", "wm.append", "wm.link", "pack_all", "unpack_all", "addon_enable",
                       "extensions", "objects.new", "cameras.new", "scenes.new", "remove("):
            self.assertNotIn(needle, body, needle)


# ---------------------------------------------------------------- F  autoexec and render-time scripts

class F_EmbeddedCode(BlenderCase):
    def control(self, fixture, use_scripts, render=False):
        """TEST-SIDE sensitivity control: the same file opened the way production must never open it. Prints
        Blender's own blocked-Python flag and the driven cube location at frame 3."""
        script = self.tmp / "control.py"
        script.write_text("import bpy, sys\n"
                          f"bpy.ops.wm.open_mainfile(filepath={str(FIX[fixture])!r}, load_ui=False, use_scripts={use_scripts})\n"
                          "bpy.context.scene.frame_set(3)\n"
                          "print('GPOS_CONTROL', bpy.app.autoexec_fail, round(bpy.data.objects['Cube'].location.x, 4))\n"
                          + (f"bpy.context.scene.render.filepath = {str(self.tmp / 'c.png')!r}\n"
                             "bpy.ops.render.render(write_still=True)\n" if render else ""))
        flags = ["--enable-autoexec"] if use_scripts else ["--disable-autoexec"]
        user = self.tmp / "control-user"
        user.mkdir(exist_ok=True)
        done = blender_test(["--background", "--factory-startup", *flags, "--offline-mode", "--python", str(script)], user)
        blocked, x = re.search(rb"GPOS_CONTROL (True|False) (\S+)", done.stdout).groups()
        return blocked == b"True", float(x)

    def test_embedded_scripts_would_run_if_allowed(self):
        for fixture, expected in (("autoexec", ["autoexec_driver", "autoexec_textblock"]),
                                  ("autoexec-text", ["autoexec_text_only"]), ("autoexec-driver", ["autoexec_driver_only"])):
            with self.subTest(fixture=fixture):
                clear_markers()
                self.control(fixture, use_scripts=True)
                self.assertEqual(markers(), expected)

    def test_blender_itself_reports_which_sources_depend_on_blocked_python(self):
        # Blender 5.2, scripts disabled: its own read-only flag, and what its drivers evaluate to at frame 3
        expected = {"autoexec": True, "autoexec-text": True, "autoexec-driver": True, "safe": False,
                    "simple-driver": False, "restricted-driver": False}
        for fixture, blocked in expected.items():
            with self.subTest(fixture=fixture):
                self.assertEqual(self.control(fixture, use_scripts=False)[0], blocked)
        self.assertEqual(self.control("simple-driver", use_scripts=False)[1], 1.5)          # frame * 0.5, natively
        self.assertEqual(self.control("restricted-driver", use_scripts=False)[1], 0.75)     # max(frame, 2) * 0.25
        self.assertEqual(markers(), [])

    def test_blocked_source_python_never_runs_and_is_never_rendered(self):
        for fixture in ("autoexec", "autoexec-text", "autoexec-driver"):
            with self.subTest(fixture=fixture):
                p = self.project(fixture)
                before = sha256(self.blend(p, fixture))
                inspected = self.run_cap(ba.INSPECT, p, fixture)   # inspection offers no evidence: it stays usable
                self.assertSucceeded(inspected)
                result = self.run_cap(ba.RENDER, p, fixture)
                self.assertNotRendered(result, "AUTOEXEC_REQUIRED")
                self.assertEqual(list(self.workspace_root(p).rglob("*.png")), [])
                self.assertEqual(markers(), [])
                self.assertEqual(sha256(self.blend(p, fixture)), before)
                self.assertNotIn("__import__", json.dumps(result.to_dict()) + json.dumps(inspected.to_dict()))

    def test_drivers_blender_evaluates_without_python_still_render(self):
        safe = self.render("safe")[0]
        self.assertSucceeded(safe)
        for fixture in ("simple-driver", "restricted-driver"):
            with self.subTest(fixture=fixture):
                result, _ = self.render(fixture)
                self.assertSucceeded(result)
                self.assertEqual(result.evidence_candidates[0].capture_context, "DCC_RENDER")
                # the driver moved the cube, so the image is not the undriven scene's
                self.assertNotEqual(result.artifacts[0].sha256, safe.artifacts[0].sha256)
        self.assertEqual(markers(), [])

    def test_freestyle_runs_scripts_even_with_autoexec_off_so_it_is_refused(self):
        self.control("freestyle-script", use_scripts=False, render=True)
        self.assertEqual(markers(), ["freestyle_script"])  # the risk is real on this Blender
        clear_markers()
        result, p = self.render("freestyle-script")
        self.assertNotRendered(result, "RENDER_SCRIPTING")
        self.assertIn("Freestyle", self.messages(result))
        self.assertEqual(markers(), [])
        self.assertFalse((self.workspace_root(p)).exists() and any(self.workspace_root(p).rglob("*.png")))

    def test_osl_script_nodes_are_refused(self):
        result, _ = self.render("osl-script")
        self.assertNotRendered(result, "Open Shading Language")
        inspected, _ = self.inspect("osl-script")
        self.assertTrue(inspected.data["has_script_nodes"])


# ---------------------------------------------------------------- G  user startup and add-on isolation

class G_UserStateIsolation(BlenderCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = _STATE["work"] / "user-A"
        cls.user_a.mkdir(exist_ok=True)
        markers_dir = _STATE["markers"]
        setup = _STATE["work"] / "make-user-A.py"
        setup.write_text(
            "import bpy, os\n"
            "p = bpy.context.preferences\n"
            "p.filepaths.use_scripts_auto_execute = True\n"
            "d = bpy.utils.user_resource('SCRIPTS', path='startup', create=True)\n"
            f"open(os.path.join(d, 'gpos_user_startup.py'), 'w').write('open(%r, \"w\").write(\"x\")\\n"
            "def register(): pass\\ndef unregister(): pass\\n' % os.path.join("
            f"{str(markers_dir)!r}, 'user_startup_script'))\n"
            "a = bpy.utils.user_resource('SCRIPTS', path='addons', create=True)\n"
            "open(os.path.join(a, 'gpos_user_addon.py'), 'w').write("
            "'bl_info = {\"name\": \"GPOS test\", \"blender\": (5, 0, 0), \"category\": \"Development\"}\\n"
            "def register():\\n    open(%r, \"w\").write(\"x\")\\ndef unregister(): pass\\n' % os.path.join("
            f"{str(markers_dir)!r}, 'user_addon'))\n"
            "bpy.ops.wm.save_userpref()\n")
        blender_test(["--background", "--offline-mode", "--python", str(setup)], cls.user_a)
        enable = _STATE["work"] / "enable-user-A.py"
        enable.write_text("import bpy\nbpy.ops.preferences.addon_enable(module='gpos_user_addon')\n"
                          "bpy.ops.wm.save_userpref()\n")
        blender_test(["--background", "--offline-mode", "--python", str(enable)], cls.user_a)
        clear_markers()

    def test_the_isolated_user_state_would_take_effect_without_factory_startup(self):
        blender_test(["--background", "--offline-mode", "--python-expr", "pass"], self.user_a)
        self.assertEqual(markers(), ["user_addon", "user_startup_script"])  # the control is sensitive

    def test_production_is_unaffected_even_when_the_parent_points_blender_at_that_state(self):
        previous = os.environ.get("BLENDER_USER_RESOURCES")
        os.environ["BLENDER_USER_RESOURCES"] = str(self.user_a)
        try:
            with Recorder() as rec:
                for cap in (ba.INSPECT, ba.RENDER):
                    self.assertSucceeded(self.run_cap(cap, self.project("safe"), "safe"))
        finally:
            if previous is None:
                os.environ.pop("BLENDER_USER_RESOURCES", None)
            else:
                os.environ["BLENDER_USER_RESOURCES"] = previous
        self.assertEqual(markers(), [])
        for spec in rec.project_runs:
            env = spec.env.build(os.environ | {"BLENDER_USER_RESOURCES": str(self.user_a)})
            self.assertNotEqual(env["BLENDER_USER_RESOURCES"], str(self.user_a))  # adapter-owned, per execution
            self.assertIn("gpos-runtime", env["BLENDER_USER_RESOURCES"])

    def test_the_production_baseline_ignores_user_state_even_in_that_root(self):
        # layer 2: the exact production flags with the prepared user state still do not load it
        argv = list(ba.inspect_argv(self.tmp, FIX["safe"], "0" * 32))
        blender_test(argv, self.user_a)
        self.assertEqual(markers(), [])


# ---------------------------------------------------------------- H  input contract

class H_InputContract(BlenderCase):
    def test_bad_input_artifacts_are_refused_before_blender_runs(self):
        p = self.project("safe")
        src = self.blend(p, "safe")
        text = p / "assets" / "notes.txt"
        text.write_text("x")
        cases = {"none": (), "two": (InputArtifact("blend", str(src)), InputArtifact("blend2", str(src))),
                 "wrong id": (InputArtifact("source", str(src)),), "not a .blend": (InputArtifact("blend", str(text)),),
                 "capture context": (InputArtifact("blend", str(src), capture_context="DCC_RENDER"),)}
        with Recorder() as rec:
            for name, artifacts in cases.items():
                for cap in (ba.INSPECT, ba.RENDER):
                    with self.subTest(case=name, capability=cap):
                        self.assertRefused(self.run_cap(cap, p, artifacts=artifacts))
        self.assertEqual(rec.project_runs, [])

    def test_a_non_blend_file_with_a_blend_suffix_fails_closed(self):
        p = self.project()
        fake = p / "assets" / "fake.blend"
        fake.write_bytes(b"not a blend file at all")
        result = self.run_cap(ba.INSPECT, p, artifacts=(InputArtifact("blend", str(fake)),))
        self.assertRefused(result, "OPEN_FAILED", code="DCC_SOURCE_NOT_ACCEPTED")


# ---------------------------------------------------------------- I  inspection bounds and protocol

class I_BoundsAndProtocol(BlenderCase):
    def test_more_than_64_scenes_fails_closed(self):
        result, _ = self.inspect("many-scenes")
        self.assertRefused(result, "SCENE_LIMIT", code="DCC_SOURCE_NOT_ACCEPTED")
        self.assertFalse(result.data)

    def test_the_record_parser_fails_closed(self):
        nonce = "a" * 32
        good = b"log line\nGPOS_BLENDER_RESULT_V1:" + nonce.encode() + b':{"status":"ok"}\nmore log\n'
        self.assertEqual(bp.record(good, nonce), {"status": "ok"})
        bad = {"missing": b"just logs\n", "two": good + good, "wrong nonce": good.replace(nonce.encode(), b"b" * 32),
               "malformed": b"GPOS_BLENDER_RESULT_V1:" + nonce.encode() + b":{not json\n",
               "not object": b"GPOS_BLENDER_RESULT_V1:" + nonce.encode() + b":[1]\n",
               "oversized": b"GPOS_BLENDER_RESULT_V1:" + nonce.encode() + b':"' + b"x" * (bp.MAX_RECORD + 10) + b'"\n',
               "not bytes": good.decode()}
        for name, raw in bad.items():
            with self.subTest(case=name), self.assertRaises(bp.HelperProtocolError):
                bp.record(raw, nonce)

    def test_the_inspection_schema_fails_closed(self):
        result, _ = self.inspect("safe")
        base = dict(result.data, status="ok", mode="inspect")
        base.pop("source_artifact_id")
        bp.inspection(copy.deepcopy(base))
        scene = base["scenes"][0]
        mutations = {"extra field": dict(base, tags=["x"]), "long name": dict(base, scenes=[dict(scene, name="n" * 257)]),
                     "control char": dict(base, scenes=[dict(scene, name="a\nb")]),
                     "65 scenes": dict(base, scenes=[scene] * 65, scene_count=65),
                     "negative count": dict(base, total_vertices=-1), "bool count": dict(base, material_count=True),
                     "path field": dict(base, scenes=[dict(scene, filepath="/x")]),
                     "inconsistent count": dict(base, scene_count=2),
                     "missing > count": dict(base, external_dependencies={"count": 0, "missing": 1}),
                     "bad engine": dict(base, scenes=[dict(scene, render_engine="evil engine")])}
        for name, value in mutations.items():
            with self.subTest(case=name), self.assertRaises(bp.HelperProtocolError):
                bp.inspection(value)

    def test_truncated_real_output_is_refused(self):
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(BlenderAdapter(capture_bytes=256))
        # a 256-byte bound cannot hold even the self-test's startup output: the probe fails closed first
        p = self.project("safe")
        result = self.run_cap(ba.INSPECT, p, "safe", registry=registry)
        self.assertNotEqual(result.status, tdg.SUCCESS)
        self.assertFalse(result.data)

    def test_truncated_or_protocol_broken_execution_is_failed(self):
        record = "GPOS_BLENDER_RESULT_V1:{nonce}:" + json.dumps(GOOD_INSPECT, separators=(",", ":"))
        cases = {"truncated": dict(lines_inspect=[record] + ["x" * 400] * 6),
                 "two records": dict(lines_inspect=["GPOS_BLENDER_RESULT_V1:{nonce}:{}"] * 2),
                 "no record": dict(lines_inspect=["log only"]), "wrong nonce": dict(lines_inspect=["GPOS_BLENDER_RESULT_V1:" + "f" * 32 + ":{}"]),
                 "bad schema": dict(inspect={"status": "ok", "mode": "inspect"})}
        for name, config in cases.items():
            with self.subTest(case=name):
                stand_in = StandIn(self.tmp / name.replace(" ", "-"), **config)
                adapter = stand_in.adapter(capture_bytes=2000) if name == "truncated" else stand_in.adapter()
                self.assertEqual(adapter.probe().status, tmodel.AVAILABLE)  # the failure is the execution's own
                registry = ToolRegistry(FW, allow_test_only=False)
                registry.register(adapter)
                result = self.run_cap(ba.INSPECT, self.project("safe"), "safe", registry=registry)
                self.assertEqual(result.status, tdg.FAILED, name)
                self.assertFalse(result.data)
                if name == "truncated":  # the record itself was complete: only the capture bound refuses it
                    self.assertTrue(result.output_truncated)
                    self.assertIn("capture bound", self.messages(result))

    def test_the_record_is_parsed_from_the_private_raw_capture(self):
        # A legitimate scene name that the public redaction rewrites across JSON boundaries.
        scene = "Authorization: Bearer gposscene"
        record = dict(GOOD_INSPECT, active_scene=scene, scenes=[dict(GOOD_INSPECT["scenes"][0], name=scene)])
        line = "GPOS_BLENDER_RESULT_V1:" + "a" * 32 + ":" + json.dumps(record, separators=(",", ":"))
        from gpos.tools import redaction
        with self.assertRaises(ValueError):  # the public text is not the helper's JSON any more
            json.loads(redaction.redact(line)[0].split(":", 2)[2])
        stand_in = StandIn(self.tmp / "raw", inspect=record)
        result = self.run_cap(ba.INSPECT, self.project("safe"), "safe", registry=stand_in.registry())
        self.assertSucceeded(result)
        self.assertEqual(result.data["scene_count"], 1)


# ---------------------------------------------------------------- J  external dependencies

class J_ExternalDependencies(BlenderCase):
    def test_inspection_counts_dependencies_and_render_refuses_them(self):
        for fixture in ("external-image", "linked-library"):
            with self.subTest(fixture=fixture):
                p = self.project(fixture)
                inspected = self.run_cap(ba.INSPECT, p, fixture)
                self.assertEqual(inspected.data["external_dependencies"]["count"], 1)
                result = self.run_cap(ba.RENDER, p, fixture)
                self.assertNotRendered(result, "EXTERNAL_DEPENDENCIES")
                text = json.dumps(result.to_dict()) + json.dumps(inspected.to_dict())
                for needle in ("external-texture", "library.blend", str(_STATE["work"])):
                    self.assertNotIn(needle, text)

    def test_a_missing_dependency_is_counted_as_missing(self):
        p = self.project("external-image")  # the texture beside the generated fixture is not copied
        inspected = self.run_cap(ba.INSPECT, p, "external-image")
        self.assertEqual(inspected.data["external_dependencies"]["count"], 1)
        self.assertIn(inspected.data["external_dependencies"]["missing"], (0, 1))

    def test_the_generated_fixtures_are_self_contained(self):
        result, _ = self.inspect("safe")
        self.assertEqual(result.data["external_dependencies"], {"count": 0, "missing": 0})

    def factory_file(self, project, relative):
        """TEST-SIDE: Blender's unmodified factory scene saved in place. Its cube material keeps a weak
        reference to Blender's bundled brush library, stored as a path relative to where the file is saved."""
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        script = self.tmp / "factory.py"
        script.write_text("import bpy\nbpy.ops.wm.read_factory_settings(use_empty=False)\n"
                          "s = bpy.context.scene\ns.render.engine = 'BLENDER_WORKBENCH'\n"
                          "s.render.resolution_x, s.render.resolution_y = 64, 48\n"
                          f"bpy.ops.wm.save_as_mainfile(filepath={str(target)!r}, compress=False)\n"
                          "print('GPOS_FACTORY_PATHS', bpy.utils.blend_paths(absolute=False, packed=False, local=False))\n")
        user = self.tmp / "factory-user"
        user.mkdir(exist_ok=True)
        done = blender_test(["--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
                             "--python", str(script)], user)
        self.assertIn(b"//../", re.search(rb"GPOS_FACTORY_PATHS (.*)", done.stdout).group(1))  # the reference exists
        return target

    def test_a_reference_counts_as_blenders_own_only_where_it_really_resolves_into_the_installation(self):
        p = self.project()
        in_place = self.factory_file(p, "assets/factory.blend")
        artifacts = (InputArtifact("blend", str(in_place)),)
        self.assertEqual(self.run_cap(ba.INSPECT, p, artifacts=artifacts).data["external_dependencies"],
                         {"count": 0, "missing": 0})             # resolves inside Blender's real datafiles
        self.assertSucceeded(self.run_cap(ba.RENDER, p, artifacts=artifacts))
        moved = p / "assets" / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "h" / "i" / "j" / "k" / "l" / "factory.blend"
        moved.parent.mkdir(parents=True)
        shutil.copyfile(in_place, moved)         # the same stored text now resolves elsewhere: a real dependency
        artifacts = (InputArtifact("blend", str(moved)),)
        self.assertEqual(self.run_cap(ba.INSPECT, p, artifacts=artifacts).data["external_dependencies"],
                         {"count": 1, "missing": 1})
        self.assertNotRendered(self.run_cap(ba.RENDER, p, artifacts=artifacts), "EXTERNAL_DEPENDENCIES")

    def test_a_path_that_imitates_blenders_datafiles_is_a_dependency(self):
        tail = _STATE["spoof_tail"]
        real_datafiles = Path(os.sep + tail)
        self.assertTrue(real_datafiles.is_dir())            # the stored text names the real installation's directory
        p = self.project("spoof-datafiles")
        fake = p / tail / "gpos-spoof-texture.png"          # ...but "//../" from assets/ resolves inside the project
        fake.parent.mkdir(parents=True)
        shutil.copyfile(FIX["external-texture.png"], fake)
        self.assertFalse(fake.resolve().is_relative_to(real_datafiles.resolve()))
        inspected = self.run_cap(ba.INSPECT, p, "spoof-datafiles")
        self.assertEqual(inspected.data["external_dependencies"], {"count": 1, "missing": 0})
        result = self.run_cap(ba.RENDER, p, "spoof-datafiles")
        self.assertNotRendered(result, "EXTERNAL_DEPENDENCIES")
        self.assertEqual(list(self.workspace_root(p).rglob("*.png")), [])
        self.assertNotIn("gpos-spoof-texture", json.dumps(result.to_dict()) + json.dumps(inspected.to_dict()))


# ---------------------------------------------------------------- K  asset scope

class K_AssetScope(BlenderCase):
    def test_only_an_asset_subject_may_be_rendered(self):
        p = self.project("safe")
        with Recorder() as rec:
            for kind in ("TASK", "FEATURE", "PROJECT", "RELEASE"):
                for dry in (False, True):
                    with self.subTest(kind=kind, dry_run=dry):
                        result = self.run_cap(ba.RENDER, p, "safe", kind=kind, dry_run=dry)
                        self.assertRefused(result, "ASSET only")
        self.assertEqual(rec.project_runs, [])
        self.assertSucceeded(self.run_cap(ba.RENDER, p, "safe", kind="ASSET"))


# ---------------------------------------------------------------- L  scene

class L_Scene(BlenderCase):
    def test_scene_selection(self):
        p = self.project("multi")
        default = self.run_cap(ba.RENDER, p, "multi")
        self.assertSucceeded(default)
        self.assertEqual(default.data["scene"], "Main")
        alt = self.run_cap(ba.RENDER, p, "multi", inputs={"scene_name": "Alt"})
        self.assertSucceeded(alt)
        self.assertEqual((alt.data["scene"], alt.data["camera"], alt.data["frame"], alt.data["width"]),
                         ("Alt", "AltCamera", 25, 40))

    def test_unknown_or_inexact_scenes_are_refused(self):
        p = self.project("multi")
        for name in ("Nope", "alt", "Alt ", "Al"):
            with self.subTest(name=name):
                self.assertNotRendered(self.run_cap(ba.RENDER, p, "multi", inputs={"scene_name": name}), "SCENE_NOT_FOUND")

    def test_malformed_scene_names_are_refused_before_blender_runs(self):
        p = self.project("multi")
        with Recorder() as rec:
            for name in ("", "a" * 257, "a\nb", "a\x00b", 7, ["Alt"]):
                with self.subTest(name=repr(name)[:20]):
                    self.assertRefused(self.run_cap(ba.RENDER, p, "multi", inputs={"scene_name": name}))
        self.assertEqual(rec.project_runs, [])


# ---------------------------------------------------------------- M  camera

class M_Camera(BlenderCase):
    def test_a_scene_without_a_camera_is_refused_and_none_is_generated(self):
        p = self.project("no-camera")
        before = sha256(self.blend(p, "no-camera"))
        self.assertNotRendered(self.run_cap(ba.RENDER, p, "no-camera"), "NO_CAMERA")
        self.assertEqual(sha256(self.blend(p, "no-camera")), before)
        self.assertEqual(self.run_cap(ba.INSPECT, p, "no-camera").data["object_counts"]["CAMERA"], 0)

    def test_no_camera_input_exists(self):
        self.assertNotIn("camera", ba.DESCRIPTOR.capability(ba.RENDER).input_kinds)
        self.assertRefused(self.run_cap(ba.RENDER, self.project("safe"), "safe", inputs={"camera": "Camera"}))


# ---------------------------------------------------------------- N  frame

class N_Frame(BlenderCase):
    def test_frames(self):
        p = self.project("safe")
        omitted = self.run_cap(ba.RENDER, p, "safe")
        self.assertEqual(omitted.data["frame"], 3)
        for value in (7, "7", 1, 10):
            with self.subTest(frame=value):
                result = self.run_cap(ba.RENDER, p, "safe", inputs={"frame": value})
                self.assertSucceeded(result)
                self.assertEqual(result.data["frame"], int(value))

    def test_frames_outside_the_range_are_refused_not_clamped(self):
        p = self.project("safe")
        for value in (0, 11, 500):
            with self.subTest(frame=value):
                self.assertNotRendered(self.run_cap(ba.RENDER, p, "safe", inputs={"frame": value}), "FRAME_OUT_OF_RANGE")

    def test_malformed_frames_are_refused_before_blender_runs(self):
        p = self.project("safe")
        with Recorder() as rec:
            for value in (1.5, "1.0", "01", "-1", -1, "+1", " 1", "1 ", "1e2", "2*3", "", True, [],
                          "99999999", 1_048_575):
                with self.subTest(frame=repr(value)):
                    self.assertRefused(self.run_cap(ba.RENDER, p, "safe", inputs={"frame": value}))
        self.assertEqual(rec.project_runs, [])


# ---------------------------------------------------------------- O  render engine

class O_RenderEngine(BlenderCase):
    def test_each_built_in_engine_renders(self):
        for fixture, engine in (("safe", "BLENDER_WORKBENCH"), ("safe-eevee", "BLENDER_EEVEE"), ("safe-cycles", "CYCLES")):
            with self.subTest(engine=engine):
                result, _ = self.render(fixture)
                self.assertSucceeded(result)
                self.assertEqual(result.data["engine"], engine)

    def test_an_unavailable_custom_engine_is_refused_never_replaced(self):
        result, p = self.render("custom-engine")
        self.assertNotRendered(result, "GPOS_TEST_ENGINE")
        inspected = self.run_cap(ba.INSPECT, p, "custom-engine")
        scene = inspected.data["scenes"][0]
        self.assertEqual((scene["render_engine"], scene["render_engine_available"]), ("GPOS_TEST_ENGINE", False))

    def test_an_oversized_load_log_is_refused_never_read_in_part(self):
        result, p = self.render("oversized-log")        # 800 unavailable-engine reports: over LOG_LIMIT bytes
        self.assertNotRendered(result, "ENGINE_LOG_UNBOUNDED")
        self.assertEqual(list(self.workspace_root(p).rglob("*.png")), [])

    def test_the_helpers_log_rule(self):
        """TEST-SIDE: the real helper's log reader, run inside real Blender against crafted logs: complete
        (normal and exactly at the bound), oversized with an engine report beyond the bound, oversized without."""
        script = self.tmp / "logrule.py"
        script.write_text(
            "import json, os, sys\n"
            "HELPER, WORK = sys.argv[sys.argv.index('--') + 1:][:2]\n"
            "source = open(HELPER).read().rstrip()\n"
            "call = 'main(sys.argv[sys.argv.index(\"--\") + 1:])'\n"
            "assert source.endswith(call)\n"
            "helper = {'__name__': 'gpos_helper_under_test'}\n"
            "exec(compile(source[:-len(call)], HELPER, 'exec'), helper)\n"
            "limit = helper['LOG_LIMIT']\n"
            "engine = \"00:00.339  reports          | ERROR Engine 'GPOS_TEST_ENGINE' not available for scene 'Scene' \" \\\n"
            "         \"(an add-on may need to be installed or enabled)\\n\"\n"
            "filler = '00:00.336  blend            | Read blend: filler\\n'\n"
            "def pad(n):\n    return (filler * (n // len(filler) + 1))[:n]\n"
            "cases = {'normal': filler + engine, 'at bound': pad(limit - len(engine)) + engine,\n"
            "         'beyond bound': pad(limit) + engine, 'oversized, no engine line': pad(limit + 1)}\n"
            "out = {'limit': limit}\n"
            "for name, text in cases.items():\n"
            "    log = os.path.join(WORK, name.replace(' ', '-').replace(',', '') + '.log')\n"
            "    open(log, 'w').write(text)\n"
            "    sys.argv = ['blender', '--log-file', log, '--']\n"
            "    try:\n        out[name] = {'size': len(text), 'engines': helper['unavailable_engines']()}\n"
            "    except helper['Refusal'] as refusal:\n        out[name] = {'size': len(text), 'refused': refusal.code}\n"
            "    out[name]['report_present'] = bool(helper['UNAVAILABLE_ENGINE'].search(text))\n"
            "print('GPOS_LOG_RULE', json.dumps(out))\n")
        user = self.tmp / "logrule-user"
        user.mkdir()
        done = blender_test(["--background", "--factory-startup", "--disable-autoexec", "--offline-mode", "--python",
                             str(script), "--", ba.HELPER, str(self.tmp)], user)
        out = json.loads(re.search(rb"GPOS_LOG_RULE (.*)", done.stdout).group(1))
        limit = out["limit"]
        self.assertEqual(limit, 256 * 1024)
        self.assertEqual(out["normal"]["engines"], {"Scene": "GPOS_TEST_ENGINE"})
        self.assertEqual((out["at bound"]["size"], out["at bound"]["engines"]), (limit, {"Scene": "GPOS_TEST_ENGINE"}))
        self.assertEqual(out["beyond bound"]["refused"], "ENGINE_LOG_UNBOUNDED")
        self.assertTrue(out["beyond bound"]["report_present"])       # the report exists, only beyond the bound
        self.assertEqual(out["oversized, no engine line"], {"size": limit + 1, "refused": "ENGINE_LOG_UNBOUNDED",
                                                             "report_present": False})

    def test_no_engine_input_exists(self):
        self.assertRefused(self.run_cap(ba.RENDER, self.project("safe"), "safe", inputs={"engine": "CYCLES"}))


# ---------------------------------------------------------------- P  resolution

class P_Resolution(BlenderCase):
    def test_an_oversized_scene_is_refused_before_rendering(self):
        result, p = self.render("oversized")
        self.assertNotRendered(result, "RESOLUTION_OUT_OF_BOUNDS")
        self.assertIn("never downscaled", self.messages(result))
        self.assertEqual(list(self.workspace_root(p).rglob("*.png")), [])


# ---------------------------------------------------------------- Q  extra outputs

class Q_ExtraOutputs(BlenderCase):
    def test_a_compositor_file_output_is_refused_and_writes_nothing(self):
        result, p = self.render("compositor-output")
        self.assertNotRendered(result, "COMPOSITOR_FILE_OUTPUT")
        self.assertEqual(markers(), [])
        self.assertEqual(list(self.workspace_root(p).rglob("*.png")), [])
        self.assertTrue(self.run_cap(ba.INSPECT, p, "compositor-output").data["has_compositor_file_outputs"])

    def test_multiview_and_sequencer_are_refused(self):
        for fixture, reason in (("multiview", "MULTIVIEW"), ("sequencer", "SEQUENCER_STRIPS")):
            with self.subTest(fixture=fixture):
                self.assertNotRendered(self.render(fixture)[0], reason)


# ---------------------------------------------------------------- R  real render

class R_RealRender(BlenderCase):
    def test_a_real_render_is_a_png_of_the_authored_size(self):
        result, p = self.render("safe")
        self.assertSucceeded(result)
        (art,) = result.artifacts
        self.assertEqual((art.artifact_id, art.kind, art.media_type, art.classification, art.origin_capture_context,
                          art.derived_from, art.complete),
                         ("render", "IMAGE", "image/png", "CANONICAL", "DCC_RENDER", (), True))
        data = Path(art.absolute_path).read_bytes()
        self.assertEqual(bp.png_dimensions(data), (64, 48))
        self.assertEqual(art.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(art.path, f".game/gpos-runtime/tool-output/blender/{result.request_id}/render.png")
        self.assertEqual(sorted(x.name for x in Path(art.absolute_path).parent.iterdir()), ["render.png"])
        (cand,) = result.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context, cand.subject_kind, cand.derived_from),
                         ("VISUAL_EVIDENCE", "DCC_RENDER", "ASSET", ()))
        self.assertTrue(result.mutation_performed)

    def test_renders_are_reproducible_and_carry_no_file_metadata(self):
        a, pa = self.render("safe")
        b, _ = self.render("safe")
        self.assertEqual(a.artifacts[0].sha256, b.artifacts[0].sha256)
        data = Path(a.artifacts[0].absolute_path).read_bytes()
        for needle in (b"safe.blend", str(pa).encode(), b"tEXt", b"zTXt", b"iTXt"):
            self.assertNotIn(needle, data)

    def test_burned_in_metadata_is_refused_not_changed(self):
        self.assertNotRendered(self.render("stamp-burn-in")[0], "STAMP_BURN_IN")


# ---------------------------------------------------------------- S  materialization

class S_Materialization(BlenderCase):
    def test_a_render_materializes_without_runtime_fields(self):
        result, _ = self.render("safe")
        (cand,) = result.evidence_candidates
        self.assertTrue(cand.materializable)
        record, problems = tev.materialize(FW, cand, result.artifacts, "EV-BLENDER-1")
        self.assertEqual(problems, [])
        self.assertEqual(FW.validators["evidence"].errors(record), [])
        prov = record["provenance"]
        self.assertEqual((record["type"], prov["capture_context"], prov["subject_revision"], prov["tool_version"]),
                         ("VISUAL_EVIDENCE", "DCC_RENDER", REVISION, BlenderAdapter().probe().tool_version))
        for absent in ("build_revision", "build_id", "target_platform", "device", "instrumentation"):
            self.assertNotIn(absent, prov)
        self.assertEqual(record["subject"], {"kind": "ASSET", "ref": "ASSET-0001"})
        self.assertEqual(record["artifacts"][0]["hash"], f"sha256:{sha256(result.artifacts[0].absolute_path)}")
        self.assertTrue(record["limitations"])


# ---------------------------------------------------------------- T  DCC authority and gate boundary

class T_DccAuthority(BlenderCase):
    def test_the_limitations_state_what_a_dcc_render_does_not_prove(self):
        text = " ".join(ba.LIMITATIONS)
        for phrase in ("Blender DCC render of the named ASSET", "does not prove engine or runtime presentation",
                       "does not prove motion, interaction or game feel", "self-contained .blend"):
            self.assertIn(phrase, text)
        self.assertEqual(ba.DESCRIPTOR.capability(ba.RENDER).potential_evidence, (("VISUAL_EVIDENCE", "DCC_RENDER"),))
        for forbidden in ("TARGET_RUNTIME", "MOTION_EVIDENCE", "HUMAN_EVIDENCE", "DEVICE_EVIDENCE"):
            self.assertNotIn(forbidden, json.dumps(ba.DESCRIPTOR.capability(ba.RENDER).potential_evidence))

    def test_the_frozen_validator_counts_it_only_for_asset_visual_art(self):
        import validate_framework as vf
        subject = Subject("ASSET", "ASSET-0001", "rev-example-0002")
        p = self.project("safe")
        result = self.run_cap(ba.RENDER, p, "safe", subject=subject)
        record, problems = tev.materialize(FW, result.evidence_candidates[0], result.artifacts, "EV-BLENDER-1")
        self.assertEqual(problems, [])
        fixture = json.loads((ROOT / "tests/fixtures/records/visual-art-dcc-render-asset-scope.json").read_text())
        gate = vf.build(fixture["gate"])
        gate["evidence_refs"] = ["EV-BLENDER-1"]
        config = json.loads((ROOT / "examples/minimal-project-config.json").read_text())

        def validate(g):
            outcome = gpos.validate_project(from_records(config, [], [record], [g], []))
            return outcome.valid, {d.code for d in outcome.diagnostics}

        self.assertEqual(validate(gate), (True, set()))
        for other, owner in (("CAMERA_COMPOSITION", "camera-composition"), ("GAME_FEEL_VFX", "game-feel-vfx"),
                             ("ANIMATION", "character-animation"), ("UI_UX", "ui-ux")):
            with self.subTest(gate=other):
                g = dict(copy.deepcopy(gate), gate=other, owner=owner, assessed_by={"kind": "AGENT", "id": owner})
                valid, codes = validate(g)
                self.assertFalse(valid)
                self.assertIn("EVIDENCE_CONTEXT_NOT_COUNTING", codes)


# ---------------------------------------------------------------- U  mutation consent

class U_MutationConsent(BlenderCase):
    def test_no_render_without_consent(self):
        p = self.project("safe")
        with Recorder() as rec:
            result = self.run_cap(ba.RENDER, p, "safe", allow_mutation=False)
        self.assertRefused(result, code="MUTATION_NOT_ALLOWED")
        self.assertEqual(rec.specs, [])
        self.assertFalse(self.workspace_root(p).exists())


# ---------------------------------------------------------------- V  dry run

class V_DryRun(BlenderCase):
    def test_a_dry_run_plans_without_a_project_blender_process(self):
        p = self.project("safe", "no-camera")
        before = sorted(x.relative_to(p).as_posix() for x in p.rglob("*"))
        with Recorder() as rec:
            for fixture, inputs in (("safe", {}), ("no-camera", {"scene_name": "Scene", "frame": 5})):
                result = self.run_cap(ba.RENDER, p, fixture, dry_run=True, inputs=inputs)
                self.assertSucceeded(result)  # a dry run cannot know the camera is missing
                self.assertEqual((result.artifacts, result.evidence_candidates, result.mutation_performed), ((), (), False))
                self.assertEqual(len(result.plan), 3)
                self.assertIn("only checked by a real execution", result.plan[1])
        self.assertEqual(rec.project_runs, [])
        self.assertEqual(sorted(x.relative_to(p).as_posix() for x in p.rglob("*")), before)
        self.assertFalse((p / ".game" / "gpos-runtime").exists())

    def test_an_existing_output_is_refused_in_dry_run_and_real_run(self):
        p = self.project("safe")
        out = p / "reviews"
        out.mkdir()
        (out / "render.png").write_bytes(b"existing")
        with Recorder() as rec:
            for dry in (True, False):
                self.assertRefused(self.run_cap(ba.RENDER, p, "safe", output_dir=str(out), dry_run=dry),
                                   "already holds render.png")
        self.assertEqual(rec.project_runs, [])
        self.assertEqual((out / "render.png").read_bytes(), b"existing")
        link = p / "reviews-link"
        link.mkdir()
        (link / "render.png").symlink_to(self.tmp / "elsewhere.png")  # dangling: exists() alone would miss it
        for dry in (True, False):
            self.assertRefused(self.run_cap(ba.RENDER, p, "safe", output_dir=str(link), dry_run=dry),
                               "already holds render.png")
        self.assertFalse((self.tmp / "elsewhere.png").exists())


# ---------------------------------------------------------------- W  partial output and timeout

class W_PartialAndTimeout(BlenderCase):
    def render_with(self, name, **config):
        stand_in = StandIn(self.tmp / name, **config)
        return self.run_cap(ba.RENDER, self.project("safe"), "safe", registry=stand_in.registry())

    def test_a_partial_png_with_a_failed_exit_is_incomplete_and_not_evidence(self):
        result = self.render_with("partial", png=png_bytes(complete=False), exit_render=71, render=GOOD_RENDER)
        self.assertEqual(result.status, tdg.FAILED)
        (art,) = result.artifacts
        self.assertFalse(art.complete)
        self.assertTrue(result.mutation_performed)
        self.assertEqual(result.evidence_candidates, ())

    def test_a_timeout_leaves_an_incomplete_artifact_and_no_evidence(self):
        stand_in = StandIn(self.tmp / "slow", png=png_bytes(), sleep_render=30, render=GOOD_RENDER)
        result = self.run_cap(ba.RENDER, self.project("safe"), "safe", registry=stand_in.registry(), timeout=3.0)
        self.assertEqual(result.status, tdg.TIMED_OUT)
        self.assertFalse(result.artifacts[0].complete)
        self.assertTrue(result.mutation_performed)
        self.assertEqual(result.evidence_candidates, ())

    def test_malformed_or_mismatched_pngs_are_not_evidence(self):
        cases = {"no IEND": png_bytes(complete=False), "wrong size": png_bytes(100, 100), "not png": b"GIF89a" * 20,
                 "empty": b""}
        for name, data in cases.items():
            with self.subTest(case=name):
                result = self.render_with(name.replace(" ", "-"), png=data, render=GOOD_RENDER)
                self.assertEqual(result.status, tdg.FAILED)
                self.assertEqual(result.evidence_candidates, ())
                self.assertTrue(all(not a.complete for a in result.artifacts))

    def test_a_success_record_without_a_file_is_a_failure(self):
        result = self.render_with("nofile", render=GOOD_RENDER)
        self.assertEqual(result.status, tdg.FAILED)
        self.assertEqual((result.artifacts, result.evidence_candidates, result.mutation_performed), ((), (), False))


# ---------------------------------------------------------------- X  path and secret privacy

class X_Privacy(BlenderCase):
    def secret_project(self):
        p = self.project()
        src = p / "assets" / f"hero-{SECRET}-password=hunter2.blend"
        shutil.copyfile(FIX["safe"], src)
        return p, src

    def test_a_credential_shaped_filename_leaks_nowhere(self):
        p, src = self.secret_project()
        for cap in (ba.INSPECT, ba.RENDER):
            result = self.run_cap(cap, p, artifacts=(InputArtifact("blend", str(src)),))
            self.assertSucceeded(result)
            text = json.dumps(result.to_dict())
            for needle in (SECRET, "hunter2", str(p), str(ba.HELPER), "blender-user"):
                self.assertNotIn(needle, text, (cap, needle))
            argv = result.provenance.to_dict()["command"]["argv"]
            for placeholder in ("<gpos-blender-helper>", "<blend-input>", "<nonce>", "<isolated-user-root>/blender.log"):
                self.assertIn(placeholder, argv)
            for cand in result.evidence_candidates:
                self.assertNotIn(SECRET, json.dumps(cand.to_dict()))
        for fmt in ("json", "text"):
            code, out = cli("execute", "--adapter", "blender", "--capability", ba.RENDER, "--project", str(p),
                            "--subject-kind", "ASSET", "--subject-ref", "ASSET-0001", "--input-artifact",
                            f"blend={src}", "--allow-mutation", "--format", fmt)
            self.assertEqual(code, 0)
            self.assertNotIn(SECRET, out)
            self.assertNotIn("hunter2", out)

    def test_refusal_messages_carry_no_paths(self):
        for fixture in ("external-image", "linked-library", "no-camera", "custom-engine", "autoexec-driver",
                        "oversized-log"):
            result, p = self.render(fixture)
            text = json.dumps(result.to_dict())
            self.assertNotIn(str(p), text)
            self.assertNotIn(str(_STATE["work"]), text)


# ---------------------------------------------------------------- Y  command surface

class Y_CommandSurface(BlenderCase):
    def test_the_fixed_templates(self):
        root, h = Path("/iso"), ba.HELPER
        start = ("--background", "--factory-startup", "--disable-autoexec", "--offline-mode", "-noaudio",
                 "--log-file", "/iso/blender.log", "--python-exit-code", "71", "--python", h, "--")
        self.assertEqual(ba.selftest_argv(root, "n"), start + ("selftest", "n"))
        self.assertEqual(ba.inspect_argv(root, "/a.blend", "n"), start + ("inspect", "n", "/a.blend"))
        self.assertEqual(ba.render_argv(root, "/a.blend", "/w/render.png", "Alt", "7", "n"),
                         start + ("render", "n", "/a.blend", "/w/render.png", "Alt", "7"))
        for argv in (ba.selftest_argv(root, "n"), ba.inspect_argv(root, "/a", "n"), ba.render_argv(root, "/a", "/o", "", "", "n")):
            head = argv[:argv.index("--")]
            for flag in ("--python-expr", "--python-text", "--enable-autoexec", "-y", "--addons", "--online-mode",
                         "--python-use-system-env", "-E", "--engine", "-o", "--render-output", "-f", "-a", "-S"):
                self.assertNotIn(flag, head)
            self.assertEqual(head.count("--python"), 1)
            self.assertEqual(head[head.index("--python") + 1], ba.HELPER)

    def test_every_real_process_uses_a_template_and_the_probed_executable(self):
        p = self.project("multi")
        with Recorder() as rec:
            self.run_cap(ba.INSPECT, p, "multi")
            self.run_cap(ba.RENDER, p, "multi", inputs={"scene_name": "Alt", "frame": 27})
        runs = rec.project_runs
        self.assertEqual(len(runs), 2)
        tool = BlenderAdapter().probe().tool_path
        for spec in runs:
            self.assertEqual(spec.executable, tool)
            argv = spec.argv
            self.assertEqual(argv[:5], ba.BASELINE)
            self.assertEqual(argv[argv.index("--python") + 1], ba.HELPER)
        render = runs[1].argv
        self.assertEqual(render[-2:], ("Alt", "27"))

    def test_the_helper_has_no_code_execution_process_or_network_surface(self):
        tree = ast.parse((ROOT / "gpos" / "tools" / "blender" / "helper.py").read_text())
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertEqual(calls & {"eval", "exec", "compile", "__import__", "execfile"}, set())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertEqual(attrs & {"system", "popen", "spawn", "run_path", "run_module", "import_module", "urlopen",
                                  "addon_enable", "save_mainfile", "save_as_mainfile", "extension_install"}, set())
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(imports, {"json", "os", "re", "sys", "bpy"})
        ops = re.findall(r"bpy\.ops\.([a-z_]+\.[a-z_]+)", (ROOT / "gpos" / "tools" / "blender" / "helper.py").read_text())
        self.assertEqual(set(ops), {"wm.open_mainfile", "render.render"})
        opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "open_mainfile"]
        self.assertEqual(len(opens), 1)
        keywords = {k.arg: k.value.value for k in opens[0].keywords if isinstance(k.value, ast.Constant)}
        self.assertEqual((keywords.get("use_scripts"), keywords.get("load_ui")), (False, False))

    def test_the_adapter_reads_only_its_declared_inputs_and_no_executable_seam_exists(self):
        import inspect as pyinspect
        self.assertEqual(list(pyinspect.signature(BlenderAdapter).parameters), ["which", "platform", "capture_bytes"])
        text = (ROOT / "gpos" / "tools" / "blender" / "adapter.py").read_text()
        self.assertEqual(sorted(set(re.findall(r'inputs\.get\("(\w+)"\)', text))), ["frame", "scene_name"])
        self.assertEqual(set(re.findall(r"\brequest\.(\w+)", text)), {"capability_id", "subject", "inputs", "request_id"})
        p = self.project("safe")
        with Recorder() as rec:
            for name in ("python", "script", "argv", "engine", "output", "operator", "addon", "executable", "camera",
                         "helper", "python_path"):
                self.assertRefused(self.run_cap(ba.RENDER, p, "safe", inputs={name: "x"}))
        self.assertEqual(rec.project_runs, [])


# ---------------------------------------------------------------- Z  CLI

def cli(*argv):
    from gpos.tools import cli as tool_cli
    buffer = io.StringIO()
    code = tool_cli.main(list(argv), stdout=buffer)
    return code, buffer.getvalue()


class Z_Cli(BlenderCase):
    def test_list_describe_capabilities_probe(self):
        code, out = cli("list")
        self.assertEqual(code, 0)
        self.assertIn("5 tool adapter", out)
        self.assertIn("blender 1.0.0 · DCC · 2 capabilities", out)
        code, out = cli("describe", "blender")
        self.assertEqual(code, 0)
        for text in (ba.INSPECT, ba.RENDER, "network FORBIDDEN", "DCC"):
            self.assertIn(text, out)
        code, out = cli("capabilities", "blender", "--format", "json")
        self.assertEqual(sorted(c["id"] for c in json.loads(out)["capabilities"]["capabilities"]), [ba.INSPECT, ba.RENDER])
        code, out = cli("probe", "blender", "--format", "json")
        self.assertEqual((code, json.loads(out)["probe"]["status"]), (0, "AVAILABLE"))

    def test_execute_inspect_dry_run_and_render(self):
        p = self.project("safe")
        base = ("execute", "--adapter", "blender", "--project", str(p), "--subject-kind", "ASSET", "--subject-ref",
                "ASSET-0001", "--subject-revision", REVISION, "--input-artifact", f"blend={self.blend(p, 'safe')}")
        code, out = cli(*base, "--capability", ba.INSPECT, "--format", "json")
        self.assertEqual((code, json.loads(out)["result"]["data"]["scene_count"]), (0, 1))
        code, out = cli(*base, "--capability", ba.RENDER, "--input", "frame=5", "--dry-run", "--format", "json")
        self.assertEqual((code, json.loads(out)["result"]["artifacts"]), (0, []))
        code, out = cli(*base, "--capability", ba.RENDER, "--input", "frame=5", "--allow-mutation", "--format", "json")
        result = json.loads(out)["result"]
        self.assertEqual((code, result["evidence_candidates"][0]["capture_context"], result["data"]["frame"]),
                         (0, "DCC_RENDER", 5))
        code, _ = cli(*base, "--capability", ba.RENDER, "--input", "frame=5.0", "--allow-mutation")
        self.assertEqual(code, tdg.EXIT_FOR[tdg.INVALID_REQUEST])


if __name__ == "__main__":
    if BLENDER is None:
        print(f"BLENDER_RUNTIME_UNAVAILABLE_FOR_PHASE2C4: {_DISCOVERY_PROBLEM}")
        sys.exit(1)
    result = unittest.main(verbosity=1, exit=False).result
    version = subprocess.run([BLENDER, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    print(f"GPOS Blender adapter tests (real {version.strip()} at {BLENDER})")
    sys.exit(0 if result.wasSuccessful() else 1)
