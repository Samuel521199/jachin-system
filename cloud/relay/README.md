# Jachin Relay - Global Relay Service

Tier 1 云端中枢组件：全球加密中继服务

## 功能

- 加密流量转发（端到端加密，无法解密）
- 智能路由选择（选择最近的节点）
- P2P 直连失败时的兜底方案

## 技术栈

- Go（高性能转发）
- gRPC over HTTPS (HTTP/2)
- mTLS 双向认证
