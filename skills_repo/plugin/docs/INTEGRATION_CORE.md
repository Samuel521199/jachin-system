# HR 招聘插件 - 核心文档（Jachin 整合打包）

> 本文档为 jachin-system 大项目整合提供：核心代码清单、核心流程、功能架构、打包说明。
>
> **主仓最新招聘架构**（DAG、调度、数据目录、智能化绑定）请以 **`docs/HR_RECRUITMENT.md`**（jachin-system 根）为准；下文若与主仓 `com.jachin.hr.recruitment` 单包实现不一致，以主仓代码与 `HR_RECRUITMENT.md` 为准。

---

## 一、核心代码清单

### 1.1 必须打包的核心代码（MCP）

| 文件路径 | 类型 | 职责 |
|----------|------|------|
| `com.jachin.hr.recruitment/server.py` | **MCP 入口** | FastMCP 服务，暴露所有 HR 原子工具 |
| `com.jachin.hr.recruitment/tools/atom_post_job_boss.py` | 原子工具 | Boss 自动填表并发布职位 |
| `com.jachin.hr.recruitment/tools/atom_greet_recommend_boss.py` | 原子工具 | 推荐牛人页筛选并打招呼 |
| `com.jachin.hr.recruitment/tools/atom_inbox_harvester.py` | 原子工具 | 选职位→遍历会话→下载附件简历 PDF |
| `com.jachin.hr.recruitment/tools/atom_request_resume.py` | 原子工具 | 单人/批量点击「求简历」 |
| `com.jachin.hr.recruitment/tools/boss_harvest_orchestrator.py` | 编排 | 收网流程编排，委托 atom_inbox_harvester |
| `com.jachin.hr.recruitment/tools/boss_utils.py` | **共享基础** | Cookie 加载、职位选择、候选人导航、Playwright 操作 |
| `com.jachin.hr.recruitment/tools/local_archiver.py` | 原子工具 | PDF 保存到 data/pending/<职位>/ |
| `com.jachin.hr.recruitment/tools/brain_filter.py` | 原子工具 | 小模型粗筛（学历、年限） |
| `com.jachin.hr.recruitment/tools/recruitment_status.py` | 状态管理 | recruitment_status.json 读写、双触发判定 |

**依赖关系**（导入顺序建议保留）：
```
boss_utils（无依赖）→ recruitment_status（无依赖）→ local_archiver（无依赖）
→ brain_filter（L3 统一 Key，core.plugin_llm_identity）→ atom_post_job_boss（recruitment_status）
→ atom_inbox_harvester（boss_utils, local_archiver）
→ atom_request_resume（boss_utils）
→ atom_greet_recommend_boss（brain_filter 可选，当前用规则兜底）
→ boss_harvest_orchestrator（atom_inbox_harvester）
→ server.py（聚合所有）
```

### 1.2 Skills 与 Tools(jpp)（SKILL.md 与 Wasm）

| 文件路径 | 类型 | 职责 |
|----------|------|------|
| `4-track-b-skill/SKILL.md` | 技能 | hr-recruiter 主技能 |
| `4-track-b-skill/hr-job-manager/SKILL.md` | 技能 | 职位管理技能 |
| `4-track-b-skill/hr-progress-query/SKILL.md` | 技能 | 招聘进度查询 |
| `3-track-c-swarm-wasm/src/main.py` | Wasm/逻辑 | hr_swarm_engine 三专家评审 |
| `3-track-c-swarm-wasm/src/jachin_sdk.py` | SDK | @jachin_plugin 装饰器、stdin/stdout |
| `3-track-c-swarm-wasm/plugin.json` | 元数据 | 插件 id、entry_point、permissions、dependencies |

### 1.3 配置与数据模板

| 文件路径 | 类型 | 职责 |
|----------|------|------|
| `1-config-template/hr_rules/*.md` | 配置 | HR 硬性筛选规则（如 java_engineer.md） |
| `data/jd_to_publish.example.json` | 模板 | JD 发布配置示例 |
| `deploy/mcp_servers.example.json` | MCP 配置 | MCP 注册示例 |

### 1.4 安装与脚本

| 文件路径 | 类型 | 职责 |
|----------|------|------|
| `install.py` | **安装入口** | 一键部署四大原语（MCP + Skills + Tools·jpp）+ HR 规则到 ~/.jachin 或 jachin-system |
| `scripts/launch_chrome_debug.ps1` | 前置脚本 | Chrome 调试模式（--remote-debugging-port=9222） |

### 1.5 可选/扩展代码（非整合必需）

| 文件路径 | 说明 |
|----------|------|
| `src/orchestrator.py`、`src/skills/*` | 独立招聘主流程（可单独运行 main.py） |
| `scripts/cron_runner.py` | 定时巡逻 + 终局审判（依赖 harvest + hr_swarm_engine） |
| `5-privacy-hook/hook_desensitize.py` | PII 脱敏 Hook，需注册到 Jachin Hook Pipeline |
| `scripts/test_*.py`、`scripts/debug_*.py` | 测试/调试脚本 |

---

## 二、核心流程

### 2.1 收网流程（下载已发简历 PDF）

```
MCP atom_inbox_harvester / harvest_resume_full_flow
    ↓
boss_harvest_orchestrator.harvest_resume_full_flow
    ↓
atom_inbox_harvester.atom_inbox_harvester_full_flow
    ├── boss_utils.select_job(page, job_text)     # 在「全部职位」中选择职位
    ├── 遍历 div.geek-item 会话
    ├── has_preview_attachment_btn() → click_preview_and_download
    │       ├── _do_download (viewer URL 提取 / 拦截 / 点击下载)
    │       └── local_archiver(pdf_bytes=..., file_label=...)
    └── recruitment_status.refresh_unprocessed_count()
```

**前置条件**：Chrome 以 `--remote-debugging-port=9222` 启动，Boss 沟通页已打开。

### 2.2 打招呼流程（推荐牛人自动筛选）

```
MCP atom_greet_recommend_boss
    ↓
atom_greet_recommend_boss
    ├── load_jd_config() → _jd_to_hr_criteria()
    ├── playwright 遍历推荐牛人卡片
    ├── _rule_filter_fallback(online_resume_text, hr_criteria)  # 硬性规则筛选（brain_filter 已注释）
    └── 点击「打招呼」，限制 MAX_GREET_PER_RUN=2
```

### 2.3 发布职位流程

```
MCP atom_post_job_boss
    ↓
atom_post_job_boss
    ├── load_jd_config(jd_config_path)  # jd_to_publish.json 或 recruitment_status
    └── playwright 填表并发布
```

### 2.4 求简历流程（单人/批量）

```
MCP atom_request_resume / atom_request_resume_batch
    ↓
atom_request_resume
    ├── boss_utils.navigate_to_candidate_chat / select_job
    └── 点击 span.operate-btn:has-text('求简历') 等
```

### 2.5 Cron 调度流程（终局审判）

```
scripts/cron_runner.py --force-judge
    ↓
run_final_judgment()
    ├── 遍历 data/pending/*.pdf
    ├── pdfplumber 提取文本 + 脱敏
    ├── hr_swarm_engine(resume_text, hr_criteria)   # 3-track-c-swarm-wasm
    ├── PDF 移至 processed/
    └── sync_interview_track (可选，Lark)
```

---

## 三、核心功能架构

### 3.1 MCP 暴露的工具

| MCP 工具名 | 实现 | 说明 |
|------------|------|------|
| `brain_filter` | brain_filter | 小脑粗筛（学历、年限） |
| `local_archiver` | local_archiver | PDF 归档到 data/pending |
| `atom_inbox_harvester` | harvest_resume_full_flow | 收网（选职位→遍历→下载） |
| `harvest_resume_full_flow` | harvest_resume_full_flow | 同上，别名 |
| `atom_request_resume` | atom_request_resume | 单人求简历 |
| `atom_request_resume_batch` | atom_request_resume_batch | 批量求简历 |
| `atom_post_job_boss` | atom_post_job_boss | 发布职位 |
| `atom_greet_recommend_boss` | atom_greet_recommend_boss | 推荐牛人打招呼 |
| `atom_get_progress` | recruitment_status | 招聘进度查询 |

### 3.2 四大原语分层（Skills → MCP）

```
┌─────────────────────────────────────────────────────────────────┐
│  Skills - SKILL.md                                                │
│  hr-recruiter | hr-job-manager | hr-progress-query               │
│  mcp_tools: ["hr-atomic-tools"]                                  │
└────────────────────────────┬────────────────────────────────────┘
                             │ 调用
┌────────────────────────────▼────────────────────────────────────┐
│  MCP - 原子 server.py                                             │
│  atom_post_job | atom_greet | harvest_resume | atom_request_resume│
│  local_archiver | brain_filter | atom_get_progress                │
└────────────────────────────┬────────────────────────────────────┘
                             │ 依赖
┌────────────────────────────▼────────────────────────────────────┐
│  共享基础：boss_utils, recruitment_status, local_archiver         │
│  前置：Chrome 调试模式 + Boss 登录 + Cookie                       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Tools(jpp) - Wasm 虫群 (hr_swarm_engine)                         │
│  三专家多 Agent 评审 → 终局审判                                   │
│  dependencies: hr-recruiter, hr-atomic-tools                     │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 双触发引擎

`recruitment_status.json` 控制终局审判触发：

- `unprocessed_pdfs >= batch_limit`：待审 PDF 数量达到阈值
- 到达 `cron_trigger_time`：定时触发

---

## 四、打包给 Jachin 整合

### 4.1 最小打包清单

```
plugin/
├── com.jachin.hr.recruitment/
│   ├── server.py
│   ├── requirements.txt
│   └── tools/
│       ├── atom_post_job_boss.py
│       ├── atom_greet_recommend_boss.py
│       ├── atom_inbox_harvester.py
│       ├── atom_request_resume.py
│       ├── boss_harvest_orchestrator.py
│       ├── boss_utils.py
│       ├── local_archiver.py
│       ├── brain_filter.py
│       └── recruitment_status.py
├── 3-track-c-swarm-wasm/
│   ├── src/main.py
│   ├── src/jachin_sdk.py
│   ├── plugin.json
│   └── Makefile
├── 4-track-b-skill/
│   ├── SKILL.md
│   ├── hr-job-manager/SKILL.md
│   └── hr-progress-query/SKILL.md
├── 1-config-template/hr_rules/
├── deploy/mcp_servers.example.json
├── data/jd_to_publish.example.json
├── install.py
├── requirements.txt
├── .env.example
└── scripts/launch_chrome_debug.ps1
```

### 4.2 安装命令

```bash
# 安装到 ~/.jachin
python install.py

# 安装到 jachin-system 项目
python install.py --jachin /path/to/jachin-system

# 跳过 Wasm / MCP
python install.py --skip-wasm --skip-mcp
```

### 4.3 MCP 注册配置

`install.py` 会写入 `~/.jachin/mcp_servers.json`，或参考 `deploy/mcp_servers.example.json`：

```json
{
  "mcp_servers": [
    {
      "id": "hr-atomic-tools",
      "name": "HR 原子工具箱",
      "command": "python",
      "args": ["/path/to/plugin/com.jachin.hr.recruitment/server.py"]
    }
  ]
}
```

### 4.4 LLM 与密钥（统一走 L3 / 主仓）

| 来源 | 说明 |
|------|------|
| **仓库根 `.env`** | DashScope：`JACHIN_ACTIVE_REGION`、`DASHSCOPE_API_KEY_SEA` / `_CN` 或通用 `DASHSCOPE_API_KEY` 等（见 `docs/DASHSCOPE_REGIONAL_KEYS.md`）；以及 `LLM_MODEL`、`LLM_CODER_MODEL` 等（与 L3 主进程一致） |
| **`core.plugin_llm_identity`** | Skill/MCP 内读 Key 与模型：进程环境 → `l3_node.agent_ref.engine` 内存 Key → `credential_loader`；**禁止**插件目录 `.env` 覆盖 LLM/Key |
| **`GEMINI_API_KEY`** | 仅进程级 Gemini 回退（建议在根 `.env` 配置） |
| **brain_filter** | 粗筛模型固定为 `DASHSCOPE_ECON_FALLBACK_MODEL`，非插件 env |

### 4.5 运行时前置

1. **Chrome 调试模式**：`scripts/launch_chrome_debug.ps1` 或 `chrome --remote-debugging-port=9222`
2. **Boss 登录**：在 Chrome 中登录 Boss 直聘
3. **Cookie**（可选）：`.cookie/boss_zhipin_cookies.json` 或 `~/.hr_plugin/config/boss_zhipin_cookies.json`

---

## 五、核心代码索引（快速定位）

| 功能 | 核心代码 |
|------|----------|
| MCP 入口与工具注册 | `com.jachin.hr.recruitment/server.py` |
| Boss 自动化基础（选职位、导航、Cookie） | `com.jachin.hr.recruitment/tools/boss_utils.py` |
| 发布职位 | `com.jachin.hr.recruitment/tools/atom_post_job_boss.py` |
| 打招呼 | `com.jachin.hr.recruitment/tools/atom_greet_recommend_boss.py` |
| 收网下载简历 | `com.jachin.hr.recruitment/tools/atom_inbox_harvester.py` |
| 求简历 | `com.jachin.hr.recruitment/tools/atom_request_resume.py` |
| PDF 归档 | `com.jachin.hr.recruitment/tools/local_archiver.py` |
| 小模型粗筛 | `com.jachin.hr.recruitment/tools/brain_filter.py` |
| 招聘状态与双触发 | `com.jachin.hr.recruitment/tools/recruitment_status.py` |
| 收网编排 | `com.jachin.hr.recruitment/tools/boss_harvest_orchestrator.py` |
| 三专家评审引擎 | `3-track-c-swarm-wasm/src/main.py` |
| 一键安装 | `install.py` |

---

*文档版本：整合打包 v1.0 | 生成日期：基于项目探索*
