# com.jachin.mcp.office_powerpoint

Jachin 封装的 **PowerPoint / PPTX MCP**（stdio），上游为 [Office-PowerPoint-MCP-Server](https://github.com/GongRzhe/Office-PowerPoint-MCP-Server)，PyPI 包名 `office-powerpoint-mcp-server`（MIT）。

## 安装依赖

在 **与 L3 拉起 MCP 子进程相同的 Python** 上安装（若使用 `JACHIN_MCP_PYTHON` / 嵌入式 Python，请对该解释器安装）：

```bash
pip install -r requirements.txt
```

## `apply_professional_design`（易错）

上游工具 `operation` **只能是**（字面量，区分大小写按实现为准，建议小写）：

| operation | 含义 | slide_index |
|-----------|------|----------------|
| `get_schemes` | 列出可用配色名 | 不需要 |
| `theme` | 对整个演示文稿应用 `color_scheme` | 不需要 |
| `professional_slide` | 新增一页专业样式幻灯片 | 按工具其它参数 |
| `enhance` | 美化**已有**某一页 | **必填**（0 起） |

常见模型误用：`operation: "apply"`（无效）、`"enhance"` 但不传 `slide_index`。
Jachin 在 `l3_node/primitives/mcp/registry.py` 会对上述情况做**调用前规范化**（如 `apply`→`theme`，无索引的 `enhance`→`theme`），并在工具描述中追加说明。

## 行为说明

- L3 启动时由 `l3_packaged_stdio_mcp` 扫描本目录的 `plugin.json`，等价于在 `~/.jachin/mcp_servers.json` 增加一条 stdio 配置。
- 默认将 `PPT_TEMPLATE_PATH` 设为 `~/.jachin/workspace/ppt_templates`（可自行放入 `.potx` / `.pptx` 模板）。
- **不要**同时在 `mcp_servers.json` 里重复注册相同 `id`，否则只会保留先连接成功的一项。

## 发布

按 `docs/SKILL_MCP_UPLOAD_SPEC.md` 将本目录打包为 L3_LOCAL MCP 上架即可；无额外配置文件时可省略 `config/manifest.yaml`。
