"""TEST_ONLY separate-invocation candidate qualification; never the production CLI registry."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from gpos.framework import load_framework
from gpos.tools.registry import default_registry, ToolRegistry
from gpos.tools.execution import ExecutionRequest, execute
from gpos.tools.model import Subject, Actor
from gpos.tools.player.windows import WindowsPlayerAdapter

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('capability',choices=('launch','status','stop'))
    parser.add_argument('request_id')
    parser.add_argument('--session')
    parser.add_argument('--recover',action='store_true')
    parser.add_argument('--fault',choices=('interrupt-handoff','fail-commit','lost-handshake','wait-for-early-exit'))
    args=parser.parse_args()
    lab=Path(os.environ['GPOS_WINDOWS_PLAYER_LAB']).resolve()
    if lab.drive!='D:' or not lab.name.startswith('gpos-windows-player-runtime-core-'):
        raise ValueError('not an authorized disposable Windows Player lab')
    root=lab/'qualified-source'
    workflow=json.loads((lab/'positive-workflow.json').read_text(encoding='utf-8'))
    assert workflow['qualified'] is True
    def fault(stage, session):
        if args.fault=='wait-for-early-exit' and stage=='HANDOFF':
            import time
            time.sleep(7)
        if args.fault=='interrupt-handoff' and stage=='HANDOFF':os._exit(93)
        if args.fault=='fail-commit' and stage=='BEFORE_COMMIT':raise OSError('TEST_ONLY deliberate commit failure')
        if args.fault=='lost-handshake' and stage=='HANDOFF':
            # Force only the candidate's confirmation timeout, not an actual successful observation.
            from gpos.tools.player import windows_records
            original=windows_records.bound
            def missing(path,*rest):
                if Path(path).name=='handshake.json':raise FileNotFoundError('TEST_ONLY lost handshake')
                return original(path,*rest)
            windows_records.bound=missing
    fw=load_framework();production=default_registry(fw)
    assert production.probe('player').status=='UNAVAILABLE' and len(production.adapter_ids())==7
    registry=ToolRegistry(fw,allow_test_only=True)
    for adapter in production.adapter_ids():
        registry.register(WindowsPlayerAdapter(_qualification_hook=fault) if adapter=='player' else production.get(adapter))
    request=ExecutionRequest('player','player.'+args.capability,Subject('PROJECT','synthetic-adapter-project'),
        project_root=str(root),actor=Actor('AGENT','windows-player-core-qualification'),
        build_id=workflow['manifest']['build_id'],build_revision=workflow['revision'],target_platform='WINDOWS',
        request_id=args.request_id,session_id=args.session,allow_mutation=args.capability!='status',
        inputs={'recover_proven_gone':True} if args.recover else {})
    result=execute(registry,request)
    (lab/(args.request_id+'.json')).write_text(json.dumps(result.to_dict(),indent=2)+'\n',encoding='utf-8')
    print(result.status,[d.code for d in result.diagnostics],json.dumps(result.data),flush=True)

if __name__=='__main__':main()
