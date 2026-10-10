"""TEST_ONLY baseline-gated workflow guard mutations; no Editor or production mutation."""
import argparse,json,os,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser();parser.add_argument('--lab',required=True);args=parser.parse_args()
LAB=Path(args.lab).resolve()
assert LAB==Path(r'D:\gpos-trusted-windows-build-qualification-20261010').resolve() and LAB.is_dir()
source=(ROOT/'tests/trusted_windows_build_testkit.py').read_text()
mutations=[
 ('non-success-revision','answer.status != "SUCCESS" or ','') ,
 ('equal-revisions','if expected is not None and revision != expected:','if False:'),
 ('between-observation','if observe("git_between", revision) is None:','if observe("git_between", revision) is None and False:'),
 ('after-observation','if observe("git_after", revision) is None:','if observe("git_after", revision) is None and False:'),
 ('buildable-inspection','data.get("buildable") is not True or ',''),
 ('publication-proof',' or "BUILD_PUBLISHED" not in {d.code for d in built.diagnostics}',''),
 ('payload-revalidation','if problem is not None or not isinstance(manifest, dict):','if not isinstance(manifest, dict):'),
 ('caller-attribution','if manifest.get("build_revision_source") != "CALLER_SUPPLIED" or manifest.get("build_revision") != revision or "git_verified" in manifest:','if False:'),
]
results=[]
for name,old,new in [('baseline',None,None),*mutations]:
 work=LAB/('workflow-mutation-final-'+name);work.mkdir(exist_ok=False)
 text=source
 if old is not None:
  assert text.count(old)==1,(name,text.count(old));text=text.replace(old,new)
 (work/'trusted_windows_build_testkit.py').write_text(text,encoding='utf-8')
 shutil.copyfile(ROOT/'tests/test_trusted_windows_workflow.py',work/'test_trusted_windows_workflow.py')
 command=[sys.executable,'-B','-X','utf8',str(work/'test_trusted_windows_workflow.py')]
 with (work/'result.log').open('wb') as log:
  r=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=15,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
 log=(work/'result.log').read_text(encoding='utf-8')
 assert re.search(r'Ran 14 tests',log),('infrastructure failure',name,log)
 if name=='baseline':assert r.returncode==0 and '\nOK\n' in log;state='PASS'
 else:assert r.returncode==1 and re.search(r'FAILED \(failures=\d+\)',log),(name,'SURVIVED or infrastructure failure',log);state='CAUGHT'
 results.append({'name':name,'outcome':state,'exit_code':r.returncode});print(name,state,flush=True)
(LAB/'workflow-mutations-final.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
