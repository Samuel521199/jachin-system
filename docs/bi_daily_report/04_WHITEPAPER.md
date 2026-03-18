# 11 — 每日 BI 深度分析战报：数据增长官的自动化心脏

**文档类型**: 白皮书 · 业务模块扩充  
**版本**: v1.0  
**更新日期**: 2026-03  
**状态**: 设计规范，待实现  
**定位**: 高内聚低耦合的独立 Skill，不侵入 HR 招聘核心

---

## 一、 业务价值：为何需要每日 BI 战报

高管每日晨会前，往往需要快速掌握昨日业务盘面、异动归因与行动建议。人工整理耗时耗力，且易遗漏关键信号。

| 痛点 | 传统方式 | BI 战报 Skill |
|------|----------|---------------|
| **数据分散** | 多系统后台、Excel 手工汇总 | 自动抓取、统一落库 |
| **洞察滞后** | 人工分析需数小时 | 8 点准时推送，开晨会前即达 |
| **归因浅薄** | 只看数字，不懂「为什么」 | LLM 深度归因 + 战略建议 |
| **分发割裂** | 邮件、群聊各自发 | 飞书 + 邮件双通道，原始数据附件 |

**目标**：将「首席数据增长官」的思考能力固化为一套自动化流程，每日早晨 8 点准时产出「昨日核心盘面 → 异动归因 → 战略建议」战报，并推送到高管群与指定邮箱。

---

## 二、 架构定位：与现有系统的关系

### 2.1 在 Jachin 体系中的位置

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Layer 3 单体执行节点 (L3 Node)                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐ │
│  │ recruitment_scheduler│   │ bi.scheduler        │   │ 其他调度器...       │ │
│  │ (HR 招聘定时任务)    │   │ (BI 战报定时任务)   │   │                     │ │
│  └──────────┬──────────┘   └──────────┬──────────┘   └─────────────────────┘ │
│             │                         │                                       │
│             ▼                         ▼                                       │
│  ┌─────────────────────┐   ┌─────────────────────┐                           │
│  │ recruitment_task    │   │ skill_bi_daily_report│  ← 完全独立，零耦合       │
│  │ (HR 招聘逻辑)       │   │ (BI 战报逻辑)       │                           │
│  └──────────┬──────────┘   └──────────┬──────────┘                           │
│             │                         │                                       │
│             ▼                         ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ 通用 MCP 工具层 (l3_node/mcp_tools/bi/)                                  │ │
│  │ atom_web_scraper | atom_lark_notifier | atom_email_sender                │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 架构红线

| 红线 | 说明 |
|------|------|
| **不修改 HR 逻辑** | `recruitment_task.py`、`recruitment_scheduler.py` 及其依赖的 MCP 工具，一律不触碰 |
| **MCP 工具通用化** | `atom_lark_notifier` 等工具仅负责「发什么、发到哪」，严禁硬编码 BI 战报格式 |
| **调度隔离** | `bi/scheduler.py` 与 `recruitment_scheduler.py` 互不 import，可独立启停 |
| **配置分离** | BI 配置独立于 HR 配置，如 `config/bi_daily_report.yaml` |

---

## 三、 技术架构：三阶段设计

### 3.1 阶段一：通用 MCP 工具（可复用原子能力）

| 工具 | MCP ID | 职责 | 复用场景 |
|------|--------|------|----------|
| atom_web_scraper | mcp:atom_web_scraper | 按 URL + 规则抓取网页/表格，输出 JSON/CSV | 竞品监控、周报数据、舆情抓取 |
| atom_lark_notifier | mcp:atom_lark_notifier | webhook_url + markdown_content 发送飞书消息 | 告警、周报、任意播报 |
| atom_email_sender | mcp:atom_email_sender | SMTP + 收件人 + 正文 + 附件发送邮件 | 周报、数据导出、审批通知 |

**设计原则**：参数化、无业务语义、可被任意 Skill 组合调用。

### 3.2 阶段二：业务 Skill（技能大脑）

`skill_bi_daily_report.py` 作为统筹全局的「技能大脑」，编排四步流程：

| 步骤 | 名称 | 输入 | 输出 | 依赖 |
|------|------|------|------|------|
| A | 收集 | URL、提取规则 | raw/{date}.json | atom_web_scraper |
| B | 对比提炼 | raw 文件 | metrics_data | 纯 Python 计算 |
| C | LLM 深度洞察 | metrics_data + external_context | 战报 markdown | LiteLLM |
| D | 分发 | 战报 + 附件路径 | 飞书 + 邮件 | atom_lark_notifier、atom_email_sender |

### 3.3 阶段三：调度挂载（心脏起搏器）

| 项目 | 说明 |
|------|------|
| **文件** | `l3_node/bi/scheduler.py`（与 recruitment_scheduler 同级） |
| **触发** | CronTrigger，每天 8:00 |
| **入口** | `run_bi_daily_report(config)` |
| **启动** | 由 L3 主进程在启动时 import 并注册 |

---

## 四、 数据流与存储

### 4.1 目录结构

```
~/.jachin/client_volumes/bi_data/
├── raw/                    # 原始抓取数据
│   ├── 2026-03-09.json     # 昨日
│   ├── 2026-03-08.json     # 前日
│   └── 2026-03-02.json     # 上周同期
└── metrics/                # 计算后的指标
    └── 2026-03-09_metrics.json
```

### 4.2 数据流

```
业务后台/API
    │
    ▼ atom_web_scraper
raw/{date}.json
    │
    ▼ skill_bi_daily_report (Step B)
metrics_data (同环比、涨跌幅、极值)
    │
    ▼ LiteLLM (Step C)
战报 markdown
    │
    ├─► atom_lark_notifier → 飞书高管群
    └─► atom_email_sender  → 指定邮箱（含 raw CSV 附件）
```

---

## 五、 部署与配置

### 5.1 环境变量（可选覆盖配置文件）

| 变量 | 说明 |
|------|------|
| `BI_DATA_URL` | 业务数据源 URL（后台报表、API） |
| `BI_LARK_WEBHOOK_URL` | 飞书群机器人 incoming webhook |
| `BI_SMTP_HOST` | SMTP 服务器 |
| `BI_SMTP_USER` | 发件人账号 |
| `BI_SMTP_PASSWORD` | 发件人密码 |
| `BI_EMAIL_TO` | 收件人邮箱（逗号分隔） |

### 5.2 配置文件（推荐）

`config/bi_daily_report.yaml`：

```yaml
data_source:
  url: "https://your-backend.com/api/daily-report"
  extract_rules: {}
  headers: {}

storage:
  base_path: "client_volumes/bi_data"

distribution:
  lark_webhook_url: "${BI_LARK_WEBHOOK_URL}"
  email:
    smtp_host: "${BI_SMTP_HOST}"
    smtp_user: "${BI_SMTP_USER}"
    smtp_password: "${BI_SMTP_PASSWORD}"
    to_addrs: ["exec@company.com"]
```

### 5.3 依赖

| 依赖 | 用途 |
|------|------|
| requests / httpx | 网页抓取、飞书 webhook |
| beautifulsoup4 / lxml | HTML 解析（可选） |
| apscheduler | 定时任务（已有） |
| LiteLLM | 大模型调用（已有） |

---

## 六、 与现有规范的衔接

| 规范 | 衔接方式 |
|------|----------|
| [MCP_SPEC.md](../MCP_SPEC.md) | 三个 atom 工具按 MCP 规范注册，invoke 时路由到 `l3_node/mcp_tools/bi/` |
| [SKILL_MD_SPEC.md](../SKILL_MD_SPEC.md) | 可选：为 skill_bi_daily_report 编写 SKILL.md 声明，供 Agent 发现与触发 |
| [07_LAYER3_TERMINAL.md](../whitepaper/07_LAYER3_TERMINAL.md) | BI 战报作为 L3 单体上的独立 Skill，复用 MCP + LiteLLM 能力 |
| [08_JPP_SDK_AND_SKILLS.md](../whitepaper/08_JPP_SDK_AND_SKILLS.md) | 轨道 A (MCP) 提供原子工具，BI Skill 为轨道 A 之上的业务编排 |

---

## 七、 后续扩展方向

| 方向 | 说明 |
|------|------|
| **多数据源** | 支持多个 URL、多表合并，扩展 metrics 计算维度 |
| **周报/月报** | 复用 atom_* 工具，新增 skill_bi_weekly_report |
| **竞品监控** | atom_web_scraper 抓取竞品页面，独立 Skill 做对比分析 |
| **告警联动** | 异动超阈值时，atom_lark_notifier 发送紧急告警 |

---

## 八、 相关文档

- [01_PARALLEL_DEVELOPMENT_GUIDE.md](./01_PARALLEL_DEVELOPMENT_GUIDE.md) — **多兵种协同作战指南**（契约、任务分发、技能调用规则）
- [03_SKILL_DESIGN.md](./03_SKILL_DESIGN.md) — 详细设计规范（接口、参数、流程）
- [02_PARALLEL_DEVELOPMENT_ANALYSIS.md](./02_PARALLEL_DEVELOPMENT_ANALYSIS.md) — 深度分析与风险控制
- [MCP_SPEC.md](../MCP_SPEC.md) — MCP 接入规范
