# Jachin 文档

**版本**: V2 (2026-03)  
**架构**: 云边协同数字发行操作系统 (Cloud-Edge AI OS)

---

## 核心文档

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | **架构规范** — 一店一库、双轨制、三层架构、关键组件 |
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

---

## 智能化与招聘

| 文档 | 说明 |
|------|------|
| [JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md](./JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md) | **OpenClaw 对比** — 记忆、任务执行、路线图 |
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
