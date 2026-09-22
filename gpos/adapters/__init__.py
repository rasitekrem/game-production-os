"""GPOS agent adapter layer (Phase 2B).

Compiles canonical GPOS sources plus a project's authority into one agent-independent IR
(`compiler.compile_ir`), renders agent-native instruction bundles from it (`render.render_bundle`,
backends in `backends.py`), and syncs or checks them in a project (`pipeline`). Generated agent
files are disposable projections; canonical authority is GPOS plus Project Locked Authority.
"""

from .backends import BACKENDS
from .compiler import compile_ir
from .pipeline import check, prepare, render, sync

__all__ = ["BACKENDS", "compile_ir", "prepare", "render", "sync", "check"]
