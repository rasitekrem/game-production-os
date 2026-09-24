"""Production DCC adapter: Blender (Phase 2C-4).

Two capabilities, each one fresh background Blender process running the fixed GPOS helper
(`helper.py`) and exiting:

    blender.inspect-blend  bounded metadata of one .blend                  READ_ONLY, no artifact, no evidence
    blender.render-scene   one still of the authored scene/camera/frame    render.png -> VISUAL_EVIDENCE (DCC_RENDER)

This is not a Blender automation or Python interface. The caller never supplies Python, a script, an
expression, an operator, a Blender option, a render engine, a camera or an output path. The only
variable command-line slots are the validated .blend input, the adapter's workspace output, an exact
scene name and a canonical frame integer, all passed after `--` to the fixed helper.

Every Blender process starts from the same safety baseline:

    Blender --background --factory-startup --disable-autoexec --offline-mode -noaudio
            --log-file <isolated root>/blender.log --python-exit-code 71 --python <helper> -- <mode> <nonce> ...

with `BLENDER_USER_RESOURCES` pointed at a fresh, execution-specific directory, so no user preference,
startup file, add-on or script participates (Blender 5.2 on macOS ignores HOME for this; the official
variable does not). The helper opens the .blend explicitly with scripts disabled, and refuses to render
anything that could run embedded code (Freestyle, OSL script nodes), depends on source Python that Blender
blocked, reads undeclared files (external dependencies) or writes a second file (compositor File Output,
multi-view, sequencer strips). It never saves. The render is DCC_RENDER evidence about an ASSET, never runtime,
motion or presentation proof.

The executable is found by exact name over a fixed per-platform candidate list: `shutil.which` must
return a path whose basename is a real directory entry with exactly that spelling, so a lookup that only
succeeds on a case-insensitive filesystem is rejected rather than repaired. No caller chooses it.
This module never starts a process itself and never imports the subprocess module.
"""

import os
import re
import secrets
import shutil
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
from . import parser

ADAPTER_ID = "blender"
ADAPTER_VERSION = "1.0.0"
INSPECT = f"{ADAPTER_ID}.inspect-blend"
RENDER = f"{ADAPTER_ID}.render-scene"

HELPER = str(Path(__file__).resolve().parent / "helper.py")
HELPER_EXIT_CODE = "71"
EXECUTABLE_CANDIDATES = {  # fixed per host platform; exact directory-entry names only
    "darwin": ("blender", "Blender"),
    "linux": ("blender",),
    "win32": ("blender.exe", "Blender.exe", "blender", "Blender"),
}
BASELINE = ("--background", "--factory-startup", "--disable-autoexec", "--offline-mode", "-noaudio")
LOG_NAME = "blender.log"      # Blender's own log, inside the isolated user root; read by the helper, then deleted

INPUT_ID = "blend"
OUTPUT_NAME = "render.png"
MAX_OUTPUT_BYTES = 128 * 1024 ** 2
CAPTURE_BYTES = 8 * 1024 ** 2          # Blender logs; the machine record is bounded separately
PROBE_TIMEOUT = 60.0
MAX_SCENE_NAME = 256
MAX_FRAME = 1_048_574                   # Blender 5.2: frame_start/frame_end are 0..1048574
FRAME_TEXT = re.compile(r"0|[1-9][0-9]{0,6}")
_VERSION_LINE = re.compile(r"Blender ([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4}(?: [A-Za-z]{2,12})?)")

# Placeholders for the recorded command: the real argv is unchanged.
PLACEHOLDERS = {"helper": "<gpos-blender-helper>", "blend": "<blend-input>", "output": "<workspace>/render.png",
                "nonce": "<nonce>", "log": "<isolated-user-root>/blender.log"}

LIMITATIONS = (
    "A Blender DCC render of the named ASSET: it supports inspecting the asset, not the game.",
    "It does not prove engine or runtime presentation, target-platform appearance or performance.",
    "It does not prove motion, interaction or game feel: one still frame only.",
    "Rendered through the scene's authored camera, render engine and settings; only a self-contained .blend "
    "(no external file dependency) is accepted as its source.",
)

CAPABILITIES = (
    Capability(
        id=INSPECT, category="INSPECT", operation_class="READ_ONLY", state_model="STATELESS",
        execution_context="OFFLINE_ANALYSIS", requires_tool=True, requires_project=True,
        description="Bounded metadata of one .blend: scenes (camera, frame range, engine, resolution), object and "
                    "geometry counts, datablock counts, external-dependency counts and render-safety observations. "
                    "Never renders, saves or returns a path.",
        timeout=TimeoutPolicy(default=120.0, maximum=600.0),
        notes=("Consumes exactly one input artifact, id `blend`, a .blend file with no capture context.",)),
    Capability(
        id=RENDER, category="CAPTURE", operation_class="MUTATING", state_model="STATELESS",
        execution_context="DCC_RENDER", requires_tool=True, requires_project=True, dry_run_supported=True,
        description="Render one still of a self-contained .blend through its authored scene, camera, frame and "
                    "built-in render engine to a bounded PNG; offers VISUAL_EVIDENCE captured in DCC_RENDER for an "
                    "ASSET subject.",
        input_kinds=("scene_name", "frame"), artifact_kinds=("IMAGE",),
        potential_evidence=(("VISUAL_EVIDENCE", "DCC_RENDER"),),
        timeout=TimeoutPolicy(default=600.0, maximum=3600.0),
        side_effect_scope="writes render.png into this execution's host workspace; the .blend is never saved",
        notes=("Consumes exactly one input artifact, id `blend`, a .blend file with no capture context.",
               "The subject must be an ASSET: a DCC render is asset inspection evidence only.")),
)

DESCRIPTOR = model.AdapterDescriptor(
    adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION, tool_family="DCC", target_tool="Blender",
    adapter_kind="CLI", state_model="STATELESS", supported_platforms=("WINDOWS", "MACOS", "LINUX"),
    capabilities=CAPABILITIES,
    availability="a Blender executable on PATH under its platform name (blender; Blender on macOS app bundles), "
                 "matching a real directory entry exactly; absolute PATH entries only",
    compatibility_notes=(
        "Fixed audited helper only: no caller Python, script, expression, operator, option, engine, camera or "
        "output path.",
        "Factory startup, auto-execution disabled, scripts disabled at file open, offline mode, and an isolated "
        "per-execution BLENDER_USER_RESOURCES: no user preference, add-on or startup script participates.",
        "Renders refuse Freestyle and OSL script nodes (embedded code), sources whose Python Blender blocked, "
        "external dependencies and extra outputs; the .blend is never saved.",
    ))


class BlenderAdapter(model.ToolAdapter):
    """`which`, `platform` and `capture_bytes` are code-level seams for tests; a request can change none."""

    descriptor = DESCRIPTOR

    def __init__(self, which=shutil.which, platform=None, capture_bytes=CAPTURE_BYTES):
        self._which = which
        self._platform = platform or sys.platform
        self._capture_bytes = capture_bytes

    # ------------------------------------------------------------ discovery and probe

    def find_executable(self):
        """(absolute path, None) or (None, reason). Exact-name lookup over the fixed candidate list."""
        candidates = EXECUTABLE_CANDIDATES.get(self._platform, ("blender",))
        accepted = []
        for name in candidates:
            found = self._which(name)
            if not found:
                continue
            if not os.path.isabs(found):
                return None, (f"{name} was only found through a relative PATH entry, which would make the executed "
                              f"program depend on the working directory; it is not used")
            parent, base = os.path.split(found)
            try:
                entries = os.listdir(parent)
            except OSError:
                continue
            if base not in entries:          # exact, case-sensitive: never accepted by case-insensitive luck
                continue
            if os.path.isfile(found) and os.access(found, os.X_OK):
                accepted.append(found)
        distinct = {os.path.realpath(path) for path in accepted}
        if not accepted:
            return None, "no Blender executable was found on PATH under its exact platform name"
        if len(distinct) > 1:
            return None, "more than one distinct Blender executable is on PATH; none is chosen"
        return accepted[0], None

    def probe(self):
        platform = model.current_platform()
        unusable = lambda reason: tuple((c.id, False, reason) for c in CAPABILITIES)
        executable, problem = self.find_executable()
        if problem:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform, detail=problem,
                                     capability_availability=unusable("Blender is not available"))
        neutral = str(Path(tempfile.gettempdir()).resolve())
        user_root = tempfile.mkdtemp(prefix="gpos-blender-probe-", dir=neutral)
        try:
            env = user_environment(user_root)
            try:
                version = proc.run_process(proc.ToolProcessSpec(executable=executable, argv=("--version",), cwd=neutral,
                                                                timeout=PROBE_TIMEOUT, env=env), [neutral])
            except proc.ProcessSpecError as exc:
                return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                         detail=f"Blender could not be started: {exc}",
                                         capability_availability=unusable("Blender could not be started"))
            if version.timed_out or version.exit_code != 0 or version.truncated:
                return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                         detail=f"`Blender --version` did not complete normally (exit {version.exit_code})",
                                         capability_availability=unusable("Blender did not run normally"))
            tool_version = parse_version(version.raw_stdout)
            if tool_version is None:
                return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable, platform=platform,
                                         detail="unrecognized `Blender --version` output; the version could not be "
                                                "established",
                                         capability_availability=unusable("Blender version unknown"))
            nonce = secrets.token_hex(16)
            test = proc.run_process(proc.ToolProcessSpec(executable=executable, argv=selftest_argv(user_root, nonce),
                                                         cwd=neutral,
                                                         timeout=PROBE_TIMEOUT, env=env,
                                                         capture_bytes=self._capture_bytes), [neutral])
            problem = None
            if test.timed_out or test.truncated or test.exit_code != 0:
                problem = f"the GPOS helper self-test did not complete (exit {test.exit_code})"
            else:
                try:
                    result = parser.selftest(parser.record(test.raw_stdout, nonce))
                    if not tool_version.startswith(result["blender_version"]):
                        problem = "the helper saw a different Blender version than --version reported"
                except parser.HelperProtocolError as exc:
                    problem = f"the GPOS helper self-test failed: {exc}"
            if problem:
                return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable,
                                         tool_version=tool_version, platform=platform, detail=problem,
                                         capability_availability=unusable("helper compatibility not established"))
            return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=executable, tool_version=tool_version,
                                     platform=platform,
                                     detail=f"Blender {tool_version} at {executable}; helper self-test passed "
                                            f"(engines {', '.join(result['engines'])})",
                                     capability_availability=tuple((c.id, True, "") for c in CAPABILITIES))
        finally:
            shutil.rmtree(user_root, ignore_errors=True)

    # ------------------------------------------------------------ execution

    def execute(self, request, context):
        cap = request.capability_id
        if cap not in (INSPECT, RENDER):
            raise AssertionError(f"{cap} is declared but not implemented")
        problem = input_problem(context.input_artifacts)
        if problem:
            return _refuse(cap, problem)
        source = context.input_artifacts[0]
        scene_name = frame = None
        output = None
        if cap == RENDER:
            if request.subject.kind != "ASSET":
                return _refuse(cap, f"{RENDER} renders DCC evidence about an ASSET only; a {request.subject.kind} "
                                    f"subject needs engine or runtime evidence, which a Blender render is not")
            inputs = request.inputs or {}
            scene_name, problem = scene_input(inputs.get("scene_name"))
            if problem:
                return _refuse(cap, problem)
            frame, problem = frame_input(inputs.get("frame"))
            if problem:
                return _refuse(cap, problem)
            output = Path(context.workspace) / OUTPUT_NAME
            if output.exists() or output.is_symlink():
                return _refuse(cap, f"the workspace already holds {OUTPUT_NAME}; an existing file is never reported "
                                    f"as a new render")
            if context.dry_run:
                return AdapterOutcome(
                    plan=(f"would render {'scene ' + repr(scene_name) if scene_name else 'the active scene'} at "
                          f"{'frame ' + frame if frame else 'its current frame'} of input artifact {source.artifact_id!r} "
                          f"to {OUTPUT_NAME} and offer VISUAL_EVIDENCE (DCC_RENDER) for this ASSET",
                          "whether the scene exists, has a camera, uses a supported engine, has the frame in range, a "
                          "bounded resolution, no external dependency, no Freestyle or script nodes, no extra outputs, "
                          "a complete load report and no Python that Blender blocked is only checked by a real execution",
                          "no Blender project process, workspace, image or evidence is created by a dry run"),
                    data={"source_artifact_id": source.artifact_id,
                          **({"scene_name": scene_name} if scene_name else {}), **({"frame": int(frame)} if frame else {})})
        runtime_base = tp.runtime_dir(context.project_root, "blender-user")
        runtime_base.mkdir(parents=True, exist_ok=True)
        user_root = tempfile.mkdtemp(prefix=f"{request.request_id}-", dir=str(runtime_base))
        nonce = secrets.token_hex(16)
        try:
            argv = (inspect_argv(user_root, source.absolute_path, nonce) if cap == INSPECT else
                    render_argv(user_root, source.absolute_path, str(output), scene_name or "", frame or "", nonce))
            spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv,
                                        cwd=str(context.project_root if cap == INSPECT else context.workspace),
                                        timeout=context.timeout, env=user_environment(user_root),
                                        capture_bytes=self._capture_bytes)
            outcome = context.run(spec)
        finally:
            shutil.rmtree(user_root, ignore_errors=True)
            try:
                runtime_base.rmdir()     # only when empty: the isolated user root is disposable runtime state
            except OSError:
                pass
        record = dict(command=_command(spec, source, output, nonce, user_root), environment=spec.env.metadata())
        public = _without_output(outcome)
        if cap == INSPECT:
            return self._inspected(cap, outcome, public, record, nonce, source)
        return self._rendered(cap, context, outcome, public, record, nonce, source, output)

    def _inspected(self, cap, outcome, public, record, nonce, source):
        failure = _process_failure(outcome, public, record, "inspection")
        if failure:
            return failure
        try:
            value = parser.record(outcome.raw_stdout, nonce)
            if value.get("status") == "refused":
                code, message = parser.refusal(value)
                return AdapterOutcome(ok=True, exit_code=outcome.exit_code, process=public,
                                      diagnostics=(dg.make("DCC_SOURCE_NOT_ACCEPTED", f"{code}: {message}", ADAPTER_ID,
                                                           cap),), **record)
            summary = parser.inspection(value)
        except parser.HelperProtocolError as exc:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public,
                                  detail=f"the helper result could not be accepted ({exc})", **record)
        return AdapterOutcome(ok=True, exit_code=0, process=public,
                              data={"source_artifact_id": source.artifact_id, **summary}, **record)

    def _rendered(self, cap, context, outcome, public, record, nonce, source, output):
        produced = (ArtifactSpec("render", "IMAGE", str(output), media_type="image/png",
                                 description="Blender DCC render of the ASSET"),) if output.exists() else ()
        record["mutation_performed"] = bool(produced)
        failure = _process_failure(outcome, public, record, "render", produced)
        if failure:
            return failure
        try:
            value = parser.record(outcome.raw_stdout, nonce)
            if value.get("status") == "refused":
                code, message = parser.refusal(value)
                if produced:
                    return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public, artifacts=produced,
                                          detail="the helper refused the render but an output file exists", **record)
                return AdapterOutcome(ok=True, exit_code=outcome.exit_code, process=public,
                                      diagnostics=(dg.make("DCC_SOURCE_NOT_ACCEPTED", f"{code}: {message}", ADAPTER_ID,
                                                           cap),), **record)
            rendered = parser.rendering(value)
        except parser.HelperProtocolError as exc:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public, artifacts=produced,
                                  detail=f"the helper result could not be accepted ({exc})", **record)
        defect = output_defect(output, rendered["width"], rendered["height"])
        if defect:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public, artifacts=produced,
                                  detail=defect, **record)
        subject = context.request.subject.ref
        candidate = EvidenceCandidate(
            evidence_type="VISUAL_EVIDENCE", capture_context="DCC_RENDER",
            summary=f"Blender DCC render of ASSET {subject}: scene {rendered['scene']!r}, frame {rendered['frame']}, "
                    f"authored camera {rendered['camera']!r}, {rendered['engine']}, "
                    f"{rendered['width']}x{rendered['height']}",
            subject_kind="ASSET", subject_ref="", source_adapter=ADAPTER_ID, source_capability=cap,
            generated_at=context.clock.now(), artifact_ids=("render",), limitations=LIMITATIONS)
        return AdapterOutcome(ok=True, exit_code=0, process=public, artifacts=produced, evidence=(candidate,),
                              data={"source_artifact_id": source.artifact_id, **rendered,
                                    "bytes": output.stat().st_size}, **record)


# ---------------------------------------------------------------- the fixed command surface

def user_environment(user_root):
    """The adapter-owned environment: the foundation's allowlist plus an isolated Blender user root."""
    return proc.EnvironmentPolicy(overrides=(("BLENDER_USER_RESOURCES", str(user_root)),))


def _start(user_root):
    return BASELINE + ("--log-file", str(Path(user_root) / LOG_NAME), "--python-exit-code", HELPER_EXIT_CODE,
                       "--python", HELPER, "--")


def selftest_argv(user_root, nonce):
    return _start(user_root) + ("selftest", nonce)


def inspect_argv(user_root, blend, nonce):
    return _start(user_root) + ("inspect", nonce, str(blend))


def render_argv(user_root, blend, output, scene_name, frame, nonce):
    return _start(user_root) + ("render", nonce, str(blend), str(output), scene_name, frame)


def parse_version(raw):
    """The version from `Blender --version`'s first line (`Blender 5.2.0 LTS`), or None. Never guessed."""
    lines = bytes(raw).decode("utf-8", errors="replace").splitlines() if isinstance(raw, (bytes, bytearray)) else []
    match = _VERSION_LINE.fullmatch(lines[0].strip()) if lines else None
    return match.group(1) if match else None


# ---------------------------------------------------------------- request validation

def input_problem(inputs):
    if len(inputs) != 1:
        return f"exactly one input artifact, id {INPUT_ID!r}, is required; {len(inputs)} were supplied"
    source = inputs[0]
    if source.artifact_id != INPUT_ID:
        return f"the input artifact must have id {INPUT_ID!r}, not {source.artifact_id[:64]!r}"
    if not str(source.absolute_path).endswith(".blend"):
        return "the input artifact must be a .blend file"
    if source.origin_capture_context is not None:
        return ("a .blend is production source, not captured evidence: its input artifact must have no capture "
                "context, so no earlier capture's authority can pass to a DCC render")
    return None


def scene_input(value):
    if value is None:
        return None, None
    if not isinstance(value, str) or not value or len(value) > MAX_SCENE_NAME or \
            any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None, (f"scene_name must be a non-empty exact scene name of at most {MAX_SCENE_NAME} characters with "
                      f"no control characters")
    return value, None


def frame_input(value):
    """(canonical decimal text, None) or (None, reason). An int, or canonical non-negative integer text."""
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None, "frame must be an integer"
    text = str(value) if isinstance(value, int) else value
    if not FRAME_TEXT.fullmatch(text) or int(text) > MAX_FRAME:
        return None, f"frame must be a canonical non-negative integer no larger than {MAX_FRAME}"
    return text, None


# ---------------------------------------------------------------- results

def output_defect(output, width, height):
    """Why a finished render cannot be trusted, or None."""
    if not output.is_file() or output.is_symlink():
        return f"Blender reported a render but {OUTPUT_NAME} was not written"
    size = output.stat().st_size
    if size == 0:
        return f"{OUTPUT_NAME} is empty"
    if size > MAX_OUTPUT_BYTES:
        return f"{OUTPUT_NAME} is larger than {MAX_OUTPUT_BYTES} bytes"
    try:
        actual = parser.png_dimensions(output.read_bytes())
    except parser.HelperProtocolError as exc:
        return f"{OUTPUT_NAME} is not a complete PNG ({exc})"
    if actual != (width, height):
        return f"{OUTPUT_NAME} is {actual[0]}x{actual[1]}, not the scene's {width}x{height}"
    return None


def _process_failure(outcome, public, record, what, produced=()):
    if outcome.timed_out:
        return AdapterOutcome(ok=False, process=public, artifacts=produced, detail=f"the Blender {what} timed out",
                              **record)
    if outcome.truncated:
        return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public, artifacts=produced,
                              detail=f"Blender's output reached the capture bound; the {what} is not trusted", **record)
    if outcome.exit_code != 0:
        return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public, artifacts=produced,
                              detail=f"the Blender {what} exited {outcome.exit_code}", **record)
    return None


def _refuse(capability_id, message):
    return AdapterOutcome(ok=True, diagnostics=(dg.make("INVALID_TOOL_REQUEST", message, ADAPTER_ID, capability_id),))


def _without_output(outcome):
    """The outcome without captured payload: Blender's logs name local paths and are never passed on."""
    return replace(outcome, stdout="", stderr="", raw_stdout=b"", raw_stderr=b"")


def _command(spec, source, output, nonce, user_root):
    """The recorded command with the helper, source, output, log and nonce replaced by placeholders."""
    swap = {HELPER: PLACEHOLDERS["helper"], str(source.absolute_path): PLACEHOLDERS["blend"], nonce: PLACEHOLDERS["nonce"],
            str(Path(user_root) / LOG_NAME): PLACEHOLDERS["log"]}
    if output is not None:
        swap[str(output)] = PLACEHOLDERS["output"]
    return replace(spec, argv=tuple(swap.get(a, a) for a in spec.argv)).command_for_provenance()
