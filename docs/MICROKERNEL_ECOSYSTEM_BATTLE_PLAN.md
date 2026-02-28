# Jachin 微内核生态 — 战术战役规划

**版本**: 1.0  
**创建日期**: 2026-02-16  
**定位**: 从图纸到可跑通系统的实施路线图  
**逻辑**: 跑通核心闭环 → 完善业务生态 → 极致视觉与去中心化

---

## 战役分级说明

| 优先级 | 定位 | 目标 |
|--------|------|------|
| **P0** | 生死线 | 核心闭环与底层安全 |
| **P1** | 护城河 | 业务成型与体验贯通 |
| **P2** | 星辰大海 | 极致视觉与去中心化 |

---

## 🚨 P0 级：生死线（核心闭环与底层安全）

**目标**：让 Layer 1 的指令能够安全、准确地在 Layer 2 上执行，完成端云握手。

| 任务模块 | 具体内容 | 核心技术点 | 为什么是 P0 |
|----------|----------|------------|--------------|
| **1. JMP 封装与签名机制** | Layer 1 端：将插件代码/配置打包为 .jmp，并生成哈希签名（如 SHA-256 / Ed25519）。 | 密码学签名验证、压缩算法 | 如果没有防篡改机制，下发的插件就是定时炸弹，生态毫无信任可言。 |
| **2. Layer 2 PluginManager** | Layer 2 端：解析 .jmp 协议，下载资源，校验签名。 | 文件 I/O、Hash 校验逻辑 | 负责接收云端武器的「前线装卸工」。 |
| **3. Layer 2 沙箱与热插拔** | Layer 2 端：**混合动力沙箱** — WASM+WASI（轻量）+ UDS/gRPC 独立进程（重型）。详见 [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md)。 | 进程间通信 (IPC)、沙箱隔离、动态路由代理 | 核心难点。必须保证烂插件弄不死微内核。 |
| **4. 端云心跳与状态同步** | Layer 2 定时向 Layer 1 API 发送存活状态、资源消耗以及当前激活的插件列表（同步至 layer2_instances 表）。 | 轮询机制 / WebSocket 长连接 | 没有心跳，Console 指挥台的大盘就是盲的。 |

---

## P1 级：护城河（业务成型与体验贯通）

**目标**：让系统具备真正的多租户能力和可交付的业务流。

| 任务模块 | 具体内容 | 核心技术点 | 为什么是 P1 |
|----------|----------|------------|--------------|
| **1. Auth Center (鉴权中心)** | 实现 Jachin ID 登录，严格区分 Super Admin (系统主宰)、Developer (开发者)、Consumer (终端客户)。 | Supabase Auth、JWT 令牌、RBAC 权限中间件 | 商业化的基础，决定了谁能发布插件，谁能安装插件。 |
| **2. Forge 到 JMP 的编译引擎** | 将 /forge 中 React Flow 画布里的节点连线，自动编译转换为 manifest.json 和结构化的 .jmp 包结构。 | AST (抽象语法树) 解析、JSON Schema 校验 | 让「拖拽造武器」变成现实，打通界面到协议的最后一步。 |
| **3. API 血管实装 (去 Mock 化)** | 完成 deploy 和 poll 接口的真实数据库读写；实装商城的智能分类与环境过滤。 | Next.js Route Handlers、Supabase 查询优化 | 让整个前端 UI 活起来，跑真实数据。 |

---

## P2 级：星辰大海（极致视觉与去中心化）

**目标**：强化技术壁垒，兑现赛博朋克与 Web3 的终极愿景。

| 任务模块 | 具体内容 | 核心技术点 | 为什么是 P2 |
|----------|----------|------------|--------------|
| **1. IPFS 去中心化存储接入** | 将插件的二进制文件、模型权重上传至 IPFS 网络，Layer 1 只保存 CID (Content Identifier)。 | IPFS Node 集成、Pinata 或 Web3.Storage API | 彻底实现「防篡改、永不丢失」的承诺。 |
| **2. 3D 神经元图谱重构** | 将 /market 的 2D SVG 节点图升级为 Three.js 驱动的 3D 悬浮神经元网络。 | React Three Fiber、WebGL 渲染性能优化 | 视觉上的降维打击，极致的赛博朋克体验。 |
| **3. The Agora (广场) 建设** | 搭建 Agent 悬赏墙 (Bounty Board) 和开发者展示专区。 | 社区互动功能开发 | 建立开发者生态的后期玩法。 |
| **4. Passkey / Web3 钱包登录** | 支持指纹、Face ID 或 MetaMask 钱包无密码登录。 | WebAuthn 协议、Ethers.js | 锦上添花的极客体验。 |

---

## 实施顺序建议

```
P0-0 (配对) → P0-1 (JMP 签名) → P0-2 (PluginManager 解析) → P0-3 (沙箱热插拔) → P0-4 (心跳同步)
        ↓
P1-1 (Auth) → P1-2 (Forge 编译) → P1-3 (API 去 Mock)
        ↓
P2-1 (IPFS) → P2-2 (3D 图谱) → P2-3 (Agora) → P2-4 (Passkey/Web3)
```

---

## Layer 1 已完成战役（历史）

以下四战役骨架已完成，为当前系统基础：

| 战役 | 内容 | 位置 |
|------|------|------|
| **战役一：数据字典** | nexus_users、plugins_registry、personas_library、deploy_commands | `cloud/nexus/supabase/migrations/` |
| **战役二：JMP 协议** | manifest.json、main.py、prompt.txt 包结构 | `docs/JMP_SPEC.md` |
| **战役三：API 血管** | GET /plugins、POST /deploy、GET /deploy/poll、POST /forge/publish | `cloud/nexus/src/app/api/` |
| **战役四：端云握手** | UpdaterAgent 轮询、下载 .jmp、热加载 | `core/updater/agent.py` |

**后续 TODO**：Supabase 接入、deploy 闭环、Forge 发布为真实 .jmp、Market 环境透镜。

---

## 与现有实现的映射

| 战役 | 现有状态 | 待补齐 |
|------|----------|--------|
| P0-1 JMP 签名 | `signature.sig` 可选字段已定义 | Ed25519 签名、.jmp 结构、content_hashes、防降级，详见 [P0_TRUST_AND_HEARTBEAT_SPEC](./P0_TRUST_AND_HEARTBEAT_SPEC.md) |
| P0-2 PluginManager | `core/plugin/` 已有加载逻辑 | 增加 .jmp 解析与签名校验 |
| P0-3 沙箱热插拔 | `core/plugin/sandbox.py` 已实现 AST+__builtins__ | 完善冲突检测、优雅卸载 |
| P0-4 心跳 | `layer2_instances` 表已建 | 实装 `POST /api/v1/instances/heartbeat` + Layer 2 客户端 |
| P0-0 配对 | `pairing_sessions` 表已建 | 6 位码端云三次握手、access_token + 公钥下发，详见 [PAIRING_PROTOCOL_SPEC](./PAIRING_PROTOCOL_SPEC.md) |
| P1-1 Auth | `nexus_users` 表已建 | Supabase Auth 集成、RBAC 中间件 |
| P1-2 Forge 编译 | React Flow 画布已实现 | 画布 → manifest.json → .jmp 编译管线 |
| P1-3 API 去 Mock | plugins/deploy 有 fallback | Supabase 真实读写、环境过滤 |
| P2-* | 均为规划 | 按序推进 |

---

**相关文档**:
- [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) - **设备配对协议**（6 位码、端云三次握手）
- [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) - **P0 信任链与心跳战术规格**
- [INVISIBLE_SECURITY_UX.md](./INVISIBLE_SECURITY_UX.md) - **无感安全与渐进式授权**（安全又傻瓜）
- [MICROKERNEL_ECOSYSTEM_UPGRADE.md](./MICROKERNEL_ECOSYSTEM_UPGRADE.md) - 微内核生态深度融合方案
- [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md) - 混合动力沙箱（WASM + UDS）
- [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md) - Layer 1 架构与设计总览
