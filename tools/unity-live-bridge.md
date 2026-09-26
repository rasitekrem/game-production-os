# Unity live Editor plane (Phase 2C-6A, Scene authoring Phase 2C-6B1)

Code: [`gpos/tools/unity/live.py`](../gpos/tools/unity/live.py) and the fixed bridge package in [`gpos/tools/unity/live_bridge/`](../gpos/tools/unity/live_bridge/manifest.json) · adapter id `unity` (the same adapter as the [batch plane](unity-adapter.md)) · bridge `com.gpos.live-bridge` 1.1.0, protocol `gpos.unity.live/2` · status: live session foundation plus [Scene authoring](unity-live-authoring.md). Built on the [tool adapter foundation](adapter-foundation.md), extended by SESSION leases, lease modes and the `OUTCOME_UNKNOWN` result status.

The live plane lets an agent work with a Unity Editor that a Human already has open, after the Human approves it **inside the Editor**. It is a closed set of operations through a fixed, audited GPOS Editor bridge:

| Capability | Class | Lease mode | What it does |
|---|---|---|---|
| `unity.live-install-bridge` | `MUTATING`, `STATELESS`, `OFFLINE_ANALYSIS` | `EXECUTION` | writes the audited bridge package into a closed project, or upgrades an exact earlier released bridge there |
| `unity.live-status` | `READ_ONLY`, `EDITOR` | `NONE` | bridge and session facts from files and process identity; sends nothing to the Editor |
| `unity.live-attach` | `MUTATING`, `EDITOR` | `SESSION_OPEN` | Human approval in the Editor, then the SESSION lease, then the bridge binds the session |
| `unity.live-detach` | `MUTATING`, `EDITOR` | `SESSION_CLOSE` | unbind and release; release after a proven clean close; or recover a stale session after Human approval |
| `unity.live-inspect` | `READ_ONLY`, `EDITOR` | `SESSION_REQUIRED` | bounded facts: session, focus, compilation, Play Mode, scenes, a bounded hierarchy summary |
| `unity.live-enter-playmode`, `unity.live-pause`, `unity.live-resume`, `unity.live-exit-playmode` | `MUTATING`, `EDITOR` | `SESSION_REQUIRED` | the Editor's Play Mode state only |

None produces evidence. There is no generic command, no C#, no reflection target, no `-executeMethod`, no menu execution, no input injection, no capture, no Editor launch, quit, focus or restart, and no MCP transport. Input for every capability: `unity_project`, exactly as in the batch plane. The twelve Scene-authoring capabilities of alpha.17 use the same session and bridge; see [unity-live-authoring.md](unity-live-authoring.md).

## Bootstrap

The bootstrap order is fixed; there is no hot install or upgrade in an open project.

1. The Unity project is closed (`unity.live-install-bridge` refuses with `ENGINE_PROJECT_LOCKED` while `Temp/UnityLockfile` exists).
2. GPOS installs the bridge: `Packages/com.gpos.live-bridge/`, staged in the GPOS runtime area and moved into place with one atomic rename. `Packages/manifest.json` is not edited, and no Package Manager, registry, Git or network is involved.
3. The Human opens the project in Unity normally.
4. The bridge reaches READY.
5. An attach can be proposed.

An identical installed package is left alone (`LIVE_BRIDGE_ALREADY_INSTALLED`, no mutation). An exact earlier released bridge is [upgraded](#upgrading-the-bridge). Anything else is `LIVE_BRIDGE_UNTRUSTED` and is never overwritten or repaired: a modified, missing or extra file, an extra directory, a symbolic link, a wrong file type, or a package GPOS does not know (including a "newer" one). The installer runs the batch plane's static project preflight first, so a project with remote package sources is refused.

## Upgrading the bridge

Earlier released bridges are release content. [`live_bridge/history/1.0.0.json`](../gpos/tools/unity/live_bridge/history/1.0.0.json) is byte for byte the manifest frozen in `v1.0.0-alpha.16`, and the code pins its protocol and package digest (`gpos.unity.live/1`, `546b3cfb…8c66`). A test reads the manifest from the frozen tag and fails when the copy or the pin differs. `unity.live-status` reports the installed package as ABSENT, EXACT, PREVIOUS (with its version) or UNTRUSTED. GPOS never talks to an earlier bridge: its sessions and Scene authoring need 1.1.0, so a PREVIOUS package is `LIVE_BRIDGE_INCOMPATIBLE` until it is upgraded.

`unity.live-install-bridge` upgrades a PREVIOUS package only while the project is closed (`ENGINE_PROJECT_LOCKED` otherwise), holding its `EXECUTION` writer lease. Replacing a directory is not one atomic step, so it is a transaction:

1. Stage the new package in the GPOS runtime area and verify it against its manifest.
2. Write the transaction record `.game/gpos-runtime/unity/install-txn/<project key>.json`.
3. Re-verify the installed package against the pinned old manifest, then move it to the transaction's backup.
4. Move the staged package into `Packages/`.
5. Verify the installed package byte for byte.
6. Verify and remove the backup file by file, remove the transaction's empty directories, and clear the record.

Each move is one rename on one file system (a cross-device move is refused, never copied). The record is at most 4 KiB, strict JSON with exact keys. It holds identifiers and metadata only: schema, transaction id (32 hex digits), project key and relative Unity project, old and new version and digest, phase and start time. The staging and backup directories are derived from the GPOS runtime root, fixed names and the validated transaction id (`.game/gpos-runtime/unity/install-staging/<id>/` and `install-backup/<id>/`); no path is ever read from the record. A runtime directory on the way that is a symbolic link is refused.

**Recovery.** When a record exists, the next `unity.live-install-bridge` classifies what is actually on disk. Each place (installed, backup, staging) is absent, exactly a known release, a strict subset of one release's exact files, or UNKNOWN:

- the new bridge is installed exactly: finish (remove the backup only if it is exactly, or a subset of, the old release; clear the record);
- the old bridge is still installed and nothing was moved: roll back (discard only an exact staged copy, clear the record, then upgrade again);
- nothing is installed and the backup is the exact old bridge: place the exact staged new bridge and finish, or, with no exact staged copy, restore the old bridge and upgrade again;
- anything else, including unknown content in the installed or backup place, or a record that cannot be trusted exactly: `LIVE_BRIDGE_UPGRADE_INCOMPLETE`, and nothing is changed.

A finished or rolled-back recovery reports `LIVE_BRIDGE_UPGRADE_RECOVERED`, and a completed upgrade `LIVE_BRIDGE_UPGRADED`. Unknown content is never removed or overwritten: an unknown staging directory stays where it is, and there is no force repair and no downgrade. A crash before the record exists leaves at most a verified staged copy in the runtime area, which a later upgrade does not use.

## The fixed bridge

- GPOS release content with fixed `.meta` files whose GUIDs are derived from the package id and path, so the bytes and the digest are the same everywhere. [`manifest.json`](../gpos/tools/unity/live_bridge/manifest.json) lists every file's size and SHA-256 and the package digest (SHA-256 over sorted lines of path, SHA-256 and size). [`tests/generate_live_bridge_manifest.py`](../tests/generate_live_bridge_manifest.py) regenerates both, and the tests fail when they differ from the package.
- Editor-only assembly; it never enters a player build.
- It returns immediately in an asset import worker and in any batch-mode Editor, so a batch-plane run never activates it. No environment variable, argument or setting turns it on.
- In a windowed Editor the static constructor only registers callbacks; all file work happens in the Editor update tick. It never uses `delayCall`.
- At startup it hashes its own package and publishes the digest. GPOS accepts a bridge only when the manifest, the installed files and the running bridge agree, and the running Editor's version is the project's exact `m_EditorVersion`.
- It finds the GPOS root by a bounded walk (16 levels) from its Unity project to the nearest ancestor with `.game/gpos/project-config.json`, never through a symbolic link, and stays dormant when there is none.

## Runtime directory

Non-authoritative state under `.game/gpos-runtime/unity/live/<project key>/`, where the project key is the first 16 hex characters of SHA-256 of `gpos.unity.live`, a NUL, and the Unity project's canonical path relative to the GPOS root (or a dot). Both sides derive it; no request names a directory. Nothing live is written to `.game/gpos/`.

| Path | Written by | Contents |
|---|---|---|
| bridge.json | bridge (atomic replace) | protocol, versions, package digest, boot id, Editor pid and start time, Editor version, GPOS root, project path and key, state (INITIALIZING, READY or CLOSED), attached session and owner |
| heartbeat.json | bridge, four times a second | boot id, sequence, session, phase, focus, compilation and Play Mode flags |
| session.json | bridge | ATTACHED or DETACHED binding |
| requests/ | GPOS (dot-temp file, fsync, `link`) | at most 32 waiting |
| claimed/ | bridge (`rename` from requests/) | exactly one claim per request |
| withdrawn/ | GPOS (`rename` from requests/) | a request that provably never started |
| responses/ | bridge (`link`, never replaced) | exactly one response per request |
| rejected/ | bridge | bad names, links, duplicates |
| events.jsonl | bridge | audit log, rotated at 256 KiB |

The folders keep nothing older than 10 minutes and at most 256 files each.

## Protocol

A request (schema `gpos.unity.live.request/2`; responses `gpos.unity.live.response/2`) has exactly: schema, request id (equal to its file name), session id (only for commands that act on a session), owner (`KIND:ID`), the bridge boot id it is addressed to, one command from a closed list, a fixed argument set, `issued_utc` and `start_deadline_utc` (at most 120 s later). Requests are at most 64 KiB and responses at most 256 KiB; JSON is strict (duplicate keys, trailing data and non-JSON numbers are refused; depth at most 8). Response statuses: OK, REFUSED, FAILED, INTERRUPTED.

```
status  propose-attach  attach-status  abandon-proposal  bind  propose-recovery  recovery-status
consume-recovery  unbind  inspect  enter-playmode  exit-playmode  pause  resume
object-inspect  component-types  properties  create-gameobject  delete-gameobject  set-parent
set-gameobject  set-transform  add-component  remove-component  set-property  save-scene
```

Every argument key of an authoring command is always present; an absent value is `null`.

There is no approval command: approval exists only as a button in the Editor window.

**At most once.** At-most-once applies to one immutable GPOS request within its bound session. The bridge journals every admitted request in the Editor's session state until the request's start deadline has passed, so an exact replay is either still in the journal (refused `DUPLICATE_REQUEST`) or past its deadline (refused `LIVE_REQUEST_EXPIRED`), whatever has happened to old files. The journal's capacity covers everything the rate limit (64 requests per 10 s) can admit within the longest window, so an unexpired entry is never evicted. A request that was claimed and never answered — a Domain Reload or an Editor restart intervened — is answered INTERRUPTED with NOT_REPLAYED and never executed again. The one exception is a Play Mode transition in progress: it is recorded before it is issued, observed until it completes or its deadline passes, and never issued twice. GPOS never retries an unknown mutation. The protocol makes no security claim against same-user malware or code injected into the Editor, which could forge entirely new requests.

**Deadlines and waiting.** A request whose start deadline passed before the bridge could start it is refused `LIVE_REQUEST_EXPIRED` and never runs. When GPOS stops waiting, it tries to rename the request into withdrawn/; exactly one of that rename and the bridge's claim can succeed. A withdrawn request never runs (`LIVE_REQUEST_WITHDRAWN`, `CANCELLED`). If the bridge had already claimed it and no answer arrived, the result is `LIVE_OUTCOME_UNKNOWN` with status `OUTCOME_UNKNOWN` (exit 9): the effect is unknown, `mutation_performed` is true for a mutating capability, nothing is retried, and the caller must re-read `unity.live-status`. The frozen `TIMED_OUT` status is never used for live operations.

**Busy Editor.** Play Mode commands are refused `EDITOR_BUSY` while Unity is compiling, importing, reloading, quitting, in a Play Mode transition or finishing another transition. Scene-authoring commands run only in Edit Mode with nothing pending. Nothing is queued.

## Human approval

Attach order, fixed:

1. GPOS proposes an attach bound to the exact project key, the bridge boot, a new session id and a new proposal id, with an expiry. **No lease is held.**
2. The bridge shows it in its window (menu GPOS ▸ Live Session) and writes one console line. It never opens, focuses or raises the window.
3. The Human approves inside the Editor. The bridge records a one-use grant, valid for 60 s, in the Editor's session state only; nothing survives an Editor restart.
4. GPOS sees the grant and only then takes the SESSION lease. If another writer took the project meanwhile, the attach is a conflict, the grant is abandoned (it can never be used) and nothing is broken.
5. GPOS asks the bridge to bind. The bridge checks the grant (approved, unexpired, this boot, this owner, this project), the session id, and that the GPOS lease file names exactly this session, proposal, owner, boot and project. It consumes the grant, then binds.

A rejection is `LIVE_APPROVAL_REJECTED`; no decision before the deadline is `LIVE_APPROVAL_NOT_GRANTED`, and the proposal is abandoned. The window shows only bridge-generated or validated fields: the kind of request, the GPOS project's folder name, the Unity project's relative path, the requesting owner, short proposal and session ids, and the time left. It says that approving is operational consent only. The approval is never a Human Decision, `HUMAN_EVIDENCE`, a review or a gate verdict, and nothing is recorded in `.game/gpos/`. The Human's approval protects against an agent that only has the IPC files; code running inside the Editor could forge it, which is outside this threat model.

## Sessions and ownership

The SESSION lease binds the adapter, `EDITOR_PROJECT:<resolved GPOS project root>` (the batch plane's resource), the session id, the canonical owner `KIND:ID` built from the request's actor, the proposal id, the bridge boot, the Editor pid and start time, and the creation time. `AGENT:x` and `HUMAN:x` are different owners. Every command that needs a session verifies the session id and owner against the lease (foundation) and the bridge's binding and boot (bridge). There is one session per GPOS project and no sharing.

A SESSION lease blocks every batch writer on the same project before Unity is launched (`LIVE_SESSION_HELD`), and a batch run's lease blocks an attach.

## Status classification

`unity.live-status` reads the bridge's files (bounded, never through a link), the SESSION lease, and the identity of the Editor process the lease names, from a fixed `ps` through the audited process boundary: a process is the same Editor only when its start time matches the recorded one and it runs a Unity Hub Editor.

| Classification | When |
|---|---|
| LIVE | the recorded Editor process is proven alive; the bridge in that boot is READY, fresh and bound to the session |
| UNRESPONSIVE | the process is alive or cannot be proven gone, but the heartbeat is old, the files are unreadable, the bridge is quitting, or the session is still being bound. **Heartbeat age alone is never STALE.** |
| CLOSED | the bridge published CLOSED for that boot and session, and the process is proven gone |
| STALE | proven only: the process is gone without a matching CLOSED, its id now belongs to another process, another bridge boot serves the project, or the bridge in that boot is no longer bound |

## Detach, clean close and recovery

- **Live session, owner:** the bridge unbinds, then the lease is released after verifying the exact session and owner.
- **Clean close (owner, no approval, no lease break):** allowed only when the session id and canonical owner match the lease, the lease's boot is the CLOSED marker's boot and session, the Editor process is proven gone (not merely reused), and the lease has not been replaced. It uses the ordinary verified release.
- **Stale session:** crash, forced termination, a missing or mismatched CLOSED, pid reuse, or any case that cannot be proven clean. It ends only through a separate Human approval, "Recover stale GPOS session", in a running Editor with a trustworthy bridge. The bridge consumes the one-use recovery grant first; GPOS then breaks exactly the inspected lease through the foundation's attributable break operation (who, why, proposal, approving boot, previous holder, in `.game/gpos-runtime/leases/broken.log`). A recovery never attaches: a new attach needs its own approval. With no trustworthy bridge running, nothing is recovered and the Human must intervene outside GPOS. Attach and recovery approvals are separate state machines and separate buttons.

## Play Mode

`unity.live-enter-playmode` SUCCESS means only that the Unity Editor reached its Play Mode state; pause, resume and exit likewise report Editor state transitions. None establishes player-loop, frame, gameplay, physics, rendering or presentation progress, device behaviour or `TARGET_RUNTIME`. The context is `EDITOR`, and no evidence is produced. A transition refused by Unity (for example because scripts do not compile) is `LIVE_TRANSITION_FAILED` and is not retried.

**Background limitation.** A windowed Editor may be heavily throttled while it is not focused. The bridge's IPC keeps working (L7: status round trip median 111 ms unfocused, 56 ms focused), and Play Mode state can be controlled, but gameplay progress is not guaranteed while the Editor is unfocused (L7: no frame advanced in unfocused Play Mode). GPOS changes no Unity preference, never focuses the Editor and never injects input to change this.

## Security review

- Fixed GPOS-owned bridge code with a closed protocol; no listener, socket, HTTP or network anywhere in the bridge or the live plane.
- Everything local runs as the Human's user: any same-user process can write the IPC files. The session id binds requests; it is not a secret.
- GPOS never focuses, moves, closes, kills or restarts the Human's Editor. Its only process inspection is a read-only `ps` of the pid a bridge or lease names.
- No symbolic link is followed in the runtime directory, the installed package or the lease file the bridge reads; every file and folder is bounded.
- Every stale recovery needs a Human's approval in the Editor, and every lease break is recorded.
- User-level Unity state: the bridge never reads or writes Editor preferences. When the Human opens the approval window, Unity itself remembers its size and position in the Editor preferences (five keys named after the window class, `Gpos.LiveBridge.ApprovalWindow` plus h, w, x, y, z), as it does for any Editor window. Opening the project changes the same Unity-owned session keys as any Editor launch.

## Tests

```bash
python3 tests/test_unity_live.py
python3 tests/test_unity_live_bridge_core.py
python3 tests/mutate_unity_live.py
python3 tests/test_unity_authoring.py      # Scene authoring and the bridge upgrade
```

The fast groups drive the GPOS side against [`tests/unity_live_fake_bridge.py`](../tests/unity_live_fake_bridge.py), a Python stand-in for the protocol. The bridge's Unity-free C# core (JSON, protocol, journal, proposals, transitions) is compiled and tested with the Mono bundled with the installed Editor. The real groups open disposable synthetic projects in lab-owned batch-mode Editors. There, the test-only [testkit package](../tests/unity_live_testkit/com.gpos.live-bridge-testkit/Editor/Testkit.cs) starts the production bridge and presses its production approval method for test-named owners; it is never installed by GPOS and gives the production bridge no switch. Windowed behaviour and the real approval button are validated by a Human-attended release-candidate checklist on one disposable synthetic windowed Editor.
