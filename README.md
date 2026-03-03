# 🌌 Jachin Nexus: The Enterprise-Grade Edge AI OS

> 如果说 OpenClaw 是黑客的超级单兵武器，那么 **Jachin Nexus 就是企业的航母战斗群**。

Jachin Nexus 是一个支持极速部署、具备**物理级算力熔断机制**的分布式边缘 AI 底座。我们提供极简的「扫码即连」体验，并允许你通过云端舰队大盘，对全球成千上万个物理节点进行 AI 算法的热更新与分发。

[![Version](https://img.shields.io/badge/version-0.5.5-blue.svg)](https://github.com/Samuel521199/jachin-system)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## ✨ 核心杀器 (Why Jachin?)

| 杀器 | 说明 |
|------|------|
| **🧠 拥有直觉的数字生命** | ReAct Agent Loop：告别死板流水线。内置持久化记忆与自主代理循环，赋予边缘智能体「思考-行动-观察」的自由意志。 |
| **🛡️ 零信任物理沙箱** | The Abyss Wasm Sandbox：第三方技能一律编译为 WebAssembly。燃料熔断 (Fuel Limit) + 完全内存隔离。插件崩溃或恶意越界，宿主毫发无损。支持 Pure Compute 与 WASI stdin/stdout（Python py2wasm）。 |
| **📱 跨越物理网关** | Anywhere IM Trigger：内置 Telegram / Webhook 隧道。掏出手机发一句，内网深处的边缘算力瞬间为你轰鸣。 |
| **🌍 零摩擦部署** | 桌面端 Tauri 扫码授权后，底层引擎静默唤醒。没有丑陋的黑框框。 |

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

**版本**: v0.5.5 | **最后更新**: 2026-03
