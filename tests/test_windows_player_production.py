"""Windows production registry/dispatch regressions; native fault injections are labelled, never runtime evidence."""
import ast
import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from gpos.tools.registry import default_registry
from gpos.tools.player import adapter, contract as c, windows
from gpos.tools.execution import ExecutionRequest, execute, AdapterOutcome
from gpos.tools.model import Actor, Subject
import test_unity_windows_build as fixtures

class ProductionGate(unittest.TestCase):
    def test_actual_inventory_and_windows_capabilities(self):
        registry=default_registry()
        self.assertEqual(registry.adapter_ids(),['adb','blender','ffmpeg','ffprobe','git','player','unity'])
        self.assertIs(type(registry.get('player')),adapter.PlayerAdapter)
        self.assertFalse(registry.allow_test_only)
        self.assertFalse(registry.get('player').descriptor.test_only)
        probe=registry.probe('player')
        self.assertEqual(probe.status,'AVAILABLE')
        self.assertEqual({cid for cid,a,_ in probe.capability_availability if a},{c.LAUNCH,c.STATUS,c.STOP})
        self.assertEqual({cid for cid,a,_ in probe.capability_availability if not a},{c.INSTALL,c.SCREENSHOT,c.VIDEO})
        self.assertEqual(sum(a for _,a,_ in registry.probe('unity').capability_availability),14)
        self.assertEqual(sum(a for _,a,_ in registry.probe('git').capability_availability),2)
        self.assertEqual(registry.probe('adb').status,'UNAVAILABLE')

    def test_dispatch_uses_descriptor_free_backend_not_candidate_adapter(self):
        self.assertFalse(hasattr(windows.WindowsLifecycle,'descriptor'))
        with patch.object(windows.WindowsPlayerAdapter,'execute',side_effect=AssertionError('TEST_ONLY substitution')):
            for cap in (c.LAUNCH,c.STATUS,c.STOP):
                request=dataclasses.make_dataclass('Request',[('capability_id',str)])(cap)
                expected=AdapterOutcome(data={'dispatch':cap})
                with patch.object(windows.WindowsLifecycle,'execute',return_value=expected) as call:
                    self.assertIs(default_registry().get('player').execute(request,None),expected)
                    call.assert_called_once_with(request,None)

    def test_unsupported_capture_never_reaches_macos_or_windows_backend(self):
        production=default_registry().get('player')
        with patch.object(windows.WindowsLifecycle,'execute',side_effect=AssertionError('capture backend')):
            with patch.object(production,'_capture',side_effect=AssertionError('macOS capture')):
                for cap in (c.INSTALL,c.SCREENSHOT,c.VIDEO):
                    request=dataclasses.make_dataclass('Request',[('capability_id',str)])(cap)
                    result=production.execute(request,None)
                    self.assertEqual(result.diagnostics[0].code,'PLATFORM_UNSUPPORTED')
                    self.assertFalse(result.mutation_performed)
                    self.assertFalse(result.artifacts)

    def test_probe_digest_failure_does_not_open_gate(self):
        with patch.object(windows.native,'helper_digest',side_effect=ValueError('untrusted helper')):
            probe=default_registry().probe('player')
        self.assertEqual(probe.status,'UNAVAILABLE')

    def test_macos_dispatch_is_unchanged_after_windows_guard(self):
        source=(ROOT/'gpos/tools/player/adapter.py').read_text()
        frozen=json.loads((ROOT/'tests/fixtures/posix-parity-alpha22.json').read_text())
        import posix_parity
        current=posix_parity.units(source)
        old=frozen['units']['gpos/tools/player/adapter.py']
        for key,value in old.items():
            if key not in ('imports','assign DESCRIPTOR'):
                self.assertEqual(current[key],value,key)
        tree=ast.parse(source)
        descriptor=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='DESCRIPTOR' for t in n.targets))
        kw={x.arg:x for x in descriptor.keywords}
        self.assertEqual(ast.literal_eval(kw['supported_platforms'].value),('MACOS','WINDOWS'))
        kw['supported_platforms'].value=ast.parse("('MACOS',)",mode='eval').body
        self.assertEqual(len(kw['compatibility_notes'].value.elts),4)
        kw['compatibility_notes'].value.elts.pop(0)
        expected=old['assign DESCRIPTOR']['stmt']
        self.assertEqual(posix_parity._h(next(n for n in tree.body if isinstance(n,ast.Assign) and n.value is descriptor)),expected)

class ProductionConsumer(unittest.TestCase):
    def setUp(self):
        import shutil
        self.tmp=tempfile.TemporaryDirectory(prefix='gpos-player-production-')
        self.root=Path(self.tmp.name).resolve()
        shutil.copytree(ROOT/'tests/fixtures/adapter-project',self.root,dirs_exist_ok=True)
        self.original=fixtures.LAB
        fixtures.LAB=self.root/'.game/gpos-runtime/tool-output/unity';fixtures.LAB.mkdir(parents=True)
        self.fixture=fixtures.B_BuildBoundary('test_hardlinked_payload_is_a_closed_refusal');self.fixture.setUp()
        result=self.fixture.classify();self.assertIn('BUILD_PUBLISHED',{d.code for d in result.diagnostics})
        self.build_id='build-'+self.fixture.work.name

    def tearDown(self):
        self.fixture.tearDown();fixtures.LAB=self.original;self.tmp.cleanup()

    def request(self,**kwargs):
        return ExecutionRequest('player',c.LAUNCH,Subject('PROJECT','synthetic-adapter-project'),
            project_root=str(self.root),actor=Actor('AGENT','production-regression'),allow_mutation=True,
            target_platform='WINDOWS',request_id='prod-negative',build_id=kwargs.pop('build_id',self.build_id),**kwargs)

    def refuse_before_spawn(self,request):
        with patch.object(windows.native,'spawn_supervisor',side_effect=AssertionError('invalid build spawned')):
            result=execute(default_registry(),request)
        self.assertNotEqual(result.status,'SUCCESS')
        self.assertFalse(result.mutation_performed)
        self.assertFalse(result.evidence_candidates)
        return result

    def test_invalid_build_id_and_missing_build_refused(self):
        for bid in ('../Player.exe','build-missing'):
            self.assertIn('PLAYER_BUILD_INVALID',{d.code for d in self.refuse_before_spawn(self.request(build_id=bid)).diagnostics})

    def test_old_manifest_refused_through_production(self):
        path=self.fixture.work/'build-manifest.json';value=json.loads(path.read_text());value['gpos_version']='1.0.0-alpha.28'
        path.write_text(json.dumps(value))
        self.assertIn('PLAYER_BUILD_INVALID',{d.code for d in self.refuse_before_spawn(self.request()).diagnostics})

    def test_tampered_payload_refused_through_production(self):
        (self.fixture.work/'payload/Player/Player_Data/level0').write_bytes(b'tampered')
        self.assertIn('PLAYER_BUILD_INVALID',{d.code for d in self.refuse_before_spawn(self.request()).diagnostics})

    def test_caller_process_surface_and_mutation_without_consent_refused(self):
        for request in (self.request(inputs={'executable':'foreign.exe'}),dataclasses.replace(self.request(),allow_mutation=False)):
            self.refuse_before_spawn(request)

if __name__=='__main__':unittest.main(verbosity=2)
