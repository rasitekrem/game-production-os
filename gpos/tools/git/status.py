"""Parse `status --porcelain=v2 --branch -z` output into normalized repository state.

A pure function over text: it starts no process and reads no file. The format is Git's documented
machine interface, which the `--porcelain` option promises "will remain stable across Git versions
and regardless of user configuration":

    # branch.oid <commit> | (initial)
    # branch.head <branch> | (detached)
    1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
    2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path><NUL><origPath>
    u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
    ? <path>
    ! <path>

With `-z` every record ends in NUL and paths are printed as-is, so a path may contain spaces,
non-ASCII characters and even newlines: records are split on NUL only, never on lines, and the
fixed fields before a path are split with an explicit maximum so the path itself is never cut.
A rename carries its original path as a separate NUL-terminated field, which is consumed rather
than mistaken for a new record.

Only counts are produced. The public contract is the state of the repository, not a dump of its
paths. Anything the parser does not recognize — an unknown record type, a malformed field, a
missing branch header, an unterminated final record — is an error, never a guess: a partial or
unexpected reading must not be reported as a clean repository. Header lines that are not needed
are ignored, as the documentation directs ("Parsers should ignore headers they don't recognize").
"""

import re

OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")  # SHA-1 or SHA-256 object format
INITIAL, DETACHED = "(initial)", "(detached)"
STATUS_CODES = frozenset(".MTADRCU")

# Fields before the path in each record type, counting the type marker itself.
FIELDS_BEFORE_PATH = {"1": 8, "2": 9, "u": 10}


class StatusParseError(ValueError):
    """The output is not complete, well-formed porcelain v2; no state may be reported from it."""


def parse(text):
    """Normalized repository state from porcelain v2 `-z` output. Raises StatusParseError."""
    if not isinstance(text, str):
        raise StatusParseError("status output must be text")
    if text and not text.endswith("\0"):
        raise StatusParseError("the final record is not NUL-terminated; the output is incomplete")
    records = text.split("\0")[:-1] if text else []
    oid = head = None
    staged = unstaged = untracked = conflicted = 0
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if record.startswith("# "):
            key, _, value = record[2:].partition(" ")
            if key == "branch.oid":
                oid = value
            elif key == "branch.head":
                head = value
            continue
        kind = record[:1]
        if record[1:2] != " ":
            raise StatusParseError(f"unrecognized status record {record[:12]!r}")
        if kind == "?":
            untracked += 1
            continue
        if kind == "!":  # ignored files are not requested; tolerated if a configuration adds them
            continue
        if kind not in FIELDS_BEFORE_PATH:
            raise StatusParseError(f"unrecognized status record type {kind!r}")
        parts = record.split(" ", FIELDS_BEFORE_PATH[kind])
        if len(parts) != FIELDS_BEFORE_PATH[kind] + 1 or not parts[-1]:
            raise StatusParseError(f"malformed {kind!r} status record")
        xy = parts[1]
        if len(xy) != 2 or not set(xy) <= STATUS_CODES:
            raise StatusParseError(f"malformed XY status field {xy!r}")
        if kind == "u":
            conflicted += 1
            continue
        if kind == "2":
            if i >= len(records):
                raise StatusParseError("a rename record is missing its original path")
            i += 1  # the original path is its own NUL-terminated field
        if xy[0] != ".":
            staged += 1
        if xy[1] != ".":
            unstaged += 1
    return _state(oid, head, staged, unstaged, untracked, conflicted)


def _state(oid, head, staged, unstaged, untracked, conflicted):
    if oid is None or head is None:
        raise StatusParseError("the branch headers are missing; the output is incomplete")
    unborn = oid == INITIAL
    if not unborn and not OBJECT_ID.match(oid):
        raise StatusParseError(f"branch.oid {oid!r} is not a commit object id")
    detached = head == DETACHED
    if unborn and detached:
        raise StatusParseError("a detached HEAD cannot be unborn")
    if not head:
        raise StatusParseError("branch.head is empty")
    head_sha = None if unborn else oid
    clean = staged == unstaged == untracked == conflicted == 0
    return {
        "head_sha": head_sha,
        "branch": None if detached else head,
        "detached": detached,
        "unborn": unborn,
        "clean": clean,
        # A dirty working tree is never represented as exactly its HEAD commit, and an unborn
        # branch has no commit at all. HEAD stays available as a baseline in head_sha.
        "exact_revision": head_sha if (head_sha is not None and clean) else None,
        "staged_count": staged,
        "unstaged_count": unstaged,
        "untracked_count": untracked,
        "conflicted_count": conflicted,
    }
