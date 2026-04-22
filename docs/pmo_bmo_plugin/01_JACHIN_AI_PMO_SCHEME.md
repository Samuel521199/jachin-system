# AI-PMO 方案（适配 Jachin）

## 1. 与原方案的关系

原《BMO插件方案》中的 **四大模块**（负荷热力、上下游阻滞、调度建议、监督催办）与 **双 Pipeline**（知识库同步线 + 效能监督线）保持不变。以下为 **Jachin 落地命名**（与仓库代码一致）。

## 2. Jachin 分层映射（概念）

| 原方案中的角色 | 在 Jachin 中的落点 |
|----------------|-------------------|
| 定时 / 事件触发 | L3 **Skill** `com.jachin.pmo.bmo`（`run_pmo_knowledge_sync` 等）；飞书对话走现有 **长连接 → L3 Agent**（与 BI/HR 同体系） |
| 文档 / Wiki 只读拉取 | **`mcp:atom_pmo_lark_doc`**（`operation=sync` 等同 BI 侧 `sync_bi_project_context`，默认输出 `docs/pmo_bmo_plugin/synced/`）；亦支持 `list_nodes` / `read_doc` |
| 分块与向量预览、落盘 corpus | **`mcp:atom_pmo_knowledge_base`**（`operation=ingest`）→ `docs/pmo_bmo_plugin/corpus/` |
| 向量检索「公司项目文档」（可选进阶） | **集中式**：`POST /api/v2/memory/sync` + `namespace=pmo_corpus`（L2 路由，见 [02_MCP_DESIGN.md](./02_MCP_DESIGN.md)）；与 L3 默认 **Memory Nexus** 宿主记忆分立 |
| 催办状态 / Wait_For_Action | **L2**：`namespace=pmo_action`（Pipeline B 扩展） |
| 推送到飞书 | **复用 `mcp:atom_lark_notifier`**（与 BI 共用实现，勿复制代码） |
| 多维表读写（需求池/调配） | **`mcp:atom_pmo_bitable`**（规划中，见设计文档） |

## 3. 四大模块（简要，逻辑不变）

1. **工作负荷热力**：从多维表聚合经办人、状态、工时/截止；LLM 输出过载/饥饿诊断；推送卡片。
2. **上下游阻滞**：结合团队结构（可配置 YAML / 干系人表）与 PRD/任务状态做阻塞链分析。
3. **智能调度**：检索干系人技能矩阵（表或文档同步结果）；给出可执行调配建议。
4. **监督闭环**：上次建议写入记忆；下次运行对比表格是否变化；超时升级告警。

健康分（0–100）可作为 **Skill 输出模板** 中的一节，由 LLM 根据规则生成。

## 4. 双 Pipeline（Jachin 编排）

### Pipeline A：知识库同步线（已实现骨架）

| 步骤 | 实现 |
|------|------|
| 拉取 PRD / 需求评审 / 排期等 | **`mcp:atom_pmo_lark_doc`**，`operation=sync`，配置 `wiki_urls` 与 Lark 凭证 |
| 分块 + Embedding 预览 + 落盘 | **`mcp:atom_pmo_knowledge_base`**，`operation=ingest` |
| 编排入口 | **`run_pmo_knowledge_sync`**（`l3_node/skills/pmo_bmo/main_skill.py`），配置 `config/skills/com.jachin.pmo.bmo/pmo_bmo.yaml` |

### Pipeline B：效能监督与调配线（规划中）

依赖 **`mcp:atom_pmo_bitable`**、L2 `pmo_corpus` / `pmo_action` 检索、**`atom_lark_notifier`** 告警；交互通过 **自然语言 + L3 Agent**，不依赖飞书原生按钮。

## 5. 凭证与权限

- Lark 应用：**知识库只读**、**IM 发消息**（与 `atom_lark_notifier` 可同一应用或分应用，仅改配置）。
- 配置规范：`config/mcps/*/config.yaml`（075），敏感项 `.gitignore`。

## 6. 与《BMO插件方案》文档的对应

方案中的 **Cron 定时、Webhook、LLM 节点、负荷/阻塞诊断 Prompt** 仍适用；落地时 **工具名** 以上表为准，**lark_bot_mcp** 统一为 **`mcp:atom_lark_notifier`**，**lark_doc_mcp** 为 **`mcp:atom_pmo_lark_doc`**，**knowledge_base_mcp** 为 **`mcp:atom_pmo_knowledge_base`**。
