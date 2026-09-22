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


# ---------------------------------------------------------------- adapter-supplied metadata

# A key or command-line flag that names a credential. In a JSON structure or an argument vector the
# name and the value are separate elements, so the string patterns above never see them together:
# these two rules cover `{"token": "..."}` and `["--password", "..."]`.
CREDENTIAL_NAME = re.compile(rf"(?i)^-{{0,2}}{_KEY}$")

MAX_DEPTH = 12
# What a ToolResult may carry from an adapter: JSON-shaped data only. Anything else (a file object, a
# byte buffer, a live handle) is refused rather than stringified into the result.
JSON_SCALARS = (str, int, float, bool, type(None))


class UnsupportedValue(Exception):
    """An adapter offered a value a ToolResult cannot carry."""


def sanitize(value, _depth=0):
    """Recursively redact every string inside a JSON-shaped value. Returns (value, redactions).

    This is the foundation's boundary for *adapter-supplied* metadata — diagnostics, outcome data,
    recorded commands and environment names, artifact descriptions, evidence summaries and
    limitations, probe text. Captured process output is already redacted by the process boundary;
    this makes the same promise true for everything else an adapter can put in front of a caller,
    including an adapter that never called the boundary helpers.

    Raises UnsupportedValue for a shape a result cannot carry, so nothing is silently stringified.
    """
    if _depth > MAX_DEPTH:
        raise UnsupportedValue(f"value nests deeper than {MAX_DEPTH} levels")
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value, 0
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity are not JSON
            raise UnsupportedValue(f"{value!r} is not representable in a result")
        return value, 0
    if isinstance(value, dict):
        out, total = {}, 0
        for key, item in value.items():
            if not isinstance(key, str):
                raise UnsupportedValue(f"mapping key {key!r} is not a string")
            clean_key, key_n = redact(key)
            if CREDENTIAL_NAME.match(key.strip()) and isinstance(item, str):
                clean_item, item_n = PLACEHOLDER, 1  # the key names a credential; the value is one
            else:
                clean_item, item_n = sanitize(item, _depth + 1)
            out[clean_key] = clean_item
            total += key_n + item_n
        return out, total
    if isinstance(value, (list, tuple)):
        out, total, flagged = [], 0, False
        for item in value:
            if flagged and isinstance(item, str):
                clean, n = PLACEHOLDER, 1  # the previous element named a credential (e.g. --password)
            else:
                clean, n = sanitize(item, _depth + 1)
            flagged = isinstance(item, str) and bool(CREDENTIAL_NAME.match(item.strip()))
            out.append(clean)
            total += n
        return out, total
    raise UnsupportedValue(f"{type(value).__name__} is not a value a ToolResult can carry")


def sanitize_or_none(value):
    """(sanitized value or None, redactions, problem or None) — never raises."""
    try:
        clean, n = sanitize(value)
        return clean, n, None
    except UnsupportedValue as exc:
        return None, 0, str(exc)


def sanitize_diagnostic(diagnostic):
    """A diagnostic with its message, path and details redacted. (diagnostic, redactions)."""
    message, m = redact(diagnostic.message)
    path, p = redact(diagnostic.path) if diagnostic.path else (diagnostic.path, 0)
    details, d = redact(diagnostic.details)
    if (message, path, details) == (diagnostic.message, diagnostic.path, diagnostic.details):
        return diagnostic, 0
    return type(diagnostic)(diagnostic.code, diagnostic.cls, message, diagnostic.adapter,
                            diagnostic.capability, path, details), m + p + d


def sanitize_all(diagnostics):
    """(list of redacted diagnostics, total redactions)."""
    out, total = [], 0
    for d in diagnostics:
        clean, n = sanitize_diagnostic(d)
        out.append(clean)
        total += n
    return out, total
