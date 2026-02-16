"""
DaprClient - Dapr 客户端封装

提供统一的 Dapr 客户端接口。
"""

import os
import logging
import threading
from typing import Optional

try:
    from dapr.clients import DaprClient as DaprSDKClient
    from dapr.clients.grpc._state import StateItem
    from dapr.clients.grpc._request import TransactionalStateOperation, TransactionOperationType
    DAPR_AVAILABLE = True
except ImportError:
    DAPR_AVAILABLE = False
    # 定义占位符类，避免 NameError
    DaprSDKClient = None
    StateItem = None
    TransactionalStateOperation = None
    TransactionOperationType = None
    logging.warning("dapr package not installed. DaprClient will not work.")

from core.config import settings

logger = logging.getLogger(__name__)


class DaprClient:
    """
    Dapr 客户端封装类（单例模式）
    
    提供对 Dapr sidecar 的访问，支持：
    - 服务调用 (Service Invocation)
    - 状态管理 (State Store)
    - 发布订阅 (Pub/Sub)
    - 配置管理 (Configuration)
    - 密钥管理 (Secrets)
    """
    
    _instance: Optional["DaprClient"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """单例模式实现"""
        import threading
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """初始化 Dapr 客户端"""
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        if not DAPR_AVAILABLE:
            raise ImportError(
                "dapr package is required for DaprClient. "
                "Install it with: pip install dapr"
            )
        
        # 从配置读取 Dapr 端口
        self.dapr_http_port = settings.DAPR_HTTP_PORT
        self.dapr_grpc_port = settings.DAPR_GRPC_PORT
        
        # 初始化 Dapr SDK 客户端
        try:
            self.client = DaprSDKClient(
                address=f"localhost:{self.dapr_grpc_port}"
            )
            logger.info(
                f"DaprClient initialized - HTTP: {self.dapr_http_port}, "
                f"gRPC: {self.dapr_grpc_port}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize Dapr client: {e}")
            raise
        
        self._initialized = True
    
    def get_client(self):
        """
        获取底层 Dapr SDK 客户端
        
        Returns:
            DaprSDKClient 实例（如果可用）
        """
        if not DAPR_AVAILABLE:
            raise RuntimeError("Dapr SDK is not installed. Install with: pip install dapr")
        return self.client
    
    def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            Dapr sidecar 是否可用
        """
        try:
            # 尝试调用 Dapr 的健康检查端点
            import httpx
            response = httpx.get(
                f"http://localhost:{self.dapr_http_port}/v1.0/healthz",
                timeout=2.0
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Dapr health check failed: {e}")
            return False


# 全局单例实例
dapr_client = DaprClient() if DAPR_AVAILABLE else None
