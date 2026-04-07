# HR 插件 - 自然语言发帖与实时进度汇报方案

基于飞书（Lark）作为「全息感官界面」的端到端交互设计。飞书承担两个方向的数据流：

1. **上行指令（HR → 系统）**：接收非结构化的自然语言，转化为结构化规则和爬虫任务
2. **下行汇报（系统 → HR）**：系统状态机变化时，主动或被动向飞书推送进度卡片

---

## 一、零阶漏斗：自然语言发帖与规则定调（上行）

### 1.1 Skills：hr-job-manager

当 HR 在飞书发语音或文字时，触发此技能。

**示例**：
- 「帮我发个前端的岗，15-20k，必须统招本科，不要频繁跳槽的」
- 「招个 Java，20k，只要统招本科，要有大厂外包经验也行」

### 1.2 执行流程

1. 调用 `nat_lang_to_jd` 解析自然语言 → 结构化 JD（job_title、salary_range、hr_criteria、jd_full）
2. 保存到 `~/.jachin/workspace/hr_rules/{slug}.md` 和 `recruitment_status.json`
3. 调用 `atom_post_job` 保存 JD 到 `pending_jobs/`（当前为占位，后续可扩展 Boss API 自动发布）
4. 回复 HR：「收到指挥官。JD 已生成并发布至 Boss 直聘；『必须统招本科』的规则已烙印至评审中枢。狩猎已开始。🚀」

### 1.3 多 Agent 依据

- **brain_filter**（小脑粗筛）：使用 `hr_criteria` 做学历、年限底线过滤
- **三专家评审**（tribunal）：使用 `hr_criteria` 分析候选人是否符合

---

## 二、全天候雷达：实时进度汇报系统（下行）

### 2.1 状态机基座：recruitment_status.json

底层爬虫工具在运行过程中不断更新此文件，包含：

- `job_title`、`unprocessed_pdfs`、`batch_limit`、`cron_trigger_time`
- `scanned_online_count`、`greeted_count`、`total_processed`
- `last_milestone_notified`、`last_progress_notify_time`

### 2.2 MCP 原子工具：atom_lark_notifier

- **职责**：接收 JSON 数据，调用飞书 Message API，向指定 HR 发送结构化消息卡片
- **优点**：消息卡片可带进度条、颜色标记，视觉体验优于纯文本

### 2.3 汇报模式 A：里程碑式主动推送

由 `cron_thinker` 触发，在 `run_daily_patrol` 后检查：

- 当 `unprocessed_pdfs` 达到 10、20、30... 等里程碑节点
- 或每日下班前（如 18:00）
- 触发 `atom_lark_notifier`，发送飞书卡片

**卡片示例**：
```
📢 【前端开发】自动狩猎进度播报
🎯 目标进度：24% (12 / 50)
📡 雷达扫描：已阅览 150 份在线档案
💬 沟通转化：向 45 位牛人打招呼，12 人已提交完整附件
📋 累计已审：142 份
系统正在持续狩猎中，预计明早 8:30 触发首轮多 Agent 交叉评审。
```

### 2.4 汇报模式 B：HR 随时随地被动查询

**Skills**：hr-progress-query

HR 在飞书问：「前端岗现在收了多少简历了？」「现在几个了？」

1. 调用 `atom_get_progress` 获取 recruitment_status
2. 生成人性化回复

**示例**：「报告，目前刚刚突破 10 份大关！我们已经给 30 个匹配度极高的人打了招呼，等他们晚上下班回复，明早库房绝对充实！」

---

## 三、MCP 新增原子工具

| 工具 | 职责 |
|------|------|
| nat_lang_to_jd | 将自然语言解析为结构化 JD 与 hr_criteria，保存到 hr_rules 与 recruitment_status |
| atom_post_job | 保存 JD 到 pending_jobs（占位，后续可扩展 Boss 自动发布） |
| atom_lark_notifier | 向飞书发送进度汇报消息卡片 |
| atom_get_progress | 获取 recruitment_status，供被动查询使用 |

---

## 四、配置

在 `.env` 中配置：

```
LARK_APP_ID=
LARK_APP_SECRET=
LARK_CHAT_ID=   # 接收进度卡片的群聊 chat_id
```

---

## 五、典型用户体验流

1. **[10:00 AM]** HR 在飞书发消息：「招个前端，20k，只要统招本科，要有大厂外包经验也行。」
2. **[10:01 AM]** 系统回复：「收到指挥官。JD 已生成并发布至 Boss 直聘；『必须统招本科』的规则已烙印至评审中枢。狩猎已开始。🚀」
3. **[14:00 PM]** 系统主动推送卡片：「📢 进度更新：前端岗位目前已扫描 80 人，成功获取 5 份有效 PDF 附件。持续推进中。」
4. **[16:30 PM]** HR 在飞书问：「现在几个了？」
5. **[16:30 PM]** 系统回复：「报告，目前刚刚突破 10 份大关！我们已经给 30 个匹配度极高的人打了招呼，等他们晚上下班回复，明早库房绝对充实！」
