"""
IM 通道层 — Lark/Telegram 等同维度

L3 启动时按 ~/.jachin/config/im_channels.yaml 加载配置，
启动启用的入站通道（长连接等），与 L3 主进程解耦。

长连接类型（可独立 enabled，多机部署时每台只开需要的）:
  - lark: 主机器人 IM（PMO 触发 / 通用 / 招聘路由）
  - lark_hr: HR 招聘专用机器人 IM（独立 app_id 时用）
  - lark_pmo_bitable: PMO 多维表变更事件（非聊天）
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
from l3_node.im_channels.pmo_bitable_channel import PmoBitableLarkInboundChannel

logger = logging.getLogger(__name__)

_IM_LARK_CHANNELS = frozenset({"lark", "lark_hr"})

_REGISTRY: dict[str, type] = {
    "lark": LarkInboundChannel,
    "lark_hr": LarkInboundChannel,
    "lark_pmo_bitable": PmoBitableLarkInboundChannel,
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

    try:
        from l3_node.lark_test_schedule import ensure_test_schedule_scheduler_started

        ensure_test_schedule_scheduler_started()
    except Exception as e:
        logger.debug("[IM Channels] /test 定时调度器启动跳过: %s", e)

    try:
        from l3_node.deferred_task_scheduler import ensure_deferred_scheduler_started

        ensure_deferred_scheduler_started()
    except Exception as e:
        logger.debug("[IM Channels] deferred-task scheduler 启动跳过: %s", e)

    cfg = load_config()
    channels = cfg.get("im_channels") or {}
    threads: list[threading.Thread] = []
    lark_im_started = False

    for ch_id, ch_cfg in channels.items():
        if not isinstance(ch_cfg, dict) or not ch_cfg.get("enabled", False):
            continue
        impl = get_inbound_channel(ch_id)
        if not impl:
            logger.debug("[IM Channels] 未知通道 %s，跳过", ch_id)
            continue

        ch_run = {**ch_cfg, "_channel_id": ch_id}

        if ch_id == "lark_pmo_bitable":
            channel = impl()
            t = threading.Thread(
                target=channel.start,
                args=(ch_run, lambda *_a: None),
                name=f"im-{ch_id}",
                daemon=True,
            )
            t.start()
            threads.append(t)
            logger.info(
                "[IM Channels] 已启动 %s 长连接（PMO 多维表变更事件；"
                "凭证见 im_channels 或 pmo_bitable_watch.yaml）",
                ch_id,
            )
            continue

        if ch_id not in _IM_LARK_CHANNELS:
            logger.warning("[IM Channels] 通道 %s 暂未实现 send_reply", ch_id)
            continue

        if ch_id == "lark":
            lark_im_started = True
        send_fn = create_lark_send_reply(ch_run, channel_id=ch_id)

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
            args=(ch_run, handler),
            name=f"im-{ch_id}",
            daemon=True,
        )
        t.start()
        threads.append(t)
        logger.info(
            "[IM Channels] 已启动 %s 入站 IM 长连接（本机接管该机器人私聊/群消息）",
            ch_id,
        )

    try:
        from l3_node.runtime_diag_log import log_runtime_milestone

        log_runtime_milestone(
            f"IM 通道已启动 {len(threads)} 条线程: "
            + ",".join(t.name for t in threads)
            if threads
            else "IM 通道：无 enabled 通道"
        )
    except Exception:
        pass

    if lark_im_started:
        def _delayed_hr_online_briefing() -> None:
            import time

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
