# Jachin-System 目录结构树

**版本**: v3.2 → v4.0 (Swarm & Edge)  
**日期**: 2026-02  
**状态**: ✅ 已更新

---

## 完整目录结构 (v4.0)

```
jachin-system/
├── common/                          # 共享协议层 (The Bridge)
│   ├── protocols/
│   │   ├── jachin_link.proto
│   │   └── swarm_discovery.proto   # [v4.0] 节点自发现协议
│   ├── schemas/
│   │   ├── manifest.py
│   │   ├── auth.py                 # [v4.0] TrustZone, UserRole
│   │   └── resources.py            # [v4.0] GPU/NPU 资源标签
│   ├── __init__.py
│   ├── README.md                    # 使用指南和设计原则
│   ├── protocols/                   # gRPC 协议定义
│   │   ├── __init__.py
│   │   └── jachin_link.proto        # Jachin Link 通信协议
│   ├── schemas/                     # 数据模型（Pydantic）
│   │   ├── __init__.py
│   │   ├── manifest.py              # 插件清单模型
│   │   ├── telemetry.py             # 监控数据模型
│   │   └── sdui.py                  # [NEW] Server-Driven UI 模型 (Adaptive Cards)
│   └── crypto/                      # 加密工具类
│       ├── __init__.py
│       └── signature.py             # 签名验证工具
│
├── cloud/                           # [Tier 1] 云端实现 (Go/Python)
│   ├── market_backend/              # [NEW] 插件商城 API (Serverless)
│   │   └── __init__.py
│   ├── auth_center/                 # [NEW] CA 根证书管理
│   │   └── __init__.py
│   ├── global_relay/                # [NEW] 高性能中继网关 (K8s/Go)
│   │   └── __init__.py
│   ├── market/                      # [旧] 待迁移
│   ├── marketplace/                 # [旧] 待迁移
│   ├── auth/                        # [旧] 待迁移
│   └── relay/                       # [旧] 待迁移
│
├── core/                            # [Tier 2] 蜂巢核心实现 (Python)
│   ├── brain/                       # [The Mind] 智能层
│   │   ├── agent_orchestrator.py   # ReAct 循环核心
│   │   ├── llm_engine.py           # 大小脑路由
│   │   ├── ray_actors/             # 技能 Actor
│   │   │   ├── base_skill.py        # 技能基类 (BaseSkillActor)
│   │   │   ├── base_agent.py        # Agent 基类
│   │   │   └── sentinel.py          # 哨兵 Actor
│   │   └── ray_cluster/            # Ray 集群管理
│   ├── swarm/                       # [v4.0] [The Infrastructure] 集群层
│   │   ├── node_registry.py        # 节点注册 (谁在线？有什么硬件？)
│   │   ├── scheduler.py           # Swarm Scheduler (gpu_heavy 分配)
│   │   └── health_monitor.py      # 心跳检测
│   ├── security/                    # [v4.0] [The Shield] 安全层
│   │   ├── acl_manager.py          # 访问控制 (Office vs Home)
│   │   └── trust_zone.py           # 信任域隔离
│   │   ├── llm/                     # LLM 适配器
│   │   ├── planner/                 # 任务规划器
│   │   └── ray_cluster/             # Ray 集群管理
│   ├── transport/                   # 网络层 (Jachin Link Server)
│   │   ├── gateway.py               # gRPC Server
│   │   ├── mtls_manager.py          # 证书管理
│   │   ├── connection_manager.py    # 连接管理
│   │   ├── protocol.proto           # [旧] 待迁移到 common/protocols/
│   │   └── ...
│   ├── system/
│   │   ├── plugin_manager.py        # 技能唯一入口 (load_skills, get_actor, list_capabilities)
│   │   └── ...
│   └── memory/                      # 联邦记忆 (Postgres/Qdrant)
│
├── clients/                         # [Tier 3] 客户端实现
│   ├── desktop/                     # Tauri v2
│   └── lib/
│       ├── jachin_link_client/      # Jachin Link Client
│       └── edge_brain/              # [v4.0] Tier 3 本地智能 (Edge Reflex)
│
├── skills_repo/                     # 本地技能目录 (v4.0 分类)
│   ├── _bundled/                    # 系统预装技能 (兼容)
│   ├── drivers/                     # [v4.0] 硬件驱动 (IoT, 传感器)
│   └── apps/                        # [v4.0] 纯软件应用 (WebSurfer, Calendar)
├── docs/                            # 文档
│   ├── whitepaper_v3.2_final.md    # 架构白皮书
│   └── DIRECTORY_STRUCTURE_TREE.md  # 本文件
└── ...
```

---

## 关键目录说明

### `common/` - 共享协议层 (The Bridge)

**设计原则**: "共享协议，隔离实现"

**允许的内容**:
- ✅ Protocol Buffers 定义 (`.proto`)
- ✅ Pydantic 数据模型 (`schemas/*.py`)
  - `manifest.py`: 插件清单模型
  - `telemetry.py`: 监控数据模型
  - `sdui.py`: Server-Driven UI 模型（Adaptive Cards）
- ✅ 加密工具类 (`crypto/*.py`)
- ✅ 常量定义

**严禁的内容**:
- ❌ 业务逻辑代码
- ❌ 数据库访问代码
- ❌ 网络通信代码（除了协议定义）
- ❌ 任何 Tier 特定的实现

---

### `cloud/` - Tier 1 云端服务

**新结构**:
- `market_backend/`: 插件商城 API (Serverless)
- `auth_center/`: CA 根证书管理
- `global_relay/`: 高性能中继网关 (K8s/Go)

**隔离规则**:
- ✅ 只能引用 `common/` 目录
- ❌ 严禁引用 `core/` 目录

---

### `core/` - Tier 2 蜂巢核心

**新结构**:
- `brain/ray_actors/`: 所有 AI Agent 都是 Ray Actor
  - `base_agent.py`: Agent 基类
  - `manager.py`: 资源调度器

**隔离规则**:
- ✅ 只能引用 `common/` 目录
- ❌ 严禁引用 `cloud/` 目录

---

## 文件迁移清单

### ✅ 已完成

1. **协议文件**
   - ✅ `common/protocols/jachin_link.proto` (新建)
   - ⚠️ `core/transport/protocol.proto` (旧文件，待删除)

2. **数据模型**
   - ✅ `common/schemas/manifest.py` (新建，Pydantic 模型)
   - ⚠️ `core/system/plugin_manager.py` 中的 `PluginManifest` (旧实现，待迁移)

3. **加密工具**
   - ✅ `common/crypto/signature.py` (新建)

### ⏳ 待执行

1. **更新导入路径**
   - 将 `core/transport/gateway.py` 中的协议引用更新为 `common/protocols/jachin_link.proto`
   - 将 `core/system/plugin_manager.py` 中的 `PluginManifest` 引用更新为 `common.schemas.manifest`

2. **删除旧文件**
   - 删除 `core/transport/protocol.proto` (已迁移到 `common/protocols/jachin_link.proto`)

3. **迁移旧目录**
   - 将 `cloud/market/` 迁移到 `cloud/market_backend/`
   - 将 `cloud/auth/` 迁移到 `cloud/auth_center/`
   - 将 `cloud/relay/` 迁移到 `cloud/global_relay/`

---

## 验证检查清单

- [x] `common/` 目录创建完成
- [x] `common/protocols/jachin_link.proto` 创建完成
- [x] `common/schemas/manifest.py` 创建完成
- [x] `common/crypto/signature.py` 创建完成
- [x] `cloud/market_backend/` 创建完成
- [x] `cloud/auth_center/` 创建完成
- [x] `cloud/global_relay/` 创建完成
- [x] `core/brain/ray_actors/` 创建完成
- [x] 白皮书更新完成
- [ ] 导入路径更新（待执行）
- [ ] 旧文件删除（待执行）
- [ ] 旧目录迁移（待执行）

---

**文档版本**: v1.0  
**最后更新**: 2026-02-06  
**维护者**: Jachin-System Architecture Team
