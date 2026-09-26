// GPOS live bridge — the identifier grammars of Scene authoring (Unity-free core).
// An object is named only by its GlobalObjectId string, and only a Scene object (identifier type 2) of a saved
// Scene is ever accepted. There is no InstanceID, EntityId, hierarchy path or temporary id. Scenes are named by
// their project path under Assets/, catalogued component types by "<assembly>::<full name>", and optimistic-
// concurrency tokens by 32 hex digits (the catalog digest by 64).
using System;
using System.Text.RegularExpressions;

namespace Gpos.LiveBridge
{
    internal static class ObjectIds
    {
        public const string ZeroGuid = "00000000000000000000000000000000";
        public const int MaxNameLength = 128;
        public const int MaxScenePathLength = 512;

        static readonly Regex SceneObject = new Regex("^GlobalObjectId_V1-2-([0-9a-f]{32})-([0-9]{1,20})-([0-9]{1,20})\\z");
        static readonly Regex AnyObject = new Regex("^GlobalObjectId_V1-[0-9]+-[0-9a-f]{32}-[0-9]{1,20}-[0-9]{1,20}\\z");
        static readonly Regex ScenePath = new Regex("^Assets/[^\\u0000-\\u001f\\u007f\\\\:*?\"<>|]+\\.unity\\z");
        public static readonly Regex Token = new Regex("^[0-9a-f]{32}\\z");
        public static readonly Regex Digest = new Regex("^[0-9a-f]{64}\\z");
        public static readonly Regex TypeId = new Regex("^[A-Za-z0-9_.\\-]{1,128}::[A-Za-z_][A-Za-z0-9_.+]{0,255}\\z");

        // The GUID of the Scene asset that holds the object. Refuses anything that is not a Scene object's id
        // (OBJECT_REFUSED) and the all-zero Scene GUID an unsaved Scene gives its objects (SCENE_NOT_SAVED).
        public static string SceneGuid(string id)
        {
            if (id == null) throw new Refusal("OBJECT_REFUSED", "an object id is a GlobalObjectId string");
            var m = SceneObject.Match(id);
            var any = AnyObject.Match(id);
            if (any.Success && id.Contains("-" + ZeroGuid + "-"))
                throw new Refusal("SCENE_NOT_SAVED", "the object has no stable id: its Scene was never saved (GPOS never chooses a path for it)");
            if (!m.Success)
            {
                if (AnyObject.IsMatch(id)) throw new Refusal("OBJECT_REFUSED", "only Scene objects (GlobalObjectId type 2) are authored; assets and other objects are refused");
                throw new Refusal("OBJECT_REFUSED", "an object id is a GlobalObjectId_V1-2-<scene guid>-<file id>-<prefab id> string");
            }
            ulong ignored;
            if (!ulong.TryParse(m.Groups[2].Value, out ignored) || !ulong.TryParse(m.Groups[3].Value, out ignored))
                throw new Refusal("OBJECT_REFUSED", "the object id's numbers are out of range");
            return m.Groups[1].Value;
        }

        public static void CheckScenePath(string path)
        {
            if (path == null) throw new Refusal("BAD_ARGUMENTS", "scene must be a Scene path");
            if (path.Length == 0) throw new Refusal("SCENE_NOT_SAVED", "an unsaved Scene has no path; GPOS never chooses one");
            if (path.Length > MaxScenePathLength || !ScenePath.IsMatch(path) || path.Contains("/../") || path.Contains("/./") || path.Contains("//"))
                throw new Refusal("OBJECT_REFUSED", "a Scene is named by its path under Assets/, ending in .unity");
        }

        public static void CheckName(string name)
        {
            if (name == null || name.Length == 0 || name.Length > MaxNameLength)
                throw new Refusal("VALUE_INVALID", "a name has 1 to " + MaxNameLength + " characters");
            foreach (char c in name)
                if (c < 0x20 || c == 0x7f) throw new Refusal("VALUE_INVALID", "a name has no control characters");
        }

        public static void CheckToken(string value, string what)
        {
            if (value == null || !Token.IsMatch(value)) throw new Refusal("BAD_ARGUMENTS", what + " must be a 32-hex token");
        }
    }
}
