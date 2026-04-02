# 配对协议 (V2 L2↔L3 零信任)

**版本**: 2.2  
**状态**: 与仓库实现一致  
**定位**: **仅 L2 与 L3 之间存在配对**（RSA 双盲、`auth/sync`、`auth/poll`）。**L1 不与 L3 配对**；L1 的 `GET /api/v1/me/workspaces` 等仅供 L3 配置时拉取工作区元数据。总述见 **[ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md](./ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md)**。

**L1↔L2 控制面**（Nexus 登录、Web Bridge、CLI 辅助）见 **[L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md)**。

---

## 一、V2 L2↔L3 零信任配对（主流程）

L3（`l3_node` / 桌面侧车）自行生成 RSA 密钥对，向 **L2** 注册；**管理员在 L2 `/gateway/`** 审批后获取加密的 API Key。

### 阶段 1：L3 向 L2 注册

| 动作 | 说明 |
|------|------|
| **L3** | 检查 `~/.jachin/l3_identity.json`，无则生成 RSA 密钥对并持久化 |
| **L3 发送** | `POST /api/v2/auth/sync`：至少 `device_fingerprint`、`public_key_pem`；**已配对 L2**（`nexus_config` 含 `tenant_id`）时 **必填** `organization_id`（须与 `tenant_id` 一致），可选 `workspace_name`、`display_name`、`node_id` |
| **L2 响应** | `{ node_id }`，写入 `l3_nodes`（`sub_account_id` 为空表示待审批） |

### 阶段 2：L2 管理员审批

| 动作 | 说明 |
|------|------|
| **管理员** | 在 L2 后台将节点分配给子账号 |
| **API** | `POST /api/v2/admin/nodes/assign` `{ node_id, sub_account_id }` |
| **Header** | `Authorization: Bearer <JWT>`（需先 `POST /api/v2/admin/login` 获取 JWT） |

### 阶段 3：L3 轮询获取 Key

| 动作 | 说明 |
|------|------|
| **L3 轮询** | `GET /api/v2/auth/poll?node_id=xxx` |
| **pending** | 返回 `{ status: "pending" }` |
| **approved** | 返回 `{ status: "approved", encrypted_api_keys: [...] }` |
| **L3** | 用本地私钥解密，实例化 LiteLLMEngine，启动 WebSocket 18981 |

---

## 二、API 契约

### POST /api/v2/auth/sync

**请求体**（字段随 L2 是否已写 `tenant_id` 而定；未配对开发态可省略 `organization_id`）：
```json
{
  "device_fingerprint": "sha256:...",
  "public_key_pem": "-----BEGIN PUBLIC KEY-----\n...",
  "capabilities": [],
  "organization_id": "00000000-0000-0000-0000-000000000000",
  "workspace_name": "可选展示名"
}
```

**响应**：
```json
{
  "node_id": "l3-abc123",
  "status": "registered",
  "message": "L3 node registered. Use GET /api/v2/auth/poll to fetch encrypted API keys."
}
```

### GET /api/v2/auth/poll?node_id=xxx

**响应（pending）**：
```json
{
  "status": "pending",
  "message": "Waiting for L2 admin to assign sub-account"
}
```

**响应（approved）**：
```json
{
  "status": "approved",
  "node_id": "l3-abc123",
  "sub_account_id": "sub-xxx",
  "encrypted_api_keys": [
    { "id": "key-xxx", "provider": "openai", "encrypted_key": "base64..." }
  ]
}
```

### POST /api/v2/admin/nodes/assign

**请求体**（需 Bearer JWT，先 `POST /api/v2/admin/login` 登录）：
```json
{
  "node_id": "l3-abc123",
  "sub_account_id": "sub-xxx"
}
```

---

## 三、数据模型：l3_nodes（SQLite，迁移后列齐全）

核心列包括：`id`、`device_fingerprint`、`public_key_pem`、`sub_account_id`（NULL = 待审批）、`capabilities_json`、`organization_id`、`workspace_name`、`display_name`、`last_seen_at`、`created_at` 等（以 `core/db/schema.py` 迁移为准）。

---

## 四、本地配置

| 路径 | 用途 |
|------|------|
| `~/.jachin/l3_identity.json` | RSA 私钥/公钥（严禁泄露私钥） |
| `~/.jachin/l2_gateway_config.json` | L2 地址、`organization_id`、`workspace_name`、node_id、paired 等 |

---

## 五、安全防线

| 机制 | 说明 |
|------|------|
| **双盲** | L2 不持有 L3 私钥；L3 不持有明文 API Key 直至解密 |
| **密文下发** | L2 用 L3 公钥加密 Key，仅 L3 私钥可解密 |
| **审批门控** | 管理员显式分配节点到子账号，防止未授权接入 |

---

## 六、与 L1↔L2 控制面配对的关系

- **禁止**将本文流程表述为「L1↔L3 配对」。L3 **只**与 L2 交换 `auth/sync` / `auth/poll`。
- **L2** 须先与 L1 建立信任（`nexus_config.json`、`tenant_id`），见 **[L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md)**。

---

**相关文档**:
- [ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md](./ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md) — L1 工作区 · L2 网关 · L2↔L3 权威说明
- [L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md) — L1↔L2 网关邮箱、Web Bridge 与 CLI 辅助
- [ARCHITECTURE_V2_LAYER3_STANDALONE.md](./ARCHITECTURE_V2_LAYER3_STANDALONE.md) — V2 架构
- [V2_ARCHITECTURE_DIAGRAM.md](./V2_ARCHITECTURE_DIAGRAM.md) — 架构图
- [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) — 信任链与心跳
