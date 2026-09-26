// GPOS live bridge — the per-boot request journal (Unity-free core).
// At-most-once for one immutable request: an entry is kept until the request's start deadline has passed, so a
// replay of the same request is either still in the journal (refused as a duplicate) or past its deadline
// (refused as expired by the protocol), whatever has happened to old response files meanwhile. An unexpired
// entry is never evicted: a full journal refuses new requests instead.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace Gpos.LiveBridge
{
    internal sealed class Journal
    {
        public const int Capacity = 1024;
        public const int RateWindowSeconds = 10;
        public const int RateLimit = 64;
        public const string Claimed = "CLAIMED", Done = "DONE", Interrupted = "INTERRUPTED";

        sealed class Entry
        {
            public string Id, State;
            public long Issued, Deadline;
        }

        readonly List<Entry> entries = new List<Entry>();

        public int Count { get { return entries.Count; } }

        public static Journal Load(string text)
        {
            var j = new Journal();
            if (string.IsNullOrEmpty(text)) return j;
            var list = Json.Parse(text) as List<object>;
            if (list == null) throw new JsonProblem("journal is not a list");
            foreach (var item in list)
            {
                var row = item as List<object>;
                if (row == null || row.Count != 4) throw new JsonProblem("journal row");
                j.entries.Add(new Entry {
                    Id = (string)row[0],
                    Issued = long.Parse((string)row[1], CultureInfo.InvariantCulture),
                    Deadline = long.Parse((string)row[2], CultureInfo.InvariantCulture),
                    State = (string)row[3] });
            }
            return j;
        }

        public string Save()
        {
            return Json.Write(entries.Select(e => (object)new List<object> {
                e.Id, e.Issued.ToString(CultureInfo.InvariantCulture), e.Deadline.ToString(CultureInfo.InvariantCulture), e.State }).ToList());
        }

        // Admit a request that has already passed the protocol checks. Returns a refusal code, or null when the
        // request is recorded as CLAIMED and may be executed exactly once.
        public string Admit(Request r, long nowTicks)
        {
            entries.RemoveAll(e => e.Deadline < nowTicks);   // their replays are expired whatever the journal says
            if (entries.Any(e => e.Id == r.Id)) return "DUPLICATE_REQUEST";
            long window = nowTicks - TimeSpan.FromSeconds(RateWindowSeconds).Ticks;
            if (entries.Count(e => e.Issued >= window) >= RateLimit) return "RATE_LIMITED";
            if (entries.Count >= Capacity) return "JOURNAL_FULL";
            entries.Add(new Entry { Id = r.Id, Issued = r.IssuedTicks, Deadline = r.DeadlineTicks, State = Claimed });
            return null;
        }

        public void Mark(string id, string state)
        {
            foreach (var e in entries) if (e.Id == id) e.State = state;
        }

        public string StateOf(string id)
        {
            var e = entries.FirstOrDefault(x => x.Id == id);
            return e == null ? null : e.State;
        }
    }
}
