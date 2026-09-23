"""Production media-inspection adapter: ffprobe (Phase 2C-2).

One capability, `ffprobe.inspect`: a bounded, tag-free summary of one local media file — container,
duration, stream counts, and the first video and audio stream's basic parameters. It is READ_ONLY,
produces no artifact and offers no evidence: inspecting a file is not evidence of anything.

This adapter drives the ffprobe executable only. The FFmpeg executable has its own adapter, because the
frozen foundation records one tool path and version per execution; one adapter per executable keeps that
provenance exact.

The only variable in the command is the path of the single input artifact the caller supplied, which the
foundation has already validated and hashed. Everything else is fixed: the local-only input restrictions
(`media_common.INPUT_RESTRICTIONS`), the exact entries requested, and the JSON writer. The output is read
from the process boundary's private raw capture and normalized by `parser.py`; neither ffprobe's JSON nor
its stdout reaches the result. This module never starts a process itself and never imports the
subprocess module.
"""

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from .. import diagnostics as dg
from .. import media_common as media
from .. import model
from .. import process as proc
from ..capabilities import Capability, TimeoutPolicy
from ..execution import AdapterOutcome
from . import parser

ADAPTER_ID = "ffprobe"
ADAPTER_VERSION = "1.0.0"
EXECUTABLE_NAME = "ffprobe"
INSPECT = f"{ADAPTER_ID}.inspect"

# Exactly these entries; never -show_format / -show_streams, which would add the source's metadata tags.
ENTRIES = ("format=format_name,duration"
           ":stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,"
           "sample_rate,channels,channel_layout,duration")
VERSION_ARGV = ("-version",)


def inspect_argv(input_path):
    """The one authorized inspection command. `input_path` is the validated input artifact."""
    return (("-v", "error") + media.INPUT_RESTRICTIONS
            + ("-show_entries", ENTRIES, "-of", "json", "-i", media.local_url(input_path)))


PROBE_TIMEOUT = 10.0
CAPTURE_BYTES = 1024 * 1024

CAPABILITIES = (
    Capability(
        id=INSPECT, category="INSPECT",
        description="Summarize one local media file: container, duration, stream counts, and the first video and "
                    "audio stream's codec, size, frame rate, sample rate and channels. No tags, no paths.",
        operation_class="READ_ONLY", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_tool=True, requires_project=True,
        timeout=TimeoutPolicy(default=60.0, maximum=300.0),
        notes=("Consumes exactly one input artifact; its capture context is optional.",)),
)

DESCRIPTOR = model.AdapterDescriptor(
    adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION, tool_family="MEDIA", target_tool="ffprobe",
    adapter_kind="CLI", state_model="STATELESS", supported_platforms=("WINDOWS", "MACOS", "LINUX"),
    capabilities=CAPABILITIES,
    availability="an ffprobe executable on PATH (absolute PATH entries only)",
    compatibility_notes=(
        "Local files only: protocol whitelist `file` and a closed list of self-contained demuxers; playlists, "
        "live inputs and network protocols are refused.",
        "Returns a bounded summary without metadata tags or paths.",
    ))


class FfprobeAdapter(model.ToolAdapter):
    """`which` is a code-level seam for tests; a request can never choose the executable."""

    descriptor = DESCRIPTOR

    def __init__(self, which=shutil.which, capture_bytes=CAPTURE_BYTES):
        self._which = which
        self._capture_bytes = capture_bytes

    def probe(self):
        platform = model.current_platform()
        unusable = lambda reason: tuple((c.id, False, reason) for c in CAPABILITIES)
        executable, problem = media.resolve_executable(self._which, EXECUTABLE_NAME)
        if problem:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform, detail=problem,
                                     capability_availability=unusable("ffprobe is not available"))
        neutral = str(Path(tempfile.gettempdir()).resolve())
        try:
            outcome = proc.run_process(proc.ToolProcessSpec(executable=executable, argv=VERSION_ARGV, cwd=neutral,
                                                            timeout=PROBE_TIMEOUT), [neutral])
        except proc.ProcessSpecError as exc:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"ffprobe could not be started: {exc}",
                                     capability_availability=unusable("ffprobe could not be started"))
        if outcome.timed_out or outcome.exit_code != 0 or outcome.truncated:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"`ffprobe -version` did not complete normally (exit {outcome.exit_code})",
                                     capability_availability=unusable("ffprobe did not run normally"))
        first = outcome.raw_stdout.decode("utf-8", errors="replace").splitlines()[:1]
        version = media.version_from(first[0] if first else "", EXECUTABLE_NAME)
        if version is None:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable, platform=platform,
                                     detail="unrecognized `ffprobe -version` output; the version could not be "
                                            "established",
                                     capability_availability=unusable("ffprobe version unknown"))
        return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=executable, tool_version=version,
                                 platform=platform, detail=f"ffprobe {version} at {executable}",
                                 capability_availability=tuple((c.id, True, "") for c in CAPABILITIES))

    def execute(self, request, context):
        if request.capability_id != INSPECT:
            raise AssertionError(f"{request.capability_id} is declared but not implemented")
        inputs = context.input_artifacts
        if len(inputs) != 1:
            return _refuse(f"{INSPECT} consumes exactly one input artifact; {len(inputs)} were supplied")
        source = inputs[0]
        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=inspect_argv(source.absolute_path),
                                    cwd=context.project_root, timeout=context.timeout,
                                    capture_bytes=self._capture_bytes)
        outcome = context.run(spec)
        public = _without_output(outcome)
        record = dict(command=_command(spec, source), environment=spec.env.metadata())
        if outcome.timed_out:
            return AdapterOutcome(ok=False, process=public, detail="ffprobe timed out", **record)
        if outcome.truncated:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public,
                                  detail="ffprobe output reached the capture bound and was truncated; no partial "
                                         "summary is reported", **record)
        if outcome.exit_code != 0:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public,
                                  detail=f"ffprobe refused the input: {_reason(outcome, source)}", **record)
        try:
            summary = parser.parse(outcome.raw_stdout)
        except parser.ProbeParseError as exc:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public,
                                  detail=f"ffprobe output could not be read as the expected summary ({exc})", **record)
        return AdapterOutcome(ok=True, exit_code=0, process=public,
                              data={"source_artifact_id": source.artifact_id, **summary}, **record)


def _refuse(message):
    return AdapterOutcome(ok=True, diagnostics=(dg.make("INVALID_TOOL_REQUEST", message, ADAPTER_ID, INSPECT),))


def _without_output(outcome):
    """The outcome with no captured payload: exit code, timing, byte counts, truncation and redaction counts
    still reach the foundation, but ffprobe's JSON and its messages (which name the input path) do not."""
    return replace(outcome, stdout="", stderr="", raw_stdout=b"", raw_stderr=b"")


def _reason(outcome, source):
    """ffprobe's first error line with the input's location replaced by its artifact id."""
    text = outcome.raw_stderr.decode("utf-8", errors="replace").strip().splitlines()
    line = text[0] if text else "no reason given"
    return media.scrub_paths(line, ((f"<input {source.artifact_id}>", source.absolute_path),))[:300]


def _command(spec, source):
    """The recorded command, with the input's location replaced by its artifact id. The path is replaced before
    the boundary's redaction runs, so a credential-shaped file name cannot defeat the match."""
    placeholder = ((f"<input {source.artifact_id}>", source.absolute_path),)
    return replace(spec, argv=tuple(media.scrub_paths(a, placeholder) for a in spec.argv)).command_for_provenance()
