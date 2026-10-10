"""Separate-invocation qualification through unmodified default_registry and production PlayerAdapter.

Faults are deterministic test-side method/observation injections; no descriptor or availability replacement.
"""
import argparse,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from gpos.tools.registry import default_registry
from gpos.tools.execution import ExecutionRequest,execute
from gpos.tools.model import Subject,Actor
from gpos.tools.player import adapter,windows,contract as c

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('capability',choices=('launch','status','stop','capture-screenshot','capture-video','install-capture-helper'))
    parser.add_argument('request_id')
    parser.add_argument('--session');parser.add_argument('--recover',action='store_true')
    parser.add_argument('--actor',default='windows-player-production-qualification')
    parser.add_argument('--build')
    parser.add_argument('--fault',choices=('interrupt-handoff','fail-commit','lost-handshake','wait-for-early-exit','interrupt-stop'))
    args=parser.parse_args()
    lab=Path(os.environ['GPOS_WINDOWS_PLAYER_LAB']).resolve()
    if lab.drive!='D:' or not lab.name.startswith('gpos-windows-player-production-gate-'):
        raise ValueError('not an authorized fresh Windows Player production lab')
    root=lab/'qualified-source';workflow=json.loads((lab/'positive-workflow.json').read_text())
    assert workflow['qualified'] is True
    def fault(self,stage,session):
        if args.fault=='wait-for-early-exit' and stage=='HANDOFF':time.sleep(7)
        if args.fault=='interrupt-handoff' and stage=='HANDOFF':os._exit(93)
        if args.fault=='fail-commit' and stage=='BEFORE_COMMIT':raise OSError('DETERMINISTIC_TEST_INJECTION: commit failure')
        if args.fault=='lost-handshake' and stage=='HANDOFF':
            from gpos.tools.player import windows_records
            original=windows_records.bound
            def missing(path,*rest):
                if Path(path).name=='handshake.json':raise FileNotFoundError('DETERMINISTIC_TEST_INJECTION: lost handshake')
                return original(path,*rest)
            windows_records.bound=missing
    if args.fault:windows.WindowsLifecycle._checkpoint=fault
    if args.fault=='interrupt-stop':
        from gpos.tools.player import windows_records
        original_write=windows_records.write_once
        def interrupt(path,value):
            original_write(path,value)
            if Path(path).name=='stop-intent.json':os._exit(94)
        windows_records.write_once=interrupt
    registry=default_registry()
    assert len(registry.adapter_ids())==7 and not registry.allow_test_only
    assert type(registry.get('player')) is adapter.PlayerAdapter
    assert not registry.get('player').descriptor.test_only
    probe=registry.probe('player')
    assert probe.status=='AVAILABLE'
    assert {cid for cid,available,_ in probe.capability_availability if available}=={c.LAUNCH,c.STATUS,c.STOP}
    request=ExecutionRequest('player','player.'+args.capability,Subject('PROJECT','synthetic-adapter-project'),
        project_root=str(root),actor=Actor('AGENT',args.actor),build_id=args.build or workflow['manifest']['build_id'],
        build_revision=workflow['revision'],target_platform='WINDOWS',request_id=args.request_id,session_id=args.session,
        allow_mutation=args.capability!='status',inputs={c.RECOVER_INPUT:True} if args.recover else {})
    result=execute(registry,request)
    (lab/(args.request_id+'.json')).write_text(json.dumps(result.to_dict(),indent=2)+'\n')
    print(result.status,[d.code for d in result.diagnostics],json.dumps(result.data),flush=True)

if __name__=='__main__':main()
