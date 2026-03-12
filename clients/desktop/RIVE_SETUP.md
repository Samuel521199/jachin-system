# Rive 动画设置指南

## 安装

正确的包名是 `@rive-app/react-canvas`：

```bash
npm install @rive-app/react-canvas
```

## 基本用法

```typescript
import { useRive } from "@rive-app/react-canvas";

const { rive, RiveComponent } = useRive({
  src: "/your-animation.riv",
  stateMachines: "State Machine 1",
  autoplay: true,
});

// 渲染组件
<RiveComponent style={{ width: "200px", height: "200px" }} />
```

## 控制状态机输入

```typescript
useEffect(() => {
  if (!rive) return;

  const stateMachine = rive.stateMachineInputs("State Machine 1");
  if (!stateMachine) return;

  // 查找输入
  const input = stateMachine.find((input) => input.name === "isThinking");
  
  // 设置布尔值
  if (input && "value" in input) {
    (input as any).value = true;
  }
  
  // 触发触发器
  if (input && "fire" in input) {
    (input as any).fire();
  }
}, [rive, state]);
```

## 创建动画文件

1. **下载 Rive Editor**: https://rive.app/
2. **创建新项目**
3. **设计角色动画**
4. **添加状态机**:
   - 创建状态：Idle, Listening, Thinking, Speaking
   - 创建输入：isThinking (布尔), isListening (布尔), isSpeaking (布尔)
   - 设置状态转换
5. **导出**: File → Export → 选择 `.riv` 格式
6. **放置文件**: 将 `.riv` 文件放到 `public/` 目录

## 占位方案

如果没有 Rive 文件，代码会自动显示占位符（🤖 emoji）。可以先用简单的 CSS 动画替代：

```css
@keyframes breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.05); }
}

.sprite-placeholder {
  animation: breathe 3s ease-in-out infinite;
}
```

## 资源

- [Rive 官方文档](https://rive.app/community/doc/2.0/)
- [React 集成指南](https://rive.app/docs/runtimes/react)
- [状态机教程](https://rive.app/docs/editor/state-machine/)
