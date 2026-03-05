# 02 — 框架架构 (The Trinity + Neural Bus)

**文档类型**: 白皮书 · 框架架构  
**版本**: V2  
**基准**: [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md)

---

## 〇、 Platform First（平台优先原则）

**Layer 1 默认为官方托管的多租户 SaaS 平台。** 个人、家庭和企业用户开箱即用，只需在边缘端拉起 Layer 2/3 并连接到云端即可。用户账户、技能订阅、付费账单均在 Layer 1 平台统一管理。

**私有化部署（Self-Hosted Layer 1）** 仅作为强合规（政企、金融等）场景的 fallback 方案，不作为代码和默认设计的出发点。

---

## 一、 三位一体架构 (The Trinity Architecture)

Jachin Nexus 采用严格的“重云轻端”三位一体设计，彻底摒弃了上一代笨重的微服务网格。

```text
Layer 1 (平台) ↔ Layer 2 (控制面) ↔ Layer 3 (单体执行节点)

1. Layer 1: Jachin Nexus (平台)
定位: 用户主账号注册/登录，平台主账号管理平台内部。与 L2/L3 无直接耦合。

特性: 绝对不存储边缘节点隐私记忆。用户主账号在平台注册后，管理其自己的 L2 + L3 系统。

核心组件: The Forge (图形化编排)、Fleet Management (舰队批量下发)、Universal Message Adapter (全渠道 Webhook 统一适配)。

数据底座: Drizzle ORM + PostgreSQL（去 BaaS 化 P0 已落地），Auth.js 身份认证，统管全网设备心跳、AST 蓝图、JPP 插件元数据及跨网指令队列。详见 [09_DE_BAASIFICATION.md](./09_DE_BAASIFICATION.md)。

2. Layer 2: 控制面 (V2)
定位: 子账号（在 L2 创建）、权限、API Key 管理（密文下发）、记忆、梦境、L3 协同调度。**不代理 L3 的推理请求**。

特性: SQLite (~/.jachin/l2_control.db)、零信任密钥流转、梦境优化、L3 协同。

技术栈: Python 3.10+、FastAPI、cryptography。

3. Layer 3: 单体执行节点 (V2) — **对标 OpenClaw**
定位: 完整执行节点。持密文 Key，解密后直连外部 API；多 Agent、多 Skill、本地记忆。

特性: **L3 单体** = Agent + Skill + 直连 LLM。入口：Tauri 桌面端、IM、CLI。可与 L2 同机部署。

技术栈: Tauri v2 + Rust + React。语音: Porcupine/Snowboy + Whisper + Kokoro/XTTS。
```

**未来升维**：控制面与数据面分离。局域网 mDNS 零配置直连、广域网 WebRTC P2P 打洞，Layer 1 仅作信令。详见 [10_CONTROL_DATA_PLANE.md](./10_CONTROL_DATA_PLANE.md)。

---

## 二、 双轨制执行引擎 (Dual-Track Engine)

**V2**：执行引擎在 **Layer 3**。L3 打破“万物皆需编译 Wasm”的设定，升级为三轨道：

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
3. **IM 跨网直达**：手机 → Layer 1 Webhook (Universal Adapter) → 队列 → Jachin Mesh 推送（过渡期：心跳拉取；P0：WS 长连）→ Agent Loop → Callback → 手机。同机/同网/广域网原生客户端则直连 Layer 2，详见 [LAYER3_L2_WAN_ARCHITECTURE.md](../LAYER3_L2_WAN_ARCHITECTURE.md)。
4. **持久化感官总线**：OmniSensoryBus 底层挂载 SQLite 队列，确保进程重启不丢事件。
5. **v8.0 边缘网格计算 (Edge Mesh Swarm)**：同 Layer 2 网络下的多个 Layer 3 设备可形成**算力集群**。高负载任务（如 4K 视频压缩）由 Layer 2 向局域网内设备广播「谁空闲？」，空闲设备认领任务、执行 WASM 技能、回传结果。Jachin 升级为家庭/企业级私有云计算集群。

---

## 六、 废弃清单 (Architectural Purge)

❌ Dapr & Ray Cluster  
❌ 本地 Redis / PostgreSQL  
❌ Qdrant（已由 LanceDB + 可插拔向量引擎取代）  
❌ “万物皆 Wasm”的单一形态（现为双轨制）
