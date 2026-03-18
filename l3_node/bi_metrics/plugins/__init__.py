"""
BI 指标 — 内置插件

加载时自动注册到 registry
"""
from __future__ import annotations

from l3_node.bi_metrics.registry import register_data_source, register_outputter
from l3_node.bi_metrics.plugins.data_source_duckdb import DuckDBDataSource
from l3_node.bi_metrics.plugins.output_console import ConsoleOutputter
from l3_node.bi_metrics.plugins.output_markdown import MarkdownOutputter

register_data_source("duckdb", DuckDBDataSource)
register_outputter("console", ConsoleOutputter)
register_outputter("markdown", MarkdownOutputter)
