"""Private, bounded Git inspection copy; never a command runner or a config API.

Git has no wildcard override for arbitrary filter driver names. A config preflight followed by
status in the source repository has a check/use race. Instead, content-reading commands use a
private copy with flattened configuration, no includes, and no filter definitions. Selecting a
filter attribute is refused, rather than interpreting an unfiltered comparison as provenance.
"""

import hashlib
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path

CONFIG_ARGV = ("config", "--null", "--list", "--includes")
LAYOUT_ARGV = ("rev-parse", "--absolute-git-dir", "--git-common-dir")
INDEX_ARGV = ("ls-files", "--stage", "-v", "-z")
SYSTEM_ATTRIBUTES_ARGV = ("var", "GIT_ATTR_SYSTEM")
GLOBAL_ATTRIBUTES_ARGV = ("var", "GIT_ATTR_GLOBAL")
EXCLUDES_ARGV = ("config", "--path", "--get", "core.excludesfile")
ATTRIBUTES_ARGV = ("check-attr", "-z", "filter", "--")
COMMANDS = (CONFIG_ARGV, LAYOUT_ARGV, INDEX_ARGV, SYSTEM_ATTRIBUTES_ARGV, GLOBAL_ATTRIBUTES_ARGV, EXCLUDES_ARGV)
CAPTURE_BYTES = 1024 * 1024
MAX_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 20000
MAX_REPOSITORIES = 32
ATTRIBUTE_ARG_BYTES = 12000


class UnsafeInspection(ValueError):
    pass


def path_lines(raw, count):
    if not raw.endswith(b"\n"):
        raise UnsafeInspection("unterminated path output")
    lines = raw[:-1].split(b"\n")
    if len(lines) != count or any(not line or b"\0" in line or b"\r" in line for line in lines):
        raise UnsafeInspection("ambiguous path output")
    return lines


def config_entries(raw):
    if raw and not raw.endswith(b"\0"):
        raise UnsafeInspection("incomplete configuration output")
    result = []
    for record in raw.split(b"\0")[:-1]:
        if b"\r" in record:
            raise UnsafeInspection("unrepresentable configuration value")
        key, separator, value = record.partition(b"\n")
        name = key.decode("utf-8", "surrogateescape")
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*\.(?:.*\.)?[a-zA-Z][a-zA-Z0-9-]*", name, re.S):
            raise UnsafeInspection("unrecognized configuration key")
        result.append((name, value if separator else b"true"))
    return result


def _quote(value):
    return b'"' + value.replace(b"\\", b"\\\\").replace(b'"', b'\\"').replace(
        b"\n", b"\\n").replace(b"\t", b"\\t").replace(b"\b", b"\\b") + b'"'


def frozen_config(entries):
    """Flatten Git's own parsed values; includes and executable filters cannot be reread."""
    out = []
    for key, value in entries:
        lower = key.lower()
        if lower.startswith("include.") or lower.startswith("includeif.") or lower.startswith("filter."):
            continue
        if lower in ("core.worktree", "extensions.worktreeconfig"):
            continue
        if lower.startswith("extensions.") and lower not in ("extensions.objectformat",):
            raise UnsafeInspection("unsupported repository extension")
        if lower.endswith(".promisor") or lower == "extensions.partialclone":
            raise UnsafeInspection("partial-clone inspection is unsupported")
        section, remainder = key.split(".", 1)
        if "." in remainder:
            subsection, variable = remainder.rsplit(".", 1)
            heading = section.encode() + b" " + _quote(subsection.encode("utf-8", "surrogateescape"))
        else:
            heading, variable = section.encode(), remainder
        out.append(b"[" + heading + b"]\n" + variable.encode() + b" = " + _quote(value) + b"\n")
    return b"".join(out) + b"[core]\nbare = false\n"


def index_paths(raw):
    if raw and not raw.endswith(b"\0"):
        raise UnsafeInspection("incomplete index output")
    paths, submodules = set(), set()
    for record in raw.split(b"\0")[:-1]:
        header, separator, path = record.partition(b"\t")
        fields = header.split(b" ")
        if not separator or len(fields) != 4 or fields[0] in (b"S", b"s") or fields[0].islower():
            raise UnsafeInspection("unproven index flags or malformed index output")
        if not re.fullmatch(rb"[0-7]{6}", fields[1]) or not re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", fields[2]) or fields[3] not in (b"0", b"1", b"2", b"3"):
            raise UnsafeInspection("unrecognized index entry")
        name = os.fsdecode(path)
        parts = name.split("/")
        if sys.platform == "win32":
            if ":" in name or "\\" in name:
                raise UnsafeInspection("unsafe Windows index path")
        if not name or Path(name).is_absolute() or any(p in ("", ".", "..") or p.lower() == ".git" for p in parts):
            raise UnsafeInspection("unsafe index path")
        paths.add(name)
        if fields[1] == b"160000":
            submodules.add(name)
    return sorted(paths), sorted(submodules)


class InspectionCopy:
    def __init__(self, timeout):
        self.temporary = tempfile.TemporaryDirectory(prefix="gpos-git-inspection-")
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "worktree"
        self.hooks = self.base / "no-hooks"
        self.hooks.mkdir()
        self.deadline = time.monotonic() + timeout
        self.remaining = MAX_BYTES
        self.entries = 0
        self.sources = {}
        self.manifests = []
        self.repositories = []

    def close(self):
        self.temporary.cleanup()

    def bound(self):
        if time.monotonic() > self.deadline or self.remaining < 0 or self.entries > MAX_ENTRIES:
            raise UnsafeInspection("inspection copy exceeded its time, byte or entry bound")

    def seconds(self):
        self.bound()
        return max(0.001, self.deadline - time.monotonic())

    def remember(self, source, expected):
        if source in self.sources and self.sources[source] != expected:
            raise UnsafeInspection("reused source changed during inspection")
        self.sources[source] = expected

    def _identity(self, path):
        info = path.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise UnsafeInspection("reparse point in inspection source")
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def file(self, source, target):
        self.bound()
        identity = self._identity(source)
        self.entries += 1
        if stat.S_ISLNK(identity[2]):
            text = os.readlink(source)
            os.symlink(text, target)
            self.remember(source, (identity, text))
            return
        if not stat.S_ISREG(identity[2]):
            raise UnsafeInspection("nonregular inspection source")
        self.remaining -= identity[3]
        self.bound()
        digest = hashlib.sha256()
        copied = 0
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            while True:
                self.bound()
                chunk = incoming.read(64 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > identity[3]:
                    raise UnsafeInspection("source grew while copying")
                digest.update(chunk)
                outgoing.write(chunk)
        if copied != identity[3] or self._identity(source) != identity:
            raise UnsafeInspection("source changed while copying")
        os.chmod(target, stat.S_IMODE(identity[2]) | stat.S_IWUSR)
        # Keep times for ignored-file semantics, but always read the source again before reporting.
        os.utime(target, ns=(identity[4], identity[4]))
        self.remember(source, (identity, digest.digest()))

    def tree(self, source, target, skip=()):
        target.mkdir(parents=True, exist_ok=True)
        manifest = set()

        def walk(directory, destination):
            self.bound()
            for child in sorted(directory.iterdir()):
                relative = child.relative_to(source)
                if relative.as_posix() in skip:
                    continue
                manifest.add(relative)
                mode = self._identity(child)[2]
                if stat.S_ISDIR(mode):
                    self.entries += 1
                    (destination / child.name).mkdir(exist_ok=True)
                    walk(child, destination / child.name)
                else:
                    self.file(child, destination / child.name)
        walk(source, target)
        self.manifests.append((source, set(skip), manifest))

    def add_repository(self, source, target, layout, entries, paths, submodules):
        if len(self.repositories) >= MAX_REPOSITORIES:
            raise UnsafeInspection("too many initialized submodules")
        gitdir, common = layout
        metadata = target / ".git"
        if metadata.is_file():
            metadata.unlink()  # only our disposable copy's gitfile
        metadata.mkdir(exist_ok=True)
        self.tree(common, metadata, skip=("worktrees", "modules", "config", "config.worktree", "commondir"))
        if gitdir != common:
            for name in ("HEAD", "index"):
                if (metadata / name).exists():
                    (metadata / name).unlink()
                if (gitdir / name).is_file():
                    self.file(gitdir / name, metadata / name)
        if any((metadata / "objects").rglob("*.promisor")) or (metadata / "objects" / "info" / "alternates").exists():
            raise UnsafeInspection("partial or alternate object storage is unsupported")
        (metadata / "config").write_bytes(frozen_config(entries) + b"[core]\nhooksPath = " +
                                           _quote(str(self.hooks).encode("utf-8")) + b"\n")
        self.repositories.append((source, target, paths, submodules))

    def attributes(self, target, system, global_):
        combined = self.base / ("attributes-" + str(len(self.repositories)))
        parts = []
        for source in (system, global_):
            if source is not None and source.exists():
                copy = self.base / ("attr-source-" + str(self.entries))
                self.file(source, copy)
                if copy.is_symlink():
                    raise UnsafeInspection("linked external attribute file")
                parts.append(copy.read_bytes() + b"\n")
            elif source is not None:
                self.remember(source, None)
        combined.write_bytes(b"".join(parts))
        with (target / ".git" / "config").open("ab") as config:
            config.write(b"[core]\nattributesFile = " + _quote(str(combined).encode("utf-8")) + b"\n")

    def excludes(self, target, source):
        frozen = self.base / ("excludes-" + str(len(self.repositories)))
        if source is not None and source.exists():
            self.file(source, frozen)
            if frozen.is_symlink():
                raise UnsafeInspection("linked external ignore file")
        else:
            frozen.write_bytes(b"")
            if source is not None:
                self.remember(source, None)
        with (target / ".git" / "config").open("ab") as config:
            config.write(b"[core]\nexcludesFile = " + _quote(str(frozen).encode("utf-8")) + b"\n")

    def verify(self):
        """Refuse a changing source; this is an observation, never an atomic repository transaction."""
        for source, expected in self.sources.items():
            self.bound()
            if expected is None:
                if source.exists():
                    raise UnsafeInspection("attribute source appeared during inspection")
                continue
            identity, content = expected
            if self._identity(source) != identity:
                raise UnsafeInspection("source identity changed during inspection")
            if stat.S_ISLNK(identity[2]):
                actual = os.readlink(source)
            else:
                digest = hashlib.sha256()
                with source.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(64 * 1024), b""):
                        self.bound()
                        digest.update(chunk)
                actual = digest.digest()
            if actual != content:
                raise UnsafeInspection("source content changed during inspection")
        for source, skip, manifest in self.manifests:
            actual = set()
            def walk(directory):
                self.bound()
                for child in directory.iterdir():
                    if child.relative_to(source).as_posix() in skip:
                        continue
                    actual.add(child.relative_to(source))
                    if stat.S_ISDIR(self._identity(child)[2]):
                        walk(child)
            walk(source)
            if actual != manifest:
                raise UnsafeInspection("source entries changed during inspection")

    def invalidate_stats(self):
        """Force content comparisons without parsing or modifying Git's index format.

        An index stores stat times as big-endian 32-bit seconds. Choose a time whose byte
        representation is absent everywhere in every private index, including shared indexes.
        It therefore cannot equal any cached mtime, regardless of index version or alignment.
        Only private tracked regular files are retimed; source timestamps are untouched.
        """
        indexes = []
        for source, target, paths, children in self.repositories:
            metadata = target / ".git"
            for index in [metadata / "index", *metadata.glob("sharedindex.*")]:
                if index.exists():
                    if index.stat().st_size > CAPTURE_BYTES or index.is_symlink():
                        raise UnsafeInspection("index exceeds the content-verification bound")
                    indexes.append(index.read_bytes())
        stamp = None
        for candidate in range(1024):
            self.bound()
            if all(candidate.to_bytes(4, "big") not in raw for raw in indexes):
                stamp = candidate
                break
        if stamp is None:
            raise UnsafeInspection("cannot invalidate index stat caches")
        for source, target, paths, children in self.repositories:
            for name in paths:
                self.bound()
                path = target / name
                if path.exists() and stat.S_ISREG(path.lstat().st_mode):
                    os.utime(path, (stamp, stamp))


def attribute_batches(paths):
    batch, size = [], 0
    for name in paths:
        cost = len(name.encode("utf-16-le", "surrogatepass")) + 8
        if cost > ATTRIBUTE_ARG_BYTES:
            raise UnsafeInspection("attribute argument exceeds the bound")
        if batch and size + cost > ATTRIBUTE_ARG_BYTES:
            yield ATTRIBUTES_ARGV + tuple(batch)
            batch, size = [], 0
        batch.append(name)
        size += cost
    if batch:
        yield ATTRIBUTES_ARGV + tuple(batch)


def refuse_filters(raw, count):
    records = raw.split(b"\0")
    if records[-1:] != [b""] or len(records) != count * 3 + 1:
        raise UnsafeInspection("incomplete attribute output")
    for offset in range(0, len(records) - 1, 3):
        if records[offset + 1] != b"filter" or records[offset + 2] not in (b"unspecified", b"unset"):
            # A named driver (even missing) and a boolean filter are conservatively refused.
            raise UnsafeInspection("filter attributes cannot establish an exact unfiltered revision")
