// TEST-ONLY: tests of the GPOS live bridge's Unity-free core (Json, Protocol, Journal, Proposals, Transitions).
// Compiled together with gpos/tools/unity/live_bridge/.../Editor/Core/*.cs by tests/test_unity_live_bridge_core.py
// using the Mono compiler bundled with the installed Unity Editor; no Unity process runs. Prints one line per test
// ("PASS name" / "FAIL name: why") and exits non-zero when any test fails.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;

namespace Gpos.LiveBridge
{
    static partial class CoreTests
    {
        const string Boot = "b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0";
        const string Sid = "5e55105e55105e55105e55105e551055";
        static readonly long T0 = new DateTime(2026, 9, 26, 12, 0, 0, DateTimeKind.Utc).Ticks;

        static void Check(bool condition, string what) { if (!condition) throw new Exception(what); }

        static void Equal(object expected, object actual, string what)
        {
            if (!Equals(expected, actual)) throw new Exception(what + ": expected <" + expected + "> got <" + actual + ">");
        }

        static string Code(Action a)
        {
            try { a(); return null; }
            catch (Refusal r) { return r.Code; }
        }

        static string Utc(long ticks) { return new DateTime(ticks, DateTimeKind.Utc).ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ"); }

        static long S(double seconds) { return TimeSpan.FromSeconds(seconds).Ticks; }

        static string Req(string id, string command = "status", string args = "{}", string session = null, string owner = "AGENT:w1",
                          string boot = Boot, long? issued = null, double window = 30, string extra = "", string schema = Protocol.RequestSchema)
        {
            long i = issued ?? T0;
            return "{\"schema\":\"" + schema + "\",\"request_id\":\"" + id + "\",\"session_id\":" + (session == null ? "null" : "\"" + session + "\"") +
                   ",\"owner\":\"" + owner + "\",\"boot_id\":\"" + boot + "\",\"command\":\"" + command + "\",\"args\":" + args +
                   ",\"issued_utc\":\"" + Utc(i) + "\",\"start_deadline_utc\":\"" + Utc(i + S(window)) + "\"" + extra + "}";
        }

        static string Id(int n) { return n.ToString("x32"); }

        static Request Parse(string id, string text, long now = 0) { return Protocol.Parse(id, text, now == 0 ? T0 : now, Boot); }

        // ------------------------------------------------------------ Json

        static void JsonParsesAndRefuses()
        {
            var d = (Dictionary<string, object>)Json.Parse("{\"a\":[1,2.5,true,null,\"x\\u0041\"],\"b\":{}}");
            Equal(5, ((List<object>)d["a"]).Count, "items");
            Equal("xA", ((List<object>)d["a"])[4], "escape");
            foreach (var bad in new[] { "{\"a\":1,\"a\":2}", "{} x", "{\"a\":NaN}", "[1,]", "{\"a\":\"\\q\"}", "\"\u0001\"", "{'a':1}", "" })
            {
                bool refused = false;
                try { Json.Parse(bad); } catch (JsonProblem) { refused = true; }
                Check(refused, "refused: " + bad);
            }
            string deep = new string('[', 10) + new string(']', 10);
            bool deepRefused = false;
            try { Json.Parse(deep); } catch (JsonProblem) { deepRefused = true; }
            Check(deepRefused, "depth bound");
            bool wide = false;
            try { Json.Parse("[" + string.Join(",", Enumerable.Repeat("1", 300)) + "]"); } catch (JsonProblem) { wide = true; }
            Check(wide, "item bound");
        }

        // ------------------------------------------------------------ Protocol

        static void ProtocolAcceptsAValidRequest()
        {
            var r = Parse(Id(1), Req(Id(1)));
            Equal("status", r.Command, "command");
            Equal("AGENT:w1", r.Owner, "owner");
            var s = Parse(Id(2), Req(Id(2), "pause", "{}", Sid));
            Equal(Sid, s.SessionId, "session");
        }

        static void ProtocolRefusalsHaveStableCodes()
        {
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), "{\"schema\":1")), "garbled");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), extra: ",\"x\":1"))), "extra key");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1)).Replace(",\"owner\":\"AGENT:w1\"", ""))), "missing key");
            Equal("UNSUPPORTED_SCHEMA", Code(() => Parse(Id(1), Req(Id(1), schema: "gpos.unity.live.request/0"))), "schema");
            Equal("REQUEST_ID_MISMATCH", Code(() => Parse(Id(2), Req(Id(1)))), "file name binding");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), owner: "w1"))), "owner without kind");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), owner: "agent:w1"))), "lower-case kind");
            Equal("BOOT_MISMATCH", Code(() => Parse(Id(1), Req(Id(1), boot: Id(9)))), "boot binding");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), window: 121))), "window bound");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), issued: T0 + S(60)))), "issued in the future");
            Equal("LIVE_REQUEST_EXPIRED", Code(() => Parse(Id(1), Req(Id(1), window: 5), T0 + S(6))), "expired");
            Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(1), Req(Id(1), "execute-method"))), "closed allowlist");
            Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(1), Req(Id(1), "approve-attach", "{\"proposal_id\":\"" + Id(3) + "\"}"))), "no approve command");
            Equal("BAD_ARGUMENTS", Code(() => Parse(Id(1), Req(Id(1), "status", "{\"depth\":3}"))), "extra argument");
            Equal("BAD_ARGUMENTS", Code(() => Parse(Id(1), Req(Id(1), "attach-status", "{}"))), "missing argument");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), "pause"))), "session required");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), "status", "{}", Sid))), "no session for status");
            Check(!Protocol.Commands.Any(c => c.Contains("method") || c.Contains("menu") || c.Contains("eval") || c.Contains("approve")),
                  "no generic command");
        }

        // ------------------------------------------------------------ Journal

        static void JournalIsAtMostOncePerImmutableRequest()
        {
            var j = new Journal();
            var r = Parse(Id(1), Req(Id(1), window: 30));
            Equal(null, j.Admit(r, T0), "first admission");
            Equal("DUPLICATE_REQUEST", j.Admit(r, T0 + S(10)), "replay within the deadline");
            // After the deadline the entry may be purged — and the exact replay is refused as expired by the protocol.
            Equal("LIVE_REQUEST_EXPIRED", Code(() => Parse(Id(1), Req(Id(1), window: 30), T0 + S(31))), "replay after the deadline");
            var reloaded = Journal.Load(j.Save());
            Equal("DUPLICATE_REQUEST", reloaded.Admit(r, T0 + S(20)), "survives save/load (Domain Reload)");
            reloaded.Mark(Id(1), Journal.Done);
            Equal(Journal.Done, reloaded.StateOf(Id(1)), "state");
        }

        static void JournalBoundsNeverEvictUnexpiredEntries()
        {
            var j = new Journal();
            int n = 0;
            string code = null;
            for (; n < Journal.RateLimit + 5; n++)
            {
                code = j.Admit(Parse(Id(100 + n), Req(Id(100 + n), window: 120)), T0);
                if (code != null) break;
            }
            Equal("RATE_LIMITED", code, "rate limit");
            Equal(Journal.RateLimit, n, "admitted before the limit");
            // The capacity exceeds everything the rate limit can admit within the longest start window, so an unexpired
            // entry never has to be evicted; sustained maximum load for longer than a window proves it.
            Check(Journal.RateLimit * (Protocol.MaxStartWindowSeconds / Journal.RateWindowSeconds + 1) <= Journal.Capacity,
                  "capacity covers the longest window at the maximum rate");
            var busy = new Journal();
            int admitted = 0, id = 20000;
            for (int second = 0; second < 3 * Protocol.MaxStartWindowSeconds; second++)
            {
                long now = T0 + S(second);
                for (int k = 0; k < 8; k++, id++)
                {
                    string c = busy.Admit(Parse(Id(id), Req(Id(id), issued: now, window: 120), now), now);
                    if (c == null) admitted++;
                    else Equal("RATE_LIMITED", c, "only the rate limit refuses under load");
                }
                Check(busy.Count <= Journal.Capacity, "never above capacity");
            }
            Check(admitted > Journal.Capacity, "more requests than the capacity were admitted over time");
        }

        // ------------------------------------------------------------ Proposals

        static ProposalStore Store(out string prop)
        {
            var s = new ProposalStore();
            prop = Id(77);
            Equal(null, s.ProposeAttach(prop, Sid, "AGENT:w1", Boot, "0123456789abcdef", 300, T0), "propose");
            return s;
        }

        static void ProposalApprovalIsOneUseAndBound()
        {
            string p;
            var s = Store(out p);
            Equal("NOT_APPROVED", s.GrantProblem(p, ProposalStore.Attach, "AGENT:w1", Boot, "0123456789abcdef", T0), "unapproved");
            Check(!s.Decide(p, true, Id(9), T0), "a decision from another boot is ignored");
            Check(s.Decide(p, true, Boot, T0 + S(1)), "approve");
            Check(!s.Decide(p, false, Boot, T0 + S(2)), "decided once");
            Equal(null, s.GrantProblem(p, ProposalStore.Attach, "AGENT:w1", Boot, "0123456789abcdef", T0 + S(2)), "usable");
            Equal("OWNER_MISMATCH", s.GrantProblem(p, ProposalStore.Attach, "HUMAN:w1", Boot, "0123456789abcdef", T0 + S(2)), "owner kind");
            Equal("BOOT_MISMATCH", s.GrantProblem(p, ProposalStore.Attach, "AGENT:w1", Id(9), "0123456789abcdef", T0 + S(2)), "boot");
            Equal("PROJECT_MISMATCH", s.GrantProblem(p, ProposalStore.Attach, "AGENT:w1", Boot, "fedcba9876543210", T0 + S(2)), "project");
            Equal("WRONG_KIND", s.GrantProblem(p, ProposalStore.Recover, "AGENT:w1", Boot, "0123456789abcdef", T0 + S(2)), "kind");
            Equal("GRANT_EXPIRED", s.GrantProblem(p, ProposalStore.Attach, "AGENT:w1", Boot, "0123456789abcdef", T0 + S(62)), "grant expiry");
            s.Consume(p);
            Equal("GRANT_CONSUMED", s.GrantProblem(p, ProposalStore.Attach, "AGENT:w1", Boot, "0123456789abcdef", T0 + S(3)), "one use");
            bool twice = false;
            try { s.Consume(p); } catch (InvalidOperationException) { twice = true; }
            Check(twice, "cannot consume twice");
            var loaded = ProposalStore.Load(s.Save());
            Equal("CONSUMED", loaded.Find(p).State, "survives save/load");
        }

        static void ProposalLifecycleBounds()
        {
            string p;
            var s = Store(out p);
            Equal("EXPIRED", ProposalStore.Effective(s.Find(p), T0 + S(301)), "proposal expiry");
            Check(!s.Decide(p, true, Boot, T0 + S(301)), "an expired proposal cannot be approved");
            Equal("PENDING", s.Abandon(p, "AGENT:w1", T0), "abandon reports the state before");
            Equal("GRANT_CONSUMED", s.GrantProblem(p, ProposalStore.Attach, "AGENT:w1", Boot, "0123456789abcdef", T0), "abandoned");
            Check(!s.Decide(p, true, Boot, T0), "an abandoned proposal cannot be approved");
            Equal("OWNER_MISMATCH", s.Abandon(p, "AGENT:w2", T0), "only the owner abandons");
            var r = new ProposalStore();
            for (int i = 0; i < ProposalStore.MaxPending; i++)
                Equal(null, r.ProposeAttach(Id(200 + i), Sid, "AGENT:w1", Boot, "0123456789abcdef", 60, T0), "pending " + i);
            Equal("TOO_MANY_PROPOSALS", r.ProposeAttach(Id(299), Sid, "AGENT:w1", Boot, "0123456789abcdef", 60, T0), "pending bound");
            Equal("BAD_ARGUMENTS", r.ProposeAttach(Id(298), Sid, "AGENT:w1", Boot, "0123456789abcdef", 29, T0 + S(61)), "min expiry");
            Equal("DUPLICATE_PROPOSAL", r.ProposeAttach(Id(200), Sid, "AGENT:w1", Boot, "0123456789abcdef", 60, T0 + S(61)), "duplicate");
            for (int i = 0; i < 20; i++) r.ProposeAttach(Id(300 + i), Sid, "AGENT:w1", Boot, "0123456789abcdef", 30, T0 + S(61 + 31 * i));
            Check(r.All.Count() <= ProposalStore.MaxHistory, "history bound");
            var rec = new ProposalStore();
            Equal(null, rec.ProposeRecovery(Id(400), "AGENT:w2", Boot, "0123456789abcdef", Sid, Id(8), "AGENT:w1", 120, T0), "recovery proposal");
            Check(rec.Decide(Id(400), true, Boot, T0), "approve recovery");
            Equal("WRONG_KIND", rec.GrantProblem(Id(400), ProposalStore.Attach, "AGENT:w2", Boot, "0123456789abcdef", T0), "a recovery grant never attaches");
            Equal(null, rec.GrantProblem(Id(400), ProposalStore.Recover, "AGENT:w2", Boot, "0123456789abcdef", T0), "recovery grant");
        }

        // ------------------------------------------------------------ Transitions

        static void TransitionsAreObservedNeverReissued()
        {
            Equal("EDIT", Transitions.Phase(new EditorFlags()), "edit");
            Equal("COMPILING", Transitions.Phase(new EditorFlags { Compiling = true, Playing = true, WillChange = true }), "compiling first");
            Equal("ENTERING_PLAYMODE", Transitions.Phase(new EditorFlags { WillChange = true }), "entering");
            Equal("EXITING_PLAYMODE", Transitions.Phase(new EditorFlags { Playing = true }), "exiting");
            Equal("PAUSED", Transitions.Phase(new EditorFlags { Playing = true, WillChange = true, Paused = true }), "paused");
            Equal("PENDING_OPERATION", Transitions.Busy("PLAYING", true), "pending");
            Equal("RELOADING", Transitions.Busy("RELOADING", false), "reloading");
            Equal(null, Transitions.Busy("PAUSED", false), "paused is usable");
            Equal("ALREADY_PLAYING", Transitions.Precondition("enter-playmode", "PAUSED"), "enter");
            Equal("NOT_PLAYING", Transitions.Precondition("exit-playmode", "EDIT"), "exit");
            Equal("ALREADY_PAUSED", Transitions.Precondition("pause", "PAUSED"), "pause");
            Equal("NOT_PAUSED", Transitions.Precondition("resume", "PLAYING"), "resume");
            Equal(null, Transitions.Precondition("resume", "PAUSED"), "resume ok");
            var p = new Pending { RequestId = Id(1), Op = "enter", Issued = T0, Deadline = T0 + S(120) };
            Equal(null, Transitions.Evaluate(p, false, true, T0 + S(1)), "in progress");
            p.Events.Add("ExitingEditMode");
            var reloaded = Pending.Load(p.Save());
            reloaded.Events.Add("EnteredPlayMode");
            Equal("OK", Transitions.Evaluate(reloaded, true, true, T0 + S(3))[0], "entered across a reload");
            var aborted = new Pending { RequestId = Id(2), Op = "enter", Issued = T0, Deadline = T0 + S(60) };
            aborted.Events.AddRange(new[] { "ExitingEditMode", "EnteredEditMode" });
            Equal("PLAYMODE_ENTER_ABORTED", Transitions.Evaluate(aborted, false, false, T0 + S(1))[1], "aborted");
            var idle = new Pending { RequestId = Id(3), Op = "enter", Issued = T0, Deadline = T0 + S(60) };
            Equal("PLAYMODE_ENTER_NOT_STARTED", Transitions.Evaluate(idle, false, false, T0 + S(6))[1], "not started");
            var slow = new Pending { RequestId = Id(4), Op = "exit", Issued = T0, Deadline = T0 + S(10) };
            Equal("TRANSITION_TIMEOUT", Transitions.Evaluate(slow, true, false, T0 + S(11))[1], "timeout");
        }

        static int Main()
        {
            int failed = 0;
            foreach (var m in typeof(CoreTests).GetMethods(BindingFlags.Static | BindingFlags.NonPublic)
                                               .Where(m => m.ReturnType == typeof(void) && m.GetParameters().Length == 0 && m.Name != "Main")
                                               .OrderBy(m => m.Name))
            {
                try { m.Invoke(null, null); Console.WriteLine("PASS " + m.Name); }
                catch (TargetInvocationException e) { failed++; Console.WriteLine("FAIL " + m.Name + ": " + e.InnerException.Message); }
            }
            Console.WriteLine(failed == 0 ? "ALL PASSED" : failed + " FAILED");
            return failed == 0 ? 0 : 1;
        }
    }
}
