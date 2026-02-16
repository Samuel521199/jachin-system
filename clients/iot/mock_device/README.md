# Mock IoT Device - 能力发现测试

## 概述

这是一个模拟的树莓派节点，用于测试 Jachin-System v2.0 的"能力发现"流程。

## 功能

- 模拟位于 `living_room` 的树莓派节点
- 广播两个能力：
  - `camera.capture` - 摄像头拍照
  - `light.control` - 灯光控制
- 通过 Dapr Pub/Sub 发送到 `system/announce` 主题

## 运行方式

### 方式 1: 使用 Dapr Run（推荐）

```bash
# 在项目根目录执行
dapr run \
  --app-id mock-iot-device \
  --app-port 8001 \
  --dapr-http-port 3501 \
  --dapr-grpc-port 50002 \
  --resources-path ./dapr/components \
  --config ./dapr/config/config.yaml \
  -- python clients/iot/mock_device/main.py
```

### 方式 2: 直接运行（需要 Dapr Sidecar 已启动）

```bash
# 确保 Dapr Sidecar 已启动
# 然后直接运行
cd clients/iot/mock_device
python main.py
```

## 前置条件

1. **Dapr 已安装并初始化**
   ```bash
   dapr --version
   dapr init -s
   ```

2. **Python 环境已激活**
   ```bash
   conda activate jachin-dev
   ```

3. **依赖已安装**
   ```bash
   pip install dapr-ext-grpc pydantic
   ```

4. **Dapr 组件已配置**
   - `dapr/components/pubsub-redis.yaml` 必须存在
   - Redis 服务必须运行

5. **基础设施服务已启动**
   ```bash
   docker-compose -f docker-compose.dev.yml up -d
   ```

## 预期输出

```
============================================================
Mock IoT Device - Capability Discovery Test
============================================================

Device: Raspberry Pi (living_room)
Capabilities: camera.capture, light.control

2026-02-01 10:00:00 - __main__ - INFO - 📢 Broadcasting device capabilities to system/announce...
2026-02-01 10:00:00 - __main__ - INFO -    Device ID: raspi-living-room-001
2026-02-01 10:00:00 - __main__ - INFO -    Location: living_room
2026-02-01 10:00:00 - __main__ - INFO -    Capabilities: ['camera.capture', 'light.control']
2026-02-01 10:00:00 - __main__ - INFO - ✅ Device announcement sent successfully!
2026-02-01 10:00:00 - __main__ - INFO -    Topic: system/announce
2026-02-01 10:00:00 - __main__ - INFO -    PubSub: pubsub

============================================================
✅ Capability discovery test completed successfully!
============================================================

Next steps:
1. Check backend logs to see if device was registered
2. Query DeviceRegistry to verify device registration
3. Test get_tools() to see if capabilities are available
```

## 验证设备注册

### 方法 1: 检查后端日志

查看后端服务的日志，应该看到：
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

### 方法 3: 测试 get_tools()

在后端代码中调用：
```python
from backend.core.registry import DeviceRegistry

registry = DeviceRegistry()
tools = await registry.get_tools()
print(f"Available tools: {len(tools)}")
# 应该看到 raspi-living-room-001.camera.capture 和 raspi-living-room-001.light.control
```

## 故障排查

### 错误: "Dapr client not available"

**原因**: Dapr Sidecar 未启动或端口不正确

**解决**:
1. 确保使用 `dapr run` 启动
2. 检查 Dapr Sidecar 是否运行：`dapr list`
3. 检查端口是否被占用

### 错误: "Failed to publish to topic"

**原因**: PubSub 组件未配置或 Redis 未运行

**解决**:
1. 检查 `dapr/components/pubsub-redis.yaml` 是否存在
2. 确保 Redis 服务运行：`docker ps | grep redis`
3. 检查 Dapr 组件状态：`dapr components list`

### 错误: "Import error: backend.core.protocol"

**原因**: Python 路径不正确

**解决**:
1. 确保在项目根目录运行
2. 检查 `backend/core/protocol.py` 是否存在
3. 确保 Python 环境已激活

## 下一步

成功运行后，可以：
1. 修改能力列表，测试不同的设备能力
2. 添加心跳机制，测试设备在线状态
3. 测试设备指令接收和执行
