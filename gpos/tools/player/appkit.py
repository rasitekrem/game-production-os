"""The in-process AppKit backend of the player adapter (alpha.22), and nothing else.

Two calls, both made inside the GPOS process through the Objective-C runtime (ctypes), never through another process:

* `running_pids(application_id)` — `NSRunningApplication.runningApplicationsWithBundleIdentifier:`, the cross-check of
  the libproc same-application scan;
* `request_terminate(pid, application_id, executable)` — the graceful-quit fallback `player.stop` uses when the
  supervisor that owns the Player is gone: `NSRunningApplication.runningApplicationWithProcessIdentifier:`, and only
  when that application's bundle identifier *and* executable path are exactly the bound ones, `-terminate`. This is
  the same request the Player receives when a person chooses Quit; it is never SIGTERM.

Measured (Phase 2C-8 X8/X10): terminate quits a Unity Player gracefully (Application.quitting runs) without a privacy
prompt; a mismatched bundle or executable sends nothing; the bundle query sees every instance of the identifier.
"""

import ctypes
import os

_RT = {}


def _runtime():
    if "objc" not in _RT:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        objc.objc_getClass.restype, objc.objc_getClass.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        objc.sel_registerName.restype, objc.sel_registerName.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        send = lambda restype, *argtypes: ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
            ("objc_msgSend", objc))
        _RT.update(objc=objc, cf=cf, obj=send(ctypes.c_void_p), obj_obj=send(ctypes.c_void_p, ctypes.c_void_p),
                   obj_int=send(ctypes.c_void_p, ctypes.c_int), obj_long=send(ctypes.c_void_p, ctypes.c_long),
                   long=send(ctypes.c_long), int=send(ctypes.c_int), bool=send(ctypes.c_bool),
                   cstr=send(ctypes.c_char_p))
    return _RT


def _sel(name):
    return _runtime()["objc"].sel_registerName(name.encode())


def _string(rt, value):
    return rt["cstr"](value, _sel("UTF8String")).decode("utf-8") if value else None


def running_pids(application_id):
    """Sorted pids of every running application with exactly this bundle identifier."""
    rt = _runtime()
    pool = rt["objc"].objc_autoreleasePoolPush()
    key = rt["cf"].CFStringCreateWithCString(None, application_id.encode("utf-8"), 0x08000100)
    try:
        cls = rt["objc"].objc_getClass(b"NSRunningApplication")
        array = rt["obj_obj"](cls, _sel("runningApplicationsWithBundleIdentifier:"), key)
        count = rt["long"](array, _sel("count")) if array else 0
        return sorted(rt["int"](rt["obj_long"](array, _sel("objectAtIndex:"), i), _sel("processIdentifier"))
                      for i in range(count))
    finally:
        rt["cf"].CFRelease(key)
        rt["objc"].objc_autoreleasePoolPop(pool)


def request_terminate(pid, application_id, executable):
    """(found, accepted). Asks exactly this application to quit, only when its bundle identifier and executable path are
    exactly the bound ones; anything else sends nothing."""
    rt = _runtime()
    pool = rt["objc"].objc_autoreleasePoolPush()
    try:
        cls = rt["objc"].objc_getClass(b"NSRunningApplication")
        app = rt["obj_int"](cls, _sel("runningApplicationWithProcessIdentifier:"), pid)
        if not app:
            return False, False
        bundle = _string(rt, rt["obj"](app, _sel("bundleIdentifier")))
        url = rt["obj"](app, _sel("executableURL"))
        path = _string(rt, rt["obj"](url, _sel("path"))) if url else None
        if bundle != application_id or path is None or os.path.realpath(path) != os.path.realpath(executable):
            return True, False   # the same file under another spelling (/var vs /private/var) is still the same file
        return True, bool(rt["bool"](app, _sel("terminate")))
    finally:
        rt["objc"].objc_autoreleasePoolPop(pool)
