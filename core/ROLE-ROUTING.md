# Role Routing

Status: normative · GPOS `1.0.0-alpha.17` · Elaborates [P6](PRINCIPLES.md#p6--specialist-ownership), [P10](PRINCIPLES.md#p10--single-writer-per-stateful-editor)

Record format: [`schemas/task-routing.schema.json`](../schemas/task-routing.schema.json).

---

## 1. Specialists

| # | Skill | Domain in one line | Default gates owned |
|---|---|---|---|
| 01 | `game-director` | Routing, decomposition, gate selection, Human Review timing | — (routes; owns no discipline gate) |
| 02 | `gameplay-design` | What the player does, why, and what the choice changes | `GAMEPLAY_DESIGN` |
| 03 | `level-design` | Space, routes, traversal, encounter layout, pacing | `LEVEL_DESIGN` |
| 04 | `character-animation` | How characters move, turn, stop, interact and settle | `ANIMATION` |
| 05 | `camera-composition` | Framing, follow, readability, motion comfort | `CAMERA_COMPOSITION` |
| 06 | `art-direction` | Visual identity, shape/palette/material language, consistency | `VISUAL_ART` |
| 07 | `environment-art` | Visual massing, surfaces, set dressing, environment storytelling | `VISUAL_ART` (environment scope, when routed) |
| 08 | `technical-art` | Asset pipeline, rigs, shaders, LOD, export/import, visual performance | `TECHNICAL`, `PERFORMANCE` for asset-pipeline scope, when routed (default owner is `qa-performance`) |
| 09 | `game-feel-vfx` | Anticipation, impact, response, feedback timing, VFX | `GAME_FEEL_VFX` |
| 10 | `ui-ux` | Hierarchy, layout, touch usability, comprehension | `UI_UX` |
| 11 | `audio-design` | SFX, ambience, interaction audio, mix | `AUDIO` |
| 12 | `qa-performance` | Tests, regressions, determinism, saves, builds, devices, profiling | `TECHNICAL`, `PERFORMANCE`, `DEVICE` |
| 13 | `game-engineering` | Gameplay and system code, runtime architecture, persistence implementation, engine integration | — (implements; `qa-performance` verifies) |

`HUMAN_REVIEW` is owned by the human, never by a skill.

## 2. Routing procedure

The `game-director` produces a routing record for every non-trivial task before implementation starts:

1. **Classify** the task and select a workflow from `workflows/`.
2. **Read authority** — relevant `.game/` documents; list any `UNDECIDED` / `HUMAN_DECISION_REQUIRED` items the task depends on.
3. **Pick one primary specialist** — the one whose domain the task's success most depends on. Exactly one.
4. **Add secondary specialists** — others who must implement part of it.
5. **Add reviewers** — cross-reviewers for gates whose policy requires them (§4), plus `HUMAN` where Human Review is required.
6. **Select gates** — every relevant gate, blocking by default ([QUALITY-GATES.md §4](QUALITY-GATES.md#4-relevance-and-blocking)); state the reason for any gate omitted.
7. **Set required evidence** per gate: base requirements plus the registry conditions that apply (`applied_conditions`); record rejected conditions with a reason (`unapplied_conditions`).
8. **Set review policy** per gate (`ROUTINE`, `CROSS_REVIEW_REQUIRED`, `HUMAN_REVIEW_REQUIRED`); list every mandatory Human Review trigger that applies (`review_triggers`) ([QUALITY-GATES.md §7](QUALITY-GATES.md#7-review-policy)).
9. **Name the primary visual owner** when a scope mixes visual disciplines (§5).
10. **Set Human Review timing** — at which step, on what evidence, with what primary question.
11. **Record rationale** and out-of-scope items.

Routing is a proposal (agent recommendation). The human may change it at any time.

## 3. Routing table

Starting points, not exhaustive rules. Project authority may add patterns.

| Task pattern | Primary | Secondary (typical) | Relevant gates (typical) |
|---|---|---|---|
| New mechanic or rule change | `gameplay-design` | `game-engineering`, `qa-performance`, `game-feel-vfx`, `ui-ux` | `GAMEPLAY_DESIGN`, `TECHNICAL`, `GAME_FEEL_VFX`, `UI_UX`, `HUMAN_REVIEW` |
| Player locomotion looks robotic | `character-animation` | `technical-art`, `camera-composition` | `ANIMATION`, `TECHNICAL`, `CAMERA_COMPOSITION`, `HUMAN_REVIEW` |
| Camera feels distant / action unreadable | `camera-composition` | `level-design`, `ui-ux` | `CAMERA_COMPOSITION`, `GAME_FEEL_VFX`, `DEVICE`, `HUMAN_REVIEW` |
| New character model to in-game | `technical-art` | `art-direction`, `character-animation` | `VISUAL_ART`, `ANIMATION`, `TECHNICAL`, `PERFORMANCE`, `HUMAN_REVIEW` |
| Character visual design | `art-direction` | `technical-art` | `VISUAL_ART`, `HUMAN_REVIEW` |
| New level layout / encounter space | `level-design` | `gameplay-design`, `camera-composition` | `LEVEL_DESIGN`, `GAMEPLAY_DESIGN`, `CAMERA_COMPOSITION`, `HUMAN_REVIEW` |
| Dress a validated greybox | `environment-art` | `technical-art`, `level-design` (reviewer) | `VISUAL_ART`, `LEVEL_DESIGN`, `PERFORMANCE`, `HUMAN_REVIEW` |
| Hit / pickup / success feedback weak | `game-feel-vfx` | `audio-design`, `camera-composition` | `GAME_FEEL_VFX`, `AUDIO`, `PERFORMANCE`, `HUMAN_REVIEW` |
| HUD / menu / screen layout | `ui-ux` | `art-direction`, `qa-performance` | `UI_UX`, `VISUAL_ART`, `DEVICE`, `TECHNICAL`, `HUMAN_REVIEW` |
| Interaction sounds / ambience | `audio-design` | `game-feel-vfx` | `AUDIO`, `GAME_FEEL_VFX`, `HUMAN_REVIEW` |
| Save corruption / determinism bug | `qa-performance` | `game-engineering` | `TECHNICAL` (+ `PERSISTENCE_EVIDENCE`) |
| Gameplay system implementation | `game-engineering` | `qa-performance`, `gameplay-design` (reviewer) | `TECHNICAL`, `GAMEPLAY_DESIGN`, presentation gates as affected |
| Save / runtime architecture | `game-engineering` | `qa-performance` | `TECHNICAL` (+ `PERSISTENCE_EVIDENCE`), `HUMAN_REVIEW` when locking architecture (`AUTHORITY_CHANGE`) |
| Runtime refactor | `game-engineering` | `qa-performance`, behaviour owners as reviewers | `TECHNICAL`, and every gate whose behaviour could change (before/after evidence) |
| Engine or platform integration | `game-engineering` | `qa-performance`, `technical-art` (asset/render boundary) | `TECHNICAL`, `PERFORMANCE`, `DEVICE` |
| Frame drops on target device | `qa-performance` | `technical-art` | `PERFORMANCE`, `DEVICE`, and `VISUAL_ART` if fixes reduce visual quality |
| Shader / material implementation | `technical-art` | `art-direction` (reviewer) | `VISUAL_ART`, `TECHNICAL`, `PERFORMANCE` |
| Scope / planning / "what next" | `game-director` | as decomposed | as decomposed |

Rule: when a fix in one discipline may degrade another (a performance fix that lowers visual quality, a feedback change that alters timing), the affected discipline's gate is relevant.

### Routing integrity and gate linkage

- A routing record names its **subject** (`subject.kind`, `subject.ref`). Each gate appears **at most once** in `required_gates`, at most once in `omitted_gates`, and never in both.
- Every gate record produced for a routed task carries **`routing_ref`** (the routing `task_id`) and must agree with its routing entry: gate, owner, blocking, review policy, cross-review requirement, applied conditions, and scope kind/ref equal to the routing subject. The subject revision is recorded when the gate is assessed; routing does not need to know it.
- **Routing-aware readiness:** a routed scope is ready only when every blocking required gate has exactly one linked gate record with status `PASS`. A missing record counts as `NOT_RUN`. `NOT_APPLICABLE` does not satisfy a gate routing still requires — routing must first be revised to omit it with a reason. A linked gate that routing does not list (raised after routing) blocks readiness until routing is revised to include it.
- A gate-only readiness check over existing records is never proof of a routed task's readiness, because it cannot see missing gates.

### Routing contract (alpha.6)

- **Workflow invariants.** Each workflow's always required gates (registry `workflow_gate_requirements`, mirrored in each workflow's "Always required" line) must be in `required_gates` and can never be omitted. "When affected" gates are added by routing judgement. The `golden-gameplay-cell` workflow must also account for every quality gate: required, or omitted with a reason.
- **Required evidence is authoritative for the task.** A routed gate's `required_evidence` must cover the registry minimum (base and applied conditional evidence), may add further types, and may contain only types valid for that gate (`acceptable_evidence` or `insufficient_alone`). The linked gate cannot close — and the routed scope is not ready — until every routed evidence type is present among its counting evidence.
- **Contributing cross-reviewers are routed.** A current, passing cross-review counts toward closing a linked gate only if its reviewer is a routed reviewer (in `reviewers`, not `HUMAN`, not the owner). Not every routed reviewer must review every gate. Historical or superseded reviews may stay in the record.
- **Conditions are accounted for exactly once.** Every conditional-evidence condition the registry defines for a routed gate appears exactly once — in `applied_conditions`, or in `unapplied_conditions` with a reason; never both, never twice, never silently absent. Whether a condition applies stays a routing judgement, but the judgement is always recorded. `TARGET_PRESENTATION_DIFFERS` must be applied when project presentation parity is a decided `YES`, may be declined only when it is a decided `NO`, and is applied conservatively while parity is `UNDECIDED`.
- **Readiness is project-aware.** A routed scope is ready only when its whole record set is valid under project authority — effective review policy, overrides, triggers, presentation parity, decision references, routed evidence and reviewers — and every blocking required gate is `PASS`.
- **Effective review policy.** The routed policy is at least the effective policy ([QUALITY-GATES.md §7](QUALITY-GATES.md#7-review-policy)): mandatory trigger, then applicable project override, then routing.

## 4. Cross-review

Under `CROSS_REVIEW_REQUIRED`, the owner's output is reviewed by another specialist from this table before `PASS`; a negative review blocks until the same reviewer supersedes it. Under `HUMAN_REVIEW_REQUIRED`, cross-review happens when routing or the workflow requires it (`cross_review_required`), and any disagreement is shown to the human. Under `ROUTINE` a cross-review is optional. Cross-review is read-only, and a reviewer never reviews its own gate ([QUALITY-GATES.md §7](QUALITY-GATES.md#7-review-policy)).

| Output of | Cross-reviewed by (default) |
|---|---|
| `gameplay-design` | `level-design` or `game-feel-vfx` |
| `level-design` | `gameplay-design`, `camera-composition` |
| `character-animation` | `camera-composition`, `game-feel-vfx` |
| `camera-composition` | `level-design`, `game-feel-vfx` |
| `art-direction` | `environment-art` or `technical-art` (feasibility only) |
| `environment-art` | `art-direction` (visual), `level-design` (traversal/readability unchanged), `camera-composition` (focal and actor readability under lighting) |
| `technical-art` | `art-direction` (visual fidelity), `qa-performance` (budgets) |
| `game-feel-vfx` | `gameplay-design` (truthfulness), `camera-composition` (readability, comfort), `audio-design` (audio-visual sync, when sound is involved), `ui-ux` (when feedback includes UI elements) |
| `ui-ux` | `art-direction` (visual language), `gameplay-design` (information correctness) |
| `audio-design` | `game-feel-vfx` |
| `qa-performance` | `game-engineering` or `technical-art` — the implementing specialist (test intent) |
| `game-engineering` | `qa-performance` (correctness, testability), `gameplay-design` (rules implemented as specified) |

**Eligibility is machine-readable.** This table is mirrored in the registry (`cross_review_eligibility`, kept identical by the validator). A passing cross-review contributes to closing a gate only when its reviewer is routed, is not the owner, reviewed the current revision, and is listed for the gate's owning skill. One eligible reviewer is enough unless a workflow requires more. `game-director` never reviews discipline quality and never counts as a cross-reviewer (`never_cross_reviewer`). If a project genuinely needs a reviewer outside this table, the gate is escalated to `HUMAN_REVIEW_REQUIRED`; agents never invent reviewer exceptions.

Cross-review produces an assessment (`PASS` / `CHANGES_REQUIRED` / `FAIL` + notes) attached to the gate record. It never replaces a required Human Review. Unresolved disagreement on a blocking gate escalates to a human.

## 5. Default visual responsibility

Default split for lighting and mixed visual scopes. Locked project authority may override it.

| Specialist | Owns by default |
|---|---|
| `art-direction` | Lighting intent, mood, color and value hierarchy, the visual target |
| `environment-art` | Environment lighting composition, scene-light placement where applicable, environmental readability, visual massing |
| `technical-art` | Lighting and shader implementation, renderer and tool constraints, optimization |
| `camera-composition` | Cross-reviews focal and actor readability under the lighting |

For a scope that mixes character, environment and other visual disciplines, the `game-director` selects **one primary visual owner** — the owner of the `VISUAL_ART` gate for that scope (routing requires it). `art-direction` cross-reviews visual-language consistency where it is not itself the owner.

## 6. Handoff contract

Every handoff between specialists states:

- **What** is handed off (subject, version, location).
- **State** of relevant gates and evidence references.
- **Constraints** the receiver must respect (locked authority, validated metrics, budgets).
- **Open questions** and anything `HUMAN_DECISION_REQUIRED`.
- **What the receiver must not change** without handing back.

## 7. Concurrency

Single-writer rule for stateful editors ([P10](PRINCIPLES.md#p10--single-writer-per-stateful-editor)):

- At most **one mutating agent** per stateful editor instance (engine project, DCC scene) at a time.
- The `game-director` assigns the write lock as part of routing; handoff transfers it explicitly.
- Any number of **read-only** reviewers may inspect, capture and analyze concurrently, provided capture does not mutate project state.
- Source files outside a stateful editor follow normal version-control practice.
- A project may permit parallel writers only through a workflow validated in that project and recorded as locked project authority.

## 8. Routing anti-patterns

- Routing everything to one "developer" role.
- Choosing the primary by who writes the most code rather than whose domain decides success.
- Omitting subjective gates because the task "is just code".
- Adding every gate to every task without relevance reasoning (noise hides real blockers).
- Treating routing as final when new evidence shows another discipline is affected.
