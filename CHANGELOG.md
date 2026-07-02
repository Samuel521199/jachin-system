# Changelog

All notable changes to this project will be documented in this file.

## [v0.9.96] - 2026-06-29

### Added / Changed

- 到达 kokoro 极限，改语音模型前夕：Kokoro TTS 管线、陪伴态 UI、语音意图路由与 OS Mission 理解层增强。
- **Version**: Desktop **0.9.96**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.95] - 2026-06-29

### Added / Changed

- OS Assistant release: Windows UIA MCP, OS Mission Router, Codex project briefing to Lark, Evidence console, project memory, mission templates, Windows file ops, Lark workflows, and reliability smoke matrix scaffolding.
- Codex result extraction now prefers final screenshot + Qwen vision extraction, with clipboard fallback disabled by default.
- **Version**: Desktop **0.9.95**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.94] - 2026-06-29

### Added / Changed

- moss改kokoro之前。
- **Version**: Desktop **0.9.94**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.93] - 2026-06-23

### Added / Changed

- codex 使用前测试版：K11 Tongits 全自动打牌冒烟（独立按钮 + Lark 3016 金币结算）、视觉/UI QA MCP、ReAct 截图多模态注入与相关文档。
- **Version**: Desktop **0.9.93**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.72] - 2026-05-26

### Added / Changed

- 大模型写数据库版本，python 写数据库前夕。
- **Version**: Desktop **0.9.72**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.70] - 2026-05-25

### Added / Changed

- 重构整个 PMO 之前。
- **Version**: Desktop **0.9.70**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.68] - 2026-05-18

### Added / Changed

- 周一下午修缮整个 PMO 之前。
- **Version**: Desktop **0.9.68**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.67] - 2026-05-18

### Added / Changed

- Agent 问答监控。
- 增加控制台 PMO。
- **Version**: Desktop **0.9.67**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.66] - 2026-05-14

### Added / Changed

- Agent 问答监控。
- **Version**: Desktop **0.9.66**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.65] - 2026-05-14

### Added / Changed

- PMO 初版全流程。
- **Version**: Desktop **0.9.65**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.64] - 2026-05-14

### Added / Changed

- 修复冒烟测试定时。
- 修复冒烟测试 party 改版。
- **Version**: Desktop **0.9.64**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.63] - 2026-05-14

### Added / Changed

- 修复用户提问被上下文污染。
- **Version**: Desktop **0.9.63**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.62] - 2026-05-13

### Added / Changed

- 完成冒烟 delay 修复。
- **Version**: Desktop **0.9.62**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.61] - 2026-05-12

### Added / Changed

- PMO成功到发送消息卡片。
- **Version**: Desktop **0.9.61**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.60] - 2026-05-12

### Added / Changed

- 完整PMO改Agent的初步全流程。
- **Version**: Desktop **0.9.60**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.59] - 2026-05-12

### Added / Changed

- 改整体方案前的版本。
- **Version**: Desktop **0.9.59**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.58] - 2026-05-11

### Added / Changed

- 改成执行物理点击前的版本。
- **Version**: Desktop **0.9.58**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.57] - 2026-05-11

### Added / Changed

- 完成游戏自动测试初步流程通（GameQA：本地 MCP、Skill、HTTP 点火、YOLO/OCR 语义状态、诊断日志等）。
- **Version**: Desktop **0.9.57**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.56] - 2026-05-08

### Added / Changed

- Kalaroko 巡检与晨报：网络探活静默跳过、本地 08:15 错峰状态机补偿、互斥锁防重入、Playwright 遥测拦截等（详见本次提交）。
- **Version**: Desktop **0.9.56**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.55] - 2026-05-08

### Added / Changed

- 冒烟改为看版本号。
- **Version**: Desktop **0.9.55**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.51] - 2026-04-28

### Added / Changed

- 完成冒烟测试第一版交付。
- **Version**: Desktop **0.9.51**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.50] - 2026-04-24

### Added / Changed

- 除游戏测试外全量推送还是正常的。
- **Version**: Desktop **0.9.50**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.47] - 2026-04-21

### Added / Changed

- BI 每日战报：仓库默认关闭定时调度，避免与 Kalaroko 巡检争用 Chrome/CDP；无 YAML 时默认不再 8:00 触发；支持 ``BI_DAILY_REPORT_SCHEDULE=on`` 强制开启。
- Kalaroko 调度：每日晨报默认改为 UTC 0:15（约北京 8:15）错峰；可通过 ``KALAROKO_DAILY_MORNING_REPORT=0`` 关闭晨报任务。
- **Version**: Desktop **0.9.47**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.46] - 2026-04-24

### Added / Changed

- 游戏脚本变手机尺寸。
- **Version**: Desktop **0.9.46**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.45] - 2026-04-24

### Added / Changed

- 完成打包后冒烟测试的完整执行。
- **Version**: Desktop **0.9.45**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.40] - 2026-04-24

### Added / Changed

- 串联除游戏外的所有冒烟测试，同步 Lark 表格，发送消息卡片。
- **Kalaroko 游戏墙钟**：点击流场景下 ``real_engine_load_ms`` 零点改为「点击游戏入口完成之后」，不含大厅寻址/点击耗时；E2E 报告首页 ``page_load_ms`` 为空时用 ``dom_content_loaded_ms`` 兜底展示与趋势。
- **Kalaroko 进桌竞速**：后置 API 白名单仅保留 Agora / 成员列表；HTTP 先胜出时 ``finally`` 不再 ``await`` UI 子任务，避免死锁；服务端巡检 SSE 先发首包再加载 E2E 脚本。
- **桌面巡检页**：L3 地址并行短探测 + 内存缓存；可选 ``VITE_L3_SKILLS_URL`` 固定 L3 base；Lark 入站 WS 域名与飞书巡检 Open API 解耦（``LARK_USE_FEISHU`` / ``_resolve_lark_im_domain``）。
- **Version**: Desktop **0.9.40**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.39] - 2026-04-22

### Added / Changed

- 完成冒烟测试除游戏测试外所有测试的连接。
- **Version**: Desktop **0.9.39**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.38] - 2026-04-22

### Added / Changed

- **L3 存活看门狗（断电/死机外置告警）**：可选 Healthchecks.io 周期 GET ping（`JACHIN_HEALTHCHECKS_PING_URL`）；`http_server` 启动时后台 daemon 线程；PyInstaller 侧车显式收集 `healthchecks_watchdog` 与 `requests`/`urllib3`；`.env.example` 说明。
- **Version**: Desktop **0.9.38**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.37] - 2026-04-22

### Added / Changed

- 完成冒烟测试除游戏外测试，添加结果写入表格功能。
- **Version**: Desktop **0.9.37**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.36] - 2026-04-22

### Added / Changed

- **L3 小时报 / 日报与侧车打包**：Kalaroko E2E 在 PyInstaller frozen 下可加载（脚本与 `l3_client` 等依赖打入侧车）；`requirements_kalaroko.txt` 注释改为 ASCII 以避免 Windows GBK 下 pip 解码失败；桌面构建相关小修（Vite chunk 告警阈值、冗余 Rust 诊断函数移除）。
- **Version**: Desktop **0.9.36**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.35] - 2026-04-22

### Added / Changed

- **Kalaroko 小时报 / 巡检报告**：小时报与单轮 Markdown 格式调整（飞书友好排版、详细诊断附录、晨报轮次带巡检时间等）；相关 Lark 通知、调度器与 E2E 脚本联动。
- **Version**: Desktop **0.9.35**（`clients/desktop/VERSION` 与 Tauri/npm 对齐）。

---

## [v0.9.34] - 2026-04-22

### Added / Changed

- 冒烟测试完成 23 个；修复 BI 部分问题（含 `start-layer3` 子进程环境与桌面版本等）。
- **Version**: Desktop **0.9.34**（`clients/desktop/VERSION` 与 `sync-version` 产物）。

---

## [v0.9.33] - 2026-04-21

### Added / Changed

- **Kalaroko 定时守护**：小时巡检首跑尽快执行、启动时打印下次计划时间；小时任务开始/结束 INFO 日志；巡检控制台说明文案。
- **日报 / 小时报**：晨报写入 Memory Nexus、JSONL 锁与 E2E 记忆提交策略等相关实现（与调度器、MCP、脚本联动）。
- **Version**: Desktop **0.9.33**（`clients/desktop/VERSION` 与 `sync-version` 产物）。

---

## [v0.8.119] - 2026-04-17

### Added / Changed

- **Memory Nexus**：新增 **SSOT** 文档 `docs/architecture/MEMORY_NEXUS_L3.md`；更新 L3 记忆相关文档/规则/工具描述以反映 **Chroma** 主路径；旧 **`l3_local.json` LLM 合并**口径删除或标注停用。
- 本地整合：环境示例、MCP 示例与注册表、`publish_desktop_release`、YouTube/B 站相关 skill stub、loader 与依赖调整。
- **Version**: Desktop、core CLI 与 `core/main` 版本号 **0.8.119**。

---

## [v0.8.118] - 2026-04-13

### Added / Changed

- 表格格式修复。
- **Version**: Desktop、core CLI 与 `core/main` 版本号 **0.8.118**。

---

## [v0.8.117] - 2026-04-15

### Added / Changed

- 聊天界面：文档与图片附件传入、解析与多模态路由（含 L3 网关、WebSocket、sanitize/trim、DashScope 兼容层等）。
- **Version**: Desktop、core CLI 与 `core/main` 版本号 **0.8.117**。

---

## [v0.8.116] - 2026-04-16

### Added / Changed

- 热更新完成，添加多个 MCP。
- **Version**: Desktop、core CLI 与 `core/main` 版本号 **0.8.116**。

---

## [v0.8.115] - 2026-04-15

### Added / Changed

- L1（Nexus）界面增加 tools 模块及相关能力。
- **Version**: Desktop、core CLI 与 `core/main` 版本号 **0.8.115**。

---

## [v0.8.114] - 2026-04-14

### Added / Changed

- 弹窗、定时、思考链存储；桌面与 Nexus 中英文切换；文件整理；Lark 单聊发送与邮件相关整理。
- **Version**: Desktop（`package.json` / Tauri / `VERSION`）、core CLI 与 `core/main` 版本号 **0.8.114**。

---

## [v0.8.113] - 2026-04-13

### Added

- **L3 `util:compose_long_document`**: Map-Reduce style long Markdown assembly (per-section LLM via `LiteLLMEngine`, then write under native write allowlist).

### Fixed / Changed

- **L3 `run_tool`**: Parse ReAct XML-style `<parameter=name>...</parameter>` for `util:*` / `sys:*` so `topic` / `outline_sections` are not dropped when the model does not emit JSON.
- **`core/llm_provider`**: Longer timeouts and slack for `call_purpose=util_compose_long_document` to reduce spurious timeout → fallback on large `max_tokens` calls.
- **`l3_node/llm_client`**: `JACHIN_QWEN_MAX_MAX_TOKENS` no longer hard-capped at 8192 in code (still clamped to ≥1).
- **Desktop `chat.tsx`**: Register `registerBackgroundTaskHandler` so Omni shows background task completion (and sentry notify), aligned with `ChatPanel`.
- **Tests**: `test_loader_xml_tool_params`, `test_core_util_tools` updates.

- **Version**: Desktop and core CLI bumped to **0.8.113**.

---

## [v0.8.112] - 2026-04-13

### Fixed

- **L3 agent**: `NameError` for `hr_domain_prompt_active` in `_build_system_prompt` — pass `hr_domain_prompt_active` from `run_agent` (and strict verify rebuild) into prompt builder; restores ReAct when HR tools are present.

### Changed

- **L3**: Output-format / tool-need heuristics (`output_format_signals`), native write allowlist, `core_util_tools` / loader updates; unit tests aligned.
- **Version**: Desktop (`package.json`, `tauri.conf.json`, `Cargo.toml`, `VERSION`), core (`main.py`, `sync_daemon.py`, `cli.py`, `cli/jachin_cli`) bumped to **0.8.112**.

---

## [v0.8.108] - 2026-04-10

### Fixed

- **桌面 OMNI**：Mermaid 渲染传入沙箱容器，避免错误 DOM 污染 `body` 导致底部黑条；`chat.html` / 消息区 flex 布局与滚动
- **Tauri 2**：`allow-app-core-invoke` ACL，修复 `invoke`（如 L2 网关配对）被拒绝
- **Mermaid 导出**：全屏内 `html-to-image` 导出 PNG（兼容 foreignObject）；下载完成页内提示与系统通知；`start-layer3.ps1` 修复 PS 5.1 `param` 注释解析、UTF-8 BOM

### Changed

- **版本号**：桌面 `package.json`、`tauri.conf.json`、`Cargo.toml`、`VERSION` 与 Git 标签 **v0.8.108**
- **依赖**：`jachin-desktop` 增加 `html-to-image`（Mermaid PNG 导出）

---

## [v0.8.107] - 2026-04-10

### Fixed

- **云端 UI**：Nexus 等云端界面排版问题修复
- **登录**：账号密码登录问题修复（含注册/校验/凭证相关链路）

### Changed

- **版本号**：`core/main.py`、`core/sync_daemon.py`、`core/cli.py`、`cli/jachin_cli`、`clients/desktop/package.json`、`clients/desktop/package-lock.json`、`clients/desktop/src-tauri/tauri.conf.json`、`Cargo.toml`、`VERSION` 统一为 **0.8.107**（Git 标签 `v0.8.107`）

---

## [v0.8.106] - 2026-04-09

### Added

- **L3 ReAct 护城河**：`agent_core` 对写入对话的 Observation 串统一 **长度截断**（`MAX_REACT_OBSERVATION_FOR_LLM`），降低 MCP Fetch/Tavily/大文件读回撑爆上下文风险；HR 终稿短路与 SQLite 经验判定仍基于未截断全文
- **原生实用工具（PM/策划）**：`util:ab_test_calc`、`util:fake_data_gen`（Faker）、`util:text_diff`、`util:funnel_calc`（`core_util_tools.py`）；单测与 `faker` 依赖
- **MCP stdio 与环境**：`mcp_embedded_runtime` / `mcp_client` 子进程 env、cwd 与 Tavily 链；文档 `docs/MCP_STDIO_API_KEY_AND_ENV.md`、规则 `088-mcp-stdio-apikey-env.mdc`；`config/mcp_servers.json.example`
- **MCP · Office PowerPoint（PPTX）**：`skills_repo/plugin/com.jachin.mcp.office_powerpoint`（L3_LOCAL stdio，`python -m ppt_mcp_server`，依赖 PyPI `office-powerpoint-mcp-server`）；能力域 `office_powerpoint_mcp`（`docs/capability_domains/office_powerpoint_mcp.md`、`DOMAIN_REGISTRY`）；`mcp_servers.json.example` 可选示例条目
- **L3 路径与桌面联调**：`l3_node.paths.get_app_root` 在设置 `JACHIN_APP_ROOT` 且目录含 `l3_node` 或 `skills_repo` 时优先使用该根（修复 `packaged_stdio=0`、HR 找不到 `skills_repo/plugin`）；`scripts/start-layer3.ps1` 同控制台起 L3 时用 `ProcessStartInfo` 显式注入环境变量
- **MCP stdio 排障**：`mcp_client` 在拉起子进程前若发现 args 中 `.py` 路径不存在则跳过并打日志（避免 `Connection closed` 难查）；`scripts/repair_mcp_servers.py` / `repair-mcp-servers.ps1` 修正 `hr-atomic-tools` 指向本仓库 `com.jachin.hr.recruitment/server.py`；`start-layer3.ps1` 默认在启动前运行修复（`-SkipRepairMcp` 可关）；`com.jachin.hr.recruitment` 补 `plugin.json` 供 HR Loader 识别
- **MCP 启动自愈**：`core/mcp_json_repair.py` 在 `start_l3_stdio_mcp_host` 内于 `MCPManager.start()` 前自动修正 `~/.jachin/mcp_servers.json` 中失效的 `hr-atomic-tools` 路径；`l3_packaged_stdio_mcp` 在注册 `com.jachin.mcp.office_powerpoint` 前检测 `ppt_mcp_server`，未安装时打明确 ERROR 与 `pip install` 提示
- **桌面 OMNI**：`MarkdownMessage` + `MermaidViewer`（Mermaid、`react-zoom-pan-pinch` 全屏缩放平移）；`JachinOmniCyberProtocol` 助手气泡走 Markdown 渲染

### Changed

- **文档**：`MCP_SPEC`、`L3_TOOL_POOL_AND_MCP_ASSEMBLY`、L3 记忆与网关等相关增量
- **版本号**：`core/main.py`、`core/sync_daemon.py`、`core/cli.py`、`cli/jachin_cli`、`clients/desktop/package.json`、`clients/desktop/package-lock.json`、`clients/desktop/src-tauri/tauri.conf.json` 统一为 **0.8.106**（Git 标签 `v0.8.106`）

---

## [v0.8.105] - 2026-04-09（本地合入）

### Added

- **网站下载与热更新**：Nexus / jachin-downloads 下载站与桌面端 `tauri-plugin-updater` 对接；`jachin-updater-helper` 独立进程（下载、minisign 校验、准备/应用两阶段）；`publish_desktop_release.py` 发布与签名流程；任务 JSON 内联 `updater_pubkey_wire` 避免助手与主程序公钥不一致。

### Changed

- **版本号**：曾与 **0.8.105** 对齐；合并 `v0.8.106` 后以 **0.8.106** 为准（桌面热更新与 OMNI 等本地改动保留在工作区 / 后续提交）

---

## [v0.8.104] - 2026-04-07

### Added

- **（续 2026-04-17 说明）** 下述 L5 JSON 梦境合并与定时整理**已废弃**；L3 主记忆为 **Memory Nexus / Chroma**（`docs/architecture/MEMORY_NEXUS_L3.md`），`compact_local_memory_if_needed` 为 no-op。
- **L5 记忆坍缩（梦境合并）**（**已废弃，见上**）：`l3_local.json` 超阈值时轻量 LLM 合并；**双缓冲（影子副本）**、**原子覆写**、`memory_compact_control` 取消标记、快照后主库增量合并（`memory_compactor.py`）
- **定时整理与 WS**：`memory_compact_schedule.py`（`~/.jachin/memory/compact_schedule.json`）、`ws_server` 在 manifest 后推送 `memory_compact_suggest`；控制帧 `memory_compact_confirm` / `defer` / `auto_start` / `cancel`
- **桌面端**：`useSensoryWebSocket` 倒计时横幅；`chat.tsx` / `ChatPanel` 操作入口
- **聊天调度**：间隔天数、倒计时秒、推迟整理等话术解析（`agent_core` 注入 system 确认）
- **测试**：`tests/unit/test_memory_compactor_threshold5.py`（阈值 5、mock `litellm`，不依赖真实 API）
- **文档/配置**：`.env.example` 增加 `JACHIN_MEMORY_*` 与坍缩相关说明

### Changed

- **版本号**：`core/main.py`、`core/sync_daemon.py`、`core/cli.py`、`cli/jachin_cli`、`clients/desktop/package.json`、`clients/desktop/src-tauri/tauri.conf.json` 统一为 **0.8.104**（Git 标签 `v0.8.104`）

## [v0.8.103] - 2026-04-08（本地合入记录）

### Changed

- **备份**：本地工作区与桌面/云端/Nexus/下载站/BI 等改动合入；版本号曾为 **0.8.103**；已与 `v0.8.104` 合并后继续以本地策略保留 BI/PMO/热更新等差异（见工作区与 stash 恢复）。

---

## [v0.8.102] - 2026-04-07

### Added

- **合并上游**：Git 标签 `v0.8.101` 合入（桌面更新、Nexus/下载站、BI/PMO、Lark IM 等）
- **L4 / 混合 Agent**：语义层与文档增量（`db_semantics.yaml`、`critic_agent`、`experience_memory`、`deep_execution_log`、架构文与 `.cursor/rules/090-jachin-l4-agent.mdc` 等，以仓库树为准）

### Changed

- **L3**：网关嗅探、工作区 DB 语义、SQLite 写签批与 HR 选岗误判修复、`start-layer3.ps1`（UTF-8 BOM、Tauri CLI 检测、`@tauri-apps` / PowerShell 解析规避）等
- **版本号**：`core/main.py`、`core/sync_daemon.py`、`core/cli.py`、`cli/jachin_cli`、`clients/desktop/package.json` 统一为 **0.8.102**（Git 标签 `v0.8.102`）

---

## [v0.8.100] - 2026-04-04

### Added

- **L3 工具池**：`l3_node/primitives/tools/tool_pool.py` 的 `assemble_tool_pool`（内置 + MCP 合并、RBAC 预检、后台通道剔除）；`run_agent` 单点调用
- **文档**：`docs/architecture/L3_TOOL_POOL_AND_MCP_ASSEMBLY.md`（工具池与 MCP 组装 SSOT）；`.cursor/rules` 065 / 072 交叉引用

### Changed

- **版本号**：`core/main.py`、`core/sync_daemon.py`、`core/cli.py`、`cli/jachin_cli` 统一为 **0.8.100**（Git 标签 `v0.8.100`）

---

## [v0.8.99] - 2026-04-03

### Added

- **Intent Gateway 入站增强**：`l3_node/intent_gateway/`（澄清门控、DAG 拆分/拓扑、槽位、OOD、执行分层等）；**Omni-Context Sniffer**（`context_sniffer.py`：Git + 安全锁摘要 + 本地记忆 Top-2，硬字符预算）；`apply_gateway_ingress_pipeline` 改为 **async** 并在末尾挂载 `bundle.extra["environment_report"]`
- **状态透传**：复用 `on_step`，新增 **`system_status`**（JSON `status`）；嗅探起止、DAG 拓扑校验、task_plan / planning_composite 门闸埋点；桌面端 `useSensoryWebSocket.ts` 解析展示
- **参谋长软拦截**：`pushback_copy.py`、`_build_system_prompt` 注入 `[ENVIRONMENT_REPORT]` + 人设段（`chief_advisor_prompt_enabled`）；槽位追问统一【情报汇整】/【行动预案】；`nexus` → `intent_gateway` 可配置嗅探开关/预算/tracker/DAG 审计
- **安全锁体系**：`jachin_safety_lock.py` 按需域注入、pending + CLI 审批、`core:safety_lock_list_pending` / `remove`；`jachin_safety_lock_admin.py`；文档 `JACHIN_SAFETY_LOCK*.md`、`JACHIN_SAFETY_LOCK_REMEDIATION.md`
- **`run_agent`**：`gateway_workspace_dir` 与 `implicit_attribution` 工作区键；HTTP `agent/run` 支持 `gateway_workspace_dir` / `git_workspace_dir`
- **文档**：`INTENT_GATEWAY_CONTEXT_SNIFFER_AND_TRANSPARENCY.md`、四大原语与 L3 意图/记忆/限制类多篇；**单测** `test_intent_gateway`、`test_context_sniffer`、`test_gateway_pipeline_sniffer`、`test_jachin_safety_lock`、`test_planning_gate_phase` 等

### Changed

- **版本号**：`core/main.py`、`core/sync_daemon.py`、`core/cli.py`、`cli/jachin_cli` 统一为 **0.8.99**（Git 标签 `v0.8.99`）
- **L3 与 Core**：`agent_core` 网关流水线与 prompt 组装、MCP/原生工具与插件路径、文档与规则与仓库树大量对齐（含 skills 迁至 `skills_repo`、BI/HR 脚本与插件增量）

---

## [v0.8.97] - 2026-03-30

### Added

- **L1↔L2 网关配对（主路径）**：① L1 `POST /api/v1/l2-gateway/verify-credentials`；L2 `POST /api/v2/admin/login`（用户名为邮箱时）写 `nexus_config` 并 **热启** L1 心跳与 CloudSync。② L1 `POST /api/v1/l2-bridge/mint|redeem`、`/console/l2-bridge`；L2 `GET /api/v2/admin/l1-bridge-config`、`POST /api/v2/admin/redeem-l1-bridge` 与 `/gateway/l1-bridge-callback.html`；`L2_BRIDGE_ALLOWED_RETURN_PREFIXES`。③ CLI 6 位码与 `pairing/*` 为**无头/恢复辅助**。控制台诊断前缀 **`[L1↔L2 Pairing]`**（`core/l2_pairing_diagnostics.py`）。文档 `docs/L1_L2_PAIRING_AND_WEB_BRIDGE.md`
- **L3_LOCAL MCP 轻量 stdio 制品**：`plugin.json` 支持 `stdio_server`（command/args/env），L3 `l3_packaged_stdio_mcp.register_l3_packaged_stdio_mcps` 在 `mcp_stdio_bootstrap` 中注入 `MCPManager`；`jachin pack` 对 `L3_LOCAL` 校验「`stdio_server` 或 `tools[]` 二选一」。示例 `docs/examples/l3_local_stdio_mcp.plugin.json`，规范 `docs/SKILL_MCP_UPLOAD_SPEC.md` §2.3
- **MCP（Filesystem 上架包）**：`skills_repo/plugin/com.jachin.mcp.filesystem_workspace` — `L2_GATEWAY` 官方 `@modelcontextprotocol/server-filesystem`，根目录占位符 `__JACHIN_WORKSPACE__`
- **脚本**：`scripts/sideload_mcp_filesystem_workspace.ps1`（侧载 inventory）、`scripts/test_mcp_l2_filesystem.ps1`（本机/远程探测 `GET/POST /api/v2/mcp/*`）

### Changed

- **版本号**：`core/main.py`、`core/sync_daemon.py`、`core/cli.py`、`cli/jachin_cli` 统一为 **0.8.97**（Git 标签 `v0.8.97`）
- **文档**：MCP 规格升至 ARCHITECTURE_L3 **v0.4**、MCP_EXECUTION_MODEL **v2.2**；删除根目录重复的 `PROJECT_STRUCTURE.md`（结构以 `docs/FILE_STRUCTURE.md` 为准）；`DIRECTORY_TREE.txt` 同步

---

## [v0.8.50] - 2026-03-16

**Milestone: DeepBrain（深脑）** — 产品基线与 Git 标签 `v0.8.50` 对齐。

### Added

- **智能化 / 记忆**：L3 本地记忆与 `core:local_memory_search`；混合检索 **MMR**、`memory_scoring`、`GET /api/v2/memory/search?explain=true`；隐式学习（`intelligence_implicit` / `intelligence_implicit_embedding`、`implicit_turn_attribution`、`POST /api/v2/intelligence/implicit-signal`）、`intelligence_e` 消费；文档 `INTELLIGENCE_UPGRADE_OVERVIEW`、`JACHIN_VS_OPENCLAW_*`、`IMPLICIT_SIGNALS`、`MEMORY_SCORING`、`ORCHESTRATION_ARCHITECTURE` 等
- **任务范式与编排**：`intelligence_b_execution`、`task_plan_policy`、`workflow_spec_runner`（持久化 DAG / resume）、`l3_node/orchestration/`、`core:apply_patch` / 回滚、`shell_hitl` 等
- **规则**：`.cursor/rules/078-intelligence-roadmap-and-hr-data.mdc` 等；配置规范统一为 `075-config-root-and-cloud-sync.mdc`（移除旧 `(1)` 文件名）

### Changed

- **版本号**：`core/main.py`（FastAPI）、`core/sync_daemon.py`、`core/cli.py`、`cli/jachin_cli` 统一为 **0.8.50**
- **文档与仓库**：HR 单一事实来源 `docs/HR_RECRUITMENT.md`；多份架构 / 部署 / 技能文档与插件路径对齐

---

## [v0.8.5] - 2026-03

### Added

- **L1-L2 凭证溯源**：CLI 配对将 6 位码写入 L2 默认子账号 `l1_pairing_code`；Web 绑定写入 `web`（见后续 Web Bridge 变更）

### Changed

- 版本号统一更新至 v0.8.5
- **文档整理**：移除过时智能化/招聘长文（`MEMORY_IMPROVEMENTS_*`、`HR_RECRUITMENT_DECOUPLE_*`、`hr_decouple_inventory`、`L3_RECRUITMENT_BUILD_SPEC`、`WEEKLY_DEV_LOG_20260317`）；新增单一事实来源 [docs/HR_RECRUITMENT.md](docs/HR_RECRUITMENT.md)，`docs/README.md` 增加「智能化与招聘」索引
- **文档对齐（智能化 + HR）**：更新 `JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md`（P0/DAG/任务持久现状）、`ARCHITECTURE.md`、`INTELLIGENCE_UPGRADE_OVERVIEW.md`（§1.5 HR 物理绑定）、`README_DEPLOY.md`、`LARK_NO_REPLY_TROUBLESHOOTING.md`、`l3_node/README.md`、`docs/FILE_STRUCTURE.md`、根 `README.md`；插件侧 `skills_repo/plugin/README.md`、`INTEGRATION_CORE.md`；`skills_repo/hr-recruitment/README.md`；`.cursor/rules/077-skill-mcp-dependency.mdc`

---

## [v8.0] - 2026-02 (The Singularity OS)

### Added

- **全链路 runId 追踪 (Distributed Tracing)**：每次用户请求注入唯一 run_id，贯穿 SensoryInputEvent → PipelineContext → SensoryOutputEvent，日志染色 `[RunID: xxx]`
- **流式神经 (Streaming Chunk)**：LLM 逐 token 流式输出，`generate_response_stream` + `on_chunk` 回调，caps 含 `stream_chunk` 的客户端实时接收
- **Session Multiplexing**：按 session_id 隔离 Agent Actor，多用户/多路输入零串话
- **Nexus Hook Pipeline**：Koa.js 风格洋葱中间件，5 个生命周期 Hook
- **Dream Weaver Consolidation**：LanceDB 记忆聚类/去重/融合，is_consolidated + 冲突消解
- **Capability Negotiation**：Layer 3 Manifest 握手，按 caps 动态推送
- **Edge Mesh Swarm**：同网设备算力协同，heavy_tools 外包至虫群节点

### Changed

- 白皮书、规格、`.cursor/rules` 全面统一至 v8.0 架构
- 移除所有 v3/v5/v6/v7 版本引用，项目完全统一到 v8.0

---

## [v0.6.1] - 2026-02-28

### Removed

- **废弃 Dapr**：移除 `core/dapr/`、`dapr/`、`clients/desktop/src-tauri/src/dapr.rs` 及 Dapr 相关脚本
- **废弃 Ray Cluster**：移除 `core/brain/ray_cluster/` 全部模块
- **废弃旧 memory 架构**：移除 `core/memory/`（schema、lancedb_store、embedding 等），由 SQLite + 生物学记忆取代
- **废弃过时文档**：移除 ARCHITECTURE_DESIGN_SPEC、DAPR_GUIDE、LAYER1/LAYER2 旧设计、MICROKERNEL、NEXUS_DAEMON、RAG_ARCHITECTURE、VOICE_GUIDE 等 30+ 旧文档
- **废弃臃肿脚本**：移除 setup.ps1/sh、start-full.ps1/sh、dapr_restart_scheduler.ps1

### Changed

- 以当前版本为准，远程与本地完全同步
- 白皮书、规格、rules 已全面更新至 v6.0 架构

---

## [v0.6.0] - 2026-02-28

### Added

- **四大原语执行面**（当时文档称「双轨制/三轨道」，现已统一术语，见 `docs/Jachin 视角的「四大原语」终极架构规范.md`）
  - **MCP**：Model Context Protocol 宿主，继承全球 AI 工具生态，开箱即用
  - **Skills**：SKILL.md 声明式技能，`skills_repo/` 热加载，零编译
  - **Tools · jpp**：The Abyss Wasm 沙箱，商城第三方付费插件，零信任
- **量子记忆 (Quantum Memory)**
  - Vector SQLite (sqlite-vss/lancedb) 扩展，百万级 Token 语义检索
  - 自我修复 (Self-Healing)：工具报错时自动重试，梦境阶段生成 bug_fix 规则
- **生物钟主动心跳 (cron_thinker)**
  - 脱离云端，每 30 分钟主动环顾
  - 扫描系统日志、未读邮件，异常时 IM 推送报警
  - 支持 HEARTBEAT.md 式任务清单
- **全息感知器官 (Jarvis Protocol)**
  - Universal Message Adapter：全渠道 Webhook 统一适配（Discord、Slack、WhatsApp、iMessage 等）
  - Voice Wake (Hey Jachin)：Porcupine/Snowboy 唤醒词 → Whisper STT → Agent → TTS 播报
  - jachin-cli：`pair`、`shell` 极客终端入口
- **文档与规范**
  - 白皮书升级至 v6.0 (The Neural Bus Edition)
  - 新增 `docs/MCP_SPEC.md`、`docs/SKILL_MD_SPEC.md`
  - 更新 `docs/JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md`、P0、VOICE、IM_GATEWAY 等规格
  - `.cursor/rules/*.mdc` 全面同步 v6.0 架构

### Changed

- Layer 2 定位由「边缘守护引擎」升级为「神经中枢总线 (Neural Bus)」
- 技能体系由单一 JPP Wasm 扩展为 **MCP + Skills(SKILL.md) + Tools(jpp)**（四大原语）
- 记忆系统由生物学梦境扩展为量子记忆（向量 + 自我修复）
- 主动能力由纯 10s 心跳拉取扩展为 cron_thinker 生物钟 + 云端心跳

---

## [v0.5.7] - 2026-03-03

### Added

- **WASI 经脉打通**：`core/wasm_runner.py` 支持 stdin/stdout 协议
  - `run_plugin_wasi(wasm_path, stdin_str)`：WASI 模式执行 Python (py2wasm) 插件
  - `run_plugin(..., stdin_json=...)`：传入 stdin_json 时自动走 WASI 模式
- **战役 3：JPP Python SDK**（jachin-plugin-sdk-python）
  - `@jachin_plugin` 装饰器、stdin/stdout JSON 协议
  - 示例：fetch_crypto_price（加密货币价格）
  - plugin.json：royalty_fee、schema（input/output）
  - py2wasm 编译、Makefile、build.ps1

### Changed

- **文档更新**：LAYER2_AGENT_LOOP、JMP_SPEC、REVENUE、ECOSYSTEM、BATTLE_PLAN、NEXUS_DAEMON、PLUGIN_SECURITY_SANDBOX、core/README、TECHNICAL_SPECIFICATIONS 同步 JPP Python SDK、WASI、memory.db、Mock 工具等

### Removed

- **core/MVP_CHECKLIST.md**：过时（引用不存在的 backend/ 路径）

---

## [v0.5.6] - 2026-02-28

### Added

- **生物学记忆管线 (Biological Memory Pipeline)**
  - `core/biological_memory.py`：海马体 (short_term_logs) + 大脑皮层 (core_memory)，SQLite 极简存储
  - `core/dreamer.py`：梦境引擎，凌晨 3 点对短期日志执行 LLM 提纯，遗忘无用内容
  - Agent Loop 集成：每次交互写入短期记忆，System Prompt 注入核心记忆
  - Daemon 调度：dream_scheduler_loop 与心跳并行，每日 3:00 触发梦境
- **进化战役三：JPP 开发者脚手架**
  - `jachin-plugin-sdk/`：Rust 模板，plugin.json、Makefile、标准 ABI
  - 示例：智能灯泡、数据清洗
  - README：3 步入门、分润说明、煽动式文案

---

## [v0.5.5] - 2026-02-28

### Added

- **进化战役二：IM 网关（Telegram / 飞书）**
  - 数据库：`edge_agents.im_binding_id`、`im_platform`，`agent_message_queue` 表
  - Webhook：`POST /api/v1/webhooks/telegram` 接收 Telegram 消息，插入队列
  - 心跳扩展：返回 `task`、`pending_message_ids` 供边缘 Agent 拉取
  - 结果 API：`POST /api/v1/agents/result` 接收执行结果，推回用户手机
  - 绑定 API：`POST /api/v1/agents/bind-im` 将 Agent 与 Telegram chat_id 绑定
  - Layer 2 daemon：消费 task，执行后调用 result API
- **文档**：`docs/IM_GATEWAY_SPEC.md`

---

## [v0.5.4] - 2026-02-28

### Added

- **进化战役一：Agent Loop 与自主执行**
  - `core/agent_memory.py`：持久化记忆（add_memory, get_context），SQLite/JSON 存储
  - `core/agent_loop.py`：ReAct 代理循环（Thought → Action → Observation），LLM + Wasm 技能
  - 蓝图重定义：Persona & Skillset，Processor 节点 = Wasm 技能武器，由 Agent 按需调用
- **文档**：`docs/LAYER2_AGENT_LOOP_DESIGN.md` 完整架构说明

### Changed

- `core/daemon.py`：心跳收到蓝图后，喂给 AgentLoop.run() 自主执行，不再机械执行 Trigger→Processor→Action
- 心跳 API 支持扩展 `task`/`message` 字段，作为 Agent 用户输入
- 文档更新：plugins/README、scripts/README、NEXUS_DAEMON、LAYER1_ARCHITECTURE、docs/README

---

## [v0.2.0] - 2026-02-12

### Added

- **控制台 HUD API**：思维流日志、建议、记忆搜索、模型列表与切换
- **配置 API**：`/api/v3/config` 供 Horizon 显示环境与模型
- **技能权限字段**：manifest 中 `permissions` 支持 LiveTile 悬停展示
- **Dapr 部署适配**：`start.ps1` 支持 placement/scheduler 地址配置，适配本地/云/多级部署

### Changed

- ConsoleLayout：Void 节点数由记忆数驱动，Horizon 从后端 config 获取 environment/model
- DAPR_GUIDE：新增 Placement 与 Scheduler 地址配置文档

### Fixed

- Dapr scheduler 连接超时：显式指定 `localhost:6060` 避免 mDNS 返回容器内网 IP

---

## [v3.2] - 2026-02-03

详见 [docs/whitepaper_v3.2_final.md](docs/whitepaper_v3.2_final.md)
