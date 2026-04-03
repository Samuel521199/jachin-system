"""
MCP stdio 子进程用的嵌入式 Python / Node 路径解析与预检。

目录约定（安装包或用户目录，版本见 manifest.example.json）::

    <JACHIN_HOME>/runtime/
      manifest.json          # 可选，记录捆绑版本
      python/python.exe      # Windows embeddable
      python/bin/python3     # Unix
      node/node.exe          # Windows portable
      node/bin/node          # Unix

优先顺序：环境变量 JACHIN_MCP_PYTHON / JACHIN_MCP_NODE →
便携包 JACHIN_APP_ROOT/runtime → ~/.jachin/runtime →
frozen 下 exe 旁 runtime/ → 系统 PATH（python/python3/node）。

占位符（command / args / env 字符串）：__JACHIN_MCP_PYTHON__、__JACHIN_MCP_NODE__
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

TOKEN_PYTHON = "__JACHIN_MCP_PYTHON__"
TOKEN_NODE = "__JACHIN_MCP_NODE__"


def _jachin_home() -> Path:
    h = os.environ.get("JACHIN_HOME")
    if h:
        return Path(h).expanduser().resolve()
    return Path.home() / ".jachin"


def _runtime_base_dirs() -> list[Path]:
    """候选 runtime 根目录（内含 python/ / node/）。"""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            r = p.resolve()
        except OSError:
            return
        key = str(r)
        if key not in seen:
            seen.add(key)
            roots.append(r)

    app = os.environ.get("JACHIN_APP_ROOT")
    if app:
        add(Path(app) / "runtime")

    add(_jachin_home() / "runtime")

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        add(exe_dir / "runtime")
        add(exe_dir.parent / "runtime")

    return roots


def _first_existing(candidates: list[Path]) -> Optional[Path]:
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _embedded_python_candidates() -> list[Path]:
    out: list[Path] = []
    for base in _runtime_base_dirs():
        out.extend(
            [
                base / "python" / "python.exe",
                base / "python" / "python3.exe",
                base / "python" / "bin" / "python3",
                base / "python" / "bin" / "python",
                base / "python" / "python3",
            ]
        )
    return out


def _embedded_node_candidates() -> list[Path]:
    out: list[Path] = []
    for base in _runtime_base_dirs():
        out.extend(
            [
                base / "node" / "node.exe",
                base / "node" / "bin" / "node",
                base / "node" / "node",
            ]
        )
    return out


def find_embedded_python() -> Optional[Path]:
    """返回嵌入式 python 可执行文件路径，不存在则 None。"""
    env_p = (os.environ.get("JACHIN_MCP_PYTHON") or "").strip()
    if env_p:
        p = Path(env_p)
        if p.is_file():
            return p
        logger.debug("[MCP Runtime] JACHIN_MCP_PYTHON 指向的文件不存在: %s", env_p)
    return _first_existing(_embedded_python_candidates())


def find_embedded_node() -> Optional[Path]:
    env_p = (os.environ.get("JACHIN_MCP_NODE") or "").strip()
    if env_p:
        p = Path(env_p)
        if p.is_file():
            return p
        logger.debug("[MCP Runtime] JACHIN_MCP_NODE 指向的文件不存在: %s", env_p)
    return _first_existing(_embedded_node_candidates())


def get_effective_mcp_python_command() -> str:
    """占位符展开用：嵌入式优先，否则 python3 / python（供 PATH 解析）。"""
    emb = find_embedded_python()
    if emb:
        return str(emb)
    if sys.platform == "win32":
        w = shutil.which("python")
        return w or "python"
    w = shutil.which("python3") or shutil.which("python")
    return w or "python3"


def get_effective_mcp_node_command() -> str:
    emb = find_embedded_node()
    if emb:
        return str(emb)
    w = shutil.which("node")
    return w or "node"


def inject_embedded_tokens(s: str) -> str:
    """将字符串中的 MCP 运行时占位符替换为实际路径或回退命令。"""
    if not isinstance(s, str) or not s:
        return s
    out = s
    if TOKEN_PYTHON in out:
        out = out.replace(TOKEN_PYTHON, get_effective_mcp_python_command())
    if TOKEN_NODE in out:
        out = out.replace(TOKEN_NODE, get_effective_mcp_node_command())
    return out


def resolve_mcp_stdio_command(command: str) -> str:
    """
    解析 stdio MCP 的 command：
    1) 注入 __JACHIN_MCP_PYTHON__ / __JACHIN_MCP_NODE__
    2) 若 command 为裸 python/python3 且已部署嵌入式 Python，改用嵌入式路径
    3) 若 command 为裸 node 且已部署嵌入式 Node，改用嵌入式路径
    """
    cmd = inject_embedded_tokens((command or "").strip())
    if not cmd:
        return cmd
    low = cmd.lower()
    emb_py = find_embedded_python()
    if emb_py and low in ("python", "python3"):
        return str(emb_py)
    emb_n = find_embedded_node()
    if emb_n and low == "node":
        return str(emb_n)
    return cmd


def preflight_mcp_stdio_command(command: str, server_id: str) -> tuple[bool, str]:
    """
    预检 command 是否可在本机执行。
    返回 (ok, message)；message 仅在 ok=False 时为用户可读说明。
    """
    cmd = (command or "").strip()
    if not cmd:
        return False, f"[MCP Runtime] server_id={server_id!r} 的 command 为空。"

    p = Path(cmd)
    if p.is_file():
        return True, ""

    # 含路径分隔符时按路径处理（可能含空格等，未展开为绝对路径）
    if "/" in cmd or "\\" in cmd or (len(cmd) > 1 and cmd[1] == ":" and sys.platform == "win32"):
        if p.is_file():
            return True, ""
        hint = (
            f"[MCP Runtime] 未找到可执行文件: {cmd!r}（server_id={server_id}）。"
            "请将嵌入式 Python 解压到 ~/.jachin/runtime/python/ 或便携包 runtime/python/，"
            "或设置环境变量 JACHIN_MCP_PYTHON 为 python.exe 绝对路径。"
            " 布局与版本说明见 tools/mcp-runtime/README.txt"
        )
        return False, hint

    found = shutil.which(cmd)
    if found:
        return True, ""

    hint = (
        f"[MCP Runtime] 无法在 PATH 中找到 {cmd!r}（server_id={server_id}）。"
        "请安装系统 Python/Node，或将嵌入式运行时放入 ~/.jachin/runtime/（python、node 子目录），"
        "或设置 JACHIN_MCP_PYTHON / JACHIN_MCP_NODE。"
        " 详见 tools/mcp-runtime/README.txt"
    )
    return False, hint


def resolve_mcp_cfg_placeholders(cfg: dict[str, Any]) -> dict[str, Any]:
    """解析配置中 command、args、env 字符串里的 __JACHIN_MCP_*__ 占位符。"""
    out = dict(cfg)
    cmd = out.get("command")
    if isinstance(cmd, str):
        out["command"] = inject_embedded_tokens(cmd.strip())
    args = out.get("args")
    if isinstance(args, list):
        out["args"] = [inject_embedded_tokens(a) if isinstance(a, str) else a for a in args]
    env = out.get("env")
    if isinstance(env, dict):
        out["env"] = {
            str(k): inject_embedded_tokens(v) if isinstance(v, str) else v for k, v in env.items()
        }
    return out


def resolve_and_preflight_command(command: str, server_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    解析 command 并预检。
    返回 (resolved_command, None) 或 (None, error_message)。
    """
    resolved = resolve_mcp_stdio_command(command)
    ok, msg = preflight_mcp_stdio_command(resolved, server_id)
    if not ok:
        return None, msg
    return resolved, None
