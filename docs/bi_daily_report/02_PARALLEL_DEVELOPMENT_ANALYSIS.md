# BI 并行开发 — 深度分析与风险控制

**版本**: 1.0  
**定位**: 对 [01_PARALLEL_DEVELOPMENT_GUIDE.md](./01_PARALLEL_DEVELOPMENT_GUIDE.md) 的补充分析，回答「能否完全避免冲突」「流程如何编排」「还需哪些规范」

---

## 一、能否完全确保 A/B/C 零冲突与零污染？

### 1.1 结论：**在严格遵循规范的前提下，可达到 95%+ 的零冲突**

| 维度 | 可确保 | 存在风险 | 说明 |
|------|--------|----------|------|
| **Git 文件冲突** | ✅ | — | A/B/C 各自修改不同文件，合并时无同一行冲突 |
| **逻辑污染** | ✅ | — | 契约隔离，A 不碰 B 的飞书逻辑，B 不碰 A 的抓取逻辑 |
| **HR 逻辑污染** | ✅ | — | 物理隔离，BI 模块不修改 recruitment_*、mcp_registry 中 HR 分支 |
| **共享文件冲突** | — | ⚠️ | 见下文「剩余风险」 |
| **契约偏差** | — | ⚠️ | 参数命名、路径基准未统一，对接时需适配 |

### 1.2 物理隔离验证（Git 视角）

```
A 分支 feat/bi-scraper 仅包含：
  l3_node/mcp_tools/bi/tool_web_scraper.py  （新建）

B 分支 feat/bi-broadcaster 仅包含：
  l3_node/mcp_tools/bi/tool_lark_notifier.py、tool_email_sender.py  （新建）

C 分支 feat/bi-skill 仅包含：
  l3_node/skills/bi/bi_daily_report/main_skill.py
  l3_node/bi/scheduler.py                       （新建）

统帅分支（组装）修改：
  l3_node/skills/mcp_registry.py  （追加路由与注册）
  l3_node/__main__.py 或 http_server.py  （可选：import l3_node.skills.bi.scheduler）
```

**结论**：A、B、C 三者的文件集合**无交集**，Git 三路合并不会产生 `<<<<<<<` 冲突。

---

## 二、剩余风险与应对

### 2.1 共享资源冲突（需规范）

| 风险点 | 场景 | 应对 |
|--------|------|------|
| **requirements.txt** | A 需 `requests`、`beautifulsoup4`；B 可能不需新增；C 可能需 `litellm`（已有） | **规范**：A/B/C **禁止**修改 `core/requirements.txt`。新增依赖由统帅在组装阶段统一追加，或新建 `l3_node/requirements-bi.txt` 由 A/B 各自维护自己模块的依赖文件 |
| **l3_node/mcp_tools/__init__.py** | A、B 首次创建 `mcp_tools/` 目录时，可能都添加 `__init__.py` | **规范**：统帅在项目启动前**预创建** `l3_node/mcp_tools/` 目录及空 `__init__.py`，A/B 仅添加各自 py 文件 |
| **data/bi_raw_pool/** | A 创建目录并写入；C 读取。若路径基准不一致（项目根 vs workspace vs ~/.jachin）会读错 | **规范**：统帅在契约中**明确路径基准**，见下文「路径基准统一」 |

### 2.2 契约参数不一致（需统一）

| 契约 | 原并行指南用词 | 设计文档用词 | 风险 |
|------|----------------|--------------|------|
| 飞书 | `markdown_text` | `markdown_content` | C 调用时传错 key，B 收不到 |
| 邮件 | `to_emails` | `to_addrs` | 同上 |
| 抓取 | `config` 内含 `output_format` | 顶层 `output_path`、`output_format` | A 实现时可能只认 `config`，C 传顶层参数无效 |

**应对**：在《最高接口契约》中**强制统一**参数名，与设计文档一致，并在开发者指令中明确写出完整参数列表。

### 2.3 MCP「注册」表述歧义

指令 A 第 4 条：「在文件末尾或对应的注册表中，将其暴露为 mcp:atom_web_scraper」。

- **歧义**：A 可能理解为需要修改 `mcp_registry.py`，与「禁止修改」矛盾。
- **应对**：明确表述为「在 `tool_web_scraper.py` 内**实现**可被调用的函数，**不**修改 mcp_registry。MCP 注册由统帅在组装阶段完成。」

### 2.4 C 的 Mock 与真实对接偏差

C 用 Mock 开发时，若 Mock 的入参/出参与真实 A、B 不一致，对接时需改 C 的代码。

**应对**：C 的 Mock 必须严格遵循《最高接口契约》的 JSON 格式；建议 C 使用契约驱动的 Mock（如固定返回 `{"status":"success","file_path":"client_volumes/bi_data/raw/20260316.csv"}`）。

---

## 三、完整开发流程与前后搭配

### 3.1 推荐流程（五阶段）

```
阶段 0：统帅预备（1 天）
  ├─ 创建 l3_node/mcp_tools/ 目录及 __init__.py
  ├─ 在《最高接口契约》中统一参数名（与设计文档对齐）
  ├─ 明确路径基准：client_volumes/bi_data/raw/
  └─ 提交到 main，A/B/C 从 main 拉取

阶段 1：A、B 并行开发（互不依赖）
  ├─ A：feat/bi-scraper，只写 tool_web_scraper.py
  ├─ B：feat/bi-broadcaster，只写 tool_broadcaster.py
  ├─ 各自本地测试通过后 PR
  └─ 统帅 Code Review，合并 A、B 到 main（顺序任意）

阶段 2：C 开发（可提前启动，用 Mock）
  ├─ C：feat/bi-skill，从 main 拉取（含 A、B 合并后的 mcp_tools/）
  ├─ 若 A、B 未合并：C 用 Mock 数据 + Mock mcp_registry.invoke 调测
  └─ 若 A、B 已合并：C 可请求统帅先做「最小组装」（仅注册 MCP，不启动调度），以便 C 联调

阶段 3：统帅组装
  ├─ 合并 C 的 feat/bi-skill
  ├─ 在 mcp_registry 中添加路由与 L3_LOCAL_MCP_TOOLS 注册
  ├─ 在 __main__.py 或 http_server 中 import l3_node.skills.bi.scheduler
  ├─ 统一路径：在 BiReportConfig 或常量中指定 storage.base_path
  └─ 追加依赖（若有）：core/requirements.txt 或 l3_node/requirements-bi.txt

阶段 4：联调与验收
  ├─ 端到端跑通：抓取 → 计算 → LLM → 飞书 + 邮件
  ├─ 契约验收：用脚本或人工验证 A、B 返回值符合契约
  └─ 定时任务验证：手动触发或等到 8:00 验证调度
```

### 3.2 前后依赖关系图

```
                    ┌─────────────────────────────────────┐
                    │ 阶段 0：统帅预备                      │
                    │ - mcp_tools/ 目录                    │
                    │ - 契约参数统一                        │
                    └─────────────────┬───────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
    ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
    │ A: 抓取 MCP     │     │ B: 广播 MCP     │     │ C: BI Skill     │
    │ tool_web_       │     │ tool_broadcaster│     │ (可 Mock 先行)  │
    │ scraper.py      │     │ .py             │     │                 │
    └────────┬────────┘     └────────┬────────┘     └────────┬────────┘
             │                        │                        │
             └────────────────────────┼────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │ 阶段 3：统帅组装                      │
                    │ - mcp_registry 路由                   │
                    │ - bi_scheduler 启动                  │
                    └─────────────────────────────────────┘
```

### 3.3 合并顺序与冲突概率

| 合并顺序 | 冲突概率 | 说明 |
|----------|----------|------|
| A → B → C | 极低 | 推荐。A、B 先合，C 基于含 A、B 的 main 开发，避免 C 合并时与 A、B 冲突 |
| B → A → C | 极低 | 同上 |
| A、B 同时合 | 极低 | 不同文件，无冲突 |
| C 先于 A、B 合 | 低 | C 若用 Mock，合并时无冲突；但 C 无法联调真实 MCP，需等 A、B 合并后再测 |

---

## 四、还需补充的规范

### 4.1 路径基准统一（必须）

| 路径 | 基准 | 说明 |
|------|------|------|
| `data/bi_raw_pool/` | `get_app_root() / "data" / "bi_raw_pool"` | 项目根下的 data，与 HR 的 `skills_repo/plugin/data/` 分离 |
| 或 `client_volumes/bi_data/raw/` | `Path.home() / ".jachin" / "client_volumes" / "bi_data" / "raw"` | 用户数据卷，与现有 client_volumes 一致 |

**建议**：采用 `client_volumes/bi_data/raw/`，与设计文档、白皮书一致，且不污染项目仓库（data 可被 .gitignore）。

### 4.2 依赖管理规范（必须）

- A、B、C **禁止**直接修改 `core/requirements.txt`。
- 若需新增依赖，在各自 PR 描述中列出，由统帅在组装阶段统一追加。
- 或：在 `l3_node/mcp_tools/` 下新增 `requirements.txt`（仅 BI 工具用），由 A、B 分别追加自己需要的包，合并时可能冲突，需统帅协调。

### 4.3 契约验收规范（建议）

- 为每个 MCP 编写**契约测试**：输入固定参数，断言输出 JSON 包含 `status`、`file_path`/`msg` 等字段。
- 统帅在合并 A、B 前，可要求通过契约测试，或人工验收返回值格式。

### 4.4 分支保护与 Code Review

- `main` 分支保护：禁止 force push，PR 需至少 1 人 Review。
- A、B 的 PR 必须**仅**包含约定文件，若有额外修改需说明理由。

---

## 五、总结：确保项目顺利的检查清单

| 序号 | 检查项 | 负责方 |
|------|--------|--------|
| 1 | 统帅预创建 `l3_node/mcp_tools/` 及 `__init__.py` | 统帅 |
| 2 | 契约参数与设计文档完全统一（markdown_content、to_addrs 等） | 统帅 |
| 3 | 路径基准明确：`client_volumes/bi_data/raw/` 或 `data/bi_raw_pool/` | 统帅 |
| 4 | A、B 指令中明确「不修改 mcp_registry」 | 统帅 |
| 5 | A、B 禁止修改 requirements.txt，依赖在 PR 描述中列出 | 统帅 |
| 6 | A、B 合并后再合并 C，或 C 用 Mock 先行 | 统帅 |
| 7 | 组装阶段：路由、注册、调度启动、路径统一 | 统帅 |
| 8 | 联调验收：端到端 + 契约格式 | 统帅 |

---

## 六、相关文档

- [01_PARALLEL_DEVELOPMENT_GUIDE.md](./01_PARALLEL_DEVELOPMENT_GUIDE.md) — 多兵种协同作战指南
- [03_SKILL_DESIGN.md](./03_SKILL_DESIGN.md) — 详细设计规范
