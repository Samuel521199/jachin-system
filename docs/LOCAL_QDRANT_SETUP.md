# 本地 Qdrant 服务设置指南

## 概述

开发环境使用本地 Qdrant 服务（而非 Docker 容器），默认连接地址：
- REST API: `http://localhost:6333`
- gRPC API: `http://localhost:6334`

## 安装方式

### 方式 1: 使用 Docker（推荐）

最简单的方式是使用 Docker 运行 Qdrant：

```bash
docker run -d \
  --name qdrant-local \
  -p 6333:6333 \
  -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant:latest
```

Windows PowerShell:
```powershell
docker run -d `
  --name qdrant-local `
  -p 6333:6333 `
  -p 6334:6334 `
  -v ${PWD}/qdrant_storage:/qdrant/storage `
  qdrant/qdrant:latest
```

### 方式 2: 使用 Docker Compose

在项目根目录创建 `docker-compose.qdrant.yml`:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    container_name: qdrant-local
    ports:
      - "6333:6333"  # REST API
      - "6334:6334"  # gRPC API
    volumes:
      - ./qdrant_storage:/qdrant/storage
    restart: unless-stopped
```

启动：
```bash
docker-compose -f docker-compose.qdrant.yml up -d
```

### 方式 3: 本地二进制安装

1. 下载 Qdrant 二进制文件：
   - Windows: 从 [Qdrant Releases](https://github.com/qdrant/qdrant/releases) 下载 Windows 版本
   - 或使用包管理器（如 Chocolatey）

2. 运行 Qdrant：
   ```bash
   qdrant
   ```

## 验证服务运行

### 检查服务状态

```bash
# 检查端口是否监听
netstat -ano | findstr :6333

# 或使用 curl 检查健康状态
curl http://localhost:6333/health
```

### 访问 Web UI

打开浏览器访问：`http://localhost:6333/dashboard`

## 配置

应用配置在 `core/config/__init__.py` 中：

```python
QDRANT_URL: str = "http://localhost:6333"
QDRANT_GRPC_URL: str = "http://localhost:6334"
```

可以通过环境变量覆盖：

```bash
# Windows PowerShell
$env:QDRANT_URL="http://localhost:6333"
$env:QDRANT_GRPC_URL="http://localhost:6334"
```

## 故障排除

### 问题：连接失败 (503 Service Unavailable)

**原因**：Qdrant 服务未运行

**解决方案**：
1. 检查 Qdrant 是否运行：
   ```bash
   docker ps | findstr qdrant
   # 或
   netstat -ano | findstr :6333
   ```

2. 如果未运行，启动服务：
   ```bash
   docker start qdrant-local
   # 或使用上面的启动命令
   ```

3. 检查日志：
   ```bash
   docker logs qdrant-local
   ```

### 问题：端口被占用

**解决方案**：
1. 查找占用端口的进程：
   ```bash
   netstat -ano | findstr :6333
   ```

2. 停止占用进程或更改 Qdrant 端口

### 问题：VectorStore 初始化失败

**说明**：应用已配置为优雅降级，即使 Qdrant 不可用也能启动，但向量存储功能将不可用。

**解决方案**：确保 Qdrant 服务正在运行（见上方）

## 数据持久化

如果使用 Docker，数据会保存在 `./qdrant_storage` 目录中。

停止服务：
```bash
docker stop qdrant-local
```

启动服务（数据会保留）：
```bash
docker start qdrant-local
```

## 开发建议

1. **启动脚本**：可以创建一个启动脚本，在启动后端前检查并启动 Qdrant
2. **健康检查**：应用启动时会尝试连接 Qdrant，如果失败会记录警告但不会崩溃
3. **功能降级**：如果 Qdrant 不可用，向量存储相关功能会抛出 `RuntimeError`，提示需要启动服务

## 快速启动脚本

创建 `scripts/start_qdrant.ps1`:

```powershell
# 检查 Qdrant 是否运行
$qdrantRunning = docker ps --filter "name=qdrant-local" --format "{{.Names}}" | Select-String "qdrant-local"

if (-not $qdrantRunning) {
    Write-Host "Starting Qdrant..." -ForegroundColor Yellow
    docker run -d `
      --name qdrant-local `
      -p 6333:6333 `
      -p 6334:6334 `
      -v ${PWD}/qdrant_storage:/qdrant/storage `
      qdrant/qdrant:latest
    Write-Host "Qdrant started" -ForegroundColor Green
} else {
    Write-Host "Qdrant is already running" -ForegroundColor Green
}

# 等待服务就绪
Start-Sleep -Seconds 2
Write-Host "Qdrant is ready at http://localhost:6333" -ForegroundColor Cyan
```
