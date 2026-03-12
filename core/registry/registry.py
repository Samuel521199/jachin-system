"""
Device Registry - 设备注册表

严格遵循 .cursor/rules/010-protocol-registry.mdc 规则：
- Write: DeviceRegistry 监听 system/announce Topic，将设备信息存入 Dapr State Store (Redis)
- Read: Agent 在规划任务时，必须先调用 registry.get_tools() 获取当前活跃设备列表
- 禁止硬编码设备 IP
- 禁止假设某个设备一定在线（必须处理 Device Offline 异常）
"""

import json
import logging
from typing import List, Optional, Dict, Any
import time

from .protocol import DeviceAnnounce, DeviceCapability
from .dapr import StateStore, PubSub

logger = logging.getLogger(__name__)


class DeviceRegistry:
    """
    设备能力注册表
    
    职责:
    - 监听 system/announce Topic，接收设备广播
    - 将设备信息存入 Dapr State Store (Redis)
    - 提供设备查询接口
    - Agent 通过 get_tools() 获取动态工具列表
    - 心跳超时检测（30 秒无心跳则标记为离线）
    """
    
    def __init__(self, store_name: str = "statestore"):
        """
        初始化设备注册表
        
        Args:
            store_name: Dapr State Store 名称（默认 "statestore"，对应 Redis）
        """
        self.store = StateStore(store_name=store_name)
        self.pubsub = PubSub(pubsub_name="pubsub")
        self._device_cache: Dict[str, dict] = {}  # 内存缓存
        self._last_heartbeat: Dict[str, float] = {}  # 心跳时间戳
        self._heartbeat_timeout = 30  # 心跳超时时间（秒）
        self._device_list_key = "device:list"  # 设备列表键
    
    async def register_device(self, announce: DeviceAnnounce) -> bool:
        """
        注册设备（监听 system/announce Topic 时调用）
        
        将设备信息存入 Dapr State Store (Redis)
        
        Args:
            announce: 设备广播包（必须包含 device_id, capabilities, location）
            
        Returns:
            是否注册成功
        """
        try:
            device_id = announce.device_id
            
            # 验证必需字段
            if not announce.device_id:
                logger.error("DeviceAnnounce missing required field: device_id")
                return False
            if not announce.capabilities:
                logger.error("DeviceAnnounce missing required field: capabilities")
                return False
            if not announce.location:
                logger.error("DeviceAnnounce missing required field: location")
                return False
            
            # 保存设备信息到 Dapr State Store (Redis)
            # Pydantic V2 兼容
            try:
                device_data = announce.model_dump()
            except AttributeError:
                device_data = announce.dict()
            
            success = await self.store.save(
                key=f"device:{device_id}",
                value=device_data
            )
            
            if not success:
                logger.error(f"Failed to save device {device_id} to state store")
                return False
            
            # 更新内存缓存
            self._device_cache[device_id] = device_data
            self._last_heartbeat[device_id] = time.time()
            
            # 添加到设备列表
            await self._add_to_list(device_id)
            
            logger.info(
                f"✅ Registered device: {device_id} "
                f"at {announce.location} with {len(announce.capabilities)} capabilities"
            )
            return True
        
        except Exception as e:
            logger.error(f"Failed to register device {announce.device_id}: {e}")
            return False
    
    async def get_device(self, device_id: str) -> Optional[DeviceAnnounce]:
        """
        查询设备
        
        Args:
            device_id: 设备ID
            
        Returns:
            DeviceAnnounce 对象，如果不存在则返回 None
        """
        try:
            # 先检查内存缓存
            if device_id in self._device_cache:
                return DeviceAnnounce(**self._device_cache[device_id])
            
            # 从 Dapr State Store (Redis) 读取
            device_data = await self.store.get(key=f"device:{device_id}")
            
            if device_data:
                # 更新缓存
                self._device_cache[device_id] = device_data
                return DeviceAnnounce(**device_data)
            
            return None
        
        except Exception as e:
            logger.error(f"Failed to get device {device_id}: {e}")
            return None
    
    async def list_capabilities(self, device_id: Optional[str] = None) -> List[DeviceCapability]:
        """
        列出设备能力
        
        Args:
            device_id: 设备ID，如果为 None 则返回所有设备的能力
            
        Returns:
            能力列表
        """
        if device_id:
            # 返回特定设备的能力
            device = await self.get_device(device_id)
            if device:
                return device.capabilities
            return []
        else:
            # 返回所有设备的能力
            all_devices = await self.get_all_devices()
            capabilities = []
            for device in all_devices:
                capabilities.extend(device.capabilities)
            return capabilities
    
    async def get_all_devices(self) -> List[DeviceAnnounce]:
        """
        获取所有设备
        
        Returns:
            设备列表
        """
        try:
            # 从设备列表获取所有设备ID
            device_ids = await self._get_device_list()
            
            devices = []
            for device_id in device_ids:
                device = await self.get_device(device_id)
                if device:
                    devices.append(device)
            
            return devices
        
        except Exception as e:
            logger.error(f"Failed to get all devices: {e}")
            return []
    
    async def get_online_devices(self) -> List[DeviceAnnounce]:
        """
        获取所有在线设备（处理 Device Offline 异常）
        
        Returns:
            在线设备列表
        """
        all_devices = await self.get_all_devices()
        online_devices = []
        
        for device in all_devices:
            if self.is_device_online(device.device_id):
                online_devices.append(device)
            else:
                logger.debug(f"Device {device.device_id} is offline (heartbeat timeout)")
        
        return online_devices
    
    async def get_tools(self) -> List[Dict[str, Any]]:
        """
        获取所有设备的工具描述（供 LLM Agent 使用）
        
        Agent 在规划任务时，必须先调用此方法获取当前活跃设备列表。
        
        Returns:
            工具描述列表（JSON Schema 格式，供 LLM 使用）
        """
        # 只返回在线设备的工具（处理 Device Offline）
        devices = await self.get_online_devices()
        tools = []
        
        for device in devices:
            for capability in device.capabilities:
                tool = {
                    "type": "function",
                    "function": {
                        "name": f"{device.device_id}.{capability.name}",
                        "description": (
                            f"{capability.description} "
                            f"(设备: {device.device_id}, 位置: {device.location})"
                        ),
                        "parameters": capability.parameters or {
                            "type": "object",
                            "properties": {}
                        }
                    }
                }
                tools.append(tool)
        
        logger.debug(f"Generated {len(tools)} tools from {len(devices)} online devices")
        return tools
    
    async def update_heartbeat(self, device_id: str) -> bool:
        """
        更新设备心跳
        
        Args:
            device_id: 设备ID
            
        Returns:
            是否更新成功
        """
        try:
            self._last_heartbeat[device_id] = time.time()
            
            # 更新注册表中的时间戳
            device = await self.get_device(device_id)
            if device:
                device_data = device.dict()
                device_data["last_heartbeat"] = time.time()
                await self.store.save(
                    key=f"device:{device_id}",
                    value=device_data
                )
                self._device_cache[device_id] = device_data
            
            return True
        
        except Exception as e:
            logger.error(f"Failed to update heartbeat for {device_id}: {e}")
            return False
    
    async def unregister_device(self, device_id: str) -> bool:
        """
        注销设备
        
        Args:
            device_id: 设备ID
            
        Returns:
            是否注销成功
        """
        try:
            # 从 Dapr State Store (Redis) 删除
            await self.store.delete(key=f"device:{device_id}")
            
            # 从内存缓存删除
            self._device_cache.pop(device_id, None)
            self._last_heartbeat.pop(device_id, None)
            
            # 从设备列表删除
            await self._remove_from_list(device_id)
            
            logger.info(f"❌ Unregistered device: {device_id}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to unregister device {device_id}: {e}")
            return False
    
    def is_device_online(self, device_id: str) -> bool:
        """
        检查设备是否在线（处理 Device Offline 异常）
        
        禁止假设某个设备一定在线，必须调用此方法检查。
        
        Args:
            device_id: 设备ID
            
        Returns:
            是否在线
        """
        if device_id not in self._last_heartbeat:
            return False
        
        last_heartbeat = self._last_heartbeat[device_id]
        elapsed = time.time() - last_heartbeat
        
        return elapsed < self._heartbeat_timeout
    
    async def _get_device_list(self) -> List[str]:
        """获取设备列表"""
        device_ids = await self.store.get(key=self._device_list_key, default=[])
        return device_ids if isinstance(device_ids, list) else []
    
    async def _add_to_list(self, device_id: str):
        """添加到设备列表"""
        device_ids = await self._get_device_list()
        if device_id not in device_ids:
            device_ids.append(device_id)
            await self.store.save(key=self._device_list_key, value=device_ids)
    
    async def _remove_from_list(self, device_id: str):
        """从设备列表删除"""
        device_ids = await self._get_device_list()
        if device_id in device_ids:
            device_ids.remove(device_id)
            await self.store.save(key=self._device_list_key, value=device_ids)
