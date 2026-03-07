# local-hr-fs MCP 配置

供 HR 简历透视镜技能读取 `data/hr_resumes/` 下的简历文件。

## 安装

将本目录复制到 `~/.jachin/inventory/mcps/local-hr-fs/`：

```powershell
# Windows PowerShell
$dest = "$env:USERPROFILE\.jachin\inventory\mcps\local-hr-fs"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item config\local-hr-fs\* $dest -Recurse -Force
```

或手动创建 `~/.jachin/inventory/mcps/local-hr-fs/config.json`，内容：

```json
{
  "mcpServers": {
    "local-hr-fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "__PROJECT_ROOT__", "__PROJECT_ROOT__/data/hr_resumes"]
    }
  }
}
```

同时传入项目根与 `data/hr_resumes`，避免 MCP Roots 协议覆盖后路径校验失败。

`__PROJECT_ROOT__` 会在 L2 启动时由 inventory_scanner 替换为项目根目录（如 `D:\Projects\jachi\jachin-system-main`）。

## 重启 L2

配置后需重启 L2（`python -m core.main`）才能生效。
