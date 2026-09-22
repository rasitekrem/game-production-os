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
"""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import paths as tp

LEASES = "leases"


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

    def to_dict(self):
        return {"adapter_id": self.adapter_id, "resource_id": self.resource_id, "owner_id": self.owner_id,
                "token": self.token, "acquired_at": self.acquired_at, "pid": self.pid,
                "request_id": self.request_id, "metadata": self.metadata or {}}


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


def acquire(root, adapter_id, resource_id, owner_id, now, request_id=None, metadata=None):
    """Acquire the single-writer lease for a resource, or raise LeaseHeld.

    `now` is an RFC 3339 timestamp from the caller's clock, so tests need no real time.
    """
    path = lease_path(root, adapter_id, resource_id)
    reason = tp.unsafe_reason([Path(root).resolve()], str(path))
    if reason:  # the runtime area must stay inside the project, even if it was tampered with
        raise LeaseHeld("LEASE_INVALID", reason, None, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    lease = Lease(adapter_id=adapter_id, resource_id=resource_id, owner_id=owner_id,
                  token=uuid.uuid4().hex, acquired_at=now, pid=os.getpid(),
                  request_id=request_id, metadata=metadata or {}, path=str(path))
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
    return not live_pid(record["pid"])


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def break_lease(root, adapter_id, resource_id, broken_by, reason, now):
    """Explicit recovery: remove a lease this process does not own, recording who did it and why.

    Nothing in ordinary execution calls this. It exists so that recovering from a crashed writer is
    a deliberate, attributable act rather than a silent side effect of the next execution.
    """
    if not reason:
        raise LeaseHeld("LEASE_INVALID", "breaking a lease requires an explicit reason")
    path = lease_path(root, adapter_id, resource_id)
    record = _read(path)
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
