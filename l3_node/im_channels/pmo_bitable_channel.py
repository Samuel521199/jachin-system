"""
PMO 多维表变更 — Lark 长连接入站通道（非 IM 聊天）。

与 ``lark`` / ``lark_hr`` 独立 enabled；多机部署时仅一台接管表变更事件。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from l3_node.im_channels.base import InboundIMChannel
from l3_node.im_channels.lark_channel import _resolve_lark_im_domain

logger = logging.getLogger(__name__)


class PmoBitableLarkInboundChannel(InboundIMChannel):
    id = "lark_pmo_bitable"
    label = "PMO Bitable Events"

    def start(
        self,
        config: dict[str, Any],
        on_message: Callable[[str, str, str], None],
    ) -> None:
        del on_message  # 事件走 pmo_bitable_watch，非 IM 文本

        from l3_node.im_channels.lark_credentials import resolve_pmo_bitable_credentials

        app_id, app_secret = resolve_pmo_bitable_credentials(config)
        if not app_id or not app_secret:
            logger.warning(
                "[IM PMO Bitable] 未配置 app_id/app_secret（im_channels.lark_pmo_bitable 或 "
                "pmo_bitable_watch.yaml / PMO_BITABLE_WATCH_*），跳过长连接"
            )
            return

        domain = _resolve_lark_im_domain(config)
        try:
            from l3_node.jobs.pmo_bitable_watch_scheduler import start_pmo_bitable_watch_scheduler

            st = start_pmo_bitable_watch_scheduler()
            logger.info("[IM PMO Bitable] debounce 检查器: %s", st)
        except Exception as e:
            logger.warning("[IM PMO Bitable] debounce 检查器启动跳过: %s", e)

        try:
            from l3_node.channels.lark.pmo_bitable_events import start_pmo_bitable_long_connection
        except ImportError as e:
            logger.error("[IM PMO Bitable] 长连接依赖缺失: %s", e)
            return

        logger.info(
            "[IM PMO Bitable] 长连接启动中 app_id=%s domain=%s",
            app_id[:12] + "..." if len(app_id) > 12 else app_id,
            domain,
        )
        start_pmo_bitable_long_connection(
            app_id,
            app_secret,
            domain=domain,
            log_level="INFO",
        )
