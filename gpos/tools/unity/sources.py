"""Unity live source synchronization and compilation facts (Phase 2C-6C): four fixed capabilities through the audited
bridge 1.4.0.

    unity.live-sync-sources             tell Unity about exact changed or deleted source paths         MUTATING
    unity.live-compilation-status       phase, compile and sync generations, counts, retention facts   READ_ONLY
    unity.live-compilation-diagnostics  the journal's compiler messages, paged, closed filters only    READ_ONLY
    unity.live-wait-ready               observe until the Editor has settled (after a named sync)      READ_ONLY

Other programs write source files; GPOS never does, takes no C# text or file content and has no compile command. A
sync names exact `.cs`, `.asmdef` and `.asmref` paths below Assets/: an existing one is imported exactly (Unity may
create its .meta); a deleted one is synchronized by a recursive import of its direct parent folder — or, only when that
folder is gone too, of exactly one folder above it — never the Assets root and never a global Refresh. The bridge
validates every path and snapshots every such folder (bounded, link-free) before its first import, records the causal
baseline `compile_started_before_sync` before that import, and reports what the folder import did within the bounded
walk of the folder's files (stale database entries whose files were already gone may be reconciled unlisted). Once an
import has begun the call reports mutation_performed, whatever follows. Unity then compiles and reloads as it decides:
one edit is never claimed to be one compilation (Unity coalesces and abandons compilations).

Compilation facts come from the bridge's journal of CompilationPipeline callbacks (never Editor.log): monotonic
Editor-session counters, the last finished compilations, the sync records and the latest messages of each assembly,
kept across Domain Reloads and lost when the Editor quits. A failed compilation is Edit Mode with compilation_failed
true, not a phase. wait-ready only observes (the heartbeat, then one compilation-status request): it never imports,
compiles, refreshes, reloads, restarts, kills or replays anything. Given a sync generation it is ready only when the
Editor is in Edit Mode, idle and stable, and a compilation that started after that sync has settled: FAILED (errors,
no reload) or SUCCEEDED (no errors and a Domain Reload after it). ready with compilation_failed never means Play Mode
or tests can run. Nothing here produces evidence. Inputs are strings; path lists are strict JSON arrays.
"""

import dataclasses
import re

from .. import redaction
from ..execution import AdapterOutcome
from . import assets as A
from . import authoring as au
from . import live as lv
from . import live_ipc as ipc
from . import live_status as ls

SYNC = "unity.live-sync-sources"
STATUS = "unity.live-compilation-status"
DIAGNOSTICS = "unity.live-compilation-diagnostics"
WAIT = "unity.live-wait-ready"
COMMANDS = {SYNC: "sync-sources", STATUS: "compilation-status", DIAGNOSTICS: "compilation-diagnostics",
            WAIT: "compilation-status"}
CAPABILITY_IDS = (SYNC, STATUS, DIAGNOSTICS, WAIT)
READ_ONLY = (STATUS, DIAGNOSTICS, WAIT)

LIMITATION = ("Editor source state only: SUCCESS means the Unity Editor imported exactly these paths (and the folders of "
              "deleted ones). Compilation and Domain Reload follow as Unity decides and are observed with "
              "unity.live-wait-ready; nothing here is evidence of gameplay, build or test behaviour, and project code "
              "(AssetPostprocessors, InitializeOnLoad code after a reload) may run as a consequence.")
JOURNAL_DISCLOSURE = ("Editor-session facts from CompilationPipeline callbacks: kept across Domain Reloads and lost when "
                      "the Editor quits; an assembly Unity did not recompile (a cached result) reports nothing and keeps "
                      "its earlier entry, except that error entries are dropped (counted as superseded) once a Domain "
                      "Reload after a compilation without errors proves every assembly compiled; messages are clipped and "
                      "bounded; not evidence.")
READY_LIMITATION = ("ready means the Editor settled in Edit Mode; with compilation_failed it still runs the scripts of its "
                    "last successful compilation, and Unity refuses Play Mode and test runs until the errors are fixed. "
                    "Nothing was triggered.")

EXTENSIONS = (".cs", ".asmdef", ".asmref")
STEM = re.compile(r"^[A-Za-z0-9_+\-]([A-Za-z0-9_.+\-]{0,126}[A-Za-z0-9_+\-])?$")
EXCLUDED = ("streamingassets", "editor default resources", "cvs")
ASSEMBLY = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
SEVERITIES = ("ERROR", "WARNING")
MAX_PATHS = 64
MAX_PATH = 512
MAX_FOLDERS = 16
MAX_WAIT = 300.0
SETTLE_SECONDS = 2.0
POLL_SECONDS = 0.25
CONFIRM_WAIT = 10.0
SUCCEEDED, FAILED, NONE_OBSERVED = "SUCCEEDED", "FAILED", "NONE_OBSERVED"
ABSOLUTE_PATH = re.compile(r"(?:(?<![\w.])/(?:Users|private|var|tmp|Volumes|Applications|home|opt|Library|System)/"
                           r"[^\s'\"()<>:]*)|(?:\b[A-Za-z]:[\\/][^\s'\"()<>]*)")

InputProblem = au.InputProblem


# ---------------------------------------------------------------- input grammar

def source_path(name, value):
    """One source path, exactly as the bridge checks it again (SourcePaths.Check)."""
    def refuse(why):
        raise InputProblem("LIVE_SOURCE_PATH_INVALID", f"{name}: {why}")
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_PATH:
        refuse(f"a source path is text of 1 to {MAX_PATH} characters")
    if not value.startswith("Assets/"):
        refuse("sources are synchronized only below Assets/ (never Packages/, Library/ or an absolute path)")
    ext = next((e for e in EXTENSIONS if value.endswith(e)), None)
    if ext is None:
        refuse("a source path ends in .cs, .asmdef or .asmref")
    parts = value.split("/")
    if len(parts) > MAX_FOLDERS + 2:
        refuse(f"a source lives at most {MAX_FOLDERS} folders below Assets/")
    for s in parts[1:-1]:
        if not A.SEGMENT.fullmatch(s) or s.startswith((".", " ")) or s.endswith((".", " ")):
            refuse(f"folder name {s!r} is not a plain folder name")
        if s.lower() in EXCLUDED:
            refuse(f"sources are never synchronized in a special folder ({s})")
        if s.lower().startswith(A.SCRATCH_PREFIX):
            refuse("GPOS transaction scratch folders hold no sources")
    stem = parts[-1][:-len(ext)]
    if not STEM.fullmatch(stem) or ".." in stem:
        refuse("the file name is 1 to 128 letters, digits, _ + - or inner dots")
    return value


def path_list(name, value):
    parsed = au.strict_json(name, value)
    if not isinstance(parsed, list) or len(parsed) > MAX_PATHS:
        raise InputProblem("LIVE_SOURCE_PATH_INVALID", f"{name} is a JSON array of at most {MAX_PATHS} source paths")
    return [source_path(f"{name}[{i}]", p) for i, p in enumerate(parsed)]


def severity(name, value):
    if au._text(name, value) not in SEVERITIES:
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is ERROR or WARNING")
    return value


def assembly(name, value):
    if not ASSEMBLY.fullmatch(au._text(name, value)):
        raise InputProblem("INVALID_TOOL_REQUEST", f"{name} is an exact assembly name from the journal")
    return value


INPUTS = {
    SYNC: {"sources": path_list, "deleted": path_list},
    STATUS: {},
    DIAGNOSTICS: {"generation": au.whole(1, 2 ** 31 - 1), "severity": severity, "assembly": assembly,
                  "page": au.whole(0, 10000)},
    WAIT: {"sync_generation": au.whole(1, 9999999999)},
}


def input_kinds(cap):
    return ("unity_project",) + tuple(INPUTS[cap])


def parse_inputs(cap, inputs):
    """The bridge arguments for `cap` (every key; absent is None, an absent path list is empty), or InputProblem."""
    inputs = dict(inputs or {})
    inputs.pop("unity_project", None)
    args = {k: None for k in INPUTS[cap]}
    for k, v in inputs.items():
        if k not in INPUTS[cap]:
            raise InputProblem("INVALID_TOOL_REQUEST", f"{cap} takes no input {k!r}")
        args[k] = INPUTS[cap][k](k, v)
    if cap == SYNC:
        args = {k: args[k] or [] for k in args}
        named = args["sources"] + args["deleted"]
        if not named:
            raise InputProblem("INVALID_TOOL_REQUEST", f"{SYNC} names at least one path in sources or deleted")
        if len(named) > MAX_PATHS:
            raise InputProblem("LIVE_SOURCE_SYNC_LIMIT", f"a sync names at most {MAX_PATHS} paths")
        if len({p.lower() for p in named}) != len(named):
            raise InputProblem("LIVE_SOURCE_PATH_INVALID", "a path is named more than once")
    return args


# ---------------------------------------------------------------- facts GPOS derives (Unity-free)

def classify(last, reload_generation, compilation_failed):
    """SUCCEEDED | FAILED | None (not settled yet) for one finished compilation, from facts only: errors mean FAILED
    (Unity keeps the old domain, no reload follows); no errors and a Domain Reload after it mean SUCCEEDED; no new
    errors but Unity's settled flag set means FAILED (an assembly that was not recompiled still fails)."""
    if not last:
        return None
    if last.get("errors", 0) > 0:
        return FAILED
    if isinstance(reload_generation, int) and reload_generation > last.get("reload_generation", reload_generation):
        return SUCCEEDED
    if compilation_failed is True:
        return FAILED
    return None


def idle(state):
    state = state or {}
    return (state.get("phase") == "EDIT" and state.get("compiling") is False and state.get("updating") is False
            and state.get("pending_transition") is False)


def settled(state, compilation, reload_generation, before):
    """(ready, outcome) from one observation. Without a sync baseline, ready is the idle Edit Mode state and the
    outcome is that of the last compilation (NONE_OBSERVED when there was none). With one, ready needs a compilation
    that started after the sync (compile generation > compile_started_before_sync) and has settled."""
    compilation = compilation or {}
    last = compilation.get("last_compile")
    failed = (state or {}).get("compilation_failed")
    if before is None:
        return idle(state), (classify(last, reload_generation, failed) if last else NONE_OBSERVED)
    if not last or not last.get("compile_generation", 0) > before:
        return False, None
    outcome = classify(last, reload_generation, failed)
    return idle(state) and outcome is not None, outcome


def _observation_key(beat):
    state, comp = beat.get("state") or {}, beat.get("compilation") or {}
    last = comp.get("last_compile") or {}
    return (beat.get("boot_id"), beat.get("generation"), comp.get("compile_generation"), comp.get("completed_compiles"),
            last.get("compile_generation"), state.get("phase"), state.get("compiling"), state.get("updating"),
            state.get("compilation_failed"), state.get("pending_transition"))


def status_facts(data):
    """The compilation-status data plus what GPOS derives from it."""
    last, sync = data.get("last_compile"), data.get("latest_sync")
    state = data.get("state") or {}
    newer = bool(last and sync and last.get("compile_generation", 0) > sync.get("compile_started_before_sync", 0))
    outcome = classify(last, data.get("reload_generation"), state.get("compilation_failed")) if last else None
    return dict(data, settled=idle(state), outcome_newer_than_latest_sync=newer, last_compile_outcome=outcome,
                last_completed_compile_generation=(last or {}).get("compile_generation", 0),
                disclosure=JOURNAL_DISCLOSURE)


def clean_text(text):
    """A compiler message through the redaction boundary, with any absolute path left replaced by <path>."""
    if not isinstance(text, str):
        return text
    text, _ = redaction.redact(text)
    return ABSOLUTE_PATH.sub("<path>", text)


def _clean_entry(e):
    out = dict(e, message=clean_text(e.get("message")))
    if isinstance(out.get("file"), str) and (out["file"].startswith("/") or ABSOLUTE_PATH.search(out["file"])):
        out["file"], out["outside_project"] = None, True
    return out


# ---------------------------------------------------------------- results

def _side_effects(data):
    folders = data.get("folders") or []
    metas = sum(1 for s in data.get("sources") or () if s.get("meta_created")) + \
        sum(f.get("meta_created_count", 0) for f in folders)
    requested = {s.get("path") for s in data.get("sources") or ()} | {d.get("path") for d in data.get("deleted") or ()}
    other_new = sum(1 for f in folders for p in f.get("imported_new") or () if p not in requested)
    other_removed = sum(1 for f in folders for p in f.get("removed") or () if p not in requested)
    changed = sum(f.get("meta_removed_count", 0) + f.get("meta_changed_count", 0) for f in folders)
    return {"meta_created": metas, "other_imported": other_new, "other_removed": other_removed,
            "meta_removed_or_changed": changed}


def _success(cap, data):
    if cap != SYNC:
        return []
    if not data.get("mutation_started"):
        return [lv._diag("LIVE_SOURCES_SYNCED", "nothing needed importing: every named deleted source was already "
                                                "synchronized", cap)]
    diags = [lv._diag("LIVE_SOURCES_SYNCED", f"imported {data.get('imports')} path(s) as sync generation "
                                             f"{data.get('sync_generation')}; compilation and Domain Reload follow as "
                                             f"Unity decides (observe with {WAIT})", cap,
                      {"sync_generation": data.get("sync_generation"),
                       "compile_started_before_sync": data.get("compile_started_before_sync")})]
    effects = _side_effects(data)
    if any(effects.values()):
        diags.append(lv._diag("LIVE_SOURCE_SYNC_SIDE_EFFECTS",
                              f"Unity created {effects['meta_created']} .meta file(s), imported {effects['other_imported']} "
                              f"and removed {effects['other_removed']} other asset(s), and removed or changed "
                              f"{effects['meta_removed_or_changed']} .meta file(s), each listed within the bounded walk of "
                              f"the folder; stale entries whose files were already gone may be reconciled unlisted", cap, effects))
    return diags


def outcome(cap, r):
    result = au.outcome(cap, r, commands=COMMANDS, read_only=READ_ONLY, limitation=LIMITATION,
                        success=lambda data: _success(cap, data))
    data = result.data
    if cap == SYNC and r.response is not None and r.response.get("status") == "OK" and isinstance(data, dict) \
            and data.get("mutation_started") is False:
        result = dataclasses.replace(result, mutation_performed=False)   # every deletion was already synchronized
    if cap == STATUS and r.response is not None and r.response.get("status") == "OK" and isinstance(data, dict):
        result = dataclasses.replace(result, data=status_facts(data))
    if cap == DIAGNOSTICS and isinstance(data, dict) and isinstance(data.get("entries"), list):
        result = dataclasses.replace(result, data=dict(data, entries=[_clean_entry(e) for e in data["entries"]
                                                                      if isinstance(e, dict)],
                                                       disclosure=JOURNAL_DISCLOSURE))
    return result


def execute(request, context, facts=None, sleep=None, monotonic=None):
    cap = request.capability_id
    if cap == WAIT:
        return _wait(request, context, facts, sleep, monotonic)
    return au.execute(request, context, facts=facts, sleep=sleep, monotonic=monotonic, parse=parse_inputs,
                      command=COMMANDS[cap], finish=outcome)


# ---------------------------------------------------------------- wait-ready (observation only)

def _status(live, channel, boot, sid, wait):
    """compilation-status data, or None when the read did not answer (it is read-only: nothing to recover)."""
    r = live.call(channel, "compilation-status", {}, boot, sid, wait=wait)
    if r.outcome == ipc.RESPONDED and r.status == "OK" and isinstance((r.response or {}).get("data"), dict):
        return r.response["data"]
    return None


def _summary(status):
    rows = [a for a in (status or {}).get("assemblies") or () if isinstance(a, dict)]
    return {"errors": sum(a.get("errors", 0) for a in rows), "warnings": sum(a.get("warnings", 0) for a in rows),
            "assemblies_with_errors": sorted(a.get("assembly") for a in rows if a.get("errors", 0) > 0)[:32]}


def _wait(request, context, facts, sleep, monotonic):
    cap = WAIT
    kw = {k: v for k, v in (("facts", facts), ("sleep", sleep), ("monotonic", monotonic)) if v is not None}
    try:
        args = parse_inputs(cap, request.inputs)
    except InputProblem as problem:
        return AdapterOutcome(ok=True, diagnostics=(lv._diag(problem.code, str(problem), cap),))
    try:
        live = lv.Live(request, context, **kw)
        record = live.context.session
        sid = (record.get("session") or {}).get("session_id")
        cls, reasons, _, state = live.classify(record)
        if cls != ls.LIVE:
            code = "LIVE_SESSION_UNRESPONSIVE" if cls == ls.UNRESPONSIVE else "LIVE_SESSION_STALE"
            return lv._refuse(cap, code, f"session {sid} is {cls}", {"reasons": reasons})
        b = live.bridge(live.require_installed(), state)
        channel, boot = live.channel(), b["boot_id"]
        budget = max(1.0, min(MAX_WAIT, float(context.timeout)) - CONFIRM_WAIT)
        start = live.monotonic()
        target = args["sync_generation"]
        before = None
        if target is not None:
            status = _status(live, channel, boot, sid, CONFIRM_WAIT)
            if status is None:
                return lv._refuse(cap, "LIVE_NOT_READY", "the compilation facts could not be read to resolve the sync "
                                                         "generation; nothing was triggered", data={"ready": False})
            record_ = next((s for s in status.get("syncs") or () if s.get("sync_generation") == target), None)
            if record_ is None:
                return lv._refuse(cap, "LIVE_SYNC_GENERATION_UNKNOWN",
                                  f"sync generation {target} is not retained by this Editor session (latest "
                                  f"{status.get('sync_generation')})", data={"ready": False})
            before = record_["compile_started_before_sync"]
        key, since, beat = None, None, {}
        while True:
            now = live.monotonic()
            beat = ls.read_state(live.live_dir)["heartbeat"] or {}
            if beat.get("boot_id") != boot:
                return _not_ready(live, channel, boot, sid, cap, target, before, now - start,
                                  "the Editor boot changed or its heartbeat cannot be read")
            ready, _ = settled(beat.get("state"), beat.get("compilation"), beat.get("generation"), before)
            k = _observation_key(beat)
            if k != key:
                key, since = k, now
            if ready and now - since >= SETTLE_SECONDS:
                status = _status(live, channel, boot, sid, CONFIRM_WAIT)
                if status is not None:
                    confirmed, outcome_ = settled(status.get("state"), {"last_compile": status.get("last_compile")},
                                                  status.get("reload_generation"), before)
                    if confirmed:
                        return _ready(cap, status, target, before, outcome_, live.monotonic() - start)
                key = None
            if now - start >= budget:
                return _not_ready(live, channel, boot, sid, cap, target, before, now - start,
                                  "the wait ended before the Editor settled")
            live.sleep(POLL_SECONDS)
    except lv.Refused as refused:
        return refused.outcome


def _facts(status, target, before, waited):
    status = status or {}
    state = status.get("state") or {}
    last = status.get("last_compile") or {}
    return {"sync_generation": target, "compile_started_before_sync": before, "phase": state.get("phase"),
            "compiling": state.get("compiling"), "updating": state.get("updating"),
            "compilation_failed": state.get("compilation_failed"), "reload_generation": status.get("reload_generation"),
            "compile_generation": status.get("compile_generation"), "completed_compiles": status.get("completed_compiles"),
            "last_completed_compile_generation": last.get("compile_generation", 0),
            "compile_starts_since_last_outcome": (status.get("compile_generation") or 0) - last.get("compile_generation", 0),
            "latest_sync_generation": status.get("sync_generation"), "diagnostics_summary": _summary(status),
            "waited_seconds": round(waited, 1), "disclosure": JOURNAL_DISCLOSURE}


def _ready(cap, status, target, before, outcome_, waited):
    data = dict(_facts(status, target, before, waited), ready=True, outcome=outcome_, limitation=READY_LIMITATION)
    data["compilation_failed"] = outcome_ == FAILED or data["compilation_failed"] is True
    diags = []
    if data["compilation_failed"]:
        diags.append(lv._diag("LIVE_COMPILATION_FAILED", f"the Editor settled with a failed compilation "
                                                         f"({data['diagnostics_summary']['errors']} error(s) in the "
                                                         f"journal); read them with {DIAGNOSTICS}", cap))
    return AdapterOutcome(ok=True, data=data, diagnostics=tuple(diags))


def _not_ready(live, channel, boot, sid, cap, target, before, waited, why):
    status = None
    try:
        status = _status(live, channel, boot, sid, CONFIRM_WAIT)
    except lv.Refused:
        pass
    outcome_ = None
    if status is not None:
        _, outcome_ = settled(status.get("state"), {"last_compile": status.get("last_compile")},
                              status.get("reload_generation"), before)
        if before is not None and outcome_ is None and (status.get("compile_generation") or 0) <= before:
            outcome_ = NONE_OBSERVED
    data = dict(_facts(status, target, before, waited), ready=False, outcome=outcome_, facts_read=status is not None)
    return lv._refuse(cap, "LIVE_NOT_READY", f"{why}; nothing was triggered" +
                      ("; no compilation started after the sync" if outcome_ == NONE_OBSERVED else ""), data=data)
