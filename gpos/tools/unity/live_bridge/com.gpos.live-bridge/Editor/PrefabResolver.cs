// GPOS live bridge — prefab identity, ownership, scope, the whole-prefab token and the guards of prefab mutation
// (bridge 1.3.0). Everything here reads; nothing changes a prefab, a Scene or a file.
//
// A prefab object id (PrefabIds) resolves only to a persistent GameObject or Component of a prefab file whose
// canonical GlobalObjectId is exactly the string given. The prefab is classified (REGULAR, VARIANT or MODEL; ASSETS
// or PACKAGE) and every object of its hierarchy gets an ownership role: OWNED (stored as this prefab's own object),
// NESTED_ROOT / NESTED_CONTENT (an instance of another prefab inside it) or VARIANT_INHERITED (from a Variant's base).
// A prefab may be mutated only when it is a regular prefab below Assets/ with no nested prefab instance, no missing
// script, no hidden content and no embedded asset (PrefabScope), and — at the moment of the mutation — no Prefab Mode
// stage is open, nothing in its file is dirty, version control does not hold it and the OS lets GPOS write it.
//
// The prefab token (TokenBuilder.PrefabDomain) covers the GUID, the root id, the canonical path, the prefab type and
// source, the SHA-256 of the file and of its .meta, and for every object of the file in hierarchy order its id,
// ownership role, dirty flag, hide flags and state: a GameObject's object token (name, active, tag, layer, static
// flags, parent, sibling index, children, ordered components), its Transform token and its whole serialized state; a
// Component's component token (every serialized property, hidden ones included, references as GlobalObjectIds). The
// token is whole-prefab: any change of any object of the prefab changes it. It is given only for a regular prefab
// without nested prefab instances; a Variant's or nested prefab's effective content also depends on other files,
// which the token does not cover (NOT_COVERED).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Gpos.LiveBridge
{
    internal sealed class PrefabInfo
    {
        public string Id, Guid, Path, Source, Type;
        public GameObject Root;
        public readonly List<GameObject> GameObjects = new List<GameObject>();
        public readonly List<UnityEngine.Object> Objects = new List<UnityEngine.Object>();   // each GameObject, then its components
        public readonly Dictionary<UnityEngine.Object, string> Ids = new Dictionary<UnityEngine.Object, string>();
        public readonly Dictionary<string, UnityEngine.Object> ById = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
        public readonly Dictionary<UnityEngine.Object, string> Ownership = new Dictionary<UnityEngine.Object, string>();
        public readonly Dictionary<GameObject, int> Depth = new Dictionary<GameObject, int>();
        public int ComponentCount;
        public bool Nested, MissingScript, Hidden, Embedded;

        public List<string> StaticReasons()
        {
            var r = PrefabScope.Reasons(Type, Source, Nested, MissingScript, Hidden);
            if (Embedded) r.Add(PrefabScope.Embedded);
            return r;
        }

        public bool Covered { get { return Type == PrefabScope.Regular && !Nested && !Embedded; } }

        public Dictionary<string, object> ToData()
        {
            return new Dictionary<string, object> {
                { "id", Id }, { "name", SceneObjects.Clip(Root.name, ObjectIds.MaxNameLength) }, { "path", Path }, { "guid", Guid },
                { "source", Source }, { "type", Type } };
        }
    }

    // The covered state of one prefab: the token and what it is made of.
    internal sealed class PrefabState
    {
        public string Token, File, Meta;
        public bool Dirty;
        public readonly Dictionary<string, string> PerObject = new Dictionary<string, string>(StringComparer.Ordinal);
    }

    internal static class PrefabResolver
    {
#if UNITY_EDITOR_WIN
        // bridge 1.6.0: no libc on Windows; the commands that use these are not served there (Protocol.WindowsCommands)
        static Refusal NotServed() { return new Refusal("UNKNOWN_COMMAND", "not served by the bridge on Windows (bridge 1.6.0)"); }
        static int access(string path, int mode) { throw NotServed(); }
#else
        [DllImport("libc", SetLastError = true)] static extern int access(string path, int mode);
#endif

        // TEST SEAM: the test-only testkit sets it to stand in for an active version-control provider. It can only make
        // the version-control guard refuse; a request cannot reach it.
        internal static bool TestVersionControlActive;

        // ------------------------------------------------------------ resolution

        public static UnityEngine.Object Object(string id, out PrefabInfo prefab)
        {
            string guid = PrefabIds.Guid(id);
            GlobalObjectId gid;
            if (!GlobalObjectId.TryParse(id, out gid)) throw new Refusal("OBJECT_REFUSED", "the prefab object id is not a GlobalObjectId");
            var o = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid);
            if (o == null) throw new Refusal("OBJECT_NOT_FOUND", "no prefab object with this id exists in this project");
            if (!EditorUtility.IsPersistent(o)) throw new Refusal("OBJECT_REFUSED", "the id does not name a persistent prefab object");
            if (SceneObjects.Id(o) != id) throw new Refusal("OBJECT_REFUSED", "the id is not the object's canonical id");
            var go = SceneObjects.GameObjectOf(o);
            if (go == null) throw new Refusal("OBJECT_REFUSED", "the id names neither a GameObject nor a Component of a prefab");
            string path = AssetDatabase.GetAssetPath(o);
            if (string.IsNullOrEmpty(path) || AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets) != guid)
                throw new Refusal("OBJECT_REFUSED", "the object is not stored in the prefab file its id names");
            var root = AssetDatabase.LoadMainAssetAtPath(path) as GameObject;
            if (root == null || go.transform.root != root.transform) throw new Refusal("OBJECT_REFUSED", "the object is not part of a prefab's hierarchy");
            prefab = Of(root, path);
            if (!prefab.ById.ContainsKey(id)) throw new Refusal("OBJECT_REFUSED", "the object is not part of the prefab's hierarchy");
            return o;
        }

        // The prefab whose root the id names.
        public static PrefabInfo Prefab(string id)
        {
            PrefabInfo p;
            var o = Object(id, out p);
            if (o != p.Root) throw new Refusal("OBJECT_REFUSED", "a prefab is named by the id of its root GameObject");
            return p;
        }

        public static PrefabInfo Of(GameObject root, string path)
        {
            var p = new PrefabInfo { Root = root, Path = path, Guid = AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets) };
            if (path.StartsWith("Assets/", StringComparison.Ordinal)) p.Source = AssetKinds.Assets;
            else if (path.StartsWith("Packages/", StringComparison.Ordinal) && AssetResolver.PackageRoot(path) != null) p.Source = AssetKinds.Package;
            else throw new Refusal("PREFAB_REFUSED", "the prefab is neither below Assets/ nor inside a registered package");
            if (AssetResolver.EditorOnlyPath(path)) throw new Refusal("PREFAB_REFUSED", "Editor-only prefabs (an Editor or Editor Default Resources folder) are never used");
            switch (PrefabUtility.GetPrefabAssetType(root))
            {
                case PrefabAssetType.Regular: p.Type = PrefabScope.Regular; break;
                case PrefabAssetType.Variant: p.Type = PrefabScope.Variant; break;
                case PrefabAssetType.Model: p.Type = PrefabScope.Model; break;
                default: throw new Refusal("PREFAB_REFUSED", "the asset is not a prefab");
            }
            if (p.Type != PrefabScope.Model && !path.EndsWith(PrefabPaths.Extension, StringComparison.Ordinal))
                throw new Refusal("PREFAB_REFUSED", "a prefab is a " + PrefabPaths.Extension + " file");
            var stack = new Stack<KeyValuePair<Transform, int>>();
            stack.Push(new KeyValuePair<Transform, int>(root.transform, 0));
            while (stack.Count > 0)
            {
                var entry = stack.Pop();
                var go = entry.Key.gameObject;
                p.GameObjects.Add(go);
                p.Depth[go] = entry.Value;
                var components = go.GetComponents<Component>();
                p.ComponentCount += components.Length;
                PrefabBounds.Check(p.GameObjects.Count, p.ComponentCount, entry.Value, "the prefab");
                p.Objects.Add(go);
                foreach (var c in components)
                {
                    if (c == null || (c is MonoBehaviour && MonoScript.FromMonoBehaviour((MonoBehaviour)c) == null)) { p.MissingScript = true; if (c == null) continue; }
                    p.Objects.Add(c);
                }
                for (int i = entry.Key.childCount - 1; i >= 0; i--) stack.Push(new KeyValuePair<Transform, int>(entry.Key.GetChild(i), entry.Value + 1));
            }
            var ids = SceneObjects.Ids(p.Objects.ToArray());
            for (int i = 0; i < p.Objects.Count; i++)
            {
                var o = p.Objects[i];
                p.Ids[o] = ids[i];
                p.ById[ids[i]] = o;
                var source = PrefabUtility.GetCorrespondingObjectFromSource(o);
                string role;
                if (source == null) role = PrefabScope.Owned;
                else if (p.Type == PrefabScope.Variant) role = PrefabScope.VariantInherited;
                else
                {
                    p.Nested = true;
                    role = o is GameObject && PrefabUtility.IsAnyPrefabInstanceRoot((GameObject)o) ? PrefabScope.NestedRoot : PrefabScope.NestedContent;
                }
                p.Ownership[o] = role;
                if (SceneObjects.Hidden(o)) p.Hidden = true;
            }
            p.Id = p.Ids[root];
            // every object stored in the file belongs to the hierarchy; an embedded asset (AddObjectToAsset) does not
            if (p.Type != PrefabScope.Model)
                foreach (var o in AssetDatabase.LoadAllAssetsAtPath(path))
                    if (o != null && !p.Ids.ContainsKey(o) && !(o is GameObject) && !(o is Component)) p.Embedded = true;
            return p;
        }

        // Refuses a prefab outside the mutable scope (and, for instantiation, the same scope).
        public static void RequireScope(PrefabInfo p)
        {
            var refusal = PrefabScope.Refusal(p.StaticReasons());
            if (refusal != null) throw refusal;
        }

        public static string Role(PrefabInfo p, UnityEngine.Object o)
        {
            string role;
            return p.Ownership.TryGetValue(o, out role) ? role : null;
        }

        // ------------------------------------------------------------ state and guards

        // Any Prefab Mode stage: the current stage is not the main stage, or a prefab stage is current. Returning to the
        // main stage closes every prefab stage of the breadcrumb (real test R6), so the main stage means none is open.
        public static bool AnyStageOpen()
        {
            return StageUtility.GetCurrentStage() != StageUtility.GetMainStage() || PrefabStageUtility.GetCurrentPrefabStage() != null;
        }

        public static void RequireNoStage()
        {
            if (AnyStageOpen())
            {
                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                throw new Refusal("PREFAB_STAGE_OPEN", "a Prefab Mode stage is open" + (stage != null ? " (" + stage.assetPath + ")" : "") +
                                                       "; GPOS never saves, closes or drives Prefab Mode and changes no prefab while one is open — close Prefab Mode, then decide again (nothing was changed)");
            }
        }

        // Whether anything stored in the prefab's file has unsaved Editor changes.
        public static bool Dirty(PrefabInfo p)
        {
            foreach (var o in AssetDatabase.LoadAllAssetsAtPath(p.Path))
                if (o != null && EditorUtility.IsDirty(o)) return true;
            foreach (var o in p.Objects)
                if (EditorUtility.IsDirty(o)) return true;
            return false;
        }

        public static string VersionControl(string path)
        {
            if (TestVersionControlActive || UnityEditor.VersionControl.Provider.isActive)
                return "a version-control provider is active; GPOS never checks out or adds files, so it changes no prefab here";
            if (path != null && AssetFiles.Present(path) && !AssetDatabase.IsOpenForEdit(path, StatusQueryOptions.UseCachedIfPossible))
                return "version control does not have the prefab open for edit; GPOS never checks files out";
            return null;
        }

        // Why the OS would not let GPOS write the prefab's file, .meta or folder, or null.
        public static string NotWritable(string path)
        {
            string full = AssetFiles.Full(path);
            if (access(full, 2) != 0) return "the prefab file is not writable";
            if (access(full + ".meta", 2) != 0) return "the prefab's .meta file is not writable";
            if (access(System.IO.Path.GetDirectoryName(full), 2) != 0) return "the prefab's folder is not writable";
            return null;
        }

        public static List<string> StateReasons(PrefabInfo p)
        {
            var r = new List<string>();
            if (AnyStageOpen()) r.Add(PrefabScope.StageOpen);
            if (Dirty(p)) r.Add(PrefabScope.Dirty);
            if (p.Source == AssetKinds.Assets && NotWritable(p.Path) != null) r.Add(PrefabScope.NotWritable);
            if (p.Source == AssetKinds.Assets && VersionControl(p.Path) != null) r.Add(PrefabScope.VersionControl);
            return r;
        }

        // The SHA-256 of the prefab's file or .meta; PREFAB_LIMIT beyond the file bounds, PREFAB_REFUSED for a link.
        public static string Sha(string path, bool meta)
        {
            try { return meta ? AssetFiles.MetaSha(path) : AssetFiles.FileSha(path); }
            catch (Refusal r)
            {
                if (r.Code == "ASSET_LIMIT") throw new Refusal("PREFAB_LIMIT", r.Message);
                throw new Refusal("PREFAB_REFUSED", r.Message);
            }
        }

        public static PrefabState State(PrefabInfo p)
        {
            var s = new PrefabState { File = Sha(p.Path, false), Meta = Sha(p.Path, true), Dirty = Dirty(p) };
            var b = new TokenBuilder(TokenBuilder.PrefabDomain).Field(p.Guid).Field(p.Id).Field(p.Path).Field(p.Type).Field(p.Source)
                .Field(s.File).Field(s.Meta).Bool(s.Dirty).Int(p.GameObjects.Count).Int(p.ComponentCount);
            try
            {
                foreach (var go in p.GameObjects)
                {
                    Add(p, s, b, go);
                    var components = go.GetComponents<Component>();
                    for (int i = 0; i < components.Length; i++)
                    {
                        if (components[i] == null) b.Field("MISSING#" + i);
                        else Add(p, s, b, components[i]);
                    }
                }
            }
            catch (Refusal r)
            {
                if (r.Code == "AUTHORING_LIMIT") throw new Refusal("PREFAB_LIMIT", r.Message);
                throw;
            }
            s.Token = b.Finish();
            return s;
        }

        static void Add(PrefabInfo p, PrefabState s, TokenBuilder b, UnityEngine.Object o)
        {
            string id = p.Ids[o];
            var h = new TokenBuilder("gpos.prefab-object/1");
            var go = o as GameObject;
            if (go != null)
            {
                h.Field(SceneObjects.ObjectToken(go)).Field(SceneObjects.TransformToken(go.transform));
                h.Int(SceneObjects.Serialized(h, go, "PREFAB_LIMIT", "a prefab GameObject"));
            }
            else h.Field(SceneObjects.ComponentToken((Component)o));
            string hash = h.Finish();
            s.PerObject[id] = hash;
            b.Field(id).Field(p.Ownership[o]).Bool(EditorUtility.IsDirty(o)).UInt((uint)o.hideFlags).Field(hash);
        }

        // ------------------------------------------------------------ isolated prefab contents

        // Maps each persistent object of the prefab to its copy in the isolated contents loaded by LoadPrefabContents:
        // the copy carries identifier type 2 with the prefab's GUID and the persistent object's own file id. Anything
        // but an exact one-to-one mapping of every object (same id, type and name) is PREFAB_CONFLICT.
        public static Dictionary<string, UnityEngine.Object> Map(PrefabInfo p, GameObject contentsRoot)
        {
            var objects = new List<UnityEngine.Object>();
            foreach (var t in contentsRoot.GetComponentsInChildren<Transform>(true))
            {
                objects.Add(t.gameObject);
                foreach (var c in t.GetComponents<Component>())
                    if (c != null) objects.Add(c);
            }
            if (objects.Count != p.Objects.Count)
                throw new Refusal("PREFAB_CONFLICT", "the prefab's isolated contents do not have the prefab's objects (the prefab changed); nothing was saved");
            var ids = new GlobalObjectId[objects.Count];
            GlobalObjectId.GetGlobalObjectIdsSlow(objects.ToArray(), ids);
            var map = new Dictionary<string, UnityEngine.Object>(StringComparer.Ordinal);
            for (int i = 0; i < objects.Count; i++)
            {
                string pid = PrefabIds.FromContents(ids[i].identifierType, ids[i].assetGUID.ToString(), ids[i].targetObjectId, ids[i].targetPrefabId, p.Guid);
                UnityEngine.Object persistent;
                if (pid == null || map.ContainsKey(pid) || !p.ById.TryGetValue(pid, out persistent) || persistent.GetType() != objects[i].GetType() ||
                    (persistent is GameObject && ((GameObject)persistent).name != ((GameObject)objects[i]).name))
                    throw new Refusal("PREFAB_CONFLICT", "an object of the prefab's isolated contents is not exactly one of the prefab's objects; nothing was saved");
                map[pid] = objects[i];
            }
            return map;
        }

        // ------------------------------------------------------------ loaded Scenes

        internal sealed class SceneFact
        {
            public Scene Scene;
            public string Name;
            public bool Dirty;
        }

        static string Named(Scene s) { return s.path.Length > 0 ? s.path : "(untitled) " + s.name; }

        public static List<SceneFact> Scenes()
        {
            if (SceneManager.sceneCount > PrefabBounds.MaxLoadedScenes)
                throw new Refusal("PREFAB_LIMIT", "more than " + PrefabBounds.MaxLoadedScenes + " Scenes are open; nothing was scanned partially or changed");
            var all = new List<SceneFact>();
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var s = SceneManager.GetSceneAt(i);
                if (s.isLoaded) all.Add(new SceneFact { Scene = s, Name = Named(s), Dirty = s.isDirty });
            }
            return all;
        }

        // The loaded Scenes (clean before, dirty now) that became dirty since `before`.
        public static List<object> MarkedDirty(List<SceneFact> before)
        {
            var now = new List<object>();
            foreach (var f in before)
                if (!f.Dirty && f.Scene.IsValid() && f.Scene.isLoaded && f.Scene.isDirty) now.Add(f.Name);
            return now;
        }

        // Every loaded Scene holding a prefab instance whose source depends on the prefab at `path` (the prefab itself,
        // or a Variant or outer prefab that includes it). The scan is bounded: beyond PrefabBounds.MaxDependentScan
        // GameObjects it is PREFAB_LIMIT — never a partial answer.
        public static List<SceneFact> DependentScenes(string path)
        {
            var dependsCache = new Dictionary<string, bool>(StringComparer.Ordinal);
            var dependents = new List<SceneFact>();
            int scanned = 0;
            foreach (var f in Scenes())
            {
                bool found = false;
                foreach (var root in f.Scene.GetRootGameObjects())
                {
                    foreach (var t in root.GetComponentsInChildren<Transform>(true))
                    {
                        if (++scanned > PrefabBounds.MaxDependentScan)
                            throw new Refusal("PREFAB_LIMIT", "the loaded Scenes hold more than " + PrefabBounds.MaxDependentScan + " GameObjects; the Scenes that depend on the prefab are not scanned partially, nothing was changed");
                        if (found || !PrefabUtility.IsAnyPrefabInstanceRoot(t.gameObject)) continue;
                        string source = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(t.gameObject);
                        if (string.IsNullOrEmpty(source)) continue;
                        bool depends;
                        if (!dependsCache.TryGetValue(source, out depends))
                            dependsCache[source] = depends = source == path || AssetDatabase.GetDependencies(source, true).Contains(path);
                        if (depends) found = true;
                    }
                }
                if (found) dependents.Add(f);
            }
            return dependents;
        }
    }
}
