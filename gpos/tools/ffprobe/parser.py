"""Normalize ffprobe's JSON output into a bounded, tag-free media summary.

A pure function over the exact bytes ffprobe wrote (the process boundary's private raw capture, never the
public redacted text). It starts no process and reads no file.

The command asks ffprobe for a fixed list of entries with `-show_entries` alone. That matters: adding
`-show_format` or `-show_streams` widens the output to every entry *including the source's metadata
tags* (titles, comments, encoder strings — anything the file's author wrote). With `-show_entries` only,
ffprobe returns exactly the requested fields, and tags are shown only when explicitly requested, which
this adapter never does.

The parser trusts nothing about the shape: every field is type- and range-checked, strings are limited to
short safe tokens, numbers that ffprobe prints as text are validated before they are carried, and any
unexpected structure is an error rather than a guess. Only the normalized summary leaves this module —
never ffprobe's JSON, never a path, never a tag.
"""

import json
import re

MAX_STREAMS = 64
MAX_DIMENSION = 65535
MAX_SAMPLE_RATE = 1_000_000
MAX_CHANNELS = 64
TOP_LEVEL = {"format", "streams", "programs", "stream_groups"}
FORMAT_FIELDS = {"format_name", "duration"}
STREAM_FIELDS = {"index", "codec_type", "codec_name", "width", "height", "pix_fmt", "avg_frame_rate",
                 "sample_rate", "channels", "channel_layout", "duration"}
CODEC_TYPES = {"video", "audio", "subtitle", "data", "attachment"}

TOKEN = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
LAYOUT = re.compile(r"^[A-Za-z0-9_.()+-]{1,64}$")
DECIMAL = re.compile(r"^[0-9]{1,12}(?:\.[0-9]{1,9})?$")
RATE = re.compile(r"^([0-9]{1,9})/([0-9]{1,9})$")
SAMPLE_RATE = re.compile(r"^[0-9]{1,7}$")
FORMAT_NAMES = re.compile(r"^[a-z0-9_]{1,32}(?:,[a-z0-9_]{1,32}){0,15}$")


class ProbeParseError(ValueError):
    """ffprobe's output is not the complete, expected JSON; no media summary may be reported from it."""


def parse(raw):
    """The normalized media summary from ffprobe's JSON bytes. Raises ProbeParseError."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ProbeParseError("ffprobe output must be the exact captured bytes")
    try:
        document = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProbeParseError(f"ffprobe output is not valid JSON ({type(exc).__name__})") from None
    if not isinstance(document, dict) or not set(document) <= TOP_LEVEL or "format" not in document:
        raise ProbeParseError("ffprobe output does not have the expected top-level sections")
    fmt = document["format"]
    if not isinstance(fmt, dict) or not set(fmt) <= FORMAT_FIELDS:
        raise ProbeParseError("the format section has unexpected fields")
    streams = document.get("streams", [])
    if not isinstance(streams, list):
        raise ProbeParseError("the streams section is not a list")
    if len(streams) > MAX_STREAMS:
        raise ProbeParseError(f"{len(streams)} streams exceed the limit of {MAX_STREAMS}")
    format_names = fmt.get("format_name")
    if not isinstance(format_names, str) or not FORMAT_NAMES.fullmatch(format_names):
        raise ProbeParseError("format_name is missing or malformed")
    normalized = [_stream(s) for s in streams]
    videos = [s for s in normalized if s["codec_type"] == "video"]
    audios = [s for s in normalized if s["codec_type"] == "audio"]
    return {
        "format_names": format_names.split(","),
        "duration_seconds": _decimal(fmt.get("duration"), "format duration"),
        "stream_count": len(normalized),
        "video_stream_count": len(videos),
        "audio_stream_count": len(audios),
        "other_stream_count": len(normalized) - len(videos) - len(audios),
        "primary_video": _video(videos[0]) if videos else None,
        "primary_audio": _audio(audios[0]) if audios else None,
    }


def _stream(stream):
    if not isinstance(stream, dict) or not set(stream) <= STREAM_FIELDS:
        raise ProbeParseError("a stream has unexpected fields")
    index = stream.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < MAX_STREAMS:
        raise ProbeParseError("a stream index is missing or out of range")
    codec_type = stream.get("codec_type")
    if codec_type not in CODEC_TYPES:
        raise ProbeParseError(f"unexpected codec_type {str(codec_type)[:20]!r}")
    return dict(stream, index=index, codec_type=codec_type)


def _video(stream):
    return {
        "index": stream["index"],
        "codec_name": _token(stream.get("codec_name"), TOKEN),
        "width": _integer(stream.get("width"), 1, MAX_DIMENSION, "width"),
        "height": _integer(stream.get("height"), 1, MAX_DIMENSION, "height"),
        "pixel_format": _token(stream.get("pix_fmt"), TOKEN),
        "average_frame_rate": _rate(stream.get("avg_frame_rate")),
        "duration_seconds": _decimal(stream.get("duration"), "video duration"),
    }


def _audio(stream):
    sample_rate = stream.get("sample_rate")
    if sample_rate is not None:
        if not isinstance(sample_rate, str) or not SAMPLE_RATE.fullmatch(sample_rate):
            raise ProbeParseError("sample_rate is malformed")
        sample_rate = int(sample_rate)
        if not 1 <= sample_rate <= MAX_SAMPLE_RATE:
            raise ProbeParseError("sample_rate is out of range")
    return {
        "index": stream["index"],
        "codec_name": _token(stream.get("codec_name"), TOKEN),
        "sample_rate_hz": sample_rate,
        "channels": _integer(stream.get("channels"), 1, MAX_CHANNELS, "channels"),
        "channel_layout": _token(stream.get("channel_layout"), LAYOUT),
        "duration_seconds": _decimal(stream.get("duration"), "audio duration"),
    }


def _token(value, pattern):
    """A short safe identifier, or None when absent. Anything else is refused, not truncated."""
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ProbeParseError("a codec, pixel-format or layout name is malformed")
    return value


def _integer(value, low, high, what):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ProbeParseError(f"{what} is malformed or out of range")
    return value


def _decimal(value, what):
    """A non-negative decimal kept as text (no binary-float rounding), or None when ffprobe reports none."""
    if value is None or value == "N/A":
        return None
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise ProbeParseError(f"{what} is not a non-negative decimal")
    return value


def _rate(value):
    """A rational frame rate "num/den" kept as text; "0/0" (unknown) becomes None."""
    if value is None:
        return None
    match = RATE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ProbeParseError("average_frame_rate is not a rational number")
    numerator, denominator = (int(g) for g in match.groups())
    if numerator == 0 and denominator == 0:
        return None
    if denominator == 0:
        raise ProbeParseError("average_frame_rate has a zero denominator")
    return value
