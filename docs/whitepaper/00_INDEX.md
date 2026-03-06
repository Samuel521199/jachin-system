# Jachin Nexus 白皮书 — 文档索引

**版本**: V2  
**更新日期**: 2026-03  
**核心基调**: L1 平台 / L2 零信任控制面 / L3 单体执行面（对标 OpenClaw）

> **V2 架构规范**：[ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md) — L2 不代理推理，L3 持密文 Key 直连 API  
> **架构图**：[V2_ARCHITECTURE_DIAGRAM.md](../V2_ARCHITECTURE_DIAGRAM.md)

---

## ⚠️ 架构宪法 (The Constitution)
致所有阅读此文档的开发者与 AI 编程助手（如 Cursor）：
1. 本项目已**全面弃用** Dapr、Ray 集群、本地 PostgreSQL（L2）和复杂 Docker 编排。**Redis** 仅 L2 集群化时可选使用。
2. **V2**：L2 为**控制面**（子账号、权限、记忆、API Key 管理）；**执行引擎在 L3**（MCP + SKILL.md + Wasm）。
3. **轨道 A (MCP)**：供高信任本地环境，继承全球 AI 工具生态，开箱即用。
4. **轨道 B (SKILL.md)**：`skills_repo/` 下声明式 Markdown 技能，热加载，零编译。
5. **轨道 C (Wasm)**：商城下载的第三方付费插件，必须在 The Abyss 沙箱中运行。
6. 记忆系统：**量子记忆** = 生物学梦境 + LanceDB + **可插拔向量引擎** (Cloud/Edge)，支持自我修复 (Self-Healing)。
7. **Jachin Mesh**：基于 WebSocket 的双向长连，实现 Layer 1 到 Layer 2 的**毫秒级指令下发**。**废弃 10 秒 HTTP 轮询**。
8. **生物钟 cron_thinker**：脱离云端，每 30 分钟主动环顾（系统日志、未读邮件、异常报警）。
9. 全息感知：**全息感官总线 (Omni-Sensory Bus)** 端口-适配器架构，**持久化 SQLite 队列**；**Animus Protocol** (pvporcupine 唤醒词)；**Layer 3 视觉投射** (Daemon WebSocket 广播)；Layer 1 **Universal Message Adapter**；**jachin-cli**。
10. **Cognitive Swarm**：LiteLLM 抹平大模型差异，支持 100+ 模型；**Aegis**：OpenTelemetry 遥测 + Prompt 注入拦截墙。
11. 设备鉴权、配对：V2 L3 桌面端走 L2 网关零信任（RSA 双盲）；Layer 2 daemon 走 L1 6 位码或 `jachin-cli pair`。
12. **v8.0 升维**：**Session Multiplexing**（按 session_id 隔离 Agent Actor）；**Nexus Hook Pipeline**（洋葱中间件，pre_tool_exec/post_tool_exec）；**Dream Weaver**（梦境重塑、记忆去重、冲突消解）；**Capability Negotiation**（Layer 3 多态，接入时发送 Manifest）；**Edge Mesh Swarm**（同网设备算力协同、任务认领）；**全链路 runId 追踪**（Distributed Tracing，贯穿 SensoryInputEvent → PipelineContext → SensoryOutputEvent，日志染色）；**流式神经**（Streaming Chunk，LLM 逐 token 推送到 caps 含 `stream_chunk` 的客户端）。
13. **控制面与数据面分离**（规划）：Layer 1 仅负责鉴权、计费、信令；感官数据优先 P2P 直连。局域网 mDNS 零配置发现，广域网 WebRTC 打洞。能直连绝不绕路。详见 [10_CONTROL_DATA_PLANE.md](./10_CONTROL_DATA_PLANE.md)。

---

## 文档列表

| 序号 | 文档 | 内容概要 |
| — | [**V2 架构规范**](../ARCHITECTURE_V2_LAYER3_STANDALONE.md) | L2 控制面、L3 单体、API Key 零信任流转 |
| — | [**V2 架构图**](../V2_ARCHITECTURE_DIAGRAM.md) | Mermaid 流程图、时序图、文件结构 |
|------|------|----------|
| 01 | [设计目的](./01_DESIGN_PURPOSE.md) | Jachin 解决什么问题、B2B/B2C 定位（对标 OpenClaw） |
| 02 | [框架架构](./02_FRAMEWORK.md) | 三位一体 + 双轨制引擎 + 量子记忆 + 全息感知 |
| 03 | [业务流程](./03_WORKFLOW.md) | 扫码/CLI 配对、心跳、cron_thinker、ReAct、Voice Wake |
| 04 | [文件结构](./04_FILE_STRUCTURE.md) | 纯净目录树、core/、scripts/、~/.jachin/、v8.0 新增模块 |
| 05 | [Layer 1 云端中枢](./05_LAYER1_NEXUS.md) | 免密登录、舰队、Forge、Universal Message Adapter |
| 06 | [Layer 2 控制面](./06_LAYER2_EDGE.md) | V2：子账号、权限、API Key 管理、记忆、L3 调度（不代理推理） |
| — | [可插拔向量引擎](./PLUGGABLE_VECTOR_ENGINE.md) | Cloud/Edge 双核、策略模式、Local AI Mode |
| — | [可插拔认知引擎](./PLUGGABLE_COGNITIVE_ENGINES.md) | 大小脑动态路由、瀑布流密钥、模型非 Skill |
| — | [全息感官总线](./OMNI_SENSORY_BUS.md) | 端口-适配器、Voice/Sprite/IM 归一化、输出多路分发 |
| 07 | [Layer 3 单体执行节点](./07_LAYER3_TERMINAL.md) | V2：对标 OpenClaw，Agent + Skill + 直连 LLM |
| 08 | [JPP 与技能生态](./08_JPP_SDK_AND_SKILLS.md) | JPP (轨道 C)、MCP (轨道 A)、SKILL.md (轨道 B) |
| 09 | [**去 BaaS 化战役**](./09_DE_BAASIFICATION.md) | Auth.js、Drizzle ORM、Redis、MinIO、Helm — Layer 1 绝对主权架构 |
| 10 | [**控制面与数据面分离**](./10_CONTROL_DATA_PLANE.md) | mDNS 局域网直连、WebRTC P2P 打洞、信令分离 — 能直连绝不绕路 |
| — | [**v8.0 架构升维**](./V8_SINGULARITY_OS.md) | ⚠️ 历史参考。Session Multiplexing、Nexus Hook、Dream Weaver 等已迁移至 V2 L3 |
| — | [MCP 接入规范](../MCP_SPEC.md) | MCP Client 实现、工具发现与调用 |
| — | [SKILL.md 规范](../SKILL_MD_SPEC.md) | 声明式技能格式、Persona、热加载 |
