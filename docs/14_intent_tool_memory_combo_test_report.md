# 任务识别 / 工具调用 / 记忆组合测试报告

日期：2026-07-15

## 测试目标

本轮测试不是单独验证某一个工具，而是验证 Jachin 主链路在复杂输入下是否能稳定完成：

1. 任务识别：从自然语言中识别真实目标，而不是被单个关键词误导。
2. 意图识别：区分普通发消息、项目简报、计算器、文件操作、App 控制、模糊实体纠错。
3. 工具组合：确认 WorkOrder DAG 能表达多工具协作，包括 App 打开、Lark 发送、计算器、文件 reveal、manifest 驱动的 Web 搜索 -> 总结 -> Lark 发送。
4. 记忆参与：确认最近动作记忆、实体纠错候选、噪声记忆下的目标解析不会跑偏。
5. 防虚假调用：工具没有真实执行证据时，不能只凭“已发送”文本就判定成功。

本轮为单元级深度组合测试，不做真实 Lark 发送，不访问外网，不操作真实窗口。真实 OS live workflow 需要单独授权后再跑。

## 新增测试文件

`tests/unit/test_intent_tool_memory_combo_matrix.py`

## 覆盖场景

| 场景 | 输入类型 | 期望链路 | 结果 |
| --- | --- | --- | --- |
| Lark 普通消息 | “打开 lark 向 Neil 发送一条消息，内容为你好” | message_send -> 打开 Lark -> windows_lark_send_message | 通过 |
| 项目简报发送 | “总结 Jachin 最近 3 天进展，使用 Codex 后发给 Neil” | project_briefing_delivery -> windows_codex_lark_workflow_template | 通过 |
| Web 搜索后发送 | “上网搜索今天 AI 最新消息，总结后发给 Neil” | web_research_delivery -> tavily_search -> fetch -> web_research_summarize -> windows_lark_send_message | 通过 |
| 计算器计算 | “打开计算器，计算 99+100 等于多少” | 打开 Calculator -> windows_calculator_calculate | 通过 |
| 文件定位 | “读取 D:\tmp\report.txt 并打开所在位置” | file_operation -> windows_file_reveal_in_explorer | 通过 |
| 最近动作记忆关闭 | active window 为 Jachin，最近成功打开 WeChat，用户只说“关闭” | close_app 目标应来自最近动作记忆 WeChat | 通过 |
| 大量噪声记忆 | 800 条无关 recent action + 最新 WeChat | 仍应解析到 WeChat | 通过 |
| 实体纠错 | “open lock” | lock -> Lark 候选，但必须先问确认，不直接执行 | 通过 |
| Manifest 多工具 DAG | web_research_delivery manifest | tavily_search -> fetch -> web_research_summarize -> windows_lark_send_message | 通过 |
| 防虚假发送 | observation 只有“已发送消息给 Neil”但无 role evidence | Verification 必须失败 | 通过 |

## 本轮发现并修复的问题

1. 语义能力覆盖过宽：普通消息和文件任务可能被能力语义匹配误抢成 `project_briefing_delivery`。
   - 修复：`choose_semantic_override` 增加文本门禁，项目简报必须有项目、代码、仓库、Jachin、Codex 等证据。

2. ReviewBoard 没有把原始文本传给语义覆盖判断。
   - 修复：调用 `choose_semantic_override` 时传入 `text`，让语义门禁能基于真实输入判断。

3. 项目简报从普通 message_send 升级时，target 会残留底层 App 误识别信息，例如 Codex 被 code 别名影响成 VSCode。
   - 修复：新增 workflow target 归一化，只继承收件人和原始任务，不继承底层 App 噪声。

4. 文件“打开所在位置”被 `打开` 抢成 AppControl，或被当成普通 read。
   - 修复：中文文件意图前置，`所在位置 / 资源管理器 / 定位 / 显示位置` 归一成 reveal。

## 验证结果

运行命令：

```powershell
pytest -q tests\unit\test_intent_tool_memory_combo_matrix.py -o addopts=
pytest -q tests\unit\test_intent_tool_memory_combo_matrix.py tests\unit\test_capability_contract_validator.py tests\unit\test_intelligence_foundation_layers.py tests\unit\test_cognitive_kernel_architecture.py -o addopts=
python -m compileall -q l3_node\cognitive_kernel tests\unit\test_intent_tool_memory_combo_matrix.py
```

结果：

1. 新增组合矩阵：10 passed。
2. 关联主链路回归：60 passed。
3. Python 编译检查：通过。

## 当前结论

当前任务识别、意图识别、Capability metadata DAG、记忆最近动作、实体纠错确认、工具调用防虚假成功这几条主链路已经具备可回归验证能力。

尤其是这次修复后，系统不再因为看到“总结 / 发送 / 最新”等词就粗暴选择项目简报，而是必须结合项目证据、用户目标、基础意图和能力 metadata 做门控。

## 仍需加强的模块

1. Web Research Delivery 已补正式能力
   - 当前“上网搜索最新消息，总结后发给 Neil”会识别成 `web_research_delivery`，不会再误判成项目简报，也不会保守落到普通 `message_delivery`。
   - 已注册 `mcp:web_research_delivery`，由 capability metadata 声明搜索 -> 证据提取 -> 总结 -> Lark 发送 -> 截图/OCR/API 校验。
   - TaskDecomposer 已能根据 manifest 自动生成 `mcp:tavily_search` -> `mcp:fetch` -> `core:web_research_summarize` -> `mcp:windows_lark_send_message` 四段 WorkOrder。

2. Message slot parser 需要继续扩展更多表达
   - 已覆盖“搜索 X，总结后发给 Y”的核心路径，不会把“总结后发给 Y”当成正文。
   - 后续还应覆盖更多自然表达，例如“帮我查查这件事，整理成三条发群里”“看一下网上最新进展，发给 Neil”。

3. 多 MCP DAG 现在验证了 manifest 分解能力，但还没有 live 执行闭环
   - 单元测试证明 DAG 能从 manifest 生成。
   - 还需要真实执行：Web 搜索 MCP、总结工具、Lark dry-run / live send、Evidence 回放。

4. 记忆压测还应扩展到多通道
   - 本轮用 800 条 recent action 噪声验证最近动作记忆。
   - 后续应覆盖 aliases、corrections、failure_hints、tool_habits、project_facts 的十万级混合召回。

5. Capability Contract 需要成为发布质量门槛
   - 已有 validator，但应进一步要求关键业务 Skill 必须声明 decomposition、verification、recovery_playbook、required_mcps / required_models。
   - 低质量 manifest 可以安装，但控制台应明显标注“不够生产级”。

6. Verification 还需要更多工具族策略
   - Lark 虚假发送已经会被挡住。
   - 还需要对浏览器搜索、文件 reveal/open、计算器视觉校验、App close/switch 建立同等级的“不能只看文本”的验证策略。

7. Failure Learning Loop 需要接入组合任务
   - 现在能记录失败并生成恢复计划。
   - 下一步要在 Web 搜索 + Lark、文件到 App、AppControl + Message 这类多步任务中验证：A 路径失败后，结合 A 的原因选择 B；B 再失败后再结合 A+B 选择 C。

## 下一步建议

下一步应做 `web_research_delivery` 的真实执行验收：

1. 用已注册的 capability manifest 跑 dry-run Evidence，确认四段 WorkOrder 全部进入 Evidence Console。
2. 接入真实搜索/抓取工具返回值到 `core:web_research_summarize`，让摘要节点引用上游网页证据。
3. 用户授权后跑 live-confirmed：搜索最新消息，生成短摘要，只发送给 Neil 或测试群。

这会直接覆盖用户最关心的“多个 MCP 组合完成真实办公任务”的主线能力。
