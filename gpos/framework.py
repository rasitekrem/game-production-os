"""Loads the frozen GPOS framework the validator enforces: registry, schemas and VERSION.

core/registry.json is the canonical machine-readable vocabulary. The validator never
parses Markdown at runtime and does not hand-duplicate registry vocabulary. The few
literal tokens the rules name directly (for example the TARGET_RUNTIME capture context)
are listed in REQUIRED_TOKENS and verified against the registry and schemas at load time,
so a renamed or removed term fails loudly instead of silently changing behaviour.
"""

import json
from pathlib import Path

from .errors import FrameworkLoadError
from .schema import SchemaValidator

SCHEMA_NAMES = ("project-config", "decision", "task-routing", "gate", "evidence")

REQUIRED_REGISTRY_KEYS = (
    "gpos_version", "gates", "gate_statuses", "review_policies", "review_policy_strength",
    "evidence_types", "capture_contexts", "evidence_context_compatibility", "evidence_conditions",
    "decision_kinds", "decision_ref_fields", "decision_value_bindings", "decision_subject_rules",
    "record_id_uniqueness", "lifecycle_stages", "lifecycle_transitions", "project_trigger_prefix",
    "workflows", "workflow_gate_requirements", "cross_review_eligibility", "never_cross_reviewer",
    "platforms", "release_primary_platform_coverage", "skills", "instrumentation_timing_impacts",
)

# Literal tokens used by the rules, grouped by the registry list that must contain them.
REQUIRED_TOKENS = {
    "capture_contexts": ("TARGET_RUNTIME", "PERFORMANCE_RUNTIME"),
    "evidence_types": ("DEVICE_EVIDENCE", "PERFORMANCE_EVIDENCE", "HUMAN_EVIDENCE"),
    "evidence_conditions": ("TARGET_PRESENTATION_DIFFERS",),
    "gate_statuses": ("PASS",),
    "review_policies": ("ROUTINE", "CROSS_REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED"),
    "instrumentation_timing_impacts": ("MATERIAL", "UNKNOWN"),
    "lifecycle_stages": ("CONCEPT",),
    "decision_kinds": ("LIFECYCLE_TRANSITION",),
    "workflows": ("release", "golden-gameplay-cell"),
    "actor_kinds": ("HUMAN", "AGENT"),
    "placeholders": ("UNDECIDED",),
}
HUMAN_REVIEW_GATE = "HUMAN_REVIEW"
# The uniqueness scopes gpos.validation.ids enforces, verified against registry record_id_uniqueness.
RECORD_ID_UNIQUENESS = {
    "decision records": "decision_id", "evidence records": "evidence_id", "gate records": "gate_id",
    "routing records": "task_id", "project-config:/decision_authorities": "id",
    "project-config:/human_review/reviewers": "id", "project-config:/human_review/additional_mandatory_triggers": "id",
}

_DEFAULT_ROOT = Path(__file__).resolve().parent.parent
_CACHE = {}


class Framework:
    """Read-only view of one GPOS framework version."""

    def __init__(self, root, version, registry, schemas):
        self.root = root
        self.version = version
        self.registry = registry
        self.schemas = schemas
        self.validators = {name: SchemaValidator(schema, name) for name, schema in schemas.items()}
        self.gates = registry["gates"]
        self.gate_names = list(registry["gates"])
        self.policy_strength = registry["review_policy_strength"]
        self._check_consistency()

    def _check_consistency(self):
        reg = self.registry
        missing = [k for k in REQUIRED_REGISTRY_KEYS if k not in reg]
        if missing:
            raise FrameworkLoadError(f"registry is missing keys {missing}")
        if reg["gpos_version"] != self.version:
            raise FrameworkLoadError(f"registry gpos_version {reg['gpos_version']!r} != VERSION {self.version!r}")
        for key, tokens in REQUIRED_TOKENS.items():
            have = reg.get(key)
            if have is None:
                raise FrameworkLoadError(f"registry is missing {key!r}")
            for token in tokens:
                if token not in have:
                    raise FrameworkLoadError(f"registry {key} no longer contains {token!r}, which the validator relies on")
        if HUMAN_REVIEW_GATE not in reg["gates"]:
            raise FrameworkLoadError("registry gates no longer contain HUMAN_REVIEW")
        for wf in reg["workflows"]:
            if wf not in reg["workflow_gate_requirements"]:
                raise FrameworkLoadError(f"workflow {wf!r} has no workflow_gate_requirements entry")
        for key in reg["decision_ref_fields"]:
            name = key.split(":", 1)[0]
            if name not in self.schemas:
                raise FrameworkLoadError(f"decision_ref_fields names unknown record type {name!r}")
        uniq = reg["record_id_uniqueness"]
        for label, field in RECORD_ID_UNIQUENESS.items():
            if uniq.get(label) != field:
                raise FrameworkLoadError(f"registry record_id_uniqueness no longer maps {label!r} to {field!r}")
        for gname, etype in reg["release_primary_platform_coverage"].items():
            if gname not in reg["gates"] or etype not in reg["evidence_types"]:
                raise FrameworkLoadError(f"release_primary_platform_coverage entry {gname}:{etype} is not in the registry")

    # ------------------------------------------------------------ registry helpers

    def eligible_cross_reviewer(self, owner, reviewer):
        """Registry cross_review_eligibility; never_cross_reviewer always wins."""
        reg = self.registry
        return reviewer not in reg["never_cross_reviewer"] and reviewer in reg["cross_review_eligibility"].get(owner, [])

    def needs_cross_review(self, policy, cross_review_required):
        return policy == "CROSS_REVIEW_REQUIRED" or (policy == "HUMAN_REVIEW_REQUIRED" and bool(cross_review_required))


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FrameworkLoadError(f"cannot load {path}: {exc}") from exc


def load_framework(root=None):
    """Load (once per root) the GPOS framework next to this package, or at `root`."""
    root = Path(root).resolve() if root is not None else _DEFAULT_ROOT
    if root in _CACHE:
        return _CACHE[root]
    try:
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise FrameworkLoadError(f"cannot read {root / 'VERSION'}: {exc}") from exc
    registry = _read_json(root / "core" / "registry.json")
    schemas = {n: _read_json(root / "schemas" / f"{n}.schema.json") for n in SCHEMA_NAMES}
    fw = Framework(root, version, registry, schemas)
    _CACHE[root] = fw
    return fw
