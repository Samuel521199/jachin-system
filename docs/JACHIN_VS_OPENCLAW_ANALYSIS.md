# Jachin Nexus v6.0 vs OpenClaw 对比分析

**文档类型**: 竞品分析  
**版本**: 2.0 (v6.0 蓝图落地后)  
**基准**: OpenClaw 2026 年 2 月状态（180k+ stars，ClawHavoc 事件后）

---

## 一、架构对比

| 维度 | OpenClaw | Jachin Nexus v6.0 |
|------|----------|-------------------|
| **整体模式** | Hub-and-Spoke（单机中心化） | 三位一体（云边端分离） |
| **控制平面** | Gateway (WebSocket 127.0.0.1:18789) | Layer 1 Supabase + Next.js |
| **智能体运行时** | Agent Runtime (Node.js/Pi Agent Core) | Layer 2 daemon.py + agent_loop.py |
| **通信协议** | WebSocket 实时双向 | HTTP 心跳拉取（10 秒轮询） |
| **部署形态** | 单机 / VPS / Fly.io | 边缘节点 + 云端指挥大盘 |

**结论**：OpenClaw 是单机「个人助理」，Jachin 是「云边协同的企业级舰队」。

---

## 二、功能与能力对比

### 2.1 交互入口

| 能力 | OpenClaw | Jachin Nexus |
|------|----------|--------------|
| **IM 渠道** | 50+（WhatsApp、Telegram、Discord、Slack、iMessage、Signal、Teams 等） | 当前：Telegram Webhook、飞书（规划） |
| **Web 控制台** | 内置 Lit 组件，Gateway 直连 | Layer 1 自有大盘（Forge、舰队） |
| **桌面端** | macOS 菜单栏 App | Tauri 扫码配对、静默拉起 |
| **CLI** | Commander.js 全功能 | 无独立 CLI（依赖 Layer 3） |
| **语音** | Voice Wake (push-to-talk) | 规划中（见 VOICE_AND_TTS_GUIDE） |
| **移动端** | iOS/Android 节点，WebSocket 连接 | 规划中 |

**Jachin 不足**：IM 渠道远少于 OpenClaw，无 iMessage、Discord、Slack 等主流渠道；无语音唤醒；CLI 能力弱。

---

### 2.2 心智与记忆

| 维度 | OpenClaw | Jachin Nexus v6.0 |
|------|----------|-------------------|
| **短期记忆** | 会话内上下文 + 压缩 | short_term_logs (SQLite, 24h) |
| **长期记忆** | MEMORY.md + memory/YYYY-MM-DD.md | core_memory + Vector SQLite (sqlite-vss/lancedb) |
| **人格/偏好** | SOUL.md | 注入 core_memory 的 System Prompt |
| **检索方式** | 向量 + BM25 混合，sqlite-vec | **量子记忆**：向量检索 + 梦境提纯 + 自我修复 |
| **主动行为** | HEARTBEAT.md，30 分钟心跳 | **生物钟 cron_thinker** (30min) + 云端心跳 (10s) |

**Jachin v6.0 优势**：量子记忆（向量 + 梦境 + 自我修复）；cron_thinker 主动环顾；SQLite 单文件。

---

### 2.3 插件与技能

| 维度 | OpenClaw | Jachin Nexus v6.0 |
|------|----------|-------------------|
| **技能形态** | SKILL.md（自然语言指令） | **三轨道**：MCP + SKILL.md + JPP .wasm |
| **执行环境** | 宿主进程 / Docker 沙箱（按会话） | MCP 宿主 + SKILL 热加载 + WASI 沙箱（轨道 C） |
| **生态规模** | ClawHub 5700+ skills（供应链风险） | MCP 开箱继承 + SKILL.md 轻量 + JPP 商业沙箱 |
| **安全机制** | Docker 隔离、无代码签名 | 分轨制：高信任 MCP + 零信任 Wasm |
| **商业模型** | 纯开源 | royalty_fee 版税分润（轨道 C） |

**Jachin v6.0 优势**：双轨制打破“万物皆 Wasm”；MCP 开箱即用；SKILL.md 热加载；轨道 C 仍保持物理隔离。

---

### 2.4 工具与沙箱

| 维度 | OpenClaw | Jachin Nexus v6.0 |
|------|----------|-------------------|
| **内置工具** | 文件、Shell、邮件、浏览器、日历等 | **MCP 开箱**（轨道 A）继承全球生态 |
| **沙箱粒度** | 主会话原生 / DM&群组 Docker | 分轨制：MCP 高信任 + Wasm 零信任 |
| **MCP 支持** | 社区适配器，协议演进中 | **轨道 A 原生 MCP Client** |
| **资源限制** | Docker 可配置 CPU/内存 | 轨道 C Fuel 熔断 |

**Jachin v6.0 优势**：MCP 开箱即用；SKILL.md 轻量扩展；轨道 C 仍保持物理隔离。

---

## 三、安全对比

| 维度 | OpenClaw | Jachin Nexus |
|------|----------|--------------|
| **技能供应链** | ClawHavoc：341–1184 恶意 skill，无签名、无沙箱 | WASM 编译，无法执行任意宿主机代码 |
| **网络暴露** | 默认 127.0.0.1，远程需 SSH/Tailscale | 边缘无公网 IP，心跳拉取 |
| **设备配对** | 加密握手 + 挑战应答 | 扫码 + Layer 3 授权 |
| **提示注入防护** | 上下文隔离 + 建议用顶级模型 | 依赖 System Prompt 设计 |

**Jachin 优势**：技能层零信任，WASM 无法访问宿主机；边缘无公网暴露。  
**OpenClaw 优势**：五层纵深防御文档完善；Docker 沙箱可限制网络/文件系统。

---

## 四、企业级能力对比

| 维度 | OpenClaw | Jachin Nexus |
|------|----------|--------------|
| **多节点管理** | 无，单机单账号 | 舰队指挥大屏，批量下发 AST |
| **热更新** | 手动更新技能/配置 | 云端 AST 蓝图 + 心跳拉取 |
| **权限与审计** | 渠道级 allowlist | 舰队级 + 设备级（规划） |
| **多 Agent 路由** | 支持（按 channel/contact 映射） | 单 Agent 为主 |

**Jachin 优势**：舰队管理、批量部署、AST 热更新是核心差异化。  
**OpenClaw 不足**：无企业级多节点管控。

---

## 五、Jachin v6.0 已解决 / 待完善

### 5.1 v6.0 已解决

1. ~~**IM 渠道单一**~~：Universal Message Adapter 设计完成，可扩展 Discord、Slack、WhatsApp 等。
2. ~~**无向量检索**~~：量子记忆 = Vector SQLite (sqlite-vss/lancedb)。
3. ~~**无 MCP**~~：轨道 A MCP Client 设计完成。
4. ~~**无语音**~~：Voice Wake (Hey Jachin) 设计完成，Porcupine/Snowboy + Whisper + TTS。
5. ~~**无开箱工具**~~：MCP 轨道 A 开箱即用。
6. ~~**主动粒度粗**~~：cron_thinker 生物钟 30min 主动环顾。
7. ~~**技能形态单一**~~：三轨道（MCP + SKILL.md + Wasm）。

### 5.2 待完善

8. **JPP 生态为 0**：商城建设中，MCP/SKILL 可先行冷启动。
9. **jachin-cli**：`pair`、`shell` 需实现。
10. **Universal Message Adapter**：Telegram/飞书已实现，Discord/Slack/WhatsApp 待扩展。
11. **梦境调度**：可配置化（时间、频率）待实现。

---

## 六、v6.0 蓝图落地状态

### 已纳入设计

- ✅ 双轨制引擎（MCP + SKILL.md + Wasm）
- ✅ 量子记忆（Vector SQLite + 自我修复）
- ✅ 生物钟 cron_thinker
- ✅ Universal Message Adapter
- ✅ Voice Wake (Hey Jachin)
- ✅ jachin-cli (pair, shell)

### 实施优先级

- **P0**：MCP Client、SKILL.md 热加载、cron_thinker 基础实现
- **P1**：Vector SQLite、Voice Wake、jachin-cli
- **P2**：Universal Message Adapter 扩展（Discord、Slack、WhatsApp）

---

## 七、总结

|  | OpenClaw | Jachin Nexus v6.0 |
|---|----------|-------------------|
| **定位** | 极客单兵，个人助理 | 企业航母，神经中枢总线 |
| **最大优势** | 渠道多、生态大、产品化成熟 | 分轨制安全、舰队、量子记忆、全息感知 |
| **最大短板** | 技能供应链风险、无企业管控 | 生态建设进行中 |

**Jachin v6.0 的护城河**：双轨制（MCP 开箱 + SKILL 轻量 + Wasm 零信任）+ 舰队管理 + 量子记忆 + 生物钟 + Voice Wake。在 OpenClaw 因 ClawHavoc 暴露供应链脆弱性后，Jachin 的安全与生态叙事全面领先。蓝图落地后，OpenClaw 在 Jachin 面前将像简陋的脚本玩具。
