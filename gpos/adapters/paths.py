"""Path safety for managed files. Every path the adapter writes, compares or deletes passes here.

A managed path is relative, POSIX, free of `..`/`.`/empty parts, backslashes and NUL, and lies in
the backend's managed area: its entry file, `<skill_root>/gpos-*`, or its manifest directory.
On disk, no existing component of the path may be a symlink, and the resolved path must stay
inside the project root. Nothing outside these rules is ever written or deleted.
"""

import os
from pathlib import Path, PurePosixPath


def is_safe_relative(path):
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return False
    p = PurePosixPath(path)
    if p.is_absolute() or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return False
    return all(part not in ("..", ".", "") for part in path.split("/"))


def is_managed(backend, path):
    if not is_safe_relative(path):
        return False
    if path == backend.entrypoint:
        return True
    return any(path.startswith(prefix) and len(path) > len(prefix) for prefix in backend.managed_prefixes())


def unsafe_on_disk(root, path):
    """Reason the path is unsafe to touch under `root`, or None. Checks every existing component."""
    root = Path(root).resolve()
    current = root
    for part in path.split("/"):
        current = current / part
        if os.path.islink(current):
            return f"{path}: component {current.relative_to(root)} is a symlink"
        if not current.exists():
            break
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        return f"{path}: resolves outside the project"
    return None


def managed_files_on_disk(root, backend):
    """Every file currently inside the backend's managed area (entry file, gpos-* skills, manifest dir)."""
    root = Path(root)
    found = set()
    if (root / backend.entrypoint).is_file() or os.path.islink(root / backend.entrypoint):
        found.add(backend.entrypoint)
    skill_root = root / backend.skill_root
    if skill_root.is_dir() and not os.path.islink(skill_root):
        for d in sorted(skill_root.iterdir()):
            if d.name.startswith("gpos-"):
                found |= _walk(root, d)
    mdir = root / backend.manifest_dir
    if mdir.is_dir() and not os.path.islink(mdir):
        found |= _walk(root, mdir)
    return found


def _walk(root, start):
    out = set()
    if os.path.islink(start) or start.is_file():
        return {start.relative_to(root).as_posix()}
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            out.add((Path(dirpath) / name).relative_to(root).as_posix())
    return out
