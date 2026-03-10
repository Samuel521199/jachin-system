# JD 发布配置文件说明

## 前置条件（重要）

- 必须使用 **招聘方（招人）** 账号登录 Boss 直聘，求职者账号无「职位管理」「发布职位」入口
- 若当前是求职端，需切换至招聘端

## 文件位置

- **手动填写（测试用）**：`data/jd_to_publish.json`
- **示例模板**：`data/jd_to_publish.example.json`（可复制为 jd_to_publish.json）

## 字段说明

| 字段 | 说明 | 示例 |
|------|------|------|
| recruitment_type | 招聘类型 | 社招全职、应届校园招聘、实习生招聘、兼职招聘 |
| job_title | 职位名称 | 资深Golang语言开发 |
| jd_full | 职位描述（完整 JD 内容） | 岗位职责、任职要求等 |
| job_category_path | 职位类型路径（左栏→右栏依次点击） | ["互联网/AI", "后端开发", "Go开发工程师"] |
| experience | 经验要求 | 不限、1年以内、1-3年、3-5年、5-10年、10年以上 |
| education | 学历要求 | 不限、大专、本科、硕士、博士 |
| salary_min | 最低月薪（K） | 19 |
| salary_max | 最高月薪（K） | 20 |
| job_keywords | 职位关键词（多选） | ["Go", "Golang", "微服务", "Redis"] |

## 未来扩展

后续可对接 `nat_lang_to_jd` 工具，将 HR 自然语言解析后自动写入 recruitment_status，本工具优先读取 `jd_to_publish.json`，若无则回退到 recruitment_status。
