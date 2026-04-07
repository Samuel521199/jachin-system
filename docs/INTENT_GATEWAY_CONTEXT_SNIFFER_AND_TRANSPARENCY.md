# Intent Gateway：环境嗅探、状态透传与参谋长软拦截（执行说明）

本文对应 **PR1（Omni-Context Sniffer）**、**PR2（system_status 透传）**、**PR3（参谋长范式）** 及 **nexus 可配置项 / 工作区传入 / intent_tracker 审计**，与 `docs/L3_AMBIGUOUS_INTENT_ARCHITECTURE.md`、`docs/USER_INTENT_RECOGNITION_ARCHITECTURE.md` 互补。

---

## PR1：全域环境嗅探器（入站）

| 项 | 说明 |
|----|------|
| 模块 | `l3_node/intent_gateway/context_sniffer.py` |
| 入口 | `async def build_environment_report(user_input, workspace_dir, *, on_step, run_id, max_total_chars, max_git_chars)` |
| 预算 | 默认全文 **≤1500**、Git **≤500**（均由 `nexus_config.json` → `intent_gateway` 可覆盖）；子进程 **timeout=2s**，失败静默 |
| 战略层 | `jachin_safety_lock.get_safety_lock_snippet(user_text=…)` |
| 经验层 | `local_memory_search.search_local_memories` → Top **2**；摘录上限随 `max_total_chars` 缩放 |
| 落点 | `GatewayContextBundle.extra["environment_report"]`（dict） |
| 流水线 | `apply_gateway_ingress_pipeline` **末尾** `await` 嗅探（在分类 enrich 之前完成数据采集） |
| 关闭 | `context_sniffer_enabled: false` → `environment_report.skipped=true`，仍写 tracker 事件 `context_sniffer_skipped`（若 tracker 开关开） |

**提示词拼装**：`format_environment_report_for_prompt(report)` → `[ENVIRONMENT_REPORT]…[/ENVIRONMENT_REPORT]`，仅在 **参谋长模式** 下注入 system（见 PR3）。

**工作区目录**（优先级从高到低）：

1. `run_agent(..., gateway_workspace_dir="D:/repo")`
2. `implicit_attribution` 中 **`workspace_dir`** / **`git_workspace_dir`** / **`effective_workspace_root`**
3. 默认 `~/.jachin/workspace`（`JACHIN_HOME` 可覆盖根）

HTTP `POST …/agent/run`（见 `http_server.py`）可在 JSON 体中传 **`gateway_workspace_dir`** 或 **`git_workspace_dir`**（与上述 `run_agent` 参数等价）。

---

## PR2：思维链路透明化（复用 on_step）

| 项 | 说明 |
|----|------|
| 协议 | 仍使用 `ws_server` 现有载荷：`step_type` + `content` + `run_id` |
| 新类型 | `step_type == "system_status"`，`content = json.dumps({"status": "…"})` |
| 嗅探 | `build_environment_report` **开始 / 结束** 各推一条 |
| DAG | `validate_subintent_dag(..., on_step=on_step, run_id=run_id)`：校验前 / 成功 / 失败 |
| 门闸 | `task_plan` / `planning_composite` 拦截时各推一条 |
| 桌面端 | `clients/desktop/src/hooks/useSensoryWebSocket.ts`：解析 JSON，展示为 **### 系统状态** |

未新增独立 WebSocket 信道；后台任务路径若未传 `on_step`，则不推送（静默）。

---

## PR3：动态人设与柔性抗命（软拦截统一）

| 项 | 说明 |
|----|------|
| 硬拦截 | **不变**：`evaluate_gateway_ood_gates` 硬阻断、`get_ood_hard_block_reply` 短拒答 |
| 软拦截 | **参谋长范式**：`pushback_copy.py` 中 `CHIEF_ADVISOR_SYSTEM_BLOCK` + 门闸用户消息 |
| 触发条件 | `execution_tier == "composite"` **或** `heuristic_tool_need(user_input)` 为真，且 **`chief_advisor_prompt_enabled`** 为真 |
| Prompt | `_build_system_prompt(..., chief_advisor_mode=…, environment_report_block=…)`：`SuffixChunk` `environment_report`（rank 91）、`chief_advisor_persona`（rank 93） |
| 验证轮 | strict 只读轮重建 system 时从 `ctx.metadata["_system_prompt_extras"]` / `_gw_inject_stored` 继承 |
| 槽位追问 | `slot_clarification_llm.template_clarification_reply` 与 LLM 成功路径均包【情报汇整】+【行动预案】 |
| task_plan / planning_composite | `pushback_copy.task_plan_gate_user_message` / `planning_composite_gate_user_message` |

关闭参谋长 **仅影响 prompt 注入**：`chief_advisor_prompt_enabled: false` 时不再注入 `[ENVIRONMENT_REPORT]` 块与人设段，嗅探数据仍可留在 `bundle.extra` 供观测。

---

## nexus 配置（`~/.jachin/nexus_config.json` → `intent_gateway`）

| 键 | 默认 | 说明 |
|----|------|------|
| `context_sniffer_enabled` | `true` | 是否执行环境嗅探 |
| `context_sniffer_max_total_chars` | `1500` | 报告总字符上限（256–16000 内钳制） |
| `context_sniffer_max_git_chars` | `500` | Git 段上限（不超过总上限） |
| `context_sniffer_tracker_enabled` | `true` | `context_sniffer_complete` / `skipped` / `error` 写入 `intent_tracker.jsonl` |
| `chief_advisor_prompt_enabled` | `true` | 参谋长 + 环境报告注入 system |
| `dag_topology_tracker_enabled` | `true` | DAG 校验后写入 `dag_topology_validated` |

示例：

```json
{
  "intent_gateway": {
    "context_sniffer_enabled": true,
    "context_sniffer_max_total_chars": 1200,
    "context_sniffer_max_git_chars": 400,
    "context_sniffer_tracker_enabled": true,
    "chief_advisor_prompt_enabled": true,
    "dag_topology_tracker_enabled": true
  }
}
```

---

## intent_tracker 审计事件

| `kind` | 载荷要点 |
|--------|-----------|
| `context_sniffer_complete` | `correlation_id`, `run_id`, `truncated`, `total_chars`, `workspace_dir_tail` |
| `context_sniffer_skipped` | `correlation_id`, `run_id`, `reason: disabled` |
| `context_sniffer_error` | `correlation_id`, `run_id`, `error` |
| `dag_topology_validated` | `run_id`, `ok`, `node_count`, `detail_head` |

与 `correlation_id`（网关 bundle）/ `run_id`（本轮 `run_agent`）对齐检索：`~/.jachin/data/intent_tracker.jsonl`。

---

## 配置与运维（摘要）

- 嗅探失败：`environment_report.ok=false` 且带 `error` 短串，不阻塞主流程；可选 `context_sniffer_error` tracker 行。
- `git` 不在 PATH 或目录非仓库：Git 段为空，`git.ok=false`。
- `intent_tracker_jsonl_enabled: false` 时 tracker 模块整体不写盘（与既有行为一致）。

---

## 相关文件索引

| 路径 | 职责 |
|------|------|
| `l3_node/intent_gateway/context_sniffer.py` | 嗅探 + `format_environment_report_for_prompt` |
| `l3_node/intent_gateway/config.py` | 默认键与 nexus 合并 |
| `l3_node/intent_gateway/gateway_pipeline.py` | `async` 入站流水线 + 配置 + tracker |
| `l3_node/intent_gateway/topology.py` | `validate_subintent_dag` + tracker |
| `l3_node/intent_gateway/pushback_copy.py` | 参谋长 system 块与门闸文案 |
| `l3_node/agent_core.py` | `gateway_workspace_dir` / `implicit_attribution` 解析、`run_agent` |
| `l3_node/http_server.py` | HTTP `agent/run` 体字段 `gateway_workspace_dir` / `git_workspace_dir` → `run_agent` |
| `l3_node/intent_gateway/slot_clarification_llm.py` | 槽位追问模板与 LLM 输出包装 |
| `clients/desktop/src/hooks/useSensoryWebSocket.ts` | `system_status` 解析与展示 |
| `tests/unit/test_context_sniffer.py` | 预算与格式化单测 |
| `tests/unit/test_gateway_pipeline_sniffer.py` | 嗅探开关与 tracker 钩子（若存在） |
