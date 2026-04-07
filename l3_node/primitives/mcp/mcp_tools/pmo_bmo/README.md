# PMO/BMO — L3 MCP 工具目录

| MCP ID | 模块 | 说明 |
|--------|------|------|
| `mcp:atom_pmo_lark_doc` | `tool_lark_doc.py` | `sync` / **`export_pmo_tables`**（六表 JSON→`~/.jachin/client_volumes/PMO/raw`，MD→`docs/pmo_bmo_plugin/raw`，DuckDB）/ `list_nodes` / `read_doc` |
| `mcp:atom_pmo_knowledge_base` | `tool_knowledge_base.py` | 分块、可选向量、写入 `docs/pmo_bmo_plugin/corpus/` |
| `mcp:atom_lark_notifier` | `../bi/tool_lark_notifier.py` | **复用**，见 `lark_notifier_bridge.py` |

路径辅助：`paths.py`（`get_pmo_raw_dir`、`get_pmo_duckdb_path`）。

配置模板：`config/mcps/atom_pmo_lark_doc/config.yaml.example`、`config/mcps/atom_pmo_knowledge_base/config.yaml.example`。
