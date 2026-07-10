# BI 每日战报 — 多兵种协同作战指南

**版本**: 1.0
**状态**: 团队基石文档（代码实现前必读）
**定位**: 面向契约编程 + 绝对沙盒隔离，实现多开发者/AI 并行开发零冲突

---

## 一、 核心理念：合则天下无敌，分则各自为王

> 让多个开发者（甚至多个 AI）同时在一个仓库里并行开发，还不产生 Git 冲突和代码污染，这是现代软件工程的最高殿堂。

**解法**：**面向契约编程 (API-First Design) + 绝对沙盒隔离**。

| 原则 | 说明 |
|------|------|
| **契约先行** | 任何人写代码前，统帅先定下输入/输出 JSON 格式。只要契约对得上，内部实现互不污染 |
| **物理隔离** | A 只碰 `tool_web_scraper.py`，B 只碰 `tool_broadcaster.py`，C 只碰 `bi_daily_report/` 目录 |
| **Mock 先行** | C 可用假数据先开发，不等 A/B 完成；A/B 交码后无缝对接 |
| **资产沉淀** | MCP 工具通用化，下个「宕机报警 Skill」可直接复用 B 的飞书推送 |

---

## 二、 《最高接口契约》(团队基石，无需代码)

在任何人开始写代码前，统帅必须先定下规矩。**只要输入输出对得上，内部代码怎么写都不会污染别人**。

### 2.1 抓取组契约 (mcp:atom_web_scraper)

| 项目 | 说明 |
|------|------|
| **输入** | `url`（必填）、`output_path`（必填，或由统帅指定默认）、`config`（可选：extract_rules、output_format、headers、timeout） |
| **输出（成功）** | `{"status": "success", "file_path": "client_volumes/bi_data/raw/20260316.csv"}` |
| **输出（失败）** | `{"status": "error", "error": "错误描述"}` |
| **存储约定** | 数据统一存入 `client_volumes/bi_data/raw/`（基准 `~/.jachin/`），文件名格式 `YYYYMMDD.csv` 或 `YYYYMMDD.json` |

**契约要点**：调用方只需拿到 `file_path`，即可在后续步骤中读取；抓取组不关心数据用途。

---

### 2.2 通知组契约 — 飞书 (mcp:atom_lark_notifier)

| 项目 | 说明 |
|------|------|
| **输入** | `webhook_url`（必填）、`markdown_content`（必填）、`title`（可选） |
| **输出（成功）** | `{"status": "success", "msg": "飞书已送达"}` |
| **输出（失败）** | `{"status": "error", "error": "错误描述"}` |

**契约要点**：通知组**不需要知道**推的是什么内容；严禁在工具内硬编码 BI 战报格式。

---

### 2.3 通知组契约 — 邮件 (mcp:atom_email_sender)

| 项目 | 说明 |
|------|------|
| **输入** | `smtp_config`（host、port、user、password）、`to_addrs`、`subject`、`body`、`attachment_paths`（可选） |
| **输出（成功）** | `{"status": "success", "msg": "邮件已发送"}` |
| **输出（失败）** | `{"status": "error", "error": "错误描述"}` |

**契约要点**：附件路径由调用方传入，工具仅负责发送；可用于任意场景（周报、告警、数据导出）。

---

## 三、 物理战区划分（绝对隔离）

| 战区 | 负责人 | 唯一可修改的文件/目录 | Git 分支建议 |
|------|--------|------------------------|--------------|
| **A：数据收割** | 开发者 A | `l3_node/primitives/mcp/mcp_tools/bi/tool_web_scraper.py` | `feat/bi-scraper` |
| **B：全息广播** | 开发者 B | `l3_node/primitives/mcp/mcp_tools/bi/tool_lark_notifier.py`、`tool_email_sender.py` | `feat/bi-broadcaster` |
| **C：BI 智库** | 开发者 C / 统帅 | `l3_node/primitives/skills/bi/`、`l3_node/primitives/mcp/mcp_tools/bi/` | `feat/bi-skill` |

**合并顺序**：A、B 可任意顺序合并；C 依赖 A、B 的 MCP 注册与路由，建议 A、B 合并后再合并 C，或 C 用 Mock 先行开发。

---

## 3.1 统帅预备（开发前必做）

在 A、B、C 开始写代码前，统帅必须完成：

| 步骤 | 说明 |
|------|------|
| **预创建目录** | 创建 `l3_node/primitives/mcp/mcp_tools/` 及空 `__init__.py`，避免 A、B 同时创建产生冲突 |
| **路径基准统一** | 明确数据目录：`client_volumes/bi_data/raw/`（基准 `~/.jachin/client_volumes/`）或 `data/bi_raw_pool/`（基准 `get_app_root()`） |
| **参数名统一** | 契约与设计文档对齐：飞书用 `markdown_content`，邮件用 `to_addrs`，抓取用 `url` + `output_path` |
| **依赖规范** | A、B、C **禁止**修改 `core/requirements.txt`；新增依赖在 PR 描述中列出，由统帅组装时统一追加 |

---

## 四、 三份独立指令（分发给协同开发者）

以下三段指令可**直接复制**发给对应开发者，让其各自开分支、在 Cursor 中执行。

---

### 🧑‍💻 开发者 A：负责「数据收割」(前线侦察兵)

**负责范围**：只写抓取 MCP，绝对不碰业务逻辑和其他代码。

> **@workspace**
> **【代号：收割者 MCP】**
> 我们正在并行开发。你的任务是实现一个纯粹的网页/后台数据抓取工具，并注册为 MCP。
>
> 1. **创建文件**：在 `l3_node/primitives/mcp/mcp_tools/bi/` 下新建 `tool_web_scraper.py`。绝对不要修改其他文件。
> 2. **核心功能**：使用 requests/BeautifulSoup 或 Playwright 编写一个通用的抓取函数 `harvest_table_data(url, config)`。
> 3. **落地存储**：将抓取到的数据以 CSV 或 JSON 格式统一存入 `client_volumes/bi_data/raw/`（或统帅指定的 `storage.base_path`），文件名格式 `YYYYMMDD.csv`。目录不存在则创建。
> 4. **MCP 暴露**：在 `tool_web_scraper.py` 内实现可被调用的函数（如 `harvest_table_data(url, output_path, config)`），**不**修改 `mcp_registry.py`。MCP 注册由统帅在组装阶段完成。返回值格式：`{"status": "success", "file_path": "client_volumes/bi_data/raw/20260316.csv"}` 或 `{"status": "error", "error": "..."}`。
> 5. **独立测试**：写一个 `if __name__ == "__main__":` 进行本地测试，跑通即可提交。
>
> **契约约束**：输入 `url`、`output_path`，输出 `{"status": "success", "file_path": "..."}` 或 `{"status": "error", "error": "..."}`。

---

### 🧑‍💻 开发者 B：负责「全息广播」(通讯兵)

**负责范围**：只写飞书和邮件的推送 MCP，不需要知道推的是什么内容。

> **@workspace**
> **【代号：广播者 MCP】**
> 我们正在并行开发。你的任务是实现两个纯粹的通知发送工具，并注册为 MCP。
>
> 1. **创建文件**：在 `l3_node/primitives/mcp/mcp_tools/bi/` 下新建 `tool_lark_notifier.py`、`tool_email_sender.py`。绝对不要修改其他文件。
> 2. **飞书功能**：编写 `send_lark_markdown(webhook_url, title, markdown_content)`，通过 HTTP POST 请求飞书机器人的 Webhook。实现为 `mcp:atom_lark_notifier` 的底层函数（注册由统帅完成）。
> 3. **邮件功能**：编写 `send_email_with_attachment(smtp_config, to_addrs, subject, body, attachment_paths)`，使用 Python `smtplib` 发送带附件的邮件。实现为 `mcp:atom_email_sender` 的底层函数（注册由统帅完成）。
> 4. **异常处理**：做好网络超时和报错的 `try-except`，确保 MCP 返回标准的错误 JSON 而不是直接崩溃。
>
> **契约约束**：飞书输入 `webhook_url`、`markdown_content`，输出 `{"status": "success", "msg": "飞书已送达"}` 或 `{"status": "error", "error": "..."}`。邮件输入 `to_addrs`，输出同理。

---

### 🧑‍💻 开发者 C（或统帅）：负责「BI 智库 Skill」(指挥官/大脑)

**负责范围**：只写商业逻辑和调度，通过调用 A 和 B 写好的 MCP 来完成闭环。即使 A 和 B 还没写完，C 也可以用 Mock 先开发！

> **@workspace**
> **【代号：战略脑 Skill】**
> 我们正在并行开发。你的任务是编写一个独立的 BI 分析技能。底层的抓取和发送 MCP 由其他同事开发，你可以假设 `mcp:atom_web_scraper` 和 `mcp:atom_lark_notifier` 已经存在。
>
> 1. **创建隔离目录**：在 `l3_node/primitives/skills/bi/` 下新建 `bi_daily_report/`，里面包含 `main_skill.py`。
> 2. **数据对比引擎**：编写纯 Python 逻辑，读取 `client_volumes/bi_data/raw/`（或配置的 `storage.base_path`）下的今日和昨日 CSV 文件，计算出同环比、转化率等核心 `metrics` 字典。
> 3. **LLM 洞察引擎**：组装极其专业的商业分析 System Prompt（要求输出 📊核心盘面、🔍深度归因、💡行动建议的 Markdown）。把算好的 `metrics` 喂给大模型获取分析战报。
> 4. **技能串联编排**：编写主函数 `run_bi_daily_report()`，按顺序执行：读数据 -> 算指标 -> 调 LLM 生成战报 -> 调用 `mcp:atom_lark_notifier` 推送。
> 5. **心跳挂载**：在 `l3_node/primitives/skills/bi/scheduler.py` 中，引入 APScheduler，将 `run_bi_daily_report` 设置为每天早上 8:00 定时执行的任务。
>
> **Mock 策略**：若 MCP 尚未就绪，可在本地伪造 `client_volumes/bi_data/raw/` 下的 CSV，以及 Mock `mcp_registry.invoke` 的返回值（严格按契约 JSON 格式），直接调测 LLM Prompt 与编排逻辑。

---

## 五、 技能调用规则

### 5.1 调用方式（三种入口）

| 入口 | 触发条件 | 执行路径 |
|------|----------|----------|
| **定时调度** | 每天 8:00（CronTrigger） | `bi_scheduler` → `run_bi_daily_report()` |
| **手动触发** | 用户/Agent 显式调用 | 直接调用 `run_bi_daily_report(config)` |
| **Agent 对话** | 用户说「生成今日 BI 战报」「跑一下 BI」 | Agent 识别意图 → 调用 `run_bi_daily_report` |

### 5.2 调用前置条件

| 条件 | 说明 |
|------|------|
| **MCP 已注册** | `mcp:atom_web_scraper`、`mcp:atom_lark_notifier`、`mcp:atom_email_sender` 已加入 `L3_LOCAL_MCP_TOOLS` 并完成路由 |
| **配置就绪** | `config/bi_daily_report.yaml` 或环境变量 `BI_*` 已配置数据源 URL、飞书 webhook、SMTP |
| **数据目录可写** | `client_volumes/bi_data/raw/`（`~/.jachin/client_volumes/bi_data/raw/`）存在且可写 |

### 5.3 调用返回值约定

| 场景 | 返回值 |
|------|--------|
| **全流程成功** | `{"success": true, "report_sent": true, "lark_ok": true, "email_ok": true}` |
| **收集失败** | `{"success": false, "stage": "collect", "error": "..."}` |
| **LLM 失败** | `{"success": false, "stage": "llm", "error": "..."}`；可降级为占位文案继续分发 |
| **分发部分失败** | `{"success": true, "report_sent": true, "lark_ok": true, "email_ok": false, "email_error": "..."}` |

### 5.4 与 MCP 的调用关系

```
run_bi_daily_report()
    │
    ├─► mcp_registry.invoke("mcp:atom_web_scraper", {"url": "...", "config": {...}})
    │       → 期望返回 {"status": "success", "file_path": "client_volumes/bi_data/raw/20260316.csv"}
    │
    ├─► [纯 Python] 读取 file_path，计算 metrics
    │
    ├─► [LiteLLM] 将 metrics 喂给大模型，生成战报 markdown
    │
    ├─► mcp_registry.invoke("mcp:atom_lark_notifier", {"webhook_url": "...", "markdown_content": 战报})
    │       → 期望返回 {"status": "success", "msg": "飞书已送达"}
    │
    └─► mcp_registry.invoke("mcp:atom_email_sender", {smtp_config, to_addrs, subject, body, attachment_paths})
            → 期望返回 {"status": "success", "msg": "邮件已发送"}
```

---

## 六、 模块组装（统帅收尾）

当 A、B、C 三个分支均合并后，统帅需完成**模块组装**（一次性工作）：

| 步骤 | 说明 |
|------|------|
| **1. MCP 路由** | 在 `mcp_registry.py` 的 `invoke()` 中，为 `atom_web_scraper`、`atom_lark_notifier`、`atom_email_sender` 添加分支，路由到 `l3_node/primitives/mcp/mcp_tools/bi/` 下对应函数 |
| **2. 工具注册** | 在 `L3_LOCAL_MCP_TOOLS` 中追加三个工具的 id、desc、params |
| **3. 调度启动** | 在 L3 主进程启动逻辑中，`from l3_node.primitives.skills.bi.scheduler import register_bi_daily_report_job` 以注册定时任务 |
| **4. 路径统一** | 统一使用 `client_volumes/bi_data/raw/`，在 `BiReportConfig.storage.base_path` 或常量中指定 |

**注意**：A、B 开发者**禁止**修改 `mcp_registry.py`，以避免与 HR 逻辑冲突；路由与注册由统帅在组装阶段完成。

---

## 七、 路径与命名对照表

| 设计文档 | 并行开发指南 | 说明 |
|----------|--------------|------|
| `atom_web_scraper.py` | `tool_web_scraper.py` | 可二选一，组装时统一；MCP ID 固定为 `mcp:atom_web_scraper` |
| `atom_lark_notifier.py` + `atom_email_sender.py` | `tool_broadcaster.py`（单文件含两者） | 可二选一；MCP ID 固定为 `mcp:atom_lark_notifier`、`mcp:atom_email_sender` |
| `client_volumes/bi_data/raw/` | `client_volumes/bi_data/raw/` | **已统一**：基准 `~/.jachin/`，由 `BiReportConfig.storage.base_path` 指定 |
| `skill_bi_daily_report.py` | `bi_daily_report/main_skill.py` | 目录结构更利于隔离；入口函数统一为 `run_bi_daily_report()` |

---

## 八、 完整开发流程（五阶段）

| 阶段 | 负责方 | 动作 |
|------|--------|------|
| **0. 统帅预备** | 统帅 | 创建 `mcp_tools/`、统一路径与参数名、明确依赖规范 |
| **1. A、B 并行** | A、B | 各自开发，PR 仅含约定文件，不修改 mcp_registry、requirements |
| **2. C 开发** | C | 可 Mock 先行；A、B 合并后可联调真实 MCP |
| **3. 统帅组装** | 统帅 | 合并 C → mcp_registry 路由与注册 → 调度启动 → 路径统一 |
| **4. 联调验收** | 统帅 | 端到端跑通、契约格式验收 |

详见 [02_PARALLEL_DEVELOPMENT_ANALYSIS.md](./02_PARALLEL_DEVELOPMENT_ANALYSIS.md) — 深度分析与风险控制。

---

## 九、 相关文档

- [02_PARALLEL_DEVELOPMENT_ANALYSIS.md](./02_PARALLEL_DEVELOPMENT_ANALYSIS.md) — **深度分析与风险控制**（冲突、流程、规范）
- [03_SKILL_DESIGN.md](./03_SKILL_DESIGN.md) — 详细设计规范（接口、参数、流程）
- [04_WHITEPAPER.md](./04_WHITEPAPER.md) — BI 战报白皮书
- [MCP_SPEC.md](../MCP_SPEC.md) — MCP 接入规范
