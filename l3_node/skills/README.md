# L3 技能加载器

V2 架构下 L3 单体的真实技能层。将 Native Core 与可扩展技能转化为大模型可识别的 tools 格式。

## 内置工具

| 工具 ID | 说明 | 权限 |
|---------|------|------|
| `core:fs_read` | 读取文件 | 限于 ~/.jachin/workspace/ |
| `core:fs_write` | 写入文件 | 限于 ~/.jachin/workspace/ |
| `core:shell_exec` | 执行 Shell 命令 | 工作目录死锁在 ~/.jachin/workspace/ |

## 使用

```python
from l3_node.skills import load_tools, run_tool, build_tools_description

tools = load_tools()
desc = build_tools_description(tools)  # 供 Agent system prompt

result = run_tool("core:fs_read", "target.txt")
```

## 扩展

后续可在此目录下添加 Python 脚本或 JSON 配置，由 loader 扫描并注册为新工具。
