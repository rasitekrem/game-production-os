// TEST-ONLY (alpha.26): the bridge side of the Windows live IPC, for tests/test_unity_windows_live.py. Compiled with
// UNITY_EDITOR_WIN together with the production gpos/tools/unity/live_bridge/.../Editor/Core/WindowsFiles.cs, using
// the Mono bundled with the installed Unity Editor; no Unity process runs. It claims exactly as Ipc.Serve does on
// Windows (WindowsFiles.TakeRequest: RENAME -> PIN -> VERIFY -> DECIDE), records an executed request in effects.log
// (so a double execution is visible on disk) and answers it publish-once as Ipc.PublishOnce does.
// One command per stdin line (tab-separated), one result line each:
//   take <live> <id> <qpc> <pin ms>    claim requests/<id>.json and, when won, "execute" and answer it
//   now                                the QueryPerformanceCounter value (the race clock shared with Python)
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Gpos.LiveBridge
{
    static class IpcHarness
    {
        [DllImport("kernel32.dll")] static extern bool QueryPerformanceCounter(out long count);
        static long Qpc() { long c; QueryPerformanceCounter(out c); return c; }

        static void Answer(string live, string id, string status)
        {
            string dir = Path.Combine(live, "responses"), tmp = Path.Combine(dir, ".tmp-" + Guid.NewGuid().ToString("N"));
            byte[] b = Encoding.UTF8.GetBytes("{\"request_id\":\"" + id + "\",\"status\":\"" + status + "\"}");
            using (var fs = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write)) { fs.Write(b, 0, b.Length); fs.Flush(true); }
            if (WindowsFiles.MoveNoReplaceRetried(tmp, Path.Combine(dir, id + ".json")) != 0) WindowsFiles.Delete(tmp);
        }

        static string Take(string live, string id, long qpc, int pinMs)
        {
            string request = Path.Combine(live, "requests", id + ".json"), claimed = Path.Combine(live, "claimed", id + ".json");
            while (Qpc() < qpc) { }
            byte[] bytes;
            var taken = WindowsFiles.TakeRequest(request, claimed, 64 * 1024, pinMs, out bytes);
            if (taken == WindowsFiles.Take.Won)
            {
                string text = Encoding.UTF8.GetString(bytes);
                if (!text.Contains("\"request_id\": \"" + id + "\"")) return "REJECTED";
                File.AppendAllText(Path.Combine(live, "effects.log"), id + "\n");          // the side effect
                Answer(live, id, "OK");
            }
            else if (taken == WindowsFiles.Take.Undecided)
                Answer(live, id, "INTERRUPTED");                                           // never executed
            return taken.ToString();
        }

        static int Main()
        {
            Console.Out.WriteLine("READY " + System.Diagnostics.Process.GetCurrentProcess().Id);
            Console.Out.Flush();
            string line;
            while ((line = Console.In.ReadLine()) != null)
            {
                string[] p = line.Split('\t');
                string r;
                try
                {
                    if (p[0] == "take") r = Take(p[1], p[2], long.Parse(p[3]), int.Parse(p[4]));
                    else if (p[0] == "now") r = Qpc().ToString();
                    else if (p[0] == "quit") return 0;
                    else r = "UNKNOWN_COMMAND";
                }
                catch (Exception e) { r = "EXC " + e.GetType().Name + " " + e.Message; }
                Console.Out.WriteLine(r);
                Console.Out.Flush();
            }
            return 0;
        }
    }
}
