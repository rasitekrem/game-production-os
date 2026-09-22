"""Orchestration: record validity for a whole project, and routed readiness.

Stages run in a fixed order; a later stage runs only on a record set the earlier stages
accepted, because cross-record rules are defined over schema-valid records with unique ids:

    1. load        (files, JSON)                       -> ERROR
    2. schema      (five GPOS schemas, RFC 3339)       -> ERROR
    3. version     (project pins this framework)       -> ERROR
    4. identifiers (no duplicate ids, before indexing) -> ERROR
    5. cross-record rules and routed readiness         -> ERROR / BLOCKER / INFO

Readiness is fail-closed: a routing is READY only when the whole record set has no ERROR
and that routing has no BLOCKER.
"""

from dataclasses import dataclass, field

from .. import diagnostics as dg
from ..errors import RoutingNotFound
from ..framework import load_framework
from ..records import SCHEMA_FOR_TYPE
from . import authority, decisions, human, ids, linkage, routing as routing_rules
from .context import Context

STAGES = ("load", "schema", "version", "identifiers", "cross-record")
# Required-gate states in the readiness overview besides gate statuses.
OVERVIEW_STATES = ("NOT_RUN", "AMBIGUOUS", "UNVERIFIED")


@dataclass
class Analysis:
    framework: object
    record_set: object
    stages_completed: list = field(default_factory=list)
    errors: list = field(default_factory=list)  # ERROR / WARNING / INFO diagnostics about records
    routing_diagnostics: dict = field(default_factory=dict)  # task_id -> BLOCKER / INFO readiness diagnostics
    context: object = None

    @property
    def valid(self):
        return not any(d.severity == dg.ERROR for d in self.errors)


def _schema_stage(fw, rs):
    out = []
    for rec in rs.all_records():
        validator = fw.validators[SCHEMA_FOR_TYPE[rec.type]]
        for e in validator.errors(rec.data):
            out.append(dg.make("SCHEMA_INVALID", f"{rec.file or rec.type}: {e.path}: {e.message}", record_type=rec.type,
                               record_id=rec.id, path=e.path, file=rec.file, details={"keyword": e.keyword}))
    return out


def analyze(record_set, framework=None):
    """Run every stage the record set allows. Never mutates the record set."""
    fw = framework or load_framework()
    rs = record_set
    an = Analysis(framework=fw, record_set=rs)
    an.errors += rs.load_diagnostics
    if rs.config is None or not an.valid:
        return _finish(an)
    an.stages_completed.append("load")

    an.errors += _schema_stage(fw, rs)
    if not an.valid:
        return _finish(an)
    an.stages_completed.append("schema")

    config = rs.config.data
    if config["gpos_version"] != fw.version:
        an.errors.append(dg.make("UNSUPPORTED_GPOS_VERSION", f"project pins GPOS {config['gpos_version']}; this validator implements "
                                 f"GPOS {fw.version} and validates only records of that version", record_type="project-config",
                                 record_id=rs.project_id, path="/gpos_version", file=rs.config.file,
                                 details={"project": config["gpos_version"], "validator": fw.version}))
        return _finish(an)
    an.stages_completed.append("version")

    by_type = {"decision": rs.decisions, "routing": rs.routings, "gate": rs.gates, "evidence": rs.evidence}
    an.errors += ids.record_id_problems(by_type)
    an.errors += ids.config_id_problems(config)
    if not an.valid:
        return _finish(an)
    an.stages_completed.append("identifiers")

    ctx = Context(fw, config, [r.data for r in rs.decisions], [r.data for r in rs.routings],
                  [r.data for r in rs.gates], [r.data for r in rs.evidence])
    an.context = ctx
    errs = an.errors
    project_id = config["project"]["id"]
    errs += authority.project_authority(fw, config, ctx.decisions_by_id)
    errs += decisions.resolve_decision_refs(fw, "project-config", "project-config", project_id, config, ctx.decisions_by_id, config)
    errs += authority.human_evidence_sources(config, ctx.evidence)
    for r in ctx.routings:
        task = r["task_id"]
        errs += routing_rules.routing_structure(fw, r)
        errs += authority.routing_authority(fw, config, r)
        errs += decisions.resolve_decision_refs(fw, "task-routing", "routing", task, r, ctx.decisions_by_id, config)
        link_errors, link_blockers = linkage.routing_linkage(ctx, r)
        errs += link_errors
        an.routing_diagnostics[task] = (link_blockers + linkage.release_coverage(ctx, r) + linkage.gate_completion(ctx, r))
    for g in ctx.gates:
        gid = g["gate_id"]
        errs += decisions.resolve_decision_refs(fw, "gate", "gate", gid, g, ctx.decisions_by_id, config)
        if g.get("routing_ref") and g["routing_ref"] not in ctx.routings_by_id:
            errs.append(dg.make("ROUTING_REF_NOT_FOUND", f"{gid}: routing_ref {g['routing_ref']} does not resolve to a routing record",
                                record_type="gate", record_id=gid, path="/routing_ref", related=[g["routing_ref"]]))
        errs += ctx.gate_assessment(g)[0]
        errs += human.gate_human_authority(ctx, g)
    an.stages_completed.append("cross-record")
    return _finish(an)


def _finish(an):
    files = an.record_set.file_index()

    def attach(diags):
        return dg.sort_diagnostics([d if d.file or not d.record_type else dg.with_file(d, files.get((d.record_type, d.record_id)))
                                    for d in diags])

    an.errors = attach(an.errors)
    an.routing_diagnostics = {k: attach(v) for k, v in an.routing_diagnostics.items()}
    return an


# ---------------------------------------------------------------- public results

@dataclass
class ValidationResult:
    valid: bool
    diagnostics: list
    summary: dict

    def to_dict(self):
        return {"valid": self.valid, "summary": self.summary, "diagnostics": [d.to_dict() for d in self.diagnostics]}


@dataclass
class ReadinessResult:
    routing_id: str
    valid_records: bool
    ready: bool
    blocking_reasons: list
    diagnostics: list
    summary: dict

    def to_dict(self):
        return {"routing_id": self.routing_id, "valid_records": self.valid_records, "ready": self.ready,
                "blocking_reasons": [d.to_dict() for d in self.blocking_reasons],
                "summary": self.summary, "diagnostics": [d.to_dict() for d in self.diagnostics]}


def _counts(diags):
    return {sev: sum(1 for d in diags if d.severity == sev) for sev in (dg.ERROR, dg.BLOCKER, dg.WARNING, dg.INFO)}


def _summary(an):
    rs = an.record_set
    config = rs.config.data if rs.config is not None else {}
    return {
        "project_id": rs.project_id,
        "project_gpos_version": config.get("gpos_version") if isinstance(config, dict) else None,
        "records": {"project-config": 1 if rs.config is not None else 0, "decision": len(rs.decisions),
                    "routing": len(rs.routings), "gate": len(rs.gates), "evidence": len(rs.evidence)},
        "stages_completed": list(an.stages_completed),
    }


def validate_project(record_set, framework=None):
    """Record validity of the whole project. Readiness blockers are not record errors and are not included."""
    an = analyze(record_set, framework)
    summary = _summary(an)
    summary["counts"] = _counts(an.errors)
    return ValidationResult(valid=an.valid, diagnostics=an.errors, summary=summary)


def _routing_record(record_set, routing_id):
    """The routing record, None when it may be among files that could not be loaded, else RoutingNotFound."""
    matches = [r for r in record_set.routings if r.data.get("task_id") == routing_id]
    if matches:
        return matches[0]
    if record_set.config is None or any(d.record_type in (None, "routing") for d in record_set.load_diagnostics):
        return None  # unreadable routing files: report the invalid record set, never "not found"
    raise RoutingNotFound(f"no routing record with task_id {routing_id!r}")


def _related_to_routing(an, routing_id):
    """Record errors that concern this routing: the routing, its linked gates and their cited evidence,
    project config and decisions."""
    ctx = an.context
    gates = {g["gate_id"] for g in ctx.gates_by_routing.get(routing_id, [])} if ctx else set()
    evidence = {ref for gid in gates for ref in ctx.gates_by_id[gid]["evidence_refs"]} if ctx else set()
    keep = []
    for d in an.errors:
        if d.record_type in (None, "project-config", "decision"):
            keep.append(d)
        elif d.record_type == "routing" and d.record_id == routing_id:
            keep.append(d)
        elif d.record_type == "gate" and (d.record_id in gates or not ctx):
            keep.append(d)
        elif d.record_type == "evidence" and (d.record_id in evidence or not ctx):
            keep.append(d)
    return keep


def validate_routing(record_set, routing_id, framework=None):
    """Record validity as seen from one routing. `valid` is whole-project validity (fail-closed);
    diagnostics are those concerning this routing, its linked gates, their evidence, project config and decisions."""
    _routing_record(record_set, routing_id)
    an = analyze(record_set, framework)
    related = _related_to_routing(an, routing_id)
    summary = _summary(an)
    summary["routing_id"] = routing_id
    summary["counts"] = _counts(related)
    summary["errors_elsewhere"] = sum(1 for d in an.errors if d.severity == dg.ERROR) - summary["counts"][dg.ERROR]
    return ValidationResult(valid=an.valid, diagnostics=related, summary=summary)


def evaluate_readiness(record_set, routing_id, framework=None):
    """Routing-aware readiness. READY only when the whole record set is valid and this routing has no blocker."""
    rec = _routing_record(record_set, routing_id)
    an = analyze(record_set, framework)
    routing_diags = an.routing_diagnostics.get(routing_id, [])
    blockers = [d for d in routing_diags if d.severity == dg.BLOCKER]
    if not an.valid:
        n = sum(1 for d in an.errors if d.severity == dg.ERROR)
        blockers = dg.sort_diagnostics(blockers + [dg.make(
            "RECORD_SET_INVALID", f"the project record set has {n} error(s); no routed scope can be READY until they are fixed",
            record_type="routing", record_id=routing_id, file=rec.file if rec else None, details={"errors": n})])
    ready = rec is not None and an.valid and "cross-record" in an.stages_completed and not blockers
    summary = _summary(an)
    summary["counts"] = _counts(an.errors + routing_diags)
    summary["routing"] = _routing_overview(an, rec.data if rec else {"task_id": routing_id})
    return ReadinessResult(routing_id=routing_id, valid_records=an.valid, ready=ready, blocking_reasons=blockers,
                           diagnostics=dg.sort_diagnostics(an.errors + routing_diags), summary=summary)


def _routing_overview(an, routing):
    """Workflow, subject and the state of each required gate (NOT_RUN when no record exists)."""
    linked = {}
    if an.context is not None:
        for g in an.context.gates_by_routing.get(routing.get("task_id"), []):
            linked.setdefault(g["gate"], []).append(g)
    gates = []
    for item in routing.get("required_gates", []) if isinstance(routing.get("required_gates"), list) else []:
        if not isinstance(item, dict):
            continue
        recs = linked.get(item.get("gate"), [])
        gates.append({"gate": item.get("gate"), "blocking": item.get("blocking"),
                      "gate_ids": sorted(g["gate_id"] for g in recs),
                      "status": ("UNVERIFIED" if an.context is None else
                                 recs[0]["status"] if len(recs) == 1 else ("NOT_RUN" if not recs else "AMBIGUOUS"))})
    return {"workflow": routing.get("workflow"), "subject": routing.get("subject"), "required_gates": gates}
