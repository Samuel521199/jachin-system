# L3 Memory Nexus（MemPalace / SQLite + FastEmbed）

**单一事实来源（SSOT）**：描述 L3 宿主侧「跨会话记忆」的**现行**实现。与 **四大原语**中的 **Tools**（`core:local_memory_*`）及 ReAct 伪动作 **`recall_memory`（与 `core:local_memory_search` 同源、均走 Nexus）** 对齐；区别于 **`core_memory` SQLite**、**对话 Compaction**、**遗留 `l3_local.json` 只读** 等其它链路。

**系统总览**：[`JACHIN_MEMORY_ARCHITECTURE.md`](./JACHIN_MEMORY_ARCHITECTURE.md)。

---

## 1. 架构概览

| 概念 | 含义 |
|------|------|
| **MemPalace** | 翼区 **Wing** → **Room** → **Drawer（逐字文档）** 的逻辑划分。 |
| **存储** | **SQLite + FastEmbed**：本地单一数据库文件 **`~/.jachin/palace_db/memory_nexus.sqlite3`**，表 **`drawers`**（含 `document` 原文与 float32 **embedding** BLOB）。无外部分向量库进程。 |
| **宿主代码** | `l3_client/local_mcps/jachin_memory_nexus/memory_backend.py`：`commit_drawer`、`recall_room`、`deep_search`。 |

### 1.1 存储、向量化与 Deep Search（本地化）

1. **存储介质**  
   所有跨会话记忆（Wing / Room / Drawer）均落在上述 **SQLite** 单文件中；与旧版「`palace_db` 目录 + 外部向量服务」模型不同，**不再需要** HTTP 向量库降级路径。

2. **向量化（Embedding）**  
   由宿主 Python 进程内的 **`fastembed.TextEmbedding`** 完成（见 `memory_backend._get_embedder` / `_embed_one`），**不发起**向云端向量 API 的请求。默认模型可通过 **`JACHIN_MEMORY_EMBED_MODEL`** 覆盖。

3. **检索机制（`deep_search`）**  
   - **不使用** HNSW 等近似最近邻索引。  
   - **候选集**：SQLite **`ORDER BY timestamp DESC`**，可选 **wing** 等值过滤，最多取 **`JACHIN_NEXUS_DEEP_SEARCH_CANDIDATES`**（默认 **2500**）条。  
   - **相似度**：在内存中用 **NumPy** 对候选向量与查询向量做矩阵乘法（归一化后等价余弦相似度），取 Top-K；返回字段 **`distance` = 1 − cos_sim**（越小越近），与旧版对外 API 字段语义一致。

4. **`recall_room`**  
   按 `wing` + `room` 精确过滤，按 `timestamp` 倒序返回近期抽屉，**不做**全库向量检索。

---

## 2. 注入 Prompt（L1「唤醒栈」）

- **入口**：`l3_node/memory_nexus_bridge.py` → `build_l1_system_memory_block`，由 `agent_core._build_system_prompt` / `_build_direct_system_prompt` 调用。
- **数据来源**：`recall_room` 拉取固定翼区抽屉，例如：
  - **`E2E_Monitors` / `Kalaroko_Default`**：近期巡检摘要；
  - **`User_Persona` / `General_Chat`**：近期交互与画像类条目。
- **契约**：注入块标题为 **「系统近期核心记忆」**；失败 **fail-open**（不打断对话）。

兼容 API：`l3_node/local_memory.py` 的 `get_local_memory_for_prompt()` 已委托同一 L1 块（旧 JSON 被动衰减参数不再生效）。

---

## 3. Native 工具（四大原语 · Tools）

| 工具 id | 行为 |
|---------|------|
| **`core:local_memory_search`** | 调用 **`deep_search`**：全库（可选 wing 过滤）语义检索；返回 `matches[]`（含 `wing`/`room`/`metadata`）。 |
| **`core:local_memory_append`** | 调用 **`commit_drawer`**：默认 **`User_Persona` / `Learned_Skills`**（事实、偏好、任务检查点等业务写入仍经 `add_local_memory` 封装）。 |

实现：`l3_node/local_memory_search.py`、`l3_node/tools/core_local_memory_append.py`。

---

## 4. 回合末异步写入（潜意识）

- **位置**：`l3_node/memory_nexus_bridge.schedule_nexus_turn_commit_async`，在 `run_agent` 成功产出回复后触发（直连 LLM 路径同样）。
- **条件**：用户消息或助手回复足够长时（启发式阈值），异步 **`commit_drawer`** → **`User_Persona` / `General_Chat`**。
- **原则**：异常仅记录日志，**不阻塞**主对话。

---

## 5. 与遗留 JSON / 停用能力

- **`recall_memory`**：由 `agent_core._recall_memory_search` 调用 **`search_local_memories` → `deep_search`**，与 **`core:local_memory_search` 同源**，**不请求 L2**。
- **`l3_local.json`**：可能仍存在；**写入已迁 Nexus（SQLite）**。保留读取用于 **分片 / HR 指针 / 诊断**。
- **`memory_compactor`**：**全局停用**；见 `l3_node/memory_compactor.py`。

---

## 6. 与其它记忆链路的边界

| 链路 | 存储 / 用途 |
|------|-------------|
| **Memory Nexus（本文）** | SQLite `memory_nexus.sqlite3` / 表 `drawers`；工具、L0/L1、回合 commit、`recall_memory`。 |
| **`core_memory` SQLite** | 碎片/Compaction flush 等；**不是** Nexus。 |
| **上下文 Compaction** | 折叠对话 **token**；**不是** JSON 梦境合并。 |
| **L2 `POST/GET …/memory/*`（若部署）** | 控制面/多租户历史能力；**不是**本仓库 L3 默认宿主记忆路径。 |

---

## 7. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04 | Nexus 成为 L3 核心本地记忆；停用 `l3_local` 主编译、`merge_from_l2`、JSON compactor；本文 SSOT。 |
| 2026-04 | L3 记忆闭环：移除 L2 sync 守护；`recall_memory` 改走 Nexus。 |
| 2026-04 | Nexus 底层存储迁至 **SQLite + FastEmbed + NumPy**；移除外置向量库 HTTP 降级说明；补充 `JACHIN_MEMORY_EMBED_MODEL`、`JACHIN_NEXUS_DEEP_SEARCH_CANDIDATES`。 |
