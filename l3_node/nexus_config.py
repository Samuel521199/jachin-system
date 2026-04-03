"""读本机 nexus_config.json（L3 热读，与 core 持久化路径一致）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def get_nexus_config() -> dict[str, Any] | None:
    try:
        from l3_node.jachin_config import get_jachin_root

        p = get_jachin_root() / "nexus_config.json"
    except ImportError:
        p = Path.home() / ".jachin" / "nexus_config.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception as e:
        logger.debug("[nexus_config] 读取失败: %s", e)
        return None
