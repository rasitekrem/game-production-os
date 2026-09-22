"""Thin command-line interface over the gpos library. Read-only: no command changes any record.

    python3 -m gpos.validator validate  --project PATH [--routing ID] [--format text|json]
    python3 -m gpos.validator readiness --project PATH --routing ID   [--format text|json]
    python3 -m gpos.validator --version

Exit codes:
    0  records valid (validate) / routing READY (readiness)
    1  records invalid
    2  records valid but routing NOT READY (readiness only)
    3  invocation, tool, compatibility (UNSUPPORTED_GPOS_VERSION) or internal error — never a verdict about records
"""

import argparse
import json
import sys

from . import __version__
from .diagnostics import BLOCKER, ERROR, INFO, WARNING
from .errors import GposToolError
from .framework import load_framework
from .records import load_project
from .validation.project import evaluate_readiness, validate_project, validate_routing

EXIT_OK, EXIT_INVALID, EXIT_NOT_READY, EXIT_ERROR = 0, 1, 2, 3
TOOL = "gpos-validator"
VERDICTS = ("VALID", "INVALID", "READY", "NOT_READY", "INCOMPATIBLE", "ERROR")
# verdict -> exit code; one verdict never shares an exit code with another result class
EXIT_FOR = {"VALID": 0, "READY": 0, "INVALID": 1, "NOT_READY": 2, "INCOMPATIBLE": 3, "ERROR": 3}
CLI_ERROR_CODES = ("USAGE_ERROR", "INTERNAL_ERROR")


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse would exit 2, which means NOT READY here
        raise UsageError(message)


def build_parser():
    parser = _Parser(prog="python3 -m gpos.validator",
                     description="GPOS production validator (read-only). Checks project records; makes no creative decisions.")
    parser.add_argument("--version", action="store_true", help="print validator and GPOS versions and exit")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    for name, helptext in (("validate", "record validity of the whole project (exit 0 valid, 1 invalid)"),
                           ("readiness", "routed readiness of one routing (exit 0 READY, 1 invalid records, 2 NOT READY)")):
        p = sub.add_parser(name, help=helptext, description=helptext)
        p.add_argument("--project", required=True, help="project directory (containing .game/gpos/) or the bundle directory")
        p.add_argument("--routing", required=(name == "readiness"), help="routing task_id")
        p.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _dump(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)


def _envelope(command, fw_version, project_id, verdict, exit_code):
    return {"tool": TOOL, "validator_version": __version__, "gpos_version": fw_version, "project_id": project_id,
            "command": command, "verdict": verdict, "exit_code": exit_code}


def _diag_line(d):
    where = " ".join(x for x in (d.record_type, d.record_id, d.path) if x)
    file = f" [{d.file}]" if d.file else ""
    return f"  {d.severity:<7} {d.code}  {where}{file}\n          {d.message}  ({d.rule})"


def _text_counts(counts):
    return ", ".join(f"{counts[s]} {s.lower()}" for s in (ERROR, BLOCKER, WARNING, INFO) if counts.get(s))


def _header(fw_version, project_id):
    return f"{TOOL} {__version__} · GPOS {fw_version} · project {project_id or '(unknown)'}"


def run_validate(args, fw):
    rs = load_project(args.project)
    result = validate_routing(rs, args.routing, fw) if args.routing else validate_project(rs, fw)
    verdict = result.status
    code = EXIT_FOR[verdict]
    if args.format == "json":
        return code, _dump({**_envelope("validate", fw.version, rs.project_id, verdict, code), **result.to_dict()})
    lines = [_header(fw.version, rs.project_id)]
    recs = result.summary["records"]
    scope = f" (routing {args.routing}; {result.summary['errors_elsewhere']} error(s) elsewhere)" if args.routing else ""
    lines.append(f"validate: {verdict}{scope}")
    lines.append("records: " + ", ".join(f"{n} {t}" for t, n in recs.items()))
    if result.summary["counts"] and any(result.summary["counts"].values()):
        lines.append("diagnostics: " + _text_counts(result.summary["counts"]))
    lines += [_diag_line(d) for d in result.diagnostics]
    return code, "\n".join(lines)


def run_readiness(args, fw):
    rs = load_project(args.project)
    result = evaluate_readiness(rs, args.routing, fw)
    verdict = result.status  # READY, INVALID or NOT_READY — never "NOT_READY" for invalid records
    code = EXIT_FOR[verdict]
    if args.format == "json":
        return code, _dump({**_envelope("readiness", fw.version, rs.project_id, verdict, code), **result.to_dict()})
    overview = result.summary["routing"]
    subject = overview.get("subject") or {}
    lines = [_header(fw.version, rs.project_id),
             f"readiness {args.routing}: {verdict.replace('_', ' ')}"
             + ("" if result.valid_records else " (errors in the records this routing depends on; fix them first)"),
             f"workflow {overview.get('workflow')} · subject {subject.get('kind')} {subject.get('ref')}",
             "required gates:"]
    for g in overview["required_gates"]:
        lines.append(f"  {g['gate']:<19} {'blocking' if g['blocking'] else 'non-blocking':<12} {g['status']:<16} "
                     f"{', '.join(g['gate_ids']) or '-'}")
    if result.blocking_reasons:
        lines.append("blocking reasons:")
        lines += [_diag_line(d) for d in result.blocking_reasons]
    others = [d for d in result.diagnostics if d not in result.blocking_reasons]
    if others:
        lines.append("other diagnostics:")
        lines += [_diag_line(d) for d in others]
    if result.project_has_other_diagnostics:
        lines.append("note: records outside this routing's scope have diagnostics (they do not affect this verdict); "
                     "run `validate` for the whole project")
    return code, "\n".join(lines)


def _error(argv_format, code, message, stream, diagnostic=None, verdict="ERROR"):
    if argv_format == "json":
        error = {"code": code, "message": message}
        if diagnostic is not None:
            error["diagnostic"] = diagnostic.to_dict()
        print(_dump({"tool": TOOL, "validator_version": __version__, "verdict": verdict, "exit_code": EXIT_FOR[verdict],
                     "error": error}), file=stream)
    else:
        label = "incompatible" if verdict == "INCOMPATIBLE" else "error"
        print(f"{TOOL}: {label} [{code}]: {message}", file=sys.stderr)
    return EXIT_ERROR


def main(argv=None, stdout=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = stdout or sys.stdout
    fmt = "json" if "json" in [a.split("=", 1)[-1] for a in argv if a.startswith("--format")] + \
        [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--format"] else "text"
    try:
        args = build_parser().parse_args(argv)
        fw = load_framework()
        if args.version:
            if fmt == "json":
                print(_dump({"tool": TOOL, "validator_version": __version__, "gpos_version": fw.version}), file=stdout)
            else:
                print(f"{TOOL} {__version__} (GPOS {fw.version})", file=stdout)
            return EXIT_OK
        if args.command is None:
            raise UsageError("a command is required: validate or readiness")
        code, output = (run_validate if args.command == "validate" else run_readiness)(args, fw)
        print(output, file=stdout)
        return code
    except UsageError as exc:
        return _error(fmt, "USAGE_ERROR", str(exc), stdout)
    except GposToolError as exc:
        return _error(fmt, exc.code, str(exc), stdout, getattr(exc, "diagnostic", None), getattr(exc, "status", "ERROR"))
    except Exception as exc:  # a validator defect: exit 3, never a PASS/READY/NOT READY verdict
        return _error(fmt, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", stdout)
