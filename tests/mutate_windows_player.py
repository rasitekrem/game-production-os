"""Focused candidate/foundation mutations in independent fresh disposable source copies only."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
LAB=Path(os.environ['GPOS_WINDOWS_PLAYER_LAB']).resolve()
if LAB.drive!='D:' or not LAB.name.startswith('gpos-windows-player-runtime-core-'):
    raise ValueError('not an authorized disposable mutation lab')
NATIVE='gpos/tools/player_process_win32.py';ADAPTER='gpos/tools/player/windows.py'
MUTATIONS=[
    ('creation-time',NATIVE,"process.created() != expected['created_filetime']",'False'),
    ('job-membership',NATIVE,'job and not batch._in_job(process._h, job)','False'),
    ('same-user',NATIVE,'process.same_user() is not True','False'),
    ('nonce-job-name',NATIVE,"+'-'+session['nonce']","+'-'+session['session_id']"),
    ('no-batch-breakaway',NATIVE,'not limits.BasicLimitInformation.LimitFlags & 0x800','False'),
    ('unknown-job',NATIVE,'not job and ctypes.get_last_error() != 2','False'),
    ('control-nonce','gpos/tools/player/windows_records.py',
        "return record == {'schema': schema, 'session_id': session['session_id'], 'nonce': session['nonce']}",
        "return record.get('schema') == schema and record.get('session_id') == session['session_id']"),
    ('record-bound','gpos/tools/player/windows_records.py','len(data) > MAX_BYTES','False'),
    ('unproven-binding',ADAPTER,"binding and data['identity'] not in ('GONE','NOT_THIS_PROCESS')",'False'),
    ('active-launcher-recovery',ADAPTER,"native.identity(session['launcher']) not in ('GONE','NOT_THIS_PROCESS')",'False'),
    ('one-context-spawn','gpos/tools/execution.py','self.dry_run or self._detached or','self.dry_run or'),
    ('candidate-only-gate',ADAPTER,"adapter_kind='TEST_ONLY'","adapter_kind='DEVICE'"),
]

def run(name, mutation=None):
    work=LAB/'mutations-review-final'/name
    assert work.resolve().is_relative_to(LAB)
    work.mkdir(parents=True)
    for directory in ('gpos','tests','core','schemas','skills','workflows','templates'):
        shutil.copytree(ROOT/directory,work/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(ROOT/'VERSION',work/'VERSION')
    if mutation:
        relative,old,new=mutation;path=work/relative;source=path.read_text(encoding='utf-8')
        assert source.count(old)==1,(name,source.count(old))
        path.write_text(source.replace(old,new),encoding='utf-8')
    env=dict(os.environ,TEMP=str(LAB/'unit-temp'),TMP=str(LAB/'unit-temp'),GPOS_WINDOWS_BUILD_LAB=str(LAB),GPOS_UNITY_WINDOWS_LAB=str(LAB))
    completed=subprocess.run([sys.executable,'-I','-B','-X','utf8',str(work/'tests/test_windows_player.py')],
        cwd=work,env=env,capture_output=True,text=True,encoding='utf-8',timeout=45)
    (work/'result.log').write_text(completed.stdout+completed.stderr,encoding='utf-8')
    output=completed.stdout+completed.stderr
    result={'name':name,'returncode':completed.returncode,'verdict':
        ('PASS' if not mutation and completed.returncode==0 else 'CAUGHT' if mutation and completed.returncode!=0 and '\nFAIL:' in output and '\nERROR:' not in output else 'INCONCLUSIVE'),
        'log':str(work/'result.log')}
    print(name,result['verdict'],flush=True)
    return result

if __name__=='__main__':
    results=[]
    for name,mutation in [('baseline',None),*[(row[0],row[1:]) for row in MUTATIONS]]:
        results.append(run(name,mutation))
        (LAB/'mutation-results.json').write_text(json.dumps(results,indent=2)+'\n')
        if mutation is None:
            assert results[-1]['verdict']=='PASS','baseline must pass before any mutation is counted'
    assert results[0]['verdict']=='PASS' and all(r['verdict']=='CAUGHT' for r in results[1:]),results
