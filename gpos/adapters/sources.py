"""Explicit source selection for the adapter compiler.

Only these sources are read, each with a kind and a sha256:

    NORMATIVE          core/registry.json (canonical vocabulary, gates, workflows, triggers, the
                       agent_operating_contract) and every frozen document a contract rule cites
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

AUTHORITY_HEADER = ["Item", "Value", "Status · decision ref"]
_STATUS_CELL = re.compile(r"^`(PROPOSED)`$|^`(LOCKED)` · (D-[A-Za-z0-9._-]+)$")
_DOC_STATUS = re.compile(r"Document authority status:\s*`([A-Z_]+)`")
_LOCKED_BY = re.compile(r"Locked by decision:\s*`?([A-Za-z0-9._-]+)`?")


class AuthorityDocumentError(ValueError):
    """The machine-readable authority portion of a `.game/*.md` file is malformed (fail closed)."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_authority_document(text, placeholders, statuses=("PROPOSED", "LOCKED")):
    """Strict structural parse of a `.game/*.md` authority file written from the GPOS templates.

    Machine-readable parts: the `Document authority status:` / `Locked by decision:` lines and every
    table whose header is exactly `| Item | Value | Status · decision ref |`. Inside those, nothing is
    skipped: a row that does not parse, a duplicate row, malformed status/reference syntax, a near-miss
    authority header, or an authority-looking row outside an authority table raises
    AuthorityDocumentError. Other tables and prose are not compiled (and not interpreted).
    """
    problems, rows, seen = [], [], set()
    lines = text.splitlines()
    status_lines = [m for m in (_DOC_STATUS.search(l) for l in lines if "Document authority status:" in l)]
    locked_lines = [l for l in lines if "Locked by decision:" in l]
    section, in_table, has_tables = None, False, False
    for n, line in enumerate(lines, 1):
        h = re.match(r"^## (.+?)\s*$", line)
        if h:
            section, in_table = h.group(1).strip(), False
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        is_header = n < len(lines) and re.match(r"^\|\s*:?-", lines[n])
        if re.match(r"^\|\s*:?-", line):
            continue
        cells = _cells(line)
        if is_header:
            in_table = cells == AUTHORITY_HEADER
            has_tables |= in_table
            if in_table and section is None:
                problems.append(f"line {n}: authority table before any `## ` section; every authority row needs a section")
                in_table = False
            if not in_table and "Value" in cells and any(c.startswith("Status") for c in cells):
                problems.append(f"line {n}: table header {cells} looks like an authority table but is not {AUTHORITY_HEADER}")
            continue
        if not in_table:
            if len(cells) == 3 and re.search(r"`(LOCKED|PROPOSED)`", cells[2]):
                problems.append(f"line {n}: authority-style row outside an authority table")
            continue
        if len(cells) != 3 or not cells[0] or not cells[1]:
            problems.append(f"line {n}: authority row must have exactly three non-empty cells (Item | Value | Status)")
            continue
        m = _STATUS_CELL.match(cells[2])
        if not m:
            problems.append(f"line {n}: status {cells[2]!r} must be `PROPOSED` or `LOCKED` · D-<id>")
            continue
        key = (section, cells[0])
        if key in seen:
            problems.append(f"line {n}: duplicate authority row {section} › {cells[0]}")
            continue
        seen.add(key)
        tokens = set(re.findall(r"`([A-Z_]+)`", cells[1]))
        rows.append({"section": section, "item": cells[0], "value": cells[1],
                     "status": "PROPOSED" if m.group(1) else "LOCKED", "decision_ref": m.group(3),
                     "placeholders": sorted(tokens & set(placeholders))})
    status = locked_by = None
    if len(status_lines) > 1 or len(locked_lines) > 1:
        problems.append("authority metadata (Document authority status / Locked by decision) appears more than once")
    elif status_lines or locked_lines or has_tables:
        if len(status_lines) != 1 or status_lines[0] is None or len(locked_lines) != 1:
            problems.append("authority document needs exactly one `Document authority status:` and one `Locked by decision:` line")
        else:
            status = status_lines[0].group(1)
            lm = _LOCKED_BY.search(locked_lines[0])
            locked_by = lm.group(1) if lm else None
            if status not in statuses:
                problems.append(f"document status {status!r} is not one of {list(statuses)}")
            if status == "LOCKED" and not (locked_by and DECISION_REF.fullmatch(locked_by)):
                problems.append("a LOCKED document must name its locking decision (Locked by decision: D-<id>)")
            if status != "LOCKED" and locked_by not in placeholders:
                problems.append(f"a {status} document must not claim a locking decision ({locked_by!r}); use a placeholder")
    if problems:
        raise AuthorityDocumentError(problems)
    return {"status": status, "locked_by": locked_by if status == "LOCKED" else None, "rows": rows}


def document_sha256(authority_path, rows):
    """Canonical hash a document-level LOCK binds to: the machine-readable rows, in order."""
    payload = {"authority_path": authority_path,
               "rows": [[r["section"], r["item"], r["value"], r["status"], r["decision_ref"]] for r in rows]}
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
