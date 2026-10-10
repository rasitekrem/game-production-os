"""Focused gate mutations in fresh lab source copies. No production source is mutated by this harness."""
import hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
LAB=Path(os.environ['GPOS_WINDOWS_PLAYER_LAB']).resolve()
if LAB.drive!='D:' or not LAB.name.startswith('gpos-windows-player-production-gate-'):
    raise ValueError('not an authorized production gate mutation lab')
P='gpos/tools/player/adapter.py';W='gpos/tools/player/windows.py'
MUTATIONS=[
 ('windows-platform-removed',P,'supported_platforms=("MACOS", "WINDOWS")','supported_platforms=("MACOS",)'),
 ('linux-platform-expanded',P,'supported_platforms=("MACOS", "WINDOWS")','supported_platforms=("MACOS", "WINDOWS", "LINUX")'),
 ('capture-probe-open',P,'cap.id in (c.LAUNCH, c.STATUS, c.STOP),','cap.id in (c.LAUNCH, c.STATUS, c.STOP, c.SCREENSHOT),'),
 ('capture-dispatch-open',P,'request.capability_id not in (c.LAUNCH, c.STATUS, c.STOP)','False'),
 ('lifecycle-dispatch-blocked',P,'request.capability_id not in (c.LAUNCH, c.STATUS, c.STOP)','True'),
 ('helper-probe-fail-open',W,'digest = native.helper_digest()',"digest = 'unverified'"),
 ('test-only-dispatch',P,'from .windows import WindowsLifecycle\n            return WindowsLifecycle().execute(request, context)',
  'from .windows import WindowsPlayerAdapter\n            return WindowsPlayerAdapter().execute(request, context)'),
]
def run(name,mutation=None):
    work=LAB/'gate-mutations'/name
    assert work.resolve().is_relative_to(LAB)
    work.mkdir(parents=True)
    for directory in ('gpos','tests','core','schemas','skills','workflows','templates'):
        shutil.copytree(ROOT/directory,work/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(ROOT/'VERSION',work/'VERSION')
    hashes={str(p.relative_to(work)):hashlib.sha256(p.read_bytes()).hexdigest()
            for directory in ('gpos','tests') for p in (work/directory).rglob('*') if p.is_file()}
    (work/'source-before-mutation.json').write_text(json.dumps(hashes,indent=2))
    if mutation:
        relative,old,new=mutation;path=work/relative;source=path.read_text()
        assert source.count(old)==1,(name,source.count(old))
        path.write_text(source.replace(old,new))
    env=dict(os.environ,TEMP=str(LAB/'unit-temp'),TMP=str(LAB/'unit-temp'),GPOS_WINDOWS_BUILD_LAB=str(LAB))
    result=subprocess.run([sys.executable,'-I','-B','-X','utf8',str(work/'tests/test_windows_player_production.py')],
                          cwd=work,env=env,capture_output=True,text=True,encoding='utf-8',timeout=45)
    output=result.stdout+result.stderr;(work/'result.log').write_text(output)
    verdict=('PASS' if not mutation and result.returncode==0 else
             'CAUGHT' if mutation and result.returncode!=0 and '\nFAIL:' in output and '\nERROR:' not in output else 'INCONCLUSIVE')
    print(name,verdict,flush=True)
    return {'name':name,'verdict':verdict,'returncode':result.returncode,'log':str(work/'result.log')}
if __name__=='__main__':
    results=[]
    for name,mutation in [('baseline',None),*[(m[0],m[1:]) for m in MUTATIONS]]:
        results.append(run(name,mutation));(LAB/'gate-mutation-results.json').write_text(json.dumps(results,indent=2))
        if mutation is None:assert results[-1]['verdict']=='PASS'
    assert all(row['verdict']=='CAUGHT' for row in results[1:]),results
