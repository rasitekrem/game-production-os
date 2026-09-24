# Decisions

> **GPOS template** · `1.0.0-alpha.15` · copy to `.game/DECISIONS.md` · see [templates/README.md](README.md)
>
> This is the project's human-readable Human Decision log — authority level 1. Each entry mirrors, by id, a machine-readable decision record (`schemas/decision.schema.json`); see core/AUTHORITY-HIERARCHY.md §6 for which representation is authoritative. Only decisions made by a human are recorded here. Agents may write entries only to transcribe a human's explicit decision, quoting or linking the human's own words.

## Rules

- One entry per decision. Never edit a past entry's decision; supersede it with a new entry.
- Record the human's words, not an agent's interpretation.
- Silence, absence of objection or agent inference are not decisions.
- Every `LOCKED` item in `.game/` cites the entry that locked it.

## Open decisions

| Id | Question | Needed by (stage / task) | Blocking | Options prepared by |
|---|---|---|---|---|
| `UNDECIDED` | | | | |

## Log

<!-- Copy this block for each decision. -->

### D-0000 — `HUMAN_DECISION_REQUIRED`

| Field | Value |
|---|---|
| Date | `UNDECIDED` |
| Decided by (human) | `HUMAN_DECISION_REQUIRED` |
| Subject | `UNDECIDED` |
| Decision (human's words, quoted or linked) | `HUMAN_DECISION_REQUIRED` |
| Kind | lock · reopen · waiver · stage transition · gate downgrade · known-issue acceptance · maturity promotion · other |
| Affects documents | `UNDECIDED` |
| Evidence considered | `UNDECIDED` |
| Supersedes | none |
| Transcribed by | `UNDECIDED` |
