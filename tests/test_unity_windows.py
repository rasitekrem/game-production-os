#!/usr/bin/env python3
"""Windows Unity batch-plane qualification (alpha.25, Phase 2C-9.3a). Windows only: every group skips elsewhere.

    python -X utf8 tests/test_unity_windows.py
    GPOS_UNITY_TEST_FAST=1 python -X utf8 tests/test_unity_windows.py     (no real Unity process)

The real groups (WG, WH, WI) need exactly one Unity Hub Editor and the disposable lab root GPOS_UNITY_WINDOWS_LAB
(default D:\\gpos-unity-lab-alpha25), which must already exist. Every Unity project they open is created under that
root and removed afterwards; no other project is ever opened, and at most one Unity Editor runs at a time. They use
the production authority model unchanged: default_registry, ExecutionRequest and the fixed batch command.

Groups: WA platform and capability gating · WB discovery and version · WC lock proof against a fake host · WD lock
proof against the real host (no Unity) · WE environment · WF classification · WG real batch runs · WH real project
lock and Job behaviour · WI user state.
"""

import ast
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
WINDOWS = sys.platform == "win32"
if WINDOWS and not sys.flags.utf8_mode:
    sys.exit("WINDOWS_UTF8_MODE_REQUIRED: run this suite as `python -X utf8 tests/test_unity_windows.py`")

import unity_fixture_builder as fixtures  # noqa: E402
import windows_standin  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools import validation as tval  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.unity import UnityAdapter  # noqa: E402
from gpos.tools.unity import adapter as ua  # noqa: E402
from gpos.tools.unity import project_lock as pl  # noqa: E402
from gpos.tools.unity import results as ur  # noqa: E402

if WINDOWS:
    import winreg  # noqa: E402
    from gpos.tools.unity import host_win32  # noqa: E402

FW = load_framework()
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
FAST = os.environ.get("GPOS_UNITY_TEST_FAST") == "1"
LAB = Path(os.environ.get("GPOS_UNITY_WINDOWS_LAB", r"D:\gpos-unity-lab-alpha25"))
EDITORS = UnityAdapter().discover() if WINDOWS else []
EDITOR_VERSION, EDITOR = EDITORS[0] if len(EDITORS) == 1 else (None, None)
STANDIN_VERSION = "6000.6.4f1"
REVISION = "rev-feature-0001"
PREFS_KEY = r"Software\Unity Technologies\Unity Editor 5.x"
# EditorPrefs a batch run may write (Unity appends `_h<hash>` to each name). Measured in the alpha.25 lab: a first run
# writes about 47 values, later runs rewrite these; any other change is reported as unexpected user-state mutation.
ACCEPTED_PREFS = {"LastUsedProjectPath", "kProjectBasePath", "kWorkspacePath", "UnityConnectUrlConfiguration",
                  "unity.editor_session_count", "unity.editor_sessionid", "RecompileTracker_Times",
                  "ApplicationIdleTime", "EnableEditorAnalytics", "kAutoRefreshMode", "InteractionMode"}
_STATE = {}


def windows_only(cls):
    return unittest.skipUnless(WINDOWS, "the Windows Unity batch plane; this host is not Windows")(cls)


def real(test):
    return unittest.skipIf(FAST or not WINDOWS, "GPOS_UNITY_TEST_FAST or not Windows: no real Unity process")(test)


# ---------------------------------------------------------------- user state (hashes and metadata only)

def prefs_snapshot():
    out = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, PREFS_KEY)
    except OSError:
        return out
    with key:
        i = 0
        while True:
            try:
                name, value, kind = winreg.EnumValue(key, i)
            except OSError:
                break
            data = value if isinstance(value, bytes) else repr(value).encode("utf-8")
            out[name] = hashlib.sha256(data).hexdigest()[:16]
            i += 1
    return out


def upm_configs():
    paths = (Path(os.environ["USERPROFILE"]) / ".upmconfig.toml",
             Path(os.environ.get("ALLUSERSPROFILE", r"C:\ProgramData")) / "Unity" / "config" / "upmconfig.toml")
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((str(p), st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((str(p), None, None))
    return out


def licensing_clients():
    """{pid: creation time} of every Unity licensing client (Hub's shared service): never touched, only observed."""
    procs, _ = host_win32.processes()
    out = {}
    for pid, name in procs or ():
        if name.lower() == "unity.licensing.client.exe":
            p, _ = host_win32.open_process(pid)
            if p:
                with p:
                    out[pid] = p.created()
    return out


def editors_naming(path):
    """[pid] of running Unity.exe processes whose command line names a path under `path` (this suite's own)."""
    procs, _ = host_win32.processes()
    found, needle = [], str(path).lower().replace("/", "\\")
    for pid, name in procs or ():
        if name.lower() != "unity.exe":
            continue
        p, _ = host_win32.open_process(pid)
        if not p:
            continue
        with p:
            argv, _ = p.argv()
            if p.running() and argv and any(needle in a.lower().replace("/", "\\") for a in argv):
                found.append(pid)
    return found


def setUpModule():
    if not WINDOWS or FAST:
        return
    if len(EDITORS) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C9_3A: {len(EDITORS)} Hub Unity Editors found; exactly "
                           f"one is required")
    if not LAB.is_dir():
        raise RuntimeError(f"UNITY_LAB_UNAVAILABLE_FOR_PHASE2C9_3A: the disposable lab root {LAB} does not exist")
    if editors_naming(LAB):
        raise RuntimeError("UNITY_LAB_BUSY_FOR_PHASE2C9_3A: a Unity Editor already has a lab project open")
    _STATE["prefs"], _STATE["upm"], _STATE["licensing"] = prefs_snapshot(), upm_configs(), licensing_clients()
    _STATE["work"] = LAB / f"suite-{os.getpid()}"
    _STATE["work"].mkdir()


def tearDownModule():
    if not WINDOWS or FAST or "work" not in _STATE:
        return
    try:
        left = editors_naming(_STATE["work"])
        if left:
            raise AssertionError(f"Unity processes of this suite are still running: {left}")
        now = prefs_snapshot()
        moved = {re.sub(r"_h\d+$", "", k) for k in set(now) | set(_STATE["prefs"]) if now.get(k) != _STATE["prefs"].get(k)}
        unexpected = sorted(moved - ACCEPTED_PREFS)
        _STATE["prefs_changed"] = sorted(moved)
        if unexpected:
            raise AssertionError(f"UNITY_SHARED_USER_STATE_UNEXPECTED_MUTATION: {unexpected}")
        if upm_configs() != _STATE["upm"]:
            raise AssertionError("the user's Package Manager configuration files changed")
        if licensing_clients() != _STATE["licensing"]:
            raise AssertionError("the shared Unity licensing client changed (GPOS never touches it)")
    finally:
        windows_standin.remove_tree(_STATE["work"])
        if _STATE["work"].exists():          # residue is reported, never silent
            raise AssertionError(f"this suite's lab directory could not be removed completely: {_STATE['work']}")


# ---------------------------------------------------------------- stand-in Unity (TEST_ONLY)

STAND_IN = r"""
import json, os, sys, time
cfg = json.load(open(CONFIG, encoding="utf-8"))
argv = sys.argv[1:]
if argv == ["-version"]:
    sys.stdout.write(cfg.get("version_output", VERSION + "\n")); sys.exit(cfg.get("version_exit", 0))
opts = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("-")}
json.dump({"argv": argv, "env": {k: os.environ.get(k) for k in ("UPM_USER_CONFIG_FILE", "UPM_GLOBAL_CONFIG_FILE",
           "UPM_CACHE_ROOT", "ProgramData", "LOCALAPPDATA", "APPDATA", "HTTP_PROXY", "HTTPS_PROXY", "GPOS_SECRET")},
           "cwd": os.getcwd()}, open(RECORD, "w", encoding="utf-8"))
if "log" in cfg:
    open(opts["-logFile"], "w", encoding="utf-8").write(cfg["log"])
if "results" in cfg:
    open(opts["-testResults"], "w", encoding="utf-8").write(cfg["results"])
time.sleep(cfg.get("sleep", 0))
sys.exit(cfg.get("exit", 0))
"""


def nunit(total=2, passed=2, failed=0):
    result = "Failed(Child)" if failed else "Passed"
    body = "".join(f'<test-case id="{i}" name="T{i}" result="Passed"/>' for i in range(total))
    return (f'<?xml version="1.0" encoding="utf-8"?><test-run id="2" testcasecount="{total}" result="{result}" '
            f'total="{total}" passed="{passed}" failed="{failed}" inconclusive="0" skipped="0">'
            f'<test-suite type="TestSuite" name="S">{body}</test-suite></test-run>')


class StandIn:
    """A fake Hub root holding `<version>\\Editor\\Unity.exe`, a compiled TEST_ONLY stand-in."""

    def __init__(self, directory, version=STANDIN_VERSION, name="Unity", **config):
        self.hub = Path(directory) / "Hub" / "Editor"
        self.version = version
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.config, self.record = Path(directory) / f"config-{version}.json", Path(directory) / f"record-{version}.json"
        self.config.write_text(json.dumps(config), encoding="utf-8")
        self.exe = windows_standin.write_tool(
            self.hub / version / "Editor" / name,
            f"CONFIG = {str(self.config)!r}\nRECORD = {str(self.record)!r}\nVERSION = {version!r}\n{STAND_IN}")

    def adapter(self, **kwargs):
        return UnityAdapter(hub_roots=[str(self.hub)], platform="win32", **kwargs)

    def registry(self, **kwargs):
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(self.adapter(**kwargs))
        return registry

    def recorded(self):
        return json.loads(self.record.read_text(encoding="utf-8"))


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
        return [s for s in self.specs if "-batchmode" in s.argv]


class UnityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-unitywin-")).resolve()
        self.addCleanup(windows_standin.remove_tree, self.tmp)

    def gpos_project(self, name="p", under=None):
        base = under or self.tmp
        target, n = base / name, 1
        while target.exists():
            n += 1
            target = base / f"{name}{n}"
        shutil.copytree(FIXTURE, target)
        return target

    def unity_project(self, kind="pass", version=STANDIN_VERSION, under=None, deps=None):
        p = self.gpos_project(under=under)
        fixtures.make_project(p / "Game", kind, version, EDITOR if deps is None else None,
                              dependencies=deps if deps is not None else None)
        return p

    def request(self, cap, project, unity_project="Game", **kwargs):
        if cap != ua.INSPECT:
            kwargs.setdefault("allow_mutation", not kwargs.get("dry_run", False))
        return ExecutionRequest(adapter_id="unity", capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", REVISION),
                                project_root=str(project), inputs={"unity_project": unity_project}, **kwargs)

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

    def assertContained(self, result):
        """No process of this run escaped proof: the foundation adds these codes for any unproven outcome."""
        self.assertFalse({"PROCESS_TREE_NOT_CONTAINED", "PROCESS_CAPTURE_INCOMPLETE"} & self.codes(result))


BUILTIN = {"com.unity.test-framework": "1.8.0", "com.unity.modules.imgui": "1.0.0",
           "com.unity.modules.jsonserialize": "1.0.0"}


# ---------------------------------------------------------------- WA  platform and capability gating

@windows_only
class WA_Gating(UnityCase):
    def test_the_descriptor_adds_windows_and_names_the_batch_plane(self):
        d = ua.DESCRIPTOR
        self.assertEqual(tval.validate_descriptor(FW, d, allow_test_only=False), [])
        self.assertEqual(d.supported_platforms, ("MACOS", "WINDOWS"))
        self.assertEqual(ua.WINDOWS_CAPABILITIES, (ua.INSPECT, ua.EDITMODE, ua.PLAYMODE))
        self.assertEqual(ua.WINDOWS_ENVIRONMENT, ("ProgramData", "LOCALAPPDATA"))
        self.assertEqual(len(d.capabilities), 47)

    def test_every_other_capability_is_refused_on_windows_before_anything_runs(self):
        others = [c.id for c in ua.DESCRIPTOR.capabilities if c.id not in ua.WINDOWS_CAPABILITIES]
        self.assertEqual(len(others), 44)
        with Recorder() as rec:
            for cap in others:
                with self.subTest(capability=cap):
                    outcome = UnityAdapter().execute(types.SimpleNamespace(capability_id=cap), None)
                    self.assertEqual({d.code for d in outcome.diagnostics}, {"PLATFORM_UNSUPPORTED"})
                    self.assertIn("batch plane only", outcome.diagnostics[0].message)
        self.assertEqual(rec.specs, [])

    def test_a_refused_capability_reaches_no_tool_through_the_foundation(self):
        stand_in = StandIn(self.tmp)
        p = self.unity_project(deps=BUILTIN)
        from gpos.tools.unity import build, live
        with Recorder() as rec:
            for cap in (live.STATUS, build.CAPABILITY_IDS[0]):
                with self.subTest(capability=cap):
                    result = self.run_cap(cap, p, registry=stand_in.registry())
                    self.assertNotEqual(result.status, tdg.SUCCESS)
                    if "PLATFORM_UNSUPPORTED" not in self.codes(result):
                        self.assertNotIn(result.status, (tdg.SUCCESS,))
        self.assertEqual(rec.unity_runs, [])

    def test_the_probe_reports_only_the_batch_plane_available(self):
        probe = StandIn(self.tmp).adapter().probe()
        self.assertEqual(probe.status, "AVAILABLE", probe.detail)
        available = sorted(c for c, ok, _ in probe.capability_availability if ok)
        self.assertEqual(available, sorted(ua.WINDOWS_CAPABILITIES))
        refused = [why for c, ok, why in probe.capability_availability if not ok]
        self.assertEqual(len(refused), 44)
        self.assertTrue(all("batch plane only" in why for why in refused))


# ---------------------------------------------------------------- WB  discovery and version

@windows_only
class WB_Discovery(UnityCase):
    def test_the_exact_hub_layout_is_found(self):
        stand_in = StandIn(self.tmp)
        self.assertEqual(stand_in.adapter().discover(), [(STANDIN_VERSION, str(stand_in.exe))])

    def test_the_default_root_is_the_program_files_known_folder_not_an_environment_variable(self):
        expected = (os.path.join(host_win32.program_files(), "Unity", "Hub", "Editor"),)
        saved = {k: os.environ.get(k) for k in ("ProgramFiles", "ProgramW6432")}
        try:
            os.environ["ProgramFiles"] = os.environ["ProgramW6432"] = str(self.tmp)
            self.assertEqual(UnityAdapter()._hub_roots, expected)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_the_unity_cli_hub_executables_and_path_are_never_editors(self):
        cli = windows_standin.write_tool(self.tmp / "Unity" / "bin" / "unity", "import sys\nsys.exit(0)\n")
        hub = windows_standin.write_tool(self.tmp / "Hub" / "Editor" / "resources" / "unity", "import sys\n")
        loose = windows_standin.write_tool(self.tmp / "Hub" / "Editor" / STANDIN_VERSION / "Unity", "import sys\n")
        saved = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join([str(cli.parent), str(hub.parent), str(loose.parent), saved])
        try:
            self.assertEqual(UnityAdapter(hub_roots=[str(self.tmp / "Hub" / "Editor")], platform="win32").discover(), [])
            self.assertEqual(UnityAdapter(hub_roots=[str(self.tmp / "Unity")], platform="win32").discover(), [])
        finally:
            os.environ["PATH"] = saved

    def test_only_the_exact_names_count(self):
        StandIn(self.tmp, version="6000.6.4f1", name="unity")          # wrong letter case of Unity.exe
        self.assertEqual(UnityAdapter(hub_roots=[str(self.tmp / "Hub" / "Editor")], platform="win32").discover(), [])

    def test_a_junction_on_the_way_to_the_editor_is_refused(self):
        import _winapi
        real_dir = self.tmp / "elsewhere"
        StandIn(real_dir)
        hub = self.tmp / "Hub" / "Editor"
        hub.mkdir(parents=True)
        _winapi.CreateJunction(str(real_dir / "Hub" / "Editor" / STANDIN_VERSION), str(hub / STANDIN_VERSION))
        self.assertEqual(UnityAdapter(hub_roots=[str(hub)], platform="win32").discover(), [])

    def test_missing_two_or_misreporting_editors_are_not_usable(self):
        empty = UnityAdapter(hub_roots=[str(self.tmp / "none")], platform="win32").probe()
        self.assertEqual(empty.status, "UNAVAILABLE")
        two = self.tmp / "two"
        StandIn(two, version="6000.6.4f1")
        StandIn(two, version="6000.5.8f1")
        self.assertEqual(UnityAdapter(hub_roots=[str(two / "Hub" / "Editor")], platform="win32").probe().status,
                         "VERSION_UNSUPPORTED")
        for name, config in {"other version": {"version_output": "6000.6.5f1\n"},
                             "exit 1": {"version_exit": 1}, "noise": {"version_output": "Unity 6000.6.4f1\n"}}.items():
            with self.subTest(case=name):
                probe = StandIn(self.tmp / name.replace(" ", "-"), **config).adapter().probe()
                self.assertEqual(probe.status, "VERSION_UNSUPPORTED")
                # only the tool-free static inspection stays usable, as on macOS
                self.assertEqual([c for c, ok, _ in probe.capability_availability if ok], [ua.INSPECT])

    def test_a_project_for_another_editor_version_is_not_run(self):
        stand_in = StandIn(self.tmp)
        p = self.unity_project(version="6000.5.8f1", deps=BUILTIN)
        with Recorder() as rec:
            result = self.run_cap(ua.EDITMODE, p, registry=stand_in.registry())
        self.assertStatus(result, tdg.INCOMPATIBLE, "ENGINE_EDITOR_VERSION_UNAVAILABLE")
        self.assertEqual(rec.unity_runs, [])

    def test_a_crlf_version_line_is_accepted(self):
        probe = StandIn(self.tmp, version_output=STANDIN_VERSION + "\r\n").adapter().probe()
        self.assertEqual((probe.status, probe.tool_version), ("AVAILABLE", STANDIN_VERSION))


# ---------------------------------------------------------------- WC  lock proof against a fake host

class FakeProcess:
    def __init__(self, pid, image, argv=(), user=True, running=True, created=1000, argv_problem=None):
        self.pid, self._image, self._argv, self._user = pid, image, list(argv), user
        self._running, self._created, self._argv_problem = running, created, argv_problem

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def running(self):
        return self._running

    def image(self):
        return self._image

    def created(self):
        return self._created

    def same_user(self):
        return self._user

    def argv(self):
        return (None, self._argv_problem) if self._argv_problem else (list(self._argv), None)


class FakeHost:
    def __init__(self, processes=(), owners=((),), unopenable=(), listing_problem=None):
        self._processes = {p.pid: p for p in processes}
        self._owners = list(owners)
        self._unopenable = set(unopenable)
        self._listing_problem = listing_problem
        self.owner_queries = 0

    def processes(self):
        if self._listing_problem:
            return None, self._listing_problem
        return [(pid, Path(p._image or "Unity.exe").name) for pid, p in self._processes.items()] + \
               [(pid, "Unity.exe") for pid in self._unopenable], None

    def open_process(self, pid):
        if pid in self._unopenable:
            return None, "access denied"
        return (self._processes[pid], None) if pid in self._processes else (None, None)

    def lock_owners(self, path):
        answer = self._owners[min(self.owner_queries, len(self._owners) - 1)]
        self.owner_queries += 1
        if isinstance(answer, str):
            return None, answer
        return list(answer), None


@windows_only
class WC_LockProofLogic(UnityCase):
    def setUp(self):
        super().setUp()
        self.editor = str(self.tmp / "Hub" / "6000.6.4f1" / "Editor" / "Unity.exe")
        self.project = self.tmp / "Game"
        (self.project / "Temp").mkdir(parents=True)
        self.lock = self.project / "Temp" / "UnityLockfile"

    def assess(self, host):
        return pl.assess(str(self.project), self.editor, host)

    def editor_process(self, pid, project=None, **kw):
        project = str(project or self.project)
        return FakeProcess(pid, self.editor, ["Unity.exe", "-batchmode", "-projectPath", project], **kw)

    def test_no_lockfile_and_no_editor_is_no_lock_proven_twice(self):
        host = FakeHost()
        a = self.assess(host)
        self.assertEqual((a.state, a.lock, [p[0] for p in a.processes]),
                         (pl.NO_LOCK, pl.ABSENT, [pl.NO_MATCH_PROVEN, pl.NO_MATCH_PROVEN]))
        self.assertEqual(host.owner_queries, 0)              # nothing to ask about an absent file

    def test_an_editor_naming_the_project_is_active_in_any_spelling(self):
        for spelling in (str(self.project), str(self.project).replace("\\", "/"), str(self.project).upper(),
                         str(self.project) + "\\"):
            with self.subTest(spelling=spelling):
                host = FakeHost([FakeProcess(7, self.editor, ["Unity.exe", "-projectPath", spelling])])
                self.assertEqual(self.assess(host).state, pl.ACTIVE_EDITOR)

    def test_an_import_worker_counts_as_the_projects_editor(self):
        worker = FakeProcess(8, self.editor, ["Unity.exe", "-adb2", "-batchMode", "-noUpm", "-name",
                                              "AssetImportWorkerHW0", "-projectPath",
                                              str(self.project).replace("\\", "/"), "-parentPid", "7"])
        self.assertEqual(self.assess(FakeHost([worker])).state, pl.ACTIVE_EDITOR)

    def test_editors_of_other_projects_users_and_installations_do_not_count(self):
        other = self.tmp / "Other"
        other.mkdir()
        host = FakeHost([self.editor_process(1, other),
                         self.editor_process(2, user=False),
                         FakeProcess(3, str(self.tmp / "Unity" / "bin" / "unity.exe"), ["unity", "-projectPath",
                                                                                      str(self.project)]),
                         self.editor_process(4, running=False)])
        self.assertEqual(self.assess(host).state, pl.NO_LOCK)

    def test_anything_that_cannot_be_inspected_is_unknown(self):
        cases = {
            "unopenable Unity.exe": FakeHost(unopenable=[9]),
            "unreadable command line": FakeHost([self.editor_process(9, argv_problem="status 0xc0000022")]),
            "user unknown": FakeHost([self.editor_process(9, user=None)]),
            "image unknown": FakeHost([FakeProcess(9, None)]),
            "listing failed": FakeHost(listing_problem="the process list reached its bound"),
            "no -projectPath": FakeHost([FakeProcess(9, self.editor, ["Unity.exe", "-batchmode"])]),
            "two -projectPath": FakeHost([FakeProcess(9, self.editor, ["Unity.exe", "-projectPath", "D:\\a",
                                                                       "-projectPath", "D:\\b"])]),
            "relative -projectPath": FakeHost([FakeProcess(9, self.editor, ["Unity.exe", "-projectPath", "Game"])]),
            "malformed flag": FakeHost([FakeProcess(9, self.editor, ["Unity.exe", "-projectPath=D:\\x"])]),
        }
        for name, host in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.assess(host).state, pl.LOCK_STATE_UNKNOWN)

    def test_a_verified_lockfile_user_is_an_active_editor(self):
        self.lock.write_bytes(b"")
        host = FakeHost([FakeProcess(5, str(self.tmp / "x" / "Unity.exe"), created=4242)], owners=([(5, 4242)],))
        a = self.assess(host)
        self.assertEqual((a.state, a.lock), (pl.ACTIVE_EDITOR, pl.HELD))
        self.assertIn("process 5", " ".join(a.reasons))

    def test_an_unverifiable_lockfile_user_is_unknown(self):
        self.lock.write_bytes(b"")
        cases = {"start time differs (a reused pid)": FakeHost([FakeProcess(5, "x.exe", created=1)],
                                                               owners=([(5, 2)],)),
                 "owner exited": FakeHost(owners=([(5, 2)],)),
                 "owner unopenable": FakeHost(unopenable=[5], owners=([(5, 2)],)),
                 "owner not running": FakeHost([FakeProcess(5, "x.exe", running=False, created=2)],
                                               owners=([(5, 2)],)),
                 "query failed": FakeHost(owners=("Restart Manager could not list the lockfile's users (error 5)",))}
        for name, host in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.assess(host).state, pl.LOCK_STATE_UNKNOWN)

    def test_an_orphan_needs_two_agreeing_empty_answers(self):
        self.lock.write_bytes(b"")
        orphan = FakeHost(owners=((), ()))
        a = self.assess(orphan)
        self.assertEqual((a.state, a.lock), (pl.ORPHAN_UNHELD, pl.UNHELD))
        self.assertEqual(orphan.owner_queries, 2)
        late = FakeHost([FakeProcess(5, "x.exe", created=9)], owners=((), [(5, 9)]))
        self.assertEqual(self.assess(late).state, pl.ACTIVE_EDITOR)       # it was taken during the proof
        failed = FakeHost(owners=((), "Restart Manager could not list the lockfile's users (error 5)"))
        self.assertEqual(self.assess(failed).state, pl.LOCK_STATE_UNKNOWN)

    def test_a_lockfile_that_changes_or_is_not_a_plain_file_is_unknown(self):
        self.lock.write_bytes(b"")

        class Replacing(FakeHost):
            def lock_owners(inner, path):
                os.remove(path)
                Path(path).write_bytes(b"")              # a new file: another identity
                return [], None
        self.assertEqual(self.assess(Replacing()).state, pl.LOCK_STATE_UNKNOWN)
        # and within one query: the file Restart Manager was asked about is not the one examined afterwards
        state, why, identity = pl.windows_lock_facts(str(self.project), Replacing())
        self.assertEqual((state, identity), (pl.UNKNOWN, None))
        self.assertIn("changed", why)
        self.lock.unlink()
        self.lock.mkdir()
        self.assertEqual(self.assess(FakeHost()).state, pl.LOCK_STATE_UNKNOWN)

    def test_an_editor_appearing_during_the_proof_is_seen(self):
        class Appearing(FakeHost):
            def processes(inner):
                inner.calls = getattr(inner, "calls", 0) + 1
                if inner.calls == 2:
                    inner._processes[7] = self.editor_process(7)
                return FakeHost.processes(inner)
        self.assertEqual(self.assess(Appearing()).state, pl.ACTIVE_EDITOR)

    def test_the_proof_is_time_bounded(self):
        self.lock.write_bytes(b"")
        saved = pl.MAX_PROOF_SECONDS
        pl.MAX_PROOF_SECONDS = -1.0
        try:
            self.assertEqual(self.assess(FakeHost(owners=((), ()))).state, pl.LOCK_STATE_UNKNOWN)
        finally:
            pl.MAX_PROOF_SECONDS = saved

    def test_the_windows_proof_never_opens_the_lockfile(self):
        """alpha.25 lab: a data handle on Temp/UnityLockfile makes a starting Editor abort. The Windows code paths have
        no file open of any kind; host_win32 binds no CreateFileW."""
        tree = ast.parse((ROOT / "gpos/tools/unity/project_lock.py").read_text(encoding="utf-8"))
        windows = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith(("windows_",
                                                                                                    "assess_windows"))]
        self.assertEqual(sorted(n.name for n in windows), ["assess_windows", "windows_lock_facts",
                                                           "windows_process_proof"])
        for node in windows:
            calls = {ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)}
            self.assertFalse({c for c in calls if c in ("open", "os.open", "osx.open_readonly", "io.open")
                              or c.endswith(".open_readonly")}, node.name)
        from gpos.tools.unity import host_win32 as host
        bound = {name for names in host._LIBRARIES.values() for name in names}
        code = ast.parse((ROOT / "gpos/tools/unity/host_win32.py").read_text(encoding="utf-8"))
        code.body = code.body[1:]                         # without the module docstring, which names what is never used
        named = {n.value for n in ast.walk(code) if isinstance(n, ast.Constant) and isinstance(n.value, str)} | \
                {n.attr for n in ast.walk(code) if isinstance(n, ast.Attribute)} | \
                {n.id for n in ast.walk(code) if isinstance(n, ast.Name)}
        for name in ("CreateFileW", "RmShutdown", "RmRestart", "RmAddFilter", "TerminateProcess", "LockFileEx",
                     "ReadFile", "WriteFile", "DeleteFileW"):
            self.assertNotIn(name, bound)
            self.assertNotIn(name, named)


# ---------------------------------------------------------------- WD  lock proof against the real host (no Unity)

HOLDER = r"""
import ctypes, sys, time
from ctypes import wintypes
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                            wintypes.DWORD, wintypes.HANDLE)
h = k32.CreateFileW(sys.argv[1], 0xC0000000, 0, None, 4, 0, None)   # GENERIC_READ|WRITE, share 0, OPEN_ALWAYS
if h in (None, ctypes.c_void_p(-1).value):
    print("failed", ctypes.get_last_error(), flush=True); sys.exit(2)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


def hold_exclusively(path, seconds=120):
    """TEST-ONLY: a child Python process that holds `path` open exclusively, as a running Unity Editor does."""
    child = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), str(seconds)], stdout=subprocess.PIPE, text=True)
    line = child.stdout.readline().strip()
    child.stdout.close()                       # nothing more is read from it
    assert line == "held", line
    return child


@windows_only
class WD_LockProofRealHost(UnityCase):
    def setUp(self):
        super().setUp()
        self.project = self.tmp / "My Game"            # a space: the command line must be split as Windows does
        (self.project / "Temp").mkdir(parents=True)
        self.lock = self.project / "Temp" / "UnityLockfile"
        self.editor = str(EDITOR or self.tmp / "no-editor" / "Unity.exe")

    def test_restart_manager_names_an_exclusive_holder_with_its_exact_start_time(self):
        child = hold_exclusively(self.lock)
        try:
            owners, problem = host_win32.lock_owners(self.lock)
            self.assertIsNone(problem)
            self.assertEqual([pid for pid, _ in owners], [child.pid])
            p, _ = host_win32.open_process(child.pid)
            with p:
                self.assertEqual(owners[0][1], p.created())
            a = pl.assess(str(self.project), self.editor)
            self.assertEqual((a.state, a.lock), (pl.ACTIVE_EDITOR, pl.HELD))
        finally:
            child.terminate()            # the test's own child, by its own handle
            child.wait(10)

    def test_an_unheld_lockfile_is_an_orphan_and_an_absent_one_is_no_lock(self):
        self.assertEqual(pl.assess(str(self.project), self.editor).state, pl.NO_LOCK)
        self.lock.write_bytes(b"")
        a = pl.assess(str(self.project), self.editor)
        self.assertEqual((a.state, a.lock), (pl.ORPHAN_UNHELD, pl.UNHELD))

    def test_the_proof_holds_no_handle_that_could_stop_an_exclusive_open(self):
        """Non-interference without Unity: while the proof runs in a loop, an exclusive open (what Unity does) always
        succeeds."""
        self.lock.write_bytes(b"")
        stop, proofs = threading.Event(), [0]

        def loop():
            while not stop.is_set():
                pl.assess(str(self.project), self.editor)
                proofs[0] += 1
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        try:
            for _ in range(20):
                child = hold_exclusively(self.lock, seconds=0.05)
                child.wait(10)
        finally:
            stop.set()
            t.join(30)
        self.assertGreater(proofs[0], 0)

    def test_the_process_proof_reads_an_editor_command_line(self):
        fake_editor = windows_standin.write_tool(self.tmp / "Hub" / "6000.6.4f1" / "Editor" / "Unity",
                                                 "import time\ntime.sleep(60)\n")
        other = self.tmp / "Other"
        other.mkdir()
        for target, expected in ((self.project, pl.MATCHING_EDITOR), (other, pl.NO_MATCH_PROVEN)):
            with self.subTest(target=target.name):
                child = subprocess.Popen([str(fake_editor), "-batchmode", "-projectPath",
                                          str(target).replace("\\", "/")])
                try:
                    deadline = time.monotonic() + 20
                    verdict = None
                    while time.monotonic() < deadline:
                        verdict, why = pl.windows_process_proof(str(self.project), str(fake_editor), host_win32)
                        if verdict == expected:
                            break
                        time.sleep(0.2)
                    self.assertEqual(verdict, expected)
                finally:
                    child.terminate()
                    child.wait(10)


# ---------------------------------------------------------------- WE  environment

@windows_only
class WE_Environment(UnityCase):
    def test_exactly_two_more_variables_are_inherited(self):
        policy = ua.upm_environment(self.tmp, self.tmp / "cache")
        self.assertEqual(policy.inherit, tproc.SAFE_ENV_DEFAULTS + tproc.WINDOWS_ENV_DEFAULTS
                         + ("ProgramData", "LOCALAPPDATA"))
        parent = dict(os.environ, APPDATA=r"C:\x", HTTPS_PROXY="http://proxy", GPOS_SECRET="s3cr3t",
                      ProgramData=r"C:\ProgramData", LOCALAPPDATA=r"C:\L")
        env = policy.build(parent)
        self.assertEqual((env.get("ProgramData"), env.get("LOCALAPPDATA")), (r"C:\ProgramData", r"C:\L"))
        for name in ("APPDATA", "HTTPS_PROXY", "GPOS_SECRET"):
            self.assertNotIn(name, env)
        self.assertEqual(set(policy.metadata()["set_names"]), {"UPM_USER_CONFIG_FILE", "UPM_GLOBAL_CONFIG_FILE",
                                                                "UPM_CACHE_ROOT"})

    def test_the_editor_receives_them_and_nothing_else(self):
        stand_in = StandIn(self.tmp, results=nunit(), log="ok\n")
        p = self.unity_project(deps=BUILTIN)
        saved = os.environ.get("GPOS_SECRET")
        os.environ["GPOS_SECRET"] = "s3cr3t"
        try:
            result = self.run_cap(ua.EDITMODE, p, registry=stand_in.registry())
        finally:
            if saved is None:
                os.environ.pop("GPOS_SECRET", None)
        self.assertStatus(result, tdg.SUCCESS)
        env = stand_in.recorded()["env"]
        self.assertTrue(env["ProgramData"] and env["LOCALAPPDATA"])
        self.assertEqual((env["APPDATA"], env["GPOS_SECRET"], env["HTTPS_PROXY"]), (None, None, None))
        self.assertTrue(env["UPM_CACHE_ROOT"].lower().startswith(str(p).lower()))


# ---------------------------------------------------------------- WF  classification

PACKAGE_MANAGER_LOG = ("[Package Manager] Could not connect to IPC stream \"Upm-5340\" after 30.0 seconds.\n"
                       "[Package Manager] Failed to start the Unity Package Manager local server process. Make sure the "
                       "process [C:/Program Files/Unity/Hub/Editor/6000.6.4f1/Editor/Data/Resources/PackageManager/"
                       "Server/UnityPackageManager.exe] is not blocked by Windows Defender or any other anti-virus "
                       "configuration.\n")
LOCK_ABORT_LOG = ("Aborting batchmode due to fatal error:\nIt looks like another Unity instance is running with this "
                  "project open.\n\nMultiple Unity instances cannot open the same project.\n")


@windows_only
class WF_Classification(UnityCase):
    def test_windows_log_texts_are_classified(self):
        self.assertEqual(ur.classify_log(PACKAGE_MANAGER_LOG), ur.PACKAGE_MANAGER_UNAVAILABLE)
        self.assertEqual(ur.classify_log(LOCK_ABORT_LOG), ur.PROJECT_LOCKED)
        self.assertEqual(ur.classify_log("Scripts have compiler errors.\n"), ur.COMPILE_ERROR)
        self.assertEqual(ur.classify_log("something else\n"), ur.UNCLASSIFIED)

    def test_a_package_manager_failure_is_a_failed_run_with_its_cause(self):
        stand_in = StandIn(self.tmp, log=PACKAGE_MANAGER_LOG, exit=1)
        result = self.run_cap(ua.EDITMODE, self.unity_project(deps=BUILTIN), registry=stand_in.registry())
        self.assertStatus(result, tdg.FAILED)
        self.assertEqual(result.data.get("cause"), ur.PACKAGE_MANAGER_UNAVAILABLE)
        self.assertIn("Package Manager", self.messages(result))
        self.assertEqual(result.evidence_candidates, ())

    def test_unitys_own_lock_refusal_is_project_locked(self):
        stand_in = StandIn(self.tmp, log=LOCK_ABORT_LOG, exit=1)
        result = self.run_cap(ua.EDITMODE, self.unity_project(deps=BUILTIN), registry=stand_in.registry())
        self.assertStatus(result, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")


# ---------------------------------------------------------------- WG  real batch runs

REAL_RUNS = {}


def real_run(case, kind, cap, **kwargs):
    """One real Unity run per (fixture kind, capability); results are shared within the module."""
    key = (kind, cap)
    if key not in REAL_RUNS:
        p = case.gpos_project(name=f"{kind}-{cap.split('-')[1][:4]}", under=_STATE["work"])
        fixtures.make_project(p / "Game", kind, EDITOR_VERSION, EDITOR)
        with Recorder() as rec:
            result = case.run_cap(cap, p, **kwargs)
        REAL_RUNS[key] = (result, p, rec.specs)
    return REAL_RUNS[key]


@windows_only
class WG_RealBatch(UnityCase):
    @real
    def test_the_real_probe(self):
        probe = default_registry(FW).probe("unity")
        self.assertEqual((probe.status, probe.tool_version), ("AVAILABLE", EDITOR_VERSION), probe.detail)
        self.assertTrue(probe.tool_path.lower().endswith("\\editor\\unity.exe"))

    @real
    def test_inspect_project_reads_the_project_without_unity(self):
        p = self.gpos_project(under=_STATE["work"])
        fixtures.make_project(p / "Game", "pass", EDITOR_VERSION, EDITOR)
        with Recorder() as rec:
            result = self.run_cap(ua.INSPECT, p)
        self.assertStatus(result, tdg.SUCCESS)
        self.assertEqual(result.data["editor_version"], EDITOR_VERSION)
        self.assertEqual(rec.unity_runs, [])

    @real
    def test_editmode_pass_offers_test_evidence(self):
        result, p, specs = real_run(self, "pass", ua.EDITMODE)
        self.assertStatus(result, tdg.SUCCESS)
        self.assertContained(result)
        self.assertEqual((result.data["total"], result.data["passed"], result.data["failed"]), (2, 2, 0))
        self.assertEqual(result.data["project_lock"], pl.NO_LOCK)
        (candidate,) = result.evidence_candidates
        self.assertEqual((candidate.evidence_type, candidate.capture_context), ("TEST_EVIDENCE", "AUTOMATED_TEST"))
        self.assertEqual(sorted(a.artifact_id for a in result.artifacts), ["editor-log", "results"])
        self.assertEqual(len([s for s in specs if "-runTests" in s.argv]), 1)

    @real
    def test_editmode_fail_is_reported_with_its_counts(self):
        result, _, _ = real_run(self, "fail", ua.EDITMODE)
        self.assertIn("TESTS_FAILED", self.codes(result), self.messages(result))
        self.assertContained(result)
        self.assertEqual((result.data["total"], result.data["failed"]), (2, 1))
        self.assertEqual(len(result.evidence_candidates), 1)       # a failed run is still test evidence

    @real
    def test_playmode_pass_and_fail(self):
        passed, _, _ = real_run(self, "pass", ua.PLAYMODE)
        self.assertStatus(passed, tdg.SUCCESS)
        self.assertEqual((passed.data["total"], passed.data["failed"]), (1, 0))
        failed, _, _ = real_run(self, "fail", ua.PLAYMODE)
        self.assertIn("TESTS_FAILED", self.codes(failed), self.messages(failed))
        for r in (passed, failed):
            self.assertContained(r)

    @real
    def test_a_compile_error_is_a_failed_run_without_evidence(self):
        result, _, _ = real_run(self, "compile", ua.EDITMODE)
        self.assertStatus(result, tdg.FAILED)
        self.assertEqual(result.data.get("cause"), ur.COMPILE_ERROR)
        self.assertEqual(result.evidence_candidates, ())
        self.assertContained(result)

    @real
    def test_provenance_carries_no_fabricated_revision(self):
        result, _, _ = real_run(self, "pass", ua.EDITMODE)
        record = result.to_dict()
        self.assertIsNone(record["provenance"].get("build_revision"))     # Windows Git is unavailable; never invented
        self.assertEqual(record["provenance"]["tool_version"], EDITOR_VERSION)
        argv = record["provenance"]["command"]["argv"]
        self.assertIn("<unity-project>", argv)
        self.assertNotIn("-quit", argv)


# ---------------------------------------------------------------- WH  real project lock and Job behaviour

def hold_editor(project, seconds):
    """TEST-ONLY: one lab Editor that keeps `project` open (batch mode, no -quit) until its timeout ends it, started
    through the process boundary so it is contained in its own Job. Returns (thread, outcome holder, pid holder)."""
    from gpos.tools import process_win32 as pwin
    box, pids = {}, {}
    workspace = Path(project).parent / "hold-workspace"
    workspace.mkdir(exist_ok=True)
    spec = tproc.ToolProcessSpec(executable=EDITOR, argv=("-batchmode", "-projectPath", str(project), "-logFile",
                                                          str(workspace / "editor.log")),
                                 cwd=str(workspace), timeout=float(seconds),
                                 env=ua.upm_environment(workspace, Path(project).parent / "hold-cache"))

    def run():
        box["outcome"] = tproc.run_process(spec, [str(Path(project).parent)])
    original = pwin.HOOKS.get("before_resume")
    pwin.HOOKS["before_resume"] = lambda info, readers: pids.setdefault("root", info.dwProcessId)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    deadline = time.monotonic() + 30
    while "root" not in pids and time.monotonic() < deadline:
        time.sleep(0.05)
    if original is None:
        pwin.HOOKS.pop("before_resume", None)
    else:
        pwin.HOOKS["before_resume"] = original
    return t, box, pids


@windows_only
class WH_RealLockAndJob(UnityCase):
    @real
    def test_an_open_editor_blocks_the_run_and_its_orphan_does_not(self):
        p = self.gpos_project(name="locked", under=_STATE["work"])
        fixtures.make_project(p / "Game", "pass", EDITOR_VERSION, EDITOR)
        lock = p / "Game" / "Temp" / "UnityLockfile"
        thread, box, pids = hold_editor(p / "Game", 90)
        try:
            deadline = time.monotonic() + 90
            owners = []
            while time.monotonic() < deadline:
                if lock.exists():
                    owners, _ = host_win32.lock_owners(lock)
                    if owners:
                        break
                time.sleep(0.25)
            self.assertEqual([pid for pid, _ in owners], [pids["root"]])
            with Recorder() as rec:
                blocked = self.run_cap(ua.EDITMODE, p)
            self.assertStatus(blocked, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
            self.assertEqual(blocked.data["project_lock"], pl.ACTIVE_EDITOR)
            self.assertEqual(rec.unity_runs, [])                       # no second Editor was started
        finally:
            thread.join(150)
        held = box["outcome"]
        self.assertTrue(held.timed_out and held.tree_contained and held.capture_complete)
        self.assertEqual(editors_naming(p), [])                        # the whole tree ended with its Job
        self.assertTrue(lock.exists())                                 # an orphan: nobody holds it now
        self.assertEqual(pl.assess(str(p / "Game"), EDITOR).state, pl.ORPHAN_UNHELD)
        result = self.run_cap(ua.EDITMODE, p)
        self.assertStatus(result, tdg.SUCCESS)
        self.assertIn("ENGINE_PROJECT_ORPHAN_LOCK", self.codes(result))
        self.assertEqual(result.data["project_lock"], pl.ORPHAN_UNHELD)

    @real
    def test_the_lock_proof_does_not_disturb_a_starting_editor(self):
        result, p, _ = real_run(self, "pass", ua.EDITMODE)
        lock = p / "Game" / "Temp" / "UnityLockfile"
        lock.parent.mkdir(exist_ok=True)
        lock.write_bytes(b"")                                          # an orphan, as a killed run leaves
        stop, proofs = threading.Event(), [0]

        def loop():
            while not stop.is_set():
                pl.assess(str(p / "Game"), EDITOR)
                proofs[0] += 1
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        try:
            again = self.run_cap(ua.EDITMODE, p, output_dir=str(p / "again"))
        finally:
            stop.set()
            t.join(60)
        self.assertGreater(proofs[0], 0)
        self.assertNotEqual(again.data.get("cause"), ur.PROJECT_LOCKED, self.messages(again))
        self.assertNotIn("ENGINE_PROJECT_LOCKED", self.codes(again))

    @real
    def test_a_timeout_ends_the_whole_tree(self):
        p = self.gpos_project(name="timeout", under=_STATE["work"])
        fixtures.make_project(p / "Game", "pass", EDITOR_VERSION, EDITOR)
        started = time.monotonic()
        result = self.run_cap(ua.EDITMODE, p, timeout=6.0)
        self.assertStatus(result, tdg.TIMED_OUT)
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual(result.evidence_candidates, ())
        self.assertContained(result)
        self.assertEqual(editors_naming(p), [])

    @real
    def test_a_real_package_manager_failure_is_classified(self):
        p = self.gpos_project(name="upm", under=_STATE["work"])
        fixtures.make_project(p / "Game", "pass", EDITOR_VERSION, EDITOR)
        saved = ua.WINDOWS_ENVIRONMENT
        ua.WINDOWS_ENVIRONMENT = ("LOCALAPPDATA",)                  # TEST-ONLY: without ProgramData (lab-measured)
        try:
            result = self.run_cap(ua.EDITMODE, p)
        finally:
            ua.WINDOWS_ENVIRONMENT = saved
        self.assertStatus(result, tdg.FAILED)
        self.assertEqual(result.data.get("cause"), ur.PACKAGE_MANAGER_UNAVAILABLE)
        self.assertContained(result)


# ---------------------------------------------------------------- WI  user state

@windows_only
class WI_UserState(UnityCase):
    @real
    def test_the_licensing_client_is_observed_never_touched(self):
        self.assertEqual(licensing_clients(), _STATE["licensing"])

    @real
    def test_the_users_package_manager_configuration_is_unchanged(self):
        self.assertEqual(upm_configs(), _STATE["upm"])


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    mode = "not Windows: every group skipped" if not WINDOWS else (
        "FAST: no real Unity process" if FAST else f"real Unity {EDITOR_VERSION} at {EDITOR}; lab {LAB}")
    print(f"GPOS Windows Unity batch tests ({mode})")
    if _STATE.get("prefs_changed") is not None:
        print(f"EditorPrefs names changed by this run: {_STATE['prefs_changed']}")
    sys.exit(0 if result.wasSuccessful() else 1)
