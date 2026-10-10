"""alpha.27 Windows Build Core qualification. All real Editors are sequential, Job-contained.

GPOS_WINDOWS_BUILD_REAL=1 enables the original alpha.27 real fixture groups.
The only lab used is GPOS_WINDOWS_BUILD_LAB (default the authorized alpha.27 qualification root).
Synthetic revisions are CALLER_SUPPLIED, never Git-verified. No Player is launched.
"""
import copy
import ctypes
import dataclasses
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
if sys.platform != 'win32' or not sys.flags.utf8_mode:
    raise unittest.SkipTest('Windows UTF-8 qualification only')
from gpos.framework import load_framework
from gpos.tools import process as proc
from gpos.tools import paths_win32 as pw
from gpos.tools.execution import ExecutionRequest, execute
from gpos.tools.model import Subject
from gpos.tools.registry import ToolRegistry
from gpos.tools.unity import adapter as ua, build as ub, build_win32 as wb, build_windows as dispatch
from gpos.tools.unity import bridge_install as bi, project as up
import posix_parity as pp
import test_unity_build as mac_build_tests
import test_unity_windows as host_tests

LAB = Path(os.environ.get('GPOS_WINDOWS_BUILD_LAB', r'D:\gpos-unity-lab-alpha27-qualification'))
REV = '0123456789abcdef0123456789abcdef01234567'
FW = load_framework()
REAL = os.environ.get('GPOS_WINDOWS_BUILD_REAL') == '1'

class C_BuildSources(mac_build_tests.E_Scans):
    def entry_code(self):
        return "\n".join(pp.macos_view(mac_build_tests.code_only(p)) for p in self.ENTRY_FILES)

    def test_windows_reader_has_only_the_fixed_readonly_reflection(self):
        text=(bi.SOURCE/'Editor/Build/BuildConfiguration.cs').read_text(encoding='utf-8')
        added=text[text.index('        static void WindowsOutput'):text.index('        static SceneFact')]
        self.assertEqual(added.count('GetMethod('),1)
        self.assertIn('GetActiveOrClassicBuildProfile',added)
        self.assertIn('BuildTarget.StandaloneWindows64, StandaloneBuildSubtarget.Player, null',added)
        for forbidden in ('SetActiveBuildProfile','CreateBuildProfile','ApplyModifiedProperties','EditorPrefs','SetPlatformSettings'):
            self.assertNotIn(forbidden,added)

    def test_bridge_16_history_and_all_earlier_pins(self):
        history=bi.history()
        self.assertEqual(history['1.6.0']['package_digest'],'883d1e31751ad158f0c3d82f90a1b18a173a54ad46799a39834929315e4a0400')
        self.assertEqual(bi.verify_source()['bridge_version'],'1.7.0')
        self.assertEqual(bi.PROTOCOL,'gpos.unity.live/5')
        self.assertEqual(set(history),{'1.0.0','1.1.0','1.2.0','1.3.0','1.4.0','1.5.0','1.6.0'})

class QualificationAdapter(ua.UnityAdapter):
    """TEST_ONLY availability, never registered by default_registry or exposed by production."""
    def probe(self):
        r = super().probe()
        return dataclasses.replace(r, capability_availability=tuple(
            (c, True, 'TEST_ONLY caller-supplied revision') if c in ub.CAPABILITY_IDS else (c, ok, why)
            for c, ok, why in r.capability_availability))

    def execute(self, request, context):
        if request.capability_id not in ub.CAPABILITY_IDS:
            return super().execute(request, context)
        project, summary = up.preflight(context.project_root, request.inputs.get('unity_project'))
        result = self._run_build(request.capability_id, request, context, Path(context.project_root), project, summary)
        if result.process is not None:
            self.observations.append({"request_id":request.request_id,"process":dataclasses.asdict(result.process)})
        return result

def pe(managed=False, dll=False, x64=True):
    data = bytearray(512)
    data[:2] = b'MZ'; struct.pack_into('<I', data, 60, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<HH', data, 68, 0x8664 if x64 else 0x14c, 1)
    struct.pack_into('<HH', data, 84, 240 if x64 else 224, 2 | (0x2000 if dll else 0))
    struct.pack_into('<H', data, 88, 0x20b if x64 else 0x10b)
    directory_start = 88+(112 if x64 else 96)
    struct.pack_into('<I',data,directory_start-4,16)
    table = 88+(240 if x64 else 224)
    struct.pack_into('<IIII',data,table+8,128,4096,128,384)
    struct.pack_into('<I',data,384,72)
    if managed:
        struct.pack_into('<II', data, 88+(112 if x64 else 96)+14*8, 4096, 72)
    return bytes(data)

def payload(root, guid='1'*32):
    content = {
        'Player.exe': pe(), 'UnityPlayer.dll': pe(dll=True),
        'MonoBleedingEdge/EmbedRuntime/mono-2.0-bdwgc.dll': pe(dll=True),
        'MonoBleedingEdge/EmbedRuntime/MonoPosixHelper.dll': pe(dll=True),
        'MonoBleedingEdge/etc/mono/config': b'config', 'MonoBleedingEdge/etc/mono/4.5/machine.config': b'config',
        'Player_Data/Managed/mscorlib.dll': pe(managed=True, dll=True, x64=False),
        'Player_Data/Managed/UnityEngine.CoreModule.dll': pe(managed=True, dll=True, x64=False),
        'Player_Data/boot.config': ('build-guid='+guid+'\n').encode(),
        'Player_Data/globalgamemanagers': b'data', 'Player_Data/level0': b'scene',
        'Player_Data/ScriptingAssemblies.json': b'{}'}
    for name, data in content.items():
        p = root/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    return content

class A_NtfsContract(unittest.TestCase):
    def setUp(self):
        wb.safe(LAB)
        self.work = LAB/('unit-'+uuid.uuid4().hex); self.work.mkdir()
        self.player = self.work/'staging'/'Player'
        self.content = payload(self.player)

    def tearDown(self):
        # Resolve and prove this test-created target is within the authorized lab before recursive removal.
        self.assertTrue(self.work.resolve().is_relative_to(LAB.resolve()))
        shutil.rmtree(self.work)

    def test_native_and_managed_pe(self):
        _, tree = wb.validate_player(self.player, '1'*32)
        self.assertEqual(tree['bytes'], sum(map(len, self.content.values())))
        self.assertEqual(wb.pe(pe(True, True, False), 512), {'managed': True, 'dll': True})
        with self.assertRaises(wb.PayloadProblem): wb.pe(pe(x64=False), 512)
        for data in (b'bad', pe()[:128]):
            with self.assertRaises(wb.PayloadProblem): wb.pe(data, len(data))

    def test_ads_files_directories_root_and_explicit_paths(self):
        for target in (self.player/'Player.exe', self.player/'Player_Data', self.player):
            Path(str(target)+':hidden').write_bytes(b'ADS')
            with self.assertRaises(wb.PayloadProblem): wb.validate_player(self.player, '1'*32)
            os.remove(str(target)+':hidden')
        with patch.object(pw, 'open_file_for_read', side_effect=AssertionError('raw reader reached')):
            with self.assertRaises(wb.PayloadProblem): wb.read_file(str(self.player/'Player.exe')+':hidden', 1024)

    def test_stream_errors_unsupported_ambiguity_and_close(self):
        with patch.object(wb, '_first', return_value=wb.INVALID_HANDLE):
            for error in (1, 5, 50, 87):
                ctypes.set_last_error(error)
                with self.assertRaises(wb.PayloadProblem): wb.streams(self.player, directory=True)
        def first(path, level, ptr, flags):
            ctypes.cast(ptr, ctypes.POINTER(wb.STREAM_DATA)).contents.name = '::$DATA'
            ctypes.cast(ptr, ctypes.POINTER(wb.STREAM_DATA)).contents.size = 512
            return 123
        with patch.object(wb, '_first', side_effect=first), patch.object(wb, '_close', return_value=True):
            with patch.object(wb, '_next', return_value=True):
                with self.assertRaisesRegex(wb.PayloadProblem, 'ambiguous'): wb.streams(self.player/'Player.exe')
            with patch.object(wb, '_next', return_value=False):
                ctypes.set_last_error(5)
                with self.assertRaisesRegex(wb.PayloadProblem, 'incomplete'): wb.streams(self.player/'Player.exe')
                ctypes.set_last_error(38)
                with patch.object(wb, '_close', return_value=False):
                    with self.assertRaisesRegex(wb.PayloadProblem, 'closed'): wb.streams(self.player/'Player.exe')

    def test_report_guid_structure_and_sidecars(self):
        report = [{'path': str(self.player/p), 'size': len(d)} for p,d in self.content.items()]
        wb.validate_player(self.player, '1'*32, report)
        for bad in (report[:-1], report+[report[0]], [{'path': str(self.work/'alien'), 'size': 1}]):
            with self.assertRaises((wb.PayloadProblem, pw.PathRefused)): wb.validate_player(self.player, '1'*32, bad)
        with self.assertRaises(wb.PayloadProblem): wb.validate_player(self.player, '2'*32)
        for name in ('Player.pdb','NVUnityPlugin.dll','Player_Data/Managed/extra.pdb'):
            sidecar=self.player/name; sidecar.write_bytes(pe(dll=True))
            with self.assertRaises(wb.PayloadProblem): wb.validate_player(self.player, '1'*32)
            sidecar.unlink()

    def test_hardlink_reparse_and_same_byte_replacement(self):
        p = self.player/'Player_Data'/'level0'
        before = wb.tree(self.player)
        alien = self.work/'replacement'; alien.write_bytes(p.read_bytes()); os.replace(alien, p)
        after = wb.tree(self.player)
        self.assertNotEqual(before['digest'], after['digest'])
        os.link(p, self.work/'alias')
        with self.assertRaises(pw.PathRefused): wb.tree(self.player)
        os.unlink(self.work/'alias')
        os.symlink(self.work, self.player/'junction', target_is_directory=True)
        with self.assertRaises(wb.PayloadProblem): wb.tree(self.player)
        os.unlink(self.player/'junction')

    def test_publication_collision_partial_and_manifest_last(self):
        final = self.work/'payload'; final.mkdir(); (final/'alien').write_bytes(b'keep')
        with self.assertRaises(OSError): wb.publish(self.work)
        self.assertEqual((final/'alien').read_bytes(), b'keep')
        os.unlink(final/'alien'); final.rmdir()
        wb.publish(self.work)
        self.assertFalse((self.work/ub.MANIFEST_NAME).exists())
        wb.write_manifest(self.work, {'schema': 'deliberately-incomplete'})
        self.assertIsNotNone(wb.revalidate(self.work)[1])
        with self.assertRaises((OSError, wb.PayloadProblem)): wb.write_manifest(self.work, {})

    def test_held_scanner_blocks_rename_and_bounded_retry(self):
        pins = pw.pin_chain(str(self.player))
        started = time.monotonic()
        try:
            with self.assertRaises(OSError): wb.publish(self.work)
        finally: pw.close_all(pins)
        self.assertLess(time.monotonic()-started, 1)
        self.assertTrue(self.player.exists()); self.assertFalse((self.work/'payload').exists())
        wb.publish(self.work)

    def test_lexical_names_bounds_and_duplicate_json(self):
        for name in ('CON', 'name.', 'Player.exe:ads'):
            with self.assertRaises(wb.PayloadProblem): wb.safe(self.player/name, False)
        with patch.object(wb, 'MAX_ENTRIES', 2):
            with self.assertRaises(wb.PayloadProblem): wb.tree(self.player)
        p = self.work/'bad.json'; p.write_bytes(b'{"a":1,"a":2}')
        with self.assertRaises(wb.PayloadProblem): wb.read_json(p)

class B_BuildBoundary(A_NtfsContract):
    def setUp(self):
        super().setUp()
        c = {'schema':wb.CONFIG_SCHEMA,'unity_version':'6000.6.4f1','active_target':wb.TARGET,
             'standalone_subtarget':'Player','scripting_backend':'Mono2x','application_identifier':'com.Gpos.Fixture',
             'development':False,'mode':'CLASSIC','profile':None,
             'scenes':[{'path':'Assets/Demo.unity','guid':'2'*32,'sha256':'ab'*32}],
             'files':{n:'ab'*32 for n in ('Packages/manifest.json','Packages/packages-lock.json',
                     'ProjectSettings/EditorBuildSettings.asset','ProjectSettings/ProjectSettings.asset')},
             'debug':{n:False for n in ('connect_profiler','allow_debugging','deep_profiling',
                     'wait_for_managed_debugger','code_coverage','wait_for_player_connection')},
             'output':{'architecture':'x64','readable':True,'create_solution':False,'copy_pdb':False,'install_in_build_folder':False}}
        token = ub.token_of(c)
        self.response = {'schema':wb.RESPONSE_SCHEMA,'operation':'BUILD','request_id':self.work.name,
            'unity_version':'6000.6.4f1','outcome':'BUILT','buildable':True,'problems':[],
            'configuration':c,'configuration_token':token,'refusal':None,
            'build':{'result':'Succeeded','guid':'1'*32,'platform':wb.TARGET,'output_path':str(self.player/'Player.exe'),
                'total_errors':0,'total_warnings':0,'total_size':sum(map(len,self.content.values())),'duration_ms':500,
                'development_observed':False,'options_text':'None','messages':[],'error_message_count':0,
                'files':[{'path':str(self.player/p),'size':len(d)} for p,d in self.content.items()]},
            'post':{'configuration_token':token,'active_target':wb.TARGET,'profile_path':None,'development':False}}
        self.request = ExecutionRequest('unity',ub.BUILD,Subject('FEATURE','FEATURE-0001'),project_root=str(self.work),
            inputs={'expected_configuration_token':token},request_id=self.work.name,build_revision=REV,target_platform='WINDOWS')
        self.context = types.SimpleNamespace(project_root=str(self.work), clock=types.SimpleNamespace(now=lambda:'2026-10-10T00:00:00Z'))
        (self.work/ub.STARTED_NAME).write_text(json.dumps({'schema':ub.STARTED_SCHEMA,'request_id':self.work.name,'build_id':ub.build_id(self.work.name),'utc':'2026-10-10T00:00:00Z'}),encoding='utf-8')

    def classify(self, outcome=None):
        (self.work/ub.RESPONSE_NAME).write_text(json.dumps(self.response),encoding='utf-8')
        return dispatch.classify(ua.UnityAdapter(),ub.BUILD,self.request,self.context,
            outcome or proc.ProcessOutcome(exit_code=0),{},self.work,{'unity_project':'Game','editor_version':'6000.6.4f1'})

    def test_positive_manifest_then_consumer_revalidation(self):
        r = self.classify()
        self.assertIn('BUILD_PUBLISHED',{d.code for d in r.diagnostics}, [(d.code,d.message) for d in r.diagnostics])
        manifest, error = wb.revalidate(self.work); self.assertIsNone(error)
        final = self.work/ub.MANIFEST_NAME
        for field,value in [('gpos_version','bad'),('build_revision_source','GIT_VERIFIED'),('build_id','alien'),
                            ('limitations',[]),('subject',{}),('unity_version','bad')]:
            changed=copy.deepcopy(manifest);changed[field]=value;final.write_text(json.dumps(changed),encoding='utf-8')
            self.assertIsNotNone(wb.revalidate(self.work)[1],field)
        for section,field,value in [('unity_build','total_size',1),('unity_build','duration_seconds',float('inf')),
                                    ('payload','entries',True),('payload','bytes',True)]:
            changed=copy.deepcopy(manifest);changed[section][field]=value
            if field == 'duration_seconds': changed['duration_seconds']=value
            final.write_text(json.dumps(changed),encoding='utf-8')
            self.assertIsNotNone(wb.revalidate(self.work)[1],(section,field))
        final.write_text(json.dumps(manifest),encoding='utf-8')
        self.assertIsNone(wb.revalidate(self.work)[1])

    def test_path_guid_and_postconfiguration_mismatch(self):
        for key,value in [('output_path',str(self.player)),('guid','0'*32),('platform','StandaloneOSX')]:
            old=self.response['build'][key];self.response['build'][key]=value
            self.assertIn('BUILD_OUTCOME_UNKNOWN',{d.code for d in self.classify().diagnostics})
            self.response['build'][key]=old
        self.response['post']['configuration_token']='0'*64
        self.assertIn('BUILD_OUTCOME_UNKNOWN',{d.code for d in self.classify().diagnostics})
        self.assertFalse((self.work/ub.MANIFEST_NAME).exists())

    def test_containment_and_capture_precede_publication(self):
        for field in ('tree_contained','capture_complete'):
            with patch.object(wb,'publish',side_effect=AssertionError('publication reached')):
                r=self.classify(proc.ProcessOutcome(exit_code=0,**{field:False}))
            self.assertIn('BUILD_OUTCOME_UNKNOWN',{d.code for d in r.diagnostics})

    def test_hardlinked_payload_is_a_closed_refusal(self):
        os.link(self.player/'Player_Data/level0',self.work/'alias')
        r=self.classify()
        self.assertIn('BUILD_PAYLOAD_INVALID',{d.code for d in r.diagnostics})
        self.assertFalse((self.work/ub.MANIFEST_NAME).exists())

    def test_missing_report_and_crash_are_unknown(self):
        self.response['build']=None
        self.assertIn('BUILD_OUTCOME_UNKNOWN',{d.code for d in self.classify().diagnostics})
        for outcome in (proc.ProcessOutcome(exit_code=3),proc.ProcessOutcome(timed_out=True)):
            self.assertIn('BUILD_OUTCOME_UNKNOWN',{d.code for d in self.classify(outcome).diagnostics})

    def test_publication_faults_and_revalidation_before_success(self):
        with patch.object(wb,'write_manifest',side_effect=OSError('crash after payload rename')):
            r=self.classify()
        self.assertIn('BUILD_OUTCOME_UNKNOWN',{d.code for d in r.diagnostics})
        self.assertTrue((self.work/'payload/Player').is_dir());self.assertFalse((self.work/ub.MANIFEST_NAME).exists())
        # An incomplete old invocation is refused, never adopted by a second run.
        self.assertTrue(ub.unfresh(self.work))

    def test_manifest_last_and_postpublication_revalidation(self):
        original = wb.write_manifest
        def publish_manifest(workspace,manifest):
            self.assertTrue((workspace/'payload/Player/Player.exe').exists())
            self.assertFalse((workspace/'staging').exists())
            self.assertFalse((workspace/ub.MANIFEST_NAME).exists())
            return original(workspace,manifest)
        with patch.object(wb,'write_manifest',side_effect=publish_manifest), patch.object(wb,'revalidate',return_value=(None,'injected consumer drift')):
            r=self.classify()
        self.assertIn('BUILD_OUTCOME_UNKNOWN',{d.code for d in r.diagnostics})
        self.assertNotIn('BUILD_PUBLISHED',{d.code for d in r.diagnostics})

    def test_production_capabilities_reach_build_dispatch(self):
        for cap in ub.CAPABILITY_IDS:
            with patch.object(ua.up,'preflight',return_value=(self.work/'Game',{})), \
                 patch.object(ua.UnityAdapter,'_run_build',return_value='production-build-dispatch') as dispatch:
                request=types.SimpleNamespace(capability_id=cap,inputs={'unity_project':'Game'})
                self.assertEqual(ua.UnityAdapter().execute(request,types.SimpleNamespace(project_root=str(self.work))),
                                 'production-build-dispatch')
                dispatch.assert_called_once()

@unittest.skipUnless(REAL, 'real Unity NOT_RUN unless GPOS_WINDOWS_BUILD_REAL=1')
class R_RealBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wb.safe(LAB)
        discovered = ua.UnityAdapter().discover()
        if len(discovered) != 1 or discovered[0][0] != '6000.6.4f1':
            raise RuntimeError('one exact qualified Editor required')
        cls.version, cls.editor = discovered[0]
        cls.work = LAB/('real-'+uuid.uuid4().hex[:8]); cls.work.mkdir()
        cls.before = {'prefs':host_tests.prefs_snapshot(), 'upm':host_tests.upm_configs(),
                      'licensing':host_tests.licensing_clients()}
        cls.p = cls.work/'p'; shutil.copytree(ROOT/'tests/fixtures/adapter-project', cls.p)
        game = cls.p/'Game'
        for sub in ('Assets/Editor', 'Packages', 'ProjectSettings'): (game/sub).mkdir(parents=True)
        (game/'Packages/manifest.json').write_text('{"dependencies":{"com.unity.modules.imgui":"1.0.0","com.unity.modules.jsonserialize":"1.0.0"}}', encoding='utf-8')
        (game/'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.6.4f1\n', encoding='utf-8')
        shutil.copyfile(ROOT/'tests/unity_windows_build_testkit/WindowsBuildFixture.cs', game/'Assets/Editor/WindowsBuildFixture.cs')
        bi.install(cls.p, game, bi.verify_source())
        cls.adapter = QualificationAdapter(); cls.adapter.observations = []
        cls.registry = ToolRegistry(FW); cls.registry.register(cls.adapter)
        cls.count = 0; cls.records = []
        cls.prepare()

    @classmethod
    def prepare(cls, method='Prepare'):
        workspace = cls.work/('fixture-'+uuid.uuid4().hex[:8]); workspace.mkdir()
        cache = cls.work/'upm-cache'; cache.mkdir(exist_ok=True)
        for name in (ua.UPM_USER_NAME, ua.UPM_GLOBAL_NAME): (workspace/name).write_bytes(b'')
        spec = proc.ToolProcessSpec(cls.editor, ('-batchmode','-projectPath',str(cls.p/'Game'),
            '-logFile',str(workspace/'editor.log'),'-upmLogFile',str(workspace/'upm.log'),
            '-cacheServerEnableDownload','false','-cacheServerEnableUpload','false',
            '-executeMethod','GposWindowsBuildFixture.'+method), cwd=str(workspace), timeout=180,
            env=ua.upm_environment(workspace, cache))
        result = proc.run_process(spec, scopes=[str(LAB), str(Path(cls.editor).parent)])
        cls.records.append({'fixture_method': method, 'workspace':str(workspace), 'process': dataclasses.asdict(result)})
        cls.save_records()
        if result.exit_code != 0 or not result.integrity_ok:
            raise AssertionError(f'fixture {method} failed: {result}; log {workspace}')

    @classmethod
    def save_records(cls):
        (cls.work/'job-outcomes.json').write_text(json.dumps(cls.adapter.observations,indent=2,default=str),encoding='utf-8')
        (cls.work/'qualification-processes.json').write_text(json.dumps(cls.records, indent=2, default=str), encoding='utf-8')

    def call(self, cap, token=None, timeout=180, revision=REV):
        type(self).count += 1
        rid = 'win-build-'+str(self.count)
        inputs = {'unity_project':'Game'}
        if token is not None: inputs['expected_configuration_token'] = token
        r = execute(self.registry, ExecutionRequest('unity',cap,Subject('FEATURE','FEATURE-0001'),
            project_root=str(self.p), inputs=inputs, allow_mutation=True, target_platform='WINDOWS',
            build_revision=revision if cap==ub.BUILD else None, request_id=rid, timeout=timeout))
        self.records.append({'capability':cap,'result':r.to_dict()}); self.save_records()
        print('\nREAL', cap, r.status, [(d.code,d.message) for d in r.diagnostics], flush=True)
        return r

    def test_01_inspect_and_positive(self):
        i = self.call(ub.INSPECT_BUILD)
        self.assertTrue(i.data.get('buildable'), i.to_dict())
        b = self.call(ub.BUILD, i.data['configuration_token'])
        self.assertIn('BUILD_PUBLISHED', {d.code for d in b.diagnostics}, b.to_dict())
        workspace = self.p/'.game/gpos-runtime/tool-output/unity'/b.request_id
        manifest, error = wb.revalidate(workspace); self.assertIsNone(error)
        self.assertEqual(manifest['build_revision_source'], 'CALLER_SUPPLIED')
        self.assertNotIn('git_verified', manifest)
        type(self).published = workspace
        (self.work/'positive-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    def test_02_preconfiguration_drift(self):
        r = self.call(ub.BUILD, '0'*64)
        self.assertIn('BUILD_CONFIGURATION_CHANGED', {d.code for d in r.diagnostics})

    def test_03_il2cpp_profile_and_target_refusal(self):
        for method, rule in (('Il2cpp','BACKEND_NOT_MONO'),('CustomProfile','WINDOWS_PROFILE_UNSUPPORTED'),
                             ('WrongTarget','TARGET_NOT_ACTIVE')):
            self.prepare(method)
            i = self.call(ub.INSPECT_BUILD)
            self.assertIn(rule, [p['rule'] for p in i.data.get('problems',[])], i.to_dict())
            r = self.call(ub.BUILD, i.data['configuration_token'])
            self.assertNotIn('BUILD_PUBLISHED', {d.code for d in r.diagnostics})
            self.prepare('Restore')

    def test_04_compile_failure(self):
        bad = self.p/'Game/Assets/Broken.cs'; bad.write_text('public class Broken { int x = ; }', encoding='utf-8')
        try:
            r = self.call(ub.INSPECT_BUILD)
            self.assertIn('BUILD_COMPILE_FAILED', {d.code for d in r.diagnostics}, r.to_dict())
        finally: bad.unlink()

    def test_05_buildreport_failure_and_postdrift(self):
        for mode in ('fail', 'drift'):
            marker = self.p/'Game/fixture-mode.txt'; marker.write_text(mode, encoding='utf-8')
            try:
                i = self.call(ub.INSPECT_BUILD); self.assertTrue(i.data.get('buildable'),i.to_dict())
                r = self.call(ub.BUILD, i.data['configuration_token'])
                expected = 'BUILD_FAILED' if mode=='fail' else 'BUILD_OUTCOME_UNKNOWN'
                self.assertIn(expected, {d.code for d in r.diagnostics}, r.to_dict())
            finally:
                marker.unlink(); self.prepare('Restore')

    def test_06_timeout_after_started(self):
        marker = self.p/'Game/fixture-mode.txt'; marker.write_text('hang', encoding='utf-8')
        try:
            i = self.call(ub.INSPECT_BUILD)
            r = self.call(ub.BUILD, i.data['configuration_token'], timeout=35)
            self.assertIn('BUILD_OUTCOME_UNKNOWN', {d.code for d in r.diagnostics}, r.to_dict())
        finally: marker.unlink()

    def test_07_real_revalidation_drift(self):
        workspace = getattr(type(self), 'published', None)
        self.assertIsNotNone(workspace, 'positive baseline required')
        p = workspace/'payload/Player/Player_Data/level0'
        old = p.read_bytes(); p.write_bytes(old+b'changed')
        self.assertIsNotNone(wb.revalidate(workspace)[1]); p.write_bytes(old)
        added = p.parent/'added'; added.write_bytes(b'extra')
        self.assertIsNotNone(wb.revalidate(workspace)[1]); added.unlink()
        p.rename(p.with_name('missing'))
        self.assertIsNotNone(wb.revalidate(workspace)[1]); p.with_name('missing').rename(p)
        replacement = p.parent/'same-byte-replacement'; replacement.write_bytes(old); os.replace(replacement,p)
        self.assertIsNotNone(wb.revalidate(workspace)[1])

    def test_08_aborted_editor_after_started(self):
        marker=self.p/'Game/fixture-mode.txt'; marker.write_text('exit',encoding='utf-8')
        try:
            i=self.call(ub.INSPECT_BUILD)
            r=self.call(ub.BUILD,i.data['configuration_token'])
            self.assertIn('BUILD_OUTCOME_UNKNOWN',{d.code for d in r.diagnostics},r.to_dict())
            self.assertEqual(r.exit_code,91,r.to_dict())
            self.assertTrue(self.adapter.observations[-1]['process']['tree_contained'])
            self.assertTrue(self.adapter.observations[-1]['process']['capture_complete'])
        finally: marker.unlink()

    @classmethod
    def tearDownClass(cls):
        cls.save_records()
        proof = ua.UnityAdapter()._lock_proof(cls.p/'Game', cls.editor)
        (cls.work/'cleanup-proof.json').write_text(json.dumps(proof.details(),indent=2,default=str),encoding='utf-8')
        if proof.state not in ua.pl.PROCEED:
            raise AssertionError('fixture Editor cleanup not proven: '+proof.state)
        after={'prefs':host_tests.prefs_snapshot(), 'upm':host_tests.upm_configs(),
               'licensing':host_tests.licensing_clients()}
        (cls.work/'host-side-effects.json').write_text(json.dumps({'before':cls.before,'after':after,
            'limits':'Registry/process/config snapshots, not a machine-wide filesystem journal'},indent=2,default=str),encoding='utf-8')
        if after['upm'] != cls.before['upm'] or after['licensing'] != cls.before['licensing']:
            raise AssertionError('shared UPM or licensing identity changed')
        print('\nQualification evidence retained:', cls.work, flush=True)

if __name__ == '__main__':
    unittest.main(verbosity=2)
