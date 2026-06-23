# Jachin 记忆架构详解：三层记忆体系

> **分册**: 04 / 07 · [返回索引](./README.md)  
> **代码锚点**: `l3_client/local_mcps/jachin_memory_nexus/memory_backend.py`、`l3_node/memory_nexus_bridge.py`、`l3_node/experience_memory.py`、`l3_node/task_planning.py`  
> **专题 SSOT**: [`MEMORY_NEXUS_L3.md`](../architecture/MEMORY_NEXUS_L3.md)、[`JACHIN_MEMORY_ARCHITECTURE.md`](../architecture/JACHIN_MEMORY_ARCHITECTURE.md)

---

## 目录

1. [记忆体系总览](#一记忆体系总览)
2. [短期记忆（In-Context）](#二短期记忆in-context)
3. [中期记忆（Session-Level）](#三中期记忆session-level)
4. [长期记忆：Memory Nexus](#四长期记忆memory-nexus)
5. [长期记忆：Experience Memory](#五长期记忆experience-memory)
6. [长期记忆：PersistedIntent](#六长期记忆persistedintent)
7. [记忆检索算法详解](#七记忆检索算法详解)
8. [记忆注入 Prompt 全流程](#八记忆注入-prompt-全流程)
9. [记忆写入全路径](#九记忆写入全路径)
10. [遗留与停用项](#十遗留与停用项)

---

## 一、记忆体系总览

```mermaid
flowchart TB
    subgraph ST2["⚡ 短期记忆（单次 run_agent 内，随会话消亡）"]
        ST_MSG["messages[]\nOpenAI role/content 对话历史"]
        ST_LEDGER["context_path_ledger\n路径滑窗去重"]
        ST_PREFETCH["context_prefetch\n工具后意图关键词附件"]
        ST_HOT["session_hot_user_inject\n中段热并入（等锁前入账）"]
    end

    subgraph MT2["🔄 中期记忆（跨轮次/跨会话任务，文件持久）"]
        MT_BUF["session_messages_buf\n最近 30 条消息跨轮保留"]
        MT_PLAN["task_plan.md / progress.md\n工作区规划记忆"]
        MT_DAG2["TaskDAG active.json\n任务节点状态"]
        MT_SKILL["SKILL.md 热重载\nP1/P2 实时 SOP 同步"]
    end

    subgraph LT2["🧠 长期记忆（跨会话持久，SQLite/JSONL）"]
        LT_NEXUS["Memory Nexus\nmemory_nexus.sqlite3\nSQLite + FastEmbed 向量"]
        LT_EXP["Experience Memory\nexperience.jsonl\nTF-IDF 成功路径库"]
        LT_INTENT["PersistedIntent\npersisted_intents.sqlite3\n自治意图持久化"]
    end

    AGENT2["主 run_agent"] --> ST2
    ST2 -->|"回合末 async"| LT_NEXUS
    MT_BUF --> MT2
    MT2 -->|"prompt 后缀注入"| AGENT2
    LT2 -->|"prompt 注入 + 工具检索"| AGENT2

    AGENT2 -->|"写回"| MT_BUF
    AGENT2 -->|"显式写入"| LT_NEXUS
    AGENT2 -->|"成功路径沉淀"| LT_EXP
```

---

## 二、短期记忆（In-Context）

### 2.1 短期记忆组成

```mermaid
flowchart LR
    subgraph SHORT["单次 run_agent 上下文窗口"]
        M_SYS["system_prompt\n前缀（静态）+ 后缀（含记忆注入）"]
        M_MSG["messages[]\n[{role:user/assistant, content:...}, ...]"]
        M_META["PipelineContext.metadata\n路由元数据、迭代计数、gateway_bundle..."]
        M_LEDGER["context_path_ledger\ndict: path_key → last_seen_iteration"]
        M_HOT2["session_hot_user_inject.pending\n中段热注入缓冲区"]
        M_PREFETCH2["context_prefetch 附件\n工具后拼接的工作区摘录"]
    end

    M_SYS & M_MSG & M_META --> LLM_IN["LLM 输入\nfull_messages = [system] + messages"]
    M_LEDGER --> M_PREFETCH2
    M_HOT2 -->|"每轮 LLM 前 drain"| M_MSG
    M_PREFETCH2 -->|"拼到 Observation 后"| M_MSG
```

### 2.2 context_path_ledger 去重机制

**问题**：Agent 在多轮迭代中可能反复读取同一文件，造成上下文膨胀。

```mermaid
sequenceDiagram
    participant TOOL8 as run_tool(fs_read)
    participant LEDGER2 as context_path_ledger
    participant PREFETCH8 as context_prefetch

    Note over LEDGER2: dict: {path → last_iter}
    Note over PREFETCH8: 工具执行后附加工作区文件摘录

    TOOL8->>LEDGER2: register_path("~/.jachin/workspace/report.md", iter=3)
    LEDGER2->>LEDGER2: {"report.md": 3}

    Note over TOOL8: 下一轮迭代 iter=4

    PREFETCH8->>LEDGER2: is_stale("report.md", iter=4, window=3)
    Note over LEDGER2: last_seen=3，current=4，差值=1 < window=3
    LEDGER2-->>PREFETCH8: False（近期已附加，跳过）

    Note over TOOL8: 第 7 轮迭代 iter=7

    PREFETCH8->>LEDGER2: is_stale("report.md", iter=7, window=3)
    Note over LEDGER2: last_seen=3，current=7，差值=4 ≥ window=3
    LEDGER2-->>PREFETCH8: True（路径已过期，重新附加）
    PREFETCH8->>PREFETCH8: 附加 report.md 最新摘录
    PREFETCH8->>LEDGER2: register_path("report.md", iter=7)
```

### 2.3 session_hot_user_inject 热并入

```mermaid
sequenceDiagram
    participant U8 as 用户（HTTP）
    participant HTTP8 as http_server
    participant HOT8 as session_hot_user_inject
    participant AGENT8 as run_agent（持锁中）

    U8->>HTTP8: POST /agent/run（同会话第二条消息）
    HTTP8->>HTTP8: 检测 _http_agent_session_lock 已被持有
    HTTP8->>HOT8: record_pending_session_user_text(text)
    HTTP8-->>U8: 202 Accepted（等锁响应）

    Note over AGENT8: 下一轮 LLM 调用前

    AGENT8->>HOT8: drain_pending_session_user_texts()
    HOT8-->>AGENT8: ["补充：请注意安全性"]
    AGENT8->>AGENT8: 将文本并入 full_messages user 块
    Note over AGENT8: LLM 在同一轮看到补充指令
```

---

## 三、中期记忆（Session-Level）

### 3.1 session_messages_buf 跨轮保留

```mermaid
sequenceDiagram
    participant U9 as 用户
    participant AGENT9 as run_agent
    participant BUF9 as session_messages_buf

    Note over BUF9: 同一 session_id / lark_chat_id 共享

    U9->>AGENT9: 第 1 轮消息
    AGENT9->>BUF9: 读取历史（run 开始时复制 buf）
    AGENT9->>AGENT9: 执行 ReAct
    AGENT9->>BUF9: 写回最近 30 条 ctx.messages

    U9->>AGENT9: 第 2 轮消息（有记忆的延续）
    AGENT9->>BUF9: 读取历史（含第 1 轮最后 30 条）
    Note over AGENT9: 模型能看到上轮的 Final Answer 和关键 Observation
```

**30 条限制的意义**：防止长会话 token 膨胀，同时保留关键上文（如「同意」确认、上轮结论）。

### 3.2 工作区文件记忆

```mermaid
flowchart TB
    subgraph WORKSPACE["~/.jachin/workspace/（工作区文件记忆）"]
        TP2["task_plan.md\n当前任务目标与分解步骤\n格式: # Task Plan\n## Goal\n## Steps\n- [ ] 步骤1"]
        PR2["progress.md\n已完成/待完成事项\n格式: # Progress\n✅ 完成了X\n⏳ 进行中Y\n❌ 失败了Z"]
        FI2["findings.md\n发现与洞察\n工具执行的关键产出"]
        DA2["task_engine/active.json\nTaskDAG 节点状态\n{nodes:[{id,title,status,depends_on}]}"]
    end

    subgraph INJECT["注入到 system_prompt 后缀"]
        GET_PLAN["get_planning_context_for_prompt()\n读取 task_plan + progress + findings"]
        GET_DAG2["format_active_task_dag_prompt_suffix()\n读取 active.json"]
    end

    TP2 & PR2 & FI2 --> GET_PLAN --> SYS_SUFFIX["system_prompt 后缀\n(tier=3)"]
    DA2 --> GET_DAG2 --> SYS_SUFFIX

    AGENT9_2["run_agent"] -->|"get_planning_context_for_prompt"| GET_PLAN
    AGENT9_2 -->|"format_active_task_dag_prompt_suffix"| GET_DAG2
```

### 3.3 SKILL.md 热重载（实时 SOP 记忆）

```mermaid
sequenceDiagram
    participant EVOLVER as skill_evolver.py
    participant SKILL_FILE as SKILL.md 磁盘文件
    participant HOT_RELOAD as skill_md_hot_reload
    participant CTX9 as PipelineContext
    participant LLM9 as LLM

    Note over EVOLVER: 进化引擎写入新版 SKILL.md

    EVOLVER->>SKILL_FILE: 写入新内容
    EVOLVER->>HOT_RELOAD: notify_skill_md_changed_from_disk_write(path)
    HOT_RELOAD->>CTX9: _skill_sop_dirty = True
    HOT_RELOAD->>CTX9: 世代计数 +1

    Note over CTX9: 下一轮 LLM 调用前（HOOK_BEFORE_LLM_THINK）

    CTX9->>HOT_RELOAD: apply_skill_md_hot_reload_to_react_ctx(ctx)
    HOT_RELOAD->>SKILL_FILE: 强制读盘（force_disk_read）
    SKILL_FILE-->>HOT_RELOAD: 新版 SKILL.md 内容
    HOT_RELOAD->>CTX9: 更新 system_prompt（替换 JACHIN_HR_SKILL_MD_BODY 块）
    HOT_RELOAD->>CTX9: _skill_sop_dirty = False

    CTX9->>LLM9: generate_response（含新版 SOP）
    Note over LLM9: 模型在同一 run_agent 内感知到 SOP 变化
```

---

## 四、长期记忆：Memory Nexus

### 4.1 物理存储架构

```
~/.jachin/palace_db/
└─ memory_nexus.sqlite3
   └─ 表: drawers
      ├─ drawer_id        TEXT    (UUID, 主键)
      ├─ wing             TEXT    (翼区: Episodes/Knowledge/Procedures/Core/Inbox)
      ├─ room             TEXT    (房间名: General_Chat/Core_Profile/Kalaroko_Default/...)
      ├─ document         TEXT    (原文内容，verbatim)
      ├─ embedding        BLOB    (float32 向量，FastEmbed 生成)
      ├─ dim              INTEGER (向量维度)
      ├─ timestamp        REAL    (Unix 时间戳，用于时间衰减)
      └─ extra_meta_json  TEXT    (JSON 附加元数据)
```

**设计选择**：单一 SQLite 文件，无外部向量库依赖，便于单机部署和打包分发。

### 4.2 Wing 命名空间体系

```mermaid
flowchart TB
    NEXUS3["memory_nexus.sqlite3"]

    NEXUS3 --> EP2["Episodes Wing\n半衰期: 30天\n重要性乘数: 1.00\nRoom示例: General_Chat"]
    NEXUS3 --> KN2["Knowledge Wing\n半衰期: 90天\n重要性乘数: 1.20\nRoom示例: TechDocs, DomainKnowledge"]
    NEXUS3 --> PR2B["Procedures Wing\n半衰期: 180天\n重要性乘数: 1.30\nRoom示例: Workflows, SOPs"]
    NEXUS3 --> CO2["Core Wing\n半衰期: 180天\n重要性乘数: 1.25\nRoom示例: Core_Profile, Learned_Skills"]
    NEXUS3 --> IN2["Inbox Wing\n半衰期: 7天\n重要性乘数: 1.00\nRoom示例: PendingTasks"]

    EP2 --> EP_USE["主要用途:\n回合末 commit_drawer\nUser_Persona/General_Chat"]
    CO2 --> CO_USE["主要用途:\nL0/L1 prompt 注入\nCore_Profile + Kalaroko_Default"]
    PR2B --> PR_USE["主要用途:\nSOP / 操作流程沉淀\nProcedures/Workflows"]
    KN2 --> KN_USE["主要用途:\n技术知识/领域知识\nLearned_Skills 归入 Core"]
```

**Wing 归一化**：`normalize_wing()` 将别名统一：
- `user_persona` → `Core`
- `e2e_monitors` → `Core`（监控类）
- `learned_skills` → `Core`

### 4.3 Memory Nexus 核心操作

```mermaid
flowchart TB
    subgraph OPS["Memory Nexus 核心操作（memory_backend.py）"]
        CD["commit_drawer(wing, room, document, metadata)\n写入/更新一条记忆\n① FastEmbed 向量化 document\n② INSERT OR REPLACE INTO drawers"]
        RR["recall_room(wing, room, limit=10)\n精确过滤 wing+room\nORDER BY timestamp DESC\n返回近期条目（不做向量检索）"]
        DS["deep_search(query, wing=None, top_k=5)\n全库语义检索\n见下方检索算法"]
    end

    TOOLS8["Native 工具\ncore:local_memory_search → deep_search\ncore:local_memory_append → commit_drawer"]
    PSEUDO["ReAct 伪动作\nrecall_memory → _recall_memory_search → deep_search"]
    L1_INJECT["Prompt 注入\nbuild_l1_system_memory_block → recall_room"]
    COMMIT["回合末写入\nschedule_nexus_turn_commit_async → commit_drawer"]

    TOOLS8 & PSEUDO & L1_INJECT & COMMIT --> OPS
```

### 4.4 Deep Search 检索算法全流程

```mermaid
flowchart TB
    Q["查询文本 query"]

    Q --> EMB["FastEmbed.encode(query)\n本地向量化\n不发送外网请求\n模型: JACHIN_MEMORY_EMBED_MODEL\n默认: multilingual-e5-small"]

    EMB --> SQL2["SQLite 候选集\nSELECT * FROM drawers\n[WHERE wing = ?]  ← 可选过滤\nORDER BY timestamp DESC\nLIMIT JACHIN_NEXUS_DEEP_SEARCH_CANDIDATES(2500)"]

    SQL2 --> NUMPY["NumPy 内存计算\n候选向量堆叠为矩阵 M(2500×dim)\n查询向量 q(1×dim)\n内积: scores = M @ q.T\n等价于余弦相似度（L2 归一化后）"]

    NUMPY --> SEM_SCORE["语义分 sem_score = scores"]

    SEM_SCORE --> DECAY_CALC["时间衰减分\n_compute_time_decay(timestamp, half_life_days)\nEbbinghaus: decay = exp(-λ·Δt)\nλ = ln(2) / half_life_days\n其中 half_life 按 Wing:\nProcedures=180d, Knowledge=90d, 其余=30d"]

    DECAY_CALC --> BLEND["分数融合\nJACHIN_NEXUS_TIME_DECAY_WEIGHT=0.2(w)\nblended = sem*(1-w) + decay*w"]

    BLEND --> WING_IMP["Wing 重要性乘数\n_compute_wing_importance(wing)\nProcedures=1.30 / Core=1.25 / Knowledge=1.20\nEpisodes=Inbox=1.00\nfinal = blended * mult\nclamp [0,1]"]

    WING_IMP --> TOPK2B["Top-K 选取\ndistance = 1 - final_score\n越小越相似\n返回 matches[]{wing,room,document,distance,metadata}"]
```

**不使用 HNSW**：候选集 2500 条时 NumPy 矩阵乘法在毫秒级完成，无需近似最近邻索引，减少依赖复杂度。

### 4.5 向量写入流程

```mermaid
sequenceDiagram
    participant CALLER8 as 调用方
    participant CB as commit_drawer
    participant EMB8 as FastEmbed
    participant DB8 as SQLite drawers

    CALLER8->>CB: commit_drawer(wing="Core", room="General_Chat", document="用户偏好: 喜欢简洁回复")
    CB->>CB: normalize_wing("Core") → "Core"
    CB->>EMB8: _embed_one(document)
    EMB8->>EMB8: TextEmbedding.embed([document])
    EMB8-->>CB: float32 向量（384维）
    CB->>CB: L2 归一化 → embedding BLOB
    CB->>DB8: INSERT OR REPLACE INTO drawers VALUES(...)
    DB8-->>CB: OK
    CB-->>CALLER8: drawer_id
```

---

## 五、长期记忆：Experience Memory

Experience Memory 专门存储**成功的工具调用路径**，用于 few-shot 经验注入。

### 5.1 写入流程（成功路径沉淀）

```mermaid
flowchart LR
    subgraph WRITE_EXP["经验写入触发条件"]
        COND1["read_query / write_query 工具成功\n且 Observation 启发式判断为成功"]
        COND2["Critic 通过（或 fail-open）"]
        COND3["_l4_exp_save_gate 门控通过\n防止重复写入同一动作"]
    end

    subgraph WRITE_PATH["写入路径"]
        SAVE_ACT["experience_memory.save_successful_action\n(intent, action_type, params, observation)"]
        SAVE_MA["experience_memory.save_multi_agent_episode\n(JACHIN_EXPERIENCE_RECORD_MULTI_AGENT=1)\nmulti_agent:discuss / multi_agent:parallel_delegate"]
        FAIL_EP["save_run_failure_episode\n(JACHIN_EXPERIENCE_AUTO_RECORD_FAIL=1)\nrun_agent:brief 类型"]
    end

    subgraph STORAGE["存储"]
        JSONL3["~/.jachin/l4_experience/experience.jsonl\nNDJSON 格式\n每行: {intent, action_type, params, observation, ts, episode_type}"]
    end

    WRITE_EXP --> WRITE_PATH --> JSONL3
```

### 5.2 检索流程（每次 run_agent 开始）

```mermaid
flowchart TB
    INTENT_IN["当前用户意图 intent"]

    INTENT_IN --> LOAD["加载 experience.jsonl\n读取全量记录（启动时缓存）"]

    LOAD --> TFIDF_MATCH["TF-IDF 向量化 + 余弦相似度\n纯标准库（collections.Counter + math）\n无需外部向量库"]

    TFIDF_MATCH --> OPT_EMB["可选向量重排\nJACHIN_EXPERIENCE_USE_EMBED=1\n使用 FastEmbed 替代 TF-IDF\n(JACHIN_EXPERIENCE_EMBED_PREFILTER: 先 TF-IDF 预过滤)"]

    OPT_EMB --> TOPK3["Top-K（默认 2）\n返回最相关的历史成功案例"]

    TOPK3 --> FORMAT["format_experience_block_for_prompt()\n格式化为 [HISTORY_FEW_SHOTS] 块"]

    FORMAT --> INJECT3["注入 system_prompt 后缀\ntier=0（最高优先级）"]
```

**HISTORY_FEW_SHOTS 块格式示例**：

```
[HISTORY_FEW_SHOTS]
以下是类似任务的历史成功经验，可作为参考：

经验 1（相似度: 0.87）:
  意图: 分析 Q1 销售数据，找出异常门店
  成功动作: core:shell_exec → python analyze.py --quarter Q1
  结果摘要: 发现 3 家门店销量异常，已输出报告

经验 2（相似度: 0.72）:
  意图: 读取 CSV 数据并生成图表
  成功动作: jpp:bi_report → {data_path: "data.csv", chart_type: "bar"}
  结果摘要: 已生成 Q1 对比图表，保存为 report.png
```

---

## 六、长期记忆：PersistedIntent

自治意图持久化详见 [07_OBSERVABILITY_AUTONOMY.md](./07_OBSERVABILITY_AUTONOMY.md)，简要说明：

```mermaid
flowchart LR
    INTENT_DB["~/.jachin/workspace/\npersisted_intents.sqlite3"]

    subgraph CRUD["CRUD 操作（IntentPersister）"]
        CREATE2["save / create\n创建新意图"]
        READ2["list / get\n查询意图列表"]
        UPDATE2["set_enabled / autoreset_failed\n启停/重置"]
        DELETE2["delete\n删除意图"]
        EXEC_LOG["record_execution\n记录执行历史"]
    end

    subgraph TYPES["意图触发类型"]
        T_INT["interval: 每 N 分钟"]
        T_CRON["cron: 定时表达式"]
        T_COND2["condition: 条件触发"]
    end

    INTENT_DB <--> CRUD
    TYPES --> AWL_SCAN["AwarenessLoop 扫描"]
    AWL_SCAN -->|"触发"| RUN_AGENT2["run_agent(intent)"]
```

---

## 七、记忆检索算法详解

### 7.1 时间衰减（Ebbinghaus 遗忘曲线）

```mermaid
flowchart LR
    T_NOW["current_time (Unix ts)"]
    T_CREATE["drawer.timestamp (Unix ts)"]

    T_NOW & T_CREATE --> DELTA["Δt_days = (now - ts) / 86400"]

    DELTA --> LAMBDA["λ = ln(2) / half_life_days\nhalf_life 按 Wing:\nProcedures: 180天\nCore: 180天\nKnowledge: 90天\nEpisodes/Inbox: 30天/7天"]

    LAMBDA --> DECAY2["decay_score = exp(-λ · Δt)\n刚写入时 ≈ 1.0\n经过半衰期后 = 0.5\n远古记忆趋近 0"]

    DECAY2 --> BLEND2["blended_score\n= sem_score * (1-w) + decay_score * w\nw = JACHIN_NEXUS_TIME_DECAY_WEIGHT = 0.2"]
```

### 7.2 Wing 重要性乘数

| Wing | 乘数 | 含义 |
|------|------|------|
| Procedures | 1.30 | SOP 流程最重要，优先召回 |
| Core | 1.25 | 用户核心记忆，次重要 |
| Knowledge | 1.20 | 知识类记忆 |
| Episodes | 1.00 | 对话历史（基准） |
| Inbox | 1.00 | 临时记忆（基准） |

可通过 `JACHIN_WING_IMPORTANCE_OVERRIDE` 运行时 JSON 覆盖单 Wing 系数。

### 7.3 完整打分公式

```
final_score = clamp(blend(sem, decay, w) × wing_mult, 0, 1)

其中:
  sem       = cosine_similarity(query_vec, drawer_vec)
  decay     = exp(-ln(2)/half_life × Δt_days)
  blend     = sem*(1-w) + decay*w    // w=0.2 默认
  wing_mult = Wing 重要性乘数（1.00~1.30）
  distance  = 1 - final_score        // 对外返回，越小越相似
```

---

## 八、记忆注入 Prompt 全流程

### 8.1 注入时序

```mermaid
sequenceDiagram
    participant AGENT10 as run_agent
    participant BRIDGE as memory_nexus_bridge
    participant NEXUS10 as Memory Nexus
    participant EXP10 as ExperienceMemory
    participant SYS10 as _build_system_prompt

    AGENT10->>EXP10: format_experience_block_for_prompt(intent)
    EXP10->>EXP10: TF-IDF 检索 experience.jsonl Top-2
    EXP10-->>AGENT10: experience_few_shots (tier=0)

    AGENT10->>BRIDGE: build_l1_system_memory_block()
    BRIDGE->>NEXUS10: recall_room("Core", "Core_Profile", limit=3)
    NEXUS10-->>BRIDGE: L0 用户侧写条目
    BRIDGE->>NEXUS10: recall_room("E2E_Monitors", "Kalaroko_Default", limit=5)
    NEXUS10-->>BRIDGE: L1 巡检摘要
    BRIDGE->>NEXUS10: recall_room("User_Persona", "General_Chat", limit=8)
    NEXUS10-->>BRIDGE: L1 近期交互摘要
    BRIDGE-->>AGENT10: l1_memory_block (tier=1)

    AGENT10->>SYS10: _build_system_prompt(experience_few_shots, l1_memory_block, ...)
    SYS10->>SYS10: compose_suffix_with_eviction(chunks)
    SYS10-->>AGENT10: system_prompt（含所有记忆注入）
```

### 8.2 三层注入层级对比

| 层级 | 数据源 | 写入时机 | 检索方式 | Prompt 位置 |
|------|--------|---------|---------|------------|
| **L0** | User_Persona/Core_Profile | 用户画像更新时 | recall_room（精确） | 后缀 tier=1 最前 |
| **L1** | General_Chat/Kalaroko_Default | 每轮回合末 | recall_room（精确） | 后缀 tier=1 |
| **按需** | 全库 | 模型主动调用 | deep_search（语义） | Observation（工具返回） |
| **经验** | experience.jsonl | 成功路径沉淀 | TF-IDF 相似度 | 后缀 tier=0 |

---

## 九、记忆写入全路径

```mermaid
flowchart LR
    subgraph WRITE_PATHS2["五条写入路径"]
        W_TURN["① 回合末异步写入\nschedule_nexus_turn_commit_async\nWing: User_Persona/General_Chat\n启发式阈值（消息足够长）\n失败仅日志，不阻塞对话"]
        W_TOOL["② 工具显式写入\ncore:local_memory_append → commit_drawer\nWing: User_Persona/Learned_Skills\n模型主动触发"]
        W_SKILL["③ 技能矩阵同步\nsync_all_tools_to_nexus\n将工具描述向量化存入 Nexus\n启动时执行"]
        W_EXP["④ Experience RAG 写入\nexperience_memory.save_successful_action\n成功路径沉淀\n不写 Nexus，写 experience.jsonl"]
        W_BIZ["⑤ 业务代码封装\nadd_local_memory(content, metadata)\nHR/BI 等域直接调用\n写入 User_Persona/Learned_Skills"]
    end

    W_TURN & W_TOOL & W_SKILL & W_BIZ --> NEXUS_DB[("memory_nexus.sqlite3\ndrawers 表")]
    W_EXP --> JSONL_FILE[("experience.jsonl\nNDJSON 文件")]
```

### 9.1 回合末写入详细判断

```mermaid
flowchart TD
    END_TURN["run_agent 完成返回"]
    END_TURN --> LEN_CHK{"user_msg 或 assistant_reply\n足够长? (启发式阈值)"}

    LEN_CHK -->|"太短（寒暄）"| SKIP_COMMIT["跳过写入\n(JACHIN_NEXUS_TURN_COMMIT_SKIP_LOW_VALUE=1)"]

    LEN_CHK -->|"足够长"| CONTENT_CHK{"含低价值标记?\n[ExecutionBrief]/[未产出回复]\n/【需要补充信息】等"}

    CONTENT_CHK -->|"是"| SKIP_COMMIT
    CONTENT_CHK -->|"否"| ASYNC_COMMIT["asyncio.create_task(commit_drawer(...))\n异步写入不阻塞\nfail-open: 异常仅 log"]
```

---

## 十、遗留与停用项

| 项目 | 状态 | 替代方案 |
|------|------|---------|
| `~/.jachin/memory/l3_local.json` | **只读/诊断** | Memory Nexus（SQLite） |
| `l3_local_shard_<id>.json` | **只读** | Memory Nexus 共享 SSOT |
| `memory_compactor.compact_local_memory_if_needed` | **全局 no-op** | 上下文 Compaction（非 JSON 梦境合并） |
| `l3_memory.json` + MemorySyncDaemon | **已删除** | Memory Nexus 本地闭环，无需 L2 同步 |
| `merge_from_l2()` | **空操作** | 历史兼容占位 |
| `bump_urgent_l3_local_sync()` | **兼容占位** | 不再驱动任何 L2 同步 |
| Chroma HTTP 客户端降级路径 | **已移除** | 单 SQLite 文件（`CHROMA_USE_HTTP_CLIENT` 配置保留无害） |

**重要**：`get_local_memory_for_prompt()` 已委托 Memory Nexus L1 块（`build_l1_system_memory_block`），旧 JSON 被动衰减参数不再生效。

---

**上一篇**: [03_MULTI_AGENT.md](./03_MULTI_AGENT.md)  
**下一篇**: [05_AGI_CORE_CAPABILITIES.md](./05_AGI_CORE_CAPABILITIES.md) — AGI 核心能力模块
