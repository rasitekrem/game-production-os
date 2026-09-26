// GPOS live bridge — attach and stale-recovery proposals and their Human approval grants (Unity-free core).
// Two distinct kinds with distinct state machines; a grant of one kind never serves the other.
//   PENDING -> APPROVED (Human, in the Editor) | REJECTED (Human) | EXPIRED (time)
//   APPROVED -> CONSUMED (used once) | ABANDONED (GPOS gave up) | GRANT_EXPIRED (time)
// Only Decide() moves PENDING to APPROVED, and only the approval window calls it. Proposals live in the Editor's
// SessionState: nothing survives an Editor restart.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace Gpos.LiveBridge
{
    internal sealed class Proposal
    {
        public string Id, Kind, SessionId, Owner, BootId, ProjectKey, State;
        public string StaleSessionId, StaleBootId, StaleOwner;
        public long Created, Expires, GrantExpires;
    }

    internal sealed class ProposalStore
    {
        public const string Attach = "ATTACH", Recover = "RECOVER";
        public const int MaxPending = 4, MaxHistory = 16, GrantSeconds = 60, MinExpiry = 30, MaxExpiry = 900;

        readonly List<Proposal> items = new List<Proposal>();

        public IEnumerable<Proposal> All { get { return items; } }

        public static ProposalStore Load(string text)
        {
            var store = new ProposalStore();
            if (string.IsNullOrEmpty(text)) return store;
            var list = Json.Parse(text) as List<object>;
            if (list == null) throw new JsonProblem("proposals are not a list");
            foreach (var item in list)
            {
                var d = item as Dictionary<string, object>;
                if (d == null) throw new JsonProblem("proposal");
                store.items.Add(new Proposal {
                    Id = S(d, "id"), Kind = S(d, "kind"), SessionId = S(d, "session_id"), Owner = S(d, "owner"),
                    BootId = S(d, "boot_id"), ProjectKey = S(d, "project_key"), State = S(d, "state"),
                    StaleSessionId = S(d, "stale_session_id"), StaleBootId = S(d, "stale_boot_id"),
                    StaleOwner = S(d, "stale_owner"), Created = L(d, "created"), Expires = L(d, "expires"),
                    GrantExpires = L(d, "grant_expires") });
            }
            return store;
        }

        static string S(Dictionary<string, object> d, string k) { object v; return d.TryGetValue(k, out v) ? v as string : null; }

        static long L(Dictionary<string, object> d, string k) { return long.Parse(S(d, k) ?? "0", CultureInfo.InvariantCulture); }

        public string Save()
        {
            return Json.Write(items.Select(p => (object)new Dictionary<string, object> {
                { "id", p.Id }, { "kind", p.Kind }, { "session_id", p.SessionId }, { "owner", p.Owner }, { "boot_id", p.BootId },
                { "project_key", p.ProjectKey }, { "state", p.State }, { "stale_session_id", p.StaleSessionId },
                { "stale_boot_id", p.StaleBootId }, { "stale_owner", p.StaleOwner },
                { "created", p.Created.ToString(CultureInfo.InvariantCulture) }, { "expires", p.Expires.ToString(CultureInfo.InvariantCulture) },
                { "grant_expires", p.GrantExpires.ToString(CultureInfo.InvariantCulture) } }).ToList());
        }

        public static string Effective(Proposal p, long now)
        {
            if (p.State == "PENDING" && now > p.Expires) return "EXPIRED";
            if (p.State == "APPROVED" && now > p.GrantExpires) return "GRANT_EXPIRED";
            return p.State;
        }

        public Proposal Find(string id) { return items.FirstOrDefault(p => p.Id == id); }

        string Admit(Proposal p, int expiresSeconds, long now)
        {
            if (expiresSeconds < MinExpiry || expiresSeconds > MaxExpiry) return "BAD_ARGUMENTS";
            if (Find(p.Id) != null) return "DUPLICATE_PROPOSAL";
            if (items.Count(x => Effective(x, now) == "PENDING") >= MaxPending) return "TOO_MANY_PROPOSALS";
            p.State = "PENDING";
            p.Created = now;
            p.Expires = now + TimeSpan.FromSeconds(expiresSeconds).Ticks;
            items.Add(p);
            while (items.Count > MaxHistory)
            {
                var old = items.FirstOrDefault(x => Effective(x, now) != "PENDING" && Effective(x, now) != "APPROVED");
                if (old == null) break;
                items.Remove(old);
            }
            return null;
        }

        public string ProposeAttach(string id, string sessionId, string owner, string bootId, string projectKey, int expiresSeconds, long now)
        {
            return Admit(new Proposal { Id = id, Kind = Attach, SessionId = sessionId, Owner = owner, BootId = bootId, ProjectKey = projectKey },
                         expiresSeconds, now);
        }

        public string ProposeRecovery(string id, string owner, string bootId, string projectKey, string staleSessionId,
                                      string staleBootId, string staleOwner, int expiresSeconds, long now)
        {
            return Admit(new Proposal { Id = id, Kind = Recover, Owner = owner, BootId = bootId, ProjectKey = projectKey,
                                        StaleSessionId = staleSessionId, StaleBootId = staleBootId, StaleOwner = staleOwner },
                         expiresSeconds, now);
        }

        // The Human's decision. Only a PENDING, unexpired proposal of this boot can be decided.
        public bool Decide(string id, bool approve, string bootId, long now)
        {
            var p = Find(id);
            if (p == null || p.BootId != bootId || Effective(p, now) != "PENDING") return false;
            p.State = approve ? "APPROVED" : "REJECTED";
            p.GrantExpires = approve ? now + TimeSpan.FromSeconds(GrantSeconds).Ticks : 0;
            return true;
        }

        // Why this proposal's grant cannot be used now, or null when it can. Changes nothing.
        public string GrantProblem(string id, string kind, string owner, string bootId, string projectKey, long now)
        {
            var p = Find(id);
            if (p == null) return "NO_SUCH_PROPOSAL";
            if (p.Kind != kind) return "WRONG_KIND";
            if (p.Owner != owner) return "OWNER_MISMATCH";
            if (p.BootId != bootId) return "BOOT_MISMATCH";
            if (p.ProjectKey != projectKey) return "PROJECT_MISMATCH";
            string state = Effective(p, now);
            if (state == "PENDING") return "NOT_APPROVED";
            if (state == "REJECTED") return "REJECTED";
            if (state == "EXPIRED" || state == "GRANT_EXPIRED") return "GRANT_EXPIRED";
            if (state == "CONSUMED" || state == "ABANDONED") return "GRANT_CONSUMED";
            return state == "APPROVED" ? null : "NOT_APPROVED";
        }

        public void Consume(string id)
        {
            var p = Find(id);
            if (p == null || p.State != "APPROVED") throw new InvalidOperationException("only an approved grant is consumed");
            p.State = "CONSUMED";
        }

        // GPOS stops waiting: a pending or approved proposal can never be used afterwards.
        public string Abandon(string id, string owner, long now)
        {
            var p = Find(id);
            if (p == null) return "NO_SUCH_PROPOSAL";
            if (p.Owner != owner) return "OWNER_MISMATCH";
            string before = Effective(p, now);
            if (p.State == "PENDING" || p.State == "APPROVED") p.State = "ABANDONED";
            return before;
        }
    }
}
