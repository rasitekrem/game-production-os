// TEST ONLY (alpha.22): what a Human would set in the Editor for the synthetic QualGame, run once in a lab-owned batch
// Editor: one scene with the QualGame component as the only build scene, a unique company/product/bundle identifier per
// test run, a 960x540 window and runInBackground. Never installed or run by GPOS.
using System;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Gpos.PlayerTestkit
{
    public static class Testkit
    {
        public static void Run()
        {
            var args = Environment.GetCommandLineArgs();
            int at = Array.IndexOf(args, "-gposQualId");
            if (at < 0 || at + 1 >= args.Length) { EditorApplication.Exit(64); return; }
            string id = args[at + 1];
            System.IO.Directory.CreateDirectory("Assets/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
            var host = new GameObject("QualGame");
            var type = Type.GetType("QualGame, Assembly-CSharp");
            if (type == null) { EditorApplication.Exit(65); return; }
            host.AddComponent(type);
            EditorSceneManager.SaveScene(scene, "Assets/Scenes/Qual.unity");
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene("Assets/Scenes/Qual.unity", true) };
            PlayerSettings.companyName = "GposTest";
            PlayerSettings.productName = "GposPlayerQual" + id;
            PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Standalone, "com.gpos.test.playerqual" + id);
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            PlayerSettings.defaultScreenWidth = 960;
            PlayerSettings.defaultScreenHeight = 540;
            PlayerSettings.resizableWindow = true;
            PlayerSettings.runInBackground = true;
            PlayerSettings.usePlayerLog = true;
            AssetDatabase.SaveAssets();
            EditorApplication.Exit(0);
        }
    }
}
