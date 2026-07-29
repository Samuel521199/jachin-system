# Jachin 工作账本 MVP 实现方案

## 1. 产品定位

Jachin 第一版 MVP 不再以“替用户操作电脑”为核心目标，而是定位为：

**AI 工作记忆层 / 工作账本 / 开发复盘助手。**

它服务的不是单次操作，而是连续工作过程：

- 今天做了什么。
- 为什么这么做。
- 改了哪些文件。
- 遇到哪些问题。
- 哪些方案失败过。
- 哪些经验值得沉淀。
- 明天如何继续。
- 如何生成日报、周报、绩效材料和下一轮 Codex / Cursor 任务书。

核心判断标准：

**Jachin 是否减少了用户的工作上下文丢失。**

如果一个功能只是替用户点按钮，它不是 MVP 主线。
如果一个功能能记录过程、保留证据、生成复盘、续上明天的任务，它就是 MVP 主线。

## 2. MVP 目标

第一版只服务一个真实用户：开发者本人。

目标是在日常开发中形成这个闭环：

1. 开始任务。
2. 自动采集真实证据。
3. 用户补充关键判断。
4. 结束任务并生成工作记录。
5. 第二天继续任务。
6. 把复盘内容沉淀为长期工作资产。

一句话目标：

**每天结束时，Jachin 能基于真实证据生成可信工作记录，并帮助第二天无缝继续。**

## 3. MVP 不做什么

第一版明确不做：

- 不做大而全 AI OS。
- 不追求控制所有 App。
- 不追求无人自动办公。
- 不做复杂团队协作。
- 不做手机端。
- 不先做插件市场。
- 不依赖“Codex 自己说它做了什么”作为唯一事实来源。

Codex / Cursor 是执行者。
Jachin 是记录者、复盘者、证据账本和长期记忆层。

## 4. 核心用户场景

### 4.1 早上开始任务

用户输入：

```text
开始任务：优化 Jachin 常开语音。
项目路径：D:\Projects\jachi\jachin-system-main
目标：减少噪声误触，提高打开 Lark / 微信等语音任务的稳定性。
```

Jachin 记录：

- 任务名称。
- 项目路径。
- 开始时间。
- 用户原始目标。
- 当前 Git 状态。
- 当前分支。
- 最近提交。
- 当前工作区是否干净。

### 4.2 白天过程记录

Jachin 自动采集：

- Git status。
- Git diff。
- Git log。
- 最近修改文件。
- 新增文件。
- 删除文件。
- 测试命令结果。
- 构建日志。
- 用户手动补充。

用户可以随时说：

```text
记录一下：这次语音误触不是单纯 STT 问题，还和 pending task session 没接好有关。
```

Jachin 把它作为一条“用户确认过的过程事实”写入当前任务。

### 4.3 晚上结束任务

用户点击或输入：

```text
结束今天任务
```

Jachin 生成：

- 今日完成内容。
- 修改文件清单。
- 关键决策。
- 遇到的问题。
- 已解决问题。
- 未解决风险。
- 明日计划。
- 可复用经验。
- Lark 日报草稿。
- Codex / Cursor 下一轮任务书。

### 4.4 第二天继续任务

用户输入：

```text
继续昨天的语音任务
```

Jachin 输出：

- 昨天目标。
- 已完成部分。
- 相关文件。
- 失败尝试。
- 当前风险。
- 下一步建议。
- 可直接发给 Codex / Cursor 的任务 prompt。

## 5. 系统职责分工

### 5.1 大模型负责

大模型只负责脑力工作：

- 理解用户任务目标。
- 从原始证据中提炼事实。
- 总结 Git diff 和日志。
- 生成日报、周报和简报。
- 生成下一轮 Codex / Cursor prompt。
- 判断哪些经验值得沉淀。
- 把技术过程改写成人能看懂的表达。

### 5.2 Jachin 负责

Jachin 负责不能让大模型编造的部分：

- 创建任务会话。
- 采集真实证据。
- 保存证据链。
- 维护任务状态。
- 管理用户确认、系统推断、用户否定三类记忆。
- 召回历史任务。
- 生成 Evidence。
- 管理日报、周报、复盘和知识沉淀文件。
- 把输出交付到 Markdown / Lark / 本地知识库。

原则：

**事实由 Jachin 采集，表达由大模型生成，最终由证据链约束。**

## 6. MVP 核心模块

### 6.1 WorkSession Manager

管理当前工作任务。

职责：

- 创建任务。
- 暂停任务。
- 恢复任务。
- 结束任务。
- 记录任务状态。
- 关联项目路径。
- 关联输出文件。

第一版字段：

- session_id
- title
- project_name
- project_path
- start_time
- end_time
- status
- user_goal
- tags
- created_from

### 6.2 Evidence Collector

采集真实工作证据。

第一版只采集硬证据：

- Git status。
- Git diff。
- Git log。
- 文件修改列表。
- 测试命令输出。
- 构建日志。
- 用户手动补充。

每条证据都必须包含：

- evidence_id
- session_id
- source
- collected_at
- summary
- raw_path 或 raw_text
- trust_level

### 6.3 Manual Note Capture

允许用户随手记录关键判断。

例子：

```text
记录一下：本次失败原因是 Lark 发送结果缺少 OCR 校验，不是发送动作本身失败。
```

这类内容默认标记为：

```text
trust_level = user_confirmed
```

### 6.4 Work Summary Composer

基于证据生成工作记录。

输出包括：

- 今日工作记录。
- 技术细节版。
- 给领导看的简洁版。
- 给同事看的 Lark 版。
- 明日计划。
- Codex / Cursor 续写 prompt。

要求：

- 不能编造。
- 每个关键结论必须能回到证据。
- 不确定内容必须标为“待确认”。

### 6.5 Continuation Prompt Builder

生成给 Codex / Cursor 的下一轮任务书。

结构：

```text
当前目标：
已完成内容：
相关文件：
关键变更：
失败尝试：
当前风险：
下一步请做：
验收标准：
```

这个模块是 MVP 的关键卖点之一。

### 6.6 Memory Promotion Engine

把任务过程中的内容分层沉淀。

分为四层：

- Raw Evidence：原始证据。
- Work Fact：工作事实。
- Reusable Lesson：可复用经验。
- Playbook：方法论或排查手册。

例子：

Raw Evidence：

```text
Lark 发送日志显示 delivery verification failed。
```

Work Fact：

```text
本次 Lark 发送动作执行了，但发送后校验失败。
```

Reusable Lesson：

```text
遇到 Lark 发送失败时，需要区分“发送失败”和“校验失败”。
```

Playbook：

```text
Lark 发送排查流程：先看 UI 是否发送，再看 OCR / API 校验，再看收件人解析。
```

## 7. 数据流

### 7.1 开始任务数据流

```text
用户输入
-> Goal Interpreter
-> WorkSession Manager
-> Git Snapshot
-> Initial Evidence
-> Work Ledger
```

### 7.2 过程采集数据流

```text
Git / 文件 / 测试 / 日志 / 用户补充
-> Evidence Collector
-> Evidence Normalizer
-> Work Ledger
-> Memory Candidate Queue
```

### 7.3 结束任务数据流

```text
WorkSession
-> Evidence Recall
-> Summary Composer
-> Quality Gate
-> Daily Report
-> Continuation Prompt
-> Memory Promotion
```

### 7.4 继续任务数据流

```text
用户输入“继续某任务”
-> Task Recall
-> Evidence Recall
-> Memory Recall
-> Continuation Prompt Builder
-> 输出下一轮 Codex / Cursor prompt
```

## 8. 页面设计

第一版只需要一个页面：

**今日工作台**

### 8.1 当前任务区

展示：

- 当前任务名。
- 项目路径。
- 当前状态。
- 开始时间。
- 最近一次采集时间。
- 结束任务按钮。

### 8.2 证据时间线

展示：

- Git 变化。
- 文件变化。
- 测试结果。
- 构建日志。
- 用户补充。
- AI 总结。

每条证据可以展开查看原始内容。

### 8.3 输出区

按钮：

- 生成日报。
- 生成周报素材。
- 生成 Codex 续写 Prompt。
- 沉淀为经验。
- 导出 Markdown。
- 发送到 Lark。

### 8.4 待确认区

展示系统推断但需要用户确认的内容：

- 这个是否是关键决策？
- 这个失败原因是否正确？
- 这条经验是否要进入长期记忆？
- 这个项目路径是否绑定到项目名？

## 9. 实现步骤

### Step 1：定义 Work Ledger 数据结构

目标：

- 建立任务会话、证据、输出、记忆候选的统一数据结构。

产物：

- WorkSession schema。
- Evidence schema。
- WorkOutput schema。
- MemoryCandidate schema。

验收：

- 可以创建一个任务。
- 可以写入一条 evidence。
- 可以读取当前任务完整记录。

### Step 2：实现开始任务 / 结束任务

目标：

- 用户可以启动和结束一个工作任务。

产物：

- start_work_session。
- end_work_session。
- get_active_work_session。

验收：

- 输入“开始任务：xxx”后创建会话。
- 输入“结束任务”后关闭会话。
- 任务状态可回放。

### Step 3：接入 Git 和文件证据采集

目标：

- 第一版先采集最可靠的事实。

采集内容：

- git status。
- git diff。
- git log。
- 最近修改文件。
- 新增和删除文件。

验收：

- 开始任务时记录初始 Git 状态。
- 结束任务时记录最终 Git 状态。
- 能看出任务期间改了哪些文件。

### Step 4：实现手动补充记录

目标：

- 用户可以随时补充关键判断。

例子：

```text
记录一下：这个问题是因为 xxx。
```

验收：

- 手动记录进入当前任务。
- 标记为 user_confirmed。
- 结束任务时会进入总结素材。

### Step 5：实现日报生成

目标：

- 基于证据生成可信工作日报。

输出：

- 今日完成。
- 文件变更。
- 问题与风险。
- 明日计划。
- 可发 Lark 版。

验收：

- 不出现无证据结论。
- 文件路径准确。
- 风险和未完成点能明确标注。

### Step 6：实现 Codex / Cursor 续写 Prompt

目标：

- 第二天可以直接把 Jachin 生成的 prompt 发给 Codex / Cursor。

验收：

- Prompt 包含目标、进展、文件、风险、下一步。
- 用户不需要重新解释昨天发生了什么。

### Step 7：实现记忆候选和经验沉淀

目标：

- 从任务中提炼可复用经验。

第一版只做半自动：

- 系统提出候选经验。
- 用户确认后写入长期记忆。

验收：

- 失败经验可进入 Memory Growth。
- 下次类似任务可以召回。

### Step 8：实现今日工作台页面

目标：

- 让用户每天真的能用。

页面包含：

- 当前任务。
- 证据时间线。
- 输出区。
- 待确认区。

验收：

- 用户不需要看 JSON。
- 每个输出都能看到来源证据。

### Step 9：7 天自用测试

目标：

- 判断这个 MVP 是否真的有日常价值。

测试方式：

- 连续 7 天每天至少启动一个任务。
- 每天结束生成日报。
- 第二天使用续写 prompt。
- 周五生成周报素材。

验收：

- 每天下班 3 分钟内生成可用日报。
- 第二天 2 分钟内恢复上下文。
- 一周至少沉淀 5 条可复用经验。

## 10. 第一版成功标准

MVP 成功必须满足：

1. 用户连续 7 天每天使用。
2. 每天能生成可信日报。
3. 每份日报都能追溯到 Git / 文件 / 日志 / 用户补充。
4. 第二天能生成可用的 Codex / Cursor 任务书。
5. 周五能生成周报素材。
6. 至少沉淀 5 条可复用经验。
7. 用户明显感觉“不用 Jachin，第二天接不上任务”。

## 11. 风险与边界

### 11.1 最大风险：变成普通总结器

规避方式：

- 不以 Codex 自述作为唯一依据。
- 必须采集 Git / 文件 / 日志等真实证据。
- 关键结论必须能回溯。

### 11.2 最大风险：采集太多导致负担

规避方式：

- 第一版只采集硬证据。
- 不录屏。
- 不监听全部聊天。
- 用户可以手动补充关键判断。

### 11.3 最大风险：生成内容不可用

规避方式：

- 输出分版本：自用版、领导版、Lark 版、Codex Prompt 版。
- 允许用户编辑并把编辑结果回流为偏好记忆。

## 12. 后续演进方向

MVP 跑通后再做：

1. Cursor / Codex 对话导入。
2. Lark 日报自动发送。
3. 周报和绩效材料生成。
4. 项目经验库。
5. 团队共享知识库。
6. 多项目工作账本。
7. 与 Memory Growth 深度融合。
8. 根据历史失败自动生成排查 Playbook。
9. 从个人工作账本升级为团队 AI 工作记忆系统。

## 13. 最终结论

Jachin 第一版 MVP 应该是：

**个人 AI 工作账本：任务开始、过程证据、结束复盘、明日续写。**

它不和 Codex / Cursor 竞争，而是成为它们之后的工作记忆层。

Codex 解决“这一轮怎么做”。
Jachin 解决“这一轮做完后，工作如何被记住、复盘、交接和复用”。

## 14. 实现进度

### Node 1：Work Ledger 最小闭环骨架

状态：已完成。

本节点完成内容：

- 新增 Work Ledger 本地持久化模块。
- 默认落盘到 `~/.jachin/work_ledger`，支持通过 `JACHIN_WORK_LEDGER_HOME` 覆盖。
- 支持创建工作任务、读取活动任务、列出最近任务、结束任务。
- 支持写入 Evidence JSONL。
- 支持手动补充用户确认记录。
- 支持 Git 证据采集：
  - `git status --short --branch`
  - `git status --porcelain`
  - `git log -8 --oneline --decorate --no-merges`
  - `git diff --stat`
  - `git diff --name-status`
  - `git diff --cached --name-status`
- 支持文件系统最近修改扫描。
- 支持生成两类基础输出：
  - `daily_report.md`
  - `codex_continuation_prompt.md`
- Work Ledger 证据会同步写入 Cognitive Kernel ledger。
- Work Ledger 证据会作为 Memory Growth raw evidence 进入后续消化链路。

涉及文件：

- `l3_node/work_ledger.py`
- `l3_node/work_ledger_http.py`
- `l3_node/http_server.py`
- `clients/desktop/src/console/pages/WorkLedgerPanel.tsx`
- `clients/desktop/src/console/routes.tsx`
- `clients/desktop/src/console/Sidebar.tsx`
- `tests/unit/test_work_ledger_mvp.py`

新增 L3 HTTP API：

- `GET /api/v1/work-ledger/status`
- `GET /api/v1/work-ledger/sessions`
- `GET /api/v1/work-ledger/sessions/{session_id}`
- `POST /api/v1/work-ledger/start`
- `POST /api/v1/work-ledger/collect`
- `POST /api/v1/work-ledger/note`
- `POST /api/v1/work-ledger/generate`
- `POST /api/v1/work-ledger/end`

控制台新增页面：

- `今日工作台`
- 路由：`/work-ledger`

页面能力：

- 创建当前任务。
- 展示活动任务。
- 手动采集 Git / 文件证据。
- 写入过程记录。
- 生成日报和 Codex / Cursor 续写 Prompt。
- 结束任务。
- 查看证据时间线。
- 查看最近任务。

验证结果：

- Python 模块编译通过。
- Work Ledger smoke 闭环通过：
  - 开始任务。
  - 写入手动记录。
  - 采集 Git / 文件证据。
  - 生成日报。
  - 生成 Codex / Cursor 续写 Prompt。
  - 结束任务。
- 前端 TypeScript 检查通过。
- 单元测试通过：`tests/unit/test_work_ledger_mvp.py`

当前边界：

- 日报和续写 Prompt 仍是确定性基础版，尚未接入大模型润色。
- 采集范围目前以 Git / 文件 / 手动记录为主，尚未接入 Codex / Cursor 对话导入。
- 控制台页面已能查看证据，但尚未做 Evidence Console 的统一回放融合。

### 下一节点：Node 2：聊天入口接入 Work Ledger

目标：

- 用户可以直接在聊天框输入：
  - `开始任务：优化常开语音`
  - `记录一下：这次失败原因是声纹阈值太松`
  - `结束今天任务`
  - `继续昨天任务`
- 这些输入不再走普通聊天，而是直接进入 Work Ledger Workflow。
- 生成结果以聊天回复 + 工作台证据双通道展示。

## Node 2 执行记录：聊天入口接入 Work Ledger

状态：已完成 MVP。

本节点完成内容：

- 新增 `l3_node/work_ledger_chat.py`，把 Work Ledger 聊天命令解析和执行从 `agent_core.py` 中独立出来。
- 支持聊天或语音归一化后文本直接触发：
  - `开始任务：xxx`
  - `新建任务：xxx`
  - `记录一下：xxx`
  - `采集证据`
  - `生成日报`
  - `结束今天任务`
  - `继续昨天任务`
- 支持从文本中提取 `项目路径：D:\...`，启动任务时自动绑定项目。
- `run_agent` 已在语音/文字输入归一化之后、普通推理和工具池执行之前，先尝试 Work Ledger Chat Adapter。
- 命中 Work Ledger 命令后会直接：
  - 执行 Work Ledger Workflow。
  - 写入 Work Ledger evidence。
  - 写入 Cognitive Kernel ledger 事件 `work_ledger_chat_command_handled`。
  - 关闭当前 TurnClosure。
  - 返回聊天可见结果。
- 普通聊天不会被误拦截。

涉及文件：

- `l3_node/work_ledger_chat.py`
- `l3_node/agent_core.py`
- `tests/unit/test_work_ledger_chat.py`

验证结果：

- Python 模块编译通过：
  - `l3_node/work_ledger.py`
  - `l3_node/work_ledger_http.py`
  - `l3_node/work_ledger_chat.py`
  - `l3_node/agent_core.py`
- 单元测试通过：
  - `tests/unit/test_work_ledger_mvp.py`
  - `tests/unit/test_work_ledger_chat.py`
- 测试结果：`3 passed`

当前边界：

- 聊天入口目前支持显式账本命令，尚未做模糊语义触发，避免误把普通聊天写入工作账本。
- “继续昨天任务”当前优先读取活动任务，否则读取最近关闭任务；后续需要结合 Memory Recall 选择更符合语义的历史任务。
- 还没有接入 Codex / Cursor 对话导入，下一节点开始做过程采集增强。

### 下一节点：Node 3：过程采集增强

目标：

- 把 Work Ledger 从“用户手动记录 + Git / 文件快照”升级为“真实工作过程采集”。
- 优先接入低风险、稳定来源：
  - Git 当前 diff 摘要。
  - 最近修改文件内容片段。
  - Codex / Cursor 任务输入输出的可导入记录。
  - 用户手动补充的关键决策。
- 生成输出时区分：
  - 事实证据。
  - 用户明确确认。
  - 系统推断。
  - 待确认内容。

## Node 3 执行记录：过程采集增强

状态：已完成 MVP。

本节点完成内容：

- Work Ledger 现在不只记录“哪些文件变了”，还会采集关键文件内容片段。
- 新增文本文件片段采集：
  - 支持 `.py`、`.ts`、`.tsx`、`.rs`、`.md`、`.json`、`.yaml`、`.ps1`、`.env` 等常见工程文本文件。
  - 自动跳过 `node_modules`、`target`、`dist`、`.git` 等大目录。
  - 自动跳过过大文件、非文本文件和项目目录外路径。
- 新增风险候选扫描：
  - 识别 `TODO`、`FIXME`、`BUG`、`error`、`failed`、`失败`、`异常`、`未实现`、`待处理` 等风险词。
  - 在 evidence 中记录文件路径、行号、命中关键词和原始行内容。
- 新增 AI 工具过程导入：
  - 支持 `导入Codex记录：...`
  - 支持 `导入Cursor记录：...`
  - 支持 `导入AI记录：...`
  - 导入内容以 `ai_work_trace` evidence 存储，可信度为 `user_confirmed`。
- 生成日报时新增：
  - 文件内容线索。
  - 风险候选。
  - AI 工具过程导入。
  - 证据可信度分布：用户明确确认、系统观察事实、系统推断、待确认。
- 生成 Codex / Cursor 续写任务书时新增：
  - 文件片段摘要。
  - 风险候选。
  - 已导入的 Codex / Cursor / AI 工具过程记录。

涉及文件：

- `l3_node/work_ledger.py`
- `l3_node/work_ledger_chat.py`
- `tests/unit/test_work_ledger_mvp.py`
- `tests/unit/test_work_ledger_chat.py`

验证结果：

- Python 模块编译通过：
  - `l3_node/work_ledger.py`
  - `l3_node/work_ledger_chat.py`
  - `l3_node/agent_core.py`
- 单元测试通过：
  - `tests/unit/test_work_ledger_mvp.py`
  - `tests/unit/test_work_ledger_chat.py`
- 测试结果：`3 passed`

当前边界：

- Codex / Cursor 过程记录目前采用用户粘贴导入，尚未自动读取外部应用会话。
- 文件片段采集是安全摘要，不做完整源码搬运，避免日报过长。
- 风险候选只是关键词证据，不直接下结论，后续需要接大模型做“风险解释”和“下一步建议”。

### 下一节点：Node 4：日报与续写 Prompt 的大模型整理

目标：

- 在证据已经采集完整的基础上，让大模型只做“整理表达”，不负责编造事实。
- 输入给模型的内容必须区分：
  - 事实证据。
  - 用户确认。
  - 系统推断。
  - 待确认。
- 输出要生成：
  - 更像人写的日报。
  - 更适合第二天喂给 Codex / Cursor 的续写任务书。
  - 一段可直接发送到 Lark 的短版汇报。
- 模型输出必须经过质量门控：
  - 不得出现证据中没有的文件、结论、测试结果。
  - 不得把风险候选写成已确认问题。
  - 不得输出过长、截断或 Markdown 残片。

## Node 4 执行记录：日报与续写 Prompt 的大模型整理

状态：已完成 MVP。

本节点完成内容：

- 新增 `l3_node/work_ledger_llm.py`，作为 Work Ledger 的模型整理层。
- 模型职责被限定为“整理表达”，事实来源仍然只来自 Work Ledger evidence。
- 基础输出永远保留：
  - `daily_report.md`
  - `codex_continuation_prompt.md`
- 当 `JACHIN_WORK_LEDGER_LLM_ENABLED` 未关闭且存在 DashScope/Qwen API key 时，会额外生成：
  - `enhanced_daily_report.md`
  - `enhanced_continuation_prompt.md`
  - `lark_brief.txt`
  - `llm_quality_report.json`
- 默认模型来自：
  - `JACHIN_WORK_LEDGER_LLM_MODEL`
  - 或 `LLM_COMPLEX_MODEL`
  - 兜底为 `qwen-max`
- 模型输入明确区分：
  - session 基本信息。
  - Git 状态和最近提交。
  - 最近文件。
  - 文件内容片段。
  - 风险候选。
  - 用户手动确认记录。
  - Codex / Cursor / AI 工具过程导入。
  - 证据可信度分布。
- 新增质量门控：
  - 必须输出 `daily_report`、`continuation_prompt`、`lark_brief`。
  - 拦截 Markdown 代码块。
  - 拦截超长或疑似截断的 Lark 短版。
  - 拦截表格残片。
  - 拦截 evidence 中不存在的文件路径。
  - 对“把风险候选写成已确认问题”的倾向给出 warning。
- 控制台 `今日工作台` 的 Outputs 区域现在展示：
  - 基础日报。
  - 基础续写 Prompt。
  - 增强日报。
  - 增强续写 Prompt。
  - Lark 短版。
  - 质量门控报告。

涉及文件：

- `l3_node/work_ledger_llm.py`
- `l3_node/work_ledger.py`
- `l3_node/work_ledger_chat.py`
- `clients/desktop/src/console/pages/WorkLedgerPanel.tsx`
- `tests/unit/test_work_ledger_llm_refinement.py`

验证结果：

- Python 模块编译通过：
  - `l3_node/work_ledger.py`
  - `l3_node/work_ledger_chat.py`
  - `l3_node/work_ledger_llm.py`
- 单元测试通过：
  - `tests/unit/test_work_ledger_mvp.py`
  - `tests/unit/test_work_ledger_chat.py`
  - `tests/unit/test_work_ledger_llm_refinement.py`
- 测试结果：`5 passed`
- 前端 TypeScript 检查通过：
  - `clients/desktop: npx tsc --noEmit --pretty false`

当前边界：

- 单元测试中模拟了模型返回；尚未跑真实 DashScope/Qwen live 调用。
- 模型整理当前只在生成输出时触发，不做后台定时润色。
- 质量门控是工程规则版，后续可增加更严格的事实核查器。

### 下一节点：Node 5：任务结束入口与日常使用闭环

目标：

- 让用户真正每天能用起来：
  - 开始工作。
  - 中途自动或手动记录。
  - 下班一键结束。
  - 生成日报、Lark 短版、明日 Codex/Cursor 续写任务书。
- 增加“结束今天工作”入口，自动选择当天活动任务并生成最终输出。
- 在控制台增加更明显的日常操作按钮：
  - 开始今天工作。
  - 记录关键进展。
  - 结束并生成日报。
  - 打开 Lark 短版。
- 增加真实 live smoke：
  - 启动一个任务。
  - 修改一个测试文件。
  - 导入一段 Codex 记录。
  - 结束任务并生成基础/增强输出。

## Node 5 执行记录：任务结束入口与日常使用闭环

状态：已完成 MVP。

本节点完成内容：

- Work Ledger 基础输出现在稳定包含：
  - `daily_report.md`
  - `codex_continuation_prompt.md`
  - `lark_brief.txt`
- `lark_brief.txt` 不再强依赖大模型；即使关闭 LLM 或网络不可用，也会生成一份基于 Git、文件和用户记录的基础短版。
- 当大模型整理通过质量门控时，仍会用增强版短文覆盖基础 `lark_brief.txt`。
- 新增安全输出读取接口：
  - `GET /api/v1/work-ledger/sessions/{session_id}/outputs/{output_key}`
  - 只允许读取 Work Ledger 输出目录内的白名单文件，避免任意文件读取。
- 聊天入口新增日常命令：
  - `开始今天工作`
  - `开始今日工作`
  - `结束并生成日报`
  - `查看 Lark 短版`
  - `复制 Lark 短版`
- 控制台 `今日工作台` 增加：
  - `开始今天工作` 按钮。
  - `复制 Lark 短版` 按钮。
  - Lark 短版预览区。
- 复制短版时会自动生成最新输出，再读取 `lark_brief.txt`，并尝试写入剪贴板。

涉及文件：

- `l3_node/work_ledger.py`
- `l3_node/work_ledger_http.py`
- `l3_node/work_ledger_chat.py`
- `clients/desktop/src/console/pages/WorkLedgerPanel.tsx`
- `tests/unit/test_work_ledger_daily_loop.py`

验证结果：

- Python 模块编译通过：
  - `l3_node/work_ledger.py`
  - `l3_node/work_ledger_chat.py`
  - `l3_node/work_ledger_http.py`
- 单元测试通过：
  - `tests/unit/test_work_ledger_mvp.py`
  - `tests/unit/test_work_ledger_chat.py`
  - `tests/unit/test_work_ledger_llm_refinement.py`
  - `tests/unit/test_work_ledger_daily_loop.py`
- 测试结果：`7 passed`
- 前端 TypeScript 检查通过：
  - `clients/desktop: npx tsc --noEmit --pretty false`

当前边界：

- 当前是单元级和类型级验证，尚未在真实桌面 UI 中跑完整的一天使用流程。
- 控制台复制依赖浏览器剪贴板权限；权限不可用时会展示预览文本。
- Codex / Cursor 过程导入仍是用户粘贴式，尚未自动监听外部 AI 工具会话。

### 下一节点：Node 6：真实日常 live smoke 与 Codex/Cursor 过程导入增强

目标：

- 用真实开发流程跑一次完整闭环：
  - 开始今天工作。
  - 采集 Git / 文件证据。
  - 手动记录关键进展。
  - 导入 Codex / Cursor 过程。
  - 结束并生成日报、Lark 短版、续写 Prompt。
  - 在控制台确认输出可读、可复制。
- 增强 Codex / Cursor 过程导入：
  - 支持从剪贴板导入。
  - 支持识别“问题、尝试、失败原因、最终结论”。
  - 把可复用经验写入 Memory Growth 原始证据池。

## Node 6 执行记录：真实日常 live smoke 与 Codex/Cursor 过程导入增强

状态：已完成 MVP。

本节点完成内容：

- 新增 AI 过程记录结构化分析：
  - `goals`：任务/目标。
  - `actions`：动作/改动。
  - `failures`：失败/阻塞。
  - `decisions`：结论/决策。
  - `next_steps`：下一步。
- `add_ai_work_trace` 现在不只保存原文，还会把结构化分析写入 evidence payload。
- 日报和 Codex/Cursor 续写 Prompt 会消费 `ai_trace_analysis`，让第二天继续任务时更容易看清：
  - 当时要做什么。
  - 做了哪些改动。
  - 失败在哪里。
  - 最终采用了什么决策。
  - 下一步要做什么。
- 新增 HTTP 导入接口：
  - `POST /api/v1/work-ledger/ai-trace`
  - 支持控制台或后续其他入口导入 Codex / Cursor / AI 工具过程。
- 控制台 `今日工作台` 增加：
  - `从剪贴板导入 Codex / Cursor 过程`
  - 前端从剪贴板读取文本后提交到 Work Ledger，后端结构化落盘。
- 新增一键 smoke 脚本：
  - `scripts/work_ledger_daily_live_smoke.py`
  - 默认使用隔离样例项目和隔离 ledger home，不污染真实工作账本。
  - 支持传入真实项目路径、trace 文件、标题、目标、备注。
  - 可用 `--no-llm` 做确定性验证。
- smoke 脚本完整验证：
  - start session。
  - add manual note。
  - import Codex trace。
  - collect snapshot。
  - generate outputs。
  - read Lark brief。
  - end session。
  - 写入 JSON 结果文件。

涉及文件：

- `l3_node/work_ledger.py`
- `l3_node/work_ledger_http.py`
- `clients/desktop/src/console/pages/WorkLedgerPanel.tsx`
- `scripts/work_ledger_daily_live_smoke.py`
- `tests/unit/test_work_ledger_daily_loop.py`

验证结果：

- Python 模块编译通过：
  - `l3_node/work_ledger.py`
  - `l3_node/work_ledger_chat.py`
  - `l3_node/work_ledger_http.py`
  - `scripts/work_ledger_daily_live_smoke.py`
- 单元测试通过：
  - `tests/unit/test_work_ledger_mvp.py`
  - `tests/unit/test_work_ledger_chat.py`
  - `tests/unit/test_work_ledger_llm_refinement.py`
  - `tests/unit/test_work_ledger_daily_loop.py`
- 测试结果：`8 passed`
- 前端 TypeScript 检查通过：
  - `clients/desktop: npx tsc --noEmit --pretty false`
- live smoke 通过：
  - 命令：`python scripts\work_ledger_daily_live_smoke.py --no-llm`
  - 输出：`ok=true`
  - evidence sources：
    - `work_session`
    - `git_snapshot`
    - `file_scan`
    - `file_content_snippets`
    - `manual_note`
    - `ai_work_trace`
    - `work_output`
  - `trace_analysis` 已覆盖：
    - `goals`
    - `actions`
    - `failures`
    - `decisions`
    - `next_steps`
  - 最新结果文件：
    - `output/work_ledger_live_smoke/work_ledger_live_smoke_1784706594.json`

当前边界：

- 剪贴板导入依赖前端运行环境的 clipboard 权限。
- 过程分析目前是轻量规则版，适合稳定落盘；复杂语义归纳仍应交给后续增强模型整理层。
- smoke 默认跑隔离样例项目；真实项目 live smoke 需要用户明确指定项目路径后执行。

### 下一节点：Node 7：日报/周报产品化与团队复用出口

目标：

- 把 Work Ledger 输出从“开发者能用”升级到“每天真的想发/想存”：
  - 自用日报。
  - Lark 团队短报。
  - 周报草稿。
  - 绩效材料条目。
  - 可复用经验候选。
- 增加输出模板选择：
  - `self_daily`
  - `team_lark`
  - `weekly`
  - `performance`
  - `methodology_candidates`
- 增加“输出回流”：
  - 把用户采纳的日报/周报/经验沉淀回 AI 自生长知识系统。
  - 让下一次继续任务、周报、复盘可以召回这些高价值输出。

## Node 7 执行记录：产品化输出与回流

已完成：

- Work Ledger 输出模板从 3 类扩展为 7 类：
  - `daily_report`
  - `codex_continuation_prompt`
  - `lark_brief`
  - `team_lark_brief`
  - `weekly_report`
  - `performance_entries`
  - `methodology_candidates`
- 新增团队 Lark 简报：
  - 面向同事阅读。
  - 强调目标、主要改动、关键记录、风险提醒和证据来源。
- 新增周报草稿：
  - 面向周报/汇报场景。
  - 把目标、完成事项、AI 协作过程、风险和下周建议串起来。
- 新增绩效材料条目：
  - 面向工作记录、绩效、阶段复盘。
  - 输出可复制的条目，而不是泛泛总结。
- 新增方法论候选：
  - 汇总用户确认经验、AI 过程提炼和风险模式。
  - 明确标注这是候选，不直接伪装成长期知识结论。
- 新增采纳回流能力：
  - `adopt_work_output(...)`
  - 用户采纳某个输出后，会写入 `work_output_adoption` 证据。
  - 采纳证据使用 `user_confirmed` trust level。
  - 同时写入 Cognitive Kernel ledger 和 Memory Growth raw event。
- 新增 HTTP 接口：
  - `POST /api/v1/work-ledger/adopt-output`
- 控制台 Work Ledger 页面新增：
  - 团队简报、周报草稿、绩效条目、方法论候选路径展示。
  - “采纳团队简报 / 采纳周报 / 采纳方法论候选”按钮。
- live smoke 增加采纳回流步骤：
  - 生成输出后采纳 `team_lark_brief`。
  - smoke 结果记录 `adopted_evidence_id`。
- 新增测试：
  - `tests/unit/test_work_ledger_productized_outputs.py`

当前边界：

- 周报目前仍以单个 session 为基础；还不是跨多天、多任务自动聚合。
- 方法论候选只做证据驱动整理；还没有接入人工确认队列和自动去重治理。
- 采纳回流已经进入 Memory Growth raw event，但后续召回、晋升、归档还要继续和长期记忆治理打通。

### 下一节点：Node 8：多日聚合与 Work Ledger Recall

目标：

- 支持从最近 7 / 14 / 30 天 Work Ledger sessions 聚合周报。
- 建立 Work Ledger 输出索引：
  - 已采纳输出。
  - 高频项目。
  - 最近任务。
  - 方法论候选。
- 增加工作记忆召回：
  - “上次这个项目做到哪了？”
  - “昨天 Codex 继续任务 Prompt 是什么？”
  - “最近有哪些可复用经验？”
- 把采纳回流与 Memory Growth 晋升队列打通：
  - user_confirmed 优先进入可晋升候选。
  - 系统推断保持待确认。
  - 被用户否定的内容不能进入长期结论。

## Node 8 执行记录：多日聚合与 Work Ledger Recall

已完成：

- 新增最近窗口聚合：
  - `list_recent_sessions(days)`
  - 支持最近 7 / 14 / 30 天等时间窗口。
- 新增 Work Ledger Recall Index：
  - `build_work_ledger_recall_index(days)`
  - `write_work_ledger_recall_index(days)`
  - 聚合 session、项目分布、证据来源分布、用户采纳输出、方法论候选、手动记录、AI 过程信号。
- 新增工作记忆召回：
  - `recall_work_ledger(query, days, limit)`
  - 可以从 session、manual note、adopted output、methodology candidate、AI signal 中召回相关内容。
  - 初版使用透明的关键词评分和 trust level 加权。
- 新增 HTTP 接口：
  - `GET /api/v1/work-ledger/recall-index?days=7`
  - `POST /api/v1/work-ledger/recall`
- 控制台 Work Ledger 页面新增：
  - Work Recall 查询框。
  - 7 / 14 / 30 天窗口选择。
  - Recall Result 结果卡片，展示命中类型、trust level、score、摘要。
- live smoke 增加 recall 验证：
  - 写入 recall index。
  - 对 “Work Ledger daily loop” 执行召回。
  - smoke 结果记录 `recall_hit_count` 和 top hits。
- 新增测试覆盖：
  - 多 session。
  - 用户采纳输出。
  - recall 跨 session 命中已采纳方法论/手动记录。

当前边界：

- Recall 精排仍是轻量关键词/规则评分，还没有接入 embedding 归一化点积或小模型 rerank。
- 周报聚合已经有索引基础，但还没有生成“跨多日真实周报文件”。
- Recall 结果还未直接接入聊天入口，例如“上次做到哪了”现在主要通过 API / 控制台验证。

### 下一节点：Node 9：跨多日周报生成与聊天入口接入

目标：

- 基于最近 7 / 14 / 30 天 recall index 生成跨多日周报。
- 聊天入口支持：
  - “上次这个项目做到哪了？”
  - “最近有什么可复用经验？”
  - “生成这周工作周报。”
  - “给 Codex 生成继续任务提示词。”
- Recall 排序升级：
  - 第一层关键词召回。
  - 第二层规则评分。
  - 第三层可选 embedding / 小模型 rerank。
- Evidence 中记录：
  - 使用了哪些历史 session。
  - 哪些内容是 user_confirmed。
  - 哪些内容只是 system_observed / system_inferred。

## Node 9 执行记录：跨多日周报与聊天入口

已完成：

- 新增跨多日周报生成：
  - `build_multi_day_weekly_report(index)`
  - `generate_multi_day_weekly_report(days)`
  - 基于 recall index 聚合最近 7 / 14 / 30 天 sessions。
- 跨多日周报包含：
  - 工作分布。
  - 本期任务。
  - 已采纳成果。
  - 关键过程记录。
  - 风险、决策与下一步。
  - 可复用方法论候选。
  - 证据边界。
- 新增 HTTP 接口：
  - `POST /api/v1/work-ledger/weekly-report`
- 控制台 Work Ledger 页面新增：
  - “生成多日周报”按钮。
  - Weekly Report Preview。
- 聊天入口新增明确命令：
  - “生成这周工作周报”
  - “生成周报”
  - “上次做到哪了”
  - “最近有什么可复用经验”
  - “召回工作记忆”
- `继续任务` 支持关键词召回：
  - 例如“继续语音上下文任务”会先从最近 30 天 Work Ledger Recall 中找相关 session。
- live smoke 增加：
  - recall index 写入。
  - recall 命中验证。
  - 跨多日周报生成验证。
- 新增测试覆盖：
  - 聊天 recall 命令。
  - 聊天 weekly 命令。
  - 带关键词的继续任务。

当前边界：

- Recall 排序仍是关键词 + trust level 的透明规则，尚未接入 embedding 精排。
- 聊天命令仍偏显式，暂未开放太模糊的自动触发，避免误把普通聊天写入 Work Ledger。
- 周报是证据驱动基础版，还没有接入 LLM 编辑器做更像人写的增强版。

### 下一节点：Node 10：Recall 精排与 LLM 周报编辑器

目标：

- Recall 增加三层检索：
  - 关键词召回。
  - 规则评分。
  - 可选 embedding / 小模型精排。
- 增加 LLM 周报编辑器：
  - 只基于 recall index 和证据整理表达。
  - 不允许编造。
  - 输出“可直接发领导/团队”的周报。
- 聊天入口支持更自然的表达：
  - “帮我整理这周干了什么”
  - “把最近 Jachin 的进展写成周报”
  - “明天让 Codex 接着哪里做”
- Evidence 记录召回链路：
  - query。
  - 命中条目。
  - trust level。
  - 采用/舍弃原因。

## Node 10 执行记录：Recall 精排与 LLM 周报编辑器

已完成：

- Work Ledger Recall 升级为三层排序：
  - 第一层：关键词召回。
  - 第二层：规则评分，包括 trust level、命中类型、近期性。
  - 第三层：本地归一化向量点积精排。
- Recall hit 增加可解释字段：
  - `score`
  - `score_parts`
  - `ranking_reason`
- 控制台 Recall Result 展示排序依据：
  - 用户可以看到命中是因为 keyword、rule、vector 还是 user_confirmed。
- 新增 LLM 周报编辑器：
  - `build_weekly_digest(index)`
  - `refine_weekly_report_with_llm(index, baseline_report)`
  - `validate_weekly_report_outputs(...)`
- 跨多日周报生成支持增强版：
  - 基础周报稳定生成。
  - 如果 LLM 可用，则生成 enhanced weekly report。
  - 如果 LLM 失败或质量门控失败，不影响基础周报。
- LLM 周报质量门控包括：
  - 必须有 `weekly_report`。
  - 不允许代码块、控制字符、省略号/截断。
  - 不允许编造不存在的 session id。
  - 检查证据边界、风险/下一步表达。
- live smoke 记录：
  - 基础周报路径。
  - 增强周报路径。
  - 周报质量报告路径。
- 新增测试覆盖：
  - Recall 三层排序元数据。
  - hit 的 `score_parts`。
  - fake LLM 周报编辑器写出增强版文件。

当前边界：

- 第三层精排是本地轻量 hash/ngram 向量，不是大型 embedding 模型；优点是零依赖、快、可离线，缺点是语义泛化仍有限。
- LLM 周报编辑器默认受 `JACHIN_WORK_LEDGER_LLM_ENABLED` 和 DashScope key 控制；未启用时只输出基础周报。
- 聊天入口仍以明确 Work Ledger 表达为主，下一步再扩大自然语言触发覆盖。

### 下一节点：Node 11：自然语言 Work Ledger Goal Interpreter

目标：

- 让用户更自然地说：
  - “帮我整理这周干了什么”
  - “今天下班了，给我生成一份日报”
  - “明天让 Codex 接着这个项目做”
  - “把最近 Jachin 的工作写成周报”
- 不再只依赖固定短语，而是通过 Goal Interpreter 识别：
  - 是否是 Work Ledger 任务。
  - 是 start / note / collect / generate / recall / weekly / continue 哪一类。
  - 缺少什么信息。
  - 是否可以用历史项目记忆补全。
- 保持安全边界：
  - 普通闲聊不写入 Work Ledger。
  - 不确定时只问一个澄清问题。

## Node 11A 执行记录：结束工作七问标准输出

本节点先补齐 Work Ledger 每天真正可用的结束工作结构。用户结束任务时，系统不应只给“改了哪些文件”，而要稳定回答七个工作复盘问题：

1. 今天主要做了什么。
2. 改了哪些模块。
3. 哪些任务完成了。
4. 哪些问题卡住了。
5. 明天接着做什么。
6. 这段内容怎么发日报。
7. 这段内容怎么沉淀成方法论。

实现内容：

- 新增 `work_review.md` 输出，作为 Work Ledger 的核心复盘文件。
- `work_review.md` 基于 Git、文件扫描、用户补充、AI 过程导入和风险候选生成，不凭空编造。
- `generate_work_outputs()` 会和日报、续写 Prompt、Lark 简报、周报、绩效材料、方法论候选一起生成 `work_review.md`。
- 聊天输出汇总和控制台详情页都展示“工作复盘七问”文件路径。
- 方法论沉淀不再只是宽泛总结，而是区分：
  - 用户确认的经验。
  - 失败/阻塞可沉淀为 recovery playbook。
  - 下一步可沉淀为 checklist。
  - 证据不足时保持待确认。
- 单测新增断言，保证七个标题不会丢失。

当前边界：

- 七问目前是确定性证据整理版；LLM 可以后续做润色，但不能替代证据采集。
- 如果用户没有补充“完成了什么 / 卡住了什么”，系统只会基于 AI trace 和风险候选推断，并明确证据边界。

下一节点仍然是 Node 11：自然语言 Work Ledger Goal Interpreter，把“开始记录 / 结束工作 / 生成日报 / 明天继续 / 最近做了什么”这些自然说法正式接入 Work Ledger。

## Node 11 执行记录：自然语言 Work Ledger Goal Interpreter

本节点把 Work Ledger 从固定命令触发升级为自然语言目标识别。

实现内容：

- 新增 `l3_node/work_ledger_goal_interpreter.py`。
- 解释器只负责识别目标，不直接执行任务；执行仍由 `work_ledger_chat.py` 统一处理。
- 显式命令优先，自然语言解释器作为兜底，避免破坏已有入口。
- 识别结果包含：
  - `kind`
  - `confidence`
  - `reason`
  - `raw_text`
  - `days`
  - `project_path`
  - `query`
- 已支持自然语言：
  - “开始记录今天 Jachin 的开发工作”
  - “今天下班了，给我生成一份日报”
  - “帮我整理这周干了什么”
  - “把最近 Jachin 的工作写成周报”
  - “明天让 Codex 接着这个项目做”
  - “看看之前语音常开做到哪了”
  - “帮我生成今天工作的 Lark 简报”
- 加入闲聊保护：
  - “你好，今天怎么样”不会被误识别为 Work Ledger。
- 新增单测：
  - 自然语言分类测试。
  - 自然语言启动任务并生成 `work_review.md` 的执行测试。

当前边界：

- 这是轻量规则解释器，不是 LLM 语义解析器；优点是稳定、快、可测试，缺点是对更含糊的表达还需要继续扩展。
- 下一步可以接入项目记忆和用户习惯，让“这个项目”“昨天那个任务”“最近主要工作”自动补全到具体 Work Ledger session。

### 下一节点：Node 12：项目记忆与任务续接

目标：

- 让 Work Ledger 能记住：
  - Jachin 对应哪个项目路径。
  - 最近活跃的是哪个任务。
  - 用户说“这个项目 / 上次那个 / 昨天的任务”时应该续接哪个 session。
- 让“继续昨天那个任务”“把最近 Jachin 的工作整理一下”不只是关键词召回，而是先用项目记忆补全上下文。

## Node 12 执行记录：项目记忆与任务续接

本节点把 Work Ledger 从“单次任务记录”升级为“能记住项目并续接上下文”的工作记忆。

实现内容：

- 新增 `l3_node/work_ledger_project_memory.py`，独立维护 Work Ledger 项目记忆文件。
- 每次开始或结束任务时，自动从 session 中沉淀：
  - 项目名。
  - 项目路径。
  - 最近 session。
  - 用户目标。
  - 可复用别名。
- 项目记忆写入 `project_memory.json`，不依赖 Git 是否提交。
- 已支持用户说：
  - “继续这个项目”
  - “继续上次那个任务”
  - “继续 Jachin”
  - “把最近 Jachin 的工作整理一下”
- Goal Interpreter 会优先用项目记忆补全缺失路径，避免用户每次重复输入 `D:\Projects\...`。
- Work Ledger Chat 的 continue 逻辑会先查当前 active session，再查项目记忆，再回退到最近 session。
- `status()` 接口返回 `project_memory`，控制台新增 Project Memory 区域，能看到系统当前记住的项目、路径和最近续接任务。
- 新增单测覆盖：
  - 项目别名写入与路径复用。
  - “继续 Jachin” 自动命中之前的 session。

当前边界：

- 项目记忆仍是本地 JSON 轻量实现，还不是统一 Memory Nexus 图谱节点。
- 别名推断以稳定规则为主，后续可以接入用户确认和 Memory Trust Layer。
- 多个同名项目暂按最近任务优先，后续需要冲突提示和显式选择。

### 下一节点：Node 13：任务上下文自动补全与 AI 过程导入体验

目标：

- 让用户不需要精确描述上下文，只说“继续昨天那个”“把 Codex 做的整理一下”也能进入正确任务。
- 进一步打通：
  - 项目记忆。
  - AI trace 导入。
  - 日报/周报/继续任务书。
  - 方法论沉淀。
- 提供更顺手的“导入 Codex/Cursor 最近过程”体验，让 Jachin 真正成为 AI 工作过程的账本，而不是只靠用户手写记录。

## Node 13 执行记录：任务上下文自动补全与 AI 过程导入体验

本节点把 Work Ledger 从“能生成日报”继续推进到“能把 AI 工作过程整理成下一轮可续接上下文”。

实现内容：

- 新增 `context_pack.md` 输出，定位为“明天继续任务 / 发给 Codex 或 Cursor 的上下文包”。
- `context_pack.md` 汇总：
  - 当前任务、项目路径和目标。
  - 项目记忆中的 alias、最近 session 和置信度。
  - 已经推进的动作、决策、用户确认记录。
  - Git / 文件扫描发现的关键文件。
  - 文件片段、TODO / failed / error 等风险候选。
  - 下一轮建议动作。
  - 可直接复制给 Codex / Cursor 的下一轮任务书。
  - 证据边界说明。
- `generate_work_outputs()` 统一生成 `context_pack.md`，并写入 session 的 `output_paths.context_pack`。
- 控制台 Outputs 区域新增“任务上下文包”路径。
- 聊天入口新增自然触发：
  - “生成上下文包”
  - “生成任务上下文”
  - “生成继续任务书”
  - “生成下一轮任务书”
  - “明天给 Codex 的任务书”
- 聊天入口新增更自然的 AI 过程导入前缀：
  - “导入 Codex 过程”
  - “导入 Cursor 过程”
  - “整理这段 Codex 过程”
- live smoke 的成功条件加入 `context_pack`，避免上下文包断链时误判通过。
- 新增单测覆盖：
  - context pack 文件生成。
  - context pack 内容包含 AI trace、风险文件和下一轮任务书。
  - 聊天命令“生成上下文包”能返回路径和预览。

当前边界：

- AI trace 仍然依赖用户粘贴/导入文本；下一步要做“更低摩擦的自动导入”。
- Context Pack 是证据整理版，不替代 Codex/Cursor 的真实代码理解。
- 如果用户没有导入 Codex/Cursor 过程，context pack 仍会基于 Git / 文件扫描生成，但“决策/失败/下一步”会比较弱。

### 下一节点：Node 14：低摩擦 AI 工作过程采集

目标：

- 让用户不用手动整理 Codex/Cursor 过程，Jachin 能更轻地收集：
  - 剪贴板中的 AI 输出。
  - 用户粘贴的大段对话。
  - 终端日志中的关键片段。
  - Git diff 和最近文件变化。
- 导入后自动形成：
  - Context Pack。
  - Daily Report。
  - Lark Brief。
  - Methodology Candidates。
- 重点验证：不上传 Git 时，Jachin 仍能通过本地文件变化、用户确认和 AI trace 知道今天做了什么。

## Node 14 执行记录：低摩擦 AI 工作过程采集

本节点把 Work Ledger 从“用户手动粘贴一段 AI trace”推进到“可以导入真实工作过程材料”。目标不是替代 Codex / Cursor，而是把它们做过什么、失败过什么、下一步是什么，沉淀成 Jachin 自己可回放、可续接、可写日报的工作账本。

实现内容：

- 新增统一后端入口 `import_ai_work_process()`：
  - 支持直接文本。
  - 支持文件路径。
  - 支持终端日志 / Codex 输出 / Cursor 输出 / 普通工作过程片段。
  - 自动识别来源工具：Codex、Cursor、Claude、Terminal、Git 或 AI。
- 新增 `prepare_work_process_import()`：
  - 从大段噪声文本中筛出真正有价值的工作信号。
  - 保留目标、变更、失败、决策、下一步、命令、文件路径、diff / error / TODO 等高价值行。
  - 对重复行去重，限制最大导入长度，避免把整段无意义日志灌入记忆。
- 导入后自动写 Evidence：
  - 原始来源。
  - 原始行数。
  - 选中行数。
  - 丢弃行数。
  - 信号数量。
  - 推断出的工具名。
- 导入后可自动执行：
  - `collect_snapshot`
  - `generate_work_outputs`
  - 刷新 `daily_report.md`
  - 刷新 `work_review.md`
  - 刷新 `context_pack.md`
  - 刷新 Lark brief / team brief / methodology candidates。
- 新增 HTTP 接口：
  - `POST /api/v1/work-ledger/import-process`
  - 控制台可以直接导入粘贴文本或日志文件路径。
- Work Ledger 控制台新增 `AI Process Import` 区域：
  - 可粘贴 Codex / Cursor / 终端输出。
  - 可填写日志文件路径。
  - 一键导入并刷新上下文包。
- 聊天入口新增日志导入能力：
  - “导入终端日志 D:\xxx.log”
  - “导入 Codex 日志 D:\xxx.log”
  - “导入 Cursor 过程 D:\xxx.log”
- live smoke 已升级为真实过程导入：
  - 先创建本地样例项目。
  - 写入文件变化。
  - 导入一段 Cursor/Codex 风格工作过程。
  - 生成日报、上下文包、Lark brief 和团队简报。
  - 验证 recall / adopted output / evidence source 全链路。

验证结果：

- Python 编译检查通过：
  - `python -m py_compile l3_node\work_ledger.py l3_node\work_ledger_chat.py l3_node\work_ledger_http.py l3_node\work_ledger_llm.py l3_node\work_ledger_goal_interpreter.py l3_node\work_ledger_project_memory.py scripts\work_ledger_daily_live_smoke.py`
- Work Ledger 单测通过：
  - `18 passed`
- 前端类型检查通过：
  - `npx tsc --noEmit --pretty false`
- live smoke 通过：
  - `ok: true`
  - 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_live_smoke\work_ledger_live_smoke_1784711534.json`
  - 输出包含：
    - `daily_report.md`
    - `work_review.md`
    - `context_pack.md`
    - `lark_brief.txt`
    - `team_lark_brief.md`
    - `methodology_candidates.md`

当前边界：

- 目前是低摩擦导入，不是完全自动监听 Codex / Cursor 的所有窗口和会话。
- 终端日志筛选是工程规则，不等于完整语义理解；后续可接轻量模型做二次归纳。
- 暂时不会自动上传或读取外部 AI 产品的隐私会话，只处理用户显式导入的材料。

### 下一节点：Node 15：自动候选采集与结束工作一键流程

目标：

- 让用户每天真正用起来，而不是记得一堆命令。
- 在“结束今天工作”时自动收集候选材料：
  - 最近 Git 变更。
  - 最近修改文件。
  - 最近 Work Ledger session。
  - 用户手动补充。
  - 剪贴板或指定日志中的 AI 工作过程。
- 先生成预览，不直接把所有东西写死到长期记忆。
- 用户确认后统一输出：
  - 今日工作日报。
  - 明天继续任务书。
  - Codex / Cursor 续接 prompt。
  - Lark 可发送短报。
  - 可沉淀的方法论候选。
- 增加隐私与噪声保护：
  - 对明显 token、key、cookie、手机号、邮箱等敏感内容做过滤或提示。
  - 对低价值日志只做摘要，不进入长期记忆。

## Node 15 执行记录：自动候选采集与结束工作一键流程

本节点把 Work Ledger 从“能生成各种输出”推进到“每天结束工作时可以先预览、再确认、再统一生成工作包”。这一步是日常可用性的关键：用户不需要想清楚要点哪个按钮、导入哪个材料，而是围绕“收工”这个真实动作完成闭环。

实现内容：

- 新增 `build_end_day_preview()`：
  - 读取当前 session。
  - 汇总已有 Evidence 数量。
  - 重新扫描 Git 状态。
  - 重新扫描任务期内最近文件。
  - 可选读取用户粘贴的 AI / 终端过程文本。
  - 可选读取指定日志文件路径。
  - 输出候选证据组，而不是直接写死到最终结果。
- 新增 `finalize_end_day_package()`：
  - 用户确认后再导入过程材料。
  - 重新采集 Git / 文件 / 片段证据。
  - 统一生成日报、复盘七问、Context Pack、Codex/Cursor 续接任务书、Lark brief、团队简报、方法论候选。
  - 可选择关闭当前 Work Ledger session。
- 新增敏感信息治理：
  - `scan_sensitive_material()` 检测 API key、Bearer token、private key、cookie、邮箱、手机号。
  - API key / token / private key / cookie 默认阻断导入。
  - `redact_sensitive_material()` 用于必要时脱敏。
  - Preview 只展示风险类型和数量，不记录敏感原文。
- 新增 Evidence 类型：
  - `end_day_preview`
  - `end_day_package`
- 新增 HTTP 接口：
  - `POST /api/v1/work-ledger/end-day-preview`
  - `POST /api/v1/work-ledger/end-day-finalize`
- 聊天入口新增：
  - “收工预览”
  - “结束工作预览”
  - “确认收工”
  - “确认结束今天工作”
- Work Ledger 控制台新增：
  - “收工预览”按钮。
  - “确认收工”按钮。
  - End Day Preview 卡片，展示候选证据组、安全检查状态、样例文件/过程片段。
- live smoke 已升级：
  - 从“直接导入 AI trace”改为“先收工预览，再确认生成工作包”。
  - smoke 结果写入 `end_day_preview`、`end_day_package`、`process_import` 等证据链。

验证结果：

- Python 编译检查通过。
- Work Ledger 单测通过：
  - `19 passed`
- 前端类型检查通过：
  - `npx tsc --noEmit --pretty false`
- live smoke 通过：
  - `ok: true`
  - 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_live_smoke\work_ledger_live_smoke_1784712160.json`
  - Evidence sources 包含：
    - `end_day_preview`
    - `end_day_package`
    - `ai_work_trace`
    - `git_snapshot`
    - `file_scan`
    - `file_content_snippets`
    - `work_output`
    - `work_output_adoption`

当前边界：

- 目前仍是用户主动点击“收工预览 / 确认收工”，不是后台自动判断用户已经下班。
- AI / 终端过程材料需要用户粘贴或填写日志路径，后续再做自动候选发现。
- 敏感信息规则是第一版静态规则，后续需要接入更多公司内部敏感模式和可配置策略。

### 下一节点：Node 16：自动候选发现与日常工作入口收敛

目标：

- 让 Jachin 在用户点击“收工预览”时自动提示可导入候选：
  - 最近的 Codex / Cursor 导出文件。
  - 最近的 L3 / Work Ledger 日志。
  - 最近修改的 Markdown / 任务文档。
  - 最近生成但未采纳的日报 / 简报 / context pack。
- 控制台只保留少量高频入口：
  - 开始今天工作。
  - 补充记录。
  - 导入 AI 过程。
  - 收工预览。
  - 确认收工。
- 把 Work Ledger 输出和 Jachin 总体记忆进一步接起来：
  - 被采纳的日报 / 方法论候选进入长期记忆。
  - 未确认的系统推断保留为 floating。
  - 敏感或被用户否定的内容不进入召回候选。

## Node 16 执行记录：自动候选发现与日常工作入口收敛

本节点把“收工预览”从纯手工补材料，推进到系统自动发现候选材料。核心原则是：Jachin 可以主动提示“这些可能是今天的工作过程”，但不能擅自把敏感日志或不确定材料写进正式日报；用户点选后才进入导入链路。

已实现内容：

- `build_end_day_preview()` 默认启用自动候选发现：
  - 项目目录。
  - Work Ledger 输出目录。
  - Jachin `output`。
  - Jachin `logs`。
  - 用户 `.jachin` 目录。
- 新增 `discover_work_process_candidates()`：
  - 扫描最近的 `.log`、`.txt`、`.md`、`.jsonl`、`.out`、`.err` 文件。
  - 优先识别 Codex、Cursor、terminal、powershell、Work Ledger、smoke、debug、trace、report、brief、context、daily、weekly 等过程材料。
  - 根据文件名、目录来源、大小、修改时间和工作信号打分。
  - 读取候选文件摘要，不直接导入全文。
  - 对候选内容执行敏感信息扫描。
- 新增候选去重：
  - 对 Work Ledger 自己生成的日报、周报、context pack、Codex continuation prompt 等文件做降噪。
  - 避免同一轮输出在预览里刷屏。
- 安全策略调整：
  - 自动发现的候选文件即使包含敏感信号，也只作为“可选候选”提示，不阻断整个收工预览。
  - 用户显式选择导入后，仍然执行敏感内容阻断和脱敏策略。
- 新增 HTTP 接口：
  - `POST /api/v1/work-ledger/process-candidates`
- 控制台增强：
  - End Day Preview 中展示自动发现候选的文件路径、来源、评分原因。
  - 每个候选增加“使用这个文件”按钮，可以一键填入 AI 过程导入路径。
- 新增测试覆盖：
  - 自动发现真实工作过程候选。
  - 候选含敏感 API key 时不阻断预览。
  - 未经用户确认的候选不会自动写入 `ai_work_trace`。

验证结果：

- Python 编译检查通过：
  - `python -m py_compile l3_node\work_ledger.py`
- Work Ledger 单测通过：
  - `20 passed`
- 前端类型检查通过：
  - `npx tsc --noEmit --pretty false`
- live smoke 通过：
  - `ok: true`
  - 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_live_smoke\work_ledger_live_smoke_1784713321.json`
  - `end_day_preview` 已包含 `discovered_process_file` 候选。

当前边界：

- 当前自动发现仍是文件 / 日志 / 输出扫描，不是直接读取 Codex / Cursor 应用内部会话。
- 自动候选默认不自动导入，这是为了避免把敏感日志、噪声日志或旧输出误写入日报。
- 候选评分是第一版工程规则，后续需要接入用户采纳反馈，让系统知道“哪些候选真的有用”。

### 下一节点：Node 17：采纳回流与长期记忆信任层打通

目标：

- 用户在 End Day Preview 中选择并导入某个候选后，记录“候选被采纳”的反馈。
- 被采纳的日报、团队简报、方法论候选进入 `user_confirmed` 长期记忆候选。
- 系统自动发现但用户没有选择的候选保留为 `floating`，不参与高置信召回。
- 用户明确否定或敏感阻断的候选标记为 `rejected` / `blocked`，后续召回默认过滤。
- 召回时在 Evidence 中展示：
  - 这条记忆为什么被使用。
  - 是用户确认、系统推断，还是历史失败经验。

## Node 17 执行记录：采纳回流与长期记忆信任层打通

本节点把自动候选发现进一步接入“记忆可信度”。Work Ledger 不再只是发现文件，而是开始记录用户对候选材料的态度：采纳、拒绝、阻断。这样系统以后能知道哪些材料值得信，哪些只是系统猜测，哪些明确不该再进入高置信召回。

已实现内容：

- 新增候选反馈 Evidence：
  - `work_process_candidate_feedback`
  - `action=accepted`
  - `action=rejected`
  - `action=blocked`
- 新增 `record_work_process_candidate_feedback()`：
  - 记录候选文件路径。
  - 记录用户反馈动作。
  - 记录安全扫描结果。
  - 对候选摘要做脱敏后再进入 Evidence。
- 新增 `adopt_work_process_candidate()`：
  - 读取候选文件。
  - 执行敏感信息扫描。
  - 通过后导入为 `ai_work_trace`。
  - 再写入 `work_process_candidate_feedback: accepted`。
  - 可选择刷新日报、复盘、Context Pack、Lark brief 等输出。
- `finalize_end_day_package()` 增强：
  - 如果用户在收工确认时使用了候选文件路径，会自动记录 `accepted during end-day finalize`。
- Recall Index 增强：
  - 新增 `adopted_process_candidates`。
  - 新增 `rejected_process_candidates`。
  - 默认召回只使用 accepted 候选。
  - rejected / blocked 候选只进入统计和治理，不进入高置信召回。
- Recall Ranking 增强：
  - `user_rejected` 默认打到极低分，并标记 `user_rejected_filtered`。
- Memory Growth 接入：
  - `work_process_candidate_feedback` 进入 Memory Growth 原始事件。
  - accepted 候选作为高优先级治理候选。
- HTTP 接口新增：
  - `POST /api/v1/work-ledger/adopt-candidate`
  - `POST /api/v1/work-ledger/reject-candidate`
- Work Ledger 控制台新增：
  - End Day Preview 候选卡片增加“采纳并导入”。
  - End Day Preview 候选卡片增加“拒绝”。

验证结果：

- Python 编译检查通过：
  - `python -m py_compile l3_node\work_ledger.py l3_node\work_ledger_http.py`
- Work Ledger 单测通过：
  - `21 passed`
- 前端类型检查通过：
  - `npx tsc --noEmit --pretty false`
- live smoke 通过：
  - `ok: true`
  - 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_live_smoke\work_ledger_live_smoke_1784713981.json`

当前边界：

- live smoke 仍然验证主闭环；候选采纳 / 拒绝目前由单测覆盖，下一步要补进专门的候选采纳 live smoke。
- UI 的旧中文乱码仍存在于部分历史文案中，本节点只新增必要按钮和功能，没有做整页文案清洗。
- 候选反馈已经进入 Recall / Memory Growth，但还没有形成长期的“候选来源质量评分”，例如 Cursor 文件命中率、Codex continuation prompt 是否长期有用等。

### 下一节点：Node 18：候选来源质量评分与日常入口产品化

目标：

- 给每类候选来源建立质量分：
  - Codex/Cursor trace。
  - terminal log。
  - Work Ledger output。
  - 项目 Markdown。
  - Jachin runtime logs。
- 根据历史采纳 / 拒绝 / 阻断比例调整候选排序。
- 控制台增加“今日工作入口”聚合视图：
  - 开始工作。
  - 当前任务。
  - 最近候选。
  - 收工预览。
  - 一键生成可发日报。
- 补专门 live smoke：
  - 自动发现候选。
  - 采纳候选。
  - 拒绝候选。
  - 再次预览时排序变化。
  - Recall 展示 user_confirmed / user_rejected 的不同处理。

## Node 18 执行记录：候选来源质量评分核心层

本节点先完成“候选来源质量评分”的底层闭环。它解决的问题是：自动发现候选不能永远靠固定规则排序，而要根据用户过去真实采纳 / 拒绝 / 阻断的反馈，逐步知道哪类来源更可靠。

已实现内容：

- 新增 `build_work_process_candidate_source_quality()`：
  - 统计最近 30 天候选反馈。
  - 按来源聚合 accepted / rejected / blocked / total。
  - 计算 `accept_rate`。
  - 计算 `score_adjustment`，用于候选排序加权。
- 新增候选来源归因：
  - `codex_trace`
  - `cursor_trace`
  - `terminal_log`
  - `jachin_runtime_log`
  - `work_ledger_output`
  - `project_markdown`
  - `structured_log`
  - 其他来源按根目录归类。
- `discover_work_process_candidates()` 增强：
  - 每个候选附带 `quality_key`。
  - 每个候选附带历史质量记录。
  - 候选分数会叠加来源质量调整值。
- `record_work_process_candidate_feedback()` 增强：
  - 写入 `root_reason`。
  - 写入 `quality_key`。
  - 让后续质量统计可以按来源聚合。
- 新增 HTTP 接口：
  - `POST /api/v1/work-ledger/candidate-quality`

验证结果：

- Python 编译检查通过：
  - `python -m py_compile l3_node\work_ledger.py l3_node\work_ledger_http.py`
- Work Ledger 单测通过：
  - `21 passed`
- 前端类型检查通过：
  - `npx tsc --noEmit --pretty false`
- live smoke 通过：
  - `ok: true`
  - 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_live_smoke\work_ledger_live_smoke_1784714240.json`
  - `end_day_preview` 中候选已经包含 `quality_key`。

当前边界：

- 当前完成的是质量评分核心层；控制台还没有单独展示“候选来源质量榜”。
- live smoke 还没有专门覆盖“先拒绝某类候选，再次预览排序下降”的场景。
- 质量分是按来源类型聚合，还没有细化到具体项目、具体工具实例、具体文件模式。

### 下一节点：Node 19：候选质量可视化与采纳压测

目标：

- 控制台展示候选来源质量：
  - 来源类型。
  - 采纳次数。
  - 拒绝次数。
  - 阻断次数。
  - 接受率。
  - 当前分数调整。
- 增加专门 smoke：
  - 构造多个候选来源。
  - 采纳一个来源。
  - 拒绝一个来源。
  - 再次预览时验证排序发生变化。
- 把候选质量写入 Work Ledger Evidence 或 dashboard summary，方便排查为什么某个候选排在前面。
- 开始清理 Work Ledger 控制台历史乱码文案，让这条主线真正进入可日常使用状态。

## Node 19 执行记录：候选质量可视化与采纳压测

本节点把 Node 18 的来源质量分从后台统计升级成用户可见、可解释、可验证的日常能力。用户现在可以直接看到某类来源为什么排在前面，以及一次采纳或拒绝如何改变下一次收工预览的排序。

已实现内容：

- 候选来源质量汇总增强：
  - 输出 accepted / rejected / blocked / total 总计。
  - 输出来源数量、正向来源、中性来源、负向来源。
  - 输出按质量分调整排序后的 `ranked_sources`。
- 收工预览增强：
  - `candidate_quality` 随预览返回。
  - 质量快照随 `end_day_preview` Evidence 一起落盘。
  - 候选发现结果也包含本轮使用的完整质量依据。
- Work Ledger 控制台新增“候选来源质量”：
  - 展示来源类型。
  - 展示采纳、拒绝、阻断次数。
  - 展示接受率。
  - 展示当前排序分数调整。
- 候选卡片增强：
  - 展示候选最终得分。
  - 展示来源质量类型。
  - 展示历史反馈次数和分数调整。
  - 展示本轮排序依据。
- 采纳 / 拒绝闭环增强：
  - 用户操作后立即重新生成收工预览。
  - 页面立即刷新质量榜和候选顺序。
  - 用户不需要关闭页面或重新进入任务。
- 历史文案清理：
  - 本节点触达的 Work Ledger 候选操作文案统一为中文。
  - 文件按 UTF-8 读取复核，当前页面源码未发现残留乱码字节。
- 新增专用 smoke：
  - `scripts/work_ledger_candidate_quality_smoke.py`
  - 构造 Codex 与 Cursor 两类候选来源。
  - 连续采纳 Codex 来源。
  - 连续拒绝 Cursor 来源。
  - 验证下一次预览中 Codex 加分、Cursor 扣分、排序发生变化。
  - 验证质量快照进入 Evidence。

验证结果：

- Python 编译检查通过。
- Work Ledger 相关单测通过。
- 前端类型检查通过。
- 候选质量专用 smoke 通过：
  - `ok: true`
  - Codex 候选：`20.0 -> 22.4`
  - Cursor 候选：`20.0 -> 17.2`
  - 接受来源排在拒绝来源之前。
  - `end_day_preview` Evidence 已包含 `candidate_quality`。
  - 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_candidate_quality_smoke\work_ledger_candidate_quality_smoke_1784772223.json`

### 下一节点：Node 20：七天自用可靠性与工作资产增长指标

目标：

- 增加每日使用健康度：
  - 是否开始任务。
  - 是否采集到有效过程证据。
  - 是否完成收工。
  - 是否生成可发送日报和续写 Prompt。
- 增加 7 天趋势：
  - 连续使用天数。
  - 每日有效证据数。
  - 候选采纳率。
  - 输出采纳率。
  - 第二天继续任务的命中率。
- 对“有记录但没有形成资产”的任务给出明确提醒。
- 增加七天自用 smoke / replay，验证跨日召回、项目记忆和续写 Prompt 是否真的能接上工作。

## Node 20 执行记录：七天自用可靠性与工作资产增长指标

本节点开始回答 Work Ledger 最重要的产品问题：它是否真的能每天使用，并且让第二天更容易继续工作。系统不再只统计任务数量，而是衡量一次工作有没有留下过程证据、有没有正常收工、有没有形成日报和续写资产、有没有被用户采纳、下一次任务有没有成功承接上一任务。

已实现内容：

- 新增 `build_work_ledger_reliability()`：
  - 按 7/14/30 天窗口聚合工作任务。
  - 统计活动天数和连续使用天数。
  - 统计任务收工率。
  - 统计有效过程证据数量。
  - 统计工作资产形成率。
  - 统计输出采纳率和候选采纳率。
  - 统计跨任务续接机会、命中次数和命中率。
  - 为每个自然日计算 0-100 健康度。
- 新增工作资产缺口提醒：
  - 有记录但没有生成日报、Context Pack、续写 Prompt。
  - 已生成工作资产但没有任何输出被采纳。
  - 活动任务超过 8 小时仍未收工。
- 新增跨任务续接 Evidence：
  - 新任务启动时自动查找同项目上一任务。
  - 检查上一任务是否具有 Context Pack 和 Codex/Cursor 续写 Prompt。
  - 写入 `work_continuation_context` Evidence。
  - 记录上一任务 ID、可用资产、是否命中及判断原因。
- 新增可靠性报告：
  - 写入 `outputs/reliability/work_ledger_reliability_7d.json`。
  - 同步写入 Cognitive Kernel ledger 事件。
- 新增 HTTP 接口：
  - `GET /api/v1/work-ledger/reliability?days=7`
- Work Ledger 控制台新增七天可靠性面板：
  - 总体健康分。
  - 连续使用天数。
  - 任务收工率。
  - 工作资产形成率。
  - 输出采纳率。
  - 次日续接命中率。
  - 七天柱状趋势。
  - 需要补齐的工作资产。
- 新增单元测试：
  - `tests/unit/test_work_ledger_reliability.py`
  - 验证同项目任务自动承接上一任务。
  - 验证缺失输出时能产生资产缺口提醒。
- 新增七天 replay smoke：
  - `scripts/work_ledger_seven_day_replay_smoke.py`
  - 构造连续七天真实 Work Ledger 数据。
  - 第四天故意只记录、不生成工作资产。
  - 验证第五天续接无法命中完整资产。
  - 验证第七天仍能召回第一天保存的指定事实。

实测结果：

- `ok: true`
- 连续活动：7 天。
- 任务数量：7。
- 任务收工率：100%。
- 工作资产形成率：85.7%。
- 输出采纳率：66.7%。
- 次日续接命中率：83.3%（6 次机会命中 5 次）。
- 平均有效证据：7.29 条/任务。
- 七天综合健康度：92.1/100。
- 故意制造的资产缺口被准确识别。
- 第一天写入的 `ORBIT-LEDGER-731` 在第七天召回成功。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_seven_day_replay\work_ledger_seven_day_replay_1784772710.json`

### 下一节点：Node 21：活动任务时间线与无 Git 场景自动记录

目标：

- 活动任务期间按合理间隔生成轻量 checkpoint，而不是只在开始和结束采集。
- 对 Git 项目记录提交、diff 和文件变化；对非 Git 项目仍记录文件、文档和用户确认过程。
- 把 Codex/Cursor/终端候选导入、手动记录、采集快照和输出生成统一成一条任务时间线。
- 对重复快照做内容指纹去重，避免长时间工作产生大量无价值记录。
- 控制台展示“今天从开始到现在发生了什么”，并明确哪些是系统观察、用户确认和 AI 过程导入。
- 增加无 Git 项目与长任务 checkpoint smoke，验证不提交代码也能知道今天做了什么。

## Node 21 执行记录：活动任务时间线与无 Git 场景自动记录

本节点解决“如果今天没有提交 Git，Jachin 是否就不知道做了什么”的问题。活动任务现在会在 L3 后台按间隔生成轻量 checkpoint；Git 项目记录 Git 与文件状态，非 Git 项目仍然记录文件变化。所有过程统一进入任务时间线，并通过指纹避免重复写入。

已实现内容：

- 新增 `collect_work_checkpoint()`：
  - 支持 Git 项目和普通文件目录。
  - 记录 Git 分支、状态、改动文件。
  - 记录任务期内最近修改文件。
  - 生成 SHA-256 内容指纹。
  - 相同工作状态自动去重，不重复写 Evidence。
- 新增 L3 后台 checkpoint loop：
  - 默认每 300 秒检查一次活动任务。
  - 最短间隔限制为 60 秒。
  - 没有活动任务时不执行文件扫描。
  - 可通过 `JACHIN_WORK_LEDGER_AUTO_CHECKPOINT=0` 关闭。
  - 可通过 `JACHIN_WORK_LEDGER_CHECKPOINT_SECONDS` 调整间隔。
  - L3 退出时会取消后台任务，不遗留异步循环。
- 新增统一任务时间线：
  - `build_work_timeline()`
  - 统一展示任务开始/结束、跨日续接、用户记录、AI 过程导入、checkpoint、系统观察、候选反馈、收工预览、输出生成和采纳回流。
  - 明确标记信息来源：用户确认、系统观察、AI 过程导入。
- 新增 HTTP 接口：
  - `POST /api/v1/work-ledger/checkpoint`
  - `GET /api/v1/work-ledger/sessions/{session_id}/timeline`
- Work Ledger 控制台增强：
  - 新增“记录检查点”按钮。
  - Evidence 列表升级为 Task Timeline。
  - checkpoint 显示项目类型、Git 改动数和文件变化数。
  - 跨日续接显示资产命中或缺失原因。
- 七天可靠性统计增强：
  - `work_checkpoint` 计入有效过程证据。
- 新增单元测试：
  - `tests/unit/test_work_ledger_timeline.py`
  - 验证非 Git 目录可记录。
  - 验证空闲状态重复采集会被去重。
  - 验证文件变化后生成新 checkpoint。
  - 验证用户记录、AI 过程和系统观察进入统一时间线。
- 新增长任务 smoke：
  - `scripts/work_ledger_timeline_smoke.py`
  - 在系统临时目录创建真正没有父级 `.git` 的工作目录。
  - 模拟 20 次无变化后台轮询。
  - 模拟 5 次真实文件变化。
  - 验证只保留 6 个有效 checkpoint。
  - 验证非 Git 任务仍能生成日报和 Context Pack。

验证结果：

- `ok: true`
- 非 Git 项目识别：通过。
- 20 次空闲 checkpoint：全部去重。
- 5 次文件变化：全部生成新 checkpoint。
- 最终 checkpoint 数：6，未产生无价值膨胀。
- 时间线共 14 条，覆盖 task / checkpoint / user_note / ai_process / system_observation / output。
- 日报与 Context Pack 生成成功。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_timeline_smoke\work_ledger_timeline_smoke_1784773235.json`

### 下一节点：Node 22：AI 工作来源适配器与每日过程收件箱

目标：

- 建立统一 `WorkSourceAdapter`，让 Codex、Cursor、终端和普通文档以相同协议提供候选过程。
- 只读取本机明确允许的历史文件或导出内容，不通过屏幕猜测，也不直接保存完整敏感对话。
- 对候选过程先做脱敏、去重、任务关联和质量评分，再进入“今日过程收件箱”。
- 用户可以一键采纳、拒绝或忽略候选；采纳后进入任务时间线并刷新日报。
- 支持没有 Git 提交时，从 Codex/Cursor 过程和文件 checkpoint 共同判断今天完成了什么。
- 增加多来源归并 smoke：同一工作被 Codex、终端和文件变化重复描述时，只形成一条主要工作事件，并保留来源证据链。

## Node 22 执行记录：AI 工作来源适配器与每日过程收件箱

本节点把散落在 Codex、Cursor、终端、普通文档和文件变化中的工作过程统一成一份可审核的“今日过程收件箱”。系统不直接保存完整对话，也不通过屏幕猜测用户做了什么；它只读取当前任务项目、Work Ledger 导入目录或用户明确允许的本地来源，先脱敏、归并和评分，再等待用户采纳。

已实现内容：

- 新增独立 `WorkSourceAdapter` 协议与适配器注册表：
  - Codex。
  - Cursor。
  - Terminal / PowerShell / 测试日志。
  - 普通工作文档。
  - 文件 checkpoint。
- 新增来源隐私边界：
  - 默认只读取活动任务项目目录和 Work Ledger 导入目录。
  - 可通过 `JACHIN_WORK_SOURCE_ALLOWLIST` 增加明确允许目录。
  - HTTP 调用也可显式传入允许目录。
  - 不自动扫描浏览器页面，不通过 OCR 猜测工作过程。
  - 不持久化完整原始对话，只保存脱敏后的结构化摘要、短摘录、内容指纹和来源链。
- 新增来源层强制脱敏：
  - 通用 `sk-...` 密钥。
  - Bearer Token。
  - API Key、Access Token、Secret、Password 等常见键值格式。
  - 即使通用安全扫描器漏检，来源适配层仍会再次清理。
- 新增任务关联和质量评分：
  - 根据任务标题、目标、项目名和路径计算关联度。
  - 根据信号数量、来源类型、安全状态和任务关联度计算候选质量。
- 新增跨来源归并：
  - 通过事件关键词集合和共同代码/文档文件名识别同一工作。
  - 同一事项被 Codex、终端、文档和文件 checkpoint 重复描述时，只生成一个主工作事件。
  - 主事件保留每个来源的类型、位置、时间和质量分，避免“去重后证据丢失”。
- 新增每日过程收件箱持久化：
  - 状态包括待处理、已采纳、已拒绝、已忽略和敏感阻断。
  - 刷新收件箱时保留已审核状态，不会反复询问同一事件。
  - 采纳后写入 AI 过程 Evidence，进入任务时间线并刷新日报和续写 Prompt。
  - 拒绝内容不会进入工作资产。
  - 忽略只跳过本次，不作为负面来源质量反馈。
- 新增 HTTP 接口：
  - `GET /api/v1/work-ledger/sessions/{session_id}/process-inbox`
  - `POST /api/v1/work-ledger/process-inbox/refresh`
  - `POST /api/v1/work-ledger/process-inbox/review`
- Work Ledger 控制台新增“每日过程收件箱”：
  - 一键扫描今日过程。
  - 展示来源候选数、归并事件数、待处理数和采纳数。
  - 展示主事件、质量分和多来源证据链。
  - 支持采纳、拒绝、忽略。
- 统一任务时间线新增 `work_process_inbox_review`，可回放用户为什么采纳或拒绝一项工作过程。
- 新增单元测试：
  - `tests/unit/test_work_ledger_sources.py`
  - 验证四类来源归并、审核状态持久化、时间线回放和敏感信息脱敏。
- 新增多来源归并 smoke：
  - `scripts/work_ledger_source_inbox_smoke.py`
  - 模拟 Codex、终端、文档和文件 checkpoint 同时描述一项工作。

验证结果：

- `ok: true`
- 4 个来源候选成功归并为 1 个主工作事件。
- 来源链包含 Codex / Terminal / Document / File Checkpoint。
- 用户采纳状态在再次刷新后仍保持。
- 采纳结果进入任务时间线。
- 日报和 Codex 续写 Prompt 生成成功。
- 敏感密钥脱敏测试通过。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_source_inbox_smoke\work_ledger_source_inbox_smoke_1784774060.json`

### 下一节点：Node 23：增量来源游标与真实 AI 工具接入

目标：

- 为每个来源记录读取游标，只读取上次之后新增的过程，避免每天反复扫描完整日志。
- 支持 Codex / Cursor 明确导出的本地历史目录配置，不依赖固定安装路径。
- 为终端增加命令结果摘要适配器，区分成功命令、失败命令和仅有噪声的长日志。
- 将来源授权、最近同步时间、读取条数和错误状态展示在控制台。
- 增加“暂停来源”和“清除游标后重扫”，让用户始终控制本机数据读取范围。
- 做真实增量 smoke：第一次读 100 条，第二次只新增 5 条时必须只处理 5 条；历史 100 条不能再次进入收件箱。

## Node 23 执行记录：增量来源游标与真实 AI 工具接入

本节点把来源扫描从“每次重新读取日志”升级为带游标的增量同步。Codex、Cursor、终端和普通导出文件现在都有独立读取位置、同步状态和暂停开关；后续扫描只消费新增内容，避免历史过程反复进入收件箱。

已实现内容：

- 新增来源游标存储：
  - 普通文件按字节偏移记录读取位置。
  - 内联/导出内容按字符偏移记录读取位置。
  - 记录来源类型、文件大小、更新时间、累计读取次数和累计行数。
  - 日志被截断或轮转时自动识别并从头重建游标。
- 新增增量读取：
  - 文件未变化时直接跳过，不再读取和分析全文。
  - 文件追加时只读取旧偏移到新文件尾部的内容。
  - 相同 checkpoint Evidence 不重复进入候选。
  - 旧收件箱事件保持不变，新事件增量合并。
- 新增显式来源目录配置：
  - Codex / Cursor 历史或导出目录由用户明确配置。
  - 不依赖某个固定安装路径。
  - 配置结果随任务持久化。
- 新增来源运行控制：
  - 单来源暂停。
  - 单来源恢复。
  - 单来源清除游标并重扫。
  - 全部来源清除游标并重扫。
- 新增终端结果分类：
  - success：命令或测试成功。
  - failure：错误、异常、失败退出。
  - mixed：同一段中同时出现成功与失败。
  - noise：heartbeat、progress、polling 等低价值进度噪声。
  - unknown：缺少明确结果信号。
  - 纯噪声默认进入忽略状态，不要求用户反复确认。
- 修复来源类型误判：
  - 文件名中的明确来源信号优先于父目录名称。
  - 例如 `codex_exports/terminal-heartbeat.log` 会正确识别为 Terminal，而不是 Codex。
- 新增来源状态 HTTP 接口：
  - `GET /api/v1/work-ledger/sessions/{session_id}/source-status`
  - `POST /api/v1/work-ledger/source-configure`
  - `POST /api/v1/work-ledger/source-control`
- 控制台增强：
  - 配置明确允许的本地来源目录。
  - 显示本次新增行数、未变化跳过数、来源错误数和暂停数。
  - 显示每个来源的类型、位置、累计读取行数和错误信息。
  - 支持暂停、恢复、单独重扫和全部重扫。
- 新增单元测试：
  - `tests/unit/test_work_ledger_source_cursors.py`
  - 验证增量读取、空刷新、暂停/恢复、目录配置和终端噪声分类。
- 新增增量游标 smoke：
  - `scripts/work_ledger_source_cursor_smoke.py`
  - 首次同步 100 行。
  - 第二次追加 5 行，只读取 5 行。
  - 第三次无变化，读取 0 行。
  - 暂停来源后新增 1 行，读取 0 行。
  - 恢复来源后只补读该 1 行。

验证结果：

- `ok: true`
- 首次同步：100 行。
- 第二次同步：5 行。
- 无变化同步：0 行。
- 暂停期间：0 行。
- 恢复后：1 行。
- 前 105 行累计读取计数准确，没有重复消费历史内容。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_source_cursor_smoke\work_ledger_source_cursor_smoke_1784774756.json`

### 下一节点：Node 24：低打扰自动同步与来源健康守护

目标：

- 活动任务期间按低频间隔自动执行增量来源同步，不要求用户反复点击“扫描今日过程”。
- 仅在来源发生变化时分析内容；所有来源未变化时不调用模型、不生成 Evidence。
- 对连续失败来源执行指数退避，避免坏路径或锁定文件持续占用 CPU。
- 发现高质量新工作事件时在 Work Ledger 页面显示非打扰提示，不自动采纳。
- 将同步耗时、读取字节数、解析事件数、错误率和退避状态加入来源健康指标。
- 增加长时间自动同步 smoke：模拟 60 轮轮询，仅 6 轮有新内容，必须只处理 6 次且资源占用稳定。

## Node 24 执行记录：低打扰自动同步与来源健康守护

本节点把来源收件箱从“用户手动扫描”升级为活动任务期间的低频后台增量同步。同步守护只维护来源游标和待确认候选，不自动采纳、不调用大模型，也不为无变化轮询生成 Evidence。

已实现内容：

- 新增独立后台来源同步守护：
  - 默认每 180 秒同步一次。
  - 仅存在活动任务时运行。
  - 可通过 `JACHIN_WORK_LEDGER_AUTO_SOURCE_SYNC=0` 关闭。
  - 可通过 `JACHIN_WORK_LEDGER_SOURCE_SYNC_SECONDS` 调整周期，最低 30 秒。
- 手动扫描与后台同步按任务串行：
  - 同一任务只允许一个来源刷新过程写入游标。
  - 避免用户点击扫描与后台守护同时更新游标导致重复读取或丢失偏移。
- 无变化快速路径：
  - 文件大小和游标一致时直接跳过。
  - 不解析历史正文。
  - 不调用模型。
  - 不写入 Evidence。
- 新增来源指数退避：
  - 第一次失败退避 30 秒。
  - 后续连续失败依次退避 60、120、240、480 秒，最高 3600 秒。
  - 退避期间不反复读取坏来源。
  - 来源恢复成功或用户点击恢复后清空错误计数和退避状态。
- 新增来源健康累计指标：
  - 总同步次数。
  - 有变化同步次数与空闲同步次数。
  - 失败来源累计数。
  - 累计耗时与平均耗时。
  - 累计读取字节、字符、行、候选和事件。
  - 当前退避来源数与错误率。
- 新增高质量过程提示：
  - 新事件为待确认状态且质量分达到 70 时标记为高质量新过程。
  - 控制台显示“等待你确认后再进入工作资产”。
  - 不自动采纳，不改变用户的日报、方法论或长期记忆。
- 控制台增加轻量轮询：
  - 活动任务期间每 15 秒只刷新过程收件箱和来源健康状态。
  - 不重新加载完整 Evidence 和输出资产。
  - 展示退避数、平均同步耗时、累计同步次数和变更命中次数。
- 新增测试：
  - `tests/unit/test_work_ledger_source_health.py`
  - 验证指数退避上限、稀疏变更健康计数、无 Evidence 写入、失败退避和人工恢复。
- 新增长时间 smoke：
  - `scripts/work_ledger_source_health_smoke.py`
  - 连续执行 60 轮同步。
  - 仅第 5、15、25、35、45、55 轮写入新内容。

验证结果：

- `ok: true`
- 60 轮同步全部完成。
- 仅 6 轮读取并处理新内容，其余 54 轮走无变化快速路径。
- 累计读取 6 行，没有重复消费。
- 加入项目级游标持久化后，平均同步耗时 6.22 ms。
- 来源错误数为 0。
- 自动采纳数为 0。
- 同步前后 Evidence 数量一致。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_source_health_smoke\work_ledger_source_health_smoke_1784775971.json`

### 下一节点：Node 25：项目级来源授权继承与跨日续接

目标：

- 将用户明确授权的 Codex、Cursor、终端和文档来源目录记入项目级来源配置，而不是只绑定单次任务。
- 第二天为同一项目开始新任务时自动继承已授权来源，不要求重复配置。
- 继承前验证路径是否存在、是否仍在允许范围；失效路径只标记不可用，不静默替换。
- 增加来源授权预览和撤销能力，让用户看到读取范围、最近同步时间和数据量，但不展示原始敏感正文。
- 不同项目保持来源隔离，避免 A 项目的 AI 过程进入 B 项目账本。
- 增加跨日 smoke：第一天授权并同步，第二天同项目自动继承；切换项目后不得读取第一天项目的来源。

## Node 25 执行记录：项目级来源授权继承与跨日续接

本节点解决了来源授权和读取位置只能存在于单次任务的问题。用户第一次为项目授权 Codex、Cursor、终端或文档目录后，同一项目后续任务会继承授权和读取游标；不同项目不会共享来源。

已实现内容：

- 新增项目级来源档案：
  - 存储在 Work Ledger 的项目记忆中。
  - 使用规范化项目绝对路径生成稳定项目键。
  - 不依赖容易重名的项目别名。
  - 记录明确授权的来源根目录、最近任务、授权时间和更新时间。
- 新增跨任务来源游标继承：
  - 文件字节偏移随同步结果回写项目档案。
  - 新任务继承上一次已读取位置。
  - 第二天只读取新增内容，不重新导入前一天历史。
  - 每个新任务仍有独立健康指标和过程收件箱。
- 新增严格项目隔离：
  - A 项目的来源档案不会被 B 项目继承。
  - 项目切换后不会使用“最近项目”来源兜底。
  - 没有项目路径的临时任务仍可使用单次任务来源配置，但不会写入项目级授权。
- 新增失效路径处理：
  - 已授权目录被移动或删除后保留原授权记录。
  - 控制台明确显示“不可用”。
  - 同步时读取 0 个来源，不静默替换为其他目录，也不回退扫描无关项目。
- 新增授权预览：
  - 展示授权路径。
  - 展示是否存在、是否可读取。
  - 展示来源数量、累计读取行数和最近同步时间。
  - 不展示原始对话或日志正文。
- 新增授权撤销：
  - 支持撤销单个来源根目录。
  - 支持撤销当前项目全部来源授权。
  - 撤销后清理对应项目级游标。
  - 后续任务不再继承已撤销来源。
- 新增 HTTP 接口：
  - `POST /api/v1/work-ledger/source-revoke`
- 增加项目记忆并发保护：
  - 项目记忆读写使用进程内可重入锁。
  - 文件更新改为临时文件加原子替换。
  - 避免后台来源同步与收工操作同时写入造成档案损坏。
- 新增单元测试：
  - `tests/unit/test_work_ledger_source_profiles.py`
  - 验证同项目继承、游标续接、跨项目隔离、失效路径和撤销。
- 新增跨日 smoke：
  - `scripts/work_ledger_cross_day_source_smoke.py`

验证结果：

- `ok: true`
- 第一天读取 1 行来源内容。
- 第二天同项目自动继承来源授权和游标，仅读取新增的 1 行。
- 切换到另一项目后授权目录为空，没有读取前一项目来源。
- 授权目录移动后状态为不可用，同步没有回退到其他目录。
- 撤销授权后当前任务授权清空。
- 再次创建同项目任务时没有重新继承。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_cross_day_source_smoke\work_ledger_cross_day_source_smoke_1784775971.json`

### 下一节点：Node 26：跨日工作事件去重与项目事实链

目标：

- 为采纳后的工作事件生成跨任务稳定身份，避免同一功能在 Codex、终端、Git 和日报中被重复计算。
- 建立项目级工作事件索引，保留每次出现的来源链、时间、任务和验证证据。
- 将“完成了什么、为什么修改、失败尝试、最终结论、后续动作”组织成可追踪事实链。
- 收工生成日报时优先选择已确认的新事实，不重复汇报以前已经完成的内容。
- 第二天续接时区分“昨日已完成事实”和“尚未完成动作”。
- 不自动把相似事件合并为事实；低置信度冲突进入待确认队列。
- 增加七天多来源 smoke，验证同一事项多次出现只形成一个项目事实，但每天的过程记录仍完整保留。

## Node 26 执行记录：跨日工作事件去重与项目事实链

本节点把“用户采纳的一条过程记录”从单次任务素材提升为项目级事实。事实拥有稳定身份，同一事项在不同日期、不同任务、不同工具中重复出现时不会被重复计算，但每次出现的时间、来源和验证证据仍然完整保留。

已实现内容：

- 新增独立项目事实索引：
  - 模块：`l3_node/work_ledger_facts.py`
  - 数据目录：`<work-ledger-home>/project_facts/`
  - 使用规范化项目绝对路径生成项目键。
  - 每个事实保存稳定 `fact_id`、规范摘要、状态、可信等级、首次和最近出现时间。
- 事实身份与跨日去重：
  - 组合事件关键词、去重词和文件路径生成事实指纹。
  - 完全相同的指纹直接续接原事实。
  - 高置信度语义重合或共享关键文件时续接原事实。
  - 同一事实跨任务出现时增加 occurrence，不新建“今日完成项”。
- 完整来源链保留：
  - occurrence 保存 `session_id`、`event_id`、来源类型、来源 URI。
  - 保存对应验证 Evidence ID。
  - Codex、Cursor、终端、Git、文档等来源可以共同支撑一个项目事实。
- 禁止低置信度静默合并：
  - 中等相似度事件分别建立事实。
  - 同时进入 `review_queue`。
  - 用户可以选择“确认为同一事实”“保持独立”或“忽略建议”。
  - 未确认前不会互相覆盖，也不会丢失任一来源。
- 采纳入口接入：
  - 过程收件箱事件被用户采纳后自动写入项目事实链。
  - 事件返回 `project_fact_id`、`fact_match_type` 和 `fact_review_pending`。
  - 审查 Evidence 同步记录事实身份和匹配原因。
- 日报去重：
  - 本次首次出现的事实标记为“新增事实”。
  - 以前任务已经出现、本次再次获得证据的事项标记为“持续事实”。
  - 持续事实不再重复计为今天新完成的成果。
  - 待确认相似事实会显示数量，不会隐藏。
- Codex / Cursor 续写任务书接入：
  - 注入当前任务事实。
  - 注入以前任务尚未闭环的事实。
  - 注入累计证据出现次数。
  - 有事实冲突时明确要求模型不得自行合并。
- Lark 简报接入：
  - 优先展示本次用户确认的新事实。
  - 文件变化仍作为证据，不再替代成果事实。
- 新增 HTTP 接口：
  - `GET /api/v1/work-ledger/sessions/{session_id}/project-facts`
  - `POST /api/v1/work-ledger/project-facts/review`
- 控制台新增 Project Fact Chain：
  - 展示事实总量、已完成、未闭环和待确认数量。
  - 区分本次新增、再次出现和历史未闭环。
  - 展示累计证据次数、来源类型和最近出现时间。
  - 支持直接处理事实相似性审查。
- 新增单元测试：
  - `tests/unit/test_work_ledger_facts.py`
  - 验证跨任务稳定身份、多来源 occurrence、日报去重、模糊冲突和人工合并。
  - 原过程收件箱测试增加 `project_fact_id` 集成断言。
- 新增七天多来源 smoke：
  - `scripts/work_ledger_project_fact_chain_smoke.py`

验证结果：

- 36 个 Work Ledger 单元测试全部通过。
- 前端 TypeScript 检查通过。
- Python 编译检查通过。
- `git diff --check` 通过。
- 七个任务中的同一事项只生成一个 `fact_id`。
- Codex、Cursor、终端、Git、文档五类来源均保留。
- 同一事实保留 7 次 occurrence。
- 独立事项没有被错误合并。
- 相似度 44% 的两个事项进入待确认队列，没有静默合并。
- 第七天日报把重复事项标记为持续事实，并显示累计出现 7 次。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_project_fact_chain_smoke\work_ledger_project_fact_chain_smoke_1784776793.json`

### 下一节点：Node 27：项目事实生命周期与决策链

目标：

- 为事实增加 `open -> in_progress -> completed -> reopened -> superseded` 状态演化，而不是只在创建时判断一次。
- 允许新证据关闭历史未完成事实，也允许回归失败重新打开已完成事实。
- 将失败尝试、关键决策、最终结论和后续动作关联到同一事实，形成可追踪决策链。
- 用户确认“保持独立”后持久化反合并关系，避免后续同步反复询问同一组事实。
- 日报只把本次真正完成或状态发生变化的事实计为成果。
- 续写 Prompt 优先带入未闭环、重开和被新事实替代的事项。
- 增加 30 天状态演化 smoke，验证完成、重开、替代和反合并关系在跨任务场景中稳定生效。

## Node 27 执行记录：项目事实生命周期与决策链

本节点把项目事实从“去重后的静态成果”升级为可持续演化的工作对象。同一事项可以跨任务进入开发、完成、回归重开、再次完成和被新方案替代等阶段，历史失败与决策不会因为状态变化而被覆盖。

已实现内容：

- 事实生命周期：
  - 正式支持 `open`、`in_progress`、`completed`、`reopened`、`superseded`。
  - 每次状态变化追加独立 transition，保存原状态、目标状态、原因、任务、事件、时间和验证 Evidence。
  - 状态版本随变化递增，旧 transition 保持只追加、不改写。
- 事实状态自动判断：
  - 过程事件可依据完成、进展、失败、决策和后续动作语义生成目标状态。
  - 也支持通过结构化 `target_state` 明确指定。
  - 已完成事实收到新的失败证据时自动进入 `reopened`。
  - 后续修复可以从 `reopened` 回到 `in_progress` 或 `completed`。
- 决策链：
  - 同一事实保存 append-only 的 `decisions`、`failure_attempts` 和 `next_actions`。
  - 每一条记录保留所属任务、事件、时间和验证 Evidence。
  - 日报、续作 Prompt 和控制台均显示最近失败、已确认决策和后续动作。
- 方案替代关系：
  - 新事实可以声明 `supersedes_fact_id`。
  - 旧事实转为 `superseded`，并保存 `superseded_by_fact_id`。
  - 新事实保存 `supersedes_fact_ids`，形成可反向追溯的替代链。
  - 续作 Prompt 会展开被替代事实的历史决策、失败与原后续动作，避免新方案丢失来龙去脉。
- 反合并规则：
  - 用户选择“保持独立”后生成持久化 separation rule。
  - 后续日期、任务或来源再次出现同一事实对时，不再重复进入待确认队列。
  - 用户后续明确合并时，对应反合并规则自动失效。
- 日报成果口径：
  - 本次新完成的事实标记为“新增完成”。
  - 新出现但尚未闭环的事实标记为“新增未闭环”。
  - 以前已经出现的事项继续标记为“持续事实”，不重复算作当天新成果。
  - 本次完成、重开和被替代的事实分别进入状态变化区域。
- 续作上下文：
  - 优先带入未闭环和重开事实。
  - 带入本次状态变化和方案替代历史。
  - 带入失败、决策和下一动作，下一轮 Codex / Cursor 不必重新猜测历史。
- Lark 简报：
  - 只把本次真正完成的事实作为成果发送。
  - 重开事项进入风险区，不把仍然失败的事项包装为完成。
- HTTP 与控制台：
  - 新增 `POST /api/v1/work-ledger/project-facts/update`。
  - 控制台可将事实标记为进行中、完成或重开。
  - 展示各状态数量、最近决策、失败与下一动作。
- 新增 30 天生命周期 smoke：
  - `scripts/work_ledger_fact_lifecycle_smoke.py`
  - 覆盖完成、回归重开、恢复、再次完成、方案替代和持久反合并。

验证结果：

- 38 个 Work Ledger 单元测试全部通过。
- 前端 TypeScript 检查通过。
- Python 编译检查通过。
- `git diff --check` 通过。
- 30 个连续任务全部记录成功。
- 主事实状态序列严格为：
  - `open -> in_progress -> completed -> reopened -> in_progress -> completed -> superseded`
- 两次失败、关键决策和后续动作均完整保留。
- 替代事实可以追溯旧事实，续作 Prompt 可以读取旧方案的决策与失败。
- 用户确认独立的事实对跨日再次出现时没有重新询问。
- 第 30 天日报没有把重复事实再次包装为新成果。
- 30 天结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_fact_lifecycle_smoke\work_ledger_fact_lifecycle_smoke_1784777849.json`
- 七天事实链回归结果：`D:\Projects\jachi\jachin-system-main\output\work_ledger_project_fact_chain_smoke\work_ledger_project_fact_chain_smoke_1784777881.json`
- 七天连续工作回放结果：`D:\Projects\jachi\jachin-system-main\output\work_ledger_seven_day_replay\work_ledger_seven_day_replay_1784777882.json`

### 下一节点：Node 28：成果关系图与方法论晋升

目标：

- 把项目事实、失败尝试、关键决策、最终结果和可复用方法建立显式关系，而不是只在单个事实内部保存列表。
- 只有用户确认且已经验证完成的事实，才允许成为日报、周报和绩效材料中的成果。
- 从反复成功的“失败原因 -> 决策 -> 恢复动作 -> 结果”链中生成方法论候选。
- 方法论候选必须保留来源事实和 Evidence，默认进入用户审查，不自动写成长期方法论。
- 周报按事实状态变化生成，不用 Git 文件数量或聊天条数冒充成果。
- 控制台增加项目成果关系视图，能够从一条成果回看其证据、失败、决策、替代方案和沉淀方法。
- 增加 90 天项目演化 smoke，验证成果不会重复计算、未闭环事项不会进入绩效材料、方法论可以追溯到原始事实。

## Node 28 执行记录：成果关系图与方法论晋升

本节点解决“留下了很多文件、证据和聊天记录，但哪些才算真正成果”的问题。系统新增独立 Outcome Graph，将确认事实、失败、决策、动作、完成结果和方法论组织为可追溯关系，并统一日报、周报、绩效材料和召回的成果口径。

已实现内容：

- 新增成果关系图引擎：
  - 模块：`l3_node/work_ledger_outcomes.py`
  - 数据目录：`<work-ledger-home>/project_outcomes/`
  - 图节点包括 fact、failure、decision、next_action、outcome 和 methodology。
  - 图关系包括 `has_failure`、`informed_decision`、`drives_action`、`produced_outcome`、`verified_as`、`suggests_methodology` 和 `superseded_by`。
- 严格成果准入：
  - 只有 `trust_level=user_confirmed` 且存在 `completed` transition 的事实可以生成 outcome。
  - 同一事实多次出现只保留一个当前有效成果。
  - 已完成后回归重开的结果标记为 invalidated，不再进入成果。
  - 被新方案替代的结果标记为 superseded，不再作为当前成果。
  - 未闭环、进行中和仅有文件变化的事项不会进入成果。
- 稳定成果身份：
  - outcome ID 由事实 ID 和完成 transition 生成。
  - 首次完成、回归重开和再次完成均保留历史结果。
  - 当前有效结果与历史结果分开统计，不丢失演化过程。
- 方法论晋升：
  - 只从完整的“失败 -> 决策 -> 动作 -> 再次完成”关系生成候选。
  - 必须至少经过两次完成，避免一次偶然成功被包装成方法论。
  - 候选保存来源事实、成果、任务和 Evidence ID。
  - 默认状态为 `pending_review`，不会自动写入长期方法论。
  - 用户可以批准、否决或重置审查。
- 输出口径统一：
  - 日报新增“本次可计入成果的验证结果”。
  - 单任务周报不再把修改文件数量写成完成事项。
  - 绩效材料只列已验证成果，不再使用证据数、聊天数和文件数包装价值。
  - 多日周报只从 `verified_outcomes` 聚合成果。
  - 只有已批准方法论可以写入“已批准方法论”；待确认候选单独展示。
- LLM 周报约束：
  - 明确要求模型只能从 `verified_outcomes` 写成果。
  - `adopted_outputs` 只能表示输出被使用，不能作为完成成果。
  - 未批准的方法论不得写成已沉淀经验。
- Recall 接入：
  - 已验证成果进入 Work Ledger 召回索引。
  - 只有用户批准的方法论进入召回。
  - 后续询问“项目真正完成了什么”时优先命中成果，而不是文件数量。
- HTTP 接口：
  - `GET /api/v1/work-ledger/sessions/{session_id}/project-outcomes`
  - `POST /api/v1/work-ledger/methodology/review`
- 控制台新增 Outcome Graph：
  - 展示有效成果、历史结果、待审方法论和已批准方法论数量。
  - 展示本次可以计入成果的事项及完成依据。
  - 展示方法论候选的失败、决策、动作和验证结果。
  - 支持批准沉淀和否决候选。
- Evidence 完整性修复：
  - 结构化事实事件自带的 `verification_evidence_id` 会自动进入 occurrence、transition、outcome 和 methodology。
- Windows 并发写稳定性：
  - 项目事实与成果图使用唯一临时文件执行原子替换。
  - 遇到短暂文件占用时有界退避重试。
  - 写入结束后清理残留临时文件，避免后台同步、报告生成和控制台刷新互相争用固定 `.tmp`。
- 时间窗口修复：
  - 多日周报由最多 60 天扩展到最多 365 天。
- 新增测试：
  - `tests/unit/test_work_ledger_outcomes.py`
  - 验证成果准入、关系完整性、方法论审查和回归重开后成果失效。
- 新增 90 天项目演化 smoke：
  - `scripts/work_ledger_outcome_graph_smoke.py`

验证结果：

- 40 个 Work Ledger 单元测试全部通过。
- 前端 TypeScript 检查通过。
- Python 编译检查通过。
- 30 天事实生命周期回归通过。
- 7 天多来源事实链回归通过。
- 90 个连续任务全部成功记录。
- 重复出现 5 次的同一事实只保留 1 个有效成果。
- 未闭环事实没有进入成果。
- 被替代的旧方案没有进入当前周报。
- 回归后再次完成的事实保留两次完成历史，但只计 1 个当前成果。
- 方法论候选保留失败、决策、动作和完成结果，并在用户批准后才进入周报。
- 90 天周报中每个有效成果只出现一次。
- 关系图包含全部七类核心关系。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_outcome_graph_smoke\work_ledger_outcome_graph_smoke_1784778984.json`

### 下一节点：Node 29：成果价值反馈与日常使用闭环

目标：

- 区分“技术上完成”和“实际产生价值”，为成果增加 delivered、adopted、measurable_impact 等后续状态。
- 记录日报是否真的发送、续作 Prompt 是否真的被 Codex / Cursor 使用、方法论是否在后续任务中命中并成功。
- 用户能够对成果标记“有价值、一般、无价值”，反馈只影响成果价值排序，不改写原始完成事实。
- 周报优先展示被实际使用或产生可验证影响的成果，并明确区分完成、交付、采用和价值。
- 建立“昨天结束任务 -> 今天继续任务 -> 使用上下文包 -> 完成新成果”的连续工作链。
- 控制台展示成果采用率、续作命中率、方法论复用成功率和减少上下文丢失的证据。
- 增加 30 天真实日常使用回放，验证系统不仅会记录成果，还能证明哪些输出真正帮助了下一次工作。

## Node 29 执行记录：成果价值反馈与日常使用闭环

本节点把“做完了”和“真的有用”拆成两层。项目事实和 Outcome Graph 继续负责回答是否真实完成；新增 Value Chain 只记录完成后的交付、采用、影响、续作和方法论复用，不允许价值反馈反向篡改事实。

已实现内容：

- 新增成果价值账本：
  - 模块：`l3_node/work_ledger_value.py`
  - 数据目录：`<work-ledger-home>/project_value/`
  - 采用追加式 value event，支持幂等键和 Windows 原子写入。
- 成果价值阶段：
  - `completed`：已经通过事实完成验证。
  - `delivered`：成果或简报已经真实交付。
  - `adopted`：报告、Prompt 或结果已经在真实工作中使用。
  - `impact`：用户确认产生了可观察影响。
  - 阶段只允许按证据向上聚合，不会覆盖 Outcome Graph。
- 用户价值反馈：
  - 支持 positive、neutral、negative。
  - 反馈影响价值分和周报排序。
  - 负面反馈不会把完成事实改成失败，也不会删除原始证据。
- 连续工作链：
  - 新任务找到上一任务 Context Pack 和续写 Prompt 时记录 `continuation_available`。
  - 用户实际采用 Context Pack 或续写 Prompt 后记录 `continuation_used`。
  - “找到了旧资产”和“真的用旧资产继续工作”分开统计。
- 输出采用接入：
  - `adopt_work_output` 会同时写 Work Ledger Evidence 和 Value Chain。
  - 输出采用事件关联本任务的已验证成果。
  - 续写资产被采用时自动形成跨 session 续作关系。
- 方法论复用：
  - 支持记录复用成功和复用失败。
  - 统计复用尝试数、成功数和成功率。
  - 失败不会删除已批准方法论，保留为后续修订依据。
- 价值聚合指标：
  - 成果交付率、成果采用率、成果影响率。
  - 续作上下文实际使用率。
  - 方法论复用成功率。
  - 正向和负向价值反馈数量。
- 周报升级：
  - 按 `impact > adopted > delivered > completed` 排序。
  - 每条成果明确标注已完成、已交付、已采用或已产生影响。
  - 证据边界明确说明负面反馈只改变价值排序，不改写事实。
  - LLM 周报编辑器同步接收 valued outcomes 和 value summary，不能用文件数、聊天数或输出数冒充成果。
- 日报升级：
  - 新增“成果交付与实际价值”。
  - 只显示真实价值事件，不把资产生成自动包装成价值。
- Recall 升级：
  - 召回索引包含价值阶段、价值分和用户反馈。
  - 同一语义下，真实采用或产生影响的成果优先于仅完成成果。
- HTTP 接口：
  - `GET /api/v1/work-ledger/sessions/{session_id}/value-chain`
  - `POST /api/v1/work-ledger/value-events`
- 控制台新增 Outcome Value Chain：
  - 展示完成、交付、采用、影响和续作实际使用率。
  - 用户可以标记已交付、已采用、有影响、一般和没价值。
  - 已批准方法论可以记录复用成功或失败。
  - 七天健康度新增成果交付率、成果采用率和续作实际使用率。
- 新增测试：
  - `tests/unit/test_work_ledger_value.py`
  - 覆盖阶段演进、事实不可篡改、幂等、续作和方法论复用。
- 新增 30 天价值链回放：
  - `scripts/work_ledger_value_chain_smoke.py`
  - 验证影响、采用、交付、完成和负面反馈的排序。
  - 验证 29 次跨任务续作均区分 available 与 used。

验证结果：

- 40 个既有 Work Ledger 测试、4 个 Value Chain 测试和 2 个诊断日志测试通过，合计 46 个测试。
- 前端 TypeScript 检查通过。
- Python 编译检查通过。
- 30 天回放通过全部 9 项断言。
- 5 个成果严格按“影响、采用、交付、完成、低价值完成”排序。
- 负面反馈后的项目事实仍保持 `completed`。
- 29 次续作机会全部被实际使用，使用率为 100%。
- 方法论复用 3 次、成功 2 次，成功率为 66.7%。
- 周报中四种价值阶段均有明确标签，且顺序正确。
- 结果文件：`D:\Projects\jachi\jachin-system-main\output\work_ledger_value_chain_smoke\work_ledger_value_chain_smoke_1784786022.json`

开发测试与排障补充：

- 在控制台 `今日工作台` 中新增 `价值链测试` 开发 Tab，不增加独立路由和侧边栏页面。
- Tab 可以选择 Session、查看 Value Chain 原始数据、写入测试事件并运行无副作用一致性诊断。
- 新增诊断模块 `l3_node/work_ledger_value_diagnostics.py`。
- 每次诊断和 Value Chain HTTP 异常同时写入 Markdown 与 JSONL。
- 动态日志默认位于 `<JACHIN_WORK_LEDGER_HOME>/logs/`，页面显示实际绝对路径。
- 仓库排障说明：`docs/23_work_ledger_value_chain_test_log.md`。
- 诊断覆盖事件 ID、Outcome 引用、Evidence、聚合统计、续作和方法论复用一致性。

### 下一节点：Node 30：真实使用信号自动采集与隐私门控

目标：

- 把 Lark 发送成功、报告真实打开、续写 Prompt 交给 Codex / Cursor 等现有 WorkOrder 和 Evidence 自动映射为 delivered / adopted，减少用户手动点击。
- 自动采集只接受已通过 Verification 的真实动作，工具“声称成功”但没有验证证据时不得写价值事件。
- measurable impact 和价值评价仍由用户确认，系统只能提出候选，不能自行宣布产生价值。
- 为自动价值事件增加来源、工具、WorkOrder、Verification 和截图/OCR Evidence 的完整追溯。
- 增加隐私门控：允许用户按 App、项目和输出类型关闭自动价值采集或撤销授权。
- 控制台展示自动采集与用户确认的区别，并提供错误映射纠正入口。
- 增加连续 7 天真实自用回放，验证无需频繁手动标记也能形成可信的交付、采用和续作链。

## 逐条工作汇报输出升级

工作记录的最终总结不再只生成连续段落，同时生成独立的
`work_report_summary.md`，用于日报、周报和面向同事的工作汇报。

固定结构：

1. 今日完成与推进。
2. 涉及模块。
3. 风险与未完成。
4. 下一步计划。

每个部分都必须使用编号条目。基础规则生成器和 LLM 增强输出使用相同的
格式约束；LLM 返回连续段落、网页残片或无法核验的文件路径时，质量门控
拒绝采用增强结果。

控制台 `今日工作台` 提供“生成并复制工作汇报”按钮，生成后可直接预览并
复制逐条内容。该输出与 `daily_report.md`、`lark_brief.txt` 并存，不影响
已有日报、续写任务书和团队简报流程。

验证标准：

1. 输出文件存在且 UTF-8 可读。
2. 四个固定部分全部存在。
3. 每个部分至少包含一个编号条目。
4. 文件、模块和风险描述能够追溯到 Work Ledger Evidence。
5. LLM 增强版仍保持逐条结构。

### 即时简报

- 控制台提供独立的“即时工作简报”，不依赖任务结束或固定时间触发。
- 支持“今天、7 天、30 天”三个自然日范围。
- “今天”严格从本地时间 00:00 聚合到当前时刻，不使用滚动 24 小时。
- 用户一天内可以反复生成；生成前会为当前活动任务补一次去重检查点，
  新增的 Git 和文件变化会进入最新简报。
- 每次生成同时保存 Markdown 简报和 Evidence 索引快照。
- 简报保持逐条工作汇报格式，并提供预览与复制操作。
- 即时检查点以 Git diff 内容指纹去重；同一个文件继续修改后再次生成，
  最新 diff 与代码片段仍会进入证据，不会因为文件一直处于 `M` 状态而漏采。
- 生成链路先形成不编造的 Evidence Baseline，再由高质量模型读取 diff、
  文件片段、手动记录和 AI 过程轨迹，提炼具体行为、对象、结果与价值。
- LLM 结果必须通过即时简报质量门禁：禁止把任务标题、`M/A/D` 文件状态、
  “工作记录”或“任务进行中”当作工作成果；禁止编造测试、交付和业务价值。
- 模型不可用或输出未通过门禁时，自动回退 Evidence Baseline；证据不足会
  明确提示用户补充过程结论，不再用泛化句伪装成果。
- 控制台预览明确标记“AI 证据整理”或“证据基础版”，并保存 baseline、
  final、Evidence 索引和质量报告，便于排查每次简报为什么这样生成。
- HTTP 接口：`POST /api/v1/work-ledger/briefing`，`days` 仅接受
  `1`、`7`、`30`。

## Codex 工作计划协作链

每个需要形成工作记录的项目约定维护一个名为 `工作计划` 的 Codex 会话。
Jachin 不把 Codex 当作事实来源，也不会在每一步机械询问；它先检查本地
Evidence，只有缺少深度理解、决策依据或完成边界时才建立协作请求。

正式进入工作链的场景：

1. 任务启动：目标过于模糊且没有可续接上下文时，请 Codex 基于真实仓库
   形成执行计划。
2. 方案权衡：任务涉及架构、方案选择或技术取舍，但当前没有决策记录时，
   请 Codex 给出候选方案、依据和待验证假设。
3. 过程检查点：已有 Git/文件改动但缺少语义记录时，请 Codex 解释改动
   解决的问题、模块作用和当前进展。
4. 失败恢复：出现失败记录或风险候选时，请 Codex 基于已经失败的路径
   分析原因，并提出与上一条路径有实质差异的恢复顺序。
5. 收工验收：存在改动但缺少验收证据时，请 Codex 区分已修改、已验证、
   已完成和已交付，防止日报夸大。
6. 续作交接：任务缺少明确下一步时，请 Codex 生成可直接交给下一轮
   Codex/Cursor 的任务书。
7. 即时简报：本地证据不足以形成可读工作成果时，自动查询 `工作计划`
   会话补充解释，再由 Work Ledger 质量门禁整理输出。

执行约束：

- 项目、会话和输入框全部通过视觉模型定位，不使用固定屏幕坐标。
- 输入前必须再次核验当前项目、当前会话和输入框；核验失败立即停止。
- 每个请求包含触发阶段、目的、结果用途、证据引用和内容指纹。
- 相同证据指纹只询问一次；成功结果会关闭对应工作链请求。
- Codex 回答以 `system_observed` 写入，不能单独证明功能完成或测试通过。
- 所有截图、提示词、回答来源、校验结果和时间线写入 Evidence。
- 控制台显示待处理协作建议，用户可以直接点击“询问 Codex”。

接口：

- `POST /api/v1/work-ledger/codex-consult`

### Codex 信息融合边界

Codex 不是工作简报作者，也不是完成状态的事实来源。其回复只作为
`system_observed` 级别的解释性信息进入 Jachin Work Ledger。

最终日报、周报、即时简报必须遵循统一融合顺序：

1. 收集用户确认、成果验证、Git、文件、测试、运行结果和过程记录。
2. 把 Codex 回复作为解释、风险和建议素材加入候选信息。
3. 按信任级别处理冲突，区分已验证事实、系统观察和外部解释。
4. 由 Jachin 重新组织完整内容，不直接拼接任何单一来源。
5. 运行文件路径、完成边界、格式和 Codex 原文重合质量门。

如最终文本直接复制或高度近似改写 Codex 原句，该生成结果判定失败，
回退到不使用 Codex 正文的本地证据基线版本。
- MCP：`windows_codex_work_plan_query`
