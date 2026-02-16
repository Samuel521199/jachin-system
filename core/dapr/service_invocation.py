"""
ServiceInvocation - 服务调用封装

通过 Dapr 进行服务间调用，实现服务发现和解耦。
"""

import json
import logging
from typing import Dict, Any, Optional
import httpx

from .client import dapr_client

logger = logging.getLogger(__name__)


class ServiceInvocation:
    """
    服务调用封装类
    
    通过 Dapr 的 app-id 进行服务调用，无需知道服务的实际地址。
    """
    
    def __init__(self):
        """初始化服务调用客户端"""
        self.dapr_http_port = "3500"
        if dapr_client:
            self.dapr_http_port = dapr_client.dapr_http_port
    
    async def invoke(
        self,
        app_id: str,
        method_name: str,
        data: Optional[Dict[str, Any]] = None,
        http_verb: str = "POST",
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        调用远程服务
        
        Args:
            app_id: 目标服务的 Dapr app-id（如 "backend", "desktop-client"）
            method_name: 方法路径（如 "/api/chat", "/v1/process"）
            data: 请求数据（可选）
            http_verb: HTTP 方法（GET, POST, PUT, DELETE）
            timeout: 超时时间（秒）
        
        Returns:
            服务响应数据
        
        Example:
            ```python
            result = await service_invocation.invoke(
                app_id="backend",
                method_name="/api/chat",
                data={"message": "Hello"},
            )
            ```
        """
        url = (
            f"http://localhost:{self.dapr_http_port}/v1.0/invoke/"
            f"{app_id}/method/{method_name.lstrip('/')}"
        )
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if http_verb.upper() == "GET":
                    response = await client.get(url, params=data)
                elif http_verb.upper() == "POST":
                    response = await client.post(url, json=data)
                elif http_verb.upper() == "PUT":
                    response = await client.put(url, json=data)
                elif http_verb.upper() == "DELETE":
                    response = await client.delete(url)
                else:
                    raise ValueError(f"Unsupported HTTP verb: {http_verb}")
                
                response.raise_for_status()
                return response.json()
        
        except httpx.HTTPError as e:
            logger.error(f"Service invocation failed: {e}")
            raise Exception(f"Failed to invoke {app_id}{method_name}: {str(e)}")
    
    async def invoke_grpc(
        self,
        app_id: str,
        method_name: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        """
        通过 gRPC 调用远程服务（性能更好）
        
        Args:
            app_id: 目标服务的 Dapr app-id
            method_name: 方法路径
            data: 请求数据
        
        Returns:
            服务响应的原始字节数据
        """
        if not dapr_client:
            raise RuntimeError("Dapr client not available")
        
        try:
            # 使用 Dapr SDK 的 gRPC 调用
            response = dapr_client.client.invoke_method(
                app_id=app_id,
                method_name=method_name,
                data=json.dumps(data).encode() if data else b"",
            )
            return response.data
        
        except Exception as e:
            logger.error(f"gRPC service invocation failed: {e}")
            raise


# 全局实例
service_invocation = ServiceInvocation()
