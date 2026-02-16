# Windows 运行指南

## ⚠️ 重要提示

在 Windows PowerShell 中，应该使用 `run.bat` 而不是 `run.sh`。

`run.sh` 是 Linux/macOS 的脚本，在 Windows 上无法直接运行。

## 正确的运行方式

### 方式 1: 使用 run.bat（推荐）

```powershell
cd clients\iot\mock_device
.\run.bat
```

### 方式 2: 手动运行 Dapr

```powershell
# 在项目根目录
cd e:\jachin-system

# 确保已激活 conda 环境
conda activate jachin-dev

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

## 前置条件检查

### 1. 安装 Dapr Python SDK

```powershell
pip install dapr-ext-grpc
```

### 2. 验证安装

```powershell
python -c "from dapr.clients import DaprClient; print('OK')"
```

### 3. 确保基础设施运行

```powershell
docker-compose -f docker-compose.dev.yml up -d
```

## 常见错误

### 错误: "No module named 'dapr.clients'"

**解决**: 
```powershell
pip install dapr-ext-grpc
```

### 错误: "&& 不是有效的命令"

**原因**: PowerShell 不支持 `&&` 操作符

**解决**: 使用分号 `;` 或分别执行命令

### 错误: UnicodeEncodeError

**已修复**: 代码已更新，移除了 emoji 字符，使用 ASCII 文本

## 预期输出

```
============================================================
Mock IoT Device - Capability Discovery Test
============================================================

Device: Raspberry Pi (living_room)
Capabilities: camera.capture, light.control

[INFO] Broadcasting device capabilities to system/announce...
   Device ID: raspi-living-room-001
   Location: living_room
   Capabilities: ['camera.capture', 'light.control']
[SUCCESS] Device announcement sent successfully!
   Topic: system/announce
   PubSub: pubsub
   Data size: XXX bytes

============================================================
[SUCCESS] Capability discovery test completed successfully!
============================================================
```
