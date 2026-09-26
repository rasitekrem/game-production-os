"""Game Production OS — production validator (Phase 2A).

Deterministic, read-only, fail-closed validation of a project's GPOS records against the
frozen GPOS contracts (core/registry.json, schemas/, core/GOVERNANCE.md §12). It enforces
records; it makes no creative decisions.

Library use:

    from gpos import load_project_record_set, validate_project, evaluate_readiness
    rs = load_project_record_set("path/to/project")
    result = validate_project(rs)            # ValidationResult(valid, diagnostics, summary)
    ready = evaluate_readiness(rs, "TASK-1")  # ReadinessResult(valid_records, ready, ...)
"""

__version__ = "1.0.0-alpha.16"

from .diagnostics import CODES, Diagnostic  # noqa: E402
from .errors import (BundleNotFound, FrameworkLoadError, GposToolError, RoutingNotFound,  # noqa: E402
                     UnsupportedGposVersion, UnsupportedSchemaKeyword)
from .framework import load_framework  # noqa: E402
from .records import RecordSet, from_records, load_project  # noqa: E402
from .validation.project import (ReadinessResult, ValidationResult, evaluate_readiness,  # noqa: E402
                                 validate_project, validate_routing)

load_project_record_set = load_project

__all__ = [
    "__version__", "CODES", "Diagnostic", "BundleNotFound", "FrameworkLoadError", "GposToolError", "RoutingNotFound",
    "UnsupportedGposVersion", "UnsupportedSchemaKeyword", "load_framework", "RecordSet", "from_records", "load_project", "load_project_record_set",
    "ReadinessResult", "ValidationResult", "evaluate_readiness", "validate_project", "validate_routing",
]
