# 设备注册验证指南

## ✅ 模拟设备运行成功！

从输出可以看到：
- ✅ Device ID: `raspi-living-room-001`
- ✅ Location: `living_room`
- ✅ Capabilities: `camera.capture`, `light.control`
- ✅ 消息已发送到 `system/announce` 主题（929 bytes）

## 验证设备注册

### 方法 1: 使用验证脚本（推荐）

```powershell
# 在项目根目录
cd e:\jachin-system
python clients/iot/mock_device/verify_registration.py
```

### 方法 2: 通过 REST API 验证

确保后端正在运行，然后：

```powershell
# 查询所有设备
curl http://localhost:8000/api/v2/devices

# 查询特定设备
curl http://localhost:8000/api/v2/devices/raspi-living-room-001

# 查询设备能力
curl http://localhost:8000/api/v2/devices/raspi-living-room-001/capabilities
```

### 方法 3: 检查后端日志

如果后端正在运行并监听 `system/announce` 主题，应该看到：
```
✅ Registered device: raspi-living-room-001 at living_room with 2 capabilities
```

## 下一步：让后端监听 system/announce

要让后端接收设备广播，需要：

1. **确保后端正在运行**
   ```powershell
   dapr run --app-id jachin-brain --app-port 8000 --dapr-http-port 3500 `
     --resources-path ./dapr/components --config ./dapr/config/config.yaml `
     -- python backend/main.py
   ```

2. **后端需要订阅 system/announce 主题**

   在 `backend/main.py` 或相关路由文件中添加订阅逻辑，监听 `system/announce` 并调用 `DeviceRegistry.register_device()`。

## 测试流程

1. ✅ **模拟设备已运行** - 设备能力已广播
2. ⏳ **后端监听** - 需要后端订阅 `system/announce` 主题
3. ⏳ **验证注册** - 使用验证脚本或 API 查询

## 常见问题

### Q: 为什么设备没有注册？

**A**: 可能的原因：
1. 后端未运行
2. 后端未订阅 `system/announce` 主题
3. DeviceRegistry 未初始化
4. Redis 未运行或连接失败

### Q: 如何让后端接收设备广播？

**A**: 需要在后端代码中添加订阅逻辑，参考 `.cursor/rules/010-protocol-registry.mdc`：
- DeviceRegistry 监听 `system/announce` Topic
- 将设备信息存入 Dapr State Store (Redis)
