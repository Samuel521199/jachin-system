"""
Jachin Nexus Daemon - Layer 2 点火总控

边缘智能体通电启动时的终极 boot 入口，整合：
- 本地密钥配置
- Event Bus 后台消费者
- TelemetryAgent 心跳
- UpdaterAgent 轮询
- Local Ingress API（localhost:9000）

用法:
    python -m core.nexus_daemon
"""

from core.nexus_daemon.daemon import main

__all__ = ["main"]
