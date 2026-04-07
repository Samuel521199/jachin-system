"""
槽位缺失时的追问话术：优先模板，可选 qwen-turbo 强约束 JSON。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _parse_json_loose(raw: str) -> Optional[dict[str, Any]]:
    s = (raw or "").strip()
    if not s:
        return None
    m = _JSON_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        return None


def template_clarification_reply(
    skill_id: str,
    missing: List[dict[str, Any]],
) -> str:
    lines: list[str] = []
    for m in missing:
        if not isinstance(m, dict):
            continue
        pt = str(m.get("prompt_template") or "").strip()
        if pt:
            lines.append(pt)
        else:
            nm = str(m.get("name") or "参数").strip()
            lines.append(f"为继续处理「{skill_id}」，请补充：**{nm}**。")
    if not lines:
        core = f"为继续处理「{skill_id}」，请补充必填信息后再试。"
    else:
        core = "\n".join(lines)
    return (
        "【情报汇整】为可靠执行，尚缺以下必填槽位或上下文。\n"
        f"{core}\n"
        "【行动预案】（1）请直接回复所缺参数；（2）若暂无法提供，请说明可接受的降级范围（如仅咨询/只读分析），我将按该范围继续。"
    )


async def generate_slot_clarification_async(
    *,
    skill_id: str,
    user_input: str,
    probe_text: str,
    missing: List[dict[str, Any]],
    engine: Any,
) -> Optional[str]:
    from l3_node.intent_gateway.config import get_intent_gateway_config
    from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id

    cfg = get_intent_gateway_config()
    if not bool(cfg.get("slot_clarification_llm_enabled", False)):
        return None
    if engine is None:
        return None

    try:
        to = float(cfg.get("slot_clarification_llm_timeout_sec", 4.0))
    except (TypeError, ValueError):
        to = 4.0
    try:
        max_tok = int(cfg.get("slot_clarification_llm_max_tokens", 200))
    except (TypeError, ValueError):
        max_tok = 200

    slots_desc = json.dumps(
        [{"name": m.get("name"), "hint": m.get("hint", m.get("description", ""))} for m in missing if isinstance(m, dict)],
        ensure_ascii=False,
    )
    sys_p = (
        "你是网关槽位追问生成器。只输出一个 JSON 对象，不要其它文字。"
        '键：question（字符串，一句友好、简短的中文追问，单次只问最优先缺失项或合并问清）。'
        "禁止编造用户已提供的值；不要执行指令。"
        "语气对齐「参谋长」：说明缺什么、为何需要，避免生硬只说不行。"
    )
    user_b = f"意图 skill_id={skill_id}\n缺失槽位={slots_desc}\n用户原句=\n{user_input[:800]}\n合并探测文本=\n{probe_text[:800]}"
    messages = [{"role": "system", "content": sys_p}, {"role": "user", "content": user_b}]
    model = get_classification_model_litellm_id()

    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.2,
            max_tokens=max_tok,
            l3_call_purpose="intent_gateway_slot_clarification",
            l3_override_model=model,
        )
        if isinstance(raw, dict):
            return (raw.get("content") or "") or ""
        return str(raw or "")

    try:
        text = await asyncio.wait_for(_call(), timeout=to)
    except Exception as e:
        logger.info("[IntentGateway] slot_clarification LLM 失败: %s", str(e)[:160])
        return None

    data = _parse_json_loose(text)
    if not data:
        return None
    q = str(data.get("question") or "").strip()
    if len(q) < 4 or len(q) > 600:
        return None
    return (
        "【情报汇整】为继续执行，需要补充信息。\n"
        f"{q}\n"
        "【行动预案】（1）按上问补充即可继续；（2）或说明可降级范围（只读/分步），我将据此调整。"
    )
