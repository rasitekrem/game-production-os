# Framework validation

```bash
python3 tests/validate_framework.py
```

Requires Python 3.8+ and **no third-party packages**.

Optional cross-check: with `jsonschema` and `rfc3339-validator` installed, every result is compared with the reference implementation, including date-time format checking. If `jsonschema` is installed without date-time support the run fails rather than silently skipping format checks.

## Why no dependency

JSON Schema validation normally uses the `jsonschema` package. GPOS schemas are kept to a small keyword subset so a ~150-line standard-library validator (`schema_lite.py`) can check them. The validator **refuses unknown keywords** rather than ignoring them, so a schema change cannot silently weaken validation. If `jsonschema` happens to be installed, every validation result is cross-checked against it and any disagreement fails the run.

## What is checked

| Group | Checks |
|---|---|
| T01–T05 | Skills, workflows, core documents, templates exist; every skill and workflow has every contract section in order; every skill is `DRAFT` |
| T06–T08 | Gate statuses, gates and evidence types identical in registry, schemas and documents; `NOT_RUN` never counts as `PASS`; registry gate rules coherent |
| T09–T14 | Schema behaviour: invalid evidence, primary specialist, mandatory Human Review, project config, Golden Cell required by default |
| T15–T20 | Contract content: two examples per skill, Level Design ≠ Environment Art, Art Direction ≠ Technical Art, QA cannot override subjective gates, animation and camera motion claims require motion evidence |
| H01–H09 | Final hardening: review policies and their defaults (registry, schemas, QUALITY-GATES table, every skill contract); mandatory triggers incl. Golden Cell exit; conditional evidence (gameplay design not motion-only, device evidence never universal); capture contexts and provenance; record fixtures; visual responsibility split; GOVERNANCE sections; Phase-2 boundary; human-evidence trust boundary |
| A01–A11 | Alpha.3 corrections: `game-engineering` contract and implementation/verification separation, `runtime-system` routing, evidence type/context compatibility (incl. DCC render masquerading as runtime evidence), Human Decision schema and decision-ref fields, authority fixtures, lifecycle transition authority, corrected review semantics, accountable assessor, authorization model, scope kinds, presentation parity, future considerations |
| B01–B09 | Alpha.4 hardening: decision value binding (opposite values rejected), GPOS upgrade authority, accountable carryover, Human-Review-only routing, record-id uniqueness, gate-aware reviewer authorization, RFC 3339 timestamps and unambiguous ids, expanded Phase-2 contract, maturity and boundary |
| C01–C14 | Alpha.5 binding and readiness: evidence subject binding and explicit applicability, decision references resolved in gate and routing records, blocking-downgrade binding, routing↔gate linkage, routing-aware readiness (missing gates), routing uniqueness/disjointness, revision-bound cross-reviews, multi-routing authority checks, project triggers, character-production trigger semantics, `ROUTINE` owner assessment, config-decision subject rules, Human Review scope kind |
| D01–D10 | Alpha.6 routing authority closure: effective review policy (overrides, scoped overrides, ambiguity, trigger precedence), workflow always-required gates, routing evidence contract (validity and fulfilment), routed cross-reviewers, complete condition accounting and presentation parity `YES`, Golden Cell 12-gate accounting, Phase-2 contract, maturity and boundary |
| E01–E06 | Alpha.7 freeze closures: machine-readable cross-review eligibility (game-director never counts), Release 12-gate accounting, target-platform and reference-device binding, Release PRIMARY-platform coverage, Phase-2 contract, maturity and boundary |
| P (helper) | Phase-2A boundary (`phase_boundary_problems`): code only in `tests/` and `gpos/`, `adapters/` only README, `tools/` documentation only, production code imports neither tests nor network modules. The Phase-1 boundary tests (B09, C14, D10, E06, X08) asserted the Phase-1 file layout; they now assert this boundary, so their protection is preserved |
| X00–X10 | Consistency: gate-evidence table vs registry, schema enums and per-gate owner/condition rules vs registry, backticked vocabulary in every Markdown file, internal links and anchors, versions, examples, schema fixtures, Phase-1 boundary, templates contain no decisions, cross-review sections vs the routing table |

## Production validator tests

```bash
python3 tests/test_production_validator.py
```

Tests for the Phase-2A production validator (`gpos/`): schema parity with `schema_lite.py` and `jsonschema`, RFC 3339 boundaries, record-set and readiness parity with the frozen reference model on every authority and record fixture, named adversarial regressions on the synthetic bundles, bundle loading, read-only behaviour, determinism, CLI exit codes, performance on thousands of records, and the GOVERNANCE §12 coverage matrix. See [tools/validator/README.md](../tools/validator/README.md).

`fixtures/bundles/` holds synthetic project bundles (generic data) generated by `generate_bundles.py`. `mutate_production.py` is a bounded mutation harness for `gpos/`.

## Adapter tests

```bash
python3 tests/test_adapters.py
```

Tests for the Phase-2B agent adapter layer (`gpos/adapters/`): the IR (all 13 skills, authority order, project authority rows, placeholders preserved, maturity never promoted), Claude Code and Codex rendering (layout, budgets, front matter, determinism, no global paths), semantic parity of the two manifests, drift detection, sync ownership and idempotency, path security (traversal, manifest injection, symlinks), the validator precondition, context budgets and monolith rejection, the CLI and layout snapshots (`fixtures/adapter-snapshots/`, updated deliberately with `GPOS_UPDATE_SNAPSHOTS=1`). `fixtures/adapter-project/` is the synthetic project; `mutate_adapters.py` is a bounded mutation harness for `gpos/adapters/`.

## Fixtures

**Schema fixtures** — `fixtures/valid/*.json` and `fixtures/invalid/*.json` are patches applied to a valid example:

```json
{ "description": "...", "schema": "gate", "base": "examples/example-gate-record.json",
  "set": { "/status": "PASS" }, "remove": ["/required_changes"],
  "expect": "invalid", "expect_keyword": "contains" }
```

The base must itself be valid, so an invalid fixture fails for the reason it describes.

**Authority fixtures** — `fixtures/authority/*.json` pair a project config with Human Decision records (and optionally routing or evidence) and state the expected referential-integrity, lifecycle, authorization or presentation-parity problem. They show that a well-formed but unresolved decision id is not authority.

**Record fixtures** — `fixtures/records/*.json` pair a gate record with evidence records (each schema-valid) and state the cross-record problem expected, or `null` for none: stale revisions, justified and mismatched carryover, superseded evidence, self cross-review, non-counting capture contexts, target-runtime conditions, instrumentation impact, conditional evidence (e.g. turn-based gameplay passing on runtime evidence). They exercise the reference implementation of rules Phase-2 tooling must enforce on real projects ([core/GOVERNANCE.md §12](../core/GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation)).

## What is not checked

Nothing here judges whether the framework is *good*. Tests confirm structure and internal consistency only. Subjective framework quality is a Human Review matter.
