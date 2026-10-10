#!/usr/bin/env python3
"""alpha.22 POSIX source parity (alpha.23): Windows support may add, never silently change, the frozen POSIX paths.

    python3 tests/test_posix_parity.py          (on Windows: python -X utf8 tests/test_posix_parity.py)

Compares the working tree with tests/fixtures/posix-parity-alpha22.json, which tests/generate_posix_parity.py wrote
from the frozen v1.0.0-alpha.22 tag. Standard library only; it runs no process and needs no git.

This is source evidence, not execution evidence: it proves which POSIX code is unchanged, and lists every deliberate
shared change with its reason, so a reviewer sees exactly what a macOS regression run would have to cover. It is
never reported as a macOS PASS.
"""

import ast
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
if sys.platform == "win32" and not sys.flags.utf8_mode:
    sys.exit("WINDOWS_UTF8_MODE_REQUIRED: run this suite as `python -X utf8 tests/test_posix_parity.py`")

import posix_parity as pp  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "posix-parity-alpha22.json").read_bytes().decode("utf-8"))
BRIDGE_DIGEST = "b7f4775d4f1e807136df0978d27218eac20dbe5e163fe23618fd84a17a62ea16"
VERSION_FROZEN, VERSION_NOW = b"1.0.0-alpha.22", b"1.0.0-alpha.26"

# The only frozen files alpha.23 and alpha.24 edit. Every other file under core/, schemas/, skills/, workflows/,
# templates/ and gpos/ is byte-identical to alpha.22.
EDITED = {"gpos/tools/artifacts.py", "gpos/tools/cli.py", "gpos/tools/diagnostics.py", "gpos/tools/execution.py",
          "gpos/tools/leases.py", "gpos/tools/paths.py", "gpos/tools/process.py", "gpos/tools/registry.py",
          "gpos/tools/synthetic/helper.py", "gpos/tools/unity/project_lock.py",
          # alpha.24 (2C-9.2): Windows discovery guards, D-B1/D-B2/D-B3, D-W1 and the ADB server classification
          "gpos/tools/adb/adapter.py", "gpos/tools/blender/adapter.py", "gpos/tools/ffmpeg/adapter.py",
          "gpos/tools/ffprobe/adapter.py", "gpos/tools/git/adapter.py",
          # alpha.25 (2C-9.3a): the Windows Unity batch plane
          "gpos/tools/unity/adapter.py", "gpos/tools/unity/project.py", "gpos/tools/unity/results.py",
          # alpha.26 (2C-9.3b): the Windows live bridge (bridge 1.6.0) and its GPOS side
          "gpos/tools/unity/bridge_install.py", "gpos/tools/unity/identity.py", "gpos/tools/unity/live.py",
          "gpos/tools/unity/live_ipc.py", "gpos/tools/unity/live_status.py",
          "gpos/tools/unity/live_bridge/manifest.json", "gpos/tools/unity/live_bridge/com.gpos.live-bridge/package.json", "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/AssetAuthoring.cs",
          "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Commands.cs", "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/Protocol.cs", "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Identity.cs", "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Ipc.cs",
          "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/PrefabAuthoring.cs", "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/PrefabResolver.cs"}

# Every alpha.22 unit whose POSIX code changed beyond guarded `if sys.platform == "win32":` statements, and why. The
# mechanical checks below narrow several of them further; the rest are the shared changes a macOS regression covers.
DELIBERATE = {
    ("gpos/tools/process.py", "def run_process"):
        "split: validate, choose the host backend, pass every outcome through the integrity observer; the alpha.22 "
        "body moved verbatim into _run_posix (P03 proves it)",
    ("gpos/tools/process.py", "class ProcessOutcome"):
        "three appended fields with defaults (tree_contained, capture_complete, descendants_terminated) and the "
        "integrity_ok property; POSIX outcomes keep the defaults (P04 proves the alpha.22 fields are an unchanged "
        "prefix)",
    ("gpos/tools/artifacts.py", "def collect"):
        "the hash-and-size expression moved verbatim into _measure (P05 proves the substitution is the whole change)",
    ("gpos/tools/artifacts.py", "def collect_inputs"):
        "the hash-and-size expression moved verbatim into _measure (P05)",
    ("gpos/tools/artifacts.py", "def _relative"):
        "str(path) -> path.as_posix() for an artifact outside the project: the same string for a POSIX path (P05)",
    ("gpos/tools/diagnostics.py", "assign CODES"):
        "four codes added (three in alpha.23, DCC_CLEANUP_INCOMPLETE in alpha.24); every alpha.22 code keeps its "
        "class and meaning (P06)",
    ("gpos/tools/execution.py", "def request_problems"):
        "D12: an unrecognised host (current_platform() is None) now fails closed with PLATFORM_UNSUPPORTED; MACOS and "
        "LINUX hosts are judged exactly as before",
    ("gpos/tools/execution.py", "def execute"):
        "the adapter call runs inside the process-integrity observer; an unproven outcome adds blocking diagnostics "
        "and marks the run unfinished. On POSIX every outcome is proven, so no diagnostic is ever added",
    ("gpos/tools/registry.py", "def ToolRegistry.probe"):
        "D12 platform gate before the adapter is invoked, the probe inside the integrity observer, and the state "
        "update moved verbatim into ToolRegistry._record; on macOS every production adapter declares MACOS",
    ("gpos/tools/synthetic/helper.py", "def main"):
        "TEST_ONLY helper writes with newline='\\n' (the same bytes on POSIX)",
    ("gpos/tools/unity/project_lock.py", "assign OPEN_FLAGS"):
        "os.O_* read through getattr so the module imports on Windows; every flag exists on macOS, so the value is "
        "unchanged",
    # alpha.24 (2C-9.2); P08 proves each is exactly the change named
    ("gpos/tools/git/adapter.py", "assign DESCRIPTOR"):
        "D-W1: WINDOWS withdrawn from supported_platforms pending the D-G1 repository-filter decision; MACOS and LINUX "
        "are declared exactly as before",
    ("gpos/tools/adb/adapter.py", "assign DESCRIPTOR"):
        "D-W1: WINDOWS withdrawn from supported_platforms (no physical target or ADB server lifecycle is qualified "
        "there); MACOS and LINUX are declared exactly as before",
    ("gpos/tools/adb/adapter.py", "def _Runner.ready"):
        "a failed get-state whose stderr names a host ADB server failure is a tool failure (FAILED), checked before the "
        "target states; it applies on every host (a macOS regression covers it)",
    ("gpos/tools/blender/adapter.py", "def BlenderAdapter.execute"):
        "D-B1: a workspace render path holding `#`, `{` or `}` is refused before Blender starts; it applies on every "
        "host (a macOS regression covers it); the other alpha.24 statements are Windows guards",
    # alpha.25 (2C-9.3a); P09 proves each is exactly the change named
    ("gpos/tools/unity/adapter.py", "assign DESCRIPTOR"):
        "WINDOWS appended to supported_platforms (the batch plane only; every other capability is refused on Windows "
        "inside execute), the availability text and the first compatibility note name it; MACOS is declared exactly "
        "as before",
    ("gpos/tools/unity/adapter.py", "def UnityAdapter._classify"):
        "one more cause in the no-results message (the Package Manager server could not start); every other branch "
        "is unchanged and a macOS log without that text is classified exactly as before",
    ("gpos/tools/unity/results.py", "assign SIGNATURES"):
        "one more signature appended last (the Package Manager server failure, measured on Windows); the alpha.22 "
        "signatures keep their order, so a log they match is classified exactly as before",
    # alpha.26 (2C-9.3b); P10 proves each is exactly the change named
    ("gpos/tools/unity/bridge_install.py", "assign BRIDGE_VERSION"):
        "1.5.0 -> 1.6.0: the bridge package gains its Windows layer; macOS installs it too, and its macOS view is the "
        "1.5.0 source but for the version (P10); macOS runtime NOT_RUN",
    ("gpos/tools/unity/bridge_install.py", "assign PREVIOUS"):
        "1.5.0 appended last, pinned at its frozen digest (an installed 1.5.0 is upgraded with the existing "
        "transaction); every earlier pin is unchanged",
    ("gpos/tools/unity/bridge_install.py", "def runtime_path"):
        "a Windows guard nested in the component loop (a reparse point is refused like a link); without it the unit "
        "is the alpha.22 unit (P10)",
    ("gpos/tools/unity/bridge_install.py", "def tree"):
        "a Windows guard nested in the walk (a reparse point is a problem like a link); without it the unit is the "
        "alpha.22 unit (P10)",
    ("gpos/tools/unity/identity.py", "def gpos_root_of"):
        "a Windows guard nested in the root test (a reparse point on the way is no root, as for the bridge); without "
        "it the unit is the alpha.22 unit (P10)",
    ("gpos/tools/unity/live_ipc.py", "def Channel.__init__"):
        "a Windows guard nested in the folder loop (a reparse point is refused like a link); without it the unit is "
        "the alpha.22 unit (P10)",
}
IMPORT_CHANGES = {("gpos/tools/unity/project_lock.py", "import fcntl"):
                  "imported in try/except ImportError (fcntl = None on Windows); the macOS-only proofs never run there"}


def current_units(rel):
    return pp.units((ROOT / rel).read_bytes().decode("utf-8"))


class P01_FrozenFilesAreByteIdentical(unittest.TestCase):
    def test_the_fixture_is_the_frozen_tag(self):
        self.assertEqual((FIXTURE["tag"], FIXTURE["commit"]),
                         ("v1.0.0-alpha.22", "92ca10b582f1633201810781c11095a8db1ca21b"))

    def test_every_frozen_file_not_deliberately_edited_is_byte_identical_but_for_the_version(self):
        """A release bumps `1.0.0-alpha.22` to `1.0.0-alpha.26` (tests/validate_framework.py X05). That is the only
        change a frozen file outside EDITED may carry: putting the old version back gives its exact alpha.22 bytes."""
        for rel, digest in FIXTURE["frozen"].items():
            with self.subTest(file=rel):
                path = ROOT / rel
                self.assertTrue(path.is_file(), f"{rel} disappeared")
                data = path.read_bytes()
                restored = data.replace(VERSION_NOW, VERSION_FROZEN)
                if rel in EDITED:
                    self.assertNotEqual(hashlib.sha256(data).hexdigest(), digest, f"{rel} is listed as edited")
                else:
                    self.assertEqual(hashlib.sha256(restored).hexdigest(), digest,
                                     f"{rel}: changed beyond the version string")

    def test_the_version_is_the_only_text_the_release_changed_in_core_and_skills(self):
        bumped = [rel for rel, digest in FIXTURE["frozen"].items() if rel not in EDITED
                  and hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != digest]
        self.assertTrue(all(VERSION_NOW in (ROOT / rel).read_bytes() for rel in bumped))
        self.assertIn("core/registry.json", bumped)

    def test_no_new_file_appeared_in_the_frozen_areas_except_the_reviewed_ones(self):
        new = sorted(p.relative_to(ROOT).as_posix() for d in ("core", "schemas", "skills", "workflows", "templates",
                                                              "gpos")
                     for p in (ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts
                     and p.relative_to(ROOT).as_posix() not in FIXTURE["frozen"])
        self.assertEqual(new, ["gpos/tools/executables.py", "gpos/tools/paths_win32.py", "gpos/tools/process_win32.py",
                               "gpos/tools/unity/host_win32.py",
                               # alpha.26: the Windows core of bridge 1.6.0 and the pinned 1.5.0 release manifest
                               "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/WindowsFiles.cs", "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/WindowsFiles.cs.meta",
                               "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/WindowsPaths.cs", "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/WindowsPaths.cs.meta",
                               "gpos/tools/unity/live_bridge/history/1.5.0.json"])


class P02_PosixUnitsAreUnchanged(unittest.TestCase):
    def test_every_alpha22_unit_survives_unless_its_change_is_listed(self):
        changed = set()
        for rel in sorted(EDITED):
            old, new = FIXTURE["units"].get(rel), current_units(rel) if rel.endswith(".py") else None
            if old is None:
                continue
            for key, value in old.items():
                if key == "imports":
                    for statement in sorted(set(value) - set(new["imports"])):
                        with self.subTest(file=rel, unit=statement):
                            self.assertIn((rel, statement), IMPORT_CHANGES)
                    continue
                with self.subTest(file=rel, unit=key):
                    self.assertIn(key, new, f"{rel}: {key} was removed")
                    if new[key] != value:
                        changed.add((rel, key))
                        self.assertIn((rel, key), DELIBERATE, f"{rel}: {key} changed beyond a Windows guard")
        self.assertEqual(changed, set(DELIBERATE), "a listed change no longer differs: the list must stay exact")

    def test_only_one_guard_shape_was_added(self):
        """Any new platform conditional in an edited file is exactly `if sys.platform == "win32":` without else."""
        for rel in sorted(r for r in EDITED if r.endswith(".py")):
            tree = ast.parse((ROOT / rel).read_bytes().decode("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.If, ast.IfExp)) and "sys.platform" in ast.unparse(node.test):
                    with self.subTest(file=rel, test=ast.unparse(node.test)):
                        self.assertEqual(ast.unparse(node.test), pp.GUARD)
                        if isinstance(node, ast.If):
                            self.assertEqual(node.orelse, [])


class P03_TheMovedPosixBody(unittest.TestCase):
    def test_run_posix_is_the_alpha22_run_process_body_after_validation(self):
        old = ast.parse(FIXTURE["sources"]["gpos/tools/process.py"]["run_process"]).body[0]
        new = next(n for n in ast.parse((ROOT / "gpos/tools/process.py").read_bytes().decode("utf-8")).body
                   if isinstance(n, ast.FunctionDef) and n.name == "_run_posix")
        old_body = old.body[1:]                        # without the docstring
        self.assertEqual(ast.unparse(old_body[0]), "validate_spec(spec, scopes)")
        dump = lambda body: [ast.dump(s, include_attributes=False) for s in body]
        self.assertEqual(dump(new.body[1:]), dump(old_body[1:]))
        # the same parameters; run_process always passes `clock`, so the private body needs no default for it
        self.assertEqual([a.arg for a in new.args.args], [a.arg for a in old.args.args])


class P04_AppendedOutcomeFields(unittest.TestCase):
    def test_the_alpha22_outcome_fields_are_an_unchanged_prefix(self):
        old = FIXTURE["units"]["gpos/tools/process.py"]["class ProcessOutcome"]["body"]
        new = current_units("gpos/tools/process.py")["class ProcessOutcome"]["body"]
        self.assertEqual(new[:len(old)], old)
        from gpos.tools import process
        defaults = process.ProcessOutcome()
        self.assertEqual((defaults.tree_contained, defaults.capture_complete, defaults.descendants_terminated),
                         (True, True, 0))


class P05_OneExpressionSubstitutions(unittest.TestCase):
    def same_after(self, name, old_text, new_text):
        old = ast.parse(FIXTURE["sources"]["gpos/tools/artifacts.py"][name]).body[0]
        source = (ROOT / "gpos/tools/artifacts.py").read_bytes().decode("utf-8")
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
        text = ast.unparse(node)
        self.assertIn(new_text, text)
        back = ast.parse(text.replace(new_text, old_text)).body[0]
        strip = lambda f: [ast.dump(s, include_attributes=False) for s in f.body[1:]]   # without the docstring
        self.assertEqual(strip(back), strip(old))

    def test_collect_and_collect_inputs_only_moved_the_measure(self):
        for name in ("collect", "collect_inputs"):
            with self.subTest(name=name):
                self.same_after(name, "(hash_file(path), path.stat().st_size)", "_measure(path)")

    def test_measure_on_posix_is_the_alpha22_expression(self):
        source = (ROOT / "gpos/tools/artifacts.py").read_bytes().decode("utf-8")
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "_measure")
        posix = [s for s in node.body[1:] if not pp.is_windows_guard(s)]
        self.assertEqual([ast.unparse(s) for s in posix], ["return (hash_file(path), path.stat().st_size)"])

    def test_relative_only_spells_the_fallback_portably(self):
        self.same_after("_relative", "str(path)", "path.as_posix()")


class P06_DiagnosticCodes(unittest.TestCase):
    def test_every_alpha22_code_is_unchanged_and_only_four_were_added(self):
        source = (ROOT / "gpos/tools/diagnostics.py").read_bytes().decode("utf-8")
        tree = ast.parse(source)
        table = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                     and [ast.unparse(t) for t in n.targets] == ["CODES"])
        now = {k.value: ast.unparse(v) for k, v in zip(table.keys, table.values)}
        for code, value in FIXTURE["codes"].items():
            with self.subTest(code=code):
                self.assertEqual(now.get(code), value)
        self.assertEqual(sorted(set(now) - set(FIXTURE["codes"])),
                         ["DCC_CLEANUP_INCOMPLETE", "PROCESS_CAPTURE_INCOMPLETE", "PROCESS_DESCENDANTS_TERMINATED",
                          "PROCESS_TREE_NOT_CONTAINED"])   # alpha.24 added DCC_CLEANUP_INCOMPLETE (Windows only)


class P08_Alpha24SharedChanges(unittest.TestCase):
    """Each alpha.24 shared change is exactly the one named in DELIBERATE: undoing it gives the alpha.22 fingerprint."""

    def node(self, rel, unit):
        tree = ast.parse((ROOT / rel).read_bytes().decode("utf-8"))
        if unit.startswith("assign "):
            return next(n for n in tree.body if isinstance(n, ast.Assign)
                        and [ast.unparse(t) for t in n.targets] == [unit[len("assign "):]])
        owner, _, name = unit[len("def "):].rpartition(".")
        scope = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner).body if owner else tree.body
        return next(n for n in scope if isinstance(n, ast.FunctionDef) and n.name == name)

    def assertAlpha22(self, rel, unit, node):
        old = FIXTURE["units"][rel][unit]
        self.assertEqual(pp._function(node) if isinstance(node, ast.FunctionDef) else {"stmt": pp._h(node)}, old)

    def test_the_descriptors_only_withdraw_windows(self):
        for rel in ("gpos/tools/git/adapter.py", "gpos/tools/adb/adapter.py"):
            with self.subTest(file=rel):
                node = self.node(rel, "assign DESCRIPTOR")
                keyword = next(k for k in node.value.keywords if k.arg == "supported_platforms")
                self.assertEqual(ast.literal_eval(keyword.value), ("MACOS", "LINUX"))
                keyword.value = ast.parse('("WINDOWS", "MACOS", "LINUX")', mode="eval").body
                self.assertAlpha22(rel, "assign DESCRIPTOR", node)

    def test_adb_readiness_only_adds_the_server_failure_check(self):
        node = self.node("gpos/tools/adb/adapter.py", "def _Runner.ready")
        failed = next(s for s in node.body if isinstance(s, ast.If) and ast.unparse(s.test) == "outcome.exit_code != 0")
        added = failed.body.pop(0)
        self.assertEqual(ast.unparse(added.test), "server_failure(outcome.raw_stderr)")
        self.assertEqual(len(added.body), 1)
        self.assertTrue(ast.unparse(added.body[0]).startswith("return self.failed(outcome,"))
        self.assertAlpha22("gpos/tools/adb/adapter.py", "def _Runner.ready", node)

    def test_blender_execute_only_adds_the_template_refusal(self):
        node = self.node("gpos/tools/blender/adapter.py", "def BlenderAdapter.execute")
        added = [(parent, s) for parent in ast.walk(node) if isinstance(getattr(parent, "body", None), list)
                 for s in parent.body if isinstance(s, ast.If) and "TEMPLATE_CHARACTERS" in ast.unparse(s.test)]
        self.assertEqual(len(added), 1)
        parent, statement = added[0]
        self.assertEqual(ast.unparse(statement.test), "any((c in str(output) for c in TEMPLATE_CHARACTERS))")
        self.assertTrue(ast.unparse(statement.body[0]).startswith("return _refuse(cap,"))
        parent.body.remove(statement)
        self.assertAlpha22("gpos/tools/blender/adapter.py", "def BlenderAdapter.execute", node)


class P09_Alpha25SharedChanges(unittest.TestCase):
    """Each alpha.25 shared change is exactly the one named in DELIBERATE: undoing it gives the alpha.22 fingerprint."""

    def node(self, rel, unit):
        return P08_Alpha24SharedChanges.node(self, rel, unit)

    def assertAlpha22(self, rel, unit, node):
        P08_Alpha24SharedChanges.assertAlpha22(self, rel, unit, node)

    def test_the_unity_descriptor_only_adds_windows(self):
        rel = "gpos/tools/unity/adapter.py"
        node = self.node(rel, "assign DESCRIPTOR")
        kw = {k.arg: k for k in node.value.keywords}
        self.assertEqual(ast.literal_eval(kw["supported_platforms"].value), ("MACOS", "WINDOWS"))
        kw["supported_platforms"].value = ast.parse('("MACOS",)', mode="eval").body
        kw["availability"].value = ast.parse(
            '"exactly one Unity Editor installed under the Unity Hub Editor root '
            '(/Applications/Unity/Hub/Editor/<version>/Unity.app); PATH is never used"', mode="eval").body
        notes = kw["compatibility_notes"].value
        self.assertIn("Alpha.25 adds Windows for the batch plane only", ast.literal_eval(notes.elts[0]))
        notes.elts[0] = ast.parse('"Alpha.15 supports exactly one usable Hub-installed Editor and macOS only."',
                                  mode="eval").body
        self.assertAlpha22(rel, "assign DESCRIPTOR", node)

    def test_the_classifier_only_adds_the_package_manager_message(self):
        rel = "gpos/tools/unity/adapter.py"
        node = self.node(rel, "def UnityAdapter._classify")
        hits = [n for n in ast.walk(node) if isinstance(n, ast.IfExp)
                and "PACKAGE_MANAGER_UNAVAILABLE" in ast.unparse(n.test)]
        self.assertEqual(len(hits), 1)
        added = hits[0]
        for parent in ast.walk(node):
            if isinstance(parent, ast.IfExp) and parent.orelse is added:
                parent.orelse = added.orelse            # drop exactly the new branch
        self.assertAlpha22(rel, "def UnityAdapter._classify", node)

    def test_the_signatures_only_append_the_package_manager_failure(self):
        rel = "gpos/tools/unity/results.py"
        node = self.node(rel, "assign SIGNATURES")
        last = node.value.elts[-1]
        self.assertEqual(ast.unparse(last.elts[0]), "PACKAGE_MANAGER_UNAVAILABLE")
        node.value.elts.pop()
        self.assertAlpha22(rel, "assign SIGNATURES", node)


class P10_Alpha26SharedChanges(unittest.TestCase):
    """Each alpha.26 shared change is exactly the one named in DELIBERATE, and the macOS view of every bridge C# file
    is its alpha.22 (bridge 1.5.0) source: dropping the UNITY_EDITOR_WIN branches gives the frozen bytes."""

    BRIDGE = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/"

    def node(self, rel, unit):
        return P08_Alpha24SharedChanges.node(self, rel, unit)

    def assertAlpha22(self, rel, unit, node):
        P08_Alpha24SharedChanges.assertAlpha22(self, rel, unit, node)

    @staticmethod
    def without_nested_guards(node):
        for parent in ast.walk(node):
            for field in ("body", "orelse"):
                block = getattr(parent, field, None)
                if isinstance(block, list) and parent is not node:
                    setattr(parent, field, [s for s in block if not pp.is_windows_guard(s)])
        return node

    def test_nested_windows_guards_are_the_whole_change(self):
        for rel, unit in (("gpos/tools/unity/bridge_install.py", "def runtime_path"),
                          ("gpos/tools/unity/bridge_install.py", "def tree"),
                          ("gpos/tools/unity/identity.py", "def gpos_root_of"),
                          ("gpos/tools/unity/live_ipc.py", "def Channel.__init__")):
            with self.subTest(file=rel, unit=unit):
                node = self.node(rel, unit)
                nested = [s for p in ast.walk(node) if p is not node for f in ("body", "orelse")
                          for s in (getattr(p, f, None) or []) if isinstance(s, ast.stmt) and pp.is_windows_guard(s)]
                self.assertGreaterEqual(len(nested), 1)
                self.assertAlpha22(rel, unit, self.without_nested_guards(node))

    def test_the_bridge_version_and_its_history_pin(self):
        rel = "gpos/tools/unity/bridge_install.py"
        node = self.node(rel, "assign BRIDGE_VERSION")
        self.assertEqual(ast.literal_eval(node.value), "1.6.0")
        node.value = ast.parse('"1.5.0"', mode="eval").body
        self.assertAlpha22(rel, "assign BRIDGE_VERSION", node)
        node = self.node(rel, "assign PREVIOUS")
        self.assertEqual(ast.literal_eval(node.value.keys[-1]), "1.5.0")
        self.assertEqual(ast.literal_eval(node.value.values[-1]), ("gpos.unity.live/5", BRIDGE_DIGEST))
        node.value.keys.pop()
        node.value.values.pop()
        self.assertAlpha22(rel, "assign PREVIOUS", node)

    @staticmethod
    def macos_view(text):
        return pp.macos_view(text)

    def test_the_macos_view_of_every_bridge_source_is_the_alpha22_source(self):
        changed = [rel for rel in EDITED if rel.startswith(self.BRIDGE) and rel.endswith(".cs")]
        self.assertEqual(sorted(changed), sorted(self.BRIDGE + "Editor/" + n for n in (
            "AssetAuthoring.cs", "Commands.cs", "Core/Protocol.cs", "Identity.cs", "Ipc.cs", "PrefabAuthoring.cs",
            "PrefabResolver.cs")))
        for rel in changed:
            with self.subTest(file=rel):
                view = self.macos_view((ROOT / rel).read_bytes().decode("utf-8"))
                if rel.endswith("Core/Protocol.cs"):
                    now = '        public const string BridgeVersion = "1.6.0";   // 1.6.0 adds Windows; the live protocol is unchanged\n'
                    then = ('        public const string BridgeVersion = "1.5.0";   // 1.5.0 adds the batch-only BuildEntry; '
                            'the live protocol is unchanged\n')
                    self.assertEqual(view.count(now), 1)
                    view = view.replace(now, then)
                self.assertEqual(hashlib.sha256(view.encode("utf-8")).hexdigest(), FIXTURE["frozen"][rel])
        package = (ROOT / self.BRIDGE / "package.json").read_bytes()
        self.assertEqual(hashlib.sha256(package.replace(b'"version": "1.6.0"', b'"version": "1.5.0"')).hexdigest(),
                         FIXTURE["frozen"][self.BRIDGE + "package.json"])

    def test_the_new_windows_core_files_hold_no_macos_code(self):
        for name in ("WindowsFiles.cs", "WindowsPaths.cs"):
            with self.subTest(file=name):
                view = self.macos_view((ROOT / self.BRIDGE / "Editor/Core" / name).read_bytes().decode("utf-8"))
                code = [l for l in view.splitlines() if l.strip() and not l.strip().startswith("//")]
                self.assertEqual(code, [])


class P07_FrozenBoundaries(unittest.TestCase):
    def test_the_unity_bridge_is_the_audited_1_6_0_bridge_and_1_5_0_is_pinned(self):
        """alpha.26: the shipped bridge is 1.6.0 (protocol /5 unchanged); the alpha.22 manifest (bridge 1.5.0) is kept
        byte for byte as live_bridge/history/1.5.0.json, pinned at its frozen digest."""
        history = (ROOT / "gpos/tools/unity/live_bridge/history/1.5.0.json").read_bytes()
        self.assertEqual(hashlib.sha256(history).hexdigest(),
                         FIXTURE["frozen"]["gpos/tools/unity/live_bridge/manifest.json"])
        self.assertEqual(json.loads(history.decode("utf-8"))["package_digest"], BRIDGE_DIGEST)
        manifest = json.loads((ROOT / "gpos/tools/unity/live_bridge/manifest.json").read_bytes().decode("utf-8"))
        self.assertEqual((manifest["bridge_version"], manifest["protocol"], len(manifest["files"])),
                         ("1.6.0", "gpos.unity.live/5", 71))
        lines = "".join(f"{e['path']}\t{e['sha256']}\t{e['size']}\n"
                        for e in sorted(manifest["files"], key=lambda e: e["path"]))
        self.assertEqual(hashlib.sha256(lines.encode("utf-8")).hexdigest(), manifest["package_digest"])
        package = ROOT / "gpos/tools/unity/live_bridge" / manifest["package_id"]
        for entry in manifest["files"]:
            data = (package / entry["path"]).read_bytes()
            with self.subTest(file=entry["path"]):
                self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (entry["size"], entry["sha256"]))
        on_disk = sorted(p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file())
        self.assertEqual(on_disk, sorted(e["path"] for e in manifest["files"]))

    def test_registry_gates_evidence_and_authority_are_unchanged_but_for_the_version(self):
        for rel in ("core/registry.json", "core/GOVERNANCE.md", "core/EVIDENCE-RULES.md", "core/QUALITY-GATES.md",
                    "core/HUMAN-AUTHORITY.md", "core/AUTHORITY-HIERARCHY.md"):
            with self.subTest(file=rel):
                restored = (ROOT / rel).read_bytes().replace(VERSION_NOW, VERSION_FROZEN)
                self.assertEqual(hashlib.sha256(restored).hexdigest(), FIXTURE["frozen"][rel])
        registry = json.loads((ROOT / "core/registry.json").read_bytes().decode("utf-8"))
        self.assertEqual(registry["gpos_version"], VERSION_NOW.decode())


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    print("GPOS alpha.22 POSIX source parity (source evidence only; never a macOS execution PASS)")
    sys.exit(0 if result.wasSuccessful() else 1)
