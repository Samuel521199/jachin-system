# 能力域：Vision UI / 桌面视觉 QA

**域 id**: `ui_qa`
**L3 进程内工具**: `mcp:get_parsed_screen`、`mcp:click_element`、`mcp:type_text`
**Skill**: `l3_node/skills/ui_qa/ui_qa_skill.md`

<!-- PROMPT_INJECT_UI_QA_START -->

### 【域自检 · 桌面视觉 UI 测试】

若「可用工具」中出现 **`mcp:get_parsed_screen`**，则本机已启用**全息神机**视觉坐标解析（OCR 编号 + PyAutoGUI）。

**必须遵守：**

1. **先 `get_parsed_screen` 再操作** — 禁止在未看图的情况下猜测像素或控件 id。
2. **只用返回的 `element_id`（编号）** 调用 `click_element` / `type_text`；编号与标注图 `[N]` 一致。
3. **打开桌面程序**（记事本、浏览器图标等）→ `click_element` 时设 **`double_click": true`**。
4. **中文输入** → 用 `type_text`，不要声称无法输入中文。
5. **禁止**用 `mcp:fetch`、Puppeteer DOM 或 GameQA 浏览器工具替代**纯桌面**任务（除非用户明确要测网页 DOM）。

环境：`pip install pyautogui pyperclip rapidocr-onnxruntime`；可选 `VISION_UI_YOLO_MODEL` 或 `GAMEQA_YOLO_MODEL` 增强检测。

<!-- PROMPT_INJECT_UI_QA_END -->
