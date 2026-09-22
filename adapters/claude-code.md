# Claude Code adapter

Adapter id `claude-code` · format `claude-code-project/1` · code: `gpos/adapters/backends.py` (format only) on the shared [adapter layer](README.md).

## Compatibility target

These first-party documents were consulted on 2026-09-22 (implementation time only):

- [How Claude remembers your project](https://code.claude.com/docs/en/memory):
  - project instructions live in `./CLAUDE.md` (or `./.claude/CLAUDE.md`) and are loaded at session start;
  - keep them under about 200 lines;
  - while any `CLAUDE.md` exists in the working directory or above, Claude Code does not read `AGENTS.md` (the default setting).
- [Skills](https://code.claude.com/docs/en/skills):
  - project skills live in `.claude/skills/<name>/SKILL.md`, discovered from the session directory and its parents up to the repository root;
  - `description` is always in context, and the body loads when the skill is used;
  - keep `SKILL.md` under 500 lines; supporting files load only when referenced.

Claude Code 2.1.220 was installed at implementation time. The adapter renders and syncs files; it does not run Claude Code.

## Generated files

| File | Content |
|---|---|
| `CLAUDE.md` | concise operating contract (budget 8,000 characters / 200 lines) |
| `.claude/skills/gpos-<skill>/SKILL.md` | one skill per enabled GPOS specialist; front matter `name: gpos-<skill>` and `description` |
| `.claude/skills/gpos-game-director/references/workflows/*.md` | workflow contracts, read when routing |
| `.game/gpos-generated/claude-code/manifest.json` | ownership, provenance and semantic parity |

Skills are invoked by domain intent, or directly with `/gpos-<skill>`.

## Ownership

- A human-written `CLAUDE.md` or `.claude/CLAUDE.md` is never overwritten or adopted: sync stops with `UNOWNED_ENTRYPOINT`.
- Other skills in `.claude/skills/` and `CLAUDE.local.md` are never touched.
- No hooks, settings, plugins or user-level (`~/.claude`) files are generated.

## Limitations

- A project that already relies on a human-written `CLAUDE.md` must move or merge it by hand before syncing. Phase 2B has no adoption command.
- `CLAUDE.local.md` and `CLAUDE.md` files in other directories are human-owned and load alongside the generated file. They may add local conventions, but they cannot relax GPOS rules.
- If both adapters are synced, Claude Code reads `CLAUDE.md` and not the Codex `AGENTS.md` (default setting), so the operating contract is not loaded twice.
