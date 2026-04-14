# Jachin 文档

**版本**: V2 (2026-03)  
**架构**: 云边协同数字发行操作系统 (Cloud-Edge AI OS)

---

## 核心文档

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | **架构规范** — 一店一库、四大原语、三层架构、关键组件 |
| [architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md](./architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md) | **L3 执行主轴 SSOT** — 单 ReAct 循环、网关/语义层、SOP、内联 Critic、Experience RAG、与 delegate/coordinate 关系 |
| [Jachin 视角的「四大原语」终极架构规范.md](./Jachin%20视角的「四大原语」终极架构规范.md) | **术语 SSOT** — Tools / MCP / Skills / Agent Tasks 定义与代码落点（索引：[FOUR_PRIMITIVES.md](./FOUR_PRIMITIVES.md)） |
| [FILE_STRUCTURE.md](./FILE_STRUCTURE.md) | 项目文件结构 |
| [CLOUD_EDGE_AI_OS_IMPLEMENTATION_ANALYSIS.md](./CLOUD_EDGE_AI_OS_IMPLEMENTATION_ANALYSIS.md) | 实现度分析 — 三大场景、缺口与风险 |

---

## 快速开始

- [QUICKSTART.md](./QUICKSTART.md) — 3 分钟启动

---

## 规范与指南

| 文档 | 说明 |
|------|------|
| [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) | **L2↔L3** 零信任配对（非 L1↔L3） |
| [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) | 信任链与心跳 |
| [LAYER3_L2_WAN_ARCHITECTURE.md](./LAYER3_L2_WAN_ARCHITECTURE.md) | L2↔L3 广域网（L3–L2 数据面） |
| [L1_L2_L3_END_TO_END_FLOW.md](./L1_L2_L3_END_TO_END_FLOW.md) | 端到端流程与 503 排查 |
| [L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md) | **L1↔L2 配对**：网关 L1 邮箱+密码、Nexus Web 绑定、6 位码 CLI（辅）、热启与控制台诊断 |
| [ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md](./ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md) | **权威**：L1 工作区 · L2 网关权限 · **L2↔L3** 配对与现行实现摘要 |
| [MCP_SPEC.md](./MCP_SPEC.md) | MCP 协议 |
| [MCP_EXECUTION_MODEL.md](./MCP_EXECUTION_MODEL.md) | MCP：L3 stdio、L2 TaskManager、Task Token、Pull / HTTP 降级（v2.2） |
| [ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](./ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md) | **（设计规格 v0.4）** L3 stdio + L2 TaskManager；Task Token / 租户边界 / LOCAL_PINNED；拉取模型与未决项见文内 |
| [SKILL_MD_SPEC.md](./SKILL_MD_SPEC.md) | Skill 规范 |
| [SKILL_MCP_FLOW_AND_RECENT_CHANGES.md](./SKILL_MCP_FLOW_AND_RECENT_CHANGES.md) | **Skill/MCP 流转与近期变更** — 云端上传、三层流转、最新代码说明 |
| [SKILL_MCP_UPLOAD_SPEC.md](./SKILL_MCP_UPLOAD_SPEC.md) | **Skill/MCP 上传规范** — 配置随包、订阅下载后可安装到目标机 |
| [ADMIN_PLUGIN_MANAGEMENT_API.md](./ADMIN_PLUGIN_MANAGEMENT_API.md) | **插件管理 API** — L1/L2 删除与隐藏 |
| [PLUGIN_SECURITY_SANDBOX.md](./PLUGIN_SECURITY_SANDBOX.md) | 插件沙箱 |
| [VOICE_AND_TTS_GUIDE.md](./VOICE_AND_TTS_GUIDE.md) | 语音与 TTS |
| [TESTING_GUIDE.md](./TESTING_GUIDE.md) | 测试指南 |
| [L3_LARK_CONFIG_SINGLE_SOURCE.md](./L3_LARK_CONFIG_SINGLE_SOURCE.md) | **L3 × 飞书**：`plugin/.env`、`im_channels.yaml`、终端 WS、工具发信 — **单一说明** |

---

## 智能化与招聘

| 文档 | 说明 |
|------|------|
| [JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md) | **OpenClaw 对比** — 记忆、任务执行、路线图 |
| [JACHIN_VS_CLAUDE_CODE_ARCHITECTURE.md](./JACHIN_VS_CLAUDE_CODE_ARCHITECTURE.md) | **Claude Code 对照** — 上下文/记忆/Agents/MCP；与现行实现对齐 |
| [前台闲聊与后台重负荷任务的物理隔离与背压熔断.md](./前台闲聊与后台重负荷任务的物理隔离与背压熔断.md) | **L3 单一事实来源**：前台超时、后台队列、`submit/check`、prefetch、规划链、WS 事件 |
| [L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md](./L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md) | **L3 执行面深度说明**：主/子/后台 Agent、消息与 metadata、多路记忆、Prompt 前后缀与门禁 |
| [JACHIN_SAFETY_LOCK.md](./JACHIN_SAFETY_LOCK.md) | **安全锁**：`JACHIN_SAFETY_LOCK.md` 与 MEMORY 分离、prompt 高优先级注入、`core:safety_lock_append` 受控追加 |
| [JACHIN_SAFETY_LOCK_LEARNING.md](./JACHIN_SAFETY_LOCK_LEARNING.md) | **安全锁学习逻辑**：架构图、写入决策流、与对话时序、与记忆优先级对照 |
| [L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md](./L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md) | **薄弱点与治理路线**：并发/状态、Agent 嵌套与观测、上下文缓存与去重、记忆多源、Prompt 耦合 |
| [L3_AMBIGUOUS_INTENT_ARCHITECTURE.md](./L3_AMBIGUOUS_INTENT_ARCHITECTURE.md) | **模糊/不标准用户指令**：精确→模糊澄清→澄清态门控→网关 OOD/语义闸→ReAct 分工与扩展清单 |
| [L3_FUZZY_INTENT_CLARIFICATION.md](./L3_FUZZY_INTENT_CLARIFICATION.md) | **L3 模糊遥控澄清框架**：`ClarificationRule`、HR 插件、冷却与数据流 |
| [INTELLIGENCE_UPGRADE_OVERVIEW.md](./INTELLIGENCE_UPGRADE_OVERVIEW.md) | **P0 智能化落地** — L3 本地记忆、pre-reset flush、梦境阈值、task_plan 三文件 |
| [HR_RECRUITMENT.md](./HR_RECRUITMENT.md) | **HR 招聘当前架构** — MCP 包、DAG、调度、数据路径（单一事实来源） |

---

## 业务模块文档（独立 Skill）

| 文档 | 说明 |
|------|------|
| [bi_daily_report/](./bi_daily_report/) | **每日 BI 深度分析战报** — 契约、设计、并行开发指南、白皮书 |

---

## 愿景

| 文档 | 说明 |
|------|------|
| [VISION.md](./VISION.md) | 产品愿景 |

---

## 历史参考（已归档）

以下文档保留供参考，当前实现以 [ARCHITECTURE.md](./ARCHITECTURE.md) 为准：

- [ARCHITECTURE_V2_LAYER3_STANDALONE.md](./ARCHITECTURE_V2_LAYER3_STANDALONE.md) — V2 详细设计
- [V2_ARCHITECTURE_DIAGRAM.md](./V2_ARCHITECTURE_DIAGRAM.md) — 架构图
- [whitepaper/](./whitepaper/) — 白皮书系列
