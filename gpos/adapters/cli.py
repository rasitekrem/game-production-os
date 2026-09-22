"""`python3 -m gpos.adapters render|sync|check` — thin CLI over gpos.adapters.pipeline.

    python3 -m gpos.adapters render --project PATH [--agent claude-code|codex|all] [--out DIR] [--format text|json]
    python3 -m gpos.adapters sync   --project PATH [--agent claude-code|codex|all] [--repair] [--format text|json]
    python3 -m gpos.adapters check  --project PATH [--agent claude-code|codex|all] [--format text|json]

Exit codes: 0 OK / clean, 1 INVALID project or adapter settings, 2 DRIFT, 3 ERROR (invocation, tool,
incompatible GPOS version, internal), 4 CONFLICT (sync refused; nothing written).
"""

import argparse
import json
import sys

from .. import __version__
from . import diagnostics as dg
from .backends import ADAPTER_LAYER_VERSION, BACKENDS
from .pipeline import check, render, sync

TOOL = "gpos-adapters"


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse would exit 2, which means DRIFT here
        raise UsageError(message)


def build_parser():
    parser = _Parser(prog="python3 -m gpos.adapters",
                     description="GPOS agent adapters: render, sync and check generated agent instructions.")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    for name, helptext in (("render", "compile and render without touching the project (optionally into --out)"),
                           ("sync", "update the project's GPOS-managed agent files"),
                           ("check", "read-only drift detection")):
        p = sub.add_parser(name, help=helptext, description=helptext)
        p.add_argument("--project", required=True, help="project root (the directory containing .game/)")
        p.add_argument("--agent", default="all", choices=sorted(BACKENDS) + ["all"])
        p.add_argument("--format", choices=("text", "json"), default="text")
        if name == "render":
            p.add_argument("--out", help="new or empty directory to write the rendered bundles into")
        if name == "sync":
            p.add_argument("--repair", action="store_true", help="overwrite or delete generated files that were edited by hand")
    return parser


def _dump(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)


def _text(result):
    lines = [f"{TOOL} {__version__} · {result.command}: {result.status}" + (f" · project {result.project_id}" if result.project_id else "")]
    for agent, info in sorted(result.agents.items()):
        files = info.get("files")
        count = len(files) if isinstance(files, list) else files
        lines.append(f"  {agent}: {count} files · semantic hash {info['semantic_hash'][:16]}")
        if isinstance(files, list):
            lines += [f"    {f['sha256'][:12]}  {f['path']}" for f in files]
    for d in result.diagnostics:
        where = " ".join(x for x in (d.agent, d.path) if x)
        lines.append(f"  {d.cls:<8} {d.code}  {where}\n           {d.message}")
    return "\n".join(lines)


def main(argv=None, stdout=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = stdout or sys.stdout
    fmt = "json" if "json" in argv else "text"
    try:
        args = build_parser().parse_args(argv)
        if args.version:
            print(f"{TOOL} {__version__} (adapter layer {ADAPTER_LAYER_VERSION}; adapters {', '.join(sorted(BACKENDS))})", file=stdout)
            return 0
        if args.command is None:
            raise UsageError("a command is required: render, sync or check")
        if args.command == "render":
            result = render(args.project, args.agent, args.out)
        elif args.command == "sync":
            result = sync(args.project, args.agent, args.repair)
        else:
            result = check(args.project, args.agent)
    except UsageError as exc:
        return _error(fmt, "USAGE_ERROR", str(exc), stdout)
    except Exception as exc:  # adapter defect: exit 3, never a clean/synced verdict
        return _error(fmt, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", stdout)
    if fmt == "json":
        print(_dump({"tool": TOOL, "version": __version__, "command": result.command, "project_id": result.project_id,
                     "status": result.status, "exit_code": result.exit_code, "agents": result.agents,
                     "diagnostics": [d.to_dict() for d in result.diagnostics]}), file=stdout)
    else:
        print(_text(result), file=stdout)
    return result.exit_code


def _error(fmt, code, message, stream):
    if fmt == "json":
        print(_dump({"tool": TOOL, "version": __version__, "status": dg.ERROR, "exit_code": 3,
                     "error": {"code": code, "message": message}}), file=stream)
    else:
        print(f"{TOOL}: error [{code}]: {message}", file=sys.stderr)
    return 3
