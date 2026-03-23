"""
P2-8：意图 → 技能调用结果轻量统计（本地 JSONL，供后续分析与调参）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATS_PATH = Path.home() / ".jachin" / "cache" / "intent_skill_stats.jsonl"


def _ensure_parent() -> None:
    _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)


def _intent_hash(intent: str) -> str:
    n = (intent or "").strip().lower()
    n = " ".join(n.split())[:500]
    return hashlib.sha256(n.encode("utf-8")).hexdigest()[:16]


def _failure_heuristic(observation: str) -> bool:
    o = (observation or "").strip()
    if not o:
        return True
    bad = (
        "[权限拒绝",
        "[执行失败",
        "[系统异常",
        "[MCP]",
        "[未知工具",
        "[Wasm 执行失败",
        "[记忆检索失败",
        "[协同请求失败",
        "[子任务失败",
    )
    return any(o.startswith(p) for p in bad)


def record_tool_outcome(
    user_intent: str,
    skill_id: str,
    observation: str,
    *,
    success_override: bool | None = None,
) -> None:
    max_bytes = 4 * 1024 * 1024
    try:
        from l3_node.intelligence_p2 import get_intel_p2_config, intent_stats_enabled

        if not intent_stats_enabled():
            return
        cfg = get_intel_p2_config()
        max_bytes = int(cfg.get("intent_stats_max_file_mb", 4) or 4) * 1024 * 1024
    except ImportError:
        return
    except Exception:
        pass

    skill_id = (skill_id or "").strip() or "unknown"
    ok = success_override if success_override is not None else (not _failure_heuristic(observation))
    evt = {
        "ts": time.time(),
        "intent_hash": _intent_hash(user_intent),
        "skill_id": skill_id.lower(),
        "success": ok,
        "obs_len": len(observation or ""),
    }
    _ensure_parent()
    try:
        line = json.dumps(evt, ensure_ascii=False) + "\n"
        with open(_STATS_PATH, "a", encoding="utf-8") as f:
            f.write(line)
        if _STATS_PATH.stat().st_size > max_bytes:
            _trim_stats_file(max_bytes // 2)
    except Exception as e:
        logger.debug("[P2-8] 写入统计失败: %s", e)


def _trim_stats_file(target_bytes: int) -> None:
    try:
        raw = _STATS_PATH.read_text(encoding="utf-8")
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        # 从尾部保留，直到约 target_bytes
        keep: list[str] = []
        size = 0
        for ln in reversed(lines):
            size += len(ln.encode("utf-8")) + 1
            keep.append(ln)
            if size >= target_bytes:
                break
        keep.reverse()
        _STATS_PATH.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    except Exception as e:
        logger.debug("[P2-8] trim 失败: %s", e)


def aggregate_recent(limit_lines: int = 5000) -> dict[str, Any]:
    """简易聚合：按 skill_id 统计 success 率（供脚本或未来 Admin）。"""
    if not _STATS_PATH.exists():
        return {}
    try:
        lines = _STATS_PATH.read_text(encoding="utf-8").splitlines()[-limit_lines:]
    except Exception:
        return {}
    by_skill: dict[str, dict[str, int]] = {}
    for ln in lines:
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        sid = str(o.get("skill_id", ""))
        if sid not in by_skill:
            by_skill[sid] = {"ok": 0, "fail": 0}
        if o.get("success"):
            by_skill[sid]["ok"] += 1
        else:
            by_skill[sid]["fail"] += 1
    return {"by_skill": by_skill, "lines": len(lines)}
