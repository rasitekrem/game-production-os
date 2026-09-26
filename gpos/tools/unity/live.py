"""The Unity live plane (Phase 2C-6A): a fixed Editor bridge and one Human-approved session per GPOS project.

Nine capabilities, and nothing generic:

    unity.live-install-bridge   write the audited bridge package into a closed project            MUTATING, EXECUTION
    unity.live-status           bridge and session facts from files and process identity          READ_ONLY, no session
    unity.live-attach           Human approval in the Editor, then the SESSION lease, then bind   SESSION_OPEN
    unity.live-detach           unbind and release, clean-close release, or Human-approved recovery SESSION_CLOSE
    unity.live-inspect          bounded structured Editor facts                                   READ_ONLY, SESSION_REQUIRED
    unity.live-enter-playmode / -pause / -resume / -exit-playmode   Editor Play Mode state only   SESSION_REQUIRED

GPOS never starts, quits, focuses or restarts an Editor, never injects input and never runs code in it. The bridge
answers a closed protocol over local files (live_ipc). None of these capabilities produces evidence: Play Mode here
is the Editor's state, never TARGET_RUNTIME, and SUCCESS establishes no player-loop, frame or gameplay progress.
"""

import time
import uuid
from pathlib import Path

from .. import diagnostics as dg
from .. import leases as lease_mod
from ..execution import AdapterOutcome
from . import bridge_install as bi
from . import identity as ident
from . import live_ipc as ipc
from . import live_status as ls
from . import project as up

ADAPTER_ID = "unity"
INSTALL = "unity.live-install-bridge"
STATUS = "unity.live-status"
ATTACH = "unity.live-attach"
DETACH = "unity.live-detach"
INSPECT = "unity.live-inspect"
ENTER, PAUSE, RESUME, EXIT = ("unity.live-enter-playmode", "unity.live-pause", "unity.live-resume",
                              "unity.live-exit-playmode")
PLAYMODE = {ENTER: "enter-playmode", PAUSE: "pause", RESUME: "resume", EXIT: "exit-playmode"}
CAPABILITY_IDS = (INSTALL, STATUS, ATTACH, DETACH, INSPECT, ENTER, PAUSE, RESUME, EXIT)

POLL_SECONDS = 0.5
MIN_EXPIRY, MAX_EXPIRY = 30, 900
STATUS_WAIT = 10.0
COMMAND_WAIT = 30.0
PLAYMODE_LIMITATION = (
    "Editor Play Mode state only: SUCCESS means the Unity Editor reached this Play Mode state. It establishes no "
    "player-loop, frame, gameplay, physics, rendering or presentation progress, no device behaviour and nothing about "
    "TARGET_RUNTIME; while the Editor is unfocused, Unity may throttle it and gameplay progress is not guaranteed.")
REFUSALS = {
    "EDITOR_BUSY": "EDITOR_BUSY", "RATE_LIMITED": "EDITOR_BUSY", "JOURNAL_FULL": "EDITOR_BUSY",
    "ALREADY_PLAYING": "LIVE_STATE_REFUSED", "NOT_PLAYING": "LIVE_STATE_REFUSED",
    "ALREADY_PAUSED": "LIVE_STATE_REFUSED", "NOT_PAUSED": "LIVE_STATE_REFUSED",
    "LIVE_REQUEST_EXPIRED": "LIVE_REQUEST_EXPIRED",
    "SESSION_NOT_BOUND": "LIVE_SESSION_MISMATCH", "SESSION_MISMATCH": "LIVE_SESSION_MISMATCH",
    "OWNER_MISMATCH": "LIVE_SESSION_MISMATCH", "BOOT_MISMATCH": "LIVE_SESSION_MISMATCH",
    "NOT_APPROVED": "LIVE_GRANT_INVALID", "GRANT_EXPIRED": "LIVE_GRANT_INVALID", "GRANT_CONSUMED": "LIVE_GRANT_INVALID",
    "REJECTED": "LIVE_GRANT_INVALID", "WRONG_KIND": "LIVE_GRANT_INVALID", "NO_SUCH_PROPOSAL": "LIVE_GRANT_INVALID",
    "LEASE_NOT_HELD": "LIVE_BIND_REFUSED", "SESSION_ALREADY_BOUND": "LIVE_BIND_REFUSED",
    "PROJECT_MISMATCH": "LIVE_PROJECT_IDENTITY_MISMATCH", "NO_STALE_SESSION": "LIVE_BIND_REFUSED",
    "TOO_MANY_PROPOSALS": "EDITOR_BUSY", "DUPLICATE_PROPOSAL": "LIVE_PROTOCOL_ERROR",
}

# Every status, code, state and phase the bridge protocol and the live plane report (documentation vocabulary).
PROTOCOL_VOCABULARY = frozenset(REFUSALS) | frozenset({
    "OK", "REFUSED", "FAILED", "INTERRUPTED", "NOT_REPLAYED", "PENDING_HUMAN_APPROVAL", "ATTACHED", "DETACHED",
    "RECOVERY_GRANTED", "PLAYMODE_ENTER_ABORTED", "PLAYMODE_ENTER_NOT_STARTED", "TRANSITION_TIMEOUT",
    "MALFORMED_REQUEST", "UNSUPPORTED_SCHEMA", "REQUEST_ID_MISMATCH", "REQUEST_TOO_LARGE", "UNKNOWN_COMMAND",
    "BAD_ARGUMENTS", "DUPLICATE_REQUEST", "RESPONSE_TOO_LARGE", "BRIDGE_INTERNAL_ERROR",
    "PENDING", "APPROVED", "REJECTED", "EXPIRED", "CONSUMED", "ABANDONED", "GRANT_EXPIRED", "ATTACH", "RECOVER",
    "EDIT", "PLAYING", "PAUSED", "ENTERING_PLAYMODE", "EXITING_PLAYMODE", "COMPILING", "UPDATING", "RELOADING",
    "QUITTING", "PENDING_OPERATION", "INITIALIZING", "READY", "CLOSED",
    ls.LIVE, ls.UNRESPONSIVE, ls.STALE, ls.CLOSED, bi.ABSENT, bi.EXACT, bi.UNTRUSTED,
    ipc.RESPONDED, ipc.WITHDRAWN, ipc.UNKNOWN})


def _diag(code, message, cap, details=None):
    return dg.make(code, message, ADAPTER_ID, cap, None, details)


def _refuse(cap, code, message, details=None, **kw):
    return AdapterOutcome(ok=True, diagnostics=(_diag(code, message, cap, details),), **kw)


class Refused(Exception):
    """A precondition failed; carries the AdapterOutcome to return."""

    def __init__(self, outcome):
        super().__init__(outcome.diagnostics[0].message if outcome.diagnostics else "refused")
        self.outcome = outcome


class Live:
    """One live execution: the project, its live directory and the bridge serving it."""

    def __init__(self, request, context, facts=None, sleep=time.sleep, monotonic=time.monotonic):
        self.request, self.context, self.cap = request, context, request.capability_id
        self.root = Path(context.project_root)
        self.owner = lease_mod.session_owner(request.actor)
        self.sleep, self.monotonic = sleep, monotonic
        self._facts = facts or (lambda pid: ls.process_facts(context.run, pid, self.root))
        try:
            self.project, self.summary = up.preflight(self.root, (request.inputs or {}).get("unity_project"))
        except up.ProjectProblem as problem:
            raise Refused(_refuse(self.cap, "ENGINE_PROJECT_UNSUPPORTED", f"{problem.rule}: {problem.message}"))
        self.rel = ident.relative(self.root, self.project)
        self.key = ident.project_key(self.rel)
        self.live_dir = ident.live_dir(self.root, self.key)
        self.resource = f"EDITOR_PROJECT:{self.root.resolve()}"

    # ------------------------------------------------------------ facts

    def state(self):
        return ls.read_state(self.live_dir)

    def editor(self, pid, started):
        return ls.identity(self._facts(pid), started)

    def holder(self):
        return lease_mod.holder(self.root, ADAPTER_ID, self.resource)

    def installed(self):
        try:
            manifest = bi.verify_source()
        except bi.BridgeSourceCorrupt as exc:
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_SOURCE_CORRUPT", str(exc)))
        state, differences = bi.inspect_target(self.project, manifest)
        return manifest, state, differences

    def require_installed(self):
        manifest, state, differences = self.installed()
        if state == bi.ABSENT:
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_ABSENT", f"the audited bridge is not installed in "
                                                                  f"{self.summary['unity_project']!r}; run {INSTALL} "
                                                                  f"while the project is closed"))
        if state == bi.UNTRUSTED:
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_UNTRUSTED", "the installed bridge is not the audited GPOS "
                                                                     "bridge; it is never overwritten or repaired",
                                  {"differences": differences[:20]}))
        return manifest

    def bridge(self, manifest, state=None):
        """The READY, compatible bridge serving exactly this project (dict), or Refused."""
        spelling = ident.root_spelling_problem(self.root)
        if spelling:
            raise Refused(_refuse(self.cap, "LIVE_PROJECT_IDENTITY_MISMATCH", spelling))
        state = state or self.state()
        b = state["bridge"]
        if not b or b.get("state") not in ("READY",) or state["problems"]:
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_UNAVAILABLE", "no running bridge is ready for this project; "
                                                                       "open it in the Unity Editor",
                                  {"problems": state["problems"]}))
        if (b.get("protocol"), b.get("bridge_version"), b.get("package_digest")) != \
                (bi.PROTOCOL, bi.BRIDGE_VERSION, manifest["package_digest"]):
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_INCOMPATIBLE", "the running bridge is not the audited bridge "
                                                                        "this release installs"))
        if b.get("editor_version") != self.summary["editor_version"]:
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_INCOMPATIBLE",
                                  f"the running Editor is {b.get('editor_version')!r}; the project requires "
                                  f"{self.summary['editor_version']!r}"))
        if not (ident.same_directory(b.get("gpos_root"), self.root) and
                ident.same_directory(b.get("project_path"), self.project) and b.get("project_key") == self.key):
            raise Refused(_refuse(self.cap, "LIVE_PROJECT_IDENTITY_MISMATCH", "the bridge in this live directory "
                                                                              "serves another GPOS root or project"))
        age = state["heartbeat_age"]
        beat = state["heartbeat"] or {}
        if age is None or age > ls.FRESH_SECONDS or beat.get("boot_id") != b.get("boot_id"):
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_UNAVAILABLE", "the bridge heartbeat is not fresh"))
        if self.editor(b.get("editor_pid"), b.get("editor_started_utc")) != ls.ALIVE:
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_UNAVAILABLE", "the Editor process the bridge publishes could "
                                                                       "not be proven to be that running Editor"))
        return b

    def channel(self):
        try:
            return ipc.Channel(self.live_dir)
        except ipc.ChannelProblem as exc:
            raise Refused(_refuse(self.cap, "LIVE_BRIDGE_UNAVAILABLE", str(exc)))

    def call(self, channel, command, args, boot, session_id=None, wait=COMMAND_WAIT, start=ipc.DEFAULT_START_SECONDS):
        try:
            return ipc.call(channel, command, args, self.owner, boot, session_id, wait=wait, start_seconds=start,
                            sleep=self.sleep, monotonic=self.monotonic)
        except ipc.ChannelProblem as exc:
            raise Refused(_refuse(self.cap, "LIVE_PROTOCOL_ERROR", str(exc)))

    def classify(self, holder, state=None):
        state = state or self.state()
        session = holder.get("session") or {}
        b = state["bridge"] or {}
        lease_editor = self.editor(session.get("editor_pid"), session.get("editor_started_utc"))
        bridge_editor = self.editor(b.get("editor_pid"), b.get("editor_started_utc")) if b else ls.UNKNOWN
        cls, reasons = ls.classify(holder, state, lease_editor, bridge_editor)
        return cls, reasons, lease_editor, state


# ---------------------------------------------------------------- dispatch

def execute(request, context, facts=None, sleep=time.sleep, monotonic=time.monotonic):
    cap = request.capability_id
    try:
        live = Live(request, context, facts, sleep, monotonic)
        if cap == INSTALL:
            return _install(live)
        if cap == STATUS:
            return _status(live)
        if cap == ATTACH:
            return _attach(live)
        if cap == DETACH:
            return _detach(live)
        return _session_command(live)
    except Refused as refused:
        return refused.outcome


def _mapped(cap, result, what, mutating, success=None, data=None):
    """An AdapterOutcome for a call result. `success` builds the SUCCESS outcome from an OK response."""
    if result.outcome == ipc.WITHDRAWN:
        return _refuse(cap, "LIVE_REQUEST_WITHDRAWN", f"{what}: the Editor did not claim the request in time; it was "
                                                      f"withdrawn and never executed")
    if result.outcome == ipc.UNKNOWN or result.status == "INTERRUPTED":
        return _refuse(cap, "LIVE_OUTCOME_UNKNOWN", f"{what}: the Editor claimed the request but its final effect is "
                                                    f"unknown; it is not retried — re-read unity.live-status",
                       {"request_id": result.request_id}, mutation_performed=mutating, data=data)
    r = result.response
    if r["status"] == "OK":
        return success(r)
    if r["status"] == "FAILED":
        code = "LIVE_TRANSITION_FAILED" if r["code"] in ("PLAYMODE_ENTER_ABORTED", "PLAYMODE_ENTER_NOT_STARTED",
                                                        "TRANSITION_TIMEOUT") else "LIVE_PROTOCOL_ERROR"
        return _refuse(cap, code, f"{what}: {r['code']}", {"bridge_code": r["code"], "request_id": result.request_id},
                       mutation_performed=mutating and code == "LIVE_TRANSITION_FAILED",
                       data=dict(data or {}, **_bridge_data(r)))
    return _refuse(cap, REFUSALS.get(r["code"], "LIVE_PROTOCOL_ERROR"), f"{what}: the bridge refused ({r['code']})",
                   {"bridge_code": r["code"], "request_id": result.request_id}, data=data)


def _bridge_data(r):
    data = r.get("data") or {}
    return {k: data[k] for k in ("state", "transitions") if k in data}


# ---------------------------------------------------------------- install

def _install(live):
    cap, project = live.cap, live.project
    lock = project / "Temp" / "UnityLockfile"
    if lock.exists() or lock.is_symlink():
        return _refuse(cap, "ENGINE_PROJECT_LOCKED", "the Unity project is open (Temp/UnityLockfile exists); the bridge "
                                                     "is installed only into a closed project")
    manifest, state, differences = live.installed()
    data = {"unity_project": live.summary["unity_project"], "package_id": bi.PACKAGE_ID,
            "bridge_version": bi.BRIDGE_VERSION, "protocol": bi.PROTOCOL, "package_digest": manifest["package_digest"],
            "installed_state": state}
    if state == bi.UNTRUSTED:
        return _refuse(cap, "LIVE_BRIDGE_UNTRUSTED", f"Packages/{bi.PACKAGE_ID} exists and is not the audited bridge; "
                                                     f"it is never overwritten or repaired",
                       {"differences": differences[:20]}, data=data)
    if state == bi.EXACT:
        return AdapterOutcome(ok=True, data=data, diagnostics=(_diag(
            "LIVE_BRIDGE_ALREADY_INSTALLED", "the audited bridge is already installed byte for byte", cap),))
    if live.context.dry_run:
        return AdapterOutcome(data=data, plan=(
            f"would write the {len(manifest['files'])} files of {bi.PACKAGE_ID} {bi.BRIDGE_VERSION} into "
            f"Packages/{bi.PACKAGE_ID} of the closed project with one atomic rename",
            "nothing else in the project changes; Packages/manifest.json is not edited"))
    try:
        bi.install(live.root, project, manifest)
    except bi.BridgeSourceCorrupt as exc:
        return _refuse(cap, "LIVE_BRIDGE_SOURCE_CORRUPT", str(exc), data=data)
    except ValueError as exc:
        return _refuse(cap, "LIVE_BRIDGE_UNTRUSTED", "the target changed while the bridge was being installed; nothing "
                                                     "was overwritten", {"differences": list(exc.args[0])[:20]}, data=data)
    except OSError as exc:
        return AdapterOutcome(ok=False, detail=f"the bridge could not be installed ({type(exc).__name__}: {exc}); "
                                               f"nothing was moved into Packages/", data=data)
    return AdapterOutcome(ok=True, mutation_performed=True, data=dict(data, installed_state=bi.EXACT),
                          diagnostics=(_diag("LIVE_BRIDGE_INSTALLED", f"installed {bi.PACKAGE_ID} "
                                                                      f"{bi.BRIDGE_VERSION}; open the project in Unity",
                                             cap),))


# ---------------------------------------------------------------- status

def _status(live):
    manifest, installed, differences = live.installed()
    state = live.state()
    b, beat = state["bridge"] or {}, state["heartbeat"] or {}
    bridge = {"installed": installed, "differences": differences[:20], "running_state": None, "boot_id": None,
              "compatible": None, "heartbeat_age_seconds": None, "problems": state["problems"]}
    if b:
        bridge.update(running_state=b.get("state"), boot_id=b.get("boot_id"), editor_version=b.get("editor_version"),
                      compatible=(b.get("protocol"), b.get("bridge_version"), b.get("package_digest"))
                      == (bi.PROTOCOL, bi.BRIDGE_VERSION, manifest["package_digest"]),
                      heartbeat_age_seconds=None if state["heartbeat_age"] is None else round(state["heartbeat_age"], 1),
                      phase=(beat.get("state") or {}).get("phase"), focused=(beat.get("state") or {}).get("focused"),
                      attached_session_id=b.get("session_id") or None)
    holder = live.holder()
    session = None
    if holder and lease_mod.scope_of(holder) == lease_mod.SESSION:
        cls, reasons, _, _ = live.classify(holder, state)
        s = holder.get("session") or {}
        session = {"classification": cls, "reasons": reasons, "session_id": s.get("session_id"),
                   "owner": holder.get("owner_id"), "boot_id": s.get("boot_id"), "since": holder.get("acquired_at")}
    elif holder:
        session = {"classification": None, "writer": "EXECUTION", "owner": holder.get("owner_id")}
    return AdapterOutcome(ok=True, data={"unity_project": live.summary["unity_project"], "project_key": live.key,
                                         "bridge": bridge, "session": session})


# ---------------------------------------------------------------- attach

def _attach(live):
    cap = live.cap
    manifest = live.require_installed()
    holder = live.holder()
    if holder:
        if lease_mod.scope_of(holder) == lease_mod.SESSION:
            cls, reasons, _, _ = live.classify(holder)
            return _refuse(cap, "LIVE_SESSION_STALE" if cls == ls.STALE else "LIVE_SESSION_HELD",
                           f"a live session already holds this project ({cls}); detach it"
                           + (" through the Human-approved recovery" if cls == ls.STALE else ""),
                           {"session_id": (holder.get("session") or {}).get("session_id"),
                            "owner": holder.get("owner_id"), "classification": cls, "reasons": reasons})
        return _refuse(cap, "LEASE_CONFLICT", f"a writer ({holder.get('owner_id')!r}) holds this project")
    b = live.bridge(manifest)
    channel = live.channel()
    boot = b["boot_id"]
    session_id, proposal = uuid.uuid4().hex, uuid.uuid4().hex
    budget = float(live.context.timeout)
    deadline = live.monotonic() + budget
    expires = int(max(MIN_EXPIRY, min(MAX_EXPIRY, budget)))
    r = live.call(channel, "propose-attach", {"proposal_id": proposal, "session_id": session_id,
                                              "project_key": live.key, "expires_s": expires}, boot, wait=STATUS_WAIT)
    if r.status != "OK":
        return _mapped(cap, r, "the attach proposal", False)
    state = _await_decision(live, channel, "attach-status", proposal, boot, deadline)
    if state != "APPROVED":
        _abandon(live, channel, proposal, boot)
        if state == "REJECTED":
            return _refuse(cap, "LIVE_APPROVAL_REJECTED", "the Human rejected the attach request in the Unity Editor")
        return _refuse(cap, "LIVE_APPROVAL_NOT_GRANTED", f"no Human approved the attach request in the Unity Editor "
                                                         f"({state}); it was abandoned and can never be used")
    session = {"session_id": session_id, "proposal_id": proposal, "boot_id": boot, "project_key": live.key,
               "editor_pid": b.get("editor_pid"), "editor_started_utc": b.get("editor_started_utc")}
    lease, problems = live.context.sessions.open(session)
    if problems:
        _abandon(live, channel, proposal, boot)
        return AdapterOutcome(ok=True, diagnostics=tuple(problems))
    r = live.call(channel, "bind", {"proposal_id": proposal, "session_id": session_id}, boot, wait=COMMAND_WAIT)
    data = {"session_id": session_id, "proposal_id": proposal, "owner": live.owner, "boot_id": boot,
            "unity_project": live.summary["unity_project"]}
    if r.outcome == ipc.UNKNOWN or r.status == "INTERRUPTED":
        live.context.sessions.confirm()   # never leave a possibly bound Editor without its lease
        return _mapped(cap, r, "binding the session", True, data=data)
    if r.status != "OK":
        _abandon(live, channel, proposal, boot)
        return _mapped(cap, r, "binding the session", False, data=data)
    live.context.sessions.confirm()
    return AdapterOutcome(ok=True, mutation_performed=True, data=data, diagnostics=(_diag(
        "LIVE_SESSION_ATTACHED", f"session {session_id} is attached for {live.owner}", cap),))


def _await_decision(live, channel, command, proposal, boot, deadline):
    """PENDING until the Human decides in the Editor or the deadline passes. Returns the last known state."""
    state = "PENDING"
    while live.monotonic() < deadline:
        r = live.call(channel, command, {"proposal_id": proposal}, boot, wait=STATUS_WAIT)
        if r.status == "OK":
            state = (r.response.get("data") or {}).get("state", "PENDING")
            if state != "PENDING":
                return state
        elif r.status in ("REFUSED", "FAILED"):
            return r.code or "REFUSED"
        live.sleep(POLL_SECONDS)
    return "EXPIRED" if state == "PENDING" else state


def _abandon(live, channel, proposal, boot):
    try:
        live.call(channel, "abandon-proposal", {"proposal_id": proposal}, boot, wait=STATUS_WAIT)
    except Refused:
        pass


# ---------------------------------------------------------------- detach, clean close, recovery

def _detach(live):
    cap, record = live.cap, live.context.session
    session = record.get("session") or {}
    sid = session.get("session_id")
    cls, reasons, lease_editor, state = live.classify(record)
    own = record.get("owner_id") == live.owner
    if cls == ls.LIVE:
        if not own:
            return _refuse(cap, "LIVE_SESSION_MISMATCH", f"session {sid} is live and owned by "
                                                         f"{record.get('owner_id')!r}; only its owner detaches it")
        manifest = live.require_installed()
        b = live.bridge(manifest, state)
        r = live.call(live.channel(), "unbind", {}, b["boot_id"], sid, wait=COMMAND_WAIT)
        if r.status != "OK":
            return _mapped(cap, r, "unbinding the session", True)
        return _release(live, sid, "the bridge unbound the session")
    if cls == ls.CLOSED and own and _cleanly_closed(record, state, lease_editor):
        return _release(live, sid, "the Editor closed cleanly (CLOSED for this boot and session, process gone)")
    if cls in (ls.STALE, ls.CLOSED):
        return _recover(live, record, cls, reasons)
    return _refuse(cap, "LIVE_SESSION_UNRESPONSIVE", f"session {sid} is {cls}; it is neither detached nor recovered "
                                                     f"while its Editor may still be running", {"reasons": reasons})


def _cleanly_closed(record, state, lease_editor):
    session, b = record.get("session") or {}, state.get("bridge") or {}
    return (lease_editor == ls.GONE and b.get("state") == "CLOSED" and b.get("boot_id") == session.get("boot_id")
            and b.get("session_id") == session.get("session_id") and b.get("owner") == record.get("owner_id"))


def _release(live, sid, why):
    _, problems = live.context.sessions.close(sid)
    if problems:
        return AdapterOutcome(ok=True, diagnostics=tuple(problems))
    return AdapterOutcome(ok=True, mutation_performed=True, data={"session_id": sid, "released": True},
                          diagnostics=(_diag("LIVE_SESSION_DETACHED", f"session {sid} ended: {why}", live.cap),))


def _recover(live, record, cls, reasons):
    """A proven STALE (or not cleanly CLOSED) session ends only with a Human's recovery approval in a running Editor."""
    cap = live.cap
    session = record.get("session") or {}
    sid, stale_boot = session.get("session_id"), session.get("boot_id")
    try:
        manifest = live.require_installed()
        b = live.bridge(manifest)
    except Refused:
        return _refuse(cap, "LIVE_SESSION_STALE", f"session {sid} is {cls} and no trustworthy running bridge can ask a "
                                                  f"Human to approve its recovery; open the project in the Unity Editor, "
                                                  f"or resolve it outside GPOS", {"reasons": reasons})
    channel = live.channel()
    boot = b["boot_id"]
    proposal = uuid.uuid4().hex
    budget = float(live.context.timeout)
    deadline = live.monotonic() + budget
    r = live.call(channel, "propose-recovery", {"proposal_id": proposal, "project_key": live.key,
                                                "stale_session_id": sid,
                                                "expires_s": int(max(MIN_EXPIRY, min(MAX_EXPIRY, budget)))}, boot,
                  wait=STATUS_WAIT)
    if r.status != "OK":
        return _mapped(cap, r, "the recovery proposal", False)
    state = _await_decision(live, channel, "recovery-status", proposal, boot, deadline)
    if state != "APPROVED":
        _abandon(live, channel, proposal, boot)
        code = "LIVE_APPROVAL_REJECTED" if state == "REJECTED" else "LIVE_APPROVAL_NOT_GRANTED"
        return _refuse(cap, code, f"no Human approved recovering stale session {sid} ({state}); nothing was broken")
    r = live.call(channel, "consume-recovery", {"proposal_id": proposal}, boot, wait=COMMAND_WAIT)
    if r.status != "OK":
        return _mapped(cap, r, "the recovery grant", False)
    granted = r.response.get("data") or {}
    if granted.get("stale_session_id") != sid or granted.get("stale_boot_id") != stale_boot:
        return _refuse(cap, "LIVE_GRANT_INVALID", "the recovery grant names another session")
    reason = (f"Human-approved stale-session recovery in the Unity Editor: proposal {proposal}, approving Editor boot "
              f"{boot}; stale session {sid} of {record.get('owner_id')} (Editor boot {stale_boot}; {cls}: "
              f"{'; '.join(reasons)})")
    entry, problems = live.context.sessions.recover(record.get("token"), reason)
    if problems:
        return AdapterOutcome(ok=True, diagnostics=tuple(problems))
    return AdapterOutcome(ok=True, mutation_performed=True,
                          data={"session_id": sid, "recovered": True, "proposal_id": proposal, "attached": False},
                          diagnostics=(_diag("LIVE_SESSION_RECOVERED", f"stale session {sid} was recovered after Human "
                                                                       f"approval; no session is attached", cap),))


# ---------------------------------------------------------------- inspect and Play Mode

def _session_command(live):
    cap, record = live.cap, live.context.session
    sid = (record.get("session") or {}).get("session_id")
    cls, reasons, _, state = live.classify(record)
    if cls != ls.LIVE:
        code = "LIVE_SESSION_UNRESPONSIVE" if cls == ls.UNRESPONSIVE else "LIVE_SESSION_STALE"
        return _refuse(cap, code, f"session {sid} is {cls}", {"reasons": reasons})
    manifest = live.require_installed()
    b = live.bridge(manifest, state)
    channel = live.channel()
    if cap == INSPECT:
        r = live.call(channel, "inspect", {}, b["boot_id"], sid, wait=float(live.context.timeout))
        return _mapped(cap, r, "inspection", False,
                       success=lambda resp: AdapterOutcome(ok=True, data=resp.get("data")))
    command = PLAYMODE[cap]
    timeout = float(live.context.timeout)
    args = {"timeout_s": int(max(5, min(600, timeout)))} if command in ("enter-playmode", "exit-playmode") else {}
    wait = timeout + 10.0 if args else timeout
    r = live.call(channel, command, args, b["boot_id"], sid, wait=wait)
    data = {"limitation": PLAYMODE_LIMITATION}
    return _mapped(cap, r, command, True, data=data,
                   success=lambda resp: AdapterOutcome(ok=True, mutation_performed=True,
                                                       data=dict(data, **_bridge_data(resp))))
