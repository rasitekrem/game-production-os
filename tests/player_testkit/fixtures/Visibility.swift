// TEST ONLY (alpha.22): hide or unhide one running application by pid, as a person would with Cmd-H (AppKit; no
// Accessibility or Automation). Never part of GPOS.
//   visibility <pid> hide|unhide
import AppKit
let a = CommandLine.arguments
guard a.count == 3, let pid = Int32(a[1]), let app = NSRunningApplication(processIdentifier: pid) else { exit(2) }
print(a[2] == "hide" ? app.hide() : app.unhide())
