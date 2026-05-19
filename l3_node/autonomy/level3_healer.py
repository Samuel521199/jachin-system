"""
Level 3 自愈引擎（AQ）—— Experience RAG 辅助诊断

当 PersistedIntent 连续失败次数超过阈值时，Level 3 自愈引擎：
1. 从 Experience RAG 检索与失败意图相似的历史成功案例
2. 构建诊断报告：失败摘要 + 历史成功路径对比 + 建议修复方向
3. 通过飞书推送详细自愈报告
4. 可选：若 JACHIN_LEVEL3_AUTO_APPLY=1，自动将成功路径的工具/参数
   注入意图的 metadata，下次触发时优先使用（light-inject 策略）
5. 可选：若 JACHIN_SKILL_EVOLVE_ENABLE=1 且能识别关联 Skill，
   将 RAG 证据预存为进化候选（healing 路径），下次成功时立即应用 Skill 进化

层级对比：
  Level 2（AK）：detect → reset → plain notify
  Level 3（AQ）：detect → diagnose(Experience RAG) → rich notify + optional auto-inject
               + optional skill_evolution staging（AY healing 路径）

环境变量
--------
JACHIN_LEVEL3_HEALER_ENABLE=1         开启 Level 3 自愈（默认关）
JACHIN_LEVEL3_FAILURE_THRESHOLD=3     触发 Level 3 诊断的连续失败次数阈值（默认 3）
JACHIN_LEVEL3_RAG_TOP_K=3             检索历史成功案例数（默认 3）
JACHIN_LEVEL3_AUTO_APPLY=0            是否自动将历史成功路径注入意图 metadata（默认关）
JACHIN_SKILL_EVOLVE_ENABLE=1          开启后，诊断成功时同步预存 Skill 进化候选（AY）
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("autonomy.level3_healer")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def level3_healer_enabled() -> bool:
    return (os.environ.get("JACHIN_LEVEL3_HEALER_ENABLE") or "").strip().lower() in (
        "1", "true", "yes"
    )


def _failure_threshold() -> int:
    raw = (os.environ.get("JACHIN_LEVEL3_FAILURE_THRESHOLD") or "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _rag_top_k() -> int:
    raw = (os.environ.get("JACHIN_LEVEL3_RAG_TOP_K") or "3").strip()
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 3


def _auto_apply_enabled() -> bool:
    return (os.environ.get("JACHIN_LEVEL3_AUTO_APPLY") or "0").strip() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# 诊断结果
# ---------------------------------------------------------------------------

@dataclass
class HealingDiagnosis:
    intent_id: str
    intent_description: str
    consecutive_failures: int
    last_error: str
    similar_successes: list[dict[str, Any]] = field(default_factory=list)
    suggested_tools: list[str] = field(default_factory=list)
    suggested_action: str = ""
    auto_applied: bool = False

    def format_report(self) -> str:
        """生成发往飞书的 Level 3 自愈报告文本。"""
        lines = [
            f"[Level 3 自愈诊断] 意图「{self.intent_description[:60]}」（{self.intent_id[:12]}…）",
            f"  连续失败次数：{self.consecutive_failures}",
            f"  最后错误摘要：{self.last_error[:200]}",
        ]
        if self.similar_successes:
            lines.append(f"\n  Experience RAG 命中 {len(self.similar_successes)} 条历史成功案例：")
            for i, s in enumerate(self.similar_successes, 1):
                ui_preview = str(s.get("user_intent") or "")[:80]
                tool = str(s.get("executed_tool") or "unknown")
                pl = s.get("action_payload") or {}
                pl_preview = str(pl)[:120]
                lines.append(f"  [{i}] 意图: {ui_preview}")
                lines.append(f"      工具: {tool}  参数摘要: {pl_preview}")
        if self.suggested_tools:
            lines.append(f"\n  建议优先尝试工具：{', '.join(self.suggested_tools[:5])}")
        if self.suggested_action:
            lines.append(f"  修复建议：{self.suggested_action}")
        if self.auto_applied:
            lines.append("  ✓ 已自动将首条成功路径注入意图 metadata，下次触发时优先使用。")
        else:
            lines.append("  → 请人工确认并酌情调整意图参数（JACHIN_LEVEL3_AUTO_APPLY=1 可开启自动注入）。")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 核心诊断逻辑
# ---------------------------------------------------------------------------

def diagnose_failed_intent(
    intent_id: str,
    intent_description: str,
    consecutive_failures: int,
    last_error: str,
) -> HealingDiagnosis | None:
    """
    对失败意图进行 Level 3 诊断。
    未达阈值、未开启或 RAG 不可用时返回 None。
    """
    if not level3_healer_enabled():
        return None
    if consecutive_failures < _failure_threshold():
        return None

    diagnosis = HealingDiagnosis(
        intent_id=intent_id,
        intent_description=intent_description,
        consecutive_failures=consecutive_failures,
        last_error=last_error,
    )

    # ── 1. 检索历史成功案例 ──────────────────────────────────────────────────
    try:
        from l3_node.experience_memory import retrieve_experience
        hits = retrieve_experience(intent_description, top_k=_rag_top_k())
        diagnosis.similar_successes = hits
    except Exception as e:
        logger.debug("[Level3Healer] Experience RAG 检索失败: %s", e)
        hits = []

    # ── 2. 提取建议工具列表 ──────────────────────────────────────────────────
    seen_tools: list[str] = []
    for hit in hits:
        t = str(hit.get("executed_tool") or "")
        if t and t not in seen_tools:
            seen_tools.append(t)
    diagnosis.suggested_tools = seen_tools[:5]

    # ── 3. 生成修复建议文本 ──────────────────────────────────────────────────
    if hits:
        first = hits[0]
        best_tool = str(first.get("executed_tool") or "")
        diagnosis.suggested_action = (
            f"参考历史案例，建议使用 {best_tool!r} 并检查参数格式是否与成功案例一致；"
            f"若意图描述过于宽泛，建议细化后重新保存。"
        )
    else:
        diagnosis.suggested_action = (
            "Experience RAG 未找到相似成功案例。建议：① 检查意图 action 的参数是否缺失；"
            "② 确认依赖的外部服务/权限是否正常；③ 适当降低意图复杂度。"
        )

    # ── 4. 可选：自动注入成功路径到意图 metadata ────────────────────────────
    if _auto_apply_enabled() and hits:
        try:
            _auto_inject_success_path(intent_id, hits[0])
            diagnosis.auto_applied = True
            logger.info(
                "[Level3Healer] auto-injected success path for intent %s tool=%s",
                intent_id,
                str(hits[0].get("executed_tool") or ""),
            )
        except Exception as e:
            logger.warning("[Level3Healer] auto-inject failed: %s", e)

    logger.info(
        "[Level3Healer] diagnosis complete intent_id=%s failures=%d hits=%d",
        intent_id,
        consecutive_failures,
        len(hits),
    )
    return diagnosis


def _auto_inject_success_path(intent_id: str, success_hit: dict[str, Any]) -> None:
    """
    将历史成功案例的工具 + 参数摘要写入意图的 metadata，
    下次 fire_intent 时由 agent_core 作为 hint 注入 system prompt。
    """
    from l3_node.autonomy.intent_persister import get_intent_persister

    persister = get_intent_persister()
    intent = persister.get(intent_id)
    if intent is None:
        return

    tool = str(success_hit.get("executed_tool") or "")
    payload = success_hit.get("action_payload") or {}
    hint = {
        "_level3_suggested_tool": tool,
        "_level3_payload_hint": str(payload)[:500],
        "_level3_injected_at": time.time(),
    }

    # 合并到现有 metadata（若无此字段则创建）
    existing_meta = intent.extra_meta or {}
    if not isinstance(existing_meta, dict):
        existing_meta = {}
    existing_meta.update(hint)

    persister.update_extra_meta(intent_id, existing_meta)


# ---------------------------------------------------------------------------
# 对外接口：awareness_loop 调用
# ---------------------------------------------------------------------------

async def run_level3_healing(
    intent_id: str,
    intent_description: str,
    consecutive_failures: int,
    last_error: str,
    skill_name: str = "",
) -> HealingDiagnosis | None:
    """
    异步入口（在 awareness_loop._execute_action 中 await 调用）。
    诊断完成后推送飞书报告；若开启 Skill 进化，同步预存进化候选。
    返回诊断结果供调用方记录。

    Parameters
    ----------
    skill_name : str
        可选。关联的 Skill 名称（如 'com.jachin.bi.analysis'）。
        若为空，系统尝试从 intent_description 中自动提取。
    """
    try:
        diagnosis = diagnose_failed_intent(
            intent_id=intent_id,
            intent_description=intent_description,
            consecutive_failures=consecutive_failures,
            last_error=last_error,
        )
        if diagnosis is None:
            return None

        report = diagnosis.format_report()
        logger.warning("[Level3Healer] %s", report)

        # ── AY healing 路径：预存进化候选 ──────────────────────────────────
        if diagnosis.similar_successes:
            _try_stage_skill_evolution(
                skill_name=skill_name or _extract_skill_name_from_intent(intent_description),
                intent_description=intent_description,
                last_error=last_error,
                success_hits=diagnosis.similar_successes,
            )

        # 通知飞书
        try:
            from l3_node.channels.lark.im import send_text_to_default_chat
            await send_text_to_default_chat(
                f"[Jachin 自主通知·level3_heal]\n{report[:1400]}"
            )
        except Exception as push_e:
            logger.debug("[Level3Healer] push feishu failed: %s", push_e)

        return diagnosis
    except Exception as e:
        logger.warning("[Level3Healer] run_level3_healing error: %s", e)
        return None


def _extract_skill_name_from_intent(intent_description: str) -> str:
    """从意图描述中尝试提取 Skill 名（用于自动关联 Skill 进化）。"""
    import re
    if not intent_description:
        return ""
    # 显式标记 SKILL:xxx
    m = re.search(r"SKILL:([a-zA-Z0-9._\-]+)", intent_description)
    if m:
        return m.group(1)
    # 反向域名 com.jachin.xxx
    m = re.search(r"(com\.[a-zA-Z0-9._\-]+)", intent_description)
    if m:
        return m.group(1)
    return ""


def _try_stage_skill_evolution(
    skill_name: str,
    intent_description: str,
    last_error: str,
    success_hits: list[dict[str, Any]],
) -> None:
    """
    若 Skill 自动进化已开启且 skill_name 有效，调用 stage_evolution_candidate 预存候选。
    静默失败，不阻塞主流程。
    """
    if not skill_name:
        return
    try:
        from l3_node.autonomy.skill_evolver import stage_evolution_candidate, evolve_enabled
        if not evolve_enabled():
            return
        staged = stage_evolution_candidate(
            skill_name=skill_name,
            failure_desc=intent_description,
            last_error=last_error,
            success_hits=success_hits,
        )
        if staged:
            logger.info(
                "[Level3Healer][AY] staged healing evolution for skill=%s (will apply on next success)",
                skill_name,
            )
    except Exception as e:
        logger.debug("[Level3Healer][AY] stage evolution failed: %s", e)
