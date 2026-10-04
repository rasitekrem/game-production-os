"""The player runtime directory and its files (alpha.22).

The SESSION lease is the only lifecycle authority. The runtime directory — the launch execution's workspace,
`.game/gpos-runtime/tool-output/player/<launch request id>/` — holds supporting observations only:

    supervisor-request.json  launch -> helper      the closed supervise request
    handshake.json           helper (supervise)    the supervisor's and the Player's kernel identity, once
    runtime-binding.json     launch                the verified identities, written once after the handshake proof
    commit.json / abort.json launch (abort: stop)  whether the launch committed
    stop-intent-<rid>.json   stop                  the normal stop request the supervisor acts on
    fallback-<rid>.json      stop                  a stop that had to act without the supervisor
    kill-<rid>.json          stop                  written immediately before a fallback SIGKILL
    exit.json                helper (supervise)    the Player's exit status, observed by its parent, once
    player.log               the Player            the one log (-logFile), read bounded and sanitized

Every file has an exact name and a strict schema, is bounded, is written once (an exclusive temporary file, fsync,
then a hard link that never replaces anything) and is never read through a link. Nothing here takes a path from a
request: every path is derived from the project root and an id that the foundation or this adapter validated.
"""

import json
import os
import stat
from pathlib import Path

from .. import paths as tp

SUPERVISOR_REQUEST = "supervisor-request.json"
HANDSHAKE = "handshake.json"
BINDING = "runtime-binding.json"
COMMIT, ABORT, EXIT = "commit.json", "abort.json", "exit.json"
PLAYER_LOG = "player.log"
HELPER_REQUEST, HELPER_RESULT = "helper-request.json", "helper-result.json"
MAX_RECORD_BYTES = 8192
MAX_CONTROL_BYTES = 1024
MAX_STOP_RECORDS = 8

ABORT_REASONS = ("HANDSHAKE_INVALID", "IDENTITY_UNPROVEN", "BUILD_INVALID", "RUNTIME_CONFLICT", "BINDING_FAILED",
                 "LAUNCH_FAILED", "STOP_UNRESOLVED")


class RecordProblem(Exception):
    pass


# ---------------------------------------------------------------- the directory

def player_dir(root, request_id):
    """`<root>/.game/gpos-runtime/tool-output/player/<request id>` (not created)."""
    return tp.runtime_dir(root, "tool-output", "player", request_id)


def chain_problem(root, directory):
    """Why `directory` is not a usable GPOS runtime directory below `root` (a link in the chain, not canonical,
    missing), or None."""
    root, directory = Path(root), Path(directory)
    try:
        rel = directory.relative_to(root)
    except ValueError:
        return "the runtime directory is not inside the project"
    current = root
    for part in rel.parts:
        current = current / part
        if os.path.islink(current):
            return f"{current.name} is a link"
    if not directory.is_dir():
        return "the runtime directory does not exist"
    if os.path.realpath(directory) != str(directory):
        return "the runtime directory is not canonical"
    return None


# ---------------------------------------------------------------- write once

def write_once(path, obj, bound=MAX_RECORD_BYTES):
    """Write `obj` as JSON exactly once. Raises FileExistsError when the name exists, RecordProblem when too large."""
    data = json.dumps(obj, sort_keys=True).encode("utf-8")
    if len(data) > bound:
        raise RecordProblem(f"{Path(path).name} would exceed {bound} bytes")
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
    return path


def read_json(path, bound=MAX_RECORD_BYTES):
    """A JSON object from a regular, non-link file of at most `bound` bytes; RecordProblem otherwise."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise RecordProblem(f"{Path(path).name} does not exist") from None
    if not stat.S_ISREG(st.st_mode):
        raise RecordProblem(f"{Path(path).name} is not a regular file")
    if st.st_size > bound:
        raise RecordProblem(f"{Path(path).name} is larger than {bound} bytes")
    with open(path, "rb") as fh:
        data = fh.read(bound + 1)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RecordProblem(f"{Path(path).name} is not UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise RecordProblem(f"{Path(path).name} is not a JSON object")
    return value


def exists(path):
    return os.path.lexists(path)


# ---------------------------------------------------------------- strict shapes

def _is_int(v, low=0):
    return isinstance(v, int) and not isinstance(v, bool) and v >= low


def _is_str(v):
    return isinstance(v, str) and 0 < len(v) <= 4096 and "\x00" not in v


def _shape(d, keys, what):
    if set(d) != set(keys):
        raise RecordProblem(f"{what} does not hold exactly its keys")


def handshake(path, session_id, nonce):
    """The supervisor's handshake, checked strictly and bound to this session and nonce."""
    d = read_json(path)
    _shape(d, ("schema", "session_id", "nonce", "spawned_at", "supervisor", "player"), "the handshake")
    if (d["schema"], d["session_id"], d["nonce"]) != ("gpos.player.handshake/1", session_id, nonce):
        raise RecordProblem("the handshake belongs to another schema, session or launch")
    s, p = d["supervisor"], d["player"]
    if not (isinstance(s, dict) and set(s) == {"pid", "start_sec", "start_usec", "executable", "cdhash", "version"}
            and _is_int(s["pid"], 1) and _is_int(s["start_sec"], 1) and _is_int(s["start_usec"])
            and _is_str(s["executable"]) and isinstance(s["cdhash"], str) and _is_str(s["version"])):
        raise RecordProblem("the handshake's supervisor identity is malformed")
    if not (isinstance(p, dict) and set(p) == {"pid", "ppid", "pgid", "start_sec", "start_usec", "executable"}
            and all(_is_int(p[k], 1) for k in ("pid", "ppid", "pgid", "start_sec")) and _is_int(p["start_usec"])
            and _is_str(p["executable"])):
        raise RecordProblem("the handshake's Player identity is malformed")
    if not _is_str(d["spawned_at"]):
        raise RecordProblem("the handshake has no spawn time")
    return d


BINDING_KEYS = ("schema", "session_id", "nonce", "build_id", "manifest_sha256", "supervisor", "player", "handshake_at",
                "bound_at")


def binding(path, session):
    """runtime-binding.json, checked strictly and bound to the SESSION lease's session id, nonce, build and
    executable. RecordProblem when it is missing, malformed or belongs to anything else."""
    d = read_json(path)
    _shape(d, BINDING_KEYS, "the runtime binding")
    if d["schema"] != "gpos.player.runtime-binding/1":
        raise RecordProblem("the runtime binding has another schema")
    for key in ("session_id", "nonce", "build_id", "manifest_sha256"):
        if d[key] != session.get(key):
            raise RecordProblem(f"the runtime binding's {key} is not the session's")
    s, p = d["supervisor"], d["player"]
    if not (isinstance(s, dict) and set(s) == {"pid", "start_sec", "start_usec", "executable", "cdhash"}
            and _is_int(s["pid"], 1) and _is_int(s["start_sec"], 1) and _is_int(s["start_usec"])
            and _is_str(s["executable"]) and isinstance(s["cdhash"], str)):
        raise RecordProblem("the runtime binding's supervisor identity is malformed")
    if not (isinstance(p, dict) and set(p) == {"pid", "start_sec", "start_usec", "executable", "dev", "ino"}
            and _is_int(p["pid"], 1) and _is_int(p["start_sec"], 1) and _is_int(p["start_usec"])
            and _is_int(p["dev"]) and _is_int(p["ino"]) and p["executable"] == session.get("executable")):
        raise RecordProblem("the runtime binding's Player identity is malformed or names another executable")
    return d


EXIT_KEYS = ("schema", "session_id", "nonce", "committed", "player", "status", "code", "signal", "spawn_errno", "stop",
             "observed_at")
STOP_KEYS = {"reason", "stop_request_id", "terminate_requested_at", "terminate_accepted", "kill_sent"}


def exit_record(path, session_id, nonce):
    """exit.json, checked strictly and bound to this session and nonce."""
    d = read_json(path)
    _shape(d, EXIT_KEYS, "the exit record")
    if (d["schema"], d["session_id"], d["nonce"]) != ("gpos.player.exit/1", session_id, nonce):
        raise RecordProblem("the exit record belongs to another schema, session or launch")
    if d["status"] not in ("NOT_STARTED", "EXITED", "SIGNALED") or not isinstance(d["committed"], bool):
        raise RecordProblem("the exit record's status is malformed")
    if d["status"] == "EXITED" and not _is_int(d["code"]):
        raise RecordProblem("an EXITED record has no exit code")
    if d["status"] == "SIGNALED" and not _is_int(d["signal"], 1):
        raise RecordProblem("a SIGNALED record has no signal")
    p = d["player"]
    if d["status"] != "NOT_STARTED" and not (isinstance(p, dict) and set(p) == {"pid", "start_sec", "start_usec"}
                                             and all(_is_int(p[k]) for k in p)):
        raise RecordProblem("the exit record's Player identity is malformed")
    s = d["stop"]
    if not (isinstance(s, dict) and set(s) == STOP_KEYS and isinstance(s["terminate_accepted"], bool)
            and isinstance(s["kill_sent"], bool) and s["reason"] in (None, "STOP_INTENT", "ABORT", "DEADLINE")):
        raise RecordProblem("the exit record's stop facts are malformed")
    return d


def control(path, schema, session_id, nonce, **extra):
    return {"schema": schema, "session_id": session_id, "nonce": nonce, **extra}


def stop_records(directory, prefix):
    """The names of existing `<prefix>-<rid>.json` records (bounded)."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(n for n in names if n.startswith(prefix + "-") and n.endswith(".json"))
