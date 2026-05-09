"""
Healthchecks.io：不再使用独立周期线程；仅在 **飞书巡检战报实际发送成功** 后
由 ``kalaroko_inspection_notify.send_kalaroko_inspection_to_lark`` 触发 GET ping。

环境变量：``JACHIN_HEALTHCHECKS_PING_URL`` 或 ``HEALTHCHECKS_PING_URL``（未设置则跳过）。
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


def _resolve_ping_url() -> str:
    for key in ("JACHIN_HEALTHCHECKS_PING_URL", "HEALTHCHECKS_PING_URL"):
        v = (os.environ.get(key) or "").strip()
        if v and not v.startswith("${"):
            return v
    return ""


def ping_healthchecks_if_configured() -> None:
    """
    在 Kalaroko 巡检 Lark 推送完整成功后调用（同步，可由 asyncio.to_thread 包裹）。

    原则：No Report, No Ping — 无飞书巡检产出则不应触发本函数。
    """
    ping_url = _resolve_ping_url()
    if not ping_url:
        return
    try:
        resp = requests.get(ping_url, timeout=10)
        if resp.status_code < 400:
            logger.info("[Healthchecks] 真实业务心跳已发送: 巡检报告产出证明")
        else:
            logger.warning(
                "[Healthchecks] 心跳发送异常 HTTP %s",
                resp.status_code,
            )
    except Exception as e:
        logger.warning("[Healthchecks] 心跳网络波动: %s", e)
