#!/usr/bin/env python3
"""Bounded mutation harness for the tool adapter foundation (gpos/tools/).

    python3 tests/mutate_tools.py [--jobs N] [--only TEXT]

Each mutation disables or weakens exactly one high-risk foundation invariant in a temporary copy of
the repository and runs tests/test_tool_foundation.py there. A mutation must make the suite fail
("CAUGHT"); one that leaves it green is "MISSED" and fails this harness. An anchor that no longer
exists is reported "NOT APPLIED" and also fails the harness, so the list cannot rot. The repository
itself is never modified.

The list is deliberately bounded to the invariants that protect authority, safety and evidence
meaning; it is not a general mutation framework.
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

# (name, file, anchor, replacement)
MUTATIONS = [
    # --- authority: what a tool may claim
    ('HUMAN_EVIDENCE allowed from a tool', 'gpos/tools/evidence.py',
     '    if candidate.evidence_type in pol[FORBIDDEN_KEY]:\n        return None, [diag("EVIDENCE_TYPE_FORBIDDEN",',
     '    if False:\n        return None, [diag("EVIDENCE_TYPE_FORBIDDEN",'),
    ('HUMAN_EVIDENCE accepted at registration', 'gpos/tools/validation.py',
     '        if etype in pol["forbidden_evidence_types"]:', '        if False:'),
    ('HUMAN_EVIDENCE materialized into a record', 'gpos/tools/evidence.py',
     '    if candidate.evidence_type in pol[FORBIDDEN_KEY]:\n        return None, [f"{candidate.evidence_type} can never be materialized',
     '    if False:\n        return None, [f"{candidate.evidence_type} can never be materialized'),
    ('a record is materialized without a proven revision', 'gpos/tools/evidence.py',
     '    if not candidate.materializable:\n        return None, ["the candidate has no proven subject revision',
     '    if False:\n        return None, ["the candidate has no proven subject revision'),
    # --- evidence semantics
    ('evidence context compatibility skipped', 'gpos/tools/evidence.py',
     '    if not compatible(framework, candidate.evidence_type, candidate.capture_context):', '    if False:'),
    ('registry compatibility ignored at registration', 'gpos/tools/validation.py',
     '        if context not in reg["evidence_context_compatibility"][etype]:', '        if False:'),
    ('an unobserved capture context is accepted', 'gpos/tools/evidence.py',
     '    elif candidate.capture_context != execution_context:', '    elif False:'),
    ('derivation upgrades its source context', 'gpos/tools/evidence.py',
     '        if inherited != {candidate.capture_context}:', '        if False:'),
    ('derived artifacts lose their origin context', 'gpos/tools/artifacts.py',
     '        origin = sorted(origins)[0] if len(origins) == 1 else (None if origins else execution_context)',
     '        origin = execution_context'),
    ('dry run allowed to emit runtime evidence', 'gpos/tools/evidence.py',
     '    if dry_run and candidate.evidence_type not in pol[DRY_RUN_KEY]:', '    if False:'),
    ('a capability may offer evidence it never declared', 'gpos/tools/evidence.py',
     '    if pair not in capability.evidence_pairs():', '    if False:'),
    ('incomplete artifacts offered as evidence', 'gpos/tools/evidence.py',
     '    incomplete = [a for a in candidate.artifact_ids if not by_id[a].complete]',
     '    incomplete = []'),
    ('evidence from an unfinished execution is kept', 'gpos/tools/execution.py',
     '        if accepted is not None and complete:', '        if accepted is not None:'),
    # --- mutation semantics
    ('mutating capability runs without consent', 'gpos/tools/execution.py',
     '    if capability.mutating and not request.dry_run and not request.allow_mutation:', '    if False:'),
    ('a mutating capability may be declared read-only', 'gpos/tools/validation.py',
     '    if cap.mutating and cap.side_effect_scope in (None, "", "NONE"):', '    if False:'),
    ('a read-only capability may report a mutation', 'gpos/tools/execution.py',
     '    if outcome.mutation_performed and not capability.mutating:', '    if False:'),
    ('dry run may mutate', 'gpos/tools/execution.py',
     '    mutation = bool(outcome.mutation_performed) and capability.mutating and not dry_run',
     '    mutation = bool(outcome.mutation_performed)'),
    ('dry run may run an unsupported capability', 'gpos/tools/execution.py',
     '    if request.dry_run and not capability.dry_run_supported:', '    if False:'),
    # --- single writer
    ('single-writer declaration no longer required', 'gpos/tools/validation.py',
     '    if needs_writer and not cap.single_writer_required:', '    if False:'),
    ('single-writer lease never taken', 'gpos/tools/execution.py',
     '    if capability.single_writer_required and not request.dry_run:\n        lease, problems = _acquire(',
     '    if False:\n        lease, problems = _acquire('),
    ('lease conflict ignored', 'gpos/tools/leases.py',
     '        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)',
     '        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)'),
    ('an unreadable lease is treated as free', 'gpos/tools/leases.py',
     '    return data if data is not None else {"unreadable": True, "path": str(path)}', '    return None'),
    ("another owner's lease is released", 'gpos/tools/leases.py',
     '    if holder.get("owner_id") != lease.owner_id or holder.get("token") != lease.token:\n        return False',
     '    if False:\n        return False'),
    ('breaking a lease needs no reason', 'gpos/tools/leases.py',
     '    if not reason:\n        raise LeaseHeld("LEASE_INVALID", "breaking a lease requires an explicit reason")',
     '    if False:\n        raise LeaseHeld("LEASE_INVALID", "breaking a lease requires an explicit reason")'),
    # --- process safety
    ('shell execution enabled', 'gpos/tools/process.py',
     '                    "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "close_fds": True}',
     '                    "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "close_fds": True,\n                    "shell": False}  # shell=True would be here'),
    ('shell interpreters accepted as the executable', 'gpos/tools/process.py',
     '    if Path(exe).name.lower() in SHELL_EXECUTABLES:', '    if False:'),
    ('program-string flags accepted', 'gpos/tools/process.py',
     '        if arg in SHELL_PROGRAM_FLAGS:', '        if False:'),
    ('a relative executable is accepted', 'gpos/tools/process.py',
     '    if not Path(exe).is_absolute():', '    if False:'),
    ('cwd boundary removed', 'gpos/tools/process.py',
     '    reason = tp.unsafe_reason(scopes, spec.cwd, must_exist=True)\n    if reason:', '    reason = None\n    if reason:'),
    ('timeout ignored', 'gpos/tools/process.py', '        proc.wait(timeout=spec.timeout)', '        proc.wait()'),
    ('timeout policy bound removed', 'gpos/tools/capabilities.py',
     '        if requested > self.maximum:', '        if False:'),
    ('a timed-out execution reports success', 'gpos/tools/execution.py',
     '        if process.timed_out:\n            diagnostics.append(dg.make("EXECUTION_TIMEOUT",',
     '        if False:\n            diagnostics.append(dg.make("EXECUTION_TIMEOUT",'),
    ('a failing tool reports success', 'gpos/tools/execution.py',
     '    if failed:\n        diagnostics.append(dg.make("EXECUTION_FAILED",',
     '    if False:\n        diagnostics.append(dg.make("EXECUTION_FAILED",'),
    ('output capture bound removed', 'gpos/tools/process.py',
     '            room = limit - len(sink["data"])\n            if room > 0:\n                sink["data"] += chunk[:room]',
     '            sink["data"] += chunk'),
    ('secret redaction disabled', 'gpos/tools/redaction.py',
     '    for pattern, replacement in PATTERNS:\n        text, n = pattern.subn(replacement, text)\n        count += n',
     '    for pattern, replacement in PATTERNS:\n        n = 0\n        count += n'),
    ('environment values copied into provenance', 'gpos/tools/process.py',
     '        names = {name for name, _ in self.overrides} | set(self.recorded)\n        return {"inherited_names": sorted(self.inherit), "set_names": sorted(names)}',
     '        return {"inherited_names": sorted(self.inherit), "set_names": sorted(self.build())}'),
    # --- paths and artifacts
    ('artifact path escape allowed', 'gpos/tools/artifacts.py',
     '        reason = tp.unsafe_reason(scopes, spec.path)\n        if reason:', '        reason = None\n        if reason:'),
    ('symlink escape allowed', 'gpos/tools/paths.py',
     '            if os.path.islink(current):\n                return f"{path}: component {current} is a symlink"',
     '            if False:\n                return f"{path}: component {current} is a symlink"'),
    ('path traversal allowed', 'gpos/tools/paths.py',
     '    if _under(roots, _resolve_prefix(lexical)) is None:\n        return outside',
     '    if False:\n        return outside'),
    ('the record area becomes a write target', 'gpos/tools/artifacts.py',
     '        if tp.in_records(root, spec.path):', '        if False:'),
    ('artifact hash ignored', 'gpos/tools/artifacts.py',
     '        try:  # the foundation hashes the file; an adapter never supplies its own digest\n            digest, size = hash_file(path), path.stat().st_size',
     '        try:  # the foundation hashes the file; an adapter never supplies its own digest\n            digest, size = "0" * 64, path.stat().st_size'),
    ('a missing artifact is recorded anyway', 'gpos/tools/artifacts.py',
     '        if not path.is_file():', '        if False and not path.is_file():'),
    # --- provenance and result integrity
    ('provenance adapter id dropped', 'gpos/tools/provenance.py',
     '            "gpos_version": self.gpos_version, "adapter_id": self.adapter_id,',
     '            "gpos_version": self.gpos_version, "adapter_id": None,'),
    ('unknown provenance values are invented', 'gpos/tools/provenance.py',
     '    def unknown(self):\n        return tuple(name for name in CALLER_SUPPLIED if getattr(self, name) is None)',
     '    def unknown(self):\n        return ()'),
    ('a blocking diagnostic no longer downgrades the status', 'gpos/tools/diagnostics.py',
     '    classes = {d.cls for d in diags}\n    for cls in SEVERITY:',
     '    classes = set()\n    for cls in SEVERITY:'),
    ('an adapter may re-point a candidate at another subject', 'gpos/tools/execution.py',
     '        subject_kind=request.subject.kind, subject_ref=request.subject.ref,\n        subject_revision=request.subject.revision,',
     '        subject_kind=candidate.subject_kind or request.subject.kind, subject_ref=candidate.subject_ref,\n        subject_revision=candidate.subject_revision,'),
    ('all failures collapse into one status', 'gpos/tools/diagnostics.py',
     'EXIT_FOR = {SUCCESS: 0, INVALID_REQUEST: 1, FAILED: 2, TIMED_OUT: 3, UNAVAILABLE: 4,\n            CONFLICT: 5, INCOMPATIBLE: 6, CANCELLED: 7, INTERNAL_ERROR: 8}',
     'EXIT_FOR = {SUCCESS: 0, INVALID_REQUEST: 1, FAILED: 1, TIMED_OUT: 1, UNAVAILABLE: 1,\n            CONFLICT: 1, INCOMPATIBLE: 1, CANCELLED: 1, INTERNAL_ERROR: 1}'),
    # --- registry, lifecycle and preconditions
    ('a test-only adapter may be a production adapter', 'gpos/tools/validation.py',
     '    if test_markers and not allow_test_only:', '    if False:'),
    ('duplicate adapter ids accepted', 'gpos/tools/registry.py',
     '        if descriptor.adapter_id in self._adapters:', '        if False:'),
    ('duplicate capability ids accepted', 'gpos/tools/validation.py',
     '        if cap.id in seen:', '        if False:'),
    ('an adapter without a probe may register', 'gpos/tools/registry.py',
     '            if impl is None or impl is getattr(model.ToolAdapter, name):', '            if False:'),
    ('execution proceeds without a READY tool', 'gpos/tools/execution.py',
     '        probe, problems = registry.ready(request.adapter_id, started_at)\n        if problems:',
     '        probe, problems = registry.ready(request.adapter_id, started_at)\n        if False:'),
    ('a probe exception escapes as a crash', 'gpos/tools/registry.py',
     '        except Exception as exc:  # an adapter defect must not escape as a subprocess traceback',
     '        except KeyboardInterrupt as exc:'),
    ('an invalid project no longer blocks execution', 'gpos/tools/execution.py',
     '    if not result.valid:', '    if False:'),
    ('unrelated routing readiness blocks every capability', 'gpos/tools/execution.py',
     '    if capability.requires_ready_routing:', '    if True:'),
    ('unknown request inputs accepted', 'gpos/tools/execution.py',
     '    unknown = sorted(set(request.inputs or {}) - set(capability.input_kinds))',
     '    unknown = []'),
    # --- hardening round: integrity gaps closed after the independent code review
    ('extra_provenance allowed to override foundation truth', 'gpos/tools/evidence.py',
     '        if name in FOUNDATION_OWNED_PROVENANCE:', '        if False:'),
    ('a caller may supply provenance the execution never observed', 'gpos/tools/evidence.py',
     '            if known is None:', '            if False:'),
    ('any provenance field accepted as a supplement', 'gpos/tools/evidence.py',
     '        if name not in SUPPLEMENTAL_PROVENANCE:', '        if False:'),
    ('candidate provenance no longer reaches the record', 'gpos/tools/evidence.py',
     '    for name in FOUNDATION_OWNED_PROVENANCE:\n        value = known.get(name)',
     '    for name in ():\n        value = known.get(name)'),
    ('project-less project_root becomes a filesystem scope', 'gpos/tools/execution.py',
     '        if request.project_root:\n            return None, [dg.make("INVALID_TOOL_REQUEST",',
     '        if False:\n            return None, [dg.make("INVALID_TOOL_REQUEST",'),
    ('nested project id lookup broken', 'gpos/tools/execution.py',
     '    project_id = record_set.project_id if record_set is not None else None',
     '    project_id = None'),
    ('provenance duration taken from the process again', 'gpos/tools/execution.py',
     '    finished_at, duration = clock.now(), round(clock.monotonic() - started, 6)',
     '    finished_at, duration = clock.now(), round(process.duration_seconds if process else 0.0, 6)'),
    ('the adapter establishes evidence freshness', 'gpos/tools/execution.py',
     '        generated_at=generated_at, provenance=provenance.to_dict(), materializable=False)',
     '        provenance=provenance.to_dict(), materializable=False)'),
    ('invalid subject vocabulary accepted', 'gpos/tools/execution.py',
     '        if subject.kind not in reg["gate_scope_kinds"]:', '        if False:'),
    ('empty subject reference accepted', 'gpos/tools/execution.py',
     '        if not isinstance(subject.ref, str) or not subject.ref.strip():', '        if False:'),
    ('invalid actor accepted', 'gpos/tools/execution.py',
     '        if actor.kind not in reg["actor_kinds"]:', '        if False:'),
    ('invalid target platform accepted', 'gpos/tools/execution.py',
     '    if request.target_platform is not None and request.target_platform not in reg["platforms"]:',
     '    if False:'),
    ('impossible expected evidence accepted', 'gpos/tools/execution.py',
     '    for pair in request.expected_evidence or ():', '    for pair in ():'),
    ('request vocabulary never checked', 'gpos/tools/execution.py',
     '    problems = vocabulary_problems(framework, request, capability)\n    if problems:',
     '    problems = vocabulary_problems(framework, request, capability)\n    if False:'),
    ('adapter metadata redaction bypassed', 'gpos/tools/execution.py',
     '    outcome, redacted, unsupported = _sanitize_outcome(outcome, adapter_id, cap_id)',
     '    redacted, unsupported = 0, []'),
    ('credential-named keys no longer redacted', 'gpos/tools/redaction.py',
     '            if CREDENTIAL_NAME.match(key.strip()) and isinstance(item, str):',
     '            if False:'),
    ('credential-named flags no longer redact their value', 'gpos/tools/redaction.py',
     '            flagged = isinstance(item, str) and bool(CREDENTIAL_NAME.match(item.strip()))',
     '            flagged = False'),
    ('unsupported result values are stringified', 'gpos/tools/redaction.py',
     '    raise UnsupportedValue(f"{type(value).__name__} is not a value a ToolResult can carry")',
     '    return str(value), 0'),
    ('probe text is not redacted', 'gpos/tools/registry.py',
     '        result = _sanitized_probe(result)', '        pass'),
    ('artifact kind declaration ignored', 'gpos/tools/artifacts.py',
     '        elif spec.kind not in declared:', '        elif False:'),
    ('unknown registry artifact kind accepted', 'gpos/tools/artifacts.py',
     '        if spec.kind not in registry_kinds:', '        if False:'),
    ('duplicate artifact ids allowed', 'gpos/tools/artifacts.py',
     '        if aid in seen:', '        if False:'),
    ('an output artifact may shadow an input', 'gpos/tools/artifacts.py',
     '        if aid in inputs:', '        if False:'),
    ('artifact claim problems no longer refuse the artifact', 'gpos/tools/execution.py',
     '    refused = {aid for _, aid, _ in claim_problems}', '    refused = set()'),
    ('lease release failure ignored', 'gpos/tools/execution.py',
     '    if lease_mod.release(root, lease):\n        return []', '    if True:\n        return []'),
    ('a stranded lease still reports the original status', 'gpos/tools/execution.py',
     '    if problems:  # the status is recomputed: a stranded lease is never a clean SUCCESS',
     '    if False:  # the status is recomputed: a stranded lease is never a clean SUCCESS'),
    ('project-root lease identity uses the raw path', 'gpos/tools/execution.py',
     '    return f"{capability.resource_kind}:{Path(root).resolve()}", None',
     '    return f"{capability.resource_kind}:{str(root).rstrip(chr(47))}", None'),
    ('a request-named resource is no longer required', 'gpos/tools/execution.py',
     '        if not isinstance(request.resource_id, str) or not request.resource_id.strip():',
     '        if False:'),
    ('a process-start failure is an opaque defect again', 'gpos/tools/process.py',
     '    except (FileNotFoundError, PermissionError, NotADirectoryError, IsADirectoryError) as exc:',
     '    except NotADirectoryError as exc:'),
    ('dry run creates its workspace anyway', 'gpos/tools/execution.py',
     '    if request.dry_run:\n        return tuple(scopes), workspace, []  # a dry run resolves the plan and creates nothing',
     '    if False:\n        return tuple(scopes), workspace, []  # a dry run resolves the plan and creates nothing'),
    ('the caller-named scope is created before it is checked', 'gpos/tools/execution.py',
     '            scopes.insert(0, tp.resolve_without_creating(workspace))',
     '            workspace.mkdir(parents=True, exist_ok=True)\n            scopes.insert(0, workspace.resolve())'),
    ('workspace creation failure escapes as an exception', 'gpos/tools/execution.py',
     '    except OSError as exc:\n        return [dg.make("WORKSPACE_NOT_USABLE",',
     '    except UnicodeError as exc:\n        return [dg.make("WORKSPACE_NOT_USABLE",'),
    ('an output path that is a regular file is not refused', 'gpos/tools/execution.py',
     '    if workspace.exists() and not workspace.is_dir():', '    if False:'),
    ('a relative output directory is accepted', 'gpos/tools/execution.py',
     '        if not workspace.is_absolute():', '        if False:'),
    # --- Phase-2C-1 amendment: the private raw capture channel
    ('raw capture accidentally serialized into the result', 'gpos/tools/execution.py',
     '        diagnostics=tuple(diagnostics), data=outcome.data, plan=tuple(outcome.plan))',
     '        diagnostics=tuple(diagnostics), plan=tuple(outcome.plan),\n'
     '        data={**(outcome.data or {}), "raw": process.raw_stdout.decode("utf-8", "replace") if process else ""})'),
    ('public stdout no longer redacted', 'gpos/tools/process.py',
     '    out, out_n = redact(raw_out.decode("utf-8", errors="replace"))',
     '    out, out_n = raw_out.decode("utf-8", errors="replace"), 0'),
    ('raw buffer bypasses the capture limit', [
        ('gpos/tools/process.py', '            sink["total"] += len(chunk)\n',
         '            sink["total"] += len(chunk)\n            sink.setdefault("all", bytearray()).extend(chunk)\n'),
        ('gpos/tools/process.py',
         '    raw_out, raw_err = bytes(sinks[0]["data"]), bytes(sinks[1]["data"])  # the bounded capture, unredacted',
         '    raw_out, raw_err = bytes(sinks[0].get("all", sinks[0]["data"])), bytes(sinks[1].get("all", sinks[1]["data"]))')]),
    ('raw capture shown in repr', 'gpos/tools/process.py',
     '    raw_stdout: bytes = field(default=b"", repr=False)', '    raw_stdout: bytes = field(default=b"")'),
    ('the CLI packs path and context into one ambiguous value', 'gpos/tools/cli.py',
     '        artifact_id, path = item.split("=", 1)', '        artifact_id, path = item.split(":", 1)'),
]


def run(mutation):
    """A mutation is (name, file, anchor, replacement), or (name, [(file, anchor, replacement), ...]) when one
    realistic defect needs more than one edit."""
    name = mutation[0]
    edits = mutation[1] if len(mutation) == 2 else [tuple(mutation[1:])]
    tmp = Path(tempfile.mkdtemp(prefix="gpos-toolmut-"))
    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"))
        for rel, anchor, replacement in edits:
            path = copy / rel
            text = path.read_text()
            if text.count(anchor) != 1:
                return name, f"NOT APPLIED ({rel}: anchor found {text.count(anchor)} times)"
            path.write_text(text.replace(anchor, replacement))
        out = subprocess.run([sys.executable, "-B", str(copy / "tests" / "test_tool_foundation.py")],
                             capture_output=True, text=True,
                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=900)
        return name, "CAUGHT" if out.returncode != 0 else "MISSED"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=4)
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
