"""TEST ONLY (alpha.22): stand-ins for the player adapter's macOS backends and for the helper's three modes.

FakeOS / FakeAppKit replace libproc, csops, CoreGraphics and AppKit with a process table. FakeSupervisor replaces the
foundation's detached spawn: it plays the helper's supervise mode in a thread, speaking exactly the runtime-file
protocol (handshake, commit/abort, stop intents, exit record). FakeHelper replaces the process boundary for the one
LaunchServices invocation and plays shot/video: it writes the helper result and the media. Builders make a valid GPOS
project, an alpha.21-shaped completed build and synthetic PNG/MP4 files.
"""

import json
import os
import shutil
import struct
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools.player import contract as c  # noqa: E402
from gpos.tools.player import helper as hp  # noqa: E402
from gpos.tools.player import macos  # noqa: E402
from gpos.tools.player import runtime as rt  # noqa: E402
from gpos.tools.unity import build as ub  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
CDHASH = hp.cdhashes()["arm64"]
APP_ID = "com.gpos.test.fakegame"
REV = "0123456789abcdef0123456789abcdef01234567"


# ---------------------------------------------------------------- processes and AppKit

class FakeOS:
    def __init__(self):
        self.procs, self.killed, self.next_pid, self.lock = {}, [], 41000, threading.Lock()
        self.windows_for = {}

    def add(self, executable, app_id=None, ppid=1, cdhash=None, windows=1, on_terminate="exit", code=0, pgid=None):
        with self.lock:
            self.next_pid += 1
            pid = self.next_pid
            self.procs[pid] = {"pid": pid, "ppid": ppid, "pgid": pgid or pid, "uid": os.getuid(),
                               "start_sec": 1_700_000_000 + pid, "start_usec": pid % 1000, "executable": executable,
                               "app_id": app_id, "cdhash": cdhash, "windows": windows, "on_terminate": on_terminate,
                               "code": code, "terminated": False}
        return pid

    def remove(self, pid):
        with self.lock:
            return self.procs.pop(pid, None)

    def facts(self, pid):
        p = self.procs.get(pid)
        return None if p is None else {k: p[k] for k in ("pid", "ppid", "pgid", "uid", "start_sec", "start_usec",
                                                          "executable")}

    def identity(self, pid, start_sec, start_usec, executable):
        p = self.procs.get(pid)
        if p is None:
            return c.GONE
        if (p["start_sec"], p["start_usec"]) != (start_sec, start_usec):
            return c.NOT_THIS_PROCESS
        return c.PROVEN if p["executable"] == executable else c.UNPROVEN

    def cdhash(self, pid):
        p = self.procs.get(pid)
        return p and p["cdhash"]

    def file_identity(self, path):
        return macos.file_identity(path)

    def same_application(self, application_id):
        return [self.facts(p) for p in sorted(self.procs) if self.procs[p]["app_id"] == application_id]

    def eligible_windows(self, pid):
        p = self.procs.get(pid)
        return [] if p is None else [{"layer": 0, "alpha": 1.0, "on_screen": True, "width": 480.0, "height": 298.0}] \
            * p["windows"]

    def kill_proven(self, pid, start_sec, start_usec, executable):
        if self.identity(pid, start_sec, start_usec, executable) != c.PROVEN:
            return False
        self.remove(pid)
        self.killed.append(pid)
        return True


class FakeAppKit:
    def __init__(self, fake_os, registered=True):
        self.os, self.registered, self.calls = fake_os, registered, []

    def running_pids(self, application_id):
        if not self.registered:
            return []
        return sorted(p for p, v in self.os.procs.items() if v["app_id"] == application_id)

    def request_terminate(self, pid, application_id, executable):
        self.calls.append((pid, application_id, executable))
        p = self.os.procs.get(pid)
        if p is None:
            return False, False
        if p["app_id"] != application_id or p["executable"] != executable:
            return True, False
        if p["on_terminate"] == "exit":
            self.os.remove(pid)
        return True, True


# ---------------------------------------------------------------- the supervise mode, played

class FakeSupervisor:
    """Replaces gpos.tools.process.spawn_detached. scenario: ok, no_handshake, bad_nonce, wrong_player_exe, not_child,
    other_supervisor, wrong_cdhash, player_exits, supervisor_dies, ignore_abort, player_hangs, drift, exit_code=<n>."""

    def __init__(self, fake_os, scenario="ok", foreign_after_handshake=False):
        self.os, self.scenario, self.foreign = fake_os, scenario, foreign_after_handshake
        self.specs, self.threads, self.player, self.supervisor, self.stop = [], [], None, None, threading.Event()

    def __call__(self, spec, scopes):
        assert isinstance(spec, tproc.DetachedProcessSpec)
        tproc.validate_spec(tproc.ToolProcessSpec(spec.executable, tuple(spec.argv), spec.cwd, env=spec.env), scopes)
        self.specs.append(spec)
        assert spec.argv[0] == "supervise" and len(spec.argv) == 2
        request_path = Path(spec.argv[1])
        req = json.loads(request_path.read_text())
        directory = request_path.parent
        exe = spec.executable if self.scenario != "other_supervisor" else spec.executable + "-other"
        sup = self.os.add(exe, cdhash="0" * 40 if self.scenario == "wrong_cdhash" else CDHASH, windows=0)
        self.supervisor = sup
        hang = "hang" if self.scenario == "player_hangs" else "exit"
        player = self.os.add(req["executable"] + ("-x" if self.scenario == "wrong_player_exe" else ""),
                             app_id=req["application_id"], ppid=1 if self.scenario == "not_child" else sup,
                             on_terminate=hang)
        self.player = player
        if self.scenario == "supervisor_dies":
            self.os.remove(sup)
            return tproc.DetachedHandle(sup)
        s, p = self.os.procs[sup], self.os.procs[player]
        handshake = {"schema": "gpos.player.handshake/1", "session_id": req["session_id"],
                     "nonce": req["nonce"] if self.scenario != "bad_nonce" else "f" * 32,
                     "spawned_at": "2026-10-04T00:00:00.000Z",
                     "supervisor": {"pid": sup, "start_sec": s["start_sec"], "start_usec": s["start_usec"],
                                    "executable": s["executable"], "cdhash": s["cdhash"], "version": "1.0.0"},
                     "player": {"pid": player, "ppid": p["ppid"], "pgid": p["pgid"], "start_sec": p["start_sec"],
                                "start_usec": p["start_usec"], "executable": p["executable"]}}
        if self.scenario == "drift":   # the build changes between the spawn and the launch's verification
            with open(req["executable"], "a") as fh:
                fh.write("# drifted\n")
        if self.scenario == "player_exits":
            self.os.remove(player)
            self._exit(directory, req, p, "EXITED", 1, None, None, False, False, False)
        if self.scenario != "no_handshake":
            rt.write_once(directory / rt.HANDSHAKE, handshake)
        if self.foreign:
            self.os.add("/Applications/Other Copy.app/Contents/MacOS/Game", app_id=req["application_id"])
        t = threading.Thread(target=self._loop, args=(directory, req, sup, player), daemon=True)
        t.start()
        self.threads.append(t)
        return tproc.DetachedHandle(sup)

    def _exit(self, directory, req, p, status, code, signal, reason, committed, accepted, killed, stop_rid=None):
        rec = {"schema": "gpos.player.exit/1", "session_id": req["session_id"], "nonce": req["nonce"],
               "committed": committed, "player": {"pid": p["pid"], "start_sec": p["start_sec"],
                                                  "start_usec": p["start_usec"]},
               "status": status, "code": code, "signal": signal, "spawn_errno": None,
               "stop": {"reason": reason, "stop_request_id": stop_rid, "terminate_requested_at": None if reason is None
                        else "2026-10-04T00:00:01.000Z", "terminate_accepted": accepted, "kill_sent": killed},
               "observed_at": "2026-10-04T00:00:02.000Z"}
        try:
            rt.write_once(directory / rt.EXIT, rec)
        except FileExistsError:
            pass

    def _loop(self, directory, req, sup, player):
        p = dict(self.os.procs.get(player) or {"pid": player, "start_sec": 0, "start_usec": 0})
        committed, started = False, time.monotonic()
        while not self.stop.is_set():
            if sup not in self.os.procs:
                return   # the supervisor was killed: nothing observes the Player any more
            if player not in self.os.procs:
                if not rt.exists(directory / rt.EXIT):
                    self._exit(directory, req, p, "EXITED", self.os_code(), None, None, committed, False, False)
                self.os.remove(sup)
                return
            reason, stop_rid = None, None
            if not committed:
                if rt.exists(directory / rt.COMMIT):
                    committed = True
                elif rt.exists(directory / rt.ABORT) and self.scenario != "ignore_abort":
                    reason = "ABORT"
                elif time.monotonic() - started > req["commit_deadline_s"]:
                    reason = "DEADLINE"
            if committed:
                intents = rt.stop_records(directory, "stop-intent")
                if intents:
                    reason, stop_rid = "STOP_INTENT", intents[0][len("stop-intent-"):-len(".json")]
            if reason:
                hang = self.os.procs.get(player, {}).get("on_terminate") == "hang"
                self.os.remove(player)
                if hang:
                    self._exit(directory, req, p, "SIGNALED", None, 9, reason, committed, True, True, stop_rid)
                else:
                    self._exit(directory, req, p, "EXITED", self.os_code(), None, reason, committed, True, False,
                               stop_rid)
                self.os.remove(sup)
                return
            time.sleep(0.02)

    def os_code(self):
        return int(self.scenario.split("=")[1]) if self.scenario.startswith("exit_code=") else 0


# ---------------------------------------------------------------- the shot/video modes, played

def png_bytes(w, h, colour=(20, 40, 90, 255)):
    row = b"\x00" + bytes(colour) * w
    raw = zlib.compress(row * h)
    chunk = lambda kind, body: struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"iDOT", b"\x00" * 28) + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def _box(kind, body):
    return struct.pack(">I", 8 + len(body)) + kind + body


def mp4_bytes(seconds, w, h, samples, audio=False, moov=True, codec=b"avc1"):
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")
    mvhd = _box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 600, int(seconds * 600)) + b"\x00" * 80)

    def trak(handler, tw, th, entry):
        tkhd = _box(b"tkhd", b"\x00\x00\x00\x03" + b"\x00" * 72 + struct.pack(">II", tw << 16, th << 16))
        hdlr = _box(b"hdlr", b"\x00" * 8 + handler + b"\x00" * 13)
        stsd = _box(b"stsd", b"\x00" * 4 + struct.pack(">I", 1) + _box(entry, b"\x00" * 70))
        stsz = _box(b"stsz", b"\x00" * 8 + struct.pack(">I", samples))
        stbl = _box(b"stbl", stsd + stsz)
        return _box(b"trak", tkhd + _box(b"mdia", hdlr + _box(b"minf", stbl)))

    traks = trak(b"vide", w, h, codec) + (trak(b"soun", 0, 0, b"mp4a") if audio else b"")
    body = ftyp + (_box(b"moov", mvhd + traks) if moov else b"") + _box(b"mdat", b"\x00" * 64)
    return body


class FakeHelper:
    """Replaces gpos.tools.process.run_process for the one /usr/bin/open invocation. scenario: captured, permission,
    window_not_found, window_changed, stream_stopped, no_result, timeout, wrong_nonce, wrong_cdhash, wrong_version,
    permission_after_sck, bad_media, audio_track, unfinished, size_mismatch, foreign_window."""

    def __init__(self, fake_os, scenario="captured", width=960, height=596):
        self.os, self.scenario, self.width, self.height, self.specs = fake_os, scenario, width, height, []

    def __call__(self, spec, scopes, clock=time.monotonic):
        tproc.validate_spec(spec, scopes)
        self.specs.append(spec)
        assert spec.executable == "/usr/bin/open"
        request_path = Path(spec.argv[-1])
        req = json.loads(request_path.read_text())
        ws = request_path.parent
        bundle = Path(spec.argv[6])
        if self.scenario == "timeout":
            return tproc.ProcessOutcome(exit_code=None, timed_out=True, terminated=True)
        if self.scenario != "no_result":
            self._answer(ws, req, bundle)
        return tproc.ProcessOutcome(exit_code=0)

    def _answer(self, ws, req, bundle):
        s = self.scenario
        video = req["mode"] == "VIDEO"
        helper = {"version": "0.9.9" if s == "wrong_version" else "1.0.0", "pid": 50001, "start_sec": 1,
                  "start_usec": 2, "executable": str(hp.executable(bundle)), "bundle_path": str(bundle),
                  "cdhash": "1" * 40 if s == "wrong_cdhash" else CDHASH}
        result = {"schema": "gpos.player.helper-result/1", "mode": req["mode"], "request_id": req["request_id"],
                  "request_nonce": "e" * 32 if s == "wrong_nonce" else req["request_nonce"],
                  "session_id": req["session_id"], "sck_called": s not in ("permission",), "helper": helper,
                  "window": None, "media": None, "identity": {"before": s != "permission", "after": False},
                  "timing": {"started_at": "t0", "finished_at": "t1"}, "outcome": "FAILED", "rule": None}
        rules = {"permission": ("REFUSED", "PERMISSION_NOT_GRANTED"), "permission_after_sck": ("REFUSED", "PERMISSION_NOT_GRANTED"),
                 "window_not_found": ("REFUSED", "WINDOW_NOT_FOUND"), "window_changed": ("FAILED", "WINDOW_CHANGED"),
                 "stream_stopped": ("FAILED", "STREAM_STOPPED")}
        if s in rules:
            result["outcome"], result["rule"] = rules[s]
            rt.write_once(ws / rt.HELPER_RESULT, result, bound=16384)
            return
        w, h = self.width, self.height
        result["window"] = {"owner_pid": req["target"]["pid"] + (1 if s == "foreign_window" else 0), "layer": 0,
                            "on_screen": True, "alpha_positive": True,
                            "candidates": 1, "points_w": w / 2, "points_h": h / 2, "scale": 2.0,
                            "same_after_settle": True}
        if video:
            d = req["duration_s"]
            data = mp4_bytes(d + (3 if s == "bad_media" else 0), w, h, d * 30, audio=s == "audio_track",
                             moov=s != "unfinished")
            (ws / "runtime-video.mp4").write_bytes(data)
            result["media"] = {"width": w, "height": h, "bytes": len(data), "format": "MP4", "codec": "avc1",
                               "frames": d * 30, "dropped": 0, "duration_s": float(d), "fps_cap": 30,
                               "video_tracks": 1, "audio_tracks": 0}
        else:
            data = png_bytes(w, h)
            if s == "bad_media":
                data = data[:-20]
            (ws / "runtime-screenshot.png").write_bytes(data)
            result["media"] = {"width": w + (2 if s == "size_mismatch" else 0), "height": h, "bytes": len(data),
                               "format": "PNG"}
        result.update(outcome="CAPTURED", identity={"before": True, "after": True})
        rt.write_once(ws / rt.HELPER_RESULT, result, bound=16384)


# ---------------------------------------------------------------- a project, a build and a helper install

def gpos_project(tmp, name="p"):
    target = Path(tmp) / name
    shutil.copytree(FIXTURE, target)
    return target.resolve()


def make_build(root, rid="req-0123456789abcdef", app_id=APP_ID, exe="FakeGame", revision=REV, guid="ab" * 16):
    """An alpha.21-shaped completed build (payload + manifest) that ub.revalidate accepts."""
    ws = Path(root) / ".game/gpos-runtime/tool-output/unity" / rid
    app = ws / ub.PAYLOAD / ub.APP
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources/Data").mkdir(parents=True)
    import plistlib
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": app_id, "CFBundleExecutable": exe,
                                                              "CFBundleShortVersionString": "1.0"}))
    (app / "Contents/MacOS" / exe).write_text("#!/bin/sh\nexit 0\n")
    (app / "Contents/MacOS" / exe).chmod(0o755)
    (app / "Contents/Resources/Data/boot.config").write_text(f"build-guid={guid}\n")
    tree = ub.payload_tree(app)
    manifest = {"schema": ub.MANIFEST_SCHEMA, "build_id": ub.build_id(rid), "request_id": rid, "target": ub.TARGET,
                "build_revision": revision, "build_revision_source": "CALLER_SUPPLIED", "unity_build": {"guid": guid},
                "payload": {"path": f"{ub.PAYLOAD}/{ub.APP}", "kind": "MACOS_APP_BUNDLE",
                            "tree_algorithm": tree["algorithm"], "tree_digest": tree["digest"],
                            "entries": tree["entries"], "bytes": tree["bytes"], "bundle_identifier": app_id,
                            "executable": exe, "bundle_version": "1.0"}}
    ub.write_manifest(ws, manifest)
    return ub.build_id(rid), str((app / "Contents/MacOS" / exe).resolve())


def install_helper(home):
    host = Path(home) / "Applications" / "GPOS"
    hp.install(host, "fixture-install")
    return host
