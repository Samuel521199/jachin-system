# Jachin-System 技能存储库

## 目录说明

此目录用于存储已安装的技能包。每个技能都会被解压到独立的子目录中。

## 目录结构

```
skills_repo/
├── {skill_id}/          # 技能唯一标识
│   ├── manifest.yaml    # 技能清单文件
│   ├── code/            # 技能代码
│   ├── config/          # 技能配置
│   └── ...              # 其他技能文件
└── README.md            # 本文件
```

## 技能安装

技能通过以下方式安装：

1. **从市场安装**: 通过 Jachin Market 下载并安装
2. **本地安装**: 上传技能包（zip文件）进行安装
3. **开发模式**: 直接放置到 `skills_repo/` 目录

## 技能清单 (manifest.yaml)

每个技能必须包含一个 `manifest.yaml` 文件，定义技能的基本信息和能力。

示例：

```yaml
name: "example-skill"
version: "1.0.0"
description: "示例技能"
author: "Jachin Team"
license: "MIT"
runtime:
  type: "docker"
  image: "jachin-skill/example:latest"
capabilities:
  - name: "example_action"
    type: "action"
    description: "示例动作"
    input_schema:
      type: "object"
      properties:
        text:
          type: "string"
    output_schema:
      type: "object"
      properties:
        result:
          type: "string"
```

## 技能管理

- **安装**: 技能安装后会自动注册到数据库
- **启用/禁用**: 可以通过API或Web UI管理技能状态
- **卸载**: 卸载技能会删除目录和数据库记录

## 注意事项

- ⚠️ **不要手动删除技能目录**，请使用系统提供的卸载功能
- ⚠️ **不要修改已安装技能的 manifest.yaml**，这可能导致系统错误
- ✅ 技能代码可以热更新，但需要重启技能运行时

## 开发技能

如果你要开发新技能，请参考：

- `docs/TECHNICAL_SPECIFICATIONS.md` - 技能开发规范
- `core/runtime/schemas/manifest_schema.json` - Manifest Schema定义
