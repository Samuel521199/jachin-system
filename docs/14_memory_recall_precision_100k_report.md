# 十万级本地记忆召回压力测试报告

执行日期：2026-07-15

## 测试目标

本轮只测试记忆系统，不测试 Lark、计算器、浏览器、文件打开等桌面功能。目标是验证：当本地存在大量记忆、相似干扰记忆、过期诱饵记忆和结构化证据时，Memory Lifecycle 是否还能稳定召回真正需要的记忆。

## 本轮修复

- 召回分词从简单空格切分升级为中文、英文混合 query terms。
- 中文无空格查询增加 2、3、4 字窗口，提升连续中文问题的命中率。
- 召回文本加入 evidence、owner、domain、skill_id，让 governance_key、project_key、alias_key 等结构化证据参与检索。
- 清理旧的错误分词残留，避免乱码替换逻辑污染后续维护。
- 增加 Memory Lifecycle 进程内 records cache，按 store path、mtime、size 校验。
- 写入、治理、过期重写会刷新缓存；外部文件变化会自动重新加载。
- 新增纯记忆压测脚本，不启动桌面、不调用 Lark、不操作浏览器、不做 UI 点击。
- 新增记忆召回单测，覆盖大量噪声、中文无空格查询、相似干扰、过期诱饵过滤和 evidence key 召回。

## 测试命令

```powershell
python -m pytest -o addopts= -q tests\unit\test_memory_stress_mvp.py tests\unit\test_memory_quality_governance.py tests\unit\test_memory_recall_precision.py
python scripts\memory_recall_precision_stress.py --noise-count 10000
python scripts\memory_recall_precision_stress.py --noise-count 50000
python scripts\memory_recall_precision_stress.py --noise-count 100000
```

## 测试结果

单元测试：

```text
7 passed
```

1 万条噪声记忆：

```text
Top1 rate: 1.0
Top3 rate: 1.0
MRR: 1.0
Avg recall: 193.93 ms
Max recall: 220 ms
Result: PASS
```

5 万条噪声记忆，缓存修复前：

```text
Top1 rate: 1.0
Top3 rate: 1.0
MRR: 1.0
Avg recall: 1000.13 ms
Result: FAIL
```

失败原因：每次召回都重新读取并解析完整 JSONL，延迟不可接受。

5 万条噪声记忆，缓存修复后：

```text
Top1 rate: 1.0
Top3 rate: 1.0
MRR: 1.0
Avg recall: 120.87 ms
Max recall: 189 ms
Result: PASS
```

10 万条噪声记忆：

```text
Top1 rate: 1.0
Top3 rate: 1.0
MRR: 1.0
Avg recall: 244 ms
Max recall: 372 ms
Result: PASS
```

Evidence：

```text
output\memory_recall_precision\20260715_100640\memory_recall_precision_stress.evidence.json
```

## 覆盖场景

| 场景 | 结果 | 说明 |
| --- | --- | --- |
| 大量噪声记忆 | 通过 | 10 万条噪声下目标记忆 Top1 仍为 100% |
| 中文无空格查询 | 通过 | `Jachin项目路径在哪里` 可以命中正确项目路径 |
| 英文、中文混合查询 | 通过 | `project jachin path`、`lock是不是lark` 均能命中 |
| 相似干扰记忆 | 通过 | 旧项目路径、弱纠错提示、旧联系人噪声不会排到目标前面 |
| 过期高置信诱饵 | 通过 | `ttl=1ms` 的高置信 decoy 被过期过滤 |
| 结构化证据召回 | 通过 | `governance_key` 等 evidence 字段进入召回文本 |
| 性能退化暴露与修复 | 通过 | 5 万条从 1000ms 降到 121ms |

## 结论

本轮证明的不是“某个功能能点通”，而是 Memory Lifecycle 在大量记忆下仍然具备可用的召回精度和延迟表现。当前在 10 万条噪声记忆规模下，目标记忆 Top1、Top3、MRR 均为 100%，平均召回 244ms，已经可以支撑当前阶段的本地长期记忆检索。

下一阶段如果要进入百万级记忆，不应继续依赖 JSONL 全量扫描，应升级为 SQLite FTS、BM25 倒排索引、向量索引与图谱索引混合检索，并保留当前 lifecycle cache 作为热数据层。
