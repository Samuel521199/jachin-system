"""
§6.3 负反馈落盘（最小闭环）：仅结构化字段，默认不写原始全文。
路径：~/.jachin/data/intent_gateway_feedback.jsonl
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _feedback_path() -> Path:
    try:
        from l3_node.jachin_config import get_jachin_root

        root = get_jachin_root()
    except ImportError:
        root = Path.home() / ".jachin"
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "intent_gateway_feedback.jsonl"


def emit_intent_gateway_signal(event: dict[str, Any]) -> None:
    """追加一行 JSON（失败仅打 debug）。"""
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        if not bool(get_intent_gateway_config().get("flywheel_feedback_enabled", False)):
            return
    except Exception:
        return
    row = {
        "ts": time.time(),
        **event,
    }
    try:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        p = _feedback_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.open("a", encoding="utf-8").write(line)
    except OSError as e:
        logger.debug("[IntentGateway][Flywheel] 写入失败: %s", e)


def hash_utterance(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:24]
