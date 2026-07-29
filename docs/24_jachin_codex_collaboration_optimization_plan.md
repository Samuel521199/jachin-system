# Jachin × Codex 协作链优化执行计划

## 1. 目标

Codex 不是 Jachin 的替代品，也不是简报作者。它是工作链中的深度代码理解、
方案分析和失败诊断协作者。

目标链路：

```text
用户任务
  -> Jachin Work Ledger 收集真实证据
  -> 判断是否值得询问 Codex
  -> 构建有边界的 Codex 请求
  -> 执行并验证本次回复
  -> 将回复降级为解释性信息
  -> Jachin 融合全部来源
  -> Jachin 生成日报、周报、简报和续作任务书
  -> 质量门与 Evidence
```

## 2. 当前主要问题

1. Codex 桌面调用依赖视觉界面，窗口、项目折叠和会话状态会变化。
2. 仅等待界面稳定不足以证明生成完成，可能读取旧回复或半截回复。
3. 同一 Codex 会话可能存在多个任务，必须证明回答属于本次请求。
4. 多个 Jachin 任务同时使用 Codex 时，可能竞争同一输入框和前台窗口。
5. 当前提示词已基于证据动态生成，但缺少统一上下文预算、脱敏和附件策略。
6. Codex 回复质量验证偏通用，尚未按任务场景强制结构化输出。
7. Codex 回复已经进入 Work Ledger，但仍需持续强化冲突处理和逐条事实溯源。
8. 失败信息已有 Evidence，但还没有形成完整的阶段级恢复经验库。
9. 缺少长期指标：成功率、错误会话率、旧回复误收率、等待耗时和人工介入率。

## 3. 不可破坏的边界

1. Codex 回复只以 `system_observed` 进入证据层。
2. Codex 不能单独证明完成、测试通过、交付或业务价值。
3. 最终输出只能由 Jachin 在融合全部来源后生成。
4. 禁止直接复制或轻微改写 Codex 原文作为最终简报。
5. 项目、会话、输入框、本次请求和回复必须逐层验证。
6. 视觉坐标必须来自当前屏幕，不允许固定坐标。
7. 失败必须显式返回，不允许用旧回复或本地猜测伪装成功。

## 4. 分阶段执行

### Node 0：现状审计

状态：已完成。

完成内容：

1. 审计触发、视觉定位、提示词、等待、回复提取、融合和质量门。
2. 确认高风险缺口是“本次请求和回复缺少强关联”。
3. 确认最终简报已经走 Jachin LLM 融合，但需要防照搬硬校验。

验收：

- 形成本文档。
- 每个后续节点都有代码、测试、Evidence 和退出条件。

### Node 1：Codex Invocation Contract

状态：已完成第一版。

目标：

1. 每次真实调用生成唯一 `invocation_id`。
2. 提示词要求 Codex 第一行返回关联标记。
3. 视觉提取和复制回退都必须保留关联标记。
4. 只有关联标记匹配本次请求，回复才允许进入 Work Ledger。
5. 存入 Work Ledger 前移除关联标记，保留验证结果。

失败代码：

- `codex_reply_invocation_mismatch`
- `codex_work_plan_reply_unverified`

验收：

- 旧回复不能冒充新回复。
- 错误关联标记不能关闭 Work Chain 请求。
- Evidence 包含 `invocation_id`、关联验证和回复来源。

### Node 2：调用队列、租约与取消恢复

状态：已完成第一版。

目标：

1. 建立单机 Codex Invocation Manager。
2. 同一时刻只允许一个任务占用 Codex 桌面输入通道。
3. 支持 `queued/running/waiting/succeeded/failed/cancelled` 生命周期。
4. 支持用户停止任务、进程重启后识别孤儿请求和安全恢复。
5. 同一 prompt hash 只复用经过验证且证据上下文未变化的结果。

验收：

- 并发请求不串话、不覆盖输入框。
- 取消后不再继续粘贴、提交或收集回复。
- 重启后不会把旧运行状态当成功。

已实现：

1. 新增持久化 `CodexInvocationManager`，每个调用独立记录状态、阶段、
   元数据和最近 120 条生命周期事件。
2. 使用原子文件租约保护 Codex 桌面输入通道，同一时刻只允许一个调用
   执行，其余调用保持排队状态。
3. 调用状态统一为
   `queued/running/waiting/succeeded/failed/cancelled`。
4. 用户取消会写入持久化记录；执行链在打开、导航、核验、输入、提交、
   等待和提取前检查取消信号。
5. 租约包含 PID、心跳和有效期；进程退出、租约过期或应用重启后，
   孤儿调用会被恢复为明确失败，不会伪装为成功。
6. Work Ledger HTTP 增加调用列表和取消接口。
7. Work Ledger 页面实时轮询活动调用，展示当前阶段并允许停止任务。
8. 调用记录关联 `session_id`、`request_key`、项目、会话和 Evidence 路径，
   为后续按 invocation 回放打下基础。

验证：

- Invocation Manager 专项测试：15 项通过。
- Work Ledger/Codex 相关单元测试：67 项通过。
- Desktop TypeScript 类型检查通过。

### Node 3：项目与会话导航韧性

状态：已完成第一版。

目标：

1. 视觉定位同时结合窗口标题、可访问性树和当前截图。
2. 支持项目折叠、搜索、滚动、会话重名和窗口缩放。
3. 连续两次上下文核验一致后才允许提交。
4. 失败时按阶段选择恢复路径，而不是从头盲目重试。

验收：

- 不同分辨率和侧边栏宽度下不使用固定坐标。
- 错项目、错会话、输入框不可见时提交次数为零。

已实现：

1. 每次视觉定位同时采集活动窗口标题、当前截图和活动窗口可访问性树摘要。
2. 可访问性树只提供候选名称和边界证据，最终点击坐标仍由当前截图中的
   视觉模型返回，不使用窗口比例或固定坐标。
3. 导航路径按阶段执行：当前可见会话 -> 展开项目 -> 视觉定位搜索框 ->
   搜索会话；每条路径独立记录结果和失败原因。
4. 视觉定位增加最低置信度门槛，低置信度坐标不会被点击。
5. 上下文验证必须连续采集两次，并要求项目、会话、选中状态、输入框状态
   和窗口身份保持一致。
6. 首次上下文验证失败后切换到定向恢复导航，不重复已经失败的直接路径。
7. 提示词粘贴后、按下发送键前再次执行双样本上下文验证。
8. 如果提交前上下文发生变化，自动清空已粘贴提示词并停止，绝不发送到
   错误项目或错误会话。

失败代码：

- `codex_work_plan_conversation_not_found`
- `codex_work_plan_context_mismatch`
- `codex_context_changed_before_submit`
- `codex_work_plan_navigation_exhausted`

验证：

- Codex Invocation/Navigation 专项测试：20 项通过。
- Work Ledger/Codex 相关完整测试：72 项通过。

### Node 4：Context Pack 与提示词预算

状态：已完成第一版。

目标：

1. 为不同场景定义独立输入契约。
2. 只提供与问题相关的 Git、diff、文件片段、失败证据和用户约束。
3. 设置上下文大小、单文件额度、敏感信息脱敏和路径白名单。
4. 超预算时先做本地结构化压缩，不把大段日志直接粘贴给 Codex。
5. 输出要求包含结论、证据、不确定项和下一步。

验收：

- 提示词中每段上下文都能解释用途。
- 密钥、Token、隐私数据不会进入 Codex。
- 超大 diff 不会导致输入框或模型上下文失控。

已实现：

1. 新增统一 `Codex Context Pack`，工作简报补全和场景协作共用同一套
   输入契约。
2. 大 diff 按文件块解析，结合任务标题、用户目标、场景目的和缺失证据
   进行相关性排序。
3. 修改文件和文件片段按相关性排序；单文件片段、总上下文和 diff 分别
   设置独立预算。
4. 超预算时优先删除低相关片段，再压缩 diff、暂存 diff 和低优先级上下文，
   始终保持合法结构化 JSON。
5. `.env`、凭据、私钥、证书、依赖目录、Git 内部目录以及项目根目录外文件
   默认不允许进入 Context Pack。
6. API Key、Bearer Token、私钥、Cookie、GitHub/Slack/AWS Token、
   数据库连接密码、邮箱和手机号在 Codex 边界再次脱敏。
7. Context Pack 生成稳定 digest；相同证据得到相同 digest，相关证据变化
   会产生新 digest。
8. Evidence 保存预算、输出长度、被裁剪区段、被拦截路径、脱敏类型、
   Context Pack digest 和是否在预算内。
9. 原始大 diff 的采集上限扩大，先保留足够候选，再由 Context Pack 做
   相关性筛选，避免简单截取开头导致关键文件丢失。

验证：

- Context Pack 专项测试覆盖预算、相关性、脱敏、项目边界和 digest。
- Work Ledger/Codex 相关完整测试：75 项通过。

### Node 5：生成完成与回复提取

状态：第一版已完成。

已完成：

1. 同时观察生成状态、停止按钮、回复长度、界面稳定和关联标记。
2. 优先使用 Codex 回复复制能力，视觉/OCR 作为验证和回退。
3. 检测半句话、截断、错误提示、权限请求和仍在生成状态。
4. 对回复进行场景 Schema 校验。
5. 新增独立 `codex_reply_protocol.py`，避免把提取策略继续堆入 Windows 自动化实现。
6. 原生复制必须带本次 invocation marker；复制到旧回复或原始提示词会被拒绝。
7. 复制、视觉与 OCR 的可信结果发生冲突时，任务保持失败并写入 Evidence。
8. 超时、权限请求、生成错误和焦点丢失都有独立阶段码、末次截图和诊断依据。

验收：

- 半截回复成功率为零。
- 超时后明确报告卡点，并保留最后截图。
- 复制结果、视觉结果和 OCR 冲突时不直接成功。
- 协议专项测试覆盖生成中、稳定完成、权限请求、错误、提示词回显、旧 marker、截断和来源冲突。

### Node 6：Jachin Information Fusion

状态：第一版已完成。

已完成：

1. Codex 回复被标记为解释性素材。
2. Jachin 最终融合用户确认、验证结果、Git、文件、运行证据和 Codex 信息。
3. 日报、周报、即时简报禁止照搬 Codex 原句。
4. 发现直接复制或高度近似改写时，质量门拒绝并回退本地证据版本。
5. Codex 回答会拆成稳定 claim，并分类为完成、验证、交付、文件变更、决策、
   风险、假设、解释或建议。
6. 每条 claim 都会关联本机支持证据、反证、信任等级和匹配分。
7. Claim 会被处置为 `accepted_fact`、`supported_interpretation`、
   `recommendation`、`unknown_requires_confirmation` 或 `rejected_conflict`。
8. 只有用户确认或验证证据支持的完成声明，才能证明任务已经完成。
9. 未知声明进入确认队列；与本机证据冲突的声明进入冲突记录，不允许进入最终输出。
10. 即时简报、日报、Lark 短报和周报质量门会读取 claim disposition，阻止模型通过
    改写绕过事实边界。
11. Claim Fusion 会随 Codex consultation 写入 Work Ledger Evidence，并进入最终
    Composer 的 Evidence Digest。

验收：

- 最终输出的每个完成结论都能指向非 Codex 的真实证据。
- Codex 观点与 Git/测试冲突时，以真实证据为准。

### Node 7：阶段化 Recovery 与失败学习

状态：第一版已完成。

已完成：

1. 按 `open/navigate/verify/input/submit/wait/extract/fuse` 分类失败。
2. 每次重试吸收前一次失败原因。
3. 恢复路径来自 Capability Metadata，不写死在主流程。
4. 成功恢复经验进入本机 Learned Playbook。
5. 每次失败后只选择一条新路径，不会在失败前预先固定 B/C/D。
6. 已经失败的 `stage + strategy` 不会再次选择。
7. 权限、确认和取消类失败不会自动重试。
8. 提交前上下文变化时先清空未提交 Prompt，再重新导航、核验和粘贴。
9. 提交后的等待恢复只恢复焦点或延长等待，禁止重新提交 Prompt。
10. 回复提取失败时重新执行原生复制、视觉和 OCR 融合，不重新触发 Codex 生成。
11. 五次恢复仍失败时生成失败时间线、停止原因和建议动作。
12. 所有尝试、决策、成功恢复和最终失败都会写入 Tool Evidence、Work Ledger Evidence
    和本机 `codex_recovery_learned.jsonl`。

验收：

- 不重复执行已证明无效的路径。
- 五次失败后能说明每次尝试、失败原因和建议动作。

### Node 8：可观察性与质量指标

状态：第一版已完成。

指标：

1. 真实调用成功率。
2. 项目/会话定位成功率。
3. 旧回复误收率。
4. 回复截断率。
5. 平均与 P95 等待时间。
6. 人工介入率。
7. Codex 信息被最终采用、拒绝和冲突的比例。
8. 最终简报质量门通过率。

验收：

- Evidence Console 可按 invocation 回放完整链路。
- 7/14/30 天可查看趋势和主要失败阶段。

当前实现：

1. Evidence 索引会递归识别并保留信息最完整的 Codex invocation，避免只拿到外层摘要。
2. 相同 `invocation_id` 的多份 Evidence 自动去重，以最新记录作为统计口径。
3. 后端生成 `output/codex_observability_index.json`，统一提供 7/14/30 天窗口。
4. 统计真实调用数、成功率、恢复成功数、恢复率、人工介入数、平均耗时和 P95 耗时。
5. 单独统计旧回复拦截、截断回复拦截、主要失败阶段和主要恢复策略。
6. Windows Codex Work Plan 调用从入队开始记录总耗时、尝试次数、最终状态和失败阶段。
7. Evidence Console 增加 Codex Invocation 可观察性区域，可切换 7/14/30 天。
8. 单条 Evidence 增加 invocation 回放，展示 Manager 阶段、Evidence 阶段、失败尝试、
   Recovery 决策、回复校验、人工确认点和最终建议动作。
9. `os_evidence_governance_index` 和 `os_evidence_codex_observability_index`
   已加入 Tauri ACL，页面不再因为命令未授权静默降级。
10. 无后端索引时，前端仍可基于当前 Evidence 临时聚合，保证开发态可诊断。

尚未在本节点执行：

1. 真实 Codex 桌面长任务、权限弹窗、网络中断和窗口遮挡 live smoke。
2. 采用/拒绝/冲突比例仍依赖 Node 6 Claim Fusion Evidence 的真实样本积累。
3. Rust 专项测试受当前 Windows linker `LNK1105` 文件关闭异常阻塞；
   TypeScript、Vite 生产构建和 Python 98 项回归已通过。

### Node 9：真实桌面发布门

状态：待执行。

场景：

1. 正确项目、正确工作计划会话。
2. 当前停留在其他项目和其他会话。
3. 项目折叠、滚动和窗口缩放。
4. Codex 正在执行其他任务。
5. 回复超时、权限请求、网络中断和应用重启。
6. 同时发起两次简报请求。
7. Codex 给出与 Git 或测试冲突的解释。

发布标准：

- 旧回复误收率为 0。
- 错项目提交率为 0。
- Codex 原文直接进入最终简报的比例为 0。
- 所有失败都有 Evidence、阶段代码和可执行建议。

## 5. 执行顺序

```text
Node 1 Invocation Contract
  -> Node 2 Invocation Manager
  -> Node 3 Navigation
  -> Node 4 Context Pack
  -> Node 5 Completion/Extraction
  -> Node 6 Claim Fusion
  -> Node 7 Recovery Learning
  -> Node 8 Metrics
  -> Node 9 Live Release Gate
```

## 6. 当前节点记录

### 2026-07-23 / Node 1

已实现：

1. 每次 Work Ledger Codex 调用生成唯一 invocation id。
2. 自动向提示词附加回复关联要求。
3. 视觉提取失败或缺少关联标记时进入复制回退。
4. 最终回复必须匹配本次 invocation。
5. Work Ledger Evidence 保存 invocation 验证结果。
6. 原始关联标记不会进入 Jachin 信息融合正文。

下一节点：

`Node 2：调用队列、租约与取消恢复。`

### 2026-07-23 / Node 2

已实现：

1. Codex 桌面调用进入单机持久化队列。
2. 独占租约阻止并发任务覆盖输入框或收集错误回复。
3. 调用执行阶段持续写入心跳与状态。
4. 排队期间和执行期间均可取消。
5. 重启时自动识别孤儿调用和失效租约。
6. 控制台展示活动调用、阶段和停止操作。

尚未在本节点执行：

1. 真实 Codex 桌面并发和取消 live smoke。
2. 跨项目、折叠侧栏、重名会话和窗口缩放导航烟测。

下一节点：

`Node 3：项目与会话导航韧性。`

### 2026-07-23 / Node 3

已实现：

1. 截图、窗口标题和 UIA 可访问性树参与同一次导航判断。
2. 项目折叠时先展开项目，再定位约定的“工作计划”会话。
3. 侧边栏当前不可见时，视觉定位搜索框并搜索会话。
4. 导航失败后按失败阶段切换路径，不盲目重复点击。
5. 连续两次上下文一致才允许进入输入阶段。
6. 粘贴后、提交前再次连续核验；上下文变化时清空输入并停止。
7. Evidence 保存全部导航候选、截图、可访问性摘要、置信度、验证签名和
   恢复路径。

尚未在本节点执行：

1. 真实 Codex 桌面在不同缩放、分辨率和折叠状态下的 live smoke。
2. 多个同名“工作计划”会话的真实 UI 验收。

下一节点：

`Node 4：Context Pack 与提示词预算。`

### 2026-07-23 / Node 4

已实现：

1. 简报补全和六类 Codex 协作场景统一使用 Context Pack。
2. 证据先按任务相关性排序，再按预算进入提示词。
3. 大 diff 按文件拆分，低相关块不会挤掉关键文件。
4. 文件片段受项目根目录白名单、敏感路径黑名单和单文件额度约束。
5. Codex 边界执行二次隐私与密钥脱敏。
6. Prompt Hash 改为绑定 Context Pack digest，证据变化后不会错误复用。
7. Invocation 与 Work Ledger Evidence 都保存 Context Pack 审计元数据。

尚未在本节点执行：

1. 用真实超大 Git diff 做 Codex 桌面输入耗时和输出质量 live smoke。
2. 根据真实调用统计调整默认 16000 字符预算和各区段比例。

下一节点：

`Node 5：生成完成与回复提取。`

### 2026-07-23 / Node 5

已实现：

1. 建立 Codex Reply Protocol，统一生成完成与回复可信度判断。
2. 完成条件由单一 OCR 稳定升级为停止控件、生成文案、稳定样本、回复长度和 invocation marker 多信号联合判定。
3. 权限请求、网络/生成错误、超时和窗口焦点丢失不再误判为成功。
4. 回复提取调整为原生复制优先，Qwen Vision 与 OCR 只承担验证和回退。
5. 复制按钮遍历增加 invocation marker 约束，不再接受历史回答或原始 prompt。
6. 增加半句、未闭合代码块/括号、场景 Schema 和 prompt echo 检测。
7. 多个可信来源语义冲突时停止交付，并在 Evidence 中保存冲突双方和相似度。
8. Work Plan Evidence 新增 `completion_state`、`reply_selection`、候选校验和明确失败阶段。

验证：

1. Codex 回复协议、协作链与 Invocation Manager 定向测试：25 项通过。
2. Work Ledger / Codex 相关完整回归：80 项通过。
3. Python 编译检查通过。
4. `git diff --check` 通过。

尚未在本节点执行：

1. 真实 Codex 桌面长回答、权限弹窗和网络中断 live smoke。
2. 根据真实 UI 样本校准稳定样本数、最小回答长度和来源冲突阈值。

下一节点：

`Node 6：Jachin Information Fusion 的逐条 claim 溯源。`

### 2026-07-23 / Node 6

已实现：

1. 新增 Codex Claim Extractor，把长回答拆成可独立审计的原子声明。
2. 新增 Claim Classifier，区分完成、验证、交付、文件变更、决策、风险、假设、
   解释和建议。
3. 新增 Evidence Matcher，关联用户确认、测试结果、Git、文件、运行和其他
   Work Ledger Evidence。
4. 新增反证检测；Codex 与本机事实冲突时，以本机证据为准。
5. 完成声明必须得到用户确认或验证级证据支持，Git 改动本身不再等同于“已经完成”。
6. 未知声明进入 `confirmation_queue`，冲突声明进入 `conflicts`。
7. 每条声明保存 `supporting_evidence`、`counter_evidence`、`unknown_reasons`、
   `allowed_uses` 和 `can_support_completion`。
8. 即时简报、日报、Lark 短报和周报质量门拒绝未知/冲突声明。
9. Prompt 明确约束五类 disposition 的使用边界，Jachin 仍是最终作者。
10. Claim Fusion 已进入 consultation Evidence、会话索引与最终 Evidence Digest。

验证：

1. Claim 分类、支持证据、完成声明升级、反证冲突、确认队列和建议边界专项测试通过。
2. 即时简报、日报和周报的未知/冲突声明拦截测试通过。
3. Work Ledger / Codex 协作定向回归通过。
4. Python 编译检查通过。

尚未在本节点执行：

1. 真实 Codex 桌面返回“错误完成结论”时的 live conflict smoke。
2. 根据真实长回答样本继续校准中文语义匹配阈值。

下一节点：

`Node 7：阶段化 Recovery 与失败学习。`

### 2026-07-24 / Node 7

已实现：

1. 新增 `CodexStageRecoveryPlanner`，统一处理八个 Codex 桌面阶段。
2. Windows UIA MCP manifest 新增 `windows_codex_work_plan_staged_recovery`，
   声明最大尝试次数、触发条件、恢复策略、动作参数、优先级和原因。
3. 导航从“一次把所有办法都试完”改成逐次选择：直接会话失败后才展开项目，
   展开失败后才使用侧栏搜索。
4. 每次选择都会携带完整 `history_reasons`，下一条路径同时参考此前全部失败。
5. 上下文验证支持等待页面稳定和换导航路径后重新验证。
6. Prompt 输入框定位失败时支持恢复前台焦点并重新视觉定位。
7. 提交前上下文变化时清除未提交 Prompt，恢复目标会话后重新粘贴并再次核验。
8. 等待阶段支持前台焦点恢复和一次受限延时，不会重复发送同一个 Prompt。
9. 回复提取支持重新截图、原生复制、Qwen Vision 和 OCR 交叉融合。
10. 独立来源仍冲突时停止自动交付并写入用户确认状态。
11. Recovery Snapshot、Terminal Report 和 Learned Playbook 已进入 Evidence。
12. Work Ledger consultation、会话索引和最终 Evidence Digest 均保留恢复信息。

验证：

1. Manifest Schema 校验通过。
2. A 失败后选择 B、B 失败后结合 A+B 选择 C 的专项测试通过。
3. 已失败策略不重复、跨阶段策略不串用、权限失败不盲目重试的专项测试通过。
4. 成功恢复和最终耗尽 JSONL 日志测试通过。
5. 真实 `codex_work_plan_query` 入口的导航恢复 Evidence 集成测试通过。

尚未在本节点执行：

1. 真实 Codex 桌面窗口遮挡、权限请求、网络中断和超长回答 live smoke。
2. 根据真实失败样本校准每条 manifest 策略的优先级和等待时间。

下一节点：

`Node 8：可观察性、恢复质量趋势和 invocation 回放。`

### 2026-07-24 / Node 8

已实现：

1. 新增 Codex invocation 后端聚合索引和 7/14/30 天观测窗口。
2. Evidence 加载时自动提取最完整的 invocation payload，并按 invocation id 去重。
3. 新增成功、恢复、人工介入、旧回复拦截、截断拦截、平均耗时和 P95 指标。
4. 新增主要失败阶段与恢复路径统计。
5. Evidence Console 新增 Invocation 趋势区和完整阶段回放区。
6. 回放同时展示 Invocation Manager、Evidence Timeline、Recovery Attempt、
   Recovery Decision、Completion State 和 Reply Selection。
7. 调用入口新增统一耗时、尝试次数、最终状态与失败阶段落盘。
8. 修复 Evidence 治理索引与 Codex 可观察性索引的 Tauri ACL。

验证：

1. TypeScript `tsc --noEmit` 通过。
2. Vite 生产构建通过，5908 个模块完成转换。
3. Codex / Work Ledger / Recovery 定向回归 98 项全部通过。
4. Python 编译检查通过。
5. Rust 专项测试已编写；当前机器的 MSVC linker 因 `LNK1105` 无法关闭新生成文件而未完成执行，
   与代码诊断无关，需在 linker 环境恢复后重跑。

下一节点：

`Node 9：真实桌面发布门与 live smoke。`

### 2026-07-24 / Node 9

状态：自动发布门第一版已完成；真实桌面 live smoke 待人工授权执行。

已实现：

1. 新增独立 `Codex Release Gate Evaluator`，发布结论不再等同于单次脚本成功。
2. 发布门覆盖正确上下文、错误上下文、折叠项目、并发排队、权限请求、网络超时和事实冲突七类场景。
3. 正确场景必须完成上下文验证、关联回复校验和 Invocation Manager 成功终态。
4. 错项目或错会话必须在提交 Prompt 前停止，禁止“先发出去再判断”。
5. 折叠项目必须使用 manifest 声明的项目展开或侧栏搜索恢复路径。
6. 并发请求通过真实 `CodexInvocationManager` 租约验证唯一 invocation、串行执行和 Prompt 不重叠。
7. 权限请求必须显示需要用户采取的动作，并禁止伪成功。
8. 网络超时必须在有限次数内停止，同时输出可执行的下一步建议。
9. Codex 结论与 Git/测试证据冲突时必须阻断交付，Codex 原文只能作为辅助素材。
10. 新增四条硬性发布不变量：旧回复误收、错上下文提交、Codex 原文直用、失败证据不完整，任一非零即阻断发布。
11. 新增安全的 contract smoke 脚本，运行后为每个场景生成独立 Evidence 和统一发布报告。
12. Evidence Console 接入最新发布门报告，展示 READY/BLOCKED、场景通过率、硬性不变量和失败检查项。
13. 同一脚本支持 `--mode live`；它会先建立完整 contract 基线，再用真实 Codex 桌面调用替换 baseline，
    重新计算完整发布结论，避免把单次 live 成功误当成全部场景通过。

验证：

1. `python scripts/codex_live_release_gate.py --mode contract` 通过，七个场景全部通过。
2. 四条发布不变量均为 0。
3. Release Gate 专项测试覆盖完整矩阵、生成器输入、错上下文提交、旧回复误收、Codex 原文直用和失败证据缺失。
4. 自动发布报告位于 `output/codex_live_release_gate/release_gate_latest.json`。

尚未在本节点执行：

1. 对真实 Codex 桌面执行窗口遮挡、权限弹窗、网络中断、应用重启和长回答 live smoke。
2. 真实 live 证据通过后，才能把 Node 9 从“自动发布门完成”升级为“桌面发布验收完成”。

#### Node 9 真实桌面验收补充

真实执行结论：

1. 已运行一次真实 Codex 桌面 baseline，最终发布门为 `BLOCKED 6/7`。
2. 自动化成功识别并聚焦 Microsoft Store 版 Codex，确认其真实进程为
   `OpenAI.Codex_*` 包目录内的 `ChatGPT.exe`。
3. 普通 ChatGPT 与 Codex 虽共享进程名，现已通过安装包路径标记强制区分；
   前台进程来源不匹配时必须停止，不能仅凭窗口标题继续操作。
4. 第一次 live 中视觉模型虽然识别到 `工作计划`，但给出的坐标落在
   `了解项目用途` 行；提交前二次上下文验证发现当前仍是
   `评估 Windows MCP 实现`，因此在粘贴 Prompt 前安全停止。
5. 该结果证明错会话防护有效：没有向错误会话提交 Prompt，也没有产生伪成功。

针对 live 暴露问题已完成：

1. Codex 导航使用项目目录 basename `jachin-system-main` 识别桌面工作区，
   业务展示名 `Jachin` 继续用于工作账本与简报，两种名称不再混用。
2. 侧栏项目和会话定位改为本地 OCR 优先，必须精确匹配项目锚点及其下方
   `工作计划` 文本行；不再根据列表顺序推断坐标。
3. Qwen Vision 降为 OCR 失败后的可选回退；外部视觉调用必须遵守截图授权边界。
4. Prompt 定位提示进一步要求坐标落在目标文字所在行，不能只返回语义上可能的条目。
5. Existing-window 路径改为先聚焦并验证已打开的 Codex，再考虑启动程序，
   避免直接执行受保护的 WindowsApps 路径。
6. Evidence 聚合器现在优先读取 `release_gate_live_latest.json`；
   contract 报告只能在没有 live 报告时作为回退。
7. Evidence Console 会明确标记 `LIVE` 或 `CONTRACT` 以及报告来源，
   后续 contract 的 READY 不会遮住真实 live 的 BLOCKED。

本轮验证：

1. Codex / Work Ledger / Recovery 相关回归：103 项通过。
2. 另有 20 项 Windows MCP wrapper 测试因当前 Anaconda 环境缺少
   `mcp.server.fastmcp` 未执行成功，属于测试运行时依赖缺口，不计为功能通过。
3. 新增环境身份和 OCR 选择专项测试通过。
4. Rust Release Gate 报告优先级专项测试通过。
5. TypeScript `tsc --noEmit` 通过。
6. Vite 生产构建通过，5908 个模块完成转换。
7. contract 发布门复测仍为 7/7，四条硬性不变量均为 0。
8. 最新真实报告仍保持 `BLOCKED 6/7`，位于
   `output/codex_live_release_gate/release_gate_live_latest.json`。

Node 9 当前结论：

1. 自动发布门、错上下文拦截、进程身份验证、证据优先级和本地 OCR 导航已完成。
2. 代码层根因已修复，但在未重新完成一次授权的真实桌面 baseline 前，
   不把 Node 9 标记为 live 验收通过。
3. 下一次真实验收通过后，再进入窗口遮挡、权限、网络异常和应用重启矩阵，
   不继续扩建新的框架层。

#### Node 9 真实桌面验收完成

状态：核心 live 链路已通过；异常矩阵继续扩充。

本轮新增实现：

1. Codex 支持通过 `codex:` 协议启动，不再依赖受保护的 WindowsApps 可执行文件路径。
2. 启动、聚焦、点击、提交和复制前均验证真实前台进程及
   `OpenAI.Codex_*` 安装包路径，避免普通 ChatGPT 或其他窗口冒充 Codex。
3. 项目、会话和输入框使用本地 OCR 与自适应区域定位，不依赖固定屏幕坐标。
4. 输入框已存在旧草稿时，通过底部模型/模式锚点推断真实编辑区域，
   定位后先清除旧草稿再粘贴本次带 invocation marker 的 Prompt。
5. 修复短 OCR 文本误命中长项目名的问题；单字符文本不再通过子串规则匹配。
6. 回复生成状态只读取 UI 控件信号，不再把回复正文里的 `running` 等普通单词
   误判为仍在生成。
7. 提交前增加最后一次原子化前台身份守卫；失焦时停止，不向其他应用发送回车。
8. 等待回复期间的暂时失焦改为最多五次退避恢复。每次恢复都保留此前失败原因，
   恢复成功后继续等待同一个 invocation，绝不重复提交 Prompt。
9. live smoke 增加真实前台遮挡注入器，按启动前后 PID 差集只关闭本轮测试创建的
   Notepad，不影响用户原有窗口。
10. live-task 输出统一使用 UTF-8，中文提示和 Evidence 不再受控制台编码影响。

真实验收结果：

1. Baseline 通过：成功打开并聚焦 Codex，定位
   `jachin-system-main / 工作计划`，提交唯一 Prompt，提取关联回复并通过质量门。
   Evidence：
   `output/codex_live_release_gate/matrix/live_task_v18/live_task_latest.json`。
2. 连续调用与长回复通过：第二个独立 invocation 未复用上一条回复，
   原生复制得到约 1500 字的六项结构化回答，marker、完整性和来源校验均通过。
   Evidence：
   `output/codex_live_release_gate/matrix/live_task_v19_sequential_long/live_task_latest.json`。
3. 持续遮挡安全失败：Notepad 长时间占据前台时，没有重复提交，也没有把输入发给
   错误窗口，最终输出 `codex_reply_completion_timeout` 和恢复证据。
   Evidence：
   `output/codex_live_release_gate/matrix/live_task_v21_obstruction/live_task_latest.json`。
4. 临时遮挡恢复通过：Notepad 在等待回复阶段抢占前台，8 秒后关闭；
   Jachin 重新聚焦 Codex、继续追踪原 invocation，最终复制完整回复并通过 marker
   校验。总耗时约 89 秒。
   Evidence：
   `output/codex_live_release_gate/matrix/live_task_v24_temporary_obstruction_recovery/live_task_latest.json`。
5. 相关 Python 回归：54 项通过；触及文件的 Python 编译检查通过。

Node 9 更新结论：

1. 正确上下文、唯一提交、连续调用隔离、长回复提取、暂时遮挡恢复和持续遮挡
   安全停止均已有真实桌面 Evidence，不再只是 contract 模拟。
2. 当前核心 Codex 协作路径具备可重复执行、可验证、可恢复和可回放能力。
3. 后续异常矩阵继续覆盖权限弹窗、断网、Codex 应用重启与超长输出，但不阻塞
   当前工作账本 MVP 使用 Codex 完成真实信息补全。

### 2026-07-24 / Node 10

状态：超长回复与远程视觉故障恢复已完成真实桌面验收。

本轮根因治理：

1. OCR 只允许承担等待状态、进度判断和控件定位，不能再作为最终回复可信来源。
2. 最终回复只接受剪贴板、Accessibility 或可信视觉抽取；OCR 即使文字较长也必须标记
   `untrusted_final_reply_source`。
3. Prompt 中明确声明“不少于 N 字符”时，质量门会提取长度约束并强制校验，
   防止只复制屏幕可见尾部却误报成功。
4. OCR 行结构新增文字框边界，复制按钮由最新回复块的左边界和末行底部共同定位，
   不再使用固定坐标。
5. 修复末行换行缩进导致横坐标偏移、误点点赞按钮并打开反馈弹窗的问题。
6. 增加 Codex 瞬态反馈弹窗识别和 `Esc` 清理；弹窗未消失时禁止猜测复制坐标。
7. 复制前增加会话上下文硬门禁。普通上下文 OCR 不稳定时，以本次唯一
   `[JACHIN_REF]` 在主内容区可见作为更强关联证据。
8. Codex 在等待结束后回到项目首页时，系统会重新进入约定会话再复制，
   但不会重复提交 Prompt。
9. 原生 `Ctrl+Shift+C` 保留为低成本优先路径；不可用时才进入本地 OCR
   动态定位复制按钮。
10. 鼠标位于 PyAutoGUI 紧急停止角时维持安全失败，不关闭 fail-safe。

真实验收：

1. 超长回复完整复制通过：v31 获取 `10774` 字符清洗后正文，
   来源为 clipboard，关联标记和不少于 2500 字符约束均通过。
   Evidence：
   `output/codex_live_release_gate/matrix/live_task_v31_ultra_long_full_copy/live_task_latest.json`。
2. v32-v34 在修复过程中均保持安全失败，没有把 OCR 拼接文本或错误剪贴板当成成功；
   这些失败分别暴露了反馈弹窗遮挡、会话漂移和末行缩进误点。
3. 修正后的现场复制探针从 v34 当前回复复制 `693` 字符，marker 精确命中，
   本地定位点为 `(346, 752)`，没有重复发送任务。
4. 远程视觉故障完整链路 v35 通过：视觉 API 被强制指向不可达地址，
   系统仍由本地 OCR 定位复制按钮并从 clipboard 获取 `706` 字符原文，
   清洗后正文 `669` 字符，最终状态为 `codex_work_plan_reply_ready`。
   Evidence：
   `output/codex_live_release_gate/matrix/live_task_v35_remote_vision_outage_final/live_task_latest.json`。
5. v35 Invocation Manager 终态为 `succeeded`，完整记录打开、导航、上下文验证、
   粘贴、提交、等待、复制和回复验证阶段。
6. 发布契约门复测为 `7/7`，错误上下文、折叠项目、并发排队、权限请求、
   网络超时和事实冲突全部通过，四条硬性不变量均为 `0`。
   报告：
   `output/codex_live_release_gate/release_contract_final/release_gate_latest.json`。
7. Codex 回复协议与工作账本协作定向测试 `46/46` 通过，相关 Python 编译检查通过。

Node 10 结论：

1. 正常回复、超长回复、远程视觉不可用、瞬态弹窗、会话漂移和误点击路径均已有
   真实证据或安全失败证据。
2. “看见一部分就算成功”与“点到相邻按钮仍继续”的两类伪成功已被硬性阻断。
3. 当前 Codex 工作计划协作链可以进入工作账本 MVP 的日常使用阶段。
4. 应用进程重启 live smoke 必须由外部 runner 执行，避免测试脚本关闭承载当前任务的
   Codex 进程；权限请求已经由 contract gate 验证为显式人工介入且不误报成功。
