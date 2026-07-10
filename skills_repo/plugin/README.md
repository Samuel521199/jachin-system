# HR 招聘插件 (jachin-hr-plugins)

基于 **Jachin 四大原语**（MCP / Skills / Tools / Agent Tasks）与 **原子架构** 的 HR 招聘筛选插件。
独立工程结构，规范对齐 jachin-system v8.0 `docs/PLUGIN_DEVELOPMENT_GUIDE.md` v2.0（技能发现：SemanticRouter 向量检索）。

> **主仓（jachin-system）当前招聘执行架构**（`com.jachin.hr.recruitment` MCP、`hr_recruitment_dag`、调度走 DAG、`task_planning` HR 路径等）以仓库根目录 **`docs/HR_RECRUITMENT.md`** 为单一事实来源；术语 SSOT：**`docs/Jachin 视角的「四大原语」终极架构规范.md`**。

## 分发模型

本插件采用 **Tools(jpp) Wasm 为主体 + MCP 与 Skills 为依赖** 的分发策略：

| 组件 | 原语 | 商店形态 | 说明 |
|------|------|----------|------|
| **主体** | **Tools · jpp** | Wasm 插件 | `hr_swarm_engine` 虫群评审引擎，上架神经元商城 |
| **依赖** | **Skills** | Skill 包 | `hr-recruiter` 意图路由，SKILL.md |
| **依赖** | **MCP** | MCP 包 | `hr-atomic-tools` 原子工具箱 |

**安装流程**：用户从插件商店下载 Wasm 主体时，平台将自动拉取并安装 MCP、Skills 依赖并完成集成；若平台暂不支持依赖自动安装，可运行项目根目录的 `install.py` 实现等效的一键安装。

## 架构概览

```
Skills(SKILL.md) 意图路由 → MCP 原子工具 → Tools(jpp) Wasm 虫群评审 → 输出
                              ↑
                    v8.0 洋葱 Hook (PII 脱敏)
```

| 模块 | 原语/组件 | 职责 |
|------|-----------|------|
| 1-config-template | 动态配置 | HR 业务规则（Markdown） |
| com.jachin.hr.recruitment | **MCP**（高信任） | 原子工具：PDF 提取、网页抓取、Boss 简历 |
| 3-track-c-swarm-wasm | **Tools · jpp**（零信任） | 三专家多 Agent：Tech Lead + HR BP + 主理法官 |
| 4-track-b-skill | **Skills**（用户可控） | hr-recruiter + hr-job-manager + hr-progress-query |
| 5-privacy-hook | Nexus Hook | before_llm_think 简历 PII 脱敏 |

## 依赖声明

**Tools(jpp)** 的 `plugin.json` 中声明了依赖，供平台解析：

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
pip install -r com.jachin.hr.recruitment/requirements.txt
```

**LLM / 百炼**：在 **jachin-system 仓库根目录** `.env` 配置 DashScope 相关变量（国内/东南亚分 Key 见 `docs/DASHSCOPE_REGIONAL_KEYS.md`）、`LLM_MODEL` 等；由 L3 或进程统一注入。Skill/MCP 通过 `core.plugin_llm_identity` 读取，**勿**在 `skills_repo/plugin/.env` 配置 Key 或主模型（插件 `.env.example` 仅保留 Lark 等非 LLM 项）。

**三专家（Tools·jpp 虫群）**：提示词仍区分 Tech Lead / HR BP / 法官角色；DashScope 实际调用统一使用 **L3 主推理模型**（与根 `.env` 的 `LLM_MODEL` 一致）。

**第一漏斗（brain_filter）**：使用 **`core.llm_provider.DASHSCOPE_ECON_FALLBACK_MODEL`**（经济型降级模型），由上层策略定义，不由插件环境变量覆盖。

### 2. 一键安装（推荐）

将 MCP、Skills、Tools·jpp 及 HR 规则一次性部署到本地或 jachin-system：

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

### 4. Tools(jpp) 单独测试

```bash
cd 3-track-c-swarm-wasm
echo '{"resume_text":"张三\nJava 3年\nSpringCloud","hr_criteria":"要求本科"}' | python src/main.py
```

### 5. MCP 原子工具（MCP Server）

```bash
cd com.jachin.hr.recruitment
pip install mcp
python server.py   # stdio 模式，供 Jachin MCP 客户端连接
```

## 目录结构

```
plugin/
├── 1-config-template/       # 部署时复制到 ~/.jachin/workspace/hr_rules/
│   └── hr_rules/
│   └── java_engineer.md
├── com.jachin.hr.recruitment/   # MCP - 原子 MCP Server（依赖）
│   ├── server.py
│   ├── tools/
│   └── requirements.txt
├── 3-track-c-swarm-wasm/   # Tools(jpp) - 虫群评审引擎（主体）；目录名保留历史
│   ├── src/
│   ├── plugin.json         # 含 dependencies 声明
│   └── Makefile
├── 4-track-b-skill/        # Skills - 意图路由（依赖）
│   └── SKILL.md
├── 5-privacy-hook/         # v8.0 洋葱中间件
│   └── hook_desensitize.py
├── deploy/
│   ├── copy_to_jachin.bat  # 旧版部署脚本（HR 规则 + SKILL）
│   └── mcp_servers.example.json
├── tests/
│   └── mock_v8_runner.py   # 本地全链路测试舱
├── install.py              # 一键安装脚本
├── src/                    # 适配层：llm_client（阿里百炼 + Gemini 回退）
├── .env.example
├── requirements.txt
└── README.md
```

## 集成到 Jachin

**方式一**：使用 `install.py`（见上文「一键安装」）

**方式二**：手动部署

1. **HR 规则**：将 `1-config-template/hr_rules/` 复制到 `~/.jachin/workspace/hr_rules/`
2. **MCP**：将 MCP 加入 `~/.jachin/mcp_servers.json`（Jachin v8 格式 `{"mcp_servers": [...]}`），参考 [deploy/mcp_servers.example.json](deploy/mcp_servers.example.json)
3. **Skills**：将 `4-track-b-skill/` 下三个 SKILL 复制到 `skills_repo/`：
   - `hr-recruiter/SKILL.md`、`hr-progress-query/SKILL.md`、`hr-job-manager/SKILL.md`
4. **首次集成**：需触发技能向量索引 `SemanticRouter().reindex_all_skills()`（daemon 启动或 API 调用）
5. **Tools(jpp)**：`cd 3-track-c-swarm-wasm && make build`，将 `dist/plugin.wasm` + `plugin.json` 放入 `skills_repo/com.jachin.hr-swarm-engine/` 或 `skills_repo/_bundled/`
6. **Hook（可选）**：将 `5-privacy-hook/hook_desensitize.py` 注册进 Jachin 的 Hook Pipeline

**Tools(jpp) 独立验证**（无需 Jachin 主项目）：
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

- **Tools(jpp)**：禁止读写本地文件，stdin 入 / stdout 出
- **plugin.json**：必须声明 `"permissions": ["llm.invoke"]`
- **core:fs_read**：仅限 `~/.jachin/workspace/`
- **Hook**：必须 `async def` 且调用 `await next_middleware()`
- **日志**：统一前缀 `[RunID: {run_id[:8]}]`
