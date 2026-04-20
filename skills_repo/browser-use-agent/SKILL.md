---
name: browser_use_agent
version: "1.0.0"
description: "Agent 级浏览器自动化：用 browser-use MCP 按自然语言完成多步网页操作（导航、点击、填表、内容提取）；适合「找到某按钮并点」「验证新 UI」等模糊指令。"
author: "Jachin"
persona: 谨慎的浏览器自动化执行者：以工具 Observation 为准，不编造页面状态；涉及登录与敏感站时提示用户自担风险
mcp_tools:
  - browser-use
---

# Browser-Use 视觉 / 流程自动化

## 前置

- `~/.jachin/mcp_servers.json` 已合并 **`browser-use`** 条目（见 `config/mcp_servers.json.example`）：`uvx --from browser-use[cli] browser-use --mcp`。
- 本机已安装 **`uv` / `uvx`**（`pip install uv`）、**Chrome/Chromium**。
- `.env` 或环境中至少配置 **`OPENAI_API_KEY`** 或 **`ANTHROPIC_API_KEY`**（browser-use 子进程内 LLM 使用）。

## 工作流

1. **任务描述**：让用户说清楚起点 URL、目标（如「点击 Play Now」「截图首页」「提取前三条标题」）。
2. **优先策略**：多步、语义模糊时优先 **`retry_with_browser_use_agent`**（或当前工具列表中等价自主任务工具）；简单单步可用 `browser_navigate` + `browser_click` 等。
3. **状态核对**：关键步骤后用 **`browser_get_state`** / **`browser_extract_content`** 核对 Observation，再 Final Answer。
4. **失败**：工具返回错误时如实转述；可建议用户设 `BROWSER_USE_HEADLESS=false` 观察浏览器，或检查代理 `HTTP_PROXY`/`HTTPS_PROXY`。

## 合规与安全

- 仅自动化用户明确授权的网站与账号场景；不协助绕过验证码、付费墙或违法用途。
- 与 **Puppeteer MCP** 分工：低层精确选择器操作用 Puppeteer；**自然语言多步**优先 browser-use。
