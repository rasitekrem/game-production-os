"""Single-writer leases for stateful mutating tool operations.

Purpose: stop two GPOS tool operations from concurrently mutating the same stateful target — an
editor project, a device, a scene file. This is deliberately a small local mechanism, not a lock
service: there is no daemon, no distributed consensus and no network.

Shape:

* the lease is a file in the project's non-authoritative runtime area (`.game/gpos-runtime/leases/`);
  no mutable lease state is ever written into the canonical record area `.game/gpos/`, and nothing is
  written outside the project;
* the file name is a deterministic hash of (adapter id, resource id), so the same target always maps
  to the same lease regardless of how the path was spelled;
* acquisition is atomic: `O_CREAT | O_EXCL`. If the file exists, acquisition fails explicitly with
  LEASE_CONFLICT and the holder's metadata — there is no waiting, no retry loop and no force;
* release only ever removes a lease this process owns (matching owner id and token); releasing
  someone else's lease is refused;
* a lease that looks abandoned is reported as LEASE_STALE and **never** broken automatically.
  Recovery is an explicit, separately requested operation (`break_lease`) that records who broke
  what and why. Silent force-unlock does not exist here.

Read-only operations never take a lease. Read-only coexistence with a writer is a property of the
adapter's declared state model, not something the foundation assumes.

Scopes (Phase 2C-6A). An EXECUTION lease — the frozen behaviour — is held by one execution and
released when it ends. A SESSION lease is the same file on the same resource key, so the two always
conflict, but it outlives the command that took it: it binds a session id and the canonical owner
`KIND:ID`, and stays until the session is closed after verification (`release_session`) or broken
through the attributable, explicitly authorized `break_lease`. The process that took a SESSION lease
exits long before the session ends, so a SESSION lease is never judged stale from its process id;
what "stale" means for a live session belongs to the adapter that runs it. A lease file written
before scopes existed carries no `scope` field and is an EXECUTION lease.
"""

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import paths as tp

LEASES = "leases"
EXECUTION, SESSION = "EXECUTION", "SESSION"
SCOPES = (EXECUTION, SESSION)
SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
SESSION_OWNER = re.compile(r"^[A-Z][A-Z_]*:[A-Za-z0-9][A-Za-z0-9._@-]*$")


def session_owner(actor):
    """The canonical SESSION owner identity `KIND:ID` of a validated actor, or None when there is none.

    A SESSION lease binds the kind as well as the id, so `AGENT:x` and `HUMAN:x` are different owners.
    EXECUTION leases keep their frozen owner (the bare actor id) unchanged.
    """
    if actor is None or not isinstance(actor.kind, str) or not isinstance(actor.id, str):
        return None
    owner = f"{actor.kind}:{actor.id}"
    return owner if SESSION_OWNER.match(owner) else None


def scope_of(record):
    """EXECUTION or SESSION for a lease record; a record without a scope field is an EXECUTION lease."""
    return record.get("scope", EXECUTION) if isinstance(record, dict) else None


def resource_key(adapter_id, resource_id):
    """Deterministic identity of a lease target: same target, same key, always."""
    digest = hashlib.sha256(f"{adapter_id}\x00{resource_id}".encode("utf-8")).hexdigest()
    return f"{digest[:32]}.json"


def lease_dir(root):
    return tp.runtime_dir(root, LEASES)


def lease_path(root, adapter_id, resource_id):
    return lease_dir(root) / resource_key(adapter_id, resource_id)


@dataclass(frozen=True)
class Lease:
    adapter_id: str
    resource_id: str
    owner_id: str
    token: str
    acquired_at: str
    pid: int
    request_id: str = None
    metadata: dict = None
    path: str = None
    scope: str = EXECUTION
    session: dict = None

    def to_dict(self):
        out = {"adapter_id": self.adapter_id, "resource_id": self.resource_id, "owner_id": self.owner_id,
               "token": self.token, "acquired_at": self.acquired_at, "pid": self.pid,
               "request_id": self.request_id, "metadata": self.metadata or {}}
        if self.scope == SESSION:  # an EXECUTION lease file keeps its frozen shape
            out["scope"], out["session"] = SESSION, dict(self.session or {})
        return out


class LeaseHeld(Exception):
    """Another owner holds this lease. `holder` is the recorded metadata, or None when unreadable."""

    def __init__(self, code, message, holder=None, path=None):
        super().__init__(message)
        self.code, self.holder, self.path = code, holder, path


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def acquire(root, adapter_id, resource_id, owner_id, now, request_id=None, metadata=None, scope=EXECUTION,
            session=None):
    """Acquire the single-writer lease for a resource, or raise LeaseHeld.

    `now` is an RFC 3339 timestamp from the caller's clock, so tests need no real time. A SESSION lease
    needs a canonical `KIND:ID` owner and a `session` binding with a 32-hex `session_id`; an EXECUTION
    lease takes no session binding.
    """
    if scope not in SCOPES:
        raise LeaseHeld("LEASE_INVALID", f"lease scope {scope!r} is not one of {list(SCOPES)}")
    if scope == SESSION:
        if not isinstance(owner_id, str) or not SESSION_OWNER.match(owner_id):
            raise LeaseHeld("LEASE_INVALID", f"a SESSION lease needs a canonical KIND:ID owner, not {owner_id!r}")
        if not isinstance(session, dict) or not SESSION_ID.match(str(session.get("session_id", ""))):
            raise LeaseHeld("LEASE_INVALID", "a SESSION lease needs a session binding with a 32-hex session_id")
    elif session is not None:
        raise LeaseHeld("LEASE_INVALID", "an EXECUTION lease takes no session binding")
    path = lease_path(root, adapter_id, resource_id)
    reason = tp.unsafe_reason([Path(root).resolve()], str(path))
    if reason:  # the runtime area must stay inside the project, even if it was tampered with
        raise LeaseHeld("LEASE_INVALID", reason, None, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    lease = Lease(adapter_id=adapter_id, resource_id=resource_id, owner_id=owner_id,
                  token=uuid.uuid4().hex, acquired_at=now, pid=os.getpid(),
                  request_id=request_id, metadata=metadata or {}, path=str(path), scope=scope,
                  session=dict(session) if session is not None else None)
    payload = json.dumps(lease.to_dict(), sort_keys=True, indent=2).encode("utf-8")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        holder = _read(path)
        if holder is None:
            raise LeaseHeld("LEASE_INVALID", f"{path}: a lease file exists but cannot be read; it is not broken "
                                             f"automatically", None, str(path)) from None
        raise LeaseHeld("LEASE_CONFLICT", f"{adapter_id}:{resource_id} is held by owner {holder.get('owner_id')!r} "
                                          f"(pid {holder.get('pid')}, since {holder.get('acquired_at')})",
                        holder, str(path)) from None
    except OSError as exc:
        raise LeaseHeld("LEASE_INVALID", f"{path}: {type(exc).__name__}: {exc}", None, str(path)) from exc
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
    return lease


def release(root, lease):
    """Release a lease this process owns. Returns True when it was removed.

    A lease whose recorded owner or token differs is left alone: this function never breaks a lease
    belonging to another owner, and never removes anything it did not verify first.
    """
    path = Path(lease.path or lease_path(root, lease.adapter_id, lease.resource_id))
    holder = _read(path)
    if holder is None:
        return False
    if holder.get("owner_id") != lease.owner_id or holder.get("token") != lease.token:
        return False
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def holder(root, adapter_id, resource_id):
    """The recorded holder of a lease, or None when it is free. An unreadable lease file returns
    `{"unreadable": True}` rather than being treated as free."""
    path = lease_path(root, adapter_id, resource_id)
    if not path.exists():
        return None
    data = _read(path)
    return data if data is not None else {"unreadable": True, "path": str(path)}


def looks_stale(record, live_pid):
    """Whether a lease looks abandoned: recorded on this machine, by a process that is gone.

    Looking stale changes nothing on its own. It is reported (LEASE_STALE) so a human or an explicit
    recovery operation can decide; the foundation never breaks a lease on the strength of it.
    """
    if not isinstance(record, dict) or "pid" not in record:
        return False
    if scope_of(record) != EXECUTION:  # the command that took a SESSION lease has long exited
        return False
    return not live_pid(record["pid"])


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def verify_session(root, adapter_id, resource_id, session_id, owner_id=None):
    """The SESSION lease record on this resource when it binds exactly `session_id` (and, when given, the
    canonical owner `owner_id`). Raises LeaseHeld otherwise; nothing is ever changed here."""
    path = lease_path(root, adapter_id, resource_id)
    if not path.exists() and not path.is_symlink():
        raise LeaseHeld("LIVE_SESSION_MISMATCH", f"no SESSION lease is held on {adapter_id}:{resource_id}",
                        None, str(path))
    record = None if path.is_symlink() else _read(path)
    if record is None:
        raise LeaseHeld("LEASE_INVALID", f"{path}: the lease file cannot be read; it is not broken automatically",
                        None, str(path))
    if record.get("adapter_id") != adapter_id or record.get("resource_id") != resource_id:
        raise LeaseHeld("LEASE_INVALID", f"{path}: the lease names another adapter or resource", record, str(path))
    if scope_of(record) != SESSION:
        raise LeaseHeld("LEASE_CONFLICT", f"{adapter_id}:{resource_id} is held by an execution of owner "
                                          f"{record.get('owner_id')!r}, not by a session", record, str(path))
    bound = record.get("session") if isinstance(record.get("session"), dict) else {}
    if bound.get("session_id") != session_id:
        raise LeaseHeld("LIVE_SESSION_MISMATCH", f"the SESSION lease on {adapter_id}:{resource_id} binds another "
                                                 f"session", record, str(path))
    if owner_id is not None and record.get("owner_id") != owner_id:
        raise LeaseHeld("LIVE_SESSION_MISMATCH", f"the SESSION lease on {adapter_id}:{resource_id} is owned by "
                                                 f"{record.get('owner_id')!r}, not {owner_id!r}", record, str(path))
    return record


def release_session(root, adapter_id, resource_id, session_id, owner_id):
    """Release a SESSION lease after verifying its exact session id and canonical owner. Returns the
    released record; raises LeaseHeld when verification fails or the file cannot be removed."""
    record = verify_session(root, adapter_id, resource_id, session_id, owner_id)
    path = lease_path(root, adapter_id, resource_id)
    if _read(path) != record:  # changed since it was verified: leave it alone
        raise LeaseHeld("LIVE_SESSION_MISMATCH", f"{path}: the SESSION lease changed while it was being released",
                        None, str(path))
    try:
        os.unlink(path)
    except OSError as exc:
        raise LeaseHeld("LEASE_RELEASE_FAILED", f"{path}: {type(exc).__name__}: {exc}", record, str(path)) from exc
    return record


def break_lease(root, adapter_id, resource_id, broken_by, reason, now, expected_token=None):
    """Explicit recovery: remove a lease this process does not own, recording who did it and why.

    Nothing in ordinary execution calls this. It exists so that recovering from a crashed writer is
    a deliberate, attributable act rather than a silent side effect of the next execution. With
    `expected_token`, only the exact lease that was inspected is broken: a lease that has since been
    replaced is left alone.
    """
    if not reason:
        raise LeaseHeld("LEASE_INVALID", "breaking a lease requires an explicit reason")
    path = lease_path(root, adapter_id, resource_id)
    record = _read(path)
    if expected_token is not None and (record is None or record.get("token") != expected_token):
        raise LeaseHeld("LEASE_CONFLICT", f"{path}: the lease is not the one that was inspected; it is not broken",
                        record, str(path))
    if record is None and not path.exists():
        return None
    log = tp.runtime_dir(root, LEASES, "broken.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    entry = {"broken_at": now, "broken_by": broken_by, "reason": reason,
             "adapter_id": adapter_id, "resource_id": resource_id, "previous_holder": record}
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    try:
        os.unlink(path)
    except OSError:
        pass
    return entry
