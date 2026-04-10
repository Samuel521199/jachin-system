# Skill 可视化 UI 展示模式（桌面端）

本文档说明 **Jachin 桌面端**（`clients/desktop`）中，与 **Tool Call** 绑定的 Skill 生成式 UI 的三种展示路径，供后续做 Skill 时在 **纯文本 / 内嵌 / 右侧画布** 之间做产品选型。

> **与四大原语的关系**：这里的 UI 仍属于 **Tools（原子工具）** 的交互壳；`tool_ui_result` 经 L3 WebSocket 回传执行结果。详见仓库内「四大原语」规范，勿与 **Skills（SKILL.md 知识包）** 本体混称。

---

## 1. 三种展示路径总览

| 模式 | 用户看到什么 | 何时出现 | 前端配置要点 |
|------|----------------|----------|----------------|
| **A. 纯文本（默认）** | 助手气泡里只有 Markdown/纯文本；或「正在执行工具…」轻量占位 | 消息带 `tool_call`，但 **未** 在注册表中配置可视化组件 | 无需注册；由 `AssistantMessageContent` 走默认分支 |
| **B. 内嵌（Inline）** | 交互面板 **嵌在聊天气泡内**（与对话流同一列） | 消息带 `tool_call`，且注册表 `displayMode: "inline"` | `SKILL_UI_REGISTRY` + `displayMode: "inline"` |
| **C. 右侧画布（Canvas）** | 左侧气泡仅 **短提示**；**同一块** 表单在 **右侧独立栏**（与 OMNI 双栏并排） | 消息带 `tool_call`，且注册表 `displayMode: "canvas"` | 同上 + `displayMode: "canvas"`；宿主需为 Omni 窗口扩宽（Tauri 命令） |

**同一套 React 组件** 可同时支持内嵌与画布：通过 Props **`layout: "inline" | "canvas"`** 区分排版（由宿主注入，见 `SkillUiPanelProps`）。

---

## 2. 行为与数据流（共同前提）

1. **触发条件**：助手消息 `StoredMessage` 上存在 **`tool_call`**，且 **`resolved === false`** 时，才渲染上述 B/C 的自定义 UI；完成后由后端或前端将 `resolved` 置为 `true` 并写入正文等。
2. **工具名对齐**：注册表 key 为 **小写裸名**（`normalizeSkillToolName` 会去掉 `core:` 前缀）。例如 `core:compose_essay` 与 `compose_essay` 均映射到 `compose_essay`。
3. **提交回传**：用户在面板内点击确认后，组件调用 **`onToolResponse(result)`**，上层经 **`sendToolUiResult` / `tool_ui_result`** 发往 L3，由 `l3_node` 执行对应 Native 工具并把结果写回会话。

---

## 3. 模式 A：纯文本（无自定义面板）

- **适用**：不需要表单、仅由模型在气泡里说明即可；或工具尚未接前端面板。
- **实现**：**不要**在 `SKILL_UI_REGISTRY` 中注册该工具名。
- **表现**：未注册时，未解决的 `tool_call` 会走 **LegacyToolCallPlaceholder**（转圈 +「正在执行工具」类文案），不渲染 Markdown 正文，避免与后台状态冲突。

---

## 4. 模式 B：内嵌（Inline）

- **适用**：轻量选择（如模版、少量选项），希望 **不离开对话流**、不占用右侧整栏。
- **示例（当前仓库）**：`generate_ppt` → `PptGeneratorUI`，`displayMode: "inline"`。
- **表现**：面板组件直接渲染在 **助手气泡内**（`AssistantMessageContent`），`layout` 为 **`inline`**。

**注册示例**（概念）：

```ts
// clients/desktop/src/skills-ui/skillUIRegistry.tsx
some_tool: { component: SomeToolUI, displayMode: "inline" },
```

---

## 5. 模式 C：右侧画布（Canvas）

- **适用**：表单字段多、需要 **Artifacts 式** 独立工作区，与左侧对话 **并排**。
- **示例（当前仓库）**：`compose_essay` → `EssayWritingUI`，`displayMode: "canvas"`。
- **表现**：
  - **左侧**：同一条助手消息在气泡内只显示 **简短提示**（右侧画布引导文案），不重复渲染整表。
  - **右侧**：`SkillCanvasPane` 挂载 **同一组件**，并传入 **`layout="canvas"`**（通常更宽 padding、文案提示「写入左侧气泡」等）。
  - **窗口**：Omni 通过 Tauri **`expand_chat_window_for_skill_canvas`** 保证宽度足够，避免默认窄窗下右栏宽度为 0。

**注册示例**（概念）：

```ts
some_heavy_tool: { component: SomeHeavyUI, displayMode: "canvas" },
```

---

## 6. 新增一种 Skill 可视化时的检查清单

1. **后端 / L3**：助手消息需带上 **`tool_call.name` / `args` / `id`**（与 Native 工具名一致）。
2. **前端注册**：在 **`clients/desktop/src/skills-ui/skillUIRegistry.tsx`** 增加一条 `SKILL_UI_REGISTRY`。
3. **组件**：实现 **`SkillUiPanelProps`**，对 **`layout`** 做内嵌/画布样式分支（若两种都要）。
4. **选模式**：在注册表中选 **`inline`** 或 **`canvas`**（见上表）。
5. **联调**：确认 **`tool_ui_result`** 路径与 `findUnresolvedToolCallMessageIndex` 能匹配 `tool_call_id` 或工具名。

---

## 7. 代码与文档索引（SSOT）

| 说明 | 路径 |
|------|------|
| 注册表 + `displayMode` | `clients/desktop/src/skills-ui/skillUIRegistry.tsx` |
| 类型与 `SkillUiPanelProps` | `clients/desktop/src/skills-ui/types.ts` |
| 气泡内分发（纯文本 / inline / canvas 占位） | `clients/desktop/src/components/Chat/AssistantMessageContent.tsx` |
| 右侧画布容器 | `clients/desktop/src/skills-ui/SkillCanvasPane.tsx` |
| 当前活跃画布（从消息推导） | `clients/desktop/src/skills-ui/canvasState.ts` |
| Omni 双栏 + 扩窗调用 | `clients/desktop/src/chat.tsx`、`clients/desktop/src/skills-ui/skillCanvasWindow.ts` |
| Tauri 扩窗/还原 | `clients/desktop/src-tauri/src/main.rs`（`expand_chat_window_for_skill_canvas`：按 `scale_factor` 将 **逻辑** 最小总宽换为物理像素，避免高 DPI 下仍过窄） |
| SKILL.md 规范（知识包，非本 UI 注册表） | `docs/SKILL_MD_SPEC.md` |

---

## 8. 修订记录

- 初版：记录 inline / canvas / 纯文本三种路径及注册方式，与桌面端当前实现对齐。
- 扩窗：Rust 侧使用 **逻辑最小总宽 × `scale_factor`** 换算物理宽度，避免高 DPI 下仍需手动拖窗才能看到右栏。
