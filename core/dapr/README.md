# Dapr Integration Module - Dapr 集成模块

## 概述

本模块提供 Dapr 的 Python SDK 封装，简化服务调用、状态管理和发布订阅的使用。

## 核心组件

### DaprClient

Dapr 客户端单例类，提供对 Dapr sidecar 的访问。

```python
from core.dapr import dapr_client

# 健康检查
is_healthy = dapr_client.health_check()
```

### ServiceInvocation

服务调用封装，通过 `app-id` 调用其他服务。

```python
from core.dapr import service_invocation

# 调用其他服务
result = await service_invocation.invoke(
    app_id="desktop-client",
    method_name="/api/device-status",
    data={"device_id": "raspberry-pi-001"},
)
```

### StateStore

状态存储封装，支持 Redis、PostgreSQL 等后端。

```python
from core.dapr import state_store

# 保存状态
await state_store.save("user:123", {"name": "Alice"})

# 获取状态
user = await state_store.get("user:123", {})

# 删除状态
await state_store.delete("user:123")
```

### PubSub

发布订阅封装，支持 Redis Streams、Kafka 等后端。

```python
from core.dapr import pubsub

# 发布消息
await pubsub.publish(
    topic="device-events",
    data={"device_id": "raspberry-pi-001", "event": "motion_detected"},
)

# 订阅消息（在 FastAPI 中）
from fastapi import FastAPI
app = FastAPI()

async def handle_event(data: dict):
    print(f"Received: {data}")

app.include_router(
    pubsub.subscribe("device-events", handle_event)
)
```

## 使用示例

详细示例请参考 `backend/examples/dapr_usage_example.py`。

## 配置

Dapr 客户端从环境变量读取配置：

- `DAPR_HTTP_PORT`: Dapr HTTP API 端口（默认 3500）
- `DAPR_GRPC_PORT`: Dapr gRPC API 端口（默认 50001）

## 注意事项

1. **单例模式**: `DaprClient` 使用单例模式，确保整个应用只有一个客户端实例

2. **异步操作**: 所有方法都是异步的，需要使用 `await` 调用

3. **错误处理**: 服务调用可能失败，始终处理异常和超时

4. **配置驱动**: 组件配置在 `dapr/components/` 目录下，修改配置后需要重启 Dapr sidecar
