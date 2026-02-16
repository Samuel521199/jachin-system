# Dapr 集成指南

## 概述

Jachin-System 使用 Dapr (Distributed Application Runtime) 作为服务发现和管理的核心组件，实现多语言、多平台服务的统一通信。

## 为什么选择 Dapr？

1. **多语言支持**: Python (后端), Rust (桌面端), C++ (IoT) 可以通过统一的 HTTP/gRPC 标准互相调用
2. **解耦**: 基础设施变更（如 Redis → Kafka）只需修改 YAML 配置，无需改代码
3. **服务发现**: 通过 `app-id` 调用服务，无需硬编码 IP 地址
4. **生产就绪**: 支持 Kubernetes、云原生部署

## 架构设计

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│  Backend    │◄───────►│  Dapr       │◄───────►│  Desktop    │
│  (Python)   │         │  Sidecar    │         │  (Rust)     │
└─────────────┘         └─────────────┘         └─────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  Redis/Postgres │
                    │  (State Store)  │
                    └─────────────────┘
```

## 目录结构

```
dapr/
├── config/
│   └── config.yaml          # Dapr 全局配置
├── components/
│   ├── statestore-redis.yaml    # 状态存储组件（Redis）
│   ├── pubsub-redis.yaml        # 发布订阅组件（Redis）
│   └── secretstore-local.yaml   # 密钥存储组件（本地文件）
└── secrets/
    └── secrets.json             # 密钥文件（不应提交到 Git）
```

## 快速开始

### 1. 启动服务（带 Dapr Sidecar）

```bash
# 启动所有服务（包括 Dapr）
docker-compose up -d

# 检查 Dapr sidecar 状态
curl http://localhost:3500/v1.0/healthz
```

### 2. 在代码中使用 Dapr

#### 服务调用（Service Invocation）

```python
from core.dapr import service_invocation

# 调用其他服务（通过 app-id）
result = await service_invocation.invoke(
    app_id="desktop-client",
    method_name="/api/device-status",
    data={"device_id": "raspberry-pi-001"},
)
```

#### 状态管理（State Store）

```python
from core.dapr import state_store

# 保存状态
await state_store.save("user:123", {"name": "Alice", "age": 30})

# 获取状态
user = await state_store.get("user:123", {})

# 删除状态
await state_store.delete("user:123")
```

#### 发布订阅（Pub/Sub）

```python
from core.dapr import pubsub

# 发布消息
await pubsub.publish(
    topic="device-events",
    data={"device_id": "raspberry-pi-001", "event": "motion_detected"},
)

# 订阅消息（在 FastAPI 路由中）
from fastapi import FastAPI
app = FastAPI()

async def handle_device_event(data: dict):
    print(f"Received: {data}")

app.include_router(
    pubsub.subscribe("device-events", handle_device_event)
)
```

## 配置说明

### 状态存储（statestore-redis.yaml）

使用 Redis 作为状态存储后端。如需切换到 PostgreSQL：

```yaml
apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: statestore
spec:
  type: state.postgresql
  version: v1
  metadata:
    - name: connectionString
      value: "postgresql://user:password@localhost:5432/dbname"
```

### 发布订阅（pubsub-redis.yaml）

使用 Redis Streams 作为消息队列。如需切换到 Kafka：

```yaml
apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: pubsub
spec:
  type: pubsub.kafka
  version: v1
  metadata:
    - name: brokers
      value: "localhost:9092"
```

## 服务注册

每个服务需要在启动时指定 `app-id`：

### Python (FastAPI)

```python
# main.py
import uvicorn

if __name__ == "__main__":
    # Dapr sidecar 会自动发现 app-id
    # 通过环境变量 DAPR_APP_ID 或启动参数指定
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
```

### Docker Compose

```yaml
backend-dapr:
  command: [
    "./daprd",
    "-app-id", "backend",  # 服务标识
    "-app-port", "8000",
    ...
  ]
```

## 服务调用示例

### 从 Desktop 客户端调用 Backend

```rust
// Rust (Tauri) 示例
use reqwest;

let response = reqwest::Client::new()
    .post("http://localhost:3500/v1.0/invoke/backend/method/api/chat")
    .json(&json!({"message": "Hello"}))
    .send()
    .await?;
```

### 从 Backend 调用 IoT 客户端

```python
# Python 示例
from core.dapr import service_invocation

result = await service_invocation.invoke(
    app_id="iot-client",
    method_name="/api/control",
    data={"action": "turn_on_led", "pin": 18},
)
```

## 最佳实践

1. **使用 app-id 而非 IP**: 始终通过 `app-id` 调用服务，Dapr 会自动处理服务发现

2. **配置驱动**: 基础设施变更（Redis → Kafka）只需修改 YAML，无需改代码

3. **错误处理**: 服务调用可能失败，始终处理异常和超时

4. **幂等性**: 状态操作应该是幂等的，支持重试

5. **监控**: 使用 Zipkin 进行分布式追踪

## 本地开发（不使用 Docker）

如果不想使用 Docker Compose，可以手动启动 Dapr：

```bash
# 安装 Dapr CLI
# Windows: choco install dapr-cli
# macOS: brew install dapr/tap/dapr-cli
# Linux: wget -q https://raw.githubusercontent.com/dapr/cli/master/install/install.sh -O - | /bin/bash

# 初始化 Dapr
dapr init

# 启动应用（带 Dapr sidecar）
dapr run --app-id backend --app-port 8000 -- uvicorn main:app --port 8000
```

## 生产部署

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
spec:
  template:
    metadata:
      annotations:
        dapr.io/enabled: "true"
        dapr.io/app-id: "backend"
        dapr.io/app-port: "8000"
    spec:
      containers:
        - name: backend
          image: jachin-backend:latest
```

## 故障排查

### 检查 Dapr Sidecar 状态

```bash
# 健康检查
curl http://localhost:3500/v1.0/healthz

# 查看组件状态
curl http://localhost:3500/v1.0/components

# 查看配置
curl http://localhost:3500/v1.0/configuration/jachin-config
```

### 查看日志

```bash
# Docker Compose
docker-compose logs backend-dapr

# Kubernetes
kubectl logs <pod-name> -c daprd
```

## 相关文档

- [Dapr 官方文档](https://docs.dapr.io/)
- [Dapr Python SDK](https://github.com/dapr/python-sdk)
- [Dapr 最佳实践](https://docs.dapr.io/developing-applications/best-practices/)
