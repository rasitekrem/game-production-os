# Tools — placeholder (Phase 1)

**No tools are implemented in Phase 1.** The only executable in this repository is the framework validator in `tests/`.

## Planned concepts

| Tool | Purpose | Constraints |
|---|---|---|
| Adapter sync | Regenerate agent-specific instruction files (see `adapters/`) from GPOS source | Output is generated, never hand-edited; source of truth stays in `core/`, `skills/`, `workflows/` |
| Project bootstrap | Create a project's `.game/` directory from `templates/` and write a project config | Must leave every decision as a placeholder; must not invent game decisions |
| Validation CLI | Validate a project's `.game/` records (routing, gates, evidence) against `schemas/` and cross-record rules (evidence type, context and revision per gate; linked Human Review; reviewer ≠ owner; supersession) | Reports; never changes gate statuses on its own |
| Evidence collection | Capture and register evidence records with correct type, capture context, provenance (revision, build, hash, platform, tool version, instrumentation) and limitations | Cannot create `HUMAN_EVIDENCE`; provenance filled by the tool, not typed by the agent |
| Framework upgrade / migration | Move a project from one GPOS version to another, with a report of changed semantics | MAJOR upgrades require Human Decision per project |

**Phase-2 acceptance requirement.** Project record validation is not implemented in Phase 1. The requirements Phase 2 must meet are defined in [core/GOVERNANCE.md §12](../core/GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation).

The reference implementation of the cross-record rules currently lives in `tests/validate_framework.py` (`gate_evidence_problems`, `routing_problems`, `scope_ready`) and is expected to move into the validation CLI in a later phase.
