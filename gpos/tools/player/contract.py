"""The platform-neutral player runtime contract (alpha.22).

Everything a caller sees of the player adapter is named here: capability ids, the one request input, the classes a
runtime, an identity and a stop can be in, the public result shapes and the fixed bounds. Nothing here knows how a
platform launches, observes or captures a process — no application bundle, launcher, permission database or window
id appears in it — so a later Windows backend keeps this contract unchanged and supplies its own backend modules.
"""

import re

ADAPTER_ID = "player"
ADAPTER_VERSION = "1.0.0"

INSTALL = "player.install-capture-helper"
LAUNCH = "player.launch"
STATUS = "player.status"
SCREENSHOT = "player.capture-screenshot"
VIDEO = "player.capture-video"
STOP = "player.stop"
CAPABILITY_IDS = (INSTALL, LAUNCH, STATUS, SCREENSHOT, VIDEO, STOP)

RESOURCE_KIND = "PLAYER_RUNTIME"   # one runtime per project: PLAYER_RUNTIME:<resolved project root>

# The one request input of the whole adapter.
DURATION_INPUT = "duration_seconds"
MIN_VIDEO_SECONDS, MAX_VIDEO_SECONDS = 1, 15
# stop only: a different owner may close a session whose runtime is proven gone.
RECOVER_INPUT = "recover_proven_gone"

# The external processes each capability may originate (at most one): the permanent invocation table.
SUPERVISE, SHOT, VIDEO_KIND = "SUPERVISE", "SHOT", "VIDEO"
INVOCATIONS = {INSTALL: (), LAUNCH: (SUPERVISE,), STATUS: (), SCREENSHOT: (SHOT,), VIDEO: (VIDEO_KIND,), STOP: ()}

# Classes. The SESSION lease records the phase it was opened in (LAUNCHING) and is never changed afterwards.
PHASE_LAUNCHING = "LAUNCHING"
BOUND, LAUNCHING_UNRESOLVED = "BOUND", "LAUNCHING_UNRESOLVED"
PROVEN, GONE, NOT_THIS_PROCESS, UNPROVEN = "PROVEN", "GONE", "NOT_THIS_PROCESS", "UNPROVEN"
ALIVE = "ALIVE"
VALID, DRIFT = "VALID", "DRIFT"
EXACT, ABSENT, UNTRUSTED = "EXACT", "ABSENT", "UNTRUSTED"
NOT_CHECKED, UNKNOWN = "NOT_CHECKED", "UNKNOWN"
GRACEFUL_STOP, FORCED_STOP, EXITED, CRASHED = "GRACEFUL_STOP", "FORCED_STOP", "EXITED", "CRASHED"
GONE_UNOBSERVED, OUTCOME_UNKNOWN = "GONE_UNOBSERVED", "OUTCOME_UNKNOWN"
STOP_CLASSES = (GRACEFUL_STOP, FORCED_STOP, EXITED, CRASHED, GONE_UNOBSERVED, OUTCOME_UNKNOWN)

# Bounds.
SETTLE_SECONDS = 5            # a splash-race mitigation only, never a readiness signal
MAX_EDGE_PX = 1920
VIDEO_FPS = 30
MAX_PNG_BYTES = 16 * 1024 * 1024
MAX_MP4_BYTES = 64 * 1024 * 1024
STOP_GRACE_SECONDS = 10
KILL_WAIT_SECONDS = 5
HANDSHAKE_WAIT_SECONDS = 20
LOG_TAIL_LINES = 20
MAX_LOG_READ_BYTES = 8 * 1024 * 1024
MAX_LOG_ARTIFACT_BYTES = 1024 * 1024

HEX32 = re.compile(r"[0-9a-f]{32}")

LIMITATIONS = (
    "Diagnostic local run: the build ran on this macOS host, not on a declared target platform; this is never "
    "TARGET_RUNTIME evidence.",
    "GPOS injected no input; the game ran whatever its own code does on start.",
)
CAPTURE_LIMITATIONS = (
    "The capture waits a fixed 5 s technical settle after the window appears; it may still show loading, a splash "
    "screen or any game state — it is not a readiness or gameplay proof.",
    "Exactly the game's own window was captured (no display, area or other window); the image is scaled down so the "
    "longest edge is at most 1920 px and never scaled up.",
)


def public_process(pid, started_at, executable_relative):
    """The portable public view of a runtime process."""
    return {"pid": pid, "started_at": started_at, "executable": executable_relative}
