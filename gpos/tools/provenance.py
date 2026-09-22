"""Provenance of a tool execution: what ran, on what, producing what.

Every future tool output must be traceable, so provenance is built by the foundation from what it
actually observed, not by the adapter from what it would like to claim.

The rule for unknown values is explicit and mechanical:

* a value the foundation observed (adapter, capability, request, tool version from the probe, start
  and end time, artifact hashes, mutation state, dry-run flag, truncation) is always recorded;
* a value only the caller can know (subject revision, build revision, target platform, device) is
  recorded when the caller supplied it and is otherwise **absent** — never guessed, never defaulted;
* every absent value is named in `unknown`, so a reader can tell "not known" from "not applicable"
  without inspecting the schema.

Phase 2C-0 does not infer a repository revision: that is the Git adapter's job in Phase 2C-1. A
build revision that nobody supplied stays unknown here.
"""

from dataclasses import dataclass, field

# Caller-supplied values the foundation must never invent.
CALLER_SUPPLIED = ("subject_revision", "build_revision", "build_id", "target_platform", "device")


@dataclass(frozen=True)
class ToolProvenance:
    # observed by the foundation
    gpos_version: str
    adapter_id: str
    adapter_version: str
    capability_id: str
    request_id: str
    execution_context: str            # registry capture_contexts: what this execution observed
    started_at: str                   # RFC 3339
    finished_at: str                  # RFC 3339
    duration_seconds: float           # measured with a monotonic clock, not by subtracting timestamps
    dry_run: bool
    mutation_performed: bool
    subject_kind: str
    subject_ref: str
    tool_name: str = None
    tool_version: str = None
    tool_path: str = None
    platform: str = None
    project_id: str = None
    routing_ref: str = None
    actor: dict = None
    command: dict = None              # redacted executable + argv, never a shell string
    environment: dict = None          # names only; values are never copied here
    input_artifacts: tuple = ()       # ((artifact_id, sha256), ...)
    output_artifacts: tuple = ()
    output_truncated: bool = False
    # caller-supplied; absent when unknown
    subject_revision: str = None
    build_revision: str = None
    build_id: str = None
    target_platform: str = None
    device: str = None
    notes: tuple = field(default=())

    def unknown(self):
        return tuple(name for name in CALLER_SUPPLIED if getattr(self, name) is None)

    def to_dict(self):
        out = {
            "gpos_version": self.gpos_version, "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version, "capability_id": self.capability_id,
            "request_id": self.request_id, "execution_context": self.execution_context,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds, "dry_run": self.dry_run,
            "mutation_performed": self.mutation_performed,
            "subject": {"kind": self.subject_kind, "ref": self.subject_ref},
            "input_artifacts": [{"artifact_id": a, "sha256": h} for a, h in self.input_artifacts],
            "output_artifacts": [{"artifact_id": a, "sha256": h} for a, h in self.output_artifacts],
            "output_truncated": self.output_truncated,
            "unknown": list(self.unknown()),
        }
        for name in ("tool_name", "tool_version", "tool_path", "platform", "project_id", "routing_ref",
                     "actor", "command", "environment") + CALLER_SUPPLIED:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.subject_revision is not None:
            out["subject"]["revision"] = self.subject_revision
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def missing_required(provenance, required):
    """Names among `required` that this provenance does not know. The caller decides what that means:
    an evidence candidate needs a subject revision, a bare inspection does not."""
    return tuple(name for name in required if getattr(provenance, name, None) is None)
