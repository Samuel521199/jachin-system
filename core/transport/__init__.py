"""
Jachin Link - Zero Trust Network Layer
Jachin Link 零信任网络层

替代 Tailscale VPN，实现：
- gRPC over HTTPS (HTTP/2) 通信
- mTLS 双向认证
- P2P 直连 + 全球中继兜底
- 扫码即连的极简配对流程
"""

__version__ = "3.2.0"

from core.transport.mtls_manager import MTLSManager
from core.transport.connection_manager import ConnectionManager
from core.transport.gateway import JachinLinkGateway, JachinLinkGatewayServicer

__all__ = [
    "MTLSManager",
    "ConnectionManager",
    "JachinLinkGateway",
    "JachinLinkGatewayServicer",
]
