"""Production media-inspection adapter for ffprobe (Phase 2C-2). Local files only; see `adapter.py`."""

from .adapter import ADAPTER_ID, CAPABILITIES, DESCRIPTOR, INSPECT, FfprobeAdapter

__all__ = ["ADAPTER_ID", "CAPABILITIES", "DESCRIPTOR", "INSPECT", "FfprobeAdapter"]
