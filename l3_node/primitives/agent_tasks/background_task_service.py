"""L3 background task queue and worker service; events flow through l3_event_bus."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BackgroundTaskStderrPulse:
    """后台 ``run_agent`` 执行期间：
    - **stderr**：单行 ``.``（终端仍可见）；
    - **前台 WebSocket**：同逻辑通过 ``event=pulse`` + ``pulse_line`` 推送，供桌面聊天页展示。

    规则（与产品约定一致）：
    - 每 **≥500ms** 或 LLM **流式累计 ≥500 字符** 输出一个 ``.``；
    - **仅一行**：写满 ``JACHIN_BG_TASK_STDERR_PULSE_WIDTH``（默认 120）后回卷，``pulse_line`` 为空串表示行已清空；
    - 任务结束 ``stop()`` 时 stderr 换行；完成/失败由 ``completed``/``failed`` 帧清 UI。

    关闭：``JACHIN_BG_TASK_STDERR_PULSE=0``（stderr 与 WS 均关闭）。
    """

    def __init__(self, task_id: str) -> None:
        self.task_id = str(task_id)
        raw_w = (os.environ.get("JACHIN_BG_TASK_STDERR_PULSE_WIDTH") or "").strip()
        try:
            self._width = max(16, min(500, int(raw_w))) if raw_w else 120
        except ValueError:
            self._width = 120
        self._col = 0
        self._chars_acc = 0
        self._last_emit = time.monotonic()
        self._stop = asyncio.Event()
        self._tick_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def enabled(self) -> bool:
        return os.environ.get("JACHIN_BG_TASK_STDERR_PULSE", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )

    async def start(self) -> None:
        if not self.enabled():
            return
        self._tick_task = asyncio.create_task(
            self._time_tick_loop(), name=f"jachin-bg-stderr-pulse-{self.task_id[:12]}"
        )

    async def _time_tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=0.5)
                return
            except asyncio.TimeoutError:
                await self._emit_if_due_time()

    async def _emit_if_due_time(self) -> None:
        pl: str | None = None
        async with self._lock:
            now = time.monotonic()
            if now - self._last_emit >= 0.5:
                pl = self._emit_dot_nolock()
                self._last_emit = time.monotonic()
        if pl is not None:
            await self._emit_ui_pulse(pl)

    async def note_stream_chars(self, n: int) -> None:
        if not self.enabled() or n <= 0:
            return
        last_pl: str | None = None
        async with self._lock:
            self._chars_acc += n
            while self._chars_acc >= 500:
                self._chars_acc -= 500
                last_pl = self._emit_dot_nolock()
                self._last_emit = time.monotonic()
        if last_pl is not None:
            await self._emit_ui_pulse(last_pl)

    def _emit_dot_nolock(self) -> str:
        try:
            sys.stderr.write(".")
            self._col += 1
            if self._col >= self._width:
                sys.stderr.write("\r")
                self._col = 0
                sys.stderr.flush()
                return ""
            sys.stderr.flush()
            return "." * self._col
        except Exception:
            return "." * max(0, self._col)

    async def _emit_ui_pulse(self, pulse_line: str) -> None:
        if not self.enabled():
            return
        try:
            await _emit("pulse", self.task_id, pulse_line=pulse_line)
        except Exception:
            pass

    async def stop(self) -> None:
        self._stop.set()
        if self._tick_task:
            try:
                await asyncio.wait_for(self._tick_task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                self._tick_task.cancel()
                try:
                    await self._tick_task
                except asyncio.CancelledError:
                    pass
            except Exception:
                pass
            self._tick_task = None
        try:
            sys.stderr.write("\n")
            sys.stderr.flush()
        except Exception:
            pass


# zombie_tasks.json 追加与读取共用锁（同进程内并发；跨进程靠原子 replace）
_zombie_tasks_file_lock = threading.Lock()

_ENGINE: Any = None
_queue: asyncio.Queue | None = None
_runtime_started = False
_registry: dict[str, dict[str, Any]] = {}
_progress_lock: asyncio.Lock | None = None
_worker_tasks: list[asyncio.Task] = []


def _jachin_dir() -> Path:
    return Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))


def _task_dir() -> Path:
    d = _jachin_dir() / "workspace" / ".background_tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _zombie_tasks_path() -> Path:
    return _task_dir() / "zombie_tasks.json"


def load_zombie_tasks_snapshot() -> list[dict[str, Any]]:
    with _zombie_tasks_file_lock:
        p = _zombie_tasks_path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]


def _append_zombie_task_record(entry: dict[str, Any]) -> None:
    """将中断任务元数据追加到 zombie_tasks.json（列表）。
    同 task_id 已存在则先移除再追加，避免重复堆积。
    写盘：临时文件 + os.replace，降低断电时写坏主文件的概率。
    """
    tid = str(entry.get("task_id") or "").strip()
    if not tid:
        return
    with _zombie_tasks_file_lock:
        p = _zombie_tasks_path()
        data: list[Any] = []
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    data = raw
            except Exception as e:
                logger.warning("[BackgroundTasks] zombie_tasks.json 解析失败，将重建列表: %s", e)
                data = []
        data = [x for x in data if not (isinstance(x, dict) and str(x.get("task_id") or "").strip() == tid)]
        data.append(entry)
        tmp = p.with_name(p.name + ".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(p))
        except Exception as e:
            logger.warning("[BackgroundTasks] zombie_tasks.json 写入失败: %s", e)
            try:
                if tmp.is_file():
                    tmp.unlink()
            except Exception:
                pass


def _load_cfg() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "enabled": True,
        "max_concurrent": 3,
        "max_queued": 32,
        "default_max_iterations": 24,
    }
    p = _jachin_dir() / "nexus_config.json"
    if not p.exists():
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        bt = raw.get("background_tasks")
        if isinstance(bt, dict):
            if "enabled" in bt:
                cfg["enabled"] = bool(bt["enabled"])
            if bt.get("max_concurrent") is not None:
                try:
                    cfg["max_concurrent"] = max(1, min(32, int(bt["max_concurrent"])))
                except (TypeError, ValueError):
                    pass
            if bt.get("max_queued") is not None:
                try:
                    cfg["max_queued"] = max(1, min(512, int(bt["max_queued"])))
                except (TypeError, ValueError):
                    pass
            if bt.get("default_max_iterations") is not None:
                try:
                    cfg["default_max_iterations"] = max(1, min(64, int(bt["default_max_iterations"])))
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        logger.debug("[BackgroundTasks] 读取 nexus_config 跳过: %s", e)
    return cfg


def format_background_tasks_prompt_suffix() -> str:
    """供系统提示注入一行后台任务负载；同步、仅读内存，无阻塞 I/O。"""
    if not _runtime_started or _queue is None or _env_disabled():
        return ""
    running = 0
    for rec in _registry.values():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("status") or "").lower() == "running":
            running += 1
    try:
        q_wait = int(_queue.qsize())
    except Exception:
        q_wait = 0
    if running <= 0 and q_wait <= 0:
        return ""
    parts: list[str] = []
    if running > 0:
        parts.append(f"后台 Worker 正在执行约 {running} 个任务")
    if q_wait > 0:
        parts.append(f"内存队列中约 {q_wait} 个待 Worker 领取")
    body = "；".join(parts)
    return (
        f"【系统负载·后台任务（P3，core:submit_background_task）】{body}。"
        "若用户询问系统是否繁忙或响应变慢，可如实说明当前存在后台任务。"
    )


def get_background_queue_metrics() -> dict[str, int]:
    if not _runtime_started or _queue is None or _env_disabled():
        return {"running": 0, "queued": 0}
    running = 0
    for rec in _registry.values():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("status") or "").lower() == "running":
            running += 1
    try:
        q_wait = int(_queue.qsize())
    except Exception:
        q_wait = 0
    return {"running": int(running), "queued": int(q_wait)}


def set_background_task_engine(engine: Any) -> None:
    global _ENGINE
    _ENGINE = engine


def _env_disabled() -> bool:
    return os.environ.get("JACHIN_BACKGROUND_TASKS", "").strip().lower() in ("0", "false", "no", "off")


@dataclass
class BackgroundJob:
    task_id: str
    intent: str
    require_skills: list[str] = field(default_factory=list)
    max_iterations: int = 24
    allowed_skills: Optional[list[str]] = None
    created_at: float = field(default_factory=time.time)
    # 优先级：0=普通，1=较高，2=紧急。Worker 按 FIFO 顺序处理，优先级仅用于插队排序（PriorityQueue 扩展预留）。
    priority: int = 0
    # 子 Agent 委托标识：由 delegate 路径提交的后台任务，用于可观测性归因
    parent_run_id: Optional[str] = None
    # 任务标签：便于 list_recent / 监控面板筛选
    tags: list[str] = field(default_factory=list)
    # 原始请求的 Lark 会话 ID；定时任务触发时用于把结果回推到正确飞书会话
    lark_chat_id: Optional[str] = None


def _persist_record(task_id: str, rec: dict[str, Any]) -> None:
    try:
        fp = _task_dir() / f"{task_id}.json"
        fp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[BackgroundTasks] 落盘失败 task_id=%s: %s", task_id, e)


def _append_tasks_index(rec: dict[str, Any], event: str) -> None:
    try:
        p = _task_dir() / "tasks_index.jsonl"
        payload: dict[str, Any] = {"ts": time.time(), "event": event}
        for k in (
            "task_id",
            "status",
            "intent",
            "queued_at",
            "created_at",
            "started_at",
            "finished_at",
        ):
            if rec.get(k) is not None:
                payload[k] = rec[k]
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("[BackgroundTasks] tasks_index 写入跳过: %s", e)


def _merge_registry(rec: dict[str, Any]) -> None:
    tid = rec.get("task_id")
    if tid:
        _registry[str(tid)] = dict(rec)


async def _emit(ev: str, task_id: str, **extra: Any) -> None:
    payload = {
        "type": "background_task",
        "event": ev,
        "task_id": task_id,
        "ts": time.time(),
        **extra,
    }
    try:
        from l3_node.l3_event_bus import broadcast_background_task_event

        await broadcast_background_task_event(payload)
    except Exception as e:
        logger.debug("[BackgroundTasks] 广播跳过: %s", e)


def _progress_append_sync(line: str) -> None:
    try:
        p = _jachin_dir() / "workspace" / "progress.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n{line}\n")
    except Exception as e:
        logger.debug("[BackgroundTasks] progress.md 追加跳过: %s", e)


def _get_progress_lock() -> asyncio.Lock:
    global _progress_lock
    if _progress_lock is None:
        _progress_lock = asyncio.Lock()
    return _progress_lock


async def _append_progress_line_async(line: str) -> None:
    async with _get_progress_lock():
        await asyncio.to_thread(_progress_append_sync, line)


def reconcile_stale_background_tasks_on_startup() -> int:
    """进程重启后内存队列为空，磁盘上仍为 running/queued 的记录无法被 Worker 捡起。

    标记为 interrupted 并写回，避免「僵尸任务」永久卡住。
    同时将任务摘要追加到 zombie_tasks.json，供 core:check_interrupted_tasks 晨会提示。
    """
    n = 0
    base_msg = (
        "节点重启或进程崩溃：内存队列已丢失，该任务未继续执行。"
        "请用新的 intent 重新调用 core:submit_background_task。"
    )
    try:
        for fp in sorted(_task_dir().glob("*.json")):
            if fp.name == "zombie_tasks.json":
                continue
            try:
                rec = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            st = rec.get("status")
            if st not in ("running", "queued"):
                continue
            tid = str(rec.get("task_id") or fp.stem)
            rec["status"] = "interrupted"
            rec["finished_at"] = time.time()
            prev = rec.get("error")
            rec["error"] = (f"{prev}；" if prev else "") + base_msg
            _merge_registry(rec)
            _persist_record(tid, rec)
            try:
                _append_zombie_task_record(
                    {
                        "task_id": tid,
                        "task_prompt": str(rec.get("intent") or ""),
                        "interrupted_at": float(rec["finished_at"]),
                        "previous_status": str(st),
                        "require_skills": list(rec.get("require_skills") or []),
                        "max_iterations": rec.get("max_iterations"),
                    }
                )
            except Exception as ze:
                logger.debug("[BackgroundTasks] zombie 记录追加跳过: %s", ze)
            n += 1
    except Exception as e:
        logger.warning("[BackgroundTasks] 启动对账失败: %s", e)
    if n:
        logger.warning("[BackgroundTasks] 启动对账：已将 %d 条 running/queued 标为 interrupted", n)
    return n


async def _run_job(job: BackgroundJob) -> None:
    tid = job.task_id
    rec = {
        "task_id": tid,
        "status": "running",
        "intent": job.intent,
        "require_skills": job.require_skills,
        "created_at": job.created_at,
        "started_at": time.time(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    _merge_registry(rec)
    _persist_record(tid, rec)
    _append_tasks_index(rec, "started")
    await _emit("started", tid, intent_preview=(job.intent[:120] + "…") if len(job.intent) > 120 else job.intent)

    engine = _ENGINE
    if engine is None:
        try:
            from l3_node.agent_ref import engine_ref

            engine = engine_ref.get("engine")
        except Exception:
            engine = None
    if engine is None:
        err = "L3 引擎未就绪，无法执行后台任务"
        rec.update({"status": "failed", "finished_at": time.time(), "error": err})
        _merge_registry(rec)
        _persist_record(tid, rec)
        _append_tasks_index(rec, "failed")
        await _emit("failed", tid, message=err)
        await _append_progress_line_async(f"- [后台任务 {tid}] 失败：{err}")
        return

    intent = job.intent
    if job.require_skills:
        intent = f"[后台任务·领域偏好: {', '.join(job.require_skills)}]\n\n{intent}"

    _pulse = BackgroundTaskStderrPulse(tid)
    pulse: BackgroundTaskStderrPulse | None = _pulse if _pulse.enabled() else None
    if pulse:
        await pulse.start()

    async def _bg_on_chunk(chunk: str) -> None:
        if pulse:
            await pulse.note_stream_chars(len(chunk or ""))

    try:
        # /pmo 指令：走完整 PMO Skill 执行路径，而不是普通 run_agent
        _is_pmo = False
        try:
            from l3_node.deferred_task_scheduler import _is_pmo_command
            _is_pmo = _is_pmo_command(intent)
        except Exception:
            pass

        if _is_pmo:
            logger.info("[BackgroundTasks] 检测到 /pmo 指令，走 PMO Skill 执行路径 task_id=%s", tid)
            try:
                from l3_node.deferred_task_scheduler import _run_pmo_deferred
                answer = await _run_pmo_deferred(intent, engine, job.lark_chat_id)
            except Exception as _pmo_ex:
                logger.warning("[BackgroundTasks] PMO deferred 路径失败，回退 run_agent: %s", _pmo_ex)
                _is_pmo = False

        if not _is_pmo:
            from l3_node.agent_core import run_agent

            _implicit: dict[str, Any] = {
                "channel": "background_task",
                "task_id": tid,
            }
            if job.lark_chat_id:
                _implicit["lark_chat_id"] = job.lark_chat_id
            answer = await run_agent(
                intent,
                engine,
                max_iterations=job.max_iterations,
                implicit_attribution=_implicit,
                _allowed_skills_override=job.allowed_skills,
                on_chunk=_bg_on_chunk if pulse else None,
            )
        rec.update(
            {
                "status": "completed",
                "finished_at": time.time(),
                "result": (answer or "")[:200_000],
            }
        )
        _merge_registry(rec)
        _persist_record(tid, rec)
        _append_tasks_index(rec, "completed")
        await _emit("completed", tid, result_preview=(answer or "")[:500])
        await _append_progress_line_async(f"- [后台任务 {tid}] 已完成。")
        # 定时任务来源：由代码强制把结果推送到原始飞书会话（不依赖 LLM 自行填写 receive_id）
        if job.lark_chat_id and answer and not str(answer).startswith("error:"):
            try:
                from l3_node.deferred_task_scheduler import _send_deferred_result_to_lark
                await asyncio.to_thread(
                    _send_deferred_result_to_lark,
                    str(answer)[:4000],
                    job.lark_chat_id,
                )
            except Exception as _lark_ex:
                logger.warning("[BackgroundTasks] 定时任务 Lark 推送异常 task_id=%s: %s", tid, _lark_ex)
    except asyncio.CancelledError:
        rec.update({"status": "cancelled", "finished_at": time.time(), "error": "cancelled"})
        _merge_registry(rec)
        _persist_record(tid, rec)
        _append_tasks_index(rec, "cancelled")
        await _emit("cancelled", tid)
        raise
    except Exception as e:
        logger.exception("[BackgroundTasks] 任务执行异常 task_id=%s", tid)
        rec.update({"status": "failed", "finished_at": time.time(), "error": str(e)})
        _merge_registry(rec)
        _persist_record(tid, rec)
        _append_tasks_index(rec, "failed")
        await _emit("failed", tid, message=str(e))
        await _append_progress_line_async(f"- [后台任务 {tid}] 失败：{e}")
    finally:
        if pulse:
            await pulse.stop()


def _job_from_sqlite_payload(data: dict[str, Any]) -> BackgroundJob:
    return BackgroundJob(
        task_id=str(data["task_id"]),
        intent=str(data.get("intent") or ""),
        require_skills=list(data.get("require_skills") or []),
        max_iterations=int(data.get("max_iterations") or 24),
        allowed_skills=data.get("allowed_skills"),
        created_at=float(data.get("created_at") or time.time()),
    )


def _recover_sqlite_pending_queue() -> None:
    global _queue
    if _queue is None:
        return
    try:
        from l3_node.primitives.agent_tasks.background_task_sqlite import delete_pending, list_pending_rows
    except ImportError:
        return
    n = 0
    for tid, raw in list_pending_rows():
        try:
            data = json.loads(raw)
        except Exception:
            delete_pending(tid)
            continue
        terminal = ("completed", "failed", "interrupted", "cancelled")
        rec = _registry.get(str(tid))
        if rec and str(rec.get("status") or "").lower() in terminal:
            delete_pending(tid)
            continue
        try:
            job = _job_from_sqlite_payload(data)
        except Exception:
            delete_pending(tid)
            continue
        try:
            _queue.put_nowait(job)
            n += 1
        except asyncio.QueueFull:
            logger.warning("[BackgroundTasks] SQLite 恢复时内存队列已满，保留余下行待下次启动")
            break
    if n:
        logger.info("[BackgroundTasks] 已从 SQLite 恢复 %d 条待处理任务", n)


async def _worker_loop(worker_id: int) -> None:
    assert _queue is not None
    name = f"bg-worker-{worker_id}"
    logger.info("[BackgroundTasks] worker %s 启动", name)
    while True:
        job = await _queue.get()
        try:
            try:
                from l3_node.primitives.agent_tasks.background_task_sqlite import delete_pending

                delete_pending(job.task_id)
            except Exception:
                pass
            await _run_job(job)
        except asyncio.CancelledError:
            logger.info("[BackgroundTasks] worker %s 取消", name)
            break
        except Exception:
            logger.exception("[BackgroundTasks] worker %s 未捕获异常", name)
        finally:
            _queue.task_done()


async def start_background_task_runtime(engine: Any) -> None:
    global _runtime_started, _queue, _ENGINE
    set_background_task_engine(engine)
    cfg = _load_cfg()
    if not cfg.get("enabled", True) or _env_disabled():
        logger.info("[BackgroundTasks] 已禁用（配置或 JACHIN_BACKGROUND_TASKS）")
        return
    if _runtime_started:
        return
    _runtime_started = True
    _queue = asyncio.Queue(maxsize=int(cfg["max_queued"]))
    n = int(cfg["max_concurrent"])
    global _worker_tasks
    _worker_tasks.clear()
    for i in range(n):
        t = asyncio.create_task(_worker_loop(i), name=f"jachin-bg-task-worker-{i}")
        _worker_tasks.append(t)
    # 重启后恢复 registry，便于 list_recent / check
    try:
        for fp in _task_dir().glob("*.json"):
            try:
                rec = json.loads(fp.read_text(encoding="utf-8"))
                tid = rec.get("task_id")
                if tid:
                    _registry[str(tid)] = rec
            except Exception:
                pass
    except Exception as e:
        logger.debug("[BackgroundTasks] 加载历史任务记录跳过: %s", e)
    reconcile_stale_background_tasks_on_startup()
    _recover_sqlite_pending_queue()
    _ensure_background_shutdown_hook()
    zombies = load_zombie_tasks_snapshot()
    if zombies:
        parts: list[str] = []
        for z in zombies[:8]:
            tid = str(z.get("task_id") or "?")[:32]
            tip = str(z.get("task_prompt") or "")[:100]
            parts.append(f"{tid} → {tip!r}")
        logger.warning(
            "[BackgroundTasks] 断电/崩溃遗留未完成后台任务：共 %d 条（见 ~/.jachin/workspace/.background_tasks/zombie_tasks.json）。"
            " 摘要：%s%s",
            len(zombies),
            " | ".join(parts),
            " …" if len(zombies) > 8 else "",
        )
        logger.warning(
            "[BackgroundTasks] 请在新会话中由助手调用 core:check_interrupted_tasks 向统帅确认是否用 core:submit_background_task 重投；"
            "已连接且订阅 background_task 的客户端将收到 zombie_tasks_pending 事件。"
        )
        try:
            from l3_node.l3_event_bus import broadcast_background_task_event

            await broadcast_background_task_event(
                {
                    "type": "background_task",
                    "event": "zombie_tasks_pending",
                    "count": len(zombies),
                    "tasks": [
                        {
                            "task_id": z.get("task_id"),
                            "task_prompt": str(z.get("task_prompt") or "")[:800],
                            "previous_status": z.get("previous_status"),
                        }
                        for z in zombies[:40]
                    ],
                }
            )
        except Exception as e:
            logger.debug("[BackgroundTasks] zombie 启动事件广播跳过: %s", e)
    logger.info(
        "[BackgroundTasks] 已启动 workers=%d 队列容量=%d default_max_iterations=%s",
        n,
        int(cfg["max_queued"]),
        cfg["default_max_iterations"],
    )


def _norm_tool_id_alnum(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _only_util_get_weather_lite_skills(skills: list[str]) -> bool:
    if not skills:
        return False
    want = _norm_tool_id_alnum("util:get_weather_lite")
    for s in skills:
        if _norm_tool_id_alnum(s) != want:
            return False
    return True


def submit_background_task_sync(inp: str, *, allowed_skills: Optional[list[str]] = None) -> str:
    cfg = _load_cfg()
    if not cfg.get("enabled", True) or _env_disabled():
        return json.dumps(
            {
                "status": "rejected",
                "reason": "disabled",
                "message": "后台任务队列已禁用（nexus_config.background_tasks 或 JACHIN_BACKGROUND_TASKS）。",
            },
            ensure_ascii=False,
        )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return json.dumps(
            {
                "status": "error",
                "reason": "no_event_loop",
                "message": "无运行中的 asyncio 循环，无法投递后台任务。",
            },
            ensure_ascii=False,
        )
    if _queue is None or not _runtime_started:
        return json.dumps(
            {
                "status": "error",
                "reason": "runtime_not_ready",
                "message": "后台任务运行时未初始化，请确保 L3 启动流程已调用 start_background_task_runtime。",
            },
            ensure_ascii=False,
        )

    obj: dict[str, Any] = {}
    raw = (inp or "").strip()
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                obj = parsed
        except json.JSONDecodeError:
            pass
    intent = str(obj.get("intent") or raw or "").strip()
    if not intent:
        return json.dumps(
            {
                "status": "error",
                "reason": "invalid_input",
                "message": "需要 intent 或 JSON：{\"intent\":\"任务描述\", \"require_skills\":[], \"max_iterations\":24}",
            },
            ensure_ascii=False,
        )

    rs = obj.get("require_skills")
    require_skills: list[str] = []
    if isinstance(rs, list):
        require_skills = [str(x).strip() for x in rs if str(x).strip()]

    # 短时实况天气不应走后台：避免用户看到「已入队」与前台即时 Verification evidence 矛盾
    if _only_util_get_weather_lite_skills(require_skills):
        return json.dumps(
            {
                "status": "rejected",
                "reason": "weather_must_foreground",
                "message": (
                    "util:get_weather_lite 为短时网络查询，应在当前对话中前台直接调用，"
                    "勿使用 core:submit_background_task。请生成 WorkOrder: util:get_weather_lite，"
                    'tool input: {"city":"城市名"} 或 {"location":"地区"}。'
                ),
            },
            ensure_ascii=False,
        )

    max_it = cfg["default_max_iterations"]
    if obj.get("max_iterations") is not None:
        try:
            max_it = max(1, min(64, int(obj["max_iterations"])))
        except (TypeError, ValueError):
            pass

    # 优先级（0=普通，1=较高，2=紧急）
    priority = 0
    if obj.get("priority") is not None:
        try:
            priority = max(0, min(2, int(obj["priority"])))
        except (TypeError, ValueError):
            pass

    # 任务标签（便于监控面板筛选，如 ["delegate", "hr_recruitment"]）
    tags: list[str] = []
    raw_tags = obj.get("tags")
    if isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    elif isinstance(raw_tags, str) and raw_tags.strip():
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

    # 父任务 run_id（由 delegate 路径提交时注入，用于可观测性归因）
    parent_run_id: Optional[str] = obj.get("parent_run_id") or None

    # 原始 Lark 会话 ID（由 deferred_task_scheduler 触发时注入，确保结果回推到正确会话）
    lark_chat_id_from_payload: Optional[str] = (
        str(obj.get("lark_chat_id") or "").strip() or None
    )

    task_id = "T-" + uuid.uuid4().hex[:12]
    job = BackgroundJob(
        task_id=task_id,
        intent=intent,
        require_skills=require_skills,
        max_iterations=max_it,
        allowed_skills=allowed_skills,
        priority=priority,
        parent_run_id=parent_run_id,
        tags=tags,
        lark_chat_id=lark_chat_id_from_payload,
    )

    rec = {
        "task_id": task_id,
        "status": "queued",
        "intent": intent,
        "require_skills": require_skills,
        "max_iterations": max_it,
        "priority": priority,
        "tags": tags,
        "parent_run_id": parent_run_id,
        "created_at": job.created_at,
        "queued_at": time.time(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    _merge_registry(rec)
    _persist_record(task_id, rec)
    _append_tasks_index(rec, "queued")

    try:
        from l3_node.primitives.agent_tasks.background_task_sqlite import insert_pending, job_to_payload

        insert_pending(task_id, job_to_payload(job))
    except Exception as e:
        logger.debug("[BackgroundTasks] SQLite 入队记录跳过: %s", e)

    try:
        _queue.put_nowait(job)
    except asyncio.QueueFull:
        try:
            from l3_node.primitives.agent_tasks.background_task_sqlite import delete_pending

            delete_pending(task_id)
        except Exception:
            pass
        return json.dumps(
            {
                "status": "rejected",
                "reason": "resource_exhausted",
                "message": (
                    f"本地后台任务等待队列已满（上限 {cfg['max_queued']}）。"
                    "请让用户稍后再试、缩小单次任务规模，或待已有任务完成后再提交。"
                ),
                "max_queued": cfg["max_queued"],
            },
            ensure_ascii=False,
        )
    loop.create_task(_emit("queued", task_id, queue_hint="任务已进入队列，由后台 Worker 执行"))

    return json.dumps(
        {
            "status": "queued",
            "task_id": task_id,
            "message": "任务已排队，前台可继续对话。使用 core:check_background_task 查询进度。",
            "max_concurrent": cfg["max_concurrent"],
        },
        ensure_ascii=False,
    )


def check_background_task_status_sync(inp: str) -> str:
    raw = (inp or "").strip()
    task_id = ""
    list_recent = False
    if raw.startswith("{"):
        try:
            o = json.loads(raw)
            if isinstance(o, dict):
                task_id = str(o.get("task_id") or "").strip()
                list_recent = bool(o.get("list_recent") or o.get("list"))
        except json.JSONDecodeError:
            pass
    elif raw:
        task_id = raw

    if list_recent or not task_id:
        items = sorted(_registry.values(), key=lambda x: float(x.get("created_at") or 0), reverse=True)[:20]
        return json.dumps(
            {"status": "ok", "recent_tasks": [ {k: v for k, v in i.items() if k != "result"} for i in items ]},
            ensure_ascii=False,
        )

    rec = _registry.get(task_id)
    if not rec:
        fp = _task_dir() / f"{task_id}.json"
        if fp.exists():
            try:
                rec = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                rec = None
    if not rec:
        return json.dumps(
            {"status": "error", "reason": "not_found", "message": f"未找到任务 {task_id}"},
            ensure_ascii=False,
        )
    # 大结果截断给模型
    out = dict(rec)
    res = out.get("result")
    if isinstance(res, str) and len(res) > 12_000:
        out["result"] = res[:12_000] + "\n…(truncated，完整结果在磁盘 JSON)"
    return json.dumps({"status": "ok", "task": out}, ensure_ascii=False)


def check_interrupted_tasks_sync(inp: str) -> str:
    """读取 zombie_tasks.json 中崩溃或断电时未跑完的后台任务摘要。

    tool input 可选 JSON `{"consume": true}`，成功读取后清空列表。
    """
    raw = (inp or "").strip()
    consume = False
    if raw.startswith("{"):
        try:
            o = json.loads(raw)
            if isinstance(o, dict):
                consume = bool(o.get("consume") or o.get("clear") or o.get("acknowledge"))
        except json.JSONDecodeError:
            pass
    elif raw.lower() in ("1", "true", "yes", "consume", "clear"):
        consume = True

    with _zombie_tasks_file_lock:
        p = _zombie_tasks_path()
        if not p.exists():
            return json.dumps(
                {"ok": True, "tasks": [], "count": 0, "consumed": False},
                ensure_ascii=False,
            )
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return json.dumps(
                {
                    "ok": False,
                    "error": f"zombie_tasks.json 无法解析: {e}",
                    "tasks": [],
                    "count": 0,
                },
                ensure_ascii=False,
            )
        if not isinstance(data, list):
            data = []
        tasks = [x for x in data if isinstance(x, dict)]
        if consume:
            tmp = p.with_name(p.name + ".tmp")
            try:
                tmp.write_text(json.dumps([], ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(str(tmp), str(p))
            except Exception as e:
                return json.dumps(
                    {
                        "ok": False,
                        "error": str(e),
                        "tasks": tasks,
                        "count": len(tasks),
                        "consumed": False,
                    },
                    ensure_ascii=False,
                )
        return json.dumps(
            {
                "ok": True,
                "tasks": tasks,
                "count": len(tasks),
                "consumed": bool(consume),
            },
            ensure_ascii=False,
        )


_shutdown_hook_registered = False


async def flush_background_tasks_to_persistent_queue() -> int:
    """Flush in-memory queued tasks back to the persistent pending queue."""
    global _queue
    if _queue is None:
        return 0
    n = 0
    while True:
        try:
            job = _queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        try:
            from l3_node.primitives.agent_tasks.background_task_sqlite import insert_pending, job_to_payload

            insert_pending(job.task_id, job_to_payload(job))
            n += 1
        except Exception as e:
            logger.warning("[BackgroundTasks] flush 灌回 SQLite 失败 task_id=%s: %s", getattr(job, "task_id", "?"), e)
    if n:
        logger.info("[BackgroundTasks] 已将 %d 条内存队列任务写回 SQLite pending", n)
    return n


async def graceful_shutdown_background_tasks(*, timeout_sec: float = 4.0) -> None:
    global _worker_tasks
    try:
        await flush_background_tasks_to_persistent_queue()
    except Exception as e:
        logger.warning("[BackgroundTasks] flush 队列失败: %s", e)
    base_msg = (
        "优雅停机：进程关闭，该任务未跑完。"
        "请必要时重新 core:submit_background_task。"
    )
    try:
        for rec in list(_registry.values()):
            if str(rec.get("status") or "") != "running":
                continue
            tid = str(rec.get("task_id") or "").strip()
            if not tid:
                continue
            nr = dict(rec)
            nr["status"] = "interrupted"
            nr["finished_at"] = time.time()
            prev = nr.get("error")
            nr["error"] = (f"{prev}；" if prev else "") + base_msg
            _merge_registry(nr)
            _persist_record(tid, nr)
            try:
                _append_zombie_task_record(
                    {
                        "task_id": tid,
                        "task_prompt": str(nr.get("intent") or ""),
                        "interrupted_at": float(nr["finished_at"]),
                        "previous_status": "running",
                        "require_skills": list(nr.get("require_skills") or []),
                        "max_iterations": nr.get("max_iterations"),
                        "reason": "graceful_shutdown",
                    }
                )
            except Exception as ze:
                logger.debug("[BackgroundTasks] 停机 zombie 记录追加跳过: %s", ze)
    except Exception as e:
        logger.warning("[BackgroundTasks] 停机标记 running 失败: %s", e)

    tasks = [t for t in _worker_tasks if t is not None and not t.done()]
    for t in tasks:
        t.cancel()
    if tasks:
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning("[BackgroundTasks] Worker 取消等待超时 %.1fs", timeout_sec)
    _worker_tasks.clear()
    logger.info("[BackgroundTasks] 优雅停机完成")


def _ensure_background_shutdown_hook() -> None:
    global _shutdown_hook_registered
    if _shutdown_hook_registered:
        return
    _shutdown_hook_registered = True
    try:
        from l3_node.graceful_shutdown import register_shutdown_hook

        async def _hook() -> None:
            await graceful_shutdown_background_tasks(timeout_sec=4.0)

        register_shutdown_hook(_hook)
    except Exception as e:
        logger.debug("[BackgroundTasks] 注册停机钩子跳过: %s", e)
