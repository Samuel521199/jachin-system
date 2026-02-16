# 桌面精灵 v2.0 架构实现指南

## 概述

按照 Jachin-System v2.0 架构，桌面精灵现在作为"设备"注册到系统中，具备以下能力：
- 显示通知
- 控制窗口显示/隐藏
- 设置精灵动画状态

## 已实现功能

### 1. 设备注册模块 (`device_registry.rs`)

- ✅ 设备广播 (`DeviceAnnounce`)
- ✅ 心跳机制 (`DeviceHeartbeat`)
- ✅ 命令接收 (`DeviceCommand`)
- ✅ 响应发送 (`DeviceResponse`)

### 2. 设备能力定义

桌面客户端注册以下能力：

1. **notification.show** - 显示系统通知
2. **window.show** - 显示指定窗口
3. **window.hide** - 隐藏指定窗口
4. **sprite.set_state** - 设置精灵动画状态

### 3. 自动注册

应用启动时自动：
- 发送设备广播到 `system/announce`
- 启动心跳循环（每 10 秒）
- 监听设备指令（通过 Tauri 命令）

## 使用方式

### 启动桌面客户端

```bash
cd clients/desktop
npm run tauri:dev
```

### 验证设备注册

```powershell
# 查询所有设备
Invoke-RestMethod -Uri http://localhost:8000/api/v2/devices

# 应该看到 desktop-{hostname} 设备
```

### 测试设备能力

通过后端 API 或 LLM Agent 可以调用桌面客户端的能力：

```json
{
  "target_device_id": "desktop-LAPTOP-XXX",
  "capability_name": "notification.show",
  "params": {
    "title": "测试通知",
    "message": "这是一条测试消息"
  }
}
```

## 架构说明

### 设备注册流程

```
桌面客户端启动
  ↓
发送 DeviceAnnounce 到 system/announce
  ↓
后端 DeviceRegistry 接收并注册
  ↓
启动心跳循环（每 10 秒发送 system/heartbeat）
  ↓
设备状态保持为 online
```

### 命令执行流程

```
LLM Agent 决策
  ↓
生成 DeviceCommand
  ↓
发布到 device/{device_id}/command
  ↓
桌面客户端接收（通过 Tauri 命令）
  ↓
执行相应操作
  ↓
发送 DeviceResponse 到 device/{device_id}/response
```

## 待实现功能

- [ ] Dapr Pub/Sub 订阅（当前通过 Tauri 命令模拟）
- [ ] 完整的通知系统集成
- [ ] 精灵状态同步到前端（Rive 动画）
- [ ] 设备注销机制

## 注意事项

1. **Dapr Sidecar**: 桌面客户端需要连接到后端的 Dapr sidecar（端口 3500），或者运行自己的 sidecar
2. **设备ID**: 使用 `desktop-{hostname}` 格式，确保唯一性
3. **心跳间隔**: 当前设置为 10 秒，可根据需要调整

## 下一步

1. 实现 Dapr Pub/Sub 订阅机制（监听 `device/{device_id}/command`）
2. 集成 Tauri 通知 API
3. 实现前端状态同步（通过 Tauri 事件）
4. 添加设备注销功能
