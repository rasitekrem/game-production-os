#!/usr/bin/env python3
"""Bounded mutation harness for Unity Build Core (Phase 2C-7, alpha.21) and the foundation's request-id guard.

    python3 tests/mutate_unity_build.py [--jobs N] [--only TEXT] [--anchors] [--real]

Each mutation breaks exactly one guarantee in a temporary copy of the repository and runs one suite there:

    "build"       tests/test_unity_build.py with GPOS_UNITY_TEST_FAST=1 (stand-in Editor, scans, tree digest, publication)
    "core"        tests/test_unity_live_bridge_core.py (the build rules of the bridge's C# core, with Unity's bundled Mono)
    "foundation"  tests/test_tool_foundation.py (the request-id path-safety guard)
    "real_classic", "real_profile"  (--real) RB1_Classic / RB2_Profile of tests/test_unity_build.py in lab-owned Editors

A mutation of the bridge package regenerates its manifest in the copy first, so it can only be caught by a test of
what the code does. The copy has no .git; GPOS_SOURCE_GIT_DIR points the frozen-tag tests at this repository's
(read only). A mutation must make its suite fail ("CAUGHT"); one that leaves it green is "MISSED" and fails this
harness, and so does an anchor that does not match exactly once ("NOT APPLIED"). The repository itself is never
modified, and after each real mutation every Unity process started for that copy is stopped.
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
AD, UB, EX, BI = "gpos/tools/unity/adapter.py", "gpos/tools/unity/build.py", "gpos/tools/execution.py", "gpos/tools/unity/bridge_install.py"
ED = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/"
RULES, ENTRY, CONF, PROTO = ED + "Core/BuildRules.cs", ED + "Build/BuildEntry.cs", ED + "Build/BuildConfiguration.cs", ED + "Core/Protocol.cs"

MUTATIONS = [
    # --- the fixed command
    ("caller-controlled executeMethod", "build", [(UB, '"-cacheServerEnableUpload", "false", "-executeMethod", BUILD_ENTRY_METHOD,',
                                                   '"-cacheServerEnableUpload", "false", "-executeMethod", Path(workspace).name,')]),
    ("caller-controlled argv", "build", [(AD, "argv=ub.build_argv(project, workspace),", "argv=ub.build_argv(project, workspace) + (request.subject.ref,),")]),
    ("the build command quits", "build", [(UB, '            REQUEST_FLAG, str(workspace / REQUEST_NAME))', '            REQUEST_FLAG, str(workspace / REQUEST_NAME), "-quit")')]),
    # --- request rules and identity
    ("accepting request.build_id", "build", [(AD, "        if request.build_id is not None:\n            return _refuse(cap, f\"{cap} takes no build_id",
                                               "        if False:\n            return _refuse(cap, f\"{cap} takes no build_id")]),
    ("unsafe request_id reaches workspace creation", "foundation", [(EX, "    if request.request_id is not None and not (isinstance(request.request_id, str)",
                                                                     "    if False and not (isinstance(request.request_id, str)")]),
    ("an empty request_id is silently replaced", "foundation", [(EX, "    request = request if request.request_id is not None else \\",
                                                                "    request = request if request.request_id else \\")]),
    ("build id not derived from request_id", "build", [(UB, '    return "build-" + request_id', '    return "build-" + hashlib.sha256(request_id.encode()).hexdigest()[:16]')]),
    ("the build request-id grammar is not enforced", "build", [(AD, "            if not ub.BUILD_REQUEST_ID.fullmatch(rid):", "            if False:")]),
    ("a build without build_revision", "build", [(AD, "            if not (isinstance(request.build_revision, str) and ub.REVISION.fullmatch(request.build_revision)):",
                                                   "            if False:")]),
    ("a non-macOS target platform is accepted", "build", [(AD, '            if request.target_platform not in (None, "MACOS"):', "            if False:")]),
    # --- configuration token and post-checks (GPOS side)
    ("omitted config pre-check", "build", [(AD, '''            ("the inspected configuration token", response["configuration_token"] == request.inputs[
                "expected_configuration_token"]),\n''', "")]),
    ("omitted config post-check", "build", [(AD, '''            ("the post-build configuration token", post["configuration_token"] == response["configuration_token"]),\n''', "")]),
    ("Development mismatch accepted", "build", [(AD, '''            ("the development state", post["development"] is conf["development"] is b["development_observed"]),\n''', "")]),
    ("the target is not re-checked", "build", [(AD, '''            ("the active target", post["active_target"] == conf["active_target"] == b["platform"] == ub.TARGET),\n''', "")]),
    ("the token is not recomputed by GPOS", "build", [(UB, "    if not (_is(token, str) and TOKEN.fullmatch(token)) or token_of(configuration) != token:",
                                                        "    if not (_is(token, str) and TOKEN.fullmatch(token)):")]),
    ("the canonical form differs from the entry's", "build", [(UB, 'separators=(",", ":"), ensure_ascii=True, allow_nan=False)', 'separators=(", ", ":"), ensure_ascii=True, allow_nan=False)')]),
    # --- payload validation, digest and publication
    ("publication before validation", "build", [(AD, "        try:\n            names = sorted(os.listdir(workspace / ub.STAGING))",
                                                  "        ub.publish(workspace)\n        try:\n            names = sorted(os.listdir(workspace / ub.STAGING))")]),
    ("manifest before payload rename", "build", [(AD, "            ub.publish(workspace)\n        except OSError as exc:",
                                                   "            ub.write_manifest(workspace, build_manifest(request, response, app, tree, _started_utc(workspace), context.clock.now()))\n            ub.publish(workspace)\n        except OSError as exc:"),
                                                  (AD, "            ub.write_manifest(workspace, manifest)\n", "            pass\n")]),
    ("directory emitted as ArtifactSpec", "build", [(AD, '''                                  description="GPOS build manifest: binds the workspace payload by its tree digest"),) + log''',
                                                     '''                                  description="GPOS build manifest: binds the workspace payload by its tree digest"),
                     ArtifactSpec("payload", "OTHER", str(workspace / ub.PAYLOAD / ub.APP))) + log''')]),
    ("payload outside workspace", "build", [(UB, "    os.rename(workspace / STAGING, workspace / PAYLOAD)", "    os.rename(workspace / STAGING, workspace.parent / PAYLOAD)")]),
    ("staging may hold more than the app", "build", [(AD, "            if names != [ub.APP]:", "            if ub.APP not in names:")]),
    ("boot.config GUID check removed", "build", [(UB, '    if lines != [f"build-guid={guid}"]:', "    if False:")]),
    ("the bundle identifier is not checked", "build", [(UB, "    if ident != bundle_identifier:", "    if False:")]),
    ("a non-executable is accepted", "build", [(UB, "    if not est.st_mode & stat.S_IXUSR:", "    if False:")]),
    ("tree bound removed", "build", [(UB, "            if len(lines) > MAX_ENTRIES:", "            if False:")]),
    ("tree byte bound removed", "build", [(UB, "                if total > MAX_TOTAL_BYTES:", "                if False:")]),
    ("tree depth bound removed", "build", [(UB, "        if depth > MAX_DEPTH:", "        if False:")]),
    ("escaping link accepted", "build", [(UB, "                        or not _inside(text, target_text)):", "                        or False):")]),
    ("special files accepted", "build", [(UB, '                raise PayloadProblem(f"payload entry {text} is not a regular file, directory or link")',
                                          "                continue")]),
    ("the digest ignores file sizes", "build", [(UB, '                lines.append(f"F\\t{text}\\t{est.st_size}\\t{_sha(full)}")', '                lines.append(f"F\\t{text}\\t{_sha(full)}")')]),
    ("the manifest may overwrite", "build", [(UB, "        os.link(temporary, final)", "        os.replace(temporary, final)\n        os.link(final, temporary)")]),
    ("git_verified written", "build", [(AD, '        "build_revision_source": "CALLER_SUPPLIED",\n        "configuration_token"',
                                        '        "build_revision_source": "CALLER_SUPPLIED",\n        "git_verified": True,\n        "configuration_token"')]),
    # --- public text and paths
    ("BuildReport path sanitization is bypassed", "build", [(AD, "        clean = lambda text, bound: ub.clean_message(text, bases, bound)   # every text the entry or Unity wrote",
                                                             "        clean = lambda text, bound: text")]),
    ("BuildReport text skips the redaction boundary", "build", [(UB, "    text, _ = redaction.redact(text)\n    text = ABSOLUTE_PATH.sub", "    text = ABSOLUTE_PATH.sub")]),
    ("absolute paths are kept in BuildReport text", "build", [(UB, "    text = ABSOLUTE_PATH.sub(\"<path>\", text)\n    return text[:bound]", "    return text[:bound]")]),
    ("BuildReport text is not clipped after cleaning", "build", [(UB, "    return text[:bound]", "    return text")]),
    ("project paths are not relativized", "build", [(UB, "        text = text.replace(base.rstrip(\"/\") + \"/\", \"\")", "        pass")]),
    ("the absolute outputPath leaks into the result", "build", [(AD, "\"development_observed\", \"error_message_count\")}", "\"development_observed\", \"error_message_count\", \"output_path\")}")]),
    ("a mismatched outputPath is reported by value", "build", [(AD, "            (\"the output path\", os.path.realpath(b[\"output_path\"])",
                                                                "            (\"the output path \" + b[\"output_path\"], os.path.realpath(b[\"output_path\"])")]),
    # --- outcomes
    ("retry after unknown", "build", [(AD, "        outcome = context.run(spec)\n        record = dict(command=_command(spec, project, workspace), environment=spec.env.metadata())\n        result = self._classify_build(",
                                       "        outcome = context.run(spec)\n        if ub.started(workspace) and not (workspace / ub.RESPONSE_NAME).exists():\n            outcome = context.run(spec)\n        record = dict(command=_command(spec, project, workspace), environment=spec.env.metadata())\n        result = self._classify_build(")]),
    ("a started build without an answer is a plain failure", "build", [(AD, "        began = building and ub.started(workspace)", "        began = False")]),
    ("a killed build counts as timed out, not unknown", "build", [(AD, "            if began:\n                return unknown(f\"the build started but no trustworthy final response exists ({problem})\")",
                                                                   "            if began and not outcome.timed_out:\n                return unknown(f\"the build started but no trustworthy final response exists ({problem})\")")]),
    ("the log is read although the entry answered", "build", [(AD, "        if not outcome.timed_out and outcome.exit_code == 0:\n            try:\n                response = ub.read_response(",
                                                               "        if ur.classify_log(ur.log_tail(log_path), outcome.stdout) == ur.UNCLASSIFIED and not outcome.timed_out and outcome.exit_code == 0:\n            try:\n                response = ub.read_response(")]),
    ("a refusal maps to success", "build", [(UB, '    "TARGET_NOT_ACTIVE": "BUILD_TARGET_NOT_ACTIVE",', '    "TARGET_NOT_ACTIVE": "BUILD_CONFIGURATION_NOT_BUILDABLE",')]),
    # --- writer boundary
    ("lock proof skipped", "build", [(AD, "        proof = self._lock_proof(project, context.probe.tool_path)   # again, immediately before the launch\n        if proof.state not in pl.PROCEED:   # a writer appeared meanwhile\n            return _locked(cap, proof)\n        outcome = context.run(spec)\n        record = dict(command=_command(spec, project, workspace), environment=spec.env.metadata())\n        result = self._classify_build(",
                                       "        outcome = context.run(spec)\n        record = dict(command=_command(spec, project, workspace), environment=spec.env.metadata())\n        result = self._classify_build(")]),
    ("the build treats any lockfile as a conflict", "build", [(AD, "   # before the workspace is used\n            return _locked(cap, proof)\n        if context.dry_run:",
                                                                "   # before the workspace is used\n            return _locked(cap, proof)\n        if (project / \"Temp\" / \"UnityLockfile\").exists():\n            return _locked(cap, proof)\n        if context.dry_run:")]),
    ("the build orphan lock is not reported", "build", [(AD, "    if proof.state == pl.ORPHAN_UNHELD:\n        result = replace(result, diagnostics=tuple(result.diagnostics) + (dg.make(",
                                                         "    if False:\n        result = replace(result, diagnostics=tuple(result.diagnostics) + (dg.make(")]),
    ("a reused workspace is adopted", "build", [(AD, "        present = ub.unfresh(workspace)\n        if present:", "        present = ub.unfresh(workspace)\n        if False:")]),
    ("the build entry runs from an older bridge", "build", [(AD, "        if entry != bi.EXACT:", "        if entry not in (bi.EXACT, bi.PREVIOUS_STATE):")]),
    ("Git invocation added to the Unity adapter", "build", [(AD, "        ub.write_request(workspace, \"BUILD\" if building else \"INSPECT\", rid, token if building else None)",
                                                               "        __import__(\"subprocess\").run([\"git\", \"status\"], cwd=str(root), capture_output=True)\n        ub.write_request(workspace, \"BUILD\" if building else \"INSPECT\", rid, token if building else None)")]),
    ("the build is declared read-only", "build", [(AD, '''        id=ub.BUILD, category="BUILD", operation_class="MUTATING",''', '''        id=ub.BUILD, category="BUILD", operation_class="READ_ONLY",''')]),
    ("the inspection is declared read-only", "build", [(AD, '''        id=ub.INSPECT_BUILD, category="INSPECT", operation_class="MUTATING",''', '''        id=ub.INSPECT_BUILD, category="INSPECT", operation_class="READ_ONLY",''')]),
    # --- the release
    ("the live protocol is bumped", "build", [(PROTO, 'public const string Name = "gpos.unity.live/5";', 'public const string Name = "gpos.unity.live/6";')]),
    ("the pinned 1.4.0 digest is not the frozen one", "build", [(BI, '"90dedd4089e602728c3402557b232fcb8a865a423a0a3f11e52e02e3c6c88422"', '"90dedd4089e602728c3402557b232fcb8a865a423a0a3f11e52e02e3c6c88420"')]),
    # --- the build entry (C#) — caught by the source scans
    ("Editor: the entry runs outside batch mode", "build", [(ENTRY, "            if (AssetDatabase.IsAssetImportWorkerProcess() || !Application.isBatchMode) return;   // never a Human's Editor",
                                                             "            if (AssetDatabase.IsAssetImportWorkerProcess()) return;")]),
    ("Editor: the entry switches the target", "build", [(ENTRY, "            BuildProfile profile;\n            var facts = BuildConfiguration.Read(place.ProjectPath, out profile);\n            var problems = BuildRules.Assess(facts);",
                                                          "            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Standalone, BuildTarget.StandaloneOSX);\n            BuildProfile profile;\n            var facts = BuildConfiguration.Read(place.ProjectPath, out profile);\n            var problems = BuildRules.Assess(facts);")]),
    ("Editor: the entry activates a profile", "build", [(ENTRY, "            BuildProfile after;", "            BuildProfile.SetActiveBuildProfile(profile);\n            BuildProfile after;")]),
    ("Editor: the entry assigns a build setting", "build", [(ENTRY, "            string output = Path.Combine(workspace, BuildRules.StagingName, BuildRules.PayloadName);",
                                                             "            EditorUserBuildSettings.development = false;\n            string output = Path.Combine(workspace, BuildRules.StagingName, BuildRules.PayloadName);")]),
    ("Editor: the request comes from the environment", "build", [(ENTRY, "            var args = Environment.GetCommandLineArgs();",
                                                                   "            var args = new[] { BuildRules.RequestFlag, Environment.GetEnvironmentVariable(\"GPOS_BUILD_REQUEST\") };")]),
    ("Editor: output leaves the workspace", "build", [(ENTRY, "            string output = Path.Combine(workspace, BuildRules.StagingName, BuildRules.PayloadName);",
                                                       "            string output = Path.Combine(place.GposRoot, \"builds\", request.BuildId, BuildRules.PayloadName);")]),
    ("Editor: a second public entry", "build", [(ENTRY, "        static int Execute()", "        public static void Other() { }\n\n        static int Execute()")]),
    # --- the build rules (C# core)
    ("Rules: Player Settings overrides accepted", "core", [(RULES, "                    if (pr.PlayerSettingsOverrides != 0)", "                    if (false)")]),
    ("Rules: debug/profiler flags accepted", "core", [(RULES, "            if (debug)\n", "            if (false)\n")]),
    ("Rules: classic code coverage accepted", "core", [(RULES, "                debug = f.ConnectProfiler || f.AllowDebugging || f.DeepProfiling || f.WaitForManagedDebugger || f.CodeCoverage || f.WaitForPlayerConnection;",
                                                         "                debug = f.ConnectProfiler || f.AllowDebugging || f.DeepProfiling || f.WaitForManagedDebugger || f.WaitForPlayerConnection;")]),
    ("Rules: profile development may disagree", "core", [(RULES, "                    if (pr.Development != f.Development)", "                    if (false)")]),
    ("Rules: a non-active target is buildable", "core", [(RULES, "            if (f.ActiveTarget != Target)", "            if (false)")]),
    ("Rules: IL2CPP is buildable", "core", [(RULES, "            if (f.Backend != Mono)", "            if (false)")]),
    ("Rules: a non-macOS profile is buildable", "core", [(RULES, "                    if (!pr.TargetIsMac || pr.PlatformId != MacModuleGuid || !pr.SubtargetIsPlayer)", "                    if (!pr.SubtargetIsPlayer)")]),
    ("Rules: no scenes is buildable", "core", [(RULES, "            else if (f.Scenes.Count == 0)", "            else if (false)")]),
    ("Rules: a scene's GUID is not checked", "core", [(RULES, "                    s.ResolvedGuid != s.Guid ||", "                    false ||")]),
    ("Rules: the token misses the profile file", "core", [(RULES, '{ "path", pr.Path }, { "guid", pr.Guid }, { "sha256", pr.Sha256 }, { "readable", pr.Readable },',
                                                           '{ "path", pr.Path }, { "guid", pr.Guid }, { "readable", pr.Readable },')]),
    ("Rules: DEL is not escaped like Python", "core", [(RULES, "                        if (c < 0x20 || c > 0x7e) sb.Append", "                        if (c < 0x20 || c > 0x7f) sb.Append")]),
    ("Rules: an extra request key is accepted", "core", [(RULES, "            if (d.Count != keys.Length || keys.Any(k => !d.ContainsKey(k) || !(d[k] is string)))",
                                                          "            if (keys.Any(k => !d.ContainsKey(k) || !(d[k] is string)))")]),
]

# Editor-side behaviour only a real build can show (run with --real; each runs one real class).
REAL_MUTATIONS = [
    ("Editor: the entry skips its token pre-check", "real_classic", [(ENTRY, '            if ((string)answer["configuration_token"] != request.ExpectedToken)', "            if (false)")]),
    ("Editor: classic builds ignore Development", "real_classic", [(ENTRY, "                      options = facts.Development ? BuildOptions.Development : BuildOptions.None });",
                                                                    "                      options = BuildOptions.None });")]),
    ("Editor: a profile build uses the classic options", "real_profile", [(ENTRY, "            BuildReport report = profile != null\n",
                                                                           "            BuildReport report = false\n")]),
]


def lab_editors_under(path):
    """PIDs of Unity processes whose command line names `path` (only the lab Editors of one mutation copy)."""
    out = subprocess.run(["/bin/ps", "-Ao", "pid=,command="], capture_output=True, text=True).stdout
    return [int(line.split(None, 1)[0]) for line in out.splitlines()
            if str(path) in line and "Unity.app/Contents/MacOS/Unity" in line]


def apply(copy, edits):
    for rel, anchor, replacement in edits:
        path = copy / rel
        text = path.read_text()
        if text.count(anchor) != 1:
            return f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
        path.write_text(text.replace(anchor, replacement))
    return None


SUITES = {"build": ("test_unity_build.py", []), "core": ("test_unity_live_bridge_core.py", []),
          "foundation": ("test_tool_foundation.py", []),
          "real_classic": ("test_unity_build.py", ["RB1_Classic"]), "real_profile": ("test_unity_build.py", ["RB2_Profile"])}


def run(mutation):
    name, suite, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-buildmut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        problem = apply(copy, edits)
        if problem:
            return name, problem
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GPOS_UNITY_TEST_FAST="1", GPOS_SOURCE_GIT_DIR=str(ROOT / ".git"))
        if suite.startswith("real"):
            env.pop("GPOS_UNITY_TEST_FAST")
        if any("live_bridge/" in rel for rel, _, _ in edits):
            subprocess.run([sys.executable, "-B", str(copy / "tests" / "generate_live_bridge_manifest.py")],
                           capture_output=True, text=True, timeout=120, env=env, check=True)
        test, groups = SUITES[suite]
        argv = [sys.executable, "-B", str(copy / "tests" / test)] + groups
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=5400, env=env)
        except subprocess.TimeoutExpired:
            return name, "CAUGHT"   # a hang is a failure of the suite
        finally:
            for pid in lab_editors_under(tmp):   # never leave a lab Editor of this copy running
                os.kill(pid, 9)
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def anchors():
    problems = []
    for name, _, edits in MUTATIONS + REAL_MUTATIONS:
        for rel, anchor, _ in edits:
            n = (ROOT / rel).read_text().count(anchor)
            if n != 1:
                problems.append(f"{name}: {rel} anchor found {n} times")
    print("\n".join(problems) or f"all anchors of {len(MUTATIONS)} + {len(REAL_MUTATIONS)} mutations apply exactly once")
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    parser.add_argument("--anchors", action="store_true", help="only check that every anchor applies exactly once")
    parser.add_argument("--real", action="store_true", help="run the Editor-side mutations against real lab Editors")
    args = parser.parse_args()
    if args.anchors:
        return anchors()
    selected = [m for m in (REAL_MUTATIONS if args.real else MUTATIONS) if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
