"""
Nexus Daemon - Layer 2 点火总控

边缘智能体通电启动时，自动：
1. 读取本地密钥配置（~/.jachin/nexus_config.json 或环境变量）
2. 拉起 Event Bus 后台消费者
3. 启动 TelemetryAgent 心跳（若已配对）
4. 启动 UpdaterAgent 轮询（若已配对）
5. 启动 Local Ingress API（localhost:9000）

未配对时：仅运行 Event Bus + Ingress，具备本地自治能力。
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from core.nexus_daemon.config import DaemonConfig, load_daemon_config

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """配置 daemon 日志"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


async def _run_daemon(config: DaemonConfig) -> None:
    """主运行逻辑"""
    from core.event_bus import start_consumer, stop_consumer, start_omni_consumer, stop_omni_consumer
    from core.event_bus import subscribe_omni_output
    from core.nexus_daemon.ingress import start_ingress_server
    from core.sensory_server import _broadcast_to_ui, start_sensory_server

    # 1. Event Bus 消费者（核心，必须最先启动）
    start_consumer()
    logger.info("[Event Bus] 消费者已启动")

    # 1b. 全息感官总线 + brain_worker（供 Layer 3 聊天、Sensory 广播）
    subscribe_omni_output("layer3_broadcast", _broadcast_to_ui)
    subscribe_omni_output("swarm_broadcast", _broadcast_to_ui)
    start_omni_consumer()
    logger.info("[OmniSensoryBus] brain_worker 已启动")

    # 1c. Sensory WebSocket（ws://localhost:18881/sensory）
    await start_sensory_server()
    logger.info("[Sensory] 全息共振通道已启动 ws://localhost:18881/sensory")

    # 2. Local Ingress API（始终启动，确立边缘中枢地位）
    ingress_runner = await start_ingress_server(
        host=config.ingress_host,
        port=config.ingress_port,
    )

    # 3. 若已配对：Telemetry + Updater
    telemetry_agent = None
    updater_agent = None

    if config.is_paired:
        from core.telemetry import TelemetryAgent
        from core.plugin import get_plugin_runners
        from core.updater.agent import UpdaterAgent

        telemetry_agent = TelemetryAgent(
            nexus_endpoint=config.heartbeat_url,
            instance_id=config.instance_id,
            access_token=config.access_token,
            get_runners=get_plugin_runners,
        )
        telemetry_agent.start(interval_seconds=config.telemetry_interval_sec)
        logger.info("[Telemetry] 已启动，向 Layer 1 汇报心跳")

        updater_agent = UpdaterAgent(
            instance_id=config.instance_id,
            base_url=config.nexus_base_url,
            poll_interval_sec=config.updater_poll_interval_sec,
        )
        updater_agent.start()
        logger.info("[Updater] 已启动，监听部署指令")
    else:
        logger.warning(
            "[Daemon] 未配对：仅运行 Event Bus + Ingress。"
            "设置 NEXUS_INSTANCE_ID、NEXUS_ACCESS_TOKEN 或完成配对流程以启用心跳与轮询。"
        )

    # 4. 保持运行，直到收到退出信号
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        # 优雅退出
        logger.info("[Daemon] 正在关闭...")

        if telemetry_agent:
            telemetry_agent.stop()
        if updater_agent:
            updater_agent.stop()

        stop_omni_consumer()
        stop_consumer()

        if ingress_runner:
            await ingress_runner.cleanup()

        logger.info("[Daemon] 已安全退出")


def main() -> None:
    """入口"""
    _setup_logging()

    config = load_daemon_config()

    logger.info(
        "[Nexus Daemon] 点火启动 | paired=%s | ingress=%s:%d",
        config.is_paired,
        config.ingress_host,
        config.ingress_port,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 信号处理
    stop_event = asyncio.Event()

    def _on_signal(sig: int, _frame: object) -> None:
        logger.info("收到信号 %s，准备退出", sig)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass  # Windows 上部分信号可能不可用

    async def _run_with_stop() -> None:
        task = asyncio.create_task(_run_daemon(config))
        await stop_event.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        loop.run_until_complete(_run_with_stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
