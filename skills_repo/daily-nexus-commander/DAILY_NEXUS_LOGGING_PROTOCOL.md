# Daily Nexus Commander — 日志协议（SSOT）

**技能 ID（概念）**: `daily_nexus_commander`  
**执行入口**: 仓库根目录 `scripts/run_daily_nexus.py`  
**本文档版本**: 1.0  

---

## 1. 目的

- 每次运行留下**可审计、可排障**的轨迹：环境、配置、各原子步骤的输入摘要、输出摘要、耗时、错误栈。
- 与「早报正文」`~/.jachin/workspace/daily_nexus/YYYY-MM-DD.md` 分离：**正文给用户看，日志给运维/开发者看**。

---

## 2. 日志目录与文件命名

| 项 | 说明 |
|----|------|
| **默认目录** | `%USERPROFILE%\.jachin\jachin_debug\健康skill`（Windows）；可通过配置或环境变量覆盖，见 §4。 |
| **单次运行主日志** | `daily_nexus_run_YYYY-MM-DD_HHMMSS_fff.log`（本地时间，`fff` 为毫秒，避免同秒并发覆盖）。 |
| **协议副本** | 首次在日志目录落地时写入 `DAILY_NEXUS_LOGGING_PROTOCOL.md`（与仓库 `skills_repo/daily-nexus-commander/` 下同名文件同步）。 |

---

## 3. 日志级别与字段

- **级别**: `DEBUG`（文件） / `INFO`（关键里程碑） / `WARNING` / `ERROR`（含 `exc_info`）。
- **行格式**: ISO8601 风格时间戳 + 级别 + logger 名 + 消息；消息中可含 `trace_id=...` 关联一次运行。
- **禁止**在日志中写入完整密钥：Webhook、SMTP、Token 等仅允许 **前缀掩码**（如 `https://open.feishu.../****`）或布尔「已设置/未设置」。

---

## 4. 配置覆盖（优先级从高到低）

1. 环境变量 **`DAILY_NEXUS_LOG_DIR`**（绝对路径优先）。
2. 用户配置 `~/.jachin/config/skills/com.jachin.daily_nexus_commander/daily_nexus.yaml` 中键 **`log_dir`**。
3. 默认 `%USERPROFILE%\.jachin\jachin_debug\健康skill`。

可选：

- **`log_level`**: `DEBUG` | `INFO` | `WARNING`（默认 `DEBUG` 写入文件，便于排障）。
- **`log_copy_latest`**: 若为 `true`，额外写入 `daily_nexus_latest.log`（覆盖为最近一次运行全文）。

---

## 5. 单次运行必须记录的内容（检查清单）

以下条目在实现中应逐条出现（缺失则视为日志不完整）。

| # | 阶段 | 记录要点 |
|---|------|----------|
| 1 | **启动** | `trace_id`；`sys.argv`；进程 PID；Python 版本；平台；**仓库 ROOT**；当前工作目录 `cwd`。 |
| 2 | **配置** | 解析后的配置文件绝对路径；`weather_city`；`notify_channel`；是否配置 `lark_webhook_url`（仅掩码）；`log_dir` 最终路径。 |
| 3 | **机器脉搏** | 调用 `sys:health_stats` 等效实现；原始返回 JSON 或错误栈。 |
| 4 | **天气** | 请求城市；`util:get_weather_lite` 返回或错误。 |
| 5 | **SQLite** | 数据库路径是否存在；只读 URI；每条 snippet 的 **SQL 原文**、行数、失败原因。 |
| 6 | **后台任务** | `core:check_background_task` + `list_recent` 的原始返回字符串。 |
| 7 | **Shell 后台** | `registry.json` 是否存在；任务条数；抽样 job_id / status。 |
| 8 | **飞书任务队列** | 是否 `--skip-mcp`；插件路径；`atom_lark_list_tasks` 返回 JSON 或异常栈。 |
| 9 | **桌面提醒** | 每条 reminder 的 kwargs（不含密钥）；`run_schedule_desktop_reminder` 返回。 |
| 10 | **输出物** | 早报 Markdown 长度、写入路径（若未 `--no-save`）。 |
| 11 | **飞书推送** | 是否尝试发送；`notify_channel` 与 `--send-lark`；返回摘要或错误（无密钥明文）。 |
| 12 | **结束** | 总耗时 ms；退出码 0。 |

---

## 6. 与「四大原语」的对应关系（便于文档索引）

| 原语 | 本脚本中的体现 |
|------|----------------|
| **Tools** | `sys:health_stats`、`util:get_weather_lite`、`core:check_background_task`、`util:schedule_desktop_reminder` 等（宿主 Native 实现）。 |
| **MCP** | 飞书列表**不**走 stdio（直连插件 Python）；飞书推送可走 `mcp:atom_lark_notifier` 等价实现 `send_lark_markdown`。 |
| **Skills** | 声明式见 `skills_repo/daily-nexus-commander/SKILL.md`；本协议描述**可观测性**，非替代 SKILL 业务规则。 |

---

## 7. 故障排查速查

| 现象 | 日志中先看 |
|------|------------|
| 无日志文件 | `DAILY_NEXUS_LOG_DIR` / `log_dir` / 默认路径是否可写；盘符是否存在。 |
| 飞书未推送 | `notify_channel` 与 `send_lark` 段落；Webhook 掩码是否为「未设置」。 |
| SQLite 空 | `db` 路径与 `sqlite_snippets` 段落。 |

---

*由 Jachin 仓库维护；修改 `run_daily_nexus.py` 时请同步更新本协议与 SKILL 中「日志」一句说明。*
