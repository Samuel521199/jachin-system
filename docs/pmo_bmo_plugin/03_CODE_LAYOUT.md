# PMO/BMO 代码与配置落点

| 类型 | 路径 |
|------|------|
| MCP `atom_pmo_lark_doc` | `l3_node/primitives/mcp/mcp_tools/pmo_bmo/tool_lark_doc.py`（兼容：`l3_node/mcp_tools/pmo_bmo/` 薄转发） |
| MCP `atom_pmo_knowledge_base` | `l3_node/primitives/mcp/mcp_tools/pmo_bmo/tool_knowledge_base.py` |
| 飞书发送（复用 BI） | `l3_node/primitives/mcp/mcp_tools/bi/tool_lark_notifier.py`（`mcp:atom_lark_notifier`）；说明见 `l3_node/primitives/mcp/mcp_tools/pmo_bmo/lark_notifier_bridge.py` |
| L3 MCP 注册 | `l3_node/primitives/mcp/registry.py` → `L3_LOCAL_MCP_TOOLS` 与 `_invoke_*_local` |
| Skill | `l3_node/primitives/skills/pmo_bmo/main_skill.py`（`run_pmo_knowledge_sync`；兼容：`l3_node/skills/pmo_bmo/` 薄转发） |
| 技能配置 | `config/skills/com.jachin.pmo.bmo/pmo_bmo.yaml` |
| MCP 配置模板 | `config/mcps/atom_pmo_lark_doc/config.yaml.example`、`config/mcps/atom_pmo_knowledge_base/config.yaml.example` |
| 同步产出（PMO 默认） | `docs/pmo_bmo_plugin/project_progress_daily/YYYY-MM-DD/`（K11 三表 + `00_K11_TABLES_INDEX.md`） |
| 同步产出（`daily_snapshot: false`） | `docs/pmo_bmo_plugin/synced/` |
| 分块 corpus 默认目录 | `docs/pmo_bmo_plugin/corpus/chunks/`、`ingest_manifest.jsonl` |
| 六表 JSON 原始导出 | `~/.jachin/client_volumes/PMO/raw/{date}_{slug}.json` |
| 六表 MD 导出 | `docs/pmo_bmo_plugin/raw/{slug}.md`（固定名覆盖） |
| PMO DuckDB | `~/.jachin/client_volumes/PMO/duckdb/pmo.duckdb`（`pmo_bitable_records` / `pmo_bitable_export_meta`） |
| 导出实现 | `l3_node/primitives/mcp/mcp_tools/pmo_bmo/tool_pmo_bitable_export.py`，`atom_pmo_lark_doc` 的 `operation=export_pmo_tables` |


## 默认 MCP 初始化

首次加载时，`l3_node/jachin_config.py` 中 `_MCP_DEFAULTS` 可为 `atom_pmo_*` 在 `~/.jachin/config/mcps/` 生成模板（与 075 规范一致）。
