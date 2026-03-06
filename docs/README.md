# Jachin Nexus 文档

**版本**: V2 (L3 单体对标 OpenClaw)

---

## 基石：白皮书 (Single Source of Truth)

**[docs/whitepaper/](./whitepaper/)** 为项目架构宪法，共 10 份：

| 序号 | 文档 | 内容 |
|------|------|------|
| 00 | [INDEX](./whitepaper/00_INDEX.md) | 文档索引与架构宪法 |
| 01 | [设计目的](./whitepaper/01_DESIGN_PURPOSE.md) | B2B/B2C 定位 |
| 02 | [框架架构](./whitepaper/02_FRAMEWORK.md) | Layer 1-3、生物学记忆 |
| 03 | [业务流程](./whitepaper/03_WORKFLOW.md) | 扫码、心跳、ReAct |
| 04 | [文件结构](./whitepaper/04_FILE_STRUCTURE.md) | 纯净目录树 |
| 05 | [Layer 1](./whitepaper/05_LAYER1_NEXUS.md) | 平台（用户主账号注册/管理） |
| 06 | [Layer 2](./whitepaper/06_LAYER2_EDGE.md) | 控制面（子账号、权限、记忆、L3 调度） |
| 07 | [Layer 3](./whitepaper/07_LAYER3_TERMINAL.md) | 单体执行节点（对标 OpenClaw） |
| 08 | [JPP 插件](./whitepaper/08_JPP_SDK_AND_SKILLS.md) | 插件协议与生态 |
| 09 | [去 BaaS 化](./whitepaper/09_DE_BAASIFICATION.md) | Drizzle、Auth.js、绝对主权 |
| 10 | [控制面与数据面分离](./whitepaper/10_CONTROL_DATA_PLANE.md) | mDNS、WebRTC P2P、能直连绝不绕路 |

---

## 快速开始

- [QUICKSTART.md](./QUICKSTART.md) — 3 分钟启动

---

## 规范与指南

| 文档 | 说明 |
|------|------|
| [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md) | Telegram/飞书 IM 网关 |
| [TELEGRAM_TUNNEL_SETUP.md](./TELEGRAM_TUNNEL_SETUP.md) | Bot 配置与 Webhook |
| [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) | V2 L3-L2 零信任配对 |
| [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) | 信任链与心跳 |
| [LAYER3_L2_WAN_ARCHITECTURE.md](./LAYER3_L2_WAN_ARCHITECTURE.md) | 智能路由、控制面与数据面分离 |
| [L2_GATEWAY_CLUSTER_ARCHITECTURE.md](./L2_GATEWAY_CLUSTER_ARCHITECTURE.md) | 企业内网 L2/L3 集群 + 统一网关、配对、负载均衡 |
| [ARCHITECTURE_V2_LAYER3_STANDALONE.md](./ARCHITECTURE_V2_LAYER3_STANDALONE.md) | 架构 V2：L3 单体对标 OpenClaw，L2 权限/记忆/调度 |
| [V2_ARCHITECTURE_DIAGRAM.md](./V2_ARCHITECTURE_DIAGRAM.md) | V2 架构图、流程图、L2 无状态集群 |
| [UX_AND_SECURITY_DESIGN.md](./UX_AND_SECURITY_DESIGN.md) | 零摩擦与无感安全 |
| [JMP_SPEC.md](./JMP_SPEC.md) | JMP 2.0 协议 |
| [PLUGIN_SECURITY_SANDBOX.md](./PLUGIN_SECURITY_SANDBOX.md) | 插件沙箱安全 |
| [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md) | WASM/WASI 沙箱 |
| [VOICE_AND_TTS_GUIDE.md](./VOICE_AND_TTS_GUIDE.md) | 语音与 TTS |
| [TESTING_GUIDE.md](./TESTING_GUIDE.md) | 测试指南 |
| [OAUTH_SETUP.md](./OAUTH_SETUP.md) | OAuth 登录配置 |

---

## 商业与生态

| 文档 | 说明 |
|------|------|
| [VISION.md](./VISION.md) | 产品愿景 |
| [ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md](./ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md) | 生态与商业化 |
| [REVENUE_AND_ROYALTY_SPEC.md](./REVENUE_AND_ROYALTY_SPEC.md) | 版税分润 |
| [GTM_STRATEGY.md](./GTM_STRATEGY.md) | 市场策略 |

---

## 技术参考

| 文档 | 说明 |
|------|------|
| [QWEN_ARCHITECTURE.md](./QWEN_ARCHITECTURE.md) | Qwen LLM 架构 |
