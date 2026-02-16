# 桌面精灵架构文档

## 概述

Jachin Desktop Client 采用"桌面精灵"模式，类似于瑞星小狮子或 Clippy，但内核连接着超级大脑（AMD+Nvidia 算力）。

## 核心特性

### 1. 透明窗口 + 多窗口协同

- **精灵窗口**: 200x200 像素，透明背景，无边框，始终置顶
- **对话气泡**: 400x300 像素，临时窗口，附着在精灵旁边
- **系统托盘**: 后台驻留，防止误关

### 2. 交互方式

- **拖拽**: 左键按住拖动精灵位置
- **双击**: 打开对话气泡窗口
- **右键**: 显示功能菜单
- **悬停**: 精灵看向鼠标位置（未来实现）

### 3. 动画状态机

使用 Rive 动画引擎，支持以下状态：

```
Idle (待机)
  ↓ 用户双击
Listening (倾听)
  ↓ 后端开始处理
Thinking (思考) - 头部灯光旋转
  ↓ 后端返回结果
Speaking (说话) - 嘴巴开合
  ↓ 3秒后
Idle (待机)
```

## 技术架构

### 前端架构

```
sprite.html → sprite.tsx → Rive 动画组件
chat.html → chat.tsx → 对话界面
index.html → App.tsx → 传统控制台（可选保留）
```

### 窗口管理

```
Tauri App
├── Sprite Window (sprite)
│   ├── 透明背景
│   ├── Rive 动画
│   └── 拖拽区域
├── Chat Window (chat)
│   ├── 对话界面
│   ├── 附着在精灵旁边
│   └── 动态显示/隐藏
└── System Tray
    ├── 图标
    └── 右键菜单
```

### 状态同步

```
Backend (Python Brain)
  ↓ Dapr Pub/Sub
Tauri Event System
  ↓ listen('brain-status')
React State (Zustand)
  ↓ useStateMachineInput
Rive Animation State
```

## 文件结构

```
clients/desktop/
├── sprite.html              # 精灵窗口 HTML
├── chat.html                # 对话窗口 HTML
├── index.html               # 传统控制台（可选）
├── src/
│   ├── sprite.tsx           # 精灵窗口组件
│   ├── chat.tsx             # 对话窗口组件
│   ├── App.tsx              # 传统控制台（可选）
│   ├── store/
│   │   └── spriteStore.ts  # 精灵状态管理
│   └── lib/
│       └── api.ts           # API 客户端
├── src-tauri/
│   ├── src/
│   │   ├── main.rs          # Tauri 主入口
│   │   ├── window.rs        # 窗口管理
│   │   ├── dapr.rs          # Dapr 客户端
│   │   └── device.rs        # 设备控制
│   └── tauri.conf.json      # Tauri 配置（多窗口）
└── public/
    └── jachin_sprite.riv     # Rive 动画文件
```

## 配置说明

### Tauri 配置

```json
{
  "app": {
    "windows": [
      {
        "label": "sprite",
        "transparent": true,
        "decorations": false,
        "alwaysOnTop": true,
        "width": 200,
        "height": 200
      },
      {
        "label": "chat",
        "transparent": true,
        "decorations": false,
        "alwaysOnTop": true,
        "visible": false
      }
    ],
    "systemTray": {
      "iconPath": "icons/icon.ico"
    }
  }
}
```

### Rive 动画配置

1. **创建动画文件**：
   - 使用 Rive Editor 创建 `.riv` 文件
   - 包含状态机：Idle, Listening, Thinking, Speaking
   - 创建输入：isThinking, isListening, isSpeaking

2. **放置文件**：
   - 将 `.riv` 文件放到 `public/` 目录
   - 在代码中引用：`/jachin_sprite.riv`

## 开发流程

### 1. 安装依赖

```bash
npm install
npm install @rive-app/react
```

### 2. 创建 Rive 动画

- 使用 Rive Editor 设计动画
- 导出为 `.riv` 文件
- 放到 `public/` 目录

### 3. 启动开发

```bash
npm run tauri:dev
```

这将启动：
- Vite 开发服务器（端口 1420）
- 精灵窗口（透明，200x200）
- 对话窗口（初始隐藏）

### 4. 测试交互

- **拖拽**: 左键按住精灵拖动
- **双击**: 双击精灵打开对话窗口
- **对话**: 在对话窗口输入消息
- **状态**: 观察动画状态变化

## 未来扩展

### 短期
- [ ] 实现鼠标跟随（精灵看向鼠标）
- [ ] 添加右键菜单功能
- [ ] 优化窗口位置同步
- [ ] 添加动画过渡效果

### 中期
- [ ] 语音输入（麦克风监听）
- [ ] 语音输出（TTS 播放）
- [ ] 根据音量控制嘴巴开合
- [ ] 添加更多动画状态（happy, sad, excited）

### 长期
- [ ] 像素级点击穿透（只有角色实体可点击）
- [ ] 多角色支持
- [ ] 自定义动画主题
- [ ] 插件化动画系统

## 相关文档

- [Rive 官方文档](https://rive.app/community/doc/2.0/)
- [Tauri 多窗口文档](https://v2.tauri.app/develop/window/)
- [Tauri 系统托盘](https://v2.tauri.app/develop/system-tray/)
