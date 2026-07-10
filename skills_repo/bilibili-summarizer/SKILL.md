---
name: bilibili-summarizer
description: 抓取 B 站视频的完整字幕与热门评论，并生成结构化的内容总结。
persona: 忠实于字幕与评论原文、结构化输出中文要点，不臆造视频中未出现的信息
mcp_tools:
  - bilibili-mcp
tools:
  - prefer: "mcp:get_video_info"
  - prefer: "mcp:get_video_comments"
---

# B 站视频总结（L3 / WorkOrder）

本技能依赖 **`~/.jachin/mcp_servers.json`** 中已配置 **`id: bilibili-mcp`** 的 stdio 服务（npm **`@xzxzzx/bilibili-mcp`**）。**Windows** 上请使用 **`cmd.exe` + `/c` + `npx -y @xzxzzx/bilibili-mcp`**，避免裸 `npx` 解析失败；配置示例见仓库 **`config/mcp_servers.json.example`**。修改配置后需**重启 L3**。

**登录凭证（必须）**：该 MCP **不会**仅凭匿名访问稳定拉字幕/评论。须在 **`.env`（仓库根或 `~/.jachin/.env`）** 配置 **`BILIBILI_SESSDATA` / `BILIBILI_BILI_JCT` / `BILIBILI_DEDEUSERID`**（对应浏览器 Cookie **`SESSDATA`**、**`bili_jct`**、**`DedeUserID`**），并在 **`mcp_servers.json` 的 `bilibili-mcp` 条目**里用 **`"env": { "BILIBILI_SESSDATA": "${BILIBILI_SESSDATA}", ... }`** 注入。从扩展导出的 Cookie JSON **常常不含** `SESSDATA`（HttpOnly），须在 **开发者工具 → Application → Cookies → bilibili.com** 手动复制三项 Value。

MCP 暴露的工具名以当前轮 **可用工具列表**为准，常见为 **`get_video_info`**、**`get_video_comments`**（调用时的 **WorkOrder** 多为 **`mcp:get_video_info`** / **`mcp:get_video_comments`**）。

## 触发条件

用户提供 **B 站视频链接**（`https://www.bilibili.com/...`）或 **BV 号**，并希望要「摘要 / 总结 / 笔记 / 知识点」类结果。

## 工具缺失时的合法回复

若在可用工具列表中**找不到**上述 **`mcp:get_*`** 且无任何可替代的同源 B 站信息工具：在 User-facing result 中说明 **`工具未挂载成功`**，并提示检查 **`bilibili-mcp`** 是否已写入 **`~/.jachin/mcp_servers.json`**、Node/npx 是否可用、是否已重启 L3。**禁止**编造字幕或评论。

## 工作流（必须按顺序）

1. **解析输入** — 从用户消息中提取 **完整 URL** 或 **BV 号**（`BV` + 10 位字符）；信息不足则追问。
2. **视频信息与字幕** — 调用 **`mcp:get_video_info`**（或列表中同名的 `get_video_info`），按工具 **inputSchema** 传入 URL/BV；以返回的 **CC 字幕/正文** 作为总结主依据。
3. **（可选）高赞评论** — 调用 **`mcp:get_video_comments`** 获取评论；若需控制成本，**至少取前 3 条高赞**作为视角补充（以工具参数与返回为准）。
4. **结构化输出（中文）** — 仅依据字幕与评论生成：
   - **视频主旨**
   - **核心知识点 / 时间线**（按内容分段或时间戳，若有）
   - **神评论补充**（无评论或工具失败时诚实说明「未获取评论」）

## 合规

个人学习笔记风格；不鼓励未授权再分发完整字幕或批量爬取。

## 示例

**用户**：请总结：https://www.bilibili.com/video/BVxxxxxxxxxx

**Agent**：`mcp:get_video_info` →（可选）`mcp:get_video_comments` → 输出上述三节结构化中文总结。
