# L3 嵌入式 Python / Node 运行时（MCP stdio）

**版本**: 2026-04  
**定位**：在**目标机不安装**系统 Python / Node 的前提下，仍能拉起 `python -m mcp_server_*`、`npx -y @scope/mcp` 等 stdio MCP。  
**代码 SSOT**：`core/mcp_embedded_runtime.py`（路径解析）、`tools/mcp-runtime/README.txt`（目录约定）。

---

## 1. 为什么需要单独说明 Node

多数「官方 MCP」（含 Anthropic 生态常用包）在配置里写作 **`command: npx`**。仅解压 `node.exe` 而**没有同目录的 `npx.cmd`（Windows）** 时，`npx` 无法解析。  
本仓库已支持：

- 在 `~/.jachin/runtime/node/`（或 `JACHIN_APP_ROOT/runtime/node/`、**与 L3 Sidecar exe 同目录的 `runtime/node/`**）部署 **官方 Node 便携包完整内容**；
- 配置里使用裸 **`npx`** / **`npm`** 或占位符 **`__JACHIN_MCP_NPX__`** 时，优先解析到上述目录中的 **`npx.cmd` / `npx`**。

---

## 2. 目录布局（与 L1 Linux 便携包一致）

在以下**任一根**下放置 `runtime/python/` 与 `runtime/node/`（见 `core/mcp_embedded_runtime._runtime_base_dirs()`）：

| 根 | 适用场景 |
|----|-----------|
| `JACHIN_APP_ROOT/runtime` | 安装器「绿色目录」根 |
| `~/.jachin/runtime` | 用户目录（默认 `JACHIN_HOME`） |
| `{l3_node-*.exe 所在目录}/runtime` | **PyInstaller Sidecar**：与桌面/Tauri 同发的 L3 二进制旁 |

**Node（Windows 示例）**：从 [Node.js 官方](https://nodejs.org/) 下载 **Windows x64 zip**，解压后应包含同一目录下的 `node.exe`、`npx.cmd`、`npm.cmd`。将该目录内容整体复制到：

`.../runtime/node/`（即 `runtime/node/node.exe`、`runtime/node/npx.cmd` 并列）。

---

## 3. 环境变量（覆盖自动探测）

| 变量 | 含义 |
|------|------|
| `JACHIN_MCP_PYTHON` | `python.exe` 绝对路径 |
| `JACHIN_MCP_NODE` | `node.exe` 绝对路径 |
| `JACHIN_MCP_NPX` | `npx` / `npx.cmd` 绝对路径 |
| `JACHIN_MCP_NPM` | `npm` / `npm.cmd` 绝对路径（可选） |

---

## 4. MCP 配置写法

- **推荐**：`mcp_servers.json` / `plugin.json` 的 `stdio_server.command` 仍写 **`npx`**（或 **`__JACHIN_MCP_NPX__`**），由解析器映射到嵌入式路径。
- **显式绝对路径**：若已写死本机 `C:\...\npx.cmd`，解析器**不会**改写成另一路径。

---

## 5. 安装器 / 打包建议

1. 在安装程序中增加一步：下载或随包附带 **Node 官方 zip**，解压到目标机 `runtime/node/`（与 `tools/mcp-runtime/README.txt` 一致）。
2. 若使用 **Tauri + L3 Sidecar**：将 `runtime` 与 `clients/desktop/src-tauri/bin/l3_node-*.exe` 置于同一父目录，或安装到 `%USERPROFILE%\.jachin\runtime\`。
3. 可选：在首次启动 L3 前运行 `scripts/stage-l3-mcp-node-runtime.ps1`（见脚本内说明）从本机已有 Node 目录复制到 `~/.jachin/runtime/node`（开发机辅助，非终端用户必选）。

---

## 6. 相关文档

- [L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md](./L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md) — slim L3 and subscribed artifact boundary
- [SKILL_MCP_UPLOAD_SPEC.md](./SKILL_MCP_UPLOAD_SPEC.md) — `stdio_server` 与占位符  
- [architecture/CURRENT_SYSTEM_ARCHITECTURE.md](./architecture/CURRENT_SYSTEM_ARCHITECTURE.md) — MCP 小节
