"""
IM 通道层 — Lark/Telegram 等同维度

L3 启动时按 ~/.jachin/config/im_channels.yaml 加载配置，
启动启用的入站通道（长连接等），与 L3 主进程解耦。
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from l3_node.im_channels.config import load_config
from l3_node.im_channels.lark_channel import (
    LarkInboundChannel,
    create_lark_send_reply,
)

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type] = {
    "lark": LarkInboundChannel,
    # "telegram": TelegramInboundChannel,  # future
}


def get_inbound_channel(channel_id: str) -> type | None:
    """获取入站通道实现类"""
    return _REGISTRY.get(channel_id)


def start_im_channels(
    run_agent_fn: Callable[..., Any],
    engine: Any,
    main_loop: Any,
) -> list[threading.Thread]:
    """
    按配置启动 IM 通道，每个通道在独立线程中运行。
    返回已启动的线程列表，便于 join 或 daemon 管理。
    """
    from l3_node.im_channels.dispatcher import create_im_message_handler

    cfg = load_config()
    channels = cfg.get("im_channels") or {}
    threads: list[threading.Thread] = []
    lark_started = False

    for ch_id, ch_cfg in channels.items():
        if not isinstance(ch_cfg, dict) or not ch_cfg.get("enabled", False):
            continue
        impl = get_inbound_channel(ch_id)
        if not impl:
            logger.debug("[IM Channels] 未知通道 %s，跳过", ch_id)
            continue

        if ch_id == "lark":
            lark_started = True
            send_fn = create_lark_send_reply(ch_cfg)
        else:
            logger.warning("[IM Channels] 通道 %s 暂未实现 send_reply", ch_id)
            continue

        _im_timeout = float(os.environ.get("LARK_IM_AGENT_TIMEOUT", "180"))
        handler = create_im_message_handler(
            run_agent_fn,
            engine,
            send_fn,
            main_loop=main_loop,
            timeout=_im_timeout,
        )
        channel = impl()
        t = threading.Thread(
            target=channel.start,
            args=(ch_cfg, handler),
            name=f"im-{ch_id}",
            daemon=True,
        )
        t.start()
        threads.append(t)
        logger.info("[IM Channels] 已启动 %s 入站通道（长连接），招聘测试请直接使用 Lark 发消息", ch_id)

    if lark_started:
        def _delayed_hr_online_briefing() -> None:
            import time

            # 略晚于 Lark WS connect，且等 im_channels 凭证已参与首轮请求
            time.sleep(6.0)
            try:
                from l3_node.channels.lark.hr_recruitment_notify import send_hr_l3_online_briefing_if_configured

                send_hr_l3_online_briefing_if_configured(reason="startup")
            except Exception as e:
                logger.debug("[IM Channels] HR 上线简报跳过: %s", e)

        threading.Thread(
            target=_delayed_hr_online_briefing,
            name="hr-lark-startup-briefing",
            daemon=True,
        ).start()

    return threads
