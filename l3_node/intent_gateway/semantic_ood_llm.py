"""
L1.5 语义域 OOD：在 L0.5 代码规则放行后，由小模型判定「非业务域 / 闲聊 / 与系统职责无关」请求并拒答。
失败或 uncertain 时 fail-open，不阻断（韧性）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

Verdict = Literal["in_domain", "out_of_domain", "uncertain"]


@dataclass(frozen=True)
class SemanticOodResult:
    verdict: Verdict
    confidence: float
    reason_short: str


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


def parse_semantic_ood_response(raw: str) -> Optional[SemanticOodResult]:
    data = _parse_json_loose(raw)
    if not data:
        return None
    v = str(data.get("verdict") or data.get("label") or "").strip().lower()
    if v in ("ood_out_of_domain", "reject", "out_of_domain", "off_domain"):
        verdict: Verdict = "out_of_domain"
    elif v in ("in_domain", "accept", "ok", "allowed"):
        verdict = "in_domain"
    elif v in ("uncertain", "unknown", "maybe"):
        verdict = "uncertain"
    else:
        return None
    try:
        conf = float(data.get("confidence", data.get("score", 0.0)))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    rs = str(data.get("reason_short") or data.get("reason") or "")[:200]
    return SemanticOodResult(verdict=verdict, confidence=conf, reason_short=rs)


def get_semantic_ood_reject_reply() -> str:
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        m = str(get_intent_gateway_config().get("semantic_ood_reply_zh") or "").strip()
        if m:
            return m
    except Exception:
        pass
    return (
        "【语义网关】当前节点面向企业任务与系统协助（工具编排、数据与自动化、协作与排查等）。"
        "您的描述看起来与业务域无关或为日常闲聊/创作类请求，已在此层终止，不调用大模型。"
        "请用与工作或系统相关的具体目标重新描述（例如：要查什么数据、执行哪项任务、排查哪个错误）。"
    )


async def classify_semantic_ood_async(
    *,
    user_input: str,
    classification_text: str,
    engine: Any,
    timeout_sec: float,
    max_tokens: int,
    max_chars: int,
) -> Optional[SemanticOodResult]:
    from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id

    ui = (user_input or "").strip()
    ct = (classification_text or ui).strip()
    blob = ct if len(ct) >= len(ui) else f"{ui}\n---\n{ct}"
    if len(blob) > max_chars > 0:
        blob = blob[:max_chars]

    sys_p = (
        "你是企业级「任务与系统助手」入口的语义域分类器，只输出一个 JSON 对象，不要其它文字。\n"
        "键：verdict（字符串，必须是以下之一：in_domain | out_of_domain | uncertain）、"
        "confidence（0~1 小数）、reason_short（≤80 字中文简述）。\n"
        "in_domain：与工作、业务系统、自动化任务、数据分析、招聘/办公协作、技术排查、配置与工具调用相关的合理需求。\n"
        "out_of_domain：纯天气闲聊、让助手写诗/写小说、无工作语境的娱乐、与系统职责明显无关的索取；"
        "或明显越权/荒诞且非业务表述。\n"
        "uncertain：信息不足、可能相关也可能无关时。\n"
        "宁可标 uncertain，也不要在模糊时标 out_of_domain。"
    )
    user_block = f"【用户原句】\n{ui}\n\n【分类面文本】\n{blob}"
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_block},
    ]
    model = get_classification_model_litellm_id()

    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.0,
            max_tokens=max_tokens,
            l3_call_purpose="intent_gateway_semantic_ood",
            l3_override_model=model,
        )
        if isinstance(raw, dict):
            return (raw.get("content") or "") or ""
        return str(raw or "")

    try:
        text = await asyncio.wait_for(_call(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.info("[IntentGateway] semantic_ood LLM 超时 %.1fs", timeout_sec)
        return None
    except Exception as e:
        logger.info("[IntentGateway] semantic_ood LLM 失败: %s", str(e)[:200])
        return None

    return parse_semantic_ood_response(text)
