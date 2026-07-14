"""
Event Bus - 全息感官总线 (Omni-Sensory Bus) v8.0

端口-适配器架构：所有交互方式（Voice、Sprite、IM、CLI）作为外接感官器官，
统一归一化为 SensoryInputEvent -> SessionManager -> Agent Actor -> SensoryOutputEvent。

v8.0 Session Multiplexing：按 session_id 隔离，每 session 独立 Actor 协程，高并发记忆隔离。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# SQLite 持久化路径
EVENT_QUEUE_DB = Path.home() / ".jachin" / "event_queue.db"
OMNI_WORKER_COUNT = 3


# -----------------------------------------------------------------------------
# 标准感官协议 (Standard Sensory Protocol)
# -----------------------------------------------------------------------------

@dataclass
class SensoryInputEvent:
    """输入归一化：CLI/Voice/GUI/Webhook 统一结构"""
    source: str  # "cli" | "voice" | "gui" | "webhook" | "telegram" | "sprite" | "lark"
    intent: str  # 用户输入的文本
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SensoryOutputEvent:
    """输出多路分发：大脑结论 + 动作类型"""
    source_ref: str  # 回显输入来源，用于路由
    content: str
    action_type: str = "text"  # "text" | "tts_play" | "ui_animate"
    metadata: dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# OmniSensoryBus 单例 — 事件驱动架构核心
# -----------------------------------------------------------------------------

SQLITE_TIMEOUT = 15  # 等待锁的最大秒数
SQLITE_MAX_RETRIES = 5  # database is locked 时重试次数


def _init_sqlite_queue() -> None:
    """初始化 SQLite 表 omni_input_queue，启用 WAL 模式以支持多进程/多连接并发"""
    EVENT_QUEUE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(EVENT_QUEUE_DB), timeout=SQLITE_TIMEOUT)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")  # 15 秒
    conn.execute("""
        CREATE TABLE IF NOT EXISTS omni_input_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            intent TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_omni_status ON omni_input_queue(status)")
    conn.commit()
    conn.close()


def _persist_omni_input_sync(source: str, intent: str, metadata: dict[str, Any]) -> bool:
    """同步写入 SQLite，供 emit_omni_input / publish_input 调用。v8.0 全链路追踪：无 run_id 则自动生成"""
    _init_sqlite_queue()
    meta = dict(metadata or {})
    if not meta.get("run_id"):
        meta["run_id"] = uuid.uuid4().hex
    for attempt in range(SQLITE_MAX_RETRIES):
        try:
            conn = sqlite3.connect(str(EVENT_QUEUE_DB), timeout=SQLITE_TIMEOUT)
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute(
                "INSERT INTO omni_input_queue (source, intent, metadata_json, status) VALUES (?, ?, ?, 'pending')",
                (source, intent, json.dumps(meta, ensure_ascii=False)),
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < SQLITE_MAX_RETRIES - 1:
                import time
                time.sleep(0.2 * (attempt + 1))
                continue
            logger.warning("[OmniSensoryBus] SQLite 写入失败: %s", e)
            return False
        except Exception as e:
            logger.warning("[OmniSensoryBus] SQLite 写入失败: %s", e)
            return False
    return False


class OmniSensoryBus:
    """全息感官总线：subscribe + publish_input / publish_output；v8.0 Session Multiplexing"""

    _instance: OmniSensoryBus | None = None

    def __new__(cls) -> OmniSensoryBus:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._output_handlers: dict[str, list[Callable[..., Coroutine[Any, Any, None]]]] = defaultdict(list)
        self._step_callback: Callable[[str, str, str], None] | None = None
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._session_manager: SessionManager | None = None
        _init_sqlite_queue()
        logger.info("[OmniSensoryBus] 全息感官总线已初始化 (SQLite: %s)", EVENT_QUEUE_DB)

    def subscribe(self, event_type: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
        """
        订阅事件类型。event_type 示例：
        - "output"：全局输出
        - "output.cli"：仅 CLI 来源的输出
        - "output.voice"：仅 Voice 来源
        """
        self._output_handlers[event_type].append(handler)
        logger.debug("[OmniSensoryBus] 订阅 %s", event_type)

    async def publish_input(self, event: SensoryInputEvent) -> None:
        """异步发射输入事件，持久化到 SQLite (status=pending)"""
        ok = await asyncio.to_thread(
            _persist_omni_input_sync,
            event.source,
            event.intent,
            event.metadata,
        )
        if ok:
            logger.info("[OmniSensoryBus] 输入持久化: %s -> %s", event.source, event.intent[:50])
        else:
            logger.warning("[OmniSensoryBus] 持久化失败，丢弃来自 %s 的输入", event.source)

    async def publish_output(self, event: SensoryOutputEvent) -> None:
        """异步发射输出事件，分发给订阅者（含 output.* 全局）"""
        for etype in (f"output.{event.source_ref}", "output.*", "output"):
            for h in self._output_handlers.get(etype, []):
                try:
                    await h(event)
                except Exception as e:
                    logger.exception("[OmniSensoryBus] 输出处理器异常 %s: %s", etype, e)

    def set_step_callback(self, cb: Callable[[str, str, str], None] | None) -> None:
        """设置 CognitiveKernel 步骤打印回调"""
        self._step_callback = cb

    def start_brain_worker(self) -> None:
        """启动 v8.0 调度器：SessionManager + 单 dispatcher 协程"""
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        from core.session_manager import SessionManager

        async def _processor(task_data: dict[str, Any]) -> None:
            await _process_single_task(self, task_data)

        self._session_manager = SessionManager(_processor)
        self._dispatcher_task = asyncio.create_task(_dispatcher_loop(self))
        logger.info("[OmniSensoryBus] v8.0 Session Multiplexing 调度器已启动")

    def stop_brain_worker(self) -> None:
        """停止调度器"""
        if self._dispatcher_task and not self._dispatcher_task.done():
            self._dispatcher_task.cancel()
        self._dispatcher_task = None
        self._session_manager = None


def _connect_db():
    import aiosqlite
    return aiosqlite.connect(str(EVENT_QUEUE_DB), timeout=SQLITE_TIMEOUT)


async def _dispatcher_loop(bus: OmniSensoryBus) -> None:
    """
    v8.0 调度器：从 SQLite 取 pending -> 按 session_id 提交到 SessionManager。
    多 session 并行，单 session 串行。
    """
    mgr = bus._session_manager
    if mgr is None:
        return
    while True:
        try:
            row = None
            ev_id = source = intent = metadata_json = None
            for _attempt in range(SQLITE_MAX_RETRIES):
                try:
                    async with _connect_db() as db:
                        cursor = await db.execute(
                            "SELECT id, source, intent, metadata_json FROM omni_input_queue WHERE status='pending' ORDER BY id LIMIT 1"
                        )
                        row = await cursor.fetchone()
                        if not row:
                            await asyncio.sleep(0.5)
                            break
                        ev_id, source, intent, metadata_json = row
                        cur2 = await db.execute(
                            "UPDATE omni_input_queue SET status='processing', processed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                            (ev_id,),
                        )
                        await db.commit()
                        if cur2.rowcount == 0:
                            break
                        break
                except Exception as e:
                    if "locked" in str(e).lower() and _attempt < SQLITE_MAX_RETRIES - 1:
                        await asyncio.sleep(0.2 * (_attempt + 1))
                        continue
                    raise
            if not row or ev_id is None:
                continue

            metadata = json.loads(metadata_json or "{}")
            await mgr.submit(ev_id, source, intent, metadata)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("[OmniSensoryBus] dispatcher 异常: %s", e)
            await asyncio.sleep(1.0)


async def _process_single_task(bus: OmniSensoryBus, task_data: dict[str, Any]) -> None:
    """单任务处理：Agent Loop -> 标记 done -> publish_output。按 session 隔离执行。"""
    import aiosqlite
    from core.agent_loop import run as agent_run

    ev_id = task_data["ev_id"]
    source = task_data["source"]
    intent = task_data["intent"]
    metadata = task_data["metadata"]
    session_id = task_data.get("session_id", "")
    run_id = (metadata or {}).get("run_id", "")

    logger.info("[SessionActor] [RunID:%s] session=%s 处理: %s -> %s", run_id[:8] if run_id else "-", session_id[:12] if session_id else source, source, intent[:50])

    def _on_step(step_type: str, step_content: str) -> None:
        if bus._step_callback:
            bus._step_callback(step_type, step_content, run_id)
        ev_sprite = SensoryOutputEvent(
            source_ref="layer3_broadcast",
            content=step_content,
            action_type=step_type,
            metadata={"step_type": step_type, "session_id": session_id, "run_id": run_id},
        )
        asyncio.create_task(bus.publish_output(ev_sprite))

    def _on_hitl_request(task_id: str, content: str) -> None:
        ev_sprite = SensoryOutputEvent(
            source_ref="layer3_broadcast",
            content=content,
            action_type="HITL_REQUIRED",
            metadata={"step_type": "HITL_REQUIRED", "task_id": task_id, "session_id": session_id, "run_id": run_id},
        )
        asyncio.create_task(bus.publish_output(ev_sprite))

    async def _on_chunk(chunk_text: str) -> None:
        """v8.0 流式神经：逐 token 广播。文本优先：不 await 分发链，避免慢处理器阻塞 LLM 流式回调。"""
        ev = SensoryOutputEvent(
            source_ref="layer3_broadcast",
            content=chunk_text,
            action_type="chunk",
            metadata={"step_type": "chunk", "chunk": chunk_text, "session_id": session_id, "run_id": run_id},
        )

        async def _publish_chunk_safe() -> None:
            try:
                await bus.publish_output(ev)
            except Exception as e:
                logger.exception("[OmniSensoryBus] chunk 输出分发异常: %s", e)

        asyncio.create_task(_publish_chunk_safe())

    try:
        from core.agent_loop import SecurityException
        result = await agent_run(
            intent,
            ast_json=metadata.get("ast_json") or {},
            run_id=run_id,
            on_step=_on_step,
            on_hitl_request=_on_hitl_request,
            on_chunk=_on_chunk,
        )
        if isinstance(result, dict) and result.get("status") == "HITL_REQUIRED":
            content = "[HITL] 需人工授权"
            step_type_final = "HITL_REQUIRED"
        else:
            content = str(result)
            step_type_final = "answer"
    except SecurityException as e:
        content = str(e)
        step_type_final = "rejected"
    except Exception as e:
        content = f"[异常] {e}"
        step_type_final = "error"

    try:
        for _attempt in range(SQLITE_MAX_RETRIES):
            try:
                async with _connect_db() as db:
                    await db.execute("UPDATE omni_input_queue SET status='done' WHERE id=?", (ev_id,))
                    await db.commit()
                break
            except Exception as e:
                if "locked" in str(e).lower() and _attempt < SQLITE_MAX_RETRIES - 1:
                    await asyncio.sleep(0.2 * (_attempt + 1))
                    continue
                logger.warning("[SessionActor] 更新 done 失败: %s", e)
    except Exception:
        pass

    meta_out = dict(metadata or {})
    if run_id:
        meta_out["run_id"] = run_id
    out = SensoryOutputEvent(source_ref=source, content=content, action_type="text", metadata=meta_out)
    await bus.publish_output(out)

    ev_sprite = SensoryOutputEvent(
        source_ref="layer3_broadcast",
        content=content,
        action_type=step_type_final,
        metadata={"step_type": step_type_final, "session_id": session_id, "run_id": run_id},
    )
    await bus.publish_output(ev_sprite)


def get_bus() -> OmniSensoryBus:
    """获取全局单例"""
    return OmniSensoryBus()


# -----------------------------------------------------------------------------
# 适配层：OmniInputEvent / OmniOutputEvent（daemon 等仍使用）
# -----------------------------------------------------------------------------

@dataclass
class OmniInputEvent:
    """输入归一化（兼容 OmniInputEvent，payload=metadata）"""
    source: str
    intent: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class OmniOutputEvent:
    """输出（兼容 daemon，source=source_ref, result=content, payload=metadata）"""
    source: str
    result: str
    step_type: str = "answer"
    payload: dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# 兼容 API：emit_omni_input / subscribe_omni_output（daemon 等使用）
# -----------------------------------------------------------------------------

def set_omni_step_callback(cb: Callable[[str, str, str], None] | None) -> None:
    """设置 CognitiveKernel 步骤打印回调"""
    get_bus().set_step_callback(cb)


_last_user_interaction_time: float = time.time()


def get_last_interaction_time() -> float:
    """获取上次用户交互时间戳，用于主动任务与状态监控"""
    return _last_user_interaction_time


def emit_omni_input(source: str, intent: str, payload: dict[str, Any] | None = None) -> None:
    """
    同步发射归一化输入，持久化到 SQLite (status=pending)。
    v8.0 全链路追踪：若 metadata 无 run_id，自动生成并注入。
    """
    global _last_user_interaction_time
    meta = dict(payload or {})
    if not meta.get("run_id"):
        meta["run_id"] = uuid.uuid4().hex
    ok = _persist_omni_input_sync(source, intent, meta)
    if ok:
        _last_user_interaction_time = time.time()
    else:
        logger.warning("[OmniSensoryBus] 持久化失败，丢弃来自 %s 的输入", source)


def subscribe_omni_output(source: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """订阅输出：handler 接收 OmniOutputEvent（兼容旧签名）"""
    async def _adapter(ev: SensoryOutputEvent) -> None:
        archived = OmniOutputEvent(source=ev.source_ref, result=ev.content, payload=ev.metadata)
        await handler(archived)
    get_bus().subscribe(f"output.{source}", _adapter)


def start_omni_consumer() -> None:
    """启动 brain_worker"""
    get_bus().start_brain_worker()


def stop_omni_consumer() -> None:
    """停止 brain_worker"""
    get_bus().stop_brain_worker()


# -----------------------------------------------------------------------------
# 原有 Workflow 事件总线 (兼容)
# -----------------------------------------------------------------------------

@dataclass
class BusEvent:
    """总线事件"""
    type: str  # audio.input, cron.trigger, ...
    payload: dict[str, Any]
    source_plugin_id: str | None = None


# 事件类型 → 工作流 ID 列表 → handler
_subscriptions: dict[str, dict[str, Callable[..., Coroutine[Any, Any, None]]]] = defaultdict(dict)
_queue: asyncio.Queue[BusEvent] = asyncio.Queue()
_consumer_task: asyncio.Task | None = None


def emit(event_type: str, payload: dict[str, Any], source_plugin_id: str | None = None) -> None:
    """
    同步发射事件（非阻塞入队）
    插件调用示例：emit("audio.input", {"text": "用户说的话"}, "core-vad-audio")
    """
    ev = BusEvent(type=event_type, payload=payload, source_plugin_id=source_plugin_id)
    try:
        _queue.put_nowait(ev)
    except asyncio.QueueFull:
        logger.warning("Event bus queue full, dropping event %s", event_type)


async def emit_async(event_type: str, payload: dict[str, Any], source_plugin_id: str | None = None) -> None:
    """异步发射事件"""
    ev = BusEvent(type=event_type, payload=payload, source_plugin_id=source_plugin_id)
    await _queue.put(ev)


def subscribe(event_type: str, workflow_id: str, handler: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """
    订阅事件类型，当事件到达时调用 handler(workflow_id, event)
    """
    _subscriptions[event_type][workflow_id] = handler
    logger.debug("Subscribed workflow %s to event %s", workflow_id, event_type)


def unsubscribe(event_type: str, workflow_id: str) -> None:
    """取消订阅"""
    _subscriptions[event_type].pop(workflow_id, None)


async def _consume_loop() -> None:
    """消费循环：从队列取事件，分发给订阅者"""
    while True:
        try:
            ev = await _queue.get()
            handlers = _subscriptions.get(ev.type, {})
            for wf_id, handler in list(handlers.items()):
                try:
                    await handler(ev)
                except Exception as e:
                    logger.exception("Event handler error workflow=%s: %s", wf_id, e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Event consume error: %s", e)


def start_consumer() -> None:
    """启动事件消费循环（在 asyncio 事件循环中调用）"""
    global _consumer_task
    if _consumer_task is None or _consumer_task.done():
        _consumer_task = asyncio.create_task(_consume_loop())
        logger.info("Event bus consumer started")


def stop_consumer() -> None:
    """停止消费循环"""
    global _consumer_task
    if _consumer_task and not _consumer_task.done():
        _consumer_task.cancel()
        _consumer_task = None
