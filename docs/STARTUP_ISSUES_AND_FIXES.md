# 启动问题分析与解决方案

根据 `start.bat` 启动日志整理的问题与对应解决方案。

---

## 1. Qdrant 连接 503（VectorStore 降级）

### 现象
```
Qdrant connection attempt 1/3 failed: Unexpected Response: 503 (Service Unavailable)
Qdrant connection attempt 2/3 failed: Unexpected Response: 503 (Service Unavailable)
Failed to connect to Qdrant at http://localhost:6333: Unexpected Response: 503 (Service Unavailable)
VectorStore will run in degraded mode (memory-only)
```

### 可能原因
- **localhost 解析**：Windows 上 `localhost` 可能解析到 IPv6 `::1`，与 Qdrant 监听不一致
- **多实例冲突**：多个进程占用 6333 时，部分返回 503
- **Qdrant 未就绪**：本地 Qdrant 启动后短暂返回 503
- **时序**：后端启动时 Qdrant 尚未完全就绪

### 解决方案

**方案 A：改用 127.0.0.1（推荐）**

在 `.env` 中修改：
```env
QDRANT_URL=http://127.0.0.1:6333
QDRANT_GRPC_URL=http://127.0.0.1:6334
```

**方案 B：确保 Qdrant 先启动**
- 先启动 Qdrant，再运行 `start.bat`
- 本地 Qdrant：直接运行 `qdrant.exe`
- Docker：`docker start qdrant-local` 或 `.\scripts\start_qdrant.ps1`

**方案 C：检查端口占用**
```powershell
netstat -ano | findstr 6333
```
确保只有一个进程监听 6333，避免多实例冲突。

**方案 D：代码已做改进**
- 已增加重试（3 次，间隔 2 秒）
- 已设置 `check_compatibility=False`
- 可进一步增加重试次数和间隔（见下方代码修改）

---

## 2. Dapr Python SDK 未安装

### 现象
```
dapr package not installed. DaprClient will not work.
Dapr client not available, StateStore will use in-memory storage
Dapr client not available, PubSub will log messages only
```

### 影响
- 状态存储使用内存，重启后丢失
- Pub/Sub 仅打印日志，不实际发布

### 解决方案
```powershell
conda activate jachin-dev
pip install dapr
```

---

## 3. dashscope 未安装（Qwen 云端）

### 现象
```
dashscope not installed. QwenAdapter will not work.
```
随后又显示 `dashscope_available: True`，说明可能通过其他方式加载。

### 解决方案
若使用阿里云 Qwen API，需安装：
```powershell
pip install dashscope
```

---

## 4. Dapr 组件目录中的 README.md

### 现象
```
A non-YAML Component file README.md was detected, it will not be loaded
```

### 影响
无功能影响，仅为提示。

### 解决方案
将 `components/` 下的 `README.md` 移出或重命名，避免被 Dapr 当作组件加载。

---

## 5. 冲突容器被清理

### 现象
```
Found conflicting container: jachin-dapr-scheduler-dev
Stopping container... Removing container...
```

### 说明
这是预期行为。启动脚本会清理旧容器并重新创建，保证环境干净。

---

## 6. Dapr Scheduler 连接超时

### 现象
```
Failed to watch scheduler jobs, retrying: dial tcp 172.19.0.3:6060: i/o timeout
```

### 原因
Dapr Scheduler 在 Docker 网络内（172.x），宿主机无法直接访问。

### 解决方案
`.env` 中已设置 `DAPR_SCHEDULER_HOST_ADDRESS=skip`，可跳过 Scheduler 连接。若需完整 Dapr 调度，需调整网络或部署方式。

---

## 快速检查清单

| 检查项 | 命令/操作 |
|--------|-----------|
| Qdrant 运行 | `Invoke-WebRequest -Uri "http://127.0.0.1:6333/collections" -UseBasicParsing` |
| 端口占用 | `netstat -ano \| findstr 6333` |
| Dapr 包 | `pip show dapr` |
| dashscope | `pip show dashscope` |
