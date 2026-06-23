"""
Memory Nexus（SQLite + FastEmbed / MemPalace）与 L3 宿主之间的薄桥接：L0 统帅 Persona（Core_Profile）、
L1 唤醒块、深度检索展示、回合末异步提交、技能矩阵同步与动态工具检索。

失败须 fail-open，不阻塞对话主路径。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

SKILL_MATRIX_WING = "System_Core"
SKILL_MATRIX_ROOM = "Skill_Matrix"

PERSONA_USER_WING = "User_Persona"
PERSONA_CORE_PROFILE_ROOM = "Core_Profile"


def build_l0_persona_block() -> str:
    """
    L0：从 User_Persona/Core_Profile 取 **最新一条** 统帅行为侧写（recall_room 按 timestamp 降序）。
    失败返回空串，fail-open。
    """
    try:
        from l3_client.local_mcps.jachin_memory_nexus.memory_backend import recall_room
    except Exception as e:
        logger.debug("[Memory Nexus] L0 Core_Profile 导入失败: %s", e)
        return ""

    try:
        res = recall_room(wing=PERSONA_USER_WING, room=PERSONA_CORE_PROFILE_ROOM, limit=1)
    except Exception as e:
        logger.debug("[Memory Nexus] L0 Core_Profile recall 失败: %s", e)
        return ""

    if not res.get("ok"):
        return ""
    drawers = res.get("drawers") or []
    if not drawers:
        return ""
    doc = (drawers[0].get("text") or "").strip()
    return doc


# 动态检索关闭时无需；开启时与 Top-K 结果一并始终保留的工具 id
_DEFAULT_ALWAYS_INCLUDE_TOOL_IDS: frozenset[str] = frozenset({
    "core:local_memory_search",
    "core:local_memory_append",
})

_TOOL_NAME_LINE_RE = re.compile(r"^Tool Name:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def build_l1_system_memory_block(
    *,
    recall_room_fn: Callable[..., dict[str, Any]],
) -> str:
    """
    L1：从固定 Wing/Room 拉取近期抽屉文本，拼成 system prompt 可用的短块。
    """
    parts: list[str] = []
    try:
        mon = recall_room_fn(wing="E2E_Monitors", room="Kalaroko_Default", limit=2)
        if mon.get("ok") and mon.get("drawers"):
            for d in mon["drawers"]:
                meta = d.get("metadata") or {}
                ts = meta.get("timestamp") or ""
                doc = (d.get("text") or "").strip()
                if not doc:
                    continue
                snippet = doc[:200] + ("…" if len(doc) > 200 else "")
                parts.append(f"- [系统巡检 {ts}] {snippet}")
    except Exception as e:
        logger.debug("[Memory Nexus] L1 巡检块失败: %s", e)

    try:
        persona = recall_room_fn(wing="User_Persona", room="General_Chat", limit=3)
        if persona.get("ok") and persona.get("drawers"):
            for d in persona["drawers"]:
                meta = d.get("metadata") or {}
                ts = meta.get("timestamp") or ""
                doc = (d.get("text") or "").strip()
                if not doc:
                    continue
                parts.append(f"- [用户交互 {ts}] {doc}")
    except Exception as e:
        logger.debug("[Memory Nexus] L1 用户画像块失败: %s", e)

    if not parts:
        return ""
    return "【系统近期核心记忆】\n" + "\n".join(parts) + "\n"


def _nexus_prompt_io_timeout_sec() -> float:
    """L0/L1 注入 Memory Nexus 读路径硬上限，超时 fail-open，避免卡死主对话。"""
    raw = (os.environ.get("JACHIN_MEMORY_NEXUS_PROMPT_TIMEOUT_SEC") or "2").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 2.0
    return max(0.25, min(v, 60.0))


_L0_L1_HARD_TIMEOUT_SEC = 15.0


async def async_build_l0_persona_block() -> str:
    """在独立线程中执行 L0；``wait_for(15s)`` 包裹 ``to_thread``，防止 SQLite/嵌入假死导致协程永不返回。"""
    _cap = _L0_L1_HARD_TIMEOUT_SEC
    try:
        return await asyncio.wait_for(asyncio.to_thread(build_l0_persona_block), timeout=_cap)
    except asyncio.TimeoutError:
        logger.warning(
            "[Memory Nexus] async L0 硬超时（%.0fs），已跳过以免阻塞主流程",
            _cap,
        )
        return ""
    except Exception as e:
        logger.warning("[Memory Nexus] async L0 提取异常: %s", e)
        return ""


async def async_build_l1_system_memory_block(
    recall_room_fn: Callable[..., dict[str, Any]],
) -> str:
    """在独立线程中执行 L1；``wait_for(15s)`` 包裹 ``to_thread``。"""
    _cap = _L0_L1_HARD_TIMEOUT_SEC
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(build_l1_system_memory_block, recall_room_fn=recall_room_fn),
            timeout=_cap,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[Memory Nexus] async L1 硬超时（%.0fs），已跳过以免阻塞主流程",
            _cap,
        )
        return ""
    except Exception as e:
        logger.warning("[Memory Nexus] async L1 提取异常: %s", e)
        return ""


def format_deep_search_matches_for_agent(res: dict[str, Any]) -> str:
    """将 deep_search 结果格式化为可供 Observation 展示的纯文本。"""
    if not res.get("ok"):
        err = res.get("error") or "unknown"
        return f"[memory_nexus] 检索失败（底层 error 字段）: {err}"
    matches = res.get("matches") or []
    if not matches:
        return "[memory_nexus] 未找到相关记忆。"
    lines: list[str] = []
    for i, m in enumerate(matches, 1):
        meta = m.get("metadata") or {}
        wing = meta.get("wing") or "?"
        room = meta.get("room") or "?"
        dist = m.get("distance")
        rid = m.get("id") or ""
        text = (m.get("text") or "").strip()
        dist_s = ""
        if dist is not None:
            try:
                dist_s = f" distance={float(dist):.4f}"
            except (TypeError, ValueError):
                dist_s = f" distance={dist!r}"
        lines.append(f"{i}. wing={wing} room={room} id={rid}{dist_s}\n{text}")
    return "\n---\n".join(lines)


def _assistant_reply_too_low_value_for_nexus(ar: str) -> bool:
    """有界退出 / 空输出 / 极短套话等不适合写入 General_Chat 记忆。"""
    s = (ar or "").strip()
    if not s:
        return True
    sl = s.lstrip()
    if sl.startswith("[ExecutionBrief]"):
        return True
    if sl.startswith("[未产出回复]"):
        return True
    if sl.startswith("【需要补充信息】"):
        return True
    if sl.startswith("[System]") and len(s) < 160:
        return True
    s_low = s.lower()
    if len(s) <= 8 and s_low in ("ok", "okay", "好的", "收到", "嗯", "好", "行", "嗯嗯"):
        return True
    return False


def schedule_nexus_turn_commit_async(user_message: str, assistant_reply: str) -> None:
    """回合结束后异步写入 User_Persona / General_Chat；不阻塞、不向上抛错。"""
    um = (user_message or "").strip()
    ar = (assistant_reply or "").strip()
    try:
        raw = (os.environ.get("JACHIN_NEXUS_TURN_COMMIT_SKIP_CHITCHAT") or "1").strip().lower()
        _skip_chitchat = raw not in ("0", "false", "no", "off")
        if _skip_chitchat:
            from l3_node.routing.output_format_signals import heuristic_trivial_chitchat_only

            if heuristic_trivial_chitchat_only(um):
                logger.debug("[Memory Nexus] turn commit 跳过（纯寒暄/致谢，JACHIN_NEXUS_TURN_COMMIT_SKIP_CHITCHAT）")
                return
    except Exception:
        pass
    try:
        raw_lv = (os.environ.get("JACHIN_NEXUS_TURN_COMMIT_SKIP_LOW_VALUE") or "1").strip().lower()
        _skip_lv = raw_lv not in ("0", "false", "no", "off")
        if _skip_lv and _assistant_reply_too_low_value_for_nexus(ar):
            logger.debug("[Memory Nexus] turn commit 跳过（低价值助手回复，JACHIN_NEXUS_TURN_COMMIT_SKIP_LOW_VALUE）")
            return
    except Exception:
        pass
    if len(um) <= 10 and len(ar) <= 50:
        return
    text = f"User: {um[:12000]}\nJachin: {ar[:12000]}"

    async def _commit() -> None:
        try:
            from l3_client.local_mcps.jachin_memory_nexus.memory_backend import commit_drawer

            await asyncio.to_thread(commit_drawer, text, "User_Persona", "General_Chat")
        except Exception as e:
            logger.debug("[Memory Nexus] turn commit 跳过: %s", e, exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_commit())
    except RuntimeError:
        logger.debug("[Memory Nexus] turn commit 无运行中事件循环，跳过")


def _stable_skill_matrix_drawer_id(tool_id: str) -> str:
    return "sm_" + hashlib.sha256(tool_id.encode("utf-8")).hexdigest()[:48]


def _tool_dict_to_index_text(tool: dict[str, Any]) -> tuple[str, str]:
    """返回 (tool_id, 入库正文)。"""
    tid = str(tool.get("id") or tool.get("label") or "").strip()
    if not tid:
        return "", ""
    desc = str(tool.get("desc") or tool.get("description") or "").strip()
    schema_bits: dict[str, Any] = {}
    for k in ("params", "input_schema", "schema"):
        v = tool.get(k)
        if v is not None:
            schema_bits[k] = v
    schema_str = json.dumps(schema_bits, ensure_ascii=False)[:8000]
    text = f"Tool Name: {tid}\nDescription: {desc}\nSchema: {schema_str}"
    return tid, text


def sync_all_tools_to_nexus(all_tools: Sequence[dict[str, Any]] | None) -> dict[str, Any]:
    """
    将当前可见工具表写入 Skill_Matrix：先清空 ``System_Core`` / ``Skill_Matrix``，再按稳定 id upsert。

    失败返回 ``{"ok": False, "error": ...}``；成功 ``{"ok": True, "count": n}``。
    """
    if not all_tools:
        return {"ok": True, "count": 0, "message": "empty tools"}

    try:
        from l3_client.local_mcps.jachin_memory_nexus.memory_backend import (
            delete_drawers_in_room,
            upsert_drawer,
        )
    except Exception as e:
        return {"ok": False, "error": repr(e), "count": 0}

    try:
        delete_drawers_in_room(SKILL_MATRIX_WING, SKILL_MATRIX_ROOM)
    except Exception as e:
        logger.warning("[Skill Matrix] 清空旧抽屉失败（继续 upsert 可能重复语义）：%s", e)

    n_ok = 0
    errors: list[str] = []
    for tool in all_tools:
        if not isinstance(tool, dict):
            continue
        tid, text = _tool_dict_to_index_text(tool)
        if not tid or not text:
            continue
        drawer_id = _stable_skill_matrix_drawer_id(tid)
        try:
            upsert_drawer(
                drawer_id,
                text,
                SKILL_MATRIX_WING,
                SKILL_MATRIX_ROOM,
                extra_meta={"tool_name": tid},
            )
            n_ok += 1
        except Exception as ex:
            errors.append(f"{tid}: {ex!r}")

    if errors:
        logger.warning("[Skill Matrix] 部分工具写入失败 (%d/%d): %s", len(errors), len(all_tools), errors[:5])
    logger.info("[Skill Matrix] 已同步 %d 条工具描述至 Nexus（输入条目=%d）", n_ok, len(all_tools))
    return {"ok": True, "count": n_ok, "errors": errors[:16]}


def dynamic_tool_retrieval_enabled() -> bool:
    return (os.environ.get("JACHIN_DYNAMIC_TOOL_RETRIEVAL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _match_tool_ids_from_deep_search(matches: Iterable[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in matches:
        meta = m.get("metadata") if isinstance(m, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        tid = str(meta.get("tool_name") or "").strip()
        if not tid:
            doc = str(m.get("text") or "")
            mm = _TOOL_NAME_LINE_RE.search(doc)
            if mm:
                tid = mm.group(1).strip()
        if tid and tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def _filter_tools_for_dynamic_retrieval_sync(
    tools: list[dict[str, Any]],
    user_input: str,
    *,
    wing: str = SKILL_MATRIX_WING,
    limit: int = 5,
    always_include_ids: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """
    同步实现：按用户 query 在 Skill_Matrix 上 deep_search，仅保留 Top-K 命中工具 + 必选工具；任意失败 **返回原列表**（fail-open）。
    """
    if not tools:
        return tools
    q = (user_input or "").strip()
    if not q:
        return tools

    inc = always_include_ids if always_include_ids is not None else _DEFAULT_ALWAYS_INCLUDE_TOOL_IDS

    try:
        from l3_client.local_mcps.jachin_memory_nexus.memory_backend import deep_search

        res = deep_search(query=q, wing=wing, limit=max(1, min(int(limit), 50)))
    except Exception as e:
        logger.debug("[Skill Matrix] deep_search 异常，保持全量工具: %s", e)
        return tools

    if not res.get("ok"):
        logger.debug("[Skill Matrix] deep_search 失败，保持全量工具: %s", res.get("error"))
        return tools

    matches = res.get("matches") or []
    if not matches:
        logger.debug("[Skill Matrix] deep_search 无命中，保持全量工具")
        return tools

    ranked_ids = _match_tool_ids_from_deep_search(matches)
    if not ranked_ids:
        return tools

    pool = {str(t.get("id") or "").strip(): t for t in tools if isinstance(t, dict) and t.get("id")}
    picked: list[dict[str, Any]] = []
    picked_ids: set[str] = set()

    lim = max(1, min(int(limit), 50))
    for tid in ranked_ids[:lim]:
        t = pool.get(tid)
        if t is not None:
            picked.append(t)
            picked_ids.add(tid)

    for aid in sorted(inc):
        if aid in pool and aid not in picked_ids:
            picked.append(pool[aid])
            picked_ids.add(aid)

    if not picked:
        return tools

    logger.info(
        "[Skill Matrix] 动态检索：保留 %d 个工具（Top-%d 语义命中 + 必选合并，原池=%d）",
        len(picked),
        lim,
        len(tools),
    )
    return picked


# 兼容旧名：单元测试 / 脚本可能仍 import filter_tools_for_dynamic_retrieval
filter_tools_for_dynamic_retrieval = _filter_tools_for_dynamic_retrieval_sync


def _dynamic_tool_retrieval_async_timeout_sec() -> float:
    raw = (os.environ.get("JACHIN_DYNAMIC_TOOL_RETRIEVAL_ASYNC_TIMEOUT_SEC") or "10").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 10.0
    return max(0.5, min(v, 120.0))


async def async_filter_tools_for_dynamic_retrieval(
    tools: list[dict[str, Any]],
    user_input: str,
    *,
    wing: str = SKILL_MATRIX_WING,
    limit: int = 5,
    always_include_ids: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """
    异步包装：在独立线程中执行动态工具检索 + ``wait_for`` 硬熔断，避免阻塞主事件循环或永久挂起。
    失败 fail-open 返回全量 ``tools``。
    """
    import functools

    if not tools:
        return tools
    _tmo = _dynamic_tool_retrieval_async_timeout_sec()
    _fn = functools.partial(
        _filter_tools_for_dynamic_retrieval_sync,
        tools,
        user_input,
        wing=wing,
        limit=limit,
        always_include_ids=always_include_ids,
    )
    try:
        return await asyncio.wait_for(asyncio.to_thread(_fn), timeout=_tmo)
    except asyncio.TimeoutError:
        logger.warning(
            "[Memory Nexus] 动态技能路由硬超时（%.1fs），回退全量工具池",
            _tmo,
        )
        return tools
    except Exception as e:
        logger.warning("[Memory Nexus] 动态技能路由失败，回退全量工具池: %s", e)
        return tools
