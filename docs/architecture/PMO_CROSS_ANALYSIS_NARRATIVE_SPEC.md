# PMO 整体交叉分析 · LLM 人话解读方案

> **这篇文档给谁看？**  
> 产品经理、PMO、Agent / 后端工程师——想搞懂「怎样对整个 PMO 项目做**有价值的交叉分析**」，且**正文由 LLM 写人话**、而不是 Python 硬编码模板或数据堆砌，读这一份就够。  
> **版本**：草案 2026-06-08 · 对齐 PMO-Copilot Skill `7.2.x` · v7 镜像库架构。  
> **关联 SSOT**：[`PMO_COPILOT_ARCHITECTURE.md`](./PMO_COPILOT_ARCHITECTURE.md) · [`PMO_CHANGE_ALERT_DESIGN.md`](./PMO_CHANGE_ALERT_DESIGN.md) · `skills_repo/pmo-copilot/SKILL.md` §1.2.1 · `SKILL.change-alert.md`

---

## 1. 一句话：要解决什么问题？

PMO 数据分散在飞书 **12 个多维表视图**（产品、开发 Epic、人员看板、美术、甘特等）。单看任意一张表都能得出「局部正确」的结论，但 **只有交叉比对** 才能发现：

- 表填了、人没认领（幽灵任务）
- 人在干活、Epic 没挂上（有头无脚）
- 产品说待上线、开发还在验收（对外承诺风险）
- 有人已收尾、关键路径仍有人掉队（负载与路径未对齐）

**本方案定义**：从哪几个维度分析、维度之间如何交叉、**Python 负责什么、LLM 负责什么**、最终如何以 **人话简报** 推送到 Lark。

**与现有能力的关系**：

| 能力 | 产出形态 | 与本方案关系 |
|------|----------|--------------|
| 分支 A · 宏观看板 | 📊👥📦 三表 GFM 战报 | **互补**：三表偏「快照看数」；本方案偏「诊断 + 建议」 |
| 分支 B · 变更预警 | 事件驱动精简卡 | **互补**：变更预警看「这次改动」；本方案看「整个 Sprint / 项目健康度」 |
| resource-monitor | 定时人员巡检 | **复用** §1.4.1b 节奏判定，不重复造轮子 |
| Auditor | B/C JSON 规则审计 | **复用** 跨视图矛盾检测，结果写入 fact_pack |

---

## 2. 设计哲学：查对与解读必须分离

这是本方案的 **硬性边界**，与 [`PMO_COPILOT_ARCHITECTURE.md`](./PMO_COPILOT_ARCHITECTURE.md) §2 一致，并针对「整体健康度分析」做强化说明。

### 2.1 两层分工

```text
┌─────────────────────────────────────────────────────────────┐
│  Layer 1 · 事实层（Python / 宿主预取 / Auditor 规则）        │
│  ─ 查对：Sprint 窗、Epic 层级、人员矩阵、填写率、矛盾清单      │
│  ─ 输出：fact_pack JSON（结构化、可单测、无修辞）              │
│  ─ 禁止：写「发现一」「建议 PM」等段落模板                     │
└───────────────────────────┬─────────────────────────────────┘
                            │ fact_pack 注入 Prompt
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2 · 解读层（LLM）                                     │
│  ─ 读 fact_pack，写人话：因果链、优先级、可执行建议            │
│  ─ 允许：归纳、类比、点名大需求/同学、判断「要不要 PM 介入」   │
│  ─ 禁止：无 Observation 捏造人名/数字；自由 db_query 改结论    │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
                    Lark 交互卡片（工整排版 · 非数据堆砌）
```

### 2.2 为什么要 LLM 写人话，而不是 Python 硬编码？

| 方式 | 问题 |
|------|------|
| Python `if count > 98: return "建议补负责人"` | 文案固定、无法把 **多维度交叉** 合成一句有因果的判断 |
| 直接 dump SQL / JSON 到 Lark | PM 看不懂，也没有「So what」 |
| LLM 从零查库写报告 | 字段踩坑、Person 双形态、Epic 筛错表——不稳定 |
| **fact_pack + LLM 解读** | 数字与规则可信；叙述灵活；可随 Sprint 语境换说法 |

**反模式（明确禁止作为正式路径）**：

- 脚本里 `build_narrative()` / `sections.append("发现一：…")` 写死段落（仅允许作 **离线调试**，不得进 Skill SSOT）
- `PMO_CHANGE_ALERT_LLM_NARRATE=0` 式的纯规则模板作为主交付（仅作 LLM 不可用时的降级）
- 把宏观看板三表原样复制当「交叉分析」（缺解读层）

**正模式（本方案 SSOT）**：

- 宿主调用 `core:pmo_personnel_report` + `core:pmo_sprint_epic_report` + Auditor → 组装 `fact_pack`
- LLM 读 `fact_pack` + Skill 中的 **解读 SOP** → 输出 narrative
- Publisher / 专用子 Skill 负责 Lark 卡片排版（Python 只做结构，不做业务措辞）

---

## 3. 建议的分析维度（六轴 + 交叉）

整体健康度不建议「单表单维度扫一遍」，而应按 **六条分析轴** 采集事实，再在 **轴与轴的交点** 上找洞察。

### 3.1 六轴定义

| 轴 | 中文名 | 核心问题 | 主要数据源（source_view） | Python 预取 / 规则 |
|----|--------|----------|---------------------------|-------------------|
| **A** | 数据完整性 | 关键字段有没有填？填了能不能支撑决策？ | `vew8TxMcSh`、`vewpI8lyYw`、`vewCz1FFJi` | Version Goal 填写率；无负责人计数；无 Due 计数 |
| **B** | 需求层级与拆解 | 大需求有没有拆成可执行任务？父子关系对不对？ | `vewpI8lyYw`（Epic + 子任务） | C-TOOL epics / epic_children；orphan Epic 检测 |
| **C** | 排期与 Sprint | 任务是否落在合理 Sprint？有无跨周、插单、延期链？ | `vewpI8lyYw`、`vewCz1FFJi`、`vew4Im7GO3` | current_sprint；cross_week_tasks；延期状态 |
| **D** | 跨视图语义一致 | 产品、开发、人员三表对同一需求说法是否矛盾？ | `vew8TxMcSh` × `vewpI8lyYw` × `vewCz1FFJi` | Auditor；Step 6a/6b；需求状态 ≠ 开发状态 |
| **E** | 人员节奏与负载 | 谁在掉队？谁已收尾？关键路径是否对齐？ | **`vewCz1FFJi`（SSOT）** | B-TOOL；§1.4.1b 节奏判定；**禁止** COUNT 排名当过载 |
| **F** | 版本与发布对齐 | 需求能否映射到目标版本？发版窗口是否可信？ | `vew8TxMcSh`、`vewL9Mofgd` + 发版邮件窗 | Version Goal；`core:pmo_release_epic_mapping`（可选） |

**轴 A～F 的采集** 应优先走已有 Tool（B-TOOL / C-TOOL / Auditor），而不是让 LLM 每轮手写 SQL。

### 3.2 交叉分析：有价值的交点（LLM 重点发挥处）

单轴只能回答「有没有问题」；**交叉**才能回答「问题严重吗、先修什么、为什么 PM 和研发说法不一致」。

| 交叉 | 若同时出现… | 典型洞察（LLM 应写成的「人话」） |
|------|-------------|----------------------------------|
| **A × E** | 大量无负责人 + 人员矩阵有人 🚨 | 不是没人做，是 **开发表没维护 Owner**；Epic 汇总虚高 |
| **B × E** | orphan Epic 很多 + 人员看板该主题任务很多 | **活在做、表没挂上**；父记录维护问题，不是需求悬空 |
| **B × C** | Epic 无子任务 + 本 Sprint 已过半 | 排期 **看起来满、实际不可执行**；需 Epic Owner 拆任务 |
| **D × C** | 产品「待上线」+ 开发「验收中」+ Due 临近 | **对外承诺风险**；发布窗口可能被动推迟 |
| **D × F** | Version Goal 全空 + 产品状态「已发布」 | 版本映射 **无法自动化**；发版复盘只能靠人工 |
| **C × E** | 同一大需求：一人自测中、另一人进度 0% | **关键路径未对齐**；不是人不够，是依赖或状态未更新 |
| **A × B** | 无负责人行集中在某 Epic 下 | 该 Epic 缺 **唯一 Owner**；建议 PM 指定后再拆子任务 |

LLM 的职责：从 fact_pack 里的 **flags + 样例行** 中，选出 3～5 条 **最有 PM 行动价值** 的交叉洞察，用因果句写出来；其余可一句带过或省略。

### 3.3 与 Skill §1.2.1「七步框架」的映射

| 七步 | 主要覆盖轴 |
|------|------------|
| Step 1 地图 | 全轴前置 |
| Step 2 样本 + Sprint | C |
| Step 3 人员矩阵 | E |
| Step 4 Epic | B |
| Step 5 状态×Sprint | C |
| Step 6 跨视图检验 | D |
| Step 7 Version Goal | A、F |

**整体交叉分析** = 七步事实采集 + **Auditor** + **LLM 解读层**（本方案新增，不替代七步）。

---

## 4. 端到端流水线（推荐）

```text
触发（手动 / 定时 / 飞书指令「PMO 整体分析」）
  │
  ├─ INIT（若库空或过期）→ mirror_import
  │
  ├─ Layer 1 · 事实预取（Python，零 LLM 或极少 LLM）
  │     ├─ core:pmo_personnel_report   → personnel_tasks, by_person, rhythm_flags
  │     ├─ core:pmo_sprint_epic_report → epics, epic_children, current_sprint
  │     ├─ Auditor（可选）             → cross_view_conflicts[]
  │     └─ health_probes（待实现）     → completeness, orphan_epics, status_mismatch_samples
  │
  ├─ 组装 fact_pack JSON（宿主）
  │
  ├─ Layer 2 · LLM 解读（ReAct 1 轮或 Publisher 专轮）
  │     ├─ 输入：fact_pack + 本 Skill §5 解读 SOP
  │     ├─ 输出：narrative_sections[]（纯文本，无 ## / **）
  │     └─ 禁止：db_query 改事实；捏造未在 pack 中的人名/数字
  │
  └─ Lark 推送
        ├─ Python：交互卡片结构（header / note / hr / plain_text）
        └─ 正文：LLM 生成的 narrative_sections
```

**轮次预算建议**：

- 事实层：固定 Tool 调用，**不占用** ReAct 分析轮次（与 FanOut 宿主预取同构）
- 解读层：**1～2 轮** LLM（读 pack → 写 narrative；必要时 1 轮自检「是否数据堆砌」）
- 推送：1 次 `atom_lark_notifier`（主群；是否双群见 §7）

---

## 5. fact_pack 约定（Python 输出 · LLM 输入）

fact_pack 是 **Layer 1 的唯一交付物**。字段应 **稳定、可版本化、可单测**；**不得**包含写好的「发现一、发现二」段落。

### 5.1 建议 Schema（v0.1 草案）

```json
{
  "meta": {
    "analysis_type": "pmo_cross_health",
    "current_sprint": "2026/06/01-Sprint",
    "current_sprint_date": "2026-06-01",
    "synced_at": "2026-06-05T07:28:08+00:00",
    "recent_sprints": ["2026/06/01-Sprint", "2026/05/25-Sprint"]
  },
  "axis_A_completeness": {
    "version_goal_fill_rate": { "vew8TxMcSh": { "filled": 0, "total": 97 } },
    "no_owner_count_dev_sprint": 98,
    "no_due_date_count_personnel_sprint": 2,
    "unassigned_cross_view_count": 30
  },
  "axis_B_hierarchy": {
    "epic_count_current_sprint": 11,
    "orphan_epic_names": ["在线奖励-…", "…"],
    "orphan_epic_count": 8,
    "sample_epic_progress": [
      { "name": "FB外跳", "priority": "P0", "pct": 51, "child_count": 5 }
    ]
  },
  "axis_C_schedule": {
    "status_distribution": { "🔵 按时完成": 40, "（空）": 48 },
    "cross_week_task_count": 3,
    "overdue_personnel_samples": [
      { "person": "Gavin", "task": "FB外跳-程序开发", "status": "…" }
    ]
  },
  "axis_D_cross_view": {
    "status_mismatch_count": 10,
    "status_mismatch_samples": [
      { "requirement": "…", "demand_status": "待上线", "dev_status": "验收中" }
    ],
    "auditor_conflicts": []
  },
  "axis_E_personnel": {
    "rhythm_alerts": [
      { "person": "Kelden", "level": "red", "summary": "进度落后 57%", "tasks": ["…"] }
    ],
    "rhythm_idle": [
      { "person": "Patrick", "level": "yellow", "summary": "偏闲", "task_count": 8 }
    ],
    "rhythm_ok_count": 4
  },
  "axis_F_release": {
    "version_goal_usable": false,
    "release_mapping_available": false
  },
  "cross_flags": [
    "orphan_epic_high_but_personnel_busy:在线奖励",
    "no_owner_high_with_rhythm_red",
    "status_mismatch_with_near_due"
  ],
  "data_gaps": [
    { "field": "version_goal", "severity": "high", "note": "产品表 0% 填写" }
  ]
}
```

### 5.2 cross_flags 的作用

`cross_flags` 由 **Python 规则** 打标（非 LLM 临场发明），供 LLM **优先展开**：

| flag | 触发条件（示例） |
|------|------------------|
| `orphan_epic_high_but_personnel_busy:{theme}` | 某主题 orphan Epic ≥ N 且 personnel 同主题任务 ≥ M |
| `no_owner_high_with_rhythm_red` | 无负责人行 > 阈值 且 rhythm_alerts 非空 |
| `status_mismatch_with_near_due` | 状态不一致样本中存在 Due ≤ 7 天 |
| `version_goal_empty_blocks_release` | Version Goal 填写率 < 10% 且存在「已发布/待上线」产品行 |
| `key_path_split` | 同 Epic 下多人进度差异极大（一人 100%、一人 0%） |

新增 flag 须 **单测 + 文档一行**，避免 LLM 侧「感觉有问题」却无事实锚点。

---

## 6. LLM 解读 SOP（人话层 SSOT）

以下应写入 **Skill 正文**（如 `SKILL.cross-health.md` 或 `SKILL.md` 新分支 D），作为 LLM 的强制写作规范。

### 6.1 写作目标

| 要做 | 不要做 |
|------|--------|
| 用 PM 能直接转述给研发的话 | 贴 SQL、贴 JSON、贴 50 行任务列表 |
| 写清 **因为 A，所以 B，建议 C** | 只列「98 条无负责人」不解释影响 |
| 3～5 条 **交叉洞察** + 3～5 条 **可执行建议** | 六个维度各写一段流水账 |
| 一切正常时明确写「整体正常，仅需维持」 | 没问题时硬凑「发现」 |
| 数据缺口时诚实标注，不静默 | 用「数据质量差」跳过推送 |

### 6.2 推荐结构（Lark 卡片分段）

每段 **标题 + 正文** 分开；**禁止**在正文使用 Markdown 标题符号（`##`）和加粗符号（`**`）。标题由卡片 `note` 或 `▎标题` 样式承担。

1. **一句话结论**（🟢 / 🟡 / 🟢+⚠️）  
2. **最值得 PM 知道的 3 件事**（每条 2～4 句，必须是交叉洞察）  
3. **人员与关键路径**（只点名 fact_pack 里 rhythm_alerts / key_path 相关人）  
4. **表格与流程**（Version Goal、orphan Epic、状态不一致——合并为一段，避免报表感）  
5. **本周建议优先做的 N 件事**（按 ROI 排序，带预估时间）  
6. **数据说明**（一行：快照时间 + 若有 gap）

### 6.3 LLM Prompt 要点（注入 fact_pack 时）

```text
你是 PMO 协作者。下方 fact_pack 是 Python 已算好的唯一事实源。
请写「项目健康度」中文简报：
- 只使用 pack 中出现的数字、人名、需求名；禁止编造。
- 优先解读 cross_flags 与 cross 交点，不要按 axis_A～F 顺序逐条复述。
- 若 rhythm_idle 与 rhythm_alerts 同时存在，必须写出「冷热不均 / 关键路径」类判断。
- 若 orphan_epic 与 personnel 同主题任务并存，必须写出「表没挂上、活在做」类判断。
- 全文禁止 ##、**、GFM 表格；分段清晰，每段不超过 120 字为宜。
- 若所有轴均无显著问题，结论写整体正常，建议可省略或只留 1 条。
```

### 6.4 与变更预警（change-alert）的一致性

[`SKILL.change-alert.md`](../../skills_repo/pmo-copilot/SKILL.change-alert.md) 已采用 **fact_pack + LLM 写预警正文**。整体交叉分析与之 **同构**：

| 项 | 变更预警 | 整体交叉分析 |
|----|----------|--------------|
| 触发 | 单条/批量表变更 | 周期 / 手动 |
| fact_pack 范围 | 变更 + 人员快照 | 六轴全量 |
| 推送 | 有问题才推 | 默认推（或配置为仅 🟡 以上推） |
| LLM | `_llm_polish_change_alert_narrative` | 待统一为 `_llm_polish_cross_health_narrative` |

---

## 7. Lark 排版约定

整体交叉分析的交付物是 **诊断简报**，不是宏观看板三表。

| 项 | 约定 |
|----|------|
| 格式 | 飞书 **交互卡片 JSON 1.0**：`header`（蓝）+ `note`（元信息）+ 多段 `plain_text` + `hr` |
| 禁止 | 正文 `##`、`**`、大段 GFM 表 |
| 长度 | 单卡建议 ≤ 4000 字；过长拆「结论卡 + 细节卡」 |
| 推送 | 默认主群 `PMO_PRIMARY_CHAT_ID`；是否同步监控群可配置 |
| 实现 | 结构由 Python 组装；**段落内容由 LLM 填入 `narrative_sections[]`** |

---

## 8. 质量门槛（Review 检查清单）

**事实层（Python）**

- [ ] current_sprint 来自 B-S1/C-1 规则（sd ≤ today），非 row_index 猜
- [ ] 人员节奏来自 `vewCz1FFJi`，非 vewpI8lyYw COUNT
- [ ] orphan Epic 仅在 vewpI8lyYw 上判定
- [ ] fact_pack 含 `data_gaps`，无静默 null

**解读层（LLM）**

- [ ] 每个数字可在 fact_pack 中找到出处
- [ ] 至少 1 条 **跨轴** 洞察（非单轴复述）
- [ ] 无数据堆砌、无 SQL
- [ ] 建议可执行（谁、做什么、约多久）
- [ ] 整体正常时未虚构问题

**推送层**

- [ ] 卡片分段清晰、标题与正文样式区分
- [ ] 无 ## / ** 出现在正文

---

## 9. 落地路线（建议分三期）

| 阶段 | 内容 | 产出 |
|------|------|------|
| **P0** | 宿主预取 B/C + 现有 Auditor → 手工拼 fact_pack → LLM 1 轮写 narrative → Lark 卡片 | 脚本 / 子 Skill 验证链路（**不写死 narrative 模板**） |
| **P1** | `core:pmo_cross_health_pack` Tool：固定输出 §5.1 schema + cross_flags | fact_pack 可单测；Skill 只读 pack |
| **P2** | 飞书指令 / 定时任务；与分支 A 宏观看板 **可选捆绑**（先 pack 分析，再三表快照附录） | 产品化入口 |

**代码锚点（规划，尚未全部实现）**：

| 模块 | 职责 |
|------|------|
| `l3_node/tools/pmo_cross_health.py` | 组装 fact_pack |
| `l3_node/pmo_cross_health_flags.py` | cross_flags 规则 |
| `l3_node/pmo_narrative_lark_card.py` | 卡片结构 + 填入 LLM sections |
| `skills_repo/pmo-copilot/SKILL.cross-health.md` | LLM 解读 SOP |
| 复用 `pmo_personnel_query` / `pmo_sprint_query` / Auditor | Layer 1 事实 |

---

## 10. 示例：同一事实，错误 vs 正确写法

**fact_pack 片段**：

```json
{
  "axis_A_completeness": { "no_owner_count_dev_sprint": 98 },
  "axis_B_hierarchy": { "orphan_epic_count": 8, "orphan_epic_names": ["在线奖励-…"] },
  "axis_E_personnel": {
    "rhythm_alerts": [{ "person": "Gavin", "summary": "延期 1 项" }],
    "rhythm_idle": [{ "person": "Patrick", "summary": "偏闲", "task_count": 8 }]
  },
  "cross_flags": ["orphan_epic_high_but_personnel_busy:在线奖励", "key_path_split"]
}
```

**错误（Python 硬编码 / 数据堆砌）**：

> 发现一：无负责人 98 条。发现二：orphan 8 条。发现三：Gavin 延期。Patrick 8 任务。

**正确（LLM 人话 · 交叉解读）**：

> 本 Sprint 开发表有近百条任务没有负责人，同时人员看板上 Gavin 在 FB外跳 仍显示延期。交叉看，更像是 **表字段没维护**，而不是没人做——否则人员矩阵里不应出现明确 Owner 的延期项。  
>  
> 「在线奖励」更明显：Epic 层有多行挂不到子任务，但 Jack、Patrick 等人下面已有十几条具体任务。**结论：活在做，父记录没对齐**；建议指定 Epic Owner 统一维护，比再加人更有效。  
>  
> Patrick 多条线已到自测，Gavin、Kelden 仍在关键路径落后——**不是人手不够，是关键路径和负载没对齐**；建议本周内各 15 分钟 sync，先区分「真阻塞」还是「忘改状态」。

---

## 11. 文档索引

| 文档 | 用途 |
|------|------|
| 本文 | 整体交叉分析 + LLM 人话方案 SSOT |
| [`PMO_COPILOT_ARCHITECTURE.md`](./PMO_COPILOT_ARCHITECTURE.md) | INIT / FanOut / 三表战报 |
| [`PMO_CHANGE_ALERT_DESIGN.md`](./PMO_CHANGE_ALERT_DESIGN.md) | 变更驱动、三轴、fact_pack 先例 |
| [`PMO_WORK_ZONG_CASE_STUDY.md`](./PMO_WORK_ZONG_CASE_STUDY.md) | 宏观看板案例 |
| `skills_repo/pmo-copilot/SKILL.md` §1.2.1 | 七步查数 SOP |
| `skills_repo/pmo-copilot/SKILL.change-alert.md` | fact_pack + LLM 叙述先例 |

---

## 12. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-06-08 | 初稿：六轴交叉、fact_pack / LLM 边界、Lark 排版、落地路线 |
