#!/usr/bin/env python3
"""Bounded mutation harness for Unity live prefab authoring (Phase 2C-6B2B).

    python3 tests/mutate_unity_prefabs.py [--jobs N] [--only TEXT] [--anchors] [--real]

Each mutation breaks exactly one guarantee in a temporary copy of the repository and runs one suite there:

    "prefabs"    tests/test_unity_prefabs.py with GPOS_UNITY_TEST_FAST=1 (fake bridge, source boundaries; no Unity)
    "core"       tests/test_unity_live_bridge_core.py (the bridge's C# core, compiled with Unity's bundled Mono)
    "real5"      (--real) R5_RealPrefabs: inspection, creation, instantiation, every edit in a lab-owned batch Editor
    "real6"      (--real) R6_RealPrefabGuards: Prefab Mode, dirty prefab, version control, permissions, races, Scenes
    "real7"      (--real) R7_RealPrefabRecovery: the lab Editor is stopped at exact steps

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
PF, AS, LV = "gpos/tools/unity/prefabs.py", "gpos/tools/unity/assets.py", "gpos/tools/unity/live.py"
AD, BI = "gpos/tools/unity/adapter.py", "gpos/tools/unity/bridge_install.py"
ED = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/"
CORE = ED + "Core/"
RULES, ARULES, PROTO = CORE + "PrefabRules.cs", CORE + "AssetRules.cs", CORE + "Protocol.cs"
PA, PR, AA, AR = ED + "PrefabAuthoring.cs", ED + "PrefabResolver.cs", ED + "AssetAuthoring.cs", ED + "AssetResolver.cs"

MUTATIONS = [
    # --- the input grammar (GPOS side)
    ("a prefab id may be a Scene object", "prefabs", [(PF, r'PREFAB_ID = re.compile(r"^GlobalObjectId_V1-1-', r'PREFAB_ID = re.compile(r"^GlobalObjectId_V1-[12]-')]),
    ("a prefab id may carry a prefab id", "prefabs", [(PF, r'-(\d{1,20})-0$")', r'-(\d{1,20})-\d+$")')]),
    ("a prefab id may end in a newline", "prefabs", [(PF, "    m = PREFAB_ID.fullmatch(value)\n", "    m = PREFAB_ID.match(value)\n")]),
    ("a prefab id may name a built-in GUID", "prefabs", [(PF, " or m.group(1) in au.BUILTIN_GUIDS:", ":")]),
    ("a new prefab may be an .asset", "prefabs", [(PF, 'PREFAB_PATH_EXT = ".prefab"', 'PREFAB_PATH_EXT = ".asset"')]),
    ("a prefab property may reference a Scene object", "prefabs", [(PF, "        if not au.ASSET_ID.fullmatch(value):\n", "        if False:\n")]),
    ("instantiate takes a parent and the roots token", "prefabs", [(PF, '            au._forbidden(args, "expected_scene_roots_token", "with a parent")\n', "")]),
    ("instantiate at the root needs no roots token", "prefabs", [(PF, '            if args["expected_scene_roots_token"] is None:\n', "            if False:\n")]),
    ("an unknown prefab input is accepted", "prefabs", [(PF, "        if k not in INPUTS[cap]:\n            raise", "        if False:\n            raise")]),
    ("path_prefix without a component", "prefabs", [(PF, '    if cap == PREFAB_INSPECT and args["path_prefix"] is not None and args["component"] is None:', "    if False:")]),
    # --- results (GPOS side)
    ("a stage refusal maps to another code", "prefabs", [(LV, '"PREFAB_STAGE_OPEN": "LIVE_PREFAB_STAGE_OPEN"', '"PREFAB_STAGE_OPEN": "LIVE_PREFAB_CONFLICT"')]),
    ("a prefab conflict maps to another code", "prefabs", [(LV, '"PREFAB_CONFLICT": "LIVE_PREFAB_CONFLICT"', '"PREFAB_CONFLICT": "LIVE_AUTHORING_CONFLICT"')]),
    ("a dirty prefab maps to another code", "prefabs", [(LV, '"PREFAB_DIRTY": "LIVE_PREFAB_DIRTY"', '"PREFAB_DIRTY": "LIVE_ASSET_DIRTY"')]),
    ("an unwritable prefab maps to another code", "prefabs", [(LV, '"PREFAB_NOT_EDITABLE": "LIVE_PREFAB_NOT_EDITABLE"', '"PREFAB_NOT_EDITABLE": "LIVE_ASSET_NOT_EDITABLE"')]),
    ("an incomplete prefab creation maps to the asset code", "prefabs", [(LV, '"PREFAB_CREATE_INCOMPLETE": "LIVE_PREFAB_CREATE_INCOMPLETE"', '"PREFAB_CREATE_INCOMPLETE": "LIVE_ASSET_CREATE_INCOMPLETE"')]),
    ("the prefab limit maps to another code", "prefabs", [(LV, '"PREFAB_LIMIT": "LIVE_PREFAB_LIMIT"', '"PREFAB_LIMIT": "LIVE_AUTHORING_LIMIT"')]),
    ("recovery ignores the record kind", "prefabs", [(AS, '"LIVE_PREFAB_CREATE_RECOVERED" if rec.get("kind") == "PREFAB" else "LIVE_ASSET_CREATE_RECOVERED"', '"LIVE_ASSET_CREATE_RECOVERED"')]),
    ("side effects are not disclosed", "prefabs", [(PF, "    if not changed and not scenes:\n        return []\n", "    return []\n")]),
    ("prefab reads report a mutation", "prefabs", [(PF, "    result = au.outcome(cap, r, commands=COMMANDS, read_only=READ_ONLY,", "    result = au.outcome(cap, r, commands=COMMANDS, read_only=(),")]),
    ("prefab capabilities are not dispatched", "prefabs", [(AD, "        if cap in prefabs.CAPABILITY_IDS:\n            return prefabs.execute", "        if False:\n            return prefabs.execute")]),
    ("a creation claims to be undoable", "prefabs", [(AD, '"GPOS-owned scratch folder; the source is not connected; not undoable;', '"GPOS-owned scratch folder; the source is not connected;')]),
    ("bridge 1.2.0 is not an upgradable release", "prefabs", [(BI, '    "1.2.0": ("gpos.unity.live/3", "042379a6413c8b55ce3d6deada529fbdbe609c96b9dace56f256d94428c782ce"),\n', "")]),
    ("the pinned 1.2.0 digest is not the frozen one", "prefabs", [(BI, '"042379a6413c8b55ce3d6deada529fbdbe609c96b9dace56f256d94428c782ce"', '"042379a6413c8b55ce3d6deada529fbdbe609c96b9dace56f256d94428c782cf"')]),
    # --- the bridge's prefab sources (the boundary scan and the order of every guard)
    ("Editor: an edit does not refuse Prefab Mode", "prefabs", [(PA, '            edit.Check(p, target);\n            PrefabResolver.RequireNoStage();\n', "            edit.Check(p, target);\n")]),
    ("Editor: an edit does not refuse a dirty prefab", "prefabs", [(PA, "            if (PrefabResolver.Dirty(p))\n                throw new Refusal(\"PREFAB_DIRTY\", \"the prefab has unsaved changes in the Editor; GPOS never saves them",
                                                                     "            if (false)\n                throw new Refusal(\"PREFAB_DIRTY\", \"the prefab has unsaved changes in the Editor; GPOS never saves them")]),
    ("Editor: an edit ignores version control", "prefabs", [(PA, '            if (vcs != null) throw new Refusal("PREFAB_NOT_EDITABLE", vcs + " (nothing was changed)");\n', "")]),
    ("Editor: an edit ignores OS permissions", "prefabs", [(PA, '            if (os != null) throw new Refusal("PREFAB_NOT_EDITABLE", os + "; GPOS never changes permissions (nothing was changed)");\n', "")]),
    ("Editor: an edit ignores dirty dependent Scenes", "prefabs", [(PA, "            var dirtyDependents = PrefabResolver.DependentScenes(p.Path).Where(f => f.Dirty).Select(f => (object)f.Name).ToList();",
                                                                     "            var dirtyDependents = new List<object>();")]),
    ("Editor: the file is not re-hashed before the save", "prefabs", [(PA, "                if (fileNow != state.File || metaNow != state.Meta)\n", "                if (false)\n")]),
    ("Editor: the contents are not always unloaded", "prefabs", [(PA, "            finally\n            {\n                if (contents != null)\n", "            catch (InvalidOperationException)\n            {\n                if (contents != null)\n")]),
    ("Editor: a creation saves at the final path", "prefabs", [(PA, "PrefabUtility.SaveAsPrefabAsset(source, temp, out success)", "PrefabUtility.SaveAsPrefabAsset(source, path, out success)")]),
    ("Editor: a creation connects its source", "prefabs", [(PA, "PrefabUtility.SaveAsPrefabAsset(source, temp, out success)", "PrefabUtility.SaveAsPrefabAssetAndConnect(source, temp, InteractionMode.AutomatedAction, out success)")]),
    ("Editor: an edit records Undo", "prefabs", [(PA, "so.ApplyModifiedPropertiesWithoutUndo();", "so.ApplyModifiedProperties();")]),
    ("Editor: an unpack hook", "prefabs", [(PA, "                case \"prefab-inspect\": return Inspect(a);", "                case \"prefab-inspect\": return Inspect(a);\n                case \"unpack\": PrefabUtility.UnpackPrefabInstance(null, PrefabUnpackMode.OutermostRoot, InteractionMode.AutomatedAction); return null;")]),
    ("Editor: instance ids are requested after the creation is recorded", "prefabs", [
        (PA, "                    ids = SceneObjects.Ids(made.ToArray());\n                    Undo.RegisterCreatedObjectUndo(go, \"GPOS: instantiate \" + name);",
         "                    Undo.RegisterCreatedObjectUndo(go, \"GPOS: instantiate \" + name);\n                    ids = SceneObjects.Ids(made.ToArray());")]),
    ("Editor: the asset surface authors every kind", "prefabs", [(AR, "            if (r.Kind != AssetKinds.Material && r.Kind != AssetKinds.ScriptableObject) return false;\n", "")]),
    ("Editor: the Scene prefab boundary of a property is gone", "prefabs", [(ED + "Properties.cs", '            if (c != null && SceneObjects.Role(c) != SceneObjects.None) return "PREFAB_BOUNDARY";\n            return CoreRefusal(o, so, p, visible);',
                                                                              "            return CoreRefusal(o, so, p, visible);")]),
    ("Editor: the Scene commands use the prefab property rule", "prefabs", [(ED + "Properties.cs", "return Writable(c, so, path, kind, false);", "return Writable(c, so, path, kind, true);")]),
    ("Editor: the resolver writes", "prefabs", [(PR, "        public static bool Dirty(PrefabInfo p)\n        {", "        public static bool Dirty(PrefabInfo p)\n        {\n            AssetDatabase.ImportAsset(p.Path);")]),
    # --- the bridge's Unity-free prefab core (C#)
    ("C#: a prefab id may be a Scene object", "core", [(RULES, 'Persistent = new Regex("^GlobalObjectId_V1-1-', 'Persistent = new Regex("^GlobalObjectId_V1-[12]-')]),
    ("C#: a prefab id may end in a newline", "core", [(RULES, '-([0-9]{1,20})-0\\\\z");\n        static readonly Regex Any', '-([0-9]{1,20})-0$");\n        static readonly Regex Any')]),
    ("C#: a contents copy of a nested object maps", "core", [(RULES, "if (identifierType != 2 || guid != prefabGuid || prefabId != 0 || fileId == 0) return null;", "if (identifierType != 2 || guid != prefabGuid || fileId == 0) return null;")]),
    ("C#: a contents copy of another prefab maps", "core", [(RULES, "if (identifierType != 2 || guid != prefabGuid || prefabId != 0 || fileId == 0) return null;", "if (identifierType != 2 || prefabId != 0 || fileId == 0) return null;")]),
    ("C#: a record root may be another GUID", "core", [(RULES, "            return Guid(globalId) == guid && FileId(globalId) != \"0\";", "            return FileId(globalId) != \"0\";")]),
    ("C#: a source may reference outside its subtree", "core", [(RULES, "                case SceneOutside:\n", "")]),
    ("C#: a prefab link may be saved", "core", [(RULES, "            if (Links.Contains(top)) return target == Null ? null : PrefabLink;\n", "")]),
    ("C#: any Transform's parent is cut", "core", [(RULES, '            if (top == "m_Father" && rootTransform) return null;', '            if (top == "m_Father") return null;')]),
    ("C#: the GameObject bound is gone", "core", [(RULES, "            if (gameObjects > MaxGameObjects) throw", "            if (false) throw")]),
    ("C#: a nested prefab is in scope", "core", [(RULES, "            if (nested) r.Add(NestedPresent);\n", "")]),
    ("C#: a package prefab is in scope", "core", [(RULES, "            if (source != AssetKinds.Assets) r.Add(InPackage);\n", "")]),
    ("C#: a prefab record may be schema /1", "core", [(ARULES, 'if (s("schema") == Schema ? t.Kind != AssetKinds.Material && t.Kind != AssetKinds.ScriptableObject : t.Kind != AssetKinds.Prefab)',
                                                        'if (t.Kind != AssetKinds.Material && t.Kind != AssetKinds.ScriptableObject && t.Kind != AssetKinds.Prefab)')]),
    ("C#: a prefab record's identity is not checked", "core", [(ARULES, "                    if (!PrefabIds.IsRecordRoot(t.GlobalId, t.Guid)) throw Bad(", "                    if (false) throw Bad(")]),
    ("C#: a prefab record's path is not checked", "core", [(ARULES, "                try { PrefabPaths.CheckWritePath(t.FinalPath); }", "                try { }")]),
    ("C#: the asset path rule accepts a prefab", "core", [(ARULES, 'if (ext != ".mat" && ext != ".asset") throw Invalid(', 'if (ext != ".mat" && ext != ".asset" && ext != ".prefab") throw Invalid(')]),
    ("C#: the kind table gives prefabs an extension", "core", [(ARULES, '{ Prefab, new[] { "t:Prefab", "the root GameObject of a prefab asset", "" } }', '{ Prefab, new[] { "t:Prefab", "the root GameObject of a prefab asset", ".prefab" } }')]),
    ("C#: create-prefab changes a Scene", "core", [(PROTO, 'PrefabAssetMutations = { "create-prefab", ', 'PrefabAssetMutations = { ')]),
    ("C#: an apply command is allowed", "core", [(PROTO, '            { "prefab-inspect", AuthoringSpec(false, "prefab", "component", "path_prefix", "page") },',
                                                  '            { "prefab-inspect", AuthoringSpec(false, "prefab", "component", "path_prefix", "page") },\n            { "apply-prefab", AuthoringSpec(true) },')]),
]

# Editor-side mutations of the bridge (Unity APIs): each runs a real group of tests/test_unity_prefabs.py in a
# lab-owned batch Editor (--real; several minutes each).
REAL_MUTATIONS = [
    ("Editor: Prefab Mode never blocks anything", "real6", [(PR, "            if (AnyStageOpen())\n            {", "            if (false)\n            {")]),
    ("Editor: only the edited prefab's own stage blocks it", "real6", [(PR, "            return StageUtility.GetCurrentStage() != StageUtility.GetMainStage() || PrefabStageUtility.GetCurrentPrefabStage() != null;",
                                                                        "            return false;")]),
    ("Editor: a dirty prefab is edited", "real6", [(PA, "            if (PrefabResolver.Dirty(p))\n                throw new Refusal(\"PREFAB_DIRTY\", \"the prefab has unsaved changes in the Editor; GPOS never saves them",
                                                    "            if (false)\n                throw new Refusal(\"PREFAB_DIRTY\", \"the prefab has unsaved changes in the Editor; GPOS never saves them")]),
    ("Editor: a dirty prefab is instantiated", "real5", [(PA, "            if (PrefabResolver.Dirty(p))\n                throw new Refusal(\"PREFAB_DIRTY\", \"the prefab has unsaved changes in the Editor; GPOS never turns",
                                                          "            if (false)\n                throw new Refusal(\"PREFAB_DIRTY\", \"the prefab has unsaved changes in the Editor; GPOS never turns")]),
    ("Editor: an unwritable prefab is overwritten", "real6", [(PA, '            if (os != null) throw new Refusal("PREFAB_NOT_EDITABLE", os + "; GPOS never changes permissions (nothing was changed)");\n', "")]),
    ("Editor: version control is ignored", "real6", [(PR, "            if (TestVersionControlActive || UnityEditor.VersionControl.Provider.isActive)", "            if (UnityEditor.VersionControl.Provider.isActive)")]),
    ("Editor: the file is not re-hashed before the save", "real6", [(PA, "                if (fileNow != state.File || metaNow != state.Meta)\n", "                if (false)\n")]),
    ("Editor: a stale prefab token is not compared", "real5", [(PA, "                state = PrefabResolver.State(before);\n                if (state.Token != expected)\n", "                state = PrefabResolver.State(before);\n                if (false)\n")]),
    ("Editor: instantiate compares no fresh token", "real5", [(PA, "                state = PrefabResolver.State(fresh);\n                if (state.Token != expected)\n", "                state = PrefabResolver.State(fresh);\n                if (false)\n")]),
    ("Editor: instantiate does not import first", "real6", [(PA, "            AssetDatabase.ImportAsset(p.Path);\n            AssetAuthoring.Step(\"import\", p.Path);", "            AssetAuthoring.Step(\"import\", p.Path);")]),
    ("Editor: an edit does not import first", "real6", [(PA, "            AssetDatabase.ImportAsset(path);\n            AssetAuthoring.Step(\"import\", path);", "            AssetAuthoring.Step(\"import\", path);")]),
    ("Editor: a dirty dependent Scene is ignored", "real6", [(PA, "            if (dirtyDependents.Count > 0)\n", "            if (false)\n")]),
    ("Editor: the dependent Scene scan is not bounded", "real6", [(PR, "                        if (++scanned > PrefabBounds.MaxDependentScan)\n", "                        if (++scanned < 0)\n")]),
    ("Editor: a source reference is not scanned", "real5", [(PA, "                    string why = PrefabReferences.Refusal(it.propertyPath, o == source.transform, Target(it, o, plan.Set));",
                                                             "                    string why = null;")]),
    ("Editor: an instance source becomes a Variant", "real5", [(PA, "                if (SceneObjects.Role(go) != SceneObjects.None || PrefabUtility.IsPartOfAnyPrefab(go))", "                if (false)")]),
    ("Editor: a creation connects its source", "real5", [(PA, "PrefabUtility.SaveAsPrefabAsset(source, temp, out success)", "PrefabUtility.SaveAsPrefabAssetAndConnect(source, temp, InteractionMode.AutomatedAction, out success)")]),
    ("Editor: the destination is not re-checked before the move", "real7", [(PA, "            AssetAuthoring.Step(\"pre-move\", path);\n            Folder(folder);\n            AssetAuthoring.CheckAbsent(folder, path);\n            string valid = AssetDatabase.ValidateMoveAsset(temp, path);\n            if (!string.IsNullOrEmpty(valid)) throw new Refusal(\"ASSET_EXISTS\", \"Unity refuses the destination: \" + valid);\n",
                                                                              "            AssetAuthoring.Step(\"pre-move\", path);\n")]),
    ("Editor: the temporary prefab's proof is not recorded", "real7", [(PA, "            txn.Phase = CreateTxn.TempProven;\n            CreateTxns.Write(txn);\n", "            txn.Phase = CreateTxn.TempProven;\n")]),
    ("Editor: an undecidable prefab record is an asset record", "real7", [(AA, 't.Kind == AssetKinds.Prefab ? "PREFAB_CREATE_INCOMPLETE" : "CREATE_INCOMPLETE"', '"CREATE_INCOMPLETE"')]),
    ("Editor: instance ids are requested after the creation is recorded", "real5", [
        (PA, "                    ids = SceneObjects.Ids(made.ToArray());\n                    Undo.RegisterCreatedObjectUndo(go, \"GPOS: instantiate \" + name);",
         "                    Undo.RegisterCreatedObjectUndo(go, \"GPOS: instantiate \" + name);\n                    ids = SceneObjects.Ids(made.ToArray());")]),
    ("Editor: an edit writes the persistent prefab object", "real5", [(PA, "                var copy = map[id];", "                var copy = target;")]),
    ("Editor: new component ids are not recovered", "real5", [(PA, "                edit.Saved(map, copy);\n", "")]),
    ("Editor: a Sprite passes as a Texture", "real5", [(PA, "                    if (!Properties.Accepts(PropertyRules.PPtrType(prop.type), t))\n                        throw new Refusal(\"VALUE_INVALID\", \"the field does not accept that object's type\");\n", "")]),
    ("Editor: the prefab root is renamed", "real5", [(PA, "                if (name != null && go == p.Root)\n", "                if (false)\n")]),
    ("Editor: the asset surface writes prefabs", "real5", [(AR, "            if (r.Kind != AssetKinds.Material && r.Kind != AssetKinds.ScriptableObject) return false;\n", ""),
                                                            (AR, "            return AssetDatabase.LoadAllAssetsAtPath(r.Path).Length == 1;", "            return AssetDatabase.LoadAllAssetsAtPath(r.Path).Length >= 1;"),
                                                            (ARULES, 'if (ext != ".mat" && ext != ".asset") throw Invalid(', 'if (ext != ".mat" && ext != ".asset" && ext != ".prefab") throw Invalid('),
                                                            (ARULES, '{ Prefab, new[] { "t:Prefab", "the root GameObject of a prefab asset", "" } }', '{ Prefab, new[] { "t:Prefab", "the root GameObject of a prefab asset", ".prefab" } }')]),
    ("Editor: the token omits the dirty flags", "real6", [(PR, "            b.Field(id).Field(p.Ownership[o]).Bool(EditorUtility.IsDirty(o)).UInt((uint)o.hideFlags).Field(hash);",
                                                           "            b.Field(id).Field(p.Ownership[o]).UInt((uint)o.hideFlags).Field(hash);"),
                                                          (PR, "                .Field(s.File).Field(s.Meta).Bool(s.Dirty).Int(p.GameObjects.Count).Int(p.ComponentCount);",
                                                           "                .Field(s.File).Field(s.Meta).Int(p.GameObjects.Count).Int(p.ComponentCount);")]),
    ("Editor: the token is the file hash only", "real6", [(PR, "            s.Token = b.Finish();", "            s.Token = new TokenBuilder(TokenBuilder.PrefabDomain).Field(s.File).Finish();")]),
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


SUITES = {"prefabs": ("test_unity_prefabs.py", []), "core": ("test_unity_live_bridge_core.py", []),
          "real5": ("test_unity_prefabs.py", ["R5_RealPrefabs"]), "real6": ("test_unity_prefabs.py", ["R6_RealPrefabGuards"]),
          "real7": ("test_unity_prefabs.py", ["R7_RealPrefabRecovery"])}


def run(mutation):
    name, suite, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-prefabmut-"))
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
            out = subprocess.run(argv, capture_output=True, text=True, timeout=3600, env=env)
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
