# P0 战役：信任链与心跳 — 战术规格

**版本**: v8.0 (The Singularity OS)  
**状态**: 设计规范  
**定位**: 补齐 P0 核心闭环；v8.0 生物钟 cron_thinker  
**推荐顺序**: 方向一（信任链）→ 方向二（心跳）→ 方向三（生物钟）

---

## 背景

混合沙箱（WASM + UDS）的装载逻辑已清晰，但 PluginManager 第一步：

```
jmp_package = extract_and_verify_signature(downloaded_file, public_key)
```

**没有签名验证，沙箱再安全也是形同虚设** — 恶意开发者可在网络传输中途替换插件代码。

**UX 原则**：所有密码学对用户**无感**。V2 L3 使用 L2 网关零信任配对（RSA 双盲），管理员审批后密文 Key 自动下发。详见 [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md)、[INVISIBLE_SECURITY_UX.md](./INVISIBLE_SECURITY_UX.md)。

---

# ⚔️ 方向一：攻克信任链 — JMP 打包与防篡改签名机制

**强烈建议先做**，补齐安全闭环。类似 iOS App Store 签名或 Docker Notary，确保分发的「武器」绝对纯洁。

## 1.1 非对称加密选型

| 方案 | 优点 | 缺点 | 推荐 |
|------|------|------|------|
| **Ed25519** | 极快、签名体积小（64 字节）、抗侧信道 | 相对 RSA 生态略新 | ✅ **推荐** |
| **RSA-2048** | 生态成熟、工具链丰富 | 签名大、速度慢 | 备选 |

**结论**：采用 **Ed25519**。

## 1.2 密钥管理体系

| 角色 | 持有 | 用途 |
|------|------|------|
| **Layer 1 (Nexus)** | 私钥 (Private Key) | 给插件盖戳，生成 `signature.sig` |
| **Layer 2 (前线边缘智能体)** | 公钥 (Public Key)，**首次配对时无感下发** | 验真，拒绝未签名或签名无效的 .jmp |

**安全原则**：V2 L3 持本地 RSA 私钥，公钥向 L2 注册；L2 用 L3 公钥加密 Key 下发，仅 L3 私钥可解密。详见 [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md)。

## 1.3 .jmp 物理结构

```
weapon-vad.jmp  (ZIP 归档)
├── manifest.json       # 元数据 + 资源哈希 (SHA-256)
├── signature.sig       # Layer 1 私钥对 manifest 的 Ed25519 签名 (Base64)
└── payload/            # 实际代码与资源
    ├── main.wasm       # 或 main.py（过渡期）
    ├── prompt.txt
    └── assets/
```

**签名对象**：对 `manifest.json` 的**规范化 JSON**（去除空白、键排序）进行签名。manifest 内包含 `payload/` 下各文件的 SHA-256 哈希，形成**内容绑定**。

## 1.4 防降级攻击

| 威胁 | 机制 |
|------|------|
| 恶意替换为有漏洞的旧版本 | manifest 中 `version` 为语义化版本；Layer 2 可配置 `min_acceptable_version` 或由 deploy 指令显式指定期望版本 |
| 重放旧包 | 签名绑定内容哈希，旧包哈希不同则签名无效；若需更强时效性，可在 manifest 中增加 `issued_at` 时间戳，Layer 2 拒绝过期包 |

**manifest 扩展字段**：

```json
{
  "plugin_id": "com.jachin.vad",
  "version": "1.2.0",
  "content_hashes": {
    "payload/main.wasm": "sha256:abc123...",
    "payload/prompt.txt": "sha256:def456..."
  },
  "issued_at": "2026-02-16T12:00:00Z"
}
```

**验证流程**：Layer 2 解包后，先校验 `signature.sig` 对 manifest 的签名 → 再校验 `payload/` 各文件哈希与 `content_hashes` 一致。

## 1.5 实施要点

| 组件 | 位置 | 职责 |
|------|------|------|
| **签名生成** | Layer 1 (Forge 发布 / 商城上架) | 读取 manifest，计算 payload 哈希，Ed25519 签名，写入 `signature.sig` |
| **签名验证** | Layer 2 `core/plugin/validator.py` | 解压 .jmp，验证 signature.sig，校验 content_hashes |
| **公钥配置** | Layer 2 `core/config` 或环境变量 | `NEXUS_PUBLIC_KEY` 或内置默认公钥 |

---

# 📡 方向二：点亮指挥台 — 端云心跳与状态同步

**当前实现**：`edge_agents` 表 + `POST /api/v1/agents/heartbeat`（轻量版 daemon 使用）。  
`layer2_instances` 为旧版，`instances/heartbeat` 仍存在，但 **agents/heartbeat** 为 Nexus 主路径。

## 2.1 心跳协议设计（agents/heartbeat — 已实现）

**频率**：Layer 2 轻量版 daemon 每隔 **10 秒** 发送一次 `POST /api/v1/agents/heartbeat`。

**请求体**：

```json
{
  "instance_id": "dev-layer2-001",
  "core_version": "1.0.0",
  "metrics": {},
  "active_plugins": {}
}
```

**Headers**：`Authorization: Bearer <access_token>`（配对后获得）

**响应**：`200 OK`，可包含：
- `blueprint`：当前分配的蓝图（name, ast_json）
- `task`：IM 网关待下发消息（用户通过 TG/飞书发来的指令）
- `pending_message_ids`：对应队列 ID，供 result API 标记已处理

**Layer 1 侧**：更新 `edge_agents.last_heartbeat`；若有 pending inbound 消息则打包返回。详见 [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md)。

## 2.2 连接保活策略

| 方案 | 优点 | 缺点 | 推荐 |
|------|------|------|------|
| **短链接轮询 (HTTP POST)** | 实现简单、穿透防火墙、无状态 | 有轮询间隔、略耗带宽 | ✅ **P0 首选** |
| **WebSocket 长连接** | 实时、可服务端推送 | 需维护连接、增加 Layer 1 复杂度 | P1 演进 |
| **gRPC 双向流** | 低延迟、可复用 Jachin Link | 需 gRPC 服务端 | P2 可选 |

**结论**：P0 采用 **短链接 HTTP POST**，每 30 秒一次。后续可演进为 WebSocket 或 gRPC 流。

## 2.3 视觉反馈

| 元素 | 行为 |
|------|------|
| **节点状态** | `last_heartbeat` 在 60 秒内 → 绿色在线；超时 → 灰色离线 |
| **数据流动画** | 指令流（绿）：L1 → L2；状态流（蓝）：L2 → L1；数据流（无）强调零上传 |
| **active_plugins** | Console 可展示该实例当前加载的插件列表 |

## 2.4 实施要点

| 组件 | 位置 | 职责 |
|------|------|------|
| **心跳客户端** | Layer 2 `core/daemon.py`（轻量版） | 每 10 秒 POST `/api/v1/agents/heartbeat`，拉取 blueprint + task |
| **心跳 API** | Layer 1 `cloud/nexus/src/app/api/v1/agents/heartbeat/route.ts` | 校验 token，更新 last_heartbeat，返回 blueprint、task、pending_message_ids |
| **Console 前端** | `cloud/nexus/src/app/console/page.tsx` | 根据 last_heartbeat 渲染绿/灰 |
| **结果回传** | Layer 1 `cloud/nexus/src/app/api/v1/agents/result/route.ts` | Agent 执行完成后 POST 结果，推回 TG/飞书 |

---

---

# 📡 方向三：生物钟主动心跳 (cron_thinker) — v8.0

**当前实现**：Layer 2 每 10 秒云端心跳拉取，纯被动。

**v8.0 增强**：增加**脱离云端的独立 cron_thinker 异步线程**，每 30 分钟主动环顾。

## 3.1 设计目标

- 扫描系统日志、读取未读邮件、发现异常时主动通过 IM 推送报警。
- 可配置 `HEARTBEAT.md` 式任务清单，Agent 按清单检查。
- 与 10s 云端心跳并行，互为补充。

## 3.2 实现要点

| 组件 | 位置 | 职责 |
|------|------|------|
| **cron_thinker** | Layer 2 `core/cron_thinker.py` | 每 30min 触发，读取 HEARTBEAT.md，调用 MCP/SKILL 工具扫描 |
| **HEARTBEAT.md** | `~/.jachin/HEARTBEAT.md` | 用户可编辑的检查清单 |
| **主动推送** | 通过 Layer 1 IM Callback | 发现异常时 POST 结果至用户绑定 IM |

## 3.3 配置示例

```yaml
# ~/.jachin/cron_thinker_config.yaml (可选)
interval_minutes: 30
checks:
  - type: log_scan
    path: /var/log/syslog
    pattern: "ERROR|CRITICAL"
  - type: email_unread
    mcp_tool: imap_unread
```

---

## 实施顺序建议

```
1. 方向一：JMP 签名 (P0-1)
   → 定义 manifest 扩展 (content_hashes, issued_at)
   → Layer 1 签名生成管线
   → Layer 2 validator 验签逻辑

2. 方向二：心跳 (P0-4) ✅ 已实现
   → Layer 1 POST /api/v1/agents/heartbeat
   → Layer 2 core/daemon.py 心跳循环
   → Console 绿/灰状态展示
   → IM 网关扩展：task、pending_message_ids、result API
```

---

**相关文档**:
- [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) - **V2 L3-L2 零信任配对**（RSA 双盲、auth/poll 轮询；Legacy L1 6 位码）
- [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md) - **IM 网关**（TG/飞书 Webhook、消息队列、心跳扩展 task、result API）
- [NEXUS_DAEMON.md](./NEXUS_DAEMON.md) - 守护进程总览（轻量版 daemon 心跳 + Agent Loop）
- [INVISIBLE_SECURITY_UX.md](./INVISIBLE_SECURITY_UX.md) - **无感安全与渐进式授权**（傻瓜式配对、权限大白话、云端无感打包）
- [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md) - 沙箱装载流程
- [MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md](./MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md) - P0 战役总览
- [JMP_SPEC.md](./JMP_SPEC.md) - 协议规范
