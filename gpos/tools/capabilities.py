"""The capability model every GPOS tool adapter uses.

A capability is a declaration, not a description: the foundation reads it to decide whether an
execution is allowed, whether it may mutate anything, whether it needs the single-writer lease,
what it may claim as evidence and how long it may run. An adapter cannot talk its way past its own
declaration at execution time, and it cannot declare a capability the registry vocabulary does not
contain.

Two fields carry most of the safety weight:

* `operation_class` — READ_ONLY may inspect, calculate and capture but must not change authoritative
  project or tool state; MUTATING may. The classification belongs to the capability, not to the
  size of the change: a small write is still MUTATING.
* `state_model` — STATELESS execution does not depend on a long-lived tool state the adapter manages;
  STATEFUL does (an editor session, a device deployment state). A MUTATING + STATEFUL capability must
  declare `single_writer_required`, which is how the frozen stateful-editor policy is preserved.

`execution_context` is the capture context this capability's execution actually observes (an offline
media pass observes OFFLINE_ANALYSIS, a device capture observes TARGET_RUNTIME). `potential_evidence`
lists the (evidence type, capture context) pairs it may ever produce; each pair is checked against
the registry's evidence/context compatibility at registration, and a derived candidate may only
inherit its origin's context, never upgrade to a stronger one.
"""

from dataclasses import dataclass, field

TIMEOUT_DEFAULT = 60.0
TIMEOUT_MAX = 3600.0


@dataclass(frozen=True)
class TimeoutPolicy:
    default: float = TIMEOUT_DEFAULT
    maximum: float = TIMEOUT_MAX

    def resolve(self, requested):
        """(timeout, error). A request may ask for less than the maximum, never more."""
        if requested is None:
            return min(self.default, self.maximum), None
        if not isinstance(requested, (int, float)) or isinstance(requested, bool) or requested <= 0:
            return None, f"timeout must be a positive number, not {requested!r}"
        if requested > self.maximum:
            return None, f"requested timeout {requested}s exceeds the capability maximum {self.maximum}s"
        return float(requested), None


@dataclass(frozen=True)
class Capability:
    id: str                              # adapter-scoped, e.g. synthetic.inspect
    category: str                        # registry tool_capability_categories
    description: str
    operation_class: str                 # registry tool_operation_classes
    state_model: str                     # registry tool_state_models
    execution_context: str               # registry capture_contexts: what this execution observes
    single_writer_required: bool = False
    dry_run_supported: bool = False
    requires_tool: bool = True           # execution needs the adapter's tool to be AVAILABLE
    requires_project: bool = True        # execution is bound to a GPOS project that must be VALID
    requires_ready_routing: bool = False # only for capabilities that act on a routed task
    input_kinds: tuple = ()              # accepted input names/kinds (adapter-specific vocabulary)
    artifact_kinds: tuple = ()           # registry tool_artifact_kinds this capability may produce
    potential_evidence: tuple = ()       # ((evidence_type, capture_context), ...)
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    side_effect_scope: str = "NONE"      # prose: what MUTATING touches; "NONE" for READ_ONLY
    resource_kind: str = None            # what a single-writer lease is taken on, e.g. EDITOR_PROJECT
    resource_from_request: bool = False  # the lease target is named by the request, not by the project
    notes: tuple = ()

    @property
    def mutating(self):
        return self.operation_class == "MUTATING"

    @property
    def stateful(self):
        return self.state_model == "STATEFUL"

    def evidence_pairs(self):
        return tuple((t, c) for t, c in self.potential_evidence)

    def to_dict(self):
        return {"id": self.id, "category": self.category, "description": self.description,
                "operation_class": self.operation_class, "state_model": self.state_model,
                "execution_context": self.execution_context,
                "single_writer_required": self.single_writer_required,
                "dry_run_supported": self.dry_run_supported, "requires_tool": self.requires_tool,
                "requires_project": self.requires_project, "requires_ready_routing": self.requires_ready_routing,
                "input_kinds": list(self.input_kinds), "artifact_kinds": list(self.artifact_kinds),
                "potential_evidence": [{"evidence_type": t, "capture_context": c} for t, c in self.potential_evidence],
                "timeout": {"default": self.timeout.default, "maximum": self.timeout.maximum},
                "side_effect_scope": self.side_effect_scope, "resource_kind": self.resource_kind,
                "resource_from_request": self.resource_from_request,
                "notes": list(self.notes)}
