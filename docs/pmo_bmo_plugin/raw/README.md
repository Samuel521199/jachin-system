# PMO 多维表导出（Markdown）

由 **`mcp:atom_pmo_lark_doc`**、`operation=export_pmo_tables` 或 Skill `run_pmo_knowledge_sync`（`pipeline.export_scheduled_tables: true`）生成。

- 文件名：**`{slug}.md`**（固定名，每轮导出覆盖，不按日累加）。原始 **JSON** 仍在 `~/.jachin/client_volumes/PMO/raw/{YYYY-MM-DD}_{slug}.json`（按快照日区分）。
- **slug** 含义见 `l3_node/mcp_tools/pmo_bmo/tool_pmo_bitable_export.py` 中 `PMO_SCHEDULED_BITABLES`（六张：3 月需求细分/大表、开发两视图、美术两视图）。
- DuckDB：`~/.jachin/client_volumes/PMO/duckdb/pmo.duckdb`，表 `pmo_bitable_records`、`pmo_bitable_export_meta`。
