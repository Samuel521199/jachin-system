# Jachin 可观测性与自治能力详解

> **分册**: 07 / 07 · [返回索引](./README.md)  
> **代码锚点**: `l3_node/http_server.py`（诊断端点）、`l3_node/awareness_loop.py`、`l3_node/dag_resume.py`、`l3_node/dag_handoff.py`、`l3_node/global_task_registry.py`

---

## 目录

1. [可观测性体系总览](#一可观测性体系总览)
2. [诊断 HTTP 端点全景](#二诊断-http-端点全景)
3. [可观测日志标签体系](#三可观测日志标签体系)
4. [Hook 事件持久化与回放](#四hook-事件持久化与回放)
5. [自治状态机完整详解](#五自治状态机完整详解)
6. [DAG 续跑与断点恢复](#六dag-续跑与断点恢复)
7. [DAG 跨节点转交（Handoff）](#七dag-跨节点转交handoff)
8. [ProactiveReporter 日终报告](#八proactivereporter-日终报告)
9. [运行时快照与监控面板](#九运行时快照与监控面板)
10. [告警与飞书通知](#十告警与飞书通知)

---

## 一、可观测性体系总览

```mermaid
flowchart TB
    subgraph PILLARS["可观测性三支柱"]
        LOGS["日志（Logs）\n结构化可检索标签\n[StrategyShift] / [ExecutionBrief]\n[delegate RunReport] / [ExperienceRAG]..."]
        METRICS["指标（Metrics）\nToken 用量 / 磁盘 / 任务数\nCritic 拒绝率 / 迭代均值"]
        EVENTS["事件（Events）\nhook_events.sqlite3\nHook 持久化事件流\n支持回放续跑"]
    end

    subgraph ACCESS["访问方式"]
        HTTP_DIAG["诊断 HTTP 端点\n/api/v1/autonomy/*\n/api/v1/registry/*"]
        WS_PUSH["WebSocket 推送\n后台任务 progress/completed"]
        LARK_ALERT["飞书告警通知\n磁盘/Token/失败阈值"]
        LOG_FILE["日志文件\nstdout/stderr + structlog"]
    end

    PILLARS --> ACCESS
```

---

## 二、诊断 HTTP 端点全景

### 2.1 端点完整列表

| 端点 | 方法 | 用途 | 典型使用场景 |
|------|------|------|------------|
| `/api/v1/autonomy/status` | GET | 可观测性全局面板（Token/磁盘/任务/告警） | 监控仪表盘 |
| `/api/v1/registry/runtime-snapshot` | GET | 前台任务快照 + hot_inject 缓冲 | 查看当前运行状态 |
| `/api/v1/registry/global-tasks` | GET | 全局任务注册表（跨进程） | 查看所有活跃任务 |
| `/api/v1/registry/task-dag-active` | GET | 当前 TaskDAG active.json 内容 | DAG 进度追踪 |
| `/api/v1/registry/hook-events-recent` | GET | Hook 事件历史（含 run_id 精确筛） | 事件回溯调试 |
| `/api/v1/registry/hook-replay` | POST | 触发 Hook 事件回放（续跑） | 失败任务恢复 |
| `/api/v1/registry/dag-resume` | POST | DAG 轻量续跑（dry_run / apply） | 断点续跑 |
| `/api/v1/registry/dag-guardrails` | GET | DAG 预算护栏状态 | 护栏监控 |
| `/dag-handoff/export` | POST | 导出 DAG 转交包 | 负载均衡/迁移 |
| `/dag-handoff/import` | POST | 导入 DAG 转交包 | 接受转交 |
| `/dag-handoff/auto-transfer` | POST | 自动转交到空闲 peer | 自动负载均衡 |
| `/api/v1/autonomy/intents` | GET | 持久化意图列表 | 查看自治任务 |
| `/api/v1/autonomy/intents` | POST | 创建持久化意图 | 注册自治任务 |
| `/api/v1/autonomy/intents/{id}` | PUT | 更新意图（启停/修改） | 管理自治任务 |
| `/api/v1/autonomy/intents/{id}` | DELETE | 删除意图 | 注销自治任务 |

### 2.2 /api/v1/autonomy/status 响应格式

```json
{
  "status": "healthy",
  "timestamp": 1748390400.0,
  "version": "v1.0.0",
  "uptime_sec": 86400,
  "token_usage": {
    "today_total": 456789,
    "daily_budget": 1000000,
    "usage_pct": 45.7,
    "alerts": []
  },
  "disk": {
    "free_gb": 42.3,
    "alert_threshold_gb": 5.0,
    "status": "ok"
  },
  "active_tasks": {
    "foreground": 1,
    "background": 3,
    "autonomy": 0
  },
  "awareness_loop": {
    "running": true,
    "scan_interval_sec": 60,
    "last_scan_at": 1748390380.0,
    "active_intents": 5
  },
  "skill_evolution": {
    "total_evolutions": 12,
    "last_evolution_at": 1748300000.0
  }
}
```

### 2.3 /api/v1/registry/runtime-snapshot 响应

```mermaid
flowchart LR
    subgraph SNAPSHOT_RESP["runtime-snapshot 响应结构"]
        RS_FRONT["foreground_tasks[]\n当前前台运行任务\n{run_id, session_id, intent, started_at, iteration}"]
        RS_HOT["hot_inject_pending[]\n等待并入的补充指令\n{session_id, texts[]}"]
        RS_DAG["dag_active\n当前 TaskDAG 摘要\n{dag_id, done/pending/running}"]
        RS_META["meta\n{node_id, l3_version, process_pid}"]
    end
```

---

## 三、可观测日志标签体系

### 3.1 可检索日志标签全表

| 标签 | 触发场景 | 用于排查 |
|------|---------|---------|
| `[delegate RunReport]` | delegate 子任务完成汇总 | 子任务成功/失败率 |
| `[FanOut RunReport]` | FanOut 并行完成汇总 | 批量任务效率 |
| `[Pipeline]` | Pipeline 流水线阶段 | 依赖链执行顺序 |
| `[ExecutionBrief]` | 任务有界退出 | 为何停止、失败类别 |
| `[StrategyShift]` | 策略切换 | 降级路径追踪 |
| `[Discussion]` | 讨论模式轮次摘要 | 辩论过程审查 |
| `[L3 Agent]` | coordinate/delegate RunReport | 跨节点/并行执行 |
| `[SubAgent]` | 子 Agent 执行日志 | 子任务内部状态 |
| `[ExperienceRAG]` | 经验检索/写入 | few-shot 命中率 |
| `[SkillEvolution]` | Skill 进化事件 JSONL | 进化过程审计 |
| `[StrategyInject]` | 策略消息注入 | 韧性链路追踪 |
| `[RetryAttempt]` | 重试触发 | 重试频率分析 |
| `[SkipItem]` | per_item 跳过 | 坏数据追踪 |
| `[hook_events]` | Hook 事件持久化 | 事件回放需求 |
| `[DAGResume]` | DAG 续跑触发 | 断点恢复验证 |
| `[DAGHandoff]` | DAG 跨节点转交 | 负载均衡追踪 |
| `[prompt_suffix_eviction]` | System prompt 后缀裁剪 | Token 优化诊断 |
| `[CriticReject]` | Critic 审查拒绝 | 危险操作拦截统计 |

### 3.2 日志结构示例

```python
# structlog 格式（JSON）
{
    "ts": "2026-05-28T09:45:00+08:00",
    "level": "info",
    "tag": "[delegate RunReport]",
    "run_id": "run_20260528_abc123",
    "session_id": "sess_xyz",
    "ok_count": 8,
    "total_count": 10,
    "failed_items": [
        {"index": 3, "error_class": "transient", "message": "TimeoutError"},
        {"index": 7, "error_class": "per_item", "message": "PDF 损坏"}
    ],
    "elapsed_sec": 34.2
}

# [StrategyShift]
{
    "tag": "[StrategyShift]",
    "from_strategy": "batch(n=10)",
    "to_strategy": "batch(n=1)",
    "reason": "3次 resource 错误后降级",
    "attempt": 3
}
```

---

## 四、Hook 事件持久化与回放

### 4.1 Hook 持久化存储

```
~/.jachin/workspace/
└─ hook_events.sqlite3
   └─ 表: hook_events
      ├─ event_id     TEXT (UUID)
      ├─ run_id       TEXT (关联的 run_agent 实例)
      ├─ dag_id       TEXT (关联的 DAG)
      ├─ hook_type    TEXT (HOOK_ON_TASK_NODE_DONE / HOOK_AFTER_TOOL_EXEC / ...)
      ├─ payload_json TEXT (事件数据 JSON)
      ├─ timestamp    REAL (Unix 时间戳)
      └─ session_id   TEXT (会话标识)
```

### 4.2 事件写入流程

```mermaid
sequenceDiagram
    participant HOOK_RUN as run_agent (HOOK_ON_TASK_NODE_DONE)
    participant HOOK_MGR as hooks_pipeline
    participant HOOK_LOG as hook_events.sqlite3

    Note over HOOK_RUN: JACHIN_PERSIST_HOOKS=1

    HOOK_RUN->>HOOK_MGR: fire_hook(HOOK_ON_TASK_NODE_DONE, {node_id, status, result})
    HOOK_MGR->>HOOK_MGR: 调用所有注册处理器
    HOOK_MGR->>HOOK_LOG: INSERT hook_event(run_id, dag_id, hook_type, payload, ts)

    Note over HOOK_RUN: 另一轮工具调用后

    HOOK_RUN->>HOOK_MGR: fire_hook(HOOK_AFTER_TOOL_EXEC, {tool_id, result})
    HOOK_MGR->>HOOK_LOG: INSERT hook_event(...)
```

### 4.3 回放与续跑时序

```mermaid
sequenceDiagram
    participant ADMIN as 管理员/自动化
    participant API9 as POST /registry/hook-replay
    participant REPLAY9 as hook_replay_executor
    participant LOG9 as hook_events.sqlite3
    participant DAG9 as active.json
    participant AGENT9 as run_agent

    ADMIN->>API9: {run_id: "run_abc", dry_run: true}

    API9->>REPLAY9: probe_dag_resume(run_id)
    REPLAY9->>LOG9: 按 run_id + ts 顺序读取 hook_events
    LOG9-->>REPLAY9: [HOOK_ON_TASK_NODE_DONE(n1, done), HOOK_ON_TASK_NODE_DONE(n2, done), ...]
    REPLAY9->>REPLAY9: 计算 completed_node_ids = {n1, n2}
    REPLAY9->>REPLAY9: 计算 pending_nodes = {n3, n4}（depends_on 已满足）
    REPLAY9-->>API9: DryRunReport{completed: [n1, n2], to_resume: [n3, n4]}

    ADMIN->>API9: {run_id: "run_abc", dry_run: false}
    API9->>REPLAY9: apply_dag_resume(run_id)
    REPLAY9->>DAG9: 写入 active.json（n3, n4 恢复为 pending）
    REPLAY9->>AGENT9: run_agent(resume_intent, channel="resume")
    Note over AGENT9: 跳过已完成节点 n1, n2，从 n3 继续
```

---

## 五、自治状态机完整详解

### 5.1 完整状态机

```mermaid
stateDiagram-v2
    [*] --> Active: 意图创建 (POST /api/v1/autonomy/intents)

    state Active {
        [*] --> WaitingTrigger
        WaitingTrigger --> Checking: 到达扫描周期
        Checking --> WaitingTrigger: 条件不满足
        Checking --> Triggered: 条件满足 → fire_intent
    }

    Active --> Running: fire_intent → run_agent 启动

    Running --> Success: run_agent Final Answer（无异常）
    Running --> Failed: 异常 / 超时 / ExecutionBrief

    Success --> Active: consecutive_failures 重置为 0
    Success --> Evolving: consecutive_successes ≥ min_evolve_successes(3)

    Failed --> Active: consecutive_failures < failure_threshold(5)
    note right of Failed
        每次失败:
        consecutive_failures += 1
        record_execution(intent_id, success=False)
    end note

    Failed --> Healing: consecutive_failures ≥ failure_threshold(5)

    Healing --> Active: Level3Healer 诊断 + 策略注入成功
    Healing --> Disabled: 无法自愈 (diagnose=irreversible)

    Active --> Disabled: PUT /intents/{id} {enabled: false}
    Disabled --> Active: PUT /intents/{id} {enabled: true}
    Disabled --> Active: autoreset_after_sec 到期自动恢复

    Evolving --> Active: SkillEvolver 完成 (applied/rejected/no_change)

    Active --> Staged: healing 路径 → save_staged_evolution
    Staged --> Evolving: 下次 Success 消费候选 pending_evolution
```

### 5.2 意图触发类型详解

```mermaid
flowchart TB
    subgraph INTENT_TYPES["意图触发类型（PersistedIntent.trigger_type）"]
        INT_TYPE["interval\n固定间隔\ninterval_minutes: 60\n→ 每小时运行一次\n→ 适合: 巡检/定期报告"]

        CRON_TYPE["cron\nCron 表达式\ncron_expr: '0 23 * * *'\n→ 每天 23:00 运行\n→ 适合: 日终结算/归档"]

        COND_TYPE["condition\n条件触发\ncondition_expr: 'disk_free_gb < 3'\n→ 当磁盘 < 3GB 时运行清理\n→ 适合: 告警响应/事件驱动"]
    end

    subgraph COND_EVAL["condition 评估流程"]
        BUILTIN["内置规则（快速路径）:\n• disk_free_gb < X\n• token_used_today > X\n• consecutive_failures >= X\n• hour_of_day == X"]
        LLM_EVAL2["LLM 评估（慢路径）:\n不匹配内置规则时\n轻量 LLM 推理判断\nJACHIN_AUTONOMY_COND_LLM_EVAL=1"]
    end

    COND_TYPE --> COND_EVAL
```

---

## 六、DAG 续跑与断点恢复

### 6.1 续跑决策树

```mermaid
flowchart TB
    CRASH["进程崩溃 / 任务中断"]

    CRASH --> RESTART["L3 进程重启"]
    RESTART --> PROBE["dag_resume.probe_dag_resume()\n读取 hook_events.sqlite3"]
    PROBE --> FOUND{"找到未完成 DAG?"}

    FOUND -->|"否"| NORMAL["正常启动（无续跑）"]
    FOUND -->|"是"| ANALYZE["分析已完成节点\nCompleted = {n|HOOK_ON_TASK_NODE_DONE(n, done)}"]

    ANALYZE --> PENDING9["计算待续节点\nPending = {n|depends_on ⊆ Completed}"]

    PENDING9 --> CHK_SAFE{"续跑前 Guardrails 检查\nJACHIN_DAG_RESUME_GUARDRAILS=1"}
    CHK_SAFE -->|"预算已满"| NO_RESUME["不续跑\n输出 ExecutionBrief"]
    CHK_SAFE -->|"预算充足"| DO_RESUME["apply_dag_resume\n更新 active.json\n构建 resume_intent"]

    DO_RESUME --> RUN_RESUME["run_agent(resume_intent)\n自动跳过已完成节点"]

    subgraph AUTO["自动续跑（JACHIN_DAG_AUTO_RESUME=1）"]
        AUTO_TRIGGER["启动时自动 probe\n发现未完成 DAG → 直接 apply"]
    end

    DO_RESUME -.-> AUTO
```

### 6.2 续跑 intent 格式

```
[DAGResume] 续跑 run_id=run_20260528_abc123

DAG ID: dag_20260528_def456
已完成节点: n1(分析代码), n2(编写接口), n3(单元测试)
待续节点: n4(集成测试), n5(更新文档)

请从节点 n4 继续执行，跳过已完成的节点。
当前进度: 3/5 完成（60%）
已知信息: n3 产出 "测试覆盖率 87%"
```

---

## 七、DAG 跨节点转交（Handoff）

### 7.1 转交完整流程

```mermaid
sequenceDiagram
    participant L3_SRC as L3 节点 A（源，超载）
    participant COORD2 as DAG Coordinator
    participant L3_DST as L3 节点 B（目标，空闲）

    Note over L3_SRC: 检测到 load > 0.8 或节点下线预警

    L3_SRC->>COORD2: list_alive_nodes()
    COORD2-->>L3_SRC: [{node_b, load: 0.2, skills: [...]}, ...]
    L3_SRC->>COORD2: find_idle_peer(min_free_load=0.5, skill_required=["hr"])
    COORD2-->>L3_SRC: node_b

    L3_SRC->>L3_SRC: POST /dag-handoff/export
    Note over L3_SRC: DagHandoffPackage {\n  dag_id,\n  completed_node_ids: [n1, n2],\n  pending_nodes: [n3, n4],\n  context_summary,\n  resume_intent,\n  workspace_files: {path: content}\n}

    L3_SRC->>L3_DST: POST /dag-handoff/import (HandoffPackage)
    L3_DST->>L3_DST: 写入 active.json + workspace files
    L3_DST-->>L3_SRC: HandoffImportResult{success, resume_intent}

    L3_SRC->>COORD2: release_dag(dag_id, transfer_token)
    L3_DST->>COORD2: claim_dag(dag_id, transfer_token)

    L3_DST->>L3_DST: run_agent(resume_intent)
    Note over L3_DST: 从 n3 继续执行

    L3_DST->>L3_SRC: WebSocket push: "DAG 续跑完成"
```

### 7.2 自动转交触发条件

```mermaid
flowchart LR
    DETECT["load_monitor 检测\n(每 30s)"]
    DETECT --> CHK_LOAD{"当前节点 load > 0.8?"}

    CHK_LOAD -->|"否"| IDLE_CHECK["检查 Semaphore 槽位\n当前并发 ≥ max-1?"]
    IDLE_CHECK -->|"否"| NO_OP["无需转交"]
    IDLE_CHECK -->|"是"| TRIGGER_AUTO

    CHK_LOAD -->|"是"| TRIGGER_AUTO["触发自动转交\nPOST /dag-handoff/auto-transfer"]
    TRIGGER_AUTO --> AUTO_LOGIC["dag_handoff.auto_handoff_to_peer()\n① find_idle_peer()\n② export(dag_id)\n③ import to peer\n④ release + claim"]
```

---

## 八、ProactiveReporter 日终报告

### 8.1 报告触发时序

```mermaid
sequenceDiagram
    participant LOOP9 as AwarenessLoop
    participant PR9 as ProactiveReporter
    participant MEM9 as Memory Nexus
    participant LLM9 as LLM
    participant LARK9 as 飞书 Bot

    Note over LOOP9: 每日 23:55（JACHIN_PROACTIVE_REPORT_HOUR=23）

    LOOP9->>PR9: trigger_daily_report()
    PR9->>MEM9: recall_room("Core", "Kalaroko_Default", limit=20)
    MEM9-->>PR9: 今日巡检摘要 + 事件记录

    PR9->>PR9: 读取今日指标
    Note over PR9: Token 用量 / 任务成功率 / Skill 进化数\n连续失败意图 / 磁盘趋势

    PR9->>LLM9: generate_daily_report(metrics, memories)
    LLM9-->>PR9: 日终报告文案（Markdown）

    PR9->>LARK9: send_rich_message(report_card)
    Note over LARK9: 飞书富文本卡片:\n📊 今日执行摘要\n✅ 成功: 47 任务\n❌ 失败: 3 任务\n💡 Skill 进化: 2 次\n💾 Token 使用: 456,789
```

### 8.2 报告内容结构

```
📊 Jachin 日终报告 · 2026-05-28

执行摘要
────────
  ✅ 完成任务: 47
  ❌ 失败任务: 3（已触发 Level3Healer 诊断）
  ⏱️  平均 ReAct 迭代: 3.2 轮
  📝 Memory 写入: 128 条

资源状态
────────
  🔑 Token 今日: 456,789 / 1,000,000 (45.7%)
  💾 磁盘剩余: 42.3 GB ✅
  🔄 后台队列: 0 待处理

AGI 进化
────────
  🧠 Experience 记录: +23 条（累计 847）
  ⚙️  Skill 进化: 2 次（hr_analyzer4 · bi_report）
  🔍 Level3Healer: 修复 2 个意图（成功率 100%）

待关注
────────
  ⚠️  候选人分析任务 3 次失败（根因: API 429）
     建议: 降低并发，或申请更高 QPS 配额
```

---

## 九、运行时快照与监控面板

### 9.1 监控数据层次

```mermaid
flowchart TB
    subgraph L1_HEALTH["L1: 健康检查（秒级）"]
        PROCESS["进程存活\nPID + 内存占用"]
        WSOCK["WebSocket 连接数"]
        QUEUE_DEPTH["队列深度\nSIQ + BG"]
    end

    subgraph L2_TASKS["L2: 任务状态（分钟级）"]
        ACTIVE_TASKS["活跃任务\n前台 + 后台 + 自治"]
        DAG_STATE["DAG 状态\n节点进度"]
        HOOK_EVENTS["Hook 事件流\n最近 N 条"]
    end

    subgraph L3_METRICS["L3: 业务指标（小时级）"]
        TOKEN_USAGE["Token 用量趋势"]
        EXP_GROWTH["Experience 库增长"]
        EVOLUTION["Skill 进化历史"]
        FAIL_RATE["任务失败率"]
    end

    L1_HEALTH --> L2_TASKS --> L3_METRICS
    L1_HEALTH --> LARK_ALERT9["飞书告警（立即）"]
    L3_METRICS --> DAILY_RPT["日终报告（23:55）"]
```

### 9.2 GET /api/v1/registry/hook-events-recent 示例

```json
{
  "run_id": "run_20260528_abc123",
  "events": [
    {
      "event_id": "evt_001",
      "ts": 1748390300.0,
      "hook_type": "HOOK_ON_TASK_NODE_DONE",
      "payload": {"node_id": "n1", "status": "done", "elapsed": 12.3}
    },
    {
      "event_id": "evt_002",
      "ts": 1748390400.0,
      "hook_type": "HOOK_AFTER_TOOL_EXEC",
      "payload": {"tool_id": "core:fs_write", "success": true}
    }
  ],
  "total": 2,
  "dag_id": "dag_20260528_def456"
}
```

### 9.3 GET /api/v1/registry/dag-guardrails 示例

```json
{
  "dag_id": "dag_20260528_def456",
  "guardrails_enabled": true,
  "budget": {
    "max_iterations": 50,
    "used_iterations": 23,
    "max_tool_calls": 100,
    "used_tool_calls": 41,
    "max_tokens": 200000,
    "used_tokens": 89000,
    "max_nodes": 20,
    "total_nodes": 5
  },
  "status": "within_budget",
  "alerts": []
}
```

---

## 十、告警与飞书通知

### 10.1 告警触发矩阵

| 告警类型 | 触发条件 | 级别 | 飞书消息类型 |
|---------|---------|------|------------|
| 磁盘告警 | `disk_free_gb < JACHIN_DISK_ALERT_GB` | ⚠️ Warning | 文本 + 动作按钮 |
| Token 告警 | `token_today > budget * 0.9` | ⚠️ Warning | 文本 |
| Token 超限 | `token_today > budget` | 🔴 Critical | 富文本卡片 |
| 意图连续失败 | `consecutive_failures >= threshold` | 🔴 Critical | 富文本卡片 + 诊断摘要 |
| Skill 进化完成 | 每次进化成功 | ℹ️ Info | 简单文本 |
| DAG 转交完成 | handoff 成功 | ℹ️ Info | 简单文本 |
| 日终报告 | 每日 23:55 | 📊 Report | 富文本卡片 |
| Level3Healer 诊断 | 连续失败 → 自愈 | ⚠️ Warning | 富文本卡片 + 建议 |

### 10.2 飞书告警发送时序

```mermaid
sequenceDiagram
    participant MONITOR as AwarenessLoop / 业务代码
    participant ALERT9 as lark_alerter.py
    participant BOT9 as 飞书 Bot（Webhook）
    participant USER9 as 运维人员

    MONITOR->>ALERT9: send_lark_alert(AlertMessage{level, title, content, actions})
    ALERT9->>ALERT9: 生成飞书卡片 JSON（card_template）
    ALERT9->>ALERT9: 防抖（同类告警 10 分钟内不重复）
    ALERT9->>BOT9: POST JACHIN_LARK_WEBHOOK_URL
    BOT9-->>ALERT9: 200 OK
    BOT9->>USER9: 推送卡片消息

    alt actions 包含操作按钮
        USER9->>BOT9: 点击"立即续跑"按钮
        BOT9->>ALERT9: 回调 POST /api/v1/registry/dag-resume
        ALERT9->>ALERT9: 触发 DAG 续跑
    end
```

### 10.3 资源监控告警流程

```mermaid
flowchart TD
    AWL10["AwarenessLoop.check_resource_alerts()"]

    AWL10 --> DISK_CHK2{"disk_free_gb < threshold?"}
    DISK_CHK2 -->|"是"| DISK_ALERT["发送磁盘告警\n建议: 清理工作区 / 归档旧文件"]

    AWL10 --> TOKEN_CHK{"token_today > budget * 0.9?"}
    TOKEN_CHK -->|"是"| TOKEN_WARN["发送 Token 告警\n降级: 日常档替代复杂档"]
    TOKEN_CHK -->|"token_today > budget"| TOKEN_CRIT["发送 Token 超限告警\n暂停: 非紧急自治任务"]

    AWL10 --> FAIL_CHK{"intent.consecutive_failures >= threshold?"}
    FAIL_CHK -->|"是"| FAIL_ALERT["触发 Level3Healer\n发送故障告警（含诊断）"]

    AWL10 --> MCP_CHK{"MCP 进程异常?"}
    MCP_CHK -->|"是"| MCP_RESTART["自动重启 MCP 进程\n告警（重启失败时）"]
```

---

**上一篇**: [06_CONCURRENCY_RESILIENCE.md](./06_CONCURRENCY_RESILIENCE.md)  
**索引**: [README.md](./README.md)
