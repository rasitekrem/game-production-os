"""The Player log as it may leave GPOS (alpha.22): bounded, normalized and sanitized.

The Player writes exactly one log, `player.log` in the runtime directory (the fixed `-logFile` argument). GPOS reads at
most its last 8 MiB and passes every line through, in order:

    1 normalization   the runtime directory and the project root become relative (removed with their trailing slash),
                      the home directory (from the account database) becomes `~`
    2 redaction       the GPOS credential redaction boundary, unchanged
    3 PLAYER_PATH     an absolute or `~/` path becomes `<path>`; it continues across a space when the next token
                      contains a `/`, so a path such as `~/Library/Application Support/<Company>/<Product>/x` is removed
                      whole instead of leaving a ` Support/...` fragment
    4 ABSOLUTE_PATH   the alpha.20 absolute-path rule, unchanged, as defence in depth
    5 bounds          every line is clipped; the artifact keeps the newest lines within 1 MiB and says how many it
                      omitted

This is player-only: the alpha.20 compiler-message and alpha.21 BuildReport sanitizers are not changed. A path whose
last component contains a space and no further `/` can leave that last word; that residual is disclosed.
"""

import os
import re

from .. import paths as tp
from ..redaction import redact
from ..unity.sources import ABSOLUTE_PATH
from . import contract as c

MAX_LINE_CHARS = 2000
_STOP = r"\s'\"()<>:|"
PLAYER_PATH = re.compile(
    rf"(?:(?<![\w.~<>])~/|(?<![\w.<>])/(?:Users|private|var|tmp|Volumes|Applications|home|opt|Library|System|usr|etc|cores)/)"
    rf"[^{_STOP}]*(?:[ \t]+[^{_STOP}/]*/[^{_STOP}]*)*")


def bases(root, runtime_dir):
    """(literal, replacement) pairs, longest first: the runtime dir and project root (as given and resolved) become
    relative, the account home becomes `~`."""
    pairs = []
    for base in (runtime_dir, root):
        if base:
            for spelling in {str(base), os.path.realpath(base)}:
                pairs.append((spelling.rstrip("/") + "/", ""))
    home = str(tp.account_home())
    for spelling in {home, os.path.realpath(home)}:
        pairs.append((spelling.rstrip("/"), "~"))
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def sanitize_line(line, pairs):
    for literal, replacement in pairs:
        line = line.replace(literal, replacement)
    line, _ = redact(line)
    line = PLAYER_PATH.sub("<path>", line)
    line = ABSOLUTE_PATH.sub("<path>", line)
    return line[:MAX_LINE_CHARS]


def _tail_lines(path, limit):
    """(lines, truncated) of the last `limit` bytes of a regular, non-link file; None when unreadable."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    import stat
    if not stat.S_ISREG(st.st_mode):
        return None
    with open(path, "rb") as fh:
        truncated = st.st_size > limit
        if truncated:
            fh.seek(st.st_size - limit)
        data = fh.read(limit)
    lines = data.decode("utf-8", errors="replace").splitlines()
    if truncated and lines:
        lines = lines[1:]   # the first line was cut by the read window
    return lines, truncated


def sanitized(path, root, runtime_dir, max_read=c.MAX_LOG_READ_BYTES, max_out=c.MAX_LOG_ARTIFACT_BYTES):
    """(text, facts) of the sanitized log artifact, or (None, None) when there is no readable log."""
    tail = _tail_lines(path, max_read)
    if tail is None:
        return None, None
    lines, truncated = tail
    pairs = bases(root, runtime_dir)
    clean = [sanitize_line(l, pairs) for l in lines]
    kept, size = [], 0
    for line in reversed(clean):
        cost = len(line.encode("utf-8")) + 1
        if size + cost > max_out - 200:
            break
        kept.append(line)
        size += cost
    kept.reverse()
    omitted = len(clean) - len(kept)
    header = []
    if truncated or omitted:
        header = [f"[GPOS: {omitted} earlier sanitized line(s) omitted"
                  + ("; only the last 8 MiB of the log were read" if truncated else "") + "]"]
    return "\n".join(header + kept) + "\n", {"lines": len(kept), "omitted_lines": omitted, "read_truncated": truncated}


def tail(path, root, runtime_dir, count=c.LOG_TAIL_LINES):
    """The last `count` sanitized lines (for status data only), or []."""
    t = _tail_lines(path, 64 * 1024)
    if t is None:
        return []
    pairs = bases(root, runtime_dir)
    return [sanitize_line(l, pairs) for l in t[0][-count:]]
