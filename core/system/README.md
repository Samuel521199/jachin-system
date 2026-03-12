# System Management

Tier 2 系统管理组件

## 组件

- `plugin_manager.py` - 插件管理器（.jsp 包管理）
- `updater.py` - 系统 OTA 更新管理器

## 功能

### Plugin Manager
- 安装/卸载插件
- 验证插件签名（Tier 1 官方签名）
- License Key 管理
- DRM 心跳验证

### System Updater
- 检查更新
- 下载更新包
- 零停机更新
