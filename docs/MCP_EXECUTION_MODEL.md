# MCP 执行模型 — L3 优先、L2 委托

**版本**: 1.0  
**状态**: 设计规范  
**定位**: 统一 MCP/Skill 执行策略，L3 分摊压力，L2 仅做协调与委托

---

## 一、核心原则

| 原则 | 说明 |
|------|------|
| **L3 优先执行** | L3 本机已安装该技能时，**直接本地执行**，不经过 L2 |
| **L2 不执行 MCP** | L2 不运行 MCP 代码，仅做同步、分配、委托协调 |
| **压力分摊** | MCP 执行压力由各 L3 节点分摊，L2 不承载 MCP 算力 |

---

## 二、执行策略

```
L3 需要调用某 MCP/Skill
    │
    ├─ 本机已安装？
    │   └─ 是 → 本地直接执行（默认路径，压力在 L3）
    │
    ├─ 复杂任务需多 L3 协同？
    │   └─ 是 → L2 coordinate 拆分、分配、聚合
    │
    └─ 本机未安装？
        └─ 是 → L2 委托其他有权限且空闲的 L3 执行，结果经 L2 返回
```

---

## 三、三种场景

| 场景 | 执行位置 | L2 角色 |
|------|----------|---------|
| **本机有技能** | L3 本地 | 无 |
| **复杂任务** | 多台 L3 协同 | L2 调度（coordinate） |
| **本机无技能** | 其他 L3 代为执行 | L2 委托、结果转发 |

---

## 四、与路径 2、3 的对应

| 路径 | 符合度 |
|------|--------|
| **路径 2**（MCP 在 L2，L3 代理调用） | ❌ 不符合。压力全在 L2 |
| **路径 3**（L3_LOCAL，L3 本地执行） | ✅ 符合。需实现 L2 委托 fallback |

---

## 五、实现路径

采用 **路径 3（L3_LOCAL 扩展）**：

1. **L3 本地 MCP**：`l3_node/mcp_tools/` 开发期即用；L1 发布后 L2 同步到 `inventory/l3_mcps/`，L3 通过 `l3_node/mcp_sync.py` 拉取到 `~/.jachin/l3_mcp_cache/` 动态加载（`mcp_registry._load_tools_from_l3_mcp_cache`）
2. **L2 委托 API**：当 L3 请求 MCP 且本机未安装时，L2 通过 `get_l3_nodes_with_mcp_tool` 查找有权限且空闲的 L3，委托 `POST /api/v3/mcp/execute` 执行并返回结果（`core/api/routes/v2_mcp.py`）
3. **双模式**：开发期 `allowed_skills=None` 时本地 MCP 全开；测试/生产期按 allowed_skills 过滤

---

## 六、相关文档

- [ARCHITECTURE.md](./ARCHITECTURE.md) — 架构规范
- [MCP_LIFECYCLE_AND_APPROVAL_FLOW.md](../l3_node/mcp_tools/MCP_LIFECYCLE_AND_APPROVAL_FLOW.md) — MCP 生命周期与审批流程
