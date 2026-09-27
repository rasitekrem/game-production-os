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
//     instance, opening an unsaved Scene — and answers each in <project>/Temp/gpos-testkit-out/<id>.json;
//   * (Phase 2C-6B2A) prepares the asset fixture (sprite import, cubemap, 3D texture, prefab, materials, ScriptableObject
//     assets, a second saved Scene), saves or imports one asset as a Human would, counts Material instances, and arms
//     the bridge's test seam AssetAuthoring.AfterStep so that at one named step of an asset command it writes a file
//     (an external program) or stops this Editor process (a crash) — the seam is unreachable from any request.
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
        public string op, target, path, kind, value, parent, scene, name, step, action, text;
        public bool keepWorld;
    }

    [InitializeOnLoad]
    static class Testkit
    {
        static readonly Regex Proposal = new Regex("\"id\":\"([0-9a-f]{32})\"[^}]*?\"owner\":\"(AGENT:testkit-(approve|reject)[^\"]*)\"[^}]*?\"state\":\"PENDING\"");
        static MethodInfo decide;
        static FieldInfo afterStep;
        static string triggers, outputs;
        static Op armed;

        static string Project { get { return Path.GetFullPath(Path.Combine(Application.dataPath, "..")); } }

        // The armed action runs once, at the named step of the next asset command that reaches it.
        static void OnStep(string step, string assetPath)
        {
            var a = armed;
            if (a == null || a.step != step) return;
            armed = null;
            string target = Path.Combine(Project, a.path ?? assetPath ?? "");
            switch (a.action)
            {
                case "crash": System.Diagnostics.Process.GetCurrentProcess().Kill(); break;
                case "write": Directory.CreateDirectory(Path.GetDirectoryName(target)); File.WriteAllText(target, a.text ?? ""); break;
                case "append": File.AppendAllText(target, a.text ?? ""); break;
                case "throw": throw new InvalidOperationException("testkit: injected failure at " + step);
                case "write-scratch":   // an unknown file appears inside the transaction's scratch folder
                {
                    var dirs = Directory.GetDirectories(Path.Combine(Project, "Assets"), "GposAssetTxn-*");
                    if (dirs.Length != 1) throw new InvalidOperationException("testkit: expected one scratch folder, found " + dirs.Length);
                    File.WriteAllText(Path.Combine(dirs[0], a.path), a.text ?? "");
                    break;
                }
                case "collide-scratch": // the transaction's scratch folder name is taken before GPOS creates it
                {
                    string dir = Project;
                    while (dir != null && !Directory.Exists(Path.Combine(dir, ".game", "gpos"))) dir = Path.GetDirectoryName(dir);
                    var records = Directory.GetFiles(Path.Combine(dir, ".game", "gpos-runtime", "unity", "asset-create-txn"), "*.json", SearchOption.AllDirectories)
                                           .OrderBy(f => File.GetLastWriteTimeUtc(f)).ToList();
                    string txn = Path.GetFileNameWithoutExtension(records.Last());
                    Directory.CreateDirectory(Path.Combine(Project, "Assets", "GposAssetTxn-" + txn));
                    File.WriteAllText(Path.Combine(Project, "Assets", "GposAssetTxn-" + txn, "mine.txt"), a.text ?? "");
                    break;
                }
            }
        }

        static Testkit()
        {
            if (AssetDatabase.IsAssetImportWorkerProcess() || !Application.isBatchMode) return;
            var bridge = Type.GetType("Gpos.LiveBridge.LiveBridge, Gpos.LiveBridge.Editor");
            var approvals = Type.GetType("Gpos.LiveBridge.Approvals, Gpos.LiveBridge.Editor");
            if (bridge == null || approvals == null) { Debug.LogError("[gpos-testkit] the bridge assembly is missing"); return; }
            bridge.GetMethod("Activate", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            decide = approvals.GetMethod("Decide", BindingFlags.Static | BindingFlags.NonPublic);
            var assets = Type.GetType("Gpos.LiveBridge.AssetAuthoring, Gpos.LiveBridge.Editor");
            if (assets != null) afterStep = assets.GetField("AfterStep", BindingFlags.Static | BindingFlags.NonPublic);
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
                case "arm":              // one-shot: at step `step` of the next asset command, do `action`
                {
                    if (afterStep == null) throw new ArgumentException("the bridge has no asset step seam");
                    armed = o;
                    afterStep.SetValue(null, (Action<string, string>)OnStep);
                    return Ok();
                }
                case "disarm": armed = null; if (afterStep != null) afterStep.SetValue(null, null); return Ok();
                case "armed": return Ok("armed", armed == null ? "false" : "true");
                case "setup-assets": return SetupAssets();
                case "save-asset":       // Ctrl+S on one asset (the Human's own save)
                {
                    var target = Find(o.target);
                    AssetDatabase.SaveAssetIfDirty(target);
                    return Ok("dirty", EditorUtility.IsDirty(target) ? "true" : "false");
                }
                case "import": AssetDatabase.ImportAsset(o.path, ImportAssetOptions.ForceUpdate); return Ok();
                case "dirty": return Ok("dirty", EditorUtility.IsDirty(Find(o.target)) ? "true" : "false");
                case "material-instances":
                    return Ok("count", Resources.FindObjectsOfTypeAll<Material>().Count(m => !EditorUtility.IsPersistent(m)).ToString(CultureInfo.InvariantCulture));
                case "shared-materials":
                {
                    var r = (Renderer)Find(o.target);
                    return Ok("ids", "[" + string.Join(",", r.sharedMaterials.Select(m => Q(Id(m) ?? ""))) + "]");
                }
                case "asset-guid": return Ok("guid", Q(AssetDatabase.AssetPathToGUID(o.path, AssetPathToGUIDOptions.OnlyExistingAssets)));
                case "id-of": { var a = AssetDatabase.LoadMainAssetAtPath(o.path); return Ok("id", a == null ? "null" : Q(Id(a))); }
                case "second-scene":     // File > New Scene (additive), one object, saved; the first Scene stays active
                {
                    var active = SceneManager.GetActiveScene();
                    var s = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    var go = new GameObject(o.name ?? "Other");
                    SceneManager.MoveGameObjectToScene(go, s);
                    EditorSceneManager.SaveScene(s, o.scene);
                    SceneManager.SetActiveScene(active);
                    return Ok("id", Q(Id(go)), "scene", Q(s.path));
                }
                case "active-scene": return Ok("path", Q(SceneManager.GetActiveScene().path));
                case "save-open-scenes": EditorSceneManager.SaveOpenScenes(); return Ok();
                case "close-scene": EditorSceneManager.CloseScene(SceneManager.GetSceneByPath(o.scene), true); return Ok();
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

        // The asset fixture a Human would have made in the Editor: a sprite import, a cubemap and a 3D texture, a prefab
        // with a root and an inner child, two materials, ScriptableObject assets (one with a sub-asset), materials in the
        // embedded fixture package (one in an Editor folder), and Scene objects that hold asset references and renderers.
        static string SetupAssets()
        {
            foreach (var f in new[] { "Materials", "Data", "Prefabs", "Art" })
                if (!AssetDatabase.IsValidFolder("Assets/" + f)) AssetDatabase.CreateFolder("Assets", f);
            var ti = (TextureImporter)AssetImporter.GetAtPath("Assets/Art/hero.png");
            ti.textureType = TextureImporterType.Sprite;
            ti.spriteImportMode = SpriteImportMode.Single;
            ti.SaveAndReimport();
            var cube = new Cubemap(8, TextureFormat.RGBA32, false);
            AssetDatabase.CreateAsset(cube, "Assets/Art/Sky.cubemap");
            var vol = new Texture3D(4, 4, 4, TextureFormat.RGBA32, false);
            AssetDatabase.CreateAsset(vol, "Assets/Art/Volume.asset");
            var gpos = Shader.Find("GPOS/Test");
            var baseMat = new Material(gpos);
            AssetDatabase.CreateAsset(baseMat, "Assets/Materials/Base.mat");
            var other = new Material(Shader.Find("Standard"));
            AssetDatabase.CreateAsset(other, "Assets/Materials/Other.mat");
            var pkg = new Material(Shader.Find("Standard"));
            AssetDatabase.CreateAsset(pkg, "Packages/com.gpos.fixture-assets/Materials/PkgMat.mat");
            var edm = new Material(Shader.Find("Standard"));
            AssetDatabase.CreateAsset(edm, "Packages/com.gpos.fixture-assets/Editor/EdMat.mat");
            var config = ScriptableObject.CreateInstance(TypeOf("GameConfig"));
            AssetDatabase.CreateAsset(config, "Assets/Data/Existing.asset");
            var plain = ScriptableObject.CreateInstance(TypeOf("PlainData"));
            AssetDatabase.CreateAsset(plain, "Assets/Data/Plain.asset");
            var callback = ScriptableObject.CreateInstance(TypeOf("CallbackData"));
            AssetDatabase.CreateAsset(callback, "Assets/Data/Callback.asset");
            var multi = ScriptableObject.CreateInstance(TypeOf("GameConfig"));
            AssetDatabase.CreateAsset(multi, "Assets/Data/Multi.asset");
            var part = ScriptableObject.CreateInstance(TypeOf("PlainData"));
            part.name = "Part";
            AssetDatabase.AddObjectToAsset(part, multi);
            var box = new GameObject("Box");
            box.AddComponent<BoxCollider>();
            box.AddComponent<MeshFilter>().sharedMesh = Resources.GetBuiltinResource<Mesh>("Cube.fbx");
            box.AddComponent<MeshRenderer>().sharedMaterial = other;
            var inner = new GameObject("Inner");
            inner.transform.SetParent(box.transform, false);
            inner.AddComponent<SphereCollider>();
            var prefab = PrefabUtility.SaveAsPrefabAsset(box, "Assets/Prefabs/Box.prefab");
            UnityEngine.Object.DestroyImmediate(box);
            AssetDatabase.SaveAssets();
            var scene = SceneManager.GetSceneByPath("Assets/Scenes/Main.unity");
            var refs = new GameObject("Refs");
            var refsComponent = refs.AddComponent(TypeOf("AssetRefs"));
            var audio = refs.AddComponent<AudioSource>();
            var slots = new GameObject("Slots");
            slots.AddComponent<MeshFilter>().sharedMesh = Resources.GetBuiltinResource<Mesh>("Cube.fbx");
            var slotRenderer = slots.AddComponent<MeshRenderer>();
            slotRenderer.sharedMaterial = other;
            var none = new GameObject("NoSlots");
            var noneRenderer = none.AddComponent<MeshRenderer>();
            noneRenderer.sharedMaterials = new Material[0];
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
            EditorSceneManager.SaveScene(scene);
            Undo.ClearAll();
            var sprite = AssetDatabase.LoadAllAssetsAtPath("Assets/Art/hero.png").OfType<Sprite>().First();
            var model = AssetDatabase.LoadMainAssetAtPath("Assets/Models/cube.obj");
            var modelMesh = AssetDatabase.LoadAllAssetsAtPath("Assets/Models/cube.obj").OfType<Mesh>().First();
            var ids = new List<string> {
                "checker", Q(Id(AssetDatabase.LoadMainAssetAtPath("Assets/Art/checker.png"))),
                "hero_texture", Q(Id(AssetDatabase.LoadMainAssetAtPath("Assets/Art/hero.png"))), "hero_sprite", Q(Id(sprite)),
                "sky", Q(Id(cube)), "volume", Q(Id(vol)), "beep", Q(Id(AssetDatabase.LoadMainAssetAtPath("Assets/Audio/beep.wav"))),
                "model", Q(Id(model)), "model_mesh", Q(Id(modelMesh)), "prefab", Q(Id(prefab)),
                "prefab_collider", Q(Id(prefab.GetComponent<BoxCollider>())), "prefab_renderer", Q(Id(prefab.GetComponent<MeshRenderer>())),
                "prefab_inner", Q(Id(prefab.transform.GetChild(0).gameObject)),
                "prefab_inner_collider", Q(Id(prefab.transform.GetChild(0).GetComponent<SphereCollider>())),
                "base", Q(Id(baseMat)), "other", Q(Id(other)), "pkg_mat", Q(Id(pkg)), "editor_mat", Q(Id(edm)),
                "config", Q(Id(config)), "plain", Q(Id(plain)), "callback", Q(Id(callback)), "multi", Q(Id(multi)), "part", Q(Id(part)),
                "shader", Q(Id(gpos)), "standard", Q(Id(Shader.Find("Standard"))),
                "scene_asset", Q(Id(AssetDatabase.LoadMainAssetAtPath("Assets/Scenes/Main.unity"))),
                "script", Q(Id(AssetDatabase.LoadMainAssetAtPath("Assets/AssetScripts/GameConfig.cs"))),
                "folder", Q(Id(AssetDatabase.LoadMainAssetAtPath("Assets/Data"))),
                "refs", Q(Id(refsComponent)), "audio", Q(Id(audio)), "slots", Q(Id(slotRenderer)), "no_slots", Q(Id(noneRenderer)),
                "instance_renderer", Q(Id(instance.GetComponent<MeshRenderer>())) };
            return Ok(ids.ToArray());
        }

        static Type TypeOf(string name)
        {
            foreach (var t in TypeCache.GetTypesDerivedFrom<UnityEngine.Object>())
                if (t.FullName == name) return t;
            throw new ArgumentException("no type " + name);
        }
    }
}
