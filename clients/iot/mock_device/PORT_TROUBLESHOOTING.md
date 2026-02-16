# 端口冲突故障排查指南

## 问题：端口 3501 被占用

当看到错误 `invalid configuration for HTTPPort. Port 3501 is not available` 时，说明端口 3501 已被占用。

## 🔧 快速修复

### 方法 1: 自动清理（推荐）

启动脚本现在会自动检测并停止旧的 mock-iot-device 实例：

```powershell
.\run_with_heartbeat.ps1
```

如果自动清理失败，使用手动清理：

### 方法 2: 手动停止 Dapr 应用

```powershell
# 停止 mock-iot-device
dapr stop --app-id mock-iot-device

# 等待几秒后重试
.\run_with_heartbeat.ps1
```

### 方法 3: 使用清理脚本

```powershell
# 检查端口占用情况
.\check_port.ps1

# 或清理端口
.\cleanup_port.ps1
```

### 方法 4: 手动查找并终止进程

```powershell
# 1. 查找占用端口的进程
netstat -ano | findstr ":3501"

# 2. 查看进程详情（替换 <PID> 为实际 PID）
tasklist | findstr <PID>

# 3. 终止进程（谨慎使用）
taskkill /PID <PID> /F
```

## 📋 端口使用说明

### Mock IoT Device 使用的端口

- **Dapr HTTP 端口**: 3501
- **Dapr gRPC 端口**: 50002
- **应用端口**: 8001

### 系统端口分配

| 应用 | Dapr HTTP | Dapr gRPC | App Port |
|------|-----------|-----------|----------|
| jachin-brain (后端) | 3500 | 50001 | 8000 |
| mock-iot-device | 3501 | 50002 | 8001 |
| desktop-client | 3502 | 50003 | 8002 |

## 🐛 常见问题

### Q: 为什么端口会被占用？

**A:** 可能的原因：
1. 之前的 mock_device 实例没有正确关闭
2. Dapr sidecar 进程仍在运行
3. 其他应用占用了该端口

### Q: 如何防止端口冲突？

**A:** 
1. 启动前总是先停止旧实例：`dapr stop --app-id mock-iot-device`
2. 使用启动脚本（已包含自动清理）
3. 检查 `dapr list` 查看运行中的应用

### Q: 可以修改端口吗？

**A:** 可以，但需要修改多个地方：
1. `run_with_heartbeat.ps1` 中的 `--dapr-http-port` 参数
2. 确保新端口未被占用
3. 确保不与系统其他服务冲突

## 📝 检查清单

启动前检查：

- [ ] 运行 `dapr list` 确认没有旧的 mock-iot-device 实例
- [ ] 运行 `.\check_port.ps1` 检查端口占用
- [ ] 如有占用，运行 `.\cleanup_port.ps1` 清理
- [ ] 确认后端服务（jachin-brain）正常运行在端口 3500

## 🔍 诊断命令

```powershell
# 查看所有 Dapr 应用
dapr list

# 查看端口占用
netstat -ano | findstr ":3501"

# 检查特定进程
Get-Process -Id <PID>

# 查看 Dapr 日志
dapr logs --app-id mock-iot-device
```

## ✅ 验证修复

修复后，重新启动应该看到：

```
Starting Mock IoT Device with Dapr and Heartbeat...
[OK] Stopped existing application
time="..." level=info msg="HTTP server is running on port 3501"
```

如果仍然失败，请检查：
1. 是否有其他 Dapr 应用使用相同端口
2. 防火墙是否阻止端口使用
3. 是否有权限问题
