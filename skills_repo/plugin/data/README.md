# HR 插件 — 仓库内仅保留 JD 模板（只读）

**候选人简历、jd.json 副本、pending/result 等业务数据已统一落在用户目录，不再写入本仓库：**

- 默认根目录：`~/.jachin/workspace/hr_recruitment/`
- 可通过环境变量 `JACHIN_HR_DATA_ROOT` 覆盖（仍建议不要指向项目仓库内路径）
- 每个职位：`hr_recruitment/{职位文件夹}/pending`、`processed`、`副本`（可选备份）、`result`、`jd.json`、`排行榜_Summary.md`；透析镜在 `pending` 为空时会继续读 `processed`/`副本`。

本目录（`skills_repo/plugin/data/`）仅保留：

```
data/
└── jd_to_publish.example.json   # 全局 JD 模板（复制到 ~/.jachin/.../hr_recruitment/{职位}/jd.json 后填写）
```

## 配置逻辑

- **jd_to_publish.example.json**：与 HR 沟通得到的职位描述以此为模版，复制到 **`~/.jachin/workspace/hr_recruitment/{职位}/jd.json`** 后填写。
- **jd.json**：发布职位、推荐牛人、抓简历时从上述用户目录下的 `jd.json` 读取。
- **pending**：收网抓取的 PDF 存于用户目录对应职位的 `pending/`。
- **result**：HR 透析镜分析报告输出到用户目录对应职位的 `result/`。
- **排行榜_Summary.md**：每个职位在用户目录下固定一份。
