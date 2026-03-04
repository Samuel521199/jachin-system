# 06 — Layer 2: Edge Agent (神经中枢总线)

**文档类型**: 白皮书 · Layer 2 详细说明  
**版本**: v8.0 (The Singularity OS)

---

## 一、 定位与职责 (Positioning & Philosophy)

Layer 2 彻底退化为**极度稳定、极度轻量的神经中枢总线**。90% 能力下放给 Skills，自身永不崩溃。

它没有 UI，不保存全局数据库，所有的数字孪生状态全部托管给 Layer 1。
Layer 2 的唯一使命：**通过 Jachin Mesh 接收指令、运行双轨制执行引擎、通过 ReAct 循环自主思考、在量子记忆中进化、在深夜通过梦境提纯、在生物钟中主动环顾。**

---

## 二、 双轨制执行引擎 (Dual-Track Engine)

### 2.1 轨道 A：MCP 宿主 (MCP Host)

* **组件**: `core/mcp_client.py`
* **职责**: 连接 MCP 服务器，发现并注册工具，供 ReAct 循环调用。
* **适用**: 高信任本地环境。文件读写、Shell、PostgreSQL、Git 等开箱即用。
* **参考**: `docs/MCP_SPEC.md`

### 2.2 轨道 B：声明式轻量技能 (SKILL.md)

* **组件**: `core/skill_loader.py`
* **职责**: 监听 `skills_repo/**/SKILL.md`，热加载 Persona 与 MCP 工具链。
* **适用**: 用户可控。丢一个 Markdown 文件，保存即生效。
* **参考**: `docs/SKILL_MD_SPEC.md`

### 2.3 轨道 C：The Abyss Wasm 沙箱

* **组件**: `core/wasm_runner.py`
* **职责**: 商城下载的第三方付费插件，WASI 物理隔离 + 燃料熔断。
* **适用**: 零信任。插件无法窃取宿主机权限。
* **参考**: `docs/whitepaper/08_JPP_SDK_AND_SKILLS.md`

---

## 三、 核心子模块 (Core Anatomy)

### 3.1 引擎心脏 (Daemon Loop) + 全息感官总线

* **组件**: `core/daemon.py` + `core/event_bus.py`
* **职责**: 维持系统的基础生命体征。通过 **Jachin Mesh** (WebSocket) 与 Layer 1 双向长连，毫秒级接收 blueprint、task。
* **全息感官总线**: Jachin Mesh 推送的 task 通过 `emit_omni_input("telegram", ...)` 注入总线；消费循环调用 Agent Loop，输出按 source 多路分发（IM → HTTP Callback，Sprite → 本地 WebSocket 广播）。**持久化**：OmniSensoryBus 底层挂载 SQLite 队列，进程重启不丢事件。详见 `docs/whitepaper/OMNI_SENSORY_BUS.md`。
* **并行**: 与 `cron_thinker` 生物钟线程并行运行。

### 3.2 生物钟 (cron_thinker)

* **组件**: `core/cron_thinker.py`
* **职责**: 脱离云端，每 30 分钟主动环顾。扫描系统日志、读取未读邮件，发现异常时通过 IM 推送报警。
* **配置**: 可设定检查清单（如 `HEARTBEAT.md` 式任务列表）。

### 3.3 前额叶皮层 (ReAct Agent Loop) + Cognitive Swarm (虫群心智)

* **组件**: `core/agent_loop.py` + `core/brain/llm/`
* **职责**: 认知路由。依据 MCP 工具、SKILL.md、Wasm 插件进行自主规划。
* **Cognitive Swarm (虫群心智)**：引入 **LiteLLM** 抹平大模型差异，支持 100+ 模型无缝切换（Claude、GPT、Qwen、Ollama、vLLM 等）。核心引入 **Router Agent** 进行多意图分发，打破单一模型绑定。
* **机制**: `[Thought]` -> `[Action]` (MCP/SKILL/Wasm) -> `[Observation]` -> `[Final Answer]`，支持自我修复 (Self-Healing)。
* **v8.0 Nexus Hook Pipeline（洋葱中间件体系）**：系统必须提供标准生命周期 Hook（`on_intent_received`、`before_llm_think`、`before_tool_exec`、`after_tool_exec`、`before_response`），允许插件无损介入执行流。采用 Koa.js 风格洋葱模型，开发者可注册 Python 函数拦截危险操作（如 rm -rf）或在 `before_response` 时自动将 Markdown 转语音。详见 `docs/whitepaper/V8_SINGULARITY_OS.md`。

### 3.3.1 Aegis 子系统 (安全中枢)

* **组件**: `core/aegis/`（规划）
* **职责**: 引入 **OpenTelemetry** 进行全链路遥测日志记录；增加 **Prompt 注入拦截墙**，防护恶意输入。

### 3.4 量子记忆 (Quantum Memory)

* **组件**: `core/biological_memory.py` + `core/memory_store.py`
* **底层**: SQLite (memory.db) + LanceDB (vector_db/memories)。
* **机制**:
    * **海马体**: short_term_logs，24 小时内无损记录。
    * **向量检索**: 百万级 Token 语义检索，补充梦境提纯。
    * **大脑皮层**: core_memory，梦境提纯 Tag + 自我修复规则 (bug_fix.md)。
    * **自我修复**: 工具报错时，错误日志作为 Observation 喂给大脑；梦境阶段可生成 bug_fix 规则。

### 3.5 全域向量路由 (Semantic Router) + 可插拔向量引擎

* **组件**: `core/vector_router.py` + `core/embedding/`
* **职责**: 基于 LanceDB，Agent 可通过自然语言意图，使用**余弦相似度**“顿悟”并热加载本地技能。
* **可插拔双引擎**:
    * **☁️ 极速云端核 (Cloud)**: `OpenAIEmbedder`，调用 OpenAI/兼容 API，零本地负担。
    * **🛡️ 深渊边缘核 (Edge)**: `ONNXEmbedder`，本地 sentence-transformers (all-MiniLM-L6-v2)，断网可用。
* **配置**: `~/.jachin/nexus_config.json` 中 `embedding_mode: "cloud"` | `"local"`，由 Layer 3 设置界面 **"Local AI Mode"** 开关控制。
* **机制**: `match_local_skill(intent: str)` 将意图经可插拔 Embedder 转为向量，检索 LanceDB `skills` 表，按相似度返回最匹配技能。
* **安全性（红线）**: 若本地未命中，向云端商城请求的“意念下载”**必须经过 Human-in-the-Loop (HITL) 强授权**（桌面弹窗或 IM 确认），**严禁静默下载执行未知云端逻辑**。

* **参考**: `docs/whitepaper/PLUGGABLE_VECTOR_ENGINE.md`

### 3.6 梦境引擎 (The Dream Sequence) + v8.0 Dream Weaver（梦境重塑）

* **dreamer.py**: 海马体 short_term → LLM 提纯 → core_memory。调度：每日凌晨 3 点。
* **dream_weaver.py** (v8.0): LanceDB `memories` 表（is_consolidated 字段）→ LLM 聚类/去重/融合 → 删除旧碎片、写入高密度事实。调度：凌晨 3 点 + 空闲 30min。
* **memory_store.py**: `get_unconsolidated_memories()`、`delete_memories()`、`insert_consolidated_memory()`。
* 若发现记忆冲突，打上「需用户澄清」标签。详见 `docs/whitepaper/V8_SINGULARITY_OS.md`。

### 3.7 v8.0 Edge Mesh Swarm（算力虫群）

* **swarm_registry.py**：任务注册表，register_task / claim_task / resolve_task / await_task_result。
* **swarm_hook.py**：HOOK_BEFORE_TOOL_EXEC 拦截 heavy_tools（video_encode 等），广播 task_offer，挂起等待节点回传。
* **daemon**：订阅 swarm_broadcast，处理 TASK_CLAIM（下发 task_assigned）、TASK_RESULT（resolve）。
* **scripts/mock_worker.py**：工蜂测试脚本，连接 ws://localhost:8080/sensory，声明 worker_video_encode，接单后模拟 10s 回传。
* **配置**：`nexus_config.json` 中 `swarm.heavy_tools` 可扩展重载工具列表。

---

## 四、 配置与隐形化管理

* **配置文件**: `~/.jachin/nexus_config.json`
* **MCP 配置**: `~/.jachin/mcp_servers.json`（可选）
* **写入方式**: Layer 3 扫码或 `jachin-cli pair` 后自动生成。
* **v8.0 swarm**：`swarm.heavy_tools` 可配置需外包至虫群的重载工具列表。

---

## 五、 启动方式

| 模式 | 场景 | 说明 |
|------|------|------|
| **静默唤醒** | C 端/企业 | Layer 3 Tauri 扫码后静默拉起，无黑框。 |
| **极客模式** | 开发者 | `jachin-cli shell` 或 `.\scripts\start-layer2.ps1`，终端流光溢彩。 |

---

## 六、 v8.0 废弃声明

1. **❌ 废弃“万物皆 Wasm”**：现为双轨制，MCP/SKILL.md 与 Wasm 并存。
2. **❌ 废弃 Qdrant**：由 LanceDB + 可插拔向量引擎 (Cloud/Edge) 取代。
3. **❌ 废弃 10 秒 HTTP 轮询**：由 Jachin Mesh (WebSocket) 毫秒级双向长连取代。
4. **❌ 废弃单一模型绑定**：由 Cognitive Swarm (LiteLLM + Router Agent) 取代。
