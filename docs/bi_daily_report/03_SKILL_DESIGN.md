# 每日 BI 深度分析战报 — Skill 设计文档

**版本**: 1.0  
**状态**: 设计规范（待实现）  
**定位**: 独立业务 Skill，高内聚低耦合，不侵入现有 HR 招聘逻辑

---

## 一、设计原则

| 原则 | 说明 |
|------|------|
| **高内聚** | BI 战报全流程封装于单一 Skill，数据收集→计算→洞察→分发闭环 |
| **低耦合** | 依赖通用 MCP 工具，无业务硬编码；与 recruitment_scheduler 完全隔离 |
| **可复用** | MCP 工具参数化设计，可供其他 Skill（如周报、竞品监控）复用 |
| **架构红线** | 禁止修改 `recruitment_task.py`、`recruitment_scheduler.py`、`mcp_registry` 中 HR 相关逻辑 |

---

## 二、阶段一：通用 MCP 工具

**存放路径**: `l3_node/mcp_tools/bi/`  
**注册前缀**: `mcp:`  
**目录规范**: 详见 [08_BI_MCP_AND_SKILL_LAYOUT.md](./08_BI_MCP_AND_SKILL_LAYOUT.md)

### 2.1 atom_web_scraper（通用网页抓取器）

| 项目 | 说明 |
|------|------|
| **MCP ID** | `mcp:atom_web_scraper` |
| **功能** | 传入目标 URL 和提取规则，抓取网页/后台表格数据，保存为 JSON/CSV |
| **通用性** | 参数化 URL、规则、输出路径，无 BI 业务逻辑 |

**参数 Schema**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 目标网页/API 地址 |
| `extract_rules` | object/string | 否 | 提取规则：XPath、CSS 选择器、或 JSONPath；为空时抓取全文 |
| `output_path` | string | 是 | 本地保存路径（支持 `client_volumes/` 下的相对路径） |
| `output_format` | string | 否 | `json` \| `csv`，默认 `json` |
| `headers` | object | 否 | 自定义 HTTP 请求头（如 Cookie、Authorization） |
| `timeout` | number | 否 | 请求超时秒数，默认 30 |

**输出**: `{ "success": bool, "path": str, "rows_count": int, "error": str }`

**约束**: 输出路径必须在 `~/.jachin/client_volumes/` 或 `workspace` 下，禁止写入系统目录。

---

### 2.2 atom_lark_notifier（通用飞书播报员）

| 项目 | 说明 |
|------|------|
| **MCP ID** | `mcp:atom_lark_notifier` |
| **功能** | 传入 webhook_url 和 markdown_content，调用飞书机器人 API 发送富文本卡片或 Markdown 消息 |
| **通用性** | 仅负责「发什么、发到哪」，不包含任何 BI 战报格式逻辑 |

**参数 Schema**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `webhook_url` | string | 是 | 飞书群机器人「 incoming webhook」地址，或留空时用 `LARK_WEBHOOK_URL` 环境变量 |
| `markdown_content` | string | 是 | Markdown 格式正文 |
| `title` | string | 否 | 卡片标题 |
| `msg_type` | string | 否 | `markdown` \| `interactive`（富文本卡片），默认 `markdown` |

**备选方案**: 若需发送到指定 chat_id（非 webhook 群），可支持 `chat_id` + `LARK_APP_ID`/`LARK_APP_SECRET`，调用 `im/v1/messages` 接口。优先实现 webhook 模式（零配置）。

**输出**: `{ "success": bool, "error": str }`

**架构红线**: 严禁在工具内硬编码「昨日核心盘面」「战略建议」等 BI 文案；内容 100% 由调用方传入。

---

### 2.3 atom_email_sender（通用邮件发射器）

| 项目 | 类型 | 说明 |
|------|------|------|
| **MCP ID** | `mcp:atom_email_sender` |
| **功能** | 传入 SMTP 配置、收件人、正文、附件路径，发送邮件 |
| **通用性** | 参数化配置，可用于周报、告警、数据导出等任意场景 |

**参数 Schema**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `smtp_host` | string | 是 | SMTP 服务器地址 |
| `smtp_port` | number | 否 | 默认 587（TLS）或 465（SSL） |
| `smtp_user` | string | 是 | 发件人账号 |
| `smtp_password` | string | 是 | 发件人密码（或环境变量引用） |
| `to_addrs` | array[string] | 是 | 收件人邮箱列表 |
| `subject` | string | 是 | 邮件主题 |
| `body_html` | string | 否 | HTML 正文 |
| `body_markdown` | string | 否 | Markdown 正文（需转换为 HTML） |
| `attachment_paths` | array[string] | 否 | 附件本地路径列表（如 CSV 文件） |
| `use_tls` | bool | 否 | 默认 true |

**输出**: `{ "success": bool, "error": str }`

**约束**: 附件路径必须在 `client_volumes` 或 `workspace` 下。

---

## 三、阶段二：业务 Skill

**存放路径**: `l3_node/skills/bi/bi_daily_report/main_skill.py`  
**定位**: 统筹全局的「技能大脑」，编排 MCP 调用与 LLM 洞察  
**目录规范**: 详见 [08_BI_MCP_AND_SKILL_LAYOUT.md](./08_BI_MCP_AND_SKILL_LAYOUT.md)

### 3.1 主流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  run_bi_daily_report(config: BiReportConfig) -> dict                         │
├─────────────────────────────────────────────────────────────────────────────┤
│  Step A: 收集 (atom_web_scraper)                                             │
│    → 抓取昨日业务数据 URL                                                     │
│    → 存入 client_volumes/bi_data/raw/{date}.json 或 .csv                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  Step B: 对比提炼 (纯 Python)                                                │
│    → 读取今日、昨日、上周同期 raw 文件                                        │
│    → 计算同环比、涨跌幅、极值 → metrics_data                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Step C: LLM 深度洞察                                                        │
│    → external_context = { 当前日期, 星期几, 节假日标记 }                       │
│    → System Prompt: 首席数据增长官，输出格式固定                               │
│    → 输入: metrics_data + external_context                                    │
│    → 输出: 战报正文 (markdown)                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  Step D: 分发                                                                │
│    → atom_lark_notifier(webhook_url, 战报 markdown)                           │
│    → atom_email_sender(..., attachment_paths=[raw CSV 路径])                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 配置结构 BiReportConfig

```python
# 伪代码，实际以 dict 或 dataclass 实现
BiReportConfig = {
    "data_source": {
        "url": str,           # 业务数据 URL（如后台报表 API）
        "extract_rules": {},  # 可选
        "headers": {},        # 可选，Cookie 等
    },
    "storage": {
        "base_path": "client_volumes/bi_data",  # raw/ 与 metrics/ 父目录
    },
    "llm": {
        "model": "dashscope/qwen3.5-plus",  # 可选覆盖；默认与 L3 主推理一致
    },
    "distribution": {
        "lark_webhook_url": str,
        "email": {
            "smtp_host": str,
            "smtp_user": str,
            "smtp_password": str,  # 或 env 引用
            "to_addrs": [str],
        },
    },
}
```

### 3.3 LLM System Prompt（固定）

```
你是首席数据增长官。请基于数据给出极具商业价值的深度归因和战略建议，严禁废话。

输出格式必须包含以下三个部分（使用对应 emoji 标题）：
1. 📊昨日核心盘面
2. 🔍异动深度归因
3. 💡战略级行动建议

数据与上下文由调用方注入，你仅负责分析与建议，不要编造数据。
```

### 3.4 数据目录结构

```
~/.jachin/client_volumes/bi_data/
├── raw/
│   ├── 2026-03-09.json      # 昨日
│   ├── 2026-03-08.json      # 前日
│   └── 2026-03-02.json      # 上周同期
└── metrics/
    └── 2026-03-09_metrics.json   # 计算后的 metrics_data
```

---

## 四、阶段三：调度器挂载

### 4.1 新建 bi_scheduler.py

**路径**: `l3_node/bi/scheduler.py`（与 `recruitment_scheduler.py` 同级）

**职责**:
- 独立模块，不 import recruitment_scheduler 内部逻辑
- 使用 APScheduler 的 CronTrigger
- 调用 `skill_bi_daily_report.run_bi_daily_report(config)`

**任务注册**:

```python
# 伪代码
scheduler.add_job(
    run_bi_daily_report_job,
    'cron',
    hour=8,
    minute=0,
    id='bi_daily_report',
    replace_existing=True,
)
```

### 4.2 与 recruitment_scheduler 的关系

| 项目 | 说明 |
|------|------|
| **隔离** | bi_scheduler 与 recruitment_scheduler 互不依赖 |
| **启动** | 由 `l3_node/__main__.py` 或 `http_server.py` 在启动时 import 并注册 |
| **配置** | BI 配置独立存放，如 `config/bi_daily_report.yaml` 或环境变量 |

### 4.3 配置加载

- 优先从 `config/bi_daily_report.yaml` 读取
- 支持环境变量覆盖：`BI_LARK_WEBHOOK_URL`、`BI_SMTP_*`、`BI_DATA_URL` 等

### 4.4 调度配置（schedule）

| 字段 | 说明 | 默认 |
|------|------|------|
| `enabled` | 是否启用定时任务，`false` 时仅支持手动触发 | `true` |
| `mode` | `cron` 固定时间 / `interval` 间隔执行 | `cron` |
| `hour` | cron 模式：小时 (0-23) | `8` |
| `minute` | cron 模式：分钟 (0-59) | `0` |
| `timezone` | cron 模式：时区，如 `Asia/Shanghai`（UTC+8） | `Asia/Shanghai` |
| `minutes` | interval 模式：每 N 分钟 | - |
| `hours` | interval 模式：每 N 小时 | - |

**示例**：每天 8:00 UTC+8 发日报；或每 30 分钟 / 每 2 小时发一次。

---

## 五、MCP 工具注册

在 `l3_node/skills/mcp_registry.py` 的 `L3_LOCAL_MCP_TOOLS` 中**追加**（不修改现有 HR 工具）：

```python
{
    "id": "mcp:atom_web_scraper",
    "label": "mcp:atom_web_scraper",
    "desc": "[L3 本地] 通用网页抓取器。传入 url、extract_rules、output_path，抓取表格/JSON 并保存。",
    "params": ["url", "extract_rules", "output_path", "output_format", "headers", "timeout"],
},
{
    "id": "mcp:atom_lark_notifier",
    "label": "mcp:atom_lark_notifier",
    "desc": "[L3 本地] 通用飞书播报员。传入 webhook_url 和 markdown_content，发送 Markdown 或富文本卡片。",
    "params": ["webhook_url", "markdown_content", "title", "msg_type"],
},
{
    "id": "mcp:atom_email_sender",
    "label": "mcp:atom_email_sender",
    "desc": "[L3 本地] 通用邮件发射器。传入 SMTP 配置、收件人、正文、附件路径，发送邮件。",
    "params": ["smtp_host", "smtp_user", "smtp_password", "to_addrs", "subject", "body_html", "body_markdown", "attachment_paths"],
},
```

**路由**: 在 `mcp_registry` 的 `invoke_mcp_tool` 分支中，根据 `tool_id` 分发到 `l3_node/mcp_tools/bi/` 下对应模块。

---

## 六、依赖与部署

| 依赖 | 说明 |
|------|------|
| `requests` / `httpx` | 网页抓取、飞书 webhook |
| `beautifulsoup4` / `lxml` | HTML 解析、XPath（可选） |
| `apscheduler` | 已有，复用 |
| LLM | 复用 `l3_node/llm_client.py` 的 LiteLLMEngine |

**环境变量**（可选，用于覆盖配置文件）:
- `BI_DATA_URL` — 业务数据源 URL
- `BI_LARK_WEBHOOK_URL` — 飞书群 incoming webhook
- `BI_SMTP_HOST`, `BI_SMTP_USER`, `BI_SMTP_PASSWORD`, `BI_EMAIL_TO`

---

## 七、错误处理与日志

| 阶段 | 失败时行为 |
|------|------------|
| A 收集 | 记录错误，跳过 B/C/D，返回 `{ "success": false, "stage": "collect", "error": "..." }` |
| B 对比 | 若历史文件缺失，降级为仅用当日数据，或跳过 LLM 洞察 |
| C LLM | 超时/异常时重试 1 次，仍失败则返回占位文案并继续 D |
| D 分发 | Lark 与邮件独立，一方失败不影响另一方；记录失败详情 |

---

## 八、技能调用规则

### 8.1 调用入口

| 入口 | 触发条件 | 执行路径 |
|------|----------|----------|
| **定时调度** | 每天 8:00（CronTrigger） | `bi_scheduler` → `run_bi_daily_report()` |
| **手动触发** | 用户/Agent 显式调用 | 直接调用 `run_bi_daily_report(config)` |
| **Agent 对话** | 用户说「生成今日 BI 战报」「跑一下 BI」 | Agent 识别意图 → 调用 `run_bi_daily_report` |

### 8.2 调用前置条件

- MCP 已注册并完成路由
- 配置就绪（`config/bi_daily_report.yaml` 或 `BI_*` 环境变量）
- 数据目录 `client_volumes/bi_data/raw/` 可写

### 8.3 返回值约定

- 全流程成功：`{"success": true, "report_sent": true, "lark_ok": true, "email_ok": true}`
- 阶段失败：`{"success": false, "stage": "collect"|"llm"|"distribute", "error": "..."}`
- 分发部分失败：`lark_ok` / `email_ok` 分别标识

---

## 九、相关文档

- [01_PARALLEL_DEVELOPMENT_GUIDE.md](./01_PARALLEL_DEVELOPMENT_GUIDE.md) — **多兵种协同作战指南**（契约、任务分发、调用规则）
- [02_PARALLEL_DEVELOPMENT_ANALYSIS.md](./02_PARALLEL_DEVELOPMENT_ANALYSIS.md) — 深度分析与风险控制
- [04_WHITEPAPER.md](./04_WHITEPAPER.md) — BI 战报白皮书
- [MCP_SPEC.md](../MCP_SPEC.md) — MCP 接入规范
- [SKILL_MD_SPEC.md](../SKILL_MD_SPEC.md) — Skill 声明式规范
