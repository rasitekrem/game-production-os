#!/usr/bin/env python3
"""Baseline-gated mutations of only the newly authorized Windows Git exposure."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import mutation_gate as gate
import windows_standin

ROOT = Path(__file__).resolve().parent.parent
MUTATIONS = [
    ("Windows production platform withdrawn", [("gpos/tools/git/adapter.py",
       'supported_platforms=("MACOS", "LINUX", "WINDOWS"), capabilities=CAPABILITIES,',
       'supported_platforms=("MACOS", "LINUX"), capabilities=CAPABILITIES,')]),
    ("production registry forgets Git", [("gpos/tools/registry.py",
       '(AdbAdapter(), BlenderAdapter(), FfmpegAdapter(), FfprobeAdapter(), GitAdapter(), UnityAdapter())',
       '(AdbAdapter(), BlenderAdapter(), FfmpegAdapter(), FfprobeAdapter(), UnityAdapter())')]),
]


def run(mutation):
    name, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-git-gate-mutant-")).resolve()
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for relative, anchor, replacement in edits:
            path = copy / relative
            source = gate.read(path)
            if source.count(anchor) != 1:
                return name, "NOT APPLIED (nonunique anchor)"
            gate.write(path, source.replace(anchor, replacement))
        try:
            outcome = subprocess.run([*gate.PYTHON, "-B", str(copy / "tests/test_git_windows_production.py")],
                                     capture_output=True, text=True, timeout=120, **gate.ISOLATED,
                                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        except subprocess.TimeoutExpired:
            return name, "INCONCLUSIVE (timeout)"
        (tmp.parent / (tmp.name + ".log")).write_bytes((outcome.stdout + outcome.stderr).encode("utf-8"))
        if outcome.returncode == 0:
            return name, "MISSED"
        # Unavailability may make later fixture checks raise: that is not a kill.
        if "FAIL:" in outcome.stderr and "AssertionError:" in outcome.stderr and "ERROR:" not in outcome.stderr:
            return name, "CAUGHT"
        return name, "INCONCLUSIVE (crash or setup error)"
    finally:
        windows_standin.remove_tree(tmp)


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("WINDOWS_RUNTIME_REQUIRED; no mutation qualification claimed")
    sys.exit(gate.qualify(run, MUTATIONS, jobs=2))
