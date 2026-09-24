#!/usr/bin/env python3
"""Bounded mutation harness for the production ADB evidence adapter (gpos/tools/adb/).

    GPOS_TEST_ANDROID_SERIALS=<serial>[,...] python3 tests/mutate_adb_adapter.py [--jobs N] [--only TEXT]

Each mutation breaks exactly one semantic guarantee of the adapter in a temporary copy of the repository
and runs tests/test_adb_adapter.py — real adb, real authorized Android targets — there. A mutation must make
the suite fail ("CAUGHT"); one that leaves it green is "MISSED" and fails this harness. An anchor that does
not match exactly once is "NOT APPLIED" and also fails, so the list cannot rot.

A mutation is a list of edits. An edit is (file, anchor, replacement), or ("CREATE", file, content) for a
defect that adds a file (a committed snapshot); content may be a function of the authorized targets, so no
device identifier is ever written into this harness. Mutations 1-38 are the ones the Phase 2C-3 brief
requires, the identity and physical-target group the evidence-authority hardening requires; the rest
defend further guarantees.
"""

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTER, PARSERS, REGISTRY = "gpos/tools/adb/adapter.py", "gpos/tools/adb/parsers.py", "gpos/tools/registry.py"

PRODUCTION = "(AdbAdapter(), BlenderAdapter(), FfmpegAdapter(), FfprobeAdapter(), GitAdapter(), UnityAdapter())"
DEVICE_DECL = '"TARGET_RUNTIME", "DEVICE_EVIDENCE",\n             "JSON", ("adb_serial",),'
MEMINFO_DECL = '"PERFORMANCE_RUNTIME", "PERFORMANCE_EVIDENCE",\n             "REPORT", ("adb_serial", "package_name"),'
DRY_RUN = "        if context.dry_run:\n            return AdapterOutcome(\n"
COLLISION = "        if output.exists() or output.is_symlink():\n            return _refuse(cap, f\"the workspace already holds"
EMU = '        if kind != "physical":\n'
TEMPLATES_END = "def meminfo_argv(serial, package):\n    return (\"-s\", serial, \"shell\", \"dumpsys\", \"meminfo\", \"-s\", package)\n"


def a_physical_serial():
    """A committed-snapshot defect: the content is taken from the environment at run time, never stored here."""
    serials = [s for s in os.environ.get("GPOS_TEST_ANDROID_SERIALS", "").split(",")
               if s and not s.startswith("emulator-")]
    return json.dumps({"device": serials[0] if serials else "", "model": "snapshot"}) + "\n"


def extra_template(name, body):
    return (ADAPTER, TEMPLATES_END, TEMPLATES_END + f"\n\ndef {name}:\n    return {body}\n")


MUTATIONS = [
    ("1 adb absent from the production registry", [
        (REGISTRY, PRODUCTION, "(BlenderAdapter(), FfmpegAdapter(), FfprobeAdapter(), GitAdapter(), UnityAdapter())")]),
    ("2 TEST_ONLY synthetic enters the production registry", [
        (REGISTRY, "        registry.register(adapter)\n    return registry",
         "        registry.register(adapter)\n    registry.allow_test_only = True\n"
         "    from .synthetic import SyntheticAdapter\n    registry.register(SyntheticAdapter())\n    return registry")]),
    ("3 target_platform ANDROID requirement removed", [
        (ADAPTER, "        if request.target_platform != TARGET_PLATFORM:\n", "        if False:\n")]),
    ("4 request.device requirement removed", [
        (ADAPTER, "        if not isinstance(identity, str) or not identity.strip() or len(identity) > parsers.MAX_IDENTITY:\n",
         "        if False:\n")]),
    ("4b adb_serial requirement removed", [
        (ADAPTER, "        problem = parsers.serial_problem(serial)\n",
         "        problem = parsers.serial_problem(serial) if serial else None\n")]),
    ("5 automatic target selection introduced", [
        (ADAPTER, '        serial = inputs.get("adb_serial")\n',
         '        serial = inputs.get("adb_serial") or os.environ.get("ANDROID_SERIAL") or "emulator-5554"\n')]),
    ("6 TCP/wireless serial accepted", [
        (PARSERS, '    if ":" in value or "." in value:\n', "    if False:\n"),
        (PARSERS, 'SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")',
         'SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")')]),
    ("7 -s SERIAL target binding removed", [
        (ADAPTER, 'return ("-s", serial, "get-state")', 'return ("get-state",)'),
        (ADAPTER, 'return ("-s", serial, "shell", "getprop", "sys.boot_completed")',
         'return ("shell", "getprop", "sys.boot_completed")'),
        (ADAPTER, 'return ("-s", serial, "shell", "getprop")\n', 'return ("shell", "getprop")\n'),
        (ADAPTER, 'return ("-s", serial, "exec-out", "screencap", "-p")', 'return ("exec-out", "screencap", "-p")'),
        (ADAPTER, 'return ("-s", serial, "shell", "dumpsys", "meminfo", "-s", package)',
         'return ("shell", "dumpsys", "meminfo", "-s", package)')]),
    ("8 get-state readiness check removed", [
        (ADAPTER, '        if outcome.exit_code != 0:\n            if b"not found"', '        if False:\n            if b"not found"'),
        (ADAPTER, '        if state != "device":\n', "        if False:\n")]),
    ("9 boot-completed check removed", [
        (ADAPTER, '        if outcome.exit_code != 0 or outcome.raw_stdout.strip() != b"1":\n', "        if False:\n")]),
    ("10 device report copies every getprop value", [
        (PARSERS, '    report["boot_completed"] = True\n    return report\n',
         '    report["boot_completed"] = True\n    return dict(report, **{k: v.decode("utf-8", "replace") '
         'for k, v in records.items()})\n')]),
    ("11 a sensitive property is added to the report", [
        (PARSERS, '    "boot_completed": "sys.boot_completed",\n',
         '    "boot_completed": "sys.boot_completed",\n    "serial_number": "ro.serialno",\n')]),
    ("12 device report claims the wrong evidence type", [
        (ADAPTER, DEVICE_DECL, DEVICE_DECL.replace("DEVICE_EVIDENCE", "VISUAL_EVIDENCE")),
        (ADAPTER, '"device.json", "JSON", "application/json", "DEVICE_EVIDENCE"',
         '"device.json", "JSON", "application/json", "VISUAL_EVIDENCE"')]),
    ("13 device report claims PERFORMANCE_RUNTIME", [
        (ADAPTER, DEVICE_DECL, DEVICE_DECL.replace("TARGET_RUNTIME", "PERFORMANCE_RUNTIME"))]),
    ("14 screenshot uses a device-side /sdcard temporary file", [
        (ADAPTER, 'return ("-s", serial, "exec-out", "screencap", "-p")',
         'return ("-s", serial, "shell", "screencap", "-p", "/sdcard/gpos-screen.png")')]),
    ("15 screenshot does not use exec-out", [
        (ADAPTER, 'return ("-s", serial, "exec-out", "screencap", "-p")', 'return ("-s", serial, "shell", "screencap", "-p")')]),
    ("16 screenshot PNG validation removed", [
        (ADAPTER, "            width, height = parsers.png_dimensions(outcome.raw_stdout)\n",
         "            width, height = 0, 0\n")]),
    ("17 screenshot truncation ignored", [
        (ADAPTER, "        if outcome.timed_out or outcome.truncated:\n            return outcome, self.step_failure(",
         "        if outcome.timed_out:\n            return outcome, self.step_failure(")]),
    ("18 screenshot automatically claims DEVICE_EVIDENCE", [
        (ADAPTER, "                              evidence=(candidate,), mutation_performed=True,",
         "                              evidence=(candidate,) + ((replace(candidate, evidence_type=\"DEVICE_EVIDENCE\"),) "
         "if cap == SCREENSHOT else ()), mutation_performed=True,")]),
    ("19 package validation removed", [
        (ADAPTER, "            problem = parsers.package_problem(package)\n", "            problem = None\n")]),
    ("20 caller package becomes raw shell text", [
        (ADAPTER, 'return ("-s", serial, "shell", "dumpsys", "meminfo", "-s", package)',
         'return ("-s", serial, "shell", f"dumpsys meminfo -s {package}")'),
        (ADAPTER, "            problem = parsers.package_problem(package)\n", "            problem = None\n")]),
    ("21 generic adb shell capability introduced", [
        (ADAPTER, "             \"writes meminfo.txt into this execution's host workspace; the target is not modified\"),\n)",
         "             \"writes meminfo.txt into this execution's host workspace; the target is not modified\"),\n"
         "    _capture(\"adb.shell\", \"RUN\", \"Run a shell command on the target.\", \"TARGET_RUNTIME\", "
         "\"RUNTIME_EVIDENCE\", \"TEXT\", (\"command\",), \"runs a caller command on the target\"),\n)")]),
    ("22 caller-selected dumpsys service", [
        (ADAPTER, MEMINFO_DECL, MEMINFO_DECL.replace('("adb_serial", "package_name")', '("adb_serial", "package_name", "service")')),
        (ADAPTER, 'run.step(meminfo_argv(serial, package), "meminfo",',
         'run.step(("-s", serial, "shell", "dumpsys", (context.request.inputs or {}).get("service", "meminfo"), '
         '"-s", package), "meminfo",')]),
    ("23 package-not-running output becomes PERFORMANCE_EVIDENCE", [
        (PARSERS, "            raise ProcessNotRunning(package)\n", "            return text, 0\n")]),
    ("24 meminfo claims TARGET_RUNTIME", [
        (ADAPTER, MEMINFO_DECL, MEMINFO_DECL.replace("PERFORMANCE_RUNTIME", "TARGET_RUNTIME"))]),
    ("25 meminfo claims an overall benchmark / FPS result", [
        (ADAPTER, 'summary=f"Point-in-time memory snapshot of {package} on {identity}",',
         'summary=f"Overall performance benchmark of {package}: FPS and memory on {identity}",')]),
    ("26 dry run contacts the target", [
        (ADAPTER, DRY_RUN, "        if context.dry_run and _Runner(context, self._capture, serial).ready(serial) is None:\n"
                           "            return AdapterOutcome(\n")]),
    ("27 dry run writes an artifact", [
        (ADAPTER, DRY_RUN, "        if context.dry_run:\n            output.parent.mkdir(parents=True, exist_ok=True)\n"
                           "            output.write_bytes(b\"\")\n            return AdapterOutcome(\n")]),
    ("28 existing-output collision check moves after the dry run", [
        (ADAPTER, COLLISION, COLLISION.replace("        if output.exists() or output.is_symlink():",
                                               "        if not context.dry_run and (output.exists() or output.is_symlink()):"))]),
    ("29 install becomes authorized", [extra_template("install_argv(serial, apk)", '("-s", serial, "install", apk)')]),
    ("30 push/pull becomes authorized", [extra_template("pull_argv(serial, path)", '("-s", serial, "pull", path, ".")')]),
    ("31 connect/pair/tcpip becomes authorized", [extra_template("connect_argv(address)", '("connect", address)')]),
    ("32 the adapter imports subprocess", [
        (ADAPTER, "import json\nimport os\n", "import json\nimport os\nimport subprocess\n")]),
    ("33 the caller can choose the adb executable", [
        (ADAPTER, DEVICE_DECL, DEVICE_DECL.replace('"JSON", ("adb_serial",),', '"JSON", ("adb_serial", "adb"),')),
        (ADAPTER, "        self.spec = proc.ToolProcessSpec(executable=self.context.probe.tool_path, argv=argv,",
         "        self.spec = proc.ToolProcessSpec(executable=(self.context.request.inputs or {}).get(\"adb\", "
         "self.context.probe.tool_path), argv=argv,")]),
    ("34 target raw output leaks to the ToolResult", [
        (ADAPTER, "        return AdapterOutcome(ok=True, exit_code=outcome.exit_code, process=_without_output(outcome),\n"
                  "                              artifacts=",
         "        return AdapterOutcome(ok=True, exit_code=outcome.exit_code, process=outcome,\n"
         "                              artifacts=")]),
    ("35 a target serial is committed into a test snapshot", [
        ("CREATE", "tests/fixtures/adb-snapshots/device.json", a_physical_serial)]),
    ("36 Git is automatically called by the ADB adapter", [
        (ADAPTER, "        run = _Runner(context, self._capture, serial)\n",
         "        context.run(proc.ToolProcessSpec(executable=shutil.which(\"git\"), argv=(\"rev-parse\", \"HEAD\"),\n"
         "                                         cwd=str(context.project_root), timeout=5.0))\n"
         "        run = _Runner(context, self._capture, serial)\n")]),
    ("37 build_revision is fabricated", [
        (ADAPTER, "                             data=report)",
         "                             data=dict(report, build_revision=report[\"build_fingerprint\"]))")]),
    ("38 target_platform is inferred instead of required", [
        (ADAPTER, "        if request.target_platform != TARGET_PLATFORM:\n",
         "        if request.target_platform not in (None, TARGET_PLATFORM):\n")]),
    # --- further guarantees the suite defends
    ("meminfo accepts another process", [(PARSERS, "    if name != package:\n", "    if False:\n")]),
    ("meminfo accepts several processes", [(PARSERS, "    if len(headers) != 1:\n", "    if not headers:\n")]),
    ("meminfo without memory totals accepted", [
        (PARSERS, "    if not any(_TOTAL_PSS.fullmatch(line) for line in lines):\n", "    if False:\n")]),
    ("getprop duplicate property accepted", [
        (PARSERS, "            if name in records:\n", "            if False:\n")]),
    ("getprop stray line accepted", [
        (PARSERS, "            if not match:\n                raise TargetOutputError(\"getprop output has a line",
         "            if not match:\n                continue\n                raise TargetOutputError(\"getprop output has a line")]),
    ("PNG completeness (IEND) not checked", [(PARSERS, "    if not data.endswith(PNG_IEND):\n", "    if False:\n")]),
    ("wireless auto-connect override removed", [
        (ADAPTER, 'ENVIRONMENT = (("ADB_MDNS_AUTO_CONNECT", "0"),)', "ENVIRONMENT = ()")]),
    ("an unavailable target is reported as merely not ready", [
        (ADAPTER, '            if b"not found" in outcome.raw_stderr:\n', "            if False:\n")]),
    ("meminfo instrumentation declared as negligible timing impact", [
        (ADAPTER, '    "timing_impact": "UNKNOWN",', '    "timing_impact": "NEGLIGIBLE",')]),
    ("probe fabricates a version from the protocol line alone", [
        (ADAPTER, "    if not protocol or not tools:\n        return None, None\n    return protocol.group(1), tools.group(1)",
         "    if not protocol:\n        return None, None\n    return protocol.group(1), (tools.group(1) if tools else protocol.group(1))")]),
    # --- evidence-authority hardening: identity and physical targets
    ("adb_serial removed; request.device used as the -s target again", [
        (ADAPTER, '        serial = inputs.get("adb_serial")\n', "        serial = request.device\n")]),
    ("canonical identity comparison removed", [(ADAPTER, "        if identity != observed:\n", "        if False:\n")]),
    ("the serial is required as provenance.device (old contract)", [
        (ADAPTER, "        if identity != observed:\n", "        if identity != serial:\n")]),
    ("the serial is copied into device.json", [
        (ADAPTER, "        report = dict(report, reference_device=identity)\n",
         "        report = dict(report, reference_device=identity, adb_serial=run.serial)\n")]),
    ("the recorded command exposes the serial", [
        (ADAPTER, "        argv = tuple(TARGET_PLACEHOLDER if a == self.serial else a for a in self.spec.argv)\n",
         "        argv = tuple(self.spec.argv)\n")]),
    ("the canonical identity drifts from the reference-device format", [
        (PARSERS, '    identity = (f"{report[\'manufacturer\']} {report[\'model\']} / Android {report[\'android_release\']} "',
         '    identity = (f"{report[\'model\']} / Android {report[\'android_release\']} "')]),
    ("emulator detection removed", [
        (ADAPTER, '        kind = "emulator" if serial.startswith("emulator-") else parsers.target_kind(records)\n',
         '        kind = "physical"\n')]),
    ("emulator detection relies on the serial prefix only", [
        (ADAPTER, '        kind = "emulator" if serial.startswith("emulator-") else parsers.target_kind(records)\n',
         '        kind = "emulator" if serial.startswith("emulator-") else "physical"\n')]),
    ("an emulator may create DEVICE_EVIDENCE", [(ADAPTER, EMU, '        if kind != "physical" and cap != DEVICE_REPORT:\n')]),
    ("an emulator screenshot may be TARGET_RUNTIME evidence", [
        (ADAPTER, EMU, '        if kind != "physical" and cap != SCREENSHOT:\n')]),
    ("an emulator meminfo may be PERFORMANCE_RUNTIME evidence", [
        (ADAPTER, EMU, '        if kind != "physical" and cap != MEMINFO:\n')]),
    ("an unknown target defaults to physical", [
        (PARSERS, '    if not value("ro.hardware") or qemu - {"0"}:\n        return None\n',
         '    if not value("ro.hardware") or qemu - {"0"}:\n        return "physical"\n')]),
    ("an input artifact is silently accepted", [
        (ADAPTER, "        if context.input_artifacts:\n            return _refuse(", "        if False:\n            return _refuse(")]),
]


def run(mutation):
    name, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-adbmut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for edit in edits:
            if edit[0] == "CREATE":
                _, rel, content = edit
                text = content() if callable(content) else content
                if '"device": ""' in text:
                    return name, "NOT APPLIED (no physical target is configured)"
                (copy / rel).parent.mkdir(parents=True, exist_ok=True)
                (copy / rel).write_text(text)
                continue
            rel, anchor, replacement = edit
            path = copy / rel
            text = path.read_text()
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            path.write_text(text.replace(anchor, replacement))
        out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_adb_adapter.py")],
                             capture_output=True, text=True, timeout=1800,
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
