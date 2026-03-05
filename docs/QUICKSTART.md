# Jachin Nexus v8.0 快速开始

**版本**: v8.0 (The Singularity OS) | **最后更新**: 2026-02

---

## 一、3 分钟启动

### 1. 启动控制台

```powershell
.\start.bat
# 或
.\scripts\start-layer2.ps1
```

### 2. 网关配对（V2 L3）

1. 启动 L2：`python core/main.py`（端口 18888）
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

- 架构：[whitepaper/](./whitepaper/)
- 配对：V2 桌面端见 [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md)；Layer 2 daemon 用 `python -m core.cli pair`
- IM 网关：[IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md)
