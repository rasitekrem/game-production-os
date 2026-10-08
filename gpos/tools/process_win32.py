"""Windows backend of the audited process boundary (alpha.23). Private: only gpos/tools/process.py imports it.

process.py validates the ToolProcessSpec and builds the command line and the environment; this module starts that
one process and nothing else. It is not a second execution facility: it has no public entry of its own, takes no
command string from anyone but process.py, and never starts a shell.

The mechanism (Microsoft Learn: CreateProcessW, UpdateProcThreadAttribute, Job Objects, Nested Jobs, Process
Creation Flags):

    Φ0 setup     pin the executable (FILE_SHARE_READ only) and the working-directory chain; create the stdout and
                 stderr pipes, a NUL stdin and a fresh, unnamed, non-inheritable Job Object with
                 KILL_ON_JOB_CLOSE | DIE_ON_UNHANDLED_EXCEPTION and no breakaway limit
    Φ1 create    CreateProcessW(lpApplicationName = the validated executable, a writable Unicode command line,
                 bInheritHandles = TRUE with PROC_THREAD_ATTRIBUTE_HANDLE_LIST = exactly the three standard handles,
                 PROC_THREAD_ATTRIBUTE_JOB_LIST = the job, CREATE_SUSPENDED, an explicit Unicode environment and
                 working directory). The job membership is applied as the process is created: if it cannot be,
                 no process exists. The native call itself cannot be interrupted; its duration counts against
                 the deadline.
    Φ2 prove     while the child is still suspended: IsProcessInJob(child, job) and the child's image path equals the
                 pinned executable's final path. Anything unproven terminates the still-suspended child before any
                 of its code ran, and the launch is refused.
    Φ2b readers  the parent closes its copies of the child's handles and starts the stdout/stderr readers
    Φ3 run       ResumeThread; wait for the child with the time left before the deadline (the loader, DLL
                 initialisation and the program itself all run inside it); a deadline terminates the whole job
    Φ5 cleanup   always: descendants still in the job are terminated (a Windows/POSIX difference: GPOS does not
                 leave a tool's descendants running), then a bounded wait until the job reports no active process
    Φ6 drain     the readers are joined within a bound; a reader that did not reach end-of-file means the capture
                 is incomplete

Containment is reported, never assumed: `tree_contained` is True only when the job was observed empty, and
`capture_complete` only when both readers reached end-of-file. Either False makes the exit code untrustworthy, so
it is not reported.
"""

import ctypes
import os
import msvcrt
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from . import paths_win32 as pw

if sys.platform != "win32":  # pragma: no cover - imported only on Windows
    raise ImportError("gpos.tools.process_win32 exists only on Windows")

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Process Creation Flags (Microsoft Learn), exactly these and nothing else.
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATION_FLAGS = (EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED
                  | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)

JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_LIMITS = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
JOB_BASIC_ACCOUNTING, JOB_BASIC_PROCESS_ID_LIST, JOB_EXTENDED_LIMIT = 1, 3, 9

PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
STARTF_USESTDHANDLES = 0x00000100

SYNCHRONIZE, PROCESS_QUERY_LIMITED_INFORMATION = 0x00100000, 0x00001000
WAIT_OBJECT_0, WAIT_TIMEOUT, WAIT_FAILED = 0x0, 0x102, 0xFFFFFFFF
STILL_ACTIVE = 259
ERROR_INVALID_PARAMETER, ERROR_INSUFFICIENT_BUFFER = 87, 122
ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, ERROR_ACCESS_DENIED = 2, 3, 5
ERROR_BAD_EXE_FORMAT, ERROR_EXE_MACHINE_TYPE_MISMATCH = 193, 216
TERMINATED_EXIT = 1
MAX_COMMAND_LINE = 32766
MAX_WAIT_MS = 0xFFFFFFFE

CLEANUP_SECONDS = 5.0          # Φ5: after the deadline (or the root's exit), how long the job may take to empty
CAPTURE_DRAIN_SECONDS = 5.0    # Φ6: how long the readers may take to reach end-of-file once the job is empty
POLL_SECONDS = 0.01


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                                                     "OtherOperationCount", "ReadTransferCount",
                                                     "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


MAX_JOB_PIDS = 1024


class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
    _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD), ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * MAX_JOB_PIDS)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


def _bind(name, restype, *argtypes):
    function = getattr(_k32, name)
    function.restype, function.argtypes = restype, list(argtypes)
    return function


_bind("CreateProcessW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID,
      wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW),
      ctypes.POINTER(PROCESS_INFORMATION))
_bind("InitializeProcThreadAttributeList", wintypes.BOOL, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
      ctypes.POINTER(ctypes.c_size_t))
_bind("UpdateProcThreadAttribute", wintypes.BOOL, wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t, wintypes.LPVOID,
      ctypes.c_size_t, wintypes.LPVOID, ctypes.POINTER(ctypes.c_size_t))
_bind("DeleteProcThreadAttributeList", None, wintypes.LPVOID)
_bind("CreateJobObjectW", wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
_bind("SetInformationJobObject", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
_bind("QueryInformationJobObject", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
      wintypes.LPDWORD)
_bind("TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
_bind("IsProcessInJob", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
_bind("ResumeThread", wintypes.DWORD, wintypes.HANDLE)
_bind("TerminateProcess", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
_bind("QueryFullProcessImageNameW", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
      wintypes.LPDWORD)
_bind("WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
_bind("GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, wintypes.LPDWORD)
_bind("OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_bind("CloseHandle", wintypes.BOOL, wintypes.HANDLE)

# Test seams: every hook is a no-op unless a test installs one. They never change what is proven; they let a test
# stand in for a slow native call, a failed proof or a job that does not empty, which cannot be produced on demand.
HOOKS = {}


def _hook(name, *args):
    hook = HOOKS.get(name)
    return hook(*args) if hook is not None else None


class LaunchRefused(Exception):
    """The launch was refused or not proven. `code` is a foundation diagnostic code; no process of it is running."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class RawRun:
    exit_code: int = None
    timed_out: bool = False
    terminated: bool = False
    raw_stdout: bytes = b""
    raw_stderr: bytes = b""
    stdout_total: int = 0
    stderr_total: int = 0
    duration: float = 0.0
    tree_contained: bool = True
    capture_complete: bool = True
    descendants_terminated: int = 0


def _error(code, what):
    err = ctypes.get_last_error()
    return LaunchRefused(code, f"{what} failed (Windows error {err}: {ctypes.FormatError(err).strip()})")


def _close(handle):
    if handle:
        _k32.CloseHandle(handle)


def _image(process):
    size = wintypes.DWORD(pw.MAX_FINAL_PATH)
    buf = ctypes.create_unicode_buffer(size.value)
    if not _k32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
        return None
    return buf.value


def _in_job(process, job):
    flag = wintypes.BOOL(False)
    if not _k32.IsProcessInJob(process, job, ctypes.byref(flag)):
        return False
    return bool(flag.value)


def active_processes(job):
    """How many processes the job reports active, or None when it cannot be queried."""
    override = _hook("active_processes", job)
    if override is not None:
        return override
    data = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    if not _k32.QueryInformationJobObject(job, JOB_BASIC_ACCOUNTING, ctypes.byref(data), ctypes.sizeof(data), None):
        return None
    return data.ActiveProcesses


def _console_host():
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "C:\\Windows"
    return os.path.join(root, "System32", "conhost.exe")


def tool_descendants(job):
    """How many processes still in the job are the tool's own descendants: every member except the console host
    Windows itself creates for a console child (CREATE_NO_WINDOW gives it a hidden console). Informational only:
    every member, the console host included, is terminated regardless."""
    data = JOBOBJECT_BASIC_PROCESS_ID_LIST()
    if not _k32.QueryInformationJobObject(job, JOB_BASIC_PROCESS_ID_LIST, ctypes.byref(data), ctypes.sizeof(data),
                                          None):
        return None
    count, host = 0, _console_host()
    for pid in data.ProcessIdList[:data.NumberOfProcessIdsInList]:
        handle = _k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue                                   # already gone: nothing the tool left running
        try:
            if _k32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0:
                continue                               # exiting as the list was read
            image = _image(handle)
        finally:
            _k32.CloseHandle(handle)
        if image is not None and not pw.same_path(image, host):
            count += 1
    return count


def environment_block(environment):
    """A Unicode environment block: `name=value` strings sorted case-insensitively, double-NUL terminated."""
    entries = sorted(environment.items(), key=lambda kv: kv[0].upper())
    return "".join(f"{k}={v}\0" for k, v in entries) + "\0"


def _create_job():
    job = _k32.CreateJobObjectW(None, None)   # unnamed: no other process can open it by name
    if not job:
        raise _error("EXECUTION_FAILED", "CreateJobObjectW")
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_LIMITS
    if not _k32.SetInformationJobObject(job, JOB_EXTENDED_LIMIT, ctypes.byref(limits), ctypes.sizeof(limits)):
        exc = _error("EXECUTION_FAILED", "SetInformationJobObject")
        _close(job)
        raise exc
    return job


def _attribute_list(handles, jobs):
    size = ctypes.c_size_t(0)
    _k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or not size.value:
        raise _error("PLATFORM_UNSUPPORTED", "InitializeProcThreadAttributeList (size)")
    buffer = (ctypes.c_byte * size.value)()
    if not _k32.InitializeProcThreadAttributeList(buffer, 2, 0, ctypes.byref(size)):
        raise _error("PLATFORM_UNSUPPORTED", "InitializeProcThreadAttributeList")
    for attribute, value in ((PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles), (PROC_THREAD_ATTRIBUTE_JOB_LIST, jobs)):
        if not _k32.UpdateProcThreadAttribute(buffer, 0, attribute, ctypes.byref(value), ctypes.sizeof(value),
                                              None, None):
            exc = _error("PLATFORM_UNSUPPORTED", f"UpdateProcThreadAttribute (0x{attribute:x})")
            _k32.DeleteProcThreadAttributeList(buffer)
            raise exc
    return buffer


def _kill_unproven(process, job):
    """Terminate a child that never ran (still suspended), whether or not it is in the job, and wait briefly."""
    if job:
        _k32.TerminateJobObject(job, TERMINATED_EXIT)
    _k32.TerminateProcess(process, TERMINATED_EXIT)   # the one call site: a suspended child that was not proven
    _k32.WaitForSingleObject(process, int(CLEANUP_SECONDS * 1000))


def _launch_error(err, executable):
    if err in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, ERROR_ACCESS_DENIED, ERROR_BAD_EXE_FORMAT,
               ERROR_EXE_MACHINE_TYPE_MISMATCH):
        return LaunchRefused("TOOL_NOT_FOUND", f"{executable}: the process could not be started (Windows error {err}: "
                                               f"{ctypes.FormatError(err).strip()})")
    return LaunchRefused("EXECUTION_FAILED", f"{executable}: the process could not be started (Windows error {err}: "
                                             f"{ctypes.FormatError(err).strip()})")


_CREATE_LOCK = threading.Lock()


def run(spec, command_line, environment, drain, clock):
    """Run one validated spec under the mechanism in the module docstring. Returns a RawRun; raises LaunchRefused
    when no process ran (or one that never ran was terminated)."""
    if len(command_line) > MAX_COMMAND_LINE:
        raise LaunchRefused("UNSAFE_PROCESS_SPEC", "the command line exceeds the Windows limit of 32767 characters")
    started = clock()                  # the caller's clock measures the duration only, as on POSIX
    now = time.monotonic               # deadlines and bounds are real time
    deadline = now() + spec.timeout
    pins, fds, job, attributes, info = [], [], None, None, None
    exe_handle = None
    readers, sinks = [], ({"data": bytearray(), "total": 0}, {"data": bytearray(), "total": 0})
    result = RawRun()
    try:
        # Φ0 setup
        try:
            exe_handle, exe_final, exe_pins = pw.pin_executable(spec.executable)
        except pw.Unavailable as exc:
            raise LaunchRefused("TOOL_NOT_FOUND", str(exc)) from None
        except pw.PathRefused as exc:
            raise LaunchRefused("UNSAFE_PROCESS_SPEC", str(exc)) from None
        pins.extend(exe_pins)
        try:
            pins.extend(pw.pin_chain(spec.cwd))
        except pw.PathRefused as exc:
            raise LaunchRefused("UNSAFE_EXECUTION_PATH", str(exc)) from None
        out_r, out_w = os.pipe()
        fds += [out_r, out_w]
        err_r, err_w = os.pipe()
        fds += [err_r, err_w]
        null = os.open(os.devnull, os.O_RDONLY | os.O_BINARY)
        fds.append(null)
        child = [msvcrt.get_osfhandle(fd) for fd in (null, out_w, err_w)]
        job = _create_job()
        handles = (wintypes.HANDLE * 3)(*child)
        jobs = (wintypes.HANDLE * 1)(job)
        attributes = _attribute_list(handles, jobs)
        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = child
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        command = ctypes.create_unicode_buffer(command_line)          # writable: CreateProcessW may modify it
        block = ctypes.create_unicode_buffer(environment_block(environment))
        _hook("setup_done")
        if now() >= deadline:
            result.timed_out, result.exit_code = True, None
            return result
        # Φ1 create (the child is created suspended and inside the job, or not at all)
        info = PROCESS_INFORMATION()
        with _CREATE_LOCK:
            for h in child:
                os.set_handle_inheritable(h, True)
            _hook("before_create")
            created = _k32.CreateProcessW(spec.executable, command, None, None, True, CREATION_FLAGS, block, spec.cwd,
                                          ctypes.byref(startup), ctypes.byref(info))
            err = ctypes.get_last_error()
            for fd in (out_w, err_w, null):          # Φ2b: the parent keeps no copy of the child's handles
                os.close(fd)
                fds.remove(fd)
        if not created:
            info = None
            raise _launch_error(err, spec.executable)
        _hook("after_create", info, job)
        # Φ2 prove, while nothing of the child has run
        image = _hook("image", info) or _image(info.hProcess)
        in_job = _in_job(info.hProcess, job) and not _hook("deny_membership")
        if not (in_job and pw.same_path(image, exe_final)):
            _kill_unproven(info.hProcess, job)
            why = "is not a member of its job" if not in_job else \
                f"runs {image or 'an unreadable image'}, not the pinned {exe_final}"
            raise LaunchRefused("EXECUTION_FAILED", f"{spec.executable}: the suspended child {why}; it was terminated "
                                                    f"before any of its code ran")
        if now() >= deadline:                      # Φ1 overran the deadline: never resumed
            _k32.TerminateJobObject(job, TERMINATED_EXIT)
            result.timed_out = result.terminated = True
        else:
            readers = [threading.Thread(target=drain, args=(os.fdopen(fd, "rb"), spec.capture_bytes, sink),
                                        daemon=True) for fd, sink in ((out_r, sinks[0]), (err_r, sinks[1]))]
            fds.remove(out_r)
            fds.remove(err_r)
            for reader in readers:
                reader.start()
            # Φ3 run
            _hook("before_resume", info, readers)
            if _k32.ResumeThread(info.hThread) == 0xFFFFFFFF:
                _k32.TerminateJobObject(job, TERMINATED_EXIT)
                raise _error("EXECUTION_FAILED", "ResumeThread")
            while True:
                remaining = deadline - now()
                if remaining <= 0:
                    result.timed_out = True
                    break
                state = _k32.WaitForSingleObject(info.hProcess, min(int(remaining * 1000) + 1, MAX_WAIT_MS))
                if state == WAIT_OBJECT_0:
                    break
                if state != WAIT_TIMEOUT:
                    result.timed_out = True          # the wait itself failed: treat the run as not finished
                    break
            if result.timed_out:
                result.terminated = True
                _k32.TerminateJobObject(job, TERMINATED_EXIT)
                _k32.WaitForSingleObject(info.hProcess, int(CLEANUP_SECONDS * 1000))
            else:
                code = wintypes.DWORD()
                result.exit_code = code.value if _k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)) else None
        # Φ5 cleanup: terminate whatever is left in the job and observe it empty
        active = active_processes(job)
        if active is None or active > 0:
            if not result.timed_out:   # D9: what the tool left running when it exited (a timeout already ended all)
                result.descendants_terminated = tool_descendants(job) or 0
            _k32.TerminateJobObject(job, TERMINATED_EXIT)
            end = now() + CLEANUP_SECONDS
            while True:
                active = active_processes(job)
                if active == 0 or now() >= end:
                    break
                time.sleep(POLL_SECONDS)
        result.tree_contained = active == 0
        # Φ6 drain
        end = now() + CAPTURE_DRAIN_SECONDS
        for reader in readers:
            reader.join(timeout=max(0.0, end - now()))
        result.capture_complete = not any(reader.is_alive() for reader in readers) \
            and not _hook("capture_incomplete")
        if result.timed_out or not result.tree_contained or not result.capture_complete:
            result.exit_code = None
        result.raw_stdout, result.raw_stderr = bytes(sinks[0]["data"]), bytes(sinks[1]["data"])
        result.stdout_total, result.stderr_total = sinks[0]["total"], sinks[1]["total"]
        result.duration = clock() - started
        return result
    finally:
        if info is not None:
            _close(info.hThread)
            _close(info.hProcess)
        if attributes is not None:
            _k32.DeleteProcThreadAttributeList(attributes)
        if job is not None:
            _close(job)                               # KILL_ON_JOB_CLOSE: nothing of the job outlives this
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass
        pw.close(exe_handle)
        pw.close_all(pins)


def pid_alive(pid):
    """Whether a process with this id exists, judged from a handle: never a signal, never a console event.

    An id that names no process is False. A process that exists but cannot be opened (access denied) is reported
    alive, so a lease is never judged stale on the strength of an unreadable process."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or pid > 0xFFFFFFFF:
        return False
    handle = _k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() != ERROR_INVALID_PARAMETER
    try:
        state = _k32.WaitForSingleObject(handle, 0)
        if state == WAIT_TIMEOUT:
            return True
        if state == WAIT_OBJECT_0:
            return False
        code = wintypes.DWORD()
        if _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return True
    finally:
        _k32.CloseHandle(handle)
