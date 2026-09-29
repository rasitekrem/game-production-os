// GPOS live bridge — compilation facts (bridge 1.4.0). The journal is fed only by CompilationPipeline callbacks
// (compilationStarted, assemblyCompilationFinished, compilationFinished) and kept in SessionState: it survives a Domain
// Reload and is lost when the Editor quits. Nothing reads Editor.log, nothing starts or requests a compilation, and the
// two commands here (compilation-status, compilation-diagnostics) only read the journal and the Editor's flags.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace Gpos.LiveBridge
{
    internal static class Compilation
    {
        internal const string KJournal = "gpos.live.compile";
        public const int DiagnosticsPage = 50;
        public const int MaxAssemblyName = 128;

        static CompileJournal journal;
        static string root;

        static string Utc() { return DateTime.UtcNow.ToString("o"); }

        // The journal of this Editor session, read back once per domain.
        internal static CompileJournal Journal
        {
            get
            {
                if (journal == null) journal = CompileJournal.Load(SessionState.GetString(KJournal, ""), Utc());
                return journal;
            }
        }

        static void Save() { SessionState.SetString(KJournal, Journal.Save()); }

        static string ProjectRoot
        {
            get
            {
                if (root == null) root = Path.GetDirectoryName(Application.dataPath);
                return root;
            }
        }

        internal static void Register()
        {
            root = Path.GetDirectoryName(Application.dataPath);
            if (SessionState.GetString(KJournal, "") == "") Save();   // this Editor session's journal starts here
            Guard(() => Journal.OnReload(LiveBridge.Generation));      // a reload after a clean compilation
            CompilationPipeline.compilationStarted += _ => Guard(() => Journal.OnStarted());
            CompilationPipeline.assemblyCompilationFinished += (path, messages) => Guard(() => OnAssembly(path, messages));
            CompilationPipeline.compilationFinished += _ => Guard(() => Journal.OnFinished(LiveBridge.Generation, Utc()));
        }

        // A journal problem never disturbs Unity's compilation: the callback records what it can and saves.
        static void Guard(Action record)
        {
            try { record(); Save(); }
            catch (Exception e) { Debug.LogWarning("[GPOS] the compilation journal could not record an event: " + e.GetType().Name); }
        }

        static void OnAssembly(string path, CompilerMessage[] messages)
        {
            string name = Path.GetFileNameWithoutExtension(path ?? "") ?? "";
            if (name.Length > MaxAssemblyName) name = name.Substring(0, MaxAssemblyName);
            var list = new List<CompileMessage>();
            foreach (var m in messages ?? new CompilerMessage[0])
            {
                string severity = m.type == CompilerMessageType.Error ? "ERROR" : m.type == CompilerMessageType.Warning ? "WARNING" : null;
                if (severity == null) continue;
                bool outside, clipped;
                string file = CompileText.RelativeFile(m.file, ProjectRoot, out outside);
                string text = CompileText.Text(m.message, ProjectRoot, out clipped);
                list.Add(new CompileMessage { Severity = severity, File = file, Outside = outside, Line = m.line, Column = m.column,
                                              Text = text, Clipped = clipped });
            }
            Journal.OnAssembly(name, list);
        }

        internal static long BeginSync(long before)
        {
            long gen = Journal.BeginSync(before, LiveBridge.Generation, Utc());
            Save();
            return gen;
        }

        internal static void EndSync(long gen, long after, bool compilingAfter)
        {
            Journal.EndSync(gen, after, compilingAfter);
            Save();
        }

        // ------------------------------------------------------------ views

        static Dictionary<string, object> FinishView(CompileJournal.Finish f)
        {
            if (f == null) return null;
            return new Dictionary<string, object> {
                { "compile_generation", f.ForStart }, { "sequence", f.Seq }, { "reload_generation", f.Generation },
                { "errors", f.Errors }, { "warnings", f.Warnings }, { "assemblies", f.Assemblies }, { "utc", f.Utc },
                { "reloaded_after", LiveBridge.Generation > f.Generation } };
        }

        static Dictionary<string, object> SyncView(CompileJournal.Sync s)
        {
            if (s == null) return null;
            return new Dictionary<string, object> {
                { "sync_generation", s.Gen }, { "compile_started_before_sync", s.Before },
                { "compile_started_after_sync", s.After < 0 ? (object)null : s.After }, { "compiling_after_sync", s.CompilingAfter },
                { "reload_generation", s.Generation }, { "utc", s.Utc } };
        }

        // The small summary every heartbeat carries (wait-ready observes it without sending requests).
        internal static Dictionary<string, object> Summary()
        {
            var j = Journal;
            var last = j.Last;
            var sync = j.LatestSync;
            return new Dictionary<string, object> {
                { "compile_generation", j.Started }, { "completed_compiles", j.Finished }, { "sync_generation", j.SyncGeneration },
                { "last_compile", last == null ? null : new Dictionary<string, object> {
                    { "compile_generation", last.ForStart }, { "reload_generation", last.Generation }, { "errors", last.Errors },
                    { "warnings", last.Warnings } } },
                { "latest_sync", sync == null ? null : new Dictionary<string, object> {
                    { "sync_generation", sync.Gen }, { "compile_started_before_sync", sync.Before } } } };
        }

        static Dictionary<string, object> Limits()
        {
            return new Dictionary<string, object> {
                { "assemblies", CompileJournal.MaxAssemblies }, { "messages", CompileJournal.MaxMessages },
                { "messages_per_assembly", CompileJournal.MaxMessagesPerAssembly }, { "message_chars", CompileText.MaxMessageChars },
                { "recent_compiles", CompileJournal.MaxFinishes }, { "syncs", CompileJournal.MaxSyncs }, { "page", DiagnosticsPage } };
        }

        internal static Dictionary<string, object> Status()
        {
            var j = Journal;
            return new Dictionary<string, object> {
                { "state", LiveBridge.State() }, { "boot_id", LiveBridge.BootId }, { "reload_generation", LiveBridge.Generation },
                { "compile_generation", j.Started }, { "completed_compiles", j.Finished },
                { "last_compile", FinishView(j.Last) }, { "recent_compiles", j.Finishes.Select(f => (object)FinishView(f)).ToList() },
                { "sync_generation", j.SyncGeneration }, { "latest_sync", SyncView(j.LatestSync) },
                { "syncs", j.Syncs.Select(s => (object)SyncView(s)).ToList() },
                { "assemblies", j.Entries.OrderBy(e => e.Assembly, StringComparer.Ordinal).Select(e => (object)new Dictionary<string, object> {
                    { "assembly", e.Assembly }, { "compile_generation", e.CompileGeneration }, { "errors", e.Errors }, { "warnings", e.Warnings },
                    { "kept", e.Messages.Count }, { "dropped", e.Dropped } }).ToList() },
                { "journal", new Dictionary<string, object> {
                    { "started_utc", j.StartedUtc }, { "reset", j.Reset }, { "assemblies", j.Entries.Count },
                    { "messages", j.Entries.Sum(e => e.Messages.Count) }, { "errors", j.Entries.Sum(e => e.Errors) },
                    { "warnings", j.Entries.Sum(e => e.Warnings) }, { "superseded", j.Superseded }, { "limits", Limits() } } } };
        }

        internal static Dictionary<string, object> Diagnostics(Dictionary<string, object> a)
        {
            var j = Journal;
            int? generation = Authoring.Int(a, "generation", 1, int.MaxValue);
            string severity = Authoring.Str(a, "severity");
            if (severity != null && severity != "ERROR" && severity != "WARNING") throw new Refusal("BAD_ARGUMENTS", "severity is ERROR or WARNING");
            string assembly = Authoring.Str(a, "assembly");
            int page = Authoring.Int(a, "page", 0, 10000) ?? 0;
            if (generation.HasValue && !j.Entries.Any(e => e.CompileGeneration == generation.Value))
                throw new Refusal("JOURNAL_FILTER_UNKNOWN", "no retained assembly result is from compile generation " + generation.Value);
            if (assembly != null && !j.Entries.Any(e => e.Assembly == assembly))
                throw new Refusal("JOURNAL_FILTER_UNKNOWN", "the journal holds no result of that assembly");
            var rows = j.Select(generation.HasValue ? (long?)generation.Value : null, severity, assembly);
            int pages = Math.Max(1, (rows.Count + DiagnosticsPage - 1) / DiagnosticsPage);
            var entries = rows.Skip(page * DiagnosticsPage).Take(DiagnosticsPage).Select(r => (object)new Dictionary<string, object> {
                { "assembly", r.Key.Assembly }, { "compile_generation", r.Key.CompileGeneration }, { "severity", r.Value.Severity },
                { "file", r.Value.File }, { "outside_project", r.Value.Outside }, { "line", r.Value.Line }, { "column", r.Value.Column },
                { "message", r.Value.Text }, { "clipped", r.Value.Clipped } }).ToList();
            return new Dictionary<string, object> {
                { "entries", entries }, { "total", rows.Count }, { "page", page }, { "pages", pages }, { "page_size", DiagnosticsPage },
                { "filters", new Dictionary<string, object> { { "generation", generation }, { "severity", severity }, { "assembly", assembly } } },
                { "compile_generation", j.Started }, { "journal_started_utc", j.StartedUtc }, { "journal_reset", j.Reset },
                { "dropped", j.Entries.Where(e => assembly == null || e.Assembly == assembly).Sum(e => e.Dropped) }, { "limits", Limits() } };
        }
    }
}
