// GPOS Player Helper — shot and video modes. Launched once per capture through LaunchServices so that Screen Recording
// is attributed to this helper, with exactly one GPOS-written request file. Each mode first runs the read-only
// CGPreflightScreenCaptureAccess() and, when it is false, refuses without touching ScreenCaptureKit. It never calls
// CGRequestScreenCaptureAccess and never asks for any other permission. The capture target is the one window of the
// proven Player process: exactly one on-screen, layer-0, visible window owned by the pid, re-found after a fixed
// settle; the filter is that desktop-independent window, never a display, an area or another window. Output is written
// under a partial name and published under its final name only after it validated; a failed capture publishes nothing.
import AppKit
import AVFoundation
import CoreGraphics
import Darwin
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

let HELPER_REQUEST = "helper-request.json"
let HELPER_RESULT = "helper-result.json"
let CAPTURE_KEYS: Set<String> = ["schema", "mode", "request_id", "request_nonce", "session_id", "target", "settle_s",
                                 "max_edge_px", "duration_s", "fps", "max_bytes", "deadline_s"]
let TARGET_KEYS: Set<String> = ["pid", "start_sec", "start_usec", "executable", "application_id"]

struct CaptureRequest {
    let mode: String, requestId: String, nonce: String, sessionId: String
    let pid: Int, startSec: Int, startUsec: Int, executable: String, applicationId: String
    let settle: Int, maxEdge: Int, duration: Int, fps: Int, maxBytes: Int, deadline: Int, workspace: String
}

final class ResultBox {
    private let lock = NSLock()
    private var written = false
    let path: String
    private var base: [String: Any]
    init(path: String, base: [String: Any]) { self.path = path; self.base = base }
    /// Record a result field; the capture task and the watchdog never touch the result concurrently.
    func set(_ key: String, _ value: Any) { lock.lock(); base[key] = value; lock.unlock() }
    /// Write the one result and exit. Whoever comes first (the capture or the watchdog) wins; nothing is written twice.
    func finish(_ outcome: String, _ rule: String?, _ extra: [String: Any] = [:]) -> Never {
        lock.lock()
        if !written {
            written = true
            var r = base
            for (k, v) in extra { r[k] = v }
            r["outcome"] = outcome
            r["rule"] = rule ?? NSNull()
            var timing = (r["timing"] as? [String: Any]) ?? [:]
            timing["finished_at"] = utcNow()
            r["timing"] = timing
            writeOnce(path, r)
        }
        lock.unlock()
        exit(0)
    }
}

func parseCaptureRequest(_ path: String, mode: String) throws -> CaptureRequest {
    guard path.hasPrefix("/"), (path as NSString).lastPathComponent == HELPER_REQUEST else {
        throw Refusal(rule: "REQUEST_INVALID", detail: "not the helper request file")
    }
    let ws = (path as NSString).deletingLastPathComponent
    let d = try readObject(path, bound: MAX_REQUEST_BYTES)
    try exactKeys(d, CAPTURE_KEYS)
    guard d["schema"] as? String == "gpos.player.helper-request/1", d["mode"] as? String == mode.uppercased(),
          let t = d["target"] as? [String: Any], Set(t.keys) == TARGET_KEYS else {
        throw Refusal(rule: "REQUEST_INVALID", detail: "schema, mode or target")
    }
    let rid = try string(d, "request_id", REQUEST_ID)
    guard (ws as NSString).lastPathComponent == rid else { throw Refusal(rule: "REQUEST_INVALID", detail: "workspace") }
    _ = try playerWorkspaceRoot(ws, rid: rid)
    let video = mode == "video"
    guard video ? !(d["duration_s"] is NSNull) : (d["duration_s"] is NSNull && d["fps"] is NSNull) else {
        throw Refusal(rule: "REQUEST_INVALID", detail: "duration/fps do not fit the mode")
    }
    return CaptureRequest(
        mode: mode, requestId: rid, nonce: try string(d, "request_nonce", HEX32), sessionId: try string(d, "session_id", HEX32),
        pid: try integer(t, "pid", 1...Int(Int32.max)), startSec: try integer(t, "start_sec", 1...Int.max),
        startUsec: try integer(t, "start_usec", 0...999_999), executable: try string(t, "executable"),
        applicationId: try string(t, "application_id", "[A-Za-z0-9][A-Za-z0-9.-]{0,254}"),
        settle: try integer(d, "settle_s", 5...5), maxEdge: try integer(d, "max_edge_px", 1920...1920),
        duration: video ? try integer(d, "duration_s", 1...15) : 0, fps: video ? try integer(d, "fps", 30...30) : 0,
        maxBytes: try integer(d, "max_bytes", 1...(64 << 20)), deadline: try integer(d, "deadline_s", 10...60),
        workspace: ws)
}

// ---------------------------------------------------------------- the exact window

struct WindowFacts { let id: CGWindowID; let points: CGSize; let alpha: Double }

/// The pid's on-screen, layer-0, visible windows (CGWindowList: owner, layer, alpha and bounds need no permission).
func eligibleWindows(_ pid: Int) -> [WindowFacts] {
    let list = (CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]) ?? []
    var out: [WindowFacts] = []
    for w in list where (w[kCGWindowOwnerPID as String] as? Int) == pid {
        let layer = w[kCGWindowLayer as String] as? Int ?? -1
        let onscreen = w[kCGWindowIsOnscreen as String] as? Bool ?? false
        let alpha = w[kCGWindowAlpha as String] as? Double ?? 0
        guard layer == 0, onscreen, alpha > 0, let n = w[kCGWindowNumber as String] as? Int,
              let b = w[kCGWindowBounds as String] as? [String: Any],
              let bw = b["Width"] as? Double, let bh = b["Height"] as? Double else { continue }
        out.append(WindowFacts(id: CGWindowID(n), points: CGSize(width: bw, height: bh), alpha: alpha))
    }
    return out
}

/// Exactly one eligible window of the pid that ScreenCaptureKit also lists as owned by that pid.
func exactWindow(_ r: CaptureRequest) async throws -> (SCWindow, Int) {
    let eligible = eligibleWindows(r.pid)
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
    let ids = Set(eligible.map { $0.id })
    let listed = content.windows.filter { ids.contains($0.windowID) }
    guard !eligible.isEmpty, !listed.isEmpty else { throw Refusal(rule: "WINDOW_NOT_FOUND", detail: "no visible window") }
    guard eligible.count == 1, listed.count == 1 else { throw Refusal(rule: "WINDOW_AMBIGUOUS", detail: "\(eligible.count) windows") }
    guard Int(listed[0].owningApplication?.processID ?? -1) == r.pid else {
        throw Refusal(rule: "WINDOW_NOT_OWNED_BY_PID", detail: "owner mismatch")
    }
    return (listed[0], eligible.count)
}

/// Pixel size of the capture: the window's native pixels, scaled down so the longest edge is at most `maxEdge`, never
/// up. H.264 needs even dimensions, so a video rounds each edge down to an even number (the aspect stays within 1 px).
func outputSize(_ filter: SCContentFilter, _ window: SCWindow, maxEdge: Int, even: Bool) -> (Int, Int, Double) {
    let scale = filter.pointPixelScale > 0 ? Double(filter.pointPixelScale) : 1.0
    var points = filter.contentRect.size
    if points.width <= 0 || points.height <= 0 { points = window.frame.size }
    let w = Double(points.width) * scale, h = Double(points.height) * scale
    let f = min(1.0, Double(maxEdge) / max(w, h, 1))   // never upscale
    var ow = max(2, Int((w * f).rounded(.down))), oh = max(2, Int((h * f).rounded(.down)))
    if even { ow -= ow % 2; oh -= oh % 2 }
    return (ow, oh, scale)
}

/// Publish `partial` as `final` without ever replacing a file: a hard link, then the partial name is removed.
func publish(_ partial: String, _ final: String) -> Bool {
    guard link(partial, final) == 0 else { return false }
    unlink(partial)
    return true
}

// ---------------------------------------------------------------- video recording

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let writer: AVAssetWriter, input: AVAssetWriterInput
    private let lock = NSLock()
    var first: CMTime? = nil, frames = 0, dropped = 0, stopped: Error? = nil, finished = false
    init(url: URL, width: Int, height: Int) throws {
        writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: width, AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 8_000_000]])
        input.expectsMediaDataInRealTime = true
        writer.add(input)
    }
    func stream(_ s: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, sb.isValid,
              let att = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let raw = att.first?[.status] as? Int, SCFrameStatus(rawValue: raw) == .complete else { return }
        lock.lock(); defer { lock.unlock() }
        if finished { return }
        if first == nil {
            guard writer.startWriting() else { return }
            writer.startSession(atSourceTime: sb.presentationTimeStamp)
            first = sb.presentationTimeStamp
        }
        if input.isReadyForMoreMediaData && input.append(sb) { frames += 1 } else { dropped += 1 }
    }
    func stream(_ s: SCStream, didStopWithError error: Error) { lock.lock(); stopped = error; lock.unlock() }
    /// No sample is appended after this: the input is marked finished under the same lock the handler takes.
    func finish() { lock.lock(); finished = true; input.markAsFinished(); lock.unlock() }
    var snapshot: (CMTime?, Int, Int, Error?) { lock.lock(); defer { lock.unlock() }; return (first, frames, dropped, stopped) }
}

// ---------------------------------------------------------------- the mode

func runCapture(_ mode: String, _ requestPath: String) -> Never {
    let started = utcNow()
    guard let r = try? parseCaptureRequest(requestPath, mode: mode) else { exit(64) }
    let ws = r.workspace
    let names = mode == "shot" ? ["runtime-screenshot.partial.png", "runtime-screenshot.png"]
                               : ["runtime-video.partial.mp4", "runtime-video.mp4"]
    guard !exists(ws + "/" + HELPER_RESULT), !names.contains(where: { exists(ws + "/" + $0) }) else { exit(65) }
    let box = ResultBox(path: ws + "/" + HELPER_RESULT, base: [
        "schema": "gpos.player.helper-result/1", "mode": mode.uppercased(), "request_id": r.requestId,
        "request_nonce": r.nonce, "session_id": r.sessionId, "sck_called": false, "helper": selfIdentity(),
        "window": NSNull(), "media": NSNull(), "identity": ["before": false, "after": false],
        "timing": ["started_at": started]])
    // The watchdog: a capture that does not finish in time records DEADLINE and ends; nothing is published.
    DispatchQueue.global().asyncAfter(deadline: .now() + .seconds(r.deadline)) { box.finish("FAILED", "DEADLINE") }

    // The window-server connection must exist before capture APIs are used (a bare process asserts otherwise);
    // an NSApplication with the prohibited activation policy shows nothing.
    _ = NSApplication.shared
    NSApp.setActivationPolicy(.prohibited)
    _ = CGMainDisplayID()

    guard CGPreflightScreenCaptureAccess() else { box.finish("REFUSED", "PERMISSION_NOT_GRANTED") }
    guard proven(r.pid, r.startSec, r.startUsec, r.executable) else { box.finish("REFUSED", "TARGET_NOT_PROVEN") }
    box.set("identity", ["before": true, "after": false])

    Task {
        var timing: [String: Any] = ["started_at": started]
        do {
            box.set("sck_called", true)
            let (window, count) = try await exactWindow(r)
            timing["window_seen_at"] = utcNow()
            try await Task.sleep(nanoseconds: UInt64(r.settle) * 1_000_000_000)
            let (again, _) = try await exactWindow(r)
            timing["settle_end_at"] = utcNow()
            guard again.windowID == window.windowID else { throw Refusal(rule: "WINDOW_CHANGED", detail: "another window") }
            guard proven(r.pid, r.startSec, r.startUsec, r.executable) else { throw Refusal(rule: "TARGET_EXITED", detail: "gone") }
            let filter = SCContentFilter(desktopIndependentWindow: again)
            let (width, height, scale) = outputSize(filter, again, maxEdge: r.maxEdge, even: mode == "video")
            box.set("window", ["owner_pid": r.pid, "layer": 0, "on_screen": true, "alpha_positive": true,
                                  "candidates": count, "points_w": Double(again.frame.width),
                                  "points_h": Double(again.frame.height), "scale": scale, "same_after_settle": true])
            let cfg = SCStreamConfiguration()
            cfg.width = width; cfg.height = height; cfg.showsCursor = false; cfg.capturesAudio = false
            timing["capture_start_at"] = utcNow()
            box.set("timing", timing)
            var media: [String: Any]
            if mode == "shot" {
                let img = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
                timing["capture_end_at"] = utcNow()
                let partial = ws + "/" + names[0]
                guard let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: partial) as CFURL,
                                                                 UTType.png.identifier as CFString, 1, nil) else {
                    throw Refusal(rule: "WRITER_FAILED", detail: "png") }
                CGImageDestinationAddImage(dest, img, nil)
                guard CGImageDestinationFinalize(dest) else { throw Refusal(rule: "WRITER_FAILED", detail: "png") }
                guard let bytes = regularFileSize(partial), bytes <= r.maxBytes else { throw Refusal(rule: "OUTPUT_TOO_LARGE", detail: "png") }
                guard img.width == width, img.height == height else { throw Refusal(rule: "OUTPUT_INVALID", detail: "size") }
                media = ["width": img.width, "height": img.height, "bytes": bytes, "format": "PNG"]
            } else {
                cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(r.fps)); cfg.queueDepth = 5
                let partial = ws + "/" + names[0]
                let rec = try Recorder(url: URL(fileURLWithPath: partial), width: width, height: height)
                let stream = SCStream(filter: filter, configuration: cfg, delegate: rec)
                try stream.addStreamOutput(rec, type: .screen, sampleHandlerQueue: DispatchQueue(label: "gpos.frames"))
                try await stream.startCapture()
                var waited = 0
                while rec.snapshot.0 == nil && rec.snapshot.3 == nil && waited < 50 {   // the first frame, at most 5 s
                    try await Task.sleep(nanoseconds: 100_000_000); waited += 1
                }
                guard let first = rec.snapshot.0 else { throw Refusal(rule: "STREAM_STOPPED", detail: "no frame") }
                try await Task.sleep(nanoseconds: UInt64(r.duration) * 1_000_000_000)
                if rec.snapshot.3 != nil { throw Refusal(rule: "STREAM_STOPPED", detail: "the stream stopped") }
                try? await stream.stopCapture()
                timing["capture_end_at"] = utcNow()
                let (_, frames, dropped, stopped) = rec.snapshot
                if stopped != nil { throw Refusal(rule: "STREAM_STOPPED", detail: "the stream stopped") }
                rec.finish()
                rec.writer.endSession(atSourceTime: CMTimeAdd(first, CMTime(value: CMTimeValue(r.duration), timescale: 1)))
                await rec.writer.finishWriting()
                guard rec.writer.status == .completed else { throw Refusal(rule: "WRITER_FAILED", detail: "mp4") }
                guard let bytes = regularFileSize(partial), bytes <= r.maxBytes else { throw Refusal(rule: "OUTPUT_TOO_LARGE", detail: "mp4") }
                let asset = AVURLAsset(url: URL(fileURLWithPath: partial))
                let videoTracks = try await asset.loadTracks(withMediaType: .video)
                let audioTracks = try await asset.loadTracks(withMediaType: .audio)
                let seconds = CMTimeGetSeconds(try await asset.load(.duration))
                let size = videoTracks.count == 1 ? try await videoTracks[0].load(.naturalSize) : .zero
                guard videoTracks.count == 1, audioTracks.isEmpty, seconds >= Double(r.duration) - 0.5,
                      seconds <= Double(r.duration) + 0.75, Int(size.width) == width, Int(size.height) == height,
                      frames >= r.duration else { throw Refusal(rule: "OUTPUT_INVALID", detail: "mp4 checks") }
                media = ["width": width, "height": height, "bytes": bytes, "format": "MP4", "codec": "avc1",
                         "frames": frames, "dropped": dropped, "duration_s": seconds, "fps_cap": r.fps,
                         "video_tracks": videoTracks.count, "audio_tracks": audioTracks.count]
            }
            guard proven(r.pid, r.startSec, r.startUsec, r.executable) else { throw Refusal(rule: "TARGET_EXITED", detail: "gone") }
            guard publish(ws + "/" + names[0], ws + "/" + names[1]) else { throw Refusal(rule: "WRITER_FAILED", detail: "publish") }
            box.set("timing", timing)
            box.finish("CAPTURED", nil, ["media": media, "identity": ["before": true, "after": true]])
        } catch let refusal as Refusal {
            box.set("timing", timing)
            let refused = ["WINDOW_NOT_FOUND", "WINDOW_AMBIGUOUS", "WINDOW_NOT_OWNED_BY_PID"].contains(refusal.rule)
            box.finish(refused ? "REFUSED" : "FAILED", refusal.rule)
        } catch {
            box.set("timing", timing)
            box.finish("FAILED", "CAPTURE_ERROR")
        }
    }
    dispatchMain()
}
