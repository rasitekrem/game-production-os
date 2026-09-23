# Definition of Done

Status: normative · GPOS `1.0.0-alpha.13`

"Done" is defined per scope. A scope is done only when every condition for its level holds. Nothing here can be satisfied by a status of `NOT_RUN`.

---

## 1. Task done

1. A routing record exists (primary specialist, gates, evidence, Human Review requirement, rationale).
2. Every **blocking relevant gate is `PASS`** on the task scope — judged against the routing record: each blocking required gate has exactly one linked gate record (`routing_ref`) with status `PASS`; a missing record is `NOT_RUN` ([ROLE-ROUTING.md § Routing integrity and gate linkage](ROLE-ROUTING.md#routing-integrity-and-gate-linkage)).
3. Every gate recorded `NOT_APPLICABLE` has a reason.
4. Every `PASS` references current, non-superseded evidence for the gate's subject revision, of an acceptable type and counting capture context, including base and applied conditional types ([QUALITY-GATES.md §5](QUALITY-GATES.md#5-evidence-per-gate)).
5. Every gate satisfied its review policy: `ROUTINE` with a stated basis, `CROSS_REVIEW_REQUIRED` with a `PASS` owner assessment, a passing independent cross-review and no unresolved negative review, `HUMAN_REVIEW_REQUIRED` with a linked `HUMAN_REVIEW` `PASS` and disclosed disagreements. Every mandatory trigger that applied was human-reviewed.
6. No higher authority was overridden silently; any deviation has a recorded Human Decision.
7. Visual work has been through the visual feedback loop with a fresh capture of the final state ([P8](PRINCIPLES.md#p8--visual-feedback-loop)).
8. New decisions are logged in `.game/DECISIONS.md`; `.game/CURRENT.md` is updated.
9. Known limitations are written down, not implied away.

## 2. Feature done

All of task done for each constituent task, plus:

1. The feature is integrated in a playable build and assessed in that build, not only in isolation.
2. Cross-discipline effects were checked (a feature adding VFX checked for `PERFORMANCE`; a camera change checked for `UI_UX` occlusion, etc.).
3. The feature is consistent with approved references and the Golden Cell benchmark (from `PRODUCTION` onward).
4. Postmortem completed if the feature was meaningful.

## 3. Golden Cell done

See [GOLDEN-GAMEPLAY-CELL.md §5](GOLDEN-GAMEPLAY-CELL.md#5-exit-conditions).

## 4. Release done

See [`workflows/release.md`](../workflows/release.md) exit conditions. In addition: build provenance recorded, `DEVICE` and `PERFORMANCE` `PASS` on every primary target platform, known issues accepted by Human Decision.

## 5. Not done — regardless of anything else

- "It compiles", "tests pass", "no errors in the console", "the asset imported", "the button works" — as the only evidence.
- A subjective gate `PASS` without the required evidence type.
- A Human Review that an agent inferred, summarized or wrote.
- A `PASS` based on evidence from before the last material change.
- Work that required a human to finish technical engine or DCC steps because the agent stopped early, without the blocker being reported.
