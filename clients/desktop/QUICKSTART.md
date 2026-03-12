# Desktop Client 快速开始

## 5 分钟快速上手

### 1. 安装前置要求

**Windows:**
```powershell
# Node.js (如果还没有)
winget install OpenJS.NodeJS.LTS

# Rust (如果还没有)
winget install Rustlang.Rustup

# Tauri CLI
npm install -g @tauri-apps/cli@next
```

**Linux/macOS:**
```bash
# Node.js (使用 nvm 推荐)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
nvm install --lts

# Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Tauri CLI
npm install -g @tauri-apps/cli@next
```

### 2. 安装依赖

```bash
cd clients/desktop
npm install
```

### 3. 启动后端服务

在项目根目录启动后端（确保 Dapr 运行）：

```bash
# Windows
.\start.bat

# Linux/macOS
./scripts/start.sh
```

### 4. 启动桌面客户端

```bash
cd clients/desktop
npm run tauri:dev
```

## 验证安装

如果一切正常，你应该看到：

1. ✅ 后端服务运行在 `http://localhost:8000`
2. ✅ Dapr sidecar 运行在端口 `3500`
3. ✅ 桌面应用窗口打开，显示 "Jachin Console"
4. ✅ 状态栏显示 "已连接"（绿色圆点）

## 首次使用

1. **发送消息**: 在输入框输入 "你好"，按 Enter 或点击发送
2. **查看回复**: AI 助手会回复你的消息
3. **检查连接**: 顶部状态栏显示连接状态

## 常见问题

### Q: Rust 编译很慢？

A: 首次编译需要下载依赖，可能需要几分钟。后续编译会快很多。

### Q: 无法连接到后端？

A: 确保：
- 后端服务已启动（`.\start.bat`）
- Dapr sidecar 运行在端口 3500
- 防火墙没有阻止连接

### Q: 窗口无法打开？

A: 检查：
- Rust 工具链是否正确安装
- Tauri CLI 版本是否正确（v2）
- 查看终端错误信息

## 下一步

- 📖 阅读 [完整开发指南](../docs/DESKTOP_CLIENT.md)
- 🎨 自定义 UI 样式
- 🔧 添加硬件控制功能
- 📦 构建生产版本
