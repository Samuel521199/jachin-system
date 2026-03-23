# L3 技能加载器

V2 架构下 L3 单体的真实技能层。将 Native Core 与可扩展技能转化为大模型可识别的 tools 格式。

## 内置工具

| 工具 ID | 说明 | 权限 / 备注 |
|---------|------|-------------|
| `core:fs_read` | 读取文件 | 限于 `~/.jachin/workspace/`（及 HR 白名单路径） |
| `core:fs_write` | 写入文件 | 限于 `~/.jachin/workspace/` |
| `core:shell_exec` | Shell（前台/后台 JSON） | cwd=`workspace`；**P1** 危险子串拦截/可选前缀白名单（`nexus_config.intelligence_p1`）；**P1+** 支持 `{"command","timeout","background":true}` |
| `core:shell_job_status` | 查后台任务日志尾 | 白名单含 `core:shell_exec` 时自动允许 |
| `core:shell_job_cancel` | 取消后台任务 | 需 `shell_job_cancel_enabled`；同上白名单联动 |

智能化配置与缓存：`l3_node/intelligence_p1.py`、`l3_node/tool_call_cache.py`、`l3_node/shell_jobs.py`。

## 使用

```python
from l3_node.skills import load_tools, run_tool, build_tools_description

tools = load_tools()
desc = build_tools_description(tools)  # 供 Agent system prompt

result = run_tool("core:fs_read", "target.txt")
```

## 扩展

后续可在此目录下添加 Python 脚本或 JSON 配置，由 loader 扫描并注册为新工具。

## BI 技能

`bi/` 目录：BI 每日战报 Skill 与调度。详见 [docs/bi_daily_report/08_BI_MCP_AND_SKILL_LAYOUT.md](../../docs/bi_daily_report/08_BI_MCP_AND_SKILL_LAYOUT.md)。
