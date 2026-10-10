"""Fixed Windows lifecycle backend; the production Player adapter owns discovery and dispatch.

The legacy TEST_ONLY wrapper remains for attributable candidate regression tests only.
"""
import dataclasses
import os
import time
import uuid
from pathlib import Path
from .. import diagnostics as dg, leases, model, player_process_win32 as native
from ..execution import AdapterOutcome
from ..artifacts import ArtifactSpec
from . import adapter as mac, contract as c, windows_records as records, windows_resolver as resolver

CAPABILITIES = tuple(dataclasses.replace(cap, potential_evidence=(),
    description={c.LAUNCH:'Revalidate a Windows manifest/2, open SESSION and confirm one fixed Job-contained supervisor and Player.',
                 c.STATUS:'Read current kernel identity, Job membership, immutable launch binding, exit observation and build drift; no capture or log evidence.',
                 c.STOP:'Request bounded console shutdown of the proven owned Player, then terminate its retained handle if needed; release only a proven gone runtime.'}[cap.id],
    artifact_kinds=('JSON',) if cap.id != c.STATUS else (),
    notes=('Windows qualification candidate only; no helper installation, window or capture operation.',
           'SESSION ownership and build attribution are preserved; unproven lifecycle outcomes retain SESSION.'),
    side_effect_scope=('NONE' if cap.id == c.STATUS else 'Project-bound Windows Player session, runtime records and owned process lifecycle; game code may write its own user state.'))
    for cap in mac.CAPABILITIES if cap.id in (c.LAUNCH, c.STATUS, c.STOP))
DESCRIPTOR = dataclasses.replace(mac.DESCRIPTOR, supported_platforms=('WINDOWS',), capabilities=CAPABILITIES,
    adapter_kind='TEST_ONLY', target_tool='GPOS Windows Player Supervisor',
    availability='Phase 2C-9.5A qualification candidate only; production Windows Player gate closed',
    compatibility_notes=('Windows manifest/2 only, exact current version and limits; no capture or gameplay evidence.',))


def outcome(code, message, cap, data=None, mutation=False, artifacts=(), command=None, environment=None):
    return AdapterOutcome(ok=True, diagnostics=(dg.make(code, message, 'player', cap),), data=data,
                          mutation_performed=mutation, artifacts=artifacts, command=command, environment=environment)


class WindowsLifecycle:
    """Backend only: no registry identity or descriptor substitution."""

    def __init__(self, *, _qualification_hook=None):
        # TEST_ONLY fault injection, never selected by a request or production registry.
        self._qualification_hook = _qualification_hook

    def _checkpoint(self, stage, session):
        if self._qualification_hook:
            self._qualification_hook(stage, session)

    def restated(self, request, context):
        session = context.session['session']
        if request.build_id is not None and request.build_id != session['build_id']:
            raise ValueError('build_id differs from the SESSION build')
        if request.build_revision is not None and request.build_revision != session['build_revision']:
            raise ValueError('build_revision differs from the SESSION attribution')

    def probe(self):
        if model.current_platform() != 'WINDOWS':
            return model.ProbeResult('player', model.UNAVAILABLE, platform=model.current_platform())
        digest = native.helper_digest()
        return model.ProbeResult('player', model.AVAILABLE, platform='WINDOWS', tool_path=str(native.HELPER),
            tool_version='windows-core-candidate+'+digest[:12],
            capability_availability=tuple((cap.id, True, '') for cap in CAPABILITIES))

    def execute(self, request, context):
        try:
            if request.device is not None or request.target_platform not in (None, 'WINDOWS'):
                return outcome('INVALID_TOOL_REQUEST', 'Windows host only; no device input', request.capability_id)
            return {c.LAUNCH:self.launch, c.STATUS:self.status, c.STOP:self.stop}[request.capability_id](request, context)
        except (OSError, ValueError, KeyError, TypeError, native.fs.PathRefused) as exc:
            # If any handoff happened, its immutable SESSION remains; no automatic retry or adoption.
            if context.sessions and context.sessions.opened:
                context.sessions.confirm()
                return outcome('PLAYER_LAUNCH_UNRESOLVED', str(exc), request.capability_id,
                               {'session_id':context.sessions.opened.session['session_id'],'phase':c.LAUNCHING_UNRESOLVED}, True)
            return outcome('PLAYER_STOP_OUTCOME_UNKNOWN' if request.capability_id == c.STOP else 'PLAYER_IDENTITY_UNPROVEN',
                           str(exc), request.capability_id)

    def launch(self, request, context):
        root = Path(context.project_root);directory = Path(context.workspace)
        if request.inputs:
            return outcome('INVALID_TOOL_REQUEST', 'launch accepts only a canonical build_id, no process inputs', c.LAUNCH)
        try:
            target = resolver.resolve(root, request.build_id)
        except (OSError, ValueError, native.fs.PathRefused) as exc:
            return outcome('PLAYER_BUILD_INVALID', str(exc), c.LAUNCH)
        if request.build_revision is not None and request.build_revision != target['build_revision']:
            return outcome('INVALID_TOOL_REQUEST', 'build revision differs from CALLER_SUPPLIED manifest', c.LAUNCH)
        if native.foreign(target['executable']):
            return outcome('PLAYER_RUNTIME_CONFLICT', 'another instance of this payload exists; none adopted or signalled', c.LAUNCH)
        if context.dry_run:
            return AdapterOutcome(plan=('Revalidate manifest/2; acquire SESSION; fixed supervisor owns one kill-on-close Job and Player.',))
        if list(directory.iterdir()):
            return outcome('PLAYER_RUNTIME_CONFLICT', 'launch workspace is not fresh; no retry/adoption', c.LAUNCH)
        session = {**target, 'schema':'gpos.player.windows-session/1', 'session_id':uuid.uuid4().hex,
            'nonce':uuid.uuid4().hex, 'phase':'LAUNCHING', 'launch_request_id':request.request_id,
            'project_root':str(root), 'runtime_absolute':str(directory), 'helper_digest':native.helper_digest(),
            'launcher':native.facts(os.getpid())}
        _, problems = context.sessions.open(session)
        if problems:
            return AdapterOutcome(diagnostics=tuple(problems))
        records.write_once(directory/'supervisor-request.json', session)
        handle = context.spawn_windows_player_supervisor()
        self._checkpoint('HANDOFF', session)
        deadline = time.monotonic()+min(context.timeout, 20)
        while not (directory/'handshake.json').exists() and time.monotonic()<deadline:
            time.sleep(.1)
        try:
            handshake = records.bound(directory/'handshake.json','gpos.player.windows-handshake/1',session,
                ('supervisor','player','job_name','helper_digest'))
            if (handshake['supervisor']['pid'] != handle['pid'] or handshake['supervisor'] != handle['identity'] or
                handshake['job_name'] != native.job_name(session) or handshake['helper_digest'] != session['helper_digest']):
                raise ValueError('handshake differs from directly created supervisor')
            self.prove(session, handshake)
            trust, why = resolver.revalidates(root, session)
            if trust != 'VALID':
                raise ValueError('launch build drift: '+str(why))
            records.write_once(directory/'runtime-binding.json', handshake)
            self._checkpoint('BEFORE_COMMIT', session)
            self.prove(session, handshake)  # recheck after full hashing; an early exit is never a successful handoff
            records.write_once(directory/'commit.json', {'schema':'gpos.player.windows-commit/1',
                'session_id':session['session_id'], 'nonce':session['nonce']})
        except (OSError, ValueError, native.fs.PathRefused) as exc:
            # Abandoning our own uncommitted supervisor is bounded; preserve the lease for explicit recovery.
            records.write_once(directory/'abort.json', {'schema':'gpos.player.windows-abort/1',
                'session_id':session['session_id'], 'nonce':session['nonce']})
            return outcome('PLAYER_LAUNCH_UNRESOLVED', str(exc), c.LAUNCH,
                {'session_id':session['session_id'],'phase':c.LAUNCHING_UNRESOLVED},True,
                command=handle['command'],environment=handle['environment'])
        return outcome('PLAYER_LAUNCHED', 'owned Windows Player bound; no gameplay/readiness claim', c.LAUNCH,
            {'session_id':session['session_id'],'build':target,'player':handshake['player'],
             'supervisor':handshake['supervisor'],'job_name':handshake['job_name'],'phase':'BOUND'},True,
            artifacts=(ArtifactSpec('runtime-binding','JSON',str(directory/'runtime-binding.json'),'application/json','Windows kernel-proven session binding'),),
            command=handle['command'],environment=handle['environment'])

    def prove(self, session, binding):
        if (binding['player']['executable'] != session['executable'] or
            binding['player']['parent_pid'] != binding['supervisor']['pid'] or
            binding['player']['created_filetime'] < binding['supervisor']['created_filetime']):
            raise ValueError('Player executable/parent/creation relationship differs')
        job = native.open_job(session)
        if not job:
            raise ValueError('session Job is gone or unproven')
        try:
            if native.identity(binding['player'], job) != 'PROVEN' or native.identity(binding['supervisor'], job) != 'PROVEN':
                raise ValueError('kernel Player/supervisor identity or Job ownership is unproven')
            s = native.facts(binding['supervisor']['pid'])
            p = native.facts(binding['player']['pid'])
            if (p != binding['player'] or p['argv'] != [session['executable'],'-logFile',str(Path(session['runtime_absolute'])/'player.log')]):
                raise ValueError('kernel Player relationship/command differs')
            if (s != binding['supervisor'] or s['executable'] != str(Path(os.sys.executable).resolve()) or
                len(s['argv'])!=8 or s['argv'][1:6]!=['-I','-B','-X','utf8',str(native.HELPER)] or
                s['argv'][6]!=session['runtime_absolute']):
                raise ValueError('supervisor is not the fixed audited entry')
            if set(native.foreign(session['executable'])) != {binding['player']['pid']}:
                raise ValueError('foreign payload instance appeared')
            return True
        finally:
            native.batch._close(job)

    def observe(self, context, *, build_observation=None):
        session = context.session['session'];directory = Path(session['runtime_absolute'])
        root = Path(context.project_root)
        if directory != root/'.game/gpos-runtime/tool-output/player'/session['launch_request_id']:
            raise ValueError('session directory differs')
        if records.read(directory/'supervisor-request.json') != session:
            raise ValueError('session request was modified')
        binding = None
        try:
            binding = records.bound(directory/'runtime-binding.json','gpos.player.windows-handshake/1',session,
                ('supervisor','player','job_name','helper_digest'))
        except FileNotFoundError:
            pass
        job = native.open_job(session)
        try:
            if binding:
                if binding['job_name'] != native.job_name(session) or binding['helper_digest'] != session['helper_digest']:
                    raise ValueError('binding Job/helper differs')
                player = native.identity(binding['player'], job)
                supervisor = native.identity(binding['supervisor'], job)
                if job and player == 'PROVEN':
                    self.prove(session, binding)
                if not job and player == 'PROVEN':
                    player = 'UNPROVEN'
            else:
                player = supervisor = 'UNPROVEN'
            exit_record = None
            try:
                exit_record = records.bound(directory/'exit.json','gpos.player.windows-exit/1',session,
                    ('player','code','committed','stop_reason','graceful_requested','forced','observed_at'))
                if binding and exit_record['player'] != binding['player']:
                    raise ValueError('exit record names another Player')
            except FileNotFoundError:
                pass
            trust, why = build_observation if build_observation is not None else resolver.revalidates(root, session)
            return {'session_id':session['session_id'],'build_id':session['build_id'],
                'build_revision':session['build_revision'],'build_revision_source':'CALLER_SUPPLIED',
                'phase':'BOUND' if binding else 'LAUNCHING_UNRESOLVED', 'identity':player,
                'supervisor_state':supervisor,'player':None if not binding else binding['player'],
                'observed_running':{'player':True if player=='PROVEN' else False if player in ('GONE','NOT_THIS_PROCESS') else None,
                    'supervisor':True if supervisor=='PROVEN' else False if supervisor in ('GONE','NOT_THIS_PROCESS') else None},
                'supervisor':None if not binding else binding['supervisor'],'exit':exit_record,
                'job_name':native.job_name(session),'job_members':native.members(job) if job else [],
                'job_present':bool(job),'build_trust':trust,'drift':why}, session, binding
        finally:
            native.batch._close(job)

    def status(self, request, context):
        self.restated(request, context)
        data, _, _ = self.observe(context)
        return AdapterOutcome(data=data)

    def stop(self, request, context):
        self.restated(request, context)
        if set(request.inputs) - {c.RECOVER_INPUT}:
            return outcome('INVALID_TOOL_REQUEST','stop accepts only the existing recovery input',c.STOP)
        data, session, binding = self.observe(context)
        own = context.session['owner_id'] == leases.session_owner(request.actor)
        recover = request.inputs.get(c.RECOVER_INPUT, False)
        if recover not in (True,False,'true','false'):
            return outcome('INVALID_TOOL_REQUEST','recovery input must be boolean',c.STOP)
        recover = recover in (True,'true')
        if not own and not recover:
            return outcome('LIVE_SESSION_MISMATCH','another owner can only recover a proven gone runtime',c.STOP)
        directory = Path(session['runtime_absolute'])
        if context.dry_run:
            return AdapterOutcome(plan=('Stop only a proven owned Player; recover only an observed gone runtime; keep uncertainty.',),data=data)
        build_observation = (data['build_trust'], data['drift'])
        attempted = False
        if data['identity']=='PROVEN':
            if not own:
                return outcome('LIVE_SESSION_MISMATCH','foreign owner cannot stop a live Player',c.STOP)
            if context.dry_run:
                return AdapterOutcome(plan=('Stop only the proven owned Player; keep uncertain session.',),data=data)
            if not (directory/'stop-intent.json').exists():
                records.write_once(directory/'stop-intent.json',{'schema':'gpos.player.windows-stop/1',
                    'session_id':session['session_id'],'nonce':session['nonce']})
                attempted = True
            deadline = time.monotonic()+18
            while time.monotonic()<deadline:
                data, _, _ = self.observe(context, build_observation=build_observation)
                if not data['job_present']:
                    break
                time.sleep(.1)
            if data['job_present']:
                # Silent but still proven supervisor: terminate the owned Job, never a pid/group from a request.
                self.prove(session, binding)
                job = native.open_job(session, terminate=True)
                try:
                    if native.identity(binding['player'],job)!='PROVEN' or native.identity(binding['supervisor'],job)!='PROVEN':
                        raise ValueError('fallback stop ownership changed')
                    attempted = True
                    if not native.batch._k32.TerminateJobObject(job,native.EXIT_FORCED):
                        raise ValueError('owned Job stop failed')
                finally:
                    native.batch._close(job)
                time.sleep(.2);data, _, _ = self.observe(context, build_observation=build_observation)
        if data['job_present'] or native.foreign(session['executable']):
            return outcome('PLAYER_STOP_OUTCOME_UNKNOWN','ownership/cleanup remains uncertain; lease kept',c.STOP,data,attempted)
        if binding and data['identity'] not in ('GONE','NOT_THIS_PROCESS'):
            return outcome('PLAYER_STOP_OUTCOME_UNKNOWN','Player identity is not proven gone; lease kept',c.STOP,data)
        if not binding and (not recover or native.identity(session['launcher']) not in ('GONE','NOT_THIS_PROCESS')):
            # A concurrent launcher must not create its Job after a recovery released SESSION.
            return outcome('PLAYER_STOP_OUTCOME_UNKNOWN','unbound runtime needs explicit recovery and proven gone launcher',c.STOP,data)
        if context.dry_run:
            return AdapterOutcome(plan=('Recover only the observed gone session.',),data=data)
        if own:
            _, problems = context.sessions.close(session['session_id'])
        else:
            _, problems = context.sessions.recover(context.session['token'],'Windows Player and Job proven gone')
        if problems:
            return AdapterOutcome(diagnostics=tuple(problems),data=data)
        exit_record = data['exit']
        classification = ('GONE_UNOBSERVED' if not exit_record else
            'FORCED_STOP' if exit_record['forced'] or (exit_record['stop_reason']=='STOP' and exit_record['code']!=0) else
            'GRACEFUL_STOP' if exit_record['stop_reason']=='STOP' else 'EXITED' if exit_record['code']==0 else 'CRASHED')
        data.update(closed=True, classification=classification)
        records.write_once(Path(context.workspace)/'stop-result.json',data)
        return outcome('PLAYER_STOPPED','proven owned Windows runtime ended; SESSION released',c.STOP,data,True,
            artifacts=(ArtifactSpec('stop-result','JSON',str(Path(context.workspace)/'stop-result.json'),'application/json','Operational stop result; no gameplay evidence'),))


class WindowsPlayerAdapter(WindowsLifecycle, model.ToolAdapter):
    """Historical explicit candidate wrapper; never registered by production."""
    descriptor = DESCRIPTOR
