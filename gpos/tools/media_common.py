"""Shared, security-relevant constants and helpers for the two local media adapters (Phase 2C-2).

The media adapters are two separate production adapters — one per executable — because the frozen
foundation records one tool path and version in execution provenance, and a single adapter driving
two executables would make that provenance ambiguous. This module holds only what both must agree on:
the closed input restrictions, the executable lookup rule, the version-line check and the scrubbing of
local paths from tool messages. It is not a media framework; the tool foundation is the framework.

Local-only input contract. Every input reaches FFmpeg or ffprobe as `file:` plus an absolute path the
foundation already validated, behind two closed whitelists that no caller can change:

* `-protocol_whitelist file` — FFmpeg enables every protocol a build supports by default (this build has
  HTTP and TLS). Only the local file protocol is permitted, for the input and for anything the input
  might try to open in turn.
* `-format_whitelist` — only self-contained media demuxers. Playlist, redirecting, live and network
  demuxers (hls, dash, concat, image2 sequences, sdp, rtsp, …) are not on it, so a local file that
  merely *describes* remote or other local media is refused before it is followed.

Each whitelist blocks a local playlist that references an HTTP server on its own; together they are
defence in depth. The FFmpeg manual documents both options ("Set a ','-separated list of allowed
protocols" / "',' separated list of allowed demuxers. By default all are allowed."). An FFmpeg that did
not know either option would refuse to run rather than ignore it (unknown options exit non-zero), so no
minimum version is needed to keep the contract.
"""

import os
import re
from pathlib import Path

# Self-contained media demuxers accepted as input. Each name matches FFmpeg's demuxer list entry of
# that name (the mp4/mov demuxer is listed as "mov,mp4,m4a,3gp,3g2,mj2"; "mov" matches it).
SAFE_DEMUXERS = ("mov", "matroska", "avi", "mpegts", "wav", "mp3", "flac", "ogg")
ALLOWED_PROTOCOLS = "file"

# Fixed input restrictions, placed before `-i` so they apply to the input.
INPUT_RESTRICTIONS = ("-protocol_whitelist", ALLOWED_PROTOCOLS, "-format_whitelist", ",".join(SAFE_DEMUXERS))

# Demuxers that must never appear on the whitelist: they follow references to other media or to networks.
FORBIDDEN_DEMUXERS = ("hls", "dash", "concat", "image2", "image2pipe", "sdp", "rtsp", "rtp", "tee", "lavfi",
                      "avfoundation", "dshow", "v4l2", "x11grab", "gdigrab", "pulse", "alsa", "jack", "fbdev")
NETWORK_PROTOCOLS = ("http", "https", "tcp", "udp", "rtmp", "rtmps", "rtsp", "srt", "tls", "ftp", "sftp",
                     "hls", "rtp", "gopher", "ipfs", "ipns", "smb", "unix", "zmq", "pipe", "data", "crypto",
                     "subfile", "concat", "cache", "async")


def local_url(path):
    """An explicit `file:` URL for a validated absolute path, so no part of a path can be read as another
    protocol, a pattern or an option."""
    return f"file:{Path(path)}"


def resolve_executable(which, name):
    """(absolute resolved path, None) or (None, reason). A match found only through a relative PATH entry
    is refused, because the program run would then depend on the working directory."""
    found = which(name)
    if not found:
        return None, f"no {name} executable was found on PATH"
    if not os.path.isabs(found):
        return None, (f"{name} was only found through a relative PATH entry, which would make the executed "
                      f"program depend on the working directory; it is not used")
    return str(Path(found).resolve()), None


def version_from(first_line, program):
    """The version token from `<program> version <token> Copyright …`, or None when the line is not in
    that form. The token is reported as FFmpeg prints it (release builds print numbers, source builds
    print a git description); it is never guessed or reformatted."""
    match = re.match(rf"^{re.escape(program)} version (\S+) Copyright ", first_line or "")
    if not match:
        return None
    token = match.group(1)
    return token if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,63}", token) else None


def scrub_paths(text, paths):
    """Replace every occurrence of the given local paths (and their `file:` URLs) with placeholders, so a
    tool message can explain a failure without publishing where files live."""
    if not text:
        return text
    for label, path in paths:
        if not path:
            continue
        for form in (local_url(path), str(path), str(Path(path).resolve())):
            text = text.replace(form, label)
    return text
