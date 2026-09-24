# Governance

Status: normative · GPOS `1.0.0-alpha.14`

How Game Production OS itself changes. Project authority (`.game/`) is governed by [AUTHORITY-HIERARCHY.md](AUTHORITY-HIERARCHY.md); this document governs the framework.

---

## 1. Principle

**A game project does not modify GPOS automatically during production.**

The only path from project experience to framework change is:

```
project postmortem → framework-general lesson → GPOS change proposal → review → validation → versioned GPOS release
```

Agents working on a project may *propose* framework changes. They never edit the GPOS repository as a side effect of project work, and a project never runs on an unreleased framework change.

## 2. Change proposals

A change proposal is a written record (in Phase 1, a document or issue; no automation is defined) containing:

- **Problem** — what failed, was missing or was unclear, with evidence (postmortem reference, gate records, captures).
- **Scope** — the files, registry entries and schemas affected.
- **Change** — the concrete wording or data change.
- **Classification** — PATCH, MINOR or MAJOR (§6), with the reason.
- **Compatibility** — effect on existing project records and `.game/` documents; migration notes (§11).
- **Alternatives considered.**

Anyone may propose. Proposals are agent recommendations (authority level 7) until accepted.

## 3. Registry changes

`core/registry.json` is the canonical vocabulary. Changes to it:

1. are made only through an accepted proposal;
2. change the registry first, then schemas, documents, skills, workflows and templates in the same change;
3. must leave `python3 tests/validate_framework.py` passing, including the vocabulary, table and schema-consistency checks;
4. never reuse a removed name for a different meaning.

Adding a compatible term (a new evidence condition, trigger, capture context or scope kind) is MINOR. Renaming or removing a gate, status, evidence type, review policy, trigger, lifecycle stage, capture context or skill, or changing what an existing term means, is MAJOR.

## 4. Lesson intake

Every workflow ends with a postmortem asking: which GPOS rule helped; which rule was missing, wrong or unclear; and whether each lesson is project-specific or framework-general.

| Lesson kind | Where it goes | Who decides |
|---|---|---|
| Project-specific (this game's camera distance, this project's budget) | The project's `.game/` documents, locked by Human Decision | The project's human authority |
| Framework-general (a rule that would help any game) | A GPOS change proposal (§2) referencing the postmortem | GPOS review (§5) |
| Unclear | Recorded in the project first; revisited after a second occurrence | The project's human authority |

A lesson becomes framework-general only if it is not specific to one game's creative choices. One project's taste is never promoted into GPOS defaults.

## 5. Review requirements

| Change class | Required review |
|---|---|
| PATCH | One reviewer other than the author; validator passes |
| MINOR | Reviewer plus Human Decision by a GPOS maintainer; validator passes; CHANGELOG entry |
| MAJOR | Independent consistency review of the whole framework; Human Decision by a GPOS maintainer; migration notes; validator passes; CHANGELOG entry |
| Maturity promotion | §9 |

Changes touching human authority, the authority hierarchy, review policy, mandatory Human Review triggers or the evidence insufficiency rules are always treated as MAJOR, whatever their size. Self-approval of a framework change by the agent that wrote it is not review.

## 6. Semantic versioning

The version in the VERSION file and `core/registry.json` follows Semantic Versioning:

| Bump | When |
|---|---|
| PATCH | Typo, clarification, non-semantic validation improvement |
| MINOR | Compatible new workflow, skill, evidence type, condition, trigger or adapter capability |
| MAJOR | Authority model, lifecycle semantics, gate semantics, review-policy semantics, or any incompatible contract or schema change |

The person accepting a change is responsible for its classification. While GPOS is in `1.0.0-alpha.N`, incompatible changes increment the pre-release number and are listed under **Breaking** in `CHANGELOG.md`; schemas keep `schema_version` `1.0` until the first non-alpha release, after which any incompatible schema change bumps `schema_version`.

## 7. Backward compatibility and deprecation

- Compatible changes must not invalidate existing valid project records.
- A term or field is **deprecated** before removal: it is marked deprecated in the registry or schema description and in `CHANGELOG.md`, stays valid for at least one MINOR release, and its replacement is documented.
- Deprecated terms are not used in new GPOS documents.
- During alpha, deprecation may be skipped for incompatible changes, but they must be listed under **Breaking**.

## 8. Breaking changes

A breaking change requires:

1. a MAJOR classification (or alpha pre-release increment, §6);
2. migration notes: what project records and `.game/` documents must change and how;
3. validator updates proving the new rules and rejecting the old ones where they are now invalid;
4. a CHANGELOG **Breaking** section.

## 9. Skill maturity promotion

Promotion follows [SKILL-MATURITY.md](SKILL-MATURITY.md):

1. A promotion proposal names the skill, target level and evidence: real tasks, gate records, Human Review records and postmortems.
2. The proposal is reviewed like a MINOR change.
3. A GPOS maintainer's Human Decision accepts it.
4. The skill's front matter and `MATURITY` section are updated with the promotion history, and the promotion is listed in `CHANGELOG.md`.

A skill never promotes itself, and no project may promote a GPOS skill. Demotion follows the same path.

## 10. Releases and tagging

- A release is a commit where the VERSION file, `core/registry.json` `gpos_version`, all version references and `CHANGELOG.md` agree, and the validator passes.
- Releases are tagged `v<version>` (e.g. `v1.0.0-alpha.7`).
- Projects pin the GPOS version they use in their project config (`gpos_version`).
- Release automation (GitHub or otherwise) is out of scope for Phase 1.

## 11. Project migration

- A project upgrades GPOS deliberately, never implicitly. Upgrading across a MAJOR (or alpha) boundary is a Human Decision for that project (trigger `AUTHORITY_CHANGE`), recorded as a `GPOS_UPGRADE` decision whose `upgrade.from_version` / `upgrade.to_version` match the project's move. Project config records the move as `gpos_upgrade` (`from_version`, `decision_ref`); a project created directly on its pinned version has no `gpos_upgrade` and needs no upgrade decision. A stable PATCH or MINOR move within one MAJOR needs no decision.
- Migration notes list the record and document changes required. Until migrated, the project keeps working on its pinned version.
- Existing gate `PASS` results remain historical records; whether they still hold under new rules is decided during migration, gate by gate, not assumed.
- Migration tooling is a later phase ([tools/README.md](../tools/README.md)).

## 12. Phase-2 acceptance requirement: record validation

Phase 1 validates the **framework**: vocabulary, contracts, schemas and fixtures. It does **not** validate a project's actual records against each other. Cross-record and runtime project validation is not implemented in Phase 1.

Phase 2 is not accepted until tooling validates, where possible:

**Evidence and gates**

1. evidence type and capture context match the claimed gate (including type/context compatibility);
2. evidence `subject_revision` matches the gate's subject revision, or an explicit, justified carryover exists;
3. a linked Human Review record exists for every `HUMAN_REVIEW_REQUIRED` `PASS`;
4. the linked Human Review verdict is valid: human-assessed, `PASS`, same scope and revision, disagreements disclosed;
5. a cross-reviewer is not the gate owner, the owner assessment and cross-reviews are consistent with the policy, and `assessed_by` is the owner or a human;
6. superseded evidence cannot satisfy a current gate, and a superseded negative cross-review has a later review by the same reviewer;
7. project records conform to the GPOS registry and schemas of the pinned version.

**Referential integrity — a non-empty or well-formed string is not authority**

8. every decision reference resolves to a real, `ACTIVE` Human Decision record, of a kind allowed for that field (registry `decision_ref_fields`), for the correct subject and scope:
   - Golden Cell waiver and exit references,
   - `blocking_downgrade_ref`,
   - review-policy override references,
   - quality-target references,
   - lifecycle transition references,
   - editor-concurrency references,
   - presentation-parity references;
9. the lifecycle stage is backed by a `LIFECYCLE_TRANSITION` decision whose `transition.to` is the current stage, following an allowed transition (registry `lifecycle_transitions`), with a Golden Cell waiver where required;
10. presentation parity: a `YES`/`NO` value is backed by a `PRESENTATION_PARITY` decision with the same value; routing that declines `TARGET_PRESENTATION_DIFFERS` is consistent with it.

**Authorization**

11. every decision's `decided_by` is a listed decision authority allowed to make that kind of decision;
12. every `HUMAN_EVIDENCE` source is a listed Human Review participant for the gate, or a decision authority.

**Value binding, upgrades and record-set integrity (added in alpha.4)**

13. every decision's structured payload matches the configuration it authorizes (registry `decision_value_bindings`): review-policy overrides (gate, policy, scope), quality target, editor concurrency, presentation parity, lifecycle stage — a decision of the right kind with a different value is rejected;
14. a governed GPOS move (`gpos_upgrade`) is backed by a `GPOS_UPGRADE` decision whose `from_version` / `to_version` match the project's actual migration (checked against its version history);
15. identifiers are unique within a project record set (registry `record_id_uniqueness`: decision, evidence, gate and routing ids; decision-authority and reviewer ids). Duplicates are rejected before any lookup; the validator never builds a lookup before duplicate checks and never resolves by last-write-wins;
16. evidence carryover is approved by the gate owner or an authorized human; CI, tools and devices may propose carryover but never approve it;
17. Human Review verdicts respect reviewer gate permissions: a reviewer limited to some gates cannot give the verdict (or be the `HUMAN_EVIDENCE` source, or approve carryover) for other gates; a decision authority holding `ALL` may review any gate;
18. timestamps relied on for ordering (`recorded_at`, `created_at`, `decided_at`) are parsed as RFC 3339 date-times before use.

**Routing completeness, subject binding and full reference resolution (added in alpha.5)**

19. every required routing gate has exactly one matching gate record (`routing_ref`); a missing required gate is `NOT_RUN` and the routed scope is not ready;
20. routing and gate metadata agree: gate, owner, blocking, review policy, cross-review requirement, subject;
21. gate `applied_conditions` equal the routing entry's `applied_conditions`;
22. required and omitted gate sets are non-conflicting: no gate twice in either, none in both; a linked gate that routing does not list blocks readiness until routing is revised;
23. evidence subject applies to the gate subject (kind and ref), or an accountable `evidence_applicability` entry maps it;
24. every active cross-review that contributes to `PASS` reviewed the current `scope.revision`; a linked Human Review matches the gate scope's kind, ref and revision;
25. every decision reference in every record type (project config, gate, task routing) resolves, with kind, authorization, subject rules and value bindings — including `BLOCKING_DOWNGRADE` / `KNOWN_ISSUE_ACCEPTANCE` bound to the downgraded gate and its scope;
26. every routing record — however many a project has — is validated against project authority, including presentation parity;
27. project-specific mandatory triggers (`PROJECT:<id>`) resolve to triggers declared in project config and force Human Review like core triggers;
28. a `ROUTINE` `PASS` is the owning specialist's own `PASS` assessment.

**Routing authority closure (added in alpha.6)**

29. every workflow `always_required` gate (registry `workflow_gate_requirements`) is present in routing and never omitted;
30. routing obeys applicable project review-policy overrides (project-wide, or scoped by `kind`/`ref` to the routing subject); ambiguous or duplicate applicable overrides are rejected;
31. the effective review policy is computed with mandatory core and project triggers taking precedence over overrides, and overrides over the routing's own choice; routing may be stronger, never weaker;
32. every routing-required evidence type is actually satisfied by counting gate evidence before the linked gate closes and before routed readiness;
33. routing cannot require evidence not valid for the gate;
34. contributing cross-reviewers are among the routing's specialist reviewers (not `HUMAN`, not the owner);
35. every conditional-evidence condition defined for a routed gate is applied or declined — exactly once, declines with a reason;
36. presentation parity `YES` forces `TARGET_PRESENTATION_DIFFERS` to be applied wherever the gate defines it; declining it requires a decided `NO`;
37. Golden Cell routing accounts for every quality gate as required or explicitly omitted with a reason.

**Freeze closures (added in alpha.7)**

38. a contributing specialist cross-reviewer is eligible for the owning discipline under the registry cross-review model (`cross_review_eligibility`), in addition to being routed, not the owner, current and passing;
39. game-director never acts as a discipline-quality cross-reviewer;
40. Release accounts for every quality gate as required or explicitly omitted with a reason;
41. target-runtime evidence resolves to a declared project target platform;
42. where reference devices are declared for a platform, `DEVICE_EVIDENCE` used for that platform resolves to one of them;
43. Release `DEVICE` and `PERFORMANCE` coverage exists for every PRIMARY target platform;
44. evidence from one target platform cannot satisfy another target's release requirement.

JSON Schema cannot express uniqueness by property or cross-record equality; these are record-set rules. A reference implementation exists in `tests/validate_framework.py` (`gate_evidence_problems`, `counting_evidence_types`, `eligible_cross_reviewer`, `target_platform_problem`, `release_coverage_problems`, `effective_policy_floor`, `routing_problems`, `authority_problems`, `resolve_decision_refs`, `routing_gate_problems`, `routed_scope_ready`, `record_set_problems`, `index_unique`, `human_may_review`, `scope_ready`), exercised by `tests/fixtures/records/` and `tests/fixtures/authority/`. It is test code, not project tooling.
