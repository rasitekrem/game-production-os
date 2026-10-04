// TEST ONLY (alpha.22 privacy controls): an unrelated application that puts one solid green, borderless, floating window
// exactly over the on-screen layer-0 window of one pid for N seconds. It is also the "unrelated running application"
// of the foreign-instance tests. Never part of GPOS.
//   occluder <pid> <seconds>     (pid 0: a small window covering nothing — an unrelated running app)
import AppKit
let a = CommandLine.arguments
guard a.count == 3, let pid = Int(a[1]), let secs = Double(a[2]) else { exit(64) }
let list = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]) ?? []
var rect: (Double, Double, Double, Double) = (0, 0, 40, 40)   // pid 0: a small window of its own, covering nothing
if pid != 0 {
    guard let target = list.first(where: { ($0[kCGWindowOwnerPID as String] as? Int) == pid && ($0[kCGWindowLayer as String] as? Int) == 0
                                         && (($0[kCGWindowAlpha as String] as? Double) ?? 0) > 0 }),
          let b = target[kCGWindowBounds as String] as? [String: Double] else { print("{\"covered\":false}"); exit(2) }
    rect = (b["X"] ?? 0, b["Y"] ?? 0, b["Width"] ?? 0, b["Height"] ?? 0)
}
let (x, y, w, h) = rect
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let screenH = NSScreen.screens[0].frame.height
let win = NSWindow(contentRect: NSRect(x: x, y: screenH - y - h, width: w, height: h), styleMask: [.borderless],
                   backing: .buffered, defer: false)
win.backgroundColor = NSColor(calibratedRed: 0, green: 1, blue: 0, alpha: 1)
win.level = .floating
win.orderFrontRegardless()
print("{\"covered\":true,\"pid\":\(ProcessInfo.processInfo.processIdentifier),\"x\":\(x),\"y\":\(y),\"w\":\(w),\"h\":\(h)}")
fflush(stdout)
DispatchQueue.main.asyncAfter(deadline: .now() + secs) { exit(0) }
app.run()
