# Production Lifecycle

Status: normative · GPOS `1.0.0-alpha.11`

Machine-readable source: [`registry.json`](registry.json) → `lifecycle_stages`.

---

## 1. Stages

```
CONCEPT → PRE_PRODUCTION → GOLDEN_CELL → PRODUCTION → POLISH → RELEASE_CANDIDATE → RELEASED
```

| Stage | Goal | Typical workflows | Content scale allowed |
|---|---|---|---|
| `CONCEPT` | Decide what the game is: fantasy, pillars, platforms, quality target | `new-game` | Documents, references, throwaway sketches |
| `PRE_PRODUCTION` | Find the fun and the production method; lock core authority | `gameplay-feature`, `character-production`, `animation-production`, `asset-production`, `level-production`, `ui-production` (all prototype-scale) | Greybox, prototypes, single representative assets, experiments |
| `GOLDEN_CELL` | Prove production direction in one representative shippable-quality slice | `golden-gameplay-cell` | Exactly the Golden Cell scope |
| `PRODUCTION` | Build content at scale to the proven standard | all production workflows | Full content |
| `POLISH` | Close quality gaps, fix, optimize; no new features without Human Decision | `visual-review`, `device-validation`, fixes | Existing content only |
| `RELEASE_CANDIDATE` | Verify a specific build is shippable | `device-validation`, `release` | Frozen except release fixes |
| `RELEASED` | Shipped; post-release fixes follow `release` again | `release` | Patches |

## 2. Stage transitions

Every transition is a **Human Decision** (trigger `MILESTONE_ACCEPTANCE`), recorded in `.game/DECISIONS.md` and reflected in `.game/CURRENT.md`. Agents may recommend a transition with evidence; they may not perform it.

| Transition | Exit evidence the `game-director` presents |
|---|---|
| `CONCEPT` → `PRE_PRODUCTION` | `PROJECT.md` and `PILLARS.md` locked; platforms and quality target decided or explicitly `UNDECIDED` with a deadline |
| `PRE_PRODUCTION` → `GOLDEN_CELL` | Core loop playable at prototype quality; `GAME-DESIGN.md` core sections locked; Golden Cell scope proposed |
| `GOLDEN_CELL` → `PRODUCTION` | Golden Cell exit conditions met ([GOLDEN-GAMEPLAY-CELL.md §5](GOLDEN-GAMEPLAY-CELL.md#5-exit-conditions)), **or** a recorded Human waiver |
| `PRE_PRODUCTION` → `PRODUCTION` | Only when the Golden Cell requirement is waived by a recorded Human Decision |
| `PRODUCTION` → `POLISH` | Content complete against project scope; open-gate list available |
| `POLISH` → `RELEASE_CANDIDATE` | Every blocking gate on release scope `PASS`; known issues a human accepted are recorded as blocking downgrades ([QUALITY-GATES.md §8](QUALITY-GATES.md#8-known-issues)) |
| `RELEASE_CANDIDATE` → `RELEASED` | `release` workflow exit conditions met |

Moving backwards is allowed and is also a Human Decision (for example, returning to `GOLDEN_CELL` when production reveals the cell's direction does not scale).

**Transition authority model.** Each transition is a decision record of kind `LIFECYCLE_TRANSITION` with `transition.from` and `transition.to`. Project config states the current stage (`lifecycle_stage`) and, for every stage after `CONCEPT`, the decision that entered it (`lifecycle_decision_ref`, schema-required). Allowed transitions are listed in the registry (`lifecycle_transitions`): the forward transitions above, any backward move, and `PRE_PRODUCTION` → `PRODUCTION` only with a Golden Cell waiver. A project may not claim a stage whose entering decision is missing, of the wrong kind, targets a different stage or was made by someone not authorized to decide it — checking that against real records is a Phase-2 requirement ([GOVERNANCE.md §12](GOVERNANCE.md#12-phase-2-acceptance-requirement-record-validation)).

## 3. Scale rule (Golden Cell interaction)

Before `PRODUCTION`, content is limited to what is needed to learn: prototypes, greyboxes, single representative assets and the Golden Cell itself. The following are **not** started before the Golden Cell exits (or is waived):

- multiple chapters, biomes, levels or worlds at production quality,
- asset sets beyond what the cell needs (full character roster, full prop library),
- mass animation sets beyond the representative locomotion and interaction set.

Pre-production prototypes and greybox exploration of future areas are allowed, provided they are labelled prototype-quality and do not claim production gates.

## 4. Stage-appropriate quality

Gates exist in every stage, but the *question* scales:

- In `PRE_PRODUCTION`, a greybox `LEVEL_DESIGN` `PASS` means "metrics and flow work", not "shippable". The gate record's scope and notes state this.
- From `GOLDEN_CELL` onward, subjective gates are judged against the production quality target ([P11](PRINCIPLES.md#p11--professional-indie-quality-target)).
- A decision that passed at prototype quality may be reopened when production-quality evidence reveals a problem ([AUTHORITY-HIERARCHY.md §4](AUTHORITY-HIERARCHY.md#4-reopening-locked-authority)).

## 5. `CURRENT.md`

`.game/CURRENT.md` always states the current stage, the active task(s) with routing references, open blocking gates, pending Human Reviews and blockers. Agents keep it current; it is the first document an agent reads when resuming work.
