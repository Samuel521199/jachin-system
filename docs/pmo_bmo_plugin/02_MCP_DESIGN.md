# MCP 设计定稿（Jachin AI-PMO / BMO）

与 [01_JACHIN_AI_PMO_SCHEME.md](./01_JACHIN_AI_PMO_SCHEME.md)、[03_CODE_LAYOUT.md](./03_CODE_LAYOUT.md) 一致。

---

## 1. 总览表（已实现 + 规划）

| MCP ID | 状态 | 职责 |
|--------|------|------|
| **`atom_pmo_lark_doc`** | **已实现** | Wiki 同步（`sync`）、列子节点（`list_nodes`）、读 docx（`read_doc`）；同步逻辑与 `atom_bi_project_context` 同源，默认输出 `docs/pmo_bmo_plugin/synced/` |
| **`atom_pmo_knowledge_base`** | **已实现** | 对同步目录下 Markdown 分块、可选向量预览、写入 `docs/pmo_bmo_plugin/corpus/` |
| **`atom_lark_notifier`** | **复用（不复制）** | 飞书 Markdown/卡片；实现位于 `l3_node/mcp_tools/bi/tool_lark_notifier.py` |
| **`atom_pmo_bitable`** | **规划中** | PMO 多维表查询/更新（需求池、调配）；与 HR 多维表隔离配置 |
| **L2 Memory API** | **可选进阶** | `pmo_corpus` / `pmo_action` 命名空间；与落盘 corpus 可并存 |
| **`human_ask`** | **复用（可选）** | 高危操作前 HITL |

---

## 2. 方案名词 → 仓库映射

| 原方案（BMO插件方案） | Jachin |
|------------------------|--------|
| `lark_doc_mcp` | **`mcp:atom_pmo_lark_doc`** |
| `knowledge_base_mcp` | **`mcp:atom_pmo_knowledge_base`**（文件级 corpus；进阶可再接 L2） |
| `lark_bot_mcp` | **`mcp:atom_lark_notifier`**（与 BI 共用） |

---

## 3. 为何飞书发送不放在 `pmo_bmo/` 下复制一份

`atom_lark_notifier` 已是 **纯发送** 能力，无 BI 业务耦合。PMO 在 `l3_node/mcp_tools/pmo_bmo/lark_notifier_bridge.py` 中说明 **直接复用**；若需独立机器人，仅复制 **配置文件**（`app_id` / `chat_id`），不复制 Python。

---

## 4. `atom_pmo_bitable`（Pipeline B）

多维表读写与 HR 招聘表结构不同，后续单独 MCP + `config/mcps/atom_pmo_bitable/config.yaml`，底层可对齐 `l3_node/channels/lark/bitable.py`。

---

## 5. 小结

| 类型 | 名称 |
|------|------|
| PMO 新建 MCP | `atom_pmo_lark_doc`、`atom_pmo_knowledge_base` |
| 复用 | `atom_lark_notifier` |
| 规划中 | `atom_pmo_bitable`、L2 `pmo_*` namespace |
