# 飞书（Lark）招聘常用指令手册

本文说明在 **L3 IM 招聘通道** 中，哪些 **纯文本短句** 会在送入大模型之前被 **`try_lark_workflow_command_intercept`** 拦截并直接驱动 **`com.jachin.hr.recruitment`** 调度与 RPA（与 Agent 里口头说「帮我…」不是同一路径）。

**实现入口：** `l3_node/lark_workflow_command_interceptor.py`
**词表/谓词：** `l3_node/hr_lark_command_lexicon.py`
**选岗合并（可与参数写在同一条消息）：** `im_channels/dispatcher.py` 在拦截前会尝试 `apply_job_select_from_hr_im_text`；`tools/atom_lark_chat.py`

**数据根目录（默认）：** `~/.jachin/workspace/hr_recruitment/{目录键}/`
**无人值守指针：** `get_hr_recruitment_workflow_pointer()` — 多数指令作用在 **当前指针岗位**，与助手卡片里「某岗历史快照」可能不是同一岗；改批次/调度前请确认指针（成功回复里会提示目录键）。

---

## 1. 使用前提

| 条件 | 说明 |
|------|------|
| IM 配置 | `config/im_channels.yaml`（或等价配置）中 Lark 通道启用，消息进入 `process_lark_message` / 拦截器链路。 |
| HR 包 | 仓库内 `com.jachin.hr.recruitment` 可被 `hr_loader` 加载。 |
| 指针与 `jd.json` | 改批次、再抓、仅收网等通常需要 **当前招聘岗位指针** 非空，且该岗目录下 **`jd.json`** 或 **`_last_job_configs`** 可追溯。 |
| Boss | 自动化依赖 **Chrome CDP** + Boss 已登录；`jd.json` 中 **`jd_select`** 需与 Boss 顶栏在招职位一致。 |

**切岗：** 单独发一行 Boss 选岗文案，例如：`Python 工程师 _ 杭州 15-25K`（与下拉文案尽量一致）。也可与下文指令 **写在同一条消息**（逗号或自然语言拼接），避免只命中拦截器而未执行选岗合并。

---

## 2. 发布与启动调度

| 你发送的（示例） | 行为摘要 |
|------------------|----------|
| **同意** / **确认发布** / **就按这个发** / **直接发布**（裸短句） | 若会话/全局有待确认 JD：先落盘；`jd.json` 未标记已发帖且未 `skip_boss_post` 时，**先 `atom_post_job_boss`**，成功后再 **注册 APScheduler**。 |
| **同意调度** / **确认启动** / **开始无人值守** / **立即启动** / …（见代码 `_sched_phrases_only`） | **只注册调度**，不强制再发帖（Boss 已有在招岗、只开定时任务时用）。 |
| **继续** / **继续收网** / **恢复** / **恢复收网** 等 | 调 `resume_hr_recruitment_scheduler`：清 STOP、必要时按上次配置 **重新挂上主定时**（打招呼↔收网或历史模式）。 |

**注意：** 助手若说「完成验证后请回复继续」，单行 **同意** 可能被映射为 **继续**（避免误消费 JD pending）。详见拦截器内 `_map_agree_to_continue_if_verify_context`。

更细的「同意 vs 发帖」对照见：`docs/HR_RECRUITMENT_WORKFLOWS.md` §0。

---

## 3. 模式切换：仅收网 / 仅打招呼 / 批次参数

飞书侧 **L3 上线/重连简报**（`l3_node/channels/lark/hr_recruitment_notify.py` → `build_hr_l3_status_briefing_text` 的 **L4** 区块）会摘要本节要点，并附带 **一条改参示例**（如 `打招呼改成10人 收网改成50人 推荐间隔2分钟`）。注册无人值守成功时的中文封装（`hr_tool_reply_zh.format_add_automated_recruitment_task_result_for_hr`）也会在文末提示同类短句。

| 指令（示例） | 行为摘要 |
|----------------|----------|
| **仅收网** / **只抓简历** / **关闭打招呼** / **不要推荐牛人** 等（整句匹配，见 `_HARVEST_ONLY_RE`） | `jd` 关闭推荐侧，**只跑沟通页收简历** 定时，重新注册。 |
| **仅打招呼20** / **只打招呼 20** / **打招呼20次** 等 | 启动 **累计 N 次** 的仅打招呼任务；有未完成进度时会提示 **继续仅打招呼** 或 **仅打招呼N重开**。 |
| **继续仅打招呼** | 续接该岗未完成的仅打招呼进度。 |
| **仅打招呼20重开** / **重开 仅打招呼 20**（变体见正则） | 放弃旧计数，按新目标重开。 |
| **打招呼改成20人** + **收网改成10人** + **推荐间隔2分钟**（可写在一条消息） | 更新 **每轮打招呼上限**、**每轮收网上限**、**推荐↔收网交替间隔**，并重注册；改收网数字时会将 **`resume_collect_target` / `analyze_threshold`** 与「每轮收网」对齐（见 `apply_lark_hr_batch_limits`）。 |
| **每轮沟通多少人** / **进度**（部分问法） | 查询当前 **max_count / greet_target / 间隔** 等（与「进度」简报可能分属不同分支，以实际回复为准）。 |

**交替模式说明：** Boss 单页 **推荐牛人与收简历严格交替**；间隔分钟数为 **`greet_harvest_switch_interval_minutes`**，与飞书 **推荐间隔 N 分钟** 写入一致。达本轮打招呼上限或沟通列表空时可能 **提前切换**（秒级），不必等满间隔。

**「打招呼改成」与「仅打招呼累计」区别：** 前者是 **交替模式每轮上限**；后者是 **累计成功打招呼总次数** 的独立战役。

---

## 4. 收网目标份数（含「仅收网」）

| 指令（示例） | 行为摘要 |
|----------------|----------|
| **再抓 6 份** / **多抓取10份简历** / **继续抓 3 份**（须匹配「抓取/抓/收」与数字，见 `_MORE_HARVEST_RE`） | **新目标 = max(原目标, 待透析估算, pending PDF 数) + N**，写回 `jd.json` 与调度并 **remove+add** 任务。飞书双投时短时 **去重**，避免连加两次。 |

若要把目标 **直接设为绝对值**（例如从 4 改 10）：改该岗 **`jd.json`** 的 **`resume_collect_target`** 与 **`analyze_threshold`**，再发 **仅收网** 或等价重注册操作。详见场景说明：`docs/HR_RECRUITMENT_WORKFLOWS.md` §1。

---

## 5. 停止、分析、进度与恢复

| 指令（示例） | 行为摘要 |
|----------------|----------|
| **停止** / **暂停** / **别抓了** / **停止收网** …（含收网/抓取语境时） | 注入 **STOP_HARVEST**，并 **移除** 当前岗的 **推荐/收网/交替/仅打招呼** 等浏览器定时任务（**保留** 分钟级 **check** 透析轮询）。目录键按指针解析（`remove_harvest_scheduler_jobs`）。 |
| **停止/关闭…无人值守** 且无收网语境 | 可能 **不拦截**，交给 Agent / `stop_automated_recruitment`（见 `recruitment_stop_without_harvest_cue`）。 |
| **分析简历** / **透析镜** / **开始分析** 等（见 `hr_lark_command_lexicon`） | 停收网侧信号 + 登记 **手动透析** / 触发分析流程；与 BI「数据分析」等歧义句 **不拦截**。短时重复「分析」会 **冷却** 提示。 |
| **进度** / **状态** / **什么进度** 等 | 返回当前岗简报（pending、调度是否运行、全局停止等），与 L3 启动推送同源逻辑。 |
| **恢复挂起岗位：`目录键`** | 换岗抢占被挂起的岗位，按 `scheduler_state` **恢复该岗**定时任务。 |

---

## 6. 清除记忆（高危）

| 指令（示例） | 行为摘要 |
|----------------|----------|
| **清除全部岗位记忆** / **清空所有招聘岗位记忆** 等 | 与 `scripts/reset_hr_recruitment_all.py` **默认**一致的全量清理（含目录、指针、会话等；**默认不保留** `lark_chat_id`）。 |
| **清除岗位：某某** / **清空岗位 某某** | 单岗：卸调度、清状态、该岗 `jd.json` 写 **`show_in_hr_briefing: false`**，**不删** 简历文件。 |

---

## 7. 易混点速查

1. **指针 ≠ 助手正在描述的岗** — 批次更新、再抓、停止都认 **指针**；看卡片后要先发 **选岗行** 或同条合并选岗。
2. **「再抓 N」是加法** — 基准取 max(旧目标, 未处理, PDF)，不是单纯「旧目标 + N」在极端 pending 情况下要留意。
3. **「停止」后** — 当前 Playwright 这一轮可能仍会跑完；主定时卸掉后应 **不再** 自动交替；若仍跑，查日志是否 **`removed`** 非空、目录键是否与 `rec_{目录键}_*` 一致。
4. **模糊句** — 未命中硬指令时可能走 **`intent_clarification`** HR 插件，反问请发短指令。

---

## 8. 相关文档与代码

| 文档/模块 | 内容 |
|-----------|------|
| `docs/HR_RECRUITMENT_WORKFLOWS.md` | 发帖/仅收网/换岗/停止与继续的场景与 `jd.json` 字段 |
| `l3_node/lark_workflow_command_interceptor.py` | 全部匹配顺序与飞书回复文案 |
| `skills_repo/.../recruitment_scheduler.py` | `add_scheduled_job`、`apply_lark_*`、`remove_harvest_scheduler_jobs` |
| `l3_node/im_channels/dispatcher.py` | 拦截前选岗 prelude、招聘类消息路由 |

---

*文档与仓库内上述路径实现同步维护；若行为与日志不一致，以当前代码为准。*
