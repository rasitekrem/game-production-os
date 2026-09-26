#!/usr/bin/env python3
"""TEST-ONLY maintenance tool: regenerate the fixed `.meta` files and the release manifest of the GPOS live bridge.

    python3 tests/generate_live_bridge_manifest.py [--check]

Every `.meta` file is written in the exact minimal form Unity itself keeps unchanged, with a deterministic GUID
(md5 of "com.gpos.live-bridge:<relative path>"), so the package bytes, and therefore its digest, never depend on
the machine that generated them. The manifest lists every file of the package with its size and SHA-256 and the
package digest. `--check` changes nothing and fails when the committed files differ from what would be written;
tests/test_unity_live.py runs it, so a bridge change cannot ship without a regenerated, reviewed manifest.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gpos.tools.unity import bridge_install as bi  # noqa: E402

IMPORTER_BLOCK = "  externalObjects: {}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"


def guid(rel):
    return hashlib.md5(f"{bi.PACKAGE_ID}:{rel}".encode("utf-8")).hexdigest()


def meta_text(rel, is_dir):
    head = f"fileFormatVersion: 2\nguid: {guid(rel)}"
    if is_dir:
        return head + "\nfolderAsset: yes\nDefaultImporter:\n" + IMPORTER_BLOCK
    if rel.endswith("package.json"):
        return head + "\nPackageManifestImporter:\n" + IMPORTER_BLOCK
    if rel.endswith(".asmdef"):
        return head + "\nAssemblyDefinitionImporter:\n" + IMPORTER_BLOCK
    return head  # a script: exactly the minimal form Unity 6.5 keeps unchanged


def expected_metas(source):
    out = {}
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source).as_posix()
        if rel.endswith(".meta"):
            continue
        out[rel + ".meta"] = meta_text(rel, path.is_dir())
    return out


def build(source=bi.SOURCE):
    metas = expected_metas(source)
    stale = [p for p in source.rglob("*.meta") if p.relative_to(source).as_posix() not in metas]
    return metas, stale


def manifest_for(source):
    entries, problems = bi.tree(source)
    if problems:
        raise SystemExit(f"the bridge package holds something that is not a regular file: {problems}")
    entries = sorted(entries, key=lambda e: e["path"])
    return {"schema": bi.MANIFEST_SCHEMA, "package_id": bi.PACKAGE_ID, "bridge_version": bi.BRIDGE_VERSION,
            "protocol": bi.PROTOCOL, "files": entries, "package_digest": bi.digest(entries)}


def main(argv):
    check = "--check" in argv
    metas, stale = build()
    problems = []
    for rel, text in metas.items():
        path = bi.SOURCE / rel
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            if check:
                problems.append(f"{rel}: not the generated .meta")
            else:
                path.write_text(text, encoding="utf-8")
    for p in stale:
        problems.append(f"{p.relative_to(bi.SOURCE)}: a .meta file for nothing")
    manifest = json.dumps(manifest_for(bi.SOURCE), indent=2, sort_keys=True) + "\n"
    current = bi.MANIFEST.read_text(encoding="utf-8") if bi.MANIFEST.exists() else None
    if current != manifest:
        if check:
            problems.append("manifest.json: not the manifest of the committed package")
        else:
            bi.MANIFEST.write_text(manifest, encoding="utf-8")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"bridge manifest {'verified' if check else 'written'}: {json.loads(manifest)['package_digest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
