# Dapr Components Configuration

## 概述

本目录包含 Dapr 组件配置文件，用于连接基础设施服务。

## 重要说明：本地开发环境配置

在**混合开发模式**下（基础设施在 Docker，后端在本地 Conda），组件配置需要使用 `localhost` 而不是容器名称：

- ✅ **开发环境**: `localhost:6379`（通过端口映射连接 Docker 容器）
- ❌ **生产环境**: `redis:6379`（容器内部网络）

## 组件列表

### statestore-redis.yaml

状态存储组件，使用 Redis 作为后端。

**配置说明**:
- `redisHost`: `localhost:6379` - 连接本地 Docker 容器的 Redis
- `redisPassword`: 空字符串（如果设置了密码需要填写）

**使用方式**:
```python
from core.dapr import state_store

# 保存状态
await state_store.save("key", {"value": "data"})

# 获取状态
data = await state_store.get("key", {})
```

### pubsub-redis.yaml

发布订阅组件，使用 Redis Streams 作为消息队列。

**配置说明**:
- `redisHost`: `localhost:6379` - 连接本地 Docker 容器的 Redis
- `redisPassword`: 空字符串

**使用方式**:
```python
from core.dapr import pubsub

# 发布消息
await pubsub.publish("topic", {"data": "message"})
```

### secretstore-local.yaml

密钥存储组件，使用本地文件存储密钥。

**配置说明**:
- `secretsFile`: `/dapr/secrets/secrets.json` - 密钥文件路径
- **注意**: 生产环境应使用云密钥管理服务

## 环境差异

### 开发环境（本地 Conda）

组件配置使用 `localhost` 连接 Docker 容器：

```yaml
metadata:
  - name: redisHost
    value: localhost:6379  # 通过端口映射
```

### 生产环境（Docker/Kubernetes）

组件配置使用容器名称或服务发现：

```yaml
metadata:
  - name: redisHost
    value: redis:6379  # 容器内部网络
```

## 验证配置

### 检查 Redis 连接

```bash
# 检查 Redis 是否运行
docker ps | grep jachin-redis-dev

# 测试连接
docker exec jachin-redis-dev redis-cli ping
# 应该返回: PONG

# 从本地测试连接
redis-cli -h localhost -p 6379 ping
```

### 检查 Dapr 组件

启动后端服务后，检查组件是否加载：

```bash
# 查看 Dapr 组件状态
curl http://localhost:3500/v1.0/components
```

## 故障排查

### 问题：无法连接到 Redis

**症状**: Dapr 报错 "connection refused"

**解决方案**:
1. 检查 Redis 容器是否运行: `docker ps | grep redis`
2. 检查端口映射: `docker port jachin-redis-dev`
3. 验证本地连接: `redis-cli -h localhost -p 6379 ping`

### 问题：组件未加载

**症状**: Dapr 启动时没有加载组件

**解决方案**:
1. 检查组件文件路径是否正确
2. 检查 YAML 语法是否正确
3. 查看 Dapr 日志: `dapr run ... --log-level debug`

## 相关文档

- [Dapr 组件文档](https://docs.dapr.io/reference/components-reference/)
- [Redis 状态存储](https://docs.dapr.io/reference/components-reference/supported-state-stores/setup-redis/)
- [Redis 发布订阅](https://docs.dapr.io/reference/components-reference/supported-pubsub/setup-redis-pubsub/)
