# DashScope 区域化 API Key 架构（国内 CN / 东南亚 SEA）

**定位**：百炼（DashScope）在 **中国大陆** 与 **东南亚 / 国际接入** 使用不同的控制台、域名与 Key。本文档为仓库内该能力的 **单一事实来源（SSOT）**。

**相关代码**：

- `core/brain/llm/dashscope_regional.py` — 区域解析、`litellm_apply_dashscope_credentials`
- `l3_node/llm_client.py` — `SecurityContext` 注入、`_inject_key`、调度日志 `region=` / `api_base=`
- `clients/desktop/src-tauri/src/l3_spawn.rs` — `L3_ENV_KEYS`、`load_l3_env_vars`、子进程 env 注入

---

## 1. 区域开关

| 变量 | 取值 | 说明 |
|------|------|------|
| `JACHIN_ACTIVE_REGION` | `CN`（默认）或 `SEA` | 决定使用哪一组区域专用环境变量与默认 `api_base`。未设置时通常回退为 `CN`（见 `get_jachin_active_region()`）。 |

- 桌面/便携场景下，该变量应写入 **项目根 `.env`** 或 **`~/.jachin/.env`**，并由 Tauri 的 `load_l3_env_vars` 注入 L3 子进程（见下文「桌面端」）。
- 若项目根 `.env` 与统帅目录合并使用，注意 **后加载规则**：统帅目录键默认不覆盖项目已有同名键（与 `override=false` 语义一致），避免旧的全局 `JACHIN_ACTIVE_REGION` 误覆盖项目配置。

---

## 2. 环境变量一览

### 2.1 分区域 Key（推荐）

| 变量 | 适用区域 | 说明 |
|------|-----------|------|
| `DASHSCOPE_API_KEY_CN` | `JACHIN_ACTIVE_REGION=CN` | 国内控制台申请的 sk，走国内接入 |
| `DASHSCOPE_API_KEY_SEA` | `JACHIN_ACTIVE_REGION=SEA` | 国际/东南亚控制台申请的 sk，走国际接入 |

若当前区域 **未** 配置上述专用 Key，则回退到通用变量（见 2.2）。

### 2.2 通用回退 Key（兼容旧部署）

以下任一非空即可作为回退（优先级见 `get_dashscope_regional_credentials()`）：

- `DASHSCOPE_API_KEY`
- `QWEN_API_KEY`
- `QWEN_AI_API_KEY`

### 2.3 API Base（可选覆盖）

| 变量 | 默认（未设置时由代码补全） |
|------|---------------------------|
| `DASHSCOPE_API_BASE_CN` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `DASHSCOPE_API_BASE_SEA` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| `DASHSCOPE_API_BASE` | 在未配置区域专用 base 时作为通用覆盖；否则各区域仍以上述默认为准 |

**校验**：东南亚场景下日志中 `api_base` 应为 **`dashscope-intl`** 域名；国内为 **`dashscope.aliyuncs.com`**。若出现国际 endpoint + 国内 sk，会返回 **401 Incorrect API key**（见第 3 节）。

---

## 3. L2 下发 Key 与区域专用 Key 的优先级

L2 经配对解密后，常将 **国内控制台** 同步的 Key 写入 `SecurityContext`（`explicit_api_key` 路径）。

**当前实现规则**（`litellm_apply_dashscope_credentials` / `_inject_key`）：

1. 若用户已为 **当前区域** 配置 `DASHSCOPE_API_KEY_SEA`（`SEA`）或 `DASHSCOPE_API_KEY_CN`（`CN`），则 **不得** 用 L2 下发的 Key 覆盖 —— 避免 **国际 `api_base` + 国内 sk** 的组合。
2. 若未配置区域专用 Key，则仍可使用 L2 下发的 `explicit_api_key` 作为 DashScope 调用 Key。

因此：**东南亚部署** 应在环境或统帅目录中配置 **`DASHSCOPE_API_KEY_SEA`**（并设 `JACHIN_ACTIVE_REGION=SEA`），而不是仅依赖 L2 同步的国内 Key。

---

## 4. 桌面端（Tauri）注入

`load_l3_env_vars` 从 **项目根 `.env`** 与 **`~/.jachin/.env`** 读取 **白名单** `L3_ENV_KEYS`，并注入到：

- Python 回退启动（`spawn_l3_via_python`）
- Sidecar（`sidecar.env(...)`）
- 便携包直接 exe 路径

白名单包含：`DASHSCOPE_API_KEY`、`DASHSCOPE_API_KEY_SEA`、`DASHSCOPE_API_KEY_CN`、`DASHSCOPE_API_BASE`、`DASHSCOPE_API_BASE_SEA`、`DASHSCOPE_API_BASE_CN`、`JACHIN_ACTIVE_REGION` 等（以 `l3_spawn.rs` 中常量为准）。

网关配对场景下，还可通过 **`env_overlay`** 与上述变量合并，保证统帅目录下发的区域与 Key 与子进程一致。

---

## 5. 从「仅 `DASHSCOPE_API_KEY`」迁移

1. 根据部署地设置 `JACHIN_ACTIVE_REGION=CN` 或 `SEA`。
2. 将 Key 迁移为 `DASHSCOPE_API_KEY_CN` 或 `DASHSCOPE_API_KEY_SEA`（推荐），或暂时保留通用 `DASHSCOPE_API_KEY` 作为回退。
3. 东南亚访问国际接入时，确认日志中 **`region=SEA`** 且 **`api_base`** 含 **`dashscope-intl`**。
4. 若同时使用 L2 密钥托管，请阅读第 3 节，避免国内 Key 覆盖 SEA 配置。

---

## 6. 延伸阅读

- L3 三种拿 Key 路径与 env 可达性：`docs/L3_KEY_AND_ENV_ANALYSIS.md`
- 便携包部署：`docs/README_DEPLOY.md`
