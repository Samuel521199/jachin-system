"""
§12.3 JIT 实体解析：工具调用前解析 resource_ref / 助理编号列表与当前 Action Input 中的序数指代。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LINE_NUM_RE = re.compile(
    r"^\s*(\d{1,3})[\.\、\)）]\s*(.{1,400})",
    re.UNICODE,
)
_ORD_INP_RE = re.compile(
    r"(?:第\s*(\d{1,3})\s*个|#\s*(\d{1,3})|（\s*(\d{1,3})\s*）)",
    re.UNICODE,
)


def _extract_numbered_map_from_assistant(messages: Optional[List[dict[str, Any]]]) -> dict[int, str]:
    out: dict[int, str] = {}
    if not messages:
        return out
    for m in reversed(messages):
        if (m.get("role") or "").strip().lower() != "assistant":
            continue
        raw = m.get("content")
        text = raw if isinstance(raw, str) else str(raw or "")
        for line in text.splitlines():
            mm = _LINE_NUM_RE.match(line.strip())
            if mm:
                try:
                    idx = int(mm.group(1))
                except ValueError:
                    continue
                if 1 <= idx <= 99:
                    out[idx] = mm.group(2).strip()[:300]
        if out:
            break
    return out


def _ordinal_from_inp(inp: str) -> Optional[int]:
    s = (inp or "").strip()
    if not s:
        return None
    m = _ORD_INP_RE.search(s)
    if not m:
        return None
    for g in m.groups():
        if g:
            try:
                v = int(g)
                if 1 <= v <= 99:
                    return v
            except ValueError:
                continue
    return None


async def jit_resolve_entity_refs(
    *,
    resource_ref_keys: list[str],
    tenant_id: str = "",
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    解析 resource_ref_keys 与 context 中的 messages：序数指代映射到助理列表项。
    无列表时仍返回桩字段，保证上游结构稳定。
    """
    ctx = dict(context or {})
    messages = ctx.get("messages")
    if not isinstance(messages, list):
        messages = None
    numbered = _extract_numbered_map_from_assistant(messages)
    inp = str(ctx.get("inp") or "")
    ord_hit = _ordinal_from_inp(inp)

    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        if bool(get_intent_gateway_config().get("jit_binding_log_only", True)):
            logger.debug(
                "[JITBinding] keys=%s tenant=%s numbered=%s ord_inp=%s tool=%s",
                resource_ref_keys[:8],
                tenant_id,
                list(numbered.keys())[:8],
                ord_hit,
                ctx.get("tool"),
            )
    except Exception:
        pass

    resolved: dict[str, Any] = {}
    for k in resource_ref_keys:
        entry: dict[str, Any] = {"resolved": True, "stub": True, "key": k}
        if ord_hit is not None and ord_hit in numbered:
            entry["ordinal_resolved"] = ord_hit
            entry["ordinal_text"] = numbered[ord_hit]
            entry["resolved"] = True
            entry["stub"] = False
        resolved[k] = entry
    if numbered:
        resolved["_numbered_items_preview"] = {str(i): t[:120] for i, t in sorted(numbered.items())[:12]}
    return resolved
