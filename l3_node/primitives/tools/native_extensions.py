"""Optional native tool extension loading.

Core tools live in ``loader.NATIVE_TOOLS`` and ``core.native_tools``. Domain
packages such as PMO must opt in here instead of being imported by the main
process unconditionally.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_PMO_PROVIDER = "l3_node.tools.pmo_db_tools"


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _configured_provider_modules() -> tuple[str, ...]:
    raw = (os.environ.get("JACHIN_NATIVE_TOOL_EXTENSION_MODULES") or "").strip()
    modules = [x.strip() for x in raw.split(",") if x.strip()]

    # PMO is a skill/domain package. It opts in when its runner marks the
    # process, or when an operator explicitly enables the legacy native tools.
    if _truthy_env("JACHIN_PMO_COPILOT_RUN") or _truthy_env("JACHIN_ENABLE_PMO_NATIVE_TOOLS"):
        if _PMO_PROVIDER not in modules:
            modules.append(_PMO_PROVIDER)
    return tuple(dict.fromkeys(modules))


@lru_cache(maxsize=8)
def _providers_for_key(key: tuple[str, ...]) -> tuple[Any, ...]:
    out: list[Any] = []
    for mod_name in key:
        try:
            out.append(importlib.import_module(mod_name))
        except Exception as e:
            logger.warning("[NativeExtensions] failed to import %s: %s", mod_name, e)
    return tuple(out)


def _providers() -> tuple[Any, ...]:
    return _providers_for_key(_configured_provider_modules())


def _provider_tools(provider: Any) -> list[dict[str, Any]]:
    for attr in ("NATIVE_TOOLS_LIST", "PMO_NATIVE_TOOLS_LIST"):
        tools = getattr(provider, attr, None)
        if isinstance(tools, list):
            return [t for t in tools if isinstance(t, dict)]
    getter = getattr(provider, "get_native_tools", None)
    if callable(getter):
        got = getter()
        if isinstance(got, list):
            return [t for t in got if isinstance(t, dict)]
    return []


def load_native_extension_tools() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for provider in _providers():
        for tool in _provider_tools(provider):
            tid = str(tool.get("id") or "").strip()
            if not tid or tid in seen:
                continue
            seen.add(tid)
            tools.append(tool)
    return tools


def _provider_for_tool(tool_id: str) -> Any | None:
    tid = (tool_id or "").strip()
    if not tid:
        return None
    for provider in _providers():
        for tool in _provider_tools(provider):
            if str(tool.get("id") or "").strip() == tid:
                return provider
    return None


def is_native_extension_tool(tool_id: str) -> bool:
    return _provider_for_tool(tool_id) is not None


def parse_native_extension_action_input(tool_id: str, action_input: str) -> dict[str, Any]:
    provider = _provider_for_tool(tool_id)
    if provider is None:
        return {}
    parser = getattr(provider, "parse_native_action_input", None)
    if callable(parser):
        out = parser(tool_id, action_input)
        return out if isinstance(out, dict) else {}

    # Compatibility for the current PMO provider. This is intentionally kept
    # outside the main loader/core dispatch path.
    if tool_id == "core:db_query" and callable(getattr(provider, "parse_db_query_action_input", None)):
        return provider.parse_db_query_action_input(action_input)

    s = (action_input or "").strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return {}
    return {}


def dispatch_native_extension_tool(tool_id: str, **kwargs: Any) -> Any:
    provider = _provider_for_tool(tool_id)
    if provider is None:
        raise ValueError(f"Unknown native extension tool: {tool_id}")
    dispatcher = getattr(provider, "dispatch_native_tool", None)
    if callable(dispatcher):
        return dispatcher(tool_id, **kwargs)
    dispatcher = getattr(provider, "dispatch_pmo_db_tool", None)
    if callable(dispatcher):
        return dispatcher(tool_id, **kwargs)
    raise ValueError(f"Native extension provider has no dispatcher: {provider.__name__}")
