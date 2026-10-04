// GPOS Player Helper 1.0.0 — the player adapter's one external tool (Phase 2C-8, alpha.22).
//
// Exactly three modes, each given exactly one GPOS-written request file and nothing else:
//
//   supervise <runtime dir>/supervisor-request.json   started detached by player.launch (direct exec)
//   shot      <workspace>/helper-request.json         started by player.capture-screenshot through LaunchServices
//   video     <workspace>/helper-request.json         started by player.capture-video through LaunchServices
//
// There is no permission-request mode, no window-listing mode, no process-listing mode and no terminate mode. The
// helper never compiles, signs, downloads, edits TCC, injects input or captures audio.
import Foundation

let args = CommandLine.arguments
guard args.count == 3 else { exit(64) }
switch args[1] {
case "supervise": exit(runSupervise(args[2]))
case "shot", "video": runCapture(args[1], args[2])
default: exit(64)
}
