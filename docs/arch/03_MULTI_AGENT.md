# Jachin 多 Agent 架构详解

> **分册**: 03 / 07 · [返回索引](./README.md)  
> **代码锚点**: `l3_node/agent_core.py`（delegate/coordinate 分支）、`l3_node/primitives/multi_agent/`、`l3_node/primitives/agent_tasks/background_task_service.py`

---

## 目录

1. [设计原则：单主轴 + 正交分支](#一设计原则单主轴--正交分支)
2. [四种多 Agent 形态](#二四种多-agent-形态)
3. [delegate — 同进程 SubAgent（主路径）](#三delegate--同进程-subagent主路径)
4. [SubAgent 角色体系](#四subagent-角色体系)
5. [三种编排模式](#五三种编排模式)
6. [coordinate — 跨节点 L2 调度](#六coordinate--跨节点-l2-调度)
7. [后台异步 Agent Task](#七后台异步-agent-task)
8. [StructuredResultMerger（结果合并）](#八structuredresultmerger结果合并)
9. [并发控制与限速](#九并发控制与限速)
10. [禁止事项与安全边界](#十禁止事项与安全边界)

---

## 一、设计原则：单主轴 + 正交分支

```mermaid
flowchart TB
    subgraph MAIN_AXIS["单主轴（永远只有一条）"]
        RA["run_agent ReAct 主循环"]
    end

    subgraph BRANCHES["按需正交分支（不改变主轴定义）"]
        D["delegate\n同进程 SubAgent × N\n嵌套 run_agent"]
        C["coordinate\nL2 跨节点 API\n等待返回"]
        BG5["submit_background_task\n异步队列\n立即返回 task_id"]
        MCP_P["MCP Pull delegate\nRedis 队列跨进程"]
    end

    RA -->|"Action: delegate"| D
    RA -->|"Action: coordinate"| C
    RA -->|"core:submit_background_task"| BG5
    RA -.->|"L2 主动推送"| MCP_P

    D & C & BG5 -->|"Observation"| RA

    RULE["核心规则:\n• 主轴是绝对中心\n• 多 Agent 是按需扩展\n• 不是对等拓扑"]
```

**多 Agent 的价值场景**：

| 场景 | 单 Agent 问题 | 多 Agent 价值 |
|------|-------------|--------------|
| 同时分析 10 份简历 | 串行 O(n) 时间，前台等待 | FanOut 并行 → O(1) |
| 编码 + 文档 + 测试 | 角色混淆，模型切换出错 | 专属角色 SubAgent |
| 大型重构任务 | max_iterations 耗尽截断 | Pipeline 分阶段 |
| 方案评审 | 单模型无法自我质疑 | Discussion 辩论模式 |
| 多设备集群 | 单节点算力/速率有限 | coordinate 跨节点 |

---

## 二、四种多 Agent 形态

```mermaid
flowchart TB
    USER5[用户消息] --> MAIN5[主 ReAct run_agent]

    MAIN5 -->|"同进程\n最主路径"| FORM1["① delegate\nSubAgent 并行"]
    MAIN5 -->|"跨物理节点\n轮询等待"| FORM2["② coordinate\nL2 跨节点"]
    MAIN5 -->|"异步队列\n立即返回"| FORM3["③ background_task\nasyncio 队列"]
    MAIN5 -.->|"L2 推送\n跨进程"| FORM4["④ MCP Pull delegate\nRedis 队列"]

    subgraph F1["① delegate 内部"]
        SEM5[Semaphore max=4]
        GATHER5[asyncio.gather]
        SA_POOL["SubAgent 角色池\n13 种角色"]
        MERGER5[StructuredResultMerger]
        SEM5 --> GATHER5 --> SA_POOL --> MERGER5
    end

    subgraph F2["② coordinate 内部"]
        L2_API["POST /coordinate/task"]
        NODE_SEL["节点匹配\nskill_required"]
        POLL["GET /coordinate/poll"]
        L2_API --> NODE_SEL --> POLL
    end

    subgraph F3["③ background_task 内部"]
        QUEUE5["asyncio.Queue\nSQLite 持久化"]
        BG_W["bg-worker-N"]
        WS_PUSH["WebSocket 进度推送"]
        QUEUE5 --> BG_W --> WS_PUSH
    end

    subgraph F4["④ MCP Pull 内部"]
        REDIS5["Redis 队列\nl3_mcp_delegate_queue:{node_id}"]
        MCP_EXEC["L3 消费执行\nrun_mcp_delegate_pull_forever"]
        REDIS5 --> MCP_EXEC
    end

    FORM1 --- F1
    FORM2 --- F2
    FORM3 --- F3
    FORM4 --- F4
```

---

## 三、delegate — 同进程 SubAgent（主路径）

### 3.1 完整执行流程

```mermaid
flowchart TB
    INPUT_D["主 Agent 输出:\nAction: delegate\nAction Input: {sub_tasks: [...], mode: 'normal'}"]

    INPUT_D --> PARSE_D["解析 delegate payload"]
    PARSE_D --> DEPTH_CHK{"delegate_depth ≤ max_delegate_depth(2)?"}
    DEPTH_CHK -->|"超出"| REJECT["返回 JSON 拒绝 Observation\n禁止嵌套"]
    DEPTH_CHK -->|"通过"| MODE_CHK{"mode?"}

    MODE_CHK -->|"discuss"| DISC["run_discussion(DiscussionConfig)\n讨论模式分支"]
    MODE_CHK -->|"normal（默认）"| BUILD_TASKS["构建 sub_tasks 列表"]

    BUILD_TASKS --> SAN["_sanitize_inline_role\n动态角色安全沙箱"]
    SAN --> SEM_CTRL["_delegate_max_concurrent_cfg()\nSemaphore(max=4)"]

    subgraph PARALLEL_EXEC["asyncio.gather(return_exceptions=True)"]
        SA_RUN1["_run_sub_agent(coder, task1, ctx_data)\n→ run_agent(depth+1)"]
        SA_RUN2["_run_sub_agent(analyst, task2)\n→ run_agent(depth+1)"]
        SA_RUN3["_run_sub_agent(writer, task3)\n→ run_agent(depth+1)"]
    end

    SEM_CTRL --> PARALLEL_EXEC

    PARALLEL_EXEC --> COLLECT["收集结果\nSubAgentResult[]{ok, result, error}"]
    COLLECT --> REPORT["RunReport 首行\n[delegate RunReport] 完成: N/M 成功"]
    REPORT --> MERGE5["StructuredResultMerger.merge_parallel\nMarkdown 索引表 + 详块"]
    MERGE5 --> OBS5["Observation → 主 Agent messages"]
```

### 3.2 delegate 详细时序

```mermaid
sequenceDiagram
    participant MAIN6 as 主 run_agent
    participant DEL as delegate 分支
    participant SAN2 as _sanitize_inline_role
    participant SEM6 as Semaphore(max=4)
    participant SA_C as SubAgent[coder]
    participant SA_A as SubAgent[analyst]
    participant SA_W as SubAgent[writer]
    participant MERGER6 as StructuredResultMerger

    MAIN6->>DEL: 解析 "Action: delegate"
    DEL->>DEL: 校验 delegate_depth(1) ≤ max(2) ✓

    loop 每个 sub_task
        DEL->>SAN2: _sanitize_inline_role(task.role)
        SAN2->>SAN2: role_id 字母数字下划线检验
        SAN2->>SAN2: system_prefix 移除 prompt 注入词
        SAN2->>SAN2: allowed_tools 与父级工具集取交集
        SAN2->>SAN2: 强制剔除 delegate 工具（防递归）
        SAN2-->>DEL: 净化后的 role 配置
    end

    par 并行执行（受 Semaphore 控制）
        DEL->>SEM6: acquire slot 1
        SEM6->>SA_C: _spawn_sub_agent_async(coder, task1, context_data)
        SA_C->>SA_C: run_agent(depth=2, _system_prompt_override=coder_prompt)
        SA_C-->>MERGER6: SubAgentResult(ok=True, result="代码已写入...")

    and
        DEL->>SEM6: acquire slot 2
        SEM6->>SA_A: _spawn_sub_agent_async(analyst, task2)
        SA_A->>SA_A: run_agent(depth=2)
        SA_A-->>MERGER6: SubAgentResult(ok=True, result="分析摘要...")

    and
        DEL->>SEM6: acquire slot 3
        SEM6->>SA_W: _spawn_sub_agent_async(writer, task3)
        SA_W->>SA_W: run_agent(depth=2)
        SA_W-->>MERGER6: SubAgentResult(ok=False, error="TimeoutError")
    end

    MERGER6->>MERGER6: merge_parallel(results)
    Note over MERGER6: [delegate RunReport] 完成: 2/3 成功，1 失败
    MERGER6-->>MAIN6: Observation 字符串

    MAIN6->>MAIN6: Observation 追加到 messages → 继续 ReAct
```

---

## 四、SubAgent 角色体系

### 4.1 角色分类与工具集

```mermaid
flowchart TB
    POOL["SubAgent 角色池\nSUB_AGENT_PROMPTS + SUB_AGENT_ALLOWED_SKILLS"]

    POOL --> DEV_G
    POOL --> ANA_G
    POOL --> WRITE_G
    POOL --> PLAN_G

    subgraph DEV_G["开发类"]
        CODER_R["coder\n编写/修改代码\nfs_read · fs_write · apply_patch · shell_exec\n⭐ 使用 LLM_CODER_MODEL"]
        TESTER_R["tester\n测试用例编写\nfs_read · fs_write · shell_exec"]
        REVIEW_R["reviewer\n代码审查/安全扫描\nfs_read · shell_exec"]
    end

    subgraph ANA_G["分析类"]
        ANALYST_R["analyst\n数据分析/指标提炼\nfs_read · shell_exec · local_memory_search"]
        RESEARCH_R["researcher\n信息调研/竞品分析\nfs_read · shell_exec"]
        DP_R["data_processor\n数据清洗/格式转换\nfs_read · fs_write · shell_exec"]
    end

    subgraph WRITE_G["写作类"]
        WRITER_R["writer\n文档撰写/更新\nfs_read · fs_write"]
        SUM_R["summarizer\n内容摘要/要点提炼\nfs_read · local_memory_search"]
    end

    subgraph PLAN_G["规划评审类"]
        PLANNER_R["planner\n任务拆解/规划\nfs_read · local_memory_search"]
        CRITIC_R["critic\n方案质疑/风险识别\nfs_read · local_memory_search"]
        EXEC_R["executor\n直接执行/少复述\nfs_read · fs_write · shell_exec"]
        DE_R["domain_expert\n领域专家/业务上下文\nfs_read · shell_exec · local_memory_search"]
        DEF_R["default\n通用子任务\nfs_read · fs_write · shell_exec"]
    end
```

### 4.2 SubAgent 生命周期

```mermaid
stateDiagram-v2
    [*] --> Created: _spawn_sub_agent_async(role, task, sub_agent_id)

    Created --> Initialized: 检查 _sub_agent_registry
    note right of Initialized
        若 sub_agent_id 已存在
        则复用（携带历史 messages）
    end note

    Initialized --> Running: SubAgent.run_once(task, engine)

    Running --> ToolCalling: Action → run_tool/MCP
    ToolCalling --> Running: Observation → messages

    Running --> Completed: Final Answer
    Running --> Failed: 超时/异常

    Completed --> Archived: messages 写回 SubAgent.messages
    note right of Archived
        可通过 spawn_sub_agent(sub_agent_id=...)
        复用续聊（同一 ID）
    end note

    Failed --> [*]: SubAgentResult(ok=False, error=...)
    Archived --> [*]: SubAgentResult(ok=True, result=...)

    Archived --> Destroyed: terminate_sub_agent(sub_agent_id)
    Destroyed --> [*]
```

### 4.3 动态角色安全沙箱

当 `sub_tasks[i]["role"]` 为 dict（内联角色）时触发：

```mermaid
flowchart LR
    INLINE["inline role dict:\n{role_id: 'custom_X',\nsystem_prefix: '...',\nallowed_tools: [...]}"]

    INLINE --> CHK1["role_id 校验\n仅允许字母数字下划线"]
    CHK1 --> CHK2["system_prefix 净化\n移除 [SYSTEM]/[INJECTED] 等关键词\n标记为 [REDACTED]"]
    CHK2 --> CHK3["allowed_tools 取交集\n∩ 父级工具集（_parent_allowed_skills）"]
    CHK3 --> CHK4["强制剔除 delegate\n防止动态角色再嵌套"]
    CHK4 --> SAFE["净化后角色\n安全可用"]
```

---

## 五、三种编排模式

### 5.1 模式 A：FanOut 并行（批量同构）

**适用**：多份相同类型的子任务，互相独立，可并行处理。

```mermaid
flowchart LR
    INPUT_F["输入列表\n[item1, item2, ..., itemN]"]
    FAN2["fanout_parallel()\nl3_node/primitives/multi_agent/fanout.py"]

    subgraph PAR_F["asyncio.gather(max_concurrent=3)"]
        F1_W["SubAgent[analyst]\nitem1"]
        F2_W["SubAgent[analyst]\nitem2"]
        F3_W["SubAgent[analyst]\nitem3"]
        FN_W["SubAgent[analyst]\nitemN"]
    end

    INPUT_F --> FAN2 --> PAR_F

    subgraph RESULT_F["FanoutResult"]
        FR_S["status: completed/partial/failed"]
        FR_O["ok_count: N"]
        FR_F["failed_count: M"]
        FR_I["items: FanoutItemResult[]\n{index, ok, result, error, error_class}"]
        FR_D["degraded: bool (有失败但有成功)"]
        FR_E["elapsed_sec: float"]
    end

    PAR_F --> RESULT_F
```

**典型使用代码示意**：

```python
# 批量简历分析
result = await fanout_parallel(
    items=[
        {"role": "analyst", "task": f"分析候选人简历，评估是否匹配后端工程师 JD",
         "context_data": resume_text}
        for resume_text in resume_list
    ],
    engine=engine,
    max_concurrent=3,
    delegate_depth=1,
)
if result.status in ("completed", "partial"):
    for item in result.ok_items:
        print(f"简历 {item.index}: {item.result[:300]}")
```

### 5.2 模式 B：Pipeline 流水线（有依赖链）

**适用**：多阶段任务，前一阶段输出是后一阶段输入。

```mermaid
sequenceDiagram
    participant CALLER5 as 调用方
    participant P5 as planner
    participant C5 as coder
    participant T5 as tester
    participant R5 as reviewer

    Note over CALLER5,R5: run_pipeline(stages=[...], initial_context="实现 JWT 鉴权 API")

    CALLER5->>P5: run_once("拆解需求：JWT 鉴权", ctx=initial_context)
    P5->>P5: ReAct 执行（max_iterations=3）
    P5-->>C5: stage_output[:3000] → context_data

    CALLER5->>C5: run_once("编写 FastAPI 代码", ctx=planner_output)
    C5->>C5: ReAct 执行（写代码）

    alt on_failure="stop" 且 C5 失败
        C5-->>CALLER5: PipelineResult(status="failed", execution_brief="...")
    else 成功
        C5-->>T5: stage_output[:3000] → context_data
        CALLER5->>T5: run_once("编写 pytest 测试", ctx=coder_output)
        T5-->>R5: stage_output[:3000] → context_data
        CALLER5->>R5: run_once("审查代码安全性", ctx=tester_output)
        R5-->>CALLER5: PipelineResult(status="completed", final_output="...")
    end
```

**失败策略对比**：

| 策略 | 行为 | 适用场景 |
|------|------|---------|
| `on_failure="stop"`（默认） | 失败时生成 ExecutionBrief 并中止 | 强依赖链，后续阶段依赖前阶段 |
| `on_failure="continue"` | 跳过失败阶段，继续后续 | 非阻塞采集，部分缺失可接受 |

### 5.3 模式 C：Discussion 讨论/辩论（复杂决策）

**适用**：方案评审、架构选型、有争议的判断。

```mermaid
sequenceDiagram
    participant MAIN7 as 主 Agent
    participant DISC2 as run_discussion
    participant PLANNER5 as planner SubAgent
    participant CRITIC5 as critic SubAgent
    participant SUM5 as summarizer SubAgent

    Note over MAIN7,SUM5: 触发: Action:delegate, mode:discuss\npayload: {topic, context, roles, max_rounds=3}

    MAIN7->>DISC2: run_discussion(DiscussionConfig)

    loop Round 1..max_rounds（自适应收紧）
        par Round N 并行
            DISC2->>PLANNER5: run_once("提出/修订方案", ctx=prev_critique)
            PLANNER5-->>DISC2: 方案文本

            DISC2->>CRITIC5: run_once("质疑方案", ctx=current_proposal)
            CRITIC5-->>DISC2: 质疑点列表
        end

        DISC2->>DISC2: 检测终止条件
        alt critic 含终止词（无新质疑/无异议/方案已完善...）
            DISC2->>DISC2: 提前终止
        end
    end

    opt use_summarizer=True
        DISC2->>SUM5: run_once("输出最终共识", ctx=all_rounds)
        SUM5-->>DISC2: 共识文本
    end

    DISC2-->>MAIN7: DiscussionResult.format_summary()
    Note over MAIN7: "[Discussion] 3 轮 · planner+critic → 最终共识:\n..."
```

**自适应轮次**（`JACHIN_DISCUSS_ADAPTIVE_ROUNDS=1`）：

```mermaid
flowchart LR
    TOPIC["议题 topic_len\n背景 ctx_len"]
    TOPIC --> CHK_S{"topic_len < 36\nAND ctx_len < 180?"}
    CHK_S -->|"是（简单议题）"| SHORT["max_rounds = min(base, 2)\n最多 2 轮"]
    CHK_S -->|"否"| CHK_L{"topic_len > 220\nOR ctx_len > 1600?"}
    CHK_L -->|"是（复杂议题）"| FULL["max_rounds = base\n完整轮次"]
    CHK_L -->|"否（中等）"| MID["max_rounds = min(base, max(2, base-1))"]
```

---

## 六、coordinate — 跨节点 L2 调度

### 6.1 coordinate 完整时序

```mermaid
sequenceDiagram
    participant L3_MAIN as L3 主节点
    participant COORD_BRANCH as _coordinate_task
    participant L2_API as L2 coordinate API
    participant L3_B5 as L3 节点 B（hr_analyzer）
    participant L3_C5 as L3 节点 C（bi_report）

    L3_MAIN->>COORD_BRANCH: 解析 "Action: coordinate"
    Note over COORD_BRANCH: payload: {intent, sub_tasks:[{intent, skill_required, input_data}]}

    COORD_BRANCH->>L2_API: POST /api/v2/coordinate/task(payload)
    L2_API->>L2_API: 按 skill_required 筛选在线节点
    Note over L2_API: allow_l2_delegate=False 防递归委派

    par L2 并行派发
        L2_API->>L3_B5: 子任务 1（skill: hr_analyzer4）
        L3_B5->>L3_B5: run_agent(intent) 或 run_tool(native_tool)
        L3_B5-->>L2_API: 结果 1

    and
        L2_API->>L3_C5: 子任务 2（skill: bi_report）
        L3_C5->>L3_C5: run_agent(intent)
        L3_C5-->>L2_API: 结果 2
    end

    loop 轮询（每 2s，超时保护）
        COORD_BRANCH->>L2_API: GET /api/v2/coordinate/poll?task_id=xxx
        L2_API-->>COORD_BRANCH: {status: "running", partial_results: [...]}
    end

    L2_API-->>COORD_BRANCH: {status: "completed", results: [...]}
    COORD_BRANCH-->>L3_MAIN: 聚合结果 Observation
    L3_MAIN->>L3_MAIN: 继续 ReAct
```

### 6.2 coordinate 与 delegate 对比

| 维度 | delegate | coordinate |
|------|---------|-----------|
| 执行位置 | 同一 L3 进程内 | 不同 L3 节点 |
| 通信方式 | 函数调用（asyncio） | HTTP REST API |
| 等待方式 | asyncio.gather | 轮询 /poll |
| 节点选择 | 按 role → 工具白名单 | 按 skill_required → 节点匹配 |
| 适用规模 | 单节点多角色 | 多节点分布式 |
| 后台通道 | ✗ 禁止 | ✗ 禁止 |

---

## 七、后台异步 Agent Task

### 7.1 完整生命周期

```mermaid
flowchart TB
    subgraph SUBMIT["提交阶段（前台同步）"]
        TRIGGER["主 Agent 输出:\nAction: core:submit_background_task\nAction Input: {intent, priority, tags, max_iterations}"]
        PARSE_B["解析 priority(0-2) + tags[] + parent_run_id"]
        ENQUEUE["asyncio.Queue.put(BackgroundJob)"]
        PERSIST["SQLite 持久化 job\n防进程重启丢失"]
        RETURN_ID["立即返回 task_id\nFinal Answer 含 task_id"]
    end

    subgraph EXEC["执行阶段（后台异步）"]
        WORKER2["bg-worker-N\nasyncio Task（可多 worker 并发）"]
        DEQUEUE["Queue.get() → BackgroundJob"]
        RUN_BG["run_agent(intent,\nchannel='background_task',\nmax_iterations=job.max_iterations)"]
        PROGRESS["broadcast_background_task_event\n('progress', step_info)"]
        DONE["broadcast_background_task_event\n('completed'/'failed', result)"]
    end

    TRIGGER --> PARSE_B --> ENQUEUE & PERSIST --> RETURN_ID
    WORKER2 --> DEQUEUE --> RUN_BG
    RUN_BG -->|"每步"| PROGRESS
    RUN_BG -->|"完成"| DONE

    subgraph WS_PUSH["WebSocket 推送"]
        WS_CLI["桌面客户端\n监听 background_task 事件"]
    end

    PROGRESS & DONE --> WS_PUSH
```

### 7.2 BackgroundJob 数据结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | str | UUID，唯一标识 |
| `intent` | str | 要执行的任务意图文本 |
| `max_iterations` | int | ReAct 最大轮次（默认 24） |
| `priority` | int | 0=普通，1=较高，2=紧急 |
| `tags` | list[str] | 如 `["hr", "recruitment"]`，用于监控筛选 |
| `parent_run_id` | str | 父 run_agent 的 run_id，可观测性归因 |
| `status` | str | pending/running/completed/failed |
| `created_at` | float | Unix 时间戳 |
| `result` | str | 完成后的输出 |

---

## 八、StructuredResultMerger（结果合并）

### 8.1 并行结果合并格式

```mermaid
flowchart TB
    RESULTS["SubAgentResult[]\n[{ok:True,result:'...'}, {ok:False,error:'...'}, ...]"]

    RESULTS --> REPORT2["RunReport 首行\n[delegate RunReport] 完成: N/M 成功，K 失败"]

    RESULTS --> INDEX["Markdown 索引表\n（with_index_table=True，默认开）\n| # | 角色 | 状态 | 摘要 |\n|---|------|------|------|\n| 1 | coder | ✅ | ... |"]

    RESULTS --> DETAILS["逐子任务详块\n[子任务 1·coder]\n（内容摘要...）\n\n[子任务 2·analyst]\n（分析结果...）\n\n[子任务 3·writer 失败: TimeoutError]"]

    REPORT2 & INDEX & DETAILS --> OBS6["最终 Observation\n注入主 Agent messages"]
```

### 8.2 讨论模式合并格式

```
[Discussion] 3 轮 · planner + critic

Round 1:
  [planner] 提出初始方案...
  [critic] 质疑点: 1. 性能 2. 安全

Round 2:
  [planner] 修订方案（针对质疑）...
  [critic] 质疑点: 1. 边界情况

Round 3:
  [planner] 最终方案...
  [critic] 无新质疑 ← 终止条件

[summarizer] 最终共识: ...
```

---

## 九、并发控制与限速

### 9.1 Semaphore 并发控制

```mermaid
flowchart LR
    CONFIG["nexus_config.json\nagent.delegate_max_concurrent = 4"]

    CONFIG --> SEM_CFG["_delegate_max_concurrent_cfg()\n读取配置，默认 4"]

    SEM_CFG --> SEM_OBJ["asyncio.Semaphore(4)"]

    subgraph SLOTS["Semaphore 槽位（同时最多 4 个）"]
        S1["槽位 1: SubAgent[coder]"]
        S2["槽位 2: SubAgent[analyst]"]
        S3["槽位 3: SubAgent[writer]"]
        S4["槽位 4: SubAgent[reviewer]"]
    end

    SEM_OBJ --> SLOTS

    WAITING["等待中: SubAgent[tester]\n等待槽位释放"]
    S1 -.->|"完成释放"| WAITING
```

### 9.2 关键配置参数

| 参数 | 位置 | 默认 | 说明 |
|------|------|------|------|
| `agent.max_delegate_depth` | `nexus_config.json` | `2` | 最大嵌套深度，防无限递归 |
| `agent.delegate_max_concurrent` | `nexus_config.json` | `4` | 单次 delegate 最大并发 SubAgent 数 |
| `agent.sub_agent_max_total_tokens` | `nexus_config.json` | `120000` | 子 Agent 单次 Token 上限 |
| `multi_agent.max_discussion_rounds` | `nexus_config.json` | `3` | 讨论最大轮次（1..12） |
| `multi_agent.discussion_item_max_iterations` | `nexus_config.json` | `3` | 讨论每轮 SubAgent 最大迭代（1..24） |
| `JACHIN_DISCUSS_MAX_ROUNDS` | 环境变量 | — | 覆盖 max_discussion_rounds |
| `JACHIN_DISCUSS_ITEM_MAX_ITER` | 环境变量 | — | 覆盖 discussion_item_max_iterations |
| `JACHIN_DISCUSS_ADAPTIVE_ROUNDS` | 环境变量 | `0` | 开启自适应轮次收紧 |

---

## 十、禁止事项与安全边界

```mermaid
flowchart TB
    subgraph PROHIBIT["禁止事项（硬约束）"]
        P1["✗ 子 Agent (delegate_depth > 0) 再次 delegate\n→ 深度校验阻止，返回 JSON 拒绝"]
        P2["✗ 后台任务 (channel=background_task) 使用 coordinate\n→ system prompt 关闭，工具表移除"]
        P3["✗ 后台任务使用 delegate 或 submit_background_task\n→ allow_delegate=False"]
        P4["✗ MCP 工具称为 Agent\n→ MCP 是 Tool，SubAgent 是 Agent Task"]
        P5["✗ 动态角色访问超出父级工具集的工具\n→ allowed_tools 强制取交集"]
        P6["✗ L2 递归委派 L3（MCP Pull 路径）\n→ allow_l2_delegate=False"]
    end

    subgraph DEPTH["深度控制示意"]
        D_0["深度 0: 主 Agent\n可以 delegate ✅"]
        D_1["深度 1: SubAgent\n可以 delegate ✅"]
        D_2["深度 2: 孙 Agent\n禁止 delegate ✗"]
    end
```

**可观测日志标签**（发生时打印，可检索）：

| 日志标签 | 含义 |
|---------|------|
| `[delegate RunReport]` | delegate 子任务完成汇总 |
| `[FanOut RunReport]` | FanOut 并行完成汇总 |
| `[Pipeline]` | Pipeline 阶段执行日志 |
| `[Discussion]` | 讨论模式轮次摘要 |
| `[SubAgent]` | 子 Agent 执行详情 |
| `[L3 Agent] delegate RunReport` | delegate 深度+状态 JSON |

---

**上一篇**: [02_MAIN_AGENT_DESIGN.md](./02_MAIN_AGENT_DESIGN.md)  
**下一篇**: [04_MEMORY_ARCHITECTURE.md](./04_MEMORY_ARCHITECTURE.md) — 记忆架构详解
