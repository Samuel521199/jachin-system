# 端口配置说明

## 概述

Jachin-System 使用非常用端口作为默认端口，以避免与系统常用服务冲突。所有应用服务端口都可以通过环境变量自定义配置。

## 默认端口分配

### 中间件服务（Docker 容器）

这些服务的端口通常保持默认值，不建议修改：

| 服务 | 端口 | 说明 |
|------|------|------|
| PostgreSQL | 5432 | 数据库（本地安装） |
| Qdrant REST | 6333 | 向量数据库 REST API（本地安装） |
| Qdrant gRPC | 6334 | 向量数据库 gRPC API（本地安装） |
| Redis | 6379 | 缓存和消息队列 |
| MQTT | 1883 | IoT 消息代理 |
| MQTT WebSocket | 9001 | MQTT WebSocket 端口 |
| Dapr Placement | 6050 | Dapr Actor 模式服务 |
| Dapr Scheduler | 6060 | Dapr 定时任务服务 |
| Tailscale | 无固定端口 | VPN 服务（使用 host 网络模式，自动管理 UDP 端口） |

### 应用服务（可配置）

这些服务的端口可以通过环境变量自定义：

| 服务 | 默认端口 | 环境变量 | 说明 |
|------|----------|----------|------|
| Backend API | **18888** | `APP_PORT` 或 `SERVER_PORT` | FastAPI 后端服务 |
| Dapr HTTP | **13500** | `DAPR_HTTP_PORT` | Dapr HTTP API |
| Dapr gRPC | **15001** | `DAPR_GRPC_PORT` | Dapr gRPC API |

**注意**: 默认端口已从常用端口（8000, 3500, 50001）改为非常用端口，以减少冲突。

## 端口配置方法

### 方法 1: 使用 .env 文件（推荐）

编辑项目根目录的 `.env` 文件：

```bash
# 应用服务端口配置
SERVER_HOST=0.0.0.0
SERVER_PORT=18888
APP_PORT=18888

# Dapr 端口配置
DAPR_HTTP_PORT=13500
DAPR_GRPC_PORT=15001
```

启动脚本会自动加载 `.env` 文件中的配置。

### 方法 2: 环境变量

在启动前设置环境变量：

**Windows PowerShell:**
```powershell
$env:APP_PORT = "18888"
$env:DAPR_HTTP_PORT = "13500"
$env:DAPR_GRPC_PORT = "15001"
.\scripts\start_backend_dev.ps1
```

**Linux/macOS:**
```bash
export APP_PORT=18888
export DAPR_HTTP_PORT=13500
export DAPR_GRPC_PORT=15001
./scripts/start_backend.sh
```

### 方法 3: 命令行参数

某些启动脚本支持直接传递端口参数，但推荐使用环境变量方式。

## 端口冲突排查

### 检查端口占用

**Windows PowerShell:**
```powershell
# 检查特定端口
Get-NetTCPConnection -LocalPort 18888 -ErrorAction SilentlyContinue

# 检查所有 Jachin 相关端口
$ports = @(18888, 13500, 15001, 6379, 1883, 6050, 6060)
foreach ($port in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Host "Port $port is in use" -ForegroundColor Yellow
    } else {
        Write-Host "Port $port is available" -ForegroundColor Green
    }
}
```

**Linux/macOS:**
```bash
# 检查特定端口
lsof -i :18888

# 或使用 netstat
netstat -an | grep 18888
```

### 使用检查脚本

项目提供了自动检查脚本：

```powershell
# Windows
.\scripts\docker_diagnose.ps1
```

## 自定义端口示例

### 示例 1: 修改后端 API 端口

如果端口 18888 被占用，可以修改为其他端口：

**.env 文件:**
```bash
APP_PORT=19999
SERVER_PORT=19999
```

### 示例 2: 修改 Dapr 端口

如果 Dapr 端口冲突，可以修改：

**.env 文件:**
```bash
DAPR_HTTP_PORT=13600
DAPR_GRPC_PORT=15101
```

**注意**: 如果修改了 Dapr 端口，确保所有使用 Dapr 的客户端也使用相同的端口配置。

### 示例 3: 多实例运行

如果需要同时运行多个实例，可以为每个实例配置不同的端口：

**实例 1 (.env):**
```bash
APP_PORT=18888
DAPR_HTTP_PORT=13500
DAPR_GRPC_PORT=15001
```

**实例 2 (.env.instance2):**
```bash
APP_PORT=18889
DAPR_HTTP_PORT=13501
DAPR_GRPC_PORT=15002
```

然后使用不同的环境文件启动：
```powershell
# 实例 1
Get-Content .env | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process") } }
.\scripts\start_backend_dev.ps1

# 实例 2（新终端）
Get-Content .env.instance2 | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process") } }
.\scripts\start_backend_dev.ps1
```

## 端口范围建议

### 推荐端口范围

- **应用服务**: 18000-19999（避免与常用服务冲突）
- **Dapr HTTP**: 13000-13999
- **Dapr gRPC**: 15000-15999

### 避免使用的端口

以下端口范围通常被系统服务占用，不建议使用：

- **0-1023**: 系统保留端口（需要 root 权限）
- **1024-49151**: 注册端口（可能被其他应用占用）
- **常用端口**: 80, 443, 3306, 5432, 6379, 8080, 8000, 9000 等

## 常见问题

### Q: 为什么默认端口从 8000 改为 18888？

**A**: 端口 8000 是常用的开发服务器端口，容易与其他应用冲突。使用 18888 可以显著减少冲突概率。

### Q: 修改端口后，API 文档地址会变化吗？

**A**: 是的。如果修改了 `APP_PORT`，API 文档地址也会相应变化：
- 默认: `http://localhost:18888/docs`
- 自定义: `http://localhost:<你的端口>/docs`

### Q: Docker 容器中的端口需要修改吗？

**A**: 通常不需要。Docker 容器使用标准端口（如 Redis 6379），这些端口在容器内部使用，不会与宿主机冲突。如果需要修改，请编辑 `docker-compose.dev.yml`。

### Q: 如何验证端口配置是否生效？

**A**: 
1. 启动服务后，检查日志中的端口信息
2. 访问健康检查端点: `http://localhost:<端口>/health`
3. 使用检查脚本: `.\scripts\docker_diagnose.ps1`

## 相关文档

- [快速开始指南](QUICKSTART.md)
- [开发模式说明](DEVELOPMENT_MODE.md)
- [本地数据库配置](LOCAL_DATABASE_SETUP.md)
