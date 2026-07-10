# MCP stdio 子进程与 API Key / 环境变量

**版本**: 2026-04
**定位**: 说明为何会出现「父进程已有 Key、MCP 仍报找不到 API Key」类问题，以及 Jachin 侧的通用约束与 Tavily 专项修复；供新增 **依赖环境变量密钥** 的 stdio MCP 时对照，避免重复踩坑。

**相关**: `docs/MCP_SPEC.md`、`core/mcp_embedded_runtime.py`、`core/mcp_client.py`、`core/l3_dotenv_merge.py`。

---

## 1. 典型现象

- **症状 A**：`list_tools` / MCP 会话握手成功，**首次**调用需要鉴权的工具时返回 JSON-RPC **`-32600`** 或文案含 `API_KEY` / `environment variable is required`。
- **症状 B**：Python / L3 日志显示已加载 `TAVILY_API_KEY`（或他键），与子进程配置「看起来」一致，但 Node 侧仍报错。
- **症状 C**（易混淆）：对话摘要或 compaction 里仍写着「tavily 失败」，实为**历史消息**中的旧叙述，与**当前**一次 MCP 调用是否成功无关；以同一次请求内的 `[TavilyMCP][invoke] outcome=ok` / `[MCP] invoke_tool` 为准。

---

## 2. 根因分析（三层）

### 2.1 MCP Python SDK：`env is None` 时子进程环境为「白名单」

官方 `mcp.client.stdio.stdio_client` 行为：当 `StdioServerParameters.env` 为 **`None`** 时，子进程环境仅为 `get_default_environment()`（PATH、USERPROFILE 等），**不会**继承 L3 的完整 `os.environ`。

因此：**仅依赖「子进程自动继承父进程环境」在 stdio MCP 下不成立**，除非显式传入非空 `env` dict（SDK 会合并 `{**get_default_environment(), **env}`）。

**结论**：配置里 **`"env": null` 且进程依赖 `process.env.XXX_API_KEY`** 的 Node MCP，极易在首次工具调用时才暴露缺 Key。

### 2.2 配置占位符与合并顺序

- `~/.jachin/mcp_servers.json` 中推荐使用 `"FOO_API_KEY": "${FOO_API_KEY}"`，由 `resolve_mcp_cfg_placeholders` 展开。
- 展开前会调用 `merge_l3_dotenv_into_os()`，尽量保证仓库根 / `JACHIN_APP_ROOT` / `~/.jachin/.env` 已并入 **当前 L3 进程**的 `os.environ`，否则占位符展开为空。
- 若展开后仍为空，实现上会对**同名键**尝试用 `os.environ` 回填（见 `resolve_mcp_cfg_placeholders`）。

### 2.3 Node 生态：`dotenv.config()` 与当前工作目录（cwd）

部分 npm MCP（如 **`tavily-mcp`**）在入口执行 `dotenv.config()`，默认读取 **进程 `cwd` 下的 `.env`**，再读取 `process.env.TAVILY_API_KEY`。

若用户在 **`clients/desktop`** 等子目录启动 `python -m l3_node`，stdio 子进程默认 **cwd** 可能落在子目录，而真实密钥仅在**仓库根** `.env` —— 则 Node 侧读不到根目录 `.env`，与 Python 侧已 merge 密钥**并存**。

**结论**：对「依赖 cwd 相对路径读 .env」的 MCP，需在宿主侧为子进程设置 **`cwd`** 到存在目标 `.env` 的目录（与 `merge_l3_dotenv_into_os` 选中的根一致）。

### 2.4 Windows：`npx.cmd` → Node 链式进程

在部分 Windows 环境下，仅靠「白名单 + 小 dict」合并后的环境，**npx 拉起实际 node 进程**时仍可能出现业务变量不可见；因此对 Tavily 增加了 **win32 下将完整父环境并入再覆盖** 的策略（见下节）。

---

## 3. Jachin 已落地修复（Tavily 为参考实现）

| 环节 | 行为 | 代码锚点 |
|------|------|----------|
| 禁止「无 env dict」 | Tavily 判定下，`effective_stdio_env_for_sdk` **始终返回 dict**，并从 `os.environ` 补 `TAVILY_API_KEY` | `effective_stdio_env_for_sdk` |
| 占位 + 无 env 块 | `resolve_mcp_cfg_placeholders` 对 Tavily 即使 JSON 未写 `env`，也注入 `TAVILY_API_KEY` | `_is_tavily_stdio_cfg` 分支 |
| Windows 全量父环境 | `expand_stdio_env_windows_npx_tavily`：win32 + Tavily 时合并完整 `os.environ` 再覆盖 | `MCPServerInstance.connect` |
| Node dotenv 与 cwd | `resolve_tavily_stdio_cwd` + `StdioServerParameters.cwd`，使 `dotenv.config()` 能读到仓库根 `.env` | `resolve_tavily_stdio_cwd`、`log_tavily_stdio_cwd_choice` |
| 调用失败重试 | `invoke_tool` 若返回含 `-32600` / `TAVILY_API_KEY`，可合并 dotenv 后 **重连** 该 server 并重试一次 | `MCPManager.invoke_tool`、`_reconnect_stdio_server_by_id` |
| 可检索日志 | `[TavilyMCP][chain] phase=...`；关闭：`JACHIN_LOG_TAVILY_CHAIN=0` | `log_tavily_mcp_chain` 等 |

**注意**：L3 另有 **Tavily HTTP 预取**（`l3_node/primitives/tavily_grounding.py`），与 **npm stdio MCP** 是两条路径；预取成功不等价于 stdio 子进程一定成功，排查时勿混为一谈。

---

## 4. 通用规范：新增「依赖 API Key」的 stdio MCP

以下适用于 **任意** 需在子进程内读取 `process.env.XXX` / 等价变量的 MCP（不限于 Tavily）。

### 4.1 配置（`mcp_servers.json`）

1. **`env` 禁止使用 `null`**（若该 MCP 文档要求密钥）：应提供显式字典，例如 `"API_KEY": "${YOUR_VENDOR_API_KEY}"`。
2. 密钥**不要**写入仓库版本库；本机 `~/.jachin/mcp_servers.json` 或占位符 + `.env`。
3. 在 **`.env.example`**（或产品文档）中登记变量名，与 JSON 占位符一致。

### 4.2 与 Python 侧对齐

1. 确保 L3 启动路径会执行 `merge_l3_dotenv_into_os`（入口、`MCPManager.start`、`resolve_mcp_cfg_placeholders` 已覆盖常见路径）。
2. 若 MCP 为 **Node** 且在入口使用 **`dotenv.config()` 无路径参数**：评估是否需要像 Tavily 一样设置 **`cwd`**；若需要，在 `MCPServerInstance.connect` 中为该 `server_id` 增加解析函数（可复用「选第一个含 `.env` 的候选目录」模式）。

### 4.3 Windows 专项

若在某 MCP 上出现「Linux/macOS 正常、Windows 首次工具调用才缺 Key」：

1. 先确认 `env` 非空且占位符已展开（日志或调试）。
2. 再考虑是否需 **win32 全量父环境合并**（当前仅 Tavily 实现；新增 MCP 可复制模式或抽象为通用 `server_id` 列表）。

### 4.4 观测与验收

1. 连接阶段：`Server 已连接 server_id=...`。
2. 调用阶段：对应 `[MCP] call_tool` / 业务包自定义的 invoke 日志。
3. **以首次真实 `tools/call` 成功为准**，不以 `list_tools` 为唯一依据。

---

## 5. 排查清单（简）

| 步骤 | 检查项 |
|------|--------|
| 1 | 仓库根或 `~/.jachin/.env` 是否包含变量，且 L3 日志中有「已加载」或 merge 成功 |
| 2 | `mcp_servers.json` 中该 server 的 `env` 是否为**非空** dict，且占位符名称与 `.env` 一致 |
| 3 | **Windows**：是否仍报 Key 缺失 → 看 `[TavilyMCP][chain]`（或后续通用日志）中 `stdio_spawn_merged` / `stdio_win32_full_parent_env` / `stdio_cwd_for_dotenv` |
| 4 | 区分 **stdio MCP** 与 **REST 预取**（`tavily_grounding`），避免用 A 的成功推断 B |

---

## 6. 参考链接

- Model Context Protocol Python SDK：`mcp.client.stdio` / `StdioServerParameters`
- Tavily 官方 MCP 包：`tavily-mcp`（npm），环境变量名以官方 README 为准
- 仓库内：`docs/MCP_SPEC.md` §3.4–3.5、`core/l3_dotenv_merge.py`
