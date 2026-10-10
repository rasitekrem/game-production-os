"""Windows CLASSIC x64 Mono payload contract. No process/Player/Git execution.

The only new native surface is FindFirstStreamW, FindNextStreamW and FindClose.
Lexical/scope checks precede every raw handle. NTFS identities are part of the
versioned tree: an identical-byte replacement is drift too. This is a local
workspace contract, not a portable archive or a power-loss durability claim.
"""
import ctypes
from contextlib import contextmanager
import datetime
import hashlib
import json
import msvcrt
import os
import struct
import time
from ctypes import wintypes
from pathlib import Path

from .. import paths_win32 as pw

TARGET = "StandaloneWindows64"
APP = "Player"
EXE = "Player.exe"
DATA = "Player_Data"
CONFIG_SCHEMA = "gpos.unity.build-config/2"
RESPONSE_SCHEMA = "gpos.unity.build-response/2"
MANIFEST_SCHEMA = "gpos.unity.build-manifest/2"
TREE_ALGORITHM = "gpos.unity.windows-payload-tree/1"
MAX_ENTRIES, MAX_BYTES, MAX_DEPTH, MAX_REL_BYTES = 4096, 8 * 1024**3, 32, 1024
MAX_JSON_BYTES, MAX_PE_HEADER, MAX_STREAMS = 1024 * 1024, 1024 * 1024, 4
RETRY_SECONDS = .1
INVALID_HANDLE = ctypes.c_void_p(-1).value

class PayloadProblem(ValueError):
    pass

VALIDATION_ERRORS = (PayloadProblem, OSError, pw.PathRefused, UnicodeError)

class STREAM_DATA(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int64), ("name", wintypes.WCHAR * 296)]

_k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
_first = _k32.FindFirstStreamW
_first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(STREAM_DATA), wintypes.DWORD]
_first.restype = wintypes.HANDLE
_next = _k32.FindNextStreamW
_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(STREAM_DATA)]
_next.restype = wintypes.BOOL
_close = _k32.FindClose
_close.argtypes = [wintypes.HANDLE]
_close.restype = wintypes.BOOL

def safe(path, exists=True):
    path = Path(path)
    why = pw.unsafe_reason([str(path.parent)], str(path), exists)
    if why:
        raise PayloadProblem(why)
    return path

def identity(handle):
    i = pw.info(handle)
    return [int(i.dwVolumeSerialNumber), int(i.nFileIndexHigh << 32 | i.nFileIndexLow)]

@contextmanager
def pinned_directory(path):
    path = safe(path)
    pins = pw.pin_chain(str(path))
    try:
        yield path
    finally:
        pw.close_all(pins)

def streams(path, directory=False, expected_size=None):
    """Caller holds the checked file/directory pin; enumeration never follows an explicit ADS path."""
    safe(path)
    data = STREAM_DATA()
    h = _first(str(path), 0, ctypes.byref(data), 0)
    if h == INVALID_HANDLE:
        error = ctypes.get_last_error()
        if directory and error == 38:  # a directory has no unnamed stream
            return
        raise PayloadProblem(f"stream enumeration failed ({error})")
    seen = []
    try:
        while True:
            name, size = data.name, int(data.size)
            seen.append(name)
            if len(seen) > MAX_STREAMS or len(set(seen)) != len(seen):
                raise PayloadProblem("ambiguous or excessive streams")
            if directory or name != "::$DATA" or size < 0 or (expected_size is not None and size != expected_size):
                raise PayloadProblem("named stream or inconsistent stream refused")
            if not _next(h, ctypes.byref(data)):
                error = ctypes.get_last_error()
                if error != 38:
                    raise PayloadProblem(f"stream enumeration incomplete ({error})")
                break
        if seen != ["::$DATA"]:
            raise PayloadProblem("unnamed stream missing")
    finally:
        if not _close(h):
            raise PayloadProblem("stream enumeration handle could not be closed")

def read_file(path, bound):
    path = safe(path)
    fd, size, pins = pw.open_file_for_read(str(path), deny_writers=True)
    try:
        with os.fdopen(fd, "rb") as f:
            before = identity(msvcrt.get_osfhandle(f.fileno()))
            if size > bound:
                raise PayloadProblem("file exceeds read bound")
            streams(path, expected_size=size)
            data = f.read(bound + 1)
            streams(path, expected_size=size)
            if len(data) != size or identity(msvcrt.get_osfhandle(f.fileno())) != before:
                raise PayloadProblem("file identity or size changed")
            return data
    finally:
        pw.close_all(pins)

def _pairs(pairs):
    d = {}
    for key, value in pairs:
        if key in d:
            raise PayloadProblem("duplicate JSON key")
        d[key] = value
    return d

def read_json(path, bound=MAX_JSON_BYTES):
    try:
        return json.loads(read_file(path, bound).decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(PayloadProblem("nonfinite JSON")))
    except (UnicodeError, ValueError) as e:
        raise PayloadProblem(f"untrusted JSON: {e}") from None

def timestamp(value):
    if type(value) is not str or len(value) > 40 or not value.endswith("Z"):
        raise PayloadProblem("timestamp is not bounded UTC")
    return datetime.datetime.fromisoformat(value)

def started_utc(workspace, request_id):
    from . import build as ub
    r = read_json(Path(workspace) / ub.STARTED_NAME, ub.MAX_STARTED_BYTES)
    if (set(r) != {"schema", "request_id", "build_id", "utc"} or r["schema"] != ub.STARTED_SCHEMA or
            r["request_id"] != request_id or r["build_id"] != ub.build_id(request_id)):
        raise PayloadProblem("started record identity differs")
    timestamp(r["utc"])
    return r["utc"]

def pe(head, size):
    if len(head) < 64 or head[:2] != b"MZ":
        raise PayloadProblem("PE DOS header missing")
    offset = struct.unpack_from("<I", head, 60)[0]
    if offset + 24 > len(head) or head[offset:offset+4] != b"PE\0\0":
        raise PayloadProblem("PE signature or bounded offset invalid")
    machine, sections = struct.unpack_from("<HH", head, offset+4)
    opt_size, flags = struct.unpack_from("<HH", head, offset+20)
    opt = offset+24
    if opt+opt_size > len(head) or opt+opt_size > size or opt_size < 96 or not 1 <= sections <= 96:
        raise PayloadProblem("PE optional header or section bound invalid")
    magic = struct.unpack_from("<H", head, opt)[0]
    if magic not in (0x10b, 0x20b) or not flags & 2:
        raise PayloadProblem("not a PE executable image")
    if opt_size < (112 if magic == 0x20b else 96):
        raise PayloadProblem("PE optional header is too short for its format")
    directories = opt + (112 if magic == 0x20b else 96)
    number = struct.unpack_from("<I", head, directories-4)[0]
    if number > 16 or directories+number*8 > opt+opt_size:
        raise PayloadProblem("PE data directory bound invalid")
    section_table = opt+opt_size
    if section_table+sections*40 > len(head):
        raise PayloadProblem("PE section table outside bounded header")
    cli = struct.unpack_from("<II", head, directories+14*8) if number >= 15 else (0, 0)
    if bool(cli[0]) != bool(cli[1]):
        raise PayloadProblem("ambiguous managed header")
    managed = bool(cli[0] and cli[1])
    mapped_cli = False
    for n in range(sections):
        virtual_size, address, raw_size, raw_offset = struct.unpack_from("<IIII", head, section_table+n*40+8)
        if raw_offset+raw_size > size:
            raise PayloadProblem("PE section exceeds file bounds")
        if managed and address <= cli[0] and cli[0]+72 <= address+raw_size:
            offset_cli = raw_offset+cli[0]-address
            if cli[1] < 72 or offset_cli+72 > len(head) or struct.unpack_from("<I",head,offset_cli)[0] < 72:
                raise PayloadProblem("managed CLI header unavailable or malformed")
            mapped_cli = True
    if managed and not mapped_cli:
        raise PayloadProblem("managed CLI header outside physical sections")
    if not managed and (machine != 0x8664 or magic != 0x20b):
        raise PayloadProblem("native image is not x64 PE32+")
    return {"managed": managed, "dll": bool(flags & 0x2000)}

def tree(root):
    root = safe(root)
    entries, aliases, pins, total = [], set(), [], 0
    try:
        root_pins = pw.pin_chain(str(root)); pins.extend(root_pins)
        root_id = identity(root_pins[-1])
        streams(root.parent, directory=True)  # staging/payload container is pinned by the same chain
        streams(root, directory=True)
        def walk(folder, depth):
            nonlocal total
            names = []
            with os.scandir(folder) as children:
                for child in children:
                    if len(names) + len(entries) >= MAX_ENTRIES:
                        raise PayloadProblem("payload directory enumeration bound exceeded")
                    names.append(child.name)
            for name in sorted(names, key=lambda n: n.encode("utf-8")):
                path = safe(folder / name)
                rel = path.relative_to(root).as_posix()
                if depth > MAX_DEPTH or len(rel.encode("utf-8")) > MAX_REL_BYTES or len(entries) >= MAX_ENTRIES:
                    raise PayloadProblem("payload tree bound exceeded")
                if rel.casefold() in aliases:
                    raise PayloadProblem("case alias refused")
                aliases.add(rel.casefold())
                if path.is_dir():
                    held = pw.pin_chain(str(path)); pins.extend(held)
                    if len(pins) > 8192:
                        raise PayloadProblem("directory pin handle bound exceeded")
                    entries.append({"path": rel, "kind": "directory", "identity": identity(held[-1])})
                    streams(path, directory=True)
                    walk(path, depth+1)
                    streams(path, directory=True)
                else:
                    fd, size, held = pw.open_file_for_read(str(path), deny_writers=True)
                    try:
                        with os.fdopen(fd, "rb") as f:
                            file_id = identity(msvcrt.get_osfhandle(f.fileno()))
                            streams(path, expected_size=size)
                            total += size
                            if total > MAX_BYTES:
                                raise PayloadProblem("payload byte bound exceeded")
                            head = f.read(min(size, MAX_PE_HEADER)); h = hashlib.sha256(head); count = len(head)
                            for block in iter(lambda: f.read(1024*1024), b""):
                                count += len(block); h.update(block)
                            streams(path, expected_size=size)
                            if count != size or identity(msvcrt.get_osfhandle(f.fileno())) != file_id:
                                raise PayloadProblem("payload file changed during hashing")
                            e = {"path": rel, "kind": "file", "identity": file_id, "size": size, "sha256": h.hexdigest()}
                            if path.suffix.lower() in (".exe", ".dll"):
                                e["pe"] = pe(head, size)
                            entries.append(e)
                    finally:
                        pw.close_all(held)
        walk(root, 1)
        streams(root, directory=True)
        if identity(root_pins[-1]) != root_id:
            raise PayloadProblem("payload root replaced")
    finally:
        pw.close_all(pins)
    entries.sort(key=lambda e: e["path"].encode("utf-8"))
    data = {"root_identity": root_id, "inventory": entries}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return {"algorithm": TREE_ALGORITHM, "digest": digest, "entries": len(entries), "bytes": total, **data}

def validate_player(root, guid, report_files=None):
    from . import build as ub
    root = safe(root)
    result = tree(root)
    files = {e["path"]: e for e in result["inventory"] if e["kind"] == "file"}
    required = (EXE, "UnityPlayer.dll", f"{DATA}/boot.config", f"{DATA}/globalgamemanagers", f"{DATA}/level0",
                f"{DATA}/ScriptingAssemblies.json", f"{DATA}/Managed/mscorlib.dll",
                f"{DATA}/Managed/UnityEngine.CoreModule.dll", "MonoBleedingEdge/EmbedRuntime/mono-2.0-bdwgc.dll",
                "MonoBleedingEdge/EmbedRuntime/MonoPosixHelper.dll", "MonoBleedingEdge/etc/mono/config",
                "MonoBleedingEdge/etc/mono/4.5/machine.config")
    if any(p not in files or files[p]["size"] == 0 for p in required):
        raise PayloadProblem("required Windows Player/Data/Mono structure missing")
    if files[EXE]["pe"] != {"managed": False, "dll": False}:
        raise PayloadProblem("Player.exe is not a native x64 executable")
    for p in ("UnityPlayer.dll", "MonoBleedingEdge/EmbedRuntime/mono-2.0-bdwgc.dll"):
        if files[p]["pe"] != {"managed": False, "dll": True}:
            raise PayloadProblem("required native DLL is not a native x64 DLL")
    top_dirs = {DATA, "MonoBleedingEdge", "D3D12"}
    # These runtime categories were observed. Optional GPU plug-in roots are unqualified, so fail closed.
    top_files = {EXE, "UnityPlayer.dll", "UnityCrashHandler64.exe", "dstorage.dll", "dstoragecore.dll"}
    for e in result["inventory"]:
        first = e["path"].split("/")[0]
        if first not in top_dirs | top_files or ("/" not in e["path"] and
                ((e["kind"] == "directory") != (first in top_dirs))):
            raise PayloadProblem("unsupported output sidecar or top-level entry")
        if e["path"].startswith(DATA+"/Managed/") and "pe" in e and not e["pe"]["managed"]:
            raise PayloadProblem("native DLL in managed assembly directory")
        if "pe" in e and e["path"].lower().endswith(".dll") and not e["pe"]["dll"]:
            raise PayloadProblem("DLL lacks the PE DLL characteristic")
        if e["path"].lower().endswith((".pdb", ".mdb", ".sln", ".csproj")):
            raise PayloadProblem("unqualified debug or project sidecar")
    boot = read_file(root / DATA / "boot.config", ub.MAX_BOOT_CONFIG_BYTES).decode("utf-8")
    if [line.split("=", 1)[1] for line in boot.splitlines() if line.startswith("build-guid=")] != [guid]:
        raise PayloadProblem("boot.config build GUID differs from BuildReport")
    if report_files is not None:
        seen = set()
        for f in report_files:
            if set(f) != {"path", "size"} or type(f["path"]) is not str or type(f["size"]) is not int:
                raise PayloadProblem("malformed BuildReport file")
            p = safe(f["path"])
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                raise PayloadProblem("BuildReport file outside payload") from None
            if rel in seen or rel not in files or f["size"] != files[rel]["size"]:
                raise PayloadProblem("BuildReport file list differs from payload")
            seen.add(rel)
        if seen != set(files):
            raise PayloadProblem("payload file absent from BuildReport")
    return {"executable": EXE, "data": DATA}, result

def rename_once(source, target):
    safe(source); safe(target, False)
    deadline = time.monotonic() + RETRY_SECONDS
    while True:
        try:
            os.rename(source, target)
            return
        except OSError as e:
            if getattr(e, "winerror", None) not in (5, 32) or time.monotonic() >= deadline:
                raise
            time.sleep(.002)

def publish(workspace):
    workspace = safe(workspace)
    pins = pw.pin_chain(str(workspace))
    try:
        rename_once(workspace / "staging", workspace / "payload")
    finally:
        pw.close_all(pins)

def write_manifest(workspace, manifest):
    workspace = safe(workspace)
    data = (json.dumps(manifest, sort_keys=True, indent=1) + "\n").encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise PayloadProblem("manifest exceeds bounded read contract")
    pins = pw.pin_chain(str(workspace))
    try:
        temporary, final = workspace / "build-manifest.json.tmp", workspace / "build-manifest.json"
        safe(temporary, False); safe(final, False)
        with open(temporary, "xb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        rename_once(temporary, final)
        return final
    finally:
        pw.close_all(pins)

def configuration_problem(c):
    """Closed supported configuration, also checked when consuming an existing manifest."""
    from . import build as ub
    if not isinstance(c, dict) or set(c) != ub.CONFIG_KEYS:
        return "configuration keys differ"
    debug = {"connect_profiler", "allow_debugging", "deep_profiling", "wait_for_managed_debugger",
             "code_coverage", "wait_for_player_connection"}
    output = {"architecture": "x64", "readable": True, "copy_pdb": False,
              "create_solution": False, "install_in_build_folder": False}
    fixed = {"schema": CONFIG_SCHEMA, "unity_version": "6000.6.4f1", "active_target": TARGET,
             "standalone_subtarget": "Player", "scripting_backend": "Mono2x", "mode": "CLASSIC",
             "profile": None, "development": False}
    if any(c[k] != v or type(c[k]) is not type(v) for k,v in fixed.items()):
        return "unsupported fixed configuration"
    if not isinstance(c["debug"], dict) or set(c["debug"]) != debug or any(v is not False for v in c["debug"].values()):
        return "debug configuration differs"
    if c["output"] != output or any(type(c["output"][k]) is not type(v) for k,v in output.items()):
        return "Windows output contract differs"
    scenes = c["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 1024:
        return "scene bounds differ"
    paths = set()
    for s in scenes:
        if not isinstance(s, dict) or set(s) != {"path", "guid", "sha256"} or not all(type(v) is str for v in s.values()):
            return "scene fields differ"
        name = s["path"]
        if not name.startswith("Assets/") or not name.endswith(".unity") or "\\" in name or any(p in ("", ".", "..") for p in name.split("/")):
            return "unsafe scene path"
        if name.casefold() in paths or not ub.GUID.fullmatch(s["guid"]) or not ub.TOKEN.fullmatch(s["sha256"]):
            return "scene identity differs"
        paths.add(name.casefold())
    names = {"Packages/manifest.json", "Packages/packages-lock.json", "ProjectSettings/EditorBuildSettings.asset", "ProjectSettings/ProjectSettings.asset"}
    if not isinstance(c["files"], dict) or set(c["files"]) != names:
        return "bound project file set differs"
    if any(type(v) is not str or (v != "ABSENT" and not ub.TOKEN.fullmatch(v)) for v in c["files"].values()):
        return "bound file identity malformed"
    return None

def revalidate(workspace):
    from . import build as ub
    from . import bridge_install as bi
    from . import adapter as ua
    from .build_windows import LIMITATIONS
    import gpos
    manifest = None
    try:
        workspace = safe(workspace)
        manifest = read_json(workspace / ub.MANIFEST_NAME)
        required = {"schema", "build_id", "request_id", "capability", "adapter", "gpos_version", "subject",
                    "build_revision", "build_revision_source", "configuration_token", "configuration", "unity_version",
                    "target", "development", "configuration_mode", "build_profile", "scenes", "unity_build",
                    "build_entry", "payload", "started_at", "built_at", "duration_seconds", "limitations"}
        if not isinstance(manifest, dict) or set(manifest) != required or manifest["schema"] != MANIFEST_SCHEMA:
            raise PayloadProblem("unknown Windows manifest schema or fields")
        if manifest["build_id"] != ub.build_id(workspace.name) or manifest["request_id"] != workspace.name:
            raise PayloadProblem("manifest workspace identity differs")
        if not ub.BUILD_REQUEST_ID.fullmatch(workspace.name):
            raise PayloadProblem("manifest request grammar differs")
        if timestamp(manifest["built_at"]) < timestamp(manifest["started_at"]):
            raise PayloadProblem("manifest timestamp order differs")
        if (manifest["gpos_version"] != gpos.__version__ or manifest["capability"] != ub.BUILD or
                manifest["adapter"] != {"id": ua.ADAPTER_ID, "version": ua.ADAPTER_VERSION} or
                manifest["build_entry"] != {"package": bi.PACKAGE_ID, "version": bi.BRIDGE_VERSION,
                    "method": ub.BUILD_ENTRY_METHOD, "package_digest": bi.load_manifest()["package_digest"]} or
                manifest["limitations"] != list(LIMITATIONS)):
            raise PayloadProblem("manifest version, entry or provenance limitations differ")
        subject = manifest["subject"]
        if (not isinstance(subject, dict) or set(subject) != {"kind", "ref"} or
                not all(type(v) is str and 0 < len(v) <= 1024 for v in subject.values())):
            raise PayloadProblem("manifest subject differs")
        if manifest["build_revision_source"] != "CALLER_SUPPLIED" or not ub.REVISION.fullmatch(manifest["build_revision"]):
            raise PayloadProblem("untrusted revision attribution")
        c = manifest["configuration"]
        problem = configuration_problem(c)
        if problem:
            raise PayloadProblem(problem)
        if (c["schema"] != CONFIG_SCHEMA or set(c) != ub.CONFIG_KEYS or c["active_target"] != TARGET or
                c["scripting_backend"] != "Mono2x" or c["standalone_subtarget"] != "Player" or
                c["mode"] != "CLASSIC" or c["profile"] is not None or c["development"] is not False or
                any(c["debug"].values()) or c["output"]["architecture"] != "x64" or
                c["output"]["install_in_build_folder"] or c["output"]["create_solution"] or c["output"]["copy_pdb"]):
            raise PayloadProblem("unsupported Windows manifest configuration")
        if (ub.token_of(c) != manifest["configuration_token"] or manifest["scenes"] != c["scenes"] or
                manifest["unity_version"] != c["unity_version"] or manifest["target"] != TARGET or
                manifest["development"] is not False or manifest["configuration_mode"] != "CLASSIC" or manifest["build_profile"] is not None):
            raise PayloadProblem("manifest configuration bindings differ")
        b, payload = manifest["unity_build"], manifest["payload"]
        if (set(b) != {"guid", "result", "total_errors", "total_warnings", "total_size", "development_observed", "duration_seconds"} or
                set(payload) != {"path", "kind", "tree_algorithm", "tree_digest", "entries", "bytes", "root_identity", "inventory", "executable", "data"}):
            raise PayloadProblem("manifest nested field set differs")
        if any(type(b[k]) is not int or b[k] < 0 for k in ("total_errors", "total_warnings", "total_size")):
            raise PayloadProblem("BuildReport counts differ")
        if (type(b["duration_seconds"]) not in (int, float) or not 0 <= b["duration_seconds"] < float("inf") or
                manifest["duration_seconds"] != b["duration_seconds"]):
            raise PayloadProblem("duration binding differs")
        if (type(payload["entries"]) is not int or not 1 <= payload["entries"] <= MAX_ENTRIES or
                type(payload["bytes"]) is not int or not 1 <= payload["bytes"] <= MAX_BYTES or
                b["total_size"] != payload["bytes"]):
            raise PayloadProblem("BuildReport and payload size/count bindings differ")
        if (b["result"] != "Succeeded" or b["total_errors"] != 0 or b["development_observed"] is not False or
                not ub.GUID.fullmatch(b["guid"]) or b["guid"] == "0"*32 or
                payload["path"] != "payload/Player" or payload["kind"] != "WINDOWS_STANDALONE_PLAYER" or
                payload["executable"] != EXE or payload["data"] != DATA or payload["tree_algorithm"] != TREE_ALGORITHM):
            raise PayloadProblem("manifest build/payload contract differs")
        _, current = validate_player(workspace / "payload" / APP, b["guid"])
        if sorted(os.listdir(workspace / "payload")) != [APP]:
            raise PayloadProblem("unexpected final payload container entry")
        if any(payload[k] != current[v] for k, v in (("tree_digest", "digest"), ("entries", "entries"),
                ("bytes", "bytes"), ("root_identity", "root_identity"), ("inventory", "inventory"))):
            raise PayloadProblem("payload content or file identity drift")
        return manifest, None
    except (OSError, ValueError, KeyError, TypeError, pw.PathRefused) as e:
        return manifest, f"Windows build does not revalidate: {e}"


def read_response(workspace, request_id, operation):
    """The build entry's answer, checked strictly; raises ResponseProblem when it cannot be trusted."""
    from .build import (RESPONSE_NAME, MAX_RESPONSE_BYTES, OUTCOMES, RULES, CONFIG_KEYS, TOKEN,
                        ResponseProblem, _is, token_of, _check_build)
    path = Path(workspace) / RESPONSE_NAME
    if not os.path.lexists(path):
        raise ResponseProblem("the build entry wrote no response")
    r = read_json(path, MAX_RESPONSE_BYTES)
    keys = {"schema", "operation", "request_id", "unity_version", "outcome", "buildable", "problems", "configuration",
            "configuration_token", "refusal", "build", "post"}
    if not isinstance(r, dict) or set(r) != keys:
        raise ResponseProblem("the response does not hold exactly the response keys")
    response_schema, config_schema = RESPONSE_SCHEMA, CONFIG_SCHEMA
    if (r["schema"], r["operation"], r["request_id"]) != (response_schema, operation, request_id):
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
    if not isinstance(configuration, dict) or set(configuration) != CONFIG_KEYS or configuration["schema"] != config_schema:
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
