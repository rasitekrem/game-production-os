#!/usr/bin/env python3
"""Phase 2C-1 — production Git provenance adapter tests.

    python3 tests/test_git_adapter.py

These are real integration tests: every repository is created with the locally installed Git in a
temporary directory, and the adapter under test drives that same Git through the audited process
boundary. Nothing is mocked except where a test needs a tool that does not exist (a missing Git, an
unrecognizable version) and says so. The suite fails if Git is not installed; it never falls back to
mocks.

Isolation: HOME is redirected to a temporary directory for the whole run, so neither the fixtures nor
the adapter read the user's global Git configuration, and fixture commands also skip the system
configuration. Fixture preparation may use Git freely (commit, merge, checkout); the adapter itself
never mutates a repository, which group N verifies.

Groups: A registration · B real probe · C missing or unusable tool · D clean repository · E dirty
state · F conflicted state · G detached HEAD · H unborn branch · I non-repository project · J root
mismatch · K path safety and parsing · L truncated or altered output · M environment and optional
locks · N read-only behaviour · O resolve-provenance · P explicit handoff · Q network and argument
surface · R CLI · S determinism and linked worktrees · T submodules · U fsmonitor neutralized ·
V raw machine output and the version floor.
"""

import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_HOME = tempfile.mkdtemp(prefix="gpos-git-home-")
os.environ["HOME"] = _HOME  # the adapter's Git inherits this: no user-level configuration is read

from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import model as tmodel  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools import validation as tval  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.git import adapter as ga  # noqa: E402
from gpos.tools.git import status as gs  # noqa: E402
from gpos.tools.git import GitAdapter  # noqa: E402
from gpos.tools.model import Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.synthetic import SyntheticAdapter  # noqa: E402
from gpos.tools.synthetic import adapter as syn  # noqa: E402

FW = load_framework()
REG = FW.registry
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
PROJECT_ID = "synthetic-adapter-project"
GIT = shutil.which("git")
SHA = re.compile(r"^[0-9a-f]{40}$")
CONTRACT_KEYS = {"repository_root", "head_sha", "branch", "detached", "unborn", "clean", "exact_revision",
                 "staged_count", "unstaged_count", "untracked_count", "conflicted_count"}
PROVENANCE_KEYS = {"repository_revision", "head_sha", "branch", "detached"}
NETWORK_SUBCOMMANDS = {"fetch", "pull", "push", "clone", "ls-remote", "remote", "submodule", "archive",
                       "send-pack", "fetch-pack", "request-pull", "daemon", "http-fetch", "http-push",
                       "upload-pack", "receive-pack", "bundle", "credential", "credential-cache"}
MUTATING_SUBCOMMANDS = {"add", "commit", "reset", "restore", "checkout", "switch", "branch", "tag", "merge",
                        "rebase", "cherry-pick", "revert", "stash", "clean", "worktree", "config", "rm", "mv",
                        "init", "update-index", "update-ref", "symbolic-ref", "gc", "prune", "notes", "am",
                        "apply", "replace", "pack-refs", "reflog", "lfs", "sparse-checkout", "maintenance"}

FIXTURE_ENV = {"HOME": _HOME, "PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
               "LC_ALL": "C"}


def git(repo, *args, check=True):
    """Prepare a fixture with the real Git. Test code only; the adapter never runs through here."""
    return subprocess.run([GIT, *args], cwd=str(repo), env=FIXTURE_ENV, capture_output=True, text=True, check=check)


def tree_digest(root):
    """{relative path: sha256} for every file under root, including everything inside .git."""
    out = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class Recorder:
    """Records every process spec started through the audited boundary while active."""

    def __init__(self):
        self.specs = []
        self._original = tproc.run_process

    def __enter__(self):
        def recording(spec, scopes, clock=None):
            self.specs.append(spec)
            return self._original(spec, scopes) if clock is None else self._original(spec, scopes, clock)
        tproc.run_process = recording
        return self

    def __exit__(self, *exc):
        tproc.run_process = self._original

    @property
    def argvs(self):
        return [tuple(spec.argv) for spec in self.specs]


class GitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-git-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # ------------------------------------------------------------ fixtures

    def project(self, name="p", parent=None):
        target = (parent or self.tmp) / name
        shutil.copytree(FIXTURE, target)
        return target

    def repo(self, name="p", commit=True):
        """A GPOS project that is itself the top level of a real Git repository."""
        p = self.project(name)
        git(p, "init", "-q", "-b", "main")
        if commit:
            git(p, "add", "-A")
            git(p, "commit", "-q", "-m", "initial")
        return p

    def head(self, repo):
        return git(repo, "rev-parse", "HEAD").stdout.strip()

    # ------------------------------------------------------------ execution

    def run_cap(self, capability, project, registry=None, **kwargs):
        request = ExecutionRequest(adapter_id="git", capability_id=capability,
                                   subject=Subject("PROJECT", PROJECT_ID), project_root=str(project), **kwargs)
        return execute(registry or default_registry(FW), request)

    def inspect(self, project, **kwargs):
        return self.run_cap(ga.INSPECT, project, **kwargs)

    def resolve(self, project, **kwargs):
        return self.run_cap(ga.RESOLVE_PROVENANCE, project, **kwargs)

    def codes(self, result):
        return {d.code for d in result.diagnostics}

    def assertState(self, result, **expected):
        self.assertEqual(result.status, tdg.SUCCESS, [d.message for d in result.diagnostics])
        self.assertEqual(set(result.data), CONTRACT_KEYS)
        for key, value in expected.items():
            self.assertEqual(result.data[key], value, key)
        return result.data


# ---------------------------------------------------------------- A  registration

class A_Registration(GitCase):
    def test_production_registry_contains_git(self):
        # Phase 2C-2 added the two media adapters and Phase 2C-3 the ADB adapter beside Git; the newest suite
        # owns the exact list.
        self.assertEqual(default_registry(FW).adapter_ids(), ["adb", "blender", "ffmpeg", "ffprobe", "git", "unity"])

    def test_synthetic_stays_out_of_production(self):
        registry = default_registry(FW)
        self.assertNotIn("synthetic", registry.adapter_ids())
        self.assertFalse(registry.allow_test_only)

    def test_descriptor_passes_registration_validation(self):
        self.assertEqual(tval.validate_descriptor(FW, ga.DESCRIPTOR, allow_test_only=False), [])
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(GitAdapter())
        self.assertEqual(registry.adapter_ids(), ["git"])

    def test_descriptor_identity(self):
        d = ga.DESCRIPTOR
        self.assertEqual((d.adapter_id, d.tool_family, d.target_tool, d.adapter_kind, d.state_model, d.network),
                         ("git", "VERSION_CONTROL", "Git", "CLI", "STATELESS", "FORBIDDEN"))
        self.assertEqual(d.supported_platforms, ("WINDOWS", "MACOS", "LINUX"))
        self.assertFalse(d.test_only)
        self.assertEqual(d.filesystem_scopes, ())

    def test_exactly_two_read_only_capabilities(self):
        caps = {c.id: c for c in ga.DESCRIPTOR.capabilities}
        self.assertEqual(sorted(caps), ["git.inspect", "git.resolve-provenance"])
        self.assertEqual(caps["git.inspect"].category, "INSPECT")
        self.assertEqual(caps["git.resolve-provenance"].category, "VERSION_CONTROL")
        for cap in caps.values():
            self.assertEqual((cap.operation_class, cap.state_model, cap.execution_context),
                             ("READ_ONLY", "STATELESS", "OFFLINE_ANALYSIS"))
            self.assertTrue(cap.requires_tool and cap.requires_project)
            self.assertFalse(cap.requires_ready_routing or cap.single_writer_required or cap.dry_run_supported)
            self.assertEqual((cap.artifact_kinds, cap.potential_evidence, cap.input_kinds), ((), (), ()))
            self.assertEqual(cap.side_effect_scope, "NONE")

    def test_agent_and_tool_registries_stay_separate(self):
        from gpos.adapters.backends import BACKENDS
        self.assertNotIn("git", REG["adapter_ids"])
        self.assertNotIn("git", BACKENDS)


# ---------------------------------------------------------------- B  real probe

class B_RealProbe(GitCase):
    def test_real_git_probes_available(self):
        registry = default_registry(FW)
        probe = registry.probe("git")
        self.assertEqual(probe.status, tmodel.AVAILABLE, probe.detail)
        self.assertEqual(registry.state("git").state, tmodel.READY)
        self.assertTrue(os.path.isabs(probe.tool_path))
        self.assertTrue(Path(probe.tool_path).is_file())
        self.assertEqual(probe.platform, tmodel.current_platform())
        self.assertTrue(all(available for _, available, _ in probe.capability_availability))

    def test_reported_version_is_the_installed_version(self):
        installed = subprocess.run([GIT, "--version"], capture_output=True, text=True).stdout
        expected = ".".join(re.match(r"git version (\d+)\.(\d+)\.(\d+)", installed).groups())
        self.assertEqual(default_registry(FW).probe("git").tool_version, expected)

    def test_the_probe_runs_through_the_process_boundary(self):
        with Recorder() as rec:
            default_registry(FW).probe("git")
        self.assertEqual(rec.argvs, [ga.VERSION_ARGV])

    def test_the_adapter_never_imports_subprocess(self):
        for path in (ROOT / "gpos" / "tools" / "git").rglob("*.py"):
            tree = ast.parse(path.read_text())
            names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            names |= {n.module.split(".")[0] for n in ast.walk(tree)
                      if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
            self.assertEqual(names & {"subprocess", "multiprocessing", "pty", "os.system"}, set(), path)

    def test_process_py_is_still_the_only_subprocess_importer(self):
        importers = []
        for path in (ROOT / "gpos").rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names):
                    importers.append(path.relative_to(ROOT).as_posix())
                if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                    importers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(set(importers)), ["gpos/tools/process.py"])


# ---------------------------------------------------------------- C  missing or unusable tool

class C_MissingTool(GitCase):
    def registry_with(self, adapter):
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(adapter)
        return registry

    def fake_git(self, output):
        """A stand-in program named git that prints `output` (TEST_ONLY; used only to probe)."""
        exe = self.tmp / "bin" / "git"
        exe.parent.mkdir()
        exe.write_text(f"#!{tproc.interpreter_path()}\nimport sys\nsys.stdout.write({output!r})\n")
        exe.chmod(0o755)
        return exe

    def test_missing_git_is_unavailable_not_an_exception(self):
        probe = GitAdapter(which=lambda name: None).probe()
        self.assertEqual(probe.status, tmodel.UNAVAILABLE)
        self.assertIsNone(probe.tool_path)
        self.assertNotIn("Traceback", json.dumps(probe.to_dict()))

    def test_missing_git_makes_execution_unavailable(self):
        registry = self.registry_with(GitAdapter(which=lambda name: None))
        _, problems = registry.ready("git")
        self.assertEqual([d.code for d in problems], ["TOOL_NOT_FOUND"])
        result = self.inspect(self.repo(), registry=registry)
        self.assertEqual(result.status, tdg.UNAVAILABLE)
        self.assertFalse(result.data)  # no repository state at all

    def test_git_found_only_through_a_relative_path_entry_is_not_used(self):
        probe = GitAdapter(which=lambda name: "bin/git").probe()
        self.assertEqual(probe.status, tmodel.UNAVAILABLE)
        self.assertIn("relative PATH entry", probe.detail)

    def test_unrecognized_version_output_is_never_fabricated(self):
        exe = self.fake_git("this is not a version\n")
        probe = GitAdapter(which=lambda name: str(exe)).probe()
        self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED)
        self.assertIsNone(probe.tool_version)

    def test_a_version_older_than_the_documented_minimum_is_unsupported(self):
        exe = self.fake_git("git version 2.17.9\n")
        probe = GitAdapter(which=lambda name: str(exe)).probe()
        self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED)
        self.assertEqual(probe.tool_version, "2.17.9")
        _, problems = self.registry_with(GitAdapter(which=lambda name: str(exe))).ready("git")
        self.assertEqual([d.code for d in problems], ["TOOL_VERSION_UNSUPPORTED"])

    def test_version_parsing_is_conservative(self):
        self.assertEqual(ga.parse_version("git version 2.52.0\n"), (2, 52, 0))
        self.assertEqual(ga.parse_version("git version 2.39.3 (Apple Git-145)"), (2, 39, 3))
        self.assertEqual(ga.parse_version("git version 2.45.1.windows.1"), (2, 45, 1))
        for bad in ("", "git version", "git version 2.52", "git version two", "hello\ngit version 2.52.0",
                    "git version 2.52.0\nextra line", "Git version 2.52.0"):
            self.assertIsNone(ga.parse_version(bad), bad)


# ---------------------------------------------------------------- D  clean repository

class D_CleanRepository(GitCase):
    def test_clean_committed_repository(self):
        p = self.repo()
        head = self.head(p)
        data = self.assertState(self.inspect(p), clean=True, head_sha=head, exact_revision=head, unborn=False,
                                branch="main", detached=False, staged_count=0, unstaged_count=0,
                                untracked_count=0, conflicted_count=0)
        self.assertTrue(SHA.match(data["head_sha"]))
        self.assertEqual(data["repository_root"], str(p.resolve()))

    def test_ignored_files_do_not_make_a_repository_dirty(self):
        p = self.repo()
        (p / ".git" / "info" / "exclude").write_text("*.log\n")
        (p / "build.log").write_text("noise")
        self.assertState(self.inspect(p), clean=True, exact_revision=self.head(p), untracked_count=0)


# ---------------------------------------------------------------- E  dirty state

class E_DirtyState(GitCase):
    def assertDirty(self, p, **counts):
        head = self.head(p)
        base = dict(staged_count=0, unstaged_count=0, untracked_count=0, conflicted_count=0)
        base.update(counts)
        self.assertState(self.inspect(p), clean=False, exact_revision=None, head_sha=head, **base)

    def test_staged(self):
        p = self.repo()
        (p / ".game" / "PROJECT.md").write_text("changed\n")
        git(p, "add", ".game/PROJECT.md")
        self.assertDirty(p, staged_count=1)

    def test_unstaged(self):
        p = self.repo()
        (p / ".game" / "PROJECT.md").write_text("changed\n")
        self.assertDirty(p, unstaged_count=1)

    def test_untracked(self):
        p = self.repo()
        (p / "notes.txt").write_text("new\n")
        (p / "more").mkdir()
        (p / "more" / "deep.txt").write_text("new\n")
        (p / "more" / "deeper.txt").write_text("new\n")
        self.assertDirty(p, untracked_count=3)  # individual files: an untracked directory is not collapsed

    def test_a_file_both_staged_and_unstaged_counts_in_both(self):
        p = self.repo()
        (p / ".game" / "PROJECT.md").write_text("staged\n")
        git(p, "add", ".game/PROJECT.md")
        (p / ".game" / "PROJECT.md").write_text("then changed again\n")
        self.assertDirty(p, staged_count=1, unstaged_count=1)

    def test_a_deletion_is_dirty(self):
        p = self.repo()
        (p / ".game" / "ANIMATION.md").unlink()
        self.assertDirty(p, unstaged_count=1)


# ---------------------------------------------------------------- F  conflicted state

class F_Conflict(GitCase):
    def test_a_real_merge_conflict(self):
        p = self.repo()
        target = p / ".game" / "PROJECT.md"
        git(p, "switch", "-q", "-c", "other")
        target.write_text("other side\n")
        git(p, "commit", "-q", "-am", "other")
        git(p, "switch", "-q", "main")
        target.write_text("main side\n")
        git(p, "commit", "-q", "-am", "main")
        merge = git(p, "merge", "other", check=False)
        self.assertNotEqual(merge.returncode, 0)  # the fixture produced a genuine conflict
        data = self.assertState(self.inspect(p), clean=False, exact_revision=None, head_sha=self.head(p))
        self.assertGreater(data["conflicted_count"], 0)
        resolved = self.resolve(p)
        self.assertEqual(resolved.status, tdg.CONFLICT)
        self.assertIsNone(resolved.data["repository_revision"])


# ---------------------------------------------------------------- G  detached HEAD

class G_Detached(GitCase):
    def test_detached_clean(self):
        p = self.repo()
        head = self.head(p)
        git(p, "checkout", "-q", "--detach", "HEAD")
        self.assertState(self.inspect(p), branch=None, detached=True, head_sha=head, exact_revision=head,
                         clean=True, unborn=False)

    def test_detached_dirty(self):
        p = self.repo()
        git(p, "checkout", "-q", "--detach", "HEAD")
        (p / "notes.txt").write_text("x")
        self.assertState(self.inspect(p), detached=True, clean=False, exact_revision=None)


# ---------------------------------------------------------------- H  unborn branch

class H_Unborn(GitCase):
    def test_unborn_repository(self):
        p = self.repo(commit=False)
        data = self.assertState(self.inspect(p), unborn=True, head_sha=None, exact_revision=None, branch="main",
                                detached=False, clean=False)
        self.assertGreater(data["untracked_count"], 0)

    def test_an_unborn_but_clean_repository_still_has_no_revision(self):
        p = self.repo(commit=False)
        (p / ".git" / "info" / "exclude").write_text("*\n")
        self.assertState(self.inspect(p), unborn=True, clean=True, head_sha=None, exact_revision=None)
        resolved = self.resolve(p)
        self.assertEqual(resolved.status, tdg.CONFLICT)
        self.assertIsNone(resolved.data["repository_revision"])

    def test_resolve_never_claims_success_on_an_unborn_branch(self):
        resolved = self.resolve(self.repo(commit=False))
        self.assertEqual(resolved.status, tdg.CONFLICT)
        self.assertIn("REPOSITORY_STATE_CONFLICT", self.codes(resolved))
        self.assertIsNone(resolved.data["repository_revision"])
        self.assertTrue(any("no commit yet" in d.message for d in resolved.diagnostics))


# ---------------------------------------------------------------- I  non-repository project

class I_NotARepository(GitCase):
    def test_a_project_outside_any_repository_is_a_structured_result(self):
        p = self.project()
        probe = subprocess.run([GIT, "rev-parse", "--show-toplevel"], cwd=p, env=FIXTURE_ENV, capture_output=True)
        self.assertNotEqual(probe.returncode, 0)  # precondition: really outside any repository
        for capability in (ga.INSPECT, ga.RESOLVE_PROVENANCE):
            result = self.run_cap(capability, p)
            self.assertEqual(result.status, tdg.INVALID_REQUEST)
            self.assertIn("REPOSITORY_NOT_FOUND", self.codes(result))
            self.assertFalse(result.data)  # no repository state at all
            self.assertNotIn("Traceback", json.dumps(result.to_dict()))


# ---------------------------------------------------------------- J  repository-root mismatch

class J_RootMismatch(GitCase):
    def nested(self):
        outer = self.tmp / "repo"
        outer.mkdir()
        git(outer, "init", "-q", "-b", "main")
        (outer / "outer.txt").write_text("belongs to the enclosing repository\n")
        (outer / "uncommitted-secret-plan.txt").write_text("dirty state of the enclosing repository\n")
        git(outer, "add", "outer.txt")
        git(outer, "commit", "-q", "-m", "outer")
        return outer, self.project("nested-gpos-project", parent=outer)

    def test_a_nested_project_fails_closed(self):
        outer, nested = self.nested()
        for capability in (ga.INSPECT, ga.RESOLVE_PROVENANCE):
            result = self.run_cap(capability, nested)
            self.assertEqual(result.status, tdg.INVALID_REQUEST)
            self.assertIn("REPOSITORY_ROOT_MISMATCH", self.codes(result))
            self.assertFalse(result.data)  # no repository state at all
            message = next(d.message for d in result.diagnostics if d.code == "REPOSITORY_ROOT_MISMATCH")
            self.assertIn("MONOREPO_NESTED_PROJECT_NOT_YET_SUPPORTED", message)

    def test_the_enclosing_repository_is_never_inspected(self):
        outer, nested = self.nested()
        with Recorder() as rec:
            result = self.inspect(nested)
        self.assertNotIn(ga.STATUS_ARGV, rec.argvs)  # its work tree is never read
        payload = json.dumps(result.to_dict())
        for leak in ("uncommitted-secret-plan", "outer.txt", self.head(outer)):
            self.assertNotIn(leak, payload)

    def test_no_scope_over_the_enclosing_repository_is_granted(self):
        outer, nested = self.nested()
        self.inspect(nested)
        self.assertFalse((outer / ".game").exists())
        self.assertEqual(sorted(p.name for p in outer.iterdir()),
                         [".git", "nested-gpos-project", "outer.txt", "uncommitted-secret-plan.txt"])


# ---------------------------------------------------------------- K  path safety and parsing

class K_Paths(GitCase):
    def test_spaces_unicode_newlines_and_a_rename(self):
        p = self.repo(commit=False)
        (p / "with space.txt").write_text("a\n")
        (p / "ünïcödé.txt").write_text("u\n")
        (p / "move me.txt").write_text("m\n" * 20)
        newline = sys.platform != "win32"
        if newline:
            (p / "new\nline.txt").write_text("n\n")
        git(p, "add", "-A")
        git(p, "commit", "-q", "-m", "names")
        (p / "with space.txt").write_text("changed\n")                  # unstaged
        (p / "ünïcödé.txt").write_text("changed\n")                     # unstaged
        git(p, "mv", "move me.txt", "renamed ü.txt")                   # staged rename (a type-2 record)
        (p / "untracked with space ü.txt").write_text("x")              # untracked
        expected_unstaged, expected_untracked = 2, 1
        if newline:
            (p / "new\nline.txt").write_text("changed\n")               # unstaged, newline in the name
            (p / "another\nnewline.txt").write_text("x")                # untracked, newline in the name
            expected_unstaged, expected_untracked = 3, 2
        raw = git(p, "status", "--porcelain=v2", "-z", "--find-renames").stdout
        self.assertIn("\n2 R", "\n" + raw.replace("\0", "\n"))  # precondition: Git reported a real rename
        self.assertState(self.inspect(p), staged_count=1, unstaged_count=expected_unstaged,
                         untracked_count=expected_untracked, conflicted_count=0, clean=False)

    def test_no_path_list_is_returned(self):
        p = self.repo()
        (p / "private file name.txt").write_text("x")
        result = self.inspect(p)
        payload = json.dumps(result.to_dict())
        self.assertNotIn("private file name", payload)
        self.assertEqual(result.stdout, "")

    def test_parser_splits_on_nul_and_consumes_the_rename_origin(self):
        oid = "a" * 40
        text = (f"# branch.oid {oid}\0# branch.head main\0"
                f"2 R. N... 100644 100644 100644 {oid} {oid} R100 new name.txt\0old\nname.txt\0"
                f"1 .M N... 100644 100644 100644 {oid} {oid} path with spaces and ü.txt\0"
                f"? untracked\nwith newline\0").encode()
        state = gs.parse(text)
        self.assertEqual((state["staged_count"], state["unstaged_count"], state["untracked_count"]), (1, 1, 1))

    def test_parser_accepts_sha256_object_ids_and_ignores_unknown_headers(self):
        oid = "b" * 64
        state = gs.parse(f"# branch.oid {oid}\0# branch.head main\0# branch.upstream origin/main\0# stash 3\0".encode())
        self.assertEqual(state["exact_revision"], oid)

    def test_parser_refuses_anything_it_does_not_fully_understand(self):
        oid = "c" * 40
        head = f"# branch.oid {oid}\0# branch.head main\0"
        bad = {
            "unterminated": head + "? file",
            "missing headers": "? file\0",
            "missing head": f"# branch.oid {oid}\0",
            "unknown record": head + "9 something\0",
            "empty record": head + "\0",
            "rename without origin": head + f"2 R. N... 1 1 1 {oid} {oid} R100 new\0",
            "short ordinary record": head + "1 .M N... path\0",
            "bad XY": head + f"1 ZZ N... 1 1 1 {oid} {oid} p\0",
            "bad oid": "# branch.oid not-a-sha\0# branch.head main\0",
            "unborn and detached": "# branch.oid (initial)\0# branch.head (detached)\0",
        }
        for name, text in bad.items():
            with self.assertRaises(gs.StatusParseError, msg=name):
                gs.parse(text.encode())
        with self.assertRaises(gs.StatusParseError, msg="decoded text instead of the captured bytes"):
            gs.parse("# branch.oid (initial)\0# branch.head main\0")


# ---------------------------------------------------------------- L  truncated or altered output

class L_IncompleteOutput(GitCase):
    def test_truncated_status_output_is_never_reported(self):
        p = self.repo()
        for i in range(8):
            (p / f"untracked-{i}.txt").write_text("x")
        registry = ToolRegistry(FW)
        registry.register(GitAdapter(status_capture_bytes=64))
        result = self.inspect(p, registry=registry)
        self.assertEqual(result.status, tdg.FAILED)
        self.assertFalse(result.data)  # no repository state at all
        self.assertIn("PROCESS_OUTPUT_TRUNCATED", self.codes(result))
        self.assertTrue(any("truncated" in d.message and d.code == "EXECUTION_FAILED" for d in result.diagnostics))

    def test_truncation_exactly_on_a_record_boundary_is_still_refused(self):
        """The dangerous case: the bound lands just after a complete record, so what was captured is
        well-formed porcelain — a valid *prefix*. Only the truncation check stands between it and a
        silently wrong count."""
        p = self.repo()
        for i in range(8):
            (p / f"untracked-{i}.txt").write_text("x")
        raw = git(p, *ga.STATUS_ARGV).stdout.encode()
        records = raw.split(b"\0")
        boundary = len(b"\0".join(records[:4])) + 1  # headers plus two complete records, NUL included
        self.assertEqual(gs.parse(raw[:boundary])["untracked_count"], 2)  # precondition: the prefix parses cleanly
        registry = ToolRegistry(FW)
        registry.register(GitAdapter(status_capture_bytes=boundary))
        result = self.inspect(p, registry=registry)
        self.assertEqual(result.status, tdg.FAILED)
        self.assertFalse(result.data)
        self.assertTrue(any("truncated" in d.message and d.code == "EXECUTION_FAILED" for d in result.diagnostics))

    def test_the_same_repository_reads_normally_within_the_bound(self):
        p = self.repo()
        for i in range(8):
            (p / f"untracked-{i}.txt").write_text("x")
        self.assertState(self.inspect(p), untracked_count=8)

    def test_credential_shaped_filenames_are_counted_correctly_and_never_exposed(self):
        """Redaction could merge the NUL-separated records of `api_key=abc` and `ordinary.txt` in the public
        text. The adapter parses the private raw capture instead, so the counts are exact — and nothing
        secret-shaped reaches any public surface."""
        p = self.repo()
        (p / "api_key=abc").write_text("x")
        (p / "ordinary.txt").write_text("y")
        result = self.inspect(p)
        self.assertState(result, untracked_count=2, clean=False, exact_revision=None)
        surfaces = {
            "ToolResult JSON": json.dumps(result.to_dict()),
            "provenance": json.dumps(result.provenance.to_dict()),
            "diagnostics": json.dumps([d.to_dict() for d in result.diagnostics]),
            "stdout/stderr": result.stdout + result.stderr,
        }
        from gpos.tools import cli as tool_cli
        for fmt in ("json", "text"):
            buffer = io.StringIO()
            tool_cli.main(["execute", "--adapter", "git", "--capability", "git.inspect", "--project", str(p),
                           "--subject-kind", "PROJECT", "--subject-ref", PROJECT_ID, "--format", fmt], stdout=buffer)
            surfaces[f"CLI {fmt}"] = buffer.getvalue()
        for name, text in surfaces.items():
            self.assertNotIn("api_key=abc", text, name)
            self.assertNotIn("=abc", text, name)
        resolved = self.resolve(p)
        self.assertEqual(resolved.status, tdg.CONFLICT)  # dirty, correctly: two untracked files
        self.assertIn("untracked 2", next(d.message for d in resolved.diagnostics
                                          if d.code == "REPOSITORY_STATE_CONFLICT"))

    def test_a_credential_shaped_value_in_the_project_path_still_matches_the_root(self):
        """The root comparison uses Git's exact bytes, so a redactable directory name no longer breaks it;
        the path shown in the public data is still redacted."""
        base = self.tmp / "password=s3cr3tDIR"
        base.mkdir()
        p = self.project(parent=base)
        git(p, "init", "-q", "-b", "main")
        git(p, "add", "-A")
        git(p, "commit", "-q", "-m", "initial")
        result = self.inspect(p)
        self.assertEqual(result.status, tdg.SUCCESS, [d.message for d in result.diagnostics])
        self.assertTrue(result.data["clean"])
        self.assertNotIn("s3cr3tDIR", json.dumps(result.to_dict()))


# ---------------------------------------------------------------- M  environment and optional locks

class M_Environment(GitCase):
    def test_the_non_interactive_environment_is_declared(self):
        env = dict(ga.ENVIRONMENT)
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_PAGER"], "cat")

    def test_every_git_process_uses_that_environment(self):
        p = self.repo()
        with Recorder() as rec:
            self.inspect(p)
            self.resolve(p)
        self.assertGreaterEqual(len(rec.specs), 5)  # probes, top level and status for both
        for spec in rec.specs:
            self.assertEqual(dict(spec.env.overrides), dict(ga.ENVIRONMENT))
            built = spec.env.build({"PATH": "/bin", "GIT_DIR": "/elsewhere", "GIT_ASKPASS": "/x",
                                    "GIT_EXTERNAL_DIFF": "/x", "GIT_CONFIG_PARAMETERS": "'core.x=y'"})
            for inherited in ("GIT_DIR", "GIT_ASKPASS", "GIT_EXTERNAL_DIFF", "GIT_CONFIG_PARAMETERS"):
                self.assertNotIn(inherited, built)  # a caller's environment cannot redirect or reconfigure Git

    def test_provenance_records_names_never_values(self):
        result = self.inspect(self.repo())
        env = result.provenance.to_dict()["environment"]
        self.assertTrue({"GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT", "GIT_PAGER"} <= set(env["set_names"]))
        self.assertNotIn("cat", json.dumps(env))

    def test_status_does_not_refresh_the_index(self):
        """Real behaviour: a stat-dirty tracked file makes plain `git status` rewrite the index; the adapter's
        GIT_OPTIONAL_LOCKS=0 must leave it byte-identical."""
        p = self.repo()
        index = p / ".git" / "index"
        target = p / ".game" / "PROJECT.md"
        stat = target.stat()
        os.utime(target, (stat.st_atime - 1000, stat.st_mtime - 1000))  # same content, different stat data
        before = index.read_bytes()
        self.assertState(self.inspect(p), clean=True)
        self.assertEqual(index.read_bytes(), before, "the adapter's status refreshed the index")
        git(p, "status", "--porcelain=v2", "-z")
        self.assertNotEqual(index.read_bytes(), before, "precondition: plain status would have rewritten it")

    def test_git_is_never_given_a_config_override(self):
        for argv in ga.AUTHORIZED_COMMANDS:
            self.assertNotIn("-c", argv)
            self.assertFalse(any(a.startswith("--config") or a.startswith("-c") and len(a) > 2 for a in argv))


# ---------------------------------------------------------------- N  read-only behaviour

class N_ReadOnly(GitCase):
    def assertUntouched(self, p):
        before_tree = tree_digest(p)
        before_refs = git(p, "for-each-ref").stdout
        before_objects = git(p, "count-objects", "-v").stdout
        for capability in (ga.INSPECT, ga.RESOLVE_PROVENANCE, ga.INSPECT):
            self.run_cap(capability, p)
        self.assertEqual(tree_digest(p), before_tree)  # work tree, index, HEAD, refs, objects, config
        self.assertEqual(git(p, "for-each-ref").stdout, before_refs)
        self.assertEqual(git(p, "count-objects", "-v").stdout, before_objects)
        self.assertFalse((p / ".game" / "gpos-runtime").exists())
        self.assertFalse((p / ".git" / "index.lock").exists())

    def test_clean_repository_is_untouched(self):
        self.assertUntouched(self.repo())

    def test_dirty_repository_is_untouched(self):
        p = self.repo()
        (p / ".game" / "PROJECT.md").write_text("staged\n")
        git(p, "add", ".game/PROJECT.md")
        (p / ".game" / "ANIMATION.md").write_text("unstaged\n")
        (p / "untracked.txt").write_text("x")
        self.assertUntouched(p)

    def test_unborn_repository_is_untouched(self):
        self.assertUntouched(self.repo(commit=False))

    def test_no_mutation_is_ever_reported(self):
        p = self.repo()
        for capability in (ga.INSPECT, ga.RESOLVE_PROVENANCE):
            result = self.run_cap(capability, p)
            self.assertFalse(result.mutation_performed)
            self.assertEqual(result.artifacts, ())


# ---------------------------------------------------------------- O  resolve-provenance

class O_ResolveProvenance(GitCase):
    def test_clean_repository_returns_the_exact_commit(self):
        p = self.repo()
        result = self.resolve(p)
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertEqual(set(result.data), PROVENANCE_KEYS)
        self.assertEqual(result.data["repository_revision"], self.head(p))
        self.assertEqual(result.data["head_sha"], self.head(p))
        self.assertTrue(SHA.match(result.data["repository_revision"]))
        self.assertEqual((result.data["branch"], result.data["detached"]), ("main", False))

    def test_dirty_repository_is_refused(self):
        p = self.repo()
        (p / "untracked.txt").write_text("x")
        result = self.resolve(p)
        self.assertEqual(result.status, tdg.CONFLICT)
        self.assertEqual(result.exit_code_for_cli, tdg.EXIT_FOR[tdg.CONFLICT])
        self.assertIn("REPOSITORY_STATE_CONFLICT", self.codes(result))
        self.assertIsNone(result.data["repository_revision"])
        self.assertEqual(result.data["head_sha"], self.head(p))  # the baseline is still visible
        message = next(d.message for d in result.diagnostics if d.code == "REPOSITORY_STATE_CONFLICT")
        self.assertIn("never represented as its HEAD commit", message)

    def test_detached_clean_repository_resolves(self):
        p = self.repo()
        git(p, "checkout", "-q", "--detach", "HEAD")
        result = self.resolve(p)
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertEqual((result.data["branch"], result.data["detached"]), (None, True))

    def test_resolution_offers_no_evidence_and_claims_no_verdict(self):
        result = self.resolve(self.repo())
        self.assertEqual(result.evidence_candidates, ())
        self.assertNotIn("PASS", json.dumps(result.data))


# ---------------------------------------------------------------- P  explicit provenance handoff

class P_ExplicitHandoff(GitCase):
    def synthetic_registry(self):
        registry = ToolRegistry(FW, allow_test_only=True)
        registry.register(SyntheticAdapter())
        return registry

    def later(self, p, **kwargs):
        return execute(self.synthetic_registry(), ExecutionRequest(
            adapter_id="synthetic", capability_id=syn.INSPECT, subject=Subject("PROJECT", PROJECT_ID),
            project_root=str(p), **kwargs))

    def test_the_resolved_revision_is_recorded_exactly_when_passed(self):
        p = self.repo()
        revision = self.resolve(p).data["repository_revision"]
        result = self.later(p, build_revision=revision)
        self.assertEqual(result.status, tdg.SUCCESS)
        self.assertEqual(result.provenance.build_revision, revision)
        self.assertEqual(result.provenance.to_dict()["build_revision"], revision)

    def test_without_the_handoff_the_revision_stays_unknown(self):
        p = self.repo()
        self.resolve(p)  # a revision exists and was just resolved, yet nothing carries it over
        result = self.later(p)
        self.assertIsNone(result.provenance.build_revision)
        self.assertIn("build_revision", result.provenance.to_dict()["unknown"])

    def test_the_git_adapter_does_not_fill_its_own_build_revision(self):
        result = self.resolve(self.repo())
        self.assertIsNone(result.provenance.build_revision)
        self.assertIn("build_revision", result.provenance.to_dict()["unknown"])

    def test_no_state_is_cached_between_requests(self):
        p = self.repo()
        registry = default_registry(FW)
        first = self.resolve(p, registry=registry).data["repository_revision"]
        (p / "next.txt").write_text("next\n")
        git(p, "add", "next.txt")
        git(p, "commit", "-q", "-m", "next")
        second = self.resolve(p, registry=registry).data["repository_revision"]
        self.assertNotEqual(first, second)
        self.assertEqual(second, self.head(p))


# ---------------------------------------------------------------- Q  network and argument surface

class Q_Surface(GitCase):
    def test_no_network_subcommand_is_authorized(self):
        for argv in ga.AUTHORIZED_COMMANDS:
            self.assertEqual(set(argv) & NETWORK_SUBCOMMANDS, set(), argv)

    def test_no_mutating_subcommand_is_authorized(self):
        for argv in ga.AUTHORIZED_COMMANDS:
            self.assertEqual(set(argv) & MUTATING_SUBCOMMANDS, set(), argv)

    def test_the_authorized_surface_is_exactly_three_fixed_vectors(self):
        self.assertEqual(ga.AUTHORIZED_COMMANDS, (
            ("--version",),
            ("rev-parse", "--show-toplevel"),
            ("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all", "--find-renames",
             "--no-ahead-behind", "--ignore-submodules=none")))

    def test_every_process_started_uses_an_authorized_vector_unmodified(self):
        p = self.repo()
        (p / "x.txt").write_text("x")
        with Recorder() as rec:
            for capability in (ga.INSPECT, ga.RESOLVE_PROVENANCE):
                self.run_cap(capability, p)
            self.inspect(self.project("not-a-repo"))
        self.assertTrue(rec.specs)
        for spec in rec.specs:
            self.assertIn(tuple(spec.argv), ga.AUTHORIZED_COMMANDS)

    def test_every_process_spec_in_the_source_names_a_fixed_vector(self):
        tree = ast.parse((ROOT / "gpos" / "tools" / "git" / "adapter.py").read_text())
        allowed = {"VERSION_ARGV", "TOPLEVEL_ARGV", "STATUS_ARGV", "argv"}
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", getattr(n.func, "id", None)) == "ToolProcessSpec"]
        self.assertTrue(calls)
        for call in calls:
            argv = next(k.value for k in call.keywords if k.arg == "argv")
            self.assertIsInstance(argv, ast.Name)
            self.assertIn(argv.id, allowed)
        runs = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "_run"]
        for call in runs:  # the only helper that takes an argv is only ever given a fixed vector
            self.assertIsInstance(call.args[1], ast.Name)
            self.assertIn(call.args[1].id, allowed - {"argv"})

    def test_no_caller_input_reaches_git(self):
        p = self.repo()
        for capability in (ga.INSPECT, ga.RESOLVE_PROVENANCE):
            result = self.run_cap(capability, p, inputs={"args": "fetch origin"})
            self.assertEqual(result.status, tdg.INVALID_REQUEST)
            self.assertIn("INVALID_TOOL_REQUEST", self.codes(result))

    def test_no_remote_url_or_name_is_read_or_reported(self):
        p = self.repo()
        git(p, "remote", "add", "origin", "https://user:token-in-url@example.invalid/repo.git")
        result = self.inspect(p)
        payload = json.dumps(result.to_dict())
        for leak in ("token-in-url", "example.invalid", "origin"):
            self.assertNotIn(leak, payload)
        self.assertEqual(ga.DESCRIPTOR.network, "FORBIDDEN")


# ---------------------------------------------------------------- R  CLI

class R_Cli(GitCase):
    def cli(self, *argv):
        from gpos.tools import cli as tool_cli
        buffer = io.StringIO()
        code = tool_cli.main(list(argv), stdout=buffer)
        return code, buffer.getvalue()

    def test_list(self):
        code, out = self.cli("list")
        self.assertEqual(code, 0)
        self.assertIn("6 tool adapter", out)
        self.assertIn("git 1.0.0 · VERSION_CONTROL · 2 capabilities", out)
        self.assertNotIn("TEST_ONLY", out)

    def test_describe_and_capabilities(self):
        code, out = self.cli("describe", "git")
        self.assertEqual(code, 0)
        for text in ("git.inspect", "git.resolve-provenance", "READ_ONLY", "network FORBIDDEN"):
            self.assertIn(text, out)
        code, out = self.cli("capabilities", "git", "--format", "json")
        self.assertEqual(code, 0)
        caps = json.loads(out)["capabilities"]["capabilities"]
        self.assertEqual(sorted(c["id"] for c in caps), ["git.inspect", "git.resolve-provenance"])

    def test_probe(self):
        code, out = self.cli("probe", "git", "--format", "json")
        self.assertEqual(code, 0)
        probe = json.loads(out)["probe"]
        self.assertEqual(probe["status"], "AVAILABLE")
        self.assertTrue(os.path.isabs(probe["tool_path"]))

    def test_execute(self):
        p = self.repo()
        code, out = self.cli("execute", "--adapter", "git", "--capability", "git.resolve-provenance",
                             "--project", str(p), "--subject-kind", "PROJECT", "--subject-ref", PROJECT_ID,
                             "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["result"]["data"]["repository_revision"], self.head(p))
        (p / "dirty.txt").write_text("x")
        code, _ = self.cli("execute", "--adapter", "git", "--capability", "git.resolve-provenance",
                           "--project", str(p), "--subject-kind", "PROJECT", "--subject-ref", PROJECT_ID)
        self.assertEqual(code, tdg.EXIT_FOR[tdg.CONFLICT])

    def test_module_entrypoint(self):
        out = subprocess.run([sys.executable, "-B", "-m", "gpos.tools", "list"], cwd=str(ROOT),
                             capture_output=True, text=True, env=dict(os.environ, HOME=_HOME))
        self.assertEqual(out.returncode, 0)
        self.assertIn("git", out.stdout)


# ---------------------------------------------------------------- S  determinism and linked worktrees

class S_Determinism(GitCase):
    def test_identical_state_gives_identical_data(self):
        p = self.repo()
        (p / "x.txt").write_text("x")
        first, second = self.inspect(p), self.inspect(p)
        self.assertEqual(first.data, second.data)
        self.assertNotEqual(first.request_id, second.request_id)

    def test_a_linked_worktree_whose_git_is_a_file_is_supported(self):
        p = self.repo()
        worktree = self.tmp / "wt"
        git(p, "worktree", "add", "-q", "--detach", str(worktree))
        self.assertTrue((worktree / ".git").is_file())
        self.assertState(self.inspect(worktree), repository_root=str(worktree.resolve()),
                         head_sha=self.head(p), exact_revision=self.head(p), clean=True, detached=True)


# ---------------------------------------------------------------- T  submodules (hardening)

class T_Submodules(GitCase):
    """Submodule ignore settings may never hide dirtiness (`--ignore-submodules=none`)."""

    def superproject(self):
        lib = self.tmp / "lib"
        lib.mkdir()
        git(lib, "init", "-q", "-b", "main")
        (lib / "a.txt").write_text("a\n")
        git(lib, "add", "a.txt")
        git(lib, "commit", "-q", "-m", "lib")
        p = self.repo()
        # Fixture only: the local file transport prepares the submodule. The adapter enables no transport.
        git(p, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "sub")
        git(p, "commit", "-q", "-m", "add submodule")
        return p

    def assertDirtySubmodule(self, p):
        data = self.assertState(self.inspect(p), clean=False, exact_revision=None, head_sha=self.head(p))
        self.assertGreaterEqual(data["unstaged_count"], 1)
        resolved = self.resolve(p)
        self.assertEqual(resolved.status, tdg.CONFLICT)
        self.assertIsNone(resolved.data["repository_revision"])

    def test_a_modified_tracked_file_inside_a_submodule(self):
        p = self.superproject()
        (p / "sub" / "a.txt").write_text("dirty\n")
        self.assertDirtySubmodule(p)

    def test_local_config_ignore_all_cannot_hide_a_dirty_submodule(self):
        p = self.superproject()
        (p / "sub" / "a.txt").write_text("dirty\n")
        git(p, "config", "submodule.sub.ignore", "all")
        self.assertNotIn(" S.M.", git(p, "status", "--porcelain=v2", "-z").stdout)  # precondition: Git hides it
        self.assertDirtySubmodule(p)

    def test_gitmodules_ignore_all_cannot_hide_a_dirty_submodule(self):
        p = self.superproject()
        git(p, "config", "-f", ".gitmodules", "submodule.sub.ignore", "all")
        git(p, "commit", "-q", "-am", "ignore the submodule in .gitmodules")
        (p / "sub" / "a.txt").write_text("dirty\n")
        self.assertNotIn(" S.M.", git(p, "status", "--porcelain=v2", "-z").stdout)  # precondition: Git hides it
        self.assertDirtySubmodule(p)

    def test_an_untracked_file_only_inside_the_submodule(self):
        p = self.superproject()
        (p / "sub" / "new.txt").write_text("untracked in the submodule\n")
        git(p, "config", "submodule.sub.ignore", "untracked")
        self.assertDirtySubmodule(p)

    def test_a_submodule_checked_out_at_another_commit(self):
        p = self.superproject()
        (p / "sub" / "b.txt").write_text("b\n")
        git(p / "sub", "add", "b.txt")
        git(p / "sub", "commit", "-q", "-m", "moves the submodule HEAD")
        git(p, "config", "submodule.sub.ignore", "all")
        self.assertDirtySubmodule(p)

    def test_a_clean_submodule_is_unchanged_behaviour(self):
        p = self.superproject()
        git(p, "config", "submodule.sub.ignore", "all")
        self.assertState(self.inspect(p), clean=True, exact_revision=self.head(p))
        self.assertEqual(self.resolve(p).data["repository_revision"], self.head(p))

    def test_the_submodules_own_fsmonitor_hook_never_runs(self):
        """Git runs `git status` inside each submodule itself; the command-scope override must reach it."""
        p = self.superproject()
        marker = self.tmp / "SUBMODULE_HOOK_RAN"
        hook = self.tmp / "submodule-hook"
        hook.write_text(f"#!/bin/sh\necho ran >> '{marker}'\nexit 1\n")
        hook.chmod(0o755)
        git(p / "sub", "config", "core.fsmonitor", str(hook))
        (p / "sub" / "a.txt").write_text("dirty\n")
        self.assertDirtySubmodule(p)
        self.assertFalse(marker.exists(), "the submodule's fsmonitor hook ran under the adapter")
        git(p, "status", "--porcelain=v2", "-z", "--ignore-submodules=none")
        self.assertTrue(marker.exists(), "precondition: plain status would have run the submodule's hook")

    def test_a_superproject_is_left_untouched(self):
        p = self.superproject()
        (p / "sub" / "a.txt").write_text("dirty\n")
        before = tree_digest(p)  # includes .git/modules/sub: the submodule's index, refs and config
        for capability in (ga.INSPECT, ga.RESOLVE_PROVENANCE):
            self.run_cap(capability, p)
        self.assertEqual(tree_digest(p), before)


# ---------------------------------------------------------------- U  fsmonitor neutralized (hardening)

class U_Fsmonitor(GitCase):
    def hook(self):
        marker = self.tmp / "FSMONITOR_RAN"
        hook = self.tmp / "fsmonitor-hook"
        hook.write_text(f"#!/bin/sh\necho ran >> '{marker}'\nexit 1\n")
        hook.chmod(0o755)
        return hook, marker

    def test_a_configured_hook_program_never_runs(self):
        p = self.repo()
        hook, marker = self.hook()
        git(p, "config", "core.fsmonitor", str(hook))
        head = self.head(p)
        self.assertState(self.inspect(p), clean=True, exact_revision=head)   # still correct
        self.assertEqual(self.resolve(p).data["repository_revision"], head)
        (p / "notes.txt").write_text("x")
        self.assertState(self.inspect(p), clean=False, exact_revision=None, untracked_count=1)
        self.assertFalse(marker.exists(), "the configured fsmonitor hook ran under the adapter")
        git(p, "status", "--porcelain=v2", "-z")
        self.assertTrue(marker.exists(), "precondition: plain status would have run the hook")

    def test_the_builtin_daemon_is_never_started(self):
        p = self.repo()
        git(p, "config", "core.fsmonitor", "true")
        self.addCleanup(git, p, "fsmonitor--daemon", "stop", check=False)
        before = sorted(x.name for x in (p / ".git").iterdir())
        self.assertState(self.inspect(p), clean=True)
        self.assertEqual(sorted(x.name for x in (p / ".git").iterdir()), before)
        self.assertFalse(any(name.startswith("fsmonitor--daemon") for name in before))
        status = git(p, "fsmonitor--daemon", "status", check=False)
        self.assertNotIn("is watching", status.stdout + status.stderr)
        supported = "not watching" in (status.stdout + status.stderr)
        if supported:  # where Git has the builtin daemon, prove the check is sensitive
            git(p, "status", "--porcelain=v2", "-z")
            watching = git(p, "fsmonitor--daemon", "status", check=False)
            self.assertIn("is watching", watching.stdout + watching.stderr)

    def test_the_override_is_fixed_and_adapter_owned(self):
        self.assertEqual(ga.FSMONITOR_OVERRIDE, (("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "core.fsmonitor"),
                                                 ("GIT_CONFIG_VALUE_0", "false")))
        for pair in ga.FSMONITOR_OVERRIDE:
            self.assertIn(pair, ga.ENVIRONMENT)
        hostile = {"PATH": "/bin", "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                   "GIT_CONFIG_VALUE_0": "/tmp/evil-hook", "GIT_CONFIG_KEY_1": "core.pager", "GIT_CONFIG_VALUE_1": "x",
                   "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=/tmp/evil-hook'"}
        built = ga.ENVIRONMENT_POLICY.build(hostile)
        self.assertEqual((built["GIT_CONFIG_COUNT"], built["GIT_CONFIG_KEY_0"], built["GIT_CONFIG_VALUE_0"]),
                         ("1", "core.fsmonitor", "false"))
        for leaked in ("GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1", "GIT_CONFIG_PARAMETERS"):
            self.assertNotIn(leaked, built)

    def test_provenance_names_the_override_without_its_values(self):
        env = self.inspect(self.repo()).provenance.to_dict()["environment"]
        self.assertTrue({"GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"} <= set(env["set_names"]))
        self.assertNotIn("core.fsmonitor", json.dumps(env))

    def test_no_configuration_is_written(self):
        p = self.repo()
        hook, _ = self.hook()
        git(p, "config", "core.fsmonitor", str(hook))
        config = (p / ".git" / "config").read_bytes()
        self.inspect(p)
        self.assertEqual((p / ".git" / "config").read_bytes(), config)


# ---------------------------------------------------------------- V  raw machine output and version floor

class V_RawOutput(GitCase):
    def test_the_status_parser_reads_bytes_not_text(self):
        oid = "d" * 40
        undecodable = b"\xff\xfe not utf-8 \x80"
        raw = (f"# branch.oid {oid}\0# branch.head main\0".encode()
               + b"? " + undecodable + b"\0"
               + f"1 .M N... 100644 100644 100644 {oid} {oid} ".encode() + undecodable + b"\nline\0"
               + b"? api_key=abc\0? ordinary.txt\0")
        state = gs.parse(raw)
        self.assertEqual((state["untracked_count"], state["unstaged_count"]), (3, 1))

    def test_an_undecodable_branch_name_is_deterministic_and_cannot_move_a_boundary(self):
        oid = "e" * 40
        raw = f"# branch.oid {oid}\0".encode() + b"# branch.head feature-\xff\0" + b"? f\0"
        first, second = gs.parse(raw), gs.parse(raw)
        self.assertEqual(first["branch"], "feature-\\xff")
        self.assertEqual(first, second)
        self.assertEqual(first["untracked_count"], 1)

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "macOS (APFS) and Windows refuse filenames that are not valid UTF-8; the parser-level "
                         "test above covers the bytes")
    def test_a_real_non_utf8_filename(self):
        p = self.repo()
        (p / os.fsdecode(b"raw-\xff-name.txt")).write_text("x")
        self.assertState(self.inspect(p), untracked_count=1, clean=False)

    def test_a_credential_shaped_branch_name(self):
        p = self.repo()
        git(p, "switch", "-q", "-c", "token=branchSECRET")
        result = self.inspect(p)
        self.assertState(result, clean=True, exact_revision=self.head(p), detached=False)
        self.assertNotIn("branchSECRET", json.dumps(result.to_dict()))  # redacted on the way out

    def test_the_result_never_holds_raw_bytes(self):
        p = self.repo()
        (p / "api_key=abc").write_text("x")
        result = self.inspect(p)

        def walk(value, seen=()):
            if isinstance(value, (bytes, bytearray)):
                self.fail("raw bytes reached the ToolResult")
            if hasattr(value, "__dataclass_fields__") and id(value) not in seen:
                for name in value.__dataclass_fields__:
                    walk(getattr(value, name), seen + (id(value),))
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item, seen)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item, seen)
        walk(result)
        self.assertEqual(result.stdout, "")

    def test_the_minimum_version_is_the_first_that_understands_a_boolean_fsmonitor(self):
        self.assertEqual(ga.MINIMUM_VERSION, (2, 36, 0))
        exe = self.tmp / "bin" / "git"
        exe.parent.mkdir()
        for version, expected in (("2.35.1", tmodel.VERSION_UNSUPPORTED), ("2.31.0", tmodel.VERSION_UNSUPPORTED),
                                  ("2.36.0", tmodel.AVAILABLE)):
            exe.write_text(f"#!{tproc.interpreter_path()}\nprint('git version {version}')\n")
            exe.chmod(0o755)
            self.assertEqual(GitAdapter(which=lambda name: str(exe)).probe().status, expected, version)


if __name__ == "__main__":
    if GIT is None:
        print("GIT_RUNTIME_UNAVAILABLE_FOR_PHASE2C1: these are real integration tests and require Git")
        sys.exit(1)
    result = unittest.main(verbosity=1, exit=False).result
    shutil.rmtree(_HOME, ignore_errors=True)
    version = subprocess.run([GIT, "--version"], capture_output=True, text=True).stdout.strip()
    print(f"GPOS Git provenance adapter tests (real {version})")
    sys.exit(0 if result.wasSuccessful() else 1)
