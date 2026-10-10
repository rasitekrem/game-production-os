"""Fixed alpha.29 Windows manifest/2 consumer; caller text never names a program."""
import hashlib
from pathlib import Path
from .. import paths as paths
from .. import paths_win32 as fs
from ..unity import build as common, build_win32 as build
from .resolver import UNITY_ADAPTER_ID


def resolve(root, build_id):
    if not isinstance(build_id, str) or not common.BUILD_ID.fullmatch(build_id):
        raise ValueError('a canonical completed build_id is required')
    root = Path(root)
    workspace = paths.runtime_dir(root, 'tool-output', UNITY_ADAPTER_ID, build_id[len('build-'):])
    raw = build.read_file(workspace/common.MANIFEST_NAME, build.MAX_JSON_BYTES)
    manifest, problem = build.revalidate(workspace)
    if problem:
        raise ValueError(problem)
    if build.read_file(workspace/common.MANIFEST_NAME, build.MAX_JSON_BYTES) != raw:
        raise ValueError('manifest changed during revalidation')
    if manifest['payload']['kind'] != 'WINDOWS_STANDALONE_PLAYER':
        raise ValueError('not the qualified Windows payload')
    executable = workspace/'payload'/'Player'/'Player.exe'
    entry = next(row for row in manifest['payload']['inventory'] if row['path'] == 'Player.exe')
    return {'build_id': build_id, 'build_revision': manifest['build_revision'],
            'build_revision_source': manifest['build_revision_source'],
            'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'tree_digest': manifest['payload']['tree_digest'],
            'executable': str(executable), 'executable_identity': entry['identity'], 'workspace': str(workspace)}


def revalidates(root, session):
    try:
        current = resolve(root, session['build_id'])
        if any(current[k] != session[k] for k in current):
            return 'DRIFT', 'build content, identity or manifest differs since launch'
        return 'VALID', None
    except (OSError, ValueError, fs.PathRefused) as exc:
        return 'DRIFT', str(exc)
