"""Whether a Unity project is held by an Editor: a read-only proof for the batch test plane (Phase 2C-6C; macOS).

A Unity Editor that has a project open holds an exclusive whole-file lock (flock) on `<project>/Temp/UnityLockfile`.
A batch run that stops on a compile error exits without Unity's own `Temp/` clean-up, and the file stays behind,
unheld, for as long as nothing opens the project (measured on Unity 6000.5.8f1: more than 600 s in 3 of 3 runs; a
later Unity launch accepts the project and replaces the file itself). The file's presence alone is therefore no proof
of an open Editor, and its absence alone is no proof that none is starting. `assess` decides one of:

    NO_LOCK             no lockfile, and no Unity process of this user has the project open (proven twice)
    ACTIVE_EDITOR       a Unity process of this user has exactly this project open, or the OS reports the lock held
    ORPHAN_UNHELD       a regular lockfile nobody holds (F_GETLK: unlocked), and no Unity process of this user has the
                        project open — proven before and after the lock query
    LOCK_STATE_UNKNOWN  anything that cannot be proven either way: it fails closed

Process proof (read-only, bounded): the kernel lists this user's processes (proc_listpids, PROC_UID_ONLY); for each
one the kernel's executable path (proc_pidpath) is compared with the discovered Hub Editor; for every such process the
exact argument vector is read (sysctl KERN_PROCARGS2) and must carry exactly one `-projectPath` (Unity's flags are
case-insensitive) with an absolute value that is this project (same file identity, or the same real path). A missing,
repeated, relative or unreadable project argument, an unreadable candidate, a truncated listing or any bound exceeded
is PROCESS_STATE_UNKNOWN — never "no process". Import workers run the same executable with the same `-projectPath` and
count as the project's Editor. Joined `ps` text is never parsed. Processes of other users are not listed; an Editor
of another user that holds the project is still seen through the lock query.

Lock proof (read-only): the file is lstat'ed (a link, or anything but a regular file, is unknown), opened read-only
with O_NOFOLLOW | O_NONBLOCK, fstat'ed (it must be the same regular file) and queried with fcntl F_GETLK for a
whole-file write lock. No lock is ever taken, and the file is never written, truncated, renamed or removed.

Between the final proof and the launch there is an unavoidable race; Unity arbitrates it with its own project lock
(a second Editor refuses the project) and the adapter reports that refusal as ENGINE_PROJECT_LOCKED.
"""

import ctypes
import errno
import fcntl
import os
import stat
import struct
import sys
from dataclasses import dataclass, field

MATCHING_EDITOR, NO_MATCH_PROVEN, PROCESS_STATE_UNKNOWN = "MATCHING_EDITOR", "NO_MATCH_PROVEN", "PROCESS_STATE_UNKNOWN"
NO_LOCK, ACTIVE_EDITOR, ORPHAN_UNHELD, LOCK_STATE_UNKNOWN = "NO_LOCK", "ACTIVE_EDITOR", "ORPHAN_UNHELD", "LOCK_STATE_UNKNOWN"
ABSENT, HELD, UNHELD, UNKNOWN = "ABSENT", "HELD", "UNHELD", "UNKNOWN"
PROCEED = (NO_LOCK, ORPHAN_UNHELD)

MAX_PIDS = 16384
MAX_CANDIDATES = 64
MAX_ARGV_BYTES = 256 * 1024
MAX_ARGC = 4096
PATH_BYTES = 4096                  # PROC_PIDPATHINFO_MAXSIZE
NAME_BYTES = 64
LIBSYSTEM = "/usr/lib/libSystem.B.dylib"
PROC_UID_ONLY = 4
CTL_KERN, KERN_PROCARGS2 = 1, 49
UNITY_COMMAND = "Unity"            # the kernel's command name of the Editor and of its import workers
PROJECT_FLAG = "-projectpath"
LOCKFILE = ("Temp", "UnityLockfile")
FLOCK = "qqihh"                    # struct flock (macOS): off_t l_start, off_t l_len, pid_t l_pid, short l_type, l_whence
OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY


@dataclass
class Assessment:
    state: str
    processes: list = field(default_factory=list)   # each process proof: (state, reason)
    lock: str = ABSENT
    reasons: list = field(default_factory=list)

    def details(self):
        return {"lock_state": self.state, "lock": self.lock, "process_proofs": [p[0] for p in self.processes],
                "reasons": self.reasons[:8]}


class Darwin:
    """The read-only macOS calls the proofs use. Nothing here writes, signals, locks or starts anything."""

    def __init__(self):
        self._lib = None

    def supported(self):
        return sys.platform == "darwin"

    def _libsystem(self):
        if self._lib is None:
            lib = ctypes.CDLL(LIBSYSTEM, use_errno=True)
            lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
            lib.proc_listpids.restype = ctypes.c_int
            lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            lib.proc_pidpath.restype = ctypes.c_int
            lib.proc_name.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            lib.proc_name.restype = ctypes.c_int
            lib.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
            lib.sysctl.restype = ctypes.c_int
            self._lib = lib
        return self._lib

    @staticmethod
    def _errno():
        return errno.errorcode.get(ctypes.get_errno(), "E?")

    def uid(self):
        return os.getuid()

    def pids(self, uid):
        """([pid], None) of this user's processes, or (None, problem): a full buffer counts as truncated."""
        lib = self._libsystem()
        buf = (ctypes.c_int * MAX_PIDS)()
        ctypes.set_errno(0)
        n = lib.proc_listpids(PROC_UID_ONLY, uid, buf, ctypes.sizeof(buf))
        if n <= 0:
            return None, f"the process list could not be read ({self._errno()})"
        if n >= ctypes.sizeof(buf):
            return None, "the process list reached its bound and may be truncated"
        return [buf[i] for i in range(n // ctypes.sizeof(ctypes.c_int)) if buf[i] > 0], None

    def exe(self, pid):
        """(kernel executable path, None) or (None, errno name)."""
        buf = ctypes.create_string_buffer(PATH_BYTES)
        ctypes.set_errno(0)
        n = self._libsystem().proc_pidpath(pid, buf, PATH_BYTES)
        if n <= 0:
            return None, self._errno()
        try:
            return buf.raw[:n].decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "EILSEQ"

    def command(self, pid):
        """(the kernel's command name, None) or (None, errno name)."""
        buf = ctypes.create_string_buffer(NAME_BYTES)
        ctypes.set_errno(0)
        n = self._libsystem().proc_name(pid, buf, NAME_BYTES)
        if n <= 0:
            return None, self._errno()
        return buf.value.decode("utf-8", errors="replace"), None

    def argv(self, pid):
        """(exact argv list, None) or (None, problem), bounded by MAX_ARGV_BYTES."""
        lib = self._libsystem()
        mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
        size = ctypes.c_size_t(0)
        ctypes.set_errno(0)
        if lib.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            return None, self._errno()
        if size.value > MAX_ARGV_BYTES:
            return None, "the argument vector exceeds its bound"
        buf = ctypes.create_string_buffer(size.value)
        ctypes.set_errno(0)
        if lib.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
            return None, self._errno()
        return parse_procargs(buf.raw[:size.value])

    lstat = staticmethod(os.lstat)
    stat = staticmethod(os.stat)
    realpath = staticmethod(os.path.realpath)

    @staticmethod
    def open_readonly(path):
        return os.open(path, OPEN_FLAGS)

    fstat = staticmethod(os.fstat)
    close = staticmethod(os.close)

    @staticmethod
    def getlk(fd):
        """The lock type another process holds on the whole file (F_UNLCK when none); never takes a lock."""
        query = struct.pack(FLOCK, 0, 0, 0, fcntl.F_WRLCK, os.SEEK_SET)
        answer = fcntl.fcntl(fd, fcntl.F_GETLK, query)
        if len(answer) != struct.calcsize(FLOCK):
            raise OSError(errno.EPROTO, "unexpected F_GETLK answer")
        return struct.unpack(FLOCK, answer)[3]


DARWIN = Darwin()


def parse_procargs(raw):
    """The argv of a KERN_PROCARGS2 buffer: int argc, the exec path, NUL padding, then argc NUL-terminated strings."""
    if len(raw) < 4:
        return None, "the argument vector is malformed"
    argc = int.from_bytes(raw[:4], sys.byteorder, signed=True)
    if not 0 < argc <= MAX_ARGC:
        return None, "the argument count is malformed"
    rest = raw[4:]
    end = rest.find(b"\0")
    if end < 0:
        return None, "the argument vector is malformed"
    i = end
    while i < len(rest) and rest[i] == 0:
        i += 1
    args = []
    for _ in range(argc):
        j = rest.find(b"\0", i)
        if j < 0:
            return None, "the argument vector is truncated"
        try:
            args.append(rest[i:j].decode("utf-8"))
        except UnicodeDecodeError:
            return None, "an argument is not UTF-8"
        i = j + 1
    return args, None


def _project_of(argv, project, osx):
    """MATCHING_EDITOR when the exact argv opens exactly this project, NO_MATCH_PROVEN for another project, else
    PROCESS_STATE_UNKNOWN (missing, repeated, relative or unreadable -projectPath)."""
    args = argv[1:]
    flags = [i for i, a in enumerate(args) if a.lower() == PROJECT_FLAG]
    if any(a.lower().startswith(PROJECT_FLAG) and a.lower() != PROJECT_FLAG for a in args):
        return PROCESS_STATE_UNKNOWN, "a -projectPath argument is malformed"
    if len(flags) != 1 or flags[0] + 1 >= len(args):
        return PROCESS_STATE_UNKNOWN, ("an Editor names no project" if not flags else
                                       "an Editor names its project ambiguously")
    value = args[flags[0] + 1]
    if not os.path.isabs(value):
        return PROCESS_STATE_UNKNOWN, "an Editor names its project with a relative path"
    try:
        theirs = osx.stat(value)
    except (FileNotFoundError, NotADirectoryError):
        return NO_MATCH_PROVEN, None
    except OSError:
        return PROCESS_STATE_UNKNOWN, "an Editor's project cannot be examined"
    try:
        ours = osx.stat(project)
        same = (theirs.st_dev, theirs.st_ino) == (ours.st_dev, ours.st_ino) or osx.realpath(value) == osx.realpath(project)
    except OSError:
        return PROCESS_STATE_UNKNOWN, "the project cannot be examined"
    return (MATCHING_EDITOR, None) if same else (NO_MATCH_PROVEN, None)


def process_proof(project, editor, osx=DARWIN):
    """(MATCHING_EDITOR | NO_MATCH_PROVEN | PROCESS_STATE_UNKNOWN, reason or None) for this user's processes."""
    if not osx.supported():
        return PROCESS_STATE_UNKNOWN, "the process proof is measured on macOS only"
    try:
        editor = osx.realpath(editor)
        pids, problem = osx.pids(osx.uid())
        if problem:
            return PROCESS_STATE_UNKNOWN, problem
        candidates = []
        for pid in pids:
            path, err = osx.exe(pid)
            if path is None:
                if err == "ESRCH":            # gone, or a zombie: it holds nothing
                    continue
                name, nerr = osx.command(pid)
                if name is None and nerr == "ESRCH":
                    continue
                if name is None or name == UNITY_COMMAND:
                    return PROCESS_STATE_UNKNOWN, f"process {pid} may be a Unity Editor and cannot be inspected ({err})"
                continue
            if path != editor:
                continue
            candidates.append(pid)
            if len(candidates) > MAX_CANDIDATES:
                return PROCESS_STATE_UNKNOWN, "more Unity Editor processes than the proof inspects"
        matched = False
        for pid in candidates:
            argv, problem = osx.argv(pid)
            if argv is None:
                if osx.exe(pid)[1] == "ESRCH":   # it exited meanwhile
                    continue
                return PROCESS_STATE_UNKNOWN, f"the arguments of Unity process {pid} cannot be read ({problem})"
            verdict, why = _project_of(argv, project, osx)
            if verdict == PROCESS_STATE_UNKNOWN:
                return verdict, f"Unity process {pid}: {why}"
            matched = matched or verdict == MATCHING_EDITOR
        return (MATCHING_EDITOR, None) if matched else (NO_MATCH_PROVEN, None)
    except (OSError, ValueError, AttributeError) as exc:
        return PROCESS_STATE_UNKNOWN, f"the process proof failed ({type(exc).__name__})"


def lock_facts(project, osx=DARWIN):
    """(ABSENT | HELD | UNHELD | UNKNOWN, reason or None) of Temp/UnityLockfile. Read-only; never takes a lock."""
    if not osx.supported():
        return UNKNOWN, "the lock query is measured on macOS only"
    temp = os.path.join(project, LOCKFILE[0])
    lock = os.path.join(temp, LOCKFILE[1])
    try:
        st = osx.lstat(temp)
    except FileNotFoundError:
        return ABSENT, None
    except OSError as exc:
        return UNKNOWN, f"Temp/ cannot be examined ({type(exc).__name__})"
    if not stat.S_ISDIR(st.st_mode):
        return UNKNOWN, "Temp/ is a link or not a folder"
    try:
        st = osx.lstat(lock)
    except FileNotFoundError:
        return ABSENT, None
    except OSError as exc:
        return UNKNOWN, f"the lockfile cannot be examined ({type(exc).__name__})"
    if stat.S_ISLNK(st.st_mode):
        return UNKNOWN, "the lockfile is a symbolic link"
    if not stat.S_ISREG(st.st_mode):
        return UNKNOWN, "the lockfile is not a regular file"
    try:
        fd = osx.open_readonly(lock)
    except FileNotFoundError:
        return ABSENT, None                   # removed meanwhile (by Unity)
    except OSError as exc:
        return UNKNOWN, f"the lockfile cannot be opened read-only ({type(exc).__name__})"
    try:
        now = osx.fstat(fd)
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            return UNKNOWN, "the lockfile changed while it was examined"
        kind = osx.getlk(fd)
    except (OSError, struct.error, ValueError) as exc:
        return UNKNOWN, f"the lock query failed ({type(exc).__name__})"
    finally:
        osx.close(fd)
    if kind == fcntl.F_UNLCK:
        return UNHELD, None
    if kind in (fcntl.F_RDLCK, fcntl.F_WRLCK):
        return HELD, None
    return UNKNOWN, "the lock query answered an unknown lock type"


def assess(project, editor, osx=DARWIN):
    """The project's effective lock state (Assessment). The process proof runs before and after the lock query."""
    project = str(project)
    a = Assessment(state=LOCK_STATE_UNKNOWN)
    first = process_proof(project, editor, osx)
    a.processes.append(first)
    if first[0] == MATCHING_EDITOR:
        a.state = ACTIVE_EDITOR
        a.reasons.append("a Unity process of this user has this project open")
        return a
    a.lock, why = lock_facts(project, osx)
    if a.lock == HELD:
        a.state = ACTIVE_EDITOR
        a.reasons.append("the OS reports Temp/UnityLockfile locked by another process")
        return a
    if a.lock == UNKNOWN or first[0] != NO_MATCH_PROVEN:
        a.reasons.extend(r for r in (why, first[1]) if r)
        return a
    final = process_proof(project, editor, osx)
    a.processes.append(final)
    if final[0] == MATCHING_EDITOR:
        a.state = ACTIVE_EDITOR
        a.reasons.append("a Unity process of this user opened this project during the proof")
        return a
    if final[0] != NO_MATCH_PROVEN:
        a.reasons.append(final[1])
        return a
    a.state = NO_LOCK if a.lock == ABSENT else ORPHAN_UNHELD
    if a.state == ORPHAN_UNHELD:
        a.reasons.append("Temp/UnityLockfile is a regular file nobody holds, and no Unity process of this user has the "
                         "project open")
    return a
