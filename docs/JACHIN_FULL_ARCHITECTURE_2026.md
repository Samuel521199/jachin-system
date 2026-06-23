# Jachin AI OS — 完整架构文档 2026

> **状态**: 已拆分至 [`docs/arch/`](./arch/README.md)（2026-05-28）  
> **记忆底座**: Memory Nexus — **SQLite + FastEmbed**（`memory_nexus.sqlite3`），非 Chroma  
> **请勿在本文件维护正文**；新增或修改架构说明请编辑 `docs/arch/` 下对应分册。

---

## 文档索引

完整架构采用「总—分」结构，入口：**[`docs/arch/README.md`](./arch/README.md)**

| 分册 | 链接 |
|------|------|
| 01 三层系统架构 | [`docs/arch/01_THREE_LAYER_SYSTEM.md`](./arch/01_THREE_LAYER_SYSTEM.md) |
| 02 主 Agent 设计 | [`docs/arch/02_MAIN_AGENT_DESIGN.md`](./arch/02_MAIN_AGENT_DESIGN.md) |
| 03 多 Agent 架构 | [`docs/arch/03_MULTI_AGENT.md`](./arch/03_MULTI_AGENT.md) |
| 04 记忆架构 | [`docs/arch/04_MEMORY_ARCHITECTURE.md`](./arch/04_MEMORY_ARCHITECTURE.md) |
| 05 AGI 核心能力 | [`docs/arch/05_AGI_CORE_CAPABILITIES.md`](./arch/05_AGI_CORE_CAPABILITIES.md) |
| 06 并发调度与韧性 | [`docs/arch/06_CONCURRENCY_RESILIENCE.md`](./arch/06_CONCURRENCY_RESILIENCE.md) |
| 07 可观测性与自治 | [`docs/arch/07_OBSERVABILITY_AUTONOMY.md`](./arch/07_OBSERVABILITY_AUTONOMY.md) |

---

## 相关 SSOT

- 全局架构规范：[`docs/ARCHITECTURE.md`](./ARCHITECTURE.md)
- 现行实现快照：[`docs/architecture/CURRENT_SYSTEM_ARCHITECTURE.md`](./architecture/CURRENT_SYSTEM_ARCHITECTURE.md)
- 四大原语：[`docs/FOUR_PRIMITIVES.md`](./FOUR_PRIMITIVES.md)
