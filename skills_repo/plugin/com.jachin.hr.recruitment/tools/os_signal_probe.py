"""OS / Workflow 停止信号探针 — 供 atom_* Playwright 长循环秒级响应 STOP_HARVEST。"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def os_stop_requested(os_context: Any) -> bool:
    """
    Playwright 循环内调用：拉桥、探测 STOP_HARVEST、消费后返回 True。

    WorkflowContext 路径使用 drain_merge_into_context + has_signal（与 Jachin OS 信号穿透一致）；
    普通 dict 走 try_consume_stop_harvest。
    """
    return rpa_loop_check_stop_harvest(os_context)


def rpa_loop_check_stop_harvest(os_context: Any) -> bool:
    """与 HarvestLoop / 飞书 inject_signal 对齐的循环首行探针。"""
    if os_context is None:
        return False
    try:
        from core.workflow_engine import SIGNAL_STOP_HARVEST, WorkflowContext, try_consume_stop_harvest

        if isinstance(os_context, WorkflowContext):
            os_context.drain_merge_into_context()
            if os_context.has_signal(SIGNAL_STOP_HARVEST):
                logger.warning(
                    "🚨 [OS 强中断] 捕获到全局停止信号，立刻终止底层 RPA 循环！"
                )
                os_context.pop_signal()
                return True
            return False
        if try_consume_stop_harvest(os_context):
            logger.warning(
                "🚨 [OS 强中断] 捕获到全局停止信号，立刻终止底层 RPA 循环！"
            )
            return True
    except Exception as e:
        logger.debug("[OS] stop probe skip: %s", e)
    return False
