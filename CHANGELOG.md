# Changelog

All notable changes to Game Production OS. Format based on Keep a Changelog; versions follow Semantic Versioning as defined in [README.md § Versioning](README.md#versioning).

Maturity promotions of skills are recorded here, each with the Human Decision and evidence references that authorized it.

## [1.0.0-alpha.9] — Phase 2B: agent adapter layer

Builds on the frozen Phase-1 core (`v1.0.0-alpha.7`) and the frozen Phase-2A validator (`v1.0.0-alpha.8`). No change to gate, evidence, authority, routing, lifecycle or validator semantics. Projects must pin `gpos_version` `1.0.0-alpha.9`.

### Added

- `gpos/adapters/`: model-independent agent adapter layer. Explicit source selection and hashing, one agent-independent IR, all GPOS meaning composed in one module, and format-only backends for Claude Code (`CLAUDE.md`, `.claude/skills/gpos-*/`) and Codex (`AGENTS.md`, `.agents/skills/gpos-*/`).
- `python3 -m gpos.adapters render|sync|check` with exit codes 0 OK, 1 INVALID, 2 DRIFT, 3 ERROR, 4 CONFLICT.
- Manifests with provenance (source ids and sha256, per-file sources and semantic blocks) and a `semantics` parity block; deterministic, without timestamps.
- Strict ownership: human-written entry files are never overwritten or adopted, the managed area is limited, paths are safe, sync is atomic and idempotent, and drift is detected without regenerating.
- Context budgets and progressive disclosure: a concise root, one skill per specialist, workflow references loaded on demand. Monolithic renders are rejected.
- Registry `adapter_ids`, `adapter_extension_key`, `agent_operating_contract` and `project_lock_binding`; `adapters/README.md`, `adapters/claude-code.md`, `adapters/codex.md`.
- Tests (`tests/test_adapters.py`), a synthetic adapter project, layout and root snapshots, and an adapter mutation harness.

### Hardened (Human Review of Phase 2B)

- Project Locked Authority is bound to its Human Decision. A `LOCK` decision's structured `value.locks` must target the exact row and current value, or the document's canonical hash (registry `project_lock_binding`). An authorized decider, `ACTIVE` status and this project as subject are also required; otherwise generation fails closed (`AUTHORITY_LOCK_UNVERIFIED`). A read-only `authority` command prints the exact bindings.
- Strict authority-document parsing: malformed, duplicate or near-miss authority rows and metadata fail (`AUTHORITY_DOCUMENT_INVALID`) instead of being skipped.
- Unmanaged project instruction layers are detected and block sync and check (`INSTRUCTION_LAYER_CONFLICT`): Codex nested or override `AGENTS` files; Claude Code `.claude/CLAUDE.md`, `CLAUDE.local.md`, nested `CLAUDE.md`, `.claude/rules/` and unowned `AGENTS.md`. Generated text no longer claims such layers cannot relax GPOS.
- Project runtime configuration that changes instruction discovery blocks sync and check (`INSTRUCTION_CONFIG_CONFLICT` / `INSTRUCTION_CONFIG_UNREADABLE`):
  - Claude Code project or local `claudeMdExcludes`;
  - Codex project `.codex/config.toml` `model_instructions_file`, a `project_doc_max_bytes` below the generated root, `[[skills.config]]` disabling a generated skill;
  - `project_doc_fallback_filenames` matching unowned files.
- Project-local skills occupying a generated skill id are rejected (`SKILL_ID_CONFLICT`).
- Authority tables must belong to a `##` section.
- The user-level, admin and global agent configuration trust boundary is documented.
- Project-scoped generated skill ids, `gpos-<namespace>-<skill>`: deterministic, 64 characters at most. Manifests map them to the logical ids.
- The rules generated instructions state come from registry `agent_operating_contract`: statements with verbatim-quoted frozen sources, whose documents are hashed as sources. `content.py` is a renderer and authors no rule.

### Changed

- The Phase-2A/Phase-1 boundary allows `adapters/*.md` documentation; code stays in `gpos/` and `tests/`. The vocabulary check accepts registered adapter ids.

### Maturity

- No skill promotions. All 13 skills `DRAFT`; adapters copy maturity and never change it.

## [1.0.0-alpha.8] — Phase 2A: production validator

Builds on the frozen Phase-1 core (`v1.0.0-alpha.7`). No change to gate, evidence, authority, routing or lifecycle semantics; records valid under alpha.7 remain valid, but must pin `gpos_version` `1.0.0-alpha.8` to be validated.

### Added

- `gpos/`: deterministic, read-only, fail-closed production validator (standard library only). Library API `load_project_record_set`, `validate_project`, `validate_routing`, `evaluate_readiness`; CLI `python3 -m gpos.validator validate|readiness` with exit codes 0/1/2/3 and text or JSON output.
- Project record bundle convention `.game/gpos/` (project config, decisions, routings, gates, evidence).
- Structured diagnostics with stable codes, severities (`ERROR` for record validity, `BLOCKER` for routed readiness), JSON pointers, files, related ids and rule references.
- Every GOVERNANCE §12 requirement (1–44) enforced and mapped to codes and tests ([tools/validator/README.md](tools/validator/README.md)).
- Production tests: parity with the frozen reference model on every Phase-1 authority and record fixture, readiness parity, named adversarial regressions, read-only, determinism, CLI and performance tests; synthetic bundles; production mutation harness.

### Changed

- Phase-1 boundary tests assert the Phase-2A boundary (code only in `tests/` and `gpos/`; `tools/` documentation only; `adapters/` unchanged).
- The vocabulary check accepts validator diagnostic codes and verdicts.

### Semantic alignment (Human Review of Phase 2A)

- Routed readiness is scoped: project-global authority plus the routing's own dependency records. Errors in unrelated routings no longer block it; `validate` still reports the whole project, and readiness reports them only as `project_has_other_diagnostics`.
- A project pinned to another GPOS version raises `UnsupportedGposVersion` (`UNSUPPORTED_GPOS_VERSION`, severity `INCOMPATIBLE`, category `COMPATIBILITY`, CLI exit 3); it is no longer reported as invalid records.
- Timestamps follow the frozen RFC 3339 contract: lower-case `t` / `z` are accepted, as by the Phase-1 oracle and the `jsonschema` cross-check. The stricter behaviour of `rfc3339-validator` called directly is recorded as a known external-checker divergence.
- Four machine-readable result classes with distinct exit codes: `VALID`/`READY` (0), `INVALID` (1), `NOT_READY` (2), `INCOMPATIBLE` (3). `ReadinessResult.status` and the CLI `verdict` report `INVALID`, never `NOT_READY`, when the routing's scope has errors (fixes `NOT_READY` with exit 1).
- The `ERROR` / `BLOCKER` split is documented as the normative Phase-2A diagnostic convention. Parity is required on verdicts and frozen rules, not on diagnostic counts.

### Normative contract enforced beyond the reference oracle (approved at Human Review)

- §12.6: a superseded negative cross-review needs a later review by the same reviewer.
- §12.12: `HUMAN_EVIDENCE` cited by a gate must come from a human authorized to review that gate.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.7] — Phase 1 freeze candidate

Independent review of alpha.6: three production-level contract gaps closed. Incompatible with alpha.6 records.

### Added

- Machine-readable cross-review eligibility (registry `cross_review_eligibility`, identical to the ROLE-ROUTING table) and `never_cross_reviewer` (`game-director`); non-standard reviewers escalate to `HUMAN_REVIEW_REQUIRED`.
- Release all-gate accounting (`account_for_all_gates` for `release`).
- Shared target-platform vocabulary (registry `platforms`) for project config and evidence; target-runtime evidence bound to declared project platforms; `DEVICE_EVIDENCE` bound to declared reference devices.
- Release PRIMARY-platform coverage for `DEVICE` and `PERFORMANCE` (registry `release_primary_platform_coverage`).
- GOVERNANCE §12 requirements 38–44.

### Breaking

- `provenance.target_platform` is an enum of the project platform vocabulary (free text rejected).
- A cross-review by `game-director` is invalid (schema).
- Release routing must account for all 12 quality gates.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.6] — Phase 1 routing authority closure

Independent review of alpha.5: five routing/authority gaps closed. Incompatible with alpha.5 records.

### Added

- Effective review policy (registry `effective_review_policy_precedence`, `review_policy_strength`): mandatory triggers, then applicable project override, then routing; routing may be stronger, never weaker.
- Workflow gate requirements (registry `workflow_gate_requirements`): `always_required` / `when_affected` per workflow, mirrored in each workflow's REQUIRED GATES section; Golden Cell `account_for_all_gates`.
- Routing evidence contract: required evidence must be valid for the gate and is enforced against the linked gate's counting evidence.
- Routed cross-reviewer authorization for linked gates.
- Complete conditional-evidence accounting (applied or declined, exactly once); presentation parity `YES` forces `TARGET_PRESENTATION_DIFFERS`.
- GOVERNANCE §12 requirements 29–37.

### Breaking

- Review-policy override `scope` (config and `REVIEW_POLICY_OVERRIDE` decision value) is now an object `{kind, ref}` matched against the routing subject.
- Routing records must account for every condition of every required gate and include every workflow always-required gate.
- `routed_scope_ready` (reference model) takes the evidence record set.

### Fixed

- Golden Cell: irrelevant gates are omitted from routing with a reason instead of being recorded `NOT_APPLICABLE`, consistent with routing-aware readiness.
- `character-production`: `HUMAN_REVIEW` listed as a when-affected gate (conditional trigger), not always required.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.5] — Phase 1 final local hardening (binding and readiness)

Independent adversarial review of alpha.4: cross-record binding and readiness integrity hardened. Incompatible with alpha.4 records.

### Added

- Evidence subject binding (`evidence.subject.kind`) and explicit, accountable `evidence_applicability` for cross-subject reuse.
- Routing `subject`; gate `routing_ref`; routing↔gate linkage checks and routing-aware readiness (`routed_scope_ready`).
- Generic decision-reference resolution for every record type (`resolve_decision_refs`).
- `BLOCKING_DOWNGRADE` / `KNOWN_ISSUE_ACCEPTANCE` payloads bound to the gate and its scope; registry `decision_subject_rules` for config-level decisions.
- Project-specific mandatory triggers as structured ids, referenced in routing as `PROJECT:<id>`.
- GOVERNANCE §12 requirements 19–28.

### Breaking

- `evidence.subject` requires `kind`; routing requires `subject`.
- `cross_reviews[].reviewed_revision` is required.
- `ROUTINE` `PASS` requires the owning specialist as assessor with a `PASS` assessment.
- `human_review.additional_mandatory_triggers` entries are objects with `id` and `description`.
- Blocking-downgrade decisions must carry `value.gate` and the downgraded scope as subject.

### Fixed

- Evidence about another subject no longer proves a gate because revisions match.
- Fake decision references in gate and routing records are no longer ignored.
- Readiness no longer ignores missing required gates.
- Presentation-parity checks apply to every routing, not only when one routing exists.
- `character-production`: `CANONICAL_CREATIVE_ASSET` is now conditional (only when canon is created or changed), matching the registry, which lists no unconditional trigger for that workflow.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.4] — Phase 1 final hardening (authority and referential semantics)

Independent review of alpha.3: architecture accepted in principle; authority and referential semantics hardened. Incompatible with alpha.3 records.

### Added

- Kind-specific decision payloads (registry `decision_payloads`) and value bindings (registry `decision_value_bindings`): `QUALITY_TARGET`, `REVIEW_POLICY_OVERRIDE` (gate, policy, scope), `EDITOR_CONCURRENCY`, `GPOS_UPGRADE` (from/to versions), alongside existing `PRESENTATION_PARITY` and `LIFECYCLE_TRANSITION`.
- GPOS upgrade authority: `gpos_upgrade` in project config (`from_version`, `decision_ref`).
- Record-id uniqueness model (registry `record_id_uniqueness`) and record-set reference validation (`record_set_problems`, `index_unique`).
- Gate-aware Human Review authorization (`human_may_review`).
- Assertive RFC 3339 `date-time` checking in the framework validator; the `jsonschema` cross-check runs with its format checker.
- Carryover `proposed_by`.
- GOVERNANCE §12 requirements 13–18.

### Breaking

- Decisions of payload-carrying kinds must include their structured value.
- Evidence carryover must be approved by the gate owner or a human; CI, tools and devices are rejected.
- Identifiers used as cross-record keys (actor, human, authority and reviewer ids) must be non-empty and contain no whitespace.
- `quality_target` values come from registry `quality_targets`.

### Fixed

- The reference routing model no longer requires a specialist reviewer for `HUMAN_REVIEW_REQUIRED` gates unless `cross_review_required` is true.
- Reference lookups no longer silently use last-write-wins on duplicate ids.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.3] — Phase 1 corrections (independent review)

Independent Human architecture review of alpha.2: Phase 1 not accepted; corrections below. Incompatible with alpha.2 records.

### Added

- `game-engineering` specialist (13th skill): owns runtime software implementation; owns no gate — `qa-performance` stays the default `TECHNICAL` owner so implementation and verification are separate.
- `runtime-system` workflow and `templates/ENGINEERING.md`.
- Evidence type / capture-context compatibility model (registry `evidence_context_compatibility`, schema-enforced).
- Human Decision record: `schemas/decision.schema.json`, `examples/example-decision-record.json`; registry `decision_kinds` and `decision_ref_fields`.
- Lifecycle transition authority: `lifecycle_decision_ref`, registry `lifecycle_transitions`.
- Authorization model: `decision_authorities` and reviewer gate lists in project config.
- Gate scope kinds `MILESTONE`, `PROJECT`, `DECISION`; registry `trigger_scope_kinds`.
- Accountable assessor model (`assessed_by` = owner or human; `recorded_by`; CI/tools never assess).
- Authority fixtures and expanded Phase-2 referential-integrity and authorization requirements (GOVERNANCE §12).
- Documented future considerations (Golden Cell set, asset licensing provenance, further specialist domains).

### Breaking

- Gate records: `assessed_by` limited to `AGENT` (the owner) or `HUMAN`; required unless `NOT_RUN`; `CROSS_REVIEW_REQUIRED` `PASS` requires owner assessment `PASS` and no unresolved negative cross-review; `HUMAN_REVIEW_REQUIRED` requires cross-review only when `cross_review_required`, and disclosure of disagreements; `blocking_downgrade_ref` must be a decision id.
- Evidence records: capture context must be compatible with the evidence type.
- Project config: `decision_authorities` required; `lifecycle_decision_ref` required after `CONCEPT`; decided presentation parity requires a decision; all authority references use decision ids; `editor_concurrency.validated_workflow_ref` replaced by `decision_ref`.
- Blender adapters no longer listed as producing game `RUNTIME_EVIDENCE`.

### Fixed

- `game-director` no longer plans Human Review for every subjective gate — only `HUMAN_REVIEW_REQUIRED` gates and mandatory triggers.

### Maturity

- No skill promotions. All 13 skills `DRAFT`.

## [1.0.0-alpha.2] — Phase 1 final hardening

Human Review of alpha.1: architecture accepted with final hardening. The changes below are incompatible with alpha.1 records, so the pre-release number was incremented ([core/GOVERNANCE.md §6](core/GOVERNANCE.md#6-semantic-versioning)).

### Added

- Proportional review policy: `ROUTINE`, `CROSS_REVIEW_REQUIRED`, `HUMAN_REVIEW_REQUIRED`, with per-gate defaults in the registry.
- Seven mandatory Human Review triggers that nothing can relax; workflow-level triggers for `new-game`, `golden-gameplay-cell`, `release`.
- Base plus conditional evidence model with nine named evidence conditions.
- Capture-context vocabulary (`DCC_RENDER`, `EDITOR`, `TARGET_RUNTIME`, `DIAGNOSTIC_RUNTIME`, `PERFORMANCE_RUNTIME`, `OFFLINE_ANALYSIS`, `AUTOMATED_TEST`, `HUMAN_RECORD`) and evidence provenance (subject revision, build revision/id, artifact hash, target platform, device, tool version, instrumentation, supersedes).
- Revision-bound gate records and explicit, justified evidence carryover.
- Default visual responsibility split (lighting intent / environment lighting composition / lighting implementation / readability cross-review); primary visual owner for mixed scopes.
- `core/GOVERNANCE.md`, including the Phase-2 record-validation acceptance requirement.
- Human-evidence trust-boundary section in `core/HUMAN-AUTHORITY.md`.
- Record-set fixtures for staleness, supersession, context and instrumentation rules.

### Breaking

- Gate records: `human_review_required` and `human_review_waiver_ref` replaced by `review_policy` (+ `routine_basis`); `scope.version` replaced by required `scope.revision`; subjective `PASS` rules now follow the review policy.
- Routing records: per-gate `review_policy` required; `review_triggers` required; `applied_conditions` / `unapplied_conditions` added; `VISUAL_ART` requires an explicit owner.
- Evidence records: `environment` replaced by `provenance`; `subject.version` replaced by `provenance.subject_revision`; context vocabulary replaced.
- Project config: `subjective_gates_require_review` and `gate_overrides` replaced by `review_policy_overrides` and `additional_mandatory_triggers`; `presentation` added.
- Registry gate rules: `required_all` / `required_any` / `conditional` replaced by `base_evidence` / `conditional_evidence`; `GAMEPLAY_DESIGN` no longer requires motion evidence universally.

### Maturity

- No skill promotions. All skills `DRAFT`.

## [1.0.0-alpha.1] — Phase 1: Core architecture

### Added

- Core documents: principles, human authority, authority hierarchy, quality gates, evidence rules, role routing, production lifecycle, Golden Gameplay Cell, skill maturity, definition of done.
- `core/registry.json`: machine-readable canonical vocabulary (gates, statuses, evidence types, skills, workflows, lifecycle stages, per-gate evidence rules).
- Twelve specialist skill contracts, all `DRAFT`.
- Twelve workflow specifications.
- Fourteen project authority templates plus a templates guide.
- JSON Schemas: project config, task routing, gate record, evidence record.
- Generic examples for every schema.
- Standard-library framework validator with fixtures.
- Placeholder boundaries for adapters and tools.

### Maturity

- No skill promotions. All skills `DRAFT`.
