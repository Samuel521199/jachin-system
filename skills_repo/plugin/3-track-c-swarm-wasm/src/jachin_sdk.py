"""
Jachin Plugin SDK (JPP) - 从 jachin-plugin-sdk-python 复制
将 Python 函数包装成 stdin/stdout JSON 协议。
"""
from __future__ import annotations

import json
import sys
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])
_REGISTERED_PLUGIN: Callable[..., Any] | None = None


def jachin_plugin(func: F) -> F:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    global _REGISTERED_PLUGIN
    _REGISTERED_PLUGIN = wrapper
    return wrapper  # type: ignore


def _run_plugin_stdin_stdout() -> None:
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
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run() -> None:
    _run_plugin_stdin_stdout()
