"""
PMO Copilot 侧车/CLI 启动时的控制台减压默认值。

PMO 详细轨迹写入 ``JACHIN_PMO_COPILOT_DEBUG_LOG`` 指定文件；
控制台仅保留 ``[pmo-copilot]`` 少量 ``print``，避免 Windows 终端因海量 stdout 背压「假死」。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_QUIET_DEFAULTS: dict[str, str] = {
    "JACHIN_L3_DEEP_LOG": "0",
    "JACHIN_EXEC_TRACE_STDERR": "0",
    "JACHIN_L3_LOG_LITELLM_DETAIL": "0",
    "LOG_LEVEL": "WARNING",
    "JACHIN_LOG_LEVEL": "WARNING",
    "JACHIN_L3_FILE_LOG_COMPACT": "1",
    # 后台 PMO 子进程：禁止 AllocConsole，避免抢桌面重定向的 stdout 管道
    "JACHIN_L3_CONSOLE": "0",
    # 标记一次性 PMO 运行：early_log 追加到独立文件，不清空常驻 L3 的 l3_debug.log
    "JACHIN_PMO_COPILOT_RUN": "1",
    # 复用本机常驻 L3 HTTP/MCP，禁止 PMO 再起第二套 stdio MCP（否则数分钟后拖死主 L3）
    "JACHIN_PMO_REUSE_L3_MCP": "1",
}


def is_pmo_copilot_cli_argv(argv: list[str] | None = None) -> bool:
    return "--run-pmo-copilot" in (argv if argv is not None else sys.argv)


def is_pmo_copilot_run() -> bool:
    """一次性 PMO 子进程/脚本（不得抢占 ~/.jachin/l3.lock 或 kill 常驻 L3）。"""
    if (os.environ.get("JACHIN_PMO_COPILOT_RUN") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    return is_pmo_copilot_cli_argv()


def _default_pmo_log_dir() -> str | None:
    root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
    if not root:
        return None
    return str(Path(root) / "logs" / "pmo")


def apply_pmo_copilot_console_quiet_defaults(*, force: bool = False) -> None:
    """在 ``l3_node`` 日志系统初始化之前调用；``setdefault`` 不覆盖用户显式配置。"""
    from l3_node.pmo_lark_env import ensure_pmo_dotenv_loaded

    ensure_pmo_dotenv_loaded()
    for key, val in _QUIET_DEFAULTS.items():
        if force:
            os.environ[key] = val
        else:
            os.environ.setdefault(key, val)
    pmo_log = _default_pmo_log_dir()
    if pmo_log and force:
        os.environ["JACHIN_LOG_DIR"] = pmo_log
    elif pmo_log:
        os.environ.setdefault("JACHIN_LOG_DIR", pmo_log)
