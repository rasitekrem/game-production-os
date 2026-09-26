"""The fixed GPOS live bridge package: release manifest, verification and the closed-project installer (Phase 2C-6A).

The bridge is GPOS release content: `live_bridge/com.gpos.live-bridge/` holds every file of the Unity package,
including fixed `.meta` files, and `live_bridge/manifest.json` records each file's size and SHA-256 plus the
package digest. Nothing about a project is written into the package, so its digest is the same everywhere.

    package digest = sha256 of the sorted lines  "<relative path>\\t<sha256>\\t<size>\\n"

The running bridge computes the same digest over its own files and publishes it; GPOS accepts a bridge only when
the manifest, the installed files and the running bridge agree.

The installer writes exactly this package into `<unity-project>/Packages/com.gpos.live-bridge/` of a *closed*
project and nothing else: no caller path, source or package, no `Packages/manifest.json` edit, no Package Manager,
no network. An identical package is left alone; anything else at that path is untrusted and never overwritten or
repaired. Files are staged in the GPOS runtime area and moved into place with one atomic rename.
"""

import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path

from .. import paths as tp

PACKAGE_ID = "com.gpos.live-bridge"
BRIDGE_VERSION = "1.0.0"
PROTOCOL = "gpos.unity.live/1"
MANIFEST_SCHEMA = "gpos.unity.live-bridge.manifest/1"
HERE = Path(__file__).resolve().parent
SOURCE = HERE / "live_bridge" / PACKAGE_ID
MANIFEST = HERE / "live_bridge" / "manifest.json"
MAX_FILE_BYTES = 1024 * 1024
ABSENT, EXACT, UNTRUSTED = "ABSENT", "EXACT", "UNTRUSTED"
STAGING = ("unity", "install-staging")


class BridgeSourceCorrupt(Exception):
    """The GPOS-owned bridge package does not match its release manifest."""


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


def load_manifest(path=MANIFEST):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    keys = {"schema", "package_id", "bridge_version", "protocol", "files", "package_digest"}
    if not isinstance(data, dict) or set(data) != keys or data["schema"] != MANIFEST_SCHEMA:
        raise BridgeSourceCorrupt("the bridge manifest does not have the expected shape")
    if (data["package_id"], data["bridge_version"], data["protocol"]) != (PACKAGE_ID, BRIDGE_VERSION, PROTOCOL):
        raise BridgeSourceCorrupt("the bridge manifest names another package, version or protocol")
    for e in data["files"]:
        if not isinstance(e, dict) or set(e) != {"path", "size", "sha256"}:
            raise BridgeSourceCorrupt("a bridge manifest entry does not have the expected shape")
    if digest(data["files"]) != data["package_digest"]:
        raise BridgeSourceCorrupt("the bridge manifest's package digest does not match its entries")
    return data


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


def inspect_target(unity_project, manifest):
    """(ABSENT | EXACT | UNTRUSTED, [difference]) for the installed package. Read-only."""
    target = package_dir(unity_project)
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return ABSENT, []
    if stat.S_ISLNK(st.st_mode):
        return UNTRUSTED, [f"Packages/{PACKAGE_ID} is a symbolic link"]
    if not stat.S_ISDIR(st.st_mode):
        return UNTRUSTED, [f"Packages/{PACKAGE_ID} is not a directory"]
    entries, problems = tree(target)
    expected = {e["path"]: e for e in manifest["files"]}
    found = {e["path"]: e for e in entries}
    differences = list(problems)
    differences += [f"{p}: missing" for p in sorted(set(expected) - set(found))]
    differences += [f"{p}: not part of the audited bridge" for p in sorted(set(found) - set(expected))]
    differences += [f"{p}: modified" for p in sorted(set(expected) & set(found)) if expected[p] != found[p]]
    extra_dirs = _directories_on_disk(target) - directories(manifest["files"])
    differences += [f"{d}/: not part of the audited bridge" for d in sorted(extra_dirs)]
    return (EXACT, []) if not differences else (UNTRUSTED, differences)


def _directories_on_disk(target):
    out = set()
    for dirpath, dirnames, _ in os.walk(target, followlinks=False):
        for name in dirnames:
            rel = (Path(dirpath) / name).relative_to(target).as_posix()
            out.add(rel)
    return out


def install(root, unity_project, manifest, source=SOURCE):
    """Write the audited package into a closed project. Returns (EXACT, written) — written False when the
    identical package was already there. Raises BridgeSourceCorrupt or OSError; the caller has already
    checked that the project is closed and holds the writer lease."""
    state, differences = inspect_target(unity_project, manifest)
    if state == EXACT:
        return EXACT, False
    if state == UNTRUSTED:
        raise ValueError(differences)
    packages = Path(unity_project) / "Packages"
    if packages.is_symlink() or not packages.is_dir():
        raise OSError(f"{packages} is not a real directory")
    staging = tp.runtime_dir(root, *STAGING, uuid.uuid4().hex)
    staged = staging / PACKAGE_ID
    try:
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
        if os.path.lexists(package_dir(unity_project)):
            raise ValueError([f"Packages/{PACKAGE_ID} appeared while the bridge was being staged"])
        os.rename(staged, package_dir(unity_project))   # one atomic step; a cross-device move is refused, not copied
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return EXACT, True
