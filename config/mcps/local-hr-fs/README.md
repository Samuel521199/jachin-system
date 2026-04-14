# local-hr-fs MCP 配置

供 HR 简历透视镜技能读取 `data/hr_resumes/` 下的简历文件。

## 安装

将本目录复制到 `~/.jachin/config/mcps/local-hr-fs/` 或 `~/.jachin/inventory/mcps/local-hr-fs/`。

`__PROJECT_ROOT__` 会在 L2 启动时由 inventory_scanner 替换为项目根目录。

**根路径**：建议只配置**一个** `__PROJECT_ROOT__`（整仓可读 `data/hr_resumes`、`config/...`）。  
勿使用不存在的子路径（例如历史上误写的 `config/hr_jds`），否则 `@modelcontextprotocol/server-filesystem` 会立即退出，L2 日志出现 `Connection closed`。

`npx` 包名已固定为 `@modelcontextprotocol/server-filesystem@0.6.3`，与 `mcp` Python SDK 1.26+ 及 `tools/list` 参数序列化行为对齐；若你仍用无版本号的 `-y @modelcontextprotocol/server-filesystem`，可能拉到不兼容版本。

若你本地 `~/.jachin/inventory/mcps/local-hr-fs/config.json` 仍是旧版多路径，请同步为本仓库 `config.json` 或删除多余无效目录参数。
