"""
BI 指标查询 — 插件化架构

支持：多数据源、可配置指标、多输出格式
"""
from __future__ import annotations

from l3_node.bi_metrics.engine import run
from l3_node.bi_metrics.registry import (
    register_data_source,
    register_outputter,
    list_data_sources,
    list_outputters,
)

__all__ = ["run", "register_data_source", "register_outputter", "list_data_sources", "list_outputters"]
