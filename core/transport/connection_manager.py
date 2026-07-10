"""
Connection Manager - Jachin Link 连接管理器
管理 Tier 3 设备的连接状态和会话

职责：
- 设备连接注册和注销
- 连接状态跟踪
- 设备认证状态管理
- 心跳超时检测
"""

import asyncio
import logging
import time
from typing import Dict, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class DeviceConnection:
    """设备连接信息"""
    device_id: str
    connected_at: datetime
    last_heartbeat: datetime
    server_id: str
    is_authenticated: bool = False
    metadata: Dict[str, any] = field(default_factory=dict)


class ConnectionManager:
    """
    连接管理器

    负责：
    - 跟踪所有连接的设备
    - 管理设备认证状态
    - 心跳超时检测
    - 连接清理
    """

    def __init__(self, heartbeat_timeout: int = 60):
        """
        初始化连接管理器

        Args:
            heartbeat_timeout: 心跳超时时间（秒），默认 60 秒
        """
        self.heartbeat_timeout = heartbeat_timeout
        self.connections: Dict[str, DeviceConnection] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    def register_device(self, device_id: str, server_id: str, metadata: Dict = None) -> DeviceConnection:
        """
        注册设备连接

        Args:
            device_id: 设备唯一标识
            server_id: 服务器标识
            metadata: 设备元数据

        Returns:
            DeviceConnection 对象
        """
        now = datetime.utcnow()
        connection = DeviceConnection(
            device_id=device_id,
            connected_at=now,
            last_heartbeat=now,
            server_id=server_id,
            is_authenticated=True,
            metadata=metadata or {}
        )

        self.connections[device_id] = connection
        logger.info(f"Device '{device_id}' registered and authenticated")
        return connection

    def update_heartbeat(self, device_id: str) -> bool:
        """
        更新设备心跳时间

        Args:
            device_id: 设备唯一标识

        Returns:
            是否更新成功（设备是否存在）
        """
        if device_id not in self.connections:
            logger.warning(f"Heartbeat from unknown device: {device_id}")
            return False

        self.connections[device_id].last_heartbeat = datetime.utcnow()
        return True

    def unregister_device(self, device_id: str) -> bool:
        """
        注销设备连接

        Args:
            device_id: 设备唯一标识

        Returns:
            是否注销成功
        """
        if device_id in self.connections:
            del self.connections[device_id]
            logger.info(f"Device '{device_id}' unregistered")
            return True
        return False

    def get_connection(self, device_id: str) -> Optional[DeviceConnection]:
        """获取设备连接信息"""
        return self.connections.get(device_id)

    def is_device_connected(self, device_id: str) -> bool:
        """检查设备是否已连接"""
        return device_id in self.connections

    def list_connected_devices(self) -> Set[str]:
        """列出所有已连接的设备 ID"""
        return set(self.connections.keys())

    def check_heartbeat_timeout(self) -> Set[str]:
        """
        检查心跳超时的设备

        Returns:
            超时设备 ID 集合
        """
        now = datetime.utcnow()
        timeout_threshold = timedelta(seconds=self.heartbeat_timeout)
        timeout_devices = set()

        for device_id, connection in list(self.connections.items()):
            if now - connection.last_heartbeat > timeout_threshold:
                timeout_devices.add(device_id)
                logger.warning(f"Device '{device_id}' heartbeat timeout")
                # 自动清理超时连接
                self.unregister_device(device_id)

        return timeout_devices

    async def start_cleanup_task(self):
        """启动清理任务（定期检查心跳超时）"""
        if self._cleanup_task is not None:
            return

        async def cleanup_loop():
            while True:
                await asyncio.sleep(30)  # 每 30 秒检查一次
                self.check_heartbeat_timeout()

        self._cleanup_task = asyncio.create_task(cleanup_loop())
        logger.info("Connection cleanup task started")

    async def stop_cleanup_task(self):
        """停止清理任务"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Connection cleanup task stopped")
