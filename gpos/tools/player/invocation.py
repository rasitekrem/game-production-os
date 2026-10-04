"""The one place the player adapter originates an external process (alpha.22).

The frozen ToolResult/ToolProvenance model records one command, so every player capability originates at most one
external OS process, and this module enforces it. The permanent table (contract.INVOCATIONS):

    player.install-capture-helper   none
    player.launch                   SUPERVISE   the verified helper executable, detached: supervise <request>
    player.status                   none
    player.capture-screenshot       SHOT        /usr/bin/open ... GposPlayerHelper.app --args shot <request>
    player.capture-video            VIDEO       /usr/bin/open ... GposPlayerHelper.app --args video <request>
    player.stop                     none

`/usr/bin/open` is the reviewed macOS LaunchServices launcher: launching the helper through it is what makes Screen
Recording attributed to the helper itself. The recorded command is truthfully `/usr/bin/open` with its fixed
arguments; the helper's own identity is bound separately by the helper's result. Every argument is derived from the
verified installed helper and the foundation-owned workspace — nothing from a request.

No other player module calls `context.run` or `context.spawn_detached`, and no player module starts a process any
other way (tests pin both).
"""

from .. import process as proc
from . import contract as c
from . import runtime as rt

OPEN = "/usr/bin/open"
ENVIRONMENT = proc.EnvironmentPolicy(inherit=("HOME", "TMPDIR", "LANG"),
                                     overrides=(("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),))
MODES = {c.SHOT: "shot", c.VIDEO_KIND: "video"}
OPEN_CAPTURE_BYTES = 64 * 1024


def supervise_spec(helper_executable, runtime_dir):
    return proc.DetachedProcessSpec(executable=str(helper_executable),
                                    argv=("supervise", f"{runtime_dir}/{rt.SUPERVISOR_REQUEST}"),
                                    cwd=str(runtime_dir), env=ENVIRONMENT)


def open_argv(helper_bundle, workspace, kind):
    return ("-n", "-W", "--stdout", f"{workspace}/helper.out", "--stderr", f"{workspace}/helper.err",
            str(helper_bundle), "--args", MODES[kind], f"{workspace}/{rt.HELPER_REQUEST}")


def open_spec(helper_bundle, workspace, kind, timeout):
    return proc.ToolProcessSpec(executable=OPEN, argv=open_argv(helper_bundle, workspace, kind), cwd=str(workspace),
                                timeout=timeout, env=ENVIRONMENT, capture_bytes=OPEN_CAPTURE_BYTES)


class ExternalInvocation:
    """At most one external process for one execution, of the one kind its capability allows."""

    def __init__(self, context, capability_id):
        self._context = context
        self._allowed = c.INVOCATIONS[capability_id]
        self.kind = None
        self.spec = None

    def _claim(self, kind):
        if kind not in self._allowed:
            raise AssertionError(f"this capability originates no {kind} process")
        if self.kind is not None:
            raise AssertionError("a player capability originates at most one external process")
        self.kind = kind

    def supervise(self, helper_executable, runtime_dir):
        """Start the verified helper's supervise mode, detached. Returns the foundation's DetachedHandle."""
        self._claim(c.SUPERVISE)
        self.spec = supervise_spec(helper_executable, runtime_dir)
        return self._context.spawn_detached(self.spec)

    def launch_services(self, kind, helper_bundle, workspace, timeout):
        """Run `/usr/bin/open -n -W ... <helper> --args <shot|video> <request>` and wait for the helper to end."""
        self._claim(kind)
        self.spec = open_spec(helper_bundle, workspace, kind, timeout)
        return self._context.run(self.spec)

    def command(self):
        return self.spec.command_for_provenance() if self.spec is not None else None

    def environment(self):
        return self.spec.env.metadata() if self.spec is not None else None
