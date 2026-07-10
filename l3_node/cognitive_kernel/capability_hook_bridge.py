"""Bridge capability hooks into structured WorkOrder suggestions.

Capability hooks are allowed to block, request confirmation, or suggest a next
tool path. They must not reintroduce text-form ``WorkOrder:`` protocols. This
module gives hooks a tiny structured envelope that the generic WorkOrder
adapter can consume.
"""

from __future__ import annotations

import json
import re
from typing import Any

_MARKER_RE = re.compile(
    r"<!--\s*jachin-kernel:work-order-suggestion\s+(\{.*?\})\s*-->",
    re.S,
)


def build_work_order_suggestion(
    *,
    tool: str,
    work_order_input: dict[str, Any] | str | None = None,
    reason: str = "",
    role_agent: str = "",
    visible_message: str = "Structured WorkOrder suggestion generated for the cognitive kernel.",
) -> str:
    payload = {
        "type": "work_order_suggestion",
        "tool": str(tool or "").strip(),
        "work_order_input": work_order_input if work_order_input is not None else {},
        "reason": str(reason or "").strip(),
        "role_agent": str(role_agent or "").strip(),
    }
    marker = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{visible_message.rstrip()}\n\n<!-- jachin-kernel:work-order-suggestion {marker} -->"


def extract_work_order_suggestion(text: str) -> dict[str, Any] | None:
    match = _MARKER_RE.search(str(text or ""))
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("type") != "work_order_suggestion":
        return None
    if not str(data.get("tool") or "").strip():
        return None
    return data
