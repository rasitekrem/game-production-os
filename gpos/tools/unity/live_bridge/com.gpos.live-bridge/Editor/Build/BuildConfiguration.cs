// GPOS build entry — reads the Editor's existing build configuration into BuildFacts (bridge 1.5.0, batch only).
// Read-only: public Unity build APIs, a SerializedObject view of the active Build Profile for the fields the public
// API does not expose (target, platform, subtarget, Player Settings overrides and its debug and output flags), and
// streamed SHA-256 of the bound project files. Nothing is assigned, activated, switched, imported or saved here.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Profile;
using UnityEngine;

namespace Gpos.LiveBridge.Build
{
    internal static class BuildConfiguration
    {
        internal static BuildFacts Read(string projectPath, out BuildProfile activeProfile)
        {
            var f = new BuildFacts();
            f.UnityVersion = Application.unityVersion;
            f.ActiveTarget = EditorUserBuildSettings.activeBuildTarget.ToString();
            f.TargetSupported = BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.Standalone, BuildTarget.StandaloneOSX);
            f.MacModuleInstalled = BuildProfile.GetInstalledPlatformModules().Any(m => m.platformGuid.ToString() == BuildRules.MacModuleGuid);
            f.StandaloneSubtarget = EditorUserBuildSettings.standaloneBuildSubtarget.ToString();
            f.Backend = PlayerSettings.GetScriptingBackend(NamedBuildTarget.Standalone).ToString();
            f.ApplicationIdentifier = PlayerSettings.GetApplicationIdentifier(NamedBuildTarget.Standalone);
            f.Development = EditorUserBuildSettings.development;
            f.Compiling = EditorApplication.isCompiling;
            f.Updating = EditorApplication.isUpdating;
            f.Building = BuildPipeline.isBuildingPlayer;
            f.ScriptsFailed = EditorUtility.scriptCompilationFailed;
            f.ConnectProfiler = EditorUserBuildSettings.connectProfiler;
            f.AllowDebugging = EditorUserBuildSettings.allowDebugging;
            f.DeepProfiling = EditorUserBuildSettings.buildWithDeepProfilingSupport;
            f.WaitForManagedDebugger = EditorUserBuildSettings.waitForManagedDebugger;
            f.CodeCoverage = EditorUserBuildSettings.buildWithCodeCoverage;
            f.WaitForPlayerConnection = EditorUserBuildSettings.waitForPlayerConnection;
            f.InstallInBuildFolder = EditorUserBuildSettings.installInBuildFolder;
            f.CreateXcodeProject = EditorUserBuildSettings.GetPlatformSettings("Standalone", "OSXUniversal", "CreateXcodeProject") == "true";
            f.Architecture = EditorUserBuildSettings.GetPlatformSettings("Standalone", "OSXUniversal", "Architecture") ?? "";
            foreach (var name in BuildRules.BoundFiles) f.Files[name] = Hash(projectPath, name);
            activeProfile = BuildProfile.GetActiveBuildProfile();
            EditorBuildSettingsScene[] scenes;
            if (activeProfile != null)
            {
                f.Profile = Profile(projectPath, activeProfile);
                scenes = activeProfile.GetScenesForBuild();
            }
            else scenes = EditorBuildSettings.scenes;
            var enabled = (scenes ?? new EditorBuildSettingsScene[0]).Where(s => s != null && s.enabled).ToList();
            f.SceneCount = enabled.Count;
            foreach (var s in enabled.Take(BuildRules.MaxScenes)) f.Scenes.Add(Scene(projectPath, s));
            return f;
        }

        static SceneFact Scene(string projectPath, EditorBuildSettingsScene s)
        {
            var fact = new SceneFact { Path = s.path, Guid = s.guid.ToString() };
            if (!BuildRules.IsScenePath(s.path)) return fact;
            string full = Path.Combine(projectPath, s.path);
            fact.IsLink = Identity.IsLink(full);
            fact.Exists = File.Exists(full);
            fact.ResolvedGuid = AssetDatabase.AssetPathToGUID(s.path, AssetPathToGUIDOptions.OnlyExistingAssets);
            fact.Sha256 = Hash(projectPath, s.path);
            return fact;
        }

        static ProfileFact Profile(string projectPath, BuildProfile profile)
        {
            var pr = new ProfileFact { Path = AssetDatabase.GetAssetPath(profile) };
            if (BuildRules.IsProfilePath(pr.Path))
            {
                pr.Guid = AssetDatabase.AssetPathToGUID(pr.Path, AssetPathToGUIDOptions.OnlyExistingAssets);
                pr.IsLink = Identity.IsLink(Path.Combine(projectPath, pr.Path));
                pr.Sha256 = Hash(projectPath, pr.Path);
            }
            pr.OverrideGlobalScenes = profile.overrideGlobalScenes;
            pr.Defines = profile.scriptingDefines ?? new string[0];
            using (var so = new SerializedObject(profile))
            {
                long? target = Long(so, "m_BuildTarget"), subtarget = Long(so, "m_Subtarget"),
                      architecture = Long(so, "m_PlatformBuildProfile.m_Architecture");
                string platform = Text(so, "m_PlatformId");
                var overrides = so.FindProperty("m_PlayerSettingsYaml.m_Settings");
                const string p = "m_PlatformBuildProfile.";
                bool? dev = Flag(so, p + "m_Development"), profiler = Flag(so, p + "m_ConnectProfiler"),
                      debugging = Flag(so, p + "m_AllowDebugging"), deep = Flag(so, p + "m_BuildWithDeepProfilingSupport"),
                      managed = Flag(so, p + "m_WaitForManagedDebugger"), coverage = Flag(so, p + "m_BuildWithCodeCoverage"),
                      xcode = Flag(so, p + "m_CreateXcodeProject"), install = Flag(so, p + "m_InstallInBuildFolder");
                pr.Readable = target.HasValue && subtarget.HasValue && architecture.HasValue && platform != null &&
                              overrides != null && overrides.isArray && dev.HasValue && profiler.HasValue && debugging.HasValue &&
                              deep.HasValue && managed.HasValue && coverage.HasValue && xcode.HasValue && install.HasValue;
                if (!pr.Readable) return pr;
                pr.BuildTarget = target.Value;
                pr.Subtarget = subtarget.Value;
                pr.PlatformId = platform;
                pr.TargetIsMac = target.Value == (long)BuildTarget.StandaloneOSX;
                pr.SubtargetIsPlayer = subtarget.Value == (long)StandaloneBuildSubtarget.Player;
                pr.PlayerSettingsOverrides = overrides.arraySize;
                pr.Development = dev.Value;
                pr.ConnectProfiler = profiler.Value;
                pr.AllowDebugging = debugging.Value;
                pr.DeepProfiling = deep.Value;
                pr.WaitForManagedDebugger = managed.Value;
                pr.CodeCoverage = coverage.Value;
                pr.CreateXcodeProject = xcode.Value;
                pr.InstallInBuildFolder = install.Value;
                pr.Architecture = architecture.Value;
            }
            return pr;
        }

        static long? Long(SerializedObject so, string path)
        {
            var p = so.FindProperty(path);
            if (p == null) return null;
            if (p.propertyType == SerializedPropertyType.Integer || p.propertyType == SerializedPropertyType.Enum) return p.longValue;
            return null;
        }

        static bool? Flag(SerializedObject so, string path)
        {
            var p = so.FindProperty(path);
            if (p == null) return null;
            if (p.propertyType == SerializedPropertyType.Boolean) return p.boolValue;
            if (p.propertyType == SerializedPropertyType.Integer) return p.longValue != 0;
            return null;
        }

        static string Text(SerializedObject so, string path)
        {
            var p = so.FindProperty(path);
            if (p == null) return null;
            if (p.propertyType == SerializedPropertyType.String) return p.stringValue;
            try
            {
                object boxed = p.boxedValue;
                return boxed == null ? null : boxed.ToString();
            }
            catch (Exception) { return null; }
        }

        // ABSENT when no such file; UNREADABLE:<why> for a link, a directory, an oversized or unreadable file.
        internal static string Hash(string projectPath, string rel)
        {
            string full = Path.Combine(projectPath, rel);
            try
            {
                if (Identity.IsLink(full)) return "UNREADABLE:link";
                if (Directory.Exists(full)) return "UNREADABLE:directory";
                if (!File.Exists(full)) return BuildRules.Absent;
                using (var stream = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    if (stream.Length > BuildRules.MaxHashedFileBytes) return "UNREADABLE:size";
                    using (var sha = SHA256.Create())
                        return string.Concat(sha.ComputeHash(stream).Select(b => b.ToString("x2")));
                }
            }
            catch (Exception) { return "UNREADABLE:io"; }
        }
    }
}
