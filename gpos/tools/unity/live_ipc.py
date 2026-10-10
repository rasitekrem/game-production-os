"""The GPOS side of the Unity live bridge's local file IPC (Phase 2C-6A; schema /5 with bridge 1.4.0).

One request is one immutable file, published atomically: written to a dot-temp name, fsynced, then linked to
`requests/<request id>.json` (a link never replaces an existing name). The bridge claims it by renaming it into
`claimed/`; it answers with exactly one `responses/<request id>.json`. There is no socket and no network.

Every request carries its canonical owner (KIND:ID), the bridge boot it is addressed to, the session it acts on
(when the command needs one), `issued_utc` and a `start_deadline_utc` at most 120 s later. The bridge never starts
a request after its start deadline.

When the caller stops waiting (`call`), it tries to take the request back with an atomic rename into `withdrawn/`.
Exactly one of that rename and the bridge's claim can succeed, so:

    RESPONDED   the bridge answered (whatever it answered);
    WITHDRAWN   the request was taken back before any claim: it never ran and never will;
    UNKNOWN     the bridge had claimed it but no answer arrived: its effect is unknown. It is never retried.
"""

import datetime
import hashlib
import json
import os
import re
import stat
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

REQUEST_SCHEMA = "gpos.unity.live.request/5"
RESPONSE_SCHEMA = "gpos.unity.live.response/5"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_DEPTH = 8
MAX_QUEUED = 32
DEFAULT_START_SECONDS = 30.0
MAX_START_SECONDS = 120.0
RESPONSE_KEYS = {"schema", "request_id", "status", "code", "message", "boot_id", "generation", "session_id", "utc",
                 "data"}
RESPONSE_STATUSES = {"OK", "REFUSED", "FAILED", "INTERRUPTED"}
RESPONDED, WITHDRAWN, UNKNOWN = "RESPONDED", "WITHDRAWN", "UNKNOWN"
FOLDERS = ("requests", "claimed", "responses", "withdrawn", "rejected")


class ChannelProblem(Exception):
    """The live directory, a state file or a response cannot be trusted."""


def utc(moment=None):
    moment = moment or datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ChannelProblem(f"duplicate key {key!r}")
        out[key] = value
    return out


def _depth(value, level=0):
    if level > MAX_DEPTH:
        raise ChannelProblem("nesting too deep")
    if isinstance(value, dict):
        for v in value.values():
            _depth(v, level + 1)
    elif isinstance(value, list):
        for v in value:
            _depth(v, level + 1)


def strict_json(data):
    def refuse(token):
        raise ChannelProblem(f"{token} is not JSON")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=refuse)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChannelProblem(f"not strict JSON ({exc})") from None
    _depth(value)
    return value


def read_bounded(path, limit=MAX_STATE_BYTES):
    """A small JSON object file the bridge wrote, or None when it does not exist. Never follows a link."""
    path = Path(path)
    if sys.platform == "win32":   # alpha.26: pinned ancestry, no reparse point, one link, never blocks the bridge
        return _windows_read_bounded(path, limit)
    if path.is_symlink():
        raise ChannelProblem(f"{path.name} is a symbolic link")
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(data) > limit:
        raise ChannelProblem(f"{path.name} is larger than {limit} bytes")
    value = strict_json(data)
    if not isinstance(value, dict):
        raise ChannelProblem(f"{path.name} is not a JSON object")
    return value


class Channel:
    """The request/response folders of one live directory. Refuses a live directory that is a link."""

    def __init__(self, live_dir):
        self.root = Path(live_dir)
        for p in [self.root] + [self.root / f for f in FOLDERS]:
            if p.is_symlink():
                raise ChannelProblem(f"{p} is a symbolic link")
            if sys.platform == "win32":   # alpha.26: a junction (or any reparse point) is a link here too
                if _windows_reparse(p):
                    raise ChannelProblem(f"{p} is a reparse point (a junction or link)")
        self.requests, self.claimed = self.root / "requests", self.root / "claimed"
        self.responses, self.withdrawn = self.root / "responses", self.root / "withdrawn"

    def queued(self):
        try:
            return sum(1 for n in os.listdir(self.requests) if not n.startswith("."))
        except FileNotFoundError:
            return 0


def publish(channel, command, args, owner, boot_id, session_id=None, start_seconds=DEFAULT_START_SECONDS, now=None):
    """Publish one request atomically and return its id."""
    if not 0 < start_seconds <= MAX_START_SECONDS:
        raise ValueError(f"the start deadline must be within {MAX_START_SECONDS} s")
    if not channel.requests.is_dir():
        raise ChannelProblem("the bridge has not created its request folder")
    if channel.queued() >= MAX_QUEUED:
        raise ChannelProblem(f"{MAX_QUEUED} requests are already waiting for the bridge")
    issued = now or datetime.datetime.now(datetime.timezone.utc)
    rid = uuid.uuid4().hex
    body = json.dumps({"schema": REQUEST_SCHEMA, "request_id": rid, "session_id": session_id, "owner": owner,
                       "boot_id": boot_id, "command": command, "args": dict(args or {}),
                       "issued_utc": utc(issued),
                       "start_deadline_utc": utc(issued + datetime.timedelta(seconds=start_seconds))},
                      sort_keys=True).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("the request is larger than the protocol bound")
    if sys.platform == "win32":   # alpha.26: a rename that never replaces (no hard link), the file identity recorded
        return _windows_publish(channel, rid, body)
    tmp = channel.requests / f".tmp-{uuid.uuid4().hex}"
    with open(tmp, "xb") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.link(tmp, channel.requests / f"{rid}.json")
    finally:
        os.unlink(tmp)
    return rid


def withdraw(channel, rid):
    """Take an unclaimed request back. True means the bridge never had it and never will."""
    if sys.platform == "win32":   # alpha.26: RENAME -> PIN -> VERIFY -> DECIDE (None: undecided, never WITHDRAWN)
        return _windows_withdraw(channel, rid)
    try:
        os.rename(channel.requests / f"{rid}.json", channel.withdrawn / f"{rid}.json")
        return True
    except FileNotFoundError:
        return False


def read_response(channel, rid):
    """The bridge's response to `rid`, or None when there is none yet. Raises ChannelProblem when it is garbled."""
    value = read_bounded(channel.responses / f"{rid}.json", MAX_RESPONSE_BYTES)
    if value is None:
        return None
    if set(value) != RESPONSE_KEYS or value["schema"] != RESPONSE_SCHEMA or value["request_id"] != rid:
        raise ChannelProblem("the response does not have the protocol shape")
    if value["status"] not in RESPONSE_STATUSES:
        raise ChannelProblem(f"unknown response status {value['status']!r}")
    return value


@dataclass(frozen=True)
class CallResult:
    outcome: str            # RESPONDED | WITHDRAWN | UNKNOWN
    response: dict = None
    request_id: str = None

    @property
    def status(self):
        return self.response["status"] if self.response else None

    @property
    def code(self):
        return self.response["code"] if self.response else None


def call(channel, command, args, owner, boot_id, session_id=None, wait=30.0, start_seconds=DEFAULT_START_SECONDS,
         sleep=time.sleep, monotonic=time.monotonic, poll=0.05):
    """Publish, wait up to `wait` seconds, then withdraw or report UNKNOWN. Never publishes twice."""
    if sys.platform == "win32":   # alpha.26: once published, an unreadable answer is an unknown outcome, never an error
        return _windows_call(channel, command, args, owner, boot_id, session_id, wait, start_seconds, sleep, monotonic,
                             poll)
    rid = publish(channel, command, args, owner, boot_id, session_id, start_seconds)
    end = monotonic() + wait
    while True:
        response = read_response(channel, rid)
        if response is not None:
            return CallResult(RESPONDED, response, rid)
        if monotonic() >= end:
            break
        sleep(poll)
    if withdraw(channel, rid):
        return CallResult(WITHDRAWN, None, rid)
    response = read_response(channel, rid)
    return CallResult(RESPONDED, response, rid) if response is not None else CallResult(UNKNOWN, None, rid)


if sys.platform == "win32":   # alpha.26 (Phase 2C-9.3b): the Windows live IPC
    # On NTFS two renames of one file at the same moment can both succeed (measured, N1-C5): a rename opens the source
    # by name and renames through the handle, and the last rename decides where the file rests. So on Windows a
    # rename decides nothing. Each side decides by a pin: after its own rename it opens the file at its destination
    # with a share mode that excludes delete (GPOS: paths_win32.open_file_for_read(deny_writers=True), FILE_SHARE_READ
    # only; the bridge: FileShare.Read). NTFS cannot grant that while any rename handle on the file is open, and once
    # granted the file can no longer be reached through requests/, so the decision holds after the pin is closed
    # (N1-B: 28 fault-injection scenarios and 3900 cross-process races). GPOS returns WITHDRAWN only when its pin
    # holds exactly the file it published (volume and file id recorded at publication, and the same bytes); anything
    # less is undecided, which `call` reports as RESPONDED or UNKNOWN, never as WITHDRAWN.
    from .. import paths_win32 as _pw

    RETRY_SECONDS = 0.25          # bounded waits for a sharing violation (another process holds the file)
    STALE_TEMP_SECONDS = 600      # a publication temp this old was interrupted; GPOS removes its own temps only
    MAX_PUBLISHED = 256
    _TEMP = re.compile(r"^\.tmp-[0-9a-f]{32}$")
    _PUBLISHED = {}               # request id -> (volume, file id, sha256) of the request this process published

    def _windows_reparse(path):
        """True for a reparse point, or when the attributes of an existing path cannot be read (fail closed)."""
        try:
            return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except FileNotFoundError:
            return False
        except OSError:
            return True

    def _read_fd(fd, limit):
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    def _windows_open(path, deny_writers):
        """(fd, pins), None when nothing exists there, or raises ChannelProblem; sharing violations are retried."""
        end = time.monotonic() + RETRY_SECONDS
        while True:
            try:
                fd, _, pins = _pw.open_file_for_read(str(path), deny_writers=deny_writers)
                return fd, pins
            except FileNotFoundError:
                return None
            except PermissionError:
                if time.monotonic() >= end:
                    raise ChannelProblem(f"{path.name} is held by another process") from None
                time.sleep(0.002)
            except _pw.PathRefused as exc:
                if not os.path.lexists(path):
                    return None
                raise ChannelProblem(f"{path.name}: {exc.reason}") from None
            except OSError as exc:
                raise ChannelProblem(f"{path.name} cannot be read ({exc.strerror})") from None

    def _windows_read_bounded(path, limit):
        opened = _windows_open(path, deny_writers=False)
        if opened is None:
            return None
        fd, pins = opened
        try:
            data = _read_fd(fd, limit)
        finally:
            os.close(fd)
            _pw.close_all(pins)
        if len(data) > limit:
            raise ChannelProblem(f"{path.name} is larger than {limit} bytes")
        value = strict_json(data)
        if not isinstance(value, dict):
            raise ChannelProblem(f"{path.name} is not a JSON object")
        return value

    def _sweep_temps(folder):
        """Remove interrupted GPOS publication temps (.tmp-<32 hex>) older than STALE_TEMP_SECONDS; nothing else."""
        try:
            names = os.listdir(folder)
        except OSError:
            return
        now = time.time()
        for name in names:
            if _TEMP.match(name):
                try:
                    if now - os.lstat(folder / name).st_mtime > STALE_TEMP_SECONDS:
                        os.unlink(folder / name)
                except OSError:
                    pass

    def _windows_publish(channel, rid, body):
        _sweep_temps(channel.requests)
        tmp = channel.requests / f".tmp-{uuid.uuid4().hex}"
        with open(tmp, "xb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
            st = os.fstat(fh.fileno())          # the file id survives the rename: it names exactly this file
        end = time.monotonic() + RETRY_SECONDS
        while True:
            try:
                os.rename(tmp, channel.requests / f"{rid}.json")   # MoveFileExW without flags: never replaces
                break
            except PermissionError:             # another process (a scanner) holds the temp without sharing delete
                if time.monotonic() >= end:
                    _discard(tmp)
                    raise ChannelProblem("the request could not be published: its temporary file is held by "
                                         "another process") from None
                time.sleep(0.002)
            except OSError as exc:
                _discard(tmp)
                raise ChannelProblem(f"the request could not be published ({exc.strerror})") from None
        while len(_PUBLISHED) >= MAX_PUBLISHED:
            _PUBLISHED.pop(next(iter(_PUBLISHED)))
        _PUBLISHED[rid] = (st.st_dev, st.st_ino, hashlib.sha256(body).hexdigest())
        return rid

    def _discard(tmp):
        try:
            os.unlink(tmp)
        except OSError:
            pass                                # left for the sweep: a dot-temp is never read as a request

    def _windows_withdraw(channel, rid):
        """True: WITHDRAWN (pinned and verified). False: the request is no longer at requests/ or was moved away
        from withdrawn/ (the bridge has it, or someone moved it). None: undecided (a sharing violation outlasted the
        bound, or the pinned file is not exactly the request GPOS published). Only True means it never ran."""
        identity = _PUBLISHED.pop(rid, None)
        src, dst = channel.requests / f"{rid}.json", channel.withdrawn / f"{rid}.json"
        end = time.monotonic() + RETRY_SECONDS
        while True:
            try:
                os.rename(src, dst)
                break
            except FileNotFoundError:
                return False
            except PermissionError:             # held without FILE_SHARE_DELETE: it may still be claimed later
                if time.monotonic() >= end:
                    return None
                time.sleep(0.002)
            except OSError:
                return None
        try:
            opened = _windows_open(dst, deny_writers=True)
        except ChannelProblem:
            return None
        if opened is None:
            return False                        # the bridge's rename landed after ours: the request is the bridge's
        fd, pins = opened
        try:
            st = os.fstat(fd)
            data = _read_fd(fd, MAX_REQUEST_BYTES)
        finally:
            os.close(fd)
            _pw.close_all(pins)
        if identity is None or (st.st_dev, st.st_ino) != identity[:2] or hashlib.sha256(data).hexdigest() != identity[2]:
            return None
        return True

    def _windows_call(channel, command, args, owner, boot_id, session_id, wait, start_seconds, sleep, monotonic, poll):
        """`call` on Windows. A response that cannot be read (held past the bound, or not of the protocol shape) after
        the request was published is not an error that ends the call: the request may have run, so it is waited for
        like a missing response and is finally UNKNOWN. Never publishes twice; never WITHDRAWN without the pin."""
        rid = publish(channel, command, args, owner, boot_id, session_id, start_seconds)

        def answered():
            try:
                return read_response(channel, rid)
            except ChannelProblem:
                return None

        end = monotonic() + wait
        while True:
            response = answered()
            if response is not None:
                return CallResult(RESPONDED, response, rid)
            if monotonic() >= end:
                break
            sleep(poll)
        if withdraw(channel, rid):
            return CallResult(WITHDRAWN, None, rid)
        response = answered()
        return CallResult(RESPONDED, response, rid) if response is not None else CallResult(UNKNOWN, None, rid)
