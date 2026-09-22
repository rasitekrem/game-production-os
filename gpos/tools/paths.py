"""Filesystem boundary for tool execution and artifacts.

The foundation default is project-root bounded: a capability's working directory, its output
directory and every artifact it declares must resolve inside the project root (or inside the
explicit extra scopes its adapter contract declares). Nothing here follows a symlink out of the
scope, and nothing outside a scope is ever written, hashed as an owned artifact or deleted.

A future adapter that legitimately reads outside the project (a system tool's own installation,
a user-nominated input file) declares those scopes in its capability contract. The foundation
never grants arbitrary filesystem authority implicitly.
"""

import os
from pathlib import Path, PurePosixPath

RUNTIME_DIR = ".game/gpos-runtime"  # non-authoritative generated state; never a GPOS record
RECORDS_DIR = ".game/gpos"          # canonical records: tool execution never writes here


def is_safe_relative(path):
    """A relative POSIX path with no `..`, `.`, empty part, backslash, NUL or drive letter."""
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return False
    p = PurePosixPath(path)
    if p.is_absolute() or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return False
    return all(part not in ("..", ".", "") for part in path.split("/"))


def contains(scope, path):
    """True when `path` (resolved) is `scope` itself or lies under it."""
    scope, path = Path(scope).resolve(), Path(path).resolve()
    return path == scope or scope in path.parents


def _roots(scopes):
    """Every scope as a resolved absolute path."""
    return [Path(s).resolve() for s in scopes]


def _under(roots, path):
    return next((r for r in roots if path == r or r in path.parents), None)


def _resolve_prefix(path):
    """`path` with its deepest existing ancestor resolved: containment is judged after symlinks are
    followed, while parts that do not exist yet are kept verbatim."""
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    return existing.resolve() / path.relative_to(existing)


def unsafe_reason(scopes, path, must_exist=False):
    """Why `path` may not be touched given the permitted `scopes`, or None.

    The path is normalised lexically, so `..` cannot walk out. Every existing component *below* the
    scope is then checked, so a symlink planted inside the project is refused before the target is
    read or written. Finally the path is resolved and must still be inside a scope, so a symlink
    cannot move the target out of the project either. A scope that itself lies under a symlinked
    directory (macOS `/var` -> `/private/var`) is handled: only components below the scope matter.
    """
    if not scopes:
        return f"{path}: no permitted filesystem scope is declared"
    if not Path(path).is_absolute():
        return f"{path}: must be an absolute path"
    lexical = Path(os.path.normpath(str(path)))
    roots = _roots(scopes)
    outside = f"{path}: resolves outside the permitted scopes {sorted({str(r) for r in roots})}"
    current, inside = Path(lexical.parts[0]), False
    for part in lexical.parts[1:]:
        current = current / part
        if inside:
            if os.path.islink(current):
                return f"{path}: component {current} is a symlink"
            if not current.exists():
                break
        elif current.exists() and _under(roots, current.resolve()) is not None:
            inside = True
    if _under(roots, _resolve_prefix(lexical)) is None:
        return outside
    if must_exist and not lexical.exists():
        return f"{path}: does not exist"
    return None


def in_records(root, path):
    """True when the path lies in the canonical record area; tool execution must never write there."""
    return contains(Path(root) / RECORDS_DIR, path)


def runtime_dir(root, *parts):
    """A path inside the project's non-authoritative runtime area."""
    return Path(root).resolve().joinpath(*RUNTIME_DIR.split("/"), *parts)
