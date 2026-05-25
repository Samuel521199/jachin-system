"""
定时 / 后台调度任务在**执行入口**注册 GlobalTaskRegistry（路线图 P1 · 先登记再跑）。

与 ``run_agent`` 内 ``register_global_foreground_task``（通常 P1）区分：
- 本模块在 APScheduler / AwarenessLoop 回调**进入时**登记 **P2**（定时强制档）
- ``run_agent`` 若带 ``_scheduled_global_registry_active`` 则跳过二次登记，避免同一次执行双 run_id

环境变量
--------
JACHIN_GLOBAL_REGISTRY_SCHEDULED=1   开启定时入口登记（默认：随 JACHIN_GLOBAL_REGISTRY_ENABLE=1 开启）
JACHIN_GLOBAL_REGISTRY_SCHEDULED_PREEMPT=0  P2 任务启动时是否 check_and_preempt（默认关）
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

logger = logging.getLogger(__name__)


def get_scheduled_l3_engine() -> Any:
    """定时 ``run_agent`` 路径用的轻量 LiteLLMEngine（与 AwarenessLoop 条件 LLM 同源）。"""
    from l3_node.llm_client import LiteLLMEngine, SecurityContext

    model = (
        os.environ.get("JACHIN_SCHEDULED_LLM_MODEL")
        or os.environ.get("LLM_MODEL")
        or ""
    ).strip()
    timeout_sec = float(os.environ.get("JACHIN_SCHEDULED_LLM_TIMEOUT") or "300")
    ctx = SecurityContext()
    return LiteLLMEngine(
        ctx,
        model_name=model or "gpt-4o-mini",
        timeout=timeout_sec,
        max_attempts=2,
    )


def scheduled_global_registry_enabled() -> bool:
    v = (os.environ.get("JACHIN_GLOBAL_REGISTRY_SCHEDULED") or "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        try:
            from l3_node.global_task_registry import global_registry_enabled

            return global_registry_enabled()
        except ImportError:
            return False
    try:
        from l3_node.global_task_registry import global_registry_enabled

        return global_registry_enabled()
    except ImportError:
        return False


def scheduled_preempt_enabled() -> bool:
    return scheduled_global_registry_enabled() and (
        os.environ.get("JACHIN_GLOBAL_REGISTRY_SCHEDULED_PREEMPT") or ""
    ).strip().lower() in ("1", "true", "yes", "on")


def make_scheduled_run_id(source: str, job_id: str) -> str:
    src = (source or "sched").strip()[:32] or "sched"
    jid = (job_id or "job").strip()[:48] or "job"
    return f"sched-{src}-{jid}-{uuid.uuid4().hex[:10]}"


def _build_resource_tags(source: str, job_id: str, extra: list[str] | None) -> list[str]:
    tags = [
        f"scheduled:{(source or 'sched')[:32]}",
        f"job:{(job_id or 'job')[:48]}",
    ]
    if extra:
        for t in extra:
            s = str(t).strip()[:64]
            if s and s not in tags:
                tags.append(s)
    return tags[:8]


def register_scheduled_global_task(
    source: str,
    job_id: str,
    *,
    title: str = "",
    extra_resource_tags: list[str] | None = None,
    run_id: str | None = None,
) -> str:
    """
    定时任务入口调用；返回 ``run_id``（供 ``unregister`` 与 ``run_agent`` metadata 关联）。
    """
    rid = (run_id or "").strip() or make_scheduled_run_id(source, job_id)
    if not scheduled_global_registry_enabled():
        return rid

    tags = _build_resource_tags(source, job_id, extra_resource_tags)
    channel = f"scheduled:{(source or 'sched')[:32]}"
    try:
        from l3_node.global_task_registry import (
            check_and_preempt,
            register_task,
        )

        register_task(
            rid,
            channel=channel,
            session_key=f"{source}:{job_id}"[:120],
            priority="P2",
            resource_tags=tags,
            extra={
                "scheduled": True,
                "source": source,
                "job_id": job_id,
                "title": (title or "")[:200],
            },
        )
        if scheduled_preempt_enabled():
            pr = check_and_preempt(rid, "P2", tags)
            if pr.preempted_run_ids:
                logger.info(
                    "[ScheduledRegistry] preempt source=%s job=%s %s",
                    source,
                    job_id,
                    pr.message,
                )
        logger.info(
            "[ScheduledRegistry] registered run_id=%s source=%s job=%s P2",
            rid[:20],
            source,
            job_id,
        )
    except Exception as e:
        logger.warning("[ScheduledRegistry] register failed: %s", e)
    return rid


def unregister_scheduled_global_task(run_id: str) -> None:
    rid = (run_id or "").strip()
    if not rid or not scheduled_global_registry_enabled():
        return
    try:
        from l3_node.global_task_registry import unregister_task

        unregister_task(rid)
        logger.debug("[ScheduledRegistry] unregistered run_id=%s", rid[:20])
    except Exception as e:
        logger.debug("[ScheduledRegistry] unregister failed: %s", e)


def run_agent_metadata_for_scheduled(
    parent_run_id: str,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """合并进 ``run_agent(..., metadata=...)``，避免内层重复 Global 登记。"""
    md = dict(base or {})
    md["_scheduled_global_registry_active"] = True
    md["_scheduled_parent_run_id"] = parent_run_id
    return md


def run_agent_implicit_attribution_for_scheduled(
    source: str,
    job_id: str,
    *,
    parent_run_id: str,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ia = dict(base or {})
    tags = list(ia.get("resource_tags") or [])
    for t in _build_resource_tags(source, job_id, None):
        if t not in tags:
            tags.append(t)
    ia["resource_tags"] = tags[:8]
    ia["task_priority"] = "P2"
    ia["_scheduled_global_registry_active"] = True
    ia["_scheduled_parent_run_id"] = parent_run_id
    ia["channel"] = ia.get("channel") or f"scheduled:{source[:32]}"
    return ia


@contextmanager
def scheduled_global_task_scope(
    source: str,
    job_id: str,
    *,
    title: str = "",
    extra_resource_tags: list[str] | None = None,
) -> Iterator[str]:
    """同步定时回调（如 BI ``_run_bi_daily_report_job``）。"""
    rid = register_scheduled_global_task(
        source,
        job_id,
        title=title,
        extra_resource_tags=extra_resource_tags,
    )
    try:
        yield rid
    finally:
        unregister_scheduled_global_task(rid)


@asynccontextmanager
async def scheduled_global_task_scope_async(
    source: str,
    job_id: str,
    *,
    title: str = "",
    extra_resource_tags: list[str] | None = None,
) -> AsyncIterator[str]:
    """异步定时回调（如 kalaroko ``hourly_inspection_job``）。"""
    rid = register_scheduled_global_task(
        source,
        job_id,
        title=title,
        extra_resource_tags=extra_resource_tags,
    )
    try:
        yield rid
    finally:
        unregister_scheduled_global_task(rid)
