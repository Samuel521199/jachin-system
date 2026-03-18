"""
BI 指标 — 插件注册表

数据源、输出器通过注册表发现与调用
"""
from __future__ import annotations

from typing import Any, Callable

_DATA_SOURCES: dict[str, type] = {}
_OUTPUTTERS: dict[str, type] = {}


def register_data_source(name: str, plugin_class: type) -> None:
    """注册数据源插件"""
    _DATA_SOURCES[name] = plugin_class


def register_outputter(name: str, plugin_class: type) -> None:
    """注册输出器插件"""
    _OUTPUTTERS[name] = plugin_class


def get_data_source(name: str) -> type | None:
    return _DATA_SOURCES.get(name)


def get_outputter(name: str) -> type | None:
    return _OUTPUTTERS.get(name)


def list_data_sources() -> list[str]:
    return list(_DATA_SOURCES.keys())


def list_outputters() -> list[str]:
    return list(_OUTPUTTERS.keys())
