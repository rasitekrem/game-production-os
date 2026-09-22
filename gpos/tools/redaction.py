"""Conservative credential redaction for captured tool output and diagnostics.

This is deliberately *not* a universal secret scanner: it removes the common shapes in which a
credential reaches a log — an assignment to a credential-named key, an HTTP authorization or
cookie header, a PEM private key block, and a few provider token formats that are unmistakable on
their own. Only the value is replaced; the key, the surrounding line and ordinary game logs are
left alone, so a `FRAME_TIME=16.7` or `player_token_count=3` style line is never touched.

Everything a ToolResult carries — captured stdout/stderr, diagnostic messages, process spec
arguments — passes through `redact` before it is stored. Nothing else in the foundation copies
raw environment values anywhere.
"""

import re

PLACEHOLDER = "[REDACTED]"

_KEY = r"(?:api[_-]?key|apikey|secret|secret[_-]?key|client[_-]?secret|access[_-]?key|access[_-]?token|" \
       r"auth[_-]?token|refresh[_-]?token|id[_-]?token|session[_-]?token|token|password|passwd|pwd|passphrase|" \
       r"private[_-]?key|credential|credentials)"

# (pattern, replacement template). Only the value group is replaced; keys and structure survive.
PATTERNS = (
    # KEY=value / KEY: value / "KEY": "value"
    (re.compile(rf'(?i)\b({_KEY})(["\']?\s*[:=]\s*["\']?)(?!\s*$)([^\s"\',;)]+)'), r"\1\2" + PLACEHOLDER),
    # --password value / -token=value on a command line
    (re.compile(rf"(?i)(--?{_KEY}[ =])(\S+)"), r"\1" + PLACEHOLDER),
    # Authorization: Bearer ... / Basic ... / Proxy-Authorization
    (re.compile(r"(?i)\b((?:proxy-)?authorization\s*[:=]\s*)(\S+(?:\s+\S+)?)"), r"\1" + PLACEHOLDER),
    # Cookie / Set-Cookie header lines
    (re.compile(r"(?i)^((?:set-)?cookie\s*:\s*)(.+)$", re.M), r"\1" + PLACEHOLDER),
    # Unmistakable provider token shapes, even without a key name
    (re.compile(r"\b(gh[pousr]_)([A-Za-z0-9]{16,})"), r"\1" + PLACEHOLDER),
    (re.compile(r"\b(xox[abposr]-)([A-Za-z0-9-]{10,})"), r"\1" + PLACEHOLDER),
    (re.compile(r"\b(AKIA)([0-9A-Z]{16})\b"), r"\1" + PLACEHOLDER),
    # PEM private key blocks, including the body
    (re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)", re.S),
     r"\1" + PLACEHOLDER + r"\3"),
)


def redact(text):
    """(redacted text, number of redactions). Non-str input is returned unchanged with 0."""
    if not isinstance(text, str) or not text:
        return text, 0
    count = 0
    for pattern, replacement in PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count


def redact_bytes(data, encoding="utf-8"):
    """Redact bytes captured from a process, replacing undecodable sequences deterministically."""
    text, n = redact(data.decode(encoding, errors="replace"))
    return text, n


def redact_all(values):
    """Redact every string in a sequence; returns (list, total redactions)."""
    out, total = [], 0
    for v in values:
        text, n = redact(v)
        out.append(text)
        total += n
    return out, total
