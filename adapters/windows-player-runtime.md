# Windows Player Runtime Core — Phase 2C-9.5A production gate qualification candidate

On the implementation branch, the existing production `PlayerAdapter` in `default_registry()` declares Windows
and dispatches `player.launch`, `player.status` and `player.stop` to the descriptor-free `WindowsLifecycle` backend.
The historical `WindowsPlayerAdapter` TEST_ONLY wrapper remains only for candidate regression attribution; production
does not register or substitute it. The actual production probe exposes exactly these three Windows capabilities;
helper installation, screenshot and video remain macOS-only and Windows dispatch refuses them.
The registry remains seven adapters, Windows Unity fourteen capabilities, Git two and ADB unavailable.
Release production availability requires the next Human Review; this branch is a qualification candidate only.
The frozen alpha.29 version, framework authority, Bridge 1.7.0 and macOS Player implementation are preserved.

## Payload and SESSION

The existing canonical `build_id` never supplies an executable, arguments, environment, cwd, process id or Job name.
The existing Windows Build Core consumer fully revalidates manifest/2, exact current version and limitations,
inventory, content, PE shape, ADS and NTFS identities. Missing, incompatible, tampered or replaced builds fail closed.
Only the verified `payload/Player/Player.exe` can run. The supervisor holds all payload files, manifest and fixed
helper files against writers/deletion; the existing 8192-directory-pin bound remains. Build/Git limits are unchanged.
CALLER_SUPPLIED remains unverified attribution. Separate Git observations are not authenticated attestation or an
atomic source snapshot. Historical artifacts are never upgraded or rewritten.

The existing `PLAYER_RUNTIME:<project>` SESSION authority, owner, session id, nonce and confirmation concepts are
reused. Bounded Windows JSON records are no-replace publications, bound to the authoritative lease. The launcher
records its executable, user and creation FILETIME. Unbound recovery needs explicit `recover_proven_gone`, a proven
gone launcher, no matching Player and no remaining Job. An active/unknown launcher must not be overtaken by recovery.

## Audited foundation amendment

Generic Windows detached execution remains refused. `ExecutionContext.spawn_windows_player_supervisor()` takes no
process specification. Only an allowlisted nondry `player.launch` with its just-opened matching SESSION reaches the
private boundary, once per execution. SESSION is confirmed before handoff; interrupted creation never silently
releases it. Ambiguous launch retains SESSION and returns OUTCOME_UNKNOWN, without retry or adoption.

There are two fixed creation sites: the current trusted Python interpreter with `-I -B -X utf8` and the installed
supervisor entry, then the verified Player with `-logFile <runtime-workspace>/player.log`. Isolated Python imports add
only the installed framework root. The fixed Player cwd is its runtime workspace, so driver caches are not directed
into the immutable build. Standard streams are NUL, not batch capture streams; bounded JSON is the lifecycle protocol.
Raw Player logs are retained operational files, not exported artifacts or sanitized log evidence. No shell runs.

JOB_LIST assigns both processes atomically to one session/nonce-named Job, with KILL_ON_JOB_CLOSE and
DIE_ON_UNHANDLED_EXCEPTION, no member breakaway. HANDLE_LIST gives only the supervisor the Job handle; the Player
inherits NUL handles and Job membership. The supervisor is never created suspended, avoiding a stranded suspended
handle owner if the creator dies. The Player starts suspended until its kernel identity is checked.

An outside parent Job is accepted only with explicit BREAKAWAY_OK; the kernel must accept the hierarchy. GPOS batch
Jobs forbid breakaway, and this path refuses them. The existing batch backend, atomic assignment, capture, redaction
and descendant cleanup are unchanged. A hidden private console provides an owned child process group; there is no
HWND, foreign console, desktop or window manipulation surface.

This follows Microsoft's [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[attribute lists](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute),
[nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs) and
[creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags).

## Confirmation, status and stop

The supervisor verifies SESSION and Job before launch. Handshake/binding/commit bind kernel executable, creation
FILETIME, same user, exact parent relationship, fixed command vectors, Job and helper digest. The initiating command
checks the directly created supervisor, Player and Job, revalidates the build, then rechecks ownership before commit.
Early exit is not a confirmed launch. Uncommitted shutdown begins after sixty seconds; a seventy-five-second
owned-Job watchdog also covers validation/initialization. Unknown controls fail closed. Supervisor exit/crash closes
its Job handle and remaining members are killed. Query handles are short-lived and never survive a CLI response.

Parent identity uses the documented `InheritedFromUniqueProcessId` result from
[NtQueryInformationProcess](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntqueryinformationprocess),
through the existing read-only host binding. Microsoft notes that this NT API/layout may change; unknown query
results fail closed, and the observed runtime qualification covers this host rather than future Windows versions.

Status starts no process and writes no runtime state. `identity`, `supervisor_state` and `observed_running` are current
observations; `player` and `supervisor` retain immutable launch identities, whose `running` fields describe the binding
moment. Exit codes come from the supervisor, never PID absence. Build trust/drift is independent of process identity.

The existing owner alone can stop a live runtime. The supervisor requests CTRL_BREAK only for its retained child's
console group, never group zero. A retained process handle prevents group-id reuse. After ten seconds it terminates
only its held Player handle if needed; Job close removes descendants. An unresponsive but still fully proven live
supervisor allows a reproven owned-Job termination fallback. Unknown identity, Job access or cleanup retains SESSION.
Build drift never blocks stopping a proven owned Player. Another owner can only explicitly recover a proven gone
session; no PID-only stale-lease breaking occurs.

[Console groups](https://learn.microsoft.com/en-us/windows/console/console-process-groups),
[GenerateConsoleCtrlEvent](https://learn.microsoft.com/en-us/windows/console/generateconsolectrlevent) and
[process handle/id lifetime](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-process_information)
define this boundary. Delivery is an attempt, not proof of application graceful cleanup or saved state. The tested
Unity fixture did not exit during the grace interval; actual stop used the retained handle and reports FORCED_STOP.
No gameplay, playability, visual, motion or Human evidence is produced.

## Qualification boundaries and Phase 2C-9.5B

Runtime qualification covers the tested Windows 11 host, CPython 3.14.8 x64, Unity 6000.6.4f1 and alpha.29 CLASSIC
StandaloneWindows64 x64 Mono nondevelopment subset only. Git runtime covers 2.56.0.windows.2, not every version above
the unchanged 2.43.0 floor. Accepted Git/Build results are reused only with original attribution and unchanged source.
Real observations and synthetic/deterministic fault injections are distinguished in HUMAN_REVIEW; injected crash
termination codes are not natural Unity engine-crash qualification.

This is ownership and cleanup, not an OS sandbox, protection against hostile same-user code, private ACL guarantee,
atomic repository snapshot or power-loss durability. GPOS installation, interpreter, OS and game code are trusted
assumptions. Native game/driver code can write elsewhere; pins cannot police arbitrary game writes. Same-user code
opening additional Job handles can interfere with kill-on-close lifetime. Unknown state is refused, not repaired.
File I/O has no hard kernel cancellation guarantee. Job-loss without an exit record is GONE_UNOBSERVED.

macOS/Linux runtime, capture, gameplay/visual quality, named antivirus and power-loss behavior are NOT_RUN. After
separate Human authorization for 2C-9.5B, bind capture to this proven process plus exact owned window identity,
refuse ambiguous/stale windows, and qualify session changes and window-id reuse. No desktop-wide capture, arbitrary
window targets, input injection or audio capture should be introduced.
