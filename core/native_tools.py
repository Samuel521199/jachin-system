"""
Jachin Nexus v8.0 - Native Core 内置标准库

权限死锁在 ~/.jachin/workspace/ 下，供 MCP 瘫痪时的 Fallback 使用。
任何越界访问直接抛出 SecurityException。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# 工作目录根，绝对不可越界
_WORKSPACE_ROOT = Path.home() / ".jachin" / "workspace"


class SecurityException(Exception):
    """Wasm/Native Core sandbox violation"""


def _assert_under_workspace(path: Path) -> None:
    """断言路径在 workspace 下，否则抛出 SecurityException"""
    abs_path = path.resolve()
    root = _WORKSPACE_ROOT.resolve()
    if not str(abs_path).startswith(str(root)):
        raise SecurityException(
            f"Wasm/Native Core sandbox violation: {path} escapes ~/.jachin/workspace/"
        )


def core_fs_read(file_path: str) -> str:
    """
    读取文件内容。路径必须位于 ~/.jachin/workspace/ 下。

    Args:
        file_path: 相对或绝对路径，必须解析后在 workspace 内

    Returns:
        文件内容

    Raises:
        SecurityException: 路径越界
    """
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = (_WORKSPACE_ROOT / p).resolve()
    _assert_under_workspace(p)
    return p.read_text(encoding="utf-8", errors="replace")


def core_fs_write(file_path: str, content: str) -> None:
    """
    写入文件。路径必须位于 ~/.jachin/workspace/ 下。

    Args:
        file_path: 相对或绝对路径
        content: 写入内容

    Raises:
        SecurityException: 路径越界
    """
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = (_WORKSPACE_ROOT / p).resolve()
    _assert_under_workspace(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def core_shell_exec(command: str, timeout: int = 30) -> tuple[int, str, str]:
    """
    执行 Shell 命令。工作目录死锁在 ~/.jachin/workspace/。

    Args:
        command: Shell 命令
        timeout: 超时秒数

    Returns:
        (returncode, stdout, stderr)

    Raises:
        SecurityException: 若 cwd 越界（内部保证不会发生）
    """
    _WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        command,
        shell=True,
        cwd=str(_WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout or "", result.stderr or ""


def dispatch_native_tool(tool_id: str, **kwargs: Any) -> Any:
    """
    根据 core:xxx 标识分发到对应 Native 函数。

    Args:
        tool_id: core:fs_read | core:fs_write | core:shell_exec
        **kwargs: 工具参数

    Returns:
        工具执行结果
    """
    if tool_id == "core:fs_read":
        return core_fs_read(kwargs.get("file_path", ""))
    if tool_id == "core:fs_write":
        core_fs_write(kwargs.get("file_path", ""), kwargs.get("content", ""))
        return {"ok": True}
    if tool_id == "core:shell_exec":
        code, out, err = core_shell_exec(
            kwargs.get("command", ""),
            timeout=kwargs.get("timeout", 30),
        )
        return {"returncode": code, "stdout": out, "stderr": err}
    raise ValueError(f"Unknown Native Core tool: {tool_id}")
