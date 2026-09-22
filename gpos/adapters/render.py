"""Render one backend's bundle from the IR (in memory; nothing is written here)."""

import hashlib

from . import content
from .manifest import build_manifest
from .model import IR

ROOT, SKILL, REFERENCE, MANIFEST = "root", "skill", "reference", "manifest"


class RenderedFile:
    __slots__ = ("path", "data", "role", "sources", "semantics", "skill")

    def __init__(self, path, text, role, sources, semantics, skill=None):
        self.path, self.data, self.role = path, text.encode("utf-8"), role
        self.sources, self.semantics, self.skill = tuple(sorted(set(sources))), tuple(semantics), skill

    @property
    def text(self):
        return self.data.decode("utf-8")

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()


class Bundle:
    def __init__(self, backend, ir, files, manifest):
        self.backend, self.ir, self.files, self.manifest = backend, ir, files, manifest

    def by_path(self):
        return {f.path: f for f in self.files}


def _authority_sources(ir, files=None):
    return [d.source_id for d in ir.project_authority if d.present and (files is None or d.file in files)]


def render_bundle(ir: IR, backend):
    fmt = backend.agent_format()
    mpath = backend.manifest_path
    config_and_decisions = [s["id"] for s in ir.sources if s["id"].startswith("project:.game/gpos/")]
    registry = ["gpos:core/registry.json"]
    files = []

    root_blocks = content.root_blocks(ir, fmt, mpath)
    files.append(RenderedFile(backend.entrypoint, content.markdown(root_blocks), ROOT,
                              registry + config_and_decisions + _authority_sources(ir) + [s.source_id for s in ir.skills],
                              [b.semantic for b in root_blocks]))
    for skill in ir.skills:
        d = f"{backend.skill_root}/{skill.agent_id}"
        blocks = content.skill_blocks(ir, skill, fmt, mpath)
        text = backend.skill_front_matter(skill.agent_id, skill.description) + "\n" + content.markdown(blocks)
        files.append(RenderedFile(f"{d}/SKILL.md", text, SKILL,
                                  registry + config_and_decisions + _authority_sources(ir, skill.authority_files)
                                  + [skill.source_id] + ([w.source_id for w in ir.workflows] if skill.name == "game-director" else []),
                                  [b.semantic for b in blocks], skill.name))
        if skill.name == "game-director":
            for w in ir.workflows:
                files.append(RenderedFile(f"{d}/references/workflows/{w.name}.md", content.workflow_reference(ir, w, mpath),
                                          REFERENCE, [w.source_id], ["workflow"], skill.name))
    files.sort(key=lambda f: f.path)
    manifest = build_manifest(ir, backend, files)
    files.append(RenderedFile(mpath, manifest.text, MANIFEST, [], ["manifest"]))
    return Bundle(backend, ir, files, manifest)
