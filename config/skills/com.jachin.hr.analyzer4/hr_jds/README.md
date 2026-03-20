# HR 岗位要求 (JD) 配置

供 HR 透析镜 (hr-analyzer4) 技能进行 JD 对照评估。
规范: .cursor/rules/075-config-root-and-cloud-sync(1).mdc
路径: config/skills/com.jachin.hr.analyzer4/hr_jds/

## 文件说明

| 文件 | 说明 |
|------|------|
| `backend_engineer.md` | 后端工程师 JD，`target_role=backend_engineer` 时加载 |

## 新增 JD（target_role 方式）

1. 在本目录创建 `{key}.md` 文件
2. 技能会按 `target_role` 自动加载对应 `{key}.md`，无需修改代码
