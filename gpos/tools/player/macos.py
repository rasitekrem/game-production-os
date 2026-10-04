"""The in-process macOS backend of the player adapter (alpha.22): kernel process identity, the same-application scan,
the kernel's code hash of a running process, window facts and the one proven kill.

Everything here runs inside the GPOS process through ctypes calls into fixed system libraries (libSystem,
CoreFoundation, CoreGraphics); nothing here starts a process, takes a pid from a request or signals anything that has
not just been proven. Libraries are loaded on first use, so importing this module on another platform is harmless.

Identity (measured, Phase 2C-8): a process is the one GPOS launched when its pid is alive, its kernel start time
(`proc_pidinfo` PROC_PIDTBSDINFO) is exactly the bound one and `proc_pidpath` is exactly the bound executable. A stale
start time means the pid was reused (NOT_THIS_PROCESS); no process means GONE. Another user's processes are not
visible to these calls, so they are never matched, blocked on or signalled.
"""

import ctypes
import os
import plistlib
import signal
from pathlib import Path

from . import contract as c

_LIB = {}
PROC_PIDTBSDINFO = 3
CS_OPS_CDHASH = 5
MAX_PIDS = 16384
MAX_PLIST_BYTES = 1024 * 1024


class _BSDInfo(ctypes.Structure):
    _fields_ = [("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32), ("pbi_xstatus", ctypes.c_uint32),
                ("pbi_pid", ctypes.c_uint32), ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
                ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32), ("pbi_rgid", ctypes.c_uint32),
                ("pbi_svuid", ctypes.c_uint32), ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
                ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32), ("pbi_nfiles", ctypes.c_uint32),
                ("pbi_pgid", ctypes.c_uint32), ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
                ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32), ("pbi_start_tvsec", ctypes.c_uint64),
                ("pbi_start_tvusec", ctypes.c_uint64)]


def _libc():
    if "c" not in _LIB:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.csops.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
        _LIB["c"] = lib
    return _LIB["c"]


# ---------------------------------------------------------------- process facts

def facts(pid):
    """{pid, ppid, pgid, uid, start_sec, start_usec, executable} of a live process this user can see, or None.
    `executable` is None when the kernel cannot name the process's executable any more (it was deleted)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    lib, info = _libc(), _BSDInfo()
    if lib.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
        return None
    buf = ctypes.create_string_buffer(4096)
    path = os.fsdecode(buf.value) if lib.proc_pidpath(pid, buf, 4096) > 0 else None
    return {"pid": pid, "ppid": int(info.pbi_ppid), "pgid": int(info.pbi_pgid), "uid": int(info.pbi_uid),
            "start_sec": int(info.pbi_start_tvsec), "start_usec": int(info.pbi_start_tvusec), "executable": path}


def all_pids():
    buf = (ctypes.c_int * MAX_PIDS)()
    n = _libc().proc_listallpids(buf, ctypes.sizeof(buf))
    return [buf[i] for i in range(max(0, min(n, MAX_PIDS))) if buf[i] > 0]


def identity(pid, start_sec, start_usec, executable):
    """The state of a bound (pid, kernel start time, executable):

        GONE              no such process
        NOT_THIS_PROCESS  the pid now belongs to another process (another kernel start time)
        UNPROVEN          the same process instance (pid and start time), but the kernel no longer names the bound
                          executable for it (moved, deleted or replaced by exec): it is alive and never signalled
        PROVEN            the same process instance running exactly the bound executable"""
    f = facts(pid)
    if f is None:
        return c.GONE
    if (f["start_sec"], f["start_usec"]) != (start_sec, start_usec):
        return c.NOT_THIS_PROCESS
    if f["executable"] != executable:
        return c.UNPROVEN
    return c.PROVEN


def same_instance(pid, start_sec, start_usec):
    """True while exactly this process instance (pid and kernel start time) is alive, wherever its file is now."""
    f = facts(pid)
    return f is not None and (f["start_sec"], f["start_usec"]) == (start_sec, start_usec)


def cdhash(pid):
    """The kernel's code-directory hash of a running process as lower-case hex, or None."""
    buf = ctypes.create_string_buffer(20)
    if _libc().csops(pid, CS_OPS_CDHASH, buf, 20) != 0:
        return None
    return buf.raw.hex()


def file_identity(path):
    """(dev, ino) of a regular, non-link file, or None."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    import stat as st_mod
    return (st.st_dev, st.st_ino) if st_mod.S_ISREG(st.st_mode) else None


# ---------------------------------------------------------------- the same application

def bundle_identifier_of(executable):
    """CFBundleIdentifier of the `.app` bundle an executable sits in (<X>.app/Contents/MacOS/<exe>), or None."""
    exe = Path(executable)
    contents = exe.parent.parent
    if exe.parent.name != "MacOS" or contents.name != "Contents" or contents.parent.suffix != ".app":
        return None
    plist = contents / "Info.plist"
    try:
        if os.lstat(plist).st_size > MAX_PLIST_BYTES:
            return None
        with open(plist, "rb") as fh:
            value = plistlib.load(fh).get("CFBundleIdentifier")
    except Exception:   # plistlib raises several types for malformed input; an unreadable bundle never matches
        return None
    return value if isinstance(value, str) else None


def same_application(application_id):
    """[facts] of every live process of this user that runs an app bundle with exactly this identifier — wherever the
    bundle is, whichever build it is, however it was launched. Matching is by the bundle's identifier, never by a
    process name."""
    uid, out = os.getuid(), []
    for pid in all_pids():
        f = facts(pid)
        if f is None or f["uid"] != uid or f["executable"] is None:
            continue
        if bundle_identifier_of(f["executable"]) == application_id:
            out.append(f)
    return sorted(out, key=lambda f: f["pid"])


# ---------------------------------------------------------------- windows (CoreGraphics, no permission needed)

def _cg():
    if "cg" not in _LIB:
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFArrayGetCount.restype, cf.CFArrayGetCount.argtypes = ctypes.c_long, [ctypes.c_void_p]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_bool
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFBooleanGetValue.restype, cf.CFBooleanGetValue.argtypes = ctypes.c_bool, [ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        _LIB["cf"], _LIB["cg"], _LIB["keys"] = cf, cg, {}
    return _LIB["cf"], _LIB["cg"]


_OPTION_ALL, _EXCLUDE_DESKTOP = 0, 1 << 4
_SINT64, _DOUBLE, _UTF8 = 4, 13, 0x08000100


def _key(name):
    cf, _ = _cg()
    keys = _LIB["keys"]
    if name not in keys:
        keys[name] = cf.CFStringCreateWithCString(None, name.encode(), _UTF8)
    return keys[name]


def _number(d, name, kind):
    cf, _ = _cg()
    v = cf.CFDictionaryGetValue(d, _key(name))
    if not v:
        return None
    out = ctypes.c_int64() if kind == _SINT64 else ctypes.c_double()
    return out.value if cf.CFNumberGetValue(v, kind, ctypes.byref(out)) else None


def windows(pid):
    """[{layer, alpha, on_screen, width, height}] of every window the pid owns (CGWindowList facts; window ids are not
    returned). Owner, layer, alpha and bounds need no Screen Recording permission."""
    cf, cg = _cg()
    array = cg.CGWindowListCopyWindowInfo(_OPTION_ALL | _EXCLUDE_DESKTOP, 0)
    if not array:
        return []
    out = []
    try:
        for i in range(cf.CFArrayGetCount(array)):
            d = cf.CFArrayGetValueAtIndex(array, i)
            if _number(d, "kCGWindowOwnerPID", _SINT64) != pid:
                continue
            on = cf.CFDictionaryGetValue(d, _key("kCGWindowIsOnscreen"))
            bounds = cf.CFDictionaryGetValue(d, _key("kCGWindowBounds"))
            out.append({"layer": _number(d, "kCGWindowLayer", _SINT64), "alpha": _number(d, "kCGWindowAlpha", _DOUBLE),
                        "on_screen": bool(on and cf.CFBooleanGetValue(on)),
                        "width": _number(bounds, "Width", _DOUBLE) if bounds else None,
                        "height": _number(bounds, "Height", _DOUBLE) if bounds else None})
    finally:
        cf.CFRelease(array)
    return out


def eligible_windows(pid):
    """The windows a capture could bind: on-screen, layer 0, visible (alpha > 0)."""
    return [w for w in windows(pid) if w["layer"] == 0 and w["on_screen"] and (w["alpha"] or 0) > 0]


# ---------------------------------------------------------------- the one signal

def kill_proven(pid, start_sec, start_usec, executable):
    """SIGKILL exactly this process, only if it is proven again right now. Returns True when the signal was sent.

    The process is not our child, so a pid could in principle be reused between the proof and the signal; the proof is
    taken immediately before, which leaves microseconds. That residual is disclosed, not hidden."""
    if identity(pid, start_sec, start_usec, executable) != c.PROVEN:
        return False
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    return True
