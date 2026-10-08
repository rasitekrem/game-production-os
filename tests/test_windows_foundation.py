#!/usr/bin/env python3
"""Windows foundation tests (alpha.23): the native process, liveness, file-system and lease mechanisms.

    python -X utf8 tests/test_windows_foundation.py

Windows only: on any other host every test is skipped with that reason (the platform-neutral integrity invariant is
in tests/test_tool_foundation.py V01 and runs everywhere). Standard library only. Every process this suite starts
through GPOS is a Python child of the interpreter running it, inside the foundation's own Job Object; the suite's
own helper processes (an outside handle holder, a crash-safety runner, a nested-job runner) are started with
subprocess and always reaped. Side effects: temporary directories, and one SUBST drive letter that W05 creates and
removes again.

Groups: W01 process mechanism · W02 executable pinning and refusals · W03 liveness · W04 environment · W05 NTFS
containment and races · W06 leases and sessions · W07 platform refusals · W08 UTF-8 and bytes.
"""

import ast
import hashlib
import json
import os
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WINDOWS = sys.platform == "win32"
if WINDOWS and not sys.flags.utf8_mode:
    sys.exit("WINDOWS_UTF8_MODE_REQUIRED: run this suite as `python -X utf8 tests/test_windows_foundation.py`")

from gpos.framework import load_framework  # noqa: E402
from gpos.tools import artifacts as art  # noqa: E402
from gpos.tools import diagnostics as tdg  # noqa: E402
from gpos.tools import execution as texec  # noqa: E402
from gpos.tools import leases as lease_mod  # noqa: E402
from gpos.tools import paths as tpaths  # noqa: E402
from gpos.tools import process as tproc  # noqa: E402
from gpos.tools.execution import ExecutionRequest, execute  # noqa: E402
from gpos.tools.model import Subject  # noqa: E402
from gpos.tools.registry import ToolRegistry  # noqa: E402
from gpos.tools.synthetic import SyntheticAdapter  # noqa: E402
from gpos.tools.synthetic import adapter as syn  # noqa: E402

if WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes
    from gpos.tools import paths_win32 as pw
    from gpos.tools import process_win32 as pwin
    K32 = ctypes.WinDLL("kernel32", use_last_error=True)   # the tests' own binding: any API a test needs
    K32.OpenProcess.restype = wintypes.HANDLE
    K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    K32.CreateFileW.restype = wintypes.HANDLE
    K32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    K32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    K32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    K32.GetCurrentProcess.restype = wintypes.HANDLE
    K32.CloseHandle.argtypes = [wintypes.HANDLE]
    K32.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]

FW = load_framework()
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
PY = tproc.interpreter_path() if WINDOWS else sys.executable
HELPER = str(syn.HELPER)

# A child program the tests run through the foundation. Modes write a marker the moment the child's own code runs,
# so a test can prove a child never ran.
CHILD = r'''
import os, subprocess, sys, time
mode, here = sys.argv[1], sys.argv[2]
open(os.path.join(here, "ran-" + mode), "w").write(str(os.getpid()))
PY = sys.executable
SLEEP = [PY, "-c", "import time; time.sleep(120)"]
if mode == "hello":
    print("hello"); print("err", file=sys.stderr); sys.exit(7)
elif mode == "flood":
    chunk = b"o" * 65536
    for _ in range(1024):
        sys.stdout.buffer.write(chunk); sys.stderr.buffer.write(chunk)
elif mode == "orphan":      # leaves a grandchild holding stdout, then exits
    p = subprocess.Popen(SLEEP, stdout=sys.stdout)
    open(os.path.join(here, "pid-1"), "w").write(str(p.pid))
    print("root exits")
elif mode == "tree":        # three levels, then sleeps past its deadline
    code = ("import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
            " open(sys.argv[1], 'w').write(str(p.pid)); time.sleep(120)")
    p = subprocess.Popen([PY, "-c", code, os.path.join(here, "pid-2")])
    open(os.path.join(here, "pid-1"), "w").write(str(p.pid))
    time.sleep(120)
elif mode == "breakaway":
    try:
        subprocess.Popen(SLEEP, creationflags=0x01000000)    # CREATE_BREAKAWAY_FROM_JOB
        print("broke away")
    except OSError as exc:
        print("breakaway refused", getattr(exc, "winerror", None))
    probe = ("import ctypes; from ctypes import wintypes; k = ctypes.WinDLL('kernel32', use_last_error=True); "
             "k.GetCurrentProcess.restype = wintypes.HANDLE; "
             "k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]; "
             "b = wintypes.BOOL(); ok = k.IsProcessInJob(k.GetCurrentProcess(), None, ctypes.byref(b)); "
             "print('grandchild in job', bool(ok) and bool(b.value))")
    p = subprocess.Popen([PY, "-c", probe], stdout=sys.stdout)
    p.wait()
elif mode == "env":
    print("\n".join(sorted(os.environ)))
elif mode == "cwd":
    print(os.getcwd())
elif mode == "handle":
    import msvcrt
    try:
        msvcrt.open_osfhandle(int(sys.argv[3]), os.O_RDONLY)
        os.read(0, 0)
        print("visible")
    except OSError:
        print("invisible")
elif mode == "dup":        # give an outside process a copy of our stdout, then exit
    import ctypes, msvcrt
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.DuplicateHandle.argtypes = [wintypes.HANDLE] * 3 + [ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                                          wintypes.BOOL, wintypes.DWORD]
    target = k.OpenProcess(0x0040, False, int(sys.argv[3]))        # PROCESS_DUP_HANDLE
    out = wintypes.HANDLE()
    ok = k.DuplicateHandle(k.GetCurrentProcess(), msvcrt.get_osfhandle(1), target, ctypes.byref(out), 0, False, 2)
    print("duplicated", bool(ok)); sys.stdout.flush()
elif mode == "sleep":
    time.sleep(float(sys.argv[3]))
'''


def windows_only(cls):
    return unittest.skipUnless(WINDOWS, "Windows foundation mechanisms; this host is not Windows")(cls)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-win-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.child = self.tmp / "child.py"
        self.child.write_text(CHILD, encoding="utf-8")
        self.addCleanup(pwin.HOOKS.clear)

    def spec(self, mode, *args, timeout=30.0, executable=None, env=None, cwd=None, capture_bytes=None):
        kw = {"capture_bytes": capture_bytes} if capture_bytes else {}
        return tproc.ToolProcessSpec(executable=executable or PY, argv=(str(self.child), mode, str(self.tmp)) + args,
                                     cwd=str(cwd or self.tmp), timeout=timeout, env=env or tproc.EnvironmentPolicy(),
                                     **kw)

    def run_child(self, mode, *args, **kw):
        return tproc.run_process(self.spec(mode, *args, **kw), [str(self.tmp)])

    def ran(self, mode):
        return (self.tmp / f"ran-{mode}").exists()

    def pid(self, n):
        path = self.tmp / f"pid-{n}"
        for _ in range(200):
            if path.exists() and path.read_text():
                return int(path.read_text())
            time.sleep(0.05)
        self.fail(f"{path} never appeared")

    def assertGone(self, pid):
        for _ in range(100):
            if not tproc.host_pid_alive(pid):
                return
            time.sleep(0.05)
        self.fail(f"process {pid} is still alive")

    def handle_count(self):
        n = wintypes.DWORD()
        K32.GetProcessHandleCount(K32.GetCurrentProcess(), ctypes.byref(n))
        return n.value


# ---------------------------------------------------------------- W01 process mechanism

@windows_only
class W01_ProcessMechanism(Case):
    def test_the_creation_flags_are_exactly_the_reviewed_ones(self):
        self.assertEqual(pwin.CREATION_FLAGS, 0x00080000 | 0x00000400 | 0x00000004 | 0x00000200 | 0x08000000)
        for forbidden in (0x00000008, 0x00000010, 0x01000000, 0x00000001, 0x00000002, 0x02000000, 0x00400000):
            self.assertFalse(pwin.CREATION_FLAGS & forbidden, hex(forbidden))   # DETACHED, NEW_CONSOLE, BREAKAWAY,…
        self.assertEqual(pwin.JOB_LIMITS, 0x2000 | 0x400)                        # KILL_ON_JOB_CLOSE | DIE_ON_UNHANDLED
        self.assertFalse(pwin.JOB_LIMITS & (0x800 | 0x1000))                     # never BREAKAWAY_OK / SILENT_BREAKAWAY_OK
        self.assertEqual((pwin.PROC_THREAD_ATTRIBUTE_HANDLE_LIST, pwin.PROC_THREAD_ATTRIBUTE_JOB_LIST), (0x20002, 0x2000D))

    def test_the_structure_layouts_are_the_documented_x64_ones(self):
        self.assertEqual(ctypes.sizeof(ctypes.c_void_p), 8, "these layouts are pinned for x64")
        sizes = {pwin.STARTUPINFOW: 104, pwin.STARTUPINFOEXW: 112, pwin.PROCESS_INFORMATION: 24,
                 pwin.JOBOBJECT_BASIC_LIMIT_INFORMATION: 64, pwin.IO_COUNTERS: 48,
                 pwin.JOBOBJECT_EXTENDED_LIMIT_INFORMATION: 144, pwin.JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: 48,
                 pw.BY_HANDLE_FILE_INFORMATION: 52}
        for struct, size in sizes.items():
            self.assertEqual(ctypes.sizeof(struct), size, struct.__name__)

    def test_a_child_runs_with_exit_code_output_and_proven_integrity(self):
        outcome = self.run_child("hello")
        self.assertEqual((outcome.exit_code, outcome.timed_out), (7, False))
        self.assertEqual(outcome.stdout.splitlines(), ["hello"])
        self.assertEqual(outcome.stderr.splitlines(), ["err"])
        self.assertEqual((outcome.tree_contained, outcome.capture_complete, outcome.descendants_terminated),
                         (True, True, 0))

    def test_the_child_is_in_its_job_and_suspended_when_creation_returns(self):
        seen = {}

        def after_create(info, job):
            member = wintypes.BOOL()
            K32.IsProcessInJob(info.hProcess, job, ctypes.byref(member))
            seen["member"], seen["ran"] = bool(member.value), self.ran("hello")

        pwin.HOOKS["after_create"] = after_create
        self.run_child("hello")
        self.assertEqual(seen, {"member": True, "ran": False})

    def test_the_readers_run_before_the_child_is_resumed(self):
        seen = {}
        pwin.HOOKS["before_resume"] = lambda info, readers: seen.update(
            alive=[r.is_alive() for r in readers], ran=self.ran("hello"))
        self.run_child("hello")
        self.assertEqual(seen, {"alive": [True, True], "ran": False})

    def test_a_deadline_spent_in_setup_creates_nothing(self):
        pwin.HOOKS["setup_done"] = lambda: time.sleep(0.4)
        before = self.handle_count()
        outcome = self.run_child("hello", timeout=0.2)
        self.assertTrue(outcome.timed_out)
        self.assertIsNone(outcome.exit_code)
        time.sleep(0.2)
        self.assertFalse(self.ran("hello"))
        self.assertLessEqual(self.handle_count(), before + 2)

    def test_a_creation_overrun_is_terminated_before_it_runs(self):
        pwin.HOOKS["after_create"] = lambda info, job: time.sleep(0.4)   # stands in for a slow native CreateProcessW
        outcome = self.run_child("hello", timeout=0.2)
        self.assertTrue(outcome.timed_out and outcome.terminated)
        self.assertIsNone(outcome.exit_code)
        time.sleep(0.2)
        self.assertFalse(self.ran("hello"))

    def test_an_unproven_membership_is_refused_and_the_child_never_runs(self):
        pwin.HOOKS["deny_membership"] = lambda: True
        before = self.handle_count()
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            self.run_child("hello")
        self.assertEqual(cm.exception.code, "EXECUTION_FAILED")
        self.assertIn("before any of its code ran", str(cm.exception))
        time.sleep(0.2)
        self.assertFalse(self.ran("hello"))
        self.assertLessEqual(self.handle_count(), before + 2)

    def test_an_image_mismatch_is_refused_and_the_child_never_runs(self):
        pwin.HOOKS["image"] = lambda info: os.path.join(os.environ["SystemRoot"], "System32", "notepad.exe")
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            self.run_child("hello")
        self.assertEqual(cm.exception.code, "EXECUTION_FAILED")
        time.sleep(0.2)
        self.assertFalse(self.ran("hello"))

    def test_heavy_interleaved_output_never_deadlocks(self):
        started = time.monotonic()
        outcome = self.run_child("flood", timeout=120.0)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual((outcome.stdout_bytes, outcome.stderr_bytes), (64 * 1024 * 1024, 64 * 1024 * 1024))
        self.assertTrue(outcome.stdout_truncated and outcome.stderr_truncated)
        self.assertTrue(outcome.capture_complete)
        self.assertLess(time.monotonic() - started, 110)

    def test_a_descendant_left_holding_the_output_is_terminated_in_bounded_time(self):
        started = time.monotonic()
        outcome = self.run_child("orphan")
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(outcome.descendants_terminated, 1)
        self.assertTrue(outcome.tree_contained and outcome.capture_complete)
        self.assertGone(self.pid(1))

    def test_a_timeout_terminates_the_whole_three_level_tree(self):
        outcome = self.run_child("tree", timeout=3.0)
        self.assertTrue(outcome.timed_out and outcome.terminated and outcome.tree_contained)
        self.assertIsNone(outcome.exit_code)
        for n in (1, 2):
            self.assertGone(self.pid(n))

    def test_no_descendant_can_break_away_from_the_job(self):
        outcome = self.run_child("breakaway")
        self.assertIn("breakaway refused 5", outcome.stdout)          # ERROR_ACCESS_DENIED
        self.assertIn("grandchild in job True", outcome.stdout)
        self.assertNotIn("broke away", outcome.stdout)

    def test_a_job_that_does_not_empty_is_not_reported_contained(self):
        pwin.HOOKS["active_processes"] = lambda job: 1
        with mock.patch.object(pwin, "CLEANUP_SECONDS", 0.3):
            outcome = self.run_child("hello")
        self.assertFalse(outcome.tree_contained)
        self.assertIsNone(outcome.exit_code)

    def test_a_pipe_held_outside_the_job_makes_the_capture_incomplete_in_bounded_time(self):
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        started = time.monotonic()
        with mock.patch.object(pwin, "CAPTURE_DRAIN_SECONDS", 0.5):
            outcome = self.run_child("dup", str(holder.pid))
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue(outcome.tree_contained)
        self.assertFalse(outcome.capture_complete)
        self.assertIsNone(outcome.exit_code)

    def test_an_inheritable_handle_of_the_parent_is_invisible_to_the_child(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        handle = msvcrt.get_osfhandle(r)
        os.set_handle_inheritable(handle, True)
        outcome = self.run_child("handle", str(handle))
        self.assertEqual(outcome.stdout.strip(), "invisible")

    def test_the_working_directory_is_the_validated_one(self):
        sub = self.tmp / "work dir"
        sub.mkdir()
        outcome = self.run_child("cwd", cwd=sub)
        self.assertEqual(Path(outcome.stdout.strip()), sub)

    def test_a_gui_subsystem_child_is_contained_and_timed_out(self):
        gui = Path(PY).with_name("pythonw.exe")
        if not gui.is_file():
            self.skipTest("no pythonw.exe beside this interpreter")
        outcome = tproc.run_process(self.spec("sleep", "30", executable=str(gui), timeout=1.0), [str(self.tmp)])
        self.assertTrue(outcome.timed_out and outcome.tree_contained)
        self.assertGone(int((self.tmp / "ran-sleep").read_text()))

    def test_a_crashed_gpos_process_takes_its_tool_tree_with_it(self):
        runner = (f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
                  "from gpos.tools import process as p\n"
                  f"s = p.ToolProcessSpec(executable={PY!r}, argv=({str(self.child)!r}, 'tree', {str(self.tmp)!r}), "
                  f"cwd={str(self.tmp)!r}, timeout=120)\n"
                  f"p.run_process(s, [{str(self.tmp)!r}])\n")
        gpos = subprocess.Popen([sys.executable, "-X", "utf8", "-c", runner])
        self.addCleanup(gpos.wait)
        grandchild = self.pid(2)
        gpos.kill()                                     # the GPOS process dies; its job handle closes with it
        gpos.wait()
        self.assertGone(self.pid(1))
        self.assertGone(grandchild)

    def test_inside_a_parent_job_it_nests_and_under_ui_limits_it_creates_nothing(self):
        runner = (f"import ctypes, sys; sys.path.insert(0, {str(ROOT)!r})\n"
                  "from ctypes import wintypes\n"
                  "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
                  "k.CreateJobObjectW.restype = wintypes.HANDLE\n"
                  "k.GetCurrentProcess.restype = wintypes.HANDLE\n"
                  "k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]\n"
                  "k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]\n"
                  "job = k.CreateJobObjectW(None, None)\n"
                  "if sys.argv[1] == 'ui':\n"
                  "    ui = wintypes.DWORD(1)\n"                               # JOB_OBJECT_UILIMIT_HANDLES
                  "    assert k.SetInformationJobObject(job, 4, ctypes.byref(ui), 4)\n"
                  "assert k.AssignProcessToJobObject(job, k.GetCurrentProcess())\n"
                  "from gpos.tools import process as p\n"
                  f"s = p.ToolProcessSpec(executable={PY!r}, argv=({str(self.child)!r}, 'hello', {str(self.tmp)!r}), "
                  f"cwd={str(self.tmp)!r})\n"
                  "try:\n"
                  "    o = p.run_process(s, [s.cwd]); print('RESULT', o.exit_code, o.tree_contained)\n"
                  "except p.ProcessSpecError as e:\n"
                  "    print('REFUSED', e.code)\n")
        nested = subprocess.run([sys.executable, "-X", "utf8", "-c", runner, "plain"], capture_output=True, text=True,
                                timeout=60)
        self.assertIn("RESULT 7 True", nested.stdout, nested.stderr)
        (self.tmp / "ran-hello").unlink()
        # Microsoft documents that jobs do not nest when one sets UI limits. Whatever Windows then does, GPOS may only
        # either run a child it proved to be in its own job (and contained), or refuse before the child ran.
        limited = subprocess.run([sys.executable, "-X", "utf8", "-c", runner, "ui"], capture_output=True, text=True,
                                 timeout=60)
        if "REFUSED" in limited.stdout:
            self.assertFalse(self.ran("hello"))
        else:
            self.assertIn("RESULT 7 True", limited.stdout, limited.stderr)   # observed on Windows 11 26200


# ---------------------------------------------------------------- W02 executable pinning and refusals

@windows_only
class W02_ExecutablesAndRefusals(Case):
    def fake(self, name, content=b"MZ not really"):
        path = self.tmp / name
        path.write_bytes(content)
        return path

    def test_only_a_dot_exe_image_is_ever_started(self):
        for name in ("tool.bat", "tool.cmd", "tool.ps1", "tool.vbs", "tool.js", "tool.com", "tool", "tool.exe.bat"):
            with self.subTest(name=name):
                with self.assertRaises(tproc.ProcessSpecError) as cm:
                    tproc.validate_spec(self.spec("hello", executable=str(self.fake(name))), [str(self.tmp)])
                self.assertEqual(cm.exception.code, "UNSAFE_PROCESS_SPEC")

    def test_windows_shells_and_script_hosts_are_refused_by_name(self):
        for name in ("bash.exe", "wsl.exe", "cscript.exe", "wscript.exe", "mshta.exe", "cmd.exe", "powershell.exe",
                     "pwsh.exe", "CMD.EXE"):
            with self.subTest(name=name):
                with self.assertRaises(tproc.ProcessSpecError) as cm:
                    tproc.validate_spec(self.spec("hello", executable=str(self.fake(name))), [str(self.tmp)])
                self.assertEqual(cm.exception.code, "UNSAFE_PROCESS_SPEC")

    def test_an_app_execution_alias_is_refused(self):
        alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "python.exe"
        if not os.path.lexists(alias):
            self.skipTest("no App Execution Alias for python on this host")
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            tproc.validate_spec(self.spec("hello", executable=str(alias)), [str(self.tmp)])
        self.assertEqual(cm.exception.code, "UNSAFE_PROCESS_SPEC")

    def test_a_linked_executable_is_refused(self):
        link = self.tmp / "linked.exe"
        os.symlink(PY, link)
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            tproc.run_process(self.spec("hello", executable=str(link)), [str(self.tmp)])
        self.assertEqual(cm.exception.code, "UNSAFE_PROCESS_SPEC")

    def test_a_non_image_exe_is_a_tool_availability_problem(self):
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            tproc.run_process(self.spec("hello", executable=str(self.fake("broken.exe", b"not a PE image"))),
                              [str(self.tmp)])
        self.assertEqual(cm.exception.code, "TOOL_NOT_FOUND")

    def test_an_executable_held_exclusively_is_a_tool_availability_problem(self):
        """The Windows counterpart of POSIX losing the execute bit (test_tool_foundation N08)."""
        exe = self.fake("held.exe", Path(PY).read_bytes())
        handle = K32.CreateFileW(str(exe), 0x80000000, 0, None, 3, 0, None)     # GENERIC_READ, no sharing at all
        self.addCleanup(K32.CloseHandle, handle)
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            tproc.run_process(self.spec("hello", executable=str(exe)), [str(self.tmp)])
        self.assertEqual(cm.exception.code, "TOOL_NOT_FOUND")

    def test_the_executable_cannot_be_renamed_or_replaced_while_it_is_pinned(self):
        """T-P5: the pin (FILE_SHARE_READ only) is held through creation, and the loader still starts the image."""
        exe = self.tmp / "py" / "python.exe"
        exe.parent.mkdir()
        for name in os.listdir(Path(PY).parent):
            if name.lower().endswith((".exe", ".dll")):
                shutil.copy2(Path(PY).parent / name, exe.parent / name)
        attempts = {}

        def before_create():
            for label, op in (("rename", lambda: os.rename(exe, exe.with_name("moved.exe"))),
                              ("delete", lambda: os.remove(exe)),
                              ("write", lambda: open(exe, "r+b").close())):
                try:
                    op()
                    attempts[label] = "succeeded"
                except OSError:
                    attempts[label] = "refused"

        pwin.HOOKS["before_create"] = before_create
        outcome = tproc.run_process(self.spec("hello", executable=str(exe)), [str(self.tmp)])
        self.assertEqual(attempts, {"rename": "refused", "delete": "refused", "write": "refused"})
        self.assertTrue(outcome.tree_contained)          # the pinned image was created, proven and ran
        os.rename(exe, exe.with_name("moved.exe"))       # and the pin is gone afterwards

    def test_the_working_directory_cannot_be_renamed_during_the_launch(self):
        sub = self.tmp / "cwd"
        sub.mkdir()
        attempts = []

        def before_create():
            try:
                os.rename(sub, self.tmp / "cwd-moved")
                attempts.append("succeeded")
            except OSError:
                attempts.append("refused")

        pwin.HOOKS["before_create"] = before_create
        self.run_child("hello", cwd=sub)
        self.assertEqual(attempts, ["refused"])

    def test_a_command_line_over_the_windows_limit_is_refused(self):
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            self.run_child("hello", "x" * 33000)
        self.assertEqual(cm.exception.code, "UNSAFE_PROCESS_SPEC")


# ---------------------------------------------------------------- W03 liveness

@windows_only
class W03_Liveness(Case):
    def test_a_live_process_is_reported_alive_and_left_untouched(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        for _ in range(5):
            self.assertTrue(lease_mod.pid_alive(child.pid))
        time.sleep(0.3)
        self.assertIsNone(child.poll())                 # still running: liveness signalled nothing

    def test_an_ended_process_and_a_bogus_id_are_not_alive(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(lease_mod.pid_alive(child.pid))
        for bogus in (0, -1, 2 ** 40, "x", None):
            self.assertFalse(lease_mod.pid_alive(bogus))

    def test_an_inaccessible_process_counts_as_alive(self):
        self.assertTrue(lease_mod.pid_alive(4))         # System: exists, access denied

    def test_liveness_never_signals_on_windows(self):
        source = (ROOT / "gpos" / "tools" / "leases.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "pid_alive")
        guard = fn.body[0]
        self.assertEqual(ast.unparse(guard.test), "sys.platform == 'win32'")
        self.assertNotIn("kill", ast.unparse(guard))
        backend = ast.unparse(ast.parse((ROOT / "gpos" / "tools" / "process_win32.py").read_text(encoding="utf-8")))
        for name in ("os.kill", "GenerateConsoleCtrlEvent", "CTRL_C_EVENT", "CTRL_BREAK_EVENT", "signal."):
            self.assertNotIn(name, backend)


# ---------------------------------------------------------------- W04 environment

@windows_only
class W04_Environment(Case):
    def test_the_child_sees_only_the_allowlist_and_the_overrides(self):
        with mock.patch.dict(os.environ, {"GPOS_TEST_SECRET_TOKEN": "s3cret", "HTTPS_PROXY": "http://p:1"}):
            outcome = self.run_child("env", env=tproc.EnvironmentPolicy(overrides=(("GPOS_SET", "1"),)))
        names = {n.upper() for n in outcome.stdout.split()}
        self.assertNotIn("GPOS_TEST_SECRET_TOKEN", names)
        self.assertNotIn("HTTPS_PROXY", names)
        self.assertIn("GPOS_SET", names)
        allowed = {n.upper() for n in tproc.SAFE_ENV_DEFAULTS + tproc.WINDOWS_ENV_DEFAULTS} | {"GPOS_SET"}
        self.assertLessEqual(names - {"=::", "PYTHONUTF8"}, allowed)   # =:: is the console's own per-drive variable
        for name in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "USERPROFILE"):
            self.assertIn(name, names)

    def test_names_are_case_insensitive_and_set_once(self):
        env = tproc.EnvironmentPolicy(overrides=(("path", "C:\\only"),)).build({"Path": "C:\\a", "TEMP": "t"})
        self.assertEqual([k for k in env if k.upper() == "PATH"], ["path"])
        self.assertEqual(env["path"], "C:\\only")
        self.assertEqual(env["TEMP"], "t")

    def test_a_narrowed_policy_is_not_widened(self):
        self.assertEqual(tproc.EnvironmentPolicy(inherit=("PATH",)).build({"PATH": "p", "USERPROFILE": "u"}),
                         {"PATH": "p"})
        meta = tproc.EnvironmentPolicy().metadata()
        self.assertLessEqual(set(tproc.WINDOWS_ENV_DEFAULTS), set(meta["inherited_names"]))

    def test_ambiguous_or_malformed_names_are_refused(self):
        for overrides in ((("PATH", "a"), ("Path", "b")), (("A=B", "x"),), (("", "x"),), (("X", "a\x00b"),)):
            with self.subTest(overrides=overrides):
                with self.assertRaises(tproc.ProcessSpecError):
                    tproc.validate_spec(self.spec("hello", env=tproc.EnvironmentPolicy(overrides=overrides)),
                                        [str(self.tmp)])


# ---------------------------------------------------------------- W05 NTFS containment and races

def junction(link, target):
    import _winapi   # the tests' own way to make a junction without privilege; production code never imports it
    _winapi.CreateJunction(str(target), str(link))


@windows_only
class W05_NtfsContainment(Case):
    def scope(self):
        scope = self.tmp / "scope"
        (scope / "inside").mkdir(parents=True)
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"outside\n")
        return scope, outside

    def test_lexical_windows_hazards_are_refused(self):
        scope, _ = self.scope()
        for name in ("a.txt:stream", "a.txt::$DATA", "CON", "nul.txt", "COM1", "LPT9.log", "CONIN$", "trailing.",
                     "trailing ", "bad|name", "q?"):
            with self.subTest(name=name):
                self.assertIsNotNone(tpaths.unsafe_reason([str(scope)], str(scope / name)))
        for path in (r"\\server\share\x", r"\\?\C:\x", r"\\.\PhysicalDrive0", "C:relative"):
            with self.subTest(path=path):
                self.assertIsNotNone(tpaths.unsafe_reason([str(scope)], path))
        self.assertIsNone(tpaths.unsafe_reason([str(scope)], str(scope / "inside" / "fine name.txt")))
        self.assertIsNone(tpaths.unsafe_reason([str(scope)], str(scope / "inside" / ".." / "inside" / "x")))

    def test_a_junction_below_the_scope_is_refused(self):
        scope, outside = self.scope()
        junction(scope / "j", outside)
        self.assertIn("reparse point", tpaths.unsafe_reason([str(scope)], str(scope / "j" / "secret.txt")))

    def test_a_junction_as_the_scope_root_or_an_ancestor_is_refused(self):
        _, outside = self.scope()
        junction(self.tmp / "root-link", outside)
        self.assertIn("not usable", tpaths.unsafe_reason([str(self.tmp / "root-link")],
                                                         str(self.tmp / "root-link" / "secret.txt")))
        (outside / "deeper").mkdir()
        self.assertIsNotNone(tpaths.unsafe_reason([str(self.tmp / "root-link" / "deeper")],
                                                  str(self.tmp / "root-link" / "deeper" / "x")))

    def test_a_pinned_chain_cannot_be_renamed_and_is_released_afterwards(self):
        scope, _ = self.scope()
        pins = pw.pin_chain(scope / "inside")
        try:
            with self.assertRaises(OSError):
                os.rename(scope, self.tmp / "scope-moved")
            with self.assertRaises(OSError):
                os.rename(scope / "inside", scope / "inside-moved")
        finally:
            pw.close_all(pins)
        os.rename(scope / "inside", scope / "inside-moved")

    def collect(self, scope, name):
        spec = art.ArtifactSpec("a", "TEXT", str(scope / name))
        return art.collect([spec], [str(scope)], scope, "req-x", "OFFLINE_ANALYSIS")

    def test_a_junction_planted_between_validation_and_hashing_is_refused(self):
        scope, outside = self.scope()
        (scope / "inside" / "secret.txt").write_bytes(b"inside\n")
        real = tpaths.unsafe_reason

        def validate_then_swap(scopes, path, must_exist=False):
            reason = real(scopes, path, must_exist)
            os.rename(scope / "inside", scope / "inside-old")
            junction(scope / "inside", outside)
            return reason

        with mock.patch.object(art.tp, "unsafe_reason", validate_then_swap):
            artifacts, problems = self.collect(scope, "inside/secret.txt")
        self.assertEqual(artifacts, [])
        self.assertEqual([p[0] for p in problems], ["ARTIFACT_HASH_FAILED"])
        self.assertIn("reparse point", problems[0][2])

    def test_a_linked_artifact_and_a_hard_linked_alias_are_refused(self):
        scope, outside = self.scope()
        os.symlink(outside / "secret.txt", scope / "inside" / "link.txt")
        os.link(outside / "secret.txt", scope / "inside" / "hard.txt")
        for name in ("inside/link.txt", "inside/hard.txt"):
            with self.subTest(name=name):
                artifacts, problems = self.collect(scope, name)
                self.assertEqual(artifacts, [])
                self.assertTrue(problems)

    def test_an_artifact_still_open_for_writing_is_never_hashed(self):
        scope, _ = self.scope()
        path = scope / "inside" / "busy.txt"
        with open(path, "wb") as writer:
            writer.write(b"half")
            artifacts, problems = self.collect(scope, "inside/busy.txt")
        self.assertEqual(artifacts, [])
        self.assertEqual([p[0] for p in problems], ["ARTIFACT_HASH_FAILED"])
        artifacts, problems = self.collect(scope, "inside/busy.txt")
        self.assertEqual((problems, artifacts[0].sha256), ([], hashlib.sha256(b"half").hexdigest()))

    def test_an_artifact_cannot_change_move_or_vanish_while_it_is_hashed(self):
        scope, _ = self.scope()
        path = scope / "inside" / "a.txt"
        path.write_bytes(b"x" * 1024)
        fd, size, pins = pw.open_file_for_read(path)
        try:
            for op in (lambda: os.remove(path), lambda: os.rename(path, path.with_name("b.txt")),
                       lambda: open(path, "ab").close()):
                with self.assertRaises(OSError):
                    op()
        finally:
            os.close(fd)
            pw.close_all(pins)
        self.assertEqual(size, 1024)

    def test_a_junction_planted_while_the_workspace_is_created_is_refused(self):
        p = self.tmp / "p"
        shutil.copytree(FIXTURE, p)
        outside = self.tmp / "outside"
        outside.mkdir()
        real_mkdir = os.mkdir

        def plant(path, *a, **kw):
            if Path(path).name == "tool-output":
                junction(path, outside)
                return
            return real_mkdir(path, *a, **kw)

        r = ToolRegistry(FW, allow_test_only=True)
        r.register(SyntheticAdapter())
        with mock.patch.object(os, "mkdir", plant):
            result = execute(r, ExecutionRequest(adapter_id="synthetic", capability_id=syn.TRANSFORM,
                                                 subject=Subject("TASK", "T"), project_root=str(p),
                                                 allow_mutation=True, inputs={"text": "x"}))
        self.assertIn("WORKSPACE_NOT_USABLE", {d.code for d in result.diagnostics})
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_subst_drive_alias_is_refused(self):
        scope, _ = self.scope()
        free = None
        for letter in reversed(string.ascii_uppercase[3:]):   # claim a letter by succeeding; parallel runs may race
            if os.path.exists(f"{letter}:\\"):
                continue
            if subprocess.run(["subst", f"{letter}:", str(scope)], capture_output=True).returncode == 0:
                free = letter
                break
        if free is None:
            self.skipTest("no free drive letter for a SUBST alias")
        self.addCleanup(subprocess.run, ["subst", f"{free}:", "/D"], capture_output=True)
        reason = tpaths.unsafe_reason([f"{free}:\\inside"], f"{free}:\\inside\\x.txt")
        self.assertIsNotNone(reason)
        self.assertIn("canonical", reason)

    def test_an_8_3_short_spelling_is_refused(self):
        long = self.tmp / "a rather long directory name"
        long.mkdir()
        buf = ctypes.create_unicode_buffer(1024)
        K32.GetShortPathNameW(str(long), buf, 1024)
        if not buf.value or Path(buf.value).name == long.name:
            self.skipTest("8.3 short names are disabled on this volume")
        self.assertIsNotNone(tpaths.unsafe_reason([buf.value], buf.value + "\\x.txt"))
        self.assertIsNone(tpaths.unsafe_reason([str(long)], str(long / "x.txt")))

    def test_network_drives_non_ntfs_volumes_and_unc_final_paths_are_refused(self):
        """The real cases (a mapped share, a ReFS or FAT volume) are NOT_RUN on this workstation; the decisions are
        exercised through their one input each."""
        scope, _ = self.scope()
        for patch in (mock.patch.object(pw, "LOCAL_DRIVES", ()),           # GetDriveTypeW: not fixed/removable
                      mock.patch.object(pw, "_filesystem", lambda handle: "ReFS"),
                      mock.patch.object(pw, "final_path", lambda handle: None)):    # a \\?\UNC\ final path
            with self.subTest(patch=patch.attribute):
                with patch:
                    self.assertIsNotNone(tpaths.unsafe_reason([str(scope)], str(scope / "inside" / "x")))

    def test_a_case_variant_of_the_project_is_the_same_lease(self):
        p = self.tmp / "Proj"
        p.mkdir()
        variant = Path(str(p).upper())
        cap = SyntheticAdapter().descriptor.capability(syn.STATEFUL_WRITE)
        request = lambda root: ExecutionRequest(adapter_id="synthetic", capability_id=cap.id,
                                                subject=Subject("TASK", "T"), project_root=str(root))
        self.assertEqual(texec._resource(request(p), cap, p.resolve()),
                         texec._resource(request(variant), cap, variant.resolve()))


# ---------------------------------------------------------------- W06 leases and sessions

SID = "0123456789abcdef0123456789abcdef"


@windows_only
class W06_LeasesAndSessions(Case):
    def root(self):
        root = self.tmp / "proj"
        root.mkdir()
        return root

    def test_exactly_one_of_two_concurrent_acquirers_wins(self):
        root, wins, errors = self.root(), [], []

        def take(owner):
            try:
                wins.append(lease_mod.acquire(root, "a", "r", owner, "2026-01-01T00:00:00Z"))
            except lease_mod.LeaseHeld as exc:
                errors.append(exc.code)

        threads = [threading.Thread(target=take, args=(f"o{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual((len(wins), errors), (1, ["LEASE_CONFLICT"]))

    def test_a_link_planted_at_the_lease_name_is_never_followed(self):
        root = self.root()
        outside = self.tmp / "outside.json"
        outside.write_bytes(b"untouched")
        path = lease_mod.lease_path(root, "a", "r")
        path.parent.mkdir(parents=True)
        os.symlink(outside, path)
        with self.assertRaises(lease_mod.LeaseHeld) as cm:
            lease_mod.acquire(root, "a", "r", "o", "2026-01-01T00:00:00Z")
        self.assertEqual(cm.exception.code, "LEASE_INVALID")
        self.assertEqual(outside.read_bytes(), b"untouched")

    def test_a_sharing_violation_keeps_the_lease_and_is_a_structured_failure(self):
        root = self.root()
        lease = lease_mod.acquire(root, "a", "r", "o", "2026-01-01T00:00:00Z")
        with open(lease.path, "rb"):                         # Python opens without FILE_SHARE_DELETE
            self.assertFalse(lease_mod.release(root, lease))
        self.assertTrue(Path(lease.path).exists())
        self.assertTrue(lease_mod.release(root, lease))

    def test_release_deletes_exactly_the_verified_lease(self):
        root = self.root()
        lease = lease_mod.acquire(root, "a", "r", "o", "2026-01-01T00:00:00Z")
        attacker = self.tmp / "attacker.json"
        attacker.write_bytes(b'{"owner_id": "o"}')
        attempts = []
        real = pw.delete_if

        def racing(path, decide):
            def wrapped(content):   # between the verifying read and the delete, try to swap the lease out
                for label, op in (("rename", lambda: os.rename(path, Path(path).with_name("moved.json"))),
                                  ("replace", lambda: os.replace(attacker, path))):
                    try:
                        op()
                        attempts.append(f"{label} succeeded")
                    except OSError:
                        attempts.append(f"{label} refused")
                return decide(content)
            return real(path, wrapped)

        with mock.patch.object(pw, "delete_if", racing):
            self.assertTrue(lease_mod.release(root, lease))
        self.assertEqual(attempts, ["rename refused", "replace refused"])
        self.assertFalse(Path(lease.path).with_name("moved.json").exists())
        self.assertFalse(Path(lease.path).exists())
        self.assertTrue(attacker.exists())

    def test_another_owner_is_never_released(self):
        root = self.root()
        lease = lease_mod.acquire(root, "a", "r", "o", "2026-01-01T00:00:00Z")
        forged = lease_mod.Lease(**{**lease.__dict__, "token": "f" * 32})
        self.assertFalse(lease_mod.release(root, forged))
        self.assertTrue(Path(lease.path).exists())

    def test_session_verify_release_and_break_keep_their_semantics(self):
        root = self.root()
        lease_mod.acquire(root, "a", "r", "AGENT:x", "2026-01-01T00:00:00Z", scope=lease_mod.SESSION,
                          session={"session_id": SID})
        record = lease_mod.verify_session(root, "a", "r", SID, "AGENT:x")
        with self.assertRaises(lease_mod.LeaseHeld) as cm:
            lease_mod.release_session(root, "a", "r", SID, "AGENT:y")
        self.assertEqual(cm.exception.code, "LIVE_SESSION_MISMATCH")
        path = lease_mod.lease_path(root, "a", "r")
        with open(path, "rb"):
            with self.assertRaises(lease_mod.LeaseHeld) as cm:
                lease_mod.release_session(root, "a", "r", SID, "AGENT:x")
            self.assertEqual(cm.exception.code, "LEASE_RELEASE_FAILED")
        self.assertEqual(lease_mod.release_session(root, "a", "r", SID, "AGENT:x"), record)
        self.assertFalse(path.exists())

        held = lease_mod.acquire(root, "a", "r", "AGENT:x", "2026-01-01T00:00:00Z", scope=lease_mod.SESSION,
                                 session={"session_id": SID})
        with self.assertRaises(lease_mod.LeaseHeld) as cm:
            lease_mod.break_lease(root, "a", "r", "HUMAN:h", "recovery", "2026-01-02T00:00:00Z", expected_token="0" * 32)
        self.assertEqual(cm.exception.code, "LEASE_CONFLICT")
        entry = lease_mod.break_lease(root, "a", "r", "HUMAN:h", "recovery", "2026-01-02T00:00:00Z",
                                      expected_token=held.token)
        self.assertEqual(entry["previous_holder"]["token"], held.token)
        self.assertFalse(path.exists())
        log = tpaths.runtime_dir(root, "leases", "broken.log").read_bytes()
        self.assertEqual(json.loads(log.decode("utf-8").splitlines()[-1])["broken_by"], "HUMAN:h")
        self.assertNotIn(b"\r\n", log)

    def test_a_dead_holder_looks_stale_and_a_live_one_does_not(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertTrue(lease_mod.looks_stale({"pid": child.pid}, lease_mod.pid_alive))
        self.assertFalse(lease_mod.looks_stale({"pid": os.getpid()}, lease_mod.pid_alive))

    def test_no_acl_privacy_is_claimed_the_lease_inherits_the_project_acl(self):
        root = self.root()
        lease = lease_mod.acquire(root, "a", "r", "o", "2026-01-01T00:00:00Z")
        listing = subprocess.run(["icacls", lease.path], capture_output=True, text=True, check=True).stdout
        aces = [line for line in listing.splitlines()[:-2] if line.strip()]
        self.assertTrue(aces)
        self.assertTrue(all("(I)" in line for line in aces), listing)   # every entry inherited, none set by GPOS


# ---------------------------------------------------------------- W07 platform refusals

@windows_only
class W07_PlatformRefusals(Case):
    def test_no_detached_process_exists_on_windows(self):
        spec = tproc.DetachedProcessSpec(executable=PY, argv=("-V",), cwd=str(self.tmp))
        with self.assertRaises(tproc.ProcessSpecError) as cm:
            tproc.spawn_detached(spec, [str(self.tmp)])
        self.assertEqual(cm.exception.code, "PLATFORM_UNSUPPORTED")
        self.assertEqual(tdg.CODES["PLATFORM_UNSUPPORTED"][0], tdg.INCOMPATIBLE)

    def test_no_host_location_exists_on_windows(self):
        with self.assertRaises(tpaths.HostLocationUnsupported):
            tpaths.account_home()
        from gpos.tools.registry import default_registry
        p = self.tmp / "p"
        shutil.copytree(FIXTURE, p)
        result = execute(default_registry(FW), ExecutionRequest(
            adapter_id="player", capability_id="player.install-capture-helper", subject=Subject("TASK", "T"),
            project_root=str(p), allow_mutation=True))
        self.assertEqual(result.status, tdg.INCOMPATIBLE)
        self.assertIn("PLATFORM_UNSUPPORTED", {d.code for d in result.diagnostics})

    def test_macos_only_adapters_are_never_probed_here(self):
        from gpos.tools.registry import default_registry
        reg = default_registry(FW)
        for adapter_id in ("player", "unity"):
            result = reg.probe(adapter_id)
            self.assertEqual(result.status, "UNAVAILABLE")
            self.assertIn("PLATFORM_UNSUPPORTED", {d.code for d in result.diagnostics})

    def run_transform(self, request_id, project):
        r = ToolRegistry(FW, allow_test_only=True)
        r.register(SyntheticAdapter())
        return execute(r, ExecutionRequest(adapter_id="synthetic", capability_id=syn.TRANSFORM,
                                           subject=Subject("TASK", "T"), project_root=str(project),
                                           allow_mutation=True, inputs={"text": "x"}, request_id=request_id))

    def test_request_ids_windows_cannot_name_are_refused(self):
        p = self.tmp / "p"
        shutil.copytree(FIXTURE, p)
        for rid in ("CON", "nul.txt", "COM1", "a.", "LPT1.log"):
            with self.subTest(rid=rid):
                result = self.run_transform(rid, p)
                self.assertEqual(result.status, tdg.INVALID_REQUEST)
                self.assertIn("INVALID_TOOL_REQUEST", {d.code for d in result.diagnostics})   # refused as a request
                self.assertFalse((p / ".game" / "gpos-runtime" / "tool-output" / "synthetic" / rid).exists())

    def test_a_letter_case_variant_of_a_workspace_is_never_shared(self):
        p = self.tmp / "p"
        shutil.copytree(FIXTURE, p)
        self.assertEqual(self.run_transform("Req-A", p).status, tdg.SUCCESS)
        second = self.run_transform("req-a", p)
        self.assertEqual(second.status, tdg.INVALID_REQUEST)
        self.assertIn("WORKSPACE_NOT_USABLE", {d.code for d in second.diagnostics})


# ---------------------------------------------------------------- W08 UTF-8 and bytes

@windows_only
class W08_Utf8AndBytes(Case):
    def test_the_cli_writes_utf8_with_lf_without_utf8_mode(self):
        out = subprocess.run([sys.executable, "-X", "utf8=0", "-m", "gpos.tools", "probe", "yok-測試-ğ"],
                             cwd=ROOT, capture_output=True, timeout=120)
        self.assertNotIn(b"Traceback", out.stderr + out.stdout)
        text = (out.stdout + out.stderr).decode("utf-8")
        self.assertIn("測試-ğ", text)
        self.assertNotIn(b"\r\n", out.stdout + out.stderr)

    def test_the_synthetic_artifact_bytes_are_the_same_as_on_posix(self):
        r = ToolRegistry(FW, allow_test_only=True)
        r.register(SyntheticAdapter())
        out = self.tmp / "out"
        result = execute(r, ExecutionRequest(adapter_id="synthetic", capability_id=syn.DETACHED_WRITE,
                                             subject=Subject("TASK", "T"), output_dir=str(out), allow_mutation=True,
                                             inputs={"text": "hashed · ğ"}))
        self.assertEqual(result.status, tdg.SUCCESS, [d.message for d in result.diagnostics])
        self.assertEqual(result.artifacts[0].sha256, hashlib.sha256("hashed · ğ\n".encode("utf-8")).hexdigest())
        self.assertNotIn("\\", result.artifacts[0].path)


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    print(f"GPOS Windows foundation tests ({'Windows' if WINDOWS else 'not Windows: every test skipped'})")
    sys.exit(0 if result.wasSuccessful() else 1)
