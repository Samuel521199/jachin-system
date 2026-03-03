# Jachin Nexus 白皮书 v5.0 — 文档索引 (The Zero-Friction Edition)

**版本**: v5.0  
**更新日期**: 2026-03  
**核心基调**: 重云轻端、极简部署、物理沙箱、零摩擦体验

---

## ⚠️ 架构宪法 (The Constitution)
致所有阅读此文档的开发者与 AI 编程助手（如 Cursor）：
1. 本项目已**全面弃用** Dapr、Ray 集群、本地 PostgreSQL 和复杂 Docker 编排。
2. Layer 2 必须保持极致轻量，核心为 `core/daemon.py` (心跳) + `core/agent_loop.py` (ReAct 大脑) + `core/wasm_runner.py` (WASI 沙箱)。
3. 所有第三方技能必须编译为 WebAssembly 运行，严禁在宿主机直接跑不受信任的 Python/Node 代码。
4. 所有的设备鉴权、配对必须通过 Layer 3 Tauri 的“扫码即连”和“静默唤醒”完成。

---

## 文档列表

| 序号 | 文档 | 内容概要 |
|------|------|----------|
| 01 | [设计目的](./01_DESIGN_PURPOSE.md) | Jachin 解决什么问题、B2B/B2C 定位（对标 OpenClaw） |
| 02 | [框架架构](./02_FRAMEWORK.md) | 三位一体架构 (Layer 1-3) 及全面革新的技术栈 |
| 03 | [业务流程](./03_WORKFLOW.md) | 扫码配对、心跳轮询、IM 跨网触发及 ReAct 循环 |
| 04 | [文件结构](./04_FILE_STRUCTURE.md) | 经过“大清洗”后的纯净目录树及各文件职责 |
| 05 | [Layer 1 云端中枢](./05_LAYER1_NEXUS.md) | 免密登录、舰队指挥大盘、The Forge、IM Webhook |
| 06 | [Layer 2 边缘引擎](./06_LAYER2_EDGE.md) | Daemon Loop、ReAct 持久化记忆大脑、WASI 熔断沙箱 |
| 07 | [Layer 3 灵动终端](./07_LAYER3_TERMINAL.md) | Tauri 架构、QR 配对流、Rust 静默唤醒 |
| 08 | [JPP 插件与生态](./08_JPP_SDK_AND_SKILLS.md) | Jachin Plugin Protocol、WASI stdin/stdout 通信、版税机制 |