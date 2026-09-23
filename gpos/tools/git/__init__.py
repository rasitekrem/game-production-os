"""Production version-control adapter for local Git repository provenance (Phase 2C-1).

Read-only and local: see `adapter.py`. Not a command runner, not a mutation tool, not a network client.
"""

from .adapter import (ADAPTER_ID, AUTHORIZED_COMMANDS, CAPABILITIES, DESCRIPTOR, ENVIRONMENT, INSPECT, LIMITATIONS,
                      RESOLVE_PROVENANCE, GitAdapter)

__all__ = ["ADAPTER_ID", "AUTHORIZED_COMMANDS", "CAPABILITIES", "DESCRIPTOR", "ENVIRONMENT", "INSPECT", "LIMITATIONS",
           "RESOLVE_PROVENANCE", "GitAdapter"]
