# Lark「我要招聘」无回复 — 排查指南

**招聘架构（DAG、数据目录、调度与智能化绑定）**：见 [HR_RECRUITMENT.md](./HR_RECRUITMENT.md)。

## 问题现象

在 Lark 群聊或私聊中发送「我要招聘」后，没有任何回复。

## 日志关键线索

根据 `l3_debug.log` 可快速定位：

| 日志内容 | 含义 |
|----------|------|
| `[IM Lark] 长连接启动中 app_id=... chat_ids=[]` | Lark 通道已启动，长连接线程开始 |
| `connect failed, err: 1000040351: Incorrect domain name` | **域名与飞书应用不匹配**（最常见） |
| `[IM Lark] 未配置 app_id/app_secret` | 未配置 Lark 凭证 |
| `[IM Lark] 收到消息 chat_id=... text=...` | 已收到消息，进入处理流程 |
| `ConnectError: Cannot connect to host dashscope.aliyuncs.com` | **LLM 网络不可达**（Lark 已连，但无法调大模型） |
| `[IM Dispatcher] 招聘类消息，走 HR process_lark_message` | 招聘流程已触发 |
| `HR 招聘 MCP 包未找到` | 未订阅或未拉取 HR 招聘包 |

## 根因分析

### 1. Incorrect domain name（域名不匹配）

**原因**：飞书应用创建在 **飞书中国版**（feishu.cn）或 **Lark 国际版**（larksuite.com），但配置中的 `domain` 与创建地不一致。

- 应用在 **feishu.cn** 创建 → 必须使用 `https://open.feishu.cn`
- 应用在 **larksuite.com** 创建 → 必须使用 `https://open.larksuite.com`

**处理**：编辑 `~/.jachin/config/im_channels.yaml`，设置正确的 `domain`：

```yaml
im_channels:
  lark:
    enabled: true
    mode: long_connection
    app_id: "cli_xxx"
    app_secret: "xxx"
    chat_ids: []
    domain: "https://open.feishu.cn"   # 中国版
    # domain: "https://open.larksuite.com"   # 国际版
```

若未配置 `domain`，默认使用 `https://open.larksuite.com`。

### 2. 未配置 Lark 凭证

**原因**：`im_channels.yaml` 中 `app_id`、`app_secret` 为空，且环境变量 `LARK_APP_ID`、`LARK_APP_SECRET` 未设置。

**处理**：

- 方式一：在 `~/.jachin/config/im_channels.yaml` 中填写 `app_id`、`app_secret`
- 方式二：在 `.env` 或系统环境变量中设置 `LARK_APP_ID`、`LARK_APP_SECRET`

### 3. 长连接未建立（无「收到消息」日志）

**原因**：长连接连接失败，或 Lark 后台未正确配置事件订阅。

**处理**：

1. 确认 Lark 应用后台「事件订阅」已启用
2. 确认「使用长连接接收回调」已开启（L3 使用长连接，非 Webhook）
3. 检查 `chat_ids`：若配置了非空列表，当前会话的 `chat_id` 必须在列表中，否则消息会被忽略

### 4. LLM 连接失败（ConnectError / 无法连接 dashscope）

**现象**：Lark 已收到消息（有 `[IM Lark] 收到消息` 日志），但无回复或回复「抱歉，处理时发生错误」。

**日志**：`httpx.ConnectError: Cannot connect to host dashscope.aliyuncs.com:443` 或 `ConnectionResetError`。

**原因**：当前机器无法访问阿里云 DashScope API（dashscope.aliyuncs.com），常见于：
- 内网/受限环境无外网访问
- 公司防火墙/代理限制
- 需代理才能访问外网

**处理**：
1. **网络测试**：在 L3 所在机器执行 `curl -I https://dashscope.aliyuncs.com` 或浏览器访问，确认是否可达
2. **代理**：若需代理，在 `.env` 或系统环境变量设置 `HTTP_PROXY`、`HTTPS_PROXY`（LiteLLM/httpx 会自动使用）
3. **换 LLM**：若 DashScope 不可用，可配置 `OPENAI_API_KEY` 和 `LLM_MODEL=openai/gpt-4o-mini` 等可访问的模型

### 5. HR 招聘 MCP 包未找到

**原因**：L3 未从 L2 拉取到 `com.jachin.hr.recruitment` 包，或路径解析失败。

**处理**：

1. 在 L2 控制台订阅 `com.jachin.hr.recruitment`
2. 确认 L3 能连接 L2，启动后自动拉取到 `~/.jachin/l3_mcp_cache/`
3. 若 L2 使用 UUID 目录名，可创建符号链接：`mklink /J com.jachin.hr.recruitment <UUID目录名>`

## 配置检查清单

- [ ] `~/.jachin/config/im_channels.yaml` 存在且 `lark.enabled: true`
- [ ] `app_id`、`app_secret` 已填写（或通过环境变量）
- [ ] `domain` 与飞书应用创建地一致（feishu.cn / larksuite.com）
- [ ] `chat_ids` 为空或包含目标会话 ID
- [ ] L2 已订阅 `com.jachin.hr.recruitment`，L3 已拉取到 `~/.jachin/l3_mcp_cache`
- [ ] `DASHSCOPE_API_KEY` 或等效 LLM Key 已配置

## 相关文件

- `l3_node/im_channels/lark_channel.py` — Lark 入站通道
- `l3_node/channels/lark/long_connection.py` — 长连接实现
- `l3_node/im_channels/config.py` — 配置加载（`~/.jachin/config/im_channels.yaml`）
- `config/im_channels.yaml.example` — 配置示例
