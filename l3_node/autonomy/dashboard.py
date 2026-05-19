"""
可观测性面板数据（§5.3.3）

供 ``GET /api/v1/autonomy/status`` 使用；与 **R**/**Q** 同源诊断鉴权。
"""
from __future__ import annotations

import os
import time
from typing import Any


_BOOT_MONO = time.monotonic()


def build_autonomy_status_dict() -> dict[str, Any]:
    """聚合进程内自主性/运行时状态（失败字段填空或 0，不抛异常）。"""
    uptime_hours = round((time.monotonic() - _BOOT_MONO) / 3600.0, 2)
    out: dict[str, Any] = {
        "uptime_hours": uptime_hours,
        "active_intents": 0,
        "running_tasks": 0,
        "queued_tasks": 0,
        "today_token_used": 0,
        "today_token_budget": 200000,
        "today_tasks_completed": 0,
        "today_tasks_failed": 0,
        "last_experience_learned": None,
        "anomalies": [],
        "next_scheduled_task": None,
        "awareness_loop_disabled": False,
        "disk_free_gb": None,
    }
    try:
        out["awareness_loop_disabled"] = (
            (os.environ.get("JACHIN_AWARENESS_LOOP_DISABLE") or "").strip().lower()
            in ("1", "true", "yes")
        )
    except Exception:
        pass

    try:
        from l3_node.llm_budget import get_today_token_usage, get_token_day_budget

        out["today_token_used"] = get_today_token_usage()
        out["today_token_budget"] = get_token_day_budget()
    except Exception:
        pass

    try:
        from l3_node.autonomy.intent_persister import get_intent_persister

        persister = get_intent_persister()
        intents = persister.list_all()
        out["active_intents"] = sum(1 for i in intents if i.enabled and i.status != "failed")
        anomalies = []
        for i in intents:
            if i.consecutive_failures >= 2:
                anomalies.append(
                    {
                        "intent_id": i.intent_id,
                        "description": i.description[:120],
                        "consecutive_failures": i.consecutive_failures,
                    }
                )
        out["anomalies"] = anomalies
        # 粗粒度：今日执行次数（有 last_executed_at 且为今天的记一次）
        import datetime

        today = datetime.date.today().isoformat()
        completed_today = 0
        failed_today = 0
        for i in intents:
            if i.last_result is None and i.last_executed_at is None:
                continue
            try:
                ts = float(i.last_executed_at or 0)
                day = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                if day != today:
                    continue
                if i.status == "failed":
                    failed_today += 1
                else:
                    completed_today += 1
            except Exception:
                pass
        out["today_tasks_completed"] = completed_today
        out["today_tasks_failed"] = failed_today
        # 下一个 cron/interval 意图（启发式：第一个 enabled + interval/cron）
        next_hint = None
        for i in intents:
            if not i.enabled or i.status == "failed":
                continue
            if i.trigger.type == "interval" and i.trigger.interval_sec:
                nxt = (i.last_executed_at or i.created_at) + float(i.trigger.interval_sec)
                next_hint = {"intent_id": i.intent_id, "at_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(nxt))}
                break
            if i.trigger.cron:
                next_hint = {"intent_id": i.intent_id, "cron": i.trigger.cron}
                break
        out["next_scheduled_task"] = next_hint
    except Exception:
        pass

    try:
        from l3_node.task_runtime_registry import get_runtime_registry_snapshot_dict

        snap = get_runtime_registry_snapshot_dict()
        fg = snap.get("foreground_tasks") or []
        out["running_tasks"] = int(snap.get("foreground_task_count") or len(fg))
    except Exception:
        pass

    try:
        from l3_node.primitives.agent_tasks.background_task_service import get_background_queue_metrics

        bgm = get_background_queue_metrics()
        out["queued_tasks"] = int(bgm.get("queued") or 0)
        out["background_p3_running"] = int(bgm.get("running") or 0)
    except Exception:
        pass

    try:
        import shutil

        usage = shutil.disk_usage("/")
        out["disk_free_gb"] = round(usage.free / (1024 ** 3), 2)
    except Exception:
        try:
            import shutil

            home = os.path.expanduser("~")
            usage = shutil.disk_usage(home)
            out["disk_free_gb"] = round(usage.free / (1024 ** 3), 2)
        except Exception:
            pass

    return out
