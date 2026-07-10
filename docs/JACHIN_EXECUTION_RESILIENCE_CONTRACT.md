# Jachin 执行韧性契约（全 Skill / 全任务）

**版本**: 1.0
**状态**: 系统设计规范（与实现渐进对齐）
**关联**: [07_memory_first_main_agent_and_voice_app_agents.md](./07_memory_first_main_agent_and_voice_app_agents.md)、Cursor 规则 `.cursor/rules/080-jachin-execution-resilience.mdc`

---

## 一、目标与适用范围

### 1.1 目标

在 **不牺牲可恢复性** 的前提下，控制 **Token、时间与副作用**，使 Jachin 在真实业务（招聘、BI、自动化、Agent 对话触发的长任务）中具备：

1. **韧性**：单点失败不默认拖死整条链路。
2. **策略链**：有限次重试后 **自动换方案**（降级、跳过、替代工具），禁止同参死循环。
3. **有界退出**：多策略仍失败时产出 **结构化尝试简报**，再 **显式结束**，避免无限 WorkOrder 烧 Token。
4. **部分成功**：子项失败时 **记录可观测说明**，**主流程继续** 直至本轮业务目标完成（或达到退出条件）。

### 1.2 适用范围

| 类型 | 说明 |
|------|------|
| **JPP / Wasm Skill** | `l3_node/primitives`（tools/skills）、`skills_repo` 下技能宿主与插件 |
| **MCP 工具** | Python 原子工具、长流程 orchestrator |
| **定时 / DAG / 调度** | 如 `recruitment_scheduler`、`hr_recruitment_dag`、APScheduler、YAML 工作流 |
| **Agent 工具调用** | `agent_core`、IM `dispatcher`、WS/HTTP 触发的 `run_tool` / 领域工作流 |
| **Core 工作流引擎** | `core` 侧持久化工作流、`domain_ref`、`on_failure` / `retry` / `resume` |

**非目标**：替代具体产品的 SLA 数值；各域可在本契约下定义 **域级默认参数**（见 §8）。

---

## 二、四条设计原则（规范表述）

### 原则 A — 韧性（Resilience）

- **定义**：外部依赖（LLM、Wasm、浏览器、第三方 API、磁盘）失败时，系统应 **降级、重试或分段提交** 已产生价值，而非默认全量回滚到「零产出」。
- **要求**：长任务必须具备 **检查点**（状态落盘、已写入产物、已同步事件），使 **后续 tick 或人工** 可续跑。

### 原则 B — 策略链（Retry → Alternate）

- **定义**：对同一 **逻辑步骤**（如「透析一批 PDF」）先采用默认策略；在 **达到 `max_attempts` 或命中特定错误签名** 后，切换到 **下一策略**（如批量 Wasm → 逐份 Wasm → 仅跳过坏文件）。
- **要求**：策略切换必须 **可配置且有上限**；禁止无上限同一策略重试。

### 原则 C — 有界执行与尝试简报（Budgeted exit + ExecutionBrief）

- **定义**：在 **重试次数、策略种类、LLM 轮次或 Token 软预算** 任一触顶后，若仍未达成硬成功条件，必须生成 **ExecutionBrief**（见 §5），并 **停止** 对该目标的自动扩张（不再静默重复调用同工具同参）。
- **要求**：简报须可被机器聚合（JSON）与人读（短摘要），并写入日志；可选同步 IM / 多维表 / 本地 `result/` 文件。

### 原则 D — 部分成功与主流程继续（Partial success）

- **定义**：批量处理中 **子项失败**（单文件、单会话、单行记录）不得默认导致 **整批失败**；应在 **RunReport**（见 §5）中记录失败子项与原因，**其余子项继续**；主流程（排行榜、通知、下一调度 tick）在达到业务定义的「可交付」条件时 **继续完成**。
- **要求**：用户可见结论需区分 **「全部成功」** 与 **「部分成功 + 异常附录」**。

---

## 三、错误分类（Error taxonomy）

实现侧应能将失败映射到以下 **类别之一**（可用枚举、错误码或约定字符串前缀），供编排与 Agent 决策：

| 类别 | 含义 | 典型动作 |
|------|------|----------|
| `transient` | 超时、网络抖动、429、可恢复 trap | 退避重试 → 换端点/模型 → 再失败则升级 |
| `resource` | 内存 OOB、磁盘满、进程锁 | 减小批量 / 逐份 / 延迟重试 |
| `per_item` | 单文件损坏、单 URL 403、单记录校验失败 | 跳过该项并记录，继续批次 |
| `config` | 缺凭证、错误 table_id、权限不足 | 不重试同参；简报提示配置 |
| `permanent` | 逻辑错误、不变式违反 | 停止该分支；简报；不无限重试 |

**Wasm / 宿主**：`loader`、`wasm_runner` 应对常见失败打标（如 `linear_memory_oob` → `resource`），避免一律合并为模糊字符串。

---

## 四、重试与策略链（规范）

### 4.1 参数（域可覆写默认值）

| 参数 | 说明 |
|------|------|
| `max_attempts_per_strategy` | 同一策略下同一步骤最多尝试次数 |
| `max_strategies` | 同一步骤最多切换策略数 |
| `backoff_ms` / `backoff_multiplier` |  transient 退避 |
| `max_llm_rounds` / `token_budget_soft` | Agent 侧有界（软预算即可） |
| `give_up_after_brief` | 产出 ExecutionBrief 后是否禁止自动再启（默认 true） |

### 4.2 策略链示例（招聘透析，示意）

1. 默认：**逐份 Wasm**（低内存压力）。
2. 若显式开启批量：`batch_wasm` → 遇 `resource`/`oob` → **自动降级** 为逐份，无需用户改环境变量（推荐实现方向）。
3. 单份连续 `permanent`：跳过该 PDF，记入 `failed_items`。

### 4.3 与 YAML 工作流对齐

若步骤由 **YAML 工作流** 描述，应在节点级支持：`retry`、`on_failure` 跳转、`timeout_seconds`，并与本契约的 **策略链** 一致，避免调度器与 core 工作流 **两套语义冲突**。

---

## 五、结构化产物契约

### 5.1 RunReport（批量 / 长任务推荐）

工具或调度在 **一轮业务执行** 结束时宜返回或落盘：

```json
{
  "run_id": "可选",
  "status": "success | partial_success | failed",
  "ok_count": 0,
  "failed_items": [
    {"id": "文件或业务键", "stage": "download|read|wasm|llm|persist|notify", "error_class": "transient|resource|per_item|config|permanent", "message": "短说明"}
  ],
  "degraded": false,
  "fallback_used": null,
  "artifacts": ["路径或 URL 列表"]
}
```

- **HTTP/MCP**：可放在 JSON 响应体；**调度器**：可写 `result/_run_report.json` 或合并入现有 Summary。
- **原则 D**：`partial_success` 时 **仍允许** 写琅琊榜、发「部分完成」类通知，并附带失败摘要。

### 5.2 ExecutionBrief（有界退出必填）

当 **原则 C** 触发时产出：

```json
{
  "goal": "本轮业务目标简述",
  "outcome": "aborted_after_strategies | budget_exceeded | unrecoverable",
  "strategies_tried": [
    {"name": "策略名", "attempts": 2, "last_error_class": "resource", "last_message": "…"}
  ],
  "partial_artifacts": [],
  "recommended_human_action": "可执行的一条建议",
  "token_or_round_hint": "可选，统计用"
}
```

- 必须 **记录日志**（`INFO`/`WARNING` 级别，带统一前缀如 `[ExecutionBrief]`）。
- Agent 在收到 Brief 后 **不得** 在无新输入时无限重复同一失败工具调用。

---

## 六、分层职责

### 6.1 Skill / 工具实现者

- 区分 **整批失败** 与 **per_item**；尽量返回结构化信息或可被解析的约定格式。
- **禁止** 静默吞掉异常导致编排误判为成功。
- 有副作用的操作应 **幂等** 或可检测重复（避免重试双写）。

### 6.2 编排 / 调度

- 持有 **互斥锁** 时仍应允许 **同锁内多轮续跑**（补缺口），但 **禁止** 无进展死循环（需「无进展检测」）。
- **不在** 已部分落盘时 **raise** 导致整次 job 被上层 `except` 截断（除非安全关键）。
- 将 `hr_analyze_continue` 类状态与 **RunReport** 对齐，便于观测。

### 6.3 Agent / LLM 层

- 工具结果应优先 **JSON 或可解析结构**；若仅有自然语言，宿主应提取 **status / error_class**。
- 对连续相同 `error_code` 的调用应注入 **系统约束**：切换策略或生成 Brief。
- 复用 `LiteLLMEngine` 等多模型降级时，与 **步骤级策略链** 分工清晰：**模型降级** 不等于 **业务步骤无限重试**。

### 6.4 宿主（HTTP / WS / IM）

- 长任务返回 **202 + 轮询** 或 **明确 correlation id**（若已有）；错误响应区分 **4xx 配置** 与 **5xx 瞬时**。
- 不向用户暴露内部堆栈；**Brief** 给人读摘要即可。

---

## 七、日志与可观测性

- 策略切换：**统一日志标签**，例如 `[StrategyShift] step=X from=A to=B reason=…`。
- 续跑轮次：**`[透析轮次]`** 等域内标签可保留，建议逐步统一为 **`[RunRound]`** 前缀 + `domain=hr`。
- 与现有 **MACHINE_CHECKPOINT / 审计** 兼容：每次策略切换或 Brief 可追加检查点记录（若项目已启用）。

---

## 八、域级配置与采纳路径

| 域 | 建议默认 |
|----|----------|
| HR 招聘 | 透析默认逐份；批量仅作可选；多轮补缺口；Lark 通知与「全部完成」语义对齐契约 |
| BI | CSV/表同步 per 表错误聚合；Brief 汇总失败表 |
| 通用 Agent | `max_llm_rounds` + 连续同错熔断 + Brief |

**采纳顺序**：新 Skill **从一开始遵守**；旧插件 **在修改触达时** 渐进补齐 RunReport / 错误分类 / 不 raise 截断续跑。

---

## 九、合规检查清单（PR / Code Review）

- [ ] 批量操作是否可能因单点失败拖死全批？是否有个别跳过与 RunReport？
- [ ] 重试是否有 **上限** 与 **退避**？
- [ ] 失败 N 次后是否有 **下一策略** 或 Brief？
- [ ] 部分成功后主流程是否仍 **可完成** 且用户可见 **异常说明**？
- [ ] 是否在部分成功路径误发「全部成功」文案？

---

## 十、修订记录

| 日期 | 说明 |
|------|------|
| 2026-03-24 | 初版：四条原则、错误分类、RunReport/ExecutionBrief、分层职责 |
| 2026-03-24 | 落地（渐进）：`l3_node/execution_resilience.py`（`classify_wasm_error_message`、`build_run_report`、`write_run_report_json`、`log_execution_brief`）；招聘调度 `recruitment_scheduler` — 批量 Wasm 后对仍缺 `*_analysis.md` 的 PDF **自动逐份补齐**（日志 `[StrategyShift]`）、逐份路径记录 `failed_items`、每锁会话在 `data/{职位}/result/_run_report.json` 写入 RunReport；无产出与无进展停滞时打 `[ExecutionBrief]` |

---

## 十一、实现索引（便于审计）

| 组件 | 路径 | 说明 |
|------|------|------|
| 共享工具 | `l3_node/execution_resilience.py` | 错误分类、RunReport 构建与落盘、Brief 日志 |
| HR 透析调度 | `skills_repo/plugin/com.jachin.hr.recruitment/recruitment_scheduler.py` | 策略链：batch→sequential；`failed_items`；`_run_report.json` |
| Cursor 规则 | `.cursor/rules/080-jachin-execution-resilience.mdc` | 修改相关代码时的强制约束 |
