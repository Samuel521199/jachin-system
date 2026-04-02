# Jachin Nexus 快速开始

**版本**: V2 (2026-03) | **架构**: [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 一、3 分钟启动

### 1. 启动控制台

```powershell
.\start.bat
# 或
.\scripts\start-layer2.ps1
```

### 2. 网关配对（V2 L3）

1. 启动 L2：`python -m core.main` 或 `uvicorn core.main:app --host 0.0.0.0 --port 18888`
2. 打开桌面端：`cd clients\desktop && npm run tauri:dev`
3. 在 GatewayConnectScreen 输入 L2 地址（如 `http://localhost:18888`），点击「发起神经接驳」
4. L2 管理员审批：`POST /api/v2/admin/nodes/assign` 将节点分配给子账号
5. 或点击「使用本地 Key (跳过 L2)」直接使用 OPENAI_API_KEY

### 3. 唤醒 Telegram（可选）

在手机上对 Telegram 机器人发消息，内网边缘算力即刻响应。

---

## 二、环境要求

- Python 3.10+
- Node.js 18+（桌面端）
- 环境变量：`DASHSCOPE_API_KEY`（优先）或 `QWEN_API_KEY`；`~/.jachin/nexus_config.json` 中 `llm_keys.dashscope`；LLM 默认 Ollama `localhost:11434`

---

## 三、核心脚本

| 脚本 | 说明 |
|------|------|
| `start.bat` | 一键启动 |
| `scripts/start-layer2.ps1` | 仅启动 Layer 2 |
| `scripts/start-layer3.ps1` | 仅启动 Layer 3 |

---

## 四、下一步

- 架构：[ARCHITECTURE.md](./ARCHITECTURE.md) | [docs/](./README.md)
- 配对：**L2↔L3** 见 [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md)；L1↔L2 见 [L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md)；边界见 [ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md](./ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md)（`/gateway`：owner/admin 或本地 `admin`；无头 `python -m core.cli pair`）
- IM 网关：[IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md)
