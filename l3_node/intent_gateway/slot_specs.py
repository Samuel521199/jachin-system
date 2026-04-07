"""
Intent Registry 必填槽位：在用户句 / routing_utterance / 分类面合并文本上做正则（或可扩展）检测。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, List

if TYPE_CHECKING:
    from l3_node.intent_gateway.bundle import GatewayContextBundle
    from l3_node.intent_gateway.registry import PreflightEntry


def combined_slot_probe_text(bundle: "GatewayContextBundle") -> str:
    """合并多路文本，避免只盯 user_input 漏掉扩写后的 routing_utterance。"""
    parts = [
        bundle.user_input or "",
        bundle.routing_utterance or "",
        bundle.classification_text or "",
    ]
    return "\n".join(p.strip() for p in parts if (p or "").strip())


def missing_required_slots(
    required_slots: list[dict[str, Any]] | None,
    probe_text: str,
) -> list[dict[str, Any]]:
    """
    required_slots 每项建议：name（必填）、pattern（正则，推荐）、prompt_template（追问模板）。
    无 pattern 时：退化为 probe 中是否包含 name 子串（弱匹配，仅适合演示）。
    """
    if not required_slots:
        return []
    text = probe_text or ""
    missing: list[dict[str, Any]] = []
    for raw in required_slots:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        pat = str(raw.get("pattern") or "").strip()
        if pat:
            try:
                ok = bool(re.search(pat, text, re.IGNORECASE | re.DOTALL))
            except re.error:
                ok = False
        else:
            ok = name.casefold() in text.casefold()
        if not ok:
            missing.append(raw)
    return missing


def get_missing_for_entry(entry: "PreflightEntry", bundle: "GatewayContextBundle") -> list[dict[str, Any]]:
    return missing_required_slots(entry.required_slots, combined_slot_probe_text(bundle))
