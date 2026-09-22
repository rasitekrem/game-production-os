# Tools

**Phase 2A implements one tool: the production validator.** Its code is the Python package [`gpos/`](../gpos/__init__.py); its documentation is [validator/README.md](validator/README.md). This directory holds documentation only.

```bash
python3 -m gpos.validator validate  --project PATH [--format text|json]
python3 -m gpos.validator readiness --project PATH --routing ID [--format text|json]
```

The validator checks a project's records (project config, Human Decisions, routing, gates, evidence) against `schemas/`, `core/registry.json` and every record-validation requirement of [core/GOVERNANCE.md §12](../core/GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation). It is read-only: it reports and never changes gate statuses, records or decisions on its own. It does not judge creative quality; that stays with Human Review.

## Planned concepts (not implemented)

| Tool | Purpose | Constraints |
|---|---|---|
| Adapter sync | Regenerate agent-specific instruction files (see `adapters/`) from GPOS source | Output is generated, never hand-edited; source of truth stays in `core/`, `skills/`, `workflows/` |
| Project bootstrap | Create a project's `.game/` directory from `templates/` and write a project config | Must leave every decision as a placeholder; must not invent game decisions |
| Evidence collection | Capture and register evidence records with correct type, capture context, provenance (revision, build, hash, platform, tool version, instrumentation) and limitations | Cannot create `HUMAN_EVIDENCE`; provenance filled by the tool, not typed by the agent |
| Framework upgrade / migration | Move a project from one GPOS version to another, with a report of changed semantics | MAJOR upgrades require Human Decision per project |

The Phase-1 reference implementation of the cross-record rules remains in `tests/validate_framework.py` as the regression oracle for the production validator; production code does not import it.
