#!/usr/bin/env python3
"""Phase 2C-5 — production Unity batch adapter tests.

    python3 tests/test_unity_adapter.py

Real integration tests against the installed Unity Editor, found exactly as the production adapter finds
it (the Unity Hub Editor root, never PATH). Every Unity project is a disposable synthetic project written
by `tests/unity_fixture_builder.py` into a temporary GPOS project; no user project is ever opened. The
suite stops with the marker UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C5 unless exactly one Hub Editor is
installed, and never falls back to mocks: stand-in programs are used only where a real Editor cannot be
made to misbehave on demand (licence refusal, missing or malformed results, a refused second instance,
a timeout), and say so.

User state: a module-level guard takes stable snapshots of Unity's EditorPrefs (key names and value
hashes only; values are never printed) and fails the suite if any key other than the six accepted
Unity-owned keys changed. The user's Package Manager configuration files are compared by existence,
size and modification time only; their content is never read.

GPOS_UNITY_TEST_FAST=1 (the mutation harness only) skips every group that starts a real Unity process.

Groups: A registration · B network semantic · C discovery and probe · D static inspection · E project path ·
F version file and exact Editor · G manifest sources · H lock-file sources · I Package Manager isolation ·
J command template · K single-writer lease · L Unity project lock · M EditMode pass · N EditMode failures ·
O zero tests · P compile failure · Q PlayMode · R results parser · S classification · T dry run ·
U mutation consent · V project side effects · W privacy · X materialization · Y command surface · Z CLI.
"""

import ast
import dataclasses
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import unity_fixture_builder as fixtures  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import evidence as tev  # noqa: E402
from gpos.tools import leases as lease_mod  # noqa: E402
from gpos.tools import model as tmodel  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools import validation as tval  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.synthetic import SyntheticAdapter  # noqa: E402
from gpos.tools.unity import UnityAdapter  # noqa: E402
from gpos.tools.unity import adapter as ua  # noqa: E402
from gpos.tools.unity import project as up  # noqa: E402
from gpos.tools.unity import results as ur  # noqa: E402

FW = load_framework()
REG = FW.registry
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
FAST = os.environ.get("GPOS_UNITY_TEST_FAST") == "1"
EDITORS = UnityAdapter().discover()
EDITOR_VERSION, EDITOR = EDITORS[0] if len(EDITORS) == 1 else (None, None)
ACCEPTED_PREFS = {"LastUsedProjectPath", "kProjectBasePath", "kWorkspacePath", "UnityConnectUrlConfiguration",
                  "unity.editor_session_count", "unity.editor_sessionid"}
EDITOR_PREFS = Path.home() / "Library" / "Preferences" / "com.unity3d.UnityEditor5.x.plist"
UPM_CONFIGS = (Path.home() / ".upmconfig.toml", Path("/etc/upmconfig.toml"))
REVISION = "rev-feature-0001"
STANDIN_VERSION = "6000.5.8f1"
_STATE = {}


def prefs_snapshot():
    """A stable snapshot: two identical, valid, non-empty reads one second apart. Hashes only."""
    previous = None
    for _ in range(30):
        try:
            data = plistlib.loads(EDITOR_PREFS.read_bytes())
            snap = {k: hashlib.sha256(repr(v).encode()).hexdigest()[:16] for k, v in data.items()}
        except Exception:
            snap = None
        if snap and snap == previous:
            return snap
        previous = snap
        time.sleep(1)
    raise RuntimeError("no stable EditorPrefs snapshot")


def upm_config_state():
    out = []
    for p in UPM_CONFIGS:
        try:
            st = p.stat()
            out.append((str(p), True, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((str(p), False, None, None))
    return out


def setUpModule():
    if FAST:
        return
    if len(EDITORS) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C5: {len(EDITORS)} Hub Unity Editors found; exactly "
                           f"one is required")
    if EDITOR_PREFS.exists():
        _STATE["prefs"] = prefs_snapshot()
    _STATE["upm"] = upm_config_state()
    _STATE["work"] = Path(tempfile.mkdtemp(prefix="gpos-unity-tests-")).resolve()


def tearDownModule():
    try:
        if FAST:
            return
        if "prefs" in _STATE:
            now = prefs_snapshot()
            moved = {k for k in set(now) | set(_STATE["prefs"]) if now.get(k) != _STATE["prefs"].get(k)}
            unexpected = sorted(moved - ACCEPTED_PREFS)
            if unexpected:
                raise AssertionError(f"UNITY_SHARED_USER_STATE_UNEXPECTED_MUTATION: {unexpected}")
        if upm_config_state() != _STATE["upm"]:
            raise AssertionError("the user's Package Manager configuration files changed")
    finally:
        shutil.rmtree(_STATE.get("work", "/nonexistent"), ignore_errors=True)


def real(test):
    return unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")(test)


class Recorder:
    """Records every process spec started through the audited boundary while active."""

    def __init__(self):
        self.specs, self._original = [], tproc.run_process

    def __enter__(self):
        def recording(spec, scopes, clock=None):
            self.specs.append(spec)
            return self._original(spec, scopes) if clock is None else self._original(spec, scopes, clock)
        tproc.run_process = recording
        return self

    def __exit__(self, *exc):
        tproc.run_process = self._original

    @property
    def unity_runs(self):
        return [s for s in self.specs if "-runTests" in s.argv]


# ---------------------------------------------------------------- stand-in Unity (TEST_ONLY)

STAND_IN = r"""
import json, os, sys, time
cfg = json.load(open(CONFIG))
argv = sys.argv[1:]
if argv == ["-version"]:
    sys.stdout.write(cfg.get("version_output", VERSION + "\n")); sys.exit(cfg.get("version_exit", 0))
opts = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("-")}
json.dump({"argv": argv, "env": {k: os.environ.get(k) for k in ("UPM_USER_CONFIG_FILE", "UPM_GLOBAL_CONFIG_FILE",
           "UPM_CACHE_ROOT", "HTTP_PROXY", "HTTPS_PROXY")}, "cwd": os.getcwd()}, open(RECORD, "w"))
if "log" in cfg:
    open(opts["-logFile"], "w").write(cfg["log"])
if "results" in cfg:
    open(opts["-testResults"], "w").write(cfg["results"])
sys.stdout.write(cfg.get("stdout", ""))
time.sleep(cfg.get("sleep", 0))
sys.exit(cfg.get("exit", 0))
"""


def nunit(total=2, passed=2, failed=0, skipped=0, inconclusive=0, result=None, cases=None):
    result = result or ("Failed(Child)" if failed else "Passed")
    cases = total if cases is None else cases
    body = "".join(f'<test-case id="{i}" name="T{i}" result="Passed"/>' for i in range(cases))
    return (f'<?xml version="1.0" encoding="utf-8"?><test-run id="2" testcasecount="{total}" result="{result}" '
            f'total="{total}" passed="{passed}" failed="{failed}" inconclusive="{inconclusive}" skipped="{skipped}">'
            f'<test-suite type="TestSuite" name="S">{body}</test-suite></test-run>')


class StandIn:
    def __init__(self, directory, version=STANDIN_VERSION, **config):
        self.hub = Path(directory) / "Hub"
        self.version = version
        macos = self.hub / version / "Unity.app" / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        self.config, self.record = Path(directory) / "config.json", Path(directory) / "record.json"
        self.config.write_text(json.dumps(config))
        self.exe = macos / "Unity"
        self.exe.write_text(f"#!{tproc.interpreter_path()}\nCONFIG = {str(self.config)!r}\nRECORD = {str(self.record)!r}\n"
                            f"VERSION = {version!r}\n{STAND_IN}")
        self.exe.chmod(0o755)

    def adapter(self, **kwargs):
        return UnityAdapter(hub_roots=[str(self.hub)], platform="darwin", **kwargs)

    def registry(self, **kwargs):
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(self.adapter(**kwargs))
        return registry

    def recorded(self):
        return json.loads(self.record.read_text())


# ---------------------------------------------------------------- base case

class UnityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-unity-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def gpos_project(self, name="p"):
        target, n = self.tmp / name, 1
        while target.exists():
            n += 1
            target = self.tmp / f"{name}{n}"
        shutil.copytree(FIXTURE, target)
        return target

    def unity_project(self, kind="pass", version=None, into=None, sub="Game", **kwargs):
        p = into or self.gpos_project()
        fixtures.make_project(p / sub, kind, version or EDITOR_VERSION or STANDIN_VERSION, EDITOR, **kwargs)
        return p

    def request(self, cap, project, unity_project="Game", kind="FEATURE", **kwargs):
        if cap != ua.INSPECT:
            kwargs.setdefault("allow_mutation", not kwargs.get("dry_run", False))
        inputs = kwargs.pop("inputs", {"unity_project": unity_project} if unity_project is not None else {})
        return ExecutionRequest(adapter_id="unity", capability_id=cap, subject=Subject(kind, "FEATURE-0001", REVISION),
                                project_root=str(project), inputs=inputs, **kwargs)

    def run_cap(self, cap, project, registry=None, **kwargs):
        return execute(registry or default_registry(FW), self.request(cap, project, **kwargs))

    def codes(self, result):
        return {d.code for d in result.diagnostics}

    def messages(self, result):
        return " ".join(d.message for d in result.diagnostics)

    def assertStatus(self, result, status, code=None):
        self.assertEqual(result.status, status, self.messages(result))
        if code:
            self.assertIn(code, self.codes(result))

    def assertUnsupported(self, result, text=None):
        self.assertStatus(result, tdg.INVALID_REQUEST, "ENGINE_PROJECT_UNSUPPORTED")
        if text:
            self.assertIn(text, self.messages(result))
        self.assertEqual((result.artifacts, result.evidence_candidates, result.mutation_performed), ((), (), False))


REAL_RUNS = {}


def real_run(case, kind, cap):
    """One real Unity run per (fixture kind, capability) for the whole module; results are shared."""
    key = (kind, cap)
    if key not in REAL_RUNS:
        work = _STATE["work"] / f"{kind}-{cap.split('.')[-1]}"
        shutil.copytree(FIXTURE, work)
        fixtures.make_project(work / "Game", kind, EDITOR_VERSION, EDITOR)
        authored = {p.relative_to(work).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (work / "Game").rglob("*") if p.is_file()}
        with Recorder() as rec:
            result = execute(default_registry(FW), ExecutionRequest(
                adapter_id="unity", capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", REVISION),
                project_root=str(work), inputs={"unity_project": "Game"}, allow_mutation=True))
        REAL_RUNS[key] = (result, work, authored, rec.specs)
    return REAL_RUNS[key]


# ---------------------------------------------------------------- A  registration

class A_Registration(UnityCase):
    def test_production_registry_is_exactly_the_six_adapters(self):
        registry = default_registry(FW)
        self.assertEqual(registry.adapter_ids(), ["adb", "blender", "ffmpeg", "ffprobe", "git", "unity"])
        with self.assertRaises(Exception):
            registry.register(SyntheticAdapter())

    def test_descriptor(self):
        d = ua.DESCRIPTOR
        self.assertEqual(tval.validate_descriptor(FW, d, allow_test_only=False), [])
        self.assertEqual((d.adapter_id, d.tool_family, d.target_tool, d.adapter_kind, d.state_model,
                          d.supported_platforms, d.network, d.test_only),
                         ("unity", "ENGINE", "Unity Editor", "CLI", "STATEFUL", ("MACOS",), "TOOL_INHERENT", False))
        # alpha.16: the adapter manages the live plane's long-lived session; each batch capability stays STATELESS

    def test_exactly_three_capabilities(self):
        from gpos.tools.unity import live
        caps = {c.id: c for c in ua.DESCRIPTOR.capabilities if c.id not in live.CAPABILITY_IDS}
        self.assertEqual(sorted(caps), ["unity.inspect-project", "unity.run-editmode-tests", "unity.run-playmode-tests"])
        self.assertEqual(len(ua.DESCRIPTOR.capabilities), 12)   # alpha.16: plus the nine live-plane capabilities
        i = caps[ua.INSPECT]
        self.assertEqual((i.category, i.operation_class, i.state_model, i.execution_context, i.requires_tool,
                          i.single_writer_required, i.dry_run_supported, i.input_kinds, i.artifact_kinds,
                          i.potential_evidence),
                         ("INSPECT", "READ_ONLY", "STATELESS", "OFFLINE_ANALYSIS", False, False, False,
                          ("unity_project",), (), ()))
        for cap in (ua.EDITMODE, ua.PLAYMODE):
            c = caps[cap]
            self.assertEqual((c.category, c.operation_class, c.state_model, c.execution_context, c.requires_tool,
                              c.single_writer_required, c.resource_kind, c.resource_from_request, c.dry_run_supported,
                              c.input_kinds, c.artifact_kinds, c.potential_evidence),
                             ("RUN", "MUTATING", "STATELESS", "AUTOMATED_TEST", True, True, "EDITOR_PROJECT", False, True,
                              ("unity_project",), ("REPORT", "LOG"), (("TEST_EVIDENCE", "AUTOMATED_TEST"),)))
            self.assertIn("ProjectSettings", c.side_effect_scope)
            self.assertEqual((c.timeout.default, c.timeout.maximum), (1800.0, 3600.0))

    def test_agent_and_tool_registries_stay_separate(self):
        from gpos.adapters.backends import BACKENDS
        self.assertNotIn("unity", REG["adapter_ids"])
        self.assertNotIn("unity", BACKENDS)


# ---------------------------------------------------------------- B  network semantic

class B_NetworkSemantic(UnityCase):
    def test_tool_inherent_with_disclosure(self):
        text = " ".join(ua.DESCRIPTOR.network_disclosure)
        for phrase in ("originates no network operation", "Licensing Client", "default package service",
                       "project or test code", "PlayerConnection", "No operating-system network confinement"):
            self.assertIn(phrase, text)
        self.assertEqual(REG["tool_adapter_policy"]["network_semantic_adapters"], {"TOOL_INHERENT": ["unity"]})

    def test_the_semantic_cannot_move_to_another_adapter(self):
        other = dataclasses.replace(ua.DESCRIPTOR, adapter_id="unity-live")
        self.assertIn("allowlisted only for", " ".join(p.message for p in tval.validate_descriptor(FW, other)))
        silent = dataclasses.replace(ua.DESCRIPTOR, network_disclosure=())
        self.assertIn("requires a network_disclosure", " ".join(p.message for p in tval.validate_descriptor(FW, silent)))


# ---------------------------------------------------------------- C  discovery and probe

class C_DiscoveryAndProbe(UnityCase):
    @real
    def test_the_real_probe(self):
        probe = UnityAdapter().probe()
        self.assertEqual((probe.status, probe.tool_version, probe.tool_path), (tmodel.AVAILABLE, EDITOR_VERSION, EDITOR))
        self.assertTrue(EDITOR.startswith("/Applications/Unity/Hub/Editor/"))

    def test_discovery_never_uses_path(self):
        decoy = self.tmp / "bin"
        decoy.mkdir()
        (decoy / "unity").write_text("#!/bin/sh\necho 6000.5.8f1\n")
        (decoy / "unity").chmod(0o755)
        os.environ["PATH"], saved = f"{decoy}{os.pathsep}{os.environ['PATH']}", os.environ["PATH"]
        try:
            adapter = UnityAdapter(hub_roots=[str(self.tmp / "empty-hub")], platform="darwin")
            self.assertEqual(adapter.discover(), [])
            self.assertEqual(adapter.probe().status, tmodel.UNAVAILABLE)
        finally:
            os.environ["PATH"] = saved

    def test_zero_one_and_several_editors(self):
        self.assertEqual(UnityAdapter(hub_roots=[str(self.tmp / "none")], platform="darwin").probe().status,
                         tmodel.UNAVAILABLE)
        one = StandIn(self.tmp / "one")
        probe = one.adapter().probe()
        self.assertEqual((probe.status, probe.tool_version, probe.tool_path), (tmodel.AVAILABLE, STANDIN_VERSION, str(one.exe)))
        two = StandIn(self.tmp / "two")
        StandIn(self.tmp / "two-b", version="6000.4.1f1")
        shutil.copytree(self.tmp / "two-b" / "Hub" / "6000.4.1f1", two.hub / "6000.4.1f1")
        probe = two.adapter().probe()
        self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED)
        self.assertIn("exactly one", probe.detail)
        self.assertIsNone(probe.tool_version)

    def test_exact_names_and_version_directories_only(self):
        s = StandIn(self.tmp / "s")
        (s.hub / "not-a-version").mkdir()
        (s.hub / "6000.5.9f1" / "Unity.app" / "Contents" / "MacOS").mkdir(parents=True)
        wrong = s.hub / "6000.5.9f1" / "Unity.app" / "Contents" / "MacOS" / "unity"                # wrong case
        wrong.write_text("#!/bin/sh\n")
        wrong.chmod(0o755)
        link = s.hub / "6000.5.10f1" / "Unity.app" / "Contents" / "MacOS"
        link.mkdir(parents=True)
        (link / "Unity").symlink_to(s.exe)                                                     # symlinked executable
        self.assertEqual(s.adapter().discover(), [(STANDIN_VERSION, str(s.exe))])

    def test_a_version_the_editor_does_not_confirm_is_unsupported(self):
        for config in (dict(version_output="6000.5.9f1\n"), dict(version_output=""), dict(version_exit=3)):
            with self.subTest(config=config):
                probe = StandIn(self.tmp / f"v{len(os.listdir(self.tmp))}", **config).adapter().probe()
                self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED)

    def test_other_platforms_are_unavailable(self):
        probe = UnityAdapter(platform="linux").probe()
        self.assertEqual(probe.status, tmodel.UNAVAILABLE)
        self.assertIn("macOS only", probe.detail)


# ---------------------------------------------------------------- D  static inspection

class D_StaticInspection(UnityCase):
    def inspect_with(self, registry, p):
        with Recorder() as rec:
            result = self.run_cap(ua.INSPECT, p, registry=registry)
        self.assertEqual(rec.specs, [])            # no Unity process, not even a probe
        return result

    def test_works_with_zero_one_and_several_editors(self):
        p = self.unity_project(version="6000.5.8f1")
        none = ToolRegistry(FW)
        none.register(UnityAdapter(hub_roots=[str(self.tmp / "none")], platform="darwin"))
        two = StandIn(self.tmp / "two")
        shutil.copytree(StandIn(self.tmp / "b", version="6000.4.1f1").hub / "6000.4.1f1", two.hub / "6000.4.1f1")
        other = StandIn(self.tmp / "other", version="2022.3.1f1")     # installed version differs: not compared
        for registry in (none, two.registry(), other.registry()):
            result = self.inspect_with(registry, p)
            self.assertStatus(result, tdg.SUCCESS)
            self.assertEqual(result.data["editor_version"], "6000.5.8f1")

    def test_the_summary(self):
        p = self.unity_project(version="6000.5.8f1")
        (p / "Game/ProjectSettings/ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.5.8f1\nm_EditorVersionWithRevision: 6000.5.8f1 (5cb7df797b7d)\n")
        (p / "Game/Packages/manifest.json").write_text(json.dumps({"dependencies": {"com.example.lib": "1.0.0"}}))
        (p / "Game/Packages/packages-lock.json").write_text(json.dumps({"dependencies": {
            "com.unity.test-framework": {"version": "1.7.0", "depth": 0, "source": "builtin"},
            "com.example.lib": {"version": "1.0.0", "depth": 0, "source": "registry", "url": up.DEFAULT_REGISTRY}}}))
        result = self.run_cap(ua.INSPECT, p)
        self.assertStatus(result, tdg.SUCCESS)
        self.assertEqual(result.data, {"unity_project": "Game", "editor_version": "6000.5.8f1",
                                       "editor_revision": "5cb7df797b7d",
                                       "manifest": {"dependencies": 1, "by_source": {"registry": 1}, "testables": 0},
                                       "lock": {"entries": 2, "by_source": {"builtin": 1, "registry": 1}, "present": True}})
        self.assertEqual((result.artifacts, result.evidence_candidates, result.mutation_performed), ((), (), False))
        self.assertNotIn("LEASE_ACQUIRED", self.codes(result))

    def test_the_default_project_is_the_gpos_root(self):
        p = self.gpos_project()
        fixtures.make_project(p, "pass", "6000.5.8f1")
        result = self.run_cap(ua.INSPECT, p, unity_project=None)
        self.assertStatus(result, tdg.SUCCESS)
        self.assertEqual(result.data["unity_project"], ".")


# ---------------------------------------------------------------- E  project path

class E_ProjectPath(UnityCase):
    def test_paths_outside_or_malformed_are_refused(self):
        p = self.unity_project()
        outside = self.tmp / "outside"
        fixtures.make_project(outside, "pass", "6000.5.8f1")
        (p / "escape").symlink_to(outside)
        for value in ("/abs", "../outside", "Game/../../outside", "Game/../Game", "escape", "~/x", "a\nb", "", "x" * 300, 7,
                      "Missing", "C:/game"):
            with self.subTest(value=repr(value)[:20]):
                self.assertUnsupported(self.run_cap(ua.INSPECT, p, unity_project=value))

    def test_layout_is_required(self):
        for missing in ("Assets", "Packages", "ProjectSettings"):
            with self.subTest(missing=missing):
                p = self.unity_project()
                shutil.rmtree(p / "Game" / missing)
                self.assertUnsupported(self.run_cap(ua.INSPECT, p), f"no {missing}/")

    def test_a_symlinked_project_inside_the_root_is_accepted(self):
        p = self.unity_project()
        (p / "alias").symlink_to(p / "Game")
        result = self.run_cap(ua.INSPECT, p, unity_project="alias")
        self.assertStatus(result, tdg.SUCCESS)
        self.assertEqual(result.data["unity_project"], "Game")


# ---------------------------------------------------------------- F  version file and exact Editor

class F_VersionFile(UnityCase):
    def test_malformed_version_files_are_refused(self):
        for text in ("", "m_EditorVersion: 6000.5\n", "m_EditorVersion: 6000.5.8f1\nm_EditorVersion: 6000.5.8f1\n",
                     "m_EditorVersion: 6000.5.8f1\nm_EditorVersionWithRevision: 6000.5.7f1 (abcdef12)\n",
                     "m_EditorVersion: 6000.5.8f1 \u00e9\n", "no colon\n", "m_EditorVersion: 6000.5.8f1\n" + "x" * 5000):
            with self.subTest(text=text[:40]):
                p = self.unity_project()
                (p / "Game/ProjectSettings/ProjectVersion.txt").write_text(text)
                self.assertUnsupported(self.run_cap(ua.INSPECT, p))

    def test_a_version_mismatch_is_refused_before_launch_with_no_fallback(self):
        s = StandIn(self.tmp / "s", exit=0)
        for project_version in ("6000.5.7f1", "6000.5.8f2", "6000.6.8f1", "2022.3.8f1"):
            with self.subTest(version=project_version):
                p = self.unity_project(version=project_version)
                result = self.run_cap(ua.EDITMODE, p, registry=s.registry())
                self.assertStatus(result, tdg.INCOMPATIBLE, "ENGINE_EDITOR_VERSION_UNAVAILABLE")
                self.assertFalse(s.record.exists())
                self.assertEqual((result.artifacts, result.evidence_candidates, result.mutation_performed), ((), (), False))


# ---------------------------------------------------------------- G  manifest package sources

class G_ManifestSources(UnityCase):
    def manifest(self, p, data, raw=None):
        (p / "Game/Packages/manifest.json").write_text(raw if raw is not None else json.dumps(data))

    def test_remote_and_git_sources_are_refused_before_launch(self):
        s = StandIn(self.tmp / "s")
        refused = ["https://github.example.com/u/r.git", "git+https://github.example.com/u/r",
                   "ssh://git@github.example.com/u/r.git", "git+ssh://git@github.example.com/u/r",
                   "git@github.example.com:u/r.git", "git+file://github.example.com/u/r", "git://github.example.com/u/r.git",
                   "file:///github.example.com/u/r.git", "http://example.com/p.tgz", "https://example.com/p.tgz",
                   "file://localpkg", "file:pkg.git", "file:pkg#v1", "file:pkg?path=/x", "latest", "1.0", "*"]
        for value in refused:
            for cap in (ua.INSPECT, ua.EDITMODE, ua.PLAYMODE):
                with self.subTest(value=value, cap=cap):
                    p = self.unity_project()
                    self.manifest(p, {"dependencies": {"com.example.remote": value}})
                    result = self.run_cap(cap, p, registry=s.registry())
                    self.assertUnsupported(result)
                    self.assertNotIn(value, self.messages(result))
        self.assertFalse(s.record.exists())

    def test_registries_and_unknown_keys_are_refused(self):
        for data in ({"dependencies": {}, "scopedRegistries": [{"name": "x", "url": "https://r.example.com",
                                                               "scopes": ["com.x"]}]},
                     {"dependencies": {}, "registry": "https://r.example.com"},
                     {"dependencies": {}, "somethingNew": True},
                     {"dependencies": {"Bad Name": "1.0.0"}},
                     {"dependencies": {"com.x": 1}}, {"dependencies": []}, [],
                     {"dependencies": {}, "enableLockFile": "yes"}, {"dependencies": {}, "resolutionStrategy": "newest"},
                     {"dependencies": {}, "testables": ["Bad Name"]}):
            with self.subTest(data=str(data)[:60]):
                p = self.unity_project()
                self.manifest(p, data)
                self.assertUnsupported(self.run_cap(ua.INSPECT, p))
        for raw in ('{"dependencies": {}, "dependencies": {}}', "{not json", "\ufeff{}", '{"dependencies": {}}' + " " * (2 * 1024 * 1024)):
            with self.subTest(raw=raw[:30]):
                p = self.unity_project()
                self.manifest(p, None, raw=raw)
                self.assertUnsupported(self.run_cap(ua.INSPECT, p))

    def test_documented_local_sources_inside_the_root_are_accepted(self):
        p = self.unity_project()
        pkg = p / "Game/Packages/com.gpos.local"
        pkg.mkdir()
        (pkg / "package.json").write_text('{"name": "com.gpos.local", "version": "1.0.0"}')
        (p / "vendor").mkdir()
        (p / "vendor/com.gpos.tar.tgz").write_bytes(b"\x1f\x8b")
        self.manifest(p, {"dependencies": {"com.gpos.local": "file:com.gpos.local",
                                           "com.gpos.tar": "file:../../vendor/com.gpos.tar.tgz",
                                           "com.unity.test-framework": "1.7.0"}, "scopedRegistries": [],
                          "enableLockFile": True, "resolutionStrategy": "lowest", "testables": ["com.gpos.local"]})
        result = self.run_cap(ua.INSPECT, p)
        self.assertStatus(result, tdg.SUCCESS)
        self.assertEqual(result.data["manifest"]["by_source"], {"local": 1, "local-tarball": 1, "registry": 1})

    def test_local_sources_outside_the_root_or_not_packages_are_refused(self):
        outside = self.tmp / "outside-pkg"
        outside.mkdir()
        (outside / "package.json").write_text("{}")
        for value, prepare in (("file:" + str(outside), None), ("file:../../../outside-pkg", None),
                               ("file:missing", None), ("file:nopkg", "nopkg"), ("file:gitpkg", "gitpkg"),
                               ("file:pkg.git", "pkg.git"), ("file://pkgurl", "pkgurl")):
            with self.subTest(value=value):
                p = self.unity_project()
                if prepare:
                    d = p / "Game/Packages" / prepare
                    d.mkdir()
                    if prepare in ("gitpkg", "pkg.git", "pkgurl"):
                        (d / "package.json").write_text("{}")
                    if prepare == "gitpkg":
                        (d / ".git").mkdir()
                self.manifest(p, {"dependencies": {"com.example.local": value}})
                self.assertUnsupported(self.run_cap(ua.INSPECT, p))


# ---------------------------------------------------------------- H  lock-file sources

class H_LockSources(UnityCase):
    def test_unsupported_lock_sources_are_refused(self):
        for entry in ({"version": "https://github.example.com/u/r.git", "source": "git", "hash": "abc"},
                      {"version": "1.0.0", "source": "registry", "url": "https://registry.example.com"},
                      {"version": "1.0.0", "source": "registry"},
                      {"version": "1.0.0", "source": "scoped"},
                      {"version": "file:../../../elsewhere", "source": "local"},
                      {"version": "1.0.0", "source": "local"}):
            with self.subTest(entry=entry["source"]):
                p = self.unity_project()
                (p / "Game/Packages/packages-lock.json").write_text(json.dumps({"dependencies": {"com.example.x": entry}}))
                self.assertUnsupported(self.run_cap(ua.INSPECT, p), "com.example.x")

    def test_supported_lock_sources_are_accepted(self):
        p = self.unity_project()
        (p / "Game/Packages/packages-lock.json").write_text(json.dumps({"dependencies": {
            "com.a": {"version": "1.0.0", "source": "builtin"}, "com.b": {"version": "1.0.0", "source": "embedded"},
            "com.c": {"version": "2.0.0", "source": "registry", "url": "https://packages.unity.com"}}}))
        self.assertStatus(self.run_cap(ua.INSPECT, p), tdg.SUCCESS)


# ---------------------------------------------------------------- I  Package Manager isolation

class I_PackageManagerIsolation(UnityCase):
    def test_every_launch_gets_gpos_owned_configuration(self):
        s = StandIn(self.tmp / "s", results=nunit(), log="Test run completed.\n")
        p = self.unity_project(version=STANDIN_VERSION)
        os.environ["HTTP_PROXY"], saved = "http://proxy.example.com:3128", os.environ.get("HTTP_PROXY")
        try:
            result = self.run_cap(ua.EDITMODE, p, registry=s.registry())
        finally:
            os.environ.pop("HTTP_PROXY") if saved is None else os.environ.__setitem__("HTTP_PROXY", saved)
        self.assertStatus(result, tdg.SUCCESS)
        env = s.recorded()["env"]
        ws = p / ".game/gpos-runtime/tool-output/unity" / result.request_id
        self.assertEqual(env["UPM_USER_CONFIG_FILE"], str((ws / "upm-user.toml").resolve()))
        self.assertEqual(env["UPM_GLOBAL_CONFIG_FILE"], str((ws / "upm-global.toml").resolve()))
        self.assertEqual(env["UPM_CACHE_ROOT"], str((p / ".game/gpos-runtime/unity/upm-cache").resolve()))
        self.assertEqual((env["HTTP_PROXY"], env["HTTPS_PROXY"]), (None, None))     # never inherited
        self.assertEqual(((ws / "upm-user.toml").read_bytes(), (ws / "upm-global.toml").read_bytes()), (b"", b""))
        self.assertIn("UPM_USER_CONFIG_FILE", result.provenance.to_dict()["environment"]["set_names"])

    @real
    def test_the_real_package_manager_uses_the_isolated_configuration(self):
        result, work, _, _ = real_run(self, "pass", ua.EDITMODE)
        upm = (work / ".game/gpos-runtime/tool-output/unity" / result.request_id / "upm.log").read_text()
        for name in ("UPM_USER_CONFIG_FILE", "UPM_GLOBAL_CONFIG_FILE", "UPM_CACHE_ROOT"):
            self.assertIn(name + "=", upm)
        self.assertTrue(any((work / ".game/gpos-runtime/unity/upm-cache").iterdir()))


# ---------------------------------------------------------------- J  command template

class J_CommandTemplate(UnityCase):
    def test_the_fixed_template(self):
        argv = ua.test_argv("/proj", "/ws", "EditMode")
        self.assertEqual(argv, ("-batchmode", "-projectPath", "/proj", "-logFile", "/ws/editor.log", "-upmLogFile",
                                "/ws/upm.log", "-cacheServerEnableDownload", "false", "-cacheServerEnableUpload", "false",
                                "-runTests", "-testPlatform", "EditMode", "-testResults", "/ws/results.xml"))
        for flag in ua.NEVER + ("-createProject", "-username", "-password", "-serial", "-buildTarget",
                                "-EnableCacheServer", "-cacheServerEndpoint", "-testFilter", "-testCategory"):
            self.assertNotIn(flag, argv)

    def test_the_recorded_run(self):
        s = StandIn(self.tmp / "s", results=nunit(), log="ok\n")
        p = self.unity_project(version=STANDIN_VERSION)
        for cap, platform in ((ua.EDITMODE, "EditMode"), (ua.PLAYMODE, "PlayMode")):
            with Recorder() as rec:
                result = self.run_cap(cap, p, registry=s.registry())
            (spec,) = rec.unity_runs
            ws = p / ".game/gpos-runtime/tool-output/unity" / result.request_id
            self.assertEqual(spec.executable, str(s.exe))
            self.assertEqual(spec.argv, ua.test_argv((p / "Game").resolve(), ws, platform))
            self.assertEqual(spec.cwd, str(ws))
            self.assertEqual(s.recorded()["cwd"], str(ws.resolve()))
            recorded = result.provenance.to_dict()["command"]["argv"]
            self.assertIn("<unity-project>", recorded)
            self.assertIn("<workspace>/results.xml", recorded)


# ---------------------------------------------------------------- K  single-writer lease

class K_Lease(UnityCase):
    def test_the_lease_is_the_resolved_project_root_and_is_taken_before_launch(self):
        s = StandIn(self.tmp / "s", results=nunit(), log="ok\n")
        p = self.unity_project(version=STANDIN_VERSION)
        alias = self.tmp / "alias"
        alias.symlink_to(p)
        resource = f"EDITOR_PROJECT:{p.resolve()}"
        held = lease_mod.acquire(p, "unity", resource, "HUMAN:someone", "2026-09-25T00:00:00Z", "req-other")
        try:
            for root in (p, alias):
                with self.subTest(root=str(root)):
                    result = execute(s.registry(), self.request(ua.EDITMODE, root))
                    self.assertStatus(result, tdg.CONFLICT, "LEASE_CONFLICT")
                    self.assertFalse(s.record.exists())
        finally:
            lease_mod.release(p, held)
        result = self.run_cap(ua.EDITMODE, p, registry=s.registry())
        self.assertStatus(result, tdg.SUCCESS)
        self.assertIn(resource, self.messages(result))
        self.assertEqual(list((p / ".game/gpos-runtime/leases").glob("*.json")), [])


# ---------------------------------------------------------------- L  Unity project lock

class L_UnityLock(UnityCase):
    def test_an_existing_unity_lock_is_a_conflict_and_is_never_removed(self):
        s = StandIn(self.tmp / "s", results=nunit())
        p = self.unity_project(version=STANDIN_VERSION)
        (p / "Game/Temp").mkdir()
        lock = p / "Game/Temp/UnityLockfile"
        lock.write_bytes(b"")
        for cap in (ua.EDITMODE, ua.PLAYMODE):
            result = self.run_cap(cap, p, registry=s.registry())
            self.assertStatus(result, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
        self.assertTrue(lock.exists())
        self.assertFalse(s.record.exists())

    def test_a_refused_second_instance_is_a_conflict(self):
        s = StandIn(self.tmp / "s", exit=1, stdout="It looks like another Unity instance is running with this project "
                                                     "open.\n")
        result = self.run_cap(ua.EDITMODE, self.unity_project(version=STANDIN_VERSION), registry=s.registry())
        self.assertStatus(result, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
        self.assertEqual(result.evidence_candidates, ())


# ---------------------------------------------------------------- M  EditMode pass (real)

class M_EditModePass(UnityCase):
    @real
    def test_a_real_passing_run(self):
        result, work, _, specs = real_run(self, "pass", ua.EDITMODE)
        self.assertStatus(result, tdg.SUCCESS)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual({k: result.data[k] for k in ("total", "passed", "failed", "skipped", "result", "test_platform")},
                         {"total": 2, "passed": 2, "failed": 0, "skipped": 0, "result": "Passed", "test_platform": "EditMode"})
        arts = {a.artifact_id: a for a in result.artifacts}
        self.assertEqual(sorted(arts), ["editor-log", "results"])
        self.assertEqual((arts["results"].kind, arts["results"].media_type, arts["results"].complete), ("REPORT", "application/xml", True))
        self.assertEqual(arts["editor-log"].kind, "LOG")
        self.assertTrue(arts["results"].path.startswith(".game/gpos-runtime/tool-output/unity/"))
        self.assertFalse((work / "Game" / "TestResults.xml").exists())
        (cand,) = result.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context, cand.artifact_ids), ("TEST_EVIDENCE", "AUTOMATED_TEST", ("results",)))
        self.assertTrue(result.mutation_performed)
        (spec,) = [s for s in specs if "-runTests" in s.argv]
        self.assertNotIn("-quit", spec.argv)
        self.assertFalse((work / "Game/Temp/UnityLockfile").exists())       # removed by Unity on a clean exit


# ---------------------------------------------------------------- N  EditMode failures (real)

class N_EditModeFailures(UnityCase):
    @real
    def test_failed_tests_are_evidence_of_failure_not_a_tool_failure(self):
        result, _, _, _ = real_run(self, "fail", ua.EDITMODE)
        self.assertStatus(result, tdg.SUCCESS, "TESTS_FAILED")
        self.assertEqual((result.exit_code, result.data["total"], result.data["failed"]), (2, 2, 1))
        (cand,) = result.evidence_candidates
        self.assertIn("1 failed", cand.summary)
        self.assertIn("Failed tests are evidence of failure, never a passing result.", cand.limitations)


# ---------------------------------------------------------------- O  zero tests (real)

class O_ZeroTests(UnityCase):
    @real
    def test_zero_tests_exit_zero_is_not_a_pass(self):
        result, _, _, _ = real_run(self, "zero", ua.EDITMODE)
        self.assertEqual(result.exit_code, 0)
        self.assertStatus(result, tdg.FAILED, "ENGINE_TESTS_NOT_EXECUTED")
        self.assertEqual(result.data["total"], 0)
        self.assertEqual(result.evidence_candidates, ())


# ---------------------------------------------------------------- P  compile failure (real)

class P_CompileFailure(UnityCase):
    @real
    def test_a_compile_failure_has_no_results_and_no_evidence(self):
        result, _, _, _ = real_run(self, "compile", ua.EDITMODE)
        self.assertStatus(result, tdg.FAILED, "EXECUTION_FAILED")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.data["cause"], "COMPILE_ERROR")
        self.assertIn("script compilation failed", self.messages(result))
        self.assertEqual(result.evidence_candidates, ())
        self.assertEqual([a.artifact_id for a in result.artifacts], ["editor-log"])


# ---------------------------------------------------------------- Q  PlayMode (real)

class Q_PlayMode(UnityCase):
    @real
    def test_a_real_passing_playmode_run(self):
        result, _, _, _ = real_run(self, "pass", ua.PLAYMODE)
        self.assertStatus(result, tdg.SUCCESS)
        self.assertEqual((result.exit_code, result.data["total"], result.data["test_platform"]), (0, 1, "PlayMode"))
        (cand,) = result.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context), ("TEST_EVIDENCE", "AUTOMATED_TEST"))
        self.assertIn("PlayMode tests run inside the Editor", " ".join(cand.limitations))

    @real
    def test_a_real_failing_playmode_run(self):
        result, _, _, _ = real_run(self, "fail", ua.PLAYMODE)
        self.assertStatus(result, tdg.SUCCESS, "TESTS_FAILED")
        self.assertEqual((result.exit_code, result.data["failed"]), (2, 1))


# ---------------------------------------------------------------- R  results parser

class R_ResultsParser(UnityCase):
    def write(self, text):
        path = self.tmp / f"r{len(os.listdir(self.tmp))}.xml"
        path.write_text(text) if isinstance(text, str) else path.write_bytes(text)
        return path

    def test_a_consistent_document(self):
        self.assertEqual(ur.read_results(self.write(nunit(total=3, passed=2, failed=1))),
                         {"total": 3, "passed": 2, "failed": 1, "skipped": 0, "inconclusive": 0, "result": "Failed(Child)",
                          "test_cases": 3})

    def test_everything_else_fails_closed(self):
        declared = nunit().replace("?>", '?><!DOCTYPE test-run [<!ENTITY a "b">]>', 1)
        bad = {"doctype": declared, "doctype-lower": declared.replace("DOCTYPE", "doctype").replace("ENTITY", "entity"),
               "truncated": nunit()[:-20], "wrong root": nunit().replace("test-run", "test-suite-x"),
               "counts": nunit(total=3, passed=2), "cases": nunit(total=2, passed=2, cases=1),
               "result": nunit(result="Maybe"), "negative": nunit().replace('passed="2"', 'passed="-2"'),
               "missing": nunit().replace('failed="0" ', ""), "not xml": "hello",
               "oversized": nunit().replace("<test-suite", " " * (ur.MAX_RESULTS + 1) + "<test-suite")}
        for name, text in bad.items():
            with self.subTest(case=name), self.assertRaises(ur.ResultsProblem):
                ur.read_results(self.write(text))
        with self.assertRaises(ur.ResultsProblem):
            ur.read_results(self.tmp / "absent.xml")

    def test_log_classification(self):
        self.assertEqual(ur.classify_log("x\nScripts have compiler errors.\n"), ur.COMPILE_ERROR)
        self.assertEqual(ur.classify_log("No valid Unity Editor license found. Please activate your license."),
                         ur.LICENSE_UNAVAILABLE)
        self.assertEqual(ur.classify_log("", "It looks like another Unity instance is running with this project open."),
                         ur.PROJECT_LOCKED)
        self.assertEqual(ur.classify_log("Exiting with code 1"), ur.UNCLASSIFIED)


# ---------------------------------------------------------------- S  classification

class S_Classification(UnityCase):
    def run_with(self, cap=ua.EDITMODE, **config):
        s = StandIn(self.tmp / f"s{len(os.listdir(self.tmp))}", **config)
        return self.run_cap(cap, self.unity_project(version=STANDIN_VERSION), registry=s.registry())

    def test_exit_codes_are_never_read_alone(self):
        cases = [
            (dict(exit=1, log="Aborting\n"), tdg.FAILED, "EXECUTION_FAILED", "could not be classified"),
            (dict(exit=0, log="done\n"), tdg.FAILED, "EXECUTION_FAILED", "could not be classified"),
            (dict(exit=0, results=nunit(total=2, passed=1, failed=1)), tdg.FAILED, "EXECUTION_FAILED", "not trusted"),
            (dict(exit=2, results=nunit()), tdg.FAILED, "EXECUTION_FAILED", "not trusted"),
            (dict(exit=3, results=nunit()), tdg.FAILED, "EXECUTION_FAILED", "not trusted"),
            (dict(exit=0, results="<test-run"), tdg.FAILED, "EXECUTION_FAILED", "could not be accepted"),
            (dict(exit=198, log="No valid Unity Editor license found. Please activate your license.\n"),
             tdg.UNAVAILABLE, "ENGINE_LICENSE_UNAVAILABLE", "no licence action"),
            (dict(exit=0, results=nunit(total=0, passed=0, cases=0)), tdg.FAILED, "ENGINE_TESTS_NOT_EXECUTED", "not a pass"),
        ]
        for config, status, code, text in cases:
            with self.subTest(config=str(config)[:60]):
                result = self.run_with(**config)
                self.assertStatus(result, status, code)
                self.assertIn(text, self.messages(result))
                self.assertEqual(result.evidence_candidates, ())

    def test_failed_tests_with_a_consistent_exit_are_evidence(self):
        result = self.run_with(exit=2, results=nunit(total=3, passed=2, failed=1), log="ok\n")
        self.assertStatus(result, tdg.SUCCESS, "TESTS_FAILED")
        (cand,) = result.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context), ("TEST_EVIDENCE", "AUTOMATED_TEST"))
        self.assertIn("1 failed", cand.summary)

    def test_a_timeout_is_never_evidence(self):
        s = StandIn(self.tmp / "slow", results=nunit(), sleep=30)
        result = self.run_cap(ua.EDITMODE, self.unity_project(version=STANDIN_VERSION), registry=s.registry(), timeout=3.0)
        self.assertEqual(result.status, tdg.TIMED_OUT)
        self.assertEqual(result.evidence_candidates, ())
        self.assertTrue(result.mutation_performed)


# ---------------------------------------------------------------- T  dry run

class T_DryRun(UnityCase):
    def test_a_dry_run_launches_nothing_and_creates_nothing(self):
        s = StandIn(self.tmp / "s", results=nunit())
        p = self.unity_project(version=STANDIN_VERSION)
        before = sorted(x.relative_to(p).as_posix() for x in p.rglob("*"))
        for cap in (ua.EDITMODE, ua.PLAYMODE):
            with Recorder() as rec:
                result = self.run_cap(cap, p, registry=s.registry(), dry_run=True)
            self.assertStatus(result, tdg.SUCCESS)
            self.assertEqual(rec.unity_runs, [])
            self.assertEqual(len(result.plan), 3)
            self.assertIn("only checked by a real execution", result.plan[1])
            self.assertEqual((result.artifacts, result.evidence_candidates, result.mutation_performed), ((), (), False))
        self.assertEqual(sorted(x.relative_to(p).as_posix() for x in p.rglob("*")), before)
        self.assertFalse((p / ".game/gpos-runtime").exists())

    def test_a_dry_run_still_enforces_the_static_contract(self):
        s = StandIn(self.tmp / "s")
        p = self.unity_project(version=STANDIN_VERSION)
        (p / "Game/Packages/manifest.json").write_text(json.dumps({"dependencies": {"com.x": "git@h:u/r.git"}}))
        self.assertUnsupported(self.run_cap(ua.EDITMODE, p, registry=s.registry(), dry_run=True))
        q = self.unity_project(version="6000.5.1f1")
        self.assertStatus(self.run_cap(ua.EDITMODE, q, registry=s.registry(), dry_run=True), tdg.INCOMPATIBLE)

    def test_an_existing_result_file_is_refused(self):
        s = StandIn(self.tmp / "s", results=nunit())
        p = self.unity_project(version=STANDIN_VERSION)
        out = p / "reviews"
        out.mkdir()
        (out / "results.xml").write_text("old")
        for dry in (True, False):
            result = self.run_cap(ua.EDITMODE, p, registry=s.registry(), dry_run=dry, output_dir=str(out))
            self.assertStatus(result, tdg.INVALID_REQUEST, "INVALID_TOOL_REQUEST")
        self.assertEqual((out / "results.xml").read_text(), "old")
        self.assertFalse(s.record.exists())


# ---------------------------------------------------------------- U  mutation consent

class U_MutationConsent(UnityCase):
    def test_no_run_without_consent(self):
        s = StandIn(self.tmp / "s", results=nunit())
        result = self.run_cap(ua.EDITMODE, self.unity_project(version=STANDIN_VERSION), registry=s.registry(),
                              allow_mutation=False)
        self.assertStatus(result, tdg.INVALID_REQUEST, "MUTATION_NOT_ALLOWED")
        self.assertFalse(s.record.exists())


# ---------------------------------------------------------------- V  project side effects (real)

class V_ProjectSideEffects(UnityCase):
    @real
    def test_unity_managed_state_changes_and_authored_sources_do_not(self):
        result, work, authored, _ = real_run(self, "pass", ua.EDITMODE)
        game = work / "Game"
        for name, digest in authored.items():                     # scripts, assembly definitions, manifest: unchanged
            if name.endswith((".cs", ".asmdef")) or name == "Game/Packages/manifest.json":
                self.assertEqual(hashlib.sha256((work / name).read_bytes()).hexdigest(), digest, name)
        version_file = (game / "ProjectSettings/ProjectVersion.txt").read_text()
        self.assertEqual(up.editor_version(game)[0], EDITOR_VERSION)   # never upgraded or downgraded
        self.assertIn("m_EditorVersionWithRevision", version_file)     # Unity adds the revision line: disclosed
        self.assertTrue((game / "Library").is_dir())
        self.assertTrue(list(game.glob("Assets/**/*.meta")))       # Unity-managed writes, disclosed, never rolled back
        self.assertTrue((game / "Packages/packages-lock.json").exists())
        self.assertTrue(list(game.glob("ProjectSettings/*.asset")))
        self.assertIn(".meta files", ua.SIDE_EFFECTS)


# ---------------------------------------------------------------- W  privacy

class W_Privacy(UnityCase):
    def test_no_absolute_paths_in_data_or_recorded_command(self):
        s = StandIn(self.tmp / "s", results=nunit(), log="ok\n")
        p = self.unity_project(version=STANDIN_VERSION)
        result = self.run_cap(ua.EDITMODE, p, registry=s.registry())
        self.assertStatus(result, tdg.SUCCESS)
        text = json.dumps(result.data) + json.dumps(result.provenance.to_dict()["command"]["argv"])
        self.assertNotIn(str(p), text)
        self.assertNotIn(str(p.resolve()), text)
        (cand,) = result.evidence_candidates
        self.assertEqual(cand.artifact_ids, ("results",))          # the Editor log is never evidence

    @real
    def test_the_real_editor_log_stays_a_log_artifact(self):
        result, work, _, _ = real_run(self, "pass", ua.EDITMODE)
        self.assertNotIn(str(work), json.dumps(result.data))
        self.assertLessEqual(result.stdout_bytes + result.stderr_bytes, ua.CAPTURE_BYTES * 2)


# ---------------------------------------------------------------- X  materialization

class X_Materialization(UnityCase):
    @real
    def test_a_real_candidate_materializes_without_runtime_fields(self):
        result, _, _, _ = real_run(self, "pass", ua.EDITMODE)
        (cand,) = result.evidence_candidates
        record, problems = tev.materialize(FW, cand, result.artifacts, "EV-UNITY-1")
        self.assertEqual(problems, [])
        self.assertEqual(FW.validators["evidence"].errors(record), [])
        prov = record["provenance"]
        self.assertEqual((record["type"], prov["capture_context"], prov["tool_version"], prov["subject_revision"]),
                         ("TEST_EVIDENCE", "AUTOMATED_TEST", EDITOR_VERSION, REVISION))
        for absent in ("build_revision", "build_id", "target_platform", "device"):
            self.assertNotIn(absent, prov)


# ---------------------------------------------------------------- Y  command surface

class Y_CommandSurface(UnityCase):
    def test_no_subprocess_or_network_import_in_the_unity_modules(self):
        for path in sorted((ROOT / "gpos/tools/unity").glob("*.py")):
            names = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
            self.assertEqual(names & {"subprocess", "socket", "ssl", "http", "urllib", "requests", "asyncio", "ctypes"},
                             set(), path)

    def test_the_adapter_reads_only_its_declared_input(self):
        text = (ROOT / "gpos/tools/unity/adapter.py").read_text()
        self.assertEqual(set(re.findall(r'\.get\("(\w+)"\)', text)), {"unity_project"})
        self.assertEqual(set(re.findall(r"\brequest\.(\w+)", text)), {"capability_id", "inputs", "subject"})
        self.assertNotIn("PATH", re.findall(r'"([A-Z_]+)"', text))
        s = StandIn(self.tmp / "s")
        for name in ("executable", "argv", "method", "testFilter", "graphics", "registry", "url", "editor_version"):
            with self.subTest(name=name):
                result = self.run_cap(ua.EDITMODE, self.unity_project(version=STANDIN_VERSION), registry=s.registry(),
                                      inputs={"unity_project": "Game", name: "x"})
                self.assertStatus(result, tdg.INVALID_REQUEST)
        self.assertFalse(s.record.exists())


# ---------------------------------------------------------------- Z  CLI

def cli(*argv):
    from gpos.tools import cli as tool_cli
    buffer = io.StringIO()
    code = tool_cli.main(list(argv), stdout=buffer)
    return code, buffer.getvalue()


class Z_Cli(UnityCase):
    def test_list_and_describe(self):
        code, out = cli("list")
        self.assertEqual(code, 0)
        self.assertIn("6 tool adapter", out)
        self.assertIn("unity 1.0.0 · ENGINE · 12 capabilities", out)
        code, out = cli("describe", "unity")
        self.assertEqual(code, 0)
        self.assertIn("network TOOL_INHERENT", out)

    def test_inspect_and_dry_run(self):
        p = self.unity_project(version="6000.5.8f1")
        code, out = cli("execute", "--adapter", "unity", "--capability", ua.INSPECT, "--project", str(p),
                        "--subject-ref", "FEATURE-0001", "--input", "unity_project=Game", "--format", "json")
        self.assertEqual((code, json.loads(out)["result"]["data"]["editor_version"]), (0, "6000.5.8f1"))
        (p / "Game/Packages/manifest.json").write_text(json.dumps({"dependencies": {"com.x": "https://h/r.git"}}))
        code, out = cli("execute", "--adapter", "unity", "--capability", ua.INSPECT, "--project", str(p),
                        "--subject-ref", "FEATURE-0001", "--input", "unity_project=Game", "--format", "json")
        self.assertEqual(json.loads(out)["result"]["status"], "INVALID_REQUEST")
        self.assertNotIn("https://h/r.git", out)

    @real
    def test_a_real_cli_dry_run(self):
        p = self.unity_project()
        code, out = cli("execute", "--adapter", "unity", "--capability", ua.EDITMODE, "--project", str(p),
                        "--subject-ref", "FEATURE-0001", "--input", "unity_project=Game", "--dry-run", "--format", "json")
        result = json.loads(out)["result"]
        self.assertEqual((code, result["status"], result["artifacts"]), (0, "SUCCESS", []))


if __name__ == "__main__":
    if not FAST and len(EDITORS) != 1:
        print(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C5: {len(EDITORS)} Hub Unity Editors found; exactly one is required")
        sys.exit(1)
    result = unittest.main(verbosity=1, exit=False).result
    if not FAST:
        print(f"GPOS Unity adapter tests (real Unity {EDITOR_VERSION} at {EDITOR})")
    sys.exit(0 if result.wasSuccessful() else 1)
