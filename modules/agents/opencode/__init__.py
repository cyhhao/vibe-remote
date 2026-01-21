"""OpenCode agent backend.

This package contains the refactored OpenCode agent implementation split into
smaller modules.

Public entrypoints:
- OpenCodeAgent: the agent backend used by the rest of vibe-remote
- OpenCodeServerManager: manages the shared OpenCode server process
"""

from .agent import OpenCodeAgent
from .server import OpenCodeServerManager
from .utils import build_reasoning_effort_options

__all__ = [
    "OpenCodeAgent",
    "OpenCodeServerManager",
    "build_reasoning_effort_options",
]
