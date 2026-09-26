// GPOS live bridge — where this Editor's project sits inside a GPOS project, and the bridge's own digest.
// The package holds no project-specific value: the GPOS root is found by a bounded walk up from the Unity project
// (the nearest ancestor with .game/gpos/project-config.json), every path is resolved with realpath, and anything
// that is a symbolic link, or no root at all, leaves the bridge dormant.
using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace Gpos.LiveBridge
{
    internal sealed class Place
    {
        public string GposRoot, ProjectPath, Rel, Key, LiveDir;
    }

    internal static class Identity
    {
        public const int MaxAscent = 16;

        [DllImport("libc", SetLastError = true)] static extern IntPtr realpath(string path, IntPtr resolved);
        [DllImport("libc")] static extern void free(IntPtr pointer);

        public static string RealPath(string path)
        {
            IntPtr p = realpath(path, IntPtr.Zero);
            if (p == IntPtr.Zero) return null;
            try { return Marshal.PtrToStringAnsi(p); }
            finally { free(p); }
        }

        public static bool IsLink(string path)
        {
            try { return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0; }
            catch { return false; }
        }

        static bool RealDirectory(string path) { return Directory.Exists(path) && !IsLink(path); }

        static bool RealFile(string path) { return File.Exists(path) && !IsLink(path); }

        public static string Sha256(byte[] bytes)
        {
            using (var sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(bytes).Select(b => b.ToString("x2")));
        }

        public static string ProjectKey(string rel)
        {
            return Sha256(Encoding.UTF8.GetBytes("gpos.unity.live\0" + rel)).Substring(0, 16);
        }

        // null when no valid GPOS root encloses this Unity project, or a component of the runtime path is a link.
        public static Place Locate()
        {
            string project = RealPath(Path.GetFullPath(Path.Combine(Application.dataPath, "..")));
            if (project == null) return null;
            string root = null, dir = project;
            for (int i = 0; i <= MaxAscent && dir != null; i++)
            {
                string game = Path.Combine(dir, ".game");
                if (RealDirectory(game) && RealDirectory(Path.Combine(game, "gpos")) &&
                    RealFile(Path.Combine(game, "gpos", "project-config.json")))
                {
                    root = dir;
                    break;
                }
                dir = Path.GetDirectoryName(dir);
            }
            if (root == null) return null;
            string rel = project == root ? "." : project.Substring(root.Length).TrimStart('/');
            if (rel != "." && (rel.Length == 0 || project.Substring(0, root.Length + 1) != root + "/")) return null;
            string key = ProjectKey(rel);
            string runtime = Path.Combine(root, ".game", "gpos-runtime");
            string[] chain = { runtime, Path.Combine(runtime, "unity"), Path.Combine(runtime, "unity", "live"),
                               Path.Combine(runtime, "unity", "live", key) };
            if (chain.Any(IsLink)) return null;
            return new Place { GposRoot = root, ProjectPath = project, Rel = rel, Key = key, LiveDir = chain[3] };
        }

        // The digest of the installed bridge package exactly as GPOS computes it from its release manifest:
        // sha256 over sorted "relpath\tsha256\tsize\n" lines. null when the package holds a link.
        public static string SelfDigest(string projectPath)
        {
            string dir = Path.Combine(projectPath, "Packages", Protocol.PackageId);
            if (!RealDirectory(dir)) return null;
            if (Directory.GetDirectories(dir, "*", SearchOption.AllDirectories).Any(IsLink)) return null;
            var files = Directory.GetFiles(dir, "*", SearchOption.AllDirectories);
            if (files.Any(IsLink)) return null;
            var lines = files.Select(f => new { Rel = f.Substring(dir.Length + 1).Replace('\\', '/'), Path = f })
                             .OrderBy(x => x.Rel, StringComparer.Ordinal)
                             .Select(x => { byte[] b = File.ReadAllBytes(x.Path); return x.Rel + "\t" + Sha256(b) + "\t" + b.Length + "\n"; });
            return Sha256(Encoding.UTF8.GetBytes(string.Concat(lines)));
        }
    }
}
