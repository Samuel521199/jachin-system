# 能力域：招聘（HR / Boss / Lark）

**域 id**：`hr_recruitment`  
**总目录**：[L3_CAPABILITY_CATALOG.md](../L3_CAPABILITY_CATALOG.md)  
**架构细节**：[HR_RECRUITMENT.md](../HR_RECRUITMENT.md)

---

## MCP 工具映射（模型自检）

当「可用工具」列表中出现下列 **id 子串** 时，**招聘域已加载**，应识别招聘/无人值守/简历相关意图并优先走工具链。

| 工具 id（片段） | 用途（何时调用） |
|-----------------|------------------|
| `atom_post_job_boss` | HR 已确认 JD：带 `jd_config` / `jd_config_path` 在 Boss 发布 |
| `add_automated_recruitment_task` | 开启无人值守调度（打招呼/收网/规则引擎）；参数含 `resume_collect_target`、`analyze_threshold`、`max_count_per_harvest_tick`、`greet_target` 等 |
| `stop_automated_recruitment` | HR 要求关闭/停止无人值守招聘（整包停表） |
| `atom_greet_recommend_boss` | 单次「推荐牛人」页打招呼 |
| `atom_lark_chat` | Lark 消息处理入口（壳模式可转发 Agent） |
| `hr_analyze_resume` | 目录+JD 触发 Wasm 透析（批量简历 MCP 包装） |
| `atom_inbox_harvester` / `atom_lark_bitable` / `atom_lark_send_message` | 收网、多维表、发消息等（与调度/自动化配合） |

**Wasm（非 MCP id）**：`jpp:com.jachin.hr.analyzer4`（HR 透析镜）——见 system prompt 中透析专项说明。

---

## 硬路径（飞书）

短句如停止收网、再抓 N 份、纯「分析/透析镜」、进度、每轮人数等，可能由 **`l3_node/lark_workflow_command_interceptor.py`** 直接处理；详见总目录 §2。域内不重复罗列口令，以免与代码分叉。

---

## 与 SKILL.md 的关系

流程话术、分支 B、JSON 确认等以注入的 **`skills_repo/hr-recruitment/SKILL.md`**（或 MCP 包内同名）为准；**本域文档**只做工具映射与硬路径指针。

---

<!-- PROMPT_INJECT_RECRUITMENT_START -->

### 【域：招聘 · 注入摘要】

若可用工具中含 **`atom_post_job_boss` / `add_automated_recruitment_task` / `stop_automated_recruitment` / `atom_greet_recommend_boss` / `atom_lark_chat` / `hr_analyze_resume`** 等招聘 MCP，则 **招聘能力已就绪**。

1. **自检**：用户谈发布职位、JD、无人值守、收网、打招呼、简历分析、透析镜、停止招聘等 → 优先用 **招聘 MCP**，禁止只写操作建议不调用工具（除非对方仅闲聊且未授权操作）。

2. **与飞书硬拦截分工**：短句 **停止 / 分析 / 再抓 N 份 / 进度 / 每轮多少人…** 在 **飞书入站** 可能已被拦截；在 **WebSocket/HTTP** 中遇到等价意图时，仍应通过 **stop 类 MCP、调度说明或透析工具** 落实。

3. **典型顺序**：澄清 JD → 确认 → `atom_post_job_boss` → `add_automated_recruitment_task`；停整包 → `stop_automated_recruitment`；仅批量透析目录 → `hr_analyze_resume` 或 `jpp:com.jachin.hr.analyzer4`（以工具列表为准）。

4. **详细 SOP**：紧随其后的 **「HR 招聘总监」** 段落（SKILL.md）为准。

<!-- PROMPT_INJECT_RECRUITMENT_END -->
