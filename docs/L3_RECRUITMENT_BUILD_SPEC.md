# L3 招聘功能 Build 规范文档

**版本**: v1.0  
**状态**: 规范文档（后续 Build 按此执行）  
**适用范围**: L3 节点打包、招聘全链路（MCP 浏览器调用、简历分析、Lark 集成）、新机器部署

---

## 一、Build 产出物结构要求

### 1.1 打包后文件夹必须包含的目录与文件

```
jachin-desktop/                    # Tauri 打包输出根目录
├── Jachin Desktop.exe             # 主程序（或等价名称）
├── bin/
│   └── l3_node-{target}.exe      # L3 Sidecar 二进制
├── scripts/                       # 【必须】与 exe 同级
│   ├── launch_chrome_debug.ps1    # 【必须】招聘 RPA 前置：启动 Chrome 调试模式
│   └── run_l3.ps1                 # 【必须】排查时手动启动 L3 并查看终端日志（JACHIN_SKIP_L3_SPAWN=1 时用）
├── skills_repo/
│   └── plugin/
│       ├── 2-track-a-atomic-mcp/ # 【必须】MCP 招聘原子工具（四项核心功能见 1.3）
│       │   └── tools/            # 含 atom_post_job_boss, atom_greet_recommend_boss,
│       │                          # atom_inbox_harvester, atom_request_resume 等
│       ├── data/                  # 默认简历存储根（可配置迁移）
│       │   └── jd_to_publish.example.json
│       └── .env.example
├── config/
│   ├── l3_recruitment.yaml        # 【新增】L3 招聘统一配置（见下文）
│   └── skills_config.yaml
├── .env.example
└── README_DEPLOY.md               # 新机器部署说明（可选）
```

### 1.2 `scripts/launch_chrome_debug.ps1` 必须存在

- **用途**：招聘 RPA（发布职位、打招呼、收网、求简历）均依赖 Chrome 以 `--remote-debugging-port=9222` 启动。
- **位置**：与 exe 同级目录下的 `scripts/` 中。
- **Build 时**：从项目根 `scripts/launch_chrome_debug.ps1` 或 `skills_repo/plugin/scripts/launch_chrome_debug.ps1` 复制到打包产物的 `scripts/`。

### 1.2.1 `scripts/run_l3.ps1` 必须存在

- **用途**：排查时手动启动 L3 并在终端查看完整日志。当设置 `JACHIN_SKIP_L3_SPAWN=1` 后，Desktop 不自动拉起 L3，用户可执行 `.\scripts\run_l3.ps1 --ws-only` 在终端查看完整输出。
- **位置**：与 exe 同级目录下的 `scripts/` 中。
- **Build 时**：从项目根 `scripts/run_l3.ps1` 复制到打包产物的 `scripts/`。
- **打包模式适配**：若环境中无 Python，脚本需支持调用 `bin/l3_node-{target}.exe` 替代 `python -m l3_node`，确保仅 exe 部署时也能手动排查。

### 1.3 2-track-a-atomic-mcp 四项核心功能（必须全部可用）

| 序号 | 功能 | 工具/模块 | 说明 |
|------|------|-----------|------|
| 1 | **发布职位** | `atom_post_job_boss` | Boss 直聘自动填表并发布，需 Chrome CDP |
| 2 | **和牛人打招呼** | `atom_greet_recommend_boss` | 推荐牛人页遍历卡片，初筛后打招呼 |
| 3 | **沟通求简历** | `atom_request_resume` / `atom_inbox_harvester_full_flow` | 遍历沟通页，对未发简历的候选人点击「求简历」 |
| 4 | **下载简历** | `atom_inbox_harvester` | 遍历沟通页，对有附件简历的自动下载 PDF 到 pending |

**完整招聘链路**：发布职位 → 打招呼 → 求简历 → 下载简历 → **HR 透析镜分析** → **写入 Lark 多维表**。  
其中 HR 透析镜（hr-analyzer4）与 Lark 同步（atom_lark_bitable_sync）必须随 Build 一并可用。

---

## 二、YAML 配置规范

### 2.1 配置文件路径与优先级

| 优先级 | 路径 | 说明 |
|--------|------|------|
| 1 | `config/l3_recruitment.yaml` | 用户可编辑，打包时附带模板 |
| 2 | 环境变量 | 覆盖 YAML 中对应项 |
| 3 | 代码默认值 | YAML 未配置且无环境变量时使用 |

### 2.2 `config/l3_recruitment.yaml` 完整结构

```yaml
# L3 招聘功能统一配置
# 打包时使用项目默认值；新机器部署时用户可修改

# ========== Chrome 浏览器（招聘 RPA 前置） ==========
chrome:
  # Chrome 可执行文件路径（支持 C/D/E 盘等任意位置）
  executable_path: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
  # 调试端口，Playwright 通过 CDP 连接
  cdp_port: 9222
  # 独立用户数据目录，避免与日常 Chrome 冲突
  user_data_dir: "${TEMP}/chrome-debug-boss"

# ========== 简历数据存储（用户可配置到任意盘） ==========
data:
  # 简历下载根目录：pending/processed/result 均在此下按职位分
  # 支持绝对路径：D:/HR/Resumes、C:/Users/xxx/招聘数据
  # 支持相对路径：./data（相对 exe 所在目录）
  # 默认：skills_repo/plugin/data（相对项目根）
  resume_root: "./skills_repo/plugin/data"

# ========== Lark 机器人 ==========
lark:
  app_id: ""           # 必填：飞书应用 App ID
  app_secret: ""       # 必填：飞书应用 App Secret
  chat_id: ""          # 必填：接收消息/通知的群或单聊 ID
  # 多维表格（可选）
  app_token: ""        # 多维表 base ID
  table_id: ""         # 主表 ID
  log_table_id: ""     # 更新日志表 ID
  replace_entire_table: true

# ========== L3 与 Webhook ==========
l3:
  ws_url: "ws://127.0.0.1:18981/sensory"   # L3 WebSocket 地址
  mirror_push_url: "http://127.0.0.1:5000/api/mirror-push"  # 终端→Lark 镜像

# ========== Webhook 与 ngrok ==========
webhook:
  port: 5000
  # ngrok 暴露时使用，用户需自行运行 ngrok http 5000
  ngrok_enabled: false

# ========== HR 透析镜（简历分析）输出 ==========
hr_analysis:
  # 分析报告输出目录（支持绝对路径）
  output_dir: "data/hr_analysis"
  output_dir_use_absolute: false
  # 或指向 plugin data：skills_repo/plugin/data/{职位}/result
```

### 2.3 必填与可选说明

| 配置项 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `chrome.executable_path` | 否 | 见 2.4 | 不填则自动探测 |
| `chrome.cdp_port` | 否 | 9222 | |
| `data.resume_root` | 否 | ./skills_repo/plugin/data | 用户可改为 D:/HR 等 |
| `lark.app_id` | 是* | - | *使用 Lark 时必填 |
| `lark.app_secret` | 是* | - | *使用 Lark 时必填 |
| `lark.chat_id` | 是* | - | *需向 Lark 发消息时必填 |
| `lark.app_token` | 否 | 代码内默认 | 多维表 |
| `lark.table_id` | 否 | 代码内默认 | 多维表 |
| `l3.ws_url` | 否 | ws://127.0.0.1:18981/sensory | |
| `webhook.port` | 否 | 5000 | |
| `hr_analysis.output_dir` | 否 | data/hr_analysis | |

---

## 三、代码中硬编码的处理策略

### 3.1 打包时默认值

- **Build 阶段**：所有可配置项从 `config/l3_recruitment.yaml` 读取；若文件不存在或某项为空，使用**项目内当前默认值**。
- **默认值来源**：以现有代码中的硬编码为准，写入 `l3_recruitment.yaml.example` 作为模板。

### 3.2 需改为从 YAML 读取的硬编码清单

| 位置 | 当前硬编码 | 改为 |
|------|------------|------|
| `launch_chrome_debug.ps1` | `$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"` | 从 YAML `chrome.executable_path` 读取，或保持脚本内默认，由调用方传入 |
| `mcp_registry.py` | `chrome_paths` 列表、`9222`、`user_data_dir` | 从配置读取 |
| `hr_data_paths.py` | `PLUGIN_DATA_ROOT = _ROOT / "data"` | 从 `data.resume_root` 读取，支持绝对路径 |
| `recruitment_scheduler.py` | `PLUGIN_DATA_ROOT = _PROJ_ROOT / "skills_repo" / "plugin" / "data"` | 从配置读取 |
| `recruitment_task.py` | 同上 | 从配置读取 |
| `atom_lark_bitable_sync.py` | `DEFAULT_APP_TOKEN`, `DEFAULT_TABLE_ID` | 从 `lark.app_token`、`lark.table_id` 读取 |
| `atom_lark_send_message.py` | `LARK_APP_ID` 等环境变量 | 优先 YAML，其次 .env |
| `lark_bot.py` | `L3_WS_URL` 等 | 优先 YAML，其次 .env |

### 3.3 简历下载路径可配置的实现要点

**是否需要修改代码**：**是**。

**涉及文件**：

1. **`skills_repo/plugin/2-track-a-atomic-mcp/tools/hr_data_paths.py`**
   - 当前：`PLUGIN_DATA_ROOT = _ROOT / "data"`（相对 plugin 根）
   - 修改：增加 `get_plugin_data_root()`，优先从配置读取 `data.resume_root`，支持：
     - 绝对路径：`D:/HR/Resumes`、`C:/Users/xxx/招聘数据`
     - 相对路径：相对**项目根**或**exe 所在目录**（需约定基准）

2. **`l3_node/recruitment_scheduler.py`**、**`l3_node/recruitment_task.py`**
   - 当前：`PLUGIN_DATA_ROOT = _PROJ_ROOT / "skills_repo" / "plugin" / "data"`
   - 修改：从统一配置读取 `data.resume_root`，解析为 `Path`

3. **`skills_repo/plugin/2-track-a-atomic-mcp/tools/`** 下所有使用 `PLUGIN_DATA_ROOT`、`get_job_dir`、`get_job_pending_dir` 的模块
   - 修改：全部改为通过 `hr_data_paths` 的 `get_plugin_data_root()` 获取根目录，确保一致性

4. **`skills_repo/plugin/scripts/cron_runner.py`**
   - 当前：`DATA_ROOT = ROOT / "data"`
   - 修改：从配置读取

**路径解析规则**：

- `resume_root` 为绝对路径时，直接使用。
- 为相对路径时，基准目录为：**exe 所在目录的父级**（即安装根目录），或通过 `JACHIN_APP_ROOT` 环境变量指定。

---

## 四、日志可见性（新机器执行 exe 时可见日志）

### 4.1 现状

- **PyInstaller**：`build_l3_sidecar.py` 使用 `--noconsole`，导致 L3 以 Sidecar 启动时**无控制台窗口**，日志不可见。
- **Tauri 启动方式**：Desktop 通过 `spawn` 拉起 `bin/l3_node-xxx.exe`，子进程 stdout/stderr 可被重定向，但默认不弹窗。

### 4.2 规范要求

1. **提供「带控制台」的 L3 启动方式**，便于新机器排查：
   - 方案 A：Build 时产出两个 exe：`l3_node.exe`（无控制台，供 Desktop 用）、`l3_node_console.exe`（有控制台，供手动排查用）。
   - 方案 B：通过环境变量 `JACHIN_L3_CONSOLE=1` 时，使用 `--console` 打包或改用 `python -m l3_node` 启动，确保有窗口输出。
   - 方案 C：将 L3 日志同时写入文件 `~/.jachin/logs/l3_node.log`，用户可 tail 查看。

2. **推荐实现**：
   - 修改 `build_l3_sidecar.py`：默认 `--noconsole`，增加 `--console` 参数可生成带控制台版本（如 `l3_node_console-xxx.exe`）。
   - 在 `README_DEPLOY.md` 中说明：新机器排查时，可设置 `JACHIN_SKIP_L3_SPAWN=1`，手动运行 `.\scripts\run_l3.ps1 --ws-only`，日志直接输出到终端。`run_l3.ps1` 已随 Build 包含在 `scripts/` 下。

3. **日志级别**：支持通过 `LOG_LEVEL=DEBUG` 或 YAML 中 `logging.level: DEBUG` 提升详细程度。

---

## 五、L3 Build 必须包含的招聘功能

### 5.1 功能清单

| 功能 | 模块/工具 | 说明 |
|------|-----------|------|
| 发布职位 | `atom_post_job_boss` | Boss 直聘自动填表发布，需 Chrome CDP |
| 推荐牛人打招呼 | `atom_greet_recommend_boss` | 遍历推荐牛人卡片，初筛后打招呼 |
| 收网抓取简历 | `atom_inbox_harvester` | 遍历沟通页，下载 PDF 到 pending |
| 求简历 | `atom_request_resume` | 点击「求简历」按钮 |
| 收网编排 | `boss_harvest_orchestrator` | 编排收网流程 |
| 无人值守调度 | `recruitment_scheduler` | 定时推荐、收网、分析 |
| **简历分析** | **HR 透析镜 Wasm (hr-analyzer4)** | 多 Agent 评审，输出排行榜 |
| **写入 Lark** | **atom_lark_bitable_sync** | 排行榜写入飞书多维表 |
| Lark 机器人 | `lark_bot` | Webhook 接收消息，转发 L3 |

### 5.2 MCP 与插件依赖

- **hr-atomic-tools**：`skills_repo/plugin/2-track-a-atomic-mcp/server.py`，需随包提供。
- **L3 本地调用**：`mcp_registry.py` 通过 `sys.path` 引入 plugin 下 tools，需确保 `skills_repo/plugin/` 与 exe 同目录或通过配置指定路径。
- **Wasm 技能**：`hr-analyzer4` 等需打包进 L3 或从 `l3_node/skills/wasm_plugins/` 加载。

### 5.3 路径解析（打包后）

- **项目根**：以 exe 所在目录向上查找包含 `l3_node`、`skills_repo` 的目录；或通过 `JACHIN_APP_ROOT` 环境变量指定。
- **plugin 根**：`{项目根}/skills_repo/plugin` 或配置中的 `plugin_root`。
- **简历根**：`data.resume_root`，支持用户配置到 C/D/E 盘任意位置。

### 5.4 当前 Build 设计实现缺口与补齐

| 缺口 | 现状 | 补齐方式 |
|------|------|----------|
| **HR 透析镜 Wasm** | `build_l3_sidecar.py` 未打包 `l3_node/skills/wasm_plugins/` | PyInstaller 增加 `--add-data "l3_node/skills/wasm_plugins;l3_node/skills/wasm_plugins"`（Windows 用 `;`），确保 `hr-analyzer4/main.wasm` 和 `plugin.json` 随 exe 解压 |
| **招聘模块隐式导入** | 未显式 `--hidden-import` 招聘相关模块 | 增加 `--hidden-import l3_node.recruitment_scheduler`、`l3_node.recruitment_task`、`l3_node.hr_analysis_persist` 等，避免动态导入缺失 |
| **skills_repo/plugin 分发** | Tauri 打包不自动包含 | 在 Tauri 的 `tauri.conf.json` 或 `resources` 配置中，将 `skills_repo/plugin/2-track-a-atomic-mcp/`、`skills_repo/plugin/data/` 等复制到安装目录 |
| **项目根解析** | exe 运行时 `__file__` 指向解压临时目录，skills_repo 不在其中 | 用 `sys.executable` 推导 app 根：`Path(sys.executable).parent.parent`（exe 在 bin/ 下）；或支持 `JACHIN_APP_ROOT` 环境变量 |

**结论**：当前 Build 设计在**规范上**已覆盖四项 MCP 功能、HR 透析镜、Lark 写入，但**实现上**需按上表补齐，否则新机器部署后无法完整跑通招聘链路。

### 5.5 未打包文件能否被 exe 调用（关键）

`skills_repo/plugin/`、`data/`、`.env.example` 等**未打包进 exe**，需以**文件系统形式**与 exe 同目录部署。exe 能否正确调用它们，取决于**项目根路径解析**是否正确。

| 运行模式 | 当前路径解析 | 结果 |
|----------|--------------|------|
| 源码运行 | `Path(__file__).parent.parent.parent` → 项目根 | ✅ 正确 |
| exe 运行 | `__file__` 指向 PyInstaller 解压临时目录（如 `_MEIxxxxx/`） | ❌ 错误：skills_repo 不在临时目录 |

**必须修改**：exe 运行时，项目根应基于 `sys.executable` 推导，例如：

```python
# 伪代码
if getattr(sys, 'frozen', False):
    # PyInstaller 打包：exe 在 bin/ 下，项目根 = exe 的父级的父级
    app_root = Path(sys.executable).resolve().parent.parent
else:
    app_root = Path(__file__).resolve().parent.parent  # 源码模式
```

确保 `app_root / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"` 存在且可导入。  
**结论**：只要 `skills_repo/plugin/` 与 exe 同目录部署，且代码按上述方式解析项目根，exe **可以**调用这些未打包文件。

---

## 六、新机器部署检查清单

### 6.1 前置条件

- [ ] 安装 Chrome（默认路径或配置 `chrome.executable_path`）
- [ ] 安装 Python 3.x（若使用 `python -m l3_node` 或脚本）
- [ ] 安装 Node.js（若使用 `npx ngrok`）
- [ ] `pip install playwright`（无需 `playwright install chromium`，因使用 CDP 连接已有 Chrome）

### 6.2 配置步骤

1. **复制配置模板**：`config/l3_recruitment.yaml.example` → `config/l3_recruitment.yaml`
2. **填写必填项**：`lark.app_id`、`lark.app_secret`、`lark.chat_id`（若使用 Lark）
3. **可选**：配置 `data.resume_root` 到 D 盘等：`D:/HR/Resumes`
4. **可选**：配置 `chrome.executable_path`（若 Chrome 不在默认路径）
5. **复制 .env**：从 `.env.example` 复制为 `.env`，填入 `DASHSCOPE_API_KEY` 等

### 6.3 启动顺序

1. **启动 Chrome 调试模式**：`.\scripts\launch_chrome_debug.ps1`，在 Chrome 中登录 Boss 直聘
2. **启动 L3**：Desktop 自动拉起，或手动 `.\scripts\run_l3.ps1 --ws-only`
3. **（可选）启动 Lark Webhook**：`cd skills_repo\plugin; python 2-track-a-atomic-mcp/lark_bot.py --webhook --port 5000`
4. **（可选）ngrok**：`ngrok http 5000`，在 Lark 后台配置回调地址

### 6.4 日志排查

- 设置 `JACHIN_SKIP_L3_SPAWN=1`，手动运行 `.\scripts\run_l3.ps1`，日志输出到终端
- 或使用带控制台的 L3 exe（若 Build 提供）
- 检查 `~/.jachin/logs/` 下日志文件（若实现文件日志）

---

## 七、Build 脚本修改要点

### 7.1 `scripts/build_l3_sidecar.py`

- 保持 `--noconsole` 作为默认，供 Desktop Sidecar 使用。
- 可选：增加 `--console` 产出 `l3_node_console-xxx.exe`，供排查用。
- 确保 `--hidden-import` 包含招聘相关模块：`l3_node.recruitment_scheduler`、`l3_node.recruitment_task`、`l3_node.hr_analysis_persist` 等。

### 7.2 Tauri 打包配置

- 确保 `scripts/launch_chrome_debug.ps1`、`scripts/run_l3.ps1` 被复制到 `bundle/resources/` 或等价位置，使安装后 `scripts/` 与 exe 同级。
- 确保 `skills_repo/plugin/`、`config/` 随包分发。

### 7.3 配置加载逻辑

- 新增 `l3_node/config_loader.py`（或等价模块）：读取 `config/l3_recruitment.yaml`，合并环境变量，提供统一 `get_config()` 接口。
- 所有招聘相关模块通过 `get_config()` 获取配置，不再直接 `os.getenv` 或硬编码。

---

## 八、简历存储路径可配置 — 代码修改清单

| 文件 | 修改内容 |
|------|----------|
| `l3_node/config_loader.py` | 新增，读取 YAML，解析 `data.resume_root` |
| `skills_repo/plugin/2-track-a-atomic-mcp/tools/hr_data_paths.py` | `PLUGIN_DATA_ROOT` 改为调用 `get_resume_root()`，支持配置 |
| `l3_node/recruitment_scheduler.py` | `PLUGIN_DATA_ROOT` 从配置读取 |
| `l3_node/recruitment_task.py` | 同上 |
| `skills_repo/plugin/2-track-a-atomic-mcp/tools/local_archiver.py` | 使用 `get_job_pending_dir` 等，依赖 `hr_data_paths` |
| `skills_repo/plugin/2-track-a-atomic-mcp/tools/boss_harvest_orchestrator.py` | 同上 |
| `skills_repo/plugin/2-track-a-atomic-mcp/tools/recruitment_status.py` | 同上 |
| `skills_repo/plugin/scripts/cron_runner.py` | `DATA_ROOT` 从配置读取 |
| `l3_node/hr_analysis_persist.py` | `output_dir` 已支持配置，确保与 `data.resume_root` 协同 |
| `core/wasm_runner.py` | 路径白名单需包含用户配置的 `resume_root` |

---

## 九、附录：默认值速查

| 配置项 | 默认值 |
|--------|--------|
| Chrome 路径 | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| CDP 端口 | 9222 |
| 简历根目录 | `./skills_repo/plugin/data` |
| Webhook 端口 | 5000 |
| L3 WebSocket | ws://127.0.0.1:18981/sensory |
| HR 分析输出 | data/hr_analysis 或 data/{职位}/result |

---

**文档结束。后续 Build 与代码修改均按本规范执行。**
