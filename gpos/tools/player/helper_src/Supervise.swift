// GPOS Player Helper — supervise mode. Started detached by player.launch with exactly one GPOS-written request file.
// It launches exactly the validated Player executable with the one fixed `-logFile <runtime>/player.log` argument and a
// narrow environment, owns that child, writes the bounded handshake, waits for commit / abort / stop intent / commit
// deadline / child exit, asks the child to quit through AppKit, SIGKILLs only its own unreaped child after the grace,
// reaps it and writes the bounded exit record. It never retries, relaunches or signals any other process.
import AppKit
import Darwin
import Foundation

let SUPERVISOR_REQUEST = "supervisor-request.json"
let SUPERVISOR_KEYS: Set<String> = ["schema", "session_id", "nonce", "launch_request_id", "application_id", "executable",
                                    "executable_dev", "executable_ino", "commit_deadline_s", "stop_grace_s"]

struct SuperviseRequest {
    let sessionId: String, nonce: String, launchRequestId: String, applicationId: String, executable: String
    let dev: Int, ino: Int, commitDeadline: Int, stopGrace: Int, workspace: String, root: String
}

func parseSuperviseRequest(_ path: String) throws -> SuperviseRequest {
    guard path.hasPrefix("/"), (path as NSString).lastPathComponent == SUPERVISOR_REQUEST else {
        throw Refusal(rule: "REQUEST_INVALID", detail: "not the supervisor request file")
    }
    let ws = (path as NSString).deletingLastPathComponent
    let d = try readObject(path, bound: MAX_REQUEST_BYTES)
    try exactKeys(d, SUPERVISOR_KEYS)
    guard d["schema"] as? String == "gpos.player.supervisor-request/1" else { throw Refusal(rule: "REQUEST_INVALID", detail: "schema") }
    let rid = try string(d, "launch_request_id", REQUEST_ID)
    guard (ws as NSString).lastPathComponent == rid else { throw Refusal(rule: "REQUEST_INVALID", detail: "workspace") }
    let root = try playerWorkspaceRoot(ws, rid: rid)
    let r = SuperviseRequest(
        sessionId: try string(d, "session_id", HEX32), nonce: try string(d, "nonce", HEX32), launchRequestId: rid,
        applicationId: try string(d, "application_id", "[A-Za-z0-9][A-Za-z0-9.-]{0,254}"),
        executable: try string(d, "executable"), dev: try integer(d, "executable_dev", 0...Int.max),
        ino: try integer(d, "executable_ino", 0...Int.max), commitDeadline: try integer(d, "commit_deadline_s", 30...330),
        stopGrace: try integer(d, "stop_grace_s", 1...30), workspace: ws, root: root)
    try checkPlayerExecutable(r)
    return r
}

/// The executable must be the Player of a GPOS-built payload of this same project:
/// `<root>/.game/gpos-runtime/tool-output/unity/<build rid>/payload/Player.app/Contents/MacOS/<CFBundleExecutable>`,
/// canonical, a regular executable file with exactly the expected device and inode, and its bundle's identifier must
/// be the application id.
func checkPlayerExecutable(_ r: SuperviseRequest) throws {
    let base = r.root + "/.game/gpos-runtime/tool-output/unity/"
    let bad = Refusal(rule: "REQUEST_INVALID", detail: "the executable is not a GPOS-built Player of this project")
    guard r.executable.hasPrefix(base) else { throw bad }
    let parts = String(r.executable.dropFirst(base.count)).split(separator: "/", omittingEmptySubsequences: false).map(String.init)
    guard parts.count == 6, matches(parts[0], BUILD_REQUEST_ID), parts[1] == "payload", parts[2] == "Player.app",
          parts[3] == "Contents", parts[4] == "MacOS", !parts[5].isEmpty, parts[5] != ".", parts[5] != ".." else { throw bad }
    guard realPath(r.executable) == r.executable else { throw bad }
    var st = stat()
    guard lstat(r.executable, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG, (st.st_mode & S_IXUSR) != 0,
          Int(st.st_dev) == r.dev, Int(st.st_ino) == r.ino else { throw bad }
    let plistPath = base + parts[0] + "/payload/Player.app/Contents/Info.plist"
    guard let size = regularFileSize(plistPath), size <= 1 << 20, let data = FileManager.default.contents(atPath: plistPath),
          let plist = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
          plist["CFBundleIdentifier"] as? String == r.applicationId, plist["CFBundleExecutable"] as? String == parts[5]
    else { throw bad }
}

func controlRecord(_ path: String, schema: String, _ r: SuperviseRequest, extra: Set<String>) -> [String: Any]? {
    guard exists(path), let d = try? readObject(path, bound: MAX_CONTROL_BYTES),
          Set(d.keys) == Set(["schema", "session_id", "nonce"]).union(extra), d["schema"] as? String == schema,
          d["session_id"] as? String == r.sessionId, d["nonce"] as? String == r.nonce else { return nil }
    return d
}

/// The first valid stop intent, by name: stop-intent-<stop request id>.json naming this session and nonce.
func stopIntent(_ r: SuperviseRequest) -> String? {
    guard let names = try? FileManager.default.contentsOfDirectory(atPath: r.workspace) else { return nil }
    for name in names.sorted() where name.hasPrefix("stop-intent-") && name.hasSuffix(".json") {
        let rid = String(name.dropFirst("stop-intent-".count).dropLast(".json".count))
        guard matches(rid, REQUEST_ID), let d = controlRecord(r.workspace + "/" + name, schema: "gpos.player.stop-intent/1",
                                                              r, extra: ["stop_request_id"]),
              d["stop_request_id"] as? String == rid else { continue }
        return rid
    }
    return nil
}

func runSupervise(_ requestPath: String) -> Int32 {
    let r: SuperviseRequest
    do { r = try parseSuperviseRequest(requestPath) } catch { return 64 }
    let ws = r.workspace
    let handshakePath = ws + "/handshake.json", exitPath = ws + "/exit.json", logPath = ws + "/player.log"
    for name in ["handshake.json", "exit.json", "player.log", "commit.json", "runtime-binding.json"] where exists(ws + "/" + name) {
        return 65   // never adopt or continue an earlier launch's runtime directory
    }
    var exitRecord: [String: Any] = ["schema": "gpos.player.exit/1", "session_id": r.sessionId, "nonce": r.nonce,
                                     "committed": false, "player": NSNull(), "status": "NOT_STARTED", "code": NSNull(),
                                     "signal": NSNull(), "spawn_errno": NSNull(),
                                     "stop": ["reason": NSNull(), "stop_request_id": NSNull(),
                                              "terminate_requested_at": NSNull(), "terminate_accepted": false,
                                              "kill_sent": false]]
    if controlRecord(ws + "/abort.json", schema: "gpos.player.abort/1", r, extra: ["reason"]) != nil {
        exitRecord["stop"] = ["reason": "ABORT", "stop_request_id": NSNull(), "terminate_requested_at": NSNull(),
                              "terminate_accepted": false, "kill_sent": false]
        exitRecord["observed_at"] = utcNow()
        writeOnce(exitPath, exitRecord)
        return 0
    }

    // ---------------------------------------------------------------- the one Player launch
    var fa: posix_spawn_file_actions_t? = nil
    var attr: posix_spawnattr_t? = nil
    posix_spawn_file_actions_init(&fa); posix_spawnattr_init(&attr)
    defer { posix_spawn_file_actions_destroy(&fa); posix_spawnattr_destroy(&attr) }
    posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0)
    posix_spawn_file_actions_addopen(&fa, 1, "/dev/null", O_WRONLY, 0)
    posix_spawn_file_actions_addopen(&fa, 2, "/dev/null", O_WRONLY, 0)
    posix_spawn_file_actions_addchdir_np(&fa, ws)
    var noSignals = sigset_t(), allSignals = sigset_t()
    sigemptyset(&noSignals); sigfillset(&allSignals)
    posix_spawnattr_setsigmask(&attr, &noSignals)
    posix_spawnattr_setsigdefault(&attr, &allSignals)
    posix_spawnattr_setpgroup(&attr, 0)
    posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF
                                          | POSIX_SPAWN_CLOEXEC_DEFAULT))
    let env = ProcessInfo.processInfo.environment
    let envList = ["HOME", "TMPDIR", "LANG"].compactMap { k in env[k].map { "\(k)=\($0)" } } + ["PATH=/usr/bin:/bin:/usr/sbin:/sbin"]
    let argv: [UnsafeMutablePointer<CChar>?] = [strdup(r.executable), strdup("-logFile"), strdup(logPath), nil]
    let envp: [UnsafeMutablePointer<CChar>?] = envList.map { strdup($0) } + [nil]
    var child: pid_t = 0
    let rc = posix_spawn(&child, r.executable, &fa, &attr, argv, envp)
    if rc != 0 {
        exitRecord["spawn_errno"] = Int(rc)
        exitRecord["observed_at"] = utcNow()
        writeOnce(exitPath, exitRecord)
        return 0
    }
    let spawnedAt = uptime()
    let childFacts = procFacts(child)
    exitRecord["status"] = "RUNNING"
    exitRecord["player"] = ["pid": Int(child), "start_sec": Int(childFacts?.startSec ?? 0),
                            "start_usec": Int(childFacts?.startUsec ?? 0)]
    let me = selfIdentity()
    let handshake: [String: Any] = [
        "schema": "gpos.player.handshake/1", "session_id": r.sessionId, "nonce": r.nonce, "spawned_at": utcNow(),
        "supervisor": ["pid": me["pid"]!, "start_sec": me["start_sec"]!, "start_usec": me["start_usec"]!,
                       "executable": me["executable"]!, "cdhash": me["cdhash"]!, "version": HELPER_VERSION],
        "player": childFacts?.json ?? ["pid": Int(child), "ppid": 0, "pgid": 0, "start_sec": 0, "start_usec": 0,
                                       "executable": ""]]
    var stopReason: String? = nil, stopRequest: String? = nil
    if !writeOnce(handshakePath, handshake) { stopReason = "ABORT" }   // no handshake: the launch can never commit

    // ---------------------------------------------------------------- observe until the child is reaped
    var status: Int32 = 0, committed = false, reaped = false
    var stopStartedAt: Double? = nil, terminateRequestedAt: String? = nil, terminateAccepted = false, killSent = false
    var lastTerminateAttempt = -1.0
    while !reaped {
        if waitpid(child, &status, WNOHANG) == child { reaped = true; break }
        if stopReason == nil {
            if !committed {
                if controlRecord(ws + "/commit.json", schema: "gpos.player.commit/1", r, extra: []) != nil {
                    committed = true
                } else if controlRecord(ws + "/abort.json", schema: "gpos.player.abort/1", r, extra: ["reason"]) != nil {
                    stopReason = "ABORT"
                } else if uptime() - spawnedAt > Double(r.commitDeadline) {
                    stopReason = "DEADLINE"
                }
            }
            if committed, let rid = stopIntent(r) { stopReason = "STOP_INTENT"; stopRequest = rid }
        }
        if stopReason != nil {
            let now = uptime()
            if stopStartedAt == nil { stopStartedAt = now; terminateRequestedAt = utcNow() }
            if !terminateAccepted && now - lastTerminateAttempt >= 0.5 {
                lastTerminateAttempt = now
                if let app = NSRunningApplication(processIdentifier: child), app.terminate() { terminateAccepted = true }
            }
            if now - stopStartedAt! >= Double(r.stopGrace) && !killSent {
                kill(child, SIGKILL)   // our own child, not yet reaped: its pid cannot have been reused
                killSent = true
                if waitpid(child, &status, 0) == child { reaped = true; break }
            }
        }
        usleep(100_000)
    }
    let exited = (status & 0x7f) == 0
    exitRecord["committed"] = committed
    exitRecord["status"] = exited ? "EXITED" : "SIGNALED"
    exitRecord["code"] = exited ? Int((status >> 8) & 0xff) : NSNull()
    exitRecord["signal"] = exited ? NSNull() : Int(status & 0x7f)
    exitRecord["stop"] = ["reason": stopReason as Any? ?? NSNull(), "stop_request_id": stopRequest as Any? ?? NSNull(),
                          "terminate_requested_at": terminateRequestedAt as Any? ?? NSNull(),
                          "terminate_accepted": terminateAccepted, "kill_sent": killSent]
    exitRecord["observed_at"] = utcNow()
    writeOnce(exitPath, exitRecord)
    return 0
}
