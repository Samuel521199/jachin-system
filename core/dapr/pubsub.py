"""
PubSub - 发布订阅封装

通过 Dapr 进行消息发布和订阅，实现服务间解耦通信。
"""

import json
import logging
from typing import Dict, Any, Optional, Callable, Awaitable

from .client import dapr_client

logger = logging.getLogger(__name__)


class PubSub:
    """
    发布订阅封装类
    
    使用 Dapr 的发布订阅组件（默认使用 Redis）进行消息传递。
    """
    
    def __init__(self, pubsub_name: str = "pubsub"):
        """
        初始化发布订阅客户端
        
        Args:
            pubsub_name: Dapr 发布订阅组件名称（默认 "pubsub"）
        """
        self.pubsub_name = pubsub_name
        self._use_dapr = dapr_client is not None
        if not self._use_dapr:
            logger.warning("Dapr client not available, PubSub will log messages only")
    
    async def publish(
        self,
        topic: str,
        data: Any,
        metadata: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        发布消息到主题
        
        Args:
            topic: 主题名称
            data: 消息数据（可以是字典、列表等可序列化对象）
            metadata: 元数据（可选）
        
        Returns:
            是否发布成功
        
        Example:
            ```python
            await pubsub.publish(
                topic="device-events",
                data={"device_id": "raspberry-pi-001", "event": "motion_detected"},
            )
            ```
        """
        try:
            if not self._use_dapr:
                # Dapr 不可用时，只记录日志
                logger.warning(f"Dapr not available, message to topic '{topic}' logged only: {data}")
                return True
            
            # 序列化数据
            if isinstance(data, (dict, list)):
                data_bytes = json.dumps(data).encode()
            elif isinstance(data, str):
                data_bytes = data.encode()
            else:
                data_bytes = data
            
            dapr_client.client.publish_event(
                pubsub_name=self.pubsub_name,
                topic_name=topic,
                data=data_bytes,
                metadata=metadata or {},
            )
            
            logger.debug(f"Published message to topic: {topic}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to publish to topic {topic}: {e}")
            return False
    
    def subscribe(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]],
        route: Optional[str] = None,
    ):
        """
        订阅主题（需要在 FastAPI 路由中注册）
        
        Args:
            topic: 主题名称
            handler: 消息处理函数（异步）
            route: 订阅路由路径（如果为 None，使用 /dapr/subscribe/{topic}）
        
        Note:
            这个方法返回的路由处理器需要在 FastAPI 应用中注册。
            实际订阅通过 Dapr 的 /dapr/subscribe 端点自动完成。
        
        Example:
            ```python
            async def handle_device_event(data: Dict[str, Any]):
                print(f"Received event: {data}")
            
            app.include_router(
                pubsub.subscribe("device-events", handle_device_event)
            )
            ```
        """
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse, Response
        
        router = APIRouter()
        route_path = route or f"/dapr/subscribe/{topic}"
        
        @router.post(route_path)
        async def subscription_handler(request: Request):
            """Dapr 订阅处理器"""
            try:
                # Dapr 发送的 CloudEvent 格式
                event = await request.json()
                
                # 提取数据
                if "data" in event:
                    data = json.loads(event["data"]) if isinstance(event["data"], str) else event["data"]
                else:
                    data = event
                
                # 调用处理函数
                await handler(data)
                
                # Dapr 期望成功时返回 HTTP 200，空响应体
                return Response(status_code=200)
            
            except Exception as e:
                logger.error(f"Error handling subscription for {topic}: {e}")
                return JSONResponse(
                    content={"status": "error", "message": str(e)},
                    status_code=500
                )
        
        return router


# 全局实例
pubsub = PubSub()
