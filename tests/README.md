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
| P (helper) | Phase-2A boundary (`phase_boundary_problems`): code only in `tests/` and `gpos/`, `adapters/` only README, `tools/` documentation only, production code imports neither tests nor network modules, and only `gpos/tools/process.py` may start a process (Phase 2C-0). The Phase-1 boundary tests (B09, C14, D10, E06, X08) asserted the Phase-1 file layout; they now assert this boundary, so their protection is preserved |
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

## Tool adapter foundation tests

```bash
python3 tests/test_tool_foundation.py
```

Tests for the Phase-2C-0 tool adapter foundation (`gpos/tools/`): adapter identity, registration validation and deterministic listing; the capability model (read-only vs mutating, stateless vs stateful, contradictory declarations); probe states (`AVAILABLE`, `UNAVAILABLE`, `VERSION_UNSUPPORTED`) and lifecycle; execution (success, failure, dry run, mutation consent, unavailable and incompatible tools, adapter defects); process safety (no shell, executable and argv separated, unsafe working directories, path traversal, symlink escape, timeout and process-tree termination, output truncation, secret redaction, environment policy); single-writer leases; artifacts, streamed hashing and derivation; provenance (observed values recorded, unknown values absent and named); evidence candidates (registry compatibility, `HUMAN_EVIDENCE` refused, dry-run limits, derivation never upgrading a source, no gate vocabulary at all); proportional project preconditions; determinism; and the phase boundary. Group N covers the integrity hardening from code review: materialization provenance, project-less scopes, provenance and timing, request vocabulary, adapter-metadata redaction against a deliberately hostile adapter, runtime artifact claims, lease release and resource identity, the portability refinements, and the dry-run filesystem contract (a dry run creates no workspace and no parent of one; creation failures are structured results). `mutate_tools.py` is a bounded mutation harness for `gpos/tools/`.

No real production tool is required or invoked: everything runs against the `TEST_ONLY` synthetic reference adapter.

## Git adapter tests

```bash
python3 tests/test_git_adapter.py
```

Real integration tests for the Phase-2C-1 Git provenance adapter (`gpos/tools/git/`): every repository is created with the installed Git in a temporary directory and inspected through the audited process boundary. The suite fails if Git is absent; it never falls back to mocks. It covers registration and the real probe; missing or unusable tools; clean, dirty, conflicted, detached and unborn repositories; non-repository and nested projects; spaces, Unicode, newlines and renames in paths; truncated output and credential-shaped names parsed from the raw capture; submodules (dirty, hidden by `ignore = all`, untracked-only, moved HEAD, clean); fsmonitor hooks and the daemon proven not to start; the environment and real optional-lock behaviour; read-only behaviour checked by hashing every file under `.git`; resolve-provenance and the explicit `build_revision` handoff; the network and argument surface; the CLI; determinism; and linked worktrees. `mutate_git_adapter.py` is its bounded mutation harness.

## Media adapter tests

```bash
python3 tests/test_media_adapters.py
```

Real integration tests for the Phase-2C-2 media adapters (`gpos/tools/ffprobe/`, `gpos/tools/ffmpeg/`). Fixture media (a test pattern and a sine tone with credential-shaped metadata) are generated with the installed FFmpeg, and the adapters drive the same ffprobe and FFmpeg through the audited process boundary. The suite fails with the marker FFMPEG_RUNTIME_UNAVAILABLE_FOR_PHASE2C2 if either executable is absent; it never falls back to mocks. Groups A–Z cover:

- registration, real probes, and missing or unusable tools;
- inspection and the strict raw-JSON parser;
- non-media input;
- a real network block: a local HTTP server receives zero requests, with a sensitivity control that proves the fixture would reach it;
- frame, clip and audio extraction, checked with ffprobe from test code;
- numeric validation;
- missing, incompatible, inherited and DCC capture contexts;
- mutation consent and dry run;
- source immutability, output boundaries and no-overwrite;
- partial output and timeout;
- secrecy of credential-shaped names and metadata;
- the explicit Git handoff and evidence materialization;
- the exact command surface and the CLI.

`mutate_media_adapters.py` is its bounded mutation harness.

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
