#!/usr/bin/env python3
"""Bounded mutation harness for Unity source synchronization, compilation facts and the batch lock proof (Phase 2C-6C).

    python3 tests/mutate_unity_sources.py [--jobs N] [--only TEXT] [--anchors] [--real]

Each mutation breaks exactly one guarantee in a temporary copy of the repository and runs one suite there:

    "sources"    tests/test_unity_sources.py with GPOS_UNITY_TEST_FAST=1 (fake bridge, lock proof, source boundaries)
    "adapter"    tests/test_unity_adapter.py with GPOS_UNITY_TEST_FAST=1 (the batch plane with a stand-in Editor)
    "core"       tests/test_unity_live_bridge_core.py (the bridge's C# core, compiled with Unity's bundled Mono)
    "real9"      (--real) R9_RealSources: the source/compile loop in a lab-owned batch Editor
    "real10"     (--real) R10_RealReloadAndIdentity: reloads, identity, deletion synchronization and its bounds

A mutation of the bridge package regenerates its manifest in the copy first, so it can only be caught by a test of
what the code does. The copy has no .git; GPOS_SOURCE_GIT_DIR points the frozen-tag tests at this repository's
(read only). A mutation must make its suite fail ("CAUGHT"); one that leaves it green is "MISSED" and fails this
harness, and so does an anchor that does not match exactly once ("NOT APPLIED"). `--anchors` only checks the
anchors. The repository itself is never modified, and after each real mutation every Unity process started for
that copy is stopped.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, PL, AD, LV = "gpos/tools/unity/sources.py", "gpos/tools/unity/project_lock.py", "gpos/tools/unity/adapter.py", "gpos/tools/unity/live.py"
BI = "gpos/tools/unity/bridge_install.py"
ED = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/"
RULES, SYNC, COMP, CMD = ED + "Core/SourceRules.cs", ED + "SourceSync.cs", ED + "Compilation.cs", ED + "Commands.cs"

MUTATIONS = [
    # --- the source path grammar and the sync inputs (GPOS side)
    ("a source may be outside Assets", "sources", [(SRC, '    if not value.startswith("Assets/"):\n        refuse("sources are synchronized only below Assets/',
                                                    '    if False:\n        refuse("sources are synchronized only below Assets/')]),
    ("a source may have any extension", "sources", [(SRC, 'EXTENSIONS = (".cs", ".asmdef", ".asmref")', 'EXTENSIONS = (".cs", ".asmdef", ".asmref", ".txt", ".dll", "")')]),
    ("a special folder holds sources", "sources", [(SRC, "        if s.lower() in EXCLUDED:\n", "        if False:\n")]),
    ("a path may be named twice", "sources", [(SRC, "        if len({p.lower() for p in named}) != len(named):\n", "        if False:\n")]),
    ("a sync has no path bound", "sources", [(SRC, "        if len(named) > MAX_PATHS:\n", "        if False:\n")]),
    ("a sync takes a folder or code input", "sources", [(SRC, "        if k not in INPUTS[cap]:\n            raise", "        if False:\n            raise")]),
    # --- results (GPOS side)
    ("a sync that imported nothing claims a mutation", "sources", [(SRC, '            and data.get("mutation_started") is False:', "            and False:")]),
    ("side effects are not disclosed", "sources", [(SRC, "    if any(effects.values()):\n", "    if False:\n")]),
    ("absolute paths leave the journal", "sources", [(SRC, '    return ABSOLUTE_PATH.sub("<path>", text)', "    return text")]),
    ("diagnostics bypass the redaction boundary", "sources", [(SRC, "    text, _ = redaction.redact(text)\n", "")]),
    ("an outside file keeps its absolute path", "sources", [(SRC, '        out["file"], out["outside_project"] = None, True', "        pass")]),
    ("a refused sync maps to another code", "sources", [(LV, '"SOURCE_SYNC_REFUSED": "LIVE_SOURCE_SYNC_REFUSED"', '"SOURCE_SYNC_REFUSED": "LIVE_SOURCE_PATH_INVALID"')]),
    ("an incomplete sync maps to another code", "sources", [(LV, '"SOURCE_SYNC_INCOMPLETE": "LIVE_SOURCE_SYNC_INCOMPLETE"', '"SOURCE_SYNC_INCOMPLETE": "LIVE_AUTHORING_FAILED"')]),
    ("an unknown journal filter maps to a protocol error", "sources", [(LV, '"JOURNAL_FILTER_UNKNOWN": "LIVE_DIAGNOSTICS_FILTER_UNKNOWN"', '"JOURNAL_FILTER_UNKNOWN": "LIVE_PROTOCOL_ERROR"')]),
    ("the sync is declared read-only", "sources", [(AD, '"compile command, no global Refresh.", "MUTATING",', '"compile command, no global Refresh.", "READ_ONLY",')]),
    ("the wait has no 300 s bound", "sources", [(AD, '"refreshes, reloads, restarts and replays nothing.", "READ_ONLY",\n            TimeoutPolicy(default=120.0, maximum=300.0)),',
                                                  '"refreshes, reloads, restarts and replays nothing.", "READ_ONLY",\n            TimeoutPolicy(default=120.0, maximum=900.0)),')]),
    ("source capabilities are not dispatched", "sources", [(AD, "        if cap in sources.CAPABILITY_IDS:\n            return sources.execute", "        if False:\n            return sources.execute")]),
    ("bridge 1.3.0 is not an upgradable release", "sources", [(BI, '    "1.3.0": ("gpos.unity.live/4", "acdbb1c84e9be9e8fbd10bb6b2c09e4dbfae3e4d4e28ad74c4f5cc708a4f3f47"),\n', "")]),
    ("the pinned 1.3.0 digest is not the frozen one", "sources", [(BI, '"acdbb1c84e9be9e8fbd10bb6b2c09e4dbfae3e4d4e28ad74c4f5cc708a4f3f47"', '"acdbb1c84e9be9e8fbd10bb6b2c09e4dbfae3e4d4e28ad74c4f5cc708a4f3f40"')]),
    # --- the compile-generation model and wait-ready (GPOS side)
    ("wait-ready uses the counter after the imports as the baseline", "sources",
     [(SRC, '            before = record_["compile_started_before_sync"]', '            before = record_.get("compile_started_after_sync") or record_["compile_started_before_sync"]')]),
    ("success needs no Domain Reload", "sources", [(SRC, '    if isinstance(reload_generation, int) and reload_generation > last.get("reload_generation", reload_generation):',
                                                    "    if True:")]),
    ("errors are not a failed outcome", "sources", [(SRC, '    if last.get("errors", 0) > 0:\n        return FAILED', '    if False:\n        return FAILED')]),
    ("an older compilation satisfies a sync", "sources", [(SRC, '    if not last or not last.get("compile_generation", 0) > before:', "    if not last:")]),
    ("ready while compiling", "sources", [(SRC, '    return (state.get("phase") == "EDIT" and state.get("compiling") is False and', '    return (state.get("phase") == "EDIT" and')]),
    ("ready without the stability window", "sources", [(SRC, "            if ready and now - since >= SETTLE_SECONDS:", "            if ready:")]),
    ("wait-ready triggers an import", "sources", [(SRC, '    r = live.call(channel, "compilation-status", {}, boot, sid, wait=wait)',
                                                   '    r = live.call(channel, "sync-sources", {"sources": ["Assets/Loop/A.cs"], "deleted": []}, boot, sid, wait=wait)')]),
    ("a changed Editor boot is ignored", "sources", [(SRC, '            if beat.get("boot_id") != boot:\n', "            if False:\n")]),
    ("a completed failure is not reported", "sources", [(SRC, '    if data["compilation_failed"]:\n        diags.append', "    if False:\n        diags.append")]),
    # --- the project-lock proof (the batch plane)
    ("an unknown first proof counts as no process", "sources", [(PL, "    if a.lock == UNKNOWN or first[0] != NO_MATCH_PROVEN:", "    if a.lock == UNKNOWN:")]),
    ("an unknown final proof counts as no process", "sources", [(PL, "    if final[0] != NO_MATCH_PROVEN:\n        a.reasons.append(final[1])\n        return a",
                                                                  "    if False:\n        a.reasons.append(final[1])\n        return a")]),
    ("a failed process listing counts as no process", "sources", [(PL, "        if problem:\n            return PROCESS_STATE_UNKNOWN, problem", "        if problem:\n            return NO_MATCH_PROVEN, None")]),
    ("the lock is never queried", "sources", [(PL, "        kind = osx.getlk(fd)", "        kind = fcntl.F_UNLCK")]),
    ("a held lock is not an active Editor", "sources", [(PL, "    if a.lock == HELD:\n", "    if False:\n")]),
    ("the project is matched as a substring", "sources", [(PL, "        same = (theirs.st_dev, theirs.st_ino) == (ours.st_dev, ours.st_ino) or osx.realpath(value) == osx.realpath(project)",
                                                            "        same = str(project) in value or value in str(project)")]),
    ("the final process proof is skipped", "sources", [(PL, "    final = process_proof(project, editor, osx)", "    final = first")]),
    ("a relative project argument is resolved", "sources", [(PL, "    if not os.path.isabs(value):\n", "    if False:\n")]),
    ("a repeated project argument is accepted", "sources", [(PL, "    if len(flags) != 1 or flags[0] + 1 >= len(args):", "    if len(flags) < 1 or flags[0] + 1 >= len(args):")]),
    ("an uninspectable Unity process is skipped", "sources", [(PL, "                if name is None or name == UNITY_COMMAND:", "                if False:")]),
    ("the candidate bound is removed", "sources", [(PL, "            if len(candidates) > MAX_CANDIDATES:\n", "            if False:\n")]),
    ("the argv bound is removed", "sources", [(PL, "        if size.value > MAX_ARGV_BYTES:\n", "        if False:\n")]),
    ("a truncated process listing is accepted", "sources", [(PL, "        if n >= ctypes.sizeof(buf):\n", "        if False:\n")]),
    ("a linked lockfile is followed", "sources", [(PL, "        st = osx.lstat(lock)", "        st = osx.stat(lock)"),
                                                  (PL, "OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK", "OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK")]),
    ("a lockfile replaced while examined is accepted", "sources", [(PL, "        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):",
                                                                     "        if not stat.S_ISREG(now.st_mode):")]),
    ("the proof opens the lockfile for writing", "sources", [(PL, "OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW", "OPEN_FLAGS = os.O_RDWR | os.O_NOFOLLOW")]),
    ("the proof takes the lock instead of querying it", "sources", [(PL, "        answer = fcntl.fcntl(fd, fcntl.F_GETLK, query)",
                                                                      "        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n        answer = struct.pack(FLOCK, 0, 0, 0, fcntl.F_UNLCK, 0)")]),
    ("the adapter deletes an orphan lockfile", "sources", [(AD, "        proof = self._lock_proof(project, context.probe.tool_path)\n        if proof.state not in pl.PROCEED:\n            return _locked(cap, proof)\n        workspace = Path(context.workspace)",
                                                            "        proof = self._lock_proof(project, context.probe.tool_path)\n        if proof.state not in pl.PROCEED:\n            return _locked(cap, proof)\n        if proof.state == pl.ORPHAN_UNHELD:\n            (project / \"Temp\" / \"UnityLockfile\").unlink()\n        workspace = Path(context.workspace)")]),
    ("the adapter skips the proof immediately before the launch", "sources", [(AD, "        proof = self._lock_proof(project, context.probe.tool_path)   # fresh, immediately before the launch\n        if proof.state not in pl.PROCEED:\n            return _locked(cap, proof)\n", "")]),
    ("the adapter treats any lockfile as a conflict again", "adapter", [(AD, "        if proof.state not in pl.PROCEED:\n            return _locked(cap, proof)\n        workspace",
                                                                         "        if proof.state not in pl.PROCEED or (project / \"Temp\" / \"UnityLockfile\").exists():\n            return _locked(cap, proof)\n        workspace")]),
    ("the orphan is not reported", "sources", [(AD, "        if proof.state == pl.ORPHAN_UNHELD:\n            result = replace(", "        if False:\n            result = replace(")]),
    ("the bridge installer uses the orphan rule", "sources", [(LV, "    if lock.exists() or lock.is_symlink():\n        return _refuse(cap, \"ENGINE_PROJECT_LOCKED\"", "    if False:\n        return _refuse(cap, \"ENGINE_PROJECT_LOCKED\"")]),
    # --- the bridge's Unity-free core
    ("the baseline is read after the imports", "core", [(RULES, "            marks.Before = host.CompileStarted;\n            host.Begin(marks.Before);\n            marks.Began = true;\n"
                                                                "            foreach (var p in exact) { marks.Imports++; host.ImportExact(p); }\n            foreach (var f in folders) { marks.Imports++; host.ImportRecursive(f); }\n",
                                                                "            marks.Began = true;\n            foreach (var p in exact) { marks.Imports++; host.ImportExact(p); }\n"
                                                                "            foreach (var f in folders) { marks.Imports++; host.ImportRecursive(f); }\n            marks.Before = host.CompileStarted;\n            host.Begin(marks.Before);\n")]),
    ("D1: Assets is the folder of a source in it", "core", [(RULES, "            return parent == null || parent == Root ? null : parent;", "            return parent;")]),
    ("D2: the widening may reach Assets", "core", [(RULES, "            return above == null || above == Root ? null : above;", "            return above;")]),
    ("the folder count is not bounded", "core", [(RULES, "            if (roots.Count > MaxRoots) throw", "            if (false) throw")]),
    ("a covered folder is imported again", "core", [(RULES, "            var roots = distinct.Where(f => !distinct.Any(o => !string.Equals(o, f, StringComparison.OrdinalIgnoreCase) && Within(f, o))).ToList();",
                                                     "            var roots = distinct.ToList();")]),
    ("core: a source may have any extension", "core", [(RULES, 'static readonly string[] Extensions = { ".cs", ".asmdef", ".asmref" };', 'static readonly string[] Extensions = { ".cs", ".asmdef", ".asmref", ".txt", "" };')]),
    ("core: a source may be outside Assets", "core", [(RULES, "            if (!path.StartsWith(Root + \"/\", StringComparison.Ordinal)) throw", "            if (false) throw")]),
    ("core: a path may be named twice", "core", [(RULES, "                if (!seen.Add(p)) throw", "                if (!seen.Add(p) && false) throw")]),
    ("a finish is attributed to the finish count", "core", [(RULES, "Finishes.Add(new Finish { Seq = Finished, ForStart = Started,", "Finishes.Add(new Finish { Seq = Finished, ForStart = Finished,")]),
    ("an assembly's earlier result is not replaced", "core", [(RULES, "            Entries.RemoveAll(x => x.Assembly == assembly);\n", "")]),
    ("the total message bound is not enforced", "core", [(RULES, "                if (total <= MaxMessages) break;", "                break;")]),
    ("messages keep the project's absolute path", "core", [(RULES, '            if (root.Length > 1) m = m.Replace(root + "/", "")', "            if (false) m = m.Replace(root + \"/\", \"\")")]),
    ("an outside file keeps its path", "core", [(RULES, "                else { outside = true; return null; }", "                else { outside = true; return f; }")]),
    ("stale errors survive a clean reload", "core", [(RULES, "            int dropped = Entries.RemoveAll(e => e.Errors > 0);", "            int dropped = 0;")]),
    ("a reload after a failed compilation drops errors", "core", [(RULES, "            if (last == null || last.Generation >= generation || last.Errors > 0) return 0;",
                                                                     "            if (last == null) return 0;")]),
    ("an unreadable journal is not marked", "core", [(RULES, "            catch (Exception) { return new CompileJournal { StartedUtc = utc, Reset = true }; }",
                                                      "            catch (Exception) { return new CompileJournal { StartedUtc = utc, Reset = false }; }")]),
    # --- the bridge's Editor sources (the boundary scans)
    ("Editor: an exact source is imported with its folder", "sources", [(SYNC, "public void ImportExact(string path) { AssetDatabase.ImportAsset(path); }",
                                                                          "public void ImportExact(string path) { AssetDatabase.ImportAsset(System.IO.Path.GetDirectoryName(path), ImportAssetOptions.ImportRecursive); }")]),
    ("Editor: a deletion refreshes globally", "sources", [(SYNC, "public void ImportRecursive(string folder) { AssetDatabase.ImportAsset(folder, ImportAssetOptions.ImportRecursive); }",
                                                           "public void ImportRecursive(string folder) { AssetDatabase.Refresh(); }")]),
    ("Editor: the sync requests a compilation", "sources", [(SYNC, "                Compilation.EndSync(host.Gen, marks.After, EditorApplication.isCompiling);",
                                                             "                Compilation.EndSync(host.Gen, marks.After, EditorApplication.isCompiling);\n                UnityEditor.Compilation.CompilationPipeline.RequestScriptCompilation();")]),
    ("Editor: an import runs inside the checks' refusal handler", "sources", [(SYNC, "            try { c = Check(r); }", "            try { c = Check(r); Import(c); }")]),
    ("Editor: an import failure reports no mutation", "sources", [(SYNC, '{ "mutation_started", marks.Began }', '{ "mutation_started", false }')]),
    ("Editor: the snapshot enumerates the whole project", "sources", [(SYNC, "        static void KnownCheck(Snap s, string rel)\n        {\n            s.Inspected++;",
                                                                           "        static void KnownCheck(Snap s, string rel)\n        {\n            s.Inspected += AssetDatabase.GetAllAssetPaths().Length > 0 ? 1 : 1;")]),
    ("Editor: the journal reads the Editor log", "sources", [(COMP, "            root = Path.GetDirectoryName(Application.dataPath);\n            if (SessionState",
                                                              "            root = Path.GetDirectoryName(Application.dataPath);\n            var log = System.IO.File.ReadAllText(UnityEditorInternal.InternalEditorUtility.GetEditorAssemblyPath() + \"/Editor.log\");\n            if (SessionState")]),
]

# Editor-side mutations of the bridge (Unity APIs): each runs a real group of tests/test_unity_sources.py in a
# lab-owned batch Editor (--real; several minutes each).
REAL_MUTATIONS = [
    ("Editor: a deletion imports the Assets root", "real10", [(SYNC, "public void ImportRecursive(string folder) { AssetDatabase.ImportAsset(folder, ImportAssetOptions.ImportRecursive); }",
                                                              "public void ImportRecursive(string folder) { AssetDatabase.ImportAsset(\"Assets\", ImportAssetOptions.ImportRecursive); }")]),
    ("Editor: an exact import takes its folder", "real9", [(SYNC, "public void ImportExact(string path) { AssetDatabase.ImportAsset(path); }",
                                                            "public void ImportExact(string path) { AssetDatabase.ImportAsset(path.Substring(0, path.LastIndexOf('/')), ImportAssetOptions.ImportRecursive); }")]),
    ("Editor: D1 deletions import the Assets root", "real10", [(SYNC, '                if (SourcePaths.DirectParent(p) == null) throw Refused(p + " is directly in Assets/; the Assets root is never imported recursively");\n',
                                                               '                if (SourcePaths.DirectParent(p) == null) { d.Root = "Assets"; d.AlreadySynchronized = false; deletions.Add(d); continue; }\n')]),
    ("Editor: the nearest existing folder is imported", "real10", [(SYNC, '                else throw Refused(p + ": more than one folder level above it is gone; only one level is ever widened");',
                                                                     '                else { d.Root = string.Join("/", parts.Take(missing)); d.Widened = true; }')]),
    ("Editor: the entry bound is not enforced", "real10", [(SYNC, "                    if (++s.Entries > maxEntries)\n", "                    if (++s.Entries < 0)\n")]),
    ("Editor: links inside a folder are imported", "real10", [(SYNC, '                    if (Identity.IsLink(entry)) throw Refused("a link inside "', '                    if (false) throw Refused("a link inside "')]),
    ("Editor: the walk's entries are not looked up in the database", "real10", [(SYNC, "            if (Known(rel)) s.Known.Add(rel);", "            s.Known.Add(rel);")]),
    ("Editor: requested deletions are not checked by their exact paths", "real10", [(SYNC, '                    Listed(f, "requested_removed", mine.Where(d => !Known(d.Path)).Select(d => d.Path)',
                                                                                     '                    Listed(f, "requested_removed", mine.Where(d => false).Select(d => d.Path)')]),
    ("Editor: side effects are not listed", "real10", [(SYNC, '                    Listed(f, "imported_new", a.Known.Where(x => !b.Known.Contains(x)));', '                    Listed(f, "imported_new", new string[0]);')]),
    ("Editor: the baseline is the finish count", "real9", [(SYNC, "            public long CompileStarted { get { return Compilation.Journal.Started; } }",
                                                            "            public long CompileStarted { get { return Compilation.Journal.Finished; } }")]),
    ("Editor: a sync is not refused while compiling", "real9", [(CMD, "            if (Protocol.ChangesSources(r.Command))\n            {\n                string busy = Transitions.AuthoringBusy(Transitions.Phase(LiveBridge.Flags()), LoadPending() != null);\n                if (busy != null) throw new Refusal(\"EDITOR_BUSY\", busy);\n",
                                                                 "            if (Protocol.ChangesSources(r.Command))\n            {\n")]),
    ("Editor: assembly messages are not journaled", "real9", [(COMP, "            CompilationPipeline.assemblyCompilationFinished += (path, messages) => Guard(() => OnAssembly(path, messages));\n", "")]),
    ("Editor: the journal is not kept across a reload", "real9", [(COMP, "            try { record(); Save(); }", "            try { record(); }")]),
    ("Editor: a reload never drops stale errors", "real9", [(COMP, "            Guard(() => Journal.OnReload(LiveBridge.Generation));      // a reload after a clean compilation\n", "")]),
    ("Editor: messages keep absolute paths", "real9", [(COMP, "                string text = CompileText.Text(m.message, ProjectRoot, out clipped);", "                string text = m.message; clipped = false;")]),
]


def lab_editors_under(path):
    """PIDs of Unity processes whose command line names `path` (only the lab Editors of one mutation copy)."""
    out = subprocess.run(["/bin/ps", "-Ao", "pid=,command="], capture_output=True, text=True).stdout
    return [int(line.split(None, 1)[0]) for line in out.splitlines()
            if str(path) in line and "Unity.app/Contents/MacOS/Unity" in line]


def apply(copy, edits):
    for rel, anchor, replacement in edits:
        path = copy / rel
        text = path.read_text()
        if text.count(anchor) != 1:
            return f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
        path.write_text(text.replace(anchor, replacement))
    return None


SUITES = {"sources": ("test_unity_sources.py", []), "adapter": ("test_unity_adapter.py", []),
          "core": ("test_unity_live_bridge_core.py", []),
          "real9": ("test_unity_sources.py", ["R9_RealSources"]), "real10": ("test_unity_sources.py", ["R10_RealReloadAndIdentity"])}


def run(mutation):
    name, suite, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-sourcemut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        problem = apply(copy, edits)
        if problem:
            return name, problem
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GPOS_UNITY_TEST_FAST="1", GPOS_SOURCE_GIT_DIR=str(ROOT / ".git"))
        if suite.startswith("real"):
            env.pop("GPOS_UNITY_TEST_FAST")
        if any("live_bridge/" in rel for rel, _, _ in edits):
            subprocess.run([sys.executable, "-B", str(copy / "tests" / "generate_live_bridge_manifest.py")],
                           capture_output=True, text=True, timeout=120, env=env, check=True)
        test, groups = SUITES[suite]
        argv = [sys.executable, "-B", str(copy / "tests" / test)] + groups
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=3600, env=env)
        except subprocess.TimeoutExpired:
            return name, "CAUGHT"   # a hang is a failure of the suite
        finally:
            for pid in lab_editors_under(tmp):   # never leave a lab Editor of this copy running
                os.kill(pid, 9)
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def anchors():
    problems = []
    for name, _, edits in MUTATIONS + REAL_MUTATIONS:
        for rel, anchor, _ in edits:
            n = (ROOT / rel).read_text().count(anchor)
            if n != 1:
                problems.append(f"{name}: {rel} anchor found {n} times")
    print("\n".join(problems) or f"all anchors of {len(MUTATIONS)} + {len(REAL_MUTATIONS)} mutations apply exactly once")
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    parser.add_argument("--anchors", action="store_true", help="only check that every anchor applies exactly once")
    parser.add_argument("--real", action="store_true", help="run the Editor-side mutations against real lab Editors")
    args = parser.parse_args()
    if args.anchors:
        return anchors()
    selected = [m for m in (REAL_MUTATIONS if args.real else MUTATIONS) if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
