# Holographic UI（OmniParser 全息屏幕）

**域**：桌面 / 全屏应用视觉自动化（无 DOM、无 API）
**工具**：`mcp:get_holographic_screen`、`mcp:physical_click`

## 硬约束

- **界面事实**仅以最近一次 `mcp:get_holographic_screen` 的 Verification evidence 为准：JSON `elements` + **带红框编号的标注图**。
- 标注图上的数字 **id** 与 `elements[].id` **一一对应（从 0 起）**。
- **禁止**在未重新 `get_holographic_screen` 的情况下凭记忆断言屏幕内容或坐标。

## WorkOrder / RoleExecutor 闭环（眼 → 脑 → 手）

1. **看**：`mcp:get_holographic_screen`（无参数或按需 `bbox_threshold`）。阅读 `elements` 与标注图，选定目标 `id`。
2. **想**：reasoning note 中写明目标 id、可见文案（`content`）、为何点击。
3. **做**：`mcp:physical_click`，例如 `{"element_id": 12}`；打开桌面快捷方式时常用 `{"element_id": 3, "double_click": true}`。
4. **验**：界面变化后**必须**再次 `get_holographic_screen`，再 final user-facing result。

## Tool Input 示例

```json
{"element_id": 12}
```

```json
{"element_id": 5, "double_click": true}
```

## 故障

- `omniparser_subprocess_exit_*`：先在本机执行 `.\scripts\setup_omniparser_venv.ps1`，并确认 `model/OmniParser-v2.0` 完整。
- `未知 element_id`：界面已变，重新 `get_holographic_screen`。
- 与 OCR 版 `get_parsed_screen` 勿混用编号；全息链路只用 `get_holographic_screen` + `physical_click`。
