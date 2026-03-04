# Jachin Nexus 白皮书 v6.0 — 文档索引 (The Neural Bus Edition)

**版本**: v6.0  
**更新日期**: 2026-02  
**核心基调**: 双轨制引擎、量子记忆、全息感知、一切皆技能

---

## ⚠️ 架构宪法 (The Constitution)
致所有阅读此文档的开发者与 AI 编程助手（如 Cursor）：
1. 本项目已**全面弃用** Dapr、Ray 集群、本地 PostgreSQL 和复杂 Docker 编排。
2. Layer 2 执行引擎为**双轨制 (Dual-Track)**：轨道 A (MCP)、轨道 B (SKILL.md)、轨道 C (Wasm 沙箱)。
3. **轨道 A (MCP)**：供高信任本地环境，继承全球 AI 工具生态，开箱即用。
4. **轨道 B (SKILL.md)**：`skills_repo/` 下声明式 Markdown 技能，热加载，零编译。
5. **轨道 C (Wasm)**：商城下载的第三方付费插件，必须在 The Abyss 沙箱中运行。
6. 记忆系统：**量子记忆** = 生物学梦境 + Vector SQLite (sqlite-vss/lancedb)，支持自我修复 (Self-Healing)。
7. 主动心跳：**生物钟 cron_thinker** 脱离云端，每 30 分钟主动环顾（系统日志、未读邮件、异常报警）。
8. 全息感知：Layer 1 **Universal Message Adapter** 统一多渠道；Layer 3 **Voice Wake** (Hey Jachin) + **jachin-cli**。
9. 设备鉴权、配对通过 Layer 3 Tauri 扫码或 `jachin-cli pair` 完成。

---

## 文档列表

| 序号 | 文档 | 内容概要 |
|------|------|----------|
| 01 | [设计目的](./01_DESIGN_PURPOSE.md) | Jachin 解决什么问题、B2B/B2C 定位（对标 OpenClaw） |
| 02 | [框架架构](./02_FRAMEWORK.md) | 三位一体 + 双轨制引擎 + 量子记忆 + 全息感知 |
| 03 | [业务流程](./03_WORKFLOW.md) | 扫码/CLI 配对、心跳、cron_thinker、ReAct、Voice Wake |
| 04 | [文件结构](./04_FILE_STRUCTURE.md) | 纯净目录树、skills_repo、MCP、cron_thinker |
| 05 | [Layer 1 云端中枢](./05_LAYER1_NEXUS.md) | 免密登录、舰队、Forge、Universal Message Adapter |
| 06 | [Layer 2 边缘引擎](./06_LAYER2_EDGE.md) | 双轨制、MCP、SKILL.md、Wasm、量子记忆、cron_thinker |
| 07 | [Layer 3 灵动终端](./07_LAYER3_TERMINAL.md) | Tauri、Voice Wake、jachin-cli、扫码配对 |
| 08 | [JPP 与技能生态](./08_JPP_SDK_AND_SKILLS.md) | JPP (轨道 C)、MCP (轨道 A)、SKILL.md (轨道 B) |
| — | [MCP 接入规范](../MCP_SPEC.md) | MCP Client 实现、工具发现与调用 |
| — | [SKILL.md 规范](../SKILL_MD_SPEC.md) | 声明式技能格式、Persona、热加载 |
