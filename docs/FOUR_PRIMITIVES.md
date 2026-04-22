# Four primitives — canonical index

**现行系统总览（实现索引，2026-04）**: [architecture/CURRENT_SYSTEM_ARCHITECTURE.md](./architecture/CURRENT_SYSTEM_ARCHITECTURE.md) · **L3 Memory Nexus（Chroma）**: [architecture/MEMORY_NEXUS_L3.md](./architecture/MEMORY_NEXUS_L3.md)

**混合架构白皮书（L3 单主轴 ReAct + L4 语义层 / 内联 Critic / Experience RAG）**: [architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md](./architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md)

**中文单一事实来源（全文）**: [Jachin 视角的「四大原语」终极架构规范.md](./Jachin%20视角的「四大原语」终极架构规范.md)

**Cursor 规则**: `.cursor/rules/072-jachin-four-primitives.mdc`、`.cursor/rules/045-four-primitives-execution.mdc`（执行面路由；**已删除**旧 `045-dual-track-mcp.mdc`）

**一句话**：**Tools** = 原子执行（`core:*` / `jpp:*`）；**MCP** = 协议外挂（`mcp:*`）；**Skills** = 声明式 SOP/白名单（`SKILL.md`、域文档、Skill 包元数据）；**Agent Tasks** = 多轮子运行时（`delegate`、`submit_background_task`、`coordinate`）。

**桌面端 Tool 生成式 UI（内嵌 / 右侧画布 / 纯文本默认）**：[SKILL_UI_VISUALIZATION_MODES.md](./SKILL_UI_VISUALIZATION_MODES.md)
