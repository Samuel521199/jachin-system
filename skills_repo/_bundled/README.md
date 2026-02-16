# 预装技能库 (_bundled)

此目录包含 Jachin-System 的预装技能（Bundled Skills），这些技能随系统一起提供，无需从市场安装。

## 目录结构

```
_bundled/
├── com.jachin.os-mate/      # 系统管家
│   ├── manifest.yaml
│   └── main.py
├── com.jachin.files/        # 文件指挥官
│   ├── manifest.yaml
│   └── main.py
└── com.jachin.sys-monitor/  # 系统仪表盘
    ├── manifest.yaml
    └── main.py
```

## 预装技能列表

### 1. com.jachin.os-mate (系统管家)

**功能**：
- `shutdown`: 关闭系统
- `reboot`: 重启系统
- `volume_set`: 设置系统音量

**权限**：
- `system.power`: 系统电源控制
- `system.control`: 系统控制

### 2. com.jachin.files (文件指挥官)

**功能**：
- `list_files`: 列出指定目录的文件
- `search_files`: 搜索文件（支持文件名和内容搜索）

**权限**：
- `file.read`: 文件读取权限

### 3. com.jachin.sys-monitor (系统仪表盘)

**功能**：
- `get_performance_snapshot`: 获取系统性能快照（CPU、内存、磁盘、温度）

**权限**：
- `system.telemetry`: 系统监控数据访问权限

## 技能格式

每个技能必须包含：

1. **manifest.yaml**: 技能清单文件，定义：
   - 技能基本信息（id, version, name, description）
   - 能力列表（capabilities）
   - 权限申请（permissions）
   - 依赖和运行时配置

2. **main.py**: 技能入口文件，必须包含：
   - `execute(capability: str, params: Dict[str, Any]) -> Dict[str, Any]` 函数

## 注意事项

- 预装技能是系统核心功能的一部分，不应被用户卸载
- 技能代码可以更新，但需要遵循版本管理规范
- 所有技能必须通过权限检查才能执行敏感操作
