"""Shared, read-only indexes for one validation run.

Indexes are built only after duplicate-id checks have passed (gpos.validation.ids), so no
lookup ever resolves an ambiguous identifier by last-write-wins.
"""


class Context:
    def __init__(self, fw, config, decisions, routings, gates, evidence):
        self.fw = fw
        self.config = config
        self.decisions = decisions
        self.routings = routings
        self.gates = gates
        self.evidence = evidence
        self.decisions_by_id = {d["decision_id"]: d for d in decisions}
        self.evidence_by_id = {e["evidence_id"]: e for e in evidence}
        self.gates_by_id = {g["gate_id"]: g for g in gates}
        self.routings_by_id = {r["task_id"]: r for r in routings}
        self.gates_by_routing = {}
        for g in gates:
            if g.get("routing_ref") is not None:
                self.gates_by_routing.setdefault(g["routing_ref"], []).append(g)
        self._assessments = {}

    def gate_assessment(self, gate):
        """Cached (diagnostics, counting evidence) for a gate under project config."""
        from .gates import assess_gate  # local import: gates does not depend on Context

        key = id(gate)
        if key not in self._assessments:
            self._assessments[key] = assess_gate(self.fw, gate, self.evidence_by_id, self.config)
        return self._assessments[key]
