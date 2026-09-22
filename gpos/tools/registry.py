"""The tool adapter registry: which production tool adapters exist, and what they may do.

This is deliberately *not* the Phase-2B agent adapter registry. That layer projects GPOS
instructions into an agent's own instruction format; tool adapters are callable capabilities that
run underneath that work. The two never share a registry, an id space or a result contract.

Registration is explicit Python — there is no plugin discovery, no entry-point scanning, no
download and no network. Everything is validated against the frozen GPOS registry before it is
accepted, and listings are deterministic (sorted by id), so two runs produce the same view.

Phase 2C-0 ships **no** production tool adapter. `default_registry()` is empty by design: the only
executable adapter in the tree is the TEST_ONLY synthetic reference adapter, and it must be
registered deliberately, into a registry that was explicitly told to allow test-only adapters.
"""

from . import diagnostics as dg
from . import model
from .errors import AdapterRegistrationError
from .validation import validate_descriptor


class ToolRegistry:
    def __init__(self, framework, allow_test_only=False):
        self.framework = framework
        self.allow_test_only = allow_test_only
        self._adapters = {}
        self._states = {}

    # ------------------------------------------------------------ registration

    def register(self, adapter):
        """Register an adapter, or raise AdapterRegistrationError with structured diagnostics."""
        descriptor = getattr(adapter, "descriptor", None)
        if descriptor is None:
            raise AdapterRegistrationError("the adapter has no descriptor",
                                           [dg.make("ADAPTER_REGISTRATION_INVALID", "no descriptor")])
        for name in ("probe", "execute"):
            impl = getattr(type(adapter), name, None)
            if impl is None or impl is getattr(model.ToolAdapter, name):
                raise AdapterRegistrationError(
                    f"{descriptor.adapter_id}: {name}() is not implemented",
                    [dg.make("ADAPTER_REGISTRATION_INVALID", f"{name}() is not implemented", descriptor.adapter_id)])
        problems = validate_descriptor(self.framework, descriptor, self.allow_test_only)
        if problems:
            raise AdapterRegistrationError(f"{descriptor.adapter_id}: {problems[0].message}", problems)
        if descriptor.adapter_id in self._adapters:
            raise AdapterRegistrationError(
                f"adapter id {descriptor.adapter_id!r} is already registered",
                [dg.make("ADAPTER_REGISTRATION_INVALID", "duplicate adapter id", descriptor.adapter_id)])
        self._adapters[descriptor.adapter_id] = adapter
        self._states[descriptor.adapter_id] = model.AdapterState(descriptor.adapter_id, model.REGISTERED)
        return adapter

    # ------------------------------------------------------------ lookup

    def __contains__(self, adapter_id):
        return adapter_id in self._adapters

    def get(self, adapter_id):
        return self._adapters.get(adapter_id)

    def adapter_ids(self):
        return sorted(self._adapters)

    def list_adapters(self):
        return [self._adapters[a].descriptor for a in self.adapter_ids()]

    def list_capabilities(self):
        """[(adapter id, Capability)] over every adapter, deterministically ordered."""
        return [(a, cap) for a in self.adapter_ids()
                for cap in sorted(self._adapters[a].descriptor.capabilities, key=lambda c: c.id)]

    def capability(self, adapter_id, capability_id):
        adapter = self.get(adapter_id)
        return adapter.descriptor.capability(capability_id) if adapter else None

    # ------------------------------------------------------------ lifecycle

    def state(self, adapter_id):
        return self._states.get(adapter_id)

    def probe(self, adapter_id, now=None):
        """Run the adapter's READ_ONLY probe and move it along the lifecycle.

        A probe that raises is a defect, not a crash: it becomes an UNAVAILABLE result with a
        diagnostic, so no caller ever sees an opaque tool exception.
        """
        adapter = self.get(adapter_id)
        if adapter is None:
            return model.ProbeResult(adapter_id, model.UNAVAILABLE, detail="not registered",
                                     diagnostics=(dg.make("ADAPTER_NOT_FOUND",
                                                          f"no tool adapter {adapter_id!r} is registered", adapter_id),))
        try:
            result = adapter.probe()
        except Exception as exc:  # an adapter defect must not escape as a subprocess traceback
            result = model.ProbeResult(adapter_id, model.UNAVAILABLE,
                                       detail=f"probe raised {type(exc).__name__}",
                                       diagnostics=(dg.make("ADAPTER_INTERNAL_ERROR",
                                                            f"{adapter_id}.probe() raised {type(exc).__name__}: {exc}",
                                                            adapter_id),))
        if not isinstance(result, model.ProbeResult) or result.status not in model.PROBE_STATUSES:
            result = model.ProbeResult(adapter_id, model.UNAVAILABLE, detail="probe returned no ProbeResult",
                                       diagnostics=(dg.make("ADAPTER_INTERNAL_ERROR",
                                                            f"{adapter_id}.probe() must return a ProbeResult with a "
                                                            f"status in {list(model.PROBE_STATUSES)}", adapter_id),))
        previous = self._states[adapter_id]
        self._states[adapter_id] = model.AdapterState(
            adapter_id, result.state, result, now, tuple(previous.history) + (previous.state,))
        return result

    def ready(self, adapter_id, now=None):
        """(ProbeResult, [ToolDiagnostic]) — probe if needed; empty diagnostics only when READY."""
        state = self.state(adapter_id)
        result = state.probe if state and state.state in (model.READY, model.UNAVAILABLE, model.INCOMPATIBLE) \
            else self.probe(adapter_id, now)
        if result is None:
            return None, [dg.make("ADAPTER_NOT_FOUND", f"no tool adapter {adapter_id!r} is registered", adapter_id)]
        if result.status == model.AVAILABLE:
            return result, []
        if result.status == model.VERSION_UNSUPPORTED:
            return result, [dg.make("TOOL_VERSION_UNSUPPORTED",
                                    f"{adapter_id}: {result.detail or 'the installed tool version is not supported'}",
                                    adapter_id)] + list(result.diagnostics)
        return result, [dg.make("TOOL_NOT_FOUND", f"{adapter_id}: {result.detail or 'the tool is not available'}",
                                adapter_id)] + list(result.diagnostics)

    def to_dict(self):
        return {"adapters": [d.to_dict() for d in self.list_adapters()],
                "states": {a: self._states[a].state for a in self.adapter_ids()},
                "allow_test_only": self.allow_test_only}


def default_registry(framework=None):
    """The production tool adapter registry.

    Phase 2C-0 registers nothing: no adapter for any real production tool exists yet (see
    tools/adapter-foundation.md). The registry is where they arrive in later phases, one at a time.
    """
    from ..framework import load_framework
    return ToolRegistry(framework or load_framework(), allow_test_only=False)
