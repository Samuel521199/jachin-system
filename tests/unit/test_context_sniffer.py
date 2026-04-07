"""context_sniffer：预算与格式化。"""
from __future__ import annotations

from l3_node.intent_gateway.context_sniffer import (
    _apply_total_budget,
    format_environment_report_for_prompt,
)


def test_apply_total_budget_respects_cap() -> None:
    merged = _apply_total_budget(
        "x" * 600,
        "y" * 800,
        "z" * 800,
        max_total=1500,
        max_git=500,
    )
    assert len(merged["git_combined"]) <= 500
    assert merged["total_chars"] <= 1500


def test_format_environment_report_for_prompt() -> None:
    s = format_environment_report_for_prompt(
        {
            "ok": True,
            "git": {"ok": True, "combined": "M foo"},
            "safety_lock_snippet": "no drop",
            "memory_excerpt": "- [t] hit",
        }
    )
    assert "[ENVIRONMENT_REPORT]" in s
    assert "Git" in s
    assert "安全锁" in s
