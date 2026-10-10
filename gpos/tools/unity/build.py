"""Unity Build Core (Phase 2C-7, alpha.21): the batch build plane's fixed command, request and response files,
configuration token, payload validation, payload tree digest and build manifest.

Two capabilities, both batch-plane, STATELESS, MUTATING (each opens the project in a fresh batch-mode Editor), under
the EXECUTION single-writer lease on `EDITOR_PROJECT:<root>` with the read-only project-lock proof:

    unity.inspect-build-configuration   read the existing build configuration; buildable or not, and its token
    unity.build-player                  build exactly that configuration into this execution's workspace

Scope: macOS Standalone Player, Mono, the already active target, either the active custom Build Profile for exactly
that (built with BuildPlayerWithProfileOptions, never activated, edited or chosen) or the classic Editor build
configuration. Development is whatever that configuration says; the caller supplies no target, development flag,
scene, option, define, output path or build id.

The Editor runs one fixed GPOS method in the audited bridge package (`BUILD_ENTRY_METHOD`), named by the adapter and
never by a request; the request travels in `build-request.json` in the execution workspace, named by one fixed flag.
The execution workspace `.game/gpos-runtime/tool-output/unity/<request id>/` is the whole, persistent build root:

    build-request.json   build-started.json   build-response.json   editor.log   upm.log   upm-*.toml
    staging/Player.app   (Unity writes here only)
    payload/Player.app   (the validated payload, after one rename)
    build-manifest.json  (written last; the only foundation artifact of a build besides the editor log)

A payload without its manifest is not a completed build. The `.app` directory is never an artifact: the manifest
binds it by a bounded, canonical tree digest that a later consumer recomputes before using the payload.

`build_id = "build-" + request_id`. Git is never run here: `build_revision` is caller-supplied provenance, bound to
the repository only by the surrounding workflow (resolve-provenance before inspection, again before the build, and
again after it). This module starts no process and imports no process or Git module.
"""

import hashlib
import json
import os
import plistlib
import re
import stat
import sys
from pathlib import Path

from .. import redaction
from .sources import ABSOLUTE_PATH   # the same absolute-path rule as the alpha.20 compiler messages

INSPECT_BUILD = "unity.inspect-build-configuration"
BUILD = "unity.build-player"
CAPABILITY_IDS = (INSPECT_BUILD, BUILD)

BUILD_ENTRY_METHOD = "Gpos.LiveBridge.Build.BuildEntry.Run"
REQUEST_FLAG = "-gposBuildRequest"
REQUEST_NAME, STARTED_NAME, RESPONSE_NAME = "build-request.json", "build-started.json", "build-response.json"
LOG_NAME, UPM_LOG_NAME = "editor.log", "upm.log"
STAGING, PAYLOAD, APP = "staging", "payload", "Player.app"
MANIFEST_NAME = "build-manifest.json"

REQUEST_SCHEMA = "gpos.unity.build-request/1"
RESPONSE_SCHEMA = "gpos.unity.build-response/1"
STARTED_SCHEMA = "gpos.unity.build-started/1"
CONFIG_SCHEMA = "gpos.unity.build-config/1"
MANIFEST_SCHEMA = "gpos.unity.build-manifest/1"
TREE_ALGORITHM = "gpos.unity.payload-tree/1"

TARGET = "StandaloneOSX"
# The foundation's request-id grammar narrowed so that `build-<request id>` is a stable lower-case id.
BUILD_REQUEST_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
BUILD_ID = re.compile(r"build-[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
TOKEN = re.compile(r"[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")   # the Git adapter's SHA-1 or SHA-256 object form
GUID = re.compile(r"[0-9a-f]{32}")

CONFIG_KEYS = {"schema", "unity_version", "active_target", "standalone_subtarget", "scripting_backend",
               "application_identifier", "development", "mode", "scenes", "files", "debug", "output", "profile"}

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_STARTED_BYTES = 4096
MAX_ENTRIES = 20000                  # payload tree bounds
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_DEPTH = 32
MAX_REL_BYTES = 1024
MAX_PLIST_BYTES = 1024 * 1024
MAX_BOOT_CONFIG_BYTES = 64 * 1024
CHUNK = 1024 * 1024

# Refusal rules of the build entry -> the diagnostic that reports them; anything else is an unsupported configuration.
RULE_CODES = {
    "TARGET_MODULE_MISSING": "BUILD_TARGET_MODULE_MISSING",
    "TARGET_NOT_ACTIVE": "BUILD_TARGET_NOT_ACTIVE",
    "CONFIGURATION_CHANGED": "BUILD_CONFIGURATION_CHANGED",
    "SCRIPTS_FAILED": "BUILD_COMPILE_FAILED",
}
OUTCOMES = {"INSPECT": ("INSPECTED",), "BUILD": ("REFUSED", "BUILT")}   # what the entry may answer per operation
RULES = ("TARGET_MODULE_MISSING", "TARGET_NOT_ACTIVE", "EDITOR_NOT_SETTLED", "SCRIPTS_FAILED", "PROFILE_NOT_MACOS_PLAYER",
         "PROFILE_FIELD_UNREADABLE", "PROFILE_PLAYER_SETTINGS_OVERRIDE", "SUBTARGET_NOT_PLAYER", "BACKEND_NOT_MONO",
         "DEBUG_STATE_UNSUPPORTED", "DEVELOPMENT_AMBIGUOUS", "OUTPUT_NOT_PLAYER_APP", "NO_SCENES", "SCENE_INVALID",
         "TOO_MANY_SCENES", "FILE_UNREADABLE", "CONFIGURATION_CHANGED")
if sys.platform == "win32":
    RULES += ("WINDOWS_PROFILE_UNSUPPORTED", "WINDOWS_OUTPUT_UNREADABLE", "WINDOWS_VERSION_UNSUPPORTED")

LIMITATIONS = (
    "build_revision is caller-supplied provenance: this build did not read Git. The Git-to-build binding is the "
    "qualified-build workflow's pre/post proof (git.resolve-provenance before inspection, before the build and after "
    "it, all returning the same clean revision), not an atomic repository lock held by the Unity adapter.",
    "An operational build fact only: not runtime, visual, motion, audio, device, performance, test or Human evidence; "
    "a successful build does not imply that any test passed.",
    "macOS builds are not byte-reproducible (the ad-hoc code signature and boot.config differ between builds of the "
    "same inputs); the Unity build GUID identifies a data build, not this invocation.",
    "Project Editor code, including build callbacks, ran in the batch Editor during the build.",
    "The payload is bound by its tree digest as of publication; a consumer must recompute it before using the payload.",
)


class ResponseProblem(Exception):
    """The build entry's response is missing, malformed or does not belong to this request."""


class PayloadProblem(Exception):
    """The built payload failed validation or a bound of the payload tree digest."""


def build_id(request_id):
    return "build-" + request_id


# ---------------------------------------------------------------- the fixed command

def build_argv(project, workspace):
    """The whole batch command: fixed, with only the adapter-owned project and workspace paths in it."""
    workspace = Path(workspace)
    return ("-batchmode", "-projectPath", str(project), "-logFile", str(workspace / LOG_NAME),
            "-upmLogFile", str(workspace / UPM_LOG_NAME), "-cacheServerEnableDownload", "false",
            "-cacheServerEnableUpload", "false", "-executeMethod", BUILD_ENTRY_METHOD,
            REQUEST_FLAG, str(workspace / REQUEST_NAME))


def unfresh(workspace):
    """The names already in the workspace (sorted); a build or inspection needs a fresh, empty one."""
    return sorted(os.listdir(workspace))


def write_request(workspace, operation, request_id, token=None):
    body = {"schema": REQUEST_SCHEMA, "operation": operation, "request_id": request_id}
    if operation == "BUILD":
        body["expected_configuration_token"] = token
    with open(Path(workspace) / REQUEST_NAME, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(body, sort_keys=True))


# ---------------------------------------------------------------- canonical configuration and its token

def canonical(value):
    """The exact bytes the build entry hashes: sorted keys, no whitespace, ASCII escapes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def token_of(configuration):
    return hashlib.sha256(canonical(configuration).encode("ascii")).hexdigest()


# ---------------------------------------------------------------- the response

def _read_bounded(path, bound):
    if sys.platform == "win32":
        from . import build_win32 as wb
        try:
            return wb.read_json(path, bound)
        except wb.VALIDATION_ERRORS as exc:
            raise ResponseProblem(str(exc)) from None
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise ResponseProblem(f"{Path(path).name} is not a regular file")
    if st.st_size > bound:
        raise ResponseProblem(f"{Path(path).name} is larger than {bound} bytes")
    with open(path, "rb") as fh:
        data = fh.read(bound + 1)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ResponseProblem(f"{Path(path).name} is not UTF-8 JSON ({exc})") from None


def started(workspace):
    """True when the build entry recorded that it was about to call BuildPlayer (whatever the file's state)."""
    try:
        os.lstat(Path(workspace) / STARTED_NAME)
        return True
    except FileNotFoundError:
        return False


def _is(value, kind):
    return isinstance(value, kind) and not (kind is int and isinstance(value, bool))


def read_response(workspace, request_id, operation):
    """The build entry's answer, checked strictly; raises ResponseProblem when it cannot be trusted."""
    if sys.platform == "win32":
        from . import build_win32 as wb
        try:
            return wb.read_response(workspace, request_id, operation)
        except wb.VALIDATION_ERRORS as exc:
            raise ResponseProblem(str(exc)) from None
    path = Path(workspace) / RESPONSE_NAME
    if not os.path.lexists(path):
        raise ResponseProblem("the build entry wrote no response")
    r = _read_bounded(path, MAX_RESPONSE_BYTES)
    keys = {"schema", "operation", "request_id", "unity_version", "outcome", "buildable", "problems", "configuration",
            "configuration_token", "refusal", "build", "post"}
    if not isinstance(r, dict) or set(r) != keys:
        raise ResponseProblem("the response does not hold exactly the response keys")
    if (r["schema"], r["operation"], r["request_id"]) != (RESPONSE_SCHEMA, operation, request_id):
        raise ResponseProblem("the response belongs to another schema, operation or request")
    allowed = OUTCOMES[operation]
    if r["outcome"] not in allowed:
        raise ResponseProblem(f"outcome {r['outcome']!r} is not possible for {operation}")
    if not _is(r["buildable"], bool) or not isinstance(r["problems"], list) or len(r["problems"]) > 64:
        raise ResponseProblem("buildable/problems are malformed")
    for p in r["problems"]:
        if not (isinstance(p, dict) and set(p) == {"rule", "message"} and p["rule"] in RULES and _is(p["message"], str)):
            raise ResponseProblem("a problem is malformed or names an unknown rule")
    if r["buildable"] != (not r["problems"]):
        raise ResponseProblem("buildable contradicts the problems")
    configuration, token = r["configuration"], r["configuration_token"]
    if not isinstance(configuration, dict) or set(configuration) != CONFIG_KEYS or configuration["schema"] != CONFIG_SCHEMA:
        raise ResponseProblem("the configuration is missing, has other keys or another schema")
    if not (_is(configuration["development"], bool) and _is(configuration["mode"], str)
            and isinstance(configuration["scenes"], list) and _is(configuration["application_identifier"], (str, type(None)))
            and (configuration["profile"] is None or (isinstance(configuration["profile"], dict)
                                                      and _is(configuration["profile"].get("path"), (str, type(None)))))):
        raise ResponseProblem("a configuration fact has the wrong type")
    if not (_is(token, str) and TOKEN.fullmatch(token)) or token_of(configuration) != token:
        raise ResponseProblem("the configuration token does not match the canonical configuration")
    if r["outcome"] == "REFUSED":
        ref = r["refusal"]
        if not (isinstance(ref, dict) and set(ref) == {"rule", "message"} and ref["rule"] in RULES) or r["build"] is not None:
            raise ResponseProblem("a refusal is malformed")
    elif r["refusal"] is not None:
        raise ResponseProblem("a refusal accompanies an outcome that is not REFUSED")
    if r["outcome"] == "BUILT":
        _check_build(r["build"])
        post = r["post"]
        if not (isinstance(post, dict) and set(post) == {"configuration_token", "active_target", "profile_path",
                                                         "development"}):
            raise ResponseProblem("the post-build facts are malformed")
    elif r["build"] is not None or r["post"] is not None:
        raise ResponseProblem("build facts accompany an outcome that is not BUILT")
    return r


def _check_build(b):
    if b is None:
        return   # BuildPlayer returned no report: the outcome is unknown, decided by the caller
    keys = {"result", "guid", "platform", "output_path", "total_errors", "total_warnings", "total_size", "duration_ms",
            "development_observed", "options_text", "messages", "error_message_count"}
    if sys.platform == "win32":
        keys = keys | {"files"}
        if not isinstance(b, dict) or not isinstance(b.get("files"), list) or len(b["files"]) > MAX_ENTRIES:
            raise ResponseProblem("unbounded Windows BuildReport files")
    if not isinstance(b, dict) or set(b) != keys:
        raise ResponseProblem("the build report facts are malformed")
    for k in ("total_errors", "total_warnings", "total_size", "duration_ms", "error_message_count"):
        if not _is(b[k], int) or b[k] < 0:
            raise ResponseProblem(f"build fact {k} is not a non-negative integer")
    if not (_is(b["development_observed"], bool) and _is(b["result"], str) and _is(b["guid"], str)
            and _is(b["platform"], str) and _is(b["output_path"], str) and _is(b["options_text"], str)):
        raise ResponseProblem("a build fact has the wrong type")
    if not isinstance(b["messages"], list) or len(b["messages"]) > 16:
        raise ResponseProblem("the build messages are unbounded")
    for m in b["messages"]:
        if not (isinstance(m, dict) and set(m) == {"step", "type", "text"} and all(_is(m[k], str) for k in m)
                and len(m["step"]) <= 120 and len(m["text"]) <= 400):
            raise ResponseProblem("a build message is malformed or unbounded")


# ---------------------------------------------------------------- public text

MAX_STEP_CHARS, MAX_MESSAGE_CHARS, MAX_PROBLEM_CHARS = 120, 400, 300   # the entry's bounds, kept after cleaning


def clean_message(text, bases, bound):
    """A BuildReport or entry text as it may leave GPOS: paths inside `bases` (the Unity project, then the GPOS root,
    each as given and resolved) become relative, the text passes the GPOS redaction boundary, any absolute path left
    becomes <path>, and the result is clipped to `bound` again. Raw BuildReport text never reaches a result."""
    if not isinstance(text, str):
        return ""
    for base in bases:
        text = text.replace(base.rstrip("/") + "/", "")
    text, _ = redaction.redact(text)
    text = ABSOLUTE_PATH.sub("<path>", text)
    return text[:bound]


def path_bases(*paths):
    """The spellings of `paths` to relativize, longest first: each as given and resolved."""
    out = set()
    for p in paths:
        if p:
            out.add(str(p))
            out.add(os.path.realpath(p))
    return sorted(out, key=len, reverse=True)


# ---------------------------------------------------------------- the payload

def payload_tree(root):
    """{algorithm, entries, bytes, digest} of a payload directory, canonical and bounded.

    Walked with lstat only, never following a link. One line per entry, sorted by the UTF-8 bytes of its path
    relative to `root`:  `D\\t<rel>` a directory, `F\\t<rel>\\t<size>\\t<sha256>` a regular file,
    `L\\t<rel>\\t<target>` a relative link whose target stays inside the payload. Anything else (an absolute or
    escaping link, a special file, a name with a tab, newline, NUL or non-UTF-8 bytes) is refused, and so is a tree
    beyond the entry, byte, depth or path bounds. digest = sha256("<algorithm>\\n" + each line + "\\n").
    """
    root = Path(root)
    st = os.lstat(root)
    if not stat.S_ISDIR(st.st_mode):
        raise PayloadProblem(f"{root.name} is not a directory")
    lines, total = [], 0
    stack = [(os.fsencode(root), b"", 0)]
    while stack:
        directory, prefix, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise PayloadProblem(f"the payload is deeper than {MAX_DEPTH} levels")
        with os.scandir(directory) as it:
            names = sorted(e.name for e in it)
        for name in names:
            rel = prefix + name
            try:
                text = rel.decode("utf-8")
            except UnicodeDecodeError:
                raise PayloadProblem("a payload path is not UTF-8") from None
            if any(c in text for c in "\t\n\r\x00") or len(rel) > MAX_REL_BYTES:
                raise PayloadProblem(f"payload path {text[:80]!r} has a control character or is too long")
            full = os.path.join(directory, name)
            est = os.lstat(full)
            if stat.S_ISLNK(est.st_mode):
                target = os.readlink(full)
                target_text = os.fsdecode(target)
                if (os.path.isabs(target_text) or any(c in target_text for c in "\t\n\r\x00")
                        or not _inside(text, target_text)):
                    raise PayloadProblem(f"payload link {text} points outside the payload")
                lines.append(f"L\t{text}\t{target_text}")
            elif stat.S_ISDIR(est.st_mode):
                lines.append(f"D\t{text}")
                stack.append((full, rel + b"/", depth + 1))
            elif stat.S_ISREG(est.st_mode):
                total += est.st_size
                if total > MAX_TOTAL_BYTES:
                    raise PayloadProblem(f"the payload is larger than {MAX_TOTAL_BYTES} bytes")
                lines.append(f"F\t{text}\t{est.st_size}\t{_sha(full)}")
            else:
                raise PayloadProblem(f"payload entry {text} is not a regular file, directory or link")
            if len(lines) > MAX_ENTRIES:
                raise PayloadProblem(f"the payload has more than {MAX_ENTRIES} entries")
    lines.sort(key=lambda line: line.split("\t")[1].encode("utf-8"))
    digest = hashlib.sha256((TREE_ALGORITHM + "\n" + "".join(line + "\n" for line in lines)).encode("utf-8"))
    return {"algorithm": TREE_ALGORITHM, "entries": len(lines), "bytes": total, "digest": digest.hexdigest()}


def _inside(rel, target):
    """True when a relative link at `rel` (payload-relative) resolves lexically inside the payload."""
    parts = rel.split("/")[:-1]
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return False
            parts.pop()
        else:
            parts.append(part)
    return True


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)


def _regular(path, bound, what):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise PayloadProblem(f"{what} is missing") from None
    if not stat.S_ISREG(st.st_mode):
        raise PayloadProblem(f"{what} is not a regular file")
    if bound is not None and st.st_size > bound:
        raise PayloadProblem(f"{what} is larger than {bound} bytes")
    return st


def validate_app(app, bundle_identifier, guid):
    """{bundle_identifier, executable, bundle_version} of a macOS Player .app, or PayloadProblem."""
    app = Path(app)
    st = os.lstat(app)
    if not stat.S_ISDIR(st.st_mode):
        raise PayloadProblem(f"{app.name} is not a real directory")
    info = app / "Contents" / "Info.plist"
    _regular(info, MAX_PLIST_BYTES, "Contents/Info.plist")
    try:
        with open(info, "rb") as fh:
            plist = plistlib.load(fh)
    except Exception as exc:   # plistlib raises several types for malformed input
        raise PayloadProblem(f"Contents/Info.plist does not parse ({type(exc).__name__})") from None
    if not isinstance(plist, dict):
        raise PayloadProblem("Contents/Info.plist is not a dictionary")
    ident, exe = plist.get("CFBundleIdentifier"), plist.get("CFBundleExecutable")
    if ident != bundle_identifier:
        raise PayloadProblem(f"CFBundleIdentifier {ident!r} is not the inspected application identifier "
                             f"{bundle_identifier!r}")
    if not (isinstance(exe, str) and re.fullmatch(r"[^/\x00-\x1f]{1,255}", exe) and exe not in (".", "..")):
        raise PayloadProblem("CFBundleExecutable is not one safe file name")
    est = _regular(app / "Contents" / "MacOS" / exe, None, f"Contents/MacOS/{exe}")
    if not est.st_mode & stat.S_IXUSR:
        raise PayloadProblem(f"Contents/MacOS/{exe} is not executable")
    boot = app / "Contents" / "Resources" / "Data" / "boot.config"
    _regular(boot, MAX_BOOT_CONFIG_BYTES, "Contents/Resources/Data/boot.config")
    lines = [line for line in boot.read_bytes().decode("utf-8", errors="replace").splitlines()
             if line.startswith("build-guid=")]
    if lines != [f"build-guid={guid}"]:
        raise PayloadProblem("boot.config does not carry exactly the BuildReport's build GUID")
    version = plist.get("CFBundleShortVersionString")
    return {"bundle_identifier": ident, "executable": exe, "bundle_version": version if isinstance(version, str) else None}


# ---------------------------------------------------------------- publication

def publish(workspace):
    """Commit the validated payload: one rename of staging/ to payload/ (never over an existing payload)."""
    workspace = Path(workspace)
    if os.path.lexists(workspace / PAYLOAD):
        raise FileExistsError(f"{PAYLOAD} already exists")
    os.rename(workspace / STAGING, workspace / PAYLOAD)
    _fsync_dir(workspace)


def write_manifest(workspace, manifest):
    """Write build-manifest.json last and once: a temporary file, fsync, a hard link that never replaces a file."""
    workspace = Path(workspace)
    final, temporary = workspace / MANIFEST_NAME, workspace / (MANIFEST_NAME + ".tmp")
    data = (json.dumps(manifest, sort_keys=True, indent=1) + "\n").encode("utf-8")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(temporary, final)
    finally:
        os.unlink(temporary)
    _fsync_dir(workspace)
    return final


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def revalidate(workspace):
    """(manifest, None) when a completed build's manifest still binds its payload, else (manifest or None, why).

    A pure check for tests and for later consumers: the manifest must exist and be well-formed, its build id must be
    this workspace's, and the payload tree digest, entry count and byte count must be recomputed identically.
    """
    if sys.platform == "win32":
        from . import build_win32 as wb
        return wb.revalidate(workspace)
    workspace = Path(workspace)
    path = workspace / MANIFEST_NAME
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_RESPONSE_BYTES:
            return None, "the build manifest is not a bounded regular file"
        manifest = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, "no readable build manifest: the build is not complete"
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        return manifest, "the manifest has another schema"
    if manifest.get("build_id") != build_id(workspace.name) or manifest.get("request_id") != workspace.name:
        return manifest, "the manifest does not belong to this workspace"
    if "git_verified" in manifest:
        return manifest, "the manifest claims a Git verification it cannot have"
    payload = manifest.get("payload") or {}
    if payload.get("path") != f"{PAYLOAD}/{APP}" or payload.get("tree_algorithm") != TREE_ALGORITHM:
        return manifest, "the manifest names another payload or tree algorithm"
    try:
        tree = payload_tree(workspace / PAYLOAD / APP)
    except (PayloadProblem, OSError) as exc:
        return manifest, f"the payload no longer validates: {exc}"
    if (tree["digest"], tree["entries"], tree["bytes"]) != (payload.get("tree_digest"), payload.get("entries"),
                                                           payload.get("bytes")):
        return manifest, "the payload tree digest no longer matches the manifest"
    return manifest, None
