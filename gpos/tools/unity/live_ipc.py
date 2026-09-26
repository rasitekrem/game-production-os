"""The GPOS side of the Unity live bridge's local file IPC (Phase 2C-6A).

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
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

REQUEST_SCHEMA = "gpos.unity.live.request/1"
RESPONSE_SCHEMA = "gpos.unity.live.response/1"
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
