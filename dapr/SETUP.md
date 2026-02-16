# Dapr 设置指南

## Docker Compose 模式（推荐用于开发）

### 1. 启动服务

```bash
# 启动所有服务（包括 Dapr sidecar）
docker-compose up -d

# 查看 Dapr sidecar 日志
docker-compose logs backend-dapr
```

### 2. 验证 Dapr Sidecar

```bash
# 健康检查
curl http://localhost:3500/v1.0/healthz

# 应该返回: {"status":"ok"}
```

## 本地开发模式（不使用 Docker）

### 1. 安装 Dapr CLI

**Windows (使用 Chocolatey)**:
```powershell
choco install dapr-cli
```

**macOS (使用 Homebrew)**:
```bash
brew install dapr/tap/dapr-cli
```

**Linux**:
```bash
wget -q https://raw.githubusercontent.com/dapr/cli/master/install/install.sh -O - | /bin/bash
```

### 2. 初始化 Dapr

```bash
# 初始化 Dapr（会下载 Dapr runtime 和 Docker 镜像）
dapr init

# 验证安装
dapr --version
```

### 3. 启动应用（带 Dapr Sidecar）

```bash
# 方式 1: 使用 dapr run（推荐）
dapr run \
  --app-id backend \
  --app-port 8000 \
  --components-path ./dapr/components \
  --config ./dapr/config/config.yaml \
  -- uvicorn main:app --port 8000

# 方式 2: 手动启动 sidecar（高级用法）
# 终端 1: 启动 Dapr sidecar
daprd \
  -app-id backend \
  -app-port 8000 \
  -dapr-http-port 3500 \
  -dapr-grpc-port 50001 \
  -components-path ./dapr/components \
  -config ./dapr/config/config.yaml

# 终端 2: 启动应用
uvicorn main:app --port 8000
```

## 配置说明

### 环境变量

Dapr 客户端会自动从环境变量读取配置：

```bash
# Dapr HTTP API 端口（默认 3500）
export DAPR_HTTP_PORT=3500

# Dapr gRPC API 端口（默认 50001）
export DAPR_GRPC_PORT=50001

# 应用端口
export APP_PORT=8000
```

### 组件配置

所有组件配置在 `dapr/components/` 目录下：

- `statestore-redis.yaml`: 状态存储（Redis）
- `pubsub-redis.yaml`: 发布订阅（Redis）
- `secretstore-local.yaml`: 密钥存储（本地文件）

### 全局配置

全局配置在 `dapr/config/config.yaml`，包括：
- 追踪配置（Zipkin）
- 指标配置
- 访问控制策略

## 故障排查

### 问题 1: Dapr Sidecar 无法启动

**症状**: `docker-compose logs backend-dapr` 显示错误

**解决方案**:
1. 检查组件配置文件格式是否正确（YAML 语法）
2. 确保 Redis 服务已启动: `docker-compose ps redis`
3. 检查网络连接: `docker network inspect jachin-network`

### 问题 2: 服务调用失败

**症状**: `service_invocation.invoke()` 抛出异常

**解决方案**:
1. 确认目标服务的 `app-id` 正确
2. 检查目标服务是否已启动并注册
3. 验证网络连接和端口映射

### 问题 3: 状态存储无法工作

**症状**: `state_store.save()` 返回 False

**解决方案**:
1. 检查 Redis 连接: `docker-compose exec redis redis-cli ping`
2. 验证组件配置中的 Redis 地址是否正确
3. 查看 Dapr sidecar 日志: `docker-compose logs backend-dapr`

## 生产部署注意事项

1. **密钥管理**: 生产环境应使用云密钥管理服务（如 Azure Key Vault、AWS Secrets Manager），而非本地文件

2. **高可用**: 使用 Redis Cluster 或 PostgreSQL 集群作为状态存储后端

3. **监控**: 启用 Zipkin/Jaeger 进行分布式追踪

4. **安全**: 配置访问控制策略，限制服务间调用权限

5. **Kubernetes**: 生产环境推荐使用 Kubernetes，Dapr 原生支持 K8s

## 相关资源

- [Dapr 官方文档](https://docs.dapr.io/)
- [Dapr Python SDK](https://github.com/dapr/python-sdk)
- [Dapr 示例](https://github.com/dapr/samples)
