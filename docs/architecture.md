# Jachin-System v3.2 架构设计文档

## 文档信息

- **版本**: v3.2
- **最后更新**: 2026-02-16
- **定位**: 分布式智能体操作系统 (Distributed Agent OS)
- **规范引用**: [ARCHITECTURE_DESIGN_SPEC.md](./ARCHITECTURE_DESIGN_SPEC.md)（正式架构规范 v1.0，术语 Layer = Tier）

---

## 一、项目愿景

### 身份定义

Jachin 是**个人的贾维斯**：有灵魂的电子宠物、伙伴好友、永不背叛的伴侣。它可部署于桌面精灵、手机、树莓派、ESP32 或任意联网芯片，作为通用客户端根据权限连接主网，成为强大助手、自主专业团队或多团队协同的「集团军」，**完成主人的愿望**。

> 详见 [VISION.md](./VISION.md)（产品愿景与身份设计）

### 核心定位

Jachin-System 是一个**本地优先、可无限扩展的 AI 智能体生态系统**。它旨在为个人、家庭和中小型团队提供一个私有的「钢铁侠」级算力中心。它**不仅仅是助手**，更是连接**物理世界（IoT）**、**数字资产（Memory）** 和 **云端能力（Marketplace）** 的操作系统。通过插件化扩展，Agents 根据功能与权限**合作、协同、讨论**，实现主人愿望。

### 核心价值主张

1. **隐私优先**: 所有数据存储在本地，用户完全掌控，永不背叛
2. **弹性扩展**: 从单台笔记本（Single Mode）平滑扩展到百卡集群（Cluster Mode）
3. **能力即服务**: 技能插件化，支持自然语言开发、一键分发与热加载
4. **联邦记忆**: 数据在物理上隔离（隐私），在逻辑上分层（共享）
5. **有灵魂**: 人格可配置、陪伴感、情绪表达，而非冰冷工具

---

## 二、三层架构 (The Trinity)

```
云端分发 (Cloud) + 蜂巢算力 (Hive) + 灵动终端 (Terminal)
```

### Tier 1: Jachin Nexus (灵界枢纽)

> **设计哲学**：不做传统 SaaS 或 Web2.0 商城。应是**轻量化、协议化、去中心化**的**智慧分发枢纽**。详见 [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md)。

**职责**: 智慧分发、协议标准、神经元商城、开发者中心

**核心模块**:
- **Neural Market (神经元商城)**：3D 技能树，节点为 Skill/Persona/Memory，可组合预览
- **The Forge (铸造厂)**：Agent Builder、模拟沙箱，开发者在线编排与发布
- **The Agora (广场)**：Agent 展示、Bounty Board 悬赏榜
- **Jachin ID & Console**：舰队视图、隐私审计（强调「0 次向云端上传」）

**核心功能**:
- 技能商店：3D 神经元网络形态浏览、搜索、下载（非传统列表）
- 用户授权：Passkey / Web3 钱包（无密码、强调所有权）
- 存储：IPFS 去中心化，永不丢失、防篡改
- 技能审核：代码安全扫描、功能测试

**技术栈**: Next.js 14 + Three.js / React Three Fiber + Tailwind + Supabase / Golang + IPFS

**部署**: 云端（轻量 Serverless）

---

### Tier 2: Jachin Hive (The Core)

**职责**: 私有主网，运行在本地高性能设备上

**核心功能**:
- **AI 推理**: 本地/云端模型适配，支持 Ray 分布式计算
- **记忆存储**: PostgreSQL（关系型数据）+ Qdrant（向量数据），支持 RAG 有机记忆管线（详见 [RAG_ARCHITECTURE.md](./RAG_ARCHITECTURE.md)）
- **设备管理**: JCP 协议，设备注册与能力发现
- **任务编排**: Ray Scheduler，智能任务分发
- **技能运行时**: Docker / Wasm 沙箱，安全执行

**架构模式**:
- **Master Node (The Queen)**: Control Plane、Brain Orchestrator、Memory、Device Registry
- **Worker Nodes (The Drones)**: GPU Node、CPU Node，执行实际计算任务

**技术栈**:
- Control Plane: FastAPI + Dapr
- Compute: Ray（分布式计算框架）
- Storage: PostgreSQL + Qdrant
- Protocol: JCP (基于 Dapr Pub/Sub)
- Discovery: mDNS / Zeroconf

**部署**: Docker / PyInstaller（Server 模式用 Docker，Personal 模式打包成 .exe 服务）

---

### Tier 3: Jachin Terminal (The Edge)

**职责**: 用户交互界面，只负责 I/O，不负责重度计算

**核心功能**:
- **桌面精灵**: Tauri v2 + React，透明窗口、系统级 API
- **手机 App**: Flutter，跨平台移动端
- **IoT 节点**: 树莓派/ESP32，传感器数据采集、设备控制

**技术栈**:
- Desktop: Tauri v2 + React
- Mobile: Flutter
- IoT: Python / MicroPython

**通信**: 通过 Dapr Pub/Sub 与 Tier 2 通信

---

## 三、核心机制（与规范对齐）

### 3.1 混合生命周期 (Hybrid Lifecycle)

L3 技能运行模式（详见 [ARCHITECTURE_DESIGN_SPEC.md](./ARCHITECTURE_DESIGN_SPEC.md) §3.1）：

| 模式 | 适用场景 | 行为 |
|------|----------|------|
| **Ephemeral (即时)** | 简单通知、一次性查询 | RAM 加载 → 执行 → 立即销毁 |
| **Cached (缓存)** | 游戏、复杂工具、静态资产 | Hash 校验 → 缺失则拉取 → 磁盘缓存 → 随用随开 |
| **Resident (常驻)** | 语音唤醒、安防、网关 | 长期驻留后台，Keep-Alive / 休眠唤醒 |

### 3.2 智能缓存策略 (Intelligent Caching)

- **Assets**（重）: 模型/素材，Hash 不匹配时增量下载
- **Logic**（轻）: 代码，每次校验 Hash，有更新即下载（KB 级）
- **L3 本地**: LRU 算法清理长期不用的 Assets

### 3.3 拓扑模式 (Topology Modes)

| 模式 | 场景 | 实现 |
|------|------|------|
| **Super Node** | 单机 PC/Mac | L2+L3 同机，Loopback 通信，Volume Mapping 零拷贝 |
| **Distributed Cluster** | NAS + Gaming PC + Robot | Ray Cluster + mDNS 服务发现 |

---

## 四、系统架构图

```mermaid
graph TD
    %% 样式定义
    classDef cloud fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
    classDef master fill:#fff3e0,stroke:#e65100,stroke-width:3px;
    classDef worker fill:#fff8e1,stroke:#fbc02d,stroke-width:2px;
    classDef client fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;

    subgraph "Tier 1: Jachin Nexus (灵界枢纽)"
        Store[Neural Market]:::cloud
        Auth[Jachin ID]:::cloud
    end

    subgraph "Tier 2: Jachin Hive (Private Cluster)"
        subgraph "Master Node (The Queen)"
            Control[<b>Control Plane</b><br/>Web UI / API Gateway]:::master
            Brain[<b>Brain Orchestrator</b><br/>Ray Head / Planner]:::master
            DB[(<b>Memory</b><br/>Postgres + Qdrant)]:::master
            Registry[<b>Device Registry</b><br/>Redis]:::master
        end

        subgraph "Worker Nodes (The Drones)"
            Worker1[<b>GPU Node A</b><br/>Ray Worker]:::worker
            Worker2[<b>CPU Node B</b><br/>Ray Worker]:::worker
        end
    end

    subgraph "Tier 3: Jachin Terminals"
        Desktop[<b>Desktop Sprite</b><br/>Tauri + React]:::client
        Mobile[<b>Mobile App</b><br/>Flutter]:::client
        IoT[<b>Raspberry Pi</b><br/>Python Agent]:::client
    end

    %% 连接关系
    Store -->|Download Skill.zip| Control
    Control -->|Install| Worker1 & Worker2
    
    Desktop & Mobile -->|CMD / Voice| Control
    IoT -->|JCP Announce| Registry
    
    Control -->|Schedule Task| Brain
    Brain -->|Ray Dispatch| Worker1 & Worker2
    Worker1 -->|Inference Result| Brain
```

---

## 五、核心业务流程

### 1. 分布式推理流程 (Distributed Inference)

当用户说"帮我分析这个 1GB 的视频"时，系统如何调度？

```mermaid
sequenceDiagram
    participant User as 用户 (Tier 3)
    participant Master as Master Node (Tier 2)
    participant Ray as Ray Scheduler
    participant Worker as Worker GPU (Tier 2)

    User->>Master: 发送指令 "分析视频"
    Master->>Master: 1. Brain 解析意图 -> 需要 "VideoSkill"
    Master->>Ray: 2. 调度任务 (Task: VideoProcess)
    
    Note over Ray: 发现 Worker 节点有空闲 GPU
    Ray->>Worker: 3. 发送任务 & 数据引用
    
    Worker->>Worker: 4. 加载模型 -> 推理
    Worker-->>Ray: 5. 返回结果 (Summary Text)
    
    Ray-->>Master: 6. 汇总结果
    Master-->>User: 7. "视频分析如下..."
```

### 2. 技能开发与分发流程 (Skill Dev & Distribute)

```mermaid
sequenceDiagram
    participant Dev as 开发者 (Tier 3)
    participant Core as Jachin Core (Tier 2)
    participant Cloud as Jachin Market (Tier 1)
    participant Other as 其他用户 (Tier 2)

    Note over Dev: 在本地 Dev Console 开发
    Dev->>Core: 创建 Skill "StockPro"
    Core->>Core: 本地沙箱测试运行
    
    Note over Dev: 测试通过，发布
    Dev->>Core: 点击 "Upload to Market"
    Core->>Cloud: 上传 Skill 包 + Manifest
    
    Note over Cloud: 审核通过，上架
    
    Other->>Core: 浏览商城 -> 点击 "购买/安装"
    Core->>Cloud: 验证 License
    Cloud-->>Core: 下载 Skill.zip
    Core->>Core: 热加载 (Hot Load) 到集群
```

### 3. 设备能力发现流程 (Device Capability Discovery)

```mermaid
sequenceDiagram
    participant Device as IoT Device (Tier 3)
    participant PubSub as Dapr Pub/Sub
    participant Registry as Device Registry (Tier 2)
    participant Brain as Brain Orchestrator (Tier 2)

    Device->>PubSub: 1. 广播能力 (system/announce)
    PubSub->>Registry: 2. 注册设备能力
    Registry->>Registry: 3. 存储到 Redis
    
    Note over Brain: 用户请求 "打开客厅灯"
    Brain->>Registry: 4. 查询能力 "控制灯光"
    Registry-->>Brain: 5. 返回设备列表
    
    Brain->>PubSub: 6. 发送指令 (device/{id}/command)
    PubSub->>Device: 7. 转发指令
    Device->>Device: 8. 执行动作
    Device->>PubSub: 9. 返回结果 (device/{id}/response)
    PubSub->>Brain: 10. 转发结果
    Brain-->>User: 11. "已打开客厅灯"
```

---

## 六、文件结构

```
jachin-system/
├── .cursor/rules/             # [AI] 所有的 .mdc 规则文件
├── cloud/                     # [Tier 1] 云端代码 (Go/Python)
│   ├── marketplace/           # 商城后端
│   └── auth/                  # 鉴权中心
├── core/                      # [Tier 2] 核心蜂巢代码 (Python)
│   ├── app/                   # 桌面服务封装 (Tray Icon, System Service)
│   ├── api/                   # FastAPI 网关
│   ├── brain/                 # 智能层
│   │   ├── llm/               # 本地/云端模型适配器
│   │   ├── ray_cluster/       # [NEW] Ray 集群管理与调度
│   │   └── planner/           # 任务编排
│   ├── runtime/               # 技能运行沙箱 (Docker/Wasm)
│   ├── registry/              # JCP 设备与能力注册表
│   ├── memory/                # Qdrant 记忆管理 (含权限过滤)
│   ├── web_ui/                # [NEW] 本地管理后台 (React Build)
│   └── main.py                # 启动入口 (自动检测 Single/Cluster 模式)
├── clients/                   # [Tier 3] 客户端
│   ├── desktop/               # Tauri v2 桌面精灵
│   ├── mobile/                # Flutter App
│   └── iot/                   # 树莓派/ESP32 脚本
├── skills_repo/               # [Local] 已安装技能存储库
├── installer/                 # [Deploy] 一键安装/集群配对脚本
├── docker-compose.yml         # 基础设施 (Redis, Qdrant, Postgres)
└── requirements.txt
```

---

## 七、技术栈清单

| 领域 | 技术选型 | 理由 |
|------|---------|------|
| **Tier 1 (Cloud)** | Go / Python | 高并发接口，处理全球 License 验证 |
| **Tier 2 (Control)** | FastAPI + Dapr | 只有 Dapr 能优雅处理 Python/Rust/Go 混合微服务 |
| **Tier 2 (Compute)** | Ray | 专门用于 AI 的分布式计算框架，比 K8s 轻量，原生支持 Python |
| **Tier 2 (Storage)** | PostgreSQL + Qdrant | 关系型数据（权限/用户）+ 向量数据（记忆） |
| **Tier 3 (Client)** | Tauri v2 + React | 极致性能与内存占用，支持透明窗口和系统级 API |
| **Protocol** | JCP (based on Pub/Sub) | 自研能力发现协议，基于 Dapr Pub/Sub |
| **Discovery** | mDNS / Zeroconf | 局域网内 Tier 3 自动发现 Tier 2 Master IP |
| **Deployment** | Docker / PyInstaller | Server 模式用 Docker，Personal 模式打包成 .exe 服务 |

---

## 八、关键设计决策

### 1. 为什么选择 Ray 而不是 Kubernetes？

- **轻量级**: Ray 专为 AI 工作负载设计，比 K8s 更轻量
- **原生 Python**: Ray 原生支持 Python，无需容器化
- **动态调度**: Ray 支持动态任务调度，适合 AI 推理场景
- **资源感知**: Ray 自动感知 GPU/CPU 资源，智能分配任务

### 2. 为什么保留 Dapr？

- **多语言支持**: Dapr 支持 Python/Rust/Go 混合微服务
- **服务网格**: Dapr 提供统一的服务发现、状态管理、Pub/Sub
- **本地优先**: Dapr 可以在本地运行，无需云端依赖

### 3. 为什么技能系统使用 Docker/Wasm？

- **安全隔离**: Docker/Wasm 提供沙箱环境，防止恶意代码
- **热加载**: 支持动态加载技能，无需重启系统
- **跨平台**: Docker 支持多平台，Wasm 支持浏览器

### 4. 为什么记忆系统分离 PostgreSQL 和 Qdrant？

- **关系型数据**: PostgreSQL 存储用户、权限、设备等结构化数据
- **向量数据**: Qdrant 存储记忆向量，支持语义搜索
- **性能优化**: 分离存储可以针对不同场景优化

### 5. RAG 记忆管线（有机记忆流动）

- **动态语义切块**: 按语义边界切分，非按字数一刀切
- **记忆分层**: 短期工作区（Redis）→ 潜意识沉淀（梦境 Agent）→ 长期向量库（Qdrant）
- **时效衰减**: 检索时引入时间权重惩罚，旧记忆自然衰减
- **重点数据永久保存**: `is_core=True` 铂金标签，免疫衰减、永不覆写
- **边缘 L1 缓存**: 高性能终端可启用本地向量库（如 LanceDB），零延迟离线反射

---

## 九、相关文档

- **[ARCHITECTURE_DESIGN_SPEC.md](./ARCHITECTURE_DESIGN_SPEC.md)** - 正式架构规范 v1.0（Single Source of Truth）
- **[RAG_ARCHITECTURE.md](./RAG_ARCHITECTURE.md)** - 第四章：RAG 架构的深度定制（有机记忆管线）
- **[LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md)** - Layer 1 架构与设计总览（Jachin Nexus 灵界枢纽）
- **[whitepaper_v4.0_swarm.md](./whitepaper_v4.0_swarm.md)** - v4.0 蜂群智能白皮书
- **[ARCHITECTURE_V3.2_GAP_ANALYSIS.md](./ARCHITECTURE_V3.2_GAP_ANALYSIS.md)** - 架构差距分析
- **[SENTINEL_DESIGN.md](./SENTINEL_DESIGN.md)** - 哨兵系统设计愿景（执行者→守护者）
- **[PROJECT_STRUCTURE.md](../PROJECT_STRUCTURE.md)** - 项目结构说明

---

**文档版本**: v3.2.0  
**最后更新**: 2026-02-03  
**维护者**: Jachin-System Team
