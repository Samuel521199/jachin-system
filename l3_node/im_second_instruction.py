"""
飞书 IM：用户在主任务尚未结束时发送第二条消息时的意图分流。

规则仲裁（原有）：多子句最高优先级合并，无 LLM 调用，延迟极低。
LLM 仲裁（AX，可选）：当 JACHIN_IM_LLM_CONFLICT_RESOLVE=1 时，在规则仲裁返回
"queue"（无法确定意图）时触发 LLM 二次裁决，考虑当前任务摘要 + 新指令全量上下文。
LLM 调用失败则自动回退规则结果，不影响主链路。

环境变量
--------
JACHIN_IM_LLM_CONFLICT_RESOLVE=1   开启 LLM 冲突仲裁（默认关）
JACHIN_IM_LLM_CONFLICT_MODEL=      仲裁用模型（默认 LLM_MODEL 或 qwen-turbo）
JACHIN_IM_LLM_CONFLICT_TIMEOUT=3   LLM 仲裁超时秒（默认 3s；超时退回规则结果）
"""
from __future__ import annotations

import logging
import os
import re
from typing import Literal

logger = logging.getLogger(__name__)

_INTERRUPT_HINTS = (
    "取消",
    "算了",
    "先停",
    "别做了",
    "停止",
    "不用了",
    "不要了",
    "打断",
    "cancel",
    "stop",
    "abort",
)
_PARALLEL_HINTS = (
    "同时",
    "顺便",
    "另外再",
    "并行",
)
# 场景四（路线图 §1.4 P2）：用户对**当前尚未结束的任务**的补充/纠正（短句 + 关键词，无 LLM）
_SUPPLEMENT_HINTS = (
    "补充一下",
    "补充",
    "更正",
    "纠正",
    "修正",
    "刚才",
    "改一下",
    "漏了",
    "还有一点",
    "另外说明",
    "附加",
    "补一句",
    "说错了",
    "不对",
    "应该是",
    "改成",
    "加上",
    "还得",
)


def _classify_busy_followup_clause(text: str) -> Literal["interrupt", "parallel", "supplement", "queue"]:
    """单句/单段文本上的启发式分类（多子句合并见 classify_busy_followup）。"""
    s = (text or "").strip()
    if not s:
        return "queue"
    low = s.lower()
    for hint in _INTERRUPT_HINTS:
        if hint.lower() in low or hint in s:
            return "interrupt"
    for hint in _PARALLEL_HINTS:
        if hint in s:
            return "parallel"
    if len(s) <= 360:
        for hint in _SUPPLEMENT_HINTS:
            if hint in s:
                return "supplement"
    return "queue"


def classify_busy_followup(text: str) -> Literal["interrupt", "parallel", "supplement", "queue"]:
    """主任务仍在执行时，用户续发一条的细分意图（打断 / 并行说明 / 补充纠正 / 排队）。

    多子句（换行或中英分号分隔）时取**最高优先级**子句：interrupt > parallel > supplement > queue，
    作为轻量「冲突仲裁」（非 LLM）。单段行为与旧版一致。
    """
    s = (text or "").strip()
    if not s:
        return "queue"
    chunks = [c.strip() for c in re.split(r"[\n\r；;]+", s) if c.strip()]
    if len(chunks) <= 1:
        return _classify_busy_followup_clause(s)
    _prio = {"interrupt": 0, "parallel": 1, "supplement": 2, "queue": 3}
    best: Literal["interrupt", "parallel", "supplement", "queue"] = "queue"
    best_p = 3
    for ch in chunks:
        k = _classify_busy_followup_clause(ch)
        p = _prio[k]
        if p < best_p:
            best, best_p = k, p
    return best


def analyze_second_im_intent(text: str) -> Literal["queue", "interrupt", "parallel"]:
    """第二条（及以后）消息相对第一条的粗粒度策略；补充类仍走排队链路，由上层注入 merge 提示。"""
    k = classify_busy_followup(text)
    if k == "interrupt":
        return "interrupt"
    if k == "parallel":
        return "parallel"
    return "queue"


# ---------------------------------------------------------------------------
# LLM 冲突仲裁（AX）
# ---------------------------------------------------------------------------

def _llm_conflict_enabled() -> bool:
    return (os.environ.get("JACHIN_IM_LLM_CONFLICT_RESOLVE") or "").strip().lower() in (
        "1", "true", "yes"
    )


def _llm_conflict_model() -> str:
    return (
        os.environ.get("JACHIN_IM_LLM_CONFLICT_MODEL")
        or os.environ.get("LLM_MODEL")
        or "qwen-turbo"
    ).strip()


def _llm_conflict_timeout() -> float:
    raw = (os.environ.get("JACHIN_IM_LLM_CONFLICT_TIMEOUT") or "3").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 3.0


_ARBITER_SYSTEM = """你是飞书智能助理的意图仲裁器。
用户正在执行一个任务时发来了新消息，你需要判断新消息的意图：

- interrupt（打断）：用户想要停止当前任务，转去处理新指令
- parallel（并行）：用户希望当前任务继续，同时处理新指令
- supplement（补充）：用户对当前任务做补充说明，应合并到当前任务
- queue（排队）：新指令与当前任务无关，等当前任务完成后再处理

请只输出四个词之一：interrupt / parallel / supplement / queue，不要输出其他任何内容。"""


async def classify_busy_followup_llm(
    new_text: str,
    *,
    current_task_summary: str = "",
) -> Literal["interrupt", "parallel", "supplement", "queue"]:
    """
    LLM 驱动的冲突仲裁（AX）。
    考虑当前任务摘要 + 新指令全量上下文，给出四分类结果。
    调用失败时返回规则仲裁的 fallback 结果。
    """
    rule_result = classify_busy_followup(new_text)
    if not _llm_conflict_enabled():
        return rule_result

    # 规则已确定高置信度时（interrupt/parallel），不再走 LLM（节省延迟）
    if rule_result in ("interrupt", "parallel"):
        return rule_result

    task_ctx = (current_task_summary or "").strip()
    user_msg = (
        f"当前正在执行的任务：{task_ctx[:300] or '（未知）'}\n\n"
        f"用户发来的新消息：{new_text[:400]}"
    )

    try:
        import asyncio
        from l3_node.llm_client import LiteLLMEngine

        engine = LiteLLMEngine(model=_llm_conflict_model())
        raw = await asyncio.wait_for(
            engine.generate_response(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=_ARBITER_SYSTEM,
                temperature=0.0,
                max_tokens=10,
            ),
            timeout=_llm_conflict_timeout(),
        )
        label = str(raw or "").strip().lower().split()[0] if raw else ""
        if label in ("interrupt", "parallel", "supplement", "queue"):
            logger.debug(
                "[IM_LLM_Arbiter] new=%r rule=%s llm=%s",
                new_text[:40], rule_result, label,
            )
            return label  # type: ignore[return-value]
    except Exception as e:
        logger.debug("[IM_LLM_Arbiter] LLM call failed (%s), fallback to rule result", e)

    return rule_result


def analyze_second_im_intent_llm_sync(
    text: str,
    *,
    current_task_summary: str = "",
) -> Literal["queue", "interrupt", "parallel"]:
    """
    同步版 LLM 仲裁（供非 async 上下文使用）。
    若 LLM 仲裁结果为 supplement 则映射到 queue（上层 dispatcher 统一处理）。
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 当前已在事件循环内（如 FastAPI），在线程池内运行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(
                    asyncio.run,
                    classify_busy_followup_llm(text, current_task_summary=current_task_summary),
                )
                k = fut.result(timeout=_llm_conflict_timeout() + 1.0)
        else:
            k = loop.run_until_complete(
                classify_busy_followup_llm(text, current_task_summary=current_task_summary)
            )
    except Exception as e:
        logger.debug("[IM_LLM_Arbiter] sync wrapper failed: %s", e)
        k = classify_busy_followup(text)

    if k == "interrupt":
        return "interrupt"
    if k == "parallel":
        return "parallel"
    return "queue"
