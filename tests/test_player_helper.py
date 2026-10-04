#!/usr/bin/env python3
"""Phase 2C-8 (alpha.22) — the GPOS Player Helper itself (real native code, no Unity, no Screen Recording).

    python3 tests/test_player_helper.py

    RH0  the release: the committed bundle is exactly what the committed source builds to (with the recorded
         toolchain; another toolchain is reported NOT_RUN, never PASS)
    RH1  supervise against a compiled stub Player: the exact argv, environment, working directory and process group;
         commit + stop intent, abort, the commit deadline, exit codes, the kill of its own child after the grace; the
         handshake and exit record shapes the adapter reads; the kernel CDHash it reports is the release's
    RH2  supervise refusals: every malformed or misplaced request starts nothing
    RH3  the mode table: only supervise, shot and video exist; shot/video refuse a bad request and, run outside
         LaunchServices, never publish anything
    RH4  the atomic no-replace install on the real filesystem (renamex_np RENAME_EXCL), including a 16-way race
    RH5  the in-process macOS backend against real processes: the bundle-identifier scan (copies anywhere, never by
         name), kernel identity, the proven kill, window facts, and the AppKit request that sends nothing unless the
         bundle identifier and the executable both match
The helper used is a fresh copy of the release in a temporary location; the Human-granted install at
~/Applications/GPOS is never touched here.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_player_helper as builder  # noqa: E402
from gpos.tools.player import appkit  # noqa: E402
from gpos.tools.player import contract as c  # noqa: E402
from gpos.tools.player import helper as hp  # noqa: E402
from gpos.tools.player import macos  # noqa: E402
from gpos.tools.player import runtime as rt  # noqa: E402

STUB = r'''
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
extern char **environ;
int main(int argc, char **argv) {
    char dir[4096] = "", path[4200], mode[64] = "run";
    for (int i = 1; i + 1 < argc; i++) if (!strcmp(argv[i], "-logFile")) { strncpy(dir, argv[i + 1], 4000); }
    char *slash = strrchr(dir, '/'); if (slash) *slash = 0;
    if (!dir[0]) { for (;;) sleep(1); }          /* started without -logFile: just run */
    snprintf(path, sizeof path, "%s/stub-facts.txt", dir);
    FILE *f = fopen(path, "w");
    if (!f) return 70;
    for (int i = 0; i < argc; i++) fprintf(f, "argv=%s\n", argv[i]);
    for (char **e = environ; *e; e++) fprintf(f, "env=%s\n", *e);
    char cwd[4096]; getcwd(cwd, sizeof cwd); fprintf(f, "cwd=%s\npid=%d\npgid=%d\nppid=%d\n", cwd, getpid(), getpgrp(), getppid());
    fclose(f);
    snprintf(path, sizeof path, "%s/../stub-mode.txt", dir);
    FILE *m = fopen(path, "r"); if (m) { fscanf(m, "%63s", mode); fclose(m); }
    if (!strncmp(mode, "exit:", 5)) { sleep(1); return atoi(mode + 5); }
    for (;;) sleep(1);
}
'''


def compile_stub(directory):
    src = directory / "stub.c"
    src.write_text(STUB)
    out = directory / "stub"
    subprocess.run(["cc", "-O2", "-o", str(out), str(src)], check=True, capture_output=True)
    return out


def stop_processes_under(base):
    """TEST HYGIENE: never leave a supervisor or stub Player of this test's own temporary tree running (matched by the
    exact executable path below `base`; a failing or mutated helper may not have ended them)."""
    base = str(Path(base).resolve())
    for pid in macos.all_pids():
        f = macos.facts(pid)
        if f and f["executable"] and f["executable"].startswith(base + "/"):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


class HelperCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="gpos-helper-")).resolve()
        cls.host = cls.base / "Applications" / "GPOS"
        hp.install(cls.host, "helper-tests")
        cls.exe = str(hp.executable(cls.host / hp.BUNDLE_NAME))
        cls.stub = compile_stub(cls.base)

    @classmethod
    def tearDownClass(cls):
        stop_processes_under(cls.base)
        shutil.rmtree(cls.base, ignore_errors=True)

    def project(self):
        root = Path(tempfile.mkdtemp(prefix="p-", dir=self.base)).resolve()
        self.addCleanup(lambda: None)
        app = root / ".game/gpos-runtime/tool-output/unity/req-stub/payload/Player.app"
        (app / "Contents/MacOS").mkdir(parents=True)
        import plistlib
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.gpos.test.stub",
                                                                  "CFBundleExecutable": "Stub"}))
        shutil.copy(self.stub, app / "Contents/MacOS/Stub")
        os.chmod(app / "Contents/MacOS/Stub", 0o755)
        return root, str(app / "Contents/MacOS/Stub")

    def request(self, root, exe, rid=None, **over):
        rid = rid or f"req-{uuid.uuid4().hex[:12]}"
        directory = root / ".game/gpos-runtime/tool-output/player" / rid
        directory.mkdir(parents=True)
        st = os.stat(exe)
        body = {"schema": "gpos.player.supervisor-request/1", "session_id": uuid.uuid4().hex, "nonce": uuid.uuid4().hex,
                "launch_request_id": rid, "application_id": "com.gpos.test.stub", "executable": exe,
                "executable_dev": st.st_dev, "executable_ino": st.st_ino, "commit_deadline_s": 30, "stop_grace_s": 1}
        body.update(over)
        (directory / "supervisor-request.json").write_text(json.dumps(body))
        return directory, body

    def supervise(self, directory, name="supervisor-request.json", wait=True):
        env = {k: os.environ[k] for k in ("HOME", "TMPDIR", "LANG") if k in os.environ}
        env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", GPOS_SHOULD_NOT_LEAK="1")
        p = subprocess.Popen([self.exe, "supervise", str(directory / name)], cwd=str(directory), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        if wait:
            deadline = time.monotonic() + 10
            while not (directory / "handshake.json").exists() and p.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
        return p

    def control(self, directory, body, name, schema, **extra):
        rt.write_once(directory / name, {"schema": schema, "session_id": body["session_id"], "nonce": body["nonce"],
                                         **extra})

    def exit_record(self, directory, body, process, timeout=40):
        process.wait(timeout)
        return rt.exit_record(directory / "exit.json", body["session_id"], body["nonce"])


# ---------------------------------------------------------------- RH0  the release

class RH0_Release(unittest.TestCase):
    def test_the_release_is_what_the_source_builds_to(self):
        status, detail = builder.check_release()
        if status == "TOOLCHAIN_MISMATCH":
            self.skipTest(f"NOT_RUN: the recorded toolchain is not this machine's ({detail})")
        self.assertEqual(status, "EXACT", detail)

    def test_the_bundle_identity(self):
        bundle = hp.RELEASE_DIR / hp.BUNDLE_NAME
        out = subprocess.run(["codesign", "-dvvv", str(bundle)], capture_output=True, text=True).stderr
        self.assertIn("Identifier=com.gpos.player-helper", out)
        self.assertIn("Signature=adhoc", out)
        verify = subprocess.run(["codesign", "--verify", "--strict", str(bundle)], capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stderr)
        archs = subprocess.run(["lipo", "-archs", str(bundle / "Contents/MacOS/GposPlayerHelper")], capture_output=True,
                               text=True).stdout.split()
        self.assertEqual(sorted(archs), ["arm64", "x86_64"])


# ---------------------------------------------------------------- RH1  supervise

class RH1_Supervise(HelperCase):
    def facts(self, directory):
        lines = (directory / "stub-facts.txt").read_text().splitlines()
        out = {"argv": [], "env": []}
        for line in lines:
            key, value = line.split("=", 1)
            if key in ("argv", "env"):
                out[key].append(value)
            else:
                out[key] = value
        return out

    def test_the_launch_the_handshake_and_a_forced_stop(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        p = self.supervise(directory)
        hs = rt.handshake(directory / "handshake.json", body["session_id"], body["nonce"])
        s, pl = hs["supervisor"], hs["player"]
        self.assertEqual((s["pid"], s["executable"], s["version"]), (p.pid, self.exe, "1.0.0"))
        self.assertIn(s["cdhash"], hp.cdhashes().values())
        self.assertEqual(macos.cdhash(p.pid), s["cdhash"])
        self.assertEqual((pl["ppid"], pl["pgid"], pl["executable"]), (p.pid, pl["pid"], exe))
        self.assertEqual(macos.identity(pl["pid"], pl["start_sec"], pl["start_usec"], exe), c.PROVEN)
        time.sleep(0.5)
        f = self.facts(directory)
        self.assertEqual(f["argv"], [exe, "-logFile", str(directory / "player.log")])
        self.assertEqual(sorted(e.split("=", 1)[0] for e in f["env"]),
                         sorted(k for k in ("HOME", "TMPDIR", "LANG", "PATH") if k == "PATH" or k in os.environ))
        self.assertIn("env=PATH=/usr/bin:/bin:/usr/sbin:/sbin", [f"env={e}" for e in f["env"]])
        self.assertEqual((f["cwd"], f["pgid"], f["ppid"]), (str(directory), f["pid"], str(p.pid)))
        self.control(directory, body, "commit.json", "gpos.player.commit/1")
        time.sleep(0.3)
        self.control(directory, body, "stop-intent-req-stop.json", "gpos.player.stop-intent/1", stop_request_id="req-stop")
        rec = self.exit_record(directory, body, p)
        self.assertEqual((rec["status"], rec["signal"], rec["committed"]), ("SIGNALED", 9, True))
        self.assertEqual((rec["stop"]["reason"], rec["stop"]["stop_request_id"], rec["stop"]["kill_sent"],
                          rec["stop"]["terminate_accepted"]), ("STOP_INTENT", "req-stop", True, False))
        self.assertEqual(macos.facts(pl["pid"]), None)

    def test_an_exit_code_is_observed_by_the_parent(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        (directory.parent / "stub-mode.txt").write_text("exit:7")
        p = self.supervise(directory)
        self.control(directory, body, "commit.json", "gpos.player.commit/1")
        rec = self.exit_record(directory, body, p)
        self.assertEqual((rec["status"], rec["code"], rec["stop"]["reason"], rec["stop"]["kill_sent"]),
                         ("EXITED", 7, None, False))
        (directory.parent / "stub-mode.txt").unlink()

    def test_abort_before_commit(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        p = self.supervise(directory)
        self.control(directory, body, "abort.json", "gpos.player.abort/1", reason="HANDSHAKE_INVALID")
        rec = self.exit_record(directory, body, p)
        self.assertEqual((rec["stop"]["reason"], rec["committed"], rec["stop"]["kill_sent"]), ("ABORT", False, True))

    def test_an_abort_before_the_start_starts_nothing(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        self.control(directory, body, "abort.json", "gpos.player.abort/1", reason="LAUNCH_FAILED")
        p = self.supervise(directory, wait=False)
        rec = self.exit_record(directory, body, p)
        self.assertEqual((rec["status"], rec["player"], rec["stop"]["reason"]), ("NOT_STARTED", None, "ABORT"))
        self.assertFalse((directory / "handshake.json").exists())
        self.assertFalse((directory / "stub-facts.txt").exists())

    def test_a_stop_intent_before_commit_is_ignored_and_a_foreign_intent_never_acts(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        p = self.supervise(directory)
        rt.write_once(directory / "stop-intent-req-x.json", {"schema": "gpos.player.stop-intent/1",
                                                            "session_id": body["session_id"], "nonce": "0" * 32,
                                                            "stop_request_id": "req-x"})
        self.control(directory, body, "commit.json", "gpos.player.commit/1")
        time.sleep(1.5)
        self.assertIsNone(p.poll(), "a stop intent with another nonce must not stop the Player")
        self.control(directory, body, "stop-intent-req-y.json", "gpos.player.stop-intent/1", stop_request_id="req-y")
        rec = self.exit_record(directory, body, p)
        self.assertEqual(rec["stop"]["stop_request_id"], "req-y")

    def test_the_commit_deadline_abandons(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        started = time.monotonic()
        p = self.supervise(directory)
        rec = self.exit_record(directory, body, p, timeout=60)
        self.assertGreaterEqual(time.monotonic() - started, 30)
        self.assertEqual((rec["stop"]["reason"], rec["committed"]), ("DEADLINE", False))

    def test_the_supervisor_dies_and_the_player_survives_provably(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        p = self.supervise(directory)
        hs = rt.handshake(directory / "handshake.json", body["session_id"], body["nonce"])
        pl = hs["player"]
        os.kill(p.pid, 9)
        p.wait(5)
        time.sleep(0.3)
        self.assertEqual(macos.identity(pl["pid"], pl["start_sec"], pl["start_usec"], exe), c.PROVEN)
        self.assertEqual(macos.facts(pl["pid"])["ppid"], 1)
        self.assertFalse((directory / "exit.json").exists())
        self.assertTrue(macos.kill_proven(pl["pid"], pl["start_sec"], pl["start_usec"], exe))
        time.sleep(0.3)
        self.assertEqual(macos.identity(pl["pid"], pl["start_sec"], pl["start_usec"], exe), c.GONE)
        self.assertFalse(macos.kill_proven(pl["pid"], pl["start_sec"], pl["start_usec"], exe))


# ---------------------------------------------------------------- RH2  refusals

class RH2_Refusals(HelperCase):
    def refused(self, directory, name="supervisor-request.json", code=64, planted=None):
        p = self.supervise(directory, name, wait=False)
        self.assertEqual(p.wait(15), code)
        if planted is None:
            self.assertFalse((directory / "handshake.json").exists())
        else:
            self.assertEqual((directory / "handshake.json").read_text(), planted)   # never adopted or replaced
        self.assertFalse((directory / "exit.json").exists())
        self.assertFalse((directory / "stub-facts.txt").exists())

    def test_closed_request(self):
        root, exe = self.project()
        cases = {"extra key": {"extra": 1}, "bool int": {"stop_grace_s": True}, "float": {"commit_deadline_s": 30.5},
                 "deadline range": {"commit_deadline_s": 5}, "grace range": {"stop_grace_s": 99},
                 "schema": {"schema": "x"}, "session": {"session_id": "XYZ"}, "dev": {"executable_dev": 1},
                 "ino": {"executable_ino": 1}, "application": {"application_id": "com.gpos.other"},
                 "relative exe": {"executable": "Stub"}, "outside payload": {"executable": "/bin/sleep"}}
        for name, over in cases.items():
            with self.subTest(case=name):
                directory, body = self.request(root, exe, **over)
                self.refused(directory)
        directory, body = self.request(root, exe)
        del body["nonce"]
        (directory / "supervisor-request.json").write_text(json.dumps(body))
        self.refused(directory)
        directory, body = self.request(root, exe)
        (directory / "supervisor-request.json").write_text(json.dumps(body) + " " * 9000)
        self.refused(directory)

    def test_misplaced_request(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        (directory / "other.json").write_text((directory / "supervisor-request.json").read_text())
        self.refused(directory, "other.json")
        directory, body = self.request(root, exe, launch_request_id="req-elsewhere")
        self.refused(directory)
        elsewhere = root / "elsewhere" / "req-x"
        elsewhere.mkdir(parents=True)
        st = os.stat(exe)
        (elsewhere / "supervisor-request.json").write_text(json.dumps({
            "schema": "gpos.player.supervisor-request/1", "session_id": "a" * 32, "nonce": "b" * 32,
            "launch_request_id": "req-x", "application_id": "com.gpos.test.stub", "executable": exe,
            "executable_dev": st.st_dev, "executable_ino": st.st_ino, "commit_deadline_s": 30, "stop_grace_s": 1}))
        self.refused(elsewhere)
        # a linked runtime chain: an otherwise valid request reached through a link
        directory, body = self.request(root, exe)
        real = root / "real-player"
        os.rename(root / ".game/gpos-runtime/tool-output/player", real)
        os.symlink(real, root / ".game/gpos-runtime/tool-output/player")
        self.refused(directory)

    def test_a_reused_runtime_directory(self):
        root, exe = self.project()
        directory, body = self.request(root, exe)
        (directory / "handshake.json").write_text("{}")
        self.refused(directory, code=65, planted="{}")

    def test_a_linked_executable(self):
        root, exe = self.project()
        link = Path(exe).with_name("Linked")
        os.symlink(exe, link)
        directory, body = self.request(root, str(link))
        self.refused(directory)


# ---------------------------------------------------------------- RH3  the mode table

class RH3_Modes(HelperCase):
    def test_only_three_modes(self):
        for mode in ("preflight", "running", "windows", "terminate", "request", "SHOT", ""):
            with self.subTest(mode=mode):
                p = subprocess.run([self.exe, mode, "/tmp/x.json"], capture_output=True, timeout=10)
                self.assertEqual(p.returncode, 64)
        for argv in ([], ["supervise"], ["shot", "a", "b"]):
            self.assertEqual(subprocess.run([self.exe, *argv], capture_output=True, timeout=10).returncode, 64)

    def capture_request(self, root, kind="SHOT", **over):
        mode = kind
        rid = f"req-{uuid.uuid4().hex[:12]}"
        ws = root / ".game/gpos-runtime/tool-output/player" / rid
        ws.mkdir(parents=True)
        body = {"schema": "gpos.player.helper-request/1", "mode": mode, "request_id": rid,
                "request_nonce": uuid.uuid4().hex, "session_id": uuid.uuid4().hex,
                "target": {"pid": 1, "start_sec": 1, "start_usec": 0, "executable": "/sbin/launchd",
                           "application_id": "com.gpos.test.stub"},
                "settle_s": 5, "max_edge_px": 1920, "duration_s": 2 if mode == "VIDEO" else None,
                "fps": 30 if mode == "VIDEO" else None, "max_bytes": 1 << 20, "deadline_s": 30}
        body.update(over)
        (ws / "helper-request.json").write_text(json.dumps(body))
        return ws, body

    def test_bad_capture_requests_write_nothing(self):
        root, _ = self.project()
        for mode, over in (("shot", {"max_edge_px": 4000}), ("shot", {"settle_s": 1}), ("video", {"duration_s": 16}),
                           ("video", {"duration_s": 0}), ("shot", {"duration_s": 3}), ("video", {"fps": 60}),
                           ("shot", {"mode": "VIDEO"}), ("shot", {"extra": True})):
            with self.subTest(mode=mode, over=over):
                ws, _ = self.capture_request(root, "VIDEO" if mode == "video" else "SHOT", **over)  # noqa
                p = subprocess.run([self.exe, mode, str(ws / "helper-request.json")], capture_output=True, timeout=20)
                self.assertEqual(p.returncode, 64)
                self.assertEqual(sorted(os.listdir(ws)), ["helper-request.json"])

    def test_outside_launchservices_nothing_is_captured(self):
        """Run directly (not through LaunchServices), the helper's preflight answers for this test's own responsible
        application, and the target (launchd) is not the proven process either: either way the helper refuses before
        ScreenCaptureKit, and publishes nothing."""
        root, _ = self.project()
        for mode in ("shot", "video"):
            ws, body = self.capture_request(root, mode.upper())
            p = subprocess.run([self.exe, mode, str(ws / "helper-request.json")], capture_output=True, timeout=40)
            self.assertEqual(p.returncode, 0)
            result = json.loads((ws / "helper-result.json").read_text())
            self.assertEqual(result["outcome"], "REFUSED")
            self.assertIn(result["rule"], ("PERMISSION_NOT_GRANTED", "TARGET_NOT_PROVEN"))
            self.assertIs(result["sck_called"], False)
            self.assertEqual((result["request_nonce"], result["session_id"]), (body["request_nonce"], body["session_id"]))
            self.assertEqual(result["helper"]["executable"], self.exe)
            self.assertIn(result["helper"]["cdhash"], hp.cdhashes().values())
            self.assertEqual(sorted(os.listdir(ws)), ["helper-request.json", "helper-result.json"])


# ---------------------------------------------------------------- RH4  the real filesystem install

class RH4_Install(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gpos-install-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_sixteen_concurrent_installs_publish_exactly_one(self):
        host = self.base / "Applications" / "GPOS"
        results, errors = [], []
        barrier = threading.Barrier(16)

        def go(i):
            barrier.wait()
            try:
                results.append(hp.install(host, f"race-{i}"))
            except Exception as exc:   # every outcome is recorded
                errors.append(exc)
        threads = [threading.Thread(target=go, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sum(1 for state, created in results if created), 1)
        self.assertTrue(all(state == "EXACT" for state, _ in results))
        self.assertEqual(hp.classify(host / hp.BUNDLE_NAME)[0], "EXACT")
        self.assertEqual(hp.staging_leftovers(host), [])

    def test_an_installed_helper_runs_and_reports_the_release_cdhash(self):
        host = self.base / "Applications" / "GPOS"
        hp.install(host, "run")
        exe = hp.executable(host / hp.BUNDLE_NAME)
        st = os.stat(exe)
        self.assertTrue(stat.S_IMODE(st.st_mode) & 0o111)
        p = subprocess.run([str(exe), "nothing", "x"], capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 64)


# ---------------------------------------------------------------- RH5  the in-process macOS backend

TESTKIT = ROOT / "tests" / "player_testkit" / "fixtures"


def occluder_app(base):
    app = base / "GposTestOccluder.app"
    if not app.exists():
        exe = app / "Contents/MacOS/Occluder"
        exe.parent.mkdir(parents=True)
        subprocess.run(["swiftc", "-O", "-o", str(exe), str(TESTKIT / "Occluder.swift")], check=True, capture_output=True)
        (app / "Contents/Info.plist").write_text((TESTKIT / "Info.plist.template").read_text().replace("@NAME@", "Occluder"))
        subprocess.run(["codesign", "-s", "-", "--force", str(app)], check=True, capture_output=True)
    return app


def wait(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = predicate()
        if v:
            return v
        time.sleep(0.1)
    return None


class RH5_Backend(HelperCase):
    def run_exe(self, exe):
        p = subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def test_the_same_application_scan(self):
        root, exe = self.project()
        app = Path(exe).parent.parent.parent
        copy = self.base / "elsewhere dir" / "Renamed Copy.app"
        shutil.copytree(app, copy)
        other = self.base / "Other.app"
        shutil.copytree(app, other)
        import plistlib
        (other / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.gpos.test.other",
                                                                    "CFBundleExecutable": "Stub"}))
        a, b, u = self.run_exe(exe), self.run_exe(str(copy / "Contents/MacOS/Stub")), self.run_exe(str(other / "Contents/MacOS/Stub"))
        found = wait(lambda: len(macos.same_application("com.gpos.test.stub")) == 2 and macos.same_application("com.gpos.test.stub"))
        self.assertEqual(sorted(f["pid"] for f in found), sorted([a.pid, b.pid]))
        self.assertEqual([f["pid"] for f in macos.same_application("com.gpos.test.other")], [u.pid])
        self.assertEqual(macos.same_application("Stub"), [], "never matched by a process or executable name")

    def test_identity_and_the_proven_kill(self):
        root, exe = self.project()
        p = self.run_exe(exe)
        f = wait(lambda: macos.facts(p.pid))
        self.assertEqual(macos.identity(p.pid, f["start_sec"], f["start_usec"], exe), c.PROVEN)
        self.assertEqual(macos.identity(p.pid, f["start_sec"] - 1, f["start_usec"], exe), c.NOT_THIS_PROCESS)
        self.assertEqual(macos.identity(p.pid, f["start_sec"], f["start_usec"] + 1, exe), c.NOT_THIS_PROCESS)
        self.assertEqual(macos.identity(p.pid, f["start_sec"], f["start_usec"], exe + "x"), c.UNPROVEN)
        self.assertFalse(macos.kill_proven(p.pid, f["start_sec"] - 1, f["start_usec"], exe))
        self.assertFalse(macos.kill_proven(p.pid, f["start_sec"], f["start_usec"], "/bin/sleep"))
        time.sleep(0.3)
        self.assertIsNone(p.poll(), "an unproven process is never signalled")
        self.assertTrue(macos.kill_proven(p.pid, f["start_sec"], f["start_usec"], exe))
        self.assertEqual(p.wait(5), -9)
        self.assertEqual(macos.identity(p.pid, f["start_sec"], f["start_usec"], exe), c.GONE)
        self.assertIsNone(macos.facts(-1))
        self.assertIsNone(macos.facts(True))

    def test_appkit_sends_nothing_unless_bundle_and_executable_match(self):
        app = occluder_app(self.base)
        exe = str(app / "Contents/MacOS/Occluder")
        subprocess.run(["open", "-n", str(app), "--args", "0", "60"], check=True)
        pids = wait(lambda: appkit.running_pids("com.gpos.test.Occluder"))
        self.assertEqual(len(pids), 1)
        pid = pids[0]
        f = macos.facts(pid)
        self.assertEqual(os.path.realpath(f["executable"]), os.path.realpath(exe))
        windows = wait(lambda: [w for w in macos.windows(pid) if w["on_screen"]])
        self.assertEqual([(w["width"], w["height"]) for w in windows], [(40.0, 40.0)])
        self.assertGreater(windows[0]["layer"], 0)                 # a floating window ...
        self.assertEqual(macos.eligible_windows(pid), [])          # ... is never a capture target (layer 0 only)
        self.assertEqual(appkit.request_terminate(pid, "com.gpos.test.other", exe), (True, False))
        self.assertEqual(appkit.request_terminate(pid, "com.gpos.test.Occluder", "/Applications/Other.app/x"), (True, False))
        time.sleep(1)
        self.assertIsNotNone(macos.facts(pid), "a mismatched request sends nothing")
        self.assertEqual(appkit.request_terminate(pid, "com.gpos.test.Occluder", exe), (True, True))
        self.assertTrue(wait(lambda: macos.facts(pid) is None, 10))
        self.assertEqual(appkit.request_terminate(pid, "com.gpos.test.Occluder", exe), (False, False))
        self.assertEqual(appkit.running_pids("com.gpos.test.none"), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
