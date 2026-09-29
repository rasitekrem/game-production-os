// TEST-ONLY: tests of the source-synchronization part of the bridge's Unity-free core (Phase 2C-6C, bridge 1.4.0): the
// source path grammar, the folder a deleted source is synchronized through (the direct parent; exactly one widening;
// never Assets), the import roots and their bound, the sync plan, the causal baseline read before the first import,
// the compilation journal (counters, per-assembly replacement, coalesced compilations, bounds, order, persistence),
// message relativizing and clipping, and the source commands of the protocol.
// Compiled and run with CoreTests.cs by tests/test_unity_live_bridge_core.py.
using System;
using System.Collections.Generic;
using System.Linq;

namespace Gpos.LiveBridge
{
    static partial class CoreTests
    {
        static string SrcCode(Action a) { return Code(a); }

        static void SourcePathsAreExactSourceFilesBelowAssets()
        {
            foreach (var good in new[] { "Assets/Foo.cs", "Assets/Scripts/Player.cs", "Assets/Game/Editor/Tool.cs", "Assets/Game/Game.Runtime.asmdef",
                                         "Assets/Game/Sub/Sub.asmref", "Assets/A (1)/b+c-d_e.cs", "Assets/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/Deep.cs" })
                Equal(good, SourcePaths.Check(good), "accepted: " + good);
            foreach (var bad in new[] { null, "", "Assets", "Assets/", "assets/Foo.cs", "/Assets/Foo.cs", "Packages/com.x/A.cs", "Library/A.cs",
                                        "Assets/Foo.CS", "Assets/Foo.txt", "Assets/Foo.cs.meta", "Assets/Foo.dll", "Assets/.hidden/A.cs", "Assets/a./A.cs",
                                        "Assets/ a/A.cs", "Assets/a~/A.cs", "Assets/../A.cs", "Assets/a/../A.cs", "Assets//A.cs", "Assets/a\\b/A.cs",
                                        "Assets/StreamingAssets/A.cs", "Assets/x/Editor Default Resources/A.cs", "Assets/cvs/A.cs",
                                        "Assets/GposAssetTxn-0123/A.cs", "Assets/a/.cs", "Assets/a/.A.cs", "Assets/a/A..B.cs", "Assets/a/A .cs",
                                        "Assets/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/TooDeep.cs", "Assets/" + new string('a', 600) + ".cs" })
                Equal("SOURCE_PATH_INVALID", SrcCode(() => SourcePaths.Check(bad)), "refused: " + bad);
        }

        static void ADeletedSourceIsSynchronizedThroughItsFolderNeverAssets()
        {
            Equal("Assets/Scripts/Combat", SourcePaths.DirectParent("Assets/Scripts/Combat/Foo.cs"), "direct parent");
            Check(SourcePaths.DirectParent("Assets/Foo.cs") == null, "D1: a source directly in Assets/ has no import folder");
            // D2: exactly one widening, never to Assets
            Equal("Assets/Scripts", SourcePaths.Widened("Assets/Scripts/Combat/Foo.cs"), "one level up");
            Check(SourcePaths.Widened("Assets/Scripts/Foo.cs") == null, "the folder above Assets/Scripts is Assets");
            Check(SourcePaths.Widened("Assets/Foo.cs") == null, "nothing above Assets");
            Equal("Assets/a/b/c", SourcePaths.Widened("Assets/a/b/c/d/Foo.cs"), "never two levels");
        }

        static void ImportRootsAreDistinctContainedAndBounded()
        {
            Equal("Assets/A|Assets/B", string.Join("|", SourcePaths.Roots(new[] { "Assets/B", "Assets/A", "Assets/A/Sub", "Assets/a" })), "covered roots drop");
            Equal("Assets/AB|Assets/A", string.Join("|", SourcePaths.Roots(new[] { "Assets/A", "Assets/AB" }).OrderByDescending(x => x.Length)), "a name prefix is no folder");
            Equal(4, SourcePaths.Roots(new[] { "Assets/A", "Assets/B", "Assets/C", "Assets/D", "Assets/D/E" }).Count, "four roots");
            Equal("SOURCE_SYNC_LIMIT", SrcCode(() => SourcePaths.Roots(new[] { "Assets/A", "Assets/B", "Assets/C", "Assets/D", "Assets/E" })), "five roots");
            Check(SourcePaths.Within("Assets/A/b.cs", "Assets/A") && !SourcePaths.Within("Assets/AB/b.cs", "Assets/A"), "Within is per folder");
        }

        static List<object> Paths(params string[] p) { return p.Cast<object>().ToList(); }

        static void ASyncPlanIsValidatedWhole()
        {
            var plan = SyncPlan.Parse(Paths("Assets/A/X.cs"), Paths("Assets/A/Y.cs"));
            Equal("Assets/A/X.cs", plan.Sources.Single(), "sources");
            Equal("Assets/A/Y.cs", plan.Deleted.Single(), "deleted");
            Equal("BAD_ARGUMENTS", SrcCode(() => SyncPlan.Parse(Paths(), Paths())), "at least one path");
            Equal("BAD_ARGUMENTS", SrcCode(() => SyncPlan.Parse(null, Paths("Assets/A/Y.cs"))), "a list");
            Equal("BAD_ARGUMENTS", SrcCode(() => SyncPlan.Parse(new List<object> { 3.0 }, Paths())), "strings");
            Equal("SOURCE_PATH_INVALID", SrcCode(() => SyncPlan.Parse(Paths("Assets/A/X.cs"), Paths("Assets/a/x.cs"))), "named twice in any spelling");
            Equal("SOURCE_PATH_INVALID", SrcCode(() => SyncPlan.Parse(Paths("Assets/A/X.cs", "Assets/../X.cs"), Paths())), "every path is checked");
            var many = Enumerable.Range(0, 65).Select(i => (object)("Assets/A/F" + i + ".cs")).ToList();
            Equal("SOURCE_SYNC_LIMIT", SrcCode(() => SyncPlan.Parse(many, Paths())), "at most 64");
            Equal("SOURCE_SYNC_LIMIT", SrcCode(() => SyncPlan.Parse(many.Take(40).ToList(), many.Skip(40).Take(25).ToList())), "at most 64 in total");
        }

        sealed class FakeHost : ISyncHost
        {
            public long Started;
            public readonly List<string> Log = new List<string>();
            public long CompileStarted { get { return Started; } }
            public void Begin(long before) { Log.Add("begin:" + before); }
            public void ImportExact(string path) { Log.Add("exact:" + path); Started++; }   // Unity starts a compilation during the import
            public void ImportRecursive(string folder) { Log.Add("folder:" + folder); Started++; }
        }

        static void TheCausalBaselineIsReadBeforeTheFirstImport()
        {
            var host = new FakeHost { Started = 7 };
            var marks = SyncRun.Run(host, new[] { "Assets/A/X.cs", "Assets/A/Z.cs" }, new[] { "Assets/B" }, new SyncMarks());
            Equal(7L, marks.Before, "the baseline is the count before any import");
            Equal(10L, marks.After, "after the imports");
            Equal(3, marks.Imports, "three imports");
            Equal("begin:7|exact:Assets/A/X.cs|exact:Assets/A/Z.cs|folder:Assets/B", string.Join("|", host.Log), "recorded before, exact first, then folders");
            // a compilation that starts during the first import is newer than the sync
            Check(8 > marks.Before, "the compilation started by the first import counts as after the sync");
        }

        static CompileMessage M(string severity, string file, int line, string text = "x")
        {
            return new CompileMessage { Severity = severity, File = file, Line = line, Column = 1, Text = text };
        }

        static void TheJournalCountsCompilationsAndCoalescesStarts()
        {
            var j = new CompileJournal();
            j.OnStarted();
            j.OnStarted();                                             // two starts, one result (coalesced)
            j.OnAssembly("Game", new[] { M("ERROR", "Assets/A.cs", 3), M("WARNING", "Assets/A.cs", 1), M("INFO", "Assets/A.cs", 2) });
            j.OnAssembly("Tests", new CompileMessage[0]);
            j.OnFinished(4, "t1");
            Equal(2L, j.Started, "starts");
            Equal(1L, j.Finished, "finishes");
            var last = j.Last;
            Equal(2L, last.ForStart, "the finish belongs to the latest start");
            Equal(4, last.Generation, "the reload generation at the finish");
            Equal(1, last.Errors, "errors of this compilation");
            Equal(1, last.Warnings, "warnings (INFO is not kept)");
            Equal(2, last.Assemblies, "assemblies reported");
            j.OnStarted();
            j.OnAssembly("Game", new CompileMessage[0]);               // Game recompiled clean: its entry is replaced
            j.OnFinished(4, "t2");
            Equal(0, j.Last.Errors, "the next compilation counts only its own messages");
            Equal(0, j.Entries.Single(e => e.Assembly == "Game").Errors, "the latest result of an assembly replaces the earlier one");
            Equal(2, j.Entries.Count, "an assembly Unity did not recompile keeps its entry");
            Equal(3L, j.Entries.Single(e => e.Assembly == "Game").CompileGeneration, "entries carry their compile generation");
        }

        static void AReloadAfterACleanCompilationDropsStaleErrors()
        {
            var j = new CompileJournal();
            Equal(0, j.OnReload(1), "the first domain of the session");
            j.OnStarted();
            j.OnAssembly("Game", new[] { M("ERROR", "Assets/A.cs", 3) });
            j.OnAssembly("Tools", new[] { M("WARNING", "Assets/T.cs", 1) });
            j.OnFinished(1, "t1");
            Equal(0, j.OnReload(1), "no reload after the failure");
            j.OnStarted();                                             // the fix: Unity takes Game from its cache, reports nothing
            j.OnFinished(1, "t2");
            Equal(1, j.OnReload(2), "a reload after a compilation without errors");
            Check(j.Entries.All(e => e.Errors == 0), "no stale error remains");
            Equal("Tools", j.Entries.Single().Assembly, "warnings of an unreported assembly are kept");
            Equal(1, j.Superseded, "the dropped entry is counted");
            Equal(0, j.OnReload(2), "one reload is handled once");
            j.OnStarted();
            j.OnAssembly("Game", new[] { M("ERROR", "Assets/A.cs", 4) });
            j.OnFinished(2, "t3");
            Equal(0, j.OnReload(3), "a reload after a failed compilation drops nothing");
            Equal(1, j.Entries.Count(e => e.Errors > 0), "the error stays");
            Equal(j.Save(), CompileJournal.Load(j.Save(), "x").Save(), "the reload marker round-trips");
        }

        static void TheJournalIsBoundedAndOrdered()
        {
            var j = new CompileJournal();
            j.OnStarted();
            j.OnAssembly("Big", Enumerable.Range(0, 40).Select(i => M(i % 2 == 0 ? "WARNING" : "ERROR", "Assets/B.cs", 40 - i)));
            var big = j.Entries.Single();
            Equal(CompileJournal.MaxMessagesPerAssembly, big.Messages.Count, "kept per assembly");
            Equal(8, big.Dropped, "dropped per assembly");
            Equal(20, big.Errors, "counts include dropped messages");
            Check(big.Messages.Take(20).All(m => m.Severity == "ERROR"), "errors first");
            Check(big.Messages.Take(20).Select(m => m.Line).SequenceEqual(big.Messages.Take(20).Select(m => m.Line).OrderBy(x => x)), "then file and line");
            for (int i = 0; i < 70; i++)
            {
                j.OnStarted();
                j.OnAssembly("A" + i.ToString("00"), new[] { M("WARNING", "Assets/W.cs", 1) });
            }
            Equal(CompileJournal.MaxAssemblies, j.Entries.Count, "at most 64 assemblies");
            Check(j.Entries.All(e => e.Assembly != "Big") && j.Entries.All(e => e.Assembly != "A00"), "the oldest go first");
            var k = new CompileJournal();
            for (int i = 0; i < 12; i++)
            {
                k.OnStarted();
                k.OnAssembly("K" + i.ToString("00"), Enumerable.Range(0, 30).Select(n => M("ERROR", "Assets/K.cs", n)));
            }
            Equal(CompileJournal.MaxMessages, k.Entries.Sum(e => e.Messages.Count), "at most 256 messages in total");
            Equal(12 * 30 - CompileJournal.MaxMessages, k.Entries.Sum(e => e.Dropped), "every dropped message is counted");
            Equal(30, k.Entries.Single(e => e.Assembly == "K11").Messages.Count, "the newest are kept whole");
            var rows = k.Select(null, null, null);
            Check(rows.Select(r => r.Key.Assembly).SequenceEqual(rows.Select(r => r.Key.Assembly).OrderBy(a => a, StringComparer.Ordinal)), "rows by assembly");
            Equal(30, k.Select(12, "ERROR", "K11").Count, "filters: generation, severity, assembly");
            Equal(0, k.Select(null, "WARNING", null).Count, "severity filter");
        }

        static void TheJournalRecordsSyncsAndSurvivesAReload()
        {
            var j = new CompileJournal { StartedUtc = "t0" };
            j.OnStarted();
            long g = j.BeginSync(1, 2, "t1");
            j.EndSync(g, 3, true);
            j.OnAssembly("Game", new[] { M("ERROR", "Assets/A.cs", 3, "quote \" and \\ and é") });
            j.OnFinished(2, "t2");
            for (int i = 0; i < 70; i++) j.BeginSync(3, 2, "t");
            Equal(71L, j.SyncGeneration, "sync generations are monotonic");
            Equal(CompileJournal.MaxSyncs, j.Syncs.Count, "sync records are bounded");
            var back = CompileJournal.Load(j.Save(), "later");
            Equal(j.Save(), back.Save(), "the journal round-trips through SessionState text");
            Equal("t0", back.StartedUtc, "the Editor session's start is kept");
            Equal("quote \" and \\ and é", back.Entries.Single().Messages.Single().Text, "text");
            var reset = CompileJournal.Load("{\"v\":2}", "t9");
            Check(reset.Reset && reset.Started == 0 && reset.StartedUtc == "t9", "an unreadable journal is replaced and marked, never guessed");
            Check(!CompileJournal.Load("", "t9").Reset, "an empty one is new");
        }

        static void MessagesAreProjectRelativeAndClipped()
        {
            bool outside, clipped;
            Equal("Assets/A.cs", CompileText.RelativeFile("/Users/x/p/Game/Assets/A.cs", "/Users/x/p/Game", out outside), "inside");
            Check(!outside, "inside is not outside");
            Equal("Assets/A.cs", CompileText.RelativeFile("Assets/A.cs", "/Users/x/p/Game", out outside), "already relative");
            Check(CompileText.RelativeFile("/Users/x/p/GameOther/A.cs", "/Users/x/p/Game", out outside) == null && outside, "a sibling is outside");
            Check(CompileText.RelativeFile("/Library/x.cs", "/Users/x/p/Game", out outside) == null && outside, "elsewhere");
            Check(CompileText.RelativeFile("Assets/../../secret.cs", "/Users/x/p/Game", out outside) == null && outside, "no climbing");
            Check(CompileText.RelativeFile("", "/p", out outside) == null && !outside, "no file");
            Equal("error CS2001: Source file 'Assets/Gone.cs' could not be found",
                  CompileText.Text("error CS2001: Source file '/Users/x/p/Game/Assets/Gone.cs' could not be found", "/Users/x/p/Game", out clipped), "text relativized");
            Equal(CompileText.MaxMessageChars, CompileText.Text(new string('e', 900), "/p", out clipped).Length, "clipped");
            Check(clipped, "clipped is reported");
        }

        static void SourceCommandsHaveExactArgumentsAndClassification()
        {
            Equal("sources,deleted", string.Join(",", Parse(Id(20), Req(Id(20), "sync-sources", "{\"sources\":[],\"deleted\":[]}", Sid)).Args.Keys), "sync args");
            Equal("BAD_ARGUMENTS", Code(() => Parse(Id(21), Req(Id(21), "sync-sources", "{\"sources\":[]}", Sid))), "exact sync args");
            Equal("BAD_ARGUMENTS", Code(() => Parse(Id(22), Req(Id(22), "sync-sources", "{\"sources\":[],\"deleted\":[],\"folder\":\"Assets\"}", Sid))), "no folder argument");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(23), Req(Id(23), "compilation-status", "{}"))), "a session is required");
            Parse(Id(24), Req(Id(24), "compilation-diagnostics", "{\"generation\":null,\"severity\":null,\"assembly\":null,\"page\":null}", Sid));
            Check(Protocol.ChangesSources("sync-sources") && Protocol.NeedsSession("sync-sources"), "sync-sources changes sources in a session");
            Check(!Protocol.ChangesEditorState("sync-sources") && !Protocol.IsAuthoring("sync-sources") && !Protocol.ChangesScene("sync-sources") &&
                  !Protocol.ChangesAssets("sync-sources"), "it is neither an Editor-state nor a Scene or asset command");
            foreach (var reader in new[] { "compilation-status", "compilation-diagnostics" })
                Check(!Protocol.ChangesSources(reader) && !Protocol.ChangesEditorState(reader) && !Protocol.IsAuthoring(reader), reader + " only reads");
            foreach (var c in new[] { "compile", "request-compilation", "refresh", "import", "import-folder", "write-source", "delete-source", "execute" })
                Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(25), Req(Id(25), c, "{}", Sid))), "no " + c + " command");
        }
    }
}
