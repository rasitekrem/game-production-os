// TEST-ONLY: tests of the build part of the bridge's Unity-free core (Phase 2C-7, bridge 1.5.0): the closed build request
// and its two request-id grammars, the buildability rules in their refusal order (target, module, profile, D-A Player
// Settings overrides, D-B debug and profiler state, development authority, output kind, scenes, files), the canonical
// configuration and its token (a golden vector GPOS's Python recomputes byte for byte), and bounded text.
// Compiled and run with CoreTests.cs by tests/test_unity_live_bridge_core.py.
using System;
using System.Collections.Generic;
using System.Linq;

namespace Gpos.LiveBridge
{
    static partial class CoreTests
    {
        const string H64 = "abababababababababababababababababababababababababababababababab";
        const string G32 = "0123456789abcdef0123456789abcdef";
        // Python: json.dumps(configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=True) and its sha256
        const string GoldenCanonical = "{\"active_target\":\"StandaloneOSX\",\"application_identifier\":\"com.Gpos.Golden\",\"debug\":{\"allow_debugging\":false,\"code_coverage\":false,\"connect_profiler\":false,\"deep_profiling\":false,\"wait_for_managed_debugger\":false,\"wait_for_player_connection\":false},\"development\":false,\"files\":{\"Packages/manifest.json\":\"" + H64 + "\",\"Packages/packages-lock.json\":\"ABSENT\",\"ProjectSettings/EditorBuildSettings.asset\":\"" + H64 + "\",\"ProjectSettings/ProjectSettings.asset\":\"" + H64 + "\"},\"mode\":\"CLASSIC\",\"output\":{\"architecture\":\"x64ARM64\",\"create_xcode_project\":false,\"install_in_build_folder\":false},\"profile\":null,\"scenes\":[{\"guid\":\"" + G32 + "\",\"path\":\"Assets/Scenes/Main.unity\",\"sha256\":\"" + H64 + "\"}],\"schema\":\"gpos.unity.build-config/1\",\"scripting_backend\":\"Mono2x\",\"standalone_subtarget\":\"Player\",\"unity_version\":\"6000.5.8f1\"}";
        const string GoldenToken = "23846ab6c5842c539339f465a4ee42e8cf00892cc7fa5d27cfc719bc29eca971";

        static BuildFacts Classic()
        {
            var f = new BuildFacts {
                UnityVersion = "6000.5.8f1", ActiveTarget = "StandaloneOSX", StandaloneSubtarget = "Player", Backend = "Mono2x",
                ApplicationIdentifier = "com.Gpos.Golden", TargetSupported = true, MacModuleInstalled = true, Architecture = "x64ARM64" };
            f.Files["Packages/manifest.json"] = H64;
            f.Files["Packages/packages-lock.json"] = BuildRules.Absent;
            f.Files["ProjectSettings/EditorBuildSettings.asset"] = H64;
            f.Files["ProjectSettings/ProjectSettings.asset"] = H64;
            f.Scenes.Add(new SceneFact { Path = "Assets/Scenes/Main.unity", Guid = G32, ResolvedGuid = G32, Sha256 = H64, Exists = true });
            f.SceneCount = 1;
#if UNITY_EDITOR_WIN
            f.UnityVersion = "6000.6.4f1";
            f.ActiveTarget = "StandaloneWindows64";
            f.WindowsOutputReadable = true;
#endif
            return f;
        }

        static BuildFacts WithProfile()
        {
            var f = Classic();
            f.Profile = new ProfileFact {
                Path = "Assets/Settings/Build Profiles/Mac.asset", Guid = "fedcba9876543210fedcba9876543210", Sha256 = H64,
                PlatformId = BuildRules.MacModuleGuid, Readable = true, BuildTarget = 2, Subtarget = 2, TargetIsMac = true,
                SubtargetIsPlayer = true, Defines = new[] { "GPOS_PROFILE_DEF" }, Architecture = 2 };
            return f;
        }

        static string[] Rules(BuildFacts f) { return BuildRules.Assess(f).Select(p => p.Rule).ToArray(); }

        static string BuildCode(Action a)
        {
            try { a(); return null; }
            catch (BuildRefusal r) { return r.Rule; }
        }

        static void BuildRequestsAreClosed()
        {
            var inspect = BuildRules.ParseRequest("{\"schema\":\"gpos.unity.build-request/1\",\"operation\":\"INSPECT\",\"request_id\":\"Req.2026_x-1\"}");
            Equal("INSPECT", inspect.Operation, "inspect");
            Check(inspect.BuildId == null && inspect.ExpectedToken == null, "an inspection has no build id or token");
            var build = BuildRules.ParseRequest("{\"schema\":\"gpos.unity.build-request/1\",\"operation\":\"BUILD\",\"request_id\":\"req-0123456789abcdef\",\"expected_configuration_token\":\"" + GoldenToken + "\"}");
            Equal("build-req-0123456789abcdef", build.BuildId, "build id derived from the request id");
            Equal(GoldenToken, build.ExpectedToken, "token");
            string ok = "\"schema\":\"gpos.unity.build-request/1\",";
            foreach (var bad in new[] {
                null, "", "[]", "{}", "{" + ok + "\"operation\":\"RUN\",\"request_id\":\"a\"}",
                "{" + ok + "\"operation\":\"INSPECT\",\"request_id\":\"a\",\"method\":\"X.Y\"}",                       // no extra key
                "{" + ok + "\"operation\":\"INSPECT\",\"request_id\":\"../escape\"}",
                "{" + ok + "\"operation\":\"INSPECT\",\"request_id\":\"a/b\"}",
                "{" + ok + "\"operation\":\"INSPECT\",\"request_id\":7}",
                "{\"schema\":\"gpos.unity.build-request/2\",\"operation\":\"INSPECT\",\"request_id\":\"a\"}",
                "{" + ok + "\"operation\":\"BUILD\",\"request_id\":\"Req-1\",\"expected_configuration_token\":\"" + GoldenToken + "\"}",   // upper case
                "{" + ok + "\"operation\":\"BUILD\",\"request_id\":\"req.1\",\"expected_configuration_token\":\"" + GoldenToken + "\"}",   // a dot
                "{" + ok + "\"operation\":\"BUILD\",\"request_id\":\"req-\",\"expected_configuration_token\":\"" + GoldenToken + "\"}",    // trailing hyphen
                "{" + ok + "\"operation\":\"BUILD\",\"request_id\":\"" + new string('a', 65) + "\",\"expected_configuration_token\":\"" + GoldenToken + "\"}",
                "{" + ok + "\"operation\":\"BUILD\",\"request_id\":\"req-1\",\"expected_configuration_token\":\"" + GoldenToken.ToUpperInvariant() + "\"}",
                "{" + ok + "\"operation\":\"BUILD\",\"request_id\":\"req-1\"}",
                "{" + ok + "\"operation\":\"BUILD\",\"request_id\":\"req-1\",\"expected_configuration_token\":\"" + GoldenToken + "\",\"build_id\":\"x\"}",
                "{" + ok + "\"operation\":\"INSPECT\",\"request_id\":\"" + new string('a', 20000) + "\"}" })
                Equal("REQUEST_INVALID", BuildCode(() => BuildRules.ParseRequest(bad)), "refused: " + (bad == null ? "null" : bad.Substring(0, Math.Min(bad.Length, 90))));
        }

        static void TheCanonicalConfigurationIsPythonsBytes()
        {
            var c = BuildRules.Configuration(Classic());
            #if UNITY_EDITOR_WIN
            Equal("{\"active_target\":\"StandaloneWindows64\",\"application_identifier\":\"com.Gpos.Golden\",\"debug\":{\"allow_debugging\":false,\"code_coverage\":false,\"connect_profiler\":false,\"deep_profiling\":false,\"wait_for_managed_debugger\":false,\"wait_for_player_connection\":false},\"development\":false,\"files\":{\"Packages/manifest.json\":\"abababababababababababababababababababababababababababababababab\",\"Packages/packages-lock.json\":\"ABSENT\",\"ProjectSettings/EditorBuildSettings.asset\":\"abababababababababababababababababababababababababababababababab\",\"ProjectSettings/ProjectSettings.asset\":\"abababababababababababababababababababababababababababababababab\"},\"mode\":\"CLASSIC\",\"output\":{\"architecture\":\"x64\",\"copy_pdb\":false,\"create_solution\":false,\"install_in_build_folder\":false,\"readable\":true},\"profile\":null,\"scenes\":[{\"guid\":\"0123456789abcdef0123456789abcdef\",\"path\":\"Assets/Scenes/Main.unity\",\"sha256\":\"abababababababababababababababababababababababababababababababab\"}],\"schema\":\"gpos.unity.build-config/2\",\"scripting_backend\":\"Mono2x\",\"standalone_subtarget\":\"Player\",\"unity_version\":\"6000.6.4f1\"}",BuildRules.Canonical(c),"Windows Python golden");
            Equal("35fdf6b73e859fd9ce2316d9a4ed6a6357bc49436073a41a1a77e0ca55120634",BuildRules.TokenOf(c),"Windows golden token");
#else
            Equal(GoldenCanonical, BuildRules.Canonical(c), "canonical configuration");
            Equal(GoldenToken, BuildRules.TokenOf(c), "golden token");
#endif
            var escapes = new Dictionary<string, object> { { "k", "é\"\\\n\r\t\b\f\u0001\u007f/\U0001F600" }, { "a", new List<object> { 1L, -2L, true, null } } };
            Equal("{\"a\":[1,-2,true,null],\"k\":\"\\u00e9\\\"\\\\\\n\\r\\t\\b\\f\\u0001\\u007f/\\ud83d\\ude00\"}", BuildRules.Canonical(escapes), "escapes");
        }

        static void TheTokenBindsEveryBuildRelevantFact()
        {
#if UNITY_EDITOR_WIN
            string original=BuildRules.TokenOf(BuildRules.Configuration(Classic()));
            foreach (var change in new Action<BuildFacts>[] {f=>f.CreateSolution=true,f=>f.CopyPdb=true,f=>f.WindowsOutputReadable=false,
                f=>f.ActiveTarget="WebGL",f=>f.Backend="IL2CPP",f=>f.Development=true,f=>f.Scenes[0].Sha256="cd"+H64.Substring(2),
                f=>f.Files["ProjectSettings/ProjectSettings.asset"]="cd"+H64.Substring(2)})
            {var f=Classic();change(f);Check(original!=BuildRules.TokenOf(BuildRules.Configuration(f)),"Windows token failed to bind fact");} return;
#else

            string baseline = BuildRules.TokenOf(BuildRules.Configuration(Classic()));
            var changes = new List<Action<BuildFacts>> {
                f => f.Development = true, f => f.ActiveTarget = "WebGL", f => f.StandaloneSubtarget = "Server", f => f.Backend = "IL2CPP",
                f => f.ApplicationIdentifier = "com.other", f => f.Architecture = "ARM64", f => f.ConnectProfiler = true,
                f => f.AllowDebugging = true, f => f.DeepProfiling = true, f => f.WaitForManagedDebugger = true, f => f.CodeCoverage = true,
                f => f.WaitForPlayerConnection = true, f => f.InstallInBuildFolder = true, f => f.CreateXcodeProject = true,
                f => f.Files["ProjectSettings/ProjectSettings.asset"] = "cd" + H64.Substring(2), f => f.Files["Packages/packages-lock.json"] = H64,
                f => f.Scenes[0].Sha256 = "cd" + H64.Substring(2), f => f.Scenes[0].Guid = "1" + G32.Substring(1),
                f => f.Scenes.Add(new SceneFact { Path = "Assets/B.unity", Guid = G32, ResolvedGuid = G32, Sha256 = H64, Exists = true }),
                f => f.Profile = WithProfile().Profile };
            var seen = new HashSet<string> { baseline };
            foreach (var change in changes)
            {
                var f = Classic();
                change(f);
                Check(seen.Add(BuildRules.TokenOf(BuildRules.Configuration(f))), "a change the token must bind was not bound");
            }
            var p = WithProfile();
            string profiled = BuildRules.TokenOf(BuildRules.Configuration(p));
            foreach (var change in new List<Action<ProfileFact>> { x => x.Sha256 = "cd" + H64.Substring(2), x => x.Defines = new string[0],
                                                                    x => x.PlayerSettingsOverrides = 1, x => x.Development = true, x => x.CodeCoverage = true,
                                                                    x => x.Path = "Assets/Other.asset", x => x.Guid = G32 })
            {
                var f = WithProfile();
                change(f.Profile);
                Check(BuildRules.TokenOf(BuildRules.Configuration(f)) != profiled, "a profile change the token must bind was not bound");
            }
            // volatile Editor state is not configuration
            var busy = Classic();
            busy.Compiling = true;
            Equal(baseline, BuildRules.TokenOf(BuildRules.Configuration(busy)), "compiling is not configuration");
#endif
        }

        static void ASupportedClassicConfigurationIsBuildable()
        {
#if UNITY_EDITOR_WIN
            var f = Classic(); Equal(0, Rules(f).Length, "Windows classic");
            f.Development = true; Equal("DEBUG_STATE_UNSUPPORTED",Rules(f).Single(),"development refused");
            return;
#else

            Equal(0, Rules(Classic()).Length, "classic");
            var dev = Classic();
            dev.Development = true;
            Equal(0, Rules(dev).Length, "development is the one supported debug dimension");
            Equal(0, Rules(WithProfile()).Length, "a macOS Player profile");
#endif
        }

        static void TheRulesAreClosedAndOrdered()
        {
#if UNITY_EDITOR_WIN
            var f=Classic();f.TargetSupported=false;f.ActiveTarget="WebGL";f.Backend="IL2CPP";
            Equal("TARGET_MODULE_MISSING,TARGET_NOT_ACTIVE,BACKEND_NOT_MONO",string.Join(",",Rules(f)),"Windows order");
            foreach(var change in new Action<BuildFacts>[] {x=>x.CopyPdb=true,x=>x.CreateSolution=true,x=>x.InstallInBuildFolder=true})
            {var x=Classic();change(x);Equal("OUTPUT_NOT_PLAYER_APP",Rules(x).Single(),"sidecar output refused");}
            var unread=Classic();unread.WindowsOutputReadable=false;Equal("WINDOWS_OUTPUT_UNREADABLE",Rules(unread).Single(),"fail closed");
            var wrongVersion=Classic();wrongVersion.UnityVersion="6000.6.3f1";Equal("WINDOWS_VERSION_UNSUPPORTED",Rules(wrongVersion).Single(),"version pin"); return;
#else

            var f = Classic();
            f.ActiveTarget = "WebGL";
            f.TargetSupported = false;
            f.Backend = "IL2CPP";
            Equal("TARGET_MODULE_MISSING,TARGET_NOT_ACTIVE,BACKEND_NOT_MONO", string.Join(",", Rules(f)), "order");
            var cases = new Dictionary<string, Action<BuildFacts>> {
                { "TARGET_MODULE_MISSING", x => x.MacModuleInstalled = false }, { "TARGET_NOT_ACTIVE", x => x.ActiveTarget = "Android" },
                { "EDITOR_NOT_SETTLED", x => x.Updating = true }, { "SCRIPTS_FAILED", x => x.ScriptsFailed = true },
                { "SUBTARGET_NOT_PLAYER", x => x.StandaloneSubtarget = "Server" }, { "BACKEND_NOT_MONO", x => x.Backend = "IL2CPP" },
                { "OUTPUT_NOT_PLAYER_APP", x => x.CreateXcodeProject = true }, { "NO_SCENES", x => { x.Scenes.Clear(); x.SceneCount = 0; } },
                { "TOO_MANY_SCENES", x => x.SceneCount = BuildRules.MaxScenes + 1 },
                { "FILE_UNREADABLE", x => x.Files["Packages/manifest.json"] = "UNREADABLE:link" } };
            foreach (var kv in cases)
            {
                var g = Classic();
                kv.Value(g);
                Equal(kv.Key, Rules(g).FirstOrDefault(), "rule " + kv.Key);
            }
            var install = Classic();
            install.InstallInBuildFolder = true;
            Equal("OUTPUT_NOT_PLAYER_APP", Rules(install).Single(), "install in build folder");
#endif
        }

        static void DebugAndProfilerStateIsRefusedInBothModes()
        {
#if UNITY_EDITOR_WIN
            foreach (var change in new Action<BuildFacts>[] {f=>f.Development=true,f=>f.ConnectProfiler=true,
                f=>f.AllowDebugging=true,f=>f.DeepProfiling=true,f=>f.WaitForManagedDebugger=true,f=>f.CodeCoverage=true,f=>f.WaitForPlayerConnection=true})
            {var f=Classic();change(f);Equal("DEBUG_STATE_UNSUPPORTED",Rules(f).Single(),"Windows debug refused");} return;
#else
   // D-B
            var classic = new List<Action<BuildFacts>> { x => x.ConnectProfiler = true, x => x.AllowDebugging = true, x => x.DeepProfiling = true,
                                                         x => x.WaitForManagedDebugger = true, x => x.CodeCoverage = true, x => x.WaitForPlayerConnection = true };
            foreach (var c in classic)
            {
                var f = Classic();
                c(f);
                Equal("DEBUG_STATE_UNSUPPORTED", Rules(f).Single(), "classic debug state");
            }
            var profiled = new List<Action<ProfileFact>> { x => x.ConnectProfiler = true, x => x.AllowDebugging = true, x => x.DeepProfiling = true,
                                                            x => x.WaitForManagedDebugger = true, x => x.CodeCoverage = true };
            foreach (var c in profiled)
            {
                var f = WithProfile();
                c(f.Profile);
                Equal("DEBUG_STATE_UNSUPPORTED", Rules(f).Single(), "profile debug state");
            }
            var both = WithProfile();
            both.WaitForPlayerConnection = true;
            Equal("DEBUG_STATE_UNSUPPORTED", Rules(both).Single(), "the Editor-level connection flag in profile mode");
            var ignored = WithProfile();
            ignored.ConnectProfiler = true;   // with a profile active its own values decide, never the classic ones
            Equal(0, Rules(ignored).Length, "profile mode reads the profile's own flags");
#endif
        }

        static void ProfilesAreMacOSPlayerOnlyWithoutOverrides()
        {
#if UNITY_EDITOR_WIN
            Equal("WINDOWS_PROFILE_UNSUPPORTED",Rules(WithProfile()).First(),"all custom profiles refused"); return;
#else
   // D2 and D-A
            var cases = new Dictionary<string, Action<ProfileFact>> {
                { "PROFILE_PLAYER_SETTINGS_OVERRIDE", x => x.PlayerSettingsOverrides = 3 },
                { "PROFILE_FIELD_UNREADABLE", x => x.Readable = false },
                { "DEVELOPMENT_AMBIGUOUS", x => x.Development = true },
                { "OUTPUT_NOT_PLAYER_APP", x => x.CreateXcodeProject = true } };
            foreach (var kv in cases)
            {
                var f = WithProfile();
                kv.Value(f.Profile);
                Equal(kv.Key, Rules(f).Single(), kv.Key);
            }
            foreach (var bad in new List<Action<ProfileFact>> { x => x.TargetIsMac = false, x => x.PlatformId = "b9b35072a6f44c2e863f17467ea3dc13",
                                                               x => x.SubtargetIsPlayer = false, x => x.Path = "Packages/p/Mac.asset",
                                                               x => x.Path = "Assets/../Mac.asset", x => x.IsLink = true, x => x.Sha256 = BuildRules.Absent,
                                                               x => x.Sha256 = "UNREADABLE:io", x => x.Guid = "" })
            {
                var f = WithProfile();
                bad(f.Profile);
                Equal("PROFILE_NOT_MACOS_PLAYER", Rules(f).First(), "profile shape");
            }
            var agreed = WithProfile();
            agreed.Development = true;
            agreed.Profile.Development = true;
            Equal(0, Rules(agreed).Length, "the profile's development state, agreed by the Editor, is buildable");
            Equal("PROFILE", BuildRules.Configuration(agreed)["mode"], "profile mode");
#endif
        }

        static void BuildScenesMustBeKnownAssetsBelowAssets()
        {
            var bad = new List<Action<SceneFact>> { s => s.Path = "Packages/x/A.unity", s => s.Path = "Assets/A.scene", s => s.Path = "Assets/../A.unity",
                                                    s => s.Path = "Assets//A.unity", s => s.Path = null, s => s.Exists = false, s => s.IsLink = true,
                                                    s => s.ResolvedGuid = "", s => s.Guid = "zz", s => s.Sha256 = "UNREADABLE:size", s => s.Sha256 = BuildRules.Absent };
            foreach (var change in bad)
            {
                var f = Classic();
                change(f.Scenes[0]);
                Equal("SCENE_INVALID", Rules(f).Single(), "scene");
            }
        }

        static void BuildTextIsBounded()
        {
            Equal("a b", BuildRules.Clip("a\nb", 10), "control characters");
            Equal(5, BuildRules.Clip(new string('x', 50), 5).Length, "length");
            Equal("", BuildRules.Clip(null, 5), "null");
            var many = Classic();
            for (int i = 0; i < 200; i++) many.Scenes.Add(new SceneFact { Path = "bad" });
            Equal(BuildRules.MaxProblems, BuildRules.Assess(many).Count, "problems are bounded");
        }
    }
}
