"""
L3 最早阶段调试日志 - 仅用 stdlib，不依赖 dotenv

每次 exe 启动时清空日志，便于排查打包问题。
日志路径：~/.jachin/l3_debug.log（优先）→ cwd/l3_debug.log → TEMP/l3_debug.log
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_LOG_PATH: str | None = None
_FILE_HANDLER: logging.FileHandler | None = None


def _resolve_log_path() -> str:
    """解析日志路径。PyInstaller 时优先 cwd（exe 同目录），否则 ~/.jachin"""
    candidates = []
    if getattr(sys, "frozen", False):
        # 打包 exe：日志放 cwd（dist_jachin_desktop），便于用户查看
        candidates.append(Path.cwd() / "l3_debug.log")
    jachin = Path.home() / ".jachin"
    candidates.append(jachin / "l3_debug.log")
    candidates.append(Path.cwd() / "l3_debug.log")
    candidates.append(Path(os.environ.get("TEMP", os.environ.get("TMP", "/tmp"))) / "l3_debug.log")
    candidates = list(dict.fromkeys(candidates))  # 去重
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            return str(p)
        except OSError:
            continue
    return str(candidates[-1])


def setup_early_logging() -> str:
    """
    最早阶段日志初始化。每次启动清空日志。
    返回日志文件路径。
    """
    global _LOG_PATH, _FILE_HANDLER
    _LOG_PATH = _resolve_log_path()

    # 每次启动清空（强制覆盖，确保日志一定更新）
    try:
        with open(_LOG_PATH, "w", encoding="utf-8") as f:
            utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            f.write(f"{utc} [L3 DEBUG] === L3 调试日志（每次启动清空）=== START\n")
            f.write(f"{utc} [L3 DEBUG] log_file={_LOG_PATH}\n")
            f.write(f"{utc} [L3 DEBUG] Python {sys.version.split()[0]} | {sys.version.split('|')[1].strip() if '|' in sys.version else ''}\n")
            f.write(f"{utc} [L3 DEBUG] platform={sys.platform}\n")
            f.write(f"{utc} [L3 DEBUG] executable={getattr(sys, 'executable', '')}\n")
            f.write(f"{utc} [L3 DEBUG] cwd={Path.cwd()}\n")
            f.write(f"{utc} [L3 DEBUG] __file__={getattr(sys.modules.get('__main__'), '__file__', '')}\n")
            frozen = getattr(sys, "frozen", False)
            f.write(f"{utc} [L3 DEBUG] PyInstaller frozen={frozen}, _MEIPASS={os.environ.get('_MEIPASS', '')}\n")
            f.write(f"{utc} [L3 DEBUG] sys.path[:3]={sys.path[:3]}\n")
            f.write(f"{utc} [L3 DEBUG] LOG_LEVEL={os.environ.get('LOG_LEVEL', '')}\n")
    except OSError:
        pass

    # 挂载 FileHandler 到 root logger
    try:
        _FILE_HANDLER = logging.FileHandler(_LOG_PATH, mode="a", encoding="utf-8")
        _FILE_HANDLER.setLevel(logging.DEBUG)
        _FILE_HANDLER.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(_FILE_HANDLER)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')} [L3 DEBUG] FileHandler attached to root logger\n")
    except OSError:
        pass

    return _LOG_PATH


def trace(fmt: str, *args) -> None:
    """写入 TRACE 级别日志"""
    if _LOG_PATH:
        try:
            utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            try:
                msg = fmt % args if args else fmt
            except (TypeError, ValueError):
                msg = fmt + " " + " ".join(str(a) for a in args)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{utc} [TRACE] {msg}\n")
        except OSError:
            pass


def get_log_path() -> str | None:
    """返回当前日志路径"""
    return _LOG_PATH


def reattach_file_handler() -> None:
    """basicConfig(force=True) 会清除 handlers，需重新挂载 FileHandler"""
    global _FILE_HANDLER
    if _LOG_PATH and _FILE_HANDLER:
        root = logging.getLogger()
        if _FILE_HANDLER not in root.handlers:
            root.addHandler(_FILE_HANDLER)
