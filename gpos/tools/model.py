"""Tool adapter identity, probe and lifecycle: the shape every GPOS tool adapter has.

A tool adapter is implementation/tool infrastructure. In the frozen authority hierarchy it sits at
`ENGINE_TOOL_DEFAULTS` at the very highest: it may execute an authorized capability, inspect tool
state, produce artifacts and collect provenance, and it may offer an evidence *candidate*. It may
never decide a gate, create HUMAN_EVIDENCE, resolve a Human Review, change Project Locked Authority
or move the lifecycle. Nothing in this module gives an adapter a way to express any of those.

Lifecycle (registry tool_adapter_states):

    REGISTERED --probe()--> PROBED --> READY
                              |----> UNAVAILABLE      the tool is not installed
                              '----> INCOMPATIBLE     the installed version or platform cannot be used

Execution of a capability that `requires_tool` fails closed unless the adapter reached READY.
"""

import platform
from dataclasses import dataclass, field

REGISTERED, PROBED, READY = "REGISTERED", "PROBED", "READY"
UNAVAILABLE, INCOMPATIBLE = "UNAVAILABLE", "INCOMPATIBLE"
ADAPTER_STATES = (REGISTERED, PROBED, READY, UNAVAILABLE, INCOMPATIBLE)

AVAILABLE, VERSION_UNSUPPORTED = "AVAILABLE", "VERSION_UNSUPPORTED"
PROBE_STATUSES = (AVAILABLE, UNAVAILABLE, VERSION_UNSUPPORTED)

PROBE_STATE = {AVAILABLE: READY, UNAVAILABLE: UNAVAILABLE, VERSION_UNSUPPORTED: INCOMPATIBLE}

PLATFORMS = {"Darwin": "MACOS", "Linux": "LINUX", "Windows": "WINDOWS"}


def current_platform():
    """This machine in the registry `platforms` vocabulary, or None when it is not one of them."""
    return PLATFORMS.get(platform.system())


@dataclass(frozen=True)
class Subject:
    """What an execution is about. A revision that the caller does not know stays absent: the
    foundation never invents one, and evidence for an unknown revision is never called current."""
    kind: str                # registry gate_scope_kinds
    ref: str
    revision: str = None

    def to_dict(self):
        out = {"kind": self.kind, "ref": self.ref}
        if self.revision is not None:
            out["revision"] = self.revision
        return out


@dataclass(frozen=True)
class InputArtifact:
    """An existing artifact an execution consumes, vouched for by the caller.

    `capture_context` is where this content was *captured* — it comes from the caller (usually from
    the evidence the artifact already belongs to), never from the tool that is about to process it.
    A derived artifact inherits it, which is why processing a capture can never upgrade it.
    """
    artifact_id: str
    path: str
    capture_context: str = None
    media_type: str = None
    description: str = ""

    def to_dict(self):
        out = {"artifact_id": self.artifact_id, "path": self.path}
        for name in ("capture_context", "media_type", "description"):
            if getattr(self, name):
                out[name] = getattr(self, name)
        return out


@dataclass(frozen=True)
class Actor:
    kind: str                # registry actor_kinds; a tool adapter is TOOL, its caller may be AGENT/HUMAN/CI
    id: str

    def to_dict(self):
        return {"kind": self.kind, "id": self.id}


@dataclass(frozen=True)
class AdapterDescriptor:
    """Deterministic identity of a tool adapter. Two runs of the same adapter version produce the
    same descriptor, so it can be hashed into provenance."""
    adapter_id: str
    adapter_version: str
    tool_family: str                 # registry tool_families
    target_tool: str                 # the tool this adapter drives, e.g. a CLI name
    adapter_kind: str                # registry tool_adapter_kinds; TEST_ONLY is never a production adapter
    state_model: str                 # registry tool_state_models: the adapter's own default
    supported_platforms: tuple       # registry platforms
    capabilities: tuple              # Capability
    availability: str = ""           # what must be installed/configured for the tool to be available
    minimum_tool_version: str = None
    compatibility_notes: tuple = ()
    filesystem_scopes: tuple = ()     # extra absolute scopes beyond the project root, if the contract needs them
    network: str = "FORBIDDEN"        # the foundation default; no adapter in Phase 2C-0 changes it

    @property
    def test_only(self):
        return self.adapter_kind == "TEST_ONLY"

    def capability(self, capability_id):
        return next((c for c in self.capabilities if c.id == capability_id), None)

    def to_dict(self):
        return {"adapter_id": self.adapter_id, "adapter_version": self.adapter_version,
                "tool_family": self.tool_family, "target_tool": self.target_tool,
                "adapter_kind": self.adapter_kind, "state_model": self.state_model,
                "supported_platforms": list(self.supported_platforms),
                "availability": self.availability, "minimum_tool_version": self.minimum_tool_version,
                "compatibility_notes": list(self.compatibility_notes),
                "filesystem_scopes": list(self.filesystem_scopes), "network": self.network,
                "test_only": self.test_only,
                "capabilities": [c.to_dict() for c in self.capabilities]}


@dataclass(frozen=True)
class ProbeResult:
    """A structured answer to "is this tool usable here?". A missing tool is a result, never an
    exception: nothing below this layer lets a subprocess failure reach a caller opaquely."""
    adapter_id: str
    status: str                      # PROBE_STATUSES
    tool_path: str = None
    tool_version: str = None
    platform: str = None
    detail: str = ""
    capability_availability: tuple = ()   # ((capability id, available bool, reason), ...)
    diagnostics: tuple = ()

    @property
    def available(self):
        return self.status == AVAILABLE

    @property
    def state(self):
        return PROBE_STATE[self.status]

    def to_dict(self):
        return {"adapter_id": self.adapter_id, "status": self.status, "state": self.state,
                "tool_path": self.tool_path, "tool_version": self.tool_version, "platform": self.platform,
                "detail": self.detail,
                "capability_availability": [{"capability_id": c, "available": a, "reason": r}
                                            for c, a, r in self.capability_availability],
                "diagnostics": [d.to_dict() for d in self.diagnostics]}


class ToolAdapter:
    """Base class for every GPOS tool adapter.

    A concrete adapter owns only the tool-specific part: mapping a capability to a real invocation,
    tool-specific arguments, tool-specific parsing and tool-specific artifacts. Everything else —
    request validation, mutation consent, leases, process safety, timeouts, capture bounds,
    redaction, artifact hashing, provenance and evidence rules — belongs to the foundation and is
    applied around `execute` whether the adapter cooperates or not.
    """

    descriptor: AdapterDescriptor = None

    @property
    def adapter_id(self):
        return self.descriptor.adapter_id

    def capabilities(self):
        return tuple(self.descriptor.capabilities)

    def capability(self, capability_id):
        return self.descriptor.capability(capability_id)

    def probe(self):  # pragma: no cover - abstract
        """READ_ONLY. Must return a ProbeResult and must not raise for a missing or wrong tool."""
        raise NotImplementedError(f"{type(self).__name__} must implement probe()")

    def execute(self, request, context):  # pragma: no cover - abstract
        """Run one capability. Must return an AdapterOutcome (gpos.tools.execution)."""
        raise NotImplementedError(f"{type(self).__name__} must implement execute()")


@dataclass(frozen=True)
class AdapterState:
    adapter_id: str
    state: str = REGISTERED
    probe: ProbeResult = None
    checked_at: str = None
    history: tuple = field(default=())
