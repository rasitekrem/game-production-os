// TEST-ONLY (alpha.26): the Windows view of the bridge's Unity-free core — the Windows command allowlist, the Windows
// path spelling (WindowsPaths) and the NTFS primitives (WindowsFiles) against real files. Compiled only with
// UNITY_EDITOR_WIN by tests/test_unity_live_bridge_core.py, with Unity's bundled Mono on Windows; no Unity process runs.
// The cross-process claim-versus-withdraw races are tests/test_unity_windows_live.py (the bridge and GPOS are separate
// processes there).
#if UNITY_EDITOR_WIN
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Gpos.LiveBridge
{
    static partial class CoreTests
    {
        static readonly string[] WindowsServed = { "status", "propose-attach", "attach-status", "abandon-proposal", "bind",
                                                   "propose-recovery", "recovery-status", "consume-recovery", "unbind",
                                                   "inspect", "object-inspect", "create-gameobject", "set-transform", "save-scene" };

        static string ArgsOf(string command)
        {
            var spec = new Dictionary<string, string> {
                { "status", "{}" }, { "inspect", "{}" }, { "unbind", "{}" },
                { "propose-attach", "{\"proposal_id\":null,\"session_id\":null,\"project_key\":null,\"expires_s\":null}" },
                { "attach-status", "{\"proposal_id\":null}" }, { "abandon-proposal", "{\"proposal_id\":null}" },
                { "bind", "{\"proposal_id\":null,\"session_id\":null}" },
                { "propose-recovery", "{\"proposal_id\":null,\"project_key\":null,\"stale_session_id\":null,\"expires_s\":null}" },
                { "recovery-status", "{\"proposal_id\":null}" }, { "consume-recovery", "{\"proposal_id\":null}" },
                { "object-inspect", "{\"object\":null,\"scene\":null,\"children_limit\":null}" },
                { "create-gameobject", "{\"scene\":null,\"name\":null,\"parent\":null,\"sibling\":null,\"local_position\":null," +
                                       "\"local_rotation\":null,\"local_scale\":null,\"expected_parent_token\":null,\"expected_scene_roots_token\":null}" },
                { "set-transform", "{\"object\":null,\"local_position\":null,\"local_rotation\":null,\"local_scale\":null,\"expected_transform_token\":null}" },
                { "save-scene", "{\"scene\":null}" } };
            return spec[command];
        }

        static readonly HashSet<string> SessionFree = new HashSet<string> {
            "status", "propose-attach", "attach-status", "abandon-proposal", "bind", "propose-recovery", "recovery-status", "consume-recovery" };

        static void WindowsServesExactlyTheQualifiedSlice()
        {
            Equal(14, Protocol.WindowsCommands.Length, "served commands");
            Check(Protocol.WindowsCommands.OrderBy(c => c).SequenceEqual(WindowsServed.OrderBy(c => c)), "the served set");
            Check(Protocol.WindowsCommands.All(c => Protocol.Commands.Contains(c)), "served commands are protocol commands");
            int n = 100;
            foreach (var c in WindowsServed)
            {
                var r = Parse(Id(++n), Req(Id(n), c, ArgsOf(c), SessionFree.Contains(c) ? null : Sid));
                Equal(c, r.Command, c + " parses");
            }
            foreach (var c in Protocol.Commands.Where(c => !WindowsServed.Contains(c)))
                Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(++n), Req(Id(n), c, "{}", Sid))), c + " is not served on Windows");
            Equal(46, Protocol.Commands.Count(), "protocol commands");
            Equal(32, Protocol.Commands.Count(c => !WindowsServed.Contains(c)), "commands refused on Windows");
        }

        static void WindowsRefusalsKeepTheirPrecedence()
        {
            // the protocol checks before the command keep their codes on Windows; the allowlist comes right after the
            // command lookup, before the arguments, the session and the journal
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), "{\"schema\":1")), "garbled");
            Equal("UNSUPPORTED_SCHEMA", Code(() => Parse(Id(1), Req(Id(1), schema: "gpos.unity.live.request/0"))), "schema");
            Equal("REQUEST_ID_MISMATCH", Code(() => Parse(Id(2), Req(Id(1)))), "file name binding");
            Equal("BOOT_MISMATCH", Code(() => Parse(Id(1), Req(Id(1), boot: Id(9)))), "boot binding");
            Equal("LIVE_REQUEST_EXPIRED", Code(() => Parse(Id(1), Req(Id(1), window: 5), T0 + S(6))), "expired");
            Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(1), Req(Id(1), "execute-method"))), "closed allowlist");
            Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(1), Req(Id(1), "pause", "{\"bad\":1}"))), "not served, before arguments");
            Equal("BAD_ARGUMENTS", Code(() => Parse(Id(1), Req(Id(1), "status", "{\"depth\":3}"))), "extra argument");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), "inspect"))), "session required");
            Equal("MALFORMED_REQUEST", Code(() => Parse(Id(1), Req(Id(1), "status", "{}", Sid))), "no session for status");
        }

        static string Scratch()
        {
            string d = Path.Combine(Path.GetTempPath(), "gpos-core-win-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(d);
            return WindowsPaths.Canonical(Path.GetFullPath(d)) ?? d;
        }

        static void WindowsPathsSpellLikeGpos()
        {
            string root = Scratch();
            try
            {
                string project = Path.Combine(root, "Unity Project", "Game");
                Directory.CreateDirectory(project);
                Equal(project, WindowsPaths.Canonical(project), "an exact spelling is kept");
                Equal(project, WindowsPaths.Canonical(project.ToUpperInvariant().Replace(":\\", ":\\")), "the on-disk case is restored");
                Equal(char.ToUpperInvariant(project[0]) + project.Substring(1), WindowsPaths.Canonical(char.ToLowerInvariant(project[0]) + project.Substring(1)), "drive letter");
                Equal("Unity Project/Game", WindowsPaths.Relative(root, project), "relative with '/'");
                Equal(".", WindowsPaths.Relative(root, root), "the root itself");
                Equal(null, WindowsPaths.Relative(root, root + "-sibling"), "a sibling with the root as prefix is not inside");
                Equal(null, WindowsPaths.Relative(project, root), "outside");
                Equal(null, WindowsPaths.Canonical(project.Replace('\\', '/')), "mixed separators");
                Equal(null, WindowsPaths.Canonical("\\\\server\\share\\x"), "UNC");
                Equal(null, WindowsPaths.Canonical("\\\\?\\" + project), "device path");
                Equal(null, WindowsPaths.Canonical(project + ":stream"), "alternate data stream");
                Equal(null, WindowsPaths.Canonical(Path.Combine(root, "missing")), "a missing component");
                Equal(null, WindowsPaths.Canonical(Path.Combine(root, "Unity Project", "..", "Unity Project")), "dot components");
                Check(!WindowsPaths.IsReparse(Path.Combine(root, "missing")), "a missing path is not a link");
                Check(!WindowsPaths.IsReparse(project), "a directory is not a link");
            }
            finally { Directory.Delete(root, true); }
        }

        static void WindowsFilesMoveNeverReplaces()
        {
            string d = Scratch();
            try
            {
                string a = Path.Combine(d, "a"), b = Path.Combine(d, "b"), c = Path.Combine(d, "c");
                File.WriteAllText(a, "A"); File.WriteAllText(b, "B");
                Equal(WindowsFiles.ErrorAlreadyExists, WindowsFiles.MoveNoReplace(a, b), "never onto an existing name");
                Equal("A", File.ReadAllText(a), "source intact"); Equal("B", File.ReadAllText(b), "target intact");
                Equal(0, WindowsFiles.MoveNoReplace(a, c), "onto a new name");
                Equal(WindowsFiles.ErrorFileNotFound, WindowsFiles.MoveNoReplace(a, Path.Combine(d, "x")), "a missing source");
                File.WriteAllText(a, "new");
                Equal(0, WindowsFiles.Replace(a, b), "replace");
                Equal("new", File.ReadAllText(b), "replaced"); Check(!File.Exists(a), "temp consumed");
                File.WriteAllText(a, "newer");
                using (var reader = new FileStream(b, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                    Equal(WindowsFiles.ErrorAccessDenied, WindowsFiles.Replace(a, b), "a held target is not replaced (bounded retries)");
                Equal("new", File.ReadAllText(b), "the held target is unchanged");
                Equal(0, WindowsFiles.Replace(a, b), "replaced once released");
                Check(WindowsFiles.Delete(b) && !File.Exists(b), "delete");
                Check(WindowsFiles.Delete(b), "deleting a missing file");
            }
            finally { Directory.Delete(d, true); }
        }

        static void WindowsPinDecidesTheClaim()
        {
            string d = Scratch();
            try
            {
                string claimed = Path.Combine(d, "claimed.json");
                FileStream pin;
                Equal(WindowsFiles.Claim.Lost, WindowsFiles.Pin(claimed, 5, out pin), "moved away: lost, untouched");
                File.WriteAllText(claimed, "{\"request_id\":\"x\"}");
                Equal(WindowsFiles.Claim.Won, WindowsFiles.Pin(claimed, 5, out pin), "a plain file is pinned");
                using (pin)
                {
                    Equal(WindowsFiles.ErrorSharingViolation, WindowsFiles.MoveNoReplace(claimed, claimed + ".w"),
                          "while pinned, no rename can start");
                    Equal("{\"request_id\":\"x\"}", System.Text.Encoding.UTF8.GetString(WindowsFiles.ReadPinned(pin, 1024)), "read through the pin");
                }
                using (var renamer = new FileStream(claimed, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    // a writer (or a rename handle, which holds delete access) cannot coexist with the pin's share mode
                    using (var writer = new FileStream(claimed, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete))
                        Equal(WindowsFiles.Claim.Undecided, WindowsFiles.Pin(claimed, 30, out pin), "held by a writer: undecided");
                }
                Directory.CreateDirectory(Path.Combine(d, "dir.json"));
                Equal(WindowsFiles.Claim.Undecided, WindowsFiles.Pin(Path.Combine(d, "dir.json"), 5, out pin), "a directory is never a claim");
                var big = new byte[70000];
                File.WriteAllBytes(claimed, big);
                Equal(WindowsFiles.Claim.Won, WindowsFiles.Pin(claimed, 5, out pin), "pinned");
                using (pin) Equal(1025, WindowsFiles.ReadPinned(pin, 1024).Length > 1024 ? 1025 : 0, "an oversized request is seen as such");
            }
            finally { Directory.Delete(d, true); }
        }

        static void WindowsSharedReadNeverBlocksAReplacement()
        {
            string d = Scratch();
            try
            {
                string lease = Path.Combine(d, "lease.json"), tmp = Path.Combine(d, "tmp");
                Equal(null, WindowsFiles.ReadShared(lease, 64), "absent");
                File.WriteAllText(lease, "old");
                Equal("old", System.Text.Encoding.UTF8.GetString(WindowsFiles.ReadShared(lease, 64)), "read");
                using (var held = new FileStream(lease, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    File.Delete(lease);                     // a reader sharing delete never stops the owner removing it
                    Check(!File.Exists(lease), "removed while read");
                }
            }
            finally { Directory.Delete(d, true); }
        }
    }
}
#endif
