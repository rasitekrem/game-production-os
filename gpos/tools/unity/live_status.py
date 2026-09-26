"""Unity live session status: bridge facts, process identity and the session classification (Phase 2C-6A).

The foundation stores and verifies SESSION ownership; what "live" or "stale" means for a Unity Editor session is
decided here, from facts only:

* the bridge's own files (`bridge.json`, `heartbeat.json`, `session.json`), read bounded and never through a link;
* the SESSION lease the foundation holds;
* the identity of the Editor process the lease names, read with a fixed `ps` through the audited process boundary:
  a process is the same one only when its start time matches the recorded start time and it runs a Unity Hub Editor.

Classifications of a session:

    LIVE          the recorded Editor process is proven alive, the bridge in that boot is fresh and bound to it
    UNRESPONSIVE  the process is alive or cannot be proven gone, but the heartbeat is old or files are unreadable
    CLOSED        the bridge published CLOSED for that boot and session, and the process is proven gone
    STALE         proven only: the process is gone (without a matching CLOSED), its id was reused, another bridge
                  boot now serves the project, or the bridge in that boot is no longer bound to the session

Heartbeat age alone is never STALE. When nothing can be proven, the answer is UNRESPONSIVE. CLOSED is useful but
never authoritative on its own: it counts only together with a process proven gone.
"""

import datetime
import re
import time
from pathlib import Path

from .. import process as proc
from . import live_ipc as ipc

FRESH_SECONDS = 20.0
BINDING_GRACE_SECONDS = 60.0
START_TOLERANCE_SECONDS = 1.5
PS = "/bin/ps"
HUB_EDITOR = re.compile(r"^/Applications/Unity/Hub/Editor/[^/]+/Unity\.app/Contents/MacOS/Unity(?: |$)")
PS_LINE = re.compile(r"^\s*([A-Z][a-z]{2} [A-Z][a-z]{2} +\d{1,2} \d\d:\d\d:\d\d \d{4})\s+(\S.*)$")
LIVE, UNRESPONSIVE, CLOSED, STALE = "LIVE", "UNRESPONSIVE", "CLOSED", "STALE"
ALIVE, GONE, REUSED, UNKNOWN = "ALIVE", "GONE", "REUSED", "UNKNOWN"


def parse_utc(text):
    if not isinstance(text, str):
        return None
    try:
        t = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # .NET writes seven fractional digits; Python accepts at most six
        m = re.match(r"^(.*\.\d{6})\d*(Z|[+-]\d\d:\d\d)$", text)
        if not m:
            return None
        try:
            t = datetime.datetime.fromisoformat(m.group(1) + m.group(2).replace("Z", "+00:00"))
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def ps_spec(pid, cwd):
    return proc.ToolProcessSpec(executable=PS, argv=("-o", "lstart=,command=", "-p", str(int(pid))), cwd=str(cwd),
                                timeout=10.0, capture_bytes=64 * 1024,
                                env=proc.EnvironmentPolicy(inherit=("PATH",),
                                                           overrides=(("LC_ALL", "C"), ("LANG", "C"), ("TZ", "UTC"))))


def process_facts(run, pid, cwd):
    """(ALIVE | GONE | UNKNOWN, start time or None, command line). Read-only: sends nothing to the process."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return UNKNOWN, None, ""
    try:
        out = run(ps_spec(pid, cwd))
    except proc.ProcessSpecError:
        return UNKNOWN, None, ""
    text = bytes(out.raw_stdout).decode("utf-8", errors="replace").strip()
    if out.timed_out or out.truncated:
        return UNKNOWN, None, ""
    if out.exit_code == 1 and not text:
        return GONE, None, ""
    m = PS_LINE.match(text)
    if out.exit_code != 0 or not m:
        return UNKNOWN, None, ""
    try:
        started = datetime.datetime.strptime(" ".join(m.group(1).split()), "%a %b %d %H:%M:%S %Y").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return UNKNOWN, None, ""
    return ALIVE, started, m.group(2)


def identity(facts, recorded_start):
    """ALIVE (the same Editor), GONE, REUSED (another process now has the id) or UNKNOWN."""
    state, started, command = facts
    if state == GONE:
        return GONE
    if state != ALIVE:
        return UNKNOWN
    recorded = parse_utc(recorded_start)
    if recorded is None:
        return UNKNOWN
    if abs((started - recorded).total_seconds()) > START_TOLERANCE_SECONDS or not HUB_EDITOR.match(command):
        return REUSED
    return ALIVE


def age(path, now=None):
    try:
        return (now or time.time()) - Path(path).stat().st_mtime
    except OSError:
        return None


def read_state(live_dir):
    """The bridge's three state files (each a dict or None) and the heartbeat age; problems are reported, not raised."""
    live_dir, out, problems = Path(live_dir), {}, []
    for name in ("bridge", "heartbeat", "session"):
        try:
            out[name] = ipc.read_bounded(live_dir / f"{name}.json")
        except ipc.ChannelProblem as exc:
            out[name] = None
            problems.append(f"{name}.json: {exc}")
    out["heartbeat_age"] = age(live_dir / "heartbeat.json")
    out["problems"] = problems
    return out


def classify(holder, state, lease_editor, bridge_editor, now_epoch=None):
    """(classification, [reason]) of the session the SESSION lease `holder` binds.

    `lease_editor` / `bridge_editor` are identity() results for the process the lease records and the process the
    bridge publishes."""
    session = holder.get("session") or {}
    sid, boot = session.get("session_id"), session.get("boot_id")
    bridge, heartbeat, beat_age = state.get("bridge") or {}, state.get("heartbeat") or {}, state.get("heartbeat_age")
    fresh = beat_age is not None and beat_age <= FRESH_SECONDS
    if lease_editor in (GONE, REUSED):
        if (lease_editor == GONE and bridge.get("boot_id") == boot and bridge.get("state") == "CLOSED"
                and bridge.get("session_id") == sid):
            return CLOSED, ["the Editor published CLOSED for this session and its process is gone"]
        return STALE, ["the Editor process recorded by the session is gone" if lease_editor == GONE
                       else "the recorded Editor process id now belongs to another process"]
    if (bridge.get("boot_id") and bridge.get("boot_id") != boot and fresh and bridge_editor == ALIVE
            and heartbeat.get("boot_id") == bridge.get("boot_id")):
        return STALE, ["another Editor boot of the bridge now serves this project"]
    if lease_editor == ALIVE and bridge.get("boot_id") == boot and bridge.get("state") == "CLOSED":
        return UNRESPONSIVE, ["the Editor published CLOSED but its process is still running (it may be quitting)"]
    if lease_editor == ALIVE and fresh and bridge.get("boot_id") == boot and heartbeat.get("boot_id") == boot:
        if heartbeat.get("session_id") == sid and bridge.get("session_id") == sid:
            return LIVE, []
        acquired = _epoch(holder.get("acquired_at"))
        if acquired is not None and (now_epoch or time.time()) - acquired < BINDING_GRACE_SECONDS:
            return UNRESPONSIVE, ["the session is being bound"]
        return STALE, ["the bridge in the recorded Editor boot is no longer bound to this session"]
    reasons = ["the heartbeat is older than %d s" % FRESH_SECONDS if not fresh else "the bridge files do not match"]
    if lease_editor == UNKNOWN:
        reasons.append("the Editor process identity could not be proven either way")
    return UNRESPONSIVE, reasons


def _epoch(text):
    t = parse_utc(text)
    return t.timestamp() if t else None
