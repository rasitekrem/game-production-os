# Principles

Status: normative · GPOS `1.0.0-alpha.8`

These eleven principles are the constitution of Game Production OS (GPOS). Every other document elaborates one or more of them. Where a detailed document exists, it is referenced instead of restating the rule.

---

## P1 — Human creative authority

Human Decision is the highest authority in any GPOS project.

Agents may inspect, analyze, implement, compare, generate options, prepare evidence and recommend. Agents may not silently create canonical creative authority, and may not synthesize Human Review.

Agents own implementation. Humans own final subjective judgement. A human should not be required to perform technical engine or DCC work merely because the agent workflow is incomplete; an incomplete workflow is an agent-side defect to escalate, not a task to hand to the human.

Detail: [HUMAN-AUTHORITY.md](HUMAN-AUTHORITY.md).

## P2 — Game production, not generic software

Correct code is mandatory. Correct code is not sufficient for acceptance.

A feature is not complete because it compiles, tests pass, it is deterministic, save data is correct, performance is acceptable, an asset imports, or a button works. Each relevant production discipline must pass on its own terms.

| This passing… | …does not imply this passing |
|---|---|
| `TECHNICAL` | `ANIMATION` |
| `PERFORMANCE` | `GAME_FEEL_VFX` |
| A working UI control (`TECHNICAL`) | `UI_UX` |
| A successful asset import (`TECHNICAL`) | `VISUAL_ART` |
| `LEVEL_DESIGN` | `VISUAL_ART` (and the reverse) |

## P3 — Independent quality gates

Every relevant discipline has its own gate with its own evidence rules and its own status. No gate passes because another gate passed. `NOT_RUN` never means `PASS`. `NOT_APPLICABLE` always carries a reason.

Detail: [QUALITY-GATES.md](QUALITY-GATES.md).

## P4 — Evidence, typed

A gate status is a claim. A claim is only as strong as the typed evidence behind it. Each gate declares which evidence classes it accepts and which classes are insufficient on their own.

Detail: [EVIDENCE-RULES.md](EVIDENCE-RULES.md).

## P5 — Golden Gameplay Cell before scale

Before mass content production, the project proves its production direction in one representative, shippable-quality slice of play. Content does not scale until the Golden Cell has exited — its gates `PASS`, Human Review accepted, and the transition to production decided by a human — unless Human Decision explicitly waives the requirement.

Detail: [GOLDEN-GAMEPLAY-CELL.md](GOLDEN-GAMEPLAY-CELL.md).

## P6 — Specialist ownership

There is no generic "game developer" specialist. The `game-director` routes; domain specialists own narrow areas with explicit non-ownership. Runtime software belongs to `game-engineering`, which implements what other disciplines specify and never decides design or accepts its own work. Review is proportional: bounded work inside approved authority may be closed by its owner, subjective work is cross-reviewed by default, and direction, canon, baselines, milestones, release and authority always go to a human.

Detail: [ROLE-ROUTING.md](ROLE-ROUTING.md), `skills/*/SKILL.md`.

## P7 — Authority hierarchy

1. Human Decision
2. Project locked authority
3. Approved visual / audio / animation references
4. Game Production OS
5. Engine / tool defaults
6. Implementation
7. Agent recommendation

Lower authority may not silently override higher authority. GPOS itself sits below project-specific Human Decisions.

Detail: [AUTHORITY-HIERARCHY.md](AUTHORITY-HIERARCHY.md).

## P8 — Visual feedback loop

Visual work follows: **modify → render/play → capture → inspect → correct → capture again.**

No asset, scene or presentation feature is visually complete because export succeeded, import succeeded, code executed or no exception occurred. An agent that has not looked at a fresh capture of its change has not finished the change.

## P9 — Motion requires motion evidence

Character animation, locomotion, turning, start/stop, camera follow, camera lag and dead-zone behaviour, VFX timing, gameplay feedback timing and game feel are judged in motion. Static screenshots and numerical diagnostics may supplement `MOTION_EVIDENCE`; they never replace it.

## P10 — Single writer per stateful editor

Stateful editors (game engines, DCC tools such as Blender) default to **one mutating agent at a time** per project instance. Any number of read-only reviewers may operate concurrently. A project may relax this only through an explicitly validated workflow recorded as project authority.

Detail: [ROLE-ROUTING.md §7](ROLE-ROUTING.md#7-concurrency).

## P11 — Professional indie quality target

Default target: **polished professional indie quality.**

- Prototype or debug presentation is not final quality.
- AAA production complexity is not the default.

Priority order when trading off: correctness → responsiveness → readability → visual coherence → animation quality → game feel → stable target-device performance → AI-maintainable production → additional complexity.

A project may raise or lower this target only through Human Decision recorded in `.game/PROJECT.md`.
