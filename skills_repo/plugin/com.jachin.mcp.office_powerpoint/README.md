# com.jachin.mcp.office_powerpoint

Jachin 封装的 **PowerPoint / PPTX MCP**（stdio），上游为 [Office-PowerPoint-MCP-Server](https://github.com/GongRzhe/Office-PowerPoint-MCP-Server)，PyPI 包名 `office-powerpoint-mcp-server`（MIT）。

## 安装依赖

在 **与 L3 拉起 MCP 子进程相同的 Python** 上安装（若使用 `JACHIN_MCP_PYTHON` / 嵌入式 Python，请对该解释器安装）：

```bash
pip install -r requirements.txt
```

## 行为说明

- L3 启动时由 `l3_packaged_stdio_mcp` 扫描本目录的 `plugin.json`，等价于在 `~/.jachin/mcp_servers.json` 增加一条 stdio 配置。
- 默认将 `PPT_TEMPLATE_PATH` 设为 `~/.jachin/workspace/ppt_templates`（可自行放入 `.potx` / `.pptx` 模板）。
- **不要**同时在 `mcp_servers.json` 里重复注册相同 `id`，否则只会保留先连接成功的一项。

## 发布

按 `docs/SKILL_MCP_UPLOAD_SPEC.md` 将本目录打包为 L3_LOCAL MCP 上架即可；无额外配置文件时可省略 `config/manifest.yaml`。
