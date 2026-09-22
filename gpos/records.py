"""Project record bundle: discovery and loading (read-only).

Bundle layout (documented in tools/validator/README.md):

    <project>/.game/gpos/
        project-config.json
        decisions/*.json     one Human Decision record per file
        routings/*.json      one task-routing record per file
        gates/*.json         one gate record per file
        evidence/*.json      one evidence record per file

Rules:
* discovery is deterministic (sorted by name); ids come from record contents, never
  from file names;
* hidden entries (names starting with ".") are ignored everywhere;
* any other unexpected file or directory is an error, never silently skipped;
* unreadable files, invalid JSON and non-object JSON are errors;
* nothing is ever written.
"""

import json
from pathlib import Path

from . import diagnostics as dg
from .errors import BundleNotFound

BUNDLE_DIR = Path(".game") / "gpos"
CONFIG_FILE = "project-config.json"
# directory -> (record type, schema name, id field)
RECORD_DIRS = {
    "decisions": ("decision", "decision", "decision_id"),
    "routings": ("routing", "task-routing", "task_id"),
    "gates": ("gate", "gate", "gate_id"),
    "evidence": ("evidence", "evidence", "evidence_id"),
}
RECORD_TYPES = ("project-config",) + tuple(v[0] for v in RECORD_DIRS.values())
SCHEMA_FOR_TYPE = {"project-config": "project-config", **{v[0]: v[1] for v in RECORD_DIRS.values()}}
ID_FIELD = {"project-config": None, **{v[0]: v[2] for v in RECORD_DIRS.values()}}


class Record:
    """One loaded record. `file` is relative to the bundle root (None for in-memory records)."""

    __slots__ = ("type", "data", "file")

    def __init__(self, type_, data, file=None):
        self.type, self.data, self.file = type_, data, file

    @property
    def id(self):
        field = ID_FIELD[self.type]
        if field is None:
            return self.data.get("project", {}).get("id") if isinstance(self.data.get("project"), dict) else None
        value = self.data.get(field)
        return value if isinstance(value, str) else None


class RecordSet:
    """All records of one project, plus any load-time diagnostics."""

    def __init__(self, config=None, decisions=(), routings=(), gates=(), evidence=(), root=None, load_diagnostics=(),
                 outside_routing_ids=()):
        self.config = config  # Record or None
        self.decisions = list(decisions)
        self.routings = list(routings)
        self.gates = list(gates)
        self.evidence = list(evidence)
        self.root = root
        self.load_diagnostics = list(load_diagnostics)
        # routing ids that exist in the project but outside this (scoped) record set
        self.outside_routing_ids = frozenset(outside_routing_ids)

    def all_records(self):
        head = [self.config] if self.config is not None else []
        return head + self.decisions + self.routings + self.gates + self.evidence

    @property
    def project_id(self):
        return self.config.id if self.config is not None else None

    def file_index(self):
        """(record type, record id) -> file of the first record with that id (files are sorted)."""
        index = {}
        for r in self.all_records():
            index.setdefault((r.type, r.id), r.file)
        return index


def from_records(config, decisions=(), evidence=(), gates=(), routings=()):
    """Build a RecordSet from in-memory JSON objects (library use and tests)."""
    return RecordSet(
        config=Record("project-config", config) if config is not None else None,
        decisions=[Record("decision", d) for d in decisions],
        routings=[Record("routing", r) for r in routings],
        gates=[Record("gate", g) for g in gates],
        evidence=[Record("evidence", e) for e in evidence],
    )


def resolve_bundle(path):
    """The bundle directory for `path`: <path>/.game/gpos, or <path> itself if it holds project-config.json."""
    p = Path(path)
    if not p.exists():
        raise BundleNotFound(f"{path}: no such file or directory")
    if not p.is_dir():
        raise BundleNotFound(f"{path}: not a directory")
    if (p / BUNDLE_DIR).is_dir():
        return p / BUNDLE_DIR
    if (p / CONFIG_FILE).exists() or any((p / d).is_dir() for d in RECORD_DIRS):
        return p
    raise BundleNotFound(f"{path}: no {BUNDLE_DIR.as_posix()}/ directory and no {CONFIG_FILE} (see tools/validator/README.md)")


def _read_record(path, rel, record_type, diags):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        diags.append(dg.make("RECORD_UNREADABLE", f"{rel}: cannot be read ({exc.strerror or exc})", record_type=record_type, file=rel))
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        diags.append(dg.make("RECORD_INVALID_JSON", f"{rel}: not UTF-8 text ({exc.reason})", record_type=record_type, file=rel))
        return None
    except ValueError as exc:
        diags.append(dg.make("RECORD_INVALID_JSON", f"{rel}: invalid JSON ({exc})", record_type=record_type, file=rel))
        return None
    if not isinstance(data, dict):
        diags.append(dg.make("RECORD_NOT_OBJECT", f"{rel}: must contain exactly one JSON object, found {type(data).__name__}",
                             record_type=record_type, file=rel))
        return None
    return Record(record_type, data, rel)


def _visible(entries):
    return sorted((e for e in entries if not e.name.startswith(".")), key=lambda e: e.name)


def load_project(path):
    """Load a project's record bundle. Raises BundleNotFound; every data problem is a diagnostic."""
    root = resolve_bundle(path)
    diags, groups, config = [], {d: [] for d in RECORD_DIRS}, None
    try:
        entries = _visible(root.iterdir())
    except OSError as exc:
        raise BundleNotFound(f"{root}: cannot list bundle ({exc.strerror or exc})") from exc
    for entry in entries:
        name = entry.name
        if name == CONFIG_FILE and entry.is_file():
            config = _read_record(entry, name, "project-config", diags)
        elif name in RECORD_DIRS and entry.is_dir():
            record_type = RECORD_DIRS[name][0]
            try:
                children = _visible(entry.iterdir())
            except OSError as exc:
                diags.append(dg.make("RECORD_UNREADABLE", f"{name}/: cannot be listed ({exc.strerror or exc})", record_type=record_type, file=name + "/"))
                continue
            for child in children:
                rel = f"{name}/{child.name}"
                if child.is_file() and child.suffix == ".json":
                    rec = _read_record(child, rel, record_type, diags)
                    if rec is not None:
                        groups[name].append(rec)
                else:
                    diags.append(dg.make("UNKNOWN_RECORD_FILE", f"{rel}: only *.json record files belong in {name}/",
                                         record_type=record_type, file=rel))
        else:
            diags.append(dg.make("UNKNOWN_RECORD_FILE",
                                 f"{name}: not part of the bundle layout ({CONFIG_FILE}, {', '.join(d + '/' for d in RECORD_DIRS)})",
                                 file=name))
    if config is None and not any(d.code in ("RECORD_UNREADABLE", "RECORD_INVALID_JSON", "RECORD_NOT_OBJECT")
                                  and d.record_type == "project-config" for d in diags):
        diags.append(dg.make("PROJECT_CONFIG_MISSING", f"{CONFIG_FILE} is missing from the bundle", record_type="project-config",
                             file=CONFIG_FILE))
    return RecordSet(config=config, decisions=groups["decisions"], routings=groups["routings"],
                     gates=groups["gates"], evidence=groups["evidence"], root=root, load_diagnostics=diags)
