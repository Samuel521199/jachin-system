# 桌面精灵模式使用指南

## 概述

Jachin Desktop Client 采用"桌面精灵"模式，类似于瑞星小狮子或 Clippy，是一个始终置顶的透明窗口，显示动画角色。

## 快速开始

### 1. 安装依赖

```bash
cd clients/desktop
npm install
```

这将安装：
- React + TypeScript
- Rive 动画引擎 (`@rive-app/react`)
- Tauri API (`@tauri-apps/api`)

### 2. 准备 Rive 动画文件（可选）

如果没有 Rive 动画文件，应用会显示占位符（🤖 emoji）。

要使用动画：
1. 使用 Rive Editor 创建动画：https://rive.app/
2. 导出为 `.riv` 文件
3. 放到 `public/jachin_sprite.riv`
4. 确保状态机包含：Idle, Listening, Thinking, Speaking

### 3. 启动开发

```bash
# 确保后端已启动
cd ../..  # 回到项目根目录
.\start.bat

# 启动桌面精灵
cd clients/desktop
npm run tauri:dev
```

## 窗口说明

### 精灵窗口 (Sprite Window)

- **尺寸**: 200x200 像素
- **特性**: 透明、无边框、始终置顶
- **功能**: 显示动画角色
- **交互**:
  - 左键拖动：移动位置
  - 双击：打开对话窗口
  - 右键：打开对话窗口（临时）

### 对话窗口 (Chat Window)

- **尺寸**: 400x300 像素
- **特性**: 透明背景、无边框、始终置顶
- **功能**: 对话输入和输出
- **位置**: 自动附着在精灵窗口旁边

### 系统托盘

- **图标**: 显示在系统托盘
- **左键点击**: 显示/隐藏精灵窗口
- **右键点击**: 显示菜单（待实现）

## 交互方式

### 基本操作

1. **移动精灵**: 左键按住精灵拖动到任意位置
2. **开始对话**: 双击精灵打开对话窗口
3. **发送消息**: 在对话窗口输入消息，按 Enter 发送
4. **关闭对话**: 点击对话窗口右上角的 X

### 动画状态

- **Idle (待机)**: 默认状态，播放呼吸动画
- **Listening (倾听)**: 用户双击后，等待输入
- **Thinking (思考)**: AI 正在处理请求
- **Speaking (说话)**: AI 正在回复

## 开发模式 vs 生产模式

### 开发模式

- 使用 Vite 开发服务器 (`http://localhost:1420`)
- 热重载支持
- 显示调试信息

### 生产模式

```bash
npm run tauri:build
```

构建产物在 `src-tauri/target/release/` 目录。

## 故障排除

### 问题1: 窗口不透明

**解决方案**: 检查 `tauri.conf.json` 中 `transparent: true`

### 问题2: 无法拖动

**解决方案**: 确保元素有 `data-tauri-drag-region` 属性

### 问题3: Rive 动画不显示

**解决方案**: 
1. 检查 `public/jachin_sprite.riv` 文件是否存在
2. 检查状态机名称是否匹配
3. 查看浏览器控制台错误信息

### 问题4: 对话窗口无法打开

**解决方案**:
1. 检查 Rust 代码中的窗口管理逻辑
2. 查看 Tauri 控制台错误信息
3. 确保窗口标签正确（"chat"）

## 下一步开发

- [ ] 创建 Rive 动画文件
- [ ] 实现右键菜单
- [ ] 添加鼠标跟随效果
- [ ] 实现语音输入
- [ ] 实现语音输出（TTS）
- [ ] 优化窗口位置同步
- [ ] 添加更多动画状态

## 相关文档

- [SPRITE_ARCHITECTURE.md](./SPRITE_ARCHITECTURE.md) - 架构详细说明
- [public/README_RIVE.md](./public/README_RIVE.md) - Rive 动画说明
- [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) - 故障排除指南
