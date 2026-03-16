"""
BI 每日战报 — 主技能逻辑

流程: 收集(A) -> 对比提炼(B) -> LLM洞察(C) -> 分发(D)
设计规范: docs/bi_daily_report/03_SKILL_DESIGN.md
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_bi_daily_report(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    BI 每日战报主入口。

    步骤:
      A. 调用 mcp:atom_web_scraper 抓取昨日数据
      B. 读取 raw 文件，计算同环比、涨跌幅 -> metrics_data
      C. 将 metrics_data 喂给 LLM，生成战报 markdown
      D. 调用 mcp:atom_lark_notifier、mcp:atom_email_sender 分发

    Args:
        config: BiReportConfig，若为空则从 config/bi_daily_report.yaml 加载

    Returns:
        {"success": bool, "stage": str, "report_sent": bool, "lark_ok": bool, "email_ok": bool, "error": str}
    """
    # TODO: 实现完整流程，依赖 mcp:atom_web_scraper、atom_lark_notifier、atom_email_sender
    logger.info("[BI Daily Report] run_bi_daily_report 占位实现，待 MCP 工具就绪后接入")
    return {
        "success": False,
        "stage": "stub",
        "report_sent": False,
        "lark_ok": False,
        "email_ok": False,
        "error": "占位实现，MCP 工具尚未接入",
    }
