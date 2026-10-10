"""Baseline-gated Windows Build Core mutations; timeout is INCONCLUSIVE, never CAUGHT.

All copies, logs and real fixture projects stay in the authorized qualification lab.
Real mutations are sequential, run through the actual test-only adapter and Windows Job boundary.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
import mutation_gate as gate

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
LAB = Path(r'D:\gpos-unity-lab-alpha27-qualification')
WB = 'gpos/tools/unity/build_win32.py'
DISPATCH = 'gpos/tools/unity/build_windows.py'
RULES = 'gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Core/BuildRules.cs'
ENTRY = 'gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Build/BuildEntry.cs'
FAST = [
    ('ADS enumeration bypassed','unit',[(WB,'    safe(path)\n    data = STREAM_DATA()','    return\n    data = STREAM_DATA()')]),
    ('public lexical gate bypassed','unit',[(WB,'    path = Path(path)\n    why =','    return Path(path)\n    why =')]),
    ('default stream errors accepted','unit',[(WB,'        if directory and error == 38:','        if directory:')]),
    ('incomplete stream enumeration accepted','unit',[(WB,'                if error != 38:','                if False:')]),
    ('stream close failure ignored','unit',[(WB,'        if not _close(h):','        if False and not _close(h):')]),
    ('native x86 PE accepted','unit',[(WB,'    if not managed and (machine != 0x8664 or magic != 0x20b):','    if False:')]),
    ('boot GUID ignored','unit',[(WB,'if [line.split("=", 1)[1] for line in boot.splitlines() if line.startswith("build-guid=")] != [guid]:','if False:')]),
    ('payload file identities ignored','unit',[(WB,'    data = {"root_identity": root_id, "inventory": entries}','    for entry in entries: entry.pop("identity", None)\n    data = {"root_identity": root_id, "inventory": entries}')]),
    ('tree entry bound removed','unit',[(WB,'or len(entries) >= MAX_ENTRIES:',':'),
                                      (WB,'                    if len(names) + len(entries) >= MAX_ENTRIES:', '                    if False:')]),
    ('BuildReport inventory coverage removed','unit',[(WB,'        if seen != set(files):','        if False:')]),
    ('sidecars accepted','unit',[(WB,'            raise PayloadProblem("unsupported output sidecar or top-level entry")','            pass')]),
    ('manifest overwrite permitted','unit',[(WB,'            os.rename(source, target)','            os.replace(source, target)')]),
    ('containment gate removed','unit',[(DISPATCH,'    if not outcome.integrity_ok:','    if False:')]),
    ('postpublication revalidation removed','unit',[(DISPATCH,'    _, problem = wb.revalidate(workspace)','    problem = None')]),
    ('revision source accepts Git verified','unit',[(WB,'manifest["build_revision_source"] != "CALLER_SUPPLIED"','False')]),
    ('manifest limits omitted','unit',[(WB,'manifest["limitations"] != list(LIMITATIONS)','False')]),
    ('manifest BuildReport size binding removed','unit',[(WB,'b["total_size"] != payload["bytes"]','False')]),
    ('Windows profiles permitted','core',[(RULES,'            if (f.Profile != null) Add(p, "WINDOWS_PROFILE_UNSUPPORTED"','            if (false) Add(p, "WINDOWS_PROFILE_UNSUPPORTED"')]),
    ('Windows output flags ignored','core',[(RULES,'            if (f.CopyPdb || f.CreateSolution || f.InstallInBuildFolder) Add(p, OutputKind,','            if (false) Add(p, OutputKind,')]),
    ('Windows version pin omitted','core',[(RULES,'            if (f.UnityVersion != "6000.6.4f1") Add(p,','            if (false) Add(p,')]),
]
REAL = [('Editor token precheck bypassed','real',[(ENTRY,
    '            if ((string)answer["configuration_token"] != request.ExpectedToken)',
    '            if (false)')])]

def run(mutation):
    name, suite, edits = mutation
    from gpos.tools.unity import build_win32 as wb
    wb.safe(LAB)
    work = LAB/('mutation-'+uuid.uuid4().hex[:12]); work.mkdir()
    copy = work/'repo'
    verdict = 'INCONCLUSIVE'
    try:
        shutil.copytree(ROOT,copy,ignore=shutil.ignore_patterns('.git','__pycache__'))
        for rel,anchor,replacement in edits:
            path=copy/rel; text=gate.read(path)
            if text.count(anchor)!=1: return name,'NOT APPLIED ('+rel+')'
            gate.write(path,text.replace(anchor,replacement))
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',GPOS_UNITY_TEST_FAST='1',
                 GPOS_WINDOWS_BUILD_LAB=str(LAB),GPOS_UNITY_WINDOWS_LIVE_LAB=str(LAB))
        env.pop('GPOS_WINDOWS_BUILD_REAL',None)
        if any('live_bridge/' in rel for rel,_,_ in edits):
            subprocess.run([*gate.PYTHON,'-B',str(copy/'tests/generate_live_bridge_manifest.py')],
                capture_output=True,check=True,timeout=60,env=env,**gate.ISOLATED)
        if suite=='core':
            argv=[str(copy/'tests/test_unity_windows_live.py'),'WLB_BridgeSources']
        else:
            argv=[str(copy/'tests/test_unity_windows_build.py')]
            if suite=='real':
                env['GPOS_WINDOWS_BUILD_REAL']='1'
                argv+=['R_RealBuild.test_01_inspect_and_positive','R_RealBuild.test_02_preconfiguration_drift']
            else: argv+=['A_NtfsContract','B_BuildBoundary']
        try:
            r=subprocess.run([*gate.PYTHON,'-B',*argv],capture_output=True,text=True,env=env,
                             timeout=240 if suite=='real' else 90,**gate.ISOLATED)
            output=r.stdout+r.stderr
            (work/'suite.log').write_text(output,encoding='utf-8')
            verdict='MISSED' if r.returncode==0 else ('CAUGHT' if 'FAIL:' in output and 'AssertionError' in output else 'INCONCLUSIVE (suite error)')
        except subprocess.TimeoutExpired:
            verdict='INCONCLUSIVE (timeout; no causal proof)'
        (work/'verdict.json').write_text(json.dumps({'name':name,'suite':suite,'verdict':verdict,'edits':edits},indent=2),encoding='utf-8')
        return name,verdict
    finally:
        # Only the checked test-created repo copy is removed; results and real fixture quarantine are retained.
        if copy.exists():
            assert copy.resolve().is_relative_to(LAB.resolve())
            shutil.rmtree(copy)

def main():
    p=argparse.ArgumentParser();p.add_argument('--real',action='store_true');p.add_argument('--only')
    a=p.parse_args(); selected=[m for m in (REAL if a.real else FAST) if not a.only or a.only in m[0]]
    baselines=[(gate.BASELINE+' ['+suite+']',suite,[]) for suite in sorted({m[1] for m in selected})]
    return gate.qualify(run,selected,jobs=1,baselines=baselines)

if __name__=='__main__':sys.exit(main())
