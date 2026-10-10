"""Workflow qualification guards, tested without an Editor or Player process."""
import copy
import sys
import types
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from trusted_windows_build_testkit import qualify

R = "1" * 40
T = "2" * 64


class Answer:
    def __init__(self, status="SUCCESS", data=None, published=False):
        self.status, self.data = status, data or {}
        self.diagnostics = [types.SimpleNamespace(code="BUILD_PUBLISHED")] if published else []

    def to_dict(self):
        return {"status": self.status, "data": copy.deepcopy(self.data)}


class WorkflowGuards(unittest.TestCase):
    def run_case(self, revisions=None, inspection=None, built=None, manifest=None, error=None):
        calls = []
        revisions = revisions or {}
        def resolve(stage):
            calls.append(stage)
            return revisions.get(stage, Answer(data={"repository_revision": R}))
        def inspect():
            calls.append("inspect")
            return inspection or Answer(data={"buildable": True, "configuration_token": T})
        def build(token, revision):
            calls.append("build")
            self.assertEqual((token, revision), (T, R))
            return built or Answer(published=True)
        def revalidate(answer):
            calls.append("revalidate")
            return manifest if manifest is not None else {"build_revision": R, "build_revision_source": "CALLER_SUPPLIED"}, error
        return qualify(resolve, inspect, build, revalidate), calls

    def test_positive_order_and_caller_attribution(self):
        result, calls = self.run_case()
        self.assertTrue(result["qualified"])
        self.assertEqual(calls, ["git_before", "inspect", "git_between", "build", "git_after", "revalidate"])
        self.assertEqual(result["git_revisions"], [R, R, R])
        self.assertNotIn("git_verified", result["manifest"])

    def test_dirty_before_never_inspects(self):
        result, calls = self.run_case({"git_before": Answer("CONFLICT")})
        self.assertFalse(result["qualified"])
        self.assertEqual(calls, ["git_before"])

    def test_non_success_cannot_supply_a_trusted_revision(self):
        result, calls = self.run_case({"git_before": Answer("OUTCOME_UNKNOWN", {"repository_revision": R})})
        self.assertFalse(result["qualified"])
        self.assertEqual(calls, ["git_before"])

    def test_null_error_data_fails_closed(self):
        answer = Answer("FAILED")
        answer.data = None
        result, calls = self.run_case({"git_before": answer})
        self.assertFalse(result["qualified"])
        self.assertEqual(calls, ["git_before"])
        result, calls = self.run_case(inspection=answer)
        self.assertFalse(result["qualified"])
        self.assertEqual(calls, ["git_before", "inspect"])

    def test_dirty_between_never_builds(self):
        result, calls = self.run_case({"git_between": Answer("CONFLICT")})
        self.assertFalse(result["qualified"])
        self.assertNotIn("build", calls)

    def test_clean_changed_head_between_never_builds(self):
        result, calls = self.run_case({"git_between": Answer(data={"repository_revision": "3" * 40})})
        self.assertFalse(result["qualified"])
        self.assertNotIn("build", calls)

    def test_dirty_after_never_promotes_published_build(self):
        result, calls = self.run_case({"git_after": Answer("CONFLICT")})
        self.assertFalse(result["qualified"])
        self.assertIn("build", calls)
        self.assertNotIn("revalidate", calls)

    def test_clean_changed_head_after_refused(self):
        result, calls = self.run_case({"git_after": Answer(data={"repository_revision": "3" * 40})})
        self.assertFalse(result["qualified"])

    def test_unbuildable_inspection_refused(self):
        result, calls = self.run_case(inspection=Answer(data={"buildable": False, "configuration_token": T}))
        self.assertFalse(result["qualified"])
        self.assertNotIn("build", calls)

    def test_missing_token_refused(self):
        result, calls = self.run_case(inspection=Answer(data={"buildable": True}))
        self.assertFalse(result["qualified"])

    def test_failed_or_unknown_build_never_retried(self):
        for status in ("FAILED", "OUTCOME_UNKNOWN"):
            with self.subTest(status=status):
                result, calls = self.run_case(built=Answer(status))
                self.assertFalse(result["qualified"])
                self.assertEqual(calls.count("build"), 1)
                self.assertNotIn("git_after", calls)

    def test_success_without_publication_refused(self):
        result, calls = self.run_case(built=Answer())
        self.assertFalse(result["qualified"])

    def test_payload_revalidation_failure_refused(self):
        result, calls = self.run_case(error="payload drift")
        self.assertFalse(result["qualified"])

    def test_manifest_revision_or_attribution_change_refused(self):
        for changed in ({"build_revision": "3" * 40}, {"build_revision_source": "GIT_VERIFIED"}, {"git_verified": True}):
            m = {"build_revision": R, "build_revision_source": "CALLER_SUPPLIED", **changed}
            result, calls = self.run_case(manifest=m)
            self.assertFalse(result["qualified"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
