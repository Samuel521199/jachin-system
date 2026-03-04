# 06 — Layer 2: Edge Agent (神经中枢总线)

**文档类型**: 白皮书 · Layer 2 详细说明  
**版本**: v6.0 (The Neural Bus Edition)

---

## 一、 定位与职责 (Positioning & Philosophy)

Layer 2 彻底退化为**极度稳定、极度轻量的神经中枢总线**。90% 能力下放给 Skills，自身永不崩溃。

它没有 UI，不保存全局数据库，所有的数字孪生状态全部托管给 Layer 1。
Layer 2 的唯一使命：**保持心跳拉取指令、运行双轨制执行引擎、通过 ReAct 循环自主思考、在量子记忆中进化、在深夜通过梦境提纯、在生物钟中主动环顾。**

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

### 3.1 引擎心脏 (Daemon Loop)

* **组件**: `core/daemon.py`
* **职责**: 维持系统的基础生命体征。每 10 秒向 Layer 1 发送 `POST /api/v1/agents/heartbeat`，拉取 blueprint、task。
* **并行**: 与 `cron_thinker` 生物钟线程并行运行。

### 3.2 生物钟 (cron_thinker)

* **组件**: `core/cron_thinker.py`
* **职责**: 脱离云端，每 30 分钟主动环顾。扫描系统日志、读取未读邮件，发现异常时通过 IM 推送报警。
* **配置**: 可设定检查清单（如 `HEARTBEAT.md` 式任务列表）。

### 3.3 前额叶皮层 (ReAct Agent Loop)

* **组件**: `core/agent_loop.py`
* **职责**: 认知路由。依据 MCP 工具、SKILL.md、Wasm 插件进行自主规划。
* **机制**: `[Thought]` -> `[Action]` (MCP/SKILL/Wasm) -> `[Observation]` -> `[Final Answer]`，支持自我修复 (Self-Healing)。

### 3.4 量子记忆 (Quantum Memory)

* **组件**: `core/biological_memory.py` + `core/vector_store.py`
* **底层**: SQLite 单文件 + sqlite-vss 或 lancedb 扩展。
* **机制**:
    * **海马体**: short_term_logs，24 小时内无损记录。
    * **向量检索**: 百万级 Token 语义检索，补充梦境提纯。
    * **大脑皮层**: core_memory，梦境提纯 Tag + 自我修复规则 (bug_fix.md)。
    * **自我修复**: 工具报错时，错误日志作为 Observation 喂给大脑；梦境阶段可生成 bug_fix 规则。

### 3.5 梦境引擎 (The Dream Sequence)

* **组件**: `core/dreamer.py`
* **调度**: 每日凌晨 3 点，或可配置 cron。
* **机制**: 海马体数据 → LLM 提纯 → 写入 core_memory。

---

## 四、 配置与隐形化管理

* **配置文件**: `~/.jachin/nexus_config.json`
* **MCP 配置**: `~/.jachin/mcp_servers.json`（可选）
* **写入方式**: Layer 3 扫码或 `jachin-cli pair` 后自动生成。

---

## 五、 启动方式

| 模式 | 场景 | 说明 |
|------|------|------|
| **静默唤醒** | C 端/企业 | Layer 3 Tauri 扫码后静默拉起，无黑框。 |
| **极客模式** | 开发者 | `jachin-cli shell` 或 `.\scripts\start-layer2.ps1`，终端流光溢彩。 |

---

## 六、 v6.0 废弃声明

1. **❌ 废弃“万物皆 Wasm”**：现为双轨制，MCP/SKILL.md 与 Wasm 并存。
2. **❌ 废弃 Qdrant**：由 Vector SQLite (sqlite-vss/lancedb) 取代。
3. **❌ 废弃纯被动心跳**：增加 cron_thinker 生物钟主动环顾。
