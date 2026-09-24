# GPOS production validator (Phase 2A)

Status: Phase 2A · GPOS `1.0.0-alpha.15` · implementation in [`gpos/`](../../gpos/__init__.py)

The production validator checks a project's GPOS records (project config, Human Decisions, task routings, gate records, evidence records) against the frozen GPOS contracts: [core/registry.json](../../core/registry.json), [schemas/](../../schemas/) and the record-validation requirements of [core/GOVERNANCE.md §12](../../core/GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation). It is deterministic, read-only, fail-closed, standard-library only, and independent of any model, engine or tool.

It enforces records; it does not make creative decisions.

> **The validator does not decide whether art looks good. It verifies that the required qualified review/evidence/authority records exist and are internally valid. Human creative authority remains outside automated judgement.**

It does not execute, fix, approve, waive or promote anything, and it never creates a Human Decision.

## Contents

- [Layout](#layout)
- [Record bundle](#record-bundle)
- [Library use](#library-use)
- [Command line](#command-line)
- [Validation and readiness](#validation-and-readiness)
- [Read-only guarantee](#read-only-guarantee)
- [Diagnostics](#diagnostics)
- [Contract coverage](#contract-coverage)
- [Relation to the Phase-1 reference model](#relation-to-the-phase-1-reference-model)
- [Known limitations](#known-limitations)
- [Tests](#tests)

## Layout

```
gpos/
  __init__.py            public API and __version__
  errors.py              tool failures (distinct from findings about records)
  framework.py           loads registry, schemas and VERSION; verifies the tokens the rules rely on
  schema.py              production JSON Schema subset validator (unsupported keyword = load failure; RFC 3339)
  diagnostics.py         Diagnostic model, code table, deterministic ordering
  records.py             bundle discovery and loading
  validation/
    ids.py               identifier uniqueness (before any index is built)
    decisions.py         decision references in every record type
    authority.py         lifecycle, GPOS upgrade, overrides, effective policy, triggers, parity, human evidence
    routing.py           routing structure and registry workflow invariants
    gates.py             one gate against its evidence and reviews
    platforms.py         target-platform and reference-device binding
    human.py             gate-aware human authority and linked Human Review
    linkage.py           routing <-> gate linkage, routed evidence and reviewers, Release coverage, gate completion
    context.py           read-only indexes for one run
    scope.py             the records one routing's readiness depends on
    project.py           compatibility check, stage orchestration, validate_project / validate_routing / evaluate_readiness
  cli.py                 thin CLI
  validator/__main__.py  `python3 -m gpos.validator`
```

The package loads the framework from the repository it lives in (the [VERSION](../../VERSION) file, `core/registry.json`, `schemas/`). It never parses Markdown, never imports `tests/`, and never uses the network.

## Record bundle

A project keeps its machine-readable records next to its `.game/` authority documents:

```
<project>/.game/gpos/
  project-config.json    exactly one project config (schemas/project-config.schema.json)
  decisions/*.json       one Human Decision record per file
  routings/*.json        one task-routing record per file
  gates/*.json           one gate record per file
  evidence/*.json        one evidence record per file
```

- `--project` may name the project directory (containing `.game/gpos/`) or the bundle directory itself.
- Discovery is deterministic: entries are read in sorted name order.
- Identifiers come from record contents (`decision_id`, `task_id`, `gate_id`, `evidence_id`), never from file names. File names are free.
- Duplicate identifiers are reported, naming every file involved, before any index is built. Nothing is resolved by last-write-wins.
- Hidden entries (names starting with `.`) are ignored everywhere.
- Every other unexpected entry fails loudly: a non-JSON file in a record directory, a nested directory, an unknown top-level file or directory, unreadable files, invalid JSON, JSON that is not one object, a missing `project-config.json`. Nothing is silently skipped.

## Library use

```python
import sys; sys.path.insert(0, "path/to/game-production-os")
from gpos import load_project_record_set, validate_project, validate_routing, evaluate_readiness

rs = load_project_record_set("path/to/project")   # raises BundleNotFound; data problems become diagnostics
result = validate_project(rs)                       # ValidationResult(valid, diagnostics, summary) — whole project
scoped = validate_routing(rs, "TASK-0001")          # ValidationResult for one routing's scope
ready = evaluate_readiness(rs, "TASK-0001")         # ReadinessResult(routing_id, valid_records, ready, blocking_reasons,
                                                    #   diagnostics, summary, project_has_other_diagnostics)
print(result.to_dict())                             # JSON-serializable
```

All three raise `UnsupportedGposVersion` when the project pins a GPOS version other than the one this validator implements (see [GPOS version compatibility](#gpos-version-compatibility)).

In-memory records: `gpos.from_records(config, decisions, evidence, gates, routings)` builds the same record set without files. Results are plain data classes with `to_dict()`; diagnostics are immutable `Diagnostic` values.

Failures of the validator itself raise `GposToolError` subclasses (`FrameworkLoadError`, `UnsupportedSchemaKeyword`, `BundleNotFound`, `RoutingNotFound`, `UnsupportedGposVersion`). They are never turned into a verdict about records.

## Command line

```bash
python3 -m gpos.validator validate  --project PATH [--routing ID] [--format text|json]
python3 -m gpos.validator readiness --project PATH --routing ID   [--format text|json]
python3 -m gpos.validator --version
```

Run from the repository root (or with the repository root on the Python path). There are no mutating commands: no fix, approve, waive, promote or edit.

Every run ends in exactly one of four result classes. Each has its own exit code and machine-readable `status` / `verdict`; none is inferred from messages, and `ready: false` never hides which class applies:

| Result class | Library | CLI `verdict` | Exit |
|---|---|---|---|
| valid / ready | `ValidationResult.status == "VALID"`; `ReadinessResult.status == "READY"` | `VALID` / `READY` | 0 |
| invalid: a record, schema, authority or integrity error in the validated scope (the whole project for `validate`; the routing's scope for `readiness`) | `status == "INVALID"` (`valid` / `valid_records` false) | `INVALID` | 1 |
| not ready: records in scope valid, routed production requirements unmet (`readiness` only) | `status == "NOT_READY"` | `NOT_READY` | 2 |
| incompatible: the project pins another GPOS version | raises `UnsupportedGposVersion` (`status == "INCOMPATIBLE"`) | `INCOMPATIBLE` | 3 |

Invocation, tool and internal errors also exit 3, with verdict `ERROR`. There is no "not ready" with exit 1: a routing whose scope contains an error is `INVALID`.

Exit 3 covers an unknown command or format, a missing argument, a missing bundle, an unknown routing id (`ROUTING_NOT_FOUND`), a project pinned to another GPOS version (`UNSUPPORTED_GPOS_VERSION`; the JSON `error` carries its diagnostic), a registry or schema that cannot be loaded (`FRAMEWORK_LOAD_ERROR`, `UNSUPPORTED_SCHEMA_KEYWORD`) and any validator defect (`INTERNAL_ERROR`). Usage errors are reported as `USAGE_ERROR`. It is never used for a verdict, and no stack trace is printed.

`--format json` prints one deterministic JSON document (sorted keys, no timestamps, no absolute paths). It contains `status`, `tool`, `validator_version`, `gpos_version` (the framework this validator enforces), `project_id`, `command`, `verdict` (`VALID`, `INVALID`, `READY`, `NOT_READY`, `INCOMPATIBLE` or `ERROR`), `exit_code`, `summary` and `diagnostics`; `readiness` adds `routing_id`, `valid_records` (validity of the routing's scope), `ready`, `blocking_reasons` and the informational `project_has_other_diagnostics`.

The text form of `readiness` also lists each required gate with its state: its gate status, `NOT_RUN` when no record exists, `AMBIGUOUS` when several do, or `UNVERIFIED` when the record set could not be checked far enough to link gates.

## Validation and readiness

**Record validity** (`validate`) asks: are the records of the whole project well formed, consistent with each other and with project authority? After the [version compatibility](#gpos-version-compatibility) check it runs in fixed stages. A later stage runs only on a record set the earlier stages accepted, because cross-record rules are defined over schema-valid records with unique identifiers:

1. load — files and JSON;
2. schema — the five GPOS schemas, with RFC 3339 date-times;
3. identifiers — no duplicate record ids or project-config ids;
4. cross-record rules — decisions, authority, routing, linkage, evidence, reviews, platforms.

`summary.stages_completed` shows how far a run got.

**Readiness scope.** Readiness of a routing is judged over that routing's scope, not over the whole project. The scope (`gpos/validation/scope.py`) is:

- project-global authority: the project config (reviewers, decision authorities, project triggers, review-policy overrides, presentation parity, target platforms, lifecycle, Golden Cell, GPOS version) and every decision it references;
- the routing record (every record with its `task_id`);
- every gate linked to it by `routing_ref`, and every `HUMAN_REVIEW` gate those gates cite;
- every evidence record those gates cite;
- every decision the routing or those gates reference (e.g. blocking downgrades);
- every load problem (an unreadable or unparsable file cannot be attributed to a routing, so it is never assumed unrelated).

A referenced identifier pulls in every record carrying it, so a duplicate anywhere in the project still makes the reference ambiguous and blocks. Problems in other routings, their gates, and evidence or decisions nothing in scope references do not change the verdict. They still make `validate` report the project `INVALID`, and `readiness` reports them only through `project_has_other_diagnostics`. `validate --routing ID` reports the validity of that scope.

**Routed readiness** (`readiness`) asks: may this routed scope be called done? A routing is `READY` only when:

- the routing's scope has no `ERROR`;
- every blocking required gate has exactly one linked gate record, and that record is `PASS` (a missing record is `NOT_RUN`; `NOT_APPLICABLE` never satisfies a gate the routing still requires);
- every linked gate is listed by the routing;
- each linked `PASS` has counting evidence of every type the routing requires, and a current passing cross-review by a routed, eligible reviewer where its policy needs one;
- for `release`, every PRIMARY target platform has counting `DEVICE_EVIDENCE` and `PERFORMANCE_EVIDENCE` on that platform. SECONDARY platforms never block.

Readiness is fail-closed within its scope. Any `ERROR` in the scope blocks the routing (`RECORD_SET_INVALID`), and global authority problems block every routing. The gate-only notion "every existing blocking gate is PASS" is never used as proof: it cannot see a missing gate.

The Golden Cell and Release rules are routing rules. `golden-gameplay-cell` and `release` routings must account for all 12 gates (required, or omitted with a reason). Their mandatory triggers force `HUMAN_REVIEW_REQUIRED` on subjective gates, and the gates they require must all exist and pass.

## GPOS version compatibility

A validator implements exactly one GPOS contract version: the one in its repository's VERSION file, with that version's registry and schemas. It loads nothing over the network and does not validate across versions.

If the project config pins another `gpos_version`, the records are not judged at all. This is checked before load, schema and cross-record findings, because other-version records measured against this version's schemas could be reported as malformed when they are not. The library raises `UnsupportedGposVersion` (a `GposToolError`), carrying an `UNSUPPORTED_GPOS_VERSION` diagnostic of severity `INCOMPATIBLE` and category `COMPATIBILITY`. No `ValidationResult` or `ReadinessResult` is produced, so the condition can never read as "invalid records". The CLI exits 3 (tool/compatibility), not 1. A missing or non-string `gpos_version` is malformed data and is reported by the schema.

To validate such a project, use the validator of the pinned GPOS version, or migrate the project through a `GPOS_UPGRADE` decision.

## Read-only guarantee

The validator opens record files for reading only. It never writes, renames, deletes or touches them, and it creates no files (no caches, no reports) in the project. Library calls never mutate the record objects they are given. The test suite snapshots every bundle file's content hash, mtime and mode, runs every command in both formats, and asserts the snapshot is unchanged (`R01_ReadOnly`).

## Diagnostics

Every finding is a `Diagnostic`:

| Field | Meaning |
|---|---|
| `code` | stable identifier (table below) |
| `severity` | `ERROR` (records invalid), `BLOCKER` (routing not ready), `WARNING`, `INFO`, `INCOMPATIBLE` (validator cannot judge the records) |
| `category` | `LOAD`, `RECORD`, `READINESS` or `COMPATIBILITY` |
| `message` | human-readable explanation |
| `record_type`, `record_id` | the record the finding is about (project-config, decision, routing, gate, evidence) |
| `path` | JSON Pointer inside that record |
| `file` | bundle-relative file of that record |
| `related` | other ids, gates, platforms or files involved |
| `rule` | the frozen contract enforced, e.g. `GOVERNANCE §12.23` |
| `details` | structured extras (missing evidence types, platform, schema keyword…) |

### Diagnostic convention (normative for Phase 2A)

| Class | Severity / category | Meaning | Effect |
|---|---|---|---|
| Record integrity | `ERROR` / `LOAD` or `RECORD` | a record is unreadable, schema-invalid, has an ambiguous id, contradicts another record or project authority, or claims a status its own evidence and reviews do not support under the registry rules | project `INVALID` (exit 1); blocks readiness of every routing whose scope contains it |
| Routed readiness | `BLOCKER` / `READINESS` | the records are valid, but the routing has not reached the bar it sets: `MISSING_REQUIRED_GATE`, `HUMAN_REVIEW_MISSING`, `GATE_NOT_PASSED`, `GATE_NOT_IN_ROUTING`, `ROUTED_EVIDENCE_MISSING`, `ROUTED_CROSS_REVIEW_MISSING`, `CROSS_REVIEWER_NOT_ROUTED`, `CROSS_REVIEWER_NOT_ELIGIBLE`, `PRIMARY_PLATFORM_COVERAGE_MISSING`, `PRIMARY_PLATFORM_UNDECIDED`; plus `RECORD_SET_INVALID` when the scope has errors | routing `NOT_READY` (exit 2 if the scope is valid); never makes records invalid |
| Information | `INFO` / `READINESS` | `NON_BLOCKING_GATE_OPEN` | none |
| Compatibility | `INCOMPATIBLE` / `COMPATIBILITY` | `UNSUPPORTED_GPOS_VERSION` | raised as a tool error, exit 3; never part of a result |

Each code belongs to exactly one class. The class is fixed in `gpos.diagnostics.CODES` and asserted by `K01_DiagnosticClassification`, so the same finding is always classified the same way.

Diagnostics are sorted by severity, record type, record id, file, code, path and message, and exact duplicates are removed. Each code has exactly one severity.

| Code | Severity | Rule | Meaning |
|---|---|---|---|
| `PROJECT_CONFIG_MISSING` | ERROR | validator/README bundle layout | project-config.json is missing |
| `RECORD_UNREADABLE` | ERROR | validator/README bundle layout | record file cannot be read |
| `RECORD_INVALID_JSON` | ERROR | validator/README bundle layout | record file is not valid JSON |
| `RECORD_NOT_OBJECT` | ERROR | validator/README bundle layout | record file does not contain one JSON object |
| `UNKNOWN_RECORD_FILE` | ERROR | validator/README bundle layout | unexpected file or directory in the bundle |
| `UNSUPPORTED_GPOS_VERSION` | INCOMPATIBLE | GOVERNANCE §12.7 | project pins a GPOS version this validator does not implement |
| `SCHEMA_INVALID` | ERROR | GOVERNANCE §12.7/18 | record does not conform to its GPOS schema |
| `DUPLICATE_RECORD_ID` | ERROR | GOVERNANCE §12.15 | record id is not unique |
| `DUPLICATE_CONFIG_ID` | ERROR | GOVERNANCE §12.15 | project-config id is not unique |
| `DECISION_REF_NOT_FOUND` | ERROR | GOVERNANCE §12.8/25 | decision reference does not resolve |
| `DECISION_NOT_ACTIVE` | ERROR | GOVERNANCE §12.8 | referenced decision is not ACTIVE |
| `DECISION_KIND_MISMATCH` | ERROR | GOVERNANCE §12.8 | referenced decision has the wrong kind |
| `UNAUTHORIZED_DECIDER` | ERROR | GOVERNANCE §12.11 | decision made by someone not authorized for its kind |
| `DECISION_SUBJECT_MISMATCH` | ERROR | GOVERNANCE §12.8 | decision subject is not the one the field requires |
| `DECISION_VALUE_MISMATCH` | ERROR | GOVERNANCE §12.13/25 | decision payload does not match what it authorizes |
| `LIFECYCLE_TRANSITION_MISMATCH` | ERROR | GOVERNANCE §12.9 | lifecycle decision does not enter the claimed stage |
| `LIFECYCLE_TRANSITION_ILLEGAL` | ERROR | GOVERNANCE §12.9 | lifecycle transition is not allowed |
| `LIFECYCLE_WAIVER_REQUIRED` | ERROR | GOVERNANCE §12.9 | lifecycle transition requires a Golden Cell waiver |
| `GPOS_UPGRADE_SAME_VERSION` | ERROR | GOVERNANCE §12.14 | gpos_upgrade from_version equals gpos_version |
| `GPOS_UPGRADE_DECISION_REQUIRED` | ERROR | GOVERNANCE §12.14 | governed GPOS move has no GPOS_UPGRADE decision |
| `REVIEW_OVERRIDE_DUPLICATE` | ERROR | GOVERNANCE §12.30 | duplicate review-policy override for a gate and scope |
| `REVIEW_OVERRIDE_AMBIGUOUS` | ERROR | GOVERNANCE §12.30 | more than one override applies to a routed gate |
| `REVIEW_POLICY_WEAKER_THAN_EFFECTIVE` | ERROR | GOVERNANCE §12.31 | routed review policy is weaker than the effective policy |
| `PROJECT_TRIGGER_UNKNOWN` | ERROR | GOVERNANCE §12.27 | PROJECT:<id> trigger is not declared in project config |
| `CONDITION_DECLINE_NOT_AUTHORIZED` | ERROR | GOVERNANCE §12.10/36 | TARGET_PRESENTATION_DIFFERS declined without a decided NO |
| `CONDITION_REQUIRED_BY_PARITY` | ERROR | GOVERNANCE §12.36 | presentation parity YES requires TARGET_PRESENTATION_DIFFERS |
| `HUMAN_EVIDENCE_SOURCE_UNLISTED` | ERROR | GOVERNANCE §12.12 | HUMAN_EVIDENCE source is not a reviewer or decision authority |
| `ROUTING_GATE_DUPLICATE` | ERROR | GOVERNANCE §12.22 | gate listed more than once in routing |
| `ROUTING_GATE_REQUIRED_AND_OMITTED` | ERROR | GOVERNANCE §12.22 | gate is both required and omitted |
| `WORKFLOW_GATE_OMITTED` | ERROR | GOVERNANCE §12.29 | workflow always-required gate is omitted |
| `WORKFLOW_GATE_MISSING` | ERROR | GOVERNANCE §12.29 | workflow always-required gate is not required |
| `WORKFLOW_GATE_UNACCOUNTED` | ERROR | GOVERNANCE §12.37/40 | workflow must account for every quality gate |
| `ROUTING_PRIMARY_REPEATED` | ERROR | registry routing_rules | primary specialist repeated as secondary |
| `ROUTING_OWNER_NOT_ROUTED` | ERROR | registry routing_rules | gate owner is not a routed specialist or reviewer |
| `ROUTING_EVIDENCE_BELOW_MINIMUM` | ERROR | GOVERNANCE §12.32/35 | routing omits evidence the registry requires |
| `ROUTING_EVIDENCE_INVALID_FOR_GATE` | ERROR | GOVERNANCE §12.33 | routing requires evidence not valid for the gate |
| `CONDITION_DUPLICATE` | ERROR | GOVERNANCE §12.35 | condition listed more than once |
| `CONDITION_APPLIED_AND_DECLINED` | ERROR | GOVERNANCE §12.35 | condition both applied and declined |
| `CONDITION_NOT_DEFINED` | ERROR | GOVERNANCE §12.35 | declined condition is not defined for the gate |
| `CONDITION_UNACCOUNTED` | ERROR | GOVERNANCE §12.35 | defined condition neither applied nor declined |
| `ROUTING_CROSS_REVIEWER_MISSING` | ERROR | GOVERNANCE §12.34 | routing has no specialist cross-reviewer for a gate that needs one |
| `ROUTING_HUMAN_REVIEWER_MISSING` | ERROR | GOVERNANCE §12.3 | HUMAN_REVIEW_REQUIRED gate routed without HUMAN as reviewer |
| `ROUTING_REF_NOT_FOUND` | ERROR | GOVERNANCE §12.19 | gate routing_ref does not resolve |
| `ROUTED_GATE_AMBIGUOUS` | ERROR | GOVERNANCE §12.19 | more than one gate record for one routed gate |
| `ROUTING_GATE_MISMATCH` | ERROR | GOVERNANCE §12.20/21 | gate record disagrees with its routing entry |
| `EVIDENCE_NOT_FOUND` | ERROR | GOVERNANCE §12.1 | gate cites evidence that does not exist |
| `EVIDENCE_SUPERSEDED` | ERROR | GOVERNANCE §12.6 | gate cites superseded evidence |
| `EVIDENCE_TYPE_NOT_ACCEPTED` | ERROR | GOVERNANCE §12.1 | evidence type is not acceptable for the gate |
| `INVALID_EVIDENCE_CONTEXT` | ERROR | GOVERNANCE §12.1 | evidence type/capture-context combination is invalid |
| `EVIDENCE_CONTEXT_NOT_COUNTING` | ERROR | GOVERNANCE §12.1 | capture context does not count for the gate on this scope |
| `EVIDENCE_SUBJECT_MISMATCH` | ERROR | GOVERNANCE §12.23 | evidence proves a different subject |
| `STALE_EVIDENCE` | ERROR | GOVERNANCE §12.2 | evidence revision is not the gate revision |
| `CARRYOVER_REVISION_MISMATCH` | ERROR | GOVERNANCE §12.2 | carryover names a different evidence revision |
| `CARRYOVER_NOT_ACCOUNTABLE` | ERROR | GOVERNANCE §12.16 | carryover not approved by the owner or a human |
| `APPLICABILITY_NOT_ACCOUNTABLE` | ERROR | GOVERNANCE §12.23 | evidence applicability not approved by the owner or a human |
| `TARGET_PLATFORM_NOT_DECLARED` | ERROR | GOVERNANCE §12.41 | target-runtime evidence on an undeclared platform |
| `REFERENCE_DEVICE_MISMATCH` | ERROR | GOVERNANCE §12.42 | DEVICE_EVIDENCE not captured on a declared reference device |
| `INSTRUMENTATION_TIMING_UNUSABLE` | ERROR | EVIDENCE-RULES instrumentation | instrumented capture cannot prove timing |
| `GATE_OWNER_NOT_PERMITTED` | ERROR | GOVERNANCE §12.5 | owner may not own this gate |
| `ASSESSOR_NOT_OWNER` | ERROR | GOVERNANCE §12.5 | assessed_by is neither the owner nor a human |
| `CROSS_REVIEW_SELF` | ERROR | GOVERNANCE §12.5 | owner cross-reviewed its own gate |
| `CROSS_REVIEW_BY_NEVER_REVIEWER` | ERROR | GOVERNANCE §12.39 | game-director (never_cross_reviewer) recorded as cross-reviewer |
| `CROSS_REVIEW_STALE` | ERROR | GOVERNANCE §12.24 | active cross-review is of another revision |
| `CROSS_REVIEW_ELIGIBLE_MISSING` | ERROR | GOVERNANCE §12.38 | PASS lacks a current passing cross-review by an eligible specialist |
| `CROSS_REVIEW_SUPERSEDED_WITHOUT_REPLACEMENT` | ERROR | GOVERNANCE §12.6 | superseded negative cross-review has no later review by the same reviewer |
| `ROUTINE_PASS_NOT_OWNER` | ERROR | GOVERNANCE §12.28 | ROUTINE PASS is not the owner's own PASS assessment |
| `PASS_EVIDENCE_MISSING` | ERROR | GOVERNANCE §12.1 | PASS lacks evidence the registry requires |
| `PASS_INSUFFICIENT_EVIDENCE` | ERROR | GOVERNANCE §12.1 | PASS rests only on insufficient evidence |
| `GATE_ASSESSOR_UNAUTHORIZED` | ERROR | GOVERNANCE §12.17 | human assessor not authorized for the gate |
| `EVIDENCE_REUSE_APPROVER_UNAUTHORIZED` | ERROR | GOVERNANCE §12.16/17 | human approving evidence reuse not authorized for the gate |
| `HUMAN_REVIEW_REF_NOT_FOUND` | ERROR | GOVERNANCE §12.3 | human_review_ref does not resolve to a HUMAN_REVIEW record |
| `HUMAN_REVIEW_SCOPE_MISMATCH` | ERROR | GOVERNANCE §12.4/24 | linked Human Review is not a PASS for the same scope and revision |
| `HUMAN_REVIEWER_UNAUTHORIZED` | ERROR | GOVERNANCE §12.17 | human reviewer not authorized for the gate |
| `HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED` | ERROR | GOVERNANCE §12.12/17 | HUMAN_EVIDENCE cited by a gate its source may not review |
| `RECORD_SET_INVALID` | BLOCKER | GOVERNANCE §12.7 | the record set has errors; no routed scope can be READY |
| `MISSING_REQUIRED_GATE` | BLOCKER | GOVERNANCE §12.19 | required gate has no record (NOT_RUN) |
| `HUMAN_REVIEW_MISSING` | BLOCKER | GOVERNANCE §12.3/19 | required HUMAN_REVIEW gate has no record |
| `GATE_NOT_PASSED` | BLOCKER | GOVERNANCE §12.19 | blocking required gate is not PASS |
| `GATE_NOT_IN_ROUTING` | BLOCKER | GOVERNANCE §12.22 | linked gate is not listed by its routing |
| `CROSS_REVIEWER_NOT_ROUTED` | BLOCKER | GOVERNANCE §12.34 | passing cross-review by a reviewer the routing did not route |
| `CROSS_REVIEWER_NOT_ELIGIBLE` | BLOCKER | GOVERNANCE §12.38 | routed cross-reviewer is not eligible for the gate owner |
| `ROUTED_CROSS_REVIEW_MISSING` | BLOCKER | GOVERNANCE §12.34/38 | PASS lacks a passing cross-review by a routed eligible reviewer |
| `ROUTED_EVIDENCE_MISSING` | BLOCKER | GOVERNANCE §12.32 | PASS lacks counting evidence the routing requires |
| `PRIMARY_PLATFORM_COVERAGE_MISSING` | BLOCKER | GOVERNANCE §12.43/44 | Release lacks counting evidence for a PRIMARY platform |
| `PRIMARY_PLATFORM_UNDECIDED` | BLOCKER | GOVERNANCE §12.43 | a PRIMARY target platform is UNDECIDED |
| `NON_BLOCKING_GATE_OPEN` | INFO | GOVERNANCE §12.19 | non-blocking required gate is missing or not PASS |

## Contract coverage

Every GOVERNANCE §12 requirement, the codes that enforce it, and the tests that prove it. Tests are in `tests/test_production_validator.py` unless noted. "Schema" means the frozen JSON Schema already enforces the rule; the validator applies it through `SCHEMA_INVALID`, and the domain model keeps the rule as defence in depth where noted.

| §12 | Requirement | Codes | Tests |
|---|---|---|---|
| 1 | evidence type and capture context match the gate | `EVIDENCE_TYPE_NOT_ACCEPTED`, `INVALID_EVIDENCE_CONTEXT`, `EVIDENCE_CONTEXT_NOT_COUNTING`, `PASS_EVIDENCE_MISSING`, `PASS_INSUFFICIENT_EVIDENCE`, `EVIDENCE_NOT_FOUND` | `B02_RecordFixtureParity`, `D01_AdversarialRegressions` |
| 2 | evidence revision matches, or justified carryover | `STALE_EVIDENCE`, `CARRYOVER_REVISION_MISMATCH` | `B02_RecordFixtureParity`, `D01_AdversarialRegressions` |
| 3 | linked Human Review for every `HUMAN_REVIEW_REQUIRED` `PASS` | schema (`human_review_ref`), `HUMAN_REVIEW_REF_NOT_FOUND`, `HUMAN_REVIEW_MISSING`, `ROUTING_HUMAN_REVIEWER_MISSING` | `D01_AdversarialRegressions`, `test_direct_gate_rules_behind_the_schema` |
| 4 | Human Review verdict valid (human, `PASS`, same scope and revision, disclosed) | schema (`disagreements_disclosed`), `HUMAN_REVIEW_SCOPE_MISMATCH` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 5 | reviewer is not owner; assessment consistent with policy; assessor is owner or human | schema, `CROSS_REVIEW_SELF`, `ASSESSOR_NOT_OWNER`, `GATE_OWNER_NOT_PERMITTED`, `CROSS_REVIEW_ELIGIBLE_MISSING` | `test_direct_gate_rules_behind_the_schema`, `D01_AdversarialRegressions` |
| 6 | superseded evidence never counts; a superseded negative review has a later review by the same reviewer | `EVIDENCE_SUPERSEDED`, `CROSS_REVIEW_SUPERSEDED_WITHOUT_REPLACEMENT` | `B02_RecordFixtureParity`, `D01_AdversarialRegressions` |
| 7 | records conform to the registry and schemas of the pinned version | `SCHEMA_INVALID`, `UNSUPPORTED_GPOS_VERSION` | `A01_SchemaParity`, `L01_Loading`, `U01_UnsupportedGposVersion` |
| 8 | decision references resolve to an `ACTIVE` decision of an allowed kind for the right subject | `DECISION_REF_NOT_FOUND`, `DECISION_NOT_ACTIVE`, `DECISION_KIND_MISMATCH`, `DECISION_SUBJECT_MISMATCH` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 9 | lifecycle stage backed by an allowed transition decision (waiver where required) | schema, `LIFECYCLE_TRANSITION_MISMATCH`, `LIFECYCLE_TRANSITION_ILLEGAL`, `LIFECYCLE_WAIVER_REQUIRED` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 10 | presentation parity backed by a matching decision; declines consistent with it | `DECISION_VALUE_MISMATCH`, `CONDITION_DECLINE_NOT_AUTHORIZED` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 11 | `decided_by` is an authority allowed that kind | `UNAUTHORIZED_DECIDER` | `B01_AuthorityFixtureParity`, `R03_Cli` |
| 12 | `HUMAN_EVIDENCE` source is a Human Review participant for the gate, or an authority | `HUMAN_EVIDENCE_SOURCE_UNLISTED`, `HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 13 | decision payloads match what they authorize | `DECISION_VALUE_MISMATCH` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 14 | governed GPOS move backed by a matching `GPOS_UPGRADE` decision | `GPOS_UPGRADE_DECISION_REQUIRED`, `GPOS_UPGRADE_SAME_VERSION`, `DECISION_VALUE_MISMATCH` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 15 | identifiers unique before any lookup; no last-write-wins | `DUPLICATE_RECORD_ID`, `DUPLICATE_CONFIG_ID` | `B01_AuthorityFixtureParity`, `L01_Loading` |
| 16 | carryover approved by the owner or an authorized human | `CARRYOVER_NOT_ACCOUNTABLE`, `EVIDENCE_REUSE_APPROVER_UNAUTHORIZED` | `B02_RecordFixtureParity`, `D01_AdversarialRegressions` |
| 17 | reviewer gate permissions for verdicts, evidence and carryover | `GATE_ASSESSOR_UNAUTHORIZED`, `HUMAN_REVIEWER_UNAUTHORIZED`, `EVIDENCE_REUSE_APPROVER_UNAUTHORIZED`, `HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 18 | timestamps parsed as RFC 3339 | schema (`format`), `SCHEMA_INVALID` | `A02_Rfc3339` |
| 19 | exactly one gate record per required gate; missing is `NOT_RUN` | `MISSING_REQUIRED_GATE`, `HUMAN_REVIEW_MISSING`, `ROUTED_GATE_AMBIGUOUS`, `GATE_NOT_PASSED`, `ROUTING_REF_NOT_FOUND`, `NON_BLOCKING_GATE_OPEN` | `C01_ReadinessParity`, `S01_ScopedReadiness`, `K01_DiagnosticClassification` |
| 20 | routing and gate metadata agree | `ROUTING_GATE_MISMATCH` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 21 | gate `applied_conditions` equal routing's | `ROUTING_GATE_MISMATCH` | `B01_AuthorityFixtureParity` |
| 22 | required/omitted sets non-conflicting; unlisted linked gate blocks | `ROUTING_GATE_DUPLICATE`, `ROUTING_GATE_REQUIRED_AND_OMITTED`, `GATE_NOT_IN_ROUTING` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 23 | evidence subject is the gate subject, or accountably mapped | `EVIDENCE_SUBJECT_MISMATCH`, `APPLICABILITY_NOT_ACCOUNTABLE` | `B02_RecordFixtureParity`, `D01_AdversarialRegressions` |
| 24 | contributing reviews are of the current revision; Human Review matches scope | `CROSS_REVIEW_STALE`, `HUMAN_REVIEW_SCOPE_MISMATCH` | `B02_RecordFixtureParity`, `D01_AdversarialRegressions` |
| 25 | decision references in every record type, incl. downgrades bound to their gate and scope | `DECISION_REF_NOT_FOUND`, `DECISION_VALUE_MISMATCH` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 26 | every routing validated against project authority | `CONDITION_DECLINE_NOT_AUTHORIZED`, `REVIEW_POLICY_WEAKER_THAN_EFFECTIVE` | `B01_AuthorityFixtureParity`, `R04_Performance` |
| 27 | project triggers resolve and force Human Review | `PROJECT_TRIGGER_UNKNOWN`, `REVIEW_POLICY_WEAKER_THAN_EFFECTIVE` | `B01_AuthorityFixtureParity` |
| 28 | `ROUTINE` `PASS` is the owner's own `PASS` | schema, `ROUTINE_PASS_NOT_OWNER` | `test_direct_gate_rules_behind_the_schema`, `D01_AdversarialRegressions` |
| 29 | workflow `always_required` gates present, never omitted | `WORKFLOW_GATE_MISSING`, `WORKFLOW_GATE_OMITTED` | `D01_AdversarialRegressions` |
| 30 | applicable project overrides obeyed; ambiguous or duplicate rejected | `REVIEW_POLICY_WEAKER_THAN_EFFECTIVE`, `REVIEW_OVERRIDE_AMBIGUOUS`, `REVIEW_OVERRIDE_DUPLICATE` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 31 | trigger, then override, then routing; routing may be stronger, never weaker | `REVIEW_POLICY_WEAKER_THAN_EFFECTIVE` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 32 | routing-required evidence satisfied by counting evidence | `ROUTED_EVIDENCE_MISSING`, `ROUTING_EVIDENCE_BELOW_MINIMUM` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 33 | routing cannot require evidence invalid for the gate | `ROUTING_EVIDENCE_INVALID_FOR_GATE` | `D01_AdversarialRegressions` |
| 34 | contributing cross-reviewers are routed specialist reviewers | `CROSS_REVIEWER_NOT_ROUTED`, `ROUTED_CROSS_REVIEW_MISSING`, `ROUTING_CROSS_REVIEWER_MISSING` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 35 | every defined condition applied or declined exactly once | `CONDITION_UNACCOUNTED`, `CONDITION_DUPLICATE`, `CONDITION_APPLIED_AND_DECLINED`, `CONDITION_NOT_DEFINED` | `D01_AdversarialRegressions` |
| 36 | parity `YES` forces `TARGET_PRESENTATION_DIFFERS`; declining needs a decided `NO` | `CONDITION_REQUIRED_BY_PARITY`, `CONDITION_DECLINE_NOT_AUTHORIZED` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 37 | Golden Cell accounts for every gate | `WORKFLOW_GATE_UNACCOUNTED` | `D01_AdversarialRegressions`, `C01_ReadinessParity` |
| 38 | contributing cross-reviewer eligible for the owning discipline | `CROSS_REVIEW_ELIGIBLE_MISSING`, `CROSS_REVIEWER_NOT_ELIGIBLE` | `D01_AdversarialRegressions`, `E01_CrossReviewEligibility` (Phase-1 suite) |
| 39 | game-director never a cross-reviewer | schema, `CROSS_REVIEW_BY_NEVER_REVIEWER` | `test_direct_gate_rules_behind_the_schema`, `D01_AdversarialRegressions` |
| 40 | Release accounts for every gate | `WORKFLOW_GATE_UNACCOUNTED` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 41 | target-runtime evidence on a declared target platform | `TARGET_PLATFORM_NOT_DECLARED` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 42 | `DEVICE_EVIDENCE` on a declared reference device | `REFERENCE_DEVICE_MISMATCH` | `B01_AuthorityFixtureParity`, `D01_AdversarialRegressions` |
| 43 | Release `DEVICE` and `PERFORMANCE` coverage for every PRIMARY platform | `PRIMARY_PLATFORM_COVERAGE_MISSING`, `PRIMARY_PLATFORM_UNDECIDED` | `C01_ReadinessParity`, `D01_AdversarialRegressions` |
| 44 | one platform's evidence never covers another | `PRIMARY_PLATFORM_COVERAGE_MISSING` | `D01_AdversarialRegressions` |

Load, readiness-aggregate and routing-structure codes not named above (`PROJECT_CONFIG_MISSING`, `RECORD_UNREADABLE`, `RECORD_INVALID_JSON`, `RECORD_NOT_OBJECT`, `UNKNOWN_RECORD_FILE`, `RECORD_SET_INVALID`, `ROUTING_PRIMARY_REPEATED`, `ROUTING_OWNER_NOT_ROUTED`, `INSTRUMENTATION_TIMING_UNUSABLE`, `CROSS_REVIEWER_NOT_ELIGIBLE`) implement the bundle convention and registry `routing_rules`; they are exercised in `L01_Loading` and `D01_AdversarialRegressions`.

## Relation to the Phase-1 reference model

The functions under "reference implementation of cross-record rules" in `tests/validate_framework.py` are the frozen executable specification. The production package re-implements them as structured rules. It does not import or call them, and a test checks this (`E01_NoReferenceImport`). The reference stays the regression oracle. Parity is required on verdicts and on frozen semantic rules, not on diagnostic counts:

- **verdict parity**, on every authority fixture: the reference reports problems exactly when production reports an `ERROR` or a reference-class `BLOCKER` (`B01_AuthorityFixtureParity`);
- **rule parity**, on every authority and record fixture: every reference problem maps to the production code of the same frozen rule, and production reports no other rule (`test_every_reference_problem_has_its_frozen_rule`, `B02_RecordFixtureParity`); counting evidence is identical;
- **readiness parity**, on every single-routing fixture and every bundle: production readiness equals reference `routed_scope_ready` over the same scope, and on the frozen fixtures also over the reference's full record list (`C01_ReadinessParity`).

Differences are classified as follows:

| Class | Differences | Status |
|---|---|---|
| A — true semantic divergence from a frozen rule | none | must stay zero |
| B — diagnostic classification (verdict and meaning unchanged) | routing-level demands the reference lists as record-set problems are readiness `BLOCKER`s here (7 authority fixtures; `gpos.diagnostics.REFERENCE_CLASS_BLOCKERS`); diagnostics are grouped by stable code, not counted like reference messages; readiness is judged over the routing scope, where the reference judged every record it was given (no frozen fixture changes verdict); a project pinned to another GPOS version is a compatibility error, not a verdict (fixture `authority/gpos-patch-upgrade-ungoverned.json`: its frozen rule, that a PATCH upgrade needs no decision, is checked at rule level) | allowed |
| C — known external-checker limitation | the `rfc3339-validator` package, called directly, rejects lower-case `t` / `z`, which RFC 3339 §5.6 and the frozen contract accept. Production, the oracle and the `jsonschema` cross-check (which upper-cases date-times before calling that package) all accept them; the direct behaviour is asserted as a recorded divergence (`A02_Rfc3339`) | recorded |

**NORMATIVE_CONTRACT_ENFORCED_BEYOND_REFERENCE_ORACLE.** Production also enforces two requirements whose §12 text the reference does not fully model. Human Review approved both as enforcement of frozen normative text. They are not semantic divergences, and they must not be weakened for reference parity. No frozen fixture exercises them, so they change no fixture verdict:

| Code | Contract text |
|---|---|
| `CROSS_REVIEW_SUPERSEDED_WITHOUT_REPLACEMENT` | §12.6 "a superseded negative cross-review has a later review by the same reviewer" |
| `HUMAN_EVIDENCE_SOURCE_NOT_AUTHORIZED` | §12.12 "a listed Human Review participant for the gate" (the reference checks this only through a linked `HUMAN_REVIEW` record) |

## Known limitations

These are documented, not solved, in Phase 2A:

- **Authentication.** The validator checks that a human id is authorized, never that the human actually acted ([HUMAN-AUTHORITY.md §7](../../core/HUMAN-AUTHORITY.md#7-authenticity-of-human-evidence-trust-boundary)).
- **Routing history.** Only the current routing record is seen. A routing revised after its gates ran cannot be compared with its earlier version, and GPOS upgrade decisions are checked against the current pin only, not a version history (§12.14).
- **Free-text quality.** Reasons, justifications and notes must be present where the schema requires them; whether they are good reasons is Human Review.
- **One Golden Cell.** The one-cell model is enforced; a Golden Cell set is a future consideration.
- **Asset licensing and source provenance** are not modelled.
- **Hardware matrices.** Coverage is per PRIMARY platform and declared reference devices; selected-device matrices and hardware catalogues are not modelled.
- **Future specialist domains** (narrative, localization, accessibility, networking, economy / live operations) have no gates yet.
- **One GPOS version per validator.** Records pinned to another version are refused with a compatibility error (`UNSUPPORTED_GPOS_VERSION`, exit 3), neither validated nor migrated. There is no multi-version validation and no schema download.
- **Staged reporting.** Cross-record findings appear only after load, schema and identifier problems are fixed.
- **Unattributable load problems.** An unreadable or unparsable file blocks the readiness of every routing, because its content (and so its routing) is unknown.

## Tests

```bash
python3 tests/test_production_validator.py   # production validator (this document's guarantees)
python3 tests/validate_framework.py          # Phase-1 framework suite (unchanged semantics)
python3 tests/generate_bundles.py            # regenerate the synthetic bundles in tests/fixtures/bundles/
python3 tests/mutate_production.py           # bounded mutation harness for gpos/ (slow; copies the repo to a temp dir)
```

Synthetic bundles in `tests/fixtures/bundles/` (generic data, no real project): `minimal-valid/`, `gameplay-feature-ready/`, `gameplay-feature-not-ready/`, `golden-cell-ready/`, `golden-cell-incomplete/`, `release-multi-platform-ready/`, `release-missing-primary-coverage/`, `invalid-authority/`, `multi-routing-scoped/` (a READY routing next to an invalid one).
