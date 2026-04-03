"""同一次 run 内大块 Observation 内容 hash 去重（替换为短引用）。"""
from __future__ import annotations

import hashlib
import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


def maybe_replace_duplicate_observation(
    meta: dict[str, Any],
    observation: str,
    *,
    min_chars: int = 6000,
    ring_max: int = 24,
) -> str:
    obs = observation or ""
    if len(obs) < min_chars:
        return obs
    h = hashlib.sha256(obs.encode("utf-8", errors="replace")).hexdigest()[:24]
    ring = meta.get("_observation_content_ring")
    if not isinstance(ring, deque):
        ring = deque(maxlen=ring_max)
        meta["_observation_content_ring"] = ring
    # list of (hash, index)
    lst = meta.get("_observation_hash_list")
    if not isinstance(lst, list):
        lst = []
        meta["_observation_hash_list"] = lst
    for i, (prev_h, _) in enumerate(lst):
        if prev_h == h:
            ref = i + 1
            logger.debug("[ObservationDedup] 重复 hash=%s 引用 #%s", h[:12], ref)
            return (
                f"【Observation 去重】与本轮第 {ref} 条大块 Observation 内容相同（sha256[:24]={h}），"
                "请直接引用上文完整块，勿重复全文。\n"
            )
    lst.append((h, len(obs)))
    ring.append(h)
    return obs
