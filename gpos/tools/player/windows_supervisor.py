"""Fixed Windows Player supervisor entry. No command/executable/mode from a caller."""
import os
import sys
import time
import threading
from pathlib import Path

# -I excludes the project cwd and PYTHONPATH. Only this installed GPOS source root is added.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from gpos.tools import player_process_win32 as native
from gpos.tools import leases
from gpos.tools.player import windows_records as records


def run(directory, job):
    directory = Path(directory)
    session = records.read(directory/'supervisor-request.json')
    root = Path(session['project_root'])
    if directory != root/'.game/gpos-runtime/tool-output/player'/session['launch_request_id']:
        raise ValueError('not a fixed Player runtime directory')
    lease = leases.verify_session(root, 'player', 'PLAYER_RUNTIME:'+str(root), session['session_id'])
    if lease['session'] != session:
        raise ValueError('supervisor request does not equal the authoritative SESSION lease')
    if not native.batch._in_job(native.wintypes.HANDLE(-1), job) or native.helper_digest() != session['helper_digest']:
        raise ValueError('supervisor Job/code is unproven')
    # Inherited Job handle remains here only; the Player and all its descendants never inherit it.
    if not native._k32.SetHandleInformation(job, 1, 0):
        raise ValueError('supervisor Job inheritance could not be restricted')
    committed_event = threading.Event()
    def uncommitted_watchdog():
        # Covers payload validation/initialization as well as the confirmation loop.
        # Our inherited handle refers to this already verified Job, never a caller pid.
        if not committed_event.wait(75):
            native.batch._k32.TerminateJobObject(job, native.EXIT_FORCED)
    watchdog = threading.Thread(target=uncommitted_watchdog, daemon=True)
    watchdog.start()
    deadline = time.monotonic()+60
    child = None
    try:
        child = native.OwnedPlayer(root, session, job)
        handshake = {'schema':'gpos.player.windows-handshake/1', 'session_id':session['session_id'],
                     'nonce':session['nonce'], 'supervisor':native.facts(os.getpid()), 'player':child.identity,
                     'job_name':native.job_name(session), 'helper_digest':session['helper_digest']}
        records.write_once(directory/'handshake.json', handshake)
        committed, graceful, forced, reason, stopping = False, False, False, None, None
        while child.exited() is None:
            if not committed:
                committed = records.control(directory/'commit.json', 'gpos.player.windows-commit/1', session)
                if committed:
                    committed_event.set()
            if reason is None:
                if records.control(directory/'abort.json', 'gpos.player.windows-abort/1', session):
                    reason = 'ABORT'
                elif not committed and time.monotonic() > deadline:
                    reason = 'UNCOMMITTED_DEADLINE'
                elif committed and records.control(directory/'stop-intent.json', 'gpos.player.windows-stop/1', session):
                    reason = 'STOP'
            if reason is not None:
                if stopping is None:
                    stopping = time.monotonic();graceful = child.graceful()
                if time.monotonic()-stopping >= 10 and not forced:
                    forced = child.terminate()
                if time.monotonic()-stopping > 15 and child.exited() is None:
                    raise ValueError('owned Player failed to terminate; Job close is the final containment boundary')
            time.sleep(.1)
        code = child.exited()
        records.write_once(directory/'exit.json', {'schema':'gpos.player.windows-exit/1',
            'session_id':session['session_id'], 'nonce':session['nonce'], 'player':child.identity,
            'code':code, 'committed':committed, 'stop_reason':reason, 'graceful_requested':graceful,
            'forced':forced, 'observed_at':time.time()})
    finally:
        committed_event.set()  # do not leave a thread using a closed/reused handle
        watchdog.join()
        if child:
            child.close()
        # Last handle close kills any remaining descendants, including our process; no survivor is adopted.
        native.batch._close(job)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('only the fixed runtime directory and inherited Job handle are accepted')
    run(sys.argv[1], int(sys.argv[2]))
