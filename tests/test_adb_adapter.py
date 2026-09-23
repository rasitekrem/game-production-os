#!/usr/bin/env python3
"""Phase 2C-3 — production Android Debug Bridge (ADB) evidence adapter tests.

    GPOS_TEST_ANDROID_SERIALS=<serial>[,<serial>...] python3 tests/test_adb_adapter.py

These are real integration tests against real Android targets. `GPOS_TEST_ANDROID_SERIALS` (test-only,
never a production input) names the targets a human authorized for testing; every real-target test runs
on each of them in turn, always as `adb -s <that serial>`, never depending on device-list order. Without
the variable the suite uses the single eligible local target (or `GPOS_TEST_ANDROID_SERIAL`), and stops
with ADB_TARGET_UNAVAILABLE_FOR_PHASE2C3 or ADB_TARGET_SELECTION_REQUIRED_FOR_PHASE2C3 otherwise. The
suite never falls back to mocks: stand-in programs are used only for deterministic error cases (a missing
or unrecognizable adb, an unauthorized or offline or booting target, truncated or malformed output, a
timeout), and say so.

Privacy: physical-device serials and build fingerprints are never printed. Targets are labelled
(USB-1, EMU-1, ...), the runner's output is filtered, and screenshots are checked for structure only
(signature, dimensions, size, hash) — never displayed, decoded or inspected — and deleted with the test
workspace. Only the read-only commands the adapter authorizes are sent to a target; test code adds
read-only `getprop` and `pidof` queries to cross-check what the adapter reported.

Groups: A registration · B real probe · C target selection · D readiness · E device report · F report
privacy · G screenshot · H screenshot truncation · I meminfo · J package validation · K package not running
· L contexts · M materialization · N explicit Git handoff · O mutation consent · P dry run · Q output
collision · R target immutability · S command surface · T network and wireless · U public output ·
V parsers · W performance limitations · X CLI · Y repository privacy · Z other adapters unchanged.
"""

import ast
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_HOME = tempfile.mkdtemp(prefix="gpos-adb-home-")  # Git fixtures only; adb keeps the real HOME (its key store)

from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import evidence as tev  # noqa: E402
from gpos.tools import model as tmodel  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools import validation as tval  # noqa: E402
from gpos.tools.adb import adapter as aa  # noqa: E402
from gpos.tools.adb import parsers as ap  # noqa: E402
from gpos.tools.adb import AdbAdapter  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.git import adapter as ga  # noqa: E402
from gpos.tools.model import InputArtifact, Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.synthetic import SyntheticAdapter  # noqa: E402

FW = load_framework()
REG = FW.registry
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
PROJECT_ID = "synthetic-adapter-project"
ADB = shutil.which("adb")
GIT = shutil.which("git")
REVISION = "0123456789abcdef0123456789abcdef01234567"
PACKAGE = "com.android.systemui"          # the fixed, non-personal test package; never launched
NOT_RUNNING = "com.gpos.notinstalled.probe"
CAPS = (aa.DEVICE_REPORT, aa.SCREENSHOT, aa.MEMINFO)
FILES = {aa.DEVICE_REPORT: "device.json", aa.SCREENSHOT: "screenshot.png", aa.MEMINFO: "meminfo.txt"}
EVIDENCE = {aa.DEVICE_REPORT: ("DEVICE_EVIDENCE", "TARGET_RUNTIME"), aa.SCREENSHOT: ("VISUAL_EVIDENCE", "TARGET_RUNTIME"),
            aa.MEMINFO: ("PERFORMANCE_EVIDENCE", "PERFORMANCE_RUNTIME")}
REPORT_KEYS = {"manufacturer", "model", "device_codename", "primary_abi", "android_release", "api_level",
               "security_patch", "build_fingerprint", "boot_completed"}
FORBIDDEN_FAMILIES = {"install", "install-multiple", "uninstall", "push", "pull", "sync", "root", "unroot", "remount",
                      "reboot", "reboot-bootloader", "disable-verity", "enable-verity", "tcpip", "connect",
                      "disconnect", "pair", "forward", "reverse", "kill-server", "start-server", "bugreport", "backup",
                      "restore", "jdwp", "emu", "wait-for-device", "logcat", "screenrecord", "input", "am", "pm",
                      "settings", "rm", "mv", "cp", "sh", "su", "mdns", "-d", "-e"}
FIXTURE_ENV = {"HOME": _HOME, "PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid", "LC_ALL": "C"}


# ---------------------------------------------------------------- the authorized targets (test-only)

def adb_test(serial, *args, timeout=60):
    """Read-only cross-check queries from test code (getprop, pidof, devices). Never a production path."""
    argv = [ADB] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(argv, capture_output=True, timeout=timeout)


def _eligible():
    listing = adb_test(None, "devices").stdout.decode("utf-8", "replace").splitlines()[1:]
    out = []
    for line in listing:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device" and ap.serial_problem(parts[0]) is None:
            if adb_test(parts[0], "shell", "getprop", "sys.boot_completed").stdout.strip() == b"1":
                out.append(parts[0])
    return out


def mask(serial):
    return serial if serial.startswith("emulator-") else f"{serial[:2]}…{serial[-2:]}"


def _targets():
    """[(label, serial, kind)] or raises SystemExit-worthy RuntimeError with the stop marker."""
    configured = os.environ.get("GPOS_TEST_ANDROID_SERIALS") or os.environ.get("GPOS_TEST_ANDROID_SERIAL")
    serials = [s.strip() for s in configured.split(",") if s.strip()] if configured else None
    if serials is None:
        eligible = _eligible()
        if not eligible:
            raise RuntimeError("ADB_TARGET_UNAVAILABLE_FOR_PHASE2C3: no eligible local Android target")
        if len(eligible) > 1:
            raise RuntimeError("ADB_TARGET_SELECTION_REQUIRED_FOR_PHASE2C3: eligible targets "
                               f"{[mask(s) for s in eligible]}; set GPOS_TEST_ANDROID_SERIALS")
        serials = eligible
    counts, out = {"usb": 0, "emulator": 0}, []
    for serial in serials:
        if ap.serial_problem(serial):
            raise RuntimeError(f"ADB_TARGET_UNAVAILABLE_FOR_PHASE2C3: {mask(serial)} is not a local ADB serial")
        kind = "emulator" if serial.startswith("emulator-") else "usb"
        counts[kind] += 1
        out.append((f"{'EMU' if kind == 'emulator' else 'USB'}-{counts[kind]}", serial, kind))
    return out


TARGETS = []          # filled in setUpModule
FINGERPRINTS = set()  # observed at runtime, only ever used to redact and to scan the repository


def setUpModule():
    if ADB is None:
        raise RuntimeError("ADB_RUNTIME_UNAVAILABLE_FOR_PHASE2C3: no adb on PATH")
    if not TARGETS:
        TARGETS.extend(_targets())
    for label, serial, _ in TARGETS:
        if adb_test(serial, "get-state").stdout.strip() != b"device" or \
                adb_test(serial, "shell", "getprop", "sys.boot_completed").stdout.strip() != b"1":
            raise RuntimeError(f"ADB_TARGET_UNAVAILABLE_FOR_PHASE2C3: {label} is not a connected, booted target")
    MATRIX.project = _new_project(Path(tempfile.mkdtemp(prefix="gpos-adb-matrix-")).resolve() / "p")


def tearDownModule():
    if MATRIX.project is not None:
        shutil.rmtree(MATRIX.project.parent, ignore_errors=True)
    shutil.rmtree(_HOME, ignore_errors=True)


def _new_project(target):
    shutil.copytree(FIXTURE, target)
    return target


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def redact(text):
    """The runner's output filter: physical serials and build fingerprints never reach the log."""
    for label, serial, kind in TARGETS:
        if kind == "usb":
            text = text.replace(serial, f"<{label}_SERIAL_REDACTED>")
    for fingerprint in FINGERPRINTS:
        text = text.replace(fingerprint, "<BUILD_FINGERPRINT_REDACTED>")
    return text


class Recorder:
    """Records every process spec started through the audited boundary while active."""

    def __init__(self, transform=None):
        self.specs = []
        self._original = tproc.run_process
        self._transform = transform

    def __enter__(self):
        def recording(spec, scopes, clock=None):
            self.specs.append(spec)
            outcome = self._original(spec, scopes) if clock is None else self._original(spec, scopes, clock)
            return self._transform(spec, outcome) if self._transform else outcome
        tproc.run_process = recording
        return self

    def __exit__(self, *exc):
        tproc.run_process = self._original

    @property
    def target_runs(self):
        """Specs addressed to a target (everything except `adb version`)."""
        return [s for s in self.specs if "-s" in s.argv]


def request(capability, project, serial, **kwargs):
    kwargs.setdefault("allow_mutation", not kwargs.get("dry_run", False))
    kwargs.setdefault("target_platform", "ANDROID")
    if capability == aa.MEMINFO:
        kwargs.setdefault("inputs", {"package_name": PACKAGE})
    revision = kwargs.pop("revision", REVISION)
    kwargs.setdefault("build_revision", REVISION)
    return ExecutionRequest(adapter_id="adb", capability_id=capability,
                            subject=Subject("TASK", "FEATURE-X", revision), project_root=str(project),
                            device=serial, **kwargs)


class _Matrix:
    """Each authorized target's three real captures, run once and shared by the groups that inspect them."""

    def __init__(self):
        self.project, self.runs, self.registry, self.retries = None, {}, None, 0

    def get(self, serial, capability):
        key = (serial, capability)
        if key not in self.runs:
            if self.registry is None:
                self.registry = default_registry(FW)
            for attempt in range(4):
                if attempt:
                    time.sleep(2)
                with Recorder() as rec:
                    result = execute(self.registry, request(capability, self.project, serial))
                # A busy target (seen on emulators: ~6 s, then a header only) may not answer the memory dump in
                # time. The adapter correctly refuses that (a stand-in test covers it); a real acceptance run
                # retries the whole execution a bounded number of times, and reports how often.
                incomplete = capability == aa.MEMINFO and result.status == tdg.FAILED and \
                    any("no memory totals" in d.message for d in result.diagnostics)
                if not incomplete:
                    break
                self.retries += 1
            self.runs[key] = (result, rec.specs)
            if capability == aa.DEVICE_REPORT and result.ok:
                FINGERPRINTS.add(result.data["build_fingerprint"])
        return self.runs[key]


MATRIX = _Matrix()


def systemui_running(serial):
    return bool(adb_test(serial, "shell", "pidof", PACKAGE).stdout.strip())


class AdbCase(unittest.TestCase):
    registry = None

    @classmethod
    def setUpClass(cls):
        cls.registry = default_registry(FW)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-adb-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def project(self, name="p"):
        target, n = self.tmp / name, 1
        while target.exists():
            n += 1
            target = self.tmp / f"{name}{n}"
        return _new_project(target)

    def run_cap(self, capability, project, serial, registry=None, **kwargs):
        return execute(registry or self.registry, request(capability, project, serial, **kwargs))

    def first(self, kind=None):
        return next(serial for _, serial, k in TARGETS if kind in (None, k))

    def codes(self, result):
        return {d.code for d in result.diagnostics}

    def messages(self, result):
        return " ".join(d.message for d in result.diagnostics)

    def assertSucceeded(self, result):
        self.assertEqual(result.status, tdg.SUCCESS, redact(self.messages(result)))

    def assertRefused(self, result, text=None, code="INVALID_TOOL_REQUEST", status=tdg.INVALID_REQUEST):
        self.assertEqual(result.status, status, redact(self.messages(result)))
        self.assertIn(code, self.codes(result))
        if text:
            self.assertIn(text, self.messages(result))
        self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
        self.assertFalse(result.mutation_performed)

    def workspace_root(self, project):
        return project / ".game" / "gpos-runtime" / "tool-output"


# ---------------------------------------------------------------- stand-in adb (TEST_ONLY)

STAND_IN = """
import json, sys, time
cfg = json.load(open(CONFIG))
argv = sys.argv[1:]
open(CONFIG + ".log", "a").write(json.dumps(argv) + "\\n")
def out(value, code=0, err=""):
    if isinstance(value, dict):
        sys.stdout.buffer.write(bytes.fromhex(value["hex"]))
    else:
        sys.stdout.write(value)
    sys.stderr.write(err)
    sys.stdout.flush()
    sys.exit(code)
if argv == ["version"]:
    out(cfg.get("version", "Android Debug Bridge version 1.0.41\\nVersion 37.0.0-14910828\\n"))
command = " ".join(argv[2:])
time.sleep(cfg.get("sleep", {}).get(command, 0))
if command == "get-state":
    out(cfg.get("state", "device\\n"), cfg.get("state_exit", 0), cfg.get("state_err", ""))
if command == "shell getprop sys.boot_completed":
    out(cfg.get("boot", "1\\n"))
if command == "shell getprop":
    out(cfg.get("getprop", ""))
if command == "exec-out screencap -p":
    out(cfg.get("png", ""))
if command.startswith("shell dumpsys meminfo -s "):
    out(cfg.get("meminfo", ""))
out("", 1, "stand-in: unexpected command")
"""


def png_bytes(width=64, height=32, complete=True):
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    body = ap.PNG_SIGNATURE + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
    body += struct.pack(">I", 4) + b"IDAT" + b"\x00" * 4 + b"\x00\x00\x00\x00"
    return body + (ap.PNG_IEND if complete else b"")


GETPROP_OK = ("[ro.product.manufacturer]: [Acme]\n[ro.product.model]: [Model One]\n[ro.product.device]: [acme1]\n"
              "[ro.product.cpu.abi]: [arm64-v8a]\n[ro.build.version.release]: [15]\n[ro.build.version.sdk]: [35]\n"
              "[ro.build.version.security_patch]: [2024-09-05]\n[ro.build.fingerprint]: [acme/one/1:15/X/1:user/k]\n"
              "[sys.boot_completed]: [1]\n[ro.serialno]: [SECRETSERIAL0001]\n[persist.sys.boot.reason.history]: "
              "[reboot,1\nshutdown,2]\n[net.hostname]: [host-private]\n")
MEMINFO_OK = ("Applications Memory Usage (in Kilobytes):\nUptime: 1 Realtime: 1\n\n** MEMINFO in pid 42 "
              f"[{PACKAGE}] **\n App Summary\n           TOTAL PSS:    98593            TOTAL RSS:   202120\n")


class StandIn:
    def __init__(self, directory, **config):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config = self.dir / "adb-config.json"
        cfg = dict(config)
        for key in ("png",):
            if isinstance(cfg.get(key), (bytes, bytearray)):
                cfg[key] = {"hex": bytes(cfg[key]).hex()}
        self.config.write_text(json.dumps(cfg))
        self.exe = self.dir / "adb"
        self.exe.write_text(f"#!{tproc.interpreter_path()}\nCONFIG = {str(self.config)!r}\n{STAND_IN}")
        self.exe.chmod(0o755)

    def adapter(self, **kwargs):
        return AdbAdapter(which=lambda name: str(self.exe), **kwargs)

    def registry(self, **kwargs):
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(self.adapter(**kwargs))
        return registry

    @property
    def calls(self):
        log = Path(str(self.config) + ".log")
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# ---------------------------------------------------------------- A  registration

class A_Registration(AdbCase):
    def test_production_registry_is_exactly_the_four_adapters(self):
        self.assertEqual(default_registry(FW).adapter_ids(), ["adb", "ffmpeg", "ffprobe", "git"])

    def test_synthetic_stays_out_of_production(self):
        registry = default_registry(FW)
        self.assertFalse(registry.allow_test_only)
        with self.assertRaises(Exception):
            registry.register(SyntheticAdapter())
        self.assertEqual(registry.adapter_ids(), ["adb", "ffmpeg", "ffprobe", "git"])

    def test_descriptor_passes_production_registration_validation(self):
        self.assertEqual(tval.validate_descriptor(FW, aa.DESCRIPTOR, allow_test_only=False), [])

    def test_descriptor_identity(self):
        d = aa.DESCRIPTOR
        self.assertEqual((d.adapter_id, d.tool_family, d.target_tool, d.adapter_kind, d.state_model, d.network),
                         ("adb", "DEVICE", "Android Debug Bridge", "CLI", "STATELESS", "FORBIDDEN"))
        self.assertEqual(d.supported_platforms, ("WINDOWS", "MACOS", "LINUX"))
        self.assertFalse(d.test_only)
        self.assertEqual(d.filesystem_scopes, ())

    def test_exactly_three_capabilities(self):
        caps = {c.id: c for c in aa.DESCRIPTOR.capabilities}
        self.assertEqual(sorted(caps), ["adb.capture-device-report", "adb.capture-meminfo", "adb.capture-screenshot"])
        expected = {aa.DEVICE_REPORT: ("CAPTURE", "TARGET_RUNTIME", (), ("JSON",)),
                    aa.SCREENSHOT: ("CAPTURE", "TARGET_RUNTIME", (), ("IMAGE",)),
                    aa.MEMINFO: ("PROFILE", "PERFORMANCE_RUNTIME", ("package_name",), ("REPORT",))}
        for cap_id, cap in caps.items():
            category, context, inputs, kinds = expected[cap_id]
            self.assertEqual((cap.category, cap.operation_class, cap.state_model, cap.execution_context),
                             (category, "MUTATING", "STATELESS", context))
            self.assertTrue(cap.requires_tool and cap.requires_project and cap.dry_run_supported)
            self.assertFalse(cap.requires_ready_routing or cap.single_writer_required)
            self.assertEqual((cap.input_kinds, cap.artifact_kinds), (inputs, kinds))
            self.assertEqual(cap.potential_evidence, (EVIDENCE[cap_id],))

    def test_agent_and_tool_registries_stay_separate(self):
        from gpos.adapters.backends import BACKENDS
        self.assertNotIn("adb", REG["adapter_ids"])
        self.assertNotIn("adb", BACKENDS)


# ---------------------------------------------------------------- B  real probe

class B_RealProbe(AdbCase):
    def test_real_adb_is_available_with_its_platform_tools_version(self):
        probe = AdbAdapter().probe()
        self.assertEqual(probe.status, tmodel.AVAILABLE, probe.detail)
        self.assertTrue(os.path.isabs(probe.tool_path))
        self.assertEqual(probe.tool_path, str(Path(ADB).resolve()))
        second = subprocess.run([ADB, "version"], capture_output=True, text=True).stdout.splitlines()[1]
        self.assertEqual(f"Version {probe.tool_version}", second.strip())

    def test_the_probe_runs_only_adb_version_and_never_lists_devices(self):
        with Recorder() as rec:
            AdbAdapter().probe()
        self.assertEqual([tuple(s.argv) for s in rec.specs], [("version",)])
        self.assertEqual(rec.target_runs, [])

    def test_the_adb_modules_never_import_subprocess(self):
        for path in sorted((ROOT / "gpos" / "tools" / "adb").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                names = set()
                if isinstance(node, ast.Import):
                    names = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {(node.module or "").split(".")[0]}
                self.assertEqual(names & {"subprocess", "socket", "urllib", "http", "ctypes"}, set(), path)

    def test_process_py_is_still_the_only_subprocess_importer(self):
        importers = set()
        for path in (ROOT / "gpos").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names)) or \
                        (isinstance(node, ast.ImportFrom) and node.module == "subprocess"):
                    importers.add(path.relative_to(ROOT).as_posix())
        self.assertEqual(importers, {"gpos/tools/process.py"})

    def test_missing_or_relative_adb_is_unavailable(self):
        probe = AdbAdapter(which=lambda n: None).probe()
        self.assertEqual((probe.status, probe.tool_path), (tmodel.UNAVAILABLE, None))
        self.assertIn("relative PATH entry", AdbAdapter(which=lambda n: "bin/adb").probe().detail)
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(AdbAdapter(which=lambda n: None))
        with Recorder() as rec:
            result = self.run_cap(aa.DEVICE_REPORT, self.project(), self.first(), registry=registry)
        self.assertEqual(result.status, tdg.UNAVAILABLE)
        self.assertEqual(rec.specs, [])

    def test_an_unrecognized_version_is_never_fabricated(self):
        for text in ("adb 1.0\n", "Android Debug Bridge version 1.0.41\n",
                     "Android Debug Bridge version 1.0.41\nVersion $(id)\n", ""):
            stand_in = StandIn(self.tmp / f"v{len(text)}", version=text)
            probe = stand_in.adapter().probe()
            self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED, text)
            self.assertIsNone(probe.tool_version)

    def test_version_parsing(self):
        self.assertEqual(aa.parse_version(b"Android Debug Bridge version 1.0.41\nVersion 37.0.0-14910828\n"
                                          b"Installed as /x/adb\n"), ("1.0.41", "37.0.0-14910828"))
        self.assertEqual(aa.parse_version(b"Android Debug Bridge version 1.0.39\nVersion 0.0.1-4500957\n"),
                         ("1.0.39", "0.0.1-4500957"))
        self.assertEqual(aa.parse_version("not bytes"), (None, None))


# ---------------------------------------------------------------- C  target selection

BAD_SERIALS = ["", " ", "emulator-5554 ", " emulator-5554", "a b", "a;id", "a|id", "a&id", "$(id)", "`id`", "a'b",
               'a"b', "../x", "/dev/bus/usb/001", "a\\b", "-d", "-e", "--help", "a\nb", "a\tb", "a" * 65,
               "192.168.1.5:5555", "localhost:5555", "[::1]:5555", "usb:1-1", "10.0.2.2",
               "adb-ABCDEF123456-XyZ._adb-tls-connect._tcp", "émulateur", None, 5554]


class C_TargetSelection(AdbCase):
    def test_request_device_is_required(self):
        p = self.project()
        for cap in CAPS:
            with Recorder() as rec:
                for dry in (False, True):
                    self.assertRefused(self.run_cap(cap, p, None, dry_run=dry), "never inferred")
            self.assertEqual(rec.target_runs, [])

    def test_target_platform_android_is_required_and_never_inferred(self):
        p = self.project()
        for cap in CAPS:
            with Recorder() as rec:
                for platform in (None, "IOS", "LINUX"):
                    self.assertRefused(self.run_cap(cap, p, self.first(), target_platform=platform),
                                       "requires target_platform ANDROID")
            self.assertEqual(rec.target_runs, [])
        result = self.run_cap(aa.DEVICE_REPORT, p, self.first(), target_platform="android")
        self.assertEqual(result.status, tdg.INVALID_REQUEST)  # vocabulary is exact, never normalized

    def test_invalid_and_network_serials_are_refused_before_any_target_command(self):
        p = self.project()
        with Recorder() as rec:
            for serial in BAD_SERIALS:
                for dry in (False, True):
                    result = self.run_cap(aa.SCREENSHOT, p, serial, dry_run=dry)
                    self.assertEqual(result.status, tdg.INVALID_REQUEST, repr(serial))
        self.assertEqual(rec.target_runs, [])

    def test_a_capture_consumes_no_input_artifact(self):
        p = self.project()
        existing = p / "captures.png"
        existing.write_bytes(b"x")
        with Recorder() as rec:
            for cap in CAPS:
                result = self.run_cap(cap, p, self.first(), input_artifacts=(InputArtifact("src", str(existing),
                                                                                           "TARGET_RUNTIME"),))
                self.assertRefused(result, "consumes no input artifact")
        self.assertEqual(rec.target_runs, [])

    def test_serial_grammar(self):
        for serial in ("emulator-5554", "emulator-5584", "0123456789ABCDEF", "R58M123ABC", "a", "A_b-9"):
            self.assertIsNone(ap.serial_problem(serial), serial)
        for serial in BAD_SERIALS:
            self.assertIsNotNone(ap.serial_problem(serial), repr(serial))
        self.assertIn("wireless", ap.serial_problem("192.168.1.5:5555"))

    def test_every_target_command_is_bound_to_exactly_the_requested_serial(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                for cap in CAPS:
                    if cap == aa.MEMINFO and not systemui_running(serial):
                        continue
                    result, specs = MATRIX.get(serial, cap)
                    self.assertSucceeded(result)
                    target_specs = [s for s in specs if "-s" in s.argv]
                    self.assertGreaterEqual(len(target_specs), 3)
                    for spec in target_specs:
                        self.assertEqual(tuple(spec.argv[:2]), ("-s", serial))
                        self.assertNotIn("-d", spec.argv)
                        self.assertNotIn("-e", spec.argv)
                    others = {s for _, s, _ in TARGETS} - {serial}
                    self.assertFalse(any(o in spec.argv for spec in specs for o in others))  # no cross-device capture
                    self.assertEqual(result.provenance.device, serial)
                    self.assertEqual(result.provenance.target_platform, "ANDROID")

    def test_android_serial_in_the_environment_is_never_used(self):
        if len(TARGETS) < 2:
            self.skipTest("needs two authorized targets")
        (_, a, _), (_, b, _) = TARGETS[0], TARGETS[1]
        p = self.project()
        previous = os.environ.get("ANDROID_SERIAL")
        os.environ["ANDROID_SERIAL"] = b
        try:
            with Recorder() as rec:
                result = self.run_cap(aa.DEVICE_REPORT, p, a)
                missing = self.run_cap(aa.DEVICE_REPORT, p, None)
        finally:
            if previous is None:
                os.environ.pop("ANDROID_SERIAL", None)
            else:
                os.environ["ANDROID_SERIAL"] = previous
        self.assertSucceeded(result)
        self.assertEqual(result.provenance.device, a)
        self.assertRefused(missing)
        for spec in rec.specs:
            self.assertNotIn("ANDROID_SERIAL", spec.env.build(os.environ | {"ANDROID_SERIAL": b}))
            self.assertNotIn(b, spec.argv)


# ---------------------------------------------------------------- D  readiness

class D_Readiness(AdbCase):
    def test_every_real_capture_checks_state_then_boot_first(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                result, specs = MATRIX.get(serial, aa.DEVICE_REPORT)
                target_specs = [tuple(s.argv) for s in specs if "-s" in s.argv]
                self.assertEqual(target_specs[:2], [aa.state_argv(serial), aa.boot_argv(serial)])

    def test_an_unknown_serial_is_unavailable_and_nothing_else_is_used(self):
        p = self.project()
        with Recorder() as rec:
            result = self.run_cap(aa.SCREENSHOT, p, "GPOSNOSUCHDEVICE0001")
        self.assertRefused(result, "is not connected", code="TARGET_DEVICE_UNAVAILABLE", status=tdg.UNAVAILABLE)
        self.assertEqual([tuple(s.argv) for s in rec.target_runs], [aa.state_argv("GPOSNOSUCHDEVICE0001")])
        self.assertEqual(list(self.workspace_root(p).rglob("*.png")), [])

    def test_unauthorized_offline_bootloader_or_booting_targets_fail_closed(self):
        cases = {"unauthorized": dict(state="", state_exit=1, state_err="error: device unauthorized.\n"),
                 "offline": dict(state="offline\n"), "bootloader": dict(state="bootloader\n"),
                 "booting": dict(boot="0\n"), "no boot property": dict(boot="\n"),
                 "state noise": dict(state="device extra\n")}
        for name, config in cases.items():
            with self.subTest(case=name):
                stand_in = StandIn(self.tmp / name.replace(" ", "-"), getprop=GETPROP_OK, png=png_bytes(),
                                   meminfo=MEMINFO_OK, **config)
                for cap in CAPS:
                    result = self.run_cap(cap, self.project(), "emulator-5554", registry=stand_in.registry())
                    self.assertRefused(result, code="TARGET_DEVICE_NOT_READY", status=tdg.CONFLICT)
                captured = [c for c in stand_in.calls if c[2:3] in (["exec-out"],) or c[2:4] == ["shell", "dumpsys"]
                            or c[2:] == ["shell", "getprop"]]
                self.assertEqual(captured, [])


# ---------------------------------------------------------------- E  device report

class E_DeviceReport(AdbCase):
    def test_every_target_gives_a_normalized_device_report(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                result, _ = MATRIX.get(serial, aa.DEVICE_REPORT)
                self.assertSucceeded(result)
                (art,) = result.artifacts
                self.assertEqual((art.artifact_id, art.kind, art.media_type, art.classification, art.origin_capture_context,
                                  art.derived_from, art.complete),
                                 ("device-report", "JSON", "application/json", "CANONICAL", "TARGET_RUNTIME", (), True))
                body = Path(art.absolute_path).read_bytes()
                report = json.loads(body)
                self.assertEqual(set(report), REPORT_KEYS)
                self.assertEqual(report, result.data)
                self.assertEqual(body, (json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode())
                self.assertIsInstance(report["api_level"], int)
                self.assertTrue(1 <= report["api_level"] <= 1000)
                self.assertIs(report["boot_completed"], True)
                for key, prop in ap.REPORT_PROPERTIES.items():
                    if key in ("api_level", "boot_completed"):
                        continue
                    actual = adb_test(serial, "shell", "getprop", prop).stdout.decode().strip()
                    self.assertEqual(report[key], actual, key)  # compared, never printed
                self.assertEqual(str(report["api_level"]),
                                 adb_test(serial, "shell", "getprop", "ro.build.version.sdk").stdout.decode().strip())
                (cand,) = result.evidence_candidates
                self.assertEqual((cand.evidence_type, cand.capture_context, cand.artifact_ids, cand.derived_from),
                                 ("DEVICE_EVIDENCE", "TARGET_RUNTIME", ("device-report",), ()))

    def test_the_matrix_spans_the_authorized_api_levels_and_target_kinds(self):
        kinds = {kind for _, _, kind in TARGETS}
        if kinds != {"usb", "emulator"}:
            self.skipTest("the configured targets are not a physical + emulator matrix")
        levels = {MATRIX.get(serial, aa.DEVICE_REPORT)[0].data["api_level"] for _, serial, _ in TARGETS}
        self.assertGreaterEqual(len(levels), 2, "the matrix must cover more than one API level")


# ---------------------------------------------------------------- F  device report privacy

SENSITIVE = re.compile(r"(serial|imei|meid|iccid|android_id|wifi|wlan|bluetooth|bt\.|mac|net\.|dhcp|ip|hostname|"
                       r"account|user|phone|gsm|ril\.|sim|operator|ssid|location)", re.I)


class F_ReportPrivacy(AdbCase):
    def test_no_sensitive_property_value_reaches_the_report_or_the_result(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                result, _ = MATRIX.get(serial, aa.DEVICE_REPORT)
                body = Path(result.artifacts[0].absolute_path).read_text()
                public = json.dumps(result.to_dict())
                records = ap.parse_getprop(adb_test(serial, "shell", "getprop").stdout)
                allowed = set(ap.REPORT_PROPERTIES.values())
                reported = {str(v) for v in result.data.values()}
                leaked = []
                for name, value in records.items():
                    text = value.decode("utf-8", "replace")
                    if name in allowed or len(text) < 6 or text in reported or not SENSITIVE.search(name):
                        continue
                    if text in body or text in json.dumps(result.data):
                        leaked.append(name)
                self.assertEqual(leaked, [])  # property names only; values are never printed
                if not serial.startswith("emulator-"):
                    self.assertNotIn(serial, body)
                self.assertNotIn("[ro.", body + public)  # no raw getprop record

    def test_unexpected_properties_are_never_copied(self):
        report = ap.device_report(GETPROP_OK.encode())
        self.assertEqual(set(report), REPORT_KEYS)
        text = json.dumps(report)
        for secret in ("SECRETSERIAL0001", "host-private", "shutdown,2"):
            self.assertNotIn(secret, text)

    def test_the_runner_output_filter_hides_serials_and_fingerprints(self):
        for label, serial, kind in TARGETS:
            fingerprint = MATRIX.get(serial, aa.DEVICE_REPORT)[0].data["build_fingerprint"]
            text = redact(f"{serial} {fingerprint}")
            self.assertNotIn(fingerprint, text)
            if kind == "usb":
                self.assertNotIn(serial, text)
                self.assertIn(f"<{label}_SERIAL_REDACTED>", text)


# ---------------------------------------------------------------- G  screenshot

class G_Screenshot(AdbCase):
    def test_every_target_gives_a_streamed_png_offering_visual_evidence(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                result, specs = MATRIX.get(serial, aa.SCREENSHOT)
                self.assertSucceeded(result)
                (art,) = result.artifacts
                self.assertEqual((art.artifact_id, art.kind, art.media_type, art.classification, art.origin_capture_context),
                                 ("screenshot", "IMAGE", "image/png", "CANONICAL", "TARGET_RUNTIME"))
                data = Path(art.absolute_path).read_bytes()  # structure only: never displayed, decoded or inspected
                self.assertTrue(data.startswith(ap.PNG_SIGNATURE) and data.endswith(ap.PNG_IEND))
                width, height = struct.unpack(">II", data[16:24])
                self.assertEqual((width, height), (result.data["width"], result.data["height"]))
                self.assertTrue(100 <= width <= 16384 and 100 <= height <= 16384)
                self.assertEqual((art.bytes, result.data["bytes"]), (len(data), len(data)))
                self.assertEqual(art.sha256, hashlib.sha256(data).hexdigest())
                (cand,) = result.evidence_candidates
                self.assertEqual((cand.evidence_type, cand.capture_context), ("VISUAL_EVIDENCE", "TARGET_RUNTIME"))
                self.assertNotIn("DEVICE_EVIDENCE", {c.evidence_type for c in result.evidence_candidates})
                capture = [tuple(s.argv) for s in specs if "screencap" in s.argv]
                self.assertEqual(capture, [aa.screencap_argv(serial)])  # streamed; no device file, no pull
                self.assertFalse(any("/sdcard" in a or "/data" in a for s in specs for a in s.argv))


# ---------------------------------------------------------------- H  screenshot truncation

class H_ScreenshotTruncation(AdbCase):
    def assertNoScreenshot(self, result, project, text):
        self.assertEqual(result.status, tdg.FAILED, redact(self.messages(result)))
        self.assertIn(text, self.messages(result))
        self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
        self.assertEqual(list(self.workspace_root(project).rglob("*.png")), [])

    def test_a_real_screenshot_over_the_capture_bound_is_refused(self):
        p = self.project()
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(AdbAdapter(capture_bytes={"screenshot": 4096}))
        result = self.run_cap(aa.SCREENSHOT, p, self.first(), registry=registry)
        self.assertNoScreenshot(result, p, "capture bound")
        self.assertTrue(result.output_truncated)

    def test_incomplete_or_invalid_png_streams_are_refused(self):
        cases = {"no IEND": png_bytes(complete=False), "not png": b"GIF89a" + b"\x00" * 64, "empty": b"",
                 "bad IHDR": ap.PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDX" + b"\x00" * 60 + ap.PNG_IEND,
                 "zero width": png_bytes(width=0), "huge": png_bytes(width=20000)}
        for name, data in cases.items():
            with self.subTest(case=name):
                stand_in = StandIn(self.tmp / name.replace(" ", "-"), png=data)
                p = self.project()
                self.assertNoScreenshot(self.run_cap(aa.SCREENSHOT, p, "emulator-5554", registry=stand_in.registry()),
                                        p, "not a complete PNG")

    def test_a_timeout_leaves_nothing(self):
        stand_in = StandIn(self.tmp / "slow", png=png_bytes(), sleep={"exec-out screencap -p": 30})
        p = self.project()
        result = self.run_cap(aa.SCREENSHOT, p, "emulator-5554", registry=stand_in.registry(), timeout=2.0)
        self.assertEqual(result.status, tdg.TIMED_OUT)
        self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
        self.assertLess(result.duration_seconds, 20)


# ---------------------------------------------------------------- I  meminfo

class I_Meminfo(AdbCase):
    def test_each_target_running_the_fixed_package_gives_a_memory_snapshot(self):
        succeeded = {"usb": 0, "emulator": 0}
        for label, serial, kind in TARGETS:
            with self.subTest(target=label):
                if not systemui_running(serial):
                    self.skipTest(f"{label}: {PACKAGE} is not running on this target (never launched by tests)")
                result, specs = MATRIX.get(serial, aa.MEMINFO)
                self.assertSucceeded(result)
                (art,) = result.artifacts
                self.assertEqual((art.artifact_id, art.kind, art.media_type, art.origin_capture_context),
                                 ("meminfo", "REPORT", "text/plain", "PERFORMANCE_RUNTIME"))
                text = Path(art.absolute_path).read_text(encoding="ascii")  # checked, never printed
                self.assertLessEqual(len(text), aa.CAPTURE_BYTES["meminfo"])
                self.assertNotIn("\r", text)
                headers = re.findall(r"\*\* MEMINFO in pid ([0-9]+) \[([^\]]+)\] \*\*", text)
                self.assertEqual([name for _, name in headers], [PACKAGE])
                self.assertEqual(result.data["package_name"], PACKAGE)
                self.assertEqual(str(result.data["pid"]), headers[0][0])
                (cand,) = result.evidence_candidates
                self.assertEqual((cand.evidence_type, cand.capture_context), ("PERFORMANCE_EVIDENCE", "PERFORMANCE_RUNTIME"))
                self.assertEqual([tuple(s.argv) for s in specs if "dumpsys" in s.argv], [aa.meminfo_argv(serial, PACKAGE)])
                succeeded[kind] += 1
        for kind in {k for _, _, k in TARGETS}:
            self.assertGreaterEqual(succeeded[kind], 1, f"no {kind} target completed the real meminfo test")


# ---------------------------------------------------------------- J  package validation

BAD_PACKAGES = ["", " ", "com.android.systemui ", "com android", "com/android", "com\\android", "com.android:ui",
                "com.android;id", "com.android|id", "com.android&id", "com.'x'", 'com."x"', "com.$(id)", "com.`id`",
                "-s", "--oom", "-com.x", "systemui", "com..x", "com.1x", "com.x.", ".com.x", "com.x\n",
                "a." + "b" * 300, None, 7, ["com.x"]]


class J_PackageValidation(AdbCase):
    def test_bad_package_names_are_refused_before_any_target_command(self):
        p = self.project()
        with Recorder() as rec:
            for value in BAD_PACKAGES:
                for dry in (False, True):
                    result = self.run_cap(aa.MEMINFO, p, self.first(), dry_run=dry, inputs={"package_name": value})
                    self.assertEqual(result.status, tdg.INVALID_REQUEST, repr(value))
            self.assertRefused(self.run_cap(aa.MEMINFO, p, self.first(), inputs={}), "package_name is required")
        self.assertEqual(rec.target_runs, [])

    def test_package_grammar(self):
        for good in (PACKAGE, "com.example.game", "a.b", "com.Example_1.x9"):
            self.assertIsNone(ap.package_problem(good), good)
        for bad in BAD_PACKAGES:
            self.assertIsNotNone(ap.package_problem(bad), repr(bad))

    def test_no_other_input_reaches_a_capture(self):
        p = self.project()
        with Recorder() as rec:
            for cap, name in ((aa.MEMINFO, "service"), (aa.MEMINFO, "argv"), (aa.SCREENSHOT, "path"),
                              (aa.DEVICE_REPORT, "properties"), (aa.SCREENSHOT, "package_name")):
                result = self.run_cap(cap, p, self.first(), inputs={name: "x"})
                self.assertEqual(result.status, tdg.INVALID_REQUEST, (cap, name))
        self.assertEqual(rec.target_runs, [])


# ---------------------------------------------------------------- K  package not running

class K_PackageNotRunning(AdbCase):
    def test_a_package_that_is_not_running_is_a_conflict_without_evidence(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                p = self.project()
                result = self.run_cap(aa.MEMINFO, p, serial, inputs={"package_name": NOT_RUNNING})
                self.assertRefused(result, "no memory snapshot exists", code="TARGET_PROCESS_NOT_RUNNING",
                                   status=tdg.CONFLICT)
                self.assertFalse(result.data)
                self.assertEqual(list(self.workspace_root(p).rglob("meminfo.txt")), [])

    def test_output_about_another_or_several_processes_is_refused(self):
        other = MEMINFO_OK.replace(f"[{PACKAGE}]", "[com.android.phone]")
        cases = {"other process": other, "two processes": MEMINFO_OK + MEMINFO_OK.split("\n\n", 1)[1],
                 "no totals": MEMINFO_OK.replace("TOTAL PSS", "TOTAL XSS"), "empty": "",
                 "binary": "\x00\x01\x02", "other missing": "No process found for: com.other.pkg\n",
                 "zeros invented": f"** MEMINFO in pid 0 [{PACKAGE}] **\n"}
        for name, text in cases.items():
            with self.subTest(case=name):
                stand_in = StandIn(self.tmp / name.replace(" ", "-"), meminfo=text)
                p = self.project()
                result = self.run_cap(aa.MEMINFO, p, "emulator-5554", registry=stand_in.registry())
                self.assertEqual(result.status, tdg.FAILED, name)
                self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
                self.assertEqual(list(self.workspace_root(p).rglob("meminfo.txt")), [])


# ---------------------------------------------------------------- L  contexts

class L_Contexts(AdbCase):
    def test_each_capture_offers_exactly_its_evidence_in_its_context(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                for cap in CAPS:
                    if cap == aa.MEMINFO and not systemui_running(serial):
                        continue
                    result, _ = MATRIX.get(serial, cap)
                    self.assertEqual([(c.evidence_type, c.capture_context) for c in result.evidence_candidates],
                                     [EVIDENCE[cap]])
                    self.assertEqual(result.provenance.execution_context, EVIDENCE[cap][1])

    def test_no_capability_can_offer_other_evidence_types(self):
        forbidden = {"HUMAN_EVIDENCE", "RUNTIME_EVIDENCE", "MOTION_EVIDENCE", "AUDIO_EVIDENCE", "PERSISTENCE_EVIDENCE",
                     "CODE_EVIDENCE", "TEST_EVIDENCE"}
        for cap in aa.DESCRIPTOR.capabilities:
            self.assertEqual({t for t, _ in cap.potential_evidence} & forbidden, set(), cap.id)
            for etype, context in cap.potential_evidence:
                self.assertIn(context, REG["evidence_context_compatibility"][etype])


# ---------------------------------------------------------------- M  materialization

class M_Materialization(AdbCase):
    def test_every_real_capture_materializes_a_schema_valid_record(self):
        version = AdbAdapter().probe().tool_version
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                for cap in CAPS:
                    if cap == aa.MEMINFO and not systemui_running(serial):
                        continue
                    result, _ = MATRIX.get(serial, cap)
                    (cand,) = result.evidence_candidates
                    self.assertTrue(cand.materializable)
                    extra = {"instrumentation": result.data["instrumentation"]} if cap == aa.MEMINFO else None
                    record, problems = tev.materialize(FW, cand, result.artifacts, f"EV-ADB-{label}", extra_provenance=extra)
                    self.assertEqual(problems, [], redact(str(problems)))
                    self.assertEqual(FW.validators["evidence"].errors(record), [])
                    self.assertEqual(record["type"], EVIDENCE[cap][0])
                    prov = record["provenance"]
                    self.assertEqual((prov["capture_context"], prov["subject_revision"], prov["build_revision"],
                                      prov["target_platform"], prov["device"], prov["tool_version"]),
                                     (EVIDENCE[cap][1], REVISION, REVISION, "ANDROID", serial, version))
                    art = result.artifacts[0]
                    self.assertEqual(record["artifacts"][0]["hash"], f"sha256:{sha256(art.absolute_path)}")
                    text = json.dumps(record)
                    for word in ("PASS", "gate", "reviewer", "assessor", "approved", "HUMAN_EVIDENCE"):
                        self.assertNotIn(word, text)

    def test_meminfo_needs_its_instrumentation_declared_by_the_caller(self):
        serial = next((s for _, s, _ in TARGETS if systemui_running(s)), None)
        if serial is None:
            self.skipTest(f"no target runs {PACKAGE}")
        result, _ = MATRIX.get(serial, aa.MEMINFO)
        record, problems = tev.materialize(FW, result.evidence_candidates[0], result.artifacts, "EV-ADB-X")
        self.assertIsNone(record)
        self.assertTrue(problems)
        self.assertEqual(result.data["instrumentation"]["timing_impact"], "UNKNOWN")

    def test_without_a_build_revision_a_runtime_record_cannot_be_made(self):
        p = self.project()
        result = self.run_cap(aa.DEVICE_REPORT, p, self.first(), build_revision=None)
        self.assertSucceeded(result)
        self.assertIn("build_revision", result.provenance.to_dict()["unknown"])
        record, problems = tev.materialize(FW, result.evidence_candidates[0], result.artifacts, "EV-ADB-Y")
        self.assertIsNone(record)


# ---------------------------------------------------------------- N  explicit Git handoff (CLI)

def cli(*argv):
    from gpos.tools import cli as tool_cli
    buffer = io.StringIO()
    code = tool_cli.main(list(argv), stdout=buffer)
    return code, buffer.getvalue()


@unittest.skipIf(GIT is None, "the handoff needs Git")
class N_GitHandoff(AdbCase):
    def repo(self):
        p = self.project()
        env = FIXTURE_ENV
        for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            subprocess.run([GIT, *args], cwd=p, env=env, check=True, capture_output=True)
        return p

    def adb_cli(self, p, serial, *extra):
        code, out = cli("execute", "--adapter", "adb", "--capability", aa.DEVICE_REPORT, "--project", str(p),
                        "--subject-ref", "FEATURE-X", "--target-platform", "ANDROID", "--device", serial,
                        "--allow-mutation", "--format", "json", *extra)
        return code, json.loads(out)["result"]

    def test_git_revision_through_the_cli_is_recorded_exactly(self):
        p = self.repo()
        code, out = cli("execute", "--adapter", "git", "--capability", ga.RESOLVE_PROVENANCE, "--project", str(p),
                        "--subject-ref", "FEATURE-X", "--format", "json")
        self.assertEqual(code, 0)
        revision = json.loads(out)["result"]["data"]["repository_revision"]
        self.assertTrue(re.fullmatch(r"[0-9a-f]{40}", revision))
        serial = self.first()
        with Recorder() as rec:
            code, result = self.adb_cli(p, serial, "--subject-revision", revision, "--build-revision", revision)
        self.assertEqual(code, 0, redact(json.dumps(result["diagnostics"])))
        prov = result["provenance"]
        self.assertEqual((prov["build_revision"], prov["target_platform"], prov["device"]), (revision, "ANDROID", serial))
        cand = result["evidence_candidates"][0]
        self.assertEqual(cand["provenance"]["build_revision"], revision)
        self.assertTrue(cand["materializable"])
        self.assertEqual({Path(s.executable).name for s in rec.specs}, {"adb"})  # ADB never called Git

    def test_without_the_handoff_the_revision_stays_unknown(self):
        p = self.repo()
        code, result = self.adb_cli(p, self.first())
        self.assertEqual(code, 0)
        self.assertIn("build_revision", result["provenance"]["unknown"])
        self.assertNotIn("build_revision", result["provenance"])

    def test_the_adb_modules_never_reference_git(self):
        for path in (ROOT / "gpos" / "tools" / "adb").glob("*.py"):
            text = path.read_text()
            self.assertNotIn("from ..git", text)
            self.assertNotIn("rev-parse", text)


# ---------------------------------------------------------------- O  mutation consent

class O_MutationConsent(AdbCase):
    def test_no_capture_without_consent(self):
        p = self.project()
        with Recorder() as rec:
            for cap in CAPS:
                result = self.run_cap(cap, p, self.first(), allow_mutation=False)
                self.assertRefused(result, code="MUTATION_NOT_ALLOWED")
        self.assertEqual(rec.target_runs, [])
        self.assertFalse(self.workspace_root(p).exists())


# ---------------------------------------------------------------- P  dry run

class P_DryRun(AdbCase):
    def test_each_capture_plans_without_contacting_the_target(self):
        p = self.project()
        before = sorted(x.relative_to(p).as_posix() for x in p.rglob("*"))
        with Recorder() as rec:
            for cap in CAPS:
                for serial in (self.first(), "GPOSNOSUCHDEVICE0001"):  # an absent target plans identically
                    result = self.run_cap(cap, p, serial, dry_run=True)
                    self.assertSucceeded(result)
                    self.assertEqual((result.artifacts, result.evidence_candidates, result.mutation_performed),
                                     ((), (), False))
                    self.assertIn("MUTATION_SKIPPED_DRY_RUN", self.codes(result))
                    self.assertEqual(len(result.plan), 3)
                    self.assertTrue(result.plan[0].startswith("would check"))
                    self.assertIn("not checked", result.plan[2])
                    for line in result.plan:
                        for claim in ("is connected", "is booted", "is running"):
                            self.assertNotIn(claim, line)
        self.assertEqual(rec.target_runs, [])
        self.assertEqual(sorted(x.relative_to(p).as_posix() for x in p.rglob("*")), before)
        self.assertFalse(self.workspace_root(p).exists())


# ---------------------------------------------------------------- Q  output collision

class Q_OutputCollision(AdbCase):
    def test_an_existing_output_is_refused_in_dry_run_and_real_run(self):
        p = self.project()
        out = p / "reviews"
        out.mkdir()
        for cap in CAPS:
            existing = out / FILES[cap]
            existing.write_bytes(b"an existing file the adapter must not touch")
            before = sha256(existing)
            with Recorder() as rec:
                for dry in (True, False):
                    self.assertRefused(self.run_cap(cap, p, self.first(), output_dir=str(out), dry_run=dry),
                                       f"already holds {FILES[cap]}")
            self.assertEqual(rec.target_runs, [])
            self.assertEqual(sha256(existing), before)

    def test_a_dangling_symlink_is_refused(self):
        p = self.project()
        out = p / "reviews"
        out.mkdir()
        (out / "screenshot.png").symlink_to(p / "elsewhere.png")
        with Recorder() as rec:
            for dry in (True, False):
                self.assertRefused(self.run_cap(aa.SCREENSHOT, p, self.first(), output_dir=str(out), dry_run=dry))
        self.assertEqual(rec.target_runs, [])
        self.assertFalse((p / "elsewhere.png").exists())


# ---------------------------------------------------------------- R  target immutability

class R_TargetImmutability(AdbCase):
    def test_every_command_sent_to_every_target_is_a_read_only_template(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                allowed = {aa.state_argv(serial), aa.boot_argv(serial), aa.getprop_argv(serial), aa.screencap_argv(serial),
                           aa.meminfo_argv(serial, PACKAGE)}
                for cap in CAPS:
                    if cap == aa.MEMINFO and not systemui_running(serial):
                        continue
                    for spec in MATRIX.get(serial, cap)[1]:
                        argv = tuple(spec.argv)
                        self.assertTrue(argv == aa.VERSION_ARGV or argv in allowed, redact(str(argv)))
                        self.assertEqual(set(argv) & FORBIDDEN_FAMILIES, set())

    def test_no_template_contains_a_mutating_family(self):
        for argv in self.templates():
            self.assertEqual(set(argv) & FORBIDDEN_FAMILIES, set(), argv)
            if "shell" in argv:
                self.assertIn(argv[argv.index("shell") + 1], {"getprop", "dumpsys"})
            if "dumpsys" in argv:
                self.assertEqual(argv[argv.index("dumpsys"):argv.index("dumpsys") + 3], ("dumpsys", "meminfo", "-s"))

    @staticmethod
    def templates():
        return (aa.VERSION_ARGV, aa.state_argv("S"), aa.boot_argv("S"), aa.getprop_argv("S"), aa.screencap_argv("S"),
                aa.meminfo_argv("S", "com.x.y"))


# ---------------------------------------------------------------- S  command surface

class S_CommandSurface(AdbCase):
    def test_the_authorized_templates_are_exact(self):
        self.assertEqual(aa.VERSION_ARGV, ("version",))
        self.assertEqual(aa.state_argv("S"), ("-s", "S", "get-state"))
        self.assertEqual(aa.boot_argv("S"), ("-s", "S", "shell", "getprop", "sys.boot_completed"))
        self.assertEqual(aa.getprop_argv("S"), ("-s", "S", "shell", "getprop"))
        self.assertEqual(aa.screencap_argv("S"), ("-s", "S", "exec-out", "screencap", "-p"))
        self.assertEqual(aa.meminfo_argv("S", "P"), ("-s", "S", "shell", "dumpsys", "meminfo", "-s", "P"))

    def test_the_command_surface_is_exactly_these_templates(self):
        tree = ast.parse((ROOT / "gpos" / "tools" / "adb" / "adapter.py").read_text())
        templates = sorted(n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.endswith("_argv"))
        self.assertEqual(templates, ["boot_argv", "getprop_argv", "meminfo_argv", "screencap_argv", "state_argv"])
        constants = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertEqual(constants & FORBIDDEN_FAMILIES, set())
        self.assertEqual({c for c in constants if c.startswith("/")}, set())  # no device-side path

    def test_the_constructor_exposes_no_executable_or_argv_seam_to_a_request(self):
        import inspect
        self.assertEqual(list(inspect.signature(AdbAdapter).parameters), ["which", "capture_bytes"])
        self.assertEqual(set(ExecutionRequest.__dataclass_fields__) & {"executable", "argv", "tool_path", "command",
                                                                      "serial", "adb_serial"}, set())

    def test_the_adapter_uses_no_shell_and_reads_only_its_declared_inputs(self):
        text = (ROOT / "gpos" / "tools" / "adb" / "adapter.py").read_text()
        for needle in ("shell=True", "os.system", "os.popen", "Popen", "os.exec", "os.spawn"):
            self.assertNotIn(needle, text)
        self.assertEqual(re.findall(r"request\.inputs[^)]*\)\.get\(\"(\w+)\"\)", text), ["package_name"])
        self.assertEqual(set(re.findall(r"\brequest\.(\w+)", text)),
                         {"capability_id", "target_platform", "device", "inputs"})  # nothing else is read

    def test_every_executable_run_is_the_probed_adb(self):
        tool = AdbAdapter().probe().tool_path
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                for spec in MATRIX.get(serial, aa.DEVICE_REPORT)[1]:
                    self.assertEqual(spec.executable, tool)


# ---------------------------------------------------------------- T  network and wireless

class T_NetworkAndWireless(AdbCase):
    def test_wireless_serials_are_refused_by_name(self):
        p = self.project()
        for serial in ("192.168.1.5:5555", "adb-ABC123-xyz._adb-tls-connect._tcp", "localhost:5555"):
            self.assertRefused(self.run_cap(aa.DEVICE_REPORT, p, serial), "wireless ADB is not supported")

    def test_no_network_command_and_no_auto_connect(self):
        for argv in R_TargetImmutability.templates():
            self.assertEqual(set(argv) & {"connect", "disconnect", "pair", "tcpip", "mdns", "forward", "reverse"}, set())
        self.assertEqual(aa.ENVIRONMENT, (("ADB_MDNS_AUTO_CONNECT", "0"),))
        parent = {"ANDROID_SERIAL": "x", "ADB_TRACE": "all", "ADB_VENDOR_KEYS": "/k", "ANDROID_ADB_SERVER_PORT": "1",
                  "ADB_MDNS_AUTO_CONNECT": "adb-tls-connect", "PATH": "/bin", "HOME": "/h"}
        env = aa.ENVIRONMENT_POLICY.build(parent)
        for name in ("ANDROID_SERIAL", "ADB_TRACE", "ADB_VENDOR_KEYS", "ANDROID_ADB_SERVER_PORT"):
            self.assertNotIn(name, env)
        self.assertEqual(env["ADB_MDNS_AUTO_CONNECT"], "0")
        self.assertEqual(aa.DESCRIPTOR.network, "FORBIDDEN")


# ---------------------------------------------------------------- U  public output

class U_PublicOutput(AdbCase):
    def test_no_target_payload_reaches_the_result(self):
        for label, serial, _ in TARGETS:
            with self.subTest(target=label):
                for cap in CAPS:
                    if cap == aa.MEMINFO and not systemui_running(serial):
                        continue
                    result, _ = MATRIX.get(serial, cap)
                    self.assertEqual((result.stdout, result.stderr), ("", ""))
                    self.assertGreater(result.stdout_bytes, 0)  # the byte count is kept
                    public = json.dumps(result.to_dict())
                    for needle in ("[ro.", "[sys.boot_completed]", "MEMINFO in pid", "App Summary", "IHDR", "raw_stdout",
                                   "\\u0089PNG"):
                        self.assertNotIn(needle, public, cap)


# ---------------------------------------------------------------- V  parsers

class V_Parsers(AdbCase):
    def test_multi_line_values_crlf_and_unknown_properties(self):
        records = ap.parse_getprop(GETPROP_OK.replace("\n", "\r\n").encode())
        self.assertEqual(records["persist.sys.boot.reason.history"], b"reboot,1\nshutdown,2")
        self.assertEqual(ap.device_report(GETPROP_OK.encode())["api_level"], 35)

    def test_malformed_getprop_fails_closed(self):
        def without(prop):
            return "\n".join(l for l in GETPROP_OK.splitlines() if not l.startswith(f"[{prop}]")).encode()
        bad = {"stray line": b"hello\n" + GETPROP_OK.encode(), "duplicate": (GETPROP_OK + "[ro.product.model]: [X]\n").encode(),
               "unterminated": GETPROP_OK.encode() + b"[ro.x]: [open", "missing model": without("ro.product.model"),
               "missing fingerprint": without("ro.build.fingerprint"),
               "control char": GETPROP_OK.replace("[Model One]", "[Model\x1bOne]").encode(),
               "too long": GETPROP_OK.replace("[Model One]", "[" + "M" * 300 + "]").encode(),
               "empty value": GETPROP_OK.replace("[Model One]", "[]").encode(),
               "api text": GETPROP_OK.replace("[35]", "[thirty]").encode(),
               "api range": GETPROP_OK.replace("[35]", "[0]").encode(),
               "patch": GETPROP_OK.replace("[2024-09-05]", "[soon]").encode(),
               "not booted": GETPROP_OK.replace("[sys.boot_completed]: [1]", "[sys.boot_completed]: [0]").encode(),
               "invalid utf-8": GETPROP_OK.replace("[Acme]", "[Ac\udcffme]").encode("utf-8", "surrogateescape"),
               "not bytes": GETPROP_OK}
        for name, raw in bad.items():
            with self.subTest(case=name), self.assertRaises(ap.TargetOutputError):
                ap.device_report(raw)
        # invalid bytes in a property the report never reads do not matter
        ap.device_report(GETPROP_OK.encode() + b"[vendor.blob]: [\xff\xfe]\n")

    def test_png_structure(self):
        self.assertEqual(ap.png_dimensions(png_bytes(640, 480)), (640, 480))
        for raw in (png_bytes(complete=False), b"", "not bytes", png_bytes(width=0), png_bytes(height=16385)):
            with self.assertRaises(ap.TargetOutputError):
                ap.png_dimensions(raw)

    def test_meminfo_snapshot(self):
        text, pid = ap.meminfo_snapshot(MEMINFO_OK.replace("\n", "\r\n").encode(), PACKAGE)
        self.assertEqual((pid, "\r" in text), (42, False))
        with self.assertRaises(ap.ProcessNotRunning):
            ap.meminfo_snapshot(f"No process found for: {PACKAGE}\n".encode(), PACKAGE)
        with self.assertRaises(ap.TargetOutputError):
            ap.meminfo_snapshot(MEMINFO_OK.encode(), "com.other.pkg")
        with self.assertRaises(ap.TargetOutputError):
            ap.meminfo_snapshot("Ünïcode".encode(), PACKAGE)


# ---------------------------------------------------------------- W  performance limitations

class W_PerformanceLimitations(AdbCase):
    def test_meminfo_claims_a_memory_snapshot_only(self):
        limitations = " ".join(aa.LIMITATIONS[aa.MEMINFO])
        for phrase in ("point-in-time memory snapshot", "vary across platform versions", "Not a controlled benchmark",
                       "CPU, GPU, thermals, frame pacing, FPS or sustained performance"):
            self.assertIn(phrase, limitations)
        serial = next((s for _, s, _ in TARGETS if systemui_running(s)), None)
        if serial is None:
            self.skipTest(f"no target runs {PACKAGE}")
        result, _ = MATRIX.get(serial, aa.MEMINFO)
        cand = result.evidence_candidates[0]
        claims = (cand.summary + " " + json.dumps({k: v for k, v in result.data.items() if k != "instrumentation"})).lower()
        for word in ("fps", "jank", "cpu", "gpu", "thermal", "benchmark", "frame", "sustained", "overall"):
            self.assertNotIn(word, claims)
        self.assertEqual(cand.limitations, aa.LIMITATIONS[aa.MEMINFO])

    def test_device_report_and_screenshot_state_what_they_do_not_prove(self):
        self.assertIn("installed, running or behaving correctly", " ".join(aa.LIMITATIONS[aa.DEVICE_REPORT]))
        self.assertIn("does not by itself prove performance", " ".join(aa.LIMITATIONS[aa.DEVICE_REPORT]).lower())
        self.assertIn("motion, input latency or audio", " ".join(aa.LIMITATIONS[aa.SCREENSHOT]))


# ---------------------------------------------------------------- X  CLI

class X_Cli(AdbCase):
    def test_list_describe_capabilities_probe(self):
        code, out = cli("list")
        self.assertEqual(code, 0)
        self.assertIn("4 tool adapter", out)
        self.assertIn("adb 1.0.0 · DEVICE · 3 capabilities", out)
        self.assertNotIn("TEST_ONLY", out)
        code, out = cli("describe", "adb")
        self.assertEqual(code, 0)
        for text in (*CAPS, "network FORBIDDEN", "DEVICE"):
            self.assertIn(text, out)
        code, out = cli("capabilities", "adb", "--format", "json")
        self.assertEqual(sorted(c["id"] for c in json.loads(out)["capabilities"]["capabilities"]), sorted(CAPS))
        code, out = cli("probe", "adb", "--format", "json")
        self.assertEqual((code, json.loads(out)["probe"]["status"]), (0, "AVAILABLE"))

    def test_execute_through_the_cli(self):
        p = self.project()
        base = ("execute", "--adapter", "adb", "--project", str(p), "--subject-ref", "FEATURE-X",
                "--target-platform", "ANDROID", "--device", self.first())
        code, out = cli(*base, "--capability", aa.SCREENSHOT, "--allow-mutation", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["result"]["evidence_candidates"][0]["evidence_type"], "VISUAL_EVIDENCE")
        code, out = cli(*base, "--capability", aa.MEMINFO, "--input", "package_name=com.x;id", "--allow-mutation")
        self.assertEqual(code, tdg.EXIT_FOR[tdg.INVALID_REQUEST])
        code, _ = cli("execute", "--adapter", "adb", "--project", str(p), "--subject-ref", "FEATURE-X",
                      "--capability", aa.DEVICE_REPORT, "--target-platform", "ANDROID", "--allow-mutation")
        self.assertEqual(code, tdg.EXIT_FOR[tdg.INVALID_REQUEST])  # no --device: nothing is chosen


# ---------------------------------------------------------------- Y  repository privacy

class Y_RepositoryPrivacy(AdbCase):
    def test_no_physical_serial_or_fingerprint_is_in_the_repository(self):
        for _, serial, _ in TARGETS:
            MATRIX.get(serial, aa.DEVICE_REPORT)
        needles = [s.encode() for _, s, k in TARGETS if k == "usb"] + [f.encode() for f in FINGERPRINTS]
        self.assertTrue(needles)
        hits = []
        for path in ROOT.rglob("*"):
            if ".git" in path.parts or "__pycache__" in path.parts or not path.is_file():
                continue
            data = path.read_bytes()
            if any(n in data for n in needles):
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(hits, [])


# ---------------------------------------------------------------- Z  other adapters unchanged

class Z_OtherAdapters(AdbCase):
    def test_the_other_production_adapters_are_unchanged(self):
        registry = default_registry(FW)
        caps = {a: sorted(c.id for c in registry.get(a).descriptor.capabilities) for a in registry.adapter_ids()}
        self.assertEqual(caps["git"], ["git.inspect", "git.resolve-provenance"])
        self.assertEqual(caps["ffprobe"], ["ffprobe.inspect"])
        self.assertEqual(caps["ffmpeg"], ["ffmpeg.extract-audio", "ffmpeg.extract-clip", "ffmpeg.extract-frame"])


class _RedactingStream(io.TextIOBase):
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        return self.stream.write(redact(text))

    def flush(self):
        self.stream.flush()


if __name__ == "__main__":
    if ADB is None:
        print("ADB_RUNTIME_UNAVAILABLE_FOR_PHASE2C3: these are real integration tests and require adb on PATH")
        sys.exit(1)
    try:
        TARGETS.extend(_targets())
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)
    stream = _RedactingStream(sys.stderr)
    result = unittest.main(verbosity=1, exit=False, testRunner=unittest.TextTestRunner(stream=stream)).result
    version = subprocess.run([ADB, "version"], capture_output=True, text=True).stdout.splitlines()[1].strip()
    print(f"GPOS ADB adapter tests (real adb {version}; targets: "
          f"{', '.join(label for label, _, _ in TARGETS)}; incomplete real meminfo snapshots retried: {MATRIX.retries})")
    sys.exit(0 if result.wasSuccessful() else 1)
