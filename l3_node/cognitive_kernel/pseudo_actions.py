"""Compatibility pseudo-actions for the legacy text transport.

Pseudo-actions are not business skills. They are compatibility shims that let
old ReAct text output map into Cognitive Kernel concepts until the parser is
fully replaced.
"""

from __future__ import annotations

from typing import Final

RECALL_MEMORY_TOOL_ID: Final[str] = "recall_memory"
REACT_PSEUDO_ACTION_IDS: Final[tuple[str, ...]] = ("recall_memory", "coordinate", "delegate")
