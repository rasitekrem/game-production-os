#!/usr/bin/env python3
"""Bounded mutation harness for the Windows Unity batch plane (alpha.25, Phase 2C-9.3a). Windows only.

    python -X utf8 tests/mutate_unity_windows.py [--jobs N] [--only TEXT] [--real]

Each mutation breaks one Windows guarantee in a temporary copy of the repository and runs
tests/test_unity_windows.py there. A FAST mutation runs the suite without any Unity process (GPOS_UNITY_TEST_FAST=1;
stand-ins, fakes, the real host's lock facts). A REAL mutation (--real) runs only the named real tests, against the
installed Editor in the disposable lab, one at a time: at most one Unity Editor ever runs.

A mutation must make its run fail ("CAUGHT"). A run that times out is INCONCLUSIVE, never CAUGHT: a cold import, a
licence stall or a hung Editor is not evidence that the mutation was detected. Every harness runs behind
tests/mutation_gate.py: a baseline with no mutation must pass first, prepared exactly like every mutant.
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
ADAPTER, LOCK, HOST, RESULTS = ("gpos/tools/unity/adapter.py", "gpos/tools/unity/project_lock.py",
                                "gpos/tools/unity/host_win32.py", "gpos/tools/unity/results.py")
FAST_TIMEOUT, REAL_TIMEOUT = 900, 1500

FAST = [
    ("non-batch capabilities are not refused on Windows", [
        (ADAPTER, "            if cap not in WINDOWS_CAPABILITIES:\n                return AdapterOutcome(ok=True, "
                  "diagnostics=(dg.make(\"PLATFORM_UNSUPPORTED\"",
         "            if False:\n                return AdapterOutcome(ok=True, diagnostics=(dg.make(\"PLATFORM_UNSUPPORTED\"")]),
    ("an unqualified live capability joins the Windows set", [
        (ADAPTER, "authoring.INSPECT_OBJECT, authoring.CREATE, authoring.SET_TRANSFORM, authoring.SAVE_SCENE)",
         "authoring.INSPECT_OBJECT, authoring.CREATE, authoring.SET_TRANSFORM, authoring.SAVE_SCENE, live.ENTER)")]),
    ("the Windows probe reports every capability usable", [
        (ADAPTER, "                                 capability_availability=tuple((c.id, windows(c), \"\" if windows(c) else "
                  "WINDOWS_REFUSAL)",
         "                                 capability_availability=tuple((c.id, True, \"\" if windows(c) else "
                  "WINDOWS_REFUSAL)")]),
    ("the Unity child inherits APPDATA too", [
        (ADAPTER, 'WINDOWS_ENVIRONMENT = ("ProgramData", "LOCALAPPDATA")',
         'WINDOWS_ENVIRONMENT = ("ProgramData", "LOCALAPPDATA", "APPDATA")')]),
    ("the Windows environment is the bare foundation default", [
        (ADAPTER, "            inherit=proc.SAFE_ENV_DEFAULTS + proc.WINDOWS_ENV_DEFAULTS + WINDOWS_ENVIRONMENT, overrides=(",
         "            inherit=proc.SAFE_ENV_DEFAULTS, overrides=(")]),
    ("the Hub root follows an environment variable", [
        (ADAPTER, "                base = host_win32.program_files()", "                base = os.environ.get(\"ProgramFiles\")")]),
    ("a reparse point on the way to the Editor is accepted", [
        (ADAPTER, "            found = [(v, p) for v, p in found if not _windows_reparse_on_way(p)]",
         "            found = list(found)")]),
    ("another user's Editor counts as this user's", [
        (LOCK, "                if not user:\n                    continue                 # another user's Editor",
         "                if False:\n                    continue                 # another user's Editor")]),
    ("an Editor that cannot be inspected counts as gone", [
        (LOCK, "                return PROCESS_STATE_UNKNOWN, f\"process {pid} may be a Unity Editor and cannot be inspected "
               "({problem})\"",
         "                continue")]),
    ("an unreadable command line counts as another project", [
        (LOCK, "                    return PROCESS_STATE_UNKNOWN, f\"the arguments of Unity process {pid} cannot be read "
               "({problem})\"",
         "                    continue")]),
    ("the Unity CLI and other images count as the Editor", [
        (LOCK, "                if not _same_file(image, editor):\n                    continue",
         "                if False:\n                    continue")]),
    ("a lockfile user is believed without its start time", [
        (LOCK, "            if process.running() is not True or process.created() != started:",
         "            if process.running() is not True:")]),
    ("one empty Restart Manager answer proves an orphan", [
        (LOCK, "    final = windows_process_proof(project, editor, host)\n    a.processes.append(final)\n"
               "    if final[0] == MATCHING_EDITOR:",
         "    a.state = NO_LOCK if lock == ABSENT else ORPHAN_UNHELD\n    return a\n"
         "    final = windows_process_proof(project, editor, host)\n    a.processes.append(final)\n"
         "    if final[0] == MATCHING_EDITOR:")]),
    ("a lockfile replaced during the query is not noticed", [
        (LOCK, "    if (after.st_dev, after.st_ino) != identity or host_win32.is_reparse(after):",
         "    if host_win32.is_reparse(after):")]),
    ("the Windows proof has no time bound", [
        (LOCK, "        if time.monotonic() > deadline:", "        if False:")]),
    ("the Windows lock query opens the lockfile", [
        (LOCK, "    identity = (before.st_dev, before.st_ino)\n",
         "    identity = (before.st_dev, before.st_ino)\n    open(lock, \"rb\").close()\n")]),
    ("a failed Restart Manager query reads as nobody", [
        (LOCK, "    if problem:\n        return UNKNOWN, problem, None", "    if problem:\n        return UNHELD, None, None")]),
    ("the Package Manager failure is not classified", [
        (RESULTS, "    (PACKAGE_MANAGER_UNAVAILABLE, (\"Failed to start the Unity Package Manager local server process\",)),\n",
         "")]),
    ("Restart Manager reports the wrong start time", [
        (HOST, "        return [(int(info[i].Process.dwProcessId), _filetime(info[i].Process.ProcessStartTime))",
         "        return [(int(info[i].Process.dwProcessId), _filetime(info[i].Process.ProcessStartTime) + 1)")]),
    ("the command line is split on spaces", [
        (HOST, "        array = split(line, ctypes.byref(count))\n        if not array:",
         "        return line.split(), None\n        array = split(line, ctypes.byref(count))\n        if not array:")]),
]

REAL = [
    # (name, edits, the real tests that must catch it)
    ("REAL the Unity child lacks ProgramData", [
        (ADAPTER, 'WINDOWS_ENVIRONMENT = ("ProgramData", "LOCALAPPDATA")', 'WINDOWS_ENVIRONMENT = ("LOCALAPPDATA",)')],
     ["WG_RealBatch.test_editmode_pass_offers_test_evidence"]),
    ("REAL an open Editor is never seen", [
        (LOCK, "    first = windows_process_proof(project, editor, host)\n    a.processes.append(first)\n",
         "    a.state = NO_LOCK\n    return a\n    first = windows_process_proof(project, editor, host)\n"
         "    a.processes.append(first)\n")],
     ["WH_RealLockAndJob.test_an_open_editor_blocks_the_run_and_its_orphan_does_not"]),
    ("REAL an orphan lockfile blocks every later run", [
        (LOCK, '    a.state = NO_LOCK if lock == ABSENT else ORPHAN_UNHELD\n    if a.state == ORPHAN_UNHELD:\n'
               '        a.reasons.append("Temp/UnityLockfile is a regular file nobody holds (two Restart Manager',
         '    a.state = NO_LOCK if lock == ABSENT else LOCK_STATE_UNKNOWN\n    if a.state == ORPHAN_UNHELD:\n'
         '        a.reasons.append("Temp/UnityLockfile is a regular file nobody holds (two Restart Manager')],
     ["WH_RealLockAndJob.test_an_open_editor_blocks_the_run_and_its_orphan_does_not"]),
]
REAL_BASELINE = sorted({t for _, _, tests in REAL for t in tests})
MODE = {"real": False}   # set by main(); the gate's own baseline (no edit) then runs exactly the real tests


def run(mutation):
    name, edits = mutation[0], mutation[1]
    tests = mutation[2] if len(mutation) > 2 else (REAL_BASELINE if MODE["real"] else None)
    tmp = Path(tempfile.mkdtemp(prefix="gpos-unitywinmut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for rel, anchor, replacement in edits:
            path = copy / rel
            text = gate.read(path)
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            gate.write(path, text.replace(anchor, replacement))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        if tests is None:
            env["GPOS_UNITY_TEST_FAST"] = "1"
        else:
            env.pop("GPOS_UNITY_TEST_FAST", None)
        try:
            out = subprocess.run([*gate.PYTHON, "-B", str(copy / "tests" / "test_unity_windows.py"), *(tests or [])],
                                 capture_output=True, text=True, env=env, **gate.ISOLATED,
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
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    parser.add_argument("--real", action="store_true", help="the bounded real-Editor mutations (one Editor at a time)")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("NOT_RUN      every mutation (the Windows Unity batch plane is Windows only)")
        return gate.BLOCKED_EXIT
    MODE["real"] = args.real
    pool = REAL if args.real else FAST
    selected = [m for m in pool if not args.only or args.only in m[0]]
    return gate.qualify(run, selected, 1 if args.real else args.jobs)


if __name__ == "__main__":
    sys.exit(main())
