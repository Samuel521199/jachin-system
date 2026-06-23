# L3 架构薄弱点与治理路线图

> **[已归档]** 本文档为 2026-04-02 快照，记录了当时的薄弱点与分阶段目标。  
> **当前执行状态与完整实现进度** 请以 **[AGI_OPTIMIZATION_ROADMAP.md](./AGI_OPTIMIZATION_ROADMAP.md)** 为准（版本 y，50 项 L3 功能已全部落地）；  
> 本文仅作历史背景参考，**不再随代码更新**。

**版本**: 2026-04-02（快照；后续演进见 AGI_OPTIMIZATION_ROADMAP.md）  
**性质**: 问题诊断 + **分阶段解决方案**（历史记录）。  
**相关**: [L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md](./L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md)、[前台闲聊与后台重负荷任务的物理隔离与背压熔断.md](./前台闲聊与后台重负荷任务的物理隔离与背压熔断.md)、[JACHIN_EXECUTION_RESILIENCE_CONTRACT.md](./JACHIN_EXECUTION_RESILIENCE_CONTRACT.md)、[Jachin 视角的「四大原语」终极架构规范.md](./Jachin%20视角的「四大原语」终极架构规范.md)（Tools/MCP/Skills/Agent Tasks）。

---

## 〇、实现快照（与仓库同步，阅读 §1～§5 时请先对照）

以下在 **`l3_node/`** 等路径**已落地**；后文各节仍描述「问题 → 目标态 → 分阶段」，其中与下表重合的条目应视为 **已完成或部分完成**，不再当作纯规划。

| 主题 | 已实现（代码锚点） |
|------|-------------------|
| **§1 队列 / WAL / 停机** | 入队 **`background_task_sqlite.insert_pending`**（SQLite WAL）与内存队列双写；启动 **`reconcile_stale_background_tasks_on_startup`**；停机 **`flush_background_tasks_to_persistent_queue`**（经 `graceful_shutdown` 钩子）；**`tasks_index.jsonl`** 事件时间线；非 Windows 下 **`ws_server` SIGINT/SIGTERM → `server.close()`**，进而 **`run_shutdown_hooks`** |
| **§1 Cancel** | **`l3_node/agent_cancel`**：`metadata` 注入 cancel **`Event`**；流式 **`register_stream_task` + `task.cancel()`**（与 `RunCancelledError` 路径配合） |
| **§2 沙箱 / 预算** | **`workspace_context`**：子 Agent 工作区落在 `workspace/sandboxes/...`；宿主记忆以 **Memory Nexus** 为主 + 遗留 **`l3_local_shard_*.json`** 隔离；**`llm_budget` + `agent.sub_agent_max_total_tokens` / `main_max_total_tokens`**；**`max_delegate_depth`** 超限禁止继续 delegate |
| **§3 去重** | **`context_path_ledger` + `context_prefetch`**（`ledger_iteration_window`、`metadata._react_iteration`）；路径滑窗 **`_prefetch_paths_shown`**；**`mcp:read_file` 与 `core:fs_read` 同路径登记**；**`observation_dedup`** 同 run 大块 hash 引用（非 shell 正文级去重） |
| **§4 记忆** | **`memory_facade`**、**Memory Nexus（SQLite + FastEmbed）**；`memory_sync_signals` 仅为历史兼容占位；**`passive_max_idle_runs` / `last_prompt_inject_cycle`** 对 JSON 主路径已弱化（见 `nexus_config` `memory`） |
| **§5 Prompt** | **`agent_preflight` + `l3_node/routing/`** 插件链；**`prompt_compose`** 后缀预算与 **`prompt_suffix_eviction`** 日志；招聘域 **动态后缀**（`intent_signals` + `recruitment_longform` / 短 `hr_hint`） |

**仍开放（文档不宣称已解决）**：线程池 **Prometheus/深度指标**（§1 P0）；广谱 **Native 子进程 kill**（§1 P1）；**进程级后台 Worker**（§1 P2）；LiteLLM **`cache_control` 多段 system**（§3 P1）；L2 **webhook 强制 flush**（§4 P2）；子 Agent 通道当前 **`allow_delegate` 仍与主会话同为 True**（仅深度卡死递归，与 §2 P0「默认禁嵌套 delegate」尚有差距）。

---

## 总览：批评中哪些仍成立、哪些已被部分缓解

| 批评点 | 是否仍成立 | 现行代码中的状态 |
|--------|------------|------------------|
| `wait_for` 不杀 `to_thread` 内阻塞 | **成立** | Python 语义限制；日志已提示「超时返回 ≠ 调用结束」（见 `agent_core._invoke_react_tool`、超时 JSON）。 |
| 僵尸线程「数百个」直至 OOM | **部分夸大** | `asyncio.to_thread` 使用**有界**默认线程池（非无限起线程）；更准确的故障是 **池被卡死任务占满** 导致新工具饥饿，而非无界线程爆炸。 |
| 磁盘 `running` vs 内存队列 | **大幅缓解** | **启动对账** + **SQLite 入队双写** + **停机 flush 内存队列回 pending** + **冷启动 `_recover_sqlite_pending_queue`**（`background_task_service.py` / `background_task_sqlite.py`）。**仍存**：`SIGKILL`/Windows 强杀下钩子不保证执行；见 §〇。 |
| `allow_substrings: mcp:atom_` | **默认已移除** | `foreground_tool_policy` 默认 `_DEFAULT_ALLOW_SUBSTRINGS = ()`；豁免以 **MCP `long_running` 元数据** + `long_running_tool_ids` 为主；遗留子串仅当用户在 `nexus_config` **显式**配置时生效。 |
| prefetch 与工具去重 | **大幅缓解** | **路径级**：fs_read / mcp:read_file 登记 + **`_prefetch_paths_shown` 滑窗** + **`context_path_ledger` 按 ReAct 轮次拦截**。**内容级**：**同 run** 下大块 Observation **`observation_dedup` hash 引用**；**未**对 shell/MCP 返回正文做全文 hash 去重。 |

下文按五个主题给出：**问题与代码锚点 → 目标态 → 分阶段方案（P0/P1/P2）→ 验收标准**。

---

## 一、架构：Python 并发陷阱与状态撕裂

### 1.1 问题诊断

1. **线程黑洞（假超时）**  
   - **机制**: `asyncio.wait_for(asyncio.to_thread(sync_fn))` 超时后，**同步函数仍在默认 `ThreadPoolExecutor` 的线程里跑**，直到自然结束。  
   - **后果**: 线程槽位被长期占用 → 池饱和 → 后续前台工具排队/饥饿；极端情况下进程内存与其它资源仍可能被慢泄漏拖垮。  
   - **代码**: `l3_node/agent_core.py` → `_invoke_react_tool`；MCP 本地路径亦大量 `to_thread`（`mcp_registry.py`）。

2. **状态脑裂（队列 vs 磁盘）**  
   - **机制**: 任务调度在 **`asyncio.Queue`（易失）**，状态与结果在 **`.background_tasks/*.json`**。  
   - **已缓解**: 冷启动对账；**入队 SQLite 双写**；**`_recover_sqlite_pending_queue`** 把 pending 灌回内存队列；**`flush_background_tasks_to_persistent_queue`** 在停机钩子中把**尚未被 Worker 取走**的内存队列项写回 SQLite。  
   - **仍弱于**「纯磁盘队列」：Worker 已取走但未落终端 JSON 的瞬间崩溃、以及 **硬杀进程** 仍可能丢意图；运维上需 **`terminationGracePeriodSeconds`≥停机预算**（Unix 信号路径）。

3. **防线脆弱（子串豁免）**  
   - **已缓解**: 默认关闭宽泛子串；`mcp_registry.tool_entry_long_running` + 显式 `long_running_tool_ids`。  
   - **仍缺**: 第三方 MCP 若未带元数据，依赖运维配置，**误配仍会导致 5s 误杀或误豁免**。

### 1.2 解决方案（分阶段）

**P0（维持韧性、低成本）**

- 保持并监控：**对账**、**元数据豁免**、超时 JSON 中的「无法终止底层调用」说明。  
- **可观测性**: 为默认线程池增加 **队列深度 / 活跃线程数** 指标（日志或 Prometheus 风格计数），超阈告警。  
- **配置**: 文档化 `long_running_tool_ids` 与 MCP `long_running` 字段，作为接入第三方 MCP 的 **Checklist**。

**P1（结构性降险）**

- **高风险同步工具子进程化**：对明确类别（如「任意 shell」「未信任 MCP 宿主」）走 **`subprocess` + 硬超时 kill**（Windows `taskkill` / POSIX `SIGKILL`），与 `core:shell_exec` 的 background 模式对齐思路。（**未广谱落地**，仍依赖个案工具策略。）  
- **持久队列 + WAL**：**已实现** `background_task_sqlite`（`bg_pending` + WAL）；入队 **`insert_pending`** 与 **`put_nowait` 配合**；Worker 取 job 时 **`delete_pending`**；冷启动 **`_recover_sqlite_pending_queue`**。（**非**严格「事务性 dequeue」的独立任务状态表，演进空间仍见 §1.3。）  
- **协作式取消（Cancellation Token）**：**已实现** `metadata` 注入 cancel **`Event`** + 流式 **`task.cancel()`**（`agent_cancel` / `llm_client`）；**自研 Native 长循环**仍须逐个接 Event（未全自动）。  
- **优雅停机（Graceful Shutdown，「遗言」）**：**已实现** `graceful_shutdown` 钩子链调用 **`graceful_shutdown_background_tasks`**（内含 **`flush_background_tasks_to_persistent_queue`**）；**非 Windows** 下 **`ws_server` 对 SIGINT/SIGTERM 关闭 `websockets` 服务**以进入 `finally` → **`run_shutdown_hooks`**。**`atexit`** 仍**不能**替代 SIGTERM；**`SIGKILL` / Windows 强杀** 下钩子**不保证**执行。

**P2（平台级）**

- 可选 **进程级 Worker**：后台任务非 `run_agent` 同进程，而是 **子进程 + IPC**，主进程 kill 即释放宿主资源（代价是引擎与状态复制）。

### 1.3 最终定案（架构：并发 + 状态）

| 手段 | 作用 | 可行性说明 |
|------|------|------------|
| **入队即写 WAL** | 缩窄「内存-only」窗口 | **高**：`put_nowait` 前或与 `put` 同事务写 SQLite，再入内存队列加速消费。 |
| **优雅停机 3–5s** | SIGTERM 时把 **仅驻内存** 的队列Drain 到盘 | **中高**：依赖信号可达；K8s `terminationGracePeriodSeconds` 需 ≥ 停机逻辑预算。 |
| **协作式 Event** | 避免仅靠 `wait_for` 假超时 | **高**：仅改自研工具；`run_tool` 可透传 `ctx` 或闭包。 |
| **子进程 kill** | 第三方同步阻塞 | **中**：工程重，按工具类渐进启用。 |

**验收标准**

- P0：对账单测 + 告警文档；新 MCP 接入评审含 `long_running`。  
- P1：**SIGTERM 演练**：入队未消费任务在 flush 后重启可恢复；至少 **一类** Native 长循环工具响应 cancel Event；至少 **一类**阻塞工具可走子进程 kill。  
- P2：可选压测报告（池饱和 vs 子进程）供选型。

---

## 二、Agents：递归嵌套、沙箱与可观测性

### 2.1 问题诊断

1. **递归爆炸**  
   - `SubAgent.run_once` → 再次 `run_agent`，默认 `allow_delegate=True` 时 **子 Agent 仍可 delegate**（除非后续再收窄）。Token 与深度无全局 **硬预算**。  
   - **代码**: `agent_core.SubAgent`、`run_agent` 后台通道显式 `allow_delegate=False`（仅后台）；主会话仍可能多层 delegate。

2. **沙箱与隔离（已部分落地）**  
   - **工作区**：`delegate` 深度大于 0 时 **`workspace_context`** 将默认根设为 `workspace/sandboxes/<sub>/`（与主会话隔离写入）。**进程内**仍共享 MCP Manager、技能缓存等（非 OS 级隔离）。  
   - **记忆**：宿主长期记忆以 **Memory Nexus（SQLite + FastEmbed）** 为主；遗留 **`l3_local_shard_*.json`** 与子 Agent 隔离，避免与主会话混写同一 JSON 文件（见 `docs/architecture/MEMORY_NEXUS_L3.md`、`docs/arch/04_MEMORY_ARCHITECTURE.md`）。

3. **可观测性**  
   - 后台：`l3_event_bus` + WS + **`tasks_index.jsonl`**（queued/started/completed 等事件行）+ `progress.md`。**强制终止**仍弱：同进程 `run_agent` 依赖 cancel Event / 流式 cancel，非子进程级 SIGKILL。

4. **盲区：深度限制 ≠ 预算限制**  
   - 子 Agent 可在 **同一深度** 内 **死循环式工具重试**，把 **Token / 费用** 打满；仅 `max_delegate_depth` **无法**防「破产」。

### 2.2 解决方案（分阶段）

**P0**

- **全局 `max_delegate_depth`**（metadata 递增），超过则 Observation 返回「禁止继续 delegate」。  
- **子 Agent 默认 `allow_delegate=False`**（或 `delegate` 仅允许一层），与后台任务策略对齐。  
- 文档写明：**子 Agent 非安全边界**，敏感操作仍依赖 OS 账户与 workspace ACL。

**P1**

- **工作区派生**：**已实现** ContextVar + `sandboxes/sub-...`（见 `workspace_context`）；**promote 合并**仍为产品/工作流层约定，非内核强制。  
- **任务面板数据模型**：**已实现** **`tasks_index.jsonl`**（事件 + 关键字段）；**`cpu_proxy` / `last_tool`** 等仍可选扩展。  
- **子代理财务硬顶（Hard Token Budget）**：**已实现** `llm_budget` + `BudgetExhaustedError` + `nexus_config` **`agent.sub_agent_max_total_tokens` / `main_max_total_tokens`**（流式/非流式在引擎层累计 usage）。

**P2**

- **独立 agent-memory 存储**：**已实现** shard 文件 **`l3_local_shard_<id>.json`**（遗留 JSON 路径）；宿主长期记忆以 **Nexus** 为主（见 **MEMORY_NEXUS_L3.md**）。  
- **Cancel API + 生成级中断**：**流式路径已实现** `register_stream_task` + **`task.cancel()`**；非流式仍依赖短超时或改流式。

### 2.3 最终定案（Agents）

| 手段 | 防什么 | 可行性 |
|------|--------|--------|
| 深度 + 子 Agent 禁 delegate | 递归套娃 | **高** |
| **Token 硬顶** | 同层死循环刷账单 | **中高**：依赖 usage 字段完整；流式需统一累计。 |
| **断生成（cancel stream）** | 叫停后仍吐完长文 | **中**：依赖 `llm_client` 暴露可取消接口；需按供应商实测。 |
| 沙箱路径 | 误写共享 workspace | **中高** |

**验收标准**

- P0：深度超限单测；子 Agent 默认不可二次 delegate。  
- P1：沙箱单测；子 Agent **工具死循环场景**下触顶 **不再发起新的 LLM 请求**。  
- P2：Cancel 后观测 **completion token 不再增长**；shard 记忆 E2E。

---

## 三、上下文：前缀缓存与去重

### 3.1 问题诊断

1. **前缀缓存被「物理击穿」**  
   - `_build_system_prompt` 虽拆 **前缀/后缀**，但 **同一字符串**仍是一次性交给模型；若提供商按 **整段 system hash** 做缓存，**后缀任意字节变化**即可能使缓存失效。  
   - **动态段**（本地记忆时间戳、HR 运行时、当日摘要）会加剧失效。  
   - **注意**: 是否命中前缀缓存取决于 **云厂商具体规则**（有的仅缓存固定前缀 token 边界），不能假设「拆文档即命中」。

2. **去重一致性**  
   - **路径**：`_register_tool_paths_for_dedupe` 覆盖 **`core:fs_read` 与 `mcp:read_file`**；**`_prefetch_paths_shown` 滑窗** + **`context_path_ledger`**（按 **`_react_iteration`** 与 **`ledger_iteration_window`**）控制「同轮强制、远轮放行」。  
   - **内容**：**同一次 `run_agent`** 内大块 Observation **`observation_dedup`（hash）**；**未**对 shell 输出或任意 MCP 正文做通用内容去重。

3. **历史说明（已缓解）**  
   - 早期仅 `_prefetch_paths_shown` 易「永久屏蔽」；现已与 **ReAct 轮次账本** 联动，见 **`context_path_ledger.py`**。

4. **盲区：工具表破坏「绝对冰封」**  
   - **任意**新增工具（如多一个 `mcp:fetch`）都会改变 **`build_tools_description` 中段**，在多数供应商实现下 **前缀缓存从工具表起整体失效**。  
   - **定案**：工具描述串列为 **cache-critical**；**禁止**在工具表 **之前**插入动态行；工具 id **稳定排序**以减少无意义抖动；接受「工具集变更必然换缓存键」为常态，但避免 **非工具因素** 插入工具表前方。

### 3.2 解决方案（分阶段）

**P0**

- **Prompt 稳定化**：动态段尽量 **规范化**（例如记忆条目不注入毫秒时间戳；同日批次共用 `date_bucket`）。  
- **文档**：在 `L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md` 注明「前缀缓存为**尽力而为**，成本优化需结合具体模型 API」。  
- **prefetch 扩展登记**：对 **`mcp:read_file`**（path 参数）与 **`core:fs_read`** 走**同一** `register_path_shown` 工具函数，避免两套路径登记逻辑分叉。  
- **冰封区拼接顺序**：**范式与输出格式骨架 → 工具表（稳定排序）→ recall/coordinate/delegate 固定模板 → 分隔线 → 全部动态后缀**。  

**P1**

- **ContextLedger + 滑动窗口去重**：**已实现**（`context_path_ledger` + `context_prefetch`，`nexus_config.context_prefetch.ledger_iteration_window`）。  
- **API 层分块 system**：若 LiteLLM/供应商支持 **多段 system** 或 **cache_control** 标记……（**未实现**，仍单段 system 字符串。）

**P2**

- **内容寻址去重**：**已实现（同 run 滑窗）** `observation_dedup`；跨 run / 全局引用链仍可扩展。

### 3.3 最终定案（上下文）

| 手段 | 解决什么 | 可行性 |
|------|----------|--------|
| 工具表在冰封区、动态段仅在后 | 减少非工具因素导致的缓存失效 | **高** |
| Ledger + `turn_age` / 滑窗 | 远轮次合法重读 | **高** |
| `cache_control` 多段 | 真·前缀命中 | **中**（依赖云厂商） |

**验收标准**

- P0：路径登记单测 + 文内「冰封区」示意图。  
- P1：第 1 轮与第 `W+1` 轮同路径 **再读不被误拦**；同轮双读仍缩略。  
- P2：hash 引用不破坏 ReAct 解析。

---

## 四、记忆：多源缝合与同步滞后

### 4.1 问题诊断

- **被动 vs 主动**：**`memory_facade` + `local_memory_ranking`** 统一排序策略；Prompt 内 **SSOT 一行规则**（以主动检索为准等）见 `L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md`。残余风险：截断条数与 MMR 细节仍可能不完全一致。  
- **~~MemorySyncDaemon~~（已移除）**：L3 宿主记忆不再周期性同步 L2；跨会话 SSOT 为 **Memory Nexus**。  
- **注意力**：**`passive_max_idle_runs` / `last_prompt_inject_cycle` / `next_prompt_cycle`** 等与被动注入衰减联动（见 `local_memory`、`nexus_config` `memory`）。

### 4.2 解决方案（分阶段）

**P0**

- **文档化 SSOT 层级**：约定「以 **主动检索** 为准，被动注入仅为 **提示**」或反之，在 system 后缀加 **一行显式规则**（二选一写死，减少模型困惑）。  
- **统一排序标签**：`correction` 优先在两条链路 **同一实现**（抽 `local_memory_ranking.py`）。

**P1**

- **门面 API**：**已实现** `memory_facade.py`（与 `load_raw_entries` / shard 一致）。  
- **防抖 + 急迫同步**：原 L2 记忆同步路径已移除；若需多副本可自行外挂同步，非本仓库默认。  
- **记忆注意力衰减**：**已实现**基于 **`next_prompt_cycle`** 与条目字段（如 **`last_prompt_inject_cycle`**、**`last_accessed_turn`**）及 **`passive_max_idle_runs`**；`local_memory_search` 侧有 **半衰** 等评分（`local_memory_search.py`）。

**P2**

- L2 侧 **webhook / outbox** 拉取；或会话结束 hook **强制 flush**。

### 4.3 最终定案（记忆）

| 手段 | 解决什么 | 可行性 |
|------|----------|--------|
| SSOT 一行 + 统一排序 | 注入 vs 检索矛盾 | **高** |
| **last_accessed / 注入轮次衰减** | 被动 prompt 臃肿、旧垃圾占位 | **中高**：**已接** `next_prompt_cycle`、条目字段与 `local_memory_search` 触达；仍可细化钩子覆盖度。 |
| Facade + 急迫同步 | 一致视图 + 降低 L2 滞后 | **中高**：facade + 信号代数 + Daemon 分片睡眠 **已落地**；独立 30s 防抖队列仍为可选增强。 |

**验收标准**

- P0：Prompt 中明确优先级；冲突场景人工评测减少。  
- P1：单测「超过 N 轮未访问的记忆不出现在被动注入」；`local_memory_search` 仍可命中。  
- P2：急迫写入后 T 秒内 L2 可检索（T 可配置）。

---

## 五、Prompt：后缀膨胀与业务耦合

### 5.1 问题诊断

- **后缀堆叠**：模块仍多，但 **`prompt_compose` + `prompt_suffix_max_chars` + `prompt_suffix_eviction` 日志** 提供硬帽与驱逐；与 §4 记忆衰减联动减体积。  
- **业务预检**：**`agent_preflight.apply_inbound_preflight`** + **`routing` 插件链** 承接 HR/BI/招聘等确定性分支；`run_agent` 内仍保留 **通用** ReAct 与 hook，**不再以「数百行内联预检」为唯一形态**（具体行数以仓库为准）。

### 5.2 解决方案（分阶段）

**P0**

- **后缀预算（硬帽）**：**已实现** `prompt_suffix_max_chars` + **`compose_suffix_with_eviction`**（`prompt_compose.py`）。  
- **Intent Router 外提**：**部分已实现** `agent_preflight` + `l3_node/routing/`；入站侧可再挂插件（`ws_server` / `im_channels` 是否预链视部署而定）。

**P1**

- **Intent Router 外提**：同 **P0「部分已实现」**；持续收紧 `agent_core` 域特例仍为迭代方向。  
- **领域插件注册表**：**`routing/plugins.py` `register_inbound_plugin`**；全通道统一调用可在 bootstrap 层补全。

**P2**

- **动态后缀选择**：**已实现关键词路径**（`intent_signals` + `recruitment_longform` / 短 `hr_hint`）；**intent_embedding** 仍未接。

### 5.3 最终定案：优先级驱逐（Priority Eviction）——「电车难题」显式化

当后缀拼接 **超过预算** 时，**按以下顺序裁剪（从低到高牺牲）**，而非随机截断：

| 优先级 | 内容 | 策略 |
|--------|------|------|
| **最高（默认不可删）** | **JACHIN.md / `.jachin/rules.md` 核心工作区规则**；**当前任务状态**（`task_plan.md` / `get_planning_context_for_prompt` 核心段） | 若仍超标，仅允许 **截断任务规划中的非关键附录**（如历史 diff），**保留 goal/steps 头** |
| **中** | **HR SKILL 长 SOP / 领域长文**；能力目录扩展段 | **若当前轮意图命中招聘/域关键词则尽量保留**；否则 **整块移除** 先于最高级动刀 |
| **低（最先驱逐）** | **被动本地记忆摘要**（`get_local_memory_for_prompt`）；P1 装饰性注入；已过衰减阈的记忆 | **先减条数再减每条 max 长度**；与 §4.3 **注意力衰减** 联动，避免与驱逐逻辑打架 |

**实现要点**：`_build_system_prompt` 或专用 **`compose_prompt_suffix(modules: list[PromptChunk])`**，每块带 **`tier: high|mid|low`** 与 **`estimated_chars`**；超标时 **从 `low` 开始 pop**，同层内 **先最短命 / 最大体积块**。打日志 **`prompt_suffix_eviction`**：记录被扔模块名与剩余 budget。

**验收标准**

- P0：超标场景下 **低优先级先失**；日志可审计。  
- P1：`agent_core` HR 预检行数下降（可 CI 统计）；路由单测覆盖停止招聘/BI。  
- P2：非 HR 会话 HR 后缀体积趋零。

---

## 六、推荐实施顺序（与风险对应）

1. **已完成或部分完成（见 §〇）**：入队 WAL、停机 flush、prefetch 路径 + ledger、observation hash、memory facade、后缀驱逐、路由外提（预检 + 插件）、Token 硬顶、流式 cancel、任务索引 JSONL、Unix 信号关闭 WS。  
2. **中短期仍优先**：§1 **线程池可观测**；**1～2 个自研长循环 Native** 显式轮询 cancel Event；§1 **一类工具子进程 kill**；§2 **子 Agent 默认 `allow_delegate=False`**（若产品确认）。  
3. **中长期**：§3 **`cache_control` 多段**；§4 L2 **webhook**；§5 **embedding 动态后缀**；§1 **进程级后台 Worker**（可选）。

**依赖关系简述**：**入队 SQLite 双写** 已缩小「仅内存队列」窗口；**停机 flush** 覆盖「未出队的内存项」；**硬 kill** 仍可能丢在途任务——运维与产品需知情。

---

## 七、修订记录

| 日期 | 说明 |
|------|------|
| 2026-04 | 初版：五项批评对照代码、分 P0/P1/P2 方案与验收标准 |
| 2026-04 | 补充定案：优雅停机与 SIGKILL 边界、协作式 Cancel、Token 硬顶与生成级中断、滑窗去重与工具表冰封区、记忆注意力衰减、后缀优先级驱逐 |
| 2026-04-02 | **§〇 实现快照**；总览与 §1～§5 与当前仓库对齐（WAL/flush/ledger/dedup/facade/routing/eviction 等）；删除过时「仍缺排队入队」等表述 |
