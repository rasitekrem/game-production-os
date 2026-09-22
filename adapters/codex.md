# Codex adapter

Adapter id `codex` · format `codex-project/1` · code: `gpos/adapters/backends.py` (format only) on the shared [adapter layer](README.md).

## Compatibility target

First-party OpenAI documentation was consulted on 2026-09-22 (implementation time only). The `developers.openai.com/codex` pages redirect (HTTP 308) to OpenAI's `learn.chatgpt.com`:

- [AGENTS.md](https://developers.openai.com/codex/guides/agents-md):
  - Codex reads `AGENTS.md` from the git root down to the working directory, closer files later;
  - an `AGENTS.override.md` takes precedence at the same level;
  - combined content is limited by `project_doc_max_bytes` (32 KiB default).
- [Skills](https://developers.openai.com/codex/skills):
  - repository skills live in `.agents/skills/<name>/SKILL.md`, scanned in every directory from the working directory up to the repository root, without installation;
  - `SKILL.md` front matter has `name` and `description`;
  - the skill list uses at most about 8,000 characters, and the body loads when the skill is selected.
- The shared skill format follows the [Agent Skills specification](https://agentskills.io/specification):
  - `name` is 1–64 characters of lower-case letters, digits and single hyphens, matching the directory;
  - `description` is at most 1,024 characters.

Codex was **not installed** at implementation time. This adapter targets the documented behaviour above and has not been verified by running Codex.

## Generated files

| File | Content |
|---|---|
| `AGENTS.md` | concise operating contract (budget 8,000 characters / 200 lines; far below the 32 KiB limit) |
| `.agents/skills/gpos-<skill>/SKILL.md` | one skill per enabled GPOS specialist; front matter `name: gpos-<skill>` and `description` |
| `.agents/skills/gpos-game-director/references/workflows/*.md` | workflow contracts, read when routing |
| `.game/gpos-generated/codex/manifest.json` | ownership, provenance and semantic parity |

Skills are invoked by domain intent, or directly with `$gpos-<skill>`.

## Ownership and scope

- A human-written root `AGENTS.md` is never overwritten or adopted: sync stops with `UNOWNED_ENTRYPOINT`.
- Only the project-root `AGENTS.md` is generated. `AGENTS.md` or `AGENTS.override.md` files closer to the working directory stay human-owned. Codex reads them after the generated file; they may add local conventions, and the generated contract states they cannot relax GPOS, project authority, Human Review or validation rules.
- Nothing is written to `~/.codex`, `~/.agents` or `$CODEX_HOME`, and no skills are installed globally. `agents/openai.yaml` is not generated.

## Limitations

- Codex has not been verified by execution (it was not installed).
- `AGENTS.md` discovery starts at the git root. For a project outside a git repository, only the working directory is assumed.
- A project that already has a human-written `AGENTS.md` must move or merge it by hand before syncing.
