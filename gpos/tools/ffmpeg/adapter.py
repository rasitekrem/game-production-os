"""Production derived-media adapter: FFmpeg (Phase 2C-2).

Three capabilities turn one caller-supplied capture into a smaller, reviewable piece of evidence:

    ffmpeg.extract-frame   one still frame        -> PNG   -> VISUAL_EVIDENCE
    ffmpeg.extract-clip    a bounded review clip  -> MKV   -> MOTION_EVIDENCE   (FFV1 video, PCM audio)
    ffmpeg.extract-audio   a bounded audio span   -> WAV   -> AUDIO_EVIDENCE    (PCM 16-bit)

MEDIA PROCESSING DOES NOT UPGRADE CAPTURE AUTHORITY. Every output is a DERIVED artifact of the input,
and its evidence candidate carries the *input's* capture context. A frame taken from a TARGET_RUNTIME
recording is TARGET_RUNTIME evidence, even though FFmpeg itself ran as an OFFLINE_ANALYSIS execution. A
transform is refused before FFmpeg starts when the input has no capture context, or when its context is
not one the frozen registry allows for the evidence type being produced. No capability can produce
RUNTIME_, DEVICE_, PERFORMANCE_ or HUMAN_EVIDENCE: processing a file observes nothing new.

This is not an FFmpeg command runner. Every invocation comes from a fixed template in this module; the
only variable slots are the validated input artifact's path, the adapter-chosen output path in this
execution's workspace, and numbers the adapter canonicalized itself. No caller codec, filter, stream
map, format, protocol, option, output name or executable reaches the command line.

Behaviours verified against the installed FFmpeg before being relied on, and handled here rather than
trusted:

* `-n` refuses to overwrite an existing output but still exits 0, so an existing output file is refused
  before FFmpeg runs — otherwise a stale file would be reported as a fresh derived artifact;
* the `image2` muxer opens files itself and skips the `-n` check entirely, so frames are written with
  `image2pipe` to a normal file output, where `-n` applies and no filename pattern is ever expanded;
* `-fs` stops writing after the limit but exits 0, and may overshoot by a whole packet or cluster, so an
  output at or above its limit is treated as incomplete, never as evidence;
* a frame requested past the end of the media produces an empty file with exit 0, so every output must be
  non-empty and start with its format's signature;
* `-fflags +bitexact` makes the outputs byte-reproducible, so identical inputs give identical hashes.

Outputs carry no source metadata or chapters. Nothing from FFmpeg's output reaches the result: its
messages name local paths, so a failure reason is reported with those paths replaced by artifact ids.
This module never starts a process itself and never imports the subprocess module.
"""

import math
import re
import shutil
import tempfile
from dataclasses import replace
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from .. import diagnostics as dg
from .. import media_common as media
from .. import model
from .. import process as proc
from ..artifacts import ArtifactSpec
from ..capabilities import Capability, TimeoutPolicy
from ..evidence import EvidenceCandidate
from ..execution import AdapterOutcome

ADAPTER_ID = "ffmpeg"
ADAPTER_VERSION = "1.0.0"
EXECUTABLE_NAME = "ffmpeg"

EXTRACT_FRAME = f"{ADAPTER_ID}.extract-frame"
EXTRACT_CLIP = f"{ADAPTER_ID}.extract-clip"
EXTRACT_AUDIO = f"{ADAPTER_ID}.extract-audio"

# Bounds. The clip ceiling is below the 120 s the brief allowed: lossless FFV1 at 1080p60 measured
# 6.6-139 MB/s here, so 30 s already approaches the 4 GiB clip size cap in the worst case.
MAX_TIMESTAMP_SECONDS = Decimal(86400)          # 24 h into a recording
MAX_CLIP_SECONDS = Decimal(30)
MAX_AUDIO_SECONDS = Decimal(120)
SIZE_LIMITS = {"frame": 64 * 1024 ** 2, "clip": 4 * 1024 ** 3, "audio": 256 * 1024 ** 2}
PRECISION = Decimal("0.000001")                 # FFmpeg's microsecond time base
SECONDS_TEXT = re.compile(r"^[0-9]{1,6}(?:\.[0-9]{1,6})?$")  # ASCII digits only

# What each transform produces: file name, artifact kind, media type, format signature, evidence type.
OUTPUTS = {
    "frame": ("frame.png", "IMAGE", "image/png", b"\x89PNG\r\n\x1a\n", "VISUAL_EVIDENCE"),
    "clip": ("clip.mkv", "VIDEO", "video/x-matroska", b"\x1a\x45\xdf\xa3", "MOTION_EVIDENCE"),
    "audio": ("audio.wav", "AUDIO", "audio/wav", b"RIFF", "AUDIO_EVIDENCE"),
}
KIND = {EXTRACT_FRAME: "frame", EXTRACT_CLIP: "clip", EXTRACT_AUDIO: "audio"}

# The frozen registry's compatible capture contexts for each evidence type, declared explicitly and
# checked against the registry at registration (and by the tests) — never widened here.
VISUAL_CONTEXTS = ("DCC_RENDER", "EDITOR", "TARGET_RUNTIME", "DIAGNOSTIC_RUNTIME", "AUTOMATED_TEST")
MOTION_CONTEXTS = ("DCC_RENDER", "EDITOR", "TARGET_RUNTIME", "DIAGNOSTIC_RUNTIME", "AUTOMATED_TEST")
AUDIO_CONTEXTS = ("EDITOR", "TARGET_RUNTIME", "DIAGNOSTIC_RUNTIME", "AUTOMATED_TEST")

# The build must provide exactly these; checked by the probe, never assumed.
REQUIRED_ENCODERS = ("png", "ffv1", "pcm_s16le")
REQUIRED_MUXERS = ("image2pipe", "matroska", "wav")
VERSION_ARGV = ("-version",)
HELP_ARGV = tuple(("-hide_banner", "-h", f"encoder={name}") for name in REQUIRED_ENCODERS) \
    + tuple(("-hide_banner", "-h", f"muxer={name}") for name in REQUIRED_MUXERS)
HELP_HEADER = {**{f"encoder={n}": f"Encoder {n} [" for n in REQUIRED_ENCODERS},
               **{f"muxer={n}": f"Muxer {n} [" for n in REQUIRED_MUXERS}}

# ---------------------------------------------------------------- fixed command templates

COMMON = ("-hide_banner", "-loglevel", "error", "-nostdin", "-n") + media.INPUT_RESTRICTIONS
CLEAN_OUTPUT = ("-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact")


def frame_argv(input_path, timestamp, output_path, size_limit):
    return COMMON + ("-ss", timestamp, "-accurate_seek", "-i", media.local_url(input_path),
                     "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn") + CLEAN_OUTPUT + (
        "-c:v", "png", "-f", "image2pipe", "-fs", str(size_limit), media.local_url(output_path))


def clip_argv(input_path, start, duration, output_path, size_limit):
    return COMMON + ("-ss", start, "-accurate_seek", "-i", media.local_url(input_path), "-t", duration,
                     "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn") + CLEAN_OUTPUT + (
        "-c:v", "ffv1", "-c:a", "pcm_s16le", "-f", "matroska", "-fs", str(size_limit), media.local_url(output_path))


def audio_argv(input_path, start, duration, output_path, size_limit):
    return COMMON + ("-ss", start, "-accurate_seek", "-i", media.local_url(input_path), "-t", duration,
                     "-map", "0:a:0", "-vn", "-sn", "-dn") + CLEAN_OUTPUT + (
        "-c:a", "pcm_s16le", "-f", "wav", "-fs", str(size_limit), media.local_url(output_path))


# ---------------------------------------------------------------- declarations

def _transform(cap_id, description, input_kinds, artifact_kind, evidence_type, contexts, timeout, side_effect):
    return Capability(
        id=cap_id, category="TRANSFORM", description=description,
        operation_class="MUTATING", state_model="STATELESS", execution_context="OFFLINE_ANALYSIS",
        requires_tool=True, requires_project=True, dry_run_supported=True,
        input_kinds=input_kinds, artifact_kinds=(artifact_kind,),
        potential_evidence=tuple((evidence_type, c) for c in contexts),
        timeout=timeout, side_effect_scope=side_effect,
        notes=("Consumes exactly one input artifact, which must carry a capture context the output inherits.",))


CAPABILITIES = (
    _transform(EXTRACT_FRAME, "Extract one still frame at a timestamp as a PNG; offers VISUAL_EVIDENCE in the "
                              "source's capture context.",
               ("timestamp_seconds",), "IMAGE", "VISUAL_EVIDENCE", VISUAL_CONTEXTS,
               TimeoutPolicy(default=120.0, maximum=600.0), "writes frame.png into this execution's workspace"),
    _transform(EXTRACT_CLIP, f"Extract a review clip of at most {MAX_CLIP_SECONDS} s as lossless FFV1 video with "
                             f"PCM audio in Matroska; offers MOTION_EVIDENCE in the source's capture context.",
               ("start_seconds", "duration_seconds"), "VIDEO", "MOTION_EVIDENCE", MOTION_CONTEXTS,
               TimeoutPolicy(default=600.0, maximum=1800.0), "writes clip.mkv into this execution's workspace"),
    _transform(EXTRACT_AUDIO, f"Extract at most {MAX_AUDIO_SECONDS} s of the first audio stream as 16-bit PCM WAV; "
                              f"offers AUDIO_EVIDENCE in the source's capture context.",
               ("start_seconds", "duration_seconds"), "AUDIO", "AUDIO_EVIDENCE", AUDIO_CONTEXTS,
               TimeoutPolicy(default=300.0, maximum=1200.0), "writes audio.wav into this execution's workspace"),
)

DESCRIPTOR = model.AdapterDescriptor(
    adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION, tool_family="MEDIA", target_tool="FFmpeg",
    adapter_kind="CLI", state_model="STATELESS", supported_platforms=("WINDOWS", "MACOS", "LINUX"),
    capabilities=CAPABILITIES,
    availability="an ffmpeg executable on PATH (absolute PATH entries only) whose build provides the png, ffv1 "
                 "and pcm_s16le encoders and the image2pipe, matroska and wav muxers",
    compatibility_notes=(
        "Local files only: protocol whitelist `file` and a closed list of self-contained demuxers; playlists, "
        "live inputs and network protocols are refused.",
        "Derived outputs inherit the source's capture context; media processing never upgrades it.",
        "Fixed templates only: no caller codec, filter, map, format, option or output name.",
    ))


class FfmpegAdapter(model.ToolAdapter):
    """`which` and `size_limits` are code-level seams for tests; a request can change neither."""

    descriptor = DESCRIPTOR

    def __init__(self, which=shutil.which, size_limits=None):
        self._which = which
        self._size_limits = dict(SIZE_LIMITS, **(size_limits or {}))

    # ------------------------------------------------------------ probe

    def probe(self):
        platform = model.current_platform()
        unusable = lambda reason: tuple((c.id, False, reason) for c in CAPABILITIES)
        executable, problem = media.resolve_executable(self._which, EXECUTABLE_NAME)
        if problem:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, platform=platform, detail=problem,
                                     capability_availability=unusable("FFmpeg is not available"))
        neutral = str(Path(tempfile.gettempdir()).resolve())
        run = lambda argv: proc.run_process(proc.ToolProcessSpec(executable=executable, argv=argv, cwd=neutral,
                                                                 timeout=10.0), [neutral])
        try:
            outcome = run(VERSION_ARGV)
        except proc.ProcessSpecError as exc:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"FFmpeg could not be started: {exc}",
                                     capability_availability=unusable("FFmpeg could not be started"))
        if outcome.timed_out or outcome.exit_code != 0 or outcome.truncated:
            return model.ProbeResult(ADAPTER_ID, model.UNAVAILABLE, tool_path=executable, platform=platform,
                                     detail=f"`ffmpeg -version` did not complete normally (exit {outcome.exit_code})",
                                     capability_availability=unusable("FFmpeg did not run normally"))
        first = outcome.raw_stdout.decode("utf-8", errors="replace").splitlines()[:1]
        version = media.version_from(first[0] if first else "", EXECUTABLE_NAME)
        if version is None:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable, platform=platform,
                                     detail="unrecognized `ffmpeg -version` output; the version could not be "
                                            "established",
                                     capability_availability=unusable("FFmpeg version unknown"))
        missing = self._missing_components(run)
        if missing:
            return model.ProbeResult(ADAPTER_ID, model.VERSION_UNSUPPORTED, tool_path=executable,
                                     tool_version=version, platform=platform,
                                     detail=f"this FFmpeg build lacks {', '.join(missing)}, required by the fixed "
                                            f"output formats",
                                     capability_availability=unusable("required encoder or muxer missing"))
        return model.ProbeResult(ADAPTER_ID, model.AVAILABLE, tool_path=executable, tool_version=version,
                                 platform=platform, detail=f"FFmpeg {version} at {executable}",
                                 capability_availability=tuple((c.id, True, "") for c in CAPABILITIES))

    @staticmethod
    def _missing_components(run):
        """Which required encoders or muxers this build lacks. `ffmpeg -h encoder=<name>` exits 0 whether or
        not the encoder exists, so presence is read from its documented header line ("Encoder png [...]"),
        and anything else — including an unrecognized reply — counts as missing."""
        missing = []
        for argv in HELP_ARGV:
            topic = argv[-1]
            try:
                outcome = run(argv)
            except proc.ProcessSpecError:
                missing.append(topic)
                continue
            first = outcome.raw_stdout.decode("utf-8", errors="replace").lstrip().splitlines()[:1]
            if outcome.exit_code != 0 or not first or not first[0].startswith(HELP_HEADER[topic]):
                missing.append(topic.replace("=", " "))
        return missing

    # ------------------------------------------------------------ execution

    def execute(self, request, context):
        kind = KIND.get(request.capability_id)
        if kind is None:
            raise AssertionError(f"{request.capability_id} is declared but not implemented")
        cap = request.capability_id
        inputs = context.input_artifacts
        if len(inputs) != 1:
            return _refuse(cap, f"{cap} consumes exactly one input artifact; {len(inputs)} were supplied")
        source = inputs[0]
        filename, artifact_kind, media_type, signature, evidence_type = OUTPUTS[kind]
        if source.origin_capture_context is None:
            return _refuse(cap, f"input artifact {source.artifact_id!r} has no capture context. A derived artifact "
                                f"inherits where its source was captured, so the source's context must be stated")
        if (evidence_type, source.origin_capture_context) not in context.capability.evidence_pairs():
            return _refuse(cap, f"{evidence_type} cannot come from a source captured in "
                                f"{source.origin_capture_context} (registry evidence_context_compatibility); "
                                f"nothing is produced rather than a different evidence type")
        params, problem = _parameters(kind, request.inputs or {})
        if problem:
            return _refuse(cap, problem)
        workspace = Path(context.workspace)
        output = workspace / filename
        if workspace.resolve() == Path(source.absolute_path).resolve().parent:
            return _refuse(cap, "the output workspace is the source artifact's own directory; derived media is never "
                                "written beside its source")
        argv = _argv(kind, source.absolute_path, params, output, self._size_limits[kind])
        described = _described(kind, params, source)
        if context.dry_run:
            return AdapterOutcome(plan=(f"would write {filename} ({artifact_kind}) derived from input artifact "
                                        f"{source.artifact_id!r}: {described}",
                                        f"would offer {evidence_type} captured in {source.origin_capture_context}",
                                        "no FFmpeg process, workspace or file is created by a dry run"),
                                  data={"source_artifact_id": source.artifact_id, "output_artifact_id": kind, **params})
        if output.exists() or output.is_symlink():
            return _refuse(cap, f"the workspace already holds {filename}; an existing file is never reported as a "
                                f"newly derived artifact")
        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv, cwd=str(workspace),
                                    timeout=context.timeout)
        outcome = context.run(spec)
        public = _without_output(outcome)
        record = dict(command=_command(spec, source, workspace), environment=spec.env.metadata(),
                      data={"source_artifact_id": source.artifact_id, "output_artifact_id": kind, **params})
        produced = (ArtifactSpec(kind, artifact_kind, str(output), media_type=media_type,
                                 description=f"{described}; derived by FFmpeg",
                                 derived_from=(source.artifact_id,)),) if output.exists() else ()
        # Any failure after FFmpeg started: a partial output is still declared, the foundation records it
        # as incomplete, and no evidence is offered. A file was written, so the mutation is reported.
        record["mutation_performed"] = bool(produced)
        if outcome.timed_out:
            return AdapterOutcome(ok=False, process=public, artifacts=produced, detail="FFmpeg timed out", **record)
        if outcome.truncated:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public, artifacts=produced,
                                  detail="FFmpeg's messages reached the capture bound; the result is not trusted",
                                  **record)
        if outcome.exit_code != 0:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public, artifacts=produced,
                                  detail=f"FFmpeg failed: {_reason(outcome, source, workspace)}", **record)
        defect = _output_defect(kind, output, signature, self._size_limits[kind])
        if defect:
            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, process=public, artifacts=produced,
                                  detail=defect, **record)
        candidate = EvidenceCandidate(
            evidence_type=evidence_type, capture_context=source.origin_capture_context,
            summary=f"{described}, derived by FFmpeg from input artifact {source.artifact_id}",
            subject_kind="TASK", subject_ref="", source_adapter=ADAPTER_ID, source_capability=cap,
            generated_at=context.clock.now(), artifact_ids=(kind,), derived_from=(source.artifact_id,),
            limitations=_limitations(kind, params, source))
        return AdapterOutcome(ok=True, exit_code=0, process=public, artifacts=produced, evidence=(candidate,),
                              **record)


# ---------------------------------------------------------------- helpers

def seconds(value, name, allow_zero, maximum):
    """(canonical text, None) or (None, reason). Accepts an int, a finite float, a Decimal or a plain decimal
    string ("12" or "12.5", at most 6 fractional digits). Never forwards the caller's text: the returned value
    is the adapter's own canonical rendering at FFmpeg's microsecond precision."""
    if isinstance(value, bool):
        return None, f"{name} must be a number, not a boolean"
    try:
        if isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                return None, f"{name} must be finite"
            number = Decimal(repr(value))
        elif isinstance(value, Decimal):
            number = value
        elif isinstance(value, str):
            if not SECONDS_TEXT.fullmatch(value):
                return None, f"{name} {value[:32]!r} is not a plain non-negative decimal number of seconds"
            number = Decimal(value)
        else:
            return None, f"{name} must be a number of seconds"
    except (InvalidOperation, ValueError):
        return None, f"{name} is not a number"
    if not number.is_finite():
        return None, f"{name} must be finite"
    if number > maximum:  # bounded before rounding, so an enormous value is refused, never quantized
        return None, f"{name} exceeds the maximum of {maximum} s"
    number = number.quantize(PRECISION, rounding=ROUND_HALF_EVEN)
    if number < 0 or (number == 0 and not allow_zero):
        return None, f"{name} must be {'non-negative' if allow_zero else 'greater than zero'}"
    return format(abs(number), "f"), None  # abs: a negative zero is rendered as 0, never as "-0"


def _parameters(kind, inputs):
    """Validated, canonical parameters for a transform, or (None, reason)."""
    if kind == "frame":
        if "timestamp_seconds" not in inputs:
            return None, "timestamp_seconds is required"
        value, problem = seconds(inputs["timestamp_seconds"], "timestamp_seconds", True, MAX_TIMESTAMP_SECONDS)
        return ({"timestamp_seconds": value}, None) if not problem else (None, problem)
    missing = [n for n in ("start_seconds", "duration_seconds") if n not in inputs]
    if missing:
        return None, f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required"
    start, problem = seconds(inputs["start_seconds"], "start_seconds", True, MAX_TIMESTAMP_SECONDS)
    if problem:
        return None, problem
    ceiling = MAX_CLIP_SECONDS if kind == "clip" else MAX_AUDIO_SECONDS
    duration, problem = seconds(inputs["duration_seconds"], "duration_seconds", False, ceiling)
    if problem:
        return None, problem
    return {"start_seconds": start, "duration_seconds": duration}, None


def _argv(kind, input_path, params, output, size_limit):
    if kind == "frame":
        return frame_argv(input_path, params["timestamp_seconds"], output, size_limit)
    if kind == "clip":
        return clip_argv(input_path, params["start_seconds"], params["duration_seconds"], output, size_limit)
    return audio_argv(input_path, params["start_seconds"], params["duration_seconds"], output, size_limit)


def _output_defect(kind, output, signature, size_limit):
    """Why a finished output cannot be trusted, or None."""
    if not output.is_file():
        return "FFmpeg reported success but wrote no output (the requested time may be past the end of the media)"
    size = output.stat().st_size
    if size == 0:
        return "FFmpeg reported success but the output is empty (the requested time may be past the end of the media)"
    if size >= size_limit:
        return (f"the output reached its size limit of {size_limit} bytes and was cut short; request a shorter "
                f"segment. The partial file is kept as an incomplete artifact and is not evidence")
    with open(output, "rb") as fh:
        head = fh.read(12)
    if not head.startswith(signature) or (kind == "audio" and head[8:12] != b"WAVE"):
        return f"the output does not have the expected {OUTPUTS[kind][2]} format signature"
    return None


def _described(kind, params, source):
    if kind == "frame":
        return f"frame at {params['timestamp_seconds']} s"
    end = Decimal(params["start_seconds"]) + Decimal(params["duration_seconds"])
    what = "clip" if kind == "clip" else "audio"
    return f"{what} {params['start_seconds']}-{format(end, 'f')} s"


def _limitations(kind, params, source):
    common = (f"Derived by FFmpeg from input artifact {source.artifact_id}: it shows the source's content and carries "
              f"the source's capture context ({source.origin_capture_context}); it is not an independent capture.",)
    if kind == "frame":
        return common + ("A single frame shows appearance at one instant only; it says nothing about motion.",)
    tail = ("If the source ends before the requested window does, the output is correspondingly shorter; inspect "
            "the derived artifact to establish its exact duration.",)
    if kind == "clip":
        return common + ("Transcoded to lossless FFV1 video with 16-bit PCM audio; audio in a clip is not audio "
                         "evidence.",) + tail
    return common + ("First audio stream only, as 16-bit PCM.",) + tail


def _refuse(capability_id, message):
    return AdapterOutcome(ok=True, diagnostics=(dg.make("INVALID_TOOL_REQUEST", message, ADAPTER_ID, capability_id),))


def _without_output(outcome):
    """The outcome without captured payload: FFmpeg's messages name local paths and are not useful to a caller;
    exit code, timing, byte counts, truncation and redaction counts still reach the foundation."""
    return replace(outcome, stdout="", stderr="", raw_stdout=b"", raw_stderr=b"")


def _reason(outcome, source, workspace):
    text = outcome.raw_stderr.decode("utf-8", errors="replace").strip().splitlines()
    line = text[0] if text else "no reason given"
    return media.scrub_paths(line, ((f"<input {source.artifact_id}>", source.absolute_path),
                                    ("<workspace>", str(workspace))))[:300]


def _command(spec, source, workspace):
    """The recorded command, with local paths replaced by the input's artifact id and `<workspace>`. Paths are
    replaced before the boundary's redaction runs, so a credential-shaped file name cannot defeat the match."""
    paths = ((f"<input {source.artifact_id}>", source.absolute_path), ("<workspace>", str(workspace)))
    return replace(spec, argv=tuple(media.scrub_paths(a, paths) for a in spec.argv)).command_for_provenance()
