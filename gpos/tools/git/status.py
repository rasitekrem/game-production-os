"""Parse `status --porcelain=v2 --branch -z` output into normalized repository state.

A pure function over the exact bytes Git wrote: it starts no process and reads no file. It takes
the process boundary's private raw capture, never the public redacted text, because redaction may
rewrite a machine protocol (a credential-shaped path can swallow the NUL that ends its record).

The format is Git's documented machine interface, which the `--porcelain` option promises "will
remain stable across Git versions and regardless of user configuration":

    # branch.oid <commit> | (initial)
    # branch.head <branch> | (detached)
    1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
    2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path><NUL><origPath>
    u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
    ? <path>
    ! <path>

It works on bytes throughout. With `-z` every record ends in NUL and a path is printed as-is, so it
may contain spaces, newlines, non-ASCII characters and any byte the platform allows except NUL.
Records are split on NUL only, and the fixed fields before a path are split with an explicit
maximum, so the path is never cut, never decoded and never needed: only counts are produced. A
rename carries its original path as a separate NUL-terminated field, which is consumed rather than
mistaken for a new record.

The branch name is the only text taken from a record. It is decoded after the record boundaries
are fixed, so an undecodable byte cannot move a boundary, and it is decoded deterministically
(invalid UTF-8 becomes a backslash escape such as `\\xff`, never a guess).

Anything the parser does not recognize — an unknown record type, a malformed field, a missing branch
header, an unterminated final record — is an error, never a guess: a partial or unexpected reading
must not be reported as a clean repository. Header lines that are not needed are ignored, as the
documentation directs ("Parsers should ignore headers they don't recognize").
"""

import re

OBJECT_ID = re.compile(rb"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")  # SHA-1 or SHA-256 object format
INITIAL, DETACHED = b"(initial)", b"(detached)"
STATUS_CODES = frozenset(b".MTADRCU")

# Fields before the path in each record type, counting the type marker itself.
FIELDS_BEFORE_PATH = {b"1": 8, b"2": 9, b"u": 10}


class StatusParseError(ValueError):
    """The output is not complete, well-formed porcelain v2; no state may be reported from it."""


def parse(raw):
    """Normalized repository state from the exact bytes of porcelain v2 `-z` output."""
    if not isinstance(raw, (bytes, bytearray)):
        raise StatusParseError("status output must be the exact captured bytes")
    raw = bytes(raw)
    if raw and not raw.endswith(b"\0"):
        raise StatusParseError("the final record is not NUL-terminated; the output is incomplete")
    records = raw.split(b"\0")[:-1] if raw else []
    oid = head = None
    staged = unstaged = untracked = conflicted = 0
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if record.startswith(b"# "):
            key, _, value = record[2:].partition(b" ")
            if key == b"branch.oid":
                oid = value
            elif key == b"branch.head":
                head = value
            continue
        kind = record[:1]
        if record[1:2] != b" ":
            raise StatusParseError(f"unrecognized status record {record[:12]!r}")
        if kind == b"?":
            untracked += 1
            continue
        if kind == b"!":  # ignored files are not requested; tolerated if a configuration adds them
            continue
        if kind not in FIELDS_BEFORE_PATH:
            raise StatusParseError(f"unrecognized status record type {kind!r}")
        parts = record.split(b" ", FIELDS_BEFORE_PATH[kind])
        if len(parts) != FIELDS_BEFORE_PATH[kind] + 1 or not parts[-1]:
            raise StatusParseError(f"malformed {kind!r} status record")
        xy = parts[1]
        if len(xy) != 2 or not set(xy) <= STATUS_CODES:
            raise StatusParseError(f"malformed XY status field {xy!r}")
        if kind == b"u":
            conflicted += 1
            continue
        if kind == b"2":
            if i >= len(records):
                raise StatusParseError("a rename record is missing its original path")
            i += 1  # the original path is its own NUL-terminated field
        if xy[0] != ord("."):
            staged += 1
        if xy[1] != ord("."):
            unstaged += 1
    return _state(oid, head, staged, unstaged, untracked, conflicted)


def branch_text(value):
    """A branch name as deterministic text: UTF-8 where valid, backslash escapes where not."""
    return value.decode("utf-8", errors="backslashreplace")


def _state(oid, head, staged, unstaged, untracked, conflicted):
    if oid is None or head is None:
        raise StatusParseError("the branch headers are missing; the output is incomplete")
    unborn = oid == INITIAL
    if not unborn and not OBJECT_ID.fullmatch(oid):
        raise StatusParseError(f"branch.oid {oid[:80]!r} is not a commit object id")
    detached = head == DETACHED
    if unborn and detached:
        raise StatusParseError("a detached HEAD cannot be unborn")
    if not head:
        raise StatusParseError("branch.head is empty")
    head_sha = None if unborn else oid.decode("ascii")
    clean = staged == unstaged == untracked == conflicted == 0
    return {
        "head_sha": head_sha,
        "branch": None if detached else branch_text(head),
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
