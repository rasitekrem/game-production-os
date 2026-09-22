"""TEST_ONLY synthetic reference adapter for the tool adapter foundation.

Nothing here is a production tool. See `adapter.py`.
"""

from .adapter import ADAPTER_ID, CAPABILITIES, DESCRIPTOR, SyntheticAdapter, register_into

__all__ = ["ADAPTER_ID", "CAPABILITIES", "DESCRIPTOR", "SyntheticAdapter", "register_into"]
