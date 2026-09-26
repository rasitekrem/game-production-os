"""Validation of a rendered bundle, before anything is written.

Structural only: file set, paths, budgets, skill front matter, manifest consistency, and the
presence (in order) of each semantic block's required markers from content.py. Generated prose
is never parsed back into rules.
"""

import json
import re

from . import content
from . import diagnostics as dg
from .manifest import ir_semantics
from .paths import is_managed
from .render import MANIFEST, REFERENCE, ROOT, SKILL

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
GLOBAL_PATHS = ("~/.claude", "~/.codex", "~/.agents", "$HOME", "CODEX_HOME", "/Users/", "/home/", "/etc/codex")


def _in_order(text, markers):
    """First marker not found (in order) in text, or None."""
    hay, pos = content.normalize(text), 0
    for m in markers:
        i = hay.find(content.normalize(m), pos)
        if i < 0:
            return m
        pos = i
    return None


def _front_matter(text):
    m = re.match(r"^---\nname: (.*)\ndescription: (.*)\n---\n", text)
    if not m:
        return None, None
    try:
        return m.group(1), json.loads(m.group(2))
    except ValueError:
        return m.group(1), None


def validate_bundle(bundle):
    ir, backend = bundle.ir, bundle.backend
    fmt = backend.agent_format()
    out = []

    def err(code, message, path=None, **details):
        out.append(dg.make(code, message, backend.id, path, details))

    files = bundle.by_path()
    notice = ir.rule("GENERATED_FILES_NOT_AUTHORITY").statement
    expected = {backend.entrypoint, backend.manifest_path}
    expected |= {f"{backend.skill_root}/{s.agent_id}/SKILL.md" for s in ir.skills}
    if any(s.name == "game-director" for s in ir.skills):
        expected |= {f"{backend.skill_root}/{ir.skill('game-director').agent_id}/references/workflows/{w.name}.md" for w in ir.workflows}
    if set(files) != expected:
        err("RENDER_INVALID", "rendered file set differs from the IR", missing=sorted(expected - set(files)),
            extra=sorted(set(files) - expected))
    descriptions = 0
    for path, f in sorted(files.items()):
        if not is_managed(backend, path):
            err("RENDER_INVALID", f"{path} is outside the managed area", path)
        text = f.text
        for g in GLOBAL_PATHS:
            if g in text:
                err("RENDER_INVALID", f"{path} names a user/global location {g!r}", path)
        if f.role == ROOT:
            lines = text.count("\n")
            if len(text) > content.ROOT_MAX_CHARS or lines > content.ROOT_MAX_LINES:
                err("CONTEXT_BUDGET_EXCEEDED", f"root instructions are {len(text)} characters / {lines} lines "
                    f"(budget {content.ROOT_MAX_CHARS} / {content.ROOT_MAX_LINES})", path, chars=len(text), lines=lines)
            markers = content.root_markers(ir, fmt)
            for skill in ir.skills:  # progressive disclosure: no contract passage in the root file
                for heading, section in skill.sections:
                    excerpt = content.normalize(section)[:content.MONOLITH_EXCERPT_CHARS]
                    if len(excerpt) >= content.MONOLITH_EXCERPT_CHARS and excerpt in content.normalize(text):
                        err("RENDER_INVALID", f"root embeds {skill.name} {heading} (monolithic instructions)", path)
            for w in ir.workflows:
                excerpt = content.normalize(w.body)[:content.MONOLITH_EXCERPT_CHARS]
                if excerpt in content.normalize(text):
                    err("RENDER_INVALID", f"root embeds workflow {w.name} (monolithic instructions)", path)
        elif f.role == SKILL:
            skill = ir.skill(f.skill)
            lines = text.count("\n")
            if len(text) > content.SKILL_MAX_CHARS or lines > content.SKILL_MAX_LINES:
                err("CONTEXT_BUDGET_EXCEEDED", f"skill {skill.name} is {len(text)} characters / {lines} lines "
                    f"(budget {content.SKILL_MAX_CHARS} / {content.SKILL_MAX_LINES})", path, chars=len(text), lines=lines)
            name, description = _front_matter(text)
            if name != skill.agent_id or not NAME_RE.fullmatch(name or "") or len(name) > 64 \
                    or path.split("/")[-2] != name:
                err("RENDER_INVALID", f"skill name {name!r} is not a valid name matching its directory", path)
            if not description or len(description) > content.DESCRIPTION_MAX_CHARS:
                err("CONTEXT_BUDGET_EXCEEDED" if description else "RENDER_INVALID",
                    f"skill description is {len(description or '')} characters (1..{content.DESCRIPTION_MAX_CHARS})", path)
            descriptions += len(description or "")
            markers = content.skill_markers(ir, skill, fmt)
            for other in ir.skills:  # no duplicated authority: a contract lives only in its own skill
                if other.name != skill.name:
                    owns = content.normalize(dict(other.sections)["OWNS"])[:content.MONOLITH_EXCERPT_CHARS]
                    if owns in content.normalize(text):
                        err("RENDER_INVALID", f"{skill.name} duplicates the {other.name} contract", path)
        elif f.role == REFERENCE:
            if len(text) > content.REFERENCE_MAX_CHARS:
                err("CONTEXT_BUDGET_EXCEEDED", f"reference is {len(text)} characters (budget {content.REFERENCE_MAX_CHARS})", path)
            markers = {"workflow": [notice]}
        else:
            markers = {}
        if f.role != MANIFEST:
            if notice not in text:
                err("RENDER_INVALID", f"{path} lacks the generated-file notice", path)
            for semantic in f.semantics:
                missing = _in_order(text, markers.get(semantic, []))
                if semantic not in markers:
                    err("RENDER_INVALID", f"{path}: semantic block {semantic!r} has no marker definition", path)
                elif missing is not None:
                    err("RENDER_INVALID", f"{path}: semantic block {semantic!r} lacks {missing[:80]!r}", path)
            required = set(markers) - set(f.semantics)
            if required:
                err("RENDER_INVALID", f"{path}: required semantic blocks missing {sorted(required)}", path)
    if descriptions > content.DESCRIPTIONS_TOTAL_MAX_CHARS:
        err("CONTEXT_BUDGET_EXCEEDED", f"skill descriptions total {descriptions} characters "
            f"(budget {content.DESCRIPTIONS_TOTAL_MAX_CHARS})")
    m = bundle.manifest.data
    if m["semantics"] != ir_semantics(ir) or m["enabled_skills"] != [s.name for s in ir.skills]:
        err("RENDER_INVALID", "manifest semantics differ from the IR")
    for entry in m["files"]:
        f = files.get(entry["path"])
        if f is None or f.sha256 != entry["sha256"]:
            err("RENDER_INVALID", f"manifest hash for {entry['path']} does not match the rendered file", entry["path"])
    if {e["path"] for e in m["files"]} != set(files) - {backend.manifest_path}:
        err("RENDER_INVALID", "manifest file list differs from the rendered files")
    return dg.sort(out)
