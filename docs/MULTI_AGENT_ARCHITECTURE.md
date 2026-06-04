# Jachin 多 Agent 架构全景文档

> **版本**: v1.0 — 2026-05-14  
> **SSOT 关联**: `docs/Jachin 视角的「四大原语」终极架构规范.md`、`docs/JACHIN_EXECUTION_RESILIENCE_CONTRACT.md`  
> **代码锚点**: `l3_node/agent_core.py`、`l3_node/primitives/multi_agent/`、`l3_node/primitives/agent_tasks/background_task_service.py`

---

## 一、为什么需要多 Agent？

Jachin 系统面向的是真实企业场景中的**复杂、耗时、多维度任务**，单个 Agent ReAct 循环在以下场景会成为瓶颈：

| 场景 | 问题 | 多 Agent 价值 |
|------|------|--------------|
| 同时分析多份简历/报告 | 串行处理耗时 O(n)，前台等待不可接受 | 并行 FanOut → O(1) 时间，每份独立处理 |
| 编写代码 + 撰写文档 + 生成测试 | 角色混淆，模型在三种模式间切换出错 | 各角色专属 SubAgent，不互相干扰 |
| 大型重构任务 | 单轮 max_iterations 耗尽后截断 | Pipeline 分阶段执行：计划 → 实施 → 审查 |
| 多节点机器人集群 | 单 L3 节点算力/API 速率有限 | L2 coordinate API 跨节点分发子任务 |
| 调试复合型浏览器错误 | DOM 错误叠加网络故障，单专家难覆盖 | 并行多专家会诊（MoE·Parallel） |

---

## 二、多 Agent 机制全景（四大形态）

### 2.1 `delegate` — 同进程嵌套 SubAgent（主路径）

**触发方式**：主 Agent 在 ReAct 中输出：

```
Action: delegate
Action Input: {"sub_tasks": [{"role": "coder", "task": "..."}, {"role": "analyst", "task": "..."}]}
```

**执行流程**：

```
主 run_agent
  └─ 解析 delegate → 校验 max_delegate_depth
       └─ asyncio.gather (受 Semaphore 限速)
            ├─ SubAgent[coder].run_once(task_1)  → run_agent(depth+1)
            ├─ SubAgent[analyst].run_once(task_2) → run_agent(depth+1)
            └─ SubAgent[writer].run_once(task_3)  → run_agent(depth+1)
  └─ 合并 RunReport → Observation → 继续 ReAct → Final Answer
```

**关键配置**：

| 参数 | 位置 | 默认 | 说明 |
|------|------|------|------|
| `agent.max_delegate_depth` | `nexus_config.json` | `2` | 防止无限嵌套 delegate |
| `agent.delegate_max_concurrent` | `nexus_config.json` | `4` | 单次 delegate 最大并发 SubAgent 数（Semaphore） |
| `agent.sub_agent_max_total_tokens` | `nexus_config.json` | `190000`（未配置时代码默认） | 子 Agent 单次 Token 上限（Worker A/B/C） |
| `agent.main_max_total_tokens` | `nexus_config.json` | 可选 | 主编排 / 阶段三 Publisher |

### 2.2 `coordinate` — 跨节点多 L3 协同（L2 调度）

**触发方式**：

```
Action: coordinate
Action Input: {"intent": "...", "sub_tasks": [{"intent": "...", "skill_required": "...", "input_data": {...}}]}
```

**执行流程**：

```
主 L3 _coordinate_task
  └─ POST /api/v2/coordinate/task → L2 数据库
       └─ L2 按 skill_required 匹配节点，派发子任务
            ├─ L3-A: run_tool(native_tool) 或 run_agent(intent)
            └─ L3-B: run_tool(native_tool) 或 run_agent(intent)
  └─ GET /api/v2/coordinate/poll + /status 轮询直至完成
  └─ 聚合结果 → Observation
```

**注意**：`coordinate` 适用于多物理节点集群（企业内网多台 Jachin 设备）；单节点部署时子任务仍分配给本机自身执行。

### 2.3 `core:submit_background_task` — 异步 Agent Task

**触发方式**：

```
Action: core:submit_background_task
Action Input: {"intent": "...", "max_iterations": 24, "priority": 1, "tags": ["hr", "recruitment"]}
```

**执行流程**：

```
submit_background_task_sync
  └─ 入队（asyncio.Queue + SQLite 持久化）
  └─ 返回 task_id 给前台 → Final Answer 含 task_id

bg-worker-N（独立 asyncio 任务）
  └─ _run_job → run_agent(intent, channel="background_task")
  └─ broadcast_background_task_event → WebSocket 推送进度/完成
```

**新增字段**：

| 字段 | 说明 |
|------|------|
| `priority` | 0=普通, 1=较高, 2=紧急（当前 FIFO 排序预留，未来扩展 PriorityQueue） |
| `tags` | 字符串标签列表，如 `["hr", "recruitment"]`，用于监控面板筛选 |
| `parent_run_id` | 父 run_agent 的 run_id，由 delegate 路径提交时注入，用于可观测性归因 |

### 2.4 MCP Pull delegate — 跨进程 MCP 委派

L2 将 MCP 工具调用塞入 Redis 队列（`l3_mcp_delegate_queue:{node_id}`），L3 的 `run_mcp_delegate_pull_forever` 消费并执行，结果回写 L2。  
`allow_l2_delegate=False` 防止 L2↔L3 递归委派。

---

## 三、SubAgent 角色体系（本次新增）

`agent_core.py` 的 `SUB_AGENT_PROMPTS` 与 `SUB_AGENT_ALLOWED_SKILLS` 定义了所有可用角色：

| 角色（role） | 专长 | 可用工具集 | 典型用途 |
|------------|------|-----------|---------|
| `coder` | 编写/修改代码 | fs_read, fs_write, apply_patch, shell_exec | 功能实现、Bug 修复 |
| `writer` | 撰写文档 | fs_read, fs_write | API 文档、README、报告撰写 |
| `researcher` | 信息调研 | fs_read, shell_exec | 竞品分析、技术调研 |
| `analyst` *(新增)* | 数据分析与洞察 | fs_read, shell_exec, local_memory_search | 数据报表、指标分析、趋势提炼 |
| `planner` *(新增)* | 任务拆解与规划 | fs_read, local_memory_search | 需求分析、方案设计、里程碑规划 |
| `reviewer` *(新增)* | 代码审查 | fs_read, shell_exec | PR 审查、安全扫描、代码质量检查 |
| `summarizer` *(新增)* | 内容摘要 | fs_read, local_memory_search | 长文档要点提炼、会议纪要 |
| `data_processor` *(新增)* | 数据清洗转换 | fs_read, fs_write, shell_exec | CSV/JSON 处理、数据迁移 |
| `tester` *(新增)* | 测试用例编写 | fs_read, fs_write, shell_exec | 单元测试、集成测试、测试报告 |
| `default` | 通用子任务 | fs_read, fs_write, shell_exec | 其他子任务 |

**设计原则**：
- 每个角色仅获得最小工具集（最小权限），绝不开放发邮件、MCP 外网访问等敏感技能
- `coder` 角色自动切换编码模型（`LLM_CODER_MODEL`）以提升代码质量
- 角色工具集受 L2 `service_switches` 控制，子账号未开启则拒绝

---

## 四、新增编排原语（本次新增）

### 4.1 FanOut 并行编排 — `l3_node/primitives/multi_agent/fanout.py`

**适用场景**：批量同构子任务（多份文档分析、多数据源查询、候选人批量初筛）

```python
from l3_node.primitives.multi_agent.fanout import fanout_parallel

result = await fanout_parallel(
    items=[
        {"role": "analyst", "task": "分析 Q1 数据", "context_data": q1_csv},
        {"role": "analyst", "task": "分析 Q2 数据", "context_data": q2_csv},
        {"role": "analyst", "task": "分析 Q3 数据", "context_data": q3_csv},
    ],
    engine=engine,
    max_concurrent=3,
    delegate_depth=1,
)

print(result.format_summary())
for item in result.ok_items:
    print(f"子任务 {item.index}: {item.result[:200]}")
```

**返回 `FanoutResult`**：

```python
@dataclass
class FanoutResult:
    status: str           # "completed" | "partial" | "failed"
    ok_count: int
    failed_count: int
    total: int
    degraded: bool        # 有失败但仍有成功时为 True
    items: list[FanoutItemResult]
    elapsed_sec: float
```

**为什么需要 FanOut？**

| 对比 | 串行 `run_agent` 循环 | FanOut 并行 |
|------|---------------------|------------|
| 10 份文档分析 | ~10 × T(单次) | ~T(单次) + 少量调度开销 |
| 部分失败处理 | 需要手写 try/except | 自动记录 failed_items，其余继续 |
| Token 成本 | 相同 | 相同（并行不减少 Token） |
| Rate Limit 风险 | 低 | 受 max_concurrent Semaphore 控制 |

### 4.2 Pipeline 流水线编排 — `l3_node/primitives/multi_agent/pipeline.py`

**适用场景**：有依赖链的多阶段任务（Planner → Coder → Reviewer，Researcher → Analyst → Writer）

```python
from l3_node.primitives.multi_agent.pipeline import run_pipeline, PipelineStage

result = await run_pipeline(
    stages=[
        PipelineStage(role="planner",  task="为以下需求设计实现方案"),
        PipelineStage(role="coder",    task="按方案编写代码"),
        PipelineStage(role="reviewer", task="审查上述代码的安全性和可维护性"),
        PipelineStage(role="writer",   task="根据审查结果更新 README"),
    ],
    initial_context={"goal": "实现用户权限管理 API"},
    engine=engine,
    delegate_depth=1,
)

if result.status == "completed":
    print("最终输出:", result.final_output)
else:
    print("ExecutionBrief:", result.execution_brief)
```

**上下文自动传递**：每阶段输出自动注入为下一阶段的 `context_data`（截取前 3000 字符），无需手动传递。

**失败策略**：

| 策略 | 行为 |
|------|------|
| `on_failure="stop"`（默认） | 失败时生成 ExecutionBrief 并中止，避免后续阶段基于错误结果继续 |
| `on_failure="continue"` | 跳过失败阶段，使用上一阶段输出继续，适合非阻塞采集场景 |

---

## 五、delegate 并发控制（本次优化）

### 问题背景

原实现在 `asyncio.gather` 中无限并行，若模型一次 delegate 10+ 个子任务，会同时发起 10+ 个 `run_agent` 调用，导致：
- API 速率限制（Rate Limit）被触发
- 系统内存峰值激增
- 日志无法定位排查

### 解决方案

新增 `_delegate_max_concurrent_cfg()` + `asyncio.Semaphore`：

```python
_max_concurrent = _delegate_max_concurrent_cfg()  # 默认 4，可配置
if _max_concurrent > 0 and len(sub_tasks) > _max_concurrent:
    _sem = asyncio.Semaphore(_max_concurrent)
    async def _run_with_sem(_t):
        async with _sem:
            return await _run_sub_agent(_t, engine, delegate_depth=_child_depth)
    results = await asyncio.gather(*[_run_with_sem(t) for t in sub_tasks], ...)
```

**配置**（`~/.jachin/nexus_config.json`）：

```json
{
  "agent": {
    "delegate_max_concurrent": 4,
    "max_delegate_depth": 2
  }
}
```

---

## 六、结构化 RunReport（本次优化）

### 问题背景

原实现将所有子任务结果拼接为纯文本 Observation，模型无法准确判断「哪些失败了」。

### 解决方案

每次 delegate 完成后，在 Observation 首行注入结构化摘要：

```
[delegate RunReport] 完成: 3/4 成功，1 失败

---

[子任务 1·coder]
（代码内容...）

[子任务 2·analyst]
（分析结果...）

[子任务 3·writer]
（文档内容...）

[子任务 4·reviewer 失败: TimeoutError: ...]
```

同时写入日志（可检索）：

```
[L3 Agent] delegate RunReport depth=1 {"status": "partial", "ok_count": 3, "failed_count": 1, ...}
```

---

## 七、并行多专家会诊（本次优化）

### 问题背景

`agentic_mesh` 的 `with_phantom_guard` 装饰器原先串行匹配专家（先检查 DOM 错误，再检查网络错误），无法处理复合型错误（DOM 超时叠加网络 403）。

### 解决方案

新增 `parallel_triage=True` 模式与 `_run_parallel_experts()` 函数：

```python
@with_phantom_guard(skills=["dom", "network"], parallel_triage=True)
async def click_and_navigate(page, ...):
    ...
```

**并行会诊流程**：

```
错误发生
  ├─ 检查记忆库（prefer_action）→ 命中则直接重试
  └─ 并行启动所有匹配专家
       ├─ DomHealer.attempt_heal()   ┐
       └─ NetworkRecoveryExpert()    ┘ asyncio.gather
  └─ 任意专家成功 → 记忆库记录成功策略 → 重试
  └─ 所有专家失败 → 提交原始异常
```

---

## 八、`context_data` 字段（本次新增）

`delegate` 的 `sub_tasks` 现在支持 `context_data` 字段，允许主 Agent 将数据直接传给子 Agent，减少子 Agent 自行读文件的开销：

```json
{
  "sub_tasks": [
    {
      "role": "analyst",
      "task": "分析以下数据，找出异常点",
      "context_data": {"date": "2026-05-14", "metrics": [{"name": "CPM", "value": 25.3}]},
      "max_iterations": 5
    }
  ]
}
```

子 Agent 会收到：

```
分析以下数据，找出异常点

【上下文数据】
{
  "date": "2026-05-14",
  "metrics": [{"name": "CPM", "value": 25.3}]
}
```

---

## 九、多 Agent 与单主轴 ReAct 的关系

Jachin 的核心架构是**单主轴 `run_agent` ReAct**，多 Agent 是其上的**按需正交分支**，而非对等拓扑：

```
用户消息
  └─ run_agent（主 ReAct）
       ├─ 简单工具调用 → run_tool → Observation
       ├─ delegate → SubAgent × N（并行嵌套 run_agent）
       ├─ coordinate → L2 API → 跨节点执行
       └─ submit_background_task → 异步队列（独立 run_agent）
```

**禁止事项**：
- 不得在子 Agent（`delegate_depth > 0`）中再次 `delegate`（深度校验已阻止）
- 不得在后台任务（`channel=background_task`）中使用 `coordinate`（已在 system prompt 关闭）
- 不得把 MCP 工具称为「Agent」——MCP 是工具（Tool），SubAgent 是 Agent Tasks

---

## 十、可观测性与执行韧性对齐

所有多 Agent 路径均符合 `JACHIN_EXECUTION_RESILIENCE_CONTRACT.md` 的规范：

| 契约要求 | 实现位置 |
|---------|---------|
| 部分成功：子任务失败不拖死全局 | delegate 的 `return_exceptions=True` + RunReport |
| 策略链：重试有上限 | `max_delegate_depth` + `Semaphore` 防级联 |
| 有界退出：出 Brief | Pipeline 的 `on_failure="stop"` 产出 ExecutionBrief |
| 错误分类 | FanoutItemResult 的 `error_class`（transient/per_item） |
| 可检索日志 | `[delegate RunReport]`、`[FanOut RunReport]`、`[Pipeline]`、`[StrategyShift]` 标签 |

---

## 十一、本次修改汇总

### 修改文件

| 文件 | 变更类型 | 核心内容 |
|------|---------|---------|
| `l3_node/agent_core.py` | 优化 | ① 新增 `_delegate_max_concurrent_cfg()`；② delegate 执行加 Semaphore；③ 结构化 RunReport；④ 扩展 SubAgent 角色（analyst/planner/reviewer/summarizer/data_processor/tester）；⑤ `_run_sub_agent` 支持 `context_data` + `max_iterations`；⑥ `_spawn_sub_agent_async` 接受 `max_iterations`；⑦ delegate System Prompt 更新为新角色表 |
| `l3_node/primitives/agent_tasks/background_task_service.py` | 优化 | `BackgroundJob` 新增 `priority`、`tags`、`parent_run_id` 字段；submit 解析逻辑同步支持 |
| `l3_client/local_mcps/agentic_mesh/core_router.py` | 优化 | 新增 `parallel_triage` 模式；新增 `_run_parallel_experts()`；新增记忆库优先复用逻辑 |

### 新增文件

| 文件 | 类型 | 核心内容 |
|------|------|---------|
| `l3_node/primitives/multi_agent/__init__.py` | 新增 | 包入口，导出 `fanout_parallel`、`run_pipeline` |
| `l3_node/primitives/multi_agent/fanout.py` | 新增 | FanOut 并行编排器，含 `FanoutResult`/`FanoutItemResult` RunReport 结构 |
| `l3_node/primitives/multi_agent/pipeline.py` | 新增 | Pipeline 流水线编排器，含 `PipelineStage`/`PipelineResult`/ExecutionBrief |
| `docs/MULTI_AGENT_ARCHITECTURE.md` | 新增 | 本文档 |

---

## 十二、快速上手示例

### 示例 1：批量 HR 简历并行分析（FanOut）

```python
# 在 L3 Skill 或 MCP 工具内调用
from l3_node.primitives.multi_agent.fanout import fanout_parallel

resume_files = ["zhang_san.pdf", "li_si.pdf", "wang_wu.pdf"]
result = await fanout_parallel(
    items=[
        {"role": "analyst", "task": f"分析候选人简历，评估是否匹配后端工程师 JD", "context_data": f}
        for f in resume_files
    ],
    engine=engine,
    max_concurrent=3,
    delegate_depth=1,
)

if result.status in ("completed", "partial"):
    for item in result.ok_items:
        print(f"简历 {item.index}: {item.result[:500]}")
```

### 示例 2：代码功能开发三段式 Pipeline

```python
from l3_node.primitives.multi_agent.pipeline import run_pipeline, PipelineStage

result = await run_pipeline(
    stages=[
        PipelineStage(role="planner",  task="拆解需求：用户登录 + JWT 鉴权"),
        PipelineStage(role="coder",    task="编写 FastAPI 实现代码"),
        PipelineStage(role="tester",   task="编写 pytest 测试用例"),
        PipelineStage(role="reviewer", task="审查代码安全性与可维护性"),
    ],
    initial_context="需求：实现 JWT 鉴权的用户登录 API，支持 refresh token",
    engine=engine,
)
print(result.format_summary())
```

### 示例 3：主 Agent 通过 delegate 调用多角色分身

在用户会话中，模型输出：

```
Thought: 这个任务需要同时分析数据和编写代码，可以用 delegate 分发给专业 Agent。

Action: delegate
Action Input: {
  "sub_tasks": [
    {"role": "analyst", "task": "分析 data/sales_2026.csv 的月度趋势", "max_iterations": 4},
    {"role": "coder", "task": "根据分析结果编写 Python 可视化脚本"},
    {"role": "writer", "task": "撰写数据分析报告摘要"}
  ]
}
```
