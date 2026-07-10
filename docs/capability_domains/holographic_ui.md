# 域：Holographic UI（OmniParser）

**L3 进程内工具**: `mcp:get_holographic_screen`、`mcp:physical_click`
**实现**: `l3_client/local_mcps/holographic_screen_mcp/`
**推理环境**: 默认 `.venv-omniparser`（见 `scripts/setup_omniparser_venv.ps1`）

## 何时使用

用户要在**全桌面**上做视觉定位并物理点击（记事本、资源管理器、无 DOM 客户端等），且工具列表含 **`mcp:get_holographic_screen`** 时，走本域而非猜测坐标。

## SOP

1. 先 `get_holographic_screen` 再 `physical_click`。
2. 仅用返回的 `element_id`（与标注图红框数字一致，**从 0 起**）。
3. 打开桌面程序图标 → `physical_click` 设 `double_click: true`。
4. 界面变化后重新 `get_holographic_screen` 再结论。

## stdio MCP（Cursor 等外部宿主）

```powershell
.\.venv-omniparser\Scripts\python.exe -m l3_client.local_mcps.holographic_screen_mcp.server
```

## 与 Vision UI（OCR）的关系

| 链路 | 眼 | 手 |
|------|----|----|
| OCR 轻量 | `get_parsed_screen` | `click_element` / `type_text` |
| OmniParser 全息 | `get_holographic_screen` | `physical_click` |

同一任务勿混用两套编号。
