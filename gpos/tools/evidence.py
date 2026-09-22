"""Evidence candidates: what a tool execution may *offer*, and what it may never claim.

A tool adapter reports what happened. It does not decide whether that satisfies anything:

    TOOL SUCCESS  !=  EVIDENCE VALID  !=  GATE PASS  !=  HUMAN APPROVAL

So an adapter produces an `EvidenceCandidate` — "this execution produced something that may satisfy
this evidence requirement" — and nothing stronger. There is deliberately no field anywhere in this
module for a gate, a gate status, a reviewer, an approval or a verdict: the vocabulary to claim one
does not exist, so no adapter can be written that claims one.

The rules the foundation enforces on every candidate, all sourced from the frozen registry rather
than a duplicate table kept here:

* the evidence type and capture context must be compatible (`evidence_context_compatibility`);
* `HUMAN_EVIDENCE` can never be produced by a tool (registry `tool_adapter_policy`): only a human
  record is human evidence;
* a candidate must be one of the (type, context) pairs its capability declared;
* a non-derived candidate's capture context must be the context this execution actually observed —
  an offline pass cannot claim it observed a device;
* a derived candidate inherits the origin's capture context unchanged, so extracting a frame from a
  target-runtime capture yields target-runtime-captured visual evidence, while the transform itself
  remains an offline execution. Derivation never upgrades a source;
* a dry run observed nothing, so it may only produce the registry's `dry_run_evidence_types`;
* every referenced artifact must exist in this result and be complete; an artifact from a timed-out
  or failed execution never becomes a candidate;
* a candidate whose subject revision is unknown is kept, marked not materializable and given an
  explicit limitation. The foundation does not invent a revision to make a candidate look current.

`materialize` turns a validated candidate into a GPOS evidence record *value*. It is explicit and
deterministic, it never writes a file, it refuses HUMAN_EVIDENCE, and it fills no reviewer, assessor
or gate field. Writing records into `.game/gpos/evidence/` remains a separate, human-authorized step
that Phase 2C-0 does not perform.
"""

from dataclasses import dataclass, field

from . import diagnostics as dg

FORBIDDEN_KEY = "forbidden_evidence_types"
DRY_RUN_KEY = "dry_run_evidence_types"


def policy(framework):
    return framework.registry["tool_adapter_policy"]


def compatible(framework, evidence_type, capture_context):
    return capture_context in framework.registry["evidence_context_compatibility"].get(evidence_type, [])


@dataclass(frozen=True)
class EvidenceCandidate:
    """An offer, not a verdict. Contains no gate, status, reviewer or approval field by design."""
    evidence_type: str                 # registry evidence_types
    capture_context: str               # registry capture_contexts: where the *content* was captured
    summary: str
    subject_kind: str
    subject_ref: str
    source_adapter: str
    source_capability: str
    generated_at: str                  # RFC 3339
    artifact_ids: tuple = ()
    subject_revision: str = None       # absent when the caller could not prove one
    derived_from: tuple = ()           # artifact ids whose capture context this candidate inherits
    limitations: tuple = ()
    provenance: dict = None
    materializable: bool = False
    notes: tuple = field(default=())

    def to_dict(self):
        out = {"evidence_type": self.evidence_type, "capture_context": self.capture_context,
               "summary": self.summary,
               "subject": {"kind": self.subject_kind, "ref": self.subject_ref},
               "source": {"adapter_id": self.source_adapter, "capability_id": self.source_capability},
               "generated_at": self.generated_at, "artifact_ids": list(self.artifact_ids),
               "derived_from": list(self.derived_from), "limitations": list(self.limitations),
               "materializable": self.materializable}
        if self.subject_revision is not None:
            out["subject"]["revision"] = self.subject_revision
        if self.provenance is not None:
            out["provenance"] = self.provenance
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def validate(framework, candidate, capability, artifacts, dry_run, execution_context):
    """(accepted candidate or None, [ToolDiagnostic]). A rejected candidate is dropped, never coerced."""
    reg, pol = framework.registry, policy(framework)
    adapter, cap_id = candidate.source_adapter, candidate.source_capability
    diag = lambda code, msg: dg.make(code, msg, adapter, cap_id)
    out = []
    if candidate.evidence_type not in reg["evidence_types"]:
        return None, [diag("INVALID_TOOL_REQUEST", f"{candidate.evidence_type!r} is not a GPOS evidence type")]
    if candidate.capture_context not in reg["capture_contexts"]:
        return None, [diag("INVALID_TOOL_REQUEST", f"{candidate.capture_context!r} is not a GPOS capture context")]
    if candidate.evidence_type in pol[FORBIDDEN_KEY]:
        return None, [diag("EVIDENCE_TYPE_FORBIDDEN",
                           f"{candidate.evidence_type} can only come from a human record; a tool adapter may never "
                           f"produce it (registry tool_adapter_policy {FORBIDDEN_KEY})")]
    if not compatible(framework, candidate.evidence_type, candidate.capture_context):
        return None, [diag("EVIDENCE_CONTEXT_INCOMPATIBLE",
                           f"{candidate.evidence_type} cannot be captured in {candidate.capture_context} "
                           f"(registry evidence_context_compatibility: "
                           f"{reg['evidence_context_compatibility'][candidate.evidence_type]})")]
    if dry_run and candidate.evidence_type not in pol[DRY_RUN_KEY]:
        return None, [diag("EVIDENCE_NOT_AVAILABLE_IN_DRY_RUN",
                           f"a dry run observed nothing, so it cannot produce {candidate.evidence_type}; a dry run may "
                           f"only produce {pol[DRY_RUN_KEY]}")]
    pair = (candidate.evidence_type, candidate.capture_context)
    if pair not in capability.evidence_pairs():
        return None, [diag("INVALID_TOOL_REQUEST",
                           f"{cap_id} did not declare it can produce {pair[0]} captured in {pair[1]}; declared: "
                           f"{[list(p) for p in capability.evidence_pairs()]}")]
    by_id = {a.artifact_id: a for a in artifacts}
    unknown = [a for a in candidate.artifact_ids if a not in by_id]
    if unknown:
        return None, [diag("EVIDENCE_ARTIFACT_UNKNOWN",
                           f"the candidate references artifacts this execution did not produce: {unknown}")]
    if not candidate.artifact_ids:
        return None, [diag("INVALID_TOOL_REQUEST", "an evidence candidate must reference at least one artifact")]
    incomplete = [a for a in candidate.artifact_ids if not by_id[a].complete]
    if incomplete:
        return None, [diag("ARTIFACT_INCOMPLETE",
                           f"artifacts {incomplete} come from an execution that did not finish; they are not evidence")]
    origins = {by_id[a].origin_capture_context for a in candidate.artifact_ids}
    if candidate.derived_from:
        missing = [a for a in candidate.derived_from if a not in by_id]
        if missing:
            return None, [diag("EVIDENCE_ARTIFACT_UNKNOWN", f"unknown origin artifacts {missing}")]
        inherited = {by_id[a].origin_capture_context for a in candidate.derived_from}
        if inherited != {candidate.capture_context}:
            return None, [diag("EVIDENCE_CONTEXT_NOT_OBSERVED",
                               f"a derived candidate inherits the capture context of its origin {sorted(inherited)}; it "
                               f"cannot claim {candidate.capture_context}. Processing a capture never upgrades it")]
    elif candidate.capture_context != execution_context:
        return None, [diag("EVIDENCE_CONTEXT_NOT_OBSERVED",
                           f"this execution observed {execution_context}, so it cannot claim {candidate.capture_context}; "
                           f"only a candidate derived from an artifact captured there may carry that context")]
    if origins - {candidate.capture_context, None}:
        return None, [diag("EVIDENCE_CONTEXT_NOT_OBSERVED",
                           f"the referenced artifacts were captured in {sorted(o for o in origins if o)}, not "
                           f"{candidate.capture_context}")]
    limitations = list(candidate.limitations)
    materializable = candidate.subject_revision is not None
    if not materializable:
        note = ("subject revision unknown: this candidate cannot be recorded as GPOS evidence until the caller "
                "supplies the exact revision it captured")
        if note not in limitations:
            limitations.append(note)
        out.append(diag("EVIDENCE_NOT_MATERIALIZABLE",
                        f"{cap_id}: no subject revision was supplied, so the candidate is offered but cannot be "
                        f"materialized as an evidence record"))
    accepted = EvidenceCandidate(
        evidence_type=candidate.evidence_type, capture_context=candidate.capture_context,
        summary=candidate.summary, subject_kind=candidate.subject_kind, subject_ref=candidate.subject_ref,
        source_adapter=adapter, source_capability=cap_id, generated_at=candidate.generated_at,
        artifact_ids=tuple(candidate.artifact_ids), subject_revision=candidate.subject_revision,
        derived_from=tuple(candidate.derived_from), limitations=tuple(limitations),
        provenance=candidate.provenance, materializable=materializable, notes=candidate.notes)
    return accepted, out


def materialize(framework, candidate, artifacts, evidence_id, tool_version=None, extra_provenance=None):
    """A GPOS evidence record *value* for a validated candidate. Returns (record, [problem]).

    Deterministic: the same candidate and artifacts always produce the same record. It writes
    nothing, it refuses HUMAN_EVIDENCE, and it fills no gate, reviewer or assessor field — a record
    is only ever attached to a gate by a separate, human-authorized step.
    """
    pol = policy(framework)
    if candidate.evidence_type in pol[FORBIDDEN_KEY]:
        return None, [f"{candidate.evidence_type} can never be materialized from a tool execution"]
    if not candidate.materializable:
        return None, ["the candidate has no proven subject revision; GPOS evidence must name the exact revision it "
                      "captured"]
    by_id = {a.artifact_id: a for a in artifacts}
    record = {
        "schema_version": "1.0",
        "evidence_id": evidence_id,
        "type": candidate.evidence_type,
        "summary": candidate.summary,
        "subject": {"kind": candidate.subject_kind, "ref": candidate.subject_ref},
        "source": {"kind": "TOOL", "id": candidate.source_adapter},
        "created_at": candidate.generated_at,
        "provenance": {"capture_context": candidate.capture_context,
                       "subject_revision": candidate.subject_revision},
        "artifacts": [{"uri": by_id[a].path, "hash": f"sha256:{by_id[a].sha256}",
                       **({"media_type": by_id[a].media_type} if by_id[a].media_type else {}),
                       **({"description": by_id[a].description} if by_id[a].description else {})}
                      for a in candidate.artifact_ids],
        "limitations": list(candidate.limitations),
        "superseded": False,
    }
    if tool_version:
        record["provenance"]["tool_version"] = tool_version
    for key, value in (extra_provenance or {}).items():
        if value is not None:
            record["provenance"][key] = value
    errors = framework.validators["evidence"].errors(record)
    if errors:
        return None, [f"the generated evidence record is not schema-valid: {errors}"]
    return record, []
