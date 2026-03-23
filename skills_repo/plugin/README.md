# HR 招聘插件 (jachin-hr-plugins)

基于 **Jachin 三轨道体系** 与 **原子架构** 的 HR 招聘筛选插件。  
独立工程结构，规范对齐 jachin-system v8.0 `docs/PLUGIN_DEVELOPMENT_GUIDE.md` v2.0（技能发现：SemanticRouter 向量检索）。

> **主仓（jachin-system）当前招聘执行架构**（`com.jachin.hr.recruitment` MCP、`hr_recruitment_dag`、调度走 DAG、`task_planning` HR 路径等）以仓库根目录 **`docs/HR_RECRUITMENT.md`** 为单一事实来源；本文档侧重插件包内轨道与历史目录结构。

## 分发模型

本插件采用 **Wasm 为主体 + 轨道 A/B 为依赖** 的分发策略：

| 组件 | 轨道 | 商店形态 | 说明 |
|------|------|----------|------|
| **主体** | 轨道 C | Wasm 插件 | `hr_swarm_engine` 虫群评审引擎，上架神经元商城 |
| **依赖** | 轨道 B | Skill 包 | `hr-recruiter` 意图路由，SKILL.md |
| **依赖** | 轨道 A | MCP 包 | `hr-atomic-tools` 原子工具箱 |

**安装流程**：用户从插件商店下载 Wasm 主体时，平台将自动拉取并安装轨道 A、B 依赖并完成集成；若平台暂不支持依赖自动安装，可运行项目根目录的 `install.py` 实现等效的一键安装。

## 架构概览

```
轨道 B (SKILL) 意图路由 → 轨道 A (MCP) 原子工具 → 轨道 C (Wasm) 虫群评审 → 输出
                              ↑
                    v8.0 洋葱 Hook (PII 脱敏)
```

| 模块 | 轨道/组件 | 职责 |
|------|-----------|------|
| 1-config-template | 动态配置 | HR 业务规则（Markdown） |
| 2-track-a-atomic-mcp | 轨道 A (高信任) | 原子工具：PDF 提取、网页抓取、Boss 简历 |
| 3-track-c-swarm-wasm | 轨道 C (零信任) | 三专家多 Agent：Tech Lead + HR BP + 主理法官 |
| 4-track-b-skill | 轨道 B (用户可控) | hr-recruiter + hr-job-manager + hr-progress-query |
| 5-privacy-hook | Nexus Hook | before_llm_think 简历 PII 脱敏 |

## 依赖声明

轨道 C 的 `plugin.json` 中声明了依赖，供平台解析：

```json
"dependencies": [
  { "type": "skill", "id": "hr-recruiter", "version": "1.0.0" },
  { "type": "mcp", "id": "hr-atomic-tools", "version": "1.0.0" }
]
```

## 快速开始

### 1. 环境准备

```bash
# 安装依赖
pip install -r requirements.txt
pip install -r 2-track-a-atomic-mcp/requirements.txt

# API Key（轨道 C 三专家多 Agent 用）
copy .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY=（阿里百炼，推荐）或 GEMINI_API_KEY=（回退）
```

**三专家模型分配**（阿里百炼 qwen3.5 系列）：

| 专家 | 角色 | 默认模型 | 说明 |
|------|------|----------|------|
| A | 技术总监 | qwen3.5-122b-a10b | 1220 亿参数，逻辑纵深、技术拆解 |
| B | HR BP | qwen3.5-plus | 长文本语义、情商均衡、稳定性分析 |
| C | 主理法官 | qwen3.5-397b-a17b | 近 4000 亿参数，指令遵循、纯净 JSON |
| 第一漏斗 | 雷达粗筛 | qwen3.5-flash-2026-02-23 | 极速扫雷，学历/年限底线过滤 |

可通过 `.env` 覆盖：`TECH_LEAD_MODEL`、`HR_BP_MODEL`、`JUDGE_MODEL`、`BRAIN_FILTER_MODEL`。

### 2. 一键安装（推荐）

将轨道 A、B、C 及 HR 规则一次性部署到本地或 jachin-system：

```bash
# 仅部署到 ~/.jachin/（HR 规则、SKILL、Wasm、MCP 注册）
python install.py

# 部署到 jachin-system 项目
python install.py --jachin D:\path\to\jachin-system

# 跳过 Wasm 编译（若已编译或暂不需要）
python install.py --skip-wasm

# 跳过 MCP 注册（若已手动配置）
python install.py --skip-mcp
```

### 3. 本地 Mock 全链路（开发测试）

```bash
# 演示模式（模拟简历）
python tests/mock_v8_runner.py --source demo

# 本地 PDF 简历
python tests/mock_v8_runner.py --source local --path "data/xxx.pdf"

# Boss 直聘（需 Cookie）
python tests/mock_v8_runner.py --source boss --job "Java工程师"
```

### 4. 轨道 C 单独测试

```bash
cd 3-track-c-swarm-wasm
echo '{"resume_text":"张三\nJava 3年\nSpringCloud","hr_criteria":"要求本科"}' | python src/main.py
```

### 5. 轨道 A 原子工具（MCP Server）

```bash
cd 2-track-a-atomic-mcp
pip install mcp
python server.py   # stdio 模式，供 Jachin MCP 客户端连接
```

## 目录结构

```
plugin/
├── 1-config-template/       # 部署时复制到 ~/.jachin/workspace/hr_rules/
│   └── hr_rules/
│       └── java_engineer.md
├── 2-track-a-atomic-mcp/   # 轨道 A - 原子 MCP Server（依赖）
│   ├── server.py
│   ├── tools/
│   └── requirements.txt
├── 3-track-c-swarm-wasm/   # 轨道 C - 虫群评审引擎（主体）
│   ├── src/
│   ├── plugin.json         # 含 dependencies 声明
│   └── Makefile
├── 4-track-b-skill/        # 轨道 B - 意图路由（依赖）
│   └── SKILL.md
├── 5-privacy-hook/         # v8.0 洋葱中间件
│   └── hook_desensitize.py
├── deploy/
│   ├── copy_to_jachin.bat  # 旧版部署脚本（HR 规则 + SKILL）
│   └── mcp_servers.example.json
├── tests/
│   └── mock_v8_runner.py   # 本地全链路测试舱
├── install.py              # 一键安装脚本
├── src/                    # 兼容层：llm_client（阿里百炼 + Gemini 回退）
├── .env.example
├── requirements.txt
└── README.md
```

## 集成到 Jachin

**方式一**：使用 `install.py`（见上文「一键安装」）

**方式二**：手动部署

1. **HR 规则**：将 `1-config-template/hr_rules/` 复制到 `~/.jachin/workspace/hr_rules/`
2. **轨道 A**：将 MCP 加入 `~/.jachin/mcp_servers.json`（Jachin v8 格式 `{"mcp_servers": [...]}`），参考 [deploy/mcp_servers.example.json](deploy/mcp_servers.example.json)
3. **轨道 B**：将 `4-track-b-skill/` 下三个 SKILL 复制到 `skills_repo/`：
   - `hr-recruiter/SKILL.md`、`hr-progress-query/SKILL.md`、`hr-job-manager/SKILL.md`
4. **首次集成**：需触发技能向量索引 `SemanticRouter().reindex_all_skills()`（daemon 启动或 API 调用）
5. **轨道 C**：`cd 3-track-c-swarm-wasm && make build`，将 `dist/plugin.wasm` + `plugin.json` 放入 `skills_repo/com.jachin.hr-swarm-engine/` 或 `skills_repo/_bundled/`
6. **Hook（可选）**：将 `5-privacy-hook/hook_desensitize.py` 注册进 Jachin 的 Hook Pipeline

**轨道 C 独立验证**（无需 Jachin 主项目）：
```bash
cd 3-track-c-swarm-wasm && python verify_standalone.py   # 或 verify_standalone.bat / make test
```

## 新增方案：三层异步招聘流程（2025）

基于招聘网站「在线简历 + 消息收网」的实时特性，新增三层异步流程，详见 [docs/HR_PLUGIN_NEW_SCHEME.md](docs/HR_PLUGIN_NEW_SCHEME.md)。

| 层级 | 名称 | 触发 | 动作 |
|------|------|------|------|
| 第一层 | 雷达粗筛与撒网 | cron 每 30 分钟 | 抓取在线简历 → 小脑粗筛 → 自动打招呼 |
| 第二层 | 意向确认与收网归档 | 与第一层并行 | 扫描消息 → 下载 PDF → 保存到 pending |
| 第三层 | 诸神黄昏（Wasm 虫群） | 双触发引擎 | pending PDFs → 虫群评审 → processed + Lark 多维表（表格形式，按推荐程度排序，含推荐理由和 PDF 链接，Top 10 实时更新；HR 手动选取后 AI 安排面试） |

**双触发引擎**：`recruitment_status.json` 维护状态。触发条件：`unprocessed_pdfs >= batch_limit(50)` 或 到达 `cron_trigger_time`（如 08:30）。

**Lark 端到端**：HR 在飞书发自然语言（如「招个前端，15-20k，统招本科」）→ 系统解析、烙印规则、多 Agent 据此筛选；实时进度汇报（里程碑推送 + 被动查询「现在几个了？」）。

**cron 调度**：

```bash
# 日常巡逻（雷达 + 收网）
python scripts/cron_runner.py --patrol --job "Java开发"

# 终局审判（强制，无视触发条件）
python scripts/cron_runner.py --force-judge --lark-sheet "xxx"
```

## 军规（开发约束）

- **轨道 C**：禁止读写本地文件，stdin 入 / stdout 出
- **plugin.json**：必须声明 `"permissions": ["llm.invoke"]`
- **core:fs_read**：仅限 `~/.jachin/workspace/`
- **Hook**：必须 `async def` 且调用 `await next_middleware()`
- **日志**：统一前缀 `[RunID: {run_id[:8]}]`
