# Jachin 并发调度与韧性保障详解

> **分册**: 06 / 07 · [返回索引](./README.md)  
> **代码锚点**: `l3_node/session_instruction_queue.py`、`l3_node/background_task_service.py`、`l3_node/execution_resilience.py`、`l3_node/global_task_registry.py`  
> **专题 SSOT**: [`JACHIN_EXECUTION_RESILIENCE_CONTRACT.md`](../JACHIN_EXECUTION_RESILIENCE_CONTRACT.md)、[`前台闲聊与后台重负荷任务的物理隔离与背压熔断.md`](../前台闲聊与后台重负荷任务的物理隔离与背压熔断.md)

---

## 目录

1. [设计哲学：四原则](#一设计哲学四原则)
2. [前台/后台物理隔离](#二前台后台物理隔离)
3. [并发调度层（SessionInstructionQueue）](#三并发调度层sessioninstructionqueue)
4. [飞书 IM 第二条指令处理策略](#四飞书-im-第二条指令处理策略)
5. [执行韧性契约（RunReport + ExecutionBrief）](#五执行韧性契约runreport--executionbrief)
6. [错误分类与重试策略](#六错误分类与重试策略)
7. [策略链（StrategyChain）](#七策略链strategychain)
8. [背压与熔断](#八背压与熔断)
9. [asyncio 并发控制详解](#九asyncio-并发控制详解)
10. [韧性检查清单](#十韧性检查清单)

---

## 一、设计哲学：四原则

```mermaid
flowchart TB
    subgraph FOUR["韧性四原则（JACHIN_EXECUTION_RESILIENCE_CONTRACT.md）"]
        P1["① 韧性\n单点失败（LLM/Wasm/网络/单文件）\n不默认拖死整条链路\n已落盘进度可续跑"]

        P2["② 策略链\n对同一步骤有限次重试后\n必须换策略（降级批量→逐份/跳过坏项/换模型等）\n禁止同参无限重试"]

        P3["③ 有界退出\n重试/策略/LLM 轮次或软预算触顶后\n产出 ExecutionBrief\n显式停止自动扩张\n避免空转烧 Token"]

        P4["④ 部分成功\n批量中子项失败须记录\n(stage + error_class + message)\n其余继续\n文案区分「全部成功」vs「部分成功 + 异常说明」"]
    end
```

---

## 二、前台/后台物理隔离

### 2.1 双轨架构

```mermaid
flowchart TB
    subgraph FOREGROUND["前台轨道（Front Track）"]
        USER_IN["用户指令\n(WebSocket/飞书IM/HTTP)"]
        FG_LOCK["chat_lock（asyncio.Lock）\n同一 session 串行保序"]
        FG_RUN["run_agent\nchannel=websocket/lark_im"]
        FG_TIMEOUT["asyncio.wait_for\n前台超时预算\nJACHIN_FOREGROUND_TIMEOUT_SEC(默认120s)"]
        FG_EXEMPT["豁免策略\nforeground_tool_policy\nMCP long_running=true 豁免超时"]
        FG_STREAM["流式返回用户\non_chunk / on_step 回调"]
    end

    subgraph BACKGROUND["后台轨道（Back Track）"]
        BG_QUEUE["asyncio.Queue\n+ SQLite 持久化\n防进程重启丢失"]
        BG_WORKER["bg-worker-N（可多 worker）\nasyncio Task 并行消费"]
        BG_RUN2["run_agent\nchannel=background_task\n无超时上限"]
        BG_RESTRICT["限制项:\n✗ delegate 禁用\n✗ coordinate 禁用\n✗ submit_background_task 禁用\n✗ context_prefetch 跳过\n✗ DAG 重规划关闭"]
        BG_PUSH["WebSocket 异步推送\n进度 / 完成 / 失败"]
    end

    USER_IN --> FG_LOCK --> FG_RUN
    FG_RUN --> FG_TIMEOUT
    FG_TIMEOUT -.->|"超时降级"| BG_QUEUE
    FG_RUN -->|"submit_background_task"| BG_QUEUE
    BG_QUEUE --> BG_WORKER --> BG_RUN2
    BG_RUN2 --> BG_PUSH

    style FOREGROUND fill:#e3f2fd,stroke:#1565c0
    style BACKGROUND fill:#e8f5e9,stroke:#2e7d32
```

### 2.2 前台超时降级流程

```mermaid
sequenceDiagram
    participant U14 as 用户
    participant FG14 as 前台 run_agent
    participant TIMER as asyncio.wait_for
    participant BG14 as 后台队列

    U14->>FG14: 发起重负荷请求（"分析 100 份简历"）
    FG14->>TIMER: wait_for(run_agent(...), timeout=120)

    Note over FG14: 处理中...（超过 120s）

    TIMER-->>FG14: asyncio.TimeoutError
    FG14->>BG14: submit_background_task(intent, priority=1)
    BG14-->>FG14: task_id = "bg_20260528_xxx"

    FG14-->>U14: "任务较复杂，已转为后台处理\n任务ID: bg_20260528_xxx\n完成后会推送给您"

    Note over BG14: 后台 worker 继续执行

    BG14->>U14: WebSocket push: "progress: 已处理 30/100"
    BG14->>U14: WebSocket push: "completed: 分析完成，见报告"
```

---

## 三、并发调度层（SessionInstructionQueue）

### 3.1 SIQ 双模式

```mermaid
flowchart TB
    subgraph SIQ_ARCH["SessionInstructionQueue（SIQ）"]
        subgraph SERIAL_M["SERIAL 模式（默认）\nJACHIN_SIQ_MODE=SERIAL"]
            SER_Q["有序队列\naioqueue.Queue(maxsize=JACHIN_SIQ_MAX_SIZE)"]
            SER_EXEC["串行消费\n一次只有一个 instruction 运行"]
            SER_WAIT["后续 instruction 等待前序完成"]
        end

        subgraph PARALLEL_M["PARALLEL 模式\nJACHIN_SIQ_MODE=PARALLEL"]
            PAR_SEMAPHORE["asyncio.Semaphore\n(JACHIN_SIQ_PARALLEL_MAX_CONCURRENCY)"]
            PAR_EXEC["真并行执行\n多 instruction 同时运行"]
            PAR_ISOLATE["结果隔离\n各自独立 chat_lock 实例"]
        end
    end

    subgraph SIQ_FLOW["消息来源"]
        WS_MSG["WebSocket 新消息"]
        IM_MSG["飞书 IM 新消息"]
        HTTP_MSG["HTTP 同会话新请求"]
    end

    SIQ_FLOW --> SIQ_ARCH
```

### 3.2 SIQ 提交时序

```mermaid
sequenceDiagram
    participant DISP14 as Dispatcher（IM/WS/HTTP）
    participant SIQ14 as SessionInstructionQueue
    participant WORKER14 as SIQ Worker
    participant AGENT14 as run_agent

    DISP14->>SIQ14: submit_instruction(InstructionItem{intent, session_id, priority})
    SIQ14->>SIQ14: 队列大小检查（maxsize 背压）

    alt 队列已满（背压触发）
        SIQ14-->>DISP14: QueueFullError
        DISP14-->>DISP14: 触发熔断（reject_with_backpressure_msg）
    else 正常入队
        SIQ14-->>DISP14: enqueue_ok
    end

    SIQ14->>WORKER14: dequeue（按优先级）
    WORKER14->>AGENT14: run_agent(instruction.intent)

    alt SERIAL 模式
        Note over SIQ14: 等待 AGENT14 完成后才 dequeue 下一条
    else PARALLEL 模式
        Note over SIQ14: 立即可 dequeue 下一条（受 Semaphore 限制）
    end

    AGENT14-->>WORKER14: Final Answer
    WORKER14->>SIQ14: mark_done(instruction_id)
```

---

## 四、飞书 IM 第二条指令处理策略

### 4.1 四种处理策略

```mermaid
flowchart TD
    BUSY["Agent 正在运行 (持有 chat_lock)\n用户发来第二条指令"]

    BUSY --> CLASSIFY["classify_busy_followup(second_input, current_context)\nLLM 轻量分类（或规则匹配）"]

    CLASSIFY -->|"打断意图\n'停/算了/重来'等"| INTERRUPT["interrupt 策略\n① request_cancel_run(current_run_id)\n② 等待当前 run 停止（取消令牌）\n③ 启动新 run_agent(second_input)"]

    CLASSIFY -->|"完全独立的新任务\n'帮我写个不相关的XXX'"| PARALLEL["parallel 策略\n① submit_instruction(PARALLEL)\n② 创建独立 asyncio Task\n③ 告知用户'并行处理中'"]

    CLASSIFY -->|"对当前任务的补充\n'哦对了另外还要...'等"| SUPPLEMENT["supplement 策略\n① session_hot_user_inject.record_pending(text)\n② 下一轮 LLM 调用前 drain 并入\n③ 用户感知：'已补充到当前任务'"]

    CLASSIFY -->|"需要当前完成后才处理\n明确的顺序依赖"| QUEUE14["queue 策略\n① submit_instruction(SERIAL)\n② 告知用户'已加入队列'\n③ 当前任务完成后自动执行"]
```

### 4.2 interrupt 策略时序

```mermaid
sequenceDiagram
    participant U15 as 用户（飞书）
    participant DISP15 as Dispatcher
    participant CANCEL15 as CancelToken
    participant AGENT15 as run_agent（运行中）
    participant NEW15 as 新 run_agent

    U15->>DISP15: "停！不要分析了，直接发 JD"
    DISP15->>DISP15: classify → interrupt

    DISP15->>CANCEL15: set_cancel_token(current_run_id)
    CANCEL15->>AGENT15: _cancel_requested = True

    Note over AGENT15: 下一轮 LLM 调用前检测到取消信号
    AGENT15->>AGENT15: break ReAct 循环
    AGENT15-->>DISP15: CancelledResult

    DISP15->>NEW15: run_agent("直接发 JD", channel="lark_im")
    NEW15-->>U15: 执行新任务的 Final Answer
```

### 4.3 supplement 策略（热注入）

```mermaid
sequenceDiagram
    participant U16 as 用户（飞书）
    participant DISP16 as Dispatcher
    participant HOT16 as session_hot_user_inject
    participant AGENT16 as run_agent（运行中，第 3 轮迭代）

    U16->>DISP16: "哦对了，分析时重点看 Python 技能"
    DISP16->>DISP16: classify → supplement
    DISP16->>HOT16: record_pending("重点看 Python 技能")
    DISP16-->>U16: "好的，已补充到当前任务中"

    Note over AGENT16: 第 4 轮迭代开始前

    AGENT16->>HOT16: drain_pending_session_user_texts()
    HOT16-->>AGENT16: ["重点看 Python 技能"]
    AGENT16->>AGENT16: 并入 full_messages（role=user）

    Note over AGENT16: LLM 在第 4 轮看到补充指令，调整分析侧重
```

---

## 五、执行韧性契约（RunReport + ExecutionBrief）

### 5.1 RunReport 数据结构

```mermaid
flowchart LR
    subgraph RR_STRUCT["RunReport 结构"]
        RR_STATUS["status: str\n'completed' | 'partial' | 'failed'"]
        RR_OK["ok_count: int\n成功子任务数"]
        RR_TOTAL["total_count: int\n总子任务数"]
        RR_FAILED["failed_items: list[FailedItem]\n[\n  {index, stage, error_class, message},\n  ...\n]"]
        RR_DEGRADED["degraded: bool\n有失败但整体仍可交付"]
        RR_FALLBACK["fallback_used: bool\n是否使用了降级策略"]
        RR_ELAPSED["elapsed_sec: float\n执行耗时"]
    end
```

**输出格式约定**：

```
[delegate RunReport] 完成: 8/10 成功，2 失败
| 序号 | 角色     | 状态  | 摘要               |
|------|---------|-------|--------------------|
| 1    | analyst | ✅    | Q1 报告分析完成     |
| 2    | analyst | ✅    | Q2 报告分析完成     |
| 3    | analyst | ❌    | TimeoutError       |
...

[子任务 1·analyst]
（详细分析内容...）

[子任务 3 失败: TimeoutError]
网络连接超时，建议重试
```

### 5.2 批量任务韧性保障

```mermaid
flowchart LR
    subgraph BATCH["批量任务执行"]
        ITEM1["item1 处理"]
        ITEM2["item2 处理"]
        ITEM3["item3（坏数据）"]
        ITEM4["item4 处理"]
    end

    ITEM3 -->|"per_item 失败"| FAIL_ITEM["记录失败\nfailed_items.append({index:3, error_class:'per_item', message:'PDF 损坏'})\n不影响其他 item"]

    ITEM1 & ITEM2 & ITEM4 -->|"成功"| OK_ITEMS["ok_items"]

    FAIL_ITEM & OK_ITEMS --> RUNREPORT["RunReport\nstatus='partial'\nok_count=3, total=4\nfailed_items=[{index:3,...}]\ndegraded=True"]

    RUNREPORT --> USER_MSG["用户文案:\n'已完成 3/4 项分析（部分成功）\n第 3 项（report3.pdf）损坏，已跳过\n详见 failed_items.json'"]
```

**严禁行为**（合规代码不得出现）：

```python
# ❌ 禁止：单项失败静默吞掉
try:
    process(item)
except Exception:
    pass  # 上层误判成功！

# ❌ 禁止：单项失败导致全批失败
try:
    for item in items:
        process(item)
except Exception as e:
    raise  # 截断整批，丢失已处理进度！

# ✅ 正确：记录失败，继续其余
failed_items = []
for item in items:
    try:
        process(item)
        ok_count += 1
    except Exception as e:
        failed_items.append(FailedItem(index=i, error_class=classify(e), message=str(e)))
```

---

## 六、错误分类与重试策略

### 6.1 五类错误分类

```mermaid
flowchart TB
    ERROR["执行异常"]

    ERROR --> CLF{"错误归类"}

    CLF --> TRANS["transient（临时）\n网络抖动/API 429/临时超时\n策略: 指数退避重试（上限 3次）"]
    CLF --> RESOURCE["resource（资源）\nWasm OOB/内存不足/磁盘满\n策略: 降级批量→逐份 → 出 Brief"]
    CLF --> PER_ITEM["per_item（单项）\n单 PDF 损坏/单文件编码错误\n策略: 记录 failed_items，跳过继续"]
    CLF --> CONFIG2["config（配置）\n工具不存在/API Key 无效\n策略: 出 Brief，等人工修复"]
    CLF --> PERM["permanent（不可恢复）\nLogicError/数据结构根本不匹配\n策略: 立即出 Brief，不重试"]

    TRANS --> RETRY_LIMIT["重试上限\nJACHIN_MAX_RETRY_COUNT(3)\n超限 → 换策略 [StrategyShift]"]
    RESOURCE --> DEGRADE["降级策略\nbatch(n) → batch(1) → skip"]
    PER_ITEM --> SKIP_LOG["跳过 + 记录\nfailed_items.append(...)"]
    CONFIG2 & PERM --> BRIEF_EXIT["ExecutionBrief\n显式停止"]
```

### 6.2 分类判断逻辑

```python
def classify_error(exc: Exception) -> ErrorClass:
    if isinstance(exc, (httpx.TimeoutException, aiohttp.ServerTimeoutError)):
        return "transient"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return "transient"  # Rate limit，可重试
    if isinstance(exc, (MemoryError, WasmOOBError)):
        return "resource"
    if isinstance(exc, (FileNotFoundError, CorruptedFileError)):
        return "per_item"
    if isinstance(exc, (AuthError, ToolNotFoundError)):
        return "config"
    # 兜底 → permanent（不确定的归为不可恢复，保守处理）
    return "permanent"
```

---

## 七、策略链（StrategyChain）

### 7.1 策略链流程

```mermaid
flowchart TB
    ATTEMPT["尝试执行（策略 A）"]

    ATTEMPT --> FAIL_A{失败?}

    FAIL_A -->|"transient 且 次数 < 限"| RETRY["重试（指数退避）\n第1次: 1s\n第2次: 2s\n第3次: 4s"]
    RETRY --> ATTEMPT

    FAIL_A -->|"超出重试上限"| SHIFT1["[StrategyShift] 策略 A → B\n策略 B: 降级批量大小\nbatch(n=10) → batch(n=1)"]
    SHIFT1 --> ATTEMPT_B["尝试执行（策略 B：逐份）"]

    ATTEMPT_B --> FAIL_B{失败?}
    FAIL_B -->|"per_item 单项错误"| SKIP["记录 failed_items\n跳过，继续下一项"]
    FAIL_B -->|"resource 资源耗尽"| SHIFT2["[StrategyShift] 策略 B → C\n策略 C: 换模型（降级 LLM）"]
    SHIFT2 --> ATTEMPT_C["尝试执行（策略 C：小模型）"]

    ATTEMPT_C --> FAIL_C{失败?}
    FAIL_C -->|"仍失败"| BUDGET{"预算耗尽?"}
    BUDGET -->|"是"| BRIEF_FINAL["[ExecutionBrief]\n列出所有尝试方法 + 失败类别 + 建议\n显式停止"]
    BUDGET -->|"否"| SHIFT3["[StrategyShift] 策略 C → D\n...继续尝试"]

    SKIP & ATTEMPT_C -->|"成功"| SUCCESS["部分/全部成功"]
    SUCCESS --> RUNREPORT2["RunReport\nstatus=partial/completed"]
```

### 7.2 策略链可观测日志

| 日志标签 | 触发时机 | 格式示例 |
|---------|---------|---------|
| `[StrategyShift]` | 策略切换 | `[StrategyShift] batch(10)→batch(1): 3次重试后切换` |
| `[ExecutionBrief]` | 有界退出 | `[ExecutionBrief] 4策略均失败，建议人工介入` |
| `[RetryAttempt]` | 重试 | `[RetryAttempt] 2/3: transient error, 2s 后重试` |
| `[SkipItem]` | per_item 跳过 | `[SkipItem] idx=3: PDF 损坏，已跳过` |

---

## 八、背压与熔断

### 8.1 背压机制

```mermaid
flowchart TB
    subgraph BACKPRESSURE["背压（Backpressure）"]
        SIQ_FULL["SIQ 队列满\nmaxsize=JACHIN_SIQ_MAX_SIZE(默认50)"]
        BG_FULL["后台队列满\nJACHIN_BG_QUEUE_MAX_SIZE(默认200)"]

        SIQ_FULL -->|"新指令到来"| REJECT_SIQ["拒绝入队\n返回用户: '系统繁忙，请稍后'\n不丢弃已排队的"]
        BG_FULL -->|"新后台任务"| REJECT_BG["拒绝创建\n返回用户: '后台任务已满，等待执行'\n可设置 priority 插队"]
    end

    subgraph CIRCUIT["熔断（Circuit Breaker）"]
        LLM_FAIL["LLM API 连续失败\n≥ JACHIN_CB_LLM_FAIL_THRESHOLD(5)"]
        MCP_FAIL["MCP 进程异常\n≥ JACHIN_CB_MCP_FAIL_THRESHOLD(3)"]

        LLM_FAIL -->|"开路"| CB_OPEN["熔断器 OPEN\n后续请求直接返回 Brief\n不再调 LLM（防止雪崩）"]
        CB_OPEN -->|"冷却期后"| CB_HALF["熔断器 HALF_OPEN\n试探一次 LLM 调用"]
        CB_HALF -->|"成功"| CB_CLOSE["熔断器 CLOSED\n恢复正常"]
        CB_HALF -->|"失败"| CB_OPEN

        MCP_FAIL -->|"进程崩溃"| MCP_RESTART["MCP 进程重启\nasyncio.create_subprocess_exec"]
    end
```

### 8.2 前台预算告警

```mermaid
sequenceDiagram
    participant ENV9 as environment_report
    participant DISK9 as 磁盘监测
    participant TOKEN9 as Token 计数
    participant LARK9 as 飞书告警

    ENV9->>DISK9: check_disk_free_gb()
    DISK9-->>ENV9: 1.2 GB

    ENV9->>TOKEN9: get_total_tokens_today()
    TOKEN9-->>ENV9: 890,000 tokens

    alt disk_free_gb < JACHIN_DISK_ALERT_GB (默认5GB)
        ENV9->>LARK9: send_alert("磁盘剩余 1.2GB，请清理")
        Note over ENV9: system_prompt 中注入磁盘告警提示
    end

    alt tokens_today > JACHIN_DAILY_TOKEN_BUDGET * 0.9
        ENV9->>LARK9: send_alert("今日 Token 已用 90%")
        Note over ENV9: 触发模型降级（日常档替代复杂档）
    end
```

---

## 九、asyncio 并发控制详解

### 9.1 关键锁与同步原语

| 原语 | 位置 | 作用 |
|------|------|------|
| `asyncio.Lock` → `chat_lock` | 前台 run_agent | 同一 session 串行保序 |
| `asyncio.Semaphore(max=4)` → delegate | `_delegate_max_concurrent_cfg` | 限制并行 SubAgent 数 |
| `asyncio.Semaphore` → SIQ PARALLEL | `SessionInstructionQueue` | 限制并行指令数 |
| `asyncio.Queue` → BG Queue | `BackgroundTaskService` | 后台任务缓冲 |
| `asyncio.Event` → cancel_token | 各 run_agent | 跨协程取消信号 |
| `asyncio.gather(return_exceptions=True)` | delegate/FanOut | 收集并行结果（不因单个异常中断） |

### 9.2 delegate 并发控制详图

```mermaid
flowchart TB
    subgraph CONCURRENCY["asyncio 并发控制层"]
        SEM_OBJ2["asyncio.Semaphore(4)\n最多 4 个 SubAgent 同时运行"]

        subgraph RUNNING["运行中 (max=4)"]
            SA_R1["SubAgent[coder]"]
            SA_R2["SubAgent[analyst]"]
            SA_R3["SubAgent[writer]"]
            SA_R4["SubAgent[reviewer]"]
        end

        subgraph WAITING2["等待中 (排队)"]
            SA_W1["SubAgent[tester]"]
            SA_W2["SubAgent[planner]"]
        end
    end

    SEM_OBJ2 --> RUNNING
    RUNNING -.->|"SA_R1 完成释放槽位"| SA_W1
    SA_W1 -->|"获得槽位"| RUNNING

    NOTE9["asyncio.gather(return_exceptions=True)\n不因单个 SubAgent 异常中断整体\n异常被收集到 SubAgentResult.error"]
```

### 9.3 取消信号传播链

```mermaid
sequenceDiagram
    participant DISPATCHER9 as Dispatcher
    participant CTK9 as CancelTokenManager
    participant REACT_LOOP as _run_react_core
    participant TOOL_EXEC9 as 工具执行

    DISPATCHER9->>CTK9: request_cancel_run(run_id)
    CTK9->>CTK9: cancel_tokens[run_id].set()

    Note over REACT_LOOP: 每轮迭代开始前检查

    REACT_LOOP->>CTK9: is_cancelled(run_id)
    CTK9-->>REACT_LOOP: True

    REACT_LOOP->>REACT_LOOP: break（提前退出迭代）
    REACT_LOOP-->>REACT_LOOP: 返回 CancelledResult

    Note over TOOL_EXEC9: 长时间工具（如 shell_exec）
    TOOL_EXEC9->>CTK9: 周期性 check_cancelled
    alt 取消信号已设置
        TOOL_EXEC9->>TOOL_EXEC9: 强制终止子进程
        TOOL_EXEC9-->>REACT_LOOP: CancelledError
    end
```

---

## 十、韧性检查清单

在修改 Skill、工具、调度器等代码时，过一遍以下检查：

```mermaid
flowchart TB
    subgraph CHECKLIST["韧性自检清单（开发者 PR 检查）"]
        Q1["✅ 批量任务是否仅因单项失败就返回「全盘失败」?\n→ 必须: failed_items 记录 + 其余继续"]
        Q2["✅ 是否有静默 except:pass?\n→ 禁止: 上层会误判成功"]
        Q3["✅ 重试是否有上限?\n→ 必须: max_retry_count，超限换策略"]
        Q4["✅ 超出所有策略后是否出 ExecutionBrief?\n→ 必须: 不能空转烧 Token"]
        Q5["✅ 用户文案是否区分「全部成功」vs「部分成功」?\n→ 必须: 诚实文案"]
        Q6["✅ 有副作用的操作是否幂等或可检测重复?\n→ 推荐: INSERT OR IGNORE / check-before-write"]
        Q7["✅ 策略切换是否打 [StrategyShift] 日志?\n→ 必须: 可检索"]
        Q8["✅ ExecutionBrief 是否打 [ExecutionBrief] 日志?\n→ 必须: 可检索"]
        Q9["✅ 后台任务是否禁用了 delegate/coordinate?\n→ 验证: channel=background_task 时工具表已裁剪"]
    end
```

**配置快查**：

| 参数 | 作用 | 默认 |
|------|------|------|
| `JACHIN_MAX_RETRY_COUNT` | 全局重试上限 | `3` |
| `JACHIN_FOREGROUND_TIMEOUT_SEC` | 前台超时（秒） | `120` |
| `JACHIN_SIQ_MAX_SIZE` | SIQ 队列容量 | `50` |
| `JACHIN_SIQ_MODE` | SIQ 模式 | `SERIAL` |
| `JACHIN_SIQ_PARALLEL_MAX_CONCURRENCY` | 并行模式并发上限 | `3` |
| `JACHIN_BG_QUEUE_MAX_SIZE` | 后台队列容量 | `200` |
| `JACHIN_CB_LLM_FAIL_THRESHOLD` | LLM 熔断阈值 | `5` |
| `JACHIN_DISK_ALERT_GB` | 磁盘告警阈值（GB） | `5` |
| `JACHIN_NEXUS_TIME_DECAY_WEIGHT` | 记忆时间衰减权重 | `0.2` |

---

**上一篇**: [05_AGI_CORE_CAPABILITIES.md](./05_AGI_CORE_CAPABILITIES.md)  
**下一篇**: [07_OBSERVABILITY_AUTONOMY.md](./07_OBSERVABILITY_AUTONOMY.md) — 可观测性与自治能力
