# 设备配对协议 (Device Authorization Grant)

**版本**: 1.0  
**状态**: 设计规范  
**定位**: 端云身份信任基石 — 6 位码傻瓜式绑定  
**行业标准**: OAuth 2.0 RFC 8628 (Device Authorization Grant)，常用于 Apple TV、智能电视、IoT 网关

---

## 一、核心流程：端云三次握手

用户**仅参与第 2 阶段**，其余全自动。

### 阶段 1：前线边缘智能体请求配对 (Layer 2 自动发起)

当全新 Layer 2 微内核启动且**未找到配置文件**时，自动向云端发起请求。

| 动作 | 说明 |
|------|------|
| **Layer 2 发送** | `POST /api/v1/pairing/request`，附带设备指纹 / 临时公钥 |
| **Layer 1 响应** | 生成 6 位短码（如 `J8K2X9`，有效期 5 分钟）、长 UUID 作为 `session_id` |
| **边缘智能体终端显示** | 见下方 |

**终端输出示例**：

```
[ Jachin Core ] 初始化完毕。未检测到指挥部授权。
请在浏览器访问: https://nexus.jachin/pair
并输入以下 6 位配对码:

👉  J8K2X9  👈

(该验证码将在 04:59 后失效...)
```

---

### 阶段 2：指挥官授权 (用户唯一需要做的)

| 动作 | 说明 |
|------|------|
| **用户** | 在浏览器打开 Layer 1 Jachin Console，登录账号 |
| **用户** | 在毛玻璃输入框中输入 `J8K2X9`，点击「授权绑定」 |
| **前端调用** | `POST /api/v1/pairing/confirm` `{ "code": "J8K2X9" }` |
| **Layer 1 动作** | 验证配对码有效 → 将 session 与 `user_id` 绑定 → 在 `layer2_instances` 中注册边缘智能体身份 |

---

### 阶段 3：密钥与令牌下发 (水下静默完成)

在用户输入的同时，Layer 2 一直在后台**静默轮询**。

| 动作 | 说明 |
|------|------|
| **Layer 2 轮询** | `GET /api/v1/pairing/status?session_id=...` |
| **用户点击确认后** | 接口返回 `status: "success"`，并附带： |
| | • `access_token` — 边缘智能体拉取插件、发送心跳的专属通行证 |
| | • `layer1_public_key` — 验证 .jmp 插件包签名的公钥 |
| **边缘智能体保存** | 写入本地 `.env` 或加密 `config.yaml` |
| **终端反馈** | `✅ 授权成功，边缘智能体已连接至指挥部。` |

---

## 二、API 契约

### POST /api/v1/pairing/request

**请求体**：
```json
{
  "device_fingerprint": "sha256:...",
  "temp_public_key": "base64...",
  "environment_type": "docker",
  "core_version": "1.2.0"
}
```

**响应**：
```json
{
  "session_id": "uuid",
  "short_code": "J8K2X9",
  "expires_in": 300,
  "pair_url": "https://nexus.jachin/pair"
}
```

### POST /api/v1/pairing/confirm

**请求体**（需登录态）：
```json
{
  "code": "J8K2X9"
}
```

**响应**：
```json
{
  "success": true,
  "instance_id": "dev-layer2-001"
}
```

### GET /api/v1/pairing/status?session_id=...

**响应（pending）**：
```json
{
  "status": "pending"
}
```

**响应（success）**：
```json
{
  "status": "success",
  "access_token": "jwt...",
  "layer1_public_key": "base64...",
  "instance_id": "dev-layer2-001",
  "nexus_base_url": "https://nexus.jachin"
}
```

---

## 三、数据模型：pairing_sessions

```sql
CREATE TABLE pairing_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    short_code VARCHAR(6) NOT NULL UNIQUE,
    status VARCHAR(20) DEFAULT 'pending',  -- pending | approved | expired
    device_info JSONB,
    user_id UUID REFERENCES nexus_users(id),
    layer2_instance_id UUID REFERENCES layer2_instances(id),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_pairing_short_code ON pairing_sessions(short_code);
CREATE INDEX idx_pairing_expires_at ON pairing_sessions(expires_at);
```

---

## 四、安全防线

| 机制 | 说明 |
|------|------|
| **短生命周期** | 6 位码仅 5 分钟有效，过期即失效或硬删除，降低暴力破解窗口 |
| **防暴破限流** | `/confirm` 接口：同一 IP 连续输错 5 次 → 封禁 1 小时 |
| **一次性消费** | Layer 2 拿到 Token 和公钥后，`session_id` 与 6 位码**立即作废**，防重放 |
| **用户无感** | 用户无需理解 API Key、公钥证书，只输入类似验证码的 6 位字符 |

---

## 五、与信任链的衔接

| 输出 | 用途 |
|------|------|
| `access_token` | 后续 `GET /api/v1/deploy/poll`、`POST /api/v1/instances/heartbeat` 的 Authorization 头 |
| `layer1_public_key` | `extract_and_verify_signature(downloaded_file, public_key)` 中的公钥 |

---

**相关文档**:
- [INVISIBLE_SECURITY_UX.md](./INVISIBLE_SECURITY_UX.md) - 无感安全 UX
- [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) - 信任链与心跳
