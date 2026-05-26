---
name: pmo-copilot-resource-monitor
version: "0.0.0"
description: "【暂停】资源预警 Skill 待 PMO v6 DB 架构落地后重写。当前请勿用于生产调度。"
persona: |
  本 Skill 已暂停维护。PMO 主路径已迁移至 pmo-copilot-enterprise v6（SQLite + core:db_query/db_write）。
  若调度器加载本文件，应输出 all_clear 并跳过 Lark 推送，或提示管理员改用主 Skill。
mcp_tools: []
native_tools: []
---

# PMO 资源预警（暂停）

> **状态**：`SKILL.resource-monitor.md` 在 **PMO v6 DB 重构** 完成前 **不重写**。  
> **主 Skill SSOT**：`skills_repo/pmo-copilot/SKILL.md`（v6.0.0）  
> **架构 SSOT**：`docs/architecture/PMO_DB_REFACTOR_DESIGN.md`

定时调度（`pmo_copilot_scheduler.py`）若仍引用本文件，应：

1. 记录日志：`resource_monitor_skipped: pending_v6_rewrite`
2. **不**调用 `atom_lark_notifier`
3. 返回 `resource_monitor_result: all_clear`

待 v6 分析层稳定后，资源预警将改为：**查 `pmo_personnel_task_progress` + §1.4.1b 规则 → 有告警才推送**。
