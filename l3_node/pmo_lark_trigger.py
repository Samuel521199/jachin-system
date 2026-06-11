"""
PMO Lark 精确触发器（/pmo、全量看板等）→ ``pmo_copilot_cli`` 完整 Skill 管线。

显式 PMO 任务请用 ``#*#`` 前缀（见 ``slash_hash_skill_router``）；普通闲聊/项目问答走默认 ``run_agent``。
**不再**对模糊项目话术弹出「请选择 1/2/3」确认卡片。
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 正则：精确触发
# ──────────────────────────────────────────────────────────────────────────────
_PMO_EXACT_RE = re.compile(
    r"^/pmo\b|^/board\b|"
    r"^执行\s*pmo|^生成\s*pmo|^触发\s*pmo|^拉取\s*pmo|^pmo\s*(看板|战报|播报|报告|报表)\b|"
    r"^宏观看板$|^全量看板$|^生成全量看板$|^生成看板$|^项目看板$|^执行看板$|"
    r"^pmo$|^执行宏观看板|^pmo报告|^产研看板$",
    re.I,
)

# 精确触发时对应的默认 PMO 消息
_PMO_EXACT_DEFAULT_MSG = (
    "请严格按系统提示中的 PMO-Copilot SKILL：按「分支 A / 定时宏观看板」拉取 §1.1 全部种子链接并汇总"
)

# ──────────────────────────────────────────────────────────────────────────────
# 内部工具函数（复制自 scripts/run_pmo_copilot_skill.py 避免跨包依赖）
# ──────────────────────────────────────────────────────────────────────────────

def _parse_skill_md(raw: str) -> tuple[dict[str, Any], str]:
    """拆分 YAML frontmatter 与 Markdown 正文。"""
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
    name = str(meta.get("name") or "pmo-copilot").strip()
    persona = str(meta.get("persona") or "").strip()
    parts = [f"【声明式技能 · {name}】\nskill_file: {skill_path}"]
    if persona:
        parts.append("### Persona（YAML frontmatter）\n\n" + persona)
    parts.append("### SKILL 指令正文（Markdown）\n\n" + body)
    return "\n\n".join(parts)


def _get_pmo_skill_path() -> Path | None:
    """查找 PMO SKILL.md，与 run_pmo_copilot_skill.py 路径一致。"""
    from l3_node.pmo_skill_paths import resolve_pmo_skill_md

    return resolve_pmo_skill_md()


# ──────────────────────────────────────────────────────────────────────────────
# action_key → PMO 用户消息映射
# ──────────────────────────────────────────────────────────────────────────────

_ACTION_MESSAGES: dict[str, str] = {
    "full_board": (
        "请严格按系统提示中的 PMO-Copilot SKILL：按「分支 A / 定时宏观看板」"
        "拉取 §1.1 全部种子链接并汇总，生成包含三张核心表的 Markdown 战报，"
        "并通过 mcp:atom_lark_notifier 推送飞书消息卡片（对用户仅确认已推送，禁止提及内部收件会话）。"
    ),
    "anomaly": (
        "请严格按系统提示中的 PMO-Copilot SKILL：按「分支 B / 表格变更预警」"
        "检查当前所有在研需求，识别阻塞、逾期、空载人员，生成告警摘要并推送到飞书群。"
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# 异步 PMO 任务执行
# ──────────────────────────────────────────────────────────────────────────────

async def _run_pmo_skill_coro(
    action_key: str,
    user_msg: str,
    engine: Any,
    session_msgs: list[dict[str, Any]],
    chat_id: str,
    *,
    trigger_source: str = "pmo_lark_trigger",
) -> str:
    """
    以 pmo_copilot_cli 信道完整执行 PMO Skill 任务。
    复刻 scripts/run_pmo_copilot_skill.py 的逻辑，但复用已有 engine。
    """
    from l3_node.agent_core import _build_system_prompt, run_agent
    from l3_node.intent_gateway.bundle import build_gateway_bundle
    from l3_node.primitives.tools.tool_pool import (
        assemble_tool_pool,
        expand_allowed_skills_with_implicit_sqlite_read,
        expand_allowed_skills_with_local_mcp,
    )
    from l3_node.routing.output_format_signals import analyze_output_format_signals

    skill_path = _get_pmo_skill_path()
    if skill_path is None:
        logger.warning("[PMO Trigger] SKILL.md 未找到")
        return "⚠️ PMO SKILL 文件未找到，请确认 skills_repo/pmo-copilot/SKILL.md 存在。"

    try:
        raw = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"⚠️ 读取 PMO SKILL 失败：{e}"

    meta, skill_body = _parse_skill_md(raw)
    base_allow = _allowed_tools_from_skill_meta(meta)
    if not base_allow:
        logger.warning("[PMO Trigger] SKILL frontmatter 未声明工具白名单")
        # 继续运行，工具白名单为空时 run_agent 会使用完整工具池

    pmo_user_msg = _ACTION_MESSAGES.get(action_key, user_msg)
    _src = (trigger_source or "pmo_lark_trigger").strip() or "pmo_lark_trigger"
    implicit: dict[str, Any] = {
        "channel": "pmo_copilot_cli",
        "source": _src,
        "lark_chat_id": chat_id,
    }

    import os
    import uuid

    from l3_node.pmo_copilot_debug_file import (
        begin_pmo_debug_log_for_im_trigger,
        finalize_pmo_debug_log,
    )

    _prev_pmo_log_env = os.environ.get("JACHIN_PMO_COPILOT_DEBUG_LOG")
    _corr = str(uuid.uuid4())
    answer = ""
    try:
        try:
            _dbg_path = begin_pmo_debug_log_for_im_trigger(
                pmo_user_msg,
                source=_src,
                chat_id=chat_id,
                correlation_id=_corr,
                max_iterations=32,
            )
            logger.info("[PMO Trigger] 详细调试日志: %s", _dbg_path)
        except Exception as _dbg_e:
            logger.warning("[PMO Trigger] 调试日志初始化跳过: %s", _dbg_e)

        bundle = build_gateway_bundle(
            user_input=pmo_user_msg,
            short_memory_context="",
            correlation_id=_corr,
            implicit_attribution=implicit,
        )

        try:
            from l3_node.intent_gateway.gateway_pipeline import apply_gateway_ingress_pipeline

            await apply_gateway_ingress_pipeline(bundle, pmo_user_msg, [], run_id=_corr)
        except Exception as e:
            logger.debug("[PMO Trigger] gateway ingress pipeline 跳过: %s", e)

        expanded = expand_allowed_skills_with_implicit_sqlite_read(list(base_allow))
        expanded = expand_allowed_skills_with_local_mcp(expanded)
        tools = await assemble_tool_pool(
            allowed_skills=expanded,
            gateway_bundle=bundle,
            bg_channel="pmo_copilot_cli",
        )

        gateway_block = _build_gateway_skill_inject(skill_path, meta, skill_body)

        fmt_sig = analyze_output_format_signals(pmo_user_msg)
        prompt_style = "slim_user_led" if fmt_sig.slim_system_prompt() else "full"

        full_system = await _build_system_prompt(
            tools=tools,
            allow_delegate=True,
            prompt_cycle=None,
            recruitment_longform=False,
            hr_domain_prompt_active=False,
            prompt_style=prompt_style,
            pure_json_contract=False,
            gateway_inject=gateway_block,
            safety_lock_user_text=pmo_user_msg,
            chief_advisor_mode=False,
            environment_report_block="",
            semantic_layer=None,
            experience_few_shots="",
            realtime_web_grounding_block="",
            domain_experts=None,
        )

        answer = await run_agent(
            pmo_user_msg,
            engine,
            max_iterations=32,
            _session_messages=list(session_msgs),
            _system_prompt_override=full_system,
            _allowed_skills_override=base_allow if base_allow else None,
            gateway_context_bundle=bundle,
            implicit_attribution=implicit,
        )
        return _shorten_pmo_lark_dispatcher_reply(answer)
    finally:
        try:
            finalize_pmo_debug_log(answer)
        except Exception as _fin_e:
            logger.debug("[PMO Trigger] 调试日志收尾跳过: %s", _fin_e)
        if _prev_pmo_log_env is not None:
            os.environ["JACHIN_PMO_COPILOT_DEBUG_LOG"] = _prev_pmo_log_env
        else:
            os.environ.pop("JACHIN_PMO_COPILOT_DEBUG_LOG", None)


def _shorten_pmo_lark_dispatcher_reply(answer: str) -> str:
    """
    PMO 战报应经 atom_lark_notifier / macro_dashboard_push 以卡片送达。
    若模型仍把三表 Markdown 写进 Final Answer，勿再当纯文本发回会话。
    """
    a = (answer or "").strip()
    if not a:
        return "✅ PMO 任务已完成；战报请以群内飞书消息卡片为准。"
    if re.search(
        r"(已成功|已经).{0,40}(推送|送达|发送).{0,24}(飞书|卡片|群)|"
        r"macro_dashboard_push|atom_lark_notifier",
        a,
        re.I,
    ):
        if len(a) <= 400 and "|" not in a[:500]:
            return a
    looks_like_war_report = (
        ("需求进度" in a or "Executive Summary" in a or "📊" in a)
        and "|" in a
        and ("---" in a or "|:---" in a)
    )
    if looks_like_war_report or (len(a) > 600 and a.count("|") >= 8):
        return (
            "✅ PMO 宏观看板战报已通过 **飞书消息卡片** 推送到群内，"
            "请在会话中查看带表格翻页的卡片（非本段纯文本 Markdown）。"
        )
    try:
        from l3_node.pmo_user_visible_sanitize import sanitize_pmo_confidential_wording

        a = sanitize_pmo_confidential_wording(a)
    except Exception:
        pass
    return a


async def run_pmo_heavy_task_from_lark(
    action_key: str,
    user_msg: str,
    engine: Any,
    session_msgs: list[dict[str, Any]],
    chat_id: str,
    *,
    trigger_source: str = "pmo_lark_trigger",
) -> str:
    """供 ``#*#`` / ``/pmo`` 等入口复用：完整 PMO Skill + 缩短 dispatcher 回执。"""
    return await _run_pmo_skill_coro(
        action_key,
        user_msg,
        engine,
        session_msgs,
        chat_id,
        trigger_source=trigger_source,
    )


def run_pmo_heavy_task_from_lark_sync(
    action_key: str,
    user_msg: str,
    engine: Any,
    session_msgs: list[dict[str, Any]],
    chat_id: str,
    loop: asyncio.AbstractEventLoop,
    *,
    trigger_source: str = "pmo_lark_trigger",
) -> str:
    future = asyncio.run_coroutine_threadsafe(
        run_pmo_heavy_task_from_lark(
            action_key,
            user_msg,
            engine,
            session_msgs,
            chat_id,
            trigger_source=trigger_source,
        ),
        loop,
    )
    return future.result(timeout=None) or ""


# ──────────────────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────────────────

def try_pmo_lark_intercept(
    text: str,
    chat_id: str,
    user_id: str,
    send_reply_fn: Callable[[str, str], bool],
    run_agent_fn: Callable[..., Any],
    engine: Any,
    loop: asyncio.AbstractEventLoop,
    session_messages: list[dict[str, Any]],
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    api_base: str | None = None,
) -> str | None:
    """
    PMO 精确触发主入口（``/pmo``、全量看板等）。未命中则返回 ``None``，由 dispatcher 走 ``run_agent``。

    显式 Skill 用 ``#*#``（``slash_hash_skill_router``）；不再弹出 1/2/3 意图确认卡片。
    """
    norm = (text or "").strip()
    if not norm:
        return None

    cid = (chat_id or "").strip()

    # ── 精确触发 ──
    if _PMO_EXACT_RE.search(norm):
        logger.info("[PMO Trigger] 精确触发 chat_id=%s text=%r", cid[:24], norm[:60])

        if cid:
            send_reply_fn(cid, "⏳ PMO 全量看板任务已启动，正在拉取飞书多维表，约需 1-3 分钟，战报将直接推送到群内…")

        # 解析精确命令中是否附带了自定义消息
        cmd_msg = _extract_exact_custom_msg(norm) or _PMO_EXACT_DEFAULT_MSG

        try:
            return run_pmo_heavy_task_from_lark_sync(
                "full_board", cmd_msg, engine, session_messages, cid, loop
            )
        except Exception as e:
            logger.exception("[PMO Trigger] 精确 PMO 任务执行失败: %s", e)
            return f"⚠️ PMO 任务执行出错：{e}"

    return None


def _extract_exact_custom_msg(text: str) -> str | None:
    """
    从精确触发文本中提取用户附加的自定义消息。
    例："/pmo 关注下5月发版" → "关注下5月发版"
    """
    # 移除命令前缀，取剩余部分
    stripped = re.sub(
        r"^(/pmo|/board|执行\s*pmo|生成\s*pmo|触发\s*pmo|pmo|全量看板|宏观看板|生成看板|执行宏观看板|产研看板)\b",
        "",
        text,
        flags=re.I,
    ).strip()
    if stripped and len(stripped) >= 4:
        return stripped
    return None
