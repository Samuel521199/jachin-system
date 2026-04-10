# 能力域：Office PowerPoint MCP（PPTX）

**域 id**: `office_powerpoint_mcp`  
**对应 MCP 包**: `com.jachin.mcp.office_powerpoint`（`skills_repo/plugin/com.jachin.mcp.office_powerpoint`）

上游实现：python-pptx + MCP stdio，工具名示例：`create_presentation`、`save_presentation`、`add_slide`、`apply_professional_design` 等（以 `tools/list` 为准）。

<!-- PROMPT_INJECT_OFFICE_POWERPOINT_START -->

### 【域自检 · PowerPoint / PPTX】

若「可用工具」中出现 `mcp:create_presentation`、`mcp:save_presentation` 等，则 **PPTX 编辑 MCP 已在本机连接**，你必须通过 **Thought / Action / Action Input** 调用这些工具落实用户意图。

- **禁止**声称「没有 MCP 连接」「无法访问文件系统」「只能给 Python 脚本代替」——只要上文明列了上述工具 id，就必须用工具创建或编辑 PPT，不得用纯文本冒充已执行。
- **最短闭环**：`mcp:create_presentation`（或等价工具名，与列表 id 完全一致）→ `mcp:add_slide` 等填标题 → `mcp:save_presentation`，`file_path` 用**绝对路径**（Windows 如 `C:\Users\某用户\.jachin\workspace\xxx.pptx`）；勿把未展开的 `~` 直接当路径传给工具，除非 schema 明确支持。
- 创建/打开后务必用返回的 `presentation_id` 贯穿后续步骤。
- 模板目录由 `PPT_TEMPLATE_PATH` 控制；可先 `list_slide_templates` / `get_template_info` 再套用。
- 参数以各工具 schema 为准；缺参时简短问用户或先列幻灯片再执行。

<!-- PROMPT_INJECT_OFFICE_POWERPOINT_END -->
