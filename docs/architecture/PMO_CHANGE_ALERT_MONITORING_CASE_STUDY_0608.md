# PMO 变更预警 · Agent 实操复盘：Lark 表监听、分析与推送（2026-06-08）

> **文档定位**：记录一次 **Agent 端到端执行** PMO 变更预警的完整过程——如何监听飞书多维表、监听结束后做什么、真实检测无变更时如何降级为模拟变更并完成推送；含 **分轮尝试、工具、报错与修复**。  
> **读者**：产品、PMO、后端 / Agent 工程师。  
> **关联 SSOT**：[`PMO_CHANGE_ALERT_DESIGN.md`](./PMO_CHANGE_ALERT_DESIGN.md) · [`PMO_CHANGE_ALERT_CASE_STUDY_0605_MAHJONG.md`](./PMO_CHANGE_ALERT_CASE_STUDY_0605_MAHJONG.md) · `skills_repo/pmo-copilot/SKILL.change-alert.md` · `l3_node/tools/pmo_bitable_watch.py` · `l3_node/tools/pmo_change_alert.py`

---

## 1. 用户要什么？

用户要求 Agent **直接完成** 变更预警任务，而不是只给方案：

1. 监听 / 检测飞书需求表变更  
2. 对变更做 **多维度交叉分析**（排期、人员、项目）  
3. **有问题才推 Lark**；无问题则停止  
4. 若当时 **检测不到真实变更**，可用 **模拟的有问题变更** 走通推送，验证链路  

后续又明确要求：**优先真实 Lark 表检测**；实在没有变更就如实告知，不要假装有变更。

---

## 2. Agent 如何拆解任务？

接到需求后，Agent 将工作拆为 **五条可验收子任务**（不启动完整 ReAct 多轮 Agent，以 **Python 流水线 + 脚本/Shell** 为主路径）：

| 序号 | 子任务 | 完成标准 | 主要代码/工具 |
|------|--------|----------|----------------|
| ① | **理解监听机制** | 能说清 webhook / poll / 防抖 session / finalize | `pmo_bitable_watch.py`、配置 YAML |
| ② | **触发检测** | 真实 diff 或 session 内有 events | `scripts/run_pmo_change_alert_once.py --watch-tick` |
| ③ | **三轴分析** | 产出 `fact_pack`，含 `should_push` | `analyze_change_events()` |
| ④ | **生成人话正文** | BI 大战报式四段 prose | `build_change_alert_prose_brief()` |
| ⑤ | **有条件推送** | `should_push=true` → Lark；否则静默 | `push_change_alert()` → `send_watch_notification()` |

**原则**（与 Skill 一致）：

- **分析结论由 Python 规则 + 镜像库决定**，LLM 只可选润色正文，不改事实。  
- **全 ✅ → 不推**；存在 🚨/⚠️ 业务风险 → 推。  
- 推送正文默认 **大战报结构**（🎯定调 → 📋变更 → ⚠️影响 → 💡建议），不是字段列表。

---

## 3. 怎么监听 Lark 多维表？

### 3.1 监控对象

| 项 | 值（本次环境） |
|----|----------------|
| 表 | `tblfK9gk6vTQpJtB`（K11 项目进度 · 同一张 Bitable） |
| 视图 | **`vewCz1FFJi`**（人员任务看板 · Worker B SSOT；变更回调监控此视图） |
| 配置 | `config/skills/pmo-copilot/pmo_bitable_watch.yaml`（运行时优先 `~/.jachin/config/skills/pmo-copilot/pmo_bitable_watch.yaml`） |
| 模式 | `hybrid` = **飞书长连接/Webhook 事件** + **定时 poll 全表 diff** |

> **说明**：三轴分析里人员负荷仍读镜像 `vewCz1FFJi`（B-TOOL）；开发 Epic 交叉核对仍用 `vewpI8lyYw`。**监听 diff 的视图**与 PM 在人员看板上的改表动作对齐。

### 3.2 三种「听到变更」的方式

```text
方式 A · Webhook / 长连接
  飞书 drive.file.bitable_record_changed_v1
    → POST /webhook/pmo_table_change 或长连接脚本
    → handle_lark_bitable_record_changed()
    → 写入防抖 session.events

方式 B · Poll diff（hybrid / poll 模式）
  每 poll_interval_seconds（默认 15s）拉全表
    → diff_record_maps(baseline_records, current_records)
    → created / updated / deleted 事件列表

方式 C · 手动一次 tick
  python scripts/run_pmo_change_alert_once.py --watch-tick
  python scripts/run_pmo_change_alert_once.py --watch-tick --force-finalize
```

### 3.3 防抖会话（不是「改一条推一条」）

```text
变更进入 session.events
  → 重置 last_change_at
  → idle_seconds（默认 20s）内无新变更
  → _finalize_session()
```

同一条 `record_id` 在 session 内会 **merge**（`changed_fields` 取并集），避免同需求连改多格产生多条推送。

### 3.4 状态落盘

| 路径 | 内容 |
|------|------|
| `~/.jachin/data/pmo_bitable_watch_state.json` | baseline、current、session.events |
| `~/.jachin/data/pmo_bitable_watch_callbacks/` | finalize 后本地 NDJSON / latest.md |

---

## 4. 监听结束后应该做什么？

`_finalize_session()`（`pmo_bitable_watch.py`）在 session 结束时顺序执行：

```text
session 结束（idle 到期 或 force_finalize）
  │
  ├─ [可选] push_change_summary
  │     format_change_summary_markdown(events) → Lark
  │     ※ 2026-06-08 起默认关闭（见 §8），避免 raw 表超长
  │
  ├─ run_change_alert（默认开启）
  │     analyze_change_events(events) → fact_pack
  │     should_push == false → 静默，notified=false
  │     should_push == true  → resolve_change_alert_push_markdown()
  │                          → push_change_alert() → 主群 (+ 可选监控群)
  │
  └─ persist_local → ~/.jachin/data/pmo_bitable_watch_callbacks/
```

**变更预警产品路径** 仅关心 **`run_change_alert` 分支**；`change_alert_result` 写入日志与本地回调 JSON，**不**出现在 PM 可见正文末尾。

### 4.1 三轴分析（Analyze）

`core:pmo_change_alert_analyze` / `analyze_change_events()`：

| 轴 | 输入 | 典型信号 |
|----|------|----------|
| 排期 | Start / Due / Sprint / Acceptable | 零缓冲、mid-sprint 插单、Due 已过 |
| 人员 | B-TOOL `personnel_tasks[]` | 延期未关、同日 Start=Due 插单 |
| 项目 | 镜像关键字检索 dev/person/product | 跨视图不一致、Epic 无 Owner |

决策门 `_should_push()`：**仅业务级 🚨/⚠️ 推送**；纯 `mirror_row_missing` 或未来 Sprint 无负荷 **不推**（2026-06-08 收紧逻辑）。

### 4.2 推送正文（Alert）

| 步骤 | 函数 | 说明 |
|------|------|------|
| 规则 prose | `build_change_alert_prose_brief(fact_pack)` | 大战报四段，默认路径 |
| LLM 润色 | `_llm_polish_change_alert_narrative()` | `PMO_CHANGE_ALERT_LLM_NARRATE=1` 时；失败回退规则 prose |
| 标题 | `human_change_alert_title(fact_pack)` | 例：`【PMO】麻将开发 · Gavin 需关注 · 2026-06-05` |
| 发送 | `send_watch_notification()` | 飞书应用机器人 IM 卡片 |

---

## 5. Agent 分轮执行记录（本次会话）

### 第 0 轮 · 读 SSOT 与代码锚点

| 动作 | 工具 / 文件 |
|------|-------------|
| 读 Skill | `skills_repo/pmo-copilot/SKILL.change-alert.md` |
| 读流水线 | `l3_node/tools/pmo_change_alert.py`、`pmo_bitable_watch.py` |
| 读 CLI | `scripts/run_pmo_change_alert_once.py` |

**结论**：主路径是 **宿主 Python 三轴 + 决策门**；Agent 不应自由 `db_query` 改结论。

---

### 第 1 轮 · 模拟麻将插单 + 首次推送

| 步骤 | 命令 / 代码 | 结果 |
|------|-------------|------|
| 构造 events | `_demo_mahjong_event()`（后扩展为 Epic + 子任务 2 条） | Gavin + 零缓冲 + 延期项场景 |
| 分析 | `analyze_change_events()` | `should_push=true`，`alert_sent` |
| 推送 | 用户 bot `cli_a9253a96b179deee` | ❌ `Bot/User can NOT be out of the chat` |
| 重试 | PMO bot `cli_a940990299f8ded2` | ✅ success → `oc_437c98d11106295fb10751a5481ee465` |

**教训**：推送 bot 必须已加入目标群；凭证与群 ID 是两件事。

---

### 第 2 轮 · 文案改造（大战报式人话）

用户反馈推送「死板、堆字段」。Agent 修改：

| 改动 | 文件 |
|------|------|
| `build_change_alert_prose_brief()` 四段结构 | `pmo_change_alert.py` |
| 飞书人员 JSON 解析（Vivian 任务7 误报） | `_parse_assignees()` + `_names_from_lark_person_value()` |
| 收紧 `_should_push()` | 镜像 lag / 未来 Sprint 不单独触发推送 |
| LLM prompt 对齐 BI 战报文风 | `_llm_polish_change_alert_narrative()` |
| 单测 | `tests/unit/test_pmo_change_alert.py` |

---

### 第 3 轮 · 真实 `--watch-tick --force-finalize`（失败）

| 步骤 | 结果 |
|------|------|
| Shell 后台跑 watch tick | 运行约 **12 分钟**，exit `-1` |
| Lark | ❌ `The length of the message content reaches its limit` |
| SQLite | 连续 `database is locked`（与长连接 / 其它 PMO 进程抢 `pmo_db.sqlite`） |

**根因分析**（事后读 state）：

| 现象 | 说明 |
|------|------|
| session.events **3091 条** | 非 PM 改 3091 次，而是 **poll diff 误报整表** |
| 来源 `poll_diff` | 3040 updated + 43 created + 8 deleted |
| state `table_id` 旧值 `tblB2uMLGIQrAttB` vs 配置 `tblfK9gk6vTQpJtB` | 换表未 reset → 一次性把视图内几乎每行当变更 |
| 推送爆长度 | **`push_change_summary=true`** 对 3091 条逐条 GFM 表 dump，非 change-alert prose |

用户直觉正确：**真实业务变更只有几条**；三千条是 **检测链路 bug**。

---

### 第 4 轮 · 修复 watch + 模拟推送验证

Agent 实施修复并跑通 `data/_run_alert_demo_push.py`：

| 修复 | 说明 |
|------|------|
| `push_change_summary: false` | 只推 change-alert 短 prose |
| poll 洪水熔断 | 单次 diff > 25 → `poll_flood_baseline_reset`，不入 session |
| `_sync_state_scope()` | table_id / view_id 变更 → 清空 baseline + session |
| SQLite `busy_timeout=30s` | `pmo_db_tools._connect()` |
| 清除脏 session | 3091 条 → 0 |

| 步骤 | 结果 |
|------|------|
| 模拟 2 条麻将 events + Gavin 延期 seed | `should_push=true` |
| 推送 `oc_437c98d11106295fb10751a5481ee465` | ✅ success |
| 产物 | `data/_alert_demo_analysis.json`、`data/_alert_demo_push.json` |

**模拟推送正文示例**（节选）：

> 🎯 【定调】：本次表更存在需要 PM 立即对齐的资源/排期风险…  
> 📋 【变更】：新增「麻将开发」尚未指定负责人…；新增「麻将花色增加开发」指派给 Gavin…  
> ⚠️ 【影响】：零缓冲；Gavin 仍有「FB外跳-程序开发」（计划 2026-06-02）未关…  
> 💡 【建议】：补 Epic Owner；先和 Gavin 对齐延期项…

---

### 第 5 轮 · 纯真实检测（用户明确要求）

| 步骤 | 命令 | 结果 |
|------|------|------|
| 读 state | Python 读 `pmo_bitable_watch_state.json` | session 0 条；baseline=current=3083 |
| 第 1 次 tick | `--watch-tick` | `baseline_initialized`（table_id 不一致后 reset） |
| 第 2 次 tick | `--watch-tick` | `idle`，**无变更** |
| debounce tick | `run_bitable_watch_debounce_tick()` | 无活跃编辑会话 |
| offline diff | baseline vs current in state | **0 条** |

**结论如实告知用户**：当前相对基线 **没有新变更**，**未推送 Lark**。  
若要真实预警：在飞书改表 → 停手 20s → 再 tick 或等长连接 finalize。

---

## 6. 报错一览与解决方式

| 报错 | 阶段 | 原因 | 解决 |
|------|------|------|------|
| `Bot/User can NOT be out of the chat` | 推送 | 用户提供的 bot 未入群 | 改用已在群内的 PMO-Copilot bot |
| LLM narrate 空 content | 推送 | reasoning 模型占满 token | 默认 `PMO_CHANGE_ALERT_LLM_NARRATE=0` 或回退规则 prose |
| `message content reaches its limit` | watch finalize | raw summary × 3091 条 | 关闭 `push_change_summary` + poll 熔断 |
| `database is locked` | 分析 | 多进程写/读同一 SQLite | busy_timeout；避免与 long_connection 同时 finalize |
| **同一条预警连发 5+ 次** | 推送 | 并发 finalize + poll 反复触发同一需求 | session claim 锁 + **1h 推送 dedup** + 变更预警默认不推监控群 |
| 任务7 JSON 负责人拆成 3 假人 | 分析 | `_parse_assignees` 按逗号切 JSON | Lark person JSON 专用解析 |
| 3091 条「变更」 | 检测 | poll + 坏 baseline / 换 table | reset + flood threshold |

---

## 7. 模拟变更 vs 真实变更：Agent 如何选路径？

```text
                    ┌─────────────────┐
                    │ 用户要求预警     │
                    └────────┬────────┘
                             │
              ┌──────────────▼──────────────┐
              │ --watch-tick / 长连接 finalize │
              └──────────────┬──────────────┘
                             │
            ┌────────────────┴────────────────┐
            │ events 非空？                      │
            └────────────────┬────────────────┘
                   是 │                │ 否
                      ▼                ▼
         analyze_change_events    如实报告 all_clear
         should_push?                  （本次第 5 轮）
              │
      ┌───────┴───────┐
      是              否
      ▼               ▼
 push_change_alert   结束

── 用户允许且真实无变更时的验证路径 ──

         _demo_mahjong_event()
         + MAHJONG_SEED（Gavin 延期）
              → analyze → push
              （本次第 4 轮）
```

**模拟 events 结构**（`scripts/run_pmo_change_alert_once.py`）：

1. **Epic**「麻将开发」：无负责人，6/5 当天交付  
2. **子任务**「麻将花色增加开发」→ Gavin，同 Sprint 零缓冲  

**人员 seed**（`data/_run_alert_demo_push.py`）：

```python
MAHJONG_SEED = {
    "current_sprint": "2026/06/01-Sprint",
    "personnel_tasks": [{
        "person": "Gavin",
        "task": "FB外跳-程序开发",
        "expected_delivery_date_iso": "2026-06-02",
        "is_current_week": True,
    }],
}
```

---

## 8. 本次会话改动的代码与配置

| 文件 | 改动摘要 |
|------|----------|
| `l3_node/tools/pmo_change_alert.py` | 大战报 prose、JSON 负责人解析、收紧 `_should_push` |
| `l3_node/tools/pmo_bitable_watch.py` | poll 熔断、table/view reset |
| `l3_node/tools/pmo_db_tools.py` | SQLite busy_timeout |
| `config/skills/pmo-copilot/pmo_bitable_watch.yaml` | `push_change_summary: false` |
| `scripts/run_pmo_change_alert_once.py` | demo 含 Epic + 子任务两条 |
| `data/_run_alert_demo_push.py` | 一键：清脏 session → 分析 → 推送 |
| `skills_repo/pmo-copilot/SKILL.change-alert.md` | §6 大战报 prose 说明 |
| `tests/unit/test_pmo_change_alert.py` | JSON 解析、任务7 静默、prose 结构 |

---

## 9. 复现命令

### 9.1 真实检测（推荐）

```powershell
# 在飞书改表后，停手 ~20s
$env:PMO_CHANGE_ALERT_LLM_NARRATE='0'
python scripts/run_pmo_change_alert_once.py --watch-tick
python scripts/run_pmo_change_alert_once.py --watch-tick --force-finalize
```

### 9.2 模拟有问题变更 + 推送

```powershell
$env:PMO_CHANGE_ALERT_LLM_NARRATE='0'
python data/_run_alert_demo_push.py
# 或
python scripts/run_pmo_change_alert_once.py --demo mahjong --push --chat-id oc_437c98d11106295fb10751a5481ee465
```

### 9.3 只看分析不推送

```powershell
python scripts/run_pmo_change_alert_once.py --demo mahjong
```

### 9.4 推送群与配置

| 项 | 说明 |
|----|------|
| 主群（用户指定） | `oc_437c98d11106295fb10751a5481ee465` |
| YAML 默认 chat | `PMO_BITABLE_WATCH_CHAT_ID` 或 `pmo_bitable_watch.yaml` → `chat_id` |
| 机器人 | 须已加入目标群（PMO-Copilot bot 已在群内） |

---

## 10. 给后续 Agent 的检查清单

- [ ] 优先 `--watch-tick` 或读 `pmo_bitable_watch_state.json` 确认 **session 条数是否合理**（>25 怀疑 poll 误报）  
- [ ] 确认 `push_change_summary` 为 **false**，避免 raw 表再次超长  
- [ ] 分析走 `analyze_change_events` → `should_push`，**不要**因镜像 lag  alone 推送  
- [ ] 正文用 `build_change_alert_prose_brief`，**禁止**向 PM 展示 `change_alert_result` 行  
- [ ] 推送前确认 bot 在群内；失败换 `atom_lark_notifier` 配置的 PMO bot  
- [ ] 真实无变更时 **明确告知用户**，仅在用户允许时用 `--demo mahjong` 验证链路  
- [ ] 长连接与手动 tick **不要**同时 finalize 同库（易 `database is locked`）  
- [ ] 同一需求 **1 小时内不应重复推送**（`pmo_change_alert_dedup.json`）  

---

## 12. 重复推送（pixel 案例 · 2026-06-08）

**现象**：`pixel分数优化-网盟体验优化` 在群内出现 **5～6 条几乎相同的预警卡**（首条或为 LLM 润色版，其余为规则 prose）。

**根因（叠加）**：

1. **调度器每 5s 跑 debounce tick**，`_job_tick` 在**后台线程**执行；idle 到期时 **多个线程同时 finalize**，在旧逻辑里 session 要推送完才清空 → 同一批 events 推多次。  
2. **poll / webhook 反复**把同一 record 标为 updated → 新 session → 20s 后再推（内容相同）。  
3. **`push_monitor=true`** 时还会再推监控群（若两群都可见则像「双份」；本次主要是同群重复）。

**修复（2026-06-08）**：

| 措施 | 位置 |
|------|------|
| finalize **先 claim 清空 session** + `threading.Lock` | `pmo_bitable_watch._finalize_session` |
| 推送指纹 **1h 内 dedup** | `pmo_change_alert_dedup.json` · `PMO_CHANGE_ALERT_DEDUP_SECONDS` |
| 变更预警 **默认不推监控群** | `change_alert_push_monitor: false` |

---

## 11. 与设计文档的差距（已知）

| 设计目标 | 本次状态 |
|----------|----------|
| Webhook 实时、条数级 session | ✅ 机制已有；本次无活跃 webhook session |
| 有问题才推、人话预警 | ✅ 已实现 prose + 决策门 |
| Poll 仅兜底 | ⚠️ 仍为 hybrid；已加熔断，建议生产改 `mode: webhook` |
| 镜像库 SSOT | ⚠️ locked 时人员快照退化，需单 writer 或 WAL |

---

*文档版本：2026-06-08 · 基于 Cursor Agent 会话实操整理*
