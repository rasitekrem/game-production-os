// GPOS live bridge — asset identity and the reference boundary (bridge 1.2.0). Everything here reads.
//
// An asset id (AssetIds) resolves only to a persistent object whose canonical GlobalObjectId is exactly the string
// given and that is one of the reviewed asset kinds (AssetKinds):
//   ASSETS   an asset below Assets/ — a main asset, or a typed sub-asset (Mesh, Sprite, Material, Texture)
//   PACKAGE  the same, below a registered package's canonical Packages/<name>/ path (never Library/PackageCache)
//   BUILTIN  one entry of the fixed, reviewed built-in resource table (identifier type 4 of the two built-in GUIDs)
// A GameObject is accepted only as the root of a prefab or model main asset, a Component only on such a root.
// Scene assets, MonoScripts, folders, other asset types, internal prefab or model objects, Editor-only paths
// (an "Editor" or "Editor Default Resources" folder) and Editor-assembly types are refused. Package and built-in
// assets are references only; nothing here ever writes. There is no lookup by InstanceID, EntityId or path.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Gpos.LiveBridge
{
    internal sealed class AssetRef
    {
        public UnityEngine.Object Object;
        public string Id, Kind, Source, Path, Type;
        public bool Main, Sub;

        public Dictionary<string, object> ToData()
        {
            return new Dictionary<string, object> {
                { "id", Id }, { "kind", Kind }, { "type", Type }, { "name", SceneObjects.Clip(Object.name, ObjectIds.MaxNameLength) },
                { "path", Path }, { "source", Source }, { "main", Main }, { "sub_asset", Sub }, { "authorable", AssetResolver.Authorable(this) } };
        }
    }

    internal static class AssetResolver
    {
        public const int MaxResolutions = 256;

        // The reviewed built-in resources: kind, loader ("default" = default resources, "extra" = built-in extra
        // resources) and resource name. A built-in id is accepted only when it is one of these, loaded and type-checked.
        static readonly string[][] BuiltinTable = {
            new[] { AssetKinds.Mesh, "default", "Cube.fbx" }, new[] { AssetKinds.Mesh, "default", "Sphere.fbx" },
            new[] { AssetKinds.Mesh, "default", "Capsule.fbx" }, new[] { AssetKinds.Mesh, "default", "Cylinder.fbx" },
            new[] { AssetKinds.Mesh, "default", "Plane.fbx" }, new[] { AssetKinds.Mesh, "default", "Quad.fbx" },
            new[] { AssetKinds.Material, "extra", "Default-Material.mat" }, new[] { AssetKinds.Material, "extra", "Sprites-Default.mat" },
            new[] { AssetKinds.Sprite, "extra", "UI/Skin/UISprite.psd" }, new[] { AssetKinds.Sprite, "extra", "UI/Skin/Background.psd" },
            new[] { AssetKinds.Sprite, "extra", "UI/Skin/Knob.psd" }, new[] { AssetKinds.Sprite, "extra", "UI/Skin/Checkmark.psd" },
        };

        static readonly HashSet<string> TextureTypes = new HashSet<string>(StringComparer.Ordinal) {
            "Texture2D", "Cubemap", "Texture3D", "Texture2DArray", "CubemapArray" };

        sealed class Builtin { public string Kind, Name, Id; public UnityEngine.Object Object; }

        static List<Builtin> builtins;

        static List<Builtin> Builtins()
        {
            if (builtins != null && builtins.All(b => b.Object != null)) return builtins;
            var list = new List<Builtin>();
            foreach (var e in BuiltinTable)
            {
                UnityEngine.Object o = null;
                try
                {
                    Type type = e[0] == AssetKinds.Mesh ? typeof(Mesh) : e[0] == AssetKinds.Material ? typeof(Material) : typeof(Sprite);
                    o = e[1] == "default" ? Resources.GetBuiltinResource(type, e[2]) : AssetDatabase.GetBuiltinExtraResource(type, e[2]);
                }
                catch (Exception) { }
                if (o == null) continue;
                string id = SceneObjects.Id(o);
                if (!AssetIds.IsAsset(id) || AssetIds.Check(id) != 4) continue;
                list.Add(new Builtin { Kind = e[0], Name = e[2], Id = id, Object = o });
            }
            builtins = list;
            return list;
        }

        public static IEnumerable<AssetRef> BuiltinRefs(string kind)
        {
            foreach (var b in Builtins())
                if (b.Kind == kind) yield return Classify(b.Object, b.Id);
        }

        // ------------------------------------------------------------ resolution

        public static AssetRef Resolve(string id, ref int count)
        {
            AssetIds.Check(id);
            if (++count > MaxResolutions) throw new Refusal("AUTHORING_LIMIT", "a request resolves at most " + MaxResolutions + " objects");
            GlobalObjectId gid;
            if (!GlobalObjectId.TryParse(id, out gid)) throw new Refusal("OBJECT_REFUSED", "the asset id is not a GlobalObjectId");
            var o = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid);
            if (o == null) throw new Refusal("OBJECT_NOT_FOUND", "no asset with this id exists in this project");
            if (!EditorUtility.IsPersistent(o)) throw new Refusal("OBJECT_REFUSED", "the id does not name a persistent asset");
            if (SceneObjects.Id(o) != id) throw new Refusal("OBJECT_REFUSED", "the id is not the asset's canonical id");
            return Classify(o, id);
        }

        public static AssetRef Resolve(string id) { int n = 0; return Resolve(id, ref n); }

        static Refusal Refused(string why) { return new Refusal("ASSET_REFUSED", why); }

        // The reviewed reference facts of a persistent object, or ASSET_REFUSED.
        public static AssetRef Classify(UnityEngine.Object o, string id)
        {
            int type = AssetIds.Check(id);
            string path = AssetDatabase.GetAssetPath(o) ?? "";
            string source;
            if (type == 4)
            {
                if (!Builtins().Any(b => b.Id == id)) throw Refused("the id names a built-in resource outside the reviewed built-in table");
                source = AssetKinds.Builtin;
            }
            else if (path.StartsWith("Assets/", StringComparison.Ordinal)) source = AssetKinds.Assets;
            else if (path.StartsWith("Packages/", StringComparison.Ordinal) && PackageRoot(path) != null) source = AssetKinds.Package;
            else throw Refused("the asset is neither below Assets/ nor inside a registered package");
            if (source != AssetKinds.Builtin && EditorOnlyPath(path)) throw Refused("Editor-only assets (an Editor or Editor Default Resources folder) are never referenced");
            if (o is SceneAsset) throw Refused("Scene assets are never referenced");
            if (o is MonoScript) throw Refused("scripts are never referenced");
            if (o is DefaultAsset) throw Refused("folders and unknown files are never referenced");
            var t = o.GetType();
            if (t.Assembly.GetName().Name.StartsWith("UnityEditor", StringComparison.Ordinal))
                throw Refused("the asset is an Editor-only type (" + t.Name + ")");
            bool main = source != AssetKinds.Builtin && AssetDatabase.IsMainAsset(o);
            bool sub = source != AssetKinds.Builtin && AssetDatabase.IsSubAsset(o);
            string kind = KindOf(o, main, sub, source);
            if (kind == null) throw Refused("a " + t.Name + " is not a reviewed asset kind here (" + string.Join(", ", AssetKinds.All) + ")");
            // a built-in resource has no project path (its engine resource file is not a place GPOS names)
            return new AssetRef { Object = o, Id = id, Kind = kind, Source = source, Path = source == AssetKinds.Builtin ? null : path, Main = main, Sub = sub,
                                  Type = Catalog.TypeKey(t) };
        }

        static string KindOf(UnityEngine.Object o, bool main, bool sub, string source)
        {
            bool builtin = source == AssetKinds.Builtin;
            bool placed = builtin || main || sub;
            if (o is Material) return placed ? AssetKinds.Material : null;
            if (o is Texture) return placed && TextureTypes.Contains(o.GetType().Name) ? AssetKinds.Texture : null;
            if (o is Sprite) return placed ? AssetKinds.Sprite : null;
            if (o is Mesh) return placed ? AssetKinds.Mesh : null;
            if (IsAudio(o.GetType())) return main ? AssetKinds.Audio : null;
            if (builtin) return null;
            var go = o as GameObject;
            if (go != null) return main && go.transform.parent == null ? RootKind(go) : null;
            var c = o as Component;
            if (c != null)
            {
                var root = c.gameObject;
                return AssetDatabase.IsMainAsset(root) && root.transform.parent == null && RootKind(root) != null ? AssetKinds.PrefabComponent : null;
            }
            if (o is ScriptableObject)
                return main && AssetCatalogs.ScriptableObjects().Find(Catalog.TypeKey(o.GetType())) != null ? AssetKinds.ScriptableObject : null;
            return null;
        }

        static string RootKind(GameObject go)
        {
            switch (PrefabUtility.GetPrefabAssetType(go))
            {
                case PrefabAssetType.Regular:
                case PrefabAssetType.Variant: return AssetKinds.Prefab;
                case PrefabAssetType.Model: return AssetKinds.Model;
            }
            return null;
        }

        public static bool IsAudio(Type t)
        {
            for (; t != null; t = t.BaseType)
                if (t == typeof(AudioClip) || (t.Name == "AudioResource" && t.Namespace == "UnityEngine.Audio")) return true;
            return false;
        }

        public static bool EditorOnlyPath(string path)
        {
            foreach (var s in path.Split('/'))
            {
                string l = s.ToLowerInvariant();
                if (l == "editor" || l == "editor default resources") return true;
            }
            return false;
        }

        // The registered package root ("Packages/<name>") that holds a canonical package path, or null.
        public static string PackageRoot(string path)
        {
            var parts = path.Split('/');
            if (parts.Length < 3) return null;
            string root = parts[0] + "/" + parts[1];
            return PackageRoots().Contains(root) ? root : null;
        }

        public static List<string> PackageRoots()
        {
            return UnityEditor.PackageManager.PackageInfo.GetAllRegisteredPackages()
                .Select(p => p.assetPath).Where(p => p != null && p.StartsWith("Packages/", StringComparison.Ordinal) && p.Split('/').Length == 2)
                .Distinct().OrderBy(p => p, StringComparer.Ordinal).ToList();
        }

        // Whether GPOS may write this asset through the asset commands: a main Material (.mat) or catalogued
        // ScriptableObject (.asset) below Assets/, alone in its file. Package and built-in assets are references only.
        // Only those two kinds are ever authorable here (bridge 1.3.0): prefab write authority belongs to the prefab
        // commands alone (PrefabResolver), never to this generic asset surface.
        public static bool Authorable(AssetRef r)
        {
            if (r.Kind != AssetKinds.Material && r.Kind != AssetKinds.ScriptableObject) return false;
            if (r.Source != AssetKinds.Assets || !r.Main) return false;
            string ext = AssetKinds.Extension(r.Kind);
            if (ext.Length == 0 || !r.Path.EndsWith(ext, StringComparison.Ordinal)) return false;
            try { AssetPaths.CheckWritePath(r.Path, ext); }
            catch (Refusal) { return false; }
            return AssetDatabase.LoadAllAssetsAtPath(r.Path).Length == 1;
        }

        public static AssetRef RequireAuthorable(AssetRef r, string kind)
        {
            if (r.Kind != kind) throw new Refusal("ASSET_REFUSED", "the asset is a " + r.Kind + ", not a " + kind);
            if (!Authorable(r))
                throw new Refusal("ASSET_REFUSED", r.Source != AssetKinds.Assets ? "package and built-in assets are references only; they are never written"
                                                                                 : "only a main " + kind + " alone in its own " + AssetKinds.Extension(kind) + " file below Assets/ (outside special folders) is written");
            return r;
        }
    }

    // Disk facts of asset files: bounded SHA-256 of a source file and its .meta, link checks, case-insensitive
    // collisions. Paths are asset paths below the Unity project, validated before they get here.
    internal static class AssetFiles
    {
        public static string Full(string assetPath) { return Path.Combine(LiveBridge.Place.ProjectPath, assetPath); }

        public static bool Present(string assetPath)
        {
            string full = Full(assetPath);
            return File.Exists(full) || Directory.Exists(full) || Identity.IsLink(full);
        }

        // SHA-256 of a regular file, null when it does not exist. ASSET_LIMIT beyond `max` bytes; ASSET_REFUSED for a link.
        public static string Sha(string assetPath, long max)
        {
            string full = Full(assetPath);
            if (Identity.IsLink(full)) throw new Refusal("ASSET_REFUSED", assetPath + " is a symbolic link");
            if (Directory.Exists(full)) throw new Refusal("ASSET_REFUSED", assetPath + " is a folder");
            if (!File.Exists(full)) return null;
            if (new FileInfo(full).Length > max) throw new Refusal("ASSET_LIMIT", assetPath + " is larger than " + max + " bytes; it is never hashed or written");
            return Identity.Sha256(File.ReadAllBytes(full));
        }

        public static string FileSha(string assetPath) { return Sha(assetPath, AssetBounds.MaxFileBytes); }

        public static string MetaSha(string assetPath) { return Sha(assetPath + ".meta", AssetBounds.MaxMetaBytes); }

        // Refuses when the project folder or any existing component of the asset path is a symbolic link.
        public static void LinkFree(string assetPath)
        {
            string current = LiveBridge.Place.ProjectPath;
            if (Identity.IsLink(current)) throw new Refusal("ASSET_PATH_INVALID", "the project folder is a link");
            foreach (var part in assetPath.Split('/'))
            {
                current = Path.Combine(current, part);
                if (Identity.IsLink(current)) throw new Refusal("ASSET_PATH_INVALID", "a component of the path is a symbolic link");
            }
        }

        public static List<string> Listing(string folderAssetPath)
        {
            string full = Full(folderAssetPath);
            if (!Directory.Exists(full)) return null;
            return Directory.GetFileSystemEntries(full).Select(Path.GetFileName).OrderBy(n => n, StringComparer.Ordinal).ToList();
        }

        // Whether anything named `name` exists in the folder, compared case-insensitively (as macOS and Windows do).
        public static bool CaseCollision(string folderAssetPath, string name)
        {
            var listing = Listing(folderAssetPath);
            return listing != null && listing.Any(n => string.Equals(n, name, StringComparison.OrdinalIgnoreCase));
        }

        // The GUID a .meta file declares ("guid: <32 hex>"), or null.
        public static string MetaGuid(string assetPath)
        {
            string full = Full(assetPath) + ".meta";
            if (!File.Exists(full) || Identity.IsLink(full) || new FileInfo(full).Length > AssetBounds.MaxMetaBytes) return null;
            foreach (var line in File.ReadAllLines(full))
                if (line.StartsWith("guid: ", StringComparison.Ordinal))
                {
                    string g = line.Substring(6).Trim();
                    return Protocol.Hex32.IsMatch(g) ? g : null;
                }
            return null;
        }
    }
}
