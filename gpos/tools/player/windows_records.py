"""Bounded Windows-only runtime observations; SESSION remains the lifecycle authority."""
import json
import os
from pathlib import Path
from .. import paths_win32 as fs
from ..unity import build_win32 as build

MAX_BYTES = 8192


def read(path):
    path = Path(path)
    why = fs.unsafe_reason([str(path.parent)], str(path), must_exist=False)
    if why:
        raise ValueError('untrusted runtime path: '+why)
    # The strict build reader intentionally wraps missing files as a payload refusal.
    # Polling records need an explicit, safely checked ABSENT state before using it.
    if not path.exists():
        raise FileNotFoundError(str(path))
    value = build.read_json(path, MAX_BYTES)
    if not isinstance(value, dict):
        raise ValueError('runtime record is not an object')
    return value


def write_once(path, value):
    path = Path(path)
    data = json.dumps(value, sort_keys=True, allow_nan=False).encode('utf-8')
    if len(data) > MAX_BYTES:
        raise ValueError('runtime record exceeds bound')
    pins = fs.pin_chain(path.parent)
    temporary = path.with_name(path.name + '.writing')
    try:
        fs.create_new_file(temporary, data)
        # Windows rename refuses an existing destination; ancestry stays pinned throughout.
        os.rename(temporary, path)
    finally:
        fs.close_all(pins)


def control(path, schema, session):
    try:
        record = read(path)
    except FileNotFoundError:
        return False
    return record == {'schema': schema, 'session_id': session['session_id'], 'nonce': session['nonce']}


def bound(path, schema, session, keys):
    value = read(path)
    if set(value) != set(keys) | {'schema', 'session_id', 'nonce'}:
        raise ValueError('runtime record fields differ')
    if (value['schema'], value['session_id'], value['nonce']) != (schema, session['session_id'], session['nonce']):
        raise ValueError('runtime record belongs to another session')
    return value
