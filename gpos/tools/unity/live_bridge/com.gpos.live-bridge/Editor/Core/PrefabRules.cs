// GPOS live bridge — prefab authoring rules (Unity-free core, bridge 1.3.0).
// A persistent prefab object is named only by its GlobalObjectId string of identifier type 1 with prefab id 0 (the
// prefab file's GUID and the object's own file id). The objects Unity loads into an isolated prefab-contents Scene
// report identifier type 2 ids with that same GUID and file id; they are an internal mapping only and never accepted
// from or returned to a caller. New prefabs are written only below Assets/ as exactly one .prefab file. This core
// decides the id grammar, the bounds, the ownership and scope vocabulary and whether a reference of a Scene subtree
// may be saved into a new prefab; it touches no Unity API.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;

namespace Gpos.LiveBridge
{
    internal static class PrefabIds
    {
        static readonly Regex Persistent = new Regex("^GlobalObjectId_V1-1-([0-9a-f]{32})-([0-9]{1,20})-0\\z");
        static readonly Regex Any = new Regex("^GlobalObjectId_V1-[0-9]+-[0-9a-f]{32}-[0-9]{1,20}-[0-9]{1,20}\\z");

        // The GUID of the prefab file a persistent prefab object id names; OBJECT_REFUSED for anything else (a Scene
        // object, a prefab-contents object, a source asset, a built-in resource, an id with a prefab id).
        public static string Guid(string id)
        {
            if (id == null) throw new Refusal("OBJECT_REFUSED", "a prefab object is named by its GlobalObjectId string");
            var m = Persistent.Match(id);
            if (!m.Success)
            {
                if (Any.IsMatch(id))
                    throw new Refusal("OBJECT_REFUSED", "a prefab object is a persistent prefab object: GlobalObjectId type 1 with prefab id 0");
                throw new Refusal("OBJECT_REFUSED", "a prefab object id is a GlobalObjectId_V1-1-<prefab guid>-<file id>-0 string");
            }
            ulong ignored;
            if (!ulong.TryParse(m.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out ignored))
                throw new Refusal("OBJECT_REFUSED", "the prefab object id's file id is out of range");
            string guid = m.Groups[1].Value;
            if (guid == ObjectIds.ZeroGuid || guid == AssetIds.DefaultResourcesGuid || guid == AssetIds.BuiltinExtraGuid)
                throw new Refusal("OBJECT_REFUSED", "the prefab object id has no prefab GUID");
            return guid;
        }

        public static bool IsPersistent(string id) { return id != null && Persistent.IsMatch(id); }

        public static string FileId(string id) { Guid(id); return id.Split('-')[3]; }

        // The persistent id of the object whose isolated prefab-contents copy has this identity (type 2, the prefab's
        // GUID, prefab id 0), or null when the contents object does not carry exactly that shape.
        public static string FromContents(int identifierType, string guid, ulong fileId, ulong prefabId, string prefabGuid)
        {
            if (identifierType != 2 || guid != prefabGuid || prefabId != 0 || fileId == 0) return null;
            return "GlobalObjectId_V1-1-" + guid + "-" + fileId.ToString(CultureInfo.InvariantCulture) + "-0";
        }

        // A creation record's identity: the root of a prefab file with the recorded GUID.
        public static bool IsRecordRoot(string globalId, string guid)
        {
            if (globalId == null || guid == null || !IsPersistent(globalId)) return false;
            return Guid(globalId) == guid && FileId(globalId) != "0";
        }
    }

    internal static class PrefabPaths
    {
        public const string Extension = ".prefab";

        // Validates a path a new prefab is written to (Assets/<existing folders>/<stem>.prefab, the asset path rules)
        // and returns the parent folder.
        public static string CheckWritePath(string path) { return AssetPaths.CheckPrefabPath(path); }
    }

    internal static class PrefabBounds
    {
        public const int MaxGameObjects = 256;
        public const int MaxComponents = 1024;
        public const int MaxDepth = 32;
        public const int InspectPage = 100;
        public const int OverridesPage = 200;
        public const int MaxOverrides = 2000;
        public const int MaxListed = 20;
        public const int MaxInstanceObjectsReported = 200;
        public const int MaxValueText = 256;
        public const int MaxLoadedScenes = 32;
        public const int MaxDependentScan = 100000;

        public static void Check(int gameObjects, int components, int depth, string what)
        {
            if (gameObjects > MaxGameObjects) throw new Refusal("PREFAB_LIMIT", what + " has more than " + MaxGameObjects + " GameObjects; nothing is hashed, listed partially or written");
            if (components > MaxComponents) throw new Refusal("PREFAB_LIMIT", what + " has more than " + MaxComponents + " components; nothing is hashed, listed partially or written");
            if (depth > MaxDepth) throw new Refusal("PREFAB_LIMIT", what + " is nested deeper than " + MaxDepth + "; nothing is hashed, listed partially or written");
        }
    }

    // Who owns a GameObject or Component of a prefab file, and why a prefab is outside the mutable alpha.19 scope.
    internal static class PrefabScope
    {
        public const string Owned = "OWNED", NestedRoot = "NESTED_ROOT", NestedContent = "NESTED_CONTENT", VariantInherited = "VARIANT_INHERITED";
        public const string Regular = "REGULAR", Variant = "VARIANT", Model = "MODEL";

        // Scope reasons: what the prefab is (static) and what state it is in now.
        public const string IsVariant = "VARIANT", IsModel = "MODEL", InPackage = "PACKAGE", NestedPresent = "NESTED_PRESENT",
                            MissingScript = "MISSING_SCRIPT", HiddenContent = "HIDDEN_CONTENT", StageOpen = "STAGE_OPEN", Dirty = "DIRTY",
                            NotWritable = "NOT_WRITABLE", VersionControl = "VERSION_CONTROL", NotOwned = "NOT_OWNED",
                            Embedded = "EMBEDDED_ASSETS";

        public static readonly string[] Static = { IsVariant, IsModel, InPackage, NestedPresent, MissingScript, HiddenContent, Embedded };
        public static readonly string[] State = { StageOpen, Dirty, NotWritable, VersionControl };

        // The ordered, de-duplicated scope reasons; empty means the prefab may be mutated.
        public static List<string> Reasons(string type, string source, bool nested, bool missingScript, bool hidden)
        {
            var r = new List<string>();
            if (type == Variant) r.Add(IsVariant);
            else if (type != Regular) r.Add(IsModel);
            if (source != AssetKinds.Assets) r.Add(InPackage);
            if (nested) r.Add(NestedPresent);
            if (missingScript) r.Add(MissingScript);
            if (hidden) r.Add(HiddenContent);
            return r;
        }

        // The refusal a mutation of a prefab with these scope reasons gets (the first reason decides the message).
        public static Refusal Refusal(IList<string> reasons)
        {
            if (reasons.Count == 0) return null;
            string first = reasons[0];
            string why;
            switch (first)
            {
                case IsVariant: why = "the prefab is a Variant; alpha.19 authors regular prefabs only"; break;
                case IsModel: why = "the prefab is a model (or not a regular prefab); models are references only"; break;
                case InPackage: why = "the prefab is inside a package; package prefabs are references only"; break;
                case NestedPresent: why = "the prefab contains a nested prefab instance; alpha.19 authors prefabs without nested prefabs only"; break;
                case MissingScript: why = "the prefab has a component whose script is missing"; break;
                case HiddenContent: why = "the prefab has hidden, DontSave or NotEditable content"; break;
                case Embedded: why = "the prefab's file holds an embedded asset besides its hierarchy"; break;
                default: why = "the prefab is outside the authorable scope (" + first + ")"; break;
            }
            return new Refusal("PREFAB_REFUSED", why + " (" + string.Join(", ", reasons) + ")");
        }
    }

    // Whether one serialized object reference of a Scene subtree may be saved into a new prefab. The subtree must
    // already be plain Scene content (no prefab instance, override, hidden or missing-script object). Allowed: null,
    // an object inside the exact subtree, a reviewed persistent asset (the alpha.18 reference kinds), a component's
    // own script and the subtree root Transform's parent (Unity cuts it when the root becomes a prefab root).
    internal static class PrefabReferences
    {
        // What the Editor found at the reference.
        public const string Null = "NULL", Inside = "INSIDE", OwnScript = "OWN_SCRIPT", ReviewedAsset = "REVIEWED_ASSET",
                            SceneOutside = "SCENE_OUTSIDE", OtherScript = "OTHER_SCRIPT", UnsupportedAsset = "UNSUPPORTED_ASSET",
                            Unknown = "UNKNOWN_REFERENCE";
        // Why it is refused.
        public const string PrefabLink = "PREFAB_LINK", Script = "SCRIPT", MissingScript = "MISSING_SCRIPT";

        static readonly HashSet<string> Links = new HashSet<string>(StringComparer.Ordinal) {
            "m_PrefabInstance", "m_PrefabAsset", "m_CorrespondingSourceObject", "m_PrefabInternal", "m_PrefabParentObject" };

        // Unity's own prefab linkage fields (never source content; a saved prefab's objects link to their prefab).
        public static bool IsLink(string propertyPath)
        {
            return propertyPath != null && Links.Contains(propertyPath.Split('.')[0]);
        }

        public static string Refusal(string propertyPath, bool rootTransform, string target)
        {
            if (propertyPath == null || target == null) return Unknown;
            string top = propertyPath.Split('.')[0];
            if (Links.Contains(top)) return target == Null ? null : PrefabLink;
            if (top == "m_Script") return target == OwnScript ? null : target == Null ? MissingScript : Script;
            if (top == "m_Father" && rootTransform) return null;
            switch (target)
            {
                case Null:
                case Inside:
                case ReviewedAsset: return null;
                case OwnScript: return Script;
                case SceneOutside:
                case OtherScript:
                case UnsupportedAsset: return target;
            }
            return Unknown;
        }
    }
}
