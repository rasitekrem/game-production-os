"""Agent backends: FORMAT only (file names, skill locations, front matter, invocation syntax,
discovery behaviour) plus each backend's documented compatibility target. GPOS meaning comes
from content.py; a backend cannot change it.

Format facts were taken from first-party documentation, consulted 2026-09-22 (implementation
time only; nothing here needs the network):

* Claude Code — https://code.claude.com/docs/en/memory and https://code.claude.com/docs/en/skills
  - project instructions: ./CLAUDE.md (or ./.claude/CLAUDE.md), loaded at session start; target
    under 200 lines; while any CLAUDE.md exists, Claude Code does not read AGENTS.md (default; a
    user-level setting can make it read both). Instruction files are additive: CLAUDE.local.md,
    CLAUDE.md files in parent directories and in subdirectories (loaded as Claude works there) and
    .claude/rules/*.md all add persistent instructions, and more specific instructions generally
    take precedence when they conflict.
  - skills with the same name at a higher scope (enterprise, personal) shadow a project skill.
  - project skills: .claude/skills/<name>/SKILL.md, discovered from the session directory and its
    parents up to the repository root; `description` is always in context, the body loads on use;
    keep SKILL.md under 500 lines; supporting files load only when referenced.
* Codex — https://developers.openai.com/codex/guides/agents-md and /codex/skills (both redirect,
  308, to OpenAI's learn.chatgpt.com)
  - AGENTS.md read from the git root down to the working directory, closer files later, so deeper
    instructions can override root instructions; AGENTS.override.md takes precedence at the same
    level; 32 KiB default project_doc_max_bytes.
  - repository skills: .agents/skills/<name>/SKILL.md, scanned in every directory from the working
    directory up to the repository root; SKILL.md front matter `name` and `description`; the skill
    list uses at most ~8000 characters; the body loads when the skill is selected; two skills with the
    same name are not merged and both can appear in skill selectors.
* Shared skill format — Agent Skills specification (https://agentskills.io/specification):
  `name` 1–64 characters of a-z, 0-9 and single hyphens, matching the directory; `description`
  1–1024 characters.
"""

import json

from .content import AgentFormat

MANIFEST_ROOT = ".game/gpos-generated"
ADAPTER_LAYER_VERSION = "1"


class Backend:
    id = None
    agent_name = None
    format_id = None
    entrypoint = None
    skill_root = None
    compatibility = None

    @property
    def manifest_dir(self):
        return f"{MANIFEST_ROOT}/{self.id}"

    @property
    def manifest_path(self):
        return f"{self.manifest_dir}/manifest.json"

    # project-local instruction files the runtime can load in addition to the entry file (format facts;
    # gpos.adapters.layers detects them, content.py names them)
    instruction_layer_names = ()
    instruction_layer_dirs = ()

    def managed_prefixes(self):
        return (f"{self.skill_root}/gpos-", f"{self.manifest_dir}/")

    def agent_format(self):
        raise NotImplementedError

    def skill_front_matter(self, name, description):
        # JSON strings are valid YAML scalars: safe for any description text.
        return f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n"


class ClaudeCodeBackend(Backend):
    id = "claude-code"
    agent_name = "Claude Code"
    format_id = "claude-code-project/1"
    entrypoint = "CLAUDE.md"
    skill_root = ".claude/skills"
    instruction_layer_names = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
    instruction_layer_dirs = (".claude/rules",)
    compatibility = {
        "target_agent": "Claude Code",
        "documented_target": "Claude Code project instructions (CLAUDE.md) and project skills (.claude/skills/<name>/SKILL.md), "
                             "per code.claude.com/docs/en/memory and /skills, consulted 2026-09-22",
        "locally_verified": "Claude Code 2.1.220 installed at implementation time; files rendered, not executed",
        "required_capabilities": ["project CLAUDE.md loaded at session start",
                                  "project skills in .claude/skills with name/description front matter, body loaded on use"],
        "entrypoints": ["CLAUDE.md"],
        "skill_discovery": "automatic: .claude/skills in the session directory and its parents up to the repository root",
        "known_limitations": [
            "an existing human-written CLAUDE.md blocks sync (no adoption in Phase 2B)",
            "CLAUDE.md layers are additive and more specific instructions may take precedence, so any project-local "
            "CLAUDE.md, .claude/CLAUDE.md, CLAUDE.local.md, AGENTS.md or .claude/rules file GPOS does not own blocks sync and check",
            "user, enterprise and parent-of-repository instruction files and settings are outside the project: a documented trust boundary",
            "higher-scope skills shadow project skills of the same name; generated skill ids are project-scoped to avoid collisions",
            "hooks and settings are not generated",
        ],
    }

    def agent_format(self):
        return AgentFormat(
            "Claude Code", self.entrypoint, self.skill_root, lambda d: f"`/{d}`",
            "Claude Code lists each skill's description automatically and loads the full skill when it is used",
            self.instruction_layer_names + tuple(f"{d}/" for d in self.instruction_layer_dirs))


class CodexBackend(Backend):
    id = "codex"
    agent_name = "Codex"
    format_id = "codex-project/1"
    entrypoint = "AGENTS.md"
    skill_root = ".agents/skills"
    instruction_layer_names = ("AGENTS.md", "AGENTS.override.md")
    compatibility = {
        "target_agent": "OpenAI Codex",
        "documented_target": "Codex AGENTS.md project instructions and repository skills (.agents/skills/<name>/SKILL.md), "
                             "per developers.openai.com/codex (redirected to learn.chatgpt.com), consulted 2026-09-22",
        "locally_verified": "Codex not installed at implementation time; not verified by execution",
        "required_capabilities": ["AGENTS.md read from the git root down to the working directory",
                                  "repository skills in .agents/skills with name/description front matter, body loaded on use"],
        "entrypoints": ["AGENTS.md"],
        "skill_discovery": "automatic: .agents/skills in every directory from the working directory up to the repository root",
        "known_limitations": [
            "an existing human-written AGENTS.md blocks sync (no adoption in Phase 2B)",
            "AGENTS.md discovery starts at the git root; outside a git repository only the working directory is assumed",
            "deeper AGENTS.md and AGENTS.override.md files can override root instructions, so any such file GPOS does not own "
            "blocks sync and check",
            "user-level Codex instructions and configured fallback file names are outside the project: a documented trust boundary",
            "same-name skills are not merged; generated skill ids are project-scoped to avoid collisions",
            "Codex truncates combined AGENTS.md content above project_doc_max_bytes (32 KiB default); the generated root is budgeted far below",
            "agents/openai.yaml is not generated; implicit invocation keeps the Codex default",
        ],
    }

    def agent_format(self):
        return AgentFormat(
            "Codex", self.entrypoint, self.skill_root, lambda d: f"`${d}`",
            "Codex lists each skill's description automatically and loads the full skill when it is selected",
            self.instruction_layer_names + tuple(f"{d}/" for d in self.instruction_layer_dirs))


BACKENDS = {b.id: b for b in (ClaudeCodeBackend(), CodexBackend())}
