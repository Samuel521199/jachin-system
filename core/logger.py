"""
Unified logging - 统一日志配置

支持控制台与文件输出，可配置日志级别（INFO/DEBUG）。
应用启动时应调用 setup_logging() 初始化。
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from core.config import settings


def setup_logging(
    level: Optional[str] = None,
    log_file: Optional[Path] = None,
    log_dir: str = "./logs",
) -> None:
    """
    配置统一日志：控制台 + 文件，支持 INFO/DEBUG。

    Args:
        level: 日志级别，如 "INFO", "DEBUG"。默认从 settings.DEBUG 推导。
        log_file: 日志文件路径。若为 None 则使用 log_dir/jachin.log。
        log_dir: 日志目录，当 log_file 为 None 时使用。
    """
    log_level = level or ("DEBUG" if settings.DEBUG else "INFO")
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # 清除已有 handlers，避免重复
    for h in root.handlers[:]:
        root.removeHandler(h)

    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(fmt)

    # 控制台
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # 文件
    if log_file is None:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_file = Path(log_dir) / "jachin.log"
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 降低第三方库日志
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    获取模块 logger。

    Args:
        name: 通常使用 __name__。

    Returns:
        配置好的 Logger 实例。
    """
    return logging.getLogger(name)
