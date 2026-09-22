# Adapters

Status: Phase 2B · GPOS `1.0.0-alpha.9` · agent adapter layer in [`gpos/adapters/`](../gpos/adapters/__init__.py)

An adapter connects GPOS contracts to a specific agent runtime or tool. Phase 2B implements **agent adapters** for two coding agents: [Claude Code](claude-code.md) and [Codex](codex.md). They render the same GPOS authority into each agent's native project instructions and skills. Engine, DCC, device and repository-hosting adapters are later phases.

> **Generated agent files are disposable projections. Canonical authority is GPOS plus Project Locked Authority.**

## What an adapter may and may not do

An adapter may:

- generate agent-specific instruction files from GPOS sources,
- expose tool capabilities (capture, render, profile, deploy) that produce GPOS-typed evidence (later phases),
- read and write GPOS records (routing, gate, evidence) in the formats defined in `schemas/`, filling evidence provenance from the tool itself wherever possible (later phases).

An adapter may **not**:

- redefine gates, statuses, evidence types, authority levels or lifecycle stages,
- emit evidence in a capture context incompatible with its type (registry `evidence_context_compatibility`),
- weaken any rule in `core/`,
- synthesize Human Review or `HUMAN_EVIDENCE` (see [HUMAN-AUTHORITY.md §7](../core/HUMAN-AUTHORITY.md#7-authenticity-of-human-evidence-trust-boundary)),
- bypass the single-writer rule for stateful editors unless a project has validated a workflow for it.

## Single source of authority

There is one production authority: GPOS (`core/`, `core/registry.json`, `skills/`, `workflows/`) plus the project's own authority (`.game/`, `.game/gpos/`). There is no Claude GPOS and no Codex GPOS.

```
GPOS sources + project authority
        │  compiler.py (explicit source selection, hashing)
        ▼
Adapter IR (model.py) ── one agent-independent model
        │  content.py (renderer: IR facts → neutral wording; authors no rule)
        ├───────────────────────┐
        ▼                       ▼
claude-code backend      codex backend        (backends.py: FORMAT only)
CLAUDE.md                AGENTS.md
.claude/skills/gpos-*/   .agents/skills/gpos-*/
```

- **Sources** (`sources.py`). Only these are read, each with a kind and a sha256:
  - `NORMATIVE`: `core/registry.json`, plus every frozen document its `agent_operating_contract` cites;
  - `SPECIALIST_SKILL`: `skills/*/SKILL.md`;
  - `WORKFLOW`: `workflows/*.md`;
  - `PROJECT_AUTHORITY`: the registry `project_authority_files` present in `.game/`, `.game/gpos/project-config.json` and decision records.
  - Manifests are `GENERATED_METADATA`, produced but never read as authority. Nothing is concatenated wholesale.
- **IR** (`model.py`). Holds:
  - GPOS version and project identity, including the project's skill namespace;
  - the operating rules: registry `agent_operating_contract` statements, each citing the frozen documents it restates;
  - authority order;
  - Human Review boundaries (mandatory and project triggers, `never_cross_reviewer`, placeholders);
  - gates;
  - enabled specialist skills, each with every contract section, maturity, gates, cross-review eligibility and the project authority files its contract names as inputs;
  - workflows with their registry gate requirements;
  - project authority documents, row by row (`LOCKED` / `PROPOSED`, decision reference, placeholders), with each lock verified against its binding Human Decision;
  - the validator contract;
  - source hashes.
- **Content** (`content.py`). A renderer: it lays IR facts out as blocks tagged with semantic ids and adds only neutral connective wording. No normative fact originates there. Every rule sentence is a registry statement rendered verbatim, and a rule the renderer does not place still renders (under "Other GPOS rules"). Tests check that no rule text appears in renderer or backend code.
- **Backends** (`backends.py`). Supply only file locations, front matter, invocation syntax, discovery wording, the instruction files their runtime can also load, and a documented compatibility declaration. A backend cannot add, drop or reword a rule.

### Canonical rules

The rules generated instructions state (authority order, Project Locked Authority, missing decisions, reserved human authority, mandatory Human Review, role routing, independent gates, game-director's routing-only role, machine validation, generated files, unmanaged instruction layers) live in registry `agent_operating_contract`. Each rule has:
- an `id`;
- a normative `statement`, rendered verbatim;
- `sources`: the frozen documents it restates, each with a verbatim quote that a test checks exists.

Each cited document is a hashed source. A change to the rule or to a document it restates is therefore reported as `SOURCE_CHANGED` until regeneration. The game-director rule names its skill and must agree with registry `never_cross_reviewer`, or compilation fails.

### Semantic equivalence, not textual equality

Claude Code and Codex outputs differ in text (file names, `/skill` vs `$skill`, discovery notes). They must not differ in meaning. Each manifest carries a `semantics` block derived from the IR only (authority order, project authority, enabled skills and their maturity, ownership, review eligibility, Human Review boundaries, validator contract, workflows). Bundle validation checks that each rendered file really contains its semantic blocks' required markers, in order: authority levels, Human Review triggers, validator commands, locked project rows, open decisions and every contract section. For one IR, the two manifests' `semantics` are identical (tested). Prose is never parsed back into rules.

## Project Locked Authority and LOCK bindings

A `LOCKED` row in a `.game/` authority table is rendered as Project Locked Authority only when a Human Decision provably locked exactly that row and value. The compiler checks every `LOCKED` row, and every document whose status is `LOCKED`, and fails closed (`AUTHORITY_LOCK_UNVERIFIED`, nothing is generated) unless the referenced decision:

- exists and is `ACTIVE`;
- has a locking kind (registry `project_lock_binding.decision_kinds`: `LOCK`);
- was decided by a human decision authority allowed to make that kind of decision;
- has this project as its subject;
- carries a structured binding in `value.locks` that targets this exact row and its current value, or, for a document lock, this document's current canonical hash.

```json
"kind": "LOCK",
"value": {"locks": [
  {"authority_path": ".game/ANIMATION.md", "section": "Quality bar",
   "item": "Root motion vs in-place policy", "value": "in-place locomotion; root motion only for authored traversal"},
  {"authority_path": ".game/PROJECT.md", "document_sha256": "<canonical hash of the document's authority rows>"}
]}
```

The human's own words (`decision.verbatim`) remain the substance; the structured binding is what tooling enforces, never by parsing prose.
- **Changed value.** A changed `LOCKED` value with the old decision reference fails; it needs a new Human Decision.
- **Wrong decision.** An unrelated `ACTIVE` decision, a decision of another kind, or a `LOCK` for another row or document never verifies a lock.
- **Helper.** `python3 -m gpos.adapters authority --project .` prints each row's exact binding and each document's canonical hash (read-only).

The frozen Phase-2A validator does not check `LOCK` payloads; this binding is enforced by the adapter compiler.

### Strict authority documents

The machine-readable part of a `.game/` authority document is parsed strictly: its status and locking-decision metadata, and every table whose header is exactly `| Item | Value | Status · decision ref |`. Any of the following fails generation with `AUTHORITY_DOCUMENT_INVALID`; nothing is silently skipped:
- a row that does not parse;
- a duplicate row;
- malformed status or reference syntax;
- a near-miss authority header;
- an authority-looking row outside an authority table;
- duplicated or missing metadata;
- a `PROPOSED` document claiming a locking decision.

Free prose and other tables are not compiled. Placeholders (`UNDECIDED`, `HUMAN_DECISION_REQUIRED`, ...) are preserved as written.

## Unmanaged instruction layers

Unmanaged project instruction layers are unsupported in Phase 2B. Agent runtimes load more than the generated entry file and do not enforce GPOS precedence:

- **Codex** reads every `AGENTS.md` and `AGENTS.override.md` from the git root down to the working directory. Deeper files come later and can override root instructions.
- **Claude Code** instruction layers are additive, and more specific instructions may take precedence. `CLAUDE.local.md`, `CLAUDE.md` files in other directories (loaded as Claude works there), `.claude/CLAUDE.md` and `.claude/rules/*.md` all add persistent instructions. With a user-level setting, `AGENTS.md` loads too.

A generated file therefore cannot promise that such a layer will not relax GPOS. Sync refuses and check reports every such project-local file that GPOS does not own (`INSTRUCTION_LAYER_CONFLICT`, exit 4). This covers:
- the project tree (all directories except `.git`);
- when the project lies in a git repository, the directories above it up to the repository root.

Such a file is never edited or deleted. Move its legitimate content into project authority (`.game/`), or wait for a future explicit adoption or overlay mechanism. Generated `AGENTS.md` and `CLAUDE.md` files that GPOS owns (listed in a manifest) do not conflict. User-level and organization-level configuration (`~/.claude`, `~/.codex`, managed settings) remains a documented trust boundary and is never read.

## Project-scoped skill ids

Generated skills are named `gpos-<namespace>-<skill>`. The namespace is the first 16 characters of the project id plus the first 6 hex digits of the project id's sha256; for example, the fixture project uses `gpos-synthetic-adapte-<hash>-character-animation`.

- **Deterministic.** Names are stable across regeneration and the same for Claude Code and Codex. There are no random or time-based parts.
- **Valid.** Names stay within 64 characters and the Agent Skills syntax.
- **Collision-resistant.** Long or similar project ids still produce different namespaces.
- **Why.** A higher-scope Claude Code skill with the same name shadows a project skill, and Codex lists same-name skills side by side. Project-scoped names remove normal name collisions with user or global skills. Global configuration can still contain arbitrary instructions; that remains a trust boundary.
- **Semantics.** Manifests map each logical GPOS skill id to its generated id (`semantics.skill_ids`). Semantic parity is on the logical ids.

## Progressive disclosure

| Layer | Loaded | Content | Budget |
|---|---|---|---|
| Root (`CLAUDE.md` / `AGENTS.md`) | every session | authority order, project authority summary, routing rule, specialist index, Human Review boundaries, validator contract, generated-file rules | 8,000 characters, 200 lines |
| Skill description (front matter) | every session (skill listing) | role and domain boundary of one specialist | 600 characters each, 8,000 total |
| Skill body (`gpos-<namespace>-<skill>/SKILL.md`) | when the skill is used | project authority for that discipline, the full generic contract, gates and reviews, validator use | 20,000 characters, 500 lines |
| Workflow references (game-director only) | when routing into a workflow | one workflow contract | 12,000 characters each |

Budgets are in characters and lines, so they do not depend on any tokenizer. They derive from the documented agent limits:
- Claude Code: `CLAUDE.md` under 200 lines, `SKILL.md` under 500 lines.
- Codex: `AGENTS.md` reads 32 KiB, and the skill list is about 8,000 characters.
- Agent Skills: a body under about 5,000 tokens, descriptions of at most 1,024 characters.

A render that exceeds a budget fails with `CONTEXT_BUDGET_EXCEEDED`; nothing is truncated. A root file that quotes any contract or workflow passage fails as monolithic. The whole of GPOS is never put in one file.

Skills are triggered by domain intent. Each description is the contract's ROLE statement plus its gates and the boundary "not for work another GPOS specialist owns". There are no keyword lists: "animation" in a UI loading spinner task is not character animation.

## Project-local layout

```
<project>/
  .game/                        project authority (canonical; never generated)
  .game/gpos/                   GPOS records (canonical; validated by the production validator)
  .game/gpos-generated/         GPOS-owned generated metadata (not authority)
    claude-code/manifest.json
    codex/manifest.json
  CLAUDE.md                     generated (Claude Code root)       } only when the adapter
  .claude/skills/gpos-<ns>-*/   generated (Claude Code skills)     } is enabled and synced
  AGENTS.md                     generated (Codex root)             }
  .agents/skills/gpos-<ns>-*/   generated (Codex skills)           }
```

Generated metadata lives in `.game/gpos-generated/`, not inside `.game/gpos/`. The frozen alpha.8 record-bundle convention treats any unexpected entry in `.game/gpos/` as an error, and adapters must not change validator semantics.

Enable adapters in the project config: `enabled_adapters: ["claude-code", "codex"]`. Optionally restrict the generated skills with `extensions` → `gpos-adapters` → `skills` (game-director is always required: it routes everything). Enabling an adapter is a project decision; sync refuses adapters the config does not enable.

## Commands

```bash
python3 -m gpos.adapters render --project PATH [--agent claude-code|codex|all] [--out <dir>] [--format text|json]
python3 -m gpos.adapters sync   --project PATH [--agent claude-code|codex|all] [--repair] [--format text|json]
python3 -m gpos.adapters check  --project PATH [--agent claude-code|codex|all] [--format text|json]
python3 -m gpos.adapters authority --project PATH [--format text|json]
```

- **render** compiles, renders and validates in memory and lists the files and their hashes. With `--out` it writes each bundle to `<dir>/<agent>/`. `<dir>` must be a new or empty directory outside `.game`, `.claude` and `.agents`. It never changes the project.
- **sync** updates only GPOS-managed files for the enabled adapters (`--agent all`) or one adapter. It is deterministic and idempotent: a second sync changes nothing, and check right after sync is clean.
- **check** is read-only. It reports drift and instruction-layer conflicts and is suitable for CI; it never regenerates.
- **authority** is read-only. It shows the parsed project authority and the exact LOCK bindings it needs.

| Exit | Meaning |
|---|---|
| 0 | OK: rendered, synced, or check clean |
| 1 | INVALID: the project is INVALID by the production validator, an authority document is malformed, a lock is not bound to its Human Decision, adapter settings are invalid, or a budget is exceeded |
| 2 | DRIFT: check found generated state out of date or edited |
| 3 | ERROR: invocation or tool error, incompatible GPOS version, unknown adapter, internal error |
| 4 | CONFLICT: sync refused because it would overwrite or delete a file GPOS does not own, or an unmanaged instruction layer exists; nothing was written. Check also reports instruction-layer conflicts with 4 |

## Validator precondition

Every command first runs the frozen Phase-2A production validator on the project (`validate_project`). An INVALID project generates nothing (`PROJECT_INVALID`). Tasks do not need to be READY: a NOT_READY routing never blocks generation. A project pinned to another GPOS version fails closed (`GPOS_VERSION_INCOMPATIBLE`, exit 3).

Generated instructions direct agents to the validator at workflow boundaries, and always before claiming READY, merge-ready, release-ready or Golden Cell exited. They say explicitly that agent reasoning is not GPOS validation. No validator logic is copied into prompts.

## Ownership and sync rules

A file is GPOS-owned only when the adapter's manifest lists it. The managed area of an adapter is its entry file, `<skill_root>/gpos-*`, and `.game/gpos-generated/<adapter>/`. Everything else, including other skills in `.claude/skills/` or `.agents/skills/`, is never touched.

- **Human-owned entry file.** An existing `CLAUDE.md` or `AGENTS.md` that GPOS did not generate stops sync with `UNOWNED_ENTRYPOINT`, and nothing is written. There is no automatic adoption and no editing of human prose. To use GPOS, move or merge that file by hand first.
- **Unowned file in the managed area** (for example a hand-made `.claude/skills/gpos-x/SKILL.md` or an extra file in a generated skill directory): `OUTPUT_CONFLICT`, nothing written.
- **Edited generated file**: `MODIFIED_MANAGED_FILE_CONFLICT`. Sync keeps the edit unless `--repair` is given, in which case it is replaced. Change the sources, not the generated files.
- **Stale generated files** (for example a skill that was disabled) are deleted only when the old manifest lists them, they lie in the managed area and they are unmodified. Empty generated skill directories are removed; nothing else is.
- **Atomicity.** Sync first plans every change for every requested adapter. If any conflict exists, nothing is written. Each file is then written to a temporary sibling and atomically replaced, and the manifest is written last. A file that already has its new content is accepted, so an interrupted sync can simply be re-run.
- **Paths.** Every managed path is relative, contains no `..`, `.` or backslash, and lies in the managed area. No existing component may be a symlink, and it must resolve inside the project. A manifest listing any other path is not trusted (`UNSAFE_PATH` on sync, `PATH_ESCAPE` on check), and such paths are never written or deleted.
- **No global state.** Nothing is written to user or global agent directories (`~/.claude`, `~/.codex`, `~/.agents`, `$CODEX_HOME`). Nothing is installed, no hooks or settings are generated, no agent is run and no network is used.

## Drift detection

`check` compares, per adapter:

| Code | Drift |
|---|---|
| `MANIFEST_MISSING` | never synced |
| `MANIFEST_INVALID` | manifest unreadable, malformed or edited (its `semantic_hash` no longer matches) |
| `ADAPTER_FORMAT_MISMATCH` | written by another adapter format version |
| `GPOS_VERSION_MISMATCH` | generated from another GPOS version |
| `SOURCE_CHANGED` | a GPOS or project source was added, removed or changed since generation |
| `MANAGED_FILE_MISSING` | a generated file was deleted |
| `MANAGED_FILE_MODIFIED` | a generated file was edited |
| `UNEXPECTED_MANAGED_FILE` | a file appeared inside the managed area |
| `GENERATED_STALE` | sources unchanged but the generator would now produce different files |
| `PATH_ESCAPE` | the manifest names a path outside the managed area |
| `INSTRUCTION_LAYER_CONFLICT` | an unmanaged project instruction file exists (class CONFLICT) |

## Manifest and provenance

`.game/gpos-generated/<adapter>/manifest.json` records:
- adapter id, format and target agent, with its compatibility declaration;
- generator versions, GPOS version and project id;
- the managed area and the enabled skills, with the logical-to-generated skill id map;
- the operating rules (id, statement hash, cited sources);
- every source (`kind`, logical id such as `gpos:skills/character-animation/SKILL.md` or `project:.game/ANIMATION.md`, and sha256);
- the IR hash and the `semantics` parity block;
- for every generated file: path, sha256, bytes, role, skill, the source ids it derives from and its semantic blocks;
- a `semantic_hash` over all of the above.

There is no timestamp, so identical inputs give byte-identical manifests; git history records when a manifest changed.

## Adding an adapter

1. Verify the agent's current first-party documentation for its instruction file, skill format and discovery, and record the target in a compatibility declaration.
2. Add a `Backend` subclass in `gpos/adapters/backends.py` with its entry file, skill root, front matter, invocation syntax, discovery note and the instruction files its runtime can also load. Add no rule text.
3. Register the id in `core/registry.json` `adapter_ids`.
4. Extend `tests/test_adapters.py` (rendering, parity, sync) and `tests/mutate_adapters.py`.

If an agent cannot express GPOS meaning in its format, stop and escalate. Do not add a backend-specific rule.

## Known limitations (Phase 2B)

- Agent behaviour is not benchmarked, and no agent is run: files are rendered, validated and synced only.
- There is no adoption of an existing human-written entry file, and no global installation of skills.
- Human authentication is out of scope ([HUMAN-AUTHORITY.md §7](../core/HUMAN-AUTHORITY.md#7-authenticity-of-human-evidence-trust-boundary)).
- There are no engine, DCC, MCP, FFmpeg, device or repository-hosting adapters, no task orchestration or subagent spawning, and no project bootstrap.
- Project authority is read from the template table format. Free prose in `.game/` files is not compiled; skills tell the agent to read the file.

## Future adapter categories

| Category | Intended role | Evidence it may produce |
|---|---|---|
| Unity official agent / plugin integration | Engine operations through the vendor's supported path | `RUNTIME_EVIDENCE`, `VISUAL_EVIDENCE`, `MOTION_EVIDENCE`, `TEST_EVIDENCE`, `PERFORMANCE_EVIDENCE` |
| Unity MCP | Engine inspection and mutation via MCP | as above |
| Blender CLI | Headless DCC operations, asset validation, renders | `VISUAL_EVIDENCE` and `MOTION_EVIDENCE` in `DCC_RENDER`; asset-analysis `CODE_EVIDENCE` / `PERFORMANCE_EVIDENCE` in `OFFLINE_ANALYSIS`. Never game `RUNTIME_EVIDENCE` — Blender executing is not the game running. |
| Blender MCP | Interactive DCC inspection and mutation | as Blender CLI; never game `RUNTIME_EVIDENCE` |
| FFmpeg | Recording, trimming, side-by-side comparisons, frame extraction | `MOTION_EVIDENCE`, `AUDIO_EVIDENCE`, `VISUAL_EVIDENCE` |
| Android / device | Install, run, capture, profile on physical devices | `DEVICE_EVIDENCE`, `PERFORMANCE_EVIDENCE`, `MOTION_EVIDENCE` |
| Git / GitHub | Provenance, review integration, gate status reporting | `CODE_EVIDENCE`, `TEST_EVIDENCE` (CI) |
