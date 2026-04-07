"""OutputFormatSignals 与语义缓存之间的序列化。"""
from __future__ import annotations

from typing import Any

from l3_node.routing.output_format_signals import OutputFormatSignals


def format_signals_to_dict(s: OutputFormatSignals) -> dict[str, Any]:
    return {
        "user_led_strict": s.user_led_strict,
        "prefer_json_object": s.prefer_json_object,
        "json_relaxed": s.json_relaxed,
    }


def format_signals_from_dict(d: dict[str, Any]) -> OutputFormatSignals:
    return OutputFormatSignals(
        user_led_strict=bool(d.get("user_led_strict")),
        prefer_json_object=bool(d.get("prefer_json_object")),
        json_relaxed=bool(d.get("json_relaxed")),
    )
