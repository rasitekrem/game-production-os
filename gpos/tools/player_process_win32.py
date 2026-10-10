"""Private Player lifecycle boundary, never a detached command runner.

Only a fixed audited supervisor and a fully revalidated Windows Player are created.
Batch run / spawn_detached stay unchanged. Atomic JOB_LIST assignment prevents a
creator-crash window outside containment. Only the supervisor inherits a Job handle.
No Player descendant can inherit it or break away. All signalling uses retained,
creation-time-proven process handles; PID identifiers stay reserved while held.
"""
import ctypes
import hashlib
import json
import msvcrt
import os
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from . import paths_win32 as fs, process_win32 as batch
from .unity import host_win32 as host, build_win32 as build

_k32 = ctypes.WinDLL('kernel32', use_last_error=True)


def _bind(name, restype, *args):
    function = getattr(_k32, name)
    function.restype, function.argtypes = restype, list(args)
    return function


_bind('OpenJobObjectW', wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_bind('SetHandleInformation', wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
_bind('GenerateConsoleCtrlEvent', wintypes.BOOL, wintypes.DWORD, wintypes.DWORD)
_LOCK = threading.Lock()
JOB_QUERY, JOB_TERMINATE = 4, 8
EXIT_FORCED = 0xE0475053
SOURCE = Path(__file__).resolve().parent.parent.parent
HELPER = Path(__file__).resolve().parent/'player'/'windows_supervisor.py'
HELPER_FILES = (Path(__file__), HELPER, HELPER.with_name('windows_records.py'),
                HELPER.with_name('windows_resolver.py'), Path(batch.__file__), Path(fs.__file__),
                Path(build.__file__), Path(host.__file__))


class LifecycleRefused(ValueError):
    pass


def helper_digest():
    digest = hashlib.sha256()
    for path in HELPER_FILES:
        digest.update(path.relative_to(SOURCE).as_posix().encode()+b'\0')
        digest.update(build.read_file(path, 1024*1024))
    return digest.hexdigest()


def helper_argv(directory, job):
    return [str(Path(sys.executable).resolve()), '-I', '-B', '-X', 'utf8', str(HELPER), str(directory), str(job)]


def job_name(session):
    from .leases import SESSION_ID
    if any(not SESSION_ID.fullmatch(str(session.get(k, ''))) for k in ('session_id', 'nonce')):
        raise LifecycleRefused('invalid session or nonce')
    return 'Local\\GPOS-Player-'+session['session_id']+'-'+session['nonce']


def parent_permission():
    flag = wintypes.BOOL()
    if not batch._k32.IsProcessInJob(wintypes.HANDLE(-1), None, ctypes.byref(flag)):
        raise LifecycleRefused('current Job state is unknown')
    if not flag.value:
        return 0
    limits = batch.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    if not batch._k32.QueryInformationJobObject(None, 9, ctypes.byref(limits), ctypes.sizeof(limits), None):
        raise LifecycleRefused('parent Job limits are unknown')
    if not limits.BasicLimitInformation.LimitFlags & 0x800:
        raise LifecycleRefused('parent Job does not explicitly permit breakaway; batch containment cannot be bypassed')
    return 0x01000000  # only the fixed supervisor; kernel checks the complete nested hierarchy


def members(job):
    data = batch.JOBOBJECT_BASIC_PROCESS_ID_LIST()
    if not batch._k32.QueryInformationJobObject(job, 3, ctypes.byref(data), ctypes.sizeof(data), None):
        raise LifecycleRefused('Job membership is unknown')
    if data.NumberOfAssignedProcesses != data.NumberOfProcessIdsInList or data.NumberOfProcessIdsInList > batch.MAX_JOB_PIDS:
        raise LifecycleRefused('Job membership exceeds bound or is incomplete')
    return list(data.ProcessIdList[:data.NumberOfProcessIdsInList])


def open_job(session, terminate=False):
    job = _k32.OpenJobObjectW(JOB_QUERY | (JOB_TERMINATE if terminate else 0), False, job_name(session))
    if not job and ctypes.get_last_error() != 2:
        raise LifecycleRefused('Job ownership is unknown')
    return job


def facts(pid):
    process, problem = host.open_process(pid)
    if problem:
        raise LifecycleRefused(problem)
    if not process:
        return None
    try:
        created, image, running, user = process.created(), process.image(), process.running(), process.same_user()
        argv, error = process.argv()
        if None in (created, image, running, user) or error or not user:
            raise LifecycleRefused('process identity/user/command line is unknown or foreign')
        # PROCESS_BASIC_INFORMATION's final pointer is InheritedFromUniqueProcessId.
        data = (ctypes.c_void_p*6)();needed = wintypes.ULONG()
        query = host._bind('NtQueryInformationProcess', ctypes.c_long, wintypes.HANDLE, ctypes.c_int,
                          ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))
        if query(process._h, 0, data, ctypes.sizeof(data), ctypes.byref(needed)) != 0:
            raise LifecycleRefused('parent process identity is unknown')
        return {'pid': pid, 'created_filetime': created, 'executable': image, 'parent_pid': int(data[5] or 0),
                'argv': argv, 'running': running, 'same_user': True}
    finally:
        process.close()


def identity(expected, job=None):
    process, problem = host.open_process(expected['pid'])
    if problem:
        return 'UNPROVEN'
    if not process:
        return 'GONE'
    try:
        if process.created() != expected['created_filetime'] or process.image() != expected['executable']:
            return 'NOT_THIS_PROCESS'
        if process.same_user() is not True:
            return 'UNPROVEN'
        running = process.running()
        if running is False:
            return 'GONE'
        if running is not True or (job and not batch._in_job(process._h, job)):
            return 'UNPROVEN'
        return 'PROVEN'
    finally:
        process.close()


def foreign(executable):
    snapshot, problem = host.processes()
    if problem:
        raise LifecycleRefused(problem)
    result = []
    for pid, name in snapshot:
        if name.casefold() != 'player.exe':
            continue
        process, error = host.open_process(pid)
        if error:
            raise LifecycleRefused('Player process state is unknown')
        if not process:
            continue
        try:
            image = process.image()
            if image is None:
                raise LifecycleRefused('Player executable identity is unknown')
            if image.casefold() == executable.casefold() and process.running() is not False:
                result.append(pid)
        finally:
            process.close()
    return result


def _create(executable, argv, directory, job, inherit_job=False, flags=0, environment=None):
    """Internal fixed call sites only. Atomic Job assignment, explicit sole NUL/Job handle inheritance."""
    from .process import _player_command_line
    null = os.open(os.devnull, os.O_RDWR | os.O_BINARY)
    nh = msvcrt.get_osfhandle(null)
    inherited = [nh, job] if inherit_job else [nh]
    handles = (wintypes.HANDLE*len(inherited))(*inherited)
    jobs = (wintypes.HANDLE*1)(job)
    attributes = None
    pi = batch.PROCESS_INFORMATION()
    try:
        attributes = batch._attribute_list(handles, jobs)
        startup = batch.STARTUPINFOEXW();startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100 | 1;startup.StartupInfo.wShowWindow = 0
        startup.StartupInfo.hStdInput = startup.StartupInfo.hStdOutput = startup.StartupInfo.hStdError = nh
        startup.lpAttributeList = ctypes.addressof(attributes)
        with _LOCK:
            try:
                for h in inherited:
                    if not _k32.SetHandleInformation(h, 1, 1):
                        raise LifecycleRefused('handle inheritance could not be restricted')
                ok = batch._k32.CreateProcessW(executable, ctypes.create_unicode_buffer(_player_command_line(argv)),
                     None, None, True, 0x80000 | 0x400 | flags,
                     ctypes.create_unicode_buffer(batch.environment_block(environment)), str(directory),
                     ctypes.byref(startup), ctypes.byref(pi))
                if not ok:
                    raise LifecycleRefused('fixed Player process creation failed: '+str(ctypes.get_last_error()))
            finally:
                for h in inherited:
                    _k32.SetHandleInformation(h, 1, 0)
    finally:
        if attributes is not None:
            batch._k32.DeleteProcThreadAttributeList(attributes)
        os.close(null)
    if not flags & 4:
        batch._close(pi.hThread)
    if not batch._in_job(pi.hProcess, job):
        # Atomic JOB_LIST should make this impossible; terminate only the directly held new process.
        batch._k32.TerminateProcess(pi.hProcess, EXIT_FORCED);batch._close(pi.hProcess)
        if flags & 4:
            batch._close(pi.hThread)
        raise LifecycleRefused('new process Job membership is unproven')
    return pi


def spawn_supervisor(root, directory, session, scopes):
    """No caller executable/argv/env. Called only through the player.launch context gate."""
    from .player import windows_records as records, windows_resolver as resolver
    from .process import EnvironmentPolicy
    root, directory = Path(root), Path(directory)
    if directory != root/'.game/gpos-runtime/tool-output/player'/session['launch_request_id']:
        raise LifecycleRefused('supervisor directory differs from the fixed launch workspace')
    if fs.unsafe_reason(scopes, directory, must_exist=True):
        raise LifecycleRefused('supervisor workspace is outside its scopes')
    target = resolver.resolve(root, session['build_id'])
    if any(target[k] != session[k] for k in ('executable', 'executable_identity', 'manifest_sha256', 'tree_digest')):
        raise LifecycleRefused('launch target drifted')
    if records.read(directory/'supervisor-request.json') != session or helper_digest() != session['helper_digest']:
        raise LifecycleRefused('supervisor request or fixed code differs')
    flags = parent_permission()
    job = batch._k32.CreateJobObjectW(None, job_name(session))
    if not job:
        raise LifecycleRefused('Player Job creation failed')
    if ctypes.get_last_error() == 183:
        batch._close(job);raise LifecycleRefused('Player Job already exists; never adopt it')
    try:
        limits = batch.JOBOBJECT_EXTENDED_LIMIT_INFORMATION();limits.BasicLimitInformation.LimitFlags = batch.JOB_LIMITS
        if not batch._k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise LifecycleRefused('Player Job kill-on-close could not be set')
        # No CREATE_SUSPENDED here: an interrupted creator must not strand a suspended Job-handle owner.
        pi = _create(str(Path(sys.executable).resolve()), helper_argv(directory, job), directory, job,
                     inherit_job=True, flags=0x10 | flags, environment=EnvironmentPolicy().build())
        try:
            return {'pid': pi.dwProcessId, 'job_name': job_name(session), 'identity': facts(pi.dwProcessId),
                    'command': {'executable': str(Path(sys.executable).resolve()), 'argv': helper_argv(directory, job)[1:]},
                    'environment': EnvironmentPolicy().metadata()}
        finally:
            batch._close(pi.hProcess)
    finally:
        batch._close(job)


class OwnedPlayer:
    """Supervisor-held process handle and payload/code pins. No file path supplied by a public request."""
    def __init__(self, root, session, job):
        from .player import windows_resolver as resolver
        from .process import EnvironmentPolicy
        self.fds, self.pins, self.process = [], [], None
        try:
            target = resolver.resolve(root, session['build_id'])
            if any(target[k] != session[k] for k in target):
                raise LifecycleRefused('build changed before owned launch')
            workspace = Path(target['workspace']);manifest = build.read_json(workspace/'build-manifest.json')
            self.pins += fs.pin_chain(workspace/'payload/Player')
            self.pins += fs.pin_chain(session['runtime_absolute'])
            files = [workspace/'build-manifest.json', *HELPER_FILES,
                     *[workspace/'payload/Player'/row['path'] for row in manifest['payload']['inventory'] if row['kind']=='file']]
            for path in files:
                fd, size, pins = fs.open_file_for_read(path, deny_writers=True)
                self.fds.append(fd);self.pins += pins
                if len(self.pins)>8192:
                    raise LifecycleRefused('runtime payload pins exceed the existing bound')
            if resolver.resolve(root, session['build_id']) != target or helper_digest() != session['helper_digest']:
                raise LifecycleRefused('build/helper changed while being pinned')
            exe = target['executable']
            # Player never inherits the Job handle; own children inherit Job membership, not authority.
            pi = _create(exe, [exe, '-logFile', str(Path(session['runtime_absolute'])/'player.log')],
                         Path(session['runtime_absolute']), job, flags=4 | 0x200, environment=EnvironmentPolicy().build())
            self.process = pi.hProcess;self.pid = pi.dwProcessId
            try:
                self.identity = facts(self.pid)
                if (not self.identity or self.identity['parent_pid'] != os.getpid() or self.identity['executable'] != exe or
                        not self.identity['same_user']):
                    raise LifecycleRefused('suspended Player identity is not proven')
                if batch._k32.ResumeThread(pi.hThread) == 0xffffffff:
                    raise LifecycleRefused('Player thread could not be resumed')
            finally:
                batch._close(pi.hThread)
        except BaseException:
            self.close();raise

    def close(self):
        if self.process:
            batch._close(self.process);self.process = None
        for fd in self.fds:
            os.close(fd)
        fs.close_all(self.pins);self.fds, self.pins = [], []

    def exited(self):
        result = batch._k32.WaitForSingleObject(self.process, 0)
        if result == batch.WAIT_OBJECT_0:
            code = wintypes.DWORD()
            if not batch._k32.GetExitCodeProcess(self.process, ctypes.byref(code)):
                raise LifecycleRefused('owned Player exit code is unknown')
            return code.value
        if result != batch.WAIT_TIMEOUT:
            raise LifecycleRefused('owned Player wait state is unknown')
        return None

    def graceful(self):
        # The retained process handle reserves this group id until it closes. Only our
        # own child's console group receives CTRL_BREAK, never group zero or another console.
        if self.exited() is not None:
            return False
        return bool(_k32.GenerateConsoleCtrlEvent(1, self.pid))

    def terminate(self):
        return bool(batch._k32.TerminateProcess(self.process, EXIT_FORCED))
