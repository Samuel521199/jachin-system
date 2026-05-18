"""
PMO 资源预警巡检调度器

- 周三 09:30 北京时间：延期 + 偏闲预警
- 周四 14:00 北京时间：延期 + 本周进度落后预警

技能文件：skills_repo/pmo-copilot/SKILL.resource-monitor.md
信道：pmo_resource_monitor_scheduler（独立于 pmo_copilot_cli，不触发强制推送守卫）

有告警才推飞书，全员 ✅ 正常则静默。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────────────────────────

_CHANNEL = "pmo_resource_monitor_scheduler"
_BJT = ZoneInfo("Asia/Shanghai")
_LOG_PATH = Path.home() / ".jachin" / "data" / "pmo_resource_monitor_log.ndjson"

_FOCUS_HINT_WED = (
    "请严格按 SKILL 指令执行资源预警巡检（周三口径）。"
    "重点关注：① 有延期未完成任务的人员（🚨超负荷·延期），② 本周任务已全部完成的人员（🟡偏闲）。"
    "全员 ✅ 正常则 Final Answer 第一行必须为：resource_monitor_result: all_clear，并且禁止调用 notifier。"
    "存在 🚨 或 🟡 则推送精简预警卡（两群），Final Answer 第一行为：resource_monitor_result: alert_sent。"
)

_FOCUS_HINT_THU = (
    "请严格按 SKILL 指令执行资源预警巡检（周四口径）。"
    "重点关注：① 有延期未完成任务的人员（🚨超负荷·延期），② 本周计划任务未按进度完成的人员（🚨超负荷·本周进度落后）。"
    "全员 ✅ 正常则 Final Answer 第一行必须为：resource_monitor_result: all_clear，并且禁止调用 notifier。"
    "存在 🚨 则推送精简预警卡（两群），Final Answer 第一行为：resource_monitor_result: alert_sent。"
)

_DEFAULT_SKILL_PATH_CANDIDATES = [
    Path(__file__).parent.parent.parent / "skills_repo" / "pmo-copilot" / "SKILL.resource-monitor.md",
    Path.home() / ".jachin" / "l3_skill_cache" / "pmo-copilot" / "SKILL.resource-monitor.md",
]

_scheduler: Any | None = None
_scheduler_started = False

# ──────────────────────────────────────────────────────────────────────────────
# 技能文件
# ──────────────────────────────────────────────────────────────────────────────


def _get_resource_monitor_skill_path() -> Path | None:
    for p in _DEFAULT_SKILL_PATH_CANDIDATES:
        if p.is_file():
            return p.resolve()
    return None


def _parse_skill_md(raw: str) -> tuple[dict[str, Any], str]:
    text = raw.lstrip("\ufeff")
    try:
        import yaml
    except ImportError:
        return {}, text.strip()
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text.strip()
    meta = yaml.safe_load(m.group(1))
    if not isinstance(meta, dict):
        meta = {}
    body = (m.group(2) or "").strip()
    return meta, body


def _allowed_tools_from_skill_meta(meta: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("mcp_tools", "native_tools"):
        for x in meta.get(key) or []:
            if isinstance(x, str) and x.strip():
                ids.append(x.strip())
    for row in meta.get("tools") or []:
        if isinstance(row, dict):
            pref = row.get("prefer") or row.get("prefer_tool")
            if isinstance(pref, str) and pref.strip():
                ids.append(pref.strip())
    seen: set[str] = set()
    return [t for t in ids if not (t.lower() in seen or seen.add(t.lower()))]  # type: ignore[func-returns-value]


def _build_gateway_skill_inject(skill_path: Path, meta: dict[str, Any], body: str) -> str:
    name = str(meta.get("name") or "pmo-resource-monitor").strip()
    persona = str(meta.get("persona") or "").strip()
    parts = [f"【声明式技能 · {name}】\nskill_file: {skill_path}"]
    if persona:
        parts.append("### Persona（YAML frontmatter）\n\n" + persona)
    parts.append("### SKILL 指令正文（Markdown）\n\n" + body)
    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Final Answer 解析
# ──────────────────────────────────────────────────────────────────────────────

_RESULT_RE = re.compile(
    r"resource_monitor_result\s*:\s*(all_clear|alert_sent)", re.I
)


def _parse_monitor_result(answer: str) -> str:
    """提取 Final Answer 里的协议标签；无标签时返回 'unknown'。"""
    m = _RESULT_RE.search(answer or "")
    return m.group(1).lower() if m else "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# 日志落盘
# ──────────────────────────────────────────────────────────────────────────────


def _append_log(record: dict[str, Any]) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[pmo_resource_monitor] 日志落盘失败（已忽略）: %s", e)


# ──────────────────────────────────────────────────────────────────────────────
# Agent 执行
# ──────────────────────────────────────────────────────────────────────────────


async def _run_resource_monitor_async(run_type: str, focus_hint: str) -> dict[str, Any]:
    """运行一次资源预警巡检，返回结构化结果。"""
    from l3_node.agent_core import _build_system_prompt, run_agent
    from l3_node.bootstrap import get_engine
    from l3_node.intent_gateway.bundle import build_gateway_bundle
    from l3_node.primitives.tools.tool_pool import (
        assemble_tool_pool,
        expand_allowed_skills_with_implicit_sqlite_read,
        expand_allowed_skills_with_local_mcp,
    )
    from l3_node.routing.output_format_signals import analyze_output_format_signals

    started_at = datetime.now(_BJT).isoformat()

    skill_path = _get_resource_monitor_skill_path()
    if skill_path is None:
        msg = "SKILL.resource-monitor.md 未找到，跳过本次巡检"
        logger.warning("[pmo_resource_monitor] %s", msg)
        return {"ok": False, "run_type": run_type, "error": msg, "started_at": started_at}

    try:
        raw = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        msg = f"读取 SKILL 失败：{e}"
        logger.warning("[pmo_resource_monitor] %s", msg)
        return {"ok": False, "run_type": run_type, "error": msg, "started_at": started_at}

    meta, skill_body = _parse_skill_md(raw)
    base_allow = _allowed_tools_from_skill_meta(meta)

    implicit: dict[str, Any] = {
        "channel": _CHANNEL,
        "source": "pmo_resource_monitor_scheduler",
    }

    bundle = build_gateway_bundle(
        user_input=focus_hint,
        short_memory_context="",
        correlation_id=None,
        implicit_attribution=implicit,
    )

    try:
        from l3_node.intent_gateway.gateway_pipeline import apply_gateway_ingress_pipeline

        await apply_gateway_ingress_pipeline(bundle, focus_hint, [], run_id="")
    except Exception as e:
        logger.debug("[pmo_resource_monitor] gateway ingress pipeline 跳过: %s", e)

    expanded = expand_allowed_skills_with_implicit_sqlite_read(list(base_allow))
    expanded = expand_allowed_skills_with_local_mcp(expanded)
    tools = await assemble_tool_pool(
        allowed_skills=expanded,
        gateway_bundle=bundle,
        bg_channel=_CHANNEL,
    )

    gateway_block = _build_gateway_skill_inject(skill_path, meta, skill_body)

    fmt_sig = analyze_output_format_signals(focus_hint)
    prompt_style = "slim_user_led" if fmt_sig.slim_system_prompt() else "full"

    full_system = await _build_system_prompt(
        tools=tools,
        allow_delegate=False,
        allow_coordinate=False,
        prompt_cycle=None,
        recruitment_longform=False,
        hr_domain_prompt_active=False,
        prompt_style=prompt_style,
        pure_json_contract=False,
        gateway_inject=gateway_block,
        safety_lock_user_text=focus_hint,
        chief_advisor_mode=False,
        environment_report_block="",
        semantic_layer=None,
        experience_few_shots="",
        realtime_web_grounding_block="",
        domain_experts=None,
    )

    engine = get_engine()

    try:
        answer = await run_agent(
            focus_hint,
            engine,
            max_iterations=20,
            _system_prompt_override=full_system,
            _allowed_skills_override=base_allow if base_allow else None,
            gateway_context_bundle=bundle,
            implicit_attribution=implicit,
        )
    except Exception as e:
        tb = traceback.format_exc()
        logger.exception("[pmo_resource_monitor] run_agent 崩溃")
        rec = {
            "ok": False,
            "run_type": run_type,
            "error": f"{type(e).__name__}: {e}",
            "traceback": tb[:3000],
            "started_at": started_at,
            "finished_at": datetime.now(_BJT).isoformat(),
        }
        _append_log(rec)
        return rec

    result_tag = _parse_monitor_result(answer or "")
    finished_at = datetime.now(_BJT).isoformat()

    rec = {
        "ok": True,
        "run_type": run_type,
        "result": result_tag,
        "answer_preview": (answer or "")[:500],
        "started_at": started_at,
        "finished_at": finished_at,
    }
    _append_log(rec)

    logger.info(
        "[pmo_resource_monitor] 巡检完成 run_type=%s result=%s",
        run_type,
        result_tag,
    )
    return rec


# ──────────────────────────────────────────────────────────────────────────────
# 定时任务
# ──────────────────────────────────────────────────────────────────────────────


async def _job_wed() -> None:
    """周三 09:30 BJT：延期 + 偏闲预警。"""
    try:
        await _run_resource_monitor_async("wed", _FOCUS_HINT_WED)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("[pmo_resource_monitor] 周三巡检任务崩溃: %s", e)


async def _job_thu() -> None:
    """周四 14:00 BJT：延期 + 进度落后预警。"""
    try:
        await _run_resource_monitor_async("thu", _FOCUS_HINT_THU)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("[pmo_resource_monitor] 周四巡检任务崩溃: %s", e)


# ──────────────────────────────────────────────────────────────────────────────
# 公共 API
# ──────────────────────────────────────────────────────────────────────────────


def start_pmo_resource_monitor_scheduler() -> dict[str, Any]:
    """注册并启动 AsyncIOScheduler（幂等：已在跑则跳过）。"""
    global _scheduler, _scheduler_started

    if os.environ.get("PMO_RESOURCE_MONITOR_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        logger.info("[pmo_resource_monitor] PMO_RESOURCE_MONITOR_DISABLE=1，跳过启动")
        return {"ok": True, "active": False, "message": "已禁用（环境变量）"}

    if _scheduler_started and _scheduler is not None:
        return {"ok": True, "active": True, "message": "已在运行"}

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = AsyncIOScheduler()

    sched.add_job(
        _job_wed,
        CronTrigger(day_of_week="wed", hour=9, minute=30, timezone=_BJT),
        id="pmo_resource_monitor_wed",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    sched.add_job(
        _job_thu,
        CronTrigger(day_of_week="thu", hour=14, minute=0, timezone=_BJT),
        id="pmo_resource_monitor_thu",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    sched.start()
    _scheduler = sched
    _scheduler_started = True

    logger.info(
        "[pmo_resource_monitor] AsyncIOScheduler 已启动"
        "（周三 09:30 BJT 延期+偏闲 / 周四 14:00 BJT 延期+进度落后）"
    )
    return {"ok": True, "active": True, "message": "已启动"}


def init_pmo_resource_monitor_auto_start() -> None:
    """L3 HTTP on_startup 调用：无条件启动 PMO 资源预警调度器（无状态文件依赖）。"""
    try:
        start_pmo_resource_monitor_scheduler()
    except Exception as e:
        logger.warning("[pmo_resource_monitor] init_pmo_resource_monitor_auto_start 失败（已忽略）: %s", e)


def stop_pmo_resource_monitor_scheduler() -> dict[str, Any]:
    """停止调度器。"""
    global _scheduler, _scheduler_started

    if _scheduler is None:
        _scheduler_started = False
        return {"ok": True, "active": False, "message": "未运行"}

    try:
        _scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning("[pmo_resource_monitor] shutdown: %s", e)
    finally:
        _scheduler = None
        _scheduler_started = False

    return {"ok": True, "active": False, "message": "已停止"}


def run_pmo_resource_monitor_once(kind: str = "wed") -> dict[str, Any]:
    """手动立即触发一次巡检（忽略禁用环境变量，供脚本 / 测试使用）。

    kind: "wed"/"w"/"wednesday" → 周三口径；"thu"/"t"/"thursday" → 周四口径
    """
    k = (kind or "wed").strip().lower()
    if k in ("thu", "t", "thursday"):
        run_type, hint = "thu", _FOCUS_HINT_THU
    else:
        run_type, hint = "wed", _FOCUS_HINT_WED

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在已有事件循环内（如从 HTTP handler 调用）——返回协程，让调用方 await
            import concurrent.futures

            future: concurrent.futures.Future[dict[str, Any]] = asyncio.run_coroutine_threadsafe(
                _run_resource_monitor_async(run_type, hint), loop
            )
            return future.result(timeout=300)
        else:
            return loop.run_until_complete(_run_resource_monitor_async(run_type, hint))
    except Exception as e:
        return {"ok": False, "run_type": run_type, "error": repr(e)}
