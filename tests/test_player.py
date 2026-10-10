#!/usr/bin/env python3
"""Phase 2C-8 (alpha.22) — the player runtime adapter, fast groups (no Unity, no Screen Recording).

    python3 tests/test_player.py

The real groups live in tests/test_player_helper.py (the native helper: release, supervise mode against stub payloads,
the atomic install on the real filesystem) and tests/test_player_real.py (a QualGame build: launch, status, stop,
foreign instances, crash matrix, capture, privacy, persistence and the FFprobe/FFmpeg composition).

Fast groups (FakeOS / FakeAppKit / FakeSupervisor / FakeHelper from tests/player_fakes.py, through the real foundation):
    A  descriptor, registry vocabulary, capability declarations and the permanent external-invocation table
    B  structural pins: only invocation.py starts a process; no other process mechanism; ctypes library allowlist;
       exactly three helper modes; no permission request anywhere; contract portability
    C  the helper release and classification (EXACT / ABSENT / UNTRUSTED)
    D  player.install-capture-helper: host location, no caller destination, atomic no-replace, races, dry run
    E  runtime files: write-once, links, strict schemas
    F  build resolution and revalidation
    G  player.launch: the sequence, every refusal, abandon and unresolved launches, invocation [SUPERVISE]
    H  player.status: zero processes, no writes, bound/unresolved/gone/drift/helper facts
    I  capture: request rules, the one LaunchServices invocation, helper identity before/after, permission, refusals,
       media validation, evidence, invocation [SHOT]/[VIDEO]
    J  player.stop: supervisor path, the in-process fallback, kill, unresolved launches, owners, recovery, classes,
       evidence rules, zero processes
    K  the player log sanitizer (the measured `Application Support` fragment)
    L  PNG and MP4 validators
    N  the network semantic
"""

import ast
import dataclasses
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import player_fakes as pf  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import leases as lease_mod  # noqa: E402
from gpos.tools import paths as tpaths  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools import validation as tval  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Actor, Subject  # noqa: E402
from gpos.tools.player import adapter as pa  # noqa: E402
from gpos.tools.player import capture as pcap  # noqa: E402
from gpos.tools.player import contract as c  # noqa: E402
from gpos.tools.player import helper as hp  # noqa: E402
from gpos.tools.player import invocation as pinv  # noqa: E402
from gpos.tools.player import logs as plogs  # noqa: E402
from gpos.tools.player import resolver as pres  # noqa: E402
from gpos.tools.player import runtime as prt  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.unity.sources import ABSOLUTE_PATH  # noqa: E402

FW = load_framework()
REG = FW.registry
PLAYER = ROOT / "gpos" / "tools" / "player"
AGENT = Actor("AGENT", "claude-main")
OTHER = Actor("AGENT", "codex-worker")
FAST_TIMING = dict(sleep=lambda s: __import__("time").sleep(min(s, 0.01)))


# ---------------------------------------------------------------- the base case

class PlayerCase(unittest.TestCase):
    supervisor_scenario = "ok"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-player-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        patcher = mock.patch.object(tpaths, "account_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host = pf.install_helper(self.home)
        self.os = pf.FakeOS()
        self.appkit = pf.FakeAppKit(self.os)
        self.project = pf.gpos_project(self.tmp)
        self.build_id, self.exe = pf.make_build(self.project)
        self.supervisor = pf.FakeSupervisor(self.os, self.supervisor_scenario)
        self.helper = pf.FakeHelper(self.os)
        self.addCleanup(self.supervisor.stop.set)

    def adapter(self):
        return pa.PlayerAdapter(os_backend=self.os, appkit=self.appkit, **FAST_TIMING)

    def registry(self):
        r = ToolRegistry(FW)
        r.register(self.adapter())
        return r

    def run_cap(self, cap, actor=AGENT, **kw):
        if cap not in (c.STATUS,) and not kw.get("dry_run"):
            kw.setdefault("allow_mutation", True)
        request = ExecutionRequest(adapter_id="player", capability_id=cap,
                                   subject=Subject("FEATURE", "FEATURE-0001", pf.REV), project_root=str(self.project),
                                   actor=actor, **kw)
        with mock.patch.object(tproc, "spawn_detached", self.supervisor), \
                mock.patch.object(tproc, "run_process", self.helper):
            return execute(self.registry(), request)

    def launch(self, **kw):
        return self.run_cap(c.LAUNCH, build_id=self.build_id, **kw)

    def launched(self):
        r = self.launch()
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        return r.data["session_id"], r

    def codes(self, r):
        return {d.code for d in r.diagnostics}

    def text(self, r):
        return " | ".join(f"{d.code}: {d.message}" for d in r.diagnostics)

    def runtime_dir(self, r):
        return self.project / ".game/gpos-runtime" / r.data["runtime_dir"]

    def lease_record(self):
        path = lease_mod.lease_path(self.project, "player", f"PLAYER_RUNTIME:{self.project}")
        return json.loads(path.read_text()) if path.exists() else None


# ---------------------------------------------------------------- A  declarations

class A_Declarations(unittest.TestCase):
    def test_descriptor(self):
        d = pa.DESCRIPTOR
        self.assertEqual((d.adapter_id, d.adapter_version, d.tool_family, d.adapter_kind, d.state_model, d.target_tool),
                         ("player", "1.0.0", "DEVICE", "DEVICE", "STATEFUL", "GPOS Player Helper"))
        self.assertEqual(d.supported_platforms, ("MACOS", "WINDOWS"))
        self.assertEqual(d.filesystem_scopes, ())
        self.assertEqual(tval.validate_descriptor(FW, d), [])

    def test_production_registry(self):
        self.assertEqual(default_registry(FW).adapter_ids(), ["adb", "blender", "ffmpeg", "ffprobe", "git", "player",
                                                              "unity"])

    def test_six_capabilities_exactly(self):
        caps = {cap.id: cap for cap in pa.CAPABILITIES}
        self.assertEqual(sorted(caps), sorted(c.CAPABILITY_IDS))
        expect = {
            c.INSTALL: ("DEPLOY", "MUTATING", "STATELESS", "OFFLINE_ANALYSIS", "NONE", False, False),
            c.LAUNCH: ("RUN", "MUTATING", "STATEFUL", "DIAGNOSTIC_RUNTIME", "SESSION_OPEN", True, True),
            c.STATUS: ("INSPECT", "READ_ONLY", "STATEFUL", "DIAGNOSTIC_RUNTIME", "SESSION_REQUIRED", False, False),
            c.SCREENSHOT: ("CAPTURE", "MUTATING", "STATEFUL", "DIAGNOSTIC_RUNTIME", "SESSION_REQUIRED", True, True),
            c.VIDEO: ("CAPTURE", "MUTATING", "STATEFUL", "DIAGNOSTIC_RUNTIME", "SESSION_REQUIRED", True, True),
            c.STOP: ("RUN", "MUTATING", "STATEFUL", "DIAGNOSTIC_RUNTIME", "SESSION_CLOSE", True, False),
        }
        for cid, (cat, op, state, ctx, lease, writer, tool) in expect.items():
            cap = caps[cid]
            with self.subTest(cap=cid):
                self.assertEqual((cap.category, cap.operation_class, cap.state_model, cap.execution_context,
                                  cap.effective_lease_mode, cap.single_writer_required, cap.requires_tool),
                                 (cat, op, state, ctx, lease, writer, tool))
                self.assertTrue(cap.requires_project)
                self.assertFalse(cap.caller_output_dir_allowed)
                self.assertFalse(cap.resource_from_request)
        for cid in (c.LAUNCH, c.STATUS, c.SCREENSHOT, c.VIDEO, c.STOP):
            self.assertEqual(caps[cid].resource_kind, "PLAYER_RUNTIME")
        self.assertEqual([cap.id for cap in pa.CAPABILITIES if cap.detached_spawn], [c.LAUNCH])
        self.assertEqual([cap.id for cap in pa.CAPABILITIES if cap.host_location], [c.INSTALL])
        self.assertEqual(caps[c.INSTALL].host_location, "USER_APPLICATIONS_GPOS")
        self.assertTrue(caps[c.INSTALL].dry_run_supported)

    def test_capture_side_effect_scope_is_explicit(self):
        for cid in (c.SCREENSHOT, c.VIDEO):
            text = next(cap for cap in pa.CAPABILITIES if cap.id == cid).side_effect_scope
            for phrase in ("exact verified helper", "LaunchServices", "/usr/bin/open", "this execution workspace",
                           "does not modify the Player", "TCC"):
                self.assertIn(phrase, text)

    def test_inputs_and_evidence(self):
        caps = {cap.id: cap for cap in pa.CAPABILITIES}
        self.assertEqual({cid: cap.input_kinds for cid, cap in caps.items()},
                         {c.INSTALL: (), c.LAUNCH: (), c.STATUS: (), c.SCREENSHOT: (), c.VIDEO: ("duration_seconds",),
                          c.STOP: ("recover_proven_gone",)})
        pairs = {cid: cap.potential_evidence for cid, cap in caps.items() if cap.potential_evidence}
        self.assertEqual(pairs, {c.SCREENSHOT: (("VISUAL_EVIDENCE", "DIAGNOSTIC_RUNTIME"),),
                                 c.VIDEO: (("MOTION_EVIDENCE", "DIAGNOSTIC_RUNTIME"),),
                                 c.STOP: (("RUNTIME_EVIDENCE", "DIAGNOSTIC_RUNTIME"),)})
        for cap in pa.CAPABILITIES:
            for etype, ctx in cap.potential_evidence:
                self.assertNotIn(etype, ("DEVICE_EVIDENCE", "PERFORMANCE_EVIDENCE", "PERSISTENCE_EVIDENCE",
                                         "HUMAN_EVIDENCE"))
                self.assertNotEqual(ctx, "TARGET_RUNTIME")

    def test_the_permanent_invocation_table(self):
        self.assertEqual(c.INVOCATIONS, {c.INSTALL: (), c.LAUNCH: ("SUPERVISE",), c.STATUS: (),
                                         c.SCREENSHOT: ("SHOT",), c.VIDEO: ("VIDEO",), c.STOP: ()})

    def test_one_external_invocation_at_most(self):
        class Ctx:
            def __init__(self):
                self.calls = []

            def spawn_detached(self, spec):
                self.calls.append(spec)
                return tproc.DetachedHandle(1)

            def run(self, spec):
                self.calls.append(spec)
                return tproc.ProcessOutcome(exit_code=0)
        ctx = Ctx()
        inv = pinv.ExternalInvocation(ctx, c.LAUNCH)
        inv.supervise("/bin/x", "/tmp/rt")
        with self.assertRaises(AssertionError):
            inv.supervise("/bin/x", "/tmp/rt")
        with self.assertRaises(AssertionError):
            inv.launch_services(c.SHOT, "/b", "/w", 1)
        self.assertEqual(len(ctx.calls), 1)
        for cap in (c.INSTALL, c.STATUS, c.STOP):
            with self.subTest(cap=cap):
                for call in (lambda i: i.supervise("/bin/x", "/tmp/rt"), lambda i: i.launch_services(c.SHOT, "/b", "/w", 1),
                             lambda i: i.launch_services(c.VIDEO_KIND, "/b", "/w", 1)):
                    with self.assertRaises(AssertionError):
                        call(pinv.ExternalInvocation(ctx, cap))
        shot = pinv.ExternalInvocation(ctx, c.SCREENSHOT)
        with self.assertRaises(AssertionError):
            shot.launch_services(c.VIDEO_KIND, "/b", "/w", 1)
        self.assertEqual(len(ctx.calls), 1)

    def test_registry_allowlists(self):
        pol = REG["tool_adapter_policy"]
        self.assertEqual(pol["detached_spawn_capabilities"], ["player.launch"])
        self.assertEqual(REG["tool_host_locations"], ["USER_APPLICATIONS_GPOS"])
        self.assertEqual(pol["host_location_capabilities"], {"USER_APPLICATIONS_GPOS": ["player.install-capture-helper"]})


# ---------------------------------------------------------------- B  structural pins

def _calls(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


class B_Structure(unittest.TestCase):
    MODULES = sorted(p for p in PLAYER.glob("*.py"))

    def test_only_invocation_starts_a_process(self):
        for path in self.MODULES:
            tree = ast.parse(path.read_text())
            names = {n.func.attr for n in _calls(tree) if isinstance(n.func, ast.Attribute)}
            with self.subTest(module=path.name):
                if path.name == "invocation.py":
                    self.assertTrue({"run", "spawn_detached"} <= names)
                else:
                    self.assertNotIn("spawn_detached", names)
                    runs = [n for n in _calls(tree) if isinstance(n.func, ast.Attribute) and n.func.attr == "run"]
                    self.assertEqual(runs, [], f"{path.name} calls .run(")

    def test_no_other_process_mechanism(self):
        forbidden = {"system", "popen", "execv", "execve", "execvp", "execl", "execlp", "spawnv", "spawnl", "posix_spawn",
                     "posix_spawnp", "fork", "forkpty", "startfile"}
        for path in self.MODULES:
            text = path.read_text()
            tree = ast.parse(text)
            imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            imports |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
                        and n.level == 0}
            with self.subTest(module=path.name):
                self.assertFalse(imports & {"subprocess", "multiprocessing", "pty", "asyncio", "socket"})
                attrs = {n.func.attr for n in _calls(tree) if isinstance(n.func, ast.Attribute)}
                self.assertFalse(attrs & forbidden, attrs & forbidden)

    def test_ctypes_libraries_are_allowlisted(self):
        allowed = {"/usr/lib/libSystem.B.dylib", "/usr/lib/libobjc.A.dylib",
                   "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
                   "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
                   "/System/Library/Frameworks/AppKit.framework/AppKit"}
        for path in self.MODULES:
            tree = ast.parse(path.read_text())
            for call in _calls(tree):
                if isinstance(call.func, ast.Attribute) and call.func.attr == "CDLL":
                    with self.subTest(module=path.name):
                        self.assertIsInstance(call.args[0], ast.Constant)
                        self.assertIn(call.args[0].value, allowed)

    def test_the_helper_has_exactly_three_modes(self):
        main = (PLAYER / "helper_src" / "main.swift").read_text()
        re_ = __import__("re")
        cases = sorted({name for line in re_.findall(r"case ([^:]+):", main) for name in re_.findall(r'"([a-z]+)"', line)})
        self.assertEqual(cases, ["shot", "supervise", "video"])
        self.assertEqual(hp.release()["modes"], ["supervise", "shot", "video"])
        self.assertEqual(sorted(pinv.MODES.values()), ["shot", "video"])

    def test_no_permission_request_or_other_privilege(self):
        for path in list((PLAYER / "helper_src").glob("*.swift")) + self.MODULES:
            text = path.read_text()
            code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(("//", "#", '"', "*")))
            with self.subTest(file=path.name):
                for needle in ("CGRequestScreenCaptureAccess(", "AXIsProcessTrusted", "AVCaptureDevice",
                               "NSAppleScript", "SBApplication", "tccutil", "CGEventPost", "IOHIDRequestAccess",
                               "capturesAudio = true", "SCStreamOutputType.audio"):
                    self.assertNotIn(needle, code)

    def test_preflight_precedes_screencapturekit_in_capture_modes(self):
        text = (PLAYER / "helper_src" / "Capture.swift").read_text()
        body = text[text.index("func runCapture("):]
        self.assertLess(body.index("CGPreflightScreenCaptureAccess()"), body.index("exactWindow(r)"))
        self.assertLess(body.index("CGPreflightScreenCaptureAccess()"), body.index("SCScreenshotManager"))
        self.assertLess(body.index("CGPreflightScreenCaptureAccess()"), body.index("SCStream("))
        guard = body[body.index("CGPreflightScreenCaptureAccess()"):][:120]
        self.assertIn('box.finish("REFUSED", "PERMISSION_NOT_GRANTED")', guard)

    def test_contract_is_portable(self):
        text = (PLAYER / "contract.py").read_text()
        code = text[text.index('"""', 3) + 3:]   # after the module docstring
        for word in ("/usr/bin/open", "LaunchServices", "TCC", "window_id", "bundle", ".app", "NSRunning", "libproc",
                     "CGWindow"):
            self.assertNotIn(word, code)

    def test_public_launch_data_has_no_backend_internals(self):
        case = PlayerCase("run")
        case.setUp()
        try:
            r = case.launch()
            text = json.dumps(r.data)
            for word in ("window_id", "cdhash", "/usr/bin/open", "Contents/MacOS", "nonce"):
                if word == "Contents/MacOS":
                    continue   # the executable is reported project-relative, which names the bundle path
                self.assertNotIn(word, text)
        finally:
            case.supervisor.stop.set()
            case.doCleanups()


# ---------------------------------------------------------------- C  the release and classification

class C_Release(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-rel-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_manifest_is_self_consistent(self):
        m = hp.release()
        self.assertEqual((m["schema"], m["helper_version"], m["bundle_identifier"], m["bundle_name"]),
                         ("gpos.player.helper-release/1", "1.0.0", "com.gpos.player-helper", "GposPlayerHelper.app"))
        self.assertEqual(m["signature"]["kind"], "ADHOC")
        self.assertEqual(sorted(m["signature"]["cdhashes"]), ["arm64", "x86_64"])
        for value in m["signature"]["cdhashes"].values():
            self.assertRegex(value, r"^[0-9a-f]{40}$")
        self.assertEqual(sorted(f["path"] for f in m["files"]),
                         ["Contents/Info.plist", "Contents/MacOS/GposPlayerHelper", "Contents/_CodeSignature/CodeResources"])
        self.assertEqual(hp.classify(hp.RELEASE_DIR / hp.BUNDLE_NAME)[0], hp.EXACT)

    def test_source_digest_binds_the_committed_source(self):
        import hashlib
        m = hp.release()
        for s in m["source"]["files"]:
            self.assertEqual(hashlib.sha256((PLAYER / s["path"]).read_bytes()).hexdigest(), s["sha256"], s["path"])
        names = sorted(Path(s["path"]).name for s in m["source"]["files"])
        self.assertEqual(names, sorted(p.name for p in (PLAYER / "helper_src").iterdir()))

    def copy(self):
        dest = self.tmp / "GposPlayerHelper.app"
        shutil.copytree(hp.RELEASE_DIR / hp.BUNDLE_NAME, dest, symlinks=True)
        return dest

    def test_classification(self):
        self.assertEqual(hp.classify(self.tmp / "absent.app")[0], hp.ABSENT)
        dest = self.copy()
        self.assertEqual(hp.classify(dest)[0], hp.EXACT)
        exe = dest / "Contents/MacOS/GposPlayerHelper"
        cases = {
            "an extra file": lambda d: (d / "Contents/extra.txt").write_text("x"),
            "a changed byte": lambda d: (d / "Contents/Info.plist").write_text((d / "Contents/Info.plist").read_text() + " "),
            "a mode change": lambda d: os.chmod(d / "Contents/MacOS/GposPlayerHelper", 0o700),
            "a missing file": lambda d: (d / "Contents/_CodeSignature/CodeResources").unlink(),
            "a link": lambda d: (os.unlink(d / "Contents/Info.plist"),
                                 os.symlink(hp.RELEASE_DIR / hp.BUNDLE_NAME / "Contents/Info.plist", d / "Contents/Info.plist")),
            "an extra directory": lambda d: (d / "Contents/Resources").mkdir(),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                shutil.rmtree(dest, ignore_errors=True)
                dest = self.copy()
                change(dest)
                self.assertEqual(hp.classify(dest)[0], hp.UNTRUSTED)
        shutil.rmtree(dest)
        os.symlink(hp.RELEASE_DIR / hp.BUNDLE_NAME, dest)
        self.assertEqual(hp.classify(dest)[0], hp.UNTRUSTED)
        self.assertTrue(exe.name)


# ---------------------------------------------------------------- D  install

class D_Install(PlayerCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.host)   # start from nothing installed

    def test_installs_at_the_account_home_location_only(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp / "elsewhere")}):
            r = self.run_cap(c.INSTALL)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertIn("PLAYER_HELPER_INSTALLED", self.codes(r))
        self.assertTrue(r.mutation_performed)
        self.assertEqual(hp.classify(self.host / hp.BUNDLE_NAME)[0], hp.EXACT)
        self.assertFalse((self.tmp / "elsewhere").exists())
        self.assertEqual(hp.staging_leftovers(self.host), [])
        self.assertIn("Screen Recording", self.text(r))
        record = json.loads((self.project / r.artifacts[0].path).read_text())
        self.assertEqual((record["state_before"], record["created"]), ("ABSENT", True))
        self.assertNotIn(str(self.home), json.dumps(record))
        self.assertIsNone(r.provenance.command)

    def test_exact_is_a_no_op(self):
        self.run_cap(c.INSTALL)
        before = {p: p.stat().st_mtime_ns for p in (self.host / hp.BUNDLE_NAME).rglob("*")}
        r = self.run_cap(c.INSTALL)
        self.assertEqual(r.status, tdg.SUCCESS)
        self.assertIn("PLAYER_HELPER_EXACT", self.codes(r))
        self.assertFalse(r.mutation_performed)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in (self.host / hp.BUNDLE_NAME).rglob("*")})

    def test_untrusted_is_never_touched(self):
        (self.host / hp.BUNDLE_NAME / "Contents").mkdir(parents=True)
        (self.host / hp.BUNDLE_NAME / "Contents/Info.plist").write_text("someone else's")
        r = self.run_cap(c.INSTALL)
        self.assertEqual(r.status, tdg.CONFLICT)
        self.assertIn("PLAYER_HELPER_INSTALL_CONFLICT", self.codes(r))
        self.assertEqual((self.host / hp.BUNDLE_NAME / "Contents/Info.plist").read_text(), "someone else's")
        self.assertEqual(hp.staging_leftovers(self.host), [])

    def test_an_empty_directory_at_the_destination_is_not_replaced(self):
        (self.host / hp.BUNDLE_NAME).mkdir(parents=True)
        r = self.run_cap(c.INSTALL)
        self.assertEqual(r.status, tdg.CONFLICT)
        self.assertEqual(list((self.host / hp.BUNDLE_NAME).iterdir()), [])

    def test_a_raced_in_destination(self):
        def racer(kind):
            def publish(staged, destination):
                if kind == "exact":
                    shutil.copytree(hp.RELEASE_DIR / hp.BUNDLE_NAME, destination)
                else:
                    os.mkdir(destination)
                    (Path(destination) / "foreign").write_text("raced")
                return hp.rename_exclusive(staged, destination)
            return publish
        for kind, status in (("exact", "EXACT"), ("foreign", None)):
            with self.subTest(kind=kind):
                shutil.rmtree(self.host, ignore_errors=True)
                if status:
                    self.assertEqual(hp.install(self.host, f"race-{kind}", publish=racer(kind)), ("EXACT", False))
                else:
                    with self.assertRaises(hp.InstallConflict):
                        hp.install(self.host, f"race-{kind}", publish=racer(kind))
                    self.assertEqual((self.host / hp.BUNDLE_NAME / "foreign").read_text(), "raced")
                self.assertEqual(hp.staging_leftovers(self.host), [])

    def test_rename_exclusive_never_replaces(self):
        src = self.tmp / "src"
        for existing in ("dir", "empty", "file", "link"):
            with self.subTest(existing=existing):
                shutil.rmtree(src, ignore_errors=True)
                src.mkdir()
                (src / "x").write_text("new")
                dest = self.tmp / f"dest-{existing}"
                if existing == "dir":
                    dest.mkdir(); (dest / "y").write_text("old")
                elif existing == "empty":
                    dest.mkdir()
                elif existing == "file":
                    dest.write_text("old")
                else:
                    os.symlink(self.tmp / "nowhere", dest)
                with self.assertRaises(FileExistsError):
                    hp.rename_exclusive(src, dest)
                self.assertTrue((src / "x").exists())
        hp.rename_exclusive(src, self.tmp / "fresh")
        self.assertEqual((self.tmp / "fresh" / "x").read_text(), "new")

    def test_ordinary_rename_would_have_replaced_an_empty_directory(self):
        """The measured reason RENAME_EXCL is the authority: os.rename silently replaces an empty directory."""
        (self.tmp / "a").mkdir(); (self.tmp / "a" / "x").write_text("1"); (self.tmp / "b").mkdir()
        os.rename(self.tmp / "a", self.tmp / "b")
        self.assertTrue((self.tmp / "b" / "x").exists())

    def test_a_staged_copy_that_does_not_verify_is_never_published(self):
        original = hp._copy_release

        def corrupt(staged):
            original(staged)
            path = Path(staged) / "Contents/Info.plist"
            os.chmod(path, 0o644)
            path.write_text(path.read_text() + " ")
        with mock.patch.object(hp, "_copy_release", corrupt):
            with self.assertRaises(hp.InstallConflict):
                hp.install(self.host, "corrupt")
        self.assertFalse((self.host / hp.BUNDLE_NAME).exists())
        self.assertEqual(hp.staging_leftovers(self.host), [])

    def test_staging_leftover_is_reported_not_removed(self):
        self.host.mkdir(parents=True)
        (self.host / ".gpos-staging-old").mkdir()
        r = self.run_cap(c.INSTALL)
        self.assertIn("PLAYER_INSTALL_STAGING_LEFTOVER", self.codes(r))
        self.assertTrue((self.host / ".gpos-staging-old").is_dir())

    def test_dry_run_creates_nothing(self):
        r = self.run_cap(c.INSTALL, dry_run=True)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertFalse(self.host.exists())
        self.assertIn("would install", " ".join(r.plan))

    def test_no_caller_destination(self):
        r = self.run_cap(c.INSTALL, output_dir=str(self.tmp / "out"))
        self.assertEqual(r.status, tdg.INVALID_REQUEST)
        r = self.run_cap(c.INSTALL, inputs={"destination": "/tmp/x"})
        self.assertEqual(r.status, tdg.INVALID_REQUEST)
        self.assertFalse(self.host.exists())

    def test_a_linked_location_is_refused(self):
        real = self.tmp / "real-apps"
        real.mkdir()
        shutil.rmtree(self.home / "Applications")
        os.symlink(real, self.home / "Applications")
        with self.assertRaises(hp.InstallConflict):
            hp.install(self.host, "linked")
        self.assertEqual(list(real.iterdir()), [])


# ---------------------------------------------------------------- E  runtime files

class E_RuntimeFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-rtf-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_write_once(self):
        p = self.tmp / "x.json"
        prt.write_once(p, {"a": 1})
        with self.assertRaises(FileExistsError):
            prt.write_once(p, {"a": 2})
        self.assertEqual(json.loads(p.read_text()), {"a": 1})
        self.assertEqual(sorted(os.listdir(self.tmp)), ["x.json"])
        with self.assertRaises(prt.RecordProblem):
            prt.write_once(self.tmp / "big.json", {"a": "x" * 10000})

    def test_links_and_bounds_are_refused(self):
        (self.tmp / "real.json").write_text("{}")
        os.symlink(self.tmp / "real.json", self.tmp / "link.json")
        with self.assertRaises(prt.RecordProblem):
            prt.read_json(self.tmp / "link.json")
        (self.tmp / "big.json").write_text("{" + " " * 9000 + "}")
        with self.assertRaises(prt.RecordProblem):
            prt.read_json(self.tmp / "big.json")

    def test_strict_shapes(self):
        sid, nonce = "a" * 32, "b" * 32
        good = {"schema": "gpos.player.handshake/1", "session_id": sid, "nonce": nonce, "spawned_at": "t",
                "supervisor": {"pid": 2, "start_sec": 1, "start_usec": 0, "executable": "/x", "cdhash": "c",
                               "version": "1.0.0"},
                "player": {"pid": 3, "ppid": 2, "pgid": 3, "start_sec": 1, "start_usec": 0, "executable": "/y"}}
        prt.write_once(self.tmp / "h.json", good)
        self.assertEqual(prt.handshake(self.tmp / "h.json", sid, nonce)["player"]["pid"], 3)
        with self.assertRaises(prt.RecordProblem):
            prt.handshake(self.tmp / "h.json", sid, "c" * 32)
        for mutate in (lambda d: d.update(extra=1), lambda d: d["player"].pop("pgid"),
                       lambda d: d["supervisor"].update(pid=True), lambda d: d.update(schema="x")):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            path = self.tmp / f"bad-{id(mutate)}.json"
            prt.write_once(path, bad)
            with self.assertRaises(prt.RecordProblem):
                prt.handshake(path, sid, nonce)

    def test_the_binding_must_belong_to_the_session(self):
        session = {"session_id": "a" * 32, "nonce": "b" * 32, "build_id": "build-x", "manifest_sha256": "m",
                   "executable": "/y"}
        binding = {"schema": "gpos.player.runtime-binding/1", **{k: session[k] for k in ("session_id", "nonce",
                                                                                           "build_id", "manifest_sha256")},
                   "supervisor": {"pid": 2, "start_sec": 1, "start_usec": 0, "executable": "/x", "cdhash": "c"},
                   "player": {"pid": 3, "start_sec": 1, "start_usec": 0, "executable": "/y", "dev": 1, "ino": 2},
                   "handshake_at": "t", "bound_at": "t"}
        prt.write_once(self.tmp / "b.json", binding)
        self.assertEqual(prt.binding(self.tmp / "b.json", session)["player"]["pid"], 3)
        for key, value in (("nonce", "c" * 32), ("build_id", "build-y"), ("manifest_sha256", "n"),
                           ("session_id", "d" * 32), ("executable", "/z")):
            with self.subTest(key=key):
                with self.assertRaises(prt.RecordProblem):
                    prt.binding(self.tmp / "b.json", dict(session, **{key: value}))


# ---------------------------------------------------------------- F  resolution

class F_Resolution(PlayerCase):
    def test_a_completed_build_resolves(self):
        t = pres.resolve(self.project, self.build_id)
        self.assertEqual((t.application_id, t.executable, t.kind, t.target_platform),
                         (pf.APP_ID, self.exe, "MACOS_APP_BUNDLE", "MACOS"))
        self.assertEqual(t.build_revision, pf.REV)

    def test_refusals(self):
        for bad in ("../x", "build-", "Build-req", "build-req/x", None, 7):
            with self.subTest(build=bad):
                with self.assertRaises(pres.BuildProblem):
                    pres.resolve(self.project, bad)
        with self.assertRaises(pres.BuildProblem):
            pres.resolve(self.project, "build-req-ffffffffffffffff")

    def test_tamper_and_manifest_reserialisation_drift(self):
        session = {"build_id": self.build_id}
        t = pres.resolve(self.project, self.build_id)
        session.update(manifest_sha256=t.manifest_sha256, tree_digest=t.tree_digest, executable_dev=t.dev,
                       executable_ino=t.ino)
        self.assertEqual(pres.revalidates(self.project, session), ("VALID", None))
        manifest = Path(t.workspace) / "build-manifest.json"
        original = manifest.read_bytes()
        os.chmod(manifest, 0o644)
        manifest.write_text(json.dumps(json.loads(original)))   # same content, other bytes
        self.assertEqual(pres.revalidates(self.project, session)[0], "DRIFT")
        manifest.write_bytes(original)
        exe = Path(self.exe)
        exe.write_text("#!/bin/sh\nexit 1\n")
        self.assertEqual(pres.revalidates(self.project, session)[0], "DRIFT")

    def test_a_linked_workspace_is_refused(self):
        unity = self.project / ".game/gpos-runtime/tool-output/unity"
        os.rename(unity, self.tmp / "moved")
        os.symlink(self.tmp / "moved", unity)
        with self.assertRaises(pres.BuildProblem):
            pres.resolve(self.project, self.build_id)


# ---------------------------------------------------------------- G  launch

class G_Launch(PlayerCase):
    def test_the_launch_sequence(self):
        r = self.launch()
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertEqual(self.codes(r), {"PLAYER_LAUNCHED"})
        self.assertTrue(r.mutation_performed)
        d = r.data
        self.assertEqual(d["application_id"], pf.APP_ID)
        self.assertEqual(d["build"]["build_id"], self.build_id)
        self.assertEqual(d["process"]["pid"], self.supervisor.player)
        self.assertFalse(d["process"]["executable"].startswith("/"))
        rdir = self.runtime_dir(r)
        self.assertEqual(sorted(os.listdir(rdir)), ["commit.json", "handshake.json", "runtime-binding.json",
                                                    "supervisor-request.json"])
        binding = json.loads((rdir / "runtime-binding.json").read_text())
        self.assertEqual(binding["player"]["pid"], self.supervisor.player)
        self.assertEqual(binding["supervisor"]["cdhash"], pf.CDHASH)
        # the immutable lease holds the pre-launch authority only
        lease = self.lease_record()
        s = lease["session"]
        self.assertEqual(s["phase"], "LAUNCHING")
        for key in ("session_id", "launch_request_id", "build_id", "build_revision", "manifest_sha256", "tree_digest",
                    "unity_build_guid", "application_id", "executable", "executable_dev", "executable_ino",
                    "runtime_dir", "nonce", "helper", "launched_at"):
            self.assertIn(key, s)
        self.assertNotIn("pid", json.dumps(s))
        self.assertEqual(lease["resource_id"], f"PLAYER_RUNTIME:{self.project}")
        # exactly one external process: the verified helper's supervise mode
        self.assertEqual(len(self.supervisor.specs), 1)
        spec = self.supervisor.specs[0]
        self.assertEqual(spec.executable, str(hp.executable(self.host / hp.BUNDLE_NAME)))
        self.assertEqual(spec.argv, ("supervise", f"{rdir}/supervisor-request.json"))
        self.assertEqual(spec.cwd, str(rdir))
        self.assertEqual(spec.env.metadata(), {"inherited_names": ["HOME", "LANG", "TMPDIR"], "set_names": ["PATH"]})
        self.assertEqual(r.provenance.command["executable"], spec.executable)
        self.assertEqual(r.provenance.tool_name, "GPOS Player Helper")
        self.assertEqual(self.helper.specs, [])
        request = json.loads((rdir / "supervisor-request.json").read_text())
        self.assertEqual(set(request), {"schema", "session_id", "nonce", "launch_request_id", "application_id",
                                        "executable", "executable_dev", "executable_ino", "commit_deadline_s",
                                        "stop_grace_s"})

    def test_request_rules(self):
        for kw, status in (({"build_id": None}, tdg.INVALID_REQUEST), ({"inputs": {"args": ["-x"]}}, tdg.INVALID_REQUEST),
                           ({"target_platform": "WINDOWS"}, tdg.INVALID_REQUEST), ({"device": "x"}, tdg.INVALID_REQUEST),
                           ({"build_revision": "f" * 40}, tdg.INVALID_REQUEST),
                           ({"output_dir": "/tmp/out"}, tdg.INVALID_REQUEST), ({"resource_id": "x"}, tdg.INVALID_REQUEST),
                           ({"actor": None}, tdg.INVALID_REQUEST), ({"allow_mutation": False}, tdg.INVALID_REQUEST)):
            with self.subTest(kw=kw):
                args = dict({"build_id": self.build_id}, **{k: v for k, v in kw.items() if k != "actor"})
                r = self.run_cap(c.LAUNCH, **({"actor": kw["actor"]} if "actor" in kw else {}), **args)
                self.assertEqual(r.status, status, self.text(r))
                self.assertEqual(self.supervisor.specs, [])
                self.assertIsNone(self.lease_record())

    def test_a_missing_or_invalid_build(self):
        r = self.run_cap(c.LAUNCH, build_id="build-req-ffffffffffffffff")
        self.assertEqual((r.status, self.codes(r)), (tdg.FAILED, {"PLAYER_BUILD_INVALID"}))
        Path(self.exe).write_text("tampered")
        r = self.launch()
        self.assertIn("PLAYER_BUILD_INVALID", self.codes(r))
        self.assertEqual(self.supervisor.specs, [])

    def test_helper_absent_or_untrusted(self):
        shutil.rmtree(self.host / hp.BUNDLE_NAME)
        r = self.launch()
        self.assertEqual(r.status, tdg.UNAVAILABLE)
        self.assertIn("PLAYER_HELPER_ABSENT", self.codes(r))
        pf.install_helper(self.home)
        (self.host / hp.BUNDLE_NAME / "Contents/extra").write_text("x")
        r = self.launch()
        self.assertEqual(r.status, tdg.UNAVAILABLE)
        self.assertIn("PLAYER_HELPER_UNTRUSTED", self.codes(r))
        self.assertEqual(self.supervisor.specs, [])

    def test_a_foreign_instance_blocks_before_anything(self):
        for how in ("copy elsewhere", "appkit only"):
            with self.subTest(how=how):
                self.os.procs.clear()
                if how == "copy elsewhere":
                    self.os.add("/Users/x/Desktop/Copy of Game.app/Contents/MacOS/FakeGame", app_id=pf.APP_ID)
                else:
                    pid = self.os.add("/x", app_id=None)
                    self.appkit.running_pids = lambda app_id, pid=pid: [pid]
                r = self.launch()
                self.assertEqual(r.status, tdg.CONFLICT, self.text(r))
                self.assertIn("PLAYER_RUNTIME_CONFLICT", self.codes(r))
                self.assertEqual(self.supervisor.specs, [])
                self.assertIsNone(self.lease_record())
                self.assertEqual(self.appkit.calls, [])
                self.appkit.running_pids = pf.FakeAppKit.running_pids.__get__(self.appkit)

    def test_an_unrelated_application_does_not_block(self):
        self.os.add("/Applications/Other.app/Contents/MacOS/Other", app_id="com.example.other")
        self.assertEqual(self.launch().status, tdg.SUCCESS)

    def test_a_held_session_conflicts(self):
        sid, _ = self.launched()
        r = self.launch()
        self.assertEqual(r.status, tdg.CONFLICT)
        self.assertTrue(self.codes(r) & {"LIVE_SESSION_HELD", "PLAYER_RUNTIME_CONFLICT"})

    def scenario(self, name, **kw):
        self.supervisor = pf.FakeSupervisor(self.os, name, **kw)
        self.addCleanup(self.supervisor.stop.set)
        return self.launch()

    def test_handshake_and_identity_failures_abandon_and_release(self):
        for name, code in (("bad_nonce", "PLAYER_HANDSHAKE_INVALID"), ("other_supervisor", "PLAYER_HANDSHAKE_INVALID"),
                           ("wrong_cdhash", "PLAYER_HANDSHAKE_INVALID"), ("not_child", "PLAYER_IDENTITY_UNPROVEN"),
                           ("wrong_player_exe", "PLAYER_IDENTITY_UNPROVEN")):
            with self.subTest(scenario=name):
                r = self.scenario(name)
                self.assertIn(code, self.codes(r), self.text(r))
                self.assertEqual(r.status, tdg.FAILED, self.text(r))
                self.assertTrue(r.mutation_performed)
                self.assertIsNone(self.lease_record(), "the never-confirmed session must be released")
                rdir = self.project / ".game/gpos-runtime/tool-output/player" / r.request_id
                self.assertTrue((rdir / "abort.json").exists())
                self.assertFalse((rdir / "commit.json").exists())
                self.assertFalse((rdir / "runtime-binding.json").exists())
                self.os.procs.clear()

    def test_the_player_exits_before_the_launch_commits(self):
        r = self.scenario("player_exits")
        self.assertIn("PLAYER_EXITED_DURING_LAUNCH", self.codes(r), self.text(r))
        self.assertIsNone(self.lease_record())

    def test_the_supervisor_dies_without_a_handshake(self):
        r = self.scenario("supervisor_dies")
        self.assertIn("PLAYER_SUPERVISOR_FAILED", self.codes(r), self.text(r))

    def test_no_handshake_in_time(self):
        with mock.patch.object(c, "HANDSHAKE_WAIT_SECONDS", 0.3):
            r = self.scenario("no_handshake")
        self.assertIn("PLAYER_HANDSHAKE_INVALID", self.codes(r), self.text(r))
        self.assertIsNone(self.lease_record())

    def test_an_unresolved_launch_keeps_its_session(self):
        """The Player is not proven to have ended (the supervisor ignores the abort): the session is kept."""
        r = self.scenario("ignore_abort", foreign_after_handshake=True)
        self.assertEqual(r.status, tdg.CONFLICT, self.text(r))   # the frozen severity order ranks CONFLICT first
        self.assertTrue({"PLAYER_RUNTIME_CONFLICT", "PLAYER_LAUNCH_UNRESOLVED"} <= self.codes(r))
        self.assertIsNotNone(self.lease_record())
        self.assertEqual(len(self.supervisor.specs), 1, "no automatic retry")

    def test_the_build_drifts_during_the_launch(self):
        r = self.scenario("drift")
        self.assertIn("PLAYER_BUILD_INVALID", self.codes(r), self.text(r))
        self.assertIsNone(self.lease_record())

    def test_a_foreign_instance_appearing_during_the_launch(self):
        r = self.scenario("ok", foreign_after_handshake=True)
        self.assertIn("PLAYER_RUNTIME_CONFLICT", self.codes(r))
        self.assertEqual(self.appkit.calls, [], "the foreign instance is never signalled")


# ---------------------------------------------------------------- H  status

class H_Status(PlayerCase):
    def status(self, sid, actor=AGENT):
        before = sorted(str(p) for p in self.project.rglob("*"))
        r = self.run_cap(c.STATUS, session_id=sid, actor=actor)
        self.assertEqual(sorted(str(p) for p in self.project.rglob("*")), before, "status writes nothing")
        return r

    def test_a_bound_session(self):
        sid, _ = self.launched()
        specs = (len(self.supervisor.specs), len(self.helper.specs))
        r = self.status(sid)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertEqual((len(self.supervisor.specs), len(self.helper.specs)), specs, "status starts no process")
        d = r.data
        self.assertEqual((d["phase"], d["identity"], d["supervisor"], d["build"]["trust"], d["helper"],
                          d["capture_permission"]), ("BOUND", "PROVEN", "ALIVE", "VALID", "EXACT", "NOT_CHECKED"))
        self.assertEqual(d["window"], {"present": True, "candidates": 1})
        self.assertFalse(r.mutation_performed)
        self.assertIsNone(r.provenance.command)
        self.assertEqual(r.artifacts, ())
        self.assertEqual((self.appkit.calls, self.os.killed), ([], []), "status never signals anything")

    def test_helper_absent_is_a_fact_not_a_blocker(self):
        sid, _ = self.launched()
        shutil.rmtree(self.host / hp.BUNDLE_NAME)
        r = self.status(sid)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertEqual((r.data["helper"], r.data["capture_permission"], r.data["identity"]),
                         ("ABSENT", "UNKNOWN", "PROVEN"))

    def test_gone_reused_and_drift(self):
        sid, launch = self.launched()
        binding = json.loads((self.runtime_dir(launch) / "runtime-binding.json").read_text())
        p = self.os.procs[binding["player"]["pid"]]
        p["start_usec"] += 1
        self.assertEqual(self.status(sid).data["identity"], "NOT_THIS_PROCESS")
        self.os.remove(binding["player"]["pid"])
        self.assertEqual(self.status(sid).data["identity"], "GONE")
        Path(self.exe).write_text("changed")
        r = self.status(sid)
        self.assertEqual(r.data["build"]["trust"], "DRIFT")
        self.assertIn("PLAYER_BUILD_DRIFT", self.codes(r))
        self.assertEqual(r.status, tdg.SUCCESS)

    def test_an_unresolved_launch(self):
        sid, launch = self.launched()
        binding = self.runtime_dir(launch) / "runtime-binding.json"
        os.unlink(binding)
        r = self.status(sid)
        self.assertEqual((r.data["phase"], r.data["identity"]), ("LAUNCHING_UNRESOLVED", "UNPROVEN"))

    def test_only_the_owner(self):
        sid, _ = self.launched()
        r = self.run_cap(c.STATUS, session_id=sid, actor=OTHER)
        self.assertEqual(r.status, tdg.CONFLICT)
        r = self.run_cap(c.STATUS, session_id="0" * 32)
        self.assertEqual(r.status, tdg.CONFLICT)

    def test_no_permission_input(self):
        sid, _ = self.launched()
        r = self.run_cap(c.STATUS, session_id=sid, inputs={"report_capture_permission": True})
        self.assertEqual(r.status, tdg.INVALID_REQUEST)


# ---------------------------------------------------------------- I  capture

class I_Capture(PlayerCase):
    def capture(self, sid, video=False, scenario="captured", **kw):
        self.helper = pf.FakeHelper(self.os, scenario)
        kw.setdefault("build_id", self.build_id)
        if video:
            kw.setdefault("inputs", {"duration_seconds": 2})
        return self.run_cap(c.VIDEO if video else c.SCREENSHOT, session_id=sid, **kw)

    def test_a_screenshot(self):
        sid, _ = self.launched()
        r = self.capture(sid)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertTrue(r.mutation_performed)
        self.assertEqual(len(self.helper.specs), 1)
        spec = self.helper.specs[0]
        ws = self.project / ".game/gpos-runtime/tool-output/player" / r.request_id
        bundle = self.host / hp.BUNDLE_NAME
        self.assertEqual((spec.executable, spec.argv), ("/usr/bin/open", (
            "-n", "-W", "--stdout", f"{ws}/helper.out", "--stderr", f"{ws}/helper.err", str(bundle), "--args", "shot",
            f"{ws}/helper-request.json")))
        self.assertEqual(r.provenance.command["executable"], "/usr/bin/open")
        self.assertEqual(r.provenance.tool_path, str(hp.executable(bundle)))
        self.assertEqual(sorted(a.artifact_id for a in r.artifacts), ["capture-record", "runtime-screenshot"])
        (cand,) = r.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context), ("VISUAL_EVIDENCE", "DIAGNOSTIC_RUNTIME"))
        self.assertEqual(set(cand.artifact_ids), {"runtime-screenshot", "capture-record"})
        self.assertEqual(cand.provenance["build_id"], self.build_id)
        self.assertTrue(any("5 s" in l for l in cand.limitations))
        record = json.loads((ws / "capture-record.json").read_text())
        for key in ("session_id", "build", "application_id", "process", "capture", "helper", "window_proof", "media"):
            self.assertIn(key, record)
        self.assertEqual(record["helper"]["reported_cdhash"], pf.CDHASH)
        self.assertEqual(record["capture"]["mechanism"], "MACOS_SCREENCAPTUREKIT_WINDOW")
        self.assertNotIn("window_id", json.dumps(record))
        request = json.loads((ws / "helper-request.json").read_text())
        self.assertEqual(request["target"]["pid"], self.supervisor.player)
        self.assertEqual((request["settle_s"], request["max_edge_px"]), (5, 1920))

    def test_a_video(self):
        sid, _ = self.launched()
        r = self.capture(sid, video=True)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertEqual(self.helper.specs[0].argv[-2], "video")
        (cand,) = r.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context), ("MOTION_EVIDENCE", "DIAGNOSTIC_RUNTIME"))
        self.assertEqual(r.data["media"]["duration_s"], 2.0)

    def test_duration_rules(self):
        sid, _ = self.launched()
        for value in (0, 16, 1.5, True, "1.5", "x", "016", " 3", "", None, -1, "16", "0"):
            with self.subTest(value=value):
                r = self.capture(sid, video=True, inputs={"duration_seconds": value})
                self.assertEqual(r.status, tdg.INVALID_REQUEST)
        self.assertEqual(self.helper.specs, [])
        for value in (1, 15, "3", "15"):   # the CLI passes a plain digit string
            self.assertEqual(self.capture(sid, video=True, inputs={"duration_seconds": value}).status, tdg.SUCCESS)

    def test_no_caller_target(self):
        sid, _ = self.launched()
        for inputs in ({"pid": 1}, {"window_id": 3}, {"region": [0, 0, 1, 1]}, {"screen": 0}, {"mode": "shot"}):
            with self.subTest(inputs=inputs):
                self.assertEqual(self.capture(sid, inputs=inputs).status, tdg.INVALID_REQUEST)
        self.assertEqual(self.capture(sid, output_dir=str(self.tmp)).status, tdg.INVALID_REQUEST)
        self.assertEqual(self.capture(sid, allow_mutation=False).status, tdg.INVALID_REQUEST)

    def test_restated_build(self):
        sid, _ = self.launched()
        self.assertEqual(self.capture(sid, build_id=None).status, tdg.INVALID_REQUEST)
        self.assertEqual(self.capture(sid, build_id="build-req-other").status, tdg.INVALID_REQUEST)
        self.assertEqual(self.capture(sid, build_revision="f" * 40).status, tdg.INVALID_REQUEST)
        self.assertEqual(self.capture(sid, target_platform="WINDOWS").status, tdg.INVALID_REQUEST)
        self.assertEqual(self.helper.specs, [])

    def test_permission_is_checked_inside_the_one_invocation(self):
        sid, _ = self.launched()
        r = self.capture(sid, scenario="permission")
        self.assertEqual(r.status, tdg.UNAVAILABLE, self.text(r))
        self.assertIn("CAPTURE_PERMISSION_REQUIRED", self.codes(r))
        self.assertIn("System Settings", self.text(r))
        self.assertEqual(len(self.helper.specs), 1)
        self.assertEqual(self.helper.specs[0].argv[-2], "shot")
        self.assertEqual(r.artifacts, ())
        self.assertEqual(r.evidence_candidates, ())
        r = self.capture(sid, scenario="permission_after_sck")
        self.assertIn("CAPTURE_HELPER_IDENTITY_MISMATCH", self.codes(r))

    def test_helper_failures_publish_nothing(self):
        sid, _ = self.launched()
        expect = {"window_not_found": "CAPTURE_WINDOW_NOT_FOUND", "window_changed": "CAPTURE_WINDOW_OWNER_MISMATCH",
                  "stream_stopped": "CAPTURE_TARGET_EXITED", "no_result": "CAPTURE_FAILED", "timeout": "CAPTURE_FAILED",
                  "wrong_nonce": "CAPTURE_HELPER_IDENTITY_MISMATCH", "wrong_cdhash": "CAPTURE_HELPER_IDENTITY_MISMATCH",
                  "wrong_version": "CAPTURE_HELPER_IDENTITY_MISMATCH", "bad_media": "CAPTURE_OUTPUT_INVALID",
                  "size_mismatch": "CAPTURE_OUTPUT_INVALID"}
        for scenario, code in expect.items():
            for video in (False, True):
                if video and scenario == "size_mismatch":
                    continue
                with self.subTest(scenario=scenario, video=video):
                    r = self.capture(sid, video=video, scenario=scenario)
                    self.assertIn(code, self.codes(r), self.text(r))
                    self.assertNotEqual(r.status, tdg.SUCCESS)
                    self.assertEqual(r.artifacts, ())
                    self.assertEqual(r.evidence_candidates, ())
                    self.assertEqual(len(self.helper.specs), 1)

    def test_a_window_proof_for_another_process_is_refused(self):
        sid, _ = self.launched()
        r = self.capture(sid, scenario="foreign_window")
        self.assertIn("CAPTURE_OUTPUT_INVALID", self.codes(r))
        self.assertEqual((r.artifacts, r.evidence_candidates), ((), ()))

    def test_video_media_rules(self):
        sid, _ = self.launched()
        for scenario in ("audio_track", "unfinished"):
            with self.subTest(scenario=scenario):
                r = self.capture(sid, video=True, scenario=scenario)
                self.assertIn("CAPTURE_OUTPUT_INVALID", self.codes(r))
                self.assertEqual(r.evidence_candidates, ())

    def test_the_helper_changed_around_the_capture(self):
        sid, _ = self.launched()
        helper = pf.FakeHelper(self.os)
        original = helper._answer

        def tamper(ws, req, bundle):
            original(ws, req, bundle)
            (self.host / hp.BUNDLE_NAME / "Contents/extra").write_text("x")
        helper._answer = tamper
        self.helper = helper
        r = self.run_cap(c.SCREENSHOT, session_id=sid, build_id=self.build_id)
        self.assertIn("CAPTURE_HELPER_IDENTITY_MISMATCH", self.codes(r))
        self.assertEqual(r.artifacts, ())

    def test_untrusted_helper_before_the_capture(self):
        sid, _ = self.launched()
        (self.host / hp.BUNDLE_NAME / "Contents/extra").write_text("x")
        r = self.capture(sid)
        self.assertEqual(r.status, tdg.UNAVAILABLE)
        self.assertEqual(self.helper.specs, [])

    def test_the_adapter_rechecks_the_helper_after_a_cached_probe(self):
        """The registry caches a READY probe; a helper changed after it is still refused before LaunchServices."""
        sid, _ = self.launched()
        registry = self.registry()
        self.assertEqual(registry.probe("player").status, "AVAILABLE")
        (self.host / hp.BUNDLE_NAME / "Contents/extra").write_text("x")
        request = ExecutionRequest(adapter_id="player", capability_id=c.SCREENSHOT, session_id=sid,
                                   subject=Subject("FEATURE", "FEATURE-0001", pf.REV), project_root=str(self.project),
                                   actor=AGENT, build_id=self.build_id, allow_mutation=True)
        with mock.patch.object(tproc, "run_process", self.helper):
            r = execute(registry, request)
        self.assertIn("PLAYER_HELPER_UNTRUSTED", self.codes(r))
        self.assertEqual(self.helper.specs, [])

    def test_trust_preconditions(self):
        sid, launch = self.launched()
        binding = json.loads((self.runtime_dir(launch) / "runtime-binding.json").read_text())
        Path(self.exe).write_text("drifted")
        r = self.capture(sid)
        self.assertIn("CAPTURE_BUILD_DRIFT", self.codes(r))
        self.assertEqual(self.helper.specs, [])
        self.setUp_build_back()
        self.os.procs[binding["player"]["pid"]]["windows"] = 0
        self.assertIn("CAPTURE_WINDOW_NOT_FOUND", self.codes(self.capture(sid)))
        self.os.procs[binding["player"]["pid"]]["windows"] = 2
        self.assertIn("CAPTURE_WINDOW_AMBIGUOUS", self.codes(self.capture(sid)))
        self.os.remove(binding["player"]["pid"])
        self.assertIn("PLAYER_RUNTIME_GONE", self.codes(self.capture(sid)))
        self.assertEqual(self.helper.specs, [])

    def setUp_build_back(self):
        Path(self.exe).write_text("#!/bin/sh\nexit 0\n")


# ---------------------------------------------------------------- J  stop

class J_Stop(PlayerCase):
    def stop(self, sid, actor=AGENT, **kw):
        kw.setdefault("build_id", self.build_id)
        return self.run_cap(c.STOP, session_id=sid, actor=actor, **kw)

    def test_the_supervisor_path_is_graceful_and_starts_no_process(self):
        sid, launch = self.launched()
        specs = len(self.supervisor.specs)
        r = self.stop(sid)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertEqual((r.data["classification"], r.data["path"], r.data["closed"]), ("GRACEFUL_STOP", "SUPERVISOR",
                                                                                         True))
        self.assertEqual(len(self.supervisor.specs), specs)
        self.assertEqual(self.helper.specs, [])
        self.assertIsNone(r.provenance.command)
        self.assertEqual(self.appkit.calls, [])
        self.assertIsNone(self.lease_record())
        self.assertTrue((self.runtime_dir(launch) / f"stop-intent-{r.request_id}.json").exists())

    def test_a_hanging_player_is_forced_by_its_supervisor(self):
        self.supervisor = pf.FakeSupervisor(self.os, "player_hangs")
        sid, _ = self.launched()
        r = self.stop(sid)
        self.assertEqual(r.data["classification"], "FORCED_STOP")

    def test_exit_codes_and_crashes(self):
        self.supervisor = pf.FakeSupervisor(self.os, "exit_code=3")
        sid, launch = self.launched()
        binding = json.loads((self.runtime_dir(launch) / "runtime-binding.json").read_text())
        self.os.remove(binding["player"]["pid"])   # the game quits by itself with 3
        import time
        deadline = time.monotonic() + 10
        while not (self.runtime_dir(launch) / "exit.json").exists() and time.monotonic() < deadline:
            time.sleep(0.02)                         # the supervisor observes the exit
        r = self.stop(sid)
        self.assertEqual((r.data["classification"], r.data["code"]), ("EXITED", 3))
        self.assertEqual(r.data["path"], "NONE")

    def test_the_fallback_when_the_supervisor_is_gone(self):
        sid, launch = self.launched()
        self.os.remove(self.supervisor.supervisor)
        shutil.rmtree(self.host / hp.BUNDLE_NAME)   # the installed helper is gone too: stop must not care
        r = self.stop(sid)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertEqual((r.data["path"], r.data["classification"]), ("FALLBACK", "GONE_UNOBSERVED"))
        binding = json.loads((self.runtime_dir(launch) / "runtime-binding.json").read_text())
        p = binding["player"]
        self.assertEqual(self.appkit.calls, [(p["pid"], pf.APP_ID, p["executable"])])
        self.assertEqual(self.helper.specs, [])
        self.assertIsNone(r.provenance.command)
        self.assertIn("PLAYER_EVIDENCE_WITHHELD", self.codes(r))
        self.assertEqual(r.evidence_candidates, ())

    def test_the_fallback_kills_only_the_reproven_player(self):
        sid, launch = self.launched()
        binding = json.loads((self.runtime_dir(launch) / "runtime-binding.json").read_text())
        pid = binding["player"]["pid"]
        self.os.procs[pid]["on_terminate"] = "hang"
        self.os.remove(self.supervisor.supervisor)
        with mock.patch.object(c, "STOP_GRACE_SECONDS", 0.3):
            r = self.stop(sid)
        self.assertEqual((r.data["path"], r.data["classification"]), ("FALLBACK", "FORCED_STOP"))
        self.assertEqual(self.os.killed, [pid])
        self.assertTrue((self.runtime_dir(launch) / f"kill-{r.request_id}.json").exists())

    def test_an_unproven_process_is_never_signalled(self):
        sid, launch = self.launched()
        binding = json.loads((self.runtime_dir(launch) / "runtime-binding.json").read_text())
        pid = binding["player"]["pid"]
        self.os.remove(self.supervisor.supervisor)
        self.os.procs[pid]["start_sec"] += 1   # the pid now belongs to another process
        r = self.stop(sid)
        self.assertEqual(self.appkit.calls, [])
        self.assertEqual(self.os.killed, [])
        self.assertEqual(r.data["classification"], "GONE_UNOBSERVED")

    def test_a_moved_executable_is_unproven_never_signalled_and_never_closed(self):
        sid, launch = self.launched()
        binding = json.loads((self.runtime_dir(launch) / "runtime-binding.json").read_text())
        self.os.procs[binding["player"]["pid"]]["executable"] = "/Users/x/Moved.app/Contents/MacOS/FakeGame"
        self.os.remove(self.supervisor.supervisor)
        s = self.run_cap(c.STATUS, session_id=sid)
        self.assertEqual(s.data["identity"], "UNPROVEN")
        r = self.stop(sid)
        self.assertEqual(r.status, tdg.OUTCOME_UNKNOWN, self.text(r))
        self.assertEqual((self.appkit.calls, self.os.killed), ([], []))
        self.assertIsNotNone(self.lease_record())
        r = self.stop(sid, actor=OTHER, inputs={"recover_proven_gone": True})
        self.assertEqual(r.status, tdg.CONFLICT)

    def test_the_supervisor_is_proven_by_its_code_not_its_path(self):
        sid, launch = self.launched()
        sup = self.supervisor.supervisor
        self.os.procs[sup]["executable"] = None    # the helper install was removed after launch
        r = self.stop(sid)
        self.assertEqual((r.data["path"], r.data["classification"]), ("SUPERVISOR", "GRACEFUL_STOP"), self.text(r))
        sid, launch = self.launched()
        self.os.procs[self.supervisor.supervisor]["cdhash"] = "0" * 40   # another program at that pid and time
        r = self.stop(sid)
        self.assertEqual(r.data["path"], "FALLBACK")

    def test_build_drift_never_blocks_a_proven_stop(self):
        sid, launch = self.launched()
        (self.runtime_dir(launch) / "player.log").write_text("GPOSQ start\n")   # a log exists: only trust is missing
        Path(self.exe).write_text("drifted")
        r = self.stop(sid)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertIn("PLAYER_BUILD_DRIFT", self.codes(r))
        self.assertEqual(r.data["classification"], "GRACEFUL_STOP")
        self.assertEqual(r.evidence_candidates, (), "no log evidence when build trust is lost")

    def test_log_evidence_only_with_trust_and_a_named_build(self):
        sid, launch = self.launched()
        (self.runtime_dir(launch) / "player.log").write_text("GPOSQ start\nAPI_KEY=abc123secret\n"
                                                             f"{self.home}/Library/Application Support/X/y.txt\n")
        r = self.stop(sid)
        (cand,) = r.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context), ("RUNTIME_EVIDENCE", "DIAGNOSTIC_RUNTIME"))
        log = (self.project / next(a.path for a in r.artifacts if a.artifact_id == "runtime-log")).read_text()
        self.assertNotIn("abc123secret", log)
        self.assertNotIn(str(self.home), log)
        self.assertNotIn("Support/X", log)
        sid, _ = self.launched()
        r = self.stop(sid, build_id=None)
        self.assertEqual(r.evidence_candidates, ())
        self.assertIn("PLAYER_EVIDENCE_WITHHELD", self.codes(r))

    def test_owners_and_recovery(self):
        sid, launch = self.launched()
        r = self.stop(sid, actor=OTHER)
        self.assertEqual(r.status, tdg.CONFLICT)
        self.assertIn("LIVE_SESSION_MISMATCH", self.codes(r))
        r = self.stop(sid, actor=OTHER, inputs={"recover_proven_gone": True})
        self.assertEqual(r.status, tdg.CONFLICT, "a running Player is never recovered by another owner")
        binding = json.loads((self.runtime_dir(launch) / "runtime-binding.json").read_text())
        self.os.remove(binding["player"]["pid"])
        self.os.remove(self.supervisor.supervisor)
        r = self.stop(sid, actor=OTHER, inputs={"recover_proven_gone": True})
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertIn("PLAYER_SESSION_RECOVERED", self.codes(r))
        self.assertIsNone(self.lease_record())
        self.assertEqual(self.appkit.calls, [])
        self.assertEqual(self.run_cap(c.STOP, session_id=sid, inputs={"recover_proven_gone": "yes"}).status,
                         tdg.CONFLICT)   # the session is already closed: verification fails first

    def test_an_unresolved_launch_is_never_signalled(self):
        sid, launch = self.launched()
        os.unlink(self.runtime_dir(launch) / "runtime-binding.json")
        self.os.remove(self.supervisor.supervisor)   # nothing will act on the abort
        with mock.patch.object(c, "STOP_GRACE_SECONDS", 0.2), mock.patch.object(c, "KILL_WAIT_SECONDS", 0.1):
            r = self.stop(sid)
        self.assertEqual(r.status, tdg.OUTCOME_UNKNOWN, self.text(r))
        self.assertIn("PLAYER_LAUNCH_UNRESOLVED", self.codes(r))
        self.assertEqual((self.appkit.calls, self.os.killed), ([], []))
        self.assertIsNotNone(self.lease_record())
        self.os.procs.clear()   # the Human quit the game
        r = self.stop(sid)
        self.assertEqual(r.status, tdg.SUCCESS, self.text(r))
        self.assertEqual(r.data["classification"], "GONE_UNOBSERVED")
        self.assertIsNone(self.lease_record())

    def test_classification_table(self):
        def rec(status, code=None, signal=None, reason=None, accepted=False, killed=False):
            return {"status": status, "code": code, "signal": signal, "stop": {"reason": reason,
                    "terminate_accepted": accepted, "kill_sent": killed}}
        cases = [
            (rec("EXITED", 0, reason="STOP_INTENT", accepted=True), "PROVEN", False, "GRACEFUL_STOP"),
            (rec("EXITED", 2, reason="STOP_INTENT", accepted=True), "GONE", False, "EXITED"),
            (rec("EXITED", 0), "GONE", False, "EXITED"),
            (rec("SIGNALED", signal=9, reason="STOP_INTENT", accepted=True, killed=True), "GONE", False, "FORCED_STOP"),
            (rec("SIGNALED", signal=9), "GONE", False, "CRASHED"),
            (rec("SIGNALED", signal=9), "GONE", True, "FORCED_STOP"),
            (rec("SIGNALED", signal=6), "GONE", False, "CRASHED"),
            (rec("NOT_STARTED"), "GONE", False, "GONE_UNOBSERVED"),
            (None, "GONE", False, "GONE_UNOBSERVED"),
            (None, "NOT_THIS_PROCESS", False, "GONE_UNOBSERVED"),
            (None, "GONE", True, "FORCED_STOP"),
            (None, "PROVEN", True, "OUTCOME_UNKNOWN"),
            (None, "UNPROVEN", False, "OUTCOME_UNKNOWN"),
        ]
        for exit_rec, identity, kill, expect in cases:
            with self.subTest(exit=exit_rec, identity=identity, kill=kill):
                self.assertEqual(pa.classify_stop(exit_rec, identity, kill, "X")[0], expect)

    def test_stop_inputs(self):
        sid, _ = self.launched()
        for value in ("yes", 1, None, "TRUE"):
            with self.subTest(value=value):
                self.assertEqual(self.stop(sid, inputs={"recover_proven_gone": value}).status, tdg.INVALID_REQUEST)
        self.assertEqual(self.stop(sid, inputs={"signal": "TERM"}).status, tdg.INVALID_REQUEST)
        self.assertEqual(self.stop(sid, build_id="build-req-other").status, tdg.INVALID_REQUEST)


# ---------------------------------------------------------------- K  the log sanitizer

class K_LogSanitizer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-log-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = tpaths.account_home()
        self.root = self.tmp / "My Project"
        self.runtime = self.root / ".game/gpos-runtime/tool-output/player/req-1"
        self.pairs = plogs.bases(self.root, self.runtime)

    def clean(self, line):
        return plogs.sanitize_line(line, self.pairs)

    def test_the_measured_application_support_fragment(self):
        line = f"persistent={self.home}/Library/Application Support/GposTest/GposPlayerQual1/qual-save.txt done"
        out = self.clean(line)
        self.assertEqual(out, "persistent=<path> done")
        self.assertNotIn("Support/", out)
        # the control: the alpha.20 rule alone leaves the fragment behind
        legacy = ABSOLUTE_PATH.sub("<path>", line.replace(str(self.home), "~"))
        self.assertIn(" Support/GposTest", legacy)

    def test_corpus(self):
        corpus = {
            f"log {self.home}/Library/Logs/GposTest/My Game/Player.log": "log <path>",
            "Mono path[0] = '/Volumes/My Disk/Game.app/Contents/Resources/Data/Managed'": "Mono path[0] = '<path>'",
            f"wrote {self.runtime}/player.log": "wrote player.log",
            f"read {self.root}/Assets/Data/x.txt": "read Assets/Data/x.txt",
            "API_KEY=supersecret123 token: abcdef": "API_KEY=[REDACTED] token: [REDACTED]",
            "ratio 3/4 at http://example.com/a": "ratio 3/4 at http://example.com/a",
            "/System/Library/Frameworks/Metal.framework loaded": "<path> loaded",
            "/private/var/folders/ab/cd/T/x.tmp": "<path>",
            f"home={self.home}": "home=~",
            "C:\\Users\\x\\file.txt": "<path>",
        }
        for line, expected in corpus.items():
            with self.subTest(line=line):
                self.assertEqual(self.clean(line), expected)
        self.assertEqual(len(self.clean("x" * 5000)), plogs.MAX_LINE_CHARS)

    def test_the_username_never_survives(self):
        user = self.home.name
        for line in (f"{self.home}/Library/Application Support/A B/C D/e.txt", f"home={self.home}",
                     f"/Users/{user}/Desktop/My File/x.log"):
            self.assertNotIn(f"/Users/{user}", self.clean(line))

    def test_bounds_and_truncation(self):
        log = self.tmp / "player.log"
        log.write_text("".join(f"line {i} API_KEY=s{i}\n" for i in range(50000)))
        text, facts = plogs.sanitized(log, self.root, self.runtime, max_read=200_000, max_out=50_000)
        self.assertLessEqual(len(text.encode()), 50_000)
        self.assertTrue(facts["read_truncated"])
        self.assertIn("line 49999", text)
        self.assertTrue(text.startswith("[GPOS:"))
        self.assertNotIn("API_KEY=s", text)
        self.assertEqual(plogs.sanitized(self.tmp / "absent.log", self.root, self.runtime), (None, None))
        os.symlink(log, self.tmp / "link.log")
        self.assertEqual(plogs.sanitized(self.tmp / "link.log", self.root, self.runtime), (None, None))


# ---------------------------------------------------------------- L  media validators

class L_Media(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-media-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def put(self, name, data):
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def test_png(self):
        p = self.put("ok.png", pf.png_bytes(40, 30))
        self.assertEqual(pcap.validate_png(p, 40, 30)["channels"], 4)
        good = pf.png_bytes(40, 30)
        bad = {
            "truncated": good[:-15], "signature": b"\x00" + good[1:], "trailing": good + b"x",
            "crc": good[:40] + bytes([good[40] ^ 1]) + good[41:],
        }
        for name, data in bad.items():
            with self.subTest(case=name):
                with self.assertRaises(pcap.MediaProblem):
                    pcap.validate_png(self.put(f"{name}.png", data), 40, 30)
        with self.assertRaises(pcap.MediaProblem):
            pcap.validate_png(p, 41, 30)
        with self.assertRaises(pcap.MediaProblem):
            pcap.validate_png(self.put("wide.png", pf.png_bytes(1921, 2)), 1921, 2)
        self.assertEqual(pcap.validate_png(self.put("edge.png", pf.png_bytes(1920, 2)), 1920, 2)["width"], 1920)
        with self.assertRaises(pcap.MediaProblem):
            pcap.validate_png(p, 40, 30, bound=100)

    def test_mp4(self):
        ok = self.put("ok.mp4", pf.mp4_bytes(5, 960, 596, 150))
        self.assertEqual(pcap.validate_mp4(ok, 5, 960, 596)["samples"], 150)
        cases = {"audio": pf.mp4_bytes(5, 960, 596, 150, audio=True), "no moov": pf.mp4_bytes(5, 960, 596, 150, moov=False),
                 "long": pf.mp4_bytes(7, 960, 596, 150), "short": pf.mp4_bytes(4, 960, 596, 150),
                 "starved": pf.mp4_bytes(5, 960, 596, 4), "codec": pf.mp4_bytes(5, 960, 596, 150, codec=b"hvc1"),
                 "truncated": pf.mp4_bytes(5, 960, 596, 150)[:-30]}
        for name, data in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(pcap.MediaProblem):
                    pcap.validate_mp4(self.put(f"{name}.mp4", data), 5, 960, 596)
        with self.assertRaises(pcap.MediaProblem):
            pcap.validate_mp4(ok, 5, 962, 596)


# ---------------------------------------------------------------- N  network

class N_Network(unittest.TestCase):
    def test_tool_inherent_with_disclosure(self):
        d = pa.DESCRIPTOR
        self.assertEqual(d.network, "TOOL_INHERENT")
        text = " ".join(d.network_disclosure)
        for phrase in ("no URL, host, endpoint, proxy or credential", "originates no network operation",
                       "may use the network", "No operating-system network confinement"):
            self.assertIn(phrase, text)

    def test_the_semantic_cannot_move(self):
        other = dataclasses.replace(pa.DESCRIPTOR, adapter_id="player-2",
                                    capabilities=tuple(dataclasses.replace(cap, id=cap.id.replace("player.", "player-2."))
                                                       for cap in pa.CAPABILITIES))
        problems = " ".join(p.message for p in tval.validate_descriptor(FW, other))
        self.assertIn("allowlisted only for ['unity', 'player']", problems)
        self.assertIn("detached process", problems)
        self.assertIn("host_location", problems)


if __name__ == "__main__":
    unittest.main(verbosity=1)
