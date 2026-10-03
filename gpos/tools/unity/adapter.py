"""Production engine adapter: Unity, with a batch plane (Phase 2C-5) and a live Editor plane (Phase 2C-6A, Scene
authoring Phase 2C-6B1, asset references and asset authoring Phase 2C-6B2A, prefab authoring Phase 2C-6B2B, source
synchronization and compilation facts Phase 2C-6C).

Batch plane, process-driven, each capability STATELESS:

    unity.inspect-project      static project facts and package-source safety   READ_ONLY, no Unity process
    unity.run-editmode-tests   Unity Test Framework, EditMode, in batch mode     results.xml -> TEST_EVIDENCE
    unity.run-playmode-tests   Unity Test Framework, PlayMode, in batch mode     results.xml -> TEST_EVIDENCE

Build plane (Phase 2C-7, alpha.21, build.py), batch-mode and STATELESS as well, through the one fixed GPOS build
entry of the audited bridge package; no evidence:

    unity.inspect-build-configuration   the existing macOS build configuration, whether it is buildable, its token
    unity.build-player                  build exactly that configuration into the execution workspace; manifest

Live plane, driven by the fixed GPOS Editor bridge and one Human-approved session per GPOS project (see
live.py): install-bridge, status, attach, detach, inspect and the four Play Mode transitions, plus thirteen Scene
authoring capabilities (authoring.py), seven asset capabilities (assets.py), nine prefab capabilities
(prefabs.py) and four source and compilation capabilities (sources.py). It never launches,
quits, focuses or restarts an Editor and produces no evidence. The batch and live planes share one writer
resource, `EDITOR_PROJECT:<resolved GPOS project root>`: a live SESSION lease makes a batch run a conflict before
Unity is launched, and a batch run's lease makes an attach a conflict. The adapter's overall state model is
STATEFUL because it manages that long-lived session.

This is not a Unity automation interface. The caller never supplies an executable, an Editor version, a
method, C#, a menu item, a test filter, a graphics mode, a network destination or any Unity argument.
The inputs are `unity_project`, a path inside the GPOS project root, and for a build the inspected
`expected_configuration_token`.

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
exact version, package sources) and its Unity project lock is proven read-only (project_lock.py): an Editor
of this user with exactly this project open, or a lock the OS reports held, is a conflict; so is anything
that cannot be proven. A leftover lockfile nobody holds (a batch run that stopped on a compile error leaves
one) is not a conflict when no Unity process has the project open before and after the lock query and
again immediately before the launch: GPOS never touches the file and Unity applies its own project-lock
semantics (it replaces the file). The GPOS single-writer lease on the project is held by the foundation for
the whole run. Package Manager
configuration is isolated per execution: empty GPOS-owned user and global configuration files in the
workspace and a GPOS runtime package cache, so the user's own configuration, tokens and registries are
never read or inherited.

Opening a Unity project changes Unity-managed project state (for example `.meta` files,
`Packages/packages-lock.json`, `ProjectSettings/*.asset`) and user-level Unity state outside the
project; the test capabilities are MUTATING and say so. Nothing is rolled back.
A build is one fixed batch invocation as well (build.build_argv): the test command's flags without the test
ones, plus `-executeMethod Gpos.LiveBridge.Build.BuildEntry.Run -gposBuildRequest <workspace>/<request file>`.
The method is a constant of this adapter and the only executeMethod GPOS ever names; nothing in a request reaches
the command. It runs only when the installed bridge package is exactly this release's.
This module never starts a process itself and never imports the subprocess module.
"""

import json
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
from . import assets
from . import authoring
from . import bridge_install as bi
from . import build as ub
from . import live
from . import prefabs
from . import project as up
from . import project_lock as pl
from . import sources
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
NEVER = ("-quit", "-accept-apiupdate", "-noUpm", "-nographics", "-executeMethod")   # never in the test command
BUILD_NEVER = ("-quit", "-accept-apiupdate", "-noUpm", "-nographics")   # the build entry exits the Editor itself

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
                "ProjectSettings/ProjectVersion.txt; Unity replaces or removes a leftover, unheld Temp/UnityLockfile "
                "itself (GPOS never touches it); user-level Unity state "
                "(Editor preferences, caches, logs, licensing client) changes; results, logs and Package Manager "
                "configuration go to the execution workspace, the package cache to the GPOS runtime area")

_project_note = "Input `unity_project`: a directory inside the GPOS project root (default `.`)."
_build_note = ("Build plane: a fresh batch-mode Editor runs the one fixed GPOS build entry of the audited bridge package "
               "(exactly this release's); macOS Standalone Player, Mono, the already active target, the active custom "
               "Build Profile for exactly that or the classic configuration; never a target switch, profile activation "
               "or settings change; produces no evidence.")
BUILD_SIDE_EFFECTS = {
    ub.INSPECT_BUILD: SIDE_EFFECTS + "; the fixed GPOS build entry only reads the build configuration",
    ub.BUILD: SIDE_EFFECTS + "; the fixed GPOS build entry builds exactly the existing configuration into this execution's "
                             "workspace (staging/, then payload/ and build-manifest.json after validation); project build "
                             "callbacks run; Unity writes Library/LastBuild.buildreport",
}
BUILD_CAPABILITIES = (
    Capability(
        id=ub.INSPECT_BUILD, category="INSPECT", operation_class="MUTATING", state_model="STATELESS",
        execution_context="EDITOR", single_writer_required=True, resource_kind="EDITOR_PROJECT", dry_run_supported=True,
        caller_output_dir_allowed=False,
        description="Read the project's existing build configuration in a fresh batch-mode Editor: active target, "
                    "profile or classic mode, development, scenes, the rules alpha.21 checks, whether it is buildable "
                    "and its configuration token. Changes no setting; MUTATING only because opening Unity changes "
                    "Library, user and tool state.",
        input_kinds=("unity_project",), artifact_kinds=("LOG",),
        timeout=TimeoutPolicy(default=600.0, maximum=1800.0), side_effect_scope=BUILD_SIDE_EFFECTS[ub.INSPECT_BUILD],
        notes=(_project_note, _build_note)),
    Capability(
        id=ub.BUILD, category="BUILD", operation_class="MUTATING", state_model="STATELESS",
        execution_context="EDITOR", single_writer_required=True, resource_kind="EDITOR_PROJECT", dry_run_supported=True,
        caller_output_dir_allowed=False,
        description="Build exactly the inspected macOS Standalone Player configuration in a fresh batch-mode Editor, "
                    "validate the .app payload, commit it inside the execution workspace and write its build manifest "
                    "last. Requires request.build_revision and the inspected configuration token; build_id is "
                    "build-<request id>.",
        input_kinds=("unity_project", "expected_configuration_token"), artifact_kinds=("REPORT", "LOG"),
        timeout=TimeoutPolicy(default=1800.0, maximum=3600.0), side_effect_scope=BUILD_SIDE_EFFECTS[ub.BUILD],
        notes=(_project_note, _build_note, "Input `expected_configuration_token`: the token an inspection returned.",
               "build_revision is caller-supplied provenance; Git is never run by this capability.")),
)
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
_asset_note = ("Assets: named only by GlobalObjectId (identifier type 1, 3 or 4); package and built-in assets are "
               "references only; new assets only at an exact new path below Assets/ in an existing folder; every edit "
               "carries the asset token of an earlier inspection and is saved to that one file only after a dirty, "
               "version-control, import, token and on-disk re-check. Edit Mode only; never queued.")
ASSET_SIDE_EFFECTS = {
    assets.CREATE_MATERIAL: "creates one .mat file (and its .meta) below Assets/ through a GPOS-owned scratch folder; "
                            "not undoable; AssetPostprocessors may run",
    assets.CREATE_SCRIPTABLE_OBJECT: "creates one .asset file (and its .meta) below Assets/ through a GPOS-owned "
                                     "scratch folder; not undoable; the type's OnEnable and AssetPostprocessors may run",
    assets.SET_MATERIAL_PROPERTY: "imports, edits (one Undo group) and saves one .mat file; AssetPostprocessors may run",
    assets.SET_ASSET_PROPERTY: "imports, edits (one Undo group) and saves one .asset file; the type's OnValidate and "
                               "AssetPostprocessors may run",
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
    _authoring(authoring.SET_RENDERER_MATERIAL, "TRANSFORM", "Set one shared-material slot of a Renderer to a Material "
                                                             "asset (or none): replace an existing slot, or give a "
                                                             "renderer without slots its first; never instantiates a "
                                                             "Material, never appends, inserts or removes slots.",
               "MUTATING", _short),
)


def _asset(cap_id, category, description, operation_class, timeout):
    side_effect = "NONE" if operation_class == "READ_ONLY" else ASSET_SIDE_EFFECTS[cap_id]
    return _live(cap_id, category, description, operation_class, "STATEFUL", "SESSION_REQUIRED", timeout,
                 input_kinds=assets.input_kinds(cap_id), notes=(_project_note, _live_note, _asset_note),
                 side_effect_scope=side_effect)


_asset_write = TimeoutPolicy(default=60.0, maximum=300.0)
ASSET_CAPABILITIES = (
    _asset(assets.ASSET_TYPES, "INSPECT", "The closed asset catalogs: the reviewed asset kinds and sources, the "
                                          "ScriptableObject catalog (editable and creatable types) or the shader "
                                          "catalog (declared properties), each with its digest.", "READ_ONLY", _short),
    _asset(assets.ASSET_FIND, "INSPECT", "Typed, bounded, paged lookup of one asset kind in Assets/, registered "
                                         "packages or the fixed built-in table, optionally by a name substring.",
           "READ_ONLY", _short),
    _asset(assets.ASSET_INSPECT, "INSPECT", "One asset by its id: kind, source, path, values (shader properties, "
                                            "ScriptableObject properties, prefab root components), whether GPOS may "
                                            "write it, and its composite token. Never imports.", "READ_ONLY", _short),
    _asset(assets.CREATE_MATERIAL, "TRANSFORM", "Create one Material of a catalogued shader at an exact new .mat path "
                                                "below Assets/; never overwrites, renames or creates folders.",
           "MUTATING", _asset_write),
    _asset(assets.SET_MATERIAL_PROPERTY, "TRANSFORM", "Write one property the Material's catalogued shader declares "
                                                      "(color, vector, float, range, int or a Texture asset) and save "
                                                      "that Material.", "MUTATING", _asset_write),
    _asset(assets.CREATE_SCRIPTABLE_OBJECT, "TRANSFORM", "Create one ScriptableObject of a creatable catalogued type "
                                                         "([CreateAssetMenu]) at an exact new .asset path below Assets/.",
           "MUTATING", _asset_write),
    _asset(assets.SET_ASSET_PROPERTY, "TRANSFORM", "Write one allowlisted serialized property of a ScriptableObject "
                                                   "asset (asset references only) and save that asset.", "MUTATING",
           _asset_write),
)

_prefab_note = ("Prefabs: prefab objects are GlobalObjectId type-1 strings (Scene objects type 2); only a regular prefab "
                "below Assets/ without nested prefabs is created, instantiated or edited; every mutation is refused while "
                "Prefab Mode is open, carries a token of an earlier inspection and is refused as a conflict when it "
                "changed; no apply, revert, unpack, connect, Variant, nested-prefab or child create/delete. Edit Mode only; "
                "never queued.")
PREFAB_SIDE_EFFECTS = {
    prefabs.CREATE_PREFAB: "creates one .prefab file (and its .meta) below Assets/ from a plain Scene subtree through a "
                           "GPOS-owned scratch folder; the source is not connected; not undoable; project code and "
                           "AssetPostprocessors may run",
    prefabs.INSTANTIATE: "imports one prefab and creates one instance of it in the open Scene as one named Undo group (the "
                         "Scene becomes dirty; nothing is saved); project Editor callbacks may run",
}
PREFAB_EDIT_SIDE_EFFECT = ("imports, edits (Unity's isolated copy) and saves one .prefab file; not undoable; its "
                           "effective content propagates to instances in loaded Scenes and dependent prefabs; Scenes "
                           "that go from clean to dirty are reported in scenes_marked_dirty (Unity itself did not dirty "
                           "them in the measured cases, project callbacks may); GPOS never saves or cleans a Scene; "
                           "project code and AssetPostprocessors may run")


def _prefab(cap_id, category, description, operation_class, timeout):
    side_effect = "NONE" if operation_class == "READ_ONLY" else PREFAB_SIDE_EFFECTS.get(cap_id, PREFAB_EDIT_SIDE_EFFECT)
    return _live(cap_id, category, description, operation_class, "STATEFUL", "SESSION_REQUIRED", timeout,
                 input_kinds=prefabs.input_kinds(cap_id), notes=(_project_note, _live_note, _prefab_note),
                 side_effect_scope=side_effect)


PREFAB_CAPABILITIES = (
    _prefab(prefabs.PREFAB_INSPECT, "INSPECT", "One prefab by its root id: type, source, hierarchy, owned and nested "
                                               "objects, components, Transforms, whether GPOS may change it (and why "
                                               "not) and its whole-prefab token; or one of its components' properties "
                                               "with the effective write authority. Never imports.", "READ_ONLY", _short),
    _prefab(prefabs.INSTANCE_INSPECT, "INSPECT", "One prefab instance in a saved Scene: its source prefab, connection "
                                                 "status, the instance-to-source object mapping and every override "
                                                 "kind Unity reports. Grants no apply or revert.", "READ_ONLY", _short),
    _prefab(prefabs.CREATE_PREFAB, "TRANSFORM", "Save one completely plain subtree of a saved Scene as a new regular "
                                                "prefab at an exact new .prefab path below Assets/; never connects, "
                                                "overwrites, renames or creates folders.", "MUTATING", _asset_write),
    _prefab(prefabs.INSTANTIATE, "TRANSFORM", "Create one instance of a clean regular prefab without nested prefabs in "
                                              "a saved, loaded Scene, at the root or under a parent.", "MUTATING",
            _asset_write),
    _prefab(prefabs.SET_GAMEOBJECT, "TRANSFORM", "Set a prefab object's name (never the root's), active state, tag, "
                                                 "layer or static flags and save the prefab.", "MUTATING", _asset_write),
    _prefab(prefabs.SET_TRANSFORM, "TRANSFORM", "Set a prefab object's local position, rotation or scale and save the "
                                                "prefab.", "MUTATING", _asset_write),
    _prefab(prefabs.ADD_COMPONENT, "TRANSFORM", "Add one component of a catalogued type (and the components it "
                                                "requires) to a prefab object and save the prefab.", "MUTATING",
            _asset_write),
    _prefab(prefabs.REMOVE_COMPONENT, "TRANSFORM", "Remove one component that nothing on its prefab object requires "
                                                   "and save the prefab.", "MUTATING", _asset_write),
    _prefab(prefabs.SET_PROPERTY, "TRANSFORM", "Write one allowlisted serialized property of a prefab component (a "
                                               "reference names an object of the same prefab or a reviewed asset) "
                                               "and save the prefab.", "MUTATING", _asset_write),
)

_source_note = ("Sources: other programs write source files; GPOS takes exact .cs, .asmdef and .asmref paths below Assets/ "
                "only — never code, content, a folder, a compile command or a global Refresh. Compilation facts come "
                "from CompilationPipeline callbacks journaled in the Editor session (kept across Domain Reloads, lost "
                "when the Editor quits); they are never evidence.")
SOURCE_SIDE_EFFECTS = {
    sources.SYNC: "imports exactly the named existing sources (Unity may create their .meta files) and, for each deleted "
                  "source, its direct parent folder recursively (one folder up only when that folder is gone too; never "
                  "Assets itself), which also imports anything else new or changed there — listed within the bounded walk of "
                  "the folder (stale database entries whose files were already gone may be reconciled unlisted); Unity then "
                  "compiles and may reload the domain as it decides; project code may run as a consequence",
}


def _source(cap_id, category, description, operation_class, timeout):
    side_effect = "NONE" if operation_class == "READ_ONLY" else SOURCE_SIDE_EFFECTS[cap_id]
    return _live(cap_id, category, description, operation_class, "STATEFUL", "SESSION_REQUIRED", timeout,
                 input_kinds=sources.input_kinds(cap_id), notes=(_project_note, _live_note, _source_note),
                 side_effect_scope=side_effect)


SOURCE_CAPABILITIES = (
    _source(sources.SYNC, "TRANSFORM", "Tell the attached Editor about exact source paths other programs changed or "
                                       "deleted: a targeted import of each existing .cs, .asmdef or .asmref path, and a "
                                       "bounded recursive import of the folder of each deleted one; no content, no "
                                       "compile command, no global Refresh.", "MUTATING",
            TimeoutPolicy(default=120.0, maximum=300.0)),
    _source(sources.STATUS, "INSPECT", "The attached Editor's compilation facts: phase, compiling, updating, "
                                       "compilation_failed, reload, compile and sync generations, the last finished "
                                       "compilations, per-assembly error and warning counts and the journal's retention "
                                       "limits. Triggers nothing.", "READ_ONLY", _short),
    _source(sources.DIAGNOSTICS, "INSPECT", "The compiler messages of the Editor-session compilation journal "
                                            "(assembly, severity, project-relative file, line, column, clipped text, "
                                            "compile generation), paged and filtered only by compile generation, "
                                            "severity or an exact journaled assembly.", "READ_ONLY", _short),
    _source(sources.WAIT, "INSPECT", "Observe the attached Editor until it has settled in Edit Mode — after a named sync, "
                                     "until a compilation that started after it has succeeded (with a Domain Reload) or "
                                     "failed — or until the timeout, then report the facts. Imports, compiles, "
                                     "refreshes, reloads, restarts and replays nothing.", "READ_ONLY",
            TimeoutPolicy(default=120.0, maximum=300.0)),
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
) + BUILD_CAPABILITIES + LIVE_CAPABILITIES + AUTHORING_CAPABILITIES + ASSET_CAPABILITIES + PREFAB_CAPABILITIES + SOURCE_CAPABILITIES

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
        "Scene authoring (bridge 1.2.0) changes the open Scene of the attached Editor only through fixed commands: no "
        "prefab asset or Prefab Mode editing, no cross-Scene reference, no array or managed-reference mutation (one "
        "reviewed Renderer material slot only), no Save As; applying serialized changes can run project Editor "
        "callbacks and no sandbox is claimed.",
        "Asset authoring (bridge 1.2.0) creates Materials and ScriptableObjects at exact new paths below Assets/ and "
        "edits reviewed properties of existing ones, saving only that asset; no prefab asset, import-setting, move, "
        "rename, delete, package or built-in asset mutation, no arbitrary AssetDatabase call, no SaveAssets and no "
        "global Refresh; version-control providers other than none are untested.",
        "Source synchronization (bridge 1.4.0) imports exact source paths and the bounded folders of deleted ones; it "
        "writes no file, takes no code or content, requests no compilation and never refreshes globally. Compilation "
        "facts are Editor-session CompilationPipeline facts, never evidence.",
        "The batch plane proves a project's Unity lock read-only on macOS (kernel process facts and an F_GETLK query); "
        "no other platform inherits that rule without its own measurement and review.",
        "Build Core (bridge 1.5.0, protocol unchanged) builds macOS Standalone Player / Mono / the active target only, "
        "from the existing configuration, through one fixed executeMethod; no Android, AAB, custom signing, IL2CPP, "
        "target switch or profile change. The .app is a workspace payload bound by its build manifest, never an artifact.",
    ))


class UnityAdapter(model.ToolAdapter):
    """`hub_roots`, `platform`, `capture_bytes`, `live_seams` and `lock_proof` are code-level seams for tests; a
    request can change none."""

    descriptor = DESCRIPTOR

    def __init__(self, hub_roots=None, platform=None, capture_bytes=CAPTURE_BYTES, live_seams=None, lock_proof=None):
        self._platform = platform or sys.platform
        self._hub_roots = tuple(hub_roots) if hub_roots is not None else HUB_ROOTS.get(self._platform, ())
        self._capture_bytes = capture_bytes
        self._live_seams = dict(live_seams or {})
        self._lock_proof = lock_proof or pl.assess

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
        if cap in assets.CAPABILITY_IDS:
            return assets.execute(request, context, **self._live_seams)
        if cap in prefabs.CAPABILITY_IDS:
            return prefabs.execute(request, context, **self._live_seams)
        if cap in sources.CAPABILITY_IDS:
            return sources.execute(request, context, **self._live_seams)
        if cap not in (INSPECT, EDITMODE, PLAYMODE) + ub.CAPABILITY_IDS:
            raise AssertionError(f"{cap} is declared but not implemented")
        root = Path(context.project_root)
        try:
            project, summary = up.preflight(root, (request.inputs or {}).get("unity_project"))
        except up.ProjectProblem as problem:
            return AdapterOutcome(ok=True, diagnostics=(dg.make("ENGINE_PROJECT_UNSUPPORTED",
                                                                f"{problem.rule}: {problem.message}", ADAPTER_ID, cap),))
        if cap == INSPECT:
            return AdapterOutcome(ok=True, data=summary)
        if cap in ub.CAPABILITY_IDS:
            return self._run_build(cap, request, context, root, project, summary)
        return self._run_tests(cap, request, context, root, project, summary)

    def _run_tests(self, cap, request, context, root, project, summary):
        platform = TEST_PLATFORMS[cap]
        required, installed = summary["editor_version"], context.probe.tool_version
        if required != installed:
            return AdapterOutcome(ok=True, diagnostics=(dg.make(
                "ENGINE_EDITOR_VERSION_UNAVAILABLE",
                f"the project requires Unity {required}; the installed Editor is {installed}; no other version, "
                f"upgrade or downgrade is used", ADAPTER_ID, cap),))
        proof = self._lock_proof(project, context.probe.tool_path)
        if proof.state not in pl.PROCEED:
            return _locked(cap, proof)
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
                data=dict(summary, test_platform=platform, project_lock=proof.state))
        cache = tp.runtime_dir(root, *UPM_CACHE)
        cache.mkdir(parents=True, exist_ok=True)
        for name in (UPM_USER_NAME, UPM_GLOBAL_NAME):   # GPOS-owned, empty: nothing is inherited from the user
            with open(workspace / name, "x", encoding="utf-8"):
                pass
        argv = test_argv(project, workspace, platform)
        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv, cwd=str(workspace),
                                    timeout=context.timeout, env=upm_environment(workspace, cache),
                                    capture_bytes=self._capture_bytes)
        proof = self._lock_proof(project, context.probe.tool_path)   # fresh, immediately before the launch
        if proof.state not in pl.PROCEED:
            return _locked(cap, proof)
        outcome = context.run(spec)
        record = dict(command=_command(spec, project, workspace), environment=spec.env.metadata())
        result = self._classify(cap, context, outcome, record, workspace, summary, platform)
        if proof.state == pl.ORPHAN_UNHELD:
            result = replace(result, diagnostics=tuple(result.diagnostics) + (dg.make(
                "ENGINE_PROJECT_ORPHAN_LOCK", "an unheld leftover Temp/UnityLockfile existed and no Unity process had "
                                              "the project open; GPOS did not modify it, and Unity was allowed to apply "
                                              "its own project-lock semantics", ADAPTER_ID, cap, details=proof.details()),))
        return replace(result, data=dict(result.data or {}, project_lock=proof.state))

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

    # ------------------------------------------------------------ the build plane (alpha.21)

    def _run_build(self, cap, request, context, root, project, summary):
        building = cap == ub.BUILD
        rid = request.request_id
        token = (request.inputs or {}).get("expected_configuration_token")
        if request.build_id is not None:
            return _refuse(cap, f"{cap} takes no build_id: a build's id is always build-<request id>, so a supplied one is "
                                f"meaningless for creation")
        if request.target_platform not in (None, "MACOS"):   # provenance only, never a build input: macOS or absent
            return _refuse(cap, f"{cap} is macOS-only; target_platform must be absent or MACOS, not "
                                f"{request.target_platform!r}")
        if building:
            if not ub.BUILD_REQUEST_ID.fullmatch(rid):
                return _refuse(cap, f"request id {rid!r} cannot name a build: build-<request id> needs lower-case letters, "
                                    f"digits and inner hyphens only, at most 64 characters")
            if not (isinstance(request.build_revision, str) and ub.REVISION.fullmatch(request.build_revision)):
                return _refuse(cap, "a build requires request.build_revision: the exact 40- or 64-hex revision "
                                    "git.resolve-provenance returned; HEAD is never inferred")
            if not (isinstance(token, str) and ub.TOKEN.fullmatch(token)):
                return _refuse(cap, "expected_configuration_token must be the 64-hex token an inspection returned")
        required, installed = summary["editor_version"], context.probe.tool_version
        if required != installed:   # the exact version, as for a test run
            return AdapterOutcome(ok=True, diagnostics=(dg.make(
                "ENGINE_EDITOR_VERSION_UNAVAILABLE",
                f"the project requires Unity {required}; the installed Editor is {installed}; no other version, "
                f"upgrade or downgrade is used", ADAPTER_ID, cap),))
        entry, _ = bi.inspect_target(project, bi.load_manifest())
        if entry != bi.EXACT:
            return AdapterOutcome(ok=True, data={"build_entry_package": entry}, diagnostics=(dg.make(
                "BUILD_ENTRY_UNAVAILABLE", f"Packages/{bi.PACKAGE_ID} is {entry}, not exactly {bi.PACKAGE_ID} "
                                           f"{bi.BRIDGE_VERSION}, so the fixed build entry is not available; install or "
                                           f"upgrade it with {live.INSTALL} on the closed project", ADAPTER_ID, cap),))
        workspace = Path(context.workspace)   # the build root
        proof = self._lock_proof(project, context.probe.tool_path)
        if proof.state not in pl.PROCEED:   # before the workspace is used
            return _locked(cap, proof)
        if context.dry_run:
            what = f"build {summary['unity_project']!r} as {ub.build_id(rid)}" if building else \
                f"inspect the build configuration of {summary['unity_project']!r}"
            return AdapterOutcome(
                plan=(f"would {what} with Unity {installed} in a fresh batch-mode Editor running the fixed GPOS build "
                      f"entry {ub.BUILD_ENTRY_METHOD}",
                      "the configuration, its buildability, the build result and the payload are only established by a "
                      "real execution",
                      "no Unity process, workspace, package cache, build output or manifest is created by a dry run"),
                data=dict(summary, project_lock=proof.state, **({"build_id": ub.build_id(rid)} if building else {})))
        present = ub.unfresh(workspace)
        if present:
            return AdapterOutcome(ok=True, diagnostics=(dg.make(
                "BUILD_WORKSPACE_NOT_FRESH", f"the execution workspace of request {rid} already holds {present[:6]}; an "
                                             f"earlier build is never adopted, reused or overwritten", ADAPTER_ID, cap),))
        cache = tp.runtime_dir(root, *UPM_CACHE)
        cache.mkdir(parents=True, exist_ok=True)
        for name in (UPM_USER_NAME, UPM_GLOBAL_NAME):   # GPOS-owned, empty: nothing is inherited from the user
            with open(workspace / name, mode="x", encoding="utf-8"):
                pass
        ub.write_request(workspace, "BUILD" if building else "INSPECT", rid, token if building else None)
        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=ub.build_argv(project, workspace),
                                    cwd=str(workspace), timeout=context.timeout, env=upm_environment(workspace, cache),
                                    capture_bytes=self._capture_bytes)
        proof = self._lock_proof(project, context.probe.tool_path)   # again, immediately before the launch
        if proof.state not in pl.PROCEED:   # a writer appeared meanwhile
            return _locked(cap, proof)
        outcome = context.run(spec)
        record = dict(command=_command(spec, project, workspace), environment=spec.env.metadata())
        result = self._classify_build(cap, request, context, outcome, record, workspace, summary)
        return _with_lock_state(cap, proof, result)

    def _classify_build(self, cap, request, context, outcome, record, workspace, summary):
        """What the fixed build entry's one response (never the Editor log, once the entry ran) establishes."""
        building, rid = cap == ub.BUILD, request.request_id
        log_path = workspace / ub.LOG_NAME
        log = tuple(ArtifactSpec("editor-log", "LOG", str(log_path), media_type="text/plain",
                                 description="Unity Editor log of this run (machine and session identifiers, local paths)")
                    for _ in (0,) if log_path.is_file())
        data = dict(unity_project=summary["unity_project"], editor_version=summary["editor_version"],
                    exit_code=outcome.exit_code, **({"build_id": ub.build_id(rid)} if building else {}))

        def done(*diagnostics, ok=True, artifacts=log, detail=""):
            return AdapterOutcome(ok=ok, exit_code=outcome.exit_code, data=data, diagnostics=diagnostics, detail=detail,
                                  artifacts=artifacts, process=outcome, mutation_performed=True, **record)

        def diag(code, message, **details):
            return dg.make(code, message, ADAPTER_ID, cap, details=details or None)

        def unknown(why):
            return done(diag("BUILD_OUTCOME_UNKNOWN", f"{why}; nothing was published and nothing is retried"),
                        *_quarantine(cap, workspace))

        began = building and ub.started(workspace)
        response, problem = None, "the Editor did not exit normally"
        if not outcome.timed_out and outcome.exit_code == 0:
            try:
                response = ub.read_response(workspace, rid, "BUILD" if building else "INSPECT")
            except (ub.ResponseProblem, OSError) as exc:
                problem = str(exc)
        if response is None:
            if began:
                return unknown(f"the build started but no trustworthy final response exists ({problem})")
            if outcome.timed_out:
                return done(ok=False, detail=f"{cap} timed out before any build started")
            cause = ur.classify_log(ur.log_tail(log_path), outcome.stdout)   # pre-entry failures only
            if cause == ur.COMPILE_ERROR:
                return done(diag("BUILD_COMPILE_FAILED", "script compilation failed while the batch Editor opened the "
                                                         "project, before the fixed build entry could run"))
            if cause == ur.PROJECT_LOCKED:
                return done(diag("ENGINE_PROJECT_LOCKED", "Unity refused the project because another instance has it open"))
            if cause == ur.LICENSE_UNAVAILABLE:
                return done(diag("ENGINE_LICENSE_UNAVAILABLE", "Unity reported that no valid Editor licence was "
                                                               "available; no licence action is ever taken"))
            return done(diag("BUILD_ENTRY_FAILED", f"the fixed build entry gave no trustworthy answer (exit "
                                                   f"{outcome.exit_code}; {problem}) and never started a build"))
        conf = response["configuration"]
        profile = conf["profile"]
        bases = ub.path_bases(Path(context.project_root) / summary["unity_project"], context.project_root)
        clean = lambda text, bound: ub.clean_message(text, bases, bound)   # every text the entry or Unity wrote
        problems = [{"rule": p["rule"], "message": clean(p["message"], ub.MAX_PROBLEM_CHARS)} for p in response["problems"]]
        data.update(buildable=response["buildable"], problems=problems,
                    configuration_token=response["configuration_token"], configuration=conf,
                    unity_version=response["unity_version"], active_target=conf["active_target"],
                    mode=conf["mode"], development=conf["development"])
        if not building:
            if response["buildable"]:
                return done()
            rules = [p["rule"] for p in response["problems"]]
            return done(diag("BUILD_CONFIGURATION_NOT_BUILDABLE", f"the configuration is not buildable by this release: "
                                                                  f"{', '.join(rules)}", rules=rules))
        if response["outcome"] == "REFUSED":
            if began:
                return unknown("the build entry both refused and recorded a started build")
            rule, message = response["refusal"]["rule"], clean(response["refusal"]["message"], ub.MAX_PROBLEM_CHARS)
            return done(diag(ub.RULE_CODES.get(rule, "BUILD_CONFIGURATION_UNSUPPORTED"), f"{rule}: {message}", rule=rule))
        b, post = response["build"], response["post"]
        if not began or b is None:
            return unknown("the build entry reported a build without a started marker or without a BuildReport")
        # outputPath is used below only to verify the exact staging path; it never leaves GPOS
        data["build"] = {k: b[k] for k in ("result", "guid", "total_errors", "total_warnings", "total_size",
                                           "development_observed", "error_message_count")}
        data["build"]["messages"] = [{"step": clean(m["step"], ub.MAX_STEP_CHARS), "type": m["type"],
                                      "text": clean(m["text"], ub.MAX_MESSAGE_CHARS)} for m in b["messages"]]
        data["build"]["duration_seconds"] = round(b["duration_ms"] / 1000.0, 3)
        if b["result"] != "Succeeded" or b["total_errors"] != 0:
            return done(diag("BUILD_FAILED", f"Unity reported result {b['result']} with {b['total_errors']} error(s)",
                             result=b["result"]), *_quarantine(cap, workspace))
        staging_app = workspace / ub.STAGING / ub.APP
        mismatched = [name for name, holds in (
            ("the inspected configuration token", response["configuration_token"] == request.inputs[
                "expected_configuration_token"]),
            ("the post-build configuration token", post["configuration_token"] == response["configuration_token"]),
            ("the active target", post["active_target"] == conf["active_target"] == b["platform"] == ub.TARGET),
            ("the active Build Profile", post["profile_path"] == (profile["path"] if profile else None)),
            ("the development state", post["development"] is conf["development"] is b["development_observed"]),
            ("the Unity build GUID", bool(ub.GUID.fullmatch(b["guid"])) and b["guid"] != "0" * 32),
            ("the output path", os.path.realpath(b["output_path"]) == os.path.realpath(staging_app)),
        ) if not holds]
        if mismatched:
            return unknown(f"the post-build checks failed ({'; '.join(mismatched)})")
        try:
            names = sorted(os.listdir(workspace / ub.STAGING))
            if names != [ub.APP]:
                raise ub.PayloadProblem(f"staging/ holds {names[:6]}, not exactly {ub.APP}")
            app = ub.validate_app(staging_app, conf["application_identifier"], b["guid"])
            tree = ub.payload_tree(staging_app)
        except (ub.PayloadProblem, OSError) as exc:
            return done(diag("BUILD_PAYLOAD_INVALID", f"the payload is not published: {exc}"), *_quarantine(cap, workspace))
        try:
            ub.publish(workspace)
        except OSError as exc:
            return unknown(f"the payload could not be committed ({type(exc).__name__})")
        started_at = _started_utc(workspace)
        manifest = build_manifest(request, response, app, tree, started_at, context.clock.now())
        try:
            ub.write_manifest(workspace, manifest)
        except OSError as exc:
            return unknown(f"the payload was committed but its manifest could not be written ({type(exc).__name__}), so "
                           f"this is not a completed build")
        data.update(payload={k: manifest["payload"][k] for k in manifest["payload"]}, manifest=ub.MANIFEST_NAME,
                    build_revision=request.build_revision, build_revision_source="CALLER_SUPPLIED")
        artifacts = (ArtifactSpec("build-manifest", "REPORT", str(workspace / ub.MANIFEST_NAME),
                                  media_type="application/json",
                                  description="GPOS build manifest: binds the workspace payload by its tree digest"),) + log
        return done(diag("BUILD_PUBLISHED", f"{manifest['build_id']}: {ub.PAYLOAD}/{ub.APP} ({tree['entries']} entries, "
                                            f"{tree['bytes']} bytes) committed; {ub.MANIFEST_NAME} written last"),
                    artifacts=artifacts)


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


LOCK_MESSAGES = {
    pl.ACTIVE_EDITOR: "a Unity Editor has this project open (a Unity process of this user names it, or the OS reports "
                      "Temp/UnityLockfile held); no second Editor is launched",
    pl.LOCK_STATE_UNKNOWN: "whether a Unity Editor has this project open cannot be proven; nothing is launched",
}


def _locked(cap, proof):
    """ENGINE_PROJECT_LOCKED from a read-only proof; the lockfile is never removed, modified or bypassed."""
    why = "; ".join(r for r in proof.reasons if r)
    return AdapterOutcome(ok=True, data={"project_lock": proof.state}, diagnostics=(dg.make(
        "ENGINE_PROJECT_LOCKED", LOCK_MESSAGES[proof.state] + (f" ({why})" if why else ""), ADAPTER_ID, cap,
        details=proof.details()),))


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


def _with_lock_state(cap, proof, result):
    """A build result with the lock state it ran under, and the orphan notice when Unity replaced a leftover lockfile."""
    if proof.state == pl.ORPHAN_UNHELD:
        result = replace(result, diagnostics=tuple(result.diagnostics) + (dg.make(
            "ENGINE_PROJECT_ORPHAN_LOCK", "an unheld leftover Temp/UnityLockfile existed and no Unity process had the "
                                          "project open; GPOS did not modify it, and Unity was allowed to apply its own "
                                          "project-lock semantics", ADAPTER_ID, cap, details=proof.details()),))
    return replace(result, data=dict(result.data or {}, project_lock=proof.state))


def _quarantine(cap, workspace):
    """INFO naming the build-owned partial state left in the workspace (never deleted, never published)."""
    left = [n for n in (ub.STAGING, ub.PAYLOAD, ub.STARTED_NAME) if os.path.lexists(Path(workspace) / n)]
    if not left:
        return ()
    return (dg.make("BUILD_QUARANTINED", f"left in the execution workspace, not a completed build: {', '.join(left)}",
                    ADAPTER_ID, cap, details={"left": left}),)


def _started_utc(workspace):
    try:
        with open(Path(workspace) / ub.STARTED_NAME, "rb") as fh:
            started = json.loads(fh.read(ub.MAX_STARTED_BYTES).decode("utf-8"))
        value = started["utc"] if isinstance(started, dict) and "utc" in started else None
        return value if isinstance(value, str) and len(value) <= 40 else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def build_manifest(request, response, app, tree, started_at, built_at):
    """The gpos.unity.build-manifest/1 document: what was built, from which configuration, bound to which payload."""
    import gpos
    conf, b = response["configuration"], response["build"]
    profile = conf["profile"]
    return {
        "schema": ub.MANIFEST_SCHEMA,
        "build_id": ub.build_id(request.request_id),
        "request_id": request.request_id,
        "capability": ub.BUILD,
        "adapter": {"id": ADAPTER_ID, "version": ADAPTER_VERSION},
        "gpos_version": gpos.__version__,
        "subject": {"kind": request.subject.kind, "ref": request.subject.ref},
        "build_revision": request.build_revision,
        "build_revision_source": "CALLER_SUPPLIED",
        "configuration_token": response["configuration_token"],
        "configuration": conf,
        "unity_version": response["unity_version"],
        "target": ub.TARGET,
        "development": conf["development"],
        "configuration_mode": conf["mode"],
        "build_profile": None if not profile else {k: profile[k] for k in ("path", "guid", "sha256")},
        "scenes": conf["scenes"],
        "unity_build": {"guid": b["guid"], "result": b["result"], "total_errors": b["total_errors"],
                        "total_warnings": b["total_warnings"], "total_size": b["total_size"],
                        "development_observed": b["development_observed"],
                        "duration_seconds": round(b["duration_ms"] / 1000.0, 3)},
        "build_entry": {"package": bi.PACKAGE_ID, "version": bi.BRIDGE_VERSION, "method": ub.BUILD_ENTRY_METHOD,
                        "package_digest": bi.load_manifest()["package_digest"]},
        "payload": {"path": f"{ub.PAYLOAD}/{ub.APP}", "kind": "MACOS_APP_BUNDLE", "tree_algorithm": tree["algorithm"],
                    "tree_digest": tree["digest"], "entries": tree["entries"], "bytes": tree["bytes"],
                    "bundle_identifier": app["bundle_identifier"], "executable": app["executable"],
                    "bundle_version": app["bundle_version"]},
        "started_at": started_at,
        "built_at": built_at,
        "duration_seconds": round(b["duration_ms"] / 1000.0, 3),
        "limitations": list(ub.LIMITATIONS),
    }
