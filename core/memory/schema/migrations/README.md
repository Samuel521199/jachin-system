# 数据库迁移说明

## Alembic迁移系统

本目录包含Jachin-System v3.2的数据库迁移脚本。

## 使用方法

### 初始化数据库（首次运行）

```bash
# Windows PowerShell
cd core
alembic upgrade head

# Linux/macOS
cd core
alembic upgrade head
```

### 创建新迁移

```bash
# 自动生成迁移脚本
alembic revision --autogenerate -m "描述信息"

# 手动创建迁移脚本
alembic revision -m "描述信息"
```

### 查看迁移历史

```bash
alembic history
```

### 回滚迁移

```bash
# 回滚到上一个版本
alembic downgrade -1

# 回滚到指定版本
alembic downgrade <revision_id>
```

## 配置文件

- `alembic.ini` - Alembic配置文件
- `env.py` - Alembic环境配置
- `script.py.mako` - 迁移脚本模板

## 迁移脚本

- `versions/001_initial_schema.py` - 初始Schema（v3.2）

## 注意事项

1. 迁移脚本会自动从`core.config.settings`读取数据库URL
2. 确保数据库服务已启动
3. 生产环境建议先备份数据库再运行迁移
