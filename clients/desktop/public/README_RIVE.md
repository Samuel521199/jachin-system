# Rive 动画文件说明

## 文件位置

将 Rive 动画文件（`.riv`）放在 `public/` 目录下：

```
public/
└── jachin_sprite.riv  # 主精灵动画文件
```

## 动画要求

### 状态机设计

Rive 文件需要包含一个状态机，包含以下状态：

1. **Idle** - 待机状态（呼吸动画）
2. **Listening** - 倾听状态（耳朵/眼睛动画）
3. **Thinking** - 思考状态（头部灯光旋转）
4. **Speaking** - 说话状态（嘴巴开合，根据音量）

### 状态机输入

需要在 Rive 编辑器中创建以下布尔输入：

- `isThinking` - 是否在思考
- `isListening` - 是否在倾听
- `isSpeaking` - 是否在说话

### 动画尺寸

建议尺寸：200x200 像素（与窗口大小匹配）

## 创建动画

1. **使用 Rive 编辑器**：
   - 下载 Rive Editor: https://rive.app/
   - 创建新项目
   - 设计角色动画
   - 添加状态机
   - 导出为 `.riv` 文件

2. **占位方案**：
   - 如果没有 Rive 文件，代码会自动显示占位符（🤖 emoji）
   - 可以先用简单的 CSS 动画替代

## 集成步骤

1. 将 `.riv` 文件放到 `public/` 目录
2. 在 `src/sprite.tsx` 中更新路径：
   ```typescript
   src: "/jachin_sprite.riv"
   ```
3. 确保状态机名称匹配：
   ```typescript
   stateMachines: "State Machine 1"
   ```

## 示例动画状态

```
┌─────────┐
│  Idle   │ ← 默认状态，循环播放
└────┬────┘
     │
     ├─[双击]→ Listening
     │
     ├─[后端思考]→ Thinking
     │
     └─[AI回复]→ Speaking
```

## 资源

- [Rive 官方文档](https://rive.app/community/doc/2.0/getting-started)
- [Rive React 集成](https://rive.app/community/doc/2.0/react)
- [Rive 示例](https://rive.app/community/showcase)
