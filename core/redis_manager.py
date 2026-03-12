"""
Jachin Nexus V2 - Redis 共享状态层

L2 集群化：Session、Task Queue、短期缓存、分布式锁。
供 sync_daemon Leader 选举及后续无状态化改造使用。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_redis_client: Optional[Any] = None
_redis_failed_once = False  # 单机模式：首次失败后不再重试、不再打日志


def get_redis_client():
    """获取 Redis 客户端。未配置或连接失败时返回 None。"""
    global _redis_client, _redis_failed_once
    if _redis_client is not None:
        return _redis_client
    if _redis_failed_once:
        return None
    try:
        from core.config import settings
        url = getattr(settings, "REDIS_URL", None) or "redis://localhost:6379"
        if url and "://" not in url:
            url = f"redis://{url}"
        import redis
        client = redis.from_url(url, decode_responses=True)
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception as e:
        _redis_failed_once = True
        logger.debug("[RedisManager] Redis 不可用（单节点模式）: %s", e)
        return None


def try_acquire_lock(key: str, value: str, ttl_seconds: int) -> bool:
    """
    尝试获取分布式锁（SET NX + EX）。
    成功返回 True，失败返回 False。
    Redis 不可用时返回 True（单节点退化，始终执行）。
    """
    client = get_redis_client()
    if not client:
        return True  # 单节点模式：无 Redis 时始终执行
    try:
        return bool(client.set(key, value, nx=True, ex=ttl_seconds))
    except Exception as e:
        logger.warning("[RedisManager] 获取锁失败: %s", e)
        return False


def release_lock(key: str, value: str) -> bool:
    """释放锁（仅当 value 匹配时删除，防止误删其他节点持有的锁）。"""
    client = get_redis_client()
    if not client:
        return False
    try:
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        return bool(client.eval(script, 1, key, value))
    except Exception as e:
        logger.warning("[RedisManager] 释放锁失败: %s", e)
        return False
