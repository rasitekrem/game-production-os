"""Tool foundation diagnostics: stable codes, a fixed result class per code, deterministic order.

Result statuses are library values, independent of CLI exit codes. A caller distinguishes them
without reading any message:

    SUCCESS          the capability executed and did what it declared
    FAILED           the tool ran and failed
    TIMED_OUT        the deadline passed; the process was terminated
    CANCELLED        execution was cancelled before or during the run
    UNAVAILABLE      the required tool is not present, or the adapter is not READY
    CONFLICT         a single-writer lease or another safety conflict refused the operation
    INVALID_REQUEST  the adapter, capability, request or a declared evidence candidate is invalid
    INCOMPATIBLE     the installed tool or the pinned GPOS version cannot be used
    INTERNAL_ERROR   a foundation or adapter defect

`SUCCESS` is never inferred from an exit code alone: an adapter reports what happened and the
foundation downgrades the status whenever a blocking diagnostic was recorded (fail closed).
"""

import json
from dataclasses import dataclass

SUCCESS, FAILED, TIMED_OUT, CANCELLED = "SUCCESS", "FAILED", "TIMED_OUT", "CANCELLED"
UNAVAILABLE, CONFLICT, INVALID_REQUEST = "UNAVAILABLE", "CONFLICT", "INVALID_REQUEST"
INCOMPATIBLE, INTERNAL_ERROR = "INCOMPATIBLE", "INTERNAL_ERROR"
INFO = "INFO"

STATUSES = (SUCCESS, FAILED, TIMED_OUT, CANCELLED, UNAVAILABLE, CONFLICT, INVALID_REQUEST,
            INCOMPATIBLE, INTERNAL_ERROR)

# Distinct exit codes: no two statuses collapse into one, and no status shares 0 with SUCCESS.
EXIT_FOR = {SUCCESS: 0, INVALID_REQUEST: 1, FAILED: 2, TIMED_OUT: 3, UNAVAILABLE: 4,
            CONFLICT: 5, INCOMPATIBLE: 6, CANCELLED: 7, INTERNAL_ERROR: 8}

# Most severe first: the status of a result is the most severe class among its diagnostics.
SEVERITY = (INTERNAL_ERROR, INCOMPATIBLE, INVALID_REQUEST, CONFLICT, UNAVAILABLE, TIMED_OUT,
            CANCELLED, FAILED, INFO)
_RANK = {cls: i for i, cls in enumerate(SEVERITY)}

# code: (result class, meaning)
CODES = {
    # adapter / capability / request (INVALID_REQUEST)
    "ADAPTER_NOT_FOUND": (INVALID_REQUEST, "no tool adapter with this id is registered"),
    "ADAPTER_REGISTRATION_INVALID": (INVALID_REQUEST, "the adapter descriptor or one of its capabilities is invalid"),
    "CAPABILITY_NOT_FOUND": (INVALID_REQUEST, "the adapter declares no capability with this id"),
    "INVALID_TOOL_REQUEST": (INVALID_REQUEST, "the execution request is malformed or contradicts the capability"),
    "DRY_RUN_UNSUPPORTED": (INVALID_REQUEST, "the capability does not support dry run"),
    "MUTATION_NOT_ALLOWED": (INVALID_REQUEST, "a mutating capability was requested without explicit mutation consent"),
    "TIMEOUT_NOT_PERMITTED": (INVALID_REQUEST, "the requested timeout is outside the capability's policy bounds"),
    "UNSAFE_EXECUTION_PATH": (INVALID_REQUEST, "the working directory is outside the permitted filesystem scope"),
    "UNSAFE_ARTIFACT_PATH": (INVALID_REQUEST, "an artifact path escapes the permitted filesystem scope"),
    "WORKSPACE_NOT_USABLE": (INVALID_REQUEST, "the execution workspace does not exist and cannot be created"),
    "UNSAFE_PROCESS_SPEC": (INVALID_REQUEST, "the process spec is not a resolved executable plus an argument vector"),
    "EVIDENCE_CONTEXT_INCOMPATIBLE": (INVALID_REQUEST, "the evidence type and capture context are incompatible (registry evidence_context_compatibility)"),
    "EVIDENCE_TYPE_FORBIDDEN": (INVALID_REQUEST, "a tool adapter may never produce this evidence type"),
    "EVIDENCE_CONTEXT_NOT_OBSERVED": (INVALID_REQUEST, "the candidate claims a capture context this execution did not observe"),
    "EVIDENCE_NOT_AVAILABLE_IN_DRY_RUN": (INVALID_REQUEST, "a dry run observed nothing and cannot produce this evidence type"),
    "EVIDENCE_ARTIFACT_UNKNOWN": (INVALID_REQUEST, "the candidate references an artifact this execution did not produce"),
    "INVALID_ARTIFACT_CLAIM": (INVALID_REQUEST, "an output artifact contradicts the registered capability"),
    "UNSUPPORTED_RESULT_VALUE": (INVALID_REQUEST, "an adapter offered a value a ToolResult cannot carry"),
    "PROVENANCE_INCOMPLETE": (INVALID_REQUEST, "provenance is missing a value the foundation requires and must not invent"),
    "PROJECT_LAYOUT": (INVALID_REQUEST, "the path is not a GPOS project root"),
    "PROJECT_INVALID": (INVALID_REQUEST, "the Phase-2A validator reports the project INVALID"),
    "ROUTING_NOT_READY": (INVALID_REQUEST, "the capability requires a READY routing and this routing is not ready"),
    # target device (Phase 2C-3): generic to any device adapter
    "TARGET_DEVICE_NOT_PHYSICAL": (INVALID_REQUEST, "the target is an emulator, or could not be established as physical target hardware"),
    "TARGET_DEVICE_IDENTITY_MISMATCH": (INVALID_REQUEST, "the requested device identity is not the identity the selected target reports"),
    # version control (Phase 2C-1): generic to any repository adapter, not specific to one tool
    "REPOSITORY_NOT_FOUND": (INVALID_REQUEST, "the version-control tool found no work tree at the project root"),
    "REPOSITORY_ROOT_MISMATCH": (INVALID_REQUEST, "the repository top level is not the GPOS project root"),
    # tool availability (UNAVAILABLE)
    "TOOL_NOT_FOUND": (UNAVAILABLE, "the tool this adapter drives is not available"),
    # target device (Phase 2C-3): generic to any device adapter, not specific to one tool
    "TARGET_DEVICE_UNAVAILABLE": (UNAVAILABLE, "the named target device is not connected to the device tool"),
    "ADAPTER_NOT_READY": (UNAVAILABLE, "the adapter has not established that its tool is available and compatible"),
    # compatibility (INCOMPATIBLE)
    "TOOL_VERSION_UNSUPPORTED": (INCOMPATIBLE, "the installed tool version is outside the adapter's supported range"),
    "PLATFORM_UNSUPPORTED": (INCOMPATIBLE, "the adapter does not support this platform"),
    "GPOS_VERSION_INCOMPATIBLE": (INCOMPATIBLE, "the project pins a GPOS version this toolchain does not implement"),
    # single writer (CONFLICT)
    "LEASE_CONFLICT": (CONFLICT, "another owner holds the single-writer lease for this resource"),
    "LEASE_INVALID": (CONFLICT, "the lease file is unreadable, malformed or owned by an unknown owner"),
    "LEASE_NOT_HELD": (CONFLICT, "the lease required for this operation is not held"),
    "LEASE_RELEASE_FAILED": (CONFLICT, "the single-writer lease this execution held could not be released"),
    "REPOSITORY_STATE_CONFLICT": (CONFLICT, "the repository has no exact committed revision (uncommitted work or no commit yet)"),
    "TARGET_DEVICE_NOT_READY": (CONFLICT, "the named target device is connected but not ready (offline, unauthorized or still booting)"),
    "TARGET_PROCESS_NOT_RUNNING": (CONFLICT, "the named application process is not running on the target device"),
    # execution (FAILED / TIMED_OUT / CANCELLED)
    "EXECUTION_FAILED": (FAILED, "the tool executed and reported failure"),
    "ARTIFACT_MISSING": (FAILED, "a declared artifact does not exist on disk"),
    "ARTIFACT_HASH_FAILED": (FAILED, "an artifact could not be hashed"),
    "EXECUTION_TIMEOUT": (TIMED_OUT, "the deadline passed; the process was terminated"),
    "EXECUTION_CANCELLED": (CANCELLED, "execution was cancelled"),
    # defects (INTERNAL_ERROR)
    "ADAPTER_INTERNAL_ERROR": (INTERNAL_ERROR, "the adapter raised an unexpected exception"),
    # informational
    "PROCESS_OUTPUT_TRUNCATED": (INFO, "captured output reached the capture bound and was truncated"),
    "ARTIFACT_INCOMPLETE": (INFO, "an artifact was produced by an execution that did not finish; it is not evidence"),
    "SECRETS_REDACTED": (INFO, "credential-shaped values were redacted from captured text"),
    "MUTATION_SKIPPED_DRY_RUN": (INFO, "dry run: the planned mutation was not performed"),
    "LEASE_ACQUIRED": (INFO, "a single-writer lease was acquired for this execution"),
    "LEASE_STALE": (INFO, "a lease looks abandoned; recovery is explicit and never automatic"),
    "EVIDENCE_NOT_MATERIALIZABLE": (INFO, "the candidate is offered but cannot become a GPOS evidence record yet"),
}


@dataclass(frozen=True)
class ToolDiagnostic:
    code: str
    cls: str
    message: str
    adapter: str = None
    capability: str = None
    path: str = None
    details: str = "{}"  # canonical JSON

    def to_dict(self):
        return {"code": self.code, "class": self.cls, "message": self.message, "adapter": self.adapter,
                "capability": self.capability, "path": self.path, "details": json.loads(self.details)}

    def key(self):
        return (_RANK[self.cls], self.adapter or "", self.capability or "", self.path or "", self.code, self.message)


def make(code, message, adapter=None, capability=None, path=None, details=None):
    return ToolDiagnostic(code, CODES[code][0], message, adapter, capability, path,
                          json.dumps(details or {}, sort_keys=True))


def sort(diags):
    return sorted(set(diags), key=ToolDiagnostic.key)


def blocking(diags):
    """Diagnostics that forbid a SUCCESS status."""
    return [d for d in diags if d.cls != INFO]


def result_status(diags, default=SUCCESS):
    """The most severe class among `diags`, or `default` when none blocks."""
    classes = {d.cls for d in diags}
    for cls in SEVERITY:
        if cls == INFO:
            break
        if cls in classes:
            return cls
    return default
