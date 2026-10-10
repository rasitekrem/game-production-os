#!/usr/bin/env python3
"""Phase 2C-9.3b (alpha.26) — the Unity live bridge 1.6.0 on Windows: the live session and the Scene-authoring slice.

    python -X utf8 tests/test_unity_windows_live.py
    GPOS_UNITY_TEST_FAST=1 python -X utf8 tests/test_unity_windows_live.py      (no Unity process)

Fast groups (no Unity process):
  WLA  gating: 12 Unity capabilities on Windows, 35 refused before anything runs, and the bridge's own allowlist
  WLB  the bridge sources: the Unity-free core compiled and tested in both platform views with the Mono bundled with
       the Editor, the whole Editor assembly compiled in both views against the Editor's reference assemblies, and
       the native imports of each view (Windows: kernel32.dll!MoveFileExW only; macOS: the frozen libc set)
  WLC  the Windows live IPC across processes on real NTFS, production code on both sides: the bridge's
       WindowsFiles.TakeRequest (in Mono, tests/unity_live_ipc_harness) against GPOS's live_ipc publish / withdraw
       / call: deterministic interleavings (a rename held in flight), sharing interference, replaced requests,
       and timed races at the windows N1 measured
  WLD  the Editor identity: exact FILETIME, executable, user, liveness and project through one handle
  WLE  call outcomes on Windows: verified withdrawal, unreadable answers, stale temporaries, reparse points

Real groups (exactly one Hub Editor; the disposable lab GPOS_UNITY_WINDOWS_LIVE_LAB, default
D:\\gpos-unity-lab-alpha26; one Editor at a time, each started through the process boundary inside its own Job):
  WLR1 install into a closed project, reinstall, and the upgrade of an exact 1.5.0 bridge
  WLR2 the exact creation-time gate (R2): editor_started_utc against GetProcessTimes on the same process handle
  WLR3 the Scene-authoring demonstration and its persistence, verified by the batch plane and from the file
  WLR4 SESSION integrity at each commit and publication boundary
  WLR5 approval rejection and expiry, the bridge's allowlist, and Human-approved stale-session recovery

Approval in the real groups is SYNTHETIC: the test-only testkit (tests/unity_live_testkit, never installed by GPOS)
presses the bridge's approval method for owners named AGENT:testkit-approve-* / AGENT:testkit-reject-*. It is not a
Human approval and is never evidence of one; any other owner is left for a Human, so its attach expires.
"""

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
import uuid
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
if sys.platform == "win32" and not sys.flags.utf8_mode:
    sys.exit("WINDOWS_UTF8_MODE_REQUIRED: run this suite as `python -X utf8 tests/test_unity_windows_live.py`")

import posix_parity as pp  # noqa: E402
import unity_fixture_builder as fixtures  # noqa: E402
import windows_standin  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import leases as lease_mod  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Actor, Subject  # noqa: E402
from gpos.tools.registry import default_registry  # noqa: E402
from gpos.tools.unity import UnityAdapter  # noqa: E402
from gpos.tools.unity import adapter as ua  # noqa: E402
from gpos.tools.unity import authoring as au  # noqa: E402
from gpos.tools.unity import bridge_install as bi  # noqa: E402
from gpos.tools.unity import identity as ident  # noqa: E402
from gpos.tools.unity import live  # noqa: E402
from gpos.tools.unity import live_ipc as ipc  # noqa: E402
from gpos.tools.unity import live_status as ls  # noqa: E402
from gpos.tools.unity import project_lock as pl  # noqa: E402

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import _winapi  # noqa: E402  (TEST-ONLY: junctions without privilege; production code never imports it)
    from ctypes import wintypes  # noqa: E402
    from gpos.tools.unity import host_win32  # noqa: E402

FW = load_framework()
FAST = os.environ.get("GPOS_UNITY_TEST_FAST") == "1"
LAB = Path(os.environ.get("GPOS_UNITY_WINDOWS_LIVE_LAB", r"D:\gpos-unity-lab-alpha26"))
EDITORS = UnityAdapter().discover() if WINDOWS else []
EDITOR_VERSION, EDITOR = EDITORS[0] if len(EDITORS) == 1 else (None, None)
DATA = Path(EDITOR).parent / "Data" if EDITOR else None
MONO = DATA / "MonoBleedingEdge" / "bin" / "mono.exe" if DATA else None
CSC = DATA / "MonoBleedingEdge" / "lib" / "mono" / "4.5" / "csc.exe" if DATA else None
API = DATA / "MonoBleedingEdge" / "lib" / "mono" / "4.7.1-api" if DATA else None
BRIDGE = ROOT / "gpos" / "tools" / "unity" / "live_bridge" / "com.gpos.live-bridge"
CORE = BRIDGE / "Editor" / "Core"
CORE_TESTS = ROOT / "tests" / "unity_live_bridge_core"
HARNESS = ROOT / "tests" / "unity_live_ipc_harness" / "IpcHarness.cs"
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
TESTKIT = ROOT / "tests" / "unity_live_testkit" / "com.gpos.live-bridge-testkit"
APPROVER = Actor("AGENT", "testkit-approve-1")          # SYNTHETIC approval (testkit), never a Human's
REJECTER = Actor("AGENT", "testkit-reject-1")
UNAPPROVED = Actor("AGENT", "claude-main")               # nobody approves this owner: its attach expires
OTHER = Actor("AGENT", "codex-worker")
WINDOWS_LIVE = (live.INSTALL, live.STATUS, live.ATTACH, live.INSPECT, live.DETACH, au.INSPECT_OBJECT, au.CREATE,
                au.SET_TRANSFORM, au.SAVE_SCENE)
SERVED = ("status", "propose-attach", "attach-status", "abandon-proposal", "bind", "propose-recovery",
          "recovery-status", "consume-recovery", "unbind", "inspect", "object-inspect", "create-gameobject",
          "set-transform", "save-scene")
LIBC = {"rename", "link", "unlink", "chmod", "realpath", "free", "mkdir", "access"}
_STATE = {"editors": [], "r2": [], "launches": []}


def windows_only(cls):
    return unittest.skipUnless(WINDOWS, "the Windows live bridge; this host is not Windows")(cls)


def toolchain_only(cls):
    return unittest.skipUnless(WINDOWS and MONO is not None and MONO.is_file(),
                               "UNITY_RUNTIME_UNAVAILABLE: no single Hub Editor with its bundled Mono")(cls)


def real(cls):
    return unittest.skipIf(FAST or not WINDOWS, "GPOS_UNITY_TEST_FAST or not Windows: no real Unity process")(cls)


def qpc():
    return time.perf_counter_ns() // 100


def csc(args, cwd):
    r = subprocess.run([str(MONO), str(CSC), "-noconfig", "-nologo", "-nostdlib", *args], capture_output=True,
                       text=True, cwd=str(cwd), timeout=600)
    return r.returncode, r.stdout + r.stderr


STD = lambda: [f"-r:{API / n}" for n in ("mscorlib.dll", "System.dll", "System.Core.dll")]


# ================================================================ WLA  gating

@windows_only
class WLA_Gating(unittest.TestCase):
    def test_twelve_capabilities_are_qualified_on_windows(self):
        caps = [c.id for c in ua.DESCRIPTOR.capabilities]
        self.assertEqual(len(caps), 47)
        self.assertEqual(ua.WINDOWS_CAPABILITIES, (ua.INSPECT, ua.EDITMODE, ua.PLAYMODE) + WINDOWS_LIVE)
        self.assertEqual(len([c for c in caps if c not in ua.WINDOWS_CAPABILITIES]), 35)

    def test_every_other_live_or_authoring_capability_is_refused_before_anything_runs(self):
        from gpos.tools.unity import assets, prefabs, sources
        others = [c for c in live.CAPABILITY_IDS + au.CAPABILITY_IDS + assets.CAPABILITY_IDS + prefabs.CAPABILITY_IDS
                  + sources.CAPABILITY_IDS if c not in WINDOWS_LIVE]
        self.assertEqual(len(others), 42 - 9)
        started = []
        original = tproc.run_process
        tproc.run_process = lambda *a, **k: started.append(a) or original(*a, **k)
        try:
            for cap in others:
                with self.subTest(capability=cap):
                    outcome = UnityAdapter().execute(types.SimpleNamespace(capability_id=cap), None)
                    self.assertEqual({d.code for d in outcome.diagnostics}, {"PLATFORM_UNSUPPORTED"})
                    self.assertIn("not available on Windows in this release", outcome.diagnostics[0].message)
        finally:
            tproc.run_process = original
        self.assertEqual(started, [])

    def test_the_bridge_serves_exactly_the_session_and_the_slice(self):
        text = (CORE / "Protocol.cs").read_text(encoding="utf-8")
        block = re.search(r"WindowsCommands = \{(.*?)\};", text, re.S).group(1)
        self.assertEqual(tuple(re.findall(r'"([a-z-]+)"', block)), SERVED)
        commands = {au.COMMANDS[c] for c in WINDOWS_LIVE if c in au.COMMANDS}
        self.assertLessEqual(commands, set(SERVED))
        # Play Mode, the other Scene edits, assets, prefabs and sources are never served on Windows
        for c in ("enter-playmode", "pause", "add-component", "set-property", "create-material", "create-prefab",
                  "sync-sources", "compilation-status"):
            self.assertNotIn(c, SERVED)


# ================================================================ WLB  the bridge sources

DLLIMPORT = re.compile(r'\[DllImport\("([^"]+)"[^\]]*\]\s*static extern \w+ (\w+)\(')


def windows_view(text):
    """The source a Windows Editor compiles: UNITY_EDITOR_WIN branches kept, their #else branches dropped."""
    out, stack = [], []
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("#if UNITY_EDITOR_WIN"):
            stack.append(["win", True]); continue
        if s.startswith("#if !UNITY_EDITOR_WIN"):
            stack.append(["notwin", False]); continue
        if s == "#else" and stack:
            stack[-1][1] = not stack[-1][1]; continue
        if s == "#endif" and stack:
            stack.pop(); continue
        if all(keep for _, keep in stack):
            out.append(line)
    return "".join(out)


@windows_only
class WLB_BridgeSources(unittest.TestCase):
    def test_the_windows_view_imports_only_movefileexw_and_the_macos_view_only_the_frozen_libc_set(self):
        win, mac = set(), set()
        for f in BRIDGE.rglob("*.cs"):
            text = f.read_text(encoding="utf-8")
            win |= {(dll, fn) for dll, fn in DLLIMPORT.findall(windows_view(text))}
            mac |= {(dll, fn) for dll, fn in DLLIMPORT.findall(pp.macos_view(text))}
        self.assertEqual(win, {("kernel32.dll", "MoveFileExW")})
        self.assertEqual({dll for dll, _ in mac}, {"libc"})
        self.assertEqual({fn for _, fn in mac}, LIBC)
        decl = (CORE / "WindowsFiles.cs").read_text(encoding="utf-8")
        self.assertIn('[DllImport("kernel32.dll", EntryPoint = "MoveFileExW", CharSet = CharSet.Unicode, '
                      'ExactSpelling = true, SetLastError = true)]', decl)
        self.assertEqual(sorted(set(re.findall(r"MoveFileExW\(\w+, \w+, ([^)]*)\)", decl))),
                         ["0", "MoveFileReplaceExisting | MoveFileWriteThrough"])
        for forbidden in ("SetFileInformationByHandle", "CreateHardLink", "DeleteFileW", "CreateFileW", "ReplaceFile"):
            self.assertNotIn(forbidden, "".join(windows_view(f.read_text(encoding="utf-8")) for f in BRIDGE.rglob("*.cs")))

    @toolchain_only
    def test_the_core_passes_its_tests_in_both_views(self):
        work = Path(tempfile.mkdtemp(prefix="gpos-live-core-"))
        try:
            for view, define in (("macOS", None), ("Windows", "UNITY_EDITOR_WIN")):
                with self.subTest(view=view):
                    exe = work / f"core-{view}.exe"
                    rc, out = csc(["-t:exe", f"-out:{exe}", *STD()] + ([f"-define:{define}"] if define else []) +
                                  [str(p) for p in sorted(CORE.glob("*.cs")) + sorted(CORE_TESTS.glob("*.cs"))], work)
                    self.assertEqual(rc, 0, out)
                    r = subprocess.run([str(MONO), str(exe)], capture_output=True, text=True, cwd=str(work),
                                       timeout=600)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertIn("ALL PASSED", r.stdout)
                    if define:
                        self.assertIn("PASS WindowsPinDecidesTheClaim", r.stdout)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @toolchain_only
    def test_the_editor_assembly_compiles_in_both_views_with_its_own_native_imports(self):
        work = Path(tempfile.mkdtemp(prefix="gpos-live-editor-"))
        try:
            refs = [f"-r:{p}" for p in sorted((DATA / "Managed" / "UnityEngine").glob("*.dll"))]
            sources = [str(p) for p in sorted((BRIDGE / "Editor").rglob("*.cs"))]
            for view, defines, has, lacks in (("macOS", "UNITY_EDITOR;UNITY_EDITOR_OSX", b"libc", b"kernel32.dll"),
                                              ("Windows", "UNITY_EDITOR;UNITY_EDITOR_WIN", b"kernel32.dll", b"libc")):
                with self.subTest(view=view):
                    dll = work / f"editor-{view}.dll"
                    rc, out = csc(["-t:library", f"-out:{dll}", f"-define:{defines}", "-nowarn:0618,0649,0414,0169",
                                   *STD(), f"-r:{API / 'Facades' / 'netstandard.dll'}", *refs, *sources], work)
                    self.assertEqual(rc, 0, out)
                    data = dll.read_bytes()
                    self.assertIn(has, data)
                    self.assertNotIn(lacks, data)
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ================================================================ WLC  the Windows live IPC across processes

if WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)    # TEST-ONLY adversary: a rename held "in flight"
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.SetFileInformationByHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
DELETE, GENERIC_READ, GENERIC_WRITE, SYNCHRONIZE = 0x00010000, 0x80000000, 0x40000000, 0x00100000
SHARE_ALL, SHARE_RW = 7, 3


def handle(path, access, share):
    h = _k32.CreateFileW(str(path), access, share, None, 3, 0, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), f"CreateFileW {path}")
    return h


def rename_by_handle(h, dst):
    """TEST ONLY: the rename step MoveFileExW performs through its own handle (FileRenameInfo, no replace). This is
    how a test holds a rename "in flight" deterministically; the product never calls it (D-L8)."""
    name = str(dst).encode("utf-16-le")
    buf = ctypes.create_string_buffer(20 + len(name) + 2)
    ctypes.memmove(ctypes.addressof(buf) + 16, ctypes.byref(wintypes.DWORD(len(name))), 4)
    ctypes.memmove(ctypes.addressof(buf) + 20, name, len(name))
    if not _k32.SetFileInformationByHandle(h, 3, buf, len(buf)):
        raise OSError(ctypes.get_last_error(), "rename by handle")


class Harness:
    """The bridge side (Mono, production WindowsFiles) as a separate process."""

    def __init__(self, work):
        exe = Path(work) / "ipc-harness.exe"
        rc, out = csc(["-t:exe", f"-out:{exe}", *STD(), "-define:UNITY_EDITOR_WIN", str(CORE / "WindowsFiles.cs"),
                       str(HARNESS)], work)
        if rc:
            raise AssertionError(out)
        self.p = subprocess.Popen([str(MONO), str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8-sig",
                                  cwd=str(work))
        greeting = self.p.stdout.readline()
        if not greeting.startswith("READY"):
            self.p.stdin.close(); self.p.wait(30); self.p.stdout.close()
            raise AssertionError("IPC harness greeting: " + repr(greeting))

    def send(self, *parts):
        self.p.stdin.write("\t".join(map(str, parts)) + "\n")
        self.p.stdin.flush()

    def recv(self):
        return self.p.stdout.readline().strip()

    def close(self):
        self.send("quit")
        self.p.wait(30)


@toolchain_only
class WLC_NtfsIpc(unittest.TestCase):
    """Exactly-once claim versus withdrawal: never both decided, a WITHDRAWN request never executes, every request
    executes at most once, a decision matches where the file rests."""

    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix="gpos-live-ipc-")).resolve()
        cls.bridge = Harness(cls.work)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.bridge.close()
        windows_standin.remove_tree(cls.work)

    def channel(self):
        WLC_NtfsIpc.n += 1
        live_dir = self.work / f"l{self.n:04d}"
        for f in ipc.FOLDERS:
            (live_dir / f).mkdir(parents=True)
        return ipc.Channel(live_dir)

    def publish(self, ch):
        return ipc.publish(ch, "create-gameobject", {"name": "x"}, "AGENT:w1", "b" * 32)

    def facts(self, ch, rid):
        eff = ch.root / "effects.log"
        executions = eff.read_text().split().count(rid) if eff.exists() else 0
        rests = [f for f in ("requests", "claimed", "withdrawn") if (ch.root / f / f"{rid}.json").exists()]
        return executions, rests

    def check(self, ch, rid, bridge, gpos, exactly_one=True):
        executions, rests = self.facts(ch, rid)
        won, withdrawn = bridge == "Won", gpos is True
        self.assertFalse(won and withdrawn, "both decided")
        self.assertLessEqual(executions, 1)
        if withdrawn:
            self.assertEqual((executions, rests), (0, ["withdrawn"]))
        if won:
            self.assertEqual((executions, rests), (1, ["claimed"]))
        if exactly_one:
            self.assertNotEqual(won, withdrawn, f"exactly one decision: bridge {bridge}, gpos {gpos}")
        return executions, rests

    def take(self, ch, rid, pin_ms=20, at=0):
        self.bridge.send("take", ch.root, rid, at, pin_ms)
        return self.bridge.recv()

    def test_sequential_claim_then_withdraw_and_withdraw_then_claim(self):
        ch = self.channel()
        rid = self.publish(ch)
        b = self.take(ch, rid)
        g = ipc.withdraw(ch, rid)
        self.assertEqual((b, g), ("Won", False))
        self.check(ch, rid, b, g)
        rid = self.publish(ch)
        g = ipc.withdraw(ch, rid)
        b = self.take(ch, rid)
        self.assertEqual((g, b), (True, "NotTaken"))
        self.check(ch, rid, b, g)

    def test_gpos_rename_in_flight_lands_after_the_bridge_rename(self):
        """The N1 double success: GPOS's rename handle is open while the bridge renames; GPOS's rename then lands."""
        ch = self.channel()
        rid = self.publish(ch)
        h = handle(ch.requests / f"{rid}.json", DELETE | SYNCHRONIZE, SHARE_ALL)
        self.bridge.send("take", ch.root, rid, 0, 3000)
        time.sleep(0.3)                                  # the bridge renamed and is failing to pin
        rename_by_handle(h, ch.withdrawn / f"{rid}.json")
        _k32.CloseHandle(h)
        b = self.bridge.recv()
        self.assertEqual(b, "Lost")
        self.assertEqual(self.facts(ch, rid), (0, ["withdrawn"]))

    def test_gpos_rename_in_flight_that_never_lands_leaves_the_claim_to_the_bridge(self):
        ch = self.channel()
        rid = self.publish(ch)
        h = handle(ch.requests / f"{rid}.json", DELETE | SYNCHRONIZE, SHARE_ALL)
        self.bridge.send("take", ch.root, rid, 0, 3000)
        time.sleep(0.3)
        _k32.CloseHandle(h)
        self.assertEqual(self.bridge.recv(), "Won")
        self.assertIs(ipc.withdraw(ch, rid), False)
        self.assertEqual(self.facts(ch, rid), (1, ["claimed"]))

    def test_bridge_rename_in_flight_lands_after_the_gpos_rename(self):
        """GPOS renamed and is pinning while the bridge's rename handle is open; the bridge's rename lands: GPOS must
        not report WITHDRAWN, and the request is the bridge's (one execution)."""
        ch = self.channel()
        rid = self.publish(ch)
        h = handle(ch.requests / f"{rid}.json", DELETE | SYNCHRONIZE, SHARE_ALL)
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("gpos", ipc.withdraw(ch, rid)))
        saved = ipc.RETRY_SECONDS
        ipc.RETRY_SECONDS = 3.0
        try:
            t.start()
            time.sleep(0.3)
            rename_by_handle(h, ch.claimed / f"{rid}.json")
            _k32.CloseHandle(h)
            t.join(10)
        finally:
            ipc.RETRY_SECONDS = saved
        self.assertIs(box["gpos"], False)                # moved away from withdrawn/: the bridge has it
        self.assertEqual(self.facts(ch, rid), (0, ["claimed"]))

    def test_both_renames_land_before_either_pin_in_both_orders(self):
        for first in ("bridge", "gpos"):
            with self.subTest(first=first):
                ch = self.channel()
                rid = self.publish(ch)
                src = ch.requests / f"{rid}.json"
                hb, hg = handle(src, DELETE | SYNCHRONIZE, SHARE_ALL), handle(src, DELETE | SYNCHRONIZE, SHARE_ALL)
                for h, folder in ([(hb, "claimed"), (hg, "withdrawn")] if first == "bridge"
                                  else [(hg, "withdrawn"), (hb, "claimed")]):
                    rename_by_handle(h, ch.root / folder / f"{rid}.json")
                _k32.CloseHandle(hb)
                _k32.CloseHandle(hg)
                # GPOS's own rename happened (by handle); what it decides is its pin of withdrawn/<id>
                g = self.gpos_decide(ch, rid)
                rests = self.facts(ch, rid)[1]
                self.assertEqual(g, rests == ["withdrawn"])
                self.assertEqual(len(rests), 1)

    def gpos_decide(self, ch, rid):
        """GPOS's PIN -> VERIFY after its rename: what _windows_withdraw does past its rename."""
        dst = ch.withdrawn / f"{rid}.json"
        opened = ipc._windows_open(dst, deny_writers=True)
        if opened is None:
            return False
        fd, pins = opened
        os.close(fd)
        ipc._pw.close_all(pins)
        return True

    def test_a_reader_without_share_delete_blocks_both_renames_and_gpos_never_reports_withdrawn(self):
        ch = self.channel()
        rid = self.publish(ch)
        h = handle(ch.requests / f"{rid}.json", GENERIC_READ, SHARE_RW)
        try:
            self.assertIsNone(ipc.withdraw(ch, rid))       # undecided: it may still be claimed later
            self.assertEqual(self.take(ch, rid), "NotTaken")
        finally:
            _k32.CloseHandle(h)
        self.assertEqual(self.take(ch, rid), "Won")         # claimed once the reader lets go
        self.assertEqual(self.facts(ch, rid), (1, ["claimed"]))

    def test_a_reader_sharing_delete_never_changes_the_decision(self):
        ch = self.channel()
        rid = self.publish(ch)
        h = handle(ch.requests / f"{rid}.json", GENERIC_READ, SHARE_ALL)
        try:
            self.assertIs(ipc.withdraw(ch, rid), True)
        finally:
            _k32.CloseHandle(h)
        self.assertEqual(self.take(ch, rid), "NotTaken")
        self.assertEqual(self.facts(ch, rid), (0, ["withdrawn"]))

    def test_a_writer_on_the_claimed_request_makes_the_claim_undecided_and_never_executed(self):
        ch = self.channel()
        rid = self.publish(ch)
        h = handle(ch.requests / f"{rid}.json", GENERIC_WRITE, SHARE_ALL)
        try:
            self.assertEqual(self.take(ch, rid, pin_ms=60), "Undecided")
        finally:
            _k32.CloseHandle(h)
        self.assertEqual(self.facts(ch, rid), (0, ["claimed"]))
        self.assertEqual(json.loads((ch.responses / f"{rid}.json").read_text())["status"], "INTERRUPTED")

    def test_a_replaced_request_is_never_reported_withdrawn(self):
        ch = self.channel()
        rid = self.publish(ch)
        alien = ch.root / "alien"
        alien.write_text(json.dumps({"request_id": rid, "command": "create-gameobject"}))
        os.replace(alien, ch.requests / f"{rid}.json")      # same name, another file
        self.assertIsNone(ipc.withdraw(ch, rid))
        self.assertEqual(self.facts(ch, rid)[1], ["withdrawn"])

    def test_a_request_moved_away_by_a_third_process_is_unknown_never_withdrawn(self):
        ch = self.channel()
        rid = self.publish(ch)
        os.rename(ch.requests / f"{rid}.json", ch.root / f"{rid}.moved")
        self.assertIs(ipc.withdraw(ch, rid), False)
        self.assertEqual(self.take(ch, rid), "NotTaken")

    def test_timed_races_at_the_measured_windows(self):
        """N1 measured the double-rename window at about +-100..500 us around one instant. Race the production
        claim and the production withdrawal there: every invariant, every time."""
        tally = Counter()
        for offset in (-500, -100, -50, 0, 0, 50, 100, 500):
            for _ in range(60):
                ch = self.channel()
                rid = self.publish(ch)
                t = qpc() + 30000
                self.bridge.send("take", ch.root, rid, t, 20)
                while qpc() < t + offset * 10:
                    pass
                g = ipc.withdraw(ch, rid)
                b = self.bridge.recv()
                self.check(ch, rid, b, g)
                tally[f"bridge={b} gpos={g}"] += 1
                shutil.rmtree(ch.root)
        print("\nWLC races:", dict(tally))
        self.assertGreater(tally["bridge=Lost gpos=True"] + tally["bridge=Won gpos=False"], 0)


# ================================================================ WLD  the Editor identity

class FakeProcess:
    def __init__(self, created=134360640001234567, image=r"C:\Hub\Editor\6000.6.4f1\Editor\Unity.exe", user=True,
                 running=True, argv=None, project=r"D:\lab\p\Game"):
        self.c, self.i, self.u, self.r = created, image, user, running
        self.a = argv if argv is not None else [image, "-projectpath", project, "-useHub"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def running(self):
        return self.r

    def created(self):
        return self.c

    def image(self):
        return self.i

    def same_user(self):
        return self.u

    def argv(self):
        return (self.a, None) if self.a is not False else (None, "unreadable")


class FakeHost:
    def __init__(self, process=None, problem=None):
        self.p, self.problem = process, problem

    def open_process(self, pid):
        return (self.p, None) if self.p else (None, self.problem)


@windows_only
class WLD_Identity(unittest.TestCase):
    T = "2026-10-10T00:00:00.1234567Z"
    FT = 134360640001234567
    EDITOR = r"C:\Hub\Editor\6000.6.4f1\Editor\Unity.exe"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-live-id-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "My Game"
        self.project.mkdir()
        self.editor = self.tmp / "Unity.exe"
        self.editor.write_bytes(b"")

    def who(self, process=None, problem=None, recorded=T, pid=4242):
        return ls.windows_identity(pid, recorded, "6000.6.4f1", str(self.project), FakeHost(process, problem),
                                   editor=str(self.editor))

    def proc(self, **kw):
        kw.setdefault("image", str(self.editor))
        kw.setdefault("project", str(self.project))
        return FakeProcess(**kw)

    def test_the_timestamp_is_parsed_to_the_exact_filetime(self):
        self.assertEqual(ls.filetime_of(self.T), self.FT)
        self.assertEqual(ls.filetime_of("1601-01-01T00:00:00.0000001Z"), 1)
        for bad in ("2026-10-10T00:00:00.123456Z", "2026-10-10T00:00:00.1234567+00:00", "2026-10-10T00:00:00Z",
                    "2026-13-10T00:00:00.1234567Z", None, 5):
            self.assertIsNone(ls.filetime_of(bad), bad)

    def test_alive_only_with_every_fact_exact(self):
        self.assertEqual(self.who(self.proc()), ls.ALIVE)

    def test_a_creation_time_one_tick_off_is_never_alive(self):
        self.assertEqual(self.who(self.proc(created=self.FT + 1)), ls.REUSED)    # created later: another process
        self.assertEqual(self.who(self.proc(created=self.FT - 1)), ls.UNKNOWN)   # earlier: inconsistent

    def test_gone_and_exited(self):
        self.assertEqual(self.who(None, None), ls.GONE)
        self.assertEqual(self.who(self.proc(running=False)), ls.GONE)

    def test_contradicting_facts_with_the_exact_time_are_unknown(self):
        other = self.tmp / "Other.exe"
        other.write_bytes(b"")
        for name, p in {"another executable": self.proc(image=str(other)), "another user": self.proc(user=False),
                        "user unknown": self.proc(user=None), "another project": self.proc(project=str(self.tmp)),
                        "no project": self.proc(argv=[str(self.editor), "-batchmode"]),
                        "argv unreadable": self.proc(argv=False), "liveness unknown": self.proc(running=None),
                        "time unreadable": self.proc(created=None)}.items():
            with self.subTest(case=name):
                self.assertEqual(self.who(p), ls.UNKNOWN)

    def test_never_from_the_pid_alone(self):
        for pid in (None, True, 0, 4, "4242", 4242.0):
            self.assertEqual(self.who(self.proc(), pid=pid), ls.UNKNOWN)
        self.assertEqual(self.who(None, "access denied"), ls.UNKNOWN)
        self.assertEqual(self.who(self.proc(), recorded="2026-10-10T00:00:00.123456Z"), ls.UNKNOWN)

    def test_the_project_matches_in_any_spelling_windows_accepts(self):
        spelled = str(self.project).upper().replace("\\", "/")
        self.assertEqual(self.who(self.proc(argv=[str(self.editor), "-projectPath", spelled])), ls.ALIVE)

    def test_the_live_plane_uses_the_windows_proof(self):
        import inspect
        self.assertIn("ls.windows_identity(", inspect.getsource(live.Live.editor))


# ================================================================ WLE  call outcomes on Windows

@windows_only
class WLE_CallOutcomes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-live-call-")).resolve()
        self.addCleanup(windows_standin.remove_tree, self.tmp)
        for f in ipc.FOLDERS:
            (self.tmp / f).mkdir()
        self.ch = ipc.Channel(self.tmp)

    def call(self, wait=0.2):
        return ipc.call(self.ch, "inspect", {}, "AGENT:w1", "b" * 32, "s" * 32, wait=wait, poll=0.02)

    def test_a_request_nobody_claims_is_withdrawn_after_the_pin(self):
        r = self.call()
        self.assertEqual(r.outcome, ipc.WITHDRAWN)
        self.assertEqual(os.listdir(self.tmp / "withdrawn"), [f"{r.request_id}.json"])
        self.assertEqual(os.listdir(self.tmp / "requests"), [])

    def test_publication_never_makes_a_hard_link_and_never_replaces(self):
        rid = ipc.publish(self.ch, "inspect", {}, "AGENT:w1", "b" * 32, "s" * 32)
        st = os.stat(self.tmp / "requests" / f"{rid}.json")
        self.assertEqual(st.st_nlink, 1)
        self.assertEqual([n for n in os.listdir(self.tmp / "requests") if n.startswith(".")], [])
        self.assertEqual(ipc._PUBLISHED[rid][:2], (st.st_dev, st.st_ino))

    def test_an_unreadable_answer_after_publication_is_unknown_never_an_error(self):
        box = {}

        def answer():
            # Only the published request is claimable; .tmp-* is a writer's private staging file.
            names = []
            while not names:
                names = [n for n in os.listdir(self.tmp / "requests") if re.fullmatch(r"[0-9a-f]{32}\.json", n)]
                if not names: time.sleep(0.005)
            name = names[0]
            os.rename(self.tmp / "requests" / name, self.tmp / "claimed" / name)        # claimed ...
            (self.tmp / "responses" / name).write_text("{not json")                     # ... and garbled
            box["done"] = True
        t = threading.Thread(target=answer)
        t.start()
        r = self.call(wait=0.5)
        t.join(5)
        self.assertEqual(r.outcome, ipc.UNKNOWN)

    def test_a_held_request_is_unknown_never_withdrawn(self):
        original, held = ipc._windows_publish, []

        def publish(channel, rid, body):                   # a scanner opens the request the moment it appears
            rid = original(channel, rid, body)
            held.append(handle(channel.requests / f"{rid}.json", GENERIC_READ, SHARE_RW))
            return rid
        ipc._windows_publish = publish
        try:
            r = self.call(wait=0.3)
        finally:
            ipc._windows_publish = original
            for h in held:
                _k32.CloseHandle(h)
        self.assertEqual(len(held), 1)
        self.assertEqual(r.outcome, ipc.UNKNOWN)
        self.assertEqual(len(os.listdir(self.tmp / "requests")), 1)   # still there: it may yet be claimed

    def test_stale_publication_temporaries_are_swept_and_nothing_else(self):
        stale = self.tmp / "requests" / f".tmp-{'a' * 32}"
        fresh = self.tmp / "requests" / f".tmp-{'b' * 32}"
        other = self.tmp / "requests" / ".tmp-not-ours"
        for p in (stale, fresh, other):
            p.write_text("{}")
        old = time.time() - 3600
        os.utime(stale, (old, old))
        os.utime(other, (old, old))
        ipc.publish(self.ch, "inspect", {}, "AGENT:w1", "b" * 32, "s" * 32)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists() and other.exists())

    def test_reparse_points_and_aliases_are_refused(self):
        outside = self.tmp.parent / f"{self.tmp.name}-outside"
        outside.mkdir()
        self.addCleanup(shutil.rmtree, outside, True)
        live_dir = self.tmp / "j"
        _winapi.CreateJunction(str(outside), str(live_dir))
        with self.assertRaises(ipc.ChannelProblem):
            ipc.Channel(live_dir)
        (outside / "bridge.json").write_text("{}")
        with self.assertRaises(ipc.ChannelProblem):
            ipc.read_bounded(live_dir / "bridge.json")
        (self.tmp / "bridge.json").write_text("{}")
        os.link(self.tmp / "bridge.json", self.tmp / "alias.json")
        with self.assertRaises(ipc.ChannelProblem):          # a file with two names is never trusted
            ipc.read_bounded(self.tmp / "bridge.json")
        self.assertIsNone(ipc.read_bounded(self.tmp / "missing.json"))
        self.assertIsNone(ipc.read_bounded(self.tmp / "no-such-dir" / "bridge.json"))

    def test_the_gpos_root_and_install_paths_refuse_junctions(self):
        outside = self.tmp.parent / f"{self.tmp.name}-root"
        (outside / ".game" / "gpos").mkdir(parents=True)
        (outside / ".game" / "gpos" / "project-config.json").write_text("{}")
        self.addCleanup(shutil.rmtree, outside, True)
        root = self.tmp / "root"
        (root / "Game").mkdir(parents=True)
        _winapi.CreateJunction(str(outside / ".game"), str(root / ".game"))
        self.assertIsNone(ident.gpos_root_of(root / "Game"))
        runtime = self.tmp / "rt"
        runtime.mkdir()
        _winapi.CreateJunction(str(outside), str(runtime / ".game"))
        with self.assertRaises(OSError):
            bi.runtime_path(runtime, "unity", "install-staging", "x")



# ================================================================ real groups: the lab

MUTATING = {c.id for c in ua.DESCRIPTOR.capabilities if c.mutating}
LAUNCH_SECONDS = 1500.0            # an Editor's whole life; its Job ends it at the latest then


def setUpModule():
    if not WINDOWS or FAST:
        return
    import test_unity_windows as tuw
    if len(EDITORS) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C9_3B: {len(EDITORS)} Hub Unity Editors found; exactly "
                           f"one is required")
    if not LAB.is_dir():
        raise RuntimeError(f"UNITY_LAB_UNAVAILABLE_FOR_PHASE2C9_3B: the disposable lab root {LAB} does not exist")
    if tuw.editors_naming(LAB):
        raise RuntimeError("UNITY_LAB_BUSY_FOR_PHASE2C9_3B: a Unity Editor already has a lab project open")
    _STATE["tuw"] = tuw
    _STATE["prefs"], _STATE["upm"], _STATE["licensing"] = tuw.prefs_snapshot(), tuw.upm_configs(), \
        tuw.licensing_clients()
    _STATE["work"] = LAB / f"suite-{os.getpid()}"
    _STATE["work"].mkdir()


def tearDownModule():
    if not WINDOWS or FAST or "work" not in _STATE:
        return
    tuw = _STATE["tuw"]
    try:
        for editor in _STATE["editors"]:
            editor.stop()
        left = tuw.editors_naming(_STATE["work"])
        if left:
            raise AssertionError(f"Unity processes of this suite are still running: {left}")
        now = tuw.prefs_snapshot()
        moved = {re.sub(r"_h\d+$", "", k) for k in set(now) | set(_STATE["prefs"]) if now.get(k) != _STATE["prefs"].get(k)}
        print(f"\nEditorPrefs names changed by this run: {sorted(moved)}")
        unexpected = sorted(moved - tuw.ACCEPTED_PREFS)
        if unexpected:
            raise AssertionError(f"UNITY_SHARED_USER_STATE_UNEXPECTED_MUTATION: {unexpected}")
        if tuw.upm_configs() != _STATE["upm"]:
            raise AssertionError("the user's Package Manager configuration files changed")
        if tuw.licensing_clients() != _STATE["licensing"]:
            raise AssertionError("the shared Unity licensing client changed (GPOS never touches it)")
        print("R2 measurements (editor_started_utc vs GetProcessTimes, same handle):")
        for m in _STATE["r2"]:
            print(f"  pid {m['pid']}: {m['editor_started_utc']} -> {m['published']} | GetProcessTimes {m['created']} "
                  f"| equal={m['equal']}")
    finally:
        windows_standin.remove_tree(_STATE["work"])
        if _STATE["work"].exists():
            raise AssertionError(f"this suite's lab directory could not be removed completely: {_STATE['work']}")


def r2_measure(bridge):
    """R2: the bridge's editor_started_utc, parsed to the exact FILETIME, against GetProcessTimes read through one
    limited-query handle on the same process (with that handle's image, liveness and command line)."""
    pid, text = bridge["editor_pid"], bridge["editor_started_utc"]
    process, problem = host_win32.open_process(pid)
    if process is None:
        raise AssertionError(f"R2: process {pid} cannot be opened ({problem})")
    with process:
        created, image, running, (argv, _) = process.created(), process.image(), process.running(), process.argv()
    published = ls.filetime_of(text)
    m = {"pid": pid, "editor_started_utc": text, "published": published, "created": created,
         "equal": published is not None and published == created, "image": image, "running": running,
         "argv": argv}
    _STATE["r2"].append(m)
    return m


class LabEditor:
    """One lab-owned batch-mode Editor with the testkit, started through the process boundary inside its own Job: it
    ends with the testkit's `quit` or `crash`, or with its Job at LAUNCH_SECONDS. The test never signals it."""

    def __init__(self, root, game):
        self.root, self.game = Path(root), Path(game)
        self.live = ident.live_dir(self.root, ident.project_key(ident.relative(self.root, self.game)))
        self.thread, self.box, self.pid, self.launches = None, {}, None, 0
        _STATE["editors"].append(self)

    def bridge(self):
        try:
            return ipc.read_bounded(self.live / "bridge.json")
        except ipc.ChannelProblem:
            return None

    def heartbeat(self):
        try:
            return ipc.read_bounded(self.live / "heartbeat.json") or {}
        except ipc.ChannelProblem:
            return {}

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def launch(self, ready=900):
        from gpos.tools import process_win32 as pwin
        assert not self.running(), "one Editor at a time"
        assert not _STATE["tuw"].editors_naming(_STATE["work"]), "one Editor at a time"
        self.launches += 1
        ws = self.root.parent / f"ws-{self.launches}"
        ws.mkdir(parents=True, exist_ok=True)
        cache = self.root.parent / "upm-cache"
        cache.mkdir(exist_ok=True)
        old = (self.bridge() or {}).get("boot_id")
        spec = tproc.ToolProcessSpec(
            executable=EDITOR, argv=("-batchmode", "-projectPath", str(self.game), "-logFile", str(ws / "editor.log"),
                                     "-upmLogFile", str(ws / "upm.log"), "-cacheServerEnableDownload", "false",
                                     "-cacheServerEnableUpload", "false"),
            cwd=str(ws), timeout=LAUNCH_SECONDS, env=ua.upm_environment(ws, cache))
        pids, self.box = {}, {}
        original = pwin.HOOKS.get("before_resume")
        pwin.HOOKS["before_resume"] = lambda info, readers: pids.setdefault("root", info.dwProcessId)

        def run():
            self.box["outcome"] = tproc.run_process(spec, [str(self.root.parent)])
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        try:
            end = time.monotonic() + 60
            while "root" not in pids and time.monotonic() < end and self.thread.is_alive():
                time.sleep(0.05)
        finally:
            if original is None:
                pwin.HOOKS.pop("before_resume", None)
            else:
                pwin.HOOKS["before_resume"] = original
        self.pid = pids.get("root")
        end = time.monotonic() + ready
        while time.monotonic() < end:
            b = self.bridge()
            if b and b.get("state") == "READY" and b.get("boot_id") != old and b.get("editor_pid") == self.pid:
                m = r2_measure(b)
                _STATE["launches"].append({"pid": self.pid, "boot": b["boot_id"], "r2_equal": m["equal"]})
                if not m["equal"]:
                    raise AssertionError(f"R2_TIMESTAMP_MISMATCH: {m}")
                return b
            if not self.thread.is_alive():
                raise AssertionError(f"the lab Editor ended ({self.box.get('outcome')}) before its bridge was READY: "
                                     f"{self.log_tail(ws)}")
            time.sleep(0.5)
        raise AssertionError(f"the lab Editor's bridge did not become READY: {self.log_tail(ws)}")

    @staticmethod
    def log_tail(ws):
        try:
            return (ws / "editor.log").read_text(encoding="utf-8", errors="replace")[-3000:]
        except OSError:
            return "(no log)"

    def trigger(self, name):
        d = self.game / "Temp" / "gpos-testkit"
        d.mkdir(parents=True, exist_ok=True)
        (d / f".{name}").write_text("")
        os.replace(d / f".{name}", d / name)

    def human(self, timeout=180, **op):
        """A testkit op standing in for what a person (or the fixture) does in the Editor. TEST ONLY."""
        oid = uuid.uuid4().hex
        d = self.game / "Temp" / "gpos-testkit"
        d.mkdir(parents=True, exist_ok=True)
        (d / f".{oid}").write_text(json.dumps(op))
        os.rename(d / f".{oid}", d / f"op-{oid}.json")
        out = self.game / "Temp" / "gpos-testkit-out" / f"{oid}.json"
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if out.exists():
                value = json.loads(out.read_text())
                if not value.get("ok"):
                    raise AssertionError(f"testkit {op}: {value}")
                return value
            time.sleep(0.05)
        raise AssertionError(f"testkit {op} timed out")

    def end(self, how, timeout=240):
        self.trigger(how)
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise AssertionError(f"the lab Editor did not end after `{how}`")
        return self.box.get("outcome")

    def quit(self, timeout=240):
        return self.end("quit", timeout)

    def stop(self):
        if self.running():
            try:
                self.end("quit", 180)
            except AssertionError:
                self.end("crash", 120)


def real_project(name, demo=True, testkit=True):
    p = _STATE["work"] / name / "p"
    shutil.copytree(FIXTURE, p)
    if demo:
        fixtures.make_live_demo_project(p / "Game", EDITOR_VERSION, EDITOR)
    else:
        fixtures.make_project(p / "Game", "pass", EDITOR_VERSION, EDITOR)
    if testkit:
        shutil.copytree(TESTKIT, p / "Game" / "Packages" / TESTKIT.name)
    return p


def real_request(p, cap, actor=APPROVER, **kw):
    kw.setdefault("inputs", {"unity_project": "Game"})
    if cap in MUTATING and not kw.get("dry_run"):
        kw.setdefault("allow_mutation", True)
    return execute(default_registry(FW), ExecutionRequest(
        adapter_id="unity", capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", "rev-1"),
        project_root=str(p), actor=actor, **kw))


def codes(result):
    return {d.code for d in result.diagnostics}


def text(result):
    return " | ".join(f"{d.code}: {d.message}" for d in result.diagnostics)


class RealCase(unittest.TestCase):
    p = editor = None

    def run_cap(self, cap, actor=APPROVER, session_id=None, timeout=None, **inputs):
        time.sleep(0.2)   # well inside the bridge's admission rate
        inputs = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in inputs.items()}
        inputs.setdefault("unity_project", "Game")
        kw = {"inputs": inputs}
        if timeout:
            kw["timeout"] = timeout
        if session_id:
            kw["session_id"] = session_id
        return real_request(self.p, cap, actor, **kw)

    def ok(self, result, code=None, status=tdg.SUCCESS):
        """The result's data. status=None: any outcome but SUCCESS (a refusal), with `code` among its diagnostics."""
        if status is None:
            self.assertNotEqual(result.status, tdg.SUCCESS, text(result))
        else:
            self.assertEqual(result.status, status, text(result))
        if code:
            self.assertIn(code, codes(result), text(result))
        return result.data

    def resource(self):
        return f"EDITOR_PROJECT:{Path(self.p).resolve()}"

    def holder(self):
        return lease_mod.holder(self.p, "unity", self.resource())

    def status(self):
        return self.ok(self.run_cap(live.STATUS))

    def attach(self, actor=APPROVER):
        return self.ok(self.run_cap(live.ATTACH, actor=actor, timeout=120), "LIVE_SESSION_ATTACHED")["session_id"]

    def detach(self, sid, actor=APPROVER, code="LIVE_SESSION_DETACHED", status=tdg.SUCCESS):
        return self.ok(self.run_cap(live.DETACH, actor=actor, session_id=sid, timeout=120), code, status)

    def channel(self):
        return ipc.Channel(self.editor.live)

    def raw(self, command, args, owner="AGENT:testkit-approve-1", session_id=None, boot=None, wait=10.0):
        b = self.editor.bridge()
        return ipc.call(self.channel(), command, args, owner, boot or b["boot_id"], session_id, wait=wait)

    def events(self):
        try:
            return (self.editor.live / "events.jsonl").read_text(encoding="utf-8")
        except OSError:
            return ""


def need_r2(cls):
    if not _STATE.get("r2_passed"):
        raise unittest.SkipTest("NOT_RUN: the R2 exact creation-time gate has not passed in this run")


# ---------------------------------------------------------------- WLR1  install and the 1.5.0 upgrade

@real
class WLR1_Install(RealCase):
    def test_01_install_into_a_closed_project_and_reinstall(self):
        self.p = real_project("install", demo=False, testkit=False)
        r = self.run_cap(live.INSTALL)
        self.ok(r, "LIVE_BRIDGE_INSTALLED")
        self.assertTrue(r.mutation_performed)
        self.assertEqual(bi.inspect_target(self.p / "Game", bi.verify_source())[0], bi.EXACT)
        again = self.run_cap(live.INSTALL)
        self.ok(again, "LIVE_BRIDGE_ALREADY_INSTALLED")
        self.assertFalse(again.mutation_performed)

    def test_02_an_exact_1_6_0_bridge_is_upgraded_and_then_runs_as_1_7_0(self):
        self.p = real_project("upgrade", demo=False, testkit=True)
        target = self.p / "Game" / "Packages" / bi.PACKAGE_ID
        import io
        import zipfile
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", "v1.0.0-alpha.26",
                                  "gpos/tools/unity/live_bridge/com.gpos.live-bridge"], capture_output=True,
                                 check=True).stdout
        extract = self.p.parent / "frozen-1.6.0"
        zipfile.ZipFile(io.BytesIO(archive)).extractall(extract)
        shutil.copytree(extract / "gpos/tools/unity/live_bridge/com.gpos.live-bridge", target)
        history = bi.history()["1.6.0"]
        self.assertEqual(bi.differences(target, history), [])          # byte for byte the frozen 1.6.0 bridge
        self.assertEqual(bi.inspect_target(self.p / "Game", bi.verify_source())[0], bi.PREVIOUS_STATE)
        self.assertEqual(bi.installed_version(self.p / "Game"), "1.6.0")
        r = self.run_cap(live.INSTALL)
        data = self.ok(r, "LIVE_BRIDGE_UPGRADED")
        self.assertEqual(data["upgraded_from"], "1.6.0")
        self.assertEqual(bi.inspect_target(self.p / "Game", bi.verify_source())[0], bi.EXACT)
        self.assertIsNone(bi.read_record(self.p, ident.project_key(ident.relative(self.p, self.p / "Game"))))
        self.editor = LabEditor(self.p, self.p / "Game")
        b = self.editor.launch()
        try:
            manifest = bi.verify_source()
            self.assertEqual((b["bridge_version"], b["protocol"], b["package_digest"], b["editor_version"]),
                             ("1.7.0", "gpos.unity.live/5", manifest["package_digest"], EDITOR_VERSION))
            locked = self.run_cap(live.INSTALL)                          # an open project is never written
            self.ok(locked, "ENGINE_PROJECT_LOCKED", None)
            self.assertFalse(locked.mutation_performed)
        finally:
            outcome = self.editor.quit()
        self.assertEqual(outcome.exit_code, 0)
        self.assertTrue(outcome.tree_contained)
        self.assertEqual((self.editor.bridge() or {}).get("state"), "CLOSED")


# ---------------------------------------------------------------- WLR2  the exact creation-time gate

SESSION_PROJECT = {}


def session_project():
    if "p" not in SESSION_PROJECT:
        p = real_project("session", demo=False, testkit=True)
        result = real_request(p, live.INSTALL)
        assert result.status == tdg.SUCCESS, text(result)
        SESSION_PROJECT["p"] = p
        SESSION_PROJECT["editor"] = LabEditor(p, p / "Game")
    return SESSION_PROJECT["p"], SESSION_PROJECT["editor"]


@real
class WLR2_ExactCreationTime(RealCase):
    """editor_started_utc is Process.GetCurrentProcess().StartTime: the OS creation time of the Editor process. It must
    equal GetProcessTimes on the same process handle to the 100 ns tick, over several Editor starts."""

    def test_editor_started_utc_is_getprocesstimes_exactly_over_three_starts(self):
        self.p, self.editor = session_project()
        seen = []
        for _ in range(3):
            b = self.editor.launch()
            m = _STATE["r2"][-1]
            self.assertEqual(m["pid"], b["editor_pid"])
            self.assertEqual(m["published"], m["created"])
            self.assertTrue(m["running"])
            self.assertTrue(pl._same_file(m["image"], EDITOR))
            self.assertEqual(ls.windows_identity(b["editor_pid"], b["editor_started_utc"], EDITOR_VERSION,
                                                 str(self.p / "Game")), ls.ALIVE)
            seen.append(m)
            outcome = self.editor.quit()
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(ls.windows_identity(b["editor_pid"], b["editor_started_utc"], EDITOR_VERSION,
                                                 str(self.p / "Game")), ls.GONE)
        self.assertEqual(len({m["pid"] for m in seen}), 3)
        _STATE["r2_passed"] = True


# ---------------------------------------------------------------- WLR3  the Scene-authoring demonstration

@real
class WLR3_SceneAuthoring(RealCase):
    """Install, identity, attach (synthetic approval), inspect the existing saved Scene, create an EMPTY GposDemoRoot,
    set its Transform, save, reinspect, detach, close; then the batch plane and the file prove persistence."""
    s = {}

    @classmethod
    def setUpClass(cls):
        need_r2(cls)
        cls.p = real_project("demo", demo=True, testkit=True)
        r = real_request(cls.p, live.INSTALL)
        assert r.status == tdg.SUCCESS, text(r)
        cls.editor = LabEditor(cls.p, cls.p / "Game")

    @classmethod
    def tearDownClass(cls):
        if cls.editor:
            cls.editor.stop()

    def test_01_the_bridge_starts_with_its_proven_identity_and_the_fixture_saves_the_scene(self):
        b = self.editor.launch()
        self.assertEqual((b["bridge_version"], b["protocol"]), (bi.BRIDGE_VERSION, bi.PROTOCOL))
        self.assertTrue(ident.same_directory(b["project_path"], self.p / "Game"))
        self.assertEqual(b["gpos_root"], str(self.p.resolve()))          # the spelling the lease is found by
        made = self.editor.human(op="demo-scene")                       # the disposable fixture, not GPOS
        self.assertEqual((made["scene"], made["roots"]), (fixtures.DEMO_SCENE, 0))
        scene = self.p / "Game" / fixtures.DEMO_SCENE
        self.assertTrue(scene.is_file())
        self.s["before"] = hashlib.sha256(scene.read_bytes()).hexdigest()

    def test_02_attach_through_the_approved_workflow(self):
        sid = self.attach()
        self.s["sid"] = sid
        holder = self.holder()
        self.assertEqual((holder["scope"], holder["session"]["session_id"]), ("SESSION", sid))
        self.assertEqual(self.status()["session"]["classification"], ls.LIVE)
        self.assertEqual(self.editor.bridge()["session_id"], sid)

    def test_03_inspect_the_editor_and_the_existing_scene(self):
        self.ok(self.run_cap(live.INSPECT, session_id=self.s["sid"]))
        data = self.ok(self.run_cap(au.INSPECT_OBJECT, session_id=self.s["sid"], scene=fixtures.DEMO_SCENE))
        self.assertEqual((data["scene"]["path"], data["root_count"]), (fixtures.DEMO_SCENE, 0))
        self.s["roots"] = data["tokens"]["scene_roots"]

    def test_04_create_an_empty_gposdemoroot(self):
        data = self.ok(self.run_cap(au.CREATE, session_id=self.s["sid"], scene=fixtures.DEMO_SCENE,
                                    name=fixtures.DEMO_NAME, expected_scene_roots_token=self.s["roots"]))
        oid = data["created"]["id"]
        self.s["id"] = oid
        d = self.ok(self.run_cap(au.INSPECT_OBJECT, session_id=self.s["sid"], object=oid))
        self.assertEqual(d["object"]["name"], fixtures.DEMO_NAME)
        self.assertEqual(len(d["components"]), 1, d["components"])       # only its Transform: an EMPTY GameObject
        self.assertIn("Transform", json.dumps(d["components"][0]))
        self.s["transform_token"] = d["tokens"]["transform"]

    def test_05_set_its_transform(self):
        self.ok(self.run_cap(au.SET_TRANSFORM, session_id=self.s["sid"], object=self.s["id"],
                             local_position=list(fixtures.DEMO_POSITION), local_rotation=list(fixtures.DEMO_ROTATION),
                             local_scale=list(fixtures.DEMO_SCALE), expected_transform_token=self.s["transform_token"]))
        self.assertTransform(self.ok(self.run_cap(au.INSPECT_OBJECT, session_id=self.s["sid"], object=self.s["id"])))

    def assertTransform(self, d):
        tr = d["transform"]
        for got, want in ((tr["local_position"], fixtures.DEMO_POSITION), (tr["local_scale"], fixtures.DEMO_SCALE),
                          (tr["local_rotation"], fixtures.DEMO_ROTATION)):
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=5)

    def test_06_save_the_existing_scene(self):
        self.ok(self.run_cap(au.SAVE_SCENE, session_id=self.s["sid"], scene=fixtures.DEMO_SCENE))
        scene = (self.p / "Game" / fixtures.DEMO_SCENE).read_bytes()
        self.assertNotEqual(hashlib.sha256(scene).hexdigest(), self.s["before"])
        self.assertIn(b"m_Name: GposDemoRoot", scene)

    def test_07_reinspect_after_the_save(self):
        d = self.ok(self.run_cap(au.INSPECT_OBJECT, session_id=self.s["sid"], object=self.s["id"]))
        self.assertEqual(d["object"]["name"], fixtures.DEMO_NAME)
        self.assertTransform(d)
        data = self.ok(self.run_cap(au.INSPECT_OBJECT, session_id=self.s["sid"], scene=fixtures.DEMO_SCENE))
        self.assertEqual((data["root_count"], data["scene"]["dirty"]), (1, False))

    def test_08_detach_releases_the_lease(self):
        self.detach(self.s["sid"])
        self.assertIsNone(self.holder())
        self.assertEqual(self.editor.bridge()["session_id"], "")
        self.assertIsNone(self.status()["session"])

    def test_09_close_the_test_editor(self):
        b = self.editor.bridge()
        outcome = self.editor.quit()
        self.assertEqual(outcome.exit_code, 0)
        self.assertTrue(outcome.tree_contained and outcome.capture_complete)
        self.assertEqual(self.editor.bridge()["state"], "CLOSED")
        self.assertEqual(ls.windows_identity(b["editor_pid"], b["editor_started_utc"], EDITOR_VERSION,
                                             str(self.p / "Game")), ls.GONE)
        self.assertEqual(_STATE["tuw"].editors_naming(self.p), [])

    def test_10_the_batch_plane_proves_persistence_with_an_editmode_test(self):
        shutil.rmtree(self.p / "Game" / "Packages" / TESTKIT.name)       # the project as its owner keeps it
        r = real_request(self.p, ua.EDITMODE, timeout=900)
        self.ok(r)
        self.assertEqual((r.data["total"], r.data["passed"], r.data["failed"]), (1, 1, 0), r.data)

    def test_11_the_saved_file_holds_exactly_the_empty_gposdemoroot(self):
        """Independent of Unity: the Scene YAML has one GameObject named GposDemoRoot with one component, a
        Transform with the set values, and nothing else at the root."""
        text_ = (self.p / "Game" / fixtures.DEMO_SCENE).read_text(encoding="utf-8")
        docs = re.split(r"^--- !u!(\d+) &(\d+)[^\n]*\n", text_, flags=re.M)
        objects = {docs[i + 1]: (docs[i], docs[i + 2]) for i in range(1, len(docs) - 2, 3)}
        game_objects = [(fid, body) for fid, (cls, body) in objects.items() if cls == "1"]
        self.assertEqual(len(game_objects), 1, "exactly one GameObject in the Scene")
        fid, body = game_objects[0]
        self.assertIn("m_Name: GposDemoRoot", body)
        components = re.findall(r"- component: \{fileID: (\d+)\}", body)
        self.assertEqual(len(components), 1, "an empty GameObject: one component")
        cls, transform = objects[components[0]]
        self.assertEqual(cls, "4", "the component is its Transform")
        def vec(name):
            m = re.search(name + r": \{x: ([-\d.e]+), y: ([-\d.e]+), z: ([-\d.e]+)(?:, w: ([-\d.e]+))?\}", transform)
            return [float(v) for v in m.groups() if v is not None]
        for got, want in ((vec("m_LocalPosition"), fixtures.DEMO_POSITION), (vec("m_LocalScale"), fixtures.DEMO_SCALE),
                          (vec("m_LocalRotation"), fixtures.DEMO_ROTATION)):
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=5)
        self.assertIn("m_Father: {fileID: 0}", transform)


# ---------------------------------------------------------------- WLR4  SESSION integrity at each boundary

class Held:
    """A reader holding a file without FILE_SHARE_DELETE: what a scanner can do. TEST ONLY."""

    def __init__(self, path):
        self.h = handle(path, GENERIC_READ, SHARE_RW)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        _k32.CloseHandle(self.h)


@real
class WLR4_SessionIntegrity(RealCase):
    @classmethod
    def setUpClass(cls):
        need_r2(cls)
        cls.p, cls.editor = session_project()
        cls.editor.launch()

    @classmethod
    def tearDownClass(cls):
        cls.editor.stop()

    def test_01_a_bind_whose_session_file_cannot_be_published_is_undone_and_releases_the_lease(self):
        self.ensure_session_file()
        with Held(self.editor.live / "session.json"):
            r = self.run_cap(live.ATTACH, timeout=120)
        self.assertNotEqual(r.status, tdg.SUCCESS)
        self.assertIn("LIVE_PROTOCOL_ERROR", codes(r), text(r))
        details = json.loads(next(d for d in r.diagnostics if d.code == "LIVE_PROTOCOL_ERROR").details)
        self.assertEqual(details["bridge_code"], "BRIDGE_INTERNAL_ERROR")
        self.assertIsNone(self.holder())                                # known unbound: released, nothing ambiguous
        self.assertEqual(self.editor.bridge()["session_id"], "")
        undone = re.findall(r"bind-undone:([0-9a-f]{32})", self.events())
        self.assertTrue(undone)
        self.assertEditorUnbound(undone[-1], lease_mod.session_owner(APPROVER))

    def assertEditorUnbound(self, sid, owner):
        """The Editor itself holds no binding: a fresh heartbeat (published from the Editor's session state, not from
        session.json or bridge.json) names no session, and a session command with `sid` is SESSION_NOT_BOUND."""
        seq = self.editor.heartbeat().get("seq")
        end = time.monotonic() + 10
        while time.monotonic() < end and self.editor.heartbeat().get("seq") == seq:
            time.sleep(0.1)
        self.assertEqual(self.editor.heartbeat().get("session_id"), "")
        r = self.raw("inspect", {}, owner, sid)
        self.assertEqual((r.status, r.code), ("REFUSED", "SESSION_NOT_BOUND"))

    def ensure_session_file(self):
        """session.json exists once any session was bound (and so can be held); bind and unbind one if needed."""
        if not (self.editor.live / "session.json").exists():
            self.detach(self.attach())

    def test_02_a_consumed_approval_can_never_bind_twice(self):
        self.ensure_session_file()
        owner = "AGENT:testkit-approve-2"
        proposal, sid = uuid.uuid4().hex, uuid.uuid4().hex
        b = self.editor.bridge()
        key = ident.project_key(ident.relative(self.p, self.p / "Game"))
        r = self.raw("propose-attach", {"proposal_id": proposal, "session_id": sid, "project_key": key,
                                        "expires_s": 120}, owner)
        self.assertEqual(r.status, "OK", r.response)
        end = time.monotonic() + 60
        while time.monotonic() < end:
            s = self.raw("attach-status", {"proposal_id": proposal}, owner)
            if (s.response or {}).get("data", {}).get("state") == "APPROVED":
                break
            time.sleep(0.3)
        lease = lease_mod.acquire(self.p, "unity", self.resource(), owner, ipc.utc(), uuid.uuid4().hex,
                                  scope=lease_mod.SESSION,
                                  session={"session_id": sid, "proposal_id": proposal, "boot_id": b["boot_id"],
                                           "project_key": key, "editor_pid": b["editor_pid"],
                                           "editor_started_utc": b["editor_started_utc"]})
        try:
            with Held(self.editor.live / "session.json"):
                first = self.raw("bind", {"proposal_id": proposal, "session_id": sid}, owner, wait=30)
            self.assertEqual((first.status, first.code), ("FAILED", "BRIDGE_INTERNAL_ERROR"))
            self.assertEqual(self.editor.bridge()["session_id"], "")
            self.assertEditorUnbound(sid, owner)
            again = self.raw("bind", {"proposal_id": proposal, "session_id": sid}, owner, wait=30)
            self.assertEqual((again.status, again.code), ("REFUSED", "GRANT_CONSUMED"))
            self.assertEditorUnbound(sid, owner)
        finally:
            lease_mod.release(self.p, lease)

    def test_03_a_bound_session_whose_answer_is_lost_keeps_its_lease_and_is_live(self):
        original, saved_wait = ipc._windows_publish, live.COMMAND_WAIT

        def publish(channel, rid, body):
            if b'"command": "bind"' in body:
                (channel.responses / f"{rid}.json").mkdir()            # the answer can never be published or read
            return original(channel, rid, body)
        ipc._windows_publish, live.COMMAND_WAIT = publish, 6.0
        try:
            r = self.run_cap(live.ATTACH, timeout=120)
        finally:
            ipc._windows_publish, live.COMMAND_WAIT = original, saved_wait
        self.assertIn("LIVE_OUTCOME_UNKNOWN", codes(r), text(r))
        sid = r.data["session_id"]
        self.assertEqual(self.holder()["session"]["session_id"], sid)   # never released on an unknown outcome
        self.assertEqual(self.editor.bridge()["session_id"], sid)       # the Editor is bound
        self.assertEqual(self.status()["session"]["classification"], ls.LIVE)
        self.detach(sid)
        self.assertIsNone(self.holder())
        for d in self.editor.live.joinpath("responses").iterdir():
            if d.is_dir():
                d.rmdir()

    def test_04_a_bind_the_editor_never_claimed_is_withdrawn_and_the_lease_released(self):
        original, saved_wait = ipc._windows_publish, live.COMMAND_WAIT
        seen = {}

        def publish(channel, rid, body):
            if b'"command": "bind"' in body:
                self.editor.trigger("block-15000")                        # the Editor stalls (a long import, say)
                beat = self.editor.heartbeat().get("seq")
                end = time.monotonic() + 20
                while time.monotonic() < end:
                    time.sleep(1.5)
                    now = self.editor.heartbeat().get("seq")
                    if now == beat:
                        break
                    beat = now
                seen["rid"] = rid
            return original(channel, rid, body)
        ipc._windows_publish, live.COMMAND_WAIT = publish, 4.0
        try:
            r = self.run_cap(live.ATTACH, timeout=120)
        finally:
            ipc._windows_publish, live.COMMAND_WAIT = original, saved_wait
        self.assertIn("LIVE_REQUEST_WITHDRAWN", codes(r), text(r))
        self.assertIsNone(self.holder())
        self.assertTrue((self.editor.live / "withdrawn" / f"{seen['rid']}.json").exists())
        end = time.monotonic() + 60
        time.sleep(16)                                                  # the stall is over
        self.assertNotIn(f"claimed:{seen['rid']}", self.events())       # never claimed, never executed
        self.assertEqual(self.editor.bridge()["session_id"], "")

    def test_05_status_tells_unknown_from_known_unbound_and_recovery_needs_approval(self):
        b = self.editor.bridge()
        key = ident.project_key(ident.relative(self.p, self.p / "Game"))
        owner = lease_mod.session_owner(APPROVER)
        sid = uuid.uuid4().hex
        lease_mod.acquire(self.p, "unity", self.resource(), owner, ipc.utc(), uuid.uuid4().hex, scope=lease_mod.SESSION,
                          session={"session_id": sid, "proposal_id": uuid.uuid4().hex, "boot_id": b["boot_id"],
                                   "project_key": key, "editor_pid": b["editor_pid"],
                                   "editor_started_utc": b["editor_started_utc"]})
        s = self.status()["session"]
        self.assertEqual((s["classification"], s["reasons"]), (ls.UNRESPONSIVE, ["the session is being bound"]))
        saved = ls.BINDING_GRACE_SECONDS
        ls.BINDING_GRACE_SECONDS = 0.5
        try:
            time.sleep(1.0)
            s = self.status()["session"]
            self.assertEqual(s["classification"], ls.STALE)
            self.assertIn("no longer bound", " ".join(s["reasons"]))
            self.detach(sid, actor=REJECTER, code="LIVE_APPROVAL_REJECTED", status=None)
            self.assertIsNotNone(self.holder())                         # a rejected recovery breaks nothing
            self.detach(sid, actor=APPROVER, code="LIVE_SESSION_RECOVERED")
        finally:
            ls.BINDING_GRACE_SECONDS = saved
        self.assertIsNone(self.holder())

    def test_06_wrong_owner_session_and_boot_are_refused(self):
        sid = self.attach()
        try:
            other = self.run_cap(live.INSPECT, actor=OTHER, session_id=sid)
            self.assertNotEqual(other.status, tdg.SUCCESS)
            self.assertTrue(codes(other) & {"LIVE_SESSION_MISMATCH", "LEASE_CONFLICT", "LIVE_SESSION_HELD"},
                            text(other))
            owner = lease_mod.session_owner(APPROVER)
            self.assertEqual(self.raw("inspect", {}, owner, uuid.uuid4().hex).code, "SESSION_MISMATCH")
            self.assertEqual(self.raw("inspect", {}, "AGENT:codex-worker", sid).code, "OWNER_MISMATCH")
            self.assertEqual(self.raw("inspect", {}, owner, sid, boot="0" * 32).code, "BOOT_MISMATCH")
        finally:
            self.detach(sid)


# ---------------------------------------------------------------- WLR5  approval, allowlist, recovery

@real
class WLR5_ApprovalAndRecovery(RealCase):
    @classmethod
    def setUpClass(cls):
        need_r2(cls)
        cls.p, cls.editor = session_project()
        cls.editor.launch()

    @classmethod
    def tearDownClass(cls):
        cls.editor.stop()

    def test_01_a_rejected_attach_takes_no_lease(self):
        r = self.run_cap(live.ATTACH, actor=REJECTER, timeout=120)
        self.assertIn("LIVE_APPROVAL_REJECTED", codes(r), text(r))
        self.assertIsNone(self.holder())

    def test_02_an_attach_nobody_approves_expires(self):
        r = self.run_cap(live.ATTACH, actor=UNAPPROVED, timeout=30)
        self.assertIn("LIVE_APPROVAL_NOT_GRANTED", codes(r), text(r))
        self.assertIsNone(self.holder())

    def test_03_the_bridge_serves_nothing_outside_the_windows_allowlist(self):
        owner = lease_mod.session_owner(APPROVER)
        for command in ("enter-playmode", "pause", "add-component", "set-property", "delete-gameobject",
                        "create-material", "create-prefab", "sync-sources", "compilation-status"):
            with self.subTest(command=command):
                r = self.raw(command, {}, owner, "s" * 32)
                self.assertEqual((r.status, r.code), ("REFUSED", "UNKNOWN_COMMAND"))
        # through GPOS, even with an attached session, an unqualified capability never reaches the Editor
        sid = self.attach()
        try:
            for cap, inputs in ((au.ADD_COMPONENT, dict(object="x", type_id="x", expected_object_token="0" * 32,
                                                        expected_catalog_digest="0" * 64)),
                                (live.ENTER, {})):
                with self.subTest(capability=cap):
                    refused = self.run_cap(cap, session_id=sid, **inputs)
                    self.assertIn("PLATFORM_UNSUPPORTED", codes(refused), text(refused))
                    self.assertFalse(refused.mutation_performed)
        finally:
            self.detach(sid)

    def test_04_a_clean_close_releases_without_recovery(self):
        sid = self.attach()
        self.editor.quit()
        self.assertEqual(self.status()["session"]["classification"], ls.CLOSED)
        data = self.detach(sid)
        self.assertTrue(data["released"])
        self.assertIsNone(self.holder())
        self.editor.launch()

    def test_05_a_crashed_editor_leaves_a_stale_session_only_a_human_approved_recovery_ends(self):
        sid = self.attach()
        self.editor.end("crash")
        self.assertEqual(self.status()["session"]["classification"], ls.STALE)
        self.detach(sid, actor=REJECTER, code="LIVE_SESSION_STALE", status=None)   # no running bridge to ask
        self.editor.launch()
        r = self.run_cap(live.DETACH, actor=REJECTER, session_id=sid, timeout=120)
        self.assertIn("LIVE_APPROVAL_REJECTED", codes(r), text(r))
        self.assertEqual(self.holder()["session"]["session_id"], sid)   # nothing was broken
        self.detach(sid, actor=APPROVER, code="LIVE_SESSION_RECOVERED")
        self.assertIsNone(self.holder())
        sid = self.attach()                                             # the project is usable again
        self.detach(sid)


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    print("GPOS Windows live bridge tests (bridge 1.7.0, protocol gpos.unity.live/5; approval in the real groups is "
          "synthetic)")
    sys.exit(0 if result.wasSuccessful() else 1)
