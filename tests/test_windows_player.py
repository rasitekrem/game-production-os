"""Focused Windows Player candidate guards. Synthetic outcomes are never real Player evidence."""
import copy
import ctypes
import dataclasses
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parent.parent), str(Path(__file__).resolve().parent)]
from gpos.framework import load_framework
from gpos.tools import execution as ex, process, player_process_win32 as native
from gpos.tools.registry import default_registry, ToolRegistry
from gpos.tools.player import windows as win, windows_records as records, windows_resolver as resolver, contract as c
import test_unity_windows_build as fixtures


class WindowsRuntimeGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gpos-player-unit-')
        self.root = Path(self.tmp.name).resolve()
        self.adapter = win.WindowsPlayerAdapter()

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_production_lifecycle_and_inventory_fixed(self):
        registry = default_registry(load_framework())
        self.assertEqual(len(registry.adapter_ids()),7)
        self.assertFalse(registry.allow_test_only)
        self.assertEqual(registry.probe('player').status,'AVAILABLE')
        self.assertEqual(registry.get('player').descriptor.supported_platforms,('MACOS','WINDOWS'))
        self.assertEqual({cid for cid,available,_ in registry.probe('player').capability_availability if available},
                         {c.LAUNCH,c.STATUS,c.STOP})
        self.assertEqual(sum(a for _,a,_ in registry.probe('unity').capability_availability),14)
        self.assertEqual(sum(a for _,a,_ in registry.probe('git').capability_availability),2)
        self.assertEqual(registry.probe('adb').status,'UNAVAILABLE')

    def test_candidate_is_test_only_and_has_no_capture(self):
        registry=ToolRegistry(load_framework(),allow_test_only=True);registry.register(self.adapter)
        self.assertTrue(self.adapter.descriptor.test_only)
        self.assertEqual({c.id for c in self.adapter.descriptor.capabilities},{c.LAUNCH,c.STATUS,c.STOP})
        self.assertEqual(registry.probe('player').status,'AVAILABLE')
        with self.assertRaises(Exception):default_registry().register(self.adapter)

    def test_general_detached_runner_stays_refused(self):
        with self.assertRaises(process.ProcessSpecError) as refused:
            process.spawn_detached(process.DetachedProcessSpec(sys.executable,cwd=str(self.root)),[str(self.root)])
        self.assertEqual(refused.exception.code,'PLATFORM_UNSUPPORTED')

    def test_player_context_gate_rejects_missing_lease_dry_run_and_wrong_capability(self):
        cap=win.CAPABILITIES[0]
        request=types.SimpleNamespace(adapter_id='player',build_id='build-one',request_id='one')
        base=dict(request=request,capability=cap,project_root=str(self.root),workspace=str(self.root),scopes=(str(self.root),),clock=None,probe=None,dry_run=False,timeout=30,detached_allowed=True)
        for changes in ({},{'dry_run':True},{'detached_allowed':False},{'capability':win.CAPABILITIES[1]}):
            ctx=ex.ExecutionContext(**(base|changes))
            with self.assertRaises(AssertionError):ctx.spawn_windows_player_supervisor()
        opened=types.SimpleNamespace(session={'build_id':'build-one','launch_request_id':'one'})
        control=types.SimpleNamespace(opened=opened,confirm=lambda:None)
        ctx=ex.ExecutionContext(**base,sessions=control)
        with patch.object(process,'spawn_windows_player_supervisor',return_value={'pid':1}) as call:
            self.assertEqual(ctx.spawn_windows_player_supervisor(),{'pid':1})
            with self.assertRaises(AssertionError):ctx.spawn_windows_player_supervisor()
            self.assertEqual(call.call_count,1)

    def test_real_current_process_creation_time_mismatch_is_not_adopted(self):
        facts=native.facts(os.getpid());self.assertTrue(facts['same_user'])
        self.assertEqual(native.identity(facts),'PROVEN')
        facts['created_filetime']+=1
        with patch.object(native.batch._k32,'TerminateProcess',side_effect=AssertionError('foreign signal')):
            self.assertEqual(native.identity(facts),'NOT_THIS_PROCESS')

    def test_missing_and_arbitrary_builds_refused_before_process(self):
        for build_id in (None,'../Player.exe','build-no-such-build','C:\\foreign.exe'):
            with self.assertRaises((ValueError,OSError,native.fs.PathRefused)):
                resolver.resolve(self.root,build_id)

    def test_record_write_once_and_oversize_and_duplicate_keys(self):
        path=self.root/'one.json';records.write_once(path,{'one':1})
        self.assertEqual(records.read(path),{'one':1})
        with self.assertRaises(OSError):records.write_once(path,{'one':2})
        self.assertEqual(records.read(path),{'one':1})
        with self.assertRaises(ValueError):records.write_once(self.root/'large.json',{'large':'x'*9000})
        (self.root/'duplicate.json').write_text('{"one":1,"one":2}',encoding='utf-8')
        with self.assertRaises(ValueError):records.read(self.root/'duplicate.json')

    def test_record_named_stream_refused(self):
        path=self.root/'ads.json';records.write_once(path,{'one':1})
        Path(str(path)+':hidden').write_bytes(b'unknown')
        with self.assertRaises(ValueError):records.read(path)

    def test_missing_control_is_absent_not_a_supervisor_crash(self):
        self.assertFalse(records.control(self.root/'not-yet-created.json','commit',{'session_id':'1'*32,'nonce':'2'*32}))
        with self.assertRaises(FileNotFoundError):records.read(self.root/'missing.json')

    def test_control_nonce_and_schema_and_extra_keys_do_not_signal(self):
        session={'session_id':'1'*32,'nonce':'2'*32};path=self.root/'stop.json'
        for value in ({'schema':'stop','session_id':'1'*32,'nonce':'3'*32},
                      {'schema':'stop','session_id':'1'*32,'nonce':'2'*32,'exe':'foreign'}):
            path.write_text(json.dumps(value),encoding='utf-8');self.assertFalse(records.control(path,'stop',session))

    def test_job_name_is_bound_to_nonce_not_caller_text(self):
        for session in ({'session_id':'../foreign','nonce':'1'*32},{'session_id':'1'*32,'nonce':''}):
            with self.assertRaises(ValueError):native.job_name(session)
        self.assertNotEqual(native.job_name({'session_id':'1'*32,'nonce':'2'*32}),native.job_name({'session_id':'1'*32,'nonce':'3'*32}))

    def test_graceful_targets_only_retained_child_group_not_zero(self):
        child=native.OwnedPlayer.__new__(native.OwnedPlayer);child.pid=12345
        with patch.object(child,'exited',return_value=None),patch.object(native._k32,'GenerateConsoleCtrlEvent',return_value=True) as send:
            self.assertTrue(child.graceful());send.assert_called_once_with(1,12345)
        with patch.object(child,'exited',return_value=0),patch.object(native._k32,'GenerateConsoleCtrlEvent',side_effect=AssertionError('exited process signalled')):
            self.assertFalse(child.graceful())

    def test_failed_stop_keeps_session(self):
        data={'identity':'UNPROVEN','job_present':True,'build_trust':'VALID','drift':None};session={'session_id':'1'*32,'nonce':'2'*32,'executable':'missing','runtime_absolute':str(self.root)}
        request=types.SimpleNamespace(actor=types.SimpleNamespace(kind='AGENT',id='own'),inputs={},capability_id=c.STOP)
        control=types.SimpleNamespace(close=lambda sid:(_ for _ in ()).throw(AssertionError('uncertain lease released')))
        context=types.SimpleNamespace(session={'owner_id':'AGENT:own'},sessions=control,dry_run=False)
        with patch.object(self.adapter,'restated'),patch.object(self.adapter,'observe',return_value=(data,session,None)),patch.object(native,'foreign',return_value=[]):
            result=self.adapter.stop(request,context)
        self.assertEqual(result.diagnostics[0].code,'PLAYER_STOP_OUTCOME_UNKNOWN')
        self.assertFalse(result.mutation_performed)

    def test_foreign_owner_cannot_stop_running_player(self):
        request=types.SimpleNamespace(actor=types.SimpleNamespace(kind='AGENT',id='foreign'),inputs={c.RECOVER_INPUT:True})
        context=types.SimpleNamespace(session={'owner_id':'AGENT:own'},dry_run=False)
        with patch.object(self.adapter,'restated'),patch.object(self.adapter,'observe',return_value=({'identity':'PROVEN','build_trust':'VALID','drift':None}, {'runtime_absolute':str(self.root)}, {})):
            result=self.adapter.stop(request,context)
        self.assertEqual(result.diagnostics[0].code,'LIVE_SESSION_MISMATCH')

    def test_identity_parent_mismatch_refuses_job_signal(self):
        binding={'player':{'executable':'intended','parent_pid':77,'created_filetime':2},'supervisor':{'pid':88,'created_filetime':1}}
        with patch.object(native,'open_job',side_effect=AssertionError('wrong relationship reached Job')):
            with self.assertRaises(ValueError):self.adapter.prove({'executable':'intended'},binding)

    def test_parent_job_without_breakaway_refuses_before_creation(self):
        def in_job(process,job,out):ctypes.cast(out,ctypes.POINTER(native.wintypes.BOOL))[0]=True;return True
        def limits(job,kind,out,size,ret):ctypes.cast(out,ctypes.POINTER(native.batch.JOBOBJECT_EXTENDED_LIMIT_INFORMATION))[0].BasicLimitInformation.LimitFlags=0x2400;return True
        with patch.object(native.batch._k32,'IsProcessInJob',side_effect=in_job),patch.object(native.batch._k32,'QueryInformationJobObject',side_effect=limits):
            with self.assertRaises(ValueError):native.parent_permission()

    def test_real_batch_job_cannot_use_player_breakaway(self):
        spec=process.ToolProcessSpec(sys.executable,('-I','-B','-X','utf8',str(Path(__file__).with_name('windows_player_boundary_probe.py'))),
            cwd=str(self.root),timeout=15)
        result=process.run_process(spec,[str(self.root)])
        self.assertTrue(result.integrity_ok)
        self.assertEqual(result.exit_code,0)
        self.assertIn('REFUSED_IN_BATCH_JOB',result.stdout)

    def test_real_process_wrong_job_is_unproven(self):
        job=native.batch._k32.CreateJobObjectW(None,None)
        self.assertTrue(job)
        try:self.assertEqual(native.identity(native.facts(os.getpid()),job),'UNPROVEN')
        finally:native.batch._close(job)

    def test_restatement_cannot_change_build_or_revision(self):
        ctx=types.SimpleNamespace(session={'session':{'build_id':'build-one','build_revision':'recorded'}})
        for build,revision in [('build-two',None),('build-one','unverified'),(None,'unverified')]:
            with self.assertRaises(ValueError):self.adapter.restated(types.SimpleNamespace(build_id=build,build_revision=revision),ctx)
        self.adapter.restated(types.SimpleNamespace(build_id=None,build_revision=None),ctx)

    def stop_context(self, identity='GONE', *, binding=None, launcher=None, drift=False):
        data={'identity':identity,'job_present':False,'build_trust':'DRIFT' if drift else 'VALID','drift':'changed' if drift else None,'exit':None}
        session={'session_id':'1'*32,'nonce':'2'*32,'executable':'absent','runtime_absolute':str(self.root),
                 'launcher':launcher or native.facts(os.getpid())}
        request=types.SimpleNamespace(actor=types.SimpleNamespace(kind='AGENT',id='own'),inputs={},build_id=None,build_revision=None)
        close=unittest.mock.Mock(return_value=(None,()))
        context=types.SimpleNamespace(session={'owner_id':'AGENT:own'},sessions=types.SimpleNamespace(close=close),dry_run=False,workspace=str(self.root))
        return data,session,request,context,close

    def test_unbound_recovery_cannot_overtake_live_launcher(self):
        data,session,request,ctx,close=self.stop_context()
        request.inputs={c.RECOVER_INPUT:True}
        with patch.object(self.adapter,'restated'),patch.object(self.adapter,'observe',return_value=(data,session,None)),patch.object(native,'foreign',return_value=[]):
            result=self.adapter.stop(request,ctx)
        self.assertEqual(result.diagnostics[0].code,'PLAYER_STOP_OUTCOME_UNKNOWN');close.assert_not_called()

    def test_unbound_recovery_must_be_explicit_even_when_launcher_gone(self):
        data,session,request,ctx,close=self.stop_context()
        with patch.object(self.adapter,'restated'),patch.object(self.adapter,'observe',return_value=(data,session,None)),patch.object(native,'foreign',return_value=[]),patch.object(native,'identity',return_value='GONE'):
            result=self.adapter.stop(request,ctx)
        self.assertEqual(result.diagnostics[0].code,'PLAYER_STOP_OUTCOME_UNKNOWN');close.assert_not_called()

    def test_explicit_unbound_recovery_requires_gone_launcher_and_job(self):
        data,session,request,ctx,close=self.stop_context();request.inputs={c.RECOVER_INPUT:True}
        with patch.object(self.adapter,'restated'),patch.object(self.adapter,'observe',return_value=(data,session,None)),patch.object(native,'foreign',return_value=[]),patch.object(native,'identity',return_value='GONE'):
            result=self.adapter.stop(request,ctx)
        close.assert_called_once_with(session['session_id']);self.assertEqual(result.data['classification'],'GONE_UNOBSERVED')

    def test_dry_run_uncertain_stop_neither_signals_nor_releases(self):
        data,session,request,ctx,close=self.stop_context('UNPROVEN');ctx.dry_run=True
        with patch.object(self.adapter,'restated'),patch.object(self.adapter,'observe',return_value=(data,session,None)),patch.object(native,'foreign',side_effect=AssertionError('dry run reached recovery')):
            result=self.adapter.stop(request,ctx)
        self.assertFalse(result.mutation_performed);close.assert_not_called()

    def test_build_drift_does_not_prevent_owned_stop(self):
        data,session,request,ctx,close=self.stop_context('PROVEN',drift=True)
        gone=dict(data,identity='GONE');binding={'player':{'pid':1}}
        with patch.object(self.adapter,'restated'),patch.object(self.adapter,'observe',side_effect=[(data,session,binding),(gone,session,binding)]),patch.object(native,'foreign',return_value=[]):
            result=self.adapter.stop(request,ctx)
        close.assert_called_once();self.assertEqual(result.data['build_trust'],'DRIFT')

    def test_foreign_payload_blocks_launch_before_lease(self):
        request=types.SimpleNamespace(inputs={},build_id='build-one',build_revision=None)
        ctx=types.SimpleNamespace(project_root=str(self.root),workspace=str(self.root),dry_run=False)
        with patch.object(resolver,'resolve',return_value={'executable':'owned'}),patch.object(native,'foreign',return_value=[123]):
            result=self.adapter.launch(request,ctx)
        self.assertEqual(result.diagnostics[0].code,'PLAYER_RUNTIME_CONFLICT')

    def test_native_creation_failure_restores_inheritance_and_closes_nul(self):
        real_open=os.open;real_close=os.close;opened=[];closed=[]
        def opening(*args):fd=real_open(*args);opened.append(fd);return fd
        def closing(fd):closed.append(fd);return real_close(fd)
        with patch.object(os,'open',side_effect=opening),patch.object(os,'close',side_effect=closing),patch.object(native.batch._k32,'CreateProcessW',return_value=False),patch.object(native._k32,'SetHandleInformation',return_value=True) as inherit:
            with self.assertRaises(native.LifecycleRefused):native._create(sys.executable,[sys.executable],self.root,123,inherit_job=True,environment={})
        self.assertEqual(opened,closed)
        self.assertEqual(inherit.call_args_list[-1].args,(123,1,0))

    def test_unknown_job_open_never_counts_as_gone(self):
        with patch.object(native._k32,'OpenJobObjectW',return_value=None),patch.object(ctypes,'get_last_error',return_value=5):
            with self.assertRaises(native.LifecycleRefused):native.open_job({'session_id':'1'*32,'nonce':'2'*32})

    def test_unproven_player_without_job_still_keeps_binding_and_lease(self):
        data,session,request,ctx,close=self.stop_context('UNPROVEN')
        with patch.object(self.adapter,'restated'),patch.object(self.adapter,'observe',return_value=(data,session,{'player':{}})),patch.object(native,'foreign',return_value=[]):
            result=self.adapter.stop(request,ctx)
        self.assertEqual(result.diagnostics[0].code,'PLAYER_STOP_OUTCOME_UNKNOWN');close.assert_not_called()

    def test_unknown_process_user_does_not_prove_ownership(self):
        fake=types.SimpleNamespace(created=lambda:1,image=lambda:'image',same_user=lambda:False,running=lambda:True,close=lambda:None)
        with patch.object(native.host,'open_process',return_value=(fake,None)):
            self.assertEqual(native.identity({'pid':1,'created_filetime':1,'executable':'image'}),'UNPROVEN')

    def test_atomic_membership_failure_terminates_only_directly_held_creation(self):
        def created(exe,argv,a,b,inherit,flags,env,cwd,start,out):
            pi=ctypes.cast(out,ctypes.POINTER(native.batch.PROCESS_INFORMATION))[0]
            pi.hProcess=123;pi.hThread=124;pi.dwProcessId=125;return True
        with patch.object(native.batch._k32,'CreateProcessW',side_effect=created),patch.object(native._k32,'SetHandleInformation',return_value=True),patch.object(native.batch,'_in_job',return_value=False),patch.object(native.batch._k32,'TerminateProcess',return_value=True) as terminate,patch.object(native.batch,'_close') as close:
            with self.assertRaises(native.LifecycleRefused):native._create(sys.executable,[sys.executable],self.root,999,flags=4,environment={})
        terminate.assert_called_once_with(123,native.EXIT_FORCED)
        self.assertEqual({call.args[0] for call in close.call_args_list},{123,124})

    def test_attribute_list_failure_does_not_leak_nul_handle(self):
        real_open=os.open;real_close=os.close;opened=[];closed=[]
        def opening(*args):fd=real_open(*args);opened.append(fd);return fd
        def closing(fd):closed.append(fd);return real_close(fd)
        with patch.object(os,'open',side_effect=opening),patch.object(os,'close',side_effect=closing),patch.object(native.batch,'_attribute_list',side_effect=OSError('TEST_ONLY Job attribute failure')):
            with self.assertRaises(OSError):native._create(sys.executable,[sys.executable],self.root,999,environment={})
        self.assertEqual(opened,closed)

    def test_missing_handshake_aborts_and_preserves_session(self):
        self.launch_confirmation_fault('missing')

    def test_failed_commit_aborts_and_preserves_session(self):
        self.launch_confirmation_fault('commit')

    def launch_confirmation_fault(self, mode):
        target={'executable':'owned','build_revision':'r'}
        control=types.SimpleNamespace(open=lambda session:(None,()))
        ctx=types.SimpleNamespace(project_root=str(self.root),workspace=str(self.root),dry_run=False,sessions=control,timeout=0,
            spawn_windows_player_supervisor=lambda:{'pid':1,'identity':{'pid':1},'command':{},'environment':{}})
        request=types.SimpleNamespace(inputs={},build_id='build-one',build_revision='r',request_id='one')
        handshake={'supervisor':{'pid':1},'player':{},'job_name':'job','helper_digest':'digest'}
        writes=[]
        def write(path,value):
            writes.append(path.name)
            if mode=='commit' and path.name=='commit.json':raise OSError('qualification commit failure')
        with patch.object(resolver,'resolve',return_value=target),patch.object(native,'foreign',return_value=[]),patch.object(native,'helper_digest',return_value='digest'),patch.object(records,'write_once',side_effect=write),patch.object(records,'bound',side_effect=FileNotFoundError if mode=='missing' else None,return_value=handshake),patch.object(native,'job_name',return_value='job'),patch.object(self.adapter,'prove',return_value=True),patch.object(resolver,'revalidates',return_value=('VALID',None)):
            result=self.adapter.launch(request,ctx)
        self.assertIn('abort.json',writes);self.assertTrue(result.mutation_performed)
        self.assertEqual(result.diagnostics[0].code,'PLAYER_LAUNCH_UNRESOLVED')


class WindowsBuildConsumer(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='gpos-player-consumer-');self.root=Path(self.tmp.name).resolve()
        self.original=fixtures.LAB;fixtures.LAB=self.root/'.game/gpos-runtime/tool-output/unity';fixtures.LAB.mkdir(parents=True)
        self.fixture=fixtures.B_BuildBoundary('test_hardlinked_payload_is_a_closed_refusal');self.fixture.setUp()
        result=self.fixture.classify();self.assertIn('BUILD_PUBLISHED',{d.code for d in result.diagnostics})
        self.build_id='build-'+self.fixture.work.name

    def tearDown(self):
        self.fixture.tearDown();fixtures.LAB=self.original;self.tmp.cleanup()

    def test_full_current_manifest_resolves_exact_executable_and_attribution(self):
        target=resolver.resolve(self.root,self.build_id)
        self.assertEqual(target['executable'],str(self.fixture.work/'payload/Player/Player.exe'))
        self.assertEqual(target['build_revision_source'],'CALLER_SUPPLIED')

    def test_tampering_and_same_byte_replacement_refused(self):
        player=self.fixture.work/'payload/Player/Player.exe';original=player.read_bytes()
        player.write_bytes(original+b'altered')
        with self.assertRaises(ValueError):resolver.resolve(self.root,self.build_id)
        player.unlink();player.write_bytes(original)
        with self.assertRaises(ValueError):resolver.resolve(self.root,self.build_id)

    def test_old_version_and_limitations_refused(self):
        path=self.fixture.work/'build-manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
        for field,value in (('gpos_version','1.0.0-alpha.27'),('limitations',['historical'])):
            changed=copy.deepcopy(manifest);changed[field]=value;path.write_text(json.dumps(changed),encoding='utf-8')
            with self.assertRaises(ValueError):resolver.resolve(self.root,self.build_id)

    def test_status_build_drift_does_not_reclassify_revision(self):
        target=resolver.resolve(self.root,self.build_id);(self.fixture.work/'payload/Player/Player_Data/level0').write_bytes(b'drift')
        self.assertEqual(resolver.revalidates(self.root,target)[0],'DRIFT')


if __name__=='__main__':unittest.main(verbosity=2)
