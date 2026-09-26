"""Where a Unity project's live runtime state lives, and how the live bridge's identity is checked (Phase 2C-6A).

Both sides derive the same place without anyone naming it: the bridge walks up from its Unity project to the
nearest GPOS root, GPOS starts from the request's project root, and each computes

    project key = sha256("gpos.unity.live\\0" + <Unity project path relative to the GPOS root, or ".">)[:16]
    live dir    = <GPOS root>/.game/gpos-runtime/unity/live/<project key>/

from canonical paths (symbolic links resolved, each component in its on-disk case, as the bridge's realpath
returns it). No request ever supplies a live directory, and two Unity projects under one GPOS root never share
one. A bridge is only trusted for a request when its published GPOS root and Unity project are the same
directories (same device and inode) as the request's.
"""

import hashlib
import os
from pathlib import Path

from .. import paths as tp

KEY_PREFIX = "gpos.unity.live\0"
MAX_ASCENT = 16
LIVE = ("unity", "live")


def canonical(path):
    """The real path with every component spelled as it is on disk (a case-insensitive file system accepts
    other spellings; the bridge sees only the on-disk one)."""
    real = Path(os.path.realpath(path))
    out = Path(real.parts[0])
    for part in real.parts[1:]:
        try:
            names = os.listdir(out)
        except OSError:
            out = out / part
            continue
        if part in names:
            out = out / part
        else:
            matches = [n for n in names if n.casefold() == part.casefold()]
            out = out / (matches[0] if len(matches) == 1 else part)
    return out


def relative(root, unity_project):
    rel = canonical(unity_project).relative_to(canonical(root)).as_posix()
    return rel if rel not in ("", ".") else "."


def project_key(rel):
    return hashlib.sha256((KEY_PREFIX + rel).encode("utf-8")).hexdigest()[:16]


def live_dir(root, key):
    return tp.runtime_dir(root, *LIVE, key)


def root_spelling_problem(root):
    """Why the GPOS root cannot be used for a live session as spelled, or None. The SESSION lease names the
    resolved root, and the bridge finds the lease by the canonical root: the two must be the same text."""
    resolved, real = str(Path(root).resolve()), str(canonical(root))
    if resolved != real:
        return f"spell the GPOS project root as it is on disk ({real}), not {resolved}"
    return None


def same_directory(a, b):
    try:
        return os.path.samefile(a, b)
    except (OSError, TypeError, ValueError):
        return False


def gpos_root_of(unity_project):
    """The GPOS root the bridge would find for this Unity project (the nearest ancestor, at most MAX_ASCENT
    levels up, holding .game/gpos/project-config.json with no link on the way), or None."""
    d = canonical(unity_project)
    for _ in range(MAX_ASCENT + 1):
        game = d / ".game"
        config = game / "gpos" / "project-config.json"
        if (game.is_dir() and not game.is_symlink() and (game / "gpos").is_dir() and not (game / "gpos").is_symlink()
                and config.is_file() and not config.is_symlink()):
            return d
        if d.parent == d:
            return None
        d = d.parent
    return None
