# 配对协议 (V2 L3-L2 零信任)

**版本**: 2.0  
**状态**: 设计规范  
**定位**: L3 节点向 L2 网关宣誓效忠 — RSA 双盲零信任  
**已废弃**: L1 6 位码配对（仅 Layer 2 daemon 保留兼容）

---

## 一、V2 L3-L2 零信任配对（主流程）

L3 节点（Tauri 桌面端 + l3_node）自行生成 RSA 密钥对，向 Layer 2 网关发起注册，管理员审批后获取加密的 API Key。

### 阶段 1：L3 向 L2 注册

| 动作 | 说明 |
|------|------|
| **L3** | 检查 `~/.jachin/l3_identity.json`，无则生成 RSA 密钥对并持久化 |
| **L3 发送** | `POST /api/v2/auth/sync` `{ device_fingerprint, public_key_pem }` |
| **L2 响应** | `{ node_id }`，登记到 `l3_nodes` 表（sub_account_id 为空，待审批） |

### 阶段 2：L2 管理员审批

| 动作 | 说明 |
|------|------|
| **管理员** | 在 L2 后台将节点分配给子账号 |
| **API** | `POST /api/v2/admin/nodes/assign` `{ node_id, sub_account_id }` |
| **Header** | `X-Admin-Token`（环境变量 `JACHIN_L2_ADMIN_TOKEN`） |

### 阶段 3：L3 轮询获取 Key

| 动作 | 说明 |
|------|------|
| **L3 轮询** | `GET /api/v2/auth/poll?node_id=xxx` |
| **pending** | 返回 `{ status: "pending" }` |
| **approved** | 返回 `{ status: "approved", encrypted_api_keys: [...] }` |
| **L3** | 用本地私钥解密，实例化 LiteLLMEngine，启动 WebSocket 18881 |

---

## 二、API 契约

### POST /api/v2/auth/sync

**请求体**：
```json
{
  "device_fingerprint": "sha256:...",
  "public_key_pem": "-----BEGIN PUBLIC KEY-----\n...",
  "capabilities": []
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

**请求体**（需 X-Admin-Token）：
```json
{
  "node_id": "l3-abc123",
  "sub_account_id": "sub-xxx"
}
```

---

## 三、数据模型：l3_nodes

```sql
CREATE TABLE l3_nodes (
    id TEXT PRIMARY KEY,
    device_fingerprint TEXT,
    public_key_pem TEXT NOT NULL,
    sub_account_id TEXT,  -- NULL = 待审批
    capabilities_json TEXT DEFAULT '{}',
    last_seen_at REAL,
    created_at REAL
);
```

---

## 四、本地配置

| 路径 | 用途 |
|------|------|
| `~/.jachin/l3_identity.json` | RSA 私钥/公钥（严禁泄露私钥） |
| `~/.jachin/l2_gateway_config.json` | L2 地址、node_id、paired 状态 |

---

## 五、安全防线

| 机制 | 说明 |
|------|------|
| **双盲** | L2 不持有 L3 私钥；L3 不持有明文 API Key 直至解密 |
| **密文下发** | L2 用 L3 公钥加密 Key，仅 L3 私钥可解密 |
| **审批门控** | 管理员显式分配节点到子账号，防止未授权接入 |

---

## 六、Legacy：L1 6 位码配对（仅 Layer 2 daemon）

Layer 2 daemon（nexus_daemon）、jachin-cli、run-pair 仍使用 L1 配对：

- `POST /api/v1/pairing/request` → 6 位 short_code
- 用户去 Nexus Console 输入 → `POST /api/v1/pairing/confirm`
- 轮询 `GET /api/v1/pairing/status` → access_token
- 写入 `~/.jachin/nexus_config.json`

**Tauri 桌面端已不再使用此流程**，统一走 V2 L2 网关配对。

---

**相关文档**:
- [ARCHITECTURE_V2_LAYER3_STANDALONE.md](./ARCHITECTURE_V2_LAYER3_STANDALONE.md) — V2 架构
- [V2_ARCHITECTURE_DIAGRAM.md](./V2_ARCHITECTURE_DIAGRAM.md) — 架构图
- [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) — 信任链与心跳
