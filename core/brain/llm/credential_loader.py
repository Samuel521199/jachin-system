"""
Jachin Nexus v8.0 - 瀑布流密钥读取 (Waterfall Credentialing)

优先级：环境变量 > nexus_config.json llm_keys > .qwen_api_key > settings
若全部为空且 required=True，抛出赛博风格警告并挂起。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

console = Console()
_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"


def _load_nexus_config() -> dict[str, Any]:
    """读取 ~/.jachin/nexus_config.json，兼容 UTF-8 / UTF-16（Windows 可能产生 UTF-16）"""
    if not _NEXUS_CONFIG.exists():
        return {}
    try:
        return json.loads(_NEXUS_CONFIG.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        try:
            return json.loads(_NEXUS_CONFIG.read_text(encoding="utf-16"))
        except Exception:
            return {}
    except Exception:
        return {}


def get_dashscope_key(required: bool = False) -> str | None:
    """
    瀑布流读取 DashScope/Qwen API Key。

    优先级：
    1. os.environ["DASHSCOPE_API_KEY"]
    2. nexus_config.json llm_keys.dashscope
    3. .qwen_api_key 覆盖文件
    4. settings (QWEN_API_KEY, DASHSCOPE_API_KEY)

    Args:
        required: 若为 True 且全部为空，打印红色警告并 raise ValueError

    Returns:
        API Key 或 None
    """
    # 1. 环境变量
    key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("QWEN_AI_API_KEY")
    if key and key.strip():
        return key.strip()

    # 2. nexus_config.json
    cfg = _load_nexus_config()
    llm_keys = cfg.get("llm_keys") or {}
    if isinstance(llm_keys, dict):
        key = llm_keys.get("dashscope") or llm_keys.get("qwen")
        if key and str(key).strip():
            return str(key).strip()

    # 3. .qwen_api_key 覆盖
    try:
        from core.config.api_key_override import get_qwen_api_key_override
        key = get_qwen_api_key_override()
        if key:
            return key
    except ImportError:
        pass

    # 4. settings (.env)
    try:
        from core.config import settings
        key = (
            getattr(settings, "QWEN_AI_API_KEY", None)
            or getattr(settings, "DASHSCOPE_API_KEY", None)
            or getattr(settings, "QWEN_API_KEY", None)
        )
        if key:
            return key
    except Exception:
        pass

    if required:
        console.print(Panel(
            "[bold red]❌ 认知引擎密钥缺失[/bold red]\n\n"
            "DashScope/Qwen API Key 未配置。请任选其一：\n"
            "  1. 环境变量: [cyan]DASHSCOPE_API_KEY[/cyan]\n"
            "  2. 配置文件: [cyan]~/.jachin/nexus_config.json[/cyan] → llm_keys.dashscope\n"
            "  3. 桌面端设置中保存 API Key\n\n"
            "[dim]进程挂起，等待配置...[/dim]",
            border_style="red",
            title="[Pluggable Cognitive Engine]",
        ))
        raise ValueError(
            "DASHSCOPE_API_KEY required. Set env var or add llm_keys.dashscope to ~/.jachin/nexus_config.json"
        )
    return None


def get_openai_key(required: bool = False) -> str | None:
    """瀑布流读取 OpenAI API Key。优先级：env OPENAI_API_KEY > nexus_config llm_keys.openai"""
    key = os.environ.get("OPENAI_API_KEY")
    if key and key.strip():
        return key.strip()
    cfg = _load_nexus_config()
    llm_keys = cfg.get("llm_keys") or {}
    if isinstance(llm_keys, dict):
        key = llm_keys.get("openai")
        if key and str(key).strip():
            return str(key).strip()
    if required:
        raise ValueError("OPENAI_API_KEY required")
    return None
