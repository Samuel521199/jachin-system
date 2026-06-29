# 01 — 设计目的与定位

**文档类型**: 白皮书 · 设计目的  
**版本**: V2.3  
**更新日期**: 2026-06  
**基准**: [ARCHITECTURE.md](../ARCHITECTURE.md)

---

## 一、系统为何存在

当前 AI Agent 的发展陷入两个极端：

1. **纯云端 API（如 ChatGPT）**：缺乏对本地文件、内网设备和物理世界的控制力，隐私与合规风险高。
2. **重度极客开源框架（如 OpenClaw）**：宿主机权限过大、CLI 部署门槛高、第三方脚本裸跑，难以企业级批量管控。

**Jachin Nexus** 要构建的是：**拥有企业级舰队管控、零信任沙箱、云边分治、且执行面在本机 L3 的 AI 操作系统**。

---

## 二、核心价值主张

### 1. 零摩擦接入（Zero-Friction UX）

- L2 网关 Web 绑定 + L3 桌面 **RSA 零信任配对**（`PAIRING_PROTOCOL_SPEC.md`）。
- Tauri 桌面壳静默拉起 `python -m l3_node`，Omni 经 `ws://127.0.0.1:18981/sensory` 连接。
- 普通用户可在数分钟内完成「L1 账号 → L2 网关 → L3 执行面」闭环。

### 2. 分层安全（四大原语）

| 原语 | 信任级别 | 说明 |
|------|----------|------|
| **MCP** | 高信任本机/边缘 | stdio 外挂，继承全球 MCP 生态 |
| **Skills** | 用户可控 | `SKILL.md` 声明式 SOP，热加载 |
| **Tools · jpp** | 零信任 | Wasm 沙箱 + 燃料熔断 |
| **Agent Tasks** | 独立预算 | delegate / 后台任务 / coordinate |

**术语 SSOT**：[Jachin 视角的「四大原语」终极架构规范.md](../Jachin%20视角的「四大原语」终极架构规范.md)

### 3. 一店一库，云边分治

- **L1**：商城、订阅、License、组织与舰队元数据；**不**跑用户推理。
- **L2**：企业数字仓库 + 控制面；同步 L1 制品、RBAC、API Key 保险箱、MCP 委托。
- **L3**：**单主轴 ReAct**（`run_agent`）+ 直连 LLM + 本地 Memory Nexus。

### 4. 企业级舰队统治力

L1 控制台按 **组织** 管理 `edge_agents`、`device_groups`、蓝图下发；L2 同步 manifest 到 `~/.jachin/inventory/`；L3 从 L2 拉取 Skill/MCP 到本地缓存执行。

---

## 三、对标 OpenClaw

| 维度 | OpenClaw（极客单兵） | Jachin Nexus（企业航母） |
|------|---------------------|-------------------------|
| **执行位置** | 单机 ReAct | **L3 本机 ReAct** + L2 控制面 |
| **安全性** | 脚本裸跑、供应链风险 | **四大原语分层** + Wasm 沙箱 |
| **生态** | 大量 community skills | **MCP + SKILL.md + JPP 商城** |
| **交互** | 寄生于 IM | **桌面 Omni + Lark/Telegram + Voice** |
| **大规模管理** | 无 | **组织 · 舰队 · 设备组 ACL** |
| **记忆** | 各实现不一 | **L3 Memory Nexus** + 可选 L2 LanceDB 梦境 |
| **主动能力** | HEARTBEAT 等 | **后台任务 / zombie 恢复 / 规划中 cron_thinker** |

---

## 四、与 v8.0 叙事的关系

白皮书早期版本使用「v8.0 Singularity OS」营销口径。**现行工程基准为 V2（2026-04+）**：执行面在 L3、L2 不代理推理、组织即租户。v8.0 中仍有效的概念（Dream Weaver、Omni Bus、四大原语）已并入 V2 文档；已废弃概念（L2 ReAct 主循环、Dapr/Ray）见各章「废弃清单」。
