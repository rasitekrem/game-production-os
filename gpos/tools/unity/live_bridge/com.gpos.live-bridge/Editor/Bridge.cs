// GPOS live bridge — lifecycle. Fixed GPOS-owned Editor code with a closed protocol: no caller C#, no reflection
// entry point, no executeMethod, no menu execution, no input, no listener and no network.
//
// The bridge never runs in an asset import worker and never in a batch-mode Editor (so a GPOS batch invocation can
// never activate it). In a normal windowed Editor the static constructor only registers callbacks; every file
// operation happens in the Editor update tick, never during initialization and never through delayCall.
using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace Gpos.LiveBridge
{
    [InitializeOnLoad]
    internal static class LiveBridge
    {
        const double BeatEvery = 0.25;
        const double RetentionEvery = 30.0;
        const string KBoot = "gpos.live.boot", KGeneration = "gpos.live.generation";
        internal const string KSession = "gpos.live.session", KOwner = "gpos.live.owner", KBound = "gpos.live.bound",
                              KProposal = "gpos.live.proposal", KJournal = "gpos.live.journal",
                              KProposals = "gpos.live.proposals", KPending = "gpos.live.pending";

        static bool activated, started, dormant, quitting, reloading;
        static long seq;
        static double lastBeat = -1, lastRetention = -1, lastRepaint = -1;
        static int pid;
        static string startedUtc = "";

        internal static Place Place;
        internal static string BootId = "", PackageDigest;
        internal static int Generation;

        internal static string SessionId { get { return SessionState.GetString(KSession, ""); } }
        internal static string Owner { get { return SessionState.GetString(KOwner, ""); } }

        static LiveBridge()
        {
            if (AssetDatabase.IsAssetImportWorkerProcess()) return;   // import workers run [InitializeOnLoad] code too
            if (Application.isBatchMode) return;                      // never in batch mode: no switch turns this on
            Activate();
        }

        static void Activate()
        {
            if (activated || AssetDatabase.IsAssetImportWorkerProcess()) return;
            activated = true;
            BootId = SessionState.GetString(KBoot, "");
            if (BootId == "")
            {
                BootId = Guid.NewGuid().ToString("N");
                SessionState.SetString(KBoot, BootId);
            }
            Generation = SessionState.GetInt(KGeneration, 0) + 1;
            SessionState.SetInt(KGeneration, Generation);
            try
            {
                var me = System.Diagnostics.Process.GetCurrentProcess();
                pid = me.Id;
                startedUtc = me.StartTime.ToUniversalTime().ToString("o");
            }
            catch { }
            EditorApplication.update += Tick;
            AssemblyReloadEvents.beforeAssemblyReload += () => { reloading = true; Ipc.Event("before-assembly-reload"); Heartbeat(true); };
            CompilationPipeline.compilationStarted += _ => Ipc.Event("compilation-started");
            CompilationPipeline.compilationFinished += _ => Ipc.Event("compilation-finished");
            EditorApplication.playModeStateChanged += s => { Ipc.Event("playmode:" + s); Commands.TransitionEvent(s.ToString()); };
            EditorApplication.pauseStateChanged += s => Ipc.Event("pause:" + s);
            EditorApplication.focusChanged += f => Ipc.Event("focus:" + f);
            EditorApplication.quitting += () =>
            {
                quitting = true;
                Ipc.Event("quitting");
                if (started) { PublishBridge("CLOSED"); Heartbeat(true); }   // opportunistic: a killed Editor never runs this
            };
        }

        static void Tick()
        {
            if (dormant) return;
            if (!started && !Start()) { dormant = true; return; }
            Heartbeat(false);
            Commands.AdvancePending();
            Ipc.Serve(Commands.Handle);
            double now = EditorApplication.timeSinceStartup;
            if (now - lastRetention > RetentionEvery) { lastRetention = now; Ipc.Retention(); }
            if (ApprovalWindow.Open != null && now - lastRepaint > 0.5) { lastRepaint = now; ApprovalWindow.Open.Repaint(); }
        }

        static bool Start()
        {
            try
            {
                Place = Identity.Locate();
                if (Place == null || !Ipc.Init(Place.LiveDir))
                {
                    Debug.LogWarning("[GPOS] The live bridge is dormant: this Unity project is not inside a GPOS project, " +
                                     "or its runtime folder is a link.");
                    return false;
                }
                PackageDigest = Identity.SelfDigest(Place.ProjectPath);
                started = true;
                Ipc.Event(Generation == 1 ? "bridge-boot" : "bridge-domain-reloaded");
                Pending p = Commands.LoadPending();
                Ipc.RecoverClaimed(p == null ? "" : p.RequestId);
                PublishBridge("READY");
                return true;
            }
            catch (Exception e)
            {
                Debug.LogWarning("[GPOS] The live bridge is dormant: " + e.GetType().Name);
                return false;
            }
        }

        internal static EditorFlags Flags()
        {
            return new EditorFlags {
                Quitting = quitting, Reloading = reloading, Compiling = EditorApplication.isCompiling,
                Updating = EditorApplication.isUpdating, Playing = EditorApplication.isPlaying,
                WillChange = EditorApplication.isPlayingOrWillChangePlaymode, Paused = EditorApplication.isPaused };
        }

        internal static Dictionary<string, object> State()
        {
            return new Dictionary<string, object> {
                { "phase", Transitions.Phase(Flags()) }, { "focused", EditorApplication.isFocused },
                { "compiling", EditorApplication.isCompiling }, { "updating", EditorApplication.isUpdating },
                { "playing", EditorApplication.isPlaying }, { "paused", EditorApplication.isPaused },
                { "compilation_failed", EditorUtility.scriptCompilationFailed },
                { "pending_transition", SessionState.GetString(KPending, "") != "" } };
        }

        internal static Dictionary<string, object> Identification()
        {
            return new Dictionary<string, object> {
                { "protocol", Protocol.Name }, { "bridge_version", Protocol.BridgeVersion }, { "package_digest", PackageDigest },
                { "boot_id", BootId }, { "generation", Generation }, { "editor_pid", pid }, { "editor_started_utc", startedUtc },
                { "editor_version", Application.unityVersion }, { "gpos_root", Place.GposRoot },
                { "project_path", Place.ProjectPath }, { "project_rel", Place.Rel }, { "project_key", Place.Key } };
        }

        internal static void PublishBridge(string state)
        {
            try
            {
                var b = Identification();
                b["state"] = state;
                b["session_id"] = SessionId;
                b["owner"] = Owner;
                b["utc"] = DateTime.UtcNow.ToString("o");
                Ipc.AtomicReplace(Path.Combine(Place.LiveDir, "bridge.json"), Json.Write(b));
            }
            catch (Exception e) { Debug.LogWarning("[GPOS] live bridge state could not be published: " + e.GetType().Name); }
        }

        internal static void WriteSession(string state, string sessionId, string owner, string proposal)
        {
            var s = Identification();
            s["state"] = state;
            s["session_id"] = sessionId;
            s["owner"] = owner;
            s["proposal_id"] = proposal;
            s["utc"] = DateTime.UtcNow.ToString("o");
            Ipc.AtomicReplace(Path.Combine(Place.LiveDir, "session.json"), Json.Write(s));
        }

        internal static void Heartbeat(bool force)
        {
            if (!started) return;
            double now = EditorApplication.timeSinceStartup;
            if (!force && now - lastBeat < BeatEvery) return;
            lastBeat = now;
            seq++;
            try
            {
                Ipc.AtomicReplace(Path.Combine(Place.LiveDir, "heartbeat.json"), Json.Write(new Dictionary<string, object> {
                    { "boot_id", BootId }, { "generation", Generation }, { "seq", seq }, { "utc", DateTime.UtcNow.ToString("o") },
                    { "editor_pid", pid }, { "session_id", SessionId }, { "state", State() } }));
            }
            catch { }
        }
    }
}
