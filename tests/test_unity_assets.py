#!/usr/bin/env python3
"""Phase 2C-6B2A — Unity live asset references and asset authoring (alpha.18).

    python3 tests/test_unity_assets.py

Fast groups need no Unity. A–C drive the GPOS side against `unity_live_fake_bridge.FakeBridge` (a stand-in that
speaks the file protocol; it proves GPOS behaviour only): declarations, the input grammar (asset ids, write paths,
shader and ScriptableObject values, whole-string matching), the exact arguments GPOS sends and how every bridge
answer maps to a result — including mutation_performed once a targeted import happened, OUTCOME_UNKNOWN from the
commit point on, and the creation-recovery diagnostics. E checks the bridge's asset sources for forbidden
mechanisms and for the order of the persistence and creation steps. The bridge's Unity-free asset rules (grammars,
kinds, value rules, slot rule, catalog digests, the transaction record) are covered by
tests/test_unity_live_bridge_core.py.

Real groups open disposable synthetic Unity projects (unity_fixture_builder.make_asset_project) in lab-owned
batch-mode Editors activated by the test-only testkit, which also stands in for the Human (Inspector edits, Cmd-Z,
Ctrl+S on an asset, a sprite import) and for other programs (a file written at an exact step through the bridge's
AssetAuthoring.AfterStep test seam) — never the Human's Editor, never a user project. R3 is one asset session end to
end; R4 stops the lab Editor process at exact creation steps (a real crash) and proves recovery after a restart. The
real groups stop with UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A unless exactly one Hub Editor is installed, and guard
Unity's EditorPrefs and the user's Package Manager configuration files.

GPOS_UNITY_TEST_FAST=1 (the mutation harness only) skips every group that starts a real Unity process.
"""

import hashlib
import json
import os
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
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools.unity import UnityAdapter  # noqa: E402
from gpos.tools.unity import assets as A  # noqa: E402
from gpos.tools.unity import authoring as au  # noqa: E402
from gpos.tools.unity import bridge_install as bi  # noqa: E402
from gpos.tools.unity import live  # noqa: E402
from test_unity_live import EDITOR, EDITOR_VERSION, EDITORS, FAST, FIXTURE, LiveCase, published  # noqa: E402

T = "0" * 31 + "1"
DIGEST = "a" * 64
MAT = "GlobalObjectId_V1-3-" + "b" * 32 + "-2100000-0"
TEX = "GlobalObjectId_V1-1-" + "c" * 32 + "-2800000-0"
SPRITE = "GlobalObjectId_V1-1-" + "c" * 32 + "-21300000-0"
BUILTIN_MESH = "GlobalObjectId_V1-4-0000000000000000e000000000000000-10202-0"
BUILTIN_MAT = "GlobalObjectId_V1-4-0000000000000000f000000000000000-10303-0"
SCENE_ID = "GlobalObjectId_V1-2-0123456789abcdef0123456789abcdef-5-0"
SO = "GlobalObjectId_V1-3-" + "d" * 32 + "-11400000-0"
EDITOR_SOURCE = bi.SOURCE / "Editor"


def call(cap, **inputs):
    return dict(inputs, unity_project="Game")


# ---------------------------------------------------------------- A  declarations

class A_Declarations(unittest.TestCase):
    def test_seven_fixed_asset_capabilities(self):
        caps = {c.id: c for c in UnityAdapter.descriptor.capabilities}
        self.assertEqual(len(caps), 41)
        self.assertEqual(len(A.CAPABILITY_IDS), 7)
        self.assertTrue(set(A.CAPABILITY_IDS) <= set(caps))
        self.assertFalse(set(A.CAPABILITY_IDS) & set(au.CAPABILITY_IDS))
        for cid in A.CAPABILITY_IDS:
            c = caps[cid]
            read_only = cid in A.READ_ONLY
            with self.subTest(cid):
                self.assertEqual((c.category, c.operation_class, c.state_model, c.effective_lease_mode,
                                  c.execution_context, c.resource_kind, c.requires_tool),
                                 ("INSPECT" if read_only else "TRANSFORM", "READ_ONLY" if read_only else "MUTATING",
                                  "STATEFUL", "SESSION_REQUIRED", "EDITOR", "EDITOR_PROJECT", False))
                self.assertEqual((c.potential_evidence, c.artifact_kinds, c.dry_run_supported), ((), (), False))
                self.assertEqual(c.input_kinds, ("unity_project",) + tuple(A.INPUTS[cid]))
                self.assertEqual(c.single_writer_required, not read_only)
                self.assertEqual(c.side_effect_scope == "NONE", read_only)
                self.assertEqual((c.timeout.default, c.timeout.maximum), (30.0, 120.0) if read_only else (60.0, 300.0))
        self.assertIn("not undoable", caps[A.CREATE_MATERIAL].side_effect_scope)
        self.assertIn("not evidence", A.LIMITATION)
        self.assertIn("Cmd-Z", A.LIMITATION)

    def test_no_generic_surface(self):
        names = set()
        for cid in A.CAPABILITY_IDS:
            names |= set(A.INPUTS[cid])
        for word in ("method", "menu", "command", "script", "executable", "url", "file", "code", "eval", "reflect",
                     "instance", "entity", "filter", "importer", "folder", "guid", "refresh", "save", "move", "rename",
                     "delete", "search"):
            self.assertFalse([n for n in names if word in n], word)
        self.assertEqual(A.KINDS, ("MATERIAL", "TEXTURE", "SPRITE", "AUDIO", "MESH", "PREFAB", "PREFAB_COMPONENT",
                                   "MODEL", "SCRIPTABLE_OBJECT"))
        self.assertEqual((A.SOURCES, A.CATALOGS), (("ASSETS", "PACKAGE", "BUILTIN"),
                                                   ("KINDS", "SCRIPTABLE_OBJECTS", "SHADERS")))
        self.assertEqual(A.MATERIAL_KINDS, ("color", "vector", "float", "range", "int", "texture"))
        self.assertNotIn("PREFAB_COMPONENT", A.FINDABLE)


# ---------------------------------------------------------------- B  input grammar

class B_Inputs(unittest.TestCase):
    def refused(self, cap, inputs, code):
        with self.assertRaises(A.InputProblem) as ctx:
            A.parse_inputs(cap, inputs)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))

    def test_asset_ids(self):
        for good in (MAT, TEX, SPRITE, BUILTIN_MESH, BUILTIN_MAT, SO,
                     "GlobalObjectId_V1-1-" + "a" * 32 + "-18446744073709551615-0"):
            with self.subTest(good):
                self.assertEqual(A.parse_inputs(A.ASSET_INSPECT, {"asset": good})["asset"], good)
        for bad in (SCENE_ID, MAT + "\n", MAT + "\r\n", MAT.replace("-0", "-1")[:-1] + "1", MAT.upper(),
                    "GlobalObjectId_V1-5-" + "a" * 32 + "-1-0", "GlobalObjectId_V1-3-" + "0" * 32 + "-1-0",
                    "GlobalObjectId_V1-4-" + "a" * 32 + "-1-0", "GlobalObjectId_V1-1-0000000000000000e000000000000000-1-0",
                    "GlobalObjectId_V1-1-" + "a" * 32 + "-18446744073709551616-0", "12345", "Assets/Materials/A.mat",
                    "Library/PackageCache/com.x/A.mat", " " + MAT, ""):
            with self.subTest(repr(bad)):
                self.refused(A.ASSET_INSPECT, {"asset": bad}, "LIVE_OBJECT_REFUSED")

    def test_write_paths(self):
        digest = {"shader": MAT, "expected_shader_catalog_digest": DIGEST}
        for good in ("Assets/Materials/Hero.mat", "Assets/A b/C (1)/x-y_z.mat", "Assets/" + "/".join(["d"] * 16) + "/m.mat",
                     "Assets/Resources/Hero.mat", "Assets/v1.2/Hero.mat"):
            with self.subTest(good):
                self.assertEqual(A.parse_inputs(A.CREATE_MATERIAL, dict(digest, path=good))["path"], good)
        for bad in ("Packages/com.x/Hero.mat", "Library/Hero.mat", "/Users/x/Assets/M/Hero.mat", "Assets/Hero.mat",
                    "Assets/M/../Hero.mat", "Assets/./M/Hero.mat", "Assets//Hero.mat", "Assets/M/Hero.asset",
                    "Assets/M/Hero.MAT", "Assets/M/Hero", "Assets/M/Hero.mat\n", "Assets/M/Hero.mat\r\n",
                    "Assets/M/Hero\n.mat", "Assets/M/Hero\r\n.mat", "Assets/M\n/Hero.mat",
                    "Assets/Editor/Hero.mat", "Assets/X/editor/Hero.mat", "Assets/StreamingAssets/Hero.mat",
                    "Assets/Editor Default Resources/H.mat", "Assets/.hidden/Hero.mat", "Assets/M~/Hero.mat",
                    "Assets/GposAssetTxn-" + "a" * 32 + "/Hero.mat", "Assets/M/ Hero.mat", "Assets/M/Hero .mat",
                    "Assets/M/He:ro.mat", "Assets/M/He*ro.mat", "Assets/M/" + "x" * 65 + ".mat", "Assets\\M\\Hero.mat",
                    "Assets/" + "/".join(["d"] * 17) + "/m.mat", "Assets/M/.mat", "assets/M/Hero.mat", ""):
            with self.subTest(repr(bad)):
                self.refused(A.CREATE_MATERIAL, dict(digest, path=bad), "LIVE_ASSET_PATH_INVALID")
        so = {"type_id": "Assembly-CSharp::GameConfig", "expected_so_catalog_digest": DIGEST}
        A.parse_inputs(A.CREATE_SCRIPTABLE_OBJECT, dict(so, path="Assets/Data/Config.asset"))
        self.refused(A.CREATE_SCRIPTABLE_OBJECT, dict(so, path="Assets/Data/Config.mat"), "LIVE_ASSET_PATH_INVALID")
        self.refused(A.CREATE_SCRIPTABLE_OBJECT, dict(so, path="Assets/Data/C.asset", type_id="GameConfig"),
                     "LIVE_TYPE_NOT_IN_CATALOG")
        self.refused(A.CREATE_MATERIAL, dict(digest, path="Assets/M/H.mat", shader=SCENE_ID), "LIVE_OBJECT_REFUSED")
        self.refused(A.CREATE_MATERIAL, dict(digest, path="Assets/M/H.mat", expected_shader_catalog_digest=DIGEST + "\n"),
                     "INVALID_TOOL_REQUEST")

    def test_material_values(self):
        def args(kind, value, prop="_Color"):
            return {"material": MAT, "property": prop, "kind": kind, "value": value, "expected_asset_token": T}
        good = {"color": "[1, 0.5, 2, 1]", "vector": "[0, 0, 0, 1]", "float": "-3.5", "range": "0.25", "int": "-7",
                "texture": json.dumps(TEX)}
        self.assertEqual(set(good), set(A.MATERIAL_KINDS))
        for kind, value in good.items():
            with self.subTest(kind):
                A.parse_inputs(A.SET_MATERIAL_PROPERTY, args(kind, value))
        A.parse_inputs(A.SET_MATERIAL_PROPERTY, args("texture", "null"))
        A.parse_inputs(A.SET_MATERIAL_PROPERTY, args("texture", json.dumps(SPRITE)))   # grammar: the bridge refuses a Sprite
        for kind, value in (("color", "[1, 1, 1]"), ("color", "[1, 1, 1, NaN]"), ("vector", '"x"'), ("float", "1e39"),
                            ("float", "true"), ("range", "Infinity"), ("int", "1.5"), ("int", "2147483648"),
                            ("int", "true"), ("texture", "5"), ("texture", json.dumps(SCENE_ID)),
                            ("float", '{"a": 1, "a": 2}')):
            with self.subTest(f"{kind} {value}"):
                with self.assertRaises(A.InputProblem):
                    A.parse_inputs(A.SET_MATERIAL_PROPERTY, args(kind, value))
        self.refused(A.SET_MATERIAL_PROPERTY, args("keyword", "true"), "LIVE_PROPERTY_UNSUPPORTED")
        for prop in ("_Color\n", "_Co lor", "1abc", "", "_" + "x" * 128, "_Color.r"):
            with self.subTest(repr(prop)):
                self.refused(A.SET_MATERIAL_PROPERTY, args("float", "1", prop), "LIVE_PROPERTY_UNSUPPORTED")

    def test_scriptable_object_values_reference_assets_only(self):
        def args(kind, value, path="value"):
            return {"asset": SO, "path": path, "kind": kind, "value": value, "expected_asset_token": T}
        A.parse_inputs(A.SET_ASSET_PROPERTY, args("int32", "5"))
        A.parse_inputs(A.SET_ASSET_PROPERTY, args("object", json.dumps(MAT)))
        A.parse_inputs(A.SET_ASSET_PROPERTY, args("object", "null"))
        self.refused(A.SET_ASSET_PROPERTY, args("object", json.dumps(SCENE_ID)), "LIVE_VALUE_INVALID")
        self.refused(A.SET_ASSET_PROPERTY, args("object", '"GlobalObjectId_V1-3-' + "a" * 32 + '-1-5"'),
                     "LIVE_VALUE_INVALID")
        self.refused(A.SET_ASSET_PROPERTY, args("int32", "1", "arr.Array.data[0]"), "LIVE_PROPERTY_UNSUPPORTED")
        self.refused(A.SET_ASSET_PROPERTY, args("uint8", "300"), "LIVE_VALUE_INVALID")

    def test_lookup_and_catalog_inputs(self):
        self.assertEqual(A.parse_inputs(A.ASSET_FIND, {"kind": "SPRITE", "source": "BUILTIN", "query": "ui"}),
                         {"kind": "SPRITE", "source": "BUILTIN", "query": "ui", "page": None})
        for kind, source in (("PREFAB_COMPONENT", "ASSETS"), ("SHADER", "ASSETS"), ("MATERIAL", "LIBRARY"),
                             ("MATERIAL\n", "ASSETS"), ("material", "ASSETS"), ("MATERIAL", "assets")):
            with self.subTest(f"{kind}/{source}"):
                self.refused(A.ASSET_FIND, {"kind": kind, "source": source}, "INVALID_TOOL_REQUEST")
        self.refused(A.ASSET_FIND, {"kind": "MATERIAL", "source": "ASSETS", "query": "t:Material\n"}, "INVALID_TOOL_REQUEST")
        self.refused(A.ASSET_FIND, {"kind": "MATERIAL", "source": "ASSETS", "query": "x" * 65}, "INVALID_TOOL_REQUEST")
        self.refused(A.ASSET_FIND, {"kind": "MATERIAL"}, "INVALID_TOOL_REQUEST")
        A.parse_inputs(A.ASSET_TYPES, {"catalog": "SHADERS", "query": "Standard", "page": "2"})
        self.refused(A.ASSET_TYPES, {"catalog": "KINDS", "query": "x"}, "INVALID_TOOL_REQUEST")
        self.refused(A.ASSET_TYPES, {"catalog": "ALL"}, "INVALID_TOOL_REQUEST")
        self.refused(A.ASSET_INSPECT, {"asset": MAT, "filter": "x"}, "INVALID_TOOL_REQUEST")   # no unknown input


# ---------------------------------------------------------------- C  what GPOS sends and how answers map

class C_Mapping(LiveCase):
    def author(self, cap, sid=None, **inputs):
        timeout = inputs.pop("timeout", None)
        inputs.setdefault("unity_project", "Game")
        kw = {"inputs": inputs, "session_id": sid}
        if timeout:
            kw["timeout"] = timeout
        return self.run_cap(cap, **kw)

    def setUp(self):
        super().setUp()
        self.b, self.sid = self.attached()

    CALLS = {
        A.ASSET_TYPES: {"catalog": "SCRIPTABLE_OBJECTS", "page": "1"},
        A.ASSET_FIND: {"kind": "MESH", "source": "BUILTIN", "query": "cube"},
        A.ASSET_INSPECT: {"asset": SO, "path_prefix": "nested."},
        A.CREATE_MATERIAL: {"path": "Assets/Materials/Hero.mat", "shader": BUILTIN_MAT.replace("10303", "46"),
                            "expected_shader_catalog_digest": DIGEST},
        A.SET_MATERIAL_PROPERTY: {"material": MAT, "property": "_Color", "kind": "color", "value": "[1, 0, 0, 1]",
                                  "expected_asset_token": T},
        A.CREATE_SCRIPTABLE_OBJECT: {"path": "Assets/Data/C.asset", "type_id": "Assembly-CSharp::GameConfig",
                                     "expected_so_catalog_digest": DIGEST},
        A.SET_ASSET_PROPERTY: {"asset": SO, "path": "material", "kind": "object", "value": json.dumps(MAT),
                               "expected_asset_token": T},
    }

    def test_every_capability_sends_exactly_its_arguments(self):
        for cap, inputs in self.CALLS.items():
            with self.subTest(cap):
                r = self.author(cap, sid=self.sid, **inputs)
                self.assertStatus(r, tdg.SUCCESS)
                command, args = self.b.author_calls[-1]
                self.assertEqual(command, A.COMMANDS[cap])
                self.assertEqual(set(args), set(A.INPUTS[cap]))
                self.assertEqual(args, A.parse_inputs(cap, inputs))
                self.assertEqual(r.mutation_performed, cap not in A.READ_ONLY)
                self.assertEqual(r.evidence_candidates, ())
                self.assertEqual("limitation" in r.data, cap not in A.READ_ONLY)
                if cap in A.CREATES:
                    self.assertIn("LIVE_ASSET_CREATED", self.codes(r))
                elif cap not in A.READ_ONLY:
                    self.assertIn("LIVE_ASSET_SAVED", self.codes(r))
                else:
                    self.assertEqual(self.codes(r), set())
        req = sorted((self.b.live / "claimed").glob("*.json"), key=lambda f: f.stat().st_mtime)[-1]
        self.assertEqual(json.loads(req.read_text())["schema"], "gpos.unity.live.request/4")

    def test_bad_inputs_are_refused_before_anything_is_sent(self):
        before = len(self.b.claimed_ids)
        r = self.author(A.CREATE_MATERIAL, sid=self.sid, path="Packages/com.x/H.mat", shader=MAT,
                        expected_shader_catalog_digest=DIGEST)
        self.assertStatus(r, tdg.INVALID_REQUEST, "LIVE_ASSET_PATH_INVALID")
        r = self.author(A.SET_ASSET_PROPERTY, sid=self.sid, asset=SO, path="go", kind="object",
                        value=json.dumps(SCENE_ID), expected_asset_token=T)
        self.assertStatus(r, tdg.INVALID_REQUEST, "LIVE_VALUE_INVALID")
        self.assertFalse(r.mutation_performed)
        self.assertEqual(len(self.b.claimed_ids), before)

    def test_refusals_map_and_report_whether_a_mutation_began(self):
        table = {"ASSET_REFUSED": ("LIVE_ASSET_REFUSED", tdg.INVALID_REQUEST),
                 "ASSET_PATH_INVALID": ("LIVE_ASSET_PATH_INVALID", tdg.INVALID_REQUEST),
                 "ASSET_LIMIT": ("LIVE_ASSET_LIMIT", tdg.INVALID_REQUEST),
                 "SHADER_NOT_IN_CATALOG": ("LIVE_SHADER_NOT_IN_CATALOG", tdg.INVALID_REQUEST),
                 "ASSET_CONFLICT": ("LIVE_ASSET_CONFLICT", tdg.CONFLICT),
                 "ASSET_DIRTY": ("LIVE_ASSET_DIRTY", tdg.CONFLICT),
                 "ASSET_EXISTS": ("LIVE_ASSET_EXISTS", tdg.CONFLICT),
                 "ASSET_NOT_EDITABLE": ("LIVE_ASSET_NOT_EDITABLE", tdg.CONFLICT),
                 "CREATE_INCOMPLETE": ("LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT),
                 "CATALOG_CHANGED": ("LIVE_CATALOG_CHANGED", tdg.CONFLICT),
                 "TYPE_NOT_IN_CATALOG": ("LIVE_TYPE_NOT_IN_CATALOG", tdg.INVALID_REQUEST),
                 "PROPERTY_UNSUPPORTED": ("LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST),
                 "VALUE_INVALID": ("LIVE_VALUE_INVALID", tdg.INVALID_REQUEST),
                 "VALUE_NOT_APPLIED": ("LIVE_VALUE_INVALID", tdg.INVALID_REQUEST),
                 "OBJECT_NOT_FOUND": ("LIVE_OBJECT_NOT_FOUND", tdg.CONFLICT),
                 "OBJECT_REFUSED": ("LIVE_OBJECT_REFUSED", tdg.INVALID_REQUEST),
                 "EDITOR_BUSY": ("EDITOR_BUSY", tdg.CONFLICT)}
        inputs = self.CALLS[A.SET_MATERIAL_PROPERTY]
        for bridge_code, (code, status) in table.items():
            for started in (False, True):
                with self.subTest(f"{bridge_code} started={started}"):
                    data = {"mutation_started": True, "import_performed": True, "value_persisted": False} \
                        if started else {"mutation_started": False}
                    self.b.author_reply = lambda c, a, bc=bridge_code, d=data: ("REFUSED", bc, d)
                    r = self.author(A.SET_MATERIAL_PROPERTY, sid=self.sid, **inputs)
                    self.assertStatus(r, status, code)
                    self.assertEqual(r.mutation_performed, started)
        self.b.author_reply = lambda c, a: ("REFUSED", "ASSET_CONFLICT", {"mutation_started": True})
        for cap in A.READ_ONLY:
            self.assertFalse(self.author(cap, sid=self.sid, **self.CALLS[cap]).mutation_performed)

    def test_a_targeted_import_is_reported_as_a_mutation(self):
        """Correction 3: once the bridge imported the asset, a refusal still reports mutation_performed, and the
        data says no requested value was persisted."""
        for code, why in (("ASSET_CONFLICT", "the token changed after the import"),
                          ("ASSET_CONFLICT", "the file changed on disk before the save"),
                          ("ASSET_DIRTY", "the import left the asset dirty")):
            with self.subTest(why):
                data = {"mutation_started": True, "import_performed": True, "value_persisted": False,
                        "reverted": True, "restored": True}
                self.b.author_reply = lambda c, a, bc=code, d=data: ("REFUSED", bc, d)
                r = self.author(A.SET_ASSET_PROPERTY, sid=self.sid, **self.CALLS[A.SET_ASSET_PROPERTY])
                self.assertTrue(r.mutation_performed)
                self.assertEqual((r.data["import_performed"], r.data["value_persisted"]), (True, False))
        self.b.author_reply = lambda c, a: ("REFUSED", "ASSET_DIRTY", {"mutation_started": False})
        r = self.author(A.SET_ASSET_PROPERTY, sid=self.sid, **self.CALLS[A.SET_ASSET_PROPERTY])
        self.assertStatus(r, tdg.CONFLICT, "LIVE_ASSET_DIRTY")
        self.assertFalse(r.mutation_performed)                       # refused before the import

    def test_the_commit_point_and_failures(self):
        cases = (("PERSISTENCE_UNKNOWN", {"mutation_started": True, "commit_started": True}, "LIVE_OUTCOME_UNKNOWN",
                  tdg.OUTCOME_UNKNOWN, True),
                 ("PERSISTENCE_UNKNOWN", None, "LIVE_OUTCOME_UNKNOWN", tdg.OUTCOME_UNKNOWN, True),
                 ("ROLLBACK_INCOMPLETE", {"mutation_started": True, "reverted": True, "restored": False},
                  "LIVE_ROLLBACK_INCOMPLETE", tdg.FAILED, True),
                 ("AUTHORING_FAILED", {"mutation_started": True, "compensated": "TEMP_REMOVED"}, "LIVE_AUTHORING_FAILED",
                  tdg.FAILED, True),
                 ("BRIDGE_INTERNAL_ERROR", {"mutation_started": False}, "LIVE_PROTOCOL_ERROR", tdg.INTERNAL_ERROR, False))
        for bridge_code, data, code, status, mutated in cases:
            with self.subTest(bridge_code):
                self.b.author_reply = lambda c, a, bc=bridge_code, d=data: ("FAILED", bc, d)
                r = self.author(A.SET_MATERIAL_PROPERTY, sid=self.sid, **self.CALLS[A.SET_MATERIAL_PROPERTY])
                self.assertStatus(r, status, code)
                self.assertEqual(r.mutation_performed, mutated)
        self.b.author_reply = lambda c, a: ("FAILED", "PERSISTENCE_UNKNOWN", None)
        count = published(self.b, "set-material-property")
        self.author(A.SET_MATERIAL_PROPERTY, sid=self.sid, **self.CALLS[A.SET_MATERIAL_PROPERTY])
        time.sleep(0.5)
        self.assertEqual(published(self.b, "set-material-property"), count + 1)    # never retried

    def test_creation_recovery_is_reported(self):
        recovered = [{"txn_id": "a" * 32, "final_path": "Assets/Materials/Old.mat", "outcome": "TEMP_REMOVED",
                      "changed": True},
                     {"txn_id": "b" * 32, "final_path": "Assets/Materials/Kept.mat", "outcome": "FINAL_KEPT",
                      "changed": False}]
        self.b.author_reply = lambda c, a: ("OK", None, {"created": {"path": a["path"]}, "recovered": recovered})
        r = self.author(A.CREATE_MATERIAL, sid=self.sid, **self.CALLS[A.CREATE_MATERIAL])
        self.assertStatus(r, tdg.SUCCESS, "LIVE_ASSET_CREATED")
        self.assertEqual([d.code for d in r.diagnostics].count("LIVE_ASSET_CREATE_RECOVERED"), 2)
        self.b.author_reply = lambda c, a: ("REFUSED", "CREATE_INCOMPLETE",
                                            {"mutation_started": True, "recovered": recovered[:1],
                                             "transaction": {"txn_id": "c" * 32}, "state": {"temp": "DIFFERENT"}})
        r = self.author(A.CREATE_MATERIAL, sid=self.sid, **self.CALLS[A.CREATE_MATERIAL])
        self.assertStatus(r, tdg.CONFLICT, "LIVE_ASSET_CREATE_INCOMPLETE")
        self.assertIn("LIVE_ASSET_CREATE_RECOVERED", self.codes(r))
        self.assertTrue(r.mutation_performed)
        self.assertEqual(r.data["state"], {"temp": "DIFFERENT"})
        self.b.author_reply = lambda c, a: ("REFUSED", "CREATE_INCOMPLETE", {"mutation_started": False})
        r = self.author(A.CREATE_SCRIPTABLE_OBJECT, sid=self.sid, **self.CALLS[A.CREATE_SCRIPTABLE_OBJECT])
        self.assertStatus(r, tdg.CONFLICT, "LIVE_ASSET_CREATE_INCOMPLETE")
        self.assertFalse(r.mutation_performed)

    def test_busy_withdrawn_unknown_and_incompatible(self):
        inputs = self.CALLS[A.CREATE_MATERIAL]
        self.b.phase = "PLAYING"
        r = self.author(A.CREATE_MATERIAL, sid=self.sid, **inputs)
        self.assertStatus(r, tdg.CONFLICT, "EDITOR_BUSY")
        self.assertFalse(r.mutation_performed)
        self.b.phase = "EDIT"
        self.b.mode = "stall"
        r = self.author(A.CREATE_MATERIAL, sid=self.sid, timeout=1, **inputs)
        self.assertStatus(r, tdg.CANCELLED, "LIVE_REQUEST_WITHDRAWN")
        self.assertFalse(r.mutation_performed)
        self.b.mode = "claim-only"
        r = self.author(A.CREATE_MATERIAL, sid=self.sid, timeout=1, **inputs)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertTrue(r.mutation_performed)
        self.b.mode = "interrupt"
        r = self.author(A.ASSET_FIND, sid=self.sid, **self.CALLS[A.ASSET_FIND])
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertFalse(r.mutation_performed)
        self.b.mode = None
        self.b.protocol, self.b.bridge_version = "gpos.unity.live/2", "1.1.0"
        self.b.publish("READY")
        before = len(self.b.author_calls)
        r = self.author(A.ASSET_TYPES, sid=self.sid, catalog="KINDS")
        self.assertStatus(r, tdg.INCOMPATIBLE, "LIVE_BRIDGE_INCOMPATIBLE")
        self.assertEqual(len(self.b.author_calls), before)


# ---------------------------------------------------------------- E  boundaries (source)

class E_Boundaries(unittest.TestCase):
    SOURCES = ("AssetAuthoring.cs", "AssetResolver.cs", "AssetCatalogs.cs", "Core/AssetRules.cs")

    def text(self, name):
        lines = (EDITOR_SOURCE / name).read_text().splitlines()
        return "\n".join(line.split("//")[0] if not line.lstrip().startswith("//") else "" for line in lines)

    def test_no_forbidden_mechanism(self):
        forbidden = ("System.Reflection", "Assembly.Load", "Activator.", ".Invoke(", "GetMethod(", "Type.GetType",
                     "ExecuteMenuItem", "executeMethod", "AssetDatabase.SaveAssets(", "AssetDatabase.Refresh",
                     "AssetDatabase.CreateFolder", "AssetDatabase.RenameAsset", "AssetDatabase.CopyAsset",
                     "MoveAssetToTrash", "DeleteAssets(", "ForceReserializeAssets", "StartAssetEditing",
                     "AssetImporter", "TextureImporter", "ModelImporter", "SetLabels", "Provider.Checkout",
                     "Provider.Add", "GetInstanceID", "InstanceIDToObject", "EntityId", "Process.Start", "Socket",
                     "HttpClient", "WebRequest", "DisplayDialog", "OpenScene", "SaveScene", "PrefabStage",
                     "ApplyPrefabInstance", "RevertPrefabInstance", "UnpackPrefabInstance", "SaveAsPrefabAsset",
                     "managedReferenceValue", "arraySize", "InsertArrayElement", "DeleteArrayElement",
                     "animationCurveValue", "gradientValue", "EnableKeyword", "DisableKeyword", "renderQueue",
                     "SetOverrideTag", "SetShaderPassEnabled", ".material ", ".material;", "Undo.ClearAll",
                     "LoadAssetAtPath(", "GetBuiltinResource<Shader>", "EditorApplication.Exit", "Kill(")
        for name in self.SOURCES:
            text = self.text(name)
            for word in forbidden:
                with self.subTest(f"{name}: {word}"):
                    self.assertNotIn(word, text)
        core = self.text("Core/AssetRules.cs")
        for word in ("File.", "Directory.", "AssetDatabase", "UnityEngine", "UnityEditor"):
            self.assertNotIn(word, core)
        for name in ("AssetResolver.cs", "AssetCatalogs.cs"):          # they only read
            text = self.text(name)
            for word in ("AssetDatabase.CreateAsset", "AssetDatabase.DeleteAsset", "AssetDatabase.MoveAsset",
                         "AssetDatabase.ImportAsset", "SaveAssetIfDirty", "SetDirty", "File.Write", "File.Delete",
                         "Directory.Create", "Undo."):
                self.assertNotIn(word, text, f"{name}: {word}")

    def test_every_write_primitive_appears_exactly_where_reviewed(self):
        text = self.text("AssetAuthoring.cs")
        counts = {"AssetDatabase.SaveAssetIfDirty(": 1, "AssetDatabase.CreateAsset(": 1, "AssetDatabase.MoveAsset(": 1,
                  "AssetDatabase.ValidateMoveAsset(": 1, "AssetDatabase.ImportAsset(": 2, "AssetDatabase.DeleteAsset(": 3,
                  "mkdir(AssetFiles.Full(scratch)": 1, "File.Delete(": 1, "Ipc.AtomicReplace(": 1, "AfterStep": 2,
                  "Undo.RevertAllDownToGroup(group)": 1, "Undo.IncrementCurrentGroup()": 2}
        for word, n in counts.items():
            with self.subTest(word):
                self.assertEqual(text.count(word), n)
        self.assertEqual(text.count("AssetDatabase.DeleteAsset(t.TempPath)"), 2)      # only a proven temporary asset
        self.assertEqual(text.count("AssetDatabase.DeleteAsset(t.ScratchFolder)"), 1)  # only the proven scratch folder
        self.assertNotIn("DeleteAsset(path", text)
        self.assertNotIn("DeleteAsset(t.FinalPath", text)
        self.assertEqual(text.count("AssetDatabase.ImportAsset(path)"), 1)             # the one targeted import
        self.assertEqual(text.count("AssetDatabase.ImportAsset(scratch)"), 1)          # GPOS's own new scratch folder

    def test_the_persistence_sequence_is_in_order(self):
        text = self.text("AssetAuthoring.cs")
        persist = text[text.index("static Dictionary<string, object> Persist("):text.index("static Dictionary<string, object> SetMaterialProperty(")]
        order = ("EditorUtility.IsDirty(r.Object)", "AssetDatabase.IsOpenForEdit(path", "AssetDatabase.ImportAsset(path)",
                 "before = State(r)", "before.Token != expected", "Undo.IncrementCurrentGroup()", "change(o)",
                 "holds(o)", "Undo.CollapseUndoOperations(group)", 'Step("pre-save", path)',
                 "fileNow != before.File || metaNow != before.Meta", "AssetDatabase.SaveAssetIfDirty(o)",
                 'Step("saved", path)', "s.Dirty || !holds(after.Object)")
        positions = [persist.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('"PERSISTENCE_UNKNOWN"', persist[persist.index("AssetDatabase.SaveAssetIfDirty(o)"):])
        self.assertNotIn('"PERSISTENCE_UNKNOWN"', persist[:persist.index("AssetDatabase.SaveAssetIfDirty(o)")])

    def test_the_creation_transaction_is_in_order(self):
        text = self.text("AssetAuthoring.cs")
        create = text[text.index("static Dictionary<string, object> Create(string kind"):text.index("sealed class CreateState")]
        order = ("Provider.isActive", "CheckFolder(folder)", "CheckAbsent(folder, path)", "CreateTxns.RecoverAll()",
                 "CreateTxns.Write(txn)", 'Step("prepared", path)', "Transact(")
        positions = [create.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))
        transact = text[text.index("static Dictionary<string, object> Transact("):text.index("static Refusal Compensate(")]
        order = ("mkdir(AssetFiles.Full(scratch)", "AssetDatabase.ImportAsset(scratch)", "CreateTxns.Write(txn)",
                 'Step("scratch", path)', "unknown content appeared in the scratch folder", "AssetDatabase.CreateAsset(st.Created, temp)",
                 "txn.Phase = CreateTxn.TempProven", 'Step("pre-move", path)', "CheckAbsent(folder, path)",
                 "AssetDatabase.ValidateMoveAsset(temp, path)", "st.MoveAttempted = true", "AssetDatabase.MoveAsset(temp, path)",
                 "txn.Phase = CreateTxn.FinalProven", "CreateTxns.RemoveScratch(txn)", "CreateTxns.Delete(txn)")
        positions = [transact.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))
        compensate = text[text.index("static Refusal Compensate("):text.index("internal static class CreateTxns")]
        self.assertIn("if (!st.MoveAttempted)", compensate)             # a moved asset is never removed in-process
        rules = (EDITOR_SOURCE / "Core" / "AssetRules.cs").read_text()
        self.assertIn('return "Assets/" + ScratchPrefix + txnId;', rules)   # the scratch place is derived
        self.assertNotIn('"temp_path"', rules)
        self.assertNotIn('"scratch_path"', rules)

    def test_the_python_side_starts_nothing(self):
        text = Path(A.__file__).read_text()
        for word in ("subprocess", "socket", "urllib", "http", "os.system", "eval(", "exec(", "open("):
            self.assertNotIn(word, text)


# ================================================================ real synthetic Unity (lab-owned batch Editors)

import test_unity_authoring as ta  # noqa: E402
import test_unity_live as tl  # noqa: E402

REAL = {"editors": []}


def setUpModule():
    if FAST:
        return
    if len(EDITORS) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A: {len(EDITORS)} Hub Unity Editors found; exactly "
                           f"one is required")
    if tl.EDITOR_PREFS.exists():
        REAL["prefs"] = tl.prefs_snapshot()
    REAL["upm"] = tl.upm_config_state()
    REAL["work"] = Path(tempfile.mkdtemp(prefix="gpos-assets-real-")).resolve()


def tearDownModule():
    try:
        for editor in ta.REAL_STATE["editors"]:
            editor.stop()
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


def asset_project(name):
    p = REAL["work"] / name / "p"
    shutil.copytree(FIXTURE, p)
    fixtures.make_asset_project(p / "Game", EDITOR_VERSION, EDITOR)
    shutil.copytree(tl.TESTKIT, p / "Game" / "Packages" / tl.TESTKIT.name)
    bi.install(p, p / "Game", bi.verify_source())
    return p


ok = ta.ok


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RealAssets(unittest.TestCase):
    """Shared helpers of the real asset groups."""

    lab = None
    s = None

    def run_cap(self, cap, **inputs):
        return self.lab.run(cap, **inputs)

    def human(self, **op):
        return self.lab.human(**op)

    def inspect(self, asset):
        return ok(self, self.run_cap(A.ASSET_INSPECT, asset=asset))

    def token(self, asset):
        return self.inspect(asset)["tokens"]["asset"]

    def file(self, rel):
        return self.lab.game / rel

    def catalogs(self):
        so = ok(self, self.run_cap(A.ASSET_TYPES, catalog="SCRIPTABLE_OBJECTS"))
        sh = ok(self, self.run_cap(A.ASSET_TYPES, catalog="SHADERS", query="GPOS/Test"))
        return so, sh

    def create_material(self, path, **kw):
        _, sh = self.catalogs()
        return self.run_cap(A.CREATE_MATERIAL, path=path, shader=self.s["shader"],
                            expected_shader_catalog_digest=sh["shader_catalog_digest"], **kw)

    def scratch(self):
        """The GPOS scratch folders below Assets/ (their .meta files are in leftovers())."""
        return sorted(p.name for p in (self.lab.game / "Assets").iterdir()
                      if p.name.startswith("GposAssetTxn-") and not p.name.endswith(".meta"))

    def leftovers(self):
        return sorted(p.name for p in (self.lab.game / "Assets").iterdir() if p.name.startswith("GposAssetTxn-"))

    def records(self):
        from gpos.tools.unity import identity as ident
        key = ident.project_key(ident.relative(self.lab.p, self.lab.game))
        d = self.lab.p / ".game" / "gpos-runtime" / "unity" / "asset-create-txn" / key
        return sorted(p for p in d.glob("*.json")) if d.is_dir() else []

    def set_mat(self, material, prop, kind, value, token=None):
        return self.run_cap(A.SET_MATERIAL_PROPERTY, material=material, property=prop, kind=kind, value=value,
                            expected_asset_token=token or self.token(material))

    def set_asset(self, asset, path, kind, value, token=None):
        return self.run_cap(A.SET_ASSET_PROPERTY, asset=asset, path=path, kind=kind, value=value,
                            expected_asset_token=token or self.token(asset))

    def ctoken(self, cid):
        return ok(self, self.run_cap(au.PROPERTIES, component=cid))["component_token"]

    def wait_edit(self, timeout=180):
        self.assertTrue(self.lab.editor.wait(lambda: (self.lab.editor.heartbeat().get("state") or {}).get("phase")
                                             == "EDIT", timeout))
        time.sleep(1)


def start(cls, name):
    cls.lab = ta.Lab(asset_project(name))
    cls.s = {}
    cls.lab.editor.launch()
    r = cls.lab.run(live.ATTACH, timeout=120)
    if r.status != tdg.SUCCESS:
        raise AssertionError(f"attach failed: {[d.message for d in r.diagnostics]}")
    cls.lab.sid = r.data["session_id"]
    cls.s.update(cls.lab.human(op="setup"))
    cls.s.update(cls.lab.human(op="setup-assets"))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R3_RealAssets(RealAssets):
    """One synthetic project, one lab Editor, one live session: every asset rule in order."""

    @classmethod
    def setUpClass(cls):
        start(cls, "assets")

    @classmethod
    def tearDownClass(cls):
        cls.lab.editor.stop()

    def test_01_catalogs(self):
        kinds = ok(self, self.run_cap(A.ASSET_TYPES, catalog="KINDS"))
        self.assertEqual([k["kind"] for k in kinds["kinds"]], list(A.KINDS))
        self.assertEqual({k["kind"]: k["authorable_extension"] for k in kinds["kinds"] if k["authorable_extension"]},
                         {"MATERIAL": ".mat", "SCRIPTABLE_OBJECT": ".asset"})
        so, sh = self.catalogs()
        types = {t["type_id"]: t for t in so["types"]}
        self.assertEqual(set(types), {"Assembly-CSharp::GameConfig", "Assembly-CSharp::PlainData",
                                      "Assembly-CSharp::CallbackData", "Assembly-CSharp::Changeable"})
        self.assertEqual({k: v["creatable"] for k, v in types.items()},
                         {"Assembly-CSharp::GameConfig": True, "Assembly-CSharp::PlainData": False,
                          "Assembly-CSharp::CallbackData": True, "Assembly-CSharp::Changeable": False})
        self.assertEqual((types["Assembly-CSharp::GameConfig"]["menu_name"],
                          types["Assembly-CSharp::GameConfig"]["file_name"]), ("GPOS/Game Config", "Config"))
        shader = sh["shaders"][0]
        self.assertEqual((shader["id"], shader["name"]), (self.s["shader"], "GPOS/Test"))
        props = {p["name"]: p for p in shader["properties"]}
        self.assertEqual({n: (p["kind"], p["refusal"]) for n, p in props.items()},
                         {"_Color": ("color", None), "_Tint": ("color", "HIDDEN"), "_Amount": ("range", None),
                          "_Count": ("int", None), "_Offset": ("vector", None), "_Scale": ("float", None),
                          "_MainTex": ("texture", None), "_Cube": ("texture", None), "_Vol": ("texture", None),
                          "_PerR": ("texture", "PER_RENDERER_DATA")})
        self.assertEqual((props["_Amount"]["range_min"], props["_Amount"]["range_max"]), (0, 1))
        self.assertEqual((props["_MainTex"]["dimension"], props["_Cube"]["dimension"], props["_Vol"]["dimension"]),
                         ("Tex2D", "Cube", "Tex3D"))
        everything = ok(self, self.run_cap(A.ASSET_TYPES, catalog="SHADERS"))
        self.assertGreater(everything["catalog_count"], 10)
        self.assertEqual(everything["shader_catalog_digest"], sh["shader_catalog_digest"])
        self.assertFalse(any(s["name"].startswith("Hidden/") for s in everything["shaders"]))
        self.s.update(so_digest=so["so_catalog_digest"], shader_digest=sh["shader_catalog_digest"])

    def test_02_typed_lookup_in_every_source(self):
        def find(kind, source="ASSETS", **kw):
            return ok(self, self.run_cap(A.ASSET_FIND, kind=kind, source=source, **kw))
        materials = {a["name"]: a for a in find("MATERIAL")["assets"]}
        self.assertEqual(set(materials), {"Base", "Other", "defaultMat"})     # the model's embedded material too ...
        self.assertEqual((materials["defaultMat"]["sub_asset"], materials["defaultMat"]["authorable"],
                          materials["defaultMat"]["path"]), (True, False, "Assets/Models/cube.obj"))   # ... read only
        self.assertEqual((materials["Base"]["authorable"], materials["Other"]["authorable"]), (True, True))
        self.s["model_material"] = materials["defaultMat"]["id"]
        self.assertEqual({a["id"] for a in find("TEXTURE")["assets"]},
                         {self.s["checker"], self.s["hero_texture"], self.s["sky"], self.s["volume"]})
        sprites = find("SPRITE")["assets"]
        self.assertEqual([(a["id"], a["sub_asset"]) for a in sprites], [(self.s["hero_sprite"], True)])
        self.assertEqual([a["id"] for a in find("AUDIO")["assets"]], [self.s["beep"]])
        self.assertEqual([a["id"] for a in find("MESH")["assets"]], [self.s["model_mesh"]])
        self.assertEqual({a["name"] for a in find("PREFAB")["assets"]}, {"Box", "Crate"})
        self.assertEqual([a["id"] for a in find("MODEL")["assets"]], [self.s["model"]])
        found = find("SCRIPTABLE_OBJECT")
        self.assertEqual({a["name"]: a["authorable"] for a in found["assets"]},
                         {"Existing": True, "Plain": True, "Callback": True, "Multi": False})
        self.assertEqual([a["name"] for a in find("MATERIAL", query="oth")["assets"]], ["Other"])
        self.assertEqual((find("MATERIAL", page="1")["assets"], find("MATERIAL", page="1")["next_page"]), ([], None))
        pkg = find("TEXTURE", "PACKAGE")["assets"]
        self.assertEqual([(a["path"], a["source"], a["authorable"]) for a in pkg],
                         [("Packages/com.gpos.fixture-assets/Textures/pkg.png", "PACKAGE", False)])
        self.assertEqual([a["id"] for a in find("MATERIAL", "PACKAGE")["assets"]], [self.s["pkg_mat"]])  # no Editor/ one
        self.s["pkg_tex"] = pkg[0]["id"]
        meshes = find("MESH", "BUILTIN")["assets"]
        self.assertEqual(len(meshes), 6)
        self.assertTrue(all(m["id"].startswith("GlobalObjectId_V1-4-0000000000000000e000000000000000-") and
                            m["path"] is None and m["source"] == "BUILTIN" for m in meshes))
        mats = find("MATERIAL", "BUILTIN")["assets"]
        self.assertEqual({m["name"] for m in mats}, {"Default-Material", "Sprites-Default"})
        self.s["builtin_material"] = next(m["id"] for m in mats if m["name"] == "Default-Material")
        self.s["builtin_cube"] = next(m["id"] for m in meshes if m["name"] == "Cube")
        self.assertEqual([m["name"] for m in find("SPRITE", "BUILTIN", query="uisprite")["assets"]], ["UISprite"])
        self.assertEqual(find("AUDIO", "BUILTIN")["assets"], [])

    def test_03_the_reference_boundary_and_inspection(self):
        for key in ("scene_asset", "script", "folder", "prefab_inner", "prefab_inner_collider", "editor_mat", "shader",
                    "part"):
            with self.subTest(key):
                r = self.run_cap(A.ASSET_INSPECT, asset=self.s[key])
                ok(self, r, "LIVE_ASSET_REFUSED", tdg.INVALID_REQUEST)
        ok(self, self.run_cap(A.ASSET_INSPECT, asset="GlobalObjectId_V1-3-" + "9" * 32 + "-2100000-0"),
           "LIVE_OBJECT_NOT_FOUND", tdg.CONFLICT)
        ok(self, self.run_cap(A.ASSET_INSPECT, asset=self.s["standard"]), "LIVE_ASSET_REFUSED",
           tdg.INVALID_REQUEST)          # a built-in resource outside the reviewed table
        base = self.inspect(self.s["base"])
        self.assertEqual((base["asset"]["kind"], base["asset"]["authorable"], base["shader"]["in_catalog"]),
                         ("MATERIAL", True, True))
        self.assertEqual({p["name"]: p["value"] for p in base["properties"]}["_Color"], [1, 1, 1, 1])
        self.assertEqual((base["dirty"], base["editable"], len(base["tokens"]["asset"])), (False, True, 32))
        self.assertEqual(base["file_sha256"], sha(self.file("Assets/Materials/Base.mat")))
        self.assertEqual(base["meta_sha256"], sha(self.file("Assets/Materials/Base.mat.meta")))
        sprite = self.inspect(self.s["hero_sprite"])
        self.assertEqual(sprite["sprite"]["texture"], self.s["hero_texture"])        # the Texture's own id
        prefab = self.inspect(self.s["prefab"])
        self.assertIn(self.s["prefab_collider"], [c["id"] for c in prefab["root_components"]])
        self.assertEqual(self.inspect(self.s["prefab_collider"])["asset"]["kind"], "PREFAB_COMPONENT")
        for key in ("pkg_mat", "builtin_material", "checker", "multi"):
            with self.subTest(key):
                d = self.inspect(self.s[key])
                self.assertFalse(d["asset"]["authorable"])
                self.assertNotIn("tokens", d)                                      # references only: no token
        config = self.inspect(self.s["config"])
        listed = {p["path"]: p for p in config["properties"]}
        self.assertNotIn("hidden", listed)
        self.assertEqual((listed["m_Script"]["writable"], listed["value"]["writable"], listed["arr"]["writable"]),
                         (False, True, False))

    def test_04_create_material_and_scriptable_object(self):
        so, sh = self.catalogs()
        r = self.create_material("Assets/Materials/Hero.mat")
        data = ok(self, r, "LIVE_ASSET_CREATED")
        self.assertTrue(r.mutation_performed)
        created = data["created"]
        self.assertEqual((created["path"], created["kind"], created["name"], created["authorable"], data["undoable"]),
                         ("Assets/Materials/Hero.mat", "MATERIAL", "Hero", True, False))
        self.assertTrue(created["id"].startswith("GlobalObjectId_V1-3-") and created["id"].endswith("-2100000-0"))
        guid = created["id"].split("-")[2]
        self.assertIn(f"guid: {guid}", self.file("Assets/Materials/Hero.mat.meta").read_text())
        self.assertEqual(data["file_sha256"], sha(self.file("Assets/Materials/Hero.mat")))
        self.assertEqual(data["tokens"]["asset"], self.token(created["id"]))
        self.assertEqual((self.leftovers(), self.records()), ([], []))                 # nothing of the transaction is left
        self.assertEqual(sorted(p.name for p in self.file("Assets/Materials").iterdir()),
                         ["Base.mat", "Base.mat.meta", "Hero.mat", "Hero.mat.meta", "Other.mat", "Other.mat.meta"])
        self.s["hero"] = created["id"]
        before = sha(self.file("Assets/Materials/Hero.mat"))
        for path, code in (("Assets/Materials/Hero.mat", "LIVE_ASSET_EXISTS"), ("Assets/Materials/hero.mat", "LIVE_ASSET_EXISTS"),
                           ("Assets/Materials/HERO.mat", "LIVE_ASSET_EXISTS"), ("Assets/Missing/Hero.mat", "LIVE_ASSET_PATH_INVALID")):
            with self.subTest(path):
                r = self.create_material(path)
                ok(self, r, code, tdg.CONFLICT if code == "LIVE_ASSET_EXISTS" else tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)
        self.assertEqual(sha(self.file("Assets/Materials/Hero.mat")), before)          # never overwritten
        self.assertFalse(self.file("Assets/Missing").exists())                          # no folder was created
        self.file("Assets/Materials/Orphan.mat.meta").write_text("fileFormatVersion: 2\nguid: " + "1" * 32 + "\n")
        r = self.create_material("Assets/Materials/Orphan.mat")
        ok(self, r, "LIVE_ASSET_EXISTS", tdg.CONFLICT)                                    # an orphan .meta is never replaced
        self.assertEqual(self.file("Assets/Materials/Orphan.mat.meta").read_text(), "fileFormatVersion: 2\nguid: " + "1" * 32 + "\n")
        self.file("Assets/Materials/Orphan.mat.meta").unlink()
        os.symlink(self.file("Assets/Materials"), self.file("Assets/Linked"))
        ok(self, self.create_material("Assets/Linked/Hero2.mat"), "LIVE_ASSET_PATH_INVALID", tdg.INVALID_REQUEST)
        self.assertFalse(self.file("Assets/Materials/Hero2.mat").exists())
        self.file("Assets/Linked").unlink()
        ok(self, self.run_cap(A.CREATE_MATERIAL, path="Assets/Materials/H3.mat", shader=self.s["standard"],
                              expected_shader_catalog_digest="0" * 64), "LIVE_CATALOG_CHANGED", tdg.CONFLICT)
        ok(self, self.run_cap(A.CREATE_MATERIAL, path="Assets/Materials/H3.mat", shader=self.s["hero_texture"],
                              expected_shader_catalog_digest=sh["shader_catalog_digest"]), "LIVE_SHADER_NOT_IN_CATALOG",
           tdg.INVALID_REQUEST)
        ok(self, self.run_cap(A.CREATE_SCRIPTABLE_OBJECT, path="Assets/Data/P.asset", type_id="Assembly-CSharp::PlainData",
                              expected_so_catalog_digest=so["so_catalog_digest"]), "LIVE_TYPE_NOT_IN_CATALOG",
           tdg.INVALID_REQUEST)                                                         # editable, not creatable
        ok(self, self.run_cap(A.CREATE_SCRIPTABLE_OBJECT, path="Assets/Data/E.asset", type_id="Assembly-CSharp-Editor::EditorData",
                              expected_so_catalog_digest=so["so_catalog_digest"]), "LIVE_TYPE_NOT_IN_CATALOG",
           tdg.INVALID_REQUEST)
        for type_id, path in (("Assembly-CSharp::GameConfig", "Assets/Data/Config.asset"),
                              ("Assembly-CSharp::CallbackData", "Assets/Data/Made.asset")):
            data = ok(self, self.run_cap(A.CREATE_SCRIPTABLE_OBJECT, path=path, type_id=type_id,
                                         expected_so_catalog_digest=so["so_catalog_digest"]), "LIVE_ASSET_CREATED")
            self.assertEqual((data["created"]["type"], data["created"]["kind"]), (type_id, "SCRIPTABLE_OBJECT"))
            self.assertTrue(data["created"]["id"].endswith("-11400000-0"))
        self.assertIn("enables: 1", self.file("Assets/Data/Made.asset").read_text())    # OnEnable ran (TOOL_INHERENT)
        self.s["new_config"] = self.lab.human(op="id-of", path="Assets/Data/Config.asset")["id"]
        log = self.file("Temp/gpos-postprocess.log").read_text()
        self.assertIn("Assets/Data/Config.asset", log)                                  # postprocessors saw the move ...
        self.assertTrue(re.search(r"Assets/GposAssetTxn-[0-9a-f]{32}/Config\.asset", log))   # ... and the scratch import

    def test_05_the_temporary_namespace_never_overwrites(self):
        """Correction 2: nothing inside the GPOS-owned scratch folder or at the final path is ever overwritten."""
        cases = (("T1a", "T1a.mat", "user bytes"),                              # an existing temp-path file
                 ("T1b", "T1b.mat.meta", "user meta"))                          # an orphan temp .meta
        for stem, name, text in cases:
            with self.subTest(name):
                self.human(op="arm", step="scratch", action="write-scratch", path=name, text=text)
                r = self.create_material(f"Assets/Materials/{stem}.mat")
                ok(self, r, "LIVE_ROLLBACK_INCOMPLETE", tdg.FAILED)
                self.assertTrue(r.mutation_performed)
                (scratch,) = self.scratch()
                self.assertEqual((self.file(f"Assets/{scratch}") / name).read_text(), text)   # never overwritten
                self.assertEqual(sorted(p.name for p in self.file(f"Assets/{scratch}").iterdir()), [name])
                self.assertEqual(len(self.records()), 1)
                self.assertFalse(self.file(f"Assets/Materials/{stem}.mat").exists())
                r = self.create_material(f"Assets/Materials/{stem}.mat")                     # stays refused, untouched
                ok(self, r, "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
                self.assertFalse(r.mutation_performed)
                self.assertEqual((self.file(f"Assets/{scratch}") / name).read_text(), text)
                (self.file(f"Assets/{scratch}") / name).unlink()                              # the Human removes it
                r = self.create_material(f"Assets/Materials/{stem}.mat")
                ok(self, r, "LIVE_ASSET_CREATE_RECOVERED")                                     # NOTHING_CREATED, then done
                self.assertEqual(r.data["recovered"][0]["outcome"], "NOTHING_CREATED")
                self.assertEqual((self.leftovers(), self.records()), ([], []))
                self.assertTrue(self.file(f"Assets/Materials/{stem}.mat").exists())
        # a temporary namespace collision: the scratch folder's name is taken before GPOS creates it
        self.human(op="arm", step="prepared", action="collide-scratch", text="not GPOS's")
        r = self.create_material("Assets/Materials/T2.mat")
        ok(self, r, "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)                                                 # nothing was written
        (scratch,) = self.scratch()
        self.assertEqual((self.file(f"Assets/{scratch}") / "mine.txt").read_text(), "not GPOS's")
        self.assertEqual(self.records(), [])
        shutil.rmtree(self.file(f"Assets/{scratch}"))
        # a competing final file appears immediately before the move: it wins, GPOS removes only its own temp
        self.human(op="arm", step="pre-move", action="write", path="Assets/Materials/T3.mat", text="competing")
        r = self.create_material("Assets/Materials/T3.mat")
        ok(self, r, "LIVE_ASSET_EXISTS", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)
        self.assertEqual(r.data["compensated"], "TEMP_REMOVED")
        self.assertEqual(self.file("Assets/Materials/T3.mat").read_text(), "competing")
        self.assertEqual((self.leftovers(), self.records()), ([], []))
        self.file("Assets/Materials/T3.mat").unlink()
        # unknown data appears in the scratch folder before its cleanup: the asset exists, the scratch stays
        self.human(op="arm", step="final-proven", action="write-scratch", path="unknown.txt", text="someone's")
        r = self.create_material("Assets/Materials/T4.mat")
        ok(self, r, "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)
        self.assertEqual(r.data["created"]["path"], "Assets/Materials/T4.mat")
        (scratch,) = self.scratch()
        self.assertEqual((self.file(f"Assets/{scratch}") / "unknown.txt").read_text(), "someone's")
        ok(self, self.create_material("Assets/Materials/T5.mat"), "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertEqual((self.file(f"Assets/{scratch}") / "unknown.txt").read_text(), "someone's")   # untouched
        (self.file(f"Assets/{scratch}") / "unknown.txt").unlink()
        r = self.create_material("Assets/Materials/T5.mat")
        ok(self, r, "LIVE_ASSET_CREATED")
        self.assertEqual(r.data["recovered"][0]["outcome"], "FINAL_KEPT")
        self.assertTrue(self.file("Assets/Materials/T4.mat").exists())                        # the final is kept
        self.assertEqual((self.leftovers(), self.records()), ([], []))
        self.assertEqual(self.human(op="armed")["armed"], False)

    def test_06_material_properties(self):
        hero = self.s["hero"]
        path = self.file("Assets/Materials/Hero.mat")
        cases = (("_Color", "color", [1, 0.25, 0, 1]), ("_Offset", "vector", [1, 2, 3, 4]), ("_Scale", "float", -2.5),
                 ("_Amount", "range", 0.75), ("_Count", "int", 7), ("_MainTex", "texture", self.s["hero_texture"]),
                 ("_MainTex", "texture", self.s["checker"]), ("_Cube", "texture", self.s["sky"]),
                 ("_Vol", "texture", self.s["volume"]), ("_MainTex", "texture", self.s["pkg_tex"]),
                 ("_MainTex", "texture", None))
        for prop, kind, value in cases:
            with self.subTest(f"{prop}={value}"):
                before = sha(path)
                r = self.set_mat(hero, prop, kind, value)
                data = ok(self, r, "LIVE_ASSET_SAVED")
                self.assertTrue(r.mutation_performed)
                self.assertEqual(data["property"]["value"], value)
                self.assertEqual((data["value_persisted"], data["import_performed"]), (True, True))
                self.assertNotEqual(sha(path), before)
                self.assertEqual(data["file_sha256"], sha(path))
                self.assertEqual(self.human(op="dirty", target=hero)["dirty"], False)
                self.assertEqual(self.human(op="undo-name")["group"], f"GPOS: set {prop} of Hero")
        refused = ((self.s["hero_sprite"], "_MainTex", "texture", "LIVE_VALUE_INVALID"),     # Correction 4
                   (self.s["sky"], "_MainTex", "texture", "LIVE_VALUE_INVALID"),              # a Cube is not a 2D texture
                   (self.s["checker"], "_Cube", "texture", "LIVE_VALUE_INVALID"),
                   (self.s["base"], "_MainTex", "texture", "LIVE_VALUE_INVALID"),              # not a texture
                   (2, "_Amount", "range", "LIVE_VALUE_INVALID"), ([1, 1, 1, 1], "_Tint", "color", "LIVE_PROPERTY_UNSUPPORTED"),
                   (self.s["checker"], "_PerR", "texture", "LIVE_PROPERTY_UNSUPPORTED"),
                   (1, "_Nope", "float", "LIVE_PROPERTY_UNSUPPORTED"), (1, "_Color", "float", "LIVE_PROPERTY_UNSUPPORTED"))
        before, token = sha(path), self.token(hero)
        for value, prop, kind, code in refused:
            with self.subTest(f"refused {prop} {value}"):
                r = self.set_mat(hero, prop, kind, value, token=token)
                ok(self, r, code, tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)                                      # refused before the import
        self.assertEqual((sha(path), self.token(hero)), (before, token))
        texture = self.inspect(hero)
        self.assertEqual({p["name"]: p["value"] for p in texture["properties"]}["_Cube"], self.s["sky"])
        for key in ("pkg_mat", "builtin_material"):
            ok(self, self.set_mat(self.s[key], "_Color", "color", [1, 1, 1, 1], token=T), "LIVE_ASSET_REFUSED",
               tdg.INVALID_REQUEST)

    def test_07_scriptable_object_properties(self):
        config, path = self.s["new_config"], self.file("Assets/Data/Config.asset")
        for prop, kind, value in (("value", "int32", 5), ("speed", "float32", 1.5), ("title", "string", "Hello"),
                                  ("material", "object", self.s["hero"]), ("icon", "object", self.s["checker"]),
                                  ("badge", "object", self.s["hero_sprite"]), ("next", "object", config),
                                  ("material", "object", self.s["builtin_material"]), ("material", "object", None)):
            with self.subTest(f"{prop}={value}"):
                data = ok(self, self.set_asset(config, prop, kind, value), "LIVE_ASSET_SAVED")
                self.assertEqual(data["property"]["value"], value)
        self.assertIn("value: 5", path.read_text())
        before, token = sha(path), self.token(config)
        for prop, kind, value, code in (("icon", "object", self.s["hero_sprite"], "LIVE_VALUE_INVALID"),
                                        ("badge", "object", self.s["checker"], "LIVE_VALUE_INVALID"),
                                        ("material", "object", self.s["checker"], "LIVE_VALUE_INVALID"),
                                        ("next", "object", self.s["plain"], "LIVE_VALUE_INVALID"),
                                        ("material", "object", self.s["script"], "LIVE_ASSET_REFUSED"),
                                        ("hidden", "int32", 1, "LIVE_PROPERTY_UNSUPPORTED"),
                                        ("m_Name", "string", "X", "LIVE_PROPERTY_UNSUPPORTED"),
                                        ("m_Script", "object", self.s["script"], "LIVE_PROPERTY_UNSUPPORTED"),
                                        ("curve", "int32", 1, "LIVE_PROPERTY_UNSUPPORTED")):
            with self.subTest(f"refused {prop}"):
                r = self.set_asset(config, prop, kind, value, token=token)
                ok(self, r, code, tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)
        self.assertEqual(sha(path), before)
        ok(self, self.set_asset(self.s["multi"], "value", "int32", 1, token=T), "LIVE_ASSET_REFUSED", tdg.INVALID_REQUEST)
        # project code keeps another value: the edit is reverted in memory and nothing is saved
        callback = self.s["callback"]
        cpath = self.file("Assets/Data/Callback.asset")
        before = sha(cpath)
        r = self.set_asset(callback, "value", "int32", 50)
        ok(self, r, "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST)
        self.assertTrue(r.mutation_performed)                                                # the import happened
        self.assertEqual((r.data["reverted"], r.data["restored"], r.data["value_persisted"]), (True, True, False))
        self.assertEqual(sha(cpath), before)
        self.assertEqual(self.lab.human(op="read", target=callback, path="value")["value"], "0")
        if self.human(op="dirty", target=callback)["dirty"]:
            self.human(op="save-asset", target=callback)                                      # the Human decides

    def test_08_scene_references_to_assets(self):
        refs, audio = self.s["refs"], self.s["audio"]
        good = (("mat", self.s["hero"]), ("mat", self.s["pkg_mat"]), ("mat", self.s["builtin_material"]),
                ("tex", self.s["checker"]), ("tex", self.s["sky"]), ("tex2d", self.s["hero_texture"]),
                ("sprite", self.s["hero_sprite"]), ("clip", self.s["beep"]), ("mesh", self.s["model_mesh"]),
                ("mesh", self.s["builtin_cube"]), ("prefab", self.s["prefab"]), ("prefab", self.s["model"]),
                ("col", self.s["prefab_collider"]), ("config", self.s["new_config"]), ("cube", self.s["sky"]),
                ("anything", self.s["pkg_tex"]), ("anything", self.s["prefab_renderer"]))
        for field, value in good:
            with self.subTest(f"{field}={value}"):
                data = ok(self, self.run_cap(au.SET_PROPERTY, component=refs, path=field, kind="object", value=value,
                                             expected_component_token=self.ctoken(refs)))
                self.assertEqual(data["property"]["value"], value)
                self.assertEqual(self.lab.human(op="read", target=refs, path=field)["value"], value)
        token = self.ctoken(refs)
        for field, value, code in (("tex", self.s["hero_sprite"], "LIVE_VALUE_INVALID"),
                                   ("sprite", self.s["hero_texture"], "LIVE_VALUE_INVALID"),
                                   ("tex2d", self.s["sky"], "LIVE_VALUE_INVALID"),
                                   ("clip", self.s["checker"], "LIVE_VALUE_INVALID"),
                                   ("col", self.s["prefab_renderer"], "LIVE_VALUE_INVALID"),
                                   ("config", self.s["plain"], "LIVE_VALUE_INVALID"),
                                   ("prefab", self.s["prefab_inner"], "LIVE_ASSET_REFUSED"),
                                   ("col", self.s["prefab_inner_collider"], "LIVE_ASSET_REFUSED"),
                                   ("anything", self.s["scene_asset"], "LIVE_ASSET_REFUSED"),
                                   ("anything", self.s["script"], "LIVE_ASSET_REFUSED"),
                                   ("anything", self.s["folder"], "LIVE_ASSET_REFUSED"),
                                   ("anything", self.s["editor_mat"], "LIVE_ASSET_REFUSED")):
            with self.subTest(f"refused {field}={value}"):
                r = self.run_cap(au.SET_PROPERTY, component=refs, path=field, kind="object", value=value,
                                 expected_component_token=token)
                ok(self, r, code, tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)
        self.assertEqual(self.ctoken(refs), token)
        listed = {e["path"]: e for e in ok(self, self.run_cap(au.PROPERTIES, component=audio))["properties"]}
        self.assertEqual((listed["m_audioClip"]["refusal"], listed["m_Resource"]["writable"]), ("AUDIO_USES_RESOURCE", True))
        ok(self, self.run_cap(au.SET_PROPERTY, component=audio, path="m_audioClip", kind="object", value=self.s["beep"],
                              expected_component_token=self.ctoken(audio)), "LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST)
        r = self.run_cap(au.SET_PROPERTY, component=audio, path="m_Resource", kind="object", value=self.s["checker"],
                         expected_component_token=self.ctoken(audio))
        ok(self, r, "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST)                  # audio only, checked before any write
        self.assertFalse(r.mutation_performed)
        data = ok(self, self.run_cap(au.SET_PROPERTY, component=audio, path="m_Resource", kind="object",
                                     value=self.s["beep"], expected_component_token=self.ctoken(audio)))
        self.assertEqual(data["property"]["value"], self.s["beep"])
        self.assertEqual(self.lab.human(op="read", target=audio, path="m_Resource")["value"], self.s["beep"])

    def test_09_renderer_material_slots(self):
        slots, none, instance = self.s["slots"], self.s["no_slots"], self.s["instance_renderer"]
        count = self.human(op="material-instances")["count"]
        data = ok(self, self.run_cap(au.SET_RENDERER_MATERIAL, renderer=slots, material=self.s["hero"], slot="0",
                                     expected_component_token=self.ctoken(slots)))
        self.assertEqual((data["operation"], data["slot_count"], data["material"]), ("REPLACE", 1, self.s["hero"]))
        self.assertEqual(self.human(op="shared-materials", target=slots)["ids"], [self.s["hero"]])
        self.assertEqual(self.human(op="material-instances")["count"], count)          # no Material was instantiated
        self.assertEqual(self.human(op="undo-name")["group"], "GPOS: set material 0 of MeshRenderer on Slots")
        self.human(op="undo")
        self.assertEqual(self.human(op="shared-materials", target=slots)["ids"], [self.s["other"]])
        self.human(op="redo")
        self.assertEqual(self.human(op="shared-materials", target=slots)["ids"], [self.s["hero"]])
        token = self.ctoken(slots)
        for material, slot, code, status in ((self.s["hero"], "1", "LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST),
                                             (self.s["checker"], "0", "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST),
                                             (self.s["hero_sprite"], "0", "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST)):
            with self.subTest(f"{material} {slot}"):
                r = self.run_cap(au.SET_RENDERER_MATERIAL, renderer=slots, material=material, slot=slot,
                                 expected_component_token=token)
                ok(self, r, code, status)
                self.assertFalse(r.mutation_performed)
        self.assertEqual(self.human(op="shared-materials", target=slots)["ids"], [self.s["hero"]])   # never appended
        ok(self, self.run_cap(au.SET_RENDERER_MATERIAL, renderer=slots, material=self.s["other"], slot="0",
                              expected_component_token=T), "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        data = ok(self, self.run_cap(au.SET_RENDERER_MATERIAL, renderer=slots, material=self.s["builtin_material"],
                                     slot="0", expected_component_token=token))
        self.assertEqual(data["material"], self.s["builtin_material"])
        data = ok(self, self.run_cap(au.SET_RENDERER_MATERIAL, renderer=slots, material="", slot="0",
                                     expected_component_token=self.ctoken(slots)))
        self.assertEqual((data["material"], data["slot_count"]), (None, 1))
        self.assertEqual(self.human(op="shared-materials", target=none)["ids"], [])
        ok(self, self.run_cap(au.SET_RENDERER_MATERIAL, renderer=none, material=self.s["hero"], slot="1",
                              expected_component_token=self.ctoken(none)), "LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST)
        data = ok(self, self.run_cap(au.SET_RENDERER_MATERIAL, renderer=none, material=self.s["hero"], slot="0",
                                     expected_component_token=self.ctoken(none)))
        self.assertEqual((data["operation"], data["slot_count"]), ("CREATE_FIRST", 1))
        self.assertEqual(self.human(op="material-instances")["count"], count)
        r = self.run_cap(au.SET_RENDERER_MATERIAL, renderer=instance, material=self.s["hero"], slot="0",
                         expected_component_token=T)
        ok(self, r, "LIVE_PREFAB_BOUNDARY", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        ok(self, self.run_cap(au.SET_RENDERER_MATERIAL, renderer=self.s["refs"], material=self.s["hero"], slot="0",
                              expected_component_token=T), "LIVE_OBJECT_REFUSED", tdg.INVALID_REQUEST)

    def test_10_undo_dirty_after_undo_and_human_edits(self):
        config, path = self.s["new_config"], self.file("Assets/Data/Config.asset")
        ok(self, self.set_asset(config, "value", "int32", 7), "LIVE_ASSET_SAVED")
        saved = sha(path)
        self.human(op="undo")                                                  # Cmd-Z after the save
        self.assertEqual(self.human(op="read", target=config, path="value")["value"], "5")
        self.assertEqual(self.human(op="dirty", target=config)["dirty"], True)     # memory reverted, marked dirty ...
        self.assertEqual(sha(path), saved)                                          # ... the file keeps the saved value
        r = self.set_asset(config, "value", "int32", 8)
        ok(self, r, "LIVE_ASSET_DIRTY", tdg.CONFLICT)                              # GPOS never saves it for the Human
        self.assertFalse(r.mutation_performed)
        self.assertEqual(sha(path), saved)
        self.human(op="save-asset", target=config)                              # the Human saves the reverted value
        self.assertIn("value: 5", path.read_text())
        # another asset the Human left unsaved is never saved by a GPOS save (only that one asset is saved)
        plain, ppath = self.s["plain"], self.file("Assets/Data/Plain.asset")
        before = sha(ppath)
        self.human(op="set-serialized", target=plain, path="value", kind="int", value="77")
        ok(self, self.set_asset(config, "speed", "float32", 2), "LIVE_ASSET_SAVED")
        self.assertEqual((sha(ppath), self.human(op="dirty", target=plain)["dirty"]), (before, True))
        self.human(op="save-asset", target=plain)
        # an Inspector edit that is not saved: dirty, refused, and never saved by GPOS
        token = self.token(config)
        self.human(op="set-serialized", target=config, path="value", kind="int", value="9")
        r = self.set_asset(config, "value", "int32", 10, token=token)
        ok(self, r, "LIVE_ASSET_DIRTY", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        self.assertIn("value: 5", path.read_text())
        self.human(op="save-asset", target=config)                              # the Human saves their own edit
        r = self.set_asset(config, "value", "int32", 10, token=token)           # the stale token now conflicts
        ok(self, r, "LIVE_ASSET_CONFLICT", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)                                  # after the targeted import
        self.assertIn("value: 9", path.read_text())

    def test_11_external_disk_changes(self):
        config, path = self.s["new_config"], self.file("Assets/Data/Config.asset")
        # an external program changes the file before the inspection; nothing imports it until the mutation does
        text = path.read_text()
        path.write_text(text.replace("value: 9", "value: 42"))
        stale = self.token(config)
        self.assertEqual(self.human(op="read", target=config, path="value")["value"], "9")   # inspection never imports
        r = self.set_asset(config, "speed", "float32", 3, token=stale)
        ok(self, r, "LIVE_ASSET_CONFLICT", tdg.CONFLICT)                                       # validation 9
        self.assertTrue(r.mutation_performed)
        self.assertEqual((r.data["import_performed"], r.data["value_persisted"]), (True, False))
        self.assertEqual(self.human(op="read", target=config, path="value")["value"], "42")   # the import synced memory
        self.assertIn("value: 42", path.read_text())
        ok(self, self.set_asset(config, "speed", "float32", 3), "LIVE_ASSET_SAVED")            # decided again: fine
        # the file changes between the import and the save: the edit is reverted and nothing is saved
        external = path.read_text().replace("value: 42", "value: 43")
        self.human(op="arm", step="pre-save", action="write", path="Assets/Data/Config.asset", text=external)
        r = self.set_asset(config, "speed", "float32", 4)
        ok(self, r, "LIVE_ASSET_CONFLICT", tdg.CONFLICT)                                       # validation 10
        self.assertTrue(r.mutation_performed)
        self.assertEqual((r.data["reverted"], r.data["restored"], r.data["value_persisted"]), (True, True, False))
        self.assertEqual(path.read_text(), external)                                          # never overwritten
        self.human(op="import", path="Assets/Data/Config.asset")                               # the Human's Editor catches up
        # a .meta change (a label, an importer setting) changes the token too
        meta = self.file("Assets/Data/Config.asset.meta")
        token = self.token(config)
        meta.write_text(meta.read_text().replace("userData: \n", "userData: gpos-test\n"))
        r = self.set_asset(config, "speed", "float32", 5, token=token)
        ok(self, r, "LIVE_ASSET_CONFLICT", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)
        ok(self, self.set_asset(config, "speed", "float32", 5), "LIVE_ASSET_SAVED")
        self.assertIn("userData: gpos-test", meta.read_text())

    def test_12_reload_recompile_and_catalog_digests(self):
        hero, config = self.s["hero"], self.s["new_config"]
        before = (self.token(hero), self.token(config))
        so, _ = self.catalogs()
        generation = self.lab.editor.heartbeat()["generation"]
        self.lab.editor.trigger("reload")
        self.assertTrue(self.lab.editor.wait(lambda: self.lab.editor.heartbeat().get("generation", 0) > generation, 120))
        self.wait_edit()
        self.assertEqual((self.token(hero), self.token(config)), before)                # ids and tokens survive
        self.assertEqual(self.catalogs()[0]["so_catalog_digest"], so["so_catalog_digest"])
        script = self.file("Assets/AssetScripts/Changeable.cs")
        script.write_text("using UnityEngine;\n[CreateAssetMenu] public class Changeable : ScriptableObject { public int x; }\n")
        generation = self.lab.editor.heartbeat()["generation"]
        self.lab.editor.trigger("refresh")
        self.assertTrue(self.lab.editor.wait(lambda: self.lab.editor.heartbeat().get("generation", 0) > generation, 180))
        self.wait_edit()
        now, _ = self.catalogs()
        self.assertNotEqual(now["so_catalog_digest"], so["so_catalog_digest"])
        self.assertTrue(next(t for t in now["types"] if t["name"] == "Changeable")["creatable"])
        r = self.run_cap(A.CREATE_SCRIPTABLE_OBJECT, path="Assets/Data/Ch.asset", type_id="Assembly-CSharp::Changeable",
                         expected_so_catalog_digest=so["so_catalog_digest"])
        ok(self, r, "LIVE_CATALOG_CHANGED", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        ok(self, self.run_cap(A.CREATE_SCRIPTABLE_OBJECT, path="Assets/Data/Ch.asset", type_id="Assembly-CSharp::Changeable",
                              expected_so_catalog_digest=now["so_catalog_digest"]), "LIVE_ASSET_CREATED")

    def test_13_play_mode_refuses_asset_commands(self):
        ok(self, self.lab.run(live.ENTER))
        r = self.run_cap(A.CREATE_MATERIAL, path="Assets/Materials/Playing.mat", shader=self.s["shader"],
                         expected_shader_catalog_digest=DIGEST)
        ok(self, r, "EDITOR_BUSY", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        ok(self, self.run_cap(A.ASSET_FIND, kind="MATERIAL", source="ASSETS"), "EDITOR_BUSY", tdg.CONFLICT)
        ok(self, self.lab.run(live.EXIT))
        self.assertFalse(self.file("Assets/Materials/Playing.mat").exists())

    def test_14_detach_leaves_no_gpos_state_in_the_project(self):
        ok(self, self.lab.run(live.DETACH), "LIVE_SESSION_DETACHED")
        self.lab.editor.stop()
        self.assertEqual((self.leftovers(), self.records()), ([], []))
        self.assertEqual(bi.inspect_target(self.lab.game, bi.verify_source()), (bi.EXACT, []))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R4_RealCreationRecovery(RealAssets):
    """Correction 1: the lab Editor process is stopped at exact creation steps (a real crash); after a restart the
    next creation recovers every earlier transaction from what is on disk, or refuses and touches nothing."""

    @classmethod
    def setUpClass(cls):
        start(cls, "recovery")

    @classmethod
    def tearDownClass(cls):
        cls.lab.editor.stop()

    def crash(self, step, path):
        self.human(op="arm", step=step, action="crash")
        r = self.create_material(path, timeout=20)
        ok(self, r, "LIVE_OUTCOME_UNKNOWN", tdg.OUTCOME_UNKNOWN)          # the request was claimed, never answered
        self.assertTrue(r.mutation_performed)
        self.lab.editor.proc.wait(120)
        self.assertEqual(len(self.records()), 1)
        self.lab.editor.launch()
        ok(self, self.lab.run(live.DETACH, timeout=120), "LIVE_SESSION_RECOVERED")
        self.lab.sid = ok(self, self.lab.run(live.ATTACH, timeout=120), "LIVE_SESSION_ATTACHED")["session_id"]
        return json.loads(self.records()[0].read_text())

    def test_01_a_crash_before_create_asset_leaves_nothing(self):
        record = self.crash("prepared", "Assets/Materials/C1.mat")                   # validation 1
        self.assertEqual((record["phase"], record["final_path"], record["guid"]), ("PREPARED", "Assets/Materials/C1.mat", None))
        self.assertEqual((record["session_id"] is not None, record["owner"]), (True, "AGENT:testkit-approve-1"))
        self.assertEqual(self.scratch(), [])
        r = self.create_material("Assets/Materials/Next1.mat")
        ok(self, r, "LIVE_ASSET_CREATE_RECOVERED")
        self.assertEqual([x["outcome"] for x in r.data["recovered"]], ["NOTHING_CREATED"])
        self.assertFalse(self.file("Assets/Materials/C1.mat").exists())
        self.assertEqual((self.leftovers(), self.records()), ([], []))

    def test_02_an_unproven_temporary_asset_is_never_removed(self):
        record = self.crash("temp-created", "Assets/Materials/C2.mat")               # after CreateAsset, before proof
        self.assertEqual(record["phase"], "SCRATCH_READY")
        (scratch,) = self.scratch()
        files = {p.name: p.read_bytes() for p in self.file(f"Assets/{scratch}").iterdir()}
        self.assertEqual(sorted(files), ["C2.mat", "C2.mat.meta"])
        r = self.create_material("Assets/Materials/Next2.mat")
        ok(self, r, "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        self.assertEqual(r.data["state"]["temp"], "DIFFERENT")
        self.assertEqual({p.name: p.read_bytes() for p in self.file(f"Assets/{scratch}").iterdir()}, files)  # untouched
        self.assertFalse(self.file("Assets/Materials/Next2.mat").exists())
        # the Human resolves it outside GPOS: removes the scratch folder and the record
        shutil.rmtree(self.file(f"Assets/{scratch}"))
        self.file(f"Assets/{scratch}.meta").unlink()
        self.records()[0].unlink()
        generation = self.lab.editor.heartbeat().get("generation", 0)
        self.lab.editor.trigger("refresh")                                            # the Human's Editor catches up
        time.sleep(3)
        self.wait_edit()
        self.assertEqual(self.lab.editor.heartbeat().get("generation", 0), generation)
        ok(self, self.create_material("Assets/Materials/Next2.mat"), "LIVE_ASSET_CREATED")

    def test_03_an_exact_temporary_asset_is_removed_a_modified_one_is_not(self):
        record = self.crash("temp-proven", "Assets/Materials/C3.mat")                # validation 2
        self.assertEqual(record["phase"], "TEMP_PROVEN")
        (scratch,) = self.scratch()
        temp = self.file(f"Assets/{scratch}/C3.mat")
        self.assertEqual((sha(temp), record["temp_sha256"]), (record["temp_sha256"], sha(temp)))
        original = temp.read_bytes()
        temp.write_bytes(original + b"# changed after the crash\n")                    # validation 6: modified temp
        r = self.create_material("Assets/Materials/Next3.mat")
        ok(self, r, "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertEqual(r.data["state"]["temp"], "DIFFERENT")
        self.assertEqual(temp.read_bytes(), original + b"# changed after the crash\n")  # never removed
        temp.write_bytes(original)
        r = self.create_material("Assets/Materials/Next3.mat")                        # validation 4: exact temp
        ok(self, r, "LIVE_ASSET_CREATE_RECOVERED")
        self.assertEqual([x["outcome"] for x in r.data["recovered"]], ["TEMP_REMOVED"])
        self.assertTrue(r.mutation_performed)
        self.assertFalse(self.file("Assets/Materials/C3.mat").exists())               # no creation is replayed
        self.assertEqual((self.leftovers(), self.records()), ([], []))

    def test_04_an_exact_final_asset_is_kept_a_modified_one_is_untouched(self):
        record = self.crash("moved", "Assets/Materials/C4.mat")                      # validation 3: after the move
        self.assertEqual(record["phase"], "TEMP_PROVEN")
        final = self.file("Assets/Materials/C4.mat")
        self.assertEqual(sha(final), record["temp_sha256"])                          # the move changed no byte
        self.assertIn(f"guid: {record['guid']}", self.file("Assets/Materials/C4.mat.meta").read_text())
        original = final.read_bytes()
        final.write_bytes(original + b"# changed after the crash\n")                   # validation 7: modified final
        r = self.create_material("Assets/Materials/Next4.mat")
        ok(self, r, "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertEqual(r.data["state"]["final"], "OURS_DIFFERENT")
        self.assertEqual(final.read_bytes(), original + b"# changed after the crash\n")
        final.write_bytes(original)
        r = self.create_material("Assets/Materials/Next4.mat")                        # validation 5: exact final
        ok(self, r, "LIVE_ASSET_CREATE_RECOVERED")
        self.assertEqual([x["outcome"] for x in r.data["recovered"]], ["FINAL_KEPT"])
        self.assertEqual(final.read_bytes(), original)                                # the final is never deleted
        self.assertEqual(self.lab.human(op="id-of", path="Assets/Materials/C4.mat")["id"], record["global_id"])
        self.assertEqual((self.leftovers(), self.records()), ([], []))

    def test_05_untrusted_records_are_never_acted_on(self):
        from gpos.tools.unity import identity as ident
        key = ident.project_key(ident.relative(self.lab.p, self.lab.game))
        d = self.lab.p / ".game" / "gpos-runtime" / "unity" / "asset-create-txn" / key
        d.mkdir(parents=True, exist_ok=True)
        victim = self.file("Assets/Materials/Next4.mat")
        before = sha(victim)
        txn = "e" * 32
        good = {"schema": "gpos.unity.live-bridge.asset-create-txn/1", "txn_id": txn, "project_key": key,
                "session_id": "1" * 32, "request_id": "2" * 32, "owner": "AGENT:x", "kind": "MATERIAL",
                "final_path": "Assets/Materials/Next4.mat", "phase": "PREPARED", "scratch_meta_sha256": None,
                "guid": None, "global_id": None, "type": None, "temp_sha256": None, "temp_meta_sha256": None,
                "final_sha256": None, "final_meta_sha256": None, "started_utc": "2026-09-27T00:00:00.0000000Z"}
        bad = ('{"schema": 1, "schema": 2}', "[]", json.dumps(dict(good, final_path="/etc/passwd")),
               json.dumps(dict(good, final_path="Assets/../x.mat")), json.dumps(dict(good, txn_id="f" * 32)),
               json.dumps(dict(good, project_key="0" * 16)), json.dumps(dict(good, phase="DONE")),
               json.dumps(dict(good, guid="a" * 32)), json.dumps(dict(good, extra=1)),
               json.dumps(dict(good, final_path="Assets/Materials/Next4.mat\n")))
        for text in bad:
            with self.subTest(text[:50]):
                (d / f"{txn}.json").write_text(text)
                r = self.create_material("Assets/Materials/Next5.mat")
                ok(self, r, "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
                self.assertFalse(r.mutation_performed)
                self.assertEqual(sha(victim), before)
        (d / f"{txn}.json").unlink()
        (d / "notes.txt").write_text("x")
        ok(self, self.create_material("Assets/Materials/Next5.mat"), "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)
        (d / "notes.txt").unlink()
        ok(self, self.create_material("Assets/Materials/Next5.mat"), "LIVE_ASSET_CREATED")
        self.assertEqual(sha(victim), before)

    def test_06_detach(self):
        ok(self, self.lab.run(live.DETACH), "LIVE_SESSION_DETACHED")
        self.lab.editor.stop()
        self.assertEqual((self.leftovers(), self.records()), ([], []))


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
