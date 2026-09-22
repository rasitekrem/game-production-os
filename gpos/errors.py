"""Tool-level failures, kept distinct from findings about project records.

A finding about project data is a Diagnostic (gpos.diagnostics). An exception from this
module means the validator could not do its job (framework missing, invocation wrong,
schema uses a keyword the validator does not implement). The CLI maps these to exit 3.
"""


class GposToolError(Exception):
    """Base class for failures of the validator itself (never a verdict about records)."""

    code = "TOOL_ERROR"


class FrameworkLoadError(GposToolError):
    """The GPOS registry, schemas or VERSION could not be loaded or are inconsistent."""

    code = "FRAMEWORK_LOAD_ERROR"


class UnsupportedSchemaKeyword(FrameworkLoadError):
    """A schema uses a keyword or format the production validator does not implement."""

    code = "UNSUPPORTED_SCHEMA_KEYWORD"


class BundleNotFound(GposToolError):
    """The path given does not contain a GPOS record bundle."""

    code = "BUNDLE_NOT_FOUND"


class RoutingNotFound(GposToolError):
    """Readiness was requested for a routing id the record set does not contain."""

    code = "ROUTING_NOT_FOUND"
