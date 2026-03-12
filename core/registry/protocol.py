"""
JCP (Jachin Capability Protocol) - 协议定义

定义设备能力协议的所有数据模型，使用 Pydantic 进行验证。
严格遵循 .cursor/rules/010-protocol-registry.mdc 规则。
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import time


class DeviceCapability(BaseModel):
    """
    设备能力定义
    
    格式: {category}.{action}
    例如: camera.capture, light.switch, sensor.temperature
    """
    name: str  # 能力名称，格式: "{category}.{action}"
    description: str  # 能力描述（供 LLM 理解）
    parameters: Dict[str, Any] = {}  # JSON Schema 格式的参数定义
    return_type: Optional[str] = None  # 返回值类型（可选）


class DeviceAnnounce(BaseModel):
    """
    设备广播包 - 设备上线时发送
    
    所有设备 (Client/Node) 必须使用此模型进行广播。
    
    必须包含的字段（根据规则 010-protocol-registry.mdc）:
    - device_id: 设备唯一标识
    - capabilities: 设备能力列表 (List[DeviceCapability])
    - location: 设备位置
    """
    device_id: str  # 设备唯一标识 (e.g., "raspi-living-room")
    capabilities: List[DeviceCapability]  # 设备能力列表（必需）
    location: str  # 设备位置 (e.g., "living_room", "office")（必需）
    
    # 可选字段
    device_type: Optional[str] = None  # "iot-node" | "desktop-client" | "mobile-client" | "web-client"
    metadata: Dict[str, Any] = {}  # 额外元数据（IP、版本、硬件信息等）
    timestamp: float = Field(default_factory=time.time)  # 广播时间戳


class DeviceCommand(BaseModel):
    """
    设备指令包 - 大脑向设备发送执行指令
    
    格式: { target: "device_id", action: "camera.snap", params: {...} }
    """
    command_id: str  # 指令唯一标识（UUID）
    target_device_id: str  # 目标设备ID
    capability_name: str  # 要执行的能力名称（格式: {category}.{action}）
    params: Dict[str, Any] = {}  # 能力参数
    timeout: Optional[int] = 30  # 超时时间（秒）
    timestamp: float = Field(default_factory=time.time)


class DeviceResponse(BaseModel):
    """
    设备响应包 - 设备执行结果反馈
    """
    command_id: str  # 对应的指令ID
    device_id: str  # 设备ID
    status: str  # "success" | "error" | "timeout"
    result: Optional[Any] = None  # 执行结果
    error: Optional[str] = None  # 错误信息（如果失败）
    timestamp: float = Field(default_factory=time.time)
