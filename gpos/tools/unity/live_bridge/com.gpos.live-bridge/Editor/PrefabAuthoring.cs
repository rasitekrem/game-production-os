// GPOS live bridge — the prefab commands (bridge 1.3.0): prefab-inspect and prefab-instance-inspect (read only),
// create-prefab, instantiate-prefab and five bounded edits of one regular prefab's own objects. There is no generic
// PrefabUtility call, no apply, revert, unpack, connect, Variant, nested-prefab authoring, child creation or deletion,
// model editing and no Prefab Mode control; no global Refresh, SaveAssets or Scene save.
//
// Creating a prefab (Create) never overwrites anything, never connects the source and is not undoable. The source is a
// completely plain subtree of a saved, loaded Scene whose every serialized reference is null, inside the subtree, a
// reviewed asset or structural (PrefabReferences). It is the alpha.18 creation transaction (CreateTxns, record schema
// /2, kind PREFAB) with the same phases and steps: exclusive scratch folder, SaveAsPrefabAsset into it under the final
// file name, identity proof (TEMP_PROVEN), content proof (structure, values and every reference saved as it is in the
// source; the source unchanged), the final path re-checked, ValidateMoveAsset / MoveAsset, final proof, cleanup.
//
// Instantiating (Instantiate) is Scene authoring: a clean regular prefab without nested prefabs, imported first (from
// the targeted import on the request reports mutation_started), its fresh token compared, then one Undo group in which
// every id of the new instance is requested before the creation is recorded, so Redo brings back the same ids.
//
// Editing an existing prefab (Edit), in this order:
//   resolve the caller's type-1 id to an OWNED object of a mutable prefab; validate the edit read-only
//   refuse an open Prefab Mode stage, a dirty prefab, version control, an OS-unwritable file/.meta/folder
//   refuse when a loaded Scene holding a dependent instance is dirty (bounded scan)                PREFAB_CONFLICT
//   targeted import of that one prefab — from here the request reports mutation_started
//   re-resolve; the fresh whole-prefab token must equal the expected one                            PREFAB_CONFLICT
//   LoadPrefabContents; map every persistent object to its isolated copy by file id; one edit; read it back
//   immediately before saving: no stage, the same prefab, nothing dirty, the same file and .meta bytes
//   commit point: SaveAsPrefabAsset(contents, the same path) — anything uncertain from here on is PERSISTENCE_UNKNOWN
//   UnloadPrefabContents (always); re-resolve and verify the persistent result
// A persistent prefab object is never edited directly and the edit is not undoable.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Gpos.LiveBridge
{
    internal static class PrefabAuthoring
    {
#if UNITY_EDITOR_WIN
        // bridge 1.6.0: no libc on Windows; the commands that use these are not served there (Protocol.WindowsCommands)
        static Refusal NotServed() { return new Refusal("UNKNOWN_COMMAND", "not served by the bridge on Windows (bridge 1.6.0)"); }
        static int mkdir(string path, int mode) { throw NotServed(); }
#else
        [DllImport("libc", SetLastError = true)] static extern int mkdir(string path, int mode);
#endif

        static Request current;

        public static Dictionary<string, object> Run(Request r)
        {
            current = r;
            var a = r.Args;
            switch (r.Command)
            {
                case "prefab-inspect": return Inspect(a);
                case "prefab-instance-inspect": return InstanceInspect(a);
                case "create-prefab": return Create(a);
                case "instantiate-prefab": return Instantiate(a);
                case "set-prefab-gameobject": return Edit(a, "object", new SetGameObjectEdit(a));
                case "set-prefab-transform": return Edit(a, "object", new SetTransformEdit(a));
                case "add-prefab-component": return Edit(a, "object", new AddComponentEdit(a));
                case "remove-prefab-component": return Edit(a, "component", new RemoveComponentEdit());
                case "set-prefab-property": return Edit(a, "component", new SetPropertyEdit(a));
            }
            throw new Refusal("UNKNOWN_COMMAND", "the command is not in the closed allowlist");
        }

        // ------------------------------------------------------------ shared

        static Dictionary<string, object> Started(List<PrefabResolver.SceneFact> scenes, Dictionary<string, object> more = null)
        {
            var d = new Dictionary<string, object> { { "mutation_started", true }, { "import_performed", true }, { "value_persisted", false },
                                                     { "scenes_marked_dirty", PrefabResolver.MarkedDirty(scenes) } };
            if (more != null)
                foreach (var kv in more)
                    if (!d.ContainsKey(kv.Key) || kv.Key == "tokens_now") d[kv.Key] = kv.Value;
            return d;
        }

        // Every GameObject and component of a hierarchy in the order PrefabResolver lists a prefab's objects.
        static List<UnityEngine.Object> Collect(GameObject root)
        {
            var objects = new List<UnityEngine.Object>();
            var stack = new Stack<Transform>();
            stack.Push(root.transform);
            while (stack.Count > 0)
            {
                var t = stack.Pop();
                objects.Add(t.gameObject);
                foreach (var c in t.GetComponents<Component>())
                    if (c != null) objects.Add(c);
                for (int i = t.childCount - 1; i >= 0; i--) stack.Push(t.GetChild(i));
            }
            return objects;
        }

        static Dictionary<string, object> Ref(PrefabInfo p, UnityEngine.Object o)
        {
            var go = SceneObjects.GameObjectOf(o);
            var d = new Dictionary<string, object> { { "id", p.Ids[o] }, { "name", SceneObjects.Clip(go.name, ObjectIds.MaxNameLength) },
                                                     { "ownership", p.Ownership[o] } };
            var c = o as Component;
            if (c == null) d["kind"] = "GAME_OBJECT";
            else
            {
                d["kind"] = "COMPONENT";
                d["type"] = Catalog.TypeKey(c.GetType());
                d["gameobject"] = p.Ids[go];
                var b = c as Behaviour;
                if (b != null) d["enabled"] = b.enabled;
            }
            return d;
        }

        static List<object> Listed(IEnumerable<object> items, int max) { return items.Take(max).ToList(); }

        static Dictionary<string, object> Transform(Transform t)
        {
            return new Dictionary<string, object> {
                { "kind", t is RectTransform ? "RectTransform" : "Transform" },
                { "local_position", SceneObjects.Floats(t.localPosition.x, t.localPosition.y, t.localPosition.z) },
                { "local_rotation", SceneObjects.Floats(t.localRotation.x, t.localRotation.y, t.localRotation.z, t.localRotation.w) },
                { "local_scale", SceneObjects.Floats(t.localScale.x, t.localScale.y, t.localScale.z) } };
        }

        static bool SameRotation(Quaternion q, Quaternion r)
        {
            return Math.Abs(q.x - r.x) <= 1e-6 && Math.Abs(q.y - r.y) <= 1e-6 && Math.Abs(q.z - r.z) <= 1e-6 && Math.Abs(q.w - r.w) <= 1e-6;
        }

        // ------------------------------------------------------------ inspection (never imports, never writes)

        static Dictionary<string, object> Inspect(Dictionary<string, object> a)
        {
            var p = PrefabResolver.Prefab(Authoring.Str(a, "prefab", true));
            string componentId = Authoring.Str(a, "component"), prefix = Authoring.Str(a, "path_prefix");
            if (prefix != null && componentId == null) throw new Refusal("BAD_ARGUMENTS", "path_prefix needs a component");
            if (prefix != null && prefix.Length > PropertyRules.MaxPathLength) throw new Refusal("BAD_ARGUMENTS", "path_prefix is too long");
            int page = Authoring.Int(a, "page", 0, 10000) ?? 0;
            var reasons = p.StaticReasons();
            reasons.AddRange(PrefabResolver.StateReasons(p));
            var d = new Dictionary<string, object> {
                { "prefab", p.ToData() }, { "mutable", reasons.Count == 0 }, { "scope_reasons", reasons.Cast<object>().ToList() },
                { "game_object_count", p.GameObjects.Count }, { "component_count", p.ComponentCount }, { "nested_present", p.Nested },
                { "dirty", PrefabResolver.Dirty(p) }, { "stage_open", PrefabResolver.AnyStageOpen() } };
            if (p.Covered)
            {
                var s = PrefabResolver.State(p);
                d["tokens"] = Authoring.Tokens("prefab", s.Token);
                d["token_scope"] = "PREFAB";
                d["file_sha256"] = s.File;
                d["meta_sha256"] = s.Meta;
            }
            else
            {
                d["tokens"] = Authoring.Tokens("prefab", null);
                d["token_scope"] = "NOT_COVERED";   // a Variant's or nested prefab's content also depends on other files
            }
            if (componentId == null)
            {
                int start = page * PrefabBounds.InspectPage;
                d["page"] = page;
                d["page_size"] = PrefabBounds.InspectPage;
                d["next_page"] = start + PrefabBounds.InspectPage < p.GameObjects.Count ? (object)(page + 1) : null;
                d["objects"] = p.GameObjects.Skip(start).Take(PrefabBounds.InspectPage).Select(go => (object)ObjectData(p, go)).ToList();
                return d;
            }
            PrefabInfo q;
            var c = PrefabResolver.Object(componentId, out q) as Component;
            if (c == null || q.Guid != p.Guid) throw new Refusal("OBJECT_REFUSED", "component names a Component of this prefab");
            string role = PrefabResolver.Role(p, c);
            bool authority = reasons.Count == 0 && role == PrefabScope.Owned;
            string scopeRefusal = reasons.Count > 0 ? reasons[0] : role != PrefabScope.Owned ? PrefabScope.NotOwned : null;
            var entries = Properties.Entries(c, prefix, true);
            foreach (Dictionary<string, object> e in entries)
            {
                bool propertyWritable = (bool)e["writable"];
                e["property_writable"] = propertyWritable;
                e["prefab_mutable"] = authority;
                e["writable"] = propertyWritable && authority;   // what set-prefab-property would accept now
                if (e["refusal"] == null) e["refusal"] = scopeRefusal;
                var id = e.ContainsKey("value") ? e["value"] as string : null;
                if ((string)e["kind"] == "object") e["same_prefab"] = id != null && PrefabIds.IsPersistent(id) && PrefabIds.Guid(id) == p.Guid;
            }
            int from = page * Properties.PageSize;
            d["component"] = Ref(p, c);
            d["count"] = entries.Count;
            d["page"] = page;
            d["page_size"] = Properties.PageSize;
            d["next_page"] = from + Properties.PageSize < entries.Count ? (object)(page + 1) : null;
            d["properties"] = entries.Skip(from).Take(Properties.PageSize).ToList();
            return d;
        }

        static Dictionary<string, object> ObjectData(PrefabInfo p, GameObject go)
        {
            var t = go.transform;
            var components = go.GetComponents<Component>();
            var listed = new List<object>();
            for (int i = 0; i < components.Length; i++)
                listed.Add(components[i] == null ? new Dictionary<string, object> { { "missing_script", true }, { "index", i } } : (object)Ref(p, components[i]));
            var d = Ref(p, go);
            d["parent"] = t.parent == null ? null : p.Ids[t.parent.gameObject];
            d["sibling_index"] = t.GetSiblingIndex();
            d["depth"] = p.Depth[go];
            d["child_count"] = t.childCount;
            d["active_self"] = go.activeSelf;
            d["tag"] = go.tag;
            d["layer"] = go.layer;
            d["static_flags"] = (long)(uint)GameObjectUtility.GetStaticEditorFlags(go);
            d["transform"] = Transform(t);
            d["components"] = listed;
            if (p.Ownership[go] == PrefabScope.NestedRoot)
            {
                var source = PrefabUtility.GetCorrespondingObjectFromSource(go);
                d["nested_source"] = source == null ? null : new Dictionary<string, object> { { "id", SceneObjects.Id(source) }, { "path", AssetDatabase.GetAssetPath(source) } };
            }
            return d;
        }

        static string Status(GameObject root)
        {
            switch (PrefabUtility.GetPrefabInstanceStatus(root))
            {
                case PrefabInstanceStatus.Connected: return "CONNECTED";
                case PrefabInstanceStatus.MissingAsset: return "MISSING_ASSET";
            }
            return "NOT_A_PREFAB";
        }

        static object PersistentId(UnityEngine.Object o) { return o == null ? null : SceneObjects.Id(o); }

        static Dictionary<string, object> InstanceInspect(Dictionary<string, object> a)
        {
            var go = new Resolver().GameObject(Authoring.Str(a, "object", true));
            int page = Authoring.Int(a, "page", 0, 10000) ?? 0;
            string role = SceneObjects.Role(go);
            var d = new Dictionary<string, object> { { "object", SceneObjects.Ref(go) }, { "role", role } };
            if (role == SceneObjects.None) { d["instance"] = null; return d; }
            GameObject root = null;
            for (var t = go.transform; t != null && root == null; t = t.parent)
                if (PrefabUtility.IsPartOfPrefabInstance(t.gameObject)) root = PrefabUtility.GetOutermostPrefabInstanceRoot(t.gameObject);
            if (root == null) { d["instance"] = null; return d; }
            var objects = Collect(root);
            int gameObjects = objects.Count(o => o is GameObject);
            PrefabBounds.Check(gameObjects, objects.Count - gameObjects, 0, "the prefab instance");
            string status = Status(root), path = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(root);
            var ids = SceneObjects.Ids(objects.ToArray());
            var mapping = new List<object>();
            for (int i = 0; i < objects.Count; i++)
            {
                var source = PrefabUtility.GetCorrespondingObjectFromSource(objects[i]);
                mapping.Add(new Dictionary<string, object> { { "id", ids[i] }, { "kind", objects[i] is GameObject ? "GAME_OBJECT" : "COMPONENT" },
                                                             { "source", PersistentId(source) }, { "source_path", source == null ? null : AssetDatabase.GetAssetPath(source) } });
            }
            var overrides = new List<object>();
            if (status == "CONNECTED")
            {
                var mods = PrefabUtility.GetPropertyModifications(root) ?? new PropertyModification[0];
                var addedComponents = PrefabUtility.GetAddedComponents(root);
                var removedComponents = PrefabUtility.GetRemovedComponents(root);
                var addedGameObjects = PrefabUtility.GetAddedGameObjects(root);
                var removedGameObjects = PrefabUtility.GetRemovedGameObjects(root);
                int total = mods.Length + addedComponents.Count + removedComponents.Count + addedGameObjects.Count + removedGameObjects.Count;
                if (total > PrefabBounds.MaxOverrides)
                    throw new Refusal("PREFAB_LIMIT", "the instance has more than " + PrefabBounds.MaxOverrides + " overrides; nothing is listed partially");
                foreach (var m in mods)
                {
                    string value = m.value ?? "";
                    // a target inside a nested instance of the source prefab: the override belongs to that inner object
                    var inner = m.target == null ? null : PrefabUtility.GetCorrespondingObjectFromSource(m.target);
                    overrides.Add(new Dictionary<string, object> {
                        { "kind", "PROPERTY" }, { "target", PersistentId(m.target) }, { "target_path", m.target == null ? null : AssetDatabase.GetAssetPath(m.target) },
                        { "nested", inner != null }, { "nested_source", PersistentId(inner) }, { "nested_source_path", inner == null ? null : AssetDatabase.GetAssetPath(inner) },
                        { "property_path", SceneObjects.Clip(m.propertyPath, PrefabBounds.MaxValueText) }, { "value", SceneObjects.Clip(value, PrefabBounds.MaxValueText) },
                        { "value_truncated", value.Length > PrefabBounds.MaxValueText }, { "reference", PersistentId(m.objectReference) },
                        { "default_override", PrefabUtility.IsDefaultOverride(m) } });
                }
                foreach (var x in addedComponents)
                    overrides.Add(new Dictionary<string, object> { { "kind", "ADDED_COMPONENT" }, { "id", SceneObjects.Id(x.instanceComponent) },
                                                                   { "type", Catalog.TypeKey(x.instanceComponent.GetType()) }, { "game_object", SceneObjects.Id(x.instanceComponent.gameObject) } });
                foreach (var x in removedComponents)
                    overrides.Add(new Dictionary<string, object> { { "kind", "REMOVED_COMPONENT" }, { "source", PersistentId(x.assetComponent) },
                                                                   { "game_object", PersistentId(x.containingInstanceGameObject) } });
                foreach (var x in addedGameObjects)
                    overrides.Add(new Dictionary<string, object> { { "kind", "ADDED_GAME_OBJECT" }, { "id", SceneObjects.Id(x.instanceGameObject) },
                                                                   { "parent", x.instanceGameObject.transform.parent == null ? null : SceneObjects.Id(x.instanceGameObject.transform.parent.gameObject) } });
                foreach (var x in removedGameObjects)
                    overrides.Add(new Dictionary<string, object> { { "kind", "REMOVED_GAME_OBJECT" }, { "source", PersistentId(x.assetGameObject) },
                                                                   { "parent", PersistentId(x.parentOfRemovedGameObjectInInstance) } });
            }
            var sourceRoot = PrefabUtility.GetCorrespondingObjectFromSource(root);
            var problems = new Dictionary<string, object>();
            int mapStart = page * PrefabBounds.InspectPage, overrideStart = page * PrefabBounds.OverridesPage;
            d["instance"] = new Dictionary<string, object> {
                { "root", SceneObjects.Ref(root) }, { "status", status }, { "source_path", path }, { "source_root", PersistentId(sourceRoot) },
                { "object_count", objects.Count }, { "override_count", overrides.Count } };
            d["page"] = page;
            d["objects"] = mapping.Skip(mapStart).Take(PrefabBounds.InspectPage).ToList();
            d["objects_next_page"] = mapStart + PrefabBounds.InspectPage < mapping.Count ? (object)(page + 1) : null;
            d["overrides"] = overrides.Skip(overrideStart).Take(PrefabBounds.OverridesPage).ToList();
            d["overrides_next_page"] = overrideStart + PrefabBounds.OverridesPage < overrides.Count ? (object)(page + 1) : null;
            d["tokens"] = Authoring.Tokens("object", SceneObjects.ObjectToken(root),
                                           "subtree", SceneObjects.TokenOrProblem(() => SceneObjects.SubtreeToken(root), problems, "subtree"));
            d["token_problems"] = problems;
            return d;
        }

        // ------------------------------------------------------------ creation

        sealed class SourcePlan
        {
            public readonly List<GameObject> GameObjects = new List<GameObject>();
            public readonly List<UnityEngine.Object> Objects = new List<UnityEngine.Object>();
            public readonly HashSet<UnityEngine.Object> Set = new HashSet<UnityEngine.Object>();
            public readonly Dictionary<GameObject, int> Parent = new Dictionary<GameObject, int>();
            public string[] Ids;
        }

        static Refusal SourceRefused(string why) { return new Refusal("PREFAB_REFUSED", "the source is not a completely plain Scene subtree: " + why + " (nothing was written)"); }

        // The completely plain source subtree, or PREFAB_REFUSED / PREFAB_LIMIT: no prefab instance content, root or
        // override, no hidden / DontSave / NotEditable object, no missing script, stable Scene ids, and every
        // serialized reference null, inside the subtree, a reviewed asset or structural.
        static SourcePlan Scan(GameObject source)
        {
            var plan = new SourcePlan();
            var stack = new Stack<KeyValuePair<Transform, int>>();
            stack.Push(new KeyValuePair<Transform, int>(source.transform, 0));
            int components = 0;
            while (stack.Count > 0)
            {
                var entry = stack.Pop();
                var go = entry.Key.gameObject;
                var all = go.GetComponents<Component>();
                components += all.Length;
                PrefabBounds.Check(plan.GameObjects.Count + 1, components, entry.Value, "the source subtree");
                if (SceneObjects.Role(go) != SceneObjects.None || PrefabUtility.IsPartOfAnyPrefab(go))
                    throw SourceRefused(go.name + " is " + SceneObjects.Role(go) + " relative to a prefab instance (a prefab instance source would become a Variant)");
                if (go.hideFlags != HideFlags.None) throw SourceRefused(go.name + " is hidden or excluded from saving");
                plan.GameObjects.Add(go);
                plan.Parent[go] = entry.Key.parent == null || go == source ? -1 : plan.GameObjects.IndexOf(entry.Key.parent.gameObject);
                plan.Objects.Add(go);
                foreach (var c in all)
                {
                    if (c == null || (c is MonoBehaviour && MonoScript.FromMonoBehaviour((MonoBehaviour)c) == null))
                        throw SourceRefused(go.name + " has a component whose script is missing");
                    if (c.hideFlags != HideFlags.None) throw SourceRefused(go.name + " has a hidden component");
                    if (SceneObjects.Role(c) != SceneObjects.None) throw SourceRefused(go.name + " has a prefab override component");
                    plan.Objects.Add(c);
                }
                for (int i = entry.Key.childCount - 1; i >= 0; i--) stack.Push(new KeyValuePair<Transform, int>(entry.Key.GetChild(i), entry.Value + 1));
            }
            foreach (var o in plan.Objects) plan.Set.Add(o);
            plan.Ids = SceneObjects.Ids(plan.Objects.ToArray());
            string sceneGuid = AssetDatabase.AssetPathToGUID(source.scene.path);
            if (plan.Ids.Distinct().Count() != plan.Ids.Length ||
                plan.Ids.Any(id => { try { return ObjectIds.SceneGuid(id) != sceneGuid || !id.EndsWith("-0", StringComparison.Ordinal); } catch (Refusal) { return true; } }))
                throw SourceRefused("an object of the subtree has no stable Scene id");
            var refused = new List<object>();
            int count = 0;
            foreach (var o in plan.Objects)
            {
                var so = new SerializedObject(o);
                var it = so.GetIterator();
                int walked = 0;
                bool enter = true;
                while (it.Next(enter))
                {
                    if (++walked > SceneObjects.MaxNodes)
                        throw new Refusal("PREFAB_LIMIT", "an object of the source holds more than " + SceneObjects.MaxNodes + " serialized values; nothing was written");
                    enter = true;
                    if (it.propertyType != SerializedPropertyType.ObjectReference) continue;
                    enter = false;
                    string why = PrefabReferences.Refusal(it.propertyPath, o == source.transform, Target(it, o, plan.Set));
                    if (why == null) continue;
                    if (++count <= PrefabBounds.MaxListed)
                        refused.Add(new Dictionary<string, object> { { "object", plan.Ids[plan.Objects.IndexOf(o)] }, { "property_path", it.propertyPath }, { "reason", why } });
                }
            }
            if (count > 0)
                throw new Refusal("PREFAB_REFUSED", count + " serialized reference(s) of the source would not be saved as they are (a Scene object outside the subtree, a missing script, an unsupported or unprovable reference); nothing was written")
                      { Data = new Dictionary<string, object> { { "mutation_started", false }, { "references_refused", refused }, { "references_refused_count", count } } };
            return plan;
        }

        // What a reference of a source object points at (PrefabReferences vocabulary).
        static string Target(SerializedProperty p, UnityEngine.Object owner, HashSet<UnityEngine.Object> subtree)
        {
            var t = p.objectReferenceValue;
            if (t == null) return PrefabReferences.Null;
            if (EditorUtility.IsPersistent(t))
            {
                if (t is MonoScript)
                    return owner is MonoBehaviour && MonoScript.FromMonoBehaviour((MonoBehaviour)owner) == t ? PrefabReferences.OwnScript : PrefabReferences.OtherScript;
                try { AssetResolver.Classify(t, SceneObjects.Id(t)); return PrefabReferences.ReviewedAsset; }
                catch (Refusal) { return PrefabReferences.UnsupportedAsset; }
            }
            return subtree.Contains(t) ? PrefabReferences.Inside : PrefabReferences.SceneOutside;
        }

        static void Folder(string folder)
        {
            try { AssetAuthoring.CheckFolder(folder); }
            catch (Refusal r)
            {
                if (r.Code == "ASSET_NOT_EDITABLE") throw new Refusal("PREFAB_NOT_EDITABLE", r.Message);
                throw;
            }
        }

        static Dictionary<string, object> Create(Dictionary<string, object> a)
        {
            string sourceId = Authoring.Str(a, "source", true), path = Authoring.Str(a, "path", true);
            string folder = PrefabPaths.CheckWritePath(path), stem = AssetPaths.StemOf(path, PrefabPaths.Extension);
            string expected = Authoring.Token(a, "expected_subtree_token", true);
            PrefabResolver.RequireNoStage();
            string vcs = PrefabResolver.VersionControl(null);
            if (vcs != null) throw new Refusal("PREFAB_NOT_EDITABLE", vcs + " (nothing was written)");
            var source = new Resolver().GameObject(sourceId);
            var plan = Scan(source);
            string token = SceneObjects.SubtreeToken(source);
            Authoring.Expect(expected, token, "the source object or something below it");
            Folder(folder);
            AssetAuthoring.CheckAbsent(folder, path);
            var scenes = PrefabResolver.Scenes();
            var recovered = CreateTxns.RecoverAll();
            bool changed = recovered.Any(x => (bool)((Dictionary<string, object>)x)["changed"]);
            var txn = new CreateTxn {
                TxnId = Guid.NewGuid().ToString("N"), ProjectKey = LiveBridge.Place.Key, SessionId = current.SessionId, RequestId = current.Id,
                Owner = current.Owner, Kind = AssetKinds.Prefab, FinalPath = path, Phase = CreateTxn.Prepared,
                StartedUtc = DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'", CultureInfo.InvariantCulture) };
            try
            {
                Folder(folder);
                AssetAuthoring.CheckAbsent(folder, path);
                CreateTxns.Write(txn);
            }
            catch (Refusal refusal)
            {
                refusal.Data = new Dictionary<string, object> { { "mutation_started", changed }, { "recovered", recovered } };
                throw;
            }
            AssetAuthoring.Step("prepared", path);
            var state = new AssetAuthoring.CreateState { Txn = txn, Started = changed };
            try
            {
                return Transact(state, folder, path, stem, sourceId, source, plan, token, scenes, recovered);
            }
            catch (Refusal refusal)
            {
                if (refusal.Code == "PREFAB_CREATE_INCOMPLETE" && refusal.Data != null) throw;   // already decided: the record is kept
                throw AssetAuthoring.Compensate(state, refusal.Code, refusal.Message, refusal.Code == "AUTHORING_FAILED" ? "FAILED" : "REFUSED", recovered);
            }
            catch (Exception e)
            {
                throw AssetAuthoring.Compensate(state, "AUTHORING_FAILED", "Unity raised " + e.GetType().Name + " while creating the prefab", "FAILED", recovered);
            }
        }

        static Dictionary<string, object> Transact(AssetAuthoring.CreateState st, string folder, string path, string stem, string sourceId, GameObject source,
                                                   SourcePlan plan, string token, List<PrefabResolver.SceneFact> scenes, List<object> recovered)
        {
            var txn = st.Txn;
            string scratch = txn.ScratchFolder, temp = txn.TempPath, file = stem + PrefabPaths.Extension;
            if (AssetFiles.Present(scratch) || AssetFiles.Present(scratch + ".meta"))
                throw new Refusal("PREFAB_CREATE_INCOMPLETE", "the transaction's scratch folder already exists; nothing was written");
            // ---- the GPOS-owned scratch namespace: created exclusively, so nothing of anyone else can be inside it
            st.Started = true;
            if (mkdir(AssetFiles.Full(scratch), Convert.ToInt32("755", 8)) != 0)
                throw new Refusal("PREFAB_CREATE_INCOMPLETE", "the scratch folder could not be created exclusively (errno " + Marshal.GetLastWin32Error() + ")");
            AssetDatabase.ImportAsset(scratch);
            var listing = AssetFiles.Listing(scratch);
            txn.ScratchMeta = AssetFiles.MetaSha(scratch);
            if (!AssetDatabase.IsValidFolder(scratch) || listing == null || listing.Count != 0 || txn.ScratchMeta == null)
                throw new Refusal("AUTHORING_FAILED", "Unity did not register the scratch folder");
            txn.Phase = CreateTxn.ScratchReady;
            CreateTxns.Write(txn);
            AssetAuthoring.Step("scratch", path);
            listing = AssetFiles.Listing(scratch);
            if (listing == null || listing.Count != 0) throw new Refusal("AUTHORING_FAILED", "unknown content appeared in the scratch folder before the prefab was created");
            if (SceneObjects.SubtreeToken(source) != token) throw new Refusal("PREFAB_CONFLICT", "the source changed before it was saved");
            // ---- the new prefab, saved from the plain source into the scratch folder under its final file name; never connected
            bool success;
            var saved = PrefabUtility.SaveAsPrefabAsset(source, temp, out success);
            st.Created = saved;
            AssetAuthoring.Step("temp-created", path);
            listing = AssetFiles.Listing(scratch);
            string guid = AssetDatabase.AssetPathToGUID(temp, AssetPathToGUIDOptions.OnlyExistingAssets);
            var loaded = AssetDatabase.LoadMainAssetAtPath(temp) as GameObject;
            if (!success || saved == null || listing == null || !listing.SequenceEqual(new[] { file, file + ".meta" }) || string.IsNullOrEmpty(guid) || loaded == null ||
                loaded != saved || PrefabUtility.GetPrefabAssetType(loaded) != PrefabAssetType.Regular || AssetFiles.MetaGuid(temp) != guid ||
                !PrefabIds.IsRecordRoot(SceneObjects.Id(loaded), guid) || EditorUtility.IsDirty(loaded))
                throw new Refusal("AUTHORING_FAILED", "the created prefab is not exactly the regular prefab GPOS asked Unity to create");
            txn.Guid = guid;
            txn.GlobalId = SceneObjects.Id(loaded);
            txn.Type = Catalog.TypeKey(loaded.GetType());
            txn.TempFile = AssetFiles.FileSha(temp);
            txn.TempMeta = AssetFiles.MetaSha(temp);
            txn.Phase = CreateTxn.TempProven;
            CreateTxns.Write(txn);
            AssetAuthoring.Step("temp-proven", path);
            // ---- the content: exactly the source, every reference saved as it is, the source itself unchanged
            var created = PrefabResolver.Of(loaded, temp);
            var mapping = Prove(created, plan, stem);
            if (SceneObjects.SubtreeToken(source) != token || PrefabUtility.IsPartOfAnyPrefab(source))
                throw new Refusal("PREFAB_CONFLICT", "the source changed or was connected while it was saved (project code ran)");
            var unrequested = Differences(created, plan, mapping);
            // ---- the final destination, checked again immediately before the move; MoveAsset never overwrites
            AssetAuthoring.Step("pre-move", path);
            Folder(folder);
            AssetAuthoring.CheckAbsent(folder, path);
            string valid = AssetDatabase.ValidateMoveAsset(temp, path);
            if (!string.IsNullOrEmpty(valid)) throw new Refusal("ASSET_EXISTS", "Unity refuses the destination: " + valid);
            st.MoveAttempted = true;
            string error = AssetDatabase.MoveAsset(temp, path);
            AssetAuthoring.Step("moved", path);
            if (!string.IsNullOrEmpty(error)) throw new Refusal("AUTHORING_FAILED", "Unity did not move the prefab into place: " + error);
            string finalFile = AssetFiles.FileSha(path), finalMeta = AssetFiles.MetaSha(path);
            var placed = AssetDatabase.LoadMainAssetAtPath(path) as GameObject;
            if (placed != saved || AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets) != guid || SceneObjects.Id(placed) != txn.GlobalId ||
                Catalog.TypeKey(placed.GetType()) != txn.Type || placed.name != stem || AssetFiles.MetaGuid(path) != guid || finalFile != txn.TempFile ||
                finalMeta != txn.TempMeta || EditorUtility.IsDirty(placed) || AssetFiles.Present(temp) || AssetFiles.Present(temp + ".meta"))
                throw new Refusal("AUTHORING_FAILED", "the moved prefab is not exactly the one GPOS created");
            txn.FinalFile = finalFile;
            txn.FinalMeta = finalMeta;
            txn.Phase = CreateTxn.FinalProven;
            CreateTxns.Write(txn);
            AssetAuthoring.Step("final-proven", path);
            var final = PrefabResolver.Of(placed, path);
            if (final.Objects.Count != plan.Objects.Count) throw new Refusal("AUTHORING_FAILED", "the moved prefab does not hold the source's objects");
            var data = final.ToData();
            data["objects"] = Listed(plan.Objects.Select((o, i) => (object)new Dictionary<string, object> {
                { "source", plan.Ids[i] }, { "id", final.Ids[final.Objects[i]] } }), PrefabBounds.MaxInstanceObjectsReported);
            data["object_count"] = plan.Objects.Count;
            // ---- the scratch folder is removed only when it is exactly the empty folder GPOS created
            string scratchProblem = CreateTxns.RemoveScratch(txn);
            if (scratchProblem != null)
                throw new Refusal("PREFAB_CREATE_INCOMPLETE", "the prefab was created at " + path + ", but the transaction's scratch folder " + scratch + " " + scratchProblem +
                                                              "; it is left untouched and the transaction stays open — remove the unknown content, then any later creation closes it")
                      { Data = new Dictionary<string, object> { { "mutation_started", true }, { "created", data }, { "txn_id", txn.TxnId }, { "recovered", recovered } } };
            AssetAuthoring.Step("scratch-removed", path);
            CreateTxns.Delete(txn);
            AssetAuthoring.Step("cleared", path);
            var s = PrefabResolver.State(PrefabResolver.Of(placed, path));
            return new Dictionary<string, object> {
                { "created", data }, { "txn_id", txn.TxnId }, { "tokens", Authoring.Tokens("prefab", s.Token) }, { "file_sha256", finalFile },
                { "meta_sha256", finalMeta }, { "undoable", false },
                { "source", new Dictionary<string, object> { { "id", sourceId }, { "subtree_token", token }, { "connected", false } } },
                { "unrequested_changes", Listed(unrequested.Cast<object>(), PrefabBounds.MaxListed) }, { "unrequested_change_count", unrequested.Count },
                { "scenes_marked_dirty", PrefabResolver.MarkedDirty(scenes) }, { "recovered", recovered } };
        }

        // Proves the saved prefab is the source: the same hierarchy (the root named as the file), the same GameObject
        // state and local Transforms, the same component types, and every reference saved as it is in the source — a
        // reference inside the subtree to the corresponding prefab object, an asset to that same asset. Returns the
        // source object -> prefab object mapping. AUTHORING_FAILED otherwise (the temporary prefab is then removed).
        static Dictionary<UnityEngine.Object, UnityEngine.Object> Prove(PrefabInfo created, SourcePlan plan, string stem)
        {
            Func<string, Refusal> fail = why => new Refusal("AUTHORING_FAILED", "the saved prefab is not the source (" + why + ")");
            if (created.Type != PrefabScope.Regular || created.Nested || created.MissingScript || created.Embedded) throw fail("it is not a plain regular prefab");
            if (created.Root.name != stem) throw fail("the root is not named as the file");
            if (created.GameObjects.Count != plan.GameObjects.Count || created.Objects.Count != plan.Objects.Count) throw fail("another hierarchy");
            var map = new Dictionary<UnityEngine.Object, UnityEngine.Object>();
            for (int i = 0; i < plan.Objects.Count; i++)
            {
                var s = plan.Objects[i];
                var c = created.Objects[i];
                if (s.GetType() != c.GetType()) throw fail("another component order");
                map[s] = c;
            }
            for (int i = 0; i < plan.GameObjects.Count; i++)
            {
                var s = plan.GameObjects[i];
                var c = created.GameObjects[i];
                var st = s.transform;
                var ct = c.transform;
                int parent = plan.Parent[s];
                if ((parent < 0) != (ct.parent == null) || (parent >= 0 && ct.parent.gameObject != created.GameObjects[parent]))
                    throw fail("another parent");
                if ((i > 0 && s.name != c.name) || s.activeSelf != c.activeSelf || s.tag != c.tag || s.layer != c.layer ||
                    GameObjectUtility.GetStaticEditorFlags(s) != GameObjectUtility.GetStaticEditorFlags(c))
                    throw fail("another GameObject state of " + s.name);
                if (!Authoring.Same(st.localPosition, ct.localPosition) || !Authoring.Same(st.localScale, ct.localScale) || !SameRotation(st.localRotation, ct.localRotation))
                    throw fail("another local Transform of " + s.name);
            }
            foreach (var s in plan.Objects)
            {
                var c = map[s];
                var cso = new SerializedObject(c);
                var it = new SerializedObject(s).GetIterator();
                bool enter = true;
                while (it.Next(enter))
                {
                    enter = it.propertyType != SerializedPropertyType.ObjectReference;
                    if (enter) continue;
                    if (s == plan.GameObjects[0].transform && it.propertyPath == "m_Father") continue;
                    if (PrefabReferences.IsLink(it.propertyPath)) continue;   // the saved prefab's own linkage (nesting is refused above)
                    var q = cso.FindProperty(it.propertyPath);
                    var t = it.objectReferenceValue;
                    UnityEngine.Object expected = t == null ? null : plan.Set.Contains(t) ? map[t] : t;
                    if (q == null || q.propertyType != SerializedPropertyType.ObjectReference || q.objectReferenceValue != expected)
                        throw fail("the reference " + it.propertyPath + " was not saved as it is in the source");
                }
            }
            return map;
        }

        // The prefab objects whose visible plain values differ from their source object (project code on save).
        static List<string> Differences(PrefabInfo created, SourcePlan plan, Dictionary<UnityEngine.Object, UnityEngine.Object> map)
        {
            var changed = new List<string>();
            foreach (var s in plan.Objects)
            {
                var c = map[s];
                var cso = new SerializedObject(c);
                var it = new SerializedObject(s).GetIterator();
                while (it.NextVisible(true))
                {
                    if (it.propertyType == SerializedPropertyType.ObjectReference || it.propertyType == SerializedPropertyType.Generic || it.propertyType == SerializedPropertyType.ManagedReference) continue;
                    if (s == plan.GameObjects[0] && it.propertyPath == "m_Name") continue;
                    var q = cso.FindProperty(it.propertyPath);
                    if (q == null || q.contentHash != it.contentHash) { changed.Add(created.Ids[c]); break; }
                }
            }
            return changed;
        }

        // ------------------------------------------------------------ instantiation (Scene authoring)

        static Dictionary<string, object> Instantiate(Dictionary<string, object> a)
        {
            string prefabId = Authoring.Str(a, "prefab", true);
            string expected = Authoring.Token(a, "expected_prefab_token", true);
            var scene = Resolver.SceneByPath(Authoring.Str(a, "scene", true));
            var resolver = new Resolver();
            string parentId = Authoring.Str(a, "parent");
            GameObject parent = parentId == null ? null : resolver.GameObject(parentId);
            var position = Authoring.Vec3(a, "local_position");
            var rotation = Authoring.Rotation(a, "local_rotation");
            var scale = Authoring.Vec3(a, "local_scale");
            string parentToken = null, rootsToken = null;
            if (parent != null)
            {
                if (parent.scene != scene) throw new Refusal("OBJECT_REFUSED", "the parent is in another Scene");
                Authoring.PrefabBoundary("the parent", parent, SceneObjects.None);
                Authoring.Absent(a, "expected_scene_roots_token", "with a parent");
                parentToken = SceneObjects.ObjectToken(parent);
                Authoring.Expect(Authoring.Token(a, "expected_parent_token", true), parentToken, "the parent");
            }
            else
            {
                Authoring.Absent(a, "expected_parent_token", "at the Scene root");
                rootsToken = SceneObjects.RootsToken(scene);
                Authoring.Expect(Authoring.Token(a, "expected_scene_roots_token", true), rootsToken, "the Scene's root objects");
            }
            int count = parent != null ? parent.transform.childCount : scene.rootCount;
            int? sibling = Authoring.Int(a, "sibling", 0, count);
            // ---- the source: a clean regular prefab without nested prefabs, never Human unsaved state
            PrefabResolver.RequireNoStage();
            var p = PrefabResolver.Prefab(prefabId);
            PrefabResolver.RequireScope(p);
            if (PrefabResolver.Dirty(p))
                throw new Refusal("PREFAB_DIRTY", "the prefab has unsaved changes in the Editor; GPOS never turns unsaved prefab state into an instance — save or revert it in the Editor, re-inspect and decide again (nothing was changed)");
            try { AssetFiles.LinkFree(p.Path); }
            catch (Refusal r) { throw new Refusal("PREFAB_REFUSED", r.Message); }
            if (PrefabResolver.Sha(p.Path, false) == null || PrefabResolver.Sha(p.Path, true) == null) throw new Refusal("PREFAB_REFUSED", "the prefab's file or .meta file is missing on disk");
            var scenes = PrefabResolver.Scenes();
            // ---- the targeted import: from here on the request reports that it changed Unity's state
            AssetDatabase.ImportAsset(p.Path);
            AssetAuthoring.Step("import", p.Path);
            var pre = new Authoring.PreState();
            PrefabInfo fresh;
            PrefabState state;
            try
            {
                fresh = PrefabResolver.Prefab(prefabId);
                if (fresh.Path != p.Path) throw new Refusal("PREFAB_CONFLICT", "the prefab moved during its import");
                PrefabResolver.RequireScope(fresh);
                PrefabResolver.RequireNoStage();
                if (PrefabResolver.Dirty(fresh)) throw new Refusal("PREFAB_DIRTY", "the prefab is dirty after its import; no instance was created");
                state = PrefabResolver.State(fresh);
                if (state.Token != expected)
                    throw new Refusal("PREFAB_CONFLICT", "the prefab changed since it was inspected (in memory, on disk or in its .meta); re-inspect and decide again — no instance was created")
                          { Data = new Dictionary<string, object> { { "tokens_now", Authoring.Tokens("prefab", state.Token) } } };
                // the import may have changed the Scene's instances: the Scene expectations are checked again
                if (parent != null)
                {
                    string t = SceneObjects.ObjectToken(parent);
                    Authoring.Expect(parentToken, t, "the parent");
                    pre.Add("object", parentId, t);
                }
                else
                {
                    string t = SceneObjects.RootsToken(scene);
                    Authoring.Expect(rootsToken, t, "the Scene's root objects");
                    pre.Add("roots", scene.path, t);
                }
            }
            catch (Refusal refusal)
            {
                refusal.Data = Started(scenes, refusal.Data);
                refusal.Data["scene_changed"] = false;
                throw;
            }
            var before = PrefabResolver.Scenes();
            GameObject go = null;
            List<UnityEngine.Object> made = null;
            string[] ids = null;
            string name = fresh.Root.name;
            Dictionary<string, object> result;
            try
            {
                result = Authoring.Mutate("GPOS: instantiate " + name, scene, pre, () =>
                {
                    go = (parent != null ? PrefabUtility.InstantiatePrefab(fresh.Root, parent.transform) : PrefabUtility.InstantiatePrefab(fresh.Root, scene)) as GameObject;
                    if (go == null) throw new Refusal("AUTHORING_FAILED", "Unity did not instantiate the prefab");
                    if (position.HasValue) go.transform.localPosition = position.Value;
                    if (rotation.HasValue) go.transform.localRotation = rotation.Value;
                    if (scale.HasValue) go.transform.localScale = scale.Value;
                    if (sibling.HasValue) go.transform.SetSiblingIndex(sibling.Value);
                    // Every id of the new instance, before its creation is recorded: Redo then brings back the same
                    // objects with the same GlobalObjectIds (an id first requested after registration is not recorded).
                    made = Collect(go);
                    ids = SceneObjects.Ids(made.ToArray());
                    Undo.RegisterCreatedObjectUndo(go, "GPOS: instantiate " + name);
                    if (go.scene != scene || go.transform.parent != (parent == null ? null : parent.transform) ||
                        (sibling.HasValue && go.transform.GetSiblingIndex() != sibling.Value) ||
                        (position.HasValue && !Authoring.Same(go.transform.localPosition, position.Value)) ||
                        (scale.HasValue && !Authoring.Same(go.transform.localScale, scale.Value)) ||
                        (rotation.HasValue && !SameRotation(go.transform.localRotation, rotation.Value)))
                        throw new Refusal("VALUE_NOT_APPLIED", "the new instance is not where it was asked to be");
                    if (!PrefabUtility.IsOutermostPrefabInstanceRoot(go) || PrefabUtility.GetPrefabInstanceStatus(go) != PrefabInstanceStatus.Connected ||
                        PrefabUtility.GetCorrespondingObjectFromSource(go) != fresh.Root || PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(go) != fresh.Path)
                        throw new Refusal("AUTHORING_FAILED", "the new object is not a connected instance of the prefab");
                    if (made.Count != fresh.Objects.Count || made.Where((o, i) => PrefabUtility.GetCorrespondingObjectFromSource(o) != fresh.Objects[i]).Any())
                        throw new Refusal("AUTHORING_FAILED", "the new instance does not correspond object by object to the prefab");
                    string sceneGuid = AssetDatabase.AssetPathToGUID(scene.path);
                    if (ids.Distinct().Count() != ids.Length || ids.Any(id => { try { return ObjectIds.SceneGuid(id) != sceneGuid; } catch (Refusal) { return true; } }))
                        throw new Refusal("AUTHORING_FAILED", "the new instance has no stable Scene ids");
                    foreach (var f in before)
                        if (f.Scene != scene && !f.Dirty && f.Scene.isDirty) throw new Refusal("AUTHORING_FAILED", "another Scene (" + f.Name + ") became dirty");
                }, () =>
                {
                    var mods = PrefabUtility.GetPropertyModifications(go) ?? new PropertyModification[0];
                    var own = mods.Where(m => !PrefabUtility.IsDefaultOverride(m)).ToList();
                    return new Dictionary<string, object> {
                        { "instance", SceneObjects.Ref(go) }, { "prefab", fresh.ToData() },
                        { "objects", Listed(made.Select((o, i) => (object)new Dictionary<string, object> { { "id", ids[i] }, { "source", fresh.Ids[fresh.Objects[i]] } }),
                                            PrefabBounds.MaxInstanceObjectsReported) },
                        { "object_count", made.Count },
                        { "overrides_after", new Dictionary<string, object> {
                            { "default_overrides", mods.Length - own.Count }, { "property_overrides", own.Count },
                            { "property_override_targets", Listed(own.Select(m => (object)new Dictionary<string, object> { { "target", PersistentId(m.target) }, { "property_path", m.propertyPath } }), PrefabBounds.MaxListed) },
                            { "added_components", PrefabUtility.GetAddedComponents(go).Count }, { "added_game_objects", PrefabUtility.GetAddedGameObjects(go).Count } } },
                        { "tokens", Authoring.Tokens("object", SceneObjects.ObjectToken(go), "subtree", SceneObjects.SubtreeToken(go),
                                                     "parent_object", parent == null ? null : SceneObjects.ObjectToken(parent),
                                                     "scene_roots", SceneObjects.RootsToken(scene), "prefab", state.Token) } };
                });
            }
            catch (Refusal refusal)
            {
                refusal.Data = Started(scenes, refusal.Data);
                throw;
            }
            result["import_performed"] = true;
            result["scene_saved"] = false;
            // other Scenes only: the target Scene is dirty by design (result["scene"])
            result["scenes_marked_dirty"] = PrefabResolver.MarkedDirty(scenes.Where(f => f.Scene != scene).ToList());
            return result;
        }

        // ------------------------------------------------------------ editing an existing prefab

        abstract class PrefabEdit
        {
            // Read-only validation against the persistent prefab, before anything is imported or loaded.
            public abstract void Check(PrefabInfo p, UnityEngine.Object target);
            // The one edit on the isolated copy; throws when Unity did not keep exactly what was written.
            public abstract void Apply(Dictionary<string, UnityEngine.Object> contents, UnityEngine.Object copy);
            // After the save and before the contents are unloaded: the ids the save gave to new objects.
            public virtual void Saved(Dictionary<string, UnityEngine.Object> contents, UnityEngine.Object copy) { }
            // The persistent objects this edit changes (their state is expected to differ afterwards).
            public abstract IEnumerable<string> Changes(PrefabInfo before, string id);
            // Verifies the persistent result; throws when it is not exactly the requested change.
            public abstract Dictionary<string, object> Verify(PrefabInfo after, PrefabInfo before, string id);
        }

        static Dictionary<string, object> Edit(Dictionary<string, object> a, string idKey, PrefabEdit edit)
        {
            string id = Authoring.Str(a, idKey, true);
            string expected = Authoring.Token(a, "expected_prefab_token", true);
            // ---- resolve and validate: nothing is imported or loaded yet
            PrefabInfo p;
            var target = PrefabResolver.Object(id, out p);
            PrefabResolver.RequireScope(p);
            if (PrefabResolver.Role(p, target) != PrefabScope.Owned) throw new Refusal("PREFAB_REFUSED", "the object is not owned by this prefab");
            edit.Check(p, target);
            PrefabResolver.RequireNoStage();
            if (PrefabResolver.Dirty(p))
                throw new Refusal("PREFAB_DIRTY", "the prefab has unsaved changes in the Editor; GPOS never saves them — save or revert it in the Editor, re-inspect and decide again (nothing was changed)");
            string vcs = PrefabResolver.VersionControl(p.Path);
            if (vcs != null) throw new Refusal("PREFAB_NOT_EDITABLE", vcs + " (nothing was changed)");
            string os = PrefabResolver.NotWritable(p.Path);
            if (os != null) throw new Refusal("PREFAB_NOT_EDITABLE", os + "; GPOS never changes permissions (nothing was changed)");
            try { AssetFiles.LinkFree(p.Path); }
            catch (Refusal r) { throw new Refusal("PREFAB_REFUSED", r.Message); }
            if (PrefabResolver.Sha(p.Path, false) == null || PrefabResolver.Sha(p.Path, true) == null) throw new Refusal("PREFAB_REFUSED", "the prefab's file or .meta file is missing on disk");
            // ---- a Scene the save would propagate into must not hold unsaved Human work
            var scenes = PrefabResolver.Scenes();
            var dirtyDependents = PrefabResolver.DependentScenes(p.Path).Where(f => f.Dirty).Select(f => (object)f.Name).ToList();
            if (dirtyDependents.Count > 0)
                throw new Refusal("PREFAB_CONFLICT", "a loaded Scene holding an instance that depends on the prefab has unsaved changes; saving the prefab would propagate into it — save or revert that Scene, then decide again (nothing was changed)")
                      { Data = new Dictionary<string, object> { { "mutation_started", false }, { "dependent_scenes_dirty", Listed(dirtyDependents, PrefabBounds.MaxListed) } } };
            // ---- the targeted import: from here on the request reports that it changed Unity's state
            string path = p.Path;
            AssetDatabase.ImportAsset(path);
            AssetAuthoring.Step("import", path);
            PrefabInfo before;
            PrefabState state;
            try
            {
                target = PrefabResolver.Object(id, out before);
                if (before.Path != path || before.Guid != p.Guid) throw new Refusal("PREFAB_CONFLICT", "the prefab moved during its import");
                PrefabResolver.RequireScope(before);
                if (PrefabResolver.Role(before, target) != PrefabScope.Owned) throw new Refusal("PREFAB_CONFLICT", "the object is no longer owned by the prefab");
                if (PrefabResolver.Dirty(before)) throw new Refusal("PREFAB_DIRTY", "the prefab is dirty after its import; nothing was written");
                state = PrefabResolver.State(before);
                if (state.Token != expected)
                    throw new Refusal("PREFAB_CONFLICT", "the prefab changed since it was inspected (in memory, on disk or in its .meta); re-inspect and decide again — nothing was written")
                          { Data = new Dictionary<string, object> { { "tokens_now", Authoring.Tokens("prefab", state.Token) } } };
                edit.Check(before, target);
            }
            catch (Refusal refusal)
            {
                refusal.Data = Started(scenes, refusal.Data);
                throw;
            }
            GameObject contents = null;
            bool commit = false;
            try
            {
                contents = PrefabUtility.LoadPrefabContents(path);
                AssetAuthoring.Step("loaded", path);
                var map = PrefabResolver.Map(before, contents);
                var copy = map[id];
                edit.Apply(map, copy);
                // ---- immediately before saving: no stage, the same clean prefab, the same file and .meta bytes
                AssetAuthoring.Step("pre-save", path);
                PrefabInfo now;
                PrefabResolver.Object(before.Id, out now);
                if (PrefabResolver.AnyStageOpen()) throw new Refusal("PREFAB_STAGE_OPEN", "a Prefab Mode stage was opened before the prefab was saved; nothing was written");
                if (now.Path != path || now.Guid != before.Guid || now.Id != before.Id) throw new Refusal("PREFAB_CONFLICT", "the prefab moved before it was saved; nothing was written");
                if (PrefabResolver.Dirty(now)) throw new Refusal("PREFAB_DIRTY", "the prefab became dirty before it was saved; nothing was written");
                string fileNow, metaNow;
                try { fileNow = PrefabResolver.Sha(path, false); metaNow = PrefabResolver.Sha(path, true); }
                catch (Refusal) { fileNow = metaNow = null; }
                if (fileNow != state.File || metaNow != state.Meta)
                    throw new Refusal("PREFAB_CONFLICT", "the prefab's file or .meta changed on disk before it was saved; nothing was written");
                // ---- the commit point: this prefab's file only
                commit = true;
                bool success;
                var saved = PrefabUtility.SaveAsPrefabAsset(contents, path, out success);
                if (!success || saved == null) throw new Refusal("PERSISTENCE_UNKNOWN", "Unity reported that the prefab was not saved; whether its file changed is unknown");
                edit.Saved(map, copy);
            }
            catch (Exception e)
            {
                var refusal = e as Refusal;
                if (commit)
                    throw new Refusal("PERSISTENCE_UNKNOWN", (refusal != null ? refusal.Message : "Unity raised " + e.GetType().Name + " while saving the prefab") +
                                                             "; whether it was written is unknown — it is not retried, re-inspect")
                          { Status = "FAILED", Data = Started(scenes, new Dictionary<string, object> { { "commit_started", true } }) };
                if (refusal == null) refusal = new Refusal("AUTHORING_FAILED", "Unity raised " + e.GetType().Name + " during the edit; nothing was saved");
                if (refusal.Code == "AUTHORING_FAILED") refusal.Status = "FAILED";
                refusal.Data = Started(scenes, refusal.Data);
                throw refusal;
            }
            finally
            {
                if (contents != null)
                {
                    try { PrefabUtility.UnloadPrefabContents(contents); }
                    catch (Exception) { }
                }
            }
            try
            {
                AssetAuthoring.Step("saved", path);
                var after = PrefabResolver.Prefab(before.Id);
                if (after.Path != path) throw new Refusal("PERSISTENCE_UNKNOWN", "the prefab is at another path after the save");
                if (PrefabResolver.AnyStageOpen()) throw new Refusal("PERSISTENCE_UNKNOWN", "a Prefab Mode stage is open after the save");
                if (PrefabResolver.Dirty(after)) throw new Refusal("PERSISTENCE_UNKNOWN", "the prefab is still dirty after the save");
                var result = edit.Verify(after, before, id);
                var s = PrefabResolver.State(after);
                var expectedChanges = new HashSet<string>(edit.Changes(before, id), StringComparer.Ordinal);
                var unrequested = s.PerObject.Keys.Union(state.PerObject.Keys)
                    .Where(k => !expectedChanges.Contains(k) && s.PerObject.ContainsKey(k) && state.PerObject.ContainsKey(k) && s.PerObject[k] != state.PerObject[k])
                    .OrderBy(k => k, StringComparer.Ordinal).ToList();
                result["prefab"] = after.ToData();
                result["tokens"] = Authoring.Tokens("prefab", s.Token);
                result["file_sha256"] = s.File;
                result["meta_sha256"] = s.Meta;
                result["file_changed"] = s.File != state.File;
                result["import_performed"] = true;
                result["value_persisted"] = true;
                result["undoable"] = false;
                result["unrequested_changes"] = Listed(unrequested.Cast<object>(), PrefabBounds.MaxListed);
                result["unrequested_change_count"] = unrequested.Count;
                result["scenes_marked_dirty"] = PrefabResolver.MarkedDirty(scenes);
                return result;
            }
            catch (Exception e)
            {
                throw new Refusal("PERSISTENCE_UNKNOWN", "the save could not be verified (" + (e is Refusal ? e.Message : e.GetType().Name) + "); it is not retried, re-inspect")
                      { Status = "FAILED", Data = Started(scenes, new Dictionary<string, object> { { "commit_started", true } }) };
            }
        }

        static PrefabInfo PrefabOf(PrefabInfo after, string id, out UnityEngine.Object o)
        {
            if (!after.ById.TryGetValue(id, out o)) throw new Refusal("PERSISTENCE_UNKNOWN", "the edited object is not in the prefab after the save");
            return after;
        }

        sealed class SetGameObjectEdit : PrefabEdit
        {
            readonly string name, tag;
            readonly bool? active;
            readonly int? layer;
            readonly object flagsValue;
            StaticEditorFlags? flags;

            public SetGameObjectEdit(Dictionary<string, object> a)
            {
                name = Authoring.Str(a, "name");
                tag = Authoring.Str(a, "tag");
                active = Authoring.Bool(a, "active");
                layer = Authoring.Int(a, "layer", 0, 31);
                flagsValue = a["static_flags"];
            }

            public override void Check(PrefabInfo p, UnityEngine.Object target)
            {
                var go = target as GameObject;
                if (go == null) throw new Refusal("OBJECT_REFUSED", "the id names a Component; a GameObject is expected");
                if (name == null && tag == null && !active.HasValue && !layer.HasValue && flagsValue == null)
                    throw new Refusal("BAD_ARGUMENTS", "at least one of name, active, tag, layer and static_flags is required");
                if (name != null && go == p.Root)
                    throw new Refusal("AUTHORING_REFUSED", "a prefab root's name is its file name (Unity renames it on save); it is never set");
                if (name != null) ObjectIds.CheckName(name);
                if (tag != null && !InternalEditorUtility.tags.Contains(tag)) throw new Refusal("VALUE_INVALID", "the tag is not defined in this project");
                if (flagsValue != null)
                {
                    long mask = 0;
                    foreach (var f in Enum.GetValues(typeof(StaticEditorFlags))) mask |= Convert.ToInt64(f);
                    double d = flagsValue is double ? (double)flagsValue : -1;
                    if (d != Math.Floor(d) || d < 0 || d > int.MaxValue || ((long)d & ~mask) != 0)
                        throw new Refusal("VALUE_INVALID", "static_flags is a combination of StaticEditorFlags bits");
                    flags = (StaticEditorFlags)(int)(long)d;
                }
            }

            bool Holds(GameObject go)
            {
                return (name == null || go.name == name) && (tag == null || go.tag == tag) && (!layer.HasValue || go.layer == layer.Value) &&
                       (!active.HasValue || go.activeSelf == active.Value) && (!flags.HasValue || GameObjectUtility.GetStaticEditorFlags(go) == flags.Value);
            }

            public override void Apply(Dictionary<string, UnityEngine.Object> contents, UnityEngine.Object copy)
            {
                var go = (GameObject)copy;
                if (name != null) go.name = name;
                if (tag != null) go.tag = tag;
                if (layer.HasValue) go.layer = layer.Value;
                if (active.HasValue) go.SetActive(active.Value);
                if (flags.HasValue) GameObjectUtility.SetStaticEditorFlags(go, flags.Value);
                if (!Holds(go)) throw new Refusal("VALUE_NOT_APPLIED", "Unity or project code kept a different value; nothing was saved");
            }

            public override IEnumerable<string> Changes(PrefabInfo before, string id) { return new[] { id }; }

            public override Dictionary<string, object> Verify(PrefabInfo after, PrefabInfo before, string id)
            {
                UnityEngine.Object o;
                PrefabOf(after, id, out o);
                if (!Holds((GameObject)o)) throw new Refusal("PERSISTENCE_UNKNOWN", "the saved GameObject holds another value");
                return new Dictionary<string, object> { { "object", Ref(after, o) } };
            }
        }

        sealed class SetTransformEdit : PrefabEdit
        {
            readonly Vector3? position, scale;
            readonly Quaternion? rotation;
            string transformId;

            public SetTransformEdit(Dictionary<string, object> a)
            {
                position = Authoring.Vec3(a, "local_position");
                rotation = Authoring.Rotation(a, "local_rotation");
                scale = Authoring.Vec3(a, "local_scale");
            }

            public override void Check(PrefabInfo p, UnityEngine.Object target)
            {
                if (!(target is GameObject)) throw new Refusal("OBJECT_REFUSED", "the id names a Component; a GameObject is expected");
                if (!position.HasValue && !rotation.HasValue && !scale.HasValue)
                    throw new Refusal("BAD_ARGUMENTS", "at least one of local_position, local_rotation and local_scale is required");
                transformId = p.Ids[((GameObject)target).transform];
            }

            bool Holds(Transform t)
            {
                return (!position.HasValue || Authoring.Same(t.localPosition, position.Value)) && (!scale.HasValue || Authoring.Same(t.localScale, scale.Value)) &&
                       (!rotation.HasValue || SameRotation(t.localRotation, rotation.Value));
            }

            public override void Apply(Dictionary<string, UnityEngine.Object> contents, UnityEngine.Object copy)
            {
                var t = ((GameObject)copy).transform;
                if (position.HasValue) t.localPosition = position.Value;
                if (rotation.HasValue) t.localRotation = rotation.Value;
                if (scale.HasValue) t.localScale = scale.Value;
                if (!Holds(t)) throw new Refusal("VALUE_NOT_APPLIED", "Unity or project code kept a different Transform; nothing was saved");
            }

            public override IEnumerable<string> Changes(PrefabInfo before, string id) { return new[] { id, transformId }; }

            public override Dictionary<string, object> Verify(PrefabInfo after, PrefabInfo before, string id)
            {
                UnityEngine.Object o;
                PrefabOf(after, id, out o);
                var t = ((GameObject)o).transform;
                if (!Holds(t)) throw new Refusal("PERSISTENCE_UNKNOWN", "the saved Transform holds another value");
                return new Dictionary<string, object> { { "object", Ref(after, o) }, { "transform", Transform(t) } };
            }
        }

        sealed class AddComponentEdit : PrefabEdit
        {
            readonly string typeId, digest;
            Type type;
            List<Component> added = new List<Component>();
            readonly List<string> addedIds = new List<string>();
            List<string> oldIds = new List<string>();   // the object's components before the edit

            public AddComponentEdit(Dictionary<string, object> a)
            {
                typeId = Authoring.Str(a, "type_id", true);
                digest = Authoring.Str(a, "expected_catalog_digest", true);
            }

            public override void Check(PrefabInfo p, UnityEngine.Object target)
            {
                var go = target as GameObject;
                if (go == null) throw new Refusal("OBJECT_REFUSED", "the id names a Component; a GameObject is expected");
                if (!ObjectIds.TypeId.IsMatch(typeId)) throw new Refusal("TYPE_NOT_IN_CATALOG", "a component type is named <assembly>::<full name>");
                if (!ObjectIds.Digest.IsMatch(digest)) throw new Refusal("BAD_ARGUMENTS", "expected_catalog_digest must be 64 hex");
                var catalog = Catalog.Build();
                var entry = catalog.Find(typeId);
                if (entry == null) throw new Refusal("TYPE_NOT_IN_CATALOG", "the type is not in the component catalog");
                if (catalog.Digest != digest) throw new Refusal("CATALOG_CHANGED", "the component catalog changed since it was read (a recompile or Domain Reload); read it again (nothing was changed)");
                type = catalog.Types[typeId];
                if (entry.DisallowMultiple && go.GetComponent(type) != null)
                    throw new Refusal("AUTHORING_REFUSED", "the type allows one component per object and the object already has one");
                oldIds = go.GetComponents<Component>().Where(c => c != null).Select(c => p.Ids[c]).ToList();
            }

            public override void Apply(Dictionary<string, UnityEngine.Object> contents, UnityEngine.Object copy)
            {
                var go = (GameObject)copy;
                var existing = go.GetComponents<Component>();
                var c = go.AddComponent(type);
                if (c == null) throw new Refusal("AUTHORING_REFUSED", "Unity did not add the component (it conflicts with a component on the object); nothing was saved");
                added = go.GetComponents<Component>().Where(x => x != null && !existing.Contains(x)).ToList();
                if (!added.Contains(c)) throw new Refusal("VALUE_NOT_APPLIED", "Unity did not keep the added component; nothing was saved");
                added.Remove(c);
                added.Insert(0, c);   // the requested component first, then those its [RequireComponent] added
            }

            // The save gives each new component its file id: its isolated copy now reports it (type 2, the prefab GUID).
            public override void Saved(Dictionary<string, UnityEngine.Object> contents, UnityEngine.Object copy)
            {
                var ids = new GlobalObjectId[added.Count];
                GlobalObjectId.GetGlobalObjectIdsSlow(added.Cast<UnityEngine.Object>().ToArray(), ids);
                string guid = PrefabIds.Guid(contents.Keys.First());
                foreach (var gid in ids)
                    addedIds.Add(PrefabIds.FromContents(gid.identifierType, gid.assetGUID.ToString(), gid.targetObjectId, gid.targetPrefabId, guid));
            }

            public override IEnumerable<string> Changes(PrefabInfo before, string id) { return new[] { id }; }

            public override Dictionary<string, object> Verify(PrefabInfo after, PrefabInfo before, string id)
            {
                UnityEngine.Object o;
                PrefabOf(after, id, out o);
                var go = (GameObject)o;
                var old = new HashSet<string>(oldIds, StringComparer.Ordinal);
                var now = go.GetComponents<Component>().Where(c => c != null).ToList();
                var fresh = now.Where(c => !old.Contains(after.Ids[c])).ToList();
                if (addedIds.Any(x => x == null) || fresh.Count != addedIds.Count || !fresh.Select(c => after.Ids[c]).OrderBy(x => x, StringComparer.Ordinal)
                        .SequenceEqual(addedIds.OrderBy(x => x, StringComparer.Ordinal)) || old.Any(x => !after.ById.ContainsKey(x)))
                    throw new Refusal("PERSISTENCE_UNKNOWN", "the saved GameObject does not hold exactly the old components and the added ones");
                var requested = after.ById[addedIds[0]] as Component;
                if (requested == null || requested.GetType() != type || requested.gameObject != go)
                    throw new Refusal("PERSISTENCE_UNKNOWN", "the saved component is not the requested type on the object");
                return new Dictionary<string, object> {
                    { "object", Ref(after, go) }, { "component", Ref(after, requested) },
                    { "added_components", addedIds.Select(x => (object)Ref(after, after.ById[x])).ToList() } };
            }
        }

        sealed class RemoveComponentEdit : PrefabEdit
        {
            string gameObjectId;
            List<string> oldIds = new List<string>();   // the object's components before the edit

            public override void Check(PrefabInfo p, UnityEngine.Object target)
            {
                var c = target as Component;
                if (c == null) throw new Refusal("OBJECT_REFUSED", "the id names a GameObject; a Component is expected");
                if (c is Transform) throw new Refusal("AUTHORING_REFUSED", "a Transform is never removed");
                var others = c.gameObject.GetComponents<Component>().Where(x => x != null && x != c).ToList();
                foreach (var other in others)
                    foreach (var required in Catalog.Required(other.GetType()))
                        if (required.IsInstanceOfType(c) && !others.Any(x => x != other && required.IsInstanceOfType(x)))
                            throw new Refusal("AUTHORING_REFUSED", other.GetType().Name + " on the object requires this component");
                gameObjectId = p.Ids[c.gameObject];
                oldIds = c.gameObject.GetComponents<Component>().Where(x => x != null).Select(x => p.Ids[x]).ToList();
            }

            public override void Apply(Dictionary<string, UnityEngine.Object> contents, UnityEngine.Object copy)
            {
                UnityEngine.Object.DestroyImmediate(copy);
                if (copy != null) throw new Refusal("AUTHORING_REFUSED", "Unity did not remove the component; nothing was saved");
            }

            public override IEnumerable<string> Changes(PrefabInfo before, string id) { return new[] { id, gameObjectId }; }

            public override Dictionary<string, object> Verify(PrefabInfo after, PrefabInfo before, string id)
            {
                UnityEngine.Object o;
                PrefabOf(after, gameObjectId, out o);
                var old = oldIds.Where(x => x != id);
                var now = ((GameObject)o).GetComponents<Component>().Where(c => c != null).Select(c => after.Ids[c]);
                if (after.ById.ContainsKey(id) || !now.SequenceEqual(old))
                    throw new Refusal("PERSISTENCE_UNKNOWN", "the saved GameObject does not hold exactly its other components");
                return new Dictionary<string, object> { { "removed", id }, { "object", Ref(after, o) } };
            }
        }

        sealed class SetPropertyEdit : PrefabEdit
        {
            readonly string path, kind;
            readonly object raw;
            PropertyValue value;
            int enumIndex = -1;
            string referenceId;        // the persistent id the property references afterwards (null for none)
            bool inside;               // a reference to an object of the same prefab
            UnityEngine.Object asset;  // a reviewed asset reference

            public SetPropertyEdit(Dictionary<string, object> a)
            {
                path = Authoring.Str(a, "path", true);
                kind = Authoring.Str(a, "kind", true);
                raw = a["value"];
            }

            public override void Check(PrefabInfo p, UnityEngine.Object target)
            {
                var c = target as Component;
                if (c == null) throw new Refusal("OBJECT_REFUSED", "the id names a GameObject; a Component is expected");
                if (c is Transform) throw new Refusal("PROPERTY_UNSUPPORTED", "a Transform is set with set-prefab-transform");
                var so = new SerializedObject(c);
                var prop = Properties.WritableCore(c, so, path, kind);
                value = PropertyRules.Parse(kind, raw);
                if (kind == "enum")
                {
                    var names = prop.enumNames;
                    var matches = Enumerable.Range(0, names.Length).Where(i => names[i] == value.String).ToList();
                    if (matches.Count != 1) throw new Refusal("VALUE_INVALID", "the value is not exactly one of the enum's names");
                    enumIndex = matches[0];
                }
                inside = false;
                asset = null;
                referenceId = null;
                if (kind == "object" && value.String != null)
                {
                    UnityEngine.Object t;
                    if (!AssetIds.IsAsset(value.String)) throw new Refusal("VALUE_INVALID", "a prefab never references Scene objects; a reference names an object of this prefab or a reviewed asset");
                    if (PrefabIds.IsPersistent(value.String) && PrefabIds.Guid(value.String) == p.Guid)
                    {
                        if (!p.ById.TryGetValue(value.String, out t) || PrefabResolver.Role(p, t) != PrefabScope.Owned)
                            throw new Refusal("VALUE_INVALID", "the reference names no object this prefab owns");
                        inside = true;
                    }
                    else
                    {
                        var r = AssetResolver.Resolve(value.String);
                        string requiredKind = Properties.RequiredKind(c, path);
                        if (requiredKind != null && r.Kind != requiredKind)
                            throw new Refusal("VALUE_INVALID", "this field takes a " + requiredKind + " asset, not a " + r.Kind);
                        t = asset = r.Object;
                    }
                    if (!Properties.Accepts(PropertyRules.PPtrType(prop.type), t))
                        throw new Refusal("VALUE_INVALID", "the field does not accept that object's type");
                    referenceId = value.String;
                }
            }

            public override void Apply(Dictionary<string, UnityEngine.Object> contents, UnityEngine.Object copy)
            {
                var so = new SerializedObject(copy);
                var q = so.FindProperty(path);
                if (q == null) throw new Refusal("PROPERTY_UNSUPPORTED", "the component has no serialized property with this path");
                UnityEngine.Object t = referenceId == null ? null : inside ? contents[referenceId] : asset;
                Properties.Assign(q, value, enumIndex, t);
                so.ApplyModifiedPropertiesWithoutUndo();
                var back = new SerializedObject(copy).FindProperty(path);
                if (back == null || !Properties.Holds(back, value, enumIndex, t))
                    throw new Refusal("VALUE_NOT_APPLIED", "Unity or project code stored a different value than the one written; nothing was saved");
            }

            public override IEnumerable<string> Changes(PrefabInfo before, string id) { return new[] { id }; }

            public override Dictionary<string, object> Verify(PrefabInfo after, PrefabInfo before, string id)
            {
                UnityEngine.Object o;
                PrefabOf(after, id, out o);
                var q = new SerializedObject(o).FindProperty(path);
                if (q == null) throw new Refusal("PERSISTENCE_UNKNOWN", "the saved component has no such property");
                bool holds = kind == "object" ? (q.objectReferenceValue == null ? referenceId == null : SceneObjects.Id(q.objectReferenceValue) == referenceId)
                                              : Properties.Holds(q, value, enumIndex, null);
                if (!holds) throw new Refusal("PERSISTENCE_UNKNOWN", "the saved property holds another value");
                return new Dictionary<string, object> {
                    { "component", Ref(after, o) },
                    { "property", new Dictionary<string, object> { { "path", path }, { "kind", kind }, { "value", Properties.Read(q, kind, o) } } } };
            }
        }
    }
}
