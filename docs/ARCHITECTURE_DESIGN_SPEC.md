# Jachin System Architecture & Design Specification

**Version:** 1.0.0  
**Status:** Approved for Implementation

---

## 1. System Overview (系统概览)

Jachin 是一个三层架构的分布式 AI 蜂群系统，支持从单机（Super Node）到家庭数据中心（Cluster）的弹性扩展。系统以「技能（Skill）」为核心资产，实现云端分发、边缘管控、端侧执行。

---

## 2. Architecture Layers (架构分层)

### Layer 1: Cloud (The Soul) - 云端

* **Role**: 全局管理、市场、鉴权中心。
* **Key Responsibilities**:
    * **Account & Auth**: 统一身份认证 (OAuth2/OIDC)，管理用户与家庭域 (Home Domain) 的绑定关系。
    * **Skill Store**: 技能市场，存储技能元数据、代码包、模型权重。
    * **License Authority**: 颁发数字证书，管理技能的购买记录和授权范围（Site License）。

### Layer 2: Brain / Edge (The Mind) - 边缘/家庭服务器

* **Role**: 局域网控制中心、算力枢纽、技能仓库。
* **Topology**: 支持单机部署，也支持 **Ray Cluster** (Head + Workers) 多机分布式部署。
* **Key Responsibilities**:
    * **Skill Management**: 负责从 L1 下载、安装、卸载、更新技能包。
    * **Task Dispatcher**: 接收 L3 请求，根据算力需求调度任务（本地执行 or 远程分发）。
    * **Context Holder**: 维护家庭全局状态 (Redis)，确保多设备间上下文同步。

### Layer 3: Agent (The Body) - 端侧执行器

* **Role**: 交互界面、传感器数据采集、执行动作。
* **Topology**: 也就是所谓的 Client，可以是 PC、手机、IoT 设备。
* **Key Responsibilities**:
    * **Runtime**: 运行轻量级执行切片 (Wasm/Python Script)。
    * **I/O Handling**: 语音采集 (STT 前端)、TTS 播放、UI 渲染。
    * **Cache Manager**: 智能缓存技能资产，减少网络传输。

---

## 3. Core Mechanisms (核心机制)

### 3.1 Hybrid Lifecycle Management (混合生命周期管理)

Layer 3 的技能运行遵循以下三种模式：

1. **Ephemeral (即时模式)**:
    * *适用*: 简单通知、一次性查询。
    * *行为*: 代码 RAM 加载 -> 执行 -> 立即销毁。零磁盘占用。

2. **Cached (缓存模式)**:
    * *适用*: 游戏、复杂工具、需加载静态资产的技能。
    * *行为*: 检查本地 Hash -> (缺失则从 L2 拉取) -> 磁盘缓存 -> 运行。进程随用随开。

3. **Resident (常驻模式/Daemon)**:
    * *适用*: 语音唤醒、安防监控、网关服务。
    * *行为*: 安装后长期驻留后台，支持 Keep-Alive 和休眠唤醒。

### 3.2 Intelligent Caching Strategy (智能缓存策略)

* **Structure**: 技能包分为 `Assets` (重，模型/素材) 和 `Logic` (轻，代码)。
* **Policy**:
    * Logic 每次校验 Hash，有更新即下载（KB级）。
    * Assets 仅在 Hash 不匹配时下载（增量更新）。
    * L3 本地通过 LRU (Least Recently Used) 算法自动清理长期不用的 Assets。

### 3.3 Topology Modes (拓扑模式)

* **Super Node (超级节点)**:
    * *场景*: 单机高性能 PC / Mac。
    * *实现*: L2 和 L3 进程跑在同一台机器。
    * *优化*: 使用 **Loopback** 通信；文件传输通过 **Volume Mapping/Symlink** 实现零拷贝（Zero-Copy），秒级加载。

* **Distributed Cluster (分布式集群)**:
    * *场景*: NAS (L2 Head) + Gaming PC (L2 Worker) + Robot (L3)。
    * *实现*: 基于 Ray Cluster 进行算力调度；基于 mDNS 进行服务发现。

---

## 4. User Experience Flows (用户体验流程)

### 4.1 Purchase & Install (购买与安装)

1. **Initiate**: 用户在 L3 (手机 App) 浏览 L1 市场，点击「购买」。
2. **Auth**: L1 验证支付，向 L2 发送 License Token。
3. **Download**: L2 自动从 L1 拖取技能包到本地仓库 (`/var/jachin/skills`)。
4. **Sync**: L2 推送通知给所有 L3：「新技能已就绪」。
5. **Provision**: 当用户对 L3 喊话使用该技能时，L3 根据 Lifecycle 策略从 L2 拉取执行逻辑。

### 4.2 Cross-Device Execution (跨设备执行)

1. 用户在 **客厅 (Agent A)** 设置闹钟。
2. L2 将闹钟状态写入全局 Redis。
3. 用户走到 **卧室 (Agent B)**，闹钟响起。
4. 用户对 **卧室 (Agent B)** 说「关闭」。
5. Agent B 请求 L2，L2 更新 Redis 并停止所有相关 Agent 的铃声。

---

## 5. 术语映射 (Terminology Mapping)

| 本规范 | 项目内常用 | 说明 |
|--------|------------|------|
| Layer 1 | Tier 1 / Jachin Market | 云端 |
| Layer 2 | Tier 2 / Jachin Hive | 边缘/蜂巢 |
| Layer 3 | Tier 3 / Jachin Terminal | 端侧/终端 |
| Brain/Edge | Hive / Core | 控制中心 |
| Agent | Terminal / Client | 客户端 |

---

## 6. 相关文档

- [architecture.md](./architecture.md) - v3.2 架构设计（详细版）
- [whitepaper_v4.0_swarm.md](./whitepaper_v4.0_swarm.md) - v4.0 蜂群智能白皮书
- [TECHNICAL_SPECIFICATIONS.md](./TECHNICAL_SPECIFICATIONS.md) - 技术规范（DB Schema 等）
