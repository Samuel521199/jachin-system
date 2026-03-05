# Jachin Link Client Library

跨平台客户端网络库（Tier 3）

## 支持平台

- Desktop (Tauri v2 + React)
- Mobile (Flutter)
- IoT (Python/Rust 嵌入式)

## 功能

- 扫码配对（首次连接）
- mTLS 连接管理
- P2P 直连 + 全球中继兜底
- AI 指令传输（gRPC over HTTPS）

## 使用示例

```python
from jachin_link_client import JachinLinkClient

client = JachinLinkClient()
await client.pair_with_qr_code(qr_data)
await client.connect()
response = await client.send_ai_request(command)
```
