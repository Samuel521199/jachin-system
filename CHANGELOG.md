# Changelog

All notable changes to this project will be documented in this file.

## [v0.5.7] - 2026-03-03

### Added

- **WASI 经脉打通**：`core/wasm_runner.py` 支持 stdin/stdout 协议
  - `run_plugin_wasi(wasm_path, stdin_str)`：WASI 模式执行 Python (py2wasm) 插件
  - `run_plugin(..., stdin_json=...)`：传入 stdin_json 时自动走 WASI 模式
- **战役 3：JPP Python SDK**（jachin-plugin-sdk-python）
  - `@jachin_plugin` 装饰器、stdin/stdout JSON 协议
  - 示例：fetch_crypto_price（加密货币价格）
  - plugin.json：royalty_fee、schema（input/output）
  - py2wasm 编译、Makefile、build.ps1

### Changed

- **文档更新**：LAYER2_AGENT_LOOP、JMP_SPEC、REVENUE、ECOSYSTEM、BATTLE_PLAN、NEXUS_DAEMON、PLUGIN_SECURITY_SANDBOX、core/README、TECHNICAL_SPECIFICATIONS 同步 JPP Python SDK、WASI、memory.db、Mock 工具等

### Removed

- **core/MVP_CHECKLIST.md**：过时（引用不存在的 backend/ 路径）

---

## [v0.5.6] - 2026-02-28

### Added

- **生物学记忆管线 (Biological Memory Pipeline)**
  - `core/biological_memory.py`：海马体 (short_term_logs) + 大脑皮层 (core_memory)，SQLite 极简存储
  - `core/dreamer.py`：梦境引擎，凌晨 3 点对短期日志执行 LLM 提纯，遗忘无用内容
  - Agent Loop 集成：每次交互写入短期记忆，System Prompt 注入核心记忆
  - Daemon 调度：dream_scheduler_loop 与心跳并行，每日 3:00 触发梦境
- **进化战役三：JPP 开发者脚手架**
  - `jachin-plugin-sdk/`：Rust 模板，plugin.json、Makefile、标准 ABI
  - 示例：智能灯泡、数据清洗
  - README：3 步入门、分润说明、煽动式文案

---

## [v0.5.5] - 2026-02-28

### Added

- **进化战役二：IM 网关（Telegram / 飞书）**
  - 数据库：`edge_agents.im_binding_id`、`im_platform`，`agent_message_queue` 表
  - Webhook：`POST /api/v1/webhooks/telegram` 接收 Telegram 消息，插入队列
  - 心跳扩展：返回 `task`、`pending_message_ids` 供边缘 Agent 拉取
  - 结果 API：`POST /api/v1/agents/result` 接收执行结果，推回用户手机
  - 绑定 API：`POST /api/v1/agents/bind-im` 将 Agent 与 Telegram chat_id 绑定
  - Layer 2 daemon：消费 task，执行后调用 result API
- **文档**：`docs/IM_GATEWAY_SPEC.md`

---

## [v0.5.4] - 2026-02-28

### Added

- **进化战役一：Agent Loop 与自主执行**
  - `core/agent_memory.py`：持久化记忆（add_memory, get_context），SQLite/JSON 存储
  - `core/agent_loop.py`：ReAct 代理循环（Thought → Action → Observation），LLM + Wasm 技能
  - 蓝图重定义：Persona & Skillset，Processor 节点 = Wasm 技能武器，由 Agent 按需调用
- **文档**：`docs/LAYER2_AGENT_LOOP_DESIGN.md` 完整架构说明

### Changed

- `core/daemon.py`：心跳收到蓝图后，喂给 AgentLoop.run() 自主执行，不再机械执行 Trigger→Processor→Action
- 心跳 API 支持扩展 `task`/`message` 字段，作为 Agent 用户输入
- 文档更新：plugins/README、scripts/README、NEXUS_DAEMON、LAYER1_ARCHITECTURE、docs/README

---

## [v0.2.0] - 2026-02-12

### Added

- **控制台 HUD API**：思维流日志、建议、记忆搜索、模型列表与切换
- **配置 API**：`/api/v3/config` 供 Horizon 显示环境与模型
- **技能权限字段**：manifest 中 `permissions` 支持 LiveTile 悬停展示
- **Dapr 部署适配**：`start.ps1` 支持 placement/scheduler 地址配置，适配本地/云/多级部署

### Changed

- ConsoleLayout：Void 节点数由记忆数驱动，Horizon 从后端 config 获取 environment/model
- DAPR_GUIDE：新增 Placement 与 Scheduler 地址配置文档

### Fixed

- Dapr scheduler 连接超时：显式指定 `localhost:6060` 避免 mDNS 返回容器内网 IP

---

## [v3.2] - 2026-02-03

详见 [docs/whitepaper_v3.2_final.md](docs/whitepaper_v3.2_final.md)
