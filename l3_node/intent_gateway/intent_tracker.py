"""
与 L2/RBAC 同层的独立 Tracker：结构化事件落盘（§9 M4/M5 可观测性），默认轻量 JSONL。
路径：~/.jachin/data/intent_tracker.jsonl
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _tracker_path() -> Path:
    try:
        from l3_node.jachin_config import get_jachin_root

        root = get_jachin_root()
    except ImportError:
        root = Path.home() / ".jachin"
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "intent_tracker.jsonl"


def emit_intent_tracker_event(kind: str, payload: dict[str, Any] | None = None) -> None:
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        if not bool(get_intent_gateway_config().get("intent_tracker_jsonl_enabled", True)):
            return
    except Exception:
        return
    row: dict[str, Any] = {"ts": time.time(), "kind": str(kind or "unknown")}
    if payload:
        row.update(payload)
    try:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        p = _tracker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.open("a", encoding="utf-8").write(line)
    except OSError as e:
        logger.debug("[IntentTracker] 写入失败: %s", e)
