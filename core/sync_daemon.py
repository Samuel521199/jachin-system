"""
Jachin Nexus V2 - L1-L2 策略同步心跳守护进程

私有化部署的 L2 定期向 L1 云端发起心跳，拉取订阅状态与全局安全策略。

L2 集群化：多节点时仅 Leader（协调 Agent）执行心跳，基于 Redis 分布式锁选举。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"
_HEARTBEAT_PATH = "/api/v1/edge/heartbeat"
_DEFAULT_INTERVAL_SEC = 60
_LEADER_LOCK_KEY = "l2_cluster_leader_lock"
# 锁过期时间略大于心跳间隔，避免 Leader 失联后长时间阻塞
_LOCK_TTL_SEC = _DEFAULT_INTERVAL_SEC + 15

# 本进程唯一标识（短命 UUID，每次启动重新生成）
L2_PROCESS_ID = str(uuid.uuid4())[:8]


def _load_nexus_config() -> dict[str, Any]:
    """读取 nexus_config.json"""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        raw = _CONFIG_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            raw = _CONFIG_PATH.read_text(encoding="utf-16")
        except Exception:
            return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


async def l1_heartbeat_sync() -> dict[str, Any] | None:
    """
    向 L1 平台发起心跳，拉取策略。
    读取 ~/.jachin/nexus_config.json 中的 instance_id、access_token、nexus_base_url。

    Returns:
        心跳响应体，失败返回 None
    """
    cfg = _load_nexus_config()
    instance_id = cfg.get("instance_id") or ""
    access_token = cfg.get("access_token") or ""
    base_url = (cfg.get("nexus_base_url") or "").rstrip("/")

    if not instance_id or not access_token or not base_url:
        logger.debug("[L1Heartbeat] 未配对或未配置 nexus_base_url，跳过心跳")
        return None

    url = f"{base_url}{_HEARTBEAT_PATH}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "instance_id": instance_id,
        "core_version": "0.8.5",
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data
    except Exception as e:
        logger.warning("[L1Heartbeat] 心跳失败: %s", e)
        return None


async def _heartbeat_loop(stop_event: asyncio.Event, interval_sec: float) -> None:
    """
    心跳循环：定期调用 l1_heartbeat_sync，并将策略写入 l1_policy。
    L2 集群化：仅成功获取 Redis 锁的节点作为 Leader（协调 Agent）执行心跳。
    """
    from core.l1_policy import apply_heartbeat_response

    first_run = True
    lock_ttl = int(interval_sec) + 15
    lock_value = f"{L2_PROCESS_ID}-{uuid.uuid4().hex[:12]}"

    while not stop_event.is_set():
        try:
            # 尝试获取 Leader 锁（Redis 不可用时退化：单节点模式，直接执行）
            loop = asyncio.get_running_loop()
            acquired = await loop.run_in_executor(
                None,
                lambda: _try_acquire_leader_lock(lock_value, lock_ttl),
            )
            if acquired:
                log_fn = logger.info if first_run else logger.debug
                log_fn(
                    "[L1Heartbeat] [Leader/Coordinating Agent] 本节点 %s 已获取锁，执行 L1 心跳",
                    L2_PROCESS_ID,
                )
                data = await l1_heartbeat_sync()
                if data:
                    apply_heartbeat_response(data)
                    if first_run:
                        logger.info("[L1Heartbeat] 首次策略同步完成")
                # 锁自动过期，无需显式释放（避免持有期间进程挂掉导致死锁）
            else:
                logger.debug(
                    "[L1Heartbeat] [Follower] 本节点 %s 未获取锁，跳过本次心跳（由其他 Leader 执行）",
                    L2_PROCESS_ID,
                )
        except Exception as e:
            logger.warning("[L1Heartbeat] 循环异常: %s", e)
        first_run = False
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            pass


def _try_acquire_leader_lock(value: str, ttl_sec: int) -> bool:
    """同步获取 Leader 锁。Redis 不可用或异常时返回 True（单节点退化）。"""
    try:
        from core.redis_manager import try_acquire_lock
        return try_acquire_lock(_LEADER_LOCK_KEY, value, ttl_sec)
    except Exception:
        return True  # 单节点退化


def start_l1_heartbeat_background() -> asyncio.Task | None:
    """
    启动 L1 心跳后台任务。
    返回 Task，供 lifespan 在 shutdown 时 cancel。
    L2 集群化：多节点时通过 Redis 锁选举 Leader，仅 Leader 执行心跳。
    """
    cfg = _load_nexus_config()
    if not cfg.get("instance_id") or not cfg.get("access_token") or not cfg.get("nexus_base_url"):
        logger.info("[L1Heartbeat] 未配对 L1，心跳守护进程不启动")
        return None

    interval = cfg.get("heartbeat_interval_sec") or _DEFAULT_INTERVAL_SEC
    stop_event = asyncio.Event()
    task = asyncio.create_task(_heartbeat_loop(stop_event, interval))
    task.add_done_callback(lambda t: stop_event.set() if not t.cancelled() else None)

    try:
        from core.redis_manager import get_redis_client
        if get_redis_client():
            logger.info(
                "[L1Heartbeat] 心跳守护进程已启动，间隔 %ds，L2 进程 ID=%s（集群模式：Leader 选举）",
                interval,
                L2_PROCESS_ID,
            )
        else:
            logger.info(
                "[L1Heartbeat] 心跳守护进程已启动，间隔 %ds，L2 进程 ID=%s（单节点模式）",
                interval,
                L2_PROCESS_ID,
            )
    except Exception:
        logger.info(
            "[L1Heartbeat] 心跳守护进程已启动，间隔 %ds，L2 进程 ID=%s",
            interval,
            L2_PROCESS_ID,
        )
    return task
