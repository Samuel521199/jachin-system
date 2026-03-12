# HR 岗位要求 (JD) 配置

供 HR 简历透视镜 / 透析镜 2 技能进行 JD 对照评估。

## 文件说明

| 文件 | 说明 |
|------|------|
| `backend_engineer.md` | 后端工程师 JD，`target_role=backend_engineer` 时加载 |
| `hr_analyzer2.md` | HR 透析镜 2 专用 JD，在 Skill Matrix 设置中修改 JD_template 时会同步更新此文件 |

## 新增 JD（target_role 方式）

1. 在本目录创建 `{key}.md` 文件
2. 在 `l3_node/skills/loader.py` 的 `_HR_JD_KEYS` 中加入该 key
