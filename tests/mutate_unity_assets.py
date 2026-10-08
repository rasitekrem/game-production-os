#!/usr/bin/env python3
"""Bounded mutation harness for Unity live asset references and asset authoring (Phase 2C-6B2A).

    python3 tests/mutate_unity_assets.py [--jobs N] [--only TEXT] [--anchors] [--real]

Each mutation breaks exactly one guarantee in a temporary copy of the repository and runs one suite there:

    "assets"     tests/test_unity_assets.py with GPOS_UNITY_TEST_FAST=1 (fake bridge, source boundaries; no Unity)
    "authoring"  tests/test_unity_authoring.py with GPOS_UNITY_TEST_FAST=1 (renderer inputs, references, history)
    "core"       tests/test_unity_live_bridge_core.py (the bridge's C# core, compiled with Unity's bundled Mono)
    "real3"      (--real) R3_RealAssets of tests/test_unity_assets.py in a lab-owned batch Editor
    "real4"      (--real) R4_RealCreationRecovery: the lab Editor is stopped at exact creation steps

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

import mutation_gate as gate

ROOT = Path(__file__).resolve().parent.parent
AS, AU, LV = "gpos/tools/unity/assets.py", "gpos/tools/unity/authoring.py", "gpos/tools/unity/live.py"
AD, BI = "gpos/tools/unity/adapter.py", "gpos/tools/unity/bridge_install.py"
CORE = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/"
RULES = CORE + "AssetRules.cs"
ED = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/"
AA, AR = ED + "AssetAuthoring.cs", ED + "AssetResolver.cs"

MUTATIONS = [
    # --- the input grammar (GPOS side)
    ("an asset id may end in a newline", "assets", [(AU, "    m = ASSET_ID.fullmatch(value)\n", "    m = ASSET_ID.match(value)\n")]),
    ("an asset id may carry a prefab id", "assets", [(AU, r'ASSET_ID = re.compile(r"^GlobalObjectId_V1-([134])-([0-9a-f]{32})-(\d{1,20})-0$")',
                                                      r'ASSET_ID = re.compile(r"^GlobalObjectId_V1-([134])-([0-9a-f]{32})-(\d{1,20})-(\d{1,20})$")')]),
    ("built-in GUIDs are not tied to built-in ids", "assets", [(AU, '    if (m.group(1) == "4") != (m.group(2) in BUILTIN_GUIDS):\n', "    if False:\n")]),
    ("a zero GUID or an overflowing file id is accepted", "assets", [(AU, "    if int(m.group(3)) >= 2 ** 64 or m.group(2) == ZERO_GUID:\n", "    if False:\n")]),
    ("a new asset may be written outside Assets/", "assets", [(AS, '        if not value.startswith("Assets/"):\n', "        if False:\n")]),
    ("a new asset may have another extension", "assets", [(AS, "        if not value.endswith(ext):\n", "        if False:\n")]),
    ("a dot folder may be written into", "assets", [(AS, 's in (".", "..") or s.startswith((".", " ")) or s.endswith((".", " "))', 's in (".", "..") or s.endswith((".", " "))')]),
    ("special folders may be written into", "assets", [(AS, "            if s.lower() in SPECIAL_FOLDERS:\n", "            if False:\n")]),
    ("a GPOS scratch folder may be a destination", "assets", [(AS, "            if s.lower().startswith(SCRATCH_PREFIX):\n", "            if False:\n")]),
    ("a file name may end in a newline", "assets", [(AS, "        if not STEM.fullmatch(parts[-1][:-len(ext)]):", "        if not STEM.match(parts[-1][:-len(ext)]):")]),
    ("a folder name may end in a newline", "assets", [(AS, "            if (not SEGMENT.fullmatch(s) or", "            if (not SEGMENT.match(s) or")]),
    ("a path may be deeper than the bound", "assets", [(AS, "        if not 3 <= len(parts) <= MAX_FOLDERS + 2:\n", "        if not 3 <= len(parts):\n")]),
    ("material values are not validated", "assets", [(AS, '        args["value"] = material_value(args["kind"], args["value"])\n', "        pass\n")]),
    ("a material texture may name a Scene object", "assets", [(AS, '            au.asset_id("value", value)\n    return value\n\n\ndef asset_value',
                                                               '            au.reference_id("value", value)\n    return value\n\n\ndef asset_value')]),
    ("an asset may reference a Scene object", "assets", [(AS, "        if not au.ASSET_ID.fullmatch(value):\n", "        if False:\n")]),
    ("the KINDS table takes a query", "assets", [(AS, '    if cap == ASSET_TYPES and args["catalog"] == "KINDS" and', '    if False and')]),
    ("an unknown input is accepted", "assets", [(AS, "        if k not in INPUTS[cap]:\n            raise", "        if False:\n            raise")]),
    ("a lookup source is not closed", "assets", [(AS, '"source": _closed(SOURCES)', '"source": au.query')]),
    ("the Renderer slot bound is 8", "authoring", [(AU, "MAX_RENDERER_SLOT = 7", "MAX_RENDERER_SLOT = 8")]),
    ('an empty material does not clear a slot', "authoring", [(AU, '    return None if _text(name, value) == "" else asset_id(name, value)', "    return asset_id(name, value)")]),
    ("an object reference may not name an asset", "authoring", [(AU, "    return asset_id(name, value) if ASSET_ID.fullmatch(value) else object_id(name, value)",
                                                                 "    return object_id(name, value)")]),
    # --- results (GPOS side)
    ("a targeted import reports no mutation", "assets", [(AU, "mutation_performed=mutating and started, data=", "mutation_performed=False, data=")]),
    ("uncertain persistence is not OUTCOME_UNKNOWN", "assets", [(AU, '    if resp["code"] == "PERSISTENCE_UNKNOWN":\n', "    if False:\n")]),
    ("uncertain persistence reports no mutation", "assets", [(AU, '"point was reached, so nothing is rolled back or retried",\n                          details, mutation_performed=mutating,',
                                                              '"point was reached, so nothing is rolled back or retried",\n                          details, mutation_performed=False,')]),
    ("an asset conflict maps to another code", "assets", [(LV, '"ASSET_CONFLICT": "LIVE_ASSET_CONFLICT"', '"ASSET_CONFLICT": "LIVE_AUTHORING_CONFLICT"')]),
    ("an incomplete creation maps to another code", "assets", [(LV, '"CREATE_INCOMPLETE": "LIVE_ASSET_CREATE_INCOMPLETE"', '"CREATE_INCOMPLETE": "LIVE_ASSET_EXISTS"')]),
    ("a dirty asset maps to another code", "assets", [(LV, '"ASSET_DIRTY": "LIVE_ASSET_DIRTY"', '"ASSET_DIRTY": "LIVE_ASSET_CONFLICT"')]),
    ("recovered creations are not reported", "assets", [(AS, '    diags = _recovered(cap, data.get("recovered"))\n', "    diags = []\n")]),
    ("recovered creations are not reported on a refusal", "assets", [(AS, '    if refused and isinstance(data, dict) and data.get("recovered"):\n', "    if False:\n")]),
    ("a creation is not reported", "assets", [(AS, '        diags.append(lv._diag("LIVE_ASSET_CREATED"', '        (lv._diag("LIVE_ASSET_CREATED"')]),
    ("asset reads report a mutation", "assets", [(AS, "    result = au.outcome(cap, r, commands=COMMANDS, read_only=READ_ONLY,", "    result = au.outcome(cap, r, commands=COMMANDS, read_only=(),")]),
    ("asset capabilities are not dispatched", "assets", [(AD, "        if cap in assets.CAPABILITY_IDS:\n            return assets.execute", "        if False:\n            return assets.execute")]),
    ("asset writes get the short timeout", "assets", [(AD, "_asset_write = TimeoutPolicy(default=60.0, maximum=300.0)", "_asset_write = TimeoutPolicy(default=30.0, maximum=120.0)")]),
    ("bridge 1.1.0 is not an upgradable release", "authoring", [(BI, '    "1.1.0": ("gpos.unity.live/2", "00af2b3afccaea2700b6de3fedb5e1c2cae11b990c9640b68d7133b49f383394"),\n', "")]),
    ("the pinned 1.1.0 digest is not the frozen one", "authoring", [(BI, '"00af2b3afccaea2700b6de3fedb5e1c2cae11b990c9640b68d7133b49f383394"', '"00af2b3afccaea2700b6de3fedb5e1c2cae11b990c9640b68d7133b49f383395"')]),
    # --- the bridge's asset sources (the boundary scan)
    ("Editor: SaveAssets saves every dirty asset", "assets", [(AA, "            try { AssetDatabase.SaveAssetIfDirty(o); }", "            try { AssetDatabase.SaveAssets(); }")]),
    ("Editor: the import happens before the dirty check", "assets", [
        (AA, "            string path = r.Path, id = r.Id;\n            if (EditorUtility.IsDirty(r.Object))",
         "            string path = r.Path, id = r.Id;\n            AssetDatabase.ImportAsset(path);\n            if (EditorUtility.IsDirty(r.Object))")]),
    ("Editor: a global Refresh", "assets", [(AA, "            AssetDatabase.ImportAsset(path);\n            Step(\"import\", path);", "            AssetDatabase.Refresh();\n            Step(\"import\", path);")]),
    ("Editor: the final path may be deleted", "assets", [(AA, "                if (!AssetDatabase.DeleteAsset(t.TempPath) || AssetFiles.Present(t.TempPath) || AssetFiles.Present(t.TempPath + \".meta\")) return null;\n                done",
                                                            "                if (!AssetDatabase.DeleteAsset(t.FinalPath) || AssetFiles.Present(t.TempPath) || AssetFiles.Present(t.TempPath + \".meta\")) return null;\n                done")]),
    ("Editor: the destination is not re-checked before the move", "assets", [(AA, '            Step("pre-move", path);\n            CheckFolder(folder);\n            CheckAbsent(folder, path);\n',
                                                                               '            Step("pre-move", path);\n')]),
    # --- the bridge's Unity-free asset core (C#)
    ("C#: an asset id may end in a newline", "core", [(RULES, 'Asset = new Regex("^GlobalObjectId_V1-([134])-([0-9a-f]{32})-([0-9]{1,20})-0\\\\z");',
                                                       'Asset = new Regex("^GlobalObjectId_V1-([134])-([0-9a-f]{32})-([0-9]{1,20})-0$");')]),
    ("C#: an asset id may carry a prefab id", "core", [(RULES, 'Asset = new Regex("^GlobalObjectId_V1-([134])-([0-9a-f]{32})-([0-9]{1,20})-0\\\\z");',
                                                        'Asset = new Regex("^GlobalObjectId_V1-([134])-([0-9a-f]{32})-([0-9]{1,20})-[0-9]{1,20}\\\\z");')]),
    ("C#: a Scene object is an asset", "core", [(RULES, 'Asset = new Regex("^GlobalObjectId_V1-([134])-', 'Asset = new Regex("^GlobalObjectId_V1-([1234])-')]),
    ("C#: a built-in id may name any GUID", "core", [(RULES, "            if (type == 4 && guid != DefaultResourcesGuid && guid != BuiltinExtraGuid)\n", "            if (false)\n")]),
    ("C#: a built-in GUID may be named by another type", "core", [(RULES, "            if (type != 4 && (guid == DefaultResourcesGuid || guid == BuiltinExtraGuid))\n", "            if (false)\n")]),
    ("C#: a zero GUID is an asset", "core", [(RULES, '            if (guid == ObjectIds.ZeroGuid) throw new Refusal("OBJECT_REFUSED", "the asset id has no asset GUID");\n', "")]),
    ("C#: a new asset may be written outside Assets/", "core", [(RULES, '            if (!path.StartsWith("Assets/", StringComparison.Ordinal)) throw Invalid(', '            if (false) throw Invalid(')]),
    ("C#: a new asset may have another extension", "core", [(RULES, '            if (!path.EndsWith(ext, StringComparison.Ordinal)) throw Invalid(', '            if (false) throw Invalid(')]),
    ("C#: special folders may be written into", "core", [(RULES, "                if (Special.Contains(s.ToLowerInvariant())) throw Invalid(", "                if (false) throw Invalid(")]),
    ("C#: a scratch folder may be a destination", "core", [(RULES, "                if (s.StartsWith(ScratchPrefix, StringComparison.OrdinalIgnoreCase)) throw Invalid(", "                if (false) throw Invalid(")]),
    ("C#: a dot folder may be written into", "core", [(RULES, ' s.StartsWith(".", StringComparison.Ordinal) ||', "")]),
    ("C#: a file name may end in a newline", "core", [(RULES, 'Stem = new Regex("^[A-Za-z0-9_()\\\\-]([A-Za-z0-9 _()\\\\-]{0,62}[A-Za-z0-9_()\\\\-])?\\\\z");',
                                                        'Stem = new Regex("^[A-Za-z0-9_()\\\\-]([A-Za-z0-9 _()\\\\-]{0,62}[A-Za-z0-9_()\\\\-])?$");')]),
    ("C#: a folder name may end in a newline", "core", [(RULES, 'Segment = new Regex("^[A-Za-z0-9 _().,+\\\\-]{1,64}\\\\z");',
                                                          'Segment = new Regex("^[A-Za-z0-9 _().,+\\\\-]{1,64}$");')]),
    ("C#: the temporary asset has another name", "core", [(RULES, '            return ScratchFolder(txnId) + "/" + StemOf(finalPath, ext) + ext;', '            return ScratchFolder(txnId) + "/temp" + ext;')]),
    ("C#: the asset token ignores the dirty flag", "core", [(RULES, ".Field(memory).Bool(dirty)\n", ".Field(memory)\n")]),
    ("C#: the asset token ignores the .meta file", "core", [(RULES, "                .Field(file).Field(meta).Finish();", "                .Field(file).Finish();")]),
    ("C#: a shader range is not enforced", "core", [(RULES, "                    if (m.Float < rangeMin || m.Float > rangeMax)\n", "                    if (false)\n")]),
    ("C#: a fractional int is accepted", "core", [(RULES, "                    if (d != Math.Floor(d) || d < int.MinValue || d > int.MaxValue) throw Invalid(\"a 32-bit whole number is expected\");",
                                                    "                    if (d < int.MinValue || d > int.MaxValue) throw Invalid(\"a 32-bit whole number is expected\");")]),
    ("C#: a texture value is not an id", "core", [(RULES, "                    if (m.TextureId != null) AssetIds.Check(m.TextureId);\n", "")]),
    ("C#: any texture dimension fits", "core", [(RULES, '            return declared == "Any" || declared == actual;', "            return true;")]),
    ("C#: hidden shader properties are writable", "core", [(RULES, '            if (hideInInspector) return "HIDDEN";\n', "")]),
    ("C#: per-renderer shader properties are writable", "core", [(RULES, '            if (perRendererData) return "PER_RENDERER_DATA";\n', "")]),
    ("C#: a renderer slot may be appended", "core", [(RULES, "            if (size == 0 && slot == 0) return CreateFirst;", "            if (slot == size) return CreateFirst;")]),
    ("C#: the renderer slot bound is gone", "core", [(RULES, "            if (slot < 0 || slot > MaxSlot) throw", "            if (slot < 0) throw")]),
    ("C#: the SO digest ignores creatable", "core", [(RULES, "                 .Bool(e.ScriptMapped).Bool(e.Creatable)", "                 .Bool(e.ScriptMapped)")]),
    ("C#: the SO digest ignores the script mapping", "core", [(RULES, "                 .Bool(e.ScriptMapped).Bool(e.Creatable)", "                 .Bool(e.Creatable)")]),
    ("C#: the SO digest ignores the menu", "core", [(RULES, ".Field(e.MenuName ?? \"\").Field(e.FileName ?? \"\");", ".Field(e.FileName ?? \"\");")]),
    ("C#: the SO digest depends on order", "core", [(RULES, "            var ordered = entries.OrderBy(e => e.TypeId, StringComparer.Ordinal).ToList();", "            var ordered = entries.ToList();")]),
    ("C#: the shader digest ignores range limits", "core", [(RULES, "                     .Float(p.RangeMin).Float(p.RangeMax).Field(p.Dimension ?? \"\");", "                     .Field(p.Dimension ?? \"\");")]),
    ("C#: the shader digest ignores the texture dimension", "core", [(RULES, "                     .Float(p.RangeMin).Float(p.RangeMax).Field(p.Dimension ?? \"\");", "                     .Float(p.RangeMin).Float(p.RangeMax);")]),
    ("C#: the shader digest ignores flags", "core", [(RULES, ".Bool(p.HideInInspector).Bool(p.PerRendererData).Bool(p.NonModifiableTexture)", "")]),
    ("C#: a record may name another project", "core", [(RULES, '            if (t.ProjectKey != projectKey || !Protocol.Hex16.IsMatch(t.ProjectKey ?? "")) throw Bad(', '            if (false) throw Bad(')]),
    ("C#: a record's id need not be its file name", "core", [(RULES, " || t.TxnId != fileTxnId) throw Bad(", ") throw Bad(")]),
    ("C#: a record's final path is not validated", "core", [(RULES, "            try { AssetPaths.CheckWritePath(t.FinalPath, t.Ext); }", "            try { }")]),
    ("C#: a record may hold identity before proof", "core", [(RULES, '            else if (t.Guid != null || t.GlobalId != null || t.Type != null) throw Bad("identity is recorded before it was proven");\n', "")]),
    ("C#: a record's id need not match its GUID", "core", [(RULES, '            if (t.GlobalId != "GlobalObjectId_V1-3-" + t.Guid + "-"', '            if (t.GlobalId == "GlobalObjectId_V1-3-" + t.Guid + "-"')]),
    ("C#: record hashes need not match the phase", "core", [(RULES, "                if (phase >= from ? value == null || !Hex64.IsMatch(value) : value != null) throw Bad(", "                if (false) throw Bad(")]),
    ("C#: a record may have extra keys", "core", [(RULES, "            if (d == null || d.Count != Keys.Length || Keys.Any(k => !d.ContainsKey(k))) throw Bad(", "            if (d == null || Keys.Any(k => !d.ContainsKey(k))) throw Bad(")]),
    ("C#: create-material is not an asset command", "core", [(CORE + "Protocol.cs", 'AssetMutations = { "create-material", ', 'AssetMutations = { ')]),
]

# Editor-side mutations of the bridge (Unity APIs): each runs a real group of tests/test_unity_assets.py in a
# lab-owned batch Editor (--real; several minutes each).
REAL_MUTATIONS = [
    ("Editor: a dirty asset is edited and saved", "real3", [(AA, "            if (EditorUtility.IsDirty(r.Object))\n                throw new Refusal(\"ASSET_DIRTY\", \"the asset has unsaved changes",
                                                             "            if (false)\n                throw new Refusal(\"ASSET_DIRTY\", \"the asset has unsaved changes")]),
    ("Editor: no targeted import", "real3", [(AA, "            AssetDatabase.ImportAsset(path);\n            Step(\"import\", path);", "            Step(\"import\", path);")]),
    ("Editor: a stale asset token is not compared", "real3", [(AA, "            if (before.Token != expected)\n", "            if (false)\n")]),
    ("Editor: the file is not re-checked before the save", "real3", [(AA, "            if (fileNow != before.File || metaNow != before.Meta)\n", "            if (false)\n")]),
    ("Editor: SaveAssets saves every dirty asset", "real3", [(AA, "            try { AssetDatabase.SaveAssetIfDirty(o); }", "            try { AssetDatabase.SaveAssets(); }")]),
    ("Editor: a failed edit is not reverted", "real3", [(AA, "            try { Undo.RevertAllDownToGroup(group); }", "            try { }")]),
    ("Editor: hidden shader properties are writable", "real3", [(AA, "            if (p.Refusal != null) throw new Refusal(\"PROPERTY_UNSUPPORTED\", \"the shader property cannot be written", "            if (false) throw new Refusal(\"PROPERTY_UNSUPPORTED\", \"the shader property cannot be written")]),
    ("Editor: a Sprite passes as its Texture", "real3", [(AA, "                if (t.Kind == AssetKinds.Sprite)\n                    throw new Refusal(",
                                                           "                if (t.Kind == AssetKinds.Sprite) t = AssetResolver.Classify(((Sprite)t.Object).texture, SceneObjects.Id(((Sprite)t.Object).texture));\n                if (false)\n                    throw new Refusal(")]),
    ("Editor: the texture dimension is not checked", "real3", [(AA, "                if (!MaterialRules.DimensionMatches(p.Dimension, texture.dimension.ToString()))\n", "                if (false)\n")]),
    ("Editor: an internal prefab object is referenced", "real3", [(AR, "            if (go != null) return main && go.transform.parent == null ? RootKind(go) : null;", "            if (go != null) return RootKind(go.transform.root.gameObject);")]),
    ("Editor: Editor-only assets are referenced", "real3", [(AR, "            if (source != AssetKinds.Builtin && EditorOnlyPath(path)) throw Refused(", "            if (false) throw Refused(")]),
    ("Editor: any asset satisfies a reference field", "real3", [(ED + "Properties.cs", '            if (declared == "Object") return true;\n            for', '            return true;\n            for')]),
    ("Editor: m_Resource takes any asset", "real3", [(ED + "Properties.cs", '            return owner is AudioSource && path == "m_Resource" ? AssetKinds.Audio : null;', "            return null;")]),
    ("Editor: the renderer slot reads Renderer.material", "real3", [(ED + "Authoring.cs", "                if (operation == RendererSlots.CreateFirst) slots.arraySize = 1;",
                                                                     "                var instantiated = renderer.material;\n                if (operation == RendererSlots.CreateFirst) slots.arraySize = 1;")]),
    ("Editor: the renderer token is not compared", "real3", [(ED + "Authoring.cs", '            Expect(Token(a, "expected_component_token", true), token, "the renderer");', '            Token(a, "expected_component_token", true);')]),
    ("Editor: unknown content in the scratch folder is overwritten", "real3", [(AA, '            if (listing == null || listing.Count != 0) throw new Refusal("AUTHORING_FAILED", "unknown content appeared in the scratch folder before the asset was created");\n', "")]),
    ("Editor: unknown content in the scratch folder is removed", "real3", [(AA, '            if (s.Scratch != "EMPTY") return "holds content GPOS did not create";\n', "")]),
    ("Editor: earlier creations are not recovered", "real3", [(AA, "            var recovered = CreateTxns.RecoverAll();", "            var recovered = new List<object>();")]),
    ("Editor: the scratch folder is not created exclusively", "real3", [
        (AA, "            if (AssetFiles.Present(scratch) || AssetFiles.Present(scratch + \".meta\"))\n                throw new Refusal(\"CREATE_INCOMPLETE\", \"the transaction's scratch folder already exists; nothing was written\");\n", ""),
        (AA, "            if (mkdir(AssetFiles.Full(scratch), Convert.ToInt32(\"755\", 8)) != 0)\n", "            if (Directory.CreateDirectory(AssetFiles.Full(scratch)) == null)\n")]),
    ("Editor: no record before the creation starts", "real4", [(AA, "                CheckAbsent(folder, path);\n                CreateTxns.Write(txn);\n", "                CheckAbsent(folder, path);\n")]),
    ("Editor: the temporary asset's proof is not recorded", "real4", [(AA, "            txn.Phase = CreateTxn.TempProven;\n            CreateTxns.Write(txn);\n", "            txn.Phase = CreateTxn.TempProven;\n")]),
    ("Editor: recovery removes a modified temporary asset", "real4", [(AA, '            if (s.Temp == "EXACT" && s.Final == "ABSENT")\n            {\n                if (!AssetDatabase.DeleteAsset(t.TempPath)',
                                                                        '            if (s.Temp != "ABSENT" && s.Final == "ABSENT")\n            {\n                if (!AssetDatabase.DeleteAsset(t.TempPath)')]),
    ("Editor: recovery accepts a modified final asset", "real4", [(AA, '(s.Final == "EXACT" || s.Final == "ABSENT" || s.Final == "OTHER")', '(s.Final != "UNKNOWN")')]),
]


def lab_editors_under(path):
    """PIDs of Unity processes whose command line names `path` (only the lab Editors of one mutation copy)."""
    out = subprocess.run(["/bin/ps", "-Ao", "pid=,command="], capture_output=True, text=True).stdout
    return [int(line.split(None, 1)[0]) for line in out.splitlines()
            if str(path) in line and "Unity.app/Contents/MacOS/Unity" in line]


def apply(copy, edits):
    for rel, anchor, replacement in edits:
        path = copy / rel
        text = gate.read(path)
        if text.count(anchor) != 1:
            return f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
        gate.write(path, text.replace(anchor, replacement))
    return None


SUITES = {"assets": ("test_unity_assets.py", []), "authoring": ("test_unity_authoring.py", []),
          "core": ("test_unity_live_bridge_core.py", []), "real3": ("test_unity_assets.py", ["R3_RealAssets"]),
          "real4": ("test_unity_assets.py", ["R4_RealCreationRecovery"])}


def run(mutation):
    name, suite, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-assetmut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        problem = apply(copy, edits)
        if problem:
            return name, problem
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GPOS_UNITY_TEST_FAST="1", GPOS_SOURCE_GIT_DIR=str(ROOT / ".git"))
        if suite.startswith("real"):
            env.pop("GPOS_UNITY_TEST_FAST")
        if any("live_bridge/" in rel for rel, _, _ in edits):
            subprocess.run([*gate.PYTHON, "-B", str(copy / "tests" / "generate_live_bridge_manifest.py")],
                           capture_output=True, text=True, timeout=120, env=env, check=True)
        test, groups = SUITES[suite]
        argv = [*gate.PYTHON, "-B", str(copy / "tests" / test)] + groups
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=3000, env=env)
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
            n = gate.read(ROOT / rel).count(anchor)
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
    baselines = [(f"{gate.BASELINE} [{suite}]", suite, []) for suite in sorted({m[1] for m in selected})]
    return gate.qualify(run, selected, args.jobs, baselines)


if __name__ == "__main__":
    sys.exit(main())
