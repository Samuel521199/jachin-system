"""
BI 指标 — 插件基类
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DataSource(ABC):
    """数据源插件基类"""

    @abstractmethod
    def fetch(
        self,
        tables: list[str],
        date_col: str | None,
        date_value: str | None,
        compare_date: str | None,
        config: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """
        拉取数据。

        Returns:
            {table_name: {"current": {col: value}, "compare": {col: value} | None}, ...}
            或兼容旧格式 {table_name: {col: value}}（无 compare 时）
        """
        pass


class Outputter(ABC):
    """输出器插件基类"""

    @abstractmethod
    def format(self, metrics: dict[str, Any], config: dict[str, Any]) -> str:
        """将指标 dict 格式化为输出字符串"""
        pass
