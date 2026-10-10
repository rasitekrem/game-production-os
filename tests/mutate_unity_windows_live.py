#!/usr/bin/env python3
"""Bounded mutation harness for the Windows live bridge (alpha.26, Phase 2C-9.3b). Windows only.

    python -X utf8 tests/mutate_unity_windows_live.py [--jobs N] [--only TEXT] [--real]

Each mutation breaks one Windows live guarantee in a temporary copy of the repository and runs
tests/test_unity_windows_live.py there. A FAST mutation runs the suite without any Unity process
(GPOS_UNITY_TEST_FAST=1): the gating, the bridge sources compiled with the Mono bundled with the Editor, the live IPC
across real processes on NTFS, the identity proof and the call outcomes. A REAL mutation (--real) runs only the
named real tests, against the installed Editor in the disposable lab, one at a time: at most one Unity Editor ever
runs. A mutation of the bridge's C# regenerates the copy's release manifest, so the mutant is installable and what
fails is the behaviour, not the digest.

A mutation must make its run fail ("CAUGHT"). A run that times out is INCONCLUSIVE, never CAUGHT. Every harness runs
behind tests/mutation_gate.py: a baseline with no mutation must pass first, prepared exactly like every mutant.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import mutation_gate as gate

ROOT = Path(__file__).resolve().parent.parent
ADAPTER, IPC, STATUS, LOCK, LIVE = ("gpos/tools/unity/adapter.py", "gpos/tools/unity/live_ipc.py",
                                    "gpos/tools/unity/live_status.py", "gpos/tools/unity/project_lock.py",
                                    "gpos/tools/unity/live.py")
IDENT, INSTALL = "gpos/tools/unity/identity.py", "gpos/tools/unity/bridge_install.py"
CS = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/"
FILES, PATHS, PROTOCOL = CS + "Core/WindowsFiles.cs", CS + "Core/WindowsPaths.cs", CS + "Core/Protocol.cs"
COMMANDS, IPC_CS = CS + "Commands.cs", CS + "Ipc.cs"
FAST_TIMEOUT, REAL_TIMEOUT = 1200, 3000

FAST = [
    # gating
    ("an unqualified live capability joins the Windows set", [
        (ADAPTER, "authoring.INSPECT_OBJECT, authoring.CREATE, authoring.SET_TRANSFORM, authoring.SAVE_SCENE)",
         "authoring.INSPECT_OBJECT, authoring.CREATE, authoring.SET_TRANSFORM, authoring.SAVE_SCENE, live.ENTER)")]),
    ("the Windows execute gate lets everything through", [
        (ADAPTER, "            if cap not in WINDOWS_CAPABILITIES:\n                return AdapterOutcome(ok=True, "
                  "diagnostics=(dg.make(\"PLATFORM_UNSUPPORTED\"",
         "            if False:\n                return AdapterOutcome(ok=True, diagnostics=(dg.make(\"PLATFORM_UNSUPPORTED\"")]),
    ("the bridge serves Play Mode on Windows", [
        (PROTOCOL, '"inspect", "object-inspect", "create-gameobject", "set-transform",\n',
         '"inspect", "object-inspect", "create-gameobject", "set-transform", "enter-playmode",\n')]),
    ("the bridge's Windows allowlist is never checked", [
        (PROTOCOL, "            if (Array.IndexOf(WindowsCommands, r.Command) < 0)\n",
         "            if (false)\n")]),
    # the GPOS side of the IPC
    ("a withdrawal counts once its rename succeeds (no pin)", [
        (IPC, "            try:\n                os.rename(src, dst)\n                break\n",
         "            try:\n                os.rename(src, dst)\n                return True\n")]),
    ("the withdrawal pin shares delete", [
        (IPC, "            opened = _windows_open(dst, deny_writers=True)\n",
         "            opened = _windows_open(dst, deny_writers=False)\n")]),
    ("the pinned file is not verified to be the published request", [
        (IPC, "        if identity is None or (st.st_dev, st.st_ino) != identity[:2] or "
              "hashlib.sha256(data).hexdigest() != identity[2]:\n            return None\n",
         "        pass\n")]),
    ("a blocked withdrawal is taken as claimed", [
        (IPC, "            except PermissionError:             # held without FILE_SHARE_DELETE: it may still be claimed "
              "later\n                if time.monotonic() >= end:\n                    return None\n",
         "            except PermissionError:             # held without FILE_SHARE_DELETE: it may still be claimed "
              "later\n                if time.monotonic() >= end:\n                    return False\n")]),
    ("an unreadable answer ends the call with an error", [
        (IPC, "            try:\n                return read_response(channel, rid)\n            except ChannelProblem:\n"
              "                return None\n",
         "            return read_response(channel, rid)\n")]),
    ("the temp sweep removes any dot file", [
        (IPC, '    _TEMP = re.compile(r"^\\.tmp-[0-9a-f]{32}$")\n', '    _TEMP = re.compile(r"^\\.")\n')]),
    ("a junction in the live folder is accepted", [
        (IPC, "                if _windows_reparse(p):\n                    raise ChannelProblem(", "                if False:\n"
              "                    raise ChannelProblem(")]),
    ("state files are read without the pinned, alias-refusing reader", [
        (IPC, "        return _windows_read_bounded(path, limit)\n", "        pass\n")]),
    # the identity proof
    ("the creation time is truncated to microseconds", [
        (STATUS, '\\.(\\d{7})Z$")', '\\.(\\d{6,7})Z$")')]),
    ("the creation time has a one-second tolerance", [
        (LOCK, "            if created != recorded:\n", "            if abs(created - recorded) > 10_000_000:\n")]),
    ("a later creation time is unknown instead of reused", [
        (LOCK, '                return "REUSED" if created > recorded else "UNKNOWN"\n',
         '                return "UNKNOWN"\n')]),
    ("the Editor's project is not checked", [
        (LOCK, '            return "ALIVE" if verdict == MATCHING_EDITOR else "UNKNOWN"\n', '            return "ALIVE"\n')]),
    ("another user's Editor counts", [
        (LOCK, "            if image is None or not _same_file(image, editor) or process.same_user() is not True:\n",
         "            if image is None or not _same_file(image, editor):\n")]),
    ("an exited Editor counts as alive", [
        (LOCK, '            if not running:\n                return "GONE"\n', '')]),
    ("the live plane uses the macOS ps proof on Windows", [
        (LIVE, "            return ls.windows_identity(pid, started, self.summary[\"editor_version\"], self.project)\n",
         "            pass\n")]),
    # the bridge's Windows file layer (C#)
    ("the bridge's pin shares delete", [
        (FILES, "pin = new FileStream(claimed, FileMode.Open, FileAccess.Read, FileShare.Read);",
         "pin = new FileStream(claimed, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);")]),
    ("the bridge claims on its rename alone", [
        (FILES, "            var decided = Pin(claimed, pinBudgetMs, out pin);\n",
         "            var decided = Pin(claimed, pinBudgetMs, out pin);\n            if (decided == Claim.Lost) { bytes = new byte[0]; return Take.Won; }\n")]),
    ("the bridge's no-replace move replaces", [
        (FILES, "            return MoveFileExW(from, to, 0) ? 0 : Marshal.GetLastWin32Error();",
         "            return MoveFileExW(from, to, MoveFileReplaceExisting) ? 0 : Marshal.GetLastWin32Error();")]),
    ("the project key is built with backslashes", [
        (PATHS, "            return project.Substring(prefix.Length).Replace('\\\\', '/');",
         "            return project.Substring(prefix.Length);")]),
    ("an 8.3 or wrongly cased spelling is accepted", [
        (PATHS, "                if (count != 1) return null;", "                if (count == 0) match = part; else if (count != 1) return null;")]),
    # the install and root paths
    ("a junction in the runtime area is accepted for installation", [
        (INSTALL, "        if sys.platform == \"win32\":   # alpha.26: a junction (or any reparse point) is a link here too\n"
                  "            if st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:\n"
                  "                raise OSError(f\"{current} is not a real directory\")\n", "")]),
    ("a junction on the way to the GPOS root is accepted", [
        (IDENT, "                if any(_reparse(p) for p in (game, game / \"gpos\", config)):\n                    return None\n",
         "")]),
]

REAL = [
    ("a bind whose session file cannot be published stays bound", [
        (COMMANDS, "                SessionState.EraseString(LiveBridge.KSession);\n                SessionState.EraseString("
                   "LiveBridge.KOwner);\n                SessionState.EraseString(LiveBridge.KBound);\n"
                   "                SessionState.EraseString(LiveBridge.KProposal);\n                Ipc.Event(\"bind-undone:\"",
         "                Ipc.Event(\"bind-undone:\"")],
     ["WLR2_ExactCreationTime", "WLR4_SessionIntegrity.test_02_a_consumed_approval_can_never_bind_twice"]),
    ("the bridge serves every command on Windows", [
        (PROTOCOL, "            if (Array.IndexOf(WindowsCommands, r.Command) < 0)\n", "            if (false)\n")],
     ["WLR2_ExactCreationTime", "WLR5_ApprovalAndRecovery.test_03_the_bridge_serves_nothing_outside_the_windows_allowlist"]),
    ("the live plane uses the macOS ps proof on Windows", [
        (LIVE, "            return ls.windows_identity(pid, started, self.summary[\"editor_version\"], self.project)\n",
         "            pass\n")],
     ["WLR2_ExactCreationTime", "WLR5_ApprovalAndRecovery.test_01_a_rejected_attach_takes_no_lease"]),
]
REAL_BASELINE = sorted({t for _, _, tests in REAL for t in tests})
MODE = {"real": False}   # set by main(); the gate's own baseline (no edit) then runs exactly the real tests

REGENERATE = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from gpos.tools.unity import bridge_install as bi
lb = Path(sys.argv[1]) / "gpos" / "tools" / "unity" / "live_bridge"
entries, problems = bi.tree(lb / "com.gpos.live-bridge")
assert not problems, problems
entries = sorted(entries, key=lambda e: e["path"])
data = {"bridge_version": bi.BRIDGE_VERSION, "files": entries, "package_digest": bi.digest(entries),
        "package_id": bi.PACKAGE_ID, "protocol": bi.PROTOCOL, "schema": bi.MANIFEST_SCHEMA}
(lb / "manifest.json").write_bytes((json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))
"""


def run(mutation):
    name, edits = mutation[0], mutation[1]
    tests = mutation[2] if len(mutation) > 2 else (REAL_BASELINE if MODE["real"] else None)
    tmp = Path(tempfile.mkdtemp(prefix="gpos-livewinmut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for rel, anchor, replacement in edits:
            path = copy / rel
            text = gate.read(path)
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            gate.write(path, text.replace(anchor, replacement))
        if any(rel.startswith(CS) for rel, _, _ in edits):
            subprocess.run([*gate.PYTHON, "-B", "-c", REGENERATE, str(copy)], check=True, capture_output=True,
                           **gate.ISOLATED)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GPOS_SOURCE_GIT_DIR=str(ROOT / ".git"))
        if tests is None:
            env["GPOS_UNITY_TEST_FAST"] = "1"
        else:
            env.pop("GPOS_UNITY_TEST_FAST", None)
        try:
            out = subprocess.run([*gate.PYTHON, "-B", str(copy / "tests" / "test_unity_windows_live.py"),
                                  *(tests or [])], capture_output=True, text=True, env=env, **gate.ISOLATED,
                                 timeout=FAST_TIMEOUT if tests is None else REAL_TIMEOUT)
        except subprocess.TimeoutExpired:
            return name, "INCONCLUSIVE (timed out; never counted as caught)"
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        gate_remove(tmp)


def gate_remove(path):
    sys.path.insert(0, str(ROOT / "tests"))
    import windows_standin
    windows_standin.remove_tree(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    parser.add_argument("--real", action="store_true", help="the bounded real-Editor mutations (one Editor at a time)")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("NOT_RUN      every mutation (the Windows live bridge is Windows only)")
        return gate.BLOCKED_EXIT
    MODE["real"] = args.real
    pool = REAL if args.real else FAST
    selected = [m for m in pool if not args.only or args.only in m[0]]
    return gate.qualify(run, selected, 1 if args.real else args.jobs)


if __name__ == "__main__":
    sys.exit(main())
