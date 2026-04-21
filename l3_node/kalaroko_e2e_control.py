"""
Kalaroko E2E 手动巡检 — 进程内共享状态。

独立模块避免 ``importlib`` 以不同 module name 加载同一脚本时出现两套全局变量，
导致 ``POST /api/v1/monitor/stop`` 无法打断 SSE 中的 ``_run_full_cycle``。
"""

from __future__ import annotations

CANCEL_MANUAL_RUN = False


def stop_manual_run() -> None:
    global CANCEL_MANUAL_RUN
    CANCEL_MANUAL_RUN = True


def reset_manual_run_flag() -> None:
    global CANCEL_MANUAL_RUN
    CANCEL_MANUAL_RUN = False


def is_manual_run_cancel_requested() -> bool:
    return CANCEL_MANUAL_RUN
