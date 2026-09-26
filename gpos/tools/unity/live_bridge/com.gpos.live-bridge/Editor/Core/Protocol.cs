// GPOS live bridge — the closed request protocol (Unity-free core).
// A request is one immutable JSON file: exact keys, a request id bound to its file name, the canonical owner
// KIND:ID, the bridge boot it is addressed to, one command from a closed allowlist with a fixed argument set, and
// an issue time plus a start deadline. There is no generic command, no code, no reflection target and no path.
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
        public const string Name = "gpos.unity.live/2";
        public const string RequestSchema = "gpos.unity.live.request/2";
        public const string ResponseSchema = "gpos.unity.live.response/2";
        public const string BridgeVersion = "1.1.0";
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
            public bool Session, Mutating, Authoring;
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
        };

        static Spec AuthoringSpec(bool mutating, params string[] args)
        {
            return new Spec { Session = true, Mutating = mutating, Authoring = true, Args = args };
        }

        public static IEnumerable<string> Commands { get { return Specs.Keys; } }

        public static bool NeedsSession(string command) { return Specs[command].Session; }

        public static bool ChangesEditorState(string command) { return Specs[command].Mutating && !Specs[command].Authoring; }

        // A Scene-authoring command: only in Edit Mode, with nothing pending (Transitions.AuthoringBusy).
        public static bool IsAuthoring(string command) { return Specs[command].Authoring; }

        public static bool ChangesScene(string command) { return Specs[command].Authoring && Specs[command].Mutating; }

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
