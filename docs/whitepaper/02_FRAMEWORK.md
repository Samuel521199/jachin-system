# 02 — 框架架构 (The Trinity + Neural Bus)

**文档类型**: 白皮书 · 框架架构  
**版本**: v6.0

---

## 一、 三位一体架构 (The Trinity Architecture)

Jachin Nexus 采用严格的“重云轻端”三位一体设计，彻底摒弃了上一代笨重的微服务网格。

```text
Layer 1 (云端调度枢纽) ↔ Layer 2 (神经中枢总线) ↔ Layer 3 (全息感知外壳)

1. Layer 1: Jachin Nexus (数字孪生云端)
定位: 免密、可视化、主导资产确权与指令下发的全局指挥大盘。

特性: 绝对不存储边缘节点隐私记忆。它是边缘节点的“DNA 库”与“调度室”。

核心组件: The Forge (图形化编排)、Fleet Management (舰队批量下发)、Universal Message Adapter (全渠道 Webhook 统一适配)。

数据底座: Supabase (Managed PostgreSQL)，统管全网设备心跳、AST 蓝图、JPP 插件元数据及跨网指令队列。

2. Layer 2: Edge Agent (神经中枢总线)
定位: 极度稳定、极度轻量的执行引擎。90% 能力下放给 Skills，自身永不崩溃。

特性: 双轨制执行引擎 (MCP + SKILL.md + Wasm)、量子记忆 (Vector SQLite + 梦境)、自我修复、生物钟主动心跳。

数据底座: SQLite (memory.db) + sqlite-vss/lancedb 扩展，单文件百万级 Token 语义检索。

技术栈: Python 3.10+ (asyncio, httpx, openai) + wasmtime + sqlite3 + MCP Client。

3. Layer 3: Jachin Terminal (全息感知外壳)
定位: 零摩擦外壳 + 全息感官。扫码配对、Voice Wake (Hey Jachin)、jachin-cli。

特性: Tauri 桌面端 + 唤醒词监听 (Porcupine/Snowboy) + Whisper STT + TTS 播报。极客可用 `jachin-cli pair`、`jachin-cli shell` 获得终端级控制。

技术栈: Tauri v2 + Rust + React。语音: Porcupine/Snowboy + Whisper + Kokoro/XTTS。
```

---

## 二、 双轨制执行引擎 (Dual-Track Engine)

Layer 2 打破“万物皆需编译 Wasm”的设定，升级为三轨道：

| 轨道 | 形态 | 信任级别 | 用途 |
|------|------|----------|------|
| **A** | MCP (Model Context Protocol) | 高信任 | 文件、Shell、PostgreSQL、Git 等开箱工具 |
| **B** | SKILL.md 声明式技能 | 用户可控 | skills_repo/ 下 Markdown，热加载 |
| **C** | The Abyss Wasm 沙箱 | 零信任 | 商城第三方付费插件，燃料熔断 |

详见 `docs/MCP_SPEC.md`、`docs/SKILL_MD_SPEC.md`、`docs/whitepaper/08_JPP_SDK_AND_SKILLS.md`。

---

## 三、 量子记忆与自我进化 (Quantum Memory)

### 3.1 轻量化向量

- 不引入 Redis/Pinecone。在 SQLite 中加载 **sqlite-vss** 或 **lancedb** 扩展。
- 单文件，百万级 Token 极速语义检索。

### 3.2 自我修复 (Self-Healing)

- Agent 调用工具报错时，ReAct 循环捕获 Exception，将错误日志作为 Observation 喂给大脑。
- Agent 自动调整参数、重试。
- 梦境阶段可生成 `bug_fix.md` 规则写入长期记忆，确保同样错误不再犯。

### 3.3 生物钟主动心跳 (Bio-Rhythm Proactivity)

- 脱离云端的 **cron_thinker** 异步线程，每 30 分钟主动环顾。
- 扫描系统日志、读取未读邮件，发现异常时通过 IM 推送报警。
- 与 10s 云端心跳拉取并行，互为补充。

---

## 四、 全息感知器官 (Jarvis Protocol)

### 4.1 视觉与文本流 (Omni-Channel)

- Layer 1 **Universal Message Adapter**：Discord、Slack、WhatsApp、iMessage 等 Webhook 统一清洗成 Jachin Message 格式入队。
- 核心逻辑只写一次，渠道无限扩展。

### 4.2 听觉流 (Voice & Wake-word)

- Layer 3 集成 Porcupine/Snowboy 唤醒词。
- “Hey Jachin” → 录音 → Whisper STT → Layer 2 Agent → TTS 播报。复刻钢铁侠 Jarvis 体验。

### 4.3 极客视觉流 (Cyber-CLI)

- `jachin-cli pair`：配对授权
- `jachin-cli shell`：终端流光溢彩，满足顶尖黑客控制欲

---

## 五、 核心通信与调度拓扑

1. **边缘心跳驱动 (10s)**：Layer 2 POST `/api/v1/agents/heartbeat` 拉取 blueprint、task。
2. **生物钟 cron_thinker (30min)**：本地主动环顾，无需云端。
3. **IM 跨网直达**：手机 → Layer 1 Webhook (Universal Adapter) → 队列 → Layer 2 心跳 → Agent Loop → Callback → 手机。

---

## 六、 废弃清单 (Architectural Purge)

❌ Dapr & Ray Cluster  
❌ 本地 Redis / PostgreSQL  
❌ Qdrant（已由 Vector SQLite 取代）  
❌ “万物皆 Wasm”的单一形态（现为双轨制）
