# 04 — 文件结构 (The Purged Structure)

**文档类型**: 白皮书 · 文件结构  
**版本**: V2  
**基准**: [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md)

---

## ⚠️ 架构师宣告 (The Great Purge)

**V2**：L2 控制面、L3 单体执行。**严禁再次引入**：`core/dapr/`、`core/ray_cluster/`、`core/memory/schema/`、臃肿部署脚本。

**文件结构变更规范**：新增 `core/` 模块或 `~/.jachin/` 数据文件时，必须同步更新本文档及 `.cursor/rules/070-layer1-platform.mdc`（若涉及 Layer 1 多租户），避免乱写。

---

## 一、 顶层星图 (Top-Level Directory)

```text
jachin-system/
├── cloud/                    # [Layer 1] 平台 (Next.js + Drizzle ORM + Auth.js)
├── core/                     # [Layer 2] 控制面 (子账号、权限、记忆、L3 调度；不代理推理)
├── clients/                  # [Layer 3] 终端 (Tauri + Voice Wake + jachin-cli)
├── l3_node/                  # [Layer 3] 单体执行引擎 (Agent + Skill + 直连 LLM)
├── jachin-plugin-sdk-python/ # [JPP] 轨道 C Wasm 插件脚手架
├── skills_repo/              # 轨道 B SKILL.md + 轨道 C Wasm 插件
├── scripts/                  # 极简启动脚本
├── docs/                     # 核心白皮书
└── .cursor/rules/            # Cursor AI 规则
```

---

## 二、 Layer 1: 云端大盘 (cloud/nexus/)

```text
cloud/nexus/
├── src/
│   ├── db/                    # v8.0+ 去 BaaS 化：Drizzle ORM 数据层
│   │   ├── index.ts           # 数据库连接实例 (postgres.js)
│   │   └── schema.ts          # Auth.js + 多租户 + 舰队资产 Schema
│   ├── app/
│   ├── console/              # Jachin ID 控制台
│   ├── fleet/                # 舰队指挥大屏
│   ├── forge/                # 造物厂 (React Flow 蓝图编排)
│   ├── market/               # 神经元商城
│   ├── pair/                 # L1 6 位码配对承接页（Legacy，仅 Layer 2 daemon 使用）
│   └── api/v1/
│       ├── agents/heartbeat/ # 边缘心跳与指令下发
│       ├── agents/callback/  # 执行结果回传
│       ├── fleet/deploy/     # 舰队批量下发
│       └── webhooks/         # Universal Message Adapter (telegram, discord, slack, ...)
├── drizzle.config.ts         # Drizzle Kit 配置 (schema: src/db/schema.ts, out: drizzle/)
├── .env.local
└── package.json
```

---

## 三、 Layer 2: 控制面 (core/)

```text
core/
├── daemon.py                 # 守护进程主循环 (心跳 + dream_scheduler + Layer 3 WS)
├── cron_thinker.py           # 生物钟：每 30min 主动环顾
├── agent_loop.py             # ReAct 循环 (v8.0 Nexus Hook Pipeline)
├── compaction_hook.py        # v8.0 神盾：Token 超载时时空折叠
├── personas.py               # v8.0 Cognitive Swarm：Persona 注册表（Handoff 接力）
├── event_bus.py              # 全息感官总线 (Session Multiplexing)
├── session_manager.py        # v8.0 会话隔离器 (session_id → Actor)
├── hooks_pipeline.py         # v8.0 洋葱中间件 (pre_intent/pre_llm/post_tool/pre_response)
├── mcp_client.py             # 轨道 A：MCP 宿主
├── skill_loader.py           # 轨道 B：SKILL.md 热加载
├── wasm_runner.py            # 轨道 C：The Abyss Wasm 沙箱
├── biological_memory.py      # 海马体 + 大脑皮层 (short_term + core_memory)
├── memory_store.py           # v8.0 LanceDB 记忆碎片 (is_consolidated, Dream Weaver 数据层)
├── vector_router.py          # 全域向量路由 (Semantic Router, skills 表)
├── embedding/                # 可插拔向量引擎 (Cloud/Edge 双核)
│   └── __init__.py
├── dreamer.py                # 梦境引擎 (short_term → core_memory 提纯)
├── dream_weaver.py           # v8.0 Dream Weaver (LanceDB 记忆聚类/去重/融合)
├── swarm_registry.py         # v8.0 Edge Mesh 虫群任务注册表 (register/claim/resolve/await)
├── swarm_hook.py             # v8.0 虫群 Hook (HOOK_BEFORE_TOOL_EXEC 拦截 heavy_tools)
├── hitl_registry.py          # HITL 授权挂起 (core:shell_exec 等人机确认)
├── agent_memory.py           # Agent 对话上下文 (add_memory, get_context)
├── llm_provider.py           # LiteLLM 认知引擎 (CognitiveEngineFactory)
├── native_tools.py           # Native Core (core:fs_read, core:shell_exec)
├── config/
└── requirements.txt
```

---

## 四、 Layer 3: 单体执行节点 (clients/desktop + l3_node/)

```text
clients/desktop/
├── src/
│   ├── components/
│   │   ├── GatewayConnectScreen.tsx # V2 L2 网关神经接驳
│   │   └── ConsoleApp.tsx   # 控制面板
│   └── main.tsx
├── src-tauri/
│   ├── src/
│   │   ├── commands/
│   │   │   ├── pairing.rs    # V2 网关配对（read_l2_gateway_url, gateway_connect, is_l3_engine_ready）
│   │   │   └── daemon.rs
│   │   └── stt/              # Voice Wake: Porcupine/Snowboy, VAD, Whisper
│   └── tauri.conf.json
└── package.json
```

```text
l3_node/                       # V2 单体执行引擎
├── llm_client.py             # SecurityContext + LiteLLMEngine 直连
├── agent_core.py              # ReAct Agent + MemorySyncDaemon
├── bootstrap.py               # 引导：注册、拉 Key
├── crypto.py                  # RSA 加解密
├── ws_server.py               # 本地 WebSocket (127.0.0.1:18881)
└── engine/hooks_pipeline.py   # 洋葱中间件
```

---

## 五、 scripts/ (启动与测试脚本)

```text
scripts/
└── mock_worker.py            # v8.0 Edge Mesh 工蜂：连接 ws://localhost:8080/sensory，
                              # 声明 worker_video_encode，接单后模拟 10s 回传 TASK_RESULT
```

**实弹演习**：先启动 `python -m core.daemon`，再运行 `python scripts/mock_worker.py`，通过 CLI 输入「压缩视频」触发 Swarm 流程。

---

## 六、 jachin-cli (极客终端)

```text
clients/cli/                  # 或 core/cli.py
├── jachin-cli
│   ├── pair                  # L1 配对授权（Legacy，Layer 2 daemon）
│   └── shell                 # 终端流光溢彩，ReAct 日志流
```

---

## 七、 skills_repo/ (技能库)

```text
skills_repo/
├── github-pr-reviewer/
│   └── SKILL.md              # 轨道 B 声明式技能
├── email-briefing/
│   └── SKILL.md
├── crypto-oracle/
│   ├── plugin.wasm           # 轨道 C JPP 插件
│   └── plugin.json
└── ...
```

---

## 八、 用户配置 (~/.jachin/)

```text
~/.jachin/
├── nexus_config.json         # 配对凭证 (L3 或 jachin-cli pair 写入)
├── mcp_servers.json          # MCP 服务器配置 (可选)
├── memory.db                 # SQLite 生物学记忆 (short_term_logs + core_memory)
├── event_queue.db            # v8.0 感官总线任务队列 (SQLite)
├── vector_db/                # LanceDB 向量库
│   ├── skills                # Semantic Router 技能表
│   └── memories              # v8.0 Dream Weaver 记忆表 (id, text, vector, is_consolidated, created_at)
└── HEARTBEAT.md              # cron_thinker 检查清单 (可选)
```

**nexus_config.json 示例（含可插拔向量引擎 + 认知引擎）：**

```json
{
  "access_token": "...",
  "layer1_url": "https://...",
  "embedding": {
    "embedding_mode": "cloud"
  },
  "llm": {
    "cognitive_mode": "dual",
    "edge_model": "qwen2.5:0.5b",
    "cloud_model": "qwen-max",
    "max_attempts": 2,
    "fallback_models": ["ollama/qwen2.5"],
    "timeout_seconds": 60,
    "compaction_threshold": 6000,
    "compaction_model": "ollama/qwen2.5"
  },
  "llm_keys": {
    "dashscope": "sk-xxx",
    "openai": "sk-xxx"
  },
  "swarm": {
    "heavy_tools": ["video_encode", "ffmpeg_encode", "heavy_render"]
  }
}
```

| 字段 | 说明 |
|------|------|
| `embedding.embedding_mode` | `"cloud"` = ☁️ OpenAI API（默认）；`"local"` / `"edge"` = 🛡️ 本地 ONNX 断网可用 |
| `llm.cognitive_mode` | `"dual"` = 大小脑动态路由；`"edge"` = 仅小脑；`"cloud"` = 仅大脑 |
| `llm.max_attempts` | 神盾：LLM 调用重试次数（默认 2） |
| `llm.fallback_models` | 神盾：降级模型列表，主模型失败时依次尝试 |
| `llm.timeout_seconds` | 神盾：单次 LLM 调用超时（秒） |
| `llm.compaction_threshold` | 神盾：超此 token 数触发时空折叠（默认 6000） |
| `llm.compaction_model` | 神盾：摘要生成用模型（默认 ollama/qwen2.5） |
| `llm_keys.dashscope` | 阿里云 DashScope API Key（瀑布流第 2 优先级） |
| `llm_keys.openai` | OpenAI API Key（可选） |
| `swarm.heavy_tools` | v8.0 Edge Mesh 重载工具列表，需外包至虫群节点 |

Layer 3 设置界面中的 **"Local AI Mode"** 开关 → `embedding_mode: "local"`。  
**大小脑模式** 下拉菜单 → `llm.cognitive_mode`。
