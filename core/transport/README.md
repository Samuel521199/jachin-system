# Jachin Link - Zero Trust Network Layer

Tier 2 核心组件：零信任网络层

## 功能

- gRPC over HTTPS (HTTP/2) 通信
- mTLS 双向认证
- P2P 直连 + 全球中继兜底
- 扫码即连的极简配对流程

## 文件说明

- `gateway.py` - gRPC Server，接收 Tier 3 连接
- `mtls_manager.py` - 证书管理器，处理 mTLS 认证
- `protocol.proto` - gRPC 协议定义

## 技术栈

- gRPC (Python)
- OpenSSL / cryptography
- mTLS (双向 TLS)
