"""Adapter manifest: ownership, provenance and semantic-parity metadata of one generated bundle.

    .game/gpos-generated/<adapter>/manifest.json

The manifest is deterministic (no timestamps: git history records when it changed) and holds
hashes and identifiers, never copies of source documents. It answers, for every generated file:
which GPOS version and adapter produced it, from which sources (ids + sha256), with which
semantic blocks. `semantics` is derived from the IR only, so manifests of different agents
rendered from one IR must carry identical `semantics` (tested).
"""

import hashlib
import json

from .. import __version__
from .sources import canonical_json

MANIFEST_VERSION = 1


def ir_semantics(ir):
    """Agent-independent semantic summary of the IR (the parity contract between backends)."""
    return {
        "gpos_version": ir.gpos_version,
        "project": {"id": ir.project["id"], "gpos_version": ir.project["gpos_version"],
                    "lifecycle_stage": ir.project["lifecycle_stage"]},
        "authority_order": [lvl for lvl, _ in ir.authority_order],
        "project_authority": [{
            "path": d.path, "present": d.present, "status": d.status, "locked_by": d.locked_by, "source": d.source_id,
            "document_sha256": d.document_sha256,
            "locked": [[r.section, r.item, r.decision_ref, r.lock_verified] for r in d.rows if r.status == "LOCKED"],
            "human_decision_required": [[r.section, r.item] for r in d.rows if "HUMAN_DECISION_REQUIRED" in r.placeholders],
            "undecided": [[r.section, r.item] for r in d.rows if "UNDECIDED" in r.placeholders],
        } for d in ir.project_authority],
        "enabled_skills": [s.name for s in ir.skills],
        "skill_ids": {s.name: s.agent_id for s in ir.skills},
        "disabled_skills": list(ir.disabled_skills),
        "rules": [{"id": r.id, "statement_sha256": hashlib.sha256(r.statement.encode("utf-8")).hexdigest(),
                   "skill": r.skill, "sources": list(r.sources)} for r in ir.rules],
        "skills": {s.name: {
            "maturity": s.maturity, "may_own_gates": list(s.may_own_gates), "sections": [h for h, _ in s.sections],
            "authority_files": list(s.authority_files), "cross_reviewers": list(s.cross_reviewers),
            "reviews_for": list(s.reviews_for), "never_cross_reviewer": s.never_cross_reviewer,
        } for s in ir.skills},
        "human_review": dict(ir.human_review),
        "validator": dict(ir.validator),
        "workflows": [{"name": w.name, "always_required": list(w.always_required), "account_for_all_gates": w.account_for_all_gates,
                       "mandatory_triggers": list(w.mandatory_triggers)} for w in ir.workflows],
    }


class Manifest:
    def __init__(self, data):
        self.data = data
        self.text = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def build_manifest(ir, backend, files):
    data = {
        "manifest_version": MANIFEST_VERSION,
        "adapter": {"id": backend.id, "format": backend.format_id, "target_agent": backend.agent_name},
        "generator": {"gpos": __version__, "adapter_layer": "1"},
        "gpos_version": ir.gpos_version,
        "project_id": ir.project["id"],
        "compatibility": backend.compatibility,
        "managed": {"entrypoints": [backend.entrypoint], "prefixes": list(backend.managed_prefixes())},
        "enabled_skills": [s.name for s in ir.skills],
        "sources": [dict(s) for s in ir.sources],
        "ir_sha256": hashlib.sha256(canonical_json(ir.to_dict()).encode("utf-8")).hexdigest(),
        "semantics": ir_semantics(ir),
        "files": [{"path": f.path, "sha256": f.sha256, "bytes": len(f.data), "role": f.role, "skill": f.skill,
                   "sources": list(f.sources), "semantics": list(f.semantics)} for f in files],
    }
    data["semantic_hash"] = hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
    return Manifest(data)


REQUIRED_KEYS = ("manifest_version", "adapter", "generator", "gpos_version", "project_id", "managed", "enabled_skills",
                 "sources", "ir_sha256", "semantics", "files", "semantic_hash")


def parse_manifest(raw):
    """Parsed manifest dict, or raise ValueError describing why it is malformed."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest is not a JSON object")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"manifest lacks {missing}")
    if not isinstance(data["files"], list) or not all(
            isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("sha256"), str) for f in data["files"]):
        raise ValueError("manifest files must be objects with path and sha256")
    body = {k: v for k, v in data.items() if k != "semantic_hash"}
    if hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest() != data["semantic_hash"]:
        raise ValueError("manifest semantic_hash does not match its content (edited by hand)")
    return data
