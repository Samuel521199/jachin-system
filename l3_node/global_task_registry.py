"""
GlobalTaskRegistry — 跨进程 SSOT + resource_tags 抢占调度（AT）

在单机多进程（或多个 L3 实例）场景下，用 SQLite WAL 作为进程间共享状态：

- 任务注册：每个 run_agent 在进程内注册同时写入共享 DB
- resource_tags 冲突检测：高优先级任务启动时，可查询当前持有相同 resource_tags 的
  低优先级任务并发送抢占信号（SQLite 行标记 + HTTP 可选通知）
- 优先级定义：P1（用户前台）> P2（定时强制）> P3（后台批量）> P4（低优先级自动）

与进程内版本的关系：
  进程内 task_runtime_registry.py 保持原有接口不变（向后兼容）；
  GlobalTaskRegistry 作为「跨进程 SSOT 扩展层」，同时在本地内存 + SQLite 双写；
  读取侧（prompt 注入、抢占检测）优先用 SQLite，降级到进程内内存。

环境变量
--------
JACHIN_GLOBAL_REGISTRY_ENABLE=1     开启跨进程 SSOT（默认关，默认仅进程内）
JACHIN_GLOBAL_REGISTRY_PREEMPT=1    开启 resource_tags 抢占逻辑（需先开启上项）
JACHIN_GLOBAL_REGISTRY_TTL=300      僵尸任务超时清除秒（默认 300s）
JACHIN_GLOBAL_REGISTRY_REDIS=1      Redis 集群 SSOT（见 global_registry_redis.py）
JACHIN_GLOBAL_REGISTRY_BACKEND=redis|sqlite  显式后端（默认 sqlite；Redis 失败回退 SQLite）
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()

TaskPriority = Literal["P1", "P2", "P3", "P4"]
TaskStatus = Literal["running", "queued", "preempted", "done"]


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def global_registry_enabled() -> bool:
    return (os.environ.get("JACHIN_GLOBAL_REGISTRY_ENABLE") or "").strip().lower() in (
        "1", "true", "yes"
    )


def preempt_enabled() -> bool:
    return global_registry_enabled() and (
        os.environ.get("JACHIN_GLOBAL_REGISTRY_PREEMPT") or ""
    ).strip().lower() in ("1", "true", "yes")


def use_redis_backend() -> bool:
    """当前是否使用 Redis 作为 SSOT（连接失败时由 ``_ssot_*`` 回退 SQLite）。"""
    if not global_registry_enabled():
        return False
    try:
        from l3_node.global_registry_redis import redis_backend_requested, redis_available

        return redis_backend_requested() and redis_available()
    except ImportError:
        return False


def _task_ttl() -> float:
    raw = (os.environ.get("JACHIN_GLOBAL_REGISTRY_TTL") or "300").strip()
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 300.0


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    root = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()
    d = root / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d / "global_task_registry.sqlite3"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=8.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            run_id       TEXT PRIMARY KEY,
            pid          INTEGER NOT NULL,
            channel      TEXT NOT NULL DEFAULT '',
            session_key  TEXT NOT NULL DEFAULT '',
            priority     TEXT NOT NULL DEFAULT 'P1',
            status       TEXT NOT NULL DEFAULT 'running',
            resource_tags_json TEXT NOT NULL DEFAULT '[]',
            started_at   REAL NOT NULL,
            preempted_by TEXT NOT NULL DEFAULT '',
            extra_json   TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class GlobalTask:
    run_id: str
    pid: int
    channel: str
    session_key: str
    priority: TaskPriority
    status: TaskStatus
    resource_tags: list[str]
    started_at: float
    preempted_by: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def age_sec(self) -> float:
        return time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"age_sec": round(self.age_sec, 1)}


@dataclass
class PreemptResult:
    preempted_run_ids: list[str]
    conflict_tags: list[str]
    message: str


# ---------------------------------------------------------------------------
# 核心操作
# ---------------------------------------------------------------------------

def _notify_preempt_pubsub(run_id: str, preempted_by: str) -> None:
    try:
        from l3_node.global_registry_redis import publish_preempt_message

        publish_preempt_message(run_id, preempted_by)
    except Exception:
        pass


def _write_sqlite_register(
    run_id: str,
    *,
    channel: str,
    session_key: str,
    priority: TaskPriority,
    tags: list[str],
    extra: dict[str, Any] | None,
) -> None:
    now = time.time()
    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO tasks
                  (run_id, pid, channel, session_key, priority, status,
                   resource_tags_json, started_at, extra_json)
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  status='running', started_at=excluded.started_at
                """,
                (
                    run_id, os.getpid(), channel, session_key, priority,
                    json.dumps(tags, ensure_ascii=False),
                    now,
                    json.dumps(extra or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def register_task(
    run_id: str,
    *,
    channel: str = "",
    session_key: str = "",
    priority: TaskPriority = "P1",
    resource_tags: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """注册一个正在运行的任务到全局 SQLite 注册表（进程内内存 + SQLite 双写）。"""
    # 进程内写（原有逻辑）
    try:
        from l3_node.task_runtime_registry import register_foreground_task
        register_foreground_task(
            run_id=run_id,
            channel=channel,
            session_key=session_key,
            resource_tags=resource_tags,
        )
    except Exception:
        pass

    if not global_registry_enabled():
        return

    tags = [str(t).strip()[:64] for t in (resource_tags or []) if str(t).strip()][:8]
    redis_ok = False
    dual = False
    try:
        from l3_node.global_registry_redis import (
            dual_write_enabled,
            redis_available,
            redis_backend_requested,
            register_task_redis,
        )

        want_redis = redis_backend_requested() and redis_available()
        dual = dual_write_enabled() and want_redis
        if want_redis:
            redis_ok = register_task_redis(
                run_id,
                channel=channel,
                session_key=session_key,
                priority=priority,
                resource_tags=tags,
                extra=extra,
            )
            if redis_ok and not dual:
                logger.debug(
                    "[GlobalTaskRegistry][redis] registered run_id=%s priority=%s tags=%s",
                    run_id,
                    priority,
                    tags,
                )
                return
    except Exception as e:
        logger.warning("[GlobalTaskRegistry] Redis register 失败，回退 SQLite: %s", e)

    if not redis_ok or dual:
        _write_sqlite_register(
            run_id,
            channel=channel,
            session_key=session_key,
            priority=priority,
            tags=tags,
            extra=extra,
        )
    logger.debug(
        "[GlobalTaskRegistry] registered run_id=%s priority=%s tags=%s redis=%s dual=%s",
        run_id,
        priority,
        tags,
        redis_ok,
        dual,
    )


def unregister_task(run_id: str) -> None:
    """任务完成/取消时调用（进程内 + SQLite 双写）。"""
    try:
        from l3_node.task_runtime_registry import unregister_foreground_task
        unregister_foreground_task(run_id)
    except Exception:
        pass

    if not global_registry_enabled():
        return

    redis_ok = False
    dual = False
    want_redis = False
    try:
        from l3_node.global_registry_redis import (
            dual_write_enabled,
            redis_available,
            redis_backend_requested,
            unregister_task_redis,
        )

        want_redis = redis_backend_requested() and redis_available()
        dual = dual_write_enabled() and want_redis
        if want_redis:
            redis_ok = unregister_task_redis(run_id)
            if redis_ok and not dual:
                return
    except Exception as e:
        logger.warning("[GlobalTaskRegistry] Redis unregister 失败，回退 SQLite: %s", e)

    with _LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE tasks SET status='done' WHERE run_id=?", (run_id,)
            )
            conn.commit()
        finally:
            conn.close()


def _dict_to_global_task(data: dict[str, Any]) -> GlobalTask:
    extra = data.get("extra")
    if not isinstance(extra, dict):
        extra = {}
    tags = data.get("resource_tags")
    if not isinstance(tags, list):
        tags = []
    return GlobalTask(
        run_id=str(data.get("run_id") or ""),
        pid=int(data.get("pid") or 0),
        channel=str(data.get("channel") or ""),
        session_key=str(data.get("session_key") or ""),
        priority=str(data.get("priority") or "P1"),  # type: ignore[arg-type]
        status=str(data.get("status") or "running"),  # type: ignore[arg-type]
        resource_tags=[str(t) for t in tags],
        started_at=float(data.get("started_at") or time.time()),
        preempted_by=str(data.get("preempted_by") or ""),
        extra=extra,
    )


def list_running_tasks(
    *,
    include_done: bool = False,
    include_zombie: bool = False,
) -> list[GlobalTask]:
    """列出全局注册表中的任务；自动清除超过 TTL 的僵尸任务。"""
    if not global_registry_enabled():
        return []
    if use_redis_backend():
        try:
            from l3_node.global_registry_redis import list_running_tasks_redis

            rows = list_running_tasks_redis(
                include_done=include_done,
                include_zombie=include_zombie,
            )
            return [_dict_to_global_task(r) for r in rows]
        except Exception as e:
            logger.warning("[GlobalTaskRegistry] Redis list 失败，回退 SQLite: %s", e)

    ttl = _task_ttl()
    now = time.time()
    with _LOCK:
        conn = _get_conn()
        try:
            # 清除僵尸
            if not include_zombie:
                conn.execute(
                    "UPDATE tasks SET status='done' WHERE status='running' AND ? - started_at > ?",
                    (now, ttl),
                )
                conn.commit()
            status_filter = "" if include_done else "WHERE status != 'done'"
            rows = conn.execute(
                f"SELECT * FROM tasks {status_filter} ORDER BY started_at DESC LIMIT 100"
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_task(r) for r in rows]


def _row_to_task(row: sqlite3.Row) -> GlobalTask:
    try:
        tags = json.loads(row["resource_tags_json"])
    except Exception:
        tags = []
    try:
        extra = json.loads(row["extra_json"])
    except Exception:
        extra = {}
    return GlobalTask(
        run_id=str(row["run_id"]),
        pid=int(row["pid"]),
        channel=str(row["channel"]),
        session_key=str(row["session_key"]),
        priority=str(row["priority"]),  # type: ignore[arg-type]
        status=str(row["status"]),  # type: ignore[arg-type]
        resource_tags=tags if isinstance(tags, list) else [],
        started_at=float(row["started_at"]),
        preempted_by=str(row["preempted_by"] or ""),
        extra=extra if isinstance(extra, dict) else {},
    )


# ---------------------------------------------------------------------------
# resource_tags 抢占调度
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


def check_and_preempt(
    new_run_id: str,
    new_priority: TaskPriority,
    new_resource_tags: list[str],
) -> PreemptResult:
    """
    新任务启动时调用：
    1. 检查是否有低优先级任务持有相同 resource_tags
    2. 若有且启用抢占，将其标记为 preempted + 尝试通过 foreground_run_registry 取消

    返回 PreemptResult（无论是否抢占成功，调用方可记录）。
    """
    if not preempt_enabled() or not new_resource_tags:
        return PreemptResult([], [], "preemption disabled or no resource_tags")

    new_prio_num = _PRIORITY_ORDER.get(new_priority, 99)
    conflict_tags: list[str] = []
    preempted: list[str] = []
    tasks = list_running_tasks()

    for task in tasks:
        if task.run_id == new_run_id or task.status != "running":
            continue
        task_prio_num = _PRIORITY_ORDER.get(task.priority, 99)
        if task_prio_num <= new_prio_num:
            continue  # 同等或更高优先级，不抢占
        # 检查 resource_tags 是否重叠
        overlap = set(new_resource_tags) & set(task.resource_tags)
        if not overlap:
            continue
        conflict_tags.extend(overlap)
        preempted.append(task.run_id)
        # 标记为已抢占
        marked = False
        if use_redis_backend():
            try:
                from l3_node.global_registry_redis import mark_preempted_redis

                marked = mark_preempted_redis(task.run_id, new_run_id)
            except Exception as e:
                logger.debug("[GlobalTaskRegistry] redis mark preempted failed: %s", e)
        if not marked:
            try:
                with _LOCK:
                    conn = _get_conn()
                    try:
                        conn.execute(
                            "UPDATE tasks SET status='preempted', preempted_by=? WHERE run_id=?",
                            (new_run_id, task.run_id),
                        )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception as e:
                logger.debug("[GlobalTaskRegistry] mark preempted failed: %s", e)
        _notify_preempt_pubsub(task.run_id, new_run_id)
        # 尝试通过 foreground_run_registry 取消（同进程）
        cancelled_local = False
        try:
            from l3_node.primitives.agent_tasks.agent_cancel import request_cancel_run

            cancelled_local = bool(request_cancel_run(task.run_id))
            logger.info(
                "[GlobalTaskRegistry][Preempt] run_id=%s preempted by %s (tags=%s) local_cancel=%s",
                task.run_id, new_run_id, list(overlap), cancelled_local,
            )
        except Exception:
            pass
        if not cancelled_local:
            try:
                from l3_node.global_registry_remote import try_remote_preempt_after_local

                try_remote_preempt_after_local([task.run_id])
            except Exception:
                pass

    return PreemptResult(
        preempted_run_ids=preempted,
        conflict_tags=list(set(conflict_tags)),
        message=(
            f"抢占 {len(preempted)} 个低优先级任务（冲突 tags: {list(set(conflict_tags))}）"
            if preempted else "无冲突任务"
        ),
    )


def get_global_registry_summary() -> dict[str, Any]:
    """返回供 HTTP 诊断端点使用的全局注册表摘要。"""
    tasks = list_running_tasks(include_done=False)
    backend = "redis" if use_redis_backend() else ("sqlite" if global_registry_enabled() else "memory")
    out: dict[str, Any] = {
        "enabled": global_registry_enabled(),
        "preempt_enabled": preempt_enabled(),
        "backend": backend,
        "running_count": sum(1 for t in tasks if t.status == "running"),
        "preempted_count": sum(1 for t in tasks if t.status == "preempted"),
        "tasks": [t.to_dict() for t in tasks[:20]],
    }
    try:
        from l3_node.global_registry_redis import get_redis_registry_summary, redis_backend_requested

        out["redis"] = get_redis_registry_summary()
        if redis_backend_requested() and backend != "redis":
            out["redis"]["fallback_sqlite"] = True
    except ImportError:
        pass
    return out
