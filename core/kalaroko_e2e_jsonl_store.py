"""
``~/.jachin/data/kalaroko_e2e.jsonl`` 单一路径约定 + FileLock。

调度器 prune、MCP append/query、晨报读取共用同一把锁，避免整点与巡检并发损坏文件。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock

KALAROKO_E2E_JSONL_PATH = Path.home() / ".jachin" / "data" / "kalaroko_e2e.jsonl"


def is_kalaroko_e2e_jsonl_file(path: Path | str) -> bool:
    """是否与 E2E 持久化文件指向同一路径（解析后比较）。"""
    try:
        a = Path(path).expanduser().resolve()
        b = KALAROKO_E2E_JSONL_PATH.resolve()
        return a == b
    except Exception:
        s = str(path).replace("\\", "/")
        return s.rstrip("/").endswith("kalaroko_e2e.jsonl")


@contextmanager
def kalaroko_e2e_jsonl_lock(
    path: Path | str | None = None,
    *,
    timeout: float = 10.0,
) -> Iterator[None]:
    """对 ``kalaroko_e2e.jsonl`` 的互斥锁；非该路径时为空操作（不阻塞）。"""
    p = Path(path).expanduser() if path is not None else KALAROKO_E2E_JSONL_PATH
    if not is_kalaroko_e2e_jsonl_file(p):
        yield
        return
    try:
        resolved = Path(p).expanduser().resolve()
    except Exception:
        resolved = Path(p).expanduser()
    lock_path = str(resolved) + ".lock"
    lock = FileLock(lock_path, timeout=timeout)
    with lock:
        yield


def atomic_replace_path(tmp: Path, target: Path) -> None:
    """``os.replace`` 原子替换；目标已存在时由平台保证与临时文件切换。"""
    os.replace(str(tmp), str(target))
