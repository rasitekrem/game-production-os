// GPOS live bridge — the closed request protocol (Unity-free core).
// A request is one immutable JSON file: exact keys, a request id bound to its file name, the canonical owner
// KIND:ID, the bridge boot it is addressed to, one command from a closed allowlist with a fixed argument set, and
// an issue time plus a start deadline. There is no generic command, no code and no reflection target; a path argument
// is an exact asset, prefab or (bridge 1.4.0) source path checked by AssetPaths or SourcePaths.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace Gpos.LiveBridge
{
    internal sealed class Request
    {
        public string Id, SessionId, Owner, BootId, Command;
        public Dictionary<string, object> Args;
        public long IssuedTicks, DeadlineTicks;
    }

    // A request that is not carried out as asked. Status is REFUSED unless an authoring command's mutation began and
    // could not complete (FAILED); Data then says what was reverted and whether the pre-state was restored.
    internal sealed class Refusal : Exception
    {
        public readonly string Code;
        public string Status = "REFUSED";
        public Dictionary<string, object> Data;
        public Refusal(string code, string message) : base(message) { Code = code; }
    }

    internal static class Protocol
    {
        public const string Name = "gpos.unity.live/5";
        public const string RequestSchema = "gpos.unity.live.request/5";
        public const string ResponseSchema = "gpos.unity.live.response/5";
        public const string BridgeVersion = "1.6.0";   // 1.6.0 adds Windows; the live protocol is unchanged
        public const string PackageId = "com.gpos.live-bridge";
        public const int MaxRequestBytes = 64 * 1024;
        public const int MaxResponseBytes = 256 * 1024;
        public const int MaxStartWindowSeconds = 120;
        public const int FutureSkewSeconds = 5;

        public static readonly Regex Hex32 = new Regex("^[0-9a-f]{32}\\z");
        public static readonly Regex Hex16 = new Regex("^[0-9a-f]{16}\\z");
        public static readonly Regex Owner = new Regex("^[A-Z][A-Z_]*:[A-Za-z0-9][A-Za-z0-9._@-]{0,63}\\z");
        static readonly Regex Utc = new Regex(@"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,7})?Z\z");

        static readonly string[] Keys = { "schema", "request_id", "session_id", "owner", "boot_id", "command", "args",
                                          "issued_utc", "start_deadline_utc" };

        sealed class Spec
        {
            public bool Session, Mutating, Authoring, Sources;
            public string[] Args;
        }

        static readonly Dictionary<string, Spec> Specs = new Dictionary<string, Spec>(StringComparer.Ordinal)
        {
            { "status", new Spec { Args = new string[0] } },
            { "propose-attach", new Spec { Args = new[] { "proposal_id", "session_id", "project_key", "expires_s" } } },
            { "attach-status", new Spec { Args = new[] { "proposal_id" } } },
            { "abandon-proposal", new Spec { Args = new[] { "proposal_id" } } },
            { "bind", new Spec { Args = new[] { "proposal_id", "session_id" } } },
            { "propose-recovery", new Spec { Args = new[] { "proposal_id", "project_key", "stale_session_id", "expires_s" } } },
            { "recovery-status", new Spec { Args = new[] { "proposal_id" } } },
            { "consume-recovery", new Spec { Args = new[] { "proposal_id" } } },
            { "unbind", new Spec { Session = true, Args = new string[0] } },
            { "inspect", new Spec { Session = true, Args = new string[0] } },
            { "enter-playmode", new Spec { Session = true, Mutating = true, Args = new[] { "timeout_s" } } },
            { "exit-playmode", new Spec { Session = true, Mutating = true, Args = new[] { "timeout_s" } } },
            { "pause", new Spec { Session = true, Mutating = true, Args = new string[0] } },
            { "resume", new Spec { Session = true, Mutating = true, Args = new string[0] } },
            // Scene authoring (bridge 1.1.0): every argument key is always present; an absent value is null.
            { "object-inspect", AuthoringSpec(false, "object", "scene", "children_limit") },
            { "component-types", AuthoringSpec(false, "query", "page") },
            { "properties", AuthoringSpec(false, "component", "path_prefix", "page") },
            { "create-gameobject", AuthoringSpec(true, "scene", "name", "parent", "sibling", "local_position", "local_rotation",
                                             "local_scale", "expected_parent_token", "expected_scene_roots_token") },
            { "delete-gameobject", AuthoringSpec(true, "object", "expected_subtree_token") },
            { "set-parent", AuthoringSpec(true, "object", "parent", "keep_world", "sibling", "expected_object_token",
                                      "expected_transform_token", "expected_old_parent_token", "expected_old_scene_roots_token",
                                      "expected_new_parent_token", "expected_new_scene_roots_token",
                                      "expected_transform_chain_token", "expected_new_parent_chain_token") },
            { "set-gameobject", AuthoringSpec(true, "object", "name", "active", "tag", "layer", "static_flags", "expected_object_token") },
            { "set-transform", AuthoringSpec(true, "object", "local_position", "local_rotation", "local_scale", "expected_transform_token") },
            { "add-component", AuthoringSpec(true, "object", "type_id", "expected_object_token", "expected_catalog_digest") },
            { "remove-component", AuthoringSpec(true, "component", "expected_component_token", "expected_object_token") },
            { "set-property", AuthoringSpec(true, "component", "path", "kind", "value", "expected_component_token") },
            { "save-scene", AuthoringSpec(true, "scene") },
            // Asset references and asset authoring (bridge 1.2.0).
            { "asset-types", AuthoringSpec(false, "catalog", "query", "page") },
            { "asset-find", AuthoringSpec(false, "kind", "source", "query", "page") },
            { "asset-inspect", AuthoringSpec(false, "asset", "path_prefix", "page") },
            { "create-material", AuthoringSpec(true, "path", "shader", "expected_shader_catalog_digest") },
            { "set-material-property", AuthoringSpec(true, "material", "property", "kind", "value", "expected_asset_token") },
            { "create-scriptable-object", AuthoringSpec(true, "path", "type_id", "expected_so_catalog_digest") },
            { "set-asset-property", AuthoringSpec(true, "asset", "path", "kind", "value", "expected_asset_token") },
            { "set-renderer-material", AuthoringSpec(true, "renderer", "material", "slot", "expected_component_token") },
            // Prefab authoring (bridge 1.3.0): inspection, one plain Scene subtree saved as a new regular prefab,
            // instantiation into a Scene, and bounded edits of one regular prefab's own objects.
            { "prefab-inspect", AuthoringSpec(false, "prefab", "component", "path_prefix", "page") },
            { "prefab-instance-inspect", AuthoringSpec(false, "object", "page") },
            { "create-prefab", AuthoringSpec(true, "source", "path", "expected_subtree_token") },
            { "instantiate-prefab", AuthoringSpec(true, "prefab", "scene", "parent", "sibling", "local_position", "local_rotation", "local_scale",
                                                 "expected_prefab_token", "expected_parent_token", "expected_scene_roots_token") },
            { "set-prefab-gameobject", AuthoringSpec(true, "object", "name", "active", "tag", "layer", "static_flags", "expected_prefab_token") },
            { "set-prefab-transform", AuthoringSpec(true, "object", "local_position", "local_rotation", "local_scale", "expected_prefab_token") },
            { "add-prefab-component", AuthoringSpec(true, "object", "type_id", "expected_prefab_token", "expected_catalog_digest") },
            { "remove-prefab-component", AuthoringSpec(true, "component", "expected_prefab_token") },
            { "set-prefab-property", AuthoringSpec(true, "component", "path", "kind", "value", "expected_prefab_token") },
            // Source synchronization and compilation facts (bridge 1.4.0): exact source paths in, targeted imports only;
            // the two readers answer in any Editor phase and change nothing.
            { "sync-sources", new Spec { Session = true, Mutating = true, Sources = true, Args = new[] { "sources", "deleted" } } },
            { "compilation-status", new Spec { Session = true, Args = new string[0] } },
            { "compilation-diagnostics", new Spec { Session = true, Args = new[] { "generation", "severity", "assembly", "page" } } },
        };

        static Spec AuthoringSpec(bool mutating, params string[] args)
        {
            return new Spec { Session = true, Mutating = mutating, Authoring = true, Args = args };
        }

        public static IEnumerable<string> Commands { get { return Specs.Keys; } }

        public static bool NeedsSession(string command) { return Specs[command].Session; }

        public static bool ChangesEditorState(string command) { return Specs[command].Mutating && !Specs[command].Authoring && !Specs[command].Sources; }

        // The source sync (bridge 1.4.0): only in Edit Mode with nothing pending, like authoring; it changes neither a
        // Scene nor an asset GPOS authors — it asks Unity to import exact source paths.
        public static bool ChangesSources(string command) { return Specs[command].Sources; }

        // A Scene- or asset-authoring command: only in Edit Mode, with nothing pending (Transitions.AuthoringBusy).
        public static bool IsAuthoring(string command) { return Specs[command].Authoring; }

        public static bool ChangesScene(string command) { return Specs[command].Authoring && Specs[command].Mutating && !IsAsset(command) && !ChangesPrefabAsset(command); }

        public static bool ChangesAssets(string command) { return Specs[command].Mutating && (IsAsset(command) || ChangesPrefabAsset(command)); }

        // A prefab command (bridge 1.3.0): prefab objects are type-1 GlobalObjectIds. instantiate-prefab changes a
        // Scene; create-prefab and the prefab edits change one prefab file.
        public static bool IsPrefab(string command) { return Array.IndexOf(PrefabCommands, command) >= 0; }

        public static bool ChangesPrefabAsset(string command) { return Array.IndexOf(PrefabAssetMutations, command) >= 0; }

        static readonly string[] PrefabCommands = { "prefab-inspect", "prefab-instance-inspect", "create-prefab", "instantiate-prefab", "set-prefab-gameobject",
                                                    "set-prefab-transform", "add-prefab-component", "remove-prefab-component", "set-prefab-property" };

        static readonly string[] PrefabAssetMutations = { "create-prefab", "set-prefab-gameobject", "set-prefab-transform", "add-prefab-component",
                                                          "remove-prefab-component", "set-prefab-property" };

        // An asset command (bridge 1.2.0): identifiers are asset GlobalObjectIds; mutations persist project files.
        public static bool IsAsset(string command) { return command.StartsWith("asset-", StringComparison.Ordinal) || Array.IndexOf(AssetMutations, command) >= 0; }

        static readonly string[] AssetMutations = { "create-material", "set-material-property", "create-scriptable-object", "set-asset-property" };
#if UNITY_EDITOR_WIN

        // bridge 1.6.0: the commands a Windows Editor serves — the session (status, attach approval and binding,
        // stale-session recovery grants, unbind, inspect) and the Scene-authoring slice qualified on Windows. Every
        // other command is UNKNOWN_COMMAND here before it is admitted, journaled or dispatched.
        public static readonly string[] WindowsCommands = { "status", "propose-attach", "attach-status", "abandon-proposal", "bind",
                                                            "propose-recovery", "recovery-status", "consume-recovery", "unbind",
                                                            "inspect", "object-inspect", "create-gameobject", "set-transform",
                                                            "save-scene" };
#endif

        // Parses and validates one request. Throws Refusal with a stable code; never executes anything.
        public static Request Parse(string fileId, string text, long nowTicks, string bootId)
        {
            Dictionary<string, object> d;
            try { d = Json.Parse(text) as Dictionary<string, object>; }
            catch (JsonProblem e) { throw new Refusal("MALFORMED_REQUEST", e.Message); }
            if (d == null) throw new Refusal("MALFORMED_REQUEST", "a request is a JSON object");
            if (d.Count != Keys.Length) throw new Refusal("MALFORMED_REQUEST", "a request has exactly the protocol keys");
            foreach (var k in Keys)
                if (!d.ContainsKey(k)) throw new Refusal("MALFORMED_REQUEST", "a request has exactly the protocol keys");
            if (!(d["schema"] is string) || (string)d["schema"] != RequestSchema)
                throw new Refusal("UNSUPPORTED_SCHEMA", "the request schema is not " + RequestSchema);
            var r = new Request();
            r.Id = d["request_id"] as string;
            if (r.Id == null || r.Id != fileId) throw new Refusal("REQUEST_ID_MISMATCH", "the request id is not its file name");
            r.Owner = d["owner"] as string;
            if (r.Owner == null || !Owner.IsMatch(r.Owner)) throw new Refusal("MALFORMED_REQUEST", "owner must be KIND:ID");
            r.BootId = d["boot_id"] as string;
            if (r.BootId == null || !Hex32.IsMatch(r.BootId)) throw new Refusal("MALFORMED_REQUEST", "boot_id must be 32 hex");
            r.IssuedTicks = Ticks(d["issued_utc"], "issued_utc");
            r.DeadlineTicks = Ticks(d["start_deadline_utc"], "start_deadline_utc");
            if (r.DeadlineTicks <= r.IssuedTicks ||
                r.DeadlineTicks - r.IssuedTicks > TimeSpan.FromSeconds(MaxStartWindowSeconds).Ticks)
                throw new Refusal("MALFORMED_REQUEST", "the start deadline must follow the issue time by at most " + MaxStartWindowSeconds + " s");
            if (r.IssuedTicks > nowTicks + TimeSpan.FromSeconds(FutureSkewSeconds).Ticks)
                throw new Refusal("MALFORMED_REQUEST", "the request is issued in the future");
            if (nowTicks > r.DeadlineTicks)
                throw new Refusal("LIVE_REQUEST_EXPIRED", "the start deadline passed before the bridge could start the request; it was not executed");
            if (r.BootId != bootId) throw new Refusal("BOOT_MISMATCH", "the request is addressed to another Editor boot");
            r.Command = d["command"] as string;
            Spec spec;
            if (r.Command == null || !Specs.TryGetValue(r.Command, out spec))
                throw new Refusal("UNKNOWN_COMMAND", "the command is not in the closed allowlist");
#if UNITY_EDITOR_WIN
            if (Array.IndexOf(WindowsCommands, r.Command) < 0)
                throw new Refusal("UNKNOWN_COMMAND", "the command is not in the closed allowlist of this platform (Windows)");
#endif
            r.Args = d["args"] as Dictionary<string, object>;
            if (r.Args == null || r.Args.Count != spec.Args.Length)
                throw new Refusal("BAD_ARGUMENTS", "the arguments are exactly " + string.Join(",", spec.Args));
            foreach (var a in spec.Args)
                if (!r.Args.ContainsKey(a)) throw new Refusal("BAD_ARGUMENTS", "the arguments are exactly " + string.Join(",", spec.Args));
            r.SessionId = d["session_id"] as string;
            if (spec.Session && (r.SessionId == null || !Hex32.IsMatch(r.SessionId)))
                throw new Refusal("MALFORMED_REQUEST", "this command needs a 32-hex session_id");
            if (!spec.Session && d["session_id"] != null)
                throw new Refusal("MALFORMED_REQUEST", "this command takes no session_id");
            return r;
        }

        static long Ticks(object value, string name)
        {
            var s = value as string;
            DateTime t;
            if (s == null || !Utc.IsMatch(s) ||
                !DateTime.TryParse(s, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out t))
                throw new Refusal("MALFORMED_REQUEST", name + " must be an RFC 3339 UTC timestamp");
            return t.Ticks;
        }

        public static string Hex(Dictionary<string, object> args, string key)
        {
            var s = args[key] as string;
            if (s == null || !Hex32.IsMatch(s)) throw new Refusal("BAD_ARGUMENTS", key + " must be 32 hex");
            return s;
        }

        public static string Key(Dictionary<string, object> args, string key)
        {
            var s = args[key] as string;
            if (s == null || !Hex16.IsMatch(s)) throw new Refusal("BAD_ARGUMENTS", key + " must be 16 hex");
            return s;
        }

        public static int Int(Dictionary<string, object> args, string key, int min, int max)
        {
            object v = args[key];
            if (!(v is double)) throw new Refusal("BAD_ARGUMENTS", key + " must be a whole number");
            double n = (double)v;
            if (n != Math.Floor(n) || n < min || n > max) throw new Refusal("BAD_ARGUMENTS", key + " must be between " + min + " and " + max);
            return (int)n;
        }
    }
}
