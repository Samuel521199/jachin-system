# 聊天功能无法使用 - 深度分析

## 现象

- **错误**：`无法连接 L3 服务 ([WinError 1225] 远程计算机拒绝网络连接)，请确认 L3 终端已启动且端口 18981 正确`
- **API Key**：已加载（.env 中 DASHSCOPE_API_KEY 存在）
- **聊天仍不可用**

## 根因分析

### 1. 聊天链路与 API Key 的关系

```
用户发消息 → Lark Webhook → MCP (atom_lark_chat) → L3 WebSocket (ws://127.0.0.1:18981/sensory) → L3 Agent → 调用 LLM (需 API Key)
```

**关键点**：API Key 由 **L3 进程** 加载使用，不是由 Lark/MCP 直接使用。

- **L3 壳模式**（配置了 `L3_WS_URL`）：Lark 仅做转发，所有对话由 L3 执行
- **L3 需要**：① 进程运行 ② WebSocket 监听 18981 ③ API Key 加载

因此：**API Key 加载了 ≠ 聊天能用**。若 L3 未启动或未监听 18981，连接会被拒绝（WinError 1225）。

### 2. WinError 1225 含义

- `ERROR_CONNECTION_REFUSED`：目标端口无进程监听
- 说明：**127.0.0.1:18981 上没有 L3 WebSocket 服务**

### 3. 可能原因

| 原因 | 说明 |
|------|------|
| L3 未启动 | jachin-desktop 启动时 Sidecar 失败（如 os error 2），Python 回退在 dist 下也失败 |
| L3 启动慢 | Gateway 模式需 5–8 秒审批，WebSocket 才就绪；用户过早发消息会失败 |
| L3 已崩溃 | 启动后异常退出，端口释放 |
| 端口冲突 | 18981 被占用，L3 改端口或启动失败 |
| 运行环境不一致 | MCP 在远程，L3 在本机，127.0.0.1 指向 MCP 所在机器 |

### 4. 从 l3_debug.log 的佐证

- **第一次启动**：L3 exe 正常，WebSocket 启动，Gateway 审批通过
- **第二次启动**：Sidecar 报 `系统找不到指定的文件 (os error 2)`，回退 Python 后成功

说明：**L3 启动存在不稳定**，Sidecar 偶发找不到 exe，会导致 L3 未运行，进而聊天失败。

## 排查步骤

1. **确认 L3 是否在跑**
   - 任务管理器查看 `l3_node-*.exe` 或 `python`（l3_node）
   - 或：`netstat -ano | findstr 18981`

2. **确认 WebSocket 已就绪**
   - 查看 `l3_debug.log` 中是否有：`L3 WebSocket 服务已启动 ws://127.0.0.1:18981/sensory`

3. **确认 jachin-desktop 已成功拉起 L3**
   - 查看 `l3_debug.log` 是否有 `[L3] 引擎已启动` 或 Sidecar 失败记录

4. **确认 MCP 与 L3 同机**
   - 若 MCP 在云上，`127.0.0.1` 指云主机，无法连到本机 L3
   - 需用本机 IP 或隧道（如 ngrok）

## 结论

**聊天不可用的直接原因是 L3 未在 18981 端口提供 WebSocket 服务**，与 API Key 是否加载无关。  
需保证 L3 稳定启动并监听 18981，同时修复 Sidecar 偶发失败等问题。

---

## 附：logs/stream 200 0 说明

`GET /api/system/logs/stream` 返回 `200 0` 为 **SSE 流式响应** 的正常现象：
- 连接建立后保持长连接，数据按需推送
- aiohttp 在记录 access log 时，流式响应的 body 长度可能显示为 0
- 服务端已发送欢迎消息，前端应能收到；若仍显示「连接中…」，需排查 CORS 或 EventSource 消费逻辑
