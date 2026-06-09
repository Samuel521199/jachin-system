"""
L3 运行时诊断：里程碑 + 定期快照写入 l3_debug.log（[L3 Runtime] 前缀）。

- 默认每 90s 一条 JSON 快照：前台 run、后台队列、IM 通道、PMO 表监控等。
- 关闭：JACHIN_L3_RUNTIME_DIAG=0
- 间隔：JACHIN_L3_RUNTIME_DIAG_SEC（默认 90，最小 30）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger("l3_node.runtime_diag")

_START_MONO = time.monotonic()
_LOOP_TASK: asyncio.Task | None = None


def _runtime_diag_enabled() -> bool:
    v = (os.environ.get("JACHIN_L3_RUNTIME_DIAG") or "").strip().lower()
    return v not in ("0", "false", "no", "off")


def _diag_interval_sec() -> float:
    raw = (os.environ.get("JACHIN_L3_RUNTIME_DIAG_SEC") or "90").strip()
    try:
        sec = float(raw)
    except ValueError:
        sec = 90.0
    return max(30.0, sec)


def log_runtime_milestone(msg: str) -> None:
    """关键节点（HTTP 就绪、WS 监听、IM 启动等）。"""
    logger.info("[L3 Runtime] milestone: %s", (msg or "").strip())


def _im_channels_summary() -> dict[str, Any]:
    out: dict[str, Any] = {"enabled": [], "config_path": None}
    try:
        from l3_node.im_channels.config import get_config_path, load_config

        out["config_path"] = str(get_config_path())
        cfg = load_config()
        channels = cfg.get("im_channels") or {}
        for ch_id, ch_cfg in channels.items():
            if not isinstance(ch_cfg, dict):
                continue
            if ch_cfg.get("enabled"):
                chat_ids = ch_cfg.get("chat_ids") or []
                out["enabled"].append(
                    {
                        "id": ch_id,
                        "mode": ch_cfg.get("mode") or "long_connection",
                        "chat_ids_count": len(chat_ids) if isinstance(chat_ids, list) else 0,
                    }
                )
    except Exception as e:
        out["error"] = str(e)[:240]
    return out


def _pmo_bitable_watch_summary() -> dict[str, Any]:
    try:
        from l3_node.tools.pmo_bitable_watch import run_bitable_watch_status

        st = run_bitable_watch_status()
        return {
            "enabled": st.get("enabled"),
            "session_active": st.get("session_active"),
            "session_event_count": st.get("session_event_count"),
            "last_tick_at": st.get("last_tick_at"),
            "last_notify_at": st.get("last_notify_at"),
        }
    except Exception as e:
        return {"error": str(e)[:240]}


def build_runtime_snapshot() -> dict[str, Any]:
    snap: dict[str, Any] = {
        "ts": time.time(),
        "uptime_sec": round(time.monotonic() - _START_MONO, 1),
        "pid": os.getpid(),
        "argv_mode": next((a for a in sys.argv[1:] if a.startswith("--")), "default"),
        "log_file": None,
        "jachin_log_dir": os.environ.get("JACHIN_LOG_DIR") or None,
        "app_root": os.environ.get("JACHIN_APP_ROOT") or None,
    }
    try:
        from l3_node.early_log import get_log_path

        snap["log_file"] = get_log_path()
    except Exception:
        pass

    try:
        from l3_node.task_runtime_registry import get_runtime_registry_snapshot_dict

        snap["foreground"] = get_runtime_registry_snapshot_dict()
    except Exception as e:
        snap["foreground_error"] = str(e)[:200]

    try:
        from l3_node.primitives.agent_tasks.background_task_service import get_background_queue_metrics

        snap["background_queue"] = get_background_queue_metrics()
    except Exception as e:
        snap["background_queue_error"] = str(e)[:200]

    snap["im_channels"] = _im_channels_summary()
    snap["pmo_bitable_watch"] = _pmo_bitable_watch_summary()

    try:
        from l3_node.http_server import L3_HTTP_PORT

        snap["http_port_default"] = L3_HTTP_PORT
    except Exception:
        pass

    return snap


def format_runtime_snapshot_line() -> str:
    return json.dumps(build_runtime_snapshot(), ensure_ascii=False, separators=(",", ":"))


def log_runtime_snapshot_now() -> None:
    logger.info("[L3 Runtime] snapshot: %s", format_runtime_snapshot_line())


async def start_runtime_diag_loop() -> None:
    """HTTP 就绪后调用一次；幂等。"""
    global _LOOP_TASK
    if not _runtime_diag_enabled():
        logger.info("[L3 Runtime] 定期快照已关闭 (JACHIN_L3_RUNTIME_DIAG=0)")
        return
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        return

    interval = _diag_interval_sec()

    async def _loop() -> None:
        log_runtime_milestone(f"runtime diag loop started interval={interval}s")
        await asyncio.sleep(min(12.0, interval / 3))
        while True:
            try:
                log_runtime_snapshot_now()
            except Exception as e:
                logger.warning("[L3 Runtime] snapshot failed: %s", e)
            await asyncio.sleep(interval)

    _LOOP_TASK = asyncio.create_task(_loop(), name="jachin-l3-runtime-diag")
