# Player runtime adapter (Phase 2C-8)

Status: GPOS `1.0.0-alpha.22` · implementation in [`gpos/tools/player/`](../gpos/tools/player/__init__.py) · macOS only

The `player` adapter runs exactly one already-built game on this host and observes it — launch, status, an exact-window
screenshot, a short silent exact-window video, stop — without generic process or desktop control. It is engine-neutral:
alpha.22 resolves one build kind, an alpha.21 Unity macOS application bundle (`unity.build-player`), and has one
backend, macOS. Its one external tool is the **GPOS Player Helper**, a small native application shipped as release
content.

```bash
python3 -m gpos.tools execute --adapter player --capability player.install-capture-helper --project P --allow-mutation
python3 -m gpos.tools execute --adapter player --capability player.launch --project P --build-id build-<id> --actor AGENT:me --allow-mutation
python3 -m gpos.tools execute --adapter player --capability player.status --project P --session-id <sid> --actor AGENT:me
python3 -m gpos.tools execute --adapter player --capability player.capture-screenshot --project P --session-id <sid> --build-id build-<id> --actor AGENT:me --allow-mutation
python3 -m gpos.tools execute --adapter player --capability player.capture-video --project P --session-id <sid> --build-id build-<id> --input duration_seconds=5 --actor AGENT:me --allow-mutation
python3 -m gpos.tools execute --adapter player --capability player.stop --project P --session-id <sid> --build-id build-<id> --actor AGENT:me --allow-mutation
```

## Descriptor

| Field | Value |
|---|---|
| adapter id / version | `player` / `1.0.0` |
| tool family / adapter kind / state model | `DEVICE` / `DEVICE` / `STATEFUL` |
| target tool | `GPOS Player Helper` (`com.gpos.player-helper` 1.0.0, ad-hoc signed, universal arm64 + x86_64, macOS ≥ 14) |
| platforms | `MACOS` |
| network | `TOOL_INHERENT` (below) |
| probe | AVAILABLE when the helper at `~/Applications/GPOS/GposPlayerHelper.app` is byte for byte this release |

The probe's `tool_path` is the installed helper executable's **real absolute path**; it names the local home directory,
and so the user name, in provenance. `tool_version` is `1.0.0+<bundle digest prefix>`.

## Capabilities

| Capability | Category / class / state | Lease | `requires_tool` | External processes |
|---|---|---|---|---|
| `player.install-capture-helper` | DEPLOY / MUTATING / STATELESS, `OFFLINE_ANALYSIS` | none | no | 0 |
| `player.launch` | RUN / MUTATING / STATEFUL | `SESSION_OPEN` on `PLAYER_RUNTIME`, single writer | yes | 1: the verified helper, `supervise`, detached |
| `player.status` | INSPECT / READ_ONLY / STATEFUL | `SESSION_REQUIRED` | no | 0 |
| `player.capture-screenshot` | CAPTURE / MUTATING / STATEFUL | `SESSION_REQUIRED`, single writer | yes | 1: `/usr/bin/open` … `shot` |
| `player.capture-video` | CAPTURE / MUTATING / STATEFUL | `SESSION_REQUIRED`, single writer | yes | 1: `/usr/bin/open` … `video` |
| `player.stop` | RUN / MUTATING / STATEFUL | `SESSION_CLOSE`, single writer | no | 0 |

All runtime capabilities observe `DIAGNOSTIC_RUNTIME`, are project-bound, refuse a caller `output_dir` and take no
caller resource. The only request input of the whole adapter is `duration_seconds` (an integer 1–15) for a video, and
`recover_proven_gone` (a boolean) for a stop. The caller names a build (`build_id`), a session (`session_id`) and
nothing else: never a pid, window, screen, region, coordinates, path, command, argument, environment or helper mode.
Captures and stop restate `build_id` (and may restate `build_revision` and `target_platform=MACOS`); the adapter
verifies them against the session, because evidence provenance is taken from them.

The capture capabilities are MUTATING: they launch the helper and write artifacts into their execution workspace. They
do not modify the Player, the project's authority or any privacy setting, and they take no lifecycle session of their
own: the session lease supplies the writer authority.

## The one-external-process invariant

The frozen result and provenance model records one command, so each player capability originates at most one external
OS process. `invocation.py` is the only player module that calls `context.run` or `context.spawn_detached`; it claims
the one process of the one kind the capability allows (`contract.INVOCATIONS`) and refuses a second. No player module
starts a process any other way; tests pin both structurally, and mutations of either are caught.

Status, stop, the foreign-instance scan and the stop fallback run **in process**, through `macos.py` (libproc, csops,
CoreGraphics) and `appkit.py` (AppKit) — ctypes calls into fixed system libraries, never another process.

## The GPOS Player Helper

Release content: `helper_src/` (the Swift source), `helper_release/GposPlayerHelper.app` (the exact prebuilt bundle)
and `helper_release/manifest.json` (per-file size, mode and SHA-256, the bundle digest, the per-architecture CDHash, the
source digest and the toolchain). `tests/build_player_helper.py` is maintainer tooling that rebuilds the bundle
reproducibly (whole-module compilation by relative name, `ZERO_AR_DATE=1`, no debug map, `lipo`, `codesign -s - -i
com.gpos.player-helper`); its `--check` proves the committed bundle is what the committed source builds to with the
recorded toolchain. No production capability compiles, signs, downloads or edits a privacy setting.

Exactly three modes, each given exactly one GPOS-written request file:

| Mode | Started by | How |
|---|---|---|
| `supervise <runtime>/supervisor-request.json` | `player.launch` | direct exec, detached |
| `shot <workspace>/helper-request.json` | `player.capture-screenshot` | `/usr/bin/open -n -W --stdout … --stderr … <helper.app> --args shot …` |
| `video <workspace>/helper-request.json` | `player.capture-video` | the same, `video` |

There is no permission-request, window-listing, process-listing or terminate mode. `CGRequestScreenCaptureAccess` is
never called.

**Install.** `player.install-capture-helper` copies the release to exactly `~/Applications/GPOS/GposPlayerHelper.app`,
the home directory taken from the account database (`pwd`), never from `HOME` or a request (the foundation's
`USER_APPLICATIONS_GPOS` host location, allowlisted for this capability only). It classifies what is there:

| State | Install |
|---|---|
| ABSENT | copy into `.gpos-staging-<request id>/` beside it, verify, publish with `renamex_np(RENAME_EXCL)` |
| EXACT | nothing to do (`PLAYER_HELPER_EXACT`) |
| UNTRUSTED (anything else: modified, extra or missing files, a link) | `CONFLICT`, never overwritten, repaired or removed |

`renamex_np(RENAME_EXCL)` refuses atomically when anything — a file, a directory (even an empty one), a link — exists at the
destination; a plain `rename` replaces an empty directory, so it is never the no-overwrite authority. A destination
that appears during the install is re-classified: EXACT is success, anything else a conflict. Only the execution's own
staging directory is removed; an interrupted earlier install's staging directory is reported, never removed.

**Screen Recording.** A Human grants it once to the installed helper in System Settings → Privacy & Security → Screen &
System Audio Recording → GposPlayerHelper. GPOS never changes this setting. The ad-hoc grant binds the helper's code
identity: a changed helper needs the grant again.

## Session and runtime files

One runtime per project: the `PLAYER_RUNTIME:<resolved project root>` SESSION lease is the only lifecycle authority, and
the foundation session id is the runtime handle. The lease is written once, before the Player starts, with the stable
pre-launch facts — session id, launch request id, phase `LAUNCHING`, build id and revision, manifest SHA-256, payload
tree digest, Unity build GUID, application id, the canonical executable path and its device/inode, the runtime
directory, a nonce and the helper release identity — and is never changed.

The runtime directory is the launch execution's workspace, `.game/gpos-runtime/tool-output/player/<launch request id>/`.
Its files are observations bound to the lease, never authority; each has an exact name and a strict schema, is bounded,
written once (exclusive temporary file, fsync, a hard link that never replaces anything) and never read through a link:

| File | Writer | Content |
|---|---|---|
| `supervisor-request.json` | launch | the closed supervise request |
| `handshake.json` | supervise | the supervisor's and the Player's kernel identity and the supervisor's code hash |
| `runtime-binding.json` | launch | the verified identities (also the launch's artifact) |
| `commit.json`, `abort.json` | launch (abort also stop) | whether the launch committed |
| `stop-intent-<rid>.json` | stop | the normal stop request |
| `fallback-<rid>.json`, `kill-<rid>.json` | stop | a stop without the supervisor; written before acting |
| `exit.json` | supervise | the Player's exit status observed by its parent |
| `player.log` | the Player | the one log (`-logFile`) |

Status, capture and stop derive the runtime directory from the lease, check `runtime-binding.json` belongs to its
session, nonce, build and executable, and re-prove the process in the kernel. A missing binding is an unresolved
`LAUNCHING` session.

**Identity.** A Player is PROVEN when its pid is alive, its kernel start time is exactly the bound one and the kernel
names exactly the bound executable; NOT_THIS_PROCESS when the pid now belongs to another process; GONE when there is
none; UNPROVEN when it is the same process instance but the kernel no longer names the bound executable (moved, deleted,
replaced) — alive, never signalled and its session never closed. The supervisor is proven by pid, kernel start time and
the kernel's code hash (`csops`), not by its path: the helper install may be moved or removed after launch and the
running supervisor still owns the Player.

## Launch

helper EXACT → resolve and revalidate the build (alpha.21 `revalidate` unchanged; the manifest's own bytes hashed) →
the foreign scan → open the session → write the supervise request → start the one detached `supervise` → the handshake
(≤ 20 s) → prove the supervisor (spawned pid, start time, the release CDHash in the kernel) and the Player (its child,
its own process group, the build's executable, kernel identity, device/inode) → revalidate again → the foreign scan
again (exactly the new Player may match) → `runtime-binding.json` → `commit.json` → confirm the session →
`PLAYER_LAUNCHED`.

`supervise` validates its closed request (its own directory must be a GPOS player workspace of a project; the
executable must be `<that project>/.game/gpos-runtime/tool-output/unity/<build>/payload/Player.app/Contents/MacOS/
<CFBundleExecutable>` with the expected device, inode and bundle identifier), launches exactly
`<executable> -logFile <runtime>/player.log` in its own process group with only `HOME`, `TMPDIR`, `LANG` and a fixed
`PATH`, writes the handshake and waits for commit, abort, a stop intent, the commit deadline (60 s) or the Player's exit.
To stop it asks the Player to quit through AppKit (`NSRunningApplication.terminate`), waits 10 s, then SIGKILLs only its
own, not-yet-reaped child — whose pid cannot have been reused — reaps it and writes `exit.json`. It never retries,
relaunches or signals anything else.

**Foreign instances.** Any other running instance of the same application id — another build, a renamed copy anywhere,
a manual launch — is `PLAYER_RUNTIME_CONFLICT`, found by an own-user libproc scan (each process's executable inside a
`.app` whose `Info.plist` names that identifier) united with AppKit's running applications for that identifier. Matching
is never by name; the other instance is never adopted or signalled; an unrelated application never blocks. Other users'
processes are not visible to the scan.

**A launch that does not commit** writes `abort.json`, waits for the supervisor's exit record and releases the session
only when the Player is proven to have ended (or never started); otherwise it keeps the session (`OUTCOME_UNKNOWN`,
`PLAYER_LAUNCH_UNRESOLVED`). Nothing is retried. If the launching command itself dies, the supervisor abandons the
uncommitted Player at its commit deadline.

## Status

Read-only, no process, no write: the session phase, the Player's identity, the supervisor, any exit observation, the
build trust (`VALID` / `DRIFT`, including a replaced executable), the helper state, window presence and candidate count
(in-process CoreGraphics; owner, layer, alpha and bounds need no permission) and the last 20 sanitized log lines.
Screen Recording is not probed: `capture_permission` is `NOT_CHECKED` with an EXACT helper, `UNKNOWN` otherwise.

## Capture

Authority: the session → the runtime binding → the PROVEN Player → an unchanged manifest SHA-256 and a full payload
revalidation → exactly one capturable window. Then one LaunchServices invocation. Before it the installed helper must be
EXACT; after it the helper must still be EXACT, and the helper's result must name this request's nonce and session and
the release's version, executable, bundle path and CDHash. The Player is re-proven and the build re-validated after the
capture, and the published file is validated again by GPOS before it becomes an artifact.

Inside `shot` and `video` the helper first runs the read-only `CGPreflightScreenCaptureAccess()`; when it is false it
answers `REFUSED`/`PERMISSION_NOT_GRANTED` with `sck_called: false` and touches no ScreenCaptureKit API
(`CAPTURE_PERMISSION_REQUIRED`, `UNAVAILABLE`, with the Human instruction). Then: exactly one on-screen, layer-0,
visible window owned by the pid that ScreenCaptureKit also lists as the pid's; a fixed 5 s settle; the same window again
and the identity again; `SCContentFilter(desktopIndependentWindow:)` without the cursor — never a display, an area or
another window. Output is published under its final name only after it validated.

- **Screenshot:** PNG, the longest edge at most 1920 px, aspect preserved, never upscaled; GPOS checks the signature,
  every CRC, an 8-bit RGB/RGBA IHDR of the reported size, known critical chunks, IEND last and the exact inflated size.
- **Video:** 1–15 s, at most 1920 px, at most 30 fps, H.264 at about 8 Mbit/s, no audio of any kind, no cursor. The
  helper finishes the AVAssetWriter and checks the file with AVFoundation (one video track, no audio track, duration,
  size, at least one frame per second); GPOS then walks the MP4's boxes (bounded) and checks the same independently. A
  Player that exits, a helper that dies or any failed check publishes nothing. FFprobe and FFmpeg are never run here.

The 5 s settle only mitigates the splash-screen race: a capture may show loading, a splash or any game state.

**Composition.** A published runtime video may be inspected and processed afterwards as separate tool results —
`ffprobe.inspect`, `ffmpeg.extract-frame`, `ffmpeg.extract-clip` — with the video as an input artifact in
`DIAGNOSTIC_RUNTIME`; derived outputs keep that context and name the video as their origin.

## Stop

- **Normal:** with the supervisor proven alive, stop writes `stop-intent-<rid>.json` and waits for `exit.json`; the
  supervisor asks the Player to quit and kills its own child after the grace. No process is started.
- **Fallback:** when the supervisor is gone (or silent) and the Player is still PROVEN, stop writes
  `fallback-<rid>.json`, asks the Player to quit in process through AppKit — only when AppKit's bundle identifier and
  executable path for that pid are exactly the bound ones — re-proves it every 100 ms for 10 s and, if it is still the
  same proven process, writes `kill-<rid>.json` and SIGKILLs exactly that pid. Never SIGTERM, never by name, never an
  unproven process. The Player is not the caller's child, so a pid could in principle be reused in the microseconds
  between the last proof and the signal; that residual is disclosed.
- **Unresolved launch** (no binding): stop writes `abort.json` so a live supervisor ends its own child, and closes the
  session only once no instance of the application runs.
- Build drift never prevents stopping a PROVEN Player; it is reported (`PLAYER_BUILD_DRIFT`).

| Class | When |
|---|---|
| `GRACEFUL_STOP` | the supervisor observed exit code 0 after this stop's intent |
| `FORCED_STOP` | the supervisor's kill, or a fallback kill this stop sent |
| `EXITED(code)` | the Player exited by itself, or with a non-zero code after the intent |
| `CRASHED(signal)` | a signal GPOS did not send (for example an abort, 6) |
| `GONE_UNOBSERVED` | gone without an exit observation (the fallback path, a dead supervisor) |
| `OUTCOME_UNKNOWN` | still alive (PROVEN or UNPROVEN) after everything; the session is kept |

The session's owner closes it; another owner may only pass `recover_proven_gone` and only once the Player is proven
gone (an attributable recovery). Stop produces the sanitized log (`runtime-log.txt`) and a runtime record, and offers
the log as `RUNTIME_EVIDENCE` only when the supervisor observed the end, the build still revalidates and `build_id` was
restated.

## The Player log

`player.log` is read bounded (the last 8 MiB) and every line is normalized (the runtime directory and project root
become relative, the account home becomes `~`), credential-redacted (the shared boundary), stripped of absolute and `~/`
paths by a player-only, space-aware rule (a path continues across a space when the next token contains a `/`, so
`~/Library/Application Support/<Company>/<Product>/…` is removed whole instead of leaving a ` Support/…` fragment),
passed through the alpha.20 absolute-path rule as defence in depth and clipped; the artifact keeps the newest lines
within 1 MiB. A path whose last component contains a space and no further `/` can leave that last word. The alpha.20 and
alpha.21 sanitizers are unchanged.

## Evidence

| Capability | Candidate | Artifacts |
|---|---|---|
| capture-screenshot | `VISUAL_EVIDENCE` / `DIAGNOSTIC_RUNTIME` | the PNG and `capture-record.json` |
| capture-video | `MOTION_EVIDENCE` / `DIAGNOSTIC_RUNTIME` | the MP4 and `capture-record.json` |
| stop | `RUNTIME_EVIDENCE` / `DIAGNOSTIC_RUNTIME` | the sanitized log and `runtime-record.json` |

Never `TARGET_RUNTIME`, `DEVICE_EVIDENCE`, `PERFORMANCE_EVIDENCE`, `PERSISTENCE_EVIDENCE` or `HUMAN_EVIDENCE`. The capture
record binds the session, the build (id, revision, manifest SHA-256, tree digest, Unity GUID, MACOS), the application
id, the process identity before and after, the mechanism, the settle and timing, the helper release version, digest and
CDHashes with the helper-reported pid, start time and CDHash, the request nonce and the window proof — without absolute
paths or window ids. Every candidate states that the run was a diagnostic local macOS run, not a target-platform run.

## Provenance and LaunchServices (D7)

The launch's recorded command is the helper executable with `supervise <request>`. A capture's recorded command is
truthfully `/usr/bin/open` with its fixed arguments: macOS LaunchServices is the reviewed launcher that makes Screen
Recording attributed to the helper itself; a directly executed helper would be attributed to the caller's application.
This exception applies to the capture modes only. The helper's own identity is bound by its result and the capture
record. The checks around the invocation are not one atomic step: the helper's self-reported identity binds the code
that ran only against honest code, and a locally swapped binary would also lose the ad-hoc Screen Recording grant.

## Network

`TOOL_INHERENT`: GPOS accepts no URL, host, endpoint, proxy or credential and originates no network operation; the game
build it launches is the caller's own code and may use the network (including the engine's analytics); no
operating-system network confinement is claimed.

## Side effects

The game may write its `~/Library/Application Support/<Company>/<Product>` folder (its persistent data, including the
engine's analytics archive), `~/Library/Preferences/<bundle id>.plist` (PlayerPrefs, window size and mode, engine
session identifiers), `~/Library/Saved Application State/<bundle id>.savedState`, its logs and, when it crashes, a crash
report in `~/Library/Logs/DiagnosticReports`. GPOS never reads, inspects or deletes any of them, and there is no
reset-save capability. The install writes `~/Applications/GPOS/` (and creates `~/Applications` when absent).

## Diagnostics

`PLAYER_HELPER_ABSENT`, `PLAYER_HELPER_UNTRUSTED`, `CAPTURE_PERMISSION_REQUIRED` (UNAVAILABLE);
`PLAYER_RUNTIME_CONFLICT`, `PLAYER_HELPER_INSTALL_CONFLICT`, `LIVE_SESSION_HELD`, `LIVE_SESSION_MISMATCH` (CONFLICT);
`PLAYER_BUILD_INVALID`, `CAPTURE_BUILD_DRIFT`, `PLAYER_SUPERVISOR_FAILED`, `PLAYER_HANDSHAKE_INVALID`,
`PLAYER_IDENTITY_UNPROVEN`, `PLAYER_EXITED_DURING_LAUNCH`, `PLAYER_RUNTIME_GONE`, `CAPTURE_WINDOW_NOT_FOUND`,
`CAPTURE_WINDOW_AMBIGUOUS`, `CAPTURE_WINDOW_OWNER_MISMATCH`, `CAPTURE_TARGET_EXITED`, `CAPTURE_FAILED`,
`CAPTURE_OUTPUT_INVALID`, `CAPTURE_HELPER_IDENTITY_MISMATCH` (FAILED); `PLAYER_LAUNCH_UNRESOLVED`,
`PLAYER_STOP_OUTCOME_UNKNOWN` (OUTCOME_UNKNOWN); `PLAYER_LAUNCHED`, `PLAYER_STOPPED`, `PLAYER_BUILD_DRIFT`,
`PLAYER_HELPER_INSTALLED`, `PLAYER_HELPER_EXACT`, `PLAYER_INSTALL_STAGING_LEFTOVER`, `PLAYER_SESSION_RECOVERED`,
`PLAYER_EVIDENCE_WITHHELD` (INFO).

## Portability

`contract.py` is the portable surface: capability ids, the inputs, the classes and the public shapes (application id,
process pid / start time / project-relative executable, window presence and candidate count). LaunchServices,
application bundles, privacy settings, CoreGraphics, AppKit and window ids exist only in the macOS backend modules
(`macos.py`, `appkit.py`, `helper.py`, `invocation.py`'s specs). A Windows backend (an executable directory, process
creation time and image path for identity, a window-close request for a graceful stop, Windows.Graphics.Capture) is
deferred, and `TARGET_RUNTIME` is reconsidered only at Windows production-host qualification.
