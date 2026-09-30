// GPOS build entry — the closed rules (Unity-free core, bridge 1.5.0). The batch-only BuildEntry reads the Editor's
// build configuration into BuildFacts; everything that decides whether that configuration is buildable by alpha.21,
// what the configuration token binds and how a request and a BuildReport are bounded lives here, so it is tested
// without Unity. Supported: macOS Standalone Player, Mono, the already active target, an active custom Build Profile
// for exactly that or the classic Editor build configuration. Nothing here chooses or changes a setting.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace Gpos.LiveBridge
{
    internal sealed class BuildRefusal : Exception
    {
        public readonly string Rule;
        public BuildRefusal(string rule, string message) : base(message) { Rule = rule; }
    }

    internal sealed class BuildRequest
    {
        public string Operation, RequestId, BuildId, ExpectedToken;
    }

    internal sealed class SceneFact
    {
        public string Path, Guid, ResolvedGuid, Sha256;
        public bool Exists, IsLink;
    }

    internal sealed class ProfileFact
    {
        public string Path, Guid, Sha256, PlatformId;
        public bool Readable, IsLink;
        public long BuildTarget, Subtarget, PlayerSettingsOverrides;
        public bool TargetIsMac, SubtargetIsPlayer, OverrideGlobalScenes;
        public string[] Defines = new string[0];
        public bool Development, ConnectProfiler, AllowDebugging, DeepProfiling, WaitForManagedDebugger, CodeCoverage,
                    CreateXcodeProject, InstallInBuildFolder;
        public long Architecture;
    }

    internal sealed class BuildFacts
    {
        public string UnityVersion, ActiveTarget, StandaloneSubtarget, Backend, ApplicationIdentifier;
        public bool TargetSupported, MacModuleInstalled, Development, Compiling, Updating, Building, ScriptsFailed;
        // classic Editor build state (the active profile's own values are used instead when a profile is active)
        public bool ConnectProfiler, AllowDebugging, DeepProfiling, WaitForManagedDebugger, CodeCoverage,
                    WaitForPlayerConnection, InstallInBuildFolder, CreateXcodeProject;
        public string Architecture;
        public Dictionary<string, string> Files = new Dictionary<string, string>(StringComparer.Ordinal);
        public List<SceneFact> Scenes = new List<SceneFact>();
        public int SceneCount;
        public ProfileFact Profile;
    }

    internal sealed class BuildProblem
    {
        public string Rule, Message;
    }

    internal static class BuildRules
    {
        public const string RequestSchema = "gpos.unity.build-request/1";
        public const string ResponseSchema = "gpos.unity.build-response/1";
        public const string StartedSchema = "gpos.unity.build-started/1";
        public const string ConfigSchema = "gpos.unity.build-config/1";
        public const string RequestFlag = "-gposBuildRequest";
        public const string RequestName = "build-request.json";
        public const string ResponseName = "build-response.json";
        public const string StartedName = "build-started.json";
        public const string StagingName = "staging";
        public const string PayloadName = "Player.app";
        public const string Target = "StandaloneOSX";
        public const string Player = "Player";
        public const string Mono = "Mono2x";
        public const string MacModuleGuid = "0d2129357eac403d8b359c2dcbf82502";   // the macOS Standalone platform module
        public const int MaxRequestBytes = 16 * 1024;
        public const int MaxScenes = 1024;
        public const int MaxDefines = 64;
        public const int MaxProblems = 64;
        public const int MaxMessages = 16;
        public const int MaxMessageChars = 400;
        public const int MaxStepChars = 120;
        public const int MaxProblemChars = 300;
        public const int MaxOptionsChars = 512;
        public const long MaxHashedFileBytes = 256L * 1024 * 1024;
        public const string Absent = "ABSENT";

        // The files whose bytes the token binds, relative to the Unity project.
        public static readonly string[] BoundFiles = { "Packages/manifest.json", "Packages/packages-lock.json",
                                                       "ProjectSettings/EditorBuildSettings.asset",
                                                       "ProjectSettings/ProjectSettings.asset" };

        // A build request id is the foundation's safe grammar narrowed so `build-<id>` is a stable lower-case id.
        public static readonly Regex BuildRequestId = new Regex("^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\\z");
        public static readonly Regex InspectRequestId = new Regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\\z");
        public static readonly Regex Token = new Regex("^[0-9a-f]{64}\\z");
        static readonly Regex Hex32 = new Regex("^[0-9a-f]{32}\\z");
        static readonly Regex ScenePath = new Regex("^Assets/(?:[^/\\\\\\x00-\\x1f]+/)*[^/\\\\\\x00-\\x1f]+\\.unity\\z");
        static readonly Regex ProfilePath = new Regex("^Assets/(?:[^/\\\\\\x00-\\x1f]+/)*[^/\\\\\\x00-\\x1f]+\\.asset\\z");

        // Rule ids, in the order a build refuses on them (the first problem found decides).
        public const string TargetModuleMissing = "TARGET_MODULE_MISSING", TargetNotActive = "TARGET_NOT_ACTIVE",
            EditorNotSettled = "EDITOR_NOT_SETTLED", ScriptsFailed = "SCRIPTS_FAILED",
            ProfileUnsupported = "PROFILE_NOT_MACOS_PLAYER", ProfileUnreadable = "PROFILE_FIELD_UNREADABLE",
            ProfileOverrides = "PROFILE_PLAYER_SETTINGS_OVERRIDE", SubtargetNotPlayer = "SUBTARGET_NOT_PLAYER",
            BackendNotMono = "BACKEND_NOT_MONO", DebugState = "DEBUG_STATE_UNSUPPORTED",
            DevelopmentAmbiguous = "DEVELOPMENT_AMBIGUOUS", OutputKind = "OUTPUT_NOT_PLAYER_APP", NoScenes = "NO_SCENES",
            SceneInvalid = "SCENE_INVALID", TooManyScenes = "TOO_MANY_SCENES", FileUnreadable = "FILE_UNREADABLE";

        public static bool IsScenePath(string path) { return path != null && ScenePath.IsMatch(path) && !path.Split('/').Any(Dotty); }
        public static bool IsProfilePath(string path) { return path != null && ProfilePath.IsMatch(path) && !path.Split('/').Any(Dotty); }
        static bool Dotty(string part) { return part == "." || part == ".." || part.Length == 0; }

        // ---------------------------------------------------------------- the request

        public static BuildRequest ParseRequest(string text)
        {
            if (text == null || Encoding.UTF8.GetByteCount(text) > MaxRequestBytes) throw new BuildRefusal("REQUEST_INVALID", "the request is missing or too large");
            Dictionary<string, object> d;
            try { d = Json.Parse(text) as Dictionary<string, object>; }
            catch (JsonProblem p) { throw new BuildRefusal("REQUEST_INVALID", "the request is not strict JSON: " + p.Message); }
            if (d == null) throw new BuildRefusal("REQUEST_INVALID", "the request is not a JSON object");
            string op = d.ContainsKey("operation") ? d["operation"] as string : null;
            string[] keys = op == "INSPECT" ? new[] { "schema", "operation", "request_id" }
                          : op == "BUILD" ? new[] { "schema", "operation", "request_id", "expected_configuration_token" }
                          : null;
            if (keys == null) throw new BuildRefusal("REQUEST_INVALID", "operation must be INSPECT or BUILD");
            if (d.Count != keys.Length || keys.Any(k => !d.ContainsKey(k) || !(d[k] is string)))
                throw new BuildRefusal("REQUEST_INVALID", "the request must hold exactly " + string.Join(", ", keys) + " as strings");
            if ((string)d["schema"] != RequestSchema) throw new BuildRefusal("REQUEST_INVALID", "unknown request schema");
            var r = new BuildRequest { Operation = op, RequestId = (string)d["request_id"] };
            if (!(op == "BUILD" ? BuildRequestId : InspectRequestId).IsMatch(r.RequestId))
                throw new BuildRefusal("REQUEST_INVALID", "the request id does not match the " + op + " grammar");
            if (op == "BUILD")
            {
                r.ExpectedToken = (string)d["expected_configuration_token"];
                if (!Token.IsMatch(r.ExpectedToken)) throw new BuildRefusal("REQUEST_INVALID", "the expected configuration token is not 64 lower-case hex digits");
                r.BuildId = "build-" + r.RequestId;
            }
            return r;
        }

        // ---------------------------------------------------------------- buildability and the canonical configuration

        static void Add(List<BuildProblem> problems, string rule, string message)
        {
            if (problems.Count < MaxProblems) problems.Add(new BuildProblem { Rule = rule, Message = Clip(message, MaxProblemChars) });
        }

        public static List<BuildProblem> Assess(BuildFacts f)
        {
            var p = new List<BuildProblem>();
            if (!f.TargetSupported || !f.MacModuleInstalled)
                Add(p, TargetModuleMissing, "the macOS Standalone build module is not installed or not supported by this Editor");
            if (f.ActiveTarget != Target)
                Add(p, TargetNotActive, "the active build target is " + f.ActiveTarget + ", not " + Target + "; GPOS never switches it");
            if (f.Compiling || f.Updating || f.Building)
                Add(p, EditorNotSettled, "the Editor is compiling, importing or already building");
            if (f.ScriptsFailed)
                Add(p, ScriptsFailed, "the last script compilation failed");
            bool debug, xcode, install, dev = f.Development;
            if (f.Profile != null)
            {
                var pr = f.Profile;
                if (pr.IsLink || !IsProfilePath(pr.Path) || pr.Guid == null || !Hex32.IsMatch(pr.Guid) || pr.Sha256 == null || pr.Sha256 == Absent ||
                    pr.Sha256.StartsWith("UNREADABLE", StringComparison.Ordinal))
                    Add(p, ProfileUnsupported, "the active Build Profile is not a regular .asset file below Assets/");
                if (!pr.Readable)
                    Add(p, ProfileUnreadable, "a field of the active Build Profile that alpha.21 binds could not be read");
                else
                {
                    if (!pr.TargetIsMac || pr.PlatformId != MacModuleGuid || !pr.SubtargetIsPlayer)
                        Add(p, ProfileUnsupported, "the active Build Profile does not describe a macOS Standalone Player");
                    if (pr.PlayerSettingsOverrides != 0)
                        Add(p, ProfileOverrides, "the active Build Profile overrides Player Settings (" + pr.PlayerSettingsOverrides + "); alpha.21 does not build with profile Player Settings overrides");
                    if (pr.Development != f.Development)
                        Add(p, DevelopmentAmbiguous, "the Editor's development state and the active Build Profile's disagree");
                }
                debug = pr.ConnectProfiler || pr.AllowDebugging || pr.DeepProfiling || pr.WaitForManagedDebugger || pr.CodeCoverage ||
                        f.WaitForPlayerConnection;   // the Editor-level flag has no profile field; it is refused in both modes
                xcode = pr.CreateXcodeProject;
                install = pr.InstallInBuildFolder;
            }
            else
            {
                debug = f.ConnectProfiler || f.AllowDebugging || f.DeepProfiling || f.WaitForManagedDebugger || f.CodeCoverage || f.WaitForPlayerConnection;
                xcode = f.CreateXcodeProject;
                install = f.InstallInBuildFolder;
            }
            if (f.StandaloneSubtarget != Player)
                Add(p, SubtargetNotPlayer, "the Standalone subtarget is " + f.StandaloneSubtarget + ", not Player");
            if (f.Backend != Mono)
                Add(p, BackendNotMono, "the Standalone scripting backend is " + f.Backend + "; alpha.21 builds Mono2x only");
            if (debug)
                Add(p, DebugState, "a debugger, profiler, deep-profiling or code-coverage option is enabled; development is the only debug state alpha.21 builds");
            if (xcode || install)
                Add(p, OutputKind, "the configuration would produce an Xcode project or install into the build folder, not a macOS Player .app");
            foreach (var kv in f.Files.Where(x => x.Value == null || x.Value.StartsWith("UNREADABLE", StringComparison.Ordinal)))
                Add(p, FileUnreadable, kv.Key + " could not be read as one regular file within the bound");
            if (f.SceneCount > MaxScenes)
                Add(p, TooManyScenes, "more than " + MaxScenes + " scenes are enabled for the build");
            else if (f.Scenes.Count == 0)
                Add(p, NoScenes, "no scene is enabled for the build");
            foreach (var s in f.Scenes)
                if (!IsScenePath(s.Path) || !s.Exists || s.IsLink || s.Guid == null || !Hex32.IsMatch(s.Guid) ||
                    s.ResolvedGuid != s.Guid || s.Sha256 == null || s.Sha256 == Absent || s.Sha256.StartsWith("UNREADABLE", StringComparison.Ordinal))
                    Add(p, SceneInvalid, "the build scene " + (s.Path ?? "(null)") + " is not an existing, known .unity asset below Assets/ with its recorded GUID");
            return p;
        }

        // The configuration the token binds: only what the alpha.21 build contract depends on. D-A/D-B states are
        // bound too (so they are visible), never normalised away.
        public static Dictionary<string, object> Configuration(BuildFacts f)
        {
            var c = new Dictionary<string, object>(StringComparer.Ordinal);
            c["schema"] = ConfigSchema;
            c["unity_version"] = f.UnityVersion;
            c["active_target"] = f.ActiveTarget;
            c["standalone_subtarget"] = f.StandaloneSubtarget;
            c["scripting_backend"] = f.Backend;
            c["application_identifier"] = f.ApplicationIdentifier;
            c["development"] = f.Development;
            c["mode"] = f.Profile == null ? "CLASSIC" : "PROFILE";
            c["scenes"] = f.Scenes.Select(s => (object)new Dictionary<string, object>(StringComparer.Ordinal) {
                { "path", s.Path }, { "guid", s.Guid }, { "sha256", s.Sha256 } }).ToList();
            var files = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (var name in BoundFiles) files[name] = f.Files.ContainsKey(name) ? f.Files[name] : null;
            c["files"] = files;
            var pr = f.Profile;
            c["debug"] = new Dictionary<string, object>(StringComparer.Ordinal) {
                { "connect_profiler", pr != null ? pr.ConnectProfiler : f.ConnectProfiler },
                { "allow_debugging", pr != null ? pr.AllowDebugging : f.AllowDebugging },
                { "deep_profiling", pr != null ? pr.DeepProfiling : f.DeepProfiling },
                { "wait_for_managed_debugger", pr != null ? pr.WaitForManagedDebugger : f.WaitForManagedDebugger },
                { "code_coverage", pr != null ? pr.CodeCoverage : f.CodeCoverage },
                { "wait_for_player_connection", f.WaitForPlayerConnection } };
            c["output"] = new Dictionary<string, object>(StringComparer.Ordinal) {
                { "create_xcode_project", pr != null ? pr.CreateXcodeProject : f.CreateXcodeProject },
                { "install_in_build_folder", pr != null ? pr.InstallInBuildFolder : f.InstallInBuildFolder },
                { "architecture", pr != null ? pr.Architecture.ToString(CultureInfo.InvariantCulture) : f.Architecture } };
            c["profile"] = pr == null ? null : new Dictionary<string, object>(StringComparer.Ordinal) {
                { "path", pr.Path }, { "guid", pr.Guid }, { "sha256", pr.Sha256 }, { "readable", pr.Readable },
                { "build_target", pr.BuildTarget }, { "platform_id", pr.PlatformId }, { "subtarget", pr.Subtarget },
                { "override_global_scenes", pr.OverrideGlobalScenes },
                { "scripting_defines", pr.Defines.Take(MaxDefines).Select(x => (object)Clip(x, MaxProblemChars)).ToList() },
                { "player_settings_overrides", pr.PlayerSettingsOverrides }, { "development", pr.Development } };
            return c;
        }

        public static string TokenOf(Dictionary<string, object> configuration)
        {
            return Sha256Hex(Encoding.UTF8.GetBytes(Canonical(configuration)));
        }

        // ---------------------------------------------------------------- canonical JSON (Python json.dumps with sort_keys,
        // separators (",", ":") and ensure_ascii): the token is recomputed by GPOS from the same bytes.

        public static string Canonical(object value)
        {
            var sb = new StringBuilder();
            Write(sb, value);
            return sb.ToString();
        }

        static void Write(StringBuilder sb, object v)
        {
            if (v == null) { sb.Append("null"); return; }
            var s = v as string;
            if (s != null) { Quote(sb, s); return; }
            if (v is bool) { sb.Append((bool)v ? "true" : "false"); return; }
            if (v is int || v is long) { sb.Append(Convert.ToInt64(v).ToString(CultureInfo.InvariantCulture)); return; }
            var d = v as IDictionary<string, object>;
            if (d != null)
            {
                sb.Append('{');
                bool first = true;
                foreach (var key in d.Keys.OrderBy(k => k, StringComparer.Ordinal))
                {
                    if (!first) sb.Append(',');
                    first = false;
                    Quote(sb, key);
                    sb.Append(':');
                    Write(sb, d[key]);
                }
                sb.Append('}');
                return;
            }
            var l = v as System.Collections.IEnumerable;
            if (l != null)
            {
                sb.Append('[');
                bool first = true;
                foreach (var x in l)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    Write(sb, x);
                }
                sb.Append(']');
                return;
            }
            throw new ArgumentException("no canonical form for " + v.GetType().Name);
        }

        static void Quote(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    default:
                        if (c < 0x20 || c > 0x7e) sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }

        public static string Sha256Hex(byte[] bytes)
        {
            using (var sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(bytes).Select(b => b.ToString("x2", CultureInfo.InvariantCulture)));
        }

        // ---------------------------------------------------------------- bounded text

        public static string Clip(string text, int max)
        {
            if (text == null) return "";
            var sb = new StringBuilder();
            foreach (char c in text)
            {
                if (sb.Length >= max) break;
                sb.Append(c < 0x20 || c == 0x7f ? ' ' : c);
            }
            return sb.ToString();
        }
    }
}
