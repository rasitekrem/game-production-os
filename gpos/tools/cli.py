"""`python3 -m gpos.tools list|describe|capabilities|probe|execute` — a thin CLI over the tool foundation.

    python3 -m gpos.tools list                                   registered tool adapters
    python3 -m gpos.tools describe  <adapter>                    descriptor and capabilities
    python3 -m gpos.tools capabilities <adapter>                 capabilities only
    python3 -m gpos.tools probe     <adapter>                    READ_ONLY availability probe
    python3 -m gpos.tools execute   --adapter A --capability C   one declared capability

`execute` takes an adapter id, a capability id and structured request fields. It does **not** take a
command line, an argument vector, an executable or a shell string, and there is no option that
accepts one: this CLI cannot run anything the adapter has not declared. Inputs are `--input
name=value` pairs restricted to the names the capability declares.

`list`, `describe`, `capabilities` and `probe` need no project and are never blocked on task
readiness. The listing shows the production adapters; `--include-test-adapters` adds the TEST_ONLY
synthetic reference adapter.

Exit codes follow the library status exactly (gpos.tools.diagnostics.EXIT_FOR): 0 SUCCESS,
1 INVALID_REQUEST, 2 FAILED, 3 TIMED_OUT, 4 UNAVAILABLE, 5 CONFLICT, 6 INCOMPATIBLE, 7 CANCELLED,
8 INTERNAL_ERROR. Nothing collapses into a single failure code.
"""

import argparse
import json
import sys

from .. import __version__
from ..framework import load_framework
from . import diagnostics as dg
from .execution import ExecutionRequest, execute
from .model import Actor, InputArtifact, Subject
from .redaction import sanitize
from .registry import ToolRegistry, register_production_adapters

TOOL = "gpos-tools"
USAGE_EXIT = dg.EXIT_FOR[dg.INVALID_REQUEST]


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def build_parser():
    parser = _Parser(prog="python3 -m gpos.tools",
                     description="GPOS tool adapter foundation: list, describe, probe and execute declared capabilities.")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--include-test-adapters", action="store_true",
                        help="also register the TEST_ONLY synthetic reference adapter (never a production adapter)")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    for name, helptext in (("list", "list registered tool adapters"),
                           ("describe", "show an adapter descriptor and its capabilities"),
                           ("capabilities", "show an adapter's capabilities"),
                           ("probe", "READ_ONLY: is the adapter's tool available and compatible?")):
        p = sub.add_parser(name, help=helptext, description=helptext)
        if name != "list":
            p.add_argument("adapter")
        p.add_argument("--format", choices=("text", "json"), default="text")
    ex = sub.add_parser("execute", help="run one declared capability", description="run one declared capability")
    ex.add_argument("--adapter", required=True)
    ex.add_argument("--capability", required=True)
    ex.add_argument("--project", help="GPOS project root")
    ex.add_argument("--subject-kind", default="TASK")
    ex.add_argument("--subject-ref", required=True)
    ex.add_argument("--subject-revision", help="the exact revision captured; omitted stays unknown, never invented")
    ex.add_argument("--input", action="append", default=[], metavar="NAME=VALUE",
                    help="a declared capability input; repeatable")
    ex.add_argument("--input-artifact", action="append", default=[], metavar="ID=PATH",
                    help="an existing artifact this execution consumes; repeatable")
    ex.add_argument("--input-artifact-context", action="append", default=[], metavar="ID=CONTEXT",
                    help="the capture context an input artifact came from, e.g. gameplay=TARGET_RUNTIME")
    ex.add_argument("--build-revision",
                    help="the exact build/source revision, e.g. from git.resolve-provenance; omitted stays unknown")
    ex.add_argument("--build-id", help="the build identifier; omitted stays unknown")
    ex.add_argument("--target-platform", help="a GPOS platform (registry vocabulary); omitted stays unknown")
    ex.add_argument("--device", help="the device the capture came from; omitted stays unknown")
    ex.add_argument("--output-dir")
    ex.add_argument("--resource", help="the single-writer target, for a capability that leases one the request names")
    ex.add_argument("--routing", dest="routing_ref")
    ex.add_argument("--actor", help="KIND:ID, e.g. HUMAN:ekrem or AGENT:reviewer-1")
    ex.add_argument("--timeout", type=float)
    ex.add_argument("--dry-run", action="store_true")
    ex.add_argument("--allow-mutation", action="store_true",
                    help="explicit consent for a MUTATING capability; without it, mutation is refused")
    ex.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _registry(include_test_adapters):
    registry = register_production_adapters(ToolRegistry(load_framework(), allow_test_only=include_test_adapters))
    if include_test_adapters:
        from .synthetic import SyntheticAdapter
        registry.register(SyntheticAdapter())
    return registry


def _pairs(items, what):
    out = {}
    for item in items:
        if "=" not in item:
            raise UsageError(f"{what} {item!r} must be NAME=VALUE")
        name, value = item.split("=", 1)
        out[name] = value
    return out


def _input_artifacts(items, contexts):
    """Input artifacts from `ID=PATH` plus separate `ID=CONTEXT` pairs.

    Path and capture context are separate options on purpose: packing them into one colon-separated
    value cannot be parsed unambiguously for a Windows path such as `C:\\capture.mp4`, and guessing by
    operating system would make the same command mean different things on different machines.
    """
    contexts = _pairs(contexts, "--input-artifact-context")
    out, seen = [], set()
    for item in items:
        if "=" not in item:
            raise UsageError(f"--input-artifact {item!r} must be ID=PATH")
        artifact_id, path = item.split("=", 1)
        if not artifact_id or not path:
            raise UsageError(f"--input-artifact {item!r} must be ID=PATH with both parts present")
        if artifact_id in seen:
            raise UsageError(f"--input-artifact {artifact_id!r} is given more than once")
        seen.add(artifact_id)
        out.append(InputArtifact(artifact_id, path, capture_context=contexts.pop(artifact_id, None)))
    if contexts:
        raise UsageError(f"--input-artifact-context names artifacts that were not supplied: {sorted(contexts)}")
    return tuple(out)


def _dump(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)


def _describe_text(descriptor):
    d = descriptor
    lines = [f"{d.adapter_id} {d.adapter_version} · {d.tool_family} · {d.adapter_kind} · {d.state_model}",
             f"  tool {d.target_tool}" + (f" >= {d.minimum_tool_version}" if d.minimum_tool_version else ""),
             f"  platforms {', '.join(d.supported_platforms)} · network {d.network}"
             + ("  · TEST_ONLY: never a production adapter" if d.test_only else "")]
    if d.availability:
        lines.append(f"  availability: {d.availability}")
    for note in d.compatibility_notes:
        lines.append(f"  note: {note}")
    return lines + _capabilities_text(d)


def _capabilities_text(descriptor):
    lines = []
    for cap in sorted(descriptor.capabilities, key=lambda c: c.id):
        flags = [cap.operation_class, cap.state_model, f"observes {cap.execution_context}"]
        if cap.single_writer_required:
            flags.append(f"single writer on {cap.resource_kind}")
        if cap.dry_run_supported:
            flags.append("dry run")
        lines.append(f"  {cap.id}  [{cap.category}] {' · '.join(flags)}")
        lines.append(f"      {cap.description}")
        if cap.mutating:
            lines.append(f"      side effects: {cap.side_effect_scope}")
        if cap.potential_evidence:
            lines.append("      may offer: " + ", ".join(f"{t} in {c}" for t, c in cap.potential_evidence))
        lines.append(f"      timeout: {cap.timeout.default}s default, {cap.timeout.maximum}s maximum")
    return lines


def _result_text(result):
    lines = [f"{TOOL} {__version__} · {result.adapter_id} {result.capability_id}: {result.status}"
             f" · request {result.request_id}",
             f"  dry_run={result.dry_run} mutation_performed={result.mutation_performed} "
             f"exit_code={result.exit_code} duration={result.duration_seconds}s"]
    for a in result.artifacts:
        lines.append(f"  artifact {a.artifact_id} [{a.kind}/{a.classification}] {a.sha256[:16]} {a.bytes} B  {a.path}")
        if a.derived_from:
            lines.append(f"      derived from {', '.join(a.derived_from)} · captured in {a.origin_capture_context}")
    for c in result.evidence_candidates:
        lines.append(f"  candidate {c.evidence_type} captured in {c.capture_context} "
                     f"(materializable={c.materializable}) · {c.summary}")
        for limitation in c.limitations:
            lines.append(f"      limitation: {limitation}")
    for step in result.plan:
        lines.append(f"  plan: {step}")
    for d in result.diagnostics:
        lines.append(f"  {d.cls:<15} {d.code}\n      {d.message}")
    lines.append("  a tool result is not a gate verdict: evidence candidates are offers that a human or the validator "
                 "judges.")
    return "\n".join(lines)


def main(argv=None, stdout=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = stdout or sys.stdout
    fmt = "json" if "json" in argv else "text"
    try:
        args = build_parser().parse_args(argv)
        if args.version:
            print(f"{TOOL} {__version__} (tool adapter foundation)", file=stdout)
            return 0
        if args.command is None:
            raise UsageError("a command is required: list, describe, capabilities, probe or execute")
        registry = _registry(args.include_test_adapters)
        fmt = args.format
        if args.command == "execute":
            return _execute(args, registry, fmt, stdout)
        return _read_only(args, registry, fmt, stdout)
    except UsageError as exc:
        return _error(fmt, "USAGE_ERROR", str(exc), stdout)
    except Exception as exc:  # a foundation defect is INTERNAL_ERROR, never a clean verdict
        return _error(fmt, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", stdout,
                      dg.EXIT_FOR[dg.INTERNAL_ERROR], dg.INTERNAL_ERROR)


def _read_only(args, registry, fmt, stdout):
    if args.command == "list":
        descriptors = registry.list_adapters()
        if fmt == "json":
            print(_dump({"tool": TOOL, "version": __version__, "adapters": [d.to_dict() for d in descriptors]}),
                  file=stdout)
        else:
            print(f"{TOOL} {__version__}: {len(descriptors)} tool adapter(s)", file=stdout)
            for d in descriptors:
                print(f"  {d.adapter_id} {d.adapter_version} · {d.tool_family} · {len(d.capabilities)} capabilities"
                      + ("  · TEST_ONLY" if d.test_only else ""), file=stdout)
            if not descriptors:
                print("  none registered", file=stdout)
        return 0
    adapter = registry.get(args.adapter)
    if adapter is None:
        return _error(fmt, "ADAPTER_NOT_FOUND", f"no tool adapter {args.adapter!r} is registered", stdout,
                      dg.EXIT_FOR[dg.INVALID_REQUEST], dg.INVALID_REQUEST)
    if args.command == "probe":
        result = registry.probe(args.adapter)
        if fmt == "json":
            print(_dump({"tool": TOOL, "version": __version__, "probe": result.to_dict()}), file=stdout)
        else:
            print(f"{args.adapter}: {result.status} (state {result.state})\n  {result.detail}", file=stdout)
            if result.tool_path:
                print(f"  tool {result.tool_version or 'unknown version'} at {result.tool_path}", file=stdout)
        return 0 if result.available else dg.EXIT_FOR[dg.UNAVAILABLE if result.status == "UNAVAILABLE"
                                                      else dg.INCOMPATIBLE]
    descriptor = adapter.descriptor
    if fmt == "json":
        payload = descriptor.to_dict() if args.command == "describe" else \
            {"adapter_id": descriptor.adapter_id, "capabilities": [c.to_dict() for c in descriptor.capabilities]}
        print(_dump({"tool": TOOL, "version": __version__, args.command: payload}), file=stdout)
    else:
        lines = _describe_text(descriptor) if args.command == "describe" else \
            [descriptor.adapter_id] + _capabilities_text(descriptor)
        print("\n".join(lines), file=stdout)
    return 0


def _execute(args, registry, fmt, stdout):
    actor = None
    if args.actor:
        if ":" not in args.actor:
            raise UsageError("--actor must be KIND:ID, e.g. HUMAN:ekrem or AGENT:reviewer-1")
        kind, _, ident = args.actor.partition(":")
        actor = Actor(kind, ident)
    request = ExecutionRequest(
        adapter_id=args.adapter, capability_id=args.capability,
        subject=Subject(args.subject_kind, args.subject_ref, args.subject_revision),
        project_root=args.project, inputs=_pairs(args.input, "--input"),
        input_artifacts=_input_artifacts(args.input_artifact, args.input_artifact_context),
        output_dir=args.output_dir, resource_id=args.resource,
        dry_run=args.dry_run, allow_mutation=args.allow_mutation, timeout=args.timeout,
        actor=actor, routing_ref=args.routing_ref,
        # Passed through unchanged: the foundation validates them, and an omitted value stays unknown.
        build_revision=args.build_revision, build_id=args.build_id,
        target_platform=args.target_platform, device=args.device)
    result = execute(registry, request)
    if fmt == "json":
        # The request is echoed through the same redaction as the result: an input artifact's file name is
        # caller-supplied text and may be credential-shaped.
        echoed, _ = sanitize(request.to_dict())
        print(_dump({"tool": TOOL, "version": __version__, "request": echoed,
                     "result": result.to_dict()}), file=stdout)
    else:
        print(_result_text(result), file=stdout)
    return result.exit_code_for_cli


def _error(fmt, code, message, stream, exit_code=USAGE_EXIT, status=dg.INVALID_REQUEST):
    if fmt == "json":
        print(_dump({"tool": TOOL, "version": __version__, "status": status, "exit_code": exit_code,
                     "error": {"code": code, "message": message}}), file=stream)
    else:
        print(f"{TOOL}: error [{code}]: {message}", file=sys.stderr)
    return exit_code
