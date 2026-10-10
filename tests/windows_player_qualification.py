"""Real Windows Player negative qualification; TEST_ONLY, explicitly authorized fresh lab only.

Run after the production build workflow and successful separate-invocation launch/status/stop.
Every fault targets a handle proven against the session created here. No foreign process is stopped.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from gpos.tools import player_process_win32 as native, leases
from gpos.tools.player import windows_records as records, windows_resolver as resolver

LAB=Path(os.environ['GPOS_WINDOWS_PLAYER_LAB']).resolve()
if LAB.drive!='D:' or not LAB.name.startswith('gpos-windows-player-runtime-core-'):
    raise ValueError('qualification lab scope refused')
PROJECT=LAB/'qualified-source'
CLI=Path(__file__).with_name('windows_player_cli.py')
workflow=json.loads((LAB/'positive-workflow.json').read_text())
BUILD=workflow['manifest']['build_id']
VERDICTS=[]

def save():
    (LAB/'real-negative-results.json').write_text(json.dumps(VERDICTS,indent=2)+'\n',encoding='utf-8')

def call(cap, rid, session=None, *, fault=None, recover=False):
    args=[sys.executable,'-I','-B','-X','utf8',str(CLI),cap,rid]
    if session:args+=['--session',session['session_id']]
    if fault:args+=['--fault',fault]
    if recover:args+=['--recover']
    started=time.monotonic()
    child=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=90)
    (LAB/(rid+'-console.txt')).write_text(child.stdout+child.stderr,encoding='utf-8')
    VERDICTS.append({'request_id':rid,'seconds':round(time.monotonic()-started,3),'cli_exit':child.returncode})
    save()
    if fault=='interrupt-handoff':
        assert child.returncode==93,child.stderr
        return None
    assert child.returncode==0,child.stderr
    return json.loads((LAB/(rid+'.json')).read_text())

def launched(rid, **kw):
    result=call('launch',rid,**kw)
    if result is not None and not kw:
        assert result['status']=='SUCCESS',result['diagnostics']
    return records.read(PROJECT/'.game/gpos-runtime/tool-output/player'/rid/'supervisor-request.json'),result

def gone(session, wait=22):
    deadline=time.monotonic()+wait
    while time.monotonic()<deadline:
        job=native.open_job(session)
        if not job and not native.foreign(session['executable']):return
        native.batch._close(job);time.sleep(.25)
    raise AssertionError('owned runtime did not clean up within bound')

def clean(session):
    gone(session)
    assert leases.holder(PROJECT,'player','PLAYER_RUNTIME:'+str(PROJECT)) is None
    assert resolver.resolve(PROJECT,BUILD)['build_id']==BUILD

def terminate_owned(session, expected, code):
    job=native.open_job(session)
    assert job and native.identity(expected,job)=='PROVEN'
    handle=native.batch._k32.OpenProcess(0x1001,False,expected['pid'])
    assert handle
    try:
        process=native.host.Process(expected['pid'],handle)
        assert process.created()==expected['created_filetime'] and process.image()==expected['executable']
        assert process.same_user() and native.batch._in_job(handle,job)
        assert native.batch._k32.TerminateProcess(handle,code)
    finally:
        native.batch._close(handle);native.batch._close(job)

def verify(name, action):
    action();VERDICTS.append({'case':name,'verdict':'PASS','evidence_kind':'REAL_WINDOWS_PLAYER_AND_KERNEL'})
    save();print('PASS',name,flush=True)

def drift_and_identity():
    session,result=launched('negative-drift-launch')
    directory=Path(session['runtime_absolute']);binding=directory/'runtime-binding.json';original=binding.read_bytes()
    invalid=json.loads(original);invalid['player']['created_filetime']+=1
    binding.write_text(json.dumps(invalid),encoding='utf-8')
    status=call('status','negative-wrong-creation-status',session)
    assert status['data']['identity']=='NOT_THIS_PROCESS'
    stop=call('stop','negative-wrong-creation-stop',session)
    assert stop['status']=='OUTCOME_UNKNOWN'
    assert leases.holder(PROJECT,'player','PLAYER_RUNTIME:'+str(PROJECT)) is not None
    assert native.identity(result['data']['player'])=='PROVEN'
    binding.write_bytes(original)
    lease_path=leases.lease_path(PROJECT,'player','PLAYER_RUNTIME:'+str(PROJECT))
    hidden=lease_path.with_name(lease_path.name+'.qualification-held')
    assert lease_path.resolve().is_relative_to(LAB)
    os.rename(lease_path,hidden)
    try:
        foreign=call('launch','negative-foreign-no-lease-launch')
        assert foreign['status']=='CONFLICT' and 'PLAYER_RUNTIME_CONFLICT' in {d['code'] for d in foreign['diagnostics']}
        assert native.identity(result['data']['player'])=='PROVEN'
    finally:os.rename(hidden,lease_path)
    extra=Path(session['workspace'])/'payload/Player/qualification-drift.txt'
    extra.write_bytes(b'TEST_ONLY additional entry; no existing payload bytes altered')
    try:
        status=call('status','negative-drift-status',session)
        assert status['data']['identity']=='PROVEN' and status['data']['build_trust']=='DRIFT'
        stop=call('stop','negative-drift-stop',session)
        assert stop['status']=='SUCCESS' and stop['data']['closed'] and stop['data']['build_trust']=='DRIFT'
    finally:
        # Exact file created above, inside verified fresh payload; no recursive or unrelated cleanup.
        assert extra.resolve().is_relative_to(LAB)
        extra.unlink()
    clean(session)

def supervisor_failure():
    session,result=launched('negative-supervisor-launch')
    terminate_owned(session,result['data']['supervisor'],91)
    gone(session)
    status=call('status','negative-supervisor-status',session)
    assert status['data']['identity'] in ('GONE','NOT_THIS_PROCESS') and status['data']['exit'] is None
    assert leases.holder(PROJECT,'player','PLAYER_RUNTIME:'+str(PROJECT)) is not None
    assert call('stop','negative-supervisor-recover',session)['status']=='SUCCESS'
    clean(session)

def player_crash():
    session,result=launched('negative-crash-launch')
    terminate_owned(session,result['data']['player'],0xC0000005)
    gone(session)
    status=call('status','negative-crash-status',session)
    assert status['data']['exit']['code']==0xC0000005
    stop=call('stop','negative-crash-stop',session)
    assert stop['status']=='SUCCESS' and stop['data']['classification']=='CRASHED'
    clean(session)

def failed_confirmation(fault):
    session,result=launched('negative-'+fault+'-launch',fault=fault)
    assert result['status']=='OUTCOME_UNKNOWN'
    assert leases.holder(PROJECT,'player','PLAYER_RUNTIME:'+str(PROJECT)) is not None
    gone(session)
    stop=call('stop','negative-'+fault+'-recover',session,recover=True)
    assert stop['status']=='SUCCESS'
    clean(session)

def interrupted_handoff():
    session,result=launched('negative-interrupted-launch',fault='interrupt-handoff')
    assert result is None and leases.holder(PROJECT,'player','PLAYER_RUNTIME:'+str(PROJECT)) is not None
    directory=Path(session['runtime_absolute'])
    deadline=time.monotonic()+20
    while not (directory/'handshake.json').exists() and time.monotonic()<deadline:time.sleep(.1)
    handshake=records.read(directory/'handshake.json')
    job=native.open_job(session)
    assert job and native.identity(handshake['player'],job)=='PROVEN'
    native.batch._close(job)
    # Unknown stop must retain SESSION while the uncommitted supervisor owns a live Job.
    stop=call('stop','negative-interrupted-uncertain-stop',session,recover=True)
    assert stop['status']=='OUTCOME_UNKNOWN'
    deadline=time.monotonic()+85
    while time.monotonic()<deadline:
        job=native.open_job(session)
        if not job:break
        native.batch._close(job);print('waiting for bounded uncommitted cleanup',flush=True);time.sleep(10)
    gone(session,wait=2)
    stop=call('stop','negative-interrupted-recover',session,recover=True)
    assert stop['status']=='SUCCESS'
    clean(session)

def early_exit():
    mode=LAB/'runtime-mode.txt';mode.write_text('exit-during-launch',encoding='utf-8')
    try:
        session,result=launched('negative-early-exit-launch',fault='wait-for-early-exit')
        assert result['status']=='OUTCOME_UNKNOWN',result
        gone(session)
        record=records.read(Path(session['runtime_absolute'])/'exit.json')
        assert record['code']==17 and record['committed'] is False
        assert call('stop','negative-early-exit-recover',session,recover=True)['status']=='SUCCESS'
    finally:
        assert mode.resolve().is_relative_to(LAB);mode.unlink()
    clean(session)

if __name__=='__main__':
    # Individual selection permits resuming after an evidence/assertion failure without replaying passed cases.
    cases={'drift-and-identity':drift_and_identity,'supervisor-failure':supervisor_failure,'player-crash':player_crash,
           'failed-commit':lambda:failed_confirmation('fail-commit'),'lost-handshake':lambda:failed_confirmation('lost-handshake'),
           'early-exit':early_exit,'interrupted-handoff':interrupted_handoff}
    previous=LAB/'real-negative-results.json'
    if previous.exists():VERDICTS[:]=json.loads(previous.read_text())
    for name in sys.argv[1:] or cases:verify(name,cases[name])
