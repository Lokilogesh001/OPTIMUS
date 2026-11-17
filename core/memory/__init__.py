"""OPTIMUS Memory Subsystem and Shared Context Bus.

Provides:
- Multi-category persistent memory (WORKING, EPISODIC, SEMANTIC, PROCEDURAL).
- Scoped, role-filtered SharedContextBus connecting orchestrator and agents.
- Adaptive ContextWindowManager with token budgeting and verified artifact preservation.
- Full backward compatibility with Mark-LVII memory_manager.
"""

from __future__ import annotations

from core.memory.context_bus import (
    SharedContextBus,
)
from core.memory.manager import (
    OptimusMemoryManager,
    ensure,
    get_memory_manager,
)
from core.memory.store import (
    MemoryStore,
)
from core.memory.types import (
    ContextEnvelope,
    MemoryCategory,
    MemoryEntry,
    new_memory_id,
)
from core.memory.window_manager import (
    ContextWindowManager,
    compact_text,
    estimate_chars,
    estimate_tokens,
)

__all__ = [
    # Types
    "MemoryCategory",
    "MemoryEntry",
    "ContextEnvelope",
    "new_memory_id",
    # Store
    "MemoryStore",
    # Bus
    "SharedContextBus",
    # Window Manager
    "ContextWindowManager",
    "estimate_tokens",
    "estimate_chars",
    "compact_text",
    # Manager & Singletons
    "OptimusMemoryManager",
    "ensure",
    "get_memory_manager",
]
