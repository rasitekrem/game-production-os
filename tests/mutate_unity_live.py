#!/usr/bin/env python3
"""Bounded mutation harness for the Unity live plane (Phase 2C-6A).

    python3 tests/mutate_unity_live.py [--jobs N] [--only TEXT]

Each mutation breaks exactly one guarantee in a temporary copy of the repository and runs one suite there:

    "live"  tests/test_unity_live.py with GPOS_UNITY_TEST_FAST=1 (fake bridge; no Unity process)
    "core"  tests/test_unity_live_bridge_core.py (the bridge's C# core, compiled with Unity's bundled Mono)

A mutation of the bridge package regenerates its manifest in the copy first, so it can only be caught by a test
of what the code does, never by the manifest noticing that bytes changed. A mutation must make its suite fail
("CAUGHT"); one that leaves it green is "MISSED" and fails this harness. An anchor that does not match exactly
once is "NOT APPLIED" and also fails. The repository itself is never modified.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE, IPC, STATUS = "gpos/tools/unity/live.py", "gpos/tools/unity/live_ipc.py", "gpos/tools/unity/live_status.py"
IDENT, INSTALL, ADAPTER = "gpos/tools/unity/identity.py", "gpos/tools/unity/bridge_install.py", "gpos/tools/unity/adapter.py"
BRIDGE = "gpos/tools/unity/live_bridge/com.gpos.live-bridge/Editor/"
CORE = BRIDGE + "Core/"

MUTATIONS = [
    # --- attach and Human approval (GPOS side)
    ("attach proceeds without a Human approval", "live", [
        (LIVE, '    state = _await_decision(live, channel, "attach-status", proposal, boot, deadline)\n',
         '    state = "APPROVED"\n')]),
    ("a rejected attach is treated as approved", "live", [
        (LIVE, '    state = _await_decision(live, channel, "attach-status", proposal, boot, deadline)\n'
               '    if state != "APPROVED":\n',
         '    state = _await_decision(live, channel, "attach-status", proposal, boot, deadline)\n'
         '    if state not in ("APPROVED", "REJECTED"):\n')]),
    ("an unapproved proposal is left usable", "live", [
        (LIVE, '    if state != "APPROVED":\n        _abandon(live, channel, proposal, boot)\n        if state == "REJECTED":',
         '    if state != "APPROVED":\n        if state == "REJECTED":')]),
    ("the session is bound although another writer took the lease", "live", [
        (LIVE, '    if problems:\n        _abandon(live, channel, proposal, boot)\n        return AdapterOutcome(ok=True, diagnostics=tuple(problems))\n'
               '    r = live.call(channel, "bind"',
         '    r = live.call(channel, "bind"')]),
    ("an unknown bind releases the lease of a possibly bound Editor", "live", [
        (LIVE, "        live.context.sessions.confirm()   # never leave a possibly bound Editor without its lease\n", "")]),
    ("a bind refusal keeps the lease", "live", [
        (LIVE, '    if r.status != "OK":\n        _abandon(live, channel, proposal, boot)\n        return _mapped(cap, r, "binding the session", False, data=data)\n',
         '    if r.status != "OK":\n        live.context.sessions.confirm()\n        return _mapped(cap, r, "binding the session", False, data=data)\n')]),
    ("an existing session does not stop an attach", "live", [
        (LIVE, "    holder = live.holder()\n    if holder:\n        if lease_mod.scope_of(holder) == lease_mod.SESSION:\n            cls, reasons, _, _ = live.classify(holder)",
         "    holder = live.holder()\n    if False:\n        if lease_mod.scope_of(holder) == lease_mod.SESSION:\n            cls, reasons, _, _ = live.classify(holder)")]),
    # --- bridge trust
    ("the running bridge's digest is not compared", "live", [
        (LIVE, '        if (b.get("protocol"), b.get("bridge_version"), b.get("package_digest")) != \\\n                (bi.PROTOCOL, bi.BRIDGE_VERSION, manifest["package_digest"]):',
         '        if (b.get("protocol"), b.get("bridge_version")) != \\\n                (bi.PROTOCOL, bi.BRIDGE_VERSION):')]),
    ("the Editor version is not compared", "live", [
        (LIVE, '        if b.get("editor_version") != self.summary["editor_version"]:', "        if False:")]),
    ("bridge project identity is not checked", "live", [
        (LIVE, '        if not (ident.same_directory(b.get("gpos_root"), self.root) and\n                ident.same_directory(b.get("project_path"), self.project) and b.get("project_key") == self.key):',
         "        if False:")]),
    ("a stale heartbeat is accepted", "live", [
        (LIVE, '        if age is None or age > ls.FRESH_SECONDS or beat.get("boot_id") != b.get("boot_id"):',
         '        if beat.get("boot_id") != b.get("boot_id"):')]),
    ("the bridge's Editor process identity is not required", "live", [
        (LIVE, '        if self.editor(b.get("editor_pid"), b.get("editor_started_utc")) != ls.ALIVE:', "        if False:")]),
    ("the static package check is skipped before an attach", "live", [
        (LIVE, "    cap = live.cap\n    manifest = live.require_installed()\n    holder = live.holder()",
         "    cap = live.cap\n    manifest = bi.load_manifest()\n    holder = live.holder()")]),
    # --- session commands
    ("commands run on a session that is not LIVE", "live", [
        (LIVE, "    cls, reasons, _, state = live.classify(record)\n    if cls != ls.LIVE:", "    cls, reasons, _, state = live.classify(record)\n    if False:")]),
    ("Play Mode success reports no mutation", "live", [
        (LIVE, "                   success=lambda resp: AdapterOutcome(ok=True, mutation_performed=True,",
         "                   success=lambda resp: AdapterOutcome(ok=True, mutation_performed=False,")]),
    ("the Play Mode limitation is dropped from results", "live", [
        (LIVE, '    data = {"limitation": PLAYMODE_LIMITATION}\n', "    data = {}\n")]),
    ("an Editor refusal is reported as success", "live", [
        (LIVE, '    return _refuse(cap, REFUSALS.get(r["code"], "LIVE_PROTOCOL_ERROR"), f"{what}: the bridge refused ({r[\'code\']})",',
         '    return AdapterOutcome(ok=True) or _refuse(cap, REFUSALS.get(r["code"], "LIVE_PROTOCOL_ERROR"), f"{what}: the bridge refused ({r[\'code\']})",')]),
    # --- outcomes
    ("a withdrawn request is not reported as never started", "live", [
        (LIVE, "    if result.outcome == ipc.WITHDRAWN:\n", "    if False:\n")]),
    ("an unknown outcome reports no mutation", "live", [
        (LIVE, '                       {"request_id": result.request_id}, mutation_performed=mutating, data=data)',
         '                       {"request_id": result.request_id}, mutation_performed=False, data=data)')]),
    ("an unknown outcome is reported as a timeout", "live", [
        (LIVE, '        return _refuse(cap, "LIVE_OUTCOME_UNKNOWN",', '        return _refuse(cap, "EXECUTION_TIMEOUT",')]),
    ("an INTERRUPTED answer is trusted as done", "live", [
        (LIVE, '    if result.outcome == ipc.UNKNOWN or result.status == "INTERRUPTED":\n        return _refuse(cap, "LIVE_OUTCOME_UNKNOWN",',
         '    if result.outcome == ipc.UNKNOWN:\n        return _refuse(cap, "LIVE_OUTCOME_UNKNOWN",')]),
    ("an unknown request is retried", "live", [
        (IPC, "    return CallResult(RESPONDED, response, rid) if response is not None else CallResult(UNKNOWN, None, rid)",
         "    if response is None:\n        publish(channel, command, args, owner, boot_id, session_id, start_seconds)\n"
         "    return CallResult(RESPONDED, response, rid) if response is not None else CallResult(UNKNOWN, None, rid)")]),
    ("withdrawal always claims success", "live", [
        (IPC, "        os.rename(channel.requests / f\"{rid}.json\", channel.withdrawn / f\"{rid}.json\")\n        return True\n    except FileNotFoundError:\n        return False",
         "        os.rename(channel.requests / f\"{rid}.json\", channel.withdrawn / f\"{rid}.json\")\n        return True\n    except FileNotFoundError:\n        return True")]),
    ("the start deadline is stretched", "live", [
        (IPC, '"start_deadline_utc": utc(issued + datetime.timedelta(seconds=start_seconds))},',
         '"start_deadline_utc": utc(issued + datetime.timedelta(seconds=start_seconds * 100))},')]),
    ("the start window is unbounded", "live", [
        (IPC, "    if not 0 < start_seconds <= MAX_START_SECONDS:\n", "    if not 0 < start_seconds:\n")]),
    ("the request queue is unbounded", "live", [(IPC, "    if channel.queued() >= MAX_QUEUED:\n", "    if False:\n")]),
    ("duplicate JSON keys accepted in responses", "live", [
        (IPC, '        if key in out:\n            raise ChannelProblem(f"duplicate key {key!r}")\n', "")]),
    ("a linked state or response file is read", "live", [
        (IPC, '    if path.is_symlink():\n        raise ChannelProblem(f"{path.name} is a symbolic link")\n', "")]),
    ("a linked live directory is used", "live", [
        (IPC, "            if p.is_symlink():\n                raise ChannelProblem(f\"{p} is a symbolic link\")\n", "")]),
    # --- status classification
    ("heartbeat age alone makes a session STALE", "live", [
        (STATUS, "    fresh = beat_age is not None and beat_age <= FRESH_SECONDS\n",
         "    fresh = beat_age is not None and beat_age <= FRESH_SECONDS\n    if not fresh:\n        return STALE, [\"old heartbeat\"]\n")]),
    ("pid reuse counts as a clean close", "live", [
        (STATUS, "        if (lease_editor == GONE and bridge.get(\"boot_id\") == boot", "        if (lease_editor in (GONE, REUSED) and bridge.get(\"boot_id\") == boot")]),
    ("process identity ignores the start time", "live", [
        (STATUS, "    if abs((started - recorded).total_seconds()) > START_TOLERANCE_SECONDS or not HUB_EDITOR.match(command):",
         "    if not HUB_EDITOR.match(command):")]),
    ("an unprovable process counts as gone", "live", [(STATUS, "    if state != ALIVE:\n        return UNKNOWN\n", "    if state != ALIVE:\n        return GONE\n")]),
    ("a session being bound is STALE", "live", [
        (STATUS, "        if acquired is not None and (now_epoch or time.time()) - acquired < BINDING_GRACE_SECONDS:", "        if False:")]),
    ("a closing Editor still counts as LIVE", "live", [
        (STATUS, '    if lease_editor == ALIVE and bridge.get("boot_id") == boot and bridge.get("state") == "CLOSED":', "    if False:")]),
    # --- detach, clean close, recovery
    ("a live session is detached by a non-owner", "live", [(LIVE, "        if not own:\n", "        if False:\n")]),
    ("clean close without the owner check", "live", [
        (LIVE, "    if cls == ls.CLOSED and own and _cleanly_closed(record, state, lease_editor):",
         "    if cls == ls.CLOSED and _cleanly_closed(record, state, lease_editor):")]),
    ("clean close without the process proven gone", "live", [
        (LIVE, "    return (lease_editor == ls.GONE and b.get(\"state\") == \"CLOSED\"", "    return (b.get(\"state\") == \"CLOSED\"")]),
    ("a crashed session is released without any approval", "live", [
        (LIVE, "    if cls in (ls.STALE, ls.CLOSED):\n        return _recover(live, record, cls, reasons)",
         "    if cls in (ls.STALE, ls.CLOSED):\n        return _release(live, sid, \"stale\")")]),
    ("recovery proceeds without a Human approval", "live", [
        (LIVE, '    state = _await_decision(live, channel, "recovery-status", proposal, boot, deadline)\n',
         '    state = "APPROVED"\n')]),
    ("recovery does not check which session the grant names", "live", [
        (LIVE, '    if granted.get("stale_session_id") != sid or granted.get("stale_boot_id") != stale_boot:', "    if False:")]),
    # --- installer
    ("the installer ignores an open project", "live", [(LIVE, "    if lock.exists() or lock.is_symlink():\n", "    if False:\n")]),
    ("the installer follows symbolic links", "live", [(INSTALL, "            st = os.lstat(path)\n", "            st = os.stat(path)\n")]),
    ("extra files in the installed package are ignored", "live", [
        (INSTALL, '    out += [f"{p}: not part of the audited bridge" for p in sorted(set(found) - set(expected))]\n', "")]),
    ("extra directories in the installed package are ignored", "live", [
        (INSTALL, '    out += [f"{d}/: not part of the audited bridge" for d in sorted(extra_dirs)]\n', "")]),
    ("modified installed files are ignored", "live", [
        (INSTALL, '    out += [f"{p}: modified" for p in sorted(set(expected) & set(found)) if expected[p] != found[p]]\n', "")]),
    ("the GPOS-owned source is not verified", "live", [
        (INSTALL, '    if problems or sorted(entries, key=lambda e: e["path"]) != sorted(manifest["files"], key=lambda e: e["path"]):',
         "    if False:")]),
    ("the manifest digest is not checked", "live", [(INSTALL, '    if digest(data["files"]) != data["package_digest"]:', "    if False:")]),
    # --- identity
    ("the project key ignores the project path", "live", [
        (IDENT, '    return hashlib.sha256((KEY_PREFIX + rel).encode("utf-8")).hexdigest()[:16]',
         '    return hashlib.sha256(KEY_PREFIX.encode("utf-8")).hexdigest()[:16]')]),
    ("paths are not canonicalized to their on-disk spelling", "live", [
        (IDENT, "            matches = [n for n in names if n.casefold() == part.casefold()]\n", "            matches = []\n")]),
    ("GPOS root discovery is unbounded", "live", [(IDENT, "    for _ in range(MAX_ASCENT + 1):\n", "    for _ in range(4096):\n")]),
    # --- declarations
    ("install declared as an Editor-observed operation", "live", [
        (ADAPTER, '          TimeoutPolicy(default=60.0, maximum=300.0), execution_context="OFFLINE_ANALYSIS", dry_run_supported=True,',
         '          TimeoutPolicy(default=60.0, maximum=300.0), execution_context="EDITOR", dry_run_supported=True,')]),
    ("the adapter state model stays STATELESS", "live", [
        (ADAPTER, '    adapter_kind="CLI", state_model="STATEFUL",', '    adapter_kind="CLI", state_model="STATELESS",')]),
    # --- bridge glue (manifest regenerated in the copy)
    ("the bridge runs in batch mode", "live", [(BRIDGE + "Bridge.cs", "            if (Application.isBatchMode) return;                      // never in batch mode: no switch turns this on\n", "")]),
    ("the bridge runs in import workers", "live", [
        (BRIDGE + "Bridge.cs", "            if (AssetDatabase.IsAssetImportWorkerProcess()) return;   // import workers run [InitializeOnLoad] code too\n", "")]),
    ("the bridge opens the approval window itself", "live", [
        (BRIDGE + "Commands.cs", '            Debug.Log("[GPOS] A live-session attach request is waiting for your approval: GPOS > Live Session");',
         '            EditorWindow.GetWindow<ApprovalWindow>();')]),
    ("the bridge uses delayCall", "live", [
        (BRIDGE + "Bridge.cs", "            EditorApplication.update += Tick;\n", "            EditorApplication.update += Tick;\n            EditorApplication.delayCall += () => { };\n")]),
    # --- the bridge's C# core
    ("C#: an expired request still runs", "core", [(CORE + "Protocol.cs", "            if (nowTicks > r.DeadlineTicks)\n", "            if (false)\n")]),
    ("C#: a request for another boot runs", "core", [(CORE + "Protocol.cs", "            if (r.BootId != bootId) throw", "            if (false) throw")]),
    ("C#: extra request keys accepted", "core", [(CORE + "Protocol.cs", "            if (d.Count != Keys.Length) throw", "            if (false) throw")]),
    ("C#: a generic command is added", "core", [
        (CORE + "Protocol.cs", '            { "status", new Spec { Args = new string[0] } },',
         '            { "status", new Spec { Args = new string[0] } },\n            { "execute-method", new Spec { Args = new string[0] } },')]),
    ("C#: an approval command is added", "core", [
        (CORE + "Protocol.cs", '            { "status", new Spec { Args = new string[0] } },',
         '            { "status", new Spec { Args = new string[0] } },\n            { "approve-attach", new Spec { Args = new[] { "proposal_id" } } },')]),
    ("C#: the session requirement is not enforced", "core", [
        (CORE + "Protocol.cs", "            if (spec.Session && (r.SessionId == null || !Hex32.IsMatch(r.SessionId)))", "            if (false)")]),
    ("C#: the owner kind is optional", "core", [
        (CORE + "Protocol.cs", 'new Regex("^[A-Z][A-Z_]*:[A-Za-z0-9][A-Za-z0-9._@-]{0,63}\\\\z")', 'new Regex("^[A-Za-z0-9:._@-]{1,80}\\\\z")')]),
    ("C#: the start window is unbounded", "core", [
        (CORE + "Protocol.cs", "            if (r.DeadlineTicks <= r.IssuedTicks ||\n                r.DeadlineTicks - r.IssuedTicks > TimeSpan.FromSeconds(MaxStartWindowSeconds).Ticks)",
         "            if (r.DeadlineTicks <= r.IssuedTicks)")]),
    ("C#: the journal forgets admitted requests", "core", [
        (CORE + "Journal.cs", "            entries.RemoveAll(e => e.Deadline < nowTicks);", "            entries.Clear();")]),
    ("C#: duplicates are admitted", "core", [(CORE + "Journal.cs", '            if (entries.Any(e => e.Id == r.Id)) return "DUPLICATE_REQUEST";\n', "")]),
    ("C#: the rate limit is removed", "core", [
        (CORE + "Journal.cs", '            if (entries.Count(e => e.Issued >= window) >= RateLimit) return "RATE_LIMITED";\n', "")]),
    ("C#: a decided proposal can be decided again", "core", [
        (CORE + "Proposals.cs", '            if (p == null || p.BootId != bootId || Effective(p, now) != "PENDING") return false;',
         "            if (p == null || p.BootId != bootId) return false;")]),
    ("C#: a grant can be used twice", "core", [(CORE + "Proposals.cs", '            p.State = "CONSUMED";\n', "")]),
    ("C#: grants never expire", "core", [
        (CORE + "Proposals.cs", '            if (p.State == "APPROVED" && now > p.GrantExpires) return "GRANT_EXPIRED";\n', "")]),
    ("C#: a grant serves another owner", "core", [(CORE + "Proposals.cs", '            if (p.Owner != owner) return "OWNER_MISMATCH";\n            if (p.BootId', "            if (p.BootId")]),
    ("C#: a recovery grant serves an attach", "core", [(CORE + "Proposals.cs", '            if (p.Kind != kind) return "WRONG_KIND";\n', "")]),
    ("C#: an expired proposal can still be approved", "core", [
        (CORE + "Proposals.cs", '            if (p.State == "PENDING" && now > p.Expires) return "EXPIRED";\n', "")]),
    ("C#: entering Play Mode is done before it happened", "core", [
        (CORE + "Transitions.cs", '                if (playing && willChange && p.Events.Contains("EnteredPlayMode")) return new[] { "OK", null };',
         '                if (p.Events.Contains("ExitingEditMode")) return new[] { "OK", null };')]),
    ("C#: a pending transition does not make the Editor busy", "core", [
        (CORE + "Transitions.cs", '            if (pending) return "PENDING_OPERATION";\n            return phase == Edit || phase == Playing || phase == Paused ? null : phase;',
         '            return phase == Edit || phase == Playing || phase == Paused ? null : phase;')]),
    ("C#: transitions never time out", "core", [
        (CORE + "Transitions.cs", '            if (now > p.Deadline) return new[] { "FAILED", "TRANSITION_TIMEOUT" };\n', "")]),
    ("C#: duplicate JSON keys accepted", "core", [(CORE + "Json.cs", '                if (d.ContainsKey(key)) throw new JsonProblem("duplicate key");\n', "")]),
    ("C#: JSON nesting unbounded", "core", [(CORE + "Json.cs", '            if (depth > MaxDepth) throw new JsonProblem("nesting too deep");\n', "")]),
]


def run(mutation):
    name, suite, edits = mutation
    tmp = Path(tempfile.mkdtemp(prefix="gpos-livemut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for rel, anchor, replacement in edits:
            path = copy / rel
            text = path.read_text()
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            path.write_text(text.replace(anchor, replacement))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GPOS_UNITY_TEST_FAST="1")
        if any("live_bridge/" in rel for rel, _, _ in edits):
            subprocess.run([sys.executable, "-B", str(copy / "tests" / "generate_live_bridge_manifest.py")],
                           capture_output=True, text=True, timeout=120, env=env, check=True)
        test = "test_unity_live.py" if suite == "live" else "test_unity_live_bridge_core.py"
        try:
            out = subprocess.run([sys.executable, "-B", str(copy / "tests" / test)], capture_output=True, text=True,
                                 timeout=1800, env=env)
        except subprocess.TimeoutExpired:
            return name, "CAUGHT"   # a hang is a failure of the suite
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--only", help="run only mutations whose name contains this text")
    args = parser.parse_args()
    selected = [m for m in MUTATIONS if not args.only or args.only in m[0]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, selected))
    for name, verdict in results:
        print(f"{verdict:<12} {name}")
    caught = sum(v == "CAUGHT" for _, v in results)
    print(f"caught {caught} of {len(results)}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
