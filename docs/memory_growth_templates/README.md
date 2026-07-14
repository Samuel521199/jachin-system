# Memory Growth Templates

这些模板用于 Jachin AI 自生长知识系统。

运行时数据不写入仓库，而是写入：

```text
$JACHIN_COGNITIVE_KERNEL_HOME/memory_growth/
```

默认路径为：

```text
~/.jachin/cognitive_kernel/memory_growth/
```

目录分层：

```text
memory_growth/
  raw/          # append-only 原始证据
  concepts/     # 高价值概念 Markdown Wiki
  playbooks/    # 可复用方法论
  outputs/      # 对外成果和任务输出
  reviews/      # Daily/Weekly review 与 patch
  indexes/      # overview、实体目录、检索索引
  conflicts/    # 冲突事实和待确认事实
```

原则：

- Raw 只能追加，不覆盖。
- Concepts 和 Playbooks 必须可追溯到 Raw。
- 实时链路只写轻量事件，深度消化交给 Review Agent。
- 输出成果必须回流，供后续复盘升级。
