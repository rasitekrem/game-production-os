# Skill Maturity

Status: normative · GPOS `1.0.0-alpha.17`

Machine-readable source: [`registry.json`](registry.json) → `maturity_levels`.

---

## 1. Levels

| Level | Meaning |
|---|---|
| `DRAFT` | The contract exists and has been internally reviewed, but has not been validated through a real production task. |
| `PILOTED` | Used successfully in at least one real production task with Human Review. |
| `PROVEN` | Validated repeatedly across multiple meaningful tasks or projects. |

Maturity describes **how much the contract has been validated**, not how capable the underlying model is. A `DRAFT` contract may be excellent; it simply has not been tested against real production.

Every skill in GPOS `1.0.0-alpha.17` is `DRAFT`. No Phase-1 skill is `PILOTED` or `PROVEN`.

## 2. Declaration

Each `skills/*/SKILL.md` declares maturity twice, and both must agree:

- front matter: `maturity: DRAFT`
- the `MATURITY` section, with its promotion history (empty in Phase 1).

## 3. Promotion requirements

### `DRAFT` → `PILOTED`

All of:

1. Successful use in a real production task (not a synthetic exercise) on a real game project.
2. The skill's required evidence was produced and is retrievable.
3. Human Review of the task's subjective output, recorded.
4. A postmortem identifying what in the contract helped, what was missing or wrong, and which lessons are framework-general.
5. Contract corrections from the postmortem applied or filed.

### `PILOTED` → `PROVEN`

All of:

1. Repeated successful use, preferably across multiple meaningful tasks or multiple projects.
2. No unresolved systemic failure — a failure pattern that recurred and whose cause is in the contract.
3. Documented lessons incorporated into the contract.

### Demotion

A skill returns to a lower level when a systemic failure is found in its contract, or when a MAJOR change rewrites its ownership or evidence rules. Demotion is also a Human Decision.

## 4. Who promotes

**A skill must not promote itself.** No agent may change a skill's maturity based on its own assessment of success.

Promotion requires an explicit Human Decision, following [GOVERNANCE.md §9](GOVERNANCE.md#9-skill-maturity-promotion): a promotion proposal with evidence references, recorded in `CHANGELOG.md` and released as a versioned change.

## 5. Using a `DRAFT` skill

A `DRAFT` skill is usable in production. Users should expect gaps: follow the contract, and treat surprises as postmortem input rather than improvising new authority mid-task.
