// GPOS live bridge — Windows path spelling (bridge 1.6.0; compiled only in a Windows Editor). Managed APIs only.
// GPOS names the GPOS root by Python's Path.resolve(): a drive-letter path, every component in its on-disk case,
// separated by '\'. The bridge must spell its GPOS root and Unity project exactly so (the SESSION lease is found by
// that text) and the project's relative path with '/' (the project key). Anything else leaves the bridge dormant:
// a UNC or device path, a component that is a reparse point (junction, symbolic link, mount point), an 8.3 short
// name, or a spelling that is not exactly one on-disk entry.
#if UNITY_EDITOR_WIN
using System;
using System.IO;

namespace Gpos.LiveBridge
{
    internal static class WindowsPaths
    {
        public const int MaxComponents = 64;

        // True when `path` is a reparse point, or its attributes cannot be read although it exists (fail closed).
        // False when it does not exist.
        public static bool IsReparse(string path)
        {
            try { return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0; }
            catch (FileNotFoundException) { return false; }
            catch (DirectoryNotFoundException) { return false; }
            catch (Exception) { return true; }
        }

        // The on-disk spelling of an absolute path, or null.
        public static string Canonical(string full)
        {
            if (full == null || full.Length < 3 || !IsDriveLetter(full[0]) || full[1] != ':' || full[2] != '\\') return null;
            if (full.IndexOf('/') >= 0 || full.IndexOf(':', 2) >= 0) return null;          // no mixed separators, no streams
            string current = char.ToUpperInvariant(full[0]) + ":\\";
            string[] parts = full.Substring(3).Split(new[] { '\\' }, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length > MaxComponents) return null;
            foreach (string part in parts)
            {
                if (part == "." || part == ".." || part.IndexOfAny(new[] { '*', '?' }) >= 0) return null;
                string[] found;
                try { found = Directory.GetFileSystemEntries(current, part); }        // also matches an 8.3 alias ...
                catch (Exception) { return null; }
                string match = null;
                int count = 0;
                foreach (string entry in found)
                {
                    string name = Path.GetFileName(entry);
                    if (string.Equals(name, part, StringComparison.OrdinalIgnoreCase)) { match = name; count++; }
                }
                if (count != 1) return null;                                          // ... which the name check refuses
                current = Path.Combine(current, match);
                if (IsReparse(current)) return null;
            }
            return current;
        }

        // "." for the root itself, else the '/'-separated path of `project` below `root` (both canonical), or null when
        // the project is not strictly inside the root.
        public static string Relative(string root, string project)
        {
            if (project == root) return ".";
            string prefix = root.EndsWith("\\", StringComparison.Ordinal) ? root : root + "\\";
            if (!project.StartsWith(prefix, StringComparison.Ordinal) || project.Length == prefix.Length) return null;
            return project.Substring(prefix.Length).Replace('\\', '/');
        }

        static bool IsDriveLetter(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }
    }
}
#endif
