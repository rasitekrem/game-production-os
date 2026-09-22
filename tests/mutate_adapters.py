#!/usr/bin/env python3
"""Bounded mutation harness for the agent adapter layer (gpos/adapters/).

    python3 tests/mutate_adapters.py [--jobs N] [--only TEXT]

Each mutation disables or weakens exactly one high-risk adapter rule in a temporary copy of the
repository and runs tests/test_adapters.py there. A mutation must make the suite fail
("CAUGHT"); one that leaves it green is "MISSED" and fails this harness. An anchor that no
longer exists is reported "NOT APPLIED" and also fails the harness, so the list cannot rot.
The repository itself is never modified.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (name, file, anchor, replacement)
MUTATIONS = [
    ('Human Decision dropped from the authority order', 'gpos/adapters/compiler.py', 'for lvl in reg["authority_levels"]),', 'for lvl in reg["authority_levels"][1:]),'),
    ('skill ownership altered', 'gpos/adapters/compiler.py', 'may_own_gates=gates,', "may_own_gates=gates[1:] + gates[:1] + ('AUDIO',) if gates else gates,"),
    ('DRAFT promoted to PILOTED', 'gpos/adapters/compiler.py', 'maturity=fm["maturity"],', 'maturity="PILOTED",'),
    ('root bypasses the validator', 'gpos/adapters/content.py', "exited: `{v['readiness']}` (exit 0", 'exited: use your judgement (exit 0'),
    ('game-director allowed as reviewer', 'gpos/adapters/compiler.py', 'never_cross_reviewer=name in reg["never_cross_reviewer"]', 'never_cross_reviewer=False'),
    ('project authority omitted from skills', 'gpos/adapters/compiler.py', 'authority_files=tuple(f for f in authority_files if f in _named_inputs(sections)),', 'authority_files=(),'),
    ('locked project rows dropped', 'gpos/adapters/content.py', '    return [r for r in doc.rows if r.status == "LOCKED"]', '    return []'),
    ('placeholders not preserved', 'gpos/adapters/sources.py', '"placeholders": sorted(tokens & set(placeholders))})', '"placeholders": []})'),
    ('unbacked lock shown as verified', 'gpos/adapters/compiler.py', '        _verify_locks(reg, config, decisions, f".game/{f}", parsed, doc_hash)', '        pass'),
    ('source hash ignored', 'gpos/adapters/pipeline.py', 'changed = sorted(i for i in set(old_sources) | set(new_sources) if old_sources.get(i) != new_sources.get(i))', 'changed = []'),
    ('managed file drift ignored', 'gpos/adapters/pipeline.py', '        elif sha256_bytes(current) != e["sha256"]:', '        elif False:'),
    ('unowned entry file overwritten', 'gpos/adapters/pipeline.py', '        elif path == backend.entrypoint:\n            diags.append(dg.make("UNOWNED_ENTRYPOINT"', '        elif path == backend.entrypoint:\n            writes[path] = f.data\n        elif False:\n            diags.append(dg.make("UNOWNED_ENTRYPOINT"'),
    ('edited generated file overwritten silently', 'gpos/adapters/pipeline.py', '            if repair:\n                writes[path] = f.data', '            if True:\n                writes[path] = f.data'),
    ('edited stale file deleted', 'gpos/adapters/pipeline.py', 'elif sha256_bytes(current) == digest or repair:', 'elif True:'),
    ('path traversal allowed', 'gpos/adapters/paths.py', '    return all(part not in ("..", ".", "") for part in path.split("/"))', '    return True'),
    ('symlink check disabled', 'gpos/adapters/paths.py', '        if os.path.islink(current):', '        if False:'),
    ('validator precondition skipped', 'gpos/adapters/pipeline.py', '    if not result.valid:\n        codes =', '    if False:\n        codes ='),
    ('root budget ignored', 'gpos/adapters/validation.py', 'if len(text) > content.ROOT_MAX_CHARS or lines > content.ROOT_MAX_LINES:', 'if False:'),
    ('monolith check disabled', 'gpos/adapters/validation.py', 'if len(excerpt) >= content.MONOLITH_EXCERPT_CHARS and excerpt in content.normalize(text):', 'if False:'),
    ('unexpected managed files ignored', 'gpos/adapters/pipeline.py', '    for path in sorted(managed_files_on_disk(root, backend) - set(listed) - {backend.manifest_path}):', '    for path in sorted(set()):'),
    ('conflicts do not stop sync', 'gpos/adapters/pipeline.py', '    if dg.result_class(diags) != dg.OK:\n        return Result("sync", ir.project["id"], diags)  # nothing written', '    if False:\n        return Result("sync", ir.project["id"], diags)  # nothing written'),
    ('lock kind not checked', 'gpos/adapters/compiler.py', 'if decision.get("kind") not in binding["decision_kinds"]:', 'if False:'),
    ('lock decider not checked', 'gpos/adapters/compiler.py', 'if who.get("kind") != "HUMAN" or allowed is None or not ({"ALL", decision["kind"]} & allowed):', 'if False:'),
    ('lock subject not checked', 'gpos/adapters/compiler.py', 'if subject.get("kind") != binding["subject_kind"] or subject.get("ref") != config["project"]["id"]:', 'if False:'),
    ('lock row target not checked', 'gpos/adapters/compiler.py', 'and x.get("item") == r["item"]]', 'or True]'),
    ('locked value not compared', 'gpos/adapters/compiler.py', 'elif all(x["value"] != r["value"] for x in bound):', 'elif False:'),
    ('document lock hash not checked', 'gpos/adapters/compiler.py', 'x.get("document_sha256") == doc_hash', 'True'),
    ('inactive lock accepted', 'gpos/adapters/compiler.py', 'if decision.get("status") != "ACTIVE":', 'if False:'),
    ('malformed authority row skipped', 'gpos/adapters/sources.py', '            problems.append(f"line {n}: authority row must have exactly three non-empty cells (Item | Value | Status)")', '            pass'),
    ('duplicate authority rows accepted', 'gpos/adapters/sources.py', '        if key in seen:', '        if False:'),
    ('near-miss authority header ignored', 'gpos/adapters/sources.py', 'if not in_table and "Value" in cells and any(c.startswith("Status") for c in cells):', 'if False:'),
    ('instruction layers ignored on sync', 'gpos/adapters/pipeline.py', '    diags += _layer_conflicts(root, bundle, set(owned_entry_files) | {backend.entrypoint})', '    pass'),
    ('instruction layers ignored on check', 'gpos/adapters/pipeline.py', '    out = _layer_conflicts(root, bundle, owned_entrypoints(root, BACKENDS.values(), _manifest_or_none))', '    out = []'),
    ('nested layers not scanned', 'gpos/adapters/layers.py', 'dirnames[:] = sorted(d for d in dirnames if d != ".git")', 'dirnames[:] = []'),
    ('Codex override file not a layer', 'gpos/adapters/backends.py', 'instruction_layer_names = ("AGENTS.md", "AGENTS.override.md")', 'instruction_layer_names = ("AGENTS.md",)'),
    ('Claude rules directory not a layer', 'gpos/adapters/backends.py', 'instruction_layer_dirs = (".claude/rules",)', 'instruction_layer_dirs = ()'),
    ('skill ids not project-scoped', 'gpos/adapters/compiler.py', '    name = f"{SKILL_PREFIX}{namespace}-{skill}"', '    name = f"{SKILL_PREFIX}{skill}"'),
    ('namespace without hash', 'gpos/adapters/compiler.py', '    return f"{prefix}-{hashlib.sha256(project_id.encode(\'utf-8\')).hexdigest()[:6]}"', '    return prefix'),
    ('rule sources not hashed', 'gpos/adapters/compiler.py', '        used.append(src.read_source(src.NORMATIVE, f"gpos:{path}", full))', '        pass'),
    ('renderer drops unplaced rules', 'gpos/adapters/content.py', '    other = _rules_for(ir, "other-rules")', '    other = ""'),
    ('rule markers not checked', 'gpos/adapters/content.py', '        "routing": rules("routing") + [_invoke(ir, fmt, "game-director")]', '        "routing": [_invoke(ir, fmt, "game-director")]'),
    ('director rule not tied to never_cross_reviewer', 'gpos/adapters/compiler.py', '        if skill is not None and skill not in reg["never_cross_reviewer"]:', '        if False:'),
    ('claudeMdExcludes ignored', 'gpos/adapters/layers.py', 'if isinstance(data, dict) and "claudeMdExcludes" in data and excludes not in (None, []):', 'if False:'),
    ('unreadable Claude settings ignored', 'gpos/adapters/layers.py', '            out.append(("INSTRUCTION_CONFIG_UNREADABLE", rel, f"{rel} cannot be parsed ({type(exc).__name__}); GPOS cannot "\n                                                             f"tell whether it excludes the generated instructions"))', '            pass'),
    ('model_instructions_file ignored', 'gpos/adapters/layers.py', '        for key in CODEX_INSTRUCTION_REPLACEMENT_KEYS:', '        for key in ():'),
    ('profiles not inspected', 'gpos/adapters/layers.py', '    for name, prof in sorted((table.get("profiles") or {}).items()) if isinstance(table.get("profiles"), dict) else []:', '    for name, prof in []:'),
    ('project_doc_max_bytes ignored', 'gpos/adapters/layers.py', '            elif value < root_bytes:', '            elif False:'),
    ('fallback names not scanned', 'gpos/adapters/pipeline.py', 'for path in unmanaged_instruction_layers(root, backend, owned, extra_names)]', 'for path in unmanaged_instruction_layers(root, backend, owned)]'),
    ('malformed Codex config ignored', 'gpos/adapters/layers.py', '            out.append(("INSTRUCTION_CONFIG_UNREADABLE", rel, f"{rel} cannot be parsed ({type(exc).__name__}); GPOS cannot "\n                                                             f"tell whether it replaces or limits the generated instructions"))', '            pass'),
    ('skills.config disable ignored', 'gpos/adapters/layers.py', '            if isinstance(entry, dict) and entry.get("enabled") is False:', '            if False:'),
    ('nested config files not found', 'gpos/adapters/layers.py', '    for base, _ in _walk(root):\n        for n in names:', "    for base, _ in [(Path('.'), None)]:\n        for n in names:"),
    ('skill id collisions ignored', 'gpos/adapters/pipeline.py', '            for path, sid in skill_id_collisions(root, backend, [s.agent_id for s in ir.skills])]', '            for path, sid in []]'),
    ('declared skill names ignored', 'gpos/adapters/layers.py', '        hit = ids & {base.name, _skill_name(root / rel)}', '        hit = ids & {base.name}'),
    ('authority table without section accepted', 'gpos/adapters/sources.py', '            if in_table and section is None:', '            if False:'),
]


def run(mutation):
    name, rel, anchor, replacement = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-mut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        path = copy / rel
        text = path.read_text()
        if text.count(anchor) != 1:
            return name, f"NOT APPLIED (anchor found {text.count(anchor)} times)"
        path.write_text(text.replace(anchor, replacement))
        out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_adapters.py")], capture_output=True,
                             text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=600)
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    args = parser.parse_args()
    selected = [m for m in MUTATIONS if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
