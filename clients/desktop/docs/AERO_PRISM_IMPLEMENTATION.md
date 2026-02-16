# Aero Prism 风格桌面精灵实现文档

## 概述

本文档描述了 Tier 3 (Desktop Client) 的 UI 重构，实现了 "Aero Prism" 风格的桌面精灵界面。

## 核心特性

### 1. 分离式架构

界面分为两个独立的视觉组件：

- **AvatarContainer** (`components/Sprite/AvatarContainer.tsx`): 精灵形象容器
  - 120x120px 透明容器
  - 支持 Rive 动画或 Emoji 占位符
  - 呼吸光晕效果
  - 双击触发开心动画

- **InputBar** (`components/Input/InputBar.tsx`): 输入胶囊
  - 全圆角胶囊形状（高度 56px）
  - 磨砂玻璃效果 (`bg-black/60 backdrop-blur-xl`)
  - 文本和语音双模式输入
  - 语音输入时显示声波可视化

### 2. 双模拖拽

- **Avatar 区域**: 使用 `data-tauri-drag-region` 拖拽整个窗口
- **InputBar 区域**: 使用 Framer Motion 的 `drag` 在窗口内独立移动

### 3. 磁吸效果

当 InputBar 靠近 Avatar 底部时（距离 < 80px），自动对齐到 Avatar 底部中心，间距 10px。

实现方式：
- 使用 `useEffect` 定期检测 Avatar 和 InputBar 的距离
- 使用 Framer Motion 的 `useMotionValue` 和 `animate` 实现平滑动画
- 使用 Spring 动画（`stiffness: 300, damping: 30`）提供自然的物理反馈

### 4. 多模态输入

#### 文本输入
- 透明输入框，白色文字
- 支持 Enter 键发送
- 发送按钮（紫色背景）

#### 语音输入
- 麦克风按钮：Normal 状态为灰色，Active 状态为红色，带有波纹动画
- 录音时显示声波可视化（5 个跳动的竖条）
- 使用 Web Audio API (`AudioContext`, `AnalyserNode`) 实时分析音频频率
- 录音结束后自动调用 `voiceChat` API

### 5. 右键菜单

**FloatingMenu** (`components/Menu/FloatingMenu.tsx`):
- iOS 风格的毛玻璃效果 (`bg-white/10 backdrop-blur-2xl`)
- 动画展开/收起（使用 Framer Motion）
- 点击外部自动关闭
- 菜单项：
  - 🛍️ Skill Market (技能商城)
  - 👕 Change Skin (更换形象)
  - ⚙️ Settings (设置)
  - 🔌 Plugins (插件管理)
  - 🚪 Quit (退出)

## 技术栈

- **React 18**: UI 框架
- **TypeScript**: 类型安全
- **Tailwind CSS**: 样式系统
- **Framer Motion**: 动画库（用于拖拽、磁吸、菜单动画）
- **Lucide React**: 图标库
- **Tauri v2**: 桌面应用框架
- **@rive-app/react-canvas**: Rive 动画支持（可选）

## 文件结构

```
clients/desktop/src/
├── components/
│   ├── Sprite/
│   │   ├── AvatarContainer.tsx      # 精灵形象容器
│   │   └── AeroPrismSprite.tsx      # 主组件（组装 Avatar + InputBar + Menu）
│   ├── Input/
│   │   └── InputBar.tsx             # 输入胶囊组件
│   └── Menu/
│       └── FloatingMenu.tsx         # 右键菜单组件
├── sprite.tsx                       # 入口文件（已重构）
└── utils/
    └── cn.ts                        # className 合并工具
```

## 窗口配置

更新了 `src-tauri/tauri.conf.json` 中的 sprite 窗口配置：

```json
{
  "label": "sprite",
  "width": 400,
  "height": 300,
  "minWidth": 300,
  "minHeight": 250,
  "maxWidth": 500,
  "maxHeight": 400
}
```

## 依赖安装

需要安装 `framer-motion`：

```bash
npm install framer-motion
```

## 使用说明

1. **启动开发服务器**:
   ```bash
   npm run tauri:dev
   ```

2. **测试功能**:
   - 拖拽 Avatar 区域移动整个窗口
   - 拖拽 InputBar 在窗口内移动
   - 将 InputBar 靠近 Avatar 底部测试磁吸效果
   - 右键点击 Avatar 打开菜单
   - 双击 Avatar 触发开心动画
   - 点击麦克风按钮测试语音输入（需要浏览器权限）
   - 输入文本并按 Enter 发送消息

## 待实现功能

1. **商城入口**: 右键菜单中的 "Skill Market" 需要实现模态浮层
2. **形象选择器**: "Change Skin" 需要实现形象选择界面
3. **设置窗口**: "Settings" 需要实现设置界面
4. **插件管理**: "Plugins" 需要实现插件管理界面
5. **Rive 动画**: 如果有 Rive 动画文件，AvatarContainer 会自动加载
6. **状态同步**: AvatarContainer 的状态视觉需要从 `useSpriteStore` 获取（当前为占位符）

## 设计细节

### 颜色方案

- **主色调**: 紫色 (`purple-500/600`)
- **语音激活**: 红色 (`red-500`)
- **背景**: 黑色半透明 (`black/60`)
- **文字**: 白色 (`white`)
- **占位符**: 灰色 (`gray-400`)

### 动画参数

- **呼吸光晕**: 3 秒循环，scale [1, 1.05, 1]，opacity [0.3, 0.5, 0.3]
- **磁吸动画**: Spring (stiffness: 300, damping: 30)
- **菜单展开**: 0.2 秒，easeOut
- **声波可视化**: 0.1 秒更新频率

## 注意事项

1. **权限**: 语音输入需要浏览器权限（`getUserMedia`）
2. **性能**: 磁吸检测每 100ms 执行一次，如果性能有问题可以调整间隔
3. **窗口大小**: 当前窗口大小（400x300）适合显示 Avatar + InputBar，如果内容增加可能需要调整
4. **拖拽冲突**: InputBar 内部的交互元素（按钮、输入框）使用 `onMouseDown={(e) => e.stopPropagation()}` 防止触发拖拽
