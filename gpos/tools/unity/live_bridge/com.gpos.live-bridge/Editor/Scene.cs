// GPOS live bridge — Scene objects: resolution by GlobalObjectId, the prefab boundary, references and the
// optimistic-concurrency tokens. Everything here reads; nothing changes the Scene.
//
// An object id resolves only to a GameObject or Component of a loaded, saved Scene under Assets/ whose GUID the id
// names, that is not hidden from or excluded from saving by its hide flags, and whose canonical id is exactly the
// string given. Unity's id lookup misses a component that Redo re-created until the Scene is next saved, although
// the component reports that same id; when the lookup misses, the Scene's own objects are compared by id (bounded). Tokens (TokenBuilder domains) cover:
//   object     id, name, activeSelf, tag, layer, static flags, parent id, sibling index, ordered child ids and
//              the ordered component list (type@id, or MISSING#index for a missing script)
//   transform  the Transform's id and kind, local position, rotation and scale (and a RectTransform's anchors,
//              anchored position, size delta and pivot)
//   chain      the Scene path and, from the Scene root down to the object, each object's id and transform token
//   component  id, type, enabled (a Behaviour) and every top-level serialized property — visible or hidden — as
//              path, property type and type name plus an opaque hash of its whole value, nested values included:
//              Unity's content hash for plain values, and for every object reference the referenced object's
//              GlobalObjectId (Unity's own hash of a reference follows the session-local instance id, which Undo
//              and Scene reloads change). More than MaxProperties top-level properties or MaxNodes values is
//              AUTHORING_LIMIT. Hashing a property never makes it listed, valued or writable.
//   subtree    pre-order, the object and every descendant: id, object token, transform token and the component
//              token of each component
//   roots      the Scene path and the ordered root object ids
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Gpos.LiveBridge
{
    internal sealed class Resolver
    {
        public const int MaxResolutions = 256;
        public const int MaxScan = 100000;
        int count;

        public UnityEngine.Object Resolve(string id)
        {
            string guid = ObjectIds.SceneGuid(id);
            if (++count > MaxResolutions) throw new Refusal("AUTHORING_LIMIT", "a request resolves at most " + MaxResolutions + " objects");
            Scene scene = default(Scene);
            bool loaded = false;
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var s = SceneManager.GetSceneAt(i);
                if (s.isLoaded && s.path.Length > 0 && AssetDatabase.AssetPathToGUID(s.path) == guid) { scene = s; loaded = true; break; }
            }
            if (!loaded) throw new Refusal("OBJECT_NOT_FOUND", "the object's Scene is not loaded in this Editor");
            if (!scene.path.StartsWith("Assets/", StringComparison.Ordinal)) throw new Refusal("OBJECT_REFUSED", "the object's Scene is not under Assets/");
            GlobalObjectId gid;
            if (!GlobalObjectId.TryParse(id, out gid)) throw new Refusal("OBJECT_REFUSED", "the object id is not a GlobalObjectId");
            var o = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) ?? Scan(scene, id);
            if (o == null) throw new Refusal("OBJECT_NOT_FOUND", "no object with this id exists in its loaded Scene");
            var go = SceneObjects.GameObjectOf(o);
            if (go == null) throw new Refusal("OBJECT_REFUSED", "the id names neither a GameObject nor a Component");
            if (EditorUtility.IsPersistent(o) || go.scene != scene) throw new Refusal("OBJECT_REFUSED", "the id does not name an object of that Scene");
            if (SceneObjects.Hidden(o)) throw new Refusal("OBJECT_REFUSED", "the object is hidden or excluded from saving");
            if (SceneObjects.Id(o) != id) throw new Refusal("OBJECT_REFUSED", "the id is not the object's canonical id");
            return o;
        }

        // The GameObject or Component of `scene` whose GlobalObjectId is exactly `id`, or null.
        static UnityEngine.Object Scan(Scene scene, string id)
        {
            var all = new List<UnityEngine.Object>();
            foreach (var root in scene.GetRootGameObjects())
                foreach (var t in root.GetComponentsInChildren<Transform>(true))
                {
                    all.Add(t.gameObject);
                    foreach (var c in t.GetComponents<Component>())
                        if (c != null) all.Add(c);
                    if (all.Count > MaxScan) return null;
                }
            var ids = SceneObjects.Ids(all.ToArray());
            for (int i = 0; i < ids.Length; i++)
                if (ids[i] == id) return all[i];
            return null;
        }

        public GameObject GameObject(string id)
        {
            var go = Resolve(id) as GameObject;
            if (go == null) throw new Refusal("OBJECT_REFUSED", "the id names a Component; a GameObject is expected");
            return go;
        }

        public Component Component(string id)
        {
            var c = Resolve(id) as Component;
            if (c == null) throw new Refusal("OBJECT_REFUSED", "the id names a GameObject; a Component is expected");
            return c;
        }

        // A loaded Scene by its exact path; unsaved Scenes have none.
        public static Scene SceneByPath(string path)
        {
            ObjectIds.CheckScenePath(path);
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var s = SceneManager.GetSceneAt(i);
                if (s.path == path)
                {
                    if (!s.isLoaded) throw new Refusal("OBJECT_NOT_FOUND", "the Scene is not loaded");
                    return s;
                }
            }
            throw new Refusal("OBJECT_NOT_FOUND", "no loaded Scene has this path");
        }
    }

    internal static class SceneObjects
    {
        public const int MaxProperties = 2048;
        public const int MaxNodes = 200000;
        public const int MaxSubtreeObjects = 2000;
        public const int MaxSubtreeComponents = 20000;
        public const int MaxChildren = 4096;
        public const int MaxRoots = 10000;
        public const int MaxDepth = 1000;
        public const string None = "NONE", InstanceRoot = "INSTANCE_ROOT", InstanceContent = "INSTANCE_CONTENT",
                            AddedOverride = "ADDED_OVERRIDE", Missing = "MISSING";

        public static GameObject GameObjectOf(UnityEngine.Object o)
        {
            var go = o as GameObject;
            if (go != null) return go;
            var c = o as Component;
            return c != null ? c.gameObject : null;
        }

        public static bool Hidden(UnityEngine.Object o)
        {
            const HideFlags never = HideFlags.DontSaveInEditor | HideFlags.HideInHierarchy | HideFlags.NotEditable;
            var go = GameObjectOf(o);
            if ((go.hideFlags & never) != 0) return true;
            return o is Component && (o.hideFlags & (never | HideFlags.HideInInspector)) != 0;
        }

        public static string Id(UnityEngine.Object o) { return GlobalObjectId.GetGlobalObjectIdSlow(o).ToString(); }

        public static string[] Ids(UnityEngine.Object[] objects)
        {
            var ids = new GlobalObjectId[objects.Length];
            GlobalObjectId.GetGlobalObjectIdsSlow(objects, ids);
            return ids.Select(i => i.ToString()).ToArray();
        }

        // The object's place relative to prefab instances: NONE, INSTANCE_ROOT (an outermost instance root),
        // INSTANCE_CONTENT (inside an instance), ADDED_OVERRIDE (added to an instance) or MISSING (asset gone).
        public static string Role(UnityEngine.Object o)
        {
            var go = GameObjectOf(o);
            var c = o as Component;
            if (c != null && PrefabUtility.IsAddedComponentOverride(c)) return AddedOverride;
            if (PrefabUtility.IsAddedGameObjectOverride(go)) return AddedOverride;
            bool inside = false;
            for (var t = go.transform; t != null; t = t.parent)
                if (PrefabUtility.IsPartOfPrefabInstance(t.gameObject)) { inside = true; break; }
            if (!inside) return None;
            if (!PrefabUtility.IsPartOfPrefabInstance(go)) return AddedOverride;
            if (PrefabUtility.GetPrefabInstanceStatus(go) != PrefabInstanceStatus.Connected) return Missing;
            return PrefabUtility.IsOutermostPrefabInstanceRoot(go) ? InstanceRoot : InstanceContent;
        }

        public static Dictionary<string, object> SceneInfo(Scene s)
        {
            return new Dictionary<string, object> { { "path", s.path }, { "name", s.name }, { "loaded", s.isLoaded }, { "dirty", s.isDirty } };
        }

        public static Dictionary<string, object> Ref(UnityEngine.Object o)
        {
            var go = GameObjectOf(o);
            var d = new Dictionary<string, object> { { "id", Id(o) }, { "name", Clip(go.name, ObjectIds.MaxNameLength) }, { "scene", go.scene.path }, { "prefab", Role(o) } };
            var c = o as Component;
            if (c == null) d["kind"] = "GAME_OBJECT";
            else
            {
                d["kind"] = "COMPONENT";
                d["type"] = Catalog.TypeKey(c.GetType());
                d["gameobject"] = Id(go);
                var b = c as Behaviour;
                if (b != null) d["enabled"] = b.enabled;
            }
            return d;
        }

        public static string Clip(string s, int n) { return s == null ? "" : s.Length > n ? s.Substring(0, n) : s; }

        public static List<object> Floats(params float[] values) { return values.Select(v => (object)(double)v).ToList(); }

        // ------------------------------------------------------------ tokens

        public static string ObjectToken(GameObject go)
        {
            var t = go.transform;
            if (t.childCount > MaxChildren) throw new Refusal("AUTHORING_LIMIT", "the object has more than " + MaxChildren + " children");
            var b = new TokenBuilder(TokenBuilder.ObjectDomain).Field(Id(go)).Field(go.name).Bool(go.activeSelf).Field(go.tag)
                .Int(go.layer).UInt((uint)GameObjectUtility.GetStaticEditorFlags(go))
                .Field(t.parent == null ? "" : Id(t.parent.gameObject)).Int(t.GetSiblingIndex()).Int(t.childCount);
            var children = new UnityEngine.Object[t.childCount];
            for (int i = 0; i < t.childCount; i++) children[i] = t.GetChild(i).gameObject;
            foreach (var id in Ids(children)) b.Field(id);
            var components = go.GetComponents<Component>();
            b.Int(components.Length);
            for (int i = 0; i < components.Length; i++)
                b.Field(components[i] == null ? "MISSING#" + i : Catalog.TypeKey(components[i].GetType()) + "@" + Id(components[i]));
            return b.Finish();
        }

        public static string TransformToken(Transform t)
        {
            var rt = t as RectTransform;
            var b = new TokenBuilder(TokenBuilder.TransformDomain).Field(Id(t)).Field(rt != null ? "RectTransform" : "Transform");
            var p = t.localPosition; var r = t.localRotation; var s = t.localScale;
            b.Floats(p.x, p.y, p.z, r.x, r.y, r.z, r.w, s.x, s.y, s.z);
            if (rt != null)
                b.Floats(rt.anchorMin.x, rt.anchorMin.y, rt.anchorMax.x, rt.anchorMax.y, rt.anchoredPosition.x, rt.anchoredPosition.y,
                         rt.sizeDelta.x, rt.sizeDelta.y, rt.pivot.x, rt.pivot.y);
            return b.Finish();
        }

        public static string ChainToken(GameObject go)
        {
            var chain = new List<Transform>();
            for (var t = go.transform; t != null; t = t.parent)
            {
                if (chain.Count >= MaxDepth) throw new Refusal("AUTHORING_LIMIT", "the object is nested deeper than " + MaxDepth);
                chain.Add(t);
            }
            chain.Reverse();
            var b = new TokenBuilder(TokenBuilder.ChainDomain).Field(go.scene.path).Int(chain.Count);
            foreach (var t in chain) b.Field(Id(t.gameObject)).Field(TransformToken(t));
            return b.Finish();
        }

        public static string ComponentToken(Component c)
        {
            var b = new TokenBuilder(TokenBuilder.ComponentDomain).Field(Id(c)).Field(Catalog.TypeKey(c.GetType()));
            var behaviour = c as Behaviour;
            if (behaviour != null) b.Bool(behaviour.enabled);
            var so = new SerializedObject(c);
            var it = so.GetIterator();
            int n = 0, nodes = 0;
            if (it.Next(true))
            {
                do
                {
                    if (++n > MaxProperties)
                        throw new Refusal("AUTHORING_LIMIT", "the component has more than " + MaxProperties + " top-level serialized properties");
                    b.Field(it.propertyPath).Field(it.propertyType.ToString()).Field(it.type).Field(ValueHash(it, ref nodes));
                } while (it.Next(false));
            }
            return b.Int(n).Finish();
        }

        static readonly HashSet<string> PlainElements = new HashSet<string>(StringComparer.Ordinal) {
            "bool", "char", "sbyte", "byte", "short", "ushort", "int", "uint", "long", "ulong", "float", "double", "string",
            "SInt8", "UInt8", "SInt16", "UInt16", "SInt32", "UInt32", "SInt64", "UInt64", "Vector2", "Vector3", "Vector4",
            "Vector2Int", "Vector3Int", "Quaternion", "Color", "Color32", "Rect", "RectInt", "Bounds", "BoundsInt", "Hash128" };

        // An opaque hash of one property's whole value, independent of session-local instance ids.
        static string ValueHash(SerializedProperty property, ref int nodes)
        {
            var b = new TokenBuilder("v");
            var p = property.Copy();
            var end = property.GetEndProperty(true);
            bool enter = true;
            bool first = true;
            while (first || (p.Next(enter) && !SerializedProperty.EqualContents(p, end)))
            {
                first = false;
                if (++nodes > MaxNodes) throw new Refusal("AUTHORING_LIMIT", "the component holds more than " + MaxNodes + " serialized values");
                var type = p.propertyType;
                b.Field(p.propertyPath).Field(type.ToString());
                if (type == SerializedPropertyType.ObjectReference)
                {
                    b.Field(ReferenceHash(p));
                    enter = false;
                }
                else if (type == SerializedPropertyType.ManagedReference)
                {
                    b.Field(p.managedReferenceFullTypename);
                    enter = true;
                }
                else if (type == SerializedPropertyType.Generic || type == SerializedPropertyType.ExposedReference)
                {
                    if (p.isArray && PlainElements.Contains(p.arrayElementType)) { b.UInt(p.contentHash); enter = false; }
                    else enter = true;
                }
                else
                {
                    b.UInt(p.contentHash);   // a plain value (number, text, vector, curve, gradient ...): no object reference inside
                    enter = false;
                }
            }
            return b.Finish();
        }

        static string ReferenceHash(SerializedProperty p)
        {
            var target = p.objectReferenceValue;
            if (target == null) return "none:" + p.contentHash.ToString(System.Globalization.CultureInfo.InvariantCulture);   // null or a missing target
            var id = GlobalObjectId.GetGlobalObjectIdSlow(target);
            if (id.identifierType == 0) return "unsaved:" + target.GetType().FullName + ":" + target.name;
            return id.ToString();
        }

        public static string SubtreeToken(GameObject root)
        {
            var b = new TokenBuilder(TokenBuilder.SubtreeDomain);
            int objects = 0, components = 0;
            var stack = new Stack<Transform>();
            stack.Push(root.transform);
            while (stack.Count > 0)
            {
                var t = stack.Pop();
                if (++objects > MaxSubtreeObjects) throw new Refusal("AUTHORING_LIMIT", "the subtree has more than " + MaxSubtreeObjects + " objects");
                var go = t.gameObject;
                b.Field(Id(go)).Field(ObjectToken(go)).Field(TransformToken(t));
                foreach (var c in go.GetComponents<Component>())
                {
                    if (c == null) { b.Field("MISSING"); continue; }
                    if (++components > MaxSubtreeComponents) throw new Refusal("AUTHORING_LIMIT", "the subtree has more than " + MaxSubtreeComponents + " components");
                    b.Field(ComponentToken(c));
                }
                for (int i = t.childCount - 1; i >= 0; i--) stack.Push(t.GetChild(i));
            }
            return b.Int(objects).Finish();
        }

        public static string RootsToken(Scene s)
        {
            var roots = s.GetRootGameObjects();
            if (roots.Length > MaxRoots) throw new Refusal("AUTHORING_LIMIT", "the Scene has more than " + MaxRoots + " root objects");
            var b = new TokenBuilder(TokenBuilder.RootsDomain).Field(s.path).Int(roots.Length);
            foreach (var id in Ids(roots.Cast<UnityEngine.Object>().ToArray())) b.Field(id);
            return b.Finish();
        }

        // A token, or null with the reason recorded, for inspection results.
        public static object TokenOrProblem(Func<string> token, Dictionary<string, object> problems, string key)
        {
            try { return token(); }
            catch (Refusal r) { problems[key] = r.Code + ": " + r.Message; return null; }
        }
    }
}
