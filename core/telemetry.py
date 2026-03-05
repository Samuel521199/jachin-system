"""
Telemetry Agent - P0-4 端云心跳与状态遥测

遥测雷达守护线程：每隔 N 秒向 Layer 1 指挥部汇报
- 硬件级生命体征（CPU、内存，需 psutil）
- 武器库实时状态（Supervisor 状态机：running / restarting / fatal 等）

用法:
    from core.telemetry import TelemetryAgent
    from core.plugin import get_plugin_runners

    agent = TelemetryAgent(
        nexus_endpoint="https://nexus.jachin/api/v1/instances/heartbeat",
        instance_id="dev-layer2-001",
        access_token="<配对时获得的 token>",
        get_runners=get_plugin_runners,
    )
    agent.start(interval_seconds=30)
"""

import logging
import threading
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

# psutil 可选：未安装时跳过硬件指标
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


def _collect_metrics() -> dict[str, Any]:
    """采集硬件级生命体征"""
    if not HAS_PSUTIL:
        return {"cpu_percent": 0, "ram_used_mb": 0, "ram_total_mb": 0}
    vm = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_mb": round(vm.used / (1024 * 1024), 2),
        "ram_total_mb": round(vm.total / (1024 * 1024), 2),
    }


class TelemetryAgent:
    """
    遥测雷达：向 Layer 1 指挥部定期发射心跳

    :param nexus_endpoint: Layer 1 心跳 API，如 https://nexus.jachin/api/v1/instances/heartbeat
    :param instance_id: 本边缘智能体唯一标识（配对时获得）
    :param access_token: 通信凭证（配对时获得）
    :param get_runners: 获取插件 Runner 注册表的可调用对象，返回 {plugin_id: HeavyProcessRunner}
    """

    def __init__(
        self,
        nexus_endpoint: str,
        instance_id: str,
        access_token: str,
        get_runners: Callable[[], dict[str, Any]] | None = None,
    ):
        self.nexus_endpoint = nexus_endpoint.rstrip("/")
        self.instance_id = instance_id
        self.access_token = access_token
        self.get_runners = get_runners or (lambda: {})

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, interval_seconds: int = 30) -> None:
        """启动心跳雷达"""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._radar_loop,
            args=(interval_seconds,),
            name="TelemetryRadar",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "📡 遥测雷达已上线，每 %s 秒向指挥部汇报一次生命体征。",
            interval_seconds,
        )

    def stop(self) -> None:
        """关闭雷达"""
        self._stop_event.set()
        logger.info("🔇 遥测雷达已关闭。")

    def _radar_loop(self, interval: int) -> None:
        # 首次启动稍等，让插件先飞一会儿
        self._stop_event.wait(2.0)

        while not self._stop_event.is_set():
            self._send_heartbeat()
            self._stop_event.wait(timeout=interval)

    def _send_heartbeat(self) -> None:
        try:
            metrics = _collect_metrics()

            active_plugins: dict[str, str] = {}
            runners = self.get_runners()
            for plugin_id, runner in runners.items():
                if hasattr(runner, "state"):
                    active_plugins[plugin_id] = runner.state.value

            payload = {
                "instance_id": self.instance_id,
                "core_version": "0.8.5",
                "metrics": metrics,
                "active_plugins": active_plugins,
            }

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            with httpx.Client(timeout=5.0) as client:
                response = client.post(
                    self.nexus_endpoint,
                    json=payload,
                    headers=headers,
                )

            if response.status_code == 200:
                logger.debug("💓 [Heartbeat] 状态已同步至指挥部。")
            else:
                logger.warning(
                    "⚠️ [Heartbeat] 同步遭拒 (HTTP %s): %s",
                    response.status_code,
                    response.text[:200],
                )

        except httpx.RequestError as e:
            logger.error("📉 [Heartbeat] 遭遇网络干扰，失去与指挥部的连接: %s", e)
        except Exception as e:
            logger.error("🐛 [Heartbeat] 雷达内部故障: %s", e, exc_info=True)
