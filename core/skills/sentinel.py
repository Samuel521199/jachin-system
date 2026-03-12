"""
Sentinel 哨兵 Actor - 永不休眠，监控待确认任务并主动升级提醒
使用 PluginManager.list_capabilities('user.reach') 查找通知工具
escalation_policy: Level 1 os-mate (Notify) -> Level 2 voip (Call)
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import ray

from common.schemas.sentinel import SentinelTask, SentinelPriority
from core.config import settings

logger = logging.getLogger(__name__)

_SENTINEL_TEST_INVOKES: List[Dict[str, Any]] = []

# escalation_policy: Level 1 -> Level 2
# Level 1: com.jachin.os-mate.desktop_notify (桌面通知)
# Level 2: com.jachin.voip.voip_call (电话) - 未安装则跳过
ESCALATION_POLICY = [
    {"skill_id": "com.jachin.os-mate", "capability": "desktop_notify"},
    {"skill_id": "com.jachin.voip", "capability": "voip_call"},
]
ESCALATION_TIMEOUTS = {0: 5, 1: 10}  # 分钟


@ray.remote(num_cpus=0.1, num_gpus=0)
class SentinelActor:
    """哨兵 Actor：每 30 秒扫描 pending_tasks，超时未确认则升级"""

    def __init__(self):
        self._pending: Dict[str, SentinelTask] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def add_task(self, task: Dict[str, Any]) -> str:
        t = SentinelTask(**task) if isinstance(task, dict) else task
        with self._lock:
            self._pending[t.task_id] = t
        logger.info(f"Sentinel: added task {t.task_id} priority={t.priority}")
        return t.task_id

    def ack_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._pending:
                return False
            self._pending.pop(task_id)
        logger.info(f"Sentinel: task {task_id} acknowledged")
        return True

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [t.model_dump() for t in self._pending.values()]

    def start_loop(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Sentinel: loop started (30s interval)")

    def stop_loop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=35)
        logger.info("Sentinel: loop stopped")

    def run_scan_once(self) -> None:
        asyncio.run(self._scan_and_escalate())

    def get_test_invokes(self) -> List[Dict[str, Any]]:
        return list(_SENTINEL_TEST_INVOKES)

    def clear_test_invokes(self) -> None:
        _SENTINEL_TEST_INVOKES.clear()

    def _run_loop(self) -> None:
        while self._running:
            self._stop_event.wait(30)
            if not self._running:
                break
            try:
                asyncio.run(self._scan_and_escalate())
            except Exception as e:
                logger.error(f"Sentinel scan error: {e}", exc_info=True)

    async def _scan_and_escalate(self) -> None:
        with self._lock:
            to_process = list(self._pending.values())
        for task in to_process:
            try:
                await self._check_and_escalate(task)
            except Exception as e:
                logger.error(f"Sentinel escalate task {task.task_id} error: {e}", exc_info=True)

    async def _check_and_escalate(self, task: SentinelTask) -> None:
        if task.status == "acked":
            return
        level = task.escalation_level
        timeout_min = ESCALATION_TIMEOUTS.get(level, 5)
        last_at = task.last_notified_at
        if not last_at:
            await self._execute_escalation_step(task)
            return
        try:
            last_dt = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
            if last_dt.tzinfo:
                last_dt = last_dt.replace(tzinfo=None)
        except Exception:
            last_dt = datetime.now() - timedelta(hours=1)
        elapsed_min = (datetime.now() - last_dt).total_seconds() / 60
        if timeout_min > 0 and elapsed_min < timeout_min:
            return
        max_level = self._max_escalation_level(task.priority)
        if level >= max_level:
            return
        next_level = level + 1
        if next_level >= len(ESCALATION_POLICY):
            if task.priority == SentinelPriority.CRITICAL:
                await self._execute_escalation_step(task)
            task.status = "failed"
            with self._lock:
                if task.task_id in self._pending:
                    self._pending[task.task_id] = task
            return
        task.escalation_level = next_level
        task.status = "escalated"
        with self._lock:
            if task.task_id in self._pending:
                self._pending[task.task_id] = task
        await self._execute_escalation_step(task)

    def _max_escalation_level(self, priority: str) -> int:
        if priority in (SentinelPriority.LOW, SentinelPriority.NORMAL):
            return 0
        return 1  # HIGH, CRITICAL 可到 Level 1 (voip)

    async def _execute_escalation_step(self, task: SentinelTask) -> None:
        level = task.escalation_level
        if level >= len(ESCALATION_POLICY):
            return
        step = ESCALATION_POLICY[level]
        skill_id = step["skill_id"]
        capability = step["capability"]
        plugin_mgr = _get_plugin_manager()
        actor = plugin_mgr.get_actor(skill_id)
        if not actor:
            logger.warning(f"Sentinel: skill {skill_id} not loaded, skip")
            return
        if settings.SENTINEL_TEST_MODE == "1":
            _SENTINEL_TEST_INVOKES.append({
                "skill_id": skill_id,
                "capability_name": capability,
                "input_data": self._build_notify_input(task),
            })
            success = True
        else:
            input_data = self._build_notify_input(task)
            try:
                ref = actor.execute.remote(capability, input_data)
                result = ray.get(ref)
                success = result.get("success", False)
            except Exception as e:
                logger.error(f"Sentinel invoke {skill_id}.{capability} error: {e}", exc_info=True)
                success = False
        with self._lock:
            if task.task_id in self._pending:
                t = self._pending[task.task_id]
                t.last_notified_at = datetime.now().isoformat()
                t.status = "notified"
                self._pending[task.task_id] = t
        if success:
            logger.info(f"Sentinel: invoked {skill_id}.{capability} for task {task.task_id}")
        else:
            logger.warning(f"Sentinel: invoke {skill_id}.{capability} failed for task {task.task_id}")

    def _build_notify_input(self, task: SentinelTask) -> Dict[str, Any]:
        return {
            "title": task.context.get("title", "提醒"),
            "message": task.context.get("content", task.context.get("message", "您有一条待确认事项")),
            "task_id": task.task_id,
            "priority": task.priority,
        }


def _get_plugin_manager():
    from core.system.plugin_manager import get_plugin_manager
    return get_plugin_manager()
