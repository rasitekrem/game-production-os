# Human Review Record

> **GPOS template** · `1.0.0-alpha.12` · one record per review · stored where the project keeps review records (e.g. `.game/reviews/`) · see [core/HUMAN-AUTHORITY.md §4](../core/HUMAN-AUTHORITY.md#4-human-review-protocol)
>
> Part A is prepared by agents. Part B is the human's verdict — agents may only transcribe it verbatim.

## Part A — Review packet (prepared by agents)

| Field | Value |
|---|---|
| Review id | `UNDECIDED` |
| Scope (task / feature / cell / release) | `UNDECIDED` |
| Subject and exact version (build, revision, asset version) | `UNDECIDED` |
| Gates under review | `UNDECIDED` |
| Mandatory triggers that apply (e.g. `GOLDEN_CELL_EXIT`) | `UNDECIDED` |
| Capture context of primary evidence (e.g. `TARGET_RUNTIME`) and its limitations | `UNDECIDED` |
| Primary question (answerable yes / no) | `UNDECIDED` |
| Prepared by | `UNDECIDED` |

### Primary evidence — shown first, without explanation

| Evidence id | Type | Context / device | Artifact |
|---|---|---|---|
| — | `MOTION_EVIDENCE` | | |

### Specialist and cross-review assessments

| Gate | Owner assessment | Cross-reviewer assessment | Disagreements |
|---|---|---|---|
| — | | | |

### Known limitations and out-of-scope items

- `UNDECIDED`

### Options (if the human is asked to choose)

- `NOT_APPLICABLE` — reason:

Packet rules: no pre-judging language; no hidden known defects; evidence must be current and of the correct type (motion subjects need `MOTION_EVIDENCE`).

## Part B — Human verdict (human's words; transcribed verbatim)

| Field | Value |
|---|---|
| Reviewer (human) | `HUMAN_DECISION_REQUIRED` |
| Date | `UNDECIDED` |
| Answer to primary question | `HUMAN_DECISION_REQUIRED` |
| Recorded `HUMAN_REVIEW` status | `NOT_RUN` |
| Per-gate verdicts (if given) | `UNDECIDED` |
| Required changes (if `CHANGES_REQUIRED`) | `UNDECIDED` |
| Verbatim comments / link | `UNDECIDED` |
| Transcribed by | `UNDECIDED` |

Status mapping: accepts as asked → `PASS` · rejects direction → `FAIL` · accepts direction with changes → `CHANGES_REQUIRED` · declares review unnecessary → `NOT_APPLICABLE` with the human's reason · not yet reviewed → `NOT_RUN`. Ambiguous answers are never rounded up to `PASS`.

This verdict covers only the subject, version and question above. A material change to the subject invalidates it.
