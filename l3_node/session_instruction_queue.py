"""
SessionInstructionQueue — 全量队列化 + 真·双轨并行（AU）

解决当前「飞书第二条仅排队不并行」的问题：
每个会话（session_key）维护一个独立的异步指令队列 + worker 协程，
实现真正的多指令并发执行（可选），同时支持降级为「有序队列」模式。

两种工作模式（由环境变量控制）：
  SERIAL  — 有序串行（默认，与当前行为一致，保持上下文连贯性）
  PARALLEL — 真·双轨并行：新指令立即在独立上下文中并发执行，
             结果分别推送到各自的 reply_channel；
             用 JACHIN_SIQ_MAX_PARALLEL 限制最大并发数（默认 2）

架构要点：
  - 每个 session_key 一个 SIQSession（含 asyncio.Queue + worker task）
  - worker 从 queue 中取指令，调用 execute_fn（通常是 run_agent）
  - PARALLEL 模式：直接 create_task，不入队列；超过上限时降级串行
  - 指令超时（JACHIN_SIQ_INSTRUCTION_TIMEOUT_SEC）保护，防止单条指令占满 worker

环境变量
--------
JACHIN_SIQ_ENABLE=1                  开启 SIQ（默认关；开启后 dispatcher.py 可接入）
JACHIN_SIQ_MODE=SERIAL|PARALLEL      队列模式（默认 SERIAL）
JACHIN_SIQ_MAX_PARALLEL=2            PARALLEL 模式最大并发数（默认 2）
JACHIN_SIQ_INSTRUCTION_TIMEOUT_SEC=300  单条指令执行超时秒（默认 300s）
JACHIN_SIQ_MAX_QUEUE_DEPTH=10        每会话队列最大深度（超出则拒绝，默认 10）
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

SIQMode = Literal["SERIAL", "PARALLEL"]


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def siq_enabled() -> bool:
    return (os.environ.get("JACHIN_SIQ_ENABLE") or "").strip().lower() in (
        "1", "true", "yes"
    )


def siq_mode() -> SIQMode:
    raw = (os.environ.get("JACHIN_SIQ_MODE") or "SERIAL").strip().upper()
    return "PARALLEL" if raw == "PARALLEL" else "SERIAL"


def _max_parallel() -> int:
    raw = (os.environ.get("JACHIN_SIQ_MAX_PARALLEL") or "2").strip()
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return 2


def _instruction_timeout() -> float:
    raw = (os.environ.get("JACHIN_SIQ_INSTRUCTION_TIMEOUT_SEC") or "300").strip()
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 300.0


def _max_queue_depth() -> int:
    raw = (os.environ.get("JACHIN_SIQ_MAX_QUEUE_DEPTH") or "10").strip()
    try:
        return max(1, min(50, int(raw)))
    except ValueError:
        return 10


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class SIQInstruction:
    instruction_id: str
    session_key: str
    intent: str
    metadata: dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)
    reply_fn: Callable[[str], Awaitable[None]] | None = None  # 结果回传函数


@dataclass
class SIQResult:
    instruction_id: str
    session_key: str
    ok: bool
    result: str
    elapsed_sec: float
    error: str = ""


# ---------------------------------------------------------------------------
# 会话级队列 + worker
# ---------------------------------------------------------------------------

class SIQSession:
    """
    单会话（session_key）的指令队列 + worker 协程。
    SERIAL 模式：指令入队，worker 逐条处理。
    PARALLEL 模式：新指令直接 create_task（并发），超限时降级串行。
    """

    def __init__(self, session_key: str) -> None:
        self.session_key = session_key
        self._queue: asyncio.Queue[SIQInstruction] = asyncio.Queue(maxsize=_max_queue_depth())
        self._worker_task: asyncio.Task | None = None
        self._active_parallel: int = 0
        self._total_enqueued: int = 0
        self._total_completed: int = 0
        self._created_at: float = time.time()

    def start_worker(self, execute_fn: Callable) -> None:
        """启动 worker 协程（幂等，已运行则跳过）。"""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._worker_task = asyncio.get_event_loop().create_task(
            self._worker_loop(execute_fn),
            name=f"siq_worker_{self.session_key[:16]}",
        )

    async def enqueue(
        self,
        instruction: SIQInstruction,
        execute_fn: Callable,
    ) -> Literal["queued", "parallel", "rejected"]:
        """
        提交指令。
        PARALLEL 模式下若未达并发上限：直接 create_task（并行执行）。
        否则：尝试入队，队满则拒绝。
        """
        mode = siq_mode()

        if mode == "PARALLEL" and self._active_parallel < _max_parallel():
            # 真·并行：独立 task
            self._active_parallel += 1
            self._total_enqueued += 1
            asyncio.get_event_loop().create_task(
                self._run_parallel(instruction, execute_fn),
                name=f"siq_parallel_{instruction.instruction_id[:8]}",
            )
            logger.info(
                "[SIQ] PARALLEL task started session=%s inst=%s active=%d",
                self.session_key[:16], instruction.instruction_id[:8], self._active_parallel,
            )
            return "parallel"

        # 有序入队
        try:
            self._queue.put_nowait(instruction)
            self._total_enqueued += 1
            self.start_worker(execute_fn)
            logger.info(
                "[SIQ] queued inst=%s depth=%d session=%s",
                instruction.instruction_id[:8], self._queue.qsize(), self.session_key[:16],
            )
            return "queued"
        except asyncio.QueueFull:
            logger.warning("[SIQ] queue full session=%s, instruction rejected", self.session_key[:16])
            return "rejected"

    async def _run_parallel(self, instruction: SIQInstruction, execute_fn: Callable) -> None:
        """独立并行任务的执行包装。"""
        t0 = time.time()
        try:
            result = await asyncio.wait_for(
                execute_fn(instruction),
                timeout=_instruction_timeout(),
            )
            self._total_completed += 1
            if instruction.reply_fn:
                try:
                    await instruction.reply_fn(str(result or ""))
                except Exception as re:
                    logger.debug("[SIQ] reply_fn failed: %s", re)
        except asyncio.TimeoutError:
            logger.warning(
                "[SIQ] parallel instruction timed out after %.0fs session=%s",
                _instruction_timeout(), self.session_key[:16],
            )
        except Exception as e:
            logger.warning("[SIQ] parallel instruction failed: %s", e)
        finally:
            self._active_parallel = max(0, self._active_parallel - 1)
            elapsed = time.time() - t0
            logger.debug(
                "[SIQ] parallel task done inst=%s elapsed=%.1fs",
                instruction.instruction_id[:8], elapsed,
            )

    async def _worker_loop(self, execute_fn: Callable) -> None:
        """有序串行 worker 主循环。"""
        logger.debug("[SIQ] worker started session=%s", self.session_key[:16])
        while True:
            try:
                instruction: SIQInstruction = await asyncio.wait_for(
                    self._queue.get(), timeout=120.0
                )
            except asyncio.TimeoutError:
                # 超时无新指令，退出 worker（下次 enqueue 时重启）
                logger.debug("[SIQ] worker idle timeout, exiting session=%s", self.session_key[:16])
                return
            t0 = time.time()
            try:
                result = await asyncio.wait_for(
                    execute_fn(instruction),
                    timeout=_instruction_timeout(),
                )
                self._total_completed += 1
                if instruction.reply_fn:
                    try:
                        await instruction.reply_fn(str(result or ""))
                    except Exception as re:
                        logger.debug("[SIQ] reply_fn failed: %s", re)
            except asyncio.TimeoutError:
                logger.warning(
                    "[SIQ] serial instruction timed out after %.0fs",
                    _instruction_timeout(),
                )
            except asyncio.CancelledError:
                logger.info("[SIQ] worker cancelled session=%s", self.session_key[:16])
                return
            except Exception as e:
                logger.warning("[SIQ] serial instruction error: %s", e)
            finally:
                elapsed = time.time() - t0
                logger.debug(
                    "[SIQ] serial done inst=%s elapsed=%.1fs",
                    instruction.instruction_id[:8], elapsed,
                )
                self._queue.task_done()

    def stats(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "mode": siq_mode(),
            "queue_depth": self._queue.qsize(),
            "active_parallel": self._active_parallel,
            "total_enqueued": self._total_enqueued,
            "total_completed": self._total_completed,
            "uptime_sec": round(time.time() - self._created_at, 1),
        }


# ---------------------------------------------------------------------------
# 全局会话注册表
# ---------------------------------------------------------------------------

_sessions: weakref.WeakValueDictionary[str, SIQSession] = weakref.WeakValueDictionary()
_sessions_lock = asyncio.Lock()


async def get_or_create_session(session_key: str) -> SIQSession:
    """获取或创建会话级 SIQSession（弱引用持有，GC 自动回收空闲会话）。"""
    sess = _sessions.get(session_key)
    if sess is None:
        sess = SIQSession(session_key)
        _sessions[session_key] = sess
    return sess


async def submit_instruction(
    session_key: str,
    intent: str,
    execute_fn: Callable,
    *,
    metadata: dict[str, Any] | None = None,
    reply_fn: Callable[[str], Awaitable[None]] | None = None,
) -> Literal["queued", "parallel", "rejected", "disabled"]:
    """
    提交一条用户指令到指定会话的 SIQ。
    execute_fn(instruction: SIQInstruction) -> str  异步执行函数。
    返回提交状态。
    """
    if not siq_enabled():
        return "disabled"
    import uuid
    instruction = SIQInstruction(
        instruction_id=str(uuid.uuid4()),
        session_key=session_key,
        intent=intent,
        metadata=metadata or {},
        reply_fn=reply_fn,
    )
    session = await get_or_create_session(session_key)
    return await session.enqueue(instruction, execute_fn)


def get_all_session_stats() -> list[dict[str, Any]]:
    """获取所有活跃会话的统计信息（诊断用）。"""
    return [sess.stats() for sess in list(_sessions.values())]
