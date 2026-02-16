# Communication Layer (Nerves)

通信层代码，包含多种协议的实现。

## 目录结构

```
communication/
├── grpc/             # gRPC 服务定义和客户端
│   ├── proto/       # Protocol Buffers 定义
│   ├── server/      # gRPC 服务端
│   └── client/      # gRPC 客户端
├── mqtt/            # MQTT 客户端和服务端
│   ├── client.py
│   ├── server.py
│   └── mosquitto.conf
└── websocket/       # WebSocket 服务
    ├── server.py
    └── client.py
```

## 协议选择指南

### gRPC
- **适用场景**: 高性能端（PC/Linux/服务器）
- **数据类型**: 大数据流传输（音频/视频）
- **优势**: 高性能、类型安全、流式传输

### MQTT
- **适用场景**: 极小芯片（ESP32/树莓派/IoT）
- **数据类型**: 指令控制、传感器数据
- **优势**: 轻量级、低功耗、发布订阅模式

### WebSocket
- **适用场景**: Web 管理端和实时对话
- **数据类型**: RESTful API、实时消息推送
- **优势**: 通用性强、易于集成

## 使用示例

### gRPC Client

```python
from communication.grpc.client import JachinClient

client = JachinClient("localhost:50051")
response = await client.send_command(command="capture_image")
```

### MQTT Client

```python
from communication.mqtt.client import MQTTClient

client = MQTTClient("localhost", 1883)
await client.publish("devices/raspberry-pi/commands", {"action": "turn_on_led"})
```

### WebSocket Client

```python
from communication.websocket.client import WSClient

client = WSClient("ws://localhost:8000/ws")
await client.send_message({"type": "chat", "content": "Hello"})
```
