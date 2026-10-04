"""Maintainer/test tooling (alpha.22): build the GPOS Player Helper release bundle reproducibly, or check that the
committed release is exactly what the committed source builds to. No GPOS capability reaches this file: production
never compiles, signs or downloads anything.

    python3 tests/build_player_helper.py --write   rebuild gpos/tools/player/helper_release/ from helper_src/
    python3 tests/build_player_helper.py --check   rebuild into a temporary directory and compare with the release

The build is deterministic with the recorded toolchain: whole-module compilation of the sources by relative name into
one object per architecture, linking with ZERO_AR_DATE=1 and no debug map (-Xlinker -S), `lipo` into one universal
executable, then an ad-hoc signature with the fixed identifier. A different Swift toolchain may produce different
bytes; --check then reports TOOLCHAIN_MISMATCH instead of a false difference.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYER = ROOT / "gpos" / "tools" / "player"
SOURCE_DIR = PLAYER / "helper_src"
RELEASE_DIR = PLAYER / "helper_release"
SOURCES = ("main.swift", "Common.swift", "Supervise.swift", "Capture.swift")
PLIST = "Info.plist"
BUNDLE = "GposPlayerHelper.app"
EXECUTABLE = "GposPlayerHelper"
IDENTIFIER = "com.gpos.player-helper"
VERSION = "1.0.0"
ARCHS = ("arm64", "x86_64")
MINIMUM = "14.0"
BUNDLE_ALGORITHM = "gpos.player-helper.bundle/1"
SOURCE_ALGORITHM = "gpos.player-helper.source/1"


def run(argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kw)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def toolchain():
    swiftc = run(["swiftc", "--version"]).stdout.splitlines()[0].strip()
    sdk = run(["xcrun", "--show-sdk-version"]).stdout.strip()
    return {"swiftc": swiftc, "sdk": sdk, "targets": [f"{a}-apple-macos{MINIMUM}" for a in ARCHS],
            "minimum_macos": MINIMUM,
            "commands": ["swiftc -O -wmo -c -target <arch>-apple-macos14.0 -module-name GposPlayerHelper -o <arch>.o "
                         + " ".join(SOURCES),
                         "ZERO_AR_DATE=1 swiftc -target <arch>-apple-macos14.0 -Xlinker -S -o <arch> <arch>.o",
                         "lipo -create arm64 x86_64 -output GposPlayerHelper",
                         f"codesign -s - --force -i {IDENTIFIER} GposPlayerHelper.app"]}


def build(out_dir):
    """Build the bundle into out_dir/GposPlayerHelper.app (which must not exist yet)."""
    out_dir = Path(out_dir)
    bundle = out_dir / BUNDLE
    work = Path(tempfile.mkdtemp(prefix="gpos-helper-build-"))
    try:
        for arch in ARCHS:
            run(["swiftc", "-O", "-wmo", "-c", "-target", f"{arch}-apple-macos{MINIMUM}", "-module-name", EXECUTABLE,
                 "-o", str(work / f"{arch}.o"), *SOURCES], cwd=SOURCE_DIR)
            run(["swiftc", "-target", f"{arch}-apple-macos{MINIMUM}", "-Xlinker", "-S", "-o", str(work / arch),
                 str(work / f"{arch}.o")], cwd=work, env={**os.environ, "ZERO_AR_DATE": "1"})
        (bundle / "Contents" / "MacOS").mkdir(parents=True)
        run(["lipo", "-create", *[str(work / a) for a in ARCHS], "-output", str(bundle / "Contents/MacOS" / EXECUTABLE)])
        shutil.copyfile(SOURCE_DIR / PLIST, bundle / "Contents" / PLIST)
        os.chmod(bundle / "Contents" / PLIST, 0o644)
        os.chmod(bundle / "Contents/MacOS" / EXECUTABLE, 0o755)
        run(["codesign", "-s", "-", "--force", "-i", IDENTIFIER, str(bundle)])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return bundle


def cdhashes(bundle):
    out = {}
    for arch in ARCHS:
        text = run(["codesign", "-dvvv", "--arch", arch, str(bundle)]).stderr
        match = re.search(r"^CDHash=([0-9a-f]{40})$", text, re.M)
        out[arch] = match.group(1)
    return out


def bundle_files(bundle):
    files, dirs = [], []
    for path in sorted(Path(bundle).rglob("*")):
        rel = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            raise SystemExit(f"unexpected link in the bundle: {rel}")
        if path.is_dir():
            dirs.append(rel)
        else:
            files.append({"path": rel, "size": path.stat().st_size, "mode": format(path.stat().st_mode & 0o777, "04o"),
                          "sha256": sha256(path)})
    return files, dirs


def digest(algorithm, lines):
    return hashlib.sha256((algorithm + "\n" + "".join(line + "\n" for line in sorted(lines))).encode("utf-8")).hexdigest()


def manifest(bundle):
    files, dirs = bundle_files(bundle)
    sources = [{"path": f"helper_src/{name}", "sha256": sha256(SOURCE_DIR / name)} for name in SOURCES + (PLIST,)]
    return {
        "schema": "gpos.player.helper-release/1",
        "helper_version": VERSION,
        "bundle_name": BUNDLE,
        "bundle_identifier": IDENTIFIER,
        "executable": f"Contents/MacOS/{EXECUTABLE}",
        "modes": ["supervise", "shot", "video"],
        "signature": {"kind": "ADHOC", "identifier": IDENTIFIER, "cdhashes": cdhashes(bundle)},
        "directories": dirs,
        "files": files,
        "bundle_digest_algorithm": BUNDLE_ALGORITHM,
        "bundle_digest": digest(BUNDLE_ALGORITHM, [f"{f['path']}\t{f['sha256']}\t{f['size']}\t{f['mode']}" for f in files]),
        "source": {"files": sources, "digest_algorithm": SOURCE_ALGORITHM,
                   "digest": digest(SOURCE_ALGORITHM, [f"{s['path']}\t{s['sha256']}" for s in sources])},
        "toolchain": toolchain(),
    }


def write_release():
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    RELEASE_DIR.mkdir(parents=True)
    bundle = build(RELEASE_DIR)
    (RELEASE_DIR / "manifest.json").write_text(json.dumps(manifest(bundle), indent=1, sort_keys=True) + "\n")
    return json.loads((RELEASE_DIR / "manifest.json").read_text())


def check_release():
    """(status, detail): EXACT, DIFFERENT, or TOOLCHAIN_MISMATCH when this machine's toolchain is not the recorded one."""
    recorded = json.loads((RELEASE_DIR / "manifest.json").read_text())
    here = toolchain()
    if (here["swiftc"], here["sdk"]) != (recorded["toolchain"]["swiftc"], recorded["toolchain"]["sdk"]):
        return "TOOLCHAIN_MISMATCH", f"{here['swiftc']} / SDK {here['sdk']}"
    with tempfile.TemporaryDirectory(prefix="gpos-helper-check-") as tmp:
        rebuilt = manifest(build(tmp))
    keys = ("files", "directories", "bundle_digest", "signature", "source")
    different = [k for k in keys if rebuilt[k] != recorded[k]]
    return ("EXACT", "") if not different else ("DIFFERENT", ", ".join(different))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.write:
        m = write_release()
        print(json.dumps({k: m[k] for k in ("helper_version", "bundle_digest", "signature")}, indent=1))
        print("source digest", m["source"]["digest"])
    else:
        status, detail = check_release()
        print(status, detail)
        sys.exit(0 if status in ("EXACT", "TOOLCHAIN_MISMATCH") else 1)
