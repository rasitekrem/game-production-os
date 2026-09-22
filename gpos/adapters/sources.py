"""Explicit source selection for the adapter compiler.

Only these sources are read, each with a kind and a sha256:

    NORMATIVE          core/registry.json (canonical vocabulary, gates, workflows, triggers)
    SPECIALIST_SKILL   skills/<name>/SKILL.md (the 13 frozen specialist contracts)
    WORKFLOW           workflows/<name>.md
    PROJECT_AUTHORITY  <project>/.game/<file>.md for registry project_authority_files,
                       <project>/.game/gpos/project-config.json and decision records

GENERATED_METADATA (manifests) is produced, never consumed as authority. Nothing else in the
GPOS repository or the project is compiled: no directory is concatenated wholesale.

Contract files are parsed by their frozen structure (front matter plus the `## SECTION`
headings the Phase-1 suite enforces); project authority files by the template table format
(`| Item | Value | Status · decision ref |`). Parsing is structural, never interpretive.
"""

import hashlib
import json
import re
from pathlib import Path

NORMATIVE, PROJECT_AUTHORITY, SPECIALIST_SKILL, WORKFLOW, GENERATED_METADATA = (
    "NORMATIVE", "PROJECT_AUTHORITY", "SPECIALIST_SKILL", "WORKFLOW", "GENERATED_METADATA")
SOURCE_KINDS = (NORMATIVE, PROJECT_AUTHORITY, SPECIALIST_SKILL, WORKFLOW, GENERATED_METADATA)

LOCKED, PROPOSED = "LOCKED", "PROPOSED"
DECISION_REF = re.compile(r"\bD-[A-Za-z0-9._-]+\b")
_H2 = re.compile(r"^## (.+?)\s*$", re.M)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


class Source:
    """One consumed source. `id` is logical and machine-independent: gpos:<path> or project:<path>."""

    __slots__ = ("kind", "id", "path", "text", "sha256")

    def __init__(self, kind, id_, path, raw):
        self.kind, self.id, self.path = kind, id_, path
        self.sha256 = sha256_bytes(raw)
        self.text = raw.decode("utf-8")

    def ref(self):
        return {"kind": self.kind, "id": self.id, "sha256": self.sha256}


def read_source(kind, id_, path):
    return Source(kind, id_, path, Path(path).read_bytes())


# ---------------------------------------------------------------- contract documents

def front_matter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def h2_sections(text):
    """Ordered {HEADING: body} for `## HEADING` sections (body stripped)."""
    parts = _H2.split(text)
    return {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def title(text):
    m = re.search(r"^# (.+?)\s*$", text, re.M)
    return m.group(1).strip() if m else ""


def delink(text, origin):
    """Relative GPOS links do not resolve inside a project: keep the label, cite the GPOS path."""
    def repl(m):
        label, target = m.group(1), m.group(2)
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            return label if target.startswith("#") else m.group(0)
        resolved = (Path(origin).parent / target.split("#")[0]).as_posix()
        parts = []
        for p in resolved.split("/"):
            if p == "..":
                if parts:
                    parts.pop()
            elif p not in ("", "."):
                parts.append(p)
        return f"{label} (GPOS `{'/'.join(parts)}`)"
    return _LINK.sub(repl, text)


# ---------------------------------------------------------------- project authority documents

def parse_authority_document(text, placeholders):
    """Structured view of a `.game/*.md` authority file written from the GPOS templates."""
    status = re.search(r"Document authority status:\s*`([A-Z_]+)`", text)
    locked_by = re.search(r"Locked by decision:\s*`?([A-Za-z0-9._-]+)`?", text)
    rows, section = [], None
    for line in text.splitlines():
        h = re.match(r"^## (.+?)\s*$", line)
        if h:
            section = h.group(1).strip()
            continue
        if not line.startswith("|") or re.match(r"^\|\s*-", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] == "Item":
            continue
        item, value, status_cell = cells[0], cells[1], cells[-1]
        row_status = LOCKED if "`LOCKED`" in status_cell else PROPOSED if "`PROPOSED`" in status_cell else None
        if row_status is None:
            continue
        tokens = set(re.findall(r"`([A-Z_]+)`", value))
        rows.append({
            "section": section, "item": item, "value": value.strip(),
            "status": row_status, "decision_ref": (DECISION_REF.findall(status_cell) or [None])[0],
            "placeholders": sorted(tokens & set(placeholders)),
        })
    return {"status": status.group(1) if status else None,
            "locked_by": locked_by.group(1) if locked_by else None, "rows": rows}


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
