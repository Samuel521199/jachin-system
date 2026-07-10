# 隐式学习信号（§4.3）— 单一事实来源

目标：把 **跳过 / 停留 / 复述 / 重复追问** 等信号 **系统化** 进 `intelligence_events.jsonl`，供 `intelligence_e` 聚合、检索加权、产品与调试使用。

## 1. 事件存储

- 路径：`~/.jachin/logs/intelligence_events.jsonl`
- 行格式：`{"ts": <unix>, "type": "<见下表>", "payload": { ... }}`
- 写入 API：`core.intelligence_workspace.emit_intelligence_event`
- 封装：`core.intelligence_implicit.emit_implicit_signal`（自动补 `source`）

## 2. 标准 `type` 与含义

| type | 含义 | 典型 payload |
|------|------|----------------|
| `user_message_skipped` | 用户跳过/忽略助手回复或卡片 | `source`, `reason?`, `sub_account_id?` |
| `user_message_dwell` | 前端上报停留（认为内容被阅读） | `dwell_ms` 或 `seconds` / `dwell_sec`, `source` |
| `user_repeat_intent` | 与 **上一轮用户句** 高度相似（同句重发） | `ratio`, `snippet`, `source` |
| `user_repeat_followup` | **上一轮为 assistant** 后追问：同题复述或不满短句 | `kind`: `same_question_after_answer` \| `dissatisfaction_short`, `ratio?`, `pattern?`, `snippet`, `source` |
| `user_rephrased_assistant` | 用户复述/粘贴助手内容（可选） | `len`, `source` |
| `user_repeat_intent_embedding` | **向量级** 与上一轮用户句语义接近（换表述复述） | `cosine`, `threshold`, `source` |
| `user_repeat_followup_embedding` | **向量级** 在 assistant 后与「再上一轮用户句」语义同题 | `cosine`, `kind`: `semantic_same_question`, `source` |
| `user_echo_assistant_embedding` | **向量级** 用户话与上一轮 **assistant** 内容语义接近（消化/复述答复） | `cosine`, `threshold`, `source` |
| `implicit_turn_attribution` | **全端默认**：每轮用户进线打标（不计入 reinforce，供覆盖率分析） | `channel`, `input_chars`, `run_id`, 及入口自定义字段 |

## 3. 自动检测（L3 `run_agent`）

- **文本**：`core.intelligence_implicit.apply_session_implicit_events` → `user_repeat_intent` / `user_repeat_followup`。
- **向量**：`core.intelligence_implicit_embedding.emit_embedding_implicit_signals`（需可用 `embedding`）；若文本已命中 `user_repeat_intent` 且 `embedding_skip_if_text_repeat=true`，则不再发 `user_repeat_intent_embedding`，避免双计。
- **全端埋点**：传入 `implicit_attribution` 时写 `implicit_turn_attribution`。**已默认接入**：`lark_im_dispatcher`、`websocket_*`、`http_agent_run`、`lark_hr_recruitment`、`delegate_sub_agent`。
- 客户端可叠加 `implicit_signals`：`skip`、`dwell_ms` / `dwell_sec`、`assistant_echo`。

### 3.1 WebSocket（18981）

消息 JSON 可带 `implicit_signals`（对象）；服务端会合并进 `run_agent`。

### 3.2 `nexus_config.json` → `intelligence_implicit`

```json
{
  "intelligence_implicit": {
    "embedding_signals_enabled": true,
    "embedding_prev_user_threshold": 0.88,
    "embedding_followup_user_threshold": 0.82,
    "embedding_echo_assistant_threshold": 0.80,
    "embedding_max_chars": 512,
    "embedding_skip_if_text_repeat": true
  }
}
```

## 4. HTTP 埋点（Lark / Console / 自研 UI）

`POST /api/v2/intelligence/implicit-signal`
Header：`X-Sub-Account-Id`（与 v2 记忆 API 一致）

```json
{
  "signal": "skip",
  "source": "lark",
  "payload": { "reason": "card_dismiss" }
}
```

| signal 值 | 映射 type | payload 要求 |
|-----------|-----------|--------------|
| `skip` | `user_message_skipped` | 可选 `reason` |
| `dwell` | `user_message_dwell` | 必填其一：`dwell_ms` / `dwell_sec` / `seconds` |
| `repeat_followup` | `user_repeat_followup` | 建议 `kind`, `snippet` |
| `repeat_intent` | `user_repeat_intent` | 建议 `ratio`, `snippet` |
| `assistant_echo` | `user_rephrased_assistant` | 自定义 |

服务端会将 `sub_account_id` 写入 `payload`。

## 5. `intelligence_e` 聚合（可选）

`nexus_config.json` → `intelligence_e`：

- `repeat_intent_delta` / `repeat_followup_delta` / `embedding_repeat_intent_delta` / `embedding_followup_delta` / `embedding_echo_assistant_delta` / `message_skipped_delta` / `dwell_bucket_delta` 等按 **事件 type 出现次数** 累加至侧车 `reinforce_memory_id`（与单条 `memory_id` 的 `/memory/feedback` 分离，避免双计）。**`implicit_turn_attribution` 不参与聚合**。

详见 `core/intelligence_e_consumer.py` 文件头注释。

## 6. 与 OpenClaw 对比（产品叙事）

OpenClaw 社区常靠 **插件/脚本** 自行记 JSON；Jachin 提供 **稳定 type 名、统一 HTTP、Agent 内自动检测、文档与消费端**，便于「越用越聪明」在 **多客户端** 上同构落地。
