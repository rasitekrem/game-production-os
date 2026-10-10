"""The engine-neutral player runtime adapter (Phase 2C-8, alpha.22).

Six capabilities run exactly one already-built game on this host and observe it, without generic process or desktop
control:

    player.install-capture-helper  copy this exact helper release to its one fixed location (no process)
    player.launch                  revalidate one build, start the helper's supervise mode, which launches its Player
    player.status                  read-only facts about the session's Player (no process, no write)
    player.capture-screenshot      one exact-window PNG through the helper (one LaunchServices invocation)
    player.capture-video           one exact-window silent H.264 clip of 1-15 s through the helper (one invocation)
    player.stop                    end the Player through its supervisor, or the reviewed in-process fallback (no process)

Authority: the PLAYER_RUNTIME SESSION lease on the project is the only lifecycle authority; it is written once at
launch with the stable pre-launch facts and never changed. The runtime directory's files are observations bound to it
(see runtime.py). The caller names a build (`build_id`), a session (`session_id`) and, for a video, a duration —
never a pid, window, screen, region, path, command, argument, environment or helper mode.

The adapter's one external tool is the GPOS Player Helper (helper.py); `invocation.py` is the only module that starts a
process, and each capability starts at most one. The macOS specifics (libproc, AppKit, CoreGraphics, LaunchServices,
application bundles) stay in the backend modules; `contract.py` is the portable public surface.
"""

import json
import os
import platform as host_platform
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import diagnostics as dg
from .. import leases as lease_mod
from .. import model
from .. import paths as tp
from ..artifacts import ArtifactSpec
from ..capabilities import Capability, TimeoutPolicy
from ..evidence import EvidenceCandidate
from ..execution import AdapterOutcome
from . import appkit as appkit_backend
from . import capture as cap_media
from . import contract as c
from . import helper as hp
from . import invocation as inv
from . import logs
from . import macos as macos_backend
from . import resolver
from . import runtime as rt

ADAPTER_ID, ADAPTER_VERSION = c.ADAPTER_ID, c.ADAPTER_VERSION
HOST_LOCATION = "USER_APPLICATIONS_GPOS"

NETWORK_DISCLOSURE = (
    "GPOS provides no network capability: the player adapter accepts no URL, host, endpoint, proxy or credential and "
    "GPOS originates no network operation.",
    "The game build it launches is the caller's own code and may use the network as that code decides (including the "
    "engine's analytics).",
    "No operating-system network confinement is claimed.",
)
PERMISSION_INSTRUCTION = ("A Human grants Screen Recording in System Settings → Privacy & Security → Screen & System "
                          "Audio Recording → GposPlayerHelper (~/Applications/GPOS/GposPlayerHelper.app). GPOS never "
                          "changes this setting; a changed helper needs the grant again.")

_note_project = "Project-bound: the build, the SESSION lease and every runtime file live in this GPOS project."
_note_session = ("One runtime per project: the PLAYER_RUNTIME SESSION lease is the only lifecycle authority; the "
                 "foundation session id is the runtime handle.")
SIDE_EFFECTS = {
    c.INSTALL: ("copies this exact helper release to ~/Applications/GPOS/GposPlayerHelper.app (creating ~/Applications "
                "and ~/Applications/GPOS when absent) through a staging directory beside it and one atomic no-replace "
                "rename; never overwrites, repairs or removes anything there and never compiles, signs, downloads or "
                "changes a privacy setting"),
    c.LAUNCH: ("starts the verified helper's supervise mode, which launches the build's Player with only "
               "-logFile <runtime>/player.log; takes the PLAYER_RUNTIME SESSION lease and writes the runtime directory's "
               "files. The game itself may write its Application Support folder, its Preferences (PlayerPrefs) file, its "
               "Saved Application State, logs and, on a crash, a crash report; GPOS never reads or deletes them"),
    c.SCREENSHOT: ("launches the exact verified helper through the reviewed LaunchServices path (/usr/bin/open) once; "
                   "writes only the bounded capture and result files into this execution workspace; does not modify the "
                   "Player, the project's authority or any privacy (TCC) setting"),
    c.STOP: ("asks the session's supervisor to quit the Player (AppKit terminate, then SIGKILL of its own child after "
             "a 10 s grace) or, when the supervisor is gone, asks the re-proven Player to quit in-process and SIGKILLs "
             "only the re-proven process; writes stop records in the runtime directory and the stop artifacts in this "
             "workspace; releases the SESSION lease. The game may write its save, preferences and state files while "
             "quitting"),
}
SIDE_EFFECTS[c.VIDEO] = SIDE_EFFECTS[c.SCREENSHOT]


def _session_cap(cap_id, category, description, operation_class, lease_mode, timeout, **kw):
    return Capability(id=cap_id, category=category, description=description, operation_class=operation_class,
                      state_model="STATEFUL", execution_context="DIAGNOSTIC_RUNTIME",
                      single_writer_required=operation_class == "MUTATING", resource_kind=c.RESOURCE_KIND,
                      lease_mode=lease_mode, requires_project=True, caller_output_dir_allowed=False, timeout=timeout,
                      side_effect_scope=SIDE_EFFECTS.get(cap_id, "NONE") if operation_class == "MUTATING" else "NONE",
                      **kw)


CAPABILITIES = (
    Capability(
        id=c.INSTALL, category="DEPLOY",
        description="Install this exact, released GPOS Player Helper at its one fixed location, or confirm that it is "
                    "already installed; anything else there is refused and never overwritten.",
        operation_class="MUTATING", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_tool=False, requires_project=True, dry_run_supported=True, artifact_kinds=("JSON",),
        timeout=TimeoutPolicy(default=60.0, maximum=300.0), side_effect_scope=SIDE_EFFECTS[c.INSTALL],
        caller_output_dir_allowed=False, host_location=HOST_LOCATION,
        notes=(_note_project, "No caller destination, source, compiler, signing identity or privacy change; a Human "
                              "grants Screen Recording afterwards.")),
    _session_cap(c.LAUNCH, "RUN", "Launch exactly one revalidated build of this project under the helper's supervisor "
                                  "and open the project's player runtime session once its identity is proven.",
                 "MUTATING", "SESSION_OPEN", TimeoutPolicy(default=60.0, maximum=180.0), requires_tool=True,
                 detached_spawn=True, artifact_kinds=("JSON",), notes=(_note_project, _note_session)),
    _session_cap(c.STATUS, "INSPECT", "Read-only facts about the session's Player: binding, kernel identity, supervisor, "
                                      "exit, build trust, helper state, window presence and a sanitized log tail.",
                 "READ_ONLY", "SESSION_REQUIRED", TimeoutPolicy(default=30.0, maximum=60.0), requires_tool=False,
                 notes=(_note_project, "Starts no process and writes nothing; Screen Recording is not probed.")),
    _session_cap(c.SCREENSHOT, "CAPTURE", "Capture the session Player's one window as a PNG (longest edge at most "
                                          "1920 px, never upscaled) through the helper.",
                 "MUTATING", "SESSION_REQUIRED", TimeoutPolicy(default=60.0, maximum=120.0), requires_tool=True,
                 artifact_kinds=("IMAGE", "JSON"), potential_evidence=(("VISUAL_EVIDENCE", "DIAGNOSTIC_RUNTIME"),),
                 notes=(_note_project, "Exact window only: no display, area or other window; no caller target.")),
    _session_cap(c.VIDEO, "CAPTURE", "Record the session Player's one window as a silent H.264 MP4 of 1-15 s (longest "
                                     "edge at most 1920 px, at most 30 fps) through the helper.",
                 "MUTATING", "SESSION_REQUIRED", TimeoutPolicy(default=90.0, maximum=120.0), requires_tool=True,
                 input_kinds=(c.DURATION_INPUT,), artifact_kinds=("VIDEO", "JSON"),
                 potential_evidence=(("MOTION_EVIDENCE", "DIAGNOSTIC_RUNTIME"),),
                 notes=(_note_project, "No audio of any kind is recorded; no FFprobe or FFmpeg runs here.")),
    _session_cap(c.STOP, "RUN", "End the session's Player through its supervisor (or the reviewed in-process fallback "
                                "when the supervisor is gone), classify how it ended and close the session.",
                 "MUTATING", "SESSION_CLOSE", TimeoutPolicy(default=60.0, maximum=120.0), requires_tool=False,
                 input_kinds=(c.RECOVER_INPUT,), artifact_kinds=("LOG", "JSON"),
                 potential_evidence=(("RUNTIME_EVIDENCE", "DIAGNOSTIC_RUNTIME"),),
                 notes=(_note_project, "Build drift never prevents stopping a proven Player; an unproven process is "
                                       "never signalled.")),
)

DESCRIPTOR = model.AdapterDescriptor(
    adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION, tool_family="DEVICE", target_tool="GPOS Player Helper",
    adapter_kind="DEVICE", state_model="STATEFUL", supported_platforms=("MACOS", "WINDOWS"), capabilities=CAPABILITIES,
    network="TOOL_INHERENT", network_disclosure=NETWORK_DISCLOSURE,
    availability="macOS 14 or later and this exact GPOS Player Helper release installed at "
                  "~/Applications/GPOS/GposPlayerHelper.app (player.install-capture-helper)",
    compatibility_notes=(
        "Windows supports launch/status/stop only through the fixed owned-Job lifecycle and current compatible "
        "manifest/2; helper installation and capture remain macOS-only. No Windows runtime evidence is produced.",
        "alpha.22 runs alpha.21 Unity macOS application-bundle builds of the same project on this macOS host only.",
        "The probe's tool path is the installed helper executable's real absolute path, which names the local home "
        "directory (and so the user name) in provenance.",
        "Screen capture uses /usr/bin/open (macOS LaunchServices) to start the helper so that Screen Recording is "
        "attributed to the helper itself; the recorded command is truthfully /usr/bin/open.",
    ))


def _diag(code, message, cap, details=None):
    return dg.make(code, message, ADAPTER_ID, cap, None, details)


def _refuse(cap, code, message, details=None, data=None, mutation=False, artifacts=(), command=None, environment=None):
    return AdapterOutcome(ok=True, diagnostics=(_diag(code, message, cap, details),), data=data,
                          mutation_performed=mutation, artifacts=tuple(artifacts), command=command,
                          environment=environment)


def _utc(sec, usec=0):
    return datetime.fromtimestamp(sec + usec / 1e6, tz=timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Refused(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details


class PlayerAdapter(model.ToolAdapter):
    """Seams (code-level only, never reachable from a request): the OS backends, the helper location, sleeping and
    the monotonic clock, so tests can stand in for macOS without real processes."""

    descriptor = DESCRIPTOR

    def __init__(self, os_backend=macos_backend, appkit=appkit_backend, host_location=None, sleep=time.sleep,
                 monotonic=time.monotonic):
        self.os = os_backend
        self.appkit = appkit
        self._host = host_location or (lambda: str(tp.host_location(HOST_LOCATION)))
        self._sleep, self._monotonic = sleep, monotonic

    # ------------------------------------------------------------ probe

    def helper_bundle(self):
        """The installed helper's canonical path: what the kernel reports for a process running it."""
        return hp.install_path(os.path.realpath(self._host()))

    def probe(self):
        if sys.platform == "win32":
            from .windows import WindowsLifecycle
            result = WindowsLifecycle().probe()
            from dataclasses import replace
            return replace(result, capability_availability=tuple(
                (cap.id, cap.id in (c.LAUNCH, c.STATUS, c.STOP),
                 "" if cap.id in (c.LAUNCH, c.STATUS, c.STOP) else "macOS-only helper installation or capture")
                for cap in CAPABILITIES))
        platform = model.current_platform()
        unusable = lambda reason: tuple((cap.id, cap.id in (c.INSTALL, c.STATUS, c.STOP), reason)
                                        for cap in CAPABILITIES)
        if platform != "MACOS":
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail="the player adapter supports macOS only in alpha.22",
                                     capability_availability=unusable("not macOS"))
        major = int((host_platform.mac_ver()[0] or "0").split(".")[0] or 0)
        if major < 14:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, platform=platform,
                                     detail=f"macOS {host_platform.mac_ver()[0]} is older than 14",
                                     capability_availability=unusable("macOS 14 or later is required"))
        bundle = self.helper_bundle()
        state, reason = hp.classify(bundle)
        if state != hp.EXACT:
            code = "PLAYER_HELPER_ABSENT" if state == hp.ABSENT else "PLAYER_HELPER_UNTRUSTED"
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail=f"GPOS Player Helper {state}: {reason}",
                                     capability_availability=unusable(f"helper {state}"),
                                     diagnostics=(_diag(code, f"GPOS Player Helper {state}: {reason}", None),))
        m = hp.release()
        return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=str(hp.executable(bundle)),
                                 tool_version=f"{m['helper_version']}+{m['bundle_digest'][:12]}", platform=platform,
                                 detail=f"GPOS Player Helper {m['helper_version']} (EXACT)",
                                 capability_availability=tuple((cap.id, True, "") for cap in CAPABILITIES))

    # ------------------------------------------------------------ dispatch

    def execute(self, request, context):
        if sys.platform == "win32":
            if request.capability_id not in (c.LAUNCH, c.STATUS, c.STOP):
                return _refuse(request.capability_id, "PLATFORM_UNSUPPORTED",
                               "Windows Player supports lifecycle only; helper installation and capture are macOS-only")
            from .windows import WindowsLifecycle
            return WindowsLifecycle().execute(request, context)
        handler = {c.INSTALL: self._install, c.LAUNCH: self._launch, c.STATUS: self._status,
                   c.SCREENSHOT: self._capture, c.VIDEO: self._capture, c.STOP: self._stop}.get(request.capability_id)
        if handler is None:
            raise AssertionError(f"{request.capability_id} is declared but not implemented")
        invocation = inv.ExternalInvocation(context, request.capability_id)
        try:
            outcome = handler(request, context, invocation)
        except Refused as exc:
            outcome = _refuse(request.capability_id, exc.code, str(exc), exc.details,
                              mutation=invocation.kind is not None)
        if invocation.spec is not None and outcome.command is None:
            from dataclasses import replace
            outcome = replace(outcome, command=invocation.command(), environment=invocation.environment())
        return outcome

    # ------------------------------------------------------------ shared checks

    def _root(self, context):
        return Path(context.project_root).resolve()

    def _restated_build(self, request, session, required):
        """The canonical build fields a request restates must be exactly the session's (provenance comes from them)."""
        if request.device is not None:
            raise Refused("INVALID_TOOL_REQUEST", "the player adapter runs on this host; device has no meaning here")
        if request.target_platform not in (None, "MACOS"):
            raise Refused("INVALID_TOOL_REQUEST", "alpha.22 runs macOS builds only; target_platform must be MACOS")
        if required and request.build_id is None:
            raise Refused("INVALID_TOOL_REQUEST", "this capability restates the session's build_id (evidence names it)")
        if request.build_id is not None and request.build_id != session["build_id"]:
            raise Refused("INVALID_TOOL_REQUEST", f"build_id {request.build_id!r} is not the session's build")
        if request.build_revision is not None and request.build_revision != session.get("build_revision"):
            raise Refused("INVALID_TOOL_REQUEST", "build_revision is not the session's build revision")

    def _helper_exact(self):
        bundle = self.helper_bundle()
        state, reason = hp.classify(bundle)
        if state == hp.ABSENT:
            raise Refused("PLAYER_HELPER_ABSENT", "the GPOS Player Helper is not installed; run "
                                                  "player.install-capture-helper")
        if state != hp.EXACT:
            raise Refused("PLAYER_HELPER_UNTRUSTED", f"the installed GPOS Player Helper is not this release ({reason})")
        return bundle

    def _foreign(self, application_id):
        """{pid: kind} of every live instance of the application, by bundle identifier (libproc) and by AppKit."""
        found = {f["pid"]: f for f in self.os.same_application(application_id)}
        pids = set(found) | set(self.appkit.running_pids(application_id))
        return pids, found

    def _runtime_dir(self, root, session):
        directory = root / tp.RUNTIME_DIR / session["runtime_dir"]
        if directory != rt.player_dir(root, session["launch_request_id"]):
            raise Refused("LIVE_SESSION_MISMATCH", "the session names another runtime directory")
        return directory

    def _binding(self, root, session):
        """(runtime dir, binding or None, reason)."""
        directory = self._runtime_dir(root, session)
        problem = rt.chain_problem(root, directory)
        if problem:
            return directory, None, problem
        try:
            return directory, rt.binding(directory / rt.BINDING, session), None
        except rt.RecordProblem as exc:
            return directory, None, str(exc)

    def _identity(self, binding):
        p = binding["player"]
        return self.os.identity(p["pid"], p["start_sec"], p["start_usec"], p["executable"])

    def _supervisor(self, binding):
        """ALIVE when exactly the bound supervisor instance (pid and kernel start time) still runs the bound helper
        code (the kernel's CDHash). Its path is not part of the proof: the helper install may have been moved or
        removed after launch, and the running supervisor still owns the Player."""
        s = binding["supervisor"]
        f = self.os.facts(s["pid"])
        if f is None:
            return c.GONE
        if (f["start_sec"], f["start_usec"]) != (s["start_sec"], s["start_usec"]) or self.os.cdhash(s["pid"]) != s["cdhash"]:
            return c.NOT_THIS_PROCESS
        return c.ALIVE

    def _exit(self, directory, session):
        try:
            return rt.exit_record(directory / rt.EXIT, session["session_id"], session["nonce"])
        except rt.RecordProblem:
            return None

    def _public_process(self, root, binding):
        p = binding["player"]
        return c.public_process(p["pid"], _utc(p["start_sec"], p["start_usec"]),
                                os.path.relpath(p["executable"], root))

    # ------------------------------------------------------------ install

    def _install(self, request, context, invocation):
        cap = c.INSTALL
        host = context.host_location
        if host is None or Path(host) != Path(self._host()):
            raise AssertionError("the install location must be the foundation-resolved host location")
        if request.inputs:
            raise Refused("INVALID_TOOL_REQUEST", "the helper install takes no input")
        bundle = hp.install_path(host)
        m = hp.release()
        state, reason = hp.classify(bundle)
        leftovers = hp.staging_leftovers(host)
        if context.dry_run:
            plan = (f"helper at the fixed location: {state} ({reason})",
                    "would install this release" if state == hp.ABSENT else
                    "nothing to do" if state == hp.EXACT else "would refuse: never overwritten")
            return AdapterOutcome(ok=True, plan=plan, data={"state": state})
        diags = [_diag("PLAYER_INSTALL_STAGING_LEFTOVER", f"{len(leftovers)} staging director(ies) of an interrupted "
                                                          f"install remain beside the helper; GPOS never removes them",
                       cap, {"names": leftovers})] if leftovers else []
        try:
            final, created = hp.install(host, request.request_id)
        except hp.InstallConflict as exc:
            return AdapterOutcome(ok=True, diagnostics=tuple(diags + [_diag("PLAYER_HELPER_INSTALL_CONFLICT", str(exc), cap)]),
                                  data={"state": hp.classify(bundle)[0]})
        record = {"schema": "gpos.player.install-record/1", "helper_version": m["helper_version"],
                  "bundle_digest": m["bundle_digest"], "cdhashes": m["signature"]["cdhashes"],
                  "source_digest": m["source"]["digest"], "location": f"{HOST_LOCATION}/{hp.BUNDLE_NAME}",
                  "state_before": state, "created": created, "staging_leftovers": len(leftovers)}
        path = Path(context.workspace) / "install-record.json"
        rt.write_once(path, record)
        code = "PLAYER_HELPER_INSTALLED" if created else "PLAYER_HELPER_EXACT"
        message = ("this helper release was installed. " if created else "this helper release is already installed. ") \
            + PERMISSION_INSTRUCTION
        return AdapterOutcome(ok=True, mutation_performed=created, data=dict(record),
                              artifacts=(ArtifactSpec("install-record", "JSON", str(path), "application/json",
                                                      "the helper install record"),),
                              diagnostics=tuple(diags + [_diag(code, message, cap)]))

    # ------------------------------------------------------------ launch

    def _launch(self, request, context, invocation):
        cap = c.LAUNCH
        root = self._root(context)
        if request.inputs:
            raise Refused("INVALID_TOOL_REQUEST", "player.launch takes no input; it names a build with build_id")
        if request.device is not None or request.target_platform not in (None, "MACOS"):
            raise Refused("INVALID_TOOL_REQUEST", "alpha.22 launches macOS builds on this host only")
        if request.build_id is None:
            raise Refused("INVALID_TOOL_REQUEST", "player.launch needs the build_id of a completed build of this project")
        bundle = self._helper_exact()
        helper_exe = str(hp.executable(bundle))
        try:
            target = resolver.resolve(root, request.build_id)
        except (resolver.BuildProblem, OSError) as exc:
            raise Refused("PLAYER_BUILD_INVALID", str(exc)) from None
        if request.build_revision is not None and request.build_revision != target.build_revision:
            raise Refused("INVALID_TOOL_REQUEST", "build_revision is not the revision this build recorded")
        pids, _ = self._foreign(target.application_id)
        if pids:
            raise Refused("PLAYER_RUNTIME_CONFLICT", f"{len(pids)} instance(s) of {target.application_id} already run "
                                                     f"(any build, any path, however launched); none is adopted or "
                                                     f"signalled", {"instances": len(pids)})
        runtime_dir = Path(context.workspace)
        if runtime_dir != rt.player_dir(root, request.request_id) or rt.chain_problem(root, runtime_dir):
            raise AssertionError("the launch workspace must be the canonical player runtime directory")
        m = hp.release()
        session_id, nonce = uuid.uuid4().hex, uuid.uuid4().hex
        session = {"session_id": session_id, "phase": c.PHASE_LAUNCHING, "launch_request_id": request.request_id,
                   "launched_at": context.clock.now(), "build_id": target.build_id,
                   "build_revision": target.build_revision, "manifest_sha256": target.manifest_sha256,
                   "tree_digest": target.tree_digest, "unity_build_guid": target.unity_build_guid,
                   "application_id": target.application_id, "executable": target.executable,
                   "executable_dev": target.dev, "executable_ino": target.ino,
                   "runtime_dir": runtime_dir.relative_to(root / tp.RUNTIME_DIR).as_posix(), "nonce": nonce,
                   "helper": {"version": m["helper_version"], "bundle_digest": m["bundle_digest"],
                              "cdhashes": m["signature"]["cdhashes"]}}
        _, problems = context.sessions.open(session)
        if problems:
            return AdapterOutcome(ok=True, diagnostics=tuple(problems))
        rt.write_once(runtime_dir / rt.SUPERVISOR_REQUEST, {
            "schema": "gpos.player.supervisor-request/1", "session_id": session_id, "nonce": nonce,
            "launch_request_id": request.request_id, "application_id": target.application_id,
            "executable": target.executable, "executable_dev": target.dev, "executable_ino": target.ino,
            "commit_deadline_s": 60, "stop_grace_s": c.STOP_GRACE_SECONDS})
        handle = invocation.supervise(helper_exe, runtime_dir)
        try:
            binding = self._prove_launch(root, runtime_dir, session, target, handle.pid, helper_exe)
        except Refused as exc:
            return self._abandon_launch(root, runtime_dir, session, exc, context)
        rt.write_once(runtime_dir / rt.COMMIT, rt.control(None, "gpos.player.commit/1", session_id, nonce))
        context.sessions.confirm()
        process = self._public_process(root, binding)
        data = {"session_id": session_id, "application_id": target.application_id,
                "build": {"build_id": target.build_id, "build_revision": target.build_revision,
                          "manifest_sha256": target.manifest_sha256, "payload_tree_digest": target.tree_digest,
                          "unity_build_guid": target.unity_build_guid, "target_platform": "MACOS",
                          "kind": target.kind},
                "process": process, "runtime_dir": session["runtime_dir"], "launched_at": session["launched_at"]}
        return AdapterOutcome(ok=True, mutation_performed=True, data=data,
                              artifacts=(ArtifactSpec("runtime-binding", "JSON", str(runtime_dir / rt.BINDING),
                                                      "application/json", "the verified runtime binding"),),
                              diagnostics=(_diag("PLAYER_LAUNCHED", f"session {session_id} runs {target.build_id} as pid "
                                                                    f"{process['pid']}", cap),))

    def _prove_launch(self, root, runtime_dir, session, target, supervisor_pid, helper_exe):
        """The handshake, proven against the kernel, the build and the foreign scan; then runtime-binding.json."""
        deadline = self._monotonic() + c.HANDSHAKE_WAIT_SECONDS
        hs_path = runtime_dir / rt.HANDSHAKE
        while not rt.exists(hs_path):
            exit_rec = self._exit(runtime_dir, session)
            if exit_rec is not None:
                raise Refused("PLAYER_SUPERVISOR_FAILED", "the supervisor ended without launching the Player "
                                                          f"({exit_rec['status']})", {"exit": exit_rec["status"]})
            if self.os.facts(supervisor_pid) is None:
                raise Refused("PLAYER_SUPERVISOR_FAILED", "the supervisor ended without a handshake")
            if self._monotonic() > deadline:
                raise Refused("PLAYER_HANDSHAKE_INVALID", f"no handshake within {c.HANDSHAKE_WAIT_SECONDS} s")
            self._sleep(0.05)
        try:
            hs = rt.handshake(hs_path, session["session_id"], session["nonce"])
        except rt.RecordProblem as exc:
            raise Refused("PLAYER_HANDSHAKE_INVALID", str(exc)) from None
        s, p = hs["supervisor"], hs["player"]
        if s["pid"] != supervisor_pid or s["executable"] != helper_exe:
            raise Refused("PLAYER_HANDSHAKE_INVALID", "the handshake names another supervisor")
        if self.os.identity(s["pid"], s["start_sec"], s["start_usec"], s["executable"]) != c.PROVEN:
            raise Refused("PLAYER_HANDSHAKE_INVALID", "the supervisor is not the process that wrote the handshake")
        kernel_cdhash = self.os.cdhash(s["pid"])
        if kernel_cdhash is None or kernel_cdhash != s["cdhash"] or kernel_cdhash not in hp.cdhashes().values():
            raise Refused("PLAYER_HANDSHAKE_INVALID", "the running supervisor is not the released helper code")
        if p["ppid"] != s["pid"] or p["pgid"] != p["pid"] or p["executable"] != target.executable:
            raise Refused("PLAYER_IDENTITY_UNPROVEN", "the handshake's Player is not the supervisor's child running "
                                                      "the build's executable in its own process group")
        if self._exit(runtime_dir, session) is not None:
            raise Refused("PLAYER_EXITED_DURING_LAUNCH", "the Player ended before the launch committed")
        if self.os.identity(p["pid"], p["start_sec"], p["start_usec"], p["executable"]) != c.PROVEN:
            raise Refused("PLAYER_IDENTITY_UNPROVEN", "the Player's kernel identity does not match the handshake")
        if self.os.file_identity(target.executable) != (target.dev, target.ino):
            raise Refused("PLAYER_BUILD_INVALID", "the Player executable file changed during the launch")
        trust, why = resolver.revalidates(root, session)
        if trust != c.VALID:
            raise Refused("PLAYER_BUILD_INVALID", f"the build no longer revalidates after the launch: {why}")
        pids, _ = self._foreign(target.application_id)
        if pids - {p["pid"]}:
            raise Refused("PLAYER_RUNTIME_CONFLICT", "another instance of the same application appeared during the "
                                                     "launch", {"instances": len(pids - {p['pid']})})
        binding = {"schema": "gpos.player.runtime-binding/1", "session_id": session["session_id"],
                   "nonce": session["nonce"], "build_id": session["build_id"],
                   "manifest_sha256": session["manifest_sha256"],
                   "supervisor": {"pid": s["pid"], "start_sec": s["start_sec"], "start_usec": s["start_usec"],
                                  "executable": s["executable"], "cdhash": s["cdhash"]},
                   "player": {"pid": p["pid"], "start_sec": p["start_sec"], "start_usec": p["start_usec"],
                              "executable": p["executable"], "dev": target.dev, "ino": target.ino},
                   "handshake_at": hs["spawned_at"], "bound_at": _utc(time.time())}
        try:
            rt.write_once(runtime_dir / rt.BINDING, binding)
        except (OSError, rt.RecordProblem) as exc:
            raise Refused("PLAYER_HANDSHAKE_INVALID", f"the runtime binding could not be written once ({exc})") from None
        return binding

    def _abandon_launch(self, root, runtime_dir, session, refused, context):
        """The launch did not commit: ask the supervisor to abandon its child, then release the session only when the
        Player is proven to have ended (or never started); otherwise keep it, unresolved."""
        cap = c.LAUNCH
        reason = {"PLAYER_HANDSHAKE_INVALID": "HANDSHAKE_INVALID", "PLAYER_IDENTITY_UNPROVEN": "IDENTITY_UNPROVEN",
                  "PLAYER_BUILD_INVALID": "BUILD_INVALID", "PLAYER_RUNTIME_CONFLICT": "RUNTIME_CONFLICT"}.get(
            refused.code, "LAUNCH_FAILED")
        try:
            rt.write_once(runtime_dir / rt.ABORT, rt.control(None, "gpos.player.abort/1", session["session_id"],
                                                             session["nonce"], reason=reason))
        except FileExistsError:
            pass
        deadline = self._monotonic() + c.STOP_GRACE_SECONDS + c.KILL_WAIT_SECONDS
        exit_rec = self._exit(runtime_dir, session)
        while exit_rec is None and self._monotonic() < deadline:
            self._sleep(0.1)
            exit_rec = self._exit(runtime_dir, session)
        tail = logs.tail(runtime_dir / rt.PLAYER_LOG, root, runtime_dir, 5)
        details = {"cause": refused.details or {}, "log_tail": tail}
        ended = exit_rec is not None or not self._foreign(session["application_id"])[0]
        if exit_rec is not None and exit_rec["status"] != "NOT_STARTED" and refused.code == "PLAYER_HANDSHAKE_INVALID" \
                and exit_rec["stop"]["reason"] is None:
            refused = Refused("PLAYER_EXITED_DURING_LAUNCH", "the Player ended before the launch committed")
        if ended:   # the session is never confirmed, so the foundation releases it
            details["exit"] = None if exit_rec is None else {k: exit_rec[k] for k in ("status", "code", "signal")}
            return AdapterOutcome(ok=True, mutation_performed=True, data={"session_id": None},
                                  diagnostics=(_diag(refused.code, str(refused), cap, details),))
        context.sessions.confirm()   # a possibly running Player is never left without its session
        return AdapterOutcome(ok=True, mutation_performed=True, data={"session_id": session["session_id"]},
                              diagnostics=(_diag(refused.code, str(refused), cap, details),
                                           _diag("PLAYER_LAUNCH_UNRESOLVED",
                                                 f"session {session['session_id']} is kept: the launch did not commit "
                                                 f"and its Player is not proven to have ended; stop or recover it",
                                                 cap)))

    # ------------------------------------------------------------ status

    def _status(self, request, context, invocation):
        if request.inputs:
            raise Refused("INVALID_TOOL_REQUEST", "player.status takes no input")
        root = self._root(context)
        record = context.session
        session = record["session"]
        directory, binding, why = self._binding(root, session)
        trust, drift = resolver.revalidates(root, session)
        helper_state = hp.classify(self.helper_bundle())[0]
        exit_rec = self._exit(directory, session)
        data = {"session_id": session["session_id"], "application_id": session["application_id"],
                "build": {"build_id": session["build_id"], "trust": trust, "drift": drift},
                "helper": helper_state,
                "capture_permission": c.NOT_CHECKED if helper_state == hp.EXACT else c.UNKNOWN,
                "exit": None if exit_rec is None else self._exit_view(exit_rec),
                "log_tail": logs.tail(directory / rt.PLAYER_LOG, root, directory) if not rt.chain_problem(root, directory)
                else []}
        if binding is None:
            data.update(phase=c.LAUNCHING_UNRESOLVED, identity=c.UNPROVEN, supervisor=c.UNKNOWN, process=None,
                        window=None, reason=why)
        else:
            identity = self._identity(binding)
            windows = self.os.eligible_windows(binding["player"]["pid"]) if identity == c.PROVEN else []
            data.update(phase=c.BOUND, identity=identity, supervisor=self._supervisor(binding),
                        process=self._public_process(root, binding),
                        window={"present": len(windows) == 1, "candidates": len(windows)} if identity == c.PROVEN
                        else None)
        diags = (_diag("PLAYER_BUILD_DRIFT", f"build {session['build_id']} drifted: {drift}", c.STATUS),) \
            if trust != c.VALID else ()
        return AdapterOutcome(ok=True, data=data, diagnostics=diags)

    @staticmethod
    def _exit_view(e):
        return {"status": e["status"], "code": e["code"], "signal": e["signal"], "committed": e["committed"],
                "stop_reason": e["stop"]["reason"], "kill_sent": e["stop"]["kill_sent"]}

    # ------------------------------------------------------------ capture

    def _capture(self, request, context, invocation):
        cap = request.capability_id
        video = cap == c.VIDEO
        root = self._root(context)
        session = context.session["session"]
        self._restated_build(request, session, required=True)
        duration = None
        if video:
            duration = _whole_seconds((request.inputs or {}).get(c.DURATION_INPUT))
            if duration is None or not c.MIN_VIDEO_SECONDS <= duration <= c.MAX_VIDEO_SECONDS:
                raise Refused("INVALID_TOOL_REQUEST", f"{c.DURATION_INPUT} must be a whole number of seconds from "
                                                      f"{c.MIN_VIDEO_SECONDS} to {c.MAX_VIDEO_SECONDS}")
        elif request.inputs:
            raise Refused("INVALID_TOOL_REQUEST", "player.capture-screenshot takes no input")
        bundle = self._helper_exact()
        directory, binding, why = self._binding(root, session)
        if binding is None:
            raise Refused("PLAYER_IDENTITY_UNPROVEN", f"the session has no usable runtime binding ({why})")
        identity = self._identity(binding)
        if identity != c.PROVEN:
            raise Refused("PLAYER_RUNTIME_GONE" if identity == c.GONE else "PLAYER_IDENTITY_UNPROVEN",
                          f"the session's Player is {identity}")
        trust, drift = resolver.revalidates(root, session)
        if trust != c.VALID:
            raise Refused("CAPTURE_BUILD_DRIFT", f"nothing is captured: {drift}")
        p = binding["player"]
        windows = self.os.eligible_windows(p["pid"])
        if len(windows) != 1:
            raise Refused("CAPTURE_WINDOW_NOT_FOUND" if not windows else "CAPTURE_WINDOW_AMBIGUOUS",
                          f"the Player has {len(windows)} capturable window(s)")
        workspace = Path(context.workspace)
        if workspace != rt.player_dir(root, request.request_id) or rt.chain_problem(root, workspace):
            raise AssertionError("a capture workspace must be its canonical player execution directory")
        nonce = uuid.uuid4().hex
        deadline = (c.SETTLE_SECONDS + duration + 35) if video else 30
        rt.write_once(workspace / rt.HELPER_REQUEST, {
            "schema": "gpos.player.helper-request/1", "mode": "VIDEO" if video else "SHOT",
            "request_id": request.request_id, "request_nonce": nonce, "session_id": session["session_id"],
            "target": {"pid": p["pid"], "start_sec": p["start_sec"], "start_usec": p["start_usec"],
                       "executable": p["executable"], "application_id": session["application_id"]},
            "settle_s": c.SETTLE_SECONDS, "max_edge_px": c.MAX_EDGE_PX, "duration_s": duration,
            "fps": c.VIDEO_FPS if video else None, "max_bytes": c.MAX_MP4_BYTES if video else c.MAX_PNG_BYTES,
            "deadline_s": deadline})
        kind = c.VIDEO_KIND if video else c.SHOT
        process = invocation.launch_services(kind, bundle, workspace, timeout=min(float(context.timeout), deadline + 15))
        record = dict(command=invocation.command(), environment=invocation.environment())
        public = _quiet(process)
        fail = lambda code, message, details=None: AdapterOutcome(
            ok=True, mutation_performed=True, process=public, diagnostics=(_diag(code, message, cap, details),), **record)
        if process.timed_out:
            return fail("CAPTURE_FAILED", "the capture helper did not finish in time; nothing was published")
        if hp.classify(bundle)[0] != hp.EXACT:
            return fail("CAPTURE_HELPER_IDENTITY_MISMATCH", "the installed helper changed around the capture")
        try:
            result = self._helper_result(workspace, request.request_id, nonce, session["session_id"], kind, bundle)
        except Refused as exc:
            return fail(exc.code, str(exc))
        if result["outcome"] != "CAPTURED":
            code, message = _capture_failure(result)
            return fail(code, message, {"rule": result["rule"], "sck_called": result["sck_called"]})
        if self._identity(binding) != c.PROVEN:
            return fail("CAPTURE_TARGET_EXITED", "the Player is no longer the proven process")
        trust, drift = resolver.revalidates(root, session)
        if trust != c.VALID:
            return fail("CAPTURE_BUILD_DRIFT", f"the build drifted during the capture: {drift}")
        media = result["media"]
        name = "runtime-video.mp4" if video else "runtime-screenshot.png"
        try:
            facts = (cap_media.validate_mp4(workspace / name, duration, media["width"], media["height"]) if video else
                     cap_media.validate_png(workspace / name, media["width"], media["height"]))
            _window_proof(result, p["pid"])
        except (cap_media.MediaProblem, Refused) as exc:
            return fail("CAPTURE_OUTPUT_INVALID", str(exc))
        capture_record = self._capture_record(root, session, binding, result, facts, video, duration, nonce)
        rt.write_once(workspace / "capture-record.json", capture_record, bound=16384)
        media_id = "runtime-video" if video else "runtime-screenshot"
        artifacts = (ArtifactSpec(media_id, "VIDEO" if video else "IMAGE", str(workspace / name),
                                  "video/mp4" if video else "image/png",
                                  "exact-window capture of the session's Player"),
                     ArtifactSpec("capture-record", "JSON", str(workspace / "capture-record.json"), "application/json",
                                  "the capture's session, build, process, helper and window binding"))
        evidence = EvidenceCandidate(
            evidence_type="MOTION_EVIDENCE" if video else "VISUAL_EVIDENCE", capture_context="DIAGNOSTIC_RUNTIME",
            summary=(f"{duration} s silent exact-window recording" if video else "exact-window screenshot")
                    + f" of build {session['build_id']} running locally on macOS",
            subject_kind="", subject_ref="", source_adapter=ADAPTER_ID, source_capability=cap, generated_at="",
            artifact_ids=(media_id, "capture-record"), limitations=c.LIMITATIONS + c.CAPTURE_LIMITATIONS)
        return AdapterOutcome(ok=True, mutation_performed=True, process=public, artifacts=artifacts,
                              evidence=(evidence,), data={"session_id": session["session_id"], "media": facts,
                                                          "window": result["window"], "timing": result["timing"]},
                              **record)

    def _helper_result(self, workspace, request_id, nonce, session_id, kind, bundle):
        try:
            r = rt.read_json(workspace / rt.HELPER_RESULT, bound=16384)
        except rt.RecordProblem as exc:
            raise Refused("CAPTURE_FAILED", f"the helper gave no trustworthy result ({exc}); nothing was published") \
                from None
        keys = {"schema", "mode", "request_id", "request_nonce", "session_id", "outcome", "rule", "sck_called",
                "helper", "window", "media", "identity", "timing"}
        if set(r) != keys or r["schema"] != "gpos.player.helper-result/1":
            raise Refused("CAPTURE_FAILED", "the helper result does not hold exactly its keys")
        if (r["mode"], r["request_id"], r["request_nonce"], r["session_id"]) != (kind, request_id, nonce, session_id):
            raise Refused("CAPTURE_HELPER_IDENTITY_MISMATCH", "the helper result answers another request")
        h = r["helper"]
        if not (isinstance(h, dict) and h.get("version") == hp.version()
                and h.get("executable") == str(hp.executable(bundle)) and h.get("bundle_path") == str(bundle)
                and h.get("cdhash") in hp.cdhashes().values()):
            raise Refused("CAPTURE_HELPER_IDENTITY_MISMATCH", "the helper that answered is not the verified release")
        if r["outcome"] not in ("CAPTURED", "REFUSED", "FAILED") or not isinstance(r["sck_called"], bool):
            raise Refused("CAPTURE_FAILED", "the helper result's outcome is malformed")
        if r["outcome"] == "CAPTURED" and not (isinstance(r["media"], dict) and isinstance(r["window"], dict)
                                              and r["identity"] == {"before": True, "after": True}):
            raise Refused("CAPTURE_FAILED", "a CAPTURED result lacks its media, window or identity proof")
        return r

    def _capture_record(self, root, session, binding, result, facts, video, duration, nonce):
        m = hp.release()
        return {"schema": "gpos.player.capture-record/1", "session_id": session["session_id"],
                "build": {"build_id": session["build_id"], "build_revision": session.get("build_revision"),
                          "manifest_sha256": session["manifest_sha256"], "payload_tree_digest": session["tree_digest"],
                          "unity_build_guid": session["unity_build_guid"], "target_platform": "MACOS",
                          "kind": resolver.MACOS_APP_BUNDLE},
                "application_id": session["application_id"],
                "process": dict(self._public_process(root, binding), identity_before=c.PROVEN, identity_after=c.PROVEN),
                "capture": {"mechanism": "MACOS_SCREENCAPTUREKIT_WINDOW", "kind": "VIDEO" if video else "SCREENSHOT",
                            "duration_s": duration, "settle_s": c.SETTLE_SECONDS, "max_edge_px": c.MAX_EDGE_PX,
                            "request_nonce": nonce, "timing": result["timing"]},
                "helper": {"version": m["helper_version"], "bundle_digest": m["bundle_digest"],
                           "release_cdhashes": m["signature"]["cdhashes"], "reported_cdhash": result["helper"]["cdhash"],
                           "reported_pid": result["helper"]["pid"],
                           "reported_started_at": _utc(result["helper"]["start_sec"], result["helper"]["start_usec"])},
                "window_proof": {k: result["window"][k] for k in ("owner_pid", "layer", "on_screen", "alpha_positive",
                                                                  "candidates", "same_after_settle")},
                "media": facts, "limitations": list(c.LIMITATIONS + c.CAPTURE_LIMITATIONS)}

    # ------------------------------------------------------------ stop

    def _stop(self, request, context, invocation):
        cap = c.STOP
        root = self._root(context)
        record = context.session
        session = record["session"]
        inputs = dict(request.inputs or {})
        recover = {True: True, False: False, "true": True, "false": False}.get(inputs.pop(c.RECOVER_INPUT, False)) \
            if isinstance(inputs.get(c.RECOVER_INPUT, False), (bool, str)) else None
        if inputs or recover is None:
            raise Refused("INVALID_TOOL_REQUEST", f"player.stop accepts only {c.RECOVER_INPUT} (true or false)")
        self._restated_build(request, session, required=False)
        own = record.get("owner_id") == lease_mod.session_owner(request.actor)
        if not own and not recover:
            raise Refused("LIVE_SESSION_MISMATCH", f"session {session['session_id']} belongs to "
                                                   f"{record.get('owner_id')!r}; another owner may only recover it "
                                                   f"with {c.RECOVER_INPUT} once its Player is proven gone")
        directory, binding, why = self._binding(root, session)
        trust, drift = resolver.revalidates(root, session)
        diags = [_diag("PLAYER_BUILD_DRIFT", f"build {session['build_id']} drifted ({drift}); the stop continues",
                       cap)] if trust != c.VALID else []
        if binding is None:
            return self._stop_unresolved(root, directory, record, session, own, why, diags, context)
        identity = self._identity(binding)
        if not own and identity in (c.PROVEN, c.UNPROVEN):
            raise Refused("LIVE_SESSION_MISMATCH", "another owner's Player is still running; only its owner stops it")
        path, kill_sent = "NONE", False
        if identity == c.PROVEN:
            path, kill_sent = self._end_player(directory, session, binding, request.request_id)
        identity = self._identity(binding)
        exit_rec = self._exit(directory, session)
        stop_class, detail = classify_stop(exit_rec, identity, kill_sent, path)
        artifacts, evidence = self._stop_artifacts(root, directory, session, binding, request, context, stop_class,
                                                   detail, exit_rec, trust, path)
        diags += [_diag("PLAYER_EVIDENCE_WITHHELD", "no runtime-log evidence: the end was not observed by the "
                                                    "supervisor, the build trust is lost or no build_id was restated",
                        cap)] if not evidence and stop_class != c.OUTCOME_UNKNOWN else []
        data = {"session_id": session["session_id"], "classification": stop_class, **detail, "path": path,
                "build_trust": trust, "closed": False}
        if stop_class == c.OUTCOME_UNKNOWN:
            return AdapterOutcome(ok=True, mutation_performed=path != "NONE", data=data, artifacts=artifacts,
                                  diagnostics=tuple(diags + [_diag("PLAYER_STOP_OUTCOME_UNKNOWN",
                                                                   "the Player could not be proven to have ended; the "
                                                                   "session is kept", cap)]))
        closed = self._close(context, record, session, own, f"player stop: {stop_class}")
        if isinstance(closed, AdapterOutcome):
            return closed
        data["closed"] = True
        return AdapterOutcome(ok=True, mutation_performed=True, data=data, artifacts=artifacts, evidence=evidence,
                              diagnostics=tuple(diags + closed + [_diag("PLAYER_STOPPED",
                                                                        f"session {session['session_id']}: {stop_class}",
                                                                        cap, {"classification": stop_class})]))

    def _end_player(self, directory, session, binding, stop_rid):
        """('SUPERVISOR' | 'FALLBACK', kill sent by this stop). The proven Player is asked to end: through the running
        supervisor when it is proven, otherwise (or when it stays silent) by the reviewed in-process fallback."""
        sid, nonce = session["session_id"], session["nonce"]
        if len(rt.stop_records(directory, "stop-intent")) + len(rt.stop_records(directory, "fallback")) \
                >= rt.MAX_STOP_RECORDS:
            raise Refused("PLAYER_STOP_OUTCOME_UNKNOWN", "this session has reached its bound of stop attempts")
        if self._supervisor(binding) == c.ALIVE:
            rt.write_once(directory / f"stop-intent-{stop_rid}.json",
                          rt.control(None, "gpos.player.stop-intent/1", sid, nonce, stop_request_id=stop_rid))
            deadline = self._monotonic() + c.STOP_GRACE_SECONDS + c.KILL_WAIT_SECONDS + 3
            while self._monotonic() < deadline:
                if self._exit(directory, session) is not None:
                    return "SUPERVISOR", False
                self._sleep(0.1)
            if self._identity(binding) != c.PROVEN:
                return "SUPERVISOR", False
        # The fallback: the supervisor is gone or silent and the Player is still proven.
        p = binding["player"]
        rt.write_once(directory / f"fallback-{stop_rid}.json",
                      rt.control(None, "gpos.player.fallback/1", sid, nonce, stop_request_id=stop_rid))
        deadline = self._monotonic() + c.STOP_GRACE_SECONDS
        accepted, last = False, -1.0
        while self._monotonic() < deadline:
            if self._identity(binding) != c.PROVEN:
                return "FALLBACK", False
            now = self._monotonic()
            if not accepted and now - last >= 0.5:
                last = now
                _, accepted = self.appkit.request_terminate(p["pid"], session["application_id"], p["executable"])
            self._sleep(0.1)
        if self._identity(binding) != c.PROVEN:
            return "FALLBACK", False
        rt.write_once(directory / f"kill-{stop_rid}.json",
                      rt.control(None, "gpos.player.kill/1", sid, nonce, stop_request_id=stop_rid))
        sent = self.os.kill_proven(p["pid"], p["start_sec"], p["start_usec"], p["executable"])
        deadline = self._monotonic() + c.KILL_WAIT_SECONDS
        while self._monotonic() < deadline and self._identity(binding) == c.PROVEN:
            self._sleep(0.1)
        return "FALLBACK", sent

    def _stop_unresolved(self, root, directory, record, session, own, why, diags, context):
        """No runtime binding: never signal. Ask the supervisor (if any) to abandon its child, then close only when
        the application is proven not to run."""
        cap = c.STOP
        try:
            if not rt.chain_problem(root, directory):
                rt.write_once(directory / rt.ABORT, rt.control(None, "gpos.player.abort/1", session["session_id"],
                                                               session["nonce"], reason="STOP_UNRESOLVED"))
        except FileExistsError:
            pass
        deadline = self._monotonic() + c.STOP_GRACE_SECONDS + c.KILL_WAIT_SECONDS + 3
        exit_rec = self._exit(directory, session)
        while exit_rec is None and self._monotonic() < deadline and self._foreign(session["application_id"])[0]:
            self._sleep(0.1)
            exit_rec = self._exit(directory, session)
        running = self._foreign(session["application_id"])[0]
        if running:
            return AdapterOutcome(ok=True, mutation_performed=True,
                                  data={"session_id": session["session_id"], "classification": c.OUTCOME_UNKNOWN,
                                        "reason": why, "closed": False},
                                  diagnostics=tuple(diags + [_diag("PLAYER_LAUNCH_UNRESOLVED",
                                                                   "the session never bound a Player and an instance of "
                                                                   "the application still runs; GPOS signals nothing it "
                                                                   "cannot prove — quit the game, then stop again", cap,
                                                                   {"instances": len(running)})]))
        stop_class, detail = classify_stop(exit_rec, c.GONE, False, "NONE")
        closed = self._close(context, record, session, own, f"player stop of an unresolved launch: {stop_class}")
        if isinstance(closed, AdapterOutcome):
            return closed
        return AdapterOutcome(ok=True, mutation_performed=True,
                              data={"session_id": session["session_id"], "classification": stop_class, **detail,
                                    "path": "NONE", "closed": True, "reason": why},
                              diagnostics=tuple(diags + closed + [_diag("PLAYER_STOPPED", f"session "
                                                                        f"{session['session_id']}: {stop_class}", cap,
                                                                        {"classification": stop_class})]))

    def _close(self, context, record, session, own, reason):
        """[diagnostics] after releasing the session, or an AdapterOutcome when the release was refused."""
        if own:
            _, problems = context.sessions.close(session["session_id"])
            return AdapterOutcome(ok=True, mutation_performed=True, diagnostics=tuple(problems)) if problems else []
        _, problems = context.sessions.recover(record.get("token"), f"{reason}; the Player is proven gone")
        if problems:
            return AdapterOutcome(ok=True, mutation_performed=True, diagnostics=tuple(problems))
        return [_diag("PLAYER_SESSION_RECOVERED", f"session {session['session_id']} of {record.get('owner_id')!r} was "
                                                  f"recovered: its Player is proven gone", c.STOP)]

    def _stop_artifacts(self, root, directory, session, binding, request, context, stop_class, detail, exit_rec,
                        trust, path):
        workspace = Path(context.workspace)
        artifacts, evidence = [], ()
        text, facts = (None, None) if rt.chain_problem(root, directory) else \
            logs.sanitized(directory / rt.PLAYER_LOG, root, directory)
        if text is not None:
            (workspace / "runtime-log.txt").write_text(text, "utf-8")
            artifacts.append(ArtifactSpec("runtime-log", "LOG", str(workspace / "runtime-log.txt"), "text/plain",
                                          "the Player's sanitized, bounded log"))
        record = {"schema": "gpos.player.runtime-record/1", "session_id": session["session_id"],
                  "build": {"build_id": session["build_id"], "build_revision": session.get("build_revision"),
                            "manifest_sha256": session["manifest_sha256"], "payload_tree_digest": session["tree_digest"],
                            "unity_build_guid": session["unity_build_guid"], "target_platform": "MACOS"},
                  "application_id": session["application_id"], "process": self._public_process(root, binding),
                  "stop": {"classification": stop_class, **detail, "path": path}, "build_trust": trust,
                  "log": facts, "limitations": list(c.LIMITATIONS)}
        rt.write_once(workspace / "runtime-record.json", record, bound=16384)
        artifacts.append(ArtifactSpec("runtime-record", "JSON", str(workspace / "runtime-record.json"),
                                      "application/json", "the runtime's identity, build and how it ended"))
        observed = exit_rec is not None and exit_rec["status"] != "NOT_STARTED" and path in ("SUPERVISOR", "NONE")
        if text is not None and observed and trust == c.VALID and request.build_id is not None \
                and stop_class in (c.GRACEFUL_STOP, c.FORCED_STOP, c.EXITED, c.CRASHED):
            evidence = (EvidenceCandidate(
                evidence_type="RUNTIME_EVIDENCE", capture_context="DIAGNOSTIC_RUNTIME",
                summary=f"sanitized Player log of build {session['build_id']} running locally on macOS ({stop_class})",
                subject_kind="", subject_ref="", source_adapter=ADAPTER_ID, source_capability=c.STOP,
                generated_at="", artifact_ids=("runtime-log", "runtime-record"),
                limitations=c.LIMITATIONS + ("The log is sanitized for credentials and local paths only; the game's "
                                             "own messages are otherwise unchanged.",)),)
        return tuple(artifacts), evidence


def _whole_seconds(value):
    """An int, or a plain decimal-digit string as the CLI passes it; never a bool, a float or anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 2:
        return int(value)
    return None


def classify_stop(exit_rec, identity, kill_sent, path):
    """(class, detail) of how a session's Player ended. Exact when the supervisor observed it; otherwise only what is
    proven: a kill this stop sent, a process gone without an observation, or nothing proven at all."""
    if exit_rec is not None:
        stop = exit_rec["stop"]
        detail = {"code": exit_rec["code"], "signal": exit_rec["signal"], "observed": True}
        if exit_rec["status"] == "NOT_STARTED":
            return c.GONE_UNOBSERVED, dict(detail, observed=False)
        if stop["kill_sent"] or (exit_rec["status"] == "SIGNALED" and exit_rec["signal"] == 9 and kill_sent):
            return c.FORCED_STOP, detail
        if exit_rec["status"] == "SIGNALED":
            return c.CRASHED, detail
        if stop["reason"] == "STOP_INTENT" and stop["terminate_accepted"] and exit_rec["code"] == 0:
            return c.GRACEFUL_STOP, detail
        return c.EXITED, detail
    detail = {"code": None, "signal": None, "observed": False}
    if identity in (c.PROVEN, c.UNPROVEN):   # alive: proven, or the same instance whose executable moved
        return c.OUTCOME_UNKNOWN, detail
    if kill_sent:
        return c.FORCED_STOP, dict(detail, signal=9)
    return c.GONE_UNOBSERVED, detail


def _window_proof(result, pid):
    w = result["window"]
    if not (w.get("owner_pid") == pid and w.get("candidates") == 1 and w.get("layer") == 0 and w.get("on_screen") is True
            and w.get("alpha_positive") is True and w.get("same_after_settle") is True):
        raise Refused("CAPTURE_WINDOW_OWNER_MISMATCH", "the helper's window proof does not bind the session's Player")


CAPTURE_RULES = {
    "PERMISSION_NOT_GRANTED": "CAPTURE_PERMISSION_REQUIRED", "TARGET_NOT_PROVEN": "PLAYER_IDENTITY_UNPROVEN",
    "WINDOW_NOT_FOUND": "CAPTURE_WINDOW_NOT_FOUND", "WINDOW_AMBIGUOUS": "CAPTURE_WINDOW_AMBIGUOUS",
    "WINDOW_NOT_OWNED_BY_PID": "CAPTURE_WINDOW_OWNER_MISMATCH", "WINDOW_CHANGED": "CAPTURE_WINDOW_OWNER_MISMATCH",
    "TARGET_EXITED": "CAPTURE_TARGET_EXITED", "STREAM_STOPPED": "CAPTURE_TARGET_EXITED",
    "OUTPUT_INVALID": "CAPTURE_OUTPUT_INVALID", "OUTPUT_TOO_LARGE": "CAPTURE_OUTPUT_INVALID",
}


def _capture_failure(result):
    rule = result.get("rule")
    code = CAPTURE_RULES.get(rule, "CAPTURE_FAILED")
    if code == "CAPTURE_PERMISSION_REQUIRED":
        if result.get("sck_called") is not False:
            return "CAPTURE_HELPER_IDENTITY_MISMATCH", "the helper reported a permission refusal after using capture"
        return code, "Screen Recording is not granted to the GPOS Player Helper; nothing was captured. " \
            + PERMISSION_INSTRUCTION
    return code, f"the capture helper reported {result.get('outcome')} {rule}; nothing was published"


def _quiet(outcome):
    """The launcher's outcome without its captured text (LaunchServices output names local paths)."""
    from dataclasses import replace
    return replace(outcome, stdout="", stderr="", raw_stdout=b"", raw_stderr=b"")
