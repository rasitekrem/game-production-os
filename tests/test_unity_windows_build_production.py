"""Focused Windows production gate checks; real workflow has separate retained evidence."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gpos.framework import load_framework
from gpos.tools import model, process
from gpos.tools.registry import default_registry
from gpos.tools.unity import adapter as ua, build as ub, build_windows as dispatch
from gpos.tools.unity import authoring, live
from gpos.tools.git import adapter as git


@unittest.skipUnless(sys.platform == 'win32', 'Windows production gate; no POSIX runtime claim')
class ProductionGate(unittest.TestCase):
    def setUp(self):
        self.registry = default_registry(load_framework())
        self.adapter = self.registry.get('unity')
        self.summary = {'unity_project': 'Game', 'editor_version': '6000.6.4f1'}
        self.context = types.SimpleNamespace(project_root=str(ROOT),
                                            probe=types.SimpleNamespace(tool_version='6000.6.4f1'))

    def call(self, cap, **overrides):
        fields = dict(capability_id=cap, request_id='production-guard', inputs={'unity_project': 'Game'},
                      target_platform='WINDOWS', build_revision='1'*40, build_id=None)
        fields.update(overrides)
        request = types.SimpleNamespace(**fields)
        with patch.object(ua.up, 'preflight', return_value=(ROOT/'Game', self.summary)), \
             patch.object(process, 'run_process', side_effect=AssertionError('refusal must not launch a tool')):
            return self.adapter.execute(request, self.context)

    def codes(self, outcome):
        return {d.code for d in outcome.diagnostics}

    def test_exact_production_adapters_and_identity(self):
        self.assertEqual(self.registry.adapter_ids(), ['adb','blender','ffmpeg','ffprobe','git','player','unity'])
        self.assertIs(type(self.adapter), ua.UnityAdapter)
        self.assertIs(self.adapter.descriptor, ua.DESCRIPTOR)
        self.assertTrue(all(not d.test_only for d in self.registry.list_adapters()))

    def test_exact_fourteen_capability_allowlist(self):
        expected = {ua.INSPECT, ua.EDITMODE, ua.PLAYMODE, live.INSTALL, live.STATUS, live.ATTACH,
                    live.INSPECT, live.DETACH, authoring.INSPECT_OBJECT, authoring.CREATE,
                    authoring.SET_TRANSFORM, authoring.SAVE_SCENE, ub.INSPECT_BUILD, ub.BUILD}
        self.assertEqual(len(ua.WINDOWS_CAPABILITIES), 14)
        self.assertEqual(set(ua.WINDOWS_CAPABILITIES), expected)
        self.assertEqual(len(ua.DESCRIPTOR.capabilities), 47)

    def test_unrelated_thirty_three_capabilities_refused_before_dispatch(self):
        unsupported = [c.id for c in ua.DESCRIPTOR.capabilities if c.id not in ua.WINDOWS_CAPABILITIES]
        self.assertEqual(len(unsupported), 33)
        for cap in unsupported:
            with self.subTest(cap=cap), patch.object(ua.up, 'preflight', side_effect=AssertionError('dispatch reached')):
                self.assertEqual(self.codes(self.adapter.execute(types.SimpleNamespace(capability_id=cap),None)),
                                 {'PLATFORM_UNSUPPORTED'})

    def test_each_new_capability_uses_existing_windows_dispatch(self):
        for cap in ub.CAPABILITY_IDS:
            with self.subTest(cap=cap), patch.object(dispatch,'run',return_value='existing-production-dispatch') as run:
                self.assertEqual(self.call(cap), 'existing-production-dispatch')
                self.assertIs(run.call_args.args[0], self.adapter)
                self.assertEqual(run.call_args.args[1], cap)

    def test_probe_reports_both_new_capabilities(self):
        outcome = process.ProcessOutcome(exit_code=0, raw_stdout=b'6000.6.4f1\n')
        with patch.object(self.adapter,'discover',return_value=[('6000.6.4f1',str(ROOT/'Unity.exe'))]), \
             patch.object(process,'run_process',return_value=outcome):
            probe = self.adapter.probe()
        self.assertEqual(probe.status, model.AVAILABLE)
        self.assertEqual({c for c,ok,_ in probe.capability_availability if ok},set(ua.WINDOWS_CAPABILITIES))
        self.assertTrue(all(dict((c,ok) for c,ok,_ in probe.capability_availability)[cap] for cap in ub.CAPABILITY_IDS))

    def test_git_two_and_adb_platforms_unchanged(self):
        self.assertIs(self.registry.get('git').descriptor,git.DESCRIPTOR)
        self.assertEqual({c.id for c in git.DESCRIPTOR.capabilities},{git.INSPECT,git.RESOLVE_PROVENANCE})
        self.assertEqual(git.DESCRIPTOR.supported_platforms,('MACOS','LINUX','WINDOWS'))
        self.assertEqual(self.registry.get('adb').descriptor.supported_platforms,('MACOS','LINUX'))

    def test_wrong_project_version_refused(self):
        self.summary['editor_version']='6000.6.3f1'
        self.assertIn('ENGINE_EDITOR_VERSION_UNAVAILABLE',self.codes(self.call(ub.INSPECT_BUILD)))

    def test_unqualified_installed_version_refused_even_when_project_matches(self):
        self.summary['editor_version']='6000.6.3f1'
        self.context.probe.tool_version='6000.6.3f1'
        self.assertIn('INVALID_TOOL_REQUEST',self.codes(self.call(ub.INSPECT_BUILD)))

    def test_other_target_platform_refused(self):
        for cap in ub.CAPABILITY_IDS:
            self.assertIn('INVALID_TOOL_REQUEST',self.codes(self.call(cap,target_platform='MACOS')))

    def test_no_inferred_head_or_missing_caller_revision(self):
        for revision in (None,'HEAD','git_verified=true'):
            with self.subTest(revision=revision):
                self.assertIn('INVALID_TOOL_REQUEST',self.codes(self.call(ub.BUILD,build_revision=revision)))

    def test_no_caller_build_id_or_missing_token(self):
        self.assertIn('INVALID_TOOL_REQUEST',self.codes(self.call(ub.BUILD,build_id='invented')))
        self.assertIn('INVALID_TOOL_REQUEST',self.codes(self.call(ub.BUILD)))

    def test_corrected_limitations_are_explicit_and_do_not_grant_attestation(self):
        text=dispatch.LIMITATIONS[0]
        for required in ('available in alpha.28','qualified bounded subset','does not invoke Git or verify',
                         'CALLER_SUPPLIED','separate pre/post Git observations','not authenticated Git attestation'):
            self.assertIn(required,text)
        self.assertNotIn('unavailable pending',text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
