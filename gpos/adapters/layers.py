"""Project-local runtime discovery checks (read-only): what else the agent runtime would load or
be told, besides the GPOS-generated files.

Agent runtimes load more than the generated entry file and do not enforce GPOS precedence. Phase 2B
does not merge, adopt or edit anything found here; sync refuses and check reports it:

* instruction layers (INSTRUCTION_LAYER_CONFLICT): Codex reads every AGENTS.md / AGENTS.override.md
  (and configured fallback names) from the git root down to the working directory, deeper files later;
  Claude Code adds CLAUDE.local.md, CLAUDE.md files in other directories, .claude/rules/*.md and,
  depending on a user setting, AGENTS.md;
* instruction configuration (INSTRUCTION_CONFIG_CONFLICT / INSTRUCTION_CONFIG_UNREADABLE): Claude Code
  project settings `claudeMdExcludes` can drop CLAUDE.md files from context; Codex project
  `.codex/config.toml` can replace AGENTS.md (`model_instructions_file`), cap how much of it is read
  (`project_doc_max_bytes`), add instruction file names (`project_doc_fallback_filenames`) or disable a
  skill (`[[skills.config]] enabled = false`);
* skill identity (SKILL_ID_CONFLICT): another project-local skill with a generated GPOS skill id.

Scope: the project tree (every directory except .git; symlinked directories are not followed) and,
when the project lies inside a git repository, the directories above it up to the repository root.
User-level, organization-level and admin configuration (~/.claude, managed settings, $CODEX_HOME,
/etc/codex) is a documented trust boundary and is never read: GPOS guarantees project-local adapter
consistency, not control of the user's whole agent installation.
"""

import json
import os
import re
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - older interpreters fail closed on any Codex project config
    tomllib = None

CLAUDE_SETTINGS = (".claude/settings.json", ".claude/settings.local.json")
CODEX_CONFIG = ".codex/config.toml"
CODEX_INSTRUCTION_REPLACEMENT_KEYS = ("model_instructions_file", "experimental_instructions_file")


def _walk(root):
    """(relative dir parts, filenames) for the project tree, skipping .git and symlinked directories."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        yield Path(dirpath).relative_to(root), sorted(filenames)


def _parents_in_repo(root):
    """[(prefix like '..', parent dir)] from the project's parent up to the enclosing git root (empty if none)."""
    if (root / ".git").exists():
        return []
    chain = []
    for depth, parent in enumerate(root.parents, 1):
        chain.append(("/".join([".."] * depth), parent))
        if (parent / ".git").exists():
            return chain
    return []


def _is_user_home(path):
    try:
        return path.resolve() == Path.home().resolve()
    except OSError:
        return False


def config_files(root, names):
    """Project-relative paths (parents as ../...) of files named like `names` (e.g. .claude/settings.json) in any
    directory of the project tree or of its parents inside the repository (never the user's home)."""
    root = Path(root).resolve()
    found = []
    for base, _ in _walk(root):
        for n in names:
            if (root / base / n).is_file():
                found.append(((base / n).as_posix(), root / base / n))
    for prefix, parent in _parents_in_repo(root):
        if not _is_user_home(parent):
            for n in names:
                if (parent / n).is_file():
                    found.append((f"{prefix}/{n}", parent / n))
    return found


# ---------------------------------------------------------------- instruction configuration

def claude_settings_problems(root):
    """[(code, path, message)] for project/local Claude Code settings that can change instruction loading.

    `claudeMdExcludes` patterns are globs matched against absolute paths and merge across settings layers;
    rather than re-implement that matching, Phase 2B conservatively treats any non-empty project/local
    `claudeMdExcludes` as a conflict. Other settings are not instruction discovery and are ignored."""
    out = []
    for rel, path in config_files(root, CLAUDE_SETTINGS):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            out.append(("INSTRUCTION_CONFIG_UNREADABLE", rel, f"{rel} cannot be parsed ({type(exc).__name__}); GPOS cannot "
                                                             f"tell whether it excludes the generated instructions"))
            continue
        excludes = data.get("claudeMdExcludes") if isinstance(data, dict) else None
        if isinstance(data, dict) and "claudeMdExcludes" in data and excludes not in (None, []):
            out.append(("INSTRUCTION_CONFIG_CONFLICT", rel, f"{rel} sets claudeMdExcludes; Claude Code could skip the "
                                                            f"generated CLAUDE.md or its layers (Phase 2B rejects any "
                                                            f"non-empty project claudeMdExcludes)"))
    return out


def _codex_values(table, key):
    """Values of `key` at top level and in every [profiles.<name>] table."""
    values = []
    if key in table:
        values.append(("", table[key]))
    for name, prof in sorted((table.get("profiles") or {}).items()) if isinstance(table.get("profiles"), dict) else []:
        if isinstance(prof, dict) and key in prof:
            values.append((f"profiles.{name}.", prof[key]))
    return values


def codex_config_problems(root, root_bytes, generated_skill_ids):
    """([(code, path, message)], extra instruction file names) for project .codex/config.toml files."""
    out, fallback = [], set()
    for rel, path in config_files(root, (CODEX_CONFIG,)):
        if tomllib is None:
            out.append(("INSTRUCTION_CONFIG_UNREADABLE", rel, f"{rel} exists but this Python has no tomllib to read it"))
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            out.append(("INSTRUCTION_CONFIG_UNREADABLE", rel, f"{rel} cannot be parsed ({type(exc).__name__}); GPOS cannot "
                                                             f"tell whether it replaces or limits the generated instructions"))
            continue
        for key in CODEX_INSTRUCTION_REPLACEMENT_KEYS:
            for where, _ in _codex_values(data, key):
                out.append(("INSTRUCTION_CONFIG_CONFLICT", rel, f"{rel} sets {where}{key}, which replaces the AGENTS.md "
                                                                f"instructions; the generated AGENTS.md must stay the entrypoint"))
        for where, value in _codex_values(data, "project_doc_max_bytes"):
            if not isinstance(value, int) or isinstance(value, bool):
                out.append(("INSTRUCTION_CONFIG_UNREADABLE", rel, f"{rel}: {where}project_doc_max_bytes is not an integer"))
            elif value < root_bytes:
                out.append(("INSTRUCTION_CONFIG_CONFLICT", rel, f"{rel} sets {where}project_doc_max_bytes = {value}, below the "
                                                                f"{root_bytes} bytes of the generated AGENTS.md"))
        for where, value in _codex_values(data, "project_doc_fallback_filenames"):
            if not isinstance(value, list) or not all(isinstance(v, str) and v and "/" not in v and "\\" not in v for v in value):
                out.append(("INSTRUCTION_CONFIG_UNREADABLE", rel, f"{rel}: {where}project_doc_fallback_filenames must be "
                                                                  f"a list of file names"))
            else:
                fallback |= set(value)
        skills = (data.get("skills") or {}).get("config") if isinstance(data.get("skills"), dict) else None
        for entry in skills if isinstance(skills, list) else []:
            if isinstance(entry, dict) and entry.get("enabled") is False:
                target = str(entry.get("path", "")) + " " + str(entry.get("name", ""))
                hit = sorted(s for s in generated_skill_ids if re.search(rf"(^|[/\s]){re.escape(s)}([/\s]|$)", target))
                if hit:
                    out.append(("INSTRUCTION_CONFIG_CONFLICT", rel, f"{rel} disables the generated skill {hit[0]}"))
    return out, sorted(fallback)


# ---------------------------------------------------------------- instruction layers

def _is_layer(rel, name, names, dirs):
    if name in names:
        return True
    return name.endswith(".md") and any(f"/{d}/" in f"/{rel}" for d in dirs)


def unmanaged_instruction_layers(root, backend, owned, extra_names=()):
    """Sorted project-relative paths (parents as ../...) of instruction layers the runtime can load and GPOS does
    not own. `owned`: project-relative paths GPOS owns; `extra_names`: configured fallback instruction names."""
    root = Path(root).resolve()
    names = tuple(backend.instruction_layer_names) + tuple(n for n in extra_names if n not in backend.instruction_layer_names)
    dirs = backend.instruction_layer_dirs
    found = set()
    for base, filenames in _walk(root):
        for name in filenames:
            rel = (base / name).as_posix()
            if rel not in owned and _is_layer(rel, name, names, dirs):
                found.add(rel)
    home = Path.home().resolve()
    for prefix, parent in _parents_in_repo(root):
        for name in names:
            if (parent / name).is_file():
                found.add(f"{prefix}/{name}")
        if parent.resolve() != home:  # ~/.claude is user configuration: never read
            for d in dirs:
                if (parent / d).is_dir():
                    found |= {f"{prefix}/{d}/{p.relative_to(parent / d).as_posix()}" for p in (parent / d).rglob("*.md")}
            if (parent / ".claude" / "CLAUDE.md").is_file() and "CLAUDE.md" in names:
                found.add(f"{prefix}/.claude/CLAUDE.md")
    return sorted(found)


def owned_entrypoints(root, backends, read_manifest):
    """Entry files GPOS owns in this project: those listed in each adapter's (valid) manifest."""
    owned = set()
    for b in backends:
        m = read_manifest(root, b)
        if m is not None and any(f.get("path") == b.entrypoint for f in m.get("files", [])):
            owned.add(b.entrypoint)
    return owned


# ---------------------------------------------------------------- skill identity

def _skill_name(path):
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return None
    m = re.match(r"^---\n(.*?)\n---", head, re.S)
    if not m:
        return None
    n = re.search(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", m.group(1), re.M)
    return n.group(1).strip() if n else None


def skill_id_collisions(root, backend, generated_ids):
    """[(path, skill id)] for project-local skills of this runtime that occupy a generated GPOS skill id: a skill
    directory with that name, or a SKILL.md declaring that name, anywhere a `<skill_root>` directory exists in the
    project (nested included), except the exact GPOS-owned path."""
    root = Path(root).resolve()
    ids = set(generated_ids)
    owned = {f"{backend.skill_root}/{i}/SKILL.md" for i in ids}
    skill_root_parts = tuple(backend.skill_root.split("/"))
    out = []
    for base, filenames in _walk(root):
        if "SKILL.md" not in filenames or len(base.parts) < len(skill_root_parts) + 1:
            continue
        if tuple(base.parts[-len(skill_root_parts) - 1:-1]) != skill_root_parts:
            continue
        rel = (base / "SKILL.md").as_posix()
        if rel in owned:
            continue
        hit = ids & {base.name, _skill_name(root / rel)}
        if hit:
            out.append((rel, sorted(hit)[0]))
    return sorted(out)
