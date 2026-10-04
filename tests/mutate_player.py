#!/usr/bin/env python3
"""Bounded mutation harness for the player runtime adapter (Phase 2C-8, alpha.22).

    python3 tests/mutate_player.py [--jobs N] [--only TEXT] [--anchors]

Each mutation breaks exactly one guarantee in a temporary copy of the repository and runs one suite there:

    "player"   tests/test_player.py (fast: fakes through the real foundation; includes the static helper-source pins)
    "helper"   tests/test_player_helper.py RH4 RH5 (the real filesystem install and the in-process macOS backend)

Native helper mutations that need a rebuilt helper live in tests/mutate_player_helper.py. A mutation must make its
suite fail ("CAUGHT"); one that leaves it green is "MISSED" and fails this harness, and so does an anchor that does not
match exactly once ("NOT APPLIED"). The repository itself is never modified.
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
P = "gpos/tools/player/"
AD, INV, HP, RT, RS, MC, LG, CP, CT, AK = (P + n for n in ("adapter.py", "invocation.py", "helper.py", "runtime.py",
                                                           "resolver.py", "macos.py", "logs.py", "capture.py",
                                                           "contract.py", "appkit.py"))
SW_MAIN, SW_CAP = P + "helper_src/main.swift", P + "helper_src/Capture.swift"

SUITES = {"player": ("test_player.py", []), "helper": ("test_player_helper.py", ["RH4_Install", "RH5_Backend"])}

MUTATIONS = [
    ("the player adapter is absent from the production registry", "player",
     [("gpos/tools/registry.py", "    registry.register(PlayerAdapter())   # Phase 2C-8 (alpha.22): the engine-neutral player runtime adapter\n", "")]),
    # --- the one-external-process invariant and D7
    ("the single-invocation guard is removed", "player", [(INV, "        if self.kind is not None:\n            raise AssertionError",
                                                           "        if False:\n            raise AssertionError")]),
    ("the invocation table lets status start a process", "player", [(CT, "STATUS: (), SCREENSHOT: (SHOT,)", "STATUS: (SHOT,), SCREENSHOT: (SHOT,)")]),
    ("status starts a preflight process", "player", [(AD, "        helper_state = hp.classify(self.helper_bundle())[0]\n",
                                                      "        helper_state = hp.classify(self.helper_bundle())[0]\n        invocation.launch_services(c.SHOT, self.helper_bundle(), Path(context.workspace), 10)\n")]),
    ("capture runs a separate preflight process first", "player", [(AD, "        kind = c.VIDEO_KIND if video else c.SHOT\n",
                                                                    "        kind = c.VIDEO_KIND if video else c.SHOT\n        invocation.launch_services(kind, bundle, workspace, timeout=5)\n")]),
    ("a second supervise spawn after a failed launch (auto-retry)", "player", [(AD, "        except Refused as exc:\n            return self._abandon_launch(root, runtime_dir, session, exc, context)",
                                                                                "        except Refused as exc:\n            invocation.kind = None\n            invocation.supervise(helper_exe, runtime_dir)\n            return self._abandon_launch(root, runtime_dir, session, exc, context)")]),
    ("D7: the capture command is reported as the helper", "player", [(AD, "        record = dict(command=invocation.command(), environment=invocation.environment())",
                                                                      "        record = dict(command=dict(invocation.command(), executable=str(hp.executable(bundle))), environment=invocation.environment())")]),
    ("the open command loses -n", "player", [(INV, '    return ("-n", "-W", "--stdout"', '    return ("-W", "--stdout"')]),
    ("the open command names the helper by name", "player", [(INV, '            str(helper_bundle), "--args", MODES[kind]', '            "-a", "GposPlayerHelper", "--args", MODES[kind]')]),
    ("the supervise spawn gets an extra argument", "player", [(INV, '                                    argv=("supervise", f"{runtime_dir}/{rt.SUPERVISOR_REQUEST}"),',
                                                               '                                    argv=("supervise", f"{runtime_dir}/{rt.SUPERVISOR_REQUEST}", "--verbose"),')]),
    ("the helper environment is widened", "player", [(INV, 'proc.EnvironmentPolicy(inherit=("HOME", "TMPDIR", "LANG"),', 'proc.EnvironmentPolicy(inherit=("HOME", "TMPDIR", "LANG", "TZ"),')]),
    # --- capture declarations and the LaunchServices trust boundary
    ("capture marked READ_ONLY", "player", [(AD, '"1920 px, never upscaled) through the helper.",\n                 "MUTATING", "SESSION_REQUIRED"',
                                             '"1920 px, never upscaled) through the helper.",\n                 "READ_ONLY", "SESSION_REQUIRED"')]),
    ("capture without a single writer", "player", [(AD, 'single_writer_required=operation_class == "MUTATING", resource_kind=c.RESOURCE_KIND,',
                                                     'single_writer_required=operation_class == "MUTATING" and cap_id not in (c.SCREENSHOT, c.VIDEO), resource_kind=c.RESOURCE_KIND,')]),
    ("capture skips the helper EXACT check before LaunchServices", "player", [(AD, "        bundle = self._helper_exact()\n        directory, binding, why = self._binding(root, session)",
                                                                              "        bundle = self.helper_bundle()\n        directory, binding, why = self._binding(root, session)")]),
    ("capture skips the helper EXACT check after LaunchServices", "player", [(AD, "        if hp.classify(bundle)[0] != hp.EXACT:\n            return fail(",
                                                                             "        if False:\n            return fail(")]),
    ("the helper result's request nonce is not checked", "player", [(AD, '        if (r["mode"], r["request_id"], r["request_nonce"], r["session_id"]) != (kind, request_id, nonce, session_id):',
                                                                     '        if (r["mode"], r["request_id"], r["session_id"]) != (kind, request_id, session_id):')]),
    ("the helper's reported CDHash is not checked", "player", [(AD, '                and h.get("cdhash") in hp.cdhashes().values()):', "                and True):")]),
    ("the helper's version is not checked", "player", [(AD, 'h.get("version") == hp.version()', "True")]),
    ("a permission refusal after ScreenCaptureKit is accepted", "player", [(AD, '        if result.get("sck_called") is not False:', "        if False:")]),
    ("capture skips build revalidation", "player", [(AD, '        if trust != c.VALID:\n            raise Refused("CAPTURE_BUILD_DRIFT", f"nothing is captured: {drift}")',
                                                     '        if False:\n            raise Refused("CAPTURE_BUILD_DRIFT", f"nothing is captured: {drift}")')]),
    ("capture skips the Player identity", "player", [(AD, '        if identity != c.PROVEN:\n            raise Refused("PLAYER_RUNTIME_GONE" if identity == c.GONE',
                                                      '        if False:\n            raise Refused("PLAYER_RUNTIME_GONE" if identity == c.GONE')]),
    ("capture skips the window pre-check", "player", [(AD, "        if len(windows) != 1:\n            raise Refused(", "        if False:\n            raise Refused(")]),
    ("the helper's window proof is not checked", "player", [(AD, '            _window_proof(result, p["pid"])\n', "")]),
    ("the restated build is not checked", "player", [(AD, '        if request.build_id is not None and request.build_id != session["build_id"]:', "        if False:")]),
    ("video duration bounds widened", "player", [(AD, "not c.MIN_VIDEO_SECONDS <= duration <= c.MAX_VIDEO_SECONDS", "not 0 <= duration <= 16")]),
    ("a fractional or boolean duration is accepted", "player", [(AD, "    if isinstance(value, bool):\n        return None\n    if isinstance(value, int):\n        return value",
                                                               "    if isinstance(value, (int, float)):\n        return int(value)")]),
    ("the MP4 walk is skipped", "player", [(AD, "            facts = (cap_media.validate_mp4(workspace / name, duration, media[\"width\"], media[\"height\"]) if video else",
                                            "            facts = (dict(media) if video else")]),
    ("a sound track is accepted", "player", [(CP, '    if len(traks) != 1:\n        raise MediaProblem(f"the MP4 has {len(traks)} tracks',
                                              '    if len(traks) < 1:\n        raise MediaProblem(f"the MP4 has {len(traks)} tracks')]),
    ("the 1920 px bound is not enforced", "player", [(CP, "    if (w, h) != (width, height) or max(w, h) > max_edge or min(w, h) < 1:",
                                                      "    if (w, h) != (width, height) or min(w, h) < 1:")]),
    ("an unfinished MP4 (no moov) is accepted", "player", [(CP, '    if len(moovs) != 1 or not mdats:\n        raise MediaProblem("the MP4 has no single complete moov',
                                                            '    if len(moovs) > 1:\n        raise MediaProblem("the MP4 has no single complete moov')]),
    ("evidence claims TARGET_RUNTIME", "player", [(AD, '            evidence_type="MOTION_EVIDENCE" if video else "VISUAL_EVIDENCE", capture_context="DIAGNOSTIC_RUNTIME",',
                                                   '            evidence_type="MOTION_EVIDENCE" if video else "VISUAL_EVIDENCE", capture_context="TARGET_RUNTIME",')]),
    ("the capture record is dropped from the candidate", "player", [(AD, '            artifact_ids=(media_id, "capture-record"),', "            artifact_ids=(media_id,),")]),
    ("a caller output_dir is accepted", "player", [(AD, "lease_mode=lease_mode, requires_project=True, caller_output_dir_allowed=False, timeout=timeout,",
                                                    "lease_mode=lease_mode, requires_project=True, caller_output_dir_allowed=True, timeout=timeout,")]),
    # --- launch
    ("the foreign scan is disabled", "player", [(AD, "        if pids:\n            raise Refused(\"PLAYER_RUNTIME_CONFLICT\"", "        if False:\n            raise Refused(\"PLAYER_RUNTIME_CONFLICT\"")]),
    ("the AppKit cross-check is dropped", "player", [(AD, "        pids = set(found) | set(self.appkit.running_pids(application_id))", "        pids = set(found)")]),
    ("the post-handshake rescan is disabled", "player", [(AD, "        if pids - {p[\"pid\"]}:", "        if False:")]),
    ("the handshake nonce is not checked", "player", [(RT, '    if (d["schema"], d["session_id"], d["nonce"]) != ("gpos.player.handshake/1", session_id, nonce):',
                                                       '    if (d["schema"], d["session_id"]) != ("gpos.player.handshake/1", session_id):')]),
    ("the Player need not be the supervisor's child", "player", [(AD, '        if p["ppid"] != s["pid"] or p["pgid"] != p["pid"] or p["executable"] != target.executable:',
                                                                  '        if p["executable"] != target.executable:')]),
    ("the supervisor's code hash is not checked at launch", "player", [(AD, "        if kernel_cdhash is None or kernel_cdhash != s[\"cdhash\"] or kernel_cdhash not in hp.cdhashes().values():",
                                                                        "        if kernel_cdhash is None:")]),
    ("the launch skips revalidation after the handshake", "player", [(AD, "        if trust != c.VALID:\n            raise Refused(\"PLAYER_BUILD_INVALID\", f\"the build no longer revalidates after the launch: {why}\")",
                                                                      "        if False:\n            raise Refused(\"PLAYER_BUILD_INVALID\", f\"the build no longer revalidates after the launch: {why}\")")]),
    ("SUCCESS without a commit", "player", [(AD, '        rt.write_once(runtime_dir / rt.COMMIT, rt.control(None, "gpos.player.commit/1", session_id, nonce))\n', "")]),
    ("the runtime binding may be overwritten", "player", [(RT, "        os.link(temporary, path)\n    finally:\n        os.unlink(temporary)",
                                                           "        os.replace(temporary, path)\n    finally:\n        pass")]),
    ("the runtime binding is not bound to the session", "player", [(RT, '    for key in ("session_id", "nonce", "build_id", "manifest_sha256"):', "    for key in ():")]),
    ("a missing runtime binding is treated as bound at stop", "player", [(AD, "        if binding is None:\n            return self._stop_unresolved(",
                                                                          "        if False:\n            return self._stop_unresolved(")]),
    # --- status
    ("status signals the Player", "player", [(AD, "            identity = self._identity(binding)\n            windows = self.os.eligible_windows",
                                              "            identity = self._identity(binding)\n            self.appkit.request_terminate(binding['player']['pid'], session['application_id'], binding['player']['executable'])\n            windows = self.os.eligible_windows")]),
    # --- stop
    ("stop requires the helper tool", "player", [(AD, '                 "MUTATING", "SESSION_CLOSE", TimeoutPolicy(default=60.0, maximum=120.0), requires_tool=False,',
                                                  '                 "MUTATING", "SESSION_CLOSE", TimeoutPolicy(default=60.0, maximum=120.0), requires_tool=True,')]),
    ("stop requires build revalidation", "player", [(AD, "                       cap)] if trust != c.VALID else []\n        if binding is None:",
                                                     "                       cap)] if trust != c.VALID else []\n        if trust != c.VALID:\n            raise Refused(\"CAPTURE_BUILD_DRIFT\", str(drift))\n        if binding is None:")]),
    ("the supervisor is proven by its path", "player", [(AD, '        if (f["start_sec"], f["start_usec"]) != (s["start_sec"], s["start_usec"]) or self.os.cdhash(s["pid"]) != s["cdhash"]:',
                                                         '        if (f["start_sec"], f["start_usec"], f["executable"]) != (s["start_sec"], s["start_usec"], s["executable"]) or self.os.cdhash(s["pid"]) != s["cdhash"]:')]),
    ("the supervisor's code hash is not checked at stop", "player", [(AD, " or self.os.cdhash(s[\"pid\"]) != s[\"cdhash\"]:\n            return c.NOT_THIS_PROCESS\n        return c.ALIVE",
                                                                      ":\n            return c.NOT_THIS_PROCESS\n        return c.ALIVE")]),
    ("the fallback forces instead of a graceful request", "player", [(AD, '                _, accepted = self.appkit.request_terminate(p["pid"], session["application_id"], p["executable"])',
                                                                      '                accepted = self.os.kill_proven(p["pid"], p["start_sec"], p["start_usec"], p["executable"])')]),
    ("another owner may recover a live session", "player", [(AD, "        if not own and identity in (c.PROVEN, c.UNPROVEN):", "        if False:")]),
    ("an unproven live Player is closed as gone", "player", [(AD, "    if identity in (c.PROVEN, c.UNPROVEN):   # alive", "    if identity == c.PROVEN:   # alive")]),
    ("SIGKILL without a GPOS kill is classified FORCED", "player", [(AD, '        if stop["kill_sent"] or (exit_rec["status"] == "SIGNALED" and exit_rec["signal"] == 9 and kill_sent):',
                                                                     '        if stop["kill_sent"] or exit_rec["signal"] == 9:')]),
    ("log evidence without build trust", "player", [(AD, "        if text is not None and observed and trust == c.VALID and request.build_id is not None \\",
                                                     "        if text is not None and observed and request.build_id is not None \\")]),
    # --- install
    ("UNTRUSTED content is overwritten", "player", [(HP, "    if state == UNTRUSTED:\n        raise InstallConflict(",
                                                     "    if state == UNTRUSTED:\n        shutil.rmtree(destination)\n    if False:\n        raise InstallConflict(")]),
    ("an ordinary rename publishes the helper", "player", [(HP, '    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)\n    lib.renamex_np.argtypes',
                                                            '    return os.rename(source, destination)\n    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)\n    lib.renamex_np.argtypes')]),
    ("a raced-in destination counts as success", "player", [(HP, "            if raced != EXACT:\n                raise InstallConflict(", "            if False:\n                raise InstallConflict(")]),
    ("the staged copy is not verified", "player", [(HP, '        if classify(staged)[0] != EXACT:\n            raise InstallConflict("the staged helper did not verify")\n', "")]),
    ("the install takes a caller destination", "player", [(AD, "        host = context.host_location\n        if host is None or Path(host) != Path(self._host()):\n            raise AssertionError(\"the install location must be the foundation-resolved host location\")\n        if request.inputs:\n            raise Refused(\"INVALID_TOOL_REQUEST\", \"the helper install takes no input\")",
                                                           "        host = (request.inputs or {}).get(\"destination\", context.host_location)"),
                                                          (AD, '        timeout=TimeoutPolicy(default=60.0, maximum=300.0), side_effect_scope=SIDE_EFFECTS[c.INSTALL],',
                                                           '        timeout=TimeoutPolicy(default=60.0, maximum=300.0), side_effect_scope=SIDE_EFFECTS[c.INSTALL], input_kinds=("destination",),')]),
    # --- the log sanitizer
    ("the space-aware PLAYER_PATH rule is removed", "player", [(LG, '    line = PLAYER_PATH.sub("<path>", line)\n', "")]),
    ("credential redaction is skipped", "player", [(LG, "    line, _ = redact(line)\n", "")]),
    ("the ABSOLUTE_PATH defence in depth is removed", "player", [(LG, '    line = ABSOLUTE_PATH.sub("<path>", line)\n', "")]),
    ("lines are not clipped", "player", [(LG, "    return line[:MAX_LINE_CHARS]", "    return line")]),
    ("the home directory is not normalized", "player", [(LG, '        pairs.append((spelling.rstrip("/"), "~"))', "        pass")]),
    # --- the helper source (static pins, no rebuild)
    ("a standalone preflight mode is reintroduced", "player", [(SW_MAIN, 'case "shot", "video": runCapture(args[1], args[2])',
                                                                'case "shot", "video": runCapture(args[1], args[2])\ncase "preflight": print(CGPreflightScreenCaptureAccess()); exit(0)')]),
    ("the capture modes request permission", "player", [(SW_CAP, '    guard CGPreflightScreenCaptureAccess() else { box.finish("REFUSED", "PERMISSION_NOT_GRANTED") }',
                                                         '    guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else { box.finish("REFUSED", "PERMISSION_NOT_GRANTED") }')]),
    ("the preflight moves after ScreenCaptureKit", "player", [(SW_CAP, '    guard CGPreflightScreenCaptureAccess() else { box.finish("REFUSED", "PERMISSION_NOT_GRANTED") }\n', ""),
                                                              (SW_CAP, "            box.set(\"sck_called\", true)\n            let (window, count) = try await exactWindow(r)\n",
                                                               "            box.set(\"sck_called\", true)\n            let (window, count) = try await exactWindow(r)\n            guard CGPreflightScreenCaptureAccess() else { box.finish(\"REFUSED\", \"PERMISSION_NOT_GRANTED\") }\n")]),
    ("the capture records audio", "player", [(SW_CAP, "cfg.capturesAudio = false", "cfg.capturesAudio = true")]),
    # --- the in-process macOS backend (real processes)
    ("the foreign scan matches by name", "helper", [(MC, "        if bundle_identifier_of(f[\"executable\"]) == application_id:",
                                                     "        if os.path.basename(f[\"executable\"]) == application_id or Path(f[\"executable\"]).name == \"Stub\":")]),
    ("identity ignores the start time", "helper", [(MC, '    if (f["start_sec"], f["start_usec"]) != (start_sec, start_usec):\n        return c.NOT_THIS_PROCESS',
                                                    '    if False:\n        return c.NOT_THIS_PROCESS')]),
    ("the kill is not proven first", "helper", [(MC, "    if identity(pid, start_sec, start_usec, executable) != c.PROVEN:\n        return False\n    try:",
                                                 "    try:")]),
    ("the AppKit request ignores the bundle and executable", "helper", [(AK, "        if bundle != application_id or path is None or os.path.realpath(path) != os.path.realpath(executable):",
                                                                         "        if False:")]),
    ("windows of any layer are eligible", "helper", [(MC, '    return [w for w in windows(pid) if w["layer"] == 0 and w["on_screen"] and (w["alpha"] or 0) > 0]',
                                                      '    return [w for w in windows(pid) if w["on_screen"]]')]),
]


def apply(copy, edits):
    for rel, anchor, replacement in edits:
        path = copy / rel
        text = path.read_text()
        if text.count(anchor) != 1:
            return f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
        path.write_text(text.replace(anchor, replacement))
    return None


def run(mutation):
    name, suite, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-playermut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        problem = apply(copy, edits)
        if problem:
            return name, problem
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        test, groups = SUITES[suite]
        try:
            out = subprocess.run([sys.executable, "-B", str(copy / "tests" / test)] + groups, capture_output=True,
                                 text=True, timeout=1800, env=env)
        except subprocess.TimeoutExpired:
            return name, "CAUGHT"   # a hang is a failure of the suite
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def anchors():
    problems = []
    for name, _, edits in MUTATIONS:
        for rel, anchor, _ in edits:
            n = (ROOT / rel).read_text().count(anchor)
            if n != 1:
                problems.append(f"{name}: {rel} anchor found {n} times")
    print("\n".join(problems) or f"all anchors of {len(MUTATIONS)} mutations apply exactly once")
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    parser.add_argument("--anchors", action="store_true", help="only check that every anchor applies exactly once")
    args = parser.parse_args()
    if args.anchors:
        return anchors()
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
