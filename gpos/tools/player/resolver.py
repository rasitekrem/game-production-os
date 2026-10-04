"""Build resolution (alpha.22): from a canonical `build_id` to exactly one revalidated, launchable payload.

A closed table maps (platform, payload kind) to a resolver; alpha.22 has one entry, the alpha.21 Unity
`MACOS_APP_BUNDLE`. The Unity resolver uses the frozen alpha.21 build core unchanged: the build id names the build's
own execution workspace (`.game/gpos-runtime/tool-output/unity/<request id>/`), `revalidate` recomputes the payload
tree digest against the build manifest, and the manifest's own bytes are hashed so that a re-serialised manifest is a
different build. Nothing is searched for: no PATH, no Finder, no other directory, no "latest" build.
"""

import hashlib
import os
import plistlib
import stat
from dataclasses import dataclass
from pathlib import Path

from .. import paths as tp
from ..unity import build as ub
from ..unity.adapter import ADAPTER_ID as UNITY_ADAPTER_ID

MACOS_APP_BUNDLE = "MACOS_APP_BUNDLE"
MAX_MANIFEST_BYTES = 1024 * 1024


class BuildProblem(Exception):
    pass


@dataclass(frozen=True)
class Target:
    build_id: str
    build_revision: str
    manifest_sha256: str
    tree_digest: str
    unity_build_guid: str
    application_id: str
    executable: str              # canonical absolute path of the Player executable
    dev: int
    ino: int
    workspace: str
    kind: str = MACOS_APP_BUNDLE
    target_platform: str = "MACOS"

    def executable_relative(self, root):
        return os.path.relpath(self.executable, root)


def _manifest_sha(path):
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_MANIFEST_BYTES:
        raise BuildProblem("the build manifest is not a bounded regular file")
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read(MAX_MANIFEST_BYTES + 1)).hexdigest()


def resolve_unity_macos(root, build_id):
    """Target for a completed alpha.21 Unity macOS build of this project, revalidated now; BuildProblem otherwise."""
    if not isinstance(build_id, str) or not ub.BUILD_ID.fullmatch(build_id):
        raise BuildProblem(f"{build_id!r} is not a GPOS build id")
    root = Path(root).resolve()
    workspace = tp.runtime_dir(root, "tool-output", UNITY_ADAPTER_ID, build_id[len("build-"):])
    current = root
    for part in workspace.relative_to(root).parts:
        current = current / part
        if os.path.islink(current):
            raise BuildProblem("the build workspace is reached through a link")
    if not workspace.is_dir():
        raise BuildProblem(f"no build {build_id} exists in this project")
    manifest_path = workspace / ub.MANIFEST_NAME
    try:
        before = _manifest_sha(manifest_path)
    except FileNotFoundError:
        raise BuildProblem(f"build {build_id} has no build manifest: it is not complete") from None
    manifest, why = ub.revalidate(workspace)
    if why:
        raise BuildProblem(f"build {build_id} no longer revalidates: {why}")
    if _manifest_sha(manifest_path) != before:
        raise BuildProblem(f"the manifest of build {build_id} changed while it was being revalidated")
    payload = manifest.get("payload") or {}
    if payload.get("kind") != MACOS_APP_BUNDLE or manifest.get("target") != ub.TARGET:
        raise BuildProblem(f"build {build_id} is not a macOS application bundle")
    app = workspace / ub.PAYLOAD / ub.APP
    try:
        with open(app / "Contents" / "Info.plist", "rb") as fh:
            info = plistlib.load(fh)
    except Exception:   # plistlib raises several types for malformed input
        raise BuildProblem("the Player's Info.plist does not parse") from None
    application_id, name = info.get("CFBundleIdentifier"), info.get("CFBundleExecutable")
    if application_id != payload.get("bundle_identifier") or name != payload.get("executable") or not application_id:
        raise BuildProblem("the Player bundle no longer names the manifest's identifier and executable")
    executable = app / "Contents" / "MacOS" / name
    if os.path.realpath(executable) != str(executable):
        raise BuildProblem("the Player executable is reached through a link")
    st = os.lstat(executable)
    if not stat.S_ISREG(st.st_mode) or not st.st_mode & stat.S_IXUSR:
        raise BuildProblem("the Player executable is not a regular executable file")
    guid = (manifest.get("unity_build") or {}).get("guid")
    return Target(build_id=build_id, build_revision=manifest.get("build_revision"), manifest_sha256=before,
                  tree_digest=payload["tree_digest"], unity_build_guid=guid, application_id=application_id,
                  executable=str(executable), dev=st.st_dev, ino=st.st_ino, workspace=str(workspace))


RESOLVERS = {("MACOS", MACOS_APP_BUNDLE): resolve_unity_macos}


def resolve(root, build_id, platform="MACOS"):
    """The one resolver alpha.22 has: a Unity macOS application bundle."""
    return RESOLVERS[(platform, MACOS_APP_BUNDLE)](root, build_id)


def revalidates(root, session):
    """(VALID or DRIFT, reason or None): does the session's build still resolve to the very same bytes?"""
    try:
        target = resolve(root, session["build_id"])
    except (BuildProblem, OSError) as exc:
        return "DRIFT", str(exc)
    if target.manifest_sha256 != session["manifest_sha256"]:
        return "DRIFT", "the build manifest changed since launch"
    if target.tree_digest != session["tree_digest"]:
        return "DRIFT", "the payload tree changed since launch"
    if (target.dev, target.ino) != (session["executable_dev"], session["executable_ino"]):
        return "DRIFT", "the Player executable file was replaced since launch (EXECUTABLE_REPLACED)"
    return "VALID", None
