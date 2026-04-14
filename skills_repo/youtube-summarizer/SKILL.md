---
name: youtube_summarizer
version: "1.1.0"
description: "YouTube 知识提炼：用 Python 原生工具拉取字幕长文本，再由 LLM 提炼为结构化中文要点（如 5 条人生建议/知识点）。"
author: "Jachin"
persona: 耐心、结构化的学习助手，忠实于字幕内容，不臆造视频未出现的信息
# 字幕已改为原生 core:youtube_transcript（非 MCP）；mcp_tools 留空以兼容旧解析器
mcp_tools: []
native_tools:
  - core:youtube_transcript
tools:
  - prefer: "core:youtube_transcript"
---

# YouTube 知识提炼（Layer 3 终端 / ReAct）

本技能面向 [07_LAYER3_TERMINAL.md](../../docs/whitepaper/07_LAYER3_TERMINAL.md) 所述 **L3 单体执行**：字幕获取使用 **原生工具** **`core:youtube_transcript`**（`youtube-transcript-api`，见 `l3_node/skills/native_tools/youtube_transcript_tools.py`），**不再依赖** npm `mcp-server-youtube-transcript`（Node 子进程内 fetch 在 Windows/代理环境下易 `fetch failed`）。长篇归纳由本节点 LLM 完成。

**依赖**：`pip install youtube-transcript-api`（已列入 `core/requirements.txt`）。

## 触发条件

用户给出 **YouTube 视频链接**（`watch`、`youtu.be`、`/shorts/`），并希望得到「要点 / 笔记 / 知识提炼 / 人生建议 / 摘要」类结果。

## 致命错误路由（必须避免）

- **禁止**用 **`mcp:fetch`**、**`util:stealth_extract`** 去「抓 YouTube 页面」当作视频内容：返回几乎只有 **标题 + 页脚**，**没有字幕**。
- **必须**使用 **`core:youtube_transcript`** 拉取字幕，再提炼。**禁止**为「省事」改投 **`core:submit_background_task`** 去异步拉字幕（除非用户明确要求异步长任务）；正常对话应 **前台** `core:youtube_transcript` → 再 Final Answer。

### CRITICAL（传参铁律）

- 调用 **`core:youtube_transcript`** 时，**`url` 必须传入完整 `https://` 链接**（`https://www.youtube.com/watch?v=...`、`/shorts/...`、`https://youtu.be/...`）。
- **绝对禁止**只把裸 **Video ID** 当作 `url`；若用户只给 ID，须在模型侧拼成上述完整 URL 再调用。

## 工作流（必须按顺序）

1. **解析意图**  
   确认用户提供的字符串为可识别的视频 URL；若缺失则一句追问。

2. **获取字幕（工具调用）**  
   调用 **`core:youtube_transcript`**，Action Input 为 JSON，例如：`{"url": "https://www.youtube.com/watch?v=xxxx"}`。  
   阅读返回 JSON：`ok` 为真时使用 **`transcript`** 字段全文；失败时根据 **`error`** 向用户说明（无字幕、地区、网络、未安装依赖等），**不要编造**正文。

3. **LLM 提炼**  
   仅以 `transcript` 为依据输出用户要求的条数与体裁（如 **5 条中文核心人生建议**、加粗小标题等）；区分事实与推断。

4. **（可选）Notion**  
   若工具池中存在 Notion 类 MCP，可在完成后询问用户是否写入；**无工具则跳过**。

## 合规与安全

- 输出为用户个人学习笔记风格；不鼓励未授权再分发完整字幕。

## 示例

**用户**：请总结这个视频的人生建议：https://www.youtube.com/watch?v=xxxx  

**Agent**：`Action: core:youtube_transcript` → `Action Input: {"url": "https://..."}` → 根据 Observation 的 `transcript` 输出 **5 条中文结构化建议**。
