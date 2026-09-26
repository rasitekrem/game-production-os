#!/usr/bin/env python3
"""Bounded mutation harness for Unity live Scene authoring and the bridge upgrade (Phase 2C-6B1).

    python3 tests/mutate_unity_authoring.py [--jobs N] [--only TEXT] [--anchors]

Each mutation breaks exactly one guarantee in a temporary copy of the repository and runs one suite there:

    "authoring"  tests/test_unity_authoring.py with GPOS_UNITY_TEST_FAST=1 (fake bridge, upgrade matrix; no Unity)
    "core"       tests/test_unity_live_bridge_core.py (the bridge's C# core, compiled with Unity's bundled Mono)
    "real"       (--real) the R1 group of tests/test_unity_authoring.py in lab-owned batch Editors: the bridge's
                 Editor-side authoring code, which only a real Unity runs

A mutation of the bridge package regenerates its manifest in the copy first, so it can only be caught by a test of
what the code does. The copy has no .git; GPOS_SOURCE_GIT_DIR points the frozen-tag tests at this repository's
(read only). A mutation must make its suite fail ("CAUGHT"); one that leaves it green is "MISSED" and fails this
harness, and so does an anchor that does not match exactly once ("NOT APPLIED"). `--anchors` only checks the
anchors. The repository itself is never modified, and after each real mutation every Unity process started for
that copy is stopped.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AU, BI, LV = "gpos/tools/unity/authoring.py", "gpos/tools/unity/bridge_install.py", "gpos/tools/unity/live.py"
CORE = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/"

MUTATIONS = [
    # --- the input grammar (GPOS side)
    ("an unsaved Scene's null id is accepted", "authoring", [(AU, "    if m.group(2) == ZERO_GUID:\n", "    if False:\n")]),
    ("an asset id is accepted as a Scene object", "authoring", [(AU, '    if m.group(1) != "2":\n', "    if False:\n")]),
    ("an id with a trailing newline is accepted", "authoring", [(AU, "    m = GLOBAL_ID.fullmatch(value)\n", "    m = GLOBAL_ID.match(value)\n")]),
    ("an empty Scene path is not LIVE_SCENE_NOT_SAVED", "authoring", [
        (AU, '        raise InputProblem("LIVE_SCENE_NOT_SAVED", "an unsaved Scene has no path',
         '        raise InputProblem("INVALID_TOOL_REQUEST", "an unsaved Scene has no path')]),
    ("a Scene path may climb out of Assets/", "authoring", [(AU, ' or "/../" in value or "/./" in value', ' or "/./" in value')]),
    ("keep_world needs no transform chain", "authoring", [
        (AU, '            if args["expected_transform_chain_token"] is None:\n', "            if False:\n")]),
    ("keep_world needs no new-parent chain", "authoring", [
        (AU, '            if args["parent"] is not None and args["expected_new_parent_chain_token"] is None:\n',
         "            if False:\n")]),
    ("a keep-local move takes chain tokens", "authoring", [
        (AU, '            _forbidden(args, "expected_transform_chain_token", "with keep_world=false")\n', "")]),
    ("create under a parent needs no parent token", "authoring", [
        (AU, '            if args["expected_parent_token"] is None:\n', "            if False:\n")]),
    ("uint8 accepts 256..511", "authoring", [(AU, '"uint8": (0, 2 ** 8 - 1)', '"uint8": (0, 2 ** 9 - 1)')]),
    ("float32 accepts out-of-range numbers", "authoring", [(AU, " or not math.isfinite(v) or abs(v) > FLOAT32_MAX:", " or not math.isfinite(v):")]),
    ("a non-unit quaternion is accepted", "authoring", [(AU, ") - 1.0) > QUATERNION_TOLERANCE:", ") - 1.0) > 1e9:")]),
    ("int64 accepts a lossy JSON number", "authoring", [
        (AU, "        if not isinstance(value, str) or not DECIMAL.fullmatch(value) or not low <= int(value) <= high:",
         "        if not DECIMAL.fullmatch(str(value)) or not low <= int(value) <= high:")]),
    ("duplicate JSON keys are accepted in values", "authoring", [
        (AU, '            if k in out:\n                raise ValueError("duplicate key")\n            out[k] = v\n        return out\n    try:\n        parsed',
         '            out[k] = v\n        return out\n    try:\n        parsed')]),
    ("property values are not validated by kind", "authoring", [
        (AU, '        args["value"] = property_value(args["kind"], args["value"])\n', "        pass\n")]),
    # --- whole-string grammars (alpha.17): a value ending in LF or CRLF is refused
    ("a token may end in a newline", "authoring", [(AU, "    if not TOKEN.fullmatch(_text(name, value)):", "    if not TOKEN.match(_text(name, value)):")]),
    ("the catalog digest may end in a newline", "authoring", [(AU, "    if not DIGEST.fullmatch(_text(name, value)):", "    if not DIGEST.match(_text(name, value)):")]),
    ("a Scene path may end in a newline", "authoring", [(AU, "not SCENE_PATH.fullmatch(value)", "not SCENE_PATH.match(value)")]),
    ("a type id may end in a newline", "authoring", [(AU, "    if not TYPE_ID.fullmatch(_text(name, value)):", "    if not TYPE_ID.match(_text(name, value)):")]),
    ("a property path may end in a newline", "authoring", [(AU, "    if not PROPERTY_PATH.fullmatch(_text(name, value))", "    if not PROPERTY_PATH.match(_text(name, value))")]),
    ("a 64-bit decimal may end in a newline", "authoring", [(AU, "not DECIMAL.fullmatch(value)", "not DECIMAL.match(value)")]),
    ("a whole-number input may end in a newline", "authoring", [(AU, 'if not re.fullmatch(r"-?(0|[1-9][0-9]{0,9})", value)', 'if not re.match(r"-?(0|[1-9][0-9]{0,9})$", value)')]),
    ("static flags may end in a newline", "authoring", [(AU, 'if not re.fullmatch(r"0|[1-9][0-9]{0,9}", value)', 'if not re.match(r"(0|[1-9][0-9]{0,9})$", value)')]),
    ("a transaction id may end in a newline", "authoring", [(BI, "    if not isinstance(txn_id, str) or not TXN_ID.fullmatch(txn_id):", "    if not isinstance(txn_id, str) or not TXN_ID.match(txn_id):")]),
    ("a record's start time may end in a newline", "authoring", [(BI, 'not UTC.fullmatch(record["started_at"])', 'not UTC.match(record["started_at"])')]),
    ('C#: an object id may end in a newline', "core", [(CORE + 'ObjectIds.cs', 'SceneObject = new Regex("^GlobalObjectId_V1-2-([0-9a-f]{32})-([0-9]{1,20})-([0-9]{1,20})\\\\z");', 'SceneObject = new Regex("^GlobalObjectId_V1-2-([0-9a-f]{32})-([0-9]{1,20})-([0-9]{1,20})$");')]),
    ('C#: a Scene path may end in a newline', "core", [(CORE + 'ObjectIds.cs', 'ScenePath = new Regex("^Assets/[^\\\\u0000-\\\\u001f\\\\u007f\\\\\\\\:*?\\"<>|]+\\\\.unity\\\\z");', 'ScenePath = new Regex("^Assets/[^\\\\u0000-\\\\u001f\\\\u007f\\\\\\\\:*?\\"<>|]+\\\\.unity$");')]),
    ('C#: a token may end in a newline', "core", [(CORE + 'ObjectIds.cs', 'Token = new Regex("^[0-9a-f]{32}\\\\z");', 'Token = new Regex("^[0-9a-f]{32}$");')]),
    ('C#: a digest may end in a newline', "core", [(CORE + 'ObjectIds.cs', 'Digest = new Regex("^[0-9a-f]{64}\\\\z");', 'Digest = new Regex("^[0-9a-f]{64}$");')]),
    ('C#: a type id may end in a newline', "core", [(CORE + 'ObjectIds.cs', 'TypeId = new Regex("^[A-Za-z0-9_.\\\\-]{1,128}::[A-Za-z_][A-Za-z0-9_.+]{0,255}\\\\z");', 'TypeId = new Regex("^[A-Za-z0-9_.\\\\-]{1,128}::[A-Za-z_][A-Za-z0-9_.+]{0,255}$");')]),
    ('C#: a property path may end in a newline', "core", [(CORE + 'PropertyRules.cs', 'PathGrammar = new Regex("^[A-Za-z_][A-Za-z0-9_]*(\\\\.[A-Za-z_][A-Za-z0-9_]*){0,5}\\\\z");', 'PathGrammar = new Regex("^[A-Za-z_][A-Za-z0-9_]*(\\\\.[A-Za-z_][A-Za-z0-9_]*){0,5}$");')]),
    ('C#: a PPtr type may end in a newline', "core", [(CORE + 'PropertyRules.cs', 'PPtr = new Regex("^PPtr<\\\\$?([A-Za-z_][A-Za-z0-9_]*)>\\\\z");', 'PPtr = new Regex("^PPtr<\\\\$?([A-Za-z_][A-Za-z0-9_]*)>$");')]),
    # (No mutation of the 64-bit decimal grammar's end anchor: long/ulong.TryParse with no whitespace styles refuse a
    # trailing LF or CRLF on their own, so that mutant is equivalent; AuthoringGrammarsMatchTheWholeString covers it.)
    ('C#: a 32-hex id may end in a newline', "core", [(CORE + 'Protocol.cs', 'Hex32 = new Regex("^[0-9a-f]{32}\\\\z");', 'Hex32 = new Regex("^[0-9a-f]{32}$");')]),
    ('C#: a 16-hex key may end in a newline', "core", [(CORE + 'Protocol.cs', 'Hex16 = new Regex("^[0-9a-f]{16}\\\\z");', 'Hex16 = new Regex("^[0-9a-f]{16}$");')]),
    ('C#: an owner may end in a newline', "core", [(CORE + 'Protocol.cs', 'Owner = new Regex("^[A-Z][A-Z_]*:[A-Za-z0-9][A-Za-z0-9._@-]{0,63}\\\\z");', 'Owner = new Regex("^[A-Z][A-Z_]*:[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$");')]),
    ('C#: a timestamp may end in a newline', "core", [(CORE + 'Protocol.cs', 'Utc = new Regex(@"^\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d(\\.\\d{1,7})?Z\\z");', 'Utc = new Regex(@"^\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d(\\.\\d{1,7})?Z$");')]),
    # --- results (GPOS side)
    ("a reverted mutation reports no mutation", "authoring", [
        (AU, "mutation_performed=mutating and started, data=", "mutation_performed=False, data=")]),
    ("a failure after a possible start reports no mutation", "authoring", [
        (AU, "mutation_performed=mutating and unknown_start, data=", "mutation_performed=False, data=")]),
    ("an unknown outcome reports no mutation", "authoring", [
        (AU, '{"request_id": r.request_id}, mutation_performed=mutating)', '{"request_id": r.request_id}, mutation_performed=False)')]),
    ("a read reports a mutation", "authoring", [
        (AU, "        return AdapterOutcome(ok=True, mutation_performed=mutating, data=out, diagnostics=diags)",
         "        return AdapterOutcome(ok=True, mutation_performed=True, data=out, diagnostics=diags)")]),
    ("a withdrawn request is not reported as never executed", "authoring", [
        (AU, "    if r.outcome == ipc.WITHDRAWN:\n", "    if False:\n")]),
    ("authoring runs on a session that is not LIVE", "authoring", [(AU, "    if cls != ls.LIVE:\n", "    if False:\n")]),
    ("authoring skips the installed-package check", "authoring", [
        (AU, "    manifest = live.require_installed()\n    b = live.bridge(manifest, state)\n    r = live.call(live.channel(), COMMANDS[cap]",
         "    manifest = lv.bi.load_manifest()\n    b = live.bridge(manifest, state)\n    r = live.call(live.channel(), COMMANDS[cap]")]),
    ("an authoring conflict maps to another code", "authoring", [
        (LV, '"AUTHORING_CONFLICT": "LIVE_AUTHORING_CONFLICT"', '"AUTHORING_CONFLICT": "LIVE_AUTHORING_REFUSED"')]),
    ("a value Unity did not keep maps to a protocol error", "authoring", [
        (LV, '"VALUE_NOT_APPLIED": "LIVE_VALUE_INVALID"', '"VALUE_NOT_APPLIED": "LIVE_PROTOCOL_ERROR"')]),
    ("an incomplete rollback maps to a restored failure", "authoring", [
        (LV, '"ROLLBACK_INCOMPLETE": "LIVE_ROLLBACK_INCOMPLETE"', '"ROLLBACK_INCOMPLETE": "LIVE_AUTHORING_FAILED"')]),
    ("an earlier released bridge counts as installed", "authoring", [(LV, "        if state == bi.PREVIOUS_STATE:\n            raise Refused(", "        if False:\n            raise Refused(")]),
    # --- release history and the upgrade transaction
    ("the history manifest's pin is not checked", "authoring", [(BI, '        if manifest["package_digest"] != pinned:\n', "        if False:\n")]),
    ("the pinned 1.0.0 digest is not the frozen one", "authoring", [
        (BI, '"546b3cfbe4d41234d10450efacbb3397812d106a813dee5b3284903624a68c66"', '"546b3cfbe4d41234d10450efacbb3397812d106a813dee5b3284903624a68c67"')]),
    ("an earlier release is not recognised", "authoring", [
        (BI, '    return place[len("EXACT_"):] if place.startswith("EXACT_") else None', "    return None")]),
    ("extra directories pass as a release", "authoring", [
        (BI, '        if on_disk - directories(manifest["files"]):\n            continue\n', "")]),
    ("the placed package is not verified before the backup goes", "authoring", [
        (BI, '    if differences(places["canonical"], manifest):\n        raise UpgradeIncomplete("the installed bridge is not the audited bridge after the move",',
         '    if False:\n        raise UpgradeIncomplete("the installed bridge is not the audited bridge after the move",')]),
    ("unknown content in a backup is removed", "authoring", [
        (BI, '    if state not in (f"EXACT_{version}", f"PARTIAL_{version}"):\n        raise UpgradeIncomplete(',
         '    if False:\n        raise UpgradeIncomplete(')]),
    ("an unknown staging directory is discarded", "authoring", [
        (BI, '    if state in (f"EXACT_{BRIDGE_VERSION}", f"PARTIAL_{BRIDGE_VERSION}"):\n        _remove_known(places["staging"]',
         '    if True:\n        _remove_known(places["staging"]')]),
    ("recovery restores an unknown backup", "authoring", [
        (BI, "    if c == PLACE_ABSENT and b == old_exact:\n", "    if c == PLACE_ABSENT and b != PLACE_ABSENT:\n")]),
    ("recovery trusts a record for another project", "authoring", [(BI, '    if record["project_key"] != key:\n', "    if False:\n")]),
    ("recovery trusts a record with extra keys", "authoring", [
        (BI, "    if not isinstance(record, dict) or set(record) != TXN_KEYS or", "    if not isinstance(record, dict) or not TXN_KEYS <= set(record) or")]),
    ("transaction places accept any id", "authoring", [
        (BI, '    if not isinstance(txn_id, str) or not TXN_ID.fullmatch(txn_id):\n        raise ValueError("a transaction id', '    if False:\n        raise ValueError("a transaction id')]),
    ("a record's id is not validated", "authoring", [
        (BI, '    if not isinstance(record["txn_id"], str) or not TXN_ID.fullmatch(record["txn_id"]):\n', "    if False:\n")]),
    ("a linked runtime area is followed", "authoring", [
        (BI, "        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):\n            raise OSError(f\"{current} is not a real directory\")",
         "        if False:\n            raise OSError(f\"{current} is not a real directory\")")]),
    ("an upgrade runs in an open project", "authoring", [
        (LV, '    lock = project / "Temp" / "UnityLockfile"\n    if lock.exists() or lock.is_symlink():\n        return _refuse(cap, "ENGINE_PROJECT_LOCKED", "the Unity project is open (Temp/UnityLockfile exists); the bridge "\n                                                     "is installed or upgraded',
         '    lock = project / "Temp" / "UnityLockfile"\n    if False:\n        return _refuse(cap, "ENGINE_PROJECT_LOCKED", "the Unity project is open (Temp/UnityLockfile exists); the bridge "\n                                                     "is installed or upgraded')]),
    ("an incomplete upgrade is reported as success", "authoring", [
        (LV, '    return _refuse(cap, "LIVE_BRIDGE_UPGRADE_INCOMPLETE", f"{exc}; nothing was changed.',
         '    return AdapterOutcome(ok=True, data=data) or _refuse(cap, "LIVE_BRIDGE_UPGRADE_INCOMPLETE", f"{exc}; nothing was changed.')]),
    # --- the bridge's Unity-free core (C#)
    ("C#: an unsaved Scene's null id is accepted", "core", [
        (CORE + "ObjectIds.cs", '            if (any.Success && id.Contains("-" + ZeroGuid + "-"))\n', "            if (false)\n")]),
    ("C#: asset ids are accepted", "core", [(CORE + "ObjectIds.cs", 'new Regex("^GlobalObjectId_V1-2-(', 'new Regex("^GlobalObjectId_V1-[0-9]+-(')]),
    ("C#: token fields are not length-prefixed", "core", [
        (CORE + "Tokens.cs", "            text.Append(value.Length.ToString(CultureInfo.InvariantCulture)).Append(':').Append(value).Append(';');",
         "            text.Append(value).Append(';');")]),
    ("C#: negative zero is not normalized", "core", [(CORE + "Tokens.cs", "            if (value == 0f) value = 0f;   // -0 and +0 are one value\n", "")]),
    ("C#: the catalog digest ignores DisallowMultipleComponent", "core", [(CORE + "Tokens.cs", "                 .Bool(e.DisallowMultiple).Bool(e.RunsInEditMode)", "                 .Bool(e.RunsInEditMode)")]),
    ("C#: the catalog digest ignores ExecuteAlways", "core", [(CORE + "Tokens.cs", "                 .Bool(e.DisallowMultiple).Bool(e.RunsInEditMode)", "                 .Bool(e.DisallowMultiple)")]),
    ("C#: the catalog digest ignores which types are required", "core", [(CORE + "Tokens.cs", "                foreach (var r in requires) b.Field(r);\n", "")]),
    ("C#: requirements are not canonical in the digest", "core", [
        (CORE + "Tokens.cs", "                var requires = e.Requires.Distinct().OrderBy(r => r, StringComparer.Ordinal).ToList();",
         "                var requires = e.Requires.ToList();")]),
    ("C#: m_Script is writable", "core", [(CORE + "PropertyRules.cs", '            "m_Script", "m_GameObject",', '            "m_GameObject",')]),
    ("C#: m_GameObject is writable", "core", [(CORE + "PropertyRules.cs", '            "m_Script", "m_GameObject",', '            "m_Script",')]),
    ("C#: array element paths are accepted", "core", [
        (CORE + "PropertyRules.cs", 'new Regex("^[A-Za-z_][A-Za-z0-9_]*(\\\\.[A-Za-z_][A-Za-z0-9_]*){0,5}\\\\z")',
         'new Regex("^[A-Za-z_][A-Za-z0-9_\\\\[\\\\]]*(\\\\.[A-Za-z_][A-Za-z0-9_\\\\[\\\\]]*){0,5}\\\\z")')]),
    ("C#: the type name of a kind is ignored", "core", [
        (CORE + "PropertyRules.cs", "if (kv.Value[0] == propertyType && (kv.Value[1] == null || kv.Value[1] == typeName))",
         "if (kv.Value[0] == propertyType)")]),
    ("C#: an object reference without PPtr is a kind", "core", [
        (CORE + "PropertyRules.cs", 'return kv.Key == "object" && PPtrType(typeName) == null ? null : kv.Key;', "return kv.Key;")]),
    ("C#: int8 accepts the int16 range", "core", [(CORE + "PropertyRules.cs", 'case "int8": p.Long = Whole(v, sbyte.MinValue, sbyte.MaxValue);',
                                                  'case "int8": p.Long = Whole(v, short.MinValue, short.MaxValue);')]),
    ("C#: uint8 accepts -1 (Unity would wrap)", "core", [(CORE + "PropertyRules.cs", 'case "uint8": p.Long = Whole(v, byte.MinValue, byte.MaxValue);',
                                                          'case "uint8": p.Long = Whole(v, -1, byte.MaxValue);')]),
    ("C#: uint32 accepts -1", "core", [(CORE + "PropertyRules.cs", 'case "layermask": p.Long = Whole(v, 0, uint.MaxValue);',
                                        'case "layermask": p.Long = Whole(v, -1, uint.MaxValue);')]),
    ("C#: fractions pass as whole numbers", "core", [(CORE + "PropertyRules.cs", " || d != Math.Floor(d)) throw Invalid(\"a whole number is expected\");",
                                                      ") throw Invalid(\"a whole number is expected\");")]),
    ("C#: float32 overflow becomes infinity", "core", [(CORE + "PropertyRules.cs", " || Math.Abs(d) > float.MaxValue) throw Invalid(\"the number is not a finite 32-bit float\");",
                                                        ") throw Invalid(\"the number is not a finite 32-bit float\");")]),
    ("C#: a non-unit quaternion is accepted", "core", [(CORE + "PropertyRules.cs", "if (Math.Abs(norm - 1.0) > QuaternionTolerance)", "if (Math.Abs(norm - 1.0) > 10)")]),
    ("C#: negative bounds extents are accepted", "core", [
        (CORE + "PropertyRules.cs", '                    if (e[0] < 0 || e[1] < 0 || e[2] < 0) throw Invalid("bounds extents are not negative");\n', "")]),
    ("C#: a lone surrogate is accepted", "core", [
        (CORE + "PropertyRules.cs", '                if (char.IsSurrogate(s[i])) throw Invalid("a string holds an unpaired surrogate");\n', "")]),
    ("C#: 64-bit decimals take leading zeros", "core", [(CORE + "PropertyRules.cs", 'new Regex("^-?(0|[1-9][0-9]{0,19})\\\\z")', 'new Regex("^-?[0-9]{1,20}\\\\z")')]),
    ("C#: authoring runs while playing", "core", [
        (CORE + "Transitions.cs", "            if (pending) return \"PENDING_OPERATION\";\n            return phase == Edit ? null : phase;",
         "            if (pending) return \"PENDING_OPERATION\";\n            return phase == Edit || phase == Playing ? null : phase;")]),
    ("C#: authoring runs with a transition pending", "core", [
        (CORE + "Transitions.cs", "            if (pending) return \"PENDING_OPERATION\";\n            return phase == Edit ? null : phase;",
         "            return phase == Edit ? null : phase;")]),
    ("C#: authoring commands need no session", "core", [
        (CORE + "Protocol.cs", "return new Spec { Session = true, Mutating = mutating, Authoring = true, Args = args };",
         "return new Spec { Session = false, Mutating = mutating, Authoring = true, Args = args };")]),
    ("C#: authoring commands count as Play Mode commands", "core", [
        (CORE + "Protocol.cs", "return Specs[command].Mutating && !Specs[command].Authoring; }", "return Specs[command].Mutating; }")]),
]


ED = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/"
# Editor-side mutations of the bridge (Unity APIs): each runs the real R1 group of tests/test_unity_authoring.py in a
# lab-owned batch Editor (--real; several minutes each).
REAL_MUTATIONS = [
    ("Editor: the keep-world chain of the object is not compared", "real", [
        (ED + "Authoring.cs", '                Expect(Token(a, "expected_transform_chain_token", true), chain, "a Transform from the Scene root down to the object");',
         '                Token(a, "expected_transform_chain_token", true);')]),
    ("Editor: the keep-world chain of the new parent is not compared", "real", [
        (ED + "Authoring.cs", '                    Expect(Token(a, "expected_new_parent_chain_token", true), parentChain, "a Transform from the Scene root down to the new parent");',
         '                    Token(a, "expected_new_parent_chain_token", true);')]),
    ("Editor: set-gameobject ignores a stale object token", "real", [
        (ED + "Authoring.cs", '            Expect(Token(a, "expected_object_token", true), token, "the object");\n            var pre = new PreState();\n            pre.Add("object", id, token);\n            string label',
         '            Token(a, "expected_object_token", true);\n            var pre = new PreState();\n            pre.Add("object", id, token);\n            string label')]),
    ("Editor: a stale subtree token does not stop a delete", "real", [
        (ED + "Authoring.cs", '            Expect(Token(a, "expected_subtree_token", true), subtree, "the object or something below it");',
         '            Token(a, "expected_subtree_token", true);')]),
    ("Editor: hidden serialized state is not in the component token", "real", [
        (ED + "Scene.cs", "            if (it.Next(true))\n            {\n                do\n                {\n                    if (++n > MaxProperties)",
         "            if (it.NextVisible(true))\n            {\n                do\n                {\n                    if (++n > MaxProperties)"),
        (ED + "Scene.cs", "                } while (it.Next(false));\n            }\n            return b.Int(n).Finish();",
         "                } while (it.NextVisible(false));\n            }\n            return b.Int(n).Finish();")]),
    ("Editor: references are hashed by session-local instance", "real", [
        (ED + "Scene.cs", "                    b.Field(ReferenceHash(p));", "                    b.UInt(p.contentHash);")]),
    ("Editor: the subtree token omits component state", "real", [
        (ED + "Scene.cs", "                    b.Field(ComponentToken(c));", "                    b.Field(Catalog.TypeKey(c.GetType()));")]),
    ("Editor: a failed operation is not reverted", "real", [
        (ED + "Authoring.cs", "            try { Undo.RevertAllDownToGroup(group); }", "            try { }")]),
    ("Editor: the restored state is not verified", "real", [
        (ED + "Authoring.cs", "            var current = pre.Current(out restored);", "            var current = pre.Current(out restored);\n            restored = true;")]),
    ("Editor: prefab-instance content can be renamed", "real", [
        (ED + "Authoring.cs", "            else PrefabBoundary(go, SceneObjects.None);\n            if (name != null) ObjectIds.CheckName(name);",
         "            if (name != null) ObjectIds.CheckName(name);")]),
    ("Editor: a hidden property is writable", "real", [
        (ED + "Properties.cs", '            if (!Listed(c, path, visible)) return "HIDDEN";\n', "")]),
    ("Editor: any Scene object satisfies a reference field", "real", [
        (ED + "Properties.cs", "            if (declared == null) return false;\n            if (target is GameObject)",
         "            if (declared != null) return true;\n            if (target is GameObject)")]),
    ("Editor: a written value is not read back", "real", [
        (ED + "Authoring.cs", "                if (q == null || !Properties.Holds(q, value, enumIndex, target))", "                if (q == null)")]),
    ("Editor: a created object's id is allocated after it is recorded", "real", [
        (ED + "Authoring.cs", "                SceneObjects.Id(go);\n                SceneObjects.Id(go.transform);\n", "")]),
    ("Editor: the resolver never scans for a re-created object", "real", [
        (ED + "Scene.cs", "GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) ?? Scan(scene, id);", "GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid);")]),
    ("Editor: obsolete types are catalogued", "real", [
        (ED + "Catalog.cs", "                if (t.IsDefined(typeof(ObsoleteAttribute), false)) continue;\n", "")]),
    ("Editor: requirements are not catalogued", "real", [
        (ED + "Catalog.cs", "                        if (required != null) e.Requires.Add(TypeKey(required));", "                        if (required == null) e.Requires.Add(TypeKey(required));")]),
    ("Editor: authoring runs while playing", "real", [
        (ED + "Commands.cs", '                string busy = Transitions.AuthoringBusy(Transitions.Phase(LiveBridge.Flags()), LoadPending() != null);\n                if (busy != null) throw new Refusal("EDITOR_BUSY", busy);\n',
         "")]),
    ("Editor: set-property writes a Transform", "real", [
        (ED + "Authoring.cs", '            if (c is Transform) throw new Refusal("PROPERTY_UNSUPPORTED", "a Transform is set with set-transform");\n', "")]),
    ("Editor: a required component can be removed", "real", [
        (ED + "Authoring.cs", "                    if (required.IsInstanceOfType(c) && !others.Any(x => x != other && required.IsInstanceOfType(x)))",
         "                    if (false)")]),
]


def lab_editors_under(path):
    """PIDs of Unity processes whose command line names `path` (only the lab Editors of one mutation copy)."""
    out = subprocess.run(["/bin/ps", "-Ao", "pid=,command="], capture_output=True, text=True).stdout
    return [int(line.split(None, 1)[0]) for line in out.splitlines()
            if str(path) in line and "Unity.app/Contents/MacOS/Unity" in line]


def apply(copy, edits):
    for rel, anchor, replacement in edits:
        path = copy / rel
        text = path.read_text()
        if text.count(anchor) != 1:
            return f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
        path.write_text(text.replace(anchor, replacement))
    return None


def run(mutation):
    name, suite, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-authmut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        problem = apply(copy, edits)
        if problem:
            return name, problem
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GPOS_UNITY_TEST_FAST="1", GPOS_SOURCE_GIT_DIR=str(ROOT / ".git"))
        if suite == "real":
            env.pop("GPOS_UNITY_TEST_FAST")
        if any("live_bridge/" in rel for rel, _, _ in edits):
            subprocess.run([sys.executable, "-B", str(copy / "tests" / "generate_live_bridge_manifest.py")],
                           capture_output=True, text=True, timeout=120, env=env, check=True)
        test = "test_unity_live_bridge_core.py" if suite == "core" else "test_unity_authoring.py"
        argv = [sys.executable, "-B", str(copy / "tests" / test)] + (["R1_RealAuthoring"] if suite == "real" else [])
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=2400, env=env)
        except subprocess.TimeoutExpired:
            return name, "CAUGHT"   # a hang is a failure of the suite
        finally:
            for pid in lab_editors_under(tmp):   # never leave a lab Editor of this copy running
                os.kill(pid, 9)
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def anchors():
    problems = []
    for name, _, edits in MUTATIONS + REAL_MUTATIONS:
        for rel, anchor, _ in edits:
            n = (ROOT / rel).read_text().count(anchor)
            if n != 1:
                problems.append(f"{name}: {rel} anchor found {n} times")
    print("\n".join(problems) or f"all anchors of {len(MUTATIONS)} + {len(REAL_MUTATIONS)} mutations apply exactly once")
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    parser.add_argument("--anchors", action="store_true", help="only check that every anchor applies exactly once")
    parser.add_argument("--real", action="store_true", help="run the Editor-side mutations against real lab Editors")
    args = parser.parse_args()
    if args.anchors:
        return anchors()
    selected = [m for m in (REAL_MUTATIONS if args.real else MUTATIONS) if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
