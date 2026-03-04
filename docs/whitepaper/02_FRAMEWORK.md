# 02 — 框架架构 (The Trinity + Neural Bus)

**文档类型**: 白皮书 · 框架架构  
**版本**: v8.0 (The Singularity OS)

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

特性: 双轨制执行引擎 (MCP + SKILL.md + Wasm)、量子记忆 (LanceDB + 可插拔向量引擎 + 梦境)、自我修复、生物钟主动心跳。

数据底座: SQLite (memory.db) + LanceDB (vector_db/) + 可插拔向量引擎 (Cloud/Edge)，单文件百万级 Token 语义检索。

技术栈: Python 3.10+ (asyncio, httpx, openai) + wasmtime + sqlite3 + MCP Client。  
认知引擎: **Cognitive Swarm (虫群心智)** — LiteLLM 抹平大模型差异，支持 100+ 模型无缝切换；Router Agent 多意图分发。

3. Layer 3: Jachin Terminal (全息感知外壳) — **多态客户端**
定位: 零摩擦外壳 + 全息感官。扫码配对、Voice Wake (Hey Jachin)、jachin-cli。

特性: **Layer 3 是多态的**，涵盖 PC 桌面端、移动端、树莓派、无屏设备。客户端接入 WebSocket 时必须进行 **能力协商 (Capability Negotiation)**：发送 Manifest 声明自身能力（如 `ui_render`、`hitl_popup`、`gpio_control`、`audio_play`）。桌面精灵只是一种可选的视觉 Skill/Client。Layer 2 根据客户端能力标签，动态决定推送什么消息（UI 动画只推给有屏设备，HITL 只推给能弹窗的设备）。

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

### 3.1 轻量化向量 + 可插拔 Embedding

- 不引入 Redis/Pinecone。在 SQLite 中加载 **sqlite-vss** 或 **lancedb** 扩展。
- 单文件，百万级 Token 极速语义检索。
- **可插拔向量引擎**：`embedding_mode: "cloud"` (OpenAI) 或 `"local"` (ONNX 断网可用)，由 Layer 3 设置界面 "Local AI Mode" 开关控制。详见 `docs/whitepaper/PLUGGABLE_VECTOR_ENGINE.md`。

### 3.2 自我修复 (Self-Healing)

- Agent 调用工具报错时，ReAct 循环捕获 Exception，将错误日志作为 Observation 喂给大脑。
- Agent 自动调整参数、重试。
- 梦境阶段可生成 `bug_fix.md` 规则写入长期记忆，确保同样错误不再犯。

### 3.3 生物钟主动心跳 (Bio-Rhythm Proactivity)

- 脱离云端的 **cron_thinker** 异步线程，每 30 分钟主动环顾。
- 扫描系统日志、读取未读邮件，发现异常时通过 IM 推送报警。
- 与 **Jachin Mesh** 双向长连并行，互为补充。

---

## 四、 全息感知器官 (Jarvis Protocol) + 全息感官总线

**端口-适配器架构**：Voice、Sprite、IM 作为外接感官，统一归一化为 `Event(source, intent, payload)`，经 **Omni-Sensory Bus** 送入大脑；输出按 source 多路分发。详见 `docs/whitepaper/OMNI_SENSORY_BUS.md`。

### 4.1 感官一：Jachin Voice (听觉与喉咙)

- 空间级交互 (Ambient Computing)。Porcupine 唤醒词
- “Hey Jachin” → 录音 → Whisper STT → 总线 → Agent → TTS 播报。无屏幕亦可运行（树莓派 + 麦克风 + 音箱）。

### 4.2 感官二：Jachin Sprite (桌面精灵与外壳)

- Tauri 控制台 + 透明窗口 Live2D/3D 精灵。企业级扫码即连、静默拉起 Layer 2。
- 感官联动：`[Thought]` → 托腮思考；`core:shell_exec` → 挥动手臂。

### 4.3 感官三：Jachin Link (全息通讯网关)

- Layer 1 **Universal Message Adapter**：Telegram、飞书、Slack 等 Webhook 统一清洗入队。
- **Jachin Mesh** 双向长连 → 毫秒级指令下发 → `emit_omni_input("telegram", ...)` → Agent → HTTP Callback 回传手机。

### 4.4 极客视觉流 (Cyber-CLI)

- `jachin-cli pair`：配对授权
- `jachin-cli shell`：终端流光溢彩，满足顶尖黑客控制欲

---

## 五、 核心通信与调度拓扑

1. **Jachin Mesh**：基于 WebSocket 的双向长连通道，实现 Layer 1 到 Layer 2 的**毫秒级指令下发**，替代 10 秒 HTTP 轮询。
2. **生物钟 cron_thinker (30min)**：本地主动环顾，无需云端。
3. **IM 跨网直达**：手机 → Layer 1 Webhook (Universal Adapter) → 队列 → Jachin Mesh 推送 → Agent Loop → Callback → 手机。
4. **持久化感官总线**：OmniSensoryBus 底层挂载 SQLite 队列，确保进程重启不丢事件。
5. **v8.0 边缘网格计算 (Edge Mesh Swarm)**：同 Layer 2 网络下的多个 Layer 3 设备可形成**算力集群**。高负载任务（如 4K 视频压缩）由 Layer 2 向局域网内设备广播「谁空闲？」，空闲设备认领任务、执行 WASM 技能、回传结果。Jachin 升级为家庭/企业级私有云计算集群。

---

## 六、 废弃清单 (Architectural Purge)

❌ Dapr & Ray Cluster  
❌ 本地 Redis / PostgreSQL  
❌ Qdrant（已由 LanceDB + 可插拔向量引擎取代）  
❌ “万物皆 Wasm”的单一形态（现为双轨制）
