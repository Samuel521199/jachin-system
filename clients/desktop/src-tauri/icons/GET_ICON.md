# 快速获取图标文件

## 最简单的方法（推荐）

1. **访问在线 ICO 转换器**：
   - https://www.icoconverter.com/
   - 或 https://convertio.co/png-ico/

2. **创建图标**：
   - 上传任意图片（或使用默认图片）
   - 选择尺寸：32x32（或包含多个尺寸）
   - 下载生成的 `icon.ico`

3. **保存文件**：
   - 将下载的 `icon.ico` 文件保存到当前目录（`src-tauri/icons/`）

## 使用 Tauri CLI（如果有图标 PNG）

```bash
# 如果你有一个 PNG 图片（建议 1024x1024）
npx tauri icon path/to/your-icon.png
```

这会自动生成所有需要的图标文件。

## 临时解决方案

如果只是想快速开始开发，可以：

1. 创建一个简单的 32x32 纯色图片
2. 使用在线工具转换为 ICO
3. 保存为 `icon.ico`

## 验证

创建图标后，重新运行：

```bash
npm run tauri:dev
```
