#!/usr/bin/env python3
"""Bounded mutation harness for the production Git provenance adapter (gpos/tools/git/).

    python3 tests/mutate_git_adapter.py [--jobs N] [--only TEXT]

Each mutation breaks exactly one semantic guarantee of the adapter in a temporary copy of the
repository and runs tests/test_git_adapter.py — real Git, real repositories — there. A mutation
must make the suite fail ("CAUGHT"); one that leaves it green is "MISSED" and fails this harness.
An anchor that does not match exactly once is "NOT APPLIED" and also fails, so the list cannot rot.

A mutation is a list of (file, anchor, replacement) edits. Some guarantees are defended twice on
purpose (for example, a dirty tree has no exact revision in the parsed state *and* resolve refuses
it); a realistic bug that defeats such a guarantee has to remove both, so it is written as one
mutation with two edits rather than as two mutations that are each equivalent.
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
ADAPTER, STATUS, REGISTRY = "gpos/tools/git/adapter.py", "gpos/tools/git/status.py", "gpos/tools/registry.py"

MUTATIONS = [
    ("1 production registry forgets Git", [
        (REGISTRY, "    registry.register(GitAdapter())\n    return registry", "    return registry")]),
    ("2 TEST_ONLY synthetic enters the production registry", [
        (REGISTRY, "    registry.register(GitAdapter())\n    return registry",
         "    registry.register(GitAdapter())\n    registry.allow_test_only = True\n"
         "    from .synthetic import SyntheticAdapter\n    registry.register(SyntheticAdapter())\n    return registry")]),
    ("3 repository-root equality check removed", [
        (ADAPTER, "        if not same_directory(toplevel, context.project_root):", "        if False:")]),
    ("4 dirty repository given exact_revision = HEAD", [
        (STATUS, '        "exact_revision": head_sha if (head_sha is not None and clean) else None,',
         '        "exact_revision": head_sha,')]),
    ("5 unborn repository fabricates a revision", [
        (STATUS, "    head_sha = None if unborn else oid", '    head_sha = ("0" * 40) if unborn else oid')]),
    ("6 dirty resolve-provenance succeeds with HEAD", [
        (ADAPTER, '        data = {"repository_revision": state["exact_revision"],',
         '        data = {"repository_revision": state["head_sha"],'),
        (ADAPTER, '        elif not state["clean"]:', '        elif False:'),
        (ADAPTER, '        elif state["exact_revision"] is None:', '        elif False:')]),
    ("7 output truncation ignored", [
        (ADAPTER, "    if outcome.truncated:\n        return _failed(", "    if False:\n        return _failed(")]),
    ("8 GIT_OPTIONAL_LOCKS protection removed", [
        (ADAPTER, '    ("GIT_OPTIONAL_LOCKS", "0"),   # status must not refresh and rewrite the index\n', "")]),
    ("9 interactive prompting allowed", [
        (ADAPTER, '    ("GIT_TERMINAL_PROMPT", "0"),  # never prompt on a terminal\n', "")]),
    ("10 the adapter imports subprocess directly", [
        (ADAPTER, "import os\nimport re\nimport shutil\n", "import os\nimport re\nimport shutil\nimport subprocess\n")]),
    ("11 a network Git command becomes authorized", [
        (ADAPTER, "AUTHORIZED_COMMANDS = (VERSION_ARGV, TOPLEVEL_ARGV, STATUS_ARGV)",
         'AUTHORIZED_COMMANDS = (VERSION_ARGV, TOPLEVEL_ARGV, STATUS_ARGV, ("fetch", "--all"))')]),
    ("12 caller-controlled Git argv introduced", [
        (ADAPTER, "        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv,",
         "        spec = proc.ToolProcessSpec(executable=context.probe.tool_path,\n"
         "                                    argv=argv + tuple(str(v) for v in context.request.inputs.values()),")]),
    # --- further guarantees the suite defends
    ("output altered by redaction is parsed anyway", [
        (ADAPTER, "    if outcome.redactions:\n        return _failed(", "    if False:\n        return _failed(")]),
    ("untracked directories collapsed into one entry", [
        (ADAPTER, '"--untracked-files=all"', '"--untracked-files=normal"')]),
    ("rename origin read as a new record", [
        (STATUS, "            i += 1  # the original path is its own NUL-terminated field",
         "            pass")]),
    ("unrecognized status records tolerated", [
        (STATUS, '        if kind not in FIELDS_BEFORE_PATH:\n            raise StatusParseError(',
         '        if kind not in FIELDS_BEFORE_PATH:\n            continue\n            raise StatusParseError(')]),
    ("unrecognized version output becomes a guessed version", [
        (ADAPTER, "        if version is None:", "        if version is None:\n            version = (2, 99, 0)\n"
                                                  "        if version is None:")]),
    ("a relative PATH entry is trusted", [
        (ADAPTER, "        if not os.path.isabs(found):", "        if False:")]),
    ("a non-repository project reported as clean", [
        (ADAPTER, "        if top.exit_code != 0:\n", "        if False:\n")]),
    ("raw path output leaks into the result", [
        (ADAPTER, '    return replace(outcome, stdout="")', "    return outcome")]),
]


def run(mutation):
    name, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-gitmut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for rel, anchor, replacement in edits:
            path = copy / rel
            text = path.read_text()
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            path.write_text(text.replace(anchor, replacement))
        out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_git_adapter.py")],
                             capture_output=True, text=True, timeout=900,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=2)
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
