"""
Jachin Plugin SDK (JPP) - Python 极客脚手架

将 Python 函数包装成符合 Jachin Daemon 调用的标准格式。
支持 stdin/stdout JSON 协议，兼容 py2wasm 编译后的 Wasm 运行时。
"""
from __future__ import annotations

import json
import sys
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_REGISTERED_PLUGIN: Callable[..., Any] | None = None


def jachin_plugin(func: F) -> F:
    """
    装饰器：将 Python 函数包装成 Jachin 标准插件格式。

    被装饰的函数将作为插件的唯一入口。
    运行时从 stdin 读取 JSON 入参，调用函数，将结果写入 stdout。

    示例:
        @jachin_plugin
        def fetch_crypto_price(ticker: str) -> dict:
            return {"ticker": ticker, "price_usd": 50000}
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    global _REGISTERED_PLUGIN
    _REGISTERED_PLUGIN = wrapper
    return wrapper  # type: ignore


def _run_plugin_stdin_stdout() -> None:
    """
    标准入口：从 stdin 读取 JSON，调用已注册插件，将结果写入 stdout。

    Jachin Daemon 通过 stdin 传入 {"ticker": "BTC"} 等参数，
    插件将 {"price_usd": 50000} 等结果写入 stdout。
    """
    global _REGISTERED_PLUGIN
    if _REGISTERED_PLUGIN is None:
        _write_result({"error": "No plugin registered. Use @jachin_plugin decorator."})
        sys.exit(1)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if isinstance(payload, dict):
            result = _REGISTERED_PLUGIN(**payload)
        else:
            result = _REGISTERED_PLUGIN(payload)
        _write_result(result)
    except json.JSONDecodeError as e:
        _write_result({"error": f"Invalid JSON input: {e}"})
        sys.exit(1)
    except Exception as e:
        _write_result({"error": str(e)})
        sys.exit(1)


def _write_result(obj: Any) -> None:
    """将结果以 JSON 写入 stdout"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run() -> None:
    """
    启动插件主循环（供 py2wasm 编译后的 Wasm 入口调用）。

    当作为 __main__ 运行时，自动执行 stdin/stdout 协议。
    """
    _run_plugin_stdin_stdout()
