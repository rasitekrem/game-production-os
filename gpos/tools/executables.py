"""Finding a production tool's executable on Windows (alpha.24).

On Windows `shutil.which` searches the current directory before PATH (a stray `<tool>.exe` in the project would win)
and honours PATHEXT, so a `.cmd` or `.bat` shim earlier on PATH is found first — which the alpha.23 process boundary
then refuses, leaving a usable tool undiscovered. `windows_which` is the deterministic lookup the adapters use on
Windows instead: the directories of PATH in order, never the current directory, only absolute local directories, and
only the exact name `<name>.exe`. It starts nothing and decides nothing about trust: the adapter resolves what it
finds, and the process boundary still validates and pins the executable before anything runs.

POSIX hosts keep `shutil.which` unchanged.
"""

import os
from pathlib import PureWindowsPath


def windows_which(name, path=None):
    """The first `<name>.exe` (or `name` when it already ends in .exe) in an absolute PATH directory, or None."""
    if not name or any(c in name for c in '\\/:'):
        return None
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for entry in (os.environ.get("PATH", "") if path is None else path).split(os.pathsep):
        entry = entry.strip().strip('"')
        p = PureWindowsPath(entry)
        if not entry or not p.drive or len(p.drive) != 2 or not p.root:   # relative, drive-relative or UNC: skipped
            continue
        candidate = os.path.join(entry, exe)
        if os.path.isfile(candidate):
            return candidate
    return None
