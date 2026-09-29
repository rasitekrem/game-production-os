#!/usr/bin/env python3
"""Phase 2C-6C — Unity source synchronization, compilation facts and the batch project-lock proof (alpha.20).

    python3 tests/test_unity_sources.py

Fast groups need no Unity. A–C and W drive the GPOS side against `unity_live_fake_bridge.FakeBridge` (a stand-in that
speaks the file protocol and models Unity's compilation counters; it proves GPOS behaviour only): declarations, the
source path grammar, the exact arguments GPOS sends, how every bridge answer maps to a result (mutation_performed
once an import began), the compilation facts GPOS derives, diagnostics sanitization, and wait-ready — observation only,
ready only after a compilation newer than the sync's causal baseline has settled. D pins the frozen bridge 1.3.0
release and upgrades it. E scans the bridge's and GPOS's sources for forbidden mechanisms and pins every import call.
L covers the read-only project-lock proof with a scripted OS (every row of the decision table), with the real macOS
calls on this host (flock held by a child process, links, FIFOs, the real process list) and through the adapter.

Real groups open disposable synthetic Unity projects (unity_fixture_builder.make_prefab_project) in lab-owned
batch-mode Editors activated by the test-only testkit; this test file itself stands in for the external tooling that
writes source files. R9 is one source/compile session (exact imports, new .meta files, asmdef and asmref, syntax,
type and warning compilations, the fix, coalesced imports, generations, wait-ready, paging, sanitized paths, the
journal lost at an Editor restart). R10 covers Domain Reload and identity (session survival, requests during a
compilation and during a reload, catalog invalidation, renames, a move with .meta, deletion folder sync with its
side effects, D1/D2, the bounds, links, no global Refresh, a missing script). R11 is the permanent end-to-end
qualification of the whole loop. R12 is the real stale-lock contract. The real groups stop with
UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A unless exactly one Hub Editor is installed, and guard Unity's EditorPrefs and
the user's Package Manager configuration files.

GPOS_UNITY_TEST_FAST=1 (the mutation harness only) skips every group that starts a real Unity process.
"""

import ast
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_unity_authoring as ta  # noqa: E402
import test_unity_live as tl  # noqa: E402
import unity_fixture_builder as fixtures  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry  # noqa: E402
from gpos.tools.unity import UnityAdapter  # noqa: E402
from gpos.tools.unity import adapter as ua  # noqa: E402
from gpos.tools.unity import assets as A  # noqa: E402
from gpos.tools.unity import authoring as au  # noqa: E402
from gpos.tools.unity import bridge_install as bi  # noqa: E402
from gpos.tools.unity import live  # noqa: E402
from gpos.tools.unity import prefabs as P  # noqa: E402
from gpos.tools.unity import project_lock as pl  # noqa: E402
from gpos.tools.unity import sources as S  # noqa: E402
from test_unity_adapter import StandIn, STANDIN_VERSION, UnityCase, hold_lock, nunit  # noqa: E402
from test_unity_live import EDITOR, EDITOR_VERSION, EDITORS, FAST, FIXTURE, LiveCase  # noqa: E402

EDITOR_SOURCE = bi.SOURCE / "Editor"
FROZEN_TAG_13 = "v1.0.0-alpha.19"
FROZEN_DIGEST_13 = "acdbb1c84e9be9e8fbd10bb6b2c09e4dbfae3e4d4e28ad74c4f5cc708a4f3f47"
SCENE = "Assets/Scenes/Main.unity"
ok = ta.ok


def mb(name, body="    public int a;", ns=None):
    head, tail = (f"namespace {ns} {{\n", "}\n") if ns else ("", "")
    return f"using UnityEngine;\n{head}public class {name} : MonoBehaviour\n{{\n{body}\n}}\n{tail}"


# ---------------------------------------------------------------- A  declarations

class A_Declarations(unittest.TestCase):
    def test_four_fixed_source_capabilities(self):
        caps = {c.id: c for c in UnityAdapter.descriptor.capabilities}
        self.assertEqual(len(caps), 45)
        self.assertEqual(S.CAPABILITY_IDS, ("unity.live-sync-sources", "unity.live-compilation-status",
                                            "unity.live-compilation-diagnostics", "unity.live-wait-ready"))
        others = set(au.CAPABILITY_IDS) | set(A.CAPABILITY_IDS) | set(P.CAPABILITY_IDS) | set(live.CAPABILITY_IDS)
        self.assertFalse(set(S.CAPABILITY_IDS) & others)
        expected = {S.SYNC: ("TRANSFORM", "MUTATING", (120.0, 300.0), ("unity_project", "sources", "deleted")),
                    S.STATUS: ("INSPECT", "READ_ONLY", (30.0, 120.0), ("unity_project",)),
                    S.DIAGNOSTICS: ("INSPECT", "READ_ONLY", (30.0, 120.0),
                                    ("unity_project", "generation", "severity", "assembly", "page")),
                    S.WAIT: ("INSPECT", "READ_ONLY", (120.0, 300.0), ("unity_project", "sync_generation"))}
        for cid, (category, cls, timeout, kinds) in expected.items():
            c = caps[cid]
            with self.subTest(cid):
                self.assertEqual((c.category, c.operation_class, c.state_model, c.effective_lease_mode, c.execution_context,
                                  c.requires_tool, c.resource_kind, c.dry_run_supported, c.potential_evidence),
                                 (category, cls, "STATEFUL", "SESSION_REQUIRED", "EDITOR", False, "EDITOR_PROJECT", False, ()))
                self.assertEqual((c.timeout.default, c.timeout.maximum), timeout)
                self.assertEqual(c.input_kinds, kinds)
                self.assertEqual(c.single_writer_required, cls == "MUTATING")
                self.assertIn("never code, content, a folder, a compile command or a global Refresh", " ".join(c.notes))
                if cls == "READ_ONLY":
                    self.assertEqual(c.side_effect_scope, "NONE")
        sync = caps[S.SYNC].side_effect_scope
        for words in ("imports exactly the named existing sources", ".meta", "never Assets itself", "listed within the bounded walk",
                      "may be reconciled unlisted"):
            self.assertIn(words, sync)
        self.assertIn("Imports, compiles, refreshes, reloads, restarts and replays nothing", caps[S.WAIT].description)

    def test_no_compile_write_or_refresh_capability(self):
        for c in UnityAdapter.descriptor.capabilities:
            words = set(re.split(r"[.-]", c.id))
            self.assertFalse(words & {"compile", "refresh", "reimport", "import", "execute", "move", "rename", "code", "write",
                                      "source", "script", "reload", "restart"}, c.id)
        self.assertEqual([c.id for c in UnityAdapter.descriptor.capabilities if "sources" in c.id], [S.SYNC])
        notes = " ".join(UnityAdapter.descriptor.compatibility_notes)
        self.assertIn("writes no file, takes no code or content, requests no compilation and never refreshes globally", notes)
        self.assertIn("no other platform inherits that rule without its own measurement and review", notes)


# ---------------------------------------------------------------- B  inputs

class B_Inputs(unittest.TestCase):
    def refused(self, cap, inputs, code):
        with self.assertRaises(S.InputProblem) as ctx:
            S.parse_inputs(cap, inputs)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))

    def test_source_paths_are_exact_source_files_below_assets(self):
        for good in ("Assets/Foo.cs", "Assets/Scripts/Player.cs", "Assets/Game/Editor/Tool.cs", "Assets/Game/Game.Runtime.asmdef",
                     "Assets/Game/Sub/Sub.asmref", "Assets/A (1)/b+c-d_e.cs"):
            self.assertEqual(S.source_path("p", good), good)
        for bad in ("", "Assets", "assets/Foo.cs", "/Assets/Foo.cs", "Packages/com.x/A.cs", "Library/A.cs", "Assets/Foo.CS",
                    "Assets/Foo.txt", "Assets/Foo.cs.meta", "Assets/Foo.dll", "Assets/.hidden/A.cs", "Assets/a./A.cs",
                    "Assets/a~/A.cs", "Assets/../A.cs", "Assets/a/../A.cs", "Assets//A.cs", "Assets/a\\b/A.cs",
                    "Assets/StreamingAssets/A.cs", "Assets/x/Editor Default Resources/A.cs", "Assets/cvs/A.cs",
                    "Assets/GposAssetTxn-0123/A.cs", "Assets/a/.cs", "Assets/a/A..B.cs", "Assets/a/A .cs",
                    "Assets/" + "/".join("abcdefghijklmnopq") + "/Deep.cs", "Assets/" + "a" * 600 + ".cs", 5, None):
            with self.subTest(bad):
                with self.assertRaises(S.InputProblem) as ctx:
                    S.source_path("p", bad)
                self.assertEqual(ctx.exception.code, "LIVE_SOURCE_PATH_INVALID")

    def test_a_sync_is_strict_json_path_lists_named_once(self):
        args = S.parse_inputs(S.SYNC, {"unity_project": "Game", "sources": '["Assets/A/X.cs"]'})
        self.assertEqual(args, {"sources": ["Assets/A/X.cs"], "deleted": []})
        self.assertEqual(S.parse_inputs(S.SYNC, {"deleted": '["Assets/A/Y.cs"]'}), {"sources": [], "deleted": ["Assets/A/Y.cs"]})
        self.refused(S.SYNC, {}, "INVALID_TOOL_REQUEST")
        self.refused(S.SYNC, {"sources": "[]", "deleted": "[]"}, "INVALID_TOOL_REQUEST")
        self.refused(S.SYNC, {"sources": "Assets/A/X.cs"}, "LIVE_VALUE_INVALID")
        self.refused(S.SYNC, {"sources": '{"path": "Assets/A/X.cs"}'}, "LIVE_SOURCE_PATH_INVALID")
        self.refused(S.SYNC, {"sources": '[["Assets/A/X.cs"]]'}, "LIVE_SOURCE_PATH_INVALID")
        self.refused(S.SYNC, {"sources": '["Assets/A/X.cs", "Assets/a/x.cs"]'}, "LIVE_SOURCE_PATH_INVALID")
        self.refused(S.SYNC, {"sources": '["Assets/A/X.cs"]', "deleted": '["Assets/A/X.cs"]'}, "LIVE_SOURCE_PATH_INVALID")
        many = json.dumps([f"Assets/A/F{i}.cs" for i in range(65)])
        self.refused(S.SYNC, {"sources": many}, "LIVE_SOURCE_PATH_INVALID")
        self.refused(S.SYNC, {"sources": json.dumps([f"Assets/A/F{i}.cs" for i in range(40)]),
                              "deleted": json.dumps([f"Assets/B/F{i}.cs" for i in range(25)])}, "LIVE_SOURCE_SYNC_LIMIT")
        for key in ("folder", "code", "content", "text", "method", "extension", "operation", "folders"):
            self.refused(S.SYNC, {"sources": '["Assets/A/X.cs"]', key: "x"}, "INVALID_TOOL_REQUEST")

    def test_status_diagnostics_and_wait_inputs(self):
        self.assertEqual(S.parse_inputs(S.STATUS, {"unity_project": "Game"}), {})
        self.refused(S.STATUS, {"page": "1"}, "INVALID_TOOL_REQUEST")
        self.assertEqual(S.parse_inputs(S.DIAGNOSTICS, {"generation": "3", "severity": "ERROR", "assembly": "Game.Runtime",
                                                        "page": "2"}),
                         {"generation": 3, "severity": "ERROR", "assembly": "Game.Runtime", "page": 2})
        self.assertEqual(S.parse_inputs(S.DIAGNOSTICS, {}), {"generation": None, "severity": None, "assembly": None, "page": None})
        for bad in ({"severity": "INFO"}, {"severity": "error"}, {"assembly": "A B"}, {"assembly": "a" * 129},
                    {"assembly": ".*"}, {"generation": "0"}, {"generation": "x"}, {"page": "-1"}, {"page": "10001"},
                    {"query": "CS0246"}, {"regex": "."}, {"file": "Assets/A.cs"}):
            with self.subTest(bad):
                with self.assertRaises(S.InputProblem):
                    S.parse_inputs(S.DIAGNOSTICS, bad)
        self.assertEqual(S.parse_inputs(S.WAIT, {"sync_generation": "12"}), {"sync_generation": 12})
        self.assertEqual(S.parse_inputs(S.WAIT, {}), {"sync_generation": None})
        for bad in ({"sync_generation": "0"}, {"sync_generation": "-1"}, {"timeout": "10"}, {"compile": "true"}):
            with self.subTest(bad):
                with self.assertRaises(S.InputProblem):
                    S.parse_inputs(S.WAIT, bad)


# ---------------------------------------------------------------- C  mapping (fake bridge)

class SourceCase(LiveCase):
    def call(self, cap, sid=None, **inputs):
        timeout = inputs.pop("timeout", None)
        inputs.setdefault("unity_project", "Game")
        kw = {"inputs": inputs, "session_id": sid or self.sid}
        if timeout:
            kw["timeout"] = timeout
        return self.run_cap(cap, **kw)

    def setUp(self):
        super().setUp()
        self.b, self.sid = self.attached()

    def claimed_commands(self):
        out = []
        for f in sorted((self.b.live / "claimed").glob("*.json"), key=lambda f: f.stat().st_mtime):
            out.append(json.loads(f.read_text())["command"])
        return out


class C_Mapping(SourceCase):
    def test_a_sync_sends_exactly_its_paths(self):
        r = self.call(S.SYNC, sources='["Assets/Loop/A.cs", "Assets/Loop/Loop.asmdef"]', deleted='["Assets/Old/B.cs"]')
        self.assertStatus(r, tdg.SUCCESS, "LIVE_SOURCES_SYNCED")
        self.assertEqual(self.b.source_calls[-1], {"sources": ["Assets/Loop/A.cs", "Assets/Loop/Loop.asmdef"],
                                                   "deleted": ["Assets/Old/B.cs"]})
        self.assertTrue(r.mutation_performed)
        self.assertEqual(r.evidence_candidates, ())
        self.assertIn("Compilation and Domain Reload follow as Unity decides", r.data["limitation"])
        self.assertEqual((r.data["sync_generation"], r.data["compile_started_before_sync"]), (1, 0))
        (d,) = [d for d in r.diagnostics if d.code == "LIVE_SOURCES_SYNCED"]
        self.assertEqual(json.loads(d.details), {"sync_generation": 1, "compile_started_before_sync": 0})
        body = json.loads(sorted((self.b.live / "claimed").glob("*.json"), key=lambda f: f.stat().st_mtime)[-1].read_text())
        self.assertEqual((body["schema"], body["command"], body["session_id"]), ("gpos.unity.live.request/5", "sync-sources", self.sid))
        self.assertEqual(self.call(S.SYNC, sources='["Assets/Loop/A.cs"]').data["sync_generation"], 2)

    def test_every_side_effect_is_disclosed(self):
        def reply(args):
            status, code, data = self.b.default_sync(args)
            data["sources"][0].update(known_before=False, meta_created=True)
            data["folders"] = [{"folder": "Assets/Old", "parent_widened": True, "entries_before": 3, "entries_after": 4,
                                "imported_new": ["Assets/Old/Note.txt"], "imported_new_count": 1,
                                "removed": ["Assets/Old/B.cs", "Assets/Old/Stale.cs"], "removed_count": 2,
                                "meta_created": ["Assets/Old/Note.txt.meta"], "meta_created_count": 1,
                                "meta_removed": ["Assets/Old/Stale.cs.meta"], "meta_removed_count": 1,
                                "meta_changed": [], "meta_changed_count": 0}]
            return status, code, data
        self.b.source_reply = reply
        r = self.call(S.SYNC, sources='["Assets/Loop/A.cs"]', deleted='["Assets/Old/B.cs"]')
        self.assertStatus(r, tdg.SUCCESS, "LIVE_SOURCE_SYNC_SIDE_EFFECTS")
        (d,) = [d for d in r.diagnostics if d.code == "LIVE_SOURCE_SYNC_SIDE_EFFECTS"]
        self.assertEqual(json.loads(d.details), {"meta_created": 2, "other_imported": 1, "other_removed": 1,
                                                 "meta_removed_or_changed": 1})
        self.assertEqual(r.data["folders"][0]["imported_new"], ["Assets/Old/Note.txt"])
        self.b.source_reply = None
        self.assertNotIn("LIVE_SOURCE_SYNC_SIDE_EFFECTS", self.codes(self.call(S.SYNC, sources='["Assets/Loop/A.cs"]')))

    def test_nothing_imported_is_no_mutation(self):
        self.b.source_reply = lambda args: ("OK", None, {"mutation_started": False, "sync_generation": None, "imports": 0,
                                                         "sources": [], "folders": [], "disclosure": None,
                                                         "deleted": [{"path": "Assets/Old/B.cs", "state": "ALREADY_SYNCHRONIZED",
                                                                      "folder": None, "parent_widened": False}]})
        r = self.call(S.SYNC, deleted='["Assets/Old/B.cs"]')
        self.assertStatus(r, tdg.SUCCESS, "LIVE_SOURCES_SYNCED")
        self.assertFalse(r.mutation_performed)
        self.assertIn("already synchronized", self.messages(r))

    def test_refusals_before_an_import_are_no_mutation(self):
        table = {"SOURCE_PATH_INVALID": ("LIVE_SOURCE_PATH_INVALID", tdg.INVALID_REQUEST),
                 "SOURCE_SYNC_LIMIT": ("LIVE_SOURCE_SYNC_LIMIT", tdg.INVALID_REQUEST),
                 "SOURCE_SYNC_REFUSED": ("LIVE_SOURCE_SYNC_REFUSED", tdg.CONFLICT),
                 "EDITOR_BUSY": ("EDITOR_BUSY", tdg.CONFLICT)}
        for bridge_code, (code, status) in table.items():
            with self.subTest(bridge_code):
                self.b.source_reply = lambda args, c=bridge_code: ("REFUSED", c, {"mutation_started": False})
                r = self.call(S.SYNC, deleted='["Assets/Foo.cs"]')
                self.assertStatus(r, status, code)
                self.assertFalse(r.mutation_performed)

    def test_a_failure_after_the_first_import_is_a_mutation(self):
        self.b.source_reply = lambda args: ("FAILED", "SOURCE_SYNC_INCOMPLETE", {"mutation_started": True, "sync_generation": 4})
        r = self.call(S.SYNC, deleted='["Assets/Old/B.cs"]')
        self.assertStatus(r, tdg.FAILED, "LIVE_SOURCE_SYNC_INCOMPLETE")
        self.assertTrue(r.mutation_performed)
        self.b.source_reply = lambda args: ("FAILED", "SOURCE_SYNC_INCOMPLETE", {"mutation_started": False})
        self.assertFalse(self.call(S.SYNC, deleted='["Assets/Old/B.cs"]').mutation_performed)
        self.b.source_reply = lambda args: ("FAILED", "BRIDGE_INTERNAL_ERROR", None)
        r = self.call(S.SYNC, sources='["Assets/Old/B.cs"]')
        self.assertStatus(r, tdg.INTERNAL_ERROR, "LIVE_PROTOCOL_ERROR")
        self.assertTrue(r.mutation_performed)   # an unknown start counts as a mutation

    def test_an_unanswered_sync_is_an_unknown_outcome_never_retried(self):
        self.b.mode = "claim-only"
        before = len(self.b.claimed_ids)
        r = self.call(S.SYNC, sources='["Assets/Loop/A.cs"]', timeout=3)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertTrue(r.mutation_performed)
        self.assertEqual(len(self.b.claimed_ids), before + 1)

    def test_a_busy_editor_is_refused_and_nothing_is_queued(self):
        self.b.start_compile()
        r = self.call(S.SYNC, sources='["Assets/Loop/A.cs"]')
        self.assertStatus(r, tdg.CONFLICT, "EDITOR_BUSY")
        self.assertEqual(self.b.source_calls, [])
        self.assertFalse(r.mutation_performed)

    def test_status_reports_facts_and_what_gpos_derives(self):
        r = self.call(S.STATUS)
        self.assertStatus(r, tdg.SUCCESS)
        self.assertFalse(r.mutation_performed)
        self.assertEqual((r.data["settled"], r.data["outcome_newer_than_latest_sync"], r.data["last_compile_outcome"]),
                         (True, False, None))
        self.assertIn("lost when the Editor quits", r.data["disclosure"])
        g = self.call(S.SYNC, sources='["Assets/Loop/A.cs"]').data["sync_generation"]
        self.b.start_compile()
        self.b.finish_compile(errors=0)
        self.b.reload()
        d = self.call(S.STATUS).data
        self.assertEqual((d["sync_generation"], d["compile_generation"], d["last_completed_compile_generation"],
                          d["outcome_newer_than_latest_sync"], d["last_compile_outcome"]), (g, 1, 1, True, "SUCCEEDED"))
        self.b.start_compile()
        self.b.finish_compile(errors=3)
        d = self.call(S.STATUS).data
        self.assertEqual((d["state"]["compilation_failed"], d["last_compile_outcome"], d["settled"]), (True, "FAILED", True))
        self.assertEqual(self.claimed_commands().count("sync-sources"), 1)

    def test_diagnostics_are_sanitized_and_paged(self):
        msgs = [{"severity": "ERROR", "file": "Assets/A.cs", "outside_project": False, "line": i, "column": 1,
                 "message": f"Assets/A.cs({i},1): error CS0246: type {i}", "clipped": False} for i in range(1, 61)]
        msgs.append({"severity": "ERROR", "file": "/Users/someone/elsewhere/B.cs", "outside_project": False, "line": 1,
                     "column": 1, "message": "error CS2001: Source file '/Users/someone/p/Game/Assets/Gone.cs' could not be "
                                             "found; token=abc123secret C:\\Work\\x.cs", "clipped": False})
        self.b.comp["entries"] = [{"assembly": "Assembly-CSharp", "compile_generation": 2, "messages": msgs}]
        page0 = self.call(S.DIAGNOSTICS).data
        self.assertEqual((page0["total"], page0["pages"], len(page0["entries"])), (61, 2, 50))
        page1 = self.call(S.DIAGNOSTICS, page="1").data
        self.assertEqual(len(page1["entries"]), 11)
        last = page1["entries"][-1]
        self.assertEqual((last["file"], last["outside_project"]), (None, True))
        self.assertNotIn("/Users/", last["message"])
        self.assertNotIn("abc123secret", last["message"])
        self.assertNotIn("C:\\Work", last["message"])
        self.assertIn("<path>", last["message"])
        self.assertIn("lost when the Editor quits", page1["disclosure"])
        r = self.call(S.DIAGNOSTICS, generation="2", severity="ERROR", assembly="Assembly-CSharp", page="0")
        self.assertStatus(r, tdg.SUCCESS)
        self.assertEqual(r.data["filters"], {"generation": 2, "severity": "ERROR", "assembly": "Assembly-CSharp"})
        self.b.diagnostics_reply = lambda args: ("REFUSED", "JOURNAL_FILTER_UNKNOWN", None)
        self.assertStatus(self.call(S.DIAGNOSTICS, assembly="Nope"), tdg.INVALID_REQUEST, "LIVE_DIAGNOSTICS_FILTER_UNKNOWN")

    def test_the_cleaning_is_gpos_s_own(self):
        # the foundation redacts result data too; GPOS's own boundary must hold without it
        text = S.clean_text("password=hunter2 at /Users/someone/p/Game/Assets/A.cs and D:\\Work\\b.cs")
        self.assertNotIn("hunter2", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("D:\\Work", text)
        self.assertEqual(text.count("<path>"), 2)
        entry = S._clean_entry({"file": "/private/var/x/A.cs", "outside_project": False, "message": "token=abc123"})
        self.assertEqual((entry["file"], entry["outside_project"]), (None, True))
        self.assertNotIn("abc123", entry["message"])

    def test_reads_need_the_session(self):
        for cap in (S.STATUS, S.DIAGNOSTICS, S.WAIT, S.SYNC):
            with self.subTest(cap):
                inputs = {"sources": '["Assets/A/B.cs"]'} if cap == S.SYNC else {}
                r = self.run_cap(cap, inputs=dict(inputs, unity_project="Game"), session_id=None)
                self.assertNotEqual(r.status, tdg.SUCCESS)


# ---------------------------------------------------------------- W  wait-ready (fake bridge, observation only)

class W_WaitReady(SourceCase):
    def later(self, *steps):
        """Run (delay, action) steps on a timer thread, as Unity would advance its compilation."""
        def go():
            start = time.monotonic()
            for delay, action in steps:
                time.sleep(max(0.0, start + delay - time.monotonic()))
                action()
        t = threading.Thread(target=go, daemon=True)
        t.start()
        self.addCleanup(t.join, 30)
        return t

    def sync(self):
        return self.call(S.SYNC, sources='["Assets/Loop/A.cs"]').data["sync_generation"]

    def test_ready_after_a_successful_compilation_newer_than_the_sync(self):
        g = self.sync()
        before = len(self.b.claimed_ids)
        self.later((0.5, self.b.start_compile), (1.5, self.b.finish_compile), (2.0, self.b.reload))
        r = self.call(S.WAIT, sync_generation=str(g), timeout=40)
        self.assertStatus(r, tdg.SUCCESS)
        d = r.data
        self.assertEqual((d["ready"], d["outcome"], d["compilation_failed"], d["sync_generation"],
                          d["compile_started_before_sync"], d["last_completed_compile_generation"], d["reload_generation"]),
                         (True, "SUCCEEDED", False, g, 0, 1, 2))
        self.assertGreaterEqual(d["waited_seconds"], 2.0)
        self.assertFalse(r.mutation_performed)
        new = self.claimed_commands()[-(len(self.b.claimed_ids) - before):]
        self.assertEqual(set(new), {"compilation-status"})   # observation only: nothing but reads
        self.assertEqual(len(self.b.source_calls), 1)

    def test_a_completed_failed_compilation_is_ready(self):
        g = self.sync()
        self.later((0.3, self.b.start_compile), (1.0, lambda: self.b.finish_compile(errors=2)))
        r = self.call(S.WAIT, sync_generation=str(g), timeout=40)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_COMPILATION_FAILED")
        self.assertEqual((r.data["ready"], r.data["outcome"], r.data["compilation_failed"], r.data["reload_generation"]),
                         (True, "FAILED", True, 1))
        self.assertIn("Unity refuses Play Mode and test runs", r.data["limitation"])

    def test_a_stale_failed_flag_before_the_reload_is_not_settled(self):
        self.b.start_compile()
        self.b.finish_compile(errors=1)                               # an earlier failure: Unity's flag is set
        g = self.sync()

        def finish_clean_with_a_stale_flag():
            self.b.finish_compile(errors=0)
            self.b.comp["failed"] = True                              # stale inside the callback, as measured
        self.later((0.2, self.b.start_compile), (0.5, finish_clean_with_a_stale_flag), (1.5, self.b.reload))
        r = self.call(S.WAIT, sync_generation=str(g), timeout=40)
        self.assertStatus(r, tdg.SUCCESS)
        self.assertEqual((r.data["outcome"], r.data["compilation_failed"]), ("SUCCEEDED", False))

    def test_no_errors_without_a_reload_is_not_yet_ready(self):
        g = self.sync()
        self.later((0.2, self.b.start_compile), (0.6, self.b.finish_compile))
        r = self.call(S.WAIT, sync_generation=str(g), timeout=15)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_NOT_READY")
        self.assertEqual((r.data["ready"], r.data["outcome"]), (False, None))

    def test_a_timeout_returns_the_facts_and_triggers_nothing(self):
        g = self.sync()
        before = len(self.b.claimed_ids)
        r = self.call(S.WAIT, sync_generation=str(g), timeout=13)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_NOT_READY")
        d = r.data
        self.assertEqual((d["ready"], d["outcome"], d["facts_read"], d["phase"], d["compile_generation"]),
                         (False, "NONE_OBSERVED", True, "EDIT", 0))
        self.assertIn("no compilation started after the sync", self.messages(r))
        self.assertFalse(r.mutation_performed)
        self.assertEqual(set(self.claimed_commands()[-(len(self.b.claimed_ids) - before):]), {"compilation-status"})
        self.assertEqual(len(self.b.source_calls), 1)

    def test_the_baseline_is_the_count_before_the_first_import(self):
        self.b.comp["started"] = 5
        g = self.sync()
        self.b.comp["syncs"][-1]["compile_started_after_sync"] = 7   # a compilation started during the imports
        self.b.start_compile()
        self.b.start_compile()                                        # it is compile generation 7
        self.later((0.3, self.b.finish_compile), (0.6, self.b.reload))
        r = self.call(S.WAIT, sync_generation=str(g), timeout=30)
        self.assertStatus(r, tdg.SUCCESS)
        self.assertEqual((r.data["compile_started_before_sync"], r.data["last_completed_compile_generation"],
                          r.data["outcome"]), (5, 7, "SUCCEEDED"))

    def test_an_older_compilation_never_satisfies_a_sync(self):
        self.b.start_compile()
        self.b.finish_compile()
        self.b.reload()
        g = self.sync()                                               # baseline 1: the finished compilation is older
        r = self.call(S.WAIT, sync_generation=str(g), timeout=13)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_NOT_READY")

    def test_an_unknown_sync_generation(self):
        self.sync()
        r = self.call(S.WAIT, sync_generation="99", timeout=20)
        self.assertStatus(r, tdg.INVALID_REQUEST, "LIVE_SYNC_GENERATION_UNKNOWN")

    def test_without_a_sync_ready_is_settled_edit_mode(self):
        r = self.call(S.WAIT, timeout=20)
        self.assertStatus(r, tdg.SUCCESS)
        self.assertEqual((r.data["ready"], r.data["outcome"], r.data["sync_generation"]), (True, "NONE_OBSERVED", None))
        self.b.start_compile()
        self.later((1.0, lambda: self.b.finish_compile(errors=1)))
        r = self.call(S.WAIT, timeout=30)
        self.assertEqual((r.data["ready"], r.data["outcome"]), (True, "FAILED"))

    def test_compiling_playing_or_a_changed_boot_is_not_ready(self):
        self.b.start_compile()
        r = self.call(S.WAIT, timeout=13)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_NOT_READY")
        self.assertEqual((r.data["ready"], r.data["compiling"]), (False, True))
        self.b.finish_compile()
        self.b.phase = "PLAYING"
        self.assertStatus(self.call(S.WAIT, timeout=13), tdg.CONFLICT, "LIVE_NOT_READY")
        self.b.phase = "EDIT"
        g = self.sync()
        self.later((1.0, lambda: setattr(self.b, "boot", "f" * 32)))
        r = self.call(S.WAIT, sync_generation=str(g), timeout=30)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_NOT_READY")
        self.assertIn("boot changed", self.messages(r))

    def test_the_wait_is_bounded(self):
        r = self.call(S.WAIT, timeout=301)
        self.assertEqual(r.status, tdg.INVALID_REQUEST, self.messages(r))

    def test_the_settle_rules(self):
        edit = {"phase": "EDIT", "compiling": False, "updating": False, "pending_transition": False}
        last = {"compile_generation": 4, "reload_generation": 2, "errors": 0}
        self.assertEqual(S.settled(edit, {"last_compile": last}, 3, 3), (True, "SUCCEEDED"))
        self.assertEqual(S.settled(edit, {"last_compile": last}, 2, 3), (False, None))          # no reload yet
        self.assertEqual(S.settled(dict(edit, compilation_failed=True), {"last_compile": last}, 2, 3), (True, "FAILED"))
        self.assertEqual(S.settled(edit, {"last_compile": dict(last, errors=1)}, 2, 3), (True, "FAILED"))
        self.assertEqual(S.settled(edit, {"last_compile": last}, 3, 4), (False, None))          # not newer than the sync
        self.assertEqual(S.settled(dict(edit, compiling=True), {"last_compile": last}, 3, 3), (False, "SUCCEEDED"))
        self.assertEqual(S.settled(dict(edit, updating=None), {"last_compile": last}, 3, 3), (False, "SUCCEEDED"))
        self.assertEqual(S.settled(dict(edit, phase="RELOADING"), {"last_compile": last}, 3, 3), (False, "SUCCEEDED"))
        self.assertEqual(S.settled(edit, {}, 1, None), (True, "NONE_OBSERVED"))


# ---------------------------------------------------------------- D  release

class D_Release(LiveCase):
    install = False

    def test_the_1_3_0_history_is_the_frozen_alpha_19_release(self):
        frozen = ta.git("show", f"{FROZEN_TAG_13}:gpos/tools/unity/live_bridge/manifest.json", binary=True)
        self.assertEqual((bi.HISTORY / "1.3.0.json").read_bytes(), frozen)
        manifest = json.loads(frozen)
        self.assertEqual((manifest["bridge_version"], manifest["protocol"], manifest["package_digest"], len(manifest["files"])),
                         ("1.3.0", "gpos.unity.live/4", FROZEN_DIGEST_13, 54))
        self.assertEqual(bi.PREVIOUS["1.3.0"], ("gpos.unity.live/4", FROZEN_DIGEST_13))
        self.assertEqual((bi.BRIDGE_VERSION, bi.PROTOCOL), ("1.4.0", "gpos.unity.live/5"))
        current = bi.verify_source()
        self.assertEqual((current["bridge_version"], current["protocol"], len(current["files"])), ("1.4.0", "gpos.unity.live/5", 60))
        names = {e["path"] for e in current["files"]}
        self.assertTrue({"Editor/SourceSync.cs", "Editor/Compilation.cs", "Editor/Core/SourceRules.cs"} <= names)
        self.assertEqual(names - {e["path"] for e in manifest["files"]},
                         {"Editor/SourceSync.cs", "Editor/SourceSync.cs.meta", "Editor/Compilation.cs", "Editor/Compilation.cs.meta",
                          "Editor/Core/SourceRules.cs", "Editor/Core/SourceRules.cs.meta"})

    def test_an_exact_1_3_0_bridge_is_upgraded(self):
        target = self.game / "Packages" / bi.PACKAGE_ID
        ta.frozen_package(target, FROZEN_TAG_13)
        self.assertEqual(bi.installed_version(self.game), "1.3.0")
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.PREVIOUS_STATE, []))
        r = self.run_cap(live.INSTALL)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_BRIDGE_UPGRADED")
        self.assertEqual((r.data["upgraded_from"], r.data["installed_state"], r.data["protocol"]),
                         ("1.3.0", bi.EXACT, "gpos.unity.live/5"))
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.EXACT, []))


# ---------------------------------------------------------------- E  boundaries (source)

class E_Boundaries(unittest.TestCase):
    BRIDGE = ("SourceSync.cs", "Compilation.cs", "Core/SourceRules.cs")

    def text(self, name):
        lines = (EDITOR_SOURCE / name).read_text().splitlines()
        return "\n".join(line.split("//")[0] if not line.lstrip().startswith("//") else "" for line in lines)

    def test_no_forbidden_mechanism_in_the_source_bridge(self):
        forbidden = ("AssetDatabase.Refresh", "RequestScriptCompilation", "SaveAssets", "System.Reflection", "GetMethod(",
                     ".Invoke(", "Activator.", "Assembly.Load", "executeMethod", "ExecuteMenuItem", "MenuItem", "Process.Start",
                     "Socket", "HttpClient", "WebRequest", "File.Write", "File.Delete", "File.Move", "File.Copy",
                     "File.Create", "File.Open", "File.Append", "Directory.Delete", "Directory.Move", "Directory.CreateDirectory",
                     "DeleteAsset", "MoveAsset", "CreateAsset", "CopyAsset", "Editor.log", "consoleLogPath", "LogEntries",
                     "StartAssetEditing", "StopAssetEditing", "ForceReserializeAssets", "Undo.", "EditorApplication.Exit",
                     "OpenScene", "SaveScene", "ImportAssetOptions.ForceUpdate", "chmod", "unlink", "rename(",
                     "SetInt(", "GetFiles(\"*.cs\"", "EditorUtility.RequestScriptReload", "AssetDatabase.ImportPackage",
                     "GetAllAssetPaths", "FindAssets", "GetAllAssetBundleNames", "GetAssetPathsFromAssetBundle",
                     "GUIDToAssetPath", "LoadAllAssetsAtPath", "GetDependencies")
        for name in self.BRIDGE:
            text = self.text(name)
            for word in forbidden:
                with self.subTest(f"{name}: {word}"):
                    self.assertNotIn(word, text)
        core = self.text("Core/SourceRules.cs")
        for word in ("File.", "Directory.", "AssetDatabase", "UnityEngine", "UnityEditor", "SessionState"):
            self.assertNotIn(word, core, word)

    def test_every_import_is_exactly_where_reviewed(self):
        sync = self.text("SourceSync.cs")
        self.assertEqual(sync.count("AssetDatabase.ImportAsset("), 2)
        # the only AssetDatabase lookup is one path at a time, for entries the bounded walk visits and exact requested paths
        self.assertEqual(sync.count("AssetDatabase.AssetPathToGUID("), 1)
        self.assertEqual(sorted(set(re.findall(r"AssetDatabase\.(\w+)", sync))), ["AssetPathToGUID", "ImportAsset"])
        self.assertEqual(sync.count("AssetDatabase.ImportAsset(path)"), 1)
        self.assertEqual(sync.count("AssetDatabase.ImportAsset(folder, ImportAssetOptions.ImportRecursive)"), 1)
        for name in sorted(p.relative_to(EDITOR_SOURCE).as_posix() for p in EDITOR_SOURCE.rglob("*.cs")):
            with self.subTest(name):
                self.assertEqual(self.text(name).count("ImportAssetOptions.ImportRecursive"), int(name == "SourceSync.cs"))
        comp = self.text("Compilation.cs")
        for word in ("ImportAsset", "AssetDatabase", "File.", "Directory."):
            self.assertNotIn(word, comp, word)
        subscriptions = re.findall(r"CompilationPipeline\.(\w+) \+=", comp)
        self.assertEqual(sorted(subscriptions), ["assemblyCompilationFinished", "compilationFinished", "compilationStarted"])
        bridge = self.text("Bridge.cs")
        self.assertEqual(bridge.count("Compilation.Register()"), 1)

    def test_everything_is_checked_before_the_first_import_and_the_baseline_before_it(self):
        sync = self.text("SourceSync.cs")
        run = sync[sync.index("public static Dictionary<string, object> Run("):sync.index("static Checked Check(")]
        self.assertLess(run.index("c = Check(r)"), run.index("return Import(c)"))
        self.assertEqual(run.count("Import("), 1)                          # once, after every check, outside their handler
        self.assertEqual(run.count("Check("), 1)
        check = sync[sync.index("static Checked Check("):sync.index("static Dictionary<string, object> Import(")]
        for word in ("SyncRun", "ImportAsset", "BeginSync", "Host"):
            self.assertNotIn(word, check, word)
        for word in ("Probe(p", "SourcePaths.DirectParent(p)", "SourcePaths.Widened(p)", "SourcePaths.Roots(", "Take(root, MaxEntries)"):
            self.assertIn(word, check, word)
        rules = self.text("Core/SourceRules.cs")
        body = rules[rules.index("public static SyncMarks Run("):rules.index("internal sealed class CompileMessage")]
        order = [body.index(w) for w in ("marks.Before = host.CompileStarted", "host.Begin(marks.Before)", "host.ImportExact(p)",
                                         "host.ImportRecursive(f)", "marks.After = host.CompileStarted")]
        self.assertEqual(order, sorted(order))
        self.assertIn("{ \"mutation_started\", marks.Began }", sync)

    def test_the_protocol_takes_paths_only(self):
        proto = self.text("Core/Protocol.cs")
        self.assertIn('{ "sync-sources", new Spec { Session = true, Mutating = true, Sources = true, Args = new[] { "sources", "deleted" } } }', proto)
        self.assertIn('{ "compilation-status", new Spec { Session = true, Args = new string[0] } }', proto)
        self.assertIn('{ "compilation-diagnostics", new Spec { Session = true, Args = new[] { "generation", "severity", "assembly", "page" } } }', proto)

    def test_gpos_writes_no_source_and_waits_by_reading_only(self):
        text = (ROOT / "gpos/tools/unity/sources.py").read_text()
        for word in ("write_text", "write_bytes", "open(", "os.remove", "os.unlink", "os.rename", "os.replace", "shutil",
                     "subprocess", "unlink(", "mkdir"):
            self.assertNotIn(word, text, word)
        self.assertEqual(set(re.findall(r'live\.call\(channel, "([a-z-]+)"', text)), {"compilation-status"})
        wait = text[text.index("def _wait("):]
        self.assertNotIn("sync-sources", wait)
        self.assertNotIn("SYNC", wait.replace("LIVE_SYNC_GENERATION_UNKNOWN", ""))

    def test_the_lock_proof_is_read_only_and_binds_four_libsystem_calls(self):
        text = (ROOT / "gpos/tools/unity/project_lock.py").read_text()
        tree = ast.parse(text)
        text = "\n".join(line.split("#")[0] for line in re.sub(r'"""[\s\S]*?"""', "", text).splitlines())   # code only
        bound = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                 and n.value.id == "lib"} | set(re.findall(r"_libsystem\(\)\.(\w+)", text))
        self.assertEqual(bound, {"proc_listpids", "proc_pidpath", "proc_name", "sysctl"})
        self.assertEqual(re.findall(r"ctypes\.CDLL\(([^,)]+)", text), ["LIBSYSTEM"])
        self.assertIn('LIBSYSTEM = "/usr/lib/libSystem.B.dylib"', text)
        flags = pl.OPEN_FLAGS
        self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_EXCL), 0)
        self.assertEqual(flags & (os.O_NOFOLLOW | os.O_NONBLOCK), os.O_NOFOLLOW | os.O_NONBLOCK)
        self.assertEqual(re.findall(r"os\.open\((.*)\)", text), ["path, OPEN_FLAGS"])
        for word in ("fcntl.flock", "fcntl.lockf", "F_SETLK", "unlink", "os.remove", "rename", "os.replace", "os.truncate",
                     "ftruncate", ".truncate(",
                     ".write(", "chmod", "utime", "os.kill", "signal", "subprocess", "Popen", "rmdir", "mkdir", "shutil",
                     "O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"):
            self.assertNotIn(word, text, word)
        self.assertEqual(text.count("fcntl.F_GETLK"), 1)

    def test_the_adapter_never_touches_the_lockfile(self):
        raw = (ROOT / "gpos/tools/unity/adapter.py").read_text()
        text = "\n".join(line.split("#")[0] for line in re.sub(r'"""[\s\S]*?"""', "", raw).splitlines())
        for word in ("os.remove", "os.unlink", ".unlink(", "os.rename", "os.replace", ".rename(", ".replace(lock",
                     "os.truncate", "ftruncate", ".truncate(", "rmtree", "flock", "lockf", ".write_bytes(", "O_WRONLY"):
            self.assertNotIn(word, text, word)
        self.assertEqual(text.count("self._lock_proof(project, context.probe.tool_path)"), 2)
        run = raw[raw.index("    def _run_tests("):raw.index("    def _classify(")]
        final = run.index("# fresh, immediately before the launch")
        self.assertLess(run.index("workspace = Path(context.workspace)"), final)
        self.assertLess(final, run.index("outcome = context.run(spec)"))
        live_text = (ROOT / "gpos/tools/unity/live.py").read_text()      # D3: the installer keeps its closed-project rule
        self.assertIn('    lock = project / "Temp" / "UnityLockfile"\n    if lock.exists() or lock.is_symlink():\n'
                      '        return _refuse(cap, "ENGINE_PROJECT_LOCKED", "the Unity project is open', live_text)


# ---------------------------------------------------------------- L  the project-lock proof

class FakeOs:
    """A scripted macOS for the proofs: pids, executables, commands and argv per call; files are real (tmp)."""

    def __init__(self, editor, procs=(), listing=None, supported=True, getlk=None):
        self.editor, self.listing, self._supported, self._getlk = editor, listing, supported, getlk
        self.rounds = [list(procs)]
        self.calls = {"pids": 0, "getlk": 0}

    def then(self, procs):
        self.rounds.append(list(procs))
        return self

    def _procs(self):
        return self.rounds[min(self.calls["pids"] - 1, len(self.rounds) - 1)]

    def supported(self):
        return self._supported

    def uid(self):
        return 501

    def pids(self, uid):
        self.calls["pids"] += 1
        if self.listing:
            return None, self.listing
        return [p["pid"] for p in self._procs()], None

    def _find(self, pid):
        return next((p for p in self._procs() if p["pid"] == pid), None)

    def exe(self, pid):
        p = self._find(pid)
        if p is None or p.get("gone"):
            return None, "ESRCH"
        if "exe_errno" in p:
            return None, p["exe_errno"]
        return p.get("exe", self.editor), None

    def command(self, pid):
        p = self._find(pid)
        if p is None:
            return None, "ESRCH"
        if "command_errno" in p:
            return None, p["command_errno"]
        return p.get("command", "Unity"), None

    def argv(self, pid):
        p = self._find(pid)
        if "argv_problem" in p:
            if p.get("exits"):
                p["gone"] = True
            return None, p["argv_problem"]
        return p.get("argv"), None

    lstat = staticmethod(os.lstat)
    stat = staticmethod(os.stat)
    realpath = staticmethod(os.path.realpath)
    fstat = staticmethod(os.fstat)
    close = staticmethod(os.close)

    @staticmethod
    def open_readonly(path):
        return os.open(path, pl.OPEN_FLAGS)

    def getlk(self, fd):
        self.calls["getlk"] += 1
        if self._getlk is not None:
            return self._getlk(fd)
        return pl.Darwin.getlk(fd)


class L_LockProof(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-lock-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.game = self.tmp / "p" / "Game"
        self.game.mkdir(parents=True)
        self.other = self.tmp / "p" / "Game2"
        self.other.mkdir()
        self.editor = "/Applications/Unity/Hub/Editor/6000.5.8f1/Unity.app/Contents/MacOS/Unity"

    def unity(self, pid, project=None, *extra, **kw):
        argv = [self.editor, "-batchmode", "-projectPath", str(project or self.game), *extra]
        return dict(pid=pid, argv=argv, **kw)

    def lockfile(self, kind="file"):
        temp = self.game / "Temp"
        temp.mkdir(exist_ok=True)
        lock = temp / "UnityLockfile"
        if kind == "file":
            lock.write_bytes(b"")
        elif kind == "link":
            os.symlink("/etc/hosts", lock)
        elif kind == "dir":
            lock.mkdir()
        elif kind == "fifo":
            os.mkfifo(lock)
        return lock

    def assess(self, osx):
        return pl.assess(self.game, self.editor, osx)

    def test_no_lock_and_no_process_proceeds_after_two_proofs(self):
        osx = FakeOs(self.editor, [dict(pid=10, exe="/bin/zsh"), self.unity(11, self.other)])
        a = self.assess(osx)
        self.assertEqual((a.state, a.lock, [p[0] for p in a.processes]), (pl.NO_LOCK, pl.ABSENT, [pl.NO_MATCH_PROVEN] * 2))
        self.assertEqual(osx.calls["pids"], 2)

    def test_a_matching_editor_is_active_before_any_lock_query(self):
        self.lockfile()
        osx = FakeOs(self.editor, [self.unity(11)])
        a = self.assess(osx)
        self.assertEqual((a.state, osx.calls["getlk"]), (pl.ACTIVE_EDITOR, 0))
        # an import worker of the project counts
        worker = dict(pid=12, argv=[self.editor, "-adb2", "-batchMode", "-noUpm", "-name", "AssetImportWorker0",
                                    "-projectPath", str(self.game), "-logFile", "Logs/AssetImportWorker0.log", "-srvPort", "1"])
        self.assertEqual(pl.process_proof(str(self.game), self.editor, FakeOs(self.editor, [worker]))[0], pl.MATCHING_EDITOR)

    def test_a_held_lock_is_active_even_without_a_matching_process(self):
        self.lockfile()
        for procs in ([], [dict(pid=13, argv=[self.editor])]):     # no process, or an unprovable one
            with self.subTest(procs):
                a = self.assess(FakeOs(self.editor, procs, getlk=lambda fd: fcntl.F_WRLCK))
                self.assertEqual((a.state, a.lock), (pl.ACTIVE_EDITOR, pl.HELD))

    def test_an_unheld_regular_lockfile_with_two_no_match_proofs_is_an_orphan(self):
        lock = self.lockfile()
        before = lock.stat()
        a = self.assess(FakeOs(self.editor, [self.unity(11, self.other)]))
        self.assertEqual((a.state, a.lock, [p[0] for p in a.processes]), (pl.ORPHAN_UNHELD, pl.UNHELD, [pl.NO_MATCH_PROVEN] * 2))
        after = lock.stat()
        self.assertEqual((after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode),
                         (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode))

    def test_unknown_process_state_is_never_no_process(self):
        cases = {
            "listing failed": FakeOs(self.editor, listing="the process list could not be read (EPERM)"),
            "listing truncated": FakeOs(self.editor, listing="the process list reached its bound and may be truncated"),
            "no project argument": FakeOs(self.editor, [dict(pid=11, argv=[self.editor, "-batchmode"])]),
            "two project arguments": FakeOs(self.editor, [self.unity(11, None, "-projectPath", str(self.other))]),
            "a malformed project argument": FakeOs(self.editor, [dict(pid=11, argv=[self.editor, f"-projectPath={self.game}"])]),
            "a flag without a value": FakeOs(self.editor, [dict(pid=11, argv=[self.editor, "-projectPath"])]),
            "a relative project argument": FakeOs(self.editor, [self.unity(11, "Game")]),
            "unreadable arguments": FakeOs(self.editor, [dict(pid=11, argv_problem="EPERM")]),
            "a Unity whose executable cannot be read": FakeOs(self.editor, [dict(pid=11, exe_errno="ENOENT", command="Unity")]),
            "an unnamed process": FakeOs(self.editor, [dict(pid=11, exe_errno="EPERM", command_errno="EPERM")]),
            "unsupported platform": FakeOs(self.editor, [], supported=False),
        }
        for why, osx in cases.items():
            for lock in (None, "file"):
                with self.subTest(f"{why}, lock {lock}"):
                    shutil.rmtree(self.game / "Temp", ignore_errors=True)
                    if lock:
                        self.lockfile()
                    osx.calls["pids"] = 0
                    a = self.assess(osx)
                    self.assertEqual(a.state, pl.LOCK_STATE_UNKNOWN, a)
                    self.assertTrue(a.reasons)

    def test_an_unknown_first_proof_is_never_cured_by_a_later_one(self):
        for lock in (None, "file"):
            with self.subTest(lock):
                shutil.rmtree(self.game / "Temp", ignore_errors=True)
                if lock:
                    self.lockfile()
                osx = FakeOs(self.editor, [self.unity(11, "Game")]).then([])   # unprovable, then gone
                self.assertEqual(self.assess(osx).state, pl.LOCK_STATE_UNKNOWN)

    def test_processes_that_are_gone_or_not_unity_are_skipped(self):
        procs = [dict(pid=10, gone=True), dict(pid=11, exe_errno="ENOENT", command="crashpad_handler"),
                 dict(pid=12, exe="/usr/bin/python3"), dict(pid=13, argv_problem="EINVAL", exits=True),
                 dict(pid=14, exe="/Applications/Unity/Hub/Editor/2022.3.1f1/Unity.app/Contents/MacOS/Unity")]
        self.assertEqual(pl.process_proof(str(self.game), self.editor, FakeOs(self.editor, procs)), (pl.NO_MATCH_PROVEN, None))

    def test_the_project_argument_is_compared_exactly_never_as_text(self):
        link = self.tmp / "via-link"
        os.symlink(self.game, link)
        for value, expected in ((str(self.game), pl.MATCHING_EDITOR), (str(self.game) + "/", pl.MATCHING_EDITOR),
                                (str(link), pl.MATCHING_EDITOR), (str(self.game).upper(), pl.MATCHING_EDITOR),
                                (str(self.game) + "2", pl.NO_MATCH_PROVEN), (str(self.game.parent), pl.NO_MATCH_PROVEN),
                                (str(self.game / "Assets"), pl.NO_MATCH_PROVEN), (str(self.tmp / "absent"), pl.NO_MATCH_PROVEN),
                                (str(self.game) + "/../Game2", pl.NO_MATCH_PROVEN)):
            with self.subTest(value):
                osx = FakeOs(self.editor, [self.unity(11, value)])
                self.assertEqual(pl.process_proof(str(self.game), self.editor, osx)[0], expected)
        flags = FakeOs(self.editor, [dict(pid=11, argv=[self.editor, "-PROJECTPATH", str(self.game)])])
        self.assertEqual(pl.process_proof(str(self.game), self.editor, flags)[0], pl.MATCHING_EDITOR)
        argv0 = FakeOs(self.editor, [dict(pid=11, argv=["-projectPath", "-batchmode", "-projectPath", str(self.game)])])
        self.assertEqual(pl.process_proof(str(self.game), self.editor, argv0)[0], pl.MATCHING_EDITOR)   # argv[0] is not a flag

    def test_the_candidate_bound(self):
        many = [self.unity(100 + i, self.other) for i in range(pl.MAX_CANDIDATES + 1)]
        self.assertEqual(pl.process_proof(str(self.game), self.editor, FakeOs(self.editor, many))[0], pl.PROCESS_STATE_UNKNOWN)
        self.assertEqual(pl.process_proof(str(self.game), self.editor, FakeOs(self.editor, many[:-1]))[0], pl.NO_MATCH_PROVEN)

    def test_an_editor_appearing_at_the_final_proof_is_active(self):
        self.lockfile()
        osx = FakeOs(self.editor, []).then([self.unity(11)])
        a = self.assess(osx)
        self.assertEqual((a.state, [p[0] for p in a.processes]), (pl.ACTIVE_EDITOR, [pl.NO_MATCH_PROVEN, pl.MATCHING_EDITOR]))
        osx = FakeOs(self.editor, []).then([dict(pid=11, argv_problem="EPERM")])
        self.assertEqual(self.assess(osx).state, pl.LOCK_STATE_UNKNOWN)
        shutil.rmtree(self.game / "Temp")
        self.assertEqual(self.assess(FakeOs(self.editor, []).then([self.unity(11)])).state, pl.ACTIVE_EDITOR)

    def test_lock_query_failures_are_unknown(self):
        self.lockfile()
        def fail(fd):
            raise OSError(errno.EINVAL, "F_GETLK")
        for getlk in (fail, lambda fd: 99):
            with self.subTest(getlk):
                self.assertEqual(self.assess(FakeOs(self.editor, [], getlk=getlk)).state, pl.LOCK_STATE_UNKNOWN)

    def test_links_and_non_regular_lockfiles_are_unknown_without_blocking(self):
        for kind in ("link", "dir", "fifo"):
            with self.subTest(kind):
                shutil.rmtree(self.game / "Temp", ignore_errors=True)
                self.lockfile(kind)
                start = time.monotonic()
                a = self.assess(FakeOs(self.editor, []))
                self.assertEqual((a.state, a.lock), (pl.LOCK_STATE_UNKNOWN, pl.UNKNOWN))
                self.assertLess(time.monotonic() - start, 5)
        shutil.rmtree(self.game / "Temp")
        (self.tmp / "elsewhere").mkdir()
        os.symlink(self.tmp / "elsewhere", self.game / "Temp")
        self.assertEqual(self.assess(FakeOs(self.editor, [])).state, pl.LOCK_STATE_UNKNOWN)

    def test_a_lockfile_replaced_while_it_is_examined_is_unknown(self):
        lock = self.lockfile()
        real_open = os.open

        class Swapping(FakeOs):
            @staticmethod
            def open_readonly(path):
                os.unlink(path)                       # the test (another program), never GPOS
                Path(path).write_bytes(b"")
                return real_open(path, pl.OPEN_FLAGS)
        self.assertEqual(pl.lock_facts(str(self.game), Swapping(self.editor))[0], pl.UNKNOWN)
        self.assertTrue(lock.exists())

    def test_the_real_macos_calls_on_this_host(self):
        if sys.platform != "darwin":
            self.skipTest("macOS only")
        lock = self.lockfile()
        self.assertEqual(pl.lock_facts(str(self.game)), (pl.UNHELD, None))
        holder = hold_lock(lock)
        try:
            self.assertEqual(pl.lock_facts(str(self.game)), (pl.HELD, None))
            self.assertEqual(pl.assess(self.game, self.editor).state, pl.ACTIVE_EDITOR)
        finally:
            holder.kill()
            holder.wait()
        self.assertEqual(pl.lock_facts(str(self.game)), (pl.UNHELD, None))
        self.assertEqual(pl.assess(self.game, self.editor).state, pl.ORPHAN_UNHELD)
        own = pl.DARWIN.argv(os.getpid())
        self.assertEqual((own[1], own[0][-len(sys.argv):]), (None, sys.argv))
        self.assertEqual(pl.DARWIN.exe(os.getpid())[1], None)
        self.assertEqual(pl.DARWIN.exe(999999), (None, "ESRCH"))
        saved = pl.MAX_PIDS, pl.MAX_ARGV_BYTES
        pl.MAX_PIDS, pl.MAX_ARGV_BYTES = 2, 16
        try:
            self.assertEqual(pl.DARWIN.pids(os.getuid())[0], None)   # a full buffer is a truncated listing
            self.assertEqual(pl.DARWIN.argv(os.getpid()), (None, "the argument vector exceeds its bound"))
        finally:
            pl.MAX_PIDS, pl.MAX_ARGV_BYTES = saved

    def test_procargs_parsing(self):
        good = (3).to_bytes(4, sys.byteorder) + b"/x/Unity\0\0\0/x/Unity\0-projectPath\0/p/Game\0HOME=/u\0"
        self.assertEqual(pl.parse_procargs(good), (["/x/Unity", "-projectPath", "/p/Game"], None))
        for bad in (b"", (0).to_bytes(4, sys.byteorder) + b"/x\0", (5).to_bytes(4, sys.byteorder) + b"/x\0\0a\0b\0",
                    (1).to_bytes(4, sys.byteorder) + b"/x", (1).to_bytes(4, sys.byteorder) + b"/x\0\0\xff\xfe\0",
                    (-1).to_bytes(4, sys.byteorder, signed=True) + b"/x\0"):
            with self.subTest(bad):
                self.assertIsNone(pl.parse_procargs(bad)[0])


class L_AdapterLock(UnityCase):
    """The proof through the batch adapter (a stand-in Editor; the proof itself is scripted)."""

    def setUp(self):
        super().setUp()
        self.case = self
        self.s = StandIn(self.tmp / "s", results=nunit())
        self.p = self.unity_project(version=STANDIN_VERSION)
        self.calls = []

    def proof(self, *states):
        def assess(project, editor):
            self.calls.append((str(project), editor))
            state = states[min(len(self.calls) - 1, len(states) - 1)]
            return pl.Assessment(state=state, lock=pl.UNHELD if state == pl.ORPHAN_UNHELD else pl.ABSENT,
                                 processes=[(pl.NO_MATCH_PROVEN, None)], reasons=[f"scripted {state}"])
        return assess

    def run_tests(self, *states, cap=ua.EDITMODE, **kw):
        registry = self.s.registry(lock_proof=self.proof(*states))
        return self.case.run_cap(cap, self.p, registry=registry, **kw)

    def test_active_and_unknown_fail_closed_before_any_launch(self):
        for state in (pl.ACTIVE_EDITOR, pl.LOCK_STATE_UNKNOWN):
            with self.subTest(state):
                self.calls.clear()
                r = self.run_tests(state)
                self.case.assertStatus(r, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
                (d,) = [d for d in r.diagnostics if d.code == "ENGINE_PROJECT_LOCKED"]
                self.assertEqual(json.loads(d.details)["lock_state"], state)
                self.assertFalse(self.s.record.exists())
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0], (str(self.p / "Game"), str(self.s.exe)))

    def test_an_orphan_proceeds_and_is_reported(self):
        r = self.run_tests(pl.ORPHAN_UNHELD)
        self.case.assertStatus(r, tdg.SUCCESS, "ENGINE_PROJECT_ORPHAN_LOCK")
        self.assertTrue(self.s.record.exists())
        self.assertEqual(len(self.calls), 2)                        # before the workspace and again before the launch
        (d,) = [d for d in r.diagnostics if d.code == "ENGINE_PROJECT_ORPHAN_LOCK"]
        self.assertIn("GPOS did not modify it", d.message)
        self.assertEqual(json.loads(d.details)["lock_state"], pl.ORPHAN_UNHELD)
        self.assertEqual(r.data["project_lock"], pl.ORPHAN_UNHELD)

    def test_no_lock_proceeds_without_a_diagnostic(self):
        r = self.run_tests(pl.NO_LOCK)
        self.case.assertStatus(r, tdg.SUCCESS)
        self.assertNotIn("ENGINE_PROJECT_ORPHAN_LOCK", self.case.codes(r))
        self.assertEqual((len(self.calls), r.data["project_lock"]), (2, pl.NO_LOCK))

    def test_the_final_proof_immediately_before_the_launch_decides(self):
        for state in (pl.ACTIVE_EDITOR, pl.LOCK_STATE_UNKNOWN):
            with self.subTest(state):
                self.calls.clear()
                r = self.run_tests(pl.NO_LOCK, state)
                self.case.assertStatus(r, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
                self.assertFalse(self.s.record.exists())
                self.assertEqual(len(self.calls), 2)

    def test_a_dry_run_reports_the_proof(self):
        r = self.run_tests(pl.ORPHAN_UNHELD, dry_run=True)
        self.case.assertStatus(r, tdg.SUCCESS)
        self.assertEqual((r.data["project_lock"], len(self.calls)), (pl.ORPHAN_UNHELD, 1))
        self.assertEqual(self.run_tests(pl.ACTIVE_EDITOR, dry_run=True).status, tdg.CONFLICT)

    def test_unity_refusing_the_race_keeps_the_existing_classification(self):
        self.s = StandIn(self.tmp / "s2", exit=1, stdout="It looks like another Unity instance is running with this "
                                                         "project open.\n")
        r = self.run_tests(pl.NO_LOCK)
        self.assertStatus(r, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
        self.assertIn("another instance has it open", self.messages(r))
        self.assertTrue(self.s.record.exists())                     # it ran once; nothing is retried
        self.assertEqual(len(self.calls), 2)

    def test_the_bridge_installer_keeps_its_closed_project_rule(self):
        (self.p / "Game/Temp").mkdir()
        (self.p / "Game/Temp/UnityLockfile").write_bytes(b"")        # unheld: still a conflict for install (D3)
        r = self.case.run_cap(live.INSTALL, self.p, registry=self.s.registry(lock_proof=self.proof(pl.ORPHAN_UNHELD)))
        self.case.assertStatus(r, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
        self.assertIn("installed or upgraded only in a closed project", self.case.messages(r))
        self.assertEqual(self.calls, [])


# ================================================================ real groups (lab-owned Editors)

REAL = {}


def setUpModule():
    if FAST:
        return
    if len(EDITORS) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A: {len(EDITORS)} Hub Unity Editors found; exactly "
                           f"one is required")
    if tl.EDITOR_PREFS.exists():
        REAL["prefs"] = tl.prefs_snapshot()
    REAL["upm"] = tl.upm_config_state()
    REAL["work"] = Path(tempfile.mkdtemp(prefix="gpos-sources-real-")).resolve()
    ta.REAL_STATE.setdefault("work", REAL["work"])


def tearDownModule():
    try:
        for editor in ta.REAL_STATE["editors"]:
            editor.stop()
        for proc in REAL.get("procs", []):
            if proc.poll() is None:
                proc.kill()
                proc.wait(60)
        if FAST:
            return
        if "prefs" in REAL:
            now = tl.prefs_snapshot()
            moved = {k for k in set(now) | set(REAL["prefs"]) if now.get(k) != REAL["prefs"].get(k)}
            unexpected = sorted(moved - tl.ACCEPTED_PREFS)
            if unexpected:
                raise AssertionError(f"UNITY_SHARED_USER_STATE_UNEXPECTED_MUTATION: {unexpected}")
        if tl.upm_config_state() != REAL["upm"]:
            raise AssertionError("the user's Package Manager configuration files changed")
    finally:
        shutil.rmtree(REAL.get("work", "/nonexistent"), ignore_errors=True)


def source_project(name, install=True):
    p = REAL["work"] / name / "p"
    shutil.copytree(FIXTURE, p)
    fixtures.make_prefab_project(p / "Game", EDITOR_VERSION, EDITOR)
    shutil.copytree(tl.TESTKIT, p / "Game" / "Packages" / tl.TESTKIT.name)
    if install:
        bi.install(p, p / "Game", bi.verify_source())
    return p


class RealSources(unittest.TestCase):
    """Shared helpers: this test is the external tooling that writes files; GPOS only names paths."""

    lab = None
    s = None

    @classmethod
    def start(cls, name, setup=True):
        cls.lab = ta.Lab(source_project(name))
        cls.s = {"syncs": []}
        cls.lab.editor.launch()
        cls.attach()
        if setup:
            cls.s.update(cls.lab.human(op="setup"))

    @classmethod
    def attach(cls):
        r = cls.lab.run(live.ATTACH, timeout=120)
        if r.status != tdg.SUCCESS:
            raise AssertionError(f"attach failed: {[d.message for d in r.diagnostics]}")
        cls.lab.sid = r.data["session_id"]

    @classmethod
    def tearDownClass(cls):
        cls.lab.editor.stop()

    def run_cap(self, cap, **inputs):
        return self.lab.run(cap, **inputs)

    def write(self, rel, text):
        path = self.lab.game / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def remove(self, rel, meta=True):
        (self.lab.game / rel).unlink()
        if meta:
            (self.lab.game / (rel + ".meta")).unlink(missing_ok=True)

    def known(self, rel):
        return self.lab.human(op="asset-guid", path=rel)["guid"]

    def sync(self, sources=(), deleted=()):
        kw = {}
        if sources:
            kw["sources"] = list(sources)
        if deleted:
            kw["deleted"] = list(deleted)
        return self.run_cap(S.SYNC, **kw)

    def wait(self, generation=None, timeout=240):
        kw = {"timeout": timeout}
        if generation is not None:
            kw["sync_generation"] = generation
        return self.run_cap(S.WAIT, **kw)

    def synced(self, *sources, deleted=(), outcome="SUCCEEDED", timeout=240):
        """Sync, then wait for the settled outcome; (sync data, wait data)."""
        d = ok(self, self.sync(sources, deleted))
        self.assertTrue(d["mutation_started"])
        self.s["syncs"].append(d["sync_generation"])
        w = ok(self, self.wait(d["sync_generation"], timeout), "LIVE_COMPILATION_FAILED" if outcome == "FAILED" else None)
        self.assertEqual((w["ready"], w["outcome"]), (True, outcome), w)
        return d, w

    def status(self):
        return ok(self, self.run_cap(S.STATUS))

    def diagnostics(self, **kw):
        return ok(self, self.run_cap(S.DIAGNOSTICS, **kw))

    def inspect(self, oid):
        return ok(self, self.run_cap(au.INSPECT_OBJECT, object=oid))

    def roots(self):
        return ok(self, self.run_cap(au.INSPECT_OBJECT, scene=SCENE))["tokens"]["scene_roots"]

    def create(self, name):
        return ok(self, self.run_cap(au.CREATE, scene=SCENE, name=name, expected_scene_roots_token=self.roots()))["created"]["id"]

    def catalog(self, query):
        d = ok(self, self.run_cap(au.COMPONENT_TYPES, query=query))
        return d["catalog_digest"], [t["type_id"] for t in d["types"]]

    def add(self, oid, type_id, digest=None):
        digest = digest or self.catalog(type_id.split("::")[-1].split(".")[-1])[0]
        return self.run_cap(au.ADD_COMPONENT, object=oid, type_id=type_id, expected_object_token=self.inspect(oid)["tokens"]["object"],
                            expected_catalog_digest=digest)

    def components(self, oid):
        return [(c.get("type"), c.get("missing_script", False)) for c in self.inspect(oid)["components"]]

    def phase_until(self, phase, timeout=60):
        return self.lab.editor.wait(lambda: (self.lab.editor.heartbeat().get("state") or {}).get("phase") == phase, timeout, 0.01)


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R9_RealSources(RealSources):
    """One synthetic project, one lab Editor, one live session: the source/compile loop, rule by rule."""

    @classmethod
    def setUpClass(cls):
        cls.start("sources")

    def test_01_a_fresh_journal(self):
        d = self.status()
        self.assertEqual((d["sync_generation"], d["latest_sync"], d["settled"], d["journal"]["reset"]), (0, None, True, False))
        self.assertTrue(d["journal"]["started_utc"])
        self.assertEqual(d["journal"]["limits"], {"assemblies": 64, "messages": 256, "messages_per_assembly": 32,
                                                  "message_chars": 512, "recent_compiles": 16, "syncs": 64, "page": 50})
        self.s["reload0"] = d["reload_generation"]

    def test_02_a_new_script_is_imported_exactly_and_its_meta_disclosed(self):
        self.write("Assets/Loop/Alpha.cs", mb("Alpha"))
        self.write("Assets/Loop/Unrelated.txt", "not named: an exact import never imports it")
        self.assertFalse((self.lab.game / "Assets/Loop/Alpha.cs.meta").exists())
        d, w = self.synced("Assets/Loop/Alpha.cs")
        (src,) = d["sources"]
        self.assertEqual((src["known_before"], src["known_after"], src["meta_existed_before"], src["meta_created"]),
                         (False, True, False, True))
        self.assertTrue((self.lab.game / "Assets/Loop/Alpha.cs.meta").exists())
        self.assertFalse((self.lab.game / "Assets/Loop/Unrelated.txt.meta").exists())
        self.assertEqual(self.known("Assets/Loop/Unrelated.txt"), "")
        self.assertEqual((d["folders"], d["deleted"], d["imports"]), ([], [], 1))
        (self.lab.game / "Assets/Loop/Unrelated.txt").unlink()
        self.assertGreater(w["reload_generation"], self.s["reload0"])
        self.assertIn("Assembly-CSharp::Alpha", self.catalog("Alpha")[1])
        self.s["host"] = host = self.create("Host")
        self.s["alpha"] = ok(self, self.add(host, "Assembly-CSharp::Alpha"))["component"]["id"]

    def test_03_an_existing_script_is_reimported_exactly(self):
        self.write("Assets/Loop/Alpha.cs", mb("Alpha", "    public int a;\n    public int b;"))
        d, _ = self.synced("Assets/Loop/Alpha.cs")
        self.assertEqual((d["sources"][0]["known_before"], d["sources"][0]["meta_created"]), (True, False))
        props = {e["path"] for e in ok(self, self.run_cap(au.PROPERTIES, component=self.s["alpha"]))["properties"]}
        self.assertIn("b", props)

    def test_04_a_syntax_error_settles_failed_and_existing_types_stay_usable(self):
        before = self.status()
        self.write("Assets/Loop/Alpha.cs", mb("Alpha", "    public int a;\n    public int b\n"))
        d, w = self.synced("Assets/Loop/Alpha.cs", outcome="FAILED")
        self.assertEqual((w["compilation_failed"], w["reload_generation"]), (True, before["reload_generation"]))
        self.assertEqual(d["compile_started_before_sync"], before["compile_generation"])
        (e,) = [e for e in self.diagnostics(severity="ERROR")["entries"] if e["file"] == "Assets/Loop/Alpha.cs"]
        self.assertEqual((e["assembly"], e["severity"], e["line"]), ("Assembly-CSharp", "ERROR", 5))
        self.assertIn("CS1002", e["message"])
        # existing types stay governed by their own checks: authoring on Alpha still works while the compile failed
        token = ok(self, self.run_cap(au.PROPERTIES, component=self.s["alpha"]))["component_token"]
        ok(self, self.run_cap(au.SET_PROPERTY, component=self.s["alpha"], path="a", kind="int32", value=5, expected_component_token=token))
        ok(self, self.run_cap(live.INSPECT))
        # Unity refuses Play Mode with any compile error; GPOS does not override it
        r = self.run_cap(live.ENTER, timeout=60)
        self.assertNotEqual(r.status, tdg.SUCCESS)
        self.assertEqual((self.lab.editor.heartbeat().get("state") or {}).get("phase"), "EDIT")

    def test_05_a_type_error_and_its_fix(self):
        self.write("Assets/Loop/Alpha.cs", mb("Alpha", "    public int a;\n    public int b;\n    public MissingType c;"))
        _, w = self.synced("Assets/Loop/Alpha.cs", outcome="FAILED")
        self.assertTrue(any("CS0246" in e["message"] for e in self.diagnostics(assembly="Assembly-CSharp")["entries"]))
        superseded = self.status()["journal"]["superseded"]
        self.write("Assets/Loop/Alpha.cs", mb("Alpha", "    public int a;\n    public int b;"))   # compiled before: cached
        _, w = self.synced("Assets/Loop/Alpha.cs")
        self.assertFalse(w["compilation_failed"])
        d = self.status()
        self.assertEqual(d["last_compile_outcome"], "SUCCEEDED")
        # Unity reports nothing for a cached assembly; the reload after the clean compilation proves the error stale
        self.assertEqual([e for e in self.diagnostics(severity="ERROR")["entries"] if e["assembly"] == "Assembly-CSharp"], [])
        self.assertNotIn("Assembly-CSharp", {a["assembly"] for a in d["assemblies"] if a["errors"]})
        self.assertEqual(w["diagnostics_summary"]["errors"], 0)
        self.assertEqual(d["journal"]["superseded"], superseded + 1)

    def test_06_a_warning_only_compilation_succeeds(self):
        self.write("Assets/Loop/Warn.cs", "using UnityEngine;\npublic class Warn : MonoBehaviour\n{\n    void M() { int unused; }\n}\n")
        _, w = self.synced("Assets/Loop/Warn.cs")
        self.assertFalse(w["compilation_failed"])
        warns = [e for e in self.diagnostics(severity="WARNING")["entries"] if e["file"] == "Assets/Loop/Warn.cs"]
        self.assertTrue(warns and all("CS0168" in e["message"] for e in warns))
        self.assertGreater(self.status()["last_compile"]["warnings"], 0)

    def test_07_asmdef_and_asmref(self):
        self.write("Assets/Feature/Feature.asmdef", json.dumps({"name": "Feature"}))
        self.write("Assets/Feature/Orbiter.cs", mb("Orbiter", "    public float radius = 1f;", ns="Feature"))
        self.write("Assets/FeatureExtra/Extra.asmref", json.dumps({"reference": "Feature"}))
        self.write("Assets/FeatureExtra/Extra.cs", mb("Extra", "    public int e;", ns="Feature"))
        d, _ = self.synced("Assets/Feature/Feature.asmdef", "Assets/Feature/Orbiter.cs", "Assets/FeatureExtra/Extra.asmref",
                           "Assets/FeatureExtra/Extra.cs")
        self.assertEqual(d["imports"], 4)
        self.assertTrue(all(s["meta_created"] for s in d["sources"]))
        types = self.catalog("Orbiter")[1] + self.catalog("Extra")[1]
        self.assertIn("Feature::Feature.Orbiter", types)
        self.assertIn("Feature::Feature.Extra", types)                   # the asmref puts it into Feature

    def test_08_imports_coalesce_and_no_edit_is_one_compilation(self):
        self.write("Assets/Loop/Alpha.cs", mb("Alpha", "    public int a;\n    public int b;\n    public int c;"))
        self.write("Assets/Feature/Orbiter.cs", mb("Orbiter", "    public float radius = 2f;", ns="Feature"))
        before = self.status()
        d, w = self.synced("Assets/Loop/Alpha.cs", "Assets/Feature/Orbiter.cs")
        self.assertEqual(d["imports"], 2)
        self.assertGreater(w["last_completed_compile_generation"], d["compile_started_before_sync"])
        self.assertGreaterEqual(w["compile_generation"], before["compile_generation"] + 1)
        self.assertGreaterEqual(w["completed_compiles"], before["completed_compiles"] + 1)
        # a sync while Unity compiles is refused, never queued
        self.write("Assets/Loop/Alpha.cs", mb("Alpha", "    public int a;\n    public int b;"))
        g = ok(self, self.sync(["Assets/Loop/Alpha.cs"]))["sync_generation"]
        if self.phase_until("COMPILING", 20):
            r = self.sync(["Assets/Loop/Alpha.cs"])
            self.assertEqual(r.status, tdg.CONFLICT, r.diagnostics)
            self.assertIn("EDITOR_BUSY", {x.code for x in r.diagnostics})
            self.assertFalse(r.mutation_performed)
        ok(self, self.wait(g))
        self.s["syncs"].append(g)

    def test_08b_coalesced_compilations_are_counted_as_starts(self):
        # two imports close together (the Human's Editor importing, through the testkit): two starts, one result
        for i in range(2):
            self.write("Assets/Loop/Alpha.cs", mb("Alpha", f"    public int a;\n    public int b;\n    public int x{i};"))
            self.write("Assets/Feature/Orbiter.cs", mb("Orbiter", f"    public float radius = {i + 3}f;", ns="Feature"))
            self.lab.human(op="import", path="Assets/Loop/Alpha.cs")
            time.sleep(0.4)
            self.lab.human(op="import", path="Assets/Feature/Orbiter.cs")
            ok(self, self.wait(None))
            time.sleep(3)
        d = self.status()
        self.assertGreater(d["compile_generation"], d["completed_compiles"])   # never one edit, one compilation
        self.write("Assets/Loop/Alpha.cs", mb("Alpha", "    public int a;\n    public int b;"))
        s, w = self.synced("Assets/Loop/Alpha.cs")
        self.assertEqual(s["compile_started_before_sync"], d["compile_generation"])   # the start count, never the finish count
        self.assertGreater(w["last_completed_compile_generation"], d["compile_generation"])

    def test_09_generations_are_monotonic(self):
        gens = self.s["syncs"]
        self.assertEqual(gens, list(range(1, len(gens) + 1)))
        d = self.status()
        self.assertEqual(d["sync_generation"], gens[-1])
        compiles = [f["compile_generation"] for f in d["recent_compiles"]]
        self.assertEqual(compiles, sorted(compiles))
        self.assertEqual([f["sequence"] for f in d["recent_compiles"]], sorted(f["sequence"] for f in d["recent_compiles"]))
        befores = [s["compile_started_before_sync"] for s in d["syncs"]]
        self.assertEqual(befores, sorted(befores))

    def test_10_wait_ready_times_out_with_facts_then_succeeds(self):
        self.write("Assets/Loop/Slow.cs", mb("Slow"))
        g = ok(self, self.sync(["Assets/Loop/Slow.cs"]))["sync_generation"]
        r = self.wait(g, timeout=11)
        self.assertEqual(r.status, tdg.CONFLICT, r.diagnostics)
        self.assertIn("LIVE_NOT_READY", {x.code for x in r.diagnostics})
        self.assertEqual((r.data["ready"], r.data["facts_read"], r.data["sync_generation"]), (False, True, g))
        self.assertFalse(r.mutation_performed)
        w = ok(self, self.wait(g))
        self.assertEqual((w["ready"], w["outcome"]), (True, "SUCCEEDED"))
        self.assertEqual(ok(self, self.wait(g))["outcome"], "SUCCEEDED")     # asking again changes nothing
        ok(self, self.wait(None))
        r = self.wait(g + 50, timeout=30)
        self.assertEqual(r.status, tdg.INVALID_REQUEST)

    def test_11_diagnostics_are_paged_and_filtered(self):
        body = "    void M() {\n" + "".join(f"        int u{i};\n" for i in range(40)) + "    }"
        files = ("Assets/Warn/A/WarnA.asmdef", "Assets/Warn/A/ManyA.cs", "Assets/Warn/B/WarnB.asmdef", "Assets/Warn/B/ManyB.cs")
        self.write(files[0], json.dumps({"name": "WarnA"}))
        self.write(files[1], mb("ManyA", body))
        self.write(files[2], json.dumps({"name": "WarnB"}))
        self.write(files[3], mb("ManyB", body))
        self.synced(*files)
        both = self.diagnostics(severity="WARNING")
        per = {a["assembly"]: a for a in self.status()["assemblies"]}
        self.assertEqual((per["WarnA"]["warnings"], per["WarnA"]["kept"], per["WarnA"]["dropped"]), (40, 32, 8))
        self.assertGreaterEqual(both["total"], 64)
        self.assertEqual((len(both["entries"]), both["page_size"]), (50, 50))
        second = self.diagnostics(severity="WARNING", page=1)
        self.assertEqual(len(second["entries"]), both["total"] - 50)
        rows = both["entries"] + second["entries"]
        self.assertEqual([r["assembly"] for r in rows], sorted(r["assembly"] for r in rows))
        only = self.diagnostics(assembly="WarnB")
        self.assertEqual(({r["assembly"] for r in only["entries"]}, only["total"]), ({"WarnB"}, 32))
        self.assertEqual(self.diagnostics(generation=per["WarnB"]["compile_generation"], assembly="WarnB")["total"], 32)
        r = self.run_cap(S.DIAGNOSTICS, assembly="NoSuchAssembly")
        self.assertEqual(r.status, tdg.INVALID_REQUEST)
        self.assertIn("LIVE_DIAGNOSTICS_FILTER_UNKNOWN", {x.code for x in r.diagnostics})
        for rel in files:
            self.remove(rel)
        d, _ = self.synced(deleted=files)                                  # two folders: two bounded imports
        self.assertEqual(sorted(f["folder"] for f in d["folders"]), ["Assets/Warn/A", "Assets/Warn/B"])
        self.assertNotIn("WarnA", {a["assembly"] for a in self.status()["assemblies"] if a["errors"] or a["warnings"]} - {"WarnA"})

    def test_12_absolute_paths_never_leave_the_journal(self):
        # a vanished file Unity still compiles: CS2001 names its absolute path
        self.write("Assets/Loop/Vanish.cs", mb("Vanish"))
        self.synced("Assets/Loop/Vanish.cs")
        self.remove("Assets/Loop/Vanish.cs")
        self.write("Assets/Loop/Alpha.cs", mb("Alpha", "    public int a;\n    public int b;\n    public int d;"))
        self.synced("Assets/Loop/Alpha.cs", outcome="FAILED")
        rows = self.diagnostics(severity="ERROR")["entries"]
        cs2001 = [e for e in rows if "CS2001" in e["message"]]
        self.assertTrue(cs2001)
        for e in rows:
            self.assertNotIn(str(self.lab.game), e["message"])
            self.assertNotIn("/Users/", e["message"])
            self.assertNotIn("/private/", e["message"])
        self.assertIn("Assets/Loop/Vanish.cs", cs2001[0]["message"])
        d, _ = self.synced(deleted=("Assets/Loop/Vanish.cs",))
        self.assertEqual(d["deleted"][0]["state"], "SYNCHRONIZED")
        self.assertEqual(self.known("Assets/Loop/Vanish.cs"), "")

    def test_13_the_journal_is_lost_at_an_editor_restart(self):
        before = self.status()
        ok(self, self.run_cap(au.SAVE_SCENE, scene=SCENE))
        ok(self, self.run_cap(live.DETACH, timeout=120))
        self.lab.editor.quit()
        self.lab.editor.launch()
        self.attach()
        self.lab.human(op="reopen", scene=SCENE)
        after = self.status()
        self.assertNotEqual(after["boot_id"], before["boot_id"])
        self.assertEqual((after["sync_generation"], after["syncs"]), (0, []))
        self.assertNotEqual(after["journal"]["started_utc"], before["journal"]["started_utc"])
        self.assertIn("lost when the Editor quits", after["disclosure"])
        self.assertEqual(self.run_cap(S.WAIT, sync_generation=before["sync_generation"], timeout=30).status, tdg.INVALID_REQUEST)
        self.assertIn(("Assembly-CSharp::Alpha", False), self.components(self.s["host"]))   # the work itself persisted


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R10_RealReloadAndIdentity(RealSources):
    """Domain Reload, identity, renames, moves and deletion synchronization with its bounds."""

    @classmethod
    def setUpClass(cls):
        cls.start("reload")

    def test_01_the_session_and_every_id_survive_a_reload(self):
        s = self.s
        self.write("Assets/Loop/Spinner.cs", mb("Spinner", "    public float speed = 1f;"))
        self.synced("Assets/Loop/Spinner.cs")
        s["host"] = self.create("Host")
        s["spinner"] = ok(self, self.add(s["host"], "Assembly-CSharp::Spinner"))["component"]["id"]
        before = {"object": self.inspect(s["host"])["tokens"]["object"], "boot": self.status()["boot_id"],
                  "reload": self.status()["reload_generation"], "digest": self.catalog("Spinner")[0]}
        self.write("Assets/Loop/Fresh.cs", mb("Fresh"))
        self.synced("Assets/Loop/Fresh.cs")
        after = self.status()
        self.assertEqual((after["boot_id"], after["reload_generation"]), (before["boot"], before["reload"] + 1))
        self.assertEqual(self.inspect(s["host"])["tokens"]["object"], before["object"])   # same session, no re-attach
        digest, types = self.catalog("Fresh")
        self.assertIn("Assembly-CSharp::Fresh", types)
        self.assertNotEqual(digest, before["digest"])
        r = self.add(s["host"], "Assembly-CSharp::Fresh", digest=before["digest"])    # an old catalog digest conflicts
        self.assertEqual(r.status, tdg.CONFLICT)
        self.assertIn("LIVE_CATALOG_CHANGED", {x.code for x in r.diagnostics})

    def test_02_requests_during_a_compilation_are_busy_and_during_a_reload_run_once(self):
        host = self.s["host"]
        token = self.inspect(host)["tokens"]["object"]
        self.write("Assets/Loop/Busy.cs", mb("Busy"))
        g = ok(self, self.sync(["Assets/Loop/Busy.cs"]))["sync_generation"]
        self.assertTrue(self.phase_until("COMPILING", 30))
        r = self.run_cap(au.SET_GAMEOBJECT, object=host, layer=3, expected_object_token=token)
        self.assertEqual(r.status, tdg.CONFLICT)
        self.assertIn("EDITOR_BUSY", {x.code for x in r.diagnostics})
        self.assertTrue(self.phase_until("RELOADING", 120))
        r = self.run_cap(au.SET_GAMEOBJECT, object=host, layer=4, expected_object_token=token)
        ok(self, r)
        self.assertEqual(self.inspect(host)["layer"], 4)                  # exactly once, after the reload
        ok(self, self.wait(g))

    def test_03_a_namespace_change_and_a_class_rename_keep_the_component(self):
        host = self.s["host"]
        self.write("Assets/Loop/Spinner.cs", mb("Spinner", "    public float speed = 1f;", ns="Loop"))
        self.synced("Assets/Loop/Spinner.cs")
        self.assertIn(("Assembly-CSharp::Loop.Spinner", False), self.components(host))
        self.write("Assets/Loop/Spinner.cs", mb("Rotator", "    public float speed = 1f;", ns="Loop"))
        self.synced("Assets/Loop/Spinner.cs")
        self.assertIn(("Assembly-CSharp::Loop.Rotator", False), self.components(host))
        self.assertEqual([c["id"] for c in self.inspect(host)["components"] if "Rotator" in (c.get("type") or "")], [self.s["spinner"]])

    def test_04_a_move_with_its_meta_is_synced_through_the_new_path(self):
        host = self.s["host"]
        self.write("Assets/Loop/Mover.cs", mb("Mover"))
        self.synced("Assets/Loop/Mover.cs")
        ok(self, self.add(host, "Assembly-CSharp::Mover"))
        guid = self.known("Assets/Loop/Mover.cs")
        (self.lab.game / "Assets/Moved").mkdir()
        for suffix in ("", ".meta"):
            os.rename(self.lab.game / f"Assets/Loop/Mover.cs{suffix}", self.lab.game / f"Assets/Moved/Mover.cs{suffix}")
        d, _ = self.synced("Assets/Moved/Mover.cs")
        self.assertEqual(d["sources"][0]["meta_created"], False)
        self.assertEqual(self.known("Assets/Moved/Mover.cs"), guid)
        self.assertEqual(self.known("Assets/Loop/Mover.cs"), "")
        self.assertIn(("Assembly-CSharp::Mover", False), self.components(host))

    def test_05_a_deletion_is_synced_through_its_folder_and_everything_else_reported(self):
        self.write("Assets/Loop/Beta.cs", mb("Beta"))
        self.write("Assets/Loop/Stale.cs", mb("StaleOne"))
        self.synced("Assets/Loop/Beta.cs", "Assets/Loop/Stale.cs")
        self.remove("Assets/Loop/Beta.cs")
        self.remove("Assets/Loop/Stale.cs")                                  # deleted by other tooling, never named
        self.write("Assets/Loop/Note.txt", "an unrelated new file")
        d, _ = self.synced(deleted=("Assets/Loop/Beta.cs",))
        (f,) = d["folders"]
        self.assertEqual((f["folder"], f["parent_widened"]), ("Assets/Loop", False))
        self.assertEqual(f["imported_new"], ["Assets/Loop/Note.txt"])          # found by the bounded walk's own lookups
        self.assertEqual(f["requested_removed"], ["Assets/Loop/Beta.cs"])     # checked by its exact path
        self.assertEqual(f["removed"], [])
        self.assertIn("Assets/Loop/Note.txt.meta", f["meta_created"])
        self.assertGreater(f["known_inspected_before"], 0)
        # the narrowed contract: Unity also reconciled the stale entry, which no bounded walk can see, and it is not listed
        self.assertEqual(self.known("Assets/Loop/Stale.cs"), "")
        self.assertNotIn("Assets/Loop/Stale.cs", json.dumps(d))
        self.assertIn("not enumerable within the bound and are not listed", d["disclosure"])
        self.assertEqual(d["deleted"][0], {"path": "Assets/Loop/Beta.cs", "state": "SYNCHRONIZED", "folder": "Assets/Loop",
                                           "parent_widened": False})
        self.assertIn("anything else new or changed in that folder", d["disclosure"])
        self.assertNotIn("Assembly-CSharp::Beta", self.catalog("Beta")[1])
        r = self.sync(deleted=["Assets/Loop/Beta.cs"])                    # already synchronized: nothing imported
        self.assertEqual((ok(self, r)["deleted"][0]["state"], r.mutation_performed), ("ALREADY_SYNCHRONIZED", False))

    def aside(self, rel):
        """Move a folder (and its .meta) out of the project, as other tooling would delete it; returns a restorer."""
        stash = REAL["work"] / "stash" / rel.replace("/", "_")
        stash.parent.mkdir(parents=True, exist_ok=True)
        os.rename(self.lab.game / rel, stash)
        os.rename(self.lab.game / (rel + ".meta"), str(stash) + ".meta")

        def restore():
            os.rename(stash, self.lab.game / rel)
            os.rename(str(stash) + ".meta", self.lab.game / (rel + ".meta"))
        return restore

    def test_06_d1_a_source_directly_in_assets_is_refused(self):
        self.write("Assets/Root.cs", mb("RootScript"))
        self.synced("Assets/Root.cs")
        os.rename(self.lab.game / "Assets/Root.cs", REAL["work"] / "Root.cs")
        r = self.sync(deleted=["Assets/Root.cs"])
        self.assertEqual(r.status, tdg.CONFLICT)
        self.assertIn("LIVE_SOURCE_SYNC_REFUSED", {x.code for x in r.diagnostics})
        self.assertIn("never imported recursively", " ".join(x.message for x in r.diagnostics))
        self.assertFalse(r.mutation_performed)
        self.assertNotEqual(self.known("Assets/Root.cs"), "")               # nothing was imported
        os.rename(REAL["work"] / "Root.cs", self.lab.game / "Assets/Root.cs")

    def test_07_d2_one_widening_only(self):
        self.write("Assets/Wide/Inner/W.cs", mb("WideW"))
        self.write("Assets/Two/Levels/Deep/D.cs", mb("DeepD"))
        self.write("Assets/Solo/S.cs", mb("SoloS"))
        self.synced("Assets/Wide/Inner/W.cs", "Assets/Two/Levels/Deep/D.cs", "Assets/Solo/S.cs")
        restore = self.aside("Assets/Two/Levels")                          # two folder levels gone
        r = self.sync(deleted=["Assets/Two/Levels/Deep/D.cs"])
        self.assertIn("LIVE_SOURCE_SYNC_REFUSED", {x.code for x in r.diagnostics})
        self.assertIn("only one level is ever widened", " ".join(x.message for x in r.diagnostics))
        self.assertFalse(r.mutation_performed)
        restore()
        restore = self.aside("Assets/Solo")                                # the widened folder would be Assets
        r = self.sync(deleted=["Assets/Solo/S.cs"])
        self.assertIn("LIVE_SOURCE_SYNC_REFUSED", {x.code for x in r.diagnostics})
        self.assertFalse(r.mutation_performed)
        restore()
        shutil.rmtree(self.lab.game / "Assets/Wide/Inner")                 # one folder level gone: widened once
        (self.lab.game / "Assets/Wide/Inner.meta").unlink()
        d, _ = self.synced(deleted=("Assets/Wide/Inner/W.cs",))
        self.assertEqual((d["deleted"][0]["folder"], d["deleted"][0]["parent_widened"], d["folders"][0]["parent_widened"]),
                         ("Assets/Wide", True, True))
        self.assertEqual(d["folders"][0]["requested_removed"], ["Assets/Wide/Inner", "Assets/Wide/Inner/W.cs"])
        self.assertEqual(self.known("Assets/Wide/Inner/W.cs"), "")

    def test_08_the_folder_bounds_and_links(self):
        self.write("Assets/Bulk/K.cs", mb("BulkK"))
        self.synced("Assets/Bulk/K.cs")
        self.remove("Assets/Bulk/K.cs")
        for i in range(1001):
            (self.lab.game / f"Assets/Bulk/n{i:04d}.txt").write_text("x")
        r = self.sync(deleted=["Assets/Bulk/K.cs"])
        self.assertEqual(r.status, tdg.INVALID_REQUEST)
        self.assertIn("LIVE_SOURCE_SYNC_LIMIT", {x.code for x in r.diagnostics})
        self.assertFalse(r.mutation_performed)
        self.assertFalse((self.lab.game / "Assets/Bulk/n0000.txt.meta").exists())   # nothing was imported
        self.assertNotEqual(self.known("Assets/Bulk/K.cs"), "")
        for i in range(1001):
            (self.lab.game / f"Assets/Bulk/n{i:04d}.txt").unlink()
        deep = self.lab.game / "Assets/Bulk" / "/".join("abcdefg")
        deep.mkdir(parents=True)
        r = self.sync(deleted=["Assets/Bulk/K.cs"])
        self.assertIn("LIVE_SOURCE_SYNC_LIMIT", {x.code for x in r.diagnostics})
        shutil.rmtree(self.lab.game / "Assets/Bulk/a")
        os.symlink(REAL["work"], self.lab.game / "Assets/Bulk/link")
        r = self.sync(deleted=["Assets/Bulk/K.cs"])
        self.assertIn("LIVE_SOURCE_SYNC_REFUSED", {x.code for x in r.diagnostics})
        self.assertFalse(r.mutation_performed)
        (self.lab.game / "Assets/Bulk/link").unlink()
        d, _ = self.synced(deleted=("Assets/Bulk/K.cs",))
        self.assertEqual(d["deleted"][0]["state"], "SYNCHRONIZED")

    def test_09_no_global_refresh(self):
        # Unity compiles every script of an assembly from disk, so "no Refresh" is proven with files Unity has never
        # imported: a sync (exact, or a folder import of another folder) never imports them, gives them no .meta and
        # never compiles them.
        self.write("Assets/Other/Unseen.cs", mb("Unseen"))
        self.write("Assets/Other/Note.txt", "never imported")
        self.write("Assets/Loop/Fresh.cs", mb("Fresh", "    public int f2;"))
        _, w = self.synced("Assets/Loop/Fresh.cs")
        self.write("Assets/Loop/Temporary.cs", mb("Temporary"))
        self.synced("Assets/Loop/Temporary.cs")
        self.remove("Assets/Loop/Temporary.cs")
        d, w = self.synced(deleted=("Assets/Loop/Temporary.cs",))              # a folder import stays in its folder
        self.assertEqual([f["folder"] for f in d["folders"]], ["Assets/Loop"])
        for rel in ("Assets/Other/Unseen.cs", "Assets/Other/Note.txt"):
            self.assertEqual(self.known(rel), "")
            self.assertFalse((self.lab.game / (rel + ".meta")).exists())
        self.assertNotIn("Assembly-CSharp::Unseen", self.catalog("Unseen")[1])
        for rel in ("Assets/Other/Unseen.cs", "Assets/Other/Note.txt"):
            (self.lab.game / rel).unlink()

    def test_10_a_deleted_script_leaves_a_missing_script_that_is_never_repaired(self):
        host = self.create("Missing")
        self.write("Assets/Gone/Ghost.cs", mb("Ghost"))
        self.synced("Assets/Gone/Ghost.cs")
        ok(self, self.add(host, "Assembly-CSharp::Ghost"))
        stash = REAL["work"] / "ghost"
        stash.mkdir()
        for suffix in ("", ".meta"):
            os.rename(self.lab.game / f"Assets/Gone/Ghost.cs{suffix}", stash / f"Ghost.cs{suffix}")
        self.synced(deleted=("Assets/Gone/Ghost.cs",))
        self.assertIn((None, True), self.components(host))
        r = self.run_cap(P.CREATE_PREFAB, source=host, path="Assets/Prefabs/Ghost.prefab",
                         expected_subtree_token=self.inspect(host)["tokens"]["subtree"])
        self.assertEqual(r.status, tdg.INVALID_REQUEST)
        self.assertIn("LIVE_PREFAB_REFUSED", {x.code for x in r.diagnostics})
        for suffix in ("", ".meta"):                                       # the tooling restores the file and its .meta
            os.rename(stash / f"Ghost.cs{suffix}", self.lab.game / f"Assets/Gone/Ghost.cs{suffix}")
        self.synced("Assets/Gone/Ghost.cs")
        self.assertIn(("Assembly-CSharp::Ghost", False), self.components(host))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R11_RealQualification(RealSources):
    """The permanent end-to-end qualification: external file tooling and GPOS alone take a feature from a first source
    file through compile errors, authoring, Play Mode and batch tests to a reopened project — no Human Unity step."""

    @classmethod
    def setUpClass(cls):
        cls.start("qualification")

    def batch(self, cap):
        return tl.real_request(self.lab.p, cap, inputs={"unity_project": "Game"}, timeout=1800)

    def test_the_whole_feature_loop(self):
        s, game = self.s, self.lab.game
        # 1 file tooling writes a feature with a compile error; sync; structured diagnostics
        self.write("Assets/Feature/Feature.asmdef", json.dumps({"name": "Feature"}))
        self.write("Assets/Feature/Pulse.cs", "using UnityEngine;\nnamespace Feature {\npublic class Pulse : MonoBehaviour\n{\n"
                                              "    public float rate = 2f\n}\n}\n")
        self.write("Assets/Feature/PulseSettings.cs", "using UnityEngine;\nnamespace Feature {\n[CreateAssetMenu]\npublic class "
                                                      "PulseSettings : ScriptableObject { public int beats = 4; }\n}\n")
        self.synced("Assets/Feature/Feature.asmdef", "Assets/Feature/Pulse.cs", "Assets/Feature/PulseSettings.cs", outcome="FAILED")
        errors = self.diagnostics(severity="ERROR", assembly="Feature")["entries"]
        self.assertEqual({(e["file"], e["line"]) for e in errors}, {("Assets/Feature/Pulse.cs", 5)})
        # 2 fix; sync; wait-ready; successful reload; catalogs re-read
        self.write("Assets/Feature/Pulse.cs", "using UnityEngine;\nnamespace Feature {\npublic class Pulse : MonoBehaviour\n{\n"
                                              "    public float rate = 2f;\n}\n}\n")
        _, w = self.synced("Assets/Feature/Pulse.cs")
        self.assertFalse(w["compilation_failed"])
        digest, types = self.catalog("Pulse")
        self.assertIn("Feature::Feature.Pulse", types)
        so = ok(self, self.run_cap(A.ASSET_TYPES, catalog="SCRIPTABLE_OBJECTS", query="PulseSettings"))
        self.assertIn("Feature::Feature.PulseSettings", [t["type_id"] for t in so["types"]])
        # 3 Scene authoring with the new type
        s["pulse_go"] = go = self.create("PulseHost")
        pulse = ok(self, self.add(go, "Feature::Feature.Pulse", digest))["component"]["id"]
        token = ok(self, self.run_cap(au.PROPERTIES, component=pulse))["component_token"]
        ok(self, self.run_cap(au.SET_PROPERTY, component=pulse, path="rate", kind="float32", value=3.5, expected_component_token=token))
        # 4 asset authoring with the new type
        created = ok(self, self.run_cap(A.CREATE_SCRIPTABLE_OBJECT, path="Assets/Feature/Pulse.asset", type_id="Feature::Feature.PulseSettings",
                                        expected_so_catalog_digest=so["so_catalog_digest"]))["created"]
        s["pulse_asset"] = created["id"]
        # 5 prefab authoring from the new object
        ok(self, self.run_cap(au.SAVE_SCENE, scene=SCENE))
        made = ok(self, self.run_cap(P.CREATE_PREFAB, source=go, path="Assets/Feature/PulseHost.prefab",
                                     expected_subtree_token=self.inspect(go)["tokens"]["subtree"]))["created"]
        s["prefab"] = made["id"]
        ok(self, self.run_cap(au.SAVE_SCENE, scene=SCENE))
        # 6 Play Mode, then back
        ok(self, self.run_cap(live.ENTER, timeout=120))
        ok(self, self.run_cap(live.EXIT, timeout=120))
        ok(self, self.run_cap(au.SAVE_SCENE, scene=SCENE))
        # 7 close the live Editor; batch EditMode and PlayMode tests pass
        ok(self, self.run_cap(live.DETACH, timeout=120))
        self.lab.editor.quit()
        for cap in (ua.EDITMODE, ua.PLAYMODE):
            r = self.batch(cap)
            self.assertEqual((r.status, r.data["failed"]), (tdg.SUCCESS, 0), [d.message for d in r.diagnostics])
            self.assertEqual(r.data["project_lock"], pl.NO_LOCK)
        # 8 an intentionally failing test is evidence of failure
        failing = "Assets/Tests/Editor/PulseTests.cs"
        self.write(failing, "using NUnit.Framework;\npublic class PulseTests\n{\n    [Test] public void RateIsPositive() "
                            "{ Assert.AreEqual(1, 2, \"deliberately failing\"); }\n}\n")
        r = self.batch(ua.EDITMODE)
        self.assertEqual((r.status, r.data["failed"]), (tdg.SUCCESS, 1))
        self.assertIn("TESTS_FAILED", {d.code for d in r.diagnostics})
        # 9 the source fix goes through the live loop: reopen, sync, compile, close
        self.write(failing, "using NUnit.Framework;\npublic class PulseTests\n{\n    [Test] public void RateIsPositive() "
                            "{ Assert.AreEqual(2, 2); }\n}\n")
        self.lab.editor.launch()
        self.attach()
        self.lab.human(op="reopen", scene=SCENE)
        self.synced(failing)
        ok(self, self.run_cap(live.DETACH, timeout=120))
        self.lab.editor.quit()
        # 10 the batch rerun passes
        r = self.batch(ua.EDITMODE)
        self.assertEqual((r.status, r.data["failed"], r.data["passed"]), (tdg.SUCCESS, 0, 3), [d.message for d in r.diagnostics])
        # 11 reopen: everything persisted
        self.lab.editor.launch()
        self.attach()
        self.lab.human(op="reopen", scene=SCENE)
        comps = self.components(s["pulse_go"])
        self.assertIn(("Feature::Feature.Pulse", False), comps)
        pulse = [c for c in self.inspect(s["pulse_go"])["components"] if c.get("type") == "Feature::Feature.Pulse"][0]["id"]
        rate = [e for e in ok(self, self.run_cap(au.PROPERTIES, component=pulse))["properties"] if e["path"] == "rate"][0]
        self.assertEqual(rate["value"], 3.5)
        self.assertEqual(ok(self, self.run_cap(A.ASSET_INSPECT, asset=s["pulse_asset"]))["asset"]["path"], "Assets/Feature/Pulse.asset")
        self.assertEqual(ok(self, self.run_cap(P.PREFAB_INSPECT, prefab=s["prefab"]))["prefab"]["path"], "Assets/Feature/PulseHost.prefab")
        self.assertFalse(self.status()["state"]["compilation_failed"])


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R12_RealStaleLock(unittest.TestCase):
    """The batch plane's read-only lock proof against real Unity processes (D5)."""

    @classmethod
    def setUpClass(cls):
        cls.p = source_project("lock")
        cls.game = cls.p / "Game"
        cls.lock = cls.game / "Temp" / "UnityLockfile"
        cls.editor = tl.LabEditor(cls.p, cls.game)
        tl._STATE["editors"].remove(cls.editor)
        ta.REAL_STATE["editors"].append(cls.editor)

    @classmethod
    def tearDownClass(cls):
        cls.editor.stop()

    def run_tests(self, cap=ua.EDITMODE, lock_proof=None, **kw):
        if lock_proof is None:
            return tl.real_request(self.p, cap, inputs={"unity_project": "Game"}, timeout=1800, **kw)
        registry = ToolRegistry(tl.FW)
        registry.register(UnityAdapter(lock_proof=lock_proof))
        return execute(registry, ExecutionRequest(adapter_id="unity", capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", "rev-1"),
                                                  project_root=str(self.p), actor=tl.APPROVER, inputs={"unity_project": "Game"},
                                                  allow_mutation=True, timeout=1800, **kw))

    def locked(self, r, state):
        self.assertEqual(r.status, tdg.CONFLICT, [d.message for d in r.diagnostics])
        (d,) = [d for d in r.diagnostics if d.code == "ENGINE_PROJECT_LOCKED"]
        self.assertEqual(json.loads(d.details)["lock_state"], state)
        self.assertEqual([a for a in r.artifacts], [])                    # no Unity was launched

    def test_01_no_lock_and_no_process_proceeds(self):
        r = self.run_tests()
        self.assertEqual((r.status, r.data["project_lock"]), (tdg.SUCCESS, pl.NO_LOCK))
        self.assertFalse(self.lock.exists())

    def test_02_a_compile_error_leaves_an_orphan_that_real_unity_replaces(self):
        broken = self.game / "Assets/Scripts/Broken.cs"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("using UnityEngine;\npublic class Broken : MonoBehaviour { public int a }\n")
        r = self.run_tests()
        self.assertEqual((r.status, r.data["cause"]), (tdg.FAILED, "COMPILE_ERROR"))
        self.assertTrue(self.lock.exists())                                 # Unity left it behind, unheld
        a = pl.assess(self.game, EDITOR)
        self.assertEqual((a.state, a.lock), (pl.ORPHAN_UNHELD, pl.UNHELD))
        inode = self.lock.stat().st_ino
        time.sleep(5)
        self.assertEqual(self.lock.stat().st_ino, inode)                   # it does not go away on its own
        broken.unlink()
        (self.game / "Assets/Scripts/Broken.cs.meta").unlink(missing_ok=True)
        r = self.run_tests()
        self.assertEqual(r.status, tdg.SUCCESS, [d.message for d in r.diagnostics])
        self.assertIn("ENGINE_PROJECT_ORPHAN_LOCK", {d.code for d in r.diagnostics})
        self.assertEqual(r.data["project_lock"], pl.ORPHAN_UNHELD)
        self.assertFalse(self.lock.exists())                                # Unity itself replaced and then removed it

    def test_03_an_active_matching_editor_is_an_immediate_conflict(self):
        self.editor.launch()
        try:
            start = time.monotonic()
            r = self.run_tests()
            self.locked(r, pl.ACTIVE_EDITOR)
            self.assertLess(time.monotonic() - start, 30)
            self.assertEqual(pl.process_proof(str(self.game), EDITOR)[0], pl.MATCHING_EDITOR)
        finally:
            self.editor.quit()

    def test_04_a_held_flock_without_a_matching_process_is_a_conflict(self):
        self.lock.parent.mkdir(exist_ok=True)
        self.lock.write_bytes(b"")
        holder = hold_lock(self.lock)
        try:
            self.locked(self.run_tests(), pl.ACTIVE_EDITOR)
        finally:
            holder.kill()
            holder.wait()
        self.assertTrue(self.lock.exists())
        shutil.rmtree(self.game / "Temp")                                     # the test's own file, not GPOS

    def test_05_an_editor_with_an_unprovable_project_is_unknown(self):
        other = source_project("lock-other", install=False)
        proc = subprocess.Popen([EDITOR, "-batchmode", "-projectPath", "Game", "-logFile", str(other / "editor.log"), "-quit"],
                                cwd=str(other), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        REAL.setdefault("procs", []).append(proc)
        try:
            seen = None
            end = time.monotonic() + 60
            while time.monotonic() < end and proc.poll() is None:
                seen = pl.process_proof(str(self.game), EDITOR)
                if seen[0] == pl.PROCESS_STATE_UNKNOWN:
                    break
                time.sleep(0.2)
            self.assertEqual(seen[0], pl.PROCESS_STATE_UNKNOWN, seen)
            self.assertIn("relative path", seen[1])
            self.locked(self.run_tests(dry_run=True), pl.LOCK_STATE_UNKNOWN)
        finally:
            proc.wait(600)

    def test_06_unity_arbitrates_the_final_race(self):
        self.editor.launch()
        try:
            unaware = lambda project, editor: pl.Assessment(state=pl.NO_LOCK, processes=[(pl.NO_MATCH_PROVEN, None)] * 2)
            r = self.run_tests(lock_proof=unaware)                         # as if the Editor had opened it just now
            self.assertEqual(r.status, tdg.CONFLICT, [d.message for d in r.diagnostics])
            self.assertIn("another instance has it open", " ".join(d.message for d in r.diagnostics))
            beat = self.editor.heartbeat()
            self.assertTrue(beat and self.editor.proc.poll() is None)       # the holder is unaffected
            self.assertEqual(pl.assess(self.game, EDITOR).state, pl.ACTIVE_EDITOR)
        finally:
            self.editor.quit()

    def test_07_links_and_non_regular_lockfiles_are_unknown(self):
        (self.game / "Temp").mkdir(exist_ok=True)
        os.symlink(self.game / "ProjectSettings/ProjectVersion.txt", self.lock)
        self.locked(self.run_tests(dry_run=True), pl.LOCK_STATE_UNKNOWN)
        self.lock.unlink()
        os.mkfifo(self.lock)
        self.locked(self.run_tests(dry_run=True), pl.LOCK_STATE_UNKNOWN)
        shutil.rmtree(self.game / "Temp")


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
