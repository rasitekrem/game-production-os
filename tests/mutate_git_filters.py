#!/usr/bin/env python3
"""Focused D-G1 mutation qualification, with an identical passing baseline first.

Only assertion failures are CAUGHT; crashes, timeouts and setup failures are inconclusive.
Copies and detailed per-mutant output live only in this run's disposable temporary lab.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import mutation_gate as gate
import windows_standin

ROOT = Path(__file__).resolve().parent.parent
ADAPTER = "gpos/tools/git/adapter.py"
INSPECTION = "gpos/tools/git/inspection.py"
MUTATIONS = [
    ("filter attribute gate removed", [(ADAPTER,
        "                gi.refuse_filters(self._private_read(context, argv, target, copy), len(argv) - len(gi.ATTRIBUTES_ARGV))",
        "                self._private_read(context, argv, target, copy)")]),
    ("frozen filter definitions retained", [(INSPECTION,
        ' or lower.startswith("filter.")', "")]),
    ("global configuration isolation removed", [(ADAPTER,
        '("GIT_CONFIG_GLOBAL", os.devnull)', '("GIT_CONFIG_GLOBAL", os.environ.get("HOME", "") + "/.gitconfig")')]),
    ("original source status restored", [(ADAPTER,
        'self._status_capture_bytes, copy.root, copy)', 'self._status_capture_bytes)')]),
    ("submodule discovery removed", [(ADAPTER, '            for name in children:', '            for name in ():')]),
    ("source stability verification removed", [(ADAPTER, '            copy.verify()', '            pass')]),
    ("stat cache invalidation removed", [(ADAPTER, '        copy.invalidate_stats()', '        pass')]),
    ("assume unchanged flags accepted", [(INSPECTION,
        ' or fields[0].islower()', '')]),
    ("skip worktree flags accepted", [(INSPECTION,
        ' or fields[0] in (b"S", b"s")', '')]),
    ("byte bound removed", [(INSPECTION, ' or self.remaining < 0', '')]),
    ("partial clone configuration accepted", [(INSPECTION,
        'if lower.endswith(".promisor") or lower == "extensions.partialclone":', 'if False:')]),
    ("external ignore path isolation removed", [(ADAPTER, '            copy.excludes(target, excludes)', '            pass')]),
    ("linked repository layout recheck removed", [(ADAPTER,
        'if tuple((root / os.fsdecode(line)).resolve() for line in current) != layout:', 'if False:')]),
]


def run(mutation):
    name, edits = mutation
    temporary = Path(tempfile.mkdtemp(prefix="gpos-dg1-mutant-")).resolve()
    try:
        copy = temporary / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for relative, anchor, replacement in edits:
            path = copy / relative
            source = gate.read(path)
            if source.count(anchor) != 1:
                return name, "NOT APPLIED (nonunique anchor)"
            gate.write(path, source.replace(anchor, replacement))
        try:
            outcome = subprocess.run([*gate.PYTHON, "-B", str(copy / "tests/test_git_filters.py")],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True,
                timeout=180, **gate.ISOLATED)
        except subprocess.TimeoutExpired:
            return name, "INCONCLUSIVE (timeout)"
        log = temporary.parent / (temporary.name + ".log")
        log.write_bytes((outcome.stdout + outcome.stderr).encode("utf-8"))
        if outcome.returncode == 0:
            return name, "MISSED"
        if "FAIL:" in outcome.stderr and "AssertionError:" in outcome.stderr and "ERROR:" not in outcome.stderr:
            return name, "CAUGHT"
        return name, "INCONCLUSIVE (crash or setup failure; inspect lab log)"
    finally:
        windows_standin.remove_tree(temporary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--only", help="run only mutation names containing this text, after the same full baseline")
    arguments = parser.parse_args()
    selected = [m for m in MUTATIONS if not arguments.only or arguments.only in m[0]]
    if not selected:
        sys.exit("NO_MUTATIONS_SELECTED")
    sys.exit(gate.qualify(run, selected, jobs=arguments.jobs))
