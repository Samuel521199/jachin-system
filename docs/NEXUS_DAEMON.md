# Jachin Nexus Daemon - Layer 2 点火总控

纯血 Jachin Nexus 独立生态的系统级守护进程，与上层应用保持物理隔离与架构解耦。

---

## 快速开始：各层独立安装 / 启动

| 层 | 角色 | 安装 | 启动 |
|------|------|------|------|
| **Cloud** | 平台商 | `install-cloud.ps1` / `install-cloud.sh` | `start-cloud.ps1` / `start-cloud.sh` |
| **Layer2** | 用户 | `install-layer2.ps1` / `install-layer2.sh` | `start-layer2.ps1` / `start-layer2.sh` |
| **Layer3** | 用户 | `install-layer3.ps1` / `install-layer3.sh` | `start-layer3.ps1` / `start-layer3.sh` |

- **Cloud**：Nexus Console (Next.js)，`http://localhost:3000`
- **Layer2**：nexus_daemon（完整版）或 **daemon（轻量版）** + Local Ingress，`http://127.0.0.1:9000`
- **Layer3**：Desktop Terminal (Tauri + React)

## Layer 2 两种守护进程

| 模式 | 入口 | 说明 |
|------|------|------|
| **完整版** | `python -m core.nexus_daemon` | Event Bus、Telemetry、Updater、Ingress，适合开发/生产 |
| **轻量版** | `python -m core.daemon` | 心跳 + **Agent Loop**，蓝图作为 Persona & Skillset，ReAct 自主执行 |

轻量版详见 [LAYER2_AGENT_LOOP_DESIGN.md](./LAYER2_AGENT_LOOP_DESIGN.md)。

### 轻量版 daemon 执行流程

1. 读取 `~/.jachin/nexus_config.json`，校验配对状态
2. 每 10 秒向 Layer 1 发送 `POST /api/v1/agents/heartbeat`
3. 若响应含 `blueprint`：将蓝图 AST 与可选 `task`、`pending_message_ids` 喂给 `AgentLoop.run()`
4. 若仅有 `task`（IM 消息）：无新蓝图时仍启动 Agent，用空技能集执行
5. Agent 通过 ReAct 循环（Thought → Action → Observation）自主执行，直至输出 `Answer:`
6. Wasm 技能由 `core/wasm_runner.py` 在沙箱中执行，燃料熔断保护。支持 Pure Compute（run() -> i32）与 WASI（stdin/stdout，Python py2wasm 插件）
7. 若有 `pending_message_ids`：执行完成后 `POST /api/v1/agents/result`，Layer 1 将结果推回用户手机（TG/飞书）

**IM 网关**：用户通过 Telegram/飞书发消息 → Webhook 入队 → 心跳拉取 task → Agent 执行 → result API 回传。详见 [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md)。

---

## 一、点火总控 (nexus_daemon)

### 启动

```bash
python -m core.nexus_daemon
```

### 启动时自动执行

1. **读取本地密钥配置**：`~/.jachin/nexus_config.json` 或环境变量
2. **拉起 Event Bus 消费者**：内部事件分发循环
3. **启动 TelemetryAgent**：向 Layer 1 发送心跳（需已配对）
4. **启动 UpdaterAgent**：轮询部署指令（需已配对）
5. **启动 Local Ingress API**：`http://127.0.0.1:9000`

### 配置

| 来源 | 变量/字段 | 说明 |
|------|-----------|------|
| 环境变量 | `NEXUS_INSTANCE_ID` | 边缘智能体实例 ID（配对后获得） |
| 环境变量 | `NEXUS_ACCESS_TOKEN` | 通信凭证（配对后获得） |
| 环境变量 | `NEXUS_BASE_URL` | Layer 1 基地址，默认 `http://localhost:3000` |
| 环境变量 | `NEXUS_INGRESS_PORT` | Ingress 端口，默认 `9000` |
| 配置文件 | `~/.jachin/nexus_config.json` | 持久化配对结果 |

**未配对时**：仅运行 Event Bus + Ingress，具备本地自治能力。

### 优雅退出

支持 `SIGTERM`、`SIGINT`（Ctrl+C），按序关闭 Telemetry、Updater、Event Bus、Ingress。

---

## 二、Local Ingress API - 边缘中枢本地网关

极轻量级 HTTP 服务，跑在 `localhost:9000`。摄像头、GUI、硬件按钮等异构设备只需 POST JSON，即可唤醒内部 Event Bus 与 Workflow。

### 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/events` | 向 Event Bus 投递事件 |

### POST /api/events

**请求体**：

```json
{
  "type": "audio.input",
  "payload": { "text": "用户说的话" },
  "source_plugin_id": "external-camera"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 事件类型，如 `audio.input`、`cron.trigger` |
| `payload` | 否 | 载荷对象，默认 `{}` |
| `source_plugin_id` | 否 | 来源插件 ID，用于溯源 |

**示例**：

```bash
curl -X POST http://127.0.0.1:9000/api/events \
  -H "Content-Type: application/json" \
  -d '{"type":"audio.input","payload":{"text":"hello"}}'
```

---

## 三、OOBE 配对 (jachin pair)

新机器首次使用需完成配对。执行：

```bash
python jachin pair
```

或（若已安装 CLI）：

```bash
jachin pair
```

**流程**：

1. CLI 向 Layer 1 发起 `POST /api/v1/pairing/request`
2. 终端打印 6 位配对码（如 `X7A9K2`）
3. 用户在浏览器打开 Layer 1 的 `/pair` 页面，输入配对码，点击「授权绑定」
4. CLI 轮询 `GET /api/v1/pairing/status`，获取 `access_token` 与 `instance_id`
5. 自动写入 `~/.jachin/nexus_config.json`
6. 可选：配对成功后自动启动 `nexus_daemon`（使用 `--no-daemon` 跳过）

**参数**：

- `--base-url`：Layer 1 地址，默认 `http://localhost:3000`
- `--no-daemon`：配对后不自动启动 daemon

---

## 四、CLI 命令

| 命令 | 说明 |
|------|------|
| `jachin pair` | OOBE 配对 |
| `jachin daemon` | 启动**轻量版**守护进程（core.daemon，心跳 + Agent Loop） |
| `jachin status` | 查看配对状态 |

> 注：`jachin daemon` 启动的是轻量版（core.daemon），非 nexus_daemon。完整版需直接 `python -m core.nexus_daemon`。

---

## 五、systemd 集成

在 Linux 上安装为系统服务，实现开机自启与崩溃重启：

```bash
sudo ./scripts/install.sh layer2 --systemd
sudo systemctl daemon-reload
sudo systemctl start jachin-nexus
sudo systemctl enable jachin-nexus   # 开机自启
```

**日志**：`journalctl -u jachin-nexus -f`

---

## 六、依赖

- `aiohttp>=3.9.0`：Local Ingress HTTP 服务
- `httpx`：CLI 配对请求
- 其余见 `core/requirements.txt`
