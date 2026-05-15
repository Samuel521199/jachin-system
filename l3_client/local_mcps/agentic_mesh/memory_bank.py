"""
群体免疫记忆库：缓存错误签名与已成功过的微修复策略，减少重复专家 / 大模型调用。

当前实现为进程内、线程安全的轻量 KV；后续可换 Redis / 向量库而不改 Router API。
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HealRecord:
    expert: str
    action: str
    ok: bool
    ts: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)


class MeshMemoryBank:
    """错误签名 → 最近一条愈合记录（可扩展为多条时间序列）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[str, HealRecord] = {}

    @staticmethod
    def normalize_key(error_text: str, max_len: int = 512) -> str:
        line = (error_text or "").strip().splitlines()[0][:max_len].lower()
        digest = hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()[:24]
        return digest

    def last_record(self, error_text: str) -> HealRecord | None:
        key = self.normalize_key(error_text)
        with self._lock:
            return self._by_key.get(key)

    def remember(self, error_text: str, expert: str, action: str, ok: bool, **meta: Any) -> None:
        key = self.normalize_key(error_text)
        rec = HealRecord(expert=expert, action=action, ok=ok, meta=dict(meta))
        with self._lock:
            self._by_key[key] = rec

    def prefer_action(self, error_text: str) -> str | None:
        """若最近一次同类错误由某 action 自愈成功，则优先复用该动作名。"""
        rec = self.last_record(error_text)
        if rec and rec.ok:
            return rec.action
        return None


_bank: MeshMemoryBank | None = None


def get_memory_bank() -> MeshMemoryBank:
    global _bank
    if _bank is None:
        _bank = MeshMemoryBank()
    return _bank
