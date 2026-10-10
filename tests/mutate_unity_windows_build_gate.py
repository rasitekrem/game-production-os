"""TEST_ONLY focused gate mutations with an identical passing 12-test baseline."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser()
parser.add_argument('--lab',required=True)
args=parser.parse_args()
LAB=Path(args.lab).resolve()
if sys.platform!='win32' or LAB!=Path(r'D:\gpos-windows-build-production-gate-20261010').resolve() or not LAB.is_dir():
    sys.exit('fresh authorized Windows lab required')
mutations=[
    ('withdraw-inspection','gpos/tools/unity/adapter.py','                        ub.INSPECT_BUILD, ub.BUILD)',
     '                        ub.BUILD)'),
    ('withdraw-build','gpos/tools/unity/adapter.py','                        ub.INSPECT_BUILD, ub.BUILD)',
     '                        ub.INSPECT_BUILD)'),
    ('expand-unrelated-capability','gpos/tools/unity/adapter.py','                        ub.INSPECT_BUILD, ub.BUILD)',
     '                        ub.INSPECT_BUILD, ub.BUILD, authoring.ADD_COMPONENT)'),
    ('restore-stale-provenance-wording','gpos/tools/unity/build_windows.py',
     'Windows Git provenance is available in alpha.28 within its qualified bounded subset.',
     'Windows Git provenance is unavailable pending D-G1.'),
]
results=[]
for name,relative,anchor,replacement in [('baseline',None,None,None),*mutations]:
    work=LAB/('gate-mutation-'+name)
    shutil.copytree(ROOT,work,ignore=shutil.ignore_patterns('.git','__pycache__'))
    if relative is not None:
        path=work/relative
        source=path.read_text(encoding='utf-8')
        if source.count(anchor)!=1:sys.exit('nonunique mutation anchor: '+name)
        path.write_text(source.replace(anchor,replacement),encoding='utf-8')
    command=[sys.executable,'-B','-X','utf8',str(work/'tests/test_unity_windows_build_production.py')]
    try:
        outcome=subprocess.run(command,cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=30,
                               env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    except subprocess.TimeoutExpired:
        sys.exit('INCONCLUSIVE timeout: '+name)
    log=outcome.stdout+outcome.stderr
    (work/'result.log').write_text(log,encoding='utf-8')
    if not re.search(r'Ran 12 tests',log):sys.exit('INCONCLUSIVE runner: '+name)
    if name=='baseline':
        if outcome.returncode!=0 or '\nOK\n' not in log:sys.exit('baseline did not pass')
        verdict='PASS'
    else:
        if outcome.returncode!=1 or 'ERROR:' in log or not re.search(r'FAILED \(failures=\d+\)',log):
            sys.exit('MISSED or INCONCLUSIVE: '+name)
        verdict='CAUGHT'
    results.append({'name':name,'verdict':verdict,'exit_code':outcome.returncode})
    print(name,verdict,flush=True)
(LAB/'gate-mutations.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
