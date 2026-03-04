# 🌌 Jachin Nexus: The Enterprise-Grade Edge AI OS

> 如果说 OpenClaw 是黑客的超级单兵武器，那么 **Jachin Nexus 就是企业的航母战斗群**。

Jachin Nexus **v8.0 (The Singularity OS)** 是支持极速部署的分布式数字生命操作系统，具备**双轨制引擎 + 量子记忆 + 全息感知 + Session Multiplexing + Nexus Hook Pipeline + Dream Weaver + Edge Mesh Swarm + 全链路 runId 追踪 + 流式神经**。我们提供极简的「扫码即连」体验、**Hey Jachin** 语音唤醒、**jachin-cli** 极客终端，并允许你通过云端舰队大盘，对全球成千上万个物理节点进行 AI 算法的热更新与分发。

[![Version](https://img.shields.io/badge/version-8.0-blue.svg)](https://github.com/Samuel521199/jachin-system)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## ✨ 核心杀器 (Why Jachin?)

| 杀器 | 说明 |
|------|------|
| **🧠 双轨制引擎** | 轨道 A (MCP) 开箱即用、轨道 B (SKILL.md) 热加载、轨道 C (Wasm) 零信任沙箱。打破「万物皆 Wasm」的傲慢设定。 |
| **🛡️ 量子记忆** | 生物学梦境 + Vector SQLite + 自我修复。工具报错时自动重试，梦境阶段生成 bug_fix 规则。 |
| **⏰ 生物钟主动** | cron_thinker 每 30 分钟主动环顾，扫描日志、未读邮件，异常时 IM 推送报警。 |
| **📱 全息感知** | Universal Message Adapter 全渠道；Voice Wake (Hey Jachin)；jachin-cli pair/shell。 |
| **🌍 零摩擦部署** | 桌面端 Tauri 扫码授权后，底层引擎静默唤醒。极客可用 jachin-cli。 |

---

## 🚀 3 分钟接入星图 (Quickstart)

1. **启动控制台**：`.\start.bat`（或 `.\scripts\start.ps1`）
2. **扫码配对**：打开桌面端，用手机扫码，见证底层引擎静默轰鸣
3. **唤醒 Telegram**：在手机上对你的专属机器人发号施令

```powershell
# Windows
.\start.bat

# 或分步启动
.\scripts\start-layer2.ps1   # 选择 [2] Light 启动边缘守护进程
```

详见 [QUICKSTART.md](./QUICKSTART.md)、[TELEGRAM_TUNNEL_SETUP.md](./docs/TELEGRAM_TUNNEL_SETUP.md)。

---

## 📚 文档与生态

| 链接 | 说明 |
|------|------|
| [📖 完整白皮书与文档](./docs/README.md) | 架构、协议、商业化、GTM |
| [⚡ 成为神经元铸造师](./jachin-plugin-sdk-python/README.md) | 编写 Python 插件，5 分钟上架赚钱 |
| [🦀 Rust 插件脚手架](./jachin-plugin-sdk/README.md) | 极轻量 Wasm，KB 级体积 |

---

## 🏗️ 三层架构

```
云端分发 (Cloud) + 蜂巢算力 (Hive) + 灵动终端 (Terminal)
```

- **Tier 1**：Jachin Market — 全球技能商店、舰队大盘、IM Webhook
- **Tier 2**：Jachin Hive — AI 推理、持久化记忆、Wasm 沙箱、Agent Loop
- **Tier 3**：Jachin Terminal — 桌面精灵 (Tauri)、手机 App、IoT 节点

---

## 📁 项目结构

```
jachin-system/
├── cloud/                  # [Tier 1] 云端控制台 (Next.js)
├── core/                   # [Tier 2] 核心蜂巢（Agent Loop、Wasm 沙箱、记忆）
├── clients/                # [Tier 3] 桌面精灵、移动端
├── jachin-plugin-sdk/      # JPP Rust 脚手架
├── jachin-plugin-sdk-python/ # JPP Python SDK（@jachin_plugin + py2wasm）
└── docs/                   # 完整文档
```

---

## 🤝 贡献与安全

- [CONTRIBUTING.md](./CONTRIBUTING.md) — 代码提交规范
- [SECURITY.md](./SECURITY.md) — 安全策略与漏洞报告

---

**版本**: v8.0 (The Singularity OS) | **最后更新**: 2026-02
