"""Production engine adapter: Unity, with a batch plane (Phase 2C-5) and a live Editor plane (Phase 2C-6A, Scene
authoring Phase 2C-6B1).

Batch plane, process-driven, each capability STATELESS:

    unity.inspect-project      static project facts and package-source safety   READ_ONLY, no Unity process
    unity.run-editmode-tests   Unity Test Framework, EditMode, in batch mode     results.xml -> TEST_EVIDENCE
    unity.run-playmode-tests   Unity Test Framework, PlayMode, in batch mode     results.xml -> TEST_EVIDENCE

Live plane, driven by the fixed GPOS Editor bridge and one Human-approved session per GPOS project (see
live.py): install-bridge, status, attach, detach, inspect and the four Play Mode transitions, plus twelve Scene
authoring capabilities (authoring.py). It never launches,
quits, focuses or restarts an Editor and produces no evidence. The batch and live planes share one writer
resource, `EDITOR_PROJECT:<resolved GPOS project root>`: a live SESSION lease makes a batch run a conflict before
Unity is launched, and a batch run's lease makes an attach a conflict. The adapter's overall state model is
STATEFUL because it manages that long-lived session.

This is not a Unity automation interface. The caller never supplies an executable, an Editor version, a
method, C#, a menu item, a test filter, a graphics mode, a network destination or any Unity argument.
The only input is `unity_project`, a path inside the GPOS project root.

Discovery uses the Unity Hub Editor installation root only (never PATH: a Unity command-line tool that
is not the Editor may be installed under the name `unity`). Alpha.15 supports exactly one usable
Hub-installed Editor; a project must require exactly that version (`m_EditorVersion`), with no fallback,
upgrade or downgrade.

A test run is one fixed batch invocation:

    Unity -batchmode -projectPath <unity-project> -logFile <workspace>/editor.log
          -upmLogFile <workspace>/upm.log -cacheServerEnableDownload false -cacheServerEnableUpload false
          -runTests -testPlatform EditMode|PlayMode -testResults <workspace>/results.xml

never with -quit (the Test Framework exits the Editor when the run completes), -accept-apiupdate,
-noUpm, -nographics or -executeMethod. Before any launch the project is checked statically (layout,
exact version, package sources) and a pre-existing Unity project lock is a conflict; the GPOS
single-writer lease on the project is held by the foundation for the whole run. Package Manager
configuration is isolated per execution: empty GPOS-owned user and global configuration files in the
workspace and a GPOS runtime package cache, so the user's own configuration, tokens and registries are
never read or inherited.

Opening a Unity project changes Unity-managed project state (for example `.meta` files,
`Packages/packages-lock.json`, `ProjectSettings/*.asset`) and user-level Unity state outside the
project; the test capabilities are MUTATING and say so. Nothing is rolled back.
This module never starts a process itself and never imports the subprocess module.
"""

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from .. import diagnostics as dg
from .. import model
from .. import paths as tp
from .. import process as proc
from ..artifacts import ArtifactSpec
from ..capabilities import Capability, TimeoutPolicy
from ..evidence import EvidenceCandidate
from ..execution import AdapterOutcome
from . import authoring
from . import live
from . import project as up
from . import results as ur

ADAPTER_ID = "unity"
ADAPTER_VERSION = "1.0.0"
INSPECT = f"{ADAPTER_ID}.inspect-project"
EDITMODE = f"{ADAPTER_ID}.run-editmode-tests"
PLAYMODE = f"{ADAPTER_ID}.run-playmode-tests"
TEST_PLATFORMS = {EDITMODE: "EditMode", PLAYMODE: "PlayMode"}

HUB_ROOTS = {"darwin": ("/Applications/Unity/Hub/Editor",)}      # alpha.15: macOS only
EDITOR_IN_VERSION_DIR = {"darwin": ("Unity.app", "Contents", "MacOS", "Unity")}
PROBE_TIMEOUT = 120.0
CAPTURE_BYTES = 1024 * 1024
RESULTS_NAME = "results.xml"
LOG_NAME = "editor.log"
UPM_LOG_NAME = "upm.log"
UPM_USER_NAME = "upm-user.toml"
UPM_GLOBAL_NAME = "upm-global.toml"
WORKSPACE_FILES = (RESULTS_NAME, LOG_NAME, UPM_LOG_NAME, UPM_USER_NAME, UPM_GLOBAL_NAME)
UPM_CACHE = ("unity", "upm-cache")          # under the project's non-authoritative runtime area
NEVER = ("-quit", "-accept-apiupdate", "-noUpm", "-nographics", "-executeMethod")

PLACEHOLDERS = {"project": "<unity-project>", "workspace": "<workspace>"}

NETWORK_DISCLOSURE = (
    "GPOS provides no networking capability, takes no URL, host, endpoint, registry, proxy or credential and "
    "originates no network operation.",
    "The Unity process tree may use the network as a consequence of running: the Editor and its Licensing Client "
    "(licensing and entitlement), Unity services configuration, the Package Manager resolving supported "
    "dependencies from Unity's default package service, and project or test code Unity loads and runs.",
    "The Editor opens local PlayerConnection listening and discovery sockets.",
    "Custom scoped registries and Git or other remote package sources are refused before Unity starts; Package "
    "Manager user and global configuration are GPOS-owned per execution; Accelerator upload and download are "
    "disabled. No operating-system network confinement is claimed.",
)
LIMITATIONS = (
    "Unity Test Framework results from a batch-mode Editor on the development host: not target-runtime, device or "
    "performance evidence. PlayMode tests run inside the Editor.",
    "The whole test set of the platform ran; no filter was applied.",
    "Opening the project changed Unity-managed project state and user-level Unity state; nothing was rolled back.",
    "Failed tests are evidence of failure, never a passing result.",
)
SIDE_EFFECTS = ("opens the Unity project in batch mode: Unity writes Library/, Temp/, Logs/, UserSettings/ and may "
                "write .meta files, Packages/packages-lock.json, ProjectSettings/*.asset and the revision line of "
                "ProjectSettings/ProjectVersion.txt; user-level Unity state "
                "(Editor preferences, caches, logs, licensing client) changes; results, logs and Package Manager "
                "configuration go to the execution workspace, the package cache to the GPOS runtime area")

_project_note = "Input `unity_project`: a directory inside the GPOS project root (default `.`)."
_live_note = ("Live plane: acts through the fixed GPOS bridge in an Editor a Human opened; never launches, quits, "
              "focuses or restarts an Editor; produces no evidence.")
_playmode_note = live.PLAYMODE_LIMITATION
LIVE_SIDE_EFFECTS = {
    live.INSTALL: ("writes the audited GPOS bridge package into Packages/com.gpos.live-bridge/ of a closed Unity project "
                   "(staged in the GPOS runtime area, moved into place with one rename), or replaces an exact earlier "
                   "released bridge there through a recorded, crash-recoverable transaction; nothing else in the project "
                   "changes and nothing unknown is overwritten or removed"),
    live.ATTACH: ("after a Human approves inside the Unity Editor: takes the SESSION lease on the project and binds the "
                  "Editor's bridge to the session; proposal and grant state live in the Editor session only"),
    live.DETACH: ("unbinds the Editor's bridge and releases the SESSION lease after verification; for a proven clean "
                  "close, releases the lease; for a stale session, breaks it only after a Human approves recovery in "
                  "the Editor, recorded in the lease log"),
    live.ENTER: "asks the attached Editor to enter Play Mode (Editor state only)",
    live.PAUSE: "pauses the attached Editor's Play Mode (Editor state only)",
    live.RESUME: "resumes the attached Editor's paused Play Mode (Editor state only)",
    live.EXIT: "asks the attached Editor to exit Play Mode (Editor state only)",
}


def _live(cap_id, category, description, operation_class, state_model, lease_mode, timeout, **kw):
    return Capability(
        id=cap_id, category=category, description=description, operation_class=operation_class,
        state_model=state_model, execution_context=kw.pop("execution_context", "EDITOR"), requires_tool=False,
        requires_project=True, lease_mode=lease_mode, resource_kind="EDITOR_PROJECT",
        single_writer_required=operation_class == "MUTATING", input_kinds=kw.pop("input_kinds", ("unity_project",)),
        timeout=timeout, side_effect_scope=kw.pop("side_effect_scope", LIVE_SIDE_EFFECTS.get(cap_id, "NONE")),
        notes=kw.pop("notes", (_project_note, _live_note)), **kw)


LIVE_CAPABILITIES = (
    _live(live.INSTALL, "DEPLOY", "Install the fixed, audited GPOS live bridge package into a closed Unity project, or "
                                  "upgrade an exact earlier released bridge there; an identical package is left alone, "
                                  "anything else there is refused and never overwritten.", "MUTATING", "STATELESS",
          "EXECUTION",
          TimeoutPolicy(default=60.0, maximum=300.0), execution_context="OFFLINE_ANALYSIS", dry_run_supported=True,
          notes=(_project_note, "The project must be closed: an existing Temp/UnityLockfile is a conflict.")),
    _live(live.STATUS, "INSPECT", "Bridge and live-session facts for one Unity project, from the bridge's files and the "
                                  "Editor process identity; sends nothing to the Editor.", "READ_ONLY", "STATEFUL",
          "NONE", TimeoutPolicy(default=15.0, maximum=60.0)),
    _live(live.ATTACH, "RUN", "Attach a live session to the Unity Editor a Human has open: the Human approves inside the "
                              "Editor first, then GPOS takes the SESSION lease and the bridge binds the session.",
          "MUTATING", "STATEFUL", "SESSION_OPEN", TimeoutPolicy(default=300.0, maximum=900.0)),
    _live(live.DETACH, "RUN", "End a live session: unbind and release it, release it after a proven clean Editor close, "
                              "or recover a proven stale session after a Human approves recovery inside the Editor.",
          "MUTATING", "STATEFUL", "SESSION_CLOSE", TimeoutPolicy(default=300.0, maximum=900.0)),
    _live(live.INSPECT, "INSPECT", "Bounded structured facts from the attached Editor: bridge and session, focus, "
                                   "compilation and import state, Play Mode state, open scenes and a bounded "
                                   "hierarchy summary.", "READ_ONLY", "STATEFUL", "SESSION_REQUIRED",
          TimeoutPolicy(default=30.0, maximum=120.0)),
    _live(live.ENTER, "RUN", "Enter Play Mode in the attached Editor. " + _playmode_note, "MUTATING", "STATEFUL",
          "SESSION_REQUIRED", TimeoutPolicy(default=120.0, maximum=600.0)),
    _live(live.PAUSE, "RUN", "Pause Play Mode in the attached Editor. " + _playmode_note, "MUTATING", "STATEFUL",
          "SESSION_REQUIRED", TimeoutPolicy(default=30.0, maximum=120.0)),
    _live(live.RESUME, "RUN", "Resume paused Play Mode in the attached Editor. " + _playmode_note, "MUTATING",
          "STATEFUL", "SESSION_REQUIRED", TimeoutPolicy(default=30.0, maximum=120.0)),
    _live(live.EXIT, "RUN", "Exit Play Mode in the attached Editor. " + _playmode_note, "MUTATING", "STATEFUL",
          "SESSION_REQUIRED", TimeoutPolicy(default=120.0, maximum=600.0)),
)

_authoring_note = ("Scene authoring: objects are GlobalObjectId strings of Scene objects in saved, loaded Scenes; every "
                   "mutation carries the tokens of an earlier inspection, is refused as a conflict when they changed, runs "
                   "as one named Undo group and is read back and reverted on any mismatch. Edit Mode only; never "
                   "queued. Structured inputs are strict JSON text.")
AUTHORING_SIDE_EFFECT = ("changes the open Scene in the attached Editor as one named Undo group (the Scene becomes dirty; "
                         "nothing is saved); project Editor callbacks may run")
AUTHORING_SIDE_EFFECTS = {
    authoring.SAVE_SCENE: "saves one open, already saved Scene to its own path; project save callbacks may run",
}


def _authoring(cap_id, category, description, operation_class, timeout):
    side_effect = "NONE" if operation_class == "READ_ONLY" else AUTHORING_SIDE_EFFECTS.get(cap_id, AUTHORING_SIDE_EFFECT)
    return _live(cap_id, category, description, operation_class, "STATEFUL", "SESSION_REQUIRED", timeout,
                 input_kinds=authoring.input_kinds(cap_id), notes=(_project_note, _live_note, _authoring_note),
                 side_effect_scope=side_effect)


_short = TimeoutPolicy(default=30.0, maximum=120.0)
AUTHORING_CAPABILITIES = (
    _authoring(authoring.INSPECT_OBJECT, "INSPECT", "One GameObject of a saved Scene (name, state, parent, children, "
                                                    "components, Transform, prefab role) or one Scene's root objects, "
                                                    "with the optimistic-concurrency tokens authoring needs.",
               "READ_ONLY", _short),
    _authoring(authoring.COMPONENT_TYPES, "INSPECT", "The closed component catalog: every component type that can be "
                                                     "added, with its requirements, and the catalog digest.",
               "READ_ONLY", _short),
    _authoring(authoring.PROPERTIES, "INSPECT", "One Component's visible serialized properties with their kinds, "
                                                "values and whether each can be written, plus its token.",
               "READ_ONLY", _short),
    _authoring(authoring.CREATE, "TRANSFORM", "Create one empty GameObject in a saved, loaded Scene, at the root or "
                                              "under a parent.", "MUTATING", _short),
    _authoring(authoring.DELETE, "TRANSFORM", "Delete one GameObject and everything below it.", "MUTATING", _short),
    _authoring(authoring.SET_PARENT, "TRANSFORM", "Move one GameObject under another parent in the same Scene, or to "
                                                  "its root, keeping its local or its world pose.", "MUTATING",
               _short),
    _authoring(authoring.SET_GAMEOBJECT, "TRANSFORM", "Set a GameObject's name, active state, tag, layer or static "
                                                      "flags.", "MUTATING", _short),
    _authoring(authoring.SET_TRANSFORM, "TRANSFORM", "Set a GameObject's local position, rotation or scale.",
               "MUTATING", _short),
    _authoring(authoring.ADD_COMPONENT, "TRANSFORM", "Add one component of a catalogued type (and the components it "
                                                     "requires) to a GameObject.", "MUTATING", _short),
    _authoring(authoring.REMOVE_COMPONENT, "TRANSFORM", "Remove one component that nothing on its GameObject "
                                                        "requires.", "MUTATING", _short),
    _authoring(authoring.SET_PROPERTY, "TRANSFORM", "Write one allowlisted serialized property of a Component, "
                                                    "validated first and read back after.", "MUTATING", _short),
    _authoring(authoring.SAVE_SCENE, "TRANSFORM", "Save one open Scene that already has a path, to that path; never "
                                                  "Save As, never a dialog, never a new Scene asset.", "MUTATING",
               TimeoutPolicy(default=60.0, maximum=300.0)),
)

CAPABILITIES = (
    Capability(
        id=INSPECT, category="INSPECT", operation_class="READ_ONLY", state_model="STATELESS",
        execution_context="OFFLINE_ANALYSIS", requires_tool=False, requires_project=True,
        description="Static facts about one Unity project: layout, the exact Editor version it requires, and "
                    "package-source safety of Packages/manifest.json and Packages/packages-lock.json. Starts no "
                    "Unity process and needs no installed Editor.",
        input_kinds=("unity_project",), timeout=TimeoutPolicy(default=30.0, maximum=120.0),
        notes=(_project_note,)),
    Capability(
        id=EDITMODE, category="RUN", operation_class="MUTATING", state_model="STATELESS",
        execution_context="AUTOMATED_TEST", single_writer_required=True, resource_kind="EDITOR_PROJECT",
        dry_run_supported=True,
        description="Run the project's EditMode tests with the Unity Test Framework in a fresh batch-mode Editor and "
                    "report the NUnit results; offers TEST_EVIDENCE when at least one test executed.",
        input_kinds=("unity_project",), artifact_kinds=("REPORT", "LOG"),
        potential_evidence=(("TEST_EVIDENCE", "AUTOMATED_TEST"),),
        timeout=TimeoutPolicy(default=1800.0, maximum=3600.0), side_effect_scope=SIDE_EFFECTS,
        notes=(_project_note, "The project must require exactly the probed Editor version.")),
    Capability(
        id=PLAYMODE, category="RUN", operation_class="MUTATING", state_model="STATELESS",
        execution_context="AUTOMATED_TEST", single_writer_required=True, resource_kind="EDITOR_PROJECT",
        dry_run_supported=True,
        description="Run the project's PlayMode tests (in the Editor) with the Unity Test Framework in a fresh "
                    "batch-mode Editor and report the NUnit results; offers TEST_EVIDENCE when at least one test "
                    "executed.",
        input_kinds=("unity_project",), artifact_kinds=("REPORT", "LOG"),
        potential_evidence=(("TEST_EVIDENCE", "AUTOMATED_TEST"),),
        timeout=TimeoutPolicy(default=1800.0, maximum=3600.0), side_effect_scope=SIDE_EFFECTS,
        notes=(_project_note, "The project must require exactly the probed Editor version.")),
) + LIVE_CAPABILITIES + AUTHORING_CAPABILITIES

DESCRIPTOR = model.AdapterDescriptor(
    adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION, tool_family="ENGINE", target_tool="Unity Editor",
    adapter_kind="CLI", state_model="STATEFUL", supported_platforms=("MACOS",), capabilities=CAPABILITIES,
    network="TOOL_INHERENT", network_disclosure=NETWORK_DISCLOSURE,
    availability="exactly one Unity Editor installed under the Unity Hub Editor root "
                  "(/Applications/Unity/Hub/Editor/<version>/Unity.app); PATH is never used",
    compatibility_notes=(
        "Alpha.15 supports exactly one usable Hub-installed Editor and macOS only.",
        "Fixed batch command: no caller executable, argument, method, C#, filter, graphics mode or network setting.",
        "Remote package sources are refused; Package Manager configuration and cache are isolated per project.",
        "One adapter, two planes: the batch plane is process-driven (fixed batch-mode Editor invocations, each "
        "capability STATELESS); the live plane is fixed-bridge and session-driven (a Human-approved SESSION lease on "
        "the same EDITOR_PROJECT resource). adapter_kind stays CLI for alpha.16.",
        "The live plane never launches, quits, focuses or restarts an Editor, never injects input and runs no caller "
        "code; its Play Mode operations report Editor state only.",
        "Scene authoring (bridge 1.1.0) changes the open Scene of the attached Editor only through fixed commands: no "
        "prefab asset or Prefab Mode editing, no asset or cross-Scene reference, no array or managed-reference "
        "mutation, no Save As; applying serialized changes can run project Editor callbacks and no sandbox is claimed.",
    ))


class UnityAdapter(model.ToolAdapter):
    """`hub_roots`, `platform`, `capture_bytes` and `live_seams` are code-level seams for tests; a request can
    change none."""

    descriptor = DESCRIPTOR

    def __init__(self, hub_roots=None, platform=None, capture_bytes=CAPTURE_BYTES, live_seams=None):
        self._platform = platform or sys.platform
        self._hub_roots = tuple(hub_roots) if hub_roots is not None else HUB_ROOTS.get(self._platform, ())
        self._capture_bytes = capture_bytes
        self._live_seams = dict(live_seams or {})

    # ------------------------------------------------------------ discovery and probe

    def discover(self):
        """[(version, executable)] of Editors under the Hub roots, exact names only. Never PATH."""
        tail = EDITOR_IN_VERSION_DIR.get(self._platform)
        found = []
        if not tail:
            return found
        for root in self._hub_roots:
            try:
                versions = sorted(os.listdir(root))
            except OSError:
                continue
            for version in versions:
                if not up.EDITOR_VERSION.fullmatch(version):
                    continue
                path, ok = Path(root, version), True
                for part in tail:                   # every component must be a real entry with exactly this name
                    try:
                        entries = os.listdir(path)
                    except OSError:
                        ok = False
                        break
                    if part not in entries:
                        ok = False
                        break
                    path = path / part
                if ok and path.is_file() and not path.is_symlink() and os.access(path, os.X_OK):
                    found.append((version, str(path)))
        return found

    def probe(self):
        platform = model.current_platform()
        unusable = lambda reason: tuple((c.id, not c.requires_tool, "" if not c.requires_tool else reason)
                                        for c in CAPABILITIES)
        if self._platform not in EDITOR_IN_VERSION_DIR:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail="the Unity adapter supports macOS only in this release",
                                     capability_availability=unusable("platform not supported"))
        editors = self.discover()
        if not editors:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform,
                                     detail="no Unity Editor is installed under the Unity Hub Editor root",
                                     capability_availability=unusable("Unity Editor not found"))
        if len(editors) > 1:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, platform=platform,
                                     detail=f"{len(editors)} Unity Editors are installed "
                                            f"({', '.join(v for v, _ in editors)}); this release supports exactly one, "
                                            f"because per-project Editor selection needs a reviewed foundation "
                                            f"extension",
                                     capability_availability=unusable("more than one Unity Editor installed"))
        version, executable = editors[0]
        neutral = str(Path(tempfile.gettempdir()).resolve())
        try:
            out = proc.run_process(proc.ToolProcessSpec(executable=executable, argv=("-version",), cwd=neutral,
                                                        timeout=PROBE_TIMEOUT, env=proc.EnvironmentPolicy()), [neutral])
        except proc.ProcessSpecError as exc:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"the Unity Editor could not be started: {exc}",
                                     capability_availability=unusable("Unity Editor could not be started"))
        printed = bytes(out.raw_stdout).decode("utf-8", errors="replace").strip()
        if out.timed_out or out.truncated or out.exit_code != 0 or printed != version:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable, platform=platform,
                                     detail=f"`Unity -version` did not report the installation's version {version} "
                                            f"(exit {out.exit_code})",
                                     capability_availability=unusable("Unity Editor version not established"))
        return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=executable, tool_version=version,
                                 platform=platform, detail=f"Unity Editor {version} at {executable}",
                                 capability_availability=tuple((c.id, True, "") for c in CAPABILITIES))

    # ------------------------------------------------------------ execution

    def execute(self, request, context):
        cap = request.capability_id
        if cap in live.CAPABILITY_IDS:
            return live.execute(request, context, **self._live_seams)
        if cap in authoring.CAPABILITY_IDS:
            return authoring.execute(request, context, **self._live_seams)
        if cap not in (INSPECT, EDITMODE, PLAYMODE):
            raise AssertionError(f"{cap} is declared but not implemented")
        root = Path(context.project_root)
        try:
            project, summary = up.preflight(root, (request.inputs or {}).get("unity_project"))
        except up.ProjectProblem as problem:
            return AdapterOutcome(ok=True, diagnostics=(dg.make("ENGINE_PROJECT_UNSUPPORTED",
                                                                f"{problem.rule}: {problem.message}", ADAPTER_ID, cap),))
        if cap == INSPECT:
            return AdapterOutcome(ok=True, data=summary)
        return self._run_tests(cap, request, context, root, project, summary)

    def _run_tests(self, cap, request, context, root, project, summary):
        platform = TEST_PLATFORMS[cap]
        required, installed = summary["editor_version"], context.probe.tool_version
        if required != installed:
            return AdapterOutcome(ok=True, diagnostics=(dg.make(
                "ENGINE_EDITOR_VERSION_UNAVAILABLE",
                f"the project requires Unity {required}; the installed Editor is {installed}; no other version, "
                f"upgrade or downgrade is used", ADAPTER_ID, cap),))
        lock = project / "Temp" / "UnityLockfile"
        if lock.exists() or lock.is_symlink():
            return AdapterOutcome(ok=True, diagnostics=(dg.make(
                "ENGINE_PROJECT_LOCKED", "the Unity project has a Temp/UnityLockfile: another Editor may have it open; "
                                         "the lock is never removed or bypassed", ADAPTER_ID, cap),))
        workspace = Path(context.workspace)
        existing = [n for n in WORKSPACE_FILES if (workspace / n).exists() or (workspace / n).is_symlink()]
        if existing:
            return _refuse(cap, f"the workspace already holds {existing[0]}; an existing file is never reported as "
                                f"a new result")
        if context.dry_run:
            return AdapterOutcome(
                plan=(f"would run the {platform} tests of Unity project {summary['unity_project']!r} with Unity "
                      f"{installed} in batch mode and offer TEST_EVIDENCE (AUTOMATED_TEST) if at least one test ran",
                      "whether packages resolve, scripts compile, the Editor starts and how many tests execute is only "
                      "checked by a real execution",
                      "no Unity process, workspace, package cache, results or evidence is created by a dry run"),
                data=dict(summary, test_platform=platform))
        cache = tp.runtime_dir(root, *UPM_CACHE)
        cache.mkdir(parents=True, exist_ok=True)
        for name in (UPM_USER_NAME, UPM_GLOBAL_NAME):   # GPOS-owned, empty: nothing is inherited from the user
            with open(workspace / name, "x", encoding="utf-8"):
                pass
        argv = test_argv(project, workspace, platform)
        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv, cwd=str(workspace),
                                    timeout=context.timeout, env=upm_environment(workspace, cache),
                                    capture_bytes=self._capture_bytes)
        outcome = context.run(spec)
        record = dict(command=_command(spec, project, workspace), environment=spec.env.metadata())
        return self._classify(cap, context, outcome, record, workspace, summary, platform)

    def _classify(self, cap, context, outcome, record, workspace, summary, platform):
        results_path, log_path = workspace / RESULTS_NAME, workspace / LOG_NAME
        produced = tuple(spec for spec, present in (
            (ArtifactSpec("results", "REPORT", str(results_path), media_type="application/xml",
                          description="Unity Test Framework NUnit results"), results_path.is_file()),
            (ArtifactSpec("editor-log", "LOG", str(log_path), media_type="text/plain",
                          description="Unity Editor log of this run (machine and session identifiers, local paths)"),
             log_path.is_file())) if present)
        base = dict(process=outcome, artifacts=produced, mutation_performed=True, **record)
        data = dict(summary, test_platform=platform, exit_code=outcome.exit_code)
        if outcome.timed_out:
            return AdapterOutcome(ok=False, detail=f"the Unity {platform} test run timed out", data=data, **base)
        if results_path.is_file():
            try:
                counts = ur.read_results(results_path)
            except ur.ResultsProblem as exc:
                return AdapterOutcome(ok=False, exit_code=outcome.exit_code, detail=f"the test results could not be "
                                      f"accepted ({exc})", data=data, **base)
            data.update({k: counts[k] for k in ur.COUNTS}, result=counts["result"])
            consistent = (outcome.exit_code == 0 and counts["failed"] == 0) or \
                         (outcome.exit_code == 2 and counts["failed"] > 0)
            if not consistent:
                return AdapterOutcome(ok=False, exit_code=outcome.exit_code, data=data,
                                      detail=f"Unity exited {outcome.exit_code} but its results record "
                                             f"{counts['failed']} failed test(s); the run is not trusted", **base)
            if counts["total"] == 0:
                return AdapterOutcome(ok=True, exit_code=outcome.exit_code, data=data, diagnostics=(dg.make(
                    "ENGINE_TESTS_NOT_EXECUTED", f"the {platform} run executed no test; exit code 0 is not a pass",
                    ADAPTER_ID, cap),), **base)
            diagnostics = ()
            if counts["failed"]:
                diagnostics = (dg.make("TESTS_FAILED", f"{counts['failed']} of {counts['total']} {platform} test(s) "
                                                       f"failed", ADAPTER_ID, cap),)
            subject = context.request.subject.ref
            candidate = EvidenceCandidate(
                evidence_type="TEST_EVIDENCE", capture_context="AUTOMATED_TEST",
                summary=f"Unity {summary['editor_version']} {platform} test run for {context.request.subject.kind} "
                        f"{subject}: {counts['total']} test(s), {counts['passed']} passed, {counts['failed']} failed, "
                        f"{counts['skipped']} skipped, {counts['inconclusive']} inconclusive ({counts['result']})",
                subject_kind=context.request.subject.kind, subject_ref="", source_adapter=ADAPTER_ID,
                source_capability=cap, generated_at=context.clock.now(), artifact_ids=("results",),
                limitations=LIMITATIONS)
            return AdapterOutcome(ok=True, exit_code=outcome.exit_code, evidence=(candidate,), data=data,
                                  diagnostics=diagnostics, **base)
        cause = ur.classify_log(ur.log_tail(log_path), outcome.stdout)
        if cause == ur.LICENSE_UNAVAILABLE:
            return AdapterOutcome(ok=True, exit_code=outcome.exit_code, data=data, diagnostics=(dg.make(
                "ENGINE_LICENSE_UNAVAILABLE", "Unity reported that no valid Editor licence was available; no licence "
                                              "action is ever taken", ADAPTER_ID, cap),), **base)
        if cause == ur.PROJECT_LOCKED:
            return AdapterOutcome(ok=True, exit_code=outcome.exit_code, data=data, diagnostics=(dg.make(
                "ENGINE_PROJECT_LOCKED", "Unity refused the project because another instance has it open",
                ADAPTER_ID, cap),), **base)
        detail = ("script compilation failed before the test run started; no results were written"
                  if cause == ur.COMPILE_ERROR else
                  f"Unity exited {outcome.exit_code} without test results; the cause could not be classified from "
                  f"its log")
        return AdapterOutcome(ok=False, exit_code=outcome.exit_code, detail=detail, data=dict(data, cause=cause), **base)


# ---------------------------------------------------------------- the fixed command surface

def test_argv(project, workspace, platform):
    workspace = Path(workspace)
    return ("-batchmode", "-projectPath", str(project), "-logFile", str(workspace / LOG_NAME),
            "-upmLogFile", str(workspace / UPM_LOG_NAME), "-cacheServerEnableDownload", "false",
            "-cacheServerEnableUpload", "false", "-runTests", "-testPlatform", platform,
            "-testResults", str(workspace / RESULTS_NAME))


def upm_environment(workspace, cache):
    """The foundation's allowlist plus isolated Package Manager configuration and cache (absolute paths)."""
    workspace = Path(workspace)
    return proc.EnvironmentPolicy(overrides=(
        ("UPM_USER_CONFIG_FILE", str((workspace / UPM_USER_NAME).resolve())),
        ("UPM_GLOBAL_CONFIG_FILE", str((workspace / UPM_GLOBAL_NAME).resolve())),
        ("UPM_CACHE_ROOT", str(Path(cache).resolve()))))


def _refuse(capability_id, message):
    return AdapterOutcome(ok=True, diagnostics=(dg.make("INVALID_TOOL_REQUEST", message, ADAPTER_ID, capability_id),))


def _command(spec, project, workspace):
    """The recorded command with the project and workspace replaced by placeholders."""
    project, workspace = str(project), str(workspace)

    def swap(arg):
        if arg == project:
            return PLACEHOLDERS["project"]
        if arg.startswith(workspace + os.sep):
            return PLACEHOLDERS["workspace"] + "/" + Path(arg).name
        return arg
    return replace(spec, argv=tuple(swap(a) for a in spec.argv)).command_for_provenance()
