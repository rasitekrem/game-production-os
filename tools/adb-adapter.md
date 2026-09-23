# Android Debug Bridge evidence adapter (Phase 2C-3)

Code: [`gpos/tools/adb/`](../gpos/tools/adb/__init__.py) · adapter id `adb` · status: the first production target-device adapter, built on the frozen [tool adapter foundation](adapter-foundation.md) (`v1.0.0-alpha.12`) without changing it.

The adapter answers three questions about one explicitly named Android target:

1. which exact target produced this evidence (a device report);
2. what the target displayed at one instant (a screenshot);
3. what memory state one explicitly named, running package had at one instant (a meminfo snapshot).

**No production capability changes Android target state.** There is no install, uninstall, launch, force-stop, clear, input, settings or permission change, file transfer, reboot, root or remount, screen recording, logcat streaming, bug report, Perfetto trace, shell runner, generic dumpsys or wireless pairing. Each of those needs its own Human Review.

## Identity

| Field | Value |
|---|---|
| `adapter_id` | `adb` |
| `target_tool` | Android Debug Bridge |
| `tool_family` | `DEVICE` |
| `adapter_kind` | `CLI` |
| `state_model` | `STATELESS` |
| platforms | `WINDOWS`, `MACOS`, `LINUX` (the host running adb) |
| network | `FORBIDDEN`; no wireless ADB |
| minimum version | none (see below) |
| TEST_ONLY | no |

`default_registry()` now contains exactly `adb`, `ffmpeg`, `ffprobe` and `git`. The adapter is not an agent adapter, and it is not in the registry's `adapter_ids`.

**Why `STATELESS`.** The adapter owns no target session and changes no target state. The capabilities are `MUTATING` only because each writes one evidence file into its execution's host workspace. No single-writer lease is needed.

## Verified ADB behaviour

The first-party documentation was consulted on 2026-09-23:

| Document | What it establishes |
|---|---|
| [Android Debug Bridge](https://developer.android.com/tools/adb) | The client, daemon and server model. "When you start an adb client, the client first checks whether there is an adb server process already running. If there isn't, it starts the server process." Device states: `device` "does not imply that the Android system is fully booted and operational, because the device connects to adb while the system is still booting". `-s` selects a serial, and "`-s` overrides `$ANDROID_SERIAL`". `-d` and `-e` select automatically. A single shell command runs as `adb shell <command>`. The documented screenshot form is `adb exec-out screencap -p > screen.png` ("use 'exec-out' instead of 'shell' to get raw data"). Wireless targets appear as `device_ip_address:5555`. |
| [dumpsys](https://developer.android.com/tools/dumpsys) | `adb shell dumpsys meminfo [-d] package_name\|pid` records "a snapshot of how your app's memory is divided". "Some details of the output differ across platform versions." |
| [SDK Platform-Tools release notes](https://developer.android.com/tools/releases/platform-tools) | adb and fastboot are "backward compatible, so you should only need the latest version". The mDNS backend history (37.0.0: `libadbmdns` is the default). |
| [AOSP adb manual](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/user/adb.1.md) and [adb_mdns.cpp](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/adb_mdns.cpp) | `adb get-state` prints "offline \| bootloader \| device". The variable ADB_MDNS_AUTO_CONNECT is a "comma-separated list of mdns services to allow auto-connect (default adb-tls-connect)"; in the source, the value `0` disables all auto-connecting. |

The installed tool's own help (`adb help`) lists the same environment variables, the `adb get-state` states, and `-d` as "use USB device (error if multiple devices connected)".

The first-party pages do not document the `-s` summary flag of `dumpsys meminfo`. It was verified on every test target, along with each other command shape.

**Real runtime.** Android SDK Platform-Tools **37.0.0-14910828** (protocol 1.0.41), on macOS. The authorized test matrix was 2 physical Android devices plus 2 Android emulators, across API 31 and API 35.

Observed on the real targets, and handled in code:

| Observation | Consequence |
|---|---|
| `getprop` prints some values over several lines (a boot-reason history, on every target) | the parser joins a record until its closing `]`; a stray line or a duplicate name still fails closed |
| `dumpsys meminfo -s <package not running>` **exits 0** with `No process found for: <package>` | "not running" is read from the text; it becomes a conflict, never a snapshot and never zeros |
| a remote `adb shell` command line is joined and run by the device's shell: `com.android.systemui;id` would also run `id` | the package name is a strict application-id token; nothing else from a caller reaches a target command |
| an unknown serial: `adb get-state` exits 1 with `error: device '…' not found` | `TARGET_DEVICE_UNAVAILABLE`; no other target is used |
| a busy emulator sometimes answers `dumpsys meminfo -s` after about 6 s with the header only, without memory figures (2 of 15 calls on one emulator; never on the physical devices) | refused as "no memory totals": not a usable snapshot, never evidence. The adapter does not retry on its own; the real-target test matrix retries the whole execution up to four times and reports the count |
| a screenshot stream is a complete PNG ending in IEND (0.13–0.45 MiB at up to 1600×2560) | a stream without IEND, or one over the capture bound, is refused; no partial file is written |

## Device selection

Every capability requires:

- `request.target_platform` = `ANDROID`. It is caller-owned provenance and is never inferred, even though the adapter is for Android. Missing or anything else is `INVALID_REQUEST`.
- `request.device` = the **exact ADB serial**. It is the target selector and the provenance device, one value used unchanged. Missing is `INVALID_REQUEST`.

Every target command binds that serial with `-s <serial>`. There is no `-d`, no `-e`, no first-device fallback, and no "if one device exists, use it". The ANDROID_SERIAL variable never reaches adb, because the foundation inherits only its environment allowlist; a test proves a set value is ignored. The CLI uses the existing `--device` and `--target-platform` options. There is no separate serial option.

The serial grammar is narrow on purpose: letters, digits, `-` and `_`, starting with a letter or digit, at most 64 characters. It accepts USB serials and emulator serials such as `emulator-5554`. It refuses:
- `:` — TCP/IP `host:port` targets and `usb:` device paths;
- `.` — IP addresses and mDNS service names such as `adb-…._adb-tls-connect._tcp`;
- whitespace, quotes, slashes, shell syntax and a leading `-`.

## No wireless ADB

- No authorized command is `connect`, `disconnect`, `pair`, `tcpip`, `mdns`, `forward` or `reverse`.
- Network serials are refused by name.
- The adapter sets ADB_MDNS_AUTO_CONNECT to `0` for every adb process it starts. If that client starts the ADB server, the server never auto-connects to wireless targets it discovers.
- An ADB server that was already running keeps the configuration it was started with. The adapter cannot change it and does not claim to; the serial rule still means such a server's wireless targets are never used.

## Authorized command surface

This is not an adb command runner. The complete surface:

| Purpose | Command |
|---|---|
| probe | `adb version` |
| connection state | `adb -s <serial> get-state` |
| boot completion | `adb -s <serial> shell getprop sys.boot_completed` |
| device report | `adb -s <serial> shell getprop` |
| screenshot | `adb -s <serial> exec-out screencap -p` |
| meminfo | `adb -s <serial> shell dumpsys meminfo -s <package>` |

The only variable slots are the validated serial and, for meminfo, the validated package name. There is no caller argv, shell string, dumpsys service, device path, redirection or executable. The executable is the one the probe resolved:
- `shutil.which("adb")` looks it up;
- a match found only through a relative PATH entry is refused;
- the match is resolved to an absolute path.

The adapter never imports the subprocess module; every process goes through the audited boundary.

## Probe

The probe runs `adb version` only. It never runs `adb devices`: which targets are attached is execution-time state, and a device query talks to the ADB server.

The version is taken only from the exact two-line form, `Android Debug Bridge version <x.y.z>` followed by `Version <platform-tools version>`. The Platform-Tools version (for example `37.0.0-14910828`) is the tool version recorded in provenance. Anything else is `VERSION_UNSUPPORTED` (execution: `INCOMPATIBLE`); a version is never guessed from the protocol line alone.

No minimum version is set. Android documents Platform-Tools as backward compatible, and every command used was verified on the installed release.

## Target readiness

Before any capture, and never in a dry run:

1. `adb get-state` must print exactly `device`:
   - an unknown serial is `TARGET_DEVICE_UNAVAILABLE` (`UNAVAILABLE`);
   - `offline`, `bootloader`, unauthorized or anything else is `TARGET_DEVICE_NOT_READY` (`CONFLICT`).
2. `getprop sys.boot_completed` must print exactly `1`. Otherwise `TARGET_DEVICE_NOT_READY`: the documented `device` state alone does not mean Android has finished booting.

All the commands of one execution share its timeout as a single deadline.

## Capabilities

| Capability | Category | Observes | Inputs | Writes | Evidence |
|---|---|---|---|---|---|
| `adb.capture-device-report` | `CAPTURE` | `TARGET_RUNTIME` | — | `device.json`, `JSON`, `application/json` | `DEVICE_EVIDENCE` |
| `adb.capture-screenshot` | `CAPTURE` | `TARGET_RUNTIME` | — | `screenshot.png`, `IMAGE`, `image/png` | `VISUAL_EVIDENCE` |
| `adb.capture-meminfo` | `PROFILE` | `PERFORMANCE_RUNTIME` | `package_name` | `meminfo.txt`, `REPORT`, `text/plain` | `PERFORMANCE_EVIDENCE` |

All three are `MUTATING` (host workspace only; explicit `allow_mutation` consent, else `MUTATION_NOT_ALLOWED` with nothing started), `STATELESS`, support dry run, take no lease, and consume no input artifact.

### Device report

One `getprop` capture, of which only these properties are read:

| Key | Property |
|---|---|
| `manufacturer` | `ro.product.manufacturer` |
| `model` | `ro.product.model` |
| `device_codename` | `ro.product.device` |
| `primary_abi` | `ro.product.cpu.abi` |
| `android_release` | `ro.build.version.release` |
| `api_level` | `ro.build.version.sdk`, as an integer |
| `security_patch` | `ro.build.version.security_patch` |
| `build_fingerprint` | `ro.build.fingerprint` |
| `boot_completed` | `sys.boot_completed`, which must be `1`, reported as `true` |

- Every property is required. Every value must be printable UTF-8 text of at most 256 characters with no surrounding whitespace. The API level must be an integer from 1 to 1000, and the security patch a `YYYY-MM-DD` date.
- Nothing else is ever copied, whatever the target prints: no serial number, IMEI, Android ID, network address, Wi-Fi, account, user or installed-app data.
- The file is canonical JSON with sorted keys in UTF-8, and the same object is the result's data.
- The ADB serial is not repeated inside the file: provenance already binds the evidence to it.

### Screenshot

- `exec-out screencap -p` streams the PNG straight to the host. No file is created on the device, and there is no pull or rm.
- The stream is read from the private raw capture, bounded at 32 MiB.
- It must be a complete PNG: signature, a well-formed IHDR, dimensions from 1 to 16384, and IEND as the final chunk.
- The adapter writes the file only after that check, never over an existing file. A truncated or invalid stream is `FAILED`, with no partial file and no evidence.
- The image is never decoded, rendered, OCR-ed or inspected, and the tests check structure only.
- A screenshot offers `VISUAL_EVIDENCE` only, never `DEVICE_EVIDENCE`.

### Meminfo

- `package_name` must be an Android application id: two or more dot-separated segments of letters, digits and `_`, each starting with a letter, at most 255 characters. There is no `:` process suffix, whitespace, quote, slash, backslash, shell metacharacter or leading `-`.
- `dumpsys meminfo -s <package>` output, bounded at 2 MiB, must be:
  - printable ASCII text (line endings are normalized to `\n`);
  - about exactly one process, whose `** MEMINFO in pid N [<package>] **` header names the requested package;
  - including its memory totals.
- Another process, several processes with that name, no totals, binary output or truncated output is `FAILED`. A package with no running process is `TARGET_PROCESS_NOT_RUNNING` (`CONFLICT`). None of them offers evidence, and no zero metrics are ever invented.
- The body is kept as the report, not reinterpreted, because Android documents that its details vary between versions.
- The adapter never launches, stops or resets anything.

## Evidence

| Capture | Evidence | Capture context |
|---|---|---|
| device report | `DEVICE_EVIDENCE` | `TARGET_RUNTIME` |
| screenshot | `VISUAL_EVIDENCE` | `TARGET_RUNTIME` |
| meminfo | `PERFORMANCE_EVIDENCE` | `PERFORMANCE_RUNTIME` |

These are direct target captures, not derived media, so each candidate's context is the capability's own execution context. The foundation validates it against the frozen registry, which was not changed.

Every candidate states its limits:

| Capture | Limitations |
|---|---|
| device report | Identifies the target, build and boot state. Does not prove an app is installed, running or correct. Does not by itself prove performance. |
| screenshot | One display instant. Not motion, input latency or audio. Content as displayed, never inspected. |
| meminfo | A point-in-time memory snapshot of the named package. Details vary across platform versions. No CPU, GPU, thermals, frame pacing, FPS or sustained performance. Not a controlled benchmark. |

### Materialization

With a subject revision, and the explicitly supplied `build_revision` (or `build_id`) the evidence schema requires for runtime contexts, each candidate materializes to a schema-valid record. Its provenance carries:

- the capture context;
- `subject_revision` and `build_revision`;
- `target_platform` = `ANDROID`;
- `device` = the exact serial;
- the adb Platform-Tools `tool_version`;
- the artifact hash.

The evidence schema also requires an **instrumentation** declaration for `PERFORMANCE_EVIDENCE` and `PERFORMANCE_RUNTIME`. The frozen foundation leaves this to the caller, because an execution cannot observe it.
- The meminfo result offers a suggested declaration in its data: present, described as an on-demand `dumpsys meminfo -s` through ADB, timing impact `UNKNOWN`.
- The caller passes it explicitly: `materialize(..., extra_provenance={"instrumentation": ...})`.
- `UNKNOWN` is the honest value: the adapter did not measure the effect of the dump. Under the evidence rules such evidence never counts toward a timing `PASS`.

No gate status, verdict, reviewer or Human Review is created.

### Explicit Git handoff

No ADB capability calls Git. The alpha.12 CLI path carries a revision explicitly:

```bash
python3 -m gpos.tools execute --adapter git --capability git.resolve-provenance \
    --project P --subject-ref FEATURE-X --format json
python3 -m gpos.tools execute --adapter adb --capability adb.capture-device-report \
    --project P --subject-ref FEATURE-X --subject-revision <SHA> --build-revision <SHA> \
    --target-platform ANDROID --device <SERIAL> --allow-mutation --format json
```

- The first command's JSON result contains the `repository_revision` used as `<SHA>` in the second.
- Provenance and the candidate record that exact `build_revision`.
- Without the option, `build_revision` stays unknown.

## Dry run and output collisions

A dry run validates the target platform, the serial's syntax, the package name, the evidence declaration and output collisions. It then returns a plan:

```
would check the connection state and boot completion of ADB target <serial>
would capture device.json (JSON) from that target and offer DEVICE_EVIDENCE (TARGET_RUNTIME)
no ADB target command, workspace or file is created by a dry run; target availability is not checked
```

It sends **no target-directed command**, creates no workspace or file, and offers no evidence. It never claims that a target is connected, booted or running an app. Only the probe's `adb version` may run.

An existing `device.json`, `screenshot.png` or `meminfo.txt`, or a symlink at that path (for example in a caller-named output directory), is refused in both the dry run and the real run, before any target command. The file is left untouched. The real write also uses exclusive creation, so an existing file is never overwritten.

## Privacy

- Target output (properties, pixels, memory text) is parsed from the private raw capture, or copied into the evidence file. Public `stdout` and `stderr` are empty, and the raw capture is blanked before the process outcome reaches the result. Byte counts, exit codes and timing are kept.
- The recorded command contains the serial, which is already caller-supplied device provenance.
- The device report reads nine allowlisted properties and nothing else.
- The tests never print a physical serial or a build fingerprint:
  - targets are labelled USB-1, EMU-1 and so on;
  - the runner filters its own output;
  - a test scans the repository for every physical serial and fingerprint seen at runtime.

## ADB server trust boundary

ADB is client/server software, and an adb client command may start the host ADB server if none is running. The adapter:
- never runs `adb start-server` or `adb kill-server`;
- never configures the server beyond the fixed auto-connect override on processes it starts;
- does not treat server state as project or device authority.

The adapter does **not** claim that no host process state ever changes. A server started by the adapter's first command keeps running afterwards, as ADB always does.

It also does not claim OS or device sandboxing. What it guarantees is narrower: production capabilities never intentionally change target Android state. Each of their commands is one of the read-only templates above.

## Results and diagnostics

| Situation | Status | Code |
|---|---|---|
| captured | `SUCCESS` | — |
| no or bad `target_platform`, device, serial or package; network serial; output exists; input artifact supplied | `INVALID_REQUEST` | `INVALID_TOOL_REQUEST` |
| capture without consent | `INVALID_REQUEST` | `MUTATION_NOT_ALLOWED` |
| serial not connected | `UNAVAILABLE` | `TARGET_DEVICE_UNAVAILABLE` |
| offline, unauthorized, bootloader or still booting | `CONFLICT` | `TARGET_DEVICE_NOT_READY` |
| package not running | `CONFLICT` | `TARGET_PROCESS_NOT_RUNNING` |
| truncated, malformed or incomplete output | `FAILED` | `EXECUTION_FAILED` |
| timeout | `TIMED_OUT` | `EXECUTION_TIMEOUT` |
| adb missing, or only on a relative PATH entry | `UNAVAILABLE` | `TOOL_NOT_FOUND` |
| unrecognized `adb version` | `INCOMPATIBLE` | `TOOL_VERSION_UNSUPPORTED` |

The three target codes are generic to device adapters, not specific to ADB.

## Security review

| Concern | Finding |
|---|---|
| subprocess outside the boundary / host shell | none: argument vectors through the audited boundary only |
| arbitrary remote shell, shell metacharacters | none: two fixed shell commands (`getprop`, `dumpsys meminfo -s`); the only variable is a strict application id |
| arbitrary dumpsys | none: `meminfo -s` only |
| install, uninstall, push, pull, reboot, root, settings, app lifecycle | none authorized; tested at the template, source-constant and runtime-argv level |
| wireless ADB | none: network serials refused, no connect/pair/tcpip, auto-connect disabled for servers the adapter starts |
| target auto-selection / serial ambiguity | none: the exact serial is required and bound with `-s`; ANDROID_SERIAL, `-d` and `-e` are never used |
| target output leak | none: raw capture parsed or copied into the evidence file, then blanked |
| screenshot content | never decoded or inspected; tests check structure only |
| hardware identifiers in the repository | none: runtime scan for physical serials and fingerprints |
| raw getprop leak | none: nine allowlisted properties only |
| performance overclaim | none: a memory snapshot only, with explicit limitations; instrumentation declared `UNKNOWN` |
| fabricated target platform, device id or build revision | none: all three come from the request, unchanged, or stay unknown |

## Limitations

- Local USB and emulator targets only; wireless ADB is out of scope.
- A pre-existing ADB server keeps its own configuration, including mDNS.
- The adb client uses the default server port. A server started by the adapter lacks any vendor keys the user set only through the environment, so the adapter does not inherit ADB_VENDOR_KEYS or ANDROID_ADB_SERVER_PORT.
- Meminfo requires the package's process to be running already; the adapter never launches it.
- The device serial is recorded in provenance, as the brief requires, so a materialized record identifies the physical device.
- The frozen Phase-2A validator counts `DEVICE_EVIDENCE` toward a platform that lists `reference_devices` only when the evidence's `device` **exactly** equals a listed identifier. Because this adapter records the ADB serial, a project that lists reference devices by model name gets `REFERENCE_DEVICE_MISMATCH` for ADB device evidence. For it to count, the project config must list the exact serial, which puts a hardware identifier into project records. The evidence schema describes `device` as "Device model and OS version". This design tension is left to Human Review; the adapter does not guess a second identity.
- Real-runtime tests ran on macOS hosts only (Windows and Linux hosts were not exercised), against 2 physical devices (API 31) and 2 emulators (API 35).

## Tests

```bash
GPOS_TEST_ANDROID_SERIALS=<serial>[,<serial>...] python3 tests/test_adb_adapter.py
GPOS_TEST_ANDROID_SERIALS=<serial>[,<serial>...] python3 tests/mutate_adb_adapter.py
```

GPOS_TEST_ANDROID_SERIALS is test-only and names the targets a human authorized. Without it, the suite uses the single eligible target, or stops with ADB_TARGET_UNAVAILABLE_FOR_PHASE2C3 or ADB_TARGET_SELECTION_REQUIRED_FOR_PHASE2C3. It never falls back to mocks. Stand-in programs cover only deterministic error cases: a missing or unrecognizable adb, unauthorized, offline or booting targets, truncated or malformed output, and a timeout.

Groups A–Z cover:

- registration and the real probe;
- target selection and readiness;
- the device report and its privacy;
- screenshot and truncation;
- meminfo, package validation and a package that is not running;
- contexts and materialization;
- the explicit Git handoff through the CLI;
- mutation consent, dry run and output collision;
- target immutability, the command surface, network and wireless;
- public output, the parsers and performance limitations;
- the CLI, repository privacy and the other adapters.
