# L3 × 飞书 / Lark：配置单一说明

本文档整合「L3 交互发消息、工具发飞书、飞书长连接」涉及的配置来源，避免在 `im_channels`、`plugin/.env`、根目录 `.env` 之间来回猜。

---

## 1. 先分清三种场景

| 场景 | 你在哪操作 | 主要读哪里的配置 |
|------|------------|------------------|
| **A. 桌面/终端 ↔ L3 WebSocket**（`ws://…/sensory` 等） | 本机客户端连 L3 | **进程环境变量** + 仓库 **`skills_repo/plugin/.env`**（合并进进程，见 §3） |
| **B. 飞书里 @ 机器人聊天**（长连接收消息） | 飞书客户端 | **`~/.jachin/config/im_channels.yaml`**（`JACHIN_HOME` 可覆盖家目录） |
| **C. Agent 调工具发飞书**（如 `util:lark_send_text`） | 由 L3 内工具调用 Open API | 与 **A** 相同：以 **`LARK_*` / `HR_LARK_*`** 等环境变量为准（多来自 **A** 的合并结果） |

说明：**A 与 C 共用同一套「Open API 凭证」**；**B 单独管「机器人收消息」**，可与 A 使用同一飞书应用，也可按租户拆分，但须在两边分别配置一致或按文档拆分通用/HR 应用。

---

## 2. 一张表：变量放哪

| 配置项 | 推荐位置 | 用途 |
|--------|----------|------|
| **通用机器人** `LARK_APP_ID` / `LARK_APP_SECRET` | `skills_repo/plugin/.env` 或系统/启动脚本环境变量 | 终端会话、`util:lark_send_text`、与用户 **`ou_` open_id** 须同属该应用 |
| **HR 招聘专用** `HR_LARK_APP_ID` / `HR_LARK_APP_SECRET` | 同上 | 仅 HR 插件原子工具、多维表同步等（见 `resolve_hr_lark_credentials()`） |
| **默认会话** `LARK_CHAT_ID`、`LARK_USER_OPEN_ID` 等 | 同上 | 未指定接收方时的默认目标 |
| **长连接** `app_id`、`app_secret`、`domain`、`chat_ids` | `~/.jachin/config/im_channels.yaml` | 飞书侧消息**进入 L3** 的路由与凭证 |
| **L3 引擎 / 模型 Key** | 仓库**根目录** `.env`（若存在） | 与 Lark 无关；由 L2/启动脚本注入时常先于 plugin 加载 |

---

## 3. `skills_repo/plugin/.env` 如何被加载

实现见 `l3_node/channels/lark/client.py` 的 `_ensure_dotenv_loaded()`：

- 第一轮：按顺序对多个路径 **`load_dotenv(..., override=False)`**（不覆盖进程里已有变量，兼容 L2 注入）。
- 第二轮：**仅**对 **`skills_repo/plugin/.env`** 再加载一次 **`override=True`**，使仓库内显式配置的 `LARK_*` / `HR_LARK_*` 能覆盖用户**系统环境变量里残留的旧 `LARK_APP_ID`**（常见原因：`open_id cross app`）。
- 若需强制以**进程/系统环境**为准、禁止 plugin 覆盖：设置 **`JACHIN_IGNORE_PLUGIN_LARK=1`**。
- 若仍不生效：检查根目录 `.env` 是否在 L3 启动**更早**阶段以其它方式写入环境（通常早于本合并逻辑）。

---

## 4. `im_channels.yaml` 与 plugin/.env 的关系

- **`im_channels.yaml`**：L3 **IM 通道插件**（飞书长连接）读取，决定**哪台机器人、是否限 `chat_ids`**。
- **`plugin/.env`**：偏 **Open API 调用**（发消息、解析用户、tenant token 等）。

两者可指向**同一**飞书应用（同一 `app_id`），也可按「通用 / HR」拆分；拆分后须保证：**发消息用的 `LARK_APP_ID` 与用户 `ou_` 同属通用应用**，HR 场景用 `HR_LARK_*`，避免 `open_id cross app`。

详细步骤与排障仍见：`l3_node/im_channels/README.md`（长连接订阅、后台「使用长连接接收回调」等）。

---

## 5. 桌面客户端还读什么

桌面端除连接 **L3 WebSocket** 外，可能另有 **`nexus_config.json` / 镜像推送 URL** 等（用于云边、下载与推送），与 **飞书 tenant 凭证**不是同一套；以 `clients/desktop` 内文档与配置为准。

---

## 6. 相关代码锚点

| 主题 | 位置 |
|------|------|
| plugin 多路径 `load_dotenv` | `l3_node/channels/lark/client.py` → `_ensure_dotenv_loaded` |
| 通用凭证 | `resolve_lark_credentials()` |
| HR 凭证 | `resolve_hr_lark_credentials()` |
| 长连接配置 | `l3_node/im_channels/config.py`、`im_channels.yaml` 示例 |

---

## 7. BI / HR Skill 配置在哪

与「L3 发一条飞书消息」正交，不重复展开，仅索引：

- **BI**：`config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml`（及 `~/.jachin/config/skills/...` 覆盖）；MCP 见 `~/.jachin/config/mcps/`。
- **HR 透析镜等**：`config/skills/com.jachin.hr.analyzer4/`；招聘插件环境与 Lark 叠加仍归 **§2 表**。

---

**维护约定**：新增「飞书相关」配置说明时，优先在本文件增补一节并从他处链到此处，避免再散落多份互相矛盾的清单。
