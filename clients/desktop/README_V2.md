# 桌面精灵 v2.0 架构使用指南

## 🎉 新功能

桌面精灵现在按照 v2.0 架构实现，可以作为"设备"注册到系统中！

### 核心特性

1. **自动设备注册** - 启动时自动广播能力
2. **心跳机制** - 每 10 秒发送心跳，保持在线状态
3. **命令接收** - 通过 Dapr Pub/Sub 接收来自大脑的指令
4. **能力执行** - 支持 4 种设备能力

## 🚀 快速开始

### 1. 安装依赖

```bash
cd clients/desktop
npm install
```

### 2. 启动桌面精灵（使用 Dapr）

```powershell
# 方式 1: 使用启动脚本（推荐）
.\run_with_dapr.ps1

# 方式 2: 手动启动
dapr run `
  --app-id desktop-client `
  --app-port 8002 `
  --dapr-http-port 3502 `
  --dapr-grpc-port 50003 `
  --resources-path ../../dapr/components `
  --config ../../dapr/config/config.yaml `
  -- npm run tauri:dev
```

### 3. 验证设备注册

应用启动后，等待几秒，然后查询：

```powershell
Invoke-RestMethod -Uri http://localhost:8000/api/v2/devices
```

应该能看到 `desktop-{hostname}` 设备，状态为 `online: true`。

## 📋 设备能力

桌面客户端注册了以下能力：

### 1. notification.show - 显示通知

```json
{
  "target_device_id": "desktop-LAPTOP-XXX",
  "capability_name": "notification.show",
  "params": {
    "title": "通知标题",
    "message": "通知内容"
  }
}
```

### 2. window.show - 显示窗口

```json
{
  "target_device_id": "desktop-LAPTOP-XXX",
  "capability_name": "window.show",
  "params": {
    "window_name": "chat"  // 或 "sprite"
  }
}
```

### 3. window.hide - 隐藏窗口

```json
{
  "target_device_id": "desktop-LAPTOP-XXX",
  "capability_name": "window.hide",
  "params": {
    "window_name": "chat"  // 或 "sprite"
  }
}
```

### 4. sprite.set_state - 设置精灵状态

```json
{
  "target_device_id": "desktop-LAPTOP-XXX",
  "capability_name": "sprite.set_state",
  "params": {
    "state": "thinking"  // "idle" | "listening" | "thinking" | "speaking"
  }
}
```

## 🔧 架构说明

### 设备注册流程

```
1. 应用启动
   ↓
2. 启动 Pub/Sub HTTP 服务器（端口 8002）
   ↓
3. 启动 Dapr sidecar（端口 3502）
   ↓
4. Dapr sidecar 调用 /dapr/subscribe 发现订阅
   ↓
5. 发送设备广播到 system/announce
   ↓
6. 后端注册设备
   ↓
7. 启动心跳循环（每 10 秒）
```

### 命令执行流程

```
1. LLM Agent 或后端生成 DeviceCommand
   ↓
2. 发布到 device/{device_id}/command 主题
   ↓
3. Dapr sidecar 推送到桌面客户端
   ↓
4. Pub/Sub 服务器接收命令
   ↓
5. 通过 Tauri 事件发送到前端
   ↓
6. 执行相应操作
   ↓
7. 发送 DeviceResponse 到 device/{device_id}/response
```

## 📝 配置说明

### 端口配置

- **应用端口**: 8002（Pub/Sub HTTP 服务器）
- **Dapr HTTP 端口**: 3502（桌面客户端的 Dapr sidecar）
- **Dapr gRPC 端口**: 50003（桌面客户端的 Dapr sidecar）

### 环境变量

可以通过环境变量配置：

```powershell
$env:DAPR_HTTP_PORT = "3502"
$env:DAPR_GRPC_PORT = "50003"
```

## 🐛 故障排查

### 问题：设备未注册

**检查**:
1. Dapr sidecar 是否启动？
2. Pub/Sub 服务器是否启动（端口 8002）？
3. 后端是否运行（端口 8000）？
4. Redis 是否运行？

**调试**:
```powershell
# 检查端口
netstat -an | findstr "8002"
netstat -an | findstr "3502"

# 检查设备注册
Invoke-RestMethod -Uri http://localhost:8000/api/v2/devices
```

### 问题：命令未接收

**检查**:
1. `/dapr/subscribe` 端点是否返回订阅列表？
2. Pub/Sub 服务器日志是否有错误？
3. Dapr sidecar 日志是否有错误？

**调试**:
```powershell
# 检查订阅端点
Invoke-RestMethod -Uri http://localhost:8002/dapr/subscribe

# 查看 Dapr 日志
dapr logs --app-id desktop-client
```

## 📚 相关文档

- [实现状态文档](./IMPLEMENTATION_STATUS.md)
- [完成报告](./V2_IMPLEMENTATION_COMPLETE.md)
- [差距分析](./GAP_ANALYSIS.md)
