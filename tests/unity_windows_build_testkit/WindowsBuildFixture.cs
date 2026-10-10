// TEST_ONLY: settings changes only inside the newly created alpha.27 fixture.
using System;
using System.IO;
using System.Linq;
using System.Threading;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Profile;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class GposWindowsBuildFixture
{
    static void Guard()
    {
        if (!Application.isBatchMode || !Application.dataPath.Replace('\\','/').StartsWith("D:/gpos-unity-lab-alpha27-qualification/"))
            throw new Exception("test fixture scope refused");
    }
    public static void Prepare()
    {
        Guard(); Directory.CreateDirectory("Assets/Scenes");
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        new GameObject("GposWindowsFixture");
        EditorSceneManager.SaveScene(scene,"Assets/Scenes/Demo.unity");
        EditorBuildSettings.scenes = new[] {new EditorBuildSettingsScene("Assets/Scenes/Demo.unity",true)};
        Restore();
    }
    public static void Restore()
    {
        Guard(); BuildProfile.SetActiveBuildProfile(null);
        if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.StandaloneWindows64)
            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Standalone, BuildTarget.StandaloneWindows64);
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.Standalone,ScriptingImplementation.Mono2x);
        EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Player;
        EditorUserBuildSettings.development = false;
        EditorUserBuildSettings.connectProfiler = false;
        EditorUserBuildSettings.allowDebugging = false;
        AssetDatabase.SaveAssets(); EditorApplication.Exit(0);
    }
    public static void Il2cpp()
    { Guard(); PlayerSettings.SetScriptingBackend(NamedBuildTarget.Standalone,ScriptingImplementation.IL2CPP); AssetDatabase.SaveAssets(); EditorApplication.Exit(0); }
    public static void CustomProfile()
    {
        Guard();
        var module = BuildProfile.GetInstalledPlatformModules().First(m => m.platformGuid.ToString() == "4e3c793746204150860bf175a9a41a05");
        var known = BuildProfile.GetAllBuildProfiles().Select(x => AssetDatabase.GetAssetPath(x)).ToArray();
        BuildProfile.CreateBuildProfile(module.platformGuid,"GPOS Windows Qualification",null);
        var profile = BuildProfile.GetAllBuildProfiles().First(x => !known.Contains(AssetDatabase.GetAssetPath(x)));
        BuildProfile.SetActiveBuildProfile(profile); AssetDatabase.SaveAssets(); EditorApplication.Exit(0);
    }
    public static void WrongTarget()
    { Guard(); EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Standalone,BuildTarget.StandaloneWindows); EditorApplication.Exit(0); }
}

public class GposWindowsBuildFault : IPreprocessBuildWithReport, IPostprocessBuildWithReport
{
    public int callbackOrder {get {return 0;}}
    string Mode { get {return File.Exists("fixture-mode.txt") ? File.ReadAllText("fixture-mode.txt") : "";} }
    public void OnPreprocessBuild(BuildReport report)
    {
        if (Mode == "fail") throw new BuildFailedException("GPOS TEST_ONLY deliberate BuildReport failure");
        if (Mode == "exit") EditorApplication.Exit(91); // abort after the production started marker; no final answer
        if (Mode == "hang") Thread.Sleep(120000);
    }
    public void OnPostprocessBuild(BuildReport report)
    {
        if (Mode == "drift") EditorUserBuildSettings.development = true;
    }
}
