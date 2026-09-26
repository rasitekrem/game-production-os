// GPOS live bridge — local file IPC in .game/gpos-runtime/unity/live/<project-key>/.
// GPOS publishes requests/<id>.json atomically (dot-temp file, then link). The bridge claims a request by renaming
// it into claimed/ (exactly one claim), answers by linking responses/<id>.json (exactly one response, never
// replaced), and quarantines bad names, links and duplicates into rejected/. GPOS may withdraw an unclaimed request
// by renaming it into withdrawn/: the rename that wins decides, so a withdrawn request never runs. Every folder is
// bounded by age and count. There is no socket, listener or network anywhere.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;

namespace Gpos.LiveBridge
{
    internal static class Ipc
    {
        public const int MaxPerTick = 4;
        public const int TickBudgetMs = 25;
        public const int RetentionMinutes = 10;
        public const int MaxFilesPerFolder = 256;
        public const int MaxEventsBytes = 256 * 1024;
        static readonly Regex RequestName = new Regex("^[0-9a-f]{32}\\.json$");
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        [DllImport("libc", SetLastError = true)] static extern int rename(string from, string to);
        [DllImport("libc", SetLastError = true)] static extern int link(string existing, string created);
        [DllImport("libc", SetLastError = true)] static extern int unlink(string path);
        [DllImport("libc", SetLastError = true)] static extern int chmod(string path, int mode);

        public static string Live, Requests, Claimed, Responses, Withdrawn, Rejected;

        public static bool Init(string liveDir)
        {
            Live = liveDir;
            Requests = Path.Combine(Live, "requests");
            Claimed = Path.Combine(Live, "claimed");
            Responses = Path.Combine(Live, "responses");
            Withdrawn = Path.Combine(Live, "withdrawn");
            Rejected = Path.Combine(Live, "rejected");
            foreach (var d in new[] { Live, Requests, Claimed, Responses, Withdrawn, Rejected })
            {
                Directory.CreateDirectory(d);
                if (Identity.IsLink(d)) return false;
            }
            chmod(Live, Convert.ToInt32("700", 8));
            return true;
        }

        static void WriteNew(string path, string text)
        {
            byte[] bytes = Utf8.GetBytes(text);
            using (var fs = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
            {
                fs.Write(bytes, 0, bytes.Length);
                fs.Flush(true);
            }
        }

        static string Temp(string dir) { return Path.Combine(dir, ".tmp-" + Guid.NewGuid().ToString("N")); }

        public static void AtomicReplace(string path, string text)
        {
            string tmp = Temp(Path.GetDirectoryName(path));
            WriteNew(tmp, text);
            if (rename(tmp, path) != 0)
            {
                unlink(tmp);
                throw new IOException("rename failed: " + Marshal.GetLastWin32Error());
            }
        }

        // link() fails when the name exists, so a response is published at most once and never replaced.
        public static bool PublishOnce(string path, string text)
        {
            string tmp = Temp(Path.GetDirectoryName(path));
            WriteNew(tmp, text);
            int rc = link(tmp, path);
            unlink(tmp);
            return rc == 0;
        }

        public static void Event(string what)
        {
            if (Live == null) return;
            try
            {
                string path = Path.Combine(Live, "events.jsonl");
                if (File.Exists(path) && new FileInfo(path).Length > MaxEventsBytes)
                    rename(path, Path.Combine(Live, "events.1.jsonl"));
                File.AppendAllText(path, Json.Write(new Dictionary<string, object> {
                    { "utc", DateTime.UtcNow.ToString("o") }, { "boot_id", LiveBridge.BootId },
                    { "generation", LiveBridge.Generation }, { "event", what } }) + "\n");
            }
            catch { }
        }

        public static void Respond(string id, string status, string code, string message, Dictionary<string, object> data)
        {
            var r = new Dictionary<string, object> {
                { "schema", Protocol.ResponseSchema }, { "request_id", id }, { "status", status }, { "code", code },
                { "message", message }, { "boot_id", LiveBridge.BootId }, { "generation", LiveBridge.Generation },
                { "session_id", LiveBridge.SessionId }, { "utc", DateTime.UtcNow.ToString("o") }, { "data", data } };
            string text = Json.Write(r);
            if (Utf8.GetByteCount(text) > Protocol.MaxResponseBytes)
            {
                r["status"] = "FAILED"; r["code"] = "RESPONSE_TOO_LARGE"; r["message"] = "the response exceeded its bound"; r["data"] = null;
                text = Json.Write(r);
            }
            bool published = PublishOnce(Path.Combine(Responses, id + ".json"), text);
            Event((published ? "responded:" : "response-exists:") + id + ":" + status + (code == null ? "" : ":" + code));
        }

        // Anything claimed and never answered was interrupted by a Domain Reload or an Editor restart. It is answered
        // INTERRUPTED and never executed again, except the one Play Mode transition this Editor session still observes.
        public static void RecoverClaimed(string pendingId)
        {
            foreach (var f in Directory.GetFiles(Claimed, "*.json"))
            {
                string id = Path.GetFileNameWithoutExtension(f);
                if (id == pendingId || File.Exists(Path.Combine(Responses, id + ".json"))) continue;
                Respond(id, "INTERRUPTED", "NOT_REPLAYED",
                        "the request was claimed but not answered before a Domain Reload or Editor restart; its effect is unknown and it is never executed again",
                        null);
            }
        }

        static void Quarantine(string path, string reason)
        {
            rename(path, Path.Combine(Rejected, reason + "-" + Guid.NewGuid().ToString("N")));
            Event("rejected:" + reason);
        }

        public static void Serve(Action<string, byte[]> handle)
        {
            string[] files;
            try { files = Directory.GetFiles(Requests); } catch { return; }
            var watch = Stopwatch.StartNew();
            var ordered = files.Where(f => !Path.GetFileName(f).StartsWith(".", StringComparison.Ordinal))
                               .OrderBy(f => File.GetLastWriteTimeUtc(f)).ThenBy(f => f, StringComparer.Ordinal).Take(MaxPerTick);
            foreach (var f in ordered)
            {
                if (watch.ElapsedMilliseconds > TickBudgetMs) break;
                string name = Path.GetFileName(f);
                if (!RequestName.IsMatch(name)) { Quarantine(f, "bad-name"); continue; }
                if (Identity.IsLink(f)) { Quarantine(f, "link"); continue; }
                string id = name.Substring(0, 32);
                if (File.Exists(Path.Combine(Responses, name)) || File.Exists(Path.Combine(Claimed, name)))
                {
                    Quarantine(f, "duplicate-" + id);
                    continue;
                }
                string claim = Path.Combine(Claimed, name);
                if (rename(f, claim) != 0) continue;   // withdrawn by GPOS or already taken: never both
                Event("claimed:" + id);
                byte[] bytes;
                try
                {
                    if (new FileInfo(claim).Length > Protocol.MaxRequestBytes)
                    {
                        Respond(id, "REFUSED", "REQUEST_TOO_LARGE", "the request exceeds " + Protocol.MaxRequestBytes + " bytes", null);
                        continue;
                    }
                    bytes = File.ReadAllBytes(claim);
                }
                catch (Exception e)
                {
                    Respond(id, "FAILED", "BRIDGE_INTERNAL_ERROR", e.GetType().Name, null);
                    continue;
                }
                handle(id, bytes);
            }
        }

        public static string Text(byte[] bytes)
        {
            try { return Utf8.GetString(bytes); }
            catch (ArgumentException) { return null; }
        }

        // Bounded retention: nothing older than RetentionMinutes, and at most MaxFilesPerFolder per folder. The journal,
        // not these files, guarantees at-most-once, so deleting old files never re-opens a request.
        public static void Retention()
        {
            DateTime cutoff = DateTime.UtcNow.AddMinutes(-RetentionMinutes);
            foreach (var d in new[] { Claimed, Responses, Withdrawn, Rejected })
            {
                try
                {
                    var files = new DirectoryInfo(d).GetFiles().OrderBy(fi => fi.LastWriteTimeUtc).ToList();
                    int excess = files.Count - MaxFilesPerFolder;
                    foreach (var fi in files)
                    {
                        if (fi.LastWriteTimeUtc >= cutoff && excess <= 0) continue;
                        unlink(fi.FullName);
                        excess--;
                    }
                }
                catch { }
            }
        }
    }
}
