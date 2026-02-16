# Mock Device 安装指南

## 前置条件

### 1. Python 环境

确保已激活 conda 环境：
```bash
conda activate jachin-dev
```

### 2. 安装 Dapr Python SDK

Mock Device 需要 Dapr Python SDK 来发布消息。

**方式 1: 安装 dapr-ext-grpc（推荐）**
```bash
pip install dapr-ext-grpc
```

**方式 2: 安装 dapr（HTTP 客户端）**
```bash
pip install dapr
```

### 3. 验证安装

```bash
python -c "from dapr.clients import DaprClient; print('Dapr SDK installed successfully')"
```

如果出现错误，请安装对应的包。

## 快速安装

```bash
cd clients/iot/mock_device
pip install -r requirements.txt
```

## 依赖说明

- **dapr-ext-grpc**: Dapr gRPC 客户端（推荐，性能更好）
- **pydantic**: 协议模型定义（通常已在 backend 环境中安装）

## 故障排查

### 错误: "No module named 'dapr.clients'"

**原因**: `dapr-ext-grpc` 未安装

**解决**:
```bash
pip install dapr-ext-grpc
```

### 错误: "No module named 'backend.core.protocol'"

**原因**: Python 路径不正确

**解决**: 确保从项目根目录运行，或使用 `dapr run` 启动
