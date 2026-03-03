# Jachin 全域微内核生态架构 — 深度融合升级方案

**版本**: 2.0  
**状态**: 设计规范  
**定位**: 剥离「指挥权」与「执行权」，打通 Layer 1 与 Layer 2 经脉

---

## 一、核心哲学：剥离「指挥权」与「执行权」

架构的核心在于**绝对的物理与逻辑隔离**：

| 层级 | 角色 | 职责 | 状态 |
|------|------|------|------|
| **Layer 1 (Jachin Nexus)** | 指挥部 & 兵工厂 | 协议制定 (JMP)、武器研发 (Forge)、武器分发 (Market)、舰队大盘 (Console) | **无状态**，无用户对话记忆 |
| **Layer 2 (Microkernel Node)** | 前线边缘智能体 | 加载 JMP 包、RAG 检索、多级权限管控、VAD 语音 I/O、私密对话上下文、Proof-of-Execution 心跳 | **有状态**，本地执行 |

**设计红利**：Layer 1 永不触碰用户隐私数据；Layer 2 永不丢失执行权归属。

---

## 二、JMP 协议 2.0 — 环境感知与依赖树

### 2.1 升级目标

在 `plugins_registry.manifest_json` 中强化**「环境感知」**与**「依赖树」**，实现 Layer 1 商城与 Layer 2 微内核的完美握手。

### 2.2 manifest.json 2.0 规范

```json
{
  "jmp_version": "2.0",
  "plugin_id": "core-vad-audio",
  "type": "skill",
  "category": "IO_Enhancement",
  "compatibility": {
    "os": ["linux", "k8s"],
    "min_core_version": "1.2.0",
    "conflicts_with": ["legacy-audio-input"]
  },
  "resource_footprint": {
    "ram_estimate_mb": 256,
    "gpu_required": false
  },
  "entrypoint": "module.py",
  "permissions": ["internet.access"],
  "capabilities": []
}
```

### 2.3 新增字段说明

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `jmp_version` | string | 协议版本，如 `"2.0"` |
| `compatibility.os` | string[] | 支持的环境：`linux` / `k8s` / `docker` / `windows` / `bare_metal` |
| `compatibility.min_core_version` | string | 微内核最低版本要求（语义化版本） |
| `compatibility.conflicts_with` | string[] | 互斥插件 ID 列表 |
| `resource_footprint.ram_estimate_mb` | number | 预估内存占用 (MB) |
| `resource_footprint.gpu_required` | boolean | 是否必须 GPU |
| `entrypoint` | string | 入口文件，默认 `main.py` |

### 2.4 设计红利

- **环境透镜**：用户在 `/market` 选择目标 Layer 2 环境后，自动过滤不兼容插件，不兼容节点置灰。
- **冲突预警**：在 Forge 中，互斥插件连线时显示红色虚线并阻止发布。
- **资源透明**：重型插件（大 RAG、3D 渲染）显示红色警告边框。

---

## 三、深度融合：核心流程与端云握手演进

### 3.1 部署流程 (The Deployment Handshake)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. 编排与下单                                                             │
│    用户在 /forge 将「人工客服」「PostgreSQL 记忆体」「VAD 语音包」连线      │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. 生成清单                                                               │
│    点击发布 → Layer 1 生成「目标状态清单 (Desired State Manifest)」         │
│    → 写入 deploy_manifests + deploy_commands                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. 拉取与比对                                                             │
│    Layer 2 UpdaterAgent 轮询 GET /api/v1/deploy/poll                      │
│    → 获取新清单 → 微内核与本地 current state 做 Diff                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. 智能热插拔 (Hot-Swap)                                                  │
│    • 新增插件 → 下载 .jmp → 校验 Hash → 挂载入内存                         │
│    • 移除插件 → 优雅关闭 (Graceful Shutdown) → 卸载                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. 状态回传                                                               │
│    Layer 2 → POST /api/v1/agents/heartbeat                                 │
│    → Layer 1 更新 edge_agents (last_heartbeat)，返回 blueprint、task      │
│    → /console 拓扑图节点亮起绿色在线指示灯                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.2 与现有轮询的兼容

- 保留 `GET /api/v1/deploy/poll`，适合穿越防火墙。
- 响应体扩展为支持**单条指令**（向后兼容）或**完整 Desired State Manifest**（新格式）。
- Layer 2 根据 `manifest_version` 字段判断处理逻辑。

---

## 四、UI/UX 体验深化

### 4.1 Neural Market — 环境透镜 (Environment Lens)

| 元素 | 行为 |
|------|------|
| **环境选择器** | 右上角下拉：Kubernetes / Docker / 树莓派 / Bare Metal |
| **完全兼容节点** | 青色发光 `--glow-cyan` |
| **资源消耗大** | 红色警告边框（如 ram_estimate_mb > 512 或 gpu_required） |
| **环境不匹配** | `opacity-30` 置灰 |

### 4.2 The Forge — 防呆设计 (Conflict Resolution)

| 元素 | 行为 |
|------|------|
| **互斥连线** | 两插件在 `conflicts_with` 中互相引用 → 连线呈**红色虚线** + `animate-pulse` |
| **侧边栏** | 显示明确冲突提示，阻止生成 JMP 包 |
| **发布按钮** | 存在冲突时禁用 |

### 4.3 Console — 极致透明

| 流类型 | 颜色 | 说明 |
|--------|------|------|
| **指令流** | 绿色 | 从 Layer 1 → Layer 2（插件更新同步） |
| **状态流** | 蓝色 | 从 Layer 2 → Layer 1（健康检查、CPU 占用） |
| **数据流** | 无 | 明确展示**绝对没有**用户业务数据流向 Layer 1 |

**设计目标**：强化「零信任」与数据主权安全感。

---

## 五、数据模型补充

### 5.1 edge_agents 表（Nexus 主表）

用于追踪边缘智能体状态，支撑舰队视图、心跳、IM 绑定。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `pairing_code` | VARCHAR(6) | 配对码 |
| `auth_token` | TEXT | 心跳/API 通行证 |
| `current_blueprint_id` | UUID FK | 当前蓝图 |
| `last_heartbeat` | TIMESTAMPTZ | 最后心跳时间 |
| `im_binding_id` | TEXT | IM 绑定 ID（如 Telegram chat_id） |
| `im_platform` | TEXT | telegram \| lark |
| `status` | ENUM | pending / active / offline |

### 5.2 deploy_manifests 表（可选，GitOps 模式）

用于存储「目标状态清单」，支持批量部署与 Diff。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `instance_id` | TEXT | 目标 Layer 2 实例 |
| `plugin_ids` | JSONB | 期望运行的插件 ID 列表 |
| `manifest_version` | INT | 版本号，递增 |
| `status` | TEXT | pending / applied / failed |
| `created_at` | TIMESTAMPTZ | 创建时间 |

---

## 六、实施路线图

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| **P0** | edge_agents 表 + 心跳 API + IM 网关 | ✅ 已实现 |
| **P0** | JMP 2.0 manifest 扩展（compatibility, resource_footprint） | 高 |
| **P1** | Market 环境透镜 UI | 中 |
| **P1** | Forge 冲突检测与红线提示 | 中 |
| **P2** | Console 指令流/状态流可视化 | 中 |
| **P2** | deploy_manifests + GitOps 式 Diff 热插拔 | 低 |

---

**相关文档**:
- [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md)
- [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md) - IM 网关（TG/飞书、消息队列）
- [JMP_SPEC.md](./JMP_SPEC.md)
- [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md) - 混合动力沙箱（WASM + UDS）
