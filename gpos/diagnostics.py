"""Structured, deterministic diagnostics.

Severity and category separate two different questions:

* ERROR   (category LOAD or RECORD)  — the record set is not valid: a record is malformed,
  contradicts another record or project authority, or claims a status its own reviews and
  evidence do not support under the registry rules.
* BLOCKER (category READINESS)       — the records are valid, but a routed scope has not
  reached the bar its routing demands: a required gate is missing or not PASS, routed
  evidence or a routed reviewer is missing, PRIMARY platform coverage is missing.
* WARNING / INFO                      — reported, never decisive.

Every code has one fixed severity, category and rule reference (CODES). The rule
reference names the frozen contract it enforces, e.g. "GOVERNANCE §12.23".
"""

import json
from dataclasses import dataclass

ERROR, BLOCKER, WARNING, INFO = "ERROR", "BLOCKER", "WARNING", "INFO"
SEVERITY_ORDER = {ERROR: 0, BLOCKER: 1, WARNING: 2, INFO: 3}
LOAD, RECORD, READINESS = "LOAD", "RECORD", "READINESS"


def _g(*numbers):
    return "GOVERNANCE §12." + "/".join(str(n) for n in numbers)


# code: (severity, category, rule reference, short title)
CODES = {
    # ---- loading and structure
    "PROJECT_CONFIG_MISSING": (ERROR, LOAD, "validator/README bundle layout", "project-config.json is missing"),
    "RECORD_UNREADABLE": (ERROR, LOAD, "validator/README bundle layout", "record file cannot be read"),
    "RECORD_INVALID_JSON": (ERROR, LOAD, "validator/README bundle layout", "record file is not valid JSON"),
    "RECORD_NOT_OBJECT": (ERROR, LOAD, "validator/README bundle layout", "record file does not contain one JSON object"),
    "UNKNOWN_RECORD_FILE": (ERROR, LOAD, "validator/README bundle layout", "unexpected file or directory in the bundle"),
    "UNSUPPORTED_GPOS_VERSION": (ERROR, LOAD, _g(7), "project pins a GPOS version this validator does not implement"),
    "SCHEMA_INVALID": (ERROR, RECORD, _g(7, 18), "record does not conform to its GPOS schema"),
    "DUPLICATE_RECORD_ID": (ERROR, RECORD, _g(15), "record id is not unique"),
    "DUPLICATE_CONFIG_ID": (ERROR, RECORD, _g(15), "project-config id is not unique"),
    # ---- decisions and project authority
    "DECISION_REF_NOT_FOUND": (ERROR, RECORD, _g(8, 25), "decision reference does not resolve"),
    "DECISION_NOT_ACTIVE": (ERROR, RECORD, _g(8), "referenced decision is not ACTIVE"),
    "DECISION_KIND_MISMATCH": (ERROR, RECORD, _g(8), "referenced decision has the wrong kind"),
    "UNAUTHORIZED_DECIDER": (ERROR, RECORD, _g(11), "decision made by someone not authorized for its kind"),
    "DECISION_SUBJECT_MISMATCH": (ERROR, RECORD, _g(8), "decision subject is not the one the field requires"),
    "DECISION_VALUE_MISMATCH": (ERROR, RECORD, _g(13, 25), "decision payload does not match what it authorizes"),
    "LIFECYCLE_TRANSITION_MISMATCH": (ERROR, RECORD, _g(9), "lifecycle decision does not enter the claimed stage"),
    "LIFECYCLE_TRANSITION_ILLEGAL": (ERROR, RECORD, _g(9), "lifecycle transition is not allowed"),
    "LIFECYCLE_WAIVER_REQUIRED": (ERROR, RECORD, _g(9), "lifecycle transition requires a Golden Cell waiver"),
    "GPOS_UPGRADE_SAME_VERSION": (ERROR, RECORD, _g(14), "gpos_upgrade from_version equals gpos_version"),
    "GPOS_UPGRADE_DECISION_REQUIRED": (ERROR, RECORD, _g(14), "governed GPOS move has no GPOS_UPGRADE decision"),
    "REVIEW_OVERRIDE_DUPLICATE": (ERROR, RECORD, _g(30), "duplicate review-policy override for a gate and scope"),
    "REVIEW_OVERRIDE_AMBIGUOUS": (ERROR, RECORD, _g(30), "more than one override applies to a routed gate"),
    "REVIEW_POLICY_WEAKER_THAN_EFFECTIVE": (ERROR, RECORD, _g(31), "routed review policy is weaker than the effective policy"),
    "PROJECT_TRIGGER_UNKNOWN": (ERROR, RECORD, _g(27), "PROJECT:<id> trigger is not declared in project config"),
    "CONDITION_DECLINE_NOT_AUTHORIZED": (ERROR, RECORD, _g(10, 36), "TARGET_PRESENTATION_DIFFERS declined without a decided NO"),
    "CONDITION_REQUIRED_BY_PARITY": (ERROR, RECORD, _g(36), "presentation parity YES requires TARGET_PRESENTATION_DIFFERS"),
    "HUMAN_EVIDENCE_SOURCE_UNLISTED": (ERROR, RECORD, _g(12), "HUMAN_EVIDENCE source is not a reviewer or decision authority"),
    # ---- routing structure
    "ROUTING_GATE_DUPLICATE": (ERROR, RECORD, _g(22), "gate listed more than once in routing"),
    "ROUTING_GATE_REQUIRED_AND_OMITTED": (ERROR, RECORD, _g(22), "gate is both required and omitted"),
    "WORKFLOW_GATE_OMITTED": (ERROR, RECORD, _g(29), "workflow always-required gate is omitted"),
    "WORKFLOW_GATE_MISSING": (ERROR, RECORD, _g(29), "workflow always-required gate is not required"),
    "WORKFLOW_GATE_UNACCOUNTED": (ERROR, RECORD, _g(37, 40), "workflow must account for every quality gate"),
    "ROUTING_PRIMARY_REPEATED": (ERROR, RECORD, "registry routing_rules", "primary specialist repeated as secondary"),
    "ROUTING_OWNER_NOT_ROUTED": (ERROR, RECORD, "registry routing_rules", "gate owner is not a routed specialist or reviewer"),
    "ROUTING_EVIDENCE_BELOW_MINIMUM": (ERROR, RECORD, _g(32, 35), "routing omits evidence the registry requires"),
    "ROUTING_EVIDENCE_INVALID_FOR_GATE": (ERROR, RECORD, _g(33), "routing requires evidence not valid for the gate"),
    "CONDITION_DUPLICATE": (ERROR, RECORD, _g(35), "condition listed more than once"),
    "CONDITION_APPLIED_AND_DECLINED": (ERROR, RECORD, _g(35), "condition both applied and declined"),
    "CONDITION_NOT_DEFINED": (ERROR, RECORD, _g(35), "declined condition is not defined for the gate"),
    "CONDITION_UNACCOUNTED": (ERROR, RECORD, _g(35), "defined condition neither applied nor declined"),
    "ROUTING_CROSS_REVIEWER_MISSING": (ERROR, RECORD, _g(34), "routing has no specialist cross-reviewer for a gate that needs one"),
    "ROUTING_HUMAN_REVIEWER_MISSING": (ERROR, RECORD, _g(3), "HUMAN_REVIEW_REQUIRED gate routed without HUMAN as reviewer"),
    # ---- routing <-> gate linkage (integrity)
    "ROUTING_REF_NOT_FOUND": (ERROR, RECORD, _g(19), "gate routing_ref does not resolve"),
    "ROUTED_GATE_AMBIGUOUS": (ERROR, RECORD, _g(19), "more than one gate record for one routed gate"),
    "ROUTING_GATE_MISMATCH": (ERROR, RECORD, _g(20, 21), "gate record disagrees with its routing entry"),
    # ---- evidence for a gate
    "EVIDENCE_NOT_FOUND": (ERROR, RECORD, _g(1), "gate cites evidence that does not exist"),
    "EVIDENCE_SUPERSEDED": (ERROR, RECORD, _g(6), "gate cites superseded evidence"),
    "EVIDENCE_TYPE_NOT_ACCEPTED": (ERROR, RECORD, _g(1), "evidence type is not acceptable for the gate"),
    "INVALID_EVIDENCE_CONTEXT": (ERROR, RECORD, _g(1), "evidence type/capture-context combination is invalid"),
    "EVIDENCE_CONTEXT_NOT_COUNTING": (ERROR, RECORD, _g(1), "capture context does not count for the gate on this scope"),
    "EVIDENCE_SUBJECT_MISMATCH": (ERROR, RECORD, _g(23), "evidence proves a different subject"),
    "STALE_EVIDENCE": (ERROR, RECORD, _g(2), "evidence revision is not the gate revision"),
    "CARRYOVER_REVISION_MISMATCH": (ERROR, RECORD, _g(2), "carryover names a different evidence revision"),
    "CARRYOVER_NOT_ACCOUNTABLE": (ERROR, RECORD, _g(16), "carryover not approved by the owner or a human"),
    "APPLICABILITY_NOT_ACCOUNTABLE": (ERROR, RECORD, _g(23), "evidence applicability not approved by the owner or a human"),
    "TARGET_PLATFORM_NOT_DECLARED": (ERROR, RECORD, _g(41), "target-runtime evidence on an undeclared platform"),
    "REFERENCE_DEVICE_MISMATCH": (ERROR, RECORD, _g(42), "DEVICE_EVIDENCE not captured on a declared reference device"),
    "INSTRUMENTATION_TIMING_UNUSABLE": (ERROR, RECORD, "EVIDENCE-RULES instrumentation", "instrumented capture cannot prove timing"),
    # ---- gate assessment and reviews
    "GATE_OWNER_NOT_PERMITTED": (ERROR, RECORD, _g(5), "owner may not own this gate"),
    "ASSESSOR_NOT_OWNER": (ERROR, RECORD, _g(5), "assessed_by is neither the owner nor a human"),
    "CROSS_REVIEW_SELF": (ERROR, RECORD, _g(5), "owner cross-reviewed its own gate"),
    "CROSS_REVIEW_BY_NEVER_REVIEWER": (ERROR, RECORD, _g(39), "game-director (never_cross_reviewer) recorded as cross-reviewer"),
    "CROSS_REVIEW_STALE": (ERROR, RECORD, _g(24), "active cross-review is of another revision"),
    "CROSS_REVIEW_ELIGIBLE_MISSING": (ERROR, RECORD, _g(38), "PASS lacks a current passing cross-review by an eligible specialist"),
    "CROSS_REVIEW_SUPERSEDED_WITHOUT_REPLACEMENT": (ERROR, RECORD, _g(6), "superseded negative cross-review has no later review by the same reviewer"),
    "ROUTINE_PASS_NOT_OWNER": (ERROR, RECORD, _g(28), "ROUTINE PASS is not the owner's own PASS assessment"),
    "PASS_EVIDENCE_MISSING": (ERROR, RECORD, _g(1), "PASS lacks evidence the registry requires"),
    "PASS_INSUFFICIENT_EVIDENCE": (ERROR, RECORD, _g(1), "PASS rests only on insufficient evidence"),
    # ---- human authority on gates
    "GATE_ASSESSOR_UNAUTHORIZED": (ERROR, RECORD, _g(17), "human assessor not authorized for the gate"),
    "EVIDENCE_REUSE_APPROVER_UNAUTHORIZED": (ERROR, RECORD, _g(16, 17), "human approving evidence reuse not authorized for the gate"),
    "HUMAN_REVIEW_REF_NOT_FOUND": (ERROR, RECORD, _g(3), "human_review_ref does not resolve to a HUMAN_REVIEW record"),
    "HUMAN_REVIEW_SCOPE_MISMATCH": (ERROR, RECORD, _g(4, 24), "linked Human Review is not a PASS for the same scope and revision"),
    "HUMAN_REVIEWER_UNAUTHORIZED": (ERROR, RECORD, _g(17), "human reviewer not authorized for the gate"),
    "HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED": (ERROR, RECORD, _g(12, 17), "HUMAN_EVIDENCE cited by a gate its source may not review"),
    # ---- readiness
    "RECORD_SET_INVALID": (BLOCKER, READINESS, _g(7), "the record set has errors; no routed scope can be READY"),
    "MISSING_REQUIRED_GATE": (BLOCKER, READINESS, _g(19), "required gate has no record (NOT_RUN)"),
    "HUMAN_REVIEW_MISSING": (BLOCKER, READINESS, _g(3, 19), "required HUMAN_REVIEW gate has no record"),
    "GATE_NOT_PASSED": (BLOCKER, READINESS, _g(19), "blocking required gate is not PASS"),
    "GATE_NOT_IN_ROUTING": (BLOCKER, READINESS, _g(22), "linked gate is not listed by its routing"),
    "CROSS_REVIEWER_NOT_ROUTED": (BLOCKER, READINESS, _g(34), "passing cross-review by a reviewer the routing did not route"),
    "CROSS_REVIEWER_NOT_ELIGIBLE": (BLOCKER, READINESS, _g(38), "routed cross-reviewer is not eligible for the gate owner"),
    "ROUTED_CROSS_REVIEW_MISSING": (BLOCKER, READINESS, _g(34, 38), "PASS lacks a passing cross-review by a routed eligible reviewer"),
    "ROUTED_EVIDENCE_MISSING": (BLOCKER, READINESS, _g(32), "PASS lacks counting evidence the routing requires"),
    "PRIMARY_PLATFORM_COVERAGE_MISSING": (BLOCKER, READINESS, _g(43, 44), "Release lacks counting evidence for a PRIMARY platform"),
    "PRIMARY_PLATFORM_UNDECIDED": (BLOCKER, READINESS, _g(43), "a PRIMARY target platform is UNDECIDED"),
    "NON_BLOCKING_GATE_OPEN": (INFO, READINESS, _g(19), "non-blocking required gate is missing or not PASS"),
}

# Readiness blockers that the frozen Phase-1 reference reports as record-set problems.
# (MISSING_REQUIRED_GATE / HUMAN_REVIEW_MISSING / GATE_NOT_PASSED are readiness-only in the
# reference too.) Used by parity tests; documented in tools/validator/README.md.
REFERENCE_CLASS_BLOCKERS = frozenset({
    "GATE_NOT_IN_ROUTING", "CROSS_REVIEWER_NOT_ROUTED", "CROSS_REVIEWER_NOT_ELIGIBLE",
    "ROUTED_CROSS_REVIEW_MISSING", "ROUTED_EVIDENCE_MISSING",
    "PRIMARY_PLATFORM_COVERAGE_MISSING", "PRIMARY_PLATFORM_UNDECIDED",
})


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    category: str
    message: str
    record_type: str = None
    record_id: str = None
    path: str = None
    file: str = None
    related: tuple = ()
    rule: str = None
    details: tuple = ()  # sorted (key, value) pairs; values are JSON-serializable

    def sort_key(self):
        return (SEVERITY_ORDER[self.severity], self.record_type or "", self.record_id or "", self.file or "",
                self.code, self.path or "", self.message, self.related, repr(self.details))

    def to_dict(self):
        return {
            "code": self.code, "severity": self.severity, "category": self.category, "message": self.message,
            "record_type": self.record_type, "record_id": self.record_id, "path": self.path, "file": self.file,
            "related": list(self.related), "rule": self.rule, "details": {k: json.loads(v) for k, v in self.details},
        }


def _encode_detail(value):
    """Details are stored as canonical JSON text so a Diagnostic stays hashable and deterministic."""
    return json.dumps(value, sort_keys=True, default=sorted)


def make(code, message, record_type=None, record_id=None, path=None, related=(), details=None, file=None):
    """Build a Diagnostic whose severity, category and rule come from CODES."""
    if code not in CODES:
        raise KeyError(f"unregistered diagnostic code {code!r}")
    severity, category, rule, _ = CODES[code]
    return Diagnostic(code=code, severity=severity, category=category, message=message,
                      record_type=record_type, record_id=record_id, path=path, file=file,
                      related=tuple(sorted({str(r) for r in related if r is not None})),
                      rule=rule, details=tuple(sorted((k, _encode_detail(v)) for k, v in (details or {}).items())))


def with_file(diag, file):
    if diag.file == file:
        return diag
    return Diagnostic(**{**diag.__dict__, "file": file})


def sort_diagnostics(diags):
    """Deterministic order; exact duplicates are removed."""
    return sorted(set(diags), key=Diagnostic.sort_key)
