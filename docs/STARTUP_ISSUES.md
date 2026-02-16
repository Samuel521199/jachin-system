# 启动问题诊断

## 1. Dapr Scheduler 连接超时

**现象**：
```
Failed to watch scheduler jobs: dial tcp 172.19.0.3:6060: i/o timeout
```

**原因**：Placement 在 Docker 内发现 Scheduler 时返回容器内网 IP（172.19.0.x），宿主机上的 Dapr 无法访问。

**解决**：在 `.env` 中添加：
```
DAPR_SCHEDULER_HOST_ADDRESS=skip
```
重启后该错误将消失。开发环境通常不需要 Actor Reminders，跳过 Scheduler 不影响核心功能。

---

## 2. Qdrant 未响应 (503)

**现象**：
```
Qdrant health check failed on port 6333
VectorStore will run in degraded mode (memory-only)
```

**原因**：本地未安装或未启动 Qdrant 服务。

**解决**：
- 安装 Qdrant：参考 [docs/LOCAL_QDRANT_SETUP.md](./LOCAL_QDRANT_SETUP.md)
- 或使用 Docker：在 `docker-compose.dev.yml` 中取消注释 qdrant 服务
- 或接受降级：记忆功能使用内存存储，重启后数据不持久

---

## 3. Dapr 包未安装

**现象**：
```
dapr package not installed. DaprClient will not work.
StateStore will use in-memory storage
```

**说明**：Python 的 `dapr` 包未安装，StateStore/PubSub 使用内存实现。不影响基础运行，仅分布式状态/消息功能受限。

---

## 4. 正常启动标志

- `PluginManager: loaded 6/6 skills`：技能全部加载
- `Uvicorn running on http://0.0.0.0:18888`：API 已就绪
- `Ray initialized successfully in single mode`：Ray 已就绪
