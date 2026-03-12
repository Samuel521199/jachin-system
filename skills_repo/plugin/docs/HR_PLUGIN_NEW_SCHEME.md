# HR 插件新增方案 - 雷达粗筛与双触发引擎

基于招聘网站简历实时更新、需周期收集的特点，将流程拆分为三层 + 双触发引擎。

---

## 一、三层架构

### 第一层：雷达粗筛与撒网（在线简历处理）

- **触发**：cron_thinker 每 30 分钟
- **数据源**：推荐牛人、搜索人才库、主动打招呼消息列表
- **动作**：
  1. 抓取残缺的在线文本简历（非附件）
  2. 小脑粗筛：qwen3.5-flash-2026-02-23 云端底线过滤（学历、年限）
  3. 对底线合格者自动打招呼：「您好，我们对您的经历很感兴趣，方便发一份简历过来吗」
  4. 对主动打招呼者：「方便发一份简历过来吗」

### 第二层：意向确认与收网归档（PDF 附件获取）

- **触发**：与第一层并行，cron_thinker 每次检查
- **数据源**：消息/沟通列表
- **动作**：
  1. 扫描未读消息，剔除不回复的（无意向死库）
  2. 对仅回复文字的人，可追问催收 PDF
  3. 下载 PDF 附件，调用 local_archiver 保存到 `~/.jachin/workspace/resumes/pending/`

### 第三层：诸神黄昏（Wasm 虫群角斗场）

- **触发**：双触发引擎（见下）
- **动作**：pending 下 PDF 打包送入轨道 C Wasm，三专家评审，结果移入 processed，同步 Lark 多维表
- **Lark 输出**：以表格形式输出，从上至下按推荐程度排名，含推荐理由和 PDF 附件链接；Top 10 最终推荐候选人信息实时更新；HR 手动选取要面试的候选人后，由 AI 安排面试

---

## 二、双触发引擎

状态看板：`~/.jachin/workspace/recruitment_status.json`

```json
{
  "job_title": "Java开发",
  "status": "hunting",
  "batch_limit": 50,
  "cron_trigger_time": "08:30",
  "unprocessed_pdfs": 8,
  "total_processed": 142
}
```

**触发条件（满足其一即执行终局审判）**：

1. **满载溢出**：`unprocessed_pdfs >= batch_limit`（50 份）
2. **每日早报**：当前时间到达 `cron_trigger_time`（如 08:30）

---

## 三、轨道 A 原子工具

| 工具 | 职责 |
|------|------|
| atom_radar_scraper | 抓取推荐牛人/搜索的在线文本简历 |
| atom_auto_greeter | 自动发送打招呼话术 |
| atom_inbox_harvester | 扫描消息列表，下载 PDF 附件 |
| local_archiver | 将 PDF 保存到 pending |
| brain_filter | 小脑粗筛（qwen3.5-flash-2026-02-23 云端底线过滤） |
| nat_lang_to_jd | 自然语言解析为 JD 与评审规则，烙印至 hr_rules |
| atom_post_job | 发布岗位（当前保存到 workspace，可扩展 Boss API） |
| atom_lark_notifier | 向飞书发送进度汇报消息卡片 |
| atom_get_progress | 获取 recruitment_status，供 HR 被动查询 |

---

## 四、轨道 B 多意图

### 意图一：零阶漏斗 - 自然语言发帖（HR 在飞书发消息触发）

1. nat_lang_to_jd 解析 HR 自然语言 → 生成 JD、hr_criteria
2. atom_post_job 保存 JD 至 workspace
3. 规则烙印至 recruitment_status.json，多 Agent 据此筛选

### 意图二：日常巡逻（cron_thinker 触发）

1. atom_radar_scraper → brain_filter（使用 recruitment_status 中 hr_criteria）→ atom_auto_greeter
2. atom_inbox_harvester → local_archiver → 更新 recruitment_status.json
3. 里程碑时 atom_lark_notifier 主动推送进度卡片

### 意图三：被动查询进度（HR 在飞书问「现在几个了？」）

1. atom_get_progress 读取 recruitment_status
2. 生成人性化回复

### 意图四：终局审判（双触发达标时）

1. 读取 pending 下 PDF 文本
2. 脱敏 → Wasm 虫群（使用 recruitment_status 中 hr_criteria）→ lark_bitable_sync（表格形式，按推荐程度排序，含推荐理由和 PDF 链接，Top 10 实时更新）
3. HR 在 Lark 多维表中手动选取要面试的候选人，后续由 AI 安排面试

---

## 五、运行方式

```bash
# 日常巡逻（雷达 + 收网）
python scripts/cron_runner.py --patrol --job "Java开发"

# 终局审判（强制，无视触发条件）
python scripts/cron_runner.py --force-judge --lark-sheet "xxx"

# 检查是否应触发终局
python -c "
from tools.recruitment_status import should_trigger_final_judgment
ok, reason = should_trigger_final_judgment('08:30')
print(ok, reason)
"
```

---

## 六、定时任务配置

**Linux/Mac cron**（每 30 分钟巡逻，每天 8:30 审判）：

```
*/30 * * * * cd /path/to/plugin && python scripts/cron_runner.py --patrol --job "Java开发"
30 8 * * * cd /path/to/plugin && python scripts/cron_runner.py --judge --lark-sheet "xxx"
```

**Windows 任务计划程序**：创建两个任务，分别对应上述命令。
