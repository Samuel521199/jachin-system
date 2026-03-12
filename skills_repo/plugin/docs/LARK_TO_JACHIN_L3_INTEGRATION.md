# Lark HR 机器人接入 Jachin L3 终端 - 集成指南

## 一、架构概览

```
Lark 用户消息
    ↓
Lark Webhook (plugin/lark_bot_conversation)
    ↓
atom_lark_chat.process_lark_message
    ↓
┌─────────────────────────────────────────────────────────┐
│  L3_WS_URL 已配置 → 转发到 Jachin L3 WebSocket           │
│  L3_WS_URL 未配置 → 本地百炼直连（独立模式）             │
└─────────────────────────────────────────────────────────┘
    ↓ (L3 模式)
ws://127.0.0.1:18981/sensory
    ↓
L3 run_agent (ReAct + MCP 工具)
    ↓
answer 回传 → Lark 消息 API
```

---

## 二、前置条件

1. **Jachin L3 已启动**，WebSocket 监听 `ws://127.0.0.1:18981/sensory`
2. **L2 已配置 HR MCP**（若走 gateway 模式），或 L3 使用 `--ws-only` 本地运行
3. **plugin 的 Lark Webhook** 已跑通（接收 Lark 消息）

---

## 三、集成步骤

### Step 1：启动 Jachin L3 节点

在 jachin-system 项目目录：

```bash
cd D:\project\jachin-system-main
python -m l3_node --ws-only
```

或使用脚本：

```powershell
.\scripts\run_l3.ps1 --ws-only
```

确认输出中有类似：`L3 WebSocket 127.0.0.1:18981` 或 `18982` 等端口。

### Step 2：配置 Plugin 转发到 L3

在 plugin 的 `.env` 中增加：

```
# Jachin L3 WebSocket 地址（配置后 Lark 消息将转发给 L3，由 L3 调用 MCP 工具）
L3_WS_URL=ws://127.0.0.1:18981/sensory
```

端口需与 L3 实际监听端口一致（18981、18982...）。

### Step 3：确保 HR MCP 在 L3 可用

- 若 L3 使用 `--ws-only`：需在 `~/.jachin/mcp_servers.json` 中配置 `hr-atomic-tools`，且 L3 能拉取到该 MCP 的工具。
- 若 L3 使用 `--gateway`：L2 需已配置并启动 `hr-atomic-tools` MCP。

### Step 4：启动 Lark Webhook

```bash
cd D:\project\jachin-system-main\skills_repo\plugin
python scripts\lark_bot_conversation.py --webhook --port 5000
```

ngrok 暴露后，Lark 用户消息会进入 Webhook，再由 `atom_lark_chat` 转发到 L3。

---

## 四、运行顺序

1. **先启动 L3**：`python -m l3_node --ws-only`（jachin-system-main）
2. **再启动 Webhook**：`python scripts\lark_bot_conversation.py --webhook`（plugin）
3. **可选**：`ngrok http 5000`（若需公网接收 Lark 回调）

---

## 五、L3 WebSocket 协议（供实现参考）

**客户端 → L3：**

```json
{"type": "manifest", "caps": ["stream_chunk"]}
{"intent": "帮我同步多维表"}  或  {"content": "帮我同步多维表"}
```

**L3 → 客户端：**

```json
{"step_type": "chunk", "content": "流式内容", "run_id": "xxx"}
{"step_type": "answer", "content": "最终回复", "run_id": "xxx"}
{"step_type": "error", "content": "错误信息", "run_id": "xxx"}
```

---

## 六、模式切换

| 环境变量 | 行为 |
|----------|------|
| `L3_WS_URL` 未设置 | 使用本地百炼直连，不依赖 jachin-system |
| `L3_WS_URL=ws://127.0.0.1:18981/sensory` | 转发到 L3，由 L3 Agent + MCP 完成对话与任务 |

---

## 七、终端-Lark 镜像模式（终端为主屏，Lark 为副屏）

类似笔记本+显示器：终端为「笔记本」（主控），Lark 为「显示器」（仅展示）。Lark 消息会同步到终端，终端回复会镜像到 Lark。

### 配置步骤

1. **Desktop**：在 `clients/desktop/.env` 中设置 `VITE_LARK_CHAT_ID=oc_xxx`（Lark 群聊或私聊的 chat_id）
2. **L3**：可选设置 `LARK_MIRROR_PUSH_URL=http://127.0.0.1:5000/api/mirror-push`（与 Webhook 同端口，默认即此）
3. **Webhook**：已提供 `/api/mirror-push` 端点，无需额外配置

### 行为

- **Lark 发消息** → 同步到终端显示（带 `[Lark]` 前缀）→ L3 处理 → 回复到 Lark 与终端
- **终端发消息**（带 chat_id 时）→ L3 处理 → 回复到终端 → 自动推送到 Lark

### 协议

终端订阅：`{"type": "subscribe_mirror", "lark_chat_id": "oc_xxx"}`
终端发送（镜像模式）：`{"intent": "...", "chat_id": "oc_xxx", "origin": "terminal"}`

---

## 八、故障排查

- **L3 连接失败**：确认 L3 已启动，端口与 `L3_WS_URL` 一致。若用 Jachin 桌面，L3 由 Tauri 拉起，需确保桌面已打开；或单独运行 `python -m l3_node --ws-only`。
- **仍走百炼**：确认启动时显示 `[L3 壳模式]`，日志有 `L3 连接尝试`、`L3 已连接`。若无，检查 `.env` 中 `L3_WS_URL` 已保存。
- **L3 无 MCP 工具**：检查 `~/.jachin/mcp_servers.json` 与 L3 启动参数。
- **Lark 收不到回复**：查看 Webhook 终端日志，确认有 `Lark 回复已发送 ... ok=True`。
