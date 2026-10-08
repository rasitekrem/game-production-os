#!/usr/bin/env python3
"""Tests for the mutation baseline gate (alpha.23): a harness never counts a mutant when its baseline is not green.

    python3 tests/test_mutation_gate.py          (on Windows: python -X utf8 tests/test_mutation_gate.py)

Standard library only. No suite is run for real: each harness's run() is replaced by a stub, so these tests prove
the gate's own logic and that every one of the 16 harnesses is wired through it.
"""

import ast
import io
import sys
import tempfile
import unittest
from pathlib import Path

if sys.platform == "win32" and not sys.flags.utf8_mode:   # alpha.23: the Windows locale is not UTF-8
    sys.exit("WINDOWS_UTF8_MODE_REQUIRED: run this suite as `python -X utf8 tests/test_mutation_gate.py`")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import mutation_gate as gate  # noqa: E402

HARNESSES = sorted((ROOT / "tests").glob("mutate_*.py"))
SUITE_HARNESSES = {"mutate_player.py", "mutate_unity_assets.py", "mutate_unity_authoring.py", "mutate_unity_build.py",
                   "mutate_unity_live.py", "mutate_unity_prefabs.py", "mutate_unity_sources.py"}


class Stub:
    """A harness run(): the baseline (no edits) gets `baseline`, a mutant gets its own verdict."""

    def __init__(self, baseline, mutants=None, raises=False):
        self.baseline, self.mutants, self.raises, self.calls = baseline, dict(mutants or {}), raises, []

    def __call__(self, mutation):
        self.calls.append(mutation[0])
        edits = mutation[-1]
        if not edits:
            if self.raises:
                raise RuntimeError("the copy could not be made")
            return mutation[0], self.baseline
        return mutation[0], self.mutants[mutation[0]]


def mutant(name):
    return (name, [("gpos/x.py", "a", "b")])


class G01_BaselineGate(unittest.TestCase):
    def qualify(self, run, mutations, **kw):
        out = io.StringIO()
        return gate.qualify(run, mutations, jobs=2, out=out, **kw), out.getvalue(), run

    def test_a_red_baseline_blocks_and_counts_nothing(self):
        code, text, run = self.qualify(Stub("CAUGHT", {"m1": "CAUGHT", "m2": "CAUGHT"}), [mutant("m1"), mutant("m2")])
        self.assertEqual(code, gate.BLOCKED_EXIT)
        self.assertEqual(run.calls, [gate.BASELINE])          # no mutant ran
        self.assertIn("BLOCKED", text)
        self.assertNotIn("caught", text)                       # nothing was counted

    def test_a_crashed_baseline_blocks(self):
        code, text, run = self.qualify(Stub(None, {"m1": "CAUGHT"}, raises=True), [mutant("m1")])
        self.assertEqual(code, gate.BLOCKED_EXIT)
        self.assertEqual(run.calls, [gate.BASELINE])
        self.assertIn("NOT_RUN", text)

    def test_a_baseline_that_could_not_run_blocks(self):
        for verdict in ("NOT APPLIED (no physical target is configured)", "ERROR (x)", None, ""):
            code, _, run = self.qualify(Stub(verdict, {"m1": "CAUGHT"}), [mutant("m1")])
            self.assertEqual(code, gate.BLOCKED_EXIT, verdict)
            self.assertEqual(run.calls, [gate.BASELINE], verdict)

    def test_a_timed_out_baseline_blocks(self):
        """Harnesses report a hang as CAUGHT; for the baseline that is a red suite."""
        self.assertEqual(gate.baseline_status("CAUGHT"), "RED")
        code, _, _ = self.qualify(Stub("CAUGHT", {"m1": "CAUGHT"}), [mutant("m1")])
        self.assertEqual(code, gate.BLOCKED_EXIT)

    def test_a_green_baseline_runs_every_mutant_independently(self):
        code, text, run = self.qualify(Stub("MISSED", {"m1": "CAUGHT", "m2": "CAUGHT"}), [mutant("m1"), mutant("m2")])
        self.assertEqual(code, gate.QUALIFIED_EXIT)
        self.assertEqual(sorted(run.calls), sorted([gate.BASELINE, "m1", "m2"]))
        self.assertIn("caught 2 of 2", text)

    def test_a_missed_mutant_fails_after_a_green_baseline(self):
        code, text, _ = self.qualify(Stub("MISSED", {"m1": "CAUGHT", "m2": "MISSED"}), [mutant("m1"), mutant("m2")])
        self.assertEqual(code, gate.MISSED_EXIT)
        self.assertIn("caught 1 of 2", text)

    def test_a_mutant_whose_anchor_rotted_still_fails(self):
        code, _, _ = self.qualify(Stub("MISSED", {"m1": "NOT APPLIED (x: anchor found 0 times)"}), [mutant("m1")])
        self.assertEqual(code, gate.MISSED_EXIT)

    def test_every_suite_baseline_must_pass(self):
        run = Stub("MISSED", {"m1": "CAUGHT"})
        calls = []

        def per_suite(mutation):
            calls.append(mutation[0])
            if not mutation[-1]:
                return mutation[0], "CAUGHT" if mutation[1] == "real" else "MISSED"
            return run(mutation)

        mutations = [("m1", "fast", [("x", "a", "b")]), ("m2", "real", [("x", "a", "b")])]
        baselines = [(f"base [{s}]", s, []) for s in sorted({m[1] for m in mutations})]
        code, text, _ = self.qualify(per_suite, mutations, baselines=baselines)
        self.assertEqual(code, gate.BLOCKED_EXIT)
        self.assertEqual(sorted(calls), ["base [fast]", "base [real]"])
        self.assertIn("base [real] is RED", text)

    def test_an_empty_baseline_list_is_not_a_pass(self):
        code, _, run = self.qualify(Stub("MISSED", {"m1": "CAUGHT"}), [mutant("m1")], baselines=[])
        self.assertEqual(code, gate.BLOCKED_EXIT)
        self.assertEqual(run.calls, [])


class G04_HostApplicability(unittest.TestCase):
    def test_a_mutation_for_another_host_is_listed_not_run_and_never_counted(self):
        other = "POSIX" if gate.HOST == "WINDOWS" else "WINDOWS"
        here, elsewhere = gate.for_host([mutant("m1"), mutant("m2")], {"m2": other})
        self.assertEqual(([m[0] for m in here], [m[0] for m in elsewhere]), (["m1"], ["m2"]))
        out = io.StringIO()
        run = Stub("MISSED", {"m1": "CAUGHT", "m2": "MISSED"})
        code = gate.qualify(run, here, jobs=1, out=out, not_run=elsewhere)
        self.assertEqual(code, gate.QUALIFIED_EXIT)
        self.assertNotIn("m2", run.calls)
        self.assertIn("NOT_RUN      m2", out.getvalue())
        self.assertIn("caught 1 of 1 (1 NOT_RUN on this host)", out.getvalue())

    def test_a_run_specific_not_run_names_its_reason_and_is_never_counted(self):
        """alpha.24: an offline adb run lists device-only mutations as NOT_RUN with why, never as caught."""
        out = io.StringIO()
        run = Stub("MISSED", {"m1": "CAUGHT", "m2": "MISSED"})
        code = gate.qualify(run, [mutant("m1")], jobs=1, out=out, not_run=[mutant("m2")],
                            not_run_reason="needs a real target; this run is offline")
        self.assertEqual(code, gate.QUALIFIED_EXIT)
        self.assertNotIn("m2", run.calls)
        self.assertIn("NOT_RUN      m2 (needs a real target; this run is offline)", out.getvalue())

    def test_not_run_mutations_do_not_bypass_a_red_baseline(self):
        out = io.StringIO()
        code = gate.qualify(Stub("CAUGHT", {"m1": "CAUGHT"}), [mutant("m1")], jobs=1, out=out,
                            not_run=[mutant("m2")])
        self.assertEqual(code, gate.BLOCKED_EXIT)

    def test_windows_suites_run_isolated_in_their_own_console(self):
        self.assertEqual(gate.ISOLATED, {"creationflags": 0x08000000} if sys.platform == "win32" else {})
        for name in ("mutate_tools.py", "mutate_production.py", "mutate_adapters.py"):
            self.assertIn("**gate.ISOLATED", (ROOT / "tests" / name).read_bytes().decode("utf-8"), name)


class G02_ByteExactEdits(unittest.TestCase):
    def test_edits_keep_lf_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.py"
            path.write_bytes("a = 1\n# Ataklı · é\nb = 2\n".encode("utf-8"))
            gate.write(path, gate.read(path).replace("b = 2", "b = 3"))
            self.assertEqual(path.read_bytes(), "a = 1\n# Ataklı · é\nb = 3\n".encode("utf-8"))

    def test_the_suite_interpreter_carries_utf8_mode_on_windows_only(self):
        self.assertEqual(gate.PYTHON[0], sys.executable)
        self.assertEqual(gate.PYTHON[1:], ["-X", "utf8"] if sys.platform == "win32" else [])


class G03_EveryHarnessIsGated(unittest.TestCase):
    def test_there_are_sixteen_harnesses(self):
        self.assertEqual(len(HARNESSES), 16, [p.name for p in HARNESSES])

    def test_every_main_returns_the_gate_and_counts_nothing_itself(self):
        for path in HARNESSES:
            tree = ast.parse(path.read_bytes().decode("utf-8"))
            main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
            calls = [n for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "qualify" and getattr(n.func.value, "id", None) == "gate"]
            self.assertEqual(len(calls), 1, path.name)
            returns = [n for n in ast.walk(main) if isinstance(n, ast.Return)]
            self.assertTrue(any(isinstance(r.value, ast.Call) and r.value in calls for r in returns), path.name)
            text = ast.unparse(main)
            self.assertNotIn("ThreadPoolExecutor", text, path.name)
            self.assertNotIn('"CAUGHT"', text, path.name)

    def test_suite_harnesses_baseline_every_suite_they_use(self):
        for path in HARNESSES:
            text = path.read_bytes().decode("utf-8")
            if path.name in SUITE_HARNESSES:
                self.assertIn('for suite in sorted({m[1] for m in selected})', text, path.name)
            else:
                self.assertNotIn("baselines =", text, path.name)

    def test_no_harness_rewrites_files_through_text_mode(self):
        for path in HARNESSES:
            text = path.read_bytes().decode("utf-8")
            calls = {n.func.attr for n in ast.walk(ast.parse(text))
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            self.assertFalse(calls & {"read_text", "write_text"}, path.name)   # anchors may quote them; calls may not
            self.assertNotIn("[sys.executable,", text, path.name)
            self.assertIn("import mutation_gate as gate", text, path.name)

    def test_four_tuple_harnesses_run_a_baseline_with_no_edit(self):
        for name in ("mutate_adapters.py", "mutate_production.py"):
            text = (ROOT / "tests" / name).read_bytes().decode("utf-8")
            self.assertIn("if rel is not None:", text, name)


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
