# Jachin 多通道通讯层

参考 OpenClaw 架构设计，将 IM/邮件等通道抽离为统一抽象层，支持多通道扩展（Lark、Email、未来 Telegram 等）。

**架构说明与规则约定** 详见 [ARCHITECTURE_AND_RULES.md](./ARCHITECTURE_AND_RULES.md)。

## 目录结构

```
channels/
├── base.py       # 通道接口（OutboundAdapter、ChannelPlugin）
├── registry.py   # 通道注册与查找
├── lark/         # 飞书通道
│   ├── client.py   # Token 管理
│   ├── webhook.py  # Webhook 推送（无需 App 凭证）
│   ├── im.py       # IM 消息发送（需 App 凭证）
│   └── plugin.py   # 通道插件
└── email/        # 邮件通道
    ├── smtp.py     # SMTP 发送
    └── plugin.py   # 通道插件
```

## 用法

```python
from l3_node.channels import get_channel_plugin, send_markdown, send_im_text
from l3_node.channels.lark import get_tenant_access_token
from l3_node.channels.email import send_email_with_attachment

# Webhook 推送（BI 战报等）
send_markdown(webhook_url, markdown_content, title=title)

# IM 消息（需 App 凭证）
token = get_tenant_access_token()
send_im_text(receive_id, text, token=token)

# 邮件
send_email_with_attachment(smtp_config, to_addrs, subject, body, attachment_paths)
```

## 通道注册

导入 `l3_node.channels` 时自动注册 lark、email 插件。通过 `get_channel_plugin("lark")` 做通道无关调用。
