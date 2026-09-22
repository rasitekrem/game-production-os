"""Adapter diagnostics: stable codes, fixed severity and class, deterministic order.

Result classes (CLI exit codes, see gpos.adapters.cli):
    OK         0  rendered / synced / check clean
    INVALID    1  the project or its adapter settings cannot be compiled (validator INVALID, bad settings,
                  context budget exceeded by project authority)
    DRIFT      2  check found generated state that no longer matches its sources or manifest
    ERROR      3  invocation, tool, incompatible GPOS version or internal error
    CONFLICT   4  sync refused: it would overwrite or delete a file GPOS does not own; nothing was written
"""

import json
from dataclasses import dataclass

OK, INVALID, DRIFT, ERROR, CONFLICT = "OK", "INVALID", "DRIFT", "ERROR", "CONFLICT"
# Documented runtime status of a backend (recorded in its compatibility declaration and manifest): Phase 2B renders,
# validates and syncs files but runs no agent; live-session smoke tests belong to the real-agent pilot.
RUNTIME_NOT_YET_SMOKE_TESTED = "RUNTIME_NOT_YET_SMOKE_TESTED"
RUNTIME_STATUSES = (RUNTIME_NOT_YET_SMOKE_TESTED,)
EXIT_FOR = {OK: 0, INVALID: 1, DRIFT: 2, ERROR: 3, CONFLICT: 4}
_RANK = {ERROR: 0, CONFLICT: 1, INVALID: 2, DRIFT: 3, "WARNING": 4, "INFO": 5}

# code: (class, meaning)
CODES = {
    # project / source (INVALID)
    "PROJECT_LAYOUT": (INVALID, "the path is not a GPOS project root"),
    "PROJECT_INVALID": (INVALID, "the Phase-2A validator reports the project INVALID; nothing is generated"),
    "ADAPTER_CONFIG_INVALID": (INVALID, "adapter settings in project config are invalid"),
    "ADAPTER_NOT_ENABLED": (INVALID, "the project config does not enable this adapter (enabled_adapters)"),
    "CONTEXT_BUDGET_EXCEEDED": (INVALID, "a generated file exceeds its context budget; nothing is truncated"),
    "AUTHORITY_DOCUMENT_INVALID": (INVALID, "the machine-readable part of a .game/ authority document is malformed"),
    "AUTHORITY_LOCK_UNVERIFIED": (INVALID, "a LOCKED row or document is not bound to an authorized ACTIVE LOCK decision for its current value"),
    # tool (ERROR)
    "GPOS_VERSION_INCOMPATIBLE": (ERROR, "the project pins a GPOS version this toolchain does not implement"),
    "ADAPTER_UNSUPPORTED": (ERROR, "unknown adapter id"),
    "SOURCE_INVALID": (ERROR, "a canonical GPOS source does not have its frozen structure"),
    "RENDER_INVALID": (ERROR, "a rendered bundle failed adapter validation (adapter defect)"),
    "OUTPUT_PATH_INVALID": (ERROR, "the requested render output location is unsafe"),
    # drift (DRIFT)
    "MANIFEST_MISSING": (DRIFT, "no adapter manifest: the adapter was never synced"),
    "MANIFEST_INVALID": (DRIFT, "the adapter manifest is unreadable or malformed"),
    "ADAPTER_FORMAT_MISMATCH": (DRIFT, "the manifest was written by another adapter format version"),
    "GPOS_VERSION_MISMATCH": (DRIFT, "the manifest was generated from another GPOS version"),
    "SOURCE_CHANGED": (DRIFT, "a source changed since generation"),
    "MANAGED_FILE_MISSING": (DRIFT, "a generated file listed in the manifest is missing"),
    "MANAGED_FILE_MODIFIED": (DRIFT, "a generated file was edited after generation"),
    "UNEXPECTED_MANAGED_FILE": (DRIFT, "a file inside a GPOS-managed area is not in the manifest"),
    "GENERATED_STALE": (DRIFT, "regeneration from the current sources would produce different files"),
    "PATH_ESCAPE": (DRIFT, "the manifest names a path outside the adapter's managed area"),
    # ownership (CONFLICT)
    "UNOWNED_ENTRYPOINT": (CONFLICT, "a human-owned agent entry file exists where the adapter would write"),
    "OUTPUT_CONFLICT": (CONFLICT, "a file GPOS does not own exists where the adapter would write"),
    "MODIFIED_MANAGED_FILE_CONFLICT": (CONFLICT, "a generated file was edited; sync will not overwrite it without --repair"),
    "UNSAFE_PATH": (CONFLICT, "a target path is a symlink or resolves outside the project"),
    "MANIFEST_UNTRUSTED": (CONFLICT, "the existing manifest is malformed or edited; sync cannot establish ownership"),
    "INSTRUCTION_LAYER_CONFLICT": (CONFLICT, "a project instruction file GPOS does not own can add to or override the generated instructions"),
    "INSTRUCTION_CONFIG_CONFLICT": (CONFLICT, "project agent configuration can exclude, replace, truncate or disable generated instructions"),
    "INSTRUCTION_CONFIG_UNREADABLE": (CONFLICT, "project agent configuration that affects instruction discovery cannot be read (fail closed)"),
    "SKILL_ID_CONFLICT": (CONFLICT, "another project-local skill occupies a generated GPOS skill id"),
    # informational
    "FILE_WRITTEN": ("INFO", "a managed file was created or updated"),
    "FILE_REMOVED": ("INFO", "a stale managed file was removed"),
}


@dataclass(frozen=True)
class AdapterDiagnostic:
    code: str
    cls: str
    message: str
    agent: str = None
    path: str = None
    details: str = "{}"  # canonical JSON

    def to_dict(self):
        return {"code": self.code, "class": self.cls, "message": self.message, "agent": self.agent, "path": self.path,
                "details": json.loads(self.details)}

    def key(self):
        return (_RANK[self.cls], self.agent or "", self.path or "", self.code, self.message)


def make(code, message, agent=None, path=None, details=None):
    cls = CODES[code][0]
    return AdapterDiagnostic(code, cls, message, agent, path, json.dumps(details or {}, sort_keys=True))


def sort(diags):
    return sorted(set(diags), key=AdapterDiagnostic.key)


def result_class(diags):
    """The result class of a set of diagnostics: the most severe of ERROR > CONFLICT > INVALID > DRIFT, else OK."""
    classes = {d.cls for d in diags}
    for c in (ERROR, CONFLICT, INVALID, DRIFT):
        if c in classes:
            return c
    return OK
