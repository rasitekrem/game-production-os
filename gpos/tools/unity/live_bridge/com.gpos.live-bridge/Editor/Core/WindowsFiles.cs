// GPOS live bridge — the Windows file primitives of the live IPC (bridge 1.6.0; compiled only in a Windows Editor).
// One native function, kernel32.dll!MoveFileExW, with exactly two flag sets, as measured on local NTFS (Phase 2C-9.3b N1):
//   0                                          a same-volume move that never replaces: ERROR_ALREADY_EXISTS when the name
//                                              exists, ERROR_NOT_SAME_DEVICE across volumes (nothing is copied)
//   MOVEFILE_REPLACE_EXISTING | WRITE_THROUGH  the atomic replacement of a state file: a reader sees the old or the new
//                                              file, never a missing or partial one. It fails with ERROR_ACCESS_DENIED
//                                              while any reader holds the target, so it is retried, bounded.
// On Windows a rename is not a decision: two renames of one file at the same moment can both succeed (N1-C5). A claim
// is decided by a pin (N1-B): the claimed file opened with FileShare.Read, a share mode without delete, which NTFS cannot
// grant while any rename handle on the file is open, and after which the file can no longer be reached through
// requests/. Everything else (deletion, directories, attributes) uses managed APIs.
#if UNITY_EDITOR_WIN
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace Gpos.LiveBridge
{
    internal static class WindowsFiles
    {
        const uint MoveFileReplaceExisting = 0x1, MoveFileWriteThrough = 0x8;
        public const int ErrorFileNotFound = 2, ErrorPathNotFound = 3, ErrorAccessDenied = 5, ErrorSharingViolation = 32,
                         ErrorFileExists = 80, ErrorAlreadyExists = 183;
        // N1-C8: with 20 attempts 1 ms apart every one of 3000 replacements succeeded against a reader looping on the
        // target; with none, most failed. N1-B: a pin is normally granted at once and within ~15 ms behind a rename.
        public const int Attempts = 20;
        public const int PinBudgetMs = 20;

        [DllImport("kernel32.dll", EntryPoint = "MoveFileExW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        static extern bool MoveFileExW(string existingFileName, string newFileName, uint flags);

        static bool Transient(int error) { return error == ErrorAccessDenied || error == ErrorSharingViolation; }

        static int Error(Exception e) { return e.HResult & 0xFFFF; }

        // 0, or the Win32 error. Never replaces an existing name.
        public static int MoveNoReplace(string from, string to)
        {
            return MoveFileExW(from, to, 0) ? 0 : Marshal.GetLastWin32Error();
        }

        // As MoveNoReplace, retried (bounded) while another process holds the source.
        public static int MoveNoReplaceRetried(string from, string to)
        {
            int error = 0;
            for (int attempt = 0; attempt < Attempts; attempt++)
            {
                error = MoveNoReplace(from, to);
                if (!Transient(error)) return error;
                Thread.Sleep(1);
            }
            return error;
        }

        // The atomic replacement of `to` by `from`, retried (bounded) while a reader holds the target. 0 or the error.
        public static int Replace(string from, string to)
        {
            int error = 0;
            for (int attempt = 0; attempt < Attempts; attempt++)
            {
                if (MoveFileExW(from, to, MoveFileReplaceExisting | MoveFileWriteThrough)) return 0;
                error = Marshal.GetLastWin32Error();
                if (!Transient(error)) return error;
                Thread.Sleep(1);
            }
            return error;
        }

        // Removes a file the caller owns (its own temporary file), retried (bounded) while another process holds it.
        // True when the name is gone.
        public static bool Delete(string path)
        {
            for (int attempt = 0; attempt < Attempts; attempt++)
            {
                try { File.Delete(path); return !File.Exists(path); }
                catch (DirectoryNotFoundException) { return true; }
                catch (IOException) { Thread.Sleep(1); }
                catch (UnauthorizedAccessException) { Thread.Sleep(1); }
            }
            return !File.Exists(path);
        }

        // One attempt (retention: what stays is removed in a later round).
        public static void TryDelete(string path)
        {
            try { File.Delete(path); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        public enum Claim { Won, Lost, Undecided }

        // PIN -> VERIFY, after this bridge's own rename into claimed/ succeeded.
        //   Won        the request rests at `claimed`, pinned, a regular file: no rename of it can be in flight and none can
        //              start, so the decision holds after the pin is closed. The caller reads the request through `pin`.
        //   Lost       the file is no longer at `claimed`: GPOS's withdrawal landed after this rename. It is GPOS's; the
        //              bridge leaves it untouched and never executes it.
        //   Undecided  the pin could not be acquired within the budget (another process holds the file with delete or
        //              write access), or the file is not a plain file. The request is never executed.
        public static Claim Pin(string claimed, int budgetMs, out FileStream pin)
        {
            pin = null;
            var watch = Stopwatch.StartNew();
            while (true)
            {
                try
                {
                    pin = new FileStream(claimed, FileMode.Open, FileAccess.Read, FileShare.Read);
                }
                catch (FileNotFoundException) { return Claim.Lost; }
                catch (DirectoryNotFoundException) { return Claim.Lost; }
                catch (IOException e)
                {
                    if (!Transient(Error(e)) || watch.ElapsedMilliseconds >= budgetMs) return Claim.Undecided;
                    Thread.Sleep(1);
                    continue;
                }
                catch (UnauthorizedAccessException)
                {
                    if (watch.ElapsedMilliseconds >= budgetMs) return Claim.Undecided;
                    Thread.Sleep(1);
                    continue;
                }
                FileAttributes attributes;
                try { attributes = File.GetAttributes(claimed); }
                catch (Exception) { attributes = FileAttributes.Directory; }
                if ((attributes & (FileAttributes.ReparsePoint | FileAttributes.Directory | FileAttributes.Device)) != 0)
                {
                    pin.Dispose();
                    pin = null;
                    return Claim.Undecided;
                }
                return Claim.Won;
            }
        }

        public enum Take { NotTaken, Won, Lost, Undecided, Unreadable }

        // The whole Windows claim of one request (Ipc.Serve): RENAME -> PIN -> VERIFY -> DECIDE.
        //   NotTaken    the rename failed: withdrawn, already taken, or held by another process (tried again next tick)
        //   Won         the request is the bridge's: `bytes` holds it (at most `limit` + 1 bytes), read through the pin
        //   Lost        GPOS's withdrawal landed after the rename: GPOS's file, untouched, never executed
        //   Undecided   the pin could not be acquired, or the file is not a plain file: never executed
        //   Unreadable  won, but reading through the pin failed: never executed
        public static Take TakeRequest(string request, string claimed, int limit, int pinBudgetMs, out byte[] bytes)
        {
            bytes = null;
            if (MoveNoReplace(request, claimed) != 0) return Take.NotTaken;
            FileStream pin;
            var decided = Pin(claimed, pinBudgetMs, out pin);
            if (decided == Claim.Lost) return Take.Lost;
            if (decided == Claim.Undecided) return Take.Undecided;
            try
            {
                using (pin) bytes = ReadPinned(pin, limit);
                return Take.Won;
            }
            catch (Exception)
            {
                bytes = null;
                return Take.Unreadable;
            }
        }

        // Every byte of a pinned request, at most `limit` + 1 (so an oversized request is seen as such).
        public static byte[] ReadPinned(FileStream pin, int limit)
        {
            var buffer = new MemoryStream();
            var chunk = new byte[16 * 1024];
            int n;
            while (buffer.Length <= limit && (n = pin.Read(chunk, 0, chunk.Length)) > 0) buffer.Write(chunk, 0, n);
            return buffer.ToArray();
        }

        // A file another process may be replacing (the GPOS lease), read with every share mode so the reader never
        // blocks GPOS; a transient sharing error is retried (bounded). null when the file does not exist.
        public static byte[] ReadShared(string path, int limit)
        {
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                        return ReadPinned(fs, limit);
                }
                catch (FileNotFoundException) { return null; }
                catch (DirectoryNotFoundException) { return null; }
                catch (IOException e)
                {
                    if (!Transient(Error(e)) || attempt + 1 >= Attempts) throw;
                    Thread.Sleep(1);
                }
            }
        }
    }
}
#endif
