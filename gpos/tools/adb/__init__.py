"""Production target-device evidence adapter for Android Debug Bridge (Phase 2C-3). Read-only target
commands on one explicitly named local target; see `adapter.py`."""

from .adapter import ADAPTER_ID, CAPABILITIES, DESCRIPTOR, DEVICE_REPORT, MEMINFO, SCREENSHOT, AdbAdapter

__all__ = ["ADAPTER_ID", "CAPABILITIES", "DESCRIPTOR", "DEVICE_REPORT", "MEMINFO", "SCREENSHOT", "AdbAdapter"]
