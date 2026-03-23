"""
阶段 E 完整 MVP：消费 intelligence_events.jsonl，可选写入 reinforce 侧车。

配置：nexus_config.json → intelligence_e
{
  "enabled": true,
  "reinforce_memory_id": "_intel_from_events",
  "anchor_stale_delta": 0.04,
  "plan_gate_delta": 0.02,
  "repeat_intent_delta": 0.02,
  "repeat_followup_delta": 0.025,
  "embedding_repeat_intent_delta": 0.018,
  "embedding_followup_delta": 0.02,
  "embedding_echo_assistant_delta": 0.015,
  "message_skipped_delta": 0.01,
  "dwell_bucket_delta": 0.005,
  "max_events_per_run": 400,
  "min_interval_seconds": 60
}
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from core.intelligence_workspace import get_jachin_home

_ROOT = get_jachin_home()
_NEXUS = _ROOT / "nexus_config.json"
_LAST_STATE = _ROOT / "logs" / "intelligence_e_consumer_state.json"


def _load_intel_e_config() -> dict[str, Any]:
    if not _NEXUS.exists():
        return {}
    try:
        cfg = json.loads(_NEXUS.read_text(encoding="utf-8"))
        sec = cfg.get("intelligence_e")
        return sec if isinstance(sec, dict) else {}
    except Exception as e:
        logger.debug("[IntelE] 读取配置失败: %s", e)
        return {}


def _events_path() -> Path:
    return get_jachin_home() / "logs" / "intelligence_events.jsonl"


def _load_last_ts() -> float:
    if not _LAST_STATE.exists():
        return 0.0
    try:
        data = json.loads(_LAST_STATE.read_text(encoding="utf-8"))
        return float(data.get("last_processed_ts", 0) or 0)
    except Exception:
        return 0.0


def _save_last_ts(ts: float, counts: dict[str, int]) -> None:
    try:
        _LAST_STATE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_STATE.write_text(
            json.dumps({"last_processed_ts": ts, "last_counts": counts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug("[IntelE] 保存状态失败: %s", e)


def maybe_consume_intelligence_events() -> dict[str, Any]:
    """
    若 intelligence_e.enabled：扫描事件尾，按类型累加 reinforce_delta。
    供 run_agent 入口调用（低开销）。
    """
    cfg = _load_intel_e_config()
    if not cfg.get("enabled"):
        return {"ok": False, "skipped": "disabled"}

    min_gap = float(cfg.get("min_interval_seconds", 60) or 60)
    now = time.time()
    if now - _load_last_ts() < min_gap:
        return {"ok": False, "skipped": "throttle"}

    ep = _events_path()
    if not ep.exists():
        return {"ok": False, "skipped": "no_events_file"}

    max_lines = int(cfg.get("max_events_per_run", 400) or 400)
    try:
        lines = ep.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    tail = lines[-max_lines:] if len(lines) > max_lines else lines

    counts: dict[str, int] = {}
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        t = str(rec.get("type") or "")
        counts[t] = counts.get(t, 0) + 1

    mid = str(cfg.get("reinforce_memory_id") or "_intel_from_events").strip() or "_intel_from_events"
    d_anchor = float(cfg.get("anchor_stale_delta", 0.04) or 0)
    d_plan = float(cfg.get("plan_gate_delta", 0.02) or 0)
    d_repeat = float(cfg.get("repeat_intent_delta", 0.02) or 0)
    d_rf = float(cfg.get("repeat_followup_delta", 0.025) or 0)
    d_skip = float(cfg.get("message_skipped_delta", 0.01) or 0)
    d_dwell = float(cfg.get("dwell_bucket_delta", 0.005) or 0)
    total_delta = 0.0
    if d_anchor and counts.get("anchor_stale", 0) > 0:
        total_delta += d_anchor * min(5, counts["anchor_stale"])
    if d_plan and counts.get("plan_gate_blocked", 0) > 0:
        total_delta += d_plan * min(8, counts["plan_gate_blocked"])
    if d_repeat and counts.get("user_repeat_intent", 0) > 0:
        total_delta += d_repeat * min(4, counts["user_repeat_intent"])
    if d_rf and counts.get("user_repeat_followup", 0) > 0:
        total_delta += d_rf * min(5, counts["user_repeat_followup"])
    if d_ei and counts.get("user_repeat_intent_embedding", 0) > 0:
        total_delta += d_ei * min(4, counts["user_repeat_intent_embedding"])
    if d_ef and counts.get("user_repeat_followup_embedding", 0) > 0:
        total_delta += d_ef * min(5, counts["user_repeat_followup_embedding"])
    if d_ee and counts.get("user_echo_assistant_embedding", 0) > 0:
        total_delta += d_ee * min(5, counts["user_echo_assistant_embedding"])
    if d_skip and counts.get("user_message_skipped", 0) > 0:
        total_delta += d_skip * min(6, counts["user_message_skipped"])
    if d_dwell and counts.get("user_message_dwell", 0) > 0:
        total_delta += d_dwell * min(10, counts["user_message_dwell"])
    # ui_memory_thumbs_up/down：已由 POST /memory/feedback 直接写入目标 memory_id，此处仅记事件不入聚合桶，避免双计

    # 允许净增量为负（如 UI 点踩），仅在没有可聚合信号时跳过
    if total_delta == 0:
        _save_last_ts(now, counts)
        return {"ok": True, "delta": 0.0, "counts": counts}

    try:
        from core.db.memory_reinforcement import add_reinforce_delta

        add_reinforce_delta(mid, total_delta, max_per_id=float(cfg.get("reinforce_max_per_id", 8.0) or 8.0))
    except Exception as e:
        logger.warning("[IntelE] reinforce 写入失败: %s", e)
        return {"ok": False, "error": str(e)}

    _save_last_ts(now, counts)
    return {"ok": True, "memory_id": mid, "delta": total_delta, "counts": counts}
