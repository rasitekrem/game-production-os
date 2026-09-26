#!/usr/bin/env python3
"""Phase 2C-6A — Unity live Editor plane tests.

    python3 tests/test_unity_live.py

Fast groups (A–N) drive the GPOS side of the live plane against `unity_live_fake_bridge.FakeBridge`, a Python
stand-in that speaks the same file protocol; they need no Unity and prove GPOS-side behaviour only. The bridge's
own logic is covered by tests/test_unity_live_bridge_core.py (its C# core) and by the real groups (R*) here, which
open disposable synthetic Unity projects in lab-owned batch-mode Editors activated by the test-only testkit
package — never the Human's Editor, never a user project. The real groups stop with
UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A unless exactly one Hub Editor is installed, guard Unity's EditorPrefs by key
name and value hash (values are never printed) and the user's Package Manager configuration files by metadata.

GPOS_UNITY_TEST_FAST=1 (the mutation harness only) skips every group that starts a real Unity process.

Fast groups: A registration · B identity and project key · C bridge manifest · D installer · E IPC client ·
F status classification · G live-status · H attach and Human approval · I inspect and Play Mode ·
J deadlines, withdrawal and unknown outcomes · K detach, clean close and recovery · L batch and live on one
resource · M boundaries · N CLI.

Real groups: R1 one synthetic project through install, bridge start, Human-approval attach (the testkit presses
the production approval method for test-named owners only), inspection, Play Mode, Domain Reload, compile errors,
deadlines, withdrawal and unknown outcomes, the batch plane blocked by the session, owner detach, clean close,
crash, stale session and Human-approved recovery · R2 the frozen batch plane with the bridge installed (dormant).
"""

import ast
import dataclasses
import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import unity_fixture_builder as fixtures  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import leases as lease_mod  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Actor, Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.unity import UnityAdapter  # noqa: E402
from gpos.tools.unity import bridge_install as bi  # noqa: E402
from gpos.tools.unity import identity as ident  # noqa: E402
from gpos.tools.unity import live  # noqa: E402
from gpos.tools.unity import live_ipc as ipc  # noqa: E402
from gpos.tools.unity import live_status as ls  # noqa: E402
from unity_live_fake_bridge import FakeBridge  # noqa: E402

FW = load_framework()
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
FAST = os.environ.get("GPOS_UNITY_TEST_FAST") == "1"
EDITORS = UnityAdapter().discover()
EDITOR_VERSION, EDITOR = EDITORS[0] if len(EDITORS) == 1 else ("6000.5.8f1", None)
AGENT = Actor("AGENT", "claude-main")
OTHER_KIND = Actor("HUMAN", "claude-main")
OTHER_AGENT = Actor("AGENT", "codex-worker")
MUTATING = {c.id for c in UnityAdapter.descriptor.capabilities if c.mutating}


def real(test):
    return unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")(test)


class LiveCase(unittest.TestCase):
    """A fresh GPOS project with a synthetic Unity project in Game/ and the audited bridge installed."""

    install = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-live-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = self.tmp / "p"
        shutil.copytree(FIXTURE, self.p)
        fixtures.make_project(self.p / "Game", "pass", EDITOR_VERSION, EDITOR)
        self.game = self.p / "Game"
        if self.install:
            bi.install(self.p, self.game, bi.verify_source())
        self.bridges = []
        self.resource = f"EDITOR_PROJECT:{self.p}"

    def facts(self, pid):
        for b in self.bridges:
            if b.pid == pid:
                return b.facts()
        return ("GONE", None, "")

    def bridge(self, **kw):
        b = FakeBridge(self.p, self.game, EDITOR_VERSION, **kw)
        self.bridges.append(b)
        self.addCleanup(b.stop)
        return b.start()

    def registry(self):
        r = ToolRegistry(FW)
        r.register(UnityAdapter(live_seams={"facts": self.facts}))
        return r

    def run_cap(self, cap, actor=AGENT, **kw):
        kw.setdefault("inputs", {"unity_project": "Game"})
        if cap in MUTATING and not kw.get("dry_run"):
            kw.setdefault("allow_mutation", True)
        return execute(self.registry(), ExecutionRequest(
            adapter_id="unity", capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", "rev-1"),
            project_root=str(self.p), actor=actor, **kw))

    def attached(self, bridge=None, actor=AGENT):
        b = bridge or self.bridge()
        b.decide = "approve"
        result = self.run_cap(live.ATTACH, actor=actor, timeout=30)
        self.assertEqual(result.status, tdg.SUCCESS, self.messages(result))
        return b, result.data["session_id"]

    def holder(self):
        return lease_mod.holder(self.p, "unity", self.resource)

    def codes(self, result):
        return {d.code for d in result.diagnostics}

    def messages(self, result):
        return " | ".join(f"{d.code}: {d.message}" for d in result.diagnostics)

    def assertStatus(self, result, status, code=None):
        self.assertEqual(result.status, status, self.messages(result))
        if code:
            self.assertIn(code, self.codes(result))


def published(bridge, command):
    """How many requests for `command` were ever published to this bridge (waiting, claimed or withdrawn)."""
    count = 0
    for folder in ("requests", "claimed", "withdrawn"):
        for f in (bridge.live / folder).glob("*.json"):
            if json.loads(f.read_text()).get("command") == command:
                count += 1
    return count


# ---------------------------------------------------------------- A  registration

class A_Registration(unittest.TestCase):
    def test_one_adapter_two_planes(self):
        d = default_registry(FW).get("unity").descriptor
        self.assertEqual((d.adapter_id, d.adapter_kind, d.state_model, d.network), ("unity", "CLI", "STATEFUL",
                                                                                      "TOOL_INHERENT"))
        self.assertEqual(len(d.capabilities), 12)
        notes = " ".join(d.compatibility_notes)
        self.assertIn("One adapter, two planes", notes)
        self.assertIn("process-driven", notes)
        self.assertIn("fixed-bridge and session-driven", notes)
        batch = {c.id: c for c in d.capabilities if c.id in ("unity.inspect-project", "unity.run-editmode-tests",
                                                              "unity.run-playmode-tests")}
        self.assertEqual({c.state_model for c in batch.values()}, {"STATELESS"})
        self.assertEqual(batch["unity.run-editmode-tests"].effective_lease_mode, "EXECUTION")

    def test_live_declarations(self):
        caps = {c.id: c for c in UnityAdapter.descriptor.capabilities}
        expected = {
            live.INSTALL: ("DEPLOY", "MUTATING", "STATELESS", "EXECUTION", "OFFLINE_ANALYSIS"),
            live.STATUS: ("INSPECT", "READ_ONLY", "STATEFUL", "NONE", "EDITOR"),
            live.ATTACH: ("RUN", "MUTATING", "STATEFUL", "SESSION_OPEN", "EDITOR"),
            live.DETACH: ("RUN", "MUTATING", "STATEFUL", "SESSION_CLOSE", "EDITOR"),
            live.INSPECT: ("INSPECT", "READ_ONLY", "STATEFUL", "SESSION_REQUIRED", "EDITOR"),
            live.ENTER: ("RUN", "MUTATING", "STATEFUL", "SESSION_REQUIRED", "EDITOR"),
            live.PAUSE: ("RUN", "MUTATING", "STATEFUL", "SESSION_REQUIRED", "EDITOR"),
            live.RESUME: ("RUN", "MUTATING", "STATEFUL", "SESSION_REQUIRED", "EDITOR"),
            live.EXIT: ("RUN", "MUTATING", "STATEFUL", "SESSION_REQUIRED", "EDITOR"),
        }
        self.assertEqual(set(live.CAPABILITY_IDS), set(expected))
        for cid, (category, cls, state, mode, context) in expected.items():
            c = caps[cid]
            with self.subTest(cid):
                self.assertEqual((c.category, c.operation_class, c.state_model, c.effective_lease_mode,
                                  c.execution_context), (category, cls, state, mode, context))
                self.assertEqual((c.potential_evidence, c.artifact_kinds, c.requires_tool, c.resource_kind,
                                  c.input_kinds), ((), (), False, "EDITOR_PROJECT", ("unity_project",)))
        self.assertTrue(caps[live.INSTALL].dry_run_supported)
        for cid in (live.ENTER, live.PAUSE, live.RESUME, live.EXIT):
            self.assertIn("establishes no player-loop, frame, gameplay", caps[cid].description)

    def test_no_generic_or_forbidden_surface(self):
        names = " ".join(c.id for c in UnityAdapter.descriptor.capabilities)
        for word in ("execute", "method", "menu", "eval", "script", "launch", "start", "quit", "close", "focus",
                     "input", "author", "capture", "scene", "prefab", "asset", "refresh", "mcp"):
            self.assertNotIn(word, names.replace("playmode", "").replace("inspect", ""), word)


# ---------------------------------------------------------------- B  identity and project key

class B_Identity(LiveCase):
    install = False

    def test_project_key_is_deterministic_and_per_project(self):
        self.assertEqual(ident.project_key("Game"), hashlib.sha256(b"gpos.unity.live\0Game").hexdigest()[:16])
        other = self.p / "Tools" / "Other"
        fixtures.make_project(other, "pass", EDITOR_VERSION, EDITOR)
        keys = {ident.project_key(ident.relative(self.p, self.game)), ident.project_key(ident.relative(self.p, other))}
        self.assertEqual(len(keys), 2)
        self.assertEqual(ident.relative(self.p, self.p), ".")
        self.assertEqual(ident.live_dir(self.p, "k").relative_to(self.p).as_posix(), ".game/gpos-runtime/unity/live/k")

    def test_canonical_spelling_and_links(self):
        link = self.tmp / "alias"
        link.symlink_to(self.p)
        self.assertEqual(ident.canonical(link / "Game"), self.game)
        self.assertEqual(ident.relative(link, link / "Game"), "Game")
        self.assertIsNone(ident.root_spelling_problem(self.p))
        upper = Path(str(self.p).replace("/p", "/P"))
        if upper.exists():   # a case-insensitive file system: the other spelling is refused, not guessed
            self.assertEqual(ident.canonical(upper), self.p)
            self.assertIn("as it is on disk", ident.root_spelling_problem(upper))

    def test_gpos_root_discovery_is_bounded_and_link_free(self):
        self.assertEqual(ident.gpos_root_of(self.game), self.p)
        deep = self.p
        for i in range(ident.MAX_ASCENT + 1):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        self.assertIsNone(ident.gpos_root_of(deep))
        lonely = self.tmp / "lonely" / "Game"
        lonely.mkdir(parents=True)
        self.assertIsNone(ident.gpos_root_of(lonely))


# ---------------------------------------------------------------- C  bridge manifest

class C_Manifest(unittest.TestCase):
    def test_committed_package_matches_the_generated_manifest(self):
        import generate_live_bridge_manifest as gen
        self.assertEqual(gen.main(["--check"]), 0)
        manifest = bi.verify_source()
        self.assertEqual((manifest["package_id"], manifest["bridge_version"], manifest["protocol"]),
                         ("com.gpos.live-bridge", "1.0.0", "gpos.unity.live/1"))
        paths = {e["path"] for e in manifest["files"]}
        self.assertIn("package.json", paths)
        self.assertTrue(all(p + ".meta" in paths for p in paths if not p.endswith(".meta")))
        self.assertEqual(bi.digest(manifest["files"]), manifest["package_digest"])

    def test_a_tampered_source_is_refused(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = tmp / bi.PACKAGE_ID
        shutil.copytree(bi.SOURCE, copy)
        (copy / "Editor" / "Bridge.cs").write_text("// changed")
        with self.assertRaises(bi.BridgeSourceCorrupt):
            bi.verify_source(copy)
        with self.assertRaises(bi.BridgeSourceCorrupt):
            bad = tmp / "manifest.json"
            data = json.loads(bi.MANIFEST.read_text())
            data["files"][0]["sha256"] = "0" * 64
            bad.write_text(json.dumps(data))
            bi.load_manifest(bad)

    def test_package_holds_no_project_specific_value(self):
        for f in bi.SOURCE.rglob("*"):
            if f.is_file():
                text = f.read_text()
                for word in ("/Users/", "/private/", "Inven" + "igma", "GPOS_LIVE_BRIDGE_LAB"):
                    self.assertNotIn(word, text, f)

    def test_meta_guids_are_deterministic(self):
        import generate_live_bridge_manifest as gen
        guid = gen.guid("Editor/Bridge.cs")
        self.assertEqual(guid, hashlib.md5(b"com.gpos.live-bridge:Editor/Bridge.cs").hexdigest())
        self.assertIn(guid, (bi.SOURCE / "Editor" / "Bridge.cs.meta").read_text())


# ---------------------------------------------------------------- D  installer

class D_Installer(LiveCase):
    install = False

    def target(self):
        return self.game / "Packages" / bi.PACKAGE_ID

    def test_install_then_idempotent(self):
        manifest_before = (self.game / "Packages" / "manifest.json").read_bytes()
        result = self.run_cap(live.INSTALL)
        self.assertStatus(result, tdg.SUCCESS, "LIVE_BRIDGE_INSTALLED")
        self.assertTrue(result.mutation_performed)
        by_path = lambda entries: sorted(entries, key=lambda e: e["path"])
        self.assertEqual(by_path(bi.tree(self.target())[0]), by_path(bi.verify_source()["files"]))
        self.assertEqual((self.game / "Packages" / "manifest.json").read_bytes(), manifest_before)
        self.assertEqual(list(bi.tree(self.target())[1]), [])
        self.assertFalse(any((self.p / ".game/gpos-runtime/unity/install-staging").glob("*")))
        again = self.run_cap(live.INSTALL)
        self.assertStatus(again, tdg.SUCCESS, "LIVE_BRIDGE_ALREADY_INSTALLED")
        self.assertFalse(again.mutation_performed)
        self.assertEqual((result.evidence_candidates, again.evidence_candidates), ((), ()))
        self.assertEqual(result.provenance.execution_context, "OFFLINE_ANALYSIS")

    def test_anything_unexpected_is_untrusted_and_untouched(self):
        def installed():
            shutil.rmtree(self.target(), ignore_errors=True)
            if self.target().is_symlink() or self.target().is_file():
                self.target().unlink()
            bi.install(self.p, self.game, bi.verify_source())

        def modify():
            (self.target() / "Editor" / "Bridge.cs").write_text("// edited")

        def missing():
            (self.target() / "Editor" / "Ipc.cs").unlink()

        def extra():
            (self.target() / "Editor" / "Extra.cs").write_text("// extra")

        def extra_dir():
            (self.target() / "Editor" / "More").mkdir()

        def link():
            (self.target() / "Editor" / "Ipc.cs").unlink()
            (self.target() / "Editor" / "Ipc.cs").symlink_to(bi.SOURCE / "Editor" / "Ipc.cs")

        def target_link():
            shutil.rmtree(self.target())
            self.target().symlink_to(bi.SOURCE)

        def target_file():
            shutil.rmtree(self.target())
            self.target().write_text("x")
        for name, change in (("modified", modify), ("missing", missing), ("extra", extra), ("extra dir", extra_dir),
                             ("link", link), ("target link", target_link), ("target file", target_file)):
            with self.subTest(name):
                installed()
                change()
                before = sorted((str(p), p.is_symlink(), p.read_bytes() if p.is_file() and not p.is_symlink() else b"")
                                for p in [self.target(), *self.target().rglob("*")] if not self.target().is_symlink()) \
                    if not self.target().is_symlink() else []
                result = self.run_cap(live.INSTALL)
                self.assertStatus(result, tdg.CONFLICT, "LIVE_BRIDGE_UNTRUSTED")
                self.assertFalse(result.mutation_performed)
                after = sorted((str(p), p.is_symlink(), p.read_bytes() if p.is_file() and not p.is_symlink() else b"")
                               for p in [self.target(), *self.target().rglob("*")] if not self.target().is_symlink()) \
                    if not self.target().is_symlink() else []
                self.assertEqual(before, after)

    def test_open_project_dry_run_consent_and_sessions(self):
        (self.game / "Temp").mkdir()
        (self.game / "Temp" / "UnityLockfile").write_bytes(b"")
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
        self.assertFalse(self.target().exists())
        (self.game / "Temp" / "UnityLockfile").unlink()
        dry = self.run_cap(live.INSTALL, dry_run=True)
        self.assertStatus(dry, tdg.SUCCESS)
        self.assertTrue(dry.plan)
        self.assertFalse(self.target().exists())
        self.assertStatus(self.run_cap(live.INSTALL, allow_mutation=False), tdg.INVALID_REQUEST, "MUTATION_NOT_ALLOWED")
        lease_mod.acquire(self.p, "unity", self.resource, "AGENT:x", "t", scope=lease_mod.SESSION,
                          session={"session_id": "a" * 32})
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "LIVE_SESSION_HELD")
        self.assertFalse(self.target().exists())

    def test_a_project_the_batch_preflight_refuses_is_refused(self):
        (self.game / "Packages" / "manifest.json").write_text(json.dumps(
            {"dependencies": {"com.example.remote": "https://example.com/x.git"}}))
        self.assertStatus(self.run_cap(live.INSTALL), tdg.INVALID_REQUEST, "ENGINE_PROJECT_UNSUPPORTED")
        self.assertFalse(self.target().exists())


# ---------------------------------------------------------------- E  IPC client

class E_IpcClient(LiveCase):
    def test_publication_is_atomic_bounded_and_strict(self):
        b = self.bridge()
        b.mode = "stall"
        ch = ipc.Channel(b.live)
        rid = ipc.publish(ch, "status", {}, "AGENT:x", b.boot)
        self.assertEqual([n for n in os.listdir(ch.requests) if n.startswith(".")], [])
        body = json.loads((ch.requests / f"{rid}.json").read_text())
        self.assertEqual(set(body), {"schema", "request_id", "session_id", "owner", "boot_id", "command", "args",
                                     "issued_utc", "start_deadline_utc"})
        issued = ls.parse_utc(body["issued_utc"])
        self.assertAlmostEqual((ls.parse_utc(body["start_deadline_utc"]) - issued).total_seconds(), 30, delta=0.01)
        with self.assertRaises(ValueError):
            ipc.publish(ch, "status", {}, "AGENT:x", b.boot, start_seconds=121)
        for _ in range(ipc.MAX_QUEUED - 1):
            ipc.publish(ch, "status", {}, "AGENT:x", b.boot)
        with self.assertRaises(ipc.ChannelProblem):
            ipc.publish(ch, "status", {}, "AGENT:x", b.boot)

    def test_garbled_or_linked_responses_are_refused(self):
        b = self.bridge()
        ch = ipc.Channel(b.live)

        def valid(rid, status="OK"):
            return json.dumps({"schema": ipc.RESPONSE_SCHEMA, "request_id": rid, "status": status, "code": None,
                               "message": None, "boot_id": b.boot, "generation": 1, "session_id": "", "utc": "x",
                               "data": None})
        rid = uuid.uuid4().hex
        (ch.responses / f"{rid}.json").write_text(valid(rid))
        self.assertEqual(ipc.read_response(ch, rid)["status"], "OK")
        for make in (lambda r: valid(r)[:-1] + ', "status": "REFUSED"}', lambda r: '{"schema":"x"}',
                     lambda r: "NaN", lambda r: "[1]", lambda r: valid(r, "MAYBE")):
            rid = uuid.uuid4().hex
            (ch.responses / f"{rid}.json").write_text(make(rid))
            with self.subTest(make(rid)[:40]):
                with self.assertRaises(ipc.ChannelProblem):
                    ipc.read_response(ch, rid)
        rid = uuid.uuid4().hex
        elsewhere = self.tmp / "planted.json"
        elsewhere.write_text(valid(rid))
        (ch.responses / f"{rid}.json").symlink_to(elsewhere)   # a well-formed answer, but through a link
        with self.assertRaises(ipc.ChannelProblem):
            ipc.read_response(ch, rid)

    def test_a_linked_live_directory_is_refused(self):
        real = self.tmp / "elsewhere"
        real.mkdir()
        link = ident.live_dir(self.p, "0" * 16)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(real)
        with self.assertRaises(ipc.ChannelProblem):
            ipc.Channel(link)


# ---------------------------------------------------------------- F  status classification (pure)

class F_Classification(unittest.TestCase):
    SID, BOOT = "a" * 32, "b" * 32

    def holder(self, acquired="2020-01-01T00:00:00Z"):
        return {"scope": "SESSION", "owner_id": "AGENT:x", "acquired_at": acquired,
                "session": {"session_id": self.SID, "boot_id": self.BOOT}}

    def state(self, boot=None, session=None, age=1.0, bridge_state="READY", owner="AGENT:x"):
        boot = boot or self.BOOT
        session = self.SID if session is None else session
        return {"bridge": {"boot_id": boot, "state": bridge_state, "session_id": session, "owner": owner},
                "heartbeat": {"boot_id": boot, "session_id": session}, "heartbeat_age": age, "problems": []}

    def test_table(self):
        cases = [
            ("LIVE", self.state(), ls.ALIVE, ls.ALIVE),
            ("UNRESPONSIVE", self.state(age=300), ls.ALIVE, ls.ALIVE),        # heartbeat age alone is never stale
            ("UNRESPONSIVE", self.state(age=None), ls.UNKNOWN, ls.UNKNOWN),   # nothing proven either way
            ("UNRESPONSIVE", self.state(), ls.UNKNOWN, ls.UNKNOWN),
            ("CLOSED", self.state(bridge_state="CLOSED"), ls.GONE, ls.GONE),
            ("STALE", self.state(bridge_state="CLOSED", session=""), ls.GONE, ls.GONE),   # CLOSED for another session
            ("STALE", self.state(bridge_state="CLOSED"), ls.REUSED, ls.REUSED),        # pid reuse is never a clean close
            ("STALE", self.state(), ls.GONE, ls.GONE),                                   # crashed: no CLOSED
            ("UNRESPONSIVE", self.state(bridge_state="CLOSED"), ls.ALIVE, ls.ALIVE),   # CLOSED, process still there
            ("STALE", self.state(boot="c" * 32, session=""), ls.ALIVE, ls.ALIVE),       # another boot serves it
            ("STALE", self.state(session=""), ls.ALIVE, ls.ALIVE),                      # same boot, no longer bound
        ]
        for expected, state, lease_editor, bridge_editor in cases:
            with self.subTest(expected=expected, state=state, lease=lease_editor):
                self.assertEqual(ls.classify(self.holder(), state, lease_editor, bridge_editor)[0], expected)

    def test_binding_in_progress_is_not_stale(self):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        self.assertEqual(ls.classify(self.holder(now), self.state(session=""), ls.ALIVE, ls.ALIVE)[0], "UNRESPONSIVE")

    def test_process_identity(self):
        start = "2026-09-26T01:25:57.1234567Z"
        started = ls.parse_utc(start)
        hub = "/Applications/Unity/Hub/Editor/6000.5.8f1/Unity.app/Contents/MacOS/Unity -projectPath /x"
        self.assertEqual(ls.identity(("ALIVE", started.replace(microsecond=0), hub), start), ls.ALIVE)
        self.assertEqual(ls.identity(("ALIVE", started + datetime.timedelta(seconds=5), hub), start), ls.REUSED)
        self.assertEqual(ls.identity(("ALIVE", started, "/usr/bin/python3 x"), start), ls.REUSED)
        self.assertEqual(ls.identity(("GONE", None, ""), start), ls.GONE)
        self.assertEqual(ls.identity(("UNKNOWN", None, ""), start), ls.UNKNOWN)
        self.assertEqual(ls.identity(("ALIVE", started, hub), None), ls.UNKNOWN)


# ---------------------------------------------------------------- G  live-status

class G_LiveStatus(LiveCase):
    def test_status_before_and_after_attach(self):
        s = self.run_cap(live.STATUS)
        self.assertStatus(s, tdg.SUCCESS)
        self.assertEqual((s.data["bridge"]["installed"], s.data["bridge"]["running_state"], s.data["session"]),
                         ("EXACT", None, None))
        b, sid = self.attached()
        s = self.run_cap(live.STATUS)
        self.assertEqual((s.data["bridge"]["running_state"], s.data["bridge"]["compatible"]), ("READY", True))
        self.assertEqual((s.data["session"]["classification"], s.data["session"]["session_id"],
                          s.data["session"]["owner"]), ("LIVE", sid, "AGENT:claude-main"))
        self.assertFalse(s.mutation_performed)
        before = list(b.claimed_ids)          # live-status sends nothing to the Editor
        self.run_cap(live.STATUS, actor=OTHER_AGENT)
        self.assertEqual(b.claimed_ids, before)

    def test_status_of_an_uninstalled_or_tampered_bridge(self):
        shutil.rmtree(self.game / "Packages" / bi.PACKAGE_ID)
        self.assertEqual(self.run_cap(live.STATUS).data["bridge"]["installed"], "ABSENT")
        bi.install(self.p, self.game, bi.verify_source())
        (self.game / "Packages" / bi.PACKAGE_ID / "Editor" / "Ipc.cs").write_text("//")
        s = self.run_cap(live.STATUS)
        self.assertEqual(s.data["bridge"]["installed"], "UNTRUSTED")
        self.assertIn("Editor/Ipc.cs: modified", s.data["bridge"]["differences"])


# ---------------------------------------------------------------- H  attach and Human approval

class H_Attach(LiveCase):
    def test_approval_first_then_lease_then_bind(self):
        b = self.bridge()
        seen = {}

        def at_approval(proposal):
            seen["lease_while_pending"] = self.holder()
        b.decide, b.on_approve = "approve", at_approval
        result = self.run_cap(live.ATTACH, timeout=30)
        self.assertStatus(result, tdg.SUCCESS, "LIVE_SESSION_ATTACHED")
        self.assertIsNone(seen["lease_while_pending"])      # no lease existed while the Human decided
        record = self.holder()
        self.assertEqual((record["scope"], record["owner_id"]), ("SESSION", "AGENT:claude-main"))
        self.assertEqual(record["session"]["session_id"], result.data["session_id"])
        self.assertEqual(record["session"]["boot_id"], b.boot)
        self.assertEqual((b.session, b.owner), (result.data["session_id"], "AGENT:claude-main"))
        self.assertTrue(result.mutation_performed)
        self.assertEqual(result.evidence_candidates, ())
        self.assertEqual(result.provenance.execution_context, "EDITOR")

    def test_no_approval_means_nothing_held(self):
        b = self.bridge()
        b.decide = "reject"
        rejected = self.run_cap(live.ATTACH, timeout=30)
        self.assertStatus(rejected, tdg.CANCELLED, "LIVE_APPROVAL_REJECTED")
        b.decide = None
        waited = self.run_cap(live.ATTACH, timeout=2)
        self.assertStatus(waited, tdg.CANCELLED, "LIVE_APPROVAL_NOT_GRANTED")
        self.assertIsNone(self.holder())
        self.assertEqual(b.session, "")
        pending = [p for p in b.proposals.values() if p["state"] == "PENDING"]
        self.assertEqual(pending, [])                        # the unapproved proposal was abandoned, not left usable
        self.assertFalse(rejected.mutation_performed or waited.mutation_performed)

    def test_a_writer_that_arrives_after_approval_wins_and_nothing_binds(self):
        b = self.bridge()
        b.decide = "approve"
        b.on_approve = lambda proposal: lease_mod.acquire(self.p, "unity", self.resource, "batch-run", "t")
        result = self.run_cap(live.ATTACH, timeout=30)
        self.assertStatus(result, tdg.CONFLICT, "LEASE_CONFLICT")
        self.assertEqual(b.session, "")
        self.assertEqual({p["state"] for p in b.proposals.values()}, {"ABANDONED"})   # the grant can never be used
        self.assertEqual(self.holder()["owner_id"], "batch-run")                      # never force-broken

    def test_bind_refused_releases_and_unknown_bind_keeps_the_lease(self):
        b = self.bridge()
        b.decide = "approve"
        original = b.cmd_bind
        b.cmd_bind = lambda rid, args, owner: b.respond(rid, "REFUSED", "LEASE_NOT_HELD")
        self.assertStatus(self.run_cap(live.ATTACH, timeout=30), tdg.CONFLICT, "LIVE_BIND_REFUSED")
        self.assertIsNone(self.holder())
        b.cmd_bind = original

        def claim_bind_only(rid, args, owner):
            b.mode = "claim-only"
        b.cmd_bind = claim_bind_only
        unknown = self.run_cap(live.ATTACH, timeout=60)
        self.assertStatus(unknown, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        time.sleep(0.5)                                            # anything published again would be claimed by now
        self.assertEqual(published(b, "bind"), 2)                 # one refused earlier, one unknown: never republished
        self.assertTrue(unknown.mutation_performed)
        self.assertEqual(self.holder()["scope"], "SESSION")   # a possibly bound Editor is never left without its lease

    def test_preconditions_refuse_before_any_proposal(self):
        cases = []
        shutil.rmtree(self.game / "Packages" / bi.PACKAGE_ID)
        cases.append(("absent", self.run_cap(live.ATTACH, timeout=5), "LIVE_BRIDGE_ABSENT"))
        bi.install(self.p, self.game, bi.verify_source())
        cases.append(("no bridge", self.run_cap(live.ATTACH, timeout=5), "LIVE_BRIDGE_UNAVAILABLE"))
        b = self.bridge(digest="0" * 64)
        cases.append(("digest", self.run_cap(live.ATTACH, timeout=5), "LIVE_BRIDGE_INCOMPATIBLE"))
        b.stop()
        b2 = self.bridge()
        b2.editor_version = "6000.0.1f1"
        b2.publish("READY")
        cases.append(("editor version", self.run_cap(live.ATTACH, timeout=5), "LIVE_BRIDGE_INCOMPATIBLE"))
        b2.stop()
        b3 = self.bridge()
        b3.alive = False
        cases.append(("process unproven", self.run_cap(live.ATTACH, timeout=5), "LIVE_BRIDGE_UNAVAILABLE"))
        for name, result, code in cases:
            with self.subTest(name):
                self.assertIn(code, self.codes(result), self.messages(result))
                self.assertIsNone(self.holder())
        self.assertEqual([p for bb in self.bridges for p in bb.proposals], [])

    def test_a_bridge_for_another_project_or_with_an_old_heartbeat_is_refused(self):
        b = self.bridge()
        b.decide = "approve"
        other = self.tmp / "other"
        other.mkdir()
        b.project = other                                   # the bridge in this directory claims another project
        b.publish("READY")
        r = self.run_cap(live.ATTACH, timeout=5)
        self.assertIn("LIVE_PROJECT_IDENTITY_MISMATCH", self.codes(r), self.messages(r))
        b.project = self.game
        b.publish("READY")
        b.stop()                                            # the Editor stops ticking; its files stay
        old = time.time() - 60
        os.utime(b.live / "heartbeat.json", (old, old))
        r = self.run_cap(live.ATTACH, timeout=5)
        self.assertIn("LIVE_BRIDGE_UNAVAILABLE", self.codes(r), self.messages(r))
        self.assertIsNone(self.holder())
        self.assertEqual(b.proposals, {})

    def test_an_existing_session_is_never_taken_over(self):
        b, sid = self.attached()
        for actor in (AGENT, OTHER_AGENT, OTHER_KIND):
            with self.subTest(actor=actor):
                self.assertStatus(self.run_cap(live.ATTACH, actor=actor, timeout=5), tdg.CONFLICT, "LIVE_SESSION_HELD")
        self.assertEqual(self.holder()["session"]["session_id"], sid)


# ---------------------------------------------------------------- I  inspect and Play Mode

class I_SessionCommands(LiveCase):
    def test_inspect_and_the_play_mode_cycle(self):
        b, sid = self.attached()
        inspect = self.run_cap(live.INSPECT, session_id=sid)
        self.assertStatus(inspect, tdg.SUCCESS)
        self.assertEqual(inspect.data["scene_count"], 1)
        self.assertFalse(inspect.mutation_performed)
        for cap, phase in ((live.ENTER, "PLAYING"), (live.PAUSE, "PAUSED"), (live.RESUME, "PLAYING"),
                           (live.EXIT, "EDIT")):
            with self.subTest(cap):
                r = self.run_cap(cap, session_id=sid)
                self.assertStatus(r, tdg.SUCCESS)
                self.assertTrue(r.mutation_performed)
                self.assertEqual(r.data["state"]["phase"], phase)
                self.assertIn("establishes no player-loop, frame, gameplay", r.data["limitation"])
                self.assertEqual(r.evidence_candidates, ())
        self.assertEqual(b.executed, ["enter-playmode", "pause", "resume", "exit-playmode"])

    def test_only_the_owner_acts_on_the_session(self):
        b, sid = self.attached()
        for actor in (OTHER_KIND, OTHER_AGENT):
            with self.subTest(actor=actor):
                r = self.run_cap(live.ENTER, actor=actor, session_id=sid)
                self.assertStatus(r, tdg.CONFLICT, "LIVE_SESSION_MISMATCH")
        self.assertStatus(self.run_cap(live.ENTER, session_id="f" * 32), tdg.CONFLICT, "LIVE_SESSION_MISMATCH")
        self.assertEqual(b.executed, [])

    def test_refusals_map_and_mutate_nothing(self):
        b, sid = self.attached()
        r = self.run_cap(live.PAUSE, session_id=sid)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_STATE_REFUSED")
        self.assertFalse(r.mutation_performed)
        b.busy = "COMPILING"
        r = self.run_cap(live.ENTER, session_id=sid)
        self.assertStatus(r, tdg.CONFLICT, "EDITOR_BUSY")
        self.assertFalse(r.mutation_performed)
        b.busy = None
        b.cmd_enter_playmode = lambda rid, args, owner: b.respond(rid, "FAILED", "PLAYMODE_ENTER_ABORTED",
                                                                   {"state": {"phase": "EDIT"}})
        r = self.run_cap(live.ENTER, session_id=sid)
        self.assertStatus(r, tdg.FAILED, "LIVE_TRANSITION_FAILED")
        self.assertEqual(b.executed, [])


# ---------------------------------------------------------------- J  deadlines, withdrawal, unknown outcomes

class J_Outcomes(LiveCase):
    def test_withdrawn_before_claim_never_runs(self):
        b, sid = self.attached()
        b.mode = "stall"
        r = self.run_cap(live.PAUSE, session_id=sid, timeout=1)
        self.assertStatus(r, tdg.CANCELLED, "LIVE_REQUEST_WITHDRAWN")
        self.assertFalse(r.mutation_performed)
        self.assertEqual(len(os.listdir(b.live / "withdrawn")), 1)
        b.mode = "normal"
        time.sleep(0.5)                       # the bridge serves again: the withdrawn request is gone for good
        self.assertEqual(b.executed, [])

    def test_claimed_without_answer_is_outcome_unknown_and_never_retried(self):
        b, sid = self.attached()
        b.mode = "claim-only"
        r = self.run_cap(live.ENTER, session_id=sid, timeout=5)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertEqual(r.exit_code_for_cli, 9)
        self.assertTrue(r.mutation_performed)
        self.assertIn("re-read unity.live-status", self.messages(r))
        time.sleep(0.5)
        self.assertEqual(published(b, "enter-playmode"), 1)       # published once, never again
        b.mode = "interrupt"
        r = self.run_cap(live.RESUME, session_id=sid)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertNotIn(tdg.TIMED_OUT, {d.cls for d in r.diagnostics})

    def test_an_expired_request_is_refused_by_the_bridge(self):
        b, sid = self.attached()
        ch = ipc.Channel(b.live)
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
        rid = ipc.publish(ch, "pause", {}, "AGENT:claude-main", b.boot, sid, start_seconds=30, now=past)
        deadline = time.monotonic() + 5
        while ipc.read_response(ch, rid) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(ipc.read_response(ch, rid)["code"], "LIVE_REQUEST_EXPIRED")
        self.assertEqual(b.executed, [])


# ---------------------------------------------------------------- K  detach, clean close, recovery

class K_Detach(LiveCase):
    def test_owner_detach_unbinds_then_releases(self):
        b, sid = self.attached()
        claims = len(b.claimed_ids)
        other = self.run_cap(live.DETACH, actor=OTHER_KIND, session_id=sid)
        self.assertStatus(other, tdg.CONFLICT, "LIVE_SESSION_MISMATCH")
        self.assertEqual(b.session, sid)
        self.assertEqual(len(b.claimed_ids), claims)              # refused by GPOS before the Editor is asked
        r = self.run_cap(live.DETACH, session_id=sid)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_SESSION_DETACHED")
        self.assertEqual((b.session, self.holder()), ("", None))

    def test_clean_close_releases_without_approval_or_break(self):
        b, sid = self.attached()
        b.close()
        r = self.run_cap(live.DETACH, session_id=sid)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_SESSION_DETACHED")
        self.assertIn("closed cleanly", self.messages(r))
        self.assertIsNone(self.holder())
        self.assertFalse((self.p / ".game/gpos-runtime/leases/broken.log").exists())

    def test_clean_close_needs_every_condition(self):
        b, sid = self.attached()
        b.close()
        other = self.run_cap(live.DETACH, actor=OTHER_AGENT, session_id=sid, timeout=5)   # not the owner
        self.assertStatus(other, tdg.CONFLICT, "LIVE_SESSION_STALE")
        self.assertIsNotNone(self.holder())
        b.alive = True   # CLOSED written, but the process is (still) there: not proven gone
        self.assertStatus(self.run_cap(live.DETACH, session_id=sid, timeout=5), tdg.UNAVAILABLE,
                          "LIVE_SESSION_UNRESPONSIVE")
        self.assertIsNotNone(self.holder())

    def test_the_clean_close_predicate_needs_every_fact(self):
        record = {"owner_id": "AGENT:a", "session": {"session_id": "s", "boot_id": "b"}}
        closed = {"bridge": {"state": "CLOSED", "boot_id": "b", "session_id": "s", "owner": "AGENT:a"}}
        self.assertTrue(live._cleanly_closed(record, closed, ls.GONE))
        for editor in (ls.ALIVE, ls.REUSED, ls.UNKNOWN):
            self.assertFalse(live._cleanly_closed(record, closed, editor), editor)
        for key, value in (("state", "READY"), ("boot_id", "c"), ("session_id", "t"), ("owner", "AGENT:b")):
            changed = {"bridge": dict(closed["bridge"], **{key: value})}
            self.assertFalse(live._cleanly_closed(record, changed, ls.GONE), key)

    def test_a_recovery_grant_for_another_session_breaks_nothing(self):
        b, sid = self.attached()
        b.crash()
        new = self.bridge()
        new.decide = "approve"
        new.cmd_consume_recovery = lambda rid, args, owner: new.respond(rid, "OK", "RECOVERY_GRANTED", {
            "stale_session_id": "f" * 32, "stale_boot_id": b.boot, "stale_owner": "AGENT:claude-main",
            "approving_boot_id": new.boot})
        r = self.run_cap(live.DETACH, actor=OTHER_AGENT, session_id=sid, timeout=30)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_GRANT_INVALID")
        self.assertEqual(self.holder()["session"]["session_id"], sid)

    def test_crash_is_stale_and_never_auto_recovered(self):
        b, sid = self.attached()
        b.crash()
        s = self.run_cap(live.STATUS)
        self.assertEqual(s.data["session"]["classification"], "STALE")
        for cap in (live.INSPECT, live.ENTER):
            self.assertStatus(self.run_cap(cap, session_id=sid), tdg.CONFLICT, "LIVE_SESSION_STALE")
        r = self.run_cap(live.DETACH, session_id=sid, timeout=5)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_SESSION_STALE")
        self.assertIsNotNone(self.holder())                       # no bridge to ask a Human: nothing is broken
        batch = self.run_cap(live.INSTALL)
        self.assertStatus(batch, tdg.CONFLICT, "LIVE_SESSION_HELD")

    def test_human_approved_recovery_then_a_fresh_attach(self):
        b, sid = self.attached()
        b.crash()
        new = self.bridge()
        new.decide = "reject"
        rejected = self.run_cap(live.DETACH, actor=OTHER_AGENT, session_id=sid, timeout=30)
        self.assertStatus(rejected, tdg.CANCELLED, "LIVE_APPROVAL_REJECTED")
        self.assertIsNotNone(self.holder())
        new.decide = "approve"
        r = self.run_cap(live.DETACH, actor=OTHER_AGENT, session_id=sid, timeout=30)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_SESSION_RECOVERED")
        self.assertEqual((r.data["attached"], new.session), (False, ""))   # recovery never attaches
        self.assertIsNone(self.holder())
        log = [json.loads(line) for line in (self.p / ".game/gpos-runtime/leases/broken.log").read_text().splitlines()]
        self.assertEqual(log[-1]["broken_by"], "AGENT:codex-worker")
        self.assertIn("Human-approved stale-session recovery", log[-1]["reason"])
        self.assertEqual(log[-1]["previous_holder"]["session"]["session_id"], sid)
        kinds = [p["kind"] for p in new.proposals.values()]
        self.assertEqual(kinds, ["RECOVER", "RECOVER"])
        self.attached(new)                                     # a new attach needs its own ATTACH approval
        self.assertEqual([p["kind"] for p in new.proposals.values()], ["RECOVER", "RECOVER", "ATTACH"])

    def test_pid_reuse_is_never_a_clean_close(self):
        b, sid = self.attached()
        b.close()
        b.alive = True
        b.started = b.started - datetime.timedelta(hours=1)    # the id now belongs to another process
        r = self.run_cap(live.DETACH, session_id=sid, timeout=5)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_SESSION_STALE")
        self.assertIsNotNone(self.holder())


# ---------------------------------------------------------------- L  batch and live on one resource

class L_OneResource(LiveCase):
    def test_a_live_session_blocks_batch_before_unity_launches(self):
        from test_unity_adapter import StandIn, nunit
        b, sid = self.attached()
        stand_in = StandIn(self.tmp / "standin", version=EDITOR_VERSION, results=nunit(), log="ok\n")
        result = execute(stand_in.registry(), ExecutionRequest(
            adapter_id="unity", capability_id="unity.run-editmode-tests", subject=Subject("FEATURE", "F-1", "r"),
            project_root=str(self.p), inputs={"unity_project": "Game"}, allow_mutation=True))
        self.assertStatus(result, tdg.CONFLICT, "LIVE_SESSION_HELD")
        self.assertNotIn("LEASE_STALE", self.codes(result))
        self.assertFalse(stand_in.record.exists())             # no Unity process was started
        self.assertEqual(self.holder()["session"]["session_id"], sid)

    def test_a_batch_writer_blocks_an_attach(self):
        self.bridge()
        held = lease_mod.acquire(self.p, "unity", self.resource, "batch-run", "t")
        self.assertStatus(self.run_cap(live.ATTACH, timeout=5), tdg.CONFLICT, "LEASE_CONFLICT")
        self.assertEqual([p for b in self.bridges for p in b.proposals], [])
        lease_mod.release(self.p, held)


# ---------------------------------------------------------------- M  boundaries

class M_Boundaries(unittest.TestCase):
    BRIDGE = [p for p in bi.SOURCE.rglob("*.cs")]

    def source(self):
        """The bridge's C# without comments, so a comment that names a forbidden mechanism is not a match."""
        import re
        code = "\n".join(p.read_text() for p in self.BRIDGE)
        return re.sub(r"//[^\n]*", "", code)

    def test_the_bridge_has_no_generic_or_forbidden_mechanism(self):
        text = self.source()
        for token in ("ExecuteMenuItem", "executeMethod", "System.Reflection", "Assembly.Load", "Activator.",
                      "GetMethod(", "Invoke(", "Process.Start", "Socket", "HttpListener", "TcpListener", "WebSocket",
                      "UnityWebRequest", "delayCall", "QueuePlayerLoopUpdate", "EditorApplication.Exit(",
                      "OpenProject", "Focus()", "FocusWindowIfItsOpen", "EditorPrefs", "SetEditorSettings",
                      "GetEnvironmentVariable", "GPOS_LIVE_BRIDGE_LAB", "InternalsVisibleTo", "GetInstanceID"):
            self.assertNotIn(token, text, token)

    def test_import_workers_and_batch_mode_never_host_it(self):
        text = (bi.SOURCE / "Editor" / "Bridge.cs").read_text()
        ctor = text[text.index("static LiveBridge()"):text.index("static void Activate()")]
        lines = [line.strip() for line in ctor.splitlines() if line.strip().startswith("if")]
        self.assertTrue(lines[0].startswith("if (AssetDatabase.IsAssetImportWorkerProcess()) return;"), lines)
        self.assertTrue(lines[1].startswith("if (Application.isBatchMode) return;"), lines)
        self.assertIn("if (activated || AssetDatabase.IsAssetImportWorkerProcess()) return;", text)

    def test_the_window_is_opened_only_from_the_menu(self):
        text = self.source()
        self.assertEqual(text.count("GetWindow<"), 1)
        self.assertIn('[MenuItem("GPOS/Live Session")]', text)
        self.assertEqual(text.count("Approvals.Decide("), 2)   # the two buttons
        self.assertNotIn("approve", " ".join(__import__("re").findall(r'\{ "([a-z-]+)", new Spec', text)))

    def test_python_live_modules_start_no_process_and_use_no_network(self):
        for name in ("live.py", "live_ipc.py", "live_status.py", "identity.py", "bridge_install.py"):
            tree = ast.parse((ROOT / "gpos" / "tools" / "unity" / name).read_text())
            names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            self.assertEqual(names & {"subprocess", "socket", "http", "urllib", "ctypes", "multiprocessing"}, set(),
                             name)


# ---------------------------------------------------------------- N  CLI

class N_Cli(LiveCase):
    def test_session_id_and_exit_codes_through_the_cli(self):
        from gpos.tools import cli
        from gpos.tools import registry as reg_mod
        b, sid = self.attached()
        original = reg_mod.register_production_adapters

        def patched(registry):
            registry.register(UnityAdapter(live_seams={"facts": self.facts}))
            return registry
        cli.register_production_adapters = patched
        try:
            out = io.StringIO()
            code = cli.main(["execute", "--adapter", "unity", "--capability", live.PAUSE, "--project", str(self.p),
                             "--subject-kind", "FEATURE", "--subject-ref", "F-1", "--input", "unity_project=Game",
                             "--actor", "AGENT:claude-main", "--session-id", sid, "--allow-mutation",
                             "--format", "json"], stdout=out)
            self.assertEqual(code, tdg.EXIT_FOR[tdg.CONFLICT], out.getvalue())   # not playing
            b.mode = "claim-only"
            out = io.StringIO()
            code = cli.main(["execute", "--adapter", "unity", "--capability", live.ENTER, "--project", str(self.p),
                             "--subject-kind", "FEATURE", "--subject-ref", "F-1", "--input", "unity_project=Game",
                             "--actor", "AGENT:claude-main", "--session-id", sid, "--allow-mutation", "--timeout", "5",
                             "--format", "json"], stdout=out)
            self.assertEqual(code, 9, out.getvalue())
            self.assertEqual(json.loads(out.getvalue())["result"]["status"], "OUTCOME_UNKNOWN")
        finally:
            cli.register_production_adapters = original



# ================================================================ real synthetic Unity (lab-owned batch Editors)

import plistlib  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402  (TEST-ONLY: starts and stops lab-owned Editors; production never does)

from gpos.tools import process as tproc  # noqa: E402
from gpos.tools.unity import adapter as ua  # noqa: E402

TESTKIT = ROOT / "tests" / "unity_live_testkit" / "com.gpos.live-bridge-testkit"
ACCEPTED_PREFS = {"LastUsedProjectPath", "kProjectBasePath", "kWorkspacePath", "UnityConnectUrlConfiguration",
                  "unity.editor_session_count", "unity.editor_sessionid"}
EDITOR_PREFS = Path.home() / "Library" / "Preferences" / "com.unity3d.UnityEditor5.x.plist"
UPM_CONFIGS = (Path.home() / ".upmconfig.toml", Path("/etc/upmconfig.toml"))
APPROVER = Actor("AGENT", "testkit-approve-1")
_STATE = {"editors": []}


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
    for c in UPM_CONFIGS:
        try:
            st = c.stat()
            out.append((str(c), True, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((str(c), False, None, None))
    return out


def setUpModule():
    if FAST:
        return
    if len(EDITORS) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A: {len(EDITORS)} Hub Unity Editors found; exactly "
                           f"one is required")
    if EDITOR_PREFS.exists():
        _STATE["prefs"] = prefs_snapshot()
    _STATE["upm"] = upm_config_state()
    _STATE["work"] = Path(tempfile.mkdtemp(prefix="gpos-live-real-")).resolve()


def tearDownModule():
    try:
        for editor in _STATE["editors"]:
            editor.stop()
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


class LabEditor:
    """One lab-owned batch-mode Editor on a synthetic project. Only this process is ever signalled."""

    def __init__(self, root, game):
        self.root, self.game, self.proc, self.launches = Path(root), Path(game), None, 0
        self.live = ident.live_dir(self.root, ident.project_key(ident.relative(self.root, self.game)))
        _STATE["editors"].append(self)

    def bridge(self):
        try:
            return ipc.read_bounded(self.live / "bridge.json")
        except ipc.ChannelProblem:
            return None

    def launch(self, timeout=900):
        self.launches += 1
        ws = self.root.parent / f"ws-{self.launches}"
        ws.mkdir(parents=True, exist_ok=True)
        for n in ("upm-user.toml", "upm-global.toml"):
            (ws / n).write_text("")
        cache = self.root / ".game" / "gpos-runtime" / "unity" / "upm-cache"
        cache.mkdir(parents=True, exist_ok=True)
        env = ua.upm_environment(ws, cache).build()
        old = (self.bridge() or {}).get("boot_id")
        self.proc = subprocess.Popen(
            [EDITOR, "-batchmode", "-projectPath", str(self.game), "-logFile", str(ws / "editor.log"),
             "-upmLogFile", str(ws / "upm.log"), "-cacheServerEnableDownload", "false",
             "-cacheServerEnableUpload", "false"],
            cwd=str(ws), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            b = self.bridge()
            if b and b.get("state") == "READY" and b.get("boot_id") != old and b.get("editor_pid") == self.proc.pid:
                return b
            if self.proc.poll() is not None:
                raise AssertionError(f"the lab Editor exited with {self.proc.returncode} before its bridge was ready")
            time.sleep(0.5)
        raise AssertionError("the lab Editor's bridge did not become READY")

    def trigger(self, name):
        d = self.game / "Temp" / "gpos-testkit"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("")

    def heartbeat(self):
        try:
            return ipc.read_bounded(self.live / "heartbeat.json") or {}
        except ipc.ChannelProblem:
            return {}

    def wait(self, predicate, timeout=120, step=0.2):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(step)
        return None

    def quit(self, timeout=180):
        self.trigger("quit")
        self.proc.wait(timeout)

    def kill(self):
        os.kill(self.proc.pid, signal.SIGKILL)   # the lab's own child process, never anything else
        self.proc.wait(60)

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.quit(120)
            except Exception:
                self.kill()


def real_project(name, testkit=True):
    p = _STATE["work"] / name / "p"
    shutil.copytree(FIXTURE, p)
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


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R1_RealLiveSession(unittest.TestCase):
    """One synthetic project through the whole live lifecycle, in order, with lab-owned batch Editors."""

    @classmethod
    def setUpClass(cls):
        cls.p = real_project("session")
        cls.game = cls.p / "Game"
        cls.editor = LabEditor(cls.p, cls.game)
        cls.resource = f"EDITOR_PROJECT:{cls.p}"
        cls.state = {}

    @classmethod
    def tearDownClass(cls):
        cls.editor.stop()

    def run_cap(self, cap, actor=APPROVER, **kw):
        return real_request(self.p, cap, actor, **kw)

    def assertStatus(self, result, status, code=None):
        text = " | ".join(f"{d.code}: {d.message}" for d in result.diagnostics)
        self.assertEqual(result.status, status, text)
        if code:
            self.assertIn(code, {d.code for d in result.diagnostics}, text)

    def holder(self):
        return lease_mod.holder(self.p, "unity", self.resource)

    def classification(self):
        session = self.run_cap(live.STATUS).data["session"] or {}
        return session.get("classification"), session.get("reasons")

    def attach(self, actor=APPROVER):
        r = self.run_cap(live.ATTACH, actor=actor, timeout=120)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_SESSION_ATTACHED")
        return r.data["session_id"]

    def test_01_install_into_the_closed_project(self):
        r = self.run_cap(live.INSTALL)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_BRIDGE_INSTALLED")
        self.assertStatus(self.run_cap(live.INSTALL), tdg.SUCCESS, "LIVE_BRIDGE_ALREADY_INSTALLED")

    def test_02_the_bridge_starts_with_its_identity(self):
        b = self.editor.launch()
        manifest = bi.verify_source()
        self.assertEqual((b["protocol"], b["bridge_version"], b["package_digest"]),
                         (bi.PROTOCOL, bi.BRIDGE_VERSION, manifest["package_digest"]))
        self.assertEqual((b["editor_pid"], b["editor_version"], b["project_key"]),
                         (self.editor.proc.pid, EDITOR_VERSION, ident.project_key("Game")))
        self.assertTrue(ident.same_directory(b["project_path"], self.game))
        self.assertTrue(ident.same_directory(b["gpos_root"], self.p))
        events = [json.loads(line) for line in (self.editor.live / "events.jsonl").read_text().splitlines()]
        self.assertEqual({e["boot_id"] for e in events}, {b["boot_id"]})   # import workers never host the bridge
        seq = self.editor.heartbeat().get("seq", 0)
        self.assertTrue(self.editor.wait(lambda: self.editor.heartbeat().get("seq", 0) > seq + 3, 30))
        s = self.run_cap(live.STATUS)
        self.assertEqual((s.data["bridge"]["running_state"], s.data["bridge"]["compatible"], s.data["session"]),
                         ("READY", True, None))

    def test_03_attach_needs_a_human_approval(self):
        bystander = self.run_cap(live.ATTACH, actor=Actor("AGENT", "testkit-bystander"), timeout=5)
        self.assertStatus(bystander, tdg.CANCELLED, "LIVE_APPROVAL_NOT_GRANTED")
        self.assertIsNone(self.holder())
        rejected = self.run_cap(live.ATTACH, actor=Actor("AGENT", "testkit-reject-1"), timeout=60)
        self.assertStatus(rejected, tdg.CANCELLED, "LIVE_APPROVAL_REJECTED")
        self.assertIsNone(self.holder())
        self.state["sid"] = self.attach()
        record = self.holder()
        self.assertEqual((record["scope"], record["owner_id"]), ("SESSION", "AGENT:testkit-approve-1"))
        self.assertEqual(self.editor.bridge()["session_id"], self.state["sid"])
        self.assertEqual(self.classification()[0], "LIVE", self.classification())

    def test_04_inspect_and_play_mode(self):
        sid = self.state["sid"]
        i = self.run_cap(live.INSPECT, session_id=sid)
        self.assertStatus(i, tdg.SUCCESS)
        self.assertEqual((i.data["editor"]["phase"], i.data["session"]["session_id"]), ("EDIT", sid))
        self.assertStatus(self.run_cap(live.PAUSE, session_id=sid), tdg.CONFLICT, "LIVE_STATE_REFUSED")
        for cap, phase in ((live.ENTER, "PLAYING"), (live.PAUSE, "PAUSED"), (live.RESUME, "PLAYING")):
            r = self.run_cap(cap, session_id=sid)
            self.assertStatus(r, tdg.SUCCESS)
            self.assertEqual(r.data["state"]["phase"], phase)
            self.assertEqual(r.evidence_candidates, ())
        self.assertStatus(self.run_cap(live.ENTER, session_id=sid), tdg.CONFLICT, "LIVE_STATE_REFUSED")
        self.assertStatus(self.run_cap(live.EXIT, session_id=sid), tdg.SUCCESS)
        self.assertStatus(self.run_cap(live.INSPECT, actor=Actor("HUMAN", "testkit-approve-1"), session_id=sid),
                          tdg.CONFLICT, "LIVE_SESSION_MISMATCH")

    def test_05_domain_reload_keeps_the_session(self):
        sid = self.state["sid"]
        before = self.editor.heartbeat()["generation"]
        self.editor.trigger("reload")
        self.assertTrue(self.editor.wait(lambda: self.editor.heartbeat().get("generation", 0) > before, 120))
        self.assertEqual(self.editor.heartbeat()["session_id"], sid)
        self.assertEqual(self.classification()[0], "LIVE", self.classification())
        self.assertStatus(self.run_cap(live.INSPECT, session_id=sid), tdg.SUCCESS)

    def test_06_compile_errors_abort_entering_play_mode(self):
        sid = self.state["sid"]
        broken = self.game / "Assets" / "GposBroken.cs"
        broken.write_text("public class GposBroken { void M() { int x = ; } }\n")
        self.editor.trigger("refresh")
        state = lambda: self.editor.heartbeat().get("state") or {}
        self.assertTrue(self.editor.wait(lambda: state().get("compilation_failed") and state().get("phase") == "EDIT",
                                         180))
        r = self.run_cap(live.ENTER, session_id=sid)
        self.assertStatus(r, tdg.FAILED, "LIVE_TRANSITION_FAILED")
        broken.unlink()
        Path(str(broken) + ".meta").unlink(missing_ok=True)
        self.editor.trigger("refresh")
        self.assertTrue(self.editor.wait(lambda: state().get("compilation_failed") is False and
                                         state().get("phase") == "EDIT", 180))

    def test_07_deadlines_withdrawal_and_unknown_outcomes(self):
        sid, boot = self.state["sid"], self.editor.bridge()["boot_id"]
        ch = ipc.Channel(self.editor.live)
        self.editor.trigger("block-4000")
        time.sleep(1.0)
        rid = ipc.publish(ch, "pause", {}, "AGENT:testkit-approve-1", boot, sid, start_seconds=1.0)
        self.assertTrue(self.editor.wait(lambda: ipc.read_response(ch, rid), 30))
        self.assertEqual(ipc.read_response(ch, rid)["code"], "LIVE_REQUEST_EXPIRED")
        self.editor.trigger("block-4000")
        time.sleep(1.0)
        w = self.run_cap(live.RESUME, session_id=sid, timeout=1)
        self.assertStatus(w, tdg.CANCELLED, "LIVE_REQUEST_WITHDRAWN")
        time.sleep(4)
        unknown = ipc.call(ch, "enter-playmode", {"timeout_s": 60}, "AGENT:testkit-approve-1", boot, sid, wait=0.3)
        self.assertEqual(unknown.outcome, ipc.UNKNOWN)
        late = self.editor.wait(lambda: ipc.read_response(ch, unknown.request_id), 120)
        self.assertEqual(late["status"], "OK")                 # it ran once, after the client stopped waiting
        self.assertEqual(self.run_cap(live.INSPECT, session_id=sid).data["editor"]["phase"], "PLAYING")
        self.assertStatus(self.run_cap(live.EXIT, session_id=sid), tdg.SUCCESS)

    def test_08_a_live_session_blocks_the_batch_plane_before_launch(self):
        with Recorder() as rec:
            r = real_request(self.p, "unity.run-editmode-tests", actor=None)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_SESSION_HELD")
        self.assertEqual([s for s in rec.specs if "-runTests" in s.argv], [])
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT)

    def test_09_owner_detach(self):
        sid = self.state["sid"]
        self.assertStatus(self.run_cap(live.DETACH, actor=Actor("AGENT", "codex-worker"), session_id=sid),
                          tdg.CONFLICT, "LIVE_SESSION_MISMATCH")
        self.assertStatus(self.run_cap(live.DETACH, session_id=sid), tdg.SUCCESS, "LIVE_SESSION_DETACHED")
        self.assertIsNone(self.holder())
        self.assertEqual(self.editor.bridge()["session_id"], "")

    def test_10_clean_close_releases_without_recovery(self):
        sid = self.attach()
        self.editor.quit()
        self.assertEqual(self.editor.bridge()["state"], "CLOSED")
        self.assertEqual(self.run_cap(live.STATUS).data["session"]["classification"], "CLOSED")
        r = self.run_cap(live.DETACH, session_id=sid)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_SESSION_DETACHED")
        self.assertIsNone(self.holder())
        self.assertFalse((self.p / ".game/gpos-runtime/leases/broken.log").exists())

    def test_11_crash_stale_session_and_human_approved_recovery(self):
        self.editor.launch()
        sid = self.attach()
        old = self.editor.bridge()
        ch = ipc.Channel(self.editor.live)
        rid = ipc.publish(ch, "enter-playmode", {"timeout_s": 60}, "AGENT:testkit-approve-1", old["boot_id"], sid)
        self.assertTrue(self.editor.wait(lambda: (ch.claimed / f"{rid}.json").exists(), 30, 0.01))
        self.editor.kill()                                    # a crash in the middle of a transition
        self.assertEqual(self.run_cap(live.STATUS).data["session"]["classification"], "STALE")
        self.assertStatus(self.run_cap(live.DETACH, session_id=sid, timeout=5), tdg.CONFLICT, "LIVE_SESSION_STALE")
        self.assertStatus(real_request(self.p, "unity.run-editmode-tests", actor=None), tdg.CONFLICT,
                          "LIVE_SESSION_HELD")
        orphan = ipc.publish(ch, "pause", {}, "AGENT:testkit-approve-1", old["boot_id"], sid, start_seconds=120)
        new = self.editor.launch()                            # the Human reopens the project
        self.assertNotEqual(new["boot_id"], old["boot_id"])
        self.assertEqual(new["session_id"], "")               # a new boot never adopts the old session
        interrupted = self.editor.wait(lambda: ipc.read_response(ch, rid), 60)
        self.assertEqual((interrupted["status"], interrupted["code"]), ("INTERRUPTED", "NOT_REPLAYED"))
        refused = self.editor.wait(lambda: ipc.read_response(ch, orphan), 60)
        self.assertEqual((refused["status"], refused["code"]), ("REFUSED", "BOOT_MISMATCH"))
        self.assertEqual(self.run_cap(live.STATUS).data["session"]["classification"], "STALE")
        recovery = self.run_cap(live.DETACH, actor=Actor("AGENT", "testkit-approve-recovery"), session_id=sid,
                                timeout=120)
        self.assertStatus(recovery, tdg.SUCCESS, "LIVE_SESSION_RECOVERED")
        self.assertIsNone(self.holder())
        self.assertEqual(self.editor.bridge()["session_id"], "")   # recovery never attaches
        log = [json.loads(x) for x in (self.p / ".game/gpos-runtime/leases/broken.log").read_text().splitlines()]
        self.assertEqual((log[-1]["broken_by"], log[-1]["previous_holder"]["session"]["session_id"]),
                         ("AGENT:testkit-approve-recovery", sid))
        again = self.attach()                                  # a fresh attach needs its own approval
        self.assertStatus(self.run_cap(live.DETACH, session_id=again), tdg.SUCCESS, "LIVE_SESSION_DETACHED")

    def test_12_unity_left_the_audited_package_untouched(self):
        self.editor.stop()
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.EXACT, []))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R2_BatchPlaneDormancy(unittest.TestCase):
    def test_the_frozen_batch_plane_runs_with_the_bridge_installed_and_the_bridge_stays_dormant(self):
        p = real_project("dormant", testkit=False)
        bi.install(p, p / "Game", bi.verify_source())
        r = real_request(p, "unity.run-editmode-tests", actor=None)
        self.assertEqual(r.status, tdg.SUCCESS, [d.message for d in r.diagnostics])
        self.assertEqual([c.evidence_type for c in r.evidence_candidates], ["TEST_EVIDENCE"])
        self.assertEqual(r.data["total"], 2)
        self.assertFalse((p / ".game" / "gpos-runtime" / "unity" / "live").exists())
        self.assertEqual(bi.inspect_target(p / "Game", bi.verify_source()), (bi.EXACT, []))


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
