"""Adapter pipeline failure carrying structured diagnostics."""


class AdapterError(Exception):
    def __init__(self, code, message, diagnostics=()):
        super().__init__(message)
        self.code = code
        self.diagnostics = list(diagnostics)
