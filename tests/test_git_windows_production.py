#!/usr/bin/env python3
"""Focused Windows gate verification: real default registry, no platform overrides.

No Unity Editor or ADB process is started. Existing sentinel fixture helpers are
reused; corrected requests use the actual production adapter and process backend.
"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_git_adapter as gt
import test_git_filters as filters
from gpos.tools.unity import adapter as unity
from gpos.tools.adb import adapter as adb


@unittest.skipUnless(sys.platform == "win32", "Windows production gate qualification only")
class WindowsProduction(gt.GitCase):
    sentinel = filters.FilterSecurity.sentinel
    configure = filters.FilterSecurity.configure
    select = filters.FilterSecurity.select
    control = filters.FilterSecurity.control
    refused = filters.FilterSecurity.refused
    exercise = filters.FilterSecurity.exercise

    def run_cap(self, capability, project, registry=None, **kwargs):
        registry = registry or gt.default_registry(gt.FW)
        self.assertIn("git", registry.adapter_ids())
        self.assertIs(registry.get("git").descriptor, gt.ga.DESCRIPTOR)
        self.assertIs(gt.GitAdapter.descriptor, gt.ga.DESCRIPTOR)
        before = gt.tree_digest(project)
        captures, bases = [], []
        original_run, original_close = gt.tproc.run_process, gt.gi.InspectionCopy.close

        def recorded(spec, scopes, clock=None):
            outcome = original_run(spec, scopes) if clock is None else original_run(spec, scopes, clock)
            captures.append((spec, outcome))
            return outcome

        def closed(copy):
            bases.append(copy.base)
            return original_close(copy)

        with patch.object(gt.tproc, "run_process", recorded), patch.object(gt.gi.InspectionCopy, "close", closed):
            result = super().run_cap(capability, project, registry, **kwargs)
        self.assertTrue(captures, "a production request must actually run real Git")
        self.assertTrue(bases, "the bounded inspection copy must actually be exercised")
        self.assertTrue(all(not base.exists() for base in bases), "private copy leaked")
        for spec, outcome in captures:
            self.assertTrue(outcome.tree_contained)
            self.assertTrue(outcome.capture_complete)
            self.assertFalse(outcome.timed_out)
            self.assertEqual(Path(spec.executable).resolve(), Path(gt.GIT).resolve())
            if spec.argv == gt.ga.STATUS_ARGV:
                self.assertNotEqual(Path(spec.cwd).resolve(), Path(project).resolve())
                self.assertTrue(any(Path(spec.cwd).is_relative_to(base) for base in bases))
        self.assertEqual(gt.tree_digest(project), before, "production request mutated source")
        self.assertFalse(result.mutation_performed)
        self.assertNotIn("raw_stdout", json.dumps(result.to_dict()))
        return result

    def test_default_discovery_and_probe(self):
        registry = gt.default_registry(gt.FW)
        self.assertIn("git", registry.adapter_ids())
        self.assertIs(registry.get("git").descriptor, gt.ga.DESCRIPTOR)
        with gt.Recorder() as rec:
            probe = registry.probe("git")
        self.assertEqual(probe.status, gt.tmodel.AVAILABLE, probe.to_dict())
        self.assertEqual(probe.platform, "WINDOWS")
        self.assertEqual(Path(probe.tool_path).resolve(), Path(gt.GIT).resolve())
        self.assertEqual(rec.argvs, [gt.ga.VERSION_ARGV])
        self.assertEqual({c for c, ok, _ in probe.capability_availability if ok}, {gt.ga.INSPECT, gt.ga.RESOLVE_PROVENANCE})

    def test_both_capabilities_resolve_clean_head(self):
        p = self.repo()
        self.assertState(self.inspect(p), clean=True, exact_revision=self.head(p))
        result = self.resolve(p)
        self.assertEqual(result.status, gt.tdg.SUCCESS, result.to_dict())
        self.assertEqual(result.data["repository_revision"], self.head(p))

    def test_dirty_tree_is_never_an_exact_revision(self):
        p = self.repo()
        (p / "untracked.txt").write_text("untracked\n")
        self.assertState(self.inspect(p), clean=False, untracked_count=1, exact_revision=None)
        result = self.resolve(p)
        self.assertEqual(result.status, gt.tdg.CONFLICT, result.to_dict())
        self.assertIsNone(result.data["repository_revision"])

    def test_clean_filter_unsafe_control_and_production_refusal(self):
        self.exercise(kind="clean")

    def test_process_filter_unsafe_control_and_production_refusal(self):
        self.exercise(kind="process")

    def test_private_configuration_never_leaks_credentials(self):
        p = self.repo()
        secret = "token=gate-qualification-secret"
        gt.git(p, "config", "credential.helper", secret)
        result = self.resolve(p)
        self.assertEqual(result.status, gt.tdg.SUCCESS, result.to_dict())
        self.assertNotIn(secret, json.dumps(result.to_dict()))

    def test_authorized_unity_build_gate_and_unchanged_adb_availability(self):
        registry = gt.default_registry(gt.FW)
        expected = {"unity.inspect-project", "unity.run-editmode-tests", "unity.run-playmode-tests",
                    "unity.live-install-bridge", "unity.live-status", "unity.live-attach", "unity.live-inspect",
                    "unity.live-detach", "unity.live-object-inspect", "unity.live-create-gameobject",
                    "unity.live-set-transform", "unity.live-save-scene",
                    "unity.inspect-build-configuration", "unity.build-player"}
        self.assertEqual(len(unity.WINDOWS_CAPABILITIES), 14)
        self.assertEqual(set(unity.WINDOWS_CAPABILITIES), expected)
        self.assertIs(registry.get("adb").descriptor, adb.DESCRIPTOR)
        self.assertEqual(adb.DESCRIPTOR.supported_platforms, ("MACOS", "LINUX"))
        with patch.object(gt.tproc, "run_process", side_effect=AssertionError("unavailable capability started a process")):
            probe = registry.probe("adb")
            self.assertEqual(probe.status, gt.tmodel.UNAVAILABLE)
            self.assertIn("PLATFORM_UNSUPPORTED", {d.code for d in probe.diagnostics})
            for cap in ("unity.live-enter-playmode", "unity.live-add-component"):
                request = gt.ExecutionRequest(adapter_id="unity", capability_id=cap,
                                              subject=gt.Subject("PROJECT", gt.PROJECT_ID))
                result = registry.get("unity").execute(request, None)
                self.assertIn("PLATFORM_UNSUPPORTED", {d.code for d in result.diagnostics})

    def test_security_limits_and_version_contract_are_preserved(self):
        self.assertEqual(gt.ga.MINIMUM_VERSION, (2, 43, 0))
        self.assertEqual((gt.gi.MAX_BYTES, gt.gi.MAX_ENTRIES, gt.gi.MAX_REPOSITORIES, gt.gi.CAPTURE_BYTES),
                         (256 * 1024 * 1024, 20000, 32, 1024 * 1024))
        self.assertEqual(gt.ga.DESCRIPTOR.supported_platforms, ("MACOS", "LINUX", "WINDOWS"))
        self.assertEqual(gt.ga.DESCRIPTOR.network, "FORBIDDEN")
        self.assertEqual({c.id for c in gt.ga.DESCRIPTOR.capabilities}, {gt.ga.INSPECT, gt.ga.RESOLVE_PROVENANCE})


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False).result
    gt.windows_standin.remove_tree(gt._HOME)
    sys.exit(0 if result.wasSuccessful() else 1)
