"""The records one routing's readiness depends on (Phase-2A scoped readiness).

Readiness of a routing is evaluated over:

* project-global authority: the project config and every decision it references
  (lifecycle, Golden Cell, quality target, presentation parity, editor concurrency,
  review-policy overrides, GPOS upgrade), including project triggers and reviewers;
* the routing record itself (every record carrying its task_id, so duplicates are seen);
* every gate linked to it (routing_ref), and every gate those gates cite as their Human Review;
* every evidence record those gates cite (evidence_refs, carryover, applicability);
* every decision referenced by the routing or those gates (e.g. blocking downgrades);
* every load-time problem (an unreadable or unparsable file cannot be attributed, so it
  is never assumed unrelated: fail-closed).

A referenced identifier pulls in every record carrying it, so a duplicate anywhere in the
project still makes the reference ambiguous. Records outside this scope (other routings,
their gates, unrelated evidence or decisions) never affect this routing's readiness; they
are reported by whole-project validation.

Selection reads raw record data defensively: records may be schema-invalid at this point.
"""

from ..records import RecordSet
from .decisions import ref_sites


def _str(value):
    return value if isinstance(value, str) else None


def _decision_refs(fw, schema_name, doc):
    refs = set()
    for key in fw.registry["decision_ref_fields"]:
        name, pointer = key.split(":", 1)
        if name == schema_name and isinstance(doc, dict):
            refs.update(ref for _, ref, _ in ref_sites(doc, pointer))
    return refs


def _evidence_refs(gate):
    refs = set()
    if isinstance(gate.get("evidence_refs"), list):
        refs.update(r for r in gate["evidence_refs"] if isinstance(r, str))
    for key in ("evidence_carryover", "evidence_applicability"):
        if isinstance(gate.get(key), list):
            refs.update(_str(e.get("evidence_ref")) for e in gate[key] if isinstance(e, dict))
    refs.discard(None)
    return refs


def routing_scope(record_set, routing_id, fw):
    """A RecordSet holding exactly the records `routing_id`'s readiness depends on."""
    rs = record_set
    routings = [r for r in rs.routings if r.data.get("task_id") == routing_id]
    gates = [g for g in rs.gates if g.data.get("routing_ref") == routing_id]
    gate_ids = {_str(g.data.get("gate_id")) for g in gates}
    gate_ids |= {_str(g.data.get("human_review_ref")) for g in gates}
    gate_ids.discard(None)
    gates = [g for g in rs.gates if g.data.get("routing_ref") == routing_id or _str(g.data.get("gate_id")) in gate_ids]

    evidence_ids = set()
    for g in gates:
        evidence_ids |= _evidence_refs(g.data)
    evidence = [e for e in rs.evidence if _str(e.data.get("evidence_id")) in evidence_ids]

    decision_ids = set()
    if rs.config is not None:
        decision_ids |= _decision_refs(fw, "project-config", rs.config.data)
    for r in routings:
        decision_ids |= _decision_refs(fw, "task-routing", r.data)
    for g in gates:
        decision_ids |= _decision_refs(fw, "gate", g.data)
    decisions = [d for d in rs.decisions if _str(d.data.get("decision_id")) in decision_ids]

    outside = {_str(r.data.get("task_id")) for r in rs.routings} - {routing_id, None}
    return RecordSet(config=rs.config, decisions=decisions, routings=routings, gates=gates, evidence=evidence,
                     root=rs.root, load_diagnostics=rs.load_diagnostics, outside_routing_ids=outside)
