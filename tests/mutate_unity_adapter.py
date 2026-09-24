#!/usr/bin/env python3
"""Bounded mutation harness for the production Unity batch adapter (gpos/tools/unity/).

    python3 tests/mutate_unity_adapter.py [--jobs N] [--only TEXT]

Each mutation breaks exactly one semantic guarantee of the adapter, its static project preflight, its
results reader or the TOOL_INHERENT network semantic, in a temporary copy of the repository, and runs
tests/test_unity_adapter.py there with GPOS_UNITY_TEST_FAST=1: the static, parser and stand-in groups,
never a real Unity process, so a mutation run creates no Unity user-global state. (The real-Unity groups
run in the ordinary regression.) A mutation must make the suite fail ("CAUGHT"); one that leaves it green
is "MISSED" and fails this harness. An anchor that does not match exactly once is "NOT APPLIED" and also
fails, so the list cannot rot.
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
ADAPTER, PROJECT = "gpos/tools/unity/adapter.py", "gpos/tools/unity/project.py"
RESULTS, REGISTRY = "gpos/tools/unity/results.py", "gpos/tools/registry.py"
VALIDATION, POLICY = "gpos/tools/validation.py", "core/registry.json"

PRODUCTION = "(AdbAdapter(), BlenderAdapter(), FfmpegAdapter(), FfprobeAdapter(), GitAdapter(), UnityAdapter())"
ARGV_TAIL = '"-runTests", "-testPlatform", platform,\n            "-testResults", str(workspace / RESULTS_NAME))'
EDIT_INPUTS = 'id=EDITMODE, category="RUN", operation_class="MUTATING", state_model="STATELESS",'
UPM_USER = '        ("UPM_USER_CONFIG_FILE", str((workspace / UPM_USER_NAME).resolve())),\n'
UPM_GLOBAL = '        ("UPM_GLOBAL_CONFIG_FILE", str((workspace / UPM_GLOBAL_NAME).resolve())),\n'
UPM_CACHE = '        ("UPM_CACHE_ROOT", str(Path(cache).resolve()))))'
DEPENDENCY_RAISE = '    raise ProjectProblem("PACKAGE_SOURCE", f"dependency {name!r} is neither a registry version nor a local file: "'
NO_RESULTS = "        cause = ur.classify_log(ur.log_tail(log_path), outcome.stdout)\n"
DRY_RUN = "        if context.dry_run:\n            return AdapterOutcome(\n"


def argv_extra(flag):
    return (ADAPTER, ARGV_TAIL, ARGV_TAIL[:-1] + f', "{flag}")')


MUTATIONS = [
    # --- registration and the network semantic
    ("unity absent from the production registry", [
        (REGISTRY, PRODUCTION, "(AdbAdapter(), BlenderAdapter(), FfmpegAdapter(), FfprobeAdapter(), GitAdapter())")]),
    ("TEST_ONLY synthetic enters the production registry", [
        (REGISTRY, "        registry.register(adapter)\n    return registry",
         "        registry.register(adapter)\n    registry.allow_test_only = True\n"
         "    from .synthetic import SyntheticAdapter\n    registry.register(SyntheticAdapter())\n    return registry")]),
    ("unity declares FORBIDDEN (undisclosed vendor traffic)", [
        (ADAPTER, 'network="TOOL_INHERENT", network_disclosure=NETWORK_DISCLOSURE,', 'network="FORBIDDEN",')]),
    ("TOOL_INHERENT allowlist widened", [
        (POLICY, '"TOOL_INHERENT": [\n        "unity"\n      ]', '"TOOL_INHERENT": [\n        "unity",\n        "unity-live"\n      ]')]),
    ("validation ignores the TOOL_INHERENT allowlist", [
        (VALIDATION, '    elif aid not in pol["network_semantic_adapters"].get(net, []):\n', "    elif False:\n")]),
    ("validation no longer requires a network disclosure", [
        (VALIDATION, "    elif not disclosure:\n", "    elif False:\n")]),
    ("disclosure no longer states that no OS confinement is claimed", [
        (ADAPTER, "disabled. No operating-system network confinement is claimed.", "disabled.")]),
    # --- discovery and probe
    ("PATH used for Editor discovery", [
        (ADAPTER, "        editors = self.discover()\n",
         '        editors = self.discover() or ([("6000.5.8f1", __import__("shutil").which("unity"))]\n'
         '                                      if __import__("shutil").which("unity") else [])\n')]),
    ("case-insensitive executable components accepted", [
        (ADAPTER, "                    if part not in entries:\n",
         "                    if part.lower() not in [e.lower() for e in entries]:\n")]),
    ("symlinked Editor executable accepted", [(ADAPTER, " and not path.is_symlink() and os.access", " and os.access")]),
    ("several Editors: the first one is chosen", [(ADAPTER, "        if len(editors) > 1:\n", "        if False:\n")]),
    ("probe does not confirm the printed version", [(ADAPTER, " or printed != version:", ":")]),
    ("static inspection requires the Editor", [
        (ADAPTER, 'execution_context="OFFLINE_ANALYSIS", requires_tool=False, requires_project=True,',
         'execution_context="OFFLINE_ANALYSIS", requires_tool=True, requires_project=True,')]),
    # --- project path, layout and version
    ("unity_project may resolve outside the GPOS root", [
        (PROJECT, '    if not _inside(project, root):\n        raise ProjectProblem("PROJECT_PATH"',
         '    if False:\n        raise ProjectProblem("PROJECT_PATH"')]),
    ("'..' allowed in unity_project", [(PROJECT, '    if ".." in parts:\n', "    if False:\n")]),
    ("Unity project layout not required", [(PROJECT, "        if not os.path.isdir(os.path.join(project, name)):\n",
                                            "        if False:\n")]),
    ("project Editor version falls back to the installed Editor", [
        (ADAPTER, "        if required != installed:\n", "        if False:\n")]),
    ("version grammar loosened", [(PROJECT, 'EDITOR_VERSION = re.compile(r"[0-9]{4}\\.[0-9]{1,3}\\.[0-9]{1,3}[abfp][0-9]{1,3}")',
                                   'EDITOR_VERSION = re.compile(r"[0-9.]+[a-z]*[0-9]*")')]),
    ("revision line not checked against the version", [
        (PROJECT, "        if not match or match.group(1) != version:\n", "        if not match:\n")]),
    # --- package sources
    ("scopedRegistries accepted", [(PROJECT, '    if manifest.get("scopedRegistries", []) != []:\n', "    if False:\n")]),
    ("unknown manifest keys (e.g. registry override) accepted", [(PROJECT, "    if unknown:\n", "    if False:\n")]),
    ("remote HTTPS/Git dependencies accepted", [
        (PROJECT, DEPENDENCY_RAISE, '    if value.startswith(("https://", "http://", "git")):\n        return "registry"\n'
                                    + DEPENDENCY_RAISE)]),
    ("SSH and SCP-style Git dependencies accepted", [
        (PROJECT, DEPENDENCY_RAISE, '    if "@" in value or value.startswith("ssh://"):\n        return "registry"\n'
                                    + DEPENDENCY_RAISE)]),
    ("local file: dependency may resolve outside the GPOS root", [
        (PROJECT, '    if not _inside(target, root):\n        raise ProjectProblem("PACKAGE_SOURCE"',
         '    if False:\n        raise ProjectProblem("PACKAGE_SOURCE"')]),
    ("file: URL and .git targets accepted", [
        (PROJECT, ' or raw.startswith("//") or any(c in raw for c in "?#") or raw.rstrip("/").endswith(".git"):',
         ' or any(c in raw for c in "?#"):')]),
    ("a Git working copy accepted as a local package", [
        (PROJECT, '        if os.path.exists(os.path.join(target, ".git")):\n', "        if False:\n")]),
    ("duplicate JSON keys accepted", [(PROJECT, "        if len(keys) != len(set(keys)):\n", "        if False:\n")]),
    ("lock file may record git sources", [
        (PROJECT, 'LOCK_SOURCES = {"builtin", "registry", "embedded", "local", "local-tarball"}',
         'LOCK_SOURCES = {"builtin", "registry", "embedded", "local", "local-tarball", "git"}')]),
    ("lock file registry URL not checked", [
        (PROJECT, '        if source == "registry" and entry.get("url") != DEFAULT_REGISTRY:\n', "        if False:\n")]),
    ("lock file local paths not checked", [
        (PROJECT, "            _local_package(version, packages_dir, root, name)\n", "            pass\n")]),
    # --- Package Manager isolation
    ("UPM user configuration not isolated", [(ADAPTER, UPM_USER, "")]),
    ("UPM global configuration not isolated", [(ADAPTER, UPM_GLOBAL, "")]),
    ("UPM cache not isolated", [(ADAPTER, UPM_CACHE, "))")]),
    ("proxy settings inherited from the caller", [
        (ADAPTER, "    return proc.EnvironmentPolicy(overrides=(",
         '    return proc.EnvironmentPolicy(inherit=proc.SAFE_ENV_DEFAULTS + ("HTTP_PROXY", "HTTPS_PROXY"), overrides=(')]),
    ("UPM configuration files carry settings", [
        (ADAPTER, '            with open(workspace / name, "x", encoding="utf-8"):\n                pass\n',
         '            with open(workspace / name, "x", encoding="utf-8") as handle:\n                handle.write("strictSsl = false\\n")\n')]),
    # --- command template
    ("-quit added to test runs", [argv_extra("-quit")]),
    ("-accept-apiupdate added", [argv_extra("-accept-apiupdate")]),
    ("-noUpm added", [argv_extra("-noUpm")]),
    ("-nographics added", [argv_extra("-nographics")]),
    ("Accelerator flags removed", [
        (ADAPTER, '            "-upmLogFile", str(workspace / UPM_LOG_NAME), "-cacheServerEnableDownload", "false",\n'
                  '            "-cacheServerEnableUpload", "false", ',
         '            "-upmLogFile", str(workspace / UPM_LOG_NAME), ')]),
    ("results written into the Unity project", [
        (ADAPTER, '            "-testResults", str(workspace / RESULTS_NAME))',
         '            "-testResults", str(Path(project) / RESULTS_NAME))')]),
    ("caller test filter reaches the command", [
        (ADAPTER, 'input_kinds=("unity_project",), artifact_kinds=("REPORT", "LOG"),\n        potential_evidence=(("TEST_EVIDENCE", '
                  '"AUTOMATED_TEST"),),\n        timeout=TimeoutPolicy(default=1800.0, maximum=3600.0), side_effect_scope=SIDE_EFFECTS,\n'
                  '        notes=(_project_note, "The project must require exactly the probed Editor version.")),\n    Capability(\n'
                  '        id=PLAYMODE',
         'input_kinds=("unity_project", "testFilter"), artifact_kinds=("REPORT", "LOG"),\n        potential_evidence=(("TEST_EVIDENCE", '
         '"AUTOMATED_TEST"),),\n        timeout=TimeoutPolicy(default=1800.0, maximum=3600.0), side_effect_scope=SIDE_EFFECTS,\n'
         '        notes=(_project_note, "The project must require exactly the probed Editor version.")),\n    Capability(\n'
         '        id=PLAYMODE')]),
    ("caller-chosen executable", [
        (ADAPTER, "        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv, cwd=str(workspace),",
         '        spec = proc.ToolProcessSpec(executable=(request.inputs or {}).get("executable") or context.probe.tool_path,\n'
         '                                    argv=argv, cwd=str(workspace),')]),
    ("recorded command leaks the project path", [
        (ADAPTER, "        if arg == project:\n            return PLACEHOLDERS[\"project\"]\n", "")]),
    # --- lease and Unity lock
    ("EditMode tests take no single-writer lease", [
        (ADAPTER, EDIT_INPUTS + '\n        execution_context="AUTOMATED_TEST", single_writer_required=True, resource_kind="EDITOR_PROJECT",',
         EDIT_INPUTS + '\n        execution_context="AUTOMATED_TEST", single_writer_required=False,')]),
    ("pre-launch Unity lock check removed", [
        (ADAPTER, "        if lock.exists() or lock.is_symlink():\n            return AdapterOutcome",
         "        if False:\n            return AdapterOutcome")]),
    ("a Unity lockfile is deleted to proceed", [
        (ADAPTER, "        if lock.exists() or lock.is_symlink():\n            return AdapterOutcome",
         "        if lock.exists():\n            lock.unlink()\n        if False:\n            return AdapterOutcome")]),
    ("refused second instance not classified", [
        (RESULTS, '    (PROJECT_LOCKED, ("another Unity instance is running with this project open",\n'
                  '                      "Multiple Unity instances cannot open the same project")),\n', "")]),
    # --- results and classification
    ("exit code 0 without results counts as a pass", [
        (ADAPTER, NO_RESULTS, "        if outcome.exit_code == 0:\n            return AdapterOutcome(ok=True, data=data, **base)\n"
                              + NO_RESULTS)]),
    ("zero executed tests accepted as evidence", [(ADAPTER, '            if counts["total"] == 0:\n', "            if False:\n")]),
    ("exit code and results consistency not checked", [(ADAPTER, "            if not consistent:\n", "            if False:\n")]),
    ("failed tests reported as a tool failure", [
        (ADAPTER, "            diagnostics = ()\n            if counts[\"failed\"]:\n",
         "            if counts[\"failed\"]:\n                return AdapterOutcome(ok=False, detail=\"tests failed\", data=data, **base)\n"
         "            diagnostics = ()\n            if counts[\"failed\"]:\n")]),
    ("licence failure not classified", [
        (RESULTS, '    (LICENSE_UNAVAILABLE, ("No valid Unity Editor license found",)),\n', "")]),
    ("compile failure not classified", [(RESULTS, '    (COMPILE_ERROR, ("Scripts have compiler errors.",)),\n', "")]),
    ("DTD and entity guard removed", [
        (RESULTS, '    if b"<!doctype" in lowered or b"<!entity" in lowered:\n', "    if False:\n")]),
    ("results read without a size bound", [
        (RESULTS, "            data = handle.read(MAX_RESULTS + 1)\n", "            data = handle.read()\n"),
        (RESULTS, "    if len(data) > MAX_RESULTS:\n", "    if False:\n")]),
    ("results counts not cross-checked", [
        (RESULTS, '    if counts["passed"] + counts["failed"] + counts["skipped"] + counts["inconclusive"] != counts["total"]:\n',
         "    if False:\n")]),
    ("results total not matched to test cases", [(RESULTS, '    if cases != counts["total"]:\n', "    if False:\n")]),
    # --- evidence, dry run, consent
    ("TEST_EVIDENCE claims TARGET_RUNTIME", [
        (ADAPTER, 'evidence_type="TEST_EVIDENCE", capture_context="AUTOMATED_TEST",',
         'evidence_type="TEST_EVIDENCE", capture_context="TARGET_RUNTIME",')]),
    ("the Editor log is offered as evidence", [
        (ADAPTER, 'source_capability=cap, generated_at=context.clock.now(), artifact_ids=("results",),',
         'source_capability=cap, generated_at=context.clock.now(), artifact_ids=("results", "editor-log"),')]),
    ("dry run launches Unity", [(ADAPTER, DRY_RUN, "        if False:\n            return AdapterOutcome(\n")]),
    ("dry run creates the package cache", [
        (ADAPTER, DRY_RUN, "        tp.runtime_dir(root, *UPM_CACHE).mkdir(parents=True, exist_ok=True)\n" + DRY_RUN)]),
    ("an existing results file is overwritten", [(ADAPTER, "        if existing:\n", "        if False:\n")]),
    ("test runs declared READ_ONLY", [
        (ADAPTER, EDIT_INPUTS, 'id=EDITMODE, category="RUN", operation_class="READ_ONLY", state_model="STATELESS",')]),
    # --- command surface
    ("the Unity adapter imports subprocess", [(ADAPTER, "import os\nimport sys\n", "import os\nimport subprocess\nimport sys\n")]),
    ("the project preflight gains network access", [(PROJECT, "import json\nimport os\n", "import json\nimport os\nimport urllib.request\n")]),
]


def run(mutation):
    name, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-unitymut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for rel, anchor, replacement in edits:
            path = copy / rel
            text = path.read_text()
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            path.write_text(text.replace(anchor, replacement))
        out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_unity_adapter.py")],
                             capture_output=True, text=True, timeout=1800,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GPOS_UNITY_TEST_FAST="1"))
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
