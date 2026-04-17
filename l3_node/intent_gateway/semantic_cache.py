"""
§10.4 语义缓存：只存「意图骨架」相关可序列化快照，不存实体 ID。
当前实现：进程内 TTL LRU，键 = tenant + registry +（可选 session 隔离）+ hash(classification_text)。
classification_text 须为纯净意图面（不含会话摘要），见 bundle.rebuild_classification_text。
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Optional


class _Entry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: dict[str, Any], expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at


class SemanticIntentShapeCache:
    def __init__(self, *, max_entries: int = 2048, ttl_seconds: float = 3600.0) -> None:
        self._max = max(16, max_entries)
        self._ttl = max(30.0, ttl_seconds)
        self._data: dict[str, _Entry] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def _evict_if_needed(self) -> None:
        while len(self._order) > self._max:
            k = self._order.pop(0)
            self._data.pop(k, None)

    def _touch(self, key: str) -> None:
        try:
            self._order.remove(key)
        except ValueError:
            pass
        self._order.append(key)

    @staticmethod
    def make_key(
        tenant_id: str,
        classification_text: str,
        registry_version: str = "rv0",
        *,
        session_id: str = "",
    ) -> str:
        sid = (session_id or "").strip()
        if sid:
            payload = f"{tenant_id}\n{registry_version}\n{sid}\n{classification_text}"
        else:
            payload = f"{tenant_id}\n{registry_version}\n{classification_text}"
        return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            ent = self._data.get(key)
            if ent is None:
                return None
            if ent.expires_at < now:
                del self._data[key]
                try:
                    self._order.remove(key)
                except ValueError:
                    pass
                return None
            self._touch(key)
            return dict(ent.value)

    def set(self, key: str, shape: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            self._data[key] = _Entry(shape, now + self._ttl)
            self._touch(key)
            self._evict_if_needed()


_CACHE: SemanticIntentShapeCache | None = None


def get_semantic_cache() -> SemanticIntentShapeCache:
    global _CACHE
    if _CACHE is None:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        cfg = get_intent_gateway_config()
        _CACHE = SemanticIntentShapeCache(
            max_entries=int(cfg.get("semantic_cache_max_entries", 2048)),
            ttl_seconds=float(cfg.get("semantic_cache_ttl_seconds", 3600)),
        )
    return _CACHE
