# Jachin 主 Agent 设计详解

> **分册**: 02 / 07 · [返回索引](./README.md)  
> **代码锚点**: `l3_node/agent_core.py`（`run_agent`、`_build_system_prompt`、`_run_react_core`）  
> **专题 SSOT**: [`L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md`](../L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md)、[`JACHIN_HYBRID_AGENT_ARCHITECTURE.md`](../architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md)

---

## 目录

1. [核心设计哲学](#一核心设计哲学)
2. [run_agent 入口全流程](#二run_agent-入口全流程)
3. [ReAct 循环引擎](#三react-循环引擎)
4. [System Prompt 拼装工程](#四system-prompt-拼装工程)
5. [三档模型自动路由](#五三档模型自动路由)
6. [工具池组装（assemble_tool_pool）](#六工具池组装assemble_tool_pool)
7. [意图网关与预检（agent_preflight）](#七意图网关与预检agent_preflight)
8. [Hook 链体系](#八hook-链体系)
9. [上下文管理与 metadata](#九上下文管理与-metadata)
10. [前台/后台通道区分](#十前台后台通道区分)

---

## 一、核心设计哲学

Jachin 主 Agent 的最核心原则：

```
单主轴 ReAct：永远只有一条 run_agent 主循环
                ↕ 按需扩展
  ┌─────────────────────────────────────────┐
  │  delegate → SubAgent (嵌套 run_agent)    │
  │  coordinate → L2 跨节点（轮询等待）       │
  │  submit_background_task → 异步队列        │
  └─────────────────────────────────────────┘
```

**不是**「对等多 Agent 拓扑」：主 Agent 是绝对中心，其他形态是其正交分支。

---

## 二、run_agent 入口全流程

### 2.1 入口总览

```mermaid
flowchart TB
    subgraph ENTRY["run_agent 入口序列"]
        A["① agent_preflight\n域短路预检\n(HR停止/BI一键/同意发布等)"]
        B["② routing/plugins\n域突变插件注册"]
        C["③ apply_gateway_ingress_pipeline\n意图嗅探+语义层+环境报告"]
        D["④ experience_memory.format_experience_block\nExperience RAG 检索 → few_shots"]
        E["⑤ assemble_tool_pool\nNative+Wasm+MCP 权限合并"]
        F["⑥ _build_system_prompt\n前缀+后缀完整拼装"]
        G["⑦ _run_react_core\nReAct 主循环启动"]
        H["⑧ schedule_nexus_turn_commit_async\n回合末异步写记忆"]
    end

    A --> B --> C --> D --> E --> F --> G --> H

    A -->|"短路: HR/BI 命令"| SHORTCUT["直接返回固定文案\n不进入 LLM"]
    G -->|"后台通道"| BG_MOD["去除 submit_background_task\nallow_delegate=False\nallow_coordinate=False"]
```

### 2.2 详细入口时序

```mermaid
sequenceDiagram
    participant CALLER as 调用方（WS/HTTP/IM）
    participant PF as agent_preflight
    participant GW as gateway_pipeline
    participant EXP as ExperienceMemory
    participant POOL as assemble_tool_pool
    participant SYS as _build_system_prompt
    participant REACT as _run_react_core

    CALLER->>PF: run_agent(user_input, engine, ...)
    PF->>PF: apply_inbound_preflight(user_input)

    alt HR 停止/BI 一键/同意发布等
        PF-->>CALLER: 短路返回（不调 LLM）
    else 正常流
        PF->>GW: apply_gateway_ingress_pipeline(input, config)
        GW->>GW: ClarificationRule 澄清检测
        GW->>GW: context_sniffer 意图嗅探
        GW->>GW: 加载 db_semantics.yaml → semantic_layer
        GW->>GW: environment_report 环境摘要
        GW-->>PF: GatewayContextBundle

        PF->>EXP: format_experience_block_for_prompt(intent)
        EXP->>EXP: TF-IDF 检索 experience.jsonl Top-K
        EXP-->>PF: experience_few_shots

        PF->>POOL: assemble_tool_pool(allowed_skills, channel)
        POOL->>POOL: load Native + scan Wasm + fetch MCP
        POOL->>POOL: 权限白名单过滤 + 后台通道裁剪
        POOL-->>PF: tools[]

        PF->>SYS: _build_system_prompt(tools, experience_few_shots, ...)
        SYS-->>PF: system_prompt (前缀+后缀)

        PF->>REACT: _run_react_core(ctx, engine)
        REACT-->>PF: Final Answer

        PF->>PF: schedule_nexus_turn_commit_async（异步）
        PF-->>CALLER: Final Answer
    end
```

---

## 三、ReAct 循环引擎

### 3.1 单轮迭代完整流程

```mermaid
flowchart TB
    subgraph ITER["单次 ReAct 迭代（max_iterations 默认 8）"]
        HOOK_PRE["HOOK_BEFORE_LLM_THINK\n① SKILL.md 热重载检测\n② DAG 重规划触发\n③ hot_inject drain 并入 messages\n④ 策略链注入消息"]

        LLM_CALL["LiteLLMEngine.generate_response\n三档模型路由决策\n_react_engine_for_iteration"]

        PARSE["_parse_action\n识别输出类型:\nFinal Answer / Action+ActionInput"]

        GATE["intelligence_b 门禁\n计划卡检测 (planned/strict)\nbrainstorm 卡检测\nverify 轮强制只读"]

        DISPATCH{"Action 类型?"}

        CRITIC_CHK["critic_agent.evaluate_action\n仅 SQLite 族 + 策略命中路径\n轻量 LLM 审查"]

        EXEC_NATIVE["run_tool(tool_id, params)\nNative / Wasm"]
        EXEC_MCP["mcp_registry.invoke\nMCP stdio 进程"]
        EXEC_RECALL["_recall_memory_search\n→ Memory Nexus deep_search"]
        EXEC_DELEG["_run_sub_agent × N\n并行 SubAgent"]
        EXEC_COORD["_coordinate_task\n→ L2 coordinate API"]
        EXEC_BG["background_task_service\n→ asyncio Queue"]

        HOOK_POST["HOOK_AFTER_TOOL_EXEC\n① context_prefetch 附件\n② observation_dedup 折叠\n③ experience 写入门控"]

        OBS["Observation 追加到 messages\nctx.messages.append({role:'user', content:obs})"]

        EXIT{"Final Answer\n或迭代上限?"}
    end

    HOOK_PRE --> LLM_CALL --> PARSE --> GATE

    GATE -->|"门禁未过 (inject 伪 Obs)"| OBS
    GATE -->|"通过"| DISPATCH

    DISPATCH -->|"SQLite 族"| CRITIC_CHK
    CRITIC_CHK -->|"通过/fail-open"| EXEC_NATIVE
    CRITIC_CHK -->|"拒绝 → 伪 Obs"| OBS

    DISPATCH -->|"Native/Wasm"| EXEC_NATIVE
    DISPATCH -->|"MCP"| EXEC_MCP
    DISPATCH -->|"recall_memory"| EXEC_RECALL
    DISPATCH -->|"delegate"| EXEC_DELEG
    DISPATCH -->|"coordinate"| EXEC_COORD
    DISPATCH -->|"submit_background_task"| EXEC_BG
    DISPATCH -->|"Final Answer"| EXIT

    EXEC_NATIVE & EXEC_MCP & EXEC_RECALL & EXEC_DELEG & EXEC_COORD & EXEC_BG --> HOOK_POST --> OBS --> EXIT
    EXIT -->|"否"| HOOK_PRE
    EXIT -->|"是"| DONE["返回 Final Answer"]
```

### 3.2 ReAct 详细时序

```mermaid
sequenceDiagram
    participant CTX as PipelineContext
    participant HOOKS as GlobalHooks
    participant LLM as LiteLLMEngine
    participant CRITIC as critic_agent
    participant TOOL as run_tool/MCP
    participant MEM as Memory Nexus
    participant EXP as ExperienceMemory

    Note over CTX: 初始化: messages + system_prompt + metadata

    loop 每轮迭代 (i=1..max_iterations)

        CTX->>HOOKS: HOOK_BEFORE_LLM_THINK(ctx)
        HOOKS->>CTX: apply_skill_md_hot_reload（P1/P2）
        HOOKS->>CTX: maybe_replan_during_react（DAG 重规划）
        HOOKS->>CTX: drain_pending_session_user_texts（热并入）
        HOOKS->>CTX: pop_strategy_inject_message（策略链）

        CTX->>LLM: generate_response(full_messages, engine_i)
        Note over LLM: engine_i = coder/complex/daily 三档路由
        LLM-->>CTX: "Thought: ...\nAction: xxx\nAction Input: {...}"

        CTX->>CTX: _parse_action → type + params

        alt type = Final Answer
            CTX->>CTX: 提取 Final Answer 文本
            CTX-->>CTX: 退出循环
        else intelligence_b 门禁未通过
            CTX->>CTX: 注入伪 Observation（要求先做计划/brainstorm）
        else SQLite 族工具
            CTX->>CRITIC: evaluate_action(action_type, params, ctx)
            alt Critic 通过
                CRITIC-->>CTX: CriticResult(pass=True)
                CTX->>TOOL: run_tool(tool_id, params)
                TOOL-->>CTX: result_str
                CTX->>EXP: save_successful_action（门控：_l4_exp_save_gate）
            else Critic 拒绝
                CRITIC-->>CTX: CriticResult(pass=False, critique="...")
                CTX->>CTX: inject "Critic 审查未通过: {critique}"
            else Critic 超时/异常
                CRITIC-->>CTX: fail-open → Warning 日志
                CTX->>TOOL: run_tool(tool_id, params)
            end
        else 普通 Native/Wasm 工具
            CTX->>TOOL: run_tool(tool_id, params)
            TOOL-->>CTX: result_str
        else MCP 工具
            CTX->>TOOL: mcp_registry.invoke(tool_id, params)
            TOOL-->>CTX: result_str
        else recall_memory
            CTX->>MEM: deep_search(query=action_input)
            MEM-->>CTX: matches[] (wing/room/document)
        else delegate
            CTX->>CTX: _run_sub_agent × N（异步并行）
        else coordinate
            CTX->>CTX: _coordinate_task → L2 API
        else submit_background_task
            CTX->>CTX: background_task_service.submit
        end

        CTX->>HOOKS: HOOK_AFTER_TOOL_EXEC(ctx, tool_id, result)
        HOOKS->>CTX: context_prefetch.build_prefetch_attachment
        HOOKS->>CTX: observation_dedup 检测并折叠重复路径

        CTX->>CTX: 追加 Observation 到 messages
    end

    CTX->>MEM: schedule_nexus_turn_commit_async
```

### 3.3 _parse_action 解析逻辑

```mermaid
flowchart TB
    RAW["LLM 原始输出\n（字符串）"]

    RAW --> FA_CHK{"含 'Final Answer:'?"}
    FA_CHK -->|"是"| FA["type=final_answer\ncontent=提取文本"]

    FA_CHK -->|"否"| ACT_CHK{"含 'Action:' 且\n'Action Input:'?"}
    ACT_CHK -->|"否"| FALLBACK["type=unknown\n注入提示重新格式化"]

    ACT_CHK -->|"是"| ACT_PARSE["提取 action_name + action_input_json"]
    ACT_PARSE --> ROUTE{"action_name?"}

    ROUTE -->|"recall_memory"| REC["type=recall"]
    ROUTE -->|"delegate"| DEL["type=delegate"]
    ROUTE -->|"coordinate"| COORD["type=coordinate"]
    ROUTE -->|"core:submit_background_task"| BG["type=background_task"]
    ROUTE -->|"其他 tool_id"| TOOL["type=tool\ntool_id=action_name"]

    TOOL --> GUARD["PolicyEnforcer 鉴权\nis_tool_allowed(tool_id, sub_account_id)"]
    GUARD -->|"允许"| EXEC_OK["执行工具"]
    GUARD -->|"拒绝"| EXEC_DENY["注入权限拒绝 Observation"]
```

---

## 四、System Prompt 拼装工程

### 4.1 拼装层次结构

```mermaid
flowchart TB
    subgraph PREFIX["前缀 prompt_prefix（相对静态，利于 KV Cache）"]
        P1_R["ReAct 范式说明\nThought/Action/Action Input/Observation/Final Answer"]
        P2_IB["intelligence_b 执行模式\nreact / planned / strict 三种"]
        P3_TH["前台超时预算说明\nchat_task_hint（按通道注入）"]
        P4_TOOLS["可用工具表\nbuild_tools_description(tools)\n稳定工具靠前，MCP 靠后"]
        P5_MEM["recall_memory / coordinate / delegate 提示"]
        P6_FMT["固定输出格式约束"]
        P7_SEP["--- 以下段落随会话状态变化 ---"]
    end

    subgraph SUFFIX["后缀 prompt_suffix（易变，compose_suffix_with_eviction 按 tier 拼装）"]
        S1["[HISTORY_FEW_SHOTS]\nExperience RAG 经验块 (tier=0, 最高优先级)"]
        S2["系统近期核心记忆\nbuild_l1_system_memory_block (tier=1)"]
        S3["JACHIN 工作区规则\njachin_workspace_rules (tier=2)"]
        S4["task_plan / progress / findings\nget_planning_context_for_prompt (tier=3)"]
        S5["TaskDAG active.json\nformat_active_task_dag_prompt_suffix (tier=3)"]
        S6["HR 运行时上下文\nget_hr_recruitment_runtime_context (tier=4)"]
        S7["P1 注入\nintelligence_p1.get_p1_prompt_injections (tier=5)"]
        S8["能力总目录\nbuild_capability_prompt_inject_for_tools (tier=6)"]
        S9["HR SKILL.md 长 SOP\n(按意图信号条件注入，tier=7)"]
        S10["delegate 角色表 hint\nformat_role_pool_delegate_addon (tier=8)"]
        S11["Final Answer 约束\n(禁止篡改 Observation 等，tier=9)"]
    end

    PREFIX --> SUFFIX

    EVICT["prompt_suffix_max_chars 硬帽\n超标时低 tier 先裁\n打 [prompt_suffix_eviction] 日志"]
    SUFFIX --> EVICT
```

### 4.2 后缀 tier 优先级与裁剪规则

| tier | 内容块 | 优先级 | 备注 |
|------|--------|--------|------|
| 0 | Experience RAG `[HISTORY_FEW_SHOTS]` | 最高 | 历史成功路径，最重要 |
| 1 | Memory Nexus L1 近期核心记忆 | 高 | L0(Core_Profile) + L1(General_Chat) |
| 2 | JACHIN 工作区规则 | 高 | `workspace/JACHIN.md` |
| 3 | task_plan / progress + TaskDAG | 中 | 跨轮任务状态 |
| 4 | HR 运行时上下文 | 中 | 含 scheduler 状态 |
| 5 | P1 注入 | 中 | 意图-工具统计 |
| 6 | 能力总目录 | 低 | DOMAIN_REGISTRY 注入 |
| 7 | HR SKILL.md 长 SOP | 低 | 按意图信号条件注入 |
| 8 | delegate 角色表 | 低 | 仅当 allow_delegate=True |
| 9 | Final Answer 约束 | 最低 | 格式约束兜底 |

### 4.3 子 Agent System Prompt（简化版）

子 Agent **不走** `_build_system_prompt` 完整流程，使用简化版：

```
{role_short_prompt}            ← SUB_AGENT_PROMPTS[role]
可用工具：
{build_tools_description(allowed_tools)}
输出格式：Thought / Action / Action Input / Observation / Final Answer
```

---

## 五、三档模型自动路由

### 5.1 路由决策树

```mermaid
flowchart LR
    START["每轮迭代开始\n_react_engine_for_iteration(ctx, i)"]

    START --> CHK_CODER{"本轮已执行\nfs_write 或 apply_patch?"}
    CHK_CODER -->|"是"| CODER_ENGINE["编码档\nLLM_CODER_MODEL\n默认: qwen3-coder-plus\n优先级最高"]

    CHK_CODER -->|"否"| CHK_COMPLEX{"满足任一复杂条件?"}

    subgraph COMPLEX_CONDITIONS["复杂档触发条件（任一满足即触发）"]
        CC1["delegate_depth > 0\n(子 Agent 内部)"]
        CC2["ReAct 轮次 ≥ JACHIN_LLM_COMPLEX_MIN_REACT_ITER\n默认第 9 轮起（0-based ≥ 8）"]
        CC3["可见工具数 ≥ JACHIN_LLM_COMPLEX_MIN_TOOLS\n默认 28"]
        CC4["最后 user 消息 ≥ JACHIN_LLM_COMPLEX_MIN_USER_CHARS\n默认 2400 字符"]
        CC5["messages 总条数 ≥ JACHIN_LLM_COMPLEX_MIN_MESSAGES\n默认 28"]
        CC6["intelligence_b 为 planned 或 strict\n强制复杂档"]
    end

    CHK_COMPLEX -->|"是"| COMPLEX_ENGINE["复杂档\nLLM_COMPLEX_MODEL\n默认: qwen-max\nmax_tokens 自动钳制 ≤ 8192"]
    CHK_COMPLEX -->|"否"| DAILY_ENGINE["日常档\nLLM_MODEL\n默认: qwen3.5-plus"]
```

### 5.2 模型路由配置

| 配置方式 | 优先级 | 说明 |
|----------|--------|------|
| `nexus_config.json` → `llm.complex_model_name` | 高 | 覆盖 `LLM_COMPLEX_MODEL` |
| 环境变量 `LLM_COMPLEX_MODEL` | 中 | 进程级设置 |
| 环境变量 `LLM_MODEL` | 低 | 日常档默认 |
| `JACHIN_LLM_COMPLEX_DISABLE=1` | 特殊 | 关闭自动复杂路由 |

---

## 六、工具池组装（assemble_tool_pool）

### 6.1 组装流程

```mermaid
flowchart TB
    START2["assemble_tool_pool(allowed_skills, channel, ...)"]

    subgraph NATIVE_LOAD["Native 工具加载"]
        N1["loader.py 扫描\ncore_util_tools + hr_tools + bi_tools + ..."]
        N2["按 allowed_skills 白名单过滤\nis_tool_allowed(tool_id, allowed)"]
        N3["后台通道裁剪\n移除 core:submit_background_task"]
    end

    subgraph WASM_LOAD["Wasm 工具加载"]
        W1["scan_wasm_plugins\n扫描 l3_skill_cache/ 目录"]
        W2["wasm_runner.py 加载\njpp:* 工具注册"]
        W3["同样走白名单过滤"]
    end

    subgraph MCP_LOAD["MCP 工具加载（追加在后）"]
        M1["mcp_registry.fetch_tools_from_l2\n或本地 stdio 已运行进程"]
        M2["allowed_skills 过滤 + L2 service_switches"]
        M3["sort_tools_by_id\n稳定工具靠前，MCP 靠后"]
    end

    subgraph DYNAMIC["动态工具裁剪（可选）"]
        D1["JACHIN_DYNAMIC_TOOL_RETRIEVAL=1"]
        D2["async_filter_tools_for_dynamic_retrieval\n与用户意图余弦相似度过滤"]
        D3["fail-open: 超时则返回全量"]
    end

    START2 --> NATIVE_LOAD --> WASM_LOAD --> MCP_LOAD --> DYNAMIC
    DYNAMIC --> RESULT["tools[] 最终工具表\n注入 _build_system_prompt"]
```

### 6.2 工具 ID 命名规范

| 前缀 | 原语归类 | 示例 |
|------|---------|------|
| `core:` | Tools（Native） | `core:fs_read`、`core:shell_exec`、`core:local_memory_search` |
| `jpp:` | Tools（Wasm） | `jpp:hr_analyzer4`、`jpp:bi_report` |
| `mcp:` | MCP | `mcp:atom_web_scraper:search`、`mcp:feishu:send_message` |

---

## 七、意图网关与预检（agent_preflight）

### 7.1 预检短路逻辑

```mermaid
flowchart TD
    INPUT3["用户原始输入"]

    INPUT3 --> CHK_HR{"HR 停止收网\n短语匹配?"}
    CHK_HR -->|"是"| HR_STOP["stop_automated_recruitment()\n返回固定文案\n不调 LLM"]

    CHK_HR -->|"否"| CHK_BI{"BI 一键报告\n精确意图?"}
    CHK_BI -->|"是"| BI_RUN["run_bi_daily_report()\n直接执行\n不调 LLM"]

    CHK_BI -->|"否"| CHK_AGREE{"飞书「同意」+ 有待发 JD?"}
    CHK_AGREE -->|"是"| PUBLISH["_execute_publish_bypass()\nJD 发布短路"]

    CHK_AGREE -->|"否"| CHK_LARK{"飞书高优指令?"}
    CHK_LARK -->|"停止招聘"| STOP_REC["stop_hr_full_recruitment()"]
    CHK_LARK -->|"分析指令"| ANAL["触发 HR 分析任务"]

    CHK_LARK -->|"否"| NORMAL["正常流 → gateway_pipeline"]
    NORMAL --> PLUGIN_APPLY["routing/plugins.apply_registered_plugins\n域突变注册插件"]
    PLUGIN_APPLY --> GW_IN["apply_gateway_ingress_pipeline"]
```

### 7.2 意图网关内部

```mermaid
sequenceDiagram
    participant GW as gateway_pipeline
    participant CLR as ClarificationRule
    participant SNF as context_sniffer
    participant SEM as db_semantics.yaml
    participant ENV as environment_report

    GW->>CLR: check_clarification_needed(input)
    alt 需要澄清
        CLR-->>GW: 澄清问题列表
        GW-->>GW: 返回澄清请求（不进入 LLM ReAct）
    else 意图清晰
        CLR-->>GW: pass
    end

    GW->>SNF: sniff_context(input, config)
    SNF->>SNF: 意图关键词提取
    SNF->>SNF: 历史会话上下文感知
    opt JACHIN_CONTEXT_SNIFFER_MEMORY_TIMEOUT_SEC 内
        SNF->>SNF: Nexus 记忆辅助嗅探（可选）
    end
    SNF-->>GW: ContextSniffResult{intent_type, domain, signals}

    GW->>SEM: load db_semantics.yaml（工作区优先，次选仓库）
    SEM-->>GW: semantic_layer{table_mapping, field_aliases, ...}

    GW->>ENV: generate_environment_report()
    ENV->>ENV: 磁盘状态 + Token 用量 + 运行任务摘要
    ENV-->>GW: env_report_str

    GW-->>GW: 构建 GatewayContextBundle
    Note over GW: bundle.extra["semantic_layer"] = semantic_layer
    Note over GW: bundle.extra["environment_report"] = env_report_str
```

---

## 八、Hook 链体系

### 8.1 Hook 触发点全景

```mermaid
flowchart LR
    subgraph HOOKS2["全局 Hook 触发点（hooks_pipeline.py）"]
        H1["HOOK_ON_INTENT_RECEIVED\n入口处理完毕后"]
        H2["HOOK_BEFORE_LLM_THINK\n每轮 LLM 调用前"]
        H3["HOOK_BEFORE_TOOL_EXEC\nCritic 之前，工具执行前"]
        H4["HOOK_AFTER_TOOL_EXEC\n工具执行后"]
        H5["HOOK_BEFORE_RESPONSE\nFinal Answer 返回前"]
        H6["HOOK_ON_TASK_NODE_DONE\nTaskDAG 节点完成"]
        H7["HOOK_ON_RETRY\n重试触发"]
        H8["HOOK_ON_EXECUTION_BRIEF\nExecutionBrief 产出"]
        H9["HOOK_ON_EXPERIENCE_LEARNED\n经验写入成功"]
        H10["HOOK_ON_TASK_DECOMPOSE\ndelegate 子任务创建"]
        H11["HOOK_ON_STRATEGY_SHIFT\n策略切换 [StrategyShift]"]
        H12["HOOK_ON_MEMORY_COMMIT\nNexus 写入成功"]
    end

    subgraph HOOK_HANDLERS["Hook 处理器（注册示例）"]
        DAG_SYNC["dag_node_sync\n→ H6: 更新 active.json"]
        EXP_RECORD["experience_auto_record\n→ H4: 成功路径自动沉淀"]
        STRATEGY["execution_resilience_chain\n→ H7/H11: 策略链 inject"]
        REPLAY["hook_replay_executor\n→ H8: 自动续跑"]
        HOOK_LOG["persistent_hook_log\n→ ALL: SQLite 落盘（可选）"]
    end

    H6 --> DAG_SYNC
    H4 --> EXP_RECORD
    H7 & H11 --> STRATEGY
    H8 --> REPLAY
    H1 & H2 & H3 & H4 & H5 --> HOOK_LOG
```

### 8.2 Hook 持久化与回放

```mermaid
sequenceDiagram
    participant AGENT as run_agent
    participant HOOK as hooks_pipeline
    participant LOG as hook_events.sqlite3
    participant REPLAY as hook_replay_executor

    Note over AGENT,LOG: JACHIN_PERSIST_HOOKS=1 时开启

    AGENT->>HOOK: HOOK_ON_TASK_NODE_DONE(node_id, status)
    HOOK->>LOG: INSERT hook_event(run_id, type, payload, ts)
    HOOK->>LOG: INSERT hook_event(...)

    Note over AGENT,LOG: 进程崩溃或任务中断

    REPLAY->>LOG: read_hook_events_chronological(run_id)
    LOG-->>REPLAY: [event1, event2, ...]
    REPLAY->>REPLAY: probe_dag_resume(run_id)
    Note over REPLAY: 从 hook_events 找 completed_node_ids
    REPLAY->>REPLAY: apply_dag_resume(run_id)
    Note over REPLAY: 将 pending 节点重置，写回 active.json

    alt JACHIN_HOOK_REPLAY_AUTO_RUN=1
        REPLAY->>AGENT: run_agent(resume_intent)
        Note over AGENT: 续跑，跳过已完成节点
    end
```

---

## 九、上下文管理与 metadata

### 9.1 PipelineContext.metadata 关键字段

```mermaid
flowchart TB
    subgraph META["PipelineContext.metadata 关键字段"]
        subgraph ROUTING["路由与通道"]
            M_CHAN["_implicit_channel\n'background_task'/'delegate_sub_agent'/''"]
            M_SKILL["_skills / _skills_unfiltered\n当前可见工具列表"]
            M_ALLOW["_allowed_skills\n执行层白名单"]
        end

        subgraph ITER["迭代控制"]
            M_MAX["_max_iterations\nReAct 上限"]
            M_REACT_I["_react_iteration\n当前轮次（1起）"]
            M_DEPTH["_delegate_depth\n嵌套深度"]
        end

        subgraph CONTEXT["上下文工程"]
            M_LEDGER["_context_path_ledger\npath→last_seen_iteration 滑窗"]
            M_PREFETCH["_prefetch_paths_shown\n已附加路径集合"]
            M_BYTES["_prefetch_session_bytes\n本 run 已附加字节数"]
            M_GATEWAY["_gateway_bundle\nGatewayContextBundle"]
        end

        subgraph L4["L4 门控"]
            M_EXP_GATE["_l4_exp_save_gate\n经验写入门控"]
            M_CRITIC_S["_l4_critic_reject_streak\nCritic 连续打回计数"]
            M_SYS_EXTRA["_system_prompt_extras\nsemantic_layer/experience_few_shots"]
        end

        subgraph LARK["IM 集成"]
            M_LARK["_lark_chat_id\n飞书会话隔离"]
            M_ON_STEP["_on_step / _on_chunk\n流式回调"]
        end
    end
```

### 9.2 context_path_ledger 去重机制

```mermaid
sequenceDiagram
    participant TOOL2 as 工具执行
    participant LEDGER as context_path_ledger
    participant PREFETCH2 as context_prefetch

    Note over LEDGER: dict: path_key → last_seen_react_iteration

    TOOL2->>LEDGER: register_path(path_key, current_iteration)
    LEDGER->>LEDGER: path_key → i

    PREFETCH2->>LEDGER: is_path_stale(path_key, current_iteration, window=3)
    alt last_seen_iteration < current - window
        LEDGER-->>PREFETCH2: True（路径过期，可重新附加）
        PREFETCH2->>PREFETCH2: 附加到 Observation 后
    else 路径在近 N 轮内已出现
        LEDGER-->>PREFETCH2: False（跳过，避免重复）
    end
```

---

## 十、前台/后台通道区分

### 10.1 通道差异对比

| 特性 | 前台（websocket/lark_im） | 后台（background_task） |
|------|--------------------------|------------------------|
| `run_agent` 调用方式 | 同步（SIQ/chat_lock 保序） | 异步（bg-worker-N 消费） |
| 可用工具 | 完整工具表 | 去除 `submit_background_task` |
| allow_delegate | ✅ 允许 | ✗ 关闭 |
| allow_coordinate | ✅ 允许 | ✗ 关闭 |
| context_prefetch | ✅ 附加 | ✗ 跳过 |
| 超时机制 | `asyncio.wait_for` 前台预算 | 无超时限制 |
| 结果推送 | 流式返回用户 | WebSocket broadcast |
| TaskDAG 重规划 | ✅ 每 N 轮 | ✗ 关闭 |

### 10.2 后台任务提交与执行

```mermaid
sequenceDiagram
    participant MAIN_A as 主 run_agent
    participant BG_SVC as BackgroundTaskService
    participant QUEUE as asyncio.Queue
    participant SQLITE_P as SQLite 持久化
    participant WORKER as bg-worker-N
    participant WS4 as WebSocket

    MAIN_A->>BG_SVC: submit_background_task_sync(intent, priority, tags)
    BG_SVC->>QUEUE: put(BackgroundJob{...})
    BG_SVC->>SQLITE_P: 持久化 job（防进程重启丢失）
    BG_SVC-->>MAIN_A: task_id（立即返回）
    MAIN_A-->>MAIN_A: Final Answer 含 task_id

    WORKER->>QUEUE: get()
    WORKER->>WORKER: _run_job(job)
    WORKER->>WORKER: run_agent(intent, channel="background_task")

    loop 执行中
        WORKER->>WS4: broadcast_background_task_event("progress", ...)
    end

    WORKER->>WS4: broadcast_background_task_event("completed", result)
    WORKER->>SQLITE_P: 更新 job status=done
```

---

**上一篇**: [01_THREE_LAYER_SYSTEM.md](./01_THREE_LAYER_SYSTEM.md)  
**下一篇**: [03_MULTI_AGENT.md](./03_MULTI_AGENT.md) — 多 Agent 架构详解
