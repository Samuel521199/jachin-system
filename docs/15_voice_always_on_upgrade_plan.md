# Jachin 语音双模式升级执行规划

更新时间：2026-07-17

## 目标

Jachin 语音入口升级为两种互相兼容的模式：

1. 常开模式：一直监听用户说话，识别到完整语音后直接进入主任务链路；支持执行中打断，也支持任务执行时继续聊天。
2. 录音模式：用户点击/按住录音，录音结束后再执行任务；保留现有 Push-to-Talk 工作流。

两种模式必须共享同一套智能主路径：

Voice Input Adapter -> Voice Language Normalizer -> Goal Interpreter -> TaskDecomposer -> Dispatcher -> RoleExecutor -> Verification -> Recovery -> Evidence/Memory

语音入口不能独立成另一套状态机，也不能绕过任务拆解、记忆、失败学习和证据链。

## 模式定义

### 常开模式 continuous_listen

- 麦克风常驻监听。
- 不需要唤醒词即可识别完整语音片段。
- 系统正在说话或执行任务时，用户可以插话打断。
- 语音输入会带上 `voice_interaction_mode=continuous_listen`。
- 默认只在用户显式开启后启动，避免隐私误触。

### 唤醒模式 wake_conversation

- 先监听唤醒词。
- 唤醒后进入短时间对话窗口。
- 语音输入会带上 `voice_interaction_mode=wake_conversation`。

### 录音模式 push_to_talk

- 点击/按住开始录音。
- 松开或停止后一次性提交。
- 语音输入会带上 `voice_interaction_mode=push_to_talk`。
- 继续兼容现在的语音按钮和 STT trace。

## 执行节点

### Node V1：语音模式契约

状态：已完成

- `VoiceUxProfile` 新增 `continuous`。
- `UserSettings.sprite_voice_mode` 保留三种值：`push_to_talk`、`wake_up`、`continuous`。
- 设置页将 continuous 展示为“常开模式”。

### Node V2：常开监听底层通道

状态：已完成

- `stt_start_wake_listener` 支持 `mode` 参数。
- `WakePipelineConfig` 增加 `mode`。
- `wake_pipeline` 在 continuous 模式下跳过 KWS idle，直接进入 Conversation 监听。
- continuous 模式下失败后不进入唤醒词冷却，而是继续监听下一段语音。

### Node V3：语音来源与主链路打通

状态：已完成

- `voice_wake_bridge` 注入语音事件时携带 `source`。
- `chat.tsx` 区分 `wake`、`continuous`、`ptt`。
- 进入 L3 时补充 `voice_interaction_mode`，让 Goal Interpreter 和 Evidence 能知道语音来源。

### Node V4：控制台模式切换

状态：已完成

- 设置页选择 `wake_up` 时启动唤醒监听。
- 设置页选择 `continuous` 时启动常开监听。
- 设置页选择 `push_to_talk` 时停止后台监听，保留录音按钮模式。
- 唤醒词专用页面显式使用 wake-up，不会误启动 continuous。

### Node V5：执行中打断与并行聊天

状态：第一阶段已完成

已完成：

- 新增 `VoiceInterruptionAgent`，在语音输入进入任务规划前输出结构化判断。
- 支持区分 `cancel`、`pause`、`resume`、`modify_current_task`、`side_chat`、`confirm_required`。
- 执行中收到“停一下/取消/暂停”等明确控制语句时，优先请求取消当前任务并写入 ledger。
- 执行中收到普通聊天/提问时，不触发工具，直接走轻量 UserFacingReplyAgent。
- “改成/换成/改发/重新”等纠偏类语句不直接拦截，而是携带 active task context 继续进入主任务链路，由 TaskDecomposer 重新规划。
- 低置信度控制语句进入确认，不直接执行取消。
- 新增语音任务句柄注册与解析：`run_id`、前端 `task_id`、`session_id`、active task context 可以互相解析。
- 顶层 `run_agent` 注册可取消句柄和全局任务表，结束后清理，避免旧任务残留。

待增强：

- 将 `modify_current_task` 显式升级为 Replan WorkOrder，而不是只依赖普通主链路理解。
- 将打断结果同步写入 Memory Failure/Learning Loop。

### Node V6：Voice Evidence Agent

状态：待实现

要做：

- 每段语音记录：开始时间、结束时间、来源模式、STT 文本、热词修正、最终规范化文本。
- 记录是否触发任务、是否触发打断、是否触发确认。
- Evidence Console 展示语音入口和后续 WorkOrder 的关联。

### Node V7：噪音与误触防护

状态：待实现

要做：

- 常开模式下增加最短语音长度、置信度、静音间隔、重复文本过滤。
- 对低置信度文本进入确认，而不是直接执行。
- 对危险任务仍然走确认门控。

### Node V8：真实压测

状态：待实现

要做：

- 常开模式下连续发出 App 打开/关闭、Lark 发消息、文件 reveal、取消任务等指令。
- 录音模式下确认旧流程不回归。
- 测试执行中插话、取消、纠错、继续聊天。
- 输出测试报告并写入 Evidence。

## 当前阶段结论

本阶段完成了“常开语音模式”的底层通道和前端设置入口，使 continuous 不再只是配置值，而是真正能进入监听管线和统一主任务链路。

下一阶段应继续增强 Node V5 的 Replan WorkOrder，并实现 Node V6：Voice Evidence Agent。这样语音才会从“输入方式”升级成 Jachin 主架构的一等入口。
## Node V5 第二阶段：执行中任务修正 Replan Patch

状态：已完成最小闭环。

- 新增 `VoiceTaskReplanPatch`，把“改成发给 Neil”“不要发给 Vivian”“内容换成 xxx”等修正话术解析成结构化 patch。
- `InputAdapter` 在 `modify_current_task` 时自动构建 `voice_task_replan_patch`，并写入 `modality_evidence`。
- `run_agent` 在规划前消费 patch，把用户短修正改写为明确的重规划指令，再交给 ReviewBoard -> Arbiter -> TaskDecomposer -> Dispatcher 主链路。
- 低置信度修正不会直接执行，会生成 waiting_user closure，要求用户把修正说完整。
- 新增单元测试覆盖收件人替换、收件人移除、内容修改、无 active task 不应用。

## Node V6 第一阶段：Voice Evidence Agent

状态：已完成最小闭环。

- 新增 `VoiceEvidenceAgent`，统一记录语音入口证据，不再只依赖零散字段。
- 每个语音 turn 会按阶段写入 `voice_evidence_snapshot`：`input_adapted`、`interruption_handled`、`side_chat_handled`、`replan_waiting_user`、`replan_applied`、`planning_finished`。
- 证据字段包含：语音模式、STT 来源、STT 置信度、原始文本、归一化文本、归一化步骤、打断判断、Replan Patch、规划出的 task/workflow/work_order。
- Evidence Console 已识别 `voice_evidence_snapshot`，时间线显示为 `VoiceEvidence`，并在 Input Adapter / Voice Evidence 区块展示完整 payload。
- 新增单元测试，验证语音 turn 会生成 VoiceEvidence，普通文本 turn 不会误写语音证据。
## Node V7 第一阶段：常开语音噪声与误触防护

状态：已完成最小闭环。
- 新增 `VoiceFalseTriggerGuard`，在 `InputAdapter` 中先于语言归一化、打断识别和任务拆解执行。
- 常开语音会过滤空文本、极短片段、语气词、重复片段、系统 TTS/播放回声，避免噪声进入 Goal Interpreter。
- 低置信度但像任务的语音不会直接执行，会进入 `voice_false_trigger_confirmation` pending 状态。
- 用户回复“确认执行”后会恢复上一轮被拦截的原始任务；回复“取消”会清理 pending，不执行任务。
- 危险/有副作用的语音动作在置信度不足时走确认门控，防止常开监听误发送、误关闭、误改文件。
- `VoiceEvidenceAgent` 新增 `false_trigger_guard` 字段，Evidence Console 会展示 guard 判定、原因码和拦截证据。
- 新增单元测试覆盖：语气词丢弃、低置信动作确认、push-to-talk 放行、重复片段丢弃、pending 确认/取消恢复。
## Node V9：Owner Voiceprint Live Check + Adaptive False Trigger Learning

状态：已完成工程闭环，等待真实声纹样本验收。

- 新增 `VoiceFalseTriggerLearning`，把 always-on 误触守卫的 allow/drop/confirm、用户确认/取消、owner voiceprint live check 结果统一写入 `voice_false_trigger_learning.jsonl`、Cognitive Kernel ledger 和 Memory Growth raw evidence。
- `VoiceFalseTriggerGuard` 开始读取学习层给出的 bounded threshold hints。阈值只在安全边界内小幅调整：噪声多时提高非任务语音丢弃阈值；低置信任务被用户多次确认后，轻微降低确认阈值。
- `voice_pending_confirmation` 在用户回复“确认执行 / 取消”时把结果反哺学习层，让系统知道这次拦截是正确还是过度保守。
- 新增 `scripts/voice_owner_voiceprint_live_check.py`，检查 owner voiceprint profile、JVS health、最近 `voice_companion.log` / `voice_chat.log` 中的 owner accept/reject/drop 证据，并生成 `docs/17_voice_owner_voiceprint_live_check.md`。
- live check 不再把 `sv.owner_track_ptt_fast_bypass` 当作真实 owner pass，避免报告虚高；JVS 连接失败也会输出稳定错误码，避免报告里出现乱码。
- 当前本机 live check 结果：`NEEDS_ACTION`，缺少 `C:\Users\Legion\.jachin\voice\owner_voiceprint.json`，且 JVS health 当前 `connection_refused`。需要在 Wake Mode 页面录入 3 段主人样本并启动 JVS 后，再跑 owner/non-owner 真实验收。
- 验证：语音相关 42 个单元测试通过；always-on guard 40 场景压测通过。
