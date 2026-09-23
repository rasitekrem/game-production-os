#!/usr/bin/env python3
"""Phase 2C-2 — production media adapters (ffprobe, FFmpeg) tests.

    python3 tests/test_media_adapters.py

These are real integration tests. The fixture media are generated with the locally installed FFmpeg
(a test pattern and a sine tone, encoded to small self-contained files), and the adapters under test
drive the same ffprobe and FFmpeg through the audited process boundary. The suite fails, with an
explicit marker, if either executable is missing; it never falls back to mocks. The only stand-ins are
where a test needs a tool that behaves in a way a real FFmpeg cannot be made to on demand (missing,
unrecognizable, lacking an encoder, hanging mid-write) and those say so.

Test code may use FFmpeg and ffprobe freely (to make fixtures, and to check outputs with options
production never uses). The adapters never run through those helpers.

Groups: A registration · B real probes · C missing or unusable tools · D ffprobe inspection · E raw
JSON and the parser · F non-media input · G local network block · H frame extraction · I numeric
validation · J clip extraction · K audio extraction · L input without capture context · M incompatible
capture context · N derived context · O DCC context · P mutation consent · Q dry run · R source
immutability · S output boundaries · T no overwrite · U partial output and timeout · V private raw,
public redacted · W explicit Git handoff · X materialization · Y security surface · Z CLI.
"""

import ast
import hashlib
import http.server
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_HOME = tempfile.mkdtemp(prefix="gpos-media-home-")
os.environ["HOME"] = _HOME  # neither Git (group W) nor FFmpeg reads the user's configuration

from gpos.framework import load_framework  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import evidence as tev  # noqa: E402
from gpos.tools import media_common as media  # noqa: E402
from gpos.tools import model as tmodel  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools import validation as tval  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.ffmpeg import adapter as fa  # noqa: E402
from gpos.tools.ffmpeg import FfmpegAdapter  # noqa: E402
from gpos.tools.ffprobe import adapter as pa  # noqa: E402
from gpos.tools.ffprobe import parser as pp  # noqa: E402
from gpos.tools.ffprobe import FfprobeAdapter  # noqa: E402
from gpos.tools.git import adapter as ga  # noqa: E402
from gpos.tools.model import InputArtifact, Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry, default_registry  # noqa: E402
from gpos.tools.synthetic import SyntheticAdapter  # noqa: E402

FW = load_framework()
REG = FW.registry
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
PROJECT_ID = "synthetic-adapter-project"
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
GIT = shutil.which("git")
REVISION = "0123456789abcdef0123456789abcdef01234567"
SECRET_TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0" + "abcdefghijklmnop"       # credential-shaped, not a credential
SECRET_TITLE = "password=hunter2-title-secret"
TAG_MARKER = "MEDIA-TAG-MARKER-7f3a"                                        # not secret-shaped: tests true absence
TRANSFORMS = (fa.EXTRACT_FRAME, fa.EXTRACT_CLIP, fa.EXTRACT_AUDIO)
PARAMS = {fa.EXTRACT_FRAME: {"timestamp_seconds": 1.5},
          fa.EXTRACT_CLIP: {"start_seconds": "0.5", "duration_seconds": 1},
          fa.EXTRACT_AUDIO: {"start_seconds": 0, "duration_seconds": "1.25"}}
EVIDENCE = {fa.EXTRACT_FRAME: "VISUAL_EVIDENCE", fa.EXTRACT_CLIP: "MOTION_EVIDENCE",
            fa.EXTRACT_AUDIO: "AUDIO_EVIDENCE"}
OUTPUT = {fa.EXTRACT_FRAME: ("frame", "IMAGE", "image/png", "frame.png"),
          fa.EXTRACT_CLIP: ("clip", "VIDEO", "video/x-matroska", "clip.mkv"),
          fa.EXTRACT_AUDIO: ("audio", "AUDIO", "audio/wav", "audio.wav")}
FIXTURE_ENV = {"HOME": _HOME, "PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
               "LC_ALL": "C"}

MEDIA = {}          # fixture name -> path, generated once per run in setUpModule
_MEDIA_DIR = None


# ---------------------------------------------------------------- fixture generation (test code only)

def ffmpeg_fixture(*args):
    """Make fixture media with the real FFmpeg. Test code only: production never takes these paths."""
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                   check=True, capture_output=True)


def ffprobe_json(path, *extra):
    """Inspect an output with ffprobe from test code (including options production never uses)."""
    done = subprocess.run([FFPROBE, "-v", "error", "-of", "json", *extra, "-i", str(path)],
                          check=True, capture_output=True)
    return json.loads(done.stdout)


def setUpModule():
    global _MEDIA_DIR
    if FFMPEG is None or FFPROBE is None:
        raise unittest.SkipTest("FFMPEG_RUNTIME_UNAVAILABLE_FOR_PHASE2C2")
    _MEDIA_DIR = Path(tempfile.mkdtemp(prefix="gpos-media-fixtures-")).resolve()
    video = ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=3"]
    audio = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3"]
    tags = ["-metadata", f"title={SECRET_TITLE}", "-metadata", f"comment=token {SECRET_TOKEN} {TAG_MARKER}"]
    MEDIA["av"] = _MEDIA_DIR / "gameplay.mp4"
    ffmpeg_fixture(*video, *audio, "-c:v", "mpeg4", "-c:a", "aac", "-shortest", *tags, str(MEDIA["av"]))
    MEDIA["video"] = _MEDIA_DIR / "video-only.mkv"
    ffmpeg_fixture(*video, "-c:v", "mpeg4", *tags, str(MEDIA["video"]))
    MEDIA["audio"] = _MEDIA_DIR / "tone.wav"
    ffmpeg_fixture(*audio, "-c:a", "pcm_s16le", str(MEDIA["audio"]))
    MEDIA["text"] = _MEDIA_DIR / "notes.txt"
    MEDIA["text"].write_text("not media at all\n" * 50)


def tearDownModule():
    if _MEDIA_DIR is not None:
        shutil.rmtree(_MEDIA_DIR, ignore_errors=True)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_digest(root):
    out = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[path.relative_to(root).as_posix()] = sha256(path)
    return out


class Recorder:
    """Records every process spec started through the audited boundary while active."""

    def __init__(self, transform=None):
        self.specs = []
        self._original = tproc.run_process
        self._transform = transform

    def __enter__(self):
        def recording(spec, scopes, clock=None):
            self.specs.append(spec)
            outcome = self._original(spec, scopes) if clock is None else self._original(spec, scopes, clock)
            return self._transform(spec, outcome) if self._transform else outcome
        tproc.run_process = recording
        return self

    def __exit__(self, *exc):
        tproc.run_process = self._original

    @property
    def media_runs(self):
        """Specs that processed media (not version or capability probes)."""
        return [s for s in self.specs if "-i" in s.argv]


class HitServer:
    """A local HTTP server that counts every request it receives. Returns 404 for everything."""

    def __init__(self):
        self.requests = []
        hits = self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(404)
                self.end_headers()

            do_HEAD = do_GET

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class MediaCase(unittest.TestCase):
    registry = None

    @classmethod
    def setUpClass(cls):
        cls.registry = default_registry(FW)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-media-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # ------------------------------------------------------------ fixtures

    def project(self, name="p"):
        target, n = self.tmp / name, 1
        while target.exists():
            n += 1
            target = self.tmp / f"{name}{n}"
        shutil.copytree(FIXTURE, target)
        (target / "captures").mkdir()
        return target

    def source(self, project, fixture="av", name=None):
        """Copy a fixture medium into the project, where the foundation lets an execution read it."""
        target = project / "captures" / (name or MEDIA[fixture].name)
        shutil.copyfile(MEDIA[fixture], target)
        return target

    # ------------------------------------------------------------ execution

    def request(self, capability, project, path=None, ctx="TARGET_RUNTIME", artifacts=None, revision=None,
                **kwargs):
        adapter = capability.split(".")[0]
        if artifacts is None:
            artifacts = (InputArtifact("src", str(path), capture_context=ctx),) if path is not None else ()
        if adapter == "ffmpeg":
            kwargs.setdefault("allow_mutation", not kwargs.get("dry_run", False))
            kwargs.setdefault("inputs", dict(PARAMS[capability]))
        return ExecutionRequest(adapter_id=adapter, capability_id=capability,
                                subject=Subject("PROJECT", PROJECT_ID, revision), project_root=str(project),
                                input_artifacts=artifacts, **kwargs)

    def run_cap(self, capability, project, path=None, registry=None, **kwargs):
        return execute(registry or self.registry, self.request(capability, project, path, **kwargs))

    def inspect(self, project, path, **kwargs):
        return self.run_cap(pa.INSPECT, project, path, **kwargs)

    def codes(self, result):
        return {d.code for d in result.diagnostics}

    def messages(self, result):
        return [d.message for d in result.diagnostics]

    def assertSucceeded(self, result):
        self.assertEqual(result.status, tdg.SUCCESS, self.messages(result))

    def assertRefused(self, result, text=None):
        self.assertEqual(result.status, tdg.INVALID_REQUEST, self.messages(result))
        self.assertIn("INVALID_TOOL_REQUEST", self.codes(result))
        if text:
            self.assertTrue(any(text in m for m in self.messages(result)), self.messages(result))
        self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))

    def workspace_root(self, project):
        return project / ".game" / "gpos-runtime" / "tool-output"

    def public_json(self, result):
        return json.dumps(result.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------- A  registration

class A_Registration(MediaCase):
    def test_production_registry_is_exactly_ffmpeg_ffprobe_git(self):
        self.assertEqual(default_registry(FW).adapter_ids(), ["ffmpeg", "ffprobe", "git"])

    def test_synthetic_stays_out_of_production(self):
        registry = default_registry(FW)
        self.assertNotIn("synthetic", registry.adapter_ids())
        self.assertFalse(registry.allow_test_only)
        with self.assertRaises(Exception):
            registry.register(SyntheticAdapter())
        self.assertEqual(registry.adapter_ids(), ["ffmpeg", "ffprobe", "git"])

    def test_both_descriptors_pass_production_registration_validation(self):
        for descriptor in (fa.DESCRIPTOR, pa.DESCRIPTOR):
            self.assertEqual(tval.validate_descriptor(FW, descriptor, allow_test_only=False), [])

    def test_descriptor_identities(self):
        for d, identity in ((pa.DESCRIPTOR, ("ffprobe", "MEDIA", "ffprobe")), (fa.DESCRIPTOR, ("ffmpeg", "MEDIA", "FFmpeg"))):
            self.assertEqual((d.adapter_id, d.tool_family, d.target_tool), identity)
            self.assertEqual((d.adapter_kind, d.state_model, d.network), ("CLI", "STATELESS", "FORBIDDEN"))
            self.assertEqual(d.supported_platforms, ("WINDOWS", "MACOS", "LINUX"))
            self.assertFalse(d.test_only)
            self.assertEqual(d.filesystem_scopes, ())
            self.assertIsNone(d.minimum_tool_version)

    def test_ffprobe_declares_exactly_one_read_only_inspection(self):
        (cap,) = pa.DESCRIPTOR.capabilities
        self.assertEqual((cap.id, cap.category, cap.operation_class, cap.state_model, cap.execution_context),
                         ("ffprobe.inspect", "INSPECT", "READ_ONLY", "STATELESS", "OFFLINE_ANALYSIS"))
        self.assertTrue(cap.requires_tool and cap.requires_project)
        self.assertFalse(cap.requires_ready_routing or cap.single_writer_required or cap.dry_run_supported)
        self.assertEqual((cap.artifact_kinds, cap.potential_evidence, cap.input_kinds), ((), (), ()))

    def test_ffmpeg_declares_exactly_three_mutating_transforms(self):
        caps = {c.id: c for c in fa.DESCRIPTOR.capabilities}
        self.assertEqual(sorted(caps), ["ffmpeg.extract-audio", "ffmpeg.extract-clip", "ffmpeg.extract-frame"])
        expected = {fa.EXTRACT_FRAME: (("timestamp_seconds",), ("IMAGE",)),
                    fa.EXTRACT_CLIP: (("start_seconds", "duration_seconds"), ("VIDEO",)),
                    fa.EXTRACT_AUDIO: (("start_seconds", "duration_seconds"), ("AUDIO",))}
        for cap_id, cap in caps.items():
            self.assertEqual((cap.category, cap.operation_class, cap.state_model, cap.execution_context),
                             ("TRANSFORM", "MUTATING", "STATELESS", "OFFLINE_ANALYSIS"))
            self.assertTrue(cap.requires_tool and cap.requires_project and cap.dry_run_supported)
            self.assertFalse(cap.requires_ready_routing or cap.single_writer_required)
            self.assertEqual((cap.input_kinds, cap.artifact_kinds), expected[cap_id])
            self.assertEqual({t for t, _ in cap.potential_evidence}, {EVIDENCE[cap_id]})

    def test_declared_contexts_are_exactly_the_frozen_registry_compatibility(self):
        compat = REG["evidence_context_compatibility"]
        for cap in fa.DESCRIPTOR.capabilities:
            etype = EVIDENCE[cap.id]
            declared = {c for _, c in cap.potential_evidence}
            self.assertEqual(declared, set(compat[etype]), cap.id)
            self.assertNotIn("OFFLINE_ANALYSIS", declared)
        self.assertNotIn("DCC_RENDER", {c for _, c in fa.CAPABILITIES[2].potential_evidence})
        self.assertNotIn("PERFORMANCE_RUNTIME", set(compat["VISUAL_EVIDENCE"]) | set(compat["MOTION_EVIDENCE"])
                         | set(compat["AUDIO_EVIDENCE"]))

    def test_no_media_capability_can_offer_runtime_device_performance_or_human_evidence(self):
        forbidden = {"RUNTIME_EVIDENCE", "DEVICE_EVIDENCE", "PERFORMANCE_EVIDENCE", "HUMAN_EVIDENCE"}
        for cap in fa.DESCRIPTOR.capabilities + pa.DESCRIPTOR.capabilities:
            self.assertEqual({t for t, _ in cap.potential_evidence} & forbidden, set(), cap.id)

    def test_agent_and_tool_registries_stay_separate(self):
        from gpos.adapters.backends import BACKENDS
        for name in ("ffmpeg", "ffprobe"):
            self.assertNotIn(name, REG["adapter_ids"])
            self.assertNotIn(name, BACKENDS)


# ---------------------------------------------------------------- B  real probes

class B_RealProbes(MediaCase):
    def real_version(self, exe, program):
        first = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
        return re.match(rf"^{program} version (\S+) ", first).group(1)

    def test_real_ffprobe_is_available(self):
        probe = FfprobeAdapter().probe()
        self.assertEqual(probe.status, tmodel.AVAILABLE, probe.detail)
        self.assertTrue(os.path.isabs(probe.tool_path))
        self.assertEqual(probe.tool_path, str(Path(FFPROBE).resolve()))
        self.assertEqual(probe.tool_version, self.real_version(FFPROBE, "ffprobe"))

    def test_real_ffmpeg_is_available_with_the_required_encoders_and_muxers(self):
        probe = FfmpegAdapter().probe()
        self.assertEqual(probe.status, tmodel.AVAILABLE, probe.detail)
        self.assertTrue(os.path.isabs(probe.tool_path))
        self.assertEqual(probe.tool_path, str(Path(FFMPEG).resolve()))
        self.assertEqual(probe.tool_version, self.real_version(FFMPEG, "ffmpeg"))
        self.assertTrue(all(available for _, available, _ in probe.capability_availability))

    def test_probes_run_through_the_process_boundary_with_fixed_vectors(self):
        with Recorder() as rec:
            FfprobeAdapter().probe()
        self.assertEqual([tuple(s.argv) for s in rec.specs], [("-version",)])
        with Recorder() as rec:
            FfmpegAdapter().probe()
        self.assertEqual([tuple(s.argv) for s in rec.specs], [fa.VERSION_ARGV] + list(fa.HELP_ARGV))
        self.assertEqual(fa.HELP_ARGV, (
            ("-hide_banner", "-h", "encoder=png"), ("-hide_banner", "-h", "encoder=ffv1"),
            ("-hide_banner", "-h", "encoder=pcm_s16le"), ("-hide_banner", "-h", "muxer=image2pipe"),
            ("-hide_banner", "-h", "muxer=matroska"), ("-hide_banner", "-h", "muxer=wav")))

    def test_the_media_modules_never_import_subprocess(self):
        for path in self.media_modules():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {(node.module or "").split(".")[0]}
                else:
                    continue
                self.assertEqual(names & {"subprocess", "socket", "urllib", "http", "requests", "ctypes"}, set(),
                                 f"{path}: {names}")

    def test_process_py_is_still_the_only_subprocess_importer(self):
        importers = []
        for path in sorted((ROOT / "gpos").rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names):
                    importers.append(path.relative_to(ROOT).as_posix())
                elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                    importers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(set(importers)), ["gpos/tools/process.py"])

    @staticmethod
    def media_modules():
        return sorted((ROOT / "gpos" / "tools" / "ffmpeg").glob("*.py")) + sorted(
            (ROOT / "gpos" / "tools" / "ffprobe").glob("*.py")) + [ROOT / "gpos" / "tools" / "media_common.py"]


# ---------------------------------------------------------------- C  missing or unusable tools

def fake_tool(directory, name, script):
    """A stand-in program (TEST_ONLY) for behaviour a real FFmpeg cannot be made to show on demand."""
    exe = Path(directory) / "bin" / name
    exe.parent.mkdir(exist_ok=True)
    exe.write_text(f"#!{tproc.interpreter_path()}\nimport sys, time\nargv = sys.argv[1:]\n{script}\n")
    exe.chmod(0o755)
    return exe


FAKE_FFMPEG_HEADERS = """
if argv == ['-version']:
    print('ffmpeg version 9.0.1 Copyright (c) 2000-2025 the FFmpeg developers'); sys.exit(0)
if '-h' in argv:
    kind, name = argv[-1].split('=')
    if name in MISSING:
        print("Codec '%s' is not recognized by FFmpeg." % name if kind == 'encoder' else "Unknown format '%s'." % name)
    else:
        print(('Encoder %s [x]:' if kind == 'encoder' else 'Muxer %s [x]:') % name)
    sys.exit(0)
"""


class C_MissingTools(MediaCase):
    def registry_with(self, *adapters):
        registry = ToolRegistry(FW, allow_test_only=False)
        for adapter in adapters:
            registry.register(adapter)
        return registry

    def test_missing_ffprobe_is_unavailable_not_an_exception(self):
        probe = FfprobeAdapter(which=lambda name: None).probe()
        self.assertEqual(probe.status, tmodel.UNAVAILABLE)
        self.assertIsNone(probe.tool_path)
        self.assertNotIn("Traceback", json.dumps(probe.to_dict()))

    def test_missing_ffmpeg_is_unavailable_not_an_exception(self):
        probe = FfmpegAdapter(which=lambda name: None).probe()
        self.assertEqual(probe.status, tmodel.UNAVAILABLE)
        self.assertIsNone(probe.tool_path)
        self.assertNotIn("Traceback", json.dumps(probe.to_dict()))

    def test_missing_tools_make_execution_unavailable_without_a_process(self):
        p = self.project()
        src = self.source(p)
        cases = ((FfprobeAdapter(which=lambda n: None), pa.INSPECT),
                 (FfmpegAdapter(which=lambda n: None), fa.EXTRACT_FRAME))
        for adapter, cap in cases:
            with Recorder() as rec:
                result = self.run_cap(cap, p, src, registry=self.registry_with(adapter))
            self.assertEqual(result.status, tdg.UNAVAILABLE, cap)
            self.assertIn("TOOL_NOT_FOUND", self.codes(result))
            self.assertEqual(rec.specs, [])
            self.assertFalse(result.data)
            self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
        self.assertFalse(self.workspace_root(p).exists())

    def test_a_tool_found_only_through_a_relative_path_entry_is_not_used(self):
        for adapter in (FfprobeAdapter(which=lambda n: "bin/ffprobe"), FfmpegAdapter(which=lambda n: "bin/ffmpeg")):
            probe = adapter.probe()
            self.assertEqual(probe.status, tmodel.UNAVAILABLE)
            self.assertIn("relative PATH entry", probe.detail)

    def test_unrecognized_version_output_is_never_fabricated(self):
        for name, cls in (("ffprobe", FfprobeAdapter), ("ffmpeg", FfmpegAdapter)):
            exe = fake_tool(self.tmp, name, "print('this is not a version banner')")
            probe = cls(which=lambda n, exe=exe: str(exe)).probe()
            self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED, name)
            self.assertIsNone(probe.tool_version)

    def test_version_parsing_is_conservative(self):
        self.assertEqual(media.version_from("ffmpeg version 9.0.1 Copyright (c) 2000-2025", "ffmpeg"), "9.0.1")
        self.assertEqual(media.version_from("ffmpeg version N-118000-gabc123 Copyright (c)", "ffmpeg"),
                         "N-118000-gabc123")
        for line in ("ffmpeg version 9.0.1", "ffprobe version 9.0.1 Copyright", "ffmpeg version  Copyright",
                     "ffmpeg version $(rm) Copyright", "xffmpeg version 1 Copyright", ""):
            self.assertIsNone(media.version_from(line, "ffmpeg"), line)

    def test_a_build_without_a_required_encoder_is_unsupported_before_any_media_run(self):
        exe = fake_tool(self.tmp, "ffmpeg", "MISSING = {'ffv1'}" + FAKE_FFMPEG_HEADERS + "sys.exit(9)")
        adapter = FfmpegAdapter(which=lambda n: str(exe))
        probe = adapter.probe()
        self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED)
        self.assertEqual(probe.tool_version, "9.0.1")
        self.assertIn("encoder ffv1", probe.detail)
        p = self.project()
        with Recorder() as rec:
            result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), registry=self.registry_with(adapter))
        self.assertIn("TOOL_VERSION_UNSUPPORTED", self.codes(result))
        self.assertNotEqual(result.status, tdg.SUCCESS)
        self.assertEqual(rec.media_runs, [])

    def test_a_build_without_a_required_muxer_is_unsupported(self):
        exe = fake_tool(self.tmp, "ffmpeg", "MISSING = {'matroska'}" + FAKE_FFMPEG_HEADERS)
        probe = FfmpegAdapter(which=lambda n: str(exe)).probe()
        self.assertEqual(probe.status, tmodel.VERSION_UNSUPPORTED)
        self.assertIn("muxer matroska", probe.detail)

    def test_the_real_encoder_query_reports_absence_with_exit_zero(self):
        # Why presence is read from the header line and never from the exit code.
        done = subprocess.run([FFMPEG, "-hide_banner", "-h", "encoder=gpos_no_such_encoder"], capture_output=True,
                              text=True)
        self.assertEqual(done.returncode, 0)
        self.assertFalse(done.stdout.lstrip().startswith("Encoder gpos_no_such_encoder ["))


# ---------------------------------------------------------------- D  ffprobe inspection

class D_Inspection(MediaCase):
    def test_real_video_and_audio_are_summarized(self):
        p = self.project()
        result = self.inspect(p, self.source(p))
        self.assertSucceeded(result)
        data = result.data
        self.assertEqual(set(data), {"source_artifact_id", "format_names", "duration_seconds", "stream_count",
                                     "video_stream_count", "audio_stream_count", "other_stream_count",
                                     "primary_video", "primary_audio"})
        self.assertEqual(data["source_artifact_id"], "src")
        self.assertIn("mp4", data["format_names"])
        self.assertAlmostEqual(float(data["duration_seconds"]), 3.0, delta=0.1)
        self.assertEqual((data["stream_count"], data["video_stream_count"], data["audio_stream_count"],
                          data["other_stream_count"]), (2, 1, 1, 0))
        video, audio = data["primary_video"], data["primary_audio"]
        self.assertEqual((video["codec_name"], video["width"], video["height"], video["average_frame_rate"]),
                         ("mpeg4", 320, 240, "30/1"))
        self.assertEqual(video["pixel_format"], "yuv420p")
        self.assertEqual((audio["codec_name"], audio["sample_rate_hz"], audio["channels"], audio["channel_layout"]),
                         ("aac", 44100, 1, "mono"))
        self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
        self.assertFalse(result.mutation_performed)

    def test_video_only_and_audio_only_sources(self):
        p = self.project()
        video = self.inspect(p, self.source(p, "video")).data
        self.assertEqual((video["video_stream_count"], video["audio_stream_count"]), (1, 0))
        self.assertIsNone(video["primary_audio"])
        self.assertIn("matroska", video["format_names"])
        audio = self.inspect(p, self.source(p, "audio")).data
        self.assertEqual((audio["video_stream_count"], audio["audio_stream_count"]), (0, 1))
        self.assertIsNone(audio["primary_video"])
        self.assertEqual(audio["primary_audio"]["codec_name"], "pcm_s16le")

    def test_no_tags_paths_or_raw_json_reach_the_result(self):
        p = self.project()
        src = self.source(p)
        result = self.inspect(p, src)
        text = self.public_json(result)
        for needle in (TAG_MARKER, "hunter2", SECRET_TOKEN, "title", "comment", "tags", str(p), str(src),
                       "captures", "\"streams\""):
            self.assertNotIn(needle, text, needle)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        # the fixture really does carry those tags: test code can see them, the adapter never asks
        self.assertIn(TAG_MARKER, json.dumps(ffprobe_json(src, "-show_format")))

    def test_inspection_writes_nothing(self):
        p = self.project()
        src = self.source(p)
        before = tree_digest(p)
        self.assertSucceeded(self.inspect(p, src))
        self.assertEqual(tree_digest(p), before)
        self.assertFalse(self.workspace_root(p).exists())

    def test_exactly_one_input_artifact(self):
        p = self.project()
        src = self.source(p)
        with Recorder() as rec:
            none = self.inspect(p, None)
            two = self.inspect(p, None, artifacts=(InputArtifact("a", str(src)), InputArtifact("b", str(src))))
        self.assertRefused(none, "exactly one input artifact; 0 were supplied")
        self.assertRefused(two, "exactly one input artifact; 2 were supplied")
        self.assertEqual(rec.media_runs, [])

    def test_capture_context_is_optional_for_inspection(self):
        p = self.project()
        self.assertSucceeded(self.inspect(p, self.source(p), ctx=None))

    def test_inspection_does_not_enumerate_packets_or_frames(self):
        argv = pa.inspect_argv("/x/in.mp4")
        for option in ("-show_packets", "-show_frames", "-count_frames", "-count_packets", "-show_format",
                       "-show_streams", "-show_data", "-show_private_data", "-show_chapters"):
            self.assertNotIn(option, argv)
        self.assertNotIn("tags", pa.ENTRIES)


# ---------------------------------------------------------------- E  raw JSON and the parser

GOOD = {"streams": [{"index": 0, "codec_name": "h264", "codec_type": "video", "width": 1920, "height": 1080,
                     "pix_fmt": "yuv420p", "avg_frame_rate": "30000/1001", "duration": "10.010000"},
                    {"index": 1, "codec_name": "aac", "codec_type": "audio", "sample_rate": "48000", "channels": 2,
                     "channel_layout": "stereo", "duration": "10.000000"}],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "10.010000"}}


def raw(document):
    return json.dumps(document).encode()


class E_RawJson(MediaCase):
    def test_the_parser_normalizes_a_well_formed_document(self):
        out = pp.parse(raw(GOOD))
        self.assertEqual(out["primary_video"]["average_frame_rate"], "30000/1001")
        self.assertEqual(out["primary_audio"]["sample_rate_hz"], 48000)
        self.assertEqual(out["duration_seconds"], "10.010000")
        self.assertEqual(out["format_names"], ["mov", "mp4", "m4a", "3gp", "3g2", "mj2"])

    def test_unknown_values_are_normalized_explicitly(self):
        doc = json.loads(json.dumps(GOOD))
        doc["format"]["duration"] = "N/A"
        doc["streams"][0]["avg_frame_rate"] = "0/0"
        del doc["streams"][1]["channel_layout"]
        out = pp.parse(raw(doc))
        self.assertIsNone(out["duration_seconds"])
        self.assertIsNone(out["primary_video"]["average_frame_rate"])
        self.assertIsNone(out["primary_audio"]["channel_layout"])
        self.assertIsNone(pp.parse(raw({"format": {"format_name": "wav"}}))["primary_video"])

    def test_malformed_or_unexpected_shapes_fail_closed(self):
        def mutated(fn):
            doc = json.loads(json.dumps(GOOD))
            fn(doc)
            return raw(doc)
        bad = [b"", b"{", b"[]", b"\xff\xfe", "not bytes", raw({"streams": []}),
               mutated(lambda d: d["format"].update(tags={"title": "x"})),
               mutated(lambda d: d.update(packets=[])),
               mutated(lambda d: d["streams"][0].update(tags={"title": "x"})),
               mutated(lambda d: d["streams"][0].update(width=True)),
               mutated(lambda d: d["streams"][0].update(width=70000)),
               mutated(lambda d: d["streams"][0].update(index="0")),
               mutated(lambda d: d["streams"][0].update(codec_type="bogus")),
               mutated(lambda d: d["streams"][0].update(codec_name="h264; rm -rf")),
               mutated(lambda d: d["streams"][0].update(avg_frame_rate="30/0")),
               mutated(lambda d: d["streams"][0].update(avg_frame_rate=30.0)),
               mutated(lambda d: d["streams"][1].update(sample_rate="48k")),
               mutated(lambda d: d["streams"][1].update(sample_rate="99999999")),
               mutated(lambda d: d["streams"][1].update(channels=0)),
               mutated(lambda d: d["format"].update(duration="-1")),
               mutated(lambda d: d["format"].update(duration="1e9")),
               mutated(lambda d: d["format"].update(duration=12.5)),
               mutated(lambda d: d["format"].update(format_name="/etc/passwd")),
               mutated(lambda d: d.update(streams={}))]
        for payload in bad:
            with self.assertRaises(pp.ProbeParseError, msg=repr(payload)[:80]):
                pp.parse(payload)

    def test_an_unreasonable_stream_count_fails_closed(self):
        streams = [{"index": i, "codec_type": "data"} for i in range(pp.MAX_STREAMS)]
        self.assertEqual(pp.parse(raw({"format": {"format_name": "mpegts"}, "streams": streams}))["stream_count"],
                         pp.MAX_STREAMS)
        streams.append({"index": 0, "codec_type": "data"})
        with self.assertRaises(pp.ProbeParseError):
            pp.parse(raw({"format": {"format_name": "mpegts"}, "streams": streams}))

    def test_the_adapter_parses_the_raw_capture_not_the_public_text(self):
        # The public text is replaced by garbage; the raw capture is left intact. Inspection still works,
        # so it can only have parsed the raw bytes.
        def corrupt_public(spec, outcome):
            import dataclasses
            return dataclasses.replace(outcome, stdout="[REDACTED] not json", stderr="")
        p = self.project()
        with Recorder(corrupt_public):
            result = self.inspect(p, self.source(p))
        self.assertSucceeded(result)
        self.assertEqual(result.data["primary_video"]["width"], 320)

    def test_a_corrupted_raw_capture_is_refused(self):
        def corrupt_raw(spec, outcome):
            import dataclasses
            return dataclasses.replace(outcome, raw_stdout=outcome.raw_stdout[:-20]) if "-i" in spec.argv else outcome
        p = self.project()
        with Recorder(corrupt_raw):
            result = self.inspect(p, self.source(p))
        self.assertEqual(result.status, tdg.FAILED)
        self.assertIn("could not be read as the expected summary", " ".join(self.messages(result)))
        self.assertFalse(result.data.get("primary_video") if result.data else None)

    def test_truncated_output_refuses_a_normalized_result(self):
        p = self.project()
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(FfprobeAdapter(capture_bytes=64))
        result = self.inspect(p, self.source(p), registry=registry)
        self.assertEqual(result.status, tdg.FAILED)
        self.assertTrue(result.output_truncated)
        self.assertIn("truncated", " ".join(self.messages(result)))
        self.assertNotIn("primary_video", result.data or {})

    def test_no_raw_bytes_reach_the_public_result(self):
        p = self.project()
        result = self.inspect(p, self.source(p))
        text = self.public_json(result)
        self.assertNotIn("raw_stdout", text)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertGreater(result.stdout_bytes, 0)  # the byte count is kept


# ---------------------------------------------------------------- F  non-media input

class F_NonMedia(MediaCase):
    def test_a_text_file_is_a_structured_failure(self):
        p = self.project()
        src = self.source(p, "text")
        for cap in (pa.INSPECT,) + TRANSFORMS:
            result = self.run_cap(cap, p, src)
            self.assertEqual(result.status, tdg.FAILED, cap)
            self.assertIn("EXECUTION_FAILED", self.codes(result))
            text = self.public_json(result)
            self.assertNotIn("Traceback", text)
            self.assertNotIn(str(src), text)
            self.assertNotIn(str(p), text)
            self.assertEqual(result.evidence_candidates, ())
            self.assertEqual(result.artifacts, ())  # FFmpeg refused before creating an output

    def test_a_random_binary_file_is_a_structured_failure(self):
        p = self.project()
        src = p / "captures" / "noise.bin"
        src.write_bytes(bytes(range(256)) * 64)
        result = self.inspect(p, src)
        self.assertEqual(result.status, tdg.FAILED)
        self.assertIn("<input src>", " ".join(self.messages(result)))


# ---------------------------------------------------------------- G  local network block

HLS = "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-VERSION:3\n#EXTINF:1.0,\nhttp://127.0.0.1:{port}/segment.ts\n" \
      "#EXT-X-ENDLIST\n"


class G_NetworkBlock(MediaCase):
    def playlist(self, p, port, name="stream.m3u8"):
        path = p / "captures" / name
        path.write_text(HLS.format(port=port))
        return path

    def control(self, path, *restrictions):
        """Run the real ffprobe from test code with chosen restrictions; return nothing (the server counts)."""
        subprocess.run([FFPROBE, "-v", "quiet", *restrictions, "-i", str(path)], capture_output=True, timeout=30)

    def test_the_fixture_really_reaches_the_server_without_the_restrictions(self):
        # Sensitivity control: without the adapter's whitelists the same file makes an HTTP request.
        p = self.project()
        with HitServer() as server:
            self.control(self.playlist(p, server.port), "-protocol_whitelist", "file,http,tcp")
        self.assertGreaterEqual(len(server.requests), 1)

    def test_each_whitelist_alone_blocks_the_request(self):
        p = self.project()
        with HitServer() as server:
            path = self.playlist(p, server.port)
            self.control(path, "-protocol_whitelist", "file,http,tcp", "-format_whitelist", ",".join(media.SAFE_DEMUXERS))
            self.control(path, "-protocol_whitelist", "file", "-format_whitelist", "hls," + ",".join(media.SAFE_DEMUXERS))
        self.assertEqual(server.requests, [])

    def test_the_adapters_refuse_a_playlist_with_zero_requests(self):
        p = self.project()
        with HitServer() as server:
            path = self.playlist(p, server.port)
            disguised = self.playlist(p, server.port, name="looks-like.mp4")
            results = [self.run_cap(cap, p, src) for cap in (pa.INSPECT,) + TRANSFORMS for src in (path, disguised)]
        self.assertEqual(server.requests, [])
        for result in results:
            self.assertEqual(result.status, tdg.FAILED, result.capability_id)
            self.assertEqual(result.evidence_candidates, ())
            self.assertNotIn(str(server.port), self.public_json(result))

    def test_a_concat_list_pointing_at_the_server_is_refused_with_zero_requests(self):
        p = self.project()
        with HitServer() as server:
            path = p / "captures" / "list.ffconcat"
            path.write_text(f"ffconcat version 1.0\nfile 'http://127.0.0.1:{server.port}/clip.mp4'\n")
            results = [self.run_cap(cap, p, path) for cap in (pa.INSPECT, fa.EXTRACT_FRAME)]
        self.assertEqual(server.requests, [])
        for result in results:
            self.assertEqual(result.status, tdg.FAILED)

    def test_a_url_is_never_an_input(self):
        p = self.project()
        with HitServer() as server, Recorder() as rec:
            url = f"http://127.0.0.1:{server.port}/x.mp4"
            results = [self.run_cap(cap, p, url) for cap in (pa.INSPECT, fa.EXTRACT_FRAME)]
        self.assertEqual(server.requests, [])
        self.assertEqual(rec.media_runs, [])
        for result in results:
            self.assertNotEqual(result.status, tdg.SUCCESS)
            self.assertIn("UNSAFE_ARTIFACT_PATH", self.codes(result))

    def test_no_network_protocol_is_authorized(self):
        self.assertEqual(media.ALLOWED_PROTOCOLS, "file")
        self.assertEqual(media.INPUT_RESTRICTIONS[:2], ("-protocol_whitelist", "file"))
        self.assertEqual(set(media.SAFE_DEMUXERS) & set(media.FORBIDDEN_DEMUXERS), set())
        self.assertEqual(set(media.SAFE_DEMUXERS) & set(media.NETWORK_PROTOCOLS), set())


# ---------------------------------------------------------------- H  frame extraction

class H_Frame(MediaCase):
    def test_a_frame_is_a_derived_png_offering_visual_evidence(self):
        p = self.project()
        src = self.source(p)
        result = self.run_cap(fa.EXTRACT_FRAME, p, src, revision=REVISION)
        self.assertSucceeded(result)
        (art,) = result.artifacts
        self.assertEqual((art.artifact_id, art.kind, art.media_type), ("frame", "IMAGE", "image/png"))
        self.assertEqual((art.classification, art.derived_from, art.origin_capture_context, art.complete),
                         ("DERIVED", ("src",), "TARGET_RUNTIME", True))
        png = Path(art.absolute_path)
        self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(art.sha256, sha256(png))
        info = ffprobe_json(png, "-show_entries", "stream=codec_name,width,height")["streams"][0]
        self.assertEqual((info["codec_name"], info["width"], info["height"]), ("png", 320, 240))
        (cand,) = result.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context), ("VISUAL_EVIDENCE", "TARGET_RUNTIME"))
        self.assertEqual((cand.artifact_ids, cand.derived_from), (("frame",), ("src",)))
        self.assertTrue(cand.materializable)
        self.assertTrue(result.mutation_performed)
        self.assertEqual(result.data["timestamp_seconds"], "1.500000")

    def test_frames_are_byte_reproducible(self):
        p = self.project()
        src = self.source(p)
        first = self.run_cap(fa.EXTRACT_FRAME, p, src).artifacts[0].sha256
        second = self.run_cap(fa.EXTRACT_FRAME, p, src).artifacts[0].sha256
        self.assertEqual(first, second)
        other = self.run_cap(fa.EXTRACT_FRAME, p, src, inputs={"timestamp_seconds": 2.5}).artifacts[0].sha256
        self.assertNotEqual(first, other)

    def test_without_a_subject_revision_the_candidate_is_offered_but_not_materializable(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p))
        self.assertSucceeded(result)
        self.assertFalse(result.evidence_candidates[0].materializable)
        self.assertIn("EVIDENCE_NOT_MATERIALIZABLE", self.codes(result))

    def test_a_frame_past_the_end_is_a_failure_not_evidence(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), inputs={"timestamp_seconds": 99})
        self.assertEqual(result.status, tdg.FAILED)
        self.assertIn("past the end", " ".join(self.messages(result)))
        self.assertEqual(result.evidence_candidates, ())
        for art in result.artifacts:
            self.assertFalse(art.complete)

    def test_a_video_less_source_yields_no_frame(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p, "audio"), ctx="EDITOR")
        self.assertEqual(result.status, tdg.FAILED)
        self.assertEqual(result.evidence_candidates, ())


# ---------------------------------------------------------------- I  numeric validation

class I_NumericValidation(MediaCase):
    BAD = [-1, -0.5, "-1", float("nan"), float("inf"), float("-inf"), "nan", "NaN", "inf", "Infinity", "1e3", "abc",
           " 1", "1 ", "1\n", "1.", ".5", "+1", "0x10", "1.1234567", "１", "²", "", True, False, None, [], {}, "1;-y",
           86400.000001, "86401", 10 ** 12, Decimal("1E+100000"), Decimal("NaN"), Decimal("-0.1")]

    def test_bad_timestamps_are_refused_before_ffmpeg_runs(self):
        p = self.project()
        src = self.source(p)
        with Recorder() as rec:
            for value in self.BAD:
                for dry in (False, True):
                    result = self.run_cap(fa.EXTRACT_FRAME, p, src, inputs={"timestamp_seconds": value}, dry_run=dry)
                    self.assertRefused(result)
        self.assertEqual(rec.media_runs, [])

    def test_a_missing_timestamp_is_refused(self):
        p = self.project()
        self.assertRefused(self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), inputs={}), "timestamp_seconds is required")

    def test_canonical_rendering(self):
        cases = {0: "0.000000", 1: "1.000000", 1.5: "1.500000", "2.25": "2.250000", "7": "7.000000",
                 Decimal("3.1415926"): "3.141593", 0.1: "0.100000", -0.0: "0.000000", "86400": "86400.000000",
                 1e-7: "0.000000"}
        for value, expected in cases.items():
            self.assertEqual(fa.seconds(value, "t", True, fa.MAX_TIMESTAMP_SECONDS), (expected, None), repr(value))

    def test_duration_must_be_positive_and_bounded(self):
        for value in (0, "0", 0.0000001, -1):
            self.assertIsNone(fa.seconds(value, "d", False, fa.MAX_CLIP_SECONDS)[0], repr(value))
        self.assertEqual(fa.seconds(30, "d", False, fa.MAX_CLIP_SECONDS), ("30.000000", None))
        self.assertIsNone(fa.seconds("30.000001", "d", False, fa.MAX_CLIP_SECONDS)[0])
        self.assertEqual(fa.seconds(120, "d", False, fa.MAX_AUDIO_SECONDS), ("120.000000", None))
        self.assertIsNone(fa.seconds("120.000001", "d", False, fa.MAX_AUDIO_SECONDS)[0])

    def test_the_bounds(self):
        self.assertEqual((fa.MAX_TIMESTAMP_SECONDS, fa.MAX_CLIP_SECONDS, fa.MAX_AUDIO_SECONDS),
                         (Decimal(86400), Decimal(30), Decimal(120)))
        self.assertEqual(fa.SIZE_LIMITS, {"frame": 64 * 1024 ** 2, "clip": 4 * 1024 ** 3, "audio": 256 * 1024 ** 2})

    def test_the_numbers_reach_argv_only_in_canonical_form(self):
        p = self.project()
        with Recorder() as rec:
            self.assertSucceeded(self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), inputs={"timestamp_seconds": "1"}))
        (spec,) = rec.media_runs
        self.assertEqual(spec.argv[spec.argv.index("-ss") + 1], "1.000000")


# ---------------------------------------------------------------- J  clip extraction

class J_Clip(MediaCase):
    def test_a_clip_is_a_derived_ffv1_matroska_offering_motion_evidence(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_CLIP, p, self.source(p), revision=REVISION,
                              inputs={"start_seconds": "0.5", "duration_seconds": 2})
        self.assertSucceeded(result)
        (art,) = result.artifacts
        self.assertEqual((art.artifact_id, art.kind, art.media_type, art.classification, art.derived_from,
                          art.origin_capture_context), ("clip", "VIDEO", "video/x-matroska", "DERIVED", ("src",),
                                                        "TARGET_RUNTIME"))
        info = ffprobe_json(art.absolute_path, "-show_entries",
                            "format=format_name,duration:stream=codec_type,codec_name,sample_fmt")
        self.assertIn("matroska", info["format"]["format_name"])
        codecs = {s["codec_type"]: s for s in info["streams"]}
        self.assertEqual(codecs["video"]["codec_name"], "ffv1")
        self.assertEqual((codecs["audio"]["codec_name"], codecs["audio"]["sample_fmt"]), ("pcm_s16le", "s16"))
        self.assertAlmostEqual(float(info["format"]["duration"]), 2.0, delta=0.1)
        (cand,) = result.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context, cand.derived_from),
                         ("MOTION_EVIDENCE", "TARGET_RUNTIME", ("src",)))
        self.assertTrue(cand.materializable)
        self.assertNotIn("AUDIO_EVIDENCE", {c.evidence_type for c in result.evidence_candidates})

    def test_a_video_only_source_gives_a_clip_without_audio(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_CLIP, p, self.source(p, "video"))
        self.assertSucceeded(result)
        info = ffprobe_json(result.artifacts[0].absolute_path, "-show_entries", "stream=codec_type")
        self.assertEqual([s["codec_type"] for s in info["streams"]], ["video"])

    def test_a_window_past_the_end_is_shorter_and_says_so(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_CLIP, p, self.source(p), inputs={"start_seconds": 2, "duration_seconds": 5})
        self.assertSucceeded(result)
        info = ffprobe_json(result.artifacts[0].absolute_path, "-show_entries", "format=duration")
        self.assertLess(float(info["format"]["duration"]), 1.5)
        self.assertTrue(any("correspondingly shorter" in l for l in result.evidence_candidates[0].limitations))

    def test_duration_bounds_and_required_inputs(self):
        p = self.project()
        src = self.source(p)
        with Recorder() as rec:
            for inputs in ({"start_seconds": 0, "duration_seconds": "30.000001"},
                           {"start_seconds": 0, "duration_seconds": 0}, {"start_seconds": 0},
                           {"duration_seconds": 1}, {}, {"start_seconds": -1, "duration_seconds": 1}):
                self.assertRefused(self.run_cap(fa.EXTRACT_CLIP, p, src, inputs=inputs))
        self.assertEqual(rec.media_runs, [])

    def test_clips_are_byte_reproducible(self):
        p = self.project()
        src = self.source(p)
        hashes = {self.run_cap(fa.EXTRACT_CLIP, p, src).artifacts[0].sha256 for _ in range(2)}
        self.assertEqual(len(hashes), 1)


# ---------------------------------------------------------------- K  audio extraction

class K_Audio(MediaCase):
    def test_audio_is_a_derived_pcm_wav_offering_audio_evidence(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_AUDIO, p, self.source(p), revision=REVISION,
                              inputs={"start_seconds": 0.5, "duration_seconds": 1})
        self.assertSucceeded(result)
        (art,) = result.artifacts
        self.assertEqual((art.artifact_id, art.kind, art.media_type, art.classification, art.derived_from,
                          art.origin_capture_context), ("audio", "AUDIO", "audio/wav", "DERIVED", ("src",),
                                                        "TARGET_RUNTIME"))
        head = Path(art.absolute_path).read_bytes()[:12]
        self.assertEqual((head[:4], head[8:12]), (b"RIFF", b"WAVE"))
        info = ffprobe_json(art.absolute_path, "-show_entries",
                            "format=format_name,duration:stream=codec_name,sample_rate,channels")
        self.assertEqual(info["format"]["format_name"], "wav")
        self.assertEqual((info["streams"][0]["codec_name"], info["streams"][0]["sample_rate"]), ("pcm_s16le", "44100"))
        self.assertAlmostEqual(float(info["format"]["duration"]), 1.0, delta=0.05)
        (cand,) = result.evidence_candidates
        self.assertEqual((cand.evidence_type, cand.capture_context, cand.derived_from),
                         ("AUDIO_EVIDENCE", "TARGET_RUNTIME", ("src",)))
        self.assertTrue(cand.materializable)

    def test_audio_from_an_audio_only_source(self):
        p = self.project()
        self.assertSucceeded(self.run_cap(fa.EXTRACT_AUDIO, p, self.source(p, "audio"), ctx="EDITOR"))

    def test_a_source_without_audio_is_a_structured_failure(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_AUDIO, p, self.source(p, "video"))
        self.assertEqual(result.status, tdg.FAILED)
        self.assertEqual(result.evidence_candidates, ())
        self.assertNotIn(str(p), self.public_json(result))

    def test_duration_bound(self):
        p = self.project()
        src = self.source(p)
        self.assertRefused(self.run_cap(fa.EXTRACT_AUDIO, p, src, inputs={"start_seconds": 0,
                                                                           "duration_seconds": "120.000001"}))
        result = self.run_cap(fa.EXTRACT_AUDIO, p, src, dry_run=True, inputs={"start_seconds": 0,
                                                                              "duration_seconds": 120})
        self.assertSucceeded(result)


# ---------------------------------------------------------------- L  input without capture context

class L_NoCaptureContext(MediaCase):
    def test_every_transform_refuses_a_source_without_a_capture_context(self):
        p = self.project()
        src = self.source(p)
        with Recorder() as rec:
            for cap in TRANSFORMS:
                for dry in (False, True):
                    result = self.run_cap(cap, p, src, ctx=None, dry_run=dry)
                    self.assertRefused(result, "has no capture context")
        self.assertEqual(rec.media_runs, [])
        self.assertEqual(list(self.workspace_root(p).rglob("*.*")), [])  # no output file anywhere

    def test_every_transform_consumes_exactly_one_input_artifact(self):
        p = self.project()
        src = self.source(p)
        two = (InputArtifact("a", str(src), capture_context="TARGET_RUNTIME"),
               InputArtifact("b", str(src), capture_context="TARGET_RUNTIME"))
        with Recorder() as rec:
            for cap in TRANSFORMS:
                self.assertRefused(self.run_cap(cap, p, None), "0 were supplied")
                self.assertRefused(self.run_cap(cap, p, None, artifacts=two), "2 were supplied")
        self.assertEqual(rec.media_runs, [])


# ---------------------------------------------------------------- M  incompatible capture context

class M_IncompatibleContext(MediaCase):
    def test_every_context_is_admitted_exactly_when_the_registry_permits_it(self):
        p = self.project()
        src = self.source(p)
        compat = REG["evidence_context_compatibility"]
        with Recorder() as rec:
            for cap in TRANSFORMS:
                for context in REG["capture_contexts"]:
                    result = self.run_cap(cap, p, src, ctx=context, dry_run=True)
                    if context in compat[EVIDENCE[cap]]:
                        self.assertSucceeded(result)
                    else:
                        self.assertRefused(result, "evidence_context_compatibility")
        self.assertEqual(rec.media_runs, [])

    def test_performance_runtime_frame_is_refused_before_ffmpeg_runs(self):
        p = self.project()
        with Recorder() as rec:
            result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), ctx="PERFORMANCE_RUNTIME")
        self.assertRefused(result, "VISUAL_EVIDENCE cannot come from a source captured in PERFORMANCE_RUNTIME")
        self.assertEqual(rec.media_runs, [])

    def test_offline_analysis_source_is_refused(self):
        p = self.project()
        for cap in TRANSFORMS:
            self.assertRefused(self.run_cap(cap, p, self.source(p), ctx="OFFLINE_ANALYSIS"))

    def test_an_unknown_context_is_refused_by_the_foundation(self):
        p = self.project()
        with Recorder() as rec:
            result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), ctx="MADE_UP")
        self.assertNotEqual(result.status, tdg.SUCCESS)
        self.assertEqual(rec.media_runs, [])


# ---------------------------------------------------------------- N  derived context

class N_DerivedContext(MediaCase):
    def test_target_runtime_stays_target_runtime(self):
        p = self.project()
        src = self.source(p)
        for context in ("TARGET_RUNTIME", "AUTOMATED_TEST", "DIAGNOSTIC_RUNTIME"):
            for cap in TRANSFORMS:
                result = self.run_cap(cap, p, src, ctx=context)
                self.assertSucceeded(result)
                (cand,) = result.evidence_candidates
                self.assertEqual(cand.capture_context, context, cap)
                self.assertEqual(result.artifacts[0].origin_capture_context, context)
                self.assertNotEqual(cand.capture_context, "OFFLINE_ANALYSIS")
                self.assertEqual(result.provenance.execution_context, "OFFLINE_ANALYSIS")

    def test_every_limitation_names_the_inherited_context(self):
        p = self.project()
        for cap in TRANSFORMS:
            cand = self.run_cap(cap, p, self.source(p)).evidence_candidates[0]
            self.assertTrue(cand.limitations)
            self.assertIn("not an independent capture", cand.limitations[0])
            self.assertIn("TARGET_RUNTIME", cand.limitations[0])


# ---------------------------------------------------------------- O  DCC context

class O_DccContext(MediaCase):
    def test_dcc_render_frame_and_clip_follow_the_registry(self):
        p = self.project()
        src = self.source(p)
        for cap in (fa.EXTRACT_FRAME, fa.EXTRACT_CLIP):
            result = self.run_cap(cap, p, src, ctx="DCC_RENDER")
            self.assertSucceeded(result)
            self.assertEqual(result.evidence_candidates[0].capture_context, "DCC_RENDER")

    def test_dcc_render_audio_is_refused_exactly_as_the_frozen_matrix_says(self):
        self.assertNotIn("DCC_RENDER", REG["evidence_context_compatibility"]["AUDIO_EVIDENCE"])
        p = self.project()
        with Recorder() as rec:
            result = self.run_cap(fa.EXTRACT_AUDIO, p, self.source(p), ctx="DCC_RENDER")
        self.assertRefused(result, "AUDIO_EVIDENCE cannot come from a source captured in DCC_RENDER")
        self.assertEqual(rec.media_runs, [])


# ---------------------------------------------------------------- P  mutation consent

class P_MutationConsent(MediaCase):
    def test_a_transform_without_consent_does_nothing(self):
        p = self.project()
        src = self.source(p)
        before = tree_digest(p)
        with Recorder() as rec:
            for cap in TRANSFORMS:
                result = self.run_cap(cap, p, src, allow_mutation=False)
                self.assertEqual(result.status, tdg.INVALID_REQUEST)
                self.assertIn("MUTATION_NOT_ALLOWED", self.codes(result))
                self.assertFalse(result.mutation_performed)
        self.assertEqual(rec.specs, [])
        self.assertFalse(self.workspace_root(p).exists())
        self.assertEqual(tree_digest(p), before)


# ---------------------------------------------------------------- Q  dry run

class Q_DryRun(MediaCase):
    def test_each_transform_plans_without_a_process_workspace_output_or_evidence(self):
        p = self.project()
        src = self.source(p)
        before = tree_digest(p)
        with Recorder() as rec:
            for cap in TRANSFORMS:
                result = self.run_cap(cap, p, src, dry_run=True)
                self.assertSucceeded(result)
                self.assertTrue(result.dry_run)
                self.assertFalse(result.mutation_performed)
                self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
                self.assertIn("MUTATION_SKIPPED_DRY_RUN", self.codes(result))
                self.assertEqual(len(result.plan), 3)
                self.assertIn("would write", result.plan[0])
                self.assertIn("no FFmpeg process", result.plan[2])
                self.assertIn(f"would offer {EVIDENCE[cap]} captured in TARGET_RUNTIME", result.plan[1])
        self.assertEqual(rec.media_runs, [])
        self.assertFalse(self.workspace_root(p).exists())
        self.assertFalse((p / ".game" / "gpos-runtime").exists())
        self.assertEqual(tree_digest(p), before)

    def test_a_dry_run_with_an_output_directory_creates_nothing(self):
        p = self.project()
        out = p / "reviews" / "deep" / "out"
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), dry_run=True, output_dir=str(out))
        self.assertSucceeded(result)
        self.assertFalse((p / "reviews").exists())

    def test_a_dry_run_never_claims_an_output_exists(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_CLIP, p, self.source(p), dry_run=True)
        self.assertTrue(all(line.startswith("would") or line.startswith("no ") for line in result.plan))


# ---------------------------------------------------------------- R  source immutability

class R_SourceImmutability(MediaCase):
    def test_the_source_and_its_directory_are_unchanged_by_every_capability(self):
        p = self.project()
        src = self.source(p)
        before, directory = sha256(src), tree_digest(src.parent)
        stat = src.stat()
        for cap in (pa.INSPECT,) + TRANSFORMS:
            self.assertSucceeded(self.run_cap(cap, p, src))
        self.assertEqual(sha256(src), before)
        self.assertEqual(tree_digest(src.parent), directory)  # nothing was written beside the source
        self.assertEqual(src.stat().st_mtime_ns, stat.st_mtime_ns)


# ---------------------------------------------------------------- S  output boundaries

class S_OutputBoundaries(MediaCase):
    def test_outputs_stay_in_this_executions_workspace(self):
        p = self.project()
        src = self.source(p)
        records = p / ".game" / "gpos"
        before = tree_digest(records)
        for cap in TRANSFORMS:
            result = self.run_cap(cap, p, src)
            (art,) = result.artifacts
            expected = f".game/gpos-runtime/tool-output/ffmpeg/{result.request_id}/{OUTPUT[cap][3]}"
            self.assertEqual(art.path, expected)
            self.assertEqual(Path(art.absolute_path).parent.name, result.request_id)
        self.assertEqual(tree_digest(records), before)

    def test_no_caller_output_name_codec_filter_map_or_format(self):
        p = self.project()
        src = self.source(p)
        with Recorder() as rec:
            for name in ("output", "output_name", "filename", "codec", "c:v", "vf", "filter", "filter_complex",
                         "map", "format", "f", "protocol_whitelist", "argv", "args", "executable", "y"):
                inputs = dict(PARAMS[fa.EXTRACT_FRAME], **{name: "x"})
                result = self.run_cap(fa.EXTRACT_FRAME, p, src, inputs=inputs)
                self.assertEqual(result.status, tdg.INVALID_REQUEST, name)
        self.assertEqual(rec.media_runs, [])

    def test_the_source_directory_is_never_the_output_directory(self):
        p = self.project()
        src = self.source(p)
        before = tree_digest(src.parent)
        with Recorder() as rec:
            result = self.run_cap(fa.EXTRACT_FRAME, p, src, output_dir=str(src.parent))
        self.assertRefused(result, "never written beside its source")
        self.assertEqual(rec.media_runs, [])
        self.assertEqual(tree_digest(src.parent), before)

    def test_the_record_area_is_never_an_output_directory(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), output_dir=str(p / ".game" / "gpos" / "out"))
        self.assertIn("UNSAFE_ARTIFACT_PATH", self.codes(result))
        self.assertFalse((p / ".game" / "gpos" / "out").exists())

    def test_the_source_itself_is_never_the_output(self):
        # A source named like an output, in the workspace the next request would use, is still refused.
        p = self.project()
        out = p / "reviews"
        out.mkdir()
        src = out / "frame.png"
        shutil.copyfile(MEDIA["av"], src)
        before = sha256(src)
        result = self.run_cap(fa.EXTRACT_FRAME, p, src, output_dir=str(out))
        self.assertRefused(result)
        self.assertEqual(sha256(src), before)


# ---------------------------------------------------------------- T  no overwrite

class T_NoOverwrite(MediaCase):
    def test_every_template_uses_n_and_never_y(self):
        for argv in self.templates():
            self.assertIn("-n", argv)
            self.assertNotIn("-y", argv)
            self.assertLess(argv.index("-n"), argv.index("-i"))

    def test_every_real_run_uses_n_and_never_y(self):
        p = self.project()
        src = self.source(p)
        with Recorder() as rec:
            for cap in TRANSFORMS:
                self.run_cap(cap, p, src)
        self.assertEqual(len(rec.media_runs), 3)
        for spec in rec.media_runs:
            self.assertIn("-n", spec.argv)
            self.assertNotIn("-y", spec.argv)

    def test_an_existing_output_is_refused_and_left_untouched(self):
        # FFmpeg's -n refuses to overwrite but still exits 0, so the adapter checks first.
        p = self.project()
        src = self.source(p)
        first = self.run_cap(fa.EXTRACT_FRAME, p, src, request_id="req-fixed-0001")
        self.assertSucceeded(first)
        frame = Path(first.artifacts[0].absolute_path)
        before = sha256(frame)
        with Recorder() as rec:
            again = self.run_cap(fa.EXTRACT_FRAME, p, src, request_id="req-fixed-0001",
                                 inputs={"timestamp_seconds": 2.5})
        self.assertRefused(again, "already holds frame.png")
        self.assertEqual(rec.media_runs, [])
        self.assertEqual(sha256(frame), before)

    def test_an_existing_symlink_at_the_output_path_is_refused(self):
        p = self.project()
        src = self.source(p)
        out = p / "reviews"
        out.mkdir()
        (out / "clip.mkv").symlink_to(src)
        result = self.run_cap(fa.EXTRACT_CLIP, p, src, output_dir=str(out))
        self.assertRefused(result, "already holds clip.mkv")

    @staticmethod
    def templates():
        return (fa.frame_argv("/in/a.mp4", "1.000000", "/ws/frame.png", 1),
                fa.clip_argv("/in/a.mp4", "0.000000", "1.000000", "/ws/clip.mkv", 1),
                fa.audio_argv("/in/a.mp4", "0.000000", "1.000000", "/ws/audio.wav", 1))


# ---------------------------------------------------------------- U  partial output and timeout

class U_PartialAndTimeout(MediaCase):
    def registry_with(self, adapter):
        registry = ToolRegistry(FW, allow_test_only=False)
        registry.register(adapter)
        return registry

    def test_output_at_the_size_limit_is_an_incomplete_artifact_never_evidence(self):
        # Real FFmpeg, with the code-level size seam lowered: -fs stops the write but exits 0.
        p = self.project()
        registry = self.registry_with(FfmpegAdapter(size_limits={"clip": 8192, "audio": 4096, "frame": 1024}))
        for cap in TRANSFORMS:
            result = self.run_cap(cap, p, self.source(p), registry=registry, revision=REVISION)
            self.assertEqual(result.status, tdg.FAILED, cap)
            self.assertIn("size limit", " ".join(self.messages(result)))
            self.assertIn("ARTIFACT_INCOMPLETE", self.codes(result))
            (art,) = result.artifacts
            self.assertFalse(art.complete)
            self.assertTrue(Path(art.absolute_path).exists())  # kept, never silently deleted
            self.assertTrue(result.mutation_performed)          # a file was written, and that is reported
            self.assertEqual(result.evidence_candidates, ())

    def test_a_timeout_mid_write_leaves_an_incomplete_artifact_and_no_evidence(self):
        script = "MISSING = set()" + FAKE_FFMPEG_HEADERS + (
            "out = argv[-1][len('file:'):]\n"
            "open(out, 'wb').write(b'\\x89PNG\\r\\n\\x1a\\n' + b'\\0' * 64)\n"
            "time.sleep(60)\n")
        exe = fake_tool(self.tmp, "ffmpeg", script)
        p = self.project()
        registry = self.registry_with(FfmpegAdapter(which=lambda n: str(exe)))
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), registry=registry, timeout=1.0, revision=REVISION)
        self.assertEqual(result.status, tdg.TIMED_OUT)
        (art,) = result.artifacts
        self.assertFalse(art.complete)
        self.assertTrue(result.mutation_performed)
        self.assertEqual(result.evidence_candidates, ())
        self.assertLess(result.duration_seconds, 30)

    def test_a_failure_after_a_partial_write_keeps_the_file_as_incomplete(self):
        script = "MISSING = set()" + FAKE_FFMPEG_HEADERS + (
            "out = argv[-1][len('file:'):]\nopen(out, 'wb').write(b'RIFF')\nsys.exit(1)\n")
        exe = fake_tool(self.tmp, "ffmpeg", script)
        p = self.project()
        result = self.run_cap(fa.EXTRACT_AUDIO, p, self.source(p),
                              registry=self.registry_with(FfmpegAdapter(which=lambda n: str(exe))))
        self.assertEqual(result.status, tdg.FAILED)
        self.assertFalse(result.artifacts[0].complete)
        self.assertEqual(result.evidence_candidates, ())

    def test_success_with_a_wrong_signature_is_not_evidence(self):
        script = "MISSING = set()" + FAKE_FFMPEG_HEADERS + (
            "out = argv[-1][len('file:'):]\nopen(out, 'wb').write(b'NOTAPNGATALL')\n")
        exe = fake_tool(self.tmp, "ffmpeg", script)
        p = self.project()
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p),
                              registry=self.registry_with(FfmpegAdapter(which=lambda n: str(exe))))
        self.assertEqual(result.status, tdg.FAILED)
        self.assertIn("format signature", " ".join(self.messages(result)))
        self.assertEqual(result.evidence_candidates, ())

    def test_success_without_any_output_is_a_failure(self):
        exe = fake_tool(self.tmp, "ffmpeg", "MISSING = set()" + FAKE_FFMPEG_HEADERS)
        p = self.project()
        result = self.run_cap(fa.EXTRACT_CLIP, p, self.source(p),
                              registry=self.registry_with(FfmpegAdapter(which=lambda n: str(exe))))
        self.assertEqual(result.status, tdg.FAILED)
        self.assertIn("wrote no output", " ".join(self.messages(result)))
        self.assertEqual((result.artifacts, result.evidence_candidates), ((), ()))
        self.assertFalse(result.mutation_performed)


# ---------------------------------------------------------------- V  private raw, public redacted

class V_Secrecy(MediaCase):
    def secret_source(self, p):
        return self.source(p, name=f"run-{SECRET_TOKEN}-password=hunter2.mp4")

    def assertClean(self, text, *extra):
        for needle in (SECRET_TOKEN, "hunter2", TAG_MARKER, *extra):
            self.assertNotIn(needle, text)

    def test_credential_shaped_names_and_metadata_never_reach_a_public_surface(self):
        p = self.project()
        src = self.secret_source(p)
        for cap in (pa.INSPECT,) + TRANSFORMS:
            result = self.run_cap(cap, p, src, revision=REVISION)
            self.assertSucceeded(result)
            text = self.public_json(result)
            self.assertClean(text, str(p))
            for cand in result.evidence_candidates:
                self.assertClean(json.dumps(cand.to_dict()))
        self.assertEqual(self.inspect(p, src).data["primary_video"]["width"], 320)

    def test_failures_scrub_the_path_before_the_foundation_redacts(self):
        p = self.project()
        src = p / "captures" / f"noise-{SECRET_TOKEN}-marker-{TAG_MARKER}.bin"
        src.write_bytes(bytes(range(256)) * 64)
        for cap in (pa.INSPECT, fa.EXTRACT_FRAME):
            result = self.run_cap(cap, p, src)
            self.assertEqual(result.status, tdg.FAILED)
            text = self.public_json(result)
            self.assertClean(text, str(p), "captures")  # the marker too: the whole path is replaced
        # ffprobe's reason names the file; it is reported by artifact id
        self.assertIn("<input src>", " ".join(self.messages(self.run_cap(pa.INSPECT, p, src))))

    def test_the_recorded_command_names_the_input_by_artifact_id(self):
        p = self.project()
        src = self.secret_source(p)
        for cap in (pa.INSPECT, fa.EXTRACT_FRAME):
            command = self.run_cap(cap, p, src).provenance.to_dict()["command"]
            self.assertIn("<input src>", command["argv"])
            self.assertClean(json.dumps(command), str(p), "captures")

    def test_derived_files_carry_no_source_metadata(self):
        p = self.project()
        src = self.source(p)
        for cap in TRANSFORMS:
            art = self.run_cap(cap, p, src).artifacts[0]
            body = Path(art.absolute_path).read_bytes()
            for needle in (TAG_MARKER, "hunter2", SECRET_TOKEN):
                self.assertNotIn(needle.encode(), body, cap)
            if cap != fa.EXTRACT_FRAME:
                tags = ffprobe_json(art.absolute_path, "-show_format")["format"].get("tags", {})
                self.assertNotIn("title", {k.lower() for k in tags})
                self.assertNotIn("comment", {k.lower() for k in tags})

    def test_the_process_payload_never_reaches_the_result(self):
        p = self.project()
        for cap in (pa.INSPECT,) + TRANSFORMS:
            result = self.run_cap(cap, p, self.secret_source(p) if cap == pa.INSPECT else self.source(p))
            self.assertEqual((result.stdout, result.stderr), ("", ""))
            self.assertNotIn("raw_", self.public_json(result))


# ---------------------------------------------------------------- W  explicit Git handoff

def git(repo, *args):
    return subprocess.run([GIT, *args], cwd=str(repo), env=FIXTURE_ENV, capture_output=True, text=True, check=True)


@unittest.skipIf(GIT is None, "the explicit handoff needs Git; the Git suite reports it as unavailable")
class W_GitHandoff(MediaCase):
    def repo_with_media(self):
        p = self.project()
        src = self.source(p)
        git(p, "init", "-q", "-b", "main")
        git(p, "add", "-A")
        git(p, "commit", "-q", "-m", "fixture")
        return p, src

    def test_the_resolved_revision_is_recorded_exactly_when_passed(self):
        p, src = self.repo_with_media()
        resolved = execute(self.registry, ExecutionRequest(adapter_id="git", capability_id=ga.RESOLVE_PROVENANCE,
                                                           subject=Subject("PROJECT", PROJECT_ID), project_root=str(p)))
        revision = resolved.data["repository_revision"]
        self.assertTrue(re.fullmatch(r"[0-9a-f]{40}", revision or ""), resolved.data)
        with Recorder() as rec:
            result = self.run_cap(fa.EXTRACT_FRAME, p, src, build_revision=revision, revision=revision,
                                  target_platform="MACOS")
        self.assertSucceeded(result)
        self.assertEqual(result.provenance.build_revision, revision)
        self.assertEqual(result.provenance.to_dict()["build_revision"], revision)
        self.assertEqual(result.evidence_candidates[0].provenance["build_revision"], revision)
        self.assertEqual({Path(s.executable).name for s in rec.specs}, {"ffmpeg"})  # FFmpeg never called Git

    def test_without_the_handoff_the_revision_stays_unknown(self):
        p, src = self.repo_with_media()
        with Recorder() as rec:
            result = self.run_cap(fa.EXTRACT_FRAME, p, src)
        self.assertSucceeded(result)
        self.assertIsNone(result.provenance.build_revision)
        self.assertIn("build_revision", result.provenance.to_dict()["unknown"])
        self.assertNotIn("git", {Path(s.executable).name for s in rec.specs})

    def test_the_media_modules_never_reference_git(self):
        for path in B_RealProbes.media_modules():
            self.assertNotIn("from ..git", path.read_text())
            self.assertNotIn("import git", path.read_text())


# ---------------------------------------------------------------- X  materialization

class X_Materialization(MediaCase):
    def materialized(self, cap, ctx="TARGET_RUNTIME", **kwargs):
        p = self.project()
        kwargs.setdefault("target_platform", "MACOS")
        kwargs.setdefault("build_revision", REVISION)
        result = self.run_cap(cap, p, self.source(p), ctx=ctx, revision=REVISION, **kwargs)
        self.assertSucceeded(result)
        (cand,) = result.evidence_candidates
        self.assertTrue(cand.materializable)
        record, problems = tev.materialize(FW, cand, result.artifacts, f"EV-MEDIA-{OUTPUT[cap][0].upper()}")
        self.assertEqual(problems, [])
        return result, record

    def test_every_transform_materializes_a_schema_valid_record(self):
        version = FfmpegAdapter().probe().tool_version
        for cap in TRANSFORMS:
            result, record = self.materialized(cap)
            self.assertEqual(FW.validators["evidence"].errors(record), [])
            self.assertEqual(record["type"], EVIDENCE[cap])
            prov = record["provenance"]
            self.assertEqual(prov["capture_context"], "TARGET_RUNTIME")
            self.assertEqual(prov["subject_revision"], REVISION)
            self.assertEqual(prov["build_revision"], REVISION)
            self.assertEqual(prov["tool_version"], version)
            art = result.artifacts[0]
            self.assertEqual(record["artifacts"][0]["hash"], f"sha256:{sha256(art.absolute_path)}")
            self.assertEqual(record["artifacts"][0]["uri"], art.path)
            self.assertEqual(record["source"], {"kind": "TOOL", "id": "ffmpeg"})
            self.assertTrue(record["limitations"])
            text = json.dumps(record)
            for word in ("PASS", "gate", "reviewer", "assessor", "approved", "HUMAN_EVIDENCE"):
                self.assertNotIn(word, text)

    def test_dcc_render_and_editor_sources_materialize(self):
        for cap, ctx in ((fa.EXTRACT_FRAME, "DCC_RENDER"), (fa.EXTRACT_CLIP, "DCC_RENDER"),
                         (fa.EXTRACT_AUDIO, "EDITOR"), (fa.EXTRACT_FRAME, "AUTOMATED_TEST")):
            _, record = self.materialized(cap, ctx=ctx, target_platform=None, build_revision=None)
            self.assertEqual(record["provenance"]["capture_context"], ctx)

    def test_a_runtime_source_without_platform_and_build_cannot_materialize(self):
        p = self.project()
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), revision=REVISION)
        record, problems = tev.materialize(FW, result.evidence_candidates[0], result.artifacts, "EV-MEDIA-1")
        self.assertIsNone(record)
        self.assertTrue(problems)

    def test_the_inherited_context_cannot_be_overridden_at_materialization(self):
        result, _ = self.materialized(fa.EXTRACT_FRAME)
        record, problems = tev.materialize(FW, result.evidence_candidates[0], result.artifacts, "EV-MEDIA-2",
                                           extra_provenance={"capture_context": "OFFLINE_ANALYSIS"})
        self.assertIsNone(record)
        self.assertTrue(any("owned by the foundation" in p for p in problems))

    def test_nothing_is_written_to_the_record_area(self):
        p = self.project()
        before = tree_digest(p / ".game" / "gpos")
        result = self.run_cap(fa.EXTRACT_FRAME, p, self.source(p), revision=REVISION, target_platform="MACOS",
                              build_revision=REVISION)
        tev.materialize(FW, result.evidence_candidates[0], result.artifacts, "EV-MEDIA-3")
        self.assertEqual(tree_digest(p / ".game" / "gpos"), before)


# ---------------------------------------------------------------- Y  security surface

class Y_SecuritySurface(MediaCase):
    INPUT, WS = "/in/src.mp4", "/ws"

    def test_the_authorized_templates_are_exact(self):
        restrictions = ("-protocol_whitelist", "file", "-format_whitelist",
                        "mov,matroska,avi,mpegts,wav,mp3,flac,ogg")
        common = ("-hide_banner", "-loglevel", "error", "-nostdin", "-n") + restrictions
        clean = ("-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact")
        self.assertEqual(pa.inspect_argv(self.INPUT), ("-v", "error") + restrictions + (
            "-show_entries", "format=format_name,duration:stream=index,codec_type,codec_name,width,height,pix_fmt,"
                             "avg_frame_rate,sample_rate,channels,channel_layout,duration",
            "-of", "json", "-i", "file:/in/src.mp4"))
        self.assertEqual(fa.frame_argv(self.INPUT, "1.500000", "/ws/frame.png", 67108864), common + (
            "-ss", "1.500000", "-accurate_seek", "-i", "file:/in/src.mp4", "-map", "0:v:0", "-frames:v", "1",
            "-an", "-sn", "-dn") + clean + ("-c:v", "png", "-f", "image2pipe", "-fs", "67108864", "file:/ws/frame.png"))
        self.assertEqual(fa.clip_argv(self.INPUT, "0.500000", "2.000000", "/ws/clip.mkv", 4294967296), common + (
            "-ss", "0.500000", "-accurate_seek", "-i", "file:/in/src.mp4", "-t", "2.000000", "-map", "0:v:0",
            "-map", "0:a:0?", "-sn", "-dn") + clean + ("-c:v", "ffv1", "-c:a", "pcm_s16le", "-f", "matroska",
                                                        "-fs", "4294967296", "file:/ws/clip.mkv"))
        self.assertEqual(fa.audio_argv(self.INPUT, "0.000000", "1.000000", "/ws/audio.wav", 268435456), common + (
            "-ss", "0.000000", "-accurate_seek", "-i", "file:/in/src.mp4", "-t", "1.000000", "-map", "0:a:0",
            "-vn", "-sn", "-dn") + clean + ("-c:a", "pcm_s16le", "-f", "wav", "-fs", "268435456",
                                            "file:/ws/audio.wav"))

    def test_every_real_process_matches_its_template_exactly(self):
        p = self.project()
        src = self.source(p)
        registry = default_registry(FW)
        with Recorder() as rec:
            results = {cap: self.run_cap(cap, p, src, registry=registry) for cap in (pa.INSPECT,) + TRANSFORMS}
        ffmpeg = registry.ready("ffmpeg")[0].tool_path
        ffprobe = registry.ready("ffprobe")[0].tool_path
        runs = rec.media_runs
        self.assertEqual(len(runs), 4)
        for spec in runs:
            self.assertIn(spec.executable, (ffmpeg, ffprobe))
            self.assertFalse(getattr(spec, "shell", False))
        ws = {cap: Path(r.artifacts[0].absolute_path).parent for cap, r in results.items() if r.artifacts}
        self.assertEqual(tuple(runs[0].argv), pa.inspect_argv(src))
        self.assertEqual(tuple(runs[1].argv), fa.frame_argv(src, "1.500000", ws[fa.EXTRACT_FRAME] / "frame.png",
                                                            fa.SIZE_LIMITS["frame"]))
        self.assertEqual(tuple(runs[2].argv), fa.clip_argv(src, "0.500000", "1.000000", ws[fa.EXTRACT_CLIP] / "clip.mkv",
                                                           fa.SIZE_LIMITS["clip"]))
        self.assertEqual(tuple(runs[3].argv), fa.audio_argv(src, "0.000000", "1.250000",
                                                            ws[fa.EXTRACT_AUDIO] / "audio.wav", fa.SIZE_LIMITS["audio"]))

    def test_no_filter_network_or_overwrite_option_is_ever_authorized(self):
        forbidden = {"-y", "-filter", "-filter_complex", "-vf", "-af", "-lavfi", "-filter_script", "-i_qfactor",
                     "-dump_attachment", "-attach", "-f_strict", "-safe", "-protocols", "-listen", "-headers",
                     "-http_proxy", "-user_agent", "-reconnect", "-rtsp_transport"}
        argvs = list(T_NoOverwrite.templates()) + [pa.inspect_argv("/in/a.mp4")] + [fa.VERSION_ARGV] + list(fa.HELP_ARGV)
        for argv in argvs:
            self.assertEqual(set(argv) & forbidden, set(), argv)
            for token in argv:
                for protocol in media.NETWORK_PROTOCOLS:
                    self.assertFalse(token.startswith(protocol + ":"), token)
            self.assertEqual(argv.count("-i"), 1 if "-i" in argv else 0)

    def test_every_safe_demuxer_is_a_real_self_contained_demuxer(self):
        listing = subprocess.run([FFMPEG, "-hide_banner", "-demuxers"], capture_output=True, text=True).stdout
        names = set()
        for line in listing.splitlines():
            match = re.match(r"^\s*D\S*\s+(\S+)\s", line)
            if match:
                names.update(match.group(1).split(","))
        for name in media.SAFE_DEMUXERS:
            self.assertIn(name, names)
        self.assertEqual(set(media.SAFE_DEMUXERS) & {"hls", "dash", "concat", "image2", "sdp", "rtsp", "lavfi",
                                                     "avfoundation", "tee", "webm_dash_manifest", "applehttp"}, set())

    def test_the_adapter_constructors_expose_no_executable_or_argv_seam_to_a_request(self):
        import inspect
        self.assertEqual(list(inspect.signature(FfmpegAdapter).parameters), ["which", "size_limits"])
        self.assertEqual(list(inspect.signature(FfprobeAdapter).parameters), ["which", "capture_bytes"])
        fields = set(ExecutionRequest.__dataclass_fields__)
        self.assertEqual(fields & {"executable", "argv", "tool_path", "command"}, set())

    def test_the_media_modules_never_use_a_shell_or_start_a_process_themselves(self):
        for path in B_RealProbes.media_modules():
            text = path.read_text()
            for needle in ("shell=True", "os.system", "os.popen", "Popen", "os.exec", "os.spawn", "pty."):
                self.assertNotIn(needle, text, f"{path}: {needle}")

    def test_the_transform_reads_only_its_declared_inputs(self):
        tree = ast.parse((ROOT / "gpos" / "tools" / "ffmpeg" / "adapter.py").read_text())
        keys = {node.value for node in ast.walk(tree) if isinstance(node, ast.Subscript)
                for node in [node.slice] if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertLessEqual({k for k in keys if k.endswith("_seconds")},
                             {"timestamp_seconds", "start_seconds", "duration_seconds"})


# ---------------------------------------------------------------- Z  CLI

class Z_Cli(MediaCase):
    def cli(self, *argv):
        from gpos.tools import cli as tool_cli
        buffer = io.StringIO()
        code = tool_cli.main(list(argv), stdout=buffer)
        return code, buffer.getvalue()

    def test_list(self):
        code, out = self.cli("list")
        self.assertEqual(code, 0)
        self.assertIn("3 tool adapter", out)
        self.assertIn("ffmpeg 1.0.0 · MEDIA · 3 capabilities", out)
        self.assertIn("ffprobe 1.0.0 · MEDIA · 1 capabilities", out)
        self.assertIn("git 1.0.0 · VERSION_CONTROL · 2 capabilities", out)
        self.assertNotIn("TEST_ONLY", out)
        self.assertNotIn("synthetic", out)

    def test_describe_and_capabilities(self):
        for adapter, caps in (("ffprobe", ["ffprobe.inspect"]),
                              ("ffmpeg", ["ffmpeg.extract-audio", "ffmpeg.extract-clip", "ffmpeg.extract-frame"])):
            code, out = self.cli("describe", adapter)
            self.assertEqual(code, 0)
            for text in caps + ["network FORBIDDEN", "MEDIA"]:
                self.assertIn(text, out)
            code, out = self.cli("capabilities", adapter)
            self.assertEqual(code, 0)
            code, out = self.cli("capabilities", adapter, "--format", "json")
            self.assertEqual(code, 0)
            self.assertEqual(sorted(c["id"] for c in json.loads(out)["capabilities"]["capabilities"]), caps)

    def test_probe(self):
        for adapter in ("ffprobe", "ffmpeg"):
            code, out = self.cli("probe", adapter)
            self.assertEqual(code, 0, out)
            self.assertIn("AVAILABLE", out)
            code, out = self.cli("probe", adapter, "--format", "json")
            probe = json.loads(out)["probe"]
            self.assertEqual(probe["status"], "AVAILABLE")
            self.assertTrue(os.path.isabs(probe["tool_path"]))

    def test_execute_inspect_and_a_frame_through_the_cli(self):
        p = self.project()
        src = self.secret_source(p)
        code, out = self.cli("execute", "--adapter", "ffprobe", "--capability", "ffprobe.inspect", "--project", str(p),
                             "--subject-ref", "T-1", "--input-artifact", f"src={src}", "--format", "json")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["result"]["data"]["primary_video"]["width"], 320)
        self.assertClean(out)
        code, out = self.cli("execute", "--adapter", "ffmpeg", "--capability", "ffmpeg.extract-frame", "--project",
                             str(p), "--subject-ref", "T-1", "--input-artifact", f"src={src}",
                             "--input-artifact-context", "src=TARGET_RUNTIME", "--input", "timestamp_seconds=1",
                             "--allow-mutation", "--format", "json")
        self.assertEqual(code, 0, out)
        result = json.loads(out)["result"]
        self.assertEqual(result["evidence_candidates"][0]["capture_context"], "TARGET_RUNTIME")
        self.assertClean(out)
        code, out = self.cli("execute", "--adapter", "ffmpeg", "--capability", "ffmpeg.extract-frame", "--project",
                             str(p), "--subject-ref", "T-1", "--input-artifact", f"src={src}",
                             "--input-artifact-context", "src=TARGET_RUNTIME", "--input", "timestamp_seconds=1",
                             "--allow-mutation")
        self.assertEqual(code, 0, out)
        self.assertClean(out)

    def test_cli_refusals_have_their_exit_codes(self):
        p = self.project()
        src = self.source(p)
        base = ("execute", "--adapter", "ffmpeg", "--capability", "ffmpeg.extract-frame", "--project", str(p),
                "--subject-ref", "T-1", "--input-artifact", f"src={src}", "--input", "timestamp_seconds=1")
        code, _ = self.cli(*base, "--input-artifact-context", "src=TARGET_RUNTIME")
        self.assertEqual(code, tdg.EXIT_FOR[tdg.INVALID_REQUEST])  # no consent
        code, _ = self.cli(*base, "--allow-mutation")
        self.assertEqual(code, tdg.EXIT_FOR[tdg.INVALID_REQUEST])  # no capture context
        code, out = self.cli(*base, "--input-artifact-context", "src=TARGET_RUNTIME", "--dry-run")
        self.assertEqual(code, 0, out)

    def secret_source(self, p):
        return self.source(p, name=f"run-{SECRET_TOKEN}-password=hunter2.mp4")

    def assertClean(self, text):
        for needle in (SECRET_TOKEN, "hunter2", TAG_MARKER):
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    if FFMPEG is None or FFPROBE is None:
        print("FFMPEG_RUNTIME_UNAVAILABLE_FOR_PHASE2C2: these are real integration tests and require ffmpeg and "
              "ffprobe on PATH")
        sys.exit(1)
    result = unittest.main(verbosity=1, exit=False).result
    shutil.rmtree(_HOME, ignore_errors=True)
    versions = [subprocess.run([exe, "-version"], capture_output=True, text=True).stdout.splitlines()[0].split(" Copyright")[0]
                for exe in (FFMPEG, FFPROBE)]
    print(f"GPOS media adapter tests (real {versions[0]}; {versions[1]})")
    sys.exit(0 if result.wasSuccessful() else 1)
