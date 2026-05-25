"""
PersistedIntent — 意图持久化（§5 Layer 1）

将用户说的「每周一自动做 X」之类的意图写入 SQLite，进程重启后由
IntentRecovery 自动恢复定时调度，不再依赖外部 cron 文件。

DB 路径：$JACHIN_HOME/workspace/persisted_intents.sqlite3
（默认 ~/.jachin/workspace/persisted_intents.sqlite3）

环境变量
----------
JACHIN_INTENT_PERSIST_DISABLE=1    关闭持久化写入（读 API 仍可用）
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("autonomy.intent_persister")

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

TriggerType = Literal["cron", "event", "condition", "interval"]
IntentStatus = Literal["active", "paused", "completed", "failed"]


@dataclass
class IntentTrigger:
    type: TriggerType
    cron: str | None = None          # "0 9 * * 1" = 每周一 9 点
    event: str | None = None         # "on_new_feishu_message"
    condition: str | None = None     # "when: memory.contains('urgent')"
    interval_sec: int | None = None


@dataclass
class PersistedIntent:
    intent_id: str
    description: str                  # 自然语言描述，供诊断
    trigger: IntentTrigger
    action: str                       # 要执行的任务描述（传给 run_agent）
    created_at: float
    enabled: bool = True
    status: IntentStatus = "active"
    last_executed_at: float | None = None
    last_result: str | None = None    # 最近一次执行结果摘要
    consecutive_failures: int = 0
    max_retries_per_execution: int = 3
    failure_notification_channel: str | None = None  # 失败时通知哪个 channel

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trigger"] = asdict(self.trigger)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PersistedIntent":
        trigger_raw = d.pop("trigger", {})
        trigger = IntentTrigger(**{k: v for k, v in trigger_raw.items() if k in IntentTrigger.__dataclass_fields__})
        return cls(trigger=trigger, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_db_conn: sqlite3.Connection | None = None


def _db_path() -> Path:
    home = Path(os.environ.get("JACHIN_HOME", "~/.jachin")).expanduser()
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace / "persisted_intents.sqlite3"


def _get_conn() -> sqlite3.Connection:
    global _db_conn
    with _lock:
        if _db_conn is not None:
            return _db_conn
        path = _db_path()
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS persisted_intents (
                intent_id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                trigger_json TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                last_executed_at REAL,
                last_result TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                max_retries_per_execution INTEGER NOT NULL DEFAULT 3,
                failure_notification_channel TEXT
            )
        """)
        conn.commit()
        _db_conn = conn
        return conn


def _is_disabled() -> bool:
    return (os.environ.get("JACHIN_INTENT_PERSIST_DISABLE") or "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class IntentPersister:
    """意图 SQLite 持久化 CRUD，线程安全。"""

    def save(self, intent: PersistedIntent) -> None:
        if _is_disabled():
            return
        conn = _get_conn()
        trigger_json = json.dumps(asdict(intent.trigger), ensure_ascii=False)
        with _lock:
            conn.execute("""
                INSERT OR REPLACE INTO persisted_intents
                (intent_id, description, trigger_json, action, created_at,
                 enabled, status, last_executed_at, last_result,
                 consecutive_failures, max_retries_per_execution,
                 failure_notification_channel)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                intent.intent_id,
                intent.description,
                trigger_json,
                intent.action,
                intent.created_at,
                1 if intent.enabled else 0,
                intent.status,
                intent.last_executed_at,
                intent.last_result,
                intent.consecutive_failures,
                intent.max_retries_per_execution,
                intent.failure_notification_channel,
            ))
            conn.commit()
        logger.info("[IntentPersist] saved intent %s: %s", intent.intent_id, intent.description[:60])

    def create(
        self,
        description: str,
        action: str,
        trigger_type: TriggerType,
        *,
        cron: str | None = None,
        event: str | None = None,
        condition: str | None = None,
        interval_sec: int | None = None,
        failure_notification_channel: str | None = None,
    ) -> PersistedIntent:
        intent = PersistedIntent(
            intent_id=str(uuid.uuid4()),
            description=description,
            trigger=IntentTrigger(
                type=trigger_type,
                cron=cron,
                event=event,
                condition=condition,
                interval_sec=interval_sec,
            ),
            action=action,
            created_at=time.time(),
            failure_notification_channel=failure_notification_channel,
        )
        self.save(intent)
        return intent

    def list_all(self, enabled_only: bool = False) -> list[PersistedIntent]:
        conn = _get_conn()
        with _lock:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM persisted_intents WHERE enabled=1 ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM persisted_intents ORDER BY created_at DESC"
                ).fetchall()
        result = []
        for row in rows:
            try:
                trigger = IntentTrigger(**json.loads(row["trigger_json"]))
                intent = PersistedIntent(
                    intent_id=row["intent_id"],
                    description=row["description"],
                    trigger=trigger,
                    action=row["action"],
                    created_at=float(row["created_at"] or 0),
                    enabled=bool(row["enabled"]),
                    status=row["status"] or "active",
                    last_executed_at=row["last_executed_at"],
                    last_result=row["last_result"],
                    consecutive_failures=int(row["consecutive_failures"] or 0),
                    max_retries_per_execution=int(row["max_retries_per_execution"] or 3),
                    failure_notification_channel=row["failure_notification_channel"],
                )
                result.append(intent)
            except Exception as e:
                logger.warning("[IntentPersist] parse row error: %s", e)
        return result

    def get(self, intent_id: str) -> PersistedIntent | None:
        conn = _get_conn()
        with _lock:
            row = conn.execute(
                "SELECT * FROM persisted_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        if not row:
            return None
        try:
            trigger = IntentTrigger(**json.loads(row["trigger_json"]))
            return PersistedIntent(
                intent_id=row["intent_id"],
                description=row["description"],
                trigger=trigger,
                action=row["action"],
                created_at=float(row["created_at"] or 0),
                enabled=bool(row["enabled"]),
                status=row["status"] or "active",
                last_executed_at=row["last_executed_at"],
                last_result=row["last_result"],
                consecutive_failures=int(row["consecutive_failures"] or 0),
                max_retries_per_execution=int(row["max_retries_per_execution"] or 3),
                failure_notification_channel=row["failure_notification_channel"],
            )
        except Exception as e:
            logger.warning("[IntentPersist] get parse error: %s", e)
            return None

    def set_enabled(self, intent_id: str, enabled: bool) -> bool:
        conn = _get_conn()
        with _lock:
            cur = conn.execute(
                "UPDATE persisted_intents SET enabled=? WHERE intent_id=?",
                (1 if enabled else 0, intent_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def delete(self, intent_id: str) -> bool:
        conn = _get_conn()
        with _lock:
            cur = conn.execute(
                "DELETE FROM persisted_intents WHERE intent_id=?", (intent_id,)
            )
            conn.commit()
        logger.info("[IntentPersist] deleted intent %s", intent_id)
        return cur.rowcount > 0

    def autoreset_failed(self, intent_id: str) -> bool:
        """
        AK：将 status=failed 的意图重置为 active，consecutive_failures 归零。
        用于「失败意图自动重置」场景（AwarenessLoop 超时后恢复重试）。
        返回 True 表示操作成功。
        """
        conn = _get_conn()
        with _lock:
            cur = conn.execute(
                """
                UPDATE persisted_intents
                SET status='active', consecutive_failures=0
                WHERE intent_id=? AND status='failed'
                """,
                (intent_id,),
            )
            conn.commit()
        ok = cur.rowcount > 0
        if ok:
            logger.info("[IntentPersist][AK] intent %s autoreset to active", intent_id)
        return ok

    def update_extra_meta(self, intent_id: str, extra_meta: dict) -> bool:
        """
        AQ — 更新意图的 extra_meta 字段（用于 Level 3 自愈注入建议路径）。
        返回 True 表示操作成功。
        """
        import json as _json
        if not isinstance(extra_meta, dict):
            return False
        try:
            meta_str = _json.dumps(extra_meta, ensure_ascii=False)
        except Exception:
            return False
        conn = _get_conn()
        with _lock:
            cur = conn.execute(
                "UPDATE persisted_intents SET extra_meta_json=? WHERE intent_id=?",
                (meta_str, intent_id),
            )
            conn.commit()
        ok = cur.rowcount > 0
        if ok:
            logger.debug("[IntentPersist][AQ] intent %s extra_meta updated", intent_id)
        return ok

    def record_execution(
        self,
        intent_id: str,
        success: bool,
        result_summary: str = "",
    ) -> None:
        """执行完成后更新 last_executed_at / consecutive_failures。"""
        conn = _get_conn()
        now = time.time()
        with _lock:
            row = conn.execute(
                "SELECT consecutive_failures, max_retries_per_execution FROM persisted_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if not row:
                return
            prev_failures = int(row["consecutive_failures"] or 0)
            max_retries = int(row["max_retries_per_execution"] or 3)
            new_failures = 0 if success else prev_failures + 1
            new_status = "active"
            if not success and new_failures >= max_retries:
                new_status = "failed"
                logger.warning(
                    "[IntentPersist] intent %s failed %d/%d times, status → failed",
                    intent_id, new_failures, max_retries,
                )
            conn.execute("""
                UPDATE persisted_intents
                SET last_executed_at=?, last_result=?, consecutive_failures=?, status=?
                WHERE intent_id=?
            """, (now, result_summary[:500], new_failures, new_status, intent_id))
            conn.commit()


# ---------------------------------------------------------------------------
# 进程重启后恢复调度（IntentRecovery）
# ---------------------------------------------------------------------------

class IntentRecovery:
    """
    进程重启后，将 SQLite 中 enabled=1、type='cron'/'interval' 的意图恢复到
    APScheduler 或其他调度器。

    使用方式（在 bootstrap.py 或 app startup 中）：
        from l3_node.autonomy.intent_persister import IntentRecovery
        IntentRecovery().restore_to_scheduler(scheduler)
    """

    def restore_to_scheduler(self, scheduler: Any) -> int:
        """
        将 enabled cron/interval 意图注册到 APScheduler 实例。
        返回成功恢复的意图数量。

        若调度器未安装或意图无法解析，跳过并记日志，不抛异常。
        """
        persister = IntentPersister()
        intents = persister.list_all(enabled_only=True)
        restored = 0
        for intent in intents:
            try:
                if intent.trigger.type == "cron" and intent.trigger.cron:
                    self._add_cron_job(scheduler, intent)
                    restored += 1
                elif intent.trigger.type == "interval" and intent.trigger.interval_sec:
                    self._add_interval_job(scheduler, intent)
                    restored += 1
                else:
                    logger.debug(
                        "[IntentRecovery] intent %s type=%s skipped (event/condition driven)",
                        intent.intent_id, intent.trigger.type,
                    )
            except Exception as e:
                logger.warning("[IntentRecovery] failed to restore %s: %s", intent.intent_id, e)
        if restored:
            logger.info("[IntentRecovery] restored %d intent(s) to scheduler", restored)
        return restored

    def _add_cron_job(self, scheduler: Any, intent: PersistedIntent) -> None:
        from apscheduler.triggers.cron import CronTrigger

        cron_str = intent.trigger.cron or ""
        parts = cron_str.strip().split()
        if len(parts) != 5:
            raise ValueError(f"invalid cron '{cron_str}' for intent {intent.intent_id}")
        minute, hour, day, month, day_of_week = parts
        trigger = CronTrigger(
            minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week
        )
        job_id = f"persisted_intent_{intent.intent_id}"
        scheduler.add_job(
            self._make_job_fn(intent),
            trigger=trigger,
            id=job_id,
            name=intent.description[:80],
            replace_existing=True,
        )
        logger.info("[IntentRecovery] registered cron job %s: %s", job_id, cron_str)

    def _add_interval_job(self, scheduler: Any, intent: PersistedIntent) -> None:
        from apscheduler.triggers.interval import IntervalTrigger

        seconds = intent.trigger.interval_sec or 3600
        trigger = IntervalTrigger(seconds=seconds)
        job_id = f"persisted_intent_{intent.intent_id}"
        scheduler.add_job(
            self._make_job_fn(intent),
            trigger=trigger,
            id=job_id,
            name=intent.description[:80],
            replace_existing=True,
        )
        logger.info("[IntentRecovery] registered interval job %s: every %ds", job_id, seconds)

    def _make_job_fn(self, intent: PersistedIntent):
        """返回一个可被 APScheduler 调用的同步函数，内部通过 asyncio 执行任务。"""
        import asyncio

        intent_id = intent.intent_id
        action = intent.action
        description = intent.description

        def _job():
            persister = IntentPersister()
            logger.info("[IntentRecovery] firing intent %s: %s", intent_id, description[:60])
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            try:
                from l3_node.agent_core import run_agent
                from l3_node.scheduled_global_registry import (
                    get_scheduled_l3_engine,
                    run_agent_implicit_attribution_for_scheduled,
                    scheduled_global_task_scope,
                )

                with scheduled_global_task_scope(
                    "autonomy_intent",
                    intent_id,
                    title=description[:80],
                    extra_resource_tags=[f"intent:{intent_id[:48]}"],
                ) as sched_rid:
                    engine = get_scheduled_l3_engine()
                    result = loop.run_until_complete(
                        run_agent(
                            action,
                            engine,
                            implicit_attribution=run_agent_implicit_attribution_for_scheduled(
                                "autonomy_intent",
                                intent_id,
                                parent_run_id=sched_rid,
                                base={"channel": "autonomy_intent"},
                            ),
                        )
                    )
                persister.record_execution(intent_id, success=True, result_summary=str(result)[:300])
            except Exception as e:
                logger.error("[IntentRecovery] intent %s failed: %s", intent_id, e)
                persister.record_execution(intent_id, success=False, result_summary=str(e)[:300])

        return _job


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_persister: IntentPersister | None = None


def get_intent_persister() -> IntentPersister:
    global _persister
    if _persister is None:
        _persister = IntentPersister()
    return _persister
