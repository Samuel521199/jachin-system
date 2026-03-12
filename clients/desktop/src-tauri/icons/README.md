# Icons Directory

## 开发模式

开发模式下可以使用占位图标。使用 Tauri CLI 生成图标：

```bash
# 从单个 PNG 图片生成所有平台图标
npx tauri icon path/to/your-icon.png
```

## 图标要求

- **Windows**: `icon.ico` (需要包含 16, 24, 32, 48, 64, 256 像素层)
- **macOS**: `icon.icns`
- **Linux**: `32x32.png`, `128x128.png`, `128x128@2x.png`

## 临时解决方案

开发时，可以创建一个简单的 32x32 PNG 图片，然后使用在线工具转换为 ICO 格式。

或者使用 Tauri 的默认图标（如果可用）。
