"""
Lark 会话持久化 — 供 ws_server、im_channels 共享

Lark 长连接/WebSocket 接入时，按 chat_id 持久化对话历史，
否则多轮招聘流程（收集信息 → 输出 JD → 同意发布）无法跨消息追溯。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_JACHIN_ROOT = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
_LARK_SESSIONS_PATH = _JACHIN_ROOT / "l3_lark_sessions.json"
_MAX_SESSION_MESSAGES = 48


def load_lark_session(chat_id: str) -> list[dict[str, Any]]:
    """从文件加载 Lark chat 的对话历史"""
    if not chat_id or not str(chat_id).strip():
        return []
    chat_id = str(chat_id).strip()
    try:
        if not _LARK_SESSIONS_PATH.exists():
            return []
        data = json.loads(_LARK_SESSIONS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        entry = data.get(chat_id)
        if not isinstance(entry, dict):
            return []
        msgs = entry.get("messages", [])
        if isinstance(msgs, list):
            return msgs[-_MAX_SESSION_MESSAGES:]
    except Exception as e:
        logger.debug("[Lark Session] 加载失败 chat_id=%s: %s", chat_id[:20], e)
    return []


def save_lark_session(chat_id: str, messages: list[dict[str, Any]]) -> None:
    """将会话历史持久化到文件"""
    if not chat_id or not str(chat_id).strip():
        return
    chat_id = str(chat_id).strip()
    try:
        _LARK_SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if _LARK_SESSIONS_PATH.exists():
            try:
                data = json.loads(_LARK_SESSIONS_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        recent = messages[-_MAX_SESSION_MESSAGES:] if len(messages) > _MAX_SESSION_MESSAGES else messages
        data[chat_id] = {"messages": recent, "updated_at": time.time()}
        _LARK_SESSIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("[Lark Session] 保存失败 chat_id=%s: %s", chat_id[:20], e)
