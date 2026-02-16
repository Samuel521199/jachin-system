# Plugins

可插拔的功能模块，每个插件代表一个设备能力。

## 插件列表

- `camera/` - 摄像头控制和图像采集
- `gpio/` - GPIO 引脚控制（树莓派）
- `file_system/` - 文件系统操作（Desktop）
- `screen/` - 屏幕捕获（Desktop）
- `speaker/` - 音频播放
- `temperature/` - 温度传感器
- `relay/` - 继电器控制（IoT）

## 插件结构

每个插件必须包含：
- `manifest.json` - 插件元数据
- `actions.py` - 可执行指令
- `sensors.py` - 数据上报（可选）
- `__init__.py` - 插件初始化

## 开发新插件

1. 在 `plugins/` 目录下创建新目录
2. 创建 `manifest.json` 定义插件元数据
3. 实现 `BaseAction` 和 `BaseSensor` 接口
4. 编写测试用例
5. 更新文档

详细规范请参考 `.cursor/rules/020-client-plugins.mdc`。
