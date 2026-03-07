# HR 岗位要求 (JD) 配置

供 HR 简历透视镜技能进行 JD 对照评估。当 `target_role` 为以下预设 key 时，将自动加载对应 JD 全文：

| target_role | 说明 |
|-------------|------|
| `backend_engineer` | 云边协同后端工程师 |

## 新增 JD

1. 在本目录创建 `{key}.md` 文件
2. 在 `l3_node/skills/loader.py` 的 `_HR_JD_KEYS` 中加入该 key
