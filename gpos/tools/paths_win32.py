"""Windows file-system containment for the tool foundation (alpha.23). Private to the foundation.

Only gpos/tools/paths.py, artifacts.py, leases.py, execution.py and process_win32.py import this module, and (alpha.26,
D-L4) the engine adapter's live-bridge IPC client, which reads and pins bridge files with open_file_for_read only
(tests/validate_framework.py names it). It is not
a general file API: every function serves one reviewed foundation operation, takes an absolute path the
foundation already decided to touch, and refuses rather than falls back.

Layers (see tools/adapter-foundation.md, "Windows"):

* lexical: an absolute drive-letter path only; no UNC, device (\\\\?\\, \\\\.\\) or drive-relative path; no
  alternate data stream (`:`), reserved device name, trailing dot or space, or `<>"|?*` and control characters
  in any component;
* root and ancestor trust: every component from the volume root down is opened without following reparse points
  (FILE_FLAG_OPEN_REPARSE_POINT) and refused if it is a reparse point (a junction, symbolic link, mount point or
  any other reparse tag); its final path must equal the lexical path case-insensitively, which refuses SUBST
  drives and 8.3 short-name spellings; the volume must be a local fixed or removable NTFS volume;
* pinning: a directory is held open with a data right (FILE_LIST_DIRECTORY) and without FILE_SHARE_DELETE, so for
  as long as an operation runs no process can rename or delete any component of its path (CreateFileW: delete
  access "allows both delete and rename operations", and without FILE_SHARE_DELETE "no process can open the file
  or device if it requests delete access");
* opened-object identity: what was opened is judged from its handle (attributes, link count, final path), never
  from a second lookup of the path.
"""

import ctypes
import msvcrt
import os
import sys
from ctypes import wintypes
from pathlib import PureWindowsPath

if sys.platform != "win32":  # pragma: no cover - imported only on Windows
    raise ImportError("gpos.tools.paths_win32 exists only on Windows")

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ, GENERIC_WRITE, DELETE = 0x80000000, 0x40000000, 0x00010000
FILE_LIST_DIRECTORY, FILE_APPEND_DATA, FILE_READ_ATTRIBUTES, SYNCHRONIZE = 0x0001, 0x0004, 0x0080, 0x00100000
FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_SHARE_DELETE = 0x1, 0x2, 0x4
CREATE_NEW, OPEN_EXISTING, OPEN_ALWAYS = 1, 3, 4
FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT = 0x02000000, 0x00200000
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT = 0x10, 0x400
DRIVE_REMOVABLE, DRIVE_FIXED = 2, 3
FILE_DISPOSITION_INFO_CLASS = 4          # FILE_INFO_BY_HANDLE_CLASS FileDispositionInfo
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_SHARING_VIOLATION, ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS = 32, 80, 183
LOCAL_DRIVES = (DRIVE_FIXED, DRIVE_REMOVABLE)
FILESYSTEMS = ("NTFS",)                  # D11: local NTFS only in alpha.23; ReFS (Dev Drive) is deferred
RESERVED = ({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            | {f"{p}{n}" for p in ("COM", "LPT") for n in "0123456789¹²³"})
BAD_CHARS = set('<>"|?*') | {chr(c) for c in range(32)}
MAX_FINAL_PATH = 32768


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", FILETIME), ("ftLastAccessTime", FILETIME),
                ("ftLastWriteTime", FILETIME), ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD), ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD), ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD)]


class FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOLEAN)]


_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                             wintypes.DWORD, wintypes.HANDLE]
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
_k32.GetFileInformationByHandle.restype = wintypes.BOOL
_k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
_k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
_k32.GetVolumeInformationByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPDWORD,
                                               wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPWSTR, wintypes.DWORD]
_k32.GetVolumeInformationByHandleW.restype = wintypes.BOOL
_k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_k32.GetDriveTypeW.restype = wintypes.UINT
_k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
_k32.SetFileInformationByHandle.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL


class PathRefused(Exception):
    """The path is not one the foundation may touch on this host. `reason` is the human-readable why."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Unavailable(PathRefused):
    """An executable that is gone, not accessible or held exclusively by another process: a tool availability
    problem, not an unsafe path."""


# file not found, path not found, access denied, sharing violation
UNAVAILABLE_ERRORS = (2, 3, 5, ERROR_SHARING_VIOLATION)


# ---------------------------------------------------------------- lexical

def name_problem(part):
    """Why one path component is not a safe Windows name, or None."""
    if part in ("", ".", ".."):
        return f"{part!r} is not a name"
    if ":" in part:
        return f"{part!r} names an alternate data stream (':')"
    if part[-1] in ". ":
        return f"{part!r} ends with a dot or a space, which Windows silently strips"
    if any(c in BAD_CHARS for c in part):
        return f"{part!r} contains a character Windows does not allow in a name"
    if part.split(".", 1)[0].rstrip(" ").upper() in RESERVED:
        return f"{part!r} is a reserved Windows device name"
    return None


def lexical_problem(path):
    """Why `path` is not an absolute, local drive-letter path with safe components, or None."""
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return f"{text}: UNC, network-share and device paths are not supported"
    p = PureWindowsPath(text)
    if len(p.drive) != 2 or p.drive[1] != ":" or not p.drive[0].isalpha() or not p.root:
        return f"{text}: must be an absolute drive-letter path"
    for part in p.parts[1:]:
        problem = name_problem(part)
        if problem:
            return f"{text}: {problem}"
    return None


def _norm(path):
    """`path` lexically normalised (no `..`, no repeated separators) as a PureWindowsPath."""
    return PureWindowsPath(os.path.normpath(str(path)))


def _key(path):
    return [part.casefold() for part in PureWindowsPath(path).parts]


def within(scope, path):
    """True when `path` is `scope` or lies below it (both final paths; component-wise, case-insensitive)."""
    s, p = _key(scope), _key(path)
    return p[:len(s)] == s


# ---------------------------------------------------------------- handles

def _open(path, access, share, disposition, flags):
    handle = _k32.CreateFileW(str(path), access, share, None, disposition, flags, None)
    if handle in (None, INVALID_HANDLE_VALUE):
        err = ctypes.get_last_error()
        raise OSError(None, ctypes.FormatError(err).strip(), str(path), err)
    return handle


def close(handle):
    if handle not in (None, INVALID_HANDLE_VALUE):
        _k32.CloseHandle(handle)


def close_all(handles):
    for handle in reversed(list(handles)):
        close(handle)


def info(handle):
    data = BY_HANDLE_FILE_INFORMATION()
    if not _k32.GetFileInformationByHandle(handle, ctypes.byref(data)):
        err = ctypes.get_last_error()
        raise OSError(None, ctypes.FormatError(err).strip(), None, err)
    return data


def final_path(handle):
    """The normalised final path of an open handle as a drive-letter path, or None when it is not one (a UNC path,
    a volume without a drive letter)."""
    buf = ctypes.create_unicode_buffer(MAX_FINAL_PATH)
    n = _k32.GetFinalPathNameByHandleW(handle, buf, MAX_FINAL_PATH, 0)   # FILE_NAME_NORMALIZED | VOLUME_NAME_DOS
    if n == 0 or n >= MAX_FINAL_PATH:
        return None
    text = buf.value
    if text.startswith("\\\\?\\UNC\\") or not text.startswith("\\\\?\\"):
        return None
    text = text[4:]
    return text if len(text) >= 3 and text[1:3] == ":\\" else None


def _filesystem(handle):
    name = ctypes.create_unicode_buffer(261)
    if not _k32.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, name, 261):
        return None
    return name.value


def same_path(a, b):
    return a is not None and b is not None and _key(a) == _key(b)


# ---------------------------------------------------------------- pinning

def _pin_directory(path):
    return _open(path, FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                 OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)


def _component_problem(handle, lexical, directory=True):
    data = info(handle)
    if data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return f"{lexical} is a reparse point (a junction, symlink, mount point or other alias)"
    if directory and not data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
        return f"{lexical} is not a directory"
    if not directory and data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
        return f"{lexical} is a directory"
    actual = final_path(handle)
    if not same_path(actual, str(lexical)):
        return (f"{lexical} resolves to {actual or 'a non drive-letter path'}; only the canonical local spelling is "
                f"accepted (no SUBST drive, 8.3 short name or other alias)")
    return None


def pin_chain(path, filesystem=True):
    """Handles pinning every directory from the volume root down to `path` (inclusive), after proving that none is a
    reparse point and each is spelled canonically. The caller closes them with close_all(). Raises PathRefused."""
    lexical = _norm(path)
    problem = lexical_problem(lexical)
    if problem:
        raise PathRefused(problem)
    root = PureWindowsPath(lexical.anchor)
    if _k32.GetDriveTypeW(str(root)) not in LOCAL_DRIVES:
        raise PathRefused(f"{path}: {root} is not a local fixed or removable drive (network and mapped drives are "
                          f"not supported)")
    handles, current = [], root
    try:
        for part in (None,) + lexical.parts[1:]:
            current = current if part is None else current / part
            try:
                handle = _pin_directory(current)
            except OSError as exc:
                raise PathRefused(f"{current}: cannot be opened safely ({exc.strerror})") from None
            handles.append(handle)
            if part is None:
                if filesystem and _filesystem(handle) not in FILESYSTEMS:
                    raise PathRefused(f"{path}: the volume {root} is not NTFS")
                continue
            problem = _component_problem(handle, current)
            if problem:
                raise PathRefused(problem)
        return handles
    except BaseException:
        close_all(handles)
        raise


def existing_prefix(path):
    """(deepest existing ancestor-or-self, remaining parts) of a lexically normalised path."""
    lexical = _norm(path)
    current, rest = lexical, []
    while not os.path.lexists(current) and current != PureWindowsPath(current.anchor):
        rest.insert(0, current.name)
        current = current.parent
    return current, rest


def canonical(path):
    """The final path of `path`: its deepest existing directory proven and pinned, the rest kept lexically. Raises
    PathRefused for anything the chain rules refuse."""
    base, rest = existing_prefix(path)
    if os.path.lexists(base) and os.lstat(base).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise PathRefused(f"{base} is a reparse point (a junction, symlink, mount point or other alias)")
    if rest and not os.path.isdir(base):
        raise PathRefused(f"{base} is not a directory")
    if os.path.isdir(base) and not os.path.islink(base):
        handles = pin_chain(base)
        close_all(handles)
        return str(PureWindowsPath(base).joinpath(*rest))
    # the existing tail is a file (or a link to one): its directory is pinned and the file itself judged by handle
    handles = pin_chain(base.parent)
    try:
        handle = _open(base, FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT)
        try:
            problem = _component_problem(handle, base, directory=False)
        finally:
            close(handle)
        if problem:
            raise PathRefused(problem)
    except OSError as exc:
        raise PathRefused(f"{base}: cannot be opened safely ({exc.strerror})") from None
    finally:
        close_all(handles)
    return str(PureWindowsPath(base).joinpath(*rest))


def unsafe_reason(scopes, path, must_exist=False):
    """The Windows counterpart of paths.unsafe_reason: why `path` may not be touched, or None.

    Every scope and the target are checked lexically; every existing component of each, from the volume root down,
    must be a canonical, non-reparse directory on a local NTFS volume; the target's canonical path must lie inside a
    scope's canonical path. This is validation: the operations that then act on the path pin and re-verify it."""
    if not scopes:
        return f"{path}: no permitted filesystem scope is declared"
    if not PureWindowsPath(str(path)).is_absolute():
        return f"{path}: must be an absolute path"
    problem = lexical_problem(_norm(path))   # `..` is normalised lexically first, so it cannot walk out unseen
    if problem:
        return f"{path}: {problem}"
    roots = []
    for scope in scopes:
        try:
            roots.append(canonical(scope))
        except PathRefused as exc:
            return f"{path}: the permitted scope {scope} is not usable ({exc.reason})"
    try:
        target = canonical(path)
    except PathRefused as exc:
        return f"{path}: {exc.reason}"
    if not any(within(r, target) for r in roots):
        return f"{path}: resolves outside the permitted scopes {sorted(roots)}"
    if must_exist and not os.path.lexists(_norm(path)):
        return f"{path}: does not exist"
    return None


# ---------------------------------------------------------------- operations

def open_file_for_read(path, deny_writers=True):
    """(fd, size, pins) for an existing regular file, opened without following a reparse point, inside a pinned
    directory chain. With deny_writers, the file is opened with FILE_SHARE_READ only: it cannot be open for writing
    by anyone (a sharing violation) and cannot change, be renamed or be deleted while the fd is open. A file with
    more than one hard link is refused: it is an alias whose other names may lie anywhere on the volume. The caller
    closes the fd (os.close) and then the pins (close_all). Raises PathRefused or OSError."""
    lexical = _norm(path)
    pins = pin_chain(lexical.parent)
    try:
        share = FILE_SHARE_READ if deny_writers else FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        handle = _open(lexical, GENERIC_READ, share, OPEN_EXISTING,
                       FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN)
        try:
            problem = _component_problem(handle, lexical, directory=False)
            data = info(handle)
            if problem is None and data.nNumberOfLinks != 1:
                problem = f"{lexical} has {data.nNumberOfLinks} hard links; an aliased file is never trusted"
            if problem:
                raise PathRefused(problem)
            size = (data.nFileSizeHigh << 32) | data.nFileSizeLow
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close(handle)
            raise
        return fd, size, pins
    except BaseException:
        close_all(pins)
        raise


def read_file(path):
    """The bytes of a small foundation file (a lease), read through a handle that does not follow a reparse point.
    Concurrent readers, writers and deleters are not blocked. Raises PathRefused or OSError."""
    fd, _, pins = open_file_for_read(path, deny_writers=False)
    try:
        with os.fdopen(fd, "rb") as fh:
            return fh.read()
    finally:
        close_all(pins)


def create_new_file(path, data):
    """Create `path` with `data`, failing if anything (a file, a link, a junction) already has the name: CREATE_NEW
    with FILE_FLAG_OPEN_REPARSE_POINT never follows or replaces an existing entry. The parent chain is pinned and
    proven first. Raises FileExistsError, PathRefused or OSError."""
    lexical = _norm(path)
    pins = pin_chain(lexical.parent)
    try:
        try:
            handle = _open(lexical, GENERIC_WRITE | FILE_READ_ATTRIBUTES, FILE_SHARE_READ, CREATE_NEW,
                           FILE_FLAG_OPEN_REPARSE_POINT)
        except OSError as exc:
            if exc.winerror in (ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS):
                raise FileExistsError(exc.errno, exc.strerror, str(path)) from None
            raise
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    finally:
        close_all(pins)


def append_file(path, data):
    """Append to a foundation log, creating it if absent, without following a reparse point. Raises PathRefused or
    OSError."""
    lexical = _norm(path)
    pins = pin_chain(lexical.parent)
    try:
        handle = _open(lexical, FILE_APPEND_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_SHARE_READ, OPEN_ALWAYS,
                       FILE_FLAG_OPEN_REPARSE_POINT)
        try:
            problem = _component_problem(handle, lexical, directory=False)
            if problem:
                raise PathRefused(problem)
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_APPEND | os.O_BINARY)
        except BaseException:
            close(handle)
            raise
        with os.fdopen(fd, "ab") as fh:
            fh.write(data)
    finally:
        close_all(pins)


def delete_if(path, decide):
    """Delete exactly the file whose content `decide(bytes)` approves, by handle.

    The file is opened for read and delete without following a reparse point and with FILE_SHARE_READ only, so
    between the read and the delete nobody can write, rename or delete it; the delete is a disposition set on
    that same handle, never a second lookup of the path. Returns decide's verdict object. `decide` returns
    (delete: bool, verdict). Raises PathRefused or OSError (a sharing violation when someone else has it open for
    writing or deleting)."""
    lexical = _norm(path)
    pins = pin_chain(lexical.parent)
    try:
        handle = _open(lexical, GENERIC_READ | DELETE, FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT)
        try:
            problem = _component_problem(handle, lexical, directory=False)
            if problem:
                raise PathRefused(problem)
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)   # the fd now owns the handle
        except BaseException:
            close(handle)
            raise
        try:
            chunks = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            delete, verdict = decide(b"".join(chunks))
            if delete:
                flag = FILE_DISPOSITION_INFO(True)
                if not _k32.SetFileInformationByHandle(msvcrt.get_osfhandle(fd), FILE_DISPOSITION_INFO_CLASS,
                                                       ctypes.byref(flag), ctypes.sizeof(flag)):
                    err = ctypes.get_last_error()
                    raise OSError(None, ctypes.FormatError(err).strip(), str(path), err)
            return verdict
        finally:
            os.close(fd)
    finally:
        close_all(pins)


def make_directories(path, exact_last=True):
    """Create `path` and any missing parents inside a pinned, proven chain, then prove every directory it created or
    met: a canonical, non-reparse directory. With exact_last, the last component must also be spelled on disk
    exactly as asked (a letter-case variant that already exists is refused, never shared). Raises PathRefused or
    OSError."""
    lexical = _norm(path)
    base, rest = existing_prefix(lexical)
    pins = pin_chain(base)
    try:
        current = PureWindowsPath(base)
        for part in rest:
            current = current / part
            problem = name_problem(part)
            if problem:
                raise PathRefused(f"{current}: {problem}")
            try:
                os.mkdir(current)
            except FileExistsError:
                pass
            try:
                handle = _pin_directory(current)
            except OSError as exc:
                raise PathRefused(f"{current}: cannot be opened safely ({exc.strerror})") from None
            pins.append(handle)
            problem = _component_problem(handle, current)
            if problem:
                raise PathRefused(problem)
        if exact_last:
            actual = final_path(pins[-1])
            if actual is None or PureWindowsPath(actual).name != lexical.name:
                raise PathRefused(f"{lexical}: exists on disk as {actual}; a letter-case variant is a different "
                                  f"name on other hosts and is never shared")
    finally:
        close_all(pins)


def pin_executable(path):
    """(handle, final path, pins) pinning an executable for the duration of a launch: opened for read with
    FILE_SHARE_READ only, so it cannot be written, renamed or deleted until the handle is closed, and proven to be
    a canonical, non-reparse regular file in a proven local directory chain (any local file system). The caller
    closes the handle and then the pins. Raises PathRefused."""
    lexical = _norm(path)
    pins = pin_chain(lexical.parent, filesystem=False)
    try:
        try:
            handle = _open(lexical, GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT)
        except OSError as exc:
            if exc.winerror in UNAVAILABLE_ERRORS:
                raise Unavailable(f"{lexical}: cannot be opened for launch ({exc.strerror})") from None
            raise PathRefused(f"{lexical}: cannot be pinned for launch ({exc.strerror})") from None
        problem = _component_problem(handle, lexical, directory=False)
        if problem:
            close(handle)
            raise PathRefused(problem)
        return handle, final_path(handle), pins
    except BaseException:
        close_all(pins)
        raise


def file_attributes(path):
    """`st_file_attributes` of `path` itself (lstat: a reparse point is not followed)."""
    return os.lstat(path).st_file_attributes
