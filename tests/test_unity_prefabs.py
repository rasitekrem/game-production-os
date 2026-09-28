#!/usr/bin/env python3
"""Phase 2C-6B2B — Unity live prefab authoring (alpha.19).

    python3 tests/test_unity_prefabs.py

Fast groups need no Unity. A–C drive the GPOS side against `unity_live_fake_bridge.FakeBridge` (a stand-in that
speaks the file protocol; it proves GPOS behaviour only): declarations, the input grammar (type-1 prefab ids, Scene
ids, .prefab paths, prefab property values), the exact arguments GPOS sends and how every bridge answer maps to a
result — mutation_performed once a targeted import happened, OUTCOME_UNKNOWN from the commit point on, creation
recovery by record kind. D pins the frozen bridge 1.2.0 release in the history and upgrades it. E checks the bridge's
prefab sources for forbidden mechanisms, the absence of every deferred prefab operation and the order of the guards,
the creation transaction and the edit lifecycle, and that the alpha.18 asset surface still authors only Materials and
ScriptableObjects. The Unity-free prefab rules are covered by tests/test_unity_live_bridge_core.py.

Real groups open disposable synthetic Unity projects (unity_fixture_builder.make_prefab_project) in lab-owned
batch-mode Editors activated by the test-only testkit, which also stands in for the Human (Prefab Mode, Inspector edits,
Ctrl+S, Cmd-Z, a Scene made dirty, a moved asset) and for other programs (a file written at an exact step through the
bridge's AssetAuthoring.AfterStep test seam) — never the Human's Editor, never a user project. R5 is one prefab session
end to end; R6 exercises every guard (Prefab Mode, dirty prefab, version control, OS permissions, file races, dependent
Scenes, the scan bound, project callbacks, Domain Reload / reimport / move); R7 stops the lab Editor process at exact
steps (a real crash) and proves recovery after a restart; R8 upgrades a running 1.2.0 bridge. The real groups stop
with UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A unless exactly one Hub Editor is installed, and guard Unity's
EditorPrefs and the user's Package Manager configuration files.

GPOS_UNITY_TEST_FAST=1 (the mutation harness only) skips every group that starts a real Unity process.
"""

import hashlib
import json
import os
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
from gpos.tools.unity import prefabs as P  # noqa: E402
from test_unity_live import EDITOR, EDITOR_VERSION, EDITORS, FAST, FIXTURE, LiveCase, published  # noqa: E402

T = "0" * 31 + "1"
DIGEST = "a" * 64
G = "5" * 32
PID = f"GlobalObjectId_V1-1-{G}-100-0"
PCOMP = f"GlobalObjectId_V1-1-{G}-200-0"
SCENE_ID = "GlobalObjectId_V1-2-0123456789abcdef0123456789abcdef-5-0"
MAT = "GlobalObjectId_V1-3-" + "b" * 32 + "-2100000-0"
SCENE = "Assets/Scenes/Main.unity"
EDITOR_SOURCE = bi.SOURCE / "Editor"


# ---------------------------------------------------------------- A  declarations

class A_Declarations(unittest.TestCase):
    def test_nine_fixed_prefab_capabilities(self):
        caps = {c.id: c for c in UnityAdapter.descriptor.capabilities}
        self.assertEqual(len(caps), 41)
        self.assertEqual(len(P.CAPABILITY_IDS), 9)
        self.assertEqual(set(P.CAPABILITY_IDS), {
            "unity.live-prefab-inspect", "unity.live-prefab-instance-inspect", "unity.live-create-prefab",
            "unity.live-instantiate-prefab", "unity.live-set-prefab-gameobject", "unity.live-set-prefab-transform",
            "unity.live-add-prefab-component", "unity.live-remove-prefab-component", "unity.live-set-prefab-property"})
        self.assertFalse(set(P.CAPABILITY_IDS) & (set(au.CAPABILITY_IDS) | set(A.CAPABILITY_IDS)))
        for cid in P.CAPABILITY_IDS:
            c = caps[cid]
            read_only = cid in P.READ_ONLY
            with self.subTest(cid):
                self.assertEqual((c.category, c.operation_class, c.state_model, c.effective_lease_mode,
                                  c.execution_context),
                                 ("INSPECT" if read_only else "TRANSFORM", "READ_ONLY" if read_only else "MUTATING",
                                  "STATEFUL", "SESSION_REQUIRED", "EDITOR"))
                self.assertEqual(c.input_kinds, P.input_kinds(cid))
                self.assertEqual((c.potential_evidence, c.artifact_kinds, c.requires_tool), ((), (), False))
                self.assertEqual(c.side_effect_scope == "NONE", read_only)
        self.assertIn("not undoable", caps[P.CREATE_PREFAB].side_effect_scope)
        self.assertIn("scenes_marked_dirty", caps[P.SET_PROPERTY].side_effect_scope)
        self.assertIn("never saves or cleans", caps[P.SET_PROPERTY].side_effect_scope)
        self.assertNotIn("are marked dirty", caps[P.SET_PROPERTY].side_effect_scope)     # measured: Unity did not
        self.assertNotIn("which Unity marks dirty", P.LIMITATION)

    def test_no_deferred_or_generic_prefab_surface(self):
        names = " ".join(c.id for c in UnityAdapter.descriptor.capabilities)
        for word in ("apply", "revert", "unpack", "variant", "connect", "stage", "prefab-mode", "nested",
                     "create-prefab-child", "delete-prefab", "model", "prefab-utility", "execute", "menu"):
            with self.subTest(word):
                self.assertNotIn(word, names)
        self.assertEqual(set(P.COMMANDS.values()), {
            "prefab-inspect", "prefab-instance-inspect", "create-prefab", "instantiate-prefab", "set-prefab-gameobject",
            "set-prefab-transform", "add-prefab-component", "remove-prefab-component", "set-prefab-property"})

    def test_the_diagnostic_codes(self):
        expect = {"LIVE_PREFAB_REFUSED": tdg.INVALID_REQUEST, "LIVE_PREFAB_LIMIT": tdg.INVALID_REQUEST,
                  "LIVE_PREFAB_STAGE_OPEN": tdg.CONFLICT, "LIVE_PREFAB_DIRTY": tdg.CONFLICT,
                  "LIVE_PREFAB_CONFLICT": tdg.CONFLICT, "LIVE_PREFAB_NOT_EDITABLE": tdg.CONFLICT,
                  "LIVE_PREFAB_CREATE_INCOMPLETE": tdg.CONFLICT, "LIVE_PREFAB_CREATED": tdg.INFO,
                  "LIVE_PREFAB_SAVED": tdg.INFO, "LIVE_PREFAB_INSTANTIATED": tdg.INFO,
                  "LIVE_PREFAB_CREATE_RECOVERED": tdg.INFO, "LIVE_PREFAB_SIDE_EFFECTS": tdg.INFO}
        for code, cls in expect.items():
            self.assertEqual(tdg.CODES[code][0], cls, code)
        for bridge, code in (("PREFAB_REFUSED", "LIVE_PREFAB_REFUSED"), ("PREFAB_LIMIT", "LIVE_PREFAB_LIMIT"),
                             ("PREFAB_STAGE_OPEN", "LIVE_PREFAB_STAGE_OPEN"), ("PREFAB_DIRTY", "LIVE_PREFAB_DIRTY"),
                             ("PREFAB_CONFLICT", "LIVE_PREFAB_CONFLICT"), ("PREFAB_NOT_EDITABLE", "LIVE_PREFAB_NOT_EDITABLE"),
                             ("PREFAB_CREATE_INCOMPLETE", "LIVE_PREFAB_CREATE_INCOMPLETE")):
            self.assertEqual(live.REFUSALS[bridge], code)


# ---------------------------------------------------------------- B  the input grammar

class B_Inputs(unittest.TestCase):
    def refused(self, cap, inputs, code):
        with self.assertRaises(P.InputProblem) as ctx:
            P.parse_inputs(cap, inputs)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))

    def test_prefab_ids_are_type_1_with_prefab_id_0(self):
        self.assertEqual(P.parse_inputs(P.PREFAB_INSPECT, {"prefab": PID})["prefab"], PID)
        for bad in (SCENE_ID, MAT, f"GlobalObjectId_V1-1-{G}-100-7", f"GlobalObjectId_V1-1-{G}-100-0\n",
                    f"GlobalObjectId_V1-2-{G}-100-0", "GlobalObjectId_V1-1-" + "0" * 32 + "-100-0",
                    "GlobalObjectId_V1-1-0000000000000000e000000000000000-100-0",
                    f"GlobalObjectId_V1-1-{G}-{2 ** 64}-0", "Assets/Prefabs/Plain.prefab", PID.upper()):
            with self.subTest(bad):
                self.refused(P.PREFAB_INSPECT, {"prefab": bad}, "LIVE_OBJECT_REFUSED")
                self.refused(P.SET_GAMEOBJECT, {"object": bad, "name": "x", "expected_prefab_token": T},
                             "LIVE_OBJECT_REFUSED")
        self.refused(P.INSTANCE_INSPECT, {"object": PID}, "LIVE_OBJECT_REFUSED")        # instances are Scene objects
        self.refused(P.CREATE_PREFAB, {"source": PID, "path": "Assets/A.prefab", "expected_subtree_token": T},
                     "LIVE_OBJECT_REFUSED")

    def test_new_prefab_paths(self):
        ok = {"source": SCENE_ID, "expected_subtree_token": T}
        self.assertEqual(P.parse_inputs(P.CREATE_PREFAB, dict(ok, path="Assets/Made/Box.prefab"))["path"],
                         "Assets/Made/Box.prefab")
        for bad in ("Packages/com.x/Box.prefab", "Library/Box.prefab", "/tmp/Box.prefab", "Assets/Box.prefab",
                    "Assets/Made/Box.asset", "Assets/Made/Box.Prefab", "Assets/../Made/Box.prefab",
                    "Assets/Editor/Box.prefab", "Assets/GposAssetTxn-" + "a" * 32 + "/Box.prefab",
                    "Assets/Made/Box.prefab\n", "Assets/.hidden/Box.prefab", "Assets/Made/ Box.prefab"):
            with self.subTest(bad):
                self.refused(P.CREATE_PREFAB, dict(ok, path=bad), "LIVE_ASSET_PATH_INVALID")

    def test_references_never_name_scene_objects(self):
        base = {"component": PCOMP, "path": "other", "kind": "object", "expected_prefab_token": T}
        self.assertEqual(P.parse_inputs(P.SET_PROPERTY, dict(base, value=json.dumps(PID)))["value"], PID)
        self.assertIsNone(P.parse_inputs(P.SET_PROPERTY, dict(base, value="null"))["value"])
        self.assertEqual(P.parse_inputs(P.SET_PROPERTY, dict(base, value=json.dumps(MAT)))["value"], MAT)
        self.refused(P.SET_PROPERTY, dict(base, value=json.dumps(SCENE_ID)), "LIVE_VALUE_INVALID")
        self.refused(P.SET_PROPERTY, dict(base, value="5"), "LIVE_VALUE_INVALID")
        self.refused(P.SET_PROPERTY, dict(base, kind="int32", value="1.5"), "LIVE_VALUE_INVALID")
        self.refused(P.SET_PROPERTY, dict(base, path="arr.Array.data[0]", value="null"), "LIVE_PROPERTY_UNSUPPORTED")

    def test_instantiate_takes_exactly_one_place_token(self):
        base = {"prefab": PID, "scene": SCENE, "expected_prefab_token": T}
        args = P.parse_inputs(P.INSTANTIATE, dict(base, expected_scene_roots_token=T))
        self.assertEqual(set(args), set(P.INPUTS[P.INSTANTIATE]))
        P.parse_inputs(P.INSTANTIATE, dict(base, parent=SCENE_ID, expected_parent_token=T))
        self.refused(P.INSTANTIATE, base, "INVALID_TOOL_REQUEST")
        self.refused(P.INSTANTIATE, dict(base, parent=SCENE_ID, expected_scene_roots_token=T), "INVALID_TOOL_REQUEST")
        self.refused(P.INSTANTIATE, dict(base, parent=SCENE_ID, expected_parent_token=T, expected_scene_roots_token=T),
                     "INVALID_TOOL_REQUEST")
        self.refused(P.INSTANTIATE, dict(base, expected_parent_token=T), "INVALID_TOOL_REQUEST")
        self.refused(P.INSTANTIATE, dict(base, expected_scene_roots_token=T, local_rotation="[0, 0, 0, 2]"),
                     "LIVE_VALUE_INVALID")
        self.refused(P.INSTANTIATE, {"prefab": PID, "scene": SCENE, "expected_scene_roots_token": T},
                     "INVALID_TOOL_REQUEST")                                                  # the prefab token

    def test_edits_need_a_field_and_the_prefab_token(self):
        self.refused(P.SET_GAMEOBJECT, {"object": PID, "expected_prefab_token": T}, "INVALID_TOOL_REQUEST")
        self.refused(P.SET_TRANSFORM, {"object": PID, "expected_prefab_token": T}, "INVALID_TOOL_REQUEST")
        self.refused(P.SET_GAMEOBJECT, {"object": PID, "name": "x"}, "INVALID_TOOL_REQUEST")
        self.refused(P.REMOVE_COMPONENT, {"component": PCOMP}, "INVALID_TOOL_REQUEST")
        self.refused(P.ADD_COMPONENT, {"object": PID, "type_id": "x", "expected_prefab_token": T,
                                       "expected_catalog_digest": DIGEST}, "LIVE_TYPE_NOT_IN_CATALOG")
        self.refused(P.SET_GAMEOBJECT, {"object": PID, "name": "x", "expected_prefab_token": T, "method": "y"},
                     "INVALID_TOOL_REQUEST")
        self.refused(P.PREFAB_INSPECT, {"prefab": PID, "path_prefix": "m_"}, "INVALID_TOOL_REQUEST")


# ---------------------------------------------------------------- C  mapping (fake bridge)

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
        P.PREFAB_INSPECT: {"prefab": PID, "component": PCOMP, "path_prefix": "co", "page": "1"},
        P.INSTANCE_INSPECT: {"object": SCENE_ID, "page": "2"},
        P.CREATE_PREFAB: {"source": SCENE_ID, "path": "Assets/Made/Box.prefab", "expected_subtree_token": T},
        P.INSTANTIATE: {"prefab": PID, "scene": SCENE, "parent": SCENE_ID, "sibling": "0",
                        "local_position": "[1, 2, 3]", "local_rotation": "[0, 0, 0, 1]", "local_scale": "[1, 1, 1]",
                        "expected_prefab_token": T, "expected_parent_token": T},
        P.SET_GAMEOBJECT: {"object": PID, "name": "N", "active": "false", "tag": "Untagged", "layer": "3",
                           "static_flags": "0", "expected_prefab_token": T},
        P.SET_TRANSFORM: {"object": PID, "local_position": "[0, 1, 0]", "expected_prefab_token": T},
        P.ADD_COMPONENT: {"object": PID, "type_id": "Assembly-CSharp::AuthorSingle", "expected_prefab_token": T,
                          "expected_catalog_digest": DIGEST},
        P.REMOVE_COMPONENT: {"component": PCOMP, "expected_prefab_token": T},
        P.SET_PROPERTY: {"component": PCOMP, "path": "other", "kind": "object", "value": json.dumps(PID),
                         "expected_prefab_token": T},
    }

    def test_every_capability_sends_exactly_its_arguments(self):
        for cap, inputs in self.CALLS.items():
            with self.subTest(cap):
                r = self.author(cap, sid=self.sid, **inputs)
                self.assertStatus(r, tdg.SUCCESS)
                command, args = self.b.author_calls[-1]
                self.assertEqual(command, P.COMMANDS[cap])
                self.assertEqual(set(args), set(P.INPUTS[cap]))
                self.assertEqual(args, P.parse_inputs(cap, inputs))
                self.assertEqual(r.mutation_performed, cap not in P.READ_ONLY)
                self.assertEqual(r.evidence_candidates, ())
                self.assertEqual("limitation" in r.data, cap not in P.READ_ONLY)
                expected = {P.CREATE_PREFAB: {"LIVE_PREFAB_CREATED"}, P.INSTANTIATE: {"LIVE_PREFAB_INSTANTIATED"}}
                self.assertEqual(self.codes(r), expected.get(cap, set() if cap in P.READ_ONLY else {"LIVE_PREFAB_SAVED"}))
        req = sorted((self.b.live / "claimed").glob("*.json"), key=lambda f: f.stat().st_mtime)[-1]
        self.assertEqual(json.loads(req.read_text())["schema"], "gpos.unity.live.request/4")

    def test_bad_inputs_are_refused_before_anything_is_sent(self):
        before = len(self.b.claimed_ids)
        r = self.author(P.CREATE_PREFAB, sid=self.sid, source=SCENE_ID, path="Packages/com.x/B.prefab",
                        expected_subtree_token=T)
        self.assertStatus(r, tdg.INVALID_REQUEST, "LIVE_ASSET_PATH_INVALID")
        r = self.author(P.SET_PROPERTY, sid=self.sid, component=PCOMP, path="other", kind="object",
                        value=json.dumps(SCENE_ID), expected_prefab_token=T)
        self.assertStatus(r, tdg.INVALID_REQUEST, "LIVE_VALUE_INVALID")
        r = self.author(P.SET_GAMEOBJECT, sid=self.sid, object=SCENE_ID, name="x", expected_prefab_token=T)
        self.assertStatus(r, tdg.INVALID_REQUEST, "LIVE_OBJECT_REFUSED")
        self.assertFalse(r.mutation_performed)
        self.assertEqual(len(self.b.claimed_ids), before)

    def test_refusals_map_and_report_whether_a_mutation_began(self):
        table = {"PREFAB_REFUSED": ("LIVE_PREFAB_REFUSED", tdg.INVALID_REQUEST),
                 "PREFAB_LIMIT": ("LIVE_PREFAB_LIMIT", tdg.INVALID_REQUEST),
                 "PREFAB_STAGE_OPEN": ("LIVE_PREFAB_STAGE_OPEN", tdg.CONFLICT),
                 "PREFAB_DIRTY": ("LIVE_PREFAB_DIRTY", tdg.CONFLICT),
                 "PREFAB_CONFLICT": ("LIVE_PREFAB_CONFLICT", tdg.CONFLICT),
                 "PREFAB_NOT_EDITABLE": ("LIVE_PREFAB_NOT_EDITABLE", tdg.CONFLICT),
                 "PREFAB_CREATE_INCOMPLETE": ("LIVE_PREFAB_CREATE_INCOMPLETE", tdg.CONFLICT),
                 "PREFAB_BOUNDARY": ("LIVE_PREFAB_BOUNDARY", tdg.CONFLICT),
                 "ASSET_EXISTS": ("LIVE_ASSET_EXISTS", tdg.CONFLICT),
                 "AUTHORING_CONFLICT": ("LIVE_AUTHORING_CONFLICT", tdg.CONFLICT),
                 "CATALOG_CHANGED": ("LIVE_CATALOG_CHANGED", tdg.CONFLICT),
                 "AUTHORING_REFUSED": ("LIVE_AUTHORING_REFUSED", tdg.CONFLICT),
                 "PROPERTY_UNSUPPORTED": ("LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST),
                 "VALUE_INVALID": ("LIVE_VALUE_INVALID", tdg.INVALID_REQUEST),
                 "VALUE_NOT_APPLIED": ("LIVE_VALUE_INVALID", tdg.INVALID_REQUEST),
                 "ASSET_REFUSED": ("LIVE_ASSET_REFUSED", tdg.INVALID_REQUEST),
                 "OBJECT_NOT_FOUND": ("LIVE_OBJECT_NOT_FOUND", tdg.CONFLICT)}
        for cap in (P.SET_PROPERTY, P.INSTANTIATE):
            for bridge_code, (code, status) in table.items():
                for started in (False, True):
                    with self.subTest(f"{cap} {bridge_code} started={started}"):
                        data = {"mutation_started": True, "import_performed": True, "value_persisted": False} \
                            if started else {"mutation_started": False}
                        self.b.author_reply = lambda c, a, bc=bridge_code, d=data: ("REFUSED", bc, d)
                        r = self.author(cap, sid=self.sid, **self.CALLS[cap])
                        self.assertStatus(r, status, code)
                        self.assertEqual(r.mutation_performed, started)
        self.b.author_reply = lambda c, a: ("REFUSED", "PREFAB_CONFLICT", {"mutation_started": True})
        for cap in P.READ_ONLY:
            self.assertFalse(self.author(cap, sid=self.sid, **self.CALLS[cap]).mutation_performed)

    def test_a_targeted_import_is_reported_as_a_mutation(self):
        """Corrections 2 and 3: once the bridge imported the prefab, a refusal (even one that created no instance)
        reports mutation_performed, and the data says nothing was persisted or instantiated."""
        data = {"mutation_started": True, "import_performed": True, "value_persisted": False, "scene_changed": False,
                "scenes_marked_dirty": ["Assets/Scenes/Other.unity"]}
        self.b.author_reply = lambda c, a: ("REFUSED", "PREFAB_CONFLICT", data)
        r = self.author(P.INSTANTIATE, sid=self.sid, **self.CALLS[P.INSTANTIATE])
        self.assertStatus(r, tdg.CONFLICT, "LIVE_PREFAB_CONFLICT")
        self.assertTrue(r.mutation_performed)
        self.assertEqual((r.data["import_performed"], r.data["value_persisted"], r.data["scene_changed"],
                          r.data["scenes_marked_dirty"]), (True, False, False, ["Assets/Scenes/Other.unity"]))
        self.b.author_reply = lambda c, a: ("REFUSED", "PREFAB_DIRTY", {"mutation_started": False})
        r = self.author(P.INSTANTIATE, sid=self.sid, **self.CALLS[P.INSTANTIATE])
        self.assertStatus(r, tdg.CONFLICT, "LIVE_PREFAB_DIRTY")
        self.assertFalse(r.mutation_performed)                       # refused before the import
        self.b.author_reply = lambda c, a: ("REFUSED", "PREFAB_CONFLICT", {
            "mutation_started": False, "dependent_scenes_dirty": [SCENE]})
        r = self.author(P.SET_PROPERTY, sid=self.sid, **self.CALLS[P.SET_PROPERTY])
        self.assertStatus(r, tdg.CONFLICT, "LIVE_PREFAB_CONFLICT")
        self.assertEqual((r.mutation_performed, r.data["dependent_scenes_dirty"]), (False, [SCENE]))

    def test_the_commit_point_and_failures(self):
        cases = (("PERSISTENCE_UNKNOWN", {"mutation_started": True, "commit_started": True}, "LIVE_OUTCOME_UNKNOWN",
                  tdg.OUTCOME_UNKNOWN, True),
                 ("PERSISTENCE_UNKNOWN", None, "LIVE_OUTCOME_UNKNOWN", tdg.OUTCOME_UNKNOWN, True),
                 ("ROLLBACK_INCOMPLETE", {"mutation_started": True}, "LIVE_ROLLBACK_INCOMPLETE", tdg.FAILED, True),
                 ("AUTHORING_FAILED", {"mutation_started": True, "compensated": "TEMP_REMOVED"},
                  "LIVE_AUTHORING_FAILED", tdg.FAILED, True))
        for bridge_code, data, code, status, mutated in cases:
            with self.subTest(bridge_code):
                self.b.author_reply = lambda c, a, bc=bridge_code, d=data: ("FAILED", bc, d)
                r = self.author(P.SET_PROPERTY, sid=self.sid, **self.CALLS[P.SET_PROPERTY])
                self.assertStatus(r, status, code)
                self.assertEqual(r.mutation_performed, mutated)
        self.b.author_reply = lambda c, a: ("FAILED", "PERSISTENCE_UNKNOWN", None)
        count = published(self.b, "set-prefab-property")
        self.author(P.SET_PROPERTY, sid=self.sid, **self.CALLS[P.SET_PROPERTY])
        time.sleep(0.5)
        self.assertEqual(published(self.b, "set-prefab-property"), count + 1)    # never retried

    def test_recovery_is_reported_by_record_kind_and_side_effects_are_disclosed(self):
        recovered = [{"txn_id": "a" * 32, "kind": "PREFAB", "final_path": "Assets/Made/Old.prefab",
                      "outcome": "TEMP_REMOVED", "changed": True},
                     {"txn_id": "b" * 32, "kind": "MATERIAL", "final_path": "Assets/Materials/Kept.mat",
                      "outcome": "FINAL_KEPT", "changed": False}]
        self.b.author_reply = lambda c, a: ("OK", None, {"created": {"path": a.get("path")}, "recovered": recovered})
        for cap, calls in ((P.CREATE_PREFAB, self.CALLS[P.CREATE_PREFAB]),
                           (A.CREATE_MATERIAL, {"path": "Assets/Materials/H.mat", "shader": MAT,
                                                "expected_shader_catalog_digest": DIGEST})):
            with self.subTest(cap):
                r = self.author(cap, sid=self.sid, **calls)
                codes = [d.code for d in r.diagnostics]
                self.assertEqual((codes.count("LIVE_PREFAB_CREATE_RECOVERED"), codes.count("LIVE_ASSET_CREATE_RECOVERED")),
                                 (1, 1))
        self.b.author_reply = lambda c, a: ("REFUSED", "PREFAB_CREATE_INCOMPLETE",
                                            {"mutation_started": True, "recovered": recovered[:1]})
        r = self.author(P.CREATE_PREFAB, sid=self.sid, **self.CALLS[P.CREATE_PREFAB])
        self.assertStatus(r, tdg.CONFLICT, "LIVE_PREFAB_CREATE_INCOMPLETE")
        self.assertIn("LIVE_PREFAB_CREATE_RECOVERED", self.codes(r))
        self.assertTrue(r.mutation_performed)
        self.b.author_reply = lambda c, a: ("OK", None, {"prefab": {"path": "Assets/P.prefab"},
                                                         "unrequested_changes": [PCOMP], "unrequested_change_count": 1,
                                                         "scenes_marked_dirty": [SCENE]})
        r = self.author(P.SET_PROPERTY, sid=self.sid, **self.CALLS[P.SET_PROPERTY])
        self.assertStatus(r, tdg.SUCCESS, "LIVE_PREFAB_SIDE_EFFECTS")
        self.b.author_reply = lambda c, a: ("OK", None, {"prefab": {"path": "Assets/P.prefab"},
                                                         "unrequested_changes": [], "scenes_marked_dirty": []})
        r = self.author(P.SET_PROPERTY, sid=self.sid, **self.CALLS[P.SET_PROPERTY])
        self.assertEqual(self.codes(r), {"LIVE_PREFAB_SAVED"})

    def test_busy_withdrawn_unknown_and_incompatible(self):
        inputs = self.CALLS[P.CREATE_PREFAB]
        self.b.phase = "PLAYING"
        r = self.author(P.CREATE_PREFAB, sid=self.sid, **inputs)
        self.assertStatus(r, tdg.CONFLICT, "EDITOR_BUSY")
        self.assertFalse(r.mutation_performed)
        self.b.phase = "EDIT"
        self.b.mode = "claim-only"
        r = self.author(P.SET_PROPERTY, sid=self.sid, timeout=1, **self.CALLS[P.SET_PROPERTY])
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertTrue(r.mutation_performed)
        self.b.mode = None
        self.b.protocol, self.b.bridge_version = "gpos.unity.live/3", "1.2.0"
        self.b.publish("READY")
        before = len(self.b.author_calls)
        r = self.author(P.PREFAB_INSPECT, sid=self.sid, prefab=PID)
        self.assertStatus(r, tdg.INCOMPATIBLE, "LIVE_BRIDGE_INCOMPATIBLE")
        self.assertEqual(len(self.b.author_calls), before)


# ---------------------------------------------------------------- D  the frozen 1.2.0 release

import test_unity_authoring as ta  # noqa: E402
import test_unity_live as tl  # noqa: E402


class D_Release(LiveCase):
    install = False

    def test_the_1_2_0_history_is_the_frozen_alpha_18_release(self):
        frozen = ta.git("show", f"{ta.FROZEN_TAG_12}:gpos/tools/unity/live_bridge/manifest.json", binary=True)
        self.assertEqual((bi.HISTORY / "1.2.0.json").read_bytes(), frozen)
        self.assertEqual(hashlib.sha256(frozen).hexdigest(),
                         "d37ce938c5060262f62b5a008f62700a27ae2e8307aa27944b89069a08141948")
        self.assertEqual(bi.PREVIOUS["1.2.0"], ("gpos.unity.live/3", ta.FROZEN_DIGEST_12))
        self.assertEqual((bi.BRIDGE_VERSION, bi.PROTOCOL), ("1.3.0", "gpos.unity.live/4"))
        manifest = bi.verify_source()
        self.assertEqual((manifest["bridge_version"], manifest["protocol"], len(manifest["files"])),
                         ("1.3.0", "gpos.unity.live/4", 54))
        names = {e["path"] for e in manifest["files"]}
        self.assertTrue({"Editor/PrefabAuthoring.cs", "Editor/PrefabResolver.cs", "Editor/Core/PrefabRules.cs"} <= names)

    def test_an_exact_1_2_0_bridge_is_upgraded(self):
        target = self.game / "Packages" / bi.PACKAGE_ID
        ta.frozen_package(target, ta.FROZEN_TAG_12)
        self.assertEqual(bi.installed_version(self.game), "1.2.0")
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.PREVIOUS_STATE, []))
        r = self.run_cap(live.INSTALL)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_BRIDGE_UPGRADED")
        self.assertEqual((r.data["upgraded_from"], r.data["installed_state"]), ("1.2.0", bi.EXACT))
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.EXACT, []))


# ---------------------------------------------------------------- E  boundaries (source)

class E_Boundaries(unittest.TestCase):
    SOURCES = ("PrefabAuthoring.cs", "PrefabResolver.cs", "Core/PrefabRules.cs")

    def text(self, name):
        lines = (EDITOR_SOURCE / name).read_text().splitlines()
        return "\n".join(line.split("//")[0] if not line.lstrip().startswith("//") else "" for line in lines)

    def test_no_deferred_prefab_operation_or_forbidden_mechanism(self):
        deferred = ("SaveAsPrefabAssetAndConnect", "ApplyPrefabInstance", "ApplyObjectOverride", "ApplyPropertyOverride",
                    "ApplyAddedComponent", "ApplyRemovedComponent", "ApplyAddedGameObject", "ApplyRemovedGameObject",
                    "RevertPrefabInstance", "RevertObjectOverride", "RevertPropertyOverride", "RevertAddedComponent",
                    "RevertRemovedComponent", "RevertAddedGameObject", "RevertRemovedGameObject", "UnpackPrefabInstance",
                    "ConvertToPrefabInstance", "ReplacePrefabAssetOfPrefabInstance", "PrefabStageUtility.OpenPrefab",
                    "GoToMainStage", "GoBackToPreviousStage", "GoToStage", "ClearDirtiness",
                    "LoadPrefabContentsIntoPreviewScene", "SetPropertyModifications", "RecordPrefabInstancePropertyModifications",
                    "DisconnectPrefabInstance", "CreatePrefab", "ReplacePrefab", "new GameObject(", "Undo.DestroyObjectImmediate",
                    "SetParent(", "AssetDatabase.Refresh", "AssetDatabase.SaveAssets(", "SaveAssetIfDirty",
                    "SaveScene", "MarkSceneClean", "MarkSceneDirty", "System.Reflection", "Assembly.Load", "Activator.",
                    ".Invoke(", "GetMethod(", "Type.GetType", "ExecuteMenuItem", "executeMethod", "GetInstanceID",
                    "InstanceIDToObject", "EntityId", "Process.Start", "Socket", "HttpClient", "WebRequest",
                    "DisplayDialog", "OpenScene", "Provider.Checkout", "Provider.Add", "AddObjectToAsset",
                    "managedReferenceValue", "arraySize", "InsertArrayElement", "DeleteArrayElement", "Kill(",
                    "EditorApplication.Exit", "Undo.RevertAllDownToGroup", "Undo.RecordObject", "Undo.AddComponent",
                    "Undo.ClearAll", "ChangeCheck", "chmod", "File.Delete", "Directory.Delete", "File.Write")
        for name in self.SOURCES:
            text = self.text(name)
            for word in deferred:
                with self.subTest(f"{name}: {word}"):
                    self.assertNotIn(word, text)
        core = self.text("Core/PrefabRules.cs")
        for word in ("File.", "Directory.", "AssetDatabase", "UnityEngine", "UnityEditor", "PrefabUtility"):
            self.assertNotIn(word, core)
        resolver = self.text("PrefabResolver.cs")                      # it only reads
        for word in ("AssetDatabase.CreateAsset", "AssetDatabase.DeleteAsset", "AssetDatabase.MoveAsset",
                     "AssetDatabase.ImportAsset", "SaveAsPrefabAsset", "LoadPrefabContents", "InstantiatePrefab",
                     "SetDirty", "Undo.", "mkdir", "DestroyImmediate"):
            self.assertNotIn(word, resolver, word)

    def test_every_write_primitive_appears_exactly_where_reviewed(self):
        text = self.text("PrefabAuthoring.cs")
        counts = {"PrefabUtility.SaveAsPrefabAsset(": 2, "PrefabUtility.LoadPrefabContents(": 1,
                  "PrefabUtility.UnloadPrefabContents(": 1, "PrefabUtility.InstantiatePrefab(": 2,
                  "AssetDatabase.ImportAsset(": 3, "AssetDatabase.MoveAsset(": 1, "AssetDatabase.ValidateMoveAsset(": 1,
                  "AssetDatabase.DeleteAsset(": 0, "mkdir(AssetFiles.Full(scratch)": 1, "Undo.RegisterCreatedObjectUndo(": 1,
                  "UnityEngine.Object.DestroyImmediate(copy)": 1, "DestroyImmediate(": 1, "ApplyModifiedPropertiesWithoutUndo()": 1,
                  "ApplyModifiedProperties()": 0, "CreateTxns.Write(txn)": 4, "AssetAuthoring.Compensate(": 2,
                  "SetStaticEditorFlags(": 1, ".AddComponent(": 1, "Authoring.Mutate(": 1}
        for word, n in counts.items():
            with self.subTest(word):
                self.assertEqual(text.count(word), n)
        self.assertEqual(text.count("PrefabUtility.SaveAsPrefabAsset(source, temp, out success)"), 1)   # never the final path
        self.assertEqual(text.count("PrefabUtility.SaveAsPrefabAsset(contents, path, out success)"), 1)  # the edited copy only
        self.assertEqual(text.count("AssetDatabase.ImportAsset(path)"), 1)
        self.assertEqual(text.count("AssetDatabase.ImportAsset(p.Path)"), 1)
        self.assertEqual(text.count("AssetDatabase.ImportAsset(scratch)"), 1)

    def test_the_edit_lifecycle_is_in_order(self):
        text = self.text("PrefabAuthoring.cs")
        edit = text[text.index("static Dictionary<string, object> Edit("):text.index("static PrefabInfo PrefabOf(")]
        order = ("PrefabResolver.Object(id, out p)", "PrefabResolver.RequireScope(p)", "PrefabScope.Owned", "edit.Check(p, target)",
                 "PrefabResolver.RequireNoStage()", "PrefabResolver.Dirty(p)", "PrefabResolver.VersionControl(p.Path)",
                 "PrefabResolver.NotWritable(p.Path)", "AssetFiles.LinkFree(p.Path)", "PrefabResolver.DependentScenes(p.Path)",
                 "AssetDatabase.ImportAsset(path)", 'AssetAuthoring.Step("import", path)', "state = PrefabResolver.State(before)",
                 "state.Token != expected", "PrefabUtility.LoadPrefabContents(path)", "PrefabResolver.Map(before, contents)",
                 "edit.Apply(map, copy)", 'AssetAuthoring.Step("pre-save", path)', "PrefabResolver.AnyStageOpen()",
                 "PrefabResolver.Dirty(now)", "fileNow != state.File || metaNow != state.Meta", "commit = true",
                 "PrefabUtility.SaveAsPrefabAsset(contents, path, out success)", "PrefabUtility.UnloadPrefabContents(contents)",
                 'AssetAuthoring.Step("saved", path)', "edit.Verify(after, before, id)")
        positions = [edit.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))
        for guard in ('if (vcs != null) throw new Refusal("PREFAB_NOT_EDITABLE"', 'if (os != null) throw new Refusal("PREFAB_NOT_EDITABLE"',
                      'if (dirtyDependents.Count > 0)', 'throw new Refusal("PREFAB_DIRTY", "the prefab has unsaved changes',
                      'if (PrefabResolver.Dirty(now)) throw new Refusal("PREFAB_DIRTY"'):
            self.assertEqual(edit.count(guard), 1, guard)
            self.assertLess(edit.index(guard), edit.index("commit = true"))
        before_save = edit[:edit.index("commit = true")]
        self.assertNotIn('"PERSISTENCE_UNKNOWN"', before_save)
        self.assertIn('"PERSISTENCE_UNKNOWN"', edit[edit.index("commit = true"):])
        self.assertIn("finally", edit[edit.index("PrefabUtility.LoadPrefabContents(path)"):edit.index('AssetAuthoring.Step("saved", path)')])
        self.assertNotIn("Undo.", edit)

    def test_instantiate_imports_a_clean_prefab_and_records_ids_first(self):
        text = self.text("PrefabAuthoring.cs")
        inst = text[text.index("static Dictionary<string, object> Instantiate("):text.index("abstract class PrefabEdit")]
        order = ("PrefabResolver.RequireNoStage()", "PrefabResolver.Prefab(prefabId)", "PrefabResolver.RequireScope(p)",
                 "PrefabResolver.Dirty(p)", "AssetDatabase.ImportAsset(p.Path)", 'AssetAuthoring.Step("import", p.Path)',
                 "fresh = PrefabResolver.Prefab(prefabId)", "PrefabResolver.Dirty(fresh)", "state.Token != expected",
                 "Authoring.Mutate(", "PrefabUtility.InstantiatePrefab(", "ids = SceneObjects.Ids(made.ToArray())",
                 "Undo.RegisterCreatedObjectUndo(go")
        positions = [inst.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))

    def test_the_creation_transaction_is_in_order(self):
        text = self.text("PrefabAuthoring.cs")
        create = text[text.index("static Dictionary<string, object> Create("):text.index("static Dictionary<string, object> Transact(")]
        order = ("PrefabPaths.CheckWritePath(path)", "PrefabResolver.RequireNoStage()", "PrefabResolver.VersionControl(null)",
                 "Scan(source)", "Authoring.Expect(expected, token", "Folder(folder)", "AssetAuthoring.CheckAbsent(folder, path)",
                 "CreateTxns.RecoverAll()", "Kind = AssetKinds.Prefab", "CreateTxns.Write(txn)", 'AssetAuthoring.Step("prepared", path)',
                 "Transact(")
        positions = [create.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))
        transact = text[text.index("static Dictionary<string, object> Transact("):text.index("static Dictionary<UnityEngine.Object, UnityEngine.Object> Prove(")]
        order = ("mkdir(AssetFiles.Full(scratch)", "AssetDatabase.ImportAsset(scratch)", "CreateTxns.Write(txn)",
                 'AssetAuthoring.Step("scratch", path)', "unknown content appeared in the scratch folder",
                 "SceneObjects.SubtreeToken(source) != token", "PrefabUtility.SaveAsPrefabAsset(source, temp, out success)",
                 "txn.Phase = CreateTxn.TempProven", "Prove(created, plan, stem)", 'AssetAuthoring.Step("pre-move", path)',
                 "AssetAuthoring.CheckAbsent(folder, path)", "AssetDatabase.ValidateMoveAsset(temp, path)",
                 "st.MoveAttempted = true", "AssetDatabase.MoveAsset(temp, path)", "txn.Phase = CreateTxn.FinalProven",
                 "CreateTxns.RemoveScratch(txn)", "CreateTxns.Delete(txn)")
        positions = [transact.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))

    def test_the_alpha_18_asset_surface_still_authors_only_materials_and_scriptable_objects(self):
        """Correction 1: prefab write authority belongs only to the prefab commands."""
        resolver = self.text("AssetResolver.cs")
        self.assertIn("if (r.Kind != AssetKinds.Material && r.Kind != AssetKinds.ScriptableObject) return false;", resolver)
        rules = self.text("Core/AssetRules.cs")
        self.assertIn('{ Prefab, new[] { "t:Prefab", "the root GameObject of a prefab asset", "" } }', rules)   # no extension
        self.assertIn('if (ext != ".mat" && ext != ".asset") throw Invalid("assets of this kind are never written");', rules)
        assets = self.text("AssetAuthoring.cs")
        writes = assets[assets.index("static Dictionary<string, object> Persist("):assets.index("internal static class CreateTxns")]
        for word in ("AssetKinds.Prefab", "SaveAsPrefabAsset", "PrefabUtility", ".prefab"):   # the asset write paths
            self.assertNotIn(word, writes, word)
        self.assertEqual(assets.count("RequireAuthorable("), 2)                               # Material, ScriptableObject

    def test_the_scene_prefab_boundary_is_unchanged(self):
        authoring = self.text("Authoring.cs")
        self.assertEqual(authoring.count("PrefabBoundary("), 17)
        properties = self.text("Properties.cs")
        write = properties[properties.index("public static string WriteRefusal("):properties.index("public static string CoreRefusal(")]
        self.assertIn('if (c != null && SceneObjects.Role(c) != SceneObjects.None) return "PREFAB_BOUNDARY";', write)
        self.assertEqual(properties.count("core ? CoreRefusal(c, so, p, visible) : WriteRefusal(c, so, p, visible)"), 1)
        self.assertIn("return Writable(c, so, path, kind, false);", properties)
        prefab = self.text("PrefabAuthoring.cs")
        self.assertEqual(prefab.count("Properties.WritableCore("), 1)
        self.assertEqual(prefab.count("Properties.Entries(c, prefix, true)"), 1)

    def test_the_python_side_starts_nothing(self):
        text = Path(P.__file__).read_text()
        for word in ("subprocess", "socket", "urllib", "http", "os.system", "eval(", "exec(", "open("):
            self.assertNotIn(word, text)


# ================================================================ real synthetic Unity (lab-owned batch Editors)

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
    REAL["work"] = Path(tempfile.mkdtemp(prefix="gpos-prefabs-real-")).resolve()
    ta.REAL_STATE.setdefault("work", REAL["work"])


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


def prefab_project(name, install=True):
    p = REAL["work"] / name / "p"
    shutil.copytree(FIXTURE, p)
    fixtures.make_prefab_project(p / "Game", EDITOR_VERSION, EDITOR)
    shutil.copytree(tl.TESTKIT, p / "Game" / "Packages" / tl.TESTKIT.name)
    if install:
        bi.install(p, p / "Game", bi.verify_source())
    return p


ok = ta.ok


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RealPrefabs(unittest.TestCase):
    """Shared helpers of the real prefab groups."""

    lab = None
    s = None

    @classmethod
    def start(cls, name):
        cls.lab = ta.Lab(prefab_project(name))
        cls.s = {}
        cls.lab.editor.launch()
        r = cls.lab.run(live.ATTACH, timeout=120)
        if r.status != tdg.SUCCESS:
            raise AssertionError(f"attach failed: {[d.message for d in r.diagnostics]}")
        cls.lab.sid = r.data["session_id"]
        for op in ("setup", "setup-assets", "setup-prefabs"):
            cls.s.update(cls.lab.human(op=op))

    @classmethod
    def tearDownClass(cls):
        cls.lab.editor.stop()

    def run_cap(self, cap, **inputs):
        return self.lab.run(cap, **inputs)

    def human(self, **op):
        return self.lab.human(**op)

    def inspect(self, prefab, **kw):
        return ok(self, self.run_cap(P.PREFAB_INSPECT, prefab=prefab, **kw))

    def token(self, prefab=None):
        return self.inspect(prefab or self.s["plain"])["tokens"]["prefab"]

    def objects(self, prefab):
        d = self.inspect(prefab)
        return {o["id"]: o for o in d["objects"]}

    def props(self, prefab, component, prefix=None):
        kw = {"path_prefix": prefix} if prefix else {}
        return {e["path"]: e for e in self.inspect(prefab, component=component, **kw)["properties"]}

    def roots(self, scene=SCENE):
        return ok(self, self.run_cap(au.INSPECT_OBJECT, scene=scene))["tokens"]["scene_roots"]

    def subtree(self, oid):
        return ok(self, self.run_cap(au.INSPECT_OBJECT, object=oid))["tokens"]["subtree"]

    def digest(self):
        return ok(self, self.run_cap(au.COMPONENT_TYPES))["catalog_digest"]

    def dirty(self, scene=SCENE):
        return self.human(op="scene-state", scene=scene)["dirty"] is True

    def save(self):
        self.human(op="save-open-scenes")

    def file(self, rel):
        return self.lab.game / rel

    def records(self):
        from gpos.tools.unity import identity as ident
        key = ident.project_key(ident.relative(self.lab.p, self.lab.game))
        d = self.lab.p / ".game" / "gpos-runtime" / "unity" / "asset-create-txn" / key
        return sorted(p for p in d.glob("*.json")) if d.is_dir() else []

    def leftovers(self):
        return sorted(p.name for p in (self.lab.game / "Assets").iterdir() if p.name.startswith("GposAssetTxn-"))

    def create(self, source, path, **kw):
        return self.run_cap(P.CREATE_PREFAB, source=source, path=path, expected_subtree_token=self.subtree(source), **kw)

    def instantiate(self, prefab, scene=SCENE, parent=None, token=None, **kw):
        place = ({"parent": parent, "expected_parent_token": ok(self, self.run_cap(au.INSPECT_OBJECT, object=parent))["tokens"]["object"]}
                 if parent else {"expected_scene_roots_token": self.roots(scene)})
        return self.run_cap(P.INSTANTIATE, prefab=prefab, scene=scene, expected_prefab_token=token or self.token(prefab),
                            **place, **kw)

    def prop(self, component, path, kind, value, prefab=None, token=None, save=True, **kw):
        """One prefab property edit; open Scenes are saved first (a dirty Scene the save would propagate into is
        refused by design, D2)."""
        if save:
            self.save()
        return self.run_cap(P.SET_PROPERTY, component=component, path=path, kind=kind, value=value,
                            expected_prefab_token=token or self.token(prefab), **kw)

    def edit(self, cap, prefab=None, **inputs):
        self.save()
        return self.run_cap(cap, expected_prefab_token=self.token(prefab), **inputs)

    def wait_edit(self, timeout=240):
        self.assertTrue(self.lab.editor.wait(lambda: (self.lab.editor.heartbeat().get("state") or {}).get("phase")
                                             == "EDIT", timeout))
        time.sleep(1)


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R5_RealPrefabs(RealPrefabs):
    """One synthetic project, one lab Editor, one live session: inspection, creation, instantiation, every edit."""

    @classmethod
    def setUpClass(cls):
        cls.start("prefabs")

    def test_01_inspection_scope_and_tokens(self):
        s = self.s
        d = self.inspect(s["plain"])
        self.assertEqual((d["prefab"]["type"], d["prefab"]["source"], d["prefab"]["path"], d["mutable"], d["scope_reasons"],
                          d["token_scope"], d["nested_present"], d["dirty"], d["stage_open"]),
                         ("REGULAR", "ASSETS", "Assets/Prefabs/Plain.prefab", True, [], "PREFAB", False, False, False))
        self.assertEqual([(o["name"], o["depth"], o["ownership"]) for o in d["objects"]],
                         [("Plain", 0, "OWNED"), ("Child", 1, "OWNED"), ("Leaf", 2, "OWNED"), ("Child", 1, "OWNED")])
        guid = d["prefab"]["guid"]
        for o in d["objects"]:
            self.assertTrue(o["id"].startswith(f"GlobalObjectId_V1-1-{guid}-") and o["id"].endswith("-0"))
            for c in o["components"]:
                self.assertTrue(c["id"].startswith(f"GlobalObjectId_V1-1-{guid}-") and c["ownership"] == "OWNED")
        self.assertEqual(d["objects"][0]["id"], s["plain"])
        self.assertRegex(d["tokens"]["prefab"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.token(), d["tokens"]["prefab"])                                   # stable
        cases = {"variant": ("VARIANT", ["VARIANT"], "NOT_COVERED"), "outer": ("REGULAR", ["NESTED_PRESENT"], "NOT_COVERED"),
                 "pkg_prefab": ("REGULAR", ["PACKAGE"], "PREFAB"), "embedded": ("REGULAR", ["EMBEDDED_ASSETS"], "NOT_COVERED"),
                 "model": ("MODEL", ["MODEL"], "NOT_COVERED")}
        for key, (kind, reasons, scope) in cases.items():
            with self.subTest(key):
                x = self.inspect(s[key])
                self.assertEqual((x["prefab"]["type"], x["mutable"], x["token_scope"]), (kind, False, scope))
                self.assertTrue(set(reasons) <= set(x["scope_reasons"]), x["scope_reasons"])
                self.assertEqual(x["tokens"]["prefab"] is None, scope == "NOT_COVERED")
        outer = self.inspect(s["outer"])["objects"]
        self.assertEqual([o["ownership"] for o in outer][:2], ["OWNED", "NESTED_ROOT"])
        self.assertEqual(outer[1]["nested_source"]["path"], "Assets/Prefabs/Plain.prefab")
        self.assertIn("NESTED_CONTENT", {o["ownership"] for o in outer})
        variant = self.inspect(s["variant"])["objects"]
        self.assertEqual({o["ownership"] for o in variant}, {"VARIANT_INHERITED"})
        ok(self, self.run_cap(P.PREFAB_INSPECT, prefab=s["plain_child"]), "LIVE_OBJECT_REFUSED", tdg.INVALID_REQUEST)
        ok(self, self.run_cap(P.PREFAB_INSPECT, prefab=s["instance"]), "LIVE_OBJECT_REFUSED", tdg.INVALID_REQUEST)
        ok(self, self.run_cap(P.PREFAB_INSPECT, prefab=f"GlobalObjectId_V1-1-{'9' * 32}-5-0"), "LIVE_OBJECT_NOT_FOUND",
           tdg.CONFLICT)

    def test_02_inspection_never_claims_authority_the_mutation_refuses(self):
        """Correction 4: effective write authority = the property rule AND the prefab scope."""
        s = self.s
        plain = self.props(s["plain"], s["plain_refs"])
        self.assertEqual((plain["count"]["writable"], plain["count"]["property_writable"], plain["count"]["prefab_mutable"],
                          plain["count"]["refusal"]), (True, True, True, None))
        self.assertEqual((plain["other"]["value"], plain["other"]["same_prefab"]), (s["plain_child"], True))
        self.assertEqual((plain["m_Script"]["writable"], plain["m_Script"]["refusal"]), (False, "DENIED"))
        for key, component, refusal in (("variant", "variant_refs", "VARIANT"), ("outer", "outer_nested_refs", "NESTED_PRESENT"),
                                        ("pkg_prefab", "pkg_prefab_box", "PACKAGE")):
            with self.subTest(key):
                props = self.props(s[key], s[component])
                writable = [p for p in props.values() if p["property_writable"]]
                self.assertTrue(writable)
                for e in props.values():
                    self.assertEqual((e["writable"], e["prefab_mutable"]), (False, False), e["path"])
                self.assertEqual({e["refusal"] for e in writable}, {refusal})
        model = self.inspect(s["model"])
        mc = model["objects"][0]["components"][-1]["id"]
        for e in self.props(s["model"], mc).values():
            self.assertFalse(e["writable"])

    def test_02b_the_alpha_18_asset_surface_never_writes_a_prefab(self):
        """Correction 1: asset-inspect reports a prefab (and its root component) as not authorable, and no alpha.18
        asset mutation accepts one."""
        s = self.s
        for key in ("plain", "plain_refs", "variant", "pkg_prefab"):
            with self.subTest(key):
                d = ok(self, self.run_cap(A.ASSET_INSPECT, asset=s[key]))
                self.assertEqual((d["asset"]["kind"] in ("PREFAB", "PREFAB_COMPONENT"), d["asset"]["authorable"]), (True, False))
                self.assertNotIn("tokens", d)
        before = sha(self.file("Assets/Prefabs/Plain.prefab"))
        for cap, inputs in ((A.SET_ASSET_PROPERTY, {"asset": s["plain"], "path": "m_Name", "kind": "string", "value": "x"}),
                            (A.SET_ASSET_PROPERTY, {"asset": s["plain_refs"], "path": "count", "kind": "int32", "value": 1}),
                            (A.SET_MATERIAL_PROPERTY, {"material": s["plain"], "property": "_Color", "kind": "color", "value": [1, 0, 0, 1]})):
            with self.subTest(cap):
                r = self.run_cap(cap, expected_asset_token=T, **inputs)
                ok(self, r, "LIVE_ASSET_REFUSED", tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)
        self.assertEqual(sha(self.file("Assets/Prefabs/Plain.prefab")), before)

    def test_03_instance_inspection_reports_every_override_kind(self):
        s = self.s
        d = ok(self, self.run_cap(P.INSTANCE_INSPECT, object=s["instance"]))
        self.assertEqual((d["role"], d["instance"]["status"], d["instance"]["source_path"], d["instance"]["source_root"]),
                         ("INSTANCE_ROOT", "CONNECTED", "Assets/Prefabs/Plain.prefab", s["plain"]))
        kinds = {o["kind"] for o in d["overrides"]}
        self.assertEqual(kinds, {"PROPERTY", "ADDED_COMPONENT", "REMOVED_COMPONENT", "ADDED_GAME_OBJECT", "REMOVED_GAME_OBJECT"})
        own = [o for o in d["overrides"] if o["kind"] == "PROPERTY" and not o["default_override"]]
        self.assertEqual([(o["target"], o["property_path"], o["value"], o["nested"]) for o in own],
                         [(s["plain_refs"], "count", "5", False)])
        removed = {o["source"] for o in d["overrides"] if o["kind"].startswith("REMOVED")}
        self.assertEqual(removed, {s["plain_child2_sphere"], s["plain_leaf"]})
        self.assertIn(s["instance_added"], {o.get("id") for o in d["overrides"]})
        mapping = {o["id"]: o["source"] for o in d["objects"]}
        self.assertEqual(mapping[s["instance"]], s["plain"])
        self.assertIsNone(mapping[s["instance_added"]])
        nested = ok(self, self.run_cap(P.INSTANCE_INSPECT, object=s["outer_instance"]))
        own = [o for o in nested["overrides"] if not o.get("default_override")]
        self.assertEqual([(o["property_path"], o["value"], o["nested"], o["nested_source"], o["nested_source_path"]) for o in own],
                         [("count", "9", True, s["plain_refs"], "Assets/Prefabs/Plain.prefab")])
        plain = ok(self, self.run_cap(P.INSTANCE_INSPECT, object=s["holder"]))
        self.assertEqual((plain["role"], plain["instance"]), ("NONE", None))
        inner = ok(self, self.run_cap(P.INSTANCE_INSPECT, object=s["instance_child"]))
        self.assertEqual((inner["role"], inner["instance"]["root"]["id"]), ("INSTANCE_CONTENT", s["instance"]))

    def test_04_a_plain_subtree_becomes_a_regular_prefab(self):
        s = self.s
        token = self.subtree(s["source"])
        undo = self.human(op="undo-name")["group"]
        r = self.run_cap(P.CREATE_PREFAB, source=s["source"], path="Assets/Made/Made.prefab", expected_subtree_token=token)
        d = ok(self, r, "LIVE_PREFAB_CREATED")
        self.assertTrue(r.mutation_performed)
        c = d["created"]
        self.assertEqual((c["path"], c["type"], c["source"], c["name"]), ("Assets/Made/Made.prefab", "REGULAR", "ASSETS", "Made"))
        self.assertEqual((d["undoable"], d["source"]["connected"], d["source"]["subtree_token"], d["scenes_marked_dirty"]),
                         (False, False, token, []))
        self.assertEqual(d["tokens"]["prefab"], self.token(c["id"]))
        self.assertEqual(sha(self.file("Assets/Made/Made.prefab")), d["file_sha256"])
        self.assertEqual((self.leftovers(), self.records()), ([], []))
        # the source stays plain Scene content with the same ids and state; nothing was recorded for Undo
        self.assertEqual(self.subtree(s["source"]), token)
        self.assertEqual(ok(self, self.run_cap(au.INSPECT_OBJECT, object=s["source"]))["object"]["prefab"], "NONE")
        self.assertFalse(self.dirty())
        self.assertEqual(self.human(op="undo-name")["group"], undo)
        mapping = {m["source"]: m["id"] for m in c["objects"]}
        self.assertEqual(mapping[s["source"]], c["id"])
        made = self.inspect(c["id"])
        self.assertEqual([o["name"] for o in made["objects"]], ["Made", "Kid", "Kid2"])        # the root takes the file name
        self.assertEqual(made["objects"][0]["transform"]["local_position"], [1, 2, 3])        # the root keeps its local pose
        refs = self.props(c["id"], mapping[s["source_refs"]])
        self.assertEqual({k: refs[k]["value"] for k in ("other", "child", "box", "mat", "sprite", "prefab", "count")},
                         {"other": mapping[s["source_kid"]], "child": [o for o in made["objects"] if o["name"] == "Kid2"][0]["components"][0]["id"],
                          "box": mapping[s["source_kid_box"]], "mat": s["base"], "sprite": s["hero_sprite"],
                          "prefab": s["plain"], "count": 7})
        self.assertTrue(refs["sprite"]["value"].endswith("-21300000-0"))                     # the Sprite sub-asset itself
        self.s["made"] = c["id"]
        self.s["made_refs"] = mapping[s["source_refs"]]
        self.s["made_kid"] = mapping[s["source_kid"]]

    def test_05_creation_refuses_every_unsafe_source_and_path(self):
        s = self.s
        leak = self.create(s["leak"], "Assets/Made/Leak.prefab")
        ok(self, leak, "LIVE_PREFAB_REFUSED", tdg.INVALID_REQUEST)
        self.assertFalse(leak.mutation_performed)
        self.assertEqual([(x["property_path"], x["reason"]) for x in leak.data["references_refused"]], [("other", "SCENE_OUTSIDE")])
        u = self.create(s["unsupported"], "Assets/Made/U.prefab")
        ok(self, u, "LIVE_PREFAB_REFUSED", tdg.INVALID_REQUEST)
        self.assertEqual([x["reason"] for x in u.data["references_refused"]], ["UNSUPPORTED_ASSET"])
        for key in ("instance", "instance_child", "outer_instance"):           # an instance source would become a Variant
            with self.subTest(key):
                r = self.create(s[key], f"Assets/Made/{key}.prefab")
                ok(self, r, "LIVE_PREFAB_REFUSED", tdg.INVALID_REQUEST)
                self.assertIn("relative to a prefab instance", r.diagnostics[0].message)   # the source rule itself
                self.assertNotIn("references_refused", r.data or {})
                self.assertFalse(self.file(f"Assets/Made/{key}.prefab").exists())
        hidden = self.human(op="add-child", target=s["holder"], name="Hidden")["id"]
        self.human(op="hide", target=hidden)
        ok(self, self.create(s["holder"], "Assets/Made/Hidden.prefab"), "LIVE_PREFAB_REFUSED", tdg.INVALID_REQUEST)
        for _ in range(3):
            if self.human(op="exists", target=hidden)["exists"] is False:
                break
            self.human(op="undo")                                                               # the Human's Cmd-Z
        self.assertIs(self.human(op="exists", target=hidden)["exists"], False)
        for path, code, status in (("Assets/Prefabs/Plain.prefab", "LIVE_ASSET_EXISTS", tdg.CONFLICT),
                                   ("Assets/Prefabs/plain.prefab", "LIVE_ASSET_EXISTS", tdg.CONFLICT),
                                   ("Assets/Nowhere/X.prefab", "LIVE_ASSET_PATH_INVALID", tdg.INVALID_REQUEST),
                                   ("Assets/Made/X.asset", "LIVE_ASSET_PATH_INVALID", tdg.INVALID_REQUEST),
                                   ("Packages/com.gpos.fixture-assets/X.prefab", "LIVE_ASSET_PATH_INVALID", tdg.INVALID_REQUEST)):
            with self.subTest(path):
                r = self.create(s["source_kid2"], path)
                ok(self, r, code, status)
                self.assertFalse(r.mutation_performed)
        before = sha(self.file("Assets/Prefabs/Plain.prefab"))
        self.assertEqual(sha(self.file("Assets/Prefabs/Plain.prefab")), before)              # never overwritten
        self.file("Assets/Made/Orphan.prefab.meta").write_text("fileFormatVersion: 2\nguid: " + "7" * 32 + "\n")
        ok(self, self.create(s["source_kid2"], "Assets/Made/Orphan.prefab"), "LIVE_ASSET_EXISTS", tdg.CONFLICT)
        self.file("Assets/Made/Orphan.prefab.meta").unlink()
        stale = self.run_cap(P.CREATE_PREFAB, source=s["source_kid2"], path="Assets/Made/Stale.prefab", expected_subtree_token=T)
        ok(self, stale, "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        folder = self.file("Assets/Made")
        os.chmod(folder, 0o555)
        try:
            r = self.create(s["source_kid2"], "Assets/Made/ReadOnly.prefab")
            ok(self, r, "LIVE_PREFAB_NOT_EDITABLE", tdg.CONFLICT)
            self.assertFalse(r.mutation_performed)
        finally:
            os.chmod(folder, 0o755)
        self.assertEqual((self.leftovers(), self.records()), ([], []))

    def test_06_instantiate_is_scene_authoring_with_stable_undo_redo_ids(self):
        s = self.s
        self.save()
        prefab_sha = sha(self.file("Assets/Prefabs/Plain.prefab"))
        r = self.instantiate(s["plain"], local_position="[4, 5, 6]", local_rotation="[0, 0.7071068, 0, 0.7071068]",
                             local_scale="[2, 2, 2]")
        d = ok(self, r, "LIVE_PREFAB_INSTANTIATED")
        self.assertTrue(r.mutation_performed)
        self.assertEqual((d["instance"]["prefab"], d["prefab"]["id"], d["object_count"], d["import_performed"], d["scene_saved"],
                          d["scenes_marked_dirty"], d["undo_group"]),
                         ("INSTANCE_ROOT", s["plain"], 11, True, False, [], "GPOS: instantiate Plain"))
        self.assertIn("m_LocalScale.x", {t["property_path"] for t in d["overrides_after"]["property_override_targets"]})
        self.assertEqual(sha(self.file("Assets/Prefabs/Plain.prefab")), prefab_sha)
        self.assertTrue(self.dirty())
        ids = [o["id"] for o in d["objects"]]
        self.assertEqual({o["source"] for o in d["objects"]}, set(self.objects(s["plain"])) | {
            c["id"] for o in self.objects(s["plain"]).values() for c in o["components"]})
        tf = ok(self, self.run_cap(au.INSPECT_OBJECT, object=d["instance"]["id"]))["transform"]
        self.assertEqual((tf["local_position"], tf["local_scale"]), ([4, 5, 6], [2, 2, 2]))
        self.human(op="undo")
        self.assertEqual({self.human(op="exists", target=i)["exists"] for i in ids}, {False})
        self.human(op="redo")
        self.assertEqual({self.human(op="exists", target=i)["exists"] for i in ids}, {True})     # every id, components too
        self.assertEqual(ok(self, self.run_cap(au.INSPECT_OBJECT, object=d["instance"]["id"]))["object"]["prefab"], "INSTANCE_ROOT")
        # the alpha.17 Scene prefab boundary still holds for the new instance
        inner = next(o["id"] for o in d["objects"] if o["source"] == s["plain_child"])
        t = ok(self, self.run_cap(au.INSPECT_OBJECT, object=inner))["tokens"]["object"]
        ok(self, self.run_cap(au.SET_GAMEOBJECT, object=inner, name="X", expected_object_token=t), "LIVE_PREFAB_BOUNDARY", tdg.CONFLICT)
        # under a plain parent, at a sibling index
        d = ok(self, self.instantiate(s["plain"], parent=s["holder"], sibling="0"), "LIVE_PREFAB_INSTANTIATED")
        parent = ok(self, self.run_cap(au.INSPECT_OBJECT, object=d["instance"]["id"]))
        self.assertEqual((parent["parent"]["id"], parent["sibling_index"]), (s["holder"], 0))
        # under prefab-instance content: the Scene prefab boundary
        r = self.instantiate(s["plain"], parent=s["instance_child"])
        ok(self, r, "LIVE_PREFAB_BOUNDARY", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        self.save()
        # into a non-active saved Scene: only that Scene becomes dirty
        second = self.human(op="second-scene", scene="Assets/Scenes/Second.unity", name="Other")["scene"]
        d = ok(self, self.instantiate(s["plain"], scene=second), "LIVE_PREFAB_INSTANTIATED")
        self.assertEqual((self.dirty(second), self.dirty(), d["scenes_marked_dirty"]), (True, False, []))
        self.human(op="save-open-scenes")
        self.human(op="close-scene", scene=second)

    def test_07_instantiate_refuses_out_of_scope_dirty_and_stale_sources(self):
        """D1 and correction 2: ASSETS regular prefabs without nested prefabs only; never Human unsaved state."""
        s = self.s
        for key in ("variant", "outer", "pkg_prefab", "embedded", "model"):
            with self.subTest(key):
                d = self.inspect(s[key])
                r = self.run_cap(P.INSTANTIATE, prefab=s[key], scene=SCENE, expected_prefab_token=d["tokens"]["prefab"] or T,
                                 expected_scene_roots_token=self.roots())
                ok(self, r, "LIVE_PREFAB_REFUSED", tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)
        count = self.human(op="instance-count", path="Assets/Prefabs/Plain.prefab", scene=SCENE)["count"]
        token = self.token()
        self.human(op="set-serialized", target=s["plain_refs"], path="count", kind="int", value="3")   # the Human's Inspector edit
        r = self.instantiate(s["plain"], token=token)
        ok(self, r, "LIVE_PREFAB_DIRTY", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)                                                     # refused before the import
        d = self.inspect(s["plain"])
        self.assertEqual((d["dirty"], d["mutable"], "DIRTY" in d["scope_reasons"]), (True, False, True))
        self.human(op="save-asset", target=s["plain"])                                              # the Human's Ctrl+S
        r = self.instantiate(s["plain"], token=token)                                                # a stale token
        ok(self, r, "LIVE_PREFAB_CONFLICT", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)                                                      # the import happened
        self.assertEqual((r.data["import_performed"], r.data["value_persisted"], r.data["scene_changed"]), (True, False, False))
        self.assertEqual(self.human(op="instance-count", path="Assets/Prefabs/Plain.prefab", scene=SCENE)["count"], count)
        stale_roots = self.run_cap(P.INSTANTIATE, prefab=s["plain"], scene=SCENE, expected_prefab_token=self.token(),
                                   expected_scene_roots_token=T)
        ok(self, stale_roots, "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        self.assertFalse(stale_roots.mutation_performed)

    def test_08_every_edit_goes_through_the_isolated_copy(self):
        s = self.s
        self.save()
        undo = self.human(op="undo-name")["group"]
        before = self.objects(s["plain"])
        # set-prefab-gameobject: the second "Child" only (two children share the name)
        r = self.edit(P.SET_GAMEOBJECT, object=s["plain_child2"], name="Renamed", layer="4")
        d = ok(self, r, "LIVE_PREFAB_SAVED")
        self.assertEqual((d["object"]["name"], d["file_changed"], d["value_persisted"], d["undoable"], d["unrequested_changes"]),
                         ("Renamed", True, True, False, []))
        after = self.objects(s["plain"])
        self.assertEqual((after[s["plain_child"]]["name"], after[s["plain_child2"]]["name"], after[s["plain_child2"]]["layer"]),
                         ("Child", "Renamed", 4))
        self.assertEqual(set(after), set(before))                                                   # the same ids
        ok(self, self.edit(P.SET_GAMEOBJECT, object=s["plain"], name="Other"),
           "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)                                                  # never the root's name
        ok(self, self.edit(P.SET_GAMEOBJECT, object=s["plain"], active="false", tag="EditorOnly"),
           "LIVE_PREFAB_SAVED")
        root = self.objects(s["plain"])[s["plain"]]
        self.assertEqual((root["active_self"], root["tag"], root["name"]), (False, "EditorOnly", "Plain"))
        ok(self, self.edit(P.SET_GAMEOBJECT, object=s["plain"], active="true", tag="Untagged"),
           "LIVE_PREFAB_SAVED")
        # set-prefab-transform
        d = ok(self, self.edit(P.SET_TRANSFORM, object=s["plain_leaf"], local_position="[0, 1.5, 0]",
                                  local_rotation="[0, 0, 0.7071068, 0.7071068]"), "LIVE_PREFAB_SAVED")
        self.assertEqual(d["transform"]["local_position"], [0, 1.5, 0])
        self.assertEqual(self.objects(s["plain"])[s["plain_leaf"]]["transform"]["local_position"], [0, 1.5, 0])
        # add: the requested component and those its [RequireComponent] adds, with their final persistent ids
        digest = self.digest()
        d = ok(self, self.edit(P.ADD_COMPONENT, object=s["plain_leaf"], type_id="Assembly-CSharp::AuthorNeedsBox",
                                  expected_catalog_digest=digest), "LIVE_PREFAB_SAVED")
        added = [c["type"] for c in d["added_components"]]
        self.assertEqual(added, ["Assembly-CSharp::AuthorNeedsBox", "UnityEngine.PhysicsModule::UnityEngine.BoxCollider"])
        leaf = self.objects(s["plain"])[s["plain_leaf"]]
        self.assertEqual({c["id"] for c in leaf["components"]} - {c["id"] for c in before[s["plain_leaf"]]["components"]},
                         {c["id"] for c in d["added_components"]})
        needs, box = d["added_components"][0]["id"], d["added_components"][1]["id"]
        self.assertTrue(needs.startswith("GlobalObjectId_V1-1-") and needs.endswith("-0"))
        ok(self, self.edit(P.ADD_COMPONENT, object=s["plain_leaf"], type_id="Assembly-CSharp::AuthorSingle",
                              expected_catalog_digest=digest), "LIVE_PREFAB_SAVED")
        r = self.edit(P.ADD_COMPONENT, object=s["plain_leaf"], type_id="Assembly-CSharp::AuthorSingle",
                         expected_catalog_digest=digest)
        ok(self, r, "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)                                          # DisallowMultipleComponent
        self.assertFalse(r.mutation_performed)
        ok(self, self.run_cap(P.ADD_COMPONENT, object=s["plain"], type_id="Assembly-CSharp::AuthorSingle",
                              expected_prefab_token=self.token(), expected_catalog_digest="b" * 64), "LIVE_CATALOG_CHANGED", tdg.CONFLICT)
        ok(self, self.run_cap(P.ADD_COMPONENT, object=s["plain"], type_id="Assembly-CSharp::AuthorHidden",
                              expected_prefab_token=self.token(), expected_catalog_digest=digest), "LIVE_TYPE_NOT_IN_CATALOG",
           tdg.INVALID_REQUEST)
        # remove: never a required component, never a Transform
        r = self.edit(P.REMOVE_COMPONENT, component=box)
        ok(self, r, "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        ok(self, self.edit(P.REMOVE_COMPONENT, component=leaf["components"][0]["id"]),
           "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)
        d = ok(self, self.edit(P.REMOVE_COMPONENT, component=needs), "LIVE_PREFAB_SAVED")
        self.assertEqual(d["removed"], needs)
        ok(self, self.edit(P.REMOVE_COMPONENT, component=box), "LIVE_PREFAB_SAVED")
        self.assertNotIn(needs, {c["id"] for c in self.objects(s["plain"])[s["plain_leaf"]]["components"]})
        self.assertEqual(self.human(op="undo-name")["group"], undo)                                  # no Undo record

    def test_09_properties_and_references_including_a_real_sprite(self):
        s = self.s
        self.save()
        c = s["plain_refs"]
        ok(self, self.prop(c, "count", "int32", 11), "LIVE_PREFAB_SAVED")
        ok(self, self.prop(c, "title", "string", "hello"), "LIVE_PREFAB_SAVED")
        # a reference inside the same prefab, to an asset, to another prefab's root, and a Sprite
        d = ok(self, self.prop(c, "other", "object", s["plain_leaf"]), "LIVE_PREFAB_SAVED")
        self.assertEqual(d["property"]["value"], s["plain_leaf"])
        ok(self, self.prop(c, "mat", "object", s["other"]), "LIVE_PREFAB_SAVED")
        ok(self, self.prop(c, "prefab", "object", s["made"]), "LIVE_PREFAB_SAVED")
        d = ok(self, self.prop(c, "sprite", "object", s["hero_sprite"]), "LIVE_PREFAB_SAVED")
        self.assertEqual(d["property"]["value"], s["hero_sprite"])
        props = self.props(s["plain"], c)
        self.assertEqual({k: props[k]["value"] for k in ("count", "title", "other", "mat", "prefab", "sprite")},
                         {"count": 11, "title": "hello", "other": s["plain_leaf"], "mat": s["other"], "prefab": s["made"],
                          "sprite": s["hero_sprite"]})
        self.assertIn(f"guid: {s['hero_sprite'].split('-')[2]}", self.file("Assets/Prefabs/Plain.prefab").read_text())
        before = sha(self.file("Assets/Prefabs/Plain.prefab"))
        for path, value, code in (("tex", s["hero_sprite"], "LIVE_VALUE_INVALID"),        # a Sprite is not a Texture
                                  ("sprite", s["hero_texture"], "LIVE_VALUE_INVALID"),    # a Texture is not a Sprite
                                  ("box", s["made_kid"], "LIVE_ASSET_REFUSED"),           # another prefab's internal object
                                  ("other", s["outer_nested"], "LIVE_ASSET_REFUSED"),     # nested content of another prefab
                                  ("physics", s["base"], "LIVE_VALUE_INVALID")):          # the field's declared type
            with self.subTest(path):
                r = self.prop(c, path, "object", value)
                ok(self, r, code, tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)                                    # refused before any import
        ok(self, self.prop(c, "other", "object", SCENE_ID), "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST)
        ok(self, self.prop(c, "m_Script", "object", None), "LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST)
        ok(self, self.prop(c, "count", "string", "x"), "LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST)
        digest = self.digest()
        d = ok(self, self.edit(P.ADD_COMPONENT, object=s["plain"], type_id="Assembly-CSharp::AuthorProps",
                                  expected_catalog_digest=digest), "LIVE_PREFAB_SAVED")
        props_id = d["component"]["id"]
        for path, kind, value in (("arr", "int32", 1), ("list", "string", "x"), ("hidden", "int32", 1), ("shape", "object", None),
                                  ("m_Enabled", "int32", 1)):
            with self.subTest(path):
                ok(self, self.prop(props_id, path, kind, value), "LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST)
        ok(self, self.prop(props_id, "v3", "vector3", [1, 2, 3]), "LIVE_PREFAB_SAVED")
        ok(self, self.prop(props_id, "q", "quaternion", [0, 0, 0, 1]), "LIVE_PREFAB_SAVED")
        self.assertNotEqual(sha(self.file("Assets/Prefabs/Plain.prefab")), before)
        # the objects of other prefabs are refused by scope, a Scene id by grammar
        for key, target in (("variant", "variant_refs"), ("outer", "outer_nested_refs"), ("pkg", "pkg_prefab_box")):
            with self.subTest(key):
                r = self.run_cap(P.SET_PROPERTY, component=s[target], path="count", kind="int32", value=1, expected_prefab_token=T)
                ok(self, r, "LIVE_PREFAB_REFUSED", tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)
        ok(self, self.run_cap(P.SET_PROPERTY, component=s["instance_refs"], path="count", kind="int32", value=1,
                              expected_prefab_token=T), "LIVE_OBJECT_REFUSED", tdg.INVALID_REQUEST)
        # a stale token: refused after the targeted import, nothing written
        now = sha(self.file("Assets/Prefabs/Plain.prefab"))
        r = self.prop(c, "count", "int32", 12, token=T)
        ok(self, r, "LIVE_PREFAB_CONFLICT", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)
        self.assertEqual((r.data["import_performed"], r.data["value_persisted"]), (True, False))
        self.assertEqual(sha(self.file("Assets/Prefabs/Plain.prefab")), now)

    def test_10_a_saved_prefab_propagates_into_clean_scenes_and_says_so(self):
        """D2: a clean loaded Scene holding an instance may be affected and is reported; nothing is saved."""
        s = self.s
        self.save()
        self.assertFalse(self.dirty())
        scene_file = sha(self.file(SCENE))
        d = ok(self, self.prop(s["plain_refs"], "speed", "float32", 2.5), "LIVE_PREFAB_SAVED")
        instance_refs = ok(self, self.run_cap(au.PROPERTIES, component=s["instance_refs"], path_prefix="speed"))["properties"][0]
        self.assertEqual(instance_refs["value"], 2.5)                                     # the instance follows the prefab
        self.assertEqual(d["scenes_marked_dirty"], [SCENE] if self.dirty() else [])       # measured: Unity 6000.5 does not
        # project code that marks Scenes dirty on a prefab import: disclosed, never saved or cleaned by GPOS
        flag = self.file("Temp/gpos-dirty-scenes")
        flag.write_text("")
        try:
            r = self.prop(s["plain_refs"], "speed", "float32", 3.5)
        finally:
            flag.unlink()
        d = ok(self, r, "LIVE_PREFAB_SIDE_EFFECTS")
        self.assertEqual((d["scenes_marked_dirty"], d["unrequested_changes"]), ([SCENE], []))
        self.assertTrue(self.dirty())
        self.assertEqual(sha(self.file(SCENE)), scene_file)                               # nothing was saved

    def test_11_detach(self):
        ok(self, self.lab.run(live.DETACH), "LIVE_SESSION_DETACHED")
        self.lab.editor.stop()
        self.assertEqual((self.leftovers(), self.records()), ([], []))
        self.assertEqual(bi.inspect_target(self.lab.game, bi.verify_source()), (bi.EXACT, []))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R6_RealPrefabGuards(RealPrefabs):
    """Every guard of prefab mutation against what a Human, another program or project code does meanwhile."""

    @classmethod
    def setUpClass(cls):
        cls.start("guards")
        cls.lab.human(op="save-open-scenes")

    def setUp(self):
        self.human(op="stage-reset")                    # no test inherits a Prefab Mode stage from another

    def probe_edit(self, save=True):
        if save:
            self.save()
        return self.run_cap(P.SET_GAMEOBJECT, object=self.s["probe"], layer="5", expected_prefab_token=self.token(self.s["probe"]))

    def test_01_prefab_mode_blocks_every_prefab_mutation_and_keeps_the_humans_work(self):
        s = self.s
        plain = sha(self.file("Assets/Prefabs/Plain.prefab"))
        probe_token = self.token(s["probe"])
        plain_token = self.token()
        roots = self.roots()
        subtree = self.subtree(s["source_kid2"])
        self.assertIs(self.human(op="stage-open", path="Assets/Prefabs/Plain.prefab")["open"], True)
        self.assertIs(self.human(op="stage-edit", value="2")["dirty"], True)                      # the Human's unsaved work
        state = self.human(op="stage-state")
        self.assertEqual((state["main"], state["prefab"], state["dirty"]), (False, "Assets/Prefabs/Plain.prefab", True))
        calls = ((P.SET_GAMEOBJECT, {"object": s["probe"], "layer": "5", "expected_prefab_token": probe_token}),   # another prefab
                 (P.SET_PROPERTY, {"component": s["plain_refs"], "path": "count", "kind": "int32", "value": 4,
                                   "expected_prefab_token": plain_token}),
                 (P.INSTANTIATE, {"prefab": s["probe"], "scene": SCENE, "expected_prefab_token": probe_token,
                                  "expected_scene_roots_token": roots}),
                 (P.CREATE_PREFAB, {"source": s["source_kid2"], "path": "Assets/Made/K.prefab", "expected_subtree_token": subtree}))
        probe = sha(self.file("Assets/Prefabs/Probe.prefab"))
        for cap, inputs in calls:
            with self.subTest(cap):
                r = self.run_cap(cap, **inputs)
                ok(self, r, "LIVE_PREFAB_STAGE_OPEN", tdg.CONFLICT)
                self.assertFalse(r.mutation_performed)
        # GPOS wrote nothing; the Human's edit survives — still unsaved in the stage, or saved by Unity's own
        # Prefab Mode auto-save (the Human's setting), never discarded or overwritten
        self.assertEqual(sha(self.file("Assets/Prefabs/Probe.prefab")), probe)
        self.assertFalse(self.file("Assets/Made/K.prefab").exists())
        state = self.human(op="stage-state")
        text = self.file("Assets/Prefabs/Plain.prefab").read_text()
        self.assertNotIn("count: 4", text)
        if state["dirty"]:
            self.assertEqual(sha(self.file("Assets/Prefabs/Plain.prefab")), plain)
        else:
            self.assertIs(state["auto_save"], True)
            self.assertIn("m_LocalScale: {x: 2, y: 1, z: 1}", text)
        d = self.inspect(s["probe"])
        self.assertEqual((d["stage_open"], d["mutable"], "STAGE_OPEN" in d["scope_reasons"]), (True, False, True))
        self.human(op="stage-save")
        self.human(op="stage-main")
        state = self.human(op="stage-state")
        self.assertEqual((state["main"], state["prefab"], state["history"]), (True, None, 1))
        ok(self, self.probe_edit(), "LIVE_PREFAB_SAVED")

    def test_02_a_nested_prefab_opened_in_context_and_the_breadcrumb(self):
        """Correction 5: the main-stage rule detects every Prefab Mode state of this Unity version."""
        s = self.s
        self.human(op="stage-open", path="Assets/Prefabs/Outer.prefab")
        self.assertIs(self.human(op="stage-open-in-context", path="Assets/Prefabs/Plain.prefab", name="Plain")["open"], True)
        state = self.human(op="stage-state")
        self.assertEqual((state["main"], state["prefab"]), (False, "Assets/Prefabs/Plain.prefab"))
        self.assertEqual(state["history"], 3)                                                      # Scenes > Outer > Plain
        r = self.probe_edit()
        ok(self, r, "LIVE_PREFAB_STAGE_OPEN", tdg.CONFLICT)
        self.human(op="stage-back")                                                                      # the breadcrumb
        state = self.human(op="stage-state")
        self.assertEqual((state["main"], state["prefab"], state["history"]), (False, "Assets/Prefabs/Outer.prefab", 2))
        ok(self, self.probe_edit(), "LIVE_PREFAB_STAGE_OPEN", tdg.CONFLICT)
        self.human(op="stage-main")
        state = self.human(op="stage-state")
        self.assertEqual((state["main"], state["prefab"], state["history"]), (True, None, 1))            # no prefab stage remains
        ok(self, self.probe_edit(), "LIVE_PREFAB_SAVED")

    def test_03_a_dirty_prefab_is_never_edited_or_saved(self):
        s = self.s
        token = self.token()
        self.human(op="set-serialized", target=s["plain_refs"], path="count", kind="int", value="42")
        r = self.prop(s["plain_refs"], "speed", "float32", 1, token=token, save=False)
        ok(self, r, "LIVE_PREFAB_DIRTY", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        self.assertIs(self.human(op="dirty", target=s["plain_refs"])["dirty"], True)                    # the Human's edit is kept
        self.assertNotIn("count: 42", self.file("Assets/Prefabs/Plain.prefab").read_text())             # and never saved by GPOS
        self.human(op="save-asset", target=s["plain"])
        # the dirty flag alone (no value changed) is covered by the token
        clean = self.inspect(s["probe"])
        self.human(op="mark-dirty", target=s["probe_component"])
        dirty = self.inspect(s["probe"])
        self.assertEqual((clean["dirty"], dirty["dirty"]), (False, True))
        self.assertNotEqual(dirty["tokens"]["prefab"], clean["tokens"]["prefab"])
        self.human(op="save-asset", target=s["probe"])

    def test_04_version_control_and_os_permissions(self):
        s = self.s
        self.human(op="vcs", value="true")
        try:
            r = self.probe_edit()
            ok(self, r, "LIVE_PREFAB_NOT_EDITABLE", tdg.CONFLICT)
            self.assertFalse(r.mutation_performed)
            ok(self, self.create(s["source_kid2"], "Assets/Made/V.prefab"), "LIVE_PREFAB_NOT_EDITABLE", tdg.CONFLICT)
            self.assertIn("VERSION_CONTROL", self.inspect(s["probe"])["scope_reasons"])
        finally:
            self.human(op="vcs", value="false")
        for rel in ("Assets/Prefabs/Probe.prefab", "Assets/Prefabs/Probe.prefab.meta", "Assets/Prefabs"):
            with self.subTest(rel):
                path = self.file(rel)
                mode = path.stat().st_mode
                before = sha(self.file("Assets/Prefabs/Probe.prefab"))
                os.chmod(path, 0o555 if path.is_dir() else 0o444)
                try:
                    r = self.probe_edit()
                    ok(self, r, "LIVE_PREFAB_NOT_EDITABLE", tdg.CONFLICT)
                    self.assertFalse(r.mutation_performed)
                    self.assertEqual(sha(self.file("Assets/Prefabs/Probe.prefab")), before)
                finally:
                    os.chmod(path, mode)
        ok(self, self.probe_edit(), "LIVE_PREFAB_SAVED")

    def test_05_a_file_changed_before_the_save_is_never_overwritten(self):
        s = self.s
        self.human(op="arm", step="pre-save", action="append", text="\n")
        r = self.prop(s["probe_component"], "value", "int32", 77, prefab=s["probe"])
        ok(self, r, "LIVE_PREFAB_CONFLICT", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)
        self.assertEqual((r.data["import_performed"], r.data["value_persisted"]), (True, False))
        text = self.file("Assets/Prefabs/Probe.prefab").read_text()
        self.assertTrue(text.endswith("\n\n"))                                                       # the external bytes are kept
        self.assertNotIn("value: 77", text)
        self.human(op="import", path="Assets/Prefabs/Probe.prefab")

    def test_06_an_unimported_external_change_is_found_by_the_targeted_import(self):
        s = self.s
        token = self.token()
        count = self.human(op="instance-count", path="Assets/Prefabs/Plain.prefab", scene=SCENE)["count"]
        path = self.file("Assets/Prefabs/Plain.prefab")
        text = path.read_text()
        self.assertIn("title: t\n", text)
        path.write_text(text.replace("title: t\n", "title: external\n"))                              # another program
        r = self.prop(s["plain_refs"], "speed", "float32", 1, token=token)
        ok(self, r, "LIVE_PREFAB_CONFLICT", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)
        self.assertIn("title: external\n", path.read_text())
        path.write_text(path.read_text().replace("title: external\n", "title: external2\n"))
        r = self.instantiate(s["plain"], token=self.inspect(s["plain"])["tokens"]["prefab"])            # read before the import
        ok(self, r, "LIVE_PREFAB_CONFLICT", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)                                                      # correction 3
        self.assertEqual(self.human(op="instance-count", path="Assets/Prefabs/Plain.prefab", scene=SCENE)["count"], count)
        self.save()

    def test_07_uncertain_persistence_is_outcome_unknown(self):
        s = self.s
        self.save()
        self.human(op="arm", step="saved", action="throw")
        r = self.prop(s["probe_component"], "value", "int32", 5, prefab=s["probe"])
        ok(self, r, "LIVE_OUTCOME_UNKNOWN", tdg.OUTCOME_UNKNOWN)
        self.assertTrue(r.mutation_performed)
        self.assertTrue(r.data["commit_started"])
        self.assertEqual(self.props(s["probe"], s["probe_component"])["value"]["value"], 5)           # it was written; never retried

    def test_08_a_dirty_dependent_scene_blocks_the_edit_an_unrelated_one_does_not(self):
        """D2: before the import, a dirty loaded Scene holding a dependent instance refuses the edit."""
        s = self.s
        self.save()
        self.human(op="mark-scene-dirty", scene=SCENE)                     # Main holds Plain and Outer (which nests Plain)
        before = sha(self.file("Assets/Prefabs/Plain.prefab"))
        r = self.prop(s["plain_refs"], "speed", "float32", 9, save=False)
        ok(self, r, "LIVE_PREFAB_CONFLICT", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        self.assertEqual(r.data["dependent_scenes_dirty"], [SCENE])
        self.assertEqual(sha(self.file("Assets/Prefabs/Plain.prefab")), before)
        self.assertTrue(self.dirty())                                      # never saved or cleaned by GPOS
        ok(self, self.probe_edit(save=False), "LIVE_PREFAB_SAVED")          # Probe has no instance in Main: unrelated
        self.assertTrue(self.dirty())
        self.save()
        other = self.human(op="second-scene", scene="Assets/Scenes/Unrelated.unity", name="U")["scene"]
        self.human(op="mark-scene-dirty", scene=other)
        d = ok(self, self.prop(s["plain_refs"], "speed", "float32", 9, save=False), "LIVE_PREFAB_SAVED")   # an unrelated dirty Scene
        self.assertNotIn(other, d["scenes_marked_dirty"])                                          # it was dirty already
        self.assertTrue(self.dirty(other))
        self.human(op="save-open-scenes")
        self.human(op="close-scene", scene=other)

    def test_09_the_dependent_scene_scan_is_bounded(self):
        s = self.s
        self.save()
        self.human(op="many-objects", count=100001)
        try:
            r = self.probe_edit(save=False)
            ok(self, r, "LIVE_PREFAB_LIMIT", tdg.INVALID_REQUEST)
            self.assertFalse(r.mutation_performed)
        finally:
            self.human(op="close-unsaved")
        ok(self, self.probe_edit(), "LIVE_PREFAB_SAVED")

    def test_10_project_callbacks_are_disclosed(self):
        s = self.s
        self.save()
        flag = self.file("Temp/gpos-probe-stamp")
        flag.write_text("")                    # project code that changes the prefab on every import, the targeted one too
        try:
            before = sha(self.file("Assets/Prefabs/Probe.prefab"))
            r = self.probe_edit()
            ok(self, r, "LIVE_PREFAB_CONFLICT", tdg.CONFLICT)                                      # the fresh token differs
            self.assertTrue(r.mutation_performed)
            self.assertEqual(sha(self.file("Assets/Prefabs/Probe.prefab")), before)
        finally:
            flag.unlink()
        self.human(op="import", path="Assets/Prefabs/Probe.prefab")          # the Human reimports: memory is the file again
        flag = self.file("Temp/gpos-probe-echo")
        flag.write_text("")                    # project OnValidate on the edited copy changes another component
        try:
            d = ok(self, self.prop(s["probe_component"], "echo", "int32", 13, prefab=s["probe"]), "LIVE_PREFAB_SIDE_EFFECTS")
            self.assertEqual((d["unrequested_changes"], d["unrequested_change_count"]), ([s["probe_refs"]], 1))
            self.assertEqual(self.props(s["probe"], s["probe_refs"])["count"]["value"], 13)
        finally:
            flag.unlink()
        flag = self.file("Temp/gpos-probe-validate")
        flag.write_text("")
        try:
            d = ok(self, self.instantiate(s["probe"]), "LIVE_PREFAB_INSTANTIATED")
            self.assertGreaterEqual(d["overrides_after"]["property_overrides"], 1)               # OnValidate made overrides
            self.assertIn("validations", {t["property_path"] for t in d["overrides_after"]["property_override_targets"]})
        finally:
            flag.unlink()
        self.save()

    def test_11_ids_and_tokens_survive_reload_reimport_and_a_move(self):
        s = self.s
        self.save()
        ids, token = set(self.objects(s["plain"])), self.token()
        generation = self.lab.editor.heartbeat().get("generation", 0)
        self.lab.editor.trigger("reload")
        self.assertTrue(self.lab.editor.wait(lambda: self.lab.editor.heartbeat().get("generation", 0) != generation, 240))
        self.wait_edit()
        self.assertEqual((set(self.objects(s["plain"])), self.token()), (ids, token))
        self.human(op="import", path="Assets/Prefabs/Plain.prefab")
        self.assertEqual((set(self.objects(s["plain"])), self.token()), (ids, token))
        self.assertEqual(self.human(op="move-asset", path="Assets/Prefabs/Plain.prefab", target="Assets/Made/Moved.prefab")["error"], "")
        d = self.inspect(s["plain"])
        self.assertEqual((set(o["id"] for o in d["objects"]), d["prefab"]["path"], d["objects"][0]["name"]),
                         (ids, "Assets/Made/Moved.prefab", "Moved"))
        self.assertNotEqual(d["tokens"]["prefab"], token)                                            # the path is covered
        ok(self, self.prop(s["plain_refs"], "speed", "float32", 4), "LIVE_PREFAB_SAVED")
        self.human(op="move-asset", path="Assets/Made/Moved.prefab", target="Assets/Prefabs/Plain.prefab")
        self.save()

    def test_12_detach(self):
        ok(self, self.lab.run(live.DETACH), "LIVE_SESSION_DETACHED")
        self.lab.editor.stop()
        self.assertEqual((self.leftovers(), self.records()), ([], []))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R7_RealPrefabRecovery(RealPrefabs):
    """The lab Editor process is stopped at exact steps (a real crash); after a restart the next creation recovers every
    earlier transaction from what is on disk, or refuses and touches nothing; an edit is never replayed."""

    @classmethod
    def setUpClass(cls):
        cls.start("prefab-recovery")
        cls.lab.human(op="save-open-scenes")

    def restart(self):
        self.lab.editor.proc.wait(120)
        self.lab.editor.launch()
        ok(self, self.lab.run(live.DETACH, timeout=120), "LIVE_SESSION_RECOVERED")
        self.lab.sid = ok(self, self.lab.run(live.ATTACH, timeout=120), "LIVE_SESSION_ATTACHED")["session_id"]
        self.human(op="reopen", scene=SCENE)                  # the Human opens the Scene again after the restart

    def crash_create(self, step, path):
        token = self.subtree(self.s["source_kid2"])
        self.human(op="arm", step=step, action="crash")
        r = self.run_cap(P.CREATE_PREFAB, source=self.s["source_kid2"], path=path, expected_subtree_token=token, timeout=20)
        ok(self, r, "LIVE_OUTCOME_UNKNOWN", tdg.OUTCOME_UNKNOWN)
        self.assertTrue(r.mutation_performed)
        self.restart()
        (record,) = self.records()
        return json.loads(record.read_text())

    def next_create(self, path):
        return self.create(self.s["source_kid2"], path)

    def test_01_a_crash_before_the_save_leaves_nothing(self):
        record = self.crash_create("prepared", "Assets/Made/C1.prefab")
        self.assertEqual((record["schema"], record["kind"], record["phase"], record["guid"]),
                         ("gpos.unity.live-bridge.asset-create-txn/2", "PREFAB", "PREPARED", None))
        r = self.next_create("Assets/Made/N1.prefab")
        ok(self, r, "LIVE_PREFAB_CREATE_RECOVERED")
        self.assertEqual([x["outcome"] for x in r.data["recovered"]], ["NOTHING_CREATED"])
        self.assertFalse(self.file("Assets/Made/C1.prefab").exists())
        self.assertEqual((self.leftovers(), self.records()), ([], []))

    def test_02_an_unproven_temporary_prefab_is_never_removed(self):
        record = self.crash_create("temp-created", "Assets/Made/C2.prefab")
        self.assertEqual(record["phase"], "SCRATCH_READY")
        (scratch,) = [n for n in self.leftovers() if not n.endswith(".meta")]
        files = {p.name: p.read_bytes() for p in self.file(f"Assets/{scratch}").iterdir()}
        self.assertEqual(sorted(files), ["C2.prefab", "C2.prefab.meta"])
        r = self.next_create("Assets/Made/N2.prefab")
        ok(self, r, "LIVE_PREFAB_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        self.assertEqual(r.data["state"]["temp"], "DIFFERENT")
        self.assertEqual({p.name: p.read_bytes() for p in self.file(f"Assets/{scratch}").iterdir()}, files)   # untouched
        r = self.run_cap(A.CREATE_MATERIAL, path="Assets/Materials/N2.mat", shader=self.s["shader"],
                         expected_shader_catalog_digest=ok(self, self.run_cap(A.ASSET_TYPES, catalog="SHADERS", query="GPOS/Test"))["shader_catalog_digest"])
        ok(self, r, "LIVE_PREFAB_CREATE_INCOMPLETE", tdg.CONFLICT)                                  # the record's kind decides
        shutil.rmtree(self.file(f"Assets/{scratch}"))
        self.file(f"Assets/{scratch}.meta").unlink()
        self.records()[0].unlink()
        self.lab.editor.trigger("refresh")
        time.sleep(3)
        self.wait_edit()
        ok(self, self.next_create("Assets/Made/N2.prefab"), "LIVE_PREFAB_CREATED")

    def test_03_an_exact_temporary_prefab_is_removed_a_modified_one_is_not(self):
        record = self.crash_create("temp-proven", "Assets/Made/C3.prefab")
        self.assertEqual(record["phase"], "TEMP_PROVEN")
        self.assertTrue(record["global_id"].startswith(f"GlobalObjectId_V1-1-{record['guid']}-"))
        (scratch,) = [n for n in self.leftovers() if not n.endswith(".meta")]
        temp = self.file(f"Assets/{scratch}/C3.prefab")
        self.assertEqual(sha(temp), record["temp_sha256"])
        original = temp.read_bytes()
        temp.write_bytes(original + b"\n")
        r = self.next_create("Assets/Made/N3.prefab")
        ok(self, r, "LIVE_PREFAB_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertEqual(temp.read_bytes(), original + b"\n")
        temp.write_bytes(original)
        r = self.next_create("Assets/Made/N3.prefab")
        ok(self, r, "LIVE_PREFAB_CREATE_RECOVERED")
        self.assertEqual([x["outcome"] for x in r.data["recovered"]], ["TEMP_REMOVED"])
        self.assertFalse(self.file("Assets/Made/C3.prefab").exists())                                # never replayed
        self.assertEqual((self.leftovers(), self.records()), ([], []))

    def test_04_an_exact_final_prefab_is_kept_a_modified_one_is_untouched(self):
        record = self.crash_create("moved", "Assets/Made/C4.prefab")
        final = self.file("Assets/Made/C4.prefab")
        self.assertEqual(sha(final), record["temp_sha256"])                                          # the move changed no byte
        original = final.read_bytes()
        final.write_bytes(original + b"\n")
        r = self.next_create("Assets/Made/N4.prefab")
        ok(self, r, "LIVE_PREFAB_CREATE_INCOMPLETE", tdg.CONFLICT)
        self.assertEqual(r.data["state"]["final"], "OURS_DIFFERENT")
        final.write_bytes(original)
        r = self.next_create("Assets/Made/N4.prefab")
        ok(self, r, "LIVE_PREFAB_CREATE_RECOVERED")
        self.assertEqual([x["outcome"] for x in r.data["recovered"]], ["FINAL_KEPT"])
        self.assertEqual(self.human(op="id-of", path="Assets/Made/C4.prefab")["id"], record["global_id"])
        self.assertEqual(self.inspect(record["global_id"])["prefab"]["type"], "REGULAR")
        self.assertEqual((self.leftovers(), self.records()), ([], []))

    def test_05_every_creating_command_recovers_every_record_kind(self):
        from gpos.tools.unity import identity as ident
        key = ident.project_key(ident.relative(self.lab.p, self.lab.game))
        d = self.lab.p / ".game" / "gpos-runtime" / "unity" / "asset-create-txn" / key
        d.mkdir(parents=True, exist_ok=True)
        material = {"schema": "gpos.unity.live-bridge.asset-create-txn/1", "txn_id": "e" * 32, "project_key": key,
                    "session_id": "1" * 32, "request_id": "2" * 32, "owner": "AGENT:x", "kind": "MATERIAL",
                    "final_path": "Assets/Materials/Old.mat", "phase": "PREPARED", "scratch_meta_sha256": None,
                    "guid": None, "global_id": None, "type": None, "temp_sha256": None, "temp_meta_sha256": None,
                    "final_sha256": None, "final_meta_sha256": None, "started_utc": "2026-09-27T00:00:00.0000000Z"}
        (d / f"{'e' * 32}.json").write_text(json.dumps(material))                                  # an alpha.18 record
        r = self.next_create("Assets/Made/N5.prefab")
        ok(self, r, "LIVE_ASSET_CREATE_RECOVERED")
        self.assertEqual([(x["kind"], x["outcome"]) for x in r.data["recovered"]], [("MATERIAL", "NOTHING_CREATED")])
        prefab = dict(material, schema="gpos.unity.live-bridge.asset-create-txn/2", kind="PREFAB", txn_id="f" * 32,
                      final_path="Assets/Made/Old.prefab")
        (d / f"{'f' * 32}.json").write_text(json.dumps(prefab))
        shader = ok(self, self.run_cap(A.ASSET_TYPES, catalog="SHADERS", query="GPOS/Test"))["shader_catalog_digest"]
        r = self.run_cap(A.CREATE_MATERIAL, path="Assets/Materials/N5.mat", shader=self.s["shader"], expected_shader_catalog_digest=shader)
        ok(self, r, "LIVE_PREFAB_CREATE_RECOVERED")
        for bad in (dict(prefab, schema="gpos.unity.live-bridge.asset-create-txn/1"), dict(material, schema="gpos.unity.live-bridge.asset-create-txn/2"),
                    dict(prefab, final_path="Assets/Made/Old.mat")):
            with self.subTest(bad["schema"] + bad["kind"] + bad["final_path"]):
                (d / f"{'f' * 32}.json").write_text(json.dumps(bad))
                r = self.next_create("Assets/Made/N6.prefab")
                ok(self, r, "LIVE_ASSET_CREATE_INCOMPLETE", tdg.CONFLICT)                             # an untrusted record
                self.assertFalse(r.mutation_performed)
        (d / f"{'f' * 32}.json").unlink()

    def test_06_a_crash_during_an_edit_before_the_save_changes_nothing(self):
        s = self.s
        before = sha(self.file("Assets/Prefabs/Probe.prefab"))
        self.human(op="arm", step="loaded", action="crash")
        r = self.prop(s["probe_component"], "value", "int32", 61, prefab=s["probe"], timeout=20)
        ok(self, r, "LIVE_OUTCOME_UNKNOWN", tdg.OUTCOME_UNKNOWN)
        self.restart()
        self.assertEqual(sha(self.file("Assets/Prefabs/Probe.prefab")), before)
        self.assertEqual(self.props(s["probe"], s["probe_component"])["value"]["value"], 0)

    def test_07_a_crash_after_the_save_is_outcome_unknown_and_never_replayed(self):
        s = self.s
        self.human(op="arm", step="saved", action="crash")
        r = self.prop(s["probe_component"], "value", "int32", 62, prefab=s["probe"], timeout=20)
        ok(self, r, "LIVE_OUTCOME_UNKNOWN", tdg.OUTCOME_UNKNOWN)
        self.assertTrue(r.mutation_performed)
        self.restart()
        self.assertEqual(self.props(s["probe"], s["probe_component"])["value"]["value"], 62)        # it was written
        written = sha(self.file("Assets/Prefabs/Probe.prefab"))
        ok(self, self.lab.run(live.STATUS))
        time.sleep(2)
        self.assertEqual(sha(self.file("Assets/Prefabs/Probe.prefab")), written)                   # and never replayed

    def test_08_a_destination_taken_right_before_the_move_is_never_overwritten(self):
        self.human(op="arm", step="pre-move", action="write", path="Assets/Made/Taken.prefab", text="someone else's file\n")
        r = self.next_create("Assets/Made/Taken.prefab")
        ok(self, r, "LIVE_ASSET_EXISTS", tdg.CONFLICT)
        self.assertTrue(r.mutation_performed)
        self.assertEqual(r.data["compensated"], "TEMP_REMOVED")                                    # only GPOS's own temp
        self.assertEqual(self.file("Assets/Made/Taken.prefab").read_text(), "someone else's file\n")
        self.assertEqual((self.leftovers(), self.records()), ([], []))
        self.file("Assets/Made/Taken.prefab").unlink()

    def test_09_detach(self):
        ok(self, self.lab.run(live.DETACH), "LIVE_SESSION_DETACHED")
        self.lab.editor.stop()
        self.assertEqual((self.leftovers(), self.records()), ([], []))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R8_RealUpgrade(unittest.TestCase):
    def test_a_running_1_2_0_bridge_is_upgraded_only_while_the_project_is_closed(self):
        lab = ta.Lab(prefab_project("upgrade-12", install=False))
        ta.frozen_package(lab.game / "Packages" / bi.PACKAGE_ID, ta.FROZEN_TAG_12)
        self.assertEqual(bi.installed_version(lab.game), "1.2.0")
        b = lab.editor.launch()
        self.assertEqual((b["bridge_version"], b["protocol"], b["package_digest"]),
                         ("1.2.0", "gpos.unity.live/3", ta.FROZEN_DIGEST_12))
        ok(self, lab.run(live.ATTACH, timeout=30), "LIVE_BRIDGE_INCOMPATIBLE", tdg.INCOMPATIBLE)
        ok(self, lab.run(live.INSTALL), "ENGINE_PROJECT_LOCKED", tdg.CONFLICT)                       # no hot upgrade
        self.assertEqual(bi.installed_version(lab.game), "1.2.0")
        lab.editor.stop()
        data = ok(self, lab.run(live.INSTALL), "LIVE_BRIDGE_UPGRADED")
        self.assertEqual(data["upgraded_from"], "1.2.0")
        b = lab.editor.launch()
        self.assertEqual((b["bridge_version"], b["protocol"], b["package_digest"]),
                         (bi.BRIDGE_VERSION, bi.PROTOCOL, bi.verify_source()["package_digest"]))
        lab.sid = ok(self, lab.run(live.ATTACH, timeout=120), "LIVE_SESSION_ATTACHED")["session_id"]
        s = {}
        for op in ("setup", "setup-assets", "setup-prefabs"):
            s.update(lab.human(op=op))
        ok(self, lab.run(P.PREFAB_INSPECT, prefab=s["plain"]))                                       # the 1.3.0 commands answer
        ok(self, lab.run(live.DETACH), "LIVE_SESSION_DETACHED")
        lab.editor.stop()
        self.assertEqual(bi.inspect_target(lab.game, bi.verify_source()), (bi.EXACT, []))


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
