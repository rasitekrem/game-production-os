# Templates

Authority skeletons for a project's `.game/` directory ([AUTHORITY-HIERARCHY.md §5](../core/AUTHORITY-HIERARCHY.md#5-project-local-authority-game)). They contain **no game decisions**. Phase 1 provides no bootstrap tool; copy them manually.

## Placeholders

| Placeholder | Meaning | Agent behaviour |
|---|---|---|
| `UNDECIDED` | Not decided yet; may be proposed by agents. | May propose a value marked `PROPOSED`. Must not treat a proposal as decided. |
| `HUMAN_DECISION_REQUIRED` | Only a human can decide this. | Must surface it; may prepare options; must not fill it. |
| `NOT_APPLICABLE` | Irrelevant to this project. | Requires a written reason. |
| `PROJECT_SPECIFIC` | The project must define its own structure here. | Propose structure; do not import another project's. |

A missing decision stays visibly missing. Agents must not silently replace missing decisions with assumptions.

## Authority status

Each document and each row carries `PROPOSED` or `LOCKED`. `LOCKED` requires a reference to a `DECISIONS.md` entry.

## Files

| Template | Copied to | Primary maintainer (proposals) |
|---|---|---|
| `PROJECT.md` | `.game/PROJECT.md` | `game-director` |
| `PILLARS.md` | `.game/PILLARS.md` | `gameplay-design` |
| `GAME-DESIGN.md` | `.game/GAME-DESIGN.md` | `gameplay-design` |
| `ART-BIBLE.md` | `.game/ART-BIBLE.md` | `art-direction` |
| `GAME-FEEL.md` | `.game/GAME-FEEL.md` | `game-feel-vfx` |
| `CAMERA.md` | `.game/CAMERA.md` | `camera-composition` |
| `ANIMATION.md` | `.game/ANIMATION.md` | `character-animation` |
| `LEVEL-DESIGN.md` | `.game/LEVEL-DESIGN.md` | `level-design` |
| `UI-UX.md` | `.game/UI-UX.md` | `ui-ux` |
| `AUDIO.md` | `.game/AUDIO.md` | `audio-design` |
| `PERFORMANCE.md` | `.game/PERFORMANCE.md` | `qa-performance` |
| `ENGINEERING.md` | `.game/ENGINEERING.md` | `game-engineering` |
| `DECISIONS.md` | `.game/DECISIONS.md`, mirroring decision records (`schemas/decision.schema.json`) | human (agents transcribe only) |
| `CURRENT.md` | `.game/CURRENT.md` | `game-director` |
| `HUMAN-REVIEW.md` | one record per review, e.g. `.game/reviews/` | agents prepare Part A; human owns Part B |

"Maintainer" means who drafts proposals. Locking is always a Human Decision.
