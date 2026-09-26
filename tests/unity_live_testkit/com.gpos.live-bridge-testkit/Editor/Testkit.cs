// TEST ONLY — never installed by GPOS, never part of the audited bridge, only ever copied into disposable synthetic
// projects by tests/test_unity_live.py and tests/test_unity_authoring.py. In a lab-owned batch-mode Editor it:
//   * starts the production bridge (which by design never starts itself in batch mode) by calling its internal
//     activation, the same method the bridge's own constructor calls in a windowed Editor;
//   * presses the production approval method — the one the approval window's buttons call — for proposals whose
//     owner id is test-named ("AGENT:testkit-approve..." approves, "AGENT:testkit-reject..." rejects); any other
//     owner is left for a Human;
//   * obeys trigger files in <project>/Temp/gpos-testkit/: "reload" (script reload), "refresh" (asset refresh),
//     "quit" (Editor exit) and "block-<ms>" (block the main thread), so tests can create reloads, compilations,
//     busy periods and a clean exit on demand;
//   * obeys "op-<id>.json" files (Phase 2C-6B1) standing in for what a Human does in the Editor — an Inspector edit
//     through SerializedObject, a Hierarchy drag, Cmd-Z / Cmd-Shift-Z, creating the saved test Scene and a prefab
//     instance, opening an unsaved Scene — and answers each in <project>/Temp/gpos-testkit-out/<id>.json.
// None of this exists in the production bridge: it has no activation switch, no approval command and no triggers.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Gpos.LiveBridge.Testkit
{
    [Serializable]
    class Op
    {
        public string op, target, path, kind, value, parent, scene, name;
        public bool keepWorld;
    }

    [InitializeOnLoad]
    static class Testkit
    {
        static readonly Regex Proposal = new Regex("\"id\":\"([0-9a-f]{32})\"[^}]*?\"owner\":\"(AGENT:testkit-(approve|reject)[^\"]*)\"[^}]*?\"state\":\"PENDING\"");
        static MethodInfo decide;
        static string triggers, outputs;

        static Testkit()
        {
            if (AssetDatabase.IsAssetImportWorkerProcess() || !Application.isBatchMode) return;
            var bridge = Type.GetType("Gpos.LiveBridge.LiveBridge, Gpos.LiveBridge.Editor");
            var approvals = Type.GetType("Gpos.LiveBridge.Approvals, Gpos.LiveBridge.Editor");
            if (bridge == null || approvals == null) { Debug.LogError("[gpos-testkit] the bridge assembly is missing"); return; }
            bridge.GetMethod("Activate", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            decide = approvals.GetMethod("Decide", BindingFlags.Static | BindingFlags.NonPublic);
            triggers = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Temp", "gpos-testkit"));
            outputs = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Temp", "gpos-testkit-out"));
            EditorApplication.update += Tick;
        }

        static void Tick()
        {
            foreach (Match m in Proposal.Matches(SessionState.GetString("gpos.live.proposals", "")))
                decide.Invoke(null, new object[] { m.Groups[1].Value, m.Groups[3].Value == "approve" });
            if (!Directory.Exists(triggers)) return;
            foreach (var f in Directory.GetFiles(triggers).OrderBy(x => x, StringComparer.Ordinal))
            {
                string name = Path.GetFileName(f);
                if (name.StartsWith(".", StringComparison.Ordinal)) continue;
                string text = File.ReadAllText(f);
                File.Delete(f);
                if (name == "reload") EditorUtility.RequestScriptReload();
                else if (name == "refresh") AssetDatabase.Refresh();
                else if (name == "quit") EditorApplication.Exit(0);
                else if (name.StartsWith("block-", StringComparison.Ordinal)) System.Threading.Thread.Sleep(int.Parse(name.Substring(6)));
                else if (name.StartsWith("op-", StringComparison.Ordinal) && name.EndsWith(".json", StringComparison.Ordinal))
                {
                    string result;
                    try { result = Run(JsonUtility.FromJson<Op>(text)); }
                    catch (Exception e) { result = "{\"ok\":false,\"error\":" + Q(e.GetType().Name + ": " + e.Message) + "}"; }
                    Directory.CreateDirectory(outputs);
                    string tmp = Path.Combine(outputs, "." + name);
                    File.WriteAllText(tmp, result);
                    File.Move(tmp, Path.Combine(outputs, name.Substring(3)));
                }
            }
        }

        static string Q(string s)
        {
            var sb = new StringBuilder("\"");
            foreach (char c in s ?? "")
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                else sb.Append(c);
            }
            return sb.Append('"').ToString();
        }

        static string Id(UnityEngine.Object o) { return o == null ? null : GlobalObjectId.GetGlobalObjectIdSlow(o).ToString(); }

        static UnityEngine.Object Find(string id)
        {
            GlobalObjectId gid;
            if (!GlobalObjectId.TryParse(id, out gid)) throw new ArgumentException("bad id " + id);
            var o = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid);
            if (o == null) throw new ArgumentException("no object " + id);
            return o;
        }

        static float[] Floats(string csv) { return csv.Split(',').Select(x => float.Parse(x, CultureInfo.InvariantCulture)).ToArray(); }

        static string Ok(params string[] pairs)
        {
            var sb = new StringBuilder("{\"ok\":true");
            for (int i = 0; i < pairs.Length; i += 2) sb.Append(',').Append(Q(pairs[i])).Append(':').Append(pairs[i + 1] ?? "null");
            return sb.Append('}').ToString();
        }

        static string Run(Op o)
        {
            switch (o.op)
            {
                case "setup":
                {
                    // A saved Scene with one prefab instance (Crate: root + Child with a BoxCollider), opened alone.
                    Directory.CreateDirectory(Path.Combine(Application.dataPath, "Scenes"));
                    Directory.CreateDirectory(Path.Combine(Application.dataPath, "Prefabs"));
                    var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                    var root = new GameObject("Crate");
                    var child = new GameObject("Child");
                    child.transform.SetParent(root.transform, false);
                    child.AddComponent<BoxCollider>();
                    var asset = PrefabUtility.SaveAsPrefabAsset(root, "Assets/Prefabs/Crate.prefab");
                    UnityEngine.Object.DestroyImmediate(root);
                    var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset, scene);
                    EditorSceneManager.SaveScene(scene, "Assets/Scenes/Main.unity");
                    Undo.ClearAll();
                    var kid = instance.transform.GetChild(0).gameObject;
                    return Ok("scene", Q(scene.path), "prefab_root", Q(Id(instance)), "prefab_child", Q(Id(kid)),
                              "prefab_child_collider", Q(Id(kid.GetComponent<BoxCollider>())), "prefab_root_transform", Q(Id(instance.transform)));
                }
                case "set-serialized":   // what the Inspector does: a SerializedObject edit, recorded for Undo
                {
                    var target = Find(o.target);
                    var so = new SerializedObject(target);
                    var p = so.FindProperty(o.path);
                    if (p == null) throw new ArgumentException("no property " + o.path);
                    switch (o.kind)
                    {
                        case "int": p.intValue = int.Parse(o.value, CultureInfo.InvariantCulture); break;
                        case "float": p.floatValue = float.Parse(o.value, CultureInfo.InvariantCulture); break;
                        case "string": p.stringValue = o.value; break;
                        case "bool": p.boolValue = o.value == "true"; break;
                        case "vector3": { var f = Floats(o.value); p.vector3Value = new Vector3(f[0], f[1], f[2]); break; }
                        case "quaternion": { var f = Floats(o.value); p.quaternionValue = new Quaternion(f[0], f[1], f[2], f[3]); break; }
                        default: throw new ArgumentException("kind " + o.kind);
                    }
                    so.ApplyModifiedProperties();
                    return Ok();
                }
                case "reparent":         // a Hierarchy drag
                {
                    var t = ((GameObject)Find(o.target)).transform;
                    var parent = string.IsNullOrEmpty(o.parent) ? null : ((GameObject)Find(o.parent)).transform;
                    Undo.SetTransformParent(t, parent, o.keepWorld, "Human reparent");
                    return Ok();
                }
                case "add-child":        // GameObject > Create Empty Child
                {
                    var parent = (GameObject)Find(o.target);
                    var go = new GameObject(o.name ?? "HumanChild");
                    Undo.RegisterCreatedObjectUndo(go, "Human create");
                    Undo.SetTransformParent(go.transform, parent.transform, false, "Human create");
                    return Ok("id", Q(Id(go)));
                }
                case "undo": Undo.PerformUndo(); return Ok("group", Q(Undo.GetCurrentGroupName()));
                case "redo": Undo.PerformRedo(); return Ok("group", Q(Undo.GetCurrentGroupName()));
                case "undo-name": return Ok("group", Q(Undo.GetCurrentGroupName()));
                case "scene-state":
                {
                    var s = SceneManager.GetSceneByPath(o.scene);
                    return Ok("loaded", s.isLoaded ? "true" : "false", "dirty", s.isDirty ? "true" : "false", "roots", s.isLoaded ? s.rootCount.ToString() : "0");
                }
                case "exists":
                {
                    GlobalObjectId gid;
                    bool found = GlobalObjectId.TryParse(o.target, out gid) && GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) != null;
                    return Ok("exists", found ? "true" : "false");
                }
                case "read":             // one serialized value as text (test assertions only)
                {
                    var so = new SerializedObject(Find(o.target));
                    var p = so.FindProperty(o.path);
                    if (p == null) throw new ArgumentException("no property " + o.path);
                    string v;
                    switch (p.propertyType)
                    {
                        case SerializedPropertyType.Integer: v = p.type == "ulong" ? p.ulongValue.ToString(CultureInfo.InvariantCulture) : p.longValue.ToString(CultureInfo.InvariantCulture); break;
                        case SerializedPropertyType.Float: v = p.doubleValue.ToString("R", CultureInfo.InvariantCulture); break;
                        case SerializedPropertyType.String: v = p.stringValue; break;
                        case SerializedPropertyType.Boolean: v = p.boolValue ? "true" : "false"; break;
                        case SerializedPropertyType.Enum: v = p.intValue.ToString(CultureInfo.InvariantCulture); break;
                        case SerializedPropertyType.ObjectReference: v = Id(p.objectReferenceValue) ?? ""; break;
                        default: v = p.propertyType.ToString(); break;
                    }
                    return Ok("value", Q(v));
                }
                case "unsaved-scene":    // File > New Scene (additive), never saved, with one object
                {
                    var s = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    var go = new GameObject(o.name ?? "Unsaved");
                    SceneManager.MoveGameObjectToScene(go, s);
                    return Ok("id", Q(Id(go)));
                }
                case "reopen":           // File > Open Scene of the saved Scene (every object gets a new instance)
                {
                    var s = EditorSceneManager.OpenScene(o.scene, OpenSceneMode.Single);
                    return Ok("loaded", s.isLoaded ? "true" : "false");
                }
                case "close-unsaved":
                {
                    for (int i = SceneManager.sceneCount - 1; i >= 0; i--)
                    {
                        var s = SceneManager.GetSceneAt(i);
                        if (s.path.Length == 0) EditorSceneManager.CloseScene(s, true);
                    }
                    return Ok();
                }
            }
            throw new ArgumentException("unknown op " + o.op);
        }
    }
}
