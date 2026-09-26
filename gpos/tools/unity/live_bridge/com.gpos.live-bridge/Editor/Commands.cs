// GPOS live bridge — the closed command set. Each command reads or changes only what its name says:
// status, attach proposals and their Human approval, bind/unbind of one session, stale-session recovery grants,
// bounded inspection, and the four Play Mode transitions. Nothing here evaluates code, calls a method by name,
// runs a menu item or reads an arbitrary object property.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Gpos.LiveBridge
{
    internal static class Commands
    {
        static long Now { get { return DateTime.UtcNow.Ticks; } }

        internal static ProposalStore LoadProposals() { return ProposalStore.Load(SessionState.GetString(LiveBridge.KProposals, "")); }

        internal static void SaveProposals(ProposalStore store) { SessionState.SetString(LiveBridge.KProposals, store.Save()); }

        internal static Pending LoadPending() { return Pending.Load(SessionState.GetString(LiveBridge.KPending, "")); }

        internal static void TransitionEvent(string e)
        {
            var p = LoadPending();
            if (p == null) return;
            p.Events.Add(e);
            SessionState.SetString(LiveBridge.KPending, p.Save());
        }

        static void Mark(string id, string state)
        {
            var j = Journal.Load(SessionState.GetString(LiveBridge.KJournal, ""));
            j.Mark(id, state);
            SessionState.SetString(LiveBridge.KJournal, j.Save());
        }

        static void Answer(string id, string status, string code, string message, Dictionary<string, object> data)
        {
            Ipc.Respond(id, status, code, message, data);
            Mark(id, Journal.Done);
        }

        public static void Handle(string id, byte[] bytes)
        {
            Request r;
            try
            {
                string text = Ipc.Text(bytes);
                if (text == null) throw new Refusal("MALFORMED_REQUEST", "the request is not UTF-8");
                r = Protocol.Parse(id, text, Now, LiveBridge.BootId);
                var journal = Journal.Load(SessionState.GetString(LiveBridge.KJournal, ""));
                string admitted = journal.Admit(r, Now);
                if (admitted != null) throw new Refusal(admitted, "the request was not admitted: " + admitted);
                SessionState.SetString(LiveBridge.KJournal, journal.Save());
            }
            catch (Refusal refusal)
            {
                Ipc.Respond(id, "REFUSED", refusal.Code, refusal.Message, null);
                return;
            }
            try { Dispatch(r); }
            catch (Refusal refusal) { Answer(id, "REFUSED", refusal.Code, refusal.Message, null); }
            catch (Exception e) { Answer(id, "FAILED", "BRIDGE_INTERNAL_ERROR", e.GetType().Name, null); }
        }

        static void Dispatch(Request r)
        {
            if (Protocol.NeedsSession(r.Command))
            {
                string bound = LiveBridge.SessionId;
                if (bound == "") throw new Refusal("SESSION_NOT_BOUND", "no GPOS session is attached to this Editor");
                if (r.SessionId != bound) throw new Refusal("SESSION_MISMATCH", "the request is not bound to the attached session");
                if (r.Owner != LiveBridge.Owner) throw new Refusal("OWNER_MISMATCH", "the request's owner is not the session owner");
                if (r.IssuedTicks < long.Parse(SessionState.GetString(LiveBridge.KBound, "0")))
                    throw new Refusal("SESSION_MISMATCH", "the request was issued before this session was bound");
            }
            if (Protocol.ChangesEditorState(r.Command))
            {
                string busy = Transitions.Busy(Transitions.Phase(LiveBridge.Flags()), LoadPending() != null);
                if (busy != null) throw new Refusal("EDITOR_BUSY", busy);
            }
            switch (r.Command)
            {
                case "status": Answer(r.Id, "OK", null, null, Status()); return;
                case "propose-attach": ProposeAttach(r); return;
                case "attach-status":
                case "recovery-status": ProposalStatus(r); return;
                case "abandon-proposal": Abandon(r); return;
                case "bind": Bind(r); return;
                case "propose-recovery": ProposeRecovery(r); return;
                case "consume-recovery": ConsumeRecovery(r); return;
                case "unbind": Unbind(r); return;
                case "inspect": Answer(r.Id, "OK", null, null, Inspect()); return;
                case "enter-playmode":
                case "exit-playmode": Transition(r); return;
                case "pause":
                case "resume": Pause(r); return;
            }
            throw new Refusal("UNKNOWN_COMMAND", "the command is not in the closed allowlist");
        }

        static Dictionary<string, object> Status()
        {
            var s = LiveBridge.Identification();
            s["session_id"] = LiveBridge.SessionId;
            s["owner"] = LiveBridge.Owner;
            s["state"] = LiveBridge.State();
            var store = LoadProposals();
            s["pending_proposals"] = store.All.Count(p => ProposalStore.Effective(p, Now) == "PENDING");
            return s;
        }

        // ------------------------------------------------------------ attach

        static void ProposeAttach(Request r)
        {
            string prop = Protocol.Hex(r.Args, "proposal_id"), sid = Protocol.Hex(r.Args, "session_id");
            if (Protocol.Key(r.Args, "project_key") != LiveBridge.Place.Key) throw new Refusal("PROJECT_MISMATCH", "the proposal names another Unity project");
            int expires = Protocol.Int(r.Args, "expires_s", ProposalStore.MinExpiry, ProposalStore.MaxExpiry);
            if (LiveBridge.SessionId != "") throw new Refusal("SESSION_ALREADY_BOUND", "a session is attached to this Editor");
            var store = LoadProposals();
            string problem = store.ProposeAttach(prop, sid, r.Owner, LiveBridge.BootId, LiveBridge.Place.Key, expires, Now);
            if (problem != null) throw new Refusal(problem, "the proposal was not accepted");
            SaveProposals(store);
            Ipc.Event("proposal-attach:" + prop);
            Debug.Log("[GPOS] A live-session attach request is waiting for your approval: GPOS > Live Session");
            Answer(r.Id, "OK", "PENDING_HUMAN_APPROVAL", null, View(store.Find(prop)));
        }

        static Dictionary<string, object> View(Proposal p)
        {
            long now = Now;
            string state = ProposalStore.Effective(p, now);
            return new Dictionary<string, object> {
                { "proposal_id", p.Id }, { "kind", p.Kind }, { "state", state }, { "owner", p.Owner },
                { "grant_seconds_left", state == "APPROVED" ? Math.Max(0, (p.GrantExpires - now) / TimeSpan.TicksPerSecond) : 0 } };
        }

        static void ProposalStatus(Request r)
        {
            string prop = Protocol.Hex(r.Args, "proposal_id");
            var p = LoadProposals().Find(prop);
            if (p == null) throw new Refusal("NO_SUCH_PROPOSAL", "no proposal with this id in this Editor session");
            if (p.Owner != r.Owner) throw new Refusal("OWNER_MISMATCH", "the proposal belongs to another owner");
            if (p.Kind != (r.Command == "attach-status" ? ProposalStore.Attach : ProposalStore.Recover))
                throw new Refusal("WRONG_KIND", "the proposal is of the other kind");
            Answer(r.Id, "OK", null, null, View(p));
        }

        static void Abandon(Request r)
        {
            string prop = Protocol.Hex(r.Args, "proposal_id");
            var store = LoadProposals();
            string before = store.Abandon(prop, r.Owner, Now);
            if (before == "NO_SUCH_PROPOSAL" || before == "OWNER_MISMATCH") throw new Refusal(before, "the proposal cannot be abandoned");
            SaveProposals(store);
            Answer(r.Id, "OK", null, null, new Dictionary<string, object> { { "before", before }, { "state", ProposalStore.Effective(store.Find(prop), Now) } });
        }

        static void Bind(Request r)
        {
            string prop = Protocol.Hex(r.Args, "proposal_id"), sid = Protocol.Hex(r.Args, "session_id");
            var store = LoadProposals();
            string problem = store.GrantProblem(prop, ProposalStore.Attach, r.Owner, LiveBridge.BootId, LiveBridge.Place.Key, Now);
            if (problem != null) throw new Refusal(problem, "the attach grant cannot be used: " + problem);
            if (store.Find(prop).SessionId != sid) throw new Refusal("SESSION_MISMATCH", "the session differs from the approved proposal");
            if (LiveBridge.SessionId != "") throw new Refusal("SESSION_ALREADY_BOUND", "a session is attached to this Editor");
            string lease = LeaseProblem(sid, prop, r.Owner);
            if (lease != null) throw new Refusal("LEASE_NOT_HELD", lease);
            store.Consume(prop);   // one use: consumed before the session is bound
            SaveProposals(store);
            SessionState.SetString(LiveBridge.KSession, sid);
            SessionState.SetString(LiveBridge.KOwner, r.Owner);
            SessionState.SetString(LiveBridge.KBound, Now.ToString());
            SessionState.SetString(LiveBridge.KProposal, prop);
            LiveBridge.WriteSession("ATTACHED", sid, r.Owner, prop);
            LiveBridge.PublishBridge("READY");
            LiveBridge.Heartbeat(true);   // the binding is visible at once, not at the next beat
            Ipc.Event("bound:" + sid);
            Answer(r.Id, "OK", "ATTACHED", null, Status());
        }

        static void Unbind(Request r)
        {
            string sid = LiveBridge.SessionId, owner = LiveBridge.Owner, prop = SessionState.GetString(LiveBridge.KProposal, "");
            SessionState.EraseString(LiveBridge.KSession);
            SessionState.EraseString(LiveBridge.KOwner);
            SessionState.EraseString(LiveBridge.KBound);
            SessionState.EraseString(LiveBridge.KProposal);
            LiveBridge.WriteSession("DETACHED", sid, owner, prop);
            LiveBridge.PublishBridge("READY");
            LiveBridge.Heartbeat(true);
            Ipc.Event("unbound:" + sid);
            Answer(r.Id, "OK", "DETACHED", null, null);
        }

        // ------------------------------------------------------------ the GPOS lease (read only)

        static Dictionary<string, object> ReadLease()
        {
            string resource = "EDITOR_PROJECT:" + LiveBridge.Place.GposRoot;
            string key = Identity.Sha256(Encoding.UTF8.GetBytes("unity\0" + resource)).Substring(0, 32);
            string path = Path.Combine(LiveBridge.Place.GposRoot, ".game", "gpos-runtime", "leases", key + ".json");
            if (!File.Exists(path) || Identity.IsLink(path) || new FileInfo(path).Length > Protocol.MaxRequestBytes) return null;
            var lease = Json.Parse(File.ReadAllText(path)) as Dictionary<string, object>;
            if (lease == null || lease.ContainsKey("resource_id") && (lease["resource_id"] as string) != resource) return null;
            return lease;
        }

        static string Field(Dictionary<string, object> d, string key)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) ? v as string : null;
        }

        // Why the GPOS SESSION lease does not bind exactly this session, proposal, owner, boot and project, or null.
        static string LeaseProblem(string sid, string prop, string owner)
        {
            var lease = ReadLease();
            if (lease == null) return "no readable GPOS lease is held on this project";
            object sessionValue;
            var session = lease.TryGetValue("session", out sessionValue) ? sessionValue as Dictionary<string, object> : null;
            if (Field(lease, "adapter_id") != "unity" || Field(lease, "scope") != "SESSION" || session == null) return "the lease is not a unity SESSION lease";
            if (Field(lease, "owner_id") != owner) return "the lease belongs to another owner";
            if (Field(session, "session_id") != sid || Field(session, "proposal_id") != prop) return "the lease binds another session or proposal";
            if (Field(session, "boot_id") != LiveBridge.BootId || Field(session, "project_key") != LiveBridge.Place.Key)
                return "the lease binds another Editor boot or project";
            return null;
        }

        // ------------------------------------------------------------ stale-session recovery

        static void ProposeRecovery(Request r)
        {
            string prop = Protocol.Hex(r.Args, "proposal_id"), stale = Protocol.Hex(r.Args, "stale_session_id");
            if (Protocol.Key(r.Args, "project_key") != LiveBridge.Place.Key) throw new Refusal("PROJECT_MISMATCH", "the proposal names another Unity project");
            int expires = Protocol.Int(r.Args, "expires_s", ProposalStore.MinExpiry, ProposalStore.MaxExpiry);
            if (LiveBridge.SessionId != "") throw new Refusal("SESSION_ALREADY_BOUND", "a session is attached to this Editor; it is not stale");
            var lease = ReadLease();
            object sessionValue = null;
            var session = lease != null && lease.TryGetValue("session", out sessionValue) ? sessionValue as Dictionary<string, object> : null;
            if (session == null || Field(lease, "scope") != "SESSION" || Field(session, "session_id") != stale)
                throw new Refusal("NO_STALE_SESSION", "no SESSION lease binds that session on this project");
            var store = LoadProposals();
            string problem = store.ProposeRecovery(prop, r.Owner, LiveBridge.BootId, LiveBridge.Place.Key, stale,
                                                   Field(session, "boot_id"), Field(lease, "owner_id"), expires, Now);
            if (problem != null) throw new Refusal(problem, "the recovery proposal was not accepted");
            SaveProposals(store);
            Ipc.Event("proposal-recover:" + prop);
            Debug.Log("[GPOS] A stale-session recovery request is waiting for your approval: GPOS > Live Session");
            Answer(r.Id, "OK", "PENDING_HUMAN_APPROVAL", null, View(store.Find(prop)));
        }

        // The grant is consumed here, before GPOS breaks the lease, so one approval can never authorize two breaks.
        static void ConsumeRecovery(Request r)
        {
            string prop = Protocol.Hex(r.Args, "proposal_id");
            var store = LoadProposals();
            string problem = store.GrantProblem(prop, ProposalStore.Recover, r.Owner, LiveBridge.BootId, LiveBridge.Place.Key, Now);
            if (problem != null) throw new Refusal(problem, "the recovery grant cannot be used: " + problem);
            var p = store.Find(prop);
            store.Consume(prop);
            SaveProposals(store);
            Ipc.Event("recovery-granted:" + prop);
            Answer(r.Id, "OK", "RECOVERY_GRANTED", null, new Dictionary<string, object> {
                { "stale_session_id", p.StaleSessionId }, { "stale_boot_id", p.StaleBootId }, { "stale_owner", p.StaleOwner },
                { "approving_boot_id", LiveBridge.BootId } });
        }

        // ------------------------------------------------------------ inspection (bounded, names only)

        static string Clip(string s, int n) { return s == null ? "" : s.Length > n ? s.Substring(0, n) : s; }

        static Dictionary<string, object> Inspect()
        {
            var scenes = new List<object>();
            var active = SceneManager.GetActiveScene();
            bool truncatedScenes = SceneManager.sceneCount > 16, truncatedRoots = false;
            for (int i = 0; i < SceneManager.sceneCount && i < 16; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                var roots = new List<object>();
                if (scene.isLoaded)
                {
                    var all = scene.GetRootGameObjects();
                    truncatedRoots |= all.Length > 100;
                    for (int k = 0; k < all.Length && k < 100; k++)
                        roots.Add(new Dictionary<string, object> {
                            { "index", k }, { "name", Clip(all[k].name, 128) }, { "active", all[k].activeSelf },
                            { "child_count", all[k].transform.childCount } });
                }
                scenes.Add(new Dictionary<string, object> {
                    { "name", Clip(scene.name, 128) }, { "path", Clip(scene.path, 512) }, { "loaded", scene.isLoaded },
                    { "dirty", scene.isDirty }, { "active", scene == active }, { "root_count", scene.isLoaded ? scene.rootCount : 0 },
                    { "roots", roots } });
            }
            return new Dictionary<string, object> {
                { "bridge", new Dictionary<string, object> { { "protocol", Protocol.Name }, { "bridge_version", Protocol.BridgeVersion }, { "boot_id", LiveBridge.BootId } } },
                { "session", new Dictionary<string, object> { { "session_id", LiveBridge.SessionId }, { "owner", LiveBridge.Owner } } },
                { "editor", LiveBridge.State() }, { "editor_version", Application.unityVersion },
                { "active_scene", Clip(active.name, 128) }, { "active_scene_path", Clip(active.path, 512) },
                { "scene_count", SceneManager.sceneCount }, { "scenes", scenes },
                { "truncated", new Dictionary<string, object> { { "scenes", truncatedScenes }, { "roots", truncatedRoots } } } };
        }

        // ------------------------------------------------------------ Play Mode (Editor state only)

        static void Transition(Request r)
        {
            int timeout = Protocol.Int(r.Args, "timeout_s", 5, 600);
            string refusal = Transitions.Precondition(r.Command, Transitions.Phase(LiveBridge.Flags()));
            if (refusal != null) throw new Refusal(refusal, "the Editor is not in the state this transition starts from");
            var p = new Pending { RequestId = r.Id, Op = r.Command == "enter-playmode" ? "enter" : "exit", Issued = Now,
                                  Deadline = Now + TimeSpan.FromSeconds(timeout).Ticks };
            SessionState.SetString(LiveBridge.KPending, p.Save());   // recorded before it is issued, issued exactly once
            Ipc.Event(p.Op + "-playmode-issued:" + r.Id);
            if (p.Op == "enter") EditorApplication.EnterPlaymode();
            else EditorApplication.ExitPlaymode();
        }

        static void Pause(Request r)
        {
            string refusal = Transitions.Precondition(r.Command, Transitions.Phase(LiveBridge.Flags()));
            if (refusal != null) throw new Refusal(refusal, "the Editor is not in the state this command starts from");
            EditorApplication.isPaused = r.Command == "pause";
            Answer(r.Id, "OK", null, null, new Dictionary<string, object> { { "state", LiveBridge.State() } });
        }

        // Observes the one pending transition; completes it once, never re-issues it.
        public static void AdvancePending()
        {
            var p = LoadPending();
            if (p == null) return;
            var done = Transitions.Evaluate(p, EditorApplication.isPlaying, EditorApplication.isPlayingOrWillChangePlaymode, Now);
            if (done == null) return;
            SessionState.EraseString(LiveBridge.KPending);
            Answer(p.RequestId, done[0], done[1], done[1] == null ? null : "the transition did not complete as requested; it is not retried",
                   new Dictionary<string, object> { { "transitions", string.Join(",", p.Events) }, { "state", LiveBridge.State() } });
        }
    }

    // The Human's decision, made only from the approval window's buttons.
    internal static class Approvals
    {
        internal static bool Decide(string proposalId, bool approve)
        {
            var store = Commands.LoadProposals();
            bool decided = store.Decide(proposalId, approve, LiveBridge.BootId, DateTime.UtcNow.Ticks);
            if (!decided) return false;
            Commands.SaveProposals(store);
            Ipc.Event((approve ? "human-approved:" : "human-rejected:") + proposalId);
            return true;
        }
    }
}
