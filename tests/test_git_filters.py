#!/usr/bin/env python3
"""D-G1 real-Git adversarial qualification; unsafe controls are intentionally TEST_ONLY.

Run: python -B -X utf8 tests/test_git_filters.py
Each sentinel is an absolute harmless program in this run's disposable lab. Controls use the
frozen alpha.27 status vector and environment, through the same audited process boundary.
"""
import dataclasses
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import test_git_adapter as gt
from gpos.tools.git import inspection as gi


class FilterSecurity(gt.GitCase):
    def sentinel(self, kind="clean"):
        marker = self.tmp / (kind + "-executed")
        program = gt.windows_standin.write_tool(self.tmp / (kind + "-sentinel"),
            "import sys\nfrom pathlib import Path\n" +
            f"Path({str(marker)!r}).write_text('harmless D-G1 sentinel executed')\n" +
            ("sys.stdout.buffer.write(sys.stdin.buffer.read())\n" if kind == "clean" else "sys.exit(0)\n"))
        # Git's documented filter command is a shell command; paths are test-generated and quoted.
        command = '"' + str(program).replace("\\", "/") + '"'
        return marker, command

    def configure(self, p, command, kind="clean", source="local"):
        if source == "local":
            gt.git(p, "config", f"filter.sentinel.{kind}", command)
        elif source == "global":
            gt.git(p, "config", "--global", f"filter.sentinel.{kind}", command)
            self.addCleanup(gt.git, p, "config", "--global", "--remove-section", "filter.sentinel")
        elif source in ("include", "includeIf"):
            included = self.tmp / "included-config"
            gt.git(p, "config", "-f", str(included), f"filter.sentinel.{kind}", command)
            key = "include.path" if source == "include" else "includeIf.onbranch:main.path"
            gt.git(p, "config", key, str(included))
        elif source == "worktree":
            gt.git(p, "config", "extensions.worktreeConfig", "true")
            gt.git(p, "config", "--worktree", f"filter.sentinel.{kind}", command)
        else:
            raise AssertionError(source)

    def select(self, p, source="tracked"):
        target = p / ".game" / "PROJECT.md"
        rule = "*.md filter=sentinel\n"
        if source == "tracked":
            (p / ".gitattributes").write_text(rule)
        elif source == "nested":
            (p / ".game" / ".gitattributes").write_text(rule)
        elif source == "macro":
            (p / ".gitattributes").write_text("[attr]secret filter=sentinel\n*.md secret\n")
        elif source == "info":
            (p / ".git" / "info" / "attributes").write_text(rule)
        elif source == "global":
            attributes = self.tmp / "global-attributes"
            attributes.write_text(rule)
            gt.git(p, "config", "core.attributesFile", str(attributes))
        elif source == "index":
            # Install attributes before configuring a program, then remove the work-tree copy.
            (p / ".gitattributes").write_text(rule)
            gt.git(p, "add", ".gitattributes")
            gt.git(p, "commit", "-q", "-m", "index attributes")
            (p / ".gitattributes").unlink()
        else:
            raise AssertionError(source)
        if source in ("tracked", "nested", "macro"):
            gt.git(p, "add", "-A")
            gt.git(p, "commit", "-q", "-m", "select sentinel attribute without executable")
        info = target.stat()
        os.utime(target, (info.st_atime - 1000, info.st_mtime - 1000))
        return target

    def control(self, p):
        env = gt.tproc.EnvironmentPolicy(overrides=(
            ("GIT_TERMINAL_PROMPT", "0"), ("GIT_OPTIONAL_LOCKS", "0"), ("GIT_PAGER", "cat"), ("LC_ALL", "C"),
            ("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "core.fsmonitor"), ("GIT_CONFIG_VALUE_0", "false")))
        spec = gt.tproc.ToolProcessSpec(executable=gt.GIT, argv=gt.ga.STATUS_ARGV, cwd=str(p), env=env, timeout=10)
        outcome = gt.tproc.run_process(spec, (str(p),))
        self.assertFalse(outcome.timed_out)
        self.assertTrue(outcome.integrity_ok)
        return outcome

    def refused(self, result):
        self.assertEqual(result.status, gt.tdg.FAILED, result.to_dict())
        self.assertFalse(result.data, "an unproven state must never expose HEAD as an exact revision")
        self.assertNotIn("repository_revision", result.provenance.to_dict())

    def exercise(self, kind="clean", config="local", attributes="tracked", dirty=False):
        p = self.repo()
        (p / "payload.md").write_text("original\n")
        gt.git(p, "add", "payload.md")
        gt.git(p, "commit", "-q", "-m", "payload")
        target = self.select(p, attributes)
        marker, command = self.sentinel(kind)
        self.configure(p, command, kind, config)
        if dirty:
            (p / "payload.md").write_text("modified\n")  # equal size: force content comparison
        control = self.control(p)
        control_clean = gt.gs.parse(control.raw_stdout)["clean"]
        self.assertEqual(control_clean, not dirty and attributes != "index")
        self.assertTrue(marker.is_file(), "unsafe control must execute the harmless sentinel")
        marker.unlink()
        before = gt.tree_digest(p)
        for operation in (self.inspect, self.resolve):
            self.refused(operation(p))
            self.assertFalse(marker.exists(), "remediated inspection executed a repository filter")
        self.assertEqual(gt.tree_digest(p), before)
        print(f"D_G1_SENTINEL kind={kind} config={config} attributes={attributes} dirty={dirty} "
              f"control=EXECUTED control_clean={control_clean} corrected=NOT_EXECUTED revision=REFUSED", flush=True)

    def test_clean_filter_control_and_guard(self):
        self.exercise()

    def test_process_filter_control_and_guard(self):
        self.exercise(kind="process")

    def test_dirty_filter_cannot_report_head(self):
        self.exercise(dirty=True)

    def test_included_configuration(self):
        self.exercise(config="include")

    def test_conditional_configuration(self):
        self.exercise(config="includeIf")

    def test_global_configuration(self):
        self.exercise(config="global")

    def test_worktree_configuration(self):
        self.exercise(config="worktree")

    def test_info_attributes(self):
        self.exercise(attributes="info")

    def test_global_attributes(self):
        self.exercise(attributes="global")

    def test_nested_attributes(self):
        self.exercise(attributes="nested")

    def test_attribute_macro(self):
        self.exercise(attributes="macro")

    def test_index_attribute_fallback(self):
        self.exercise(attributes="index")

    def test_stat_clean_filtered_file_is_still_unproven(self):
        p = self.repo()
        (p / ".gitattributes").write_text("*.md filter=sentinel\n")
        gt.git(p, "add", ".gitattributes")
        gt.git(p, "commit", "-q", "-m", "attributes")
        marker, command = self.sentinel()
        self.configure(p, command)
        self.refused(self.resolve(p))
        self.assertFalse(marker.exists())

    def test_unused_filters_do_not_block_clean_revision(self):
        p = self.repo()
        marker, command = self.sentinel()
        self.configure(p, command)
        self.assertEqual(self.resolve(p).data["repository_revision"], self.head(p))
        self.assertFalse(marker.exists())

    def test_optional_missing_driver_is_fail_closed(self):
        p = self.repo()
        self.select(p)
        self.refused(self.resolve(p))

    def test_required_filter_does_not_execute(self):
        p = self.repo()
        self.select(p)
        marker, command = self.sentinel()
        self.configure(p, command)
        gt.git(p, "config", "filter.sentinel.required", "true")
        self.refused(self.resolve(p))
        self.assertFalse(marker.exists())

    def test_submodule_filter_is_refused_before_status(self):
        p, lib = self.repo("p"), self.repo("lib")
        gt.git(p, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "sub")
        gt.git(p, "commit", "-q", "-m", "submodule")
        sub = p / "sub"
        self.select(sub, "tracked")
        marker, command = self.sentinel()
        self.configure(sub, command)
        self.control(p)
        self.assertTrue(marker.exists())
        marker.unlink()
        self.refused(self.resolve(p))
        self.assertFalse(marker.exists())

    def test_linked_worktree_filter(self):
        p = self.repo()
        linked = self.tmp / "linked"
        gt.git(p, "worktree", "add", "-q", "--detach", str(linked))
        self.select(linked)
        marker, command = self.sentinel()
        self.configure(linked, command, source="worktree")
        self.control(linked)
        self.assertTrue(marker.exists())
        marker.unlink()
        self.refused(self.resolve(linked))
        self.assertFalse(marker.exists())

    def test_source_config_change_cannot_inject_executable(self):
        p = self.repo()
        self.select(p)
        marker, command = self.sentinel()
        original = gt.GitAdapter._run
        def injecting(adapter, context, argv, capture_bytes, root=None, inspection=None):
            if argv == gt.ga.STATUS_ARGV:
                self.configure(p, command)
            return original(adapter, context, argv, capture_bytes, root, inspection)
        # Remove the attributes to pass the preflight, then inject both attribute and config at status.
        (p / ".gitattributes").unlink()
        def race(adapter, context, argv, capture_bytes, root=None, inspection=None):
            if argv == gt.ga.STATUS_ARGV:
                (p / ".gitattributes").write_text("*.md filter=sentinel\n")
            return injecting(adapter, context, argv, capture_bytes, root, inspection)
        with mock.patch.object(gt.GitAdapter, "_run", race):
            self.refused(self.resolve(p))
        self.assertFalse(marker.exists())

    def test_source_file_appearing_during_status_is_unproven(self):
        p = self.repo()
        original = gt.GitAdapter._run
        def race(adapter, context, argv, capture_bytes, root=None, inspection=None):
            if argv == gt.ga.STATUS_ARGV:
                (p / "late-untracked.txt").write_text("not present in the private copy\n")
            return original(adapter, context, argv, capture_bytes, root, inspection)
        with mock.patch.object(gt.GitAdapter, "_run", race):
            self.refused(self.resolve(p))

    def test_linked_gitfile_owner_change_is_unproven(self):
        p, other = self.repo("p"), self.repo("other")
        (other / "new.txt").write_text("another repository\n")
        gt.git(other, "add", "new.txt")
        gt.git(other, "commit", "-q", "-m", "different HEAD")
        linked = self.tmp / "linked"
        gt.git(p, "worktree", "add", "-q", "--detach", str(linked))
        original = gt.GitAdapter._run
        changed = []
        def race(adapter, context, argv, capture_bytes, root=None, inspection=None):
            if argv == gt.ga.STATUS_ARGV:
                os.chmod(linked / ".git", 0o600)  # Git marks this disposable Windows gitfile read-only
                # OPEN_EXISTING also preserves the Windows hidden attribute; CREATE_ALWAYS is denied for it.
                with (linked / ".git").open("r+", encoding="utf-8") as stream:
                    stream.seek(0)
                    stream.write("gitdir: " + str(other / ".git") + "\n")
                    stream.truncate()
                changed.append(True)
            return original(adapter, context, argv, capture_bytes, root, inspection)
        with mock.patch.object(gt.GitAdapter, "_run", race):
            self.refused(self.resolve(linked))
        self.assertEqual(changed, [True], "fixture must actually replace the gitfile owner")

    def test_clean_and_dirty_submodule_provenance(self):
        p, lib = self.repo("p"), self.repo("lib")
        gt.git(p, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "sub")
        gt.git(p, "commit", "-q", "-m", "submodule")
        clean = self.resolve(p)
        self.assertEqual(clean.status, gt.tdg.SUCCESS, clean.to_dict())
        self.assertEqual(clean.data.get("repository_revision"), self.head(p), clean.to_dict())
        (p / "sub" / "untracked.txt").write_text("dirty child\n")
        dirty = self.resolve(p)
        self.assertEqual(dirty.status, gt.tdg.CONFLICT, dirty.to_dict())
        self.assertIsNone(dirty.data["repository_revision"])

    def test_uninitialized_submodule_is_unproven(self):
        p, lib = self.repo("p"), self.repo("lib")
        gt.git(p, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "sub")
        gt.git(p, "commit", "-q", "-m", "submodule")
        gt.git(p, "submodule", "deinit", "-q", "--all")
        self.refused(self.resolve(p))

    def test_untracked_embedded_repository_does_not_execute_filters(self):
        p = self.repo()
        embedded = self.repo("embedded")
        self.select(embedded)
        marker, command = self.sentinel()
        self.configure(embedded, command)
        destination = p / "embedded"
        self.assertTrue(embedded.resolve().is_relative_to(self.tmp))
        self.assertTrue(destination.resolve().is_relative_to(self.tmp))
        gt.shutil.move(str(embedded), str(destination))  # only this test's disposable fixture
        result = self.resolve(p)
        self.assertEqual(result.status, gt.tdg.CONFLICT, result.to_dict())
        self.assertIsNone(result.data["repository_revision"])
        self.assertFalse(marker.exists())

    def snapshot_attribute_race(self, config):
        p = self.repo()
        (p / ".gitattributes").write_text("*.md -filter\n")
        gt.git(p, "add", ".gitattributes")
        gt.git(p, "commit", "-q", "-m", "explicitly unset filter")
        marker, command = self.sentinel()
        self.configure(p, command, source=config)
        original = gt.GitAdapter._run
        def race(adapter, context, argv, capture_bytes, root=None, inspection=None):
            if argv == gt.ga.STATUS_ARGV and inspection is not None:
                (Path(root) / ".gitattributes").write_text("*.md filter=sentinel\n")
            return original(adapter, context, argv, capture_bytes, root, inspection)
        with mock.patch.object(gt.GitAdapter, "_run", race):
            result = self.resolve(p)
        self.assertEqual(result.status, gt.tdg.CONFLICT, result.to_dict())
        self.assertIsNone(result.data["repository_revision"])
        self.assertFalse(marker.exists(), "attribute race regained a configured program")

    def test_frozen_local_config_is_an_execution_barrier(self):
        self.snapshot_attribute_race("local")

    def test_frozen_global_config_is_an_execution_barrier(self):
        self.snapshot_attribute_race("global")

    def test_system_configuration_is_frozen(self):
        p = self.repo()
        self.select(p)
        marker, command = self.sentinel()
        config = self.tmp / "system-config"
        gt.git(p, "config", "-f", str(config), "filter.sentinel.clean", command)
        policy = gt.tproc.EnvironmentPolicy(overrides=gt.ga.ENVIRONMENT + (("GIT_CONFIG_SYSTEM", str(config)),))
        with mock.patch.object(gt.ga, "ENVIRONMENT_POLICY", policy):
            self.refused(self.resolve(p))
        # Unsafe test-only control against exactly the same isolated system config.
        spec = gt.tproc.ToolProcessSpec(executable=gt.GIT, argv=gt.ga.STATUS_ARGV, cwd=str(p), env=policy, timeout=10)
        gt.tproc.run_process(spec, (str(p),))
        self.assertTrue(marker.exists())

    def test_system_attribute_source_is_copied(self):
        p = self.repo()
        marker, command = self.sentinel()
        self.configure(p, command)
        attributes = self.tmp / "system-attributes"
        attributes.write_text("*.md filter=sentinel\n")
        original = gt.GitAdapter._private_read
        def system_path(adapter, context, argv, root, inspection=None):
            if argv == gi.SYSTEM_ATTRIBUTES_ARGV and inspection is None:
                return os.fsencode(attributes) + b"\n"
            return original(adapter, context, argv, root, inspection)
        # Test-side path seam; no host installation file is modified. check-attr/status remain real Git.
        with mock.patch.object(gt.GitAdapter, "_private_read", system_path):
            self.refused(self.resolve(p))
        self.assertFalse(marker.exists())

    def test_assume_unchanged_dirty_tree_is_unproven(self):
        p = self.repo()
        gt.git(p, "update-index", "--assume-unchanged", ".game/PROJECT.md")
        (p / ".game" / "PROJECT.md").write_text("dirty but hidden\n")
        self.refused(self.resolve(p))

    def test_skip_worktree_dirty_tree_is_unproven(self):
        p = self.repo()
        gt.git(p, "update-index", "--skip-worktree", ".game/PROJECT.md")
        (p / ".game" / "PROJECT.md").write_text("dirty but hidden\n")
        self.refused(self.resolve(p))

    def test_restored_mtime_and_ignored_ctime_cannot_hide_dirty_content(self):
        p = self.repo()
        payload = p / "payload.txt"
        payload.write_bytes(b"original")
        gt.git(p, "add", "payload.txt")
        gt.git(p, "commit", "-q", "-m", "payload")
        original = payload.stat()
        gt.git(p, "config", "core.trustctime", "false")
        gt.git(p, "config", "core.checkStat", "minimal")
        payload.write_bytes(b"modified")
        os.utime(payload, ns=(original.st_atime_ns, original.st_mtime_ns))
        # Avoid Git's racy-index fallback, otherwise this counterexample would hash anyway.
        os.utime(p / ".git" / "index", ns=(original.st_atime_ns + 100000000000,
                                           original.st_mtime_ns + 100000000000))
        with mock.patch.object(gi.InspectionCopy, "invalidate_stats", lambda copy: None):
            control = self.resolve(p)
        self.assertEqual(control.data.get("repository_revision"), self.head(p), control.to_dict())
        result = self.resolve(p)
        self.assertEqual(result.status, gt.tdg.CONFLICT, result.to_dict())
        self.assertIsNone(result.data["repository_revision"])

    def test_missing_partial_clone_object_never_fetches(self):
        p = self.repo()
        gt.git(p, "config", "remote.trap.promisor", "true")
        gt.git(p, "config", "remote.trap.url", "https://invalid.example/no-network")
        self.refused(self.resolve(p))

    def test_copy_bound_is_fail_closed(self):
        p = self.repo()
        with mock.patch.object(gi, "MAX_BYTES", 1):
            self.refused(self.resolve(p))

    def test_reused_external_source_cannot_replace_earlier_observation(self):
        source = self.tmp / "shared-attributes"
        source.write_bytes(b"first")
        copy = gi.InspectionCopy(5)
        try:
            copy.file(source, copy.base / "first")
            source.write_bytes(b"later")
            with self.assertRaises(gi.UnsafeInspection):
                copy.file(source, copy.base / "later")
            with self.assertRaises(gi.UnsafeInspection):
                copy.remember(source, None)
        finally:
            copy.close()

    def test_configuration_is_private_and_frozen(self):
        p = self.repo()
        gt.git(p, "config", "credential.helper", "token=do-not-leak")
        result = self.resolve(p)
        self.assertEqual(result.data["repository_revision"], self.head(p))
        self.assertNotIn("do-not-leak", json.dumps(result.to_dict()))
        flat = gi.frozen_config(gi.config_entries(b'include.path\n/x\0filter.x.clean\nevil\0core.autocrlf\nfalse\0'))
        self.assertNotIn(b"evil", flat)
        self.assertNotIn(b"include", flat)
        self.assertIn(b"autocrlf", flat)

    def test_other_repository_selected_programs_do_not_run(self):
        p = self.repo()
        marker, command = self.sentinel()
        gt.git(p, "config", "diff.external", command)
        gt.git(p, "config", "diff.sentinel.textconv", command)
        gt.git(p, "config", "credential.helper", command)
        gt.git(p, "config", "core.sshCommand", command)
        gt.git(p, "config", "core.alternateRefsCommand", command)
        gt.git(p, "config", "core.pager", command)
        gt.git(p, "config", "pager.status", "true")
        (p / ".gitattributes").write_text("*.md diff=sentinel\n")
        gt.git(p, "add", ".gitattributes")
        gt.git(p, "commit", "-q", "-m", "diff attributes")
        self.assertEqual(self.resolve(p).data.get("repository_revision"), self.head(p))
        self.assertFalse(marker.exists())

    def test_relative_external_ignore_path_keeps_its_original_meaning(self):
        p = self.repo()
        external = self.tmp / "ignored-patterns"
        external.write_text("ignored.txt\n")
        gt.git(p, "config", "core.excludesfile", "../ignored-patterns")
        (p / "ignored.txt").write_text("ignored in the original repository\n")
        self.assertEqual(self.resolve(p).data.get("repository_revision"), self.head(p))

    def test_snapshot_relative_path_cannot_hide_original_untracked_file(self):
        p = self.repo()
        # In the source this path is absent; in a copy it would accidentally point inside its own worktree.
        (p / "ignore-patterns").write_text("hidden.txt\n")
        gt.git(p, "add", "ignore-patterns")
        gt.git(p, "commit", "-q", "-m", "patterns")
        gt.git(p, "config", "core.excludesfile", "../worktree/ignore-patterns")
        (p / "hidden.txt").write_text("must remain untracked\n")
        result = self.resolve(p)
        self.assertEqual(result.status, gt.tdg.CONFLICT, result.to_dict())
        self.assertIsNone(result.data["repository_revision"])

    def test_production_git_platform_gate_is_authorized(self):
        self.assertEqual(gt.ga.DESCRIPTOR.supported_platforms, ("MACOS", "LINUX", "WINDOWS"))
        self.assertIs(gt.GitAdapter.descriptor, gt.ga.DESCRIPTOR)


if __name__ == "__main__":
    if gt.GIT is None:
        sys.exit("REAL_GIT_REQUIRED")
    result = unittest.main(verbosity=2, exit=False).result
    gt.windows_standin.remove_tree(gt._HOME)
    sys.exit(0 if result.wasSuccessful() else 1)
