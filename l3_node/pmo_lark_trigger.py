"""
PMO Lark 双重触发器

精确触发：/pmo、执行PMO看板、全量看板 等固定短语
  → 直接以 pmo_copilot_cli 信道触发 PMO Skill（含完整 SOP 约束与推送守卫）

模糊触发：帮我看看进度、项目情况怎么样 等模糊意图
  → 发送飞书交互卡片提供三个选项；用户回复 1/2/3 或关键词后再触发重型任务

卡片等待机制：
  card_pending[chat_id] 存放 (timestamp, source_msg)，TTL 5 分钟。
  下一条消息若命中 "1/2/3" 或对应标签文本，触发对应 PMO 任务。

动作映射：
  1 / 全量看板 / 生成全量看板  →  分支 A 宏观看板（拉表 + 三表战报 + 推送两群）
  2 / 巡检异常 / 巡检异常人员  →  分支 B 变更预警（仅检查阻塞/逾期人员）
  3 / 简单问题 / 仅回答        →  普通 run_agent（不注入 PMO SKILL，避免重型工具调用）
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
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
# 正则：模糊意图（命中则发确认卡片）
# ──────────────────────────────────────────────────────────────────────────────
_PMO_FUZZY_RE = re.compile(
    r"帮我看.{0,10}(进度|项目|任务|看板)|"
    r"看.{0,8}(项目进度|进度|状态|任务情况)|"
    r"项目.{0,8}(情况|进度|怎么样|如何)|"
    r"现在.{0,12}(进度|项目|任务)|"
    r"有没有.{0,8}(进度|异常|阻塞)|"
    r"(进度|状态).{0,8}怎么样|"
    r"帮我.{0,8}(看看|查一下|检查一下|梳理一下).{0,20}(进度|项目|任务)|"
    r"最近.{0,10}(项目|进度|任务).{0,10}(情况|怎么|如何)",
    re.I,
)

# 这些词命中了 fuzzy 但不需要卡片（已由精确命中或其它路径处理）
_PMO_FUZZY_SKIP_RE = re.compile(
    r"/pmo|^pmo$|生成全量看板|宏观看板|分支\s*[AB]|atom_bi_project|lark_notifier",
    re.I,
)

# ──────────────────────────────────────────────────────────────────────────────
# 卡片回复词典
# ──────────────────────────────────────────────────────────────────────────────
# action_key → (选项序号, 用户可能回复的关键词列表)
_CARD_ACTIONS: dict[str, tuple[str, list[str]]] = {
    "full_board": (
        "1",
        ["1", "1️⃣", "全量看板", "生成全量看板", "宏观看板", "全量", "分支a", "分支A", "branch a", "看板"],
    ),
    "anomaly": (
        "2",
        ["2", "2️⃣", "巡检异常", "巡检异常人员", "异常人员", "异常检查", "分支b", "分支B", "branch b", "巡检"],
    ),
    "simple": (
        "3",
        ["3", "3️⃣", "简单问题", "仅回答", "仅回答简单问题", "直接回答", "简单", "不拉表"],
    ),
}

# TTL 300 秒（5 分钟）
_CARD_PENDING_TTL_SEC = 300.0

# chat_id → (timestamp, source_user_text)
_card_pending: dict[str, tuple[float, str]] = {}
_card_pending_lock = threading.Lock()


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
    try:
        from l3_node.paths import get_app_root
        root = get_app_root()
    except Exception:
        root = Path(__file__).parent.parent
    jachin = Path(__file__).parent.parent / ".."  # 简单 fallback

    candidates = [
        root / "skills_repo" / "pmo-copilot" / "SKILL.md",
        Path.home() / ".jachin" / "l3_skill_cache" / "pmo-copilot" / "SKILL.md",
        Path.home() / ".jachin" / "l3_mcp_cache" / "com.jachin.pmo-copilot" / "SKILL.md",
    ]
    for p in candidates:
        if p.is_file():
            return p.resolve()
    return None


# ──────────────────────────────────────────────────────────────────────────────
# 飞书卡片构建
# ──────────────────────────────────────────────────────────────────────────────

def _build_pmo_confirm_card(source_text: str = "") -> dict[str, Any]:
    """
    构建 PMO 操作确认卡片（Lark Interactive Card JSON 1.0）。
    三个操作选项以视觉区块呈现，提示用户回复 1/2/3 触发。
    """
    preview = (source_text.strip()[:60] + "…") if len(source_text.strip()) > 60 else source_text.strip()
    hint_prefix = f'检测到意图：「{preview}」\n\n' if preview else ""

    return {
        "config": {
            "wide_screen_mode": True,
            "enable_forward": False,
        },
        "header": {
            "title": {"tag": "plain_text", "content": "📊 PMO 看板 — 请选择操作"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"{hint_prefix}"
                        "请回复序号确认要执行的操作：\n\n"
                        "**1️⃣  生成全量看板**\n"
                        "拉取所有飞书多维表，生成需求进度全览 + 人员任务矩阵 + 版本需求映射，推送到 PMO 主群与监控群。\n\n"
                        "**2️⃣  巡检异常人员**\n"
                        "仅检查当前任务状态，标记阻塞 / 逾期 / 空载人员，输出告警摘要（分支 B 预警）。\n\n"
                        "**3️⃣  仅回答简单问题**\n"
                        "不拉远端表，直接用已有上下文回答你的问题。"
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "💡 回复 1、2 或 3（或对应关键词）确认操作，有效期 5 分钟",
                    }
                ],
            },
        ],
    }


def _send_pmo_confirm_card(
    chat_id: str,
    source_text: str,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    api_base: str | None = None,
) -> bool:
    """发送 PMO 确认卡片，返回是否成功。"""
    try:
        from l3_node.channels.lark.im import send_interactive_card

        card = _build_pmo_confirm_card(source_text)
        result = send_interactive_card(
            chat_id,
            card,
            app_id=app_id,
            app_secret=app_secret,
            api_base=api_base,
        )
        return result.get("status") == "success"
    except Exception as e:
        logger.warning("[PMO Trigger] 卡片发送失败 chat_id=%s: %s", (chat_id or "")[:24], e)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# 卡片待确认状态管理
# ──────────────────────────────────────────────────────────────────────────────

def _mark_card_pending(chat_id: str, source_text: str) -> None:
    with _card_pending_lock:
        _card_pending[chat_id] = (time.monotonic(), source_text)


def _clear_card_pending(chat_id: str) -> None:
    with _card_pending_lock:
        _card_pending.pop(chat_id, None)


def _check_card_reply(text: str, chat_id: str) -> str | None:
    """
    若该 chat 有待确认卡片且本条消息是有效回复，返回 action_key；否则返回 None。
    同时清理超时条目。
    """
    with _card_pending_lock:
        now = time.monotonic()
        # 清理超时
        stale = [k for k, (ts, _) in _card_pending.items() if now - ts > _CARD_PENDING_TTL_SEC]
        for k in stale:
            del _card_pending[k]

        if chat_id not in _card_pending:
            return None

    # 卡片存在，尝试匹配动作
    norm = (text or "").strip().lower()
    for action_key, (_, keywords) in _CARD_ACTIONS.items():
        for kw in keywords:
            if norm == kw.lower() or norm == kw:
                return action_key
    return None


# ──────────────────────────────────────────────────────────────────────────────
# action_key → PMO 用户消息映射
# ──────────────────────────────────────────────────────────────────────────────

_ACTION_MESSAGES: dict[str, str] = {
    "full_board": (
        "请严格按系统提示中的 PMO-Copilot SKILL：按「分支 A / 定时宏观看板」"
        "拉取 §1.1 全部种子链接并汇总，生成包含三张核心表的 Markdown 战报，"
        "并通过 mcp:atom_lark_notifier 推送到 PMO 主群与监控群。"
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
    implicit: dict[str, Any] = {
        "channel": "pmo_copilot_cli",
        "source": "pmo_lark_trigger",
        "lark_chat_id": chat_id,
    }

    bundle = build_gateway_bundle(
        user_input=pmo_user_msg,
        short_memory_context="",
        correlation_id=None,
        implicit_attribution=implicit,
    )

    try:
        from l3_node.intent_gateway.gateway_pipeline import apply_gateway_ingress_pipeline

        await apply_gateway_ingress_pipeline(bundle, pmo_user_msg, [], run_id="")
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
    return answer or ""


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
    PMO 双重触发器主入口。

    返回值：
      - str  → 已处理，返回给用户的回复（dispatcher 直接使用，不再走 run_agent）
      - None → 未命中，dispatcher 继续走后续逻辑
    """
    norm = (text or "").strip()
    if not norm:
        return None

    cid = (chat_id or "").strip()

    # ── 优先检查：是否是对已发出卡片的回复 ──
    action_from_card = _check_card_reply(norm, cid) if cid else None
    if action_from_card is not None:
        _clear_card_pending(cid)
        logger.info(
            "[PMO Trigger] 卡片回复 action=%s chat_id=%s text=%r",
            action_from_card,
            cid[:24],
            norm[:40],
        )

        if action_from_card == "simple":
            # 不注入 SKILL，交回正常 run_agent 流程
            return None

        # 重型 PMO 任务
        ack = {
            "full_board": "⏳ PMO 全量看板任务已启动，正在拉取飞书多维表，约需 1-3 分钟，战报将直接推送到群内…",
            "anomaly": "⏳ PMO 巡检任务已启动，正在检查任务状态，约需 1-2 分钟…",
        }.get(action_from_card, "⏳ PMO 任务已启动，请稍候…")

        if cid:
            send_reply_fn(cid, ack)

        try:
            future = asyncio.run_coroutine_threadsafe(
                _run_pmo_skill_coro(action_from_card, norm, engine, session_messages, cid),
                loop,
            )
            return future.result(timeout=None) or ""
        except Exception as e:
            logger.exception("[PMO Trigger] PMO 任务执行失败 action=%s: %s", action_from_card, e)
            return f"⚠️ PMO 任务执行出错：{e}"

    # ── 精确触发 ──
    if _PMO_EXACT_RE.search(norm):
        logger.info("[PMO Trigger] 精确触发 chat_id=%s text=%r", cid[:24], norm[:60])

        if cid:
            send_reply_fn(cid, "⏳ PMO 全量看板任务已启动，正在拉取飞书多维表，约需 1-3 分钟，战报将直接推送到群内…")

        # 解析精确命令中是否附带了自定义消息
        cmd_msg = _extract_exact_custom_msg(norm) or _PMO_EXACT_DEFAULT_MSG

        try:
            future = asyncio.run_coroutine_threadsafe(
                _run_pmo_skill_coro("full_board", cmd_msg, engine, session_messages, cid),
                loop,
            )
            return future.result(timeout=None) or ""
        except Exception as e:
            logger.exception("[PMO Trigger] 精确 PMO 任务执行失败: %s", e)
            return f"⚠️ PMO 任务执行出错：{e}"

    # ── 模糊触发 ──
    if _PMO_FUZZY_RE.search(norm) and not _PMO_FUZZY_SKIP_RE.search(norm):
        logger.info("[PMO Trigger] 模糊意图，发送确认卡片 chat_id=%s text=%r", cid[:24], norm[:60])

        if cid:
            sent = _send_pmo_confirm_card(cid, norm, app_id=app_id, app_secret=app_secret, api_base=api_base)
            if sent:
                _mark_card_pending(cid, norm)
                return (
                    "（以上卡片请选择操作，或直接回复 1 / 2 / 3；"
                    "若卡片未显示请回复"生成全量看板"或"巡检异常人员"）"
                )
            else:
                # 卡片发送失败，降级为文字提示
                _mark_card_pending(cid, norm)
                return (
                    "检测到 PMO 相关意图，请回复数字确认操作：\n"
                    "**1** 生成全量看板（拉表 + 推送战报）\n"
                    "**2** 巡检异常人员（分支 B 预警）\n"
                    "**3** 仅回答简单问题（不拉表）"
                )

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
