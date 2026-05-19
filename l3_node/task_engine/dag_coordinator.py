"""
DAG Coordinator — Phase 2 中心化协调器（AS）

提供 L3 多节点集群中 DAG 续跑所需的三个核心能力：

1. 节点注册表（Node Registry）
   - 每个 L3 节点在启动时通过 register_node() 注册自身（node_id + http_url）
   - 定期心跳续约（heartbeat_interval_sec，默认 30s）
   - discover_peers() 返回活跃节点列表（心跳 < TTL）

2. 分布式 DAG 锁（Distributed DAG Lock）
   - 基于 SQLite CAS（Compare-And-Swap）实现乐观锁，不依赖 Redis/ZooKeeper
   - claim_dag(dag_id, node_id) — 若无主或锁已过期则抢占，返回是否成功
   - release_dag(dag_id, node_id) — 持有者主动释放
   - refresh_dag_lock(dag_id, node_id) — 续约（防止长任务被误抢）
   - get_dag_owner(dag_id) — 当前持有者

3. 自动对等节点发现（Peer Discovery）
   - 本地发现：同机器共享 SQLite（`workspace/dag_coordinator.sqlite3`）
   - 跨机器发现：通过 JACHIN_COORDINATOR_PEER_URLS（逗号分隔）轮询
     `GET /api/v1/registry/coordinator/info` 端点
   - find_idle_peer(exclude_self=True) — 寻找负载最低的空闲节点

同机器部署时无需额外配置（共享 SQLite）。
跨机器部署时需配置 JACHIN_COORDINATOR_PEER_URLS 让节点互相发现。

环境变量
--------
JACHIN_COORDINATOR_ENABLE=1            开启协调器（默认关）
JACHIN_COORDINATOR_NODE_ID             本节点 ID（默认 hostname:pid）
JACHIN_COORDINATOR_HTTP_URL            本节点对外可达的 HTTP URL（供对等节点回调）
JACHIN_COORDINATOR_PEER_URLS           逗号分隔的已知对等节点 URL（跨机器发现）
JACHIN_COORDINATOR_NODE_TTL=90         节点心跳超时秒（超过则视为离线，默认 90s）
JACHIN_COORDINATOR_LOCK_TTL=120        DAG 锁超时秒（超过则锁自动失效，默认 120s）
JACHIN_COORDINATOR_HEARTBEAT_SEC=30    心跳间隔秒（默认 30s）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import socket
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def coordinator_enabled() -> bool:
    return (os.environ.get("JACHIN_COORDINATOR_ENABLE") or "").strip().lower() in (
        "1", "true", "yes"
    )


def _self_node_id() -> str:
    raw = (os.environ.get("JACHIN_COORDINATOR_NODE_ID") or "").strip()
    if raw:
        return raw
    # 默认：hostname:pid（同机器多进程可区分）
    host = socket.gethostname()[:32]
    return f"{host}:{os.getpid()}"


def _self_http_url() -> str:
    return (os.environ.get("JACHIN_COORDINATOR_HTTP_URL") or "").strip()


def _peer_urls() -> list[str]:
    raw = (os.environ.get("JACHIN_COORDINATOR_PEER_URLS") or "").strip()
    if not raw:
        return []
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def _node_ttl() -> float:
    raw = (os.environ.get("JACHIN_COORDINATOR_NODE_TTL") or "90").strip()
    try:
        return max(10.0, float(raw))
    except ValueError:
        return 90.0


def _lock_ttl() -> float:
    raw = (os.environ.get("JACHIN_COORDINATOR_LOCK_TTL") or "120").strip()
    try:
        return max(10.0, float(raw))
    except ValueError:
        return 120.0


def _heartbeat_interval() -> float:
    raw = (os.environ.get("JACHIN_COORDINATOR_HEARTBEAT_SEC") or "30").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 30.0


# ---------------------------------------------------------------------------
# SQLite 存储
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    root = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()
    d = root / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d / "dag_coordinator.sqlite3"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id    TEXT PRIMARY KEY,
            http_url   TEXT NOT NULL DEFAULT '',
            last_beat  REAL NOT NULL,
            load_score REAL NOT NULL DEFAULT 0.0,
            extra_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS dag_locks (
            dag_id     TEXT PRIMARY KEY,
            owner_id   TEXT NOT NULL,
            acquired_at REAL NOT NULL,
            expires_at  REAL NOT NULL,
            lock_token  TEXT NOT NULL
        );
    """)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    conn = sqlite3.connect(str(path), timeout=8.0)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class NodeInfo:
    node_id: str
    http_url: str
    last_beat: float
    load_score: float = 0.0      # 0.0 = 空闲，1.0 = 满载；由节点自报
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_alive(self) -> bool:
        return (time.time() - self.last_beat) < _node_ttl()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "http_url": self.http_url,
            "last_beat": self.last_beat,
            "load_score": self.load_score,
            "is_alive": self.is_alive,
            "extra": self.extra,
        }


@dataclass
class DagLock:
    dag_id: str
    owner_id: str
    acquired_at: float
    expires_at: float
    lock_token: str

    @property
    def is_valid(self) -> bool:
        return time.time() < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"is_valid": self.is_valid}


# ---------------------------------------------------------------------------
# 节点注册表
# ---------------------------------------------------------------------------

def register_node(
    node_id: str,
    http_url: str,
    load_score: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> None:
    """注册或更新节点心跳（upsert）。"""
    now = time.time()
    extra_s = json.dumps(extra or {}, ensure_ascii=False)
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO nodes (node_id, http_url, last_beat, load_score, extra_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    http_url=excluded.http_url,
                    last_beat=excluded.last_beat,
                    load_score=excluded.load_score,
                    extra_json=excluded.extra_json
                """,
                (node_id, http_url, now, load_score, extra_s),
            )
            conn.commit()
        finally:
            conn.close()
    logger.debug("[Coordinator] register_node node_id=%s url=%s", node_id, http_url)


def heartbeat(node_id: str, load_score: float = 0.0) -> None:
    """仅更新心跳时间和负载分（快速路径）。"""
    now = time.time()
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE nodes SET last_beat=?, load_score=? WHERE node_id=?",
                (now, load_score, node_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_node(node_id: str) -> NodeInfo | None:
    with _LOCK:
        conn = _get_conn()
        try:
            row = conn.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return _row_to_node(row)


def list_alive_nodes(include_self: bool = True) -> list[NodeInfo]:
    """列出心跳仍在 TTL 内的所有节点。"""
    cutoff = time.time() - _node_ttl()
    self_id = _self_node_id()
    with _LOCK:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE last_beat > ? ORDER BY load_score ASC",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
    result = [_row_to_node(r) for r in rows]
    if not include_self:
        result = [n for n in result if n.node_id != self_id]
    return result


def _row_to_node(row: sqlite3.Row) -> NodeInfo:
    try:
        extra = json.loads(row["extra_json"])
    except Exception:
        extra = {}
    return NodeInfo(
        node_id=row["node_id"],
        http_url=row["http_url"],
        last_beat=float(row["last_beat"]),
        load_score=float(row["load_score"]),
        extra=extra if isinstance(extra, dict) else {},
    )


# ---------------------------------------------------------------------------
# 分布式 DAG 锁
# ---------------------------------------------------------------------------

def claim_dag(dag_id: str, node_id: str) -> tuple[bool, str]:
    """
    尝试获取 DAG 锁。
    返回 (success, lock_token)：
      success=True  — 抢锁成功，lock_token 用于后续续约/释放
      success=False — 锁已被其他节点持有且未过期
    """
    now = time.time()
    token = str(uuid.uuid4())
    expires = now + _lock_ttl()

    with _LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM dag_locks WHERE dag_id=?", (dag_id,)
            ).fetchone()

            if row is None:
                # 首次加锁
                conn.execute(
                    """
                    INSERT INTO dag_locks (dag_id, owner_id, acquired_at, expires_at, lock_token)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (dag_id, node_id, now, expires, token),
                )
                conn.commit()
                logger.info("[Coordinator] claim_dag dag=%s node=%s OK (new lock)", dag_id, node_id)
                return True, token

            # 锁已存在：检查是否已过期或被同节点持有
            existing_expires = float(row["expires_at"])
            existing_owner = str(row["owner_id"])

            if existing_owner == node_id:
                # 同节点续约
                conn.execute(
                    "UPDATE dag_locks SET expires_at=?, lock_token=? WHERE dag_id=?",
                    (expires, token, dag_id),
                )
                conn.commit()
                logger.debug("[Coordinator] claim_dag dag=%s node=%s renewed", dag_id, node_id)
                return True, token

            if now >= existing_expires:
                # 锁已超时，强制抢占
                conn.execute(
                    """
                    UPDATE dag_locks SET owner_id=?, acquired_at=?, expires_at=?, lock_token=?
                    WHERE dag_id=?
                    """,
                    (node_id, now, expires, token, dag_id),
                )
                conn.commit()
                logger.warning(
                    "[Coordinator] claim_dag dag=%s node=%s preempted from %s (lock expired)",
                    dag_id, node_id, existing_owner,
                )
                return True, token

            # 锁被其他节点持有且有效
            logger.debug(
                "[Coordinator] claim_dag dag=%s BLOCKED by %s (expires in %.0fs)",
                dag_id, existing_owner, existing_expires - now,
            )
            return False, ""
        finally:
            conn.close()


def release_dag(dag_id: str, node_id: str, lock_token: str) -> bool:
    """主动释放 DAG 锁；token 不匹配时拒绝（防止误释放他人锁）。"""
    with _LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT owner_id, lock_token FROM dag_locks WHERE dag_id=?", (dag_id,)
            ).fetchone()
            if row is None:
                return True  # 锁不存在，视为已释放
            if str(row["owner_id"]) != node_id or str(row["lock_token"]) != lock_token:
                logger.warning(
                    "[Coordinator] release_dag dag=%s refused: token mismatch (caller=%s)",
                    dag_id, node_id,
                )
                return False
            conn.execute("DELETE FROM dag_locks WHERE dag_id=?", (dag_id,))
            conn.commit()
        finally:
            conn.close()
    logger.info("[Coordinator] release_dag dag=%s node=%s released", dag_id, node_id)
    return True


def refresh_dag_lock(dag_id: str, node_id: str, lock_token: str) -> bool:
    """续约 DAG 锁（防止长任务运行中锁超时被抢占）。"""
    now = time.time()
    expires = now + _lock_ttl()
    with _LOCK:
        conn = _get_conn()
        try:
            cur = conn.execute(
                """
                UPDATE dag_locks SET expires_at=?
                WHERE dag_id=? AND owner_id=? AND lock_token=?
                """,
                (expires, dag_id, node_id, lock_token),
            )
            conn.commit()
        finally:
            conn.close()
    ok = cur.rowcount > 0
    if ok:
        logger.debug("[Coordinator] refresh_dag_lock dag=%s refreshed +%.0fs", dag_id, _lock_ttl())
    return ok


def get_dag_owner(dag_id: str) -> DagLock | None:
    """返回 DAG 当前锁信息；若锁不存在或已过期返回 None。"""
    with _LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM dag_locks WHERE dag_id=?", (dag_id,)
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    lock = DagLock(
        dag_id=dag_id,
        owner_id=str(row["owner_id"]),
        acquired_at=float(row["acquired_at"]),
        expires_at=float(row["expires_at"]),
        lock_token=str(row["lock_token"]),
    )
    return lock if lock.is_valid else None


def list_dag_locks() -> list[DagLock]:
    """列出所有当前有效的 DAG 锁（诊断用）。"""
    cutoff = time.time()
    with _LOCK:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM dag_locks WHERE expires_at > ? ORDER BY acquired_at DESC",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
    return [
        DagLock(
            dag_id=str(r["dag_id"]),
            owner_id=str(r["owner_id"]),
            acquired_at=float(r["acquired_at"]),
            expires_at=float(r["expires_at"]),
            lock_token=str(r["lock_token"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 跨机器 Peer 发现（HTTP 轮询）
# ---------------------------------------------------------------------------

async def discover_http_peers() -> list[NodeInfo]:
    """
    轮询 JACHIN_COORDINATOR_PEER_URLS，调用每个节点的
    GET /api/v1/registry/coordinator/info 端点，合并为 NodeInfo 列表。
    失败的节点静默跳过。
    """
    urls = _peer_urls()
    if not urls:
        return []
    results: list[NodeInfo] = []
    try:
        import aiohttp
    except ImportError:
        logger.debug("[Coordinator] aiohttp 不可用，跳过跨机器 peer 发现")
        return []

    async def _fetch_one(url: str) -> NodeInfo | None:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    f"{url}/api/v1/registry/coordinator/info",
                    timeout=aiohttp.ClientTimeout(total=5.0),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return NodeInfo(
                        node_id=str(data.get("node_id") or ""),
                        http_url=url,
                        last_beat=float(data.get("last_beat") or time.time()),
                        load_score=float(data.get("load_score") or 0.0),
                        extra=dict(data.get("extra") or {}),
                    )
        except Exception as e:
            logger.debug("[Coordinator] peer %s unreachable: %s", url, e)
            return None

    tasks = [_fetch_one(u) for u in urls]
    for coro in asyncio.as_completed(tasks):
        node = await coro
        if node and node.node_id:
            results.append(node)
    return results


def find_idle_peer(exclude_self: bool = True) -> NodeInfo | None:
    """
    从本地注册表找到负载最低的空闲节点。
    load_score < 0.5 视为空闲。排除自身（默认）。
    """
    nodes = list_alive_nodes(include_self=not exclude_self)
    self_id = _self_node_id()
    candidates = [
        n for n in nodes
        if (not exclude_self or n.node_id != self_id) and n.load_score < 0.5
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda n: n.load_score)


# ---------------------------------------------------------------------------
# 心跳后台任务
# ---------------------------------------------------------------------------

_heartbeat_task: asyncio.Task | None = None


async def start_heartbeat_loop(load_fn=None) -> None:
    """
    启动持续心跳循环（在 asyncio 事件循环中运行）。
    load_fn: 可选的同步函数，返回 0.0~1.0 的负载分；默认用后台任务数估算。
    """
    if not coordinator_enabled():
        return
    nid = _self_node_id()
    url = _self_http_url()
    interval = _heartbeat_interval()
    # 首次注册
    try:
        register_node(nid, url, load_score=_estimate_load())
        logger.info("[Coordinator] 节点已注册 node_id=%s url=%s", nid, url or "(无 URL)")
    except Exception as e:
        logger.warning("[Coordinator] 初始注册失败: %s", e)

    while True:
        await asyncio.sleep(interval)
        try:
            score = load_fn() if callable(load_fn) else _estimate_load()
            heartbeat(nid, load_score=score)
        except Exception as e:
            logger.debug("[Coordinator] heartbeat error: %s", e)


def _estimate_load() -> float:
    """
    估算本节点当前负载（0.0~1.0），用于 find_idle_peer 决策。
    基于当前前台运行任务数（foreground_run_registry）。
    """
    try:
        from l3_node.foreground_run_registry import get_foreground_run_count
        count = get_foreground_run_count()
        return min(1.0, count / 4.0)  # 4 个并发任务视为满载
    except Exception:
        return 0.0


def ensure_coordinator_started(app_loop=None) -> None:
    """
    在 http_server.py on_startup 中调用，幂等启动心跳循环。
    """
    global _heartbeat_task
    if not coordinator_enabled():
        return
    if _heartbeat_task is not None and not _heartbeat_task.done():
        return
    try:
        loop = app_loop or asyncio.get_event_loop()
        _heartbeat_task = loop.create_task(start_heartbeat_loop())
        logger.info("[Coordinator] heartbeat loop started node_id=%s", _self_node_id())
    except Exception as e:
        logger.warning("[Coordinator] 无法启动心跳: %s", e)


# ---------------------------------------------------------------------------
# 便捷汇总（供 HTTP info 端点）
# ---------------------------------------------------------------------------

def get_coordinator_info() -> dict[str, Any]:
    """返回本节点当前协调器状态摘要。"""
    nid = _self_node_id()
    return {
        "node_id": nid,
        "http_url": _self_http_url(),
        "last_beat": time.time(),
        "load_score": _estimate_load(),
        "enabled": coordinator_enabled(),
        "alive_nodes": len(list_alive_nodes()),
        "active_locks": len(list_dag_locks()),
        "extra": {
            "lock_ttl": _lock_ttl(),
            "node_ttl": _node_ttl(),
            "heartbeat_interval": _heartbeat_interval(),
        },
    }
