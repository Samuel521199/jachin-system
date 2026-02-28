# 第四章：RAG 架构的深度定制 (The Memory Pipeline)

**版本**: v4.0  
**状态**: 架构设计规范  
**规范引用**: [ARCHITECTURE_DESIGN_SPEC.md](./ARCHITECTURE_DESIGN_SPEC.md) | [whitepaper_v4.0_swarm.md](./whitepaper_v4.0_swarm.md)

---

## 1. 设计哲学

传统的 RAG 只是「丢文档、切块、检索」，这种粗暴的处理方式会让 Jachin 这种电子生命显得极其生硬。我们需要设计一套**「有机记忆流动管线」**，既要符合人类的遗忘规律，又要绝对捍卫核心事实，同时榨干每一滴硬件算力。

**核心原则**：
- **该记住的死都不忘**：重点数据永久保存，免疫时间衰减
- **该遗忘的随风而逝**：普通记忆随时间权重衰减
- **弱设备有后盾**：瘦客户端依赖 Layer 2 提供记忆
- **强设备有主见**：高性能终端可启用本地向量库，零延迟离线反射

---

## 2. 动态语义切块 (Semantic Chunking)

### 2.1 摒弃按字数一刀切

记忆的写入必须基于**语义边界**，而非固定字符数。

| 传统做法 | Jachin 做法 |
|----------|-------------|
| 每 512 字一刀切 | 按语义单元切块 |
| 上下文断裂 | 上下文完整 |

### 2.2 切块策略

- **对话流**：将一段连续的、同一主题的对话（如：关于周末去哪里钓鱼的讨论）作为一个独立的 Chunk 进行向量化（Embedding）
- **文档流**：同一份技术文档中的完整章节作为一个 Chunk
- **边界识别**：基于句号、段落、主题切换等语义边界进行切分

### 2.3 实现要点

- 向量化前进行语义边界检测
- 保证 Chunk 的上下文完整性，避免「半句话」截断
- 支持多模态（文本、图片、语音转写）的语义切块

---

## 3. 记忆分层与「梦境」机制 (Tiered Memory & Dream Sequence)

### 3.1 短期工作区 (Short-term Context)

| 属性 | 说明 |
|------|------|
| **存储** | Redis 或内存 |
| **存活周期** | 极短（当前会话） |
| **用途** | 维持当前聊天的流畅度 |
| **容量** | 受 Token 限制，可配置 |

### 3.2 潜意识沉淀 (Subconscious Archiving)

**「梦境整理 Agent」**：每天深夜，Layer 2 启动后台任务：

1. **回放**：遍历当天的短期记忆
2. **提取**：生成摘要，打上多维坐标标签
   - `user_id`
   - `device_id`
   - `character_id`（人格 ID）
   - `topic`（主题）
   - `timestamp`
3. **持久化**：做成 Chunk 永久存入向量数据库，完成从「经历」到「知识」的转换

### 3.3 分层示意

```
┌─────────────────────────────────────────────────────────┐
│  Short-term (Redis/Memory)  ← 当前会话，秒级过期           │
├─────────────────────────────────────────────────────────┤
│  Subconscious Archiving     ← 梦境 Agent 每日沉淀         │
│  (Qdrant Vector DB)         ← 长期记忆，语义检索           │
├─────────────────────────────────────────────────────────┤
│  Core Memory (Immutable)    ← 铂金标签，永不遗忘           │
└─────────────────────────────────────────────────────────┘
```

---

## 4. 时效衰减机制 (Forgetting Curve)

### 4.1 问题

两年前你喜欢吃苹果，今天你喜欢吃香蕉。在常规 RAG 检索中，如果不做干预，旧记忆可能会覆盖新偏好。

### 4.2 解决方案：时间权重惩罚 (Time-Decay Penalty)

在检索公式中引入**时间衰减**：

- 随着时间流逝，普通日常对话的检索权重逐渐下降
- 系统默认更倾向于采信你**最近的发言**
- 衰减曲线可配置（如指数衰减、线性衰减）

### 4.3 公式示意

```
final_score = semantic_similarity × time_decay_factor(timestamp)
```

其中 `time_decay_factor` 随 `timestamp` 越旧而越小。

---

## 5. 重点数据永久保存 (Core Memory / Immutable Truths)

### 5.1 设计目标

这是抵抗「时效衰减」的**绝对防线**。不是所有记忆都会遗忘，有些信息是构建数字生命的基石（Core Beliefs）。

### 5.2 触发机制

| 触发方式 | 示例 |
|----------|------|
| **用户明确指令** | 「Jachin，记住我绝对不吃香菜」 |
| **高情绪价值事件** | 家人生日、重要纪念日 |
| **系统识别** | 核心服务器密码、重要商业决策 |

### 5.3 铂金标签 (is_core)

- 该 Chunk 被赋予 `is_core=True` 的铂金级标签
- **免疫时间衰减**：检索时不受 Time-Decay 影响
- **永不覆写**：存储层禁止覆盖或删除
- **绝对召回**：检索时拥有最高优先级（Top-K Override）

### 5.4 持久性

即使过了十年，只要触发相关语义，这段记忆也会像本能一样被瞬间唤醒。

---

## 6. 高性能终端本地向量库 (Edge-Embedded VectorDB)

### 6.1 设计哲学

**打破「终端必须绝对无脑」的教条**。对于算力极弱的 ESP32，它当然是瘦客户端；但对于一台拥有 RTX 4090 的 PC，强行让它把所有记忆请求都发给网络另一端的 Layer 2 服务器，是对算力的巨大浪费。

### 6.2 算力自适应选配

| 终端类型 | 存储策略 | 说明 |
|----------|----------|------|
| **ESP32 / 树莓派 Zero** | 纯瘦客户端 | 依赖 Layer 2 提供记忆，无本地向量库 |
| **高性能 PC / Mac** | 可选 L1 缓存 | 用户可勾选「启用本地嵌入式向量库」 |

### 6.3 PC 端 L1 缓存 (本地化海马体)

- **用户可选**：在设置中勾选「启用本地嵌入式向量库（如 LanceDB）」
- **同步范围**：
  - 属于本机 `device_id` 的高频常驻记忆
  - UI 偏好
  - 脱机的基础百科库

### 6.4 零延迟离线反射区

- **离线能力**：即便家里的局域网断了，或 Layer 2 服务器宕机，桌面全息 Jachin 依然保留关于你的核心记忆
- **零延迟**：本地离线问答、高危操作验证

### 6.5 架构类比

| 层级 | 类比 | 说明 |
|------|------|------|
| **Layer 3 本地向量库** | CPU L1 缓存 | 高频、零延迟、离线可用 |
| **Layer 2 向量数据库** | CPU L2 缓存 | 全局记忆总汇，主存储 |

---

## 7. 技术实现要点

### 7.1 战役一：地基与神经元（已实现）

- **MemoryChunk** (`core/memory/chunk_schema.py`)：Pydantic 模型，含 `id`, `content`, `vector`, `user_id`, `device_id`, `character_id`, `is_core`, `timestamp`
- **BaseEmbedder / OpenAIEmbedder** (`core/memory/embedding.py`)：`embed_text()`, `embed_batch()`，支持 text-embedding-3-small
- **VectorStoreProtocol** (`core/memory/store_protocol.py`)：`upsert(chunks)`, `search(query_vector, limit, filter_dict)`
- **LanceDBStore** (`core/memory/lancedb_store.py`)：本地 `data/lancedb`，表 `jachin_long_term_memory`，支持 metadata 精准过滤

### 7.1.1 战役二：工作记忆与大模型挂载（已实现）

- **MemoryManager** (`core/memory/manager.py`)：记忆协调中枢
  - 短期记忆：`stm_cache[user_id]` 滑动窗口，最多 10 轮（20 条消息）
  - `add_dialogue(user_id, role, content)`：追加对话
  - `retrieve_context(query, user_id, limit=5)`：RAG 检索，优先 `is_core=True`，格式化输出
- **CommanderAgent 升级**：`process_request` 融合 LTM + STM
  - 收到 user_text → add_dialogue(user) → retrieve_context → 构建 System + LTM + STM + user → LLM → add_dialogue(assistant)

### 7.1.2 战役三：梦境机制与记忆凝结（已实现）

- **快路径 - remember_core_fact**：Commander 内置工具
  - 当用户说「记住我家的 Wi-Fi 密码是 1234」时，LLM 调用 `remember_core_fact(fact=...)`
  - 执行：Embedding → MemoryChunk(is_core=True) → store.upsert
  - 铂金标签，永不遗忘
- **慢路径 - consolidate_memory**：梦境提炼
  - 当 STM 达到 20 条时，`asyncio.create_task` 触发后台任务
  - 将短期对话交给 LLM 提炼（偏好、习惯、重要事件）
  - 提炼结果 Embedding → MemoryChunk(is_core=False) → store.upsert
  - 成功后清空该用户 STM
</think><｜tool▁call▁begin｜>
TodoWrite

### 7.2 存储层扩展（战役三）

- **Qdrant 元数据**：支持 `is_core`、`timestamp`、`user_id`、`device_id` 等过滤与排序
- **检索 API**：支持 `time_decay` 参数、`core_only` 过滤

### 7.3 梦境 Agent（战役三）

- **调度**：Cron / 定时任务，每日凌晨执行
- **输入**：Redis 短期记忆
- **输出**：向量化 Chunk 写入 Qdrant / LanceDB

### 7.4 Layer 3 本地库

- **可选实现**：LanceDB（已实现）、Chroma、SQLite + sqlite-vss 等嵌入式向量库
- **同步协议**：增量同步 Layer 2 → Layer 3，按 `device_id` 过滤

---

## 8. CTO 架构审视

加入**重点数据永久保存**和**边缘缓存**之后，这套 RAG 架构真正做到了：

- **该记住的死都不忘**：Core Memory 永不覆写，绝对召回
- **该遗忘的随风而逝**：Time-Decay 让旧偏好自然衰减
- **弱设备有后盾**：瘦客户端依赖 Layer 2
- **强设备有主见**：高性能终端可启用本地 L1 缓存

这套架构完美适配「万物互联」与「灵魂伴侣」的双重定位。

---

**相关文档**：  
[architecture.md](./architecture.md) | [ARCHITECTURE_DESIGN_SPEC.md](./ARCHITECTURE_DESIGN_SPEC.md) | [whitepaper_v4.0_swarm.md](./whitepaper_v4.0_swarm.md)
