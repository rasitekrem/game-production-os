"""Production target-device evidence adapter: Android Debug Bridge (Phase 2C-3).

Three capabilities read evidence from one explicitly named Android target:

    adb.capture-device-report   which target, OS build and boot state      -> device.json    -> DEVICE_EVIDENCE
    adb.capture-screenshot      what the target displayed at one instant   -> screenshot.png -> VISUAL_EVIDENCE
    adb.capture-meminfo         one named package's memory at one instant  -> meminfo.txt    -> PERFORMANCE_EVIDENCE

NO PRODUCTION CAPABILITY CHANGES ANDROID TARGET STATE. Every command is a fixed, read-only template:
`get-state`, `getprop`, `exec-out screencap -p` (streamed to the host; no file on the device) and
`dumpsys meminfo -s <package>`. There is no install, launch, input, settings, file transfer, reboot,
shell runner, generic dumpsys, logcat, screen recording or wireless pairing. The capabilities are
MUTATING only because each writes its one evidence file into this execution's host workspace.

The target is never inferred. `request.target_platform` must be `ANDROID` and `request.device` must be
the exact local ADB serial; every target command binds it with `-s`. `-d`, `-e` and `ANDROID_SERIAL` are
never used (the foundation does not inherit the variable), and a network serial (`host:port`, an mDNS
service name) is refused: there is no wireless ADB in this phase. `request.device` is also the device
recorded in provenance, unchanged.

Before a capture the target must be connected (`get-state` = `device`) and fully booted
(`sys.boot_completed` = `1`): Android documents that the `device` state alone does not mean the system has
finished booting. A dry run validates the request and reports a plan without contacting the target.

ADB is client/server software: an `adb` client command may start the host ADB server if none is running.
This adapter never starts, stops or configures that server explicitly, and claims no sandboxing.
This module never starts a process itself and never imports the subprocess module.
"""

import json
import os
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from .. import diagnostics as dg
from .. import model
from .. import process as proc
from ..artifacts import ArtifactSpec
from ..capabilities import Capability, TimeoutPolicy
from ..evidence import EvidenceCandidate
from ..execution import AdapterOutcome
from . import parsers

ADAPTER_ID = "adb"
ADAPTER_VERSION = "1.0.0"
EXECUTABLE_NAME = "adb"
TARGET_PLATFORM = "ANDROID"

DEVICE_REPORT = f"{ADAPTER_ID}.capture-device-report"
SCREENSHOT = f"{ADAPTER_ID}.capture-screenshot"
MEMINFO = f"{ADAPTER_ID}.capture-meminfo"

# ---------------------------------------------------------------- the closed command surface

VERSION_ARGV = ("version",)


def state_argv(serial):
    return ("-s", serial, "get-state")


def boot_argv(serial):
    return ("-s", serial, "shell", "getprop", "sys.boot_completed")


def getprop_argv(serial):
    return ("-s", serial, "shell", "getprop")


def screencap_argv(serial):
    return ("-s", serial, "exec-out", "screencap", "-p")


def meminfo_argv(serial, package):
    return ("-s", serial, "shell", "dumpsys", "meminfo", "-s", package)


# Adapter-owned environment, on top of the foundation's allowlist (which never passes ANDROID_SERIAL,
# ADB_TRACE or any other ADB variable through). A server this client happens to start never auto-connects
# to wireless targets it discovers; see the AOSP ADB manual and adb_mdns.cpp ("0" disables all).
ENVIRONMENT = (("ADB_MDNS_AUTO_CONNECT", "0"),)
ENVIRONMENT_POLICY = proc.EnvironmentPolicy(overrides=ENVIRONMENT)

# Capture bounds, from real targets: getprop ~33-56 KiB, a screenshot 0.13-0.45 MiB at up to 1600x2560,
# meminfo -s ~1.3-1.6 KiB. Output reaching a bound is refused, never reported in part.
CAPTURE_BYTES = {"state": 4096, "getprop": 1024 ** 2, "screenshot": 32 * 1024 ** 2, "meminfo": 2 * 1024 ** 2}
PROBE_TIMEOUT = 10.0

_VERSION_LINE = re.compile(r"Android Debug Bridge version ([0-9]+\.[0-9]+\.[0-9]+)")
_TOOLS_LINE = re.compile(r"Version ([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9._]{1,40})?)")

OUTPUTS = {  # kind -> (artifact id, file, artifact kind, media type, evidence type)
    DEVICE_REPORT: ("device-report", "device.json", "JSON", "application/json", "DEVICE_EVIDENCE"),
    SCREENSHOT: ("screenshot", "screenshot.png", "IMAGE", "image/png", "VISUAL_EVIDENCE"),
    MEMINFO: ("meminfo", "meminfo.txt", "REPORT", "text/plain", "PERFORMANCE_EVIDENCE"),
}

# The instrumentation a caller may declare when materializing a meminfo record (the evidence schema
# requires it for PERFORMANCE_EVIDENCE; the foundation leaves it to the caller because it is not observed).
MEMINFO_INSTRUMENTATION = {
    "present": True,
    "description": "Android `dumpsys meminfo -s` through ADB: an on-demand system memory dump of one process; "
                   "no in-app profiler",
    "timing_impact": "UNKNOWN",
}

LIMITATIONS = {
    DEVICE_REPORT: (
        "Proves the Android target, OS build and boot state observed through ADB at capture time.",
        "Does not prove that any application is installed, running or behaving correctly.",
        "Does not by itself prove performance.",
    ),
    SCREENSHOT: (
        "Shows one display instant of the named target only.",
        "Does not prove motion, input latency or audio.",
        "Reflects whatever the target displayed at capture time; the adapter does not inspect its content.",
    ),
    MEMINFO: (
        "One point-in-time memory snapshot of the explicitly named package on the named target.",
        "Android meminfo details vary across platform versions; the report is the target's own text.",
        "Does not establish CPU, GPU, thermals, frame pacing, FPS or sustained performance.",
        "Not a controlled benchmark.",
    ),
}


def _capture(cap_id, category, description, execution_context, evidence, artifact_kind, input_kinds, side_effect):
    return Capability(
        id=cap_id, category=category, description=description,
        operation_class="MUTATING", state_model="STATELESS", execution_context=execution_context,
        requires_tool=True, requires_project=True, dry_run_supported=True,
        input_kinds=input_kinds, artifact_kinds=(artifact_kind,),
        potential_evidence=((evidence, execution_context),),
        timeout=TimeoutPolicy(default=60.0, maximum=300.0), side_effect_scope=side_effect,
        notes=("Requires target_platform ANDROID and request.device naming the exact local ADB serial.",
               "Reads the target only; writes one evidence file into this execution's host workspace."))


CAPABILITIES = (
    _capture(DEVICE_REPORT, "CAPTURE", "Capture the named Android target's identity, OS build and boot state from "
             "a fixed allowlist of system properties; offers DEVICE_EVIDENCE.", "TARGET_RUNTIME", "DEVICE_EVIDENCE",
             "JSON", (), "writes device.json into this execution's host workspace; the target is not modified"),
    _capture(SCREENSHOT, "CAPTURE", "Capture one PNG screenshot streamed from the named Android target "
             "(no file on the device); offers VISUAL_EVIDENCE.", "TARGET_RUNTIME", "VISUAL_EVIDENCE", "IMAGE", (),
             "writes screenshot.png into this execution's host workspace; the target is not modified"),
    _capture(MEMINFO, "PROFILE", "Capture one point-in-time memory snapshot of one named, running package on the "
             "named Android target; offers PERFORMANCE_EVIDENCE.", "PERFORMANCE_RUNTIME", "PERFORMANCE_EVIDENCE",
             "REPORT", ("package_name",),
             "writes meminfo.txt into this execution's host workspace; the target is not modified"),
)

DESCRIPTOR = model.AdapterDescriptor(
    adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION, tool_family="DEVICE",
    target_tool="Android Debug Bridge", adapter_kind="CLI", state_model="STATELESS",
    supported_platforms=("WINDOWS", "MACOS", "LINUX"), capabilities=CAPABILITIES,
    availability="an adb executable (Android SDK Platform-Tools) on PATH (absolute PATH entries only)",
    compatibility_notes=(
        "Explicit local targets only: request.device is the exact USB or emulator serial; wireless and TCP/IP "
        "targets are refused and never paired or connected.",
        "Read-only target commands: get-state, getprop, exec-out screencap -p, dumpsys meminfo -s <package>.",
        "The ADB server is host tool infrastructure: an adb command may start it; the adapter never manages it.",
    ))


class AdbAdapter(model.ToolAdapter):
    """`which` and `capture_bytes` are code-level seams for tests; a request can change neither."""

    descriptor = DESCRIPTOR

    def __init__(self, which=shutil.which, capture_bytes=None):
        self._which = which
        self._capture = dict(CAPTURE_BYTES, **(capture_bytes or {}))

    # ------------------------------------------------------------ probe: the executable only

    def probe(self):
        """Establishes that adb exists and reports a version. It never runs `adb devices`: which targets are
        attached is execution-time state, and a device query talks to the ADB server."""
        platform = model.current_platform()
        unusable = lambda reason: tuple((c.id, False, reason) for c in CAPABILITIES)
        found = self._which(EXECUTABLE_NAME)
        if not found:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail="no adb executable was found on PATH",
                                     capability_availability=unusable("adb is not available"))
        if not os.path.isabs(found):
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail="adb was only found through a relative PATH entry, which would make the "
                                            "executed program depend on the working directory; it is not used",
                                     capability_availability=unusable("adb is not available"))
        executable = str(Path(found).resolve())
        neutral = str(Path(tempfile.gettempdir()).resolve())
        try:
            outcome = proc.run_process(proc.ToolProcessSpec(executable=executable, argv=VERSION_ARGV, cwd=neutral,
                                                            timeout=PROBE_TIMEOUT, env=ENVIRONMENT_POLICY), [neutral])
        except proc.ProcessSpecError as exc:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"adb could not be started: {exc}",
                                     capability_availability=unusable("adb could not be started"))
        if outcome.timed_out or outcome.exit_code != 0 or outcome.truncated:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"`adb version` did not complete normally (exit {outcome.exit_code})",
                                     capability_availability=unusable("adb did not run normally"))
        protocol, tools = parse_version(outcome.raw_stdout)
        if tools is None:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable, platform=platform,
                                     detail="unrecognized `adb version` output; the Platform-Tools version could not "
                                            "be established",
                                     capability_availability=unusable("adb version unknown"))
        return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=executable, tool_version=tools,
                                 platform=platform,
                                 detail=f"Android Debug Bridge {protocol}, Platform-Tools {tools} at {executable}",
                                 capability_availability=tuple((c.id, True, "") for c in CAPABILITIES))

    # ------------------------------------------------------------ execution

    def execute(self, request, context):
        cap = request.capability_id
        if cap not in OUTPUTS:
            raise AssertionError(f"{cap} is declared but not implemented")
        artifact_id, filename, artifact_kind, media_type, evidence_type = OUTPUTS[cap]
        if context.input_artifacts:
            return _refuse(cap, f"{cap} captures from the target and consumes no input artifact")
        if request.target_platform != TARGET_PLATFORM:
            return _refuse(cap, f"{cap} requires target_platform {TARGET_PLATFORM}; it is caller-owned provenance and "
                                f"is never inferred (got {request.target_platform!r})")
        serial = request.device
        problem = parsers.serial_problem(serial)
        if problem:
            return _refuse(cap, problem)
        package = None
        if cap == MEMINFO:
            package = (request.inputs or {}).get("package_name")
            problem = parsers.package_problem(package)
            if problem:
                return _refuse(cap, problem)
        output = Path(context.workspace) / filename
        # A known collision is refused before the dry-run return: a plan never succeeds where the capture
        # would be refused. Looking at the path creates nothing and contacts no target.
        if output.exists() or output.is_symlink():
            return _refuse(cap, f"the workspace already holds {filename}; an existing file is never reported as a "
                                f"new capture")
        what = f"{filename} ({artifact_kind})" + (f" for package {package}" if package else "")
        if context.dry_run:
            return AdapterOutcome(
                plan=(f"would check the connection state and boot completion of ADB target {serial}",
                      f"would capture {what} from that target and offer {evidence_type} "
                      f"({context.capability.execution_context})",
                      "no ADB target command, workspace or file is created by a dry run; target availability is "
                      "not checked"),
                data={"target_platform": TARGET_PLATFORM, "device": serial,
                      **({"package_name": package} if package else {})})
        run = _Runner(context, self._capture)
        refusal = run.ready(serial)
        if refusal:
            return refusal
        if cap == DEVICE_REPORT:
            return self._device_report(context, run, serial, output)
        if cap == SCREENSHOT:
            return self._screenshot(context, run, serial, output)
        return self._meminfo(context, run, serial, package, output)

    # ------------------------------------------------------------ the three captures

    def _device_report(self, context, run, serial, output):
        outcome, failed = run.step(getprop_argv(serial), "getprop", "shell getprop")
        if failed:
            return failed
        try:
            report = parsers.device_report(outcome.raw_stdout)
        except parsers.TargetOutputError as exc:
            return run.failed(outcome, f"the target's properties could not be read as a device report ({exc})")
        body = (json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return self._written(context, run, outcome, DEVICE_REPORT, output, body,
                             summary=f"Android target {report['manufacturer']} {report['model']}, API "
                                     f"{report['api_level']}, observed through ADB",
                             data=report)

    def _screenshot(self, context, run, serial, output):
        outcome, failed = run.step(screencap_argv(serial), "screenshot", "exec-out screencap -p")
        if failed:
            return failed
        try:
            width, height = parsers.png_dimensions(outcome.raw_stdout)
        except parsers.TargetOutputError as exc:
            return run.failed(outcome, f"the screenshot is not a complete PNG ({exc}); nothing was written")
        return self._written(context, run, outcome, SCREENSHOT, output, bytes(outcome.raw_stdout),
                             summary=f"{width}x{height} screenshot of the Android target, streamed through ADB",
                             data={"width": width, "height": height, "bytes": len(outcome.raw_stdout)})

    def _meminfo(self, context, run, serial, package, output):
        outcome, failed = run.step(meminfo_argv(serial, package), "meminfo", "shell dumpsys meminfo -s")
        if failed:
            return failed
        try:
            text, pid = parsers.meminfo_snapshot(outcome.raw_stdout, package)
        except parsers.ProcessNotRunning:
            return run.refused(outcome, "TARGET_PROCESS_NOT_RUNNING",
                               f"no process of {package} is running on the target; no memory snapshot exists, "
                               f"and none is invented")
        except parsers.TargetOutputError as exc:
            return run.failed(outcome, f"the meminfo output could not be accepted as a snapshot of {package} ({exc})")
        return self._written(context, run, outcome, MEMINFO, output, text.encode("ascii"),
                             summary=f"Point-in-time memory snapshot of {package} on the Android target",
                             data={"package_name": package, "pid": pid, "bytes": len(text),
                                   "instrumentation": dict(MEMINFO_INSTRUMENTATION)})

    def _written(self, context, run, outcome, cap, output, body, summary, data):
        """Write the validated capture (never over an existing file) and offer its one candidate."""
        artifact_id, _, artifact_kind, media_type, evidence_type = OUTPUTS[cap]
        try:
            with open(output, "xb") as fh:
                fh.write(body)
        except FileExistsError:
            return run.refused(outcome, "INVALID_TOOL_REQUEST",
                               f"the workspace already holds {output.name}; an existing file is never reported as a "
                               f"new capture")
        candidate = EvidenceCandidate(
            evidence_type=evidence_type, capture_context=context.capability.execution_context, summary=summary,
            subject_kind="TASK", subject_ref="", source_adapter=ADAPTER_ID, source_capability=cap,
            generated_at=context.clock.now(), artifact_ids=(artifact_id,), limitations=LIMITATIONS[cap])
        return AdapterOutcome(ok=True, exit_code=outcome.exit_code, process=_without_output(outcome),
                              artifacts=(ArtifactSpec(artifact_id, artifact_kind, str(output), media_type=media_type,
                                                      description=f"{cap.split('.', 1)[1]} from the Android target"),),
                              evidence=(candidate,), mutation_performed=True, data=data, **run.record())


# ---------------------------------------------------------------- helpers

class _Runner:
    """Runs this execution's fixed ADB commands under one deadline, and records the last one."""

    def __init__(self, context, capture):
        self.context, self.capture = context, capture
        self.deadline = context.clock.monotonic() + context.timeout
        self.spec = None

    def run(self, argv, bound):
        remaining = max(0.001, self.deadline - self.context.clock.monotonic())
        self.spec = proc.ToolProcessSpec(executable=self.context.probe.tool_path, argv=argv,
                                         cwd=str(self.context.project_root), timeout=remaining,
                                         env=ENVIRONMENT_POLICY, capture_bytes=self.capture[bound])
        return self.context.run(self.spec)

    def step(self, argv, bound, name):
        """(outcome, None) when the command completed, else (outcome, failure outcome)."""
        outcome = self.run(argv, bound)
        if outcome.timed_out or outcome.truncated:
            return outcome, self.step_failure(outcome, name)
        if outcome.exit_code != 0:
            return outcome, self.failed(outcome, f"`adb {name}` exited {outcome.exit_code}")
        return outcome, None

    def ready(self, serial):
        """None when the target is connected and fully booted, else the refusal outcome."""
        outcome = self.run(state_argv(serial), "state")
        if outcome.timed_out or outcome.truncated:
            return self.step_failure(outcome, "get-state")
        state = outcome.raw_stdout.decode("ascii", errors="replace").strip()
        if outcome.exit_code != 0:
            if b"not found" in outcome.raw_stderr:
                return self.refused(outcome, "TARGET_DEVICE_UNAVAILABLE",
                                    f"ADB target {serial} is not connected; no other target is used instead")
            return self.refused(outcome, "TARGET_DEVICE_NOT_READY",
                                f"ADB target {serial} is not usable (for example unauthorized or offline)")
        if state != "device":
            return self.refused(outcome, "TARGET_DEVICE_NOT_READY",
                                f"ADB target {serial} is in state {state[:20]!r}, not 'device'")
        outcome = self.run(boot_argv(serial), "state")
        if outcome.timed_out or outcome.truncated:
            return self.step_failure(outcome, "getprop sys.boot_completed")
        if outcome.exit_code != 0 or outcome.raw_stdout.strip() != b"1":
            return self.refused(outcome, "TARGET_DEVICE_NOT_READY",
                                f"ADB target {serial} is connected but Android has not completed boot "
                                f"(sys.boot_completed is not 1)")
        return None

    def step_failure(self, outcome, what):
        if outcome.timed_out:
            return AdapterOutcome(ok=False, process=_without_output(outcome), detail=f"`adb {what}` timed out",
                                  **self.record())
        return self.failed(outcome, f"`adb {what}` output reached its capture bound; a partial capture is never "
                                    f"reported")

    def failed(self, outcome, detail):
        return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=_without_output(outcome), detail=detail,
                              **self.record())

    def refused(self, outcome, code, message):
        return AdapterOutcome(ok=True, exit_code=outcome.exit_code, process=_without_output(outcome),
                              diagnostics=(dg.make(code, message, ADAPTER_ID, self.context.capability.id),),
                              **self.record())

    def record(self):
        return dict(command=self.spec.command_for_provenance(), environment=self.spec.env.metadata())


def parse_version(raw):
    """(protocol version, Platform-Tools version) from `adb version`, or (None, None). Both lines are
    required; the Platform-Tools version is the one reported as the tool version. Nothing is guessed."""
    lines = bytes(raw).decode("utf-8", errors="replace").splitlines() if isinstance(raw, (bytes, bytearray)) else []
    protocol = _VERSION_LINE.fullmatch(lines[0].strip()) if lines else None
    tools = _TOOLS_LINE.fullmatch(lines[1].strip()) if len(lines) > 1 else None
    if not protocol or not tools:
        return None, None
    return protocol.group(1), tools.group(1)


def _refuse(capability_id, message):
    return AdapterOutcome(ok=True, diagnostics=(dg.make("INVALID_TOOL_REQUEST", message, ADAPTER_ID, capability_id),))


def _without_output(outcome):
    """The outcome without captured payload: target output (properties, pixels, memory text) is copied into the
    evidence file or parsed, never passed on. Exit code, timing, byte counts and truncation are kept."""
    return replace(outcome, stdout="", stderr="", raw_stdout=b"", raw_stderr=b"")
