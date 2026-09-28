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
//     (an external program) or stops this Editor process (a crash) — the seam is unreachable from any request;
//   * (Phase 2C-6B2B) prepares the prefab fixture (regular, nested, Variant, package and embedded-asset prefabs, a
//     plain Scene subtree, prefab instances with every override kind), drives Prefab Mode as a Human would (open a
//     prefab, open a nested prefab in context, edit, save, go back, return to the Scenes), dirties a Scene, fills a
//     Scene with many objects, moves an asset and sets the bridge's version-control test seam.
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
        public int count;
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
                case "mark-dirty": EditorUtility.SetDirty(Find(o.target)); return Ok();   // an Editor tool marks an asset dirty
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
                case "setup-prefabs": return SetupPrefabs();
                case "stage-open":       // double-click a prefab: Prefab Mode in isolation
                {
                    var stage = PrefabStageUtility.OpenPrefab(o.path);
                    return Ok("open", stage == null ? "false" : "true");
                }
                case "stage-open-in-context":   // open the nested instance `name` of the current stage in context
                {
                    var current = PrefabStageUtility.GetCurrentPrefabStage();
                    var nested = current.prefabContentsRoot.GetComponentsInChildren<Transform>(true).First(t => t.name == o.name && PrefabUtility.IsAnyPrefabInstanceRoot(t.gameObject)).gameObject;
                    var stage = PrefabStageUtility.OpenPrefab(o.path, nested, PrefabStage.Mode.InContext);
                    return Ok("open", stage == null ? "false" : "true");
                }
                case "stage-edit":       // an Inspector edit inside the current stage (unsaved)
                {
                    var stage = PrefabStageUtility.GetCurrentPrefabStage();
                    var root = stage.prefabContentsRoot;
                    Undo.RecordObject(root.transform, "Human stage edit");
                    root.transform.localScale = new Vector3(float.Parse(o.value, CultureInfo.InvariantCulture), 1, 1);
                    EditorSceneManager.MarkSceneDirty(stage.scene);
                    return Ok("dirty", stage.scene.isDirty ? "true" : "false");
                }
                case "stage-save":       // Ctrl+S in Prefab Mode
                {
                    var stage = PrefabStageUtility.GetCurrentPrefabStage();
                    PrefabUtility.SaveAsPrefabAsset(stage.prefabContentsRoot, stage.assetPath);
                    stage.ClearDirtiness();
                    return Ok();
                }
                case "stage-back": StageUtility.GoBackToPreviousStage(); return Ok();
                case "stage-main": StageUtility.GoToMainStage(); return Ok();
                case "stage-state":      // the current stage and the breadcrumb (Unity's internal stage history, test only)
                {
                    var current = StageUtility.GetCurrentStage();
                    var prefab = PrefabStageUtility.GetCurrentPrefabStage();
                    return Ok("main", current == StageUtility.GetMainStage() ? "true" : "false", "prefab", prefab == null ? "null" : Q(prefab.assetPath),
                              "dirty", prefab != null && prefab.scene.isDirty ? "true" : "false", "history", History().ToString(CultureInfo.InvariantCulture),
                              "auto_save", prefab != null && AutoSave(prefab) ? "true" : "false");
                }
                case "stage-reset":      // the Human leaves Prefab Mode, saving what is unsaved
                {
                    for (int i = 0; i < 8 && StageUtility.GetCurrentStage() != StageUtility.GetMainStage(); i++)
                    {
                        var stage = PrefabStageUtility.GetCurrentPrefabStage();
                        if (stage != null && stage.scene.isDirty)
                        {
                            PrefabUtility.SaveAsPrefabAsset(stage.prefabContentsRoot, stage.assetPath);
                            stage.ClearDirtiness();
                        }
                        StageUtility.GoToMainStage();
                    }
                    return Ok("history", History().ToString(CultureInfo.InvariantCulture));
                }
                case "hide":             // a hidden, NotEditable object (what some Editor tools leave in a Scene)
                {
                    var go = (GameObject)Find(o.target);
                    go.hideFlags = HideFlags.NotEditable;
                    return Ok();
                }
                case "mark-scene-dirty": EditorSceneManager.MarkSceneDirty(SceneManager.GetSceneByPath(o.scene)); return Ok();
                case "many-objects":     // an additive, never saved Scene with `count` empty objects
                {
                    var s = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    var active = SceneManager.GetActiveScene();
                    SceneManager.SetActiveScene(s);
                    for (int i = 0; i < o.count; i++) new GameObject("Many");
                    SceneManager.SetActiveScene(active);
                    return Ok("roots", s.rootCount.ToString(CultureInfo.InvariantCulture));
                }
                case "move-asset": { string e = AssetDatabase.MoveAsset(o.path, o.target); return Ok("error", Q(e)); }
                case "vcs":              // the bridge's version-control test seam (true: as if a provider were active)
                {
                    var resolver = Type.GetType("Gpos.LiveBridge.PrefabResolver, Gpos.LiveBridge.Editor");
                    resolver.GetField("TestVersionControlActive", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, o.value == "true");
                    return Ok();
                }
                case "instance-count":   // how many instances of a prefab the Scene holds
                {
                    var asset = AssetDatabase.LoadMainAssetAtPath(o.path);
                    var s = SceneManager.GetSceneByPath(o.scene);
                    int n = s.GetRootGameObjects().SelectMany(r => r.GetComponentsInChildren<Transform>(true))
                             .Count(t => PrefabUtility.IsOutermostPrefabInstanceRoot(t.gameObject) && PrefabUtility.GetCorrespondingObjectFromSource(t.gameObject) == asset);
                    return Ok("count", n.ToString(CultureInfo.InvariantCulture));
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

        // The number of stages in Unity's breadcrumb (the Scenes stage included), from its internal navigation manager.
        static int History()
        {
            var type = typeof(StageUtility).Assembly.GetType("UnityEditor.SceneManagement.StageNavigationManager");
            if (type == null) return -1;
            object instance = null;
            for (var t = type; t != null && instance == null; t = t.BaseType)
            {
                var p = t.GetProperty("instance", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.FlattenHierarchy);
                if (p != null) instance = p.GetValue(null, null);
            }
            if (instance == null) return -2;
            foreach (var name in new[] { "stageHistory", "m_NavigationHistory" })
            {
                var prop = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                object value = prop != null ? prop.GetValue(instance, null) : null;
                if (value == null)
                {
                    var field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                    value = field != null ? field.GetValue(instance) : null;
                }
                var list = value as System.Collections.ICollection;
                if (list != null) return list.Count;
                if (value != null)
                {
                    var inner = value.GetType().GetMethod("GetHistory", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                    var history = inner != null ? inner.Invoke(value, null) as System.Collections.ICollection : null;
                    if (history != null) return history.Count;
                }
            }
            return -3;
        }

        static bool AutoSave(PrefabStage stage)
        {
            var p = typeof(PrefabStage).GetProperty("autoSave", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return p != null && (bool)p.GetValue(stage, null);
        }

        static void Set(UnityEngine.Object o, string path, UnityEngine.Object value)
        {
            var so = new SerializedObject(o);
            so.FindProperty(path).objectReferenceValue = value;
            so.ApplyModifiedPropertiesWithoutUndo();
        }

        // The prefab fixture a Human would have made (after setup and setup-assets): Plain (a regular prefab with
        // references inside it, duplicate child names and a grandchild), Probe (project callbacks), Outer (a nested
        // instance of Plain), PlainVariant, Pkg (in the embedded package), Embedded (a prefab file with an embedded
        // Mesh); in the Main Scene a plain subtree Source (references inside, to assets and a Sprite), Outside and
        // Leak (a reference out of its subtree), an instance of Plain with every override kind, an instance of Outer
        // with an override of its nested instance, and Holder (a plain parent).
        static string SetupPrefabs()
        {
            if (!AssetDatabase.IsValidFolder("Assets/Made")) AssetDatabase.CreateFolder("Assets", "Made");
            if (!AssetDatabase.IsValidFolder("Packages/com.gpos.fixture-assets/Prefabs")) AssetDatabase.CreateFolder("Packages/com.gpos.fixture-assets", "Prefabs");
            var baseMat = AssetDatabase.LoadMainAssetAtPath("Assets/Materials/Base.mat");
            var sprite = AssetDatabase.LoadAllAssetsAtPath("Assets/Art/hero.png").OfType<Sprite>().First();
            var plain = new GameObject("Plain");
            var refs = plain.AddComponent(TypeOf("PrefabRefs"));
            var a = new GameObject("Child");
            a.transform.SetParent(plain.transform, false);
            var box = a.AddComponent<BoxCollider>();
            var b = new GameObject("Child");
            b.transform.SetParent(plain.transform, false);
            b.AddComponent<SphereCollider>();
            var leaf = new GameObject("Leaf");
            leaf.transform.SetParent(a.transform, false);
            Set(refs, "other", a); Set(refs, "child", b.transform); Set(refs, "box", box); Set(refs, "mat", baseMat);
            var plainAsset = PrefabUtility.SaveAsPrefabAsset(plain, "Assets/Prefabs/Plain.prefab");
            UnityEngine.Object.DestroyImmediate(plain);
            var probe = new GameObject("Probe");
            probe.AddComponent(TypeOf("PrefabProbe"));
            probe.AddComponent(TypeOf("PrefabRefs"));
            var probeAsset = PrefabUtility.SaveAsPrefabAsset(probe, "Assets/Prefabs/Probe.prefab");
            UnityEngine.Object.DestroyImmediate(probe);
            var outer = new GameObject("Outer");
            var nested = (GameObject)PrefabUtility.InstantiatePrefab(plainAsset);
            nested.transform.SetParent(outer.transform, false);
            var outerAsset = PrefabUtility.SaveAsPrefabAsset(outer, "Assets/Prefabs/Outer.prefab");
            UnityEngine.Object.DestroyImmediate(outer);
            var forVariant = (GameObject)PrefabUtility.InstantiatePrefab(plainAsset);
            var variantAsset = PrefabUtility.SaveAsPrefabAsset(forVariant, "Assets/Prefabs/PlainVariant.prefab");
            UnityEngine.Object.DestroyImmediate(forVariant);
            var pkg = new GameObject("Pkg");
            pkg.AddComponent<BoxCollider>();
            var pkgAsset = PrefabUtility.SaveAsPrefabAsset(pkg, "Packages/com.gpos.fixture-assets/Prefabs/Pkg.prefab");
            UnityEngine.Object.DestroyImmediate(pkg);
            var emb = new GameObject("Embedded");
            var embAsset = PrefabUtility.SaveAsPrefabAsset(emb, "Assets/Prefabs/Embedded.prefab");
            UnityEngine.Object.DestroyImmediate(emb);
            AssetDatabase.AddObjectToAsset(new Mesh { name = "Inner" }, embAsset);
            AssetDatabase.SaveAssets();
            var scene = SceneManager.GetSceneByPath("Assets/Scenes/Main.unity");
            var src = new GameObject("Source");
            SceneManager.MoveGameObjectToScene(src, scene);
            var srcRefs = src.AddComponent(TypeOf("PrefabRefs"));
            var kid = new GameObject("Kid");
            kid.transform.SetParent(src.transform, false);
            var kidBox = kid.AddComponent<BoxCollider>();
            var kid2 = new GameObject("Kid2");
            kid2.transform.SetParent(src.transform, false);
            Set(srcRefs, "other", kid); Set(srcRefs, "child", kid2.transform); Set(srcRefs, "box", kidBox); Set(srcRefs, "mat", baseMat);
            Set(srcRefs, "sprite", sprite); Set(srcRefs, "prefab", plainAsset);
            var srcSo = new SerializedObject(srcRefs);
            srcSo.FindProperty("count").intValue = 7;
            srcSo.ApplyModifiedPropertiesWithoutUndo();
            src.transform.localPosition = new Vector3(1, 2, 3);
            var outside = new GameObject("Outside");
            SceneManager.MoveGameObjectToScene(outside, scene);
            var leak = new GameObject("Leak");
            SceneManager.MoveGameObjectToScene(leak, scene);
            Set(leak.AddComponent(TypeOf("PrefabRefs")), "other", outside);
            var bouncy = new PhysicsMaterial("Bouncy");
            AssetDatabase.CreateAsset(bouncy, "Assets/Materials/Bouncy.physicMaterial");
            var unsupported = new GameObject("Unsupported");
            SceneManager.MoveGameObjectToScene(unsupported, scene);
            Set(unsupported.AddComponent(TypeOf("PrefabRefs")), "physics", bouncy);
            var holder = new GameObject("Holder");
            SceneManager.MoveGameObjectToScene(holder, scene);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(plainAsset, scene);
            var instRefs = instance.GetComponent(TypeOf("PrefabRefs"));
            var so = new SerializedObject(instRefs);
            so.FindProperty("count").intValue = 5;                                                     // a property override
            so.ApplyModifiedPropertiesWithoutUndo();
            PrefabUtility.RecordPrefabInstancePropertyModifications(instRefs);
            instance.AddComponent(TypeOf("AuthorSingle"));                                             // an added component
            UnityEngine.Object.DestroyImmediate(instance.transform.GetChild(1).GetComponent<SphereCollider>());   // a removed component
            var added = new GameObject("Added");                                                        // an added GameObject
            added.transform.SetParent(instance.transform, false);
            UnityEngine.Object.DestroyImmediate(instance.transform.GetChild(0).GetChild(0).gameObject);  // a removed GameObject
            var outerInstance = (GameObject)PrefabUtility.InstantiatePrefab(outerAsset, scene);
            var nestedRefs = outerInstance.transform.GetChild(0).GetComponent(TypeOf("PrefabRefs"));
            var nso = new SerializedObject(nestedRefs);
            nso.FindProperty("count").intValue = 9;                                                    // a nested override
            nso.ApplyModifiedPropertiesWithoutUndo();
            PrefabUtility.RecordPrefabInstancePropertyModifications(nestedRefs);
            EditorSceneManager.SaveScene(scene);
            Undo.ClearAll();
            var p = plainAsset;
            return Ok("plain", Q(Id(p)), "plain_refs", Q(Id(p.GetComponent(TypeOf("PrefabRefs"))) ), "plain_transform", Q(Id(p.transform)),
                      "plain_child", Q(Id(p.transform.GetChild(0).gameObject)), "plain_child_box", Q(Id(p.transform.GetChild(0).GetComponent<BoxCollider>())),
                      "plain_child2", Q(Id(p.transform.GetChild(1).gameObject)), "plain_child2_sphere", Q(Id(p.transform.GetChild(1).GetComponent<SphereCollider>())),
                      "plain_leaf", Q(Id(p.transform.GetChild(0).GetChild(0).gameObject)),
                      "probe", Q(Id(probeAsset)), "probe_component", Q(Id(probeAsset.GetComponent(TypeOf("PrefabProbe")))),
                      "probe_refs", Q(Id(probeAsset.GetComponent(TypeOf("PrefabRefs")))),
                      "outer", Q(Id(outerAsset)), "outer_nested", Q(Id(outerAsset.transform.GetChild(0).gameObject)),
                      "outer_nested_refs", Q(Id(outerAsset.transform.GetChild(0).GetComponent(TypeOf("PrefabRefs")))),
                      "variant", Q(Id(variantAsset)), "variant_refs", Q(Id(variantAsset.GetComponent(TypeOf("PrefabRefs")))),
                      "pkg_prefab", Q(Id(pkgAsset)), "pkg_prefab_box", Q(Id(pkgAsset.GetComponent<BoxCollider>())), "embedded", Q(Id(embAsset)),
                      "source", Q(Id(src)), "source_refs", Q(Id(srcRefs)), "source_kid", Q(Id(kid)), "source_kid_box", Q(Id(kidBox)), "source_kid2", Q(Id(kid2)),
                      "outside", Q(Id(outside)), "leak", Q(Id(leak)), "holder", Q(Id(holder)), "unsupported", Q(Id(unsupported)),
                      "instance", Q(Id(instance)), "instance_refs", Q(Id(instRefs)), "instance_child", Q(Id(instance.transform.GetChild(0).gameObject)),
                      "instance_added", Q(Id(added)), "outer_instance", Q(Id(outerInstance)));
        }

        static Type TypeOf(string name)
        {
            foreach (var t in TypeCache.GetTypesDerivedFrom<UnityEngine.Object>())
                if (t.FullName == name) return t;
            throw new ArgumentException("no type " + name);
        }
    }
}
