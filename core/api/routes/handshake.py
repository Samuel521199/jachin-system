"""
Device Handshake API - 设备握手路由

监听 system/announce Topic，接收设备广播并注册到 DeviceRegistry。
严格遵循 .cursor/rules/010-protocol-registry.mdc 规则。
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response
import logging
import json

from core.registry.protocol import DeviceAnnounce
from core.registry.registry import DeviceRegistry
from core.dapr import PubSub

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v2/handshake", tags=["handshake"])

# 创建设备查询路由
device_router = APIRouter(prefix="/api/v2/devices", tags=["devices"])

# 初始化 DeviceRegistry
registry = DeviceRegistry()
pubsub = PubSub(pubsub_name="pubsub")


@router.post("/announce")
async def handle_announce(request: Request):
    """
    处理设备广播（通过 Dapr Pub/Sub 调用）
    
    当设备通过 system/announce 主题广播时，Dapr 会调用此端点。
    """
    try:
        # 从请求体读取数据
        data = await request.json()
        
        # 验证数据格式
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Invalid data format")
        
        # 转换为 DeviceAnnounce 对象
        announce = DeviceAnnounce(**data)
        
        # 注册设备
        success = await registry.register_device(announce)
        
        if success:
            logger.info(
                f"Device registered: {announce.device_id} "
                f"at {announce.location} with {len(announce.capabilities)} capabilities"
            )
            return {
                "status": "registered",
                "device_id": announce.device_id,
                "capabilities_count": len(announce.capabilities)
            }
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to register device {announce.device_id}"
            )
    
    except Exception as e:
        logger.error(f"Error handling device announce: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/heartbeat")
async def handle_heartbeat(request: Request):
    """
    处理设备心跳
    
    当设备通过 system/heartbeat 主题发送心跳时，Dapr 会调用此端点。
    """
    try:
        data = await request.json()
        device_id = data.get("device_id")
        
        if not device_id:
            raise HTTPException(status_code=400, detail="device_id is required")
        
        # 更新心跳
        success = await registry.update_heartbeat(device_id)
        
        if success:
            return {"status": "ok", "device_id": device_id}
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update heartbeat for {device_id}"
            )
    
    except Exception as e:
        logger.error(f"Error handling heartbeat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/unregister")
async def handle_unregister(request: Request):
    """
    处理设备注销
    
    当设备通过 system/unregister 主题注销时，Dapr 会调用此端点。
    """
    try:
        data = await request.json()
        device_id = data.get("device_id")
        
        if not device_id:
            raise HTTPException(status_code=400, detail="device_id is required")
        
        # 注销设备
        success = await registry.unregister_device(device_id)
        
        if success:
            return {"status": "unregistered", "device_id": device_id}
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to unregister device {device_id}"
            )
    
    except Exception as e:
        logger.error(f"Error handling unregister: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Dapr Pub/Sub 订阅处理器
# 注意：Dapr 会自动调用这些端点，当有消息发布到对应主题时

def create_subscription_router():
    """
    创建 Dapr 订阅路由
    
    返回一个包含订阅端点的路由器，用于处理 Dapr Pub/Sub 消息。
    """
    from fastapi import APIRouter, Request
    
    sub_router = APIRouter()
    
    @sub_router.post("/dapr/subscribe/system/announce")
    async def subscribe_announce(request: Request):
        """处理 system/announce 主题的消息"""
        try:
            event = await request.json()
            
            # Dapr 发送的消息格式是 CloudEvent 格式
            # CloudEvent 格式: { "data": "...", "datacontenttype": "application/json", ... }
            # 提取实际数据
            if isinstance(event, dict) and "data" in event:
                # CloudEvent 格式 - data 可能是字符串或对象
                data_str = event.get("data", {})
                if isinstance(data_str, str):
                    # 如果是字符串，需要解析 JSON
                    actual_data = json.loads(data_str)
                else:
                    # 如果已经是对象，直接使用
                    actual_data = data_str
            else:
                # 直接数据格式（不太可能，但兼容处理）
                actual_data = event
            
            logger.info(f"Received device announce: {actual_data.get('device_id', 'unknown')}")
            
            # 转换为 DeviceAnnounce
            announce = DeviceAnnounce(**actual_data)
            
            # 注册设备
            success = await registry.register_device(announce)
            
            if success:
                logger.info(f"Device registered via Pub/Sub: {announce.device_id}")
                # Dapr 期望成功时返回 HTTP 200，空响应体
                return Response(status_code=200)
            else:
                return JSONResponse(
                    status_code=500,
                    content={"status": "error", "message": "Failed to register device"}
                )
        
        except Exception as e:
            logger.error(f"Error in subscribe_announce: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e)}
            )
    
    @sub_router.post("/dapr/subscribe/system/heartbeat")
    async def subscribe_heartbeat(request: Request):
        """处理 system/heartbeat 主题的消息"""
        try:
            event = await request.json()
            
            # CloudEvent 格式处理
            if isinstance(event, dict) and "data" in event:
                data_str = event.get("data", {})
                if isinstance(data_str, str):
                    actual_data = json.loads(data_str)
                else:
                    actual_data = data_str
            else:
                actual_data = event
            
            device_id = actual_data.get("device_id")
            
            if not device_id:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "device_id is required"}
                )
            
            # 更新心跳
            success = await registry.update_heartbeat(device_id)
            
            if success:
                # Dapr 期望成功时返回 HTTP 200，空响应体
                return Response(status_code=200)
            else:
                return JSONResponse(
                    status_code=500,
                    content={"status": "error", "message": "Failed to update heartbeat"}
                )
        
        except Exception as e:
            logger.error(f"Error in subscribe_heartbeat: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e)}
            )
    
    @sub_router.post("/dapr/subscribe/system/unregister")
    async def subscribe_unregister(request: Request):
        """处理 system/unregister 主题的消息"""
        try:
            event = await request.json()
            
            # CloudEvent 格式处理
            if isinstance(event, dict) and "data" in event:
                data_str = event.get("data", {})
                if isinstance(data_str, str):
                    actual_data = json.loads(data_str)
                else:
                    actual_data = data_str
            else:
                actual_data = event
            
            device_id = actual_data.get("device_id")
            
            if not device_id:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "device_id is required"}
                )
            
            # 注销设备
            success = await registry.unregister_device(device_id)
            
            if success:
                # Dapr 期望成功时返回 HTTP 200，空响应体
                return Response(status_code=200)
            else:
                return JSONResponse(
                    status_code=500,
                    content={"status": "error", "message": "Failed to unregister device"}
                )
        
        except Exception as e:
            logger.error(f"Error in subscribe_unregister: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e)}
            )
    
    return sub_router


# 设备查询 API 路由

@device_router.get("")
async def list_devices():
    """
    获取所有设备列表
    
    Returns:
        设备列表
    """
    try:
        devices = await registry.get_all_devices()
        
        # 转换为字典格式
        devices_list = []
        for device in devices:
            device_dict = {
                "device_id": device.device_id,
                "device_type": device.device_type,
                "location": device.location,
                "capabilities": [
                    {
                        "name": cap.name,
                        "description": cap.description,
                        "parameters": cap.parameters
                    }
                    for cap in device.capabilities
                ],
                "metadata": device.metadata,
                "timestamp": device.timestamp,
                "online": registry.is_device_online(device.device_id)
            }
            devices_list.append(device_dict)
        
        return {
            "devices": devices_list,
            "total": len(devices_list),
            "online": sum(1 for d in devices_list if d["online"])
        }
    
    except Exception as e:
        logger.error(f"Error listing devices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@device_router.get("/{device_id}")
async def get_device(device_id: str):
    """
    获取特定设备信息
    
    Args:
        device_id: 设备ID
        
    Returns:
        设备信息
    """
    try:
        device = await registry.get_device(device_id)
        
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        
        return {
            "device_id": device.device_id,
            "device_type": device.device_type,
            "location": device.location,
            "capabilities": [
                {
                    "name": cap.name,
                    "description": cap.description,
                    "parameters": cap.parameters
                }
                for cap in device.capabilities
            ],
            "metadata": device.metadata,
            "timestamp": device.timestamp,
            "online": registry.is_device_online(device_id)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting device {device_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@device_router.get("/{device_id}/capabilities")
async def get_device_capabilities(device_id: str):
    """
    获取特定设备的能力列表
    
    Args:
        device_id: 设备ID
        
    Returns:
        能力列表
    """
    try:
        capabilities = await registry.list_capabilities(device_id)
        
        if not capabilities:
            # 检查设备是否存在
            device = await registry.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        
        return {
            "device_id": device_id,
            "capabilities": [
                {
                    "name": cap.name,
                    "description": cap.description,
                    "parameters": cap.parameters
                }
                for cap in capabilities
            ],
            "count": len(capabilities)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting capabilities for {device_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@device_router.get("/online/list")
async def list_online_devices():
    """
    获取所有在线设备列表
    
    Returns:
        在线设备列表
    """
    try:
        devices = await registry.get_online_devices()
        
        devices_list = []
        for device in devices:
            device_dict = {
                "device_id": device.device_id,
                "device_type": device.device_type,
                "location": device.location,
                "capabilities": [
                    {
                        "name": cap.name,
                        "description": cap.description,
                        "parameters": cap.parameters
                    }
                    for cap in device.capabilities
                ],
                "metadata": device.metadata,
                "timestamp": device.timestamp
            }
            devices_list.append(device_dict)
        
        return {
            "devices": devices_list,
            "total": len(devices_list)
        }
    
    except Exception as e:
        logger.error(f"Error listing online devices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
