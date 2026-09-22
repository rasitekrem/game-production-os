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
    ('root bypasses the validator', 'gpos/adapters/content.py', "exited: `{v['readiness']}`. Only exit", 'exited: use your judgement. Only exit'),
    ('game-director allowed as reviewer', 'gpos/adapters/compiler.py', 'never_cross_reviewer=name in reg["never_cross_reviewer"]', 'never_cross_reviewer=False'),
    ('project authority omitted from skills', 'gpos/adapters/compiler.py', 'authority_files=tuple(f for f in authority_files if f in _named_inputs(sections)),', 'authority_files=(),'),
    ('locked project rows dropped', 'gpos/adapters/content.py', '    return [r for r in doc.rows if r.status == "LOCKED"]', '    return []'),
    ('placeholders not preserved', 'gpos/adapters/sources.py', '"placeholders": sorted(tokens & set(placeholders)),', '"placeholders": [],'),
    ('unbacked lock shown as verified', 'gpos/adapters/compiler.py', 'r["status"] == src.LOCKED and r["decision_ref"] in decisions', 'r["status"] == src.LOCKED'),
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
