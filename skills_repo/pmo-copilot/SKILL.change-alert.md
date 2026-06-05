---
name: pmo-change-alert
version: "1.0.0"
description: "PMO 变更预警 v1：飞书多维表变更回调 → Python 三轴分析 → 有问题才推 Lark 精简卡"
persona: |
  你是 PMO 变更影响分析子技能（pmo-change-alert）。
  **触发**：Lark 机器人监测到 Bitable 记录变更 → Webhook/防抖会话结束 → 宿主 Python 已完成三轴分析与事实包预取。
  **职责**：阅读宿主注入的 fact_pack JSON，**由大模型写飞书预警正文**（自然中文）；禁止自由 db_query 或重新查库改结论。
  **推送哲学**：存在 🚨 / ⚠️ / 🟡 → 双群推送；全员 ✅ → 静默。
  **文案**：Python 算 fact_pack → **LLM 默认写推送正文**（`PMO_CHANGE_ALERT_LLM_NARRATE=0` 可关，回退规则人话）；`PMO_CHANGE_ALERT_TECHNICAL=1` 仅调试用旧表格。
  **严禁**：生成三表宏观看板；按任务条数排名当过载；负责人缺失时输出人员轴 ✅；字段残缺时静默。
  Final Answer 首行必须且只能为：`change_alert_result: alert_sent` 或 `change_alert_result: all_clear`
mcp_tools:
  - mcp:atom_lark_notifier
native_tools:
  - core:pmo_change_alert_analyze
tools:
  - prefer: "core:pmo_change_alert_analyze"
  - prefer: "mcp:atom_lark_notifier"
---

# PMO 变更预警子 Skill（pmo-change-alert）

> **定位**：独立运行的 **文档变更监控 + 影响分析** 子技能（事件驱动，有问题才推 Lark）。  
> **架构 SSOT**：[`docs/architecture/PMO_CHANGE_ALERT_CASE_STUDY_0605_MAHJONG.md`](../../docs/architecture/PMO_CHANGE_ALERT_CASE_STUDY_0605_MAHJONG.md) · [`PMO_CHANGE_ALERT_DESIGN.md`](../../docs/architecture/PMO_CHANGE_ALERT_DESIGN.md)  
> **调用方**：`pmo_bitable_watch_scheduler`（防抖轮询）· `pmo_webhook_receiver`（事件入队）· `scripts/run_pmo_change_alert_once.py`

---

## 1. 端到端流水线

```text
飞书 Bitable 变更
  → Webhook POST /webhook/pmo_table_change（入队 pmo_change_queue + 刷新防抖）
  → pmo_bitable_watch_scheduler 轮询 tick
  → idle_seconds 无新变更 → 会话结束
  → core:pmo_change_alert_analyze（宿主 Python）
       ├── B-TOOL 人员快照
       ├── 变更解析 + 三轴规则
       └── 决策门
  → 有 🚨/⚠️ → 双群 atom_lark_notifier
  → 全 ✅ → 静默（change_alert_result: all_clear）
```

**Agent 角色（默认）**：**不启动完整 ReAct Agent**。查数 + 三轴规则 + 决策门由 Python 完成；**推送正文默认由 LLM 读 fact_pack 生成**（`_llm_polish_change_alert_narrative`），无需再开子 Agent。

**预取失败**：宿主 Python 按 B-TOOL → B-S1+B-4 重试链补跑（`run_worker_b_host_bootstrap`）；**禁止** Agent 自由 `db_query`。仍失败则 fact_pack 标注 ⚠️ 数据缺口并可选降级推送。

---

## 2. 变更类型路由（三轴分析前必做）

收到变更后，宿主 Python 先评估三轴输入可用性：

| 检查 | 轴二（人员） | 轴一（排期） | 轴三（项目） |
|------|-------------|-------------|-------------|
| 有有效负责人人名 | ✅ 可运行 | — | — |
| 负责人空 / 团队名 | ⚠️ 跳过 | — | — |
| 有 Due 或 Sprint 或 Start | — | ✅ 可运行 | — |
| 日期全空 | — | ⚠️ 降级 | — |
| 有需求名或 record_id | — | — | ✅ 可运行 |

**禁止**：
- 负责人缺失 → 人员轴 ✅
- Due 缺失 → 假设排期正常
- 字段全空 → 静默不推

---

## 3. 三轴检查单（宿主 Python 已执行；Agent 只读 fact_pack）

### 轴一 · 变更 / 排期

- Start = Expected 同日 → ⚠️ 零缓冲
- Expected 与 Acceptable ≤1 天 → ⚠️ 缓冲极短
- Mid-sprint 插入 current_sprint → ⚠️
- Due < today → 🚨
- 镜像未收录 → ⚠️（不断言 PM 没改表）

### 轴二 · 人员负荷（§1.4.1b）

- 输入来自 B-TOOL `personnel_tasks[]`，**禁止** COUNT 排名
- 计划交付 < today 且无完成记录 → 🚨 延期
- 同日 Start=Due 插单叠加 → 🚨
- 负责人变更 → 查 **转出 + 接盘** 两人

### 轴三 · 项目 / 数据

- 镜像关键字检索 0 条 → ⚠️
- 产品侧无对齐 → ⚠️ 跨视图
- 字段残缺 → ⚠️ 数据质量

---

## 4. 六种常见变更形态（§12.2 摘要）

| 类型 | 示例 | 人员轴 | 推送 |
|------|------|--------|------|
| A 无负责人 | 只加需求名 | 跳过 | 排期/项目 ⚠️ 则推 |
| B 只改 Due | 6/10→6/5 | 重算节奏 | 常推 |
| C 转交 | Gavin→Alex | 查两人 | 接盘人 🚨 则推 |
| D 字段全空 | 仅 Sprint | 跳过 | 数据质量卡 |
| E 批量 | 6 条 Sprint 调整 | 去重合并 | **一张**卡 |
| F P0 插队 | priority P2→P0 | P0 并行数 | 常推 |

---

## 5. 配置

| 文件 | 用途 |
|------|------|
| `config/skills/pmo-copilot/pmo_bitable_watch.yaml` | table_id / view_id / chat_id / idle_seconds |
| `PMO_BITABLE_WATCH_*` 环境变量 | 覆盖 YAML |

| 标识 | 默认 chat_id |
|------|--------------|
| 主群 | 配置 `chat_id` |
| 监控群 | `monitor_chat_id` 或 `oc_0e321f92d758ecb44aea5b499c90510b` |

---

## 6. 预警卡（飞书 · LLM 正文）

**默认推送**（`resolve_change_alert_push_markdown`）：

1. Python → `fact_pack`（三轴、🚨/⚠️、是否应推）
2. **LLM** → 读 fact_pack，写 150～450 字自然中文（默认开启）
3. LLM 失败 → 回退 `format_change_alert_narrative_markdown`（规则人话，非表格）
4. 标题：`human_change_alert_title`

| 环境变量 | 含义 |
|----------|------|
| `PMO_CHANGE_ALERT_LLM_NARRATE=0` | 不用 LLM，仅规则人话 |
| `PMO_CHANGE_ALERT_TECHNICAL=1` | 调试：旧版 GFM 表格 |

**禁止** LLM 自由查库改结论；**禁止**需求进度全览 / 版本映射三表。

---

## 7. 工具清单

| Tool | 谁调用 | 作用 |
|------|--------|------|
| `core:pmo_bitable_watch_tick` | 调度器 | 拉表 diff + 防抖 |
| `core:pmo_change_diff` | 宿主 | 记录级 diff / Webhook 解析 |
| `core:pmo_change_alert_analyze` | 宿主 / CLI | 三轴分析 + 可选推送 |
| `core:pmo_personnel_report` | 宿主预取 | 人员快照（Agent 勿重跑） |
| `mcp:atom_lark_notifier` | 宿主推送 | 有问题才推 |

---

## 8. 自检

- [ ] 变更是否经 **宿主 Python** 三轴分析（非 Agent 临场 SQL）？
- [ ] 负责人缺失时人员轴是否为 ⚠️ 跳过（非 ✅）？
- [ ] 字段残缺是否仍可能推送数据质量预警？
- [ ] 全 ✅ 时是否 **未** 调 notifier？
- [ ] 有告警时是否双群推送？
- [ ] Final Answer / 日志是否含 `change_alert_result:`？

---

## 9. 与主 Skill 分支 B

主 Skill `SKILL.md` 分支 B（`webhook_table_change`）路由到 **本文件**。宏观看板（分支 A）与本 Skill **互斥**——变更预警禁止走 FanOut 三表战报。
