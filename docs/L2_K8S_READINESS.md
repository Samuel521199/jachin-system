# L2 网关 K8s 横向扩展就绪说明

## 状态存储与无单机依赖

L2 网关 API（`/api/v2/*`）的状态数据均存储在数据库或可配置路径，**无进程内内存状态**，可横向扩展。

### 数据库与存储

| 存储 | 默认路径 | 环境变量 | K8s 部署建议 |
|------|----------|----------|--------------|
| SQLite（子账号、节点、任务、记忆） | `~/.jachin/l2_control.db` | `JACHIN_L2_DB_PATH` | 共享卷（NFS/EFS）或迁移至 PostgreSQL |
| LanceDB（记忆向量） | `~/.jachin/lancedb_data` | `JACHIN_LANCEDB_PATH` 或 `JACHIN_DATA_DIR` | 共享卷 |

### 不在 L2 API 中的组件

- **swarm_registry**、**session_manager**：进程内内存，用于 L3/Agent 运行时，不在 L2 网关 API 中。
- **coordinate_tasks / coordinate_subtasks**：状态在 SQLite，已入库。

### 配置示例

```yaml
# K8s Deployment
env:
  - name: JACHIN_L2_DB_PATH
    value: /data/l2_control.db
  - name: JACHIN_LANCEDB_PATH
    value: /data/lancedb_data
volumeMounts:
  - name: l2-data
    mountPath: /data
volumes:
  - name: l2-data
    persistentVolumeClaim:
      claimName: l2-data-pvc
```

## 统一错误码

L2 API 返回统一业务错误码，便于 Prometheus / 日志中心采集：

| 错误码 | 含义 |
|--------|------|
| ERR_AUTH_001 | 缺少认证 |
| ERR_AUTH_002 | 子账号不存在 |
| ERR_AUTH_003 | 权限不足 |
| ERR_AUTH_004 | 节点未分配 |
| ERR_AUTH_005 | 管理员认证失败 |
| ERR_SCHEDULER_001 | 调度失败 |
| ERR_SCHEDULER_002 | 需要 GPU 但无可用节点 |
| ERR_SCHEDULER_003 | 任务/子任务不存在 |
| ERR_BAD_REQUEST_001 | Invalid JSON |
| ERR_BAD_REQUEST_002 | 缺少必填参数 |
| ERR_NOT_FOUND_001 | 子账号不存在 |
| ERR_NOT_FOUND_002 | L3 节点不存在 |
| ERR_INTERNAL_001 | 内部错误 |

响应格式：`{"detail": {"code": "ERR_XXX", "message": "...", "detail": "..."}}`
