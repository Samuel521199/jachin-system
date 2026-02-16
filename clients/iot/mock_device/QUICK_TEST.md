# Mock Device 快速测试指南

## Windows 用户

### 方式 1: 使用 run.bat（推荐）

```powershell
cd clients\iot\mock_device
.\run.bat
```

### 方式 2: 手动运行 Dapr

```powershell
# 在项目根目录
cd e:\jachin-system

# 使用 Dapr Run
dapr run `
  --app-id mock-iot-device `
  --app-port 8001 `
  --dapr-http-port 3501 `
  --dapr-grpc-port 50002 `
  --resources-path ./dapr/components `
  --config ./dapr/config/config.yaml `
  -- python clients/iot/mock_device/main.py
```

## Linux/macOS 用户

```bash
cd clients/iot/mock_device
./run.sh
```

## 预期输出

成功运行后应该看到：

```
============================================================
Mock IoT Device - Capability Discovery Test
============================================================

Device: Raspberry Pi (living_room)
Capabilities: camera.capture, light.control

📢 Broadcasting device capabilities to system/announce...
   Device ID: raspi-living-room-001
   Location: living_room
   Capabilities: ['camera.capture', 'light.control']
✅ Device announcement sent successfully!
   Topic: system/announce
   PubSub: pubsub
   Data size: XXX bytes

============================================================
✅ Capability discovery test completed successfully!
============================================================
```

## 验证设备注册

### 方法 1: 检查后端日志

如果后端正在运行并监听 `system/announce`，应该看到：
```
✅ Registered device: raspi-living-room-001 at living_room with 2 capabilities
```

### 方法 2: 查询 DeviceRegistry API

```bash
# 查询所有设备
curl http://localhost:8000/api/v2/devices

# 查询特定设备
curl http://localhost:8000/api/v2/devices/raspi-living-room-001

# 查询设备能力
curl http://localhost:8000/api/v2/devices/raspi-living-room-001/capabilities
```

## 故障排查

### 错误: "Dapr client not available"

**原因**: Dapr Sidecar 未启动

**解决**: 确保使用 `dapr run` 启动，不要直接运行 `python main.py`

### 错误: "Failed to publish to topic"

**原因**: PubSub 组件未配置或 Redis 未运行

**解决**:
1. 检查 `dapr/components/pubsub-redis.yaml` 是否存在
2. 确保 Redis 服务运行：`docker ps | grep redis`
3. 启动基础设施：`docker-compose -f docker-compose.dev.yml up -d`

### 警告: "PydanticDeprecatedSince20"

**已修复**: 代码已更新为使用 `model_dump()`（Pydantic V2 兼容）
