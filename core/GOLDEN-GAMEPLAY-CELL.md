# Golden Gameplay Cell

Status: normative · GPOS `1.0.0-alpha.14` · Elaborates [P5](PRINCIPLES.md#p5--golden-gameplay-cell-before-scale)

Workflow: [`workflows/golden-gameplay-cell.md`](../workflows/golden-gameplay-cell.md). Lifecycle stage: `GOLDEN_CELL`.

---

## 1. Definition

The Golden Gameplay Cell is **one short, representative slice of play built to production quality** — the smallest piece of the game that, recorded and shown without explanation, looks and feels like the intended shipped game.

It is not a vertical slice of every feature, a demo, or a tech test. It is the reference that later content is measured against.

## 2. Policy

- **Required by default** for every GPOS project. `project-config.schema.json` defaults `golden_gameplay_cell.required` to `true`.
- **Waivable only by Human Decision.** A waiver is recorded in `.game/DECISIONS.md` with a reason and is referenced from project config. Agents cannot waive it, and a missing config value is read as *required*. A waived project may move from `PRE_PRODUCTION` directly to `PRODUCTION` by Human Decision.
- **Blocks scale.** Until the cell exits (§5) or is waived, the project does not enter `PRODUCTION` and does not start large chapter, biome, level or content production ([PRODUCTION-LIFECYCLE.md §3](PRODUCTION-LIFECYCLE.md#3-scale-rule-golden-cell-interaction)).

## 3. Required content

The cell establishes production direction for each item below. The cell routing accounts for **every one of the 12 quality gates**: each relevant gate is required; a gate for something that genuinely does not exist in the game is omitted from the cell routing with a reason. No discipline disappears without a reason, and no fake `PASS` is recorded for an inapplicable one. `TECHNICAL`, `GAMEPLAY_DESIGN`, `VISUAL_ART`, `GAME_FEEL_VFX`, `PERFORMANCE`, `DEVICE` and `HUMAN_REVIEW` are always required (registry `workflow_gate_requirements`).

| Area | Must demonstrate | Primary gate |
|---|---|---|
| Player presentation | Final-quality player character or avatar representation | `VISUAL_ART` |
| Movement | Idle, start, locomotion, turns, stop, settle as the game uses them | `ANIMATION` |
| Camera | Production camera framing and follow behaviour | `CAMERA_COMPOSITION` |
| Representative interaction | One core verb end-to-end with its feedback | `GAMEPLAY_DESIGN`, `GAME_FEEL_VFX` |
| Representative environment | A small space at production visual quality with validated layout | `LEVEL_DESIGN`, `VISUAL_ART` |
| Visual language & lighting | Palette, materials, lighting consistent with the art bible | `VISUAL_ART` |
| UI | The HUD / prompts the cell actually needs | `UI_UX` |
| Game feel & feedback | Responsiveness, anticipation, impact, success/failure feedback | `GAME_FEEL_VFX` |
| Audio (where applicable) | Interaction audio and ambience for the cell | `AUDIO` |
| Target-device performance | The cell running within budget on a reference target device. If no device is available, these gates stay `NOT_RUN` and block exit until a device is obtained or a Human Decision changes the requirement. | `PERFORMANCE`, `DEVICE` |
| Combat / encounter (if the game has combat) | One representative encounter case | `GAMEPLAY_DESIGN`, `GAME_FEEL_VFX`, `ANIMATION` |
| Correctness | No blocking defects; persistence if the cell involves saving | `TECHNICAL` |

## 4. Primary Human Review question

> **"Without explanation, does a short representative gameplay recording look and feel like the intended production game?"**

Human Review of this question is mandatory (trigger `GOLDEN_CELL_EXIT`). No review policy, routing record or project override can replace it with cross-review.

Protocol:

1. The human first sees a short gameplay recording **without narration or context**. When the target platform differs materially from the development environment, the recording and the final camera, game-feel and UI evidence must be captured in `TARGET_RUNTIME`; the cell routing must explicitly apply or decline `TARGET_PRESENTATION_DIFFERS` for `CAMERA_COMPOSITION`, `GAME_FEEL_VFX` and `UI_UX`.
2. Only then are the specialist assessments, known limitations and per-area evidence presented.
3. The human answers the primary question and may give per-area verdicts.

A technically correct cell that is visually or game-feel inadequate **fails**. Green tests, stable performance and correct saves do not rescue a cell that does not look and feel like the intended game.

## 5. Exit conditions

The cell exits — and the project may request the `GOLDEN_CELL` → `PRODUCTION` transition — only when:

1. Every relevant gate in §3 is `PASS` on the cell scope, with current evidence.
2. `HUMAN_REVIEW` on the primary question is `PASS`.
3. The approved recording and captures are registered as **approved references** (authority level 3) for later production.
4. Decisions revealed during the cell (camera values, animation standards, material rules, budgets) are recorded in the relevant `.game/` documents and locked by Human Decision.
5. The postmortem is complete.

## 6. After exit

- The cell is the benchmark. New content is reviewed against it (`visual-review`).
- If production reveals the cell's direction does not scale — performance collapses with content density, a camera setup fails in larger spaces — the relevant decisions are reopened by Human Decision. The cell may need to be revised; the lifecycle may step back to `GOLDEN_CELL`.
- The cell is not frozen code. It is a frozen *quality reference*.

## 7. Future consideration (not Phase 1)

Games whose core modes differ materially (for example a real-time exploration mode and a turn-based combat mode) may need a **Golden Cell set** — one cell per core mode — rather than a single cell. Phase 1 defines one cell; a set is a candidate future change under [GOVERNANCE.md](GOVERNANCE.md).
