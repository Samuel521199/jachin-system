# 04 — 文件结构 (The Purged Structure)

**文档类型**: 白皮书 · 文件结构  
**版本**: v6.0 (The Neural Bus Edition)

---

## ⚠️ 架构师宣告 (The Great Purge)

在 v6.0 中，Jachin Nexus 采用**双轨制引擎 + 量子记忆 + 全息感知**。
**严禁再次引入**：`core/dapr/`、`core/ray_cluster/`、`core/memory/schema/`、臃肿部署脚本。

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
├── docs/                     # v6.0 核心白皮书
└── .cursor/rules/            # Cursor AI 规则 (v6.0)
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
├── daemon.py                 # 守护进程主循环 (心跳 + cron_thinker 调度)
├── cron_thinker.py           # 生物钟：每 30min 主动环顾
├── agent_loop.py             # ReAct 循环 (双轨制路由)
├── mcp_client.py             # 轨道 A：MCP 宿主
├── skill_loader.py           # 轨道 B：SKILL.md 热加载
├── wasm_runner.py            # 轨道 C：The Abyss Wasm 沙箱
├── biological_memory.py      # 海马体 + 大脑皮层
├── vector_store.py           # 量子记忆：Vector SQLite (sqlite-vss/lancedb)
├── dreamer.py                # 梦境引擎 (自我修复、bug_fix 规则)
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

## 五、 jachin-cli (极客终端)

```text
clients/cli/                  # 或 core/cli.py
├── jachin-cli
│   ├── pair                  # 配对授权
│   └── shell                 # 终端流光溢彩，ReAct 日志流
```

---

## 六、 skills_repo/ (技能库)

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

## 七、 用户配置 (~/.jachin/)

```text
~/.jachin/
├── nexus_config.json         # 配对凭证 (L3 或 jachin-cli pair 写入)
├── mcp_servers.json          # MCP 服务器配置 (可选)
├── memory.db                 # SQLite + 向量扩展
└── HEARTBEAT.md              # cron_thinker 检查清单 (可选)
```
