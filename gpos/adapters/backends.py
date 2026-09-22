"""Agent backends: FORMAT only (file names, skill locations, front matter, invocation syntax,
discovery behaviour) plus each backend's documented compatibility target. GPOS meaning comes
from content.py; a backend cannot change it.

Format facts were taken from first-party documentation, consulted 2026-09-22 (implementation
time only; nothing here needs the network):

* Claude Code — https://code.claude.com/docs/en/memory and https://code.claude.com/docs/en/skills
  - project instructions: ./CLAUDE.md (or ./.claude/CLAUDE.md), loaded at session start; target
    under 200 lines; while any CLAUDE.md exists, Claude Code does not read AGENTS.md (default).
  - project skills: .claude/skills/<name>/SKILL.md, discovered from the session directory and its
    parents up to the repository root; `description` is always in context, the body loads on use;
    keep SKILL.md under 500 lines; supporting files load only when referenced.
* Codex — https://developers.openai.com/codex/guides/agents-md and /codex/skills (both redirect,
  308, to OpenAI's learn.chatgpt.com)
  - AGENTS.md read from the git root down to the working directory, closer files later;
    AGENTS.override.md takes precedence at the same level; 32 KiB default project_doc_max_bytes.
  - repository skills: .agents/skills/<name>/SKILL.md, scanned in every directory from the working
    directory up to the repository root; SKILL.md front matter `name` and `description`; the skill
    list uses at most ~8000 characters; the body loads when the skill is selected.
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
            "an existing human-written CLAUDE.md or .claude/CLAUDE.md blocks sync (no adoption in Phase 2B)",
            "CLAUDE.local.md and CLAUDE.md files in other directories are human-owned and load alongside the generated file",
            "while CLAUDE.md exists Claude Code does not read AGENTS.md by default, so a generated Codex AGENTS.md is not loaded twice",
            "hooks and settings are not generated",
        ],
    }

    def agent_format(self):
        return AgentFormat(
            "Claude Code", self.entrypoint, self.skill_root, lambda d: f"the `{d}` skill (`/{d}`)",
            "Claude Code lists each skill's description automatically and loads the full skill when it is used",
            "A human-owned `CLAUDE.local.md` or subdirectory `CLAUDE.md` may add local conventions; it cannot relax GPOS, "
            "project authority, Human Review or validation rules.")


class CodexBackend(Backend):
    id = "codex"
    agent_name = "Codex"
    format_id = "codex-project/1"
    entrypoint = "AGENTS.md"
    skill_root = ".agents/skills"
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
            "a closer AGENTS.md or AGENTS.override.md is human-owned and is read after the generated file",
            "Codex truncates combined AGENTS.md content above project_doc_max_bytes (32 KiB default); the generated root is budgeted far below",
            "agents/openai.yaml is not generated; implicit invocation keeps the Codex default",
        ],
    }

    def agent_format(self):
        return AgentFormat(
            "Codex", self.entrypoint, self.skill_root, lambda d: f"the `{d}` skill (`${d}`)",
            "Codex lists each skill's description automatically and loads the full skill when it is selected",
            "A human-owned `AGENTS.md` or `AGENTS.override.md` closer to the working directory may add local conventions; it "
            "cannot relax GPOS, project authority, Human Review or validation rules.")


BACKENDS = {b.id: b for b in (ClaudeCodeBackend(), CodexBackend())}
