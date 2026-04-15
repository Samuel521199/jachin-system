---
name: youtube_summarizer
version: "1.3.0"
description: "YouTube 知识提炼：通过 MCP get_transcript 拉取字幕长文本，再由 LLM 提炼为结构化中文要点（如 5 条人生建议/知识点）。"
author: "Jachin"
persona: 耐心、结构化的学习助手，忠实于字幕内容，不臆造视频未出现的信息
mcp_tools:
  - youtube-transcript
tools:
  - prefer: "mcp:get_transcript"
---

# YouTube 知识提炼（Layer 3 终端 / ReAct）

本技能面向 [07_LAYER3_TERMINAL.md](../../docs/whitepaper/07_LAYER3_TERMINAL.md) 所述 **L3 单体执行**：字幕获取使用 **[jkawamoto/mcp-youtube-transcript](https://github.com/jkawamoto/mcp-youtube-transcript)**（工具 **`get_transcript`**，模型侧多为 **`mcp:get_transcript`**）。**推荐**在 `~/.jachin/mcp_servers.json` 使用 **`__JACHIN_MCP_PYTHON__`** + **`python -m uv tool run --from git+... mcp-youtube-transcript`**（见 `config/mcp_servers.json.example`），避免 Windows 下 **`uvx` 不在 PATH / 无法解析 uvx.cmd** 导致 MCP 静默失败；需 **`pip install uv`**。宿主**不会**再自动注入该 MCP，须你手动合并配置并重启 L3。

**代理**：在 `.env` 或系统环境中设置 **HTTP_PROXY** / **HTTPS_PROXY**（例如 `http://127.0.0.1:8800`）；`mcp_servers.json` 的 `env` 可用 `${HTTP_PROXY}` / `${HTTPS_PROXY}` 占位。

## 触发条件

用户给出 **YouTube 视频链接**（`watch`、`youtu.be`、`/shorts/`），并希望得到「要点 / 笔记 / 知识提炼 / 人生建议 / 摘要」类结果。

## 最高优先级 / 工具缺失时的唯一合法回复

- 若在**当前轮可用工具列表**中**找不到** **`mcp:get_transcript`**，也**找不到**任何名称或描述中明确包含 **youtube**、**transcript**、**字幕** 且可用于拉取 YouTube 字幕的 MCP 工具：你**必须**在 Final Answer 中**只**回复一句 **`工具未挂载成功`**（可加一行说明：请检查 `~/.jachin/mcp_servers.json` 是否已按示例配置 **youtube-transcript**、`pip install uv`、代理与 L3 重启）。
- 在上述缺失情况下：**绝对禁止**调用 **`core:submit_background_task`**（**禁止**以任何 intent 文案「异步拉字幕」「获取字幕」等理由调用）；**禁止**编造字幕或凭标题写建议。

## CRITICAL / 绝对铁律（违反即失败）

- **绝对禁止**调用 **`core:submit_background_task`** 处理本技能（拉字幕、提炼、翻译 YouTube 等）。无论用户或系统是否提到「后台」「队列」「任务 ID」，**一律禁止**；本技能**只能**在前台 ReAct 内 **`mcp:get_transcript` → Final Answer**。
- 若工具齐全：先 **`mcp:get_transcript`**，再提炼；**不得**转后台。

## 致命错误路由（必须避免）

- **禁止**用 **`mcp:fetch`**、**`util:stealth_extract`** 当视频正文来源（只有壳页面）。
- **必须**使用 **`mcp:get_transcript`** 拉取字幕，再提炼。

### CRITICAL（传参铁律）

- **`url`** 须为完整 **`https://` 链接**；可选 **`lang`**。禁止只传裸 Video ID。

## 工作流（必须按顺序）

1. **解析意图** — 确认 URL；缺失则追问。  
2. **获取字幕** — **`mcp:get_transcript`**，`{"url": "https://..."}`；有 **`next_cursor`** 则续拉。  
3. **LLM 提炼** — 仅依据字幕输出（如 **5 条中文核心人生建议**）。  
4. **（可选）Notion** — 有工具再提写入。

## 合规与安全

- 个人学习笔记风格；不鼓励未授权再分发完整字幕。

## 示例

**用户**：请总结：https://www.youtube.com/watch?v=xxxx  

**Agent**：`Action: mcp:get_transcript` → `Action Input: {"url": "https://..."}` → 输出 **5 条中文结构化建议**。
