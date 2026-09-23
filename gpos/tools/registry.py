"""The tool adapter registry: which production tool adapters exist, and what they may do.

This is deliberately *not* the Phase-2B agent adapter registry. That layer projects GPOS
instructions into an agent's own instruction format; tool adapters are callable capabilities that
run underneath that work. The two never share a registry, an id space or a result contract.

Registration is explicit Python — there is no plugin discovery, no entry-point scanning, no
download and no network. Everything is validated against the frozen GPOS registry before it is
accepted, and listings are deterministic (sorted by id), so two runs produce the same view.

Production adapters are registered by `register_production_adapters()`; Phase 2C-1 adds the first,
the local version-control provenance adapter. The TEST_ONLY synthetic reference adapter is never a
production adapter: it must be registered deliberately, into a registry that was explicitly told to
allow test-only adapters.
"""

from . import diagnostics as dg
from . import model
from .redaction import redact, sanitize_all
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
                                                            f"{adapter_id}.probe() raised {type(exc).__name__}: "
                                                            f"{redact(str(exc))[0]}", adapter_id),))
        if not isinstance(result, model.ProbeResult) or result.status not in model.PROBE_STATUSES:
            result = model.ProbeResult(adapter_id, model.UNAVAILABLE, detail="probe returned no ProbeResult",
                                       diagnostics=(dg.make("ADAPTER_INTERNAL_ERROR",
                                                            f"{adapter_id}.probe() must return a ProbeResult with a "
                                                            f"status in {list(model.PROBE_STATUSES)}", adapter_id),))
        result = _sanitized_probe(result)
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


def _sanitized_probe(result):
    """Redact the free text an adapter controls in a probe result before any caller sees it."""
    import dataclasses
    diagnostics, _ = sanitize_all(result.diagnostics)
    availability = tuple((c, a, redact(r)[0] if isinstance(r, str) else r)
                         for c, a, r in result.capability_availability)
    return dataclasses.replace(result, detail=redact(result.detail)[0] if result.detail else result.detail,
                               tool_path=redact(result.tool_path)[0] if result.tool_path else result.tool_path,
                               tool_version=redact(result.tool_version)[0] if result.tool_version
                               else result.tool_version,
                               capability_availability=availability, diagnostics=tuple(diagnostics))


def register_production_adapters(registry):
    """Register every production tool adapter, in a fixed order. Explicit Python, no discovery.

    Phase 2C-1 added the local Git provenance adapter; Phase 2C-2 adds the two local media adapters, one
    per executable (ffprobe for inspection, FFmpeg for derived media), so that execution provenance names
    exactly one tool. Phase 2C-3 adds the Android Debug Bridge target-device adapter. TEST_ONLY adapters never appear here; a caller that wants the synthetic reference
    adapter registers it separately, into a registry constructed with `allow_test_only=True`.
    """
    from .adb import AdbAdapter
    from .ffmpeg import FfmpegAdapter
    from .ffprobe import FfprobeAdapter
    from .git import GitAdapter
    for adapter in (AdbAdapter(), FfmpegAdapter(), FfprobeAdapter(), GitAdapter()):
        registry.register(adapter)
    return registry


def default_registry(framework=None):
    """The production tool adapter registry: exactly the production adapters, nothing TEST_ONLY."""
    from ..framework import load_framework
    return register_production_adapters(ToolRegistry(framework or load_framework(), allow_test_only=False))
