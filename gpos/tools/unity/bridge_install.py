"""The fixed GPOS live bridge package: release manifest, verification, the closed-project installer and the
crash-recoverable upgrade from an earlier released bridge (Phase 2C-6A, upgrade Phase 2C-6B1).

The bridge is GPOS release content: `live_bridge/com.gpos.live-bridge/` holds every file of the Unity package,
including fixed `.meta` files, and `live_bridge/manifest.json` records each file's size and SHA-256 plus the
package digest. Nothing about a project is written into the package, so its digest is the same everywhere.

    package digest = sha256 of the sorted lines  "<relative path>\\t<sha256>\\t<size>\\n"

The running bridge computes the same digest over its own files and publishes it; GPOS accepts a bridge only when
the manifest, the installed files and the running bridge agree.

Earlier released bridges are release content too: `live_bridge/history/<version>.json` is the byte-for-byte
manifest of that release, and PREVIOUS pins its protocol and package digest in code. An installed package is

    ABSENT     nothing at Packages/com.gpos.live-bridge
    EXACT      byte for byte the bridge this release ships
    PREVIOUS   byte for byte a pinned earlier released bridge (it can be upgraded)
    UNTRUSTED  anything else: modified, partial, extra content, a link — never overwritten, repaired or removed

The installer writes exactly this package into `<unity-project>/Packages/com.gpos.live-bridge/` of a *closed*
project and nothing else: no caller path, source or package, no `Packages/manifest.json` edit, no Package Manager,
no network. A fresh install is staged in the GPOS runtime area and moved into place with one rename.

An upgrade (PREVIOUS -> EXACT) replaces a directory, which is not one atomic step. It is a transaction whose record
names identifiers only — never a path:

    1 stage the new package in the runtime area and verify it      4 move the staged package into Packages/
    2 write the transaction record                                5 verify the installed package
    3 move the exact old package to the transaction's backup      6 verify and remove the backup, clear the record

Staging and backup directories are derived from the GPOS runtime root, fixed names and the validated transaction id.
After a crash, `recover` classifies what is actually on disk and finishes or rolls back only when every package
involved is exactly a known released package; anything unknown fails closed and is left untouched.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path

from .. import paths as tp

PACKAGE_ID = "com.gpos.live-bridge"
BRIDGE_VERSION = "1.1.0"
PROTOCOL = "gpos.unity.live/2"
MANIFEST_SCHEMA = "gpos.unity.live-bridge.manifest/1"
HERE = Path(__file__).resolve().parent
SOURCE = HERE / "live_bridge" / PACKAGE_ID
MANIFEST = HERE / "live_bridge" / "manifest.json"
HISTORY = HERE / "live_bridge" / "history"
# Every earlier released bridge this release upgrades from: version -> (protocol, package digest). The manifest of
# each is kept byte for byte under live_bridge/history/; both must agree with the frozen release that shipped it.
PREVIOUS = {
    "1.0.0": ("gpos.unity.live/1", "546b3cfbe4d41234d10450efacbb3397812d106a813dee5b3284903624a68c66"),
}
MAX_FILE_BYTES = 1024 * 1024
ABSENT, EXACT, PREVIOUS_STATE, UNTRUSTED = "ABSENT", "EXACT", "PREVIOUS", "UNTRUSTED"
STAGING = ("unity", "install-staging")
BACKUP = ("unity", "install-backup")
TXN = ("unity", "install-txn")
TXN_SCHEMA = "gpos.unity.live-bridge.install-txn/1"
TXN_KEYS = {"schema", "txn_id", "project_key", "unity_project_rel", "from_version", "from_digest", "to_version",
            "to_digest", "phase", "started_at"}
TXN_ID = re.compile(r"^[0-9a-f]{32}$")
PROJECT_KEY = re.compile(r"^[0-9a-f]{16}$")
UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?Z$")
MAX_TXN_BYTES = 4096
PHASES = ("STAGED", "OLD_MOVED", "NEW_PLACED", "VERIFIED")
# What recovery finds at each place: a package is ABSENT, EXACT_<version>, PARTIAL_<version> (a non-empty strict
# subset of that release's files, each byte for byte) or UNKNOWN.
PLACE_ABSENT, PLACE_UNKNOWN = "ABSENT", "UNKNOWN"

# A code-level seam for the upgrade interruption tests: called with each step's name after it completes. A request
# cannot reach it.
_after_step = None


class BridgeSourceCorrupt(Exception):
    """The GPOS-owned bridge package or a pinned release manifest does not match."""


class UpgradeIncomplete(Exception):
    """An interrupted upgrade cannot be finished or rolled back from known packages alone; nothing was changed."""

    def __init__(self, message, places=None):
        super().__init__(message)
        self.places = dict(places or {})


def digest(entries):
    lines = "".join(f"{e['path']}\t{e['sha256']}\t{e['size']}\n" for e in sorted(entries, key=lambda e: e["path"]))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def tree(directory):
    """(entries, problems) for a directory tree, never following a link: each regular file as
    {path, size, sha256}; every link, special file or oversized file is a problem."""
    directory = Path(directory)
    entries, problems = [], []

    def walk(d, rel):
        for name in sorted(os.listdir(d)):
            path, relpath = d / name, f"{rel}{name}"
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                problems.append(f"{relpath}: a symbolic link")
            elif stat.S_ISDIR(st.st_mode):
                walk(path, relpath + "/")
            elif not stat.S_ISREG(st.st_mode):
                problems.append(f"{relpath}: not a regular file")
            elif st.st_size > MAX_FILE_BYTES:
                problems.append(f"{relpath}: larger than {MAX_FILE_BYTES} bytes")
            else:
                data = path.read_bytes()
                entries.append({"path": relpath, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    walk(directory, "")
    return entries, problems


def directories(entries):
    """Every directory the manifest's files imply, relative to the package root."""
    out = set()
    for e in entries:
        parts = e["path"].split("/")[:-1]
        for i in range(1, len(parts) + 1):
            out.add("/".join(parts[:i]))
    return out


def load_manifest(path=MANIFEST, version=BRIDGE_VERSION, protocol=PROTOCOL):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    keys = {"schema", "package_id", "bridge_version", "protocol", "files", "package_digest"}
    if not isinstance(data, dict) or set(data) != keys or data["schema"] != MANIFEST_SCHEMA:
        raise BridgeSourceCorrupt("the bridge manifest does not have the expected shape")
    if (data["package_id"], data["bridge_version"], data["protocol"]) != (PACKAGE_ID, version, protocol):
        raise BridgeSourceCorrupt("the bridge manifest names another package, version or protocol")
    for e in data["files"]:
        if not isinstance(e, dict) or set(e) != {"path", "size", "sha256"}:
            raise BridgeSourceCorrupt("a bridge manifest entry does not have the expected shape")
    if digest(data["files"]) != data["package_digest"]:
        raise BridgeSourceCorrupt("the bridge manifest's package digest does not match its entries")
    return data


def history():
    """{version: manifest} of every pinned earlier release. Raises BridgeSourceCorrupt when a history manifest is
    missing or disagrees with its pin."""
    out = {}
    for version, (protocol, pinned) in PREVIOUS.items():
        path = HISTORY / f"{version}.json"
        if path.is_symlink() or not path.is_file():
            raise BridgeSourceCorrupt(f"the manifest of released bridge {version} is missing")
        manifest = load_manifest(path, version, protocol)
        if manifest["package_digest"] != pinned:
            raise BridgeSourceCorrupt(f"the manifest of released bridge {version} does not match its pinned digest")
        out[version] = manifest
    return out


def verify_source(source=SOURCE, manifest_path=MANIFEST):
    """The verified manifest. Raises BridgeSourceCorrupt when the GPOS-owned package differs from it."""
    manifest = load_manifest(manifest_path)
    entries, problems = tree(source)
    if problems or sorted(entries, key=lambda e: e["path"]) != sorted(manifest["files"], key=lambda e: e["path"]):
        raise BridgeSourceCorrupt("the GPOS-owned bridge package does not match its release manifest"
                                  + (f" ({problems[0]})" if problems else ""))
    return manifest


def package_dir(unity_project):
    return Path(unity_project) / "Packages" / PACKAGE_ID


def differences(target, manifest):
    """Why the directory `target` (which exists and is a real directory) is not exactly `manifest`'s package."""
    entries, problems = tree(target)
    expected = {e["path"]: e for e in manifest["files"]}
    found = {e["path"]: e for e in entries}
    out = list(problems)
    out += [f"{p}: missing" for p in sorted(set(expected) - set(found))]
    out += [f"{p}: not part of the audited bridge" for p in sorted(set(found) - set(expected))]
    out += [f"{p}: modified" for p in sorted(set(expected) & set(found)) if expected[p] != found[p]]
    extra_dirs = _directories_on_disk(target) - directories(manifest["files"])
    out += [f"{d}/: not part of the audited bridge" for d in sorted(extra_dirs)]
    return out


def inspect_target(unity_project, manifest):
    """(ABSENT | EXACT | PREVIOUS | UNTRUSTED, [difference]) for the installed package. Read-only.
    PREVIOUS means byte for byte a pinned earlier release (installed_version names it)."""
    target = package_dir(unity_project)
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return ABSENT, []
    if stat.S_ISLNK(st.st_mode):
        return UNTRUSTED, [f"Packages/{PACKAGE_ID} is a symbolic link"]
    if not stat.S_ISDIR(st.st_mode):
        return UNTRUSTED, [f"Packages/{PACKAGE_ID} is not a directory"]
    found = differences(target, manifest)
    if not found:
        return EXACT, []
    if installed_version(unity_project) is not None:
        return PREVIOUS_STATE, []
    return UNTRUSTED, found


def installed_version(unity_project):
    """The pinned earlier release the installed package is byte for byte, or None. Read-only."""
    place = classify_place(package_dir(unity_project), history())
    return place[len("EXACT_"):] if place.startswith("EXACT_") else None


def _directories_on_disk(target):
    out = set()
    for dirpath, dirnames, _ in os.walk(target, followlinks=False):
        for name in dirnames:
            rel = (Path(dirpath) / name).relative_to(target).as_posix()
            out.add(rel)
    return out


def classify_place(path, known):
    """ABSENT, EXACT_<version>, PARTIAL_<version> or UNKNOWN for a package directory, against {version: manifest}.
    A link, a special file, a modified or extra file, or a mix of releases is UNKNOWN. Read-only."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return PLACE_ABSENT
    if not stat.S_ISDIR(st.st_mode):
        return PLACE_UNKNOWN
    entries, problems = tree(path)
    if problems:
        return PLACE_UNKNOWN
    found = {e["path"]: e for e in entries}
    on_disk = _directories_on_disk(path)
    for version, manifest in known.items():
        expected = {e["path"]: e for e in manifest["files"]}
        if set(found) - set(expected) or any(expected[p] != e for p, e in found.items()):
            continue
        if on_disk - directories(manifest["files"]):
            continue
        return f"EXACT_{version}" if set(found) == set(expected) else f"PARTIAL_{version}"
    return PLACE_UNKNOWN


def _step(name):
    if _after_step is not None:
        _after_step(name)


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def runtime_path(root, *parts):
    """A path in the GPOS runtime area; refuses (OSError) when any existing component below the GPOS root is a
    symbolic link or not a directory, so nothing here can be redirected out of the runtime scope."""
    root = Path(root).resolve()
    path = tp.runtime_dir(root, *parts)
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise OSError(f"{current} is not a real directory")
    return path


def _stage(root, manifest, source, staging_parent):
    """Write and verify the package under `staging_parent/PACKAGE_ID`; returns that path."""
    staged = staging_parent / PACKAGE_ID
    for d in sorted(directories(manifest["files"])):
        (staged / d).mkdir(parents=True, exist_ok=True)
    staged.mkdir(parents=True, exist_ok=True)
    for e in manifest["files"]:
        data = (Path(source) / e["path"]).read_bytes()
        if len(data) != e["size"] or hashlib.sha256(data).hexdigest() != e["sha256"]:
            raise BridgeSourceCorrupt(f"{e['path']} changed while it was being installed")
        with open(staged / e["path"], "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    if differences(staged, manifest):
        raise BridgeSourceCorrupt("the staged bridge package does not match its release manifest")
    return staged


def install(root, unity_project, manifest, source=SOURCE):
    """Write the audited package into a closed project where none is installed. Returns (EXACT, written) —
    written False when the identical package was already there. Raises BridgeSourceCorrupt, ValueError (anything
    else is there) or OSError; the caller has already checked that the project is closed and holds the writer
    lease, and has handled PREVIOUS through `upgrade`."""
    state, found = inspect_target(unity_project, manifest)
    if state == EXACT:
        return EXACT, False
    if state != ABSENT:
        raise ValueError(found or [f"Packages/{PACKAGE_ID} holds an earlier release; it is upgraded, not installed"])
    packages = Path(unity_project) / "Packages"
    if packages.is_symlink() or not packages.is_dir():
        raise OSError(f"{packages} is not a real directory")
    staging = runtime_path(root, *STAGING, uuid.uuid4().hex)
    try:
        staged = _stage(root, manifest, source, staging)
        if os.path.lexists(package_dir(unity_project)):
            raise ValueError([f"Packages/{PACKAGE_ID} appeared while the bridge was being staged"])
        os.rename(staged, package_dir(unity_project))   # one atomic step; a cross-device move is refused, not copied
        _fsync_dir(packages)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return EXACT, True


# ---------------------------------------------------------------- upgrade transaction

def txn_record_path(root, key):
    return runtime_path(root, *TXN, f"{key}.json")


def txn_places(root, unity_project, txn_id):
    """The canonical, staging and backup package paths of a transaction — derived, never read from the record."""
    if not isinstance(txn_id, str) or not TXN_ID.fullmatch(txn_id):
        raise ValueError("a transaction id is 32 lower-case hex digits")
    return {"canonical": package_dir(unity_project),
            "staging": runtime_path(root, *STAGING, txn_id, PACKAGE_ID),
            "backup": runtime_path(root, *BACKUP, txn_id, PACKAGE_ID)}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write_record(path, record):
    body = json.dumps(record, sort_keys=True).encode("utf-8")
    if len(body) > MAX_TXN_BYTES:
        raise ValueError("the transaction record is larger than its bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".tmp-{uuid.uuid4().hex}"
    with open(tmp, "xb") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def read_record(root, key):
    """The transaction record for this project, None when there is none, or UpgradeIncomplete when a record
    exists and cannot be trusted exactly (it is then never acted on)."""
    path = txn_record_path(root, key)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_TXN_BYTES:
        raise UpgradeIncomplete("the upgrade transaction record is not a small regular file")

    def refuse(_):
        raise ValueError("not JSON")

    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError("duplicate key")
            out[k] = v
        return out
    try:
        record = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=unique, parse_constant=refuse)
    except (UnicodeDecodeError, ValueError):
        raise UpgradeIncomplete("the upgrade transaction record is not strict JSON") from None
    if not isinstance(record, dict) or set(record) != TXN_KEYS or record["schema"] != TXN_SCHEMA:
        raise UpgradeIncomplete("the upgrade transaction record does not have the expected shape")
    if not isinstance(record["txn_id"], str) or not TXN_ID.fullmatch(record["txn_id"]):
        raise UpgradeIncomplete("the upgrade transaction record's id is not a transaction id")
    if record["project_key"] != key:
        raise UpgradeIncomplete("the upgrade transaction record names another Unity project")
    if not isinstance(record["phase"], str) or record["phase"] not in PHASES:
        raise UpgradeIncomplete("the upgrade transaction record's phase is unknown")
    if not isinstance(record["started_at"], str) or not UTC.fullmatch(record["started_at"]):
        raise UpgradeIncomplete("the upgrade transaction record's start time is not a UTC timestamp")
    if record["from_version"] not in PREVIOUS or PREVIOUS[record["from_version"]][1] != record["from_digest"]:
        raise UpgradeIncomplete("the upgrade transaction record's old package is not a pinned released bridge")
    return record


def upgrade(root, unity_project, key, rel, manifest, source=SOURCE):
    """Upgrade an exact pinned earlier bridge to this release's bridge in a closed project. Returns the version
    upgraded from. Raises ValueError when the installed package is not PREVIOUS, BridgeSourceCorrupt, or OSError
    (a partial transaction is then left for `recover`)."""
    known = history()
    place = classify_place(package_dir(unity_project), known)
    if not place.startswith("EXACT_"):
        raise ValueError([f"Packages/{PACKAGE_ID} is not an exact earlier released bridge"])
    old = place[len("EXACT_"):]
    packages = Path(unity_project) / "Packages"
    if packages.is_symlink() or not packages.is_dir():
        raise OSError(f"{packages} is not a real directory")
    txn_id = uuid.uuid4().hex
    places = txn_places(root, unity_project, txn_id)
    staged = _stage(root, manifest, source, places["staging"].parent)
    _step("staged")
    if os.stat(staged).st_dev != os.stat(packages).st_dev:
        _remove_known(staged, manifest, BRIDGE_VERSION)
        _remove_empty(staged.parent)
        raise OSError("the GPOS runtime area and the Unity project are on different file systems; a move would be a "
                      "copy, so the bridge is not upgraded")
    record = {"schema": TXN_SCHEMA, "txn_id": txn_id, "project_key": key, "unity_project_rel": rel,
              "from_version": old, "from_digest": known[old]["package_digest"], "to_version": BRIDGE_VERSION,
              "to_digest": manifest["package_digest"], "phase": "STAGED", "started_at": _now()}
    record_path = txn_record_path(root, key)
    _write_record(record_path, record)
    _step("recorded")
    places["backup"].parent.mkdir(parents=True, exist_ok=True)
    if classify_place(places["canonical"], known) != f"EXACT_{old}":
        raise OSError("the installed bridge changed while the upgrade was being prepared")
    os.rename(places["canonical"], places["backup"])
    _fsync_dir(places["backup"].parent)
    _fsync_dir(packages)
    _write_record(record_path, dict(record, phase="OLD_MOVED"))
    _step("old_moved")
    os.rename(staged, places["canonical"])
    _fsync_dir(packages)
    _write_record(record_path, dict(record, phase="NEW_PLACED"))
    _step("new_placed")
    _finish(root, unity_project, key, record, known, manifest)
    return old


def _finish(root, unity_project, key, record, known, manifest):
    """The new package is in place: verify it, remove the exact backup and the transaction's empty directories,
    clear the record."""
    places = txn_places(root, unity_project, record["txn_id"])
    if differences(places["canonical"], manifest):
        raise UpgradeIncomplete("the installed bridge is not the audited bridge after the move",
                                {"canonical": PLACE_UNKNOWN})
    _write_record(txn_record_path(root, key), dict(record, phase="VERIFIED"))
    _step("verified")
    _remove_known(places["backup"], known[record["from_version"]], record["from_version"])
    _step("backup_removed")
    for place in ("staging", "backup"):
        _remove_empty(places[place].parent)
    os.unlink(txn_record_path(root, key))
    _fsync_dir(txn_record_path(root, key).parent)
    _step("record_cleared")


def _remove_known(path, manifest, version):
    """Remove a package directory that is exactly, or a strict subset of, `manifest`'s release — file by file,
    re-verified first. Anything else raises UpgradeIncomplete and nothing is removed."""
    state = classify_place(path, {version: manifest})
    if state == PLACE_ABSENT:
        return
    if state not in (f"EXACT_{version}", f"PARTIAL_{version}"):
        raise UpgradeIncomplete(f"{path.name} holds unknown content; it is left untouched", {"backup": state})
    for e in sorted(manifest["files"], key=lambda e: e["path"]):
        target = path / e["path"]
        if os.path.lexists(target):
            os.unlink(target)
            _step("backup_file_removed")
    for d in sorted(directories(manifest["files"]), key=lambda d: -d.count("/")):
        if os.path.lexists(path / d):
            os.rmdir(path / d)
    os.rmdir(path)


def _remove_empty(directory):
    try:
        os.rmdir(directory)
    except FileNotFoundError:
        pass
    except OSError:
        pass   # not empty: something that is not ours stays where it is


def recover(root, unity_project, key, rel, manifest):
    """Finish or roll back an interrupted upgrade from what is on disk. Returns None when there is no record,
    "COMPLETED" (the new bridge is installed exactly) or "ROLLED_BACK" (the exact old bridge is installed; nothing
    new is). Raises UpgradeIncomplete — changing nothing — when any involved package is unknown, the record
    cannot be trusted, or no known package can be established."""
    record = read_record(root, key)
    if record is None:
        return None
    if record["unity_project_rel"] != rel:
        raise UpgradeIncomplete("the upgrade transaction record names another Unity project")
    if (record["to_version"], record["to_digest"]) != (BRIDGE_VERSION, manifest["package_digest"]):
        raise UpgradeIncomplete("the interrupted upgrade was to a bridge this release does not ship")
    old = record["from_version"]
    known = history()
    known_all = dict(known, **{BRIDGE_VERSION: manifest})
    places = txn_places(root, unity_project, record["txn_id"])
    c = classify_place(places["canonical"], known_all)
    b = classify_place(places["backup"], {old: known[old]})
    s = classify_place(places["staging"], {BRIDGE_VERSION: manifest})
    found = {"canonical": c, "backup": b, "staging": s}
    new_exact, old_exact = f"EXACT_{BRIDGE_VERSION}", f"EXACT_{old}"
    if c == new_exact and b in (PLACE_ABSENT, old_exact, f"PARTIAL_{old}"):
        _finish_after_crash(root, unity_project, key, record, known, manifest, s)
        return "COMPLETED"
    if c == old_exact and b == PLACE_ABSENT:
        _discard_staging(places, manifest, s)
        _clear(root, unity_project, key, places)
        return "ROLLED_BACK"
    if c == PLACE_ABSENT and b == old_exact:
        if s == new_exact:
            os.rename(places["staging"], places["canonical"])
            _fsync_dir(places["canonical"].parent)
            _finish_after_crash(root, unity_project, key, record, known, manifest, PLACE_ABSENT)
            return "COMPLETED"
        os.rename(places["backup"], places["canonical"])   # restore the exact old package
        _fsync_dir(places["canonical"].parent)
        _discard_staging(places, manifest, s)
        _clear(root, unity_project, key, places)
        return "ROLLED_BACK"
    if c == PLACE_ABSENT and b == PLACE_ABSENT and s == new_exact:
        os.rename(places["staging"], places["canonical"])
        _fsync_dir(places["canonical"].parent)
        _finish_after_crash(root, unity_project, key, record, known, manifest, PLACE_ABSENT)
        return "COMPLETED"
    raise UpgradeIncomplete("the interrupted upgrade cannot be finished or rolled back from exact known packages; "
                            "nothing was changed", found)


def _finish_after_crash(root, unity_project, key, record, known, manifest, staging_state):
    places = txn_places(root, unity_project, record["txn_id"])
    _discard_staging(places, manifest, staging_state)
    _finish(root, unity_project, key, record, known, manifest)


def _discard_staging(places, manifest, state):
    if state in (f"EXACT_{BRIDGE_VERSION}", f"PARTIAL_{BRIDGE_VERSION}"):
        _remove_known(places["staging"], manifest, BRIDGE_VERSION)
    # an unknown staging directory is GPOS runtime scratch it cannot vouch for: it is left untouched


def _clear(root, unity_project, key, places):
    for place in ("staging", "backup"):
        _remove_empty(places[place].parent)
    os.unlink(txn_record_path(root, key))
    _fsync_dir(txn_record_path(root, key).parent)
