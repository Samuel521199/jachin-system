"""
P2-9：记忆检索强化分（侧车 JSON，按 memory id 累加，混合检索时加权）。

不强制迁移 LanceDB schema；新写入可选带 reinforce_score 列（与旧表兼容 .get）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PATH = Path.home() / ".jachin" / "memory" / "memory_reinforcement.json"
_lock = threading.Lock()


def _load_raw() -> dict[str, Any]:
    if not _PATH.exists():
        return {"scores": {}, "updated_at": 0.0}
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"scores": {}, "updated_at": 0.0}
        sc = data.get("scores")
        if not isinstance(sc, dict):
            data["scores"] = {}
        return data
    except Exception as e:
        logger.warning("[P2-9] 读取 reinforcement 失败: %s", e)
        return {"scores": {}, "updated_at": 0.0}


def _save_raw(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.time()
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def get_reinforce_boost(memory_id: str) -> float:
    mid = str(memory_id or "").strip()
    if not mid or mid == "init":
        return 0.0
    with _lock:
        data = _load_raw()
        scores = data.get("scores", {})
        try:
            return float(scores.get(mid, 0.0))
        except (TypeError, ValueError):
            return 0.0


def add_reinforce_delta(
    memory_id: str,
    delta: float,
    *,
    max_per_id: float = 8.0,
) -> float:
    """增加某条记忆的强化分，返回新分。"""
    mid = str(memory_id or "").strip()
    if not mid or mid == "init":
        return 0.0
    with _lock:
        data = _load_raw()
        scores: dict[str, Any] = dict(data.get("scores") or {})
        try:
            cur = float(scores.get(mid, 0.0))
        except (TypeError, ValueError):
            cur = 0.0
        try:
            d = float(delta)
        except (TypeError, ValueError):
            d = 0.0
        new_v = max(0.0, min(max_per_id, cur + d))
        scores[mid] = new_v
        data["scores"] = scores
        _save_raw(data)
    return new_v


def hybrid_reinforce_bonus(
    memory_id: str,
    row_reinforce: float | None,
    *,
    weight: float,
    max_boost: float,
) -> float:
    """
    返回加到 hybrid score 上的增量（已含 weight，且随 boost 饱和）。
    sidecar + row reinforce_score are merged locally; Memory Growth owns final ranking.
    """
    try:
        side = get_reinforce_boost(memory_id)
    except Exception:
        side = 0.0
    try:
        row = float(row_reinforce) if row_reinforce is not None else 0.0
    except (TypeError, ValueError):
        row = 0.0
    raw = min(max_boost, side + row)
    if raw <= 0 or weight <= 0:
        return 0.0
    import math

    return weight * (1.0 - math.exp(-raw))
