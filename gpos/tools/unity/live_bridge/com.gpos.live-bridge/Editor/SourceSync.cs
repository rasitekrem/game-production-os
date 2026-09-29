// GPOS live bridge — source synchronization (bridge 1.4.0): sync-sources tells Unity about exact source paths that
// other programs changed. It takes paths only — never code, file content, a folder, an extension outside .cs, .asmdef
// and .asmref, a method name or an AssetDatabase operation name — and it writes no file itself.
//   existing path  AssetDatabase.ImportAsset(path): exactly that path (Unity may create its .meta);
//   deleted path   AssetDatabase.ImportAsset(folder, ImportRecursive) of its direct parent folder, or — only when that
//                  folder is gone too — of exactly one folder above it; never Assets itself, never higher, never a
//                  global Refresh. A recursive import also imports anything else new or changed in that folder, so
//                  the folder is snapshotted before and after — a bounded, link-free walk of its files, and for each
//                  entry that walk visits (never a project-wide query) whether the AssetDatabase knows it — and every
//                  difference within that bound is reported. Stale database entries whose files are already gone are
//                  not enumerable within the bound; Unity may reconcile them too, and the result says so.
// Everything is validated, and every snapshot taken, before the first import: a request is refused with nothing
// imported, or it imports. The causal baseline (compile_started_before_sync) is recorded before the first import.
// Compilation and Domain Reload follow as Unity decides; this command neither requests nor waits for them.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;

namespace Gpos.LiveBridge
{
    internal static class SourceSync
    {
        public const int MaxEntries = 1000;
        public const int MaxDepth = 6;
        public const int MaxListed = 100;
        public const long MaxMetaHashBytes = 1024 * 1024;
        public const string Disclosure =
            "A recursive folder import also imports anything else new or changed in that folder. Listed, within the bounded walk of the " +
            "folder's files: every file or folder Unity started or stopped knowing, and every .meta file created, removed or changed; the " +
            "requested deletions are checked by their exact paths. Unity may also remove stale database entries whose files were already " +
            "gone before the sync; they are not enumerable within the bound and are not listed. Which unchanged files Unity re-read is not exposed.";

        enum Kind { Missing, File, Folder, Link, Other }

        static string Project { get { return LiveBridge.Place.ProjectPath; } }

        static string Full(string rel) { return Path.Combine(Project, rel); }

        static string Rel(string full) { return full.Substring(Project.Length).TrimStart('/').Replace('\\', '/'); }

        static bool Known(string rel) { return AssetDatabase.AssetPathToGUID(rel, AssetPathToGUIDOptions.OnlyExistingAssets) != ""; }

        // Walks a project-relative path component by component with its exact spelling: the kind of the whole path, or
        // Missing with the index of the first component that is absent. A different spelling of a present name is Other.
        static Kind Probe(string rel, out int missingAt, out bool spelling)
        {
            missingAt = -1;
            spelling = false;
            string cur = Project;
            var parts = rel.Split('/');
            for (int i = 0; i < parts.Length; i++)
            {
                string[] names;
                try { names = Directory.GetFileSystemEntries(cur).Select(Path.GetFileName).ToArray(); }
                catch (Exception) { return Kind.Other; }
                if (!names.Contains(parts[i], StringComparer.Ordinal))
                {
                    if (names.Contains(parts[i], StringComparer.OrdinalIgnoreCase)) { spelling = true; return Kind.Other; }
                    missingAt = i;
                    return Kind.Missing;
                }
                cur = Path.Combine(cur, parts[i]);
                if (Identity.IsLink(cur)) return Kind.Link;
                bool folder = Directory.Exists(cur);
                if (i < parts.Length - 1 && !folder) return Kind.Other;
                if (i == parts.Length - 1) return folder ? Kind.Folder : File.Exists(cur) ? Kind.File : Kind.Other;
            }
            return Kind.Other;
        }

        static Refusal Refused(string why) { return new Refusal("SOURCE_SYNC_REFUSED", why) { Data = NotStarted() }; }

        static Dictionary<string, object> NotStarted() { return new Dictionary<string, object> { { "mutation_started", false } }; }

        // ------------------------------------------------------------ snapshots (bounded, link-free, before and after)

        sealed class Snap
        {
            public readonly Dictionary<string, string> Metas = new Dictionary<string, string>(StringComparer.Ordinal);
            public readonly HashSet<string> Known = new HashSet<string>(StringComparer.Ordinal);   // walked entries the database knows
            public int Entries, Inspected;
        }

        static string MetaHash(string full)
        {
            var info = new FileInfo(full);
            return info.Length > MaxMetaHashBytes ? "size:" + info.Length : Identity.Sha256(File.ReadAllBytes(full));
        }

        // One bounded, link-free walk of the folder: every entry counts toward the bound, every .meta is hashed, and each
        // file or folder the walk visits (and the folder itself) is looked up in the AssetDatabase by its own path. Nothing
        // outside the walk is ever asked for.
        static Snap Take(string root, int maxEntries)
        {
            var s = new Snap();
            KnownCheck(s, root);
            var stack = new Stack<KeyValuePair<string, int>>();
            stack.Push(new KeyValuePair<string, int>(Full(root), 0));
            while (stack.Count > 0)
            {
                var at = stack.Pop();
                foreach (var entry in Directory.GetFileSystemEntries(at.Key).OrderBy(e => e, StringComparer.Ordinal))
                {
                    if (++s.Entries > maxEntries)
                        throw new Refusal("SOURCE_SYNC_LIMIT", root + " holds more than " + maxEntries + " entries; nothing was imported") { Data = NotStarted() };
                    if (Identity.IsLink(entry)) throw Refused("a link inside " + root + " (" + Rel(entry) + "); nothing was imported");
                    if (Directory.Exists(entry))
                    {
                        if (at.Value + 1 > MaxDepth)
                            throw new Refusal("SOURCE_SYNC_LIMIT", root + " is more than " + MaxDepth + " folders deep; nothing was imported") { Data = NotStarted() };
                        stack.Push(new KeyValuePair<string, int>(entry, at.Value + 1));
                        KnownCheck(s, Rel(entry));
                    }
                    else if (entry.EndsWith(".meta", StringComparison.Ordinal)) s.Metas[Rel(entry)] = MetaHash(entry);
                    else KnownCheck(s, Rel(entry));
                }
            }
            return s;
        }

        static void KnownCheck(Snap s, string rel)
        {
            s.Inspected++;
            if (Known(rel)) s.Known.Add(rel);
        }

        static void Listed(Dictionary<string, object> d, string key, IEnumerable<string> items)
        {
            var all = items.OrderBy(x => x, StringComparer.Ordinal).ToList();
            d[key] = all.Take(MaxListed).Cast<object>().ToList();
            d[key + "_count"] = all.Count;
        }

        // ------------------------------------------------------------ sync-sources

        sealed class Deletion
        {
            public string Path, Root, GoneFolder;
            public bool Widened, AlreadySynchronized, GoneFolderKnown;
        }

        sealed class Host : ISyncHost
        {
            public long Gen = -1;
            public long CompileStarted { get { return Compilation.Journal.Started; } }
            public void Begin(long before) { Gen = Compilation.BeginSync(before); }
            public void ImportExact(string path) { AssetDatabase.ImportAsset(path); }
            public void ImportRecursive(string folder) { AssetDatabase.ImportAsset(folder, ImportAssetOptions.ImportRecursive); }
        }

        sealed class Checked
        {
            public SyncPlan Plan;
            public List<Dictionary<string, object>> Sources = new List<Dictionary<string, object>>();
            public List<Deletion> Deletions = new List<Deletion>();
            public List<string> Roots;
            public Dictionary<string, Snap> Before;
        }

        public static Dictionary<string, object> Run(Request r)
        {
            Checked c;
            try { c = Check(r); }
            catch (Refusal refusal)
            {
                if (refusal.Data == null) refusal.Data = NotStarted();
                throw;
            }
            catch (Exception e) { throw Refused("the sources or folders could not be read (" + e.GetType().Name + "); nothing was imported"); }
            return Import(c);
        }

        // Every check and every snapshot, before anything is imported.
        static Checked Check(Request r)
        {
            var c = new Checked { Plan = SyncPlan.Parse(r.Args["sources"], r.Args["deleted"]) };
            var plan = c.Plan;

            // existing sources: exact spelling, real folders, a regular file within the size bound
            var sources = c.Sources;
            foreach (var p in plan.Sources)
            {
                int missing;
                bool spelling;
                Kind k = Probe(p, out missing, out spelling);
                if (spelling) throw new Refusal("SOURCE_PATH_INVALID", p + ": the spelling differs from the file on disk") { Data = NotStarted() };
                if (k == Kind.Missing) throw Refused(p + " does not exist; name a deleted source in deleted");
                if (k != Kind.File) throw Refused(p + " is not a regular file below real folders (a link, a folder or something else)");
                if (new FileInfo(Full(p)).Length > SourcePaths.MaxSourceBytes)
                    throw new Refusal("SOURCE_SYNC_LIMIT", p + " is larger than " + SourcePaths.MaxSourceBytes + " bytes") { Data = NotStarted() };
                sources.Add(new Dictionary<string, object> { { "path", p }, { "known_before", Known(p) }, { "meta_existed_before", File.Exists(Full(p) + ".meta") } });
            }

            // deleted sources: really absent, and the one folder that synchronizes each of them
            var deletions = c.Deletions;
            foreach (var p in plan.Deleted)
            {
                int missing;
                bool spelling;
                Kind k = Probe(p, out missing, out spelling);
                if (spelling || k != Kind.Missing) throw Refused(p + " still exists (or a folder on its path is not a real folder); only absent sources are deleted");
                var parts = p.Split('/');
                var d = new Deletion { Path = p };
                if (SourcePaths.DirectParent(p) == null) throw Refused(p + " is directly in Assets/; the Assets root is never imported recursively");
                if (missing == parts.Length - 1) d.Root = SourcePaths.DirectParent(p);
                else if (missing == parts.Length - 2)
                {
                    d.Root = SourcePaths.Widened(p);
                    d.Widened = true;
                    d.GoneFolder = SourcePaths.DirectParent(p);
                    if (d.Root == null) throw Refused(p + ": its folder is gone and the folder above it is Assets, which is never imported recursively");
                }
                else throw Refused(p + ": more than one folder level above it is gone; only one level is ever widened");
                d.AlreadySynchronized = !Known(p);
                d.GoneFolderKnown = d.GoneFolder != null && Known(d.GoneFolder);
                deletions.Add(d);
            }
            var roots = c.Roots = SourcePaths.Roots(deletions.Where(d => !d.AlreadySynchronized).Select(d => d.Root));
            foreach (var root in roots)
            {
                int missing;
                bool spelling;
                if (Probe(root, out missing, out spelling) != Kind.Folder || spelling) throw Refused(root + " is not a real folder");
            }
            c.Before = roots.ToDictionary(root => root, root => Take(root, MaxEntries));
            return c;
        }

        static Dictionary<string, object> Import(Checked c)
        {
            List<Dictionary<string, object>> sources = c.Sources;
            List<Deletion> deletions = c.Deletions;
            List<string> roots = c.Roots;
            var before = c.Before;
            var data = new Dictionary<string, object> { { "mutation_started", false }, { "sync_generation", null }, { "imports", 0 } };
            if (sources.Count == 0 && roots.Count == 0) return Result(data, sources, deletions, roots, before, null);

            // ---- from here the request imports: every refusal below reports mutation_started
            var host = new Host();
            var marks = new SyncMarks();
            try
            {
                SyncRun.Run(host, c.Plan.Sources, roots, marks);
                Compilation.EndSync(host.Gen, marks.After, EditorApplication.isCompiling);
            }
            catch (Exception e)
            {
                throw new Refusal("SOURCE_SYNC_INCOMPLETE", "an import did not complete (" + e.GetType().Name + "); the sync is not retried") {
                    Status = "FAILED", Data = new Dictionary<string, object> { { "mutation_started", marks.Began }, { "sync_generation", host.Gen < 0 ? (object)null : host.Gen },
                                                                              { "compile_started_before_sync", marks.Before }, { "imports", marks.Imports } } };
            }
            data["mutation_started"] = true;
            data["sync_generation"] = host.Gen;
            data["imports"] = marks.Imports;
            data["compile_started_before_sync"] = marks.Before;
            data["compile_started_after_sync"] = marks.After;
            data["compiling_after_sync"] = EditorApplication.isCompiling;
            data["reload_generation"] = LiveBridge.Generation;
            Dictionary<string, Snap> after;
            try
            {
                after = roots.ToDictionary(root => root, root => Take(root, 2 * MaxEntries));
            }
            catch (Exception e)
            {
                throw new Refusal("SOURCE_SYNC_INCOMPLETE", "the folder could not be snapshotted after the import: " + (e is Refusal ? e.Message : e.GetType().Name)) {
                    Status = "FAILED", Data = data };
            }
            var result = Result(data, sources, deletions, roots, before, after);
            var unsynced = deletions.Where(d => !d.AlreadySynchronized && Known(d.Path)).Select(d => d.Path).ToList();
            var unknown = sources.Where(s => !(bool)s["known_after"]).Select(s => (string)s["path"]).ToList();
            if (unsynced.Count > 0 || unknown.Count > 0)
                throw new Refusal("SOURCE_SYNC_INCOMPLETE", "Unity " + (unsynced.Count > 0 ? "still knows the deleted " + string.Join(", ", unsynced) : "did not import " + string.Join(", ", unknown)) +
                                                            "; the sync is not retried") { Status = "FAILED", Data = result };
            return result;
        }

        static Dictionary<string, object> Result(Dictionary<string, object> data, List<Dictionary<string, object>> sources, List<Deletion> deletions,
                                                 List<string> roots, Dictionary<string, Snap> before, Dictionary<string, Snap> after)
        {
            foreach (var s in sources)
            {
                string p = (string)s["path"];
                s["known_after"] = after == null && !(bool)data["mutation_started"] ? (bool)s["known_before"] : Known(p);
                s["meta_created"] = !(bool)s["meta_existed_before"] && File.Exists(Full(p) + ".meta");
            }
            data["sources"] = sources.Cast<object>().ToList();
            data["deleted"] = deletions.Select(d => (object)new Dictionary<string, object> {
                { "path", d.Path }, { "state", d.AlreadySynchronized ? "ALREADY_SYNCHRONIZED" : after == null ? "NOT_IMPORTED" : Known(d.Path) ? "STILL_KNOWN" : "SYNCHRONIZED" },
                { "folder", d.AlreadySynchronized ? null : roots.First(root => SourcePaths.Within(d.Root, root)) }, { "parent_widened", d.Widened && !d.AlreadySynchronized } }).ToList();
            var folders = new List<object>();
            foreach (var root in roots)
            {
                var f = new Dictionary<string, object> { { "folder", root }, { "parent_widened", deletions.Any(d => !d.AlreadySynchronized && d.Widened && d.Root == root) },
                                                         { "entries_before", before[root].Entries }, { "known_inspected_before", before[root].Inspected } };
                if (after != null)
                {
                    Snap b = before[root], a = after[root];
                    f["entries_after"] = a.Entries;
                    f["known_inspected_after"] = a.Inspected;
                    var mine = deletions.Where(d => !d.AlreadySynchronized && SourcePaths.Within(d.Root, root)).ToList();
                    Listed(f, "requested_removed", mine.Where(d => !Known(d.Path)).Select(d => d.Path)
                                                      .Concat(mine.Where(d => d.GoneFolderKnown && !Known(d.GoneFolder)).Select(d => d.GoneFolder)).Distinct());
                    Listed(f, "imported_new", a.Known.Where(x => !b.Known.Contains(x)));
                    Listed(f, "removed", b.Known.Where(x => !a.Known.Contains(x)));
                    Listed(f, "meta_created", a.Metas.Keys.Where(x => !b.Metas.ContainsKey(x)));
                    Listed(f, "meta_removed", b.Metas.Keys.Where(x => !a.Metas.ContainsKey(x)));
                    Listed(f, "meta_changed", a.Metas.Where(x => b.Metas.ContainsKey(x.Key) && b.Metas[x.Key] != x.Value).Select(x => x.Key));
                }
                folders.Add(f);
            }
            data["folders"] = folders;
            data["disclosure"] = roots.Count > 0 ? Disclosure : null;
            return data;
        }
    }
}
