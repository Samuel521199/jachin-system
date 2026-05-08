"""
JSONL：audit_trail（自治测试流水）与 training_data（影子示教）。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def _nearest_semantic(
    semantic_map: dict[str, tuple[float, float]],
    x: float,
    y: float,
    *,
    max_dist: float = 120.0,
) -> tuple[str | None, float]:
    """将像素点匹配到最近的语义元素（欧氏距离）。"""
    best: str | None = None
    best_d = float("inf")
    for name, (sx, sy) in semantic_map.items():
        d = ((sx - x) ** 2 + (sy - y) ** 2) ** 0.5
        if d < best_d:
            best_d = d
            best = name
    if best is None or best_d > max_dist:
        return None, best_d
    return best, best_d


class ShadowLogger:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.data_dir / "audit_trail.jsonl"
        self.training_path = self.data_dir / "training_data.jsonl"
        self._lock = threading.Lock()

    def append_audit(self, record: dict[str, Any]) -> None:
        line = dict(record)
        line.setdefault("ts", time.time())
        line.setdefault("id", str(uuid.uuid4()))
        self._append_jsonl(self.audit_path, line)

    def append_training(
        self,
        *,
        structured_state: dict[str, Any],
        semantic_action: str | None,
        client_xy: dict[str, float],
        meta: dict[str, Any] | None = None,
    ) -> None:
        line = {
            "ts": time.time(),
            "id": str(uuid.uuid4()),
            "structured_state": structured_state,
            "semantic_action": semantic_action,
            "client_xy": client_xy,
            "meta": meta or {},
        }
        self._append_jsonl(self.training_path, line)

    def read_audit_text(self) -> str:
        if not self.audit_path.is_file():
            return ""
        return self.audit_path.read_text(encoding="utf-8", errors="replace")

    def reset_cycle_logs(self) -> None:
        """新测试周期：截断两份日志（按需也可用 run_id 分文件，此处保持脚手架简单）。"""
        with self._lock:
            for p in (self.audit_path, self.training_path):
                if p.exists():
                    p.unlink()

    def _append_jsonl(self, path: Path, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj, ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(payload + "\n")


def resolve_click_to_semantic(
    semantic_map: dict[str, tuple[float, float]],
    x: float,
    y: float,
) -> tuple[str | None, float]:
    return _nearest_semantic(semantic_map, x, y)
