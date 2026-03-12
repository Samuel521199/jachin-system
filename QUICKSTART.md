# 🚀 快速开始

## 首次设置（只需一次）

### Windows

```powershell
# 1. 设置开发环境
.\scripts\setup.ps1

# 2. 配置 API Key（编辑 .env 文件）
# 设置 QWEN_API_KEY=你的API密钥
```

### Linux/macOS

```bash
# 1. 设置开发环境
chmod +x scripts/*.sh
./scripts/setup.sh

# 2. 配置 API Key（编辑 .env 文件）
# 设置 QWEN_API_KEY=你的API密钥
```

## 日常使用

### 启动服务

**方式 1: 使用批处理文件（推荐，最简单）**

```powershell
# 直接运行批处理文件
.\start.bat
```

**方式 2: 使用 PowerShell 脚本**

```powershell
# 使用 & 操作符（避免打开记事本）
& .\scripts\start.ps1

# 或使用完整 PowerShell 命令
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

```bash
# Linux/macOS（自动激活环境）
./scripts/start.sh
```

### 停止服务

```powershell
# 使用批处理文件（推荐）
.\stop.bat

# 或使用 PowerShell
& .\scripts\stop.ps1
```

```bash
# Linux/macOS
./scripts/stop.sh
```

### 重启服务

```powershell
# 使用批处理文件（推荐）
.\restart.bat

# 或使用 PowerShell
& .\scripts\restart.ps1
```

```bash
# Linux/macOS（自动激活环境）
./scripts/restart.sh
```

### 测试 API

```powershell
# 使用批处理文件（推荐）
.\test.bat

# 或使用 PowerShell
& .\scripts\test.ps1
```

```bash
# Linux/macOS（在另一个终端）
./scripts/test.sh
```

## 核心脚本说明

| 脚本 | 功能 |
|------|------|
| `setup` | 初始设置（Conda、依赖、Dapr） |
| `start` | 启动所有服务 |
| `stop` | 停止所有服务 |
| `restart` | 重启所有服务 |
| `test` | 测试 API |

## 注意事项

- **脚本会自动激活 jachin-dev 环境**，无需手动激活
- 确保 Docker Desktop/Docker 正在运行
- 确保已配置 API Key（`.env` 文件）

## 访问服务

启动后可以访问：

- API 文档: http://localhost:18888/docs
- 健康检查: http://localhost:18888/health
- 聊天接口: http://localhost:18888/api/chat

**注意**: 默认端口为 **18888**（可在 `.env` 文件中通过 `SERVER_PORT` 或 `APP_PORT` 自定义）。如果修改了端口，请相应调整上述 URL。
