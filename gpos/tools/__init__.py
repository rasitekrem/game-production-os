"""GPOS tool adapter foundation (Phase 2C-0).

The shared execution, capability, provenance, safety and evidence layer that every future GPOS tool
adapter — version control, media processing, target device, DCC and engine — must use, so that none of
them invents its own
command execution model, capability vocabulary, result structure, provenance model, mutation
semantics, single-writer behaviour, timeout behaviour, evidence semantics or failure model.

The boundary this layer sits on:

    agent / specialist intent -> tool adapter -> execution -> artifacts + provenance
    -> evidence candidate -> GPOS record / validator -> gate / Human Review

and the distinction it exists to protect:

    TOOL SUCCESS  !=  EVIDENCE VALID  !=  GATE PASS  !=  HUMAN APPROVAL

A tool adapter reports what happened. It never promotes its own output into acceptance.

Phase 2C-0 ships the foundation and one TEST_ONLY synthetic reference adapter. It ships no adapter
for any real production tool (see tools/adapter-foundation.md for the named list), invokes no agent
and uses no network.

Library use:

    from gpos.tools import ToolRegistry, ExecutionRequest, Subject, execute
    registry = ToolRegistry(load_framework())
    result = execute(registry, ExecutionRequest(adapter_id=..., capability_id=...,
                                                subject=Subject("TASK", "TASK-1")))
"""

from . import diagnostics
from .artifacts import Artifact, ArtifactSpec, hash_file
from .capabilities import Capability, TimeoutPolicy
from .diagnostics import EXIT_FOR, STATUSES, ToolDiagnostic
from .errors import AdapterRegistrationError, ToolFoundationError
from .evidence import EvidenceCandidate, materialize
from .execution import (AdapterOutcome, Clock, ExecutionContext, ExecutionRequest, ToolResult, execute,
                        rfc3339)
from .leases import Lease, acquire, release
from .model import Actor, AdapterDescriptor, ProbeResult, Subject, ToolAdapter
from .process import EnvironmentPolicy, ToolProcessSpec, run_process
from .provenance import ToolProvenance
from .registry import ToolRegistry, default_registry

__all__ = [
    "diagnostics", "Artifact", "ArtifactSpec", "hash_file", "Capability", "TimeoutPolicy",
    "EXIT_FOR", "STATUSES", "ToolDiagnostic", "AdapterRegistrationError", "ToolFoundationError",
    "EvidenceCandidate", "materialize", "AdapterOutcome", "Clock", "ExecutionContext",
    "ExecutionRequest", "ToolResult", "execute", "rfc3339", "Lease", "acquire", "release",
    "Actor", "AdapterDescriptor", "ProbeResult", "Subject", "ToolAdapter", "EnvironmentPolicy",
    "ToolProcessSpec", "run_process", "ToolProvenance", "ToolRegistry", "default_registry",
]
