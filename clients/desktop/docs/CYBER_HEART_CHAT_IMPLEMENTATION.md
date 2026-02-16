# Cyber-Heart 风格聊天窗口实现文档

## 概述

本文档描述了 `clients/desktop` 聊天窗口的 UI 重构，实现了 "Cyber-Heart" 风格的设计，结合了硬核科幻（机械/电路）与暖心交互（爱心/红光）。

## 设计规范

### 配色方案

- **背景**: 深海军蓝/午夜蓝渐变 (`bg-gradient-to-b from-slate-900 via-blue-950 to-slate-900`)
- **主色调**: 霓虹绯红/热粉色 (Neon Crimson/Hot Pink)
  - `neon-rose-400`: #fb7185
  - `neon-rose-500`: #f43f5e
  - `neon-rose-600`: #e11d48
  - `neon-rose-700`: #be123c
- **强调色**: 金属银/铬色 (Metallic Silver)
  - `metallic-silver-300`: #cbd5e1
  - `metallic-silver-400`: #94a3b8
  - `metallic-silver-500`: #64748b

### 组件结构

#### 1. ChatWindow (`components/Chat/ChatWindow.tsx`)
主容器组件，包含：
- 背景电路纹理层（带脉冲动画）
- 顶部发光标题栏 "ROBO-CHAT"
- 消息列表区域
- 底部输入栏（能量槽样式）

#### 2. ChatBubble (`components/Chat/ChatBubble.tsx`)
消息气泡组件：
- **AI (左侧)**: 玻璃拟态 + 红色描边 + 爱心装饰
- **User (右侧)**: 半透明背景 + 银色描边

#### 3. WindowControls (`components/Chat/WindowControls.tsx`)
自定义窗口控制按钮（最小化/关闭）

### 动画效果

1. **背景脉冲流光**: `animate-pulse-glow` (2s 循环)
2. **消息滑入**: Spring 动画 (`type: "spring", stiffness: 300, damping: 30`)
3. **标题呼吸灯**: `animate-breath` (3s 循环)
4. **按钮交互**: Framer Motion `whileHover` 和 `whileTap`

### 字体

使用科幻字体：`font-sci-fi` (Orbitron, Exo 2, sans-serif)

## 文件结构

```
clients/desktop/src/
├── components/
│   └── Chat/
│       ├── ChatWindow.tsx      # 主窗口组件
│       ├── ChatBubble.tsx       # 消息气泡组件
│       └── WindowControls.tsx   # 窗口控制按钮
├── chat.tsx                    # 入口文件（已重构）
└── styles/
    └── globals.css             # 全局样式（包含自定义滚动条）
```

## 使用方式

```tsx
import { ChatWindow, ChatMessage } from "./components/Chat/ChatWindow";

const messages: ChatMessage[] = [
  {
    role: "user",
    content: "Hello!",
    timestamp: Date.now(),
  },
  {
    role: "assistant",
    content: "Hi there!",
    timestamp: Date.now(),
  },
];

<ChatWindow
  messages={messages}
  input={input}
  onInputChange={setInput}
  onSend={handleSend}
  isLoading={false}
  isTyping={false}
  placeholder="Type a message..."
/>
```

## 特性

1. ✅ Cyber-Heart 风格设计
2. ✅ 玻璃拟态效果
3. ✅ 电路板背景纹理
4. ✅ 脉冲流光动画
5. ✅ 消息滑入动画
6. ✅ 呼吸灯效果
7. ✅ 自定义滚动条
8. ✅ 窗口控制按钮
9. ✅ 爱心图标装饰
10. ✅ 响应式交互

## 待优化

1. 添加更多电路板纹理细节
2. 实现消息删除功能
3. 添加消息编辑功能
4. 优化移动端适配（如果需要）

## 依赖

- `framer-motion`: 动画库
- `lucide-react`: 图标库
- `tailwindcss`: 样式框架
