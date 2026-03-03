# 02 — 框架架构 (The Trinity)

**文档类型**: 白皮书 · 框架架构  
**版本**: v5.0

---

## 一、 三位一体架构 (The Trinity Architecture)

Jachin Nexus 采用严格的“重云轻端”三位一体设计，彻底摒弃了上一代笨重的微服务网格。

```text
Layer 1 (云端调度枢纽) ↔ Layer 2 (本地算力/沙箱引擎) ↔ Layer 3 (零感交互外壳)

1. Layer 1: Jachin Nexus (数字孪生云端)
定位: 免密、可视化、主导资产确权与指令下发的全局指挥大盘。

特性: 绝对不存储边缘节点隐私记忆。它是边缘节点的“DNA 库”与“调度室”。

核心组件: The Forge (图形化编排)、Fleet Management (舰队批量下发)、IM Gateway (跨网通信网关)。

数据底座: 依托 Supabase (Managed PostgreSQL)，负责统管全网设备心跳、AST 蓝图文件、JPP 插件元数据及跨网指令队列。

2. Layer 2: Edge Agent (边缘守护引擎)
定位: 极致轻量、具备自我意识与物理隔离防线的核心执行实体。

特性: 在后台静默运行，无需公网 IP。负责维持心跳、运行 ReAct 思考循环，并在 WASI 沙箱中狂飙算力。

数据底座: 彻底废弃本地 Redis/PostgreSQL。采用极简单文件 SQLite (memory.db)，拔电即走，零安装成本。

技术栈: 纯 Python 3.10+ (asyncio, httpx, openai) + wasmtime (底层沙箱) + sqlite3。

3. Layer 3: Jachin Terminal (灵动终端)
定位: 零摩擦的外壳，负责配对与权限接管，把最硬核的技术完美隐藏。

特性: 启动即展示动态二维码，手机扫码免密授权；授权后，底层 Rust 直接接管 OS 级权限，静默拉起后方的 Layer 2 进程。

技术栈: Tauri v2 + Rust (系统级进程管理 std::process::Command) + React。

二、 核心通信与调度拓扑 (Topology & Routing)
为穿透极其复杂的企业内网与 NAT 防火墙，我们废弃了长连接，采用极致稳定的“心跳拉取”与“跨网桥接”模型。

1. 边缘心跳驱动 (Edge-Polling)
所有指令（蓝图更新、IM 聊天指令）统一暂存在 Layer 1 的 Supabase 队列中。

Layer 2 每 10 秒发起一次 POST /api/v1/agents/heartbeat 从云端拉取 pending_task。网络恢复后自动追齐积压任务。

2. IM 跨网直达 (IM Gateway)
链路：手机端 (Telegram/飞书) ➡️ Layer 1 Webhook ➡️ Supabase 队列 ➡️ Layer 2 心跳获取 ➡️ 本地 Agent Loop 执行 ➡️ Layer 1 Callback ➡️ 手机接收结果。

三、 划时代的记忆与认知架构 (Biological Memory Pipeline)
Jachin Nexus v5.0 不再是机械的流水线，而是拥有“睡觉、遗忘和成长”能力的数字生命体。

1. Agent Loop (ReAct 自主代理循环)
接收自然语言指令后，Agent 依据所拥有的 Wasm 技能进行 [Thought] (思考) -> [Action] (调用 Wasm) -> [Observation] (观察沙箱结果) -> [Final Answer] 的循环。

2. 生物学梦境引擎 (The Dream Sequence)
海马体 (Short-term Cache)：白天所有的高频对话、沙箱日志无损记录在 SQLite short_term_logs 表中，存活周期 24 小时。

梦境压缩 (Dreaming)：每日凌晨 3 点，Layer 2 守护进程自动触发 core/dreamer.py，利用本地/云端 LLM 对短期日志进行“梦境回放”，提取出核心信息并遗忘无用内容。

大脑皮层 (Core Memory)：梦境提纯出的高密度偏好标识（Tag），永久存入 SQLite 的 core_memory 表中，并在每次 Agent 思考前自动拼接到 System Prompt，实现“越用越懂你”的零成本进化。

3. 实现细节 (Implementation)
- 核心模块：core/biological_memory.py（短/长期记忆表）、core/dreamer.py（梦境引擎）
- 数据表：short_term_logs（短）、core_memory（长），均位于 ~/.jachin/memory.db
- 调度：core/daemon.py 中的 dream_scheduler_loop() 与心跳并行运行，每日 3:00 触发
- 集成：agent_loop 在每次交互时写入 short_term，在 _build_system_prompt 中注入 core_memory

四、 物理沙箱与信任隔离 (Zero-Trust Execution)
WASI 物理沙箱 (core/wasm_runner.py)：所有第三方 Plugin 必须编译为 .wasm，支持 stdin/stdout 协议。

燃料熔断机制 (Fuel Limit)：执行插件时注入定量“算力燃料”。发生死循环或恶意占用时，燃料耗尽，Wasm 实例当场物理超度。

五、 废弃清单 (Architectural Purge)
❌ 废弃 Dapr & Ray Cluster：被 Python 原生异步和极简 HTTP 心跳取代。

❌ 废弃本地重量级数据库：Redis 与本地 PGSQL 全面下线，云边切分为 Supabase + SQLite。

❌ 废弃繁杂启动脚本：被 Layer 3 Tauri 的单文件分发与“静默接管”所取代。