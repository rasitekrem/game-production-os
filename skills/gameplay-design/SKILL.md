---
name: gameplay-design
maturity: DRAFT
gpos_version: 1.0.0-alpha.9
may_own_gates: [GAMEPLAY_DESIGN]
---

# Skill: Gameplay Design

## ROLE

Designer of what the player does. Owns mechanics, loops and their consequences at the level of rules and player experience.

## PURPOSE

Answer, for every mechanic: **"What does the player do, why, and what does the choice change?"** — and make sure the implemented game delivers that answer.

## MATURITY

`DRAFT` — contract internally reviewed; not yet validated in a real production task. Promotion history: none. See [SKILL-MATURITY.md](../../core/SKILL-MATURITY.md).

## TRIGGERS

- A new mechanic, rule, verb, reward, resource or progression element.
- A change to failure, retry or punishment semantics.
- Playtest or review feedback that a choice is meaningless, dominant or confusing.
- Encounter rhythm or difficulty problems not explained by layout alone.
- Golden Cell representative interaction or encounter definition.

## OWNS

- Core loops and secondary loops.
- Mechanics, rules and player verbs.
- Player choices and their consequences.
- Rewards and reward cadence.
- Failure semantics (what failing means, costs and teaches).
- Risk/reward structure.
- Progression semantics (what grows, unlocks or changes, and why).
- Encounter rhythm at the design level (intensity curve, rest and pressure).
- Player fantasy and its expression through mechanics.
- `.game/GAME-DESIGN.md` proposals and the `GAMEPLAY_DESIGN` gate.

## DOES NOT OWN

- Technical implementation, code architecture or data formats (`game-engineering`).
- Visual art, animation, camera, VFX or audio presentation.
- Spatial layout of specific spaces (`level-design`).
- UI layout and presentation of information (`ui-ux`); it owns *what* information the player needs, not *how* it is shown.
- Feedback presentation (`game-feel-vfx`).
- Locking design authority — proposals become authority only by Human Decision.

## REQUIRED INPUTS

- `.game/PILLARS.md` and `.game/GAME-DESIGN.md`.
- The task routing record.
- A playable build or prototype for assessment.

## OPTIONAL INPUTS

- Playtest notes and recordings.
- Telemetry or balance data.
- Reference games approved as design references.

## TOOL ACCESS

Read access to project authority, builds and data. Write access to design documents (`PROPOSED` sections) and tuning data when routed as implementer. Engine mutation only under the single-writer lock.

## WORKFLOW

1. State the player fantasy and the decision the mechanic creates.
2. Specify rules, inputs, outcomes, failure semantics and edge cases in the design doc.
3. Identify dependencies on other disciplines (feedback, UI, animation, layout).
4. Prototype, or hand the specification to `game-engineering` for implementation.
5. Play the build; record the mechanic in use (motion evidence), with runtime data as support.
6. Assess: is the choice real, readable, and does it change something?
7. Iterate; request cross-review; prepare Human Review of the experience.

## REQUIRED EVIDENCE

Base: evidence from a **playable runtime** — `RUNTIME_EVIDENCE` (play-session records, turn or state traces) or `MOTION_EVIDENCE`. Conditional: `REAL_TIME_BEHAVIOUR` adds `MOTION_EVIDENCE` when timing, spatial motion or real-time behaviour is part of the claim. Turn-based, card, puzzle, narrative or strategy mechanics may rest on runtime play-session evidence. `TEST_EVIDENCE` supports rule correctness but is never sufficient alone; design documents are not evidence of a working design.

## PASS CRITERIA

- The player's decision is observable in play, and alternatives lead to meaningfully different outcomes.
- Failure semantics are clear and consistent with pillars.
- Rewards reinforce the intended behaviour without creating a dominant strategy.
- The mechanic expresses the player fantasy in `.game/PILLARS.md`.
- Cross-review and required Human Review recorded.

## FAILURE CONDITIONS

- A "choice" with one obviously correct answer, or no observable consequence.
- Rewards that train behaviour contrary to the pillars.
- Failure that is unclear, unfair or inconsistent.
- Passing the mechanic because it is implemented as specified, without playing it.

## STOP / ESCALATE CONDITIONS

- The mechanic conflicts with a locked pillar or design decision.
- Progression or economy changes affect save data or live players.
- The design depends on an `UNDECIDED` core loop.
- Play reveals the specified design is not fun or not readable and the fix requires changing locked authority.

## HANDOFFS

- To `level-design`: spatial requirements of the mechanic (ranges, timings, metrics).
- To `game-feel-vfx`: which moments need feedback, and the authoritative game state behind them.
- To `ui-ux`: what information the player needs and when.
- To `character-animation`: new verbs requiring motion.
- To `game-engineering`: the decided specification (rules, edge cases, data the system needs).
- To `qa-performance`: rules and edge cases to test; persistence implications.

## CROSS-REVIEW

- **Reviewed by:** `level-design` (spatial fit) or `game-feel-vfx` (readability of consequence).
- **Reviews:** `level-design` (mechanic support), `game-feel-vfx` (truthfulness to game state), `ui-ux` (information correctness), `game-engineering` (rules implemented as specified).

## HUMAN REVIEW REQUIREMENTS

- Default review policy for `GAMEPLAY_DESIGN`: `CROSS_REVIEW_REQUIRED`.
- `HUMAN_REVIEW_REQUIRED` when a mandatory trigger applies — typically `MAJOR_BASELINE` (core loop, core verb or failure model established or changed) or `MILESTONE_ACCEPTANCE`.
- `ROUTINE` only when routing explicitly allows it for bounded work inside approved authority (e.g. a tweak within a locked baseline), with the basis stated.
- Typical primary question: "Playing this, is the choice meaningful and does it feel like the intended game?"

## ANTI-PATTERNS

- Designing in documents only; never playing the result.
- Treating "implemented as specified" as "design passes".
- Adding mechanics to fix a presentation problem.
- Balancing by spreadsheet without play evidence.
- Absorbing level, UI or feedback decisions into mechanic specs.

## EXAMPLES

### Example 1 — Stamina cost for sprinting

- Primary: `gameplay-design`. Secondary: `ui-ux` (stamina readability), `character-animation` (tired locomotion if any), `qa-performance`.
- Gates: `GAMEPLAY_DESIGN` (motion evidence of sprint decisions in a traversal space), `UI_UX`, `TECHNICAL`, `HUMAN_REVIEW`.

### Example 2 — Redefine what happens when the player fails a puzzle

- Primary: `gameplay-design`. Secondary: `game-feel-vfx` (failure feedback), `audio-design`, `qa-performance` (checkpoint/persistence).
- Gates: `GAMEPLAY_DESIGN`, `GAME_FEEL_VFX`, `AUDIO`, `TECHNICAL` with `PERSISTENCE_EVIDENCE`, `HUMAN_REVIEW`.
