// GPOS Player Helper — shared, closed building blocks: strict bounded JSON, write-once files, the fixed GPOS runtime
// path chain and kernel process identity. Nothing here takes a path, a command or a pid from anywhere but the one
// GPOS-written request file a mode was given, and nothing here starts a process.
import Darwin
import Foundation

let HELPER_VERSION = "1.0.0"
let MAX_REQUEST_BYTES = 8192
let MAX_CONTROL_BYTES = 1024

struct Refusal: Error { let rule: String; let detail: String }

@_silgen_name("csops")
func csops(_ pid: pid_t, _ ops: UInt32, _ useraddr: UnsafeMutableRawPointer?, _ usersize: Int) -> Int32
let CS_OPS_CDHASH: UInt32 = 5

// ---------------------------------------------------------------- text and time

func matches(_ text: String, _ pattern: String) -> Bool {
    guard let re = try? NSRegularExpression(pattern: "^(?:" + pattern + ")$") else { return false }
    return re.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
}

let REQUEST_ID = "[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
let BUILD_REQUEST_ID = "[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
let HEX32 = "[0-9a-f]{32}"

func utcNow() -> String {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    f.timeZone = TimeZone(identifier: "UTC")
    return f.string(from: Date())
}

func uptime() -> Double { ProcessInfo.processInfo.systemUptime }

// ---------------------------------------------------------------- files

func isLink(_ path: String) -> Bool {
    var st = stat()
    return lstat(path, &st) == 0 && (st.st_mode & S_IFMT) == S_IFLNK
}

func exists(_ path: String) -> Bool {
    var st = stat()
    return lstat(path, &st) == 0
}

func realPath(_ path: String) -> String? {
    guard let r = realpath(path, nil) else { return nil }
    defer { free(r) }
    return String(cString: r)
}

func regularFileSize(_ path: String) -> Int? {
    var st = stat()
    guard lstat(path, &st) == 0, (st.st_mode & S_IFMT) == S_IFREG else { return nil }
    return Int(st.st_size)
}

/// A strict JSON object read from a regular, non-link file of at most `bound` bytes.
func readObject(_ path: String, bound: Int) throws -> [String: Any] {
    guard let size = regularFileSize(path), size <= bound,
          let data = FileManager.default.contents(atPath: path), data.count <= bound,
          let obj = try? JSONSerialization.jsonObject(with: data), let dict = obj as? [String: Any]
    else { throw Refusal(rule: "REQUEST_INVALID", detail: "\((path as NSString).lastPathComponent) is not a bounded JSON object") }
    return dict
}

func exactKeys(_ d: [String: Any], _ keys: Set<String>) throws {
    guard Set(d.keys) == keys else { throw Refusal(rule: "REQUEST_INVALID", detail: "the request does not hold exactly its keys") }
}

func isBool(_ v: Any?) -> Bool {
    guard let n = v as? NSNumber else { return false }
    return CFGetTypeID(n) == CFBooleanGetTypeID()
}

func string(_ d: [String: Any], _ key: String, _ pattern: String? = nil, max: Int = 4096) throws -> String {
    guard let s = d[key] as? String, !s.isEmpty, s.utf8.count <= max, !s.contains("\u{0}"),
          pattern == nil || matches(s, pattern!) else {
        throw Refusal(rule: "REQUEST_INVALID", detail: "\(key) is missing or malformed")
    }
    return s
}

func integer(_ d: [String: Any], _ key: String, _ range: ClosedRange<Int>) throws -> Int {
    guard let n = d[key] as? NSNumber, !isBool(n), CFNumberIsFloatType(n) == false, range.contains(n.intValue) else {
        throw Refusal(rule: "REQUEST_INVALID", detail: "\(key) is missing or outside \(range)")
    }
    return n.intValue
}

/// Write `obj` once: a temporary sibling created exclusively, fsynced, then hard-linked to the final name (which never
/// replaces an existing file) and removed. Returns false when the final name already exists or anything fails.
@discardableResult
func writeOnce(_ path: String, _ obj: [String: Any]) -> Bool {
    guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) else { return false }
    let tmp = path + ".tmp"
    let fd = open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o644)
    guard fd >= 0 else { return false }
    let ok = data.withUnsafeBytes { buf in write(fd, buf.baseAddress, buf.count) == buf.count }
    fsync(fd); close(fd)
    defer { unlink(tmp) }
    return ok && link(tmp, path) == 0
}

/// `dir` is exactly `<root>/.game/gpos-runtime/tool-output/player/<rid>`: absolute, no link anywhere in the GPOS chain,
/// already canonical. Returns the GPOS root.
func playerWorkspaceRoot(_ dir: String, rid: String) throws -> String {
    let suffix = "/.game/gpos-runtime/tool-output/player/" + rid
    guard dir.hasPrefix("/"), dir.hasSuffix(suffix), matches(rid, REQUEST_ID) else {
        throw Refusal(rule: "REQUEST_INVALID", detail: "the request is not in a GPOS player workspace")
    }
    let root = String(dir.dropLast(suffix.count))
    let chain = [root + "/.game", root + "/.game/gpos-runtime", root + "/.game/gpos-runtime/tool-output",
                 root + "/.game/gpos-runtime/tool-output/player", dir]
    guard !root.isEmpty, !chain.contains(where: isLink), realPath(dir) == dir, realPath(root) == root else {
        throw Refusal(rule: "REQUEST_INVALID", detail: "the player workspace is linked or not canonical")
    }
    return root
}

// ---------------------------------------------------------------- kernel identity

struct ProcFacts {
    let pid: pid_t, ppid: pid_t, pgid: pid_t, startSec: UInt64, startUsec: UInt64, path: String
    var json: [String: Any] {
        ["pid": Int(pid), "ppid": Int(ppid), "pgid": Int(pgid), "start_sec": Int(startSec), "start_usec": Int(startUsec),
         "executable": path]
    }
}

func procFacts(_ pid: pid_t) -> ProcFacts? {
    var info = proc_bsdinfo()
    let size = Int32(MemoryLayout<proc_bsdinfo>.size)
    guard pid > 0, proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return nil }
    var buf = [CChar](repeating: 0, count: 4096)
    guard proc_pidpath(pid, &buf, 4096) > 0 else { return nil }
    return ProcFacts(pid: pid, ppid: pid_t(info.pbi_ppid), pgid: pid_t(info.pbi_pgid), startSec: info.pbi_start_tvsec,
                     startUsec: info.pbi_start_tvusec, path: String(cString: buf))
}

/// The kernel's code-directory hash of this running process, as lower-case hex.
func selfCdhash() -> String? {
    var buf = [UInt8](repeating: 0, count: 20)
    guard csops(getpid(), CS_OPS_CDHASH, &buf, 20) == 0 else { return nil }
    return buf.map { String(format: "%02x", $0) }.joined()
}

func selfIdentity() -> [String: Any] {
    let me = procFacts(getpid())
    return ["version": HELPER_VERSION, "pid": Int(getpid()), "start_sec": Int(me?.startSec ?? 0),
            "start_usec": Int(me?.startUsec ?? 0), "executable": me?.path ?? "", "bundle_path": Bundle.main.bundlePath,
            "cdhash": selfCdhash() ?? ""]
}

/// True only when `pid` is alive, started at exactly that kernel time and runs exactly `executable`.
func proven(_ pid: Int, _ startSec: Int, _ startUsec: Int, _ executable: String) -> Bool {
    guard let f = procFacts(pid_t(pid)) else { return false }
    return Int(f.startSec) == startSec && Int(f.startUsec) == startUsec && f.path == executable
}
