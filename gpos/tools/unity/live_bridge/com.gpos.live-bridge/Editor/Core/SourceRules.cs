// GPOS live bridge — source synchronization and compilation facts (bridge 1.4.0; Unity-free core).
// A sync names exact source paths only: .cs, .asmdef and .asmref files below Assets/. An existing path is imported
// exactly; a deleted path is synchronized by a recursive import of its direct parent folder (or, when that folder is
// gone too, of exactly one folder above it — never Assets itself and never higher). The causal baseline of a sync is
// the compilation-start count read before its first import, so a compilation Unity starts during an import is
// newer than the sync. The compilation journal keeps bounded, Editor-session facts from CompilationPipeline
// callbacks: counters, the last finished compilations and the latest messages of each assembly. There is no code,
// no content, no folder argument and no generic AssetDatabase operation here.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;

namespace Gpos.LiveBridge
{
    internal static class SourcePaths
    {
        public const int MaxPaths = 64;
        public const int MaxPathLength = 512;
        public const int MaxSegments = 16;
        public const int MaxRoots = 4;
        public const long MaxSourceBytes = 2L * 1024 * 1024;
        public const string Root = "Assets";

        static readonly string[] Extensions = { ".cs", ".asmdef", ".asmref" };
        static readonly Regex Segment = new Regex("^[A-Za-z0-9 _().,+\\-]{1,64}\\z");
        static readonly Regex Stem = new Regex("^[A-Za-z0-9_+\\-]([A-Za-z0-9_.+\\-]{0,126}[A-Za-z0-9_+\\-])?\\z");
        static readonly string[] Excluded = { "streamingassets", "editor default resources", "cvs" };   // Unity compiles nothing there

        static Refusal Invalid(string why) { return new Refusal("SOURCE_PATH_INVALID", why); }

        // Validates one source path: "Assets/<folders>/<stem>.<cs|asmdef|asmref>", exactly as GPOS checks it first.
        public static string Check(string path)
        {
            if (path == null || path.Length == 0 || path.Length > MaxPathLength) throw Invalid("a source path has 1 to " + MaxPathLength + " characters");
            if (!path.StartsWith(Root + "/", StringComparison.Ordinal)) throw Invalid("sources are synchronized only below Assets/ (never Packages/, Library/ or an absolute path)");
            string ext = Extensions.FirstOrDefault(e => path.EndsWith(e, StringComparison.Ordinal));
            if (ext == null) throw Invalid("a source path ends in .cs, .asmdef or .asmref");
            var parts = path.Split('/');
            if (parts.Length > MaxSegments + 2) throw Invalid("a source lives at most " + MaxSegments + " folders below Assets/");
            for (int i = 1; i < parts.Length - 1; i++)
            {
                string s = parts[i];
                if (!Segment.IsMatch(s) || s.StartsWith(".", StringComparison.Ordinal) || s.EndsWith(".", StringComparison.Ordinal) ||
                    s.StartsWith(" ", StringComparison.Ordinal) || s.EndsWith(" ", StringComparison.Ordinal))
                    throw Invalid("folder name '" + s + "' is not a plain folder name");
                if (Excluded.Contains(s.ToLowerInvariant())) throw Invalid("sources are never synchronized in a special folder (" + s + ")");
                if (s.StartsWith(AssetPaths.ScratchPrefix, StringComparison.OrdinalIgnoreCase)) throw Invalid("GPOS transaction scratch folders hold no sources");
            }
            string file = parts[parts.Length - 1];
            string stem = file.Substring(0, file.Length - ext.Length);
            if (!Stem.IsMatch(stem) || stem.Contains("..")) throw Invalid("the file name is 1 to 128 letters, digits, _ + - or inner dots");
            return path;
        }

        public static string Parent(string path)
        {
            int slash = path.LastIndexOf('/');
            return slash <= 0 ? null : path.Substring(0, slash);
        }

        // The folder a deleted source is synchronized through when its direct parent still exists: that parent, or
        // null when the parent is Assets itself (the Assets root is never imported recursively).
        public static string DirectParent(string deleted)
        {
            string parent = Parent(deleted);
            return parent == null || parent == Root ? null : parent;
        }

        // One widening, only when the direct parent itself is gone: the folder above it, or null when that is Assets.
        public static string Widened(string deleted)
        {
            string parent = DirectParent(deleted);
            string above = parent == null ? null : Parent(parent);
            return above == null || above == Root ? null : above;
        }

        public static bool Within(string path, string folder)
        {
            return string.Equals(path, folder, StringComparison.OrdinalIgnoreCase) ||
                   path.StartsWith(folder + "/", StringComparison.OrdinalIgnoreCase);
        }

        // The distinct import roots: a folder inside another root is covered by that root and is not imported again.
        public static List<string> Roots(IEnumerable<string> folders)
        {
            var distinct = folders.Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(f => f, StringComparer.Ordinal).ToList();
            var roots = distinct.Where(f => !distinct.Any(o => !string.Equals(o, f, StringComparison.OrdinalIgnoreCase) && Within(f, o))).ToList();
            if (roots.Count > MaxRoots) throw new Refusal("SOURCE_SYNC_LIMIT", "the deleted sources need more than " + MaxRoots + " separate folder imports");
            return roots;
        }
    }

    // A validated sync request: every path checked and bounded, no path named twice — before Unity is asked anything.
    internal sealed class SyncPlan
    {
        public readonly List<string> Sources = new List<string>(), Deleted = new List<string>();

        public static SyncPlan Parse(object sources, object deleted)
        {
            var plan = new SyncPlan();
            Add(plan.Sources, sources, "sources");
            Add(plan.Deleted, deleted, "deleted");
            int total = plan.Sources.Count + plan.Deleted.Count;
            if (total == 0) throw new Refusal("BAD_ARGUMENTS", "a sync names at least one source path");
            if (total > SourcePaths.MaxPaths) throw new Refusal("SOURCE_SYNC_LIMIT", "a sync names at most " + SourcePaths.MaxPaths + " paths");
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var p in plan.Sources.Concat(plan.Deleted))
                if (!seen.Add(p)) throw new Refusal("SOURCE_PATH_INVALID", "a path is named more than once: " + p);
            return plan;
        }

        static void Add(List<string> into, object value, string key)
        {
            var list = value as List<object>;
            if (list == null) throw new Refusal("BAD_ARGUMENTS", key + " must be a list of source paths");
            if (list.Count > SourcePaths.MaxPaths) throw new Refusal("SOURCE_SYNC_LIMIT", "a sync names at most " + SourcePaths.MaxPaths + " paths");
            foreach (var item in list)
            {
                var s = item as string;
                if (s == null) throw new Refusal("BAD_ARGUMENTS", key + " must be a list of source paths");
                into.Add(SourcePaths.Check(s));
            }
        }
    }

    // What the sync needs from the Editor: the compilation-start counter, the sync record and the two imports.
    internal interface ISyncHost
    {
        long CompileStarted { get; }
        void Begin(long compileStartedBeforeSync);
        void ImportExact(string path);
        void ImportRecursive(string folder);
    }

    internal sealed class SyncMarks
    {
        public long Before = -1, After = -1;
        public int Imports;
        public bool Began;
    }

    internal static class SyncRun
    {
        // The causal baseline is read before the first import and recorded before any import happens; exact paths
        // are imported first, then each folder once, recursively.
        public static SyncMarks Run(ISyncHost host, IList<string> exact, IList<string> folders, SyncMarks marks)
        {
            marks.Before = host.CompileStarted;
            host.Begin(marks.Before);
            marks.Began = true;
            foreach (var p in exact) { marks.Imports++; host.ImportExact(p); }
            foreach (var f in folders) { marks.Imports++; host.ImportRecursive(f); }
            marks.After = host.CompileStarted;
            return marks;
        }
    }

    // One compiler message as the journal keeps it: severity, project-relative file (or none), line, column, text.
    internal sealed class CompileMessage
    {
        public string Severity, File, Text;
        public bool Outside, Clipped;
        public int Line, Column;
    }

    internal static class CompileText
    {
        public const int MaxMessageChars = 512;

        static string Slash(string s) { return s.Replace('\\', '/'); }

        // A message's file as a project-relative path, or null (Outside) when it is not inside the project.
        public static string RelativeFile(string file, string projectPath, out bool outside)
        {
            outside = false;
            if (string.IsNullOrEmpty(file)) return null;
            string f = Slash(file), root = Slash(projectPath ?? "").TrimEnd('/') + "/";
            if (f.StartsWith("/", StringComparison.Ordinal) || (f.Length > 1 && f[1] == ':'))
            {
                if (root.Length > 1 && f.StartsWith(root, StringComparison.Ordinal)) f = f.Substring(root.Length);
                else { outside = true; return null; }
            }
            if (f.Length > SourcePaths.MaxPathLength || f.Split('/').Any(s => s == ".." || s == ".")) { outside = true; return null; }
            return f;
        }

        // The message text with the project's absolute path made relative, clipped to MaxMessageChars.
        public static string Text(string message, string projectPath, out bool clipped)
        {
            string m = message ?? "", root = Slash(projectPath ?? "").TrimEnd('/');
            if (root.Length > 1) m = m.Replace(root + "/", "").Replace(root.Replace('/', '\\') + "\\", "");
            clipped = m.Length > MaxMessageChars;
            return clipped ? m.Substring(0, MaxMessageChars) : m;
        }

        public static int Rank(string severity) { return severity == "ERROR" ? 0 : 1; }

        // The journal's one deterministic message order.
        public static List<CompileMessage> Ordered(IEnumerable<CompileMessage> messages)
        {
            return messages.OrderBy(m => Rank(m.Severity)).ThenBy(m => m.File ?? "￿", StringComparer.Ordinal)
                           .ThenBy(m => m.Line).ThenBy(m => m.Column).ThenBy(m => m.Text, StringComparer.Ordinal).ToList();
        }
    }

    // The Editor-session compilation journal: monotonic counters, the last finished compilations, the sync records and
    // the latest messages of each assembly. It is persisted as JSON in SessionState, so it survives Domain Reloads and
    // is lost when the Editor quits. Every collection is bounded; nothing is ever read from a log.
    internal sealed class CompileJournal
    {
        public const int MaxAssemblies = 64, MaxMessagesPerAssembly = 32, MaxMessages = 256, MaxFinishes = 16, MaxSyncs = 64;

        public sealed class Finish
        {
            public long Seq, ForStart;
            public int Generation, Errors, Warnings, Assemblies;
            public string Utc;
        }

        public sealed class Sync
        {
            public long Gen, Before, After = -1;
            public bool CompilingAfter;
            public int Generation;
            public string Utc;
        }

        public sealed class Entry
        {
            public string Assembly;
            public long CompileGeneration;
            public int Errors, Warnings, Dropped;
            public List<CompileMessage> Messages = new List<CompileMessage>();
        }

        public long Started, Finished, SyncGeneration;
        public string StartedUtc = "";
        public bool Reset;
        public int LastReload, Superseded;
        public int PendingErrors, PendingWarnings, PendingAssemblies;
        public readonly List<Finish> Finishes = new List<Finish>();
        public readonly List<Sync> Syncs = new List<Sync>();
        public readonly List<Entry> Entries = new List<Entry>();

        public Finish Last { get { return Finishes.Count == 0 ? null : Finishes[Finishes.Count - 1]; } }

        public Sync LatestSync { get { return Syncs.Count == 0 ? null : Syncs[Syncs.Count - 1]; } }

        public void OnStarted() { Started++; }

        // One assembly's result: replaces what the journal held for it (cached assemblies report nothing and keep
        // their earlier entry). Counts include every message; at most MaxMessagesPerAssembly are kept, errors first.
        public void OnAssembly(string assembly, IEnumerable<CompileMessage> messages)
        {
            var all = CompileText.Ordered(messages.Where(m => m.Severity == "ERROR" || m.Severity == "WARNING"));
            var e = new Entry { Assembly = assembly, CompileGeneration = Started, Errors = all.Count(m => m.Severity == "ERROR"),
                                Warnings = all.Count(m => m.Severity == "WARNING") };
            e.Messages = all.Take(MaxMessagesPerAssembly).ToList();
            e.Dropped = all.Count - e.Messages.Count;
            Entries.RemoveAll(x => x.Assembly == assembly);
            Entries.Add(e);
            PendingErrors += e.Errors;
            PendingWarnings += e.Warnings;
            PendingAssemblies++;
            Bound();
        }

        public void OnFinished(int generation, string utc)
        {
            Finished++;
            Finishes.Add(new Finish { Seq = Finished, ForStart = Started, Generation = generation, Errors = PendingErrors,
                                      Warnings = PendingWarnings, Assemblies = PendingAssemblies, Utc = utc });
            if (Finishes.Count > MaxFinishes) Finishes.RemoveAt(0);
            PendingErrors = PendingWarnings = PendingAssemblies = 0;
        }

        // A Domain Reload after a compilation that reported no errors proves Unity loaded every assembly compiled. An
        // assembly Unity took from its cache reports nothing, so an error entry left from an earlier failed compilation
        // of it is stale: it is dropped (and counted), never shown as the assembly's latest result.
        public int OnReload(int generation)
        {
            if (generation <= LastReload) return 0;
            LastReload = generation;
            var last = Last;
            if (last == null || last.Generation >= generation || last.Errors > 0) return 0;
            int dropped = Entries.RemoveAll(e => e.Errors > 0);
            Superseded += dropped;
            return dropped;
        }

        public long BeginSync(long before, int generation, string utc)
        {
            SyncGeneration++;
            Syncs.Add(new Sync { Gen = SyncGeneration, Before = before, Generation = generation, Utc = utc });
            if (Syncs.Count > MaxSyncs) Syncs.RemoveAt(0);
            return SyncGeneration;
        }

        public void EndSync(long gen, long after, bool compilingAfter)
        {
            var s = Syncs.FirstOrDefault(x => x.Gen == gen);
            if (s == null) return;
            s.After = after;
            s.CompilingAfter = compilingAfter;
        }

        // Oldest assemblies go first (then by name); the total number of kept messages is bounded too.
        void Bound()
        {
            var order = Entries.OrderBy(x => x.CompileGeneration).ThenBy(x => x.Assembly, StringComparer.Ordinal).ToList();
            while (Entries.Count > MaxAssemblies) { Entries.Remove(order[0]); order.RemoveAt(0); }
            int total = Entries.Sum(x => x.Messages.Count);
            foreach (var x in order)
            {
                if (total <= MaxMessages) break;
                int drop = Math.Min(x.Messages.Count, total - MaxMessages);
                x.Messages.RemoveRange(x.Messages.Count - drop, drop);
                x.Dropped += drop;
                total -= drop;
            }
        }

        // The journal's messages, filtered only on closed fields, in one deterministic order.
        public List<KeyValuePair<Entry, CompileMessage>> Select(long? generation, string severity, string assembly)
        {
            var rows = new List<KeyValuePair<Entry, CompileMessage>>();
            foreach (var e in Entries.OrderBy(x => x.Assembly, StringComparer.Ordinal))
            {
                if (assembly != null && e.Assembly != assembly) continue;
                if (generation.HasValue && e.CompileGeneration != generation.Value) continue;
                foreach (var m in e.Messages)
                    if (severity == null || m.Severity == severity) rows.Add(new KeyValuePair<Entry, CompileMessage>(e, m));
            }
            return rows;
        }

        // ------------------------------------------------------------ persistence

        static string N(long v) { return v.ToString(CultureInfo.InvariantCulture); }

        public string Save()
        {
            return Json.Write(new Dictionary<string, object> {
                { "v", 1 }, { "started", N(Started) }, { "finished", N(Finished) }, { "sync", N(SyncGeneration) }, { "since", StartedUtc },
                { "reset", Reset }, { "pending", new List<object> { PendingErrors, PendingWarnings, PendingAssemblies } },
                { "reload", LastReload }, { "superseded", Superseded },
                { "finishes", Finishes.Select(f => (object)new List<object> { N(f.Seq), N(f.ForStart), f.Generation, f.Errors, f.Warnings, f.Assemblies, f.Utc }).ToList() },
                { "syncs", Syncs.Select(s => (object)new List<object> { N(s.Gen), N(s.Before), N(s.After), s.CompilingAfter, s.Generation, s.Utc }).ToList() },
                { "entries", Entries.Select(e => (object)new List<object> { e.Assembly, N(e.CompileGeneration), e.Errors, e.Warnings, e.Dropped,
                    e.Messages.Select(m => (object)new List<object> { m.Severity, m.File, m.Outside, m.Line, m.Column, m.Text, m.Clipped }).ToList() }).ToList() } });
        }

        // A journal that cannot be read back is replaced by an empty one marked Reset (reported, never guessed).
        public static CompileJournal Load(string text, string utc)
        {
            if (string.IsNullOrEmpty(text)) return new CompileJournal { StartedUtc = utc };
            try { return Read(text); }
            catch (Exception) { return new CompileJournal { StartedUtc = utc, Reset = true }; }
        }

        static long L(object v) { return long.Parse((string)v, CultureInfo.InvariantCulture); }

        static int I(object v) { return checked((int)(double)v); }

        static CompileJournal Read(string text)
        {
            var d = (Dictionary<string, object>)Json.Parse(text);
            if (I(d["v"]) != 1) throw new FormatException("journal version");
            var j = new CompileJournal { Started = L(d["started"]), Finished = L(d["finished"]), SyncGeneration = L(d["sync"]),
                                         StartedUtc = (string)d["since"], Reset = (bool)d["reset"] };
            var p = (List<object>)d["pending"];
            j.PendingErrors = I(p[0]); j.PendingWarnings = I(p[1]); j.PendingAssemblies = I(p[2]);
            j.LastReload = I(d["reload"]);
            j.Superseded = I(d["superseded"]);
            foreach (List<object> f in (List<object>)d["finishes"])
                j.Finishes.Add(new Finish { Seq = L(f[0]), ForStart = L(f[1]), Generation = I(f[2]), Errors = I(f[3]), Warnings = I(f[4]), Assemblies = I(f[5]), Utc = (string)f[6] });
            foreach (List<object> s in (List<object>)d["syncs"])
                j.Syncs.Add(new Sync { Gen = L(s[0]), Before = L(s[1]), After = L(s[2]), CompilingAfter = (bool)s[3], Generation = I(s[4]), Utc = (string)s[5] });
            foreach (List<object> e in (List<object>)d["entries"])
            {
                var entry = new Entry { Assembly = (string)e[0], CompileGeneration = L(e[1]), Errors = I(e[2]), Warnings = I(e[3]), Dropped = I(e[4]) };
                foreach (List<object> m in (List<object>)e[5])
                    entry.Messages.Add(new CompileMessage { Severity = (string)m[0], File = (string)m[1], Outside = (bool)m[2], Line = I(m[3]),
                                                            Column = I(m[4]), Text = (string)m[5], Clipped = (bool)m[6] });
                j.Entries.Add(entry);
            }
            return j;
        }
    }
}
