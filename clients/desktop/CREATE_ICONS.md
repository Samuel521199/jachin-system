# 创建应用图标

## 快速方法：使用 Tauri CLI（推荐）

如果你有一个 PNG 图片（建议 1024x1024 或更大），可以使用 Tauri CLI 自动生成所有平台所需的图标：

```bash
# 1. 准备一个 PNG 图片（例如：icon.png，建议 1024x1024）
# 2. 运行以下命令
npx tauri icon path/to/your-icon.png

# 这会自动生成所有需要的图标文件到 src-tauri/icons/ 目录
```

## 手动创建（临时方案）

如果暂时没有图标，可以：

1. **创建简单的占位图标**：
   - 使用在线工具创建 32x32 PNG
   - 转换为 ICO 格式（Windows）
   - 保存到 `src-tauri/icons/` 目录

2. **使用在线工具**：
   - https://convertio.co/png-ico/
   - https://www.icoconverter.com/

3. **临时禁用 bundle**（开发模式）：
   - 已在 `tauri.conf.json` 中设置 `"active": false`
   - 这允许开发模式运行，但生产构建时需要图标

## 生产构建前

在生产构建前，请确保：
1. 创建专业的应用图标
2. 使用 `npx tauri icon` 生成所有平台图标
3. 在 `tauri.conf.json` 中设置 `"bundle": { "active": true }`
