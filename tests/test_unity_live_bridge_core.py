#!/usr/bin/env python3
"""Phase 2C-6A — tests of the GPOS live bridge's Unity-free C# core.

    python3 tests/test_unity_live_bridge_core.py

Compiles gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/*.cs together with
tests/unity_live_bridge_core/*.cs using the Mono C# compiler bundled with the installed Unity Hub Editor, and runs
the result with that Mono. No Unity process is started and nothing is written outside a temporary directory. The
suite fails (never skips) when no single Hub Editor with its bundled Mono is installed:
UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gpos.tools.unity import UnityAdapter  # noqa: E402

CORE = ROOT / "gpos" / "tools" / "unity" / "live_bridge" / "com.gpos.live-bridge" / "Editor" / "Core"
TESTS = ROOT / "tests" / "unity_live_bridge_core"


def toolchain():
    editors = UnityAdapter().discover()
    if len(editors) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A: {len(editors)} Hub Unity Editors found")
    scripting = Path(editors[0][1]).parents[1] / "Resources" / "Scripting"
    mono = scripting / "MonoBleedingEdge" / "bin" / "mono"
    csc = scripting / "MonoBleedingEdge" / "lib" / "mono" / "4.5" / "csc.exe"
    netstandard = scripting / "NetStandard" / "ref" / "2.1.0" / "netstandard.dll"
    for p in (mono, csc, netstandard):
        if not p.exists():
            raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C6A: {p} is missing")
    return mono, csc, scripting


def compile_and_run(sources, work):
    mono, csc, scripting = toolchain()
    exe = Path(work) / "core-tests.exe"
    facades = scripting / "MonoBleedingEdge" / "lib" / "mono" / "4.7.1-api"
    refs = [f"-r:{facades / n}" for n in ("mscorlib.dll", "System.dll", "System.Core.dll")]
    compiled = subprocess.run([str(mono), str(csc), "-noconfig", "-nologo", "-nostdlib", "-t:exe", f"-out:{exe}",
                               *refs, *map(str, sources)], capture_output=True, text=True, timeout=300)
    if compiled.returncode != 0:
        return None, compiled.stdout + compiled.stderr
    ran = subprocess.run([str(mono), str(exe)], capture_output=True, text=True, timeout=300)
    return ran, ran.stdout + ran.stderr


class CoreSuite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gpos-live-core-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_core_needs_no_unity_types(self):
        for f in CORE.glob("*.cs"):
            text = f.read_text()
            self.assertNotIn("using UnityEditor", text, f.name)
            self.assertNotIn("using UnityEngine", text, f.name)

    def test_core_behaviour(self):
        ran, output = compile_and_run(sorted(CORE.glob("*.cs")) + sorted(TESTS.glob("*.cs")), self.tmp)
        self.assertIsNotNone(ran, f"the core does not compile:\n{output}")
        print(output.strip())
        self.assertEqual(ran.returncode, 0, output)
        self.assertIn("ALL PASSED", output)
        self.assertNotIn("FAIL ", output)


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
