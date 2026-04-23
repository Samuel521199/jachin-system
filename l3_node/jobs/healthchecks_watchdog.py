"""
Healthchecks.io 看门狗：独立守护线程定期 GET ping，用于检测 L3 进程所在机器是否存活。

未设置 ``JACHIN_HEALTHCHECKS_PING_URL``（或 ``HEALTHCHECKS_PING_URL``）时不启动线程。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Final

import requests

logger = logging.getLogger(__name__)

_started = False
_start_lock = threading.Lock()

_DEFAULT_INTERVAL: Final[float] = 60.0
_MIN_INTERVAL: Final[float] = 15.0
_MAX_INTERVAL: Final[float] = 3600.0


def _resolve_ping_url() -> str:
    for key in ("JACHIN_HEALTHCHECKS_PING_URL", "HEALTHCHECKS_PING_URL"):
        v = (os.environ.get(key) or "").strip()
        if v and not v.startswith("${"):
            return v
    return ""


def _resolve_interval_sec() -> float:
    raw = (os.environ.get("JACHIN_HEALTHCHECKS_INTERVAL_SEC") or "").strip()
    if not raw:
        return _DEFAULT_INTERVAL
    try:
        n = float(raw)
    except ValueError:
        return _DEFAULT_INTERVAL
    return max(_MIN_INTERVAL, min(_MAX_INTERVAL, n))


def _healthchecks_watchdog_worker(ping_url: str, interval_sec: float) -> None:
    """看门狗心跳线程：每隔 interval_sec 向云端发送存活信号。"""
    while True:
        try:
            resp = requests.get(ping_url, timeout=10)
            if resp.status_code >= 400:
                logger.warning(
                    "[Watchdog] Healthchecks ping HTTP %s: %s",
                    resp.status_code,
                    (resp.text or "")[:200],
                )
        except Exception as e:
            logger.warning("[Watchdog] 云端心跳发送失败（网络波动）: %s", e)
        time.sleep(interval_sec)


def start_healthchecks_watchdog() -> None:
    """启动 L3 节点的云端心跳守护线程（daemon=True，主进程退出时线程结束）。"""
    global _started
    with _start_lock:
        if _started:
            logger.debug("[Watchdog] 已启动，跳过重复注册")
            return
        ping_url = _resolve_ping_url()
        if not ping_url:
            logger.info(
                "[Watchdog] 未设置 JACHIN_HEALTHCHECKS_PING_URL / HEALTHCHECKS_PING_URL，跳过云端心跳"
            )
            return
        interval_sec = _resolve_interval_sec()
        _started = True
        t = threading.Thread(
            target=_healthchecks_watchdog_worker,
            args=(ping_url, interval_sec),
            daemon=True,
            name="L3_Healthchecks_Watchdog",
        )
        t.start()
        logger.info(
            "[Watchdog] L3 节点云端心跳守护线程已启动 (interval=%ss, url=%s…)",
            int(interval_sec) if interval_sec == int(interval_sec) else interval_sec,
            ping_url[:48],
        )
