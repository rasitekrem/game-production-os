// GPOS live bridge — the Human approval window. It is opened only by the Human (GPOS > Live Session) and is never
// opened, focused or brought forward by the bridge. It shows only fields the bridge generated or validated —
// never text a caller wrote. Attach requests and stale-session recoveries are separate sections with separate
// buttons; approving one never approves the other.
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Gpos.LiveBridge
{
    internal sealed class ApprovalWindow : EditorWindow
    {
        internal static ApprovalWindow Open;
        Vector2 scroll;

        [MenuItem("GPOS/Live Session")]
        static void OpenFromMenu() { GetWindow<ApprovalWindow>(false, "GPOS Live Session", true); }

        void OnEnable() { Open = this; }

        void OnDisable() { if (Open == this) Open = null; }

        static string Short(string id) { return string.IsNullOrEmpty(id) ? "-" : id.Substring(0, Math.Min(8, id.Length)); }

        void OnGUI()
        {
            var place = LiveBridge.Place;
            if (place == null)
            {
                GUILayout.Label("The GPOS live bridge is not active in this Editor.", EditorStyles.wordWrappedLabel);
                return;
            }
            GUILayout.Label("GPOS live session", EditorStyles.boldLabel);
            GUILayout.Label("GPOS project: " + Path.GetFileName(place.GposRoot) + "    Unity project: " + place.Rel, EditorStyles.wordWrappedMiniLabel);
            GUILayout.Label(LiveBridge.SessionId == "" ? "No session is attached." :
                            "Attached session " + Short(LiveBridge.SessionId) + " owned by " + LiveBridge.Owner, EditorStyles.wordWrappedMiniLabel);
            GUILayout.Label("Approving is operational consent for this Editor session only. It is not a Human Decision, a review or evidence.",
                            EditorStyles.wordWrappedMiniLabel);
            long now = DateTime.UtcNow.Ticks;
            var store = Commands.LoadProposals();
            var open = store.All.Where(p => ProposalStore.Effective(p, now) == "PENDING").ToList();
            scroll = GUILayout.BeginScrollView(scroll);
            Section("Attach requests", open.Where(p => p.Kind == ProposalStore.Attach), now, "Approve attach");
            Section("Recover stale GPOS session", open.Where(p => p.Kind == ProposalStore.Recover), now, "Approve recovery");
            GUILayout.EndScrollView();
        }

        void Section(string title, System.Collections.Generic.IEnumerable<Proposal> proposals, long now, string approve)
        {
            GUILayout.Space(8);
            GUILayout.Label(title, EditorStyles.boldLabel);
            bool any = false;
            foreach (var p in proposals)
            {
                any = true;
                EditorGUILayout.BeginVertical("box");
                GUILayout.Label("Requested by " + p.Owner);
                if (p.Kind == ProposalStore.Attach)
                    GUILayout.Label("Request " + Short(p.Id) + "    new session " + Short(p.SessionId));
                else
                    GUILayout.Label("Request " + Short(p.Id) + "    stale session " + Short(p.StaleSessionId) + " of " + p.StaleOwner +
                                    " (Editor boot " + Short(p.StaleBootId) + ")");
                GUILayout.Label("Expires in " + Math.Max(0, (p.Expires - now) / TimeSpan.TicksPerSecond) + " s");
                GUILayout.BeginHorizontal();
                if (GUILayout.Button(approve)) Approvals.Decide(p.Id, true);
                if (GUILayout.Button("Reject")) Approvals.Decide(p.Id, false);
                GUILayout.EndHorizontal();
                EditorGUILayout.EndVertical();
            }
            if (!any) GUILayout.Label("None pending.", EditorStyles.miniLabel);
        }
    }
}
