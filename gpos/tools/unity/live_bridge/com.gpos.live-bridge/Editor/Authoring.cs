// GPOS live bridge — the Scene-authoring commands (bridge 1.1.0). Each reads or changes only what its name says,
// through fixed Unity Editor APIs; there is no generic call, no method name, no menu, no reflection target and no
// asset operation. Every mutating command:
//   1 resolves its objects by GlobalObjectId (Resolver) and refuses prefab-instance content (the prefab boundary);
//   2 compares every expected token with the current state — any difference is AUTHORING_CONFLICT before anything
//     changes (the Human's own editing is never blocked; a stale request is);
//   3 validates every value before Unity sees it;
//   4 runs as exactly one named Undo group ("GPOS: ..."), collapsed;
//   5 reads the result back; on any mismatch or exception the group is reverted with Undo.RevertAllDownToGroup and
//     the pre-state tokens are recomputed: equal means the state they cover was restored, different is
//     ROLLBACK_INCOMPLETE. A revert may leave the Scene marked dirty; the bridge never marks a Scene clean and never
//     saves one except through save-scene.
// Project code may run as a consequence (OnValidate, component callbacks, ExecuteAlways scripts, save callbacks).
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditorInternal;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Gpos.LiveBridge
{
    internal static class Authoring
    {
        public const int MaxChildrenListed = 200;
        public const int MaxComponentsListed = 64;
        public const int CatalogPage = 200;

        // ------------------------------------------------------------ arguments (every key is present; absent is null)

        static string Str(Dictionary<string, object> a, string key, bool required = false)
        {
            object v = a[key];
            if (v == null)
            {
                if (required) throw new Refusal("BAD_ARGUMENTS", key + " is required");
                return null;
            }
            var s = v as string;
            if (s == null) throw new Refusal("BAD_ARGUMENTS", key + " must be a string");
            return s;
        }

        static bool? Bool(Dictionary<string, object> a, string key)
        {
            object v = a[key];
            if (v == null) return null;
            if (!(v is bool)) throw new Refusal("BAD_ARGUMENTS", key + " must be true or false");
            return (bool)v;
        }

        static int? Int(Dictionary<string, object> a, string key, int min, int max)
        {
            object v = a[key];
            if (v == null) return null;
            if (!(v is double)) throw new Refusal("BAD_ARGUMENTS", key + " must be a whole number");
            double d = (double)v;
            if (d != Math.Floor(d) || d < min || d > max) throw new Refusal("BAD_ARGUMENTS", key + " must be between " + min + " and " + max);
            return (int)d;
        }

        static string Token(Dictionary<string, object> a, string key, bool required)
        {
            var s = Str(a, key, required);
            if (s != null) ObjectIds.CheckToken(s, key);
            return s;
        }

        static void Absent(Dictionary<string, object> a, string key, string why)
        {
            if (a[key] != null) throw new Refusal("BAD_ARGUMENTS", key + " is not accepted " + why);
        }

        static void Expect(string expected, string actual, string what)
        {
            if (expected != actual)
                throw new Refusal("AUTHORING_CONFLICT", what + " changed since it was inspected; re-inspect and decide again (nothing was changed)");
        }

        static Vector3? Vec3(Dictionary<string, object> a, string key)
        {
            if (a[key] == null) return null;
            var l = a[key] as List<object>;
            if (l == null || l.Count != 3) throw new Refusal("VALUE_INVALID", key + " is [x, y, z]");
            return new Vector3(PropertyRules.ToFloat32(l[0]), PropertyRules.ToFloat32(l[1]), PropertyRules.ToFloat32(l[2]));
        }

        static Quaternion? Rotation(Dictionary<string, object> a, string key)
        {
            if (a[key] == null) return null;
            var q = PropertyRules.UnitQuaternion(a[key]);
            return new Quaternion(q[0], q[1], q[2], q[3]);
        }

        static void PrefabBoundary(UnityEngine.Object o, params string[] allowed) { PrefabBoundary("the object", o, allowed); }

        static void PrefabBoundary(string what, UnityEngine.Object o, params string[] allowed)
        {
            string role = SceneObjects.Role(o);
            if (!allowed.Contains(role))
                throw new Refusal("PREFAB_BOUNDARY", what + " is " + role + " relative to a prefab instance; GPOS never creates, applies or reverts prefab overrides");
        }

        // ------------------------------------------------------------ dispatch

        public static Dictionary<string, object> Run(Request r)
        {
            var a = r.Args;
            switch (r.Command)
            {
                case "object-inspect": return Inspect(a);
                case "component-types": return ComponentTypes(a);
                case "properties": return ListProperties(a);
                case "create-gameobject": return Create(a);
                case "delete-gameobject": return Delete(a);
                case "set-parent": return SetParent(a);
                case "set-gameobject": return SetGameObject(a);
                case "set-transform": return SetTransform(a);
                case "add-component": return AddComponent(a);
                case "remove-component": return RemoveComponent(a);
                case "set-property": return SetProperty(a);
                case "save-scene": return SaveScene(a);
            }
            throw new Refusal("UNKNOWN_COMMAND", "the command is not in the closed allowlist");
        }

        // ------------------------------------------------------------ the mutation envelope

        // Pre-state: the tokens an operation promised not to disturb, recomputed from ids after a revert.
        sealed class PreState
        {
            readonly List<KeyValuePair<string, string>> items = new List<KeyValuePair<string, string>>();
            readonly Dictionary<string, string> values = new Dictionary<string, string>(StringComparer.Ordinal);

            public void Add(string kind, string key, string value)
            {
                string k = kind + ":" + key;
                if (values.ContainsKey(k)) return;
                items.Add(new KeyValuePair<string, string>(kind, key));
                values[k] = value;
            }

            public Dictionary<string, object> Current(out bool restored)
            {
                restored = true;
                var now = new Dictionary<string, object>();
                var resolver = new Resolver();
                foreach (var item in items)
                {
                    string k = item.Key + ":" + item.Value, value;
                    try { value = Recompute(resolver, item.Key, item.Value); }
                    catch (Exception) { value = null; }
                    now[k] = value;
                    if (value != values[k]) restored = false;
                }
                return now;
            }

            static string Recompute(Resolver resolver, string kind, string key)
            {
                switch (kind)
                {
                    case "object": return SceneObjects.ObjectToken(resolver.GameObject(key));
                    case "transform": return SceneObjects.TransformToken(resolver.GameObject(key).transform);
                    case "chain": return SceneObjects.ChainToken(resolver.GameObject(key));
                    case "subtree": return SceneObjects.SubtreeToken(resolver.GameObject(key));
                    case "component": return SceneObjects.ComponentToken(resolver.Component(key));
                    case "roots": return SceneObjects.RootsToken(Resolver.SceneByPath(key));
                }
                return null;
            }
        }

        // `change` makes and verifies the change inside the Undo group; `report` describes the result (ids, tokens)
        // only after the group is collapsed and closed, so nothing about the result is recorded in the group itself.
        static Dictionary<string, object> Mutate(string undoName, Scene scene, PreState pre, Action change,
                                                 Func<Dictionary<string, object>> report)
        {
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName(undoName);
            try { change(); }
            catch (Refusal refusal) { throw Reverted(group, scene, pre, refusal.Code, refusal.Message); }
            catch (Exception e) { throw Reverted(group, scene, pre, "AUTHORING_FAILED", "Unity raised " + e.GetType().Name + " during the operation"); }
            Undo.CollapseUndoOperations(group);
            Undo.IncrementCurrentGroup();
            Dictionary<string, object> result;
            try { result = report(); }
            catch (Refusal r) { result = new Dictionary<string, object> { { "report_problem", r.Code + ": " + r.Message } }; }
            result["undo_group"] = undoName;
            result["scene"] = SceneObjects.SceneInfo(scene);
            return result;
        }

        static Refusal Reverted(int group, Scene scene, PreState pre, string code, string message)
        {
            string revertProblem = null;
            try { Undo.RevertAllDownToGroup(group); }
            catch (Exception e) { revertProblem = e.GetType().Name; }
            bool restored;
            var current = pre.Current(out restored);
            var data = new Dictionary<string, object> {
                { "mutation_started", true }, { "reverted", revertProblem == null }, { "restored", restored && revertProblem == null },
                { "tokens_now", current }, { "scene", SceneObjects.SceneInfo(scene) } };
            if (!restored || revertProblem != null)
                return new Refusal("ROLLBACK_INCOMPLETE", message + "; the revert did not restore the state its pre-state tokens cover — re-inspect before anything else")
                       { Status = "FAILED", Data = data };
            return new Refusal(code, message + "; the operation was reverted and the state its pre-state tokens cover was restored")
                   { Status = code == "AUTHORING_FAILED" ? "FAILED" : "REFUSED", Data = data };
        }

        static Dictionary<string, object> Tokens(params object[] pairs)
        {
            var d = new Dictionary<string, object>();
            for (int i = 0; i < pairs.Length; i += 2) d[(string)pairs[i]] = pairs[i + 1];
            return d;
        }

        // ------------------------------------------------------------ inspection

        static Dictionary<string, object> Inspect(Dictionary<string, object> a)
        {
            string id = Str(a, "object"), path = Str(a, "scene");
            int limit = Int(a, "children_limit", 0, MaxChildrenListed) ?? MaxChildrenListed;
            if ((id == null) == (path == null)) throw new Refusal("BAD_ARGUMENTS", "exactly one of object and scene is required");
            if (path != null)
            {
                var s = Resolver.SceneByPath(path);
                var roots = s.GetRootGameObjects();
                return new Dictionary<string, object> {
                    { "scene", SceneObjects.SceneInfo(s) }, { "root_count", roots.Length },
                    { "roots", roots.Take(limit).Select(g => (object)SceneObjects.Ref(g)).ToList() }, { "roots_truncated", roots.Length > limit },
                    { "tokens", Tokens("scene_roots", SceneObjects.RootsToken(s)) } };
            }
            var go = new Resolver().GameObject(id);
            var t = go.transform;
            var problems = new Dictionary<string, object>();
            var components = go.GetComponents<Component>();
            var listed = new List<object>();
            for (int i = 0; i < components.Length && i < MaxComponentsListed; i++)
            {
                if (components[i] == null) { listed.Add(new Dictionary<string, object> { { "missing_script", true }, { "index", i } }); continue; }
                var entry = SceneObjects.Ref(components[i]);
                var c = components[i];
                entry["component_token"] = SceneObjects.TokenOrProblem(() => SceneObjects.ComponentToken(c), problems, "component " + entry["id"]);
                listed.Add(entry);
            }
            var children = new List<object>();
            for (int i = 0; i < t.childCount && i < limit; i++) children.Add(SceneObjects.Ref(t.GetChild(i).gameObject));
            var tokens = Tokens(
                "object", SceneObjects.ObjectToken(go), "transform", SceneObjects.TransformToken(t),
                "transform_chain", SceneObjects.TokenOrProblem(() => SceneObjects.ChainToken(go), problems, "transform_chain"),
                "subtree", SceneObjects.TokenOrProblem(() => SceneObjects.SubtreeToken(go), problems, "subtree"),
                "parent_object", t.parent == null ? null : SceneObjects.ObjectToken(t.parent.gameObject),
                "parent_transform_chain", t.parent == null ? null : SceneObjects.TokenOrProblem(() => SceneObjects.ChainToken(t.parent.gameObject), problems, "parent_transform_chain"),
                "scene_roots", SceneObjects.TokenOrProblem(() => SceneObjects.RootsToken(go.scene), problems, "scene_roots"));
            var rt = t as RectTransform;
            var transform = new Dictionary<string, object> {
                { "kind", rt != null ? "RectTransform" : "Transform" },
                { "local_position", SceneObjects.Floats(t.localPosition.x, t.localPosition.y, t.localPosition.z) },
                { "local_rotation", SceneObjects.Floats(t.localRotation.x, t.localRotation.y, t.localRotation.z, t.localRotation.w) },
                { "local_scale", SceneObjects.Floats(t.localScale.x, t.localScale.y, t.localScale.z) } };
            return new Dictionary<string, object> {
                { "object", SceneObjects.Ref(go) }, { "scene", SceneObjects.SceneInfo(go.scene) },
                { "active_self", go.activeSelf }, { "active_in_hierarchy", go.activeInHierarchy }, { "tag", go.tag }, { "layer", go.layer },
                { "static_flags", (long)(uint)GameObjectUtility.GetStaticEditorFlags(go) },
                { "parent", t.parent == null ? null : SceneObjects.Ref(t.parent.gameObject) }, { "sibling_index", t.GetSiblingIndex() },
                { "child_count", t.childCount }, { "children", children }, { "children_truncated", t.childCount > limit },
                { "components", listed }, { "components_truncated", components.Length > MaxComponentsListed },
                { "transform", transform }, { "tokens", tokens }, { "token_problems", problems } };
        }

        static Dictionary<string, object> ComponentTypes(Dictionary<string, object> a)
        {
            string query = Str(a, "query");
            if (query != null && (query.Length > 64 || query.Any(ch => ch < 0x20))) throw new Refusal("BAD_ARGUMENTS", "query has at most 64 printable characters");
            int page = Int(a, "page", 0, 10000) ?? 0;
            var catalog = Catalog.Build();
            var hits = catalog.Entries.Where(e => query == null || e.TypeId.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0).ToList();
            int start = page * CatalogPage;
            return new Dictionary<string, object> {
                { "catalog_digest", catalog.Digest }, { "catalog_count", catalog.Entries.Count }, { "count", hits.Count },
                { "page", page }, { "page_size", CatalogPage }, { "next_page", start + CatalogPage < hits.Count ? (object)(page + 1) : null },
                { "types", hits.Skip(start).Take(CatalogPage).Select(e => (object)e.ToData()).ToList() } };
        }

        static Dictionary<string, object> ListProperties(Dictionary<string, object> a)
        {
            var c = new Resolver().Component(Str(a, "component", true));
            string prefix = Str(a, "path_prefix");
            if (prefix != null && prefix.Length > PropertyRules.MaxPathLength) throw new Refusal("BAD_ARGUMENTS", "path_prefix is too long");
            return Properties.List(c, prefix, Int(a, "page", 0, 10000) ?? 0);
        }

        // ------------------------------------------------------------ hierarchy

        static Dictionary<string, object> Create(Dictionary<string, object> a)
        {
            var scene = Resolver.SceneByPath(Str(a, "scene", true));
            string name = Str(a, "name", true);
            ObjectIds.CheckName(name);
            var resolver = new Resolver();
            string parentId = Str(a, "parent");
            GameObject parent = parentId == null ? null : resolver.GameObject(parentId);
            var position = Vec3(a, "local_position");
            var rotation = Rotation(a, "local_rotation");
            var scale = Vec3(a, "local_scale");
            var pre = new PreState();
            if (parent != null)
            {
                if (parent.scene != scene) throw new Refusal("OBJECT_REFUSED", "the parent is in another Scene");
                PrefabBoundary("the parent", parent, SceneObjects.None);
                Absent(a, "expected_scene_roots_token", "with a parent");
                string token = SceneObjects.ObjectToken(parent);
                Expect(Token(a, "expected_parent_token", true), token, "the parent");
                pre.Add("object", parentId, token);
            }
            else
            {
                Absent(a, "expected_parent_token", "at the Scene root");
                string token = SceneObjects.RootsToken(scene);
                Expect(Token(a, "expected_scene_roots_token", true), token, "the Scene's root objects");
                pre.Add("roots", scene.path, token);
            }
            int count = parent != null ? parent.transform.childCount : scene.rootCount;
            int? sibling = Int(a, "sibling", 0, count);
            GameObject go = null;
            return Mutate("GPOS: create " + name, scene, pre, () =>
            {
                go = new GameObject(name);
                if (go.scene != scene) SceneManager.MoveGameObjectToScene(go, scene);
                if (parent != null) go.transform.SetParent(parent.transform, false);
                if (position.HasValue) go.transform.localPosition = position.Value;
                if (rotation.HasValue) go.transform.localRotation = rotation.Value;
                if (scale.HasValue) go.transform.localScale = scale.Value;
                if (sibling.HasValue) go.transform.SetSiblingIndex(sibling.Value);
                // Fully constructed, ids included, before the creation is recorded: Redo then brings back the same
                // object with the same GlobalObjectId (an id first requested after registration is not recorded).
                SceneObjects.Id(go);
                SceneObjects.Id(go.transform);
                Undo.RegisterCreatedObjectUndo(go, "GPOS: create " + name);
                if (go.scene != scene || go.transform.parent != (parent == null ? null : parent.transform) ||
                    (sibling.HasValue && go.transform.GetSiblingIndex() != sibling.Value) || go.name != name)
                    throw new Refusal("VALUE_NOT_APPLIED", "the new object is not where it was asked to be");
            }, () => new Dictionary<string, object> {
                    { "created", SceneObjects.Ref(go) },
                    { "tokens", Tokens("object", SceneObjects.ObjectToken(go), "transform", SceneObjects.TransformToken(go.transform),
                                       "parent_object", parent == null ? null : SceneObjects.ObjectToken(parent),
                                       "scene_roots", SceneObjects.RootsToken(scene)) } });
        }

        static Dictionary<string, object> Delete(Dictionary<string, object> a)
        {
            string id = Str(a, "object", true);
            var go = new Resolver().GameObject(id);
            PrefabBoundary(go, SceneObjects.None, SceneObjects.InstanceRoot);
            string subtree = SceneObjects.SubtreeToken(go);
            Expect(Token(a, "expected_subtree_token", true), subtree, "the object or something below it");
            var scene = go.scene;
            var parent = go.transform.parent;
            var pre = new PreState();
            pre.Add("subtree", id, subtree);
            if (parent != null) pre.Add("object", SceneObjects.Id(parent.gameObject), SceneObjects.ObjectToken(parent.gameObject));
            else pre.Add("roots", scene.path, SceneObjects.RootsToken(scene));
            string name = go.name;
            return Mutate("GPOS: delete " + name, scene, pre, () =>
            {
                Undo.DestroyObjectImmediate(go);
                if (go != null) throw new Refusal("AUTHORING_REFUSED", "Unity did not delete the object");
            }, () => new Dictionary<string, object> {
                    { "deleted", id },
                    { "tokens", Tokens("parent_object", parent == null ? null : SceneObjects.ObjectToken(parent.gameObject),
                                       "scene_roots", SceneObjects.RootsToken(scene)) } });
        }

        static Dictionary<string, object> SetParent(Dictionary<string, object> a)
        {
            var resolver = new Resolver();
            string id = Str(a, "object", true);
            var go = resolver.GameObject(id);
            PrefabBoundary(go, SceneObjects.None, SceneObjects.InstanceRoot);
            bool? keepWorld = Bool(a, "keep_world");
            if (!keepWorld.HasValue) throw new Refusal("BAD_ARGUMENTS", "keep_world is required");
            string parentId = Str(a, "parent");
            GameObject newParent = parentId == null ? null : resolver.GameObject(parentId);
            var oldParent = go.transform.parent == null ? null : go.transform.parent.gameObject;
            var scene = go.scene;
            if (newParent != null)
            {
                if (newParent.scene != scene) throw new Refusal("OBJECT_REFUSED", "the new parent is in another Scene; objects never move between Scenes");
                PrefabBoundary("the new parent", newParent, SceneObjects.None);
                if (newParent == go || newParent.transform.IsChildOf(go.transform))
                    throw new Refusal("AUTHORING_REFUSED", "the new parent is the object itself or one of its descendants");
            }
            if (oldParent != null) PrefabBoundary("the current parent", oldParent, SceneObjects.None);
            var pre = new PreState();
            string objectToken = SceneObjects.ObjectToken(go), transformToken = SceneObjects.TransformToken(go.transform);
            Expect(Token(a, "expected_object_token", true), objectToken, "the object");
            Expect(Token(a, "expected_transform_token", true), transformToken, "the object's Transform");
            pre.Add("object", id, objectToken);
            pre.Add("transform", id, transformToken);
            string oldParentToken = Token(a, "expected_old_parent_token", false), oldRoots = Token(a, "expected_old_scene_roots_token", false);
            if ((oldParentToken == null) == (oldRoots == null))
                throw new Refusal("BAD_ARGUMENTS", "exactly one of expected_old_parent_token and expected_old_scene_roots_token is required");
            if (oldParent != null)
            {
                if (oldParentToken == null) throw new Refusal("AUTHORING_CONFLICT", "the object has a parent now; it was inspected at the Scene root (nothing was changed)");
                string t = SceneObjects.ObjectToken(oldParent);
                Expect(oldParentToken, t, "the current parent");
                pre.Add("object", SceneObjects.Id(oldParent), t);
            }
            else
            {
                if (oldRoots == null) throw new Refusal("AUTHORING_CONFLICT", "the object is at the Scene root now; it was inspected under a parent (nothing was changed)");
                string t = SceneObjects.RootsToken(scene);
                Expect(oldRoots, t, "the Scene's root objects");
                pre.Add("roots", scene.path, t);
            }
            if (newParent != null)
            {
                Absent(a, "expected_new_scene_roots_token", "with a new parent");
                string t = SceneObjects.ObjectToken(newParent);
                Expect(Token(a, "expected_new_parent_token", true), t, "the new parent");
                pre.Add("object", parentId, t);
            }
            else
            {
                Absent(a, "expected_new_parent_token", "when the new parent is the Scene root");
                string t = SceneObjects.RootsToken(scene);
                Expect(Token(a, "expected_new_scene_roots_token", true), t, "the Scene's root objects");
                pre.Add("roots", scene.path, t);
            }
            if (keepWorld.Value)
            {
                // Keeping the world pose depends on every Transform from the Scene root down to the object and down
                // to the new parent, not only on the object's own local Transform.
                string chain = SceneObjects.ChainToken(go);
                Expect(Token(a, "expected_transform_chain_token", true), chain, "a Transform from the Scene root down to the object");
                pre.Add("chain", id, chain);
                if (newParent != null)
                {
                    string parentChain = SceneObjects.ChainToken(newParent);
                    Expect(Token(a, "expected_new_parent_chain_token", true), parentChain, "a Transform from the Scene root down to the new parent");
                    pre.Add("chain", parentId, parentChain);
                }
                else Absent(a, "expected_new_parent_chain_token", "when the new parent is the Scene root");
            }
            else
            {
                Absent(a, "expected_transform_chain_token", "with keep_world false");
                Absent(a, "expected_new_parent_chain_token", "with keep_world false");
            }
            int count = newParent != null ? newParent.transform.childCount : scene.rootCount;
            if (newParent == oldParent) count -= 1;
            int? sibling = Int(a, "sibling", 0, Math.Max(0, count));
            string name = go.name;
            return Mutate("GPOS: set parent of " + name, scene, pre, () =>
            {
                Undo.SetTransformParent(go.transform, newParent == null ? null : newParent.transform, keepWorld.Value, "GPOS: set parent of " + name);
                if (sibling.HasValue) Undo.SetSiblingIndex(go.transform, sibling.Value, "GPOS: set parent of " + name);
                if (go.transform.parent != (newParent == null ? null : newParent.transform) || go.scene != scene ||
                    (sibling.HasValue && go.transform.GetSiblingIndex() != sibling.Value))
                    throw new Refusal("VALUE_NOT_APPLIED", "Unity did not place the object under the new parent");
            }, () => new Dictionary<string, object> {
                    { "object", SceneObjects.Ref(go) },
                    { "tokens", Tokens("object", SceneObjects.ObjectToken(go), "transform", SceneObjects.TransformToken(go.transform),
                                       "transform_chain", SceneObjects.ChainToken(go),
                                       "old_parent_object", oldParent == null ? null : SceneObjects.ObjectToken(oldParent),
                                       "new_parent_object", newParent == null ? null : SceneObjects.ObjectToken(newParent),
                                       "scene_roots", SceneObjects.RootsToken(scene)) } });
        }

        // ------------------------------------------------------------ GameObject and Transform

        static Dictionary<string, object> SetGameObject(Dictionary<string, object> a)
        {
            string id = Str(a, "object", true);
            var go = new Resolver().GameObject(id);
            string name = Str(a, "name"), tag = Str(a, "tag");
            bool? active = Bool(a, "active");
            int? layer = Int(a, "layer", 0, 31);
            object flagsValue = a["static_flags"];
            if (name == null && tag == null && !active.HasValue && !layer.HasValue && flagsValue == null)
                throw new Refusal("BAD_ARGUMENTS", "at least one of name, active, tag, layer and static_flags is required");
            if (SceneObjects.Role(go) == SceneObjects.InstanceRoot)
            {
                if (tag != null || active.HasValue || layer.HasValue || flagsValue != null)
                    throw new Refusal("PREFAB_BOUNDARY", "on a prefab instance root only the name is set; anything else would be a prefab override");
            }
            else PrefabBoundary(go, SceneObjects.None);
            if (name != null) ObjectIds.CheckName(name);
            if (tag != null && !InternalEditorUtility.tags.Contains(tag)) throw new Refusal("VALUE_INVALID", "the tag is not defined in this project");
            StaticEditorFlags? flags = null;
            if (flagsValue != null)
            {
                long mask = 0;
                foreach (var f in Enum.GetValues(typeof(StaticEditorFlags))) mask |= Convert.ToInt64(f);
                double d = flagsValue is double ? (double)flagsValue : -1;
                if (d != Math.Floor(d) || d < 0 || d > int.MaxValue || ((long)d & ~mask) != 0)
                    throw new Refusal("VALUE_INVALID", "static_flags is a combination of StaticEditorFlags bits");
                flags = (StaticEditorFlags)(int)(long)d;
            }
            string token = SceneObjects.ObjectToken(go);
            Expect(Token(a, "expected_object_token", true), token, "the object");
            var pre = new PreState();
            pre.Add("object", id, token);
            string label = name ?? go.name;
            return Mutate("GPOS: set " + label, go.scene, pre, () =>
            {
                Undo.RecordObject(go, "GPOS: set " + label);
                if (name != null) go.name = name;
                if (tag != null) go.tag = tag;
                if (layer.HasValue) go.layer = layer.Value;
                if (active.HasValue) go.SetActive(active.Value);
                if (flags.HasValue) GameObjectUtility.SetStaticEditorFlags(go, flags.Value);
                if ((name != null && go.name != name) || (tag != null && go.tag != tag) || (layer.HasValue && go.layer != layer.Value) ||
                    (active.HasValue && go.activeSelf != active.Value) || (flags.HasValue && GameObjectUtility.GetStaticEditorFlags(go) != flags.Value))
                    throw new Refusal("VALUE_NOT_APPLIED", "Unity or project code kept a different value");
            }, () => new Dictionary<string, object> { { "object", SceneObjects.Ref(go) }, { "tokens", Tokens("object", SceneObjects.ObjectToken(go)) } });
        }

        static Dictionary<string, object> SetTransform(Dictionary<string, object> a)
        {
            string id = Str(a, "object", true);
            var go = new Resolver().GameObject(id);
            PrefabBoundary(go, SceneObjects.None, SceneObjects.InstanceRoot);
            var position = Vec3(a, "local_position");
            var rotation = Rotation(a, "local_rotation");
            var scale = Vec3(a, "local_scale");
            if (!position.HasValue && !rotation.HasValue && !scale.HasValue)
                throw new Refusal("BAD_ARGUMENTS", "at least one of local_position, local_rotation and local_scale is required");
            var t = go.transform;
            string token = SceneObjects.TransformToken(t);
            Expect(Token(a, "expected_transform_token", true), token, "the object's Transform");
            var pre = new PreState();
            pre.Add("transform", id, token);
            return Mutate("GPOS: set transform of " + go.name, go.scene, pre, () =>
            {
                Undo.RecordObject(t, "GPOS: set transform of " + go.name);
                if (position.HasValue) t.localPosition = position.Value;
                if (rotation.HasValue) t.localRotation = rotation.Value;
                if (scale.HasValue) t.localScale = scale.Value;
                var q = t.localRotation;
                if ((position.HasValue && !Same(t.localPosition, position.Value)) ||
                    (scale.HasValue && !Same(t.localScale, scale.Value)) ||
                    (rotation.HasValue && (Math.Abs(q.x - rotation.Value.x) > 1e-6 || Math.Abs(q.y - rotation.Value.y) > 1e-6 ||
                                           Math.Abs(q.z - rotation.Value.z) > 1e-6 || Math.Abs(q.w - rotation.Value.w) > 1e-6)))
                    throw new Refusal("VALUE_NOT_APPLIED", "Unity or project code kept a different Transform");
            }, () => new Dictionary<string, object> {
                    { "object", SceneObjects.Ref(go) },
                    { "transform", new Dictionary<string, object> {
                        { "local_position", SceneObjects.Floats(t.localPosition.x, t.localPosition.y, t.localPosition.z) },
                        { "local_rotation", SceneObjects.Floats(t.localRotation.x, t.localRotation.y, t.localRotation.z, t.localRotation.w) },
                        { "local_scale", SceneObjects.Floats(t.localScale.x, t.localScale.y, t.localScale.z) } } },
                    { "tokens", Tokens("transform", SceneObjects.TransformToken(t), "transform_chain", SceneObjects.ChainToken(go)) } });
        }

        static bool Same(Vector3 a, Vector3 b) { return a.x.Equals(b.x) && a.y.Equals(b.y) && a.z.Equals(b.z); }

        // ------------------------------------------------------------ components

        static Dictionary<string, object> AddComponent(Dictionary<string, object> a)
        {
            string id = Str(a, "object", true), typeId = Str(a, "type_id", true);
            if (!ObjectIds.TypeId.IsMatch(typeId)) throw new Refusal("TYPE_NOT_IN_CATALOG", "a component type is named <assembly>::<full name>");
            string digest = Str(a, "expected_catalog_digest", true);
            if (!ObjectIds.Digest.IsMatch(digest)) throw new Refusal("BAD_ARGUMENTS", "expected_catalog_digest must be 64 hex");
            var go = new Resolver().GameObject(id);
            PrefabBoundary(go, SceneObjects.None);
            var catalog = Catalog.Build();
            var entry = catalog.Find(typeId);
            if (entry == null) throw new Refusal("TYPE_NOT_IN_CATALOG", "the type is not in the component catalog");
            if (catalog.Digest != digest) throw new Refusal("CATALOG_CHANGED", "the component catalog changed since it was read (a recompile or Domain Reload); read it again (nothing was changed)");
            string token = SceneObjects.ObjectToken(go);
            Expect(Token(a, "expected_object_token", true), token, "the object");
            var type = catalog.Types[typeId];
            if (entry.DisallowMultiple && go.GetComponent(type) != null)
                throw new Refusal("AUTHORING_REFUSED", "the type allows one component per object and the object already has one");
            var pre = new PreState();
            pre.Add("object", id, token);
            Component c = null;
            var added = new List<Component>();
            return Mutate("GPOS: add " + type.Name + " to " + go.name, go.scene, pre, () =>
            {
                var before = go.GetComponents<Component>();
                c = Undo.AddComponent(go, type);
                if (c == null) throw new Refusal("AUTHORING_REFUSED", "Unity did not add the component (it conflicts with a component on the object)");
                added = go.GetComponents<Component>().Where(x => x != null && !before.Contains(x)).ToList();
            }, () => new Dictionary<string, object> {
                    { "component", SceneObjects.Ref(c) }, { "added_components", added.Select(x => (object)SceneObjects.Ref(x)).ToList() },
                    { "tokens", Tokens("object", SceneObjects.ObjectToken(go), "component", SceneObjects.ComponentToken(c)) } });
        }

        static Dictionary<string, object> RemoveComponent(Dictionary<string, object> a)
        {
            string id = Str(a, "component", true);
            var c = new Resolver().Component(id);
            var go = c.gameObject;
            PrefabBoundary(c, SceneObjects.None);
            PrefabBoundary(go, SceneObjects.None);
            if (c is Transform) throw new Refusal("AUTHORING_REFUSED", "a Transform is never removed");
            string componentToken = SceneObjects.ComponentToken(c), objectToken = SceneObjects.ObjectToken(go);
            Expect(Token(a, "expected_component_token", true), componentToken, "the component");
            Expect(Token(a, "expected_object_token", true), objectToken, "the object");
            var others = go.GetComponents<Component>().Where(x => x != null && x != c).ToList();
            foreach (var other in others)
                foreach (var required in Catalog.Required(other.GetType()))
                    if (required.IsInstanceOfType(c) && !others.Any(x => x != other && required.IsInstanceOfType(x)))
                        throw new Refusal("AUTHORING_REFUSED", other.GetType().Name + " on the object requires this component");
            var pre = new PreState();
            pre.Add("object", SceneObjects.Id(go), objectToken);
            pre.Add("component", id, componentToken);
            string typeName = c.GetType().Name;
            return Mutate("GPOS: remove " + typeName + " from " + go.name, go.scene, pre, () =>
            {
                Undo.DestroyObjectImmediate(c);
                if (c != null) throw new Refusal("AUTHORING_REFUSED", "Unity did not remove the component");
            }, () => new Dictionary<string, object> { { "removed", id }, { "object", SceneObjects.Ref(go) }, { "tokens", Tokens("object", SceneObjects.ObjectToken(go)) } });
        }

        // resolve, token, allowlisted property, kind, validate, Undo group, write, apply, re-read, verify, revert on mismatch
        static Dictionary<string, object> SetProperty(Dictionary<string, object> a)
        {
            var resolver = new Resolver();
            string id = Str(a, "component", true), path = Str(a, "path", true), kind = Str(a, "kind", true);
            var c = resolver.Component(id);
            if (c is Transform) throw new Refusal("PROPERTY_UNSUPPORTED", "a Transform is set with set-transform");
            PrefabBoundary(c, SceneObjects.None);
            PrefabBoundary(c.gameObject, SceneObjects.None);
            string token = SceneObjects.ComponentToken(c);
            Expect(Token(a, "expected_component_token", true), token, "the component");
            var so = new SerializedObject(c);
            var p = Properties.Writable(c, so, path, kind);
            var value = PropertyRules.Parse(kind, a["value"]);
            int enumIndex = -1;
            UnityEngine.Object target = null;
            if (kind == "enum")
            {
                var names = p.enumNames;
                var matches = Enumerable.Range(0, names.Length).Where(i => names[i] == value.String).ToList();
                if (matches.Count != 1) throw new Refusal("VALUE_INVALID", "the value is not exactly one of the enum's names");
                enumIndex = matches[0];
            }
            if (kind == "object" && value.String != null)
            {
                target = resolver.Resolve(value.String);
                if (SceneObjects.GameObjectOf(target).scene != c.gameObject.scene)
                    throw new Refusal("VALUE_INVALID", "an object reference names an object of the same Scene");
                if (!Properties.Accepts(PropertyRules.PPtrType(p.type), target))
                    throw new Refusal("VALUE_INVALID", "the field does not accept that object's type");
            }
            var pre = new PreState();
            pre.Add("component", id, token);
            bool changed = false;
            return Mutate("GPOS: set " + path + " of " + c.GetType().Name + " on " + c.gameObject.name, c.gameObject.scene, pre, () =>
            {
                Properties.Assign(p, value, enumIndex, target);
                changed = so.ApplyModifiedProperties();
                var q = new SerializedObject(c).FindProperty(path);
                if (q == null || !Properties.Holds(q, value, enumIndex, target))
                    throw new Refusal("VALUE_NOT_APPLIED", "Unity or project code stored a different value than the one written");
            }, () => new Dictionary<string, object> {
                    { "component", SceneObjects.Ref(c) }, { "changed", changed },
                    { "property", new Dictionary<string, object> { { "path", path }, { "kind", kind },
                                                                   { "value", Properties.Read(new SerializedObject(c).FindProperty(path), kind, c) } } },
                    { "tokens", Tokens("component", SceneObjects.ComponentToken(c)) } });
        }

        // ------------------------------------------------------------ save

        static Dictionary<string, object> SaveScene(Dictionary<string, object> a)
        {
            var scene = Resolver.SceneByPath(Str(a, "scene", true));
            if (scene.path.Length == 0) throw new Refusal("SCENE_NOT_SAVED", "an unsaved Scene has no path; GPOS never chooses one");
            bool saved;
            try { saved = EditorSceneManager.SaveScene(scene); }
            catch (Exception e)
            {
                throw new Refusal("AUTHORING_FAILED", "Unity raised " + e.GetType().Name + " while saving the Scene")
                      { Status = "FAILED", Data = new Dictionary<string, object> { { "mutation_started", true }, { "scene", SceneObjects.SceneInfo(scene) } } };
            }
            if (!saved)
                throw new Refusal("AUTHORING_FAILED", "Unity did not save the Scene")
                      { Status = "FAILED", Data = new Dictionary<string, object> { { "mutation_started", true }, { "scene", SceneObjects.SceneInfo(scene) } } };
            return new Dictionary<string, object> { { "saved", true }, { "scene", SceneObjects.SceneInfo(scene) } };
        }
    }
}
