# L3 便携包部署说明

将 `dist_jachin_desktop` 整个文件夹拷贝到目标机器即可使用 L3。**L3 为轻量架构**：不包含任何 MCP 与 Skill，均通过 L1 订阅下载到 `~/.jachin/l3_mcp_cache` / `l3_skill_cache`。

## 目录结构

```
dist_jachin_desktop/
├── bin/                    # L3 可执行文件 l3_node-*.exe
├── scripts/                # 启动脚本
│   ├── run_l3.ps1          # 主启动脚本
│   └── launch_chrome_debug.ps1
├── config/                 # 最小配置模板（含 im_channels.yaml.example、l3_recruitment.yaml.example）
├── logs/                   # 日志目录（运行后生成）
│   ├── l3_debug.log        # 调试日志（每次启动清空）
│   └── l3_broadcast.log    # 全息监控日志（调度、任务状态）
├── .env.example            # 环境变量示例
└── README_DEPLOY.md        # 本说明
```

**无 skills_repo**：MCP 与 Skill 由 L2 从 L1 同步，L3 启动后自动拉取到 `~/.jachin/`。

## 部署步骤

### 1. 拷贝整个文件夹

将 `dist_jachin_desktop` 完整拷贝到目标机器任意目录，例如 `D:\Jachin\`。

### 2. 配置环境变量

在便携包根目录创建 `.env` 文件：

```powershell
cd D:\Jachin\dist_jachin_desktop
copy .env.example .env
# 编辑 .env，填入必要配置
```

**必填项：**

- `DASHSCOPE_API_KEY`：阿里百炼 API Key（LLM）
- L2 与 L1 建立信任后 Key 由 L2 下发；未完成前需本地配置（见仓库 `docs/L1_L2_PAIRING_AND_WEB_BRIDGE.md`）

**可选（Lark / 飞书）：**

- 方式一：在 `.env` 中配置 `LARK_APP_ID`、`LARK_APP_SECRET`、`LARK_CHAT_ID`
- 方式二：复制 `config/im_channels.yaml.example` 到 `~/.jachin/config/im_channels.yaml` 并填写

### 3. L2 连上 L1 并订阅 MCP/Skill

- L2 需先与 L1 建立信任（默认在 L2 `/gateway` 用 **Nexus 账号登录**；无头环境用 CLI 6 位码，见 `docs/L1_L2_PAIRING_AND_WEB_BRIDGE.md`）
- 在 L2 侧订阅所需 MCP、Skill；L3 启动后自动从 L2 拉取到 `~/.jachin/`

### 4. 启动 L3

```powershell
.\scripts\run_l3.ps1
```

- **已配对 L2**：使用 `--gateway`（默认），MCP/Skill 自动拉取
- **新机器 / L2 未启动**：双击 `run_l3_standalone.bat` 或 `.\scripts\run_l3.ps1 --ws-only`（需 .env 有 DASHSCOPE_API_KEY，无订阅能力）

### 5. 验证

- 健康检查：`http://127.0.0.1:18991/api/health`
- 全息监控：前端 SSE 订阅或查看 `logs/l3_broadcast.log`

## 日志说明

| 文件 | 说明 |
|------|------|
| `logs/l3_debug.log` | 调试日志，每次启动清空，含启动信息、错误堆栈 |
| `logs/l3_broadcast.log` | 全息监控日志，调度器、任务成败、推荐牛人、收网抓取等实时状态 |

排查错误时优先查看 `logs/l3_debug.log`，业务状态查看 `logs/l3_broadcast.log`。

## MCP / Skill 订阅

- L3 启动后通过 L2 从 L1 拉取已订阅的 MCP 与 Skill 到 `~/.jachin/l3_mcp_cache`、`l3_skill_cache`

### 多节点 / 跨机 MCP 委托（可选）

若使用 Redis + L2 委托到多台 L3：在 **L2 与每台执行委托的 L3** 配置相同 **`JACHIN_MCP_TASK_TOKEN_SECRET`**（见 `docs/MCP_EXECUTION_MODEL.md`）。无 Redis 时仅 HTTP 入站委托，NAT 场景不可靠。
- Lark、HR、BI 等能力均以 MCP/Skill 形式从 L1 订阅，不在便携包内

## 依赖说明

- **无需 Python**：L3 以 exe 形式运行
- **无需 Node**：若仅用 L3 独立模式（`run_l3.ps1`）
- **首次接入**：需能访问 L2 网关完成 L3 神经接驳（L3–L2）；L2–L1 信任见上文

## 故障排查

1. **ConnectError / All connection attempts failed**：新机器或 L2 未启动时会出现。使用独立模式启动：
   ```powershell
   .\scripts\run_l3.ps1 --ws-only
   ```
   需在 `.env` 配置 `DASHSCOPE_API_KEY`，无 MCP/Skill 订阅能力。
2. **exe 启动后无响应**：查看 `logs/l3_debug.log` 是否有异常
3. **MCP/Skill 不可用**：确认 L2 已与 L1 建立信任（`nexus_config`）且已在 L2 订阅对应 MCP/Skill；L3 拉取后写入 `~/.jachin/l3_mcp_cache`、`l3_skill_cache`
4. **Lark 等 MCP 配置**：订阅下载后配置在 `~/.jachin/config/mcps/{plugin_id}/`，按包内 manifest 写出
5. **Lark「我要招聘」无回复**：见下方「Lark 无回复排查」；架构与数据路径见 [HR_RECRUITMENT.md](./HR_RECRUITMENT.md)

### Lark 无回复排查

发送「我要招聘」后无回复，常见原因：

| 现象 | 原因 | 处理 |
|------|------|------|
| `connect failed, err: 1000040351: Incorrect domain name` | 应用与域名不匹配 | 应用在 **飞书中国版** 创建 → `im_channels.yaml` 中 `domain: "https://open.feishu.cn"`；在 **Lark 国际版** 创建 → `domain: "https://open.larksuite.com"` |
| `[IM Lark] 未配置 app_id/app_secret` | 未配置 Lark 凭证 | 在 `~/.jachin/config/im_channels.yaml` 填写 `app_id`、`app_secret`，或设置环境变量 `LARK_APP_ID`、`LARK_APP_SECRET` |
| 无 `[IM Lark] 收到消息` 日志 | 长连接未建立或未收到消息 | 确认 Lark 后台「事件订阅」已启用，且长连接连接成功；检查 `chat_ids` 是否过滤了当前会话 |
| `HR 招聘 MCP 包未找到` | 未订阅或未拉取 HR 包 | 在 L2 订阅 `com.jachin.hr.recruitment`，L3 启动后自动拉取到 `~/.jachin/l3_mcp_cache` |
| `ConnectError: Cannot connect to host dashscope.aliyuncs.com` | 本机无法访问阿里云 DashScope | 检查网络/防火墙；需代理时设置 `HTTP_PROXY`、`HTTPS_PROXY`；或换用可访问的 LLM |

**配置路径**：`~/.jachin/config/im_channels.yaml`（可复制 `config/im_channels.yaml.example` 后修改）
