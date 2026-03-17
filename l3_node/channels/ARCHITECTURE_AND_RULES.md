# channels 通道层 — 架构说明与规则约定

本文档说明 `l3_node/channels/` 的设计意图、核心规则及使用约束。

---

## 一、设计背景与意义

### 1.1 为什么需要 channels 层？

在引入 channels 之前，Lark 与邮件的发送能力分散在：

- `l3_node/mcp_tools/`：BI 战报推送（Webhook、SMTP）
- `skills_repo/plugin/2-track-a-atomic-mcp/tools/`：HR 场景（IM 发消息、多维表群通知）

这些实现**各自封装** Lark API、Token 获取、邮件发送逻辑，导致：

- **重复实现**：`atom_lark_send_message` 与 `atom_lark_bitable_sync` 各自实现 `_get_tenant_access_token`、`_send_lark_message`
- **难以扩展**：接入 Telegram、Slack 等新通道时，需在多个位置新增逻辑
- **职责混杂**：业务工具既做业务编排，又直接对接 IM/邮件协议

### 1.2 channels 层的职责

channels 层将「**消息投递**」从业务与 MCP 工具中抽离，形成：

| 层级 | 职责 |
|------|------|
| **channels** | 通道抽象、协议实现、统一注册与查找 |
| **mcp_tools** | MCP 工具暴露，校验参数并委托 channels |
| **plugin tools** | 业务编排（多维表、对话等），发消息时委托 channels |

业务与 MCP 工具不再直接调用 Lark/邮件 API，而是通过 channels 进行通道无关的投递。

---

## 二、核心规则

### 2.1 通道无关原则

- **禁止** 业务代码或 MCP 工具直接 `import` 某通道的内部实现（如 `requests.post` 调 Lark）。
- **推荐** 使用 `get_channel_plugin(channel_id)` 或直接 `from l3_node.channels.lark import send_im_text` 等公开接口。
- **目的**：后续切换通道（如 Lark → Telegram）时，仅调整通道选择逻辑，无需改动业务代码。

### 2.2 返回值契约

所有通道的 `send_*` 函数统一返回：

```python
# 成功
{"status": "success", "msg": "..."}

# 失败
{"status": "error", "error": "..."}
```

调用方根据 `status` 判断成功与否，不得依赖其他字段。

### 2.3 通道注册规则

- 每个通道在 `channels/<id>/__init__.py` 中完成 `register_channel_plugin(plugin)`。
- 导入 `l3_node.channels` 时自动注册所有内置通道。
- 新通道必须实现 `id`、`meta`、`outbound`，并在 `channels/__init__.py` 中增加导入。

### 2.4 别名规则

- 通道可通过 `meta.aliases` 注册别名，如 `lark` 的别名 `feishu`。
- `get_channel_plugin("feishu")` 与 `get_channel_plugin("lark")` 返回同一插件。

---

## 三、架构示意

```
┌─────────────────────────────────────────────────────────────────┐
│  业务层：BI 战报、HR 多维表、招聘调度、Lark 对话 等                  │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  MCP 工具 / Plugin Tools                                         │
│  tool_lark_notifier、atom_lark_send_message、atom_lark_bitable   │
│  → 参数校验 → 委托 channels                                       │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  channels 层                                                     │
│  registry：get_channel_plugin(id)                                │
│  lark：webhook / im / client / inbound_webhook（入站解析）         │
│  email：smtp                                                     │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  外部服务：Lark API、SMTP 服务器、未来 Telegram/Slack 等            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 四、各通道的边界

| 通道 | 用途 | 凭证 | 典型调用方 |
|------|------|------|------------|
| **lark_webhook** | Webhook 单向推送 Markdown 卡片 | 仅需 webhook_url | BI 战报、周报 |
| **lark** (IM) | 通过 Lark Open API 发群/单聊消息 | App ID + Secret | HR 多维表群通知、主动发消息 |
| **email** | SMTP 邮件 | host/user/password | BI 战报、审批通知 |

Webhook 与 IM 是两种不同的「出站」方式，分别对应 `LarkWebhookChannelPlugin` 与 `LarkChannelPlugin`。

---

## 五、扩展新通道（如 Telegram）

1. 新建 `channels/telegram/` 目录。
2. 实现 `client.py`（凭证/Token）、`send.py`（发送逻辑）、`plugin.py`（ChannelPlugin 实例）。
3. 在 `channels/telegram/__init__.py` 中调用 `register_channel_plugin(TelegramChannelPlugin())`。
4. 在 `channels/__init__.py` 中增加 `from l3_node.channels import telegram`。
5. MCP 或业务侧通过 `get_channel_plugin("telegram")` 或新增 `tool_telegram_notifier` 等薄封装调用。

---

## 六、与上下游的约定

- **mcp_tools**：保持对外的 MCP ID、参数、返回值不变，内部改为委托 channels。
- **plugin tools**：`_get_tenant_access_token`、`_send_lark_message` 等保留为兼容层，内部委托 channels。
- **契约测试**：`scripts/test_bi_mcp_contract.py` 仍针对 mcp_tools 的公开函数校验，channels 变更不得破坏契约。

---

## 七、参考

- 设计参考：OpenClaw 的 `ChannelPlugin` + `ChannelOutboundAdapter` + `extensions/*` 多通道架构。
- 契约文档：`docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md`。
