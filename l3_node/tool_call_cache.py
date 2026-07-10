"""
P1：MCP / 只读类工具调用短期缓存（tool_id + 规范化参数 → 结果，TTL 可配）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from l3_node.intelligence_p1 import get_intel_p1_config
from l3_node.jachin_config import get_jachin_root

logger = logging.getLogger(__name__)

_CACHE_FILE = get_jachin_root() / "cache" / "tool_invoke_cache.json"

# 未配置 allowlist 时的默认：仅明显只读
_DEFAULT_CACHE_ALLOWLIST = frozenset(
    {
        "mcp:read_file",
        "read_file",
        "core:fs_read",
        "recall_memory",
    }
)


def _normalize_work_order_input(work_order_input: str) -> str:
    s = (work_order_input or "").strip()
    if not s:
        return ""
    if s.startswith("{") and "}" in s:
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return json.dumps(obj, sort_keys=True, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return s


def _cache_key(tool_id: str, work_order_input: str) -> str:
    tid = (tool_id or "").strip().lower()
    norm = _normalize_work_order_input(work_order_input)
    raw = f"{tid}\n{norm}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _should_cache_tool(tool_id: str) -> bool:
    cfg = get_intel_p1_config()
    if cfg.get("tool_call_cache_enabled") is False:
        return False
    tid = (tool_id or "").strip().lower()
    raw = tid.split(":", 1)[-1] if ":" in tid else tid
    allow = cfg.get("tool_call_cache_allowlist")
    if isinstance(allow, list) and allow:
        allowed = {str(x).strip().lower() for x in allow if str(x).strip()}
        return tid in allowed or raw in allowed
    return tid in _DEFAULT_CACHE_ALLOWLIST or raw in _DEFAULT_CACHE_ALLOWLIST


def _is_bad_result(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    bad_prefixes = (
        "[权限拒绝",
        "[系统异常",
        "[MCP]",
        "[执行失败",
        "[未知工具",
        "[Wasm 执行失败",
        "[记忆检索失败",
    )
    return any(t.startswith(p) for p in bad_prefixes)


def _load_store() -> dict[str, Any]:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug("[P1] 工具缓存文件损坏，重置: %s", e)
        return {}


def _prune_and_save(store: dict[str, Any]) -> None:
    now = time.time()
    ttl = get_intel_p1_config().get("tool_call_cache_ttl_seconds")
    try:
        ttl_sec = float(ttl) if ttl is not None else 3600.0
    except (TypeError, ValueError):
        ttl_sec = 3600.0
    if ttl_sec <= 0:
        ttl_sec = 3600.0
    entries = store.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    new_entries: dict[str, Any] = {}
    for k, v in entries.items():
        if not isinstance(v, dict):
            continue
        exp = v.get("expires_at")
        try:
            if float(exp) > now:
                new_entries[k] = v
        except (TypeError, ValueError):
            continue
    store["entries"] = new_entries
    try:
        _CACHE_FILE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("[P1] 写入工具缓存失败: %s", e)


def try_get_cached(tool_id: str, work_order_input: str) -> str | None:
    if not _should_cache_tool(tool_id):
        return None
    key = _cache_key(tool_id, work_order_input)
    store = _load_store()
    entries = store.get("entries")
    if not isinstance(entries, dict):
        return None
    row = entries.get(key)
    if not isinstance(row, dict):
        return None
    try:
        if float(row.get("expires_at", 0)) <= time.time():
            return None
    except (TypeError, ValueError):
        return None
    result = row.get("result")
    if not isinstance(result, str):
        return None
    logger.debug("[P1] 工具缓存命中 tool=%s", tool_id)
    return result


def store_if_cacheable(tool_id: str, work_order_input: str, result: str) -> str:
    """若可缓存且结果健康则写入；始终返回 result。"""
    if not _should_cache_tool(tool_id):
        return result
    if _is_bad_result(result):
        return result
    ttl = get_intel_p1_config().get("tool_call_cache_ttl_seconds")
    try:
        ttl_sec = float(ttl) if ttl is not None else 3600.0
    except (TypeError, ValueError):
        ttl_sec = 3600.0
    if ttl_sec <= 0:
        ttl_sec = 3600.0
    key = _cache_key(tool_id, work_order_input)
    store = _load_store()
    entries = store.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    entries[key] = {
        "tool_id": tool_id,
        "expires_at": time.time() + ttl_sec,
        "result": result,
    }
    # 简单上限，避免文件无限涨
    if len(entries) > 500:
        # 删掉最早过期的 half
        sorted_keys = sorted(
            entries.keys(),
            key=lambda k: float(entries[k].get("expires_at", 0)) if isinstance(entries.get(k), dict) else 0,
        )
        for drop in sorted_keys[:250]:
            entries.pop(drop, None)
    store["entries"] = entries
    _prune_and_save(store)
    return result
