"""
run_gameqa_skill.py 专用诊断日志。

通过环境变量 ``JACHIN_GAMEQA_SKILL_DEBUG_LOG`` 指向单一日志文件；仅在该变量由脚本置位时，
GameQA / MCP 路径会向该文件追加详细记录（stdio MCP、当前检测页 url/title、Playwright click 等）。
**不向 stdout 写入**；依赖 ``skill_cli_debug.append`` 的调用均仅写该文件。

感知明细（YOLO 每框 class/conf/bbox、OCR 文本）：由 ``append_perception_debug`` 写入；若未设置
``JACHIN_GAMEQA_SKILL_DEBUG_LOG``，则追加到与默认路径相同的 ``gameqa_skill_debug.log``（便于 HTTP run-skill）。
OCR 写入长度上限由环境变量 ``GAMEQA_SKILL_DEBUG_OCR_LOG_CHARS``（默认 8000）控制，0 表示只记长度不记正文。
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

_ENV_KEY = "JACHIN_GAMEQA_SKILL_DEBUG_LOG"

_MCP_HANDLERS_INSTALLED = False


def default_skill_debug_dir() -> Path:
    return Path.home() / ".jachin" / "jachin_debug" / "健康skill"


def default_skill_debug_log_file() -> Path:
    return default_skill_debug_dir() / "gameqa_skill_debug.log"


def log_file_path() -> str | None:
    p = (os.environ.get(_ENV_KEY) or "").strip()
    return p or None


def append(line: str) -> None:
    p = log_file_path()
    if not p:
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")
    except OSError:
        pass


def append_perception_debug(lines: list[str]) -> None:
    """
    将整屏感知（YOLO 明细 + OCR）写入 gameqa_skill_debug.log。
    - 若已设置 ``JACHIN_GAMEQA_SKILL_DEBUG_LOG``，写入该路径；
    - 否则追加到 ``default_skill_debug_log_file()``（与 CLI 默认路径一致，便于 HTTP run-skill 也能落盘）。
    """
    if not lines:
        return
    env_p = (os.environ.get(_ENV_KEY) or "").strip()
    path = Path(env_p) if env_p else default_skill_debug_log_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(path, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(f"[{ts}] {ln}\n")
    except OSError:
        pass


def append_exception(title: str, exc: BaseException) -> None:
    append(f"{title}: {exc!r}")
    append(traceback.format_exc())


def init_run_gameqa_skill_log(meta: dict[str, Any]) -> Path:
    """
    每次运行覆盖单一日志文件，并设置 ``JACHIN_GAMEQA_SKILL_DEBUG_LOG``。
    """
    log_dir = default_skill_debug_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = default_skill_debug_log_file()
    resolved = str(path.resolve())
    os.environ[_ENV_KEY] = resolved

    lines = [
        "=" * 80,
        "run_gameqa_skill.py · 诊断日志（每运行一次覆盖本文件；旧内容不保留）",
        f"本地时间: {datetime.now().isoformat()}",
        f"python: {sys.executable}",
        f"log_file: {resolved}",
        "",
        "本日志聚焦两类问题：",
        "  (A) stdio MCP: Connection closed / 子进程未就绪 —— 与 GameQA 五件套进程内 MCP 无关时可忽略。",
        "  (B) Playwright: page.mouse.click 无超时 —— 若线程挂在此处，本条之后可能看不到「click 已返回」。",
        "=" * 80,
    ]
    for k, v in sorted(meta.items(), key=lambda x: x[0]):
        lines.append(f"{k}: {v}")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def attach_mcp_diagnostic_handlers() -> None:
    """将 core MCP 相关 logger 复制到诊断文件（追加）。"""
    global _MCP_HANDLERS_INSTALLED
    p = log_file_path()
    if not p or _MCP_HANDLERS_INSTALLED:
        return
    _MCP_HANDLERS_INSTALLED = True
    h = logging.FileHandler(p, encoding="utf-8", mode="a")
    h.setLevel(logging.DEBUG)
    h.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    for name in (
        "core.mcp_client",
        "l3_node.primitives.mcp.mcp_stdio_bootstrap",
        "l3_node.primitives.mcp.registry",
        "l3_node",
    ):
        lg = logging.getLogger(name)
        lg.addHandler(h)
        if lg.level > logging.DEBUG:
            lg.setLevel(logging.DEBUG)
