"""TEST-ONLY: a Python stand-in for the GPOS live bridge, speaking the same local file protocol.

It lets tests/test_unity_live.py drive the GPOS side of the live plane — attach and approval, lease binding,
deadlines, withdrawal, unknown outcomes, clean close, crash and recovery — deterministically and without Unity.
It is not evidence that the real bridge behaves this way: the C# core tests and the real synthetic-Editor tests
cover the bridge itself. Behaviour switches:

    mode = "normal"          serve every request
    mode = "stall"           claim nothing (the client must withdraw)
    mode = "claim-only"      claim requests but never answer them (the client must report an unknown outcome)
    mode = "interrupt"       answer Editor-state commands INTERRUPTED
    busy = "COMPILING"       refuse Editor-state commands EDITOR_BUSY
    decide = "approve" | "reject" | None   what the simulated Human does with each new proposal
    on_approve(proposal_id)  called right after a simulated approval (e.g. to take a conflicting lease)
    author_reply(command, args) -> (status, code, data)   the answer to a Scene-authoring command (Phase 2C-6B1);
                             every authoring call is recorded in `author_calls` as (command, args)
    protocol / bridge_version  what the bridge publishes (an earlier release's values make it incompatible)
"""

import datetime
import json
import os
import threading
import time
import uuid
from pathlib import Path

from gpos.tools import leases as lease_mod
from gpos.tools.unity import bridge_install as bi
from gpos.tools.unity import identity as ident
from gpos.tools.unity import live_ipc as ipc

EDITOR_COMMAND = "/Applications/Unity/Hub/Editor/{v}/Unity.app/Contents/MacOS/Unity -projectPath {p}"


AUTHORING = ("object-inspect", "component-types", "properties", "create-gameobject", "delete-gameobject", "set-parent",
             "set-gameobject", "set-transform", "add-component", "remove-component", "set-property", "save-scene")


def default_author_reply(command, args):
    return "OK", None, {"echo": command, "scene": {"path": args.get("scene") or "Assets/Scenes/Main.unity",
                                                   "dirty": command != "save-scene"}}


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


class FakeBridge:
    def __init__(self, root, unity_project, editor_version, pid=None, digest=None):
        self.root, self.project = Path(root).resolve(), Path(unity_project).resolve()
        self.editor_version = editor_version
        self.rel = ident.relative(self.root, self.project)
        self.key = ident.project_key(self.rel)
        self.live = ident.live_dir(self.root, self.key)
        self.pid = pid or (40000 + int(uuid.uuid4().hex[:4], 16))
        self.started = now_utc().replace(microsecond=0) - datetime.timedelta(seconds=30)
        self.boot = uuid.uuid4().hex
        self.digest = digest or bi.load_manifest()["package_digest"]
        self.alive, self.running = True, False
        self.mode, self.busy, self.decide, self.on_approve = "normal", None, None, None
        self.session = self.owner = self.proposal_bound = ""
        self.phase = "EDIT"
        self.proposals, self.executed, self.claimed_ids = {}, [], []
        self.author_calls, self.author_reply = [], None
        self.protocol, self.bridge_version = bi.PROTOCOL, bi.BRIDGE_VERSION
        self.lock = threading.Lock()

    # ------------------------------------------------------------ process identity seam

    def facts(self):
        if not self.alive:
            return ("GONE", None, "")
        return ("ALIVE", self.started, EDITOR_COMMAND.format(v=self.editor_version, p=self.project))

    # ------------------------------------------------------------ lifecycle

    def start(self):
        for d in ("requests", "claimed", "responses", "withdrawn", "rejected"):
            (self.live / d).mkdir(parents=True, exist_ok=True)
        self.running = True
        self.publish("READY")
        self.beat()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        return self

    def loop(self):
        last = 0.0
        while self.running:
            if time.monotonic() - last > 0.2:
                self.beat()
                last = time.monotonic()
            if self.mode != "stall":
                self.serve()
            time.sleep(0.01)

    def stop(self):
        self.running = False
        if hasattr(self, "thread"):
            self.thread.join(timeout=5)

    def crash(self):
        """The Editor dies: nothing more is written, the process is gone."""
        self.stop()
        self.alive = False

    def close(self):
        """The Human quits Unity normally: CLOSED is published, then the process is gone."""
        self.stop()
        self.publish("CLOSED")
        self.alive = False

    def identity(self):
        return {"protocol": self.protocol, "bridge_version": self.bridge_version, "package_digest": self.digest,
                "boot_id": self.boot, "generation": 1, "editor_pid": self.pid,
                "editor_started_utc": self.started.isoformat().replace("+00:00", "Z"),
                "editor_version": self.editor_version, "gpos_root": str(self.root), "project_path": str(self.project),
                "project_rel": self.rel, "project_key": self.key}

    def write(self, name, data):
        tmp = self.live / f".tmp-{uuid.uuid4().hex}"
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.live / name)

    def publish(self, state):
        self.write("bridge.json", dict(self.identity(), state=state, session_id=self.session, owner=self.owner,
                                       utc=now_utc().isoformat()))

    def beat(self):
        self.write("heartbeat.json", {"boot_id": self.boot, "generation": 1, "seq": 1, "utc": now_utc().isoformat(),
                                      "editor_pid": self.pid, "session_id": self.session,
                                      "state": {"phase": self.phase, "focused": False}})

    # ------------------------------------------------------------ protocol

    def respond(self, rid, status, code=None, data=None):
        body = json.dumps({"schema": ipc.RESPONSE_SCHEMA, "request_id": rid, "status": status, "code": code,
                           "message": None, "boot_id": self.boot, "generation": 1, "session_id": self.session,
                           "utc": now_utc().isoformat(), "data": data})
        tmp = self.live / "responses" / f".tmp-{uuid.uuid4().hex}"
        tmp.write_text(body)
        try:
            os.link(tmp, self.live / "responses" / f"{rid}.json")
        finally:
            os.unlink(tmp)

    def serve(self):
        for name in sorted(os.listdir(self.live / "requests")):
            if name.startswith("."):
                continue
            rid = name[:-5]
            try:
                os.rename(self.live / "requests" / name, self.live / "claimed" / name)
            except FileNotFoundError:
                continue
            self.claimed_ids.append(rid)
            if self.mode == "claim-only":
                continue
            req = json.loads((self.live / "claimed" / name).read_text())
            with self.lock:
                self.handle(rid, req)

    def handle(self, rid, req):
        deadline = datetime.datetime.fromisoformat(req["start_deadline_utc"].replace("Z", "+00:00"))
        if now_utc() > deadline:
            return self.respond(rid, "REFUSED", "LIVE_REQUEST_EXPIRED")
        if req["boot_id"] != self.boot:
            return self.respond(rid, "REFUSED", "BOOT_MISMATCH")
        cmd, args, owner = req["command"], req["args"], req["owner"]
        if cmd in ("unbind", "inspect", "enter-playmode", "exit-playmode", "pause", "resume") + AUTHORING:
            if not self.session:
                return self.respond(rid, "REFUSED", "SESSION_NOT_BOUND")
            if req["session_id"] != self.session or owner != self.owner:
                return self.respond(rid, "REFUSED", "SESSION_MISMATCH")
        if cmd in ("enter-playmode", "exit-playmode", "pause", "resume"):
            if self.mode == "interrupt":
                return self.respond(rid, "INTERRUPTED", "NOT_REPLAYED")
            if self.busy:
                return self.respond(rid, "REFUSED", "EDITOR_BUSY")
        if cmd in AUTHORING:
            if self.mode == "interrupt":
                return self.respond(rid, "INTERRUPTED", "NOT_REPLAYED")
            if self.busy or self.phase != "EDIT":
                return self.respond(rid, "REFUSED", "EDITOR_BUSY")
            self.author_calls.append((cmd, args))
            status, code, data = (self.author_reply or default_author_reply)(cmd, args)
            return self.respond(rid, status, code, data)
        getattr(self, "cmd_" + cmd.replace("-", "_"))(rid, args, owner)

    def cmd_status(self, rid, args, owner):
        self.respond(rid, "OK", None, dict(self.identity(), session_id=self.session))

    def propose(self, rid, args, owner, kind, extra):
        if self.session:
            return self.respond(rid, "REFUSED", "SESSION_ALREADY_BOUND")
        if args["project_key"] != self.key:
            return self.respond(rid, "REFUSED", "PROJECT_MISMATCH")
        p = dict(id=args["proposal_id"], kind=kind, owner=owner, state="PENDING", **extra)
        self.proposals[p["id"]] = p
        self.respond(rid, "OK", "PENDING_HUMAN_APPROVAL", {"proposal_id": p["id"], "state": "PENDING"})
        if self.decide:
            threading.Timer(0.3, self.human, (p["id"], self.decide == "approve")).start()

    def human(self, proposal_id, approve):
        with self.lock:
            p = self.proposals[proposal_id]
            if p["state"] != "PENDING":
                return
            p["state"] = "APPROVED" if approve else "REJECTED"
        if approve and self.on_approve:
            self.on_approve(proposal_id)

    def cmd_propose_attach(self, rid, args, owner):
        self.propose(rid, args, owner, "ATTACH", {"session_id": args["session_id"]})

    def cmd_propose_recovery(self, rid, args, owner):
        lease = lease_mod.holder(self.root, "unity", f"EDITOR_PROJECT:{self.root}") or {}
        session = lease.get("session") or {}
        if session.get("session_id") != args["stale_session_id"]:
            return self.respond(rid, "REFUSED", "NO_STALE_SESSION")
        self.propose(rid, args, owner, "RECOVER", {"stale_session_id": session.get("session_id"),
                                                    "stale_boot_id": session.get("boot_id"),
                                                    "stale_owner": lease.get("owner_id")})

    def status_of(self, rid, args, owner, kind):
        p = self.proposals.get(args["proposal_id"])
        if not p or p["owner"] != owner or p["kind"] != kind:
            return self.respond(rid, "REFUSED", "NO_SUCH_PROPOSAL")
        self.respond(rid, "OK", None, {"proposal_id": p["id"], "state": p["state"], "kind": kind})

    def cmd_attach_status(self, rid, args, owner):
        self.status_of(rid, args, owner, "ATTACH")

    def cmd_recovery_status(self, rid, args, owner):
        self.status_of(rid, args, owner, "RECOVER")

    def cmd_abandon_proposal(self, rid, args, owner):
        p = self.proposals.get(args["proposal_id"])
        if not p:
            return self.respond(rid, "REFUSED", "NO_SUCH_PROPOSAL")
        before = p["state"]
        if before in ("PENDING", "APPROVED"):
            p["state"] = "ABANDONED"
        self.respond(rid, "OK", None, {"before": before, "state": p["state"]})

    def grant(self, args, owner, kind):
        p = self.proposals.get(args["proposal_id"])
        if not p or p["kind"] != kind or p["owner"] != owner:
            return None, "NO_SUCH_PROPOSAL"
        if p["state"] == "PENDING":
            return None, "NOT_APPROVED"
        if p["state"] in ("CONSUMED", "ABANDONED"):
            return None, "GRANT_CONSUMED"
        return (p, None) if p["state"] == "APPROVED" else (None, "NOT_APPROVED")

    def cmd_bind(self, rid, args, owner):
        p, problem = self.grant(args, owner, "ATTACH")
        if problem:
            return self.respond(rid, "REFUSED", problem)
        lease = lease_mod.holder(self.root, "unity", f"EDITOR_PROJECT:{self.root}") or {}
        session = lease.get("session") or {}
        if (lease.get("scope"), lease.get("owner_id"), session.get("session_id"), session.get("proposal_id"),
                session.get("boot_id"), session.get("project_key")) != \
                ("SESSION", owner, args["session_id"], p["id"], self.boot, self.key):
            return self.respond(rid, "REFUSED", "LEASE_NOT_HELD")
        p["state"] = "CONSUMED"
        self.session, self.owner, self.proposal_bound = args["session_id"], owner, p["id"]
        self.publish("READY")
        self.beat()
        self.respond(rid, "OK", "ATTACHED", dict(self.identity(), session_id=self.session))

    def cmd_consume_recovery(self, rid, args, owner):
        p, problem = self.grant(args, owner, "RECOVER")
        if problem:
            return self.respond(rid, "REFUSED", problem)
        p["state"] = "CONSUMED"
        self.respond(rid, "OK", "RECOVERY_GRANTED", {"stale_session_id": p["stale_session_id"],
                                                     "stale_boot_id": p["stale_boot_id"],
                                                     "stale_owner": p["stale_owner"], "approving_boot_id": self.boot})

    def cmd_unbind(self, rid, args, owner):
        self.session = self.owner = ""
        self.publish("READY")
        self.beat()
        self.respond(rid, "OK", "DETACHED")

    def cmd_inspect(self, rid, args, owner):
        self.respond(rid, "OK", None, {"editor": {"phase": self.phase, "focused": False}, "scene_count": 1,
                                       "scenes": [{"name": "Untitled", "path": "", "roots": []}]})

    def editor_state(self, rid, command, precondition, after):
        if self.phase not in precondition:
            code = {"enter-playmode": "ALREADY_PLAYING", "pause": "NOT_PLAYING", "resume": "NOT_PAUSED",
                    "exit-playmode": "NOT_PLAYING"}[command]
            return self.respond(rid, "REFUSED", code)
        self.executed.append(command)
        self.phase = after
        self.respond(rid, "OK", None, {"state": {"phase": after}, "transitions": "ExitingEditMode,EnteredPlayMode"})

    def cmd_enter_playmode(self, rid, args, owner):
        self.editor_state(rid, "enter-playmode", ("EDIT",), "PLAYING")

    def cmd_exit_playmode(self, rid, args, owner):
        self.editor_state(rid, "exit-playmode", ("PLAYING", "PAUSED"), "EDIT")

    def cmd_pause(self, rid, args, owner):
        self.editor_state(rid, "pause", ("PLAYING",), "PAUSED")

    def cmd_resume(self, rid, args, owner):
        self.editor_state(rid, "resume", ("PAUSED",), "PLAYING")
