"""Read-only Windows host facts for the Unity batch plane (alpha.25, Phase 2C-9.3a; Windows only).

Imported only by `unity/adapter.py` (the Program Files known folder) and `unity/project_lock.py` (the project-lock
proof). Every function reads; nothing here starts, signals, suspends or terminates a process, takes a lock, or opens
`Temp/UnityLockfile` at all. Each call is bounded and reports a failure as a reason, never as a guess.

    program_files()        the Program Files known folder (SHGetKnownFolderPath), never an environment variable
    processes()            [(pid, executable name)] from one Toolhelp32 snapshot, bounded
    open_process(pid)      a Process holding one limited-query handle; every fact below comes from that handle, so
                           a reused pid can never mix two processes' facts
        .running()             GetExitCodeProcess is STILL_ACTIVE
        .image()               QueryFullProcessImageNameW
        .created()             GetProcessTimes creation FILETIME (100 ns since 1601)
        .same_user()           the process token's user SID equals this process's
        .argv()                the command line (NtQueryInformationProcess, ProcessCommandLineInformation) split by
                               CommandLineToArgvW
    lock_owners(path)      [(pid, start FILETIME)] of the processes Restart Manager reports using the file

Owner query (measured, alpha.25 lab): Unity opens Temp/UnityLockfile exclusively, and a data handle opened by anyone
else makes a starting Editor abort ("another Unity instance is running with this project open"). Restart Manager's
read-only session workflow — RmStartSession, RmRegisterResources (the one file), RmGetList, RmEndSession — named the
Editor with its exact process start time and did not disturb 222 queries made during an Editor's start. Its only
side effect is the session's own transient key under HKCU\\Software\\Microsoft\\RestartManager, removed by
RmEndSession. RmShutdown and RmRestart are never used.

The command line is read through NtQueryInformationProcess (ProcessCommandLineInformation, Windows 8.1 and later),
which Microsoft documents as subject to change: any failure is a reason, and the proof then fails closed.
"""

import ctypes
import os
from ctypes import wintypes

MAX_PROCESSES = 16384             # one snapshot; reaching it is a truncated listing
MAX_OWNERS = 64                   # Restart Manager entries per query
MAX_COMMAND_LINE = 32768          # characters (the Windows command-line limit)
MAX_ARGC = 4096
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1
PROCESS_COMMAND_LINE_INFORMATION = 60
TH32CS_SNAPPROCESS = 0x2
ERROR_INVALID_PARAMETER = 87      # OpenProcess on a pid that names no process
ERROR_MORE_DATA = 234
CCH_RM_SESSION_KEY = 32
FOLDERID_PROGRAM_FILES = "{905E63B6-C1BF-494E-B29C-65B732D3D21A}"
INVALID_HANDLE = ctypes.c_void_p(-1).value

# Every binding this module may make, by library. tests/validate_framework.py pins the set.
_LIBRARIES = {
    "kernel32": ("CreateToolhelp32Snapshot", "Process32FirstW", "Process32NextW", "OpenProcess", "CloseHandle",
                 "GetExitCodeProcess", "QueryFullProcessImageNameW", "GetProcessTimes", "GetCurrentProcess",
                 "LocalFree"),
    "advapi32": ("OpenProcessToken", "GetTokenInformation", "EqualSid"),
    "ntdll": ("NtQueryInformationProcess",),
    "shell32": ("CommandLineToArgvW", "SHGetKnownFolderPath"),
    "ole32": ("CLSIDFromString", "CoTaskMemFree"),
    "rstrtmgr": ("RmStartSession", "RmRegisterResources", "RmGetList", "RmEndSession"),
}
_loaded = {}


def _bind(name, restype, *argtypes):
    library = next(lib for lib, names in _LIBRARIES.items() if name in names)
    if library not in _loaded:
        _loaded[library] = ctypes.WinDLL(library, use_last_error=True)
    fn = getattr(_loaded[library], name)
    fn.restype, fn.argtypes = restype, argtypes
    return fn


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", RM_UNIQUE_PROCESS), ("strAppName", ctypes.c_wchar * 256),
                ("strServiceShortName", ctypes.c_wchar * 64), ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _filetime(ft):
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _error():
    return ctypes.get_last_error()


# ---------------------------------------------------------------- known folder

def program_files():
    """The Program Files known folder (FOLDERID_ProgramFiles), or None when it cannot be resolved."""
    guid = GUID()
    if _bind("CLSIDFromString", ctypes.c_long, wintypes.LPCWSTR, ctypes.POINTER(GUID))(
            FOLDERID_PROGRAM_FILES, ctypes.byref(guid)) != 0:
        return None
    out = ctypes.c_void_p()
    hr = _bind("SHGetKnownFolderPath", ctypes.c_long, ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
               ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(guid), 0, None, ctypes.byref(out))
    try:
        if hr != 0 or not out.value:
            return None
        return ctypes.wstring_at(out.value)
    finally:
        if out.value:
            _bind("CoTaskMemFree", None, ctypes.c_void_p)(out.value)


# ---------------------------------------------------------------- processes

def processes():
    """([(pid, executable name)], None) from one snapshot, or (None, reason)."""
    snap = _bind("CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)(TH32CS_SNAPPROCESS, 0)
    if snap in (None, INVALID_HANDLE):
        return None, f"the process list could not be read (error {_error()})"
    first = _bind("Process32FirstW", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    following = _bind("Process32NextW", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        out, ok = [], first(snap, ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), entry.szExeFile))
            if len(out) >= MAX_PROCESSES:
                return None, "the process list reached its bound and may be truncated"
            ok = following(snap, ctypes.byref(entry))
        if not out:
            return None, "the process list is empty"
        return out, None
    finally:
        _bind("CloseHandle", wintypes.BOOL, wintypes.HANDLE)(snap)


class Process:
    """One limited-query handle on one process; use as a context manager."""

    def __init__(self, pid, handle):
        self.pid, self._h = pid, handle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._h:
            _bind("CloseHandle", wintypes.BOOL, wintypes.HANDLE)(self._h)
            self._h = None

    def running(self):
        code = wintypes.DWORD()
        if not _bind("GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))(
                self._h, ctypes.byref(code)):
            return None
        return code.value == STILL_ACTIVE

    def image(self):
        buf, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not _bind("QueryFullProcessImageNameW", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                     ctypes.POINTER(wintypes.DWORD))(self._h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value

    def created(self):
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _bind("GetProcessTimes", wintypes.BOOL, wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),) * 4)(
                self._h, *(ctypes.byref(t) for t in times)):
            return None
        return _filetime(times[0])

    def same_user(self):
        """True when this process runs as the current user, False for another user, None when it cannot be told."""
        theirs, ours = _token_user(self._h), _token_user(_bind("GetCurrentProcess", wintypes.HANDLE)())
        if theirs is None or ours is None:
            return None
        return bool(_bind("EqualSid", wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)(
            ctypes.addressof(theirs), ctypes.addressof(ours)))

    def argv(self):
        """(argv list, None) from the process's command line, or (None, reason)."""
        size = ctypes.sizeof(UNICODE_STRING) + 2 * MAX_COMMAND_LINE + 16
        buf, needed = ctypes.create_string_buffer(size), wintypes.ULONG()
        status = _bind("NtQueryInformationProcess", ctypes.c_long, wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                       wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))(
            self._h, PROCESS_COMMAND_LINE_INFORMATION, buf, size, ctypes.byref(needed))
        if status != 0:
            return None, f"the command line could not be read (status 0x{status & 0xFFFFFFFF:08x})"
        text = UNICODE_STRING.from_buffer(buf)
        if not text.Buffer or text.Length % 2 or text.Length > 2 * MAX_COMMAND_LINE:
            return None, "the command line is malformed or too long"
        line = ctypes.wstring_at(text.Buffer, text.Length // 2)
        if "\0" in line:
            return None, "the command line contains a NUL character"
        count = ctypes.c_int()
        split = _bind("CommandLineToArgvW", ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        array = split(line, ctypes.byref(count))
        if not array:
            return None, "the command line could not be split"
        try:
            if not 0 < count.value <= MAX_ARGC:
                return None, "the argument count is out of bounds"
            pointers = ctypes.cast(array, ctypes.POINTER(ctypes.c_wchar_p))
            return [pointers[i] for i in range(count.value)], None
        finally:
            _bind("LocalFree", ctypes.c_void_p, ctypes.c_void_p)(array)


def _token_user(process_handle):
    """The token's TOKEN_USER buffer (SID inside), or None."""
    token = wintypes.HANDLE()
    if not _bind("OpenProcessToken", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))(
            process_handle, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        buf, needed = ctypes.create_string_buffer(256), wintypes.DWORD()
        if not _bind("GetTokenInformation", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))(token, TOKEN_USER_CLASS, buf, 256,
                                                                     ctypes.byref(needed)):
            return None
        # TOKEN_USER is { SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes } }: the SID pointer leads the buffer
        sid = ctypes.c_void_p.from_buffer(buf, 0).value
        if not sid:
            return None
        offset = sid - ctypes.addressof(buf)
        if not 0 <= offset < 256:
            return None
        return (ctypes.c_char * (256 - offset)).from_buffer(buf, offset)
    finally:
        _bind("CloseHandle", wintypes.BOOL, wintypes.HANDLE)(token)


def open_process(pid):
    """(Process, None); (None, None) when no such process exists; (None, reason) when it cannot be opened."""
    handle = _bind("OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        err = _error()
        if err == ERROR_INVALID_PARAMETER:
            return None, None
        return None, f"process {pid} cannot be opened for a limited query (error {err})"
    return Process(pid, handle), None


# ---------------------------------------------------------------- Restart Manager: who has the file open

def lock_owners(path):
    """([(pid, start FILETIME)], None) for the processes using the file, or (None, reason). Read-only: no shutdown,
    restart, filter or process control; the session is always ended."""
    session, key = wintypes.DWORD(), ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    err = _bind("RmStartSession", wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR)(
        ctypes.byref(session), 0, key)
    if err:
        return None, f"the Restart Manager session could not be started (error {err})"
    try:
        files = (wintypes.LPCWSTR * 1)(str(path))
        err = _bind("RmRegisterResources", wintypes.DWORD, wintypes.DWORD, wintypes.UINT,
                    ctypes.POINTER(wintypes.LPCWSTR), wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
                    ctypes.c_void_p)(session, 1, files, 0, None, 0, None)
        if err:
            return None, f"the lockfile could not be registered with Restart Manager (error {err})"
        needed, count, reasons = wintypes.UINT(), wintypes.UINT(MAX_OWNERS), wintypes.DWORD()
        info = (RM_PROCESS_INFO * MAX_OWNERS)()
        err = _bind("RmGetList", wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.UINT),
                    ctypes.POINTER(wintypes.UINT), ctypes.POINTER(RM_PROCESS_INFO), ctypes.POINTER(wintypes.DWORD))(
            session, ctypes.byref(needed), ctypes.byref(count), info, ctypes.byref(reasons))
        if err == ERROR_MORE_DATA:
            return None, f"more than {MAX_OWNERS} processes use the lockfile"
        if err:
            return None, f"Restart Manager could not list the lockfile's users (error {err})"
        if count.value > MAX_OWNERS or needed.value != count.value:
            return None, "Restart Manager's answer is inconsistent"
        return [(int(info[i].Process.dwProcessId), _filetime(info[i].Process.ProcessStartTime))
                for i in range(count.value)], None
    finally:
        _bind("RmEndSession", wintypes.DWORD, wintypes.DWORD)(session)


def is_reparse(stat_result):
    """True for a reparse point (a link, junction or mount point) from an os.lstat result."""
    return bool(getattr(stat_result, "st_file_attributes", 0) & 0x400)


__all__ = ["program_files", "processes", "open_process", "lock_owners", "is_reparse", "Process"]

if os.name != "nt":   # pragma: no cover - imported only on Windows
    raise ImportError("gpos.tools.unity.host_win32 is Windows-only")
