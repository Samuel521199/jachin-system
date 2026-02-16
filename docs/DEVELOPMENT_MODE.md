# 开发模式指南

## 概述

开发模式的设计理念：
- **中间件服务**（Redis、MQTT、Dapr Placement等）- 通过 Docker 运行，后台服务
- **后端应用服务** - 在控制台运行，方便查看日志、调试和错误追踪

## 为什么使用控制台模式？

### 优势

1. **即时错误反馈**
   - 所有错误和异常直接显示在控制台
   - 完整的 Python 堆栈跟踪
   - 无需查看 Docker 日志

2. **实时日志**
   - 看到所有日志输出
   - 可以设置不同的日志级别
   - 方便调试和追踪问题

3. **快速迭代**
   - 代码修改后自动重载（uvicorn --reload）
   - 无需重启容器
   - 开发效率更高

4. **调试友好**
   - 可以直接使用 Python 调试器（pdb）
   - 可以设置断点
   - IDE 集成更好

## 启动方式

### 方式 1: 一键启动（推荐）

```powershell
.\scripts\start_dev.ps1
```

这会：
1. 检查环境（Conda、依赖）
2. 启动中间件服务（Docker）
3. 检查本地数据库（PostgreSQL、Qdrant）
4. 加载环境变量
5. 启动后端服务（控制台）

### 方式 2: 分步启动

#### 步骤 1: 启动中间件服务

```powershell
.\scripts\start_dev_services.ps1
```

或手动：
```powershell
docker-compose -f docker-compose.dev.yml -p jachin-dev up -d
```

#### 步骤 2: 启动后端服务

```powershell
.\scripts\start_backend_dev.ps1
```

### 方式 3: 不使用 Dapr（纯控制台）

如果不需要 Dapr 功能，可以直接运行：

```powershell
conda activate jachin-dev
# 端口从环境变量读取，默认 18888
python -m uvicorn core.main:app --host 0.0.0.0 --port ${env:APP_PORT ?? 18888} --reload --log-level info
```

## 日志和调试

### 日志级别

开发模式默认使用 `INFO` 级别日志，显示详细信息：

```powershell
# 使用详细日志
python -m uvicorn core.main:app --log-level debug

# 使用警告级别（减少输出）
python -m uvicorn core.main:app --log-level warning
```

### 错误处理

所有错误都会直接显示在控制台：

```
[ERROR] Database connection failed
Traceback (most recent call last):
  File "core/main.py", line 45, in startup
    await init_database()
  ...
```

### 调试技巧

1. **使用 Python 调试器**
   ```python
   import pdb; pdb.set_trace()
   ```

2. **查看详细日志**
   ```powershell
   # 设置环境变量
   $env:DEBUG = "true"
   python -m uvicorn core.main:app --log-level debug
   ```

3. **检查中间件连接**
   ```powershell
   # Redis
   docker exec jachin-redis-dev redis-cli ping
   
   # MQTT
   docker exec jachin-mqtt-dev mosquitto_pub -h localhost -t test -m "hello"
   ```

## 环境变量

开发模式会自动加载 `.env` 文件中的环境变量。

常用环境变量：
```env
# 数据库
DATABASE_URL=postgresql://jachin:secure_password@localhost:5432/jachin_brain

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_GRPC_URL=http://localhost:6334

# Redis
REDIS_URL=redis://localhost:6379

# 应用服务端口配置（默认使用非常用端口避免冲突）
SERVER_HOST=0.0.0.0
SERVER_PORT=18888
APP_PORT=18888

# Dapr 端口配置
DAPR_HTTP_PORT=13500
DAPR_GRPC_PORT=15001

# LLM
QWEN_API_KEY=your-api-key-here
LLM_PROVIDER=qwen-v2
```

详细端口配置说明请参考 [端口配置文档](PORT_CONFIGURATION.md)。

## 常见问题

### 问题 1: 端口被占用

如果默认端口（18888）被占用，可以通过环境变量修改：

```powershell
# 设置自定义端口
$env:APP_PORT = "19999"
.\scripts\start_backend_dev.ps1
```

详细端口配置说明请参考 [端口配置文档](PORT_CONFIGURATION.md)。

**错误**: `Address already in use`

**解决**:
```powershell
# 检查端口占用（默认端口 18888）
netstat -ano | findstr :18888
# 或检查环境变量端口
netstat -ano | findstr :${env:APP_PORT}

# 停止占用端口的进程
taskkill /PID <PID> /F
```

### 问题 2: 数据库连接失败

**错误**: `Connection refused` 或 `password authentication failed`

**解决**:
```powershell
# 检查 PostgreSQL
.\scripts\check_local_databases.ps1

# 修复用户认证
.\scripts\fix_postgres_quick.ps1
```

### 问题 3: 中间件服务未运行

**错误**: `Connection refused` 到 Redis/MQTT

**解决**:
```powershell
# 检查 Docker 服务
.\scripts\check_dev_services.ps1

# 启动服务
.\scripts\start_dev_services.ps1
```

### 问题 4: 代码修改不生效

**解决**:
- 确保使用了 `--reload` 参数
- 检查文件是否保存
- 查看控制台是否有重载消息

## 与生产模式的区别

| 特性 | 开发模式 | 生产模式 |
|------|---------|---------|
| 后端运行方式 | 控制台 | Docker 容器 |
| 日志输出 | 控制台 | Docker 日志 |
| 自动重载 | 是（--reload） | 否 |
| 调试 | 方便（直接调试） | 需要进入容器 |
| 性能 | 略低 | 更高 |
| 错误可见性 | 即时显示 | 需要查看日志 |

## 最佳实践

1. **开发时使用控制台模式**
   - 方便调试和查看错误
   - 快速迭代

2. **测试时使用 Docker 模式**
   - 更接近生产环境
   - 测试容器化部署

3. **生产环境使用 Docker Compose**
   - 完整的容器化部署
   - 更好的资源管理

## 相关脚本

- `scripts/start_dev.ps1` - 一键启动开发环境
- `scripts/start_backend_dev.ps1` - 仅启动后端（控制台）
- `scripts/start_dev_services.ps1` - 仅启动中间件（Docker）
- `scripts/check_dev_services.ps1` - 检查服务状态
- `scripts/check_local_databases.ps1` - 检查本地数据库
