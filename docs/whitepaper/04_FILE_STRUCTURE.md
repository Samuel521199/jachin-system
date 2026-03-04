# 04 — 文件结构 (The Purged Structure)

**文档类型**: 白皮书 · 文件结构  
**版本**: v8.0 (The Singularity OS)

---

## ⚠️ 架构师宣告 (The Great Purge)

在 v8.0 中，Jachin Nexus 采用**双轨制引擎 + 量子记忆 + 全息感知 + Dream Weaver**。
**严禁再次引入**：`core/dapr/`、`core/ray_cluster/`、`core/memory/schema/`、臃肿部署脚本。

**文件结构变更规范**：新增 `core/` 模块或 `~/.jachin/` 数据文件时，必须同步更新本文档、`.cursor/rules/000-structure.mdc` 及 `060-v8-singularity.mdc`（若涉及 v8.0 特性），避免乱写。

---

## 一、 顶层星图 (Top-Level Directory)

```text
jachin-system/
├── cloud/                    # [Layer 1] 云端大盘 (Next.js + Supabase)
├── core/                     # [Layer 2] 神经中枢总线 (双轨制 + 量子记忆)
├── clients/                  # [Layer 3] 全息感知外壳 (Tauri + Voice Wake + jachin-cli)
├── jachin-plugin-sdk-python/ # [JPP] 轨道 C Wasm 插件脚手架
├── skills_repo/              # 轨道 B SKILL.md + 轨道 C Wasm 插件
├── scripts/                  # 极简启动脚本
│   └── mock_worker.py        # v8.0 Edge Mesh 工蜂测试（连接 8080，声明 worker_video_encode，接单回传）
├── docs/                     # v8.0 核心白皮书
└── .cursor/rules/            # Cursor AI 规则 (v8.0)
```

---

## 二、 Layer 1: 云端大盘 (cloud/nexus/)

```text
cloud/nexus/
├── src/app/
│   ├── console/              # Jachin ID 控制台
│   ├── fleet/                # 舰队指挥大屏
│   ├── forge/                # 造物厂 (React Flow 蓝图编排)
│   ├── market/               # 神经元商城
│   ├── pair/                 # 扫码配对承接页
│   └── api/v1/
│       ├── agents/heartbeat/ # 边缘心跳与指令下发
│       ├── agents/callback/  # 执行结果回传
│       ├── fleet/deploy/     # 舰队批量下发
│       └── webhooks/         # Universal Message Adapter (telegram, discord, slack, ...)
├── .env.local
└── package.json
```

---

## 三、 Layer 2: 神经中枢总线 (core/)

```text
core/
├── daemon.py                 # 守护进程主循环 (心跳 + dream_scheduler + Layer 3 WS)
├── cron_thinker.py           # 生物钟：每 30min 主动环顾
├── agent_loop.py             # ReAct 循环 (v8.0 Nexus Hook Pipeline)
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

## 四、 Layer 3: 全息感知外壳 (clients/desktop/)

```text
clients/desktop/
├── src/
│   ├── components/
│   │   ├── PairingScreen.tsx # 扫码即连
│   │   └── ConsoleApp.tsx   # 控制面板
│   └── main.tsx
├── src-tauri/
│   ├── src/
│   │   ├── commands/
│   │   │   ├── pairing.rs
│   │   │   └── daemon.rs
│   │   └── stt/              # Voice Wake: Porcupine/Snowboy, VAD, Whisper
│   └── tauri.conf.json
└── package.json
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
│   ├── pair                  # 配对授权
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
    "cloud_model": "qwen-max"
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
| `llm_keys.dashscope` | 阿里云 DashScope API Key（瀑布流第 2 优先级） |
| `llm_keys.openai` | OpenAI API Key（可选） |
| `swarm.heavy_tools` | v8.0 Edge Mesh 重载工具列表，需外包至虫群节点 |

Layer 3 设置界面中的 **"Local AI Mode"** 开关 → `embedding_mode: "local"`。  
**大小脑模式** 下拉菜单 → `llm.cognitive_mode`。
