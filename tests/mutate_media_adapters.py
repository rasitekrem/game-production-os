#!/usr/bin/env python3
"""Bounded mutation harness for the production media adapters (gpos/tools/ffmpeg/, gpos/tools/ffprobe/).

    python3 tests/mutate_media_adapters.py [--jobs N] [--only TEXT]

Each mutation breaks exactly one semantic guarantee of the media adapters in a temporary copy of the
repository and runs tests/test_media_adapters.py — real FFmpeg, real ffprobe — there. A mutation must
make the suite fail ("CAUGHT"); one that leaves it green is "MISSED" and fails this harness. An anchor
that does not match exactly once is "NOT APPLIED" and also fails, so the list cannot rot.

A mutation is a list of (file, anchor, replacement) edits. Where a guarantee is defended in two places
(for example, a missing capture context is refused by name *and* can never match a registry pair), a
realistic bug that defeats it has to remove both, so it is written as one mutation with several edits.
Mutations 1-28 are the ones the Phase 2C-2 brief requires; the rest defend further guarantees.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG, FFPROBE, PARSER = "gpos/tools/ffmpeg/adapter.py", "gpos/tools/ffprobe/adapter.py", "gpos/tools/ffprobe/parser.py"
COMMON, REGISTRY = "gpos/tools/media_common.py", "gpos/tools/registry.py"

PRODUCTION = "(FfmpegAdapter(), FfprobeAdapter(), GitAdapter())"
RESTRICTIONS = 'INPUT_RESTRICTIONS = ("-protocol_whitelist", ALLOWED_PROTOCOLS, "-format_whitelist", ",".join(SAFE_DEMUXERS))'
CANDIDATE_CONTEXT = "            evidence_type=evidence_type, capture_context=source.origin_capture_context,"
FRAME_KINDS = '               ("timestamp_seconds",), "IMAGE", "VISUAL_EVIDENCE", VISUAL_CONTEXTS,'
INSPECT_SPEC = "        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=inspect_argv(source.absolute_path),"
TRANSFORM_SPEC = "        spec = proc.ToolProcessSpec(executable=context.probe.tool_path, argv=argv, cwd=str(workspace),"
NO_CONTEXT = "        if source.origin_capture_context is None:"
PAIR_CHECK = "        if (evidence_type, source.origin_capture_context) not in context.capability.evidence_pairs():"


def offline_for(kind):
    return (FFMPEG, CANDIDATE_CONTEXT,
            f"            evidence_type=evidence_type, capture_context=(context.capability.execution_context "
            f"if kind == {kind!r} else source.origin_capture_context),")


MUTATIONS = [
    ("1 production registry forgets ffprobe", [
        (REGISTRY, PRODUCTION, "(FfmpegAdapter(), GitAdapter())")]),
    ("2 production registry forgets ffmpeg", [
        (REGISTRY, PRODUCTION, "(FfprobeAdapter(), GitAdapter())")]),
    ("3 TEST_ONLY synthetic enters the production registry", [
        (REGISTRY, "        registry.register(adapter)\n    return registry",
         "        registry.register(adapter)\n    registry.allow_test_only = True\n"
         "    from .synthetic import SyntheticAdapter\n    registry.register(SyntheticAdapter())\n    return registry")]),
    ("4 protocol whitelist removed", [
        (COMMON, RESTRICTIONS, 'INPUT_RESTRICTIONS = ("-format_whitelist", ",".join(SAFE_DEMUXERS))')]),
    ("5 network protocol added to the whitelist", [
        (COMMON, 'ALLOWED_PROTOCOLS = "file"', 'ALLOWED_PROTOCOLS = "file,http,tcp"')]),
    ("6 format whitelist removed", [
        (COMMON, RESTRICTIONS, 'INPUT_RESTRICTIONS = ("-protocol_whitelist", ALLOWED_PROTOCOLS)')]),
    ("7 unsafe playlist demuxer added", [
        (COMMON, 'SAFE_DEMUXERS = ("mov", "matroska",', 'SAFE_DEMUXERS = ("hls", "mov", "matroska",')]),
    ("8 ffprobe parser reads the public redacted text instead of the raw bytes", [
        (FFPROBE, "            summary = parser.parse(outcome.raw_stdout)",
         "            summary = parser.parse(outcome.stdout.encode())")]),
    ("9 ffprobe truncation ignored", [
        (FFPROBE, "        if outcome.truncated:\n            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, "
                  "process=public,\n                                  detail=\"ffprobe output reached",
         "        if False:\n            return AdapterOutcome(ok=False, exit_code=outcome.exit_code, "
         "process=public,\n                                  detail=\"ffprobe output reached")]),
    ("10 ffprobe starts returning metadata tags", [
        (FFPROBE, '            + ("-show_entries", ENTRIES, "-of", "json",',
         '            + ("-show_format", "-show_streams", "-show_entries", ENTRIES, "-of", "json",'),
        (PARSER, "    if not isinstance(fmt, dict) or not set(fmt) <= FORMAT_FIELDS:", "    if not isinstance(fmt, dict):"),
        (PARSER, "    if not isinstance(stream, dict) or not set(stream) <= STREAM_FIELDS:",
         "    if not isinstance(stream, dict):"),
        (PARSER, '        "format_names": format_names.split(","),',
         '        "format_names": format_names.split(","), "tags": fmt.get("tags"),')]),
    ("11 transform accepts a source without a capture context", [
        (FFMPEG, NO_CONTEXT, "        if False:"),
        (FFMPEG, PAIR_CHECK, "        if source.origin_capture_context is not None and (evidence_type, "
                             "source.origin_capture_context) not in context.capability.evidence_pairs():")]),
    ("12 frame candidate claims OFFLINE_ANALYSIS instead of the source context", [offline_for("frame")]),
    ("13 clip candidate claims OFFLINE_ANALYSIS", [offline_for("clip")]),
    ("14 audio candidate claims OFFLINE_ANALYSIS", [offline_for("audio")]),
    ("15 derived_from removed", [
        (FFMPEG, "                                 derived_from=(source.artifact_id,)),) if output.exists() else ()",
         "                                 ),) if output.exists() else ()"),
        (FFMPEG, "            generated_at=context.clock.now(), artifact_ids=(kind,), derived_from=(source.artifact_id,),",
         "            generated_at=context.clock.now(), artifact_ids=(kind,),")]),
    ("16 -n changed to -y", [
        (FFMPEG, 'COMMON = ("-hide_banner", "-loglevel", "error", "-nostdin", "-n")',
         'COMMON = ("-hide_banner", "-loglevel", "error", "-nostdin", "-y")')]),
    ("17 caller output filename introduced", [
        (FFMPEG, FRAME_KINDS, FRAME_KINDS.replace('("timestamp_seconds",)', '("timestamp_seconds", "output_name")')),
        (FFMPEG, "        output = workspace / filename",
         '        output = workspace / str((request.inputs or {}).get("output_name", filename))')]),
    ("18 caller filter reaches argv", [
        (FFMPEG, FRAME_KINDS, FRAME_KINDS.replace('("timestamp_seconds",)', '("timestamp_seconds", "vf")')),
        (FFMPEG, "        described = _described(kind, params, source)",
         "        described = _described(kind, params, source)\n"
         '        if "vf" in (request.inputs or {}):\n'
         '            argv = argv[:-1] + ("-vf", str(request.inputs["vf"]), argv[-1])')]),
    ("19 dry run executes FFmpeg", [
        (FFMPEG, "        if context.dry_run:\n            return AdapterOutcome(plan=",
         "        if context.dry_run and False:\n            return AdapterOutcome(plan=")]),
    ("20 the source file is used as the output", [
        (FFMPEG, "        output = workspace / filename", "        output = Path(source.absolute_path)"),
        (FFMPEG, "        if output.exists() or output.is_symlink():", "        if False:")]),
    ("21 frame evidence becomes RUNTIME_EVIDENCE", [
        (FFMPEG, FRAME_KINDS, FRAME_KINDS.replace("VISUAL_EVIDENCE", "RUNTIME_EVIDENCE")),
        (FFMPEG, 'b"\\x89PNG\\r\\n\\x1a\\n", "VISUAL_EVIDENCE"),', 'b"\\x89PNG\\r\\n\\x1a\\n", "RUNTIME_EVIDENCE"),')]),
    ("22 clip evidence becomes RUNTIME_EVIDENCE", [
        (FFMPEG, '"VIDEO", "MOTION_EVIDENCE", MOTION_CONTEXTS,', '"VIDEO", "RUNTIME_EVIDENCE", MOTION_CONTEXTS,'),
        (FFMPEG, 'b"\\x1a\\x45\\xdf\\xa3", "MOTION_EVIDENCE"),', 'b"\\x1a\\x45\\xdf\\xa3", "RUNTIME_EVIDENCE"),')]),
    ("23 audio evidence becomes HUMAN_EVIDENCE", [
        (FFMPEG, '"AUDIO", "AUDIO_EVIDENCE", AUDIO_CONTEXTS,', '"AUDIO", "HUMAN_EVIDENCE", AUDIO_CONTEXTS,'),
        (FFMPEG, 'b"RIFF", "AUDIO_EVIDENCE"),', 'b"RIFF", "HUMAN_EVIDENCE"),')]),
    ("24 duration limit removed", [
        (FFMPEG, '    ceiling = MAX_CLIP_SECONDS if kind == "clip" else MAX_AUDIO_SECONDS',
         "    ceiling = MAX_TIMESTAMP_SECONDS")]),
    ("25 output size limit removed", [
        (FFMPEG, '"-c:v", "png", "-f", "image2pipe", "-fs", str(size_limit),', '"-c:v", "png", "-f", "image2pipe",'),
        (FFMPEG, '"-f", "matroska", "-fs", str(size_limit),', '"-f", "matroska",'),
        (FFMPEG, '"-c:a", "pcm_s16le", "-f", "wav", "-fs", str(size_limit),', '"-c:a", "pcm_s16le", "-f", "wav",'),
        (FFMPEG, "    if size >= size_limit:", "    if False:")]),
    ("26 partial output offered as evidence", [
        (FFMPEG, "        if defect:\n", '        if defect and "size limit" not in defect:\n')]),
    ("27 the ffmpeg adapter imports subprocess", [
        (FFMPEG, "import math\nimport re\n", "import math\nimport re\nimport subprocess\n")]),
    ("28 the ffmpeg executable becomes caller-controlled", [
        (FFMPEG, FRAME_KINDS, FRAME_KINDS.replace('("timestamp_seconds",)', '("timestamp_seconds", "executable")')),
        (FFMPEG, TRANSFORM_SPEC,
         '        spec = proc.ToolProcessSpec(executable=(request.inputs or {}).get("executable", '
         'context.probe.tool_path), argv=argv, cwd=str(workspace),')]),
    # --- further guarantees the suite defends
    ("28b the ffprobe executable becomes caller-controlled", [
        (FFPROBE, "        timeout=TimeoutPolicy(default=60.0, maximum=300.0),",
         '        timeout=TimeoutPolicy(default=60.0, maximum=300.0), input_kinds=("executable",),'),
        (FFPROBE, INSPECT_SPEC,
         '        spec = proc.ToolProcessSpec(executable=(request.inputs or {}).get("executable", '
         'context.probe.tool_path), argv=inspect_argv(source.absolute_path),')]),
    ("the ffprobe adapter imports subprocess", [
        (FFPROBE, "import shutil\nimport tempfile\n", "import shutil\nimport subprocess\nimport tempfile\n")]),
    ("existing output no longer refused (-n exits 0)", [
        (FFMPEG, "        if output.exists() or output.is_symlink():", "        if False:")]),
    ("output signature not checked", [
        (FFMPEG, "    if not head.startswith(signature) or (kind == \"audio\" and head[8:12] != b\"WAVE\"):",
         "    if False:")]),
    ("size-limit check after the run removed", [
        (FFMPEG, "    if size >= size_limit:", "    if False:")]),
    ("source metadata kept in derived files", [
        (FFMPEG, 'CLEAN_OUTPUT = ("-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact")',
         'CLEAN_OUTPUT = ("-fflags", "+bitexact")')]),
    ("frame written with image2 (ignores -n)", [
        (FFMPEG, '"-c:v", "png", "-f", "image2pipe",', '"-c:v", "png", "-f", "image2", "-update", "1",')]),
    ("incompatible context no longer refused (pair check removed)", [
        (FFMPEG, PAIR_CHECK, "        if False:")]),
    ("exactly-one-input check becomes first-one-wins", [
        (FFMPEG, "        if len(inputs) != 1:\n            return _refuse(cap,", "        if not inputs:\n            return _refuse(cap,")]),
    ("ffprobe exactly-one-input check removed", [
        (FFPROBE, "        if len(inputs) != 1:", "        if not inputs:")]),
    ("output may be written beside the source", [
        (FFMPEG, "        if workspace.resolve() == Path(source.absolute_path).resolve().parent:", "        if False:")]),
    ("process payload reaches the result", [
        (FFMPEG, "        public = _without_output(outcome)", "        public = outcome"),
        (FFPROBE, "        public = _without_output(outcome)", "        public = outcome")]),
    ("ffprobe failure reason keeps the input path", [
        (FFPROBE, '    return media.scrub_paths(line, ((f"<input {source.artifact_id}>", source.absolute_path),))[:300]',
         "    return line[:300]")]),
    ("recorded command keeps the input path", [
        (FFMPEG, "    return replace(spec, argv=tuple(media.scrub_paths(a, paths) for a in spec.argv)).command_for_provenance()",
         "    return spec.command_for_provenance()"),
        (FFPROBE, "    return replace(spec, argv=tuple(media.scrub_paths(a, placeholder) for a in spec.argv)).command_for_provenance()",
         "    return spec.command_for_provenance()")]),
    ("caller number text forwarded instead of the canonical form", [
        (FFMPEG, '    return format(abs(number), "f"), None', "    return str(value), None")]),
    ("non-ASCII digits accepted", [
        (FFMPEG, 'SECONDS_TEXT = re.compile(r"^[0-9]{1,6}(?:\\.[0-9]{1,6})?$")',
         'SECONDS_TEXT = re.compile(r"^\\d{1,6}(?:\\.\\d{1,6})?$")')]),
    ("encoder and muxer probe skipped", [
        (FFMPEG, "        missing = self._missing_components(run)", "        missing = []")]),
    ("encoder presence read from the exit code only", [
        (FFMPEG, "            if outcome.exit_code != 0 or not first or not first[0].startswith(HELP_HEADER[topic]):",
         "            if outcome.exit_code != 0:")]),
    ("a partial write is reported as no mutation", [
        (FFMPEG, "        record[\"mutation_performed\"] = bool(produced)\n", "        record[\"mutation_performed\"] = False\n"),
        (FFMPEG, "evidence=(candidate,),\n                              **record)",
         "evidence=(candidate,),\n                              **dict(record, mutation_performed=True))")]),
    ("parser stream limit removed", [
        (PARSER, "    if len(streams) > MAX_STREAMS:", "    if False:")]),
    ("parser accepts a trailing newline in numbers", [
        (PARSER, "not DECIMAL.fullmatch(value)", "not DECIMAL.match(value)"),
        (PARSER, 'DECIMAL = re.compile(r"^[0-9]{1,12}(?:\\.[0-9]{1,9})?$")', 'DECIMAL = re.compile(r"^[0-9]{1,12}(?:\\.[0-9]{1,9})?")')]),
]


def run(mutation):
    name, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-mediamut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for rel, anchor, replacement in edits:
            path = copy / rel
            text = path.read_text()
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            path.write_text(text.replace(anchor, replacement))
        out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_media_adapters.py")],
                             capture_output=True, text=True, timeout=900,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    args = parser.parse_args()
    selected = [m for m in MUTATIONS if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
