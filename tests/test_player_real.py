#!/usr/bin/env python3
"""Phase 2C-8 (alpha.22) — the player adapter against a real Unity Player (QualGame), on this macOS host.

    python3 tests/test_player_real.py                                    # RP groups (no Screen Recording needed)
    GPOS_PLAYER_CAPTURE_DENIED=1 python3 tests/test_player_real.py RC8   # once, BEFORE the Human grant
    GPOS_REAL_CAPTURE=1 python3 tests/test_player_real.py                # RP + RC groups, AFTER the Human grant

One synthetic QualGame project is built once per run through the frozen alpha.21 qualified-build workflow (Git R ->
inspect -> Git R -> build(R) -> Git R) with a unique company/product/bundle identifier. Lab-owned batch Editors only.

RP groups run against a scratch install of the helper release (supervise needs no permission):
    RP1  launch, status, graceful stop through the supervisor; the sanitized log artifact and its evidence
    RP2  a hanging Player is forced by its supervisor
    RP3  Quit(0), Quit(3), an exception and an abort crash are classified from the supervisor's observation
    RP4  foreign instances: a renamed copy elsewhere launched by LaunchServices, a manual launch of the same payload;
         an unrelated running application does not block
    RP5  the payload drifts while running: status reports DRIFT, the proven Player still stops, no log evidence
    RP6  crash points: the launching command dies before the runtime binding (the supervisor's deadline abandons,
         stop resolves it) and after the commit (a later command stops it normally)
    RP7  the supervisor is killed and the helper install removed: the in-process fallback stops the proven Player
    RP8  the installed helper is moved or deleted after launch: status reports it, the running supervisor (proven by
         pid, kernel start time and code hash, not by its path) still stops the Player
    RP9  persistence (log half): a graceful stop saves, the relaunch restores the same value
RC groups use the Human-granted helper at ~/Applications/GPOS (GPOS_REAL_CAPTURE=1):
    RC1  screenshots: windowed, and a large window downscaled to at most 1920 px with its aspect ratio
    RC2  videos of 1 s and 15 s; 0 and 16 refused
    RC3  the Player exits mid-video; the helper is killed mid-video: nothing is published
    RC4  a hidden window is refused
    RC5  privacy: an unrelated window covering the Player is absent from the screenshot and every sampled video frame
         (positive control); the detector finds it in a synthetic image and the occluder is proven on top (negative
         controls)
    RC6  persistence (visual half): the restored value is decoded from the screenshot
    RC7  composition: player.capture-video -> ffprobe.inspect -> ffmpeg.extract-frame -> ffmpeg.extract-clip, the derived
         outputs keep DIAGNOSTIC_RUNTIME and name the runtime video as origin
    RC8  (GPOS_PLAYER_CAPTURE_DENIED=1, before the grant) shot and video refuse with CAPTURE_PERMISSION_REQUIRED without
         touching ScreenCaptureKit and publish nothing
The game's own side effects (Application Support/GposTest/<product>, Preferences/<bundle>.plist, Saved Application
State, a crash report from RP3) are never read or deleted by GPOS; the run prints them for the Human.
"""

import json
import os
import plistlib
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_unity_build as tub  # noqa: E402
import test_unity_live as tl  # noqa: E402
import unity_fixture_builder as fixtures  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import leases as lease_mod  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Actor, InputArtifact, Subject  # noqa: E402
from gpos.tools.player import PlayerAdapter  # noqa: E402
from gpos.tools.player import appkit  # noqa: E402
from gpos.tools.player import contract as c  # noqa: E402
from gpos.tools.player import helper as hp  # noqa: E402
from gpos.tools.player import macos  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.unity import adapter as ua  # noqa: E402
from gpos.tools.unity import bridge_install as bi  # noqa: E402

FW = load_framework()
AGENT = Actor("AGENT", "player-real")
OTHER = Actor("AGENT", "player-other")
CAPTURE = os.environ.get("GPOS_REAL_CAPTURE") == "1"
DENIED = os.environ.get("GPOS_PLAYER_CAPTURE_DENIED") == "1"
TESTKIT = ROOT / "tests" / "player_testkit"
REV = "0123456789abcdef0123456789abcdef01234567"
STATE = {}


# ---------------------------------------------------------------- the QualGame build

def batch_testkit(p, qual_id):
    ws = Path(tempfile.mkdtemp(prefix="tk-", dir=str(p.parent)))
    for n in (ua.UPM_USER_NAME, ua.UPM_GLOBAL_NAME):
        (ws / n).write_text("")
    cache = p / ".game" / "gpos-runtime" / "unity" / "upm-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env = ua.upm_environment(ws, cache).build()
    r = subprocess.run([tl.EDITOR, "-batchmode", "-projectPath", str(p / "Game"), "-logFile", str(ws / "editor.log"),
                        "-upmLogFile", str(ws / "upm.log"), "-cacheServerEnableDownload", "false",
                        "-cacheServerEnableUpload", "false", "-executeMethod", "Gpos.PlayerTestkit.Testkit.Run",
                        "-gposQualId", qual_id], cwd=str(ws), env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
    if r.returncode != 0:
        raise AssertionError(f"player testkit failed ({r.returncode}); see {ws}/editor.log")


def qual_build():
    qual_id = uuid.uuid4().hex[:8]
    p = STATE["work"] / "qual" / "p"
    shutil.copytree(tl.FIXTURE, p)
    fixtures.make_project(p / "Game", "pass", tl.EDITOR_VERSION, tl.EDITOR)
    bi.install(p, p / "Game", bi.verify_source())
    shutil.copytree(TESTKIT / "com.gpos.player-testkit", p / "Game" / "Packages" / "com.gpos.player-testkit")
    (p / "Game" / "Assets" / "Qual").mkdir(parents=True)
    shutil.copy(TESTKIT / "QualGame.cs", p / "Game" / "Assets" / "Qual" / "QualGame.cs")
    (p / ".gitignore").write_text(tub.GITIGNORE)
    tub.rgit(p, "init", "-q")
    tub.commit(p, "fixture")
    batch_testkit(p, qual_id)
    tub.commit(p, "qual game setup")
    s1, r1 = tub.provenance(p)
    i = tl.real_request(p, "unity.inspect-build-configuration", timeout=1800, allow_mutation=True)
    assert i.data and i.data["buildable"], [d.message for d in i.diagnostics]
    s2, r2 = tub.provenance(p)
    b = tl.real_request(p, "unity.build-player", timeout=1800, build_revision=r1, allow_mutation=True,
                        inputs={"unity_project": "Game", "expected_configuration_token": i.data["configuration_token"]})
    s3, r3 = tub.provenance(p)
    assert b.status == tdg.SUCCESS and r1 == r2 == r3, [d.message for d in b.diagnostics]
    return p, b.data["build_id"], qual_id


def setUpModule():
    if len(tl.EDITORS) != 1:
        raise unittest.SkipTest(f"NOT_RUN: {len(tl.EDITORS)} Hub Unity Editors found; exactly one is required")
    STATE["work"] = Path(tempfile.mkdtemp(prefix="gpos-player-real-")).resolve()
    STATE["host"] = STATE["work"] / "home" / "Applications" / "GPOS"
    (STATE["work"] / "home").mkdir()
    hp.install(STATE["host"], "real-scratch")
    STATE["project"], STATE["build_id"], STATE["qual_id"] = qual_build()
    STATE["app_id"] = f"com.gpos.test.playerqual{STATE['qual_id']}"
    STATE["started"] = time.time()


def tearDownModule():
    try:
        for pid in macos.same_application(STATE.get("app_id", "-")) if STATE.get("app_id") else []:
            appkit.request_terminate(pid["pid"], STATE["app_id"], pid["executable"])
    finally:
        if STATE.get("qual_id"):
            home = Path.home()
            product = f"GposPlayerQual{STATE['qual_id']}"
            print("\nQualGame side effects left for the Human (GPOS never reads or deletes them):")
            for path in (home / "Library/Application Support/GposTest" / product,
                         home / f"Library/Preferences/{STATE['app_id']}.plist",
                         home / f"Library/Saved Application State/{STATE['app_id']}.savedState"):
                print(f"  {'present' if path.exists() else 'absent '}  {path}")
            crashes = [p for p in (home / "Library/Logs/DiagnosticReports").glob(f"{product}*")
                       if p.stat().st_mtime >= STATE.get("started", 0)]
            for p in crashes:
                print(f"  present  {p}")
        shutil.rmtree(STATE.get("work", "/nonexistent"), ignore_errors=True)


# ---------------------------------------------------------------- requests

def registry(production=False):
    if production:
        return default_registry(FW)
    r = ToolRegistry(FW)
    r.register(PlayerAdapter(host_location=lambda: str(STATE["host"])))
    return r


def run(cap, production=False, actor=AGENT, **kw):
    if cap != c.STATUS and not kw.get("dry_run"):
        kw.setdefault("allow_mutation", True)
    request = ExecutionRequest(adapter_id="player", capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", REV),
                               project_root=str(STATE["project"]), actor=actor, **kw)
    return execute(registry(production), request)


def mode(name):
    control = STATE["project"] / ".game/gpos-runtime/tool-output/player/player-qual-control.txt"
    control.parent.mkdir(parents=True, exist_ok=True)
    control.write_text(name)


def codes(r):
    return {d.code for d in r.diagnostics}


def text(r):
    return " | ".join(f"{d.code}: {d.message}" for d in r.diagnostics)


def runtime_dir(r):
    return STATE["project"] / ".game/gpos-runtime" / r.data["runtime_dir"]


def artifact_text(r, artifact_id):
    a = next(a for a in r.artifacts if a.artifact_id == artifact_id)
    return (STATE["project"] / a.path).read_text()


def wait_until(predicate, timeout, step=0.2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = predicate()
        if v:
            return v
        time.sleep(step)
    return None


class RealCase(unittest.TestCase):
    production = False

    def setUp(self):
        mode("animate")
        self.assertEqual(macos.same_application(STATE["app_id"]), [], "a Player of an earlier test is still running")

    def tearDown(self):
        for f in macos.same_application(STATE["app_id"]):
            appkit.request_terminate(f["pid"], STATE["app_id"], f["executable"])
        wait_until(lambda: not macos.same_application(STATE["app_id"]), 15)
        lease = lease_mod.holder(STATE["project"], "player", f"PLAYER_RUNTIME:{STATE['project']}")
        if lease:   # never leave a session behind for the next test: stop it as its owner
            run(c.STOP, production=self.production, session_id=lease["session"]["session_id"],
                actor=Actor(*lease["owner_id"].split(":", 1)), inputs={"recover_proven_gone": True})

    def launch(self, **kw):
        r = run(c.LAUNCH, production=self.production, build_id=STATE["build_id"], **kw)
        self.assertEqual(r.status, tdg.SUCCESS, text(r))
        return r.data["session_id"], r

    def stop(self, sid, **kw):
        kw.setdefault("build_id", STATE["build_id"])
        return run(c.STOP, production=self.production, session_id=sid, **kw)

    def status(self, sid):
        return run(c.STATUS, production=self.production, session_id=sid)

    def ready(self, r):
        """TEST-SIDE readiness: QualGame logs `GPOSQ ready` once Unity's splash screen has finished (a capture before it
        shows the splash, which GPOS documents and never hides)."""
        rdir = runtime_dir(r)
        self.assertTrue(wait_until(lambda: (rdir / "player.log").exists()
                                   and "GPOSQ ready" in (rdir / "player.log").read_text(errors="replace"), 60))

    def ticks(self, r, n=3):
        rdir = runtime_dir(r)
        self.assertTrue(wait_until(lambda: (rdir / "player.log").exists()
                                   and f"GPOSQ tick={n}" in (rdir / "player.log").read_text(errors="replace"), 40))


# ---------------------------------------------------------------- RP1..RP9

class RP1_LaunchStatusStop(RealCase):
    def test_the_whole_lifecycle(self):
        sid, launch = self.launch()
        self.assertEqual(launch.provenance.command["argv"][0], "supervise")
        self.assertEqual(launch.provenance.command["executable"],
                         str(hp.executable(Path(os.path.realpath(STATE["host"])) / hp.BUNDLE_NAME)))
        binding = json.loads((runtime_dir(launch) / "runtime-binding.json").read_text())
        p = binding["player"]
        self.assertEqual(macos.identity(p["pid"], p["start_sec"], p["start_usec"], p["executable"]), c.PROVEN)
        self.assertEqual(macos.facts(p["pid"])["ppid"], binding["supervisor"]["pid"])
        self.assertIn(binding["supervisor"]["cdhash"], hp.cdhashes().values())
        self.ticks(launch)
        s = self.status(sid)
        self.assertEqual(s.status, tdg.SUCCESS, text(s))
        d = s.data
        self.assertEqual((d["phase"], d["identity"], d["supervisor"], d["build"]["trust"], d["helper"]),
                         ("BOUND", "PROVEN", "ALIVE", "VALID", "EXACT"))
        self.assertTrue(wait_until(lambda: self.status(sid).data["window"]["present"], 20))
        self.assertIsNone(s.provenance.command)
        r = self.stop(sid)
        self.assertEqual(r.status, tdg.SUCCESS, text(r))
        self.assertEqual((r.data["classification"], r.data["path"], r.data["code"]), ("GRACEFUL_STOP", "SUPERVISOR", 0))
        self.assertIsNone(r.provenance.command)
        log = artifact_text(r, "runtime-log")
        self.assertIn("GPOSQ start", log)
        self.assertIn("GPOSQ quitting", log)
        self.assertNotIn("qual-secret-value", log)
        self.assertNotIn(str(Path.home()), log)
        self.assertNotIn("Support/GposTest", log)
        (cand,) = r.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context), ("RUNTIME_EVIDENCE", "DIAGNOSTIC_RUNTIME"))
        raw = (runtime_dir(launch) / "player.log").read_text(errors="replace")
        self.assertIn("qual-secret-value", raw)              # negative control: the plant really was there
        self.assertIn("Application Support/GposTest", raw)
        self.assertEqual(macos.same_application(STATE["app_id"]), [])
        self.assertIsNone(lease_mod.holder(STATE["project"], "player", f"PLAYER_RUNTIME:{STATE['project']}"))


class RP2_Hang(RealCase):
    def test_a_hanging_player_is_forced(self):
        mode("hang")
        sid, launch = self.launch()
        self.assertTrue(wait_until(lambda: "GPOSQ hanging" in (runtime_dir(launch) / "player.log").read_text(
            errors="replace"), 40))
        r = self.stop(sid)
        self.assertEqual((r.data["classification"], r.data["path"]), ("FORCED_STOP", "SUPERVISOR"), text(r))
        self.assertEqual(r.data["signal"], 9)


class RP3_Exits(RealCase):
    def ended(self, name):
        mode(name)
        sid, launch = self.launch()
        rdir = runtime_dir(launch)
        self.assertTrue(wait_until(lambda: (rdir / "exit.json").exists(), 60), f"{name}: no exit observed")
        s = self.status(sid)
        self.assertEqual(s.data["identity"], "GONE")
        return self.stop(sid)

    def test_quit_0_and_3(self):
        r = self.ended("exit0")
        self.assertEqual((r.data["classification"], r.data["code"], r.data["path"]), ("EXITED", 0, "NONE"))
        self.assertEqual(len(r.evidence_candidates), 1)
        r = self.ended("exit3")
        self.assertEqual((r.data["classification"], r.data["code"]), ("EXITED", 3))

    def test_an_abort_crash(self):
        r = self.ended("crash")
        self.assertEqual((r.data["classification"], r.data["signal"]), ("CRASHED", 6))

    def test_an_exception_does_not_end_the_player(self):
        mode("exception")
        sid, launch = self.launch()
        self.assertTrue(wait_until(lambda: "GPOSQ deliberate exception" in (runtime_dir(launch) / "player.log").read_text(
            errors="replace"), 40))
        time.sleep(1)
        self.assertEqual(self.status(sid).data["identity"], "PROVEN")
        self.assertEqual(self.stop(sid).data["classification"], "GRACEFUL_STOP")


class RP4_Foreign(RealCase):
    def payload(self):
        return STATE["project"] / ".game/gpos-runtime/tool-output/unity" / STATE["build_id"][len("build-"):] / \
            "payload/Player.app"

    def test_a_renamed_copy_elsewhere_launched_by_launchservices(self):
        copy = STATE["work"] / "foreign dir" / "Copy Of Game.app"
        copy.parent.mkdir(exist_ok=True)
        subprocess.run(["ditto", str(self.payload()), str(copy)], check=True)
        subprocess.run(["open", "-n", str(copy)], check=True)
        self.assertTrue(wait_until(lambda: macos.same_application(STATE["app_id"]), 20))
        r = run(c.LAUNCH, build_id=STATE["build_id"])
        self.assertEqual(r.status, tdg.CONFLICT, text(r))
        self.assertIn("PLAYER_RUNTIME_CONFLICT", codes(r))
        self.assertEqual(len(macos.same_application(STATE["app_id"])), 1, "the foreign instance was not touched")
        self.assertIsNone(lease_mod.holder(STATE["project"], "player", f"PLAYER_RUNTIME:{STATE['project']}"))

    def test_a_manual_launch_of_the_same_payload(self):
        info = plistlib.loads((self.payload() / "Contents/Info.plist").read_bytes())
        exe = self.payload() / "Contents/MacOS" / info["CFBundleExecutable"]
        manual = subprocess.Popen([str(exe)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: manual.poll() is None and manual.kill())
        self.assertTrue(wait_until(lambda: macos.same_application(STATE["app_id"]), 20))
        r = run(c.LAUNCH, build_id=STATE["build_id"])
        self.assertIn("PLAYER_RUNTIME_CONFLICT", codes(r))
        self.assertIsNone(manual.poll(), "the manual instance is never signalled")

    def test_an_unrelated_application_does_not_block(self):
        occ = fixture("Occluder")
        other = subprocess.Popen([str(occ), "0", "20"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(other.kill)
        sid, _ = self.launch()
        self.assertEqual(self.stop(sid).data["classification"], "GRACEFUL_STOP")


class RP5_Drift(RealCase):
    def test_drift_is_reported_and_never_blocks_the_stop(self):
        sid, launch = self.launch()
        target = self.payload_file()
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# drift\n")
            s = self.status(sid)
            self.assertEqual(s.data["build"]["trust"], "DRIFT")
            self.assertIn("PLAYER_BUILD_DRIFT", codes(s))
            r = self.stop(sid)
            self.assertEqual(r.status, tdg.SUCCESS, text(r))
            self.assertEqual(r.data["classification"], "GRACEFUL_STOP")
            self.assertEqual(r.evidence_candidates, ())
            self.assertIn("PLAYER_EVIDENCE_WITHHELD", codes(r))
        finally:
            target.write_bytes(original)

    def payload_file(self):
        return STATE["project"] / ".game/gpos-runtime/tool-output/unity" / STATE["build_id"][len("build-"):] / \
            "payload/Player.app/Contents/Resources/Data/boot.config"


RUNNER = r'''
import json, os, sys, time
sys.path.insert(0, {root!r})
from pathlib import Path
from gpos.framework import load_framework
from gpos.tools.execution import ExecutionRequest, execute
from gpos.tools.model import Actor, Subject
from gpos.tools.registry import ToolRegistry
from gpos.tools.player import PlayerAdapter, runtime as rt
host, project, build_id, crash_at = sys.argv[1:5]
original = rt.write_once
def crashing(path, obj, bound=rt.MAX_RECORD_BYTES):
    if Path(path).name == crash_at:
        print("READY", flush=True)
        time.sleep(600)          # the test kills this process here
    out = original(path, obj, bound)
    if Path(path).name == "commit.json" and crash_at == "after-commit":
        print("READY", flush=True)
        time.sleep(600)
    return out
rt.write_once = crashing
reg = ToolRegistry(load_framework()); reg.register(PlayerAdapter(host_location=lambda: host))
execute(reg, ExecutionRequest(adapter_id="player", capability_id="player.launch", subject=Subject("FEATURE", "FEATURE-0001"),
                              project_root=project, actor=Actor("AGENT", "player-real"), build_id=build_id,
                              allow_mutation=True))
'''


class RP6_CrashPoints(RealCase):
    def die_at(self, point):
        script = STATE["work"] / "runner.py"
        script.write_text(RUNNER.format(root=str(ROOT)))
        proc = subprocess.Popen([sys.executable, str(script), str(STATE["host"]), str(STATE["project"]),
                                 STATE["build_id"], point], stdout=subprocess.PIPE, text=True)
        self.assertEqual(proc.stdout.readline().strip(), "READY")
        proc.kill()
        proc.wait()
        lease = lease_mod.holder(STATE["project"], "player", f"PLAYER_RUNTIME:{STATE['project']}")
        self.assertIsNotNone(lease, "the session the dead command opened stays")
        return lease["session"]["session_id"], STATE["project"] / ".game/gpos-runtime" / lease["session"]["runtime_dir"]

    def test_the_command_dies_before_the_runtime_binding(self):
        sid, rdir = self.die_at("runtime-binding.json")
        self.assertTrue((rdir / "handshake.json").exists())
        self.assertFalse((rdir / "runtime-binding.json").exists())
        s = self.status(sid)
        self.assertEqual((s.data["phase"], s.data["identity"]), ("LAUNCHING_UNRESOLVED", "UNPROVEN"))
        # the supervisor abandons the uncommitted launch at its commit deadline and records the exit
        self.assertTrue(wait_until(lambda: (rdir / "exit.json").exists(), 90))
        exit_rec = json.loads((rdir / "exit.json").read_text())
        self.assertEqual((exit_rec["committed"], exit_rec["stop"]["reason"]), (False, "DEADLINE"))
        r = self.stop(sid)
        self.assertEqual(r.status, tdg.SUCCESS, text(r))
        self.assertTrue(r.data["closed"])

    def test_the_command_dies_after_the_commit(self):
        sid, rdir = self.die_at("after-commit")
        s = self.status(sid)
        self.assertEqual((s.data["phase"], s.data["identity"]), ("BOUND", "PROVEN"))
        r = self.stop(sid)
        self.assertEqual(r.data["classification"], "GRACEFUL_STOP", text(r))


class RP7_Fallback(RealCase):
    def test_the_supervisor_and_the_helper_install_are_gone(self):
        sid, launch = self.launch()
        self.ticks(launch)
        binding = json.loads((runtime_dir(launch) / "runtime-binding.json").read_text())
        s = binding["supervisor"]
        self.assertEqual(macos.identity(s["pid"], s["start_sec"], s["start_usec"], s["executable"]), c.PROVEN)
        os.kill(s["pid"], signal.SIGKILL)
        self.assertTrue(wait_until(lambda: macos.facts(s["pid"]) is None, 5))
        moved = STATE["work"] / "moved-helper"
        os.rename(STATE["host"], moved)
        try:
            r = self.stop(sid)
        finally:
            os.rename(moved, STATE["host"])
        self.assertEqual(r.status, tdg.SUCCESS, text(r))
        self.assertEqual((r.data["path"], r.data["classification"]), ("FALLBACK", "GONE_UNOBSERVED"))
        self.assertIsNone(r.provenance.command)
        self.assertIn("GPOSQ quitting", artifact_text(r, "runtime-log"), "the fallback quit was graceful")
        self.assertEqual(r.evidence_candidates, ())


class RP8_HelperRemovedAfterLaunch(RealCase):
    def test_status_reports_and_the_supervisor_still_stops(self):
        sid, launch = self.launch()
        moved = STATE["work"] / "moved-helper-8"
        os.rename(STATE["host"], moved)
        try:
            s = self.status(sid)
            self.assertEqual((s.data["helper"], s.data["capture_permission"], s.data["identity"]),
                             ("ABSENT", "UNKNOWN", "PROVEN"))
            r = self.stop(sid)
        finally:
            os.rename(moved, STATE["host"])
        self.assertEqual((r.data["path"], r.data["classification"]), ("SUPERVISOR", "GRACEFUL_STOP"), text(r))


    def test_the_install_is_deleted_after_launch(self):
        sid, launch = self.launch()
        shutil.rmtree(STATE["host"])
        try:
            s = self.status(sid)
            self.assertEqual((s.data["helper"], s.data["supervisor"], s.data["identity"]), ("ABSENT", "ALIVE", "PROVEN"))
            r = self.stop(sid)
        finally:
            hp.install(STATE["host"], f"reinstall-{uuid.uuid4().hex[:6]}")
        self.assertEqual((r.data["path"], r.data["classification"]), ("SUPERVISOR", "GRACEFUL_STOP"), text(r))


class RP9_PersistenceLog(RealCase):
    def test_save_quit_relaunch_restore(self):
        value = persistence_round(self)
        self.assertRegex(value, r"^[0-9a-f]{8}$")


def persistence_round(case):
    """launch -> graceful stop (saves next=v) -> relaunch -> the log restores v. Returns (v, the second session id,
    its launch result) with the second Player still running."""
    sid, launch = case.launch()
    case.ticks(launch)
    r = case.stop(sid)
    log = artifact_text(r, "runtime-log")
    nxt = next(l.split("next=")[1].split()[0] for l in log.splitlines() if "GPOSQ start" in l)
    case.assertIn(f"GPOSQ saved={nxt}", log)
    sid2, launch2 = case.launch()
    case.ticks(launch2)
    first = next(l for l in (runtime_dir(launch2) / "player.log").read_text(errors="replace").splitlines()
                 if "GPOSQ start" in l)
    case.assertIn(f"restored={nxt}", first)
    if not getattr(case, "keep_running", False):
        r2 = case.stop(sid2)
        case.assertIn(f"restored={nxt}", artifact_text(r2, "runtime-log"))
        return nxt
    return nxt, sid2, launch2


# ---------------------------------------------------------------- fixtures and pixels

def fixture(name):
    out = STATE["work"] / "fixtures" / f"{name}.app" / "Contents" / "MacOS" / name
    if not out.exists():
        out.parent.mkdir(parents=True)
        subprocess.run(["swiftc", "-O", "-o", str(out), str(TESTKIT / "fixtures" / f"{name}.swift")],
                       check=True, capture_output=True)
        plist = (TESTKIT / "fixtures" / "Info.plist.template").read_text().replace("@NAME@", name)
        (out.parent.parent / "Info.plist").write_text(plist)
        subprocess.run(["codesign", "-s", "-", "--force", str(out.parent.parent.parent)], check=True, capture_output=True)
    return out


def decode_png(path):
    """(width, height, channels, rows) of an 8-bit RGB/RGBA PNG — a test-side decoder."""
    data = Path(path).read_bytes()
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    w, h, _, colour = ihdr[:4]
    ch = 4 if colour == 6 else 3
    raw = zlib.decompress(idat)
    stride, rows, prev = w * ch, [], bytearray(w * ch)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - ch] if x >= ch else 0
            b, cc = prev[x], prev[x - ch] if x >= ch else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - cc
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - cc)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else cc)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, ch, rows


def pixel(img, x, y):
    w, h, ch, rows = img
    return tuple(rows[y][x * ch:x * ch + 3])


def green_share(img):
    w, h, ch, rows = img
    green = total = 0
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            r, g, b = pixel(img, x, y)
            total += 1
            green += g > 200 and r < 160 and b < 140
    return green / total


def decode_stripe(img):
    """The 32-bit value QualGame draws: 32 black/white cells inside a red frame."""
    w, h, ch, rows = img
    reds = [(x, y) for y in range(0, h, 2) for x in range(0, w, 2)
            if (lambda p: p[0] > 170 and p[1] < 90 and p[2] < 90)(pixel(img, x, y))]
    if not reds:
        return None
    x0, x1 = min(p[0] for p in reds), max(p[0] for p in reds)
    y0, y1 = min(p[1] for p in reds), max(p[1] for p in reds)
    inset_x, inset_y = (x1 - x0) * 4 / (32 * 13 + 8), (y1 - y0) * 4 / 48
    cell = (x1 - x0 - 2 * inset_x) / 32
    y = int((y0 + y1) / 2)
    bits = ""
    for i in range(32):
        x = int(x0 + inset_x + cell * (i + 0.5))
        r, g, b = pixel(img, x, y)
        bits += "1" if (r + g + b) > 380 else "0"
    return f"{int(bits, 2):08x}"


# ---------------------------------------------------------------- RC (after the Human grant)

@unittest.skipUnless(CAPTURE, "NOT_RUN without GPOS_REAL_CAPTURE=1 (needs the Human-granted helper)")
class CaptureCase(RealCase):
    production = True

    @classmethod
    def setUpClass(cls):
        installed = hp.install_path(os.path.realpath(Path.home() / "Applications" / "GPOS"))
        if hp.classify(installed)[0] != hp.EXACT:
            raise unittest.SkipTest("NOT_RUN: the release helper is not installed EXACT at ~/Applications/GPOS")

    def shot(self, sid, expect=tdg.SUCCESS):
        r = run(c.SCREENSHOT, production=True, session_id=sid, build_id=STATE["build_id"])
        self.assertEqual(r.status, expect, text(r))
        if expect == tdg.SUCCESS:
            self.assertEqual(r.provenance.command["executable"], "/usr/bin/open")
            self.assertEqual(r.provenance.command["argv"][-2], "shot")
        return r

    def video(self, sid, seconds, expect=tdg.SUCCESS):
        r = run(c.VIDEO, production=True, session_id=sid, build_id=STATE["build_id"],
                inputs={"duration_seconds": seconds}, timeout=120)
        self.assertEqual(r.status, expect, text(r))
        return r

    def media(self, r, artifact_id):
        return STATE["project"] / next(a.path for a in r.artifacts if a.artifact_id == artifact_id)


class RC1_Screenshots(CaptureCase):
    def test_windowed_and_large(self):
        sid, launch = self.launch()
        self.ticks(launch)
        r = self.shot(sid)
        img = decode_png(self.media(r, "runtime-screenshot"))
        self.assertLessEqual(max(img[0], img[1]), 1920)
        (cand,) = r.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context), ("VISUAL_EVIDENCE", "DIAGNOSTIC_RUNTIME"))
        self.stop(sid)
        mode("large")
        sid, launch = self.launch()
        self.ticks(launch)
        r = self.shot(sid)
        w, h = r.data["media"]["width"], r.data["media"]["height"]
        self.assertEqual(max(w, h), 1920, "a large window is scaled down to exactly 1920 px")
        pts = r.data["window"]
        self.assertAlmostEqual(w / h, pts["points_w"] / pts["points_h"], places=2)
        self.stop(sid)


class RC2_Videos(CaptureCase):
    def test_one_and_fifteen_seconds(self):
        sid, launch = self.launch()
        self.ticks(launch)
        for seconds in (1, 15):
            r = self.video(sid, seconds)
            self.assertAlmostEqual(r.data["media"]["duration_s"], seconds, delta=0.75)
            self.assertGreaterEqual(r.data["media"]["samples"], seconds)
            (cand,) = r.evidence_candidates
            self.assertEqual((cand.evidence_type, cand.capture_context), ("MOTION_EVIDENCE", "DIAGNOSTIC_RUNTIME"))
        for seconds in (0, 16):
            self.video(sid, seconds, expect=tdg.INVALID_REQUEST)
        self.stop(sid)


class RC3_InterruptedVideos(CaptureCase):
    def test_the_player_exits_mid_video(self):
        sid, launch = self.launch()
        self.ticks(launch)
        binding = json.loads((runtime_dir(launch) / "runtime-binding.json").read_text())
        p = binding["player"]
        import threading
        threading.Timer(9.0, lambda: macos.kill_proven(p["pid"], p["start_sec"], p["start_usec"], p["executable"])).start()
        r = self.video(sid, 15, expect=tdg.FAILED)
        self.assertTrue(codes(r) & {"CAPTURE_TARGET_EXITED", "CAPTURE_FAILED"})
        self.assertEqual((r.artifacts, r.evidence_candidates), ((), ()))
        self.stop(sid)

    def test_the_helper_is_killed_mid_video(self):
        sid, launch = self.launch()
        self.ticks(launch)
        installed = str(hp.executable(hp.install_path(os.path.realpath(Path.home() / "Applications" / "GPOS"))))

        def kill_helper():
            for pid in macos.all_pids():
                f = macos.facts(pid)
                if f and f["executable"] == installed and f["ppid"] == 1:   # the LaunchServices-started capture
                    os.kill(pid, signal.SIGKILL)
        import threading
        threading.Timer(9.0, kill_helper).start()
        r = self.video(sid, 15, expect=tdg.FAILED)
        self.assertIn("CAPTURE_FAILED", codes(r))
        self.assertEqual((r.artifacts, r.evidence_candidates), ((), ()))
        self.stop(sid)


class RC4_Hidden(CaptureCase):
    def test_a_hidden_window_is_refused(self):
        sid, launch = self.launch()
        self.ticks(launch)
        binding = json.loads((runtime_dir(launch) / "runtime-binding.json").read_text())
        vis = fixture("Visibility")
        subprocess.run([str(vis), str(binding["player"]["pid"]), "hide"], check=True, capture_output=True)
        time.sleep(1)
        r = self.shot(sid, expect=tdg.FAILED)
        self.assertIn("CAPTURE_WINDOW_NOT_FOUND", codes(r))
        subprocess.run([str(vis), str(binding["player"]["pid"]), "unhide"], capture_output=True)
        self.stop(sid)


class RC5_Privacy(CaptureCase):
    def test_an_unrelated_covering_window_never_leaks(self):
        # negative control 1: the detector finds green where it exists
        import player_fakes as pf
        synthetic = STATE["work"] / "green.png"
        synthetic.write_bytes(pf.png_bytes(64, 64, colour=(107, 247, 74, 255)))
        self.assertGreater(green_share(decode_png(synthetic)), 0.95)
        sid, launch = self.launch()
        self.ticks(launch)
        binding = json.loads((runtime_dir(launch) / "runtime-binding.json").read_text())
        occ = subprocess.Popen([str(fixture("Occluder")), str(binding["player"]["pid"]), "60"], stdout=subprocess.PIPE,
                               text=True)
        self.addCleanup(occ.kill)
        covered = json.loads(occ.stdout.readline())
        self.assertTrue(covered["covered"])
        time.sleep(1)
        # negative control 2: the occluder really is on top of the Player (front-to-back window order)
        order = [w for w in macos.windows(covered["pid"]) if w["on_screen"]]
        self.assertTrue(order, "the occluder window is on screen")
        r = self.shot(sid)
        self.assertLess(green_share(decode_png(self.media(r, "runtime-screenshot"))), 0.001)
        v = self.video(sid, 5)
        video = InputArtifact("runtime-video", str(self.media(v, "runtime-video")), capture_context="DIAGNOSTIC_RUNTIME")
        for t in (0.5, 2.5, 4.5):
            frame = execute(default_registry(FW), ExecutionRequest(
                adapter_id="ffmpeg", capability_id="ffmpeg.extract-frame", subject=Subject("FEATURE", "FEATURE-0001", REV),
                project_root=str(STATE["project"]), input_artifacts=(video,), inputs={"timestamp_seconds": t},
                allow_mutation=True))
            self.assertEqual(frame.status, tdg.SUCCESS, text(frame))
            path = STATE["project"] / frame.artifacts[0].path
            self.assertLess(green_share(decode_png(path)), 0.001, f"frame at {t}s")
        occ.kill()
        self.stop(sid)


class RC6_PersistenceVisual(CaptureCase):
    keep_running = True

    def test_the_restored_value_is_visible(self):
        value, sid, launch = persistence_round(self)
        self.ready(launch)
        r = self.shot(sid)
        img = decode_png(self.media(r, "runtime-screenshot"))
        decoded = decode_stripe(img)
        if decoded != value:   # keep the evidence of a mismatch outside the temporary tree, and describe it
            keep = Path(tempfile.gettempdir()) / f"gpos-rc6-{uuid.uuid4().hex[:8]}.png"
            shutil.copy(self.media(r, "runtime-screenshot"), keep)
            w, h, _, _ = img
            samples = {f"{x},{y}": pixel(img, x, y) for x, y in ((w // 4, h // 4), (w // 2, h // 2), (40, h // 5),
                                                                  (w // 3, h // 4))}
            self.fail(f"stripe {decoded!r} != {value!r}; {w}x{h}; kept {keep}; samples {samples}")
        final = self.stop(sid)
        self.assertIn(f"restored={value}", artifact_text(final, "runtime-log"))


class RC7_Composition(CaptureCase):
    def test_capture_video_then_ffprobe_then_ffmpeg(self):
        sid, launch = self.launch()
        self.ticks(launch)
        v = self.video(sid, 3)
        self.stop(sid)
        src = InputArtifact("runtime-video", str(self.media(v, "runtime-video")), capture_context="DIAGNOSTIC_RUNTIME")
        req = lambda adapter, cap, **kw: execute(default_registry(FW), ExecutionRequest(
            adapter_id=adapter, capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", REV),
            project_root=str(STATE["project"]), input_artifacts=(src,), **kw))
        probe = req("ffprobe", "ffprobe.inspect")
        self.assertEqual(probe.status, tdg.SUCCESS, text(probe))
        self.assertEqual((probe.data["video_stream_count"], probe.data["audio_stream_count"]), (1, 0))
        self.assertAlmostEqual(float(probe.data["duration_seconds"]), 3.0, delta=0.75)
        for cap, inputs, etype in (("ffmpeg.extract-frame", {"timestamp_seconds": 1.0}, "VISUAL_EVIDENCE"),
                                   ("ffmpeg.extract-clip", {"start_seconds": 0.5, "duration_seconds": 1.5},
                                    "MOTION_EVIDENCE")):
            r = req("ffmpeg", cap, inputs=inputs, allow_mutation=True)
            self.assertEqual(r.status, tdg.SUCCESS, text(r))
            (cand,) = r.evidence_candidates
            self.assertEqual((cand.evidence_type, cand.capture_context), (etype, "DIAGNOSTIC_RUNTIME"))
            self.assertEqual(cand.derived_from, ("runtime-video",))
            self.assertEqual(r.artifacts[0].origin_capture_context, "DIAGNOSTIC_RUNTIME")
            # hoping for TARGET_RUNTIME (expected_evidence is a hope, never a promise) changes nothing: processing a
            # diagnostic capture never upgrades it
            hoped = req("ffmpeg", cap, inputs=inputs, allow_mutation=True, expected_evidence=((etype, "TARGET_RUNTIME"),))
            self.assertEqual([c_.capture_context for c_ in hoped.evidence_candidates], ["DIAGNOSTIC_RUNTIME"])
            self.assertEqual(hoped.artifacts[0].origin_capture_context, "DIAGNOSTIC_RUNTIME")


@unittest.skipUnless(DENIED, "NOT_RUN without GPOS_PLAYER_CAPTURE_DENIED=1 (only before the Human grant)")
class RC8_PermissionDenied(RealCase):
    production = True

    def test_shot_and_video_refuse_without_screencapturekit(self):
        installed = hp.install_path(os.path.realpath(Path.home() / "Applications" / "GPOS"))
        self.assertEqual(hp.classify(installed)[0], hp.EXACT)
        sid, launch = self.launch()
        self.ticks(launch)
        for cap, inputs in ((c.SCREENSHOT, {}), (c.VIDEO, {"duration_seconds": 2})):
            r = run(cap, production=True, session_id=sid, build_id=STATE["build_id"], inputs=inputs)
            self.assertEqual(r.status, tdg.UNAVAILABLE, text(r))
            self.assertIn("CAPTURE_PERMISSION_REQUIRED", codes(r))
            self.assertEqual((r.artifacts, r.evidence_candidates), ((), ()))
            self.assertEqual(r.provenance.command["executable"], "/usr/bin/open")
            ws = STATE["project"] / ".game/gpos-runtime/tool-output/player" / r.request_id
            result = json.loads((ws / "helper-result.json").read_text())
            self.assertEqual((result["outcome"], result["rule"], result["sck_called"]),
                             ("REFUSED", "PERMISSION_NOT_GRANTED", False))
            self.assertFalse(any(n.startswith("runtime-") for n in os.listdir(ws)))
            print(f"\nRC8 {cap}: {result['rule']} sck_called={result['sck_called']} helper={result['helper']['cdhash']}")
        self.stop(sid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
