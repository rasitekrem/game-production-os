# Media adapters: ffprobe and FFmpeg (Phase 2C-2)

Code: [`gpos/tools/ffprobe/`](../gpos/tools/ffprobe/__init__.py) (adapter id `ffprobe`) and [`gpos/tools/ffmpeg/`](../gpos/tools/ffmpeg/__init__.py) (adapter id `ffmpeg`), with the constants both must agree on in [`gpos/tools/media_common.py`](../gpos/tools/media_common.py). Status: production tool adapters built on the frozen [tool adapter foundation](adapter-foundation.md) (`v1.0.0-alpha.11`) without changing it.

These adapters turn a local recording that a caller already has into reviewable evidence. ffprobe summarizes a media file. FFmpeg extracts a still frame, a short review clip or an audio segment from it. They capture nothing themselves: no screen, game, device, microphone or live input, and no network.

**Media processing does not upgrade capture authority.** A frame taken from a recording captured in `TARGET_RUNTIME` is `TARGET_RUNTIME` evidence, even though FFmpeg ran offline.

## Two adapters, one per executable

The frozen foundation records one tool path and one tool version in an execution's provenance. A single adapter driving both `ffprobe` and `ffmpeg` would make that record ambiguous, so each executable has its own adapter:

| | `ffprobe` | `ffmpeg` |
|---|---|---|
| executable | `ffprobe` | `ffmpeg` |
| `target_tool` | ffprobe | FFmpeg |
| purpose | inspect: a bounded media summary | derive: frame, clip or audio artifacts and evidence candidates |
| `tool_family` | `MEDIA` | `MEDIA` |
| `adapter_kind` | `CLI` | `CLI` |
| `state_model` | `STATELESS` | `STATELESS` |
| platforms | `WINDOWS`, `MACOS`, `LINUX` | `WINDOWS`, `MACOS`, `LINUX` |
| network | `FORBIDDEN` | `FORBIDDEN` |
| minimum version | none (see below) | none (see below) |
| TEST_ONLY | no | no |

`default_registry()` now contains exactly `ffmpeg`, `ffprobe` and `git`. Neither media adapter is an agent adapter, and neither appears in the registry's `adapter_ids`. The TEST_ONLY synthetic adapter never enters the production registry. Neither adapter calls the other, and neither calls Git.

## Verified FFmpeg behaviour

The first-party documentation was consulted on 2026-09-23:

| Document | What it establishes |
|---|---|
| [ffmpeg-protocols](https://ffmpeg.org/ffmpeg-protocols.html) | "When you configure your FFmpeg build, all the supported protocols are enabled by default." `protocol_whitelist` sets the list of allowed protocols. Nested protocols are restricted to a per-protocol subset. The `file:` URL syntax. |
| [ffmpeg-formats](https://ffmpeg.org/ffmpeg-formats.html) | `format_whitelist`: a list of allowed demuxers, where "by default all are allowed". The `bitexact` flag writes only "platform-, build- and time-independent data". The mov demuxer's external-track loading (`enable_drefs`) is disabled by default. The concat demuxer's `safe` option. |
| [ffprobe](https://ffmpeg.org/ffprobe.html) | `-version`, `-v`, the JSON writer, `-show_format`, `-show_streams`, and `-show_entries` syntax, under which tags are shown only when requested. A non-media or unopenable input gives a positive exit code. |
| [ffmpeg](https://ffmpeg.org/ffmpeg.html) | `-version`, `-n` ("do not overwrite output files"), `-y`, `-nostdin`, input `-ss` and `-accurate_seek` (the default while transcoding, where the segment before the position is decoded and discarded), `-t`, `-frames:v`, `-an`/`-vn`/`-sn`/`-dn`, `-f`, `-fs` ("the size of the output file is slightly more than the requested file size"), `-hide_banner`, `-loglevel`, and `-h encoder=<name>` / `-h muxer=<name>`. |
| [ffmpeg-codecs](https://ffmpeg.org/ffmpeg-codecs.html) | The FFV1 and PNG encoders. |

The fetched pages did not document the optional-map suffix `?`, `-map_metadata -1`, `-map_chapters -1` or the PCM encoders. Each of those was verified by running the installed FFmpeg, as was every command shape below. The real tests ran against **FFmpeg 9.0.1 and ffprobe 9.0.1** (Homebrew, macOS).

The installed tools showed behaviours that matter for correctness. Each one is handled in code rather than trusted:

| Observed with the real tool | Consequence |
|---|---|
| `ffmpeg -h encoder=X` exits 0 whether or not `X` exists | presence is read from the header line (`Encoder png [`), never from the exit code |
| `-show_format` / `-show_streams` print every field **including the source's metadata tags** (titles, comments) | inspection uses `-show_entries` alone, which prints only the named fields |
| `-n` refuses to overwrite an existing output but still **exits 0** | an existing output file is refused before FFmpeg runs |
| the `image2` muxer (even with `-update 1`) opens the file itself and **ignores `-n`** (it overwrote) | frames are written with `image2pipe` to a normal file output, where `-n` applies and no filename pattern is expanded |
| `-fs` stops writing but **exits 0**, and overshoots by a packet or cluster (a Matroska clip reached 224,841 bytes under a 20,000-byte limit) | an output at or above its limit is treated as cut short: an incomplete artifact, never evidence |
| a frame requested past the end **exits 0 with an empty file** | every output must be non-empty and start with its format's signature |
| a clip window past the end exits 0 with a **shorter** clip | stated in the clip's evidence limitations |
| `-fflags +bitexact` makes frame, clip and audio outputs **byte-identical** across runs | identical requests give identical hashes |
| an unknown option exits non-zero | an FFmpeg too old to know a whitelist option refuses to run rather than ignoring it, so no minimum version is needed |

## Local input contract

Both adapters consume **exactly one** input artifact: a caller-supplied `InputArtifact` that the foundation has already path-checked inside the project and hashed. No input path comes from a request's `inputs`, and no URL is accepted. A URL is not an existing path inside the project, so the foundation refuses it with `UNSAFE_ARTIFACT_PATH` before any process starts.

The input reaches the tool as an explicit `file:` URL, behind two closed whitelists that no caller can change:

- `-protocol_whitelist file`: only the local file protocol, for the input and for anything the input tries to open in turn;
- `-format_whitelist mov,matroska,avi,mpegts,wav,mp3,flac,ogg`: only self-contained media demuxers. Each name matches the installed FFmpeg's demuxer list; `mov` covers MP4/MOV and `matroska` covers WebM.

Playlist, redirecting, sequence, live and device demuxers are not on the list: `hls`, `dash`, `concat`, `image2`, `sdp`, `rtsp`, `lavfi`, device capture. A local file that merely *describes* other media is therefore refused before anything it references is opened, whatever its file extension.

### Network block, proven

A local HTTP server counts requests while ffprobe and FFmpeg read a local HLS playlist that points at it:

| Run | HTTP requests |
|---|---|
| test-code control: protocols `file,http,tcp`, no format whitelist | ≥ 1 (the fixture really reaches the network) |
| format whitelist alone | 0 |
| protocol whitelist `file` alone, `hls` allowed as a format | 0 |
| every adapter capability (both whitelists), the playlist named `.m3u8` or disguised as `.mp4` | **0**, each a structured `FAILED` |
| a concat list pointing at the server | 0 |

Each whitelist blocks the request on its own; together they are defence in depth.

## `ffprobe.inspect`

`INSPECT` · `READ_ONLY` · `STATELESS` · observes `OFFLINE_ANALYSIS` · requires the tool and a valid project · no dry run, no lease, no inputs, no artifacts, no evidence. The input artifact's capture context is optional, because inspecting a file creates no evidence.

The one authorized command:

```
ffprobe -v error
        -protocol_whitelist file -format_whitelist mov,matroska,avi,mpegts,wav,mp3,flac,ogg
        -show_entries format=format_name,duration:stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,sample_rate,channels,channel_layout,duration
        -of json -i file:<validated input path>
```

There is no packet or frame enumeration and no full decode.

ffprobe's JSON is read from the process boundary's **private raw capture**, never from the public redacted text, and is normalized by [`parser.py`](../gpos/tools/ffprobe/parser.py) into:

```json
{
  "source_artifact_id": "gameplay",
  "format_names": ["mov", "mp4", "m4a", "3gp", "3g2", "mj2"],
  "duration_seconds": "3.000000",
  "stream_count": 2, "video_stream_count": 1, "audio_stream_count": 1, "other_stream_count": 0,
  "primary_video": {"index": 0, "codec_name": "mpeg4", "width": 320, "height": 240, "pixel_format": "yuv420p",
                    "average_frame_rate": "30/1", "duration_seconds": "3.000000"},
  "primary_audio": {"index": 1, "codec_name": "aac", "sample_rate_hz": 44100, "channels": 1,
                    "channel_layout": "mono", "duration_seconds": "3.000000"}
}
```

- `primary_video` and `primary_audio` are the first stream of each kind, or `null`.
- Durations and frame rates stay decimal or rational **strings**, so no binary-float rounding is introduced.
- An unknown duration (`N/A`) or frame rate (`0/0`) becomes `null`.
- The parser trusts nothing about the shape:
  - every field is type- and range-checked, with ASCII digits only;
  - codec, pixel-format and layout names are short safe tokens;
  - more than 64 streams, an unexpected key (a tag, a packet list), malformed JSON, an out-of-range number, truncated output or corrupted output is a `FAILED` result, never a partial summary.
- The raw JSON, tags and paths never reach the result.

## FFmpeg transforms

All three are `TRANSFORM` · `MUTATING` (explicit `allow_mutation` consent, else `MUTATION_NOT_ALLOWED` with nothing started) · `STATELESS` · observe `OFFLINE_ANALYSIS` · require the tool and a valid project · support dry run · take no lease.

| Capability | Inputs | Output | Artifact | Evidence offered |
|---|---|---|---|---|
| `ffmpeg.extract-frame` | `timestamp_seconds` | `frame.png`, PNG | `frame`, `IMAGE`, `image/png` | `VISUAL_EVIDENCE` |
| `ffmpeg.extract-clip` | `start_seconds`, `duration_seconds` | `clip.mkv`, Matroska, FFV1 video plus the first audio stream (if any) as 16-bit PCM | `clip`, `VIDEO`, `video/x-matroska` | `MOTION_EVIDENCE` only (audio in a clip is not audio evidence) |
| `ffmpeg.extract-audio` | `start_seconds`, `duration_seconds` | `audio.wav`, WAV, 16-bit PCM of the first audio stream | `audio`, `AUDIO`, `audio/wav` | `AUDIO_EVIDENCE` |

The clip is transcoded, never stream-copied, so its requested start never silently becomes an earlier keyframe.

### Before FFmpeg starts

Each transform refuses with `INVALID_TOOL_REQUEST`, in a real run and in a dry run alike, when:

1. there is not exactly one input artifact (there is no "first one wins");
2. the input artifact has **no capture context**. A derived artifact inherits where its source was captured, so an unknown source never silently becomes `OFFLINE_ANALYSIS`;
3. the source's context is not one the frozen registry's `evidence_context_compatibility` allows for the evidence type the transform produces. The transform never substitutes a different evidence type:

   | Evidence | Source contexts admitted |
   |---|---|
   | `VISUAL_EVIDENCE` (frame), `MOTION_EVIDENCE` (clip) | `DCC_RENDER`, `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `AUTOMATED_TEST` |
   | `AUDIO_EVIDENCE` (audio) | `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `AUTOMATED_TEST` |

   `PERFORMANCE_RUNTIME`, `OFFLINE_ANALYSIS` and every other context are refused. For audio, so is `DCC_RENDER`. The declared pairs equal the registry's sets exactly (tested), and the registry was not changed.
4. a number is not a plain, finite, non-negative decimal within bounds. Accepted forms are an integer, a finite float, a Decimal, or text such as `12` or `12.5` with at most 6 decimals and ASCII digits only. Refused: NaN, infinity, negatives, booleans, exponents, signs, whitespace, trailing newlines, non-ASCII digits, and values beyond the bound. The adapter forwards only its **own canonical rendering** at microsecond precision (`1.5` → `1.500000`), never the caller's text;
5. the output directory is the source's own directory, since derived media is never written beside its source;
6. the output file (or a symlink) already exists, as `-n` alone would exit 0 (see above). This is checked in a dry run too.

Any other request input, such as a codec, filter, map, format, output name, option or executable, is not a declared input kind and is refused by the foundation.

### Bounds

| Bound | Value |
|---|---|
| `timestamp_seconds` / `start_seconds` | 0 – 86 400 s |
| clip `duration_seconds` | > 0 – **30 s** |
| audio `duration_seconds` | > 0 – 120 s |
| output size (`-fs`) | frame 64 MiB · clip 4 GiB · audio 256 MiB |
| timeout (default / maximum) | frame 120 / 600 s · clip 600 / 1800 s · audio 300 / 1200 s |

The clip ceiling is lower than the 120 s the brief allowed. Lossless FFV1 at 1080p60 measured 6.6–139 MB/s here, so 30 s already approaches the 4 GiB size cap in the worst case. Captured process output is bounded by the foundation.

### The authorized FFmpeg commands

The only variable slots are the validated input path, the adapter-chosen output path in this execution's workspace, and the adapter's canonical numbers:

```
common:  ffmpeg -hide_banner -loglevel error -nostdin -n
                -protocol_whitelist file -format_whitelist mov,matroska,avi,mpegts,wav,mp3,flac,ogg

frame:   <common> -ss <T> -accurate_seek -i file:<input>
         -map 0:v:0 -frames:v 1 -an -sn -dn
         -map_metadata -1 -map_chapters -1 -fflags +bitexact
         -c:v png -f image2pipe -fs 67108864 file:<workspace>/frame.png

clip:    <common> -ss <S> -accurate_seek -i file:<input> -t <D>
         -map 0:v:0 -map 0:a:0? -sn -dn
         -map_metadata -1 -map_chapters -1 -fflags +bitexact
         -c:v ffv1 -c:a pcm_s16le -f matroska -fs 4294967296 file:<workspace>/clip.mkv

audio:   <common> -ss <S> -accurate_seek -i file:<input> -t <D>
         -map 0:a:0 -vn -sn -dn
         -map_metadata -1 -map_chapters -1 -fflags +bitexact
         -c:a pcm_s16le -f wav -fs 268435456 file:<workspace>/audio.wav
```

The adapters use `-n` everywhere and never `-y`. There is no filter, filtergraph, caller codec, caller map, caller format, caller protocol or generic command capability.

### Probe

The ffprobe and FFmpeg probes each resolve only their own executable:

- `shutil.which` looks it up on PATH;
- a match found only through a relative PATH entry is refused;
- the match is resolved to an absolute path.

Each probe runs `-version` through the audited process boundary. The version is taken only from a first line of the form `<program> version <token> Copyright`; anything else is `VERSION_UNSUPPORTED`, never a guessed version.

The FFmpeg probe then confirms that this build provides the fixed output contract: the `png`, `ffv1` and `pcm_s16le` encoders, and the `image2pipe`, `matroska` and `wav` muxers, each via `-hide_banner -h encoder=<name>` / `-h muxer=<name>`. A build that lacks any of them is `VERSION_UNSUPPORTED` (execution: `INCOMPATIBLE`), so the gap is found at probe time rather than halfway through a production evidence run.

### After FFmpeg finishes

| Outcome | Status | Artifact | Evidence |
|---|---|---|---|
| success, output non-empty, correct signature (PNG, EBML, RIFF/WAVE), under the size limit | `SUCCESS` | complete, `DERIVED` | one candidate |
| output at or over the size limit | `FAILED` | kept, **incomplete** (`ARTIFACT_INCOMPLETE`) | none |
| empty output, e.g. a frame past the end | `FAILED` | kept, incomplete | none |
| wrong signature | `FAILED` | kept, incomplete | none |
| non-zero exit after a partial write | `FAILED` | kept, incomplete | none |
| timeout mid-write | `TIMED_OUT` | kept, incomplete | none |
| no output written | `FAILED` | none | none |

A partial file is never deleted silently and never becomes evidence. A run that leaves a file behind reports `mutation_performed` as true, because it wrote one. The existing foundation semantics do this; the adapters add no rollback logic of their own.

## Derived evidence

Every output is a `DERIVED` artifact whose `derived_from` is the input artifact id. The foundation carries the input's capture context into the artifact's `origin_capture_context`, and the evidence candidate states the same context and `derived_from`. The foundation validates both against each other and against the registry.

Every candidate carries at least one limitation:

- it is derived from the named input and is not an independent capture;
- a frame shows one instant only;
- a clip or audio segment may be shorter than requested if the source ends first.

No media capability can offer `RUNTIME_EVIDENCE`, `DEVICE_EVIDENCE`, `PERFORMANCE_EVIDENCE` or `HUMAN_EVIDENCE`.

### Materialization

When the request's subject names an exact revision, each candidate is materializable. `gpos.tools.evidence.materialize()` then produces a schema-valid record whose provenance carries:

- the inherited `capture_context`;
- `subject_revision`;
- `build_revision` and `target_platform` when the caller supplied them. The evidence schema requires both for `TARGET_RUNTIME`;
- FFmpeg's `tool_version`;
- the output artifact's hash.

For a source captured in `DCC_RENDER` or `EDITOR`, the schema's required tool version is FFmpeg's. It names the tool that derived the file, not the tool that rendered the source; the limitation says so. No gate status, verdict, reviewer or Human Review is created, and nothing is written into `.game/gpos/`.

### Explicit Git handoff

No media adapter calls Git or infers a revision:

1. call `git.resolve-provenance`;
2. pass its `repository_revision` as `build_revision` in the media request;
3. the provenance records exactly that value.

Without the handoff, `build_revision` stays unknown.

The same workflow runs entirely from the CLI:

```bash
python3 -m gpos.tools execute --adapter git --capability git.resolve-provenance \
    --project P --subject-ref T-1 --format json
python3 -m gpos.tools execute --adapter ffmpeg --capability ffmpeg.extract-frame \
    --project P --subject-ref T-1 --subject-revision <SHA> --build-revision <SHA> --target-platform MACOS \
    --input-artifact gameplay=P/captures/gameplay.mp4 --input-artifact-context gameplay=TARGET_RUNTIME \
    --input timestamp_seconds=1.5 --allow-mutation --format json
```

- The first command's JSON result contains the `repository_revision` used as `<SHA>` in the second.
- The second command's result provenance and its candidate carry exactly that `build_revision` and `target_platform`, and the candidate is materializable.
- `materialize()` stays a library call, because it is deliberately not a CLI write operation. The tests materialize the CLI's own candidate.
- `--build-id` and `--device` are carried the same way. An omitted option stays unknown. An invalid platform or an empty value is refused before FFmpeg starts.

## Dry run

A dry run validates the request, the input artifact, its capture context and the numbers, and returns a plan:

```
would write frame.png (IMAGE) derived from input artifact 'gameplay': frame at 1.500000 s
would offer VISUAL_EVIDENCE captured in TARGET_RUNTIME
no FFmpeg process, workspace or file is created by a dry run
```

It starts no process and creates no workspace, output or evidence. A plan never succeeds where the real run would be refused: an output file or symlink already at the output path, for example in a caller-named output directory, is refused in the dry run too. The check only looks; it creates nothing and runs nothing. It performs no mutation and never claims that an output exists.

## Secrecy

- Outputs carry **no source metadata or chapters** (`-map_metadata -1 -map_chapters -1`). Tests search the derived bytes and the tags of the derived files for the source's title and comment.
- FFmpeg's and ffprobe's captured output is blanked before the process outcome reaches the foundation. Exit code, byte counts, truncation and redaction counts are kept.
- A failure reason is FFmpeg's first error line, with the input path replaced by `<input ID>` and the workspace by `<workspace>`.
- The recorded command in provenance uses the same placeholders. Paths are replaced *before* the boundary's redaction runs, so a credential-shaped file name cannot defeat the match.
- The CLI's JSON output echoes the request through the same redaction as the result, so a credential-shaped input file name is not printed back.

A credential-shaped file name and credential-shaped source metadata were tested against every public surface: data, stdout and stderr, diagnostics, provenance, evidence summaries and limitations, artifact descriptions, and CLI JSON and text. Neither appears.

## Results and diagnostics

| Situation | Status | Code |
|---|---|---|
| inspected / derived | `SUCCESS` | — |
| no or several input artifacts, no capture context, incompatible context, bad number, output exists, output beside the source | `INVALID_REQUEST` | `INVALID_TOOL_REQUEST` |
| undeclared input (codec, filter, output name, …) | `INVALID_REQUEST` | `INVALID_TOOL_REQUEST` |
| transform without consent | `INVALID_REQUEST` | `MUTATION_NOT_ALLOWED` |
| URL or path outside the project | `INVALID_REQUEST` | `UNSAFE_ARTIFACT_PATH` |
| non-media input, refused demuxer, missing stream, bad or partial output | `FAILED` | `EXECUTION_FAILED` (+ `ARTIFACT_INCOMPLETE`) |
| timeout | `TIMED_OUT` | `EXECUTION_TIMEOUT` (+ `ARTIFACT_INCOMPLETE`) |
| executable missing or only on a relative PATH entry | `UNAVAILABLE` | `TOOL_NOT_FOUND` |
| unrecognized version output, or a required encoder or muxer missing | `INCOMPATIBLE` | `TOOL_VERSION_UNSUPPORTED` |

## Security review

| Concern | Finding |
|---|---|
| subprocess outside the boundary / shell | none: `gpos/tools/process.py` remains the only importer; argument vectors only |
| arbitrary FFmpeg options, filtergraphs, codecs, maps, formats | none: fixed templates; undeclared inputs are refused by the foundation |
| caller executable | none: each probe resolves its own executable; relative PATH entries are refused |
| caller output filename | none: `frame.png`, `clip.mkv`, `audio.wav` in this execution's workspace |
| overwrite | never: `-n`, plus a pre-check, because `-n` exits 0 |
| network protocols; playlist, live or device demuxers | none: closed protocol and format whitelists, each proven to block a real request on its own |
| mov external references | not enabled: `enable_drefs` stays at its default (off), and only `file` is allowed anyway |
| path leakage | scrubbed from reasons and recorded commands; FFmpeg's output never reaches the result |
| metadata leakage | tags are never requested, and are stripped from outputs |
| raw bytes in the result | none: raw capture is parsed, then blanked |
| source modification | none: the source is hashed before and after every capability, and nothing is written beside it |
| evidence-context upgrade | impossible: the context is inherited and validated by the foundation; `OFFLINE_ANALYSIS` is never claimed |
| runtime, device, performance or human evidence fabrication | none can be offered |
| partial output becoming evidence | never: size, signature, emptiness, exit and timeout checks; incomplete artifacts are withheld |
| unbounded duration or output growth | bounded: numeric ceilings, `-fs`, the post-run size check, timeouts, the capture bound |

**No OS sandboxing is claimed.** FFmpeg and ffprobe are external native parsers and decoders, and they run under the same foundation trust boundary as every other tool: fixed argument vectors, an allowlisted environment, a validated working directory, bounded output and a timeout. A malicious media file is still parsed by native code.

## Limitations

- Local, self-contained files only; no capture of any kind.
- Only the first video stream and the first audio stream are used.
- The clip's audio is not audio evidence; use `ffmpeg.extract-audio`.
- FFmpeg's `tool_version` in a materialized record names the deriving tool, not the source's capture tool.
- Real-runtime tests ran on macOS with FFmpeg 9.0.1. Windows and Linux are declared but were not exercised here.

## Tests

```bash
python3 tests/test_media_adapters.py
python3 tests/mutate_media_adapters.py
```

The suite generates its fixture media with the installed FFmpeg: a test pattern plus a sine tone, with credential-shaped metadata. It fails with the marker FFMPEG_RUNTIME_UNAVAILABLE_FOR_PHASE2C2, rather than falling back to mocks, if either executable is absent. Stand-in programs are used only where a real FFmpeg cannot be made to behave a certain way on demand: missing, unrecognizable, lacking an encoder, or hanging mid-write.

Groups A–Z cover:

- registration and the real probes;
- missing tools;
- inspection and the raw-JSON parser;
- non-media input and the network block;
- frame, clip and audio extraction;
- numeric validation;
- missing and incompatible capture contexts, and derived and DCC contexts;
- mutation consent, dry run, source immutability, output boundaries and no-overwrite;
- partial output and timeout;
- secrecy;
- the explicit Git handoff and materialization;
- the security surface and the CLI.

The mutation harness breaks each guarantee in turn and requires the suite to catch it.
