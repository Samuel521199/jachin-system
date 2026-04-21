# 工作项：Kalaroko 多轮 E2E 巡检与 L3「巡检中枢」

> **用途**：可粘到周会记录、项目看板、Confluence/飞书 工作说明。  
> **状态说明**：下表「状态」未写时视为**已合入主开发线**；若你本地分支未更新，以 Git 实际文件为准。

---

## 0. 背景（1 项）

| 项 | 内容 | 状态 |
|----|------|------|
| 0.1 | 为 Kalaroko 默认场景提供**多轮 E2E 巡检**；终端/CI 用 Python 脚本，桌面用 L3 控制台 **「巡检中枢」** 拉流，避免**长时测试卡死 UI**；输出**整份 Markdown 报告** + 多轮时 **Qwen 综合分析**。 | 已落实 |

---

## 1. 长任务与「读/等」不阻塞主界面（7 项）

| 项 | 工作说明 | 实现位置 / 验证方式 | 状态 |
|----|----------|----------------------|------|
| 1.1 | 巡检**不占用**渲染主线程：前端用浏览器 **`EventSource`** 收流，**不**用 `await fetch` 等长轮询整包体堵死 React 主循环。 | `MonitorMatrix.tsx` 内 `new EventSource(url)`。 | 已落实 |
| 1.2 | 后端**不**在 HTTP 单请求里同步 `read()` 大文件拖死 worker：E2E 在**子任务**中 `async` 跑，主协程只**从 Queue 取行**写 SSE。 | `l3_node/http_server.py` → `asyncio.create_task` + `line_q`。 | 已落实 |
| 1.3 | 行级进度用 **`put_nowait` + 短 `wait_for`** 泵送，避免无输出时协程**空转占满**；任务结束后再**排空队列**写净。 | 同文件 `_handle_monitor_kalaroko_stream`。 | 已落实 |
| 1.4 | 代理/客户端**长连接保活**：定时写 **`: keepalive`**，降低 Vite/反代对**静默流**断连。 | 同 handler 内 `keepalive_sec = 15.0`。 | 已落实 |
| 1.5 | **超大内容不逐行刷 SSE**：`render_report_md` 仍 `print` 到 stdout，**整份** `markdown_report` 放在 **`done` 一次 JSON**，避免万行 Markdown 把 EventSource 打爆。 | `test_kalaroko_default_scenarios_e2e.py` 返回 `markdown_report`；`http_server` `data: {"type":"done",...}`。 | 已落实 |
| 1.6 | LLM 长文**不**经行 sink 全量重复：正文在 `done.llm_analysis` 与终端 `print`；仅短行 **Progress 进流**。 | 同 E2E 脚本 `_generate_llm_summary` 后说明行。 | 已落实 |
| 1.7 | 开发态**跨域/端口**：Vite **`/l3` → L3 多端口回退**；`getKalarokoMonitorStreamUrl` 与 `getL3LogsStreamUrl` 同一套 base 策略。 | `clients/desktop/src/lib/api.ts`；`vite.config` 代理。 | 已落实 |

---

## 2. E2E 脚本与报告内容（8 项）

| 项 | 工作说明 | 实现位置 / 验证方式 | 状态 |
|----|----------|----------------------|------|
| 2.1 | 抽离可复用入口 **`run_kalaroko_batch_test(..., line_sink=...)`**，供 API/CLI 共用。 | `scripts/test_kalaroko_default_scenarios_e2e.py` | 已落实 |
| 2.2 | 多轮核心在 **`_run_full_cycle`**；每轮 **`render_report_md`** 返回 `str` 并 **累积**到 `markdown_rounds`。 | 同脚本 | 已落实 |
| 2.3 | 成功返回 **`markdown_report`**：各轮用 `\n\n---\n\n` 拼接，含**一至七节**（五节多轮表在 `len(hist)>1` 时逐步变全）。 | 同脚本 return 字典 | 已落实 |
| 2.4 | 多轮且 `len(all_metrics_history)>1` 时调 **`_generate_llm_summary`**，返回 **`llm_analysis`**。 | 同脚本 + `httpx` → DashScope 兼容 `chat/completions` | 已落实 |
| 2.5 | 无 Key 时**不**调 LLM，返回**明确提示**（非空跑成功误报）。 | 同脚本 `if not api_key` 分支 | 已落实 |
| 2.6 | 区域、endpoint、Key 与 L3 主仓一致：经 **`get_dashscope_regional_credentials`**。 | `core/brain/llm/dashscope_regional.py` | 已落实 |
| 2.7 | 行级镜像： **`_e2e_progress` / `_e2e_echo`** 在 `line_sink` 存在时推流。 | 同 E2E 脚本 | 已落实 |
| 2.8 | Playwright 大 JSON **不**进 sink，仅 stdout，防把 SSE/内存顶满。 | 设计约定；`_run_playwright` 仍为 `print` JSON | 已落实 |

---

## 3. L3 HTTP API（6 项）

| 项 | 工作说明 | 实现位置 / 验证方式 | 状态 |
|----|----------|----------------------|------|
| 3.1 | 注册 **`GET /api/v1/monitor/stream`**，Query：`runs`、`interval`、`skip_playwright`。 | `l3_node/http_server.py` `add_get` | 已落实 |
| 3.2 | 动态加载 E2E 脚本（**`importlib`**），仓库根 **`sys.path`**，避免重复维护一套逻辑。 | `_handle_monitor_kalaroko_stream` | 已落实 |
| 3.3 | 正常结束：`done` 载荷 **`markdown_report`、`llm_analysis`、`ok`、`runs`** 等；脚本错误：`type=error`。 | 同上 | 已落实 |
| 3.4 | **`Content-Type: text/event-stream`** + CORS，与既有 `_stream_response` 一致。 | `http_server.py` | 已落实 |
| 3.5 | 早期失败（脚本缺失等）返回 JSON 内含 **`markdown_report: null`**，前端可分支。 | 同上 | 已落实 |
| 3.6 | CLI 不受影响：`python scripts/test_kalaroko_default_scenarios_e2e.py`。 | `main()` → `run_kalaroko_batch_test` | 已落实 |

---

## 4. 桌面控制台前端（7 项）

| 项 | 工作说明 | 实现位置 / 验证方式 | 状态 |
|----|----------|----------------------|------|
| 4.1 | 页面 **`MonitorMatrix`**：**Runs / Interval / 启动**；Mind Stream + **Markdown 报告区** + **Qwen 结论**，顺序固定。 | `clients/desktop/src/console/pages/MonitorMatrix.tsx` | 已落实 |
| 4.2 | **`done.markdown_report`** → `ReactMarkdown` + **`remarkGfm`**（表格渲染）。 | 同上 | 已落实 |
| 4.3 | **`done.llm_analysis`** 单独区块，避免与报告混为一谈。 | 同上 | 已落实 |
| 4.4 | 路由 **`#/monitor`**；侧栏「**巡检中枢**」。 | `routes.tsx`、`Sidebar.tsx` | 已落实 |
| 4.5 | SSE URL：**`getKalarokoMonitorStreamUrl`**（与 L3 列表/日志流同源策略）。 | `clients/desktop/src/lib/api.ts` | 已落实 |
| 4.6 | 新开任务清空报告与日志状态，防止串单。 | `MonitorMatrix.tsx` `handleStart` | 已落实 |
| 4.7 | 可选：大包体 **`done`** 单次 JSON；若日后超限再拆「分块下载」**待办**。 | — | 待评审 |

---

## 5. 环境与密钥（6 项）

| 项 | 工作说明 | 验证方式 | 状态 |
|----|----------|----------|------|
| 5.1 | **`.env`**：`JACHIN_ACTIVE_REGION=CN|SEA`，**取值勿加多余引号**。 | 启动 L3 / 跑一次 E2E tail 日志区域 | 已落实 |
| 5.2 | **`DASHSCOPE_API_KEY_SEA` / `DASHSCOPE_API_KEY_CN`**（可选）+ **`DASHSCOPE_API_KEY` / `QWEN_*`** 回退，与 `dashscope_regional` 一致。 | `docs/DASHSCOPE_REGIONAL_KEYS.md` | 已落实 |
| 5.3 | **`LLM_COMPLEX_MODEL`** 与 E2E 摘要模型对齐（如 `qwen3-max`）。 | `.env` | 已落实 |
| 5.4 | Tauri 子进程白名单注入上述变量（桌面场景）。 | `clients/desktop/src-tauri/src/l3_spawn.rs` | 已落实 |
| 5.5 | Playwright/CDP：按需 **`KALAROKO_CDP_ENDPOINT`** 等，见脚本头注释。 | 本地 Chrome 调试 | 按需 |
| 5.6 | **Lark 群推送（可选）**：`KALAROKO_INSPECT_LARK_WEBHOOK_URL`（或回退 `LARK_WEBHOOK_URL`）；巡检成功后 **`send_markdown` 分条**推送 Markdown + Qwen。 | `.env.example`、`l3_node/channels/lark/kalaroko_inspection_notify.py` | 已落实 |

---

## 6. 联调验收清单（6 项）

| 项 | 验收动作 | 通过标准 |
|----|----------|----------|
| 6.1 | 启动 L3 HTTP（18991 或回退端口）+ 桌面 `npm run dev` / Tauri | `#/monitor` 可打开 |
| 6.2 | 点「启动全链路巡检」 | Mind Stream **持续**有行（非卡死）；结束有 **`done`** |
| 6.3 | 检查 **`done` JSON** | 含 **`markdown_report`**（非空字符串）、多轮含 **`llm_analysis`**（有 Key 时） |
| 6.4 | UI 三块 | 日志 → Markdown 表格 → Qwen 段落 |
| 6.5 | 仅 CLI：`python scripts/...py --runs 2` | 终端见打印报告 + 多轮时 LLM |
| 6.6 | 切换 **`JACHIN_ACTIVE_REGION`** | DashScope endpoint 随区域变化，无 **401 key 错位** |

---

## 7. 风险与后续工作（4 项）

| 项 | 说明 | 优先级 |
|----|------|--------|
| R1 | **`done` 单次载荷过大**（超长 Markdown）：浏览器/反代 Payload 上限；可考虑 **报告下载接口**或 **gzip 分块**。 | 低（现网量级通常可接受） |
| R2 | 巡检**中途失败**：`markdown_report` 可能为 **`null`**；若需「失败前已产出部分报告」需改异常路径收集。 | 按需 |
| R3 | Lark 推送：已接 **Webhook 分条**；若需@人或审批流再扩展。 | 低 |
| R4 | 文档与 **`KALAROKO_WEB_PERF_MONITOR_TDD.md`**：前者管「控制台+SSE+报告交付」，后者管 MCP/Playwright 细则；避免重复维护时二选一引用。 | 文档 |

---

## 8. 关联路径速查

```
scripts/test_kalaroko_default_scenarios_e2e.py   # E2E + markdown_report + LLM
l3_node/http_server.py                           # GET /api/v1/monitor/stream
l3_node/channels/lark/kalaroko_inspection_notify.py
clients/desktop/src/console/pages/MonitorMatrix.tsx
clients/desktop/src/lib/api.ts                 # getKalarokoMonitorStreamUrl
clients/desktop/src/console/routes.tsx
clients/desktop/src/console/Sidebar.tsx
core/brain/llm/dashscope_regional.py
docs/DASHSCOPE_REGIONAL_KEYS.md
```

---

## 9. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04 | 改为工作文件体例：分节「（N 项）」、表格化工作说明与验收；并入流式/非阻塞 7 项。 |
