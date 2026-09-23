"""Production derived-media adapter for FFmpeg (Phase 2C-2). Local files only; see `adapter.py`."""

from .adapter import (ADAPTER_ID, CAPABILITIES, DESCRIPTOR, EXTRACT_AUDIO, EXTRACT_CLIP, EXTRACT_FRAME,
                      FfmpegAdapter)

__all__ = ["ADAPTER_ID", "CAPABILITIES", "DESCRIPTOR", "EXTRACT_AUDIO", "EXTRACT_CLIP", "EXTRACT_FRAME",
           "FfmpegAdapter"]
