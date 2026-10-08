"""TEST_ONLY tool stand-ins that work on every host (alpha.24).

A test that needs a tool to misbehave on demand (print a wrong version, hang, emit a truncated PNG) writes a small
Python body and calls `write_tool(path, body)`:

* POSIX: exactly what the suites always did, an executable script `path` with a `#!<interpreter>` line;
* Windows: the alpha.23 boundary starts only `.exe` images, so `path.exe` is a copy of the C# stand-in compiled from
  tests/fixtures/windows-standin/StandIn.cs, and the body is written to its fixed script location beside it
  (`<stem>.standin.py`, plus `standin.interpreter`). The stand-in runs it with the test's interpreter, relaying the
  exact argument vector, stdout and stderr bytes and the exit code.

The compiler is the .NET Framework C# compiler that ships with Windows (`%WINDIR%\\Microsoft.NET\\Framework64\\
v4.0.30319\\csc.exe`). Nothing is downloaded, no binary is committed, and nothing in gpos/ depends on this. The stand-in
is built once per test process into a private temporary directory; without the compiler every caller fails with
WINDOWS_STANDIN_COMPILER_UNAVAILABLE rather than skipping. `IDENTITY` records the compiler (path, size, sha256, its
version banner), the source digest and the built image digest; the legacy compiler is not deterministic, so the image
digest differs between builds and the source digest is the reproducible identity.
"""

import atexit
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WINDOWS = sys.platform == "win32"
SOURCE = Path(__file__).resolve().parent / "fixtures" / "windows-standin" / "StandIn.cs"
COMPILER = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
UNAVAILABLE = "WINDOWS_STANDIN_COMPILER_UNAVAILABLE"
IDENTITY = {}
_BUILT = []


class StandInUnavailable(RuntimeError):
    pass


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def interpreter():
    """The interpreter running the tests, resolved (the boundary's own choice: process.interpreter_path)."""
    return str(Path(sys.executable).resolve())


def build():
    """The compiled stand-in image (Windows only), built once per process."""
    if _BUILT:
        return _BUILT[0]
    if not COMPILER.is_file():
        raise StandInUnavailable(f"{UNAVAILABLE}: {COMPILER} is not installed")
    out_dir = Path(tempfile.mkdtemp(prefix="gpos-standin-build-")).resolve()
    atexit.register(shutil.rmtree, out_dir, True)
    image = out_dir / "standin.exe"
    done = subprocess.run([str(COMPILER), "/nologo", "/target:exe", "/optimize+", "/warnaserror+", f"/out:{image}",
                           str(SOURCE)], capture_output=True, text=True, timeout=300)
    if done.returncode != 0 or not image.is_file():
        raise StandInUnavailable(f"{UNAVAILABLE}: the compiler failed ({done.returncode}): "
                                 f"{(done.stdout + done.stderr).strip()[:400]}")
    banner = subprocess.run([str(COMPILER), "/?"], capture_output=True, text=True, timeout=60).stdout.splitlines()
    IDENTITY.update(compiler=str(COMPILER), compiler_bytes=COMPILER.stat().st_size, compiler_sha256=_sha256(COMPILER),
                    compiler_banner=next((line.strip() for line in banner if "Compiler version" in line), "unknown"),
                    source_sha256=_sha256(SOURCE), image_sha256=_sha256(image))
    _BUILT.append(image)
    return image


def write_tool(path, body):
    """A TEST_ONLY executable at `path` (POSIX) or `path.exe` (Windows) that runs the Python `body`. Returns it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not WINDOWS:
        path.write_text(f"#!{interpreter()}\n{body}")
        path.chmod(0o755)
        return path
    exe = path.with_name(path.name + ".exe") if path.suffix.lower() != ".exe" else path
    shutil.copyfile(build(), exe)
    (exe.parent / (exe.stem + ".standin.py")).write_bytes(body.encode("utf-8"))
    (exe.parent / "standin.interpreter").write_bytes(interpreter().encode("utf-8"))
    return exe


def remove_tree(path):
    """Remove a test directory on every host. On Windows Git writes its object files read-only, and
    `shutil.rmtree(path, ignore_errors=True)` silently leaves such files (and their directories) behind; here a
    read-only entry is made writable and removed again. Best effort, like the call it replaces."""
    def writable_then_retry(function, entry, _):
        try:
            os.chmod(entry, 0o700)
            function(entry)
        except OSError:
            pass

    if os.path.lexists(path):
        shutil.rmtree(path, **{"onexc" if sys.version_info >= (3, 12) else "onerror": writable_then_retry})


def rewrite(tool, body):
    """Replace the body of a tool write_tool made, keeping the executable."""
    tool = Path(tool)
    if WINDOWS:
        (tool.parent / (tool.stem + ".standin.py")).write_bytes(body.encode("utf-8"))
    else:
        tool.write_text(f"#!{interpreter()}\n{body}")
        tool.chmod(0o755)
    return tool
