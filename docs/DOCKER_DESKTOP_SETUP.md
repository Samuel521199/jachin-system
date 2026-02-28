# Docker Desktop 和项目中间件安装指南

## 📋 概述

本指南说明如何：
1. 安装和配置 WSL 2（Docker Desktop 的后端）
2. 安装 Docker Desktop
3. 启动项目所需的所有中间件

## 🔧 第一部分：WSL 2 安装

### 检查 WSL 状态

```powershell
# 检查 WSL 版本
wsl --version

# 检查已安装的 WSL 分布
wsl --list --all --verbose
```

### 安装 WSL 2（如果未安装）

```powershell
# 以管理员身份运行 PowerShell

# 1. 启用 WSL 功能
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart

# 2. 启用虚拟机平台
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

# 3. 重启电脑（必须！）
Restart-Computer

# 4. 重启后，设置 WSL 2 为默认版本
wsl --set-default-version 2

# 5. 安装 WSL 2 内核更新（如果需要）
# 下载：https://aka.ms/wsl2kernel
```

### 验证 WSL 2

```powershell
wsl --version
# 应该显示 WSL 版本 2.x.x
```

## 🐳 第二部分：Docker Desktop 安装

### 1. 下载 Docker Desktop

访问：https://www.docker.com/products/docker-desktop

下载 Windows 版本（Docker Desktop for Windows）

### 2. 安装 Docker Desktop

1. 运行安装程序
2. 按照提示完成安装
3. 安装完成后重启电脑（推荐）

### 3. 配置 Docker Desktop

#### 3.1 设置 WSL 2 后端

1. 启动 Docker Desktop
2. **Settings** → **General**
3. 确保 **"Use the WSL 2 based engine"** 已启用
4. 点击 **"Apply & restart"**

#### 3.2 设置磁盘位置到 E 盘（推荐）

**重要**：在首次启动 Docker Desktop 后，立即设置磁盘位置，避免数据存储在 C 盘。

1. **Settings** → **Resources** → **Advanced**
2. **Disk image location**: 设置为 `E:\DockerDesktopWSL`（或 `E:\docker\wsl`）
3. 点击 **"Apply & restart"**
4. Docker Desktop 会自动将 VHDX 文件创建在 E 盘指定位置

**注意**：
- 如果 Docker Desktop 已经创建了数据，需要先卸载并重新安装
- 重装后，在首次启动时立即设置磁盘位置
- 这样 Docker Desktop 会直接在 E 盘创建 VHDX 文件，无需迁移

### 4. 验证 Docker Desktop

```powershell
# 检查 Docker 版本
docker --version

# 检查 Docker 是否运行
docker ps

# 检查 Docker Compose
docker-compose --version
```

## 🚀 第三部分：启动项目中间件

### 项目需要的中间件

根据 `docker-compose.yml`，项目需要以下服务：

1. **PostgreSQL** (端口 5432) - 数据库
2. **Qdrant** (端口 6333/6334) - 向量数据库
3. **Redis** (端口 6379) - 缓存和消息队列
4. **MQTT Broker** (端口 1883) - IoT 通信
5. **Dapr Placement** (端口 50005) - Dapr Actor 模式
6. **Zipkin** (端口 9412) - 分布式追踪
7. **Ray Head** (端口 10001, Dashboard 8265) - 分布式计算

### 方法 1: 使用 Docker Compose（推荐）

```powershell
# 1. 确保 Docker Desktop 正在运行
docker ps

# 2. 创建 E 盘 volumes 目录（如果不存在）
.\scripts\setup_e_drive_volumes.ps1

# 3. 启动所有中间件
cd e:\jachin-system
docker-compose up -d

# 4. 查看服务状态
docker-compose ps

# 5. 查看日志（可选）
docker-compose logs -f
```

### 方法 2: 使用启动脚本

```powershell
# 使用项目提供的启动脚本
.\scripts\start.ps1

# 或使用一键启动脚本
.\start.bat
```

### 验证中间件运行状态

```powershell
# 检查所有容器
docker ps

# 检查特定服务
docker ps | findstr postgres
docker ps | findstr redis
docker ps | findstr qdrant
docker ps | findstr mqtt

# 测试连接
# PostgreSQL
docker exec jachin-postgres pg_isready -U jachin

# Redis
docker exec jachin-redis redis-cli ping

# Qdrant
curl http://localhost:6333/health

# MQTT
docker exec jachin-mqtt mosquitto_sub -h localhost -t '$SYS/broker/uptime' -C 1
```

## 📦 第四部分：初始化数据库

```powershell
# 运行数据库初始化脚本
.\installer\init_database.ps1

# 这会：
# 1. 检查 Alembic 是否安装
# 2. 运行数据库迁移
# 3. 创建所有必要的表
```

## 🔍 故障排查

### 问题 1: WSL 2 未安装

**症状**: Docker Desktop 无法启动或显示 WSL 错误

**解决**:
```powershell
# 安装 WSL 2（见第一部分）
wsl --install

# 或手动安装
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

### 问题 2: Docker Compose 服务启动失败

**症状**: `docker-compose up -d` 失败

**检查**:
```powershell
# 1. 检查 Docker Desktop 是否运行
docker ps

# 2. 检查端口是否被占用
netstat -ano | findstr ":5432"
netstat -ano | findstr ":6379"

# 3. 检查 volumes 目录是否存在
Test-Path "E:\docker\volumes\postgres_data"
Test-Path "E:\docker\volumes\redis_data"

# 4. 查看详细错误
docker-compose up
```

### 问题 3: 数据库连接失败

**症状**: 后端无法连接到 PostgreSQL

**解决**:
```powershell
# 1. 检查 PostgreSQL 容器是否运行
docker ps | findstr postgres

# 2. 检查数据库是否创建
docker exec jachin-postgres psql -U jachin -l

# 3. 手动创建数据库（如果需要）
docker exec jachin-postgres psql -U jachin -c "CREATE DATABASE jachin_brain;"
```

## 📝 快速参考

### 完整启动流程

```powershell
# 1. 确保 Docker Desktop 运行
docker ps

# 2. 创建 volumes 目录
.\scripts\setup_e_drive_volumes.ps1

# 3. 启动所有中间件
docker-compose up -d

# 4. 等待服务启动（30-60 秒）
Start-Sleep -Seconds 30

# 5. 验证服务
docker-compose ps

# 6. 初始化数据库
.\installer\init_database.ps1

# 7. 启动后端服务
.\scripts\start.ps1
```

### 停止所有服务

```powershell
# 停止所有容器
docker-compose down

# 停止并删除 volumes（谨慎！）
docker-compose down -v
```

### 查看服务日志

```powershell
# 所有服务日志
docker-compose logs -f

# 特定服务日志
docker-compose logs -f postgres
docker-compose logs -f redis
docker-compose logs -f qdrant
```

## 🎯 总结

1. **WSL 2**: 通过 Windows 功能启用（通常已安装）
2. **Docker Desktop**: 下载并安装，配置使用 WSL 2 和 E 盘
3. **中间件**: 使用 `docker-compose up -d` 一键启动
4. **数据库**: 运行 `init_database.ps1` 初始化

所有中间件都会自动下载镜像并启动，无需手动安装！

---

**最后更新**: 2026-02-05
