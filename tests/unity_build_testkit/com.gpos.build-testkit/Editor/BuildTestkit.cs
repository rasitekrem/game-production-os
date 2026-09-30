// TEST ONLY — never installed by GPOS, never part of the audited bridge; only ever copied into disposable synthetic
// projects by tests/test_unity_build.py. It stands in for a Human changing a project's build configuration in the
// Editor (a build scene, the scene list, the bundle version, the Development and profiler checkboxes, the active target,
// a macOS Build Profile, its Player Settings overrides, profiler flag and scripting defines), each as one fixed op run
// in a lab-owned batch-mode Editor:  -executeMethod Gpos.BuildTestkit.Testkit.Run -gposBuildTestkitOp <op.json>
// and answered in <op.json>.out. It also holds a build step that sleeps while Library/gpos-build-testkit-slow names a
// number of seconds, so a test can kill a build that is known to have started. None of this exists in production.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Profile;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Gpos.BuildTestkit
{
    public static class Testkit
    {
        static string Arg(string flag)
        {
            var args = Environment.GetCommandLineArgs();
            int i = Array.IndexOf(args, flag);
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
        }

        static Dictionary<string, string> Parse(string text)
        {
            var d = new Dictionary<string, string>();
            foreach (var line in text.Split('\n'))
            {
                int i = line.IndexOf('=');
                if (i > 0) d[line.Substring(0, i).Trim()] = line.Substring(i + 1).Trim();
            }
            return d;
        }

        public static void Run()
        {
            string path = Arg("-gposBuildTestkitOp");
            string answer;
            int code = 0;
            try { answer = Handle(Parse(File.ReadAllText(path))); }
            catch (Exception e) { answer = "error=" + e.GetType().Name + ": " + e.Message.Replace('\n', ' '); code = 3; }
            File.WriteAllText(path + ".out", answer);
            EditorApplication.Exit(code);
        }

        static string Handle(Dictionary<string, string> r)
        {
            string op = r["op"], value = r.ContainsKey("value") ? r["value"] : "";
            switch (op)
            {
                case "scene":   // one saved Scene with a cube, the only scene enabled for the build
                {
                    string scenePath = value == "" ? "Assets/Scenes/Build.unity" : value;
                    Directory.CreateDirectory(Path.GetDirectoryName(scenePath));
                    var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
                    GameObject.CreatePrimitive(PrimitiveType.Cube).name = "Cube";
                    EditorSceneManager.SaveScene(scene, scenePath);
                    EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(scenePath, true) };
                    break;
                }
                case "scenes":  // exactly this list: "Assets/A.unity:1|Assets/B.unity:0", or empty for none
                    EditorBuildSettings.scenes = value.Split(new[] { '|' }, StringSplitOptions.RemoveEmptyEntries)
                        .Select(x => { var p = x.Split(':'); return new EditorBuildSettingsScene(p[0], p.Length < 2 || p[1] == "1"); }).ToArray();
                    break;
                case "version": PlayerSettings.bundleVersion = value; break;
                case "development": EditorUserBuildSettings.development = value == "true"; break;
                case "connect_profiler": EditorUserBuildSettings.connectProfiler = value == "true"; break;
                case "switch":
                {
                    var target = (BuildTarget)Enum.Parse(typeof(BuildTarget), value);
                    if (!EditorUserBuildSettings.SwitchActiveBuildTarget(BuildPipeline.GetBuildTargetGroup(target), target))
                        throw new InvalidOperationException("switch refused");
                    break;
                }
                case "profile":   // a new macOS Build Profile asset, made active
                {
                    var mac = BuildProfile.GetInstalledPlatformModules().First(m => m.displayName.Contains("macOS"));
                    var known = new HashSet<string>(BuildProfile.GetAllBuildProfiles().Select(x => AssetDatabase.GetAssetPath(x)));
                    BuildProfile.CreateBuildProfile(mac.platformGuid, value == "" ? "Mac Test" : value, null);
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                    var made = BuildProfile.GetAllBuildProfiles().First(x => !known.Contains(AssetDatabase.GetAssetPath(x)));
                    BuildProfile.SetActiveBuildProfile(made);
                    break;
                }
                case "profile_override":   // "Customize player settings" in the Build Profile window, as the Editor does it
                {
                    var profile = BuildProfile.GetActiveBuildProfile();
                    var flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
                    typeof(BuildProfile).GetMethod("CreatePlayerSettingsFromGlobal", flags).Invoke(profile, null);
                    typeof(BuildProfile).GetMethod("SerializePlayerSettings", flags).Invoke(profile, null);
                    EditorUtility.SetDirty(profile);
                    break;
                }
                case "profile_connect_profiler":
                    Edit(so => so.FindProperty("m_PlatformBuildProfile.m_ConnectProfiler").boolValue = value == "true");
                    break;
                case "profile_defines":
                {
                    var profile = BuildProfile.GetActiveBuildProfile();
                    profile.scriptingDefines = value.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries);
                    EditorUtility.SetDirty(profile);
                    break;
                }
                case "deactivate_profile": BuildProfile.SetActiveBuildProfile(null); break;
                case "open": break;   // just open (and import) the project
                default: throw new ArgumentException("unknown op " + op);
            }
            AssetDatabase.SaveAssets();
            var active = BuildProfile.GetActiveBuildProfile();
            return "ok=true\nactive_target=" + EditorUserBuildSettings.activeBuildTarget + "\ndevelopment=" + EditorUserBuildSettings.development +
                   "\nprofile=" + (active == null ? "" : AssetDatabase.GetAssetPath(active)) + "\n";
        }

        static void Edit(Action<SerializedObject> change)
        {
            var profile = BuildProfile.GetActiveBuildProfile();
            using (var so = new SerializedObject(profile))
            {
                change(so);
                so.ApplyModifiedPropertiesWithoutUndo();
            }
            EditorUtility.SetDirty(profile);
        }
    }

    // Holds the build while Library/gpos-build-testkit-slow names seconds: the build has started, so killing it is an
    // outcome-unknown case, never a pre-entry one.
    public class SlowBuildStep : IPostprocessBuildWithReport
    {
        public int callbackOrder { get { return 0; } }

        public void OnPostprocessBuild(BuildReport report)
        {
            string f = Path.Combine(Directory.GetCurrentDirectory(), "Library", "gpos-build-testkit-slow");
            if (File.Exists(f)) System.Threading.Thread.Sleep(1000 * int.Parse(File.ReadAllText(f).Trim()));
        }
    }
}
