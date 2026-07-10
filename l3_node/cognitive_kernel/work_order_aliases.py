"""WorkOrder aliases that map lightweight intents into kernel concepts."""

from __future__ import annotations

from typing import Final

RECALL_MEMORY_TOOL_ID: Final[str] = "recall_memory"
WORK_ORDER_ALIAS_IDS: Final[tuple[str, ...]] = ("recall_memory", "coordinate", "delegate")
