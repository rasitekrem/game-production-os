// TEST-ONLY (alpha.22 player qualification): a synthetic game. It animates on its own, logs structured GPOSQ lines
// (including a planted credential-shaped value and its persistent data path, for the sanitizer tests), draws the value it
// restored from its previous graceful quit as a 32-cell stripe inside a red frame (decoded from screenshots), and saves a
// fresh value only when it quits gracefully. Its mode comes from a test-written control file next to the GPOS runtime
// directories, found through the fixed `-logFile <runtime>/player.log` argument; nothing is written into the save folder
// by the tests. GPOS production never writes that control file.
using System;
using System.IO;
using UnityEngine;

public class QualGame : MonoBehaviour
{
    Transform cube;
    int tick;
    float next, started;
    bool readyLogged;
    string mode = "animate", restored = "none", nextValue = "";
    Texture2D white;

    static string ControlFile()
    {
        var args = Environment.GetCommandLineArgs();
        int at = Array.IndexOf(args, "-logFile");
        if (at < 0 || at + 1 >= args.Length) return null;
        string runtime = Path.GetDirectoryName(args[at + 1]);           // .../tool-output/player/<launch request id>
        return Path.Combine(Path.GetDirectoryName(runtime), "player-qual-control.txt");
    }

    void Start()
    {
        started = Time.realtimeSinceStartup;
        white = Texture2D.whiteTexture;
        cube = GameObject.CreatePrimitive(PrimitiveType.Cube).transform;
        var cam = Camera.main;
        if (cam != null) { cam.transform.position = new Vector3(0, 0, -4); cam.backgroundColor = new Color(0.1f, 0.2f, 0.45f); cam.clearFlags = CameraClearFlags.SolidColor; }
        string control = ControlFile();
        if (control != null && File.Exists(control)) mode = File.ReadAllText(control).Trim();
        if (mode == "large") Screen.SetResolution(3840, 2400, FullScreenMode.Windowed);
        else Screen.SetResolution(960, 540, FullScreenMode.Windowed);
        string save = Path.Combine(Application.persistentDataPath, "qual-save.txt");
        if (File.Exists(save)) restored = File.ReadAllText(save).Trim();
        nextValue = ((uint)new System.Random(Guid.NewGuid().GetHashCode()).Next() | 0x80000001u).ToString("x8");
        Debug.Log("GPOSQ start mode=" + mode + " restored=" + restored + " next=" + nextValue);
        Debug.Log("GPOSQ paths persistent=" + Application.persistentDataPath + " home=" +
                  Environment.GetEnvironmentVariable("HOME") + " secret API_KEY=qual-secret-value-7f3a");
        Application.quitting += () => Debug.Log("GPOSQ quitting tick=" + tick);
    }

    void OnApplicationQuit()
    {
        File.WriteAllText(Path.Combine(Application.persistentDataPath, "qual-save.txt"), nextValue);
        Debug.Log("GPOSQ saved=" + nextValue);
    }

    void Update()
    {
        cube.Rotate(40 * Time.deltaTime, 70 * Time.deltaTime, 0);
        // The game's code runs while Unity's splash screen is still shown; tests that need the game's own pixels wait
        // for this line (GPOS itself never claims readiness).
        if (!readyLogged && UnityEngine.Rendering.SplashScreen.isFinished) { readyLogged = true; Debug.Log("GPOSQ ready"); }
        float t = Time.realtimeSinceStartup - started;
        if (Time.realtimeSinceStartup >= next) { next = Time.realtimeSinceStartup + 0.5f; tick++; Debug.Log("GPOSQ tick=" + tick + " t=" + t.ToString("F1")); }
        if (t < 3f) return;
        switch (mode)
        {
            case "exit0": mode = "done"; Application.Quit(0); break;
            case "exit3": mode = "done"; Application.Quit(3); break;
            case "exception": mode = "animate"; throw new InvalidOperationException("GPOSQ deliberate exception");
            case "crash": UnityEngine.Diagnostics.Utils.ForceCrash(UnityEngine.Diagnostics.ForcedCrashCategory.Abort); break;
            case "hang": Debug.Log("GPOSQ hanging"); while (true) { }
        }
    }

    void OnGUI()
    {
        var style = new GUIStyle(GUI.skin.label) { fontSize = 28 };
        style.normal.textColor = Color.yellow;
        GUI.Label(new Rect(20, 10, 900, 40), "GPOSQ tick " + tick, style);
        GUI.Label(new Rect(20, 50, 900, 40), "restored: " + restored, style);
        if (restored.Length != 8) return;
        uint value = Convert.ToUInt32(restored, 16);
        GUI.color = Color.red;
        GUI.DrawTexture(new Rect(16, 100, 32 * 13 + 8, 48), white);
        for (int i = 0; i < 32; i++)
        {
            GUI.color = ((value >> (31 - i)) & 1u) == 1u ? Color.white : Color.black;
            GUI.DrawTexture(new Rect(20 + i * 13, 104, 13, 40), white);
        }
        GUI.color = Color.white;
    }
}
