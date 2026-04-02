# 零摩擦与无感安全 — UX 设计规范

**版本**: v8.0 (The Singularity OS)  
**定位**: 从「极客玩具」到「魔法师与企业家买单」的体验降维打击

---

## 设计哲学

> **任何需要用户手动修改 .json 或敲冗长命令的设计，都是反人性的。**

- **零摩擦**：用户无需理解 API、Token、公钥
- **开箱即用**：通电即完成配置
- **隐形化**：`nexus_config.json` 对用户绝对隐藏，配置由云端推送
- **无感安全**：密码学在水下静默运行，仅篡改时显性提示

---

## 一、Layer 1 云端

### 1.1 免密登录
- Magic Link / GitHub / Google OAuth
- 技术栈：Auth.js

### 1.2 数字孪生大盘
- 设备卡片网格、在线状态、拖拽部署蓝图
- 详见 [whitepaper/05_LAYER1_NEXUS.md](./whitepaper/05_LAYER1_NEXUS.md)

---

## 二、配对协议

| 方案 | 适用 | 操作 |
|------|------|------|
| **A V2 L2 网关零信任** | L3 桌面端 | 输入 L2 地址，发起神经接驳，管理员审批后密文 Key 下发 |
| **B L1↔L2 控制面** | L2 网关 / 无头 daemon | **主**：L2 `/gateway` L1 邮箱+密码 或 Nexus 账号登录 → `nexus_config.json`；**辅**：CLI / 网页 6 位码（见 [L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md)） |
| **C Wi-Fi 热点** | 无屏设备 | Captive Portal 输入 Wi-Fi + 邮箱（规划） |
| **D ZTP** | 企业批量 | 预烧录 enterprise_id，通电即注册（规划） |

**详细协议**：**L2↔L3** 见 [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md)（非 L1↔L3）；L1↔L2 见 [L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md)；总述见 [ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md](./ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md)

---

## 三、权限大白话

部署插件时，将底层权限翻译成用户可理解的描述，一键安全安装。篡改检测时弹出拦截提示。

**信任链**：[P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md)
