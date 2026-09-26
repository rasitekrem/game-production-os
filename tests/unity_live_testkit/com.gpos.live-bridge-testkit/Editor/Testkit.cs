// TEST ONLY — never installed by GPOS, never part of the audited bridge, only ever copied into disposable synthetic
// projects by tests/test_unity_live.py. In a lab-owned batch-mode Editor it:
//   * starts the production bridge (which by design never starts itself in batch mode) by calling its internal
//     activation, the same method the bridge's own constructor calls in a windowed Editor;
//   * presses the production approval method — the one the approval window's buttons call — for proposals whose
//     owner id is test-named ("AGENT:testkit-approve..." approves, "AGENT:testkit-reject..." rejects); any other
//     owner is left for a Human;
//   * obeys trigger files in <project>/Temp/gpos-testkit/: "reload" (script reload), "refresh" (asset refresh),
//     "quit" (Editor exit) and "block-<ms>" (block the main thread), so tests can create reloads, compilations,
//     busy periods and a clean exit on demand.
// None of this exists in the production bridge: it has no activation switch, no approval command and no triggers.
using System;
using System.IO;
using System.Reflection;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

namespace Gpos.LiveBridge.Testkit
{
    [InitializeOnLoad]
    static class Testkit
    {
        static readonly Regex Proposal = new Regex("\"id\":\"([0-9a-f]{32})\"[^}]*?\"owner\":\"(AGENT:testkit-(approve|reject)[^\"]*)\"[^}]*?\"state\":\"PENDING\"");
        static MethodInfo decide;
        static string triggers;

        static Testkit()
        {
            if (AssetDatabase.IsAssetImportWorkerProcess() || !Application.isBatchMode) return;
            var bridge = Type.GetType("Gpos.LiveBridge.LiveBridge, Gpos.LiveBridge.Editor");
            var approvals = Type.GetType("Gpos.LiveBridge.Approvals, Gpos.LiveBridge.Editor");
            if (bridge == null || approvals == null) { Debug.LogError("[gpos-testkit] the bridge assembly is missing"); return; }
            bridge.GetMethod("Activate", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            decide = approvals.GetMethod("Decide", BindingFlags.Static | BindingFlags.NonPublic);
            triggers = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Temp", "gpos-testkit"));
            EditorApplication.update += Tick;
        }

        static void Tick()
        {
            foreach (Match m in Proposal.Matches(SessionState.GetString("gpos.live.proposals", "")))
                decide.Invoke(null, new object[] { m.Groups[1].Value, m.Groups[3].Value == "approve" });
            if (!Directory.Exists(triggers)) return;
            foreach (var f in Directory.GetFiles(triggers))
            {
                string name = Path.GetFileName(f);
                File.Delete(f);
                if (name == "reload") EditorUtility.RequestScriptReload();
                else if (name == "refresh") AssetDatabase.Refresh();
                else if (name == "quit") EditorApplication.Exit(0);
                else if (name.StartsWith("block-", StringComparison.Ordinal)) System.Threading.Thread.Sleep(int.Parse(name.Substring(6)));
            }
        }
    }
}
