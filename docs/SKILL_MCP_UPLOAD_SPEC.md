# Skill / MCP 上传规范

**版本**: 1.1
**关联**: `.cursor/rules/076-skill-mcp-upload-spec.mdc`、`.cursor/rules/075-config-root-and-cloud-sync.mdc`

---

## 一、目标

1. **上传时**：MCP 和 Skill 发布到 L1 时，配置必须随包一同上传
2. **下载后**：L2/L3 订阅拉取后，配置自动写出到目标机 `~/.jachin/config/`，立即可用
3. **单机部署**：目标机仅 exe + 网络，订阅后即可获得技能 + 配置，无需手动拷贝

---

## 二、包结构规范

### 2.1 标准结构（推荐）

```
{plugin_id}_v{version}.zip
├── plugin.json           # 必需：id、name、version、item_type、runtime_tier
├── main.wasm             # Skill 入口（MCP 则为 tools/*.py）
├── tools/                # L3_LOCAL MCP 的 Python 模块
│   ├── __init__.py
│   └── my_tool.py
└── config/               # 依赖配置时必需
    ├── manifest.yaml     # 配置写出清单，必需
    └── skills/           # 或 mcps/
        └── {plugin_id}/
            ├── bi_daily_report.yaml
            └── bi_metrics.yaml
```

### 2.2 plugin.json 必填字段

| 字段 | 说明 |
|------|------|
| `id` | 反向域名，如 `com.jachin.bi.daily_report` |
| `name` | 显示名称 |
| `version` | 语义化版本，如 `1.0.0` |
| `description` | 描述 |
| `item_type` | `SKILL` 或 `MCP` |
| `runtime_tier` | MCP **长期默认** `L3_LOCAL`（L2 落 `inventory/l3_mcps/`，L3 `mcp_sync` 拉取后本机执行）。`L2_GATEWAY` 仅兼容旧侧载。 |
| `required_mcps` | Skill 用：依赖的 MCP 列表，如 `["mcp:com.jachin.boss.atom"]`。L1 manifest 会据此自动将依赖 MCP 加入下发清单，L2 同步时一并拉取。 |

### 2.3 L3_LOCAL MCP：`tools[]`（Python）与 `stdio_server`（官方进程）

**新上架 MCP 须** `item_type=MCP` 且 `runtime_tier=L3_LOCAL`，并在 `jachin pack` 中通过下列**之一**：

| 形态 | plugin.json | 说明 |
|------|-------------|------|
| **Python 工具** | `"tools": [{ "id": "my_tool", "module": "tools.my_tool", "function": "run", "params": ["input"], "desc": "..." }]` | 包内 `tools/*.py`，由 `mcp_registry` 动态 import（与现网一致）。 |
| **stdio 声明式** | `"stdio_server": { "id": "fs-workspace", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem@0.6.2", "__JACHIN_WORKSPACE__"], "env": {} }` | 无 Python 实现；L3 启动时对应该目录执行 `MCPManager.add_server`。工具名由子进程 `tools/list` 决定。 |

可选：`"mcp_execution_mode": "stdio_server"`（显式标注；有 `stdio_server` 块时可省略）。

**占位符**（`args` / `env` 字符串）：`__PROJECT_ROOT__`、`__JACHIN_HOME__`、`__JACHIN_WORKSPACE__`（工作区目录不存在会自动创建）。与 `server-filesystem` 组合时，无效根路径会被跳过（与 `inventory` 侧载逻辑一致）。
另支持 **`__JACHIN_MCP_NPX__`**：解析为嵌入式 `runtime/node/npx.cmd` 或 PATH 中的 `npx`（与裸 `command: npx` 等价，见 `docs/L3_EMBEDDED_RUNTIME.md`）。

完整示例见 `docs/examples/l3_local_stdio_mcp.plugin.json`。

### 2.4 config/manifest.yaml 格式

```yaml
version: "1"
output_root: "~/.jachin"

writes:
  - path: "config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml"
    type: "file"
    merge: "overwrite_if_missing"
  - path: "config/skills/com.jachin.bi.daily_report/bi_metrics.yaml"
    type: "file"
    merge: "overwrite_if_missing"
  - path: "config/mcps/com.example.atom_web_scraper/config.json"
    type: "file"
    merge: "overwrite_if_missing"
  - path: "config/skills/com.jachin.hr.analyzer4/hr_jds"
    type: "directory"
    merge: "copy_missing"
```

| 字段 | 说明 |
|------|------|
| `path` | 写出到 `~/.jachin/` 下的相对路径，必须落在 `config/skills/{id}/` 或 `config/mcps/{id}/` |
| `type` | `file` 或 `directory` |
| `merge` | `copy_missing` / `overwrite_if_missing`：目标不存在才写；`never_overwrite`：已有则跳过 |

---

## 三、上传流程

### 3.1 本地开发

```bash
# 1. 进入技能/MCP 目录
cd skills_repo/my-skill   # 或 mcp_tools/my_mcp

# 2. 确保包含 config/
# 目录结构示例：
#   plugin.json
#   main.wasm
#   config/
#     manifest.yaml
#     skills/
#       com.jachin.my.skill/
#         my_config.yaml

# 3. 打包（校验 config + manifest）
jachin pack

# 4. 发布
jachin publish --visibility PUBLIC --price 0
```

### 3.2 jachin pack 校验

- 若存在 `config/` 目录，则 **必须** 存在 `config/manifest.yaml`
- `manifest.yaml` 中 `writes` 的 `path` 必须指向包内存在的文件/目录
- 校验失败时 `jachin pack` 报错，禁止打包

### 3.3 L1 publish 校验

- 解压 zip 后，若存在 `config/` 或 `payload/config/`，则 **必须** 存在 `config/manifest.yaml`
- 校验失败时返回 400，提示「包内含 config 目录但缺少 manifest.yaml」

---

## 四、下载与写出流程

### 4.1 数据流

```
L1 (manifest)  →  L2 (CloudSyncDaemon)  →  ~/.jachin/inventory/
                      ↓
               L3 (skill_sync / mcp_sync)  →  ~/.jachin/l3_skill_cache / l3_mcp_cache
                      ↓
               config_writeout.write_config_from_package()
                      ↓
               ~/.jachin/config/skills/ 或 config/mcps/
```

### 4.2 写出时机

| 环节 | 路径 | 写出 |
|------|------|------|
| L2 sync_daemon | SKILL → inventory/skills/ | 解压后调用 write_config_from_package |
| L2 sync_daemon | MCP L3_LOCAL → inventory/l3_mcps/ | 同上 |
| L2 sync_daemon | MCP L2_GATEWAY → inventory/mcps/ | 同上 |
| L3 mcp_sync | L3_LOCAL MCP → l3_mcp_cache/ | 解压后调用 write_config_from_package |

### 4.3 单机 L3 部署

目标机仅 `l3_node.exe` + 网络时：

1. L3 selects an L1 profile and publishes or installs through the L1 catalog API.
2. 订阅技能 / MCP
3. L3 拉取到 `l3_mcp_cache/` 或 `l3_skill_cache/`
4. 配置自动写出到 `~/.jachin/config/`
5. 用户可本机修改 `~/.jachin/config/skills/{id}/` 或 `config/mcps/{id}/`，后续同步不覆盖

---

## 五、敏感信息处理

- 配置模板使用占位符：`${BI_LARK_WEBHOOK_URL}`、`${BI_SMTP_PASSWORD}` 等
- **禁止**将真实密钥、Token 写入包内
- 用户下载后在本机填入实际值

---

## 六、示例

### 6.1 BI 每日战报 Skill

```
com.jachin.bi.daily_report_v1.0.0.zip
├── plugin.json
├── main.wasm
└── config/
    ├── manifest.yaml
    └── skills/
        └── com.jachin.bi.daily_report/
            ├── bi_daily_report.yaml
            └── bi_metrics.yaml
```

### 6.2 L3_LOCAL MCP

```
com.example.atom_web_scraper_v1.0.0.zip
├── plugin.json
├── tools/
│   ├── __init__.py
│   └── my_scraper.py
└── config/
    ├── manifest.yaml
    └── mcps/
        └── com.example.atom_web_scraper/
            └── config.json
```

---

## 七、参考

- [075-config-root-and-cloud-sync.mdc](../.cursor/rules/075-config-root-and-cloud-sync.mdc) — 配置根与写出逻辑
- [076-skill-mcp-upload-spec.mdc](../.cursor/rules/076-skill-mcp-upload-spec.mdc) — Cursor 规则
- [l3_node/config_writeout.py](../l3_node/config_writeout.py) — 写出实现
- [L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md](./L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md) — slim L3 and subscribed artifacts
