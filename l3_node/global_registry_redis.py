"""
GlobalTaskRegistry — Redis 集群 SSOT（路线图 Phase C · 跨机）

多 L3 实例共享同一 Redis 时，任务注册 / resource_tags 抢占可见全局。
Redis 不可用时由 ``global_task_registry`` 回退 SQLite（若已开启 ``JACHIN_GLOBAL_REGISTRY_ENABLE``）。

环境变量
--------
JACHIN_GLOBAL_REGISTRY_REDIS=1          启用 Redis 后端（需同时 JACHIN_GLOBAL_REGISTRY_ENABLE=1）
JACHIN_GLOBAL_REGISTRY_BACKEND=redis    显式指定 redis|sqlite（优先于 REDIS 开关）
JACHIN_REDIS_URL=redis://host:6379/0    L3 独立 Redis 地址（优先）
REDIS_URL=...                           回退
JACHIN_REDIS_CLUSTER=1                  使用 Redis Cluster 客户端
JACHIN_GLOBAL_REGISTRY_REDIS_PREFIX=jachin:gtreg   键前缀（默认 jachin:gtreg）
JACHIN_L3_NODE_ID=node-a                写入任务 extra.node_id（诊断）
JACHIN_GLOBAL_REGISTRY_DUAL_WRITE=1     Redis 与 SQLite 同时写（诊断/回退）
JACHIN_GLOBAL_REGISTRY_REDIS_TOUCH=1    长 run 周期性续期 Redis 键 TTL
JACHIN_GLOBAL_REGISTRY_REDIS_PREEMPT_PUBSUB=1  抢占时 Pub/Sub 通知各节点 cancel
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_client: Any = None
_client_failed = False
_PREFIX = "jachin:gtreg"
_subscriber_thread: threading.Thread | None = None
_subscriber_started = False


def dual_write_enabled() -> bool:
    return (os.environ.get("JACHIN_GLOBAL_REGISTRY_DUAL_WRITE") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def preempt_pubsub_enabled() -> bool:
    return (
        redis_backend_requested()
        and (os.environ.get("JACHIN_GLOBAL_REGISTRY_REDIS_PREEMPT_PUBSUB") or "")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )


def redis_touch_enabled() -> bool:
    return (os.environ.get("JACHIN_GLOBAL_REGISTRY_REDIS_TOUCH") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def redis_backend_requested() -> bool:
    """是否配置为 Redis 后端（不要求连接成功）。"""
    v = (os.environ.get("JACHIN_GLOBAL_REGISTRY_BACKEND") or "").strip().lower()
    if v == "sqlite":
        return False
    if v == "redis":
        return True
    return (os.environ.get("JACHIN_GLOBAL_REGISTRY_REDIS") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _key_prefix() -> str:
    return (os.environ.get("JACHIN_GLOBAL_REGISTRY_REDIS_PREFIX") or _PREFIX).strip().rstrip(":")


def _task_key(run_id: str) -> str:
    return f"{_key_prefix()}:task:{run_id}"


def _running_key() -> str:
    return f"{_key_prefix()}:running"


def _tag_key(tag: str) -> str:
    safe = str(tag).strip()[:64].replace(":", "_")
    return f"{_key_prefix()}:tag:{safe}"


def _preempt_channel() -> str:
    return f"{_key_prefix()}:preempt"


def _node_id() -> str:
    nid = (os.environ.get("JACHIN_L3_NODE_ID") or os.environ.get("JACHIN_COORDINATOR_NODE_ID") or "").strip()
    if nid:
        return nid[:128]
    return f"{socket.gethostname()}-{os.getpid()}"[:128]


def _redis_url() -> str | None:
    for key in ("JACHIN_REDIS_URL", "REDIS_URL"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            if "://" not in raw:
                return f"redis://{raw}"
            return raw
    try:
        from core.config import settings

        url = getattr(settings, "REDIS_URL", None)
        if url:
            s = str(url).strip()
            if s and "://" not in s:
                return f"redis://{s}"
            return s or None
    except Exception:
        pass
    return None


def get_redis_client() -> Any | None:
    """懒加载 Redis 客户端；失败返回 None（调用方回退 SQLite）。"""
    global _client, _client_failed
    if _client is not None:
        return _client
    if _client_failed:
        return None
    url = _redis_url()
    if not url:
        _client_failed = True
        logger.warning("[GlobalRegistryRedis] 未配置 JACHIN_REDIS_URL / REDIS_URL")
        return None
    cluster = (os.environ.get("JACHIN_REDIS_CLUSTER") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    try:
        if cluster:
            from redis.cluster import RedisCluster

            c = RedisCluster.from_url(url, decode_responses=True)
        else:
            import redis

            c = redis.from_url(url, decode_responses=True)
        c.ping()
        _client = c
        logger.info(
            "[GlobalRegistryRedis] connected cluster=%s prefix=%s",
            cluster,
            _key_prefix(),
        )
        return _client
    except Exception as e:
        _client_failed = True
        logger.warning("[GlobalRegistryRedis] 连接失败，将回退 SQLite: %s", e)
        return None


def redis_available() -> bool:
    return get_redis_client() is not None


def _task_ttl_sec() -> int:
    try:
        return max(30, int(float(os.environ.get("JACHIN_GLOBAL_REGISTRY_TTL") or "300")))
    except ValueError:
        return 300


def _encode_task(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _decode_task(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def register_task_redis(
    run_id: str,
    *,
    channel: str = "",
    session_key: str = "",
    priority: str = "P1",
    resource_tags: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    client = get_redis_client()
    if not client:
        return False
    tags = [str(t).strip()[:64] for t in (resource_tags or []) if str(t).strip()][:8]
    now = time.time()
    ex = _task_ttl_sec() * 2
    payload: dict[str, Any] = {
        "run_id": run_id,
        "pid": os.getpid(),
        "channel": channel,
        "session_key": session_key,
        "priority": priority,
        "status": "running",
        "resource_tags": tags,
        "started_at": now,
        "preempted_by": "",
        "extra": dict(extra or {}) | {"node_id": _node_id()},
    }
    try:
        pipe = client.pipeline()
        pipe.set(_task_key(run_id), _encode_task(payload), ex=ex)
        pipe.sadd(_running_key(), run_id)
        for t in tags:
            pipe.sadd(_tag_key(t), run_id)
        pipe.execute()
        logger.debug("[GlobalRegistryRedis] register run_id=%s tags=%s", run_id[:12], tags)
        return True
    except Exception as e:
        logger.warning("[GlobalRegistryRedis] register failed: %s", e)
        return False


def unregister_task_redis(run_id: str) -> bool:
    client = get_redis_client()
    if not client:
        return False
    try:
        raw = client.get(_task_key(run_id))
        data = _decode_task(raw)
        tags = list(data.get("resource_tags") or []) if data else []
        if data:
            data["status"] = "done"
            client.set(_task_key(run_id), _encode_task(data), ex=60)
        client.srem(_running_key(), run_id)
        for t in tags:
            client.srem(_tag_key(str(t)), run_id)
        return True
    except Exception as e:
        logger.warning("[GlobalRegistryRedis] unregister failed: %s", e)
        return False


def touch_task_redis(run_id: str) -> bool:
    """长 run 续期任务键 TTL（需 JACHIN_GLOBAL_REGISTRY_REDIS_TOUCH=1）。"""
    if not redis_touch_enabled():
        return False
    client = get_redis_client()
    if not client:
        return False
    rid = (run_id or "").strip()
    if not rid:
        return False
    try:
        return bool(client.expire(_task_key(rid), _task_ttl_sec() * 2))
    except Exception:
        return False


def publish_preempt_message(run_id: str, preempted_by: str) -> bool:
    """向集群广播抢占取消（各节点订阅后 request_cancel_run）。"""
    if not preempt_pubsub_enabled():
        return False
    client = get_redis_client()
    if not client:
        return False
    rid = (run_id or "").strip()
    if not rid:
        return False
    try:
        payload = json.dumps(
            {"run_id": rid, "preempted_by": preempted_by, "node_id": _node_id()},
            ensure_ascii=False,
        )
        client.publish(_preempt_channel(), payload)
        return True
    except Exception as e:
        logger.debug("[GlobalRegistryRedis] publish preempt failed: %s", e)
        return False


def _preempt_listener_loop() -> None:
    client = get_redis_client()
    if not client:
        return
    try:
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(_preempt_channel())
        logger.info("[GlobalRegistryRedis] preempt pubsub listening on %s", _preempt_channel())
        for message in pubsub.listen():
            if not message or message.get("type") != "message":
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            try:
                obj = json.loads(str(data))
            except Exception:
                continue
            rid = str(obj.get("run_id") or "").strip()
            if not rid:
                continue
            by = str(obj.get("preempted_by") or "")[:64]
            from_node = str(obj.get("node_id") or "")[:64]
            if from_node and from_node == _node_id():
                continue
            try:
                from l3_node.primitives.agent_tasks.agent_cancel import request_cancel_run

                ok = bool(request_cancel_run(rid))
                logger.info(
                    "[GlobalRegistryRedis][PreemptSub] cancel run_id=%s by=%s ok=%s",
                    rid[:12],
                    by[:12],
                    ok,
                )
            except Exception as e:
                logger.debug("[GlobalRegistryRedis] preempt cancel failed: %s", e)
    except Exception as e:
        logger.warning("[GlobalRegistryRedis] preempt listener exit: %s", e)


def start_preempt_subscriber() -> None:
    """幂等启动抢占订阅线程（http on_startup 调用）。"""
    global _subscriber_thread, _subscriber_started
    if _subscriber_started or not preempt_pubsub_enabled():
        return
    if not redis_available():
        return
    _subscriber_started = True

    def _run() -> None:
        _preempt_listener_loop()

    _subscriber_thread = threading.Thread(
        target=_run,
        name="gtreg-preempt-sub",
        daemon=True,
    )
    _subscriber_thread.start()


def mark_preempted_redis(run_id: str, preempted_by: str) -> bool:
    client = get_redis_client()
    if not client:
        return False
    try:
        raw = client.get(_task_key(run_id))
        data = _decode_task(raw)
        if not data:
            return False
        data["status"] = "preempted"
        data["preempted_by"] = preempted_by
        client.set(_task_key(run_id), _encode_task(data), ex=_task_ttl_sec() * 2)
        publish_preempt_message(run_id, preempted_by)
        return True
    except Exception as e:
        logger.debug("[GlobalRegistryRedis] mark preempted failed: %s", e)
        return False


def list_running_tasks_redis(
    *,
    include_done: bool = False,
    include_zombie: bool = False,
) -> list[dict[str, Any]]:
    """返回任务 dict 列表（与 GlobalTask.to_dict 字段对齐）。"""
    client = get_redis_client()
    if not client:
        return []
    ttl = float(_task_ttl_sec())
    now = time.time()
    out: list[dict[str, Any]] = []
    try:
        run_ids = list(client.smembers(_running_key()) or [])
    except Exception as e:
        logger.debug("[GlobalRegistryRedis] smembers failed: %s", e)
        return []

    for rid in run_ids:
        rid_s = str(rid)
        try:
            raw = client.get(_task_key(rid_s))
        except Exception:
            continue
        data = _decode_task(raw)
        if not data:
            try:
                client.srem(_running_key(), rid_s)
            except Exception:
                pass
            continue
        status = str(data.get("status") or "running")
        started = float(data.get("started_at") or now)
        if status == "running" and not include_zombie and (now - started) > ttl:
            data["status"] = "done"
            try:
                client.set(_task_key(rid_s), _encode_task(data), ex=60)
                client.srem(_running_key(), rid_s)
                for t in data.get("resource_tags") or []:
                    client.srem(_tag_key(str(t)), rid_s)
            except Exception:
                pass
            continue
        if status == "done" and not include_done:
            try:
                client.srem(_running_key(), rid_s)
            except Exception:
                pass
            continue
        if status != "done" or include_done:
            data["age_sec"] = round(now - started, 1)
            out.append(data)
    out.sort(key=lambda x: float(x.get("started_at") or 0), reverse=True)
    return out[:100]


def find_tasks_by_tags_redis(tags: list[str]) -> list[str]:
    """按 resource_tags 并集查找 run_id（抢占优化）。"""
    client = get_redis_client()
    if not client or not tags:
        return []
    keys = [_tag_key(t) for t in tags if str(t).strip()]
    if not keys:
        return []
    try:
        if len(keys) == 1:
            return [str(x) for x in (client.smembers(keys[0]) or [])]
        return [str(x) for x in (client.sunion(*keys) or [])]
    except Exception:
        return []


def get_redis_registry_summary() -> dict[str, Any]:
    """Redis 后端诊断摘要。"""
    return {
        "backend": "redis",
        "redis_requested": redis_backend_requested(),
        "redis_connected": redis_available(),
        "redis_url_configured": bool(_redis_url()),
        "key_prefix": _key_prefix(),
        "node_id": _node_id(),
        "cluster_mode": (os.environ.get("JACHIN_REDIS_CLUSTER") or "").strip().lower()
        in ("1", "true", "yes", "on"),
        "dual_write": dual_write_enabled(),
        "preempt_pubsub": preempt_pubsub_enabled(),
        "subscriber_started": _subscriber_started,
    }
