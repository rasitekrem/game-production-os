# Quality Gates

Status: normative · GPOS `1.0.0-alpha.14` · Elaborates [P2](PRINCIPLES.md#p2--game-production-not-generic-software), [P3](PRINCIPLES.md#p3--independent-quality-gates)

Machine-readable source: [`registry.json`](registry.json) → `gates`, `review_policies`, `mandatory_human_review_triggers`, `evidence_conditions`. Record format: [`schemas/gate.schema.json`](../schemas/gate.schema.json).

---

## 1. Gates

| Gate | Default owner | Subjective | Default review policy | Question it answers |
|---|---|---|---|---|
| `TECHNICAL` | `qa-performance` | no | `ROUTINE` | Does it work correctly, deterministically, without regressions, and persist correctly where relevant? |
| `GAMEPLAY_DESIGN` | `gameplay-design` | yes | `CROSS_REVIEW_REQUIRED` | Does the player do something meaningful, and does the choice change something? |
| `LEVEL_DESIGN` | `level-design` | yes | `CROSS_REVIEW_REQUIRED` | Does the space support routes, traversal, encounters, sightlines and pacing as intended? |
| `ANIMATION` | `character-animation` | yes | `CROSS_REVIEW_REQUIRED` | Does the character move with believable weight, timing, contact and silhouette? |
| `CAMERA_COMPOSITION` | `camera-composition` | yes | `CROSS_REVIEW_REQUIRED` | Does the camera frame the action readably and move comfortably? |
| `VISUAL_ART` | `art-direction` | yes | `CROSS_REVIEW_REQUIRED` | Does it look like the intended production game and match approved visual authority? |
| `GAME_FEEL_VFX` | `game-feel-vfx` | yes | `CROSS_REVIEW_REQUIRED` | Does interaction feel responsive, readable and satisfying, truthful to game state? |
| `UI_UX` | `ui-ux` | yes | `CROSS_REVIEW_REQUIRED` | Can the player read, understand and operate the interface comfortably on the target? |
| `AUDIO` | `audio-design` | yes | `CROSS_REVIEW_REQUIRED` | Does sound support feedback, space and mood with a correct mix priority? |
| `PERFORMANCE` | `qa-performance` | no | `ROUTINE` | Does it meet the project's measured budgets in the stated context? |
| `DEVICE` | `qa-performance` | no | `ROUTINE` | Does it run and behave correctly on real target hardware? |
| `HUMAN_REVIEW` | human | yes | `HUMAN_REVIEW_REQUIRED` | Does a human accept the subject for its stated question? |

"Default owner" records the gate unless routing assigns another **permitted owner** (registry `permitted_owners`): `technical-art` may own `TECHNICAL` or `PERFORMANCE` for asset-pipeline scope; `environment-art` may own `VISUAL_ART` for environment scope ([ROLE-ROUTING.md §5](ROLE-ROUTING.md#5-default-visual-responsibility)). No other reassignment is valid. `game-director` and `game-engineering` never own a gate: the director routes, and the engineer implements while `qa-performance` verifies, so implementation and verification stay separate. `HUMAN_REVIEW` is always owned by a human.

## 2. Statuses

| Status | Meaning | Rules |
|---|---|---|
| `PASS` | Assessed against required evidence and accepted. | Requires current evidence of the required types for the subject revision, and satisfaction of the gate's review policy (§7). |
| `FAIL` | Assessed and rejected; the approach or direction is wrong. | Requires notes explaining why. Rework, not polish. |
| `CHANGES_REQUIRED` | Assessed; direction acceptable; specific changes needed. | Requires a concrete list of changes. |
| `NOT_APPLICABLE` | The gate is irrelevant to this scope. | Requires a written reason. Is not a pass. May be challenged by any reviewer. |
| `NOT_RUN` | Not yet assessed, or previous assessment invalidated. | Default status of every gate. **Never implies `PASS`.** |

Exactly these five statuses exist. No others ("partial", "pass with notes", "assumed pass", "skipped") are valid.

## 3. Independence rules

1. **No transitive passing.** A gate's status is set only by assessing that gate's own evidence. `TECHNICAL` `PASS` says nothing about `ANIMATION`; `PERFORMANCE` `PASS` says nothing about `GAME_FEEL_VFX`; a successful import says nothing about `VISUAL_ART`.
2. **Missing evidence is `NOT_RUN`,** never `PASS`. Absence of complaints is not evidence.
3. **Proportional review.** Who must look before `PASS` is set by the gate's review policy (§7), never by convenience.
4. **Non-subjective gates cannot overrule subjective gates.** A green test suite, a profiler capture or a clean build cannot set, override or waive `ANIMATION`, `CAMERA_COMPOSITION`, `VISUAL_ART`, `GAME_FEEL_VFX`, `UI_UX`, `AUDIO`, `GAMEPLAY_DESIGN` or `LEVEL_DESIGN`. The reverse also holds: visual approval does not waive `PERFORMANCE`.
5. **Gates are scoped and revisioned.** A gate record names its scope — `TASK`, `FEATURE`, `ASSET`, `SPACE`, `GOLDEN_CELL`, `BUILD`, `RELEASE`, `MILESTONE`, `PROJECT` or `DECISION` — and the exact subject revision it applies to. A `PASS` for one scope or revision does not transfer to another ([EVIDENCE-RULES.md §5](EVIDENCE-RULES.md#5-revisions-staleness-and-supersession)).

## 4. Relevance and blocking

The `game-director` selects relevant gates during routing ([ROLE-ROUTING.md](ROLE-ROUTING.md)).

- A gate is **relevant** if the change can plausibly affect what that gate judges. When in doubt, it is relevant.
- A relevant gate is **blocking** by default. Making a relevant gate non-blocking requires a Human Decision, referenced by decision id in the gate record (`blocking_downgrade_ref`).
- An irrelevant gate is recorded `NOT_APPLICABLE` with a reason, or omitted from routing with the reason in the routing record.
- A reviewer who believes an omitted or `NOT_APPLICABLE` gate is relevant raises it; the gate then returns to `NOT_RUN`.

**Merge / release readiness:** a scope is ready only when **every blocking relevant gate is `PASS`**. `NOT_APPLICABLE` (with reason) does not block. `NOT_RUN`, `CHANGES_REQUIRED` and `FAIL` block.

## 5. Evidence per gate

Every gate has **base** evidence requirements that always apply, and **conditional** requirements that apply when routing applies a named condition from the registry's `evidence_conditions`. Routing records the conditions it applied (`applied_conditions`) and, where it considered and rejected one, why (`unapplied_conditions`). Applying conditions is a routing responsibility; the conditions themselves are fixed vocabulary, not prose.

| Gate | Base (all of / any of) | Conditional (condition → adds) | Never sufficient alone |
|---|---|---|---|
| `TECHNICAL` | any of `TEST_EVIDENCE`, `RUNTIME_EVIDENCE` | `PERSISTENCE_AFFECTED` → `PERSISTENCE_EVIDENCE` | `CODE_EVIDENCE` |
| `GAMEPLAY_DESIGN` | any of `RUNTIME_EVIDENCE`, `MOTION_EVIDENCE` (from a playable runtime) | `REAL_TIME_BEHAVIOUR` → `MOTION_EVIDENCE` | `CODE_EVIDENCE`, `TEST_EVIDENCE` |
| `LEVEL_DESIGN` | `VISUAL_EVIDENCE`; any of `RUNTIME_EVIDENCE`, `MOTION_EVIDENCE` | `REAL_TIME_BEHAVIOUR` → `MOTION_EVIDENCE` | `CODE_EVIDENCE`, `TEST_EVIDENCE` |
| `ANIMATION` | `MOTION_EVIDENCE` | `TARGET_PRESENTATION_DIFFERS` → captured in `TARGET_RUNTIME` | `VISUAL_EVIDENCE`, `RUNTIME_EVIDENCE`, `TEST_EVIDENCE`, `CODE_EVIDENCE` |
| `CAMERA_COMPOSITION` | `VISUAL_EVIDENCE` | `CAMERA_MOTION_CLAIM` → `MOTION_EVIDENCE`; `TARGET_PRESENTATION_DIFFERS` → captured in `TARGET_RUNTIME` | `RUNTIME_EVIDENCE`, `CODE_EVIDENCE`, `TEST_EVIDENCE` |
| `VISUAL_ART` | `VISUAL_EVIDENCE` | `ANIMATED_PRESENTATION` → `MOTION_EVIDENCE`; `TARGET_PRESENTATION_DIFFERS` → captured in `TARGET_RUNTIME` | `CODE_EVIDENCE`, `TEST_EVIDENCE`, `RUNTIME_EVIDENCE` |
| `GAME_FEEL_VFX` | `MOTION_EVIDENCE` | `FEEDBACK_INCLUDES_SOUND` → `AUDIO_EVIDENCE`; `TARGET_PRESENTATION_DIFFERS` → captured in `TARGET_RUNTIME` | `VISUAL_EVIDENCE`, `CODE_EVIDENCE`, `TEST_EVIDENCE`, `RUNTIME_EVIDENCE` |
| `UI_UX` | `VISUAL_EVIDENCE` | `TOUCH_OR_MOBILE_TARGET` → `DEVICE_EVIDENCE`; `ANIMATED_PRESENTATION` → `MOTION_EVIDENCE`; `TARGET_PRESENTATION_DIFFERS` → captured in `TARGET_RUNTIME` | `CODE_EVIDENCE`, `TEST_EVIDENCE`, `RUNTIME_EVIDENCE` |
| `AUDIO` | `AUDIO_EVIDENCE` | `TARGET_OUTPUT_MATTERS` → `DEVICE_EVIDENCE` | `CODE_EVIDENCE`, `TEST_EVIDENCE`, `RUNTIME_EVIDENCE` |
| `PERFORMANCE` | `PERFORMANCE_EVIDENCE` | `TARGET_PLATFORM_PERFORMANCE_CLAIM` → `DEVICE_EVIDENCE`, captured in `TARGET_RUNTIME` or `PERFORMANCE_RUNTIME` | `RUNTIME_EVIDENCE`, `CODE_EVIDENCE` |
| `DEVICE` | `DEVICE_EVIDENCE` | — | `RUNTIME_EVIDENCE`, `VISUAL_EVIDENCE` |
| `HUMAN_REVIEW` | `HUMAN_EVIDENCE` | — | — (only human evidence is acceptable) |

Rules:

- **Base and applied conditional** evidence must be present, current (§6) and non-superseded for `PASS`.
- **Never sufficient alone** — may be attached as support, but a `PASS` resting only on these types is invalid.
- **Capture context counts.** Evidence captured in a non-counting context for that gate (registry `non_counting_contexts`) does not count toward `PASS`. For gameplay and presentation gates, `DCC_RENDER` and `OFFLINE_ANALYSIS` do not count; the one exception is `VISUAL_ART` on `ASSET` scope, where a `DCC_RENDER` may support asset inspection. `DEVICE` also excludes `EDITOR`. Details: [EVIDENCE-RULES.md §3](EVIDENCE-RULES.md#3-capture-contexts).
- **Mobile is conditional.** `DEVICE_EVIDENCE` is required only where a condition applies it. GPOS does not make device or mobile evidence universal for games whose targets do not need it.

## 6. Invalidation

A recorded status returns to `NOT_RUN` when:

- the subject changes after its evidence was captured, unless the change is shown not to affect the claim (explicit `evidence_carryover` with justification),
- any referenced evidence is superseded or found invalid,
- a higher authority it depended on is reopened,
- a reviewer shows the evidence did not cover the claim (wrong context, wrong device, wrong revision).

Agents must re-run affected gates instead of carrying stale results forward.

## 7. Review policy

Review is **proportional**. Every routed gate and every gate record declares one review policy:

| Policy | Required before `PASS` |
|---|---|
| `ROUTINE` | The owner closes its own gate with valid evidence, **only** when routing or the workflow explicitly allows it and names the approved authority the work stays inside (`routine_basis`). The owning specialist is the assessor (`assessed_by`) and its assessment is `PASS`; a human verdict does not replace the owner assessment — when a human must accept, the policy is `HUMAN_REVIEW_REQUIRED`. |
| `CROSS_REVIEW_REQUIRED` | The owner's assessment (`specialist_assessment`) is `PASS`; at least one independent specialist cross-review is `PASS` — by a reviewer other than the owner who is eligible for the owning skill (registry `cross_review_eligibility`; `game-director` never counts); **no unresolved negative cross-review** — a `FAIL` or `CHANGES_REQUIRED` review blocks `PASS` until the same reviewer supersedes it with a later review. Every cross-review records the revision it reviewed (`reviewed_revision`); only active reviews of the current `scope.revision` count, so an old review can never close a new revision. Disagreement that cannot be resolved escalates to a human, which changes the policy to `HUMAN_REVIEW_REQUIRED`. |
| `HUMAN_REVIEW_REQUIRED` | `HUMAN` is a reviewer in routing; a valid linked `HUMAN_REVIEW` record with status `PASS` for this scope and revision is mandatory. Specialist cross-review is required only when routing or the workflow says so (`cross_review_required`). Every unresolved negative cross-review and any non-`PASS` owner assessment must be exposed in the human review packet (`disagreements_disclosed`); the human may resolve the disagreement, but it may never be silently discarded. |

Defaults are in §1: subjective gates default to `CROSS_REVIEW_REQUIRED`; technical gates default to `ROUTINE`. Routing may always raise a policy. Routing may lower a subjective gate to `ROUTINE` only for bounded work inside approved authority, stating the basis. Project authority may change defaults per gate by Human Decision (`human_review.review_policy_overrides`), most often to require Human Review more often.

### Effective review policy

For every routed gate the **effective policy** is the minimum policy routing may use (registry `effective_review_policy_precedence`, strength `ROUTINE` < `CROSS_REVIEW_REQUIRED` < `HUMAN_REVIEW_REQUIRED`):

1. **Mandatory trigger** — if a core or project Human Review trigger applies, every subjective gate is `HUMAN_REVIEW_REQUIRED`. Nothing relaxes this.
2. **Project review-policy override** — otherwise, the single applicable override in project config (`human_review.review_policy_overrides`, Project Locked Authority backed by a `REVIEW_POLICY_OVERRIDE` decision). An override without `scope` is project-wide for its gate; an override with `scope` (`kind`, `ref`) applies only when the routing subject matches it. Two overrides for the same gate and scope, or a project-wide and a scoped override that both apply to one routing, are ambiguous and rejected.
3. **Routing** — otherwise, the routing's own choice under the default rules above.

Routing may always choose a stronger policy than the effective policy, never a weaker one. A routing that silently weakens an applicable override is invalid and its scope is not ready.

### Accountable assessor

A gate status has one **accountable assessor** (`assessed_by`): the gate's owner — the owning specialist agent, whose id must equal `owner` — or a human. CI systems, tools and devices produce evidence; they never assess a gate. A cross-reviewer is never the owner. Who wrote the record (for example an agent transcribing a human verdict) is recorded separately (`recorded_by`).

### Mandatory Human Review triggers

Whatever the defaults, routing or project overrides say, the following always require Human Review. When any applies, the routing record lists it in `review_triggers`, `human_review.required` is true, and every subjective gate in scope is `HUMAN_REVIEW_REQUIRED`:

| Trigger | Applies to |
|---|---|
| `GOLDEN_CELL_EXIT` | Exit of the Golden Gameplay Cell |
| `CANONICAL_CREATIVE_ASSET` | Creating or changing a canonical creative asset (character design, key art, an approved reference) |
| `MAJOR_BASELINE` | Establishing or changing a major player, camera, animation or game-feel baseline |
| `ART_DIRECTION_CHANGE` | Changing visual identity or art-bible rules |
| `MILESTONE_ACCEPTANCE` | Accepting a major milestone or lifecycle stage transition |
| `RELEASE` | Releasing a build to players |
| `AUTHORITY_CHANGE` | Locking, reopening or changing project authority, waivers or exceptions |

Projects may add triggers and may require Human Review more often. A project trigger is declared in project config (`human_review.additional_mandatory_triggers`, each with a unique `id`) and referenced in routing as `PROJECT:<id>`; it forces Human Review exactly like a core trigger. Routing may only reference project triggers that exist; project triggers add requirements and never relax the core seven. No project, routing record or agent may relax these seven. The workflows `new-game`, `golden-gameplay-cell` and `release` always carry their trigger (registry `workflow_mandatory_triggers`).

A human is not asked to approve every trivial subjective adjustment. A human is always asked when direction, baselines, canon, authority or release are at stake.

## 8. Known issues

A known issue on a blocking gate does not make the gate pass. When a human accepts proceeding with a known issue, that Human Decision is recorded as a blocking downgrade (`blocking: false` with `blocking_downgrade_ref`). The gate keeps its real status (`FAIL`, `CHANGES_REQUIRED`), so the issue stays visible.
