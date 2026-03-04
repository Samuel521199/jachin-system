# Jachin Nexus v5.0 快速开始

**版本**: v5.0 | **最后更新**: 2026-03

---

## 一、3 分钟启动

### 1. 启动控制台

```powershell
.\start.bat
# 或
.\scripts\start-layer2.ps1
```

### 2. 扫码配对

1. 打开桌面端：`cd clients\desktop && npm run tauri:dev`
2. 手机扫码，完成授权
3. 底层 Layer 2 守护进程自动唤醒

### 3. 唤醒 Telegram（可选）

在手机上对 Telegram 机器人发消息，内网边缘算力即刻响应。

---

## 二、环境要求

- Python 3.10+
- Node.js 18+（桌面端）
- 环境变量：`QWEN_API_KEY` 或 `LOCAL_LLM_URL`（LLM 可选）

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
- 配对：`python -m core.cli pair`
- IM 网关：[IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md)
