"""Tool foundation failures that carry a diagnostic code.

Callers that want structured results never see these: `gpos.tools.execution.execute` converts
every one into a ToolResult. They exist so that registration and request construction can fail
loudly inside adapter code instead of returning half-built objects.
"""


class ToolFoundationError(Exception):
    def __init__(self, code, message, diagnostics=()):
        super().__init__(message)
        self.code = code
        self.diagnostics = list(diagnostics)


class AdapterRegistrationError(ToolFoundationError):
    def __init__(self, message, diagnostics=()):
        super().__init__("ADAPTER_REGISTRATION_INVALID", message, diagnostics)


class InvalidRequest(ToolFoundationError):
    def __init__(self, message, diagnostics=()):
        super().__init__("INVALID_TOOL_REQUEST", message, diagnostics)


class LeaseConflict(ToolFoundationError):
    def __init__(self, message, diagnostics=()):
        super().__init__("LEASE_CONFLICT", message, diagnostics)


class UnsafePath(ToolFoundationError):
    def __init__(self, code, message, diagnostics=()):
        super().__init__(code, message, diagnostics)
