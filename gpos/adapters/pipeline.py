"""render / sync / check.

prepare  the project must be a GPOS project root that the frozen Phase-2A validator reports VALID
         (readiness of individual tasks is irrelevant); an unsupported GPOS version fails closed.
render   compile + render + validate in memory; optionally write to an explicit, empty output dir.
sync     plan every change for every requested agent first; if anything would overwrite or delete a
         file GPOS does not own, write nothing (CONFLICT). Otherwise apply: each file is written to a
         temporary sibling and atomically replaced, stale owned files are removed, the manifest goes
         last. A file already equal to its new content is accepted, so an interrupted sync is safe
         to re-run. Never touches anything outside the backend's managed area.
check    read-only drift detection against the manifest, the sources and a fresh render.
"""

import os
import tempfile
from pathlib import Path

from ..errors import BundleNotFound, UnsupportedGposVersion
from ..framework import load_framework
from ..records import load_project
from ..validation.project import validate_project
from . import diagnostics as dg
from .backends import BACKENDS
from .compiler import compile_ir, project_root_of
from .errors import AdapterError
from .layers import (claude_settings_problems, codex_config_problems, owned_entrypoints, skill_id_collisions,
                     unmanaged_instruction_layers)
from .manifest import parse_manifest
from .paths import is_managed, is_safe_relative, managed_files_on_disk, unsafe_on_disk
from .render import render_bundle
from .sources import sha256_bytes
from .validation import validate_bundle


class Result:
    def __init__(self, command, project_id, diagnostics, agents=None, data=None):
        self.command, self.project_id = command, project_id
        self.diagnostics = dg.sort(diagnostics)
        self.agents = agents or {}
        self.data = data
        self.status = dg.result_class(self.diagnostics)

    @property
    def exit_code(self):
        return dg.EXIT_FOR[self.status]


def select_agents(requested, config=None):
    """Adapters to act on. `all` means every supported adapter for render, and the project's enabled ones otherwise."""
    if requested in (None, "all"):
        if config is None:
            return list(BACKENDS)
        enabled = [a for a in BACKENDS if a in config.get("enabled_adapters", [])]
        if not enabled:
            raise AdapterError("ADAPTER_NOT_ENABLED", f"project config enables none of the supported adapters {sorted(BACKENDS)}")
        return enabled
    if requested not in BACKENDS:
        raise AdapterError("ADAPTER_UNSUPPORTED", f"unknown adapter {requested!r}; supported: {sorted(BACKENDS)}")
    return [requested]


def prepare(project, framework=None):
    """(project root, IR). Raises AdapterError with a diagnostic when generation must not proceed."""
    fw = framework or load_framework()
    root = project_root_of(project)
    try:
        result = validate_project(load_project(root), fw)
    except UnsupportedGposVersion as exc:
        raise AdapterError("GPOS_VERSION_INCOMPATIBLE", str(exc)) from exc
    except BundleNotFound as exc:
        raise AdapterError("PROJECT_LAYOUT", str(exc)) from exc
    if not result.valid:
        codes = sorted({d.code for d in result.diagnostics if d.severity == "ERROR"})
        raise AdapterError("PROJECT_INVALID", f"the production validator reports the project INVALID ({', '.join(codes)}); "
                                              f"fix the records first (python3 -m gpos.validator validate --project .)")
    return root, compile_ir(root, fw)


def _fail(command, exc, project_id=None, agent=None):
    return Result(command, project_id, [dg.make(exc.code, str(exc), agent)] + list(exc.diagnostics))


def _bundles(ir, agents):
    bundles, diags = {}, []
    for a in agents:
        b = render_bundle(ir, BACKENDS[a])
        bundles[a] = b
        diags += validate_bundle(b)
    return bundles, diags


def _config(root):
    import json
    return json.loads((Path(root) / ".game" / "gpos" / "project-config.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- render

def render(project, agent="all", out=None, framework=None):
    try:
        root, ir = prepare(project, framework)
        agents = select_agents(agent if agent != "all" else None)
        bundles, diags = _bundles(ir, agents)
    except AdapterError as exc:
        return _fail("render", exc)
    info = {a: {"files": [{"path": f.path, "sha256": f.sha256, "bytes": len(f.data)} for f in b.files],
                "semantic_hash": b.manifest.data["semantic_hash"]} for a, b in bundles.items()}
    if out is not None and dg.result_class(diags) == dg.OK:
        target = Path(out).resolve()
        inside = target == root or root in target.parents
        reserved = inside and (target == root or target.relative_to(root).parts[0] in (".game", ".claude", ".agents"))
        if reserved or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
            return Result("render", ir.project["id"], diags + [dg.make(
                "OUTPUT_PATH_INVALID", f"{out}: render output must be a new or empty directory, not the project root "
                                       f"or a GPOS/agent area (.game, .claude, .agents); use sync to update a project")], info)
        for a, b in bundles.items():
            for f in b.files:
                dest = target / a / f.path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.data)
    return Result("render", ir.project["id"], diags, info)


# ---------------------------------------------------------------- sync

def _read(root, path):
    p = Path(root) / path
    return p.read_bytes() if p.is_file() else None


def _old_manifest(root, backend):
    raw = _read(root, backend.manifest_path)
    if raw is None:
        return None
    try:
        return parse_manifest(raw)
    except ValueError as exc:
        raise AdapterError("MANIFEST_UNTRUSTED", f"{backend.manifest_path}: {exc}; sync will not guess ownership") from exc


def _manifest_or_none(root, backend):
    try:
        return _old_manifest(root, backend)
    except AdapterError:
        return None


def _layer_conflicts(root, bundle, owned):
    """Runtime-discovery conflicts for one backend: project instruction configuration, unmanaged instruction
    layers (including configured fallback names) and generated-skill id collisions. Read-only."""
    backend, ir = bundle.backend, bundle.ir
    out, extra_names = [], ()
    if backend.id == "claude-code":
        problems = claude_settings_problems(root)
    else:
        root_bytes = len(bundle.by_path()[backend.entrypoint].data)
        problems, extra_names = codex_config_problems(root, root_bytes, [s.agent_id for s in ir.skills])
    out += [dg.make(code, message, backend.id, path) for code, path, message in problems]
    out += [dg.make("INSTRUCTION_LAYER_CONFLICT", f"{path} is a {backend.agent_name} instruction file GPOS does not own; the "
                    f"runtime can let it add to or override the generated instructions. Move its content into project "
                    f"authority (.game/) or remove it; GPOS never edits it", backend.id, path)
            for path in unmanaged_instruction_layers(root, backend, owned, extra_names)]
    out += [dg.make("SKILL_ID_CONFLICT", f"{path} is a project skill with the generated GPOS skill id {sid}; the runtime could "
                    f"offer both. Rename or move it; GPOS never edits it", backend.id, path)
            for path, sid in skill_id_collisions(root, backend, [s.agent_id for s in ir.skills])]
    return out


def plan_sync(root, bundle, repair=False, owned_entry_files=()):
    """(writes {path: bytes}, deletes [path], diagnostics). Nothing is touched."""
    backend = bundle.backend
    diags, writes, deletes = [], {}, []
    diags += _layer_conflicts(root, bundle, set(owned_entry_files) | {backend.entrypoint})
    old = _old_manifest(root, backend)
    old_files = {}
    if old is not None:
        if old.get("adapter", {}).get("id") != backend.id:
            diags.append(dg.make("OUTPUT_CONFLICT", f"{backend.manifest_path} belongs to another adapter", backend.id,
                                 backend.manifest_path))
        for e in old["files"]:
            if not is_managed(backend, e["path"]):
                diags.append(dg.make("UNSAFE_PATH", f"manifest lists {e['path']!r}, outside the managed area; refusing to use it",
                                     backend.id, e["path"]))
            else:
                old_files[e["path"]] = e["sha256"]
    new = bundle.by_path()
    for path, f in sorted(new.items()):
        unsafe = unsafe_on_disk(root, path)
        if unsafe:
            diags.append(dg.make("UNSAFE_PATH", unsafe, backend.id, path))
            continue
        current = _read(root, path)
        if current is None and (Path(root) / path).exists():
            diags.append(dg.make("OUTPUT_CONFLICT", f"{path} exists and is not a regular file", backend.id, path))
            continue
        if current is None:
            writes[path] = f.data
        elif current == f.data:
            continue
        elif path == backend.manifest_path and old is not None:
            writes[path] = f.data
        elif path in old_files and sha256_bytes(current) == old_files[path]:
            writes[path] = f.data
        elif path in old_files:
            if repair:
                writes[path] = f.data
            else:
                diags.append(dg.make("MODIFIED_MANAGED_FILE_CONFLICT", f"{path} was edited after generation; "
                                     f"move the edits to their source, or re-run with --repair to discard them", backend.id, path))
        elif path == backend.entrypoint:
            diags.append(dg.make("UNOWNED_ENTRYPOINT", f"{path} exists and was not generated by GPOS; it is left untouched. "
                                 f"Move or merge it by hand before syncing (no automatic adoption)", backend.id, path))
        else:
            diags.append(dg.make("OUTPUT_CONFLICT", f"{path} exists and is not owned by GPOS", backend.id, path))
    for path, digest in sorted(old_files.items()):
        if path in new:
            continue
        current = _read(root, path)
        if current is None:
            continue
        if unsafe_on_disk(root, path):
            diags.append(dg.make("UNSAFE_PATH", unsafe_on_disk(root, path), backend.id, path))
        elif sha256_bytes(current) == digest or repair:
            deletes.append(path)
        else:
            diags.append(dg.make("MODIFIED_MANAGED_FILE_CONFLICT", f"stale generated file {path} was edited; not deleted",
                                 backend.id, path))
    for path in sorted(managed_files_on_disk(root, backend) - set(new) - set(old_files)):
        diags.append(dg.make("OUTPUT_CONFLICT", f"{path} is inside the GPOS-managed area but not owned by GPOS; move it out",
                             backend.id, path))
    return writes, deletes, diags


def _atomic_write(root, path, data):
    dest = Path(root) / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gpos-tmp-", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def sync(project, agent="all", repair=False, framework=None):
    try:
        root, ir = prepare(project, framework)
        config = _config(root)
        agents = select_agents(agent, config)
        for a in agents:
            if a not in config.get("enabled_adapters", []):
                raise AdapterError("ADAPTER_NOT_ENABLED", f"{a} is not in project config enabled_adapters; enabling an "
                                                          f"adapter is a project decision")
        bundles, diags = _bundles(ir, agents)
    except AdapterError as exc:
        return _fail("sync", exc)
    if dg.result_class(diags) != dg.OK:
        return Result("sync", ir.project["id"], diags)
    owned = {BACKENDS[a].entrypoint for a in agents} | owned_entrypoints(root, BACKENDS.values(), _manifest_or_none)
    plans = {}
    for a, b in bundles.items():
        try:
            plans[a] = plan_sync(root, b, repair, owned)
        except AdapterError as exc:
            diags.append(dg.make(exc.code, str(exc), a))
            continue
        diags += plans[a][2]
    if dg.result_class(diags) != dg.OK:
        return Result("sync", ir.project["id"], diags)  # nothing written
    for a, (writes, deletes, _) in plans.items():
        backend = BACKENDS[a]
        for path in sorted(p for p in writes if p != backend.manifest_path):
            _atomic_write(root, path, writes[path])
            diags.append(dg.make("FILE_WRITTEN", f"wrote {path}", a, path))
        for path in deletes:
            (Path(root) / path).unlink()
            diags.append(dg.make("FILE_REMOVED", f"removed stale {path}", a, path))
            _prune(root, backend, Path(path).parent)
        if backend.manifest_path in writes:
            _atomic_write(root, backend.manifest_path, writes[backend.manifest_path])
            diags.append(dg.make("FILE_WRITTEN", f"wrote {backend.manifest_path}", a, backend.manifest_path))
    return Result("sync", ir.project["id"], diags,
                  {a: {"semantic_hash": b.manifest.data["semantic_hash"], "files": len(b.files)} for a, b in bundles.items()})


def _prune(root, backend, directory):
    """Remove directories left empty inside a generated skill directory (never the skill root itself)."""
    rel = directory.as_posix()
    while rel.startswith(f"{backend.skill_root}/gpos-") and is_safe_relative(rel):
        d = Path(root) / rel
        if not d.is_dir() or os.path.islink(d) or any(d.iterdir()):
            return
        d.rmdir()
        rel = Path(rel).parent.as_posix()


# ---------------------------------------------------------------- authority (read-only helper)

def authority(project, framework=None):
    """The machine-readable project authority as the compiler reads it, with the exact structured bindings a LOCK
    Human Decision must carry (`value.locks`) to lock a row or a whole document. Read-only; checks no lock."""
    from . import sources as src
    fw = framework or load_framework()
    reg = fw.registry
    try:
        root = project_root_of(project)
    except AdapterError as exc:
        return _fail("authority", exc)
    docs, diags = [], []
    for f in reg["project_authority_files"]:
        path = root / ".game" / f
        if not path.is_file():
            continue
        rel = f".game/{f}"
        try:
            parsed = src.parse_authority_document(path.read_text(encoding="utf-8"), reg["placeholders"],
                                                  tuple(reg["authority_document_statuses"]))
        except src.AuthorityDocumentError as exc:
            diags.append(dg.make("AUTHORITY_DOCUMENT_INVALID", f"{rel}: {exc}", path=rel))
            continue
        docs.append({"authority_path": rel, "status": parsed["status"], "locked_by": parsed["locked_by"],
                     "document_binding": {"authority_path": rel,
                                          "document_sha256": src.document_sha256(rel, parsed["rows"])},
                     "rows": [{"status": r["status"], "decision_ref": r["decision_ref"], "placeholders": r["placeholders"],
                               "row_binding": {"authority_path": rel, "section": r["section"], "item": r["item"],
                                               "value": r["value"]}} for r in parsed["rows"]]})
    return Result("authority", None, diags, data={"lock_binding": {k: v for k, v in reg["project_lock_binding"].items()
                                                                   if not k.startswith("$")}, "documents": docs})


# ---------------------------------------------------------------- check

def check(project, agent="all", framework=None):
    try:
        root, ir = prepare(project, framework)
        config = _config(root)
        agents = select_agents(agent, config)
        for a in agents:
            if a not in config.get("enabled_adapters", []):
                raise AdapterError("ADAPTER_NOT_ENABLED", f"{a} is not in project config enabled_adapters")
        bundles, render_diags = _bundles(ir, agents)
    except AdapterError as exc:
        return _fail("check", exc)
    diags = list(render_diags)
    for a, bundle in bundles.items():
        diags += check_agent(root, ir, bundle)
    return Result("check", ir.project["id"], diags)


def check_agent(root, ir, bundle):
    backend = bundle.backend
    a = backend.id

    def d(code, message, path=None, **details):
        return dg.make(code, message, a, path, details)

    raw = _read(root, backend.manifest_path)
    if raw is None:
        return [d("MANIFEST_MISSING", f"{backend.manifest_path} is missing; run sync", backend.manifest_path)]
    try:
        m = parse_manifest(raw)
    except ValueError as exc:
        return [d("MANIFEST_INVALID", f"{backend.manifest_path}: {exc}", backend.manifest_path)]
    out = _layer_conflicts(root, bundle, owned_entrypoints(root, BACKENDS.values(), _manifest_or_none))
    if m["adapter"].get("id") != a or m["adapter"].get("format") != backend.format_id:
        out.append(d("ADAPTER_FORMAT_MISMATCH", f"manifest adapter {m['adapter']} is not {a} {backend.format_id}",
                     backend.manifest_path))
    if m["gpos_version"] != ir.gpos_version:
        out.append(d("GPOS_VERSION_MISMATCH", f"generated from GPOS {m['gpos_version']}, current {ir.gpos_version}",
                     backend.manifest_path))
    if m["project_id"] != ir.project["id"]:
        out.append(d("MANIFEST_INVALID", f"manifest is for project {m['project_id']!r}", backend.manifest_path))
    listed = {}
    for e in m["files"]:
        if not is_managed(backend, e["path"]):
            out.append(d("PATH_ESCAPE", f"manifest lists {e['path']!r}, outside the managed area", e["path"]))
            continue
        listed[e["path"]] = e["sha256"]
        unsafe = unsafe_on_disk(root, e["path"])
        current = None if unsafe else _read(root, e["path"])
        if unsafe:
            out.append(d("PATH_ESCAPE", unsafe, e["path"]))
        elif current is None:
            out.append(d("MANAGED_FILE_MISSING", f"{e['path']} is missing", e["path"]))
        elif sha256_bytes(current) != e["sha256"]:
            out.append(d("MANAGED_FILE_MODIFIED", f"{e['path']} was modified after generation", e["path"]))
    for path in sorted(managed_files_on_disk(root, backend) - set(listed) - {backend.manifest_path}):
        out.append(d("UNEXPECTED_MANAGED_FILE", f"{path} is inside the managed area but not in the manifest", path))
    old_sources = {s["id"]: s["sha256"] for s in m["sources"] if isinstance(s, dict)}
    new_sources = {s["id"]: s["sha256"] for s in ir.sources}
    changed = sorted(i for i in set(old_sources) | set(new_sources) if old_sources.get(i) != new_sources.get(i))
    for i in changed:
        state = "added" if i not in old_sources else "removed" if i not in new_sources else "changed"
        out.append(d("SOURCE_CHANGED", f"source {i} {state} since generation; run sync", i, state=state))
    expected = {f.path: f.sha256 for f in bundle.files if f.path != backend.manifest_path}
    if not changed and expected != listed:
        out.append(d("GENERATED_STALE", "regenerating from the current sources would produce different files; run sync",
                     backend.manifest_path))
    return out
