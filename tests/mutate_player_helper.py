#!/usr/bin/env python3
"""Mutation harness for the native GPOS Player Helper (alpha.22): supervise-mode and request-validation guarantees.

    python3 tests/mutate_player_helper.py [--jobs N] [--only TEXT] [--anchors]

Each mutation edits the helper's Swift source in a temporary copy of the repository, rebuilds the helper release there
with tests/build_player_helper.py --write (so the copy's manifest, digest and CDHash describe the mutated helper), and
runs tests/test_player_helper.py RH1 RH2 RH3 against that copy. Only modes that need no Screen Recording are exercised,
so no mutated helper ever needs a privacy grant, and the Human-granted install at ~/Applications/GPOS is never touched.
Capture-mode guarantees are pinned statically by tests/test_player.py (see tests/mutate_player.py) and by the real
capture groups. A mutation must make the suite fail ("CAUGHT"); "MISSED" and "NOT APPLIED" fail this harness.
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
SRC = "gpos/tools/player/helper_src/"
SUP, COM = SRC + "Supervise.swift", SRC + "Common.swift"

MUTATIONS = [
    ("the Player gets an extra argument", [(SUP, '[strdup(r.executable), strdup("-logFile"), strdup(logPath), nil]',
                                           '[strdup(r.executable), strdup("-logFile"), strdup(logPath), strdup("-batchmode"), nil]')]),
    ("the Player environment is widened", [(SUP, 'let envList = ["HOME", "TMPDIR", "LANG"]', 'let envList = ["HOME", "TMPDIR", "LANG", "GPOS_SHOULD_NOT_LEAK"]')]),
    ("the executable is not checked", [(SUP, "    try checkPlayerExecutable(r)\n", "")]),
    ("the workspace chain is not checked", [(COM, "    guard !root.isEmpty, !chain.contains(where: isLink), realPath(dir) == dir, realPath(root) == root else {",
                                             "    guard !root.isEmpty else {")]),
    ("no commit deadline", [(SUP, "                } else if uptime() - spawnedAt > Double(r.commitDeadline) {", "                } else if false {")]),
    ("an abort is ignored", [(SUP, '                } else if controlRecord(ws + "/abort.json", schema: "gpos.player.abort/1", r, extra: ["reason"]) != nil {\n                    stopReason = "ABORT"',
                              '                } else if controlRecord(ws + "/abort.json", schema: "gpos.player.abort/1", r, extra: ["reason"]) != nil {\n                    _ = 0')]),
    ("a stop intent's nonce is not checked", [(SUP, '          d["session_id"] as? String == r.sessionId, d["nonce"] as? String == r.nonce else { return nil }',
                                               '          d["session_id"] as? String == r.sessionId else { return nil }'),
                                              (SUP, 'Set(d.keys) == Set(["schema", "session_id", "nonce"]).union(extra)', 'Set(d.keys).isSuperset(of: Set(["schema", "session_id"]))')]),
    ("no kill after the grace", [(SUP, "                kill(child, SIGKILL)   // our own child, not yet reaped: its pid cannot have been reused\n",
                                  "                _ = 0\n")]),
    ("the Player shares the supervisor's process group", [(SUP, "    posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF",
                                                           "    posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF")]),
    ("an earlier launch's runtime directory is reused", [(SUP, '    for name in ["handshake.json", "exit.json", "player.log", "commit.json", "runtime-binding.json"] where exists(ws + "/" + name) {',
                                                          '    for name in [String]() where exists(ws + "/" + name) {')]),
]


def apply(copy, edits):
    for rel, anchor, replacement in edits:
        path = copy / rel
        text = path.read_text()
        if text.count(anchor) != 1:
            return f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
        path.write_text(text.replace(anchor, replacement))
    return None


def run(mutation):
    name, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-helpermut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        problem = apply(copy, edits)
        if problem:
            return name, problem
        scratch = tmp / "t"
        scratch.mkdir()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(scratch))   # every test temp dir inside tmp
        built = subprocess.run([sys.executable, "-B", str(copy / "tests" / "build_player_helper.py"), "--write"],
                               capture_output=True, text=True, timeout=900, env=env)
        if built.returncode != 0:
            return name, "CAUGHT"   # the mutation does not even compile
        try:
            out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_player_helper.py"), "RH1_Supervise",
                                  "RH2_Refusals", "RH3_Modes"], capture_output=True, text=True, timeout=1800, env=env)
        except subprocess.TimeoutExpired:
            return name, "CAUGHT"
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        _stop_stubs(tmp)
        shutil.rmtree(tmp, ignore_errors=True)


def _stop_stubs(tmp):
    """Never leave a stub Player or supervisor of this copy running (matched by its exact path in the copy)."""
    from gpos.tools.player import macos
    for pid in macos.all_pids():
        f = macos.facts(pid)
        if f and f["executable"] and f["executable"].startswith(str(tmp.resolve())):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


def anchors():
    problems = []
    for name, edits in MUTATIONS:
        for rel, anchor, _ in edits:
            n = (ROOT / rel).read_text().count(anchor)
            if n != 1:
                problems.append(f"{name}: {rel} anchor found {n} times")
    print("\n".join(problems) or f"all anchors of {len(MUTATIONS)} mutations apply exactly once")
    return 1 if problems else 0


def main():
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--only")
    parser.add_argument("--anchors", action="store_true")
    args = parser.parse_args()
    if args.anchors:
        return anchors()
    selected = [m for m in MUTATIONS if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
