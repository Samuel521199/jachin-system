# HR 插件数据目录 - 按职位分目录存储

一级文件夹为每个职位，结构如下：

```
data/
├── jd_to_publish.example.json   # 全局 JD 模板（复制到各职位 jd.json 后填写）
├── {职位名}/                    # 如 Java工程师、Golang开发
│   ├── pending/                # 刚抓取未对比的简历 PDF
│   ├── processed/              # 对比完成的简历
│   ├── result/                 # 每个简历的 AI 分析报告 (*_analysis.md)
│   ├── jd.json                 # 专属于该职位的 JD 配置（从模板复制并填写）
│   └── 排行榜_Summary.md       # 专属于该职位的输出 MD 文档
├── {职位名2}/
│   └── ...
```

## 配置逻辑

- **jd_to_publish.example.json**：每次与 HR 沟通得到的职位描述配置均以此为模版，复制到 `data/{职位}/jd.json` 后根据实际内容填写。
- **jd.json**：发布职位、推荐牛人、抓简历时均从此文件读取；执行前需先点击「全部职位」/职位下拉展开，再选中该职位。
- **pending**：收网抓取的 PDF 先存于此。
- **result**：HR 透析镜分析报告输出。
- **排行榜_Summary.md**：每次筛选覆盖更新，每个职位固定一份。
