# Dapr Run 命令参考

## Windows (PowerShell)

```powershell
dapr run `
  --app-id mock-iot-device `
  --app-port 8001 `
  --dapr-http-port 3501 `
  --dapr-grpc-port 50002 `
  --resources-path ./dapr/components `
  --config ./dapr/config/config.yaml `
  -- python clients/iot/mock_device/main.py
```

## Linux/macOS (Bash)

```bash
dapr run \
  --app-id mock-iot-device \
  --app-port 8001 \
  --dapr-http-port 3501 \
  --dapr-grpc-port 50002 \
  --resources-path ./dapr/components \
  --config ./dapr/config/config.yaml \
  -- python clients/iot/mock_device/main.py
```

## 参数说明

- `--app-id mock-iot-device`: 应用标识符（唯一）
- `--app-port 8001`: 应用端口（模拟设备不使用，但 Dapr 需要）
- `--dapr-http-port 3501`: Dapr HTTP API 端口（避免与后端冲突）
- `--dapr-grpc-port 50002`: Dapr gRPC API 端口（避免与后端冲突）
- `--resources-path ./dapr/components`: Dapr 组件配置路径
- `--config ./dapr/config/config.yaml`: Dapr 全局配置路径

## 快速启动

### Windows
```bash
cd clients\iot\mock_device
.\run.bat
```

### Linux/macOS
```bash
cd clients/iot/mock_device
./run.sh
```

## 注意事项

1. **端口冲突**: 确保端口 3501 和 50002 未被占用
2. **Dapr 组件**: 确保 `dapr/components/pubsub-redis.yaml` 存在
3. **Redis 运行**: 确保 Redis 服务已启动（通过 docker-compose）
