# Chat UI 结构、功能与样式规范（重写前总结）

## 1. 入口与载体

- **独立 Chat 窗口**：Tauri 透明窗口，`chat.html` 挂载 `#chat-root`，入口为 `src/chat.tsx`，渲染为单页 `ChatApp`。
- **chat.html**：`html/body/#chat-root` 使用极低不透明背景 `rgba(0,0,0,0.004)`，避免 Windows 透明窗口点击穿透；`#chat-root` 及其 `input/button` 保持 `pointer-events: auto`。

## 2. 功能清单

- 消息列表：展示 user/assistant 气泡，自动滚动到底部。
- 文本发送：输入框 + 发送按钮，Enter 发送；流式回复 + 打字机效果。
- 语音：麦克风开始/停止录音，语音识别后走语音聊天 API，回复可 TTS 播放。
- 意图路由与安全：发送前调用 `routeIntent`；若 COMMAND + 高风险则弹出确认（确认码 Alpha-9），确认后再真正发送。
- 状态：`riskLevel`（safe/warning/danger）控制边框警示；录音/加载/打字状态文案。
- 持久化：`loadMessages`/`saveMessages`（localStorage）。
- 窗口控制：标题栏可拖拽（仅此处 `data-tauri-drag-region`），最小化/关闭（WindowControls）。

## 3. 视觉与样式（全息 MIND STREAM）

- **整体**：毛玻璃底 `rgba(6,14,32,0.42)` + `backdrop-filter: blur(20px) saturate(1.1)`，四角 cyan/violet 小边框，顶底渐变线（cyan/violet）。
- **标题栏**：左侧 Sparkles + "MIND STREAM" 大写，右侧最小化/关闭；仅标题栏可拖拽。
- **消息区**：可滚动；user 气泡靠右、cyan 渐变+边框；assistant 靠左、白/灰透明渐变；最后一条 assistant 打字时带脉冲光标；“处理中...” 加载态。
- **录音状态**：小条文案，错误用红，正常用 cyan。
- **输入区**：Mic 按钮（录音中红色）+ 单行 input（透明底、底线、focus 时 cyan 线动画）+ Send 按钮；主色 cyan，hover 略亮。
- **风险边框**：warning 橙框，danger 红框。
- **收起态**：单行输入栏 + 展开按钮 + Mic + Send，毛玻璃条，左侧小区域可拖拽。

## 4. 重写原则（避免控件无响应）

- 根容器不参与拖拽，不遮挡点击（或根 `pointer-events: none`，内容区 `pointer-events: auto`）。
- **仅**标题栏设置 `data-tauri-drag-region`，按钮区不设。
- 输入区单独一层，使用 `pointer-events: auto`，标准 `onClick`/`onChange`，不在输入区外拦截 mousedown/click。
- 不依赖复杂事件修补（如 onMouseDownCapture、stopPropagation 链），保证输入框与按钮可正常聚焦和点击。
