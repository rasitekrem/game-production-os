"""The GPOS Player Helper release and its one installation (alpha.22).

The helper is release content: `helper_src/` holds its source, `helper_release/GposPlayerHelper.app` the exact
prebuilt, ad-hoc-signed universal bundle built from it, and `helper_release/manifest.json` binds the two (per-file size,
mode and SHA-256, the bundle digest, the per-architecture CDHash, the source digest and the toolchain). Production never
compiles, signs, downloads or edits a privacy setting: `tests/build_player_helper.py` is maintainer tooling only.

An installed helper at the one fixed location (`~/Applications/GPOS/GposPlayerHelper.app`, the home directory taken from
the account database) is

    ABSENT     nothing there
    EXACT      byte for byte this release: exactly its directories and files, sizes, modes and hashes, no link
    UNTRUSTED  anything else — never overwritten, repaired or removed

`install` copies the release into a staging directory beside the destination, verifies it, and publishes it with
`renamex_np(RENAME_EXCL)`, which refuses atomically when anything exists at the destination (measured: a plain rename
replaces an existing empty directory, so it is never the no-overwrite authority). A destination that appears during
the install is re-classified: EXACT is success, anything else a conflict. Only this execution's own staging directory
is ever removed.
"""

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent / "helper_release"
BUNDLE_NAME = "GposPlayerHelper.app"
BUNDLE_ALGORITHM = "gpos.player-helper.bundle/1"
STAGING_PREFIX = ".gpos-staging-"
RENAME_EXCL = 0x00000004
EXACT, ABSENT, UNTRUSTED = "EXACT", "ABSENT", "UNTRUSTED"

_MANIFEST = {}


class InstallConflict(Exception):
    pass


def release():
    """The release manifest (read once). Its bundle digest is recomputed from its own file list."""
    if "m" not in _MANIFEST:
        m = json.loads((RELEASE_DIR / "manifest.json").read_text("utf-8"))
        lines = [f"{f['path']}\t{f['sha256']}\t{f['size']}\t{f['mode']}" for f in m["files"]]
        if m["bundle_digest"] != _digest(lines) or m["bundle_digest_algorithm"] != BUNDLE_ALGORITHM:
            raise ValueError("the helper release manifest contradicts its own file list")
        _MANIFEST["m"] = m
    return _MANIFEST["m"]


def _digest(lines):
    return hashlib.sha256((BUNDLE_ALGORITHM + "\n" + "".join(l + "\n" for l in sorted(lines))).encode("utf-8")).hexdigest()


def version():
    return release()["helper_version"]


def cdhashes():
    return dict(release()["signature"]["cdhashes"])


def install_path(host_location):
    return Path(host_location) / BUNDLE_NAME


def executable(bundle):
    return Path(bundle) / release()["executable"]


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def classify(bundle):
    """(EXACT | ABSENT | UNTRUSTED, reason). Walks with lstat only; never follows a link."""
    bundle = Path(bundle)
    if not os.path.lexists(bundle):
        return ABSENT, "nothing is installed"
    m = release()
    try:
        st = os.lstat(bundle)
        if not stat.S_ISDIR(st.st_mode):
            return UNTRUSTED, "the installed bundle is not a real directory"
        want_files = {f["path"]: f for f in m["files"]}
        want_dirs, seen_files, seen_dirs = set(m["directories"]), set(), set()
        for current, dirnames, filenames in os.walk(bundle, followlinks=False):
            for name in dirnames + filenames:
                full = Path(current) / name
                rel = full.relative_to(bundle).as_posix()
                est = os.lstat(full)
                if stat.S_ISDIR(est.st_mode):
                    seen_dirs.add(rel)
                elif stat.S_ISREG(est.st_mode):
                    f = want_files.get(rel)
                    if f is None:
                        return UNTRUSTED, f"an unexpected file {rel} is installed"
                    if est.st_size != f["size"] or format(est.st_mode & 0o777, "04o") != f["mode"] or _sha(full) != f["sha256"]:
                        return UNTRUSTED, f"{rel} is not the released file"
                    seen_files.add(rel)
                else:
                    return UNTRUSTED, f"{rel} is a link or a special file"
        if seen_dirs != want_dirs or seen_files != set(want_files):
            return UNTRUSTED, "the installed bundle does not hold exactly the released files"
    except OSError as exc:
        return UNTRUSTED, f"the installed bundle cannot be read ({type(exc).__name__})"
    return EXACT, "byte for byte this release"


def staging_leftovers(host_location, own=None):
    """Names of earlier, interrupted installs' staging directories (never removed automatically)."""
    try:
        names = os.listdir(host_location)
    except OSError:
        return []
    return sorted(n for n in names if n.startswith(STAGING_PREFIX) and n != own)


# ---------------------------------------------------------------- the atomic no-replace publication

def rename_exclusive(source, destination):
    """renamex_np(source, destination, RENAME_EXCL): moves only when nothing exists at the destination (a file, a
    directory — even an empty one — or a link), atomically. Raises FileExistsError or OSError."""
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    lib.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    if lib.renamex_np(os.fsencode(source), os.fsencode(destination), RENAME_EXCL) != 0:
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(err, "the destination exists", str(destination))
        raise OSError(err, os.strerror(err), str(destination))


def _real_dir(path):
    """Create `path` (mode 0755) when absent; it must then be a real directory, never a link."""
    try:
        os.mkdir(path, 0o755)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise InstallConflict(f"{Path(path).name} exists and is not a real directory")


def _copy_release(staged):
    m = release()
    os.mkdir(staged, 0o755)
    for d in sorted(m["directories"], key=lambda p: p.count("/")):
        os.mkdir(staged / d, 0o755)
    for f in m["files"]:
        source = RELEASE_DIR / BUNDLE_NAME / f["path"]
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != f["sha256"] or len(data) != f["size"]:
            raise InstallConflict(f"the release file {f['path']} does not match the release manifest")
        fd = os.open(staged / f["path"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, int(f["mode"], 8))
        try:
            os.write(fd, data)
            os.fchmod(fd, int(f["mode"], 8))
            os.fsync(fd)
        finally:
            os.close(fd)


def install(host_location, request_id, publish=rename_exclusive):
    """Install the release at `<host_location>/GposPlayerHelper.app`. Returns (state, created) where state is EXACT and
    created says whether this execution published it; raises InstallConflict for an UNTRUSTED or raced-in destination.
    `publish` is a code-level seam for the race tests; production always uses rename_exclusive."""
    host = Path(host_location)
    destination = host / BUNDLE_NAME
    state, reason = classify(destination)
    if state == EXACT:
        return EXACT, False
    if state == UNTRUSTED:
        raise InstallConflict(f"an installed helper exists and is not this release ({reason}); it is never overwritten, "
                              f"repaired or removed")
    _real_dir(host.parent)
    _real_dir(host)
    staging = host / f"{STAGING_PREFIX}{request_id}"
    os.mkdir(staging, 0o755)            # this execution's own staging directory; an existing one is never reused
    staged = staging / BUNDLE_NAME
    try:
        _copy_release(staged)
        if classify(staged)[0] != EXACT:
            raise InstallConflict("the staged helper did not verify")
        try:
            publish(staged, destination)
        except FileExistsError:
            raced, why = classify(destination)
            if raced != EXACT:
                raise InstallConflict(f"a helper appeared at the destination during the install and is not this release "
                                      f"({why}); it was not touched") from None
            return EXACT, False
        state, reason = classify(destination)
        if state != EXACT:
            raise InstallConflict(f"the published helper does not verify ({reason}); it is left in place")
        return EXACT, True
    finally:
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)      # only this execution's own staging directory (empty after a publish)
