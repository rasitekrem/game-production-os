#!/usr/bin/env python3
"""Write tests/fixtures/posix-parity-alpha22.json from the frozen v1.0.0-alpha.22 tag (alpha.23).

    python3 tests/generate_posix_parity.py

It reads every blob from the tag itself through git, never from the working tree, so regenerating it after
alpha.23 changes still records alpha.22. The fixture holds:

* `frozen`: the sha256 of every tracked file under the frozen areas (core/, schemas/, skills/, workflows/,
  templates/ and gpos/), so tests/test_posix_parity.py can require byte identity for every file alpha.23 does not
  deliberately edit;
* `units`: the POSIX fingerprints (tests/posix_parity.py) of every Python module under gpos/tools/;
* `codes`: every diagnostic code of gpos/tools/diagnostics.py with its (class, meaning) as written, so additions can
  be told apart from changes;
* `sources`: the exact alpha.22 text of the few functions alpha.23 changes by a one-expression substitution, so the
  test can prove that substitution is the whole change.
"""

import ast

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import posix_parity  # noqa: E402

TAG = "v1.0.0-alpha.22"
FROZEN = ("core/", "schemas/", "skills/", "workflows/", "templates/", "gpos/")
OUT = ROOT / "tests" / "fixtures" / "posix-parity-alpha22.json"
SOURCES = {"gpos/tools/artifacts.py": ("collect", "collect_inputs", "_relative"),
           "gpos/tools/process.py": ("run_process",)}


def codes(source):
    tree = ast.parse(source)
    table = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                 and [ast.unparse(t) for t in n.targets] == ["CODES"])
    return {k.value: ast.unparse(v) for k, v in zip(table.keys, table.values)}


def functions(source, names):
    tree = ast.parse(source)
    return {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names}


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True).stdout


def main():
    commit = git("rev-parse", f"{TAG}^{{commit}}").decode().strip()
    names = [n for n in git("ls-tree", "-r", "--name-only", "-z", commit).decode("utf-8").split("\0") if n]
    frozen, units, sources = {}, {}, {}
    for name in sorted(names):
        if not name.startswith(FROZEN):
            continue
        blob = git("show", f"{commit}:{name}")
        frozen[name] = hashlib.sha256(blob).hexdigest()
        if name.startswith("gpos/tools/") and name.endswith(".py"):
            units[name] = posix_parity.units(blob.decode("utf-8"))
        if name in SOURCES:
            sources[name] = functions(blob.decode("utf-8"), SOURCES[name])
    table = codes(git("show", f"{commit}:gpos/tools/diagnostics.py").decode("utf-8"))
    data = {"tag": TAG, "commit": commit, "frozen": frozen, "units": units, "codes": table, "sources": sources}
    OUT.write_bytes((json.dumps(data, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print(f"{OUT.relative_to(ROOT).as_posix()}: {len(frozen)} frozen files, {len(units)} fingerprinted modules "
          f"from {TAG} ({commit})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
