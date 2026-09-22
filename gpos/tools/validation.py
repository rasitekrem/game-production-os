"""Registration-time validation of a tool adapter and its capabilities.

Everything here is checked once, when an adapter is registered, against the frozen registry — not
against a table kept in Python. An adapter that contradicts itself, invents vocabulary or declares
evidence it could not honestly produce never enters the registry, so no execution path has to
defend against it later.

The contradictions that matter most:

* a MUTATING + STATEFUL capability that does not require a single writer (registry
  `tool_adapter_policy`) — this is the frozen stateful-editor policy, and an adapter cannot opt out;
* a READ_ONLY capability that declares a side-effect scope, or a mutating one that does not;
* an (evidence type, capture context) pair the registry says cannot exist;
* an evidence type a tool may never produce (HUMAN_EVIDENCE);
* a capability whose declared capture context is not the context its execution observes, unless it
  is one the capability can only reach by derivation;
* a production adapter declaring the TEST_ONLY kind or family, or a test-only adapter being
  registered into a production registry.
"""

import re

from . import diagnostics as dg

ID = re.compile(r"^[a-z][a-z0-9]*(?:[-.][a-z0-9]+)*$")
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _problem(problems, adapter_id, message, capability=None):
    problems.append(dg.make("ADAPTER_REGISTRATION_INVALID", message, adapter_id, capability))


def validate_descriptor(framework, descriptor, allow_test_only=False):
    """[ToolDiagnostic] — empty when the adapter may be registered."""
    reg, pol = framework.registry, framework.registry["tool_adapter_policy"]
    problems = []
    aid = getattr(descriptor, "adapter_id", None)
    if not isinstance(aid, str) or not ID.match(aid or ""):
        _problem(problems, aid, f"adapter_id {aid!r} must be a lower-case identifier, e.g. media-cli")
        return dg.sort(problems)
    if not isinstance(descriptor.adapter_version, str) or not VERSION.match(descriptor.adapter_version or ""):
        _problem(problems, aid, f"adapter_version {descriptor.adapter_version!r} must be a semantic version")
    for field, key in (("tool_family", "tool_families"), ("adapter_kind", "tool_adapter_kinds"),
                       ("state_model", "tool_state_models")):
        value = getattr(descriptor, field)
        if value not in reg[key]:
            _problem(problems, aid, f"{field} {value!r} is not in registry {key} {reg[key]}")
    if not descriptor.target_tool:
        _problem(problems, aid, "target_tool must name the tool this adapter drives")
    if not descriptor.supported_platforms:
        _problem(problems, aid, "supported_platforms must list at least one platform")
    for p in descriptor.supported_platforms:
        if p not in reg["platforms"]:
            _problem(problems, aid, f"supported platform {p!r} is not in registry platforms")
    test_markers = {descriptor.adapter_kind, descriptor.tool_family} & {pol["test_only_adapter_kind"]}
    if test_markers and not allow_test_only:
        _problem(problems, aid, f"{aid} is declared {sorted(test_markers)[0]}; a test-only adapter is never registered "
                                f"as a production tool adapter")
    if descriptor.network != pol["network"]:
        _problem(problems, aid, f"network {descriptor.network!r} is not permitted: the tool foundation default is "
                                f"{pol['network']!r} and Phase 2C-0 adds no network capability")
    if not descriptor.capabilities:
        _problem(problems, aid, "an adapter must declare at least one capability")
    seen = set()
    for cap in descriptor.capabilities:
        if cap.id in seen:
            _problem(problems, aid, f"duplicate capability id {cap.id!r}", cap.id)
            continue
        seen.add(cap.id)
        problems += validate_capability(framework, aid, cap)
    return dg.sort(problems)


def validate_capability(framework, adapter_id, cap):
    reg, pol = framework.registry, framework.registry["tool_adapter_policy"]
    problems = []
    if not isinstance(cap.id, str) or not ID.match(cap.id or ""):
        _problem(problems, adapter_id, f"capability id {cap.id!r} must be a lower-case identifier, e.g. media-cli.extract-frame")
        return problems
    if not cap.id.startswith(f"{adapter_id}."):
        _problem(problems, adapter_id, f"capability id {cap.id!r} must be scoped to its adapter ({adapter_id}.<name>)",
                 cap.id)
    if cap.category not in reg["tool_capability_categories"]:
        _problem(problems, adapter_id, f"category {cap.category!r} is not in registry tool_capability_categories", cap.id)
    if cap.operation_class not in reg["tool_operation_classes"]:
        _problem(problems, adapter_id, f"operation_class {cap.operation_class!r} is not in registry "
                                       f"tool_operation_classes", cap.id)
    if cap.state_model not in reg["tool_state_models"]:
        _problem(problems, adapter_id, f"state_model {cap.state_model!r} is not in registry tool_state_models", cap.id)
    if cap.execution_context not in reg["capture_contexts"]:
        _problem(problems, adapter_id, f"execution_context {cap.execution_context!r} is not a registry capture context",
                 cap.id)
    if not cap.description:
        _problem(problems, adapter_id, "a capability must describe what it does", cap.id)
    needs_writer = (cap.operation_class == pol["single_writer_operation_class"]
                    and cap.state_model == pol["single_writer_state_model"])
    if needs_writer and not cap.single_writer_required:
        _problem(problems, adapter_id,
                 f"{cap.id} is {cap.operation_class} + {cap.state_model} and must declare single_writer_required "
                 f"(registry tool_adapter_policy); the stateful-editor policy is not optional", cap.id)
    if cap.single_writer_required and not cap.resource_kind:
        _problem(problems, adapter_id, f"{cap.id} requires a single writer but names no resource_kind to take the "
                                       f"lease on", cap.id)
    if cap.single_writer_required and cap.operation_class == "READ_ONLY":
        _problem(problems, adapter_id, f"{cap.id} is READ_ONLY and must not take a writer lease", cap.id)
    if cap.mutating and cap.side_effect_scope in (None, "", "NONE"):
        _problem(problems, adapter_id, f"{cap.id} is MUTATING and must state its side_effect_scope", cap.id)
    if not cap.mutating and cap.side_effect_scope not in (None, "", "NONE"):
        _problem(problems, adapter_id, f"{cap.id} is READ_ONLY and must not declare a side-effect scope "
                                       f"({cap.side_effect_scope!r}); classify it MUTATING instead", cap.id)
    for kind in cap.artifact_kinds:
        if kind not in reg["tool_artifact_kinds"]:
            _problem(problems, adapter_id, f"artifact kind {kind!r} is not in registry tool_artifact_kinds", cap.id)
    if cap.timeout.maximum <= 0 or cap.timeout.default <= 0 or cap.timeout.default > cap.timeout.maximum:
        _problem(problems, adapter_id, f"{cap.id}: timeout policy default {cap.timeout.default} / maximum "
                                       f"{cap.timeout.maximum} is not a usable range", cap.id)
    for pair in cap.potential_evidence:
        if not (isinstance(pair, (tuple, list)) and len(pair) == 2):
            _problem(problems, adapter_id, f"potential_evidence entry {pair!r} must be (evidence type, capture context)",
                     cap.id)
            continue
        etype, context = pair
        if etype not in reg["evidence_types"]:
            _problem(problems, adapter_id, f"{etype!r} is not a GPOS evidence type", cap.id)
            continue
        if context not in reg["capture_contexts"]:
            _problem(problems, adapter_id, f"{context!r} is not a GPOS capture context", cap.id)
            continue
        if etype in pol["forbidden_evidence_types"]:
            _problem(problems, adapter_id, f"{cap.id} declares {etype}, which only a human record can be; a tool "
                                           f"adapter may never produce it", cap.id)
        if context not in reg["evidence_context_compatibility"][etype]:
            _problem(problems, adapter_id,
                     f"{cap.id} declares {etype} captured in {context}, which GPOS does not allow "
                     f"(registry evidence_context_compatibility: {reg['evidence_context_compatibility'][etype]})",
                     cap.id)
    return problems
