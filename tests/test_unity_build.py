#!/usr/bin/env python3
"""Phase 2C-7 (alpha.21) — Unity Build Core: unity.inspect-build-configuration and unity.build-player.

    python3 tests/test_unity_build.py                      # fast groups and the real Unity groups
    GPOS_UNITY_TEST_FAST=1 python3 tests/test_unity_build.py   # fast groups only

Fast groups (no Unity process; a stand-in Editor under a temporary Hub root plays the fixed build entry):
    A  the two capability declarations (Unity 45 -> 47) and the closed surface
    B  request rules: no build_id, a canonical build_revision, MACOS only, the token, the build request-id grammar
    C  the one fixed command: argv shape, the executeMethod string, nothing from a request reaches it
    D  the bridge release: 1.5.0 on protocol /5, history/1.4.0.json is the frozen alpha.20 manifest, the entry needs EXACT
    E  source scans: the batch-only build entry's allowlist, the frozen lifecycle, no Git and no directory artifact
    F  the stand-in matrix: every response, refusal, pre-entry failure, post-check and payload failure and how it is reported
    G  the payload tree digest: golden vectors, ordering, links, special files and every bound
    H  publication: staging -> payload -> manifest last, never over existing state, revalidation
    M  public text: relativized, redacted, path-free and clipped BuildReport text; no absolute outputPath leaves GPOS
    L  conflicts: a live session, a held project lock, the Editor version, a reused workspace

Real groups (Unity 6000.5.8f1 on macOS, lab-owned batch Editors only; a test-only build testkit plays the Human):
    RB1 classic: the three-Git-read qualified build, Development, a stale token, repeated builds, D-B, scenes,
        a non-active target (switched to WebGL by the testkit; never Android) and a killed build
    RB2 profile: an active macOS Build Profile, its Development and defines, D-A and D-B refusals
    RB3 failing tests still build; RB4 compile errors before the entry
    RQ  the alpha.20 source-error -> fix -> build composition, with a live-session and a Human-open-Editor conflict
No real test runs an Android build or touches the user's Gradle or Android state.
"""

import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_unity_authoring as ta  # noqa: E402
import test_unity_live as tl  # noqa: E402
import unity_fixture_builder as fixtures  # noqa: E402
from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import leases as lease_mod  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.unity import UnityAdapter  # noqa: E402
from gpos.tools.unity import adapter as ua  # noqa: E402
from gpos.tools.unity import bridge_install as bi  # noqa: E402
from gpos.tools.unity import build as ub  # noqa: E402
from gpos.tools.unity import live  # noqa: E402
from gpos.tools.unity import project_lock as pl  # noqa: E402
from gpos.tools.unity import sources as S  # noqa: E402

FW = load_framework()
FAST = os.environ.get("GPOS_UNITY_TEST_FAST") == "1"
EDITOR, EDITOR_VERSION = tl.EDITOR, tl.EDITOR_VERSION
STANDIN_VERSION = "6000.5.8f1"
FIXTURE = tl.FIXTURE
BUILD_TESTKIT = ROOT / "tests" / "unity_build_testkit" / "com.gpos.build-testkit"
ENTRY = bi.SOURCE / "Editor" / "Build"
REV = "0123456789abcdef0123456789abcdef01234567"
FROZEN_TAG_14 = "v1.0.0-alpha.20"
FROZEN_DIGEST_14 = "90dedd4089e602728c3402557b232fcb8a865a423a0a3f11e52e02e3c6c88422"
GITIGNORE = ("Game/Library/\nGame/Temp/\nGame/Logs/\nGame/UserSettings/\nGame/obj/\n.game/gpos-runtime/\n*.csproj\n*.sln\n"
             "Game/*.csproj\n")


def configuration(**over):
    c = {"schema": ub.CONFIG_SCHEMA, "unity_version": STANDIN_VERSION, "active_target": "StandaloneOSX",
         "standalone_subtarget": "Player", "scripting_backend": "Mono2x", "application_identifier": "com.DefaultCompany.Game",
         "development": False, "mode": "CLASSIC",
         "scenes": [{"path": "Assets/Scenes/Build.unity", "guid": "0123456789abcdef0123456789abcdef", "sha256": "ab" * 32}],
         "files": {"Packages/manifest.json": "ab" * 32, "Packages/packages-lock.json": "ABSENT",
                   "ProjectSettings/EditorBuildSettings.asset": "ab" * 32, "ProjectSettings/ProjectSettings.asset": "ab" * 32},
         "debug": {"allow_debugging": False, "code_coverage": False, "connect_profiler": False, "deep_profiling": False,
                   "wait_for_managed_debugger": False, "wait_for_player_connection": False},
         "output": {"architecture": "x64ARM64", "create_xcode_project": False, "install_in_build_folder": False},
         "profile": None}
    c.update(over)
    return c


PROFILE = {"path": "Assets/Settings/Build Profiles/Mac.asset", "guid": "fedcba9876543210fedcba9876543210", "sha256": "cd" * 32,
           "readable": True, "build_target": 2, "platform_id": "0d2129357eac403d8b359c2dcbf82502", "subtarget": 2,
           "override_global_scenes": False, "scripting_defines": [], "player_settings_overrides": 0, "development": False}

# ---------------------------------------------------------------- the stand-in Editor

FAKE = r'''
import hashlib, json, os, plistlib, stat, sys, time
cfg = json.load(open(CONFIG))
argv = sys.argv[1:]
if argv == ["-version"]:
    sys.stdout.write(VERSION + "\n"); sys.exit(0)
runs = json.load(open(RECORD)) if os.path.exists(RECORD) else []
runs.append({"argv": argv, "env": {k: os.environ.get(k) for k in ("UPM_USER_CONFIG_FILE", "UPM_GLOBAL_CONFIG_FILE",
             "UPM_CACHE_ROOT")}, "cwd": os.getcwd()})
json.dump(runs, open(RECORD, "w"))
opts = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("-")}
if "log" in cfg:
    open(opts["-logFile"], "w").write(cfg["log"])
reqpath = opts.get("-gposBuildRequest")
req = json.load(open(reqpath)) if reqpath and os.path.exists(reqpath) else None
ws = os.path.dirname(reqpath) if reqpath else None

def canonical(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

def write(name, obj):
    p = os.path.join(ws, name)
    with open(p + ".tmp", "w") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))
    os.rename(p + ".tmp", p)

def make_app(app, guid, conf):
    a = cfg.get("app", {})
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    os.makedirs(os.path.join(app, "Contents", "Resources", "Data"))
    os.makedirs(os.path.join(app, "Contents", "Frameworks", "X.framework", "Versions", "A"))
    if not a.get("no_plist"):
        with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleIdentifier": a.get("bundle_id", conf["application_identifier"]),
                           "CFBundleExecutable": a.get("exe", "Game"), "CFBundleShortVersionString": "1.0"}, f)
    exe = os.path.join(app, "Contents", "MacOS", "Game")
    open(exe, "wb").write(b"\xcf\xfa\xed\xfe binary")
    os.chmod(exe, a.get("exe_mode", 0o755))
    open(os.path.join(app, "Contents", "Resources", "Data", "boot.config"), "w").write(
        "gfx-enable-gfx-jobs=1\nbuild-guid=" + a.get("boot_guid", guid) + "\n")
    open(os.path.join(app, "Contents", "Resources", "Data", "level0"), "wb").write(b"level" * 100)
    open(os.path.join(app, "Contents", "Frameworks", "X.framework", "Versions", "A", "X"), "wb").write(b"lib")
    os.symlink("A", os.path.join(app, "Contents", "Frameworks", "X.framework", "Versions", "Current"))
    if a.get("escaping_link"):
        os.symlink("../../../../outside", os.path.join(app, "Contents", "Escape"))
    if a.get("absolute_link"):
        os.symlink("/etc/hosts", os.path.join(app, "Contents", "Hosts"))
    if a.get("fifo"):
        os.mkfifo(os.path.join(app, "Contents", "Pipe"))

if req is not None and cfg.get("answer", True):
    conf = dict(cfg["configuration"])
    problems = cfg.get("problems", [])
    token = cfg.get("token") or hashlib.sha256(canonical(conf).encode("ascii")).hexdigest()
    r = {"schema": "gpos.unity.build-response/1", "operation": req["operation"], "request_id": req["request_id"],
         "unity_version": VERSION, "outcome": "INSPECTED", "buildable": not problems, "problems": problems,
         "configuration": conf, "configuration_token": token, "refusal": None, "build": None, "post": None}
    if req["operation"] == "BUILD":
        if cfg.get("refuse"):
            r["outcome"] = "REFUSED"
            r["refusal"] = {"rule": cfg["refuse"], "message": "refused by the stand-in"}
            if cfg.get("started_anyway"):
                write("build-started.json", {"utc": "x"})
        else:
            if cfg.get("started", True):
                write("build-started.json", {"schema": "gpos.unity.build-started/1", "request_id": req["request_id"],
                                             "build_id": "build-" + req["request_id"], "utc": "2026-09-29T12:00:00.0000000Z"})
            time.sleep(cfg.get("sleep_after_start", 0))
            guid = cfg.get("guid", "5d473f7636a9422a89bac4a6c737bd5b")
            app = os.path.join(ws, "staging", "Player.app")
            if cfg.get("write_app", True):
                make_app(app, guid, conf)
            for extra in cfg.get("staging_extra", []):
                open(os.path.join(ws, "staging", extra), "w").write("x")
            b = {"result": "Succeeded", "guid": guid, "platform": "StandaloneOSX", "output_path": app, "total_errors": 0,
                 "total_warnings": 1, "total_size": 1234, "duration_ms": 9800, "development_observed": conf["development"],
                 "options_text": "None", "messages": [], "error_message_count": 0}
            b.update(cfg.get("build", {}))
            post = {"configuration_token": token, "active_target": conf["active_target"],
                    "profile_path": (conf["profile"] or {}).get("path"), "development": conf["development"]}
            post.update(cfg.get("post", {}))
            r.update(outcome="BUILT", build=None if cfg.get("no_report") else b, post=post)
    r.update(cfg.get("override", {}))
    if cfg.get("raw_response") is not None:
        write("build-response.json", cfg["raw_response"])
    elif cfg.get("respond", True):
        write("build-response.json", r)
time.sleep(cfg.get("sleep", 0))
sys.stdout.write(cfg.get("stdout", ""))
sys.exit(cfg.get("exit", 0))
'''


class Fake:
    def __init__(self, directory, **config):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.hub = directory / "Hub"
        macos = self.hub / STANDIN_VERSION / "Unity.app" / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        self.config, self.record = directory / "config.json", directory / "record.json"
        config.setdefault("configuration", configuration())
        self.config.write_text(json.dumps(config))
        exe = macos / "Unity"
        exe.write_text(f"#!{tproc.interpreter_path()}\nCONFIG = {str(self.config)!r}\nRECORD = {str(self.record)!r}\n"
                       f"VERSION = {STANDIN_VERSION!r}\n{FAKE}")
        exe.chmod(0o755)

    def registry(self, **kwargs):
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(UnityAdapter(hub_roots=[str(self.hub)], platform="darwin", **kwargs))
        return registry

    def runs(self):
        return json.loads(self.record.read_text()) if self.record.exists() else []


def token_of(conf):
    return ub.token_of(conf)


def codes(result):
    return {d.code for d in result.diagnostics}


class BuildCase(unittest.TestCase):
    install = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-build-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = self.tmp / "p"
        shutil.copytree(FIXTURE, self.p)
        self.game = self.p / "Game"
        fixtures.make_project(self.game, "pass", STANDIN_VERSION, EDITOR)
        if self.install:
            bi.install(self.p, self.game, bi.verify_source())
        self.n = 0

    def fake(self, **config):
        self.n += 1
        return Fake(self.tmp / f"fake{self.n}", **config)

    def run_cap(self, cap, fake=None, request_id=None, inputs=None, **kw):
        fake = fake or self.fake()
        if inputs is None:
            inputs = {"unity_project": "Game"}
            if cap == ub.BUILD:
                inputs["expected_configuration_token"] = token_of(configuration())
        kw.setdefault("allow_mutation", not kw.get("dry_run", False))
        if cap == ub.BUILD:
            kw.setdefault("build_revision", REV)
        request = ExecutionRequest(adapter_id="unity", capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", "rev-1"),
                                   project_root=str(self.p), inputs=inputs, request_id=request_id, **kw)
        return execute(fake.registry(), request)

    def workspace(self, result):
        return self.p / ".game" / "gpos-runtime" / "tool-output" / "unity" / result.request_id

    def assertStatus(self, result, status, *expected):
        text = " | ".join(f"{d.code}: {d.message}" for d in result.diagnostics)
        self.assertEqual(result.status, status, text)
        for code in expected:
            self.assertIn(code, codes(result), text)


# ---------------------------------------------------------------- A  declarations

class A_Declarations(unittest.TestCase):
    def test_two_build_capabilities_and_forty_seven_in_all(self):
        caps = {c.id: c for c in ua.DESCRIPTOR.capabilities}
        self.assertEqual(len(caps), 47)
        self.assertEqual(ub.CAPABILITY_IDS, ("unity.inspect-build-configuration", "unity.build-player"))
        for cid, category, inputs, kinds, timeout in (
                (ub.INSPECT_BUILD, "INSPECT", ("unity_project",), ("LOG",), (600.0, 1800.0)),
                (ub.BUILD, "BUILD", ("unity_project", "expected_configuration_token"), ("REPORT", "LOG"), (1800.0, 3600.0))):
            c = caps[cid]
            self.assertEqual((c.category, c.operation_class, c.state_model, c.execution_context, c.effective_lease_mode,
                              c.single_writer_required, c.resource_kind, c.requires_tool, c.requires_project,
                              c.dry_run_supported, c.input_kinds, c.artifact_kinds, c.potential_evidence,
                              (c.timeout.default, c.timeout.maximum)),
                             (category, "MUTATING", "STATELESS", "EDITOR", "EXECUTION", True, "EDITOR_PROJECT", True, True,
                              True, inputs, kinds, (), timeout))
            self.assertIn("never a target switch", " ".join(c.notes))
        self.assertIn("MUTATING only because opening Unity", caps[ub.INSPECT_BUILD].description)
        self.assertIn("Git is never run", " ".join(caps[ub.BUILD].notes))

    def test_no_generic_surface(self):
        for cid in ub.CAPABILITY_IDS:
            for word in ("execute", "method", "script", "launch", "switch", "target", "profile", "deploy", "run-", "install"):
                self.assertNotIn(word, cid)
        for cid in ub.CAPABILITY_IDS:
            c = ua.DESCRIPTOR.capability(cid)
            for word in ("development", "target", "output", "scene", "option", "define", "build_id", "method", "argv"):
                self.assertNotIn(word, c.input_kinds)

    def test_the_descriptor_discloses_the_build_plane(self):
        notes = " ".join(ua.DESCRIPTOR.compatibility_notes)
        self.assertIn("Build Core (bridge 1.5.0, protocol unchanged)", notes)
        self.assertIn("never an artifact", notes)


# ---------------------------------------------------------------- B  request rules

class B_Requests(BuildCase):
    def test_a_build_takes_no_build_id(self):
        for cap in ub.CAPABILITY_IDS:
            fake = self.fake()
            r = self.run_cap(cap, fake, build_id="build-mine")
            self.assertStatus(r, tdg.INVALID_REQUEST, "INVALID_TOOL_REQUEST")
            self.assertIn("meaningless for creation", " ".join(d.message for d in r.diagnostics))
            self.assertEqual(fake.runs(), [])

    def test_a_build_needs_a_canonical_revision(self):
        for revision in (None, "HEAD", "abc", REV.upper(), REV + "0", " " + REV, "rev-1"):
            with self.subTest(revision=revision):
                fake = self.fake()
                r = self.run_cap(ub.BUILD, fake, build_revision=revision)
                self.assertEqual(r.status, tdg.INVALID_REQUEST, revision)
                self.assertEqual(fake.runs(), [])
        self.assertEqual(self.run_cap(ub.BUILD, build_revision="f" * 64).status, tdg.SUCCESS)   # SHA-256 object form

    def test_macos_only_and_a_token(self):
        fake = self.fake()
        for token in (None, "", "ab" * 31, ("ab" * 32).upper(), 7):
            inputs = {"unity_project": "Game"}
            if token is not None:
                inputs["expected_configuration_token"] = token
            r = self.run_cap(ub.BUILD, fake, inputs=inputs)
            self.assertEqual(r.status, tdg.INVALID_REQUEST, token)
        self.assertEqual(fake.runs(), [])
        self.assertEqual(self.run_cap(ub.BUILD, target_platform="MACOS").status, tdg.SUCCESS)

    def test_a_caller_output_dir_is_refused_before_anything_runs(self):
        """alpha.21 pre-freeze M1: the build workspace is foundation-owned; output_dir is refused in request validation."""
        proofs = []

        def proof(project, editor):
            proofs.append(project)
            return pl.assess(project, editor)
        for cap in ub.CAPABILITY_IDS:
            self.assertIs(ua.DESCRIPTOR.capability(cap).caller_output_dir_allowed, False)
            self.assertIs(ua.DESCRIPTOR.capability(cap).to_dict()["caller_output_dir_allowed"], False)
            for dry in (False, True):
                with self.subTest(cap=cap, dry_run=dry):
                    target = self.game / "Assets" / "UnexpectedWorkspace"
                    fake = self.fake()
                    registry = ToolRegistry(FW, allow_test_only=False)
                    registry.register(UnityAdapter(hub_roots=[str(fake.hub)], platform="darwin", lock_proof=proof))
                    inputs = {"unity_project": "Game"}
                    if cap == ub.BUILD:
                        inputs["expected_configuration_token"] = token_of(configuration())
                    r = execute(registry, ExecutionRequest(
                        adapter_id="unity", capability_id=cap, subject=Subject("FEATURE", "FEATURE-0001", "rev-1"),
                        project_root=str(self.p), inputs=inputs, output_dir=str(target), dry_run=dry,
                        allow_mutation=not dry, build_revision=REV if cap == ub.BUILD else None))
                    self.assertStatus(r, tdg.INVALID_REQUEST, "INVALID_TOOL_REQUEST")
                    self.assertIn("takes no output_dir", " ".join(d.message for d in r.diagnostics))
                    self.assertFalse(target.exists())
                    self.assertFalse(r.mutation_performed)
                    self.assertEqual(fake.runs(), [])                 # no Unity process, no build request
                    self.assertFalse((self.p / ".game" / "gpos-runtime" / "tool-output").exists())
        self.assertEqual(proofs, [])                                  # no lock proof either
        # other Unity capabilities keep the frozen default
        self.assertTrue(all(c.caller_output_dir_allowed for c in ua.DESCRIPTOR.capabilities if c.id not in ub.CAPABILITY_IDS))

    def test_both_capabilities_are_macos_only(self):
        for cap in ub.CAPABILITY_IDS:
            for platform in ("ANDROID", "WINDOWS", "LINUX", "IOS", "WEB"):
                with self.subTest(cap=cap, platform=platform):
                    fake = self.fake()
                    r = self.run_cap(cap, fake, target_platform=platform)
                    self.assertStatus(r, tdg.INVALID_REQUEST, "INVALID_TOOL_REQUEST")
                    self.assertIn("macOS-only", " ".join(d.message for d in r.diagnostics))
                    self.assertEqual(fake.runs(), [])
            for platform in (None, "MACOS"):
                self.assertEqual(self.run_cap(cap, target_platform=platform).status, tdg.SUCCESS, (cap, platform))

    def test_no_caller_authority_input_exists(self):
        fake = self.fake()
        for extra in ("development", "target", "output_path", "scenes", "method", "argv", "build_options", "build_id"):
            with self.subTest(extra=extra):
                inputs = {"unity_project": "Game", "expected_configuration_token": token_of(configuration()), extra: "x"}
                r = self.run_cap(ub.BUILD, fake, inputs=inputs)
                self.assertStatus(r, tdg.INVALID_REQUEST, "INVALID_TOOL_REQUEST")
        self.assertEqual(fake.runs(), [])

    def test_the_build_request_id_grammar(self):
        fake = self.fake()
        for rid in ("Req-1", "req.1", "req_1", "req-", "-req", "a" * 65):
            with self.subTest(rid=rid):
                r = self.run_cap(ub.BUILD, fake, request_id=rid)
                self.assertEqual(r.status, tdg.INVALID_REQUEST, rid)
        self.assertEqual(fake.runs(), [])
        ok = self.run_cap(ub.BUILD, request_id="a" * 64)
        self.assertStatus(ok, tdg.SUCCESS, "BUILD_PUBLISHED")
        self.assertEqual(ok.data["build_id"], "build-" + "a" * 64)
        # an inspection keeps the foundation's wider (still path-safe) grammar; an unsafe id never reaches a workspace
        self.assertEqual(self.run_cap(ub.INSPECT_BUILD, request_id="Req.2026_x").status, tdg.SUCCESS)
        fake = self.fake()
        self.assertEqual(self.run_cap(ub.INSPECT_BUILD, fake, request_id="../escape").status, tdg.INVALID_REQUEST)
        self.assertEqual(fake.runs(), [])
        self.assertFalse((self.p / ".game" / "gpos-runtime" / "tool-output" / "escape").exists())

    def test_consent_and_dry_run(self):
        fake = self.fake()
        for cap in ub.CAPABILITY_IDS:
            self.assertStatus(self.run_cap(cap, fake, allow_mutation=False), tdg.INVALID_REQUEST, "MUTATION_NOT_ALLOWED")
            dry = self.run_cap(cap, fake, dry_run=True, allow_mutation=False)
            self.assertStatus(dry, tdg.SUCCESS)
            self.assertTrue(dry.plan)
            self.assertIn(ub.BUILD_ENTRY_METHOD, dry.plan[0])
            self.assertFalse(dry.mutation_performed)
        self.assertEqual(fake.runs(), [])
        self.assertFalse((self.p / ".game" / "gpos-runtime" / "tool-output").exists())


# ---------------------------------------------------------------- C  the fixed command

class C_Command(BuildCase):
    def test_the_exact_argv(self):
        fake = self.fake()
        r = self.run_cap(ub.BUILD, fake)
        self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED")
        ws = self.workspace(r)
        argv = fake.runs()[0]["argv"]
        self.assertEqual(tuple(argv), (
            "-batchmode", "-projectPath", str(self.game), "-logFile", str(ws / "editor.log"), "-upmLogFile",
            str(ws / "upm.log"), "-cacheServerEnableDownload", "false", "-cacheServerEnableUpload", "false",
            "-executeMethod", "Gpos.LiveBridge.Build.BuildEntry.Run", "-gposBuildRequest", str(ws / "build-request.json")))
        self.assertEqual(argv.count("-executeMethod"), 1)
        for flag in ua.BUILD_NEVER:
            self.assertNotIn(flag, argv)
        env = fake.runs()[0]["env"]
        self.assertEqual(env["UPM_USER_CONFIG_FILE"], str(ws / "upm-user.toml"))
        self.assertEqual(r.provenance.command["argv"][-1], "<workspace>/build-request.json")
        self.assertEqual(r.provenance.command["argv"][2], "<unity-project>")

    def test_no_request_value_reaches_the_command(self):
        shapes = set()
        for i, (subject, token, rev) in enumerate(((("FEATURE", "FEATURE-0001"), "ab" * 32, REV),
                                                   (("FEATURE", "-executeMethod Evil.Run"), "cd" * 32, "f" * 64),
                                                   (("FEATURE", "a;b $(x)"), "ef" * 32, "1" * 40))):
            fake = self.fake(configuration=configuration(), token=token)
            request = ExecutionRequest(adapter_id="unity", capability_id=ub.BUILD, subject=Subject(*subject, "rev-1"),
                                       project_root=str(self.p), allow_mutation=True, build_revision=rev,
                                       inputs={"unity_project": "Game", "expected_configuration_token": token})
            r = execute(fake.registry(), request)
            argv = fake.runs()[0]["argv"]
            ws = str(self.workspace(r))
            shapes.add(tuple(a.replace(ws, "<ws>") for a in argv))
            for value in (token, rev, subject[1]):
                self.assertFalse(any(value in a for a in argv), value)
            self.assertEqual(json.loads((Path(ws) / "build-request.json").read_text())["expected_configuration_token"], token)
        self.assertEqual(len(shapes), 1)

    def test_the_method_is_one_constant(self):
        build_py = (ROOT / "gpos/tools/unity/build.py").read_text()
        adapter_py = (ROOT / "gpos/tools/unity/adapter.py").read_text()
        self.assertEqual(build_py.count('"Gpos.LiveBridge.Build.BuildEntry.Run"'), 1)
        self.assertEqual(build_py.count('"-executeMethod"'), 1)
        self.assertNotIn('"-executeMethod"', adapter_py.replace('NEVER = ("-quit", "-accept-apiupdate", "-noUpm", "-nographics", '
                                                                '"-executeMethod")', ""))
        body = build_py[build_py.index("def build_argv("):build_py.index("def unfresh(")]
        self.assertEqual(re.findall(r"def build_argv\((.*)\)", body), ["project, workspace"])
        entry = (ENTRY / "BuildEntry.cs").read_text()
        self.assertIn("namespace Gpos.LiveBridge.Build", entry)
        self.assertIn("public static class BuildEntry", entry)
        self.assertEqual(re.findall(r"public static \w+ (\w+)\(", entry), ["Run"])


# ---------------------------------------------------------------- D  the bridge release

class D_Release(BuildCase):
    install = False

    def test_1_5_0_keeps_protocol_5_and_pins_1_4_0(self):
        self.assertEqual((bi.BRIDGE_VERSION, bi.PROTOCOL), ("1.5.0", "gpos.unity.live/5"))
        frozen = ta.git("show", f"{FROZEN_TAG_14}:gpos/tools/unity/live_bridge/manifest.json", binary=True)
        self.assertEqual((bi.HISTORY / "1.4.0.json").read_bytes(), frozen)
        release = json.loads(frozen)
        self.assertEqual((release["bridge_version"], release["protocol"], release["package_digest"], len(release["files"])),
                         ("1.4.0", "gpos.unity.live/5", FROZEN_DIGEST_14, 60))
        self.assertEqual(bi.PREVIOUS["1.4.0"], ("gpos.unity.live/5", FROZEN_DIGEST_14))
        current = bi.verify_source()
        self.assertEqual((current["bridge_version"], current["protocol"], len(current["files"])), ("1.5.0", "gpos.unity.live/5", 67))
        added = {e["path"] for e in current["files"]} - {e["path"] for e in release["files"]}
        self.assertEqual(added, {"Editor/Build.meta", "Editor/Build/BuildEntry.cs", "Editor/Build/BuildEntry.cs.meta",
                                 "Editor/Build/BuildConfiguration.cs", "Editor/Build/BuildConfiguration.cs.meta",
                                 "Editor/Core/BuildRules.cs", "Editor/Core/BuildRules.cs.meta"})
        protocol = (bi.SOURCE / "Editor" / "Core" / "Protocol.cs").read_text()
        self.assertIn('public const string Name = "gpos.unity.live/5";', protocol)
        self.assertIn('public const string RequestSchema = "gpos.unity.live.request/5";', protocol)

    def test_the_live_lifecycle_is_frozen_apart_from_its_header(self):
        old = ta.git("show", f"{FROZEN_TAG_14}:gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/Bridge.cs")
        new = (bi.SOURCE / "Editor" / "Bridge.cs").read_text()
        strip = lambda text: [line for line in text.splitlines() if not line.startswith("//")]
        self.assertEqual(strip(old), strip(new))
        for name in ("Commands.cs", "Ipc.cs", "Identity.cs", "SourceSync.cs", "Compilation.cs"):
            self.assertEqual(ta.git("show", f"{FROZEN_TAG_14}:gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/{name}"),
                             (bi.SOURCE / "Editor" / name).read_text(), name)

    def test_an_exact_1_4_0_bridge_is_upgraded(self):
        target = self.game / "Packages" / bi.PACKAGE_ID
        ta.frozen_package(target, FROZEN_TAG_14)
        self.assertEqual(bi.installed_version(self.game), "1.4.0")
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.PREVIOUS_STATE, []))
        r = execute(default_registry(FW), ExecutionRequest(
            adapter_id="unity", capability_id=live.INSTALL, subject=Subject("FEATURE", "FEATURE-0001", "rev-1"),
            project_root=str(self.p), inputs={"unity_project": "Game"}, allow_mutation=True, actor=tl.APPROVER))
        self.assertStatus(r, tdg.SUCCESS, "LIVE_BRIDGE_UPGRADED")
        self.assertEqual(bi.inspect_target(self.game, bi.verify_source()), (bi.EXACT, []))

    def test_the_build_entry_needs_exactly_this_release(self):
        for state in ("absent", "1.4.0", "modified"):
            with self.subTest(state=state):
                target = self.game / "Packages" / bi.PACKAGE_ID
                shutil.rmtree(target, ignore_errors=True)
                if state == "1.4.0":
                    ta.frozen_package(target, FROZEN_TAG_14)
                elif state == "modified":
                    bi.install(self.p, self.game, bi.verify_source())
                    with open(target / "Editor" / "Build" / "BuildEntry.cs", "a") as fh:
                        fh.write("\n// changed\n")
                fake = self.fake()
                for cap in ub.CAPABILITY_IDS:
                    r = self.run_cap(cap, fake)
                    self.assertStatus(r, tdg.CONFLICT, "BUILD_ENTRY_UNAVAILABLE")
                self.assertEqual(fake.runs(), [])


# ---------------------------------------------------------------- E  source scans

def code_only(path):
    return re.sub(r"//[^\n]*", "", Path(path).read_text())


class E_Scans(unittest.TestCase):
    ENTRY_FILES = sorted(ENTRY.glob("*.cs"))

    def entry_code(self):
        return "\n".join(code_only(p) for p in self.ENTRY_FILES)

    def test_the_build_entry_allowlist(self):
        text = self.entry_code()
        self.assertEqual([p.name for p in self.ENTRY_FILES], ["BuildConfiguration.cs", "BuildEntry.cs"])
        self.assertEqual(text.count("BuildPipeline.BuildPlayer("), 2)
        self.assertEqual(text.count("new BuildPlayerWithProfileOptions"), 1)
        self.assertEqual(text.count("new BuildPlayerOptions"), 1)
        self.assertEqual(text.count("EditorApplication.Exit("), 1)
        self.assertEqual(text.count("Environment.GetCommandLineArgs()"), 1)
        self.assertEqual(text.count("File.Move("), 1)
        self.assertEqual(text.count("File.WriteAllText("), 1)
        # exactly one public type with exactly one public member: BuildEntry.Run(); the reader is internal
        self.assertEqual(re.findall(r"\bpublic\b[^\n(]*", text), ["public static class BuildEntry", "public static void Run"])
        self.assertIn("internal static class BuildConfiguration", text)
        for word in ("SwitchActiveBuildTarget", "SetActiveBuildProfile", "CreateBuildProfile", "PlayerSettings.Set",
                     "EditorBuildSettings.scenes =", "SetPlatformSettings", "ApplyModifiedProperties", "AssetDatabase.Refresh",
                     "SaveAssets", "ImportAsset", "DeleteAsset", "InitializeOnLoad", "MenuItem", "delayCall",
                     "System.Reflection", "GetMethod(", "Invoke(", "Assembly.Load", "Activator.", "Process.Start", "Socket",
                     "WebRequest", "HttpClient", "GetEnvironmentVariable", "Android", "iOS", "File.Delete", "Directory.Delete",
                     "Directory.CreateDirectory", "File.Copy", "EditorPrefs", "SessionState", "DllImport", "unsafe"):
            self.assertNotIn(word, text, word)
        self.assertEqual(re.findall(r"EditorUserBuildSettings\.\w+\s*=[^=]", text), [])   # never assigns a build setting
        self.assertEqual(re.findall(r"\.(?:boolValue|longValue|intValue|stringValue|arraySize)\s*=[^=]", text), [])
        run = code_only(ENTRY / "BuildEntry.cs")
        body = run[run.index("public static void Run()"):run.index("static int Execute()")]
        self.assertLess(body.index("if (AssetDatabase.IsAssetImportWorkerProcess() || !Application.isBatchMode) return;"),
                        body.index("EditorApplication.Exit("))

    def test_the_core_rules_stay_unity_free(self):
        text = code_only(bi.SOURCE / "Editor" / "Core" / "BuildRules.cs")
        for word in ("UnityEditor", "UnityEngine", "File.", "Directory.", "Process", "Reflection"):
            self.assertNotIn(word, text, word)

    def test_the_lifecycle_never_hosts_the_build(self):
        text = code_only(bi.SOURCE / "Editor" / "Bridge.cs")
        self.assertNotIn("Build", text.replace("BuildTarget", ""))
        for p in bi.SOURCE.rglob("*.cs"):
            if p.parent != ENTRY:
                self.assertNotIn("BuildEntry", code_only(p), p.name)
                self.assertNotIn("BuildPipeline", code_only(p), p.name)

    def test_the_unity_adapter_never_runs_git(self):
        for name in ("adapter.py", "build.py"):
            text = (ROOT / "gpos/tools/unity" / name).read_text()
            code = "\n".join(line.split("#")[0] for line in re.sub(r'"""[\s\S]*?"""', "", text).splitlines())
            for word in ('"git"', "'git'", ".git", "gpos.tools.git", "from ..git", "import git", "subprocess", "Popen",
                         '"git.resolve-provenance"', 'adapter_id="git"', "rev-parse"):
                self.assertNotIn(word, code, f"{name}: {word}")
        self.assertNotIn("git_verified", (ROOT / "gpos/tools/unity/adapter.py").read_text())   # the manifest writer
        build = (ROOT / "gpos/tools/unity/build.py").read_text()
        self.assertEqual(build.count("git_verified"), 1)                                       # only revalidate() refuses it
        self.assertIn('if "git_verified" in manifest:', build[build.index("def revalidate("):])

    def test_no_directory_is_ever_an_artifact(self):
        text = (ROOT / "gpos/tools/unity/adapter.py").read_text()
        build = text[text.index("    def _classify_build("):text.index("\n\n# ----------------------------------------------------------------")]
        specs = re.findall(r'ArtifactSpec\("([\w-]+)", "(\w+)", str\(([^)]*)\)', build)
        self.assertEqual(specs, [("editor-log", "LOG", "log_path"), ("build-manifest", "REPORT", "workspace / ub.MANIFEST_NAME")])

    def test_output_stays_in_the_workspace(self):
        text = (ROOT / "gpos/tools/unity/build.py").read_text()
        self.assertNotIn('"builds"', text)
        self.assertNotIn("runtime_dir", text)
        adapter = (ROOT / "gpos/tools/unity/adapter.py").read_text()
        self.assertNotIn('"builds"', adapter)
        entry = code_only(ENTRY / "BuildEntry.cs")
        self.assertIn('"tool-output", "unity", request.RequestId', entry)
        self.assertEqual(entry.count("Path.Combine(workspace, BuildRules.StagingName, BuildRules.PayloadName)"), 1)


# ---------------------------------------------------------------- F  the stand-in matrix

class F_Matrix(BuildCase):
    def build(self, **config):
        fake = self.fake(**config)
        return self.run_cap(ub.BUILD, fake), fake

    def assertNotPublished(self, r, quarantined=True):
        ws = self.workspace(r)
        self.assertFalse((ws / ub.MANIFEST_NAME).exists())
        self.assertFalse((ws / ub.PAYLOAD).exists())
        self.assertNotIn("build-manifest", [a.artifact_id for a in r.artifacts])
        if quarantined:
            self.assertIn("BUILD_QUARANTINED", codes(r))

    def test_inspection(self):
        r = self.run_cap(ub.INSPECT_BUILD)
        self.assertStatus(r, tdg.SUCCESS)
        self.assertEqual((r.data["buildable"], r.data["configuration_token"], r.data["mode"], r.data["development"]),
                         (True, token_of(configuration()), "CLASSIC", False))
        self.assertTrue(r.mutation_performed)
        self.assertEqual([a.artifact_id for a in r.artifacts], [])   # no log written by this stand-in
        problems = [{"rule": "DEBUG_STATE_UNSUPPORTED", "message": "m"}, {"rule": "NO_SCENES", "message": "n"}]
        r = self.run_cap(ub.INSPECT_BUILD, self.fake(problems=problems))
        self.assertStatus(r, tdg.SUCCESS, "BUILD_CONFIGURATION_NOT_BUILDABLE")
        self.assertFalse(r.data["buildable"])
        self.assertEqual(json.loads([d for d in r.diagnostics if d.code == "BUILD_CONFIGURATION_NOT_BUILDABLE"][0].details)["rules"],
                         ["DEBUG_STATE_UNSUPPORTED", "NO_SCENES"])

    def test_a_published_build(self):
        r, fake = self.build(log="Unity log\n")
        self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED")
        ws = self.workspace(r)
        self.assertEqual(sorted(os.listdir(ws)), sorted(["build-request.json", "build-started.json", "build-response.json",
                                                         "editor.log", "upm-user.toml", "upm-global.toml", "payload",
                                                         "build-manifest.json"]))
        self.assertEqual(os.listdir(ws / "payload"), ["Player.app"])
        self.assertEqual({(a.artifact_id, a.kind, a.media_type) for a in r.artifacts},
                         {("build-manifest", "REPORT", "application/json"), ("editor-log", "LOG", "text/plain")})
        m = json.loads((ws / ub.MANIFEST_NAME).read_text())
        self.assertEqual(m["build_id"], "build-" + r.request_id)
        self.assertEqual(m["request_id"], r.request_id)
        self.assertEqual((m["build_revision"], m["build_revision_source"]), (REV, "CALLER_SUPPLIED"))
        self.assertNotIn("git_verified", json.dumps(m))
        self.assertEqual((m["schema"], m["target"], m["development"], m["configuration_mode"], m["configuration_token"]),
                         (ub.MANIFEST_SCHEMA, "StandaloneOSX", False, "CLASSIC", token_of(configuration())))
        self.assertEqual(m["configuration"], configuration())
        self.assertEqual(m["scenes"], configuration()["scenes"])
        self.assertEqual(m["unity_build"]["guid"], "5d473f7636a9422a89bac4a6c737bd5b")
        self.assertEqual((m["build_entry"]["version"], m["build_entry"]["method"], m["build_entry"]["package_digest"]),
                         ("1.5.0", ub.BUILD_ENTRY_METHOD, bi.verify_source()["package_digest"]))
        self.assertEqual((m["payload"]["path"], m["payload"]["kind"], m["payload"]["bundle_identifier"],
                          m["payload"]["executable"], m["payload"]["bundle_version"]),
                         ("payload/Player.app", "MACOS_APP_BUNDLE", "com.DefaultCompany.Game", "Game", "1.0"))
        self.assertEqual(m["payload"]["tree_digest"], ub.payload_tree(ws / "payload" / "Player.app")["digest"])
        self.assertEqual(m["limitations"], list(ub.LIMITATIONS))
        self.assertIn("not an atomic repository lock", m["limitations"][0])
        self.assertEqual(ub.revalidate(ws), (m, None))
        self.assertEqual((r.data["build_id"], r.data["build_revision_source"], r.data["payload"]["path"]),
                         ("build-" + r.request_id, "CALLER_SUPPLIED", "payload/Player.app"))
        self.assertEqual(len(fake.runs()), 1)

    def test_refusals_map_to_closed_codes(self):
        for rule, status, code in (("TARGET_NOT_ACTIVE", tdg.CONFLICT, "BUILD_TARGET_NOT_ACTIVE"),
                                   ("TARGET_MODULE_MISSING", tdg.UNAVAILABLE, "BUILD_TARGET_MODULE_MISSING"),
                                   ("CONFIGURATION_CHANGED", tdg.CONFLICT, "BUILD_CONFIGURATION_CHANGED"),
                                   ("SCRIPTS_FAILED", tdg.FAILED, "BUILD_COMPILE_FAILED"),
                                   ("PROFILE_PLAYER_SETTINGS_OVERRIDE", tdg.CONFLICT, "BUILD_CONFIGURATION_UNSUPPORTED"),
                                   ("DEBUG_STATE_UNSUPPORTED", tdg.CONFLICT, "BUILD_CONFIGURATION_UNSUPPORTED"),
                                   ("NO_SCENES", tdg.CONFLICT, "BUILD_CONFIGURATION_UNSUPPORTED")):
            with self.subTest(rule=rule):
                r, _ = self.build(refuse=rule)
                self.assertStatus(r, status, code)
                self.assertIn(rule, " ".join(d.message for d in r.diagnostics))
                self.assertNotPublished(r, quarantined=False)
        r, _ = self.build(refuse="NO_SCENES", started_anyway=True)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "BUILD_OUTCOME_UNKNOWN")

    def test_pre_entry_failures_use_the_log_only_without_a_response(self):
        for log, status, code in (("x\nScripts have compiler errors.\n", tdg.FAILED, "BUILD_COMPILE_FAILED"),
                                  ("It looks like another Unity instance is running with this project open.\n",
                                   tdg.CONFLICT, "ENGINE_PROJECT_LOCKED"),
                                  ("No valid Unity Editor license found. Please activate your license.\n",
                                   tdg.UNAVAILABLE, "ENGINE_LICENSE_UNAVAILABLE"),
                                  ("something else\n", tdg.FAILED, "BUILD_ENTRY_FAILED")):
            for cap in ub.CAPABILITY_IDS:
                with self.subTest(code=code, cap=cap):
                    r = self.run_cap(cap, self.fake(answer=False, exit=1, log=log))
                    self.assertStatus(r, status, code)
                    self.assertNotIn("BUILD_OUTCOME_UNKNOWN", codes(r))
        # the log is never read once the entry answered
        r, _ = self.build(log="Scripts have compiler errors.\n")
        self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED")

    def test_a_started_build_without_a_trustworthy_answer_is_unknown(self):
        cases = ({"respond": False}, {"exit": 70}, {"raw_response": "{not json"}, {"override": {"request_id": "other"}},
                 {"override": {"extra": 1}}, {"token": "ab" * 32}, {"no_report": True},
                 {"override": {"configuration": configuration(development=True)}},   # token no longer matches
                 {"log": "Scripts have compiler errors.\n", "respond": False, "exit": 1})
        for config in cases:
            with self.subTest(config=config):
                r, fake = self.build(**config)
                self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "BUILD_OUTCOME_UNKNOWN")
                self.assertNotPublished(r)
                self.assertEqual(len(fake.runs()), 1)   # never retried

    def test_a_token_that_does_not_bind_its_configuration_is_never_trusted(self):
        """GPOS recomputes every token: an answer whose configuration is not what its (expected) token binds is refused."""
        forged = {"configuration": configuration(development=True), "token": token_of(configuration())}
        r, _ = self.build(**forged)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "BUILD_OUTCOME_UNKNOWN")
        self.assertIn("configuration token does not match", " ".join(d.message for d in r.diagnostics))
        self.assertNotPublished(r)
        r = self.run_cap(ub.INSPECT_BUILD, self.fake(**forged))
        self.assertStatus(r, tdg.FAILED, "BUILD_ENTRY_FAILED")
        self.assertNotIn("configuration_token", r.data)

    def test_timeouts(self):
        fake = self.fake(sleep_after_start=30)
        r = self.run_cap(ub.BUILD, fake, timeout=3)
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "BUILD_OUTCOME_UNKNOWN", "EXECUTION_TIMEOUT")
        self.assertNotPublished(r)
        fake = self.fake(answer=False, sleep=30)
        r = self.run_cap(ub.BUILD, fake, timeout=3)
        self.assertStatus(r, tdg.TIMED_OUT, "EXECUTION_TIMEOUT")
        self.assertNotIn("BUILD_OUTCOME_UNKNOWN", codes(r))
        self.assertEqual(len(fake.runs()), 1)

    def test_a_failed_build_is_quarantined(self):
        r, _ = self.build(build={"result": "Failed", "total_errors": 2,
                                 "messages": [{"step": "Verify Build setup", "type": "Error", "text": "bad"}]})
        self.assertStatus(r, tdg.FAILED, "BUILD_FAILED")
        self.assertNotPublished(r)
        self.assertEqual(r.data["build"]["messages"][0]["step"], "Verify Build setup")
        r, _ = self.build(build={"total_errors": 1})
        self.assertStatus(r, tdg.FAILED, "BUILD_FAILED")

    def test_post_build_checks(self):
        for config in ({"post": {"configuration_token": "cd" * 32}}, {"post": {"active_target": "WebGL"}},
                       {"post": {"profile_path": "Assets/P.asset"}}, {"post": {"development": True}},
                       {"build": {"development_observed": True}}, {"build": {"platform": "WebGL"}},
                       {"guid": "0" * 32}, {"guid": "XYZ"}, {"build": {"output_path": "/tmp/elsewhere/Player.app"}}):
            with self.subTest(config=config):
                r, _ = self.build(**config)
                self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "BUILD_OUTCOME_UNKNOWN")
                self.assertIn("post-build checks failed", " ".join(d.message for d in r.diagnostics))
                self.assertNotPublished(r)
        # the pre-build token is checked here too, not only in the entry
        fake = self.fake()
        r = self.run_cap(ub.BUILD, fake, inputs={"unity_project": "Game", "expected_configuration_token": "ee" * 32})
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "BUILD_OUTCOME_UNKNOWN")

    def test_development_is_observed_not_requested(self):
        dev = configuration(development=True)
        fake = self.fake(configuration=dev)
        r = self.run_cap(ub.BUILD, fake, inputs={"unity_project": "Game", "expected_configuration_token": token_of(dev)})
        self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED")
        m = json.loads((self.workspace(r) / ub.MANIFEST_NAME).read_text())
        self.assertEqual((m["development"], m["unity_build"]["development_observed"]), (True, True))
        prof = configuration(mode="PROFILE", profile=dict(PROFILE))
        r = self.run_cap(ub.BUILD, self.fake(configuration=prof),
                         inputs={"unity_project": "Game", "expected_configuration_token": token_of(prof)})
        self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED")
        m = json.loads((self.workspace(r) / ub.MANIFEST_NAME).read_text())
        self.assertEqual((m["configuration_mode"], m["build_profile"]), ("PROFILE", {k: PROFILE[k] for k in ("path", "guid", "sha256")}))

    def test_payload_failures_are_quarantined(self):
        for config in ({"staging_extra": ["Player_BurstDebugInformation"]}, {"app": {"no_plist": True}},
                       {"app": {"bundle_id": "com.other"}}, {"app": {"exe": "../Game"}}, {"app": {"exe": "Missing"}},
                       {"app": {"exe_mode": 0o644}}, {"app": {"boot_guid": "ffffffffffffffffffffffffffffffff"}},
                       {"app": {"escaping_link": True}}, {"app": {"absolute_link": True}}, {"app": {"fifo": True}},
                       {"write_app": False}):
            with self.subTest(config=config):
                r, _ = self.build(**config)
                self.assertStatus(r, tdg.FAILED, "BUILD_PAYLOAD_INVALID")
                self.assertNotPublished(r)
                self.assertFalse((self.workspace(r) / ub.PAYLOAD).exists())

    def test_repeated_builds_are_distinct(self):
        fake = self.fake()
        first, second = self.run_cap(ub.BUILD, fake), self.run_cap(ub.BUILD, fake)
        for r in (first, second):
            self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED")
        self.assertNotEqual(first.data["build_id"], second.data["build_id"])
        self.assertNotEqual(self.workspace(first), self.workspace(second))
        self.assertIsNone(ub.revalidate(self.workspace(first))[1])
        self.assertIsNone(ub.revalidate(self.workspace(second))[1])


# ---------------------------------------------------------------- M  public text and paths

SECRET = "super-secret-hostile-value"


class M_PublicText(BuildCase):
    """BuildReport and entry texts leave GPOS only relativized, redacted, path-free and clipped; the absolute outputPath
    is used internally only."""

    def bases(self):
        return ub.path_bases(self.game, self.p)

    def test_project_paths_become_relative(self):
        text = f"error CS1002 at {self.game}/Assets/Scripts/A.cs(3,1) and {self.p}/.game/gpos/x.json"
        self.assertEqual(ub.clean_message(text, self.bases(), 400),
                         "error CS1002 at Assets/Scripts/A.cs(3,1) and .game/gpos/x.json")
        real = os.path.realpath(self.game)                       # the resolved spelling is relativized as well
        self.assertEqual(ub.clean_message(f"{real}/Assets/B.cs", self.bases(), 400), "Assets/B.cs")

    def test_every_other_absolute_path_becomes_a_placeholder(self):
        for path in ("/Users/someone/secret-project/A.cs", "/private/var/folders/ab/T/x.tmp", "/var/tmp/build.log",
                     "/Applications/Unity/Hub/Editor/6000.5.8f1/Unity.app", "/Volumes/External/Game/B.cs",
                     "/tmp/elsewhere/Player.app", "/Library/Caches/u", "C:\\Users\\x\\y.cs"):
            with self.subTest(path=path):
                cleaned = ub.clean_message(f"failed: {path} (see log)", self.bases(), 400)
                self.assertEqual(cleaned, "failed: <path> (see log)")

    def test_credentials_pass_the_redaction_boundary(self):
        cleaned = ub.clean_message(f"postprocess: API_KEY={SECRET} token ghp_abcdefghijklmnopqrstuvwx", self.bases(), 400)
        self.assertNotIn(SECRET, cleaned)
        self.assertNotIn("abcdefghijklmnopqrstuvwx", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    def test_clipping_applies_after_sanitization(self):
        long_text = "x" * 390 + f" {self.game}/Assets/" + "y" * 400
        cleaned = ub.clean_message(long_text, self.bases(), 400)
        self.assertEqual(len(cleaned), 400)
        self.assertEqual(cleaned, ("x" * 390 + " Assets/" + "y" * 400)[:400])
        self.assertEqual(len(ub.clean_message("z" * 1000, self.bases(), 120)), 120)
        self.assertEqual(ub.clean_message(None, self.bases(), 10), "")

    def result_text(self, r):
        return json.dumps(r.to_dict(), default=str) + " ".join(d.message for d in r.diagnostics)

    def test_no_raw_text_reaches_the_result(self):
        raw = [{"step": f"Postprocess {self.game}/Library", "type": "Error",
                "text": f"{self.game}/Assets/A.cs: missing /Users/someone/x.dll API_KEY={SECRET}"}]
        r, _ = F_Matrix.build(self, build={"result": "Failed", "total_errors": 1, "messages": raw})
        self.assertStatus(r, tdg.FAILED, "BUILD_FAILED")
        self.assertEqual(r.data["build"]["messages"], [{"step": "Postprocess Library", "type": "Error",
                                                        "text": "Assets/A.cs: missing <path> API_KEY=[REDACTED]"}])
        text = self.result_text(r)
        for value in (SECRET, "/Users/someone", f"{self.game}/Assets"):
            self.assertNotIn(value, text)
        problems = [{"rule": "NO_SCENES", "message": f"no scene in {self.game}/ProjectSettings or /Volumes/x/y"}]
        r = self.run_cap(ub.INSPECT_BUILD, self.fake(problems=problems))
        self.assertEqual(r.data["problems"], [{"rule": "NO_SCENES", "message": "no scene in ProjectSettings or <path>"}])
        r, _ = F_Matrix.build(self, refuse="NO_SCENES", override={"refusal": {"rule": "NO_SCENES", "message": f"/private/var/{SECRET}"}})
        self.assertNotIn(SECRET, self.result_text(r))
        self.assertIn("NO_SCENES: <path>", self.result_text(r))

    def test_the_absolute_output_path_never_leaves(self):
        r, _ = F_Matrix.build(self)
        self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED")
        ws = self.workspace(r)
        staging = ws / "staging" / "Player.app"
        manifest_text = (ws / ub.MANIFEST_NAME).read_text()
        for value in (str(staging), os.path.realpath(staging), str(ws / "staging"), os.path.realpath(ws)):
            self.assertNotIn(value, self.result_text(r))
            self.assertNotIn(value, manifest_text)
        self.assertNotIn("output_path", json.dumps(r.data))
        self.assertNotIn("output_path", manifest_text)

        def strings(v):
            if isinstance(v, dict):
                for x in v.values():
                    yield from strings(x)
            elif isinstance(v, list):
                for x in v:
                    yield from strings(x)
            elif isinstance(v, str):
                yield v
        absolute = [v for v in strings(json.loads(manifest_text)) if v.startswith("/") or re.match(r"^[A-Za-z]:\\", v)]
        self.assertEqual(absolute, [])                          # every path in the manifest is relative
        self.assertEqual(r.data["payload"]["path"], "payload/Player.app")
        # a mismatched output path is reported by name, never by value
        r, _ = F_Matrix.build(self, build={"output_path": "/tmp/elsewhere/Player.app"})
        self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "BUILD_OUTCOME_UNKNOWN")
        self.assertIn("the output path", self.result_text(r))
        self.assertNotIn("/tmp/elsewhere", self.result_text(r))


# ---------------------------------------------------------------- G  the payload tree digest

def make_tree(root, reverse=False):
    items = [("Contents/Info.plist", b"plist"), ("Contents/MacOS/Game", b"\x00exe"), ("Contents/Resources/Data/a", b"a" * 10),
             ("Contents/Resources/Data/b", b"")]
    for rel, data in (reversed(items) if reverse else items):
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (Path(root) / "Contents" / "Empty").mkdir()
    os.symlink("MacOS/Game", Path(root) / "Contents" / "Link")
    return Path(root)


GOLDEN_LINES = ["D\tContents", "D\tContents/Empty", "F\tContents/Info.plist\t5\t" + hashlib.sha256(b"plist").hexdigest(),
                "L\tContents/Link\tMacOS/Game", "D\tContents/MacOS",
                "F\tContents/MacOS/Game\t4\t" + hashlib.sha256(b"\x00exe").hexdigest(), "D\tContents/Resources",
                "D\tContents/Resources/Data", "F\tContents/Resources/Data/a\t10\t" + hashlib.sha256(b"a" * 10).hexdigest(),
                "F\tContents/Resources/Data/b\t0\t" + hashlib.sha256(b"").hexdigest()]


class G_Tree(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-tree-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_golden_vector(self):
        tree = ub.payload_tree(make_tree(self.tmp / "A.app"))
        expected = hashlib.sha256(("gpos.unity.payload-tree/1\n" + "".join(l + "\n" for l in GOLDEN_LINES)).encode()).hexdigest()
        self.assertEqual(tree, {"algorithm": "gpos.unity.payload-tree/1", "entries": 10, "bytes": 19, "digest": expected})
        self.assertEqual(expected, "58ac97b92fbdc28e782c8701c2e371ad4d432a7bf168bccda365b3ace18ab6d3")   # the pinned vector
        self.assertEqual(ub.payload_tree(make_tree(self.tmp / "B.app", reverse=True)), tree)   # creation order is irrelevant

    def test_every_change_changes_the_digest(self):
        base = ub.payload_tree(make_tree(self.tmp / "A.app"))["digest"]
        changes = [lambda a: (a / "Contents/Resources/Data/b").write_bytes(b"x"),
                   lambda a: (a / "Contents/New").mkdir(),
                   lambda a: os.chmod(a / "Contents/MacOS/Game", 0o700) or (a / "Contents/MacOS/Game").write_bytes(b"\x00exf"),
                   lambda a: (a / "Contents/Link").unlink() or os.symlink("MacOS", a / "Contents/Link"),
                   lambda a: (a / "Contents/Resources/Data/a").rename(a / "Contents/Resources/Data/c")]
        for i, change in enumerate(changes):
            app = make_tree(self.tmp / f"C{i}.app")
            change(app)
            self.assertNotEqual(ub.payload_tree(app)["digest"], base, i)

    def test_links_must_stay_inside(self):
        app = make_tree(self.tmp / "A.app")
        os.symlink("../../Contents/Info.plist", app / "Contents" / "MacOS" / "Up")   # inside: MacOS/.. /.. is the root
        ub.payload_tree(app)
        for target in ("../../../outside", "/etc/hosts", "../../..", "x\ty"):
            with self.subTest(target=target):
                app = make_tree(self.tmp / f"L{abs(hash(target))}.app")
                os.symlink(target, app / "Contents" / "MacOS" / "Bad")
                with self.assertRaises(ub.PayloadProblem):
                    ub.payload_tree(app)

    def test_special_files_and_names(self):
        app = make_tree(self.tmp / "A.app")
        os.mkfifo(app / "Contents" / "Pipe")
        with self.assertRaises(ub.PayloadProblem):
            ub.payload_tree(app)
        app = make_tree(self.tmp / "B.app")
        (app / "Contents" / "tab\tname").write_bytes(b"")
        with self.assertRaises(ub.PayloadProblem):
            ub.payload_tree(app)
        with self.assertRaises(ub.PayloadProblem):
            ub.payload_tree(app / "Contents" / "Info.plist")   # not a directory
        os.symlink(app, self.tmp / "Link.app")
        with self.assertRaises(ub.PayloadProblem):
            ub.payload_tree(self.tmp / "Link.app")            # the root itself is never followed

    def test_every_bound(self):
        for name, value in (("MAX_ENTRIES", 5), ("MAX_TOTAL_BYTES", 15), ("MAX_DEPTH", 1), ("MAX_REL_BYTES", 20)):
            with self.subTest(bound=name):
                old = getattr(ub, name)
                setattr(ub, name, value)
                try:
                    with self.assertRaises(ub.PayloadProblem):
                        ub.payload_tree(make_tree(self.tmp / f"{name}.app"))
                finally:
                    setattr(ub, name, old)


# ---------------------------------------------------------------- H  publication

class H_Publication(BuildCase):
    def test_the_manifest_is_written_last_and_only_after_the_rename(self):
        order, publish, write = [], ub.publish, ub.write_manifest
        ub.publish = lambda ws: (order.append(("publish", (Path(ws) / ub.MANIFEST_NAME).exists())), publish(ws))[1]
        ub.write_manifest = lambda ws, m: (order.append(("manifest", (Path(ws) / ub.PAYLOAD).exists())), write(ws, m))[1]
        try:
            r = self.run_cap(ub.BUILD)
        finally:
            ub.publish, ub.write_manifest = publish, write
        self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED")
        self.assertEqual(order, [("publish", False), ("manifest", True)])

    def test_a_publication_failure_is_never_a_completed_build(self):
        for name in ("publish", "write_manifest"):
            with self.subTest(step=name):
                original = getattr(ub, name)

                def fail(*a, **k):
                    raise OSError("injected")
                setattr(ub, name, fail)
                try:
                    r = self.run_cap(ub.BUILD)
                finally:
                    setattr(ub, name, original)
                self.assertStatus(r, tdg.OUTCOME_UNKNOWN, "BUILD_OUTCOME_UNKNOWN", "BUILD_QUARANTINED")
                ws = self.workspace(r)
                self.assertFalse((ws / ub.MANIFEST_NAME).exists())
                self.assertEqual(ub.revalidate(ws)[0], None)

    def test_validation_precedes_publication(self):
        calls, validate = [], ub.validate_app
        ub.validate_app = lambda *a: (calls.append((Path(a[0]).parent.name, (Path(a[0]).parents[1] / ub.PAYLOAD).exists())),
                                      validate(*a))[1]
        try:
            self.assertStatus(self.run_cap(ub.BUILD), tdg.SUCCESS, "BUILD_PUBLISHED")
        finally:
            ub.validate_app = validate
        self.assertEqual(calls, [("staging", False)])

    def test_nothing_is_ever_overwritten(self):
        ws = self.tmp / "ws"
        (ws / "staging" / "Player.app").mkdir(parents=True)
        (ws / "payload").mkdir()
        with self.assertRaises(FileExistsError):
            ub.publish(ws)
        (ws / ub.MANIFEST_NAME).write_text("{}")
        with self.assertRaises(FileExistsError):
            ub.write_manifest(ws, {"x": 1})
        self.assertEqual((ws / ub.MANIFEST_NAME).read_text(), "{}")
        self.assertFalse((ws / (ub.MANIFEST_NAME + ".tmp")).exists())

    def test_revalidation_detects_any_change(self):
        r = self.run_cap(ub.BUILD)
        ws = self.workspace(r)
        manifest, why = ub.revalidate(ws)
        self.assertIsNone(why)
        (ws / "payload" / "Player.app" / "Contents" / "Resources" / "Data" / "level0").write_bytes(b"tampered")
        self.assertIn("no longer matches", ub.revalidate(ws)[1])
        m = dict(manifest, git_verified=True)
        (ws / ub.MANIFEST_NAME).write_text(json.dumps(m))
        self.assertIn("Git verification", ub.revalidate(ws)[1])
        (ws / ub.MANIFEST_NAME).unlink()
        self.assertEqual(ub.revalidate(ws), (None, "no readable build manifest: the build is not complete"))


# ---------------------------------------------------------------- L  conflicts

class L_Conflicts(BuildCase):
    def test_a_live_session_holds_the_project(self):
        lease_mod.acquire(self.p, "unity", f"EDITOR_PROJECT:{self.p}", "AGENT:x", "t", scope=lease_mod.SESSION,
                          session={"session_id": "a" * 32})
        fake = self.fake()
        for cap in ub.CAPABILITY_IDS:
            self.assertStatus(self.run_cap(cap, fake), tdg.CONFLICT, "LIVE_SESSION_HELD")
        self.assertEqual(fake.runs(), [])

    def test_a_held_project_lock(self):
        lock = self.game / "Temp" / "UnityLockfile"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_bytes(b"")
        holder = tl_hold_lock(lock)
        try:
            fake = self.fake()
            for cap in ub.CAPABILITY_IDS:
                r = self.run_cap(cap, fake)
                self.assertStatus(r, tdg.CONFLICT, "ENGINE_PROJECT_LOCKED")
                self.assertEqual(r.data["project_lock"], pl.ACTIVE_EDITOR)
            self.assertEqual(fake.runs(), [])
        finally:
            holder.kill()
            holder.wait(30)
        r = self.run_cap(ub.BUILD)   # an unheld leftover lockfile: proceed, and say so
        self.assertStatus(r, tdg.SUCCESS, "BUILD_PUBLISHED", "ENGINE_PROJECT_ORPHAN_LOCK")
        self.assertTrue(lock.exists())

    def test_a_skipped_lock_proof_is_caught(self):
        calls = []

        def proof(project, editor):
            calls.append(project)
            return pl.assess(project, editor)
        fake = self.fake()
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(UnityAdapter(hub_roots=[str(fake.hub)], platform="darwin", lock_proof=proof))
        request = ExecutionRequest(adapter_id="unity", capability_id=ub.BUILD, subject=Subject("FEATURE", "FEATURE-0001", "r"),
                                   project_root=str(self.p), allow_mutation=True, build_revision=REV,
                                   inputs={"unity_project": "Game", "expected_configuration_token": token_of(configuration())})
        self.assertEqual(execute(registry, request).status, tdg.SUCCESS)
        self.assertEqual(len(calls), 2)   # before the workspace is used and immediately before the launch

    def test_the_exact_editor_version(self):
        (self.game / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.5.9f1\n")
        fake = self.fake()
        for cap in ub.CAPABILITY_IDS:
            self.assertStatus(self.run_cap(cap, fake), tdg.INCOMPATIBLE, "ENGINE_EDITOR_VERSION_UNAVAILABLE")
        self.assertEqual(fake.runs(), [])

    def test_a_reused_request_id_is_never_adopted(self):
        first = self.run_cap(ub.BUILD, request_id="req-reused")
        self.assertStatus(first, tdg.SUCCESS, "BUILD_PUBLISHED")
        ws = self.workspace(first)
        before = {p: p.stat().st_mtime_ns for p in ws.rglob("*") if not p.is_symlink()}
        fake = self.fake()
        for cap in ub.CAPABILITY_IDS:
            r = self.run_cap(cap, fake, request_id="req-reused")
            self.assertStatus(r, tdg.CONFLICT, "BUILD_WORKSPACE_NOT_FRESH")
        self.assertEqual(fake.runs(), [])
        self.assertEqual({p: p.stat().st_mtime_ns for p in ws.rglob("*") if not p.is_symlink()}, before)
        self.assertIsNone(ub.revalidate(ws)[1])


def tl_hold_lock(path):
    code = ("import fcntl, sys, time\nf = open(sys.argv[1])\nfcntl.flock(f, fcntl.LOCK_EX)\nprint('held', flush=True)\n"
            "time.sleep(600)\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    return child


# ================================================================ real Unity groups

REAL = {}


def setUpModule():
    if sys.platform == "win32":   # alpha.25: a macOS suite; the Windows batch plane is tests/test_unity_windows.py
        raise unittest.SkipTest("macOS Unity suite: NOT_RUN on Windows (see tests/test_unity_windows.py)")
    if FAST:
        return
    if len(tl.EDITORS) != 1:
        raise RuntimeError(f"UNITY_RUNTIME_UNAVAILABLE_FOR_PHASE2C7: {len(tl.EDITORS)} Hub Unity Editors found; exactly "
                           f"one is required")
    if tl.EDITOR_PREFS.exists():
        REAL["prefs"] = tl.prefs_snapshot()
    REAL["upm"] = tl.upm_config_state()
    REAL["work"] = Path(tempfile.mkdtemp(prefix="gpos-build-real-")).resolve()
    ta.REAL_STATE.setdefault("work", REAL["work"])


def tearDownModule():
    try:
        for editor in ta.REAL_STATE["editors"]:
            editor.stop()
        if FAST:
            return
        if "prefs" in REAL:
            now = tl.prefs_snapshot()
            moved = {k for k in set(now) | set(REAL["prefs"]) if now.get(k) != REAL["prefs"].get(k)}
            unexpected = sorted(moved - tl.ACCEPTED_PREFS)
            if unexpected:
                raise AssertionError(f"UNITY_SHARED_USER_STATE_UNEXPECTED_MUTATION: {unexpected}")
        if tl.upm_config_state() != REAL["upm"]:
            raise AssertionError("the user's Package Manager configuration files changed")
    finally:
        shutil.rmtree(REAL.get("work", "/nonexistent"), ignore_errors=True)


def rgit(p, *args):
    """The authorized external repository workflow of a test (isolated configuration; GPOS never runs this)."""
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_AUTHOR_NAME="lab",
               GIT_AUTHOR_EMAIL="lab@example.invalid", GIT_COMMITTER_NAME="lab", GIT_COMMITTER_EMAIL="lab@example.invalid")
    out = subprocess.run(["git", "-c", "core.fsmonitor=false", *args], cwd=str(p), capture_output=True, text=True, env=env,
                         timeout=120)
    if out.returncode != 0:
        raise AssertionError(f"git {args}: {out.stderr}")
    return out.stdout


def real_project(name, kind="pass", live_testkit=False):
    p = REAL["work"] / name / "p"
    shutil.copytree(FIXTURE, p)
    fixtures.make_project(p / "Game", kind, EDITOR_VERSION, EDITOR)
    bi.install(p, p / "Game", bi.verify_source())
    shutil.copytree(BUILD_TESTKIT, p / "Game" / "Packages" / BUILD_TESTKIT.name)
    if live_testkit:
        shutil.copytree(tl.TESTKIT, p / "Game" / "Packages" / tl.TESTKIT.name)
    (p / ".gitignore").write_text(GITIGNORE)
    rgit(p, "init", "-q")
    commit(p, "fixture")
    return p


def commit(p, message):
    rgit(p, "add", "-A")
    if rgit(p, "status", "--porcelain"):
        rgit(p, "commit", "-q", "-m", message)


def testkit(p, op, value="", timeout=900):
    """One Human-like configuration change in a lab-owned batch Editor, through the test-only build testkit."""
    ws = Path(tempfile.mkdtemp(prefix="tk-", dir=str(p.parent)))
    for n in (ua.UPM_USER_NAME, ua.UPM_GLOBAL_NAME):
        (ws / n).write_text("")
    cache = p / ".game" / "gpos-runtime" / "unity" / "upm-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env = ua.upm_environment(ws, cache).build()
    (ws / "op.txt").write_text(f"op={op}\nvalue={value}\n")
    subprocess.run([EDITOR, "-batchmode", "-projectPath", str(p / "Game"), "-logFile", str(ws / "editor.log"),
                    "-upmLogFile", str(ws / "upm.log"), "-cacheServerEnableDownload", "false", "-cacheServerEnableUpload",
                    "false", "-executeMethod", "Gpos.BuildTestkit.Testkit.Run", "-gposBuildTestkitOp", str(ws / "op.txt")],
                   cwd=str(ws), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=timeout)
    out = (ws / "op.txt.out").read_text() if (ws / "op.txt.out").exists() else "error=no answer"
    if "ok=true" not in out:
        raise AssertionError(f"testkit {op}: {out}")
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def provenance(p):
    r = execute(default_registry(FW), ExecutionRequest(adapter_id="git", capability_id="git.resolve-provenance",
                                                       subject=Subject("FEATURE", "FEATURE-0001", None), project_root=str(p)))
    return r.status, (r.data or {}).get("repository_revision")


def inspect(p, **kw):
    return tl.real_request(p, ub.INSPECT_BUILD, inputs={"unity_project": "Game"}, timeout=1800, **kw)


def build(p, token, revision, **kw):
    kw.setdefault("timeout", 1800)
    return tl.real_request(p, ub.BUILD, inputs={"unity_project": "Game", "expected_configuration_token": token},
                           build_revision=revision, **kw)


def workspace_of(p, result):
    return p / ".game" / "gpos-runtime" / "tool-output" / "unity" / result.request_id


def ok(test, result, *expected, status=tdg.SUCCESS):
    text = " | ".join(f"{d.code}: {d.message}" for d in result.diagnostics)
    test.assertEqual(result.status, status, text)
    for code in expected:
        test.assertIn(code, codes(result), text)
    return result.data


class RealBuild(unittest.TestCase):
    p = None

    def qualified(self):
        """The alpha.21 qualified-build workflow: Git R -> inspect -> Git R -> build(R, token) -> Git R."""
        s1, r1 = provenance(self.p)
        self.assertEqual(s1, tdg.SUCCESS)
        self.assertRegex(r1, r"^[0-9a-f]{40}$")
        i = ok(self, inspect(self.p))
        self.assertTrue(i["buildable"], i["problems"])
        s2, r2 = provenance(self.p)
        self.assertEqual((s2, r2), (tdg.SUCCESS, r1), "the inspection changed the repository")
        result = build(self.p, i["configuration_token"], r1)
        b = ok(self, result, "BUILD_PUBLISHED")
        s3, r3 = provenance(self.p)
        self.assertEqual((s3, r3), (tdg.SUCCESS, r1), "the build changed the repository")
        ws = workspace_of(self.p, result)
        manifest, why = ub.revalidate(ws)
        self.assertIsNone(why)
        self.assertEqual((manifest["build_revision"], manifest["build_revision_source"], manifest["configuration_token"]),
                         (r1, "CALLER_SUPPLIED", i["configuration_token"]))
        self.assertNotIn("git_verified", json.dumps(manifest))
        return i, b, manifest, ws

    def refused(self, rule, code=None, status=tdg.CONFLICT):
        i = ok(self, inspect(self.p), "BUILD_CONFIGURATION_NOT_BUILDABLE")
        self.assertIn(rule, [x["rule"] for x in i["problems"]])
        result = build(self.p, i["configuration_token"], provenance(self.p)[1] or REV)
        ok(self, result, code or "BUILD_CONFIGURATION_UNSUPPORTED", status=status)
        ws = workspace_of(self.p, result)
        self.assertFalse((ws / ub.STARTED_NAME).exists())
        self.assertFalse((ws / ub.STAGING).exists())
        self.assertFalse((ws / ub.MANIFEST_NAME).exists())
        return i


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class RB1_Classic(RealBuild):
    @classmethod
    def setUpClass(cls):
        cls.p = real_project("classic")
        testkit(cls.p, "open")
        testkit(cls.p, "scene")
        commit(cls.p, "a build scene")

    def test_01_the_qualified_classic_build(self):
        i, b, m, ws = self.qualified()
        self.assertEqual((i["mode"], i["development"], i["active_target"]), ("CLASSIC", False, "StandaloneOSX"))
        self.assertEqual((m["development"], m["unity_build"]["development_observed"], m["unity_build"]["result"]),
                         (False, False, "Succeeded"))
        self.assertEqual(m["payload"]["bundle_identifier"], i["configuration"]["application_identifier"])
        app = ws / "payload" / "Player.app"
        guid = [line for line in (app / "Contents/Resources/Data/boot.config").read_text().splitlines()
                if line.startswith("build-guid=")]
        self.assertEqual(guid, [f"build-guid={m['unity_build']['guid']}"])
        self.assertFalse((ws / "staging").exists())
        self.assertEqual(rgit(self.p, "status", "--porcelain"), "")

    def test_02_classic_development(self):
        testkit(self.p, "development", "true")
        self.assertEqual(rgit(self.p, "status", "--porcelain"), "")   # a Library-only setting
        i, b, m, ws = self.qualified()
        self.assertEqual((i["development"], m["development"], m["unity_build"]["development_observed"]), (True, True, True))
        testkit(self.p, "development", "false")

    def test_03_a_stale_token_builds_nothing(self):
        i = ok(self, inspect(self.p))
        testkit(self.p, "version", "1.1")
        commit(self.p, "a new bundle version")
        result = build(self.p, i["configuration_token"], provenance(self.p)[1])
        ok(self, result, "BUILD_CONFIGURATION_CHANGED", status=tdg.CONFLICT)
        ws = workspace_of(self.p, result)
        self.assertFalse((ws / ub.STARTED_NAME).exists() or (ws / ub.STAGING).exists() or (ws / ub.MANIFEST_NAME).exists())

    def test_04_repeated_builds_are_distinct(self):
        i = ok(self, inspect(self.p))
        rev = provenance(self.p)[1]
        a, b = build(self.p, i["configuration_token"], rev), build(self.p, i["configuration_token"], rev)
        ok(self, a, "BUILD_PUBLISHED")
        ok(self, b, "BUILD_PUBLISHED")
        self.assertNotEqual(a.data["build_id"], b.data["build_id"])
        ma, mb = ub.revalidate(workspace_of(self.p, a))[0], ub.revalidate(workspace_of(self.p, b))[0]
        self.assertIsNone(ub.revalidate(workspace_of(self.p, a))[1])
        self.assertIsNone(ub.revalidate(workspace_of(self.p, b))[1])
        self.assertEqual(ma["payload"]["entries"], mb["payload"]["entries"])
        self.assertEqual(ma["configuration_token"], mb["configuration_token"])
        REAL["repeat_digests"] = (ma["payload"]["tree_digest"], mb["payload"]["tree_digest"])

    def test_05_debug_and_profiler_state_is_refused(self):
        testkit(self.p, "connect_profiler", "true")
        self.refused("DEBUG_STATE_UNSUPPORTED")
        testkit(self.p, "connect_profiler", "false")

    def test_06_scenes(self):
        testkit(self.p, "scenes", "")
        commit(self.p, "no scenes")
        self.refused("NO_SCENES")
        testkit(self.p, "scenes", "Assets/Scenes/Gone.unity:1")
        commit(self.p, "a missing scene")
        self.refused("SCENE_INVALID")
        testkit(self.p, "scenes", "Assets/Scenes/Build.unity:1")
        commit(self.p, "the build scene again")

    def test_07_a_non_active_target_is_never_switched(self):
        testkit(self.p, "switch", "WebGL")
        i = self.refused("TARGET_NOT_ACTIVE", "BUILD_TARGET_NOT_ACTIVE")
        self.assertEqual(i["active_target"], "WebGL")
        self.assertEqual(ok(self, inspect(self.p))["active_target"], "WebGL")   # still WebGL: GPOS switched nothing
        testkit(self.p, "switch", "StandaloneOSX")
        commit(self.p, "back to macOS")

    def test_08_a_killed_build_has_no_manifest(self):
        i = ok(self, inspect(self.p))
        (self.p / "Game" / "Library" / "gpos-build-testkit-slow").write_text("600")
        try:
            result = build(self.p, i["configuration_token"], provenance(self.p)[1], timeout=90)
        finally:
            (self.p / "Game" / "Library" / "gpos-build-testkit-slow").unlink()
        ok(self, result, "BUILD_OUTCOME_UNKNOWN", "BUILD_QUARANTINED", status=tdg.OUTCOME_UNKNOWN)
        ws = workspace_of(self.p, result)
        self.assertTrue((ws / ub.STARTED_NAME).exists())
        self.assertFalse((ws / ub.MANIFEST_NAME).exists())
        self.assertFalse((ws / ub.PAYLOAD).exists())
        self.assertEqual(ub.revalidate(ws)[0], None)
        deadline = time.monotonic() + 180                    # import workers may outlive the killed Editor briefly
        while pl.assess(self.p / "Game", EDITOR).state not in pl.PROCEED and time.monotonic() < deadline:
            time.sleep(2)
        after = build(self.p, i["configuration_token"], provenance(self.p)[1])
        ok(self, after, "BUILD_PUBLISHED")                   # a new request; the killed one is never adopted or retried
        self.assertTrue((ws / ub.STARTED_NAME).exists() and not (ws / ub.MANIFEST_NAME).exists())


    def test_09_a_caller_output_dir_launches_nothing(self):
        """alpha.21 pre-freeze M1 against the real Editor: refused in request validation; no directory, no Unity."""
        target = self.p / "Game" / "Assets" / "UnexpectedWorkspace"
        before = set(tl_unity_pids())
        for cap, inputs, kw in ((ub.INSPECT_BUILD, {"unity_project": "Game"}, {}),
                                (ub.BUILD, {"unity_project": "Game", "expected_configuration_token": "ab" * 32},
                                 {"build_revision": provenance(self.p)[1]})):
            r = tl.real_request(self.p, cap, inputs=inputs, output_dir=str(target), timeout=600, **kw)
            ok(self, r, "INVALID_TOOL_REQUEST", status=tdg.INVALID_REQUEST)
            self.assertFalse(r.mutation_performed)
            self.assertFalse(target.exists())
            self.assertEqual(set(tl_unity_pids()) - before, set())
        self.assertEqual(rgit(self.p, "status", "--porcelain"), "")


def tl_unity_pids():
    out = subprocess.run(["/bin/ps", "-Ao", "pid=,command="], capture_output=True, text=True).stdout
    return [line.split(None, 1)[0] for line in out.splitlines() if str(REAL.get("work", "/nonexistent")) in line
            and "Unity.app/Contents/MacOS/Unity" in line]


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class RB2_Profile(RealBuild):
    @classmethod
    def setUpClass(cls):
        cls.p = real_project("profile")
        marker = cls.p / "Game" / "Assets" / "Marker"
        marker.mkdir(parents=True)
        (marker / "Marker.cs").write_text("#if GPOS_PROFILE_DEF\npublic static class ProfileDefineOnQ7 { }\n#else\n"
                                          "public static class ProfileDefineOffQ7 { }\n#endif\n")
        testkit(cls.p, "open")
        testkit(cls.p, "scene")
        testkit(cls.p, "profile", "Mac Test")
        commit(cls.p, "a macOS Build Profile")

    def dll(self, ws):
        return b"".join(p.read_bytes() for p in (ws / "payload").rglob("Assembly-CSharp.dll"))

    def test_01_the_active_profile_is_built_exactly(self):
        i, b, m, ws = self.qualified()
        self.assertEqual((i["mode"], m["configuration_mode"]), ("PROFILE", "PROFILE"))
        self.assertEqual(m["build_profile"]["path"], "Assets/Settings/Build Profiles/Mac Test.asset")
        self.assertIn(b"ProfileDefineOffQ7", self.dll(ws))

    def test_02_profile_development(self):
        testkit(self.p, "development", "true")        # with a profile active this edits the profile asset
        self.assertIn("Mac Test.asset", rgit(self.p, "status", "--porcelain"))
        commit(self.p, "a development profile")
        i, b, m, ws = self.qualified()
        self.assertEqual((i["development"], i["configuration"]["profile"]["development"], m["unity_build"]["development_observed"]),
                         (True, True, True))
        testkit(self.p, "development", "false")
        commit(self.p, "a release profile")

    def test_03_profile_defines(self):
        testkit(self.p, "profile_defines", "GPOS_PROFILE_DEF")
        commit(self.p, "a profile define")
        i, b, m, ws = self.qualified()
        self.assertEqual(i["configuration"]["profile"]["scripting_defines"], ["GPOS_PROFILE_DEF"])
        self.assertIn(b"ProfileDefineOnQ7", self.dll(ws))

    def test_04_player_settings_overrides_are_refused(self):
        testkit(self.p, "profile_override")
        commit(self.p, "a Player Settings override")
        self.refused("PROFILE_PLAYER_SETTINGS_OVERRIDE")
        rgit(self.p, "revert", "--no-edit", "HEAD")

    def test_05_profile_debug_state_is_refused(self):
        testkit(self.p, "profile_connect_profiler", "true")
        commit(self.p, "a profiler flag")
        self.refused("DEBUG_STATE_UNSUPPORTED")
        rgit(self.p, "revert", "--no-edit", "HEAD")
        self.assertTrue(ok(self, inspect(self.p))["buildable"])


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class RB3_FailingTests(RealBuild):
    def test_failing_tests_still_build_and_stay_failures(self):
        self.p = real_project("failing", kind="fail")
        testkit(self.p, "open")
        testkit(self.p, "scene")
        commit(self.p, "a build scene")
        i, b, m, ws = self.qualified()
        names = {p.name for p in (ws / "payload").rglob("*.dll")}
        self.assertFalse({n for n in names if "Tests" in n or n.startswith("nunit")}, names)
        r = tl.real_request(self.p, ua.EDITMODE, inputs={"unity_project": "Game"}, timeout=1800)
        self.assertIn("TESTS_FAILED", codes(r))
        self.assertGreater(r.data["failed"], 0)
        self.assertEqual(provenance(self.p)[1], m["build_revision"])


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class RB4_CompileErrors(RealBuild):
    def test_compile_errors_before_the_entry(self):
        self.p = real_project("compile", kind="compile")
        ok(self, inspect(self.p), "BUILD_COMPILE_FAILED", status=tdg.FAILED)
        commit(self.p, "what the first open wrote (external repository workflow)")
        status, revision = provenance(self.p)
        self.assertEqual(status, tdg.SUCCESS)
        result = build(self.p, "ab" * 32, revision)
        ok(self, result, "BUILD_COMPILE_FAILED", status=tdg.FAILED)
        ws = workspace_of(self.p, result)
        self.assertFalse((ws / ub.STARTED_NAME).exists() or (ws / ub.RESPONSE_NAME).exists() or (ws / ub.STAGING).exists())
        self.assertNotIn("BUILD_OUTCOME_UNKNOWN", codes(result))


@unittest.skipIf(FAST, "GPOS_UNITY_TEST_FAST: no real Unity process")
class RQ_Composition(RealBuild):
    """alpha.20 -> alpha.21 with no Human Unity step: a source error, its diagnostics, the fix, wait-ready, detach, close,
    the external repository workflow, then the qualified build; plus the session and open-Editor conflicts."""

    def test_source_error_to_fixed_build(self):
        self.p = p = real_project("composition", live_testkit=True)
        testkit(p, "open")
        testkit(p, "scene")
        commit(p, "a build scene")
        lab = ta.Lab(p)
        lab.editor.launch()
        r = lab.run(live.ATTACH, timeout=120)
        self.assertEqual(r.status, tdg.SUCCESS, [d.message for d in r.diagnostics])
        lab.sid = r.data["session_id"]
        game = p / "Game"
        (game / "Assets" / "Feature").mkdir(parents=True)
        (game / "Assets" / "Feature" / "Feature.asmdef").write_text(json.dumps({"name": "Feature"}))
        pulse = game / "Assets" / "Feature" / "Pulse.cs"
        pulse.write_text("using UnityEngine;\nnamespace Feature {\npublic class Pulse : MonoBehaviour\n{\n"
                         "    public float rate = 2f\n}\n}\n")
        d = ta.ok(self, lab.run(S.SYNC, sources=["Assets/Feature/Feature.asmdef", "Assets/Feature/Pulse.cs"]))
        w = lab.run(S.WAIT, sync_generation=d["sync_generation"], timeout=240)
        self.assertIn("LIVE_COMPILATION_FAILED", codes(w))
        errors = ta.ok(self, lab.run(S.DIAGNOSTICS, severity="ERROR"))["entries"]
        self.assertEqual({(e["file"], e["line"]) for e in errors}, {("Assets/Feature/Pulse.cs", 5)})
        # the live session holds the project: neither build capability starts an Editor
        ok(self, inspect(p), "LIVE_SESSION_HELD", status=tdg.CONFLICT)
        ok(self, build(p, "ab" * 32, REV), "LIVE_SESSION_HELD", status=tdg.CONFLICT)
        pulse.write_text("using UnityEngine;\nnamespace Feature {\npublic class Pulse : MonoBehaviour\n{\n"
                         "    public float rate = 2f;\n}\n}\n")
        d = ta.ok(self, lab.run(S.SYNC, sources=["Assets/Feature/Pulse.cs"]))
        w = ta.ok(self, lab.run(S.WAIT, sync_generation=d["sync_generation"], timeout=240))
        self.assertEqual((w["ready"], w["outcome"]), (True, "SUCCEEDED"))
        ta.ok(self, lab.run(live.DETACH, timeout=120))
        lab.editor.quit()
        commit(p, "the Pulse feature (external repository workflow)")
        i, b, m, ws = self.qualified()
        dlls = [x for x in (ws / "payload").rglob("Feature.dll")]
        self.assertEqual(len(dlls), 1)
        self.assertIn(b"Pulse", dlls[0].read_bytes())
        # a Human-open Editor (no session) holds the project lock
        lab.editor.launch()
        try:
            r = inspect(p)
            ok(self, r, "ENGINE_PROJECT_LOCKED", status=tdg.CONFLICT)
            self.assertEqual(r.data["project_lock"], pl.ACTIVE_EDITOR)
        finally:
            lab.editor.quit()


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    print(f"GPOS Unity Build Core tests ({'fast only' if FAST else 'fast and real'}); repeated-build digests: "
          f"{REAL.get('repeat_digests', 'not run')}")
    sys.exit(0 if result.wasSuccessful() else 1)
