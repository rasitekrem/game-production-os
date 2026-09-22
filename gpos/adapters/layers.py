"""Detection of project-local agent instruction layers GPOS does not own (read-only).

Agent runtimes load more than the generated entry file, and they do not enforce GPOS precedence:
Codex reads every AGENTS.md / AGENTS.override.md from the git root down to the working directory,
with deeper files later (able to override); Claude Code adds CLAUDE.local.md, CLAUDE.md files in
other directories, .claude/rules/*.md and, depending on a user setting, AGENTS.md. A generated file
therefore cannot guarantee that such a layer does not relax GPOS. Phase 2B does not merge or adopt
them: sync refuses and check reports them (INSTRUCTION_LAYER_CONFLICT). Files are never modified.

Scope: the project tree (every directory except .git; symlinked directories are not followed) and,
when the project lies inside a git repository, the directories above it up to the repository root.
User-level and organization-level configuration (for example ~/.claude, ~/.codex) is a documented
trust boundary and is never read.
"""

import os
from pathlib import Path


def _is_layer(rel_parts, name, backend):
    if name in backend.instruction_layer_names:
        return True
    rel = "/".join(rel_parts)
    return name.endswith(".md") and any(f"/{d}/" in f"/{rel}" for d in backend.instruction_layer_dirs)


def _git_root_above(root):
    for parent in root.parents:
        if (parent / ".git").exists():
            return parent
    return None


def unmanaged_instruction_layers(root, backend, owned):
    """Sorted project-relative paths (parents as ../...) of instruction layers the runtime can load and GPOS does
    not own. `owned`: project-relative paths GPOS owns (entry files listed by GPOS manifests)."""
    root = Path(root).resolve()
    found = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        base = Path(dirpath).relative_to(root)
        for name in filenames:
            rel = (base / name).as_posix()
            if rel not in owned and _is_layer(rel.split("/"), name, backend):
                found.add(rel)
    if not (root / ".git").exists():
        top = _git_root_above(root)
        if top is not None:
            home = Path.home().resolve()
            depth = 0
            for parent in root.parents:
                depth += 1
                prefix = "/".join([".."] * depth)
                for name in backend.instruction_layer_names:
                    if (parent / name).is_file():
                        found.add(f"{prefix}/{name}")
                if parent != home:  # ~/.claude is user configuration: never read
                    for d in backend.instruction_layer_dirs:
                        if (parent / d).is_dir():
                            found |= {f"{prefix}/{d}/{p.relative_to(parent / d).as_posix()}"
                                      for p in (parent / d).rglob("*.md")}
                    if (parent / ".claude" / "CLAUDE.md").is_file() and "CLAUDE.md" in backend.instruction_layer_names:
                        found.add(f"{prefix}/.claude/CLAUDE.md")
                if parent == top:
                    break
    return sorted(found)


def owned_entrypoints(root, backends, read_manifest):
    """Entry files GPOS owns in this project: those listed in each adapter's (valid) manifest."""
    owned = set()
    for b in backends:
        m = read_manifest(root, b)
        if m is not None and any(f.get("path") == b.entrypoint for f in m.get("files", [])):
            owned.add(b.entrypoint)
    return owned
