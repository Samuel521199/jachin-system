# Core 目录与 Layer 映射分析

**日期**: 2026-02  
**依据**: ARCHITECTURE_DESIGN_SPEC.md、DIRECTORY_STRUCTURE_V4.md、现有 core/ 结构

---

## 1. 依据文档

本分析基于 `docs/ARCHITECTURE_DESIGN_SPEC.md`、`docs/DIRECTORY_STRUCTURE_V4.md` 及现有 core/ 结构。  
Nexus (Layer 1) 架构见 [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md)，Agent Loop 见 [LAYER2_AGENT_LOOP_DESIGN.md](./LAYER2_AGENT_LOOP_DESIGN.md)。

---

## 2. 现有 core/ 目录结构概览

```
core/
├── api/              # FastAPI 路由 (chat, voice, skills, cluster, orchestrator...)
├── app/              # 桌面服务封装
├── brain/            # 智能层
│   ├── llm/          # LLM 适配器
│   ├── llm_engine/   # 模型路由 (Big-Little)
│   ├── planner/      # 意图解析、任务编排
│   ├── ray_actors/   # 兼容层 (→ core.skills)
│   └── ray_cluster/  # Ray 集群管理、任务调度
├── skills/           # BaseSkill, SentinelActor
├── swarm/            # NodeRegistry, Scheduler, HealthMonitor
├── security/         # TrustZone, ACL
├── system/           # PluginManager, Permission, Kernel
├── runtime/          # SkillLoader, SkillRunner, Manifest, Sandbox
├── memory/           # VectorStore, Schema
├── transport/        # Jachin Link Gateway (gRPC, mTLS)
├── registry/         # DeviceRegistry
├── dapr/             # StateStore, PubSub
├── config/           # Settings
├── voice/            # STT, TTS
├── monitoring/       # PerformanceMonitor
└── ...
```

---

## 3. 提议模块与现有结构映射

### 3.1 `core/cloud_client/`（对应 Layer 1 通信）

| 项目 | 说明 |
|------|------|
| **规范职责** | L2 与 L1 通信：Account & Auth、Skill Store 下载、License Authority |
| **现有实现** | `core/transport/` 为 **L3→L2** 入口（Gateway 接收 Tier 3 连接），无 L1 客户端 |
| **现有相关** | `plugin_manager._verify_license()` 为本地 mock；`system/updater.py` 有 `download_update` |
| **结论** | **需要新建** `core/cloud_client/` |
| **建议内容** | `CloudClient`（或 `MarketplaceClient`）：技能下载、License 验证、OAuth 回调等 |

---

### 3.2 `core/brain/skill_manager/`（对应 Layer 2 技能管理）

| 项目 | 说明 |
|------|------|
| **规范职责** | Skill Management：从 L1 下载、安装、卸载、更新技能包 |
| **现有实现** | `core/system/plugin_manager.py` 为核心（加载、Ray Actor、.jsp 安装） |
| **现有相关** | `core/runtime/skill_loader.py`（发现、manifest）、`skill_runner.py`（执行沙箱） |
| **结论** | **可选重构**：将技能管理逻辑抽到 `brain/skill_manager/` |
| **建议** | 方案 A：新建 `brain/skill_manager/`，`plugin_manager` 作为 facade 调用；方案 B：保持 `system/plugin_manager`，在文档中明确其对应 Layer 2 Skill Management |

---

### 3.3 `core/agent/runtime/`（对应 Layer 3 执行）

| 项目 | 说明 |
|------|------|
| **规范职责** | L3 Runtime：运行 Wasm/Python 切片、Cache Manager |
| **现有实现** | L3 在 `clients/`：`desktop/`、`lib/edge_brain/`、`lib/jachin_link_client/` |
| **core 定位** | core 为 **Tier 2 (L2)** 主体，不直接承载 L3 执行逻辑 |
| **结论** | **不建议** 在 core 内新建 `agent/runtime/` |
| **建议** | L3 执行逻辑保留在 `clients/lib/edge_brain/` 或新建 `clients/lib/agent_runtime/`；core 仅提供 API 与协议 |

---

### 3.4 `core/brain/cluster/`（对应 Distributed/Ray）

| 项目 | 说明 |
|------|------|
| **规范职责** | Distributed Cluster：Ray Cluster、算力调度、mDNS 发现 |
| **现有实现** | `core/brain/ray_cluster/`（cluster_manager, task_scheduler, tasks） |
| **现有相关** | `core/swarm/`（node_registry, scheduler, health_monitor） |
| **结论** | **已有对应**，无需新建 `cluster/` |
| **建议** | 方案 A：将 `ray_cluster` 重命名为 `cluster`（更通用）；方案 B：保持 `ray_cluster`，在文档中明确其对应 Distributed Cluster |

---

## 4. 目录调整建议汇总

| 提议模块 | 建议 | 操作 |
|----------|------|------|
| `core/cloud_client/` | **新建** | 创建目录，实现 L1 通信客户端（技能下载、License、Auth） |
| `core/brain/skill_manager/` | **可选** | 可抽离 `plugin_manager` 核心逻辑，或保持现状并文档化 |
| `core/agent/runtime/` | **不建** | L3 在 clients/，core 不新增 agent 模块 |
| `core/brain/cluster/` | **已有** | `ray_cluster` 已覆盖，可选重命名为 `cluster` |

---

## 5. Layer 与 core 模块映射表

| Layer | 规范职责 | core 现有模块 | 缺口/建议 |
|-------|----------|---------------|-----------|
| **L1** | Cloud: Auth, Store, License | 无 | 新建 `cloud_client/` |
| **L2** | Brain: Skill Mgmt, Task Dispatcher, Context | `system/plugin_manager`, `brain/ray_cluster`, `runtime/`, `dapr/` | 可选抽 `skill_manager` |
| **L2** | Distributed Cluster | `brain/ray_cluster`, `swarm/` | 已覆盖 |
| **L3** | Agent: Runtime, I/O, Cache | 无（在 `clients/`） | 不建 core/agent |

---

## 6. 下一步（待确认）

1. **MASTER_DESIGN.md**：若存在，请贴出内容以便按该文档细化映射。
2. **cloud_client**：确认后新建 `core/cloud_client/` 并实现 L1 通信接口。
3. **skill_manager**：决定是否从 `plugin_manager` 抽离到 `brain/skill_manager/`。
4. **ray_cluster 命名**：决定是否重命名为 `cluster`。

**暂不移动或删除任何现有文件，仅做映射与建议。**
