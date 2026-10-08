#!/usr/bin/env python3
"""The TEST_ONLY Windows tool stand-in (alpha.24) propagates exactly what a real tool would.

    python -X utf8 tests/test_windows_standin.py

Windows only (skipped elsewhere: POSIX stand-ins are plain scripts). Every run goes through the alpha.23 process
boundary, so the stand-in and its Python child are contained by GPOS's Job Object like any tool.
"""

import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
WINDOWS = sys.platform == "win32"
if WINDOWS and not sys.flags.utf8_mode:
    sys.exit("WINDOWS_UTF8_MODE_REQUIRED: run this suite as `python -X utf8 tests/test_windows_standin.py`")

import windows_standin as ws  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402

ECHO = r"""
import json, os, sys, time
args = sys.argv[1:]
if args[:1] == ["sleep"]:
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pid"), "w").write(str(os.getpid()))
    time.sleep(float(args[1]))
sys.stdout.buffer.write(json.dumps(args).encode("utf-8") + b"\n" + bytes(range(256)))
sys.stderr.buffer.write("stderr · ğ\n".encode("utf-8"))
sys.exit(int(os.environ.get("STANDIN_EXIT", "0")))
"""


@unittest.skipUnless(WINDOWS, "the Windows stand-in exists only on Windows")
class S01_StandIn(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-standin-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tool = ws.write_tool(self.tmp / "bin" / "tool", ECHO)

    def run_tool(self, argv, timeout=30.0, env=()):
        spec = tproc.ToolProcessSpec(executable=str(self.tool), argv=tuple(argv), cwd=str(self.tmp), timeout=timeout,
                                     env=tproc.EnvironmentPolicy(overrides=tuple(env)))
        return tproc.run_process(spec, [str(self.tmp)])

    def test_it_is_a_dot_exe_built_from_the_reviewed_source_by_the_system_compiler(self):
        self.assertEqual(self.tool.name, "tool.exe")
        self.assertEqual(ws.IDENTITY["source_sha256"], hashlib.sha256(ws.SOURCE.read_bytes()).hexdigest())
        self.assertTrue(ws.IDENTITY["compiler"].lower().endswith(r"framework64\v4.0.30319\csc.exe"))
        self.assertIn("Compiler version", ws.IDENTITY["compiler_banner"])
        self.assertEqual(len(ws.IDENTITY["image_sha256"]), 64)

    def test_the_exact_argument_vector_reaches_the_script(self):
        argv = ["-version", "with space", 'a"quote', "back\\slash\\", "", "ğ·測", "x\\\"y", "tab\there", "C:\\a b\\"]
        outcome = self.run_tool(argv)
        self.assertEqual(outcome.exit_code, 0, outcome.stderr)
        import json
        self.assertEqual(json.loads(outcome.raw_stdout.split(b"\n", 1)[0].decode("utf-8")), argv)

    def test_stdout_and_stderr_bytes_and_the_exit_code_propagate(self):
        outcome = self.run_tool(["x"], env=(("STANDIN_EXIT", "37"),))
        self.assertEqual(outcome.exit_code, 37)
        self.assertTrue(outcome.raw_stdout.endswith(bytes(range(256))))
        self.assertEqual(outcome.raw_stderr, "stderr · ğ\n".encode("utf-8"))
        self.assertTrue(outcome.tree_contained and outcome.capture_complete)

    def test_a_timeout_ends_the_stand_in_and_its_child(self):
        started = time.monotonic()
        outcome = self.run_tool(["sleep", "60"], timeout=1.5)
        self.assertTrue(outcome.timed_out and outcome.tree_contained)
        self.assertLess(time.monotonic() - started, 20)
        child = int((self.tool.parent / "pid").read_text())
        for _ in range(100):
            if not tproc.host_pid_alive(child):
                break
            time.sleep(0.05)
        self.assertFalse(tproc.host_pid_alive(child))

    def test_without_its_fixed_script_or_interpreter_it_refuses(self):
        (self.tool.parent / "tool.standin.py").unlink()
        outcome = self.run_tool(["x"])
        self.assertEqual(outcome.exit_code, 97)
        self.assertIn("no script beside the stand-in", outcome.stderr)
        ws.write_tool(self.tmp / "bin" / "tool", ECHO)
        (self.tool.parent / "standin.interpreter").write_text("relative\\python.exe")
        self.assertEqual(self.run_tool(["x"]).exit_code, 97)

    def test_a_missing_compiler_fails_closed(self):
        from unittest import mock
        with mock.patch.object(ws, "_BUILT", []), mock.patch.object(ws, "COMPILER", self.tmp / "absent" / "csc.exe"):
            with self.assertRaises(ws.StandInUnavailable) as cm:
                ws.build()
        self.assertIn(ws.UNAVAILABLE, str(cm.exception))


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    if ws.IDENTITY:
        print("stand-in:", ", ".join(f"{k}={v}" for k, v in sorted(ws.IDENTITY.items())))
    sys.exit(0 if result.wasSuccessful() else 1)
