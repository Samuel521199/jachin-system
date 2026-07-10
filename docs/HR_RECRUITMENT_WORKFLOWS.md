# 招聘无人值守：典型场景与操作说明

本文回答「仅收网 / 全链路 / 换岗 / 停止与继续」四类问题，并与当前 L3 + `com.jachin.hr.recruitment` 实现一致。数据根目录默认：`~/.jachin/workspace/hr_recruitment/{数据目录键}/`（目录键由 `jd_select` 或职位+城市+薪资解析，见 `resolve_recruitment_data_folder_key`）。

---

## 0. 飞书「同意」为何曾只开调度、不发 Boss 帖？（已修复）

**原因（架构）：** IM 入口 `im_channels/dispatcher.py` 对整句指令**先**调 `try_lark_workflow_command_intercept`。若拦截器返回文案，则**直接回复并结束**，**不再**进入 Agent WorkOrder。因此 Agent 里「用户同意 → `_execute_publish_bypass` → `atom_post_job_boss`」的预检**根本不会执行**。旧版拦截器在「同意」时只做 `_persist_jd_config_before_publish` + `start_scheduler_from_jd_pointer`，与助手口头「回复同意将**发布**」不一致，表现为**无人值守已开、Boss 上未发帖**。

**修复后行为：**

| HR 发送 | 是否先强制 `atom_post_job_boss` | 说明 |
|---------|----------------------------------|------|
| **同意** / **确认发布** / **就按这个发** / **直接发布**（裸短句，匹配 `_bare_agree_jd`） | **是**，当 `jd.json` **未**标记 `boss_post_published` 且会话 pending **未**带 `skip_boss_post` | 发帖成功（或已标记在招）后再注册 APScheduler |
| **同意调度** / **开始无人值守** / **立即启动** 等（`_sched_phrases_only`） | **否** | 与「发帖已在先、只确认调度」一致；**只抓简历且 Boss 已有职位**时优先用此短语 |
| 发帖失败 / 需登录 | 返回说明，**不**启动调度 | 避免无在招职位空跑推荐/收网 |

**只抓简历、Boss 上已有在招职位 —— 不想发新帖时：**

1. **推荐（飞书）**：发 **「同意调度」** 而不是裸「同意」，拦截器**不会**调用 `atom_post_job_boss`。
2. **推荐（配置）**：在 `jd.json` 中写入 **`boss_post_published: true`**（与 Boss 事实一致），裸「同意」时发帖工具会 **already_published** 短路，直接开调度。
3. **自动（Agent）**：以下任一会给会话 pending 打上 **`skip_boss_post`**（**不落盘**到 `jd.json`，见 `hr_data_paths.init_job_jd_from_template` 排除该键）：
   - 对话被识别为 **分支 B / 轻量收网**；
   - 用户近几条消息含 **「只收网 / 只抓简历 / 已有职位 / 不用发帖」** 等（`_hr_user_intent_skip_boss_post`）；
   - 助手本轮回复含 **「只收网 / 已有在招 / 无需发帖」** 等（`_hr_assistant_declares_skip_boss_post`）。
4. **手工**：在助手给出的 ```json``` 里加 **`"skip_boss_post": true`**（会进 pending 文件，仍不写 jd.json）。

**相关代码：** `l3_node/lark_workflow_command_interceptor.py`、`l3_node/agent_core.py`、`tools/hr_data_paths.py`（`skip_boss_post` 不写入 jd.json）。

---

## 1. 新岗位：不发布职位，只做收网（不打招呼）

**结论：可以。** 系统依赖的是 **`jd.json` + Boss 上已有对应在招职位可选中**（与是否在本系统里走 `atom_post_job_boss` 发帖无必然关系）。你需要的是：

1. **写好 `jd.json`**（至少含可对齐 Boss 顶栏的 `jd_select` / `job_title` 等，便于 Playwright 选对岗）。
2. **飞书确认开跑时**：不要用裸 **「同意」**（会尝试发帖，见 §0），请发 **「同意调度」**，或让会话 pending 带上 **`skip_boss_post`** / jd 带 **`boss_post_published: true`**（见 §0 小节「只抓简历…」）。
3. **注册调度**时关闭推荐侧：
   - 调用 `add_automated_recruitment_task` 时传 **`enable_greet_recommend: false`**，或
   - 在写入 jd 时显式 **`"enable_greet_recommend": false`**（若会话里 HR 确认的 JSON **未带该字段**，当前产品默认会按「开交替」合并，**仅收网意图必须在 JSON 或工具参数里显式关**）。

**会创建什么：** `init_job_jd_from_template` / 持久化流程会创建该岗目录、`jd.json`、`pending` / `processed` / `result` 等；调度器注册 **`rec_{目录键}_harvest`**（仅收网 tick）+ **`rec_{目录键}_check`**（按份数透析规则引擎）。

**后续「多抓 N 份」：** 飞书可发 **「再抓 N 份」**（见 `lark_workflow_command_interceptor`），会调高 `resume_collect_target` 并重注册任务；或通过助手改 jd / 再调 `add_automated_recruitment_task`。

**后续「直接分析」：** 可用 MCP / 飞书 **分析简历** 等入口；规则引擎也会在 **未出 AI 评价的简历达到 `analyze_threshold`（与收网目标份数对齐）** 时触发透析（与是否发帖无关）。

---

## 2. 新岗位：发帖 + 推荐牛人 + 收简历（全链路）

**结论：可以；数字可自设，且应在确认单/回复里可见。**

**默认值（与代码一致，未在 jd 中写明时多数取此默认）：**

| 含义 | 字段 / 说明 | 默认 |
|------|-------------|------|
| 每轮「推荐牛人」打招呼人数上限 | `greet_target` | **3** |
| 单次收网 tick 在沟通列表最多处理的会话数 | `max_count` | **50** |
| 累计收到多少份简历后停自动收网/交替 | `resume_collect_target` / `analyze_threshold`（二者收敛为同一套份数） | **4** |
| 推荐 ↔ 收简历切换间隔（分钟） | `greet_harvest_switch_interval_minutes`（常与 `recommend_interval_minutes` 一致） | **10**（发帖后飞书确认单合并逻辑里若 jd 无键可取 10） |
| 是否开推荐侧 | `enable_greet_recommend` | **true**（jd 未写该键时与飞书确认单一致） |

**如何提醒用户自己设置：**

1. **发帖成功后的飞书调度确认单**（`hr_scheduler_send_confirm_prompt` → `_format_scheduler_confirm_lark_text`）会说明交替规则、累计收网份数、透析触发份数，并**写明单次收网 tick 的 `max_count`**；文末提示可口述 **收网改成 N、打招呼改成 N、推荐间隔 N 分钟** 等。
2. **`add_automated_recruitment_task` 成功后的 HR 中文摘要**（`format_add_automated_recruitment_task_result_for_hr`）会打出**当前生效的** `greet_target`、`max_count_per_harvest_tick`、`greet_harvest_switch_interval_minutes`、累计份数等。

---

## 3. 岗位进行中或 L3 刚启动：换另一个岗位做调度

**结论：换岗会走「抢占 + 挂起」逻辑，流程是刻意的、可恢复的。**

- **`add_scheduled_job` 在注册新岗前**会调用 **`_apply_preempt_suspend_marks_before_switch`**：对**当前仍在跑**、且与新岗 **目录键不同** 的旧岗，在 `scheduler_state.json` 里写入 **`scheduler_suspended`**（原因 `preempted_by_switch`），再 **`remove_all_recruitment_apscheduler_jobs`**，最后只挂新岗的定时任务。
- **Boss 单页互斥**：同一时刻只跑一个岗的浏览器侧任务，这是设计约束。
- **恢复旧岗：**
  - 飞书：**「恢复挂起岗位：目录键」**（见 `lark_workflow_command_interceptor`），或
  - MCP：`list_hr_scheduler_suspended_jobs`、`resume_hr_job_scheduler(job_folder=...)`。
- **L3 冷启动：** 挂起信息在 **`scheduler_state.json`** 中持久化；进程重启后列表仍在，但若需继续跑需 **重新注册 APScheduler**（用「恢复挂起」或 `resume_hr_recruitment_scheduler` / 再次 `add_automated_recruitment_task`）。

---

## 4. 停止、继续、换招别的岗

**结论：可以切换；区分「停止」与「换岗抢占」两种语义。**

| 操作 | 行为摘要 |
|------|----------|
| **`stop_automated_recruitment`（job_name 空）** | 全局停止标志 + 按当前已注册任务逐个 `remove_scheduled_job`；**不写**「换岗抢占」式 `scheduler_suspended`。`_last_job_configs` 仍可能保留，便于之后按 jd/配置恢复。 |
| **`stop_automated_recruitment`（指定岗位）** | 只卸该岗相关 `rec_*` 任务。 |
| **换岗并启动新调度** | 旧岗（若在跑）得 **挂起标记**，新岗接管定时任务（见上一节）。 |
| **飞书「继续」** | `resume_hr_recruitment_scheduler`：清 STOP、若当前指针岗上已无定时任务则按 **`_last_job_configs` 或 jd.json** 尝试 `add_scheduled_job`。 |
| **停止当前岗后去发布/招聘别的岗** | 先 **停止**（或直接用新岗 `add_scheduled_job` 触发抢占），再走新岗的 **发帖 / 确认 jd / `add_automated_recruitment_task`**；新任务成功时会 **`set_recruitment_stopped(False)`**，不会一直被全局停止挡住。 |

**注意：** 若希望旧岗像换岗一样出现在「挂起列表」里，应依赖 **换岗时的抢占**；纯 **stop** 不自动写 `scheduler_suspended`，但通常仍可用 **`resume_hr_job_scheduler_for_folder`** + 磁盘上的 **`_last_job_configs` / jd.json** 恢复。

---

## 相关代码入口（便于排查）

- 调度核心：`skills_repo/plugin/com.jachin.hr.recruitment/recruitment_scheduler.py`（`add_scheduled_job`、`_apply_preempt_suspend_marks_before_switch`、`resume_hr_recruitment_scheduler`、`resume_hr_job_scheduler_for_folder`）
- 注册任务 MCP：`tools/add_automated_recruitment_task.py`
- 飞书确认单：`tools/hr_scheduler_confirm_prompt.py`
- HR 可读封装：`l3_node/hr_tool_reply_zh.py`
- 飞书短指令：`l3_node/lark_workflow_command_interceptor.py`（再抓 N 份、停止收网、恢复挂起等）
- **飞书指令汇总（表格式）：** `docs/HR_LARK_COMMANDS.md`
- MCP 注册：`l3_node/primitives/mcp/registry.py`（`stop_automated_recruitment`、`list_hr_scheduler_suspended_jobs`、`resume_hr_job_scheduler`）

---

## 版本说明

本文与仓库内上述路径实现同步维护；若行为与日志不符，以 **`recruitment_scheduler` 与 `add_automated_recruitment_task` 当前代码** 为准。
