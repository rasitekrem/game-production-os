"""Capture validation and the capture record (alpha.22).

The helper validates its own output natively (the PNG's size, AVFoundation's tracks, duration and size); GPOS then
validates the published file again, independently and bounded, before it may become an artifact:

* PNG — signature, every chunk's CRC, an IHDR of 8-bit RGB or RGBA with exactly the reported size (longest edge at most
  1920), only known critical chunks, IEND last with nothing after it, and IDAT inflating to exactly the byte count the
  size implies, with a valid filter byte on every row;
* MP4 — a bounded box walk: `ftyp` first, the top-level boxes tiling the file exactly, one complete `moov` and at least
  one `mdat`; exactly one track, a `vide` handler with an `avc1` sample entry, no sound track, the reported width and
  height, at least one sample per second and a movie duration within [d - 0.5, d + 0.75] seconds.

FFprobe and FFmpeg are never run here: an explicit later workflow may inspect or derive from a published runtime video
as separate tool results.
"""

import os
import stat
import struct
import zlib

from . import contract as c


class MediaProblem(Exception):
    pass


def _regular(path, bound):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise MediaProblem(f"{os.path.basename(path)} was not published") from None
    if not stat.S_ISREG(st.st_mode):
        raise MediaProblem(f"{os.path.basename(path)} is not a regular file")
    if st.st_size > bound:
        raise MediaProblem(f"{os.path.basename(path)} is larger than {bound} bytes")
    return st.st_size


# ---------------------------------------------------------------- PNG

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CRITICAL = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}


def validate_png(path, width, height, max_edge=c.MAX_EDGE_PX, bound=c.MAX_PNG_BYTES):
    """{width, height, bytes} of a strictly valid 8-bit RGB/RGBA PNG of exactly this size; MediaProblem otherwise."""
    size = _regular(path, bound)
    with open(path, "rb") as fh:
        data = fh.read(bound + 1)
    if data[:8] != PNG_SIGNATURE:
        raise MediaProblem("not a PNG signature")
    pos, ihdr, idat, ended = 8, None, [], False
    while pos < len(data):
        if pos + 12 > len(data):
            raise MediaProblem("a truncated PNG chunk")
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length or pos + 12 + length > len(data):
            raise MediaProblem("a truncated PNG chunk")
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise MediaProblem(f"PNG chunk {kind!r} fails its CRC")
        if ihdr is None and kind != b"IHDR":
            raise MediaProblem("the first PNG chunk is not IHDR")
        if kind[0:1].isupper() and kind not in CRITICAL:
            raise MediaProblem(f"unknown critical PNG chunk {kind!r}")
        if kind == b"IHDR":
            if ihdr is not None or length != 13:
                raise MediaProblem("a malformed or repeated IHDR")
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            ended = True
            pos += 12 + length
            break
        pos += 12 + length
    if not ended or pos != len(data):
        raise MediaProblem("IEND is missing or followed by more bytes")
    w, h, depth, colour, compression, filtering, interlace = ihdr
    if (w, h) != (width, height) or max(w, h) > max_edge or min(w, h) < 1:
        raise MediaProblem(f"the PNG is {w}x{h}, not the reported {width}x{height} within {max_edge} px")
    if depth != 8 or colour not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
        raise MediaProblem("the PNG is not 8-bit, non-interlaced RGB or RGBA")
    channels = 3 if colour == 2 else 4
    row = 1 + w * channels
    expected = row * h
    raw = zlib.decompressobj()
    out = raw.decompress(b"".join(idat), expected + 1)
    if len(out) != expected or not raw.eof or raw.unconsumed_tail:
        raise MediaProblem("the PNG image data does not inflate to exactly its size")
    if any(out[i * row] > 4 for i in range(h)):
        raise MediaProblem("a PNG row has an invalid filter")
    return {"width": w, "height": h, "bytes": size, "channels": channels}


# ---------------------------------------------------------------- MP4

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}
MAX_BOXES, MAX_DEPTH = 4096, 8


def _boxes(data, start, end, depth, counter):
    """[(type, body_start, body_end)] of the boxes tiling data[start:end] exactly."""
    if depth > MAX_DEPTH:
        raise MediaProblem("MP4 boxes nest too deeply")
    out, pos = [], start
    while pos < end:
        counter[0] += 1
        if counter[0] > MAX_BOXES:
            raise MediaProblem("too many MP4 boxes")
        if pos + 8 > end:
            raise MediaProblem("a truncated MP4 box header")
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                raise MediaProblem("a truncated MP4 large-size header")
            (size,) = struct.unpack(">Q", data[pos + 8:pos + 16])
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise MediaProblem(f"MP4 box {kind!r} does not fit its parent")
        out.append((kind, pos + header, pos + size))
        pos += size
    return out


def _children(data, box, counter, depth):
    return _boxes(data, box[1], box[2], depth, counter)


def _find(boxes, kind):
    return [b for b in boxes if b[0] == kind]


def validate_mp4(path, duration, width, height, bound=c.MAX_MP4_BYTES):
    """{width, height, bytes, duration_s, samples} of a structurally valid, silent single-track H.264 MP4."""
    size = _regular(path, bound)
    with open(path, "rb") as fh:
        data = fh.read(bound + 1)
    counter = [0]
    top = _boxes(data, 0, len(data), 0, counter)
    if not top or top[0][0] != b"ftyp":
        raise MediaProblem("the MP4 does not start with ftyp")
    moovs, mdats = _find(top, b"moov"), _find(top, b"mdat")
    if len(moovs) != 1 or not mdats:
        raise MediaProblem("the MP4 has no single complete moov or no mdat (an unfinished recording)")
    moov = _children(data, moovs[0], counter, 1)
    mvhd = _find(moov, b"mvhd")
    if len(mvhd) != 1:
        raise MediaProblem("the MP4 has no movie header")
    body = data[mvhd[0][1]:mvhd[0][2]]
    if body[:1] == b"\x01":
        timescale, length = struct.unpack(">IQ", body[20:32])
    else:
        timescale, length = struct.unpack(">II", body[12:20])
    if timescale == 0:
        raise MediaProblem("the MP4 movie timescale is zero")
    seconds = length / timescale
    traks = _find(moov, b"trak")
    if len(traks) != 1:
        raise MediaProblem(f"the MP4 has {len(traks)} tracks, not exactly one video track")
    trak = _children(data, traks[0], counter, 2)
    tkhd = _find(trak, b"tkhd")
    mdia = _find(trak, b"mdia")
    if len(tkhd) != 1 or len(mdia) != 1:
        raise MediaProblem("the MP4 track has no header or media")
    tk = data[tkhd[0][1]:tkhd[0][2]]
    tw, th = struct.unpack(">II", tk[-8:])
    mdia = _children(data, mdia[0], counter, 3)
    hdlr = _find(mdia, b"hdlr")
    if len(hdlr) != 1 or data[hdlr[0][1] + 8:hdlr[0][1] + 12] != b"vide":
        raise MediaProblem("the MP4 track is not a video track (no sound track is allowed)")
    minf = _find(mdia, b"minf")
    stbl = _find(_children(data, minf[0], counter, 4), b"stbl") if len(minf) == 1 else []
    if len(stbl) != 1:
        raise MediaProblem("the MP4 track has no sample table")
    stbl = _children(data, stbl[0], counter, 5)
    stsd, stsz = _find(stbl, b"stsd"), _find(stbl, b"stsz")
    if len(stsd) != 1 or len(stsz) != 1:
        raise MediaProblem("the MP4 sample table is incomplete")
    if data[stsd[0][1] + 12:stsd[0][1] + 16] != b"avc1":
        raise MediaProblem("the MP4 video is not H.264 (avc1)")
    (samples,) = struct.unpack(">I", data[stsz[0][1] + 8:stsz[0][1] + 12])
    if (tw >> 16, th >> 16) != (width, height) or max(width, height) > c.MAX_EDGE_PX:
        raise MediaProblem(f"the MP4 is {tw >> 16}x{th >> 16}, not the reported {width}x{height}")
    if not (duration - 0.5 <= seconds <= duration + 0.75):
        raise MediaProblem(f"the MP4 lasts {seconds:.3f} s, not about {duration} s")
    if samples < duration:
        raise MediaProblem(f"the MP4 holds {samples} frame(s) for {duration} s")
    return {"width": width, "height": height, "bytes": size, "duration_s": round(seconds, 3), "samples": samples}
