"""Static Unity project inspection and package-source preflight (Phase 2C-5).

Pure functions over the files of a Unity project. They start no process, touch no network and write
nothing. Everything fails closed with `ProjectProblem`: a project that is not laid out as a Unity
project, a version file that is not exactly readable, or a package configuration that could make the
Unity Package Manager reach a source other than Unity's own package service or the local project.

The package boundary (alpha.15):

* `Packages/manifest.json` may use only the documented top-level keys `dependencies`, `enableLockFile`,
  `resolutionStrategy`, `testables` and `pinnedPackages`; `scopedRegistries` is accepted only absent or
  empty; any other key (for example a legacy `registry` override) is refused;
* a dependency is either a registry version or `file:<path>` resolving to an existing local package
  folder or `.tgz` inside the GPOS project root; every Git form (`https://…`, `git+…`, `ssh://…`, `git://…`,
  `user@host:…`, `….git`, `?path=`, `#revision`) and every other URL form is refused;
* `Packages/packages-lock.json`, when present, may record only the sources `builtin`, `registry` (at
  Unity's default registry URL), `embedded`, `local` and `local-tarball`.

A refused project is reported, never rewritten: no dependency is removed, replaced or reinterpreted.
Messages name the rule and the package, never a URL or a path.
"""

import json
import os
import re
from pathlib import Path

LAYOUT = ("Assets", "Packages", "ProjectSettings")
VERSION_FILE = ("ProjectSettings", "ProjectVersion.txt")
MANIFEST = ("Packages", "manifest.json")
LOCK = ("Packages", "packages-lock.json")
MAX_VERSION_FILE = 4096
MAX_MANIFEST = 1024 * 1024
MAX_LOCK = 8 * 1024 * 1024
MAX_PROJECT_INPUT = 256
MAX_DEPENDENCIES = 1024

EDITOR_VERSION = re.compile(r"[0-9]{4}\.[0-9]{1,3}\.[0-9]{1,3}[abfp][0-9]{1,3}")
REVISION_LINE = re.compile(r"([0-9]{4}\.[0-9]{1,3}\.[0-9]{1,3}[abfp][0-9]{1,3}) \(([0-9a-f]{6,40})\)")
PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,213}")
REGISTRY_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
MANIFEST_KEYS = {"dependencies", "enableLockFile", "resolutionStrategy", "testables", "pinnedPackages",
                 "scopedRegistries"}
RESOLUTION_STRATEGIES = {"lowest", "highestPatch", "highestMinor", "highest"}
LOCK_SOURCES = {"builtin", "registry", "embedded", "local", "local-tarball"}
DEFAULT_REGISTRY = "https://packages.unity.com"


class ProjectProblem(ValueError):
    """The project is not a supported Unity project for this adapter. `rule` names what failed."""

    def __init__(self, rule, message):
        super().__init__(message)
        self.rule, self.message = rule, message


def _inside(path, root):
    path, root = os.path.normcase(path), os.path.normcase(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _bounded_read(path, limit, what):
    try:
        if path.is_symlink() or not path.is_file():
            raise ProjectProblem("LAYOUT", f"{what} is missing or not a regular file")
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        raise ProjectProblem("LAYOUT", f"{what} could not be read") from None
    if len(data) > limit:
        raise ProjectProblem("LAYOUT", f"{what} is larger than {limit} bytes")
    return data


def _strict_json(data, what):
    def no_duplicates(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ProjectProblem("PACKAGE_CONFIG", f"{what} repeats a key")
        return dict(pairs)
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates)
    except ProjectProblem:
        raise
    except (UnicodeDecodeError, ValueError):
        raise ProjectProblem("PACKAGE_CONFIG", f"{what} is not strict UTF-8 JSON") from None


# ---------------------------------------------------------------- layout and version

def resolve_project(gpos_root, value):
    """(resolved Unity project directory, project-relative POSIX spelling). Raises ProjectProblem."""
    value = "." if value is None else value
    if not isinstance(value, str) or not value or len(value) > MAX_PROJECT_INPUT or \
            any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ProjectProblem("PROJECT_PATH", "unity_project must be a short project-relative path")
    if os.path.isabs(value) or value.startswith(("~", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ProjectProblem("PROJECT_PATH", "unity_project must be relative to the GPOS project root")
    parts = Path(value).parts
    if ".." in parts:
        raise ProjectProblem("PROJECT_PATH", "unity_project must not leave the GPOS project root")
    root = os.path.realpath(gpos_root)
    project = os.path.realpath(os.path.join(root, value))
    if not _inside(project, root):
        raise ProjectProblem("PROJECT_PATH", "unity_project resolves outside the GPOS project root")
    for name in LAYOUT:
        if not os.path.isdir(os.path.join(project, name)):
            raise ProjectProblem("LAYOUT", f"the Unity project has no {name}/ directory")
    relative = Path(os.path.relpath(project, root)).as_posix()
    return Path(project), relative


def editor_version(project):
    """(exact editor version, revision or None) from ProjectSettings/ProjectVersion.txt."""
    raw = _bounded_read(Path(project).joinpath(*VERSION_FILE), MAX_VERSION_FILE, "ProjectSettings/ProjectVersion.txt")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise ProjectProblem("VERSION_FILE", "ProjectVersion.txt is not ASCII") from None
    fields = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, sep, rest = line.partition(":")
        if not sep or key.strip() in fields:
            raise ProjectProblem("VERSION_FILE", "ProjectVersion.txt is not a list of unique `key: value` lines")
        fields[key.strip()] = rest.strip()
    version = fields.get("m_EditorVersion")
    if version is None or not EDITOR_VERSION.fullmatch(version):
        raise ProjectProblem("VERSION_FILE", "ProjectVersion.txt has no exact m_EditorVersion (e.g. 6000.5.8f1)")
    revision = None
    if "m_EditorVersionWithRevision" in fields:
        match = REVISION_LINE.fullmatch(fields["m_EditorVersionWithRevision"])
        if not match or match.group(1) != version:
            raise ProjectProblem("VERSION_FILE", "m_EditorVersionWithRevision does not agree with m_EditorVersion")
        revision = match.group(2)
    return version, revision


# ---------------------------------------------------------------- package sources

def _local_package(value, packages_dir, root, name):
    """A `file:` dependency must be a plain local path to an existing package folder or .tgz inside the
    GPOS project root. URL forms (`file://…`), Git-looking targets and query/fragment syntax are refused."""
    raw = value[len("file:"):]
    if not raw or raw.startswith("//") or any(c in raw for c in "?#") or raw.rstrip("/").endswith(".git"):
        raise ProjectProblem("PACKAGE_SOURCE", f"dependency {name!r} uses an unsupported file: form")
    target = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(packages_dir, raw))
    if not _inside(target, root):
        raise ProjectProblem("PACKAGE_SOURCE", f"dependency {name!r} resolves outside the GPOS project root")
    if os.path.isdir(target):
        if not os.path.isfile(os.path.join(target, "package.json")):
            raise ProjectProblem("PACKAGE_SOURCE", f"dependency {name!r} is not a local package folder")
        if os.path.exists(os.path.join(target, ".git")):
            raise ProjectProblem("PACKAGE_SOURCE", f"dependency {name!r} is a Git working copy")
        return "local"
    if os.path.isfile(target) and target.endswith(".tgz"):
        return "local-tarball"
    raise ProjectProblem("PACKAGE_SOURCE", f"dependency {name!r} does not name an existing local package")


def _dependency_source(name, value, packages_dir, root):
    if not isinstance(name, str) or not PACKAGE_NAME.fullmatch(name):
        raise ProjectProblem("PACKAGE_CONFIG", "a dependency has a malformed package name")
    if not isinstance(value, str):
        raise ProjectProblem("PACKAGE_CONFIG", f"dependency {name!r} has a non-string value")
    if REGISTRY_VERSION.fullmatch(value):
        return "registry"
    if value.startswith("file:"):
        return _local_package(value, packages_dir, root, name)
    raise ProjectProblem("PACKAGE_SOURCE", f"dependency {name!r} is neither a registry version nor a local file: "
                                           f"package; remote, Git and other sources are not resolved by this adapter")


def _names(value, key):
    if not isinstance(value, list) or not all(isinstance(n, str) and PACKAGE_NAME.fullmatch(n) for n in value):
        raise ProjectProblem("PACKAGE_CONFIG", f"manifest {key} must be a list of package names")
    return value


def check_manifest(project, gpos_root):
    """A summary of Packages/manifest.json, or ProjectProblem."""
    project, root = Path(project), os.path.realpath(gpos_root)
    manifest = _strict_json(_bounded_read(project.joinpath(*MANIFEST), MAX_MANIFEST, "Packages/manifest.json"),
                            "Packages/manifest.json")
    if not isinstance(manifest, dict):
        raise ProjectProblem("PACKAGE_CONFIG", "Packages/manifest.json is not a JSON object")
    unknown = sorted(set(manifest) - MANIFEST_KEYS)
    if unknown:
        raise ProjectProblem("PACKAGE_CONFIG", f"manifest key {unknown[0][:64]!r} is not supported (only dependencies, "
                                               f"enableLockFile, resolutionStrategy, testables, pinnedPackages)")
    if manifest.get("scopedRegistries", []) != []:
        raise ProjectProblem("PACKAGE_SOURCE", "the manifest declares scopedRegistries; custom package registries "
                                               "are not supported")
    if "enableLockFile" in manifest and not isinstance(manifest["enableLockFile"], bool):
        raise ProjectProblem("PACKAGE_CONFIG", "manifest enableLockFile must be a boolean")
    if "resolutionStrategy" in manifest and manifest["resolutionStrategy"] not in RESOLUTION_STRATEGIES:
        raise ProjectProblem("PACKAGE_CONFIG", "manifest resolutionStrategy is not a documented value")
    testables = _names(manifest.get("testables", []), "testables")
    _names(manifest.get("pinnedPackages", []), "pinnedPackages")
    dependencies = manifest.get("dependencies", {})
    if not isinstance(dependencies, dict) or len(dependencies) > MAX_DEPENDENCIES:
        raise ProjectProblem("PACKAGE_CONFIG", "manifest dependencies must be an object of bounded size")
    packages_dir = os.path.realpath(project / "Packages")
    sources = {}
    for name, value in sorted(dependencies.items()):
        kind = _dependency_source(name, value, packages_dir, root)
        sources[kind] = sources.get(kind, 0) + 1
    return {"dependencies": len(dependencies), "by_source": dict(sorted(sources.items())), "testables": len(testables)}


def check_lock(project, gpos_root):
    """A summary of Packages/packages-lock.json when present (None when absent), or ProjectProblem."""
    project, root = Path(project), os.path.realpath(gpos_root)
    path = project.joinpath(*LOCK)
    if not path.exists() and not path.is_symlink():
        return None
    lock = _strict_json(_bounded_read(path, MAX_LOCK, "Packages/packages-lock.json"), "Packages/packages-lock.json")
    entries = lock.get("dependencies") if isinstance(lock, dict) else None
    if not isinstance(entries, dict) or len(entries) > MAX_DEPENDENCIES * 4:
        raise ProjectProblem("PACKAGE_CONFIG", "Packages/packages-lock.json has no bounded dependencies object")
    packages_dir = os.path.realpath(project / "Packages")
    sources = {}
    for name, entry in sorted(entries.items()):
        if not isinstance(name, str) or not PACKAGE_NAME.fullmatch(name) or not isinstance(entry, dict):
            raise ProjectProblem("PACKAGE_CONFIG", "the lock file has a malformed entry")
        source = entry.get("source")
        if source not in LOCK_SOURCES:
            raise ProjectProblem("PACKAGE_SOURCE", f"the lock file resolves {name!r} from an unsupported source "
                                                   f"({str(source)[:16]!r})")
        if source == "registry" and entry.get("url") != DEFAULT_REGISTRY:
            raise ProjectProblem("PACKAGE_SOURCE", f"the lock file resolves {name!r} from a registry other than "
                                                   f"Unity's default package service")
        if source in ("local", "local-tarball"):
            version = entry.get("version")
            if not isinstance(version, str) or not version.startswith("file:"):
                raise ProjectProblem("PACKAGE_SOURCE", f"the lock file records local package {name!r} without a "
                                                       f"file: location")
            _local_package(version, packages_dir, root, name)
        sources[source] = sources.get(source, 0) + 1
    return {"entries": len(entries), "by_source": dict(sorted(sources.items()))}


def preflight(gpos_root, value):
    """Everything static about one Unity project: (project dir, summary dict). Raises ProjectProblem."""
    project, relative = resolve_project(gpos_root, value)
    version, revision = editor_version(project)
    manifest = check_manifest(project, gpos_root)
    lock = check_lock(project, gpos_root)
    summary = {"unity_project": relative, "editor_version": version, "manifest": manifest,
               "lock": lock if lock is not None else {"present": False}}
    if revision:
        summary["editor_revision"] = revision
    if lock is not None:
        summary["lock"] = dict(lock, present=True)
    return project, summary
