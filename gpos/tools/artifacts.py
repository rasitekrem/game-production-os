"""Artifacts a tool execution produced: metadata, hashing and derivation.

Artifact files stay files. An artifact record holds metadata and a hash; it never holds the bytes,
and no artifact payload is ever embedded in a diagnostic, a provenance block or an evidence record.
Hashing is streamed, so a multi-gigabyte capture is hashed with bounded memory.

Derivation is first class. A frame extracted from a gameplay capture is `DERIVED`, points at the
artifact it came from, and carries the origin's capture context with it. Media processing never
upgrades the authority of its source: the derived artifact remembers where the content was captured,
not where the transform ran.

An artifact from an execution that did not finish (a timeout, a failure) is recorded with
`complete = False`. Incomplete artifacts are surfaced, never silently promoted into evidence.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths as tp

ARTIFACT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

CANONICAL, DERIVED = "CANONICAL", "DERIVED"
CHUNK = 1024 * 1024


def hash_file(path):
    """Streaming sha256 of a file, in 1 MiB chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactSpec:
    """What an adapter declares it produced. The foundation turns this into an Artifact after
    checking the path and hashing the file; an adapter never supplies its own hash."""
    artifact_id: str
    kind: str                    # registry tool_artifact_kinds
    path: str                    # absolute path inside a permitted scope
    media_type: str = None
    description: str = ""
    derived_from: tuple = ()     # artifact ids this one was derived from
    complete: bool = True


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    path: str                    # project-relative when inside the project, else absolute
    absolute_path: str
    sha256: str
    bytes: int
    media_type: str = None
    description: str = ""
    classification: str = CANONICAL
    derived_from: tuple = ()
    origin_capture_context: str = None   # where the *content* was captured, carried through derivation
    created_by_request: str = None
    complete: bool = True

    def to_dict(self):
        out = {"artifact_id": self.artifact_id, "kind": self.kind, "path": self.path,
               "sha256": self.sha256, "bytes": self.bytes, "classification": self.classification,
               "derived_from": list(self.derived_from), "created_by_request": self.created_by_request,
               "complete": self.complete}
        if self.media_type:
            out["media_type"] = self.media_type
        if self.description:
            out["description"] = self.description
        if self.origin_capture_context:
            out["origin_capture_context"] = self.origin_capture_context
        return out


def collect_inputs(inputs, scopes, root, capture_contexts):
    """([Artifact], [(code, artifact_id, message)]) for the existing artifacts an execution consumes.

    The caller declares them and the capture context they came from; the foundation checks the path,
    hashes the file and records the context unchanged. Nothing the tool produces can alter it.
    """
    out, problems = [], []
    for item in inputs:
        reason = tp.unsafe_reason(scopes, item.path, must_exist=True)
        if reason:
            problems.append(("UNSAFE_ARTIFACT_PATH", item.artifact_id, reason))
            continue
        if item.capture_context is not None and item.capture_context not in capture_contexts:
            problems.append(("INVALID_TOOL_REQUEST", item.artifact_id,
                             f"{item.capture_context!r} is not a GPOS capture context"))
            continue
        path = Path(item.path)
        try:
            digest, size = hash_file(path), path.stat().st_size
        except OSError as exc:
            problems.append(("ARTIFACT_HASH_FAILED", item.artifact_id, f"{item.path}: {type(exc).__name__}: {exc}"))
            continue
        out.append(Artifact(artifact_id=item.artifact_id, kind="OTHER", path=_relative(root, path),
                            absolute_path=str(path), sha256=digest, bytes=size, media_type=item.media_type,
                            description=item.description, classification=CANONICAL,
                            origin_capture_context=item.capture_context, created_by_request=None, complete=True))
    return out, problems


def declaration_problems(specs, capability, registry_kinds, known=()):
    """[(code, artifact_id, message)] for output claims the registered capability does not support.

    The declaration stays authoritative at runtime: a capability produces only the artifact kinds it
    registered, ids are structural and unique, and an output never shadows an input the caller
    supplied. Nothing is silently renamed or reclassified.
    """
    problems, seen = [], set()
    declared, inputs = set(capability.artifact_kinds), {a.artifact_id for a in known}
    for spec in specs:
        aid = spec.artifact_id
        if not isinstance(aid, str) or not ARTIFACT_ID.fullmatch(aid or ""):
            problems.append(("INVALID_ARTIFACT_CLAIM", str(aid),
                             f"artifact id {aid!r} must be a lower-case identifier"))
            continue
        if aid in seen:
            problems.append(("INVALID_ARTIFACT_CLAIM", aid, f"two output artifacts claim the id {aid!r}"))
            continue
        seen.add(aid)
        if aid in inputs:
            problems.append(("INVALID_ARTIFACT_CLAIM", aid,
                             f"output artifact {aid!r} would shadow an input artifact of the same id"))
            continue
        if spec.kind not in registry_kinds:
            problems.append(("INVALID_ARTIFACT_CLAIM", aid,
                             f"{aid}: artifact kind {spec.kind!r} is not in registry tool_artifact_kinds"))
        elif spec.kind not in declared:
            problems.append(("INVALID_ARTIFACT_CLAIM", aid,
                             f"{aid}: {capability.id} registered artifact kinds {sorted(declared)} and may not "
                             f"produce a {spec.kind} artifact"))
    return problems


def collect(specs, scopes, root, request_id, execution_context, complete=True, known=()):
    """([Artifact], [(code, artifact_id, message)]) for the artifacts an adapter declared.

    Every path is checked against the permitted scopes before the file is opened, so a path escape
    or a symlink is refused rather than hashed. A derived artifact inherits the origin's capture
    context; a canonical one records the context this execution observed.
    """
    by_id = {a.artifact_id: a for a in known}
    out, problems = [], []
    for spec in specs:
        reason = tp.unsafe_reason(scopes, spec.path)
        if reason:
            problems.append(("UNSAFE_ARTIFACT_PATH", spec.artifact_id, reason))
            continue
        if tp.in_records(root, spec.path):
            problems.append(("UNSAFE_ARTIFACT_PATH", spec.artifact_id,
                             f"{spec.path}: tool execution never writes into the canonical record area "
                             f"{tp.RECORDS_DIR}/"))
            continue
        path = Path(spec.path)
        if not path.is_file():
            problems.append(("ARTIFACT_MISSING", spec.artifact_id, f"{spec.path}: declared artifact does not exist"))
            continue
        try:  # the foundation hashes the file; an adapter never supplies its own digest
            digest, size = hash_file(path), path.stat().st_size
        except OSError as exc:
            problems.append(("ARTIFACT_HASH_FAILED", spec.artifact_id, f"{spec.path}: {type(exc).__name__}: {exc}"))
            continue
        missing = [d for d in spec.derived_from if d not in by_id]
        if missing:
            problems.append(("EVIDENCE_ARTIFACT_UNKNOWN", spec.artifact_id,
                             f"{spec.artifact_id} is derived from unknown artifacts {missing}"))
            continue
        origins = {by_id[d].origin_capture_context for d in spec.derived_from if by_id[d].origin_capture_context}
        origin = sorted(origins)[0] if len(origins) == 1 else (None if origins else execution_context)
        if len(origins) > 1:
            problems.append(("EVIDENCE_CONTEXT_INCOMPATIBLE", spec.artifact_id,
                             f"{spec.artifact_id} derives from artifacts captured in different contexts "
                             f"{sorted(origins)}; a single derived artifact cannot claim both"))
            continue
        artifact = Artifact(
            artifact_id=spec.artifact_id, kind=spec.kind,
            path=_relative(root, path), absolute_path=str(path), sha256=digest, bytes=size,
            media_type=spec.media_type, description=spec.description,
            classification=DERIVED if spec.derived_from else CANONICAL,
            derived_from=tuple(spec.derived_from), origin_capture_context=origin,
            created_by_request=request_id, complete=bool(spec.complete) and complete)
        by_id[artifact.artifact_id] = artifact
        out.append(artifact)
    return out, problems


def _relative(root, path):
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)
