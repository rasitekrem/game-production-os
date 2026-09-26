#!/usr/bin/env python3
"""Phase 2C-6B1 — Unity live Scene authoring and the bridge upgrade (alpha.17).

    python3 tests/test_unity_authoring.py

Fast groups need no Unity. A–C drive the GPOS side against `unity_live_fake_bridge.FakeBridge` (a stand-in that
speaks the file protocol; it proves GPOS behaviour only): declarations, the input grammar, the exact arguments GPOS
sends and how every bridge answer maps to a result. D is the crash-recoverable bridge upgrade and its recovery
matrix, including the invariant that the pinned 1.0.0 history manifest is byte for byte the one frozen in tag
v1.0.0-alpha.16. E checks the bridge and module sources for forbidden mechanisms. The bridge's own authoring
logic (grammars, tokens, catalog digest, property rules, busy rule, protocol) is covered by
tests/test_unity_live_bridge_core.py.

Real groups open disposable synthetic Unity projects in lab-owned batch-mode Editors activated by the test-only
testkit, which also stands in for the Human (Inspector edits through SerializedObject, Hierarchy drags, Cmd-Z) —
never the Human's Editor, never a user project. R1 is one authoring session end to end; R2 upgrades a real 1.0.0
bridge (extracted from the frozen tag) in a closed project and attaches to the upgraded bridge. The real groups
stop with UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A unless exactly one Hub Editor is installed, and guard Unity's
EditorPrefs (by key and value hash) and the user's Package Manager configuration files.

GPOS_UNITY_TEST_FAST=1 (the mutation harness only) skips every group that starts a real Unity process.
GPOS_SOURCE_GIT_DIR names the repository's .git when the tests run in a copy without one (the mutation harness).
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
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
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools.model import Actor  # noqa: E402
from gpos.tools.unity import UnityAdapter  # noqa: E402
from gpos.tools.unity import authoring as au  # noqa: E402
from gpos.tools.unity import bridge_install as bi  # noqa: E402
from gpos.tools.unity import live  # noqa: E402
from gpos.tools.unity import live_ipc as ipc  # noqa: E402
from test_unity_live import (AGENT, EDITOR, EDITOR_VERSION, EDITORS, FAST, FIXTURE, LiveCase,  # noqa: E402
                             published)

FROZEN_TAG = "v1.0.0-alpha.16"
FROZEN_DIGEST = "546b3cfbe4d41234d10450efacbb3397812d106a813dee5b3284903624a68c66"
ID = "GlobalObjectId_V1-2-0123456789abcdef0123456789abcdef-{}-0"
T = "0" * 31 + "1"
T2 = "0" * 31 + "2"
DIGEST = "a" * 64
SCENE = "Assets/Scenes/Main.unity"


def git_dir():
    return Path(os.environ.get("GPOS_SOURCE_GIT_DIR") or ROOT / ".git")


def git(*args, binary=False):
    out = subprocess.run(["git", f"--git-dir={git_dir()}", *args], capture_output=True, timeout=120)
    if out.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {out.stderr.decode(errors='replace')}")
    return out.stdout if binary else out.stdout.decode()


def frozen_package(dest):
    """Write the bridge package exactly as tag v1.0.0-alpha.16 froze it to `dest` (created)."""
    prefix = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/"
    data = git("archive", "--format=tar", FROZEN_TAG, prefix, binary=True)
    dest = Path(dest)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if not member.name.startswith(prefix):
                continue
            rel = member.name[len(prefix):]
            if not rel:
                continue
            if member.isdir():
                (dest / rel).mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest / rel).write_bytes(tar.extractfile(member).read())
    dest.mkdir(parents=True, exist_ok=True)
    return dest


# ---------------------------------------------------------------- A  declarations

class A_Declarations(unittest.TestCase):
    def test_twelve_fixed_capabilities(self):
        caps = {c.id: c for c in UnityAdapter.descriptor.capabilities}
        self.assertEqual(len(caps), 24)
        self.assertEqual(set(au.CAPABILITY_IDS), {c for c in caps if c in au.COMMANDS})
        self.assertEqual(len(au.CAPABILITY_IDS), 12)
        for cid in au.CAPABILITY_IDS:
            c = caps[cid]
            read_only = cid in au.READ_ONLY
            with self.subTest(cid):
                self.assertEqual((c.category, c.operation_class, c.state_model, c.effective_lease_mode,
                                  c.execution_context, c.resource_kind, c.requires_tool),
                                 ("INSPECT" if read_only else "TRANSFORM", "READ_ONLY" if read_only else "MUTATING",
                                  "STATEFUL", "SESSION_REQUIRED", "EDITOR", "EDITOR_PROJECT", False))
                self.assertEqual((c.potential_evidence, c.artifact_kinds, c.dry_run_supported), ((), (), False))
                self.assertEqual(c.input_kinds, ("unity_project",) + tuple(au.INPUTS[cid]))
                self.assertEqual(c.single_writer_required, not read_only)
                self.assertEqual(c.side_effect_scope == "NONE", read_only)
                expected = (60.0, 300.0) if cid == au.SAVE_SCENE else (30.0, 120.0)
                self.assertEqual((c.timeout.default, c.timeout.maximum), expected)
        self.assertIn("never Save As", caps[au.SAVE_SCENE].description)
        self.assertIn("not evidence", au.LIMITATION)

    def test_no_generic_surface(self):
        names = set()
        for cid in au.CAPABILITY_IDS:
            names |= set(au.INPUTS[cid])
        for word in ("method", "menu", "command", "script", "assembly", "executable", "url", "file", "asset",
                     "prefab", "code", "eval", "reflect", "instance", "entity", "hierarchy_path", "save_as"):
            self.assertFalse([n for n in names if word in n], word)
        self.assertEqual(set(au.KINDS), {"bool", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32",
                                         "uint64", "float32", "float64", "string", "enum", "vector2", "vector3",
                                         "vector4", "vector2int", "vector3int", "rect", "rectint", "bounds",
                                         "boundsint", "color", "quaternion", "layermask", "object"})


# ---------------------------------------------------------------- B  input grammar

class B_Inputs(unittest.TestCase):
    def refused(self, cap, inputs, code):
        with self.assertRaises(au.InputProblem) as ctx:
            au.parse_inputs(cap, inputs)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))

    def test_object_ids(self):
        self.assertEqual(au.parse_inputs(au.DELETE, {"object": ID.format(7), "expected_subtree_token": T})["object"],
                         ID.format(7))
        for bad, code in ((ID.format(7) + "\n", "LIVE_OBJECT_REFUSED"),
                          (ID.format(7).replace("V1-2-", "V1-1-"), "LIVE_OBJECT_REFUSED"),
                          ("GlobalObjectId_V1-0-" + "0" * 32 + "-0-0", "LIVE_SCENE_NOT_SAVED"),
                          ("GlobalObjectId_V1-2-" + "0" * 32 + "-5-0", "LIVE_SCENE_NOT_SAVED"),
                          (ID.format(2 ** 64), "LIVE_OBJECT_REFUSED"), ("12345", "LIVE_OBJECT_REFUSED"),
                          ("World/A/B", "LIVE_OBJECT_REFUSED"), (ID.format(7).upper(), "LIVE_OBJECT_REFUSED")):
            with self.subTest(bad):
                self.refused(au.DELETE, {"object": bad, "expected_subtree_token": T}, code)

    def test_scenes_tokens_and_types(self):
        self.refused(au.SAVE_SCENE, {"scene": ""}, "LIVE_SCENE_NOT_SAVED")
        for bad in ("Packages/p/Main.unity", "Assets/../Main.unity", "Assets/Main.unity\n", "Assets//M.unity",
                    "/Assets/Main.unity", "Assets/Main.prefab", "Assets/" + "x" * 520 + ".unity"):
            with self.subTest(bad):
                self.refused(au.SAVE_SCENE, {"scene": bad}, "INVALID_TOOL_REQUEST")
        self.refused(au.DELETE, {"object": ID.format(1), "expected_subtree_token": T + "\n"}, "INVALID_TOOL_REQUEST")
        self.refused(au.DELETE, {"object": ID.format(1), "expected_subtree_token": "ABCDEF" + T[6:]}, "INVALID_TOOL_REQUEST")
        add = {"object": ID.format(1), "expected_object_token": T, "expected_catalog_digest": DIGEST}
        self.assertEqual(au.parse_inputs(au.ADD_COMPONENT, dict(add, type_id="A::B+C"))["type_id"], "A::B+C")
        for bad in ("BoxCollider", "A::List`1", "A::B\n", "::B"):
            self.refused(au.ADD_COMPONENT, dict(add, type_id=bad), "LIVE_TYPE_NOT_IN_CATALOG")
        self.refused(au.ADD_COMPONENT, dict(add, type_id="A::B", expected_catalog_digest=T), "INVALID_TOOL_REQUEST")

    def test_required_and_exclusive_inputs(self):
        self.refused(au.DELETE, {"object": ID.format(1)}, "INVALID_TOOL_REQUEST")
        self.refused(au.INSPECT_OBJECT, {}, "INVALID_TOOL_REQUEST")
        self.refused(au.INSPECT_OBJECT, {"object": ID.format(1), "scene": SCENE}, "INVALID_TOOL_REQUEST")
        self.refused(au.CREATE, {"scene": SCENE, "name": "A"}, "INVALID_TOOL_REQUEST")
        self.refused(au.CREATE, {"scene": SCENE, "name": "A", "parent": ID.format(1)}, "INVALID_TOOL_REQUEST")
        self.refused(au.CREATE, {"scene": SCENE, "name": "A", "parent": ID.format(1),
                                 "expected_scene_roots_token": T}, "INVALID_TOOL_REQUEST")
        self.refused(au.CREATE, {"scene": SCENE, "name": "A", "expected_scene_roots_token": T,
                                 "expected_parent_token": T}, "INVALID_TOOL_REQUEST")
        self.refused(au.CREATE, {"scene": SCENE, "name": "a\nb", "expected_scene_roots_token": T}, "LIVE_VALUE_INVALID")
        self.refused(au.CREATE, {"scene": SCENE, "name": "x" * 129, "expected_scene_roots_token": T},
                     "LIVE_VALUE_INVALID")
        self.refused(au.SET_GAMEOBJECT, {"object": ID.format(1), "expected_object_token": T}, "INVALID_TOOL_REQUEST")
        self.refused(au.SET_TRANSFORM, {"object": ID.format(1), "expected_transform_token": T}, "INVALID_TOOL_REQUEST")
        self.refused(au.SET_GAMEOBJECT, {"object": ID.format(1), "expected_object_token": T, "layer": "32"},
                     "INVALID_TOOL_REQUEST")
        self.refused(au.SET_GAMEOBJECT, {"object": ID.format(1), "expected_object_token": T, "active": "yes"},
                     "INVALID_TOOL_REQUEST")
        args = au.parse_inputs(au.SET_GAMEOBJECT, {"object": ID.format(1), "expected_object_token": T, "active": "false"})
        self.assertEqual(args, {"object": ID.format(1), "name": None, "active": False, "tag": None, "layer": None,
                                "static_flags": None, "expected_object_token": T})

    def test_set_parent_token_rules(self):
        base = {"object": ID.format(1), "expected_object_token": T, "expected_transform_token": T}
        old_root = {"expected_old_scene_roots_token": T}
        ok = dict(base, parent=ID.format(2), keep_world="false", expected_new_parent_token=T, **old_root)
        self.assertIsNone(au.parse_inputs(au.SET_PARENT, ok)["expected_transform_chain_token"])
        to_root = dict(base, parent="", keep_world="false", expected_new_scene_roots_token=T, **old_root)
        self.assertIsNone(au.parse_inputs(au.SET_PARENT, to_root)["parent"])
        self.refused(au.SET_PARENT, dict(ok, expected_old_parent_token=T), "INVALID_TOOL_REQUEST")
        self.refused(au.SET_PARENT, {k: v for k, v in ok.items() if k != "expected_old_scene_roots_token"},
                     "INVALID_TOOL_REQUEST")
        self.refused(au.SET_PARENT, dict(ok, expected_new_scene_roots_token=T), "INVALID_TOOL_REQUEST")
        self.refused(au.SET_PARENT, dict(to_root, expected_new_parent_token=T), "INVALID_TOOL_REQUEST")
        # keep_world needs the target's transform chain and, under a parent, the new parent's chain
        self.refused(au.SET_PARENT, dict(ok, keep_world="true"), "INVALID_TOOL_REQUEST")
        self.refused(au.SET_PARENT, dict(ok, keep_world="true", expected_transform_chain_token=T),
                     "INVALID_TOOL_REQUEST")
        world = au.parse_inputs(au.SET_PARENT, dict(ok, keep_world="true", expected_transform_chain_token=T,
                                                    expected_new_parent_chain_token=T2))
        self.assertEqual((world["expected_transform_chain_token"], world["expected_new_parent_chain_token"]), (T, T2))
        au.parse_inputs(au.SET_PARENT, dict(to_root, keep_world="true", expected_transform_chain_token=T))
        self.refused(au.SET_PARENT, dict(to_root, keep_world="true"), "INVALID_TOOL_REQUEST")
        self.refused(au.SET_PARENT, dict(to_root, keep_world="true", expected_transform_chain_token=T,
                                         expected_new_parent_chain_token=T), "INVALID_TOOL_REQUEST")
        # keep_world=false never takes chain tokens: a local move does not depend on ancestor transforms
        self.refused(au.SET_PARENT, dict(ok, expected_transform_chain_token=T), "INVALID_TOOL_REQUEST")
        self.refused(au.SET_PARENT, dict(ok, expected_new_parent_chain_token=T), "INVALID_TOOL_REQUEST")

    def test_every_identifier_matches_the_whole_string(self):
        """A value that is valid but ends in LF or CRLF is refused by every authoring and upgrade grammar."""
        valid = {
            (au.DELETE, "object"): ID.format(7), (au.DELETE, "expected_subtree_token"): T,
            (au.SAVE_SCENE, "scene"): SCENE,
            (au.ADD_COMPONENT, "type_id"): "A::B", (au.ADD_COMPONENT, "expected_catalog_digest"): DIGEST,
            (au.SET_PROPERTY, "path"): "nested.speed", (au.SET_PROPERTY, "kind"): "int32",
            (au.SET_GAMEOBJECT, "layer"): "3", (au.SET_GAMEOBJECT, "static_flags"): "4",
            (au.SET_GAMEOBJECT, "active"): "true", (au.COMPONENT_TYPES, "page"): "1",
            (au.SET_PARENT, "parent"): ID.format(8),
        }
        base = {au.DELETE: {"object": ID.format(7), "expected_subtree_token": T}, au.SAVE_SCENE: {"scene": SCENE},
                au.ADD_COMPONENT: {"object": ID.format(1), "type_id": "A::B", "expected_object_token": T,
                                   "expected_catalog_digest": DIGEST},
                au.SET_PROPERTY: {"component": ID.format(3), "path": "nested.speed", "kind": "int32", "value": "1",
                                  "expected_component_token": T},
                au.SET_GAMEOBJECT: {"object": ID.format(1), "expected_object_token": T, "layer": "3",
                                    "static_flags": "4", "active": "true"},
                au.COMPONENT_TYPES: {"page": "1"},
                au.SET_PARENT: {"object": ID.format(1), "parent": ID.format(8), "keep_world": "false",
                                "expected_object_token": T, "expected_transform_token": T,
                                "expected_old_scene_roots_token": T, "expected_new_parent_token": T}}
        for cap, inputs in base.items():
            au.parse_inputs(cap, inputs)                         # the valid form is accepted
        for (cap, key), value in valid.items():
            for end in ("\n", "\r\n"):
                with self.subTest(f"{key} {end!r}"):
                    with self.assertRaises(au.InputProblem):
                        au.parse_inputs(cap, dict(base[cap], **{key: value + end}))
        for kind, value in (("int64", '"-5"'), ("uint64", '"5"'), ("object", json.dumps(ID.format(9)))):
            au.parse_inputs(au.SET_PROPERTY, dict(base[au.SET_PROPERTY], kind=kind, value=value))
            for end in ("\n", "\r\n"):
                with self.subTest(f"{kind} value {end!r}"):
                    bad = json.dumps(json.loads(value) + end)
                    with self.assertRaises(au.InputProblem):
                        au.parse_inputs(au.SET_PROPERTY, dict(base[au.SET_PROPERTY], kind=kind, value=bad))
        for end in ("\n", "\r\n"):
            with self.assertRaises(ValueError):
                bi.txn_places("/tmp/x", "/tmp/x/Game", "a" * 32 + end)
            self.assertIsNone(bi.TXN_ID.fullmatch("a" * 32 + end))
            self.assertIsNone(bi.UTC.fullmatch("2026-09-26T12:00:00Z" + end))

    def test_strict_json(self):
        for bad in ('{"a": 1, "a": 2}', "[1, 2] 3", "NaN", "[Infinity]", "{'a': 1}", "", "[1,]"):
            with self.subTest(bad):
                with self.assertRaises(au.InputProblem):
                    au.strict_json("value", bad)
        self.assertEqual(au.strict_json("value", '{"a": [1, "x", null]}'), {"a": [1, "x", None]})

    def test_transform_values(self):
        base = {"object": ID.format(1), "expected_transform_token": T}
        args = au.parse_inputs(au.SET_TRANSFORM, dict(base, local_position="[1, 2.5, -3]",
                                                      local_rotation="[0, 0.7071068, 0, 0.7071068]"))
        self.assertEqual(args["local_position"], [1, 2.5, -3])
        for key, bad in (("local_position", "[1, 2]"), ("local_position", "[1, 2, 1e39]"),
                         ("local_position", "[1, 2, NaN]"), ("local_position", "[1, 2, true]"),
                         ("local_rotation", "[0, 0, 0, 2]"), ("local_rotation", "[0, 0, 0, 0]"),
                         ("local_scale", "{\"x\": 1}"), ("local_scale", "[1, 1, 1] [2]")):
            with self.subTest(bad):
                self.refused(au.SET_TRANSFORM, dict(base, **{key: bad}), "LIVE_VALUE_INVALID")

    def test_property_values_by_kind(self):
        def args(kind, value):
            return {"component": ID.format(3), "path": "field", "kind": kind, "value": value,
                    "expected_component_token": T}
        good = {"bool": "true", "int8": "-128", "int16": "32767", "int32": "-2147483648", "int64": '"-9223372036854775808"',
                "uint8": "255", "uint16": "65535", "uint32": "4294967295", "uint64": '"18446744073709551615"',
                "float32": "3.4028234663852886e38", "float64": "1e300", "string": '"h\\u00e9llo\\nworld"',
                "enum": '"Auto"', "vector2": "[1, 2]", "vector3": "[1, 2, 3]", "vector4": "[1, 2, 3, 4]",
                "vector2int": "[1, -2]", "vector3int": "[1, 2, 3]", "rect": "[0, 0, 1, 1]", "rectint": "[0, 0, 1, 1]",
                "bounds": "[[0, 0, 0], [1, 1, 1]]", "boundsint": "[[0, 0, 0], [1, 1, 1]]", "color": "[1, 0.5, 2, 1]",
                "quaternion": "[0, 0, 0, 1]", "layermask": "4294967295", "object": "null"}
        self.assertEqual(set(good), set(au.KINDS))
        for kind, value in good.items():
            with self.subTest(kind):
                au.parse_inputs(au.SET_PROPERTY, args(kind, value))
        au.parse_inputs(au.SET_PROPERTY, args("object", json.dumps(ID.format(9))))
        bad = [("int8", "128"), ("uint8", "-1"), ("uint8", "300"), ("int32", "5000000000"), ("int32", "1.5"),
               ("int32", "true"), ("int32", '"5"'), ("uint32", "-1"), ("int64", "5"), ("int64", '"9223372036854775808"'),
               ("int64", '"05"'), ("uint64", '"-5"'), ("uint64", '"18446744073709551616"'), ("float32", "1e39"),
               ("float64", "NaN"), ("float64", "Infinity"), ("string", '"' + "x" * 4097 + '"'), ("string", '"\\ud800"'),
               ("enum", "1"), ("enum", '""'), ("vector3", "[1, 2]"), ("vector2int", "[1, 2147483648]"),
               ("quaternion", "[0, 0, 0, 2]"), ("bounds", "[[0, 0, 0], [1, -1, 1]]"), ("color", "[1, 1, 1]"),
               ("layermask", "-1"), ("object", "5"), ("object", '"GlobalObjectId_V1-1-' + "a" * 32 + '-1-0"'),
               ("bool", "1"), ("bool", '{"a": 1, "a": 2}'), ("int32", "[[[[[[1]]]]]]")]
        for kind, value in bad:
            with self.subTest(f"{kind} {value[:30]}"):
                with self.assertRaises(au.InputProblem):
                    au.parse_inputs(au.SET_PROPERTY, args(kind, value))
        self.refused(au.SET_PROPERTY, args("char", "65"), "LIVE_PROPERTY_UNSUPPORTED")
        for path in ("list.Array.data[0]", "a.b.c.d.e.f.g", "m name", "a\n"):
            with self.subTest(path):
                self.refused(au.SET_PROPERTY, dict(args("int32", "1"), path=path), "LIVE_PROPERTY_UNSUPPORTED")


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

    def test_every_capability_sends_exactly_its_arguments(self):
        calls = {
            au.INSPECT_OBJECT: {"object": ID.format(1)},
            au.COMPONENT_TYPES: {"query": "Box", "page": "1"},
            au.PROPERTIES: {"component": ID.format(2), "path_prefix": "nested."},
            au.CREATE: {"scene": SCENE, "name": "A", "local_position": "[1, 2, 3]", "expected_scene_roots_token": T},
            au.DELETE: {"object": ID.format(1), "expected_subtree_token": T},
            au.SET_PARENT: {"object": ID.format(1), "parent": ID.format(4), "keep_world": "true", "sibling": "0",
                            "expected_object_token": T, "expected_transform_token": T,
                            "expected_old_scene_roots_token": T, "expected_new_parent_token": T,
                            "expected_transform_chain_token": T, "expected_new_parent_chain_token": T2},
            au.SET_GAMEOBJECT: {"object": ID.format(1), "name": "B", "static_flags": "4", "expected_object_token": T},
            au.SET_TRANSFORM: {"object": ID.format(1), "local_scale": "[2, 2, 2]", "expected_transform_token": T},
            au.ADD_COMPONENT: {"object": ID.format(1), "type_id": "A::B", "expected_object_token": T,
                               "expected_catalog_digest": DIGEST},
            au.REMOVE_COMPONENT: {"component": ID.format(2), "expected_component_token": T,
                                  "expected_object_token": T2},
            au.SET_PROPERTY: {"component": ID.format(2), "path": "i64", "kind": "int64", "value": '"-5"',
                              "expected_component_token": T},
            au.SAVE_SCENE: {"scene": SCENE},
        }
        for cap, inputs in calls.items():
            with self.subTest(cap):
                r = self.author(cap, sid=self.sid, **inputs)
                self.assertStatus(r, tdg.SUCCESS)
                command, args = self.b.author_calls[-1]
                self.assertEqual(command, au.COMMANDS[cap])
                self.assertEqual(set(args), set(au.INPUTS[cap]))
                self.assertEqual(args, au.parse_inputs(cap, inputs))
                self.assertEqual(r.mutation_performed, cap not in au.READ_ONLY)
                self.assertEqual(r.evidence_candidates, ())
                self.assertEqual("limitation" in r.data, cap not in au.READ_ONLY)
        self.assertEqual(self.b.author_calls[-1][1], {"scene": SCENE})
        self.assertIn("LIVE_SCENE_SAVED", self.codes(r))
        req = sorted((self.b.live / "claimed").glob("*.json"), key=lambda f: f.stat().st_mtime)[-1]
        body = json.loads(req.read_text())
        self.assertEqual((body["schema"], body["owner"], body["session_id"]),
                         ("gpos.unity.live.request/2", "AGENT:claude-main", self.sid))

    def test_bad_inputs_are_refused_before_anything_is_sent(self):
        before = len(self.b.claimed_ids)
        r = self.author(au.DELETE, sid=self.sid, object="World/A", expected_subtree_token=T)
        self.assertStatus(r, tdg.INVALID_REQUEST, "LIVE_OBJECT_REFUSED")
        r = self.author(au.SAVE_SCENE, sid=self.sid, scene="")
        self.assertStatus(r, tdg.CONFLICT, "LIVE_SCENE_NOT_SAVED")
        r = self.author(au.SET_PROPERTY, sid=self.sid, component=ID.format(2), path="u8", kind="uint8", value="300",
                        expected_component_token=T)
        self.assertStatus(r, tdg.INVALID_REQUEST, "LIVE_VALUE_INVALID")
        self.assertFalse(r.mutation_performed)
        self.assertEqual(len(self.b.claimed_ids), before)

    def test_refusals_map_and_report_whether_a_mutation_began(self):
        table = {"AUTHORING_CONFLICT": ("LIVE_AUTHORING_CONFLICT", tdg.CONFLICT),
                 "OBJECT_NOT_FOUND": ("LIVE_OBJECT_NOT_FOUND", tdg.CONFLICT),
                 "OBJECT_REFUSED": ("LIVE_OBJECT_REFUSED", tdg.INVALID_REQUEST),
                 "PREFAB_BOUNDARY": ("LIVE_PREFAB_BOUNDARY", tdg.CONFLICT),
                 "SCENE_NOT_SAVED": ("LIVE_SCENE_NOT_SAVED", tdg.CONFLICT),
                 "CATALOG_CHANGED": ("LIVE_CATALOG_CHANGED", tdg.CONFLICT),
                 "TYPE_NOT_IN_CATALOG": ("LIVE_TYPE_NOT_IN_CATALOG", tdg.INVALID_REQUEST),
                 "AUTHORING_REFUSED": ("LIVE_AUTHORING_REFUSED", tdg.CONFLICT),
                 "PROPERTY_UNSUPPORTED": ("LIVE_PROPERTY_UNSUPPORTED", tdg.INVALID_REQUEST),
                 "VALUE_INVALID": ("LIVE_VALUE_INVALID", tdg.INVALID_REQUEST),
                 "VALUE_NOT_APPLIED": ("LIVE_VALUE_INVALID", tdg.INVALID_REQUEST),
                 "AUTHORING_LIMIT": ("LIVE_AUTHORING_LIMIT", tdg.INVALID_REQUEST),
                 "EDITOR_BUSY": ("EDITOR_BUSY", tdg.CONFLICT),
                 "BAD_ARGUMENTS": ("LIVE_PROTOCOL_ERROR", tdg.INTERNAL_ERROR)}
        inputs = {"object": ID.format(1), "name": "B", "expected_object_token": T}
        for bridge_code, (code, status) in table.items():
            for started in (False, True):
                with self.subTest(f"{bridge_code} started={started}"):
                    data = {"mutation_started": True, "reverted": True, "restored": True} if started else None
                    self.b.author_reply = lambda c, a, bc=bridge_code, d=data: ("REFUSED", bc, d)
                    r = self.author(au.SET_GAMEOBJECT, sid=self.sid, **inputs)
                    self.assertStatus(r, status, code)
                    self.assertEqual(r.mutation_performed, started)
        self.b.author_reply = lambda c, a: ("REFUSED", "AUTHORING_CONFLICT", {"mutation_started": True})
        r = self.author(au.INSPECT_OBJECT, sid=self.sid, object=ID.format(1))
        self.assertFalse(r.mutation_performed)     # a read never reports a mutation

    def test_failures_after_a_mutation_began(self):
        cases = (("ROLLBACK_INCOMPLETE", {"mutation_started": True, "reverted": True, "restored": False},
                  "LIVE_ROLLBACK_INCOMPLETE", tdg.FAILED, True),
                 ("AUTHORING_FAILED", {"mutation_started": True, "reverted": True, "restored": True},
                  "LIVE_AUTHORING_FAILED", tdg.FAILED, True),
                 ("BRIDGE_INTERNAL_ERROR", None, "LIVE_PROTOCOL_ERROR", tdg.INTERNAL_ERROR, True),
                 ("BRIDGE_INTERNAL_ERROR", {"mutation_started": False}, "LIVE_PROTOCOL_ERROR", tdg.INTERNAL_ERROR, False))
        for bridge_code, data, code, status, mutated in cases:
            with self.subTest(bridge_code):
                self.b.author_reply = lambda c, a, bc=bridge_code, d=data: ("FAILED", bc, d)
                r = self.author(au.SET_TRANSFORM, sid=self.sid, object=ID.format(1), local_scale="[1, 1, 1]",
                                expected_transform_token=T)
                self.assertStatus(r, status, code)
                self.assertEqual(r.mutation_performed, mutated)
                if data:
                    self.assertEqual(r.data, data)
        self.b.author_reply = lambda c, a: ("FAILED", "BRIDGE_INTERNAL_ERROR", None)
        r = self.author(au.PROPERTIES, sid=self.sid, component=ID.format(2))
        self.assertFalse(r.mutation_performed)

    def test_busy_withdrawn_unknown_and_interrupted(self):
        inputs = {"object": ID.format(1), "name": "B", "expected_object_token": T}
        self.b.phase = "PLAYING"
        self.assertStatus(self.author(au.SET_GAMEOBJECT, sid=self.sid, **inputs), tdg.CONFLICT, "EDITOR_BUSY")
        self.assertStatus(self.author(au.INSPECT_OBJECT, sid=self.sid, object=ID.format(1)), tdg.CONFLICT,
                          "EDITOR_BUSY")
        self.b.phase = "EDIT"
        self.b.mode = "stall"
        r = self.author(au.SET_GAMEOBJECT, sid=self.sid, timeout=1, **inputs)
        self.assertStatus(r, tdg.CANCELLED, "LIVE_REQUEST_WITHDRAWN")
        self.assertFalse(r.mutation_performed)
        self.b.mode = "claim-only"
        count = published(self.b, "set-gameobject")
        r = self.author(au.SET_GAMEOBJECT, sid=self.sid, timeout=1, **inputs)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertTrue(r.mutation_performed)
        time.sleep(0.5)
        self.assertEqual(published(self.b, "set-gameobject"), count + 1)     # published once, never retried
        self.b.mode = "interrupt"
        r = self.author(au.SET_GAMEOBJECT, sid=self.sid, **inputs)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertTrue(r.mutation_performed)
        r = self.author(au.INSPECT_OBJECT, sid=self.sid, object=ID.format(1))
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "LIVE_OUTCOME_UNKNOWN")
        self.assertFalse(r.mutation_performed)

    def test_only_the_session_owner_authors(self):
        inputs = {"object": ID.format(1), "name": "B", "expected_object_token": T}
        self.assertStatus(self.author(au.SET_GAMEOBJECT, sid=None, **inputs), tdg.INVALID_REQUEST)
        self.assertStatus(self.run_cap(au.SET_GAMEOBJECT, actor=Actor("AGENT", "codex-worker"),
                                       inputs=dict(inputs, unity_project="Game"), session_id=self.sid),
                          tdg.CONFLICT, "LIVE_SESSION_MISMATCH")
        self.assertStatus(self.author(au.SET_GAMEOBJECT, sid=uuid.uuid4().hex, **inputs), tdg.CONFLICT)
        self.assertStatus(self.run_cap(au.SET_GAMEOBJECT, inputs=dict(inputs, unity_project="Game"),
                                       session_id=self.sid, allow_mutation=False), tdg.INVALID_REQUEST,
                          "MUTATION_NOT_ALLOWED")
        self.assertStatus(self.run_cap(au.INSPECT_OBJECT, inputs={"unity_project": "Game", "object": ID.format(1)},
                                       session_id=self.sid, allow_mutation=True), tdg.INVALID_REQUEST)
        self.assertEqual(self.b.author_calls, [])

    def test_authoring_needs_a_live_session_and_the_audited_package(self):
        inputs = {"object": ID.format(1), "name": "B", "expected_object_token": T}
        target = self.game / "Packages" / bi.PACKAGE_ID / "Editor" / "Authoring.cs"
        original = target.read_bytes()
        target.write_bytes(original + b"\n// changed")
        r = self.author(au.SET_GAMEOBJECT, sid=self.sid, **inputs)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_BRIDGE_UNTRUSTED")
        target.write_bytes(original)
        self.b.crash()                                       # the Editor is gone: the session is proven STALE
        r = self.author(au.SET_GAMEOBJECT, sid=self.sid, **inputs)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_SESSION_STALE")
        self.assertFalse(r.mutation_performed)
        self.assertEqual(self.b.author_calls, [])

    def test_an_earlier_bridge_is_never_used(self):
        self.b.protocol, self.b.bridge_version = "gpos.unity.live/1", "1.0.0"
        self.b.publish("READY")
        r = self.author(au.INSPECT_OBJECT, sid=self.sid, object=ID.format(1))
        self.assertStatus(r, tdg.INCOMPATIBLE, "LIVE_BRIDGE_INCOMPATIBLE")
        self.assertEqual(self.b.author_calls, [])


# ---------------------------------------------------------------- D  the bridge upgrade

class D_Upgrade(LiveCase):
    install = False

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, bi, "_after_step", None)
        self.target = self.game / "Packages" / bi.PACKAGE_ID
        self.runtime = self.p / ".game" / "gpos-runtime" / "unity"

    def old(self):
        frozen_package(self.target)
        return self.target

    def snapshot(self, path):
        return {str(f.relative_to(path)): f.read_bytes() for f in sorted(Path(path).rglob("*")) if f.is_file()}

    def leftovers(self):
        return {str(p.relative_to(self.runtime)) for n in ("install-txn", "install-staging", "install-backup")
                for p in (self.runtime / n).rglob("*")}

    def test_the_history_manifest_is_the_frozen_release(self):
        frozen = git("show", f"{FROZEN_TAG}:gpos/tools/unity/live_bridge/manifest.json", binary=True)
        self.assertEqual((bi.HISTORY / "1.0.0.json").read_bytes(), frozen)
        manifest = json.loads(frozen)
        self.assertEqual((manifest["bridge_version"], manifest["protocol"], manifest["package_digest"]),
                         ("1.0.0", "gpos.unity.live/1", FROZEN_DIGEST))
        self.assertEqual(bi.PREVIOUS, {"1.0.0": ("gpos.unity.live/1", FROZEN_DIGEST)})
        self.assertEqual(bi.history()["1.0.0"]["package_digest"], FROZEN_DIGEST)
        self.assertEqual(bi.digest(manifest["files"]), FROZEN_DIGEST)
        entries, problems = bi.tree(frozen_package(self.tmp / "frozen"))
        self.assertEqual((problems, bi.digest(entries)), ([], FROZEN_DIGEST))
        self.assertEqual(sorted(p.name for p in bi.HISTORY.iterdir()), ["1.0.0.json"])
        self.assertNotEqual(bi.verify_source()["package_digest"], FROZEN_DIGEST)

    def test_a_tampered_history_manifest_is_corrupt(self):
        original = bi.HISTORY
        copy = self.tmp / "history"
        shutil.copytree(original, copy)
        data = json.loads((copy / "1.0.0.json").read_text())
        data["files"][0]["sha256"] = "0" * 64
        data["package_digest"] = bi.digest(data["files"])
        (copy / "1.0.0.json").write_text(json.dumps(data))
        bi.HISTORY = copy
        try:
            with self.assertRaises(bi.BridgeSourceCorrupt):
                bi.history()
            self.assertStatus(self.run_cap(live.INSTALL), tdg.INTERNAL_ERROR, "LIVE_BRIDGE_SOURCE_CORRUPT")
        finally:
            bi.HISTORY = original

    def test_upgrade_status_idempotence_and_no_leftovers(self):
        self.old()
        manifest_before = (self.game / "Packages" / "manifest.json").read_bytes()
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.PREVIOUS_STATE, []))
        s = self.run_cap(live.STATUS)
        self.assertEqual((s.data["bridge"]["installed"], s.data["bridge"]["installed_version"]),
                         (bi.PREVIOUS_STATE, "1.0.0"))
        dry = self.run_cap(live.INSTALL, dry_run=True)
        self.assertStatus(dry, tdg.SUCCESS)
        self.assertIn("upgrade the exact released bridge 1.0.0", " ".join(dry.plan))
        self.assertEqual(bi.installed_version(self.game), "1.0.0")
        self.assertStatus(self.run_cap(live.ATTACH, timeout=5), tdg.INCOMPATIBLE, "LIVE_BRIDGE_INCOMPATIBLE")
        (self.game / "Temp").mkdir()
        (self.game / "Temp" / "UnityLockfile").write_text("")
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
        (self.game / "Temp" / "UnityLockfile").unlink()
        r = self.run_cap(live.INSTALL)
        self.assertStatus(r, tdg.SUCCESS, "LIVE_BRIDGE_UPGRADED")
        self.assertTrue(r.mutation_performed)
        self.assertEqual((r.data["upgraded_from"], r.data["installed_state"]), ("1.0.0", bi.EXACT))
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.EXACT, []))
        self.assertEqual(self.leftovers(), set())
        self.assertEqual((self.game / "Packages" / "manifest.json").read_bytes(), manifest_before)
        again = self.run_cap(live.INSTALL)
        self.assertStatus(again, tdg.SUCCESS, "LIVE_BRIDGE_ALREADY_INSTALLED")
        self.assertFalse(again.mutation_performed)
        self.assertIsNone(self.run_cap(live.STATUS).data["bridge"]["pending_upgrade"])

    def test_every_interruption_point_is_finished_or_rolled_back(self):
        steps = ("staged", "recorded", "old_moved", "new_placed", "verified", "backup_file_removed",
                 "backup_removed", "record_cleared")
        new_files = self.snapshot(bi.SOURCE)
        for step in steps:
            with self.subTest(step):
                shutil.rmtree(self.target, ignore_errors=True)
                shutil.rmtree(self.runtime, ignore_errors=True)
                self.old()

                def crash(name, at=step):
                    if name == at:
                        raise KeyboardInterrupt(f"simulated crash after {at}")
                bi._after_step = crash
                with self.assertRaises(KeyboardInterrupt):
                    bi.upgrade(self.p, self.game, ident_key(self), "Game", bi.verify_source())
                bi._after_step = None
                pending = self.run_cap(live.STATUS).data["bridge"]["pending_upgrade"]
                self.assertEqual(pending is not None, step not in ("staged", "record_cleared"), pending)
                r = self.run_cap(live.INSTALL)
                self.assertStatus(r, tdg.SUCCESS)
                self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.EXACT, []))
                self.assertEqual(self.snapshot(self.target), new_files)
                self.assertIsNone(bi.read_record(self.p, ident_key(self)))
                if step in ("recorded", "old_moved", "new_placed", "verified", "backup_file_removed",
                            "backup_removed"):
                    self.assertIn("LIVE_BRIDGE_UPGRADE_RECOVERED", self.codes(r))
                # a crash before the record exists leaves only runtime scratch: the verified staged copy
                self.assertEqual({p.split("/")[0] for p in self.leftovers()},
                                 {"install-staging"} if step == "staged" else set())

    def test_a_crash_before_the_new_package_is_placed_can_roll_back(self):
        self.old()
        old_files = self.snapshot(self.target)
        bi._after_step = lambda name: (_ for _ in ()).throw(KeyboardInterrupt()) if name == "old_moved" else None
        with self.assertRaises(KeyboardInterrupt):
            bi.upgrade(self.p, self.game, ident_key(self), "Game", bi.verify_source())
        bi._after_step = None
        record = bi.read_record(self.p, ident_key(self))
        places = bi.txn_places(self.p, self.game, record["txn_id"])
        shutil.rmtree(places["staging"])          # the staged package is lost: only the exact old one is known
        self.assertEqual(bi.recover(self.p, self.game, ident_key(self), "Game", bi.verify_source()), "ROLLED_BACK")
        self.assertEqual(self.snapshot(self.target), old_files)
        self.assertIsNone(bi.read_record(self.p, ident_key(self)))

    def test_unknown_content_fails_closed_and_is_left_untouched(self):
        self.old()
        bi._after_step = lambda name: (_ for _ in ()).throw(KeyboardInterrupt()) if name == "old_moved" else None
        with self.assertRaises(KeyboardInterrupt):
            bi.upgrade(self.p, self.game, ident_key(self), "Game", bi.verify_source())
        bi._after_step = None
        record = bi.read_record(self.p, ident_key(self))
        places = bi.txn_places(self.p, self.game, record["txn_id"])
        (places["backup"] / "Editor" / "Extra.cs").write_text("// not a released file")
        before = {k: self.snapshot(v) for k, v in places.items() if v.exists()}
        r = self.run_cap(live.INSTALL)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")
        self.assertFalse(r.mutation_performed)
        self.assertEqual({k: self.snapshot(v) for k, v in places.items() if v.exists()}, before)
        self.assertEqual(json.loads(tdg_details(r))["places"]["backup"], bi.PLACE_UNKNOWN)
        (places["backup"] / "Editor" / "Extra.cs").unlink()
        (places["staging"] / "package.json").write_text("{}")   # an unknown staging is never used ...
        r = self.run_cap(live.INSTALL)                          # ... the exact old package is restored instead
        self.assertStatus(r, tdg.SUCCESS, "LIVE_BRIDGE_UPGRADED")
        self.assertIn("LIVE_BRIDGE_UPGRADE_RECOVERED", self.codes(r))
        self.assertTrue((places["staging"] / "package.json").exists())   # unknown content stays where it is
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.EXACT, []))

    def test_a_modified_canonical_package_after_a_crash_fails_closed(self):
        self.old()
        bi._after_step = lambda name: (_ for _ in ()).throw(KeyboardInterrupt()) if name == "new_placed" else None
        with self.assertRaises(KeyboardInterrupt):
            bi.upgrade(self.p, self.game, ident_key(self), "Game", bi.verify_source())
        bi._after_step = None
        (self.target / "Editor" / "Bridge.cs").write_text("// changed after the crash")
        r = self.run_cap(live.INSTALL)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")
        self.assertEqual((self.target / "Editor" / "Bridge.cs").read_text(), "// changed after the crash")
        self.assertIsNotNone(bi.read_record(self.p, ident_key(self)))

    def test_a_package_changed_after_staging_is_never_accepted(self):
        self.old()

        def tamper(name):
            if name == "recorded":
                record = bi.read_record(self.p, ident_key(self))
                staged = bi.txn_places(self.p, self.game, record["txn_id"])["staging"]
                (staged / "Editor" / "Bridge.cs").write_text("// swapped after staging")
        bi._after_step = tamper
        r = self.run_cap(live.INSTALL)
        bi._after_step = None
        self.assertStatus(r, tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")
        self.assertTrue(r.mutation_performed)
        record = bi.read_record(self.p, ident_key(self))
        backup = bi.txn_places(self.p, self.game, record["txn_id"])["backup"]
        self.assertEqual(bi.classify_place(backup, bi.history()), "EXACT_1.0.0")   # the old bridge is kept
        self.assertEqual((self.target / "Editor" / "Bridge.cs").read_text(), "// swapped after staging")
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")

    def test_content_appearing_in_the_backup_is_never_removed(self):
        self.old()

        def plant(name):
            if name == "verified":
                record = bi.read_record(self.p, ident_key(self))
                backup = bi.txn_places(self.p, self.game, record["txn_id"])["backup"]
                (backup / "Editor" / "Planted.cs").write_text("// appeared during the upgrade")
        bi._after_step = plant
        r = self.run_cap(live.INSTALL)
        bi._after_step = None
        self.assertStatus(r, tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")
        record = bi.read_record(self.p, ident_key(self))
        backup = bi.txn_places(self.p, self.game, record["txn_id"])["backup"]
        self.assertEqual((backup / "Editor" / "Planted.cs").read_text(), "// appeared during the upgrade")
        self.assertEqual(len(bi.tree(backup)[0]), len(bi.history()["1.0.0"]["files"]) + 1)
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.EXACT, []))

    def test_the_record_is_never_trusted_for_paths(self):
        victim = self.tmp / "victim"
        victim.mkdir()
        (victim / "keep.txt").write_text("keep")
        self.old()
        record = {"schema": bi.TXN_SCHEMA, "txn_id": "a" * 32, "project_key": ident_key(self),
                  "unity_project_rel": "Game", "from_version": "1.0.0", "from_digest": FROZEN_DIGEST,
                  "to_version": bi.BRIDGE_VERSION, "to_digest": bi.verify_source()["package_digest"],
                  "phase": "OLD_MOVED", "started_at": "2026-09-26T12:00:00.000000Z"}
        path = bi.txn_record_path(self.p, ident_key(self))
        path.parent.mkdir(parents=True, exist_ok=True)
        for bad in (dict(record, backup=str(victim)), dict(record, txn_id="../../../victim"),
                    dict(record, txn_id=str(victim)), dict(record, project_key="0" * 16),
                    dict(record, from_digest="0" * 64), dict(record, phase="DONE"), "not json", "[]",
                    dict(record, txn_id="a" * 32 + "\n"), dict(record, txn_id="a" * 32 + "\r\n"),
                    dict(record, started_at=record["started_at"] + "\n")):
            with self.subTest(str(bad)[:60]):
                path.write_text(bad if isinstance(bad, str) else json.dumps(bad))
                r = self.run_cap(live.INSTALL)
                self.assertStatus(r, tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")
                self.assertEqual((victim / "keep.txt").read_text(), "keep")
                self.assertEqual(bi.installed_version(self.game), "1.0.0")
        path.unlink()
        path.symlink_to(victim / "keep.txt")
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")
        self.assertEqual((victim / "keep.txt").read_text(), "keep")
        path.unlink()
        # a well-formed record whose derived places hold nothing known: fail closed, change nothing
        path.write_text(json.dumps(dict(record, unity_project_rel="Other")))
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")
        path.write_text(json.dumps(record))
        shutil.rmtree(self.target)
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "LIVE_BRIDGE_UPGRADE_INCOMPLETE")
        self.assertEqual((victim / "keep.txt").read_text(), "keep")

    def test_a_linked_runtime_area_is_refused(self):
        self.old()
        outside = self.tmp / "outside"
        outside.mkdir()
        self.runtime.mkdir(parents=True, exist_ok=True)
        (self.runtime / "install-backup").symlink_to(outside, target_is_directory=True)
        r = self.run_cap(live.INSTALL)
        self.assertEqual(r.status, tdg.FAILED, self.messages(r))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(bi.installed_version(self.game), "1.0.0")

    def test_modified_newer_or_linked_packages_are_untrusted_never_downgraded(self):
        old = self.old()
        (old / "Editor" / "Bridge.cs").write_text((old / "Editor" / "Bridge.cs").read_text() + "\n// edited")
        before = self.snapshot(old)
        r = self.run_cap(live.INSTALL)
        self.assertStatus(r, tdg.CONFLICT, "LIVE_BRIDGE_UNTRUSTED")
        self.assertEqual(self.snapshot(old), before)
        shutil.rmtree(old)
        shutil.copytree(bi.SOURCE, old)
        pkg = json.loads((old / "package.json").read_text())
        pkg["version"] = "9.9.9"                                 # a "newer" bridge GPOS does not know
        (old / "package.json").write_text(json.dumps(pkg))
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "LIVE_BRIDGE_UNTRUSTED")
        self.assertEqual(json.loads((old / "package.json").read_text())["version"], "9.9.9")
        shutil.rmtree(old)
        frozen_package(old)
        (old / "Editor" / "Extra").mkdir()                       # an extra empty directory is not the release
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "LIVE_BRIDGE_UNTRUSTED")
        self.assertTrue((old / "Editor" / "Extra").is_dir())
        shutil.rmtree(old)
        elsewhere = frozen_package(self.tmp / "elsewhere")
        old.symlink_to(elsewhere, target_is_directory=True)
        self.assertStatus(self.run_cap(live.INSTALL), tdg.CONFLICT, "LIVE_BRIDGE_UNTRUSTED")
        self.assertTrue(old.is_symlink())
        self.assertEqual(set(bi.PREVIOUS), {"1.0.0"})            # only earlier releases; never a downgrade target


def ident_key(case):
    from gpos.tools.unity import identity as ident
    return ident.project_key(ident.relative(case.p, case.game))


def tdg_details(result):
    return next(d.details for d in result.diagnostics if d.details != "{}")


# ---------------------------------------------------------------- E  boundaries

class E_Boundaries(unittest.TestCase):
    AUTHORING_SOURCES = ("Authoring.cs", "Scene.cs", "Properties.cs", "Catalog.cs", "Core/ObjectIds.cs",
                         "Core/Tokens.cs", "Core/PropertyRules.cs")

    def text(self, name):
        """The source without comments: only code is checked."""
        lines = (bi.SOURCE / "Editor" / name).read_text().splitlines()
        return "\n".join(line.split("//")[0] if not line.lstrip().startswith("//") else "" for line in lines)

    def test_the_bridge_authoring_code_has_no_forbidden_mechanism(self):
        forbidden = ("System.Reflection", "Assembly.Load", "Activator.", ".Invoke(", "GetMethod(", "Type.GetType",
                     "ExecuteMenuItem", "executeMethod", "SaveSceneAs", "MarkSceneClean", "MarkAllScenesClean",
                     "ApplyPrefabInstance", "RevertPrefabInstance", "UnpackPrefabInstance", "SaveAsPrefabAsset",
                     "ApplyObjectOverride", "RevertObjectOverride", "AssetDatabase.Create", "AssetDatabase.Delete",
                     "AssetDatabase.Move", "AssetDatabase.Copy", "AssetDatabase.Import", "AssetDatabase.Refresh",
                     "GetInstanceID", "InstanceIDToObject", "EntityId", "Process.Start", "Socket", "HttpClient",
                     "WebRequest", "DisplayDialog", "OpenScene", "NewScene", "CloseScene", "SaveOpenScenes",
                     "SaveScenes(", "PrefabStage", "EditorApplication.Exit", "managedReferenceValue",
                     "arraySize", "InsertArrayElement", "DeleteArrayElement", "animationCurveValue", "gradientValue",
                     "ClearUndo", "Undo.ClearAll", "File.", "Directory.")
        for name in self.AUTHORING_SOURCES:
            text = self.text(name)
            for word in forbidden:
                with self.subTest(f"{name}: {word}"):
                    self.assertNotIn(word, text)
        authoring = self.text("Authoring.cs")
        self.assertEqual(authoring.count("EditorSceneManager.SaveScene(scene)"), 1)   # never with a path
        self.assertEqual(authoring.count("return Mutate("), 8)                         # every Scene change
        self.assertIn("Undo.RevertAllDownToGroup(group)", authoring)
        self.assertIn("Undo.CollapseUndoOperations(group)", authoring)
        create = authoring[authoring.index("Create(Dictionary"):authoring.index("Delete(Dictionary")]
        # the new object is fully constructed before its creation is registered (redo and ids stay stable)
        self.assertLess(create.index("SetSiblingIndex"), create.index("Undo.RegisterCreatedObjectUndo"))
        self.assertLess(create.index("Undo.RegisterCreatedObjectUndo"), create.index('"created"'))

    def test_the_python_side_starts_nothing_and_opens_nothing(self):
        for module in (au, bi):
            text = Path(module.__file__).read_text()
            for word in ("subprocess", "socket", "urllib", "http", "os.system", "eval(", "exec("):
                self.assertNotIn(word, text.replace("http://", ""), f"{module.__name__}: {word}")

    def test_transaction_places_are_derived(self):
        text = Path(bi.__file__).read_text()
        self.assertNotIn('record["staging"]', text)
        self.assertNotIn('record["backup"]', text)
        self.assertEqual(bi.TXN_KEYS, {"schema", "txn_id", "project_key", "unity_project_rel", "from_version",
                                       "from_digest", "to_version", "to_digest", "phase", "started_at"})
        with self.assertRaises(ValueError):
            bi.txn_places("/tmp/x", "/tmp/x/Game", "../../etc")


# ================================================================ real synthetic Unity (lab-owned batch Editors)

import test_unity_live as tl  # noqa: E402

REAL_STATE = {"editors": []}


def setUpModule():
    if FAST:
        return
    if len(EDITORS) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A: {len(EDITORS)} Hub Unity Editors found; exactly "
                           f"one is required")
    if tl.EDITOR_PREFS.exists():
        REAL_STATE["prefs"] = tl.prefs_snapshot()
    REAL_STATE["upm"] = tl.upm_config_state()
    REAL_STATE["work"] = Path(tempfile.mkdtemp(prefix="gpos-authoring-real-")).resolve()


def tearDownModule():
    try:
        for editor in REAL_STATE["editors"]:
            editor.stop()
        if FAST:
            return
        if "prefs" in REAL_STATE:
            now = tl.prefs_snapshot()
            moved = {k for k in set(now) | set(REAL_STATE["prefs"]) if now.get(k) != REAL_STATE["prefs"].get(k)}
            unexpected = sorted(moved - tl.ACCEPTED_PREFS)
            if unexpected:
                raise AssertionError(f"UNITY_SHARED_USER_STATE_UNEXPECTED_MUTATION: {unexpected}")
        if tl.upm_config_state() != REAL_STATE["upm"]:
            raise AssertionError("the user's Package Manager configuration files changed")
    finally:
        shutil.rmtree(REAL_STATE.get("work", "/nonexistent"), ignore_errors=True)


def authoring_project(name, install=True):
    p = REAL_STATE["work"] / name / "p"
    shutil.copytree(FIXTURE, p)
    fixtures.make_authoring_project(p / "Game", EDITOR_VERSION, EDITOR)
    shutil.copytree(tl.TESTKIT, p / "Game" / "Packages" / tl.TESTKIT.name)
    if install:
        bi.install(p, p / "Game", bi.verify_source())
    return p


class Lab:
    """One lab-owned Editor on a synthetic project, the testkit standing in for the Human, and GPOS requests."""

    def __init__(self, p):
        self.p, self.game = p, p / "Game"
        self.editor = tl.LabEditor(p, self.game)
        tl._STATE["editors"].remove(self.editor)
        REAL_STATE["editors"].append(self.editor)
        self.sid = None

    def human(self, timeout=120, **op):
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

    def run(self, cap, actor=tl.APPROVER, **inputs):
        time.sleep(0.2)   # stay well inside the bridge's 64 requests / 10 s admission limit
        timeout = inputs.pop("timeout", None)
        inputs = {k: (v if isinstance(v, str) and k != "value" else json.dumps(v)) for k, v in inputs.items()}
        inputs.setdefault("unity_project", "Game")
        kw = {"inputs": inputs}
        if timeout:
            kw["timeout"] = timeout
        if cap not in (live.ATTACH, live.STATUS, live.INSTALL):
            kw["session_id"] = self.sid
        return tl.real_request(self.p, cap, actor, **kw)


def ok(test, result, code=None, status=tdg.SUCCESS):
    text = " | ".join(f"{d.code}: {d.message}" for d in result.diagnostics)
    test.assertEqual(result.status, status, text)
    if code:
        test.assertIn(code, {d.code for d in result.diagnostics}, text)
    return result.data


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R1_RealAuthoring(unittest.TestCase):
    """One synthetic project, one lab Editor, one live session: every authoring rule in order."""

    @classmethod
    def setUpClass(cls):
        cls.lab = Lab(authoring_project("authoring"))
        cls.s = {}

    @classmethod
    def tearDownClass(cls):
        cls.lab.editor.stop()

    # ------------------------------------------------------------ helpers

    def run_cap(self, cap, **inputs):
        return self.lab.run(cap, **inputs)

    def inspect(self, oid):
        return ok(self, self.run_cap(au.INSPECT_OBJECT, object=oid))

    def roots(self):
        return ok(self, self.run_cap(au.INSPECT_OBJECT, scene=SCENE))["tokens"]["scene_roots"]

    def create(self, name, parent=None, **kw):
        if parent:
            r = self.run_cap(au.CREATE, scene=SCENE, name=name, parent=parent,
                             expected_parent_token=self.inspect(parent)["tokens"]["object"], **kw)
        else:
            r = self.run_cap(au.CREATE, scene=SCENE, name=name, expected_scene_roots_token=self.roots(), **kw)
        return ok(self, r)["created"]["id"]

    def add(self, oid, type_id, code=None, status=tdg.SUCCESS):
        r = self.run_cap(au.ADD_COMPONENT, object=oid, type_id=type_id,
                         expected_object_token=self.inspect(oid)["tokens"]["object"],
                         expected_catalog_digest=self.s["digest"])
        data = ok(self, r, code, status)
        return data["component"]["id"] if status == tdg.SUCCESS else r

    def ctoken(self, cid):
        return ok(self, self.run_cap(au.PROPERTIES, component=cid))["component_token"]

    def set_prop(self, cid, path, kind, value, token=None):
        return self.run_cap(au.SET_PROPERTY, component=cid, path=path, kind=kind, value=value,
                            expected_component_token=token or self.ctoken(cid))

    def read(self, oid, path):
        return self.lab.human(op="read", target=oid, path=path)["value"]

    def world_x(self, oid):
        x, current = 0.0, self.inspect(oid)
        while current:
            x += current["transform"]["local_position"][0]
            current = self.inspect(current["parent"]["id"]) if current["parent"] else None
        return x

    # ------------------------------------------------------------ the session

    def test_01_install_launch_attach_and_the_saved_scene(self):
        b = self.lab.editor.launch()
        self.assertEqual((b["bridge_version"], b["protocol"]), ("1.1.0", "gpos.unity.live/2"))
        r = ok(self, self.lab.run(live.ATTACH, timeout=120), "LIVE_SESSION_ATTACHED")
        self.lab.sid = r["session_id"]
        self.s.update(self.lab.human(op="setup"))
        self.assertEqual(self.s["scene"], SCENE)
        data = ok(self, self.run_cap(au.INSPECT_OBJECT, scene=SCENE))
        self.assertEqual((data["scene"]["path"], data["root_count"]), (SCENE, 1))
        self.assertEqual(data["roots"][0]["id"], self.s["prefab_root"])

    def test_02_the_closed_component_catalog(self):
        data = ok(self, self.run_cap(au.COMPONENT_TYPES))
        self.s["digest"] = data["catalog_digest"]
        ids = set()
        page = 0
        while True:
            chunk = ok(self, self.run_cap(au.COMPONENT_TYPES, page=str(page)))
            ids.update(t["type_id"] for t in chunk["types"])
            self.s.setdefault("types", {}).update({t["type_id"]: t for t in chunk["types"]})
            if chunk["next_page"] is None:
                break
            page = chunk["next_page"]
        self.assertEqual(len(ids), data["catalog_count"])
        for present in ("Assembly-CSharp::AuthorProps", "Assembly-CSharp::AuthorSingle",
                        "Assembly-CSharp::AuthorNeedsBox", "AuthorA::AuthorDup", "AuthorB::AuthorDup",
                        "UnityEngine.PhysicsModule::UnityEngine.BoxCollider",
                        "UnityEngine.PhysicsModule::UnityEngine.Rigidbody",
                        "UnityEngine.Physics2DModule::UnityEngine.Rigidbody2D"):
            self.assertIn(present, ids)
        for absent in ("Assembly-CSharp::AuthorAbstract", "Assembly-CSharp::AuthorGeneric`1",
                       "Assembly-CSharp::AuthorObsolete", "Assembly-CSharp::AuthorHidden",
                       "Assembly-CSharp::AuthorInternal", "Assembly-CSharp::AuthorNoScript",
                       "Assembly-CSharp-Editor::AuthorEditorOnly", "UnityEngine.CoreModule::UnityEngine.Transform",
                       "UnityEngine.CoreModule::UnityEngine.RectTransform"):
            self.assertNotIn(absent, ids)
        t = self.s["types"]
        self.assertTrue(t["Assembly-CSharp::AuthorSingle"]["disallow_multiple"])
        self.assertEqual(t["Assembly-CSharp::AuthorNeedsBox"]["requires"],
                         ["UnityEngine.PhysicsModule::UnityEngine.BoxCollider"])
        self.assertEqual((t["AuthorA::AuthorDup"]["full_name"], t["AuthorB::AuthorDup"]["full_name"]),
                         ("AuthorDup", "AuthorDup"))
        self.assertEqual(ok(self, self.run_cap(au.COMPONENT_TYPES, query="authordup"))["count"], 2)

    def test_03_hierarchy_one_undo_group_each_stable_ids(self):
        g = self.create("G", local_position=[10, 0, 0])
        p = self.create("P", g, local_position=[1, 0, 0])
        a = self.create("A", p, local_position=[1, 0, 0])
        n = self.create("N", local_position=[0, 5, 0])
        m = self.create("M", n, local_position=[2, 0, 0])
        self.s.update(G=g, P=p, A=a, N=n, M=m)
        self.assertEqual(self.lab.human(op="undo-name")["group"], "GPOS: create M")
        self.assertTrue(all(x.startswith("GlobalObjectId_V1-2-") and "-0000000000" not in x for x in (g, p, a, n, m)))
        c = self.create("Temp", g)
        self.assertEqual(self.lab.human(op="undo")["group"], "GPOS: create M")    # one Cmd-Z removes one command
        self.assertEqual(self.lab.human(op="exists", target=c)["exists"], False)
        ok(self, self.run_cap(au.INSPECT_OBJECT, object=c), "LIVE_OBJECT_NOT_FOUND", tdg.CONFLICT)
        self.lab.human(op="redo")
        self.assertEqual(self.inspect(c)["object"]["name"], "Temp")               # Redo brings back the same id
        top = self.create("TempRoot")                                              # ... at the Scene root too, even
        self.lab.human(op="undo")                                                  # after the agent looked for it
        ok(self, self.run_cap(au.INSPECT_OBJECT, object=top), "LIVE_OBJECT_NOT_FOUND", tdg.CONFLICT)
        self.lab.human(op="redo")
        self.assertEqual(self.inspect(top)["object"]["name"], "TempRoot")
        ok(self, self.run_cap(au.DELETE, object=top, expected_subtree_token=self.inspect(top)["tokens"]["subtree"]))
        ok(self, self.run_cap(au.DELETE, object=c, expected_subtree_token=self.inspect(c)["tokens"]["subtree"]))
        self.assertEqual(self.inspect(g)["child_count"], 1)
        d = self.inspect(a)
        self.assertEqual((d["parent"]["id"], d["sibling_index"], d["scene"]["dirty"]), (p, 0, True))
        r = self.run_cap(au.SET_PARENT, object=a, parent=g, keep_world="true", sibling="0",
                         expected_object_token=d["tokens"]["object"], expected_transform_token=d["tokens"]["transform"],
                         expected_old_parent_token=d["tokens"]["parent_object"],
                         expected_new_parent_token=self.inspect(g)["tokens"]["object"],
                         expected_transform_chain_token=d["tokens"]["transform_chain"],
                         expected_new_parent_chain_token=self.inspect(g)["tokens"]["transform_chain"])
        self.assertEqual(ok(self, r)["undo_group"], "GPOS: set parent of A")
        self.lab.human(op="undo")
        self.assertEqual(self.inspect(a)["parent"]["id"], p)
        self.assertEqual(self.inspect(a)["tokens"]["transform"], d["tokens"]["transform"])
        cyc = self.inspect(g)
        r = self.run_cap(au.SET_PARENT, object=g, parent=a, keep_world="false",
                         expected_object_token=cyc["tokens"]["object"], expected_transform_token=cyc["tokens"]["transform"],
                         expected_old_scene_roots_token=self.roots(),
                         expected_new_parent_token=self.inspect(a)["tokens"]["object"])
        ok(self, r, "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)

    def test_04_every_supported_kind_round_trips(self):
        a = self.s["A"]
        props = self.add(a, "Assembly-CSharp::AuthorProps")
        self.s["props"] = props
        self.s["box"] = self.add(a, "UnityEngine.PhysicsModule::UnityEngine.BoxCollider")
        cases = [("b", "bool", True, True), ("i8", "int8", -128, -128), ("i16", "int16", -32768, -32768),
                 ("i32", "int32", 2147483647, 2147483647), ("i64", "int64", "-9223372036854775808", "-9223372036854775808"),
                 ("u8", "uint8", 255, 255), ("u16", "uint16", 65535, 65535), ("u32", "uint32", 4294967295, 4294967295),
                 ("u64", "uint64", "18446744073709551615", "18446744073709551615"),
                 ("f32", "float32", 0.1, 0.10000000149011612), ("f64", "float64", 0.1, 0.1),
                 ("s", "string", "héllo\nworld", "héllo\nworld"), ("mode", "enum", "Auto", "Auto"),
                 ("flags", "enum", "B", "B"), ("alias", "enum", "Other", "Other"), ("v2", "vector2", [1.5, -2], [1.5, -2]),
                 ("v3", "vector3", [1, 2, 3], [1, 2, 3]), ("v4", "vector4", [1, 2, 3, 4], [1, 2, 3, 4]),
                 ("v2i", "vector2int", [1, -2], [1, -2]), ("v3i", "vector3int", [1, 2, 3], [1, 2, 3]),
                 ("rect", "rect", [1, 2, 3, 4], [1, 2, 3, 4]), ("ri", "rectint", [1, 2, 3, 4], [1, 2, 3, 4]),
                 ("bounds", "bounds", [[1, 2, 3], [0.5, 0.5, 0.5]], [[1, 2, 3], [0.5, 0.5, 0.5]]),
                 ("bi", "boundsint", [[1, 2, 3], [4, 5, 6]], [[1, 2, 3], [4, 5, 6]]),
                 ("c", "color", [0.5, 0.25, 2, 1], [0.5, 0.25, 2, 1]),
                 ("q", "quaternion", [0, 0.7071068, 0, 0.7071068], None), ("mask", "layermask", 4294967295, 4294967295),
                 ("m_Enabled", "bool", False, False), ("m_Enabled", "bool", True, True),
                 ("nested.n", "int32", 5, 5), ("nested.v", "vector3", [1, 1, 1], [1, 1, 1]), ("priv", "int32", 7, 7),
                 ("mat", "object", None, None)]
        for path, kind, value, expected in cases:
            with self.subTest(path):
                r = self.set_prop(props, path, kind, value)
                data = ok(self, r)
                self.assertTrue(r.mutation_performed)
                if expected is not None:
                    self.assertEqual(data["property"]["value"], expected)
                self.assertTrue(data["undo_group"].startswith(f"GPOS: set {path} of AuthorProps"))
        q = ok(self, self.run_cap(au.PROPERTIES, component=props, path_prefix="q"))["properties"][0]["value"]
        self.assertAlmostEqual(q[1], 0.7071068, places=6)
        self.assertEqual(self.read(props, "u64"), "18446744073709551615")
        self.assertEqual(self.read(props, "i64"), "-9223372036854775808")
        # a Flags enum takes exactly one declared name; a combination is not writable
        ok(self, self.set_prop(props, "flags", "enum", "A, B"), "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST)
        # two names for one value are ambiguous: the write is read back as the other name and reverted
        r = self.set_prop(props, "alias", "enum", "Same")
        ok(self, r, "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST)
        self.assertTrue(r.mutation_performed and r.data["restored"])
        self.assertEqual(self.read(props, "alias"), "2")

    def test_05_unsafe_properties_are_refused_before_any_write(self):
        props, a = self.s["props"], self.s["A"]
        listed = {e["path"]: e for e in ok(self, self.run_cap(au.PROPERTIES, component=props))["properties"]}
        self.assertNotIn("hidden", listed)                            # hidden state is never listed or valued
        self.assertEqual((listed["m_Script"]["writable"], listed["m_Script"]["refusal"]), (False, "DENIED"))
        for path in ("curve", "gradient", "arr", "list", "shape", "exposed", "ch", "h"):
            self.assertEqual((listed[path]["writable"], listed[path]["refusal"]), (False, "TYPE_UNSUPPORTED"), path)
        self.assertNotIn("shape.r", listed)                            # never entered
        script, owner = self.read(props, "m_Script"), self.read(props, "m_GameObject")
        before = self.ctoken(props)
        refusals = [("m_Script", "object", None, "LIVE_PROPERTY_UNSUPPORTED"),
                    ("m_GameObject", "object", self.s["N"], "LIVE_PROPERTY_UNSUPPORTED"),
                    ("m_ObjectHideFlags", "uint32", 1, "LIVE_PROPERTY_UNSUPPORTED"),
                    ("m_Name", "string", "x", "LIVE_PROPERTY_UNSUPPORTED"),
                    ("hidden", "int32", 1, "LIVE_PROPERTY_UNSUPPORTED"),
                    ("shape.r", "float32", 2, "LIVE_PROPERTY_UNSUPPORTED"),
                    ("i32", "int64", "5", "LIVE_PROPERTY_UNSUPPORTED"),
                    ("mode", "enum", "Nope", "LIVE_VALUE_INVALID"), ("nope", "int32", 1, "LIVE_PROPERTY_UNSUPPORTED")]
        for path, kind, value, code in refusals:
            with self.subTest(path):
                r = self.set_prop(props, path, kind, value, token=before)
                self.assertEqual(r.status, tdg.INVALID_REQUEST, [d.message for d in r.diagnostics])
                self.assertIn(code, {d.code for d in r.diagnostics})
                self.assertFalse(r.mutation_performed)
        self.assertEqual((self.read(props, "m_Script"), self.read(props, "m_GameObject")), (script, owner))
        self.assertEqual(self.ctoken(props), before)
        transform = self.inspect(a)["components"][0]["id"]
        ok(self, self.set_prop(transform, "m_LocalPosition", "vector3", [1, 1, 1], token=T), "LIVE_PROPERTY_UNSUPPORTED",
           tdg.INVALID_REQUEST)

    def test_06_object_references_are_scene_only_and_type_checked(self):
        props, a, box = self.s["props"], self.s["A"], self.s["box"]
        ok(self, self.set_prop(props, "go", "object", a))
        ok(self, self.set_prop(props, "box", "object", box))
        ok(self, self.set_prop(props, "tr", "object", self.inspect(a)["components"][0]["id"]))
        self.assertEqual(self.read(props, "box"), box)
        for path, value in (("tr", box), ("go", box), ("box", a), ("mat", a), ("clamp", box)):
            with self.subTest(path):
                r = self.set_prop(props, path, "object", value)
                ok(self, r, "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST)
                self.assertFalse(r.mutation_performed)
        unsaved = self.lab.human(op="unsaved-scene", name="Elsewhere")["id"]
        ok(self, self.set_prop(props, "go", "object", unsaved), "LIVE_SCENE_NOT_SAVED", tdg.CONFLICT)
        self.lab.human(op="close-unsaved")
        ok(self, self.set_prop(props, "go", "object", None))
        self.assertEqual(self.read(props, "go"), "")

    def test_07_rollback_is_verified_restored_or_reported_incomplete(self):
        a = self.s["A"]
        clamp = self.add(a, "Assembly-CSharp::AuthorClamp")
        drift = self.add(a, "Assembly-CSharp::AuthorDrift")
        self.s["drift"] = drift
        token = self.ctoken(clamp)
        r = self.set_prop(clamp, "value", "int32", 50)          # OnValidate clamps to 10: read back differs
        ok(self, r, "LIVE_VALUE_INVALID", tdg.INVALID_REQUEST)
        self.assertTrue(r.mutation_performed)
        self.assertEqual((r.data["reverted"], r.data["restored"]), (True, True))
        self.assertEqual(self.read(clamp, "value"), "0")
        self.assertEqual(self.ctoken(clamp), token)
        self.assertTrue(r.data["scene"]["dirty"])                 # a revert may leave the Scene dirty; disclosed
        self.assertNotEqual(self.lab.human(op="undo-name")["group"], "GPOS: set value of AuthorClamp on A")
        r = self.set_prop(drift, "value", "int32", 50)            # project code changes state again on the revert
        ok(self, r, "LIVE_ROLLBACK_INCOMPLETE", tdg.FAILED)
        self.assertTrue(r.mutation_performed)
        self.assertEqual((r.data["reverted"], r.data["restored"]), (True, False))
        self.assertEqual(self.read(drift, "value"), "0")          # the value itself was reverted ...
        self.assertNotEqual(self.read(drift, "validations"), "0")  # ... project callbacks are TOOL_INHERENT

    def test_08_component_rules(self):
        a, n = self.s["A"], self.s["N"]
        r = self.run_cap(au.ADD_COMPONENT, object=n, type_id="Assembly-CSharp::AuthorNeedsBox",
                         expected_object_token=self.inspect(n)["tokens"]["object"],
                         expected_catalog_digest=self.s["digest"])
        data = ok(self, r)
        self.assertEqual(sorted(c["type"] for c in data["added_components"]),
                         ["Assembly-CSharp::AuthorNeedsBox", "UnityEngine.PhysicsModule::UnityEngine.BoxCollider"])
        self.assertEqual(self.lab.human(op="undo-name")["group"], "GPOS: add AuthorNeedsBox to N")
        box = next(c for c in data["added_components"] if c["type"].endswith("BoxCollider"))["id"]
        d = self.inspect(n)
        r = self.run_cap(au.REMOVE_COMPONENT, component=box,
                         expected_component_token=next(c for c in d["components"] if c.get("id") == box)["component_token"],
                         expected_object_token=d["tokens"]["object"])
        ok(self, r, "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)                     # refused before anything changed
        transform = d["components"][0]
        ok(self, self.run_cap(au.REMOVE_COMPONENT, component=transform["id"],
                              expected_component_token=transform["component_token"],
                              expected_object_token=d["tokens"]["object"]), "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)
        self.add(n, "Assembly-CSharp::AuthorSingle")
        self.add(n, "Assembly-CSharp::AuthorSingle", "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)
        self.add(n, "UnityEngine.PhysicsModule::UnityEngine.Rigidbody")
        before = self.inspect(n)["tokens"]["object"]
        r = self.add(n, "UnityEngine.Physics2DModule::UnityEngine.Rigidbody2D", "LIVE_AUTHORING_REFUSED", tdg.CONFLICT)
        self.assertEqual(r.data["restored"], True)                 # Unity refused inside the group: reverted
        self.assertEqual(self.inspect(n)["tokens"]["object"], before)
        self.add(n, "Assembly-CSharp::AuthorAbstract", "LIVE_TYPE_NOT_IN_CATALOG", tdg.INVALID_REQUEST)
        self.add(n, "UnityEngine.CoreModule::UnityEngine.Transform", "LIVE_TYPE_NOT_IN_CATALOG", tdg.INVALID_REQUEST)
        dup_a = self.add(a, "AuthorA::AuthorDup")
        self.lab.human(op="undo")                                  # Cmd-Z, then Cmd-Shift-Z: the same component id
        self.lab.human(op="redo")                                  # resolves again (see the resolver's by-id scan)
        self.assertEqual(ok(self, self.run_cap(au.PROPERTIES, component=dup_a))["component"]["id"], dup_a)
        dup_b = self.add(a, "AuthorB::AuthorDup")
        self.assertNotEqual(dup_a, dup_b)
        d = self.inspect(a)
        entry = next(c for c in d["components"] if c.get("id") == dup_b)
        data = ok(self, self.run_cap(au.REMOVE_COMPONENT, component=dup_b, expected_component_token=entry["component_token"],
                                     expected_object_token=d["tokens"]["object"]))
        self.assertEqual(data["removed"], dup_b)
        self.lab.human(op="undo")                                  # Cmd-Z brings the same component back
        self.assertEqual(self.lab.human(op="exists", target=dup_b)["exists"], True)
        self.assertEqual(self.ctoken(dup_b), entry["component_token"])

    def test_09_human_edits_make_stale_requests_conflict(self):
        g, p, a, n, props = self.s["G"], self.s["P"], self.s["A"], self.s["N"], self.s["props"]
        d = self.inspect(a)
        self.lab.human(op="set-serialized", target=a, path="m_Name", kind="string", value="Renamed")   # Inspector
        r = self.run_cap(au.SET_GAMEOBJECT, object=a, name="Mine", expected_object_token=d["tokens"]["object"])
        ok(self, r, "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        self.assertEqual(self.inspect(a)["object"]["name"], "Renamed")   # the Human's edit is kept
        self.assertEqual(self.inspect(a)["tokens"]["transform"], d["tokens"]["transform"])
        ok(self, self.run_cap(au.SET_TRANSFORM, object=a, local_scale=[1, 1, 1],
                              expected_transform_token=d["tokens"]["transform"]))   # untouched state: no conflict
        d = self.inspect(a)
        self.lab.human(op="set-serialized", target=d["components"][0]["id"], path="m_LocalPosition", kind="vector3",
                       value="3,0,0")
        ok(self, self.run_cap(au.SET_TRANSFORM, object=a, local_scale=[2, 2, 2],
                              expected_transform_token=d["tokens"]["transform"]), "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        ok(self, self.run_cap(au.SET_GAMEOBJECT, object=a, name="Mine", expected_object_token=d["tokens"]["object"]))
        # hidden serialized state: changes the component token, is never listed, blocks stale destructive commands
        d = self.inspect(a)
        ctok = self.ctoken(props)
        self.lab.human(op="set-serialized", target=props, path="hidden", kind="int", value="42")
        self.assertNotEqual(self.ctoken(props), ctok)
        self.assertNotIn("hidden", {e["path"] for e in ok(self, self.run_cap(au.PROPERTIES, component=props))["properties"]})
        ok(self, self.set_prop(props, "i32", "int32", 1, token=ctok), "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        ok(self, self.run_cap(au.REMOVE_COMPONENT, component=props, expected_component_token=ctok,
                              expected_object_token=d["tokens"]["object"]), "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        self.assertEqual(self.lab.human(op="exists", target=props)["exists"], True)
        # a descendant's hidden state changes the subtree token: a stale delete of an ancestor is refused
        subtree = self.inspect(g)["tokens"]["subtree"]
        self.lab.human(op="set-serialized", target=props, path="hidden", kind="int", value="43")
        ok(self, self.run_cap(au.DELETE, object=g, expected_subtree_token=subtree), "LIVE_AUTHORING_CONFLICT",
           tdg.CONFLICT)
        self.assertEqual(self.lab.human(op="exists", target=g)["exists"], True)
        # the Human adds a child: the parent's object token changes, a stale create under it is refused
        parent_token = self.inspect(n)["tokens"]["object"]
        self.lab.human(op="add-child", target=n, name="HumanChild")
        ok(self, self.run_cap(au.CREATE, scene=SCENE, name="Late", parent=n, expected_parent_token=parent_token),
           "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        # the Human drags an object to the Scene root: a stale create at the root is refused
        roots = self.roots()
        self.lab.human(op="reparent", target=self.inspect(n)["children"][-1]["id"], parent="", keepWorld=True)
        ok(self, self.run_cap(au.CREATE, scene=SCENE, name="Late", expected_scene_roots_token=roots),
           "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        # an untouched object is not affected by any of this
        ok(self, self.run_cap(au.SET_GAMEOBJECT, object=p, name="P2",
                              expected_object_token=self.inspect(p)["tokens"]["object"]))

    def test_10_keep_world_reparent_needs_the_transform_chains(self):
        g, a, n, m = self.s["G"], self.s["A"], self.s["N"], self.s["M"]

        def tokens():
            d, t = self.inspect(a), self.inspect(m)
            return {"expected_object_token": d["tokens"]["object"], "expected_transform_token": d["tokens"]["transform"],
                    "expected_old_parent_token": d["tokens"]["parent_object"],
                    "expected_new_parent_token": t["tokens"]["object"]}, \
                   {"expected_transform_chain_token": d["tokens"]["transform_chain"],
                    "expected_new_parent_chain_token": t["tokens"]["transform_chain"]}
        base, chains = tokens()
        g_transform = self.inspect(g)["components"][0]["id"]
        self.lab.human(op="set-serialized", target=g_transform, path="m_LocalPosition", kind="vector3", value="20,0,0")
        r = self.run_cap(au.SET_PARENT, object=a, parent=m, keep_world="true", **base, **chains)
        ok(self, r, "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)          # the old grandparent moved
        self.assertIn("Scene root down to the object", r.diagnostics[0].message)
        base, chains = tokens()
        n_transform = self.inspect(n)["components"][0]["id"]
        self.lab.human(op="set-serialized", target=n_transform, path="m_LocalPosition", kind="vector3", value="0,7,0")
        r = self.run_cap(au.SET_PARENT, object=a, parent=m, keep_world="true", **base, **chains)
        ok(self, r, "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)          # the new parent's ancestor moved
        self.assertIn("Scene root down to the new parent", r.diagnostics[0].message)
        self.assertEqual(self.inspect(a)["parent"]["id"], self.s["P"])
        # a local move does not depend on ancestor transforms: the same stale chains are not required
        self.lab.human(op="set-serialized", target=g_transform, path="m_LocalPosition", kind="vector3", value="30,0,0")
        base, _ = tokens()
        local = self.inspect(a)["transform"]["local_position"]
        ok(self, self.run_cap(au.SET_PARENT, object=a, parent=m, keep_world="false", **base))
        self.assertEqual(self.inspect(a)["transform"]["local_position"], local)
        self.lab.human(op="undo")
        # with fresh chains, keep_world keeps the world position
        base, chains = tokens()
        world = self.world_x(a)
        ok(self, self.run_cap(au.SET_PARENT, object=a, parent=m, keep_world="true", **base, **chains))
        self.assertAlmostEqual(self.world_x(a), world, places=4)
        self.assertEqual(self.inspect(a)["parent"]["id"], m)

    def test_11_the_prefab_boundary(self):
        root, child, collider = self.s["prefab_root"], self.s["prefab_child"], self.s["prefab_child_collider"]
        d = self.inspect(root)
        self.assertEqual((d["object"]["prefab"], self.inspect(child)["object"]["prefab"]),
                         ("INSTANCE_ROOT", "INSTANCE_CONTENT"))
        ok(self, self.run_cap(au.SET_GAMEOBJECT, object=root, name="Crate2", expected_object_token=d["tokens"]["object"]))
        d = self.inspect(root)
        ok(self, self.run_cap(au.SET_TRANSFORM, object=root, local_position=[0, 1, 0],
                              expected_transform_token=d["tokens"]["transform"]))
        refused = [
            self.run_cap(au.SET_GAMEOBJECT, object=root, tag="Player", expected_object_token=d["tokens"]["object"]),
            self.run_cap(au.SET_GAMEOBJECT, object=child, name="X",
                         expected_object_token=self.inspect(child)["tokens"]["object"]),
            self.run_cap(au.SET_TRANSFORM, object=child, local_position=[1, 1, 1],
                         expected_transform_token=self.inspect(child)["tokens"]["transform"]),
            self.run_cap(au.CREATE, scene=SCENE, name="Q", parent=root, expected_parent_token=d["tokens"]["object"]),
            self.run_cap(au.ADD_COMPONENT, object=root, type_id="UnityEngine.PhysicsModule::UnityEngine.BoxCollider",
                         expected_object_token=d["tokens"]["object"], expected_catalog_digest=self.s["digest"]),
            self.run_cap(au.DELETE, object=child, expected_subtree_token=self.inspect(child)["tokens"]["subtree"]),
            self.set_prop(collider, "m_IsTrigger", "bool", True, token=T),
            self.run_cap(au.REMOVE_COMPONENT, component=collider, expected_component_token=T,
                         expected_object_token=self.inspect(child)["tokens"]["object"]),
        ]
        for r in refused:
            ok(self, r, "LIVE_PREFAB_BOUNDARY", tdg.CONFLICT)
            self.assertFalse(r.mutation_performed)
        listed = ok(self, self.run_cap(au.PROPERTIES, component=collider))["properties"]
        self.assertEqual({e["refusal"] for e in listed if e["kind"]}, {"PREFAB_BOUNDARY"})
        n = self.s["N"]
        dn, dr = self.inspect(n), self.inspect(root)
        r = self.run_cap(au.SET_PARENT, object=n, parent=root, keep_world="false",
                         expected_object_token=dn["tokens"]["object"], expected_transform_token=dn["tokens"]["transform"],
                         expected_old_scene_roots_token=self.roots(), expected_new_parent_token=dr["tokens"]["object"])
        ok(self, r, "LIVE_PREFAB_BOUNDARY", tdg.CONFLICT)
        ok(self, self.run_cap(au.DELETE, object=root, expected_subtree_token=dr["tokens"]["subtree"]))
        self.lab.human(op="undo")
        restored = self.inspect(root)
        self.assertEqual(restored["object"]["prefab"], "INSTANCE_ROOT")
        self.assertEqual(restored["tokens"]["subtree"], dr["tokens"]["subtree"])   # Undo restored it exactly

    def test_12_save_scene(self):
        path = self.lab.game / SCENE
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(self.lab.human(op="scene-state", scene=SCENE)["dirty"], True)
        data = ok(self, self.run_cap(au.SAVE_SCENE, scene=SCENE), "LIVE_SCENE_SAVED")
        self.assertEqual((data["saved"], data["scene"]["dirty"]), (True, False))
        self.assertNotEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in path.parent.glob("*.unity")), ["Main.unity"])
        unsaved = self.lab.human(op="unsaved-scene", name="U")["id"]
        ok(self, self.run_cap(au.SAVE_SCENE, scene=""), "LIVE_SCENE_NOT_SAVED", tdg.CONFLICT)
        ok(self, self.run_cap(au.INSPECT_OBJECT, object=unsaved), "LIVE_SCENE_NOT_SAVED", tdg.CONFLICT)
        self.lab.human(op="close-unsaved")
        ok(self, self.run_cap(au.SAVE_SCENE, scene="Assets/Scenes/Missing.unity"), "LIVE_OBJECT_NOT_FOUND",
           tdg.CONFLICT)
        self.assertFalse((self.lab.game / "Assets/Scenes/Missing.unity").exists())
        # reopening the saved Scene gives every object a new session-local instance; ids and tokens stay the same
        a, g, props, drift = self.s["A"], self.s["G"], self.s["props"], self.s["drift"]
        before, ctok, g_before, dtok = self.inspect(a), self.ctoken(props), self.inspect(g), self.ctoken(drift)
        self.lab.human(op="reopen", scene=SCENE)
        after = self.inspect(a)
        self.assertEqual(after["object"]["id"], a)
        for key in ("object", "transform", "transform_chain", "parent_object", "scene_roots"):
            self.assertEqual(after["tokens"][key], before["tokens"][key], key)
        self.assertEqual(self.inspect(g)["tokens"], g_before["tokens"])
        self.assertEqual(self.ctoken(props), ctok)             # its references hash by id, not by instance
        self.assertNotEqual(self.ctoken(drift), dtok)          # loading ran AuthorDrift.OnValidate (TOOL_INHERENT)
        self.assertEqual(self.lab.human(op="scene-state", scene=SCENE)["dirty"], False)
        r = self.run_cap(au.SET_GAMEOBJECT, object=self.s["N"], name="N", expected_object_token=T)
        ok(self, r, "LIVE_AUTHORING_CONFLICT", tdg.CONFLICT)
        self.assertEqual(self.lab.human(op="scene-state", scene=SCENE)["dirty"], False)   # a refusal touches nothing

    def test_13_play_mode_refuses_authoring(self):
        ok(self, self.lab.run(live.ENTER))
        n = self.s["N"]
        ok(self, self.run_cap(au.INSPECT_OBJECT, object=n), "EDITOR_BUSY", tdg.CONFLICT)
        r = self.run_cap(au.SET_GAMEOBJECT, object=n, name="Playing", expected_object_token=T)
        ok(self, r, "EDITOR_BUSY", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        ok(self, self.lab.run(live.EXIT))
        self.assertNotEqual(self.inspect(n)["object"]["name"], "Playing")

    def test_14_domain_reload_keeps_ids_tokens_and_the_catalog(self):
        a, g, props, drift = self.s["A"], self.s["G"], self.s["props"], self.s["drift"]
        before, ctok, dtok, g_before = self.inspect(a), self.ctoken(props), self.ctoken(drift), self.inspect(g)
        digest = ok(self, self.run_cap(au.COMPONENT_TYPES))["catalog_digest"]
        generation = self.lab.editor.heartbeat()["generation"]
        self.lab.editor.trigger("reload")
        self.assertTrue(self.lab.editor.wait(lambda: self.lab.editor.heartbeat().get("generation", 0) > generation, 120))
        time.sleep(1)
        after = self.inspect(a)
        self.assertEqual(after["object"]["id"], before["object"]["id"])
        for key in ("object", "transform", "transform_chain", "parent_object", "scene_roots"):
            self.assertEqual(after["tokens"][key], before["tokens"][key], key)
        self.assertEqual(self.inspect(g)["tokens"], g_before["tokens"])
        self.assertEqual(self.ctoken(props), ctok)
        # project code ran on the reload: AuthorDrift.OnValidate changed its own serialized state, and the component
        # and subtree tokens report it (TOOL_INHERENT; a request decided on the old tokens would be a conflict)
        self.assertNotEqual(self.ctoken(drift), dtok)
        self.assertNotEqual(after["tokens"]["subtree"], before["tokens"]["subtree"])
        self.assertEqual(ok(self, self.run_cap(au.COMPONENT_TYPES))["catalog_digest"], digest)

    def test_15_a_recompile_that_changes_requirements_changes_the_digest(self):
        n = self.s["N"]
        types = ok(self, self.run_cap(au.COMPONENT_TYPES, query="AuthorChanging"))
        old = types["catalog_digest"]
        self.assertEqual(types["types"][0]["requires"], [])
        script = self.lab.game / "Assets/Authoring/AuthorChanging.cs"
        script.write_text("using UnityEngine;\n[RequireComponent(typeof(Rigidbody))] "
                          "public class AuthorChanging : MonoBehaviour { }\n")
        generation = self.lab.editor.heartbeat()["generation"]
        self.lab.editor.trigger("refresh")
        self.assertTrue(self.lab.editor.wait(lambda: self.lab.editor.heartbeat().get("generation", 0) > generation, 180))
        self.assertTrue(self.lab.editor.wait(lambda: (self.lab.editor.heartbeat().get("state") or {}).get("phase") == "EDIT",
                                             120))
        time.sleep(1)
        types = ok(self, self.run_cap(au.COMPONENT_TYPES, query="AuthorChanging"))
        self.assertEqual(types["types"][0]["type_id"], "Assembly-CSharp::AuthorChanging")   # same id ...
        self.assertEqual(types["types"][0]["requires"], ["UnityEngine.PhysicsModule::UnityEngine.Rigidbody"])
        self.assertNotEqual(types["catalog_digest"], old)                                   # ... new digest
        r = self.run_cap(au.ADD_COMPONENT, object=n, type_id="Assembly-CSharp::AuthorChanging",
                         expected_object_token=self.inspect(n)["tokens"]["object"], expected_catalog_digest=old)
        ok(self, r, "LIVE_CATALOG_CHANGED", tdg.CONFLICT)
        self.assertFalse(r.mutation_performed)
        self.s["digest"] = types["catalog_digest"]

    def test_16_a_withdrawn_request_never_runs(self):
        n = self.s["N"]
        token = self.inspect(n)["tokens"]["object"]
        self.lab.editor.trigger("block-4000")
        time.sleep(1.0)
        r = self.run_cap(au.SET_GAMEOBJECT, object=n, name="Withdrawn", expected_object_token=token, timeout=1)
        ok(self, r, "LIVE_REQUEST_WITHDRAWN", tdg.CANCELLED)
        self.assertFalse(r.mutation_performed)
        time.sleep(4)
        self.assertNotEqual(self.inspect(n)["object"]["name"], "Withdrawn")

    def test_17_detach_and_the_package_is_untouched(self):
        ok(self, self.lab.run(live.DETACH), "LIVE_SESSION_DETACHED")
        ok(self, self.run_cap(au.INSPECT_OBJECT, object=self.s["N"]), None, tdg.CONFLICT)
        self.lab.editor.stop()
        self.assertEqual(bi.inspect_target(self.lab.game, bi.verify_source()), (bi.EXACT, []))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class R2_RealUpgrade(unittest.TestCase):
    def test_a_running_1_0_0_bridge_is_upgraded_only_while_the_project_is_closed(self):
        lab = Lab(authoring_project("upgrade", install=False))
        frozen_package(lab.game / "Packages" / bi.PACKAGE_ID)
        self.assertEqual(bi.installed_version(lab.game), "1.0.0")
        b = lab.editor.launch()
        self.assertEqual((b["bridge_version"], b["protocol"], b["package_digest"]),
                         ("1.0.0", "gpos.unity.live/1", FROZEN_DIGEST))
        status = ok(self, lab.run(live.STATUS))["bridge"]
        self.assertEqual((status["installed"], status["installed_version"], status["compatible"]),
                         (bi.PREVIOUS_STATE, "1.0.0", False))
        ok(self, lab.run(live.ATTACH, timeout=30), "LIVE_BRIDGE_INCOMPATIBLE", tdg.INCOMPATIBLE)
        ok(self, lab.run(live.INSTALL), "ENGINE_PROJECT_LOCKED", tdg.CONFLICT)      # no hot upgrade
        self.assertEqual(bi.installed_version(lab.game), "1.0.0")
        lab.editor.stop()
        data = ok(self, lab.run(live.INSTALL), "LIVE_BRIDGE_UPGRADED")
        self.assertEqual(data["upgraded_from"], "1.0.0")
        b = lab.editor.launch()
        self.assertEqual((b["bridge_version"], b["protocol"], b["package_digest"]),
                         (bi.BRIDGE_VERSION, bi.PROTOCOL, bi.verify_source()["package_digest"]))
        lab.sid = ok(self, lab.run(live.ATTACH, timeout=120), "LIVE_SESSION_ATTACHED")["session_id"]
        lab.human(op="setup")
        ok(self, lab.run(au.INSPECT_OBJECT, scene=SCENE))
        ok(self, lab.run(live.DETACH), "LIVE_SESSION_DETACHED")
        lab.editor.stop()
        self.assertEqual(bi.inspect_target(lab.game, bi.verify_source()), (bi.EXACT, []))


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
