# Codex adapter

Adapter id `codex` · format `codex-project/1` · code: `gpos/adapters/backends.py` (format only) on the shared [adapter layer](README.md).

## Compatibility target

First-party OpenAI documentation was consulted on 2026-09-22 (implementation time only). The `developers.openai.com/codex` pages redirect (HTTP 308) to OpenAI's `learn.chatgpt.com`:

- [AGENTS.md](https://developers.openai.com/codex/guides/agents-md):
  - Codex reads `AGENTS.md` from the git root down to the working directory, closer files later, so **deeper instructions can override root instructions**;
  - an `AGENTS.override.md` takes precedence at the same level;
  - combined content is limited by `project_doc_max_bytes` (32 KiB default).
- [Skills](https://developers.openai.com/codex/skills):
  - repository skills live in `.agents/skills/<name>/SKILL.md`, scanned in every directory from the working directory up to the repository root, without installation;
  - `SKILL.md` front matter has `name` and `description`;
  - the skill list uses at most about 8,000 characters, and the body loads when the skill is selected;
  - two skills with the same name are not merged; both can appear in skill selectors. GPOS therefore generates project-scoped names, `gpos-<namespace>-<skill>`.
- The shared skill format follows the [Agent Skills specification](https://agentskills.io/specification):
  - `name` is 1–64 characters of lower-case letters, digits and single hyphens, matching the directory;
  - `description` is at most 1,024 characters.

Codex was **not installed** at implementation time. This adapter targets the documented behaviour above and has not been verified by running Codex.

## Generated files

| File | Content |
|---|---|
| `AGENTS.md` | concise operating contract (budget 8,000 characters / 200 lines; far below the 32 KiB limit) |
| `.agents/skills/gpos-<namespace>-<skill>/SKILL.md` | one skill per enabled GPOS specialist; front matter `name` (the scoped id) and `description` |
| `.agents/skills/gpos-<namespace>-game-director/references/workflows/*.md` | workflow contracts, read when routing |
| `.game/gpos-generated/codex/manifest.json` | ownership, provenance and semantic parity |

Skills are invoked by domain intent, or directly with `$gpos-<namespace>-<skill>`.

## Ownership and scope

- A human-written root `AGENTS.md` is never overwritten or adopted: sync stops with `UNOWNED_ENTRYPOINT`.
- Only the project-root `AGENTS.md` is generated.
- Because deeper files can override it, any `AGENTS.md` or `AGENTS.override.md` elsewhere in the project that GPOS does not own stops sync and is reported by check (`INSTRUCTION_LAYER_CONFLICT`). This includes a root `AGENTS.override.md` and such files above the project up to the git repository root. They are never edited. Move their content into `.game/` authority first.
- Project `.codex/config.toml` files (project tree and parents up to the repository root; top level and profiles) are read with `tomllib` and never edited:
  - `model_instructions_file` / `experimental_instructions_file` → `INSTRUCTION_CONFIG_CONFLICT`;
  - `project_root_markers` (any value; it changes project-root discovery, so a session in a subdirectory may miss the generated `AGENTS.md`) → `INSTRUCTION_CONFIG_CONFLICT`. Codex's full root-discovery algorithm is not re-implemented;
  - `project_doc_max_bytes` below the generated `AGENTS.md` size → `INSTRUCTION_CONFIG_CONFLICT`;
  - `project_doc_fallback_filenames` adds instruction names, and an unowned file with such a name is an `INSTRUCTION_LAYER_CONFLICT`;
  - `[[skills.config]] enabled = false` targeting a generated skill → `INSTRUCTION_CONFIG_CONFLICT`;
  - malformed configuration → `INSTRUCTION_CONFIG_UNREADABLE`.
- A project skill (root or nested `.agents/skills/`) with a generated GPOS skill id, by directory or declared `name`, is a `SKILL_ID_CONFLICT`.
- `$CODEX_HOME` / `~/.codex`, admin (`/etc/codex`) and user-level instructions, configuration and skills are outside the project and are not inspected: a documented trust boundary. GPOS guarantees project-local consistency only.
- Nothing is written to `~/.codex`, `~/.agents` or `$CODEX_HOME`, and no skills are installed globally. `agents/openai.yaml` is not generated.

## Limitations

- `RUNTIME_NOT_YET_SMOKE_TESTED`: Codex was not installed, and Phase 2B runs no agent. Loading the generated files in a live Codex session belongs to the real-agent pilot before production use.
- `AGENTS.md` discovery starts at the git root. For a project outside a git repository, only the working directory is assumed.
- A project that already has a human-written `AGENTS.md` must move or merge it by hand before syncing.
